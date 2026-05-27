"""End-to-end workflow tests via socket.io test client."""

import os
import tempfile
import time
import threading
import json

import pytest

from server.app import create_app, socketio
from server.agents import register_adapter
from server.validation import MAX_TITLE
from tests.conftest import MockAdapter
from server import workers as workers_mod


def _wait_for_worker_threads(timeout=3.0):
    """Let daemon worker threads finish their final filesystem writes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with workers_mod._process_lock:
            no_processes = not workers_mod._processes
        with workers_mod._deferred_start_lock:
            active_deferred = [t for t in workers_mod._deferred_start_threads if t.is_alive()]
        if no_processes and not active_deferred:
            return True
        time.sleep(0.02)
    return False


def _stop_app_background(app):
    manager = app.config.get("manager")
    if not manager:
        return
    for ws in manager.all_workspaces():
        scheduler = getattr(ws, "scheduler", None)
        if scheduler:
            scheduler.stop()


@pytest.fixture
def client():
    """Create a Flask-SocketIO test client with mock adapter."""
    with tempfile.TemporaryDirectory(prefix="bullpen_e2e_") as ws:
        app = create_app(ws, no_browser=True)
        # Register mock adapter
        register_adapter("mock", MockAdapter(output="Task completed successfully"))
        client = socketio.test_client(app)
        client.get_received()
        yield client, app
        client.disconnect()
        assert _wait_for_worker_threads()
        _stop_app_background(app)


def get_event(client, name):
    for evt in client.get_received():
        if evt["name"] == name:
            return evt["args"][0]
    return None


def get_all_events(client, name):
    return [evt["args"][0] for evt in client.get_received() if evt["name"] == name]


class TestHappyPath:
    """Create task → assign to worker → run → output → complete."""

    def test_create_assign_flow(self, client):
        c, app = client

        # 1. Create a task
        c.emit("task:create", {"title": "Implement login", "type": "feature", "priority": "high"})
        task = get_event(c, "task:created")
        assert task is not None
        assert task["title"] == "Implement login"
        assert task["status"] == "inbox"
        task_id = task["id"]

        # 2. Add a worker
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        layout = get_event(c, "layout:updated")
        assert layout["slots"][0] is not None
        assert layout["slots"][0]["name"] == "Feature Architect"

        # 3. Assign task to worker
        c.emit("task:assign", {"task_id": task_id, "slot": 0})
        received = c.get_received()
        # Should get task:updated (status=assigned) and layout:updated (task in queue)
        events = {e["name"]: e["args"][0] for e in received}
        assert "task:updated" in events
        assert events["task:updated"]["status"] in ("assigned", "in_progress", "in-progress")
        assert "layout:updated" in events
        assert task_id in events["layout:updated"]["slots"][0]["task_queue"]

    def test_full_create_to_multiple_tasks(self, client):
        c, _ = client

        # Create multiple tasks
        for i in range(3):
            c.emit("task:create", {"title": f"Task {i}", "type": "task"})
        c.get_received()

        # Move one to different status
        c.emit("task:create", {"title": "To be moved", "type": "bug", "priority": "urgent"})
        task = get_event(c, "task:created")
        c.emit("task:update", {"id": task["id"], "status": "in-progress"})
        updated = get_event(c, "task:updated")
        assert updated["status"] == "in-progress"


def test_create_app_uses_configured_startup_workspace_name(tmp_workspace, monkeypatch):
    monkeypatch.setenv("BULLPEN_WORKSPACE_NAME", "repo-name")

    app = create_app(tmp_workspace, no_browser=True)
    manager = app.config["manager"]
    projects = manager.list_projects(include_path=False)

    assert len(projects) == 1
    assert projects[0]["name"] == "repo-name"


class TestTeamWorkflow:
    """Load team → assign tasks → save as new team."""

    def test_team_save_load(self, client):
        c, app = client

        # Add workers
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        c.get_received()
        c.emit("worker:add", {"slot": 1, "profile": "code-reviewer"})
        c.get_received()

        # Save as team
        c.emit("team:save", {"name": "review-team"})
        teams = get_event(c, "teams:updated")
        assert "review-team" in teams

        # Remove all workers
        c.emit("worker:remove", {"slot": 0})
        c.get_received()
        c.emit("worker:remove", {"slot": 1})
        c.get_received()

        # Load team back
        c.emit("team:load", {"name": "review-team"})
        layout = get_event(c, "layout:updated")
        assert layout["slots"][0] is not None
        assert layout["slots"][1] is not None
        assert layout["slots"][0]["name"] == "Feature Architect"


class TestSecurityValidation:
    """XSS in title/body, path traversal slug, oversized payload."""

    def test_xss_in_title(self, client):
        c, _ = client
        c.emit("task:create", {"title": '<script>alert("xss")</script>'})
        task = get_event(c, "task:created")
        # Title should be stored as-is (rendering sanitized by markdown-it html:false)
        assert task["title"] == '<script>alert("xss")</script>'

    def test_path_traversal_id_rejected(self, client):
        c, _ = client
        c.emit("task:update", {"id": "../../../etc/passwd", "title": "hacked"})
        err = get_event(c, "error")
        assert err is not None
        assert "Invalid" in err["message"]

    def test_oversized_title_rejected(self, client):
        c, _ = client
        c.emit("task:create", {"title": "x" * (MAX_TITLE + 1)})
        err = get_event(c, "error")
        assert err is not None
        assert "exceeds" in err["message"]

    def test_invalid_priority_rejected(self, client):
        c, _ = client
        c.emit("task:create", {"priority": "super-mega-critical"})
        err = get_event(c, "error")
        assert err is not None
        assert "Invalid priority" in err["message"]

    def test_invalid_agent_rejected(self, client):
        c, _ = client
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        c.get_received()
        c.emit("worker:configure", {"slot": 0, "fields": {"agent": "chatgpt"}})
        err = get_event(c, "error")
        assert err is not None
        assert "Invalid agent" in err["message"]


class TestSchemaCompat:
    """Fixtures with missing/extra fields."""

    def test_create_with_extra_fields(self, client):
        c, _ = client
        c.emit("task:create", {
            "title": "Valid task",
            "type": "task",
            "unknown_field": "should be ignored",
            "another_bad": 42,
        })
        task = get_event(c, "task:created")
        assert task is not None
        assert task["title"] == "Valid task"

    def test_create_with_missing_fields(self, client):
        c, _ = client
        c.emit("task:create", {})
        task = get_event(c, "task:created")
        assert task is not None
        assert task["title"] == "Untitled"
        assert task["status"] == "inbox"

    def test_update_only_specified_fields(self, client):
        c, _ = client
        c.emit("task:create", {"title": "Original", "priority": "high"})
        task = get_event(c, "task:created")

        c.emit("task:update", {"id": task["id"], "title": "Changed"})
        updated = get_event(c, "task:updated")
        assert updated["title"] == "Changed"
        assert updated["priority"] == "high"  # unchanged


class TestDualClient:
    """Two tabs — create in one, appears in other."""

    def test_dual_client_sync(self):
        with tempfile.TemporaryDirectory(prefix="bullpen_dual_") as ws:
            app = create_app(ws, no_browser=True)
            c1 = socketio.test_client(app)
            c1.get_received()
            c2 = socketio.test_client(app)
            c2.get_received()

            # Client 1 creates a task
            c1.emit("task:create", {"title": "Shared task"})
            c1.get_received()

            # Client 2 should see it (broadcast)
            task = get_event(c2, "task:created")
            assert task is not None
            assert task["title"] == "Shared task"

            c1.disconnect()
            c2.disconnect()


class TestMultiProjectStartup:
    """Server startup should hydrate all projects in the registry."""

    def test_connect_receives_state_for_all_registered_projects(self, tmp_path):
        global_dir = str(tmp_path / "global")
        os.makedirs(global_dir, exist_ok=True)

        ws_a = str(tmp_path / "workspace_a")
        ws_b = str(tmp_path / "workspace_b")
        os.makedirs(ws_a, exist_ok=True)
        os.makedirs(ws_b, exist_ok=True)

        app = create_app(ws_a, no_browser=True, global_dir=global_dir)

        # Seed another persisted project as if it was added in a previous run.
        projects_path = os.path.join(global_dir, "projects.json")
        with open(projects_path, "r") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and "projects" in raw:
            raw["projects"].append({
                "id": "ws-b",
                "path": os.path.realpath(ws_b),
                "name": "workspace_b",
            })
        else:
            raw.append({
                "id": "ws-b",
                "path": os.path.realpath(ws_b),
                "name": "workspace_b",
            })
        with open(projects_path, "w") as f:
            json.dump(raw, f, indent=2)

        # Restart app; both projects should be activated internally, but the
        # browser bootstrap only receives the startup workspace state.
        app = create_app(ws_a, no_browser=True, global_dir=global_dir)
        c = socketio.test_client(app)
        received = c.get_received()
        c.disconnect()

        init_events = [evt for evt in received if evt["name"] == "state:init"]
        projects_events = [evt for evt in received if evt["name"] == "projects:updated"]

        assert len(init_events) == 1
        assert init_events[0]["args"][0]["workspace"] == os.path.basename(ws_a)
        assert projects_events
        listed = projects_events[-1]["args"][0]
        listed_names = {p["name"] for p in listed}
        assert os.path.basename(ws_a) in listed_names
        assert os.path.basename(ws_b) in listed_names
        assert all("path" not in p for p in listed)

    def test_connect_hides_unavailable_projects_when_configured(self, tmp_path, monkeypatch):
        global_dir = str(tmp_path / "global")
        os.makedirs(global_dir, exist_ok=True)
        monkeypatch.setenv("BULLPEN_HIDE_UNAVAILABLE_PROJECTS", "1")

        ws_a = str(tmp_path / "workspace_a")
        ws_b = str(tmp_path / "workspace_b")
        os.makedirs(ws_a, exist_ok=True)
        os.makedirs(ws_b, exist_ok=True)

        app = create_app(ws_a, no_browser=True, global_dir=global_dir)

        projects_path = os.path.join(global_dir, "projects.json")
        with open(projects_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        raw["projects"].append({
            "id": "ws-b",
            "path": os.path.realpath(ws_b),
            "name": "workspace_b",
        })
        with open(projects_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)
        os.rmdir(ws_b)

        app = create_app(ws_a, no_browser=True, global_dir=global_dir)
        c = socketio.test_client(app)
        received = c.get_received()
        c.disconnect()

        projects_events = [evt for evt in received if evt["name"] == "projects:updated"]
        assert projects_events
        listed = projects_events[-1]["args"][0]
        listed_names = {p["name"] for p in listed}
        assert os.path.basename(ws_a) in listed_names
        assert "workspace_b" not in listed_names

    def test_connect_selects_startup_project_even_if_registry_lists_another_first(self, tmp_path):
        global_dir = str(tmp_path / "global")
        os.makedirs(global_dir, exist_ok=True)

        ws_a = str(tmp_path / "workspace_a")
        ws_b = str(tmp_path / "workspace_b")
        os.makedirs(ws_a, exist_ok=True)
        os.makedirs(ws_b, exist_ok=True)

        projects_path = os.path.join(global_dir, "projects.json")
        with open(projects_path, "w", encoding="utf-8") as f:
            json.dump({
                "version": 1,
                "projects": [
                    {"id": "ws-b", "path": os.path.realpath(ws_b), "name": "workspace_b"},
                    {"id": "ws-a", "path": os.path.realpath(ws_a), "name": "workspace_a"},
                ],
            }, f, indent=2)

        app = create_app(ws_a, no_browser=True, global_dir=global_dir)
        assert app.config["startup_workspace_id"] == "ws-a"

        c = socketio.test_client(app)
        received = c.get_received()
        c.disconnect()

        init_events = [evt for evt in received if evt["name"] == "state:init"]
        assert len(init_events) == 1
        startup_state = init_events[0]["args"][0]
        assert startup_state["workspaceId"] == "ws-a"
        assert startup_state["workspace"] == "workspace_a"


class TestWorkerManagement:
    """Worker add/remove/move/configure flows."""

    def test_move_worker(self, client):
        c, _ = client
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        c.get_received()

        c.emit("worker:move", {"from": 0, "to": 5})
        layout = get_event(c, "layout:updated")
        assert layout["slots"][0] is None
        assert layout["slots"][5] is not None
        assert layout["slots"][5]["name"] == "Feature Architect"

    def test_configure_worker(self, client):
        c, _ = client
        c.emit("worker:add", {"slot": 0, "profile": "feature-architect"})
        c.get_received()

        c.emit("worker:configure", {"slot": 0, "fields": {
            "name": "Custom Name",
            "agent": "codex",
            "disposition": "done",
        }})
        layout = get_event(c, "layout:updated")
        worker = layout["slots"][0]
        assert worker["name"] == "Custom Name"
        assert worker["agent"] == "codex"
        assert worker["disposition"] == "done"

    def test_remove_nonexistent_worker(self, client):
        c, _ = client
        c.emit("worker:remove", {"slot": 99})
        err = get_event(c, "error")
        assert err is not None


class TestTaskLifecycle:
    """Delete, clear output, status transitions."""

    def test_delete_task(self, client):
        c, _ = client
        c.emit("task:create", {"title": "To delete"})
        task = get_event(c, "task:created")

        c.emit("task:delete", {"id": task["id"]})
        deleted = get_event(c, "task:deleted")
        assert deleted["id"] == task["id"]

    def test_clear_output(self, client):
        c, _ = client
        c.emit("task:create", {"title": "Has output"})
        task = get_event(c, "task:created")

        c.emit("task:clear_output", {"id": task["id"]})
        updated = get_event(c, "task:updated")
        assert updated is not None

    def test_status_transitions(self, client):
        c, _ = client
        c.emit("task:create", {"title": "Workflow task"})
        task = get_event(c, "task:created")
        assert task["status"] == "inbox"

        for status in ["assigned", "in-progress", "review", "done"]:
            c.emit("task:update", {"id": task["id"], "status": status})
            updated = get_event(c, "task:updated")
            assert updated["status"] == status
