import base64
import time

from server.app import create_app, socketio
from server.local_pty import LocalPtyError
from server.pty_driver import PtyDriverError


class FakePtyDriver:
    def __init__(self, *, base_url, token, timeout=5.0):
        self.base_url = base_url
        self.token = token

    def open(self, terminal_id, *, cwd="/workspace", shell="/bin/bash", cols=100, rows=30):
        return {"event": "opened", "id": terminal_id, "cwd": cwd, "pid": 1234}

    def poll(self, terminal_id, *, since, timeout=1.0):
        raise PtyDriverError("fake poll stopped")

    def write(self, terminal_id, data):
        return {"event": "ok"}

    def resize(self, terminal_id, *, cols, rows):
        return {"event": "ok"}

    def status(self, terminal_id):
        return {
            "event": "status",
            "id": terminal_id,
            "status": "running",
            "exit_code": None,
            "foreground": {
                "supported": True,
                "busy": True,
                "pgrp": 4321,
                "pid": 4322,
                "command": "sleep 100",
            },
        }

    def close(self, terminal_id):
        return {"event": "ok"}


class IdleShellPtyDriver(FakePtyDriver):
    writes = []

    def write(self, terminal_id, data):
        self.__class__.writes.append((terminal_id, data))
        return {"event": "ok"}

    def status(self, terminal_id):
        payload = super().status(terminal_id)
        payload["foreground"] = {
            "supported": True,
            "busy": False,
            "pgrp": 1234,
            "pid": 1234,
            "command": "/bin/bash -l",
        }
        return payload


class RecordingBusyPtyDriver(FakePtyDriver):
    writes = []

    def write(self, terminal_id, data):
        self.__class__.writes.append((terminal_id, data))
        return {"event": "ok"}


class ReplayPtyDriver(FakePtyDriver):
    polled = set()
    output = b"one\ntwo\nthree\nfour\n"

    def poll(self, terminal_id, *, since, timeout=1.0):
        if terminal_id not in self.polled:
            self.polled.add(terminal_id)
            data = base64.b64encode(self.output).decode("ascii")
            return {
                "events": [{"event": "output", "data": data}],
                "next_seq": since + 1,
            }
        raise PtyDriverError("fake poll stopped")


class FakeLocalPtyDriver:
    def __init__(self):
        self.opened = []
        self.closed = []

    def open(self, terminal_id, *, cwd, shell, cols=100, rows=30):
        self.opened.append((terminal_id, cwd, shell, cols, rows))
        return {"event": "opened", "id": terminal_id, "cwd": cwd, "pid": 2468}

    def poll(self, terminal_id, *, since, timeout=1.0):
        raise LocalPtyError("fake local poll stopped")

    def write(self, terminal_id, data):
        return {"event": "ok"}

    def resize(self, terminal_id, *, cols, rows):
        return {"event": "ok"}

    def status(self, terminal_id):
        return {
            "event": "status",
            "id": terminal_id,
            "status": "running",
            "exit_code": None,
            "foreground": {"busy": False},
        }

    def close(self, terminal_id):
        self.closed.append(terminal_id)
        return {"event": "ok"}


