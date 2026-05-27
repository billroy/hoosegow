"""Regression checks for task-card priority/type popup editing."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_task_card_exposes_popup_controls_for_priority_and_type():
    text = _read("static/components/TaskCard.js")
    assert "emits: ['select-task', 'update-task']" in text
    assert "activePopup" in text
    assert "togglePopup('priority')" in text
    assert "togglePopup('type')" in text
    assert "applyPopupChoice('priority', opt.value)" in text
    assert "applyPopupChoice('type', opt.value)" in text
    assert "$emit('update-task', { id: this.task.id, [field]: value })" in text


def test_kanban_forwards_task_card_update_event():
    text = _read("static/components/KanbanTab.js")
    assert "emits: ['select-task', 'move-task', 'archive-done', 'new-task', 'update-list-scope', 'update-task', 'update-shown-count']" in text
    assert "@update-task=\"$emit('update-task', $event)\"" in text


def test_app_wires_kanban_update_task_event_to_socket_update():
    text = _read("static/app.js")
    assert "@update-task=\"updateTask\"" in text
    assert "function emitSocketAction(eventName, data" in text
    assert "function updateTask(data) {" in text
    assert "emitSocketAction('task:update', data" in text
    assert "Ticket changes were not saved." in text


def test_app_guards_task_creates_and_disconnects_show_toasts():
    text = _read("static/app.js")
    assert "Disconnected from Bullpen server. Changes are paused until connection is restored." in text
    assert "Reconnected to Bullpen server" in text
    assert "socket.emit('task:create', _wsData({" in text
    assert "Ticket was not created." in text
    assert "return id;" in text


def test_task_card_popup_styles_exist():
    text = _read("static/style.css")
    assert ".task-card-bean-wrap {" in text
    assert ".task-card-bean-btn {" in text
    assert ".task-card-popup {" in text
    assert ".task-card-popup-item {" in text
    assert ".task-card-popup-item.active {" in text
