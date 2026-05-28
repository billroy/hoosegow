"""Flask + socket.io app factory."""

import os
import subprocess
import sys
import json
import base64
from datetime import datetime, timedelta, timezone
import threading
import uuid
from time import monotonic
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    request,
    session,
    url_for,
)
from flask_socketio import SocketIO, join_room

from server import auth
from server import base as toady_base
from server.pty_driver import PtyDriver, PtyDriverError
from server.sandboxes import SandboxService, SandboxServiceError, browse_roots_from_env
from server.toady_validation import ValidationError, validate_slug


socketio = SocketIO()

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_LOGIN_THROTTLE_WINDOW_SECONDS = 5 * 60
_LOGIN_THROTTLE_MAX_FAILURES = 5
_LOGIN_THROTTLE_BLOCK_SECONDS = 60
_DEFAULT_SESSION_DAYS = 30
_MAX_SESSION_DAYS = 365
_TERMINAL_REPLAY_LIMIT_BYTES = 5 * 1024 * 1024
_TERMINAL_REPLAY_LIMIT_LINES = 10_000
_TERMINAL_REPLAY_TRUNCATED_MARKER = b"\r\n[Toady replay truncated]\r\n"
_SERVER_LOG_MAX_BYTES = 10 * 1024 * 1024
_SERVER_LOG_BACKUPS = 5


def _rotate_log_if_needed(path, *, max_bytes=_SERVER_LOG_MAX_BYTES, backups=_SERVER_LOG_BACKUPS):
    try:
        if not os.path.exists(path) or os.path.getsize(path) <= max_bytes:
            return
    except OSError:
        return
    for index in range(backups - 1, 0, -1):
        src = f"{path}.{index}"
        dst = f"{path}.{index + 1}"
        if os.path.exists(src):
            try:
                os.replace(src, dst)
            except OSError:
                pass
    try:
        os.replace(path, f"{path}.1")
    except OSError:
        pass


def _append_json_log(log_path, event, **fields):
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        _rotate_log_if_needed(log_path)
        record = {
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "event": event,
        }
        record.update({key: value for key, value in fields.items() if value is not None})
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        return


class ToadyStateManager:
    """Minimal state-directory holder for the Toady sandbox UI."""

    def __init__(self, global_dir=None):
        self._global_dir = os.path.abspath(os.path.expanduser(global_dir or os.environ.get("TOADY_HOME", "~/.toady")))
        os.makedirs(self._global_dir, exist_ok=True)

    @property
    def global_dir(self):
        return self._global_dir

    def all_workspaces(self):
        return []

    def list_projects(self, **_kwargs):
        return []

    def list_visible_projects(self, **_kwargs):
        return []

    def get(self, _workspace_id):
        return None

    def get_or_activate(self, _workspace_id):
        return None

    def get_bp_dir(self, workspace_id):
        raise KeyError(f"Unknown workspace: {workspace_id}")


def _origin_host(origin):
    if not origin:
        return ""
    parsed = urlparse(origin)
    return (parsed.hostname or "").lower()


def _request_origin(environ, *, forwarded=False):
    if not environ:
        return ""
    if forwarded:
        scheme = environ.get("HTTP_X_FORWARDED_PROTO", environ.get("wsgi.url_scheme", "http"))
        host = environ.get("HTTP_X_FORWARDED_HOST", environ.get("HTTP_HOST", ""))
    else:
        scheme = environ.get("wsgi.url_scheme", "http")
        host = environ.get("HTTP_HOST", "")
    scheme = scheme.split(",")[0].strip()
    host = host.split(",")[0].strip()
    return f"{scheme}://{host}" if scheme and host else ""


def _normalize_origin(origin):
    if not origin:
        return ""
    parsed = urlparse(origin)
    scheme = (parsed.scheme or "").lower()
    netloc = (parsed.netloc or "").lower()
    if not scheme or not netloc:
        return ""
    return f"{scheme}://{netloc}"


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _session_lifetime():
    days = _safe_int(os.environ.get("TOADY_SESSION_DAYS", os.environ.get("BULLPEN_SESSION_DAYS", "")), _DEFAULT_SESSION_DAYS)
    days = max(1, min(days, _MAX_SESSION_DAYS))
    return timedelta(days=days)


def _configured_allowed_origins():
    raw = os.environ.get("TOADY_ALLOWED_ORIGINS", os.environ.get("BULLPEN_ALLOWED_ORIGINS", ""))
    allowed = set()
    for item in raw.split(","):
        normalized = _normalize_origin(item.strip())
        if normalized:
            allowed.add(normalized)
    return allowed


def _socketio_origin_allowed(origin, environ=None):
    """Allow only local, same-origin, or explicitly configured origins.

    Socket.IO event handlers trust an accepted handshake for the life of the
    session, so we keep the origin policy tight here rather than relying on a
    second per-event CSRF layer.
    """
    if not origin:
        return True

    normalized_origin = _normalize_origin(origin)
    if not normalized_origin:
        return False

    origin_host = _origin_host(normalized_origin)
    if origin_host in _LOOPBACK_HOSTS:
        return True

    same_origin = _normalize_origin(_request_origin(environ))
    forwarded_origin = _normalize_origin(_request_origin(environ, forwarded=True))
    if normalized_origin in {same_origin, forwarded_origin}:
        return True

    return normalized_origin in _configured_allowed_origins()


