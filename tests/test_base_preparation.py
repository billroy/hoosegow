import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import base as toady_base
from server.microsandbox_runtime import ToadyRuntimeError, ToadySandboxSpec


class FakePrepareSandbox:
    def __init__(self, events):
        self.events = events

    async def stop_and_wait(self):
        self.events.append(("sandbox.stop_and_wait",))


class FakeRuntime:
    def __init__(self, *, prepared=True):
        self.prepared = prepared
        self.events = []
        self.prepare_sandbox = FakePrepareSandbox(self.events)

    async def stop(self, name):
        self.events.append(("stop", name))

    async def remove(self, name):
        self.events.append(("remove", name))

    async def create_prepare_sandbox(self, name, source_image, source):
        self.events.append(("create_prepare", name, source_image, str(source)))
        return self.prepare_sandbox

    async def create_snapshot(self, sandbox_name, base):
        self.events.append(("snapshot", sandbox_name, base))

    async def create_base_validation_sandbox(self, name, base, spec):
        self.events.append(("create_validation", name, base, spec.sandbox_name))
        return object()

    async def prepared_base_exists(self, base):
        self.events.append(("exists", base))
        return self.prepared


def _spec(tmp_path):
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    home = tmp_path / "home"
    workspace.mkdir()
    source.mkdir()
    return ToadySandboxSpec(
        sandbox_name="demo",
        workspace=workspace,
        source_root=source,
        sandbox_home=home,
        base="toady-test-base",
    )


def test_prepare_base_runs_bullpen_style_steps_and_cleans_prepare_sandbox(tmp_path, monkeypatch):
    runtime = FakeRuntime()
    spec = _spec(tmp_path)
    labels = []

    async def fake_run(_sandbox, command, *, label):
        labels.append(label)
        if label == "Installing OS packages":
            assert "tmux" in command
            assert "nano" in command
        if label == "Installing agent CLIs":
            assert "@anthropic-ai/claude-code" in command
            assert "@openai/codex" in command
            assert "@google/gemini-cli" in command
            assert "opencode-ai" in command
        if label == "Verifying prepared base":
            assert "codex --version" in command
            assert "gemini --version" in command
            assert "opencode --version" in command
        return SimpleNamespace(stdout_text="", stderr_text="", returncode=0)

    monkeypatch.setattr(toady_base, "run_logged_sandbox_shell", fake_run)

    asyncio.run(toady_base.prepare_base(runtime, spec, source_image="node:test"))

    assert labels == [
        "Installing OS packages",
        "Installing Toady Python dependencies",
        "Installing agent CLIs",
        "Verifying prepared base",
        "Validating prepared base snapshot",
    ]
    assert runtime.events == [
        ("stop", "toady-test-base-prepare"),
        ("remove", "toady-test-base-prepare"),
        ("create_prepare", "toady-test-base-prepare", "node:test", str(spec.source_root)),
        ("sandbox.stop_and_wait",),
        ("snapshot", "toady-test-base-prepare", "toady-test-base"),
        ("create_validation", "toady-test-base-v", "toady-test-base", "demo"),
        ("stop", "toady-test-base-v"),
        ("remove", "toady-test-base-v"),
        ("remove", "toady-test-base-prepare"),
    ]


def test_prepare_base_removes_prepare_sandbox_when_step_fails(tmp_path, monkeypatch):
    runtime = FakeRuntime()
    spec = _spec(tmp_path)

    async def fake_run(_sandbox, _command, *, label):
        if label == "Installing agent CLIs":
            raise ToadyRuntimeError("npm failed")
        return SimpleNamespace(stdout_text="", stderr_text="", returncode=0)

    monkeypatch.setattr(toady_base, "run_logged_sandbox_shell", fake_run)

    with pytest.raises(ToadyRuntimeError, match="npm failed"):
        asyncio.run(toady_base.prepare_base(runtime, spec))

    assert ("snapshot", "toady-test-base-prepare", "toady-test-base") not in runtime.events
    assert runtime.events[-1] == ("remove", "toady-test-base-prepare")


def test_ensure_prepared_base_auto_prepares_when_missing(tmp_path, monkeypatch):
    runtime = FakeRuntime(prepared=False)
    spec = _spec(tmp_path)
    calls = []

    async def fake_prepare(got_runtime, got_spec, *, force):
        calls.append((got_runtime, got_spec, force))

    monkeypatch.setattr(toady_base, "prepare_base", fake_prepare)

    asyncio.run(toady_base.ensure_prepared_base(runtime, spec, auto_prepare=True))

    assert calls == [(runtime, spec, True)]


def test_ensure_prepared_base_can_still_require_manual_prepare(tmp_path):
    runtime = FakeRuntime(prepared=False)
    spec = _spec(tmp_path)

    with pytest.raises(ToadyRuntimeError, match="--prepare-base"):
        asyncio.run(toady_base.ensure_prepared_base(runtime, spec, auto_prepare=False))


def test_base_dependency_refresh_detects_missing_or_changed_metadata():
    latest = {
        "claude": "1.0.0",
        "codex": "2.0.0",
        "gemini": "3.0.0",
        "opencode": "4.0.0",
    }

    assert toady_base.base_needs_dependency_refresh(None, latest) is True
    assert toady_base.base_needs_dependency_refresh({"agent_versions": latest}, latest) is False
    assert toady_base.base_needs_dependency_refresh(
        {"agent_versions": {**latest, "codex": "1.9.9"}},
        latest,
    ) is True


def test_latest_agent_cli_versions_queries_npm_packages(monkeypatch):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=f"{argv[2]}-version\n", stderr="")

    monkeypatch.setattr(toady_base.subprocess, "run", fake_run)

    versions = toady_base.latest_agent_cli_versions()

    assert versions == {
        "claude": "@anthropic-ai/claude-code-version",
        "codex": "@openai/codex-version",
        "gemini": "@google/gemini-cli-version",
        "opencode": "opencode-ai-version",
    }
    assert calls == [
        ["npm", "view", "@anthropic-ai/claude-code", "version"],
        ["npm", "view", "@openai/codex", "version"],
        ["npm", "view", "@google/gemini-cli", "version"],
        ["npm", "view", "opencode-ai", "version"],
    ]


def test_base_status_opens_snapshot_when_path_is_lazy(monkeypatch):
    class Snapshot:
        async def open(self):
            return SimpleNamespace(path=Path("/tmp/prepared-base"))

    class Runtime:
        async def ensure_installed(self):
            return None

        async def get_prepared_base(self, base):
            assert base == "lazy-base"
            return Snapshot()

    monkeypatch.setattr(toady_base, "MicrosandboxRuntime", Runtime)

    status = asyncio.run(toady_base.base_status("lazy-base"))

    assert status["prepared"] is True
    assert status["state"] == "ready"
    assert status["path"] == "/tmp/prepared-base"
