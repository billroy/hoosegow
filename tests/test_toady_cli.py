import pytest

import toady


def test_toady_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        toady.parse_args(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"toady {toady.__version__}"


def test_default_port_is_browser_safe():
    args = toady.parse_args([])

    assert args.port == 6060
    assert args.port not in toady.BROWSER_BLOCKED_PORTS


def test_run_server_rejects_browser_blocked_port_when_opening_browser(tmp_path, capsys):
    args = toady.parse_args([
        "--workspace",
        str(tmp_path),
        "--home",
        str(tmp_path / "state"),
        "--port",
        "6000",
    ])

    assert toady.run_server(args, str(tmp_path / "state")) == 1
    assert "blocked by Chromium-based browsers" in capsys.readouterr().err


def test_run_server_warns_for_browser_blocked_port_with_no_browser(tmp_path, monkeypatch, capsys):
    class FakeApp:
        config = {"toady_sandboxes": None}

    class FakeSocketIO:
        def run(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(toady, "require_auth_for_network_bind", lambda _host, _home: None)
    monkeypatch.setattr("server.app.create_app", lambda *_args, **_kwargs: FakeApp())
    monkeypatch.setattr("server.app.socketio", FakeSocketIO())
    args = toady.parse_args([
        "--workspace",
        str(tmp_path),
        "--home",
        str(tmp_path / "state"),
        "--port",
        "6000",
        "--no-browser",
    ])

    assert toady.run_server(args, str(tmp_path / "state")) == 0
    assert "Warning: port 6000 is blocked" in capsys.readouterr().err


def test_run_server_registers_shutdown_sandboxes_hook(tmp_path, monkeypatch):
    registered = []

    class FakeService:
        async def stop_running(self):
            return [{"slug": "demo"}]

    class FakeApp:
        config = {"toady_sandboxes": FakeService()}

    class FakeSocketIO:
        def run(self, *_args, **_kwargs):
            return None

    def fake_create_app(*_args, **_kwargs):
        return FakeApp()

    monkeypatch.setattr(toady, "require_auth_for_network_bind", lambda _host, _home: None)
    monkeypatch.setattr(toady.atexit, "register", lambda callback: registered.append(callback))
    monkeypatch.setattr("server.app.create_app", fake_create_app)
    monkeypatch.setattr("server.app.socketio", FakeSocketIO())
    args = toady.parse_args([
        "--workspace",
        str(tmp_path),
        "--home",
        str(tmp_path / "state"),
        "--no-browser",
        "--shutdown-sandboxes-on-exit",
    ])

    assert toady.run_server(args, str(tmp_path / "state")) == 0
    assert len(registered) == 1
