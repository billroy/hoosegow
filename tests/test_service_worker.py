"""Tests for Service worker manual lifecycle."""

import json
import os
import sys
import time

from server.app import create_app
from server.init import init_workspace
from server.persistence import read_json, write_json
from server.tasks import create_task, read_task, update_task
from server.service_worker import (
    get_controller,
    restart_service,
    resolve_service_preview,
    suggest_service_port,
    start_service,
    stop_all_services,
    stop_service,
    tail_service,
)
from server.workers import assign_task, start_worker, yank_from_worker, _load_layout


class FakeSocket:
    def __init__(self):
        self.events = []

    def emit(self, event, payload, to=None):
        self.events.append((event, dict(payload), to))


def _write_script(path, body):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)


def _install_service_worker(bp_dir, workspace, **overrides):
    script = os.path.join(workspace, "service_app.py")
    _write_script(
        script,
        "import time\n"
        "print('service-ready', flush=True)\n"
        "time.sleep(30)\n",
    )
    worker = {
        "type": "service",
        "row": 0,
        "col": 0,
        "name": "Preview Server",
        "command": f'"{sys.executable}" "{script}"',
        "activation": "on_drop",
        "disposition": "review",
        "startup_grace_seconds": 0,
        "startup_timeout_seconds": 5,
        "stop_timeout_seconds": 1,
        "task_queue": [],
        "state": "idle",
    }
    worker.update(overrides)
    layout = read_json(os.path.join(bp_dir, "layout.json"))
    layout["slots"] = [worker]
    write_json(os.path.join(bp_dir, "layout.json"), layout)
    return worker


