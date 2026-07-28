"""
Graphviz Renderer for Column-Level Lineage.

Transforms a LineageGraph into a professional Graphviz DOT diagram
and renders it to PNG. Box-style nodes with rounded corners,
color-coded table headers, and clean arrow routing
(file ownership: Module D).
"""

from __future__ import annotations

import base64
import logging
import shutil

from lineage_trino.config import settings
from lineage_trino.lineage import LineageGraph
from lineage_trino.models import (
    LineageEdge,
    RelationType,
    TableInfo,
)

logger = logging.getLogger(__name__)

# Color palette
TABLE_SOURCE_FILL = "#e3f2fd"  # Light blue
TABLE_SOURCE_STROKE = "#1565c0"
TABLE_SOURCE_HEADER = "#1565c0"
TABLE_TARGET_FILL = "#e8f5e9"  # Light green
TABLE_TARGET_STROKE = "#2e7d32"
TABLE_TARGET_HEADER = "#2e7d32"
TABLE_INTERMEDIATE_FILL = "#fff3e0"  # Light orange
TABLE_INTERMEDIATE_STROKE = "#e65100"
TABLE_INTERMEDIATE_HEADER = "#e65100"

COLUMN_FILL = "#ffffff"
COLUMN_STROKE = "#dde1e6"
TEXT_COLOR = "#222222"
MUTED_TEXT = "#666666"
BG_COLOR = "#f8f9fa"

# Edge styling by relation type
EDGE_STYLES = {
    RelationType.DIRECT: {
        "color": "#1a73e8",
        "style": "solid",
        "penwidth": "1.8",
    },
    RelationType.THROUGH_AGGREGATE: {
        "color": "#e8710a",
        "style": "dashed",
        "penwidth": "1.5",
    },
    RelationType.THROUGH_EXPRESSION: {
        "color": "#7c4dff",
        "style": "dotted",
        "penwidth": "1.5",
    },
    RelationType.INDIRECT: {
        "color": "#9e9e9e",
        "style": "dashed",
        "penwidth": "1.2",
    },
}


