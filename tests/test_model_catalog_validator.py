"""Tests for host-side model catalog validation."""

import json
import os
import sys

import bullpen
from server.agents import get_adapter, register_adapter
from server.agents.base import AgentAdapter
from server.model_catalog_validator import (
    classify_model_error,
    fetch_provider_api_catalog,
    validate_model_catalog,
)


class ProbeAdapter(AgentAdapter):
    def __init__(self, *, mode="ok"):
        self.mode = mode

    @property
    def name(self):
        return "probe"

    def available(self):
        return self.mode != "missing"

    def unavailable_message(self):
        return "probe executable missing"

    def build_argv(self, prompt, model, workspace, bp_dir=None):
        script = (
            "import sys; "
            "model=sys.argv[1]; "
            "print('OK ' + model)"
        )
        if self.mode == "not_found":
            script = "import sys; print('Requested entity was not found.', file=sys.stderr); sys.exit(1)"
        return [sys.executable, "-c", script, model]

    def parse_output(self, stdout, stderr, exit_code):
        if exit_code == 0:
            return {"success": True, "output": stdout.strip(), "error": None}
        return {"success": False, "output": stdout.strip(), "error": stderr.strip()}


def _register_temp_adapter(name, adapter):
    previous = get_adapter(name)
    register_adapter(name, adapter)
    return previous


def _restore_adapter(name, previous):
    if previous is not None:
        register_adapter(name, previous)
    else:
        import server.agents as agents

        agents._adapters.pop(name, None)


def test_validate_model_catalog_smoke_uses_adapter_path(tmp_workspace):
    previous = _register_temp_adapter("probe-ok", ProbeAdapter())
    try:
        report = validate_model_catalog(
            providers=["probe-ok"],
            models=["model-a"],
            workspace=tmp_workspace,
            timeout_seconds=2,
        )
    finally:
        _restore_adapter("probe-ok", previous)

    row = report["providers"][0]["models"][0]
    assert row["model"] == "model-a"
    assert row["adapter_available"] is True
    assert row["accepted"] is True
    assert row["responded"] is True
    assert row["success"] is True
    assert row["output_preview"] == "OK model-a"


def test_validate_model_catalog_classifies_not_found(tmp_workspace):
    previous = _register_temp_adapter("probe-missing-model", ProbeAdapter(mode="not_found"))
    try:
        report = validate_model_catalog(
            providers=["probe-missing-model"],
            models=["bad-model"],
            workspace=tmp_workspace,
            timeout_seconds=2,
        )
    finally:
        _restore_adapter("probe-missing-model", previous)

    row = report["providers"][0]["models"][0]
    assert row["success"] is False
    assert row["accepted"] is True
    assert row["error_class"] == "not_found"


def test_fetch_provider_api_catalog_skips_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    result = fetch_provider_api_catalog("gemini")

    assert result == {
        "status": "skipped",
        "reason": "GEMINI_API_KEY is not set",
        "models": [],
    }


def test_classify_model_error_common_cases():
    assert classify_model_error("gemini", "[API Error: Requested entity was not found.]") == "not_found"
    assert classify_model_error("claude", "rate_limit_error: too many requests") == "quota"
    assert classify_model_error("codex", "Unauthorized: API key missing") == "auth"
    assert (
        classify_model_error(
            "gemini",
            '{"code":403,"message":"Your project has been denied access. Please contact support.","status":"PERMISSION_DENIED"}',
        )
        == "permission_denied"
    )


def test_model_catalog_cli_outputs_json(tmp_workspace, monkeypatch, capsys):
    previous = _register_temp_adapter("probe-cli", ProbeAdapter())
    try:
        args = bullpen.parse_args([
            "model-catalog",
            "--workspace",
            tmp_workspace,
            "validate",
            "--provider",
            "probe-cli",
            "--model",
            "model-a",
            "--timeout",
            "2",
        ])

        assert bullpen.run_model_catalog_cli(args) == 0
    finally:
        _restore_adapter("probe-cli", previous)

    report = json.loads(capsys.readouterr().out)
    row = report["providers"][0]["models"][0]
    assert row["provider"] == "probe-cli"
    assert row["success"] is True


def test_model_catalog_cli_rejects_missing_workspace(tmp_path, capsys):
    missing_workspace = os.fspath(tmp_path / "missing")
    args = bullpen.parse_args([
        "model-catalog",
        "--workspace",
        missing_workspace,
        "validate",
        "--provider",
        "gemini",
    ])

    assert bullpen.run_model_catalog_cli(args) == 1
    assert "workspace directory does not exist" in capsys.readouterr().err
