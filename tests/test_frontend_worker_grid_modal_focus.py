"""Regression checks for worker-grid focus restoration after modal/tab exits."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_worker_grid_modal_close_paths_restore_viewport_focus():
    text = _read("static/app.js")

    assert "function closeCreateModal() {" in text
    assert "function closeColumnManager() {" in text
    assert "function closeWorkerConfig() {" in text
    assert "function closeTransferModal() {" in text
    assert text.count("focusWorkerGridSoon();") >= 6
    assert "@close=\"closeCreateModal\"" in text
    assert "@close=\"closeWorkerConfig\"" in text
    assert "@close=\"closeTransferModal\"" in text
    assert "@close=\"closeColumnManager\"" in text


def test_worker_focus_tab_close_returns_keyboard_focus_to_grid():
    text = _read("static/app.js")

    assert "function closeFocusTab(slotIndex) {" in text
    assert "if (activeTab.value === 'focus-' + slotIndex) {" in text
    assert "activeTab.value = 'workers';" in text
    assert "focusWorkerGridSoon();" in text
