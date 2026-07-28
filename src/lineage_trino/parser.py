"""
Trino SQL Parser.

Wraps sqlglot to parse Trino SQL into statements and provides
AST utility functions for the lineage engine (file ownership: Module B).

All parsing goes through sqlglot exclusively (UBIQ-4).
"""

from __future__ import annotations

import logging
from pathlib import Path

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

SUPPORTED_DIALECTS = {"trino", "presto", "mysql", "postgres", "duckdb", "hive", "spark"}


class SQLParseError(Exception):
    """Raised when SQL parsing fails."""


class SQLParser:
    """
    Parse Trino SQL files into sqlglot AST statements.

    Provides methods for parsing individual SQL strings and entire files,
    along with utility queries on the parsed AST.

    Usage:
        parser = SQLParser()
        statements = parser.parse("SELECT * FROM t")
        file_map = parser.parse_files(["/path/to/query.sql"])
    """

    def __init__(self, dialect: str = "trino"):
        if dialect not in SUPPORTED_DIALECTS:
            logger.warning(
                "Unsupported dialect '%s'. Supported: %s. Proceeding anyway.",
                dialect,
                ", ".join(sorted(SUPPORTED_DIALECTS)),
            )
        self.dialect = dialect

    def parse(self, sql_text: str, source_file: str | None = None) -> list[exp.Statement]:
        """
        Parse a SQL string into a list of sqlglot AST statements.

        Args:
            sql_text: Trino SQL text to parse.
            source_file: Optional source filename for tracking.

        Returns:
            List of sqlglot Statement nodes.

        Raises:
            SQLParseError: If the SQL is syntactically invalid.
        """
        if not sql_text or not sql_text.strip():
            return []

        try:
            statements = sqlglot.parse(sql_text, dialect=self.dialect, error_level=sqlglot.ErrorLevel.RAISE)
            parsed: list[exp.Statement] = []
            for stmt in statements:
                if stmt is not None:
                    parsed.append(stmt)
            logger.debug("Parsed %d statement(s) from SQL", len(parsed))
            return parsed
        except sqlglot.errors.ParseError as e:
            msg = "Failed to parse SQL"
            if source_file:
                msg += f" in {source_file}"
            msg += f": {e}"
            raise SQLParseError(msg) from e

    def parse_files(self, paths: list[str]) -> dict[str, list[exp.Statement]]:
        """
        Parse multiple SQL files.

        Args:
            paths: List of file paths to SQL files.

        Returns:
            Dict mapping source file path to list of parsed statements.
        """
        result: dict[str, list[exp.Statement]] = {}
        for path in paths:
            path_obj = Path(path)
            if not path_obj.exists():
                logger.warning("File not found: %s", path)
                continue
            sql_text = path_obj.read_text(encoding="utf-8")
            try:
                statements = self.parse(sql_text, source_file=path)
                result[path] = statements
            except SQLParseError as e:
                logger.error("Skipping %s: %s", path, e)
                result[path] = []
        return result

    @staticmethod
    def is_select(statement: exp.Statement) -> bool:
        """Check if a statement is a SELECT (or CTE-wrapped SELECT)."""
        if isinstance(statement, exp.Select):
            return True
        if isinstance(statement, exp.Union):
            return True
        return False

    @staticmethod
    def is_ddl_with_select(statement: exp.Statement) -> bool:
        """Check if statement is a DDL that contains a SELECT (CREATE TABLE AS, INSERT INTO)."""
        if isinstance(statement, exp.Create):
            return statement.args.get("expression") is not None
        if isinstance(statement, exp.Insert):
            return statement.args.get("expression") is not None
        return False

    @staticmethod
    def get_target_table(statement: exp.Statement) -> str | None:
        """
        Extract the target table name from DDL/DML statements.

        Returns the fully qualified table name, or None for plain SELECT.
        """
        if isinstance(statement, exp.Create):
            table = statement.args.get("this")
            if isinstance(table, exp.Table):
                return table.sql(dialect="trino")
        if isinstance(statement, exp.Insert):
            table = statement.args.get("this")
            if isinstance(table, exp.Table):
                return table.sql(dialect="trino")
        return None

    @staticmethod
    def get_select_from_statement(statement: exp.Statement) -> exp.Select | exp.Union | None:
        """
        Extract the inner SELECT from a DDL/DML statement.
        For plain SELECT, return as-is. For CREATE/INSERT, extract the inner query.
        """
        if isinstance(statement, (exp.Select, exp.Union)):
            return statement
        if isinstance(statement, exp.Create):
            inner = statement.args.get("expression")
            if isinstance(inner, (exp.Select, exp.Union)):
                return inner
        if isinstance(statement, exp.Insert):
            inner = statement.args.get("expression")
            if isinstance(inner, (exp.Select, exp.Union)):
                return inner
        return None

    @staticmethod
    def extract_ctes(statement: exp.Select | exp.Union) -> dict[str, exp.Statement]:
        """
        Extract CTE (WITH clause) definitions from a statement.

        Returns:
            Dict of CTE name → CTE body AST node.
        """
        ctes: dict[str, exp.Statement] = {}
        # In sqlglot v30+, the WITH clause key is "with_" (not "with")
        with_clause = statement.args.get("with_")
        if with_clause:
            for cte in with_clause.args.get("expressions", []):
                if isinstance(cte, exp.CTE):
                    name = cte.alias_or_name.lower()
                    # The CTE body can be under "this" or "query"
                    inner = cte.args.get("this") or cte.args.get("query")
                    if inner:
                        ctes[name] = inner
        return ctes

    @staticmethod
    def get_select_expressions(query: exp.Select | exp.Union) -> list[exp.Expression]:
        """
        Get the SELECT expressions (output columns) from a query.

        In sqlglot v30+, expressions are stored directly at the top level
        under the "expressions" key (not nested under a "select" sub-node).
        For UNION, returns expressions from the first SELECT.
        """
        if isinstance(query, exp.Union):
            left = query.args.get("left")
            if isinstance(left, (exp.Select, exp.Union)):
                return SQLParser.get_select_expressions(left)
            return []
        # sqlglot v30+: Select stores expressions directly in args["expressions"]
        exprs = query.args.get("expressions", [])
        return exprs

    @staticmethod
    def get_table_aliases(query: exp.Select | exp.Union) -> dict[str, str]:
        """
        Build a mapping of table aliases → fully qualified table names.

        Returns:
            Dict: alias (lowercase) → full table name.
        """
        aliases: dict[str, str] = {}

        # sqlglot v30+: FROM clause is stored under key "from_"
        from_clause = query.args.get("from_")
        if from_clause:
            # The table is stored in from_.args["this"] (not "expressions")
            from_this = from_clause.args.get("this")
            if from_this:
                _extract_table_alias(from_this, aliases)

        joins = query.args.get("joins", [])
        for join in joins:
            join_target = join.args.get("this")
            if join_target:
                _extract_table_alias(join_target, aliases)

        return aliases


