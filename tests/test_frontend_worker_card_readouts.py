"""Regression checks for worker card task timer and token readouts."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_worker_card_shows_elapsed_and_tokens_for_current_task():
    text = _read("static/components/WorkerCard.js")
    assert 'statusLabel()' in text
    assert 'return `BUSY ${this.elapsed}`' in text
    assert 'return `RETRY${this.retryAttemptLabel}${this.retryCountdownLabel}`' in text
    assert 'worker-card-agent' not in text
    assert '{{ worker.model }}' not in text
    assert '{{ worker.agent }}/{{ worker.model }}' not in text
    assert 'this.outputLines.slice(-5)' in text
    assert 'updateElapsed()' in text


def test_worker_card_explains_held_manual_queues():
    text = _read("static/components/WorkerCard.js")
    assert "isHeldQueue()" in text
    assert "WAITING FOR RUN" in text
    assert "Run next (${this.taskQueueCount})" in text


def test_worker_card_readouts_have_styles():
    text = _read("static/style.css")
    assert '.worker-card-output {' in text
    assert 'flex: 1 1 0;' in text
    assert 'max-height: none;' in text
