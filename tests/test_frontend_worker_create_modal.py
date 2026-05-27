"""Regression checks for worker creation opening the config modal."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_worker_library_creation_paths_share_add_and_configure_helper():
    text = _read("static/components/BullpenTab.js")

    assert "createWorkerAndOpenConfig({ type, profile, fields })" in text
    assert "this.$emit('configure-worker', slot);" in text
    assert "this.createWorkerAndOpenConfig({ profile: profileId, type: 'ai' });" in text
    assert "this.createWorkerAndOpenConfig({" in text
    assert "type: 'shell'," in text
    assert "type: 'service'," in text
    assert "type: 'marker'," in text
