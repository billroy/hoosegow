"""Regression checks for UDLR pass direction tooltip indicators on worker cards."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_worker_card_pass_indicators_have_directional_tooltips():
    text = _read("static/components/WorkerCard.js")
    assert 'title="This worker passes tickets up"' in text
    assert 'title="This worker passes tickets down"' in text
    assert 'title="This worker passes tickets left"' in text
    assert 'title="This worker passes tickets right"' in text


def test_pass_indicator_is_hoverable_for_native_tooltip():
    text = _read("static/style.css")
    assert "pointer-events: auto;" in text


def test_pass_indicator_does_not_use_help_cursor():
    text = _read("static/style.css")
    assert "cursor: help;" not in text
