"""Regression checks for Kanban column header icons."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_column_icon_helper_marks_worker_and_user_columns():
    text = _read("static/utils.js")
    assert "function getColumnIcon" in text
    assert "new Set(['assigned', 'in_progress'])" in text
    assert "? 'bot' : 'user'" in text
    assert "col?.icon" in text


def test_kanban_headers_render_lucide_column_icons():
    text = _read("static/components/KanbanTab.js")
    assert "class=\"column-title\"" in text
    assert "class=\"column-icon\"" in text
    assert ":data-lucide=\"columnIcon(col)\"" in text
    assert "renderLucideIcons(this.$el);" in text
    assert "return getColumnIcon(col);" in text


def test_kanban_column_icon_styles_exist():
    text = _read("static/style.css")
    assert ".column-title" in text
    assert ".column-icon" in text
    assert "color: currentColor;" in text
    assert "width: 16px;" in text
    assert "height: 16px;" in text
    assert "text-overflow: ellipsis;" in text