class GraphRenderer:
    """
    Render lineage graphs as DOT and PNG.

    Usage:
        renderer = GraphRenderer()
        dot_string, png_bytes = renderer.render(lineage_graph)
        outputs = renderer.render_to_file(lineage_graph, "output_dir")
    """

    def __init__(self, dpi: int = 150, engine: str = "dot"):
        self.dpi = dpi
        self.engine = engine
        self._graphviz_available = shutil.which("dot") is not None
        if not self._graphviz_available:
            logger.warning(
                "Graphviz 'dot' binary not found. PNG rendering will be skipped. "
                "Install graphviz: brew install graphviz (macOS) or "
                "apt-get install graphviz (Linux)"
            )

    def render(self, graph: LineageGraph) -> tuple[str, bytes | None]:
        """
        Render a LineageGraph to DOT and PNG.

        Returns:
            Tuple of (DOT string, PNG bytes or None if rendering failed).
        """
        dot = self._build_dot(graph)
        png = self._render_png(dot) if self._graphviz_available else None
        return dot, png

    def render_to_file(
        self, graph: LineageGraph, output_dir: str = "lineage_output"
    ) -> dict[str, str]:
        """
        Render lineage graph and write files to disk.

        Returns:
            Dict with paths: {"dot": "path/to/file.dot", "png": "path/to/file.png"}
        """
        from pathlib import Path

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        dot, png = self.render(graph)

        dot_file = output_path / "lineage.dot"
        dot_file.write_text(dot)

        result: dict[str, str] = {"dot": str(dot_file)}

        if png:
            png_file = output_path / "lineage.png"
            png_file.write_bytes(png)
            result["png"] = str(png_file)

        return result

    # ------------------------------------------------------------------
    # DOT builder
    # ------------------------------------------------------------------

    def _build_dot(self, graph: LineageGraph) -> str:
        """Build DOT string for the lineage graph."""
        lines: list[str] = []
        lines.append("digraph Lineage {")
        lines.append("  rankdir=LR;")
        lines.append(f'  bgcolor="{BG_COLOR}";')
        lines.append(f'  fontname="{settings.graph_font}";')
        lines.append("  labeljust=l;")
        lines.append("  pad=0.5;")
        lines.append("  nodesep=0.8;")
        lines.append("  ranksep=2.0;")
        lines.append("  splines=polyline;")
        lines.append("  newrank=true;")
        lines.append("")

        # Node defaults
        lines.append("  node [")
        lines.append("    shape=plain, ")
        lines.append(f'    fontname="{settings.graph_font}", ')
        lines.append("    fontsize=11, ")
        lines.append("    margin=0, ")
        lines.append("  ];")
        lines.append("")

        # Edge defaults
        lines.append("  edge [")
        lines.append(f'    fontname="{settings.graph_font}", ')
        lines.append("    fontsize=9, ")
        lines.append("    arrowsize=0.8, ")
        lines.append("  ];")
        lines.append("")

        # Build table nodes
        all_tables = self._collect_table_columns(graph)
        table_ids: dict[str, str] = {}

        for idx, table_name in enumerate(sorted(all_tables.keys())):
            table_id = f"tbl_{idx}"
            table_ids[table_name] = table_id
            columns = all_tables[table_name]
            table_type = self._classify_table(table_name, graph.tables)
            node_dot = self._table_node(table_id, table_name, columns, table_type)
            lines.append(node_dot)
            lines.append("")

        # Build edges
        for edge in graph.edges:
            src_edges = self._edge_statements(edge, table_ids, graph.tables)
            lines.extend(src_edges)

        lines.append("}")
        return "\n".join(lines)

    def _collect_table_columns(self, graph: LineageGraph) -> dict[str, list[str]]:
        """Collect all tables and their columns from the graph."""
        tables: dict[str, set[str]] = {}

        for edge in graph.edges:
            # Add target column
            t_table = edge.target.table
            if t_table:
                tables.setdefault(t_table, set()).add(edge.target.column)

            # Add source columns
            for src in edge.sources:
                if src.table and not src.table.startswith("__"):
                    tables.setdefault(src.table, set()).add(src.column)

        return {k: sorted(v) for k, v in tables.items()}

    def _classify_table(self, table_name: str, table_info: TableInfo) -> str:
        """Classify a table as source, target, or intermediate."""
        if table_name in table_info.targets:
            return "target"
        if table_name in table_info.intermediates:
            return "intermediate"
        if table_name in table_info.sources:
            return "source"
        return "source"  # default

    def _table_node(
        self,
        node_id: str,
        table_name: str,
        columns: list[str],
        table_type: str,
    ) -> str:
        """Generate DOT for a table node as an HTML-like label with rows."""
        if table_type == "target":
            header_bg = TABLE_TARGET_HEADER
            header_fg = "#ffffff"
            body_bg = TABLE_TARGET_FILL
        elif table_type == "intermediate":
            header_bg = TABLE_INTERMEDIATE_HEADER
            header_fg = "#ffffff"
            body_bg = TABLE_INTERMEDIATE_FILL
        else:
            header_bg = TABLE_SOURCE_HEADER
            header_fg = "#ffffff"
            body_bg = TABLE_SOURCE_FILL

        lines: list[str] = []
        lines.append(f"  {node_id} [")
        lines.append(
            f'    label=<<table border="0" cellborder="0" cellpadding="6" cellspacing="0" bgcolor="{body_bg}" style="rounded">'
        )

        # Header row
        lines.append("      <tr>")
        lines.append(
            f'        <td bgcolor="{header_bg}" colspan="1" fixedsize="false">'
        )
        lines.append(
            f'          <font color="{header_fg}" point-size="13"><b>{self._escape_html(table_name)}</b></font>'
        )
        lines.append("        </td>")
        lines.append("      </tr>")

        # Column rows
        for col in columns:
            lines.append("      <tr>")
            lines.append(
                f'        <td port="{self._port_id(table_name, col)}" href="" bgcolor="{COLUMN_FILL}" border="1" sides="LR" cellpadding="4">'
            )
            lines.append(
                '          <table border="0" cellborder="0" cellpadding="2" cellspacing="0">'
            )
            lines.append("            <tr>")
            lines.append(
                f'              <td align="left"><font color="{TEXT_COLOR}" point-size="11">{self._escape_html(col)}</font></td>'
            )
            lines.append("            </tr>")
            lines.append("          </table>")
            lines.append("        </td>")
            lines.append("      </tr>")

        lines.append("    </table>>")
        lines.append(f'    tooltip="{self._escape_html(table_name)}"')
        lines.append("  ];")
        return "\n".join(lines)

    def _column_type_indicator(self, col: str) -> str:
        """Return a type indicator string for a column."""
        if col == "*":
            return "all"
        return "col"

    def _port_id(self, table_name: str, column: str) -> str:
        """Generate a port identifier for edge connections."""
        safe_table = table_name.replace(".", "_").replace(" ", "_")
        safe_col = column.replace(".", "_").replace(" ", "_").replace("*", "star")
        return f"{safe_table}_{safe_col}"

    # ------------------------------------------------------------------
    # Edge builders
    # ------------------------------------------------------------------

    def _edge_statements(
        self,
        edge: LineageEdge,
        table_ids: dict[str, str],
        table_info: TableInfo,
    ) -> list[str]:
        """Generate DOT edge statements for a lineage edge."""
        statements: list[str] = []
        target_table = edge.target.table
        target_col = edge.target.column

        if target_table in table_ids:
            target_id = table_ids[target_table]
            target_port = self._port_id(target_table, target_col)
        else:
            return []

        for src in edge.sources:
            if src.table.startswith("__"):
                continue
            if src.table in table_ids:
                src_id = table_ids[src.table]
                src_port = self._port_id(src.table, src.column)
                style = EDGE_STYLES.get(src.relation, EDGE_STYLES[RelationType.DIRECT])

                label = ""
                tooltip = f"{src.table}.{src.column} → {target_table}.{target_col}"

                stmt = (
                    f"  {src_id}:{src_port}:e -> {target_id}:{target_port}:w"
                    f' [color="{style["color"]}", style="{style["style"]}",'
                    f" penwidth={style['penwidth']},"
                    f' tooltip="{self._escape_html(tooltip)}"'
                    f" {label}]"
                )
                statements.append(stmt)

        return statements

    # ------------------------------------------------------------------
    # PNG rendering
    # ------------------------------------------------------------------

    def _render_png(self, dot: str) -> bytes | None:
        """Render DOT to PNG bytes using Graphviz."""
        try:
            import graphviz as gv  # type: ignore[import-untyped]

            src = gv.Source(dot)
            png_bytes = src.pipe(format="png")
            logger.debug("Rendered PNG (%d bytes)", len(png_bytes))
            return png_bytes
        except (OSError, RuntimeError, ValueError) as e:
            logger.warning("Failed to render PNG: %s", e)
            return None

    def _escape_html(self, text: str) -> str:
        """Escape text for HTML-like labels in DOT."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    @staticmethod
    def png_to_base64(png_bytes: bytes) -> str:
        """Convert PNG bytes to base64 string for JSON output."""
        return base64.b64encode(png_bytes).decode("utf-8")
