import asyncio
import importlib
from types import SimpleNamespace

import pytest

from server import microsandbox_runtime
from server.microsandbox_runtime import (
    MicrosandboxRuntime,
    HoosegowRuntimeError,
    HoosegowSandboxSpec,
    validate_network_max_connections,
)


MICROSANDBOX_OK_VERSION = "0.7.5"


def allow_supported_microsandbox_version(monkeypatch):
    monkeypatch.setattr(
        microsandbox_runtime,
        "microsandbox_distribution_version",
        lambda module=None: MICROSANDBOX_OK_VERSION,
    )


def test_validate_network_max_connections_returns_supported_value():
    assert validate_network_max_connections(20) == 20


@pytest.mark.parametrize("value", [0, 4097])
def test_validate_network_max_connections_enforces_limit(value):
    with pytest.raises(HoosegowRuntimeError, match="between 1 and 4096"):
        validate_network_max_connections(value)


def test_runtime_create_restores_prepared_snapshot_with_bullpen_volumes(tmp_path, monkeypatch):
    captured = {}

    class FakeVolume:
        @staticmethod
        def bind(path, readonly=False):
            return {"path": path, "readonly": readonly}

    class FakeNetwork:
        @staticmethod
        def allow_all():
            return SimpleNamespace(policy="allow-all")

    class FakeSnapshot:
        @staticmethod
        async def get(name):
            assert name == "hoosegow-microsandbox-0.7:hoosegow-microsandbox-0.7"
            return SimpleNamespace(reference="snapshot-reference")

    class FakeSandbox:
        @staticmethod
        def restore(snapshot, *, name, **kwargs):
            captured["snapshot"] = snapshot
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
    assert captured["snapshot"].reference == "snapshot-reference"
    assert captured["kwargs"]["ports"] == {61234: 5859}
    assert captured["kwargs"]["volumes"] == {
        "/app": {"path": str(source), "readonly": True},
        "/workspace": {"path": str(workspace), "readonly": False},
        "/home/agent": {"path": str(home), "readonly": False},
    }
    assert captured["kwargs"]["network_policy"] == "allow-all"
    assert captured["kwargs"]["max_tcp_connections"] == 3333
    assert captured["kwargs"]["max_udp_connections"] == 3333


def test_create_snapshot_uses_microsandbox_07_grouped_contract(monkeypatch):
    captured = {}

    class FakeSnapshot:
        @staticmethod
        async def remove(name, **kwargs):
            captured["removed"] = (name, kwargs)

        @staticmethod
        async def create(name, **kwargs):
            captured["name"] = name
            captured["kwargs"] = kwargs

    module = SimpleNamespace(
        Sandbox=object,
        Snapshot=FakeSnapshot,
        Volume=object,
        Network=object,
    )
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    allow_supported_microsandbox_version(monkeypatch)

    asyncio.run(MicrosandboxRuntime().create_snapshot("hoosegow-base-prepare", "hoosegow-base"))

    assert captured == {
        "removed": ("hoosegow-base:hoosegow-base", {"force": True}),
        "name": "hoosegow-base",
        "kwargs": {
            "from_sandbox": "hoosegow-base-prepare",
            "group": "hoosegow-base",
            "labels": {"app": "hoosegow", "kind": "microsandbox-base"},
        },
    }


def test_prepared_base_snapshot_uses_group_qualified_name(monkeypatch):
    captured = {}
    class FakeSnapshot:
        @staticmethod
        def get(name):
            captured["name"] = name
            return SimpleNamespace(reference="snapshot-reference")

    module = SimpleNamespace(
        Sandbox=object,
        Snapshot=FakeSnapshot,
        Volume=object,
        Network=object,
    )
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    allow_supported_microsandbox_version(monkeypatch)

    snapshot = asyncio.run(MicrosandboxRuntime().prepared_base_snapshot("base"))

    assert captured["name"] == "base:base"
    assert snapshot.reference == "snapshot-reference"


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

    with pytest.raises(HoosegowRuntimeError, match=r"requires microsandbox >= 0\.7\.5"):
        MicrosandboxRuntime()
