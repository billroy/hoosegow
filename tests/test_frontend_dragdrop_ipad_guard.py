"""Regression checks for iPad-safe drag/drop behavior."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TASK_DND_MIME = "application/x-bullpen-task-id"


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_task_drag_uses_custom_mime_and_plaintext_fallback():
    utils = _read("static/utils.js")
    app = _read("static/app.js")
    task_card = _read("static/components/TaskCard.js")
    left_pane = _read("static/components/LeftPane.js")
    assert f"window.BULLPEN_TASK_DND_MIME = '{TASK_DND_MIME}';" in utils
    assert "window.BULLPEN_TASK_DRAG_ACTIVE = false;" in utils
    assert "window.BULLPEN_TASK_DRAG_ACTIVE = true;" in app
    assert "window.BULLPEN_TASK_DRAG_ACTIVE = false;" in app
    assert "setData(window.BULLPEN_TASK_DND_MIME, this.task.id)" in task_card
    assert "window.dispatchEvent(new Event('bullpen:task-drag:start'))" in task_card
    assert "window.dispatchEvent(new Event('bullpen:task-drag:end'))" in task_card
    assert "setData(window.BULLPEN_TASK_DND_MIME, taskId)" in left_pane
    assert "window.dispatchEvent(new Event('bullpen:task-drag:start'))" in left_pane
    assert "window.dispatchEvent(new Event('bullpen:task-drag:end'))" in left_pane


def test_drop_targets_prevent_default_and_read_custom_mime():
    kanban = _read("static/components/KanbanTab.js")
    worker = _read("static/components/WorkerCard.js")
    left_pane = _read("static/components/LeftPane.js")

    assert "@drop.prevent=\"onDrop($event, col.key)\"" in kanban
    assert "e.preventDefault();" in kanban
    assert "window.BULLPEN_TASK_DRAG_ACTIVE && types.includes('text/plain')" in kanban
    assert "getData(window.BULLPEN_TASK_DND_MIME)\n        || (window.BULLPEN_TASK_DRAG_ACTIVE ? e.dataTransfer.getData('text/plain') : '')" in kanban

    assert "@drop.prevent=\"onDrop\"" in worker
    assert "window.BULLPEN_TASK_DRAG_ACTIVE && types.includes('text/plain')" in worker
    assert "getData(window.BULLPEN_TASK_DND_MIME)\n        || (window.BULLPEN_TASK_DRAG_ACTIVE ? e.dataTransfer.getData('text/plain') : '')" in worker

    assert "e.preventDefault();" in left_pane
    assert "window.BULLPEN_TASK_DRAG_ACTIVE && types.includes('text/plain')" in left_pane
    assert "getData(window.BULLPEN_TASK_DND_MIME)\n        || (window.BULLPEN_TASK_DRAG_ACTIVE ? e.dataTransfer.getData('text/plain') : '')" in left_pane


def test_task_dnd_mime_is_not_redeclared_in_global_component_scripts():
    for rel_path in [
        "static/components/LeftPane.js",
        "static/components/TaskCard.js",
        "static/components/WorkerCard.js",
        "static/components/KanbanTab.js",
    ]:
        assert "const TASK_DND_MIME" not in _read(rel_path)


def test_draggable_ticket_styles_disable_text_selection():
    css = _read("static/style.css")
    assert ".task-card {" in css
    assert ".task-card[draggable=\"true\"]" in css
    assert ".inbox-item[draggable=\"true\"]" in css
    assert "-webkit-touch-callout: none;" in css
    assert "touch-action: pan-y;" in css


def test_task_card_dnd_not_disabled_for_touch_devices():
    task_card = _read("static/components/TaskCard.js")
    assert "draggable=\"true\"" in task_card
    assert "window.matchMedia('(pointer: coarse)')" not in task_card
    assert "navigator.maxTouchPoints" not in task_card
