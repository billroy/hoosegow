"""Regression checks for ticket list Created column timestamp formatting."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_ticket_list_created_column_formats_date_and_time():
    text = _read("static/components/KanbanTab.js")
    assert "toLocaleString(undefined" in text
    assert "hour: 'numeric'" in text
    assert "minute: '2-digit'" in text

