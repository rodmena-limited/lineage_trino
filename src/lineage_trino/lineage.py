"""
Column-Level Lineage Engine.

Walks sqlglot AST to trace every output column back to its source columns
through CTEs, subqueries, JOINs, aggregations, and expressions
(file ownership: Module C).

Core algorithm: recursively trace each SELECT expression through the query tree,
resolving column references against table aliases and CTE definitions.
"""

from __future__ import annotations

import logging
import time

from sqlglot import exp

from lineage_trino.models import (
    LineageEdge,
    LineageMetadata,
    RelationType,
    SourceColumn,
    TableInfo,
    TargetColumn,
    TransformationType,
)
from lineage_trino.parser import SQLParser

logger = logging.getLogger(__name__)


class LineageEngine:
    """
    Extract column-level lineage from parsed SQL AST.

    Usage:
        engine = LineageEngine()
        # From raw SQL:
        result = engine.extract_from_sql("SELECT a+b AS c FROM t")

        # From parsed statements:
        parser = SQLParser()
        stmts = parser.parse("SELECT * FROM t")
        result = engine.extract(stmts, source_file="query.sql")
    """

    def __init__(self, dialect: str = "trino"):
        self.dialect = dialect
        self.parser = SQLParser(dialect)

    def extract_from_sql(
        self,
        sql_text: str,
        source_file: str | None = None,
        include_graph: bool = False,
    ) -> LineageGraph:
        """Parse SQL and extract lineage in one step."""
        statements = self.parser.parse(sql_text, source_file=source_file)
        return self.extract(statements, source_file=source_file)

    def extract(
        self,
        statements: list[exp.Statement],
        source_file: str | None = None,
    ) -> LineageGraph:
        """
        Extract column-level lineage from parsed statements.

        Args:
            statements: List of sqlglot AST statements.
            source_file: Optional source file for tracking.

        Returns:
            LineageGraph with all edges, table info, and metadata.
        """
        start_time = time.time()
        all_edges: list[LineageEdge] = []
        all_sources: dict[str, set[str]] = {}
        all_targets: dict[str, set[str]] = {}
        all_intermediates: dict[str, set[str]] = {}
        errors: list[str] = []

        for stmt in statements:
            try:
                edges = self._process_statement(stmt, source_file)
                all_edges.extend(edges)

                # Classify tables from edges
                for edge in edges:
                    if edge.target.table:
                        all_targets.setdefault(edge.target.table, set()).add(
                            edge.target.column
                        )
                    for src in edge.sources:
                        if src.table and not src.table.startswith("__"):
                            all_sources.setdefault(src.table, set()).add(src.column)

            except Exception as e:
                msg = f"Error processing statement: {e}"
                logger.warning(msg, exc_info=True)
                errors.append(msg)

        # Separate CTEs as intermediates
        _separate_intermediates(all_sources, all_targets, all_intermediates)

        table_info = TableInfo(
            sources={k: sorted(v) for k, v in sorted(all_sources.items())},
            targets={k: sorted(v) for k, v in sorted(all_targets.items())},
            intermediates={k: sorted(v) for k, v in sorted(all_intermediates.items())},
        )

        all_tables = set(all_sources) | set(all_targets) | set(all_intermediates)

        elapsed_ms = (time.time() - start_time) * 1000

        metadata = LineageMetadata(
            files_processed=1 if source_file else 0,
            statements_parsed=len(statements),
            edges_count=len(all_edges),
            tables_count=len(all_tables),
            dialect=self.dialect,
            errors=errors,
            processing_time_ms=round(elapsed_ms, 2),
        )

        return LineageGraph(
            edges=all_edges,
            tables=table_info,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Statement processing
    # ------------------------------------------------------------------

    def _process_statement(
        self, stmt: exp.Statement, source_file: str | None = None
    ) -> list[LineageEdge]:
        """Process a single SQL statement and return lineage edges."""
        target_table = SQLParser.get_target_table(stmt)
        select = SQLParser.get_select_from_statement(stmt)

        if select is None:
            return []

        return self._trace_select(select, target_table=target_table, source_file=source_file)

    # ------------------------------------------------------------------
    # SELECT tracer
    # ------------------------------------------------------------------

    def _trace_select(
        self,
        query: exp.Select | exp.Union,
        target_table: str | None = None,
        ctes: dict[str, exp.Statement] | None = None,
        source_file: str | None = None,
        parent_context: TraceContext | None = None,
    ) -> list[LineageEdge]:
        """
        Trace all output columns of a SELECT/UNION query.

        Builds the trace context (table aliases, CTEs) at this scope level,
        then processes each SELECT expression.
        """
        # Merge CTEs from this query with inherited ones
        local_ctes = SQLParser.extract_ctes(query)
        merged_ctes = dict(ctes or {})
        merged_ctes.update(local_ctes)

        # Build table alias mapping (FROM + JOINs)
        aliases = SQLParser.get_table_aliases(query)

        # Handle UNION: process each branch separately and merge
        if isinstance(query, exp.Union):
            return self._trace_union(query, target_table, merged_ctes, source_file, parent_context)

        context = TraceContext(
            aliases=aliases,
            ctes=merged_ctes,
            parent=parent_context,
        )

        select_exprs = SQLParser.get_select_expressions(query)
        edges: list[LineageEdge] = []

        for expr in select_exprs:
            line_edges = self._trace_select_expr(
                expr, context, target_table, source_file
            )
            edges.extend(line_edges)

        return edges

    def _trace_union(
        self,
        union: exp.Union,
        target_table: str | None,
        ctes: dict[str, exp.Statement],
        source_file: str | None,
        parent_context: TraceContext | None,
    ) -> list[LineageEdge]:
        """Trace UNION query by processing each branch separately."""
        left = union.args.get("left")
        right = union.args.get("right")

        left_edges = []
        right_edges = []

        if isinstance(left, (exp.Select, exp.Union)):
            left_edges = self._trace_select(
                left, target_table=target_table, ctes=ctes,
                source_file=source_file, parent_context=parent_context,
            )

        if isinstance(right, (exp.Select, exp.Union)):
            right_edges = self._trace_select(
                right, target_table=target_table, ctes=ctes,
                source_file=source_file, parent_context=parent_context,
            )

        return left_edges + right_edges

    # ------------------------------------------------------------------
    # SELECT expression tracer
    # ------------------------------------------------------------------

    def _trace_select_expr(
        self,
        expr: exp.Expression,
        context: TraceContext,
        target_table: str | None,
        source_file: str | None,
    ) -> list[LineageEdge]:
        """
        Trace a single SELECT expression (one output column).

        Returns one or more LineageEdge objects (star expansion yields multiple).
        """
        target_name = _extract_output_name(expr)

        # Star expansion: SELECT *
        if isinstance(expr, exp.Star):
            return self._expand_star(context, target_table, source_file)

        # Get the inner expression (unwrapping aliases)
        inner_expr = _unwrap_alias(expr)

        # Trace the expression to source columns
        sources = self._trace_expression(inner_expr, context)

        transformation, expression_str = _classify_expression(inner_expr)

        target_col = TargetColumn(
            table=target_table or "__output__",
            column=target_name,
            expression=expression_str,
        )

        edge = LineageEdge(
            target=target_col,
            sources=sources,
            transformation=transformation,
            expression=expression_str,
            confidence=_compute_confidence(sources),
            source_file=source_file,
        )

        return [edge]

    def _expand_star(
        self,
        context: TraceContext,
        target_table: str | None,
        source_file: str | None,
    ) -> list[LineageEdge]:
        """
        Handle SELECT * expansion.

        We can't know the actual columns without schema info, so we create
        a single edge per source table with a wildcard marker.
        """
        edges = []
        for table_name in context.aliases.values():
            if table_name.startswith("__subquery__"):
                continue
            target_col = TargetColumn(
                table=target_table or "__output__",
                column=f"{table_name}.*",
                expression=f"{table_name}.*",
            )
            src = SourceColumn(
                table=table_name,
                column="*",
                relation=RelationType.DIRECT,
            )
            edge = LineageEdge(
                target=target_col,
                sources=[src],
                transformation=TransformationType.DIRECT,
                expression=f"{table_name}.*",
                confidence=1.0,
                source_file=source_file,
            )
            edges.append(edge)
        return edges

    # ------------------------------------------------------------------
    # Expression tracer (recursive)
    # ------------------------------------------------------------------

    def _trace_expression(
        self,
        expr: exp.Expression,
        context: TraceContext,
    ) -> list[SourceColumn]:
        """
        Recursively trace a SQL expression to its source columns.

        This is the core resolution function that handles all expression types.
        """

        if expr is None:
            return []

        # --- Literal / constant ---
        if isinstance(expr, exp.Literal):
            return [
                SourceColumn(
                    table="__constants__",
                    column=str(expr).lower(),
                    relation=RelationType.DIRECT,
                )
            ]

        # --- Column reference (e.g., `o.price` or `price`) ---
        if isinstance(expr, exp.Column):
            return self._resolve_column(expr, context)

        # --- Alias: unwrap and trace inner ---
        if isinstance(expr, exp.Alias):
            return self._trace_expression(expr.args.get("this"), context)

        # --- Star: SELECT * ---
        if isinstance(expr, exp.Star):
            all_sources = []
            for table_name in context.aliases.values():
                if not table_name.startswith("__subquery__"):
                    all_sources.append(
                        SourceColumn(
                            table=table_name,
                            column="*",
                            relation=RelationType.DIRECT,
                        )
                    )
            return all_sources

        # --- Binary expression (a + b, a * b, etc.) ---
        if isinstance(expr, exp.Binary):
            left = self._trace_expression(expr.left, context)
            right = self._trace_expression(expr.right, context)
            combined = _merge_sources(left, right)
            for src in combined:
                if src.relation == RelationType.DIRECT:
                    src.relation = RelationType.THROUGH_EXPRESSION
            return combined

        # --- Unary expression (-x, NOT x) ---
        if isinstance(expr, exp.Unary):
            traced = self._trace_expression(expr.this, context)
            for src in traced:
                if src.relation == RelationType.DIRECT:
                    src.relation = RelationType.THROUGH_EXPRESSION
            return traced

        # --- Function call ---
        if isinstance(expr, exp.Func):
            return self._trace_function(expr, context)

        # --- Anonymous function ---
        if isinstance(expr, exp.Anonymous):
            return self._trace_function_args(expr.args.get("expressions", []), context)

        # --- Cast: CAST(x AS type) ---
        if isinstance(expr, exp.Cast):
            return self._trace_expression(expr.args.get("this"), context)

        # --- Extract: EXTRACT(year FROM date) ---
        if isinstance(expr, exp.Extract):
            return self._trace_expression(expr.args.get("this"), context)

        # --- Subquery in expression ---
        if isinstance(expr, exp.Subquery):
            sub_select = expr.args.get("this")
            if isinstance(sub_select, (exp.Select, exp.Union)):
                sub_context = self._build_subquery_context(context)
                return self._trace_subquery_select(sub_select, sub_context)
            return []

        # --- Case: CASE WHEN ... THEN ... ELSE ... ---
        if isinstance(expr, exp.Case):
            sources: list[SourceColumn] = []
            for when_clause in expr.args.get("ifs", []):
                if isinstance(when_clause, exp.If):
                    cond = self._trace_expression(when_clause.this, context)
                    then = self._trace_expression(
                        when_clause.args.get("true"), context
                    )
                    sources.extend(cond)
                    sources.extend(then)
            else_clause = expr.args.get("default")
            if else_clause:
                sources.extend(self._trace_expression(else_clause, context))
            for src in sources:
                if src.relation == RelationType.DIRECT:
                    src.relation = RelationType.THROUGH_EXPRESSION
            return sources

        # --- Not expression: NOT x ---
        if isinstance(expr, exp.Not):
            return self._trace_expression(expr.this, context)

        # --- Paren: (expression) ---
        if isinstance(expr, exp.Paren):
            return self._trace_expression(expr.this, context)

        # --- Tuple: (a, b) ---
        if isinstance(expr, exp.Tuple):
            sources = []
            for arg in expr.args.get("expressions", []):
                sources.extend(self._trace_expression(arg, context))
            return sources

        # --- Select/Union nested (scalar subquery) ---
        if isinstance(expr, (exp.Select, exp.Union)):
            sub_context = self._build_subquery_context(context)
            return self._trace_subquery_select(expr, sub_context)

        # --- Identifier (unqualified, but not Column) ---
        if isinstance(expr, exp.Identifier):
            col = exp.Column(this=expr)
            return self._resolve_column(col, context)

        # --- Null literal ---
        if isinstance(expr, exp.Null):
            return []

        # --- Boolean literal ---
        if isinstance(expr, exp.Boolean):
            return [
                SourceColumn(
                    table="__constants__",
                    column=str(expr).lower(),
                    relation=RelationType.DIRECT,
                )
            ]

        # Fallback: try tracing child expressions
        logger.debug("Unknown expression type: %s (%s)", type(expr).__name__, expr)
        child_sources = self._trace_children(expr, context)
        if child_sources:
            return child_sources
        return [
            SourceColumn(
                table="__unknown__",
                column=str(expr)[:50],
                relation=RelationType.INDIRECT,
            )
        ]

    def _trace_function(
        self, expr: exp.Func, context: TraceContext
    ) -> list[SourceColumn]:
        """Trace a function call, detecting aggregation vs scalar."""
        args = []
        if isinstance(expr, exp.Anonymous):
            args = expr.args.get("expressions", [])
        else:
            args = _get_func_args(expr)

        is_agg = _is_aggregation_function(expr)
        sources = self._trace_function_args(args, context)
        for src in sources:
            if src.relation == RelationType.DIRECT:
                src.relation = (
                    RelationType.THROUGH_AGGREGATE
                    if is_agg
                    else RelationType.THROUGH_EXPRESSION
                )
        return sources

    def _trace_function_args(
        self, args: list, context: TraceContext
    ) -> list[SourceColumn]:
        """Trace all arguments to a function."""
        sources: list[SourceColumn] = []
        for arg in args:
            sources.extend(self._trace_expression(arg, context))
        return _deduplicate_sources(sources)

    def _trace_children(
        self, expr: exp.Expression, context: TraceContext
    ) -> list[SourceColumn]:
        """Fallback: trace all child expression nodes."""
        sources: list[SourceColumn] = []
        for child in expr.args.values():
            if isinstance(child, exp.Expression):
                sources.extend(self._trace_expression(child, context))
            elif isinstance(child, list):
                for item in child:
                    if isinstance(item, exp.Expression):
                        sources.extend(self._trace_expression(item, context))
        return _deduplicate_sources(sources)

    # ------------------------------------------------------------------
    # Column resolution
    # ------------------------------------------------------------------

    def _resolve_column(
        self, column: exp.Column, context: TraceContext
    ) -> list[SourceColumn]:
        """
        Resolve a column reference to source table + column name.

        Handles qualified (table.column) and unqualified (column) references.
        Uses CTE definitions and table aliases in scope.
        """
        col_name = _column_name(column)
        table_name = _column_table(column)

        if table_name:
            # Qualified reference: resolve alias or table name
            resolved_table = context.resolve_alias(table_name)
            return [
                SourceColumn(
                    table=resolved_table,
                    column=col_name,
                    relation=RelationType.DIRECT,
                )
            ]

        # Unqualified column: search all available tables
        candidates = context.find_table_for_column(col_name)
        if candidates:
            sources = [
                SourceColumn(
                    table=table,
                    column=col_name,
                    relation=RelationType.DIRECT,
                )
                for table in candidates
            ]
            return sources

        # Column not found: return low confidence
        return [
            SourceColumn(
                table="__unresolved__",
                column=col_name,
                relation=RelationType.INDIRECT,
            )
        ]

    def _trace_subquery_select(
        self,
        query: exp.Select | exp.Union,
        context: TraceContext,
    ) -> list[SourceColumn]:
        """
        Trace all output columns of a subquery SELECT and return them as sources.
        This is used when a subquery appears in an expression context (scalar subquery).
        """
        edges = self._trace_select(query, ctes=context.ctes, parent_context=context)
        sources: list[SourceColumn] = []
        for edge in edges:
            for src in edge.sources:
                sources.append(
                    SourceColumn(
                        table=src.table,
                        column=src.column,
                        relation=RelationType.INDIRECT,
                    )
                )
        return sources

    def _build_subquery_context(self, context: TraceContext) -> TraceContext:
        """Build a fresh context for subquery resolution."""
        return TraceContext(
            aliases={},
            ctes=context.ctes,
            parent=context,
        )


# ------------------------------------------------------------------
# TraceContext: scope management
# ------------------------------------------------------------------


class TraceContext:
    """
    Resolution context for tracing column references.

    Maintains the current scope's table aliases, available CTEs,
    and a link to the parent scope for nested subquery resolution.
    """

    def __init__(
        self,
        aliases: dict[str, str] | None = None,
        ctes: dict[str, exp.Statement] | None = None,
        parent: TraceContext | None = None,
    ):
        self.aliases = aliases or {}
        self.ctes = ctes or {}
        self.parent = parent

    def resolve_alias(self, name: str) -> str:
        """Resolve a table alias to its fully qualified name."""
        key = name.lower().strip('"').strip("`")
        # Direct alias match
        if key in self.aliases:
            return self.aliases[key]
        # Might be a CTE reference
        if key in self.ctes:
            return key
        # Might already be a table name
        for table_name in self.aliases.values():
            if table_name.lower() == key:
                return table_name
        # Search parent scopes
        if self.parent:
            return self.parent.resolve_alias(name)
        return name

    def find_table_for_column(self, col_name: str) -> list[str]:
        """
        Find table(s) that contain a column with the given name.
        Returns all candidates (for ambiguity detection).
        """
        candidates: list[str] = []

        # Check CTEs first
        cte_lower = col_name.lower()
        for cte_name, cte_query in self.ctes.items():
            if isinstance(cte_query, (exp.Select, exp.Union)):
                cte_cols = SQLParser.get_select_expressions(cte_query)
                for cte_col in cte_cols:
                    output_name = _extract_output_name(cte_col)
                    if output_name.lower() == cte_lower:
                        candidates.append(cte_name)

        # Check table aliases
        for table_name in self.aliases.values():
            if not table_name.startswith("__subquery__"):
                candidates.append(table_name)

        # Search parent scopes
        if not candidates and self.parent:
            return self.parent.find_table_for_column(col_name)

        return candidates

    def is_cte(self, name: str) -> bool:
        """Check if a name refers to a CTE."""
        return name.lower() in self.ctes


# ------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------


def _extract_output_name(expr: exp.Expression) -> str:
    """Extract the output column name from a SELECT expression."""
    # Handle alias
    alias = expr.args.get("alias")
    if alias:
        return alias.sql(dialect="trino").strip('"').strip("`")

    # Handle star
    if isinstance(expr, exp.Star):
        return "*"

    # Handle column
    if isinstance(expr, exp.Column):
        return expr.name

    # For other expressions, use the SQL representation
    return expr.sql(dialect="trino")


def _unwrap_alias(expr: exp.Expression) -> exp.Expression:
    """Unwrap alias nodes to get the inner expression."""
    if isinstance(expr, exp.Alias):
        inner = expr.args.get("this")
        if inner:
            return inner
    return expr


def _column_name(column: exp.Column) -> str:
    """Extract column name from a Column node."""
    this = column.args.get("this")
    if isinstance(this, exp.Identifier):
        return this.name
    return str(column)


def _column_table(column: exp.Column) -> str | None:
    """Extract table qualifier from a Column node (e.g., 'o' from 'o.price')."""
    table = column.args.get("table")
    if table:
        return table.sql(dialect="trino").strip('"').strip("`")
    return None


def _get_func_args(func: exp.Func) -> list:
    """Get arguments from a function expression."""
    if isinstance(func, exp.Anonymous):
        return func.args.get("expressions", [])
    # For named functions, get all expression-type args
    args = []
    for key, value in func.args.items():
        if isinstance(value, exp.Expression) and key not in ("distinct", "order"):
            args.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, exp.Expression):
                    args.append(item)
    return args


