"""Regression checks for left-pane worker roster queue count labels."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_leftpane_worker_status_label_includes_working_queue_count():
    text = (ROOT / "static" / "components" / "LeftPane.js").read_text(encoding="utf-8")
    assert 'workerStatusLabel(w)' in text
    assert "workerStatusLabel(worker)" in text
    assert "if (state === 'RETRYING')" in text
    assert "return attempt && max ? `${state} (${attempt}/${max})` : state;" in text
    assert "if (state !== 'WORKING') return state;" in text
    assert "const queueCount = Math.max(1, Number(worker?.taskQueueLength || 0));" in text
    assert "return `${state} (${queueCount})`;" in text
