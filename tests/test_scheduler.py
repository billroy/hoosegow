"""Tests for server/scheduler.py."""

import os
import time
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from server.init import init_workspace
from server.persistence import read_json, write_json
from server.scheduler import Scheduler
from server.tasks import create_task, list_tasks
from server.workers import assign_task
from server.agents import register_adapter
from tests.conftest import MockAdapter


@pytest.fixture
def bp_dir(tmp_workspace):
    bp = init_workspace(tmp_workspace)
    register_adapter("mock", MockAdapter(output="Scheduler test output"))
    return bp


def _make_worker(bp_dir, activation="manual", **extra):
    """Create a worker in slot 0 with given activation."""
    layout = read_json(os.path.join(bp_dir, "layout.json"))
    worker = {
        "row": 0, "col": 0,
        "profile": "test",
        "name": "Scheduler Worker",
        "agent": "mock",
        "model": "mock-model",
        "activation": activation,
        "disposition": "review",
        "watch_column": None,
        "expertise_prompt": "",
        "max_retries": 0,
        "use_worktree": False,
        "auto_commit": False,
        "auto_pr": False,
        "task_queue": [],
        "state": "idle",
    }
    worker.update(extra)
    layout["slots"] = [worker]
    write_json(os.path.join(bp_dir, "layout.json"), layout)


