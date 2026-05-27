"""Socket.IO origin policy regression checks."""

import tempfile

from server.app import create_app, socketio, _socketio_origin_allowed


def test_socketio_origin_policy_allows_loopback_origins():
    assert _socketio_origin_allowed("http://127.0.0.1:5050")
    assert _socketio_origin_allowed("http://localhost:5050")
    assert _socketio_origin_allowed("http://[::1]:5050")


def test_socketio_origin_policy_allows_same_origin_from_environ():
    environ = {
        "wsgi.url_scheme": "http",
        "HTTP_HOST": "192.168.1.20:5050",
    }
    assert _socketio_origin_allowed("http://192.168.1.20:5050", environ)


def test_socketio_origin_policy_allows_forwarded_same_origin():
    environ = {
        "wsgi.url_scheme": "http",
        "HTTP_HOST": "127.0.0.1:5050",
        "HTTP_X_FORWARDED_PROTO": "https",
        "HTTP_X_FORWARDED_HOST": "example.ngrok-free.app",
    }
    assert _socketio_origin_allowed("https://example.ngrok-free.app", environ)


def test_socketio_origin_policy_rejects_unrelated_tunnels_without_explicit_allowlist():
    assert not _socketio_origin_allowed("https://abc123.ngrok-free.app")
    assert not _socketio_origin_allowed("https://abc123.ngrok.app")
    assert not _socketio_origin_allowed("https://abc123.ngrok.io")
    assert not _socketio_origin_allowed("https://abc123.sprites.app")


def test_socketio_origin_policy_allows_exact_explicit_allowlist(monkeypatch):
    monkeypatch.setenv(
        "BULLPEN_ALLOWED_ORIGINS",
        "https://abc123.ngrok-free.app, https://codex.sprites.app",
    )
    assert _socketio_origin_allowed("https://abc123.ngrok-free.app")
    assert _socketio_origin_allowed("https://codex.sprites.app")
    assert not _socketio_origin_allowed("https://other.ngrok-free.app")


def test_socketio_origin_policy_rejects_unrelated_origins():
    assert not _socketio_origin_allowed("https://evil.example")
    assert not _socketio_origin_allowed("https://not-ngrok-free.app.evil.example")
    assert not _socketio_origin_allowed("not-an-origin")


def test_socketio_cors_is_not_wildcard_by_default():
    with tempfile.TemporaryDirectory(prefix="bullpen_cors_") as ws:
        create_app(ws, no_browser=True)
        assert socketio.server.eio.cors_allowed_origins is _socketio_origin_allowed


def test_create_app_passes_websocket_debug_to_socketio_init(monkeypatch):
    calls = []
    original_init = socketio.init_app

    def capture_init(*args, **kwargs):
        calls.append(kwargs)
        return original_init(*args, **kwargs)

    monkeypatch.setattr(socketio, "init_app", capture_init)

    with tempfile.TemporaryDirectory(prefix="bullpen_cors_") as ws:
        create_app(ws, no_browser=True, websocket_debug=False)
        create_app(ws, no_browser=True, websocket_debug=True)

    assert calls[0]["logger"] is False
    assert calls[0]["engineio_logger"] is False
    assert calls[1]["logger"] is True
    assert calls[1]["engineio_logger"] is True
