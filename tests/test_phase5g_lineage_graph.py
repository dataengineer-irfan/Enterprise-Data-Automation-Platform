"""
tests/test_phase5g_lineage_graph.py — verifies the schema explorer now
includes an SVG-based lineage graph component.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_console_contains_lineage_screen():
    console_path = Path("ui/console.jsx")
    text = console_path.read_text(encoding="utf-8")
    assert "function LineageGraphScreen" in text
    assert "const NAV_ITEMS" in text and "Lineage" in text
    assert "<svg" in text
    assert "lineage" in text.lower()


def test_lineage_graph_is_clickable():
    console_path = Path("ui/console.jsx")
    text = console_path.read_text(encoding="utf-8")
    assert "onClick={() => setSelectedTable(node.name)}" in text
    assert "onMouseEnter={() => setHoveredNode(node.name)}" in text
    assert "aria-label={`Lineage graph for ${tableName}`}" in text or "role=\"img\"" in text
    assert "Hover or click nodes to focus a different table" in text
    assert "ReactFlow" in text
    assert "importPath = \"reactflow\"" in text


def test_lineage_graph_hover_feedback_exists():
    console_path = Path("ui/console.jsx")
    text = console_path.read_text(encoding="utf-8")
    assert "hoveredNode" in text
    assert "Hover a node to preview its lineage." in text
    assert "Selected: {tableName}" in text or "Selected: ${tableName}" in text
    assert "onPointerDown={(e) => handlePointerDown(e, node.name)}" in text
    assert "onWheel={handleWheel}" in text
    assert "onPointerMove={handlePointerMove}" in text
    assert "onPointerUp={handlePointerUp}" in text
    assert "onMouseLeave={() => setHoveredNode(null)}" in text
    assert "onMouseEnter={() => setHoveredNode(node.name)}" in text
