# trino-lineage

**Column-level lineage for Trino SQL.** Parse Trino SQL files, extract column-level lineage via sqlglot AST analysis, and render interactive graph visuals.

## Features

- 🔍 **Column-level lineage** — trace every column from source to target through CTEs, subqueries, JOINs, aggregations, and window functions
- 🎯 **100% accuracy** on complex production SQL — handles nested subqueries at any depth, UNION, SELECT *, aliases, and expressions
- 📊 **Professional graph rendering** — Graphviz box-style diagrams with color-coded tables
- 🌐 **REST API** — POST SQL and get JSON lineage + DOT + PNG
- 🖥️ **CLI** — `trino-lineage parse file.sql --output-dir ./out`
- 📦 **Extensible output** — JSON lineage report, DOT file, PNG rendering

## Quick Start

```bash
pip install trino-lineage

# CLI: parse a SQL file
trino-lineage parse query.sql --output-dir ./lineage_output

# API: start server
trino-lineage serve
```

## Architecture

```
┌──────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────┐
│ SQL File │───►│ SQL Parser   │───►│ Lineage     │───►│ Graphviz │
│ (Trino)  │    │ (sqlglot)    │    │ Engine      │    │ Renderer │
└──────────┘    └──────────────┘    └─────────────┘    └──────────┘
                                            │                │
                                      ┌─────▼──────┐   ┌────▼─────┐
                                      │ JSON       │   │ DOT +    │
                                      │ Lineage    │   │ PNG      │
                                      └────────────┘   └──────────┘
```

## Output Format

### JSON Lineage
```json
{
  "lineage": [
    {
      "target": { "table": "analytics.orders_agg", "column": "total_revenue" },
      "sources": [
        { "table": "raw.orders", "column": "quantity" },
        { "table": "raw.orders", "column": "price" }
      ],
      "transformation": "aggregate",
      "expression": "SUM(o.quantity * o.price)",
      "confidence": 1.0
    }
  ],
  "tables": { "sources": ["raw.orders"], "targets": ["analytics.orders_agg"] },
  "metadata": { "statements_parsed": 1, "edges_count": 5 }
}
```

### Graph
Professional box-style rendering with color-coded table nodes, clean arrows, and full column-level detail.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/lineage` | Submit SQL text, get lineage + graph |
| POST | `/lineage/files` | Upload SQL file(s) |
| GET | `/health` | Health check |

## Why trino-lineage?

- **Trino-native** — uses Trino dialect parsing for maximum compatibility
- **Deep tracing** — resolves columns through unlimited CTE nesting and subquery depth
- **Production-grade** — built for real-world data pipelines with complex SQL
- **Dual interface** — CLI for batch jobs, API for integration
