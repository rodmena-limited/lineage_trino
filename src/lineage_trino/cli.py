"""
CLI interface for trino-lineage.

Provides command-line parsing, file processing, and API server
commands via Typer (file ownership: Module F).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from lineage_trino.config import settings
from lineage_trino.graph import GraphRenderer
from lineage_trino.lineage import LineageEngine
from lineage_trino.models import LineageResult

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("trino-lineage")

app = typer.Typer(
    name="trino-lineage",
    help="Column-level lineage extraction for Trino SQL",
    add_completion=False,
)


@app.callback()
def callback():
    """trino-lineage: Parse Trino SQL, extract column-level lineage, render graphs."""


# ------------------------------------------------------------------
# Parse command
# ------------------------------------------------------------------


@app.command()
def parse(
    files: list[str] = typer.Argument(
        ..., help="SQL file(s) to parse", exists=True, readable=True
    ),
    output_dir: str = typer.Option(
        "lineage_output", "--output-dir", "-o",
        help="Output directory for generated files",
    ),
    dialect: str = typer.Option(
        "trino", "--dialect", "-d",
        help="SQL dialect (trino, presto, etc.)",
    ),
    pretty: bool = typer.Option(
        True, "--pretty/--compact",
        help="Pretty-print JSON output",
    ),
    skip_graph: bool = typer.Option(
        False, "--skip-graph", "-s",
        help="Skip graph rendering",
    ),
):
    """
    Parse SQL file(s) and extract column-level lineage.

    Generates JSON lineage report, DOT file, and PNG graph.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    engine = LineageEngine(dialect=dialect)
    renderer = GraphRenderer()

    # Parse all files
    parsed_files = engine.parser.parse_files(files)

    all_statements = []
    for file_path, stmts in parsed_files.items():
        all_statements.extend(stmts)
        typer.echo(f"  Parsed: {file_path} ({len(stmts)} statements)")

    if not all_statements:
        typer.echo("No statements parsed. Check your SQL files.", err=True)
        raise typer.Exit(code=1)

    # Extract lineage
    graph = engine.extract(all_statements)
    typer.echo(f"  Lineage: {graph.metadata.edges_count} edges, "
               f"{graph.metadata.tables_count} tables")

    # Write JSON output
    result = LineageResult(
        lineage=graph.edges,
        tables=graph.tables,
        metadata=graph.metadata,
    )

    json_path = output_path / "lineage.json"
    indent = 2 if pretty else None
    json_content = result.model_dump(mode="json")
    # Remove graph from JSON (we write it separately)
    json_content.pop("graph", None)
    json_path.write_text(json.dumps(json_content, indent=indent, default=str))
    typer.echo(f"  JSON: {json_path}")

    # Render graph
    if not skip_graph and graph.edges:
        try:
            dot, png = renderer.render(graph)
            dot_path = output_path / "lineage.dot"
            dot_path.write_text(dot)
            typer.echo(f"  DOT:  {dot_path}")

            if png:
                png_path = output_path / "lineage.png"
                png_path.write_bytes(png)
                typer.echo(f"  PNG:  {png_path}")
            else:
                typer.echo("  PNG:  skipped (graphviz binary not found)")
        except Exception as e:
            logger.warning("Graph rendering failed: %s", e)
            typer.echo(f"  Graph rendering failed: {e}", err=True)
    elif not graph.edges:
        typer.echo("  Graph: skipped (no lineage edges)")

    # Summary
    typer.echo(f"\nOutput directory: {output_path.resolve()}")
    typer.echo("Done.")


# ------------------------------------------------------------------
# Serve command
# ------------------------------------------------------------------


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-H", help="Bind address"),
    port: int = typer.Option(8000, "--port", "-p", help="Port number"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
):
    """
    Start the trino-lineage REST API server.
    """
    typer.echo(f"Starting trino-lineage API on {host}:{port}")
    typer.echo(f"API docs: http://localhost:{port}/docs")

    import uvicorn

    uvicorn.run(
        "lineage_trino.api:app",
        host=host,
        port=port,
        reload=reload,
        log_level=settings.log_level.lower(),
    )


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main():
    """CLI entry point."""
    app()


if __name__ == "__main__":
    main()
