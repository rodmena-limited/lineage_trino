"""
trino-lineage: Column-level lineage extraction for Trino SQL.

Parse Trino SQL files with sqlglot, extract column-level lineage,
and render results as JSON, DOT, and PNG.
"""

__version__ = "1.0.0"
__all__ = [
    "GraphRenderer",
    "LineageEdge",
    "LineageEngine",
    "LineageGraph",
    "LineageResult",
    "SQLParser",
]
