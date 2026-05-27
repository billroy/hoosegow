import pytest

import toady


def test_toady_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        toady.parse_args(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"toady {toady.__version__}"


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
