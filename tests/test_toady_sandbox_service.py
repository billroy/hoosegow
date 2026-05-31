import asyncio
import json
import os
from pathlib import Path

import pytest

from server import base as toady_base
from server.microsandbox_runtime import mark_open_fds_close_on_exec
from server.sandboxes import SandboxService, SandboxServiceError
from server.toady_validation import (
    ValidationError,
    normalize_browse_roots,
    validate_slug,
    validate_workspace_path,
)


def test_validate_slug_rejects_bullpen_style_ids():
    assert validate_slug("demo-1") == "demo-1"
    with pytest.raises(ValidationError):
        validate_slug("Bad Name")


def test_mark_open_fds_close_on_exec_clears_inheritable_fd():
    read_fd, write_fd = os.pipe()
    try:
        os.set_inheritable(read_fd, True)

        marked = mark_open_fds_close_on_exec()

        assert read_fd in marked
        assert not os.get_inheritable(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_workspace_validation_allows_browse_root_child(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()

    path, warnings = validate_workspace_path(
        str(workspace),
        browse_roots=[str(tmp_path)],
        state_home=str(tmp_path / ".toady"),
    )

    assert path == str(workspace.resolve())
    assert warnings == []


def test_default_browse_roots_prefer_cwd_before_home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    roots = normalize_browse_roots(None)

    assert roots[0] == str(tmp_path.resolve())


def test_workspace_validation_rejects_symlink_escape(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = allowed / "link"
    link.symlink_to(outside)

    with pytest.raises(ValidationError):
        validate_workspace_path(
            str(link),
            browse_roots=[str(allowed)],
            state_home=str(tmp_path / ".toady"),
        )


def test_sandbox_service_creates_manifest_and_allocates_controller_port(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        source_root=str(Path(__file__).resolve().parents[1]),
        port_pool="63100-63102",
    )

    created = service.create_manifest({"name": "demo", "workspace_root": str(workspace)})

    assert created["slug"] == "demo"
    assert created["canonical_workspace_path"] == str(workspace.resolve())
    assert created["last_status"] == "configured"
    assert created["controller"]["transport"] == "http-long-poll"
    assert created["controller"]["host_port"] == 63100
    assert service.list()[0]["slug"] == "demo"


def test_sandbox_service_prevents_duplicate_slug(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(home=str(tmp_path / "state"), browse_roots=[str(tmp_path)])
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})

    with pytest.raises(RuntimeError):
        service.create_manifest({"name": "demo", "workspace_root": str(workspace)})


def test_sandbox_service_rejects_foreign_runtime_slug(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()

    class FakeRuntime:
        async def exists(self, slug):
            return slug == "demo"

    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)
    service = SandboxService(home=str(tmp_path / "state"), browse_roots=[str(tmp_path)])

    with pytest.raises(SandboxServiceError, match="outside Toady: demo"):
        service.create_manifest({"name": "demo", "workspace_root": str(workspace)})


def test_sandbox_service_allows_running_shared_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )
    service.create_manifest({"name": "first", "workspace_root": str(workspace)})
    manifest = service.store.get("first")
    manifest.last_status = "running"
    service.store.save(manifest)

    shared = service.create_manifest({
        "name": "second",
        "workspace_root": str(workspace),
    })

    assert shared["slug"] == "second"
    assert shared["canonical_workspace_path"] == str(workspace.resolve())


def test_sandbox_service_rejects_create_when_admission_limit_exceeded(tmp_path):
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        max_sandboxes=1,
        max_total_vcpus=16,
        max_total_memory_mib=32768,
    )
    service.create_manifest({"name": "first", "workspace_root": str(first_workspace)})
    manifest = service.store.get("first")
    manifest.last_status = "running"
    service.store.save(manifest)

    with pytest.raises(SandboxServiceError, match="Resource admission refused"):
        service.create_manifest({"name": "second", "workspace_root": str(second_workspace)})


def test_sandbox_service_rejects_start_when_resource_totals_exceeded(tmp_path):
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        max_sandboxes=2,
        max_total_vcpus=4,
        max_total_memory_mib=8192,
    )
    service.create_manifest({"name": "first", "workspace_root": str(first_workspace), "vcpus": 2, "memory_mib": 4096})
    service.create_manifest({"name": "second", "workspace_root": str(second_workspace), "vcpus": 3, "memory_mib": 4096})
    manifest = service.store.get("first")
    manifest.last_status = "running"
    service.store.save(manifest)

    with pytest.raises(SandboxServiceError, match="vCPUs 5/4"):
        asyncio.run(service.start("second"))


