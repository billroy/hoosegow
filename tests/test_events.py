"""Tests for server/events.py — socket event handlers."""

import os
import sys
import tempfile
import time
import json

import pytest

from server.agents import register_adapter
from server.agents.base import AgentAdapter
from server import mcp_auth
from server.app import create_app, socketio
from server.persistence import read_json, write_json
from server import service_worker as service_worker_mod
import server.workers as workers_mod
from tests.conftest import MockAdapter


@pytest.fixture
def client():
    """Create a Flask-SocketIO test client."""
    with tempfile.TemporaryDirectory(prefix="bullpen_test_") as ws:
        app = create_app(ws, no_browser=True)
        client = socketio.test_client(app)
        # Drain the state:init event
        client.get_received()
        yield client, app
        client.disconnect()


def get_event(client, name):
    """Find the first event with given name from received events."""
    for evt in client.get_received():
        if evt["name"] == name:
            return evt["args"][0]
    return None


def get_all_events(client, name):
    """Get all events with given name."""
    return [evt["args"][0] for evt in client.get_received() if evt["name"] == name]


def _wait_for_event(client, name, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for evt in client.get_received():
            if evt["name"] == name:
                return evt["args"][0]
        time.sleep(0.05)
    return None


def test_start_without_project_connects_with_empty_project_list(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    global_dir = tmp_path / "home" / ".bullpen"
    monkeypatch.setenv("BULLPEN_PROJECTS_ROOT", str(workspace_root))
    monkeypatch.setenv("BULLPEN_START_WITHOUT_PROJECT", "1")

    app = create_app(str(workspace_root), no_browser=True, global_dir=str(global_dir))
    client = socketio.test_client(app)
    events = client.get_received()
    client.disconnect()

    assert app.config["startup_workspace_id"] is None
    assert [evt for evt in events if evt["name"] == "state:init"] == []
    project_events = [evt for evt in events if evt["name"] == "projects:updated"]
    assert project_events
    assert project_events[-1]["args"][0] == []


def test_start_without_project_hides_stale_workspace_root_registry_entry(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    global_dir = tmp_path / "home" / ".bullpen"
    global_dir.mkdir(parents=True)
    monkeypatch.setenv("BULLPEN_PROJECTS_ROOT", str(workspace_root))
    monkeypatch.setenv("BULLPEN_START_WITHOUT_PROJECT", "1")
    monkeypatch.setenv("BULLPEN_HIDE_UNAVAILABLE_PROJECTS", "1")
    write_json(
        str(global_dir / "projects.json"),
        {
            "version": 1,
            "projects": [
                {"id": "stale-root", "path": str(workspace_root), "name": "old-root"},
            ],
        },
    )

    app = create_app(str(workspace_root), no_browser=True, global_dir=str(global_dir))
    client = socketio.test_client(app)
    events = client.get_received()
    client.disconnect()

    project_events = [evt for evt in events if evt["name"] == "projects:updated"]
    assert project_events
    assert project_events[-1]["args"][0] == []


def test_project_add_from_startupless_mode_activates_project(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    project = workspace_root / "project-a"
    project.mkdir(parents=True)
    global_dir = tmp_path / "home" / ".bullpen"
    monkeypatch.setenv("BULLPEN_PROJECTS_ROOT", str(workspace_root))
    monkeypatch.setenv("BULLPEN_START_WITHOUT_PROJECT", "1")

    app = create_app(str(workspace_root), no_browser=True, global_dir=str(global_dir))
    client = socketio.test_client(app)
    client.get_received()

    client.emit("project:add", {"path": str(project)})
    events = client.get_received()
    client.disconnect()

    state_events = [evt["args"][0] for evt in events if evt["name"] == "state:init"]
    assert state_events
    assert state_events[-1]["switchTo"] is True
    ws_id = state_events[-1]["workspaceId"]
    assert app.config["manager"].get_workspace_path(ws_id) == str(project.resolve())
    assert os.path.isdir(project / ".bullpen")
    assert not os.path.exists(workspace_root / ".bullpen")


def test_project_add_accepts_name_under_configured_projects_root(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    project = workspace_root / "project-a"
    project.mkdir(parents=True)
    global_dir = tmp_path / "home" / ".bullpen"
    monkeypatch.setenv("BULLPEN_PROJECTS_ROOT", str(workspace_root))
    monkeypatch.setenv("BULLPEN_START_WITHOUT_PROJECT", "1")

    app = create_app(str(workspace_root), no_browser=True, global_dir=str(global_dir))
    client = socketio.test_client(app)
    initial_events = client.get_received()
    settings = [evt["args"][0] for evt in initial_events if evt["name"] == "project:settings"]

    client.emit("project:add", {"path": "project-a"})
    events = client.get_received()
    client.disconnect()

    assert settings and settings[-1] == {"projectsRoot": str(workspace_root.resolve())}
    state_events = [evt["args"][0] for evt in events if evt["name"] == "state:init"]
    assert state_events
    ws_id = state_events[-1]["workspaceId"]
    assert app.config["manager"].get_workspace_path(ws_id) == str(project.resolve())


def test_start_without_project_refresh_requires_join_for_registered_project(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    project = workspace_root / "project-a"
    project.mkdir(parents=True)
    global_dir = tmp_path / "home" / ".bullpen"
    monkeypatch.setenv("BULLPEN_PROJECTS_ROOT", str(workspace_root))
    monkeypatch.setenv("BULLPEN_START_WITHOUT_PROJECT", "1")

    first_app = create_app(str(workspace_root), no_browser=True, global_dir=str(global_dir))
    first_client = socketio.test_client(first_app)
    first_client.get_received()
    first_client.emit("project:add", {"path": str(project)})
    first_events = first_client.get_received()
    first_client.disconnect()
    ws_id = [evt["args"][0] for evt in first_events if evt["name"] == "state:init"][-1]["workspaceId"]

    app = create_app(str(workspace_root), no_browser=True, global_dir=str(global_dir))
    client = socketio.test_client(app)
    refresh_events = client.get_received()
    assert [evt for evt in refresh_events if evt["name"] == "state:init"] == []
    project_updates = [evt for evt in refresh_events if evt["name"] == "projects:updated"]
    assert project_updates[-1]["args"][0] == [{"id": ws_id, "name": "project-a", "available": True}]

    client.emit("project:join", {"workspaceId": ws_id})
    join_events = client.get_received()
    client.disconnect()
    state_events = [evt["args"][0] for evt in join_events if evt["name"] == "state:init"]
    assert state_events
    assert state_events[-1]["workspaceId"] == ws_id


def test_project_root_guard_rejects_outside_paths(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    global_dir = tmp_path / "home" / ".bullpen"
    monkeypatch.setenv("BULLPEN_PROJECTS_ROOT", str(workspace_root))
    monkeypatch.setenv("BULLPEN_START_WITHOUT_PROJECT", "1")

    app = create_app(str(workspace_root), no_browser=True, global_dir=str(global_dir))
    client = socketio.test_client(app)
    client.get_received()

    client.emit("project:add", {"path": str(outside)})
    error = get_event(client, "error")
    client.disconnect()

    assert error is not None
    assert "Project path must be inside" in error["message"]


def test_project_root_guard_rejects_symlink_escape(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace_root / "outside-link"
    link.symlink_to(outside, target_is_directory=True)
    global_dir = tmp_path / "home" / ".bullpen"
    monkeypatch.setenv("BULLPEN_PROJECTS_ROOT", str(workspace_root))
    monkeypatch.setenv("BULLPEN_START_WITHOUT_PROJECT", "1")

    app = create_app(str(workspace_root), no_browser=True, global_dir=str(global_dir))
    client = socketio.test_client(app)
    client.get_received()

    client.emit("project:add", {"path": str(link)})
    error = get_event(client, "error")
    client.disconnect()

    assert error is not None
    assert "Project path must be inside" in error["message"]


class ChatUsageAdapter(AgentAdapter):
    @property
    def name(self):
        return "chat-usage-mock"

    def available(self):
        return True

    def build_argv(self, prompt, model, workspace, bp_dir=None):
        import sys
        script = (
            "import json; "
            "print(json.dumps({'type':'result','is_error':False,'result':'ok',"
            "'usage':{'input_tokens':11,'output_tokens':7,'cached_input_tokens':3}}))"
        )
        return [sys.executable, "-c", script]

    def parse_output(self, stdout, stderr, exit_code):
        return {"success": True, "output": stdout.strip(), "error": None, "usage": {}}

    def format_stream_line(self, line):
        return None


class ChatFailingAdapter(AgentAdapter):
    @property
    def name(self):
        return "chat-failing-mock"

    def available(self):
        return True

    def build_argv(self, prompt, model, workspace, bp_dir=None):
        import sys
        script = (
            "import sys; "
            "sys.stderr.write('ModelNotFoundError: Requested entity was not found.\\n'); "
            "sys.exit(1)"
        )
        return [sys.executable, "-c", script]

    def parse_output(self, stdout, stderr, exit_code):
        if exit_code != 0:
            return {"success": False, "output": (stdout or "").strip(), "error": (stderr or "").strip(), "usage": {}}
        return {"success": True, "output": (stdout or "").strip(), "error": None, "usage": {}}

    def format_stream_line(self, line):
        return None


class ChatEnvAdapter(AgentAdapter):
    def __init__(self):
        self.cleanup_path = None

    @property
    def name(self):
        return "chat-env-mock"

    def available(self):
        return True

    def build_argv(self, prompt, model, workspace, bp_dir=None):
        script = (
            "import os; "
            "print(os.environ.get('BULLPEN_CHAT_ENV_TEST', 'missing'))"
        )
        return [sys.executable, "-c", script]

    def prepare_env(self, workspace, bp_dir=None, task_id=None):
        env = os.environ.copy()
        env["BULLPEN_CHAT_ENV_TEST"] = "prepared"
        self.cleanup_path = tempfile.mkdtemp(prefix="bullpen-chat-env-test-")
        return env, self.cleanup_path

    def parse_output(self, stdout, stderr, exit_code):
        return {"success": exit_code == 0, "output": stdout.strip(), "error": stderr.strip(), "usage": {}}


class TestTaskEvents:
    def test_create_task(self, client):
        c, app = client
        c.emit("task:create", {"title": "Test Task", "type": "bug", "priority": "high"})
        task = get_event(c, "task:created")
        assert task is not None
        assert task["title"] == "Test Task"
        assert task["type"] == "bug"
        assert task["priority"] == "high"
        assert task["status"] == "inbox"
        assert task["id"]

        # Verify file was written
        path = os.path.join(app.config["bp_dir"], "tasks", f"{task['id']}.md")
        assert os.path.exists(path)

    def test_update_task(self, client):
        c, app = client
        c.emit("task:create", {"title": "Update Me"})
        task = get_event(c, "task:created")

        c.emit("task:update", {"id": task["id"], "status": "assigned", "priority": "urgent"})
        updated = get_event(c, "task:updated")
        assert updated is not None
        assert updated["status"] == "assigned"
        assert updated["priority"] == "urgent"
        assert updated["title"] == "Update Me"

    def test_mcp_status_update_for_owned_task_records_final_status(self, client):
        c, app = client
        bp_dir = app.config["bp_dir"]
        layout = read_json(os.path.join(bp_dir, "layout.json"))
        layout["slots"] = [{
            "row": 0, "col": 0,
            "profile": "test",
            "name": "Test Worker",
            "agent": "mock",
            "model": "mock-model",
            "activation": "manual",
            "disposition": "random:",
            "watch_column": None,
            "expertise_prompt": "",
            "max_retries": 0,
            "task_queue": [],
            "state": "idle",
        }]
        write_json(os.path.join(bp_dir, "layout.json"), layout)

        c.emit("task:create", {"title": "MCP final status"})
        task = get_event(c, "task:created")
        workers_mod.assign_task(bp_dir, 0, task["id"])
        c.get_received()

        token = mcp_auth.read_workspace_mcp_token(bp_dir)
        mcp_client = socketio.test_client(app, auth={"mcp_token": token})
        try:
            mcp_client.get_received()
            mcp_client.emit("task:update", {"id": task["id"], "status": "done"})
            updated = get_event(mcp_client, "task:updated")
            assert updated["status"] == "assigned"
            assert str(updated["assigned_to"]) == "0"
            assert updated["worker_requested_status"] == "done"
            updated_layout = read_json(os.path.join(bp_dir, "layout.json"))
            assert task["id"] in updated_layout["slots"][0]["task_queue"]
        finally:
            mcp_client.disconnect()

    def test_create_task_auto_joins_target_workspace_on_first_action(self, client):
        c, app = client
        c2 = socketio.test_client(app)
        c2.get_received()
        with tempfile.TemporaryDirectory(prefix="bullpen_new_project_parent_") as parent:
            path = os.path.join(parent, "auto-join-project")
            c.emit("project:new", {"path": path})
            events = c.get_received()
            project_updates = [evt for evt in events if evt["name"] == "projects:updated"]
            listed = project_updates[-1]["args"][0]
            ws_id = next(p["id"] for p in listed if p["name"] == "auto-join-project")

            c2.emit("task:create", {"workspaceId": ws_id, "title": "Cross-workspace create"})
            created = get_event(c2, "task:created")
            err = get_event(c2, "error")

            assert err is None
            assert created is not None
            assert created["title"] == "Cross-workspace create"
            assert created["workspaceId"] == ws_id
        c2.disconnect()

    def test_clone_project_defaults_to_configured_projects_root(self, client, monkeypatch, tmp_path):
        c, _app = client
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            os.makedirs(argv[3])
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setenv("BULLPEN_PROJECTS_ROOT", str(projects_root))
        monkeypatch.setattr("server.events.subprocess.run", fake_run)

        c.emit("project:clone", {"url": "https://example.test/busy-deck.git"})

        events = c.get_received()
        by_name = {evt["name"]: evt["args"][0] for evt in events}
        err = by_name.get("error")
        started = by_name.get("project:clone:started")
        succeeded = by_name.get("project:clone:succeeded")
        assert err is None
        assert started == {"url": "https://example.test/busy-deck.git", "path": str(projects_root / "busy-deck")}
        assert succeeded["url"] == "https://example.test/busy-deck.git"
        assert succeeded["path"] == str(projects_root / "busy-deck")
        assert succeeded["workspaceId"]
        assert calls
        assert calls[0][0] == [
            "git",
            "clone",
            "https://example.test/busy-deck.git",
            str(projects_root / "busy-deck"),
        ]

    def test_delete_task(self, client):
        c, app = client
        c.emit("task:create", {"title": "Delete Me"})
        task = get_event(c, "task:created")

        c.emit("task:delete", {"id": task["id"]})
        deleted = get_event(c, "task:deleted")
        assert deleted is not None
        assert deleted["id"] == task["id"]

        # Verify file removed
        path = os.path.join(app.config["bp_dir"], "tasks", f"{task['id']}.md")
        assert not os.path.exists(path)

    def test_clear_output(self, client):
        c, app = client
        c.emit("task:create", {"title": "Clear Output", "description": "Keep this"})
        task = get_event(c, "task:created")

        # Add some agent output
        body_with_output = task["body"] + "\n## Agent Output\n\nSome output.\n"
        c.emit("task:update", {"id": task["id"], "body": body_with_output})
        c.get_received()  # drain

        c.emit("task:clear_output", {"id": task["id"]})
        cleared = get_event(c, "task:updated")
        assert cleared is not None
        assert "## Agent Output" not in cleared["body"]
        assert "Keep this" in cleared["body"]

    def test_create_with_tags(self, client):
        c, _ = client
        c.emit("task:create", {"title": "Tagged", "tags": ["backend", "api"]})
        task = get_event(c, "task:created")
        assert task["tags"] == ["backend", "api"]

    def test_update_missing_id(self, client):
        c, _ = client
        c.emit("task:update", {"status": "done"})
        err = get_event(c, "error")
        assert err is not None
        assert "requires id" in err["message"]

    def test_delete_missing_id(self, client):
        c, _ = client
        c.emit("task:delete", {})
        err = get_event(c, "error")
        assert err is not None

    def test_assign_rejects_invalid_task_id(self, client):
        c, _ = client
        c.emit("task:assign", {"task_id": "…", "slot": 0})
        err = get_event(c, "error")
        assert err is not None
        assert "Invalid task_id" in err["message"]

    def test_assign_rejects_missing_task(self, client):
        c, _ = client
        c.emit("task:assign", {"task_id": "missing_task", "slot": 0})
        err = get_event(c, "error")
        assert err is not None
        assert err["message"] == "Task not found: missing_task"

    def test_full_lifecycle(self, client):
        """Create → update → delete lifecycle."""
        c, app = client
        # Create
        c.emit("task:create", {"title": "Lifecycle Task"})
        task = get_event(c, "task:created")
        task_id = task["id"]

        # Update
        c.emit("task:update", {"id": task_id, "status": "in_progress"})
        updated = get_event(c, "task:updated")
        assert updated["status"] == "in_progress"

        # Delete
        c.emit("task:delete", {"id": task_id})
        deleted = get_event(c, "task:deleted")
        assert deleted["id"] == task_id

        # Verify gone from disk
        path = os.path.join(app.config["bp_dir"], "tasks", f"{task_id}.md")
        assert not os.path.exists(path)

    def test_update_into_watched_column_claims_second_ticket_with_other_idle_watcher(self, client):
        """A second ticket entering a watched column while the first watcher is
        busy should be claimed through the real task:update event path."""
        class SlowAdapter(MockAdapter):
            @property
            def name(self):
                return "slow-mock"

            def build_argv(self, prompt, model, workspace, bp_dir=None):
                return [sys.executable, "-c", "import time; time.sleep(5)"]

        c, app = client
        register_adapter("slow-mock", SlowAdapter(output="slow"))
        register_adapter("mock", MockAdapter(output="fast"))

        bp_dir = app.config["bp_dir"]
        layout = read_json(os.path.join(bp_dir, "layout.json"))
        base = {
            "profile": "test", "activation": "on_queue",
            "disposition": "implemented", "watch_column": "approved",
            "expertise_prompt": "", "max_retries": 0,
            "paused": False, "task_queue": [], "state": "idle",
            "last_trigger_time": None,
        }
        layout["slots"] = [
            {**base, "row": 0, "col": 0, "name": "W1", "agent": "slow-mock", "model": "mock-model"},
            {**base, "row": 0, "col": 1, "name": "W2", "agent": "mock", "model": "mock-model"},
        ]
        write_json(os.path.join(bp_dir, "layout.json"), layout)

        c.emit("task:create", {"title": "Task 1"})
        task1 = get_event(c, "task:created")
        c.emit("task:update", {"id": task1["id"], "status": "approved"})
        time.sleep(0.4)
        c.get_received()

        c.emit("task:create", {"title": "Task 2"})
        task2 = get_event(c, "task:created")
        c.emit("task:update", {"id": task2["id"], "status": "approved"})
        time.sleep(0.6)

        updates = [
            evt["args"][0]
            for evt in c.get_received()
            if evt["name"] == "task:updated" and evt["args"] and evt["args"][0].get("id") == task2["id"]
        ]
        assert any(update.get("status") == "approved" for update in updates)
        assert any(update.get("status") == "assigned" and str(update.get("assigned_to")) == "1" for update in updates)
        assert any(update.get("status") == "in_progress" and str(update.get("assigned_to")) == "1" for update in updates)

    def test_update_into_watched_column_does_not_deadlock_on_start_failure(self, client, monkeypatch):
        """Watch-column auto-start failures must not deadlock later UI events."""
        c, app = client
        register_adapter("mock", MockAdapter(output="fast"))

        bp_dir = app.config["bp_dir"]
        layout = read_json(os.path.join(bp_dir, "layout.json"))
        layout["slots"] = [{
            "row": 0, "col": 0,
            "profile": "test", "name": "Watcher",
            "agent": "mock", "model": "mock-model",
            "activation": "on_queue", "disposition": "review",
            "watch_column": "approved", "expertise_prompt": "",
            "max_retries": 0, "task_queue": [], "state": "idle",
            "paused": False, "last_trigger_time": None,
            "use_worktree": True,
        }]
        write_json(os.path.join(bp_dir, "layout.json"), layout)

        monkeypatch.setattr(workers_mod, "_setup_worktree", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

        c.emit("task:create", {"title": "Task 1"})
        task1 = get_event(c, "task:created")
        c.emit("task:update", {"id": task1["id"], "status": "approved"})

        blocked = None
        deadline = time.time() + 2.0
        while time.time() < deadline:
            for evt in c.get_received():
                if evt["name"] == "task:updated":
                    payload = evt["args"][0]
                    if payload.get("id") == task1["id"] and payload.get("status") == "blocked":
                        blocked = payload
                        break
            if blocked:
                break
            time.sleep(0.05)

        assert blocked is not None

        c.emit("task:create", {"title": "Task 2"})
        task2 = get_event(c, "task:created")
        assert task2 is not None

    def test_archive_task(self, client):
        c, app = client
        c.emit("task:create", {"title": "Archive Me"})
        task = get_event(c, "task:created")

        c.emit("task:archive", {"id": task["id"]})
        deleted = get_event(c, "task:deleted")
        assert deleted is not None
        assert deleted["id"] == task["id"]

        # Verify moved to archive
        src = os.path.join(app.config["bp_dir"], "tasks", f"{task['id']}.md")
        dst = os.path.join(app.config["bp_dir"], "tasks", "archive", f"{task['id']}.md")
        assert not os.path.exists(src)
        assert os.path.exists(dst)

    def test_archive_done_tasks(self, client):
        c, app = client
        # Create a done task and an active task
        c.emit("task:create", {"title": "Done Task"})
        t1 = get_event(c, "task:created")
        c.emit("task:update", {"id": t1["id"], "status": "done"})
        c.get_received()

        c.emit("task:create", {"title": "Active Task"})
        t2 = get_event(c, "task:created")

        c.emit("task:archive-done", {})
        events = get_all_events(c, "task:deleted")
        archived_ids = {e["id"] for e in events}
        assert t1["id"] in archived_ids
        assert t2["id"] not in archived_ids

        # Active task still on disk
        active_path = os.path.join(app.config["bp_dir"], "tasks", f"{t2['id']}.md")
        assert os.path.exists(active_path)

    def test_task_list_scope_live_vs_archived(self, client):
        c, _ = client
        c.emit("task:create", {"title": "Live Task"})
        live_task = get_event(c, "task:created")
        c.get_received()

        c.emit("task:archive", {"id": live_task["id"]})
        c.get_received()

        c.emit("task:list", {"scope": "live"})
        live_list = get_event(c, "task:list")
        assert live_list is not None
        assert live_list["scope"] == "live"
        assert all(t["id"] != live_task["id"] for t in live_list["tasks"])

        c.emit("task:list", {"scope": "archived"})
        archived_list = get_event(c, "task:list")
        assert archived_list is not None
        assert archived_list["scope"] == "archived"
        assert any(t["id"] == live_task["id"] for t in archived_list["tasks"])


class TestWorkerEvents:
    def test_add_worker(self, client):
        c, app = client
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        layout = get_event(c, "layout:updated")
        assert layout is not None
        assert layout["slots"][0] is not None
        assert layout["slots"][0]["profile"] == "feature-architect"
        assert layout["slots"][0]["name"] == "Feature Architect"
        assert layout["slots"][0]["state"] == "idle"

    def test_marker_worker_start_via_socket_completes_without_deadlock(self, client):
        c, app = client
        c.emit("worker:add", {
            "slot": 0,
            "type": "marker",
            "fields": {
                "name": "Review Marker",
                "activation": "manual",
                "disposition": "review",
            },
        })
        assert get_event(c, "layout:updated") is not None

        c.emit("task:create", {"title": "Route through marker"})
        task = get_event(c, "task:created")
        c.emit("task:assign", {"task_id": task["id"], "slot": 0})
        c.get_received()

        c.emit("worker:start", {"slot": 0})

        from server.tasks import read_task
        updated = read_task(app.config["bp_dir"], task["id"])
        assert updated["status"] == "review"
        assert updated["assigned_to"] == ""

    def test_add_unconfigured_worker_uses_safe_defaults(self, client):
        c, _ = client
        c.emit("worker:add", {"slot": 0, "profile": "unconfigured-worker"})
        layout = get_event(c, "layout:updated")
        assert layout is not None
        worker = layout["slots"][0]
        assert worker["profile"] == "unconfigured-worker"
        assert worker["activation"] == "on_drop"
        assert worker["max_retries"] == 0
        assert worker["task_queue"] == []
        assert worker["state"] == "idle"

    @pytest.mark.skipif(sys.platform == "win32", reason="literal `true` command is POSIX-only")
    def test_add_shell_worker_defaults_to_on_drop_and_runs_true_on_assign(self, client):
        c, app = client
        c.emit("worker:add", {
            "slot": 0,
            "type": "shell",
            "fields": {"name": "True worker", "command": "true"},
        })
        layout = get_event(c, "layout:updated")
        assert layout is not None
        worker = layout["slots"][0]
        assert worker["activation"] == "on_drop"

        c.emit("task:create", {"title": "Runs on drop"})
        task = get_event(c, "task:created")
        assert task is not None

        c.emit("task:assign", {"task_id": task["id"], "slot": 0})

        from server.tasks import read_task
        deadline = time.time() + 5
        updated = None
        while time.time() < deadline:
            updated = read_task(app.config["bp_dir"], task["id"])
            if updated and updated.get("status") == "review":
                break
            time.sleep(0.05)

        assert updated["status"] == "review"
        assert updated["assigned_to"] == ""
        layout_path = os.path.join(app.config["bp_dir"], "layout.json")
        final_layout = json.load(open(layout_path))
        assert final_layout["slots"][0]["task_queue"] == []
        assert final_layout["slots"][0]["state"] == "idle"

    def test_add_service_worker_persists_service_defaults(self, client):
        c, _ = client
        c.emit("worker:add", {
            "slot": 0,
            "type": "service",
            "fields": {
                "name": "Preview Server",
                "command": "python3 app.py",
                "pre_start": "git fetch",
                "health_type": "http",
                "health_url": "http://localhost:3000/health",
            },
        })
        layout = get_event(c, "layout:updated")
        assert layout is not None
        worker = layout["slots"][0]
        assert worker["type"] == "service"
        assert worker["name"] == "Preview Server"
        assert worker["activation"] == "manual"
        assert worker["disposition"] == "review"
        assert worker["command"] == "python3 app.py"
        assert worker["command_source"] == "manual"
        assert worker["procfile_process"] == "web"
        assert worker["port"] is None
        assert worker["pre_start"] == "git fetch"
        assert worker["ticket_action"] == "start-if-stopped-else-restart"
        assert worker["health_type"] == "http"
        assert worker["health_url"] == "http://localhost:3000/health"
        assert worker["state"] == "idle"
        assert worker["task_queue"] == []

    def test_add_service_worker_defaults_to_procfile_mode_when_root_procfile_exists(self, client):
        c, app = client
        with open(os.path.join(app.config["workspace"], "Procfile"), "w", encoding="utf-8") as handle:
            handle.write("web: python3 app.py\n")

        c.emit("worker:add", {
            "slot": 0,
            "type": "service",
            "fields": {
                "name": "Procfile Service",
            },
        })
        layout = get_event(c, "layout:updated")
        assert layout is not None
        worker = layout["slots"][0]
        assert worker["type"] == "service"
        assert worker["name"] == "Procfile Service"
        assert worker["command_source"] == "procfile"
        assert worker["procfile_process"] == "web"

    def test_add_service_worker_respects_explicit_manual_command_source_even_with_root_procfile(self, client):
        c, app = client
        with open(os.path.join(app.config["workspace"], "Procfile"), "w", encoding="utf-8") as handle:
            handle.write("web: python3 app.py\n")

        c.emit("worker:add", {
            "slot": 0,
            "type": "service",
            "fields": {
                "name": "Manual Service",
                "command": "python3 custom.py",
                "command_source": "manual",
            },
        })
        layout = get_event(c, "layout:updated")
        assert layout is not None
        worker = layout["slots"][0]
        assert worker["type"] == "service"
        assert worker["name"] == "Manual Service"
        assert worker["command"] == "python3 custom.py"
        assert worker["command_source"] == "manual"

    def test_add_marker_worker_persists_marker_defaults(self, client):
        c, _ = client
        c.emit("worker:add", {
            "slot": 0,
            "type": "marker",
            "fields": {
                "name": "Deploy Marker",
                "note": "staging + release path",
            },
        })
        layout = get_event(c, "layout:updated")
        assert layout is not None
        worker = layout["slots"][0]
        assert worker["type"] == "marker"
        assert worker["name"] == "Deploy Marker"
        assert worker["note"] == "staging + release path"
        assert worker["activation"] == "on_drop"
        assert worker["disposition"] == "review"
        assert worker["icon"] == "square-dot"
        assert worker["color"] == "marker"
        assert worker["state"] == "idle"
        assert worker["task_queue"] == []

    def test_configure_marker_worker_preserves_color_override(self, client):
        c, _ = client
        c.emit("worker:add", {
            "slot": 0,
            "type": "marker",
            "fields": {"name": "Deploy Marker"},
        })
        assert get_event(c, "layout:updated") is not None

        c.emit("worker:configure", {
            "slot": 0,
            "fields": {
                "name": "Deploy Marker",
                "note": "staging + release path",
                "color": "#3a7bd5",
            },
        })
        layout = get_event(c, "layout:updated")

        assert layout is not None
        worker = layout["slots"][0]
        assert worker["type"] == "marker"
        assert worker["note"] == "staging + release path"
        assert worker["color"] == "#3a7bd5"

    def test_add_worker_persists(self, client):
        c, app = client
        c.emit("worker:add", {"slot": 2, "profile": "code-reviewer"})
        c.get_received()

        # Verify layout.json updated
        from server.persistence import read_json
        layout = read_json(os.path.join(app.config["bp_dir"], "layout.json"))
        assert layout["slots"][2]["profile"] == "code-reviewer"

    def test_remove_worker(self, client):
        c, _ = client
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        c.get_received()

        c.emit("worker:remove", {"slot": 0})
        layout = get_event(c, "layout:updated")
        assert layout["slots"] == []

    def test_move_worker(self, client):
        c, _ = client
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        c.get_received()

        c.emit("worker:move", {"from": 0, "to": 3})
        layout = get_event(c, "layout:updated")
        assert layout["slots"][0] is None
        assert layout["slots"][3] is not None
        assert layout["slots"][3]["profile"] == "feature-architect"

    def test_add_worker_at_coordinate(self, client):
        c, _ = client
        c.emit("worker:add", {"coord": {"col": -2, "row": 5}, "profile": "feature-architect"})
        layout = get_event(c, "layout:updated")
        worker = next(s for s in layout["slots"] if s)
        assert worker["profile"] == "feature-architect"
        assert worker["col"] == -2
        assert worker["row"] == 5

    def test_move_worker_to_coordinate_rejects_collision(self, client):
        c, _ = client
        c.emit("worker:add", {"coord": {"col": 0, "row": 0}, "profile": "feature-architect"})
        c.get_received()
        c.emit("worker:add", {"coord": {"col": 1, "row": 0}, "profile": "code-reviewer"})
        c.get_received()

        c.emit("worker:move", {"from": 0, "to_coord": {"col": 1, "row": 0}})
        err = get_event(c, "error")
        assert err is not None
        assert err["code"] == "coordinate_collision"

    def test_move_worker_group_updates_all_members_atomically(self, client):
        c, _ = client
        c.emit("worker:add", {"coord": {"col": 0, "row": 0}, "profile": "feature-architect"})
        c.get_received()
        c.emit("worker:add", {"coord": {"col": 1, "row": 0}, "profile": "code-reviewer"})
        c.get_received()

        c.emit("worker:move_group", {"moves": [
            {"slot": 0, "to_coord": {"col": 4, "row": 2}},
            {"slot": 1, "to_coord": {"col": 5, "row": 2}},
        ]})
        layout = get_event(c, "layout:updated")
        assert layout is not None
        assert layout["slots"][0]["col"] == 4
        assert layout["slots"][0]["row"] == 2
        assert layout["slots"][1]["col"] == 5
        assert layout["slots"][1]["row"] == 2

    def test_move_worker_group_rejects_external_coordinate_collision(self, client):
        c, _ = client
        c.emit("worker:add", {"coord": {"col": 0, "row": 0}, "profile": "feature-architect"})
        c.get_received()
        c.emit("worker:add", {"coord": {"col": 1, "row": 0}, "profile": "code-reviewer"})
        c.get_received()
        c.emit("worker:add", {"coord": {"col": 6, "row": 2}, "profile": "test-writer"})
        c.get_received()

        c.emit("worker:move_group", {"moves": [
            {"slot": 0, "to_coord": {"col": 5, "row": 2}},
            {"slot": 1, "to_coord": {"col": 6, "row": 2}},
        ]})
        err = get_event(c, "error")
        assert err is not None
        assert err["code"] == "coordinate_collision"

    def test_paste_worker_rejects_occupied_coord_without_replace(self, client):
        c, _ = client
        c.emit("worker:add", {"coord": {"col": 0, "row": 0}, "profile": "feature-architect"})
        c.get_received()
        c.emit("worker:paste", {
            "coord": {"col": 0, "row": 0},
            "worker": {"name": "Pasted", "profile": "feature-architect",
                       "agent": "claude", "model": "claude-sonnet-4-6"},
        })
        err = get_event(c, "error")
        assert err is not None
        assert err["code"] == "coordinate_collision"

    def test_paste_worker_replaces_existing_when_replace_flag_set(self, client):
        c, _ = client
        c.emit("worker:add", {"coord": {"col": 2, "row": 3}, "profile": "feature-architect"})
        c.get_received()
        c.emit("worker:paste", {
            "coord": {"col": 2, "row": 3},
            "replace": True,
            "worker": {"name": "Replacement", "profile": "feature-architect",
                       "agent": "claude", "model": "claude-sonnet-4-6"},
        })
        layout = get_event(c, "layout:updated")
        workers_here = [s for s in layout["slots"]
                        if s and s.get("col") == 2 and s.get("row") == 3]
        assert len(workers_here) == 1
        assert workers_here[0]["name"] == "Replacement"

    def test_paste_worker_whitelists_runtime_state(self, client):
        c, _ = client
        c.emit("worker:paste", {
            "coord": {"col": 4, "row": -1},
            "worker": {
                "name": "Copied",
                "profile": "feature-architect",
                "agent": "claude",
                "model": "claude-sonnet-4-6",
                "task_queue": ["secret-task"],
                "state": "working",
                "api_key": "do-not-copy",
            },
        })
        layout = get_event(c, "layout:updated")
        worker = next(s for s in layout["slots"] if s)
        assert worker["name"] == "Copied"
        assert worker["col"] == 4
        assert worker["row"] == -1
        assert worker["task_queue"] == []
        assert worker["state"] == "idle"
        assert "api_key" not in worker

    def test_paste_shell_worker_preserves_type_and_shell_fields(self, client):
        c, _ = client
        c.emit("worker:paste", {
            "coord": {"col": 5, "row": 2},
            "worker": {
                "type": "shell",
                "name": "Shell Copy",
                "activation": "on_drop",
                "disposition": "review",
                "max_retries": 0,
                "command": "true",
                "cwd": "tools",
                "timeout_seconds": 45,
                "ticket_delivery": "env-vars",
                "env": [{"key": "FOO", "value": "bar"}],
                "task_queue": ["secret-task"],
                "state": "working",
            },
        })
        layout = get_event(c, "layout:updated")
        worker = next(s for s in layout["slots"] if s)
        assert worker["type"] == "shell"
        assert worker["name"] == "Shell Copy"
        assert worker["activation"] == "on_drop"
        assert worker["command"] == "true"
        assert worker["cwd"] == "tools"
        assert worker["timeout_seconds"] == 45
        assert worker["ticket_delivery"] == "env-vars"
        assert worker["env"] == [{"key": "FOO", "value": "bar"}]
        assert worker["task_queue"] == []
        assert worker["state"] == "idle"

    def test_paste_service_worker_preserves_type_and_service_fields(self, client):
        c, _ = client
        c.emit("worker:paste", {
            "coord": {"col": 5, "row": 3},
            "worker": {
                "type": "service",
                "name": "Service Copy",
                "activation": "on_drop",
                "disposition": "review",
                "max_retries": 1,
                "command": "python3 app.py",
                "command_source": "procfile",
                "procfile_process": "web",
                "port": 3000,
                "cwd": "server",
                "pre_start": "git fetch",
                "ticket_action": "restart",
                "startup_grace_seconds": 3,
                "startup_timeout_seconds": 90,
                "health_type": "shell",
                "health_command": "curl -fsS http://localhost:3000/health",
                "health_interval_seconds": 10,
                "health_timeout_seconds": 4,
                "health_failure_threshold": 5,
                "stop_timeout_seconds": 8,
                "log_max_bytes": 123456,
                "env": [{"key": "HOSTED_PORT", "value": "3000"}],
                "task_queue": ["secret-task"],
                "state": "working",
            },
        })
        layout = get_event(c, "layout:updated")
        worker = next(s for s in layout["slots"] if s)
        assert worker["type"] == "service"
        assert worker["name"] == "Service Copy"
        assert worker["command"] == "python3 app.py"
        assert worker["command_source"] == "procfile"
        assert worker["procfile_process"] == "web"
        assert worker["port"] == 3000
        assert worker["cwd"] == "server"
        assert worker["pre_start"] == "git fetch"
        assert worker["ticket_action"] == "restart"
        assert worker["startup_grace_seconds"] == 3
        assert worker["startup_timeout_seconds"] == 90
        assert worker["health_type"] == "shell"
        assert worker["health_command"] == "curl -fsS http://localhost:3000/health"
        assert worker["health_interval_seconds"] == 10
        assert worker["health_timeout_seconds"] == 4
        assert worker["health_failure_threshold"] == 5
        assert worker["stop_timeout_seconds"] == 8
        assert worker["log_max_bytes"] == 123456
        assert worker["env"] == [{"key": "HOSTED_PORT", "value": "3000"}]
        assert worker["task_queue"] == []
        assert worker["state"] == "idle"

    def test_duplicate_service_worker_reemits_running_service_state(self, client):
        c, app = client
        c.emit("worker:paste", {
            "coord": {"col": 0, "row": 0},
            "worker": {
                "type": "service",
                "name": "Service",
                "command_source": "procfile",
                "procfile_process": "web",
            },
        })
        c.get_received()

        ws_id = app.config["startup_workspace_id"]
        controller = service_worker_mod.get_controller(app.config["bp_dir"], ws_id, 0, socketio=None)
        with controller._lock:
            controller._state = "running"
            controller._pid = 4242
            controller._started_at = "2026-04-21T00:00:00Z"

        c.emit("worker:duplicate", {"slot": 0})
        events = c.get_received()

        assert any(evt["name"] == "layout:updated" for evt in events)
        service_states = [evt["args"][0] for evt in events if evt["name"] == "service:state"]
        assert service_states
        assert any(
            state.get("slot") == 0
            and state.get("state") == "running"
            and state.get("pid") == 4242
            for state in service_states
        )

    def test_paste_worker_group_pastes_all_members(self, client):
        c, _ = client
        c.emit("worker:paste_group", {"items": [
            {"coord": {"col": 10, "row": 10}, "worker": {
                "name": "Copy",
                "profile": "feature-architect",
                "agent": "claude",
                "model": "claude-sonnet-4-6",
            }},
            {"coord": {"col": 11, "row": 10}, "worker": {
                "name": "Copy",
                "profile": "feature-architect",
                "agent": "claude",
                "model": "claude-sonnet-4-6",
            }},
        ]})
        layout = get_event(c, "layout:updated")
        workers = [s for s in layout["slots"] if s and s.get("row") == 10 and s.get("col") in (10, 11)]
        assert len(workers) == 2
        names = sorted(w["name"] for w in workers)
        assert names[0] == "Copy"
        assert names[1].startswith("Copy")

    def test_paste_worker_group_rejects_on_any_collision(self, client):
        c, app = client
        c.emit("worker:add", {"coord": {"col": 1, "row": 0}, "profile": "feature-architect"})
        c.get_received()
        c.emit("worker:paste_group", {"items": [
            {"coord": {"col": 0, "row": 0}, "worker": {
                "name": "A",
                "profile": "feature-architect",
                "agent": "claude",
                "model": "claude-sonnet-4-6",
            }},
            {"coord": {"col": 1, "row": 0}, "worker": {
                "name": "B",
                "profile": "feature-architect",
                "agent": "claude",
                "model": "claude-sonnet-4-6",
            }},
        ]})
        err = get_event(c, "error")
        assert err is not None
        assert err["code"] == "coordinate_collision"

        from server.persistence import read_json
        layout = read_json(os.path.join(app.config["bp_dir"], "layout.json"))
        assert len([s for s in layout["slots"] if s]) == 1

    def test_configure_worker(self, client):
        c, _ = client
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        c.get_received()

        c.emit("worker:configure", {"slot": 0, "fields": {
            "activation": "on_queue",
            "watch_column": "inbox",
            "max_retries": 3,
        }})
        layout = get_event(c, "layout:updated")
        worker = layout["slots"][0]
        assert worker["activation"] == "on_queue"
        assert worker["watch_column"] == "inbox"
        assert worker["max_retries"] == 3

    def test_configure_at_time_activation(self, client):
        c, app = client
        # Add a worker first
        c.emit("profile:create", {
            "id": "timer-test", "name": "Timer Test",
            "default_agent": "claude", "default_model": "sonnet",
            "color_hint": "blue", "expertise_prompt": "Timer test.",
        })
        c.get_received()
        c.emit("worker:add", {"slot": 0, "profile": "timer-test"})
        c.get_received()

        c.emit("worker:configure", {"slot": 0, "fields": {
            "activation": "at_time",
            "trigger_time": "09:30",
            "trigger_every_day": True,
        }})
        layout = get_event(c, "layout:updated")
        worker = layout["slots"][0]
        assert worker["activation"] == "at_time"
        assert worker["trigger_time"] == "09:30"
        assert worker["trigger_every_day"] is True

    def test_configure_on_interval_activation(self, client):
        c, app = client
        c.emit("profile:create", {
            "id": "interval-test", "name": "Interval Test",
            "default_agent": "claude", "default_model": "sonnet",
            "color_hint": "blue", "expertise_prompt": "Interval test.",
        })
        c.get_received()
        c.emit("worker:add", {"slot": 0, "profile": "interval-test"})
        c.get_received()

        c.emit("worker:configure", {"slot": 0, "fields": {
            "activation": "on_interval",
            "trigger_interval_minutes": 30,
        }})
        layout = get_event(c, "layout:updated")
        worker = layout["slots"][0]
        assert worker["activation"] == "on_interval"
        assert worker["trigger_interval_minutes"] == 30

    def test_add_worker_invalid_profile(self, client):
        c, _ = client
        c.emit("worker:add", {"slot": 0, "profile": "nonexistent"})
        err = get_event(c, "error")
        assert err is not None
        assert "not found" in err["message"]

    def test_configure_worker_disposition(self, client):
        """Worker disposition can be set to a worker: target."""
        c, _ = client
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        c.get_received()

        c.emit("worker:configure", {"slot": 0, "fields": {
            "disposition": "worker:Code Reviewer",
        }})
        layout = get_event(c, "layout:updated")
        assert layout["slots"][0]["disposition"] == "worker:Code Reviewer"

    def test_configure_worker_normalizes_legacy_claude_haiku_model(self, client):
        c, _ = client
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        c.get_received()

        c.emit("worker:configure", {"slot": 0, "fields": {
            "model": "claude-haiku-4-6",
        }})
        layout = get_event(c, "layout:updated")
        assert layout["slots"][0]["model"] == "claude-haiku-4-5-20251001"

    def test_new_ai_workers_default_to_untrusted_mode(self, client):
        c, _ = client
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        layout = get_event(c, "layout:updated")
        assert layout["slots"][0]["trust_mode"] == "untrusted"

    def test_configure_untrusted_worker_disables_auto_actions(self, client):
        c, _ = client
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        c.get_received()

        c.emit("worker:configure", {"slot": 0, "fields": {
            "trust_mode": "untrusted",
            "auto_commit": True,
            "auto_pr": True,
            "use_worktree": True,
        }})
        layout = get_event(c, "layout:updated")
        worker = layout["slots"][0]
        assert worker["trust_mode"] == "untrusted"
        assert worker["use_worktree"] is True
        assert worker["auto_commit"] is False
        assert worker["auto_pr"] is False


class TestChatEvents:
    def test_chat_uses_adapter_prepared_env_and_cleans_it_up(self, client):
        c, _app = client
        adapter = ChatEnvAdapter()
        register_adapter("chat-env-mock", adapter)

        c.emit("chat:send", {
            "sessionId": "session-env-1",
            "provider": "chat-env-mock",
            "model": "mock-model",
            "message": "hello",
        })

        output = []
        deadline = time.time() + 3.0
        done = False
        while time.time() < deadline and not done:
            for evt in c.get_received():
                if evt["name"] == "chat:output":
                    output.append(evt["args"][0])
                if evt["name"] == "chat:done":
                    done = True
            if not done:
                time.sleep(0.05)

        assert done
        assert any("prepared" in line for evt in output for line in evt.get("lines", []))
        assert adapter.cleanup_path
        assert not os.path.exists(adapter.cleanup_path)

    def test_chat_logs_structured_usage_and_tokens(self, client):
        c, app = client
        register_adapter("chat-usage-mock", ChatUsageAdapter())

        c.emit("chat:send", {
            "sessionId": "session-usage-1",
            "provider": "chat-usage-mock",
            "model": "mock-model",
            "message": "hello",
        })

        deadline = time.time() + 3.0
        done = False
        while time.time() < deadline and not done:
            for evt in c.get_received():
                if evt["name"] == "chat:done":
                    done = True
                    break
            if not done:
                time.sleep(0.05)

        assert done, "chat:done not received"

        from server.tasks import list_tasks

        tasks = list_tasks(app.config["bp_dir"])
        chat_tasks = [t for t in tasks if "chat" in (t.get("tags") or [])]
        assert chat_tasks
        task = chat_tasks[0]
        assert task.get("tokens") == 18
        assert isinstance(task.get("usage"), list)
        assert len(task["usage"]) == 1
        usage = task["usage"][0]
        assert usage["source"] == "chat"
        assert usage["provider"] == "chat-usage-mock"
        assert usage["model"] == "mock-model"
        assert usage["input_tokens"] == 11
        assert usage["output_tokens"] == 7
        assert usage["cached_input_tokens"] == 3
        assert isinstance(task.get("tokens_by_provider_model"), list)
        assert len(task["tokens_by_provider_model"]) == 1
        breakdown = task["tokens_by_provider_model"][0]
        assert breakdown["provider"] == "chat-usage-mock"
        assert breakdown["model"] == "mock-model"
        assert breakdown["input_tokens"] == 11
        assert breakdown["output_tokens"] == 7
        assert breakdown["cached_input_tokens"] == 3
        assert breakdown["tokens"] == 18

    def test_chat_emits_error_on_provider_failure(self, client):
        c, _ = client
        register_adapter("chat-failing-mock", ChatFailingAdapter())

        c.emit("chat:send", {
            "sessionId": "session-fail-1",
            "provider": "chat-failing-mock",
            "model": "mock-model",
            "message": "hello",
        })

        deadline = time.time() + 3.0
        error = None
        done = False
        while time.time() < deadline and error is None:
            for evt in c.get_received():
                if evt["name"] == "chat:error":
                    error = evt["args"][0]
                if evt["name"] == "chat:done":
                    done = True
            if error is None:
                time.sleep(0.05)

        assert error is not None, "chat:error not received"
        assert "Requested entity was not found" in error["message"]
        assert done is False

    def test_chat_tab_open_is_broadcast_to_other_clients(self):
        with tempfile.TemporaryDirectory(prefix="bullpen_chat_tabs_") as ws:
            app = create_app(ws, no_browser=True)
            c1 = socketio.test_client(app)
            c2 = socketio.test_client(app)
            c1.get_received()
            c2.get_received()

            ws_id = app.config["startup_workspace_id"]
            session_id = "shared-live-session"

            c1.emit("chat:tab:open", {
                "workspaceId": ws_id,
                "id": session_id,
                "sessionId": session_id,
                "label": "Live Agent",
            })

            deadline = time.time() + 3.0
            seen_c1 = False
            seen_c2 = False
            while time.time() < deadline and not (seen_c1 and seen_c2):
                for evt in c1.get_received():
                    if evt["name"] != "chat:tabs":
                        continue
                    tabs = evt["args"][0].get("tabs") or []
                    if any(t.get("sessionId") == session_id for t in tabs):
                        seen_c1 = True
                for evt in c2.get_received():
                    if evt["name"] != "chat:tabs":
                        continue
                    tabs = evt["args"][0].get("tabs") or []
                    if any(t.get("sessionId") == session_id for t in tabs):
                        seen_c2 = True
                if not (seen_c1 and seen_c2):
                    time.sleep(0.05)

            assert seen_c1
            assert seen_c2
            c1.disconnect()
            c2.disconnect()

    def test_chat_user_message_is_broadcast_to_other_clients(self):
        with tempfile.TemporaryDirectory(prefix="bullpen_chat_user_broadcast_") as ws:
            app = create_app(ws, no_browser=True)
            c1 = socketio.test_client(app)
            c2 = socketio.test_client(app)
            c1.get_received()
            c2.get_received()

            register_adapter("chat-usage-mock", ChatUsageAdapter())

            ws_id = app.config["startup_workspace_id"]
            session_id = "shared-live-session"
            message = "hello from window one"

            c1.emit("chat:send", {
                "workspaceId": ws_id,
                "sessionId": session_id,
                "provider": "chat-usage-mock",
                "model": "mock-model",
                "message": message,
            })

            user_evt = _wait_for_event(c2, "chat:user")
            assert user_evt is not None
            assert user_evt["workspaceId"] == ws_id
            assert user_evt["sessionId"] == session_id
            assert user_evt["message"] == message
            assert user_evt["senderSid"]
            assert _wait_for_event(c1, "chat:done")

            c1.disconnect()
            c2.disconnect()

    def test_chat_tabs_are_workspace_room_isolated(self):
        with tempfile.TemporaryDirectory(prefix="bullpen_chat_tabs_room_") as ws:
            app = create_app(ws, no_browser=True)
            c1 = socketio.test_client(app)
            c2 = socketio.test_client(app)
            c1.get_received()
            c2.get_received()

            with tempfile.TemporaryDirectory(prefix="bullpen_chat_tabs_room_parent_") as parent:
                path = os.path.join(parent, "room-isolated-project")
                c1.emit("project:new", {"path": path})
                events = c1.get_received()
                project_updates = [evt for evt in events if evt["name"] == "projects:updated"]
                listed = project_updates[-1]["args"][0]
                ws_b = next(p["id"] for p in listed if p["name"] == "room-isolated-project")

                c2.get_received()  # drain projects update

                c1.emit("chat:tab:open", {
                    "workspaceId": ws_b,
                    "id": "ws-b-session",
                    "sessionId": "ws-b-session",
                    "label": "Live Agent",
                })
                c1.get_received()

                leaked = False
                deadline = time.time() + 0.4
                while time.time() < deadline:
                    for evt in c2.get_received():
                        if evt["name"] == "chat:tabs" and evt["args"][0].get("workspaceId") == ws_b:
                            leaked = True
                    if leaked:
                        break
                    time.sleep(0.05)
                assert leaked is False

                c2.emit("project:join", {"workspaceId": ws_b})
                c2.get_received()  # drain state:init
                c2.emit("chat:tabs:request", {"workspaceId": ws_b})
                tabs_evt = _wait_for_event(c2, "chat:tabs")
                assert tabs_evt is not None
                assert tabs_evt["workspaceId"] == ws_b
                assert any(t.get("sessionId") == "ws-b-session" for t in (tabs_evt.get("tabs") or []))

            c1.disconnect()
            c2.disconnect()


class TestConfigEvents:
    def test_config_update(self, client):
        c, app = client
        c.emit("config:update", {"name": "My Team", "theme": "nord"})
        config = get_event(c, "config:updated")
        assert config is not None
        assert config["name"] == "My Team"
        assert config["theme"] == "nord"

    def test_pause_and_resume_worker_automation_events(self, client):
        c, app = client
        c.emit("workers:pause_automation", {})
        config = get_event(c, "config:updated")
        assert config is not None
        assert config["worker_automation_paused"] is True

        c.emit("workers:resume_automation", {})
        config = get_event(c, "config:updated")
        assert config is not None
        assert config["worker_automation_paused"] is False

    def test_stop_line_pauses_automation_even_without_active_workers(self, client):
        c, app = client
        c.emit("workers:stop_line", {})
        config = get_event(c, "config:updated")
        assert config is not None
        assert config["worker_automation_paused"] is True

    def test_prompt_update(self, client):
        c, app = client
        c.emit("prompt:update", {"type": "workspace", "content": "This is a Flask project."})
        result = get_event(c, "prompt:updated")
        assert result is not None
        assert result["type"] == "workspace"
        assert result["content"] == "This is a Flask project."

        # Verify file
        path = os.path.join(app.config["bp_dir"], "workspace_prompt.md")
        assert open(path).read() == "This is a Flask project."

    def test_prompt_update_rejects_bullpen_type(self, client):
        c, app = client
        c.emit("prompt:update", {"type": "bullpen", "content": "Focus on tests."})
        events = c.get_received()
        result = next((evt["args"][0] for evt in events if evt["name"] == "prompt:updated"), None)
        error = next((evt["args"][0] for evt in events if evt["name"] == "error"), None)
        assert result is None
        assert error is not None
        assert error["message"] == "prompt:update requires type 'workspace'"
        assert not os.path.exists(os.path.join(app.config["bp_dir"], "bullpen_prompt.md"))

    def test_profile_create(self, client):
        c, _ = client
        c.emit("profile:create", {
            "id": "custom",
            "name": "Custom",
            "default_agent": "claude",
            "default_model": "sonnet",
            "color_hint": "pink",
            "expertise_prompt": "Custom worker.",
        })
        profiles = get_event(c, "profiles:updated")
        assert profiles is not None
        ids = {p["id"] for p in profiles}
        assert "custom" in ids


class TestProjectEvents:
    def test_project_new_writes_current_mcp_runtime_config(self, client):
        c, app = client
        startup_config_path = os.path.join(app.config["bp_dir"], "config.json")
        with open(startup_config_path, "r", encoding="utf-8") as f:
            startup_config = json.load(f)
        startup_token = mcp_auth.read_workspace_mcp_token(app.config["bp_dir"])
        with tempfile.TemporaryDirectory(prefix="bullpen_new_project_parent_") as parent:
            path = os.path.join(parent, "new-mcp-project")
            c.emit("project:new", {"path": path})
            events = c.get_received()
            project_updates = [evt for evt in events if evt["name"] == "projects:updated"]
            assert project_updates

            config_path = os.path.join(path, ".bullpen", "config.json")
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            assert config["server_host"] == app.config["host"]
            assert config["server_port"] == app.config["port"]
            assert "mcp_token" not in config
            assert mcp_auth.read_workspace_mcp_token(os.path.join(path, ".bullpen"))
            assert mcp_auth.read_workspace_mcp_token(os.path.join(path, ".bullpen")) != startup_token

    def test_mcp_client_is_scoped_to_its_workspace(self, client):
        c, app = client
        with tempfile.TemporaryDirectory(prefix="bullpen_new_project_parent_") as parent:
            path = os.path.join(parent, "mcp-scoped-project")
            c.emit("project:new", {"path": path})
            events = c.get_received()
            project_updates = [evt for evt in events if evt["name"] == "projects:updated"]
            listed = project_updates[-1]["args"][0]
            startup_ws_id = app.config["startup_workspace_id"]
            other_ws_id = next(p["id"] for p in listed if p["name"] == "mcp-scoped-project")

            config_path = os.path.join(path, ".bullpen", "config.json")
            token = mcp_auth.read_workspace_mcp_token(os.path.join(path, ".bullpen"))

            mcp_client = socketio.test_client(app, auth={"mcp_token": token})
            try:
                assert mcp_client.is_connected() is True
                received = mcp_client.get_received()
                state_events = [evt for evt in received if evt["name"] == "state:init"]
                project_events = [evt for evt in received if evt["name"] == "projects:updated"]
                assert [evt["args"][0]["workspaceId"] for evt in state_events] == [other_ws_id]
                assert project_events == []

                mcp_client.emit("project:join", {"workspaceId": startup_ws_id})
                err = get_event(mcp_client, "error")
                assert err is not None
                assert "only authorized for workspace" in err["message"]

                mcp_client.emit("project:list")
                listed_projects = get_event(mcp_client, "projects:updated")
                assert [project["id"] for project in listed_projects] == [other_ws_id]
            finally:
                mcp_client.disconnect()

    def test_chat_session_ids_are_scoped_by_workspace(self, client):
        c, app = client
        register_adapter("chat-usage-mock", ChatUsageAdapter())

        with tempfile.TemporaryDirectory(prefix="bullpen_new_project_parent_") as parent:
            path = os.path.join(parent, "new-chat-project")
            c.emit("project:new", {"path": path})
            events = c.get_received()
            project_updates = [evt for evt in events if evt["name"] == "projects:updated"]
            listed = project_updates[-1]["args"][0]
            ws_b = next(p["id"] for p in listed if p["name"] == "new-chat-project")

            session_id = "same-browser-session-id"
            ws_a = app.config["startup_workspace_id"]

            c.emit("chat:send", {
                "workspaceId": ws_a,
                "sessionId": session_id,
                "provider": "chat-usage-mock",
                "model": "mock-model",
                "message": "hello from A",
            })
            assert _wait_for_event(c, "chat:done")

            c.emit("chat:send", {
                "workspaceId": ws_b,
                "sessionId": session_id,
                "provider": "chat-usage-mock",
                "model": "mock-model",
                "message": "hello from B",
            })
            assert _wait_for_event(c, "chat:done")

            from server.tasks import list_tasks

            tasks_a = list_tasks(app.config["bp_dir"])
            tasks_b = list_tasks(os.path.join(path, ".bullpen"))
            chat_a = [t for t in tasks_a if "chat" in (t.get("tags") or [])]
            chat_b = [t for t in tasks_b if "chat" in (t.get("tags") or [])]

            assert len(chat_a) == 1
            assert len(chat_b) == 1
            assert "hello from A" in (chat_a[0].get("body") or "")
            assert "hello from B" in (chat_b[0].get("body") or "")

    def test_project_new_creates_empty_directory_and_registers(self, client):
        c, _ = client
        with tempfile.TemporaryDirectory(prefix="bullpen_new_project_parent_") as parent:
            path = os.path.join(parent, "new-empty-project")
            c.emit("project:new", {"path": path})
            events = c.get_received()

            state_inits = [evt for evt in events if evt["name"] == "state:init"]
            project_updates = [evt for evt in events if evt["name"] == "projects:updated"]
            errors = [evt for evt in events if evt["name"] == "error"]

            assert not errors
            assert os.path.isdir(path)
            assert os.path.isdir(os.path.join(path, ".bullpen"))
            assert state_inits
            assert project_updates

            listed = project_updates[-1]["args"][0]
            assert any(p["name"] == "new-empty-project" for p in listed)
            assert all("path" not in p for p in listed)

    def test_new_project_client_receives_task_created_without_refresh(self, client):
        c, _ = client
        with tempfile.TemporaryDirectory(prefix="bullpen_new_project_parent_") as parent:
            path = os.path.join(parent, "new-empty-project")
            c.emit("project:new", {"path": path})
            events = c.get_received()
            state_inits = [evt for evt in events if evt["name"] == "state:init"]
            ws_id = state_inits[-1]["args"][0]["workspaceId"]

            c.emit("task:create", {
                "workspaceId": ws_id,
                "title": "Appears without refresh",
                "type": "task",
                "priority": "normal",
                "tags": [],
            })
            created = get_event(c, "task:created")

            assert created is not None
            assert created["title"] == "Appears without refresh"
            assert created["workspaceId"] == ws_id

    def test_project_remove_unregisters_but_keeps_workspace_files(self, client):
        c, _ = client
        with tempfile.TemporaryDirectory(prefix="bullpen_remove_project_parent_") as parent:
            path = os.path.join(parent, "remove-me-project")
            c.emit("project:new", {"path": path})
            events = c.get_received()
            state_inits = [evt for evt in events if evt["name"] == "state:init"]
            ws_id = state_inits[-1]["args"][0]["workspaceId"]

            c.emit("task:create", {"workspaceId": ws_id, "title": "Keep this task on disk"})
            created = get_event(c, "task:created")
            assert created is not None
            task_path = os.path.join(path, ".bullpen", "tasks", f"{created['id']}.md")
            assert os.path.exists(task_path)

            c.emit("project:remove", {"workspaceId": ws_id})
            remove_events = c.get_received()
            removed = [evt for evt in remove_events if evt["name"] == "project:removed"]
            updates = [evt for evt in remove_events if evt["name"] == "projects:updated"]
            assert removed
            assert removed[-1]["args"][0]["workspaceId"] == ws_id
            assert updates
            listed_after = updates[-1]["args"][0]
            assert all(p["id"] != ws_id for p in listed_after)

            # Unregister only: no project files are deleted.
            assert os.path.isdir(os.path.join(path, ".bullpen"))
            assert os.path.exists(task_path)
