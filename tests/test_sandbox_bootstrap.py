import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import sandbox_bootstrap
from server.microsandbox_runtime import ToadyRuntimeError, ToadySandboxSpec


def _spec(tmp_path):
    spec = ToadySandboxSpec(
        sandbox_name="demo",
        workspace=tmp_path / "workspace",
        source_root=tmp_path / "source",
        sandbox_home=tmp_path / "home",
        guest_nofile=7777,
        host_nofile=8888,
        network_max_connections=9999,
    )
    spec.runtime_env["OPENAI_API_KEY"] = "secret-token"
    return spec


class FakeSandbox:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def exec(self, command, args):
        self.calls.append((command, args))
        return self.result


def test_build_runtime_env_sets_controller_identity_and_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_bootstrap.os, "getuid", lambda: 501)
    monkeypatch.setattr(sandbox_bootstrap.os, "getgid", lambda: 20)
    spec = _spec(tmp_path)

    sandbox_bootstrap.build_runtime_env(spec, controller_port=61234, controller_token="token")

    assert spec.runtime_env["HOME"] == "/home/agent"
    assert spec.runtime_env["TOADY_UID"] == "501"
    assert spec.runtime_env["TOADY_GID"] == "20"
    assert spec.runtime_env["TOADY_PTYD_PORT"] == "61234"
    assert spec.runtime_env["TOADY_PTYD_TOKEN"] == "token"
    assert spec.runtime_env["TOADY_MICROSANDBOX_GUEST_NOFILE"] == "7777"
    assert spec.runtime_env["TOADY_MICROSANDBOX_MAX_CONNECTIONS"] == "9999"


def test_run_configured_sandbox_shell_redacts_secrets_and_uses_label(tmp_path):
    spec = _spec(tmp_path)
    sandbox_bootstrap.build_runtime_env(spec, controller_token="controller-secret")
    sandbox = FakeSandbox(SimpleNamespace(returncode=1, stderr_text="leaked secret-token"))

    with pytest.raises(ToadyRuntimeError) as excinfo:
        asyncio.run(
            sandbox_bootstrap.run_configured_sandbox_shell(
                sandbox,
                spec,
                "echo secret-token",
                label="configure sensitive thing",
            )
        )

    message = str(excinfo.value)
    assert "Sandbox command failed: configure sensitive thing" in message
    assert "secret-token" not in message
    assert "[REDACTED]" in message
    assert sandbox.calls[0][0] == "bash"
    assert sandbox.calls[0][1][0] == "-lc"


def test_prepare_runtime_dirs_writes_guest_fd_limit(tmp_path, monkeypatch):
    spec = _spec(tmp_path)
    captured = {}

    async def fake_run(_sandbox, got_spec, command, *, label):
        captured["spec"] = got_spec
        captured["command"] = command
        captured["label"] = label

    monkeypatch.setattr(sandbox_bootstrap, "run_configured_sandbox_shell", fake_run)

    asyncio.run(sandbox_bootstrap.prepare_runtime_dirs(object(), spec))

    assert captured["spec"] is spec
    assert captured["label"] == "prepare Microsandbox runtime user"
    assert 'chown agent:"$group_name" /workspace /home/agent /home/agent/logs' in captured["command"]
    assert "agent soft nofile 7777" in captured["command"]
    assert "agent hard nofile 7777" in captured["command"]
    assert "pam_limits may not be enforcing" in captured["command"]


def test_start_pty_controller_uses_env_token_reference_not_raw_secret(tmp_path, monkeypatch):
    spec = _spec(tmp_path)
    spec.runtime_env["TOADY_PTYD_TOKEN"] = "raw-controller-token"
    captured = {}

    async def fake_run_as_agent(_sandbox, got_spec, command, *, label):
        captured["spec"] = got_spec
        captured["command"] = command
        captured["label"] = label

    monkeypatch.setattr(sandbox_bootstrap, "run_as_agent", fake_run_as_agent)

    asyncio.run(sandbox_bootstrap.start_pty_controller(object(), spec))

    assert captured["spec"] is spec
    assert captured["label"] == "start PTY controller"
    assert "/app/guest/toady-ptyd.py" in captured["command"]
    assert '--token "$TOADY_PTYD_TOKEN"' in captured["command"]
    assert "raw-controller-token" not in captured["command"]


def test_run_sandbox_shell_falls_back_to_shell_method_and_reports_output():
    class ShellSandbox:
        def shell(self, command):
            assert command == "false"
            return SimpleNamespace(exit_code=2, stdout=b"out", stderr=b"err")

    with pytest.raises(ToadyRuntimeError) as excinfo:
        asyncio.run(sandbox_bootstrap.run_sandbox_shell(ShellSandbox(), "false"))

    assert "Sandbox command failed: false" in str(excinfo.value)
    assert "out\nerr" in str(excinfo.value)


def test_extract_urls_strips_trailing_sentence_punctuation():
    assert sandbox_bootstrap.extract_urls("open http://127.0.0.1:5173/foo). next") == [
        "http://127.0.0.1:5173/foo"
    ]
