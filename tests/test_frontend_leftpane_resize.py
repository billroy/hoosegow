"""Regression checks for resizing the left pane."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_leftpane_has_drag_resize_handle_and_persisted_width():
    text = _read("static/components/LeftPane.js")

    assert 'class="left-pane-resize"' in text
    assert '@pointerdown="onResizeDown"' in text
    assert '@dblclick="resetWidth"' in text
    assert 'localStorage.setItem(\'bullpen.leftPaneWidth\'' in text
    assert 'localStorage.getItem(\'bullpen.leftPaneWidth\')' in text


def test_leftpane_resize_clamps_to_reasonable_range():
    text = _read("static/components/LeftPane.js")

    assert "return Math.max(200, Math.min(520, w));" in text


def test_leftpane_resize_handle_styles_exist():
    text = _read("static/style.css")

    assert ".left-pane-resize" in text
    assert ".left-pane-scroll" in text
    assert "overflow: visible;" in text
    assert "overflow-y: auto;" in text
    assert "right: -3px;" in text
    assert "cursor: col-resize;" in text
    assert "touch-action: none;" in text
