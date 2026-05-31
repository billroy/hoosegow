import asyncio
import importlib
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from server import microsandbox_runtime
from server.microsandbox_runtime import (
    MicrosandboxRuntime,
    HoosegowRuntimeError,
    HoosegowSandboxSpec,
    create_time_env,
    network_with_max_connections,
)


MICROSANDBOX_OK_VERSION = "0.5.2"


@dataclass
class DataNetwork:
    max_connections: int


def allow_supported_microsandbox_version(monkeypatch):
    monkeypatch.setattr(
        microsandbox_runtime,
        "microsandbox_distribution_version",
        lambda module=None: MICROSANDBOX_OK_VERSION,
    )


def test_network_with_max_connections_preserves_dataclass_type():
    network = DataNetwork(max_connections=10)

    updated = network_with_max_connections(network, 20)

    assert updated == DataNetwork(max_connections=20)
    assert updated is not network


def test_network_with_max_connections_mutates_non_dataclass_object():
    network = SimpleNamespace(max_connections=10)

    updated = network_with_max_connections(network, 20)

    assert updated is network
    assert network.max_connections == 20


def test_network_with_max_connections_requires_sdk_support():
    with pytest.raises(HoosegowRuntimeError, match="does not expose max_connections"):
        network_with_max_connections(object(), 20)


def test_create_time_env_keeps_sandbox_create_environment_small(tmp_path):
    spec = HoosegowSandboxSpec(
        sandbox_name="demo",
        workspace=tmp_path / "workspace",
        source_root=tmp_path / "source",
        sandbox_home=tmp_path / "home",
    )
    spec.runtime_env.update({
        "HOME": "/home/agent",
        "USER": "agent",
        "LOGNAME": "agent",
        "OPENAI_API_KEY": "secret",
    })

    assert create_time_env(spec) == {
        "HOME": "/home/agent",
        "USER": "agent",
        "LOGNAME": "agent",
    }


def test_runtime_create_uses_prepared_snapshot_bullpen_volumes_and_small_env(tmp_path, monkeypatch):
    captured = {}

    class FakeVolume:
        @staticmethod
        def bind(path, readonly=False):
            return {"path": path, "readonly": readonly}

    class FakeNetwork:
        @staticmethod
        def allow_all():
            return DataNetwork(max_connections=1)

    class FakeSnapshot:
        @staticmethod
        async def get(_base):
            return SimpleNamespace(path="/snapshots/hoosegow-base")

    class FakeSandbox:
        @staticmethod
        def create(name, **kwargs):
            captured["name"] = name
            captured["kwargs"] = kwargs
            return SimpleNamespace(name=name)

    module = SimpleNamespace(
        Sandbox=FakeSandbox,
        Snapshot=FakeSnapshot,
        Volume=FakeVolume,
        Network=FakeNetwork,
    )
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    allow_supported_microsandbox_version(monkeypatch)
    monkeypatch.setattr(microsandbox_runtime, "ensure_host_nofile", lambda target: (target, target))

    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    home = tmp_path / "home"
    workspace.mkdir()
    source.mkdir()
    spec = HoosegowSandboxSpec(
        sandbox_name="demo",
        workspace=workspace,
        source_root=source,
        sandbox_home=home,
        ports={61234: 5859},
        runtime_env={
            "HOME": "/home/agent",
            "USER": "agent",
            "LOGNAME": "agent",
            "OPENAI_API_KEY": "secret",
        },
        network_max_connections=3333,
    )

    result = asyncio.run(MicrosandboxRuntime().create(spec))

    assert result.name == "demo"
    assert captured["name"] == "demo"
    assert captured["kwargs"]["snapshot"] == "/snapshots/hoosegow-base"
    assert captured["kwargs"]["detached"] is True
    assert captured["kwargs"]["replace"] is True
    assert captured["kwargs"]["ports"] == {61234: 5859}
    assert captured["kwargs"]["volumes"] == {
        "/app": {"path": str(source), "readonly": True},
        "/workspace": {"path": str(workspace), "readonly": False},
        "/home/agent": {"path": str(home), "readonly": False},
    }
    assert captured["kwargs"]["network"] == DataNetwork(max_connections=3333)
    assert captured["kwargs"]["env"] == {
        "HOME": "/home/agent",
        "USER": "agent",
        "LOGNAME": "agent",
    }


def test_prepared_base_snapshot_path_opens_lazy_snapshot(monkeypatch):
    class Snapshot:
        async def open(self):
            return SimpleNamespace(path="/lazy/snapshot")

    class FakeSnapshot:
        @staticmethod
        def get(_base):
            return Snapshot()

    module = SimpleNamespace(
        Sandbox=object,
        Snapshot=FakeSnapshot,
        Volume=object,
        Network=object,
    )
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    allow_supported_microsandbox_version(monkeypatch)

    path = asyncio.run(MicrosandboxRuntime().prepared_base_snapshot_path("base"))

    assert path == "/lazy/snapshot"


def test_runtime_connect_uses_handle_connect(monkeypatch):
    class Sandbox:
        pass

    class Handle:
        async def connect(self):
            return Sandbox()

    class FakeSandbox:
        @staticmethod
        async def get(_name):
            return Handle()

    module = SimpleNamespace(
        Sandbox=FakeSandbox,
        Snapshot=object,
        Volume=object,
        Network=object,
    )
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    allow_supported_microsandbox_version(monkeypatch)

    sandbox = asyncio.run(MicrosandboxRuntime().connect("demo"))

    assert isinstance(sandbox, Sandbox)


def test_ensure_installed_runs_sdk_installer_when_missing(monkeypatch):
    calls = []

    async def is_installed():
        calls.append("is_installed")
        return False

    async def install():
        calls.append("install")

    module = SimpleNamespace(
        Sandbox=object,
        Snapshot=object,
        Volume=object,
        Network=object,
        is_installed=is_installed,
        install=install,
    )
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    allow_supported_microsandbox_version(monkeypatch)

    asyncio.run(MicrosandboxRuntime().ensure_installed())

    assert calls == ["is_installed", "install"]


def test_runtime_init_reports_missing_sdk_api(monkeypatch):
    monkeypatch.setattr(importlib, "import_module", lambda name: SimpleNamespace(Sandbox=object))
    allow_supported_microsandbox_version(monkeypatch)

    with pytest.raises(HoosegowRuntimeError, match="missing the expected SDK API"):
        MicrosandboxRuntime()


def test_runtime_init_rejects_stale_microsandbox_distribution(monkeypatch):
    module = SimpleNamespace(
        Sandbox=object,
        Snapshot=object,
        Volume=object,
        Network=object,
    )
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    monkeypatch.setattr(
        microsandbox_runtime,
        "microsandbox_distribution_version",
        lambda module=None: "0.4.4",
    )

    with pytest.raises(HoosegowRuntimeError, match="published-port TCP stall fix"):
        MicrosandboxRuntime()
