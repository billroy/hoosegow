from pathlib import Path

import pytest

from server.sandboxes import SandboxService
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
