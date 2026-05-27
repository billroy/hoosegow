from server.app import create_app, socketio
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

    def close(self, terminal_id):
        return {"event": "ok"}


def _running_sandbox(app, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    service = app.config["toady_sandboxes"]
    service.browse_roots = [str(tmp_path)]
    service.create_manifest({"name": "demo", "workspace_root": str(workspace)})
    manifest = service.store.get("demo")
    manifest.last_status = "running"
    service.store.save(manifest)


def test_toady_terminal_limit_rejects_extra_sessions(tmp_path, monkeypatch):
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


def test_toady_terminal_limit_allows_default_32_sessions(tmp_path, monkeypatch):
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


def test_toady_terminal_close_frees_limit_slot(tmp_path, monkeypatch):
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


def test_toady_terminal_can_be_rejoined_by_new_socket(tmp_path, monkeypatch):
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