def test_sandbox_service_browses_allowed_workspace_roots(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    child = root / "project"
    child.mkdir()
    service = SandboxService(home=str(tmp_path / "state"), browse_roots=[str(root)])

    browse = service.browse_workspaces()

    assert browse["path"] == str(root.resolve())
    assert browse["parent"] is None
    assert browse["roots"] == [{"name": str(root.resolve()), "path": str(root.resolve())}]
    assert browse["entries"] == [
        {
            "name": "project",
            "path": str(child.resolve()),
            "basename": "project",
        }
    ]


def test_sandbox_service_browse_rejects_path_outside_roots(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    service = SandboxService(home=str(tmp_path / "state"), browse_roots=[str(root)])

    with pytest.raises(ValidationError):
        service.browse_workspaces(str(outside))


def test_sandbox_service_publishes_persisted_port_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})

    mapping = service.publish_port("demo", {"guest_port": 5173})

    assert mapping == {"guest_port": 5173, "host_port": 63101, "status": "active"}
    assert service.list_ports("demo") == [mapping]


def test_sandbox_service_writes_lifecycle_log_without_controller_token(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    state = tmp_path / "state"
    service = SandboxService(
        home=str(state),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )

    created = service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    mapping = service.publish_port("demo", {"guest_port": 5173})
    service.unpublish_port("demo", {"host_port": mapping["host_port"]})

    log_path = state / "logs" / "sandbox-demo.log"
    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]

    assert [record["event"] for record in records] == [
        "created",
        "port_published",
        "port_unpublished",
    ]
    assert records[0]["controller_host_port"] == created["controller"]["host_port"]
    assert records[1]["guest_port"] == 5173
    assert created["controller"]["token"] not in log_path.read_text(encoding="utf-8")


def test_sandbox_service_marks_running_publish_pending_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    manifest = service.store.get("demo")
    manifest.last_status = "running"
    service.store.save(manifest)

    mapping = service.publish_port("demo", {"guest_port": 3000, "host_port": 63104})

    assert mapping["status"] == "pending_restart"


def test_sandbox_service_rejects_reserved_host_port(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})

    with pytest.raises(SandboxServiceError):
        service.publish_port("demo", {"guest_port": 3000, "host_port": 63100})


def test_sandbox_service_marks_published_port_conflict_on_start(tmp_path, monkeypatch):
    occupied = set()
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda port: port in occupied)
    monkeypatch.setattr("server.sandboxes.host_port_owner", lambda port: f"python 123 *:{port}")

    class FakeRuntime:
        async def ensure_installed(self):
            return None

        async def stop(self, _name):
            return None

        async def remove(self, _name):
            return None

    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    service.publish_port("demo", {"guest_port": 3000, "host_port": 63103})
    occupied.add(63103)

    with pytest.raises(SandboxServiceError, match="Published port conflict"):
        asyncio.run(service.start("demo"))

    manifest = service.store.get("demo")
    assert manifest.last_status == "error"
    assert manifest.published_ports[0]["status"] == "conflict"
    assert "python 123" in manifest.published_ports[0]["conflict"]


