"""Phase 6 hardening tests for Shell workers.

Covers the §3.7 items not exercised in `test_shell_worker.py`:
- secret env name filtering across realistic variants (incl. lowercase)
- BULLPEN_MCP_TOKEN is filtered from inherited env and rejected from config
- malformed JSON stdout is ignored on nonzero exits
- unknown top-level JSON keys are ignored
- unknown worker type card can be removed without installing its type
- rapid same-slot reruns produce unique artifact paths
- command/env redaction for read-only viewer context
- output artifact truncation at the configured cap
- argv fallback emits the prelude line before the child command starts
- default .bullpen/.gitignore excludes logs/
- live focus buffer catch-up includes streamed Shell output
- Socket.IO end-to-end: create, configure, run, observe results
"""

import json
import os
import shlex
import sys
import time

import pytest

import server.workers as workers_mod
from server.app import create_app, socketio
from server.init import init_workspace
from server.persistence import read_json, write_json
from server.tasks import create_task, read_task
from server.worker_types import ViewerContext, serialize_worker_slot
from server.workers import (
    _load_layout,
    _processes,
    _minimal_shell_env,
    _parse_shell_result,
    _validate_shell_worker,
    assign_task,
    start_worker,
    get_output_buffer,
)


# -- helpers ---------------------------------------------------------------

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
        time.sleep(0.03)
    raise AssertionError("worker did not finish")