def _running_sandbox(app, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = app.config["hoosegow_sandboxes"]
    service.browse_roots = [str(tmp_path)]
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    manifest = service.store.get("demo")
    manifest.last_status = "running"
    service.store.save(manifest)


def test_hoosegow_local_terminal_opens_and_lists(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.LocalPtyDriver", FakeLocalPtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    client = socketio.test_client(app)
    client.get_received()

    opened = client.emit(
        "terminal:local:open",
        {"cols": 80, "rows": 24},
        callback=True,
    )
    listed = client.emit("terminal:list", {}, callback=True)

    assert opened["ok"] is True
    assert opened["terminal"]["kind"] == "local"
    assert opened["terminal"]["label"] == "shell"
    assert opened["terminal"]["sandbox_id"] is None
    assert opened["terminal"]["local_group_id"] == "local"
    assert opened["terminal"]["local_group_label"] == "Local"
    assert opened["terminal"]["cwd"] == str(tmp_path)
    assert [item["id"] for item in listed["terminals"]] == [opened["terminal"]["id"]]
    assert listed["terminals"][0]["kind"] == "local"
    assert listed["terminals"][0]["local_group_id"] == "local"
    assert listed["terminals"][0]["local_group_label"] == "Local"
    assert listed["terminals"][0]["number"] == 1


def test_hoosegow_local_terminal_group_metadata_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.LocalPtyDriver", FakeLocalPtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    client = socketio.test_client(app)
    client.get_received()

    opened = client.emit(
        "terminal:local:open",
        {
            "cols": 80,
            "rows": 24,
            "local_group_id": "local-review",
            "local_group_label": "Local Review",
        },
        callback=True,
    )
    listed = client.emit("terminal:list", {}, callback=True)
    joined = client.emit("sandbox:terminal:join", {"terminal_id": opened["terminal"]["id"]}, callback=True)

    assert opened["ok"] is True
    assert opened["terminal"]["local_group_id"] == "local-review"
    assert opened["terminal"]["local_group_label"] == "Local Review"
    assert listed["terminals"][0]["local_group_id"] == "local-review"
    assert listed["terminals"][0]["local_group_label"] == "Local Review"
    assert joined["terminal"]["local_group_id"] == "local-review"
    assert joined["terminal"]["local_group_label"] == "Local Review"


def test_hoosegow_local_terminal_numbers_do_not_renumber_after_close(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.LocalPtyDriver", FakeLocalPtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    client = socketio.test_client(app)
    client.get_received()

    first = client.emit("terminal:local:open", {"cols": 80, "rows": 24}, callback=True)
    second = client.emit("terminal:local:open", {"cols": 80, "rows": 24}, callback=True)
    client.emit("sandbox:terminal:close", {"terminal_id": first["terminal"]["id"]}, callback=True)
    third = client.emit("terminal:local:open", {"cols": 80, "rows": 24}, callback=True)
    listed = client.emit("terminal:list", {}, callback=True)

    assert first["terminal"]["number"] == 1
    assert second["terminal"]["number"] == 2
    assert third["terminal"]["number"] == 3
    assert [terminal["number"] for terminal in listed["terminals"]] == [2, 3]


def test_hoosegow_local_terminal_limit_rejects_extra_sessions_and_close_frees_slot(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.LocalPtyDriver", FakeLocalPtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
        terminal_limit=1,
    )
    client = socketio.test_client(app)
    client.get_received()

    first = client.emit("terminal:local:open", {"cols": 80, "rows": 24}, callback=True)
    blocked = client.emit("terminal:local:open", {"cols": 80, "rows": 24}, callback=True)
    closed = client.emit("sandbox:terminal:close", {"terminal_id": first["terminal"]["id"]}, callback=True)
    reopened = client.emit("terminal:local:open", {"cols": 80, "rows": 24}, callback=True)
    listed = client.emit("terminal:list", {}, callback=True)

    client.disconnect()
    local_driver = app.config["hoosegow_local_pty_driver"]
    assert first["ok"] is True
    assert blocked["ok"] is False
    assert "Local terminal limit reached" in blocked["error"]
    assert closed["ok"] is True
    assert local_driver.closed == [first["terminal"]["id"]]
    assert reopened["ok"] is True
    assert reopened["terminal"]["number"] == 2
    assert [terminal["number"] for terminal in listed["terminals"]] == [2]


def test_hoosegow_terminal_list_keeps_local_and_sandbox_numbering_separate(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.LocalPtyDriver", FakeLocalPtyDriver)
    monkeypatch.setattr("server.app.PtyDriver", FakePtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    _running_sandbox(app, tmp_path)
    client = socketio.test_client(app)
    client.get_received()

    local = client.emit("terminal:local:open", {"cols": 80, "rows": 24}, callback=True)
    sandbox = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )
    all_listed = client.emit("terminal:list", {}, callback=True)
    sandbox_listed = client.emit("sandbox:terminal:list", {"sandbox_id": "demo"}, callback=True)

    client.disconnect()
    assert local["ok"] is True
    assert sandbox["ok"] is True
    assert local["terminal"]["kind"] == "local"
    assert sandbox["terminal"]["kind"] == "sandbox"
    assert local["terminal"]["number"] == 1
    assert sandbox["terminal"]["number"] == 1
    assert [(terminal["kind"], terminal["number"]) for terminal in all_listed["terminals"]] == [
        ("local", 1),
        ("sandbox", 1),
    ]
    assert [terminal["kind"] for terminal in sandbox_listed["terminals"]] == ["sandbox"]


def test_hoosegow_local_terminal_replay_preserves_driver_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.LocalPtyDriver", FakeLocalPtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    client = socketio.test_client(app)
    client.get_received()

    opened = client.emit("terminal:local:open", {"cols": 80, "rows": 24}, callback=True)
    terminal_id = opened["terminal"]["id"]
    raw_replay = (
        b"%                                                                              \r \r\r"
        b"bill@Blackbird hoosegow % "
    )
    with app.config["hoosegow_terminals_lock"]:
        app.config["hoosegow_terminals"][terminal_id]["replay"].extend(raw_replay)

    joined = client.emit("sandbox:terminal:join", {"terminal_id": terminal_id}, callback=True)
    replay = base64.b64decode(joined["replay"]["data"])

    client.disconnect()
    assert joined["ok"] is True
    assert replay == raw_replay


def test_hoosegow_terminal_limit_rejects_extra_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.PtyDriver", FakePtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
        terminal_limit=1,
    )
    _running_sandbox(app, tmp_path)
    client = socketio.test_client(app)
    client.get_received()

    first = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )
    second = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )

    client.disconnect()
    assert first["ok"] is True
    assert second["ok"] is False
    assert "Terminal limit reached" in second["error"]


def test_generated_terminal_query_response_is_dropped_at_idle_shell(tmp_path, monkeypatch):
    IdleShellPtyDriver.writes = []
    monkeypatch.setattr("server.app.PtyDriver", IdleShellPtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    _running_sandbox(app, tmp_path)
    client = socketio.test_client(app)
    opened = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )

    terminal_id = opened["terminal"]["id"]
    dropped = client.emit(
        "sandbox:terminal:input",
        {
            "terminal_id": terminal_id,
            "data": "\x1b[>0;276;0c",
            "terminal_query_response": True,
        },
        callback=True,
    )
    typed = client.emit(
        "sandbox:terminal:input",
        {"terminal_id": terminal_id, "data": "echo ok\n"},
        callback=True,
    )

    client.disconnect()
    assert dropped == {"ok": True, "dropped": True}
    assert typed == {"ok": True}
    assert IdleShellPtyDriver.writes == [(terminal_id, "echo ok\n")]


def test_generated_terminal_query_response_is_forwarded_to_busy_program(tmp_path, monkeypatch):
    RecordingBusyPtyDriver.writes = []
    monkeypatch.setattr("server.app.PtyDriver", RecordingBusyPtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    _running_sandbox(app, tmp_path)
    client = socketio.test_client(app)
    opened = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )

    terminal_id = opened["terminal"]["id"]
    response = client.emit(
        "sandbox:terminal:input",
        {
            "terminal_id": terminal_id,
            "data": "\x1b[>0;276;0c",
            "terminal_query_response": True,
        },
        callback=True,
    )

    client.disconnect()
    assert response == {"ok": True}
    assert RecordingBusyPtyDriver.writes == [(terminal_id, "\x1b[>0;276;0c")]


def test_hoosegow_terminal_limit_allows_default_32_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.PtyDriver", FakePtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
        terminal_limit=32,
    )
    _running_sandbox(app, tmp_path)
    client = socketio.test_client(app)
    client.get_received()

    opened = [
        client.emit(
            "sandbox:terminal:open",
            {"sandbox_id": "demo", "cols": 80, "rows": 24},
            callback=True,
        )
        for _index in range(32)
    ]
    extra = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )
    listed = client.emit("sandbox:terminal:list", {"sandbox_id": "demo"}, callback=True)

    client.disconnect()
    assert all(response["ok"] for response in opened)
    assert extra["ok"] is False
    assert "Terminal limit reached" in extra["error"]
    assert listed["ok"] is True
    assert len(listed["terminals"]) == 32


