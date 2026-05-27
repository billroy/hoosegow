"""Time-based scheduler for worker activation."""

import logging
import os
import threading
import time
from datetime import datetime

from server.locks import write_lock
from server.persistence import read_json
from server.worker_types import normalize_layout
from server import workers as worker_mod

log = logging.getLogger(__name__)


class Scheduler:
    """Background thread that checks time-based activation triggers."""

    def __init__(self, bp_dir, socketio, interval=60, ws_id=None):
        self.bp_dir = bp_dir
        self.socketio = socketio
        self.interval = interval
        self.ws_id = ws_id
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                log.warning("Scheduler tick error: %s", e)
            self._stop_event.wait(self.interval)

    def _tick(self):
        """Check all workers for time-based triggers."""
        layout_path = os.path.join(self.bp_dir, "layout.json")
        if not os.path.exists(layout_path):
            return

        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_ts = time.time()

        # Collect workers to fire outside the lock.
        to_fire = []

        with write_lock:
            config = read_json(os.path.join(self.bp_dir, "config.json"))
            if config.get("worker_automation_paused"):
                return
            layout = normalize_layout(read_json(layout_path), config=config)
            dirty = False

            for slot_index, worker in enumerate(layout.get("slots", [])):
                if worker is None:
                    continue
                if worker.get("state") != "idle":
                    continue
                if worker.get("paused"):
                    continue

                activation = worker.get("activation")

                if activation == "at_time":
                    trigger_time = worker.get("trigger_time")
                    if trigger_time and trigger_time == current_time:
                        activation_kind = activation
                        if not worker.get("trigger_every_day"):
                            worker["activation"] = "manual"
                            dirty = True
                        to_fire.append((slot_index, worker, activation_kind))

                elif activation == "on_interval":
                    interval_min = worker.get("trigger_interval_minutes")
                    last_trigger = worker.get("last_trigger_time") or 0
                    if not last_trigger:
                        # First tick after config or restart — seed the
                        # timestamp so the worker waits a full interval
                        # before its first fire instead of firing immediately.
                        worker["last_trigger_time"] = current_ts
                        dirty = True
                        continue
                    if interval_min and (current_ts - last_trigger) >= interval_min * 60:
                        worker["last_trigger_time"] = current_ts
                        dirty = True
                        to_fire.append((slot_index, worker, activation))

            if dirty:
                from server.persistence import write_json
                write_json(layout_path, normalize_layout(layout, config=config))

        # Fire workers outside the lock (start_worker acquires it internally via events)
        for slot_index, worker, trigger_kind in to_fire:
            if not worker.get("task_queue"):
                # Auto-create an ephemeral task for self-directed workers
                task = worker_mod.create_auto_task(
                    self.bp_dir,
                    slot_index,
                    worker,
                    self.socketio,
                    self.ws_id,
                    trigger_kind=trigger_kind,
                    trigger_label=trigger_kind,
                )
                log.info("Auto-created task %s for worker %s (slot %d)", task["id"], worker.get("name"), slot_index)
            worker_mod.start_worker(self.bp_dir, slot_index, self.socketio, self.ws_id)
