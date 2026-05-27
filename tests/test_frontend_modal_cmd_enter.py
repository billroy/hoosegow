"""Regression checks for Cmd+Enter primary action behavior in modals."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_task_create_modal_supports_cmd_enter_submit():
    text = _read("static/components/TaskCreateModal.js")
    assert "@keydown.meta.enter=\"onPrimaryShortcut\"" in text
    assert "onPrimaryShortcut(e)" in text
    assert "this.submit();" in text


def test_worker_config_modal_supports_cmd_enter_save():
    text = _read("static/components/WorkerConfigModal.js")
    assert "@keydown.meta.enter=\"onPrimaryShortcut\"" in text
    assert "onPrimaryShortcut(e)" in text
    assert "this.onSave();" in text


def test_column_manager_modal_supports_cmd_enter_save():
    text = _read("static/components/ColumnManagerModal.js")
    assert "@keydown.meta.enter=\"onPrimaryShortcut\"" in text
    assert "onPrimaryShortcut(e)" in text
    assert "this.save();" in text


def test_task_detail_title_edit_supports_cmd_enter_save():
    text = _read("static/components/TaskDetailPanel.js")
    assert "@keydown.meta.enter=\"saveTitle\"" in text
    assert "@keydown.ctrl.enter=\"saveTitle\"" in text
