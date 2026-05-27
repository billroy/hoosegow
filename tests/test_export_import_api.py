"""Tests for workspace zip export/import endpoints."""

import io
import json
import os
import zipfile

from server.app import (
    _MAX_IMPORT_ARCHIVE_FILES,
    _MAX_IMPORT_COMPRESSION_RATIO,
    create_app,
)
from server.init import init_workspace
from server import mcp_auth
from server.persistence import read_json, write_json


def _zip_bytes(entries):
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, data in entries.items():
            zf.writestr(path, data)
    mem.seek(0)
    return mem


def test_export_workspace_returns_zip_with_bullpen_dir(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()

    resp = client.get("/api/export/workspace")
    assert resp.status_code == 200
    assert resp.headers.get("Content-Type", "").startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(resp.data), "r") as zf:
        names = set(zf.namelist())
        exported_config = json.loads(zf.read(".bullpen/config.json"))
    assert ".bullpen/config.json" in names
    assert ".bullpen/layout.json" in names
    live_config = read_json(os.path.join(bp_dir, "config.json"))
    assert "server_host" in live_config
    assert "server_port" in live_config
    assert "mcp_token" not in live_config
    assert mcp_auth.read_workspace_mcp_token(bp_dir)
    assert "server_host" not in exported_config
    assert "server_port" not in exported_config
    assert "mcp_token" not in exported_config


