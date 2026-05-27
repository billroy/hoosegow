"""Tests for workspace manager project registry lifecycle."""

import json
import os
import shutil

from server.persistence import read_json, write_json
from server.workspace_manager import REGISTRY_VERSION, WorkspaceManager, resolve_project_path


def test_remove_then_readd_path_preserves_workspace_data(tmp_path):
    global_dir = tmp_path / "global"
    workspace = tmp_path / "project-a"
    workspace.mkdir(parents=True, exist_ok=True)

    manager = WorkspaceManager(global_dir=str(global_dir))
    ws_id = manager.register_project(str(workspace), name="Project A")
    bp_dir = manager.get_bp_dir(ws_id)

    # Seed project-specific data and one ticket file.
    config_path = os.path.join(bp_dir, "config.json")
    config = read_json(config_path)
    config["theme"] = "nord"
    write_json(config_path, config)

    tasks_dir = os.path.join(bp_dir, "tasks")
    os.makedirs(tasks_dir, exist_ok=True)
    task_path = os.path.join(tasks_dir, "persist-me.md")
    with open(task_path, "w", encoding="utf-8") as f:
        f.write("---\ntitle: Persist me\nstatus: inbox\n---\n\nBody.\n")

    manager.remove_project(ws_id)
    assert os.path.exists(task_path)

    ws_id_2 = manager.register_project(str(workspace), name="Project A")
    bp_dir_2 = manager.get_bp_dir(ws_id_2)

    # Re-registering the same path points back to the same .bullpen data.
    assert bp_dir_2 == bp_dir
    assert os.path.exists(task_path)
    assert read_json(config_path)["theme"] == "nord"


def test_registry_preserves_entries_with_missing_paths(tmp_path):
    """A project whose directory is temporarily missing must NOT be deleted
    from the registry on manager startup. Re-instantiating the manager has
    historically pruned such entries silently, destroying user data when a
    path was unmounted/renamed/on an external volume at startup."""
    global_dir = tmp_path / "global"
    available = tmp_path / "available"
    missing = tmp_path / "missing"
    available.mkdir()
    missing.mkdir()

    manager = WorkspaceManager(global_dir=str(global_dir))
    available_id = manager.register_project(str(available), name="available")
    missing_id = manager.register_project(str(missing), name="missing")

    # Simulate the directory disappearing between sessions
    # (rename, unmount, network drive offline, etc.).
    shutil.rmtree(missing)
    assert not os.path.isdir(missing)

    # New manager instance, as on server restart.
    manager2 = WorkspaceManager(global_dir=str(global_dir))
    ids = {e["id"] for e in manager2.list_projects()}
    assert available_id in ids
    assert missing_id in ids, "missing-path entry was silently pruned"

    # And the on-disk registry must still contain both entries.
    with open(global_dir / "projects.json") as f:
        raw = json.load(f)
    on_disk = raw["projects"] if isinstance(raw, dict) else raw
    assert {e["id"] for e in on_disk} == {available_id, missing_id}


def test_manager_init_does_not_rewrite_registry(tmp_path):
    """Instantiating WorkspaceManager must not modify projects.json. A
    write-on-init is what made the data loss permanent — by the time the
    user noticed, the prior registry was gone."""
    global_dir = tmp_path / "global"
    workspace = tmp_path / "p"
    workspace.mkdir()

    m = WorkspaceManager(global_dir=str(global_dir))
    m.register_project(str(workspace), name="p")
    registry_path = global_dir / "projects.json"
    mtime_before = registry_path.stat().st_mtime_ns

    # Re-instantiate; should be a pure read.
    WorkspaceManager(global_dir=str(global_dir))
    assert registry_path.stat().st_mtime_ns == mtime_before


# --- Atomic + backup write tests ---


def test_save_creates_backup(tmp_path):
    """Each save should produce a .bak copy of the previous state."""
    global_dir = tmp_path / "global"
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    ws1.mkdir()
    ws2.mkdir()

    m = WorkspaceManager(global_dir=str(global_dir))
    m.register_project(str(ws1), name="first")

    registry_path = global_dir / "projects.json"
    bak_path = global_dir / "projects.json.bak"

    # After first register, no backup yet (there was no previous file).
    # Actually first register creates the file — second save creates the backup.
    snapshot_after_first = registry_path.read_text()

    m.register_project(str(ws2), name="second")
    assert bak_path.exists()
    bak_content = json.loads(bak_path.read_text())
    # Backup should contain only the first project.
    if "projects" in bak_content:
        assert len(bak_content["projects"]) == 1
    else:
        assert len(bak_content) == 1