def _is_aggregation_function(expr: exp.Func) -> bool:
    """Check if a function is an aggregation."""
    agg_funcs = {
        "sum", "count", "avg", "min", "max",
        "array_agg", "collect", "collect_set", "collect_list",
        "approx_distinct", "approx_percentile",
        "bitwise_and", "bitwise_or", "bitwise_xor",
        "bool_and", "bool_or",
        "corr", "covar_samp", "covar_pop",
        "every", "some",
        "histogram",
        "kurtosis", "skewness",
        "regr_*",
        "stddev", "stddev_pop", "stddev_samp",
        "variance", "var_pop", "var_samp",
        "count_if",
        "listagg", "string_agg",
        "multiset_union",
        "percentile_cont", "percentile_disc",
        "rank", "dense_rank", "row_number",
        "ntile", "lead", "lag", "first_value", "last_value",
        "nth_value",
    }
    func_name = expr.sql_name().lower()
    if func_name in agg_funcs:
        return True
    if isinstance(expr, exp.Window):
        return True
    if isinstance(expr, exp.AggFunc):
        return True
    # Check if it's a known agg
    return func_name in {
        "sum", "count", "avg", "min", "max",
        "array_agg", "collect",
    }


def _classify_expression(
    expr: exp.Expression,
) -> tuple[TransformationType, str | None]:
    """Classify an expression into a transformation type."""
    expr_str = expr.sql(dialect="trino")

    if isinstance(expr, exp.Literal):
        return TransformationType.CONSTANT, expr_str

    if isinstance(expr, exp.Column):
        return TransformationType.DIRECT, expr_str

    if isinstance(expr, exp.Func):
        if _is_aggregation_function(expr):
            return TransformationType.AGGREGATE, expr_str
        # Check for window function: has OVER clause
        if isinstance(expr, exp.Window) or expr.args.get("over") is not None:
            return TransformationType.WINDOW, expr_str
        return TransformationType.EXPRESSION, expr_str

    if isinstance(expr, (exp.Binary, exp.Unary, exp.Cast, exp.Extract, exp.Case)):
        return TransformationType.EXPRESSION, expr_str

    if isinstance(expr, (exp.Select, exp.Union, exp.Subquery)):
        return TransformationType.EXPRESSION, expr_str

    return TransformationType.UNKNOWN, expr_str


