"""Regression checks for shell worker example entry points."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_worker_config_modal_no_longer_shows_start_from_example_controls():
    text = _read("static/components/WorkerConfigModal.js")
    assert "Start from example" not in text
    assert "selectedExampleId" not in text
    assert "applyExample()" not in text
    assert "loadShellExamples()" not in text
    assert "/shell_worker_examples.json" not in text


def test_add_worker_library_still_loads_shell_examples():
    text = _read("static/components/BullpenTab.js")
    assert "platformShellExamples()" in text
    assert "this.loadShellExamples();" in text
    assert "addShellWorker(ex)" in text
    assert "/shell_worker_examples.json" in text
