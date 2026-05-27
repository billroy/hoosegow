"""Regression checks for the unconfigured worker profile in Add Worker modal."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_add_worker_list_pins_unconfigured_profile_first():
    text = (ROOT / "static" / "components" / "BullpenTab.js").read_text(encoding="utf-8")
    assert "UNCONFIGURED_PROFILE_ID: 'unconfigured-worker'" in text
    assert "if (a?.id === pin && b?.id !== pin) return -1;" in text
    assert "if (b?.id === pin && a?.id !== pin) return 1;" in text

