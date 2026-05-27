"""Regression checks for top-toolbar command input spacing."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_top_toolbar_uses_taller_header_for_balanced_command_input_spacing():
    css = _read("static/style.css")
    assert "--toolbar-height: 50px;" in css


def test_toolbar_quick_create_input_zeroes_bottom_margin_in_toolbar_context():
    css = _read("static/style.css")
    assert ".top-toolbar .toolbar-quick-create-input {" in css
    assert "margin-bottom: 0;" in css
