from server.app import create_app, socketio


class FakePtyDriver:
    def __init__(self, *, base_url, token, timeout=5.0):
        self.base_url = base_url
        self.token = token

    def open(self, terminal_id, *, cwd="/workspace", shell="/bin/bash", cols=100, rows=30):
        return {"event": "opened", "id": terminal_id, "cwd": cwd, "pid": 1234}

    def poll(self, terminal_id, *, since, timeout=1.0):
        return {"events": [], "next_seq": since, "status": "running"}

    def close(self, terminal_id):
        return {"event": "ok"}


class FakeRuntime:
    async def exists(self, _name):
        return False

    async def ensure_installed(self):
        return None

    async def create(self, _spec):
        return object()

    async def stop(self, _name):
        return None

    async def remove(self, _name):
        return None


async def async_noop(*_args, **_kwargs):
    return None


def test_socket_create_allows_shared_workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    workspace_root = tmp_path / "work"
    workspace_root.mkdir()
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    service = app.config["toady_sandboxes"]
    service.browse_roots = [str(tmp_path)]
    service.port_pool = (63100, 63105)
    client = socketio.test_client(app)
    client.get_received()

    first = client.emit(
        "sandbox:create",
        {"name": "sandbox", "workspace_root": str(workspace_root)},
        callback=True,
    )
    manifest = service.store.get("sandbox")
    manifest.last_status = "running"
    service.store.save(manifest)
    second = client.emit(
        "sandbox:create",
        {"name": "sandbox-2", "workspace_root": str(workspace_root)},
        callback=True,
    )

    client.disconnect()
    assert first["ok"] is True
    assert second["ok"] is True
    assert first["sandbox"]["canonical_workspace_path"] == second["sandbox"]["canonical_workspace_path"]


def test_socket_lifecycle_smoke_create_start_terminal_port_destroy(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    monkeypatch.setattr("server.sandboxes.ensure_host_ports_available", lambda _ports: None)
    monkeypatch.setattr("server.sandboxes.MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr("server.sandboxes.prepare_runtime_dirs", async_noop)
    monkeypatch.setattr("server.sandboxes.disable_guest_ipv6_for_claude", async_noop)
    monkeypatch.setattr("server.sandboxes.verify_mount_access", async_noop)
    monkeypatch.setattr("server.sandboxes.configure_codex_cli", async_noop)
    monkeypatch.setattr("server.sandboxes.start_pty_controller", async_noop)
    monkeypatch.setattr("server.sandboxes.detach_sandbox", async_noop)
    monkeypatch.setattr("server.sandboxes.verify_detached_sandbox", async_noop)
    monkeypatch.setattr("server.sandboxes.wait_for_controller_health", lambda _port: None)
    monkeypatch.setattr("server.app.PtyDriver", FakePtyDriver)
    workspace_root = tmp_path / "work"
    workspace_root.mkdir()
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    service = app.config["toady_sandboxes"]
    service.browse_roots = [str(tmp_path)]
    service.port_pool = (63100, 63110)
    client = socketio.test_client(app)
    client.get_received()

    created = client.emit(
        "sandbox:create",
        {"name": "smoke", "workspace_root": str(workspace_root)},
        callback=True,
    )
    published = client.emit(
        "port:publish",
        {"sandbox_id": "smoke", "guest_port": 5173},
        callback=True,
    )
    logs = client.emit("sandbox:logs", {"id": "smoke"}, callback=True)
    started = client.emit("sandbox:start", {"id": "smoke"}, callback=True)
    terminal = client.emit(
        "sandbox:terminal:open",
        {"sandbox_id": "smoke", "cols": 80, "rows": 24},
        callback=True,
    )
    closed = client.emit(
        "sandbox:terminal:close",
        {"terminal_id": terminal["terminal"]["id"]},
        callback=True,
    )
    destroyed = client.emit(
        "sandbox:destroy",
        {"id": "smoke", "purge": True},
        callback=True,
    )

    client.disconnect()
    assert created["ok"] is True
    assert published["ok"] is True
    assert published["port"]["guest_port"] == 5173
    assert logs["ok"] is True
    assert any('"event": "created"' in line for line in logs["logs"])
    assert any('"event": "port_published"' in line for line in logs["logs"])
    assert started["ok"] is True
    assert started["sandbox"]["last_status"] == "running"
    assert terminal["ok"] is True
    assert closed["ok"] is True
    assert destroyed["ok"] is True
    assert service.get("smoke") is None
