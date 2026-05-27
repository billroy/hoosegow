"""Regression checks for project list icon rendering in the left pane."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_leftpane_projects_render_folder_icon_before_name():
    text = _read("static/components/LeftPane.js")
    assert "class=\"project-name\"" in text
    assert "class=\"project-label-icon\"" in text
    assert "data-lucide" in text
    assert "class=\"project-label-text\">{{ p.name }}</span>" in text


def test_leftpane_project_icon_styles_exist():
    text = _read("static/style.css")
    assert ".project-label-icon" in text
    assert "width: 14px;" in text
    assert "height: 14px;" in text
