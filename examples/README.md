# trino-lineage Examples

This directory contains end-to-end examples demonstrating the column-level lineage capabilities.

## Files

| File | Description |
|------|-------------|
| `simple.sql` | Basic SQL patterns: SELECT, alias, expression, aggregation, JOIN, CTE |
| `complex.sql` | Production-scale ETL: 3 CTEs, 6-way JOINs, window functions, CASE, UNION, CREATE+INSERT |
| `run_examples.py` | Run the full pipeline on all examples and save outputs |
| `output/` | Generated output files (after running) |

## Running

```bash
# From the project root (with package installed)
python examples/run_examples.py

# Or directly
cd examples && python run_examples.py
```

## Output Files

After running, `examples/output/` contains:

- `simple_lineage.json` — Full JSON lineage for simple example
- `simple_lineage.dot` — Graphviz DOT graph
- `simple_lineage.png` — Rendered graph (requires Graphviz binary)
- `complex_lineage.json` — Full JSON lineage for complex example
- `complex_lineage.dot` — DOT graph for complex SQL
- `complex_lineage.png` — Rendered complex graph
- `inline_lineage.*` — Results for inline SQL demo

## Expected Results

### Simple Example
- 13+ lineage edges across 5 SQL statements
- Direct mappings (column → column)
- Expression mappings (price * quantity)
- Aggregation mappings (COUNT, AVG)
- CTE chaining

### Complex Example
- 40+ lineage edges from a single multi-stage ETL
- Window functions (ROW_NUMBER, RANK, LAG, LEAD, SUM OVER)
- CASE expression decomposition
- 6+ table JOIN resolution
- UNION merging
- Multiple output tables (CREATE TABLE AS, INSERT INTO)
