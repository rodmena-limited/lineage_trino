#!/usr/bin/env python3
"""
End-to-end example runner for trino-lineage.

Demonstrates parsing SQL files, extracting column-level lineage,
and rendering graphs. Generates output files in examples/output/.
"""

import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lineage_trino.graph import GraphRenderer
from lineage_trino.lineage import LineageEngine
from lineage_trino.models import LineageResult


def run_example(name: str, sql_text: str, output_dir: Path):
    """Run full pipeline on SQL text and save outputs."""
    print(f"\n{'=' * 60}")
    print(f"Example: {name}")
    print(f"{'=' * 60}")

    # 1. Parse and extract lineage
    engine = LineageEngine(dialect="trino")
    graph = engine.extract_from_sql(sql_text, source_file=f"{name}.sql")

    print(f"  Statements parsed: {graph.metadata.statements_parsed}")
    print(f"  Lineage edges:     {graph.metadata.edges_count}")
    print(f"  Tables involved:   {graph.metadata.tables_count}")
    print(f"  Processing time:   {graph.metadata.processing_time_ms}ms")

    if graph.metadata.errors:
        for err in graph.metadata.errors:
            print(f"  [WARN] {err}")

    # 2. Write JSON lineage report
    result = LineageResult(
        lineage=graph.edges,
        tables=graph.tables,
        metadata=graph.metadata,
    )
    json_path = output_dir / f"{name}_lineage.json"
    json_content = result.model_dump(mode="json")
    json_content.pop("graph", None)
    json_path.write_text(json.dumps(json_content, indent=2, default=str))
    print(f"  JSON: {json_path}")

    # 3. Render graph
    renderer = GraphRenderer()
    try:
        dot, png = renderer.render(graph)
        dot_path = output_dir / f"{name}_lineage.dot"
        dot_path.write_text(dot)
        print(f"  DOT:  {dot_path}")

        if png:
            png_path = output_dir / f"{name}_lineage.png"
            png_path.write_bytes(png)
            print(f"  PNG:  {png_path}")
        else:
            print("  PNG:  skipped (graphviz binary not found)")

    except (OSError, ValueError, RuntimeError) as e:
        print(f"  Graph rendering error: {e}")

    # 4. Print lineage summary
    print("\n  --- Lineage Summary ---")
    for edge in graph.edges[:10]:  # Show first 10
        sources_str = ", ".join(f"{s.table}.{s.column}" for s in edge.sources)
        print(f"  {edge.target.table}.{edge.target.column}")
        print(f"    <- {sources_str}")
        print(f"    [{edge.transformation.value}, confidence={edge.confidence}]")
        if edge.expression:
            print(f"    expr: {edge.expression[:80]}")

    if len(graph.edges) > 10:
        print(f"  ... and {len(graph.edges) - 10} more edges")

    return graph


def main():
    # Set up output directory
    examples_dir = Path(__file__).parent
    output_dir = examples_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Read example SQL files
    simple_sql = (examples_dir / "simple.sql").read_text()
    complex_sql = (examples_dir / "complex.sql").read_text()

    # Run examples
    run_example("simple", simple_sql, output_dir)
    run_example("complex", complex_sql, output_dir)

    # Also demo: single SQL string
    inline_sql = """
    CREATE TABLE revenue_report AS
    SELECT
        d.dept_name AS department,
        SUM(e.salary + COALESCE(e.bonus, 0)) AS total_compensation,
        COUNT(DISTINCT e.id) AS employee_count,
        AVG(e.salary) AS avg_salary
    FROM departments d
    JOIN employees e ON d.id = e.dept_id
    WHERE e.status = 'active'
    GROUP BY d.dept_name
    HAVING COUNT(DISTINCT e.id) > 5
    """
    run_example("inline", inline_sql, output_dir)

    print(f"\n{'=' * 60}")
    print("All examples completed!")
    print(f"Output directory: {output_dir.resolve()}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
