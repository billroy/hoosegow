"""Regression checks for ticket list status pill rendering with custom columns."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_ticket_list_status_pill_has_custom_column_fallback_styling():
    text = _read("static/components/KanbanTab.js")
    assert ":class=\"statusPillClass(task.status)\"" in text
    assert ":style=\"statusPillStyle(task.status)\"" in text
    assert "statusPillClass(key)" in text
    assert "statusPillStyle(key)" in text
    assert "parseHexColor(color)" in text


def test_ticket_list_status_pill_has_default_visual_style():
    text = _read("static/style.css")
    assert ".ticket-list-status-pill {" in text
    assert "background: var(--bg-hover);" in text
    assert "color: var(--text-secondary);" in text