def _wait_for(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    return None


def test_service_manual_start_stop_and_tail(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    _install_service_worker(bp_dir, tmp_workspace)
    socket = FakeSocket()
    ws_id = "ws-service"

    assert start_service(bp_dir, ws_id, 0, socket) is True
    assert start_service(bp_dir, ws_id, 0, socket) is False
    controller = get_controller(bp_dir, ws_id, 0, socket)
    running = _wait_for(lambda: controller.state_snapshot()["state"] == "running")
    assert running is True
    assert controller.state_snapshot()["pid"]

    log_seen = _wait_for(lambda: any(
        event == "service:log" and "service-ready" in "\n".join(payload.get("lines", []))
        for event, payload, _ in socket.events
    ))
    assert log_seen is True

    tail_service(bp_dir, ws_id, 0, socket, max_bytes=65536)
    catchup = [payload for event, payload, _ in socket.events if event == "service:log" and payload.get("catchup")]
    assert catchup
    assert catchup[-1]["reset"] is True
    assert "service-ready" in "\n".join(catchup[-1]["lines"])

    assert stop_service(bp_dir, ws_id, 0, socket) is True
    stopped = _wait_for(lambda: controller.state_snapshot()["state"] == "stopped")
    assert stopped is True
    assert controller.state_snapshot()["pid"] is None


def test_service_restart_replaces_process(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    _install_service_worker(bp_dir, tmp_workspace)
    socket = FakeSocket()
    ws_id = "ws-service-restart"

    start_service(bp_dir, ws_id, 0, socket)
    controller = get_controller(bp_dir, ws_id, 0, socket)
    assert _wait_for(lambda: controller.state_snapshot()["state"] == "running") is True
    first_pid = controller.state_snapshot()["pid"]

    assert restart_service(bp_dir, ws_id, 0, socket) is True
    assert _wait_for(lambda: controller.state_snapshot()["state"] == "running" and controller.state_snapshot()["pid"] != first_pid) is True
    assert controller.state_snapshot()["pid"] != first_pid

    stop_service(bp_dir, ws_id, 0, socket)


def test_paused_service_worker_blocks_direct_start_and_restart(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    _install_service_worker(bp_dir, tmp_workspace, paused=True)
    socket = FakeSocket()
    ws_id = "ws-service-paused"

    assert start_service(bp_dir, ws_id, 0, socket) is False
    assert restart_service(bp_dir, ws_id, 0, socket) is False
    assert any(
        event == "toast" and "worker paused" in payload.get("message", "")
        for event, payload, _ in socket.events
    )


def test_service_pre_start_failure_crashes_without_main_process(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    _install_service_worker(bp_dir, tmp_workspace, pre_start=f'"{sys.executable}" -c "import sys; sys.exit(3)"')
    socket = FakeSocket()
    ws_id = "ws-service-prestart"

    start_service(bp_dir, ws_id, 0, socket)
    controller = get_controller(bp_dir, ws_id, 0, socket)
    assert _wait_for(lambda: controller.state_snapshot()["state"] == "crashed") is True
    snapshot = controller.state_snapshot()
    assert snapshot["pid"] is None
    assert "Pre-start exited" in snapshot["last_error"]


def test_service_ticket_order_routes_and_injects_ticket_env(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    output_path = os.path.join(tmp_workspace, "service-env.json")
    script = os.path.join(tmp_workspace, "ticket_service.py")
    _write_script(
        script,
        "import json, os, time\n"
        f"out = {output_path!r}\n"
        "data = {key: os.environ.get(key, '') for key in [\n"
        "    'BULLPEN_SERVICE_ORDER_ID', 'BULLPEN_SERVICE_COMMIT',\n"
        "    'BULLPEN_TICKET_ID', 'BULLPEN_TICKET_TITLE', 'BULLPEN_TICKET_STATUS',\n"
        "    'BULLPEN_TICKET_PRIORITY', 'BULLPEN_TICKET_TAGS']}\n"
        "open(out, 'w', encoding='utf-8').write(json.dumps(data))\n"
        "print('ticket-service-ready', flush=True)\n"
        "time.sleep(30)\n",
    )
    _install_service_worker(
        bp_dir,
        tmp_workspace,
        command=f'"{sys.executable}" "{script}"',
        activation="manual",
        disposition="review",
        max_retries=0,
    )
    task = create_task(bp_dir, "Restart test server", description="commit: abcdef1\n")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)

    assert _wait_for(lambda: read_task(bp_dir, task["id"]).get("status") == "review") is True
    updated = read_task(bp_dir, task["id"])
    assert updated["assigned_to"] == ""
    history = [row for row in updated.get("history", []) if row.get("event") == "service_order_succeeded"]
    assert history
    assert history[-1]["log_artifact"].startswith(".bullpen/logs/services/slot-0/")
    layout = _load_layout(bp_dir)
    assert layout["slots"][0]["task_queue"] == []
    assert layout["slots"][0]["state"] == "idle"

    assert _wait_for(lambda: os.path.exists(output_path)) is True
    injected = json.loads(open(output_path, encoding="utf-8").read())
    assert injected["BULLPEN_SERVICE_ORDER_ID"] == task["id"]
    assert injected["BULLPEN_SERVICE_COMMIT"] == "abcdef1"
    assert injected["BULLPEN_TICKET_ID"] == task["id"]
    assert injected["BULLPEN_TICKET_TITLE"] == "Restart test server"
    assert injected["BULLPEN_TICKET_STATUS"] == "in_progress"
    assert injected["BULLPEN_TICKET_PRIORITY"] == "normal"

    stop_service(bp_dir, None, 0)


def test_procfile_preview_resolves_selected_process_and_port(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    procfile_path = os.path.join(tmp_workspace, "Procfile")
    _write_script(
        procfile_path,
        "# comment\n"
        "web: python3 app.py --port=$PORT --workers=${WEB_CONCURRENCY}\n"
        "worker: python3 jobs.py\n",
    )
    worker = _install_service_worker(
        bp_dir,
        tmp_workspace,
        command="",
        command_source="procfile",
        procfile_process="web",
        port=3100,
        env=[{"key": "WEB_CONCURRENCY", "value": "2"}],
    )

    preview = resolve_service_preview(worker, tmp_workspace, 0)

    assert preview["process_names"] == ["web", "worker"]
    assert preview["selected_process"] == "web"
    assert preview["raw_command"] == "python3 app.py --port=$PORT --workers=${WEB_CONCURRENCY}"
    assert preview["resolved_command"] == "python3 app.py --port=3100 --workers=2"


def test_suggest_service_port_skips_reserved_worker_ports(monkeypatch):
    layout = {
        "slots": [
            {"type": "service", "port": 3000},
            {"type": "shell", "command": "echo hi"},
            {"type": "service", "port": 3002},
            {"type": "service", "port": None},
        ]
    }
    monkeypatch.setattr("server.service_worker._port_is_bindable", lambda port: port != 3001)

    suggested = suggest_service_port(layout, ignore_slot=3, start=3000, end=3005)

    assert suggested == 3003


def test_service_preview_api_merges_unsaved_procfile_fields(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    _write_script(os.path.join(tmp_workspace, "Procfile"), "web: python3 app.py --port=$PORT\n")
    _install_service_worker(bp_dir, tmp_workspace, command="python3 old.py")
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()

    resp = client.post(
        "/api/service/preview",
        json={
            "workspaceId": app.config["startup_workspace_id"],
            "slot": 0,
            "fields": {
                "command_source": "procfile",
                "procfile_process": "web",
                "port": 3200,
            },
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["command_source"] == "procfile"
    assert data["selected_process"] == "web"
    assert data["suggested_port"] is None
    assert data["resolved_command"] == "python3 app.py --port=3200"


def test_service_preview_api_suggests_open_port_when_none_configured(tmp_workspace, monkeypatch):
    bp_dir = init_workspace(tmp_workspace)
    _write_script(os.path.join(tmp_workspace, "Procfile"), "web: python3 app.py --port=$PORT\n")
    _install_service_worker(
        bp_dir,
        tmp_workspace,
        command="",
        command_source="procfile",
        procfile_process="web",
    )
    layout = read_json(os.path.join(bp_dir, "layout.json"))
    layout["slots"].append({"type": "service", "name": "Taken", "port": 3000})
    write_json(os.path.join(bp_dir, "layout.json"), layout)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()
    monkeypatch.setattr("server.service_worker._port_is_bindable", lambda port: port not in {3001, 3002})

    resp = client.post(
        "/api/service/preview",
        json={
            "workspaceId": app.config["startup_workspace_id"],
            "slot": 0,
            "fields": {
                "command_source": "procfile",
                "procfile_process": "web",
            },
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["suggested_port"] == 3003


def test_procfile_service_restart_rereads_procfile(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    output_path = os.path.join(tmp_workspace, "procfile-run.txt")
    script = os.path.join(tmp_workspace, "procfile_runner.py")
    _write_script(
        script,
        "import pathlib, sys, time\n"
        f"path = pathlib.Path({output_path!r})\n"
        "path.write_text(sys.argv[1], encoding='utf-8')\n"
        "print(sys.argv[1], flush=True)\n"
        "time.sleep(30)\n",
    )
    procfile_path = os.path.join(tmp_workspace, "Procfile")
    _write_script(procfile_path, f'web: "{sys.executable}" "{script}" first\n')
    _install_service_worker(
        bp_dir,
        tmp_workspace,
        command="",
        command_source="procfile",
        procfile_process="web",
        startup_grace_seconds=0,
    )
    socket = FakeSocket()
    ws_id = "ws-service-procfile"

    assert start_service(bp_dir, ws_id, 0, socket) is True
    controller = get_controller(bp_dir, ws_id, 0, socket)
    assert _wait_for(lambda: controller.state_snapshot()["state"] == "running") is True
    assert _wait_for(lambda: os.path.exists(output_path) and open(output_path, encoding="utf-8").read() == "first") is True

    _write_script(procfile_path, f'web: "{sys.executable}" "{script}" second\n')
    assert restart_service(bp_dir, ws_id, 0, socket) is True
    assert _wait_for(lambda: controller.state_snapshot()["state"] == "running") is True
    assert _wait_for(lambda: os.path.exists(output_path) and open(output_path, encoding="utf-8").read() == "second") is True

    stop_service(bp_dir, ws_id, 0, socket)


def test_service_ticket_order_restarts_running_service(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    _install_service_worker(bp_dir, tmp_workspace, activation="manual", ticket_action="restart", max_retries=0)
    socket = FakeSocket()
    ws_id = "ws-service-ticket-restart"

    start_service(bp_dir, ws_id, 0, socket)
    controller = get_controller(bp_dir, ws_id, 0, socket)
    assert _wait_for(lambda: controller.state_snapshot()["state"] == "running") is True
    first_pid = controller.state_snapshot()["pid"]

    task = create_task(bp_dir, "Restart running service")
    assign_task(bp_dir, 0, task["id"], socket, ws_id)
    start_worker(bp_dir, 0, socket, ws_id)
    assert _wait_for(lambda: read_task(bp_dir, task["id"]).get("status") == "review") is True

    snapshot = controller.state_snapshot()
    assert snapshot["state"] == "running"
    assert snapshot["pid"] != first_pid
    history = [row for row in read_task(bp_dir, task["id"]).get("history", []) if row.get("event") == "service_order_succeeded"]
    assert history[-1]["action"] == "restart"

    stop_service(bp_dir, ws_id, 0, socket)


def test_service_ticket_order_failure_blocks_and_records_history(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    _install_service_worker(
        bp_dir,
        tmp_workspace,
        activation="manual",
        pre_start=f'"{sys.executable}" -c "import sys; sys.exit(4)"',
        max_retries=0,
    )
    task = create_task(bp_dir, "Broken service")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)

    assert _wait_for(lambda: read_task(bp_dir, task["id"]).get("status") == "blocked") is True
    updated = read_task(bp_dir, task["id"])
    history = updated.get("history", [])
    assert any(row.get("event") == "service_order_started" for row in history)
    failed = [row for row in history if row.get("event") == "service_order_failed"]
    assert failed
    assert "Pre-start exited" in failed[-1]["reason"]
    assert "Pre-start exited" in updated["body"]


def test_service_shell_health_gates_ticket_success(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    ready_path = os.path.join(tmp_workspace, "ready.flag")
    script = os.path.join(tmp_workspace, "healthy_service.py")
    _write_script(
        script,
        "import pathlib, time\n"
        "time.sleep(0.3)\n"
        f"pathlib.Path({ready_path!r}).write_text('ok')\n"
        "print('healthy-ready', flush=True)\n"
        "time.sleep(30)\n",
    )
    _install_service_worker(
        bp_dir,
        tmp_workspace,
        command=f'"{sys.executable}" "{script}"',
        activation="manual",
        health_type="shell",
        health_command=f'test -f "{ready_path}"',
        health_interval_seconds=1,
        health_timeout_seconds=1,
        startup_timeout_seconds=5,
        max_retries=0,
    )
    socket = FakeSocket()
    ws_id = "ws-service-health"
    task = create_task(bp_dir, "Wait for health")
    assign_task(bp_dir, 0, task["id"], socket, ws_id)

    start_worker(bp_dir, 0, socket, ws_id)

    assert _wait_for(lambda: read_task(bp_dir, task["id"]).get("status") == "review") is True
    controller = get_controller(bp_dir, ws_id, 0, socket)
    assert controller.state_snapshot()["state"] == "healthy"
    history = [row for row in read_task(bp_dir, task["id"]).get("history", []) if row.get("event") == "service_order_succeeded"]
    assert history[-1]["state"] == "healthy"

    stop_service(bp_dir, ws_id, 0, socket)


def test_service_health_timeout_blocks_ticket(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    _install_service_worker(
        bp_dir,
        tmp_workspace,
        activation="manual",
        health_type="shell",
        health_command="exit 1",
        health_interval_seconds=1,
        health_timeout_seconds=1,
        startup_timeout_seconds=1,
        max_retries=0,
    )
    task = create_task(bp_dir, "Never healthy")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)

    assert _wait_for(lambda: read_task(bp_dir, task["id"]).get("status") == "blocked", timeout=3) is True
    updated = read_task(bp_dir, task["id"])
    failed = [row for row in updated.get("history", []) if row.get("event") == "service_order_failed"]
    assert failed
    assert "health check timed out" in failed[-1]["reason"].lower()


def test_service_order_yank_cancels_without_routing_ticket(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    _install_service_worker(
        bp_dir,
        tmp_workspace,
        activation="manual",
        pre_start=f'"{sys.executable}" -c "import time; time.sleep(5)"',
        startup_timeout_seconds=10,
        max_retries=0,
    )
    socket = FakeSocket()
    ws_id = "ws-service-cancel"
    task = create_task(bp_dir, "Cancel service order")
    assign_task(bp_dir, 0, task["id"], socket, ws_id)
    start_worker(bp_dir, 0, socket, ws_id)
    assert _wait_for(lambda: read_task(bp_dir, task["id"]).get("status") == "in_progress") is True

    assert yank_from_worker(bp_dir, task["id"], socket, ws_id) is True
    update_task(bp_dir, task["id"], {"status": "review", "assigned_to": "", "handoff_depth": 0})

    time.sleep(0.4)
    updated = read_task(bp_dir, task["id"])
    assert updated["status"] == "review"
    assert updated["assigned_to"] == ""
    layout = _load_layout(bp_dir)
    assert layout["slots"][0]["task_queue"] == []
    assert layout["slots"][0]["state"] == "idle"
    assert not any(row.get("event") == "service_order_succeeded" for row in updated.get("history", []))
    assert not any(row.get("event") == "service_order_failed" for row in updated.get("history", []))


def teardown_module(_module):
    stop_all_services(wait=True)
