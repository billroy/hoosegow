"""Regression checks for modal Escape behavior in the frontend."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_task_create_modal_supports_escape_close():
    text = _read("static/components/TaskCreateModal.js")
    assert "class=\"modal-overlay\"" in text
    assert "@keydown.escape=\"$emit('close')\"" in text


def test_worker_config_modal_supports_escape_close():
    text = _read("static/components/WorkerConfigModal.js")
    assert "class=\"modal-overlay\"" in text
    assert "@keydown.escape=\"$emit('close')\"" in text


def test_add_worker_library_modal_supports_escape_close_and_focus():
    text = _read("static/components/BullpenTab.js")
    assert "class=\"modal-overlay\"" in text
    assert "@keydown.escape=\"closeLibrary\"" in text
    assert "this.$nextTick(() => this.$refs.libraryOverlay?.focus());" in text
