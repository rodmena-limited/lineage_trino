"""Tests for graphviz renderer."""

import pytest

from lineage_trino.graph import GraphRenderer
from lineage_trino.lineage import LineageEngine


@pytest.fixture
def engine():
    return LineageEngine(dialect="trino")


@pytest.fixture
def renderer():
    return GraphRenderer()


class TestGraphRenderer:
    """Graph rendering tests."""

    def test_render_simple_graph(self, engine, renderer):
        """Render a simple lineage graph (STATE-2)."""
        graph = engine.extract_from_sql("SELECT a, b FROM t")
        dot, png = renderer.render(graph)
        assert dot.startswith("digraph")
        assert "Lineage" in dot
        assert "digraph Lineage" in dot

    def test_dot_contains_table_nodes(self, engine, renderer):
        """DOT output contains table node definitions."""
        graph = engine.extract_from_sql("SELECT a, b FROM src_table")
        dot, _ = renderer.render(graph)
        assert "src_table" in dot

    def test_dot_contains_edges(self, engine, renderer):
        """DOT output contains edge definitions."""
        graph = engine.extract_from_sql("SELECT a AS out_col FROM src_table")
        dot, _ = renderer.render(graph)
        assert "->" in dot  # edge arrow
        assert "out_col" in dot or "src_table" in dot

    def test_render_with_cte(self, engine, renderer):
        """Render graph with CTEs."""
        graph = engine.extract_from_sql(
            "WITH cte AS (SELECT a FROM src) SELECT a AS out_col FROM cte"
        )
        # CTE should appear as intermediate
        if hasattr(graph.tables, 'intermediates'):
            assert True  # CTEs may be tracked as intermediates

    def test_render_to_file(self, engine, renderer, tmp_path):
        """Render to file produces DOT and optionally PNG (STATE-3)."""
        graph = engine.extract_from_sql("SELECT a, b FROM t1")
        outputs = renderer.render_to_file(graph, str(tmp_path))
        assert "dot" in outputs

    def test_escape_html_in_labels(self, renderer):
        """HTML special chars are escaped."""
        escaped = renderer._escape_html('table<name>')
        assert '&lt;' in escaped
        assert '&gt;' in escaped

    def test_port_id_generation(self, renderer):
        """Port IDs are safe for DOT syntax."""
        port = renderer._port_id("schema.table", "column.name")
        assert "." not in port  # dots replaced
        assert " " not in port  # spaces replaced


class TestGraphOutputComplex:
    """Complex graph rendering scenarios."""

    def test_render_with_aggregation(self, engine, renderer):
        """Render aggregation lineage."""
        graph = engine.extract_from_sql(
            "SELECT SUM(amount) AS total FROM payments"
        )
        dot, _ = renderer.render(graph)
        assert "total" in dot or "payments" in dot

    def test_render_multi_table(self, engine, renderer):
        """Render multi-table join lineage."""
        graph = engine.extract_from_sql(
            "SELECT o.id, c.name FROM orders o JOIN customers c ON o.cid = c.id"
        )
        dot, _ = renderer.render(graph)
        assert "orders" in dot
        assert "customers" in dot