def _compute_confidence(sources: list[SourceColumn]) -> float:
    """Compute confidence based on resolution quality."""
    if not sources:
        return 0.5
    n_unresolved = sum(1 for s in sources if s.table in ("__unresolved__", "__unknown__"))
    if n_unresolved == len(sources):
        return 0.3
    if n_unresolved > 0:
        return 0.7
    return 1.0


def _deduplicate_sources(sources: list[SourceColumn]) -> list[SourceColumn]:
    """Deduplicate a flat list of source columns by table.column."""
    seen: set[tuple[str, str]] = set()
    result: list[SourceColumn] = []
    for s in sources:
        key = (s.table, s.column)
        if key not in seen:
            seen.add(key)
            result.append(s)
    return result


def _merge_sources(*source_lists: list[SourceColumn]) -> list[SourceColumn]:
    """Merge multiple source lists, deduplicating by table.column."""
    seen: set[tuple[str, str]] = set()
    merged: list[SourceColumn] = []
    for slist in source_lists:
        for s in slist:
            key = (s.table, s.column)
            if key not in seen:
                seen.add(key)
                merged.append(s)
    return merged


def _separate_intermediates(
    sources: dict[str, set[str]],
    targets: dict[str, set[str]],
    intermediates: dict[str, set[str]],
) -> None:
    """
    Move CTE-like entries (in both sources and targets, or starting with __)
    to intermediates.
    """
    to_remove_src = []
    for table in list(sources):
        if table.startswith("__"):
            intermediates[table] = sources[table]
            to_remove_src.append(table)
    for table in to_remove_src:
        del sources[table]

    # Tables that appear in both sources and targets are intermediates
    common = set(sources.keys()) & set(targets.keys())
    for table in common:
        intermediates[table] = sources[table] | targets[table]
        del sources[table]
        del targets[table]


class LineageGraph:
    """
    Complete lineage graph with edges, table info, and metadata.

    Constructed by LineageEngine.extract() and consumed by GraphRenderer.
    """

    def __init__(
        self,
        edges: list[LineageEdge],
        tables: TableInfo,
        metadata: LineageMetadata,
    ):
        self.edges = edges
        self.tables = tables
        self.metadata = metadata

    def to_dict(self) -> dict:
        """Serialize to a dictionary."""
        return {
            "lineage": [e.model_dump() for e in self.edges],
            "tables": self.tables.model_dump(),
            "metadata": self.metadata.model_dump(),
        }