def create_app(
    workspace,
    no_browser=False,
    global_dir=None,
    host="127.0.0.1",
    port=5000,
    websocket_debug=False,
    start_without_project=False,
    terminal_limit=None,
):
    """Create and configure the Flask + SocketIO app."""
    workspace = os.path.abspath(workspace)
    start_without_project = True
    manager = ToadyStateManager(global_dir=global_dir)
    startup_id = None
    bp_dir = None

    app = Flask(
        __name__,
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"),
        static_url_path="",
    )

    # --- Authentication bootstrap ---------------------------------------
    # Re-read the env file on every create_app so tests (which patch the
    # global dir per-test) see a fresh state and do not leak credentials
    # between unrelated test cases.
    auth.reset_auth_cache()
    auth.load_credentials(manager.global_dir)
    app.config["SECRET_KEY"] = auth.load_or_create_secret_key(manager.global_dir)
    production = os.environ.get("TOADY_PRODUCTION", os.environ.get("BULLPEN_PRODUCTION", "")) == "1"
    session_lifetime = _session_lifetime()
    app.config.update(
        PERMANENT_SESSION_LIFETIME=session_lifetime,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
    )
    if production:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    if auth.auth_enabled():
        users = auth.get_users()
        user_count = len(users)
        primary = auth.get_username() or "unknown"
        print(
            f"Toady auth: ENABLED ({user_count} user(s), primary={primary}, "
            f"session_days={session_lifetime.days})",
            file=sys.stderr,
        )
    else:
        print(
            "Toady auth: DISABLED (no credentials configured). "
            "Run `python3 toady.py --set-password` to enable login.",
            file=sys.stderr,
        )
    # --------------------------------------------------------------------

    app.config["manager"] = manager
    app.config["toady_sandboxes"] = SandboxService(
        home=manager.global_dir,
        browse_roots=browse_roots_from_env(),
        source_root=os.path.dirname(os.path.dirname(__file__)),
        port_pool=os.environ.get("TOADY_PORT_POOL", "3000-3099"),
        max_sandboxes=os.environ.get("TOADY_MAX_SANDBOXES", 8),
        max_total_vcpus=os.environ.get("TOADY_MAX_TOTAL_VCPUS"),
        max_total_memory_mib=os.environ.get("TOADY_MAX_TOTAL_MEMORY_MIB"),
    )
    app.config["startup_workspace_id"] = startup_id
    # Backward-compat: existing handlers still use these directly
    app.config["workspace"] = None if start_without_project else workspace
    app.config["bp_dir"] = bp_dir
    app.config["start_without_project"] = start_without_project
    app.config["no_browser"] = no_browser

    def _server_log(event, **fields):
        _append_json_log(os.path.join(manager.global_dir, "logs", "server.log"), event, **fields)

    app.config["toady_server_log"] = _server_log

    login_failures = {}

    def _client_ip():
        forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return forwarded or request.remote_addr or "unknown"

    def _login_throttle_keys(username):
        normalized = (username or "").strip().lower() or "<blank>"
        client_ip = _client_ip()
        return (("ip", client_ip), ("user", client_ip, normalized))

    def _login_bucket(key, now):
        bucket = login_failures.setdefault(key, {"failures": [], "blocked_until": 0.0})
        bucket["failures"] = [
            ts for ts in bucket["failures"]
            if now - ts <= _LOGIN_THROTTLE_WINDOW_SECONDS
        ]
        return bucket

    def _login_is_throttled(username):
        now = monotonic()
        return any(
            _login_bucket(key, now)["blocked_until"] > now
            for key in _login_throttle_keys(username)
        )

    def _record_login_failure(username):
        now = monotonic()
        throttled = False
        for key in _login_throttle_keys(username):
            bucket = _login_bucket(key, now)
            bucket["failures"].append(now)
            if len(bucket["failures"]) >= _LOGIN_THROTTLE_MAX_FAILURES:
                bucket["blocked_until"] = max(
                    bucket["blocked_until"],
                    now + _LOGIN_THROTTLE_BLOCK_SECONDS,
                )
                throttled = True
        return throttled

    def _clear_login_failures(username):
        for key in _login_throttle_keys(username):
            login_failures.pop(key, None)

    socketio.init_app(
        app,
        cors_allowed_origins=_socketio_origin_allowed,
        async_mode="threading",
        logger=websocket_debug,
        engineio_logger=websocket_debug,
    )
    app.config["terminal_manager"] = None
    app.config["toady_terminals"] = {}
    app.config["toady_terminal_numbers"] = {}
    app.config["toady_terminals_lock"] = threading.RLock()
    app.config["toady_terminal_limit"] = max(
        1,
        _safe_int(terminal_limit or os.environ.get("TOADY_TERMINAL_LIMIT", ""), 32),
    )

    app.config["host"] = host
    app.config["port"] = port
    app.config["base_prepare"] = {
        "running": False,
        "returncode": None,
        "logs": [],
        "started_at": None,
        "finished_at": None,
        "duration_seconds": None,
        "automatic": False,
        "rebuild": False,
    }
    app.config["base_prepare_lock"] = threading.RLock()
    app.config["mcp_tokens_by_workspace"] = {}

    # --- Public (unauthenticated) assets allowlist ---------------------
    # These paths must load without a session so the login page can be
    # rendered and styled before the user authenticates.
    PUBLIC_STATIC_FILES = {"login.html", "style.css", "favicon.ico"}
    REMOVED_PRODUCT_API_PREFIXES = (
        "/api/commits",
        "/api/files",
        "/api/worker",
        "/api/export",
        "/api/import",
        "/api/service",
    )

    @app.before_request
    def _assign_request_id():
        request.environ["toady.request_id"] = uuid.uuid4().hex[:12]

    @app.after_request
    def _log_http_response(response):
        request_id = request.environ.get("toady.request_id") or uuid.uuid4().hex[:12]
        response.headers["X-Toady-Request-Id"] = request_id
        _server_log(
            "http_request",
            request_id=request_id,
            method=request.method,
            path=request.path,
            endpoint=request.endpoint or "",
            status=response.status_code,
        )
        return response

    @app.before_request
    def _gate_static_assets():
        """Gate static asset requests (served by Flask's built-in static
        handler since ``static_url_path=""``) on auth, except for the
        explicit allowlist above. Non-static routes are gated by the
        per-view ``@require_auth`` decorator instead."""
        if not auth.auth_enabled():
            return None
        if session.get("authenticated"):
            return None
        ep = request.endpoint or ""
        if ep != "static":
            return None
        filename = (request.view_args or {}).get("filename", "")
        if filename in PUBLIC_STATIC_FILES:
            return None
        if auth.is_xhr_request(request):
            return jsonify({"error": "authentication required"}), 401
        return redirect(url_for("login"))

    @app.before_request
    def _reject_removed_product_apis():
        if request.path.startswith(REMOVED_PRODUCT_API_PREFIXES):
            abort(404)
        return None

    @app.route("/")
    @auth.require_auth
    def index():
        return app.send_static_file("index.html")

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"ok": True}), 200

    def _sandbox_service():
        return app.config["toady_sandboxes"]

    def _sandbox_error_payload(exc):
        return {"ok": False, "error": str(exc)}

    def _toady_terminal_error_payload(exc):
        return {"ok": False, "error": str(exc)}

    def _log_socket_event(socket_event, *, sandbox_id=None, terminal_id=None, ok=True, error=None, **fields):
        _server_log(
            "socket_event",
            socket_event=socket_event,
            sandbox_id=sandbox_id,
            terminal_id=terminal_id,
            ok=bool(ok),
            error=str(error) if error else None,
            **fields,
        )

    def _emit_sandboxes_updated():
        socketio.emit("sandboxes:updated", {"sandboxes": _sandbox_service().list()}, to="authenticated")

    def _emit_base_status(status):
        socketio.emit("base:status", {"base": status}, to="authenticated")

    def _toady_terminal_room(terminal_id):
        return f"toady-terminal:{terminal_id}"

    def _toady_terminal_driver(manifest):
        controller = dict(manifest.controller or {})
        host_port = int(controller.get("host_port") or 0)
        token = str(controller.get("token") or "")
        if not host_port or not token:
            raise SandboxServiceError("Sandbox terminal controller is not configured.")
        return PtyDriver(base_url=f"http://127.0.0.1:{host_port}", token=token)

    def _toady_terminal_session(terminal_id):
        with app.config["toady_terminals_lock"]:
            session_info = app.config["toady_terminals"].get(terminal_id)
        if not session_info:
            raise SandboxServiceError("Unknown terminal session.")
        if request.sid not in session_info.get("clients", set()):
            raise SandboxServiceError("Join the terminal before controlling it.")
        return session_info

    def _toady_terminal_payload(session_info):
        return {
            "id": session_info.get("id"),
            "sandbox_id": session_info.get("sandbox_id"),
            "number": session_info.get("number"),
            "cwd": session_info.get("cwd") or "/workspace",
            "pid": session_info.get("pid"),
            "status": session_info.get("status") or "running",
            "exit_code": session_info.get("exit_code"),
        }

    def _toady_terminal_replay(session_info):
        replay = bytes(session_info.get("replay") or b"")
        if session_info.get("replay_truncated"):
            replay = _TERMINAL_REPLAY_TRUNCATED_MARKER + replay
        return {
            "data": base64.b64encode(replay).decode("ascii"),
            "truncated": bool(session_info.get("replay_truncated")),
        }

    def _record_toady_terminal_output(terminal_id, data_b64):
        try:
            chunk = base64.b64decode(str(data_b64 or "").encode("ascii"))
        except Exception:
            chunk = b""
        if not chunk:
            return
        with app.config["toady_terminals_lock"]:
            session_info = app.config["toady_terminals"].get(terminal_id)
            if not session_info:
                return
            replay = session_info.setdefault("replay", bytearray())
            replay.extend(chunk)
            if len(replay) > _TERMINAL_REPLAY_LIMIT_BYTES:
                del replay[: len(replay) - _TERMINAL_REPLAY_LIMIT_BYTES]
                session_info["replay_truncated"] = True
            newline_count = replay.count(b"\n")
            if newline_count > _TERMINAL_REPLAY_LIMIT_LINES:
                trim_lines = newline_count - _TERMINAL_REPLAY_LIMIT_LINES
                trim_index = -1
                search_from = 0
                for _index in range(trim_lines):
                    trim_index = replay.find(b"\n", search_from)
                    if trim_index < 0:
                        break
                    search_from = trim_index + 1
                if trim_index >= 0:
                    del replay[: trim_index + 1]
                    session_info["replay_truncated"] = True

    def _close_toady_terminal(terminal_id, *, emit_closed=True):
        with app.config["toady_terminals_lock"]:
            session_info = app.config["toady_terminals"].pop(terminal_id, None)
            if session_info:
                session_info["alive"] = False
        if not session_info:
            return
        try:
            session_info["driver"].close(terminal_id)
        except Exception:
            pass
        if emit_closed:
            socketio.emit("sandbox:terminal:closed", {"terminal_id": terminal_id}, to=_toady_terminal_room(terminal_id))

    def _close_toady_sandbox_terminals(sandbox_id):
        with app.config["toady_terminals_lock"]:
            terminal_ids = [
                terminal_id
                for terminal_id, session_info in app.config["toady_terminals"].items()
                if session_info.get("sandbox_id") == sandbox_id
            ]
        for terminal_id in terminal_ids:
            _close_toady_terminal(terminal_id)

    def _toady_sandbox_terminal_count(sandbox_id):
        with app.config["toady_terminals_lock"]:
            return sum(
                1
                for session_info in app.config["toady_terminals"].values()
                if session_info.get("sandbox_id") == sandbox_id
            )

    def _base_prepare_payload(*, include_logs=True):
        with app.config["base_prepare_lock"]:
            state = dict(app.config.get("base_prepare") or {})
        payload = {
            "running": bool(state.get("running")),
            "returncode": state.get("returncode"),
            "duration_seconds": state.get("duration_seconds"),
            "automatic": bool(state.get("automatic")),
            "rebuild": bool(state.get("rebuild")),
        }
        if include_logs:
            payload["logs"] = list(state.get("logs") or [])
        return payload

    def _toady_terminal_poll(terminal_id):
        while True:
            with app.config["toady_terminals_lock"]:
                session_info = app.config["toady_terminals"].get(terminal_id)
                if not session_info or not session_info.get("alive"):
                    return
                driver = session_info["driver"]
                since = int(session_info.get("seq") or 0)
            try:
                payload = driver.poll(terminal_id, since=since, timeout=1.0)
            except PtyDriverError as exc:
                socketio.emit(
                    "sandbox:terminal:error",
                    {"terminal_id": terminal_id, "error": str(exc)},
                    to=_toady_terminal_room(terminal_id),
                )
                with app.config["toady_terminals_lock"]:
                    session_info = app.config["toady_terminals"].get(terminal_id)
                    if session_info:
                        session_info["alive"] = False
                        session_info["status"] = "error"
                return

            events = payload.get("events") or []
            next_seq = int(payload.get("next_seq") or since)
            with app.config["toady_terminals_lock"]:
                session_info = app.config["toady_terminals"].get(terminal_id)
                if not session_info:
                    return
                session_info["seq"] = next_seq

            for event in events:
                event_name = event.get("event")
                if event_name == "output":
                    _record_toady_terminal_output(terminal_id, event.get("data") or "")
                    socketio.emit(
                        "sandbox:terminal:output",
                        {"terminal_id": terminal_id, "data": event.get("data") or ""},
                        to=_toady_terminal_room(terminal_id),
                    )
                elif event_name == "exit":
                    with app.config["toady_terminals_lock"]:
                        session_info = app.config["toady_terminals"].get(terminal_id)
                        if session_info:
                            session_info["status"] = "exited"
                            session_info["exit_code"] = event.get("exit_code")
                            session_info["alive"] = False
                    socketio.emit(
                        "sandbox:terminal:exit",
                        {"terminal_id": terminal_id, "exit_code": event.get("exit_code")},
                        to=_toady_terminal_room(terminal_id),
                    )
                    return
                elif event_name == "error":
                    socketio.emit(
                        "sandbox:terminal:error",
                        {"terminal_id": terminal_id, "error": event.get("error") or "Terminal error"},
                        to=_toady_terminal_room(terminal_id),
                    )

    def _base_prepare_status_message(rebuild=False, automatic=False):
        if rebuild:
            return "Rebuilding sandbox runtime..."
        if automatic:
            return "Setting up sandbox runtime..."
        return "Setting up sandbox runtime..."

    def _set_base_prepare_started(*, rebuild=False, automatic=False):
        state = app.config["base_prepare"]
        with app.config["base_prepare_lock"]:
            if state.get("running"):
                return False
            state["running"] = True
            state["returncode"] = None
            state["logs"] = []
            state["started_at"] = monotonic()
            state["finished_at"] = None
            state["duration_seconds"] = None
            state["automatic"] = bool(automatic)
            state["rebuild"] = bool(rebuild)
        return True

    def _start_base_prepare(rebuild=False, *, automatic=False):
        if not _set_base_prepare_started(rebuild=rebuild, automatic=automatic):
            _log_socket_event("base:prepare", ok=True, started=False, automatic=automatic)
            return False
        socketio.start_background_task(_base_prepare_worker, rebuild, automatic)
        _log_socket_event("base:prepare", ok=True, started=True, rebuild=rebuild, automatic=automatic)
        return True

    def _overlay_base_prepare_status(status):
        with app.config["base_prepare_lock"]:
            state = dict(app.config["base_prepare"])
        if state.get("running"):
            status["prepared"] = False
            status["state"] = "preparing"
            status["message"] = _base_prepare_status_message(
                rebuild=bool(state.get("rebuild")),
                automatic=bool(state.get("automatic")),
            )
        status["prepare"] = _base_prepare_payload(include_logs=False)
        return status

    def _auto_start_base_prepare_if_needed(status):
        if status.get("prepared") or status.get("state") != "missing":
            return False
        return _start_base_prepare(False, automatic=True)

    def _base_prepare_worker(rebuild=False, automatic=False):
        if not app.config["base_prepare"].get("running"):
            _set_base_prepare_started(rebuild=rebuild, automatic=automatic)
        state = app.config["base_prepare"]

        def emit_base_log(line):
            line = str(line or "")
            if not line:
                return
            with app.config["base_prepare_lock"]:
                state.setdefault("logs", []).append(line)
                if len(state["logs"]) > 500:
                    del state["logs"][: len(state["logs"]) - 500]
            socketio.emit("base:log", {"line": line}, to="authenticated")

        _emit_base_status({
            "name": "toady-microsandbox-local",
            "prepared": False,
            "state": "preparing",
            "message": _base_prepare_status_message(rebuild=rebuild, automatic=automatic),
        })
        root = os.path.dirname(os.path.dirname(__file__))
        command = [
            sys.executable,
            os.path.join(root, "toady.py"),
            "--prepare-base" if not rebuild else "--rebuild-base",
            "--home",
            manager.global_dir,
        ]
        emit_base_log("$ " + " ".join(command))
        try:
            proc = subprocess.Popen(
                command,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                emit_base_log(line.rstrip("\n"))
            returncode = proc.wait()
            with app.config["base_prepare_lock"]:
                state["returncode"] = returncode
            if returncode == 0:
                emit_base_log("Base preparation finished.")
            else:
                emit_base_log(f"Base preparation exited with code {returncode}.")
        except Exception as exc:
            with app.config["base_prepare_lock"]:
                state["returncode"] = -1
            emit_base_log(f"Base preparation failed: {exc}")
        finally:
            with app.config["base_prepare_lock"]:
                state["running"] = False
                state["finished_at"] = monotonic()
                if state["started_at"] is not None:
                    state["duration_seconds"] = round(state["finished_at"] - state["started_at"], 1)
            import asyncio

            status = asyncio.run(toady_base.base_status())
            if state["returncode"] not in (None, 0) and not status.get("prepared"):
                status["state"] = "error"
                status["error"] = status.get("error") or f"prepare exited with code {state['returncode']}"
            _emit_base_status(_overlay_base_prepare_status(status))

    # --- Login / logout -------------------------------------------------

    @app.route("/login", methods=["GET"])
    def login():
        # If auth is disabled, or the caller already has a session, send
        # them straight to the app.
        if not auth.auth_enabled() or session.get("authenticated"):
            return redirect(url_for("index"))
        # Seed a CSRF token into the session so the static page can fetch it.
        auth.generate_csrf_token()
        return app.send_static_file("login.html")

    @app.route("/login/csrf", methods=["GET"])
    def login_csrf():
        """Return a fresh CSRF token for the login form.

        Kept separate so ``login.html`` can stay static (no server-side
        templating) and fetch its token over XHR.
        """
        if not auth.auth_enabled():
            return jsonify({"csrf_token": "", "auth_enabled": False})
        token = auth.generate_csrf_token()
        return jsonify({"csrf_token": token, "auth_enabled": True})

    @app.route("/login", methods=["POST"])
    def login_submit():
        if not auth.auth_enabled():
            return redirect(url_for("index"))

        submitted_token = request.form.get("csrf_token", "")
        if not auth.validate_csrf_token(submitted_token):
            return redirect(url_for("login") + "?error=csrf")

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if _login_is_throttled(username):
            return redirect(url_for("login") + "?error=throttle")
        auth.load_credentials(manager.global_dir)
        expected_hash = auth.get_password_hash(username)

        # If the username does not exist expected_hash will be None.
        password_ok = auth.check_password(password, expected_hash)
        if not username or not password_ok:
            error = "throttle" if _record_login_failure(username) else "1"
            return redirect(url_for("login") + f"?error={error}")

        session.clear()  # prevent session fixation
        session.permanent = True
        session["authenticated"] = True
        session["username"] = username
        # Re-seed the CSRF token after login.
        auth.generate_csrf_token()
        _clear_login_failures(username)

        next_url = request.form.get("next") or request.args.get("next") or ""
        if _is_safe_next(next_url):
            return redirect(next_url)
        return redirect(url_for("index"))

    @app.route("/logout", methods=["GET"])
    def logout_get():
        abort(405)

    @app.route("/logout", methods=["POST"])
    def logout():
        if auth.auth_enabled():
            submitted_token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
            if not auth.validate_csrf_token(submitted_token):
                abort(403)
        session.clear()
        if auth.auth_enabled():
            return redirect(url_for("login"))
        return redirect(url_for("index"))

    @socketio.on("connect")
    def on_connect(_auth_data=None):
        # Reject unauthenticated Socket.IO upgrades. Flask-SocketIO makes
        # the HTTP session available here because the cookie is sent with
        # the WebSocket handshake; returning False refuses the connection.
        if auth.auth_enabled() and not session.get("authenticated"):
            return False
        join_room("authenticated")

    @socketio.on("disconnect")
    def on_disconnect():
        with app.config["toady_terminals_lock"]:
            for session_info in app.config["toady_terminals"].values():
                session_info.get("clients", set()).discard(request.sid)

    @socketio.on("sandbox:list")
    def toady_socket_list_sandboxes(_payload=None):
        import asyncio

        return {"ok": True, "sandboxes": asyncio.run(_sandbox_service().reconcile())}

    @socketio.on("base:status")
    def toady_socket_base_status(_payload=None):
        import asyncio

        status = asyncio.run(toady_base.base_status())
        _auto_start_base_prepare_if_needed(status)
        return {"ok": True, "base": _overlay_base_prepare_status(status)}

    @socketio.on("base:logs")
    def toady_socket_base_logs(_payload=None):
        return {"ok": True, "prepare": _base_prepare_payload(include_logs=True)}

    @socketio.on("base:prepare")
    def toady_socket_base_prepare(payload=None):
        state = app.config["base_prepare"]
        if state.get("running"):
            _log_socket_event("base:prepare", ok=True, started=False)
            return {"ok": True, "started": False, "message": "Base preparation is already running."}
        rebuild = bool((payload or {}).get("rebuild"))
        started = _start_base_prepare(rebuild, automatic=False)
        return {"ok": True, "started": started}

    @socketio.on("sandbox:create")
    def toady_socket_create_sandbox(payload):
        try:
            manifest = _sandbox_service().create_manifest(payload or {})
        except (SandboxServiceError, ValidationError) as exc:
            _log_socket_event("sandbox:create", ok=False, error=exc)
            return _sandbox_error_payload(exc)
        _log_socket_event("sandbox:create", sandbox_id=manifest["slug"], ok=True, status=manifest["last_status"])
        _emit_sandboxes_updated()
        socketio.emit("sandbox:status", {"id": manifest["slug"], "status": manifest["last_status"]}, to="authenticated")
        return {"ok": True, "sandbox": manifest}

    @socketio.on("sandbox:get")
    def toady_socket_get_sandbox(payload):
        try:
            manifest = _sandbox_service().get((payload or {}).get("slug") or (payload or {}).get("id") or "")
        except ValidationError as exc:
            return _sandbox_error_payload(exc)
        if manifest is None:
            return {"ok": False, "error": "Unknown sandbox"}
        return {"ok": True, "sandbox": manifest}

    @socketio.on("sandbox:logs")
    def toady_socket_sandbox_logs(payload):
        slug = str((payload or {}).get("slug") or (payload or {}).get("id") or "")
        try:
            logs = _sandbox_service().read_logs(slug)
            return {"ok": True, "sandbox_id": slug, "logs": logs}
        except (SandboxServiceError, ValidationError) as exc:
            socketio.emit("sandbox:error", {"sandbox_id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)

    @socketio.on("workspace:browse")
    def toady_socket_browse_workspace(payload):
        try:
            browse = _sandbox_service().browse_workspaces((payload or {}).get("path"))
        except (SandboxServiceError, ValidationError) as exc:
            return _sandbox_error_payload(exc)
        return {"ok": True, "browse": browse}

    @socketio.on("sandbox:start")
    def toady_socket_start_sandbox(payload):
        import asyncio

        slug = (payload or {}).get("slug") or (payload or {}).get("id") or ""
        socketio.emit("sandbox:status", {"id": slug, "status": "starting"}, to="authenticated")
        try:
            manifest = asyncio.run(_sandbox_service().start(slug))
        except (SandboxServiceError, ValidationError, RuntimeError) as exc:
            _log_socket_event("sandbox:start", sandbox_id=slug, ok=False, error=exc)
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
        _log_socket_event("sandbox:start", sandbox_id=manifest["slug"], ok=True, status=manifest["last_status"])
        _close_toady_sandbox_terminals(manifest["slug"])
        _emit_sandboxes_updated()
        socketio.emit("sandbox:status", {"id": manifest["slug"], "status": manifest["last_status"]}, to="authenticated")
        return {"ok": True, "sandbox": manifest}

    @socketio.on("sandbox:stop")
    def toady_socket_stop_sandbox(payload):
        import asyncio

        slug = (payload or {}).get("slug") or (payload or {}).get("id") or ""
        try:
            manifest = asyncio.run(_sandbox_service().stop(slug))
        except (SandboxServiceError, ValidationError, RuntimeError) as exc:
            _log_socket_event("sandbox:stop", sandbox_id=slug, ok=False, error=exc)
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
        _log_socket_event("sandbox:stop", sandbox_id=manifest["slug"], ok=True, status=manifest["last_status"])
        _emit_sandboxes_updated()
        socketio.emit("sandbox:status", {"id": manifest["slug"], "status": manifest["last_status"]}, to="authenticated")
        return {"ok": True, "sandbox": manifest}

    @socketio.on("sandbox:refresh-runtime")
    def toady_socket_refresh_sandbox_runtime(payload):
        import asyncio

        slug = (payload or {}).get("slug") or (payload or {}).get("id") or ""
        if app.config["base_prepare"].get("running"):
            exc = SandboxServiceError("Sandbox runtime setup is already running. Try refresh again when it finishes.")
            return _sandbox_error_payload(exc)
        _close_toady_sandbox_terminals(slug)
        socketio.emit("sandbox:status", {"id": slug, "status": "refreshing"}, to="authenticated")
        try:
            result = asyncio.run(_sandbox_service().refresh_runtime_dependencies(slug))
        except (SandboxServiceError, ValidationError, RuntimeError) as exc:
            _log_socket_event("sandbox:refresh-runtime", sandbox_id=slug, ok=False, error=exc)
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
        manifest = result["sandbox"]
        _log_socket_event(
            "sandbox:refresh-runtime",
            sandbox_id=manifest["slug"],
            ok=True,
            status=manifest["last_status"],
            restarted=bool(result.get("restarted")),
            rebuilt_base=bool(result.get("rebuilt_base")),
            updated=bool(result.get("updated")),
        )
        _emit_sandboxes_updated()
        socketio.emit("sandbox:status", {"id": manifest["slug"], "status": manifest["last_status"]}, to="authenticated")
        return {"ok": True, **result}

    @socketio.on("sandbox:destroy")
    def toady_socket_destroy_sandbox(payload):
        import asyncio

        payload = payload or {}
        slug = payload.get("slug") or payload.get("id") or ""
        try:
            deleted = asyncio.run(_sandbox_service().destroy(slug, purge_home=bool(payload.get("purge"))))
        except (SandboxServiceError, ValidationError, RuntimeError) as exc:
            _log_socket_event("sandbox:destroy", sandbox_id=slug, ok=False, error=exc)
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
        if not deleted:
            _log_socket_event("sandbox:destroy", sandbox_id=slug, ok=False, error="Unknown sandbox")
            return {"ok": False, "error": "Unknown sandbox"}
        _log_socket_event("sandbox:destroy", sandbox_id=slug, ok=True, purge=bool(payload.get("purge")))
        _close_toady_sandbox_terminals(slug)
        _emit_sandboxes_updated()
        socketio.emit("sandbox:destroyed", {"id": slug}, to="authenticated")
        return {"ok": True}

    @socketio.on("sandbox:terminal:open")
    def toady_socket_open_terminal(payload):
        payload = payload or {}
        slug = payload.get("sandbox_id") or payload.get("slug") or payload.get("id") or ""
        try:
            slug = validate_slug(str(slug))
            manifest = _sandbox_service().store.get(slug)
            if manifest is None:
                raise SandboxServiceError("Unknown sandbox")
            if manifest.last_status != "running":
                raise SandboxServiceError("Start the sandbox before opening a terminal.")
            terminal_limit_value = int(app.config.get("toady_terminal_limit") or 32)
            if _toady_sandbox_terminal_count(manifest.slug) >= terminal_limit_value:
                raise SandboxServiceError(
                    f"Terminal limit reached for {manifest.slug} ({terminal_limit_value}). "
                    "Close a terminal before opening another."
                )
            cols = max(20, min(300, int(payload.get("cols") or 100)))
            rows = max(5, min(100, int(payload.get("rows") or 30)))
            terminal_id = f"{manifest.slug}-{uuid.uuid4().hex[:12]}"
            driver = _toady_terminal_driver(manifest)
            opened = driver.open(terminal_id, cwd="/workspace", shell="/bin/bash", cols=cols, rows=rows)
            with app.config["toady_terminals_lock"]:
                terminal_number = int(app.config["toady_terminal_numbers"].get(manifest.slug, 0)) + 1
                app.config["toady_terminal_numbers"][manifest.slug] = terminal_number
                app.config["toady_terminals"][terminal_id] = {
                    "id": terminal_id,
                    "sandbox_id": manifest.slug,
                    "number": terminal_number,
                    "driver": driver,
                    "clients": {request.sid},
                    "seq": 0,
                    "alive": True,
                    "cwd": opened.get("cwd") or "/workspace",
                    "pid": opened.get("pid"),
                    "status": "running",
                    "exit_code": None,
                    "replay": bytearray(),
                    "replay_truncated": False,
                }
            join_room(_toady_terminal_room(terminal_id))
            socketio.start_background_task(_toady_terminal_poll, terminal_id)
            session_info = _toady_terminal_session(terminal_id)
            _log_socket_event("sandbox:terminal:open", sandbox_id=manifest.slug, terminal_id=terminal_id, ok=True)
            return {
                "ok": True,
                "terminal": _toady_terminal_payload(session_info),
            }
        except (SandboxServiceError, ValidationError, PtyDriverError, ValueError) as exc:
            _log_socket_event("sandbox:terminal:open", sandbox_id=slug, ok=False, error=exc)
            socketio.emit("sandbox:terminal:error", {"sandbox_id": slug, "error": str(exc)}, to=request.sid)
            return _toady_terminal_error_payload(exc)

    @socketio.on("sandbox:terminal:list")
    def toady_socket_list_terminals(payload):
        payload = payload or {}
        slug = payload.get("sandbox_id") or payload.get("slug") or payload.get("id") or ""
        try:
            slug = validate_slug(str(slug))
            with app.config["toady_terminals_lock"]:
                terminal_payloads = [
                    _toady_terminal_payload(session_info)
                    for session_info in app.config["toady_terminals"].values()
                    if session_info.get("sandbox_id") == slug
                ]
            return {"ok": True, "terminals": terminal_payloads}
        except (SandboxServiceError, ValidationError) as exc:
            return _toady_terminal_error_payload(exc)

    @socketio.on("sandbox:terminal:join")
    def toady_socket_join_terminal(payload):
        payload = payload or {}
        terminal_id = str(payload.get("terminal_id") or "")
        try:
            with app.config["toady_terminals_lock"]:
                session_info = app.config["toady_terminals"].get(terminal_id)
                if not session_info:
                    raise SandboxServiceError("Unknown terminal session.")
                session_info.setdefault("clients", set()).add(request.sid)
                terminal_payload = _toady_terminal_payload(session_info)
                replay_payload = _toady_terminal_replay(session_info)
            join_room(_toady_terminal_room(terminal_id))
            return {"ok": True, "terminal": terminal_payload, "replay": replay_payload}
        except (SandboxServiceError, ValidationError) as exc:
            socketio.emit("sandbox:terminal:error", {"terminal_id": terminal_id, "error": str(exc)}, to=request.sid)
            return _toady_terminal_error_payload(exc)

    @socketio.on("sandbox:terminal:status")
    def toady_socket_terminal_status(payload):
        payload = payload or {}
        terminal_id = str(payload.get("terminal_id") or "")
        try:
            session_info = _toady_terminal_session(terminal_id)
            status = session_info["driver"].status(terminal_id)
            session_info["status"] = status.get("status") or session_info.get("status") or "running"
            session_info["exit_code"] = status.get("exit_code")
            return {"ok": True, "status": status}
        except (SandboxServiceError, PtyDriverError) as exc:
            socketio.emit("sandbox:terminal:error", {"terminal_id": terminal_id, "error": str(exc)}, to=request.sid)
            return _toady_terminal_error_payload(exc)

    @socketio.on("sandbox:terminal:input")
    def toady_socket_terminal_input(payload):
        payload = payload or {}
        terminal_id = str(payload.get("terminal_id") or "")
        data = payload.get("data")
        if not isinstance(data, str):
            return _toady_terminal_error_payload(SandboxServiceError("Terminal input must be text."))
        try:
            session_info = _toady_terminal_session(terminal_id)
            session_info["driver"].write(terminal_id, data)
            return {"ok": True}
        except (SandboxServiceError, PtyDriverError) as exc:
            socketio.emit("sandbox:terminal:error", {"terminal_id": terminal_id, "error": str(exc)}, to=request.sid)
            return _toady_terminal_error_payload(exc)

    @socketio.on("sandbox:terminal:resize")
    def toady_socket_terminal_resize(payload):
        payload = payload or {}
        terminal_id = str(payload.get("terminal_id") or "")
        try:
            cols = max(20, min(300, int(payload.get("cols") or 100)))
            rows = max(5, min(100, int(payload.get("rows") or 30)))
            session_info = _toady_terminal_session(terminal_id)
            session_info["driver"].resize(terminal_id, cols=cols, rows=rows)
            return {"ok": True}
        except (SandboxServiceError, PtyDriverError, ValueError) as exc:
            socketio.emit("sandbox:terminal:error", {"terminal_id": terminal_id, "error": str(exc)}, to=request.sid)
            return _toady_terminal_error_payload(exc)

    @socketio.on("sandbox:terminal:close")
    def toady_socket_terminal_close(payload):
        payload = payload or {}
        terminal_id = str(payload.get("terminal_id") or "")
        try:
            session_info = _toady_terminal_session(terminal_id)
            sandbox_id = session_info.get("sandbox_id")
            session_info["alive"] = False
            _close_toady_terminal(terminal_id)
            _log_socket_event("sandbox:terminal:close", sandbox_id=sandbox_id, terminal_id=terminal_id, ok=True)
            return {"ok": True}
        except (SandboxServiceError, PtyDriverError) as exc:
            _log_socket_event("sandbox:terminal:close", terminal_id=terminal_id, ok=False, error=exc)
            socketio.emit("sandbox:terminal:error", {"terminal_id": terminal_id, "error": str(exc)}, to=request.sid)
            return _toady_terminal_error_payload(exc)

    @socketio.on("port:list")
    def toady_socket_list_ports(payload):
        slug = (payload or {}).get("sandbox_id") or (payload or {}).get("id") or ""
        try:
            return {"ok": True, "ports": _sandbox_service().list_ports(slug)}
        except (SandboxServiceError, ValidationError) as exc:
            return _sandbox_error_payload(exc)

    @socketio.on("port:publish")
    def toady_socket_publish_port(payload):
        payload = payload or {}
        slug = payload.get("sandbox_id") or payload.get("id") or ""
        try:
            mapping = _sandbox_service().publish_port(slug, payload)
            ports = _sandbox_service().list_ports(slug)
        except (SandboxServiceError, ValidationError, RuntimeError) as exc:
            _log_socket_event("port:publish", sandbox_id=slug, ok=False, error=exc)
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
        _log_socket_event(
            "port:publish",
            sandbox_id=slug,
            ok=True,
            guest_port=mapping.get("guest_port"),
            host_port=mapping.get("host_port"),
        )
        _emit_sandboxes_updated()
        socketio.emit("ports:updated", {"sandbox_id": slug, "ports": ports}, to="authenticated")
        return {"ok": True, "port": mapping, "ports": ports}

    @socketio.on("port:unpublish")
    def toady_socket_unpublish_port(payload):
        payload = payload or {}
        slug = payload.get("sandbox_id") or payload.get("id") or ""
        try:
            mapping = _sandbox_service().unpublish_port(slug, payload)
            ports = _sandbox_service().list_ports(slug)
        except (SandboxServiceError, ValidationError, RuntimeError) as exc:
            _log_socket_event("port:unpublish", sandbox_id=slug, ok=False, error=exc)
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
        _log_socket_event(
            "port:unpublish",
            sandbox_id=slug,
            ok=True,
            guest_port=mapping.get("guest_port"),
            host_port=mapping.get("host_port"),
        )
        _emit_sandboxes_updated()
        socketio.emit("ports:updated", {"sandbox_id": slug, "ports": ports}, to="authenticated")
        return {"ok": True, "port": mapping, "ports": ports}

    @socketio.on("port:reassign")
    def toady_socket_reassign_port(payload):
        payload = payload or {}
        slug = payload.get("sandbox_id") or payload.get("id") or ""
        try:
            mapping = _sandbox_service().reassign_port(slug, payload)
            ports = _sandbox_service().list_ports(slug)
        except (SandboxServiceError, ValidationError, RuntimeError) as exc:
            _log_socket_event("port:reassign", sandbox_id=slug, ok=False, error=exc)
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
        _log_socket_event(
            "port:reassign",
            sandbox_id=slug,
            ok=True,
            guest_port=mapping.get("guest_port"),
            host_port=mapping.get("host_port"),
        )
        _emit_sandboxes_updated()
        socketio.emit("ports:updated", {"sandbox_id": slug, "ports": ports}, to="authenticated")
        return {"ok": True, "port": mapping, "ports": ports}

    return app


def _is_safe_next(next_url):
    """Return True if ``next_url`` is a safe in-app redirect target.

    Accepts only paths that start with a single ``/`` and have no URL
    scheme. Rejects ``//evil.com`` (protocol-relative) and ``https://x``.
    """
    if not next_url or not isinstance(next_url, str):
        return False
    if not next_url.startswith("/"):
        return False
    if next_url.startswith("//"):
        return False
    if "://" in next_url:
        return False
    return True
