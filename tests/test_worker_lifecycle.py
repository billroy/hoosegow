"""Phase 4 parity tests: shared lifecycle for AI and Shell workers.

These tests pin down the contract that `start_worker` is a pure
worker-type dispatcher and that both AI and Shell workers flow through
the same `_begin_run` / `_commit_run_start` lifecycle helpers, with a
single retry/disposition pipeline.
"""

import os
import shlex
import sys
import time

import pytest

import server.workers as workers_mod
from server.init import init_workspace
from server.persistence import read_json, write_json
from server.tasks import create_task, read_task
from server.workers import (
    _begin_run,
    _commit_run_start,
    _load_layout,
    _on_agent_error,
    _on_agent_success,
    _processes,
    assign_task,
    start_worker,
)
from server.agents import register_adapter
from tests.conftest import MockAdapter


class CapturingSocket:
    def __init__(self):
        self.events = []

    def emit(self, event, payload, to=None):
        self.events.append((event, payload, to))


def _wait_idle(bp_dir, slot=0, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with workers_mod._process_lock:
            running = bool(_processes)
        layout = _load_layout(bp_dir)
        worker = layout["slots"][slot]
        if not running and worker.get("state") == "idle":
            return
        time.sleep(0.03)
    raise AssertionError("worker did not return to idle")


@pytest.fixture
def bp_dir(tmp_workspace):
    bp = init_workspace(tmp_workspace)
    register_adapter("mock", MockAdapter(output="ai ok"))
    yield bp


def _ai_worker(**overrides):
    worker = {
        "type": "ai",
        "row": 0, "col": 0,
        "name": "AI Worker",
        "agent": "mock",
        "model": "mock-model",
        "activation": "manual",
        "disposition": "review",
        "watch_column": None,
        "expertise_prompt": "",
        "max_retries": 0,
        "task_queue": [],
        "state": "idle",
    }
    worker.update(overrides)
    return worker


def _shell_worker(**overrides):
    worker = {
        "type": "shell",
        "row": 0, "col": 0,
        "name": "Shell Worker",
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
    return worker


def _install_slots(bp_dir, slots):
    layout = read_json(os.path.join(bp_dir, "layout.json"))
    layout["slots"] = slots
    write_json(os.path.join(bp_dir, "layout.json"), layout)


def _python_command(code):
    return f"{sys.executable} -c {shlex.quote(code)}"


class TestStartWorkerIsPureDispatcher:
    """`start_worker` must only route; all lifecycle work belongs to _run_*."""

    def test_ai_type_dispatches_to_ai_backend(self, bp_dir, monkeypatch):
        _install_slots(bp_dir, [_ai_worker()])
        calls = []
        monkeypatch.setattr(workers_mod, "_run_ai_worker",
                            lambda *a, **kw: calls.append(("ai", a, kw)))
        monkeypatch.setattr(workers_mod, "_run_shell_worker",
                            lambda *a, **kw: calls.append(("shell", a, kw)))
        start_worker(bp_dir, 0)
        assert [c[0] for c in calls] == ["ai"]

    def test_shell_type_dispatches_to_shell_backend(self, bp_dir, monkeypatch):
        _install_slots(bp_dir, [_shell_worker()])
        calls = []
        monkeypatch.setattr(workers_mod, "_run_ai_worker",
                            lambda *a, **kw: calls.append(("ai", a, kw)))
        monkeypatch.setattr(workers_mod, "_run_shell_worker",
                            lambda *a, **kw: calls.append(("shell", a, kw)))
        start_worker(bp_dir, 0)
        assert [c[0] for c in calls] == ["shell"]

    def test_marker_type_dispatches_to_marker_backend(self, bp_dir, monkeypatch):
        _install_slots(bp_dir, [{
            "type": "marker", "row": 0, "col": 0, "name": "Marker",
            "note": "", "activation": "manual", "disposition": "review",
            "watch_column": None, "max_retries": 0, "task_queue": [], "state": "idle",
            "icon": "square-dot", "color": "marker",
        }])
        calls = []
        monkeypatch.setattr(workers_mod, "_run_ai_worker",
                            lambda *a, **kw: calls.append(("ai", a, kw)))
        monkeypatch.setattr(workers_mod, "_run_shell_worker",
                            lambda *a, **kw: calls.append(("shell", a, kw)))
        monkeypatch.setattr(workers_mod, "_run_marker_worker",
                            lambda *a, **kw: calls.append(("marker", a, kw)))
        start_worker(bp_dir, 0)
        assert [c[0] for c in calls] == ["marker"]

    def test_eval_type_never_dispatches(self, bp_dir, monkeypatch):
        _install_slots(bp_dir, [{
            "type": "eval", "row": 0, "col": 0, "name": "Eval",
            "activation": "manual", "disposition": "review", "max_retries": 0,
            "task_queue": [], "state": "idle",
        }])
        calls = []
        monkeypatch.setattr(workers_mod, "_run_ai_worker",
                            lambda *a, **kw: calls.append("ai"))
        monkeypatch.setattr(workers_mod, "_run_shell_worker",
                            lambda *a, **kw: calls.append("shell"))
        sock = CapturingSocket()
        start_worker(bp_dir, 0, socketio=sock)
        assert calls == []
        assert any(e[0] == "toast" for e in sock.events)

    def test_unknown_type_never_dispatches(self, bp_dir, monkeypatch):
        _install_slots(bp_dir, [{
            "type": "something-future", "row": 0, "col": 0, "name": "X",
            "activation": "manual", "disposition": "review", "max_retries": 0,
            "task_queue": [], "state": "idle",
        }])
        calls = []
        monkeypatch.setattr(workers_mod, "_run_ai_worker",
                            lambda *a, **kw: calls.append("ai"))
        monkeypatch.setattr(workers_mod, "_run_shell_worker",
                            lambda *a, **kw: calls.append("shell"))
        start_worker(bp_dir, 0)
        assert calls == []


class TestSharedLifecycleEntry:
    """Both AI and Shell paths must flow through `_begin_run`."""

    def _instrument(self, monkeypatch):
        calls = []
        original = workers_mod._begin_run

        def spy(bp_dir, slot_index, **kw):
            calls.append((slot_index, kw.get("trigger_kind"), kw.get("trigger_label")))
            return original(bp_dir, slot_index, **kw)

        monkeypatch.setattr(workers_mod, "_begin_run", spy)
        return calls

    def test_ai_run_enters_shared_begin_run(self, bp_dir, monkeypatch):
        _install_slots(bp_dir, [_ai_worker()])
        task = create_task(bp_dir, "AI task")
        assign_task(bp_dir, 0, task["id"])
        calls = self._instrument(monkeypatch)
        # assign_task may auto-start for on_drop activations, but this worker
        # is manual so call start_worker explicitly.
        start_worker(bp_dir, 0)
        _wait_idle(bp_dir)
        assert len(calls) >= 1
        assert calls[0][0] == 0

    def test_shell_run_enters_shared_begin_run(self, bp_dir, monkeypatch):
        _install_slots(bp_dir, [_shell_worker(command=_python_command("pass"))])
        task = create_task(bp_dir, "Shell task")
        assign_task(bp_dir, 0, task["id"])
        calls = self._instrument(monkeypatch)
        start_worker(bp_dir, 0)
        _wait_idle(bp_dir)
        assert len(calls) >= 1
        assert calls[0][0] == 0


class TestCommitRunStartEmitsCommonEvents:
    """Both types emit `task:updated` then `layout:updated` in that order
    when the run transitions to `working`. This pins the shared event contract
    of `_commit_run_start` so adapters never diverge."""

    def _extract_order(self, sock):
        keep = [e[0] for e in sock.events if e[0] in ("task:updated", "layout:updated")]
        # Find the first adjacent pair covering the state transition.
        for i in range(len(keep) - 1):
            if keep[i] == "task:updated" and keep[i + 1] == "layout:updated":
                return True
        return False

    def test_ai_emits_task_then_layout_on_start(self, bp_dir):
        _install_slots(bp_dir, [_ai_worker()])
        task = create_task(bp_dir, "AI task")
        assign_task(bp_dir, 0, task["id"])
        sock = CapturingSocket()
        start_worker(bp_dir, 0, socketio=sock)
        _wait_idle(bp_dir)
        assert self._extract_order(sock), [e[0] for e in sock.events]

    def test_shell_emits_task_then_layout_on_start(self, bp_dir):
        _install_slots(bp_dir, [_shell_worker(command=_python_command("pass"))])
        task = create_task(bp_dir, "Shell task")
        assign_task(bp_dir, 0, task["id"])
        sock = CapturingSocket()
        start_worker(bp_dir, 0, socketio=sock)
        _wait_idle(bp_dir)
        assert self._extract_order(sock), [e[0] for e in sock.events]


class TestEmptyQueueSyntheticTicket:
    """Manual empty-queue start synthesizes a ticket for both AI and Shell
    using the same `_begin_run` path."""

    def test_ai_empty_queue_synthesizes_ticket(self, bp_dir):
        _install_slots(bp_dir, [_ai_worker()])
        start_worker(bp_dir, 0)
        _wait_idle(bp_dir)
        layout = _load_layout(bp_dir)
        # Synthetic task should have been created & consumed.
        from server.tasks import list_tasks
        all_tasks = list_tasks(bp_dir)
        assert any(
            t.get("synthetic_run") and t.get("trigger_kind") == "manual"
            for t in all_tasks
        )

    def test_shell_empty_queue_synthesizes_ticket(self, bp_dir):
        _install_slots(bp_dir, [_shell_worker(command=_python_command("pass"))])
        start_worker(bp_dir, 0)
        _wait_idle(bp_dir)
        from server.tasks import list_tasks
        all_tasks = list_tasks(bp_dir)
        assert any(
            t.get("synthetic_run") and t.get("trigger_kind") == "manual"
            for t in all_tasks
        )


class TestSharedCompletionPipeline:
    """Both AI and Shell failures land on `_on_agent_error`, and both
    successes land on `_on_agent_success`. This proves there is no
    duplicate disposition/retry implementation."""

    def test_on_agent_error_is_the_only_error_sink(self):
        import inspect
        source = inspect.getsource(workers_mod)
        # _run_ai_worker and _run_shell_worker and their helpers are the
        # only allowed callers; we only want to confirm there is no
        # separate "shell retry / shell block" function with its own logic.
        assert "def _on_agent_error" in source
        # Make sure there is no parallel shell-only error handler.
        assert "def _on_shell_error" not in source
        assert "def _on_shell_success" not in source

    def test_on_agent_success_is_the_only_success_sink(self):
        import inspect
        source = inspect.getsource(workers_mod)
        assert "def _on_agent_success" in source
        assert "def _on_shell_success" not in source


class TestDispositionDispatcherIsShared:
    """Pass/handoff/random disposition logic lives in one place. The
    Shell parser returns a disposition override that `_on_agent_success`
    consumes via the same code path AI uses."""

    def test_shell_success_uses_shared_disposition_override(self, bp_dir):
        # Return JSON that asks for disposition=done, which is only honored
        # through the shared _on_agent_success dispatcher.
        cmd = _python_command(
            "import json,sys; json.load(sys.stdin); "
            "print(json.dumps({'disposition':'done'}))"
        )
        _install_slots(bp_dir, [_shell_worker(
            command=cmd,
            disposition="review",  # default would be review; override wins
        )])
        task = create_task(bp_dir, "Shell dispo")
        assign_task(bp_dir, 0, task["id"])
        start_worker(bp_dir, 0)
        _wait_idle(bp_dir)
        updated = read_task(bp_dir, task["id"])
        assert updated["status"] == "done"