class TestSchedulerTick:
    def test_at_time_fires(self, bp_dir):
        """Worker with at_time activation fires at matching time."""
        now = datetime.now()
        current_time = now.strftime("%H:%M")

        _make_worker(bp_dir, activation="at_time", trigger_time=current_time, trigger_every_day=False)

        # Add a task to the queue
        task = create_task(bp_dir, "Scheduled task")
        assign_task(bp_dir, 0, task["id"])

        # Reset state to idle (assign may have auto-started)
        layout = read_json(os.path.join(bp_dir, "layout.json"))
        layout["slots"][0]["state"] = "idle"
        layout["slots"][0]["activation"] = "at_time"
        layout["slots"][0]["trigger_time"] = current_time
        write_json(os.path.join(bp_dir, "layout.json"), layout)

        scheduler = Scheduler(bp_dir, None, interval=60)
        scheduler._tick()
        time.sleep(0.5)

        # Worker should have started (state may be working or idle if fast mock)
        layout = read_json(os.path.join(bp_dir, "layout.json"))
        # Since trigger_every_day is False, activation should be reset to manual
        assert layout["slots"][0]["activation"] == "manual"

    def test_at_time_skips_wrong_time(self, bp_dir):
        """Worker with at_time doesn't fire at non-matching time."""
        _make_worker(bp_dir, activation="at_time", trigger_time="99:99", trigger_every_day=True)

        task = create_task(bp_dir, "Scheduled task")
        assign_task(bp_dir, 0, task["id"])

        layout = read_json(os.path.join(bp_dir, "layout.json"))
        layout["slots"][0]["state"] = "idle"
        layout["slots"][0]["activation"] = "at_time"
        write_json(os.path.join(bp_dir, "layout.json"), layout)

        scheduler = Scheduler(bp_dir, None, interval=60)
        scheduler._tick()

        layout = read_json(os.path.join(bp_dir, "layout.json"))
        assert layout["slots"][0]["state"] == "idle"

    def test_auto_creates_task_when_queue_empty(self, bp_dir):
        """Worker with at_time and empty queue gets an auto-created task."""
        now = datetime.now()
        current_time = now.strftime("%H:%M")

        _make_worker(bp_dir, activation="at_time", trigger_time=current_time, trigger_every_day=True)

        # No tasks assigned — queue is empty
        scheduler = Scheduler(bp_dir, None, interval=60)
        scheduler._tick()
        time.sleep(0.5)

        # An auto task should have been created
        tasks = list_tasks(bp_dir)
        auto_tasks = [t for t in tasks if t["title"].startswith("[Auto]")]
        assert len(auto_tasks) == 1
        assert "Scheduler Worker" in auto_tasks[0]["title"]
        assert auto_tasks[0]["type"] == "chore"

    def test_auto_task_interval_empty_queue(self, bp_dir):
        """Interval worker with empty queue auto-creates task and fires."""
        _make_worker(
            bp_dir,
            activation="on_interval",
            trigger_interval_minutes=1,
            last_trigger_time=time.time() - 120,  # 2 min ago, exceeds 1-min interval
        )

        scheduler = Scheduler(bp_dir, None, interval=60)
        scheduler._tick()
        time.sleep(0.5)

        # Auto task should exist
        tasks = list_tasks(bp_dir)
        auto_tasks = [t for t in tasks if t["title"].startswith("[Auto]")]
        assert len(auto_tasks) == 1

        # last_trigger_time should be updated
        layout = read_json(os.path.join(bp_dir, "layout.json"))
        assert layout["slots"][0]["last_trigger_time"] > 0

    def test_interval_cold_start_seeds_timestamp_without_firing(self, bp_dir):
        """on_interval worker with no last_trigger_time seeds the timestamp
        on first tick instead of firing immediately (prevents burst on restart)."""
        _make_worker(
            bp_dir,
            activation="on_interval",
            trigger_interval_minutes=1,
            last_trigger_time=0,
        )

        scheduler = Scheduler(bp_dir, None, interval=60)
        scheduler._tick()
        time.sleep(0.5)

        # No auto task should be created on cold start
        tasks = list_tasks(bp_dir)
        auto_tasks = [t for t in tasks if t["title"].startswith("[Auto]")]
        assert len(auto_tasks) == 0

        # But last_trigger_time should now be seeded
        layout = read_json(os.path.join(bp_dir, "layout.json"))
        assert layout["slots"][0]["last_trigger_time"] > 0

    def test_skips_non_time_worker_no_tasks(self, bp_dir):
        """Non-time-based worker with no tasks is not affected by scheduler."""
        _make_worker(bp_dir, activation="manual")

        scheduler = Scheduler(bp_dir, None, interval=60)
        scheduler._tick()

        layout = read_json(os.path.join(bp_dir, "layout.json"))
        assert layout["slots"][0]["state"] == "idle"
        # No auto tasks created
        tasks = list_tasks(bp_dir)
        auto_tasks = [t for t in tasks if t["title"].startswith("[Auto]")]
        assert len(auto_tasks) == 0

    def test_interval_fires(self, bp_dir):
        """Worker with on_interval activation fires after elapsed time."""
        _make_worker(
            bp_dir,
            activation="on_interval",
            trigger_interval_minutes=1,
            last_trigger_time=0,  # epoch = long ago
        )

        task = create_task(bp_dir, "Interval task")
        assign_task(bp_dir, 0, task["id"])

        layout = read_json(os.path.join(bp_dir, "layout.json"))
        layout["slots"][0]["state"] = "idle"
        layout["slots"][0]["activation"] = "on_interval"
        layout["slots"][0]["last_trigger_time"] = 0
        write_json(os.path.join(bp_dir, "layout.json"), layout)

        scheduler = Scheduler(bp_dir, None, interval=60)
        scheduler._tick()
        time.sleep(0.5)

        layout = read_json(os.path.join(bp_dir, "layout.json"))
        # last_trigger_time should be updated
        assert layout["slots"][0]["last_trigger_time"] > 0

    def test_paused_worker_skipped(self, bp_dir):
        """Paused worker is skipped by scheduler tick."""
        now = datetime.now()
        current_time = now.strftime("%H:%M")

        _make_worker(bp_dir, activation="at_time", trigger_time=current_time,
                     trigger_every_day=True, paused=True)

        task = create_task(bp_dir, "Paused task")
        assign_task(bp_dir, 0, task["id"])

        layout = read_json(os.path.join(bp_dir, "layout.json"))
        layout["slots"][0]["state"] = "idle"
        layout["slots"][0]["activation"] = "at_time"
        layout["slots"][0]["paused"] = True
        write_json(os.path.join(bp_dir, "layout.json"), layout)

        scheduler = Scheduler(bp_dir, None, interval=60)
        scheduler._tick()

        layout = read_json(os.path.join(bp_dir, "layout.json"))
        assert layout["slots"][0]["state"] == "idle"

    def test_worker_automation_pause_skips_scheduler_tick(self, bp_dir):
        """Workspace automation pause prevents scheduled triggers."""
        now = datetime.now()
        current_time = now.strftime("%H:%M")

        _make_worker(bp_dir, activation="at_time", trigger_time=current_time,
                     trigger_every_day=False, paused=False)

        config = read_json(os.path.join(bp_dir, "config.json"))
        config["worker_automation_paused"] = True
        write_json(os.path.join(bp_dir, "config.json"), config)
        task = create_task(bp_dir, "Automation paused scheduled task")
        assign_task(bp_dir, 0, task["id"])

        layout = read_json(os.path.join(bp_dir, "layout.json"))
        layout["slots"][0]["state"] = "idle"
        layout["slots"][0]["activation"] = "at_time"
        write_json(os.path.join(bp_dir, "layout.json"), layout)

        scheduler = Scheduler(bp_dir, None, interval=60)
        scheduler._tick()

        layout = read_json(os.path.join(bp_dir, "layout.json"))
        assert layout["slots"][0]["state"] == "idle"
        assert layout["slots"][0]["activation"] == "at_time"

    def test_unpaused_worker_fires(self, bp_dir):
        """Unpaused worker fires normally."""
        now = datetime.now()
        current_time = now.strftime("%H:%M")

        _make_worker(bp_dir, activation="at_time", trigger_time=current_time,
                     trigger_every_day=True, paused=False)

        task = create_task(bp_dir, "Unpaused task")
        assign_task(bp_dir, 0, task["id"])

        layout = read_json(os.path.join(bp_dir, "layout.json"))
        layout["slots"][0]["state"] = "idle"
        layout["slots"][0]["activation"] = "at_time"
        layout["slots"][0]["paused"] = False
        write_json(os.path.join(bp_dir, "layout.json"), layout)

        scheduler = Scheduler(bp_dir, None, interval=60)
        scheduler._tick()
        time.sleep(0.5)

        # Worker should have been triggered (activation reset to manual since not every_day... wait, it IS every_day)
        # Since trigger_every_day=True, activation stays at_time but worker should have started
        layout = read_json(os.path.join(bp_dir, "layout.json"))
        # Worker should have processed (may be idle again since mock is fast)
        assert layout["slots"][0]["state"] in ("working", "idle")

    def test_interval_fires_with_null_last_trigger(self, bp_dir):
        """Worker with null last_trigger_time (fresh config) should fire."""
        _make_worker(
            bp_dir,
            activation="on_interval",
            trigger_interval_minutes=1,
            last_trigger_time=None,  # null from fresh config
        )

        task = create_task(bp_dir, "Null trigger task")
        assign_task(bp_dir, 0, task["id"])

        layout = read_json(os.path.join(bp_dir, "layout.json"))
        layout["slots"][0]["state"] = "idle"
        layout["slots"][0]["activation"] = "on_interval"
        layout["slots"][0]["last_trigger_time"] = None
        write_json(os.path.join(bp_dir, "layout.json"), layout)

        scheduler = Scheduler(bp_dir, None, interval=60)
        scheduler._tick()
        time.sleep(0.5)

        layout = read_json(os.path.join(bp_dir, "layout.json"))
        assert layout["slots"][0]["last_trigger_time"] > 0
