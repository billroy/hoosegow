import asyncio
from pathlib import Path

import pytest

from server.sandboxes import SandboxService, SandboxServiceError
from server.toady_validation import ValidationError, validate_slug, validate_workspace_path


def test_validate_slug_rejects_bullpen_style_ids():
    assert validate_slug("demo-1") == "demo-1"
    with pytest.raises(ValidationError):
        validate_slug("Bad Name")


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
