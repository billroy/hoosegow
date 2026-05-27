"""Regression checks for worker trust-mode controls in the config modal."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_worker_config_modal_exposes_trust_mode_for_ai_workers():
    text = _read("static/components/WorkerConfigModal.js")
    assert "Trust Mode" in text
    assert "value=\"untrusted\">Untrusted (safer defaults)" in text
    assert "value=\"trusted\">Trusted" in text
    assert "onTrustModeChange" in text


def test_worker_config_modal_disables_auto_actions_when_untrusted():
    text = _read("static/components/WorkerConfigModal.js")
    assert ":disabled=\"isUntrustedAI\"" in text
    assert ":disabled=\"isUntrustedAI || !form.use_worktree || !form.auto_commit\"" in text
    assert "fields.auto_commit = false;" in text
    assert "fields.auto_pr = false;" in text
