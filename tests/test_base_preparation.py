import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import base as hoosegow_base
from server.microsandbox_runtime import HoosegowRuntimeError, HoosegowSandboxSpec


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
    return HoosegowSandboxSpec(
        sandbox_name="demo",
        workspace=workspace,
        source_root=source,
        sandbox_home=home,
        base="hoosegow-test-base",
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
            assert "@google/antigravity-cli" in command
            assert "@google/gemini-cli" not in command
            assert "opencode-ai" in command
        if label == "Verifying prepared base":
            assert "codex --version" in command
            assert "antigravity --version" in command
            assert "gemini --version" not in command
            assert "opencode --version" in command
        return SimpleNamespace(stdout_text="", stderr_text="", returncode=0)

    monkeypatch.setattr(hoosegow_base, "run_logged_sandbox_shell", fake_run)

    asyncio.run(hoosegow_base.prepare_base(runtime, spec, source_image="node:test"))

    assert labels == [
        "Installing OS packages",
        "Installing Hoosegow Python dependencies",
        "Installing agent CLIs",
        "Verifying prepared base",
        "Validating prepared base snapshot",
    ]
    assert runtime.events == [
        ("stop", "hoosegow-test-base-prepare"),
        ("remove", "hoosegow-test-base-prepare"),
        ("create_prepare", "hoosegow-test-base-prepare", "node:test", str(spec.source_root)),
        ("sandbox.stop_and_wait",),
        ("snapshot", "hoosegow-test-base-prepare", "hoosegow-test-base"),
        ("create_validation", "hoosegow-test-base-v", "hoosegow-test-base", "demo"),
        ("stop", "hoosegow-test-base-v"),
        ("remove", "hoosegow-test-base-v"),
        ("remove", "hoosegow-test-base-prepare"),
    ]


def test_prepare_base_removes_prepare_sandbox_when_step_fails(tmp_path, monkeypatch):
    runtime = FakeRuntime()
    spec = _spec(tmp_path)

    async def fake_run(_sandbox, _command, *, label):
        if label == "Installing agent CLIs":
            raise HoosegowRuntimeError("npm failed")
        return SimpleNamespace(stdout_text="", stderr_text="", returncode=0)

    monkeypatch.setattr(hoosegow_base, "run_logged_sandbox_shell", fake_run)

    with pytest.raises(HoosegowRuntimeError, match="npm failed"):
        asyncio.run(hoosegow_base.prepare_base(runtime, spec))

    assert ("snapshot", "hoosegow-test-base-prepare", "hoosegow-test-base") not in runtime.events
    assert runtime.events[-1] == ("remove", "hoosegow-test-base-prepare")


def test_ensure_prepared_base_auto_prepares_when_missing(tmp_path, monkeypatch):
    runtime = FakeRuntime(prepared=False)
    spec = _spec(tmp_path)
    calls = []

    async def fake_prepare(got_runtime, got_spec, *, force):
        calls.append((got_runtime, got_spec, force))

    monkeypatch.setattr(hoosegow_base, "prepare_base", fake_prepare)

    asyncio.run(hoosegow_base.ensure_prepared_base(runtime, spec, auto_prepare=True))

    assert calls == [(runtime, spec, True)]


def test_ensure_prepared_base_can_still_require_manual_prepare(tmp_path):
    runtime = FakeRuntime(prepared=False)
    spec = _spec(tmp_path)

    with pytest.raises(HoosegowRuntimeError, match="--prepare-base"):
        asyncio.run(hoosegow_base.ensure_prepared_base(runtime, spec, auto_prepare=False))


def test_base_dependency_refresh_detects_missing_or_changed_metadata():
    latest = {
        "claude": "1.0.0",
        "codex": "2.0.0",
        "antigravity": "3.0.0",
        "opencode": "4.0.0",
    }

    assert hoosegow_base.base_needs_dependency_refresh(None, latest) is True
    assert hoosegow_base.base_needs_dependency_refresh({"agent_versions": latest}, latest) is False
    assert hoosegow_base.base_needs_dependency_refresh(
        {"agent_versions": {**latest, "codex": "1.9.9"}},
        latest,
    ) is True


def test_latest_agent_cli_versions_queries_npm_packages(tmp_path, monkeypatch):
    calls = []
    envs = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        envs.append(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout=f"{argv[2]}-version\n", stderr="")

    monkeypatch.setattr(hoosegow_base.subprocess, "run", fake_run)

    cache_dir = tmp_path / "npm-cache"
    versions = hoosegow_base.latest_agent_cli_versions(cache_dir=cache_dir)

    assert versions == {
        "claude": "@anthropic-ai/claude-code-version",
        "codex": "@openai/codex-version",
        "antigravity": "@google/antigravity-cli-version",
        "opencode": "opencode-ai-version",
    }
    assert calls == [
        ["npm", "view", "@anthropic-ai/claude-code", "version"],
        ["npm", "view", "@openai/codex", "version"],
        ["npm", "view", "@google/antigravity-cli", "version"],
        ["npm", "view", "opencode-ai", "version"],
    ]
    assert all(env["npm_config_cache"] == str(cache_dir.resolve()) for env in envs)
    assert all(env["npm_config_update_notifier"] == "false" for env in envs)


def test_latest_agent_cli_versions_skips_missing_optional_antigravity_package(tmp_path, monkeypatch):
    def fake_run(argv, **_kwargs):
        package = argv[2]
        if package == "@google/antigravity-cli":
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="npm error code E404\nnpm error 404 Not Found",
            )
        return SimpleNamespace(returncode=0, stdout=f"{package}-version\n", stderr="")

    monkeypatch.setattr(hoosegow_base.subprocess, "run", fake_run)

    versions = hoosegow_base.latest_agent_cli_versions(cache_dir=tmp_path / "npm-cache")

    assert versions == {
        "claude": "@anthropic-ai/claude-code-version",
        "codex": "@openai/codex-version",
        "opencode": "opencode-ai-version",
    }


def test_latest_agent_cli_versions_still_fails_required_package_errors(tmp_path, monkeypatch):
    def fake_run(argv, **_kwargs):
        if argv[2] == "@openai/codex":
            return SimpleNamespace(returncode=1, stdout="", stderr="npm error code E500")
        return SimpleNamespace(returncode=0, stdout=f"{argv[2]}-version\n", stderr="")

    monkeypatch.setattr(hoosegow_base.subprocess, "run", fake_run)

    with pytest.raises(HoosegowRuntimeError, match="Could not check latest codex package version"):
        hoosegow_base.latest_agent_cli_versions(cache_dir=tmp_path / "npm-cache")


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

    monkeypatch.setattr(hoosegow_base, "MicrosandboxRuntime", Runtime)

    status = asyncio.run(hoosegow_base.base_status("lazy-base"))

    assert status["prepared"] is True
    assert status["state"] == "ready"
    assert status["path"] == "/tmp/prepared-base"
