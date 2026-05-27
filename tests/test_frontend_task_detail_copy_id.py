"""Regression checks for copying ticket IDs from the detail panel."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_task_detail_has_copy_id_button_and_clipboard_handler():
    text = _read("static/components/TaskDetailPanel.js")
    assert "Copy ID" in text
    assert "copyId" in text
    assert "navigator.clipboard.writeText(id)" in text
    assert "copyIdFallback" in text
    assert "Ticket ID copied" in text


def test_task_detail_copy_id_toast_is_wired_to_app():
    detail = _read("static/components/TaskDetailPanel.js")
    app = _read("static/app.js")

    assert "'toast'" in detail
    assert "@toast=\"addToast\"" in app