def test_sandbox_service_reassigns_occupied_controller_port_on_start(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )
    service.create_manifest({
        "name": "demo",
        "workspace_root": str(workspace),
        "controller_host_port": 63100,
    })
    captured = {}
    fd_guard_calls = []

    class FakeRuntime:
        async def ensure_installed(self):
            return None

        async def stop(self, _name):
            return None

        async def remove(self, _name):
            return None

        async def create(self, spec):
            captured["ports"] = spec.ports
            return object()

    async def async_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda port: port == 63100)
    monkeypatch.setattr("server.sandboxes.ensure_host_ports_available", lambda _ports: None)
    monkeypatch.setattr("server.sandboxes.mark_open_fds_close_on_exec", lambda: fd_guard_calls.append(True))
    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr(service, "_sync_manifest_clock", async_noop)
    monkeypatch.setattr("server.sandboxes.prepare_runtime_dirs", async_noop)
    monkeypatch.setattr("server.sandboxes.disable_guest_ipv6_for_claude", async_noop)
    monkeypatch.setattr("server.sandboxes.verify_mount_access", async_noop)
    monkeypatch.setattr("server.sandboxes.configure_codex_cli", async_noop)
    monkeypatch.setattr("server.sandboxes.start_pty_controller", async_noop)
    monkeypatch.setattr("server.sandboxes.detach_sandbox", async_noop)
    monkeypatch.setattr("server.sandboxes.verify_detached_sandbox", async_noop)
    monkeypatch.setattr("server.sandboxes.wait_for_controller_health", lambda _port: None)

    started = asyncio.run(service.start("demo"))

    assert started["last_status"] == "running"
    assert started["controller"]["host_port"] == 63101
    assert captured["ports"] == {63101: 5859}
    assert fd_guard_calls == [True]