def test_import_workspace_replaces_config_from_zip(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()

    original = read_json(os.path.join(bp_dir, "config.json"))
    original_token = mcp_auth.read_workspace_mcp_token(bp_dir)
    assert original["name"] == "Bullpen"

    payload = {
        ".bullpen/config.json": json.dumps({**original, "name": "Imported Workspace"}),
    }
    archive = _zip_bytes(payload)

    resp = client.post(
        "/api/import/workspace",
        data={"file": (archive, "workspace.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    config = read_json(os.path.join(bp_dir, "config.json"))
    assert config["name"] == "Imported Workspace"
    assert config["server_host"] == app.config["host"]
    assert config["server_port"] == app.config["port"]
    assert "mcp_token" not in config
    assert mcp_auth.read_workspace_mcp_token(bp_dir) == original_token


def test_export_all_and_import_all_round_trip(tmp_workspace):
    bp1 = init_workspace(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    manager = app.config["manager"]

    ws2 = os.path.join(tmp_workspace, "workspace-two")
    os.makedirs(ws2, exist_ok=True)
    bp2 = init_workspace(ws2)
    ws2_id = manager.register_project(ws2, name="workspace-two")
    ws1_id = app.config["startup_workspace_id"]
    mcp_auth.ensure_workspace_runtime_config(bp2, host=app.config["host"], port=app.config["port"])

    write_json(
        os.path.join(bp1, "config.json"),
        {
            "name": "One",
            "columns": [],
            "grid": {"rows": 4, "cols": 6},
            "server_host": app.config["host"],
            "server_port": app.config["port"],
        },
    )
    write_json(
        os.path.join(bp2, "config.json"),
        {
            "name": "Two",
            "columns": [],
            "grid": {"rows": 4, "cols": 6},
            "server_host": app.config["host"],
            "server_port": app.config["port"],
        },
    )
    token_one = mcp_auth.read_workspace_mcp_token(bp1)
    token_two = mcp_auth.read_workspace_mcp_token(bp2)

    client = app.test_client()
    export_resp = client.get("/api/export/all")
    assert export_resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(export_resp.data), "r") as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("bullpen-export.json"))
        exported_one = json.loads(zf.read(f"workspaces/{ws1_id}/.bullpen/config.json"))
        exported_two = json.loads(zf.read(f"workspaces/{ws2_id}/.bullpen/config.json"))
    assert f"workspaces/{ws1_id}/.bullpen/config.json" in names
    assert f"workspaces/{ws2_id}/.bullpen/config.json" in names
    for ws in manifest["workspaces"]:
        assert "path" not in ws
    for exported in (exported_one, exported_two):
        assert "server_host" not in exported
        assert "server_port" not in exported
        assert "mcp_token" not in exported

    import_archive = _zip_bytes({
        f"workspaces/{ws1_id}/.bullpen/config.json": json.dumps({"name": "Imported One", "columns": [], "grid": {"rows": 4, "cols": 6}}),
        f"workspaces/{ws2_id}/.bullpen/config.json": json.dumps({"name": "Imported Two", "columns": [], "grid": {"rows": 4, "cols": 6}}),
    })
    import_resp = client.post(
        "/api/import/all",
        data={"file": (import_archive, "all.zip")},
        content_type="multipart/form-data",
    )
    assert import_resp.status_code == 200
    assert import_resp.get_json()["imported"] == 2
    config_one = read_json(os.path.join(bp1, "config.json"))
    config_two = read_json(os.path.join(bp2, "config.json"))
    assert config_one["name"] == "Imported One"
    assert config_two["name"] == "Imported Two"
    for config in (config_one, config_two):
        assert config["server_host"] == app.config["host"]
        assert config["server_port"] == app.config["port"]
        assert "mcp_token" not in config
    assert mcp_auth.read_workspace_mcp_token(bp1) == token_one
    assert mcp_auth.read_workspace_mcp_token(bp2) == token_two


def test_export_workers_returns_workers_payload(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()

    write_json(
        os.path.join(bp_dir, "layout.json"),
        {
            "slots": [
                {
                    "name": "Builder",
                    "profile": "custom-worker",
                    "state": "idle",
                    "task_queue": [],
                }
            ]
        },
    )
    write_json(
        os.path.join(bp_dir, "profiles", "custom-worker.json"),
        {"id": "custom-worker", "name": "Custom Worker"},
    )

    resp = client.get("/api/export/workers")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.data), "r") as zf:
        names = set(zf.namelist())
        exported_layout = json.loads(zf.read(".bullpen/layout.json"))
        manifest = json.loads(zf.read("bullpen-workers-export.json"))
    assert ".bullpen/layout.json" in names
    assert ".bullpen/config.json" not in names
    assert ".bullpen/profiles/custom-worker.json" in names
    assert exported_layout["slots"][0]["name"] == "Builder"
    assert "path" not in manifest["workspace"]


def test_export_single_worker_returns_selected_worker_payload(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()

    write_json(
        os.path.join(bp_dir, "layout.json"),
        {
            "slots": [
                {
                    "name": "Builder",
                    "profile": "custom-worker",
                    "state": "idle",
                    "task_queue": [],
                },
                {
                    "name": "Reviewer",
                    "profile": "review-worker",
                    "state": "idle",
                    "task_queue": [],
                },
            ]
        },
    )
    write_json(
        os.path.join(bp_dir, "profiles", "custom-worker.json"),
        {"id": "custom-worker", "name": "Custom Worker"},
    )
    write_json(
        os.path.join(bp_dir, "profiles", "review-worker.json"),
        {"id": "review-worker", "name": "Review Worker"},
    )

    resp = client.get("/api/export/worker?slot=1")
    assert resp.status_code == 200
    assert "bullpen-worker-Reviewer-" in resp.headers.get("Content-Disposition", "")
    with zipfile.ZipFile(io.BytesIO(resp.data), "r") as zf:
        names = set(zf.namelist())
        exported_layout = json.loads(zf.read(".bullpen/layout.json"))
        manifest = json.loads(zf.read("bullpen-workers-export.json"))
    assert len(exported_layout["slots"]) == 1
    assert exported_layout["slots"][0]["name"] == "Reviewer"
    assert exported_layout["slots"][0]["profile"] == "review-worker"
    assert exported_layout["slots"][0]["state"] == "idle"
    assert exported_layout["slots"][0]["task_queue"] == []
    assert ".bullpen/profiles/review-worker.json" in names
    assert ".bullpen/profiles/custom-worker.json" not in names
    assert manifest["selection"] == {"slot": 1, "count": 1}


def test_export_single_worker_rejects_unknown_slot(tmp_workspace):
    init_workspace(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()

    resp = client.get("/api/export/worker?slot=99")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Unknown worker slot"


def test_import_workers_merges_layout_from_zip_without_overwriting_existing_workers(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()
    original_token = mcp_auth.read_workspace_mcp_token(bp_dir)

    write_json(
        os.path.join(bp_dir, "layout.json"),
        {
            "slots": [
                {
                    "name": "Existing Worker",
                    "profile": "existing-profile",
                    "state": "idle",
                    "task_queue": [],
                    "col": 0,
                    "row": 0,
                }
            ]
        },
    )
    archive = _zip_bytes(
        {
            ".bullpen/layout.json": json.dumps(
                {
                    "slots": [
                        {
                            "name": "Imported Worker",
                            "profile": "imported-profile",
                            "state": "idle",
                            "task_queue": [],
                            "col": 0,
                            "row": 0,
                        }
                    ]
                }
            ),
            ".bullpen/profiles/imported-profile.json": json.dumps(
                {"id": "imported-profile", "name": "Imported Profile"}
            ),
        }
    )

    resp = client.post(
        "/api/import/workers",
        data={"file": (archive, "workers.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    layout = read_json(os.path.join(bp_dir, "layout.json"))
    profile = read_json(os.path.join(bp_dir, "profiles", "imported-profile.json"))
    config = read_json(os.path.join(bp_dir, "config.json"))
    workers = [slot for slot in layout["slots"] if isinstance(slot, dict)]
    assert len(workers) == 2
    existing = next(slot for slot in workers if slot["name"] == "Existing Worker")
    imported = next(slot for slot in workers if slot["name"] == "Imported Worker")
    assert (existing["col"], existing["row"]) == (0, 0)
    assert (imported["col"], imported["row"]) == (1, 0)
    assert profile["id"] == "imported-profile"
    assert config["server_host"] == app.config["host"]
    assert config["server_port"] == app.config["port"]
    assert "mcp_token" not in config
    assert mcp_auth.read_workspace_mcp_token(bp_dir) == original_token


def test_import_workers_keeps_pass_connected_group_adjacent_after_translation(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()

    write_json(
        os.path.join(bp_dir, "layout.json"),
        {
            "slots": [
                {
                    "name": "Existing Worker",
                    "profile": "existing-profile",
                    "state": "idle",
                    "task_queue": [],
                    "col": 3,
                    "row": 2,
                }
            ]
        },
    )
    archive = _zip_bytes(
        {
            ".bullpen/layout.json": json.dumps(
                {
                    "slots": [
                        {
                            "name": "Pass Left",
                            "profile": "imported-profile",
                            "state": "idle",
                            "task_queue": [],
                            "disposition": "pass:right",
                            "col": 0,
                            "row": 0,
                        },
                        {
                            "name": "Pass Right",
                            "profile": "imported-profile",
                            "state": "idle",
                            "task_queue": [],
                            "disposition": "pass:left",
                            "col": 1,
                            "row": 0,
                        },
                    ]
                }
            )
        }
    )

    resp = client.post(
        "/api/import/workers",
        data={"file": (archive, "workers.zip")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    layout = read_json(os.path.join(bp_dir, "layout.json"))
    workers = {slot["name"]: slot for slot in layout["slots"] if isinstance(slot, dict)}
    assert (workers["Pass Left"]["col"], workers["Pass Left"]["row"]) == (4, 2)
    assert (workers["Pass Right"]["col"], workers["Pass Right"]["row"]) == (5, 2)


def test_import_workspace_rejects_archive_with_too_many_files(tmp_workspace):
    init_workspace(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()

    payload = {
        ".bullpen/config.json": json.dumps({"name": "Imported Workspace"}),
    }
    for idx in range(_MAX_IMPORT_ARCHIVE_FILES):
        payload[f".bullpen/files/file-{idx}.txt"] = "ok"

    resp = client.post(
        "/api/import/workspace",
        data={"file": (_zip_bytes(payload), "workspace.zip")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Archive contains too many files"


def test_import_workspace_rejects_high_expansion_archive(tmp_workspace):
    init_workspace(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()

    bomb_payload = {
        ".bullpen/config.json": json.dumps({"name": "Imported Workspace"}),
        ".bullpen/payload.txt": "A" * (_MAX_IMPORT_COMPRESSION_RATIO * 4096),
    }

    resp = client.post(
        "/api/import/workspace",
        data={"file": (_zip_bytes(bomb_payload), "workspace.zip")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Archive contains highly compressed entries"


def test_import_workspace_rejects_nested_archive_files(tmp_workspace):
    init_workspace(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()

    resp = client.post(
        "/api/import/workspace",
        data={
            "file": (
                _zip_bytes(
                    {
                        ".bullpen/config.json": json.dumps({"name": "Imported Workspace"}),
                        ".bullpen/nested/archive.zip": "not really a zip, still blocked",
                    }
                ),
                "workspace.zip",
            )
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Archive contains nested archive files"
