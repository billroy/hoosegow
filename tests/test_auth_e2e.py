"""End-to-end tests for HTTP + Socket.IO authentication gating.

The test harness relies on the autouse ``_isolate_global_registry``
fixture in ``tests/conftest.py`` to patch ``server.workspace_manager.GLOBAL_DIR``
to a per-test throwaway directory. For each test that needs auth enabled
we write a ``.env`` file into that patched directory *before* calling
``create_app`` so the app picks up the credential.
"""

import os
import tempfile
import json
from datetime import timedelta

import pytest

import server.workspace_manager as wm
from server import auth
from server import mcp_auth
from server.app import create_app, socketio


USERNAME = "admin"
PASSWORD = "correct horse"
SECOND_USERNAME = "alice"
SECOND_PASSWORD = "wonderland"


def _seed_credentials(global_dir, users=None):
    """Write credentials into the isolated global dir."""
    if users is None:
        users = {
            USERNAME: auth.generate_password_hash(PASSWORD),
            SECOND_USERNAME: auth.generate_password_hash(SECOND_PASSWORD),
        }
    os.makedirs(global_dir, exist_ok=True)
    existing = auth.parse_env_file(auth.env_path(global_dir))
    updated = auth.apply_credentials_mapping(existing, users)
    auth.write_env_file(
        auth.env_path(global_dir),
        updated,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_app(tmp_path):
    """Flask app with auth ENABLED via a seeded env file."""
    _seed_credentials(wm.GLOBAL_DIR)
    with tempfile.TemporaryDirectory(prefix="bullpen_auth_") as ws:
        app = create_app(ws, no_browser=True)
        yield app
    auth.reset_auth_cache()


@pytest.fixture
def auth_client(auth_app):
    return auth_app.test_client()


@pytest.fixture
def noauth_app(tmp_path):
    """Flask app with auth DISABLED (no env file written)."""
    # Sanity: the conftest fixture patched GLOBAL_DIR, and we do NOT seed.
    assert not os.path.exists(auth.env_path(wm.GLOBAL_DIR))
    with tempfile.TemporaryDirectory(prefix="bullpen_noauth_") as ws:
        app = create_app(ws, no_browser=True)
        yield app
    auth.reset_auth_cache()


@pytest.fixture
def noauth_client(noauth_app):
    return noauth_app.test_client()


def _login(client, username=USERNAME, password=PASSWORD):
    """Perform a full login flow; returns the POST response."""
    # Prime the session with a CSRF token.
    client.get("/login")
    with client.session_transaction() as sess:
        token = sess.get("csrf_token")
    return client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": token or "",
        },
    )