def test_hoosegow_terminal_close_frees_limit_slot(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.PtyDriver", FakePtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
        terminal_limit=1,
    )
    _running_sandbox(app, tmp_path)
    client = socketio.test_client(app)
    client.get_received()

    first = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )
    blocked = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )
    closed = client.emit(
        "sandbox:terminal:close",
        {"terminal_id": first["terminal"]["id"]},
        callback=True,
    )
    reopened = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )

    client.disconnect()
    assert first["ok"] is True
    assert blocked["ok"] is False
    assert closed["ok"] is True
    assert reopened["ok"] is True


def test_hoosegow_terminal_numbers_do_not_renumber_after_close(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.PtyDriver", FakePtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    _running_sandbox(app, tmp_path)
    client = socketio.test_client(app)
    client.get_received()

    first = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )
    second = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )
    closed = client.emit(
        "sandbox:terminal:close",
        {"terminal_id": first["terminal"]["id"]},
        callback=True,
    )
    third = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )
    listed = client.emit("sandbox:terminal:list", {"sandbox_id": "demo"}, callback=True)

    client.disconnect()
    assert first["terminal"]["number"] == 1
    assert second["terminal"]["number"] == 2
    assert closed["ok"] is True
    assert third["terminal"]["number"] == 3
    assert [terminal["number"] for terminal in listed["terminals"]] == [2, 3]