def test_corrupt_registry_falls_back_to_backup(tmp_path):
    """If projects.json is corrupt, load from .bak."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()

    m = WorkspaceManager(global_dir=str(global_dir))
    m.register_project(str(ws), name="proj")

    registry_path = global_dir / "projects.json"
    bak_path = global_dir / "projects.json.bak"

    # Write a valid backup, then corrupt the main file.
    shutil.copy2(str(registry_path), str(bak_path))
    registry_path.write_text("{corrupt json!!")

    m2 = WorkspaceManager(global_dir=str(global_dir))
    projects = m2.list_projects()
    assert len(projects) == 1
    assert projects[0]["name"] == "proj"


def test_empty_registry_falls_back_to_backup(tmp_path):
    """An empty (zero-byte) projects.json should fall back to backup."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()

    m = WorkspaceManager(global_dir=str(global_dir))
    m.register_project(str(ws), name="proj")

    registry_path = global_dir / "projects.json"
    bak_path = global_dir / "projects.json.bak"

    shutil.copy2(str(registry_path), str(bak_path))
    registry_path.write_text("")

    m2 = WorkspaceManager(global_dir=str(global_dir))
    projects = m2.list_projects()
    assert len(projects) == 1


# --- Versioned envelope tests ---


def test_registry_uses_versioned_envelope(tmp_path):
    """Saved registry should use {version, projects} envelope."""
    global_dir = tmp_path / "global"
    ws = tmp_path / "ws"
    ws.mkdir()

    m = WorkspaceManager(global_dir=str(global_dir))
    m.register_project(str(ws), name="proj")

    registry_path = global_dir / "projects.json"
    data = json.loads(registry_path.read_text())
    assert "version" in data
    assert data["version"] == REGISTRY_VERSION
    assert "projects" in data
    assert isinstance(data["projects"], list)
    assert len(data["projects"]) == 1


def test_load_legacy_bare_list(tmp_path):
    """Manager should still load a legacy bare-list projects.json."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()

    registry_path = global_dir / "projects.json"
    ws = tmp_path / "ws"
    ws.mkdir()
    legacy = [{"id": "abc-123", "path": str(ws), "name": "legacy"}]
    registry_path.write_text(json.dumps(legacy))

    m = WorkspaceManager(global_dir=str(global_dir))
    projects = m.list_projects()
    assert len(projects) == 1
    assert projects[0]["name"] == "legacy"


def test_refuses_newer_version(tmp_path):
    """Manager must refuse to load a registry with a higher version."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()

    registry_path = global_dir / "projects.json"
    future = {"version": REGISTRY_VERSION + 1, "projects": []}
    registry_path.write_text(json.dumps(future))

    import pytest
    with pytest.raises(RuntimeError, match="newer than supported"):
        WorkspaceManager(global_dir=str(global_dir))


# --- Availability flag tests ---


def test_list_projects_includes_availability(tmp_path):
    """list_projects should flag missing directories as unavailable."""
    global_dir = tmp_path / "global"
    available = tmp_path / "available"
    missing = tmp_path / "missing"
    available.mkdir()
    missing.mkdir()

    m = WorkspaceManager(global_dir=str(global_dir))
    m.register_project(str(available), name="available")
    m.register_project(str(missing), name="missing")

    shutil.rmtree(missing)

    projects = m.list_projects()
    by_name = {p["name"]: p for p in projects}
    assert by_name["available"]["available"] is True
    assert by_name["missing"]["available"] is False


def test_unavailable_project_stays_in_registry_across_restarts(tmp_path):
    """Unavailable projects must survive manager restarts."""
    global_dir = tmp_path / "global"
    ws = tmp_path / "ws"
    ws.mkdir()

    m = WorkspaceManager(global_dir=str(global_dir))
    ws_id = m.register_project(str(ws), name="ws")
    shutil.rmtree(ws)

    m2 = WorkspaceManager(global_dir=str(global_dir))
    projects = m2.list_projects()
    assert len(projects) == 1
    assert projects[0]["id"] == ws_id
    assert projects[0]["available"] is False

    # Restore directory — should become available again.
    ws.mkdir()
    m3 = WorkspaceManager(global_dir=str(global_dir))
    projects = m3.list_projects()
    assert projects[0]["available"] is True


def test_list_visible_projects_can_hide_unavailable_entries(tmp_path, monkeypatch):
    global_dir = tmp_path / "global"
    available = tmp_path / "available"
    missing = tmp_path / "missing"
    available.mkdir()
    missing.mkdir()

    m = WorkspaceManager(global_dir=str(global_dir))
    m.register_project(str(available), name="available")
    m.register_project(str(missing), name="missing")
    shutil.rmtree(missing)

    monkeypatch.setenv("BULLPEN_HIDE_UNAVAILABLE_PROJECTS", "1")

    projects = m.list_visible_projects()
    assert len(projects) == 1
    assert projects[0]["name"] == "available"
    assert projects[0]["available"] is True


def test_register_project_resolves_name_under_configured_projects_root(tmp_path, monkeypatch):
    global_dir = tmp_path / "global"
    projects_root = tmp_path / "projects"
    project = projects_root / "alpha"
    project.mkdir(parents=True)
    monkeypatch.setenv("BULLPEN_PROJECTS_ROOT", str(projects_root))

    m = WorkspaceManager(global_dir=str(global_dir))
    ws_id = m.register_project("alpha")

    assert resolve_project_path("alpha") == str(project)
    assert m.get_workspace_path(ws_id) == str(project.resolve())
