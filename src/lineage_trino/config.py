"""
Configuration for trino-lineage.

Settings loaded from environment variables or .env file (file ownership: Module G).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with environment variable overrides."""

    dialect: str = "trino"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    output_dir: str = "lineage_output"
    graph_dpi: int = 150
    graph_bgcolor: str = "#f8f9fa"
    graph_font: str = "Helvetica"
    graph_node_fill: str = "#1a73e8"
    graph_node_text: str = "#ffffff"

    model_config = {
        "env_prefix": "TRINO_LINEAGE_",
        "env_file": ".env",
        "extra": "ignore",
    }


settings = Settings()
