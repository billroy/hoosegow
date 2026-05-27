"""Regression checks for worker deletion confirmation behavior."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_root_remove_worker_always_confirms_before_delete():
    text = _read("static/app.js")
    assert "function workerDeleteMessage(workers) {" in text
    assert "function removeWorker(slot) {" in text
    assert "const confirmMessage = workerDeleteMessage([{ slot, worker }]);" in text
    assert 'Delete worker "${name}"?' in text
    assert 'This worker has ${queued} queued task(s).' in text
    assert "if (!confirm(confirmMessage)) return;" in text
    assert "socket.emit('worker:remove', _wsData({ slot }));" in text


def test_root_remove_workers_confirms_once_for_multiple_delete():
    text = _read("static/app.js")
    assert "function removeWorkers(slots) {" in text
    assert "Delete ${workers.length} workers?" in text
    assert "These workers have ${queued} queued task(s)." in text
    assert "if (!confirm(workerDeleteMessage(workers))) return;" in text
    assert "for (const { slot } of workers) {" in text
    assert "removeWorkers," in text


def test_worker_config_modal_uses_root_confirmation():
    text = _read("static/components/WorkerConfigModal.js")
    assert "onRemove()" in text
    assert "this.$emit('remove', this.slotIndex);" in text
    assert "if (confirm('Remove this worker from the grid?'))" not in text
