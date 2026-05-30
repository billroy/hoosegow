"""Regression checks for Socket.IO transport selection."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_prefers_websocket_with_polling_fallback():
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "const socket = io({" in app_js
    assert "transports: ['websocket', 'polling']" in app_js
    assert "const socket = io();" not in app_js


def test_requirements_include_threading_websocket_dependency():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "simple-websocket" in requirements
    assert "microsandbox==0.5.3" in requirements
    assert "websocket-client" not in requirements
    assert "eventlet" not in requirements
