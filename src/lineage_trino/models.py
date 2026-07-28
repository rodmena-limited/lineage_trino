"""
Data models for lineage-trino.

Pydantic models for input/output serialization of column-level lineage data.
All public types used across modules are defined here (file ownership: Module A).
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class RelationType(str, enum.Enum):
    """How a source column relates to the target column."""

    DIRECT = "direct"
    INDIRECT = "indirect"
    THROUGH_EXPRESSION = "through_expression"
    THROUGH_AGGREGATE = "through_aggregate"


class TransformationType(str, enum.Enum):
    """Type of transformation applied to produce the target column."""

    DIRECT = "direct"
    EXPRESSION = "expression"
    AGGREGATE = "aggregate"
    WINDOW = "window"
    CONSTANT = "constant"
    UNKNOWN = "unknown"


class SourceColumn(BaseModel):
    """A source column that contributes to a target column."""

    table: str = Field(description="Source table name (may include schema)")
    column: str = Field(description="Source column name")
    relation: RelationType = Field(
        default=RelationType.DIRECT,
        description="How this source relates to the target",
    )
    alias: str | None = Field(
        default=None, description="Alias used in the query for this column"
    )


class TargetColumn(BaseModel):
    """The target column being produced."""

    table: str = Field(description="Target table or output name")
    column: str = Field(description="Target column name")
    expression: str | None = Field(
        default=None, description="SQL expression producing this column"
    )


class LineageEdge(BaseModel):
    """A single column-level lineage edge from source(s) to one target column."""

    target: TargetColumn = Field(description="The target column")
    sources: list[SourceColumn] = Field(
        description="Source columns that feed into this target"
    )
    transformation: TransformationType = Field(
        default=TransformationType.DIRECT,
        description="Type of transformation applied",
    )
    expression: str | None = Field(
        default=None, description="Full SQL expression if applicable"
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in this lineage edge (0.0 = uncertain, 1.0 = certain)",
    )
    source_file: str | None = Field(
        default=None, description="Source SQL file for this edge"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Warnings about this edge"
    )


class TableInfo(BaseModel):
    """Information about tables involved in the lineage."""

    sources: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Source tables and their columns: {table: [columns]}",
    )
    targets: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Target tables and their columns: {table: [columns]}",
    )
    intermediates: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Intermediate tables/CTEs and their columns",
    )


class LineageMetadata(BaseModel):
    """Metadata about the lineage extraction run."""

    schema_version: str = Field(default="1.0", description="JSON schema version")
    generated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO timestamp of generation",
    )
    files_processed: int = Field(default=0, description="Number of SQL files processed")
    statements_parsed: int = Field(default=0, description="Number of SQL statements parsed")
    edges_count: int = Field(default=0, description="Total lineage edges")
    tables_count: int = Field(default=0, description="Total unique tables")
    dialect: str = Field(default="trino", description="SQL dialect used for parsing")
    errors: list[str] = Field(default_factory=list, description="Errors encountered")
    processing_time_ms: float = Field(
        default=0.0, description="Processing time in milliseconds"
    )


class GraphOutput(BaseModel):
    """Graph visualization output."""

    dot: str = Field(description="Graphviz DOT format string")
    png_base64: str | None = Field(
        default=None, description="Base64-encoded PNG rendering (null if graphviz binary unavailable)"
    )


class LineageResult(BaseModel):
    """Complete lineage analysis result."""

    lineage: list[LineageEdge] = Field(
        default_factory=list, description="All lineage edges"
    )
    tables: TableInfo = Field(
        default_factory=TableInfo, description="Table information"
    )
    graph: GraphOutput | None = Field(
        default=None, description="Graph visualization output"
    )
    metadata: LineageMetadata = Field(
        default_factory=LineageMetadata, description="Run metadata"
    )


class LineageRequest(BaseModel):
    """API request for lineage analysis."""

    sql: str = Field(description="Trino SQL text to analyze")
    dialect: str = Field(default="trino", description="SQL dialect")
    include_graph: bool = Field(
        default=True, description="Whether to include graph visualization"
    )
    source_file: str | None = Field(
        default=None, description="Source filename for tracking"
    )


class LineageFileRequest(BaseModel):
    """API request with file uploads for lineage analysis."""

    files: list[Any] = Field(description="SQL files to analyze")
    dialect: str = Field(default="trino", description="SQL dialect")
    include_graph: bool = Field(
        default=True, description="Whether to include graph visualization"
    )


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str = Field(description="Error message")
    detail: str | None = Field(default=None, description="Detailed error information")
    status_code: int = Field(default=400, description="HTTP status code")