def test_hoosegow_terminal_status_reports_foreground_process(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.PtyDriver", FakePtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    _running_sandbox(app, tmp_path)
    client = socketio.test_client(app)
    client.get_received()

    opened = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )
    status = client.emit(
        "sandbox:terminal:status",
        {"terminal_id": opened["terminal"]["id"]},
        callback=True,
    )

    client.disconnect()
    assert status["ok"] is True
    assert status["status"]["foreground"]["busy"] is True
    assert status["status"]["foreground"]["command"] == "sleep 100"


def test_hoosegow_terminal_can_be_rejoined_by_new_socket(tmp_path, monkeypatch):
    monkeypatch.setattr("server.app.PtyDriver", FakePtyDriver)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    _running_sandbox(app, tmp_path)
    first_client = socketio.test_client(app)
    first_client.get_received()
    opened = first_client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )
    terminal_id = opened["terminal"]["id"]
    first_client.disconnect()

    second_client = socketio.test_client(app)
    second_client.get_received()
    listed = second_client.emit("sandbox:terminal:list", {"sandbox_id": "demo"}, callback=True)
    joined = second_client.emit("sandbox:terminal:join", {"terminal_id": terminal_id}, callback=True)
    wrote = second_client.emit(
        "sandbox:terminal:input",
        {"terminal_id": terminal_id, "data": "echo after reconnect\n"},
        callback=True,
    )

    second_client.disconnect()
    assert opened["ok"] is True
    assert any(terminal["id"] == terminal_id for terminal in listed["terminals"])
    assert joined["ok"] is True
    assert wrote["ok"] is True


def test_hoosegow_terminal_replay_is_line_bounded_and_marked(tmp_path, monkeypatch):
    ReplayPtyDriver.polled = set()
    ReplayPtyDriver.output = b"one\ntwo\nthree\nfour\n"
    monkeypatch.setattr("server.app.PtyDriver", ReplayPtyDriver)
    monkeypatch.setattr("server.app._TERMINAL_REPLAY_LIMIT_LINES", 2)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    _running_sandbox(app, tmp_path)
    first_client = socketio.test_client(app)
    first_client.get_received()
    opened = first_client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )
    terminal_id = opened["terminal"]["id"]

    deadline = time.time() + 1
    while time.time() < deadline:
        with app.config["hoosegow_terminals_lock"]:
            session_info = app.config["hoosegow_terminals"].get(terminal_id)
            if session_info and session_info.get("replay_truncated"):
                break
        time.sleep(0.01)
    first_client.disconnect()

    second_client = socketio.test_client(app)
    second_client.get_received()
    joined = second_client.emit("sandbox:terminal:join", {"terminal_id": terminal_id}, callback=True)
    second_client.disconnect()

    replay = base64.b64decode(joined["replay"]["data"]).decode("utf-8")
    assert joined["replay"]["truncated"] is True
    assert "[Hoosegow replay truncated]" in replay
    assert "one" not in replay
    assert "two" not in replay
    assert "three" in replay
    assert "four" in replay


def test_hoosegow_terminal_replay_is_byte_bounded_and_marked(tmp_path, monkeypatch):
    ReplayPtyDriver.polled = set()
    ReplayPtyDriver.output = b"abcdef"
    monkeypatch.setattr("server.app.PtyDriver", ReplayPtyDriver)
    monkeypatch.setattr("server.app._TERMINAL_REPLAY_LIMIT_BYTES", 3)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    _running_sandbox(app, tmp_path)
    first_client = socketio.test_client(app)
    first_client.get_received()
    opened = first_client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "demo", "cols": 80, "rows": 24},
        callback=True,
    )
    terminal_id = opened["terminal"]["id"]

    deadline = time.time() + 1
    while time.time() < deadline:
        with app.config["hoosegow_terminals_lock"]:
            session_info = app.config["hoosegow_terminals"].get(terminal_id)
            if session_info and session_info.get("replay_truncated"):
                break
        time.sleep(0.01)
    first_client.disconnect()

    second_client = socketio.test_client(app)
    second_client.get_received()
    joined = second_client.emit("sandbox:terminal:join", {"terminal_id": terminal_id}, callback=True)
    second_client.disconnect()

    replay = base64.b64decode(joined["replay"]["data"]).decode("utf-8")
    assert joined["replay"]["truncated"] is True
    assert "[Hoosegow replay truncated]" in replay
    assert "abc" not in replay
    assert replay.endswith("def")
