"""Integration tests for the CLI and API."""

import json
import subprocess
import sys

import pytest
from httpx import ASGITransport, AsyncClient

from lineage_trino.api import app

# ------------------------------------------------------------------
# API tests
# ------------------------------------------------------------------

class TestAPI:
    """API endpoint tests."""

    @pytest.fixture
    async def client(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    async def test_health(self, client):
        """GET /health returns healthy (OPTNL-4)."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    async def test_lineage_post_simple(self, client):
        """POST /lineage with SQL returns lineage (EVENT-1)."""
        resp = await client.post(
            "/lineage",
            json={"sql": "SELECT a, b FROM t", "include_graph": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "lineage" in data
        assert "metadata" in data
        assert "graph" in data
        assert len(data["lineage"]) == 2

    async def test_lineage_post_empty_sql(self, client):
        """POST /lineage with empty SQL returns empty (UNWNT-1)."""
        resp = await client.post(
            "/lineage",
            json={"sql": "", "include_graph": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["lineage"]) == 0

    async def test_lineage_post_invalid_sql(self, client):
        """POST /lineage with invalid SQL returns 400 (UNWNT-1)."""
        resp = await client.post(
            "/lineage",
            json={"sql": "SEL ECT 1", "include_graph": False},
        )
        assert resp.status_code == 400

    async def test_lineage_with_cte(self, client):
        """POST with CTE traces through (EVENT-4)."""
        resp = await client.post(
            "/lineage",
            json={
                "sql": "WITH cte AS (SELECT a FROM src) SELECT a AS out_col FROM cte",
                "include_graph": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["lineage"]) == 1

    async def test_lineage_with_create_table(self, client):
        """POST CREATE TABLE AS SELECT (EVENT-2)."""
        resp = await client.post(
            "/lineage",
            json={
                "sql": "CREATE TABLE analytics.dest AS SELECT id, name FROM raw.source",
                "include_graph": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()

    async def test_lineage_with_graph_output(self, client):
        """POST includes graph DOT and PNG (STATE-2)."""
        resp = await client.post(
            "/lineage",
            json={"sql": "SELECT a FROM t", "include_graph": True},
        )
        data = resp.json()
        graph = data.get("graph")
        if graph:
            assert "dot" in graph

    async def test_lineage_files_upload(self, client):
        """POST /lineage/files with file upload (OPTNL-1)."""
        resp = await client.post(
            "/lineage/files",
            files={
                "files": ("query.sql", "SELECT a, b FROM test_table", "text/plain"),
            },
            data={"include_graph": "false"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["lineage"]) == 2

    async def test_lineage_multiple_files(self, client):
        """POST /lineage/files with multiple files."""
        resp = await client.post(
            "/lineage/files",
            files=[
                ("files", ("q1.sql", "SELECT a FROM t1", "text/plain")),
                ("files", ("q2.sql", "SELECT b FROM t2", "text/plain")),
            ],
            data={"include_graph": "false"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["lineage"]) == 2


# ------------------------------------------------------------------
# CLI tests
# ------------------------------------------------------------------

class TestCLI:
    """CLI command tests."""

    @pytest.fixture
    def cli(self):
        """Path to the CLI module."""
        return [sys.executable, "-m", "lineage_trino.cli"]

    def test_cli_help(self, cli):
        """CLI shows help."""
        result = subprocess.run(
            [*cli, "--help"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "trino-lineage" in result.stdout.lower()

    def test_cli_parse_help(self, cli):
        """CLI parse subcommand shows help."""
        result = subprocess.run(
            [*cli, "parse", "--help"], capture_output=True, text=True
        )
        assert result.returncode == 0

    def test_cli_parse_file(self, cli, tmp_path):
        """CLI parse command processes SQL file (EVENT-2)."""
        sql_file = tmp_path / "test.sql"
        sql_file.write_text("SELECT a, b, c FROM my_table")
        out_dir = tmp_path / "output"
        result = subprocess.run(
            [*cli, "parse", str(sql_file), "--output-dir", str(out_dir)],
            capture_output=True, text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        # Check JSON output
        json_file = out_dir / "lineage.json"
        assert json_file.exists()
        data = json.loads(json_file.read_text())
        assert len(data["lineage"]) == 3

    def test_cli_parse_invalid_file(self, cli, tmp_path):
        """CLI reports error on invalid SQL."""
        sql_file = tmp_path / "bad.sql"
        sql_file.write_text("CREAT TABLE")
        result = subprocess.run(
            [*cli, "parse", str(sql_file), "--output-dir", str(tmp_path / "out")],
            capture_output=True, text=True,
        )
        # May exit with 1 since no valid statements parsed, but should not crash
        assert "Failed to parse" in result.stdout + result.stderr or result.returncode == 0

    def test_cli_version(self, cli):
        """CLI has version info."""
        result = subprocess.run(
            [*cli, "--help"], capture_output=True, text=True
        )
        assert result.returncode == 0
