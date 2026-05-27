"""Regression checks for Eyeshade Dark theme wiring."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_eyeshade_dark_theme_is_registered_in_catalog():
    text = _read("static/app.js")
    assert "{ id: 'eyeshade-dark', label: 'Eyeshade Dark', mode: 'dark' }" in text


def test_eyeshade_dark_theme_has_style_tokens_and_ticket_row_background():
    text = _read("static/style.css")
    assert "[data-theme=\"eyeshade-dark\"] {" in text
    assert "--ticket-bg:" in text
    assert "[data-theme=\"eyeshade-dark\"] .priority-low" in text
    assert "[data-theme=\"eyeshade-dark\"] .badge.type-feature" in text
    assert "[data-theme=\"eyeshade-dark\"] .ticket-list-row" in text
