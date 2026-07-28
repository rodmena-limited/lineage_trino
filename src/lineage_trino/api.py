"""
FastAPI REST API for trino-lineage.

Provides endpoints to submit SQL for column-level lineage analysis
and retrieve results as JSON with optional graph rendering
(file ownership: Module E).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from lineage_trino.config import settings
from lineage_trino.graph import GraphRenderer
from lineage_trino.lineage import LineageEngine
from lineage_trino.models import (
    ErrorResponse,
    GraphOutput,
    LineageMetadata,
    LineageRequest,
    LineageResult,
    TableInfo,
)
from lineage_trino.parser import SQLParseError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan for startup/shutdown events."""
    logger.info(
        "trino-lineage API starting on %s:%s", settings.host, settings.port
    )
    yield
    logger.info("trino-lineage API shutting down")


app = FastAPI(
    title="trino-lineage API",
    description="Column-level lineage extraction for Trino SQL. "
    "Submit SQL and get JSON lineage, DOT, and PNG graph.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "dialect": settings.dialect,
    }


@app.post(
    "/lineage",
    response_model=LineageResult,
    responses={
        400: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def analyze_lineage(request: LineageRequest):
    """
    Submit SQL text for column-level lineage analysis.

    Returns JSON lineage, DOT graph, and optional PNG rendering.
    """
    engine = LineageEngine(dialect=request.dialect)
    renderer = GraphRenderer()

    try:
        statements = engine.parser.parse(
            request.sql, source_file=request.source_file
        )
    except SQLParseError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    graph = engine.extract(statements, source_file=request.source_file)

    graph_output = None
    if request.include_graph and graph.edges:
        try:
            dot, png = renderer.render(graph)
            graph_output = GraphOutput(
                dot=dot,
                png_base64=renderer.png_to_base64(png) if png else None,
            )
        except Exception as e:
            logger.warning("Failed to render graph: %s", e)

    result = LineageResult(
        lineage=graph.edges,
        tables=graph.tables,
        graph=graph_output,
        metadata=graph.metadata,
    )

    return result


@app.post(
    "/lineage/files",
    response_model=LineageResult,
    responses={
        400: {"model": ErrorResponse},
    },
)
async def analyze_lineage_files(
    files: list[UploadFile] = File(...),
    dialect: str = "trino",
    include_graph: bool = True,
):
    """
    Upload one or more SQL files for lineage analysis.

    Returns merged lineage results from all files.
    """
    engine = LineageEngine(dialect=dialect)
    renderer = GraphRenderer()

    all_edges: list[Any] = []
    all_sources: dict[str, set[str]] = {}
    all_targets: dict[str, set[str]] = {}
    all_intermediates: dict[str, set[str]] = {}
    total_statements = 0
    errors: list[str] = []

    for upload in files:
        try:
            content = await upload.read()
            sql_text = content.decode("utf-8")
        except Exception as e:
            errors.append(f"Failed to read {upload.filename}: {e}")
            continue

        try:
            statements = engine.parser.parse(sql_text, source_file=upload.filename)
        except SQLParseError as e:
            errors.append(f"Parse error in {upload.filename}: {e}")
            continue

        total_statements += len(statements)
        graph = engine.extract(statements, source_file=upload.filename)
        all_edges.extend(graph.edges)

        for table, cols in graph.tables.sources.items():
            all_sources.setdefault(table, set()).update(cols)
        for table, cols in graph.tables.targets.items():
            all_targets.setdefault(table, set()).update(cols)
        for table, cols in graph.tables.intermediates.items():
            all_intermediates.setdefault(table, set()).update(cols)

    table_info = TableInfo(
        sources={k: sorted(v) for k, v in sorted(all_sources.items())},
        targets={k: sorted(v) for k, v in sorted(all_targets.items())},
        intermediates={k: sorted(v) for k, v in sorted(all_intermediates.items())},
    )

    all_tables = set(all_sources) | set(all_targets) | set(all_intermediates)
    metadata = LineageMetadata(
        files_processed=len(files),
        statements_parsed=total_statements,
        edges_count=len(all_edges),
        tables_count=len(all_tables),
        dialect=dialect,
        errors=errors,
    )

    # Build a minimal graph for rendering
    from lineage_trino.lineage import LineageGraph
    full_graph = LineageGraph(
        edges=all_edges,
        tables=table_info,
        metadata=metadata,
    )

    graph_output = None
    if include_graph and all_edges:
        try:
            dot, png = renderer.render(full_graph)
            graph_output = GraphOutput(
                dot=dot,
                png_base64=renderer.png_to_base64(png) if png else None,
            )
        except Exception as e:
            logger.warning("Failed to render graph: %s", e)

    return LineageResult(
        lineage=all_edges,
        tables=table_info,
        graph=graph_output,
        metadata=metadata,
    )


def create_app() -> FastAPI:
    """Factory function for the FastAPI app."""
    return app
