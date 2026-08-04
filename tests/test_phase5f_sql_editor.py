"""
tests/test_phase5f_sql_editor.py — lightweight regression test for the
SQL editor screen structure and its navigation entry.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_console_contains_sql_editor_screen_and_nav():
    console_path = Path("ui/console.jsx")
    text = console_path.read_text(encoding="utf-8")
    assert "SQL Editor" in text
    assert "function SqlEditorScreen" in text
    assert "screen === \"sql\"" in text
    assert "{ id: \"sql\", label: \"SQL Editor\"" in text


def test_sql_editor_has_generate_button_and_select():
    console_path = Path("ui/console.jsx")
    text = console_path.read_text(encoding="utf-8")
    assert "Generate preview" in text
    assert "<select value={operation}" in text
    assert "<textarea value={script}" in text or "MonacoComponent" in text
    assert "setSelectedTable(e.target.value)" in text
    assert "onClick={generate}" in text
    assert "placeholder=\"Generate a preview to populate this editor…\"" in text
    assert "importPath = \"@monaco-editor/react\"" in text or "importPath = [\"@monaco-editor\", \"react\"].join(\"/\")" in text
    assert "MonacoComponent" in text
    assert "importPath = [\"@monaco-editor\", \"react\"].join(\"/\")" in text