def test_sandbox_service_removes_stopped_runtime_before_start(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    monkeypatch.setattr("server.sandboxes.ensure_host_ports_available", lambda _ports: None)
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    calls = []

    class FakeRuntime:
        async def ensure_installed(self):
            calls.append(("ensure",))

        async def stop(self, name):
            calls.append(("stop", name))

        async def remove(self, name):
            calls.append(("remove", name))

        async def create(self, spec):
            calls.append(("create", spec.sandbox_name))
            return object()

    async def async_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr(service, "_sync_manifest_clock", async_noop)
    monkeypatch.setattr("server.sandboxes.prepare_runtime_dirs", async_noop)
    monkeypatch.setattr("server.sandboxes.disable_guest_ipv6_for_claude", async_noop)
    monkeypatch.setattr("server.sandboxes.verify_mount_access", async_noop)
    monkeypatch.setattr("server.sandboxes.configure_codex_cli", async_noop)
    monkeypatch.setattr("server.sandboxes.start_pty_controller", async_noop)
    monkeypatch.setattr("server.sandboxes.detach_sandbox", async_noop)
    monkeypatch.setattr("server.sandboxes.verify_detached_sandbox", async_noop)
    monkeypatch.setattr("server.sandboxes.wait_for_controller_health", lambda _port: None)

    started = asyncio.run(service.start("demo"))

    assert started["last_status"] == "running"
    assert calls == [
        ("ensure",),
        ("stop", "demo"),
        ("remove", "demo"),
        ("create", "demo"),
    ]


def test_sandbox_service_clears_stale_self_conflict_before_start(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    monkeypatch.setattr("server.sandboxes.ensure_host_ports_available", lambda _ports: None)
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    manifest = service.store.get("demo")
    manifest.published_ports = [
        {"guest_port": 8000, "host_port": 63103, "status": "conflict", "conflict": "old msb listener"}
    ]
    service.store.save(manifest)

    class FakeRuntime:
        async def ensure_installed(self):
            return None

        async def stop(self, _name):
            return None

        async def remove(self, _name):
            return None

        async def create(self, _spec):
            return object()

    async def async_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr(service, "_sync_manifest_clock", async_noop)
    monkeypatch.setattr("server.sandboxes.prepare_runtime_dirs", async_noop)
    monkeypatch.setattr("server.sandboxes.disable_guest_ipv6_for_claude", async_noop)
    monkeypatch.setattr("server.sandboxes.verify_mount_access", async_noop)
    monkeypatch.setattr("server.sandboxes.configure_codex_cli", async_noop)
    monkeypatch.setattr("server.sandboxes.start_pty_controller", async_noop)
    monkeypatch.setattr("server.sandboxes.detach_sandbox", async_noop)
    monkeypatch.setattr("server.sandboxes.verify_detached_sandbox", async_noop)
    monkeypatch.setattr("server.sandboxes.wait_for_controller_health", lambda _port: None)

    started = asyncio.run(service.start("demo"))

    assert started["last_status"] == "running"
    assert started["published_ports"] == [{"guest_port": 8000, "host_port": 63103, "status": "active"}]


def test_sandbox_service_cleans_created_runtime_when_bootstrap_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    monkeypatch.setattr("server.sandboxes.ensure_host_ports_available", lambda _ports: None)
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    calls = []

    class FakeRuntime:
        async def ensure_installed(self):
            calls.append(("ensure",))

        async def stop(self, name):
            calls.append(("stop", name))

        async def remove(self, name):
            calls.append(("remove", name))

        async def create(self, spec):
            calls.append(("create", spec.sandbox_name))
            return object()

    async def fail_prepare(*_args, **_kwargs):
        raise RuntimeError("prepare failed")

    async def async_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr(service, "_sync_manifest_clock", async_noop)
    monkeypatch.setattr("server.sandboxes.prepare_runtime_dirs", fail_prepare)

    with pytest.raises(RuntimeError, match="prepare failed"):
        asyncio.run(service.start("demo"))

    assert service.store.get("demo").last_status == "error"
    assert calls == [
        ("ensure",),
        ("stop", "demo"),
        ("remove", "demo"),
        ("create", "demo"),
        ("stop", "demo"),
        ("remove", "demo"),
    ]


def test_sandbox_service_check_clock_records_drift(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        clock_drift_warning_seconds=5,
    )
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    manifest = service.store.get("demo")
    manifest.last_status = "running"
    service.store.save(manifest)
    times = iter([1000.0, 1000.2, 1000.3, 1000.4])

    class FakeResult:
        returncode = 0
        stdout_text = "970\n"
        stderr_text = ""

    class FakeSandbox:
        def exec(self, _cmd, _args):
            return FakeResult()

    class FakeRuntime:
        async def ensure_installed(self):
            return None

        async def connect(self, slug):
            assert slug == "demo"
            return FakeSandbox()

    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr("server.sandboxes.time.time", lambda: next(times, 1000.5))

    checked = asyncio.run(service.check_clock("demo"))

    assert checked["clock"]["status"] == "drift"
    assert checked["clock"]["drift_seconds"] == -30.1


def test_sandbox_service_sync_clock_sets_guest_time_without_restart(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(home=str(tmp_path / "state"), browse_roots=[str(tmp_path)])
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    manifest = service.store.get("demo")
    manifest.last_status = "running"
    service.store.save(manifest)
    guest_epoch = {"value": 0}
    commands = []

    class FakeResult:
        returncode = 0
        stderr_text = ""

        def __init__(self, stdout_text=""):
            self.stdout_text = stdout_text

    class FakeSandbox:
        def exec(self, _cmd, args):
            command = args[-1]
            commands.append(command)
            if "date -u -s @" in command:
                guest_epoch["value"] = int(command.split("date -u -s @", 1)[1].split()[0])
                return FakeResult()
            return FakeResult(f"{guest_epoch['value']}\n")

    class FakeRuntime:
        async def ensure_installed(self):
            return None

        async def connect(self, slug):
            assert slug == "demo"
            return FakeSandbox()

    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr("server.sandboxes.time.time", lambda: 2000.25)

    synced = asyncio.run(service.sync_clock("demo"))

    assert any("date -u -s @2000" in command for command in commands)
    assert synced["last_status"] == "running"
    assert synced["clock"]["status"] == "synced"
    assert abs(synced["clock"]["drift_seconds"]) < 1


def test_sandbox_service_reassigns_conflicted_port(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    service.publish_port("demo", {"guest_port": 3000, "host_port": 63103})
    manifest = service.store.get("demo")
    manifest.published_ports[0]["status"] = "conflict"
    manifest.published_ports[0]["conflict"] = "python 123"
    service.store.save(manifest)

    mapping = service.reassign_port("demo", {"host_port": 63103})

    assert mapping == {"guest_port": 3000, "host_port": 63101, "status": "active"}
    assert service.list_ports("demo") == [mapping]


def test_sandbox_service_refreshes_stopped_sandbox_without_starting_or_rebuilding(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    latest = {"claude": "1", "codex": "2", "gemini": "3", "opencode": "4"}
    metadata = toady_base.write_base_metadata(
        service._base_metadata_path(),
        base=service.base,
        source_image="node:test",
        versions=latest,
    )
    calls = []

    class FakeRuntime:
        async def ensure_installed(self):
            calls.append(("ensure",))

        async def prepared_base_exists(self, base):
            calls.append(("exists", base))
            return True

        async def stop(self, name):
            calls.append(("stop", name))

        async def remove(self, name):
            calls.append(("remove", name))

    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr("server.sandboxes.toady_base.latest_agent_cli_versions", lambda: latest)

    result = asyncio.run(service.refresh_runtime_dependencies("demo"))

    assert result["updated"] is True
    assert result["restarted"] is False
    assert result["rebuilt_base"] is False
    assert result["sandbox"]["last_status"] == "configured"
    assert result["sandbox"]["runtime_generation"] == metadata["generation"]
    assert result["sandbox"]["runtime_versions"] == latest
    assert calls == [
        ("ensure",),
        ("exists", service.base),
        ("stop", "demo"),
        ("remove", "demo"),
    ]


def test_sandbox_service_refresh_rebuilds_only_when_versions_change(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(
        home=str(tmp_path / "state"),
        browse_roots=[str(tmp_path)],
        port_pool="63100-63105",
    )
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    manifest = service.store.get("demo")
    manifest.last_status = "running"
    service.store.save(manifest)
    old_versions = {"claude": "1", "codex": "2", "gemini": "3", "opencode": "4"}
    latest = {"claude": "1", "codex": "2.1", "gemini": "3", "opencode": "4"}
    toady_base.write_base_metadata(
        service._base_metadata_path(),
        base=service.base,
        source_image="node:test",
        versions=old_versions,
    )
    calls = []

    class FakeRuntime:
        async def ensure_installed(self):
            calls.append(("ensure",))

        async def prepared_base_exists(self, base):
            calls.append(("exists", base))
            return True

        async def stop(self, name):
            calls.append(("stop", name))

        async def remove(self, name):
            calls.append(("remove", name))

    async def fake_prepare(_runtime, _spec, **kwargs):
        calls.append(("prepare", kwargs["dependency_versions"]))
        toady_base.write_base_metadata(
            kwargs["metadata_path"],
            base=service.base,
            source_image="node:test",
            versions=kwargs["dependency_versions"],
        )

    async def fake_start(slug):
        calls.append(("start", slug))
        refreshed = service.store.get(slug)
        refreshed.last_status = "running"
        return service.store.save(refreshed).to_dict()

    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr("server.sandboxes.toady_base.latest_agent_cli_versions", lambda: latest)
    monkeypatch.setattr("server.sandboxes.toady_base.prepare_base", fake_prepare)
    monkeypatch.setattr(service, "start", fake_start)

    result = asyncio.run(service.refresh_runtime_dependencies("demo"))

    assert result["updated"] is True
    assert result["restarted"] is True
    assert result["rebuilt_base"] is True
    assert result["sandbox"]["last_status"] == "running"
    assert result["sandbox"]["runtime_versions"] == latest
    assert calls == [
        ("ensure",),
        ("exists", service.base),
        ("prepare", latest),
        ("stop", "demo"),
        ("remove", "demo"),
        ("start", "demo"),
    ]


def test_sandbox_service_destroy_retries_remove_while_stopping(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = SandboxService(home=str(tmp_path / "state"), browse_roots=[str(tmp_path)])
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    calls = {"remove": 0, "stop": 0}

    class FakeRuntime:
        async def stop(self, _name):
            calls["stop"] += 1

        async def remove(self, _name):
            calls["remove"] += 1
            if calls["remove"] == 1:
                raise RuntimeError("sandbox still running")

    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)

    deleted = asyncio.run(service.destroy("demo", purge_home=False))

    assert deleted is True
    assert calls["remove"] == 2
    assert calls["stop"] == 2
    assert service.get("demo") is None


def test_sandbox_service_stop_running_stops_only_active_manifests(tmp_path, monkeypatch):
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    stopped = []

    class FakeRuntime:
        async def exists(self, _slug):
            return False

        async def stop(self, slug):
            stopped.append(slug)

    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)
    service = SandboxService(home=str(tmp_path / "state"), browse_roots=[str(tmp_path)])
    service.create_manifest({"name": "active", "workspace_root": str(first_workspace)})
    service.create_manifest({"name": "idle", "workspace_root": str(second_workspace)})
    active = service.store.get("active")
    active.last_status = "running"
    service.store.save(active)

    result = asyncio.run(service.stop_running())

    assert [item["slug"] for item in result] == ["active"]
    assert stopped == ["active"]
    assert service.store.get("active").last_status == "stopped"
    assert service.store.get("idle").last_status == "configured"
