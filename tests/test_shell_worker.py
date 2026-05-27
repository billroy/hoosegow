"""Backend tests for Shell workers."""

import json
import os
import shlex
import sys
import time

import pytest

import server.workers as workers_mod
from server.init import init_workspace
from server.persistence import read_json, write_json
from server.tasks import create_task, list_tasks, read_task
from server.workers import assign_task, start_worker, _load_layout, _processes


class CapturingSocket:
    def __init__(self):
        self.events = []

    def emit(self, event, payload, to=None):
        self.events.append((event, payload, to))


@pytest.fixture
def bp_dir(tmp_workspace):
    return init_workspace(tmp_workspace)


def _wait_for_worker_done(bp_dir, slot=0, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with workers_mod._process_lock:
            running = bool(_processes)
        layout = _load_layout(bp_dir)
        worker = layout["slots"][slot]
        if not running and worker.get("state") == "idle":
            return
        time.sleep(0.05)
    raise AssertionError("worker did not finish")


def _wait_for_worker_state(bp_dir, state, slot=0, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        layout = _load_layout(bp_dir)
        worker = layout["slots"][slot]
        if worker.get("state") == state:
            return
        time.sleep(0.05)
    raise AssertionError(f"worker did not reach {state}")


def _wait_for_task_status(bp_dir, task_id, status, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = read_task(bp_dir, task_id)
        if task.get("status") == status:
            return task
        time.sleep(0.05)
    raise AssertionError(f"task did not reach {status}")


def _shell_run_history(task):
    return [row for row in task.get("history", []) if row.get("event") == "worker_run"]


def _shell_stdout_artifacts(bp_dir, task_id):
    artifact_dir = os.path.join(bp_dir, "logs", "worker-runs", task_id)
    if not os.path.isdir(artifact_dir):
        return []
    return sorted(name for name in os.listdir(artifact_dir) if name.endswith(".stdout.log"))


def _set_shell_worker(bp_dir, **overrides):
    worker = {
        "type": "shell",
        "row": 0,
        "col": 0,
        "name": "Shell Gate",
        "activation": "manual",
        "disposition": "review",
        "watch_column": None,
        "max_retries": 0,
        "paused": False,
        "task_queue": [],
        "state": "idle",
        "command": "true",
        "cwd": "",
        "timeout_seconds": 10,
        "env": [],
        "ticket_delivery": "stdin-json",
    }
    worker.update(overrides)
    layout = read_json(os.path.join(bp_dir, "layout.json"))
    layout["slots"] = [worker]
    write_json(os.path.join(bp_dir, "layout.json"), layout)


def _python_command(code):
    return f"{sys.executable} -c {shlex.quote(code)}"


def test_shell_success_uses_configured_disposition_and_records_output(bp_dir):
    _set_shell_worker(
        bp_dir,
        command=_python_command(
            "import json,sys; "
            "t=json.load(sys.stdin); "
            "print('hello ' + t['title']); "
            "print('warn', file=sys.stderr)"
        ),
    )
    task = create_task(bp_dir, "Shell task", description="Do it")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    updated = read_task(bp_dir, task["id"])
    assert updated["status"] == "review"
    assert updated["assigned_to"] == ""
    assert "## Worker Output" in updated["body"]
    assert "hello Shell task" in updated["body"]
    assert "warn" in updated["body"]
    assert "worker_run" in json.dumps(updated.get("history", []))
    history = [row for row in updated.get("history", []) if row.get("event") == "worker_run"]
    assert history[-1]["worker_type"] == "shell"
    assert history[-1]["outcome"] == "success"
    assert history[-1]["stdout_artifact"].startswith(".bullpen/logs/worker-runs/")


def test_shell_json_stdout_overrides_disposition_and_updates_ticket(bp_dir):
    payload = {
        "disposition": "done",
        "ticket_updates": {
            "priority": "high",
            "tags": ["shell"],
            "body_append": "shell appended",
        },
    }
    _set_shell_worker(
        bp_dir,
        command=_python_command(f"import json; print(json.dumps({payload!r}))"),
    )
    task = create_task(bp_dir, "Update me")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    updated = read_task(bp_dir, task["id"])
    assert updated["status"] == "done"
    assert updated["priority"] == "high"
    assert updated["tags"] == ["shell"]
    assert "shell appended" in updated["body"]


def test_shell_pass_to_manual_worker_queues_until_run(bp_dir):
    layout = read_json(os.path.join(bp_dir, "layout.json"))
    base = {
        "type": "shell",
        "activation": "manual",
        "watch_column": None,
        "max_retries": 0,
        "paused": False,
        "task_queue": [],
        "state": "idle",
        "command": _python_command("import time; time.sleep(0.1)"),
        "cwd": "",
        "timeout_seconds": 10,
        "env": [],
        "ticket_delivery": "stdin-json",
    }
    layout["slots"] = [
        {
            **base,
            "row": 0,
            "col": 0,
            "name": "First",
            "disposition": "pass:down",
        },
        {
            **base,
            "row": 1,
            "col": 0,
            "name": "Second",
            "disposition": "review",
        },
    ]
    write_json(os.path.join(bp_dir, "layout.json"), layout)

    task = create_task(bp_dir, "Manual pass chain")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    updated = _wait_for_task_status(bp_dir, task["id"], "assigned")

    final_layout = _load_layout(bp_dir)
    assert final_layout["slots"][0]["state"] == "idle"
    assert final_layout["slots"][1]["state"] == "idle"
    assert final_layout["slots"][0]["task_queue"] == []
    assert final_layout["slots"][1]["task_queue"] == [task["id"]]
    assert str(updated["assigned_to"]) == "1"
    assert len(_shell_run_history(updated)) == 1

    start_worker(bp_dir, 1)
    updated = _wait_for_task_status(bp_dir, task["id"], "review")
    final_layout = _load_layout(bp_dir)
    assert final_layout["slots"][1]["task_queue"] == []
    assert updated["assigned_to"] == ""
    assert len(_shell_run_history(updated)) == 2


def test_shell_json_status_key_is_ignored(bp_dir):
    _set_shell_worker(
        bp_dir,
        command=_python_command('import json; print(json.dumps({"status":"done"}))'),
        disposition="review",
    )
    task = create_task(bp_dir, "Ignore status")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    updated = read_task(bp_dir, task["id"])
    assert updated["status"] == "review"


def test_shell_disallowed_ticket_update_fails_closed(bp_dir):
    _set_shell_worker(
        bp_dir,
        command=_python_command(
            'import json; print(json.dumps({"ticket_updates":{"priority":"high","status":"done"}}))'
        ),
        disposition="done",
    )
    task = create_task(bp_dir, "Bad update")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    updated = read_task(bp_dir, task["id"])
    assert updated["status"] == "blocked"
    assert updated["priority"] == "normal"
    assert "disallowed field: status" in updated["body"]


def test_shell_command_does_not_interpolate_ticket_fields(bp_dir):
    _set_shell_worker(
        bp_dir,
        command="printf '%s' \"$BULLPEN_TICKET_TITLE\"",
        ticket_delivery="stdin-json",
    )
    task = create_task(bp_dir, "Secret title")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    updated = read_task(bp_dir, task["id"])
    assert updated["status"] == "review"
    latest = [row for row in updated.get("history", []) if row.get("event") == "worker_run"][-1]
    artifact = os.path.join(os.path.dirname(bp_dir), latest["stdout_artifact"])
    assert open(artifact).read() == ""


def test_shell_exit_78_blocks_without_retry(bp_dir):
    _set_shell_worker(
        bp_dir,
        command=_python_command('import json,sys; print(json.dumps({"reason":"not a bug"})); sys.exit(78)'),
        max_retries=5,
    )
    task = create_task(bp_dir, "Block me")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    updated = read_task(bp_dir, task["id"])
    assert updated["status"] == "blocked"
    retries = [row for row in updated.get("history", []) if row.get("event") == "retry"]
    assert retries == []
    assert "not a bug" in updated["body"]


def test_shell_exit_2_is_retryable_error(bp_dir):
    _set_shell_worker(
        bp_dir,
        command=_python_command('import sys; print("bad", file=sys.stderr); sys.exit(2)'),
        max_retries=0,
    )
    task = create_task(bp_dir, "Fail me")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    updated = read_task(bp_dir, task["id"])
    assert updated["status"] == "blocked"
    assert "Shell command exited 2" in updated["body"]


def test_shell_exit_127_reports_missing_command(bp_dir):
    _set_shell_worker(
        bp_dir,
        command=_python_command('import sys; print("/bin/sh: 1: say: not found", file=sys.stderr); sys.exit(127)'),
        max_retries=0,
    )
    task = create_task(bp_dir, "Missing command")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    updated = read_task(bp_dir, task["id"])
    assert updated["status"] == "blocked"
    assert "Shell command not found: say (exit 127)" in updated["body"]


def test_shell_timeout_records_canonical_reason(bp_dir):
    _set_shell_worker(
        bp_dir,
        command=_python_command("import time; time.sleep(5)"),
        timeout_seconds=1,
        max_retries=0,
    )
    task = create_task(bp_dir, "Timeout")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir, timeout=7.0)

    updated = read_task(bp_dir, task["id"])
    assert updated["status"] == "blocked"
    history = [row for row in updated.get("history", []) if row.get("event") == "worker_run"]
    assert history[-1]["reason"] == "timeout"
    assert "timeout" in updated["body"]


def test_shell_env_vars_delivery_and_secret_filter(bp_dir, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "do-not-leak")
    output_path = os.path.join(os.path.dirname(bp_dir), "env-output.json")
    script = (
        "import json,os;"
        "data=dict("
        "title=os.environ.get('BULLPEN_TICKET_TITLE'),"
        "tags=os.environ.get('BULLPEN_TICKET_TAGS'),"
        "body_file_exists=os.path.exists(os.environ.get('BULLPEN_TICKET_BODY_FILE','')),"
        "github_token=os.environ.get('GITHUB_TOKEN'),"
        "explicit=os.environ.get('EXPLICIT_VALUE'));"
        f"open({output_path!r},'w').write(json.dumps(data))"
    )
    _set_shell_worker(
        bp_dir,
        command=_python_command(script),
        ticket_delivery="env-vars",
        env=[{"key": "EXPLICIT_VALUE", "value": "ok"}],
    )
    task = create_task(bp_dir, "Env Task", tags=["alpha"], description="body")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    data = json.loads(open(output_path).read())
    assert data["title"] == "Env Task"
    assert json.loads(data["tags"]) == ["alpha"]
    assert data["body_file_exists"] is True
    assert data["github_token"] is None
    assert data["explicit"] == "ok"


def test_shell_rejects_cwd_symlink_escape(bp_dir, tmp_path):
    outside = tmp_path / "outside"
    os.makedirs(outside)
    link = os.path.join(os.path.dirname(bp_dir), "escape")
    os.symlink(str(outside), link)
    _set_shell_worker(bp_dir, command="true", cwd="escape")
    task = create_task(bp_dir, "Escape")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    updated = read_task(bp_dir, task["id"])
    assert updated["status"] == "blocked"
    assert "cwd escapes" in updated["body"]


def test_shell_manual_empty_queue_creates_synthetic_ticket(bp_dir):
    _set_shell_worker(
        bp_dir,
        command=_python_command('import json,sys; t=json.load(sys.stdin); print(t["title"])'),
    )

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    tasks = [task for task in list_tasks(bp_dir) if task["title"].startswith("[Auto]")]
    assert len(tasks) == 1
    synthetic = tasks[0]
    assert synthetic["type"] == "chore"
    assert "synthetic" in synthetic["tags"]
    assert synthetic["synthetic_run"] is True
    assert synthetic["status"] == "review"


def test_shell_on_drop_empty_queue_manual_run_executes_synthetic_ticket_once(bp_dir):
    _set_shell_worker(
        bp_dir,
        activation="on_drop",
        command=_python_command('import json,sys; t=json.load(sys.stdin); print(t["title"])'),
    )

    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    tasks = [task for task in list_tasks(bp_dir) if task["title"].startswith("[Auto]")]
    assert len(tasks) == 1
    synthetic = read_task(bp_dir, tasks[0]["id"])
    history = _shell_run_history(synthetic)
    assert len(history) == 1
    assert len(_shell_stdout_artifacts(bp_dir, synthetic["id"])) == 1
    assert synthetic["status"] == "review"


def test_shell_duplicate_start_while_working_does_not_reuse_queue_head(bp_dir):
    _set_shell_worker(
        bp_dir,
        command=_python_command("import json,sys,time; json.load(sys.stdin); print('once'); time.sleep(0.25)"),
    )
    task = create_task(bp_dir, "Run once")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0)
    _wait_for_worker_state(bp_dir, "working")
    start_worker(bp_dir, 0)
    _wait_for_worker_done(bp_dir)

    updated = read_task(bp_dir, task["id"])
    history = _shell_run_history(updated)
    artifacts = _shell_stdout_artifacts(bp_dir, task["id"])
    assert len(history) == 1
    assert len(artifacts) == 1
    artifact = os.path.join(bp_dir, "logs", "worker-runs", task["id"], artifacts[0])
    with open(artifact, encoding="utf-8") as handle:
        assert handle.read().strip() == "once"


def test_shell_feature_flag_can_disable_worker(bp_dir, monkeypatch):
    monkeypatch.setenv("BULLPEN_ENABLE_SHELL_WORKERS", "0")
    socket = CapturingSocket()
    _set_shell_worker(bp_dir, command="true")
    task = create_task(bp_dir, "No run")
    assign_task(bp_dir, 0, task["id"])

    start_worker(bp_dir, 0, socketio=socket, ws_id="ws")

    updated = read_task(bp_dir, task["id"])
    layout = _load_layout(bp_dir)
    assert updated["status"] == "assigned"
    assert layout["slots"][0]["state"] == "idle"
    assert any("disabled" in payload.get("message", "") for event, payload, _ in socket.events if event == "toast")
