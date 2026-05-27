"""Frontend regression checks for Worker Focus streaming wiring."""

from pathlib import Path
import re


def _app_js_text():
    root = Path(__file__).resolve().parents[1]
    return (root / "static" / "app.js").read_text(encoding="utf-8")


def _style_css_text():
    root = Path(__file__).resolve().parents[1]
    return (root / "static" / "style.css").read_text(encoding="utf-8")


def test_worker_focus_component_registered_in_root_components():
    text = _app_js_text()
    match = re.search(r"components:\s*{(?P<body>.*?)}\s*,\s*setup\(", text, re.S)
    assert match, "Could not locate root component registration block in static/app.js"
    body = match.group("body")
    assert "WorkerFocusView" in body, "WorkerFocusView must be registered in root components"


def test_worker_focus_template_and_stream_handlers_exist():
    text = _app_js_text()
    assert "<WorkerFocusView" in text, "WorkerFocusView should be rendered from root template"
    assert "socket.on('worker:output'" in text, "worker:output handler must exist"
    assert "socket.on('worker:output:done'" in text, "worker:output:done handler must exist"
    assert "function _outputBufferKey(slot, workspaceId = activeWorkspaceId.value)" in text
    assert "function outputLinesForSlot(slot, workspaceId = activeWorkspaceId.value)" in text
    assert "const wsId = data.workspaceId || activeWorkspaceId.value;" in text
    assert ":output-lines=\"outputLinesForSlot(ft.slotIndex, ft.workspaceId)\"" in text


def test_worker_focus_and_card_preview_share_workspace_scoped_catchup_requests():
    text = _app_js_text()
    assert "function requestOutputCatchup(slot, options = {})" in text
    assert "socket.emit('service:tail', { workspaceId, slot });" in text
    assert "socket.emit('worker:output:request', { workspaceId, slot });" in text
    assert "requestOutputCatchup(slotIndex, {" in text
    assert "force: true," in text


def test_worker_focus_terminal_colors_are_theme_driven():
    text = _style_css_text()
    assert "--terminal-bg: #0a0c10;" in text
    assert "--terminal-fg: #c8ccd4;" in text
    assert "[data-theme=\"light\"]" in text
    assert "--terminal-bg: #f8f9fb;" in text
    assert ".focus-terminal" in text
    assert "background: var(--terminal-bg);" in text
    assert ".focus-output" in text
    assert "color: var(--terminal-fg);" in text