def _csrf_token(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or ""


def _session_cookie_header(response):
    for header in response.headers.getlist("Set-Cookie"):
        if header.startswith("session="):
            return header
    return ""


# ---------------------------------------------------------------------------
# Auth ENABLED — HTTP
# ---------------------------------------------------------------------------


class TestHttpAuthEnabled:
    def test_health_public_without_session(self, auth_client):
        r = auth_client.get("/health")
        assert r.status_code == 200
        assert r.get_json() == {"ok": True}

    def test_index_unauthenticated_redirects_to_login(self, auth_client):
        r = auth_client.get("/")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_login_page_accessible(self, auth_client):
        r = auth_client.get("/login")
        assert r.status_code == 200
        assert b"loginForm" in r.data

    def test_login_page_public_without_session(self, auth_client):
        # A fresh client with no cookies must still get the login page.
        r = auth_client.get("/login")
        assert r.status_code == 200

    def test_style_css_public(self, auth_client):
        # The login page needs its stylesheet to load without auth.
        r = auth_client.get("/style.css")
        assert r.status_code == 200

    def test_app_js_gated(self, auth_client):
        # Non-login static assets are gated.
        r = auth_client.get("/app.js")
        assert r.status_code == 302

    def test_login_success_redirects_to_index(self, auth_client):
        r = _login(auth_client)
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/")

    def test_login_sets_session(self, auth_client):
        _login(auth_client)
        with auth_client.session_transaction() as sess:
            assert sess.get("authenticated") is True
            assert sess.get("username") == USERNAME
            assert sess.permanent is True

    def test_login_sets_persistent_session_cookie(self, auth_client):
        r = _login(auth_client)
        cookie = _session_cookie_header(r)
        assert cookie
        assert "Expires=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie

    def test_authenticated_cookie_survives_app_restart(self):
        _seed_credentials(wm.GLOBAL_DIR)
        with tempfile.TemporaryDirectory(prefix="bullpen_auth_restart_") as ws:
            first_app = create_app(ws, no_browser=True)
            first_client = first_app.test_client()
            _login(first_client)
            cookie = first_client.get_cookie(first_app.config["SESSION_COOKIE_NAME"])
            assert cookie is not None

            second_app = create_app(ws, no_browser=True)
            second_client = second_app.test_client()
            second_client.set_cookie(second_app.config["SESSION_COOKIE_NAME"], cookie.value)

            r = second_client.get("/")
            assert r.status_code == 200
        auth.reset_auth_cache()

    def test_session_lifetime_uses_bounded_env_setting(self, monkeypatch):
        monkeypatch.setenv("BULLPEN_SESSION_DAYS", "7")
        _seed_credentials(wm.GLOBAL_DIR)
        with tempfile.TemporaryDirectory(prefix="bullpen_auth_lifetime_") as ws:
            app = create_app(ws, no_browser=True)
            assert app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(days=7)
        auth.reset_auth_cache()

    def test_login_failure_wrong_password(self, auth_client):
        r = _login(auth_client, password="nope")
        assert r.status_code == 302
        assert "error=1" in r.headers["Location"]
        with auth_client.session_transaction() as sess:
            assert sess.get("authenticated") is not True

    def test_login_failure_wrong_username(self, auth_client):
        r = _login(auth_client, username="eve")
        assert r.status_code == 302
        assert "error=1" in r.headers["Location"]

    def test_login_throttles_after_repeated_failures(self, auth_client):
        for _ in range(4):
            r = _login(auth_client, password="nope")
            assert r.status_code == 302
            assert "error=1" in r.headers["Location"]

        throttled = _login(auth_client, password="nope")
        assert throttled.status_code == 302
        assert "error=throttle" in throttled.headers["Location"]

        still_throttled = _login(auth_client)
        assert still_throttled.status_code == 302
        assert "error=throttle" in still_throttled.headers["Location"]
        with auth_client.session_transaction() as sess:
            assert sess.get("authenticated") is not True

    def test_login_success_second_user(self, auth_client):
        r = _login(auth_client, username=SECOND_USERNAME, password=SECOND_PASSWORD)
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/")
        with auth_client.session_transaction() as sess:
            assert sess.get("authenticated") is True
            assert sess.get("username") == SECOND_USERNAME

    def test_login_csrf_invalid(self, auth_client):
        # Submit without a valid CSRF token.
        r = auth_client.post(
            "/login",
            data={"username": USERNAME, "password": PASSWORD, "csrf_token": "bogus"},
        )
        assert r.status_code == 302
        assert "error=csrf" in r.headers["Location"]

    def test_logout_clears_session(self, auth_client):
        _login(auth_client)
        r = auth_client.post("/logout", data={"csrf_token": _csrf_token(auth_client)})
        assert r.status_code == 302
        with auth_client.session_transaction() as sess:
            assert sess.get("authenticated") is not True

    def test_logout_get_rejected(self, auth_client):
        _login(auth_client)
        r = auth_client.get("/logout")
        assert r.status_code == 405

    def test_api_files_unauthenticated_returns_401_when_xhr(self, auth_client):
        r = auth_client.get(
            "/api/files",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert r.status_code == 401

    def test_api_files_unauthenticated_redirects_when_browser(self, auth_client):
        r = auth_client.get("/api/files")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_file_write_unauthenticated_rejected(self, auth_client):
        r = auth_client.put(
            "/api/files/foo.txt",
            data="hi",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert r.status_code == 401

    def test_next_param_preserved(self, auth_client):
        r = auth_client.get("/")
        loc = r.headers["Location"]
        assert "next=/" in loc

    def test_authenticated_can_hit_api(self, auth_client):
        _login(auth_client)
        r = auth_client.get("/api/files")
        assert r.status_code == 200

    def test_authenticated_index_200(self, auth_client):
        _login(auth_client)
        r = auth_client.get("/")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Auth ENABLED — Socket.IO
# ---------------------------------------------------------------------------


class TestSocketIoAuthEnabled:
    def test_socketio_connect_unauthenticated_rejected(self, auth_app):
        # No session cookie → server should reject the connect.
        client = socketio.test_client(auth_app)
        assert client.is_connected() is False

    def test_socketio_connect_authenticated_accepted(self, auth_app):
        # Perform a real login via the Flask test client to obtain a
        # session cookie, then use flask_test_client= to share that
        # cookie jar with the Socket.IO test client.
        http = auth_app.test_client()
        _login(http)
        sio_client = socketio.test_client(auth_app, flask_test_client=http)
        assert sio_client.is_connected() is True
        events = sio_client.get_received()
        names = [e["name"] for e in events]
        assert "state:init" in names
        sio_client.disconnect()

    def test_socketio_connect_mcp_token_accepted_without_session(self, auth_app):
        token = mcp_auth.read_workspace_mcp_token(auth_app.config["bp_dir"])

        client = socketio.test_client(auth_app, auth={"mcp_token": token})
        assert client.is_connected() is True
        events = client.get_received()
        assert [e["name"] for e in events] == ["state:init"]
        client.disconnect()

    def test_socketio_rotated_mcp_token_invalidates_old_connects(self, auth_app):
        old_token = mcp_auth.read_workspace_mcp_token(auth_app.config["bp_dir"])

        new_token = mcp_auth.rotate_workspace_mcp_token(
            auth_app.config["bp_dir"],
            host=auth_app.config["host"],
            port=auth_app.config["port"],
        )

        rejected = socketio.test_client(auth_app, auth={"mcp_token": old_token})
        assert rejected.is_connected() is False

        accepted = socketio.test_client(auth_app, auth={"mcp_token": new_token})
        assert accepted.is_connected() is True
        accepted.disconnect()


# ---------------------------------------------------------------------------
# Auth DISABLED — everything should just work
# ---------------------------------------------------------------------------


class TestAuthDisabled:
    def test_no_auth_health_endpoint(self, noauth_client):
        r = noauth_client.get("/health")
        assert r.status_code == 200
        assert r.get_json() == {"ok": True}

    def test_no_auth_index_accessible(self, noauth_client):
        r = noauth_client.get("/")
        assert r.status_code == 200

    def test_no_auth_api_files(self, noauth_client):
        r = noauth_client.get("/api/files")
        assert r.status_code == 200

    def test_no_auth_login_page_redirects_to_index(self, noauth_client):
        # With auth disabled there is no login page — /login bounces
        # the user to the app.
        r = noauth_client.get("/login")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/")

    def test_no_auth_socketio_connects(self, noauth_app):
        client = socketio.test_client(noauth_app)
        assert client.is_connected() is True
        client.disconnect()
