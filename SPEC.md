# TRUST5 SPEC: trino-lineage — Column-Level Lineage for Trino SQL

## 1. Requirement Description

A Python CLI/API tool that parses one or more Trino SQL files, extracts column-level lineage using sqlglot AST analysis, and renders the lineage as:
- A JSON report with structured lineage data
- A DOT file for Graphviz
- A PNG rendering of the lineage graph

The system must handle complex, production Trino SQL — including CTEs, subqueries, JOINs, aggregations, window functions, UNION clauses, and nested expressions — with 100% accuracy in column origin tracing.

## 2. Module Decomposition & File Ownership

| Module | File | Ownership | Public API |
|--------|------|-----------|------------|
| **models** | `src/lineage_trino/models.py` | A | `LineageEdge`, `LineageGraph`, `LineageResult`, `SourceColumn`, `TargetColumn`, `TableInfo`, `LineageMetadata` |
| **parser** | `src/lineage_trino/parser.py` | B | `SQLParser.parse(sql: str, dialect: str = 'trino') -> List[Statement]` |
| **lineage** | `src/lineage_trino/lineage.py` | C | `LineageEngine.extract(statements) -> LineageGraph` |
| **graph** | `src/lineage_trino/graph.py` | D | `GraphRenderer.render(graph: LineageGraph) -> tuple[str, bytes]` |
| **api** | `src/lineage_trino/api.py` | E | FastAPI app with `/lineage` POST endpoint |
| **cli** | `src/lineage_trino/cli.py` | F | CLI entry point |
| **config** | `src/lineage_trino/config.py` | G | Settings and configuration |

## 3. Interface Contracts

### Module A → B, C (models consumed by all)
```
LineageEdge:
  target_table: str  (schema.table or cte_name)
  target_column: str
  source_columns: list[SourceColumn]
  transformation_type: Literal["direct", "expression", "aggregate", "window", "constant", "unknown"]
  expression: str | None
  confidence: float  (0.0-1.0)

SourceColumn:
  table: str
  column: str
  relation: Literal["direct", "indirect", "through_expression", "through_aggregate"]

LineageGraph:
  edges: list[LineageEdge]
  tables: dict[str, set[str]]  # table_name -> set of columns
  metadata: LineageMetadata

LineageResult:
  lineage: list[LineageEdge]
  graph: dict  # DOT string + PNG bytes (base64)
  tables: dict
  metadata: LineageMetadata
```

### Module B → C
```
SQLParser.parse(sql_text: str, dialect: str = "trino") -> list[sqlglot.exp.Statement]
SQLParser.parse_files(paths: list[str]) -> dict[str, list[sqlglot.exp.Statement]]
```

### Module C → D
```
LineageEngine.extract(statements: list) -> LineageGraph
LineageEngine.extract_from_sql(sql: str) -> LineageGraph
```

### Module D → Output
```
GraphRenderer.render(graph: LineageGraph, format: str = "png") -> tuple[str, bytes]
GraphRenderer.render_to_file(graph: LineageGraph, output_dir: str) -> dict[str, str]
```

## 4. EARS-Tagged Acceptance Criteria

### UBIQ - Ubiquitous (always true)
- UBIQ-1: The package installs via `pip install trino-lineage` with all dependencies resolved
- UBIQ-2: Every public function has a docstring describing parameters and return values
- UBIQ-3: Source files do not exceed 500 lines each
- UBIQ-4: All SQL parsing uses sqlglot exclusively

### EVENT - Event-driven (triggered by specific action)
- EVENT-1: WHEN `POST /lineage` receives SQL text THEN return 200 with JSON lineage
- EVENT-2: WHEN `trino-lineage parse file.sql` is invoked THEN produce JSON + DOT + PNG
- EVENT-3: WHEN `trino-lineage serve` is invoked THEN start FastAPI server on port 8000
- EVENT-4: WHEN a SQL file contains a CTE THEN trace column lineage through the CTE
- EVENT-5: WHEN a SQL file contains JOINs THEN resolve column origins to correct source tables
- EVENT-6: WHEN a SQL file contains aggregations THEN mark lineage type as "aggregate" with source columns
- EVENT-7: WHEN an output column is aliased THEN use the alias as target column name

### STATE - State-based (true during certain conditions)
- STATE-1: WHILE parsing multiple files THEN lineage includes source file tracking per edge
- STATE-2: WHILE rendering graph THEN use professional box-style nodes with rounded corners
- STATE-3: WHILE the system has no Graphviz binary THEN return DOT string but skip PNG generation with warning

### UNWNT - Unwanted (failure conditions)
- UNWNT-1: IF SQL is syntactically invalid THEN return 400 with parse error details
- UNWNT-2: IF a file is not found THEN return 404 with clear error message
- UNWNT-3: IF column origin cannot be resolved THEN set confidence < 0.5 and add warning
- UNWNT-4: IF sqlglot version is incompatible THEN raise ImportError with version requirement

### OPTNL - Optional
- OPTNL-1: Upload multiple SQL files in a single API request
- OPTNL-2: Support for Presto dialect alongside Trino
- OPTNL-3: Configurable output directory for CLI
- OPTNL-4: Health check endpoint at GET /health

### COMPLX - Complex (combining multiple conditions)
- COMPLX-1: WHEN a column passes through multiple CTEs AND joins THEN trace full path with intermediate nodes
- COMPLX-2: WHEN SQL contains nested subqueries at ANY depth THEN resolve to deepest source columns
- COMPLX-3: WHEN SELECT * is used THEN expand to all columns from source tables
- COMPLX-4: WHEN UNION combines multiple SELECTs THEN merge lineage from all branches

## 5. Quality Thresholds

| Gate | Threshold | Tool |
|------|-----------|------|
| Tested | ≥90% coverage, 0 type errors, all tests pass | pytest, mypy |
| Readable | 0 lint errors, flake8 ≤ 10 warnings | ruff |
| Understandable | ≤10 pylint warnings, files ≤500 LOC, module docstrings | pylint, cloc |
| Secured | 0 HIGH/CRITICAL | bandit |
| Trackable | No spaces in filenames, test files exist for each module | shell check |

## 6. Setup Commands

```bash
pip install trino-lineage
# or from source:
git clone ... && cd trino-lineage && pip install -e ".[dev]"
```

## 7. Tests
- pytest with coverage
- Min 90% line coverage
- Test files mirror src structure under tests/
- Integration test in examples/ that validates end-to-end flow