def _table_full_name(node: exp.Table) -> str:
    """Construct the fully qualified table name from a Table node, excluding alias."""
    parts = []
    catalog = node.args.get("catalog")
    db = node.args.get("db")
    if catalog:
        parts.append(catalog.name if hasattr(catalog, 'name') else str(catalog))
    if db:
        parts.append(db.name if hasattr(db, 'name') else str(db))
    parts.append(node.name)
    return ".".join(parts)


def _extract_table_alias(
    node: exp.Expression, aliases: dict[str, str]
) -> None:
    """Extract table name and alias from a FROM/JOIN target node."""
    if isinstance(node, exp.Table):
        full_name = _table_full_name(node)
        alias = node.alias
        key = (alias or full_name).lower()
        aliases[key] = full_name
    elif isinstance(node, exp.Subquery):
        alias = node.alias
        if alias:
            aliases[alias.lower()] = f"__subquery__{alias}"
    elif isinstance(node, exp.Select) or isinstance(node, exp.Union):
        pass
    elif isinstance(node, exp.Alias):
        inner = node.args.get("this")
        alias_name = node.args.get("alias")
        if isinstance(inner, exp.Table) and alias_name:
            full_name = _table_full_name(inner)
            aliases[alias_name.lower()] = full_name
        elif isinstance(inner, (exp.Select, exp.Union)) and alias_name:
            aliases[alias_name.lower()] = f"__subquery__{alias_name}"