def _set_shell_worker(bp_dir, **overrides):
    worker = {
        "type": "shell",
        "row": 0, "col": 0,
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


# -- security ----------------------------------------------------------------

class TestSecretEnvFiltering:
    """§3.7: secret env filtering for realistic names including lowercase."""

    @pytest.mark.parametrize("name", [
        "AWS_ACCESS_KEY_ID",
        "DATABASE_PASSWORD",
        "GITHUB_TOKEN",
        "SERVICE_CREDENTIAL_FILE",
        "NPM_PASSPHRASE",
        "api_secret",
        "my_token",
    ])
    def test_secret_names_are_stripped_from_inherited_env(self, monkeypatch, name):
        monkeypatch.setenv(name, "leak-me")
        env = _minimal_shell_env([])
        assert name not in env
        # Case-insensitive: upper-cased variant also gone.
        assert name.upper() not in env

    def test_bullpen_mcp_token_never_inherited(self, monkeypatch):
        monkeypatch.setenv("BULLPEN_MCP_TOKEN", "server-token")
        env = _minimal_shell_env([])
        assert "BULLPEN_MCP_TOKEN" not in env

    def test_bullpen_mcp_token_rejected_from_configured_env(self):
        errors = _validate_shell_worker(
            {"command": "true", "env": [{"key": "BULLPEN_MCP_TOKEN", "value": "x"}]},
            bp_dir=None,
        )
        assert any("BULLPEN_MCP_TOKEN" in e for e in errors)

    def test_non_secret_env_is_still_inherited_by_default(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        env = _minimal_shell_env([])
        # PATH is on the allowlist on POSIX; Windows has its own allowlist.
        if sys.platform != "win32":
            assert "PATH" in env

    def test_explicitly_configured_secret_name_is_allowed(self):
        env = _minimal_shell_env([{"key": "GITHUB_TOKEN", "value": "explicit"}])
        # The user knowingly re-added it, so we pass it through verbatim.
        assert env.get("GITHUB_TOKEN") == "explicit"


# -- parsing ----------------------------------------------------------------

class TestJsonStdoutParsing:
    def _worker(self, **overrides):
        base = {"disposition": "review"}
        base.update(overrides)
        return base

    class _Completed:
        def __init__(self, stdout, returncode, stderr="", timed_out=False):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode
            self.timed_out = timed_out
            self.combined_lines = []

    def test_malformed_json_on_nonzero_exit_is_ignored(self, bp_dir):
        result = _parse_shell_result(
            bp_dir,
            self._worker(),
            self._Completed("{not json", returncode=1),
        )
        assert result.outcome == "error"
        # Malformed JSON does not leak into the reason.
        assert "Shell command exited 1" in (result.reason or "")

    def test_unknown_top_level_json_keys_are_ignored(self, bp_dir):
        payload = json.dumps({
            "disposition": "done",
            "note": "whatever",
            "extra_nested": {"a": 1},
        })
        result = _parse_shell_result(bp_dir, self._worker(), self._Completed(payload, returncode=0))
        assert result.outcome == "success"
        assert result.disposition == "done"

    def test_status_key_is_ignored_on_success(self, bp_dir):
        payload = json.dumps({"status": "done", "disposition": "review"})
        result = _parse_shell_result(bp_dir, self._worker(), self._Completed(payload, returncode=0))
        assert result.outcome == "success"
        assert result.disposition == "review"


# -- layout correctness -----------------------------------------------------

class TestUnknownWorkerType:
    def test_unknown_type_round_trips_and_can_be_removed(self, bp_dir):
        layout = _load_layout(bp_dir)
        layout["slots"] = [{
            "type": "future-worker",
            "row": 0, "col": 0,
            "name": "Future",
            "activation": "manual",
            "disposition": "review",
            "max_retries": 0,
            "task_queue": [],
            "state": "idle",
            "some_future_field": {"nested": True},
        }]
        write_json(os.path.join(bp_dir, "layout.json"), layout)

        # Load through normalization — unknown type and unknown field preserved.
        reloaded = _load_layout(bp_dir)
        slot = reloaded["slots"][0]
        assert slot["type"] == "future-worker"
        assert slot["some_future_field"] == {"nested": True}

        # The slot is still removable even though the type is not installed.
        reloaded["slots"][0] = None
        write_json(os.path.join(bp_dir, "layout.json"), reloaded)
        final = _load_layout(bp_dir)
        assert final["slots"][0] is None


class TestRedactionForReadOnlyViewer:
    def test_read_only_viewer_sees_redacted_command_and_env(self):
        slot = {
            "type": "shell",
            "name": "Secret",
            "command": "curl -H 'X-Auth: super-secret' https://example.com",
            "env": [{"key": "API_KEY", "value": "leak"}],
        }
        editable = serialize_worker_slot(slot, viewer=ViewerContext(can_edit=True))
        read_only = serialize_worker_slot(slot, viewer=ViewerContext(can_edit=False))

        assert editable["command"] == slot["command"]
        assert editable["env"][0]["value"] == "leak"

        assert read_only["command"] == "<redacted>"
        # Env key name is still visible, value is not.
        assert read_only["env"][0]["key"] == "API_KEY"
        assert read_only["env"][0]["value"] == "<redacted>"
        # Round-tripping should not mutate the original.
        assert slot["command"].startswith("curl")
        assert slot["env"][0]["value"] == "leak"


# -- output handling --------------------------------------------------------

class TestOutputTruncation:
    def test_large_stdout_is_capped_to_one_mib(self, bp_dir):
        # Produce ~2 MiB of stdout; artifact should cap at 1 MiB.
        code = "import sys; sys.stdout.write('x' * (2 * 1024 * 1024))"
        _set_shell_worker(bp_dir, command=_python_command(code), timeout_seconds=20)
        task = create_task(bp_dir, "Huge")
        assign_task(bp_dir, 0, task["id"])
        start_worker(bp_dir, 0)
        _wait_for_worker_done(bp_dir, timeout=15.0)

        updated = read_task(bp_dir, task["id"])
        history = [r for r in updated.get("history", []) if r.get("event") == "worker_run"][-1]
        assert history["stdout_truncated"] is True
        assert history["stdout_bytes"] <= 1_048_576
        assert history["stdout_observed_bytes"] >= 2 * 1024 * 1024
        # Artifact is readable and stops at the cap.
        artifact = os.path.join(os.path.dirname(bp_dir), history["stdout_artifact"])
        data = open(artifact, "rb").read()
        assert len(data) <= 1_048_576


class TestRapidReruns:
    def test_rapid_same_slot_reruns_produce_unique_artifact_paths(self, bp_dir):
        _set_shell_worker(
            bp_dir,
            command=_python_command("print('run')"),
            activation="manual",
        )
        task = create_task(bp_dir, "Reruns")
        assign_task(bp_dir, 0, task["id"])

        seen = set()
        for _ in range(3):
            start_worker(bp_dir, 0)
            _wait_for_worker_done(bp_dir)
            updated = read_task(bp_dir, task["id"])
            latest = [r for r in updated.get("history", []) if r.get("event") == "worker_run"][-1]
            assert latest["stdout_artifact"] not in seen
            seen.add(latest["stdout_artifact"])
            # Re-enqueue the same task for the next rerun.
            assign_task(bp_dir, 0, task["id"])
        assert len(seen) == 3


class TestArgvFallback:
    def test_argv_fallback_emits_prelude_and_switches_delivery(self, bp_dir, monkeypatch):
        # Force the argv limit tiny so any realistic payload triggers fallback.
        monkeypatch.setattr(workers_mod, "_argv_json_limit", lambda: 16)
        _set_shell_worker(
            bp_dir,
            command=_python_command("import json,sys; t=json.load(sys.stdin); print(t['title'])"),
            ticket_delivery="argv-json",
        )
        task = create_task(bp_dir, "Fallback Task")
        assign_task(bp_dir, 0, task["id"])
        start_worker(bp_dir, 0)
        _wait_for_worker_done(bp_dir)

        updated = read_task(bp_dir, task["id"])
        history = [r for r in updated.get("history", []) if r.get("event") == "worker_run"][-1]
        assert history["delivery"] == "stdin-json"
        assert history["delivery_fallback_from"] == "argv-json"
        # The prelude line is visible in the body output block.
        assert "argv limit, using stdin-json" in updated["body"]
        # The child actually received stdin JSON.
        assert "Fallback Task" in updated["body"]


# -- workspace defaults -----------------------------------------------------

class TestGitignoreDefaults:
    def test_default_bullpen_gitignore_excludes_logs(self, bp_dir):
        gitignore_path = os.path.join(bp_dir, ".gitignore")
        assert os.path.exists(gitignore_path), "init_workspace must write .bullpen/.gitignore"
        content = open(gitignore_path).read()
        assert "logs/" in content.splitlines()


# -- live focus buffer ------------------------------------------------------

class TestFocusBufferCatchup:
    def test_live_buffer_captures_shell_output_for_focus_catchup(self, bp_dir):
        # Long-running command that prints, then sleeps, so the buffer has
        # content while the process is still registered.
        _set_shell_worker(
            bp_dir,
            command=_python_command(
                "import sys,time; print('first line', flush=True); "
                "print('second line', flush=True); time.sleep(0.6)"
            ),
            timeout_seconds=5,
        )
        task = create_task(bp_dir, "Focus")
        assign_task(bp_dir, 0, task["id"])
        start_worker(bp_dir, 0)

        # Observe mid-run.
        deadline = time.time() + 3.0
        buffer_snapshot = None
        while time.time() < deadline:
            entry = get_output_buffer(None, 0)
            if entry and entry.get("buffer"):
                buffer_snapshot = list(entry["buffer"])
                break
            time.sleep(0.05)
        _wait_for_worker_done(bp_dir, timeout=6.0)
        assert buffer_snapshot, "live buffer should populate during Shell run"
        joined = "\n".join(buffer_snapshot)
        assert "first line" in joined


# -- Socket.IO E2E ----------------------------------------------------------

class TestSocketIOEndToEnd:
    def test_shell_worker_full_lifecycle_via_socketio(self, tmp_workspace):
        app = create_app(tmp_workspace, no_browser=True)
        bp = os.path.join(tmp_workspace, ".bullpen")
        client = socketio.test_client(app)
        try:
            client.get_received()  # drain state:init

            # Create a Shell worker via the dispatcher's new shell path.
            client.emit("worker:add", {
                "coord": {"col": 0, "row": 0},
                "type": "shell",
                "fields": {"name": "E2E Shell"},
            })
            # Configure the command. print to stdout and stderr so we can
            # verify stderr lands in the output block too.
            cmd = _python_command(
                "import sys; print('hi out'); print('hi err', file=sys.stderr)"
            )
            client.emit("worker:configure", {
                "slot": 0,
                "fields": {
                    "command": cmd,
                    "ticket_delivery": "stdin-json",
                    "timeout_seconds": 10,
                    "activation": "manual",
                    "disposition": "review",
                    "max_retries": 0,
                },
            })
            client.get_received()

            # Assign a ticket and run.
            task = create_task(bp, "E2E task")
            client.emit("task:assign", {"task_id": task["id"], "slot": 0})
            client.emit("worker:start", {"slot": 0})
            _wait_for_worker_done(bp, timeout=10.0)

            updated = read_task(bp, task["id"])
            assert updated["status"] == "review"
            # Captured stderr is present in the output block.
            assert "hi err" in updated["body"]
            # Command string is NOT embedded in history or output block.
            body = updated["body"]
            assert cmd not in body
            for row in updated.get("history", []):
                assert cmd not in json.dumps(row)

            # Read-only serialization hides command/env.
            layout = _load_layout(bp)
            read_only = serialize_worker_slot(
                layout["slots"][0], viewer=ViewerContext(can_edit=False)
            )
            assert read_only["command"] == "<redacted>"
        finally:
            client.disconnect()
