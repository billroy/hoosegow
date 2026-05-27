"""Tests for worker type normalization and storage helpers."""

import os

from server.init import init_workspace
from server.persistence import read_json, write_json
from server.teams import load_team, save_team
from server.transfer import transfer_worker
from server.validation import validate_worker_configure
from server.worker_types import (
    ViewerContext,
    copy_worker_slot,
    get_worker_type,
    normalize_layout,
    normalize_worker_slot,
    serialize_worker_slot,
)
from server.workspace_manager import WorkspaceManager


def test_legacy_ai_slot_defaults_to_ai_and_saves_type(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    config = read_json(os.path.join(bp_dir, "config.json"))

    slot = normalize_worker_slot(
        {
            "row": 0,
            "col": 0,
            "name": "Legacy",
            "agent": "mock",
            "model": "mock-model",
        },
        index=0,
        config=config,
    )

    assert slot["type"] == "ai"
    assert slot["agent"] == "mock"
    assert slot["model"] == "mock-model"

    layout = normalize_layout({"slots": [slot]}, config=config)
    write_json(os.path.join(bp_dir, "layout.json"), layout)

    saved = read_json(os.path.join(bp_dir, "layout.json"))
    assert saved["slots"][0]["type"] == "ai"


def test_unknown_worker_type_preserves_unknown_fields_through_configure_and_copy(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    config = read_json(os.path.join(bp_dir, "config.json"))
    raw = {
        "type": "acme-widget",
        "row": 1,
        "col": 2,
        "name": "Mystery",
        "activation": "manual",
        "state": "working",
        "task_queue": ["ticket-1"],
        "last_trigger_time": 123,
        "paused": True,
        "custom_payload": {"keep": ["me"]},
    }

    slot = normalize_worker_slot(raw, index=3, config=config)
    assert slot["type"] == "acme-widget"
    assert slot["custom_payload"] == {"keep": ["me"]}

    _, fields = validate_worker_configure({
        "slot": 3,
        "fields": {
            "type": "acme-widget",
            "custom_payload": {"changed": True},
            "command": "should round trip even on unknown",
        },
    }, max_slots=200)
    slot.update(fields)
    slot = normalize_worker_slot(slot, index=3, config=config)

    assert slot["custom_payload"] == {"changed": True}
    assert slot["command"] == "should round trip even on unknown"

    clone = copy_worker_slot(slot, reset_runtime=True)
    assert clone["type"] == "acme-widget"
    assert clone["custom_payload"] == {"changed": True}
    assert clone["task_queue"] == []
    assert clone["state"] == "idle"
    assert clone["last_trigger_time"] is None
    assert clone["paused"] is False


def test_normalize_layout_preserves_interior_holes_but_trims_trailing_empty_slots(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    config = read_json(os.path.join(bp_dir, "config.json"))

    layout = normalize_layout({
        "slots": [
            {
                "type": "marker",
                "row": 0,
                "col": 0,
                "name": "Anchor",
            },
            None,
            {
                "type": "marker",
                "row": 3,
                "col": 1,
                "name": "Tail",
            },
            None,
            None,
        ]
    }, config=config)

    assert len(layout["slots"]) == 3
    assert layout["slots"][0]["name"] == "Anchor"
    assert layout["slots"][1] is None
    assert layout["slots"][2]["name"] == "Tail"


def test_shell_slot_round_trips_through_team_save_load(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    shell = {
        "type": "shell",
        "row": 0,
        "col": 0,
        "name": "Shell Gate",
        "command": "python3 scripts/check.py",
        "env": [{"key": "FOO", "value": "bar"}],
        "cwd": "tools",
        "timeout_seconds": 90,
        "ticket_delivery": "env-vars",
        "task_queue": ["running-ticket"],
        "state": "working",
    }
    layout = normalize_layout({"slots": [shell]}, config=read_json(os.path.join(bp_dir, "config.json")))

    saved = save_team(bp_dir, "shellteam", layout)
    loaded = load_team(bp_dir, "shellteam")

    assert saved["slots"][0]["type"] == "shell"
    assert saved["slots"][0]["command"] == "python3 scripts/check.py"
    assert saved["slots"][0]["env"] == [{"key": "FOO", "value": "bar"}]
    assert "task_queue" not in saved["slots"][0]
    assert loaded["slots"][0]["type"] == "shell"
    assert loaded["slots"][0]["command"] == "python3 scripts/check.py"
    assert loaded["slots"][0]["task_queue"] == []
    assert loaded["slots"][0]["state"] == "idle"


def test_service_slot_normalizes_defaults_and_round_trips_through_team(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    service = {
        "type": "service",
        "row": 0,
        "col": 0,
        "name": "Preview Server",
        "command": "python3 app.py",
        "pre_start": "git fetch",
        "env": [{"key": "HOSTED_PORT", "value": "3000"}],
        "health_type": "http",
        "health_url": "http://localhost:3000/health",
        "task_queue": ["running-ticket"],
        "state": "working",
    }
    config = read_json(os.path.join(bp_dir, "config.json"))
    layout = normalize_layout({"slots": [service]}, config=config)
    slot = layout["slots"][0]

    assert slot["type"] == "service"
    assert slot["activation"] == "manual"
    assert slot["ticket_action"] == "start-if-stopped-else-restart"
    assert slot["startup_grace_seconds"] == 2
    assert slot["startup_timeout_seconds"] == 60
    assert slot["health_interval_seconds"] == 5
    assert slot["health_timeout_seconds"] == 2
    assert slot["health_failure_threshold"] == 3
    assert slot["on_crash"] == "stay-crashed"
    assert slot["stop_timeout_seconds"] == 5
    assert slot["log_max_bytes"] == 5 * 1024 * 1024

    saved = save_team(bp_dir, "serviceteam", layout)
    loaded = load_team(bp_dir, "serviceteam")

    assert saved["slots"][0]["type"] == "service"
    assert saved["slots"][0]["command"] == "python3 app.py"
    assert saved["slots"][0]["pre_start"] == "git fetch"
    assert saved["slots"][0]["env"] == [{"key": "HOSTED_PORT", "value": "3000"}]
    assert "task_queue" not in saved["slots"][0]
    assert loaded["slots"][0]["type"] == "service"
    assert loaded["slots"][0]["task_queue"] == []
    assert loaded["slots"][0]["state"] == "idle"


def test_marker_slot_normalizes_defaults_and_resets_runtime_on_copy(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    config = read_json(os.path.join(bp_dir, "config.json"))
    slot = normalize_worker_slot(
        {
            "type": "marker",
            "row": 0,
            "col": 0,
            "name": "Deploy Marker",
            "note": "staging + release path",
            "activation": "manual",
            "disposition": "worker:Deploy Worker",
            "task_queue": ["ticket-1"],
            "state": "working",
        },
        index=0,
        config=config,
    )

    assert slot["type"] == "marker"
    assert slot["note"] == "staging + release path"
    assert slot["icon"] == "square-dot"
    assert slot["color"] == "marker"
    assert slot["max_retries"] == 0

    clone = copy_worker_slot(slot, reset_runtime=True)
    assert clone["type"] == "marker"
    assert clone["note"] == "staging + release path"
    assert clone["task_queue"] == []
    assert clone["state"] == "idle"
    assert clone["paused"] is False


def test_marker_slot_preserves_color_override(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    config = read_json(os.path.join(bp_dir, "config.json"))
    slot = normalize_worker_slot(
        {
            "type": "marker",
            "row": 0,
            "col": 0,
            "name": "Colored Marker",
            "color": "#3a7bd5",
        },
        index=0,
        config=config,
    )

    assert slot["color"] == "#3a7bd5"


def test_unknown_worker_type_transfer_preserves_fields(tmp_path):
    ws_a = str(tmp_path / "a")
    ws_b = str(tmp_path / "b")
    os.makedirs(ws_a)
    os.makedirs(ws_b)
    manager = WorkspaceManager(global_dir=str(tmp_path / "global"))
    id_a = manager.register_project(ws_a, name="A")
    id_b = manager.register_project(ws_b, name="B")

    bp_a = manager.get_bp_dir(id_a)
    layout = read_json(os.path.join(bp_a, "layout.json"))
    layout["slots"] = [{
        "type": "vendor-special",
        "row": 0,
        "col": 0,
        "name": "Special",
        "custom_field": {"nested": True},
        "state": "idle",
        "task_queue": [],
    }]
    write_json(os.path.join(bp_a, "layout.json"), layout)

    result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

    bp_b = manager.get_bp_dir(id_b)
    dest = read_json(os.path.join(bp_b, "layout.json"))["slots"][result["dest_slot"]]
    assert dest["type"] == "vendor-special"
    assert dest["custom_field"] == {"nested": True}
    assert dest["state"] == "idle"
    assert dest["task_queue"] == []


def test_shell_read_only_serialization_redacts_command_and_env_values(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    config = read_json(os.path.join(bp_dir, "config.json"))
    slot = normalize_worker_slot(
        {
            "type": "shell",
            "row": 0,
            "col": 0,
            "name": "Secret Shell",
            "command": "curl -H 'Authorization: secret'",
            "env": [{"key": "TOKEN", "value": "super-secret"}],
        },
        index=0,
        config=config,
    )

    editable = serialize_worker_slot(slot, viewer=ViewerContext(can_edit=True))
    read_only = serialize_worker_slot(slot, viewer=ViewerContext(can_edit=False))

    assert editable["command"].startswith("curl")
    assert editable["env"] == [{"key": "TOKEN", "value": "super-secret"}]
    assert read_only["command"] == "<redacted>"
    assert read_only["env"] == [{"key": "TOKEN", "value": "<redacted>"}]


def test_service_read_only_serialization_redacts_plaintext_command_fields(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    config = read_json(os.path.join(bp_dir, "config.json"))
    slot = normalize_worker_slot(
        {
            "type": "service",
            "row": 0,
            "col": 0,
            "name": "Secret Service",
            "command": "python3 app.py --token secret",
            "pre_start": "echo secret",
            "health_type": "shell",
            "health_command": "curl -H 'Authorization: secret'",
            "env": [{"key": "TOKEN", "value": "super-secret"}],
        },
        index=0,
        config=config,
    )

    editable = serialize_worker_slot(slot, viewer=ViewerContext(can_edit=True))
    read_only = serialize_worker_slot(slot, viewer=ViewerContext(can_edit=False))

    assert editable["command"].startswith("python3")
    assert editable["pre_start"] == "echo secret"
    assert editable["health_command"].startswith("curl")
    assert editable["env"] == [{"key": "TOKEN", "value": "super-secret"}]
    assert read_only["command"] == "<redacted>"
    assert read_only["pre_start"] == "<redacted>"
    assert read_only["health_command"] == "<redacted>"
    assert read_only["env"] == [{"key": "TOKEN", "value": "<redacted>"}]


def test_service_worker_procfile_mode_defaults_and_validates(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    config = read_json(os.path.join(bp_dir, "config.json"))
    slot = normalize_worker_slot(
        {
            "type": "service",
            "row": 0,
            "col": 0,
            "name": "Procfile Service",
            "command_source": "procfile",
            "procfile_process": "",
            "port": "3000",
        },
        index=0,
        config=config,
    )

    assert slot["command_source"] == "procfile"
    assert slot["procfile_process"] == "web"
    assert slot["port"] == 3000
    assert get_worker_type("service").validate_config(slot) == []


def test_service_worker_transfer_preserves_service_fields(tmp_path):
    ws_a = str(tmp_path / "a")
    ws_b = str(tmp_path / "b")
    os.makedirs(ws_a)
    os.makedirs(ws_b)
    manager = WorkspaceManager(global_dir=str(tmp_path / "global"))
    id_a = manager.register_project(ws_a, name="A")
    id_b = manager.register_project(ws_b, name="B")

    bp_a = manager.get_bp_dir(id_a)
    layout = read_json(os.path.join(bp_a, "layout.json"))
    layout["slots"] = [{
        "type": "service",
        "row": 0,
        "col": 0,
        "name": "Preview Server",
        "command": "python3 app.py",
        "command_source": "procfile",
        "procfile_process": "web",
        "port": 3000,
        "pre_start": "git fetch",
        "ticket_action": "restart",
        "health_type": "http",
        "health_url": "http://localhost:3000/health",
        "env": [{"key": "HOSTED_PORT", "value": "3000"}],
        "state": "idle",
        "task_queue": [],
    }]
    write_json(os.path.join(bp_a, "layout.json"), layout)

    result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

    bp_b = manager.get_bp_dir(id_b)
    dest = read_json(os.path.join(bp_b, "layout.json"))["slots"][result["dest_slot"]]
    assert dest["type"] == "service"
    assert dest["command"] == "python3 app.py"
    assert dest["command_source"] == "procfile"
    assert dest["procfile_process"] == "web"
    assert dest["port"] == 3000
    assert dest["pre_start"] == "git fetch"
    assert dest["ticket_action"] == "restart"
    assert dest["health_url"] == "http://localhost:3000/health"
    assert dest["env"] == [{"key": "HOSTED_PORT", "value": "3000"}]
    assert dest["state"] == "idle"
    assert dest["task_queue"] == []
