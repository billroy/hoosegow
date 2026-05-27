"""Flask + socket.io app factory."""

import os
import re
import subprocess
import sys
import json
import base64
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
import shutil
import atexit
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
    send_file,
    session,
    url_for,
)
from flask_socketio import SocketIO, join_room

from server import auth
from server import base as toady_base
from server.persistence import read_json, write_json, read_frontmatter, ensure_within, atomic_write
from server.pty_driver import PtyDriver, PtyDriverError
from server.sandboxes import SandboxService, SandboxServiceError, browse_roots_from_env
from server.toady_validation import ValidationError, validate_slug


socketio = SocketIO()
_service_worker_atexit_registered = False

# Set of socket.io sids that authenticated via mcp_token (agent/MCP clients).
# Used by event handlers to distinguish agent-originated updates from user
# updates so the agent can't accidentally yank its own running task.
mcp_sids = set()
mcp_sid_workspace = {}

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_MAX_IMPORT_ARCHIVE_BYTES = 200 * 1024 * 1024
_MAX_IMPORT_ARCHIVE_FILES = 1000
_MAX_IMPORT_COMPRESSION_RATIO = 100
_NESTED_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tgz",
    ".tar.gz",
    ".tbz",
    ".tbz2",
    ".tar.bz2",
    ".txz",
    ".tar.xz",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
)
_LOGIN_THROTTLE_WINDOW_SECONDS = 5 * 60
_LOGIN_THROTTLE_MAX_FAILURES = 5
_LOGIN_THROTTLE_BLOCK_SECONDS = 60
_DEFAULT_SESSION_DAYS = 30
_MAX_SESSION_DAYS = 365
_TERMINAL_REPLAY_LIMIT_BYTES = 5 * 1024 * 1024
_TERMINAL_REPLAY_LIMIT_LINES = 10_000
_TERMINAL_REPLAY_TRUNCATED_MARKER = b"\r\n[Toady replay truncated]\r\n"
_TEXTUAL_APPLICATION_MIME_PREFIXES = (
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
    "application/ecmascript",
    "application/x-sh",
    "application/x-shellscript",
)


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


def _is_textual_mime(mime):
    if not mime:
        return True
    if mime.startswith("text/"):
        return True
    return any(
        mime == prefix or mime.startswith(prefix + ";")
        for prefix in _TEXTUAL_APPLICATION_MIME_PREFIXES
    )


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _session_lifetime():
    days = _safe_int(os.environ.get("TOADY_SESSION_DAYS", os.environ.get("BULLPEN_SESSION_DAYS", "")), _DEFAULT_SESSION_DAYS)
    days = max(1, min(days, _MAX_SESSION_DAYS))
    return timedelta(days=days)


def _current_deploy_label():
    label = (os.environ.get("TOADY_DEPLOY_LABEL") or os.environ.get("BULLPEN_DEPLOY_LABEL") or "").strip()
    if not label:
        return None
    # Keep this runtime-only display string compact and single-line.
    label = re.sub(r"\s+", " ", label)
    return label[:80]


def sync_deploy_label_config(bp_dir):
    path = os.path.join(bp_dir, "config.json")
    config = read_json(path)
    label = _current_deploy_label()
    if label:
        if config.get("deploy_label") == label:
            return
        config["deploy_label"] = label
    else:
        if "deploy_label" not in config:
            return
        config.pop("deploy_label", None)
    write_json(path, config)


def _slot_coord(slot, index, cols=4):
    if isinstance(slot, dict):
        return _safe_int(slot.get("col"), index % cols), _safe_int(slot.get("row"), index // cols)
    return index % cols, index // cols


def _translated_worker_slot(slot, delta_col, delta_row):
    translated = dict(slot or {})
    translated["col"] = _safe_int(translated.get("col"), 0) + delta_col
    translated["row"] = _safe_int(translated.get("row"), 0) + delta_row
    return translated


def _merge_imported_worker_slots(existing_slots, imported_slots, *, config):
    from server.worker_types import normalize_layout

    cols = max(_safe_int(((config or {}).get("grid") or {}).get("cols"), 4), 1)
    kept_existing = list(existing_slots or [])
    normalized_import = normalize_layout({"slots": imported_slots or []}, config=config).get("slots", [])
    imported_workers = [slot for slot in normalized_import if isinstance(slot, dict)]
    if not imported_workers:
        return kept_existing

    occupied = {
        _slot_coord(slot, index, cols)
        for index, slot in enumerate(kept_existing)
        if isinstance(slot, dict)
    }
    imported_coords = [_slot_coord(slot, index, cols) for index, slot in enumerate(imported_workers)]
    imported_min_col = min(col for col, _row in imported_coords)
    imported_min_row = min(row for _col, row in imported_coords)

    if occupied:
        existing_cols = [col for col, _row in occupied]
        existing_rows = [row for _col, row in occupied]
        candidate_offsets = [
            (max(existing_cols) + 1 - imported_min_col, min(existing_rows) - imported_min_row),
            (min(existing_cols) - imported_min_col, max(existing_rows) + 1 - imported_min_row),
        ]
    else:
        candidate_offsets = [(-imported_min_col, -imported_min_row)]

    for delta_col, delta_row in candidate_offsets:
        translated_coords = {
            (col + delta_col, row + delta_row)
            for col, row in imported_coords
        }
        if occupied.isdisjoint(translated_coords):
            break
    else:
        delta_col = (max((col for col, _row in occupied), default=-1) + 1) - imported_min_col
        delta_row = -imported_min_row

    kept_existing.extend(
        _translated_worker_slot(slot, delta_col, delta_row)
        for slot in imported_workers
    )
    return kept_existing


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
    startup_workspace_name = (os.environ.get("TOADY_WORKSPACE_NAME") or os.environ.get("BULLPEN_WORKSPACE_NAME") or "").strip() or None
    start_without_project = bool(start_without_project) or os.environ.get("TOADY_START_WITHOUT_PROJECT", os.environ.get("BULLPEN_START_WITHOUT_PROJECT", "")) == "1"

    # Initialize the legacy workspace manager only for the Bullpen workspace
    # surface. Toady mode uses sandbox manifests and workspace roots, so
    # persisted project registry entries must not activate or create `.bullpen`
    # directories on startup.
    if start_without_project:
        manager = ToadyStateManager(global_dir=global_dir)
    else:
        from server.workspace_manager import WorkspaceManager

        manager = WorkspaceManager(global_dir=global_dir)
    startup_id = None
    if not start_without_project:
        startup_id = manager.register_project(workspace, name=startup_workspace_name)
        # Activate all persisted projects so the UI can switch between them immediately.
        # The registry can contain projects from prior runs that need in-memory state.
        for entry in manager.list_projects():
            if entry["id"] == startup_id:
                continue
            try:
                manager.register_project(entry["path"], name=entry.get("name"))
            except ValueError:
                # Path is currently missing/unavailable (renamed, unmounted, etc.).
                # Keep the registry entry so it returns when the path comes back;
                # do not silently delete user data.
                continue
    bp_dir = manager.get_bp_dir(startup_id) if startup_id else None

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
    mcp_auth = None
    service_worker_mod = None
    if not start_without_project:
        from server import mcp_auth as legacy_mcp_auth
        from server import service_worker as legacy_service_worker_mod
        from server.terminal import TerminalManager

        mcp_auth = legacy_mcp_auth
        service_worker_mod = legacy_service_worker_mod
        app.config["terminal_manager"] = TerminalManager(socketio)
    else:
        app.config["terminal_manager"] = None
    app.config["toady_terminals"] = {}
    app.config["toady_terminals_lock"] = threading.RLock()
    app.config["toady_terminal_limit"] = max(
        1,
        _safe_int(terminal_limit or os.environ.get("TOADY_TERMINAL_LIMIT", ""), 32),
    )

    def _portable_config(config):
        safe = dict(config or {})
        for key in ("server_host", "server_port", "mcp_token", "deploy_label"):
            safe.pop(key, None)
        return safe

    def _write_runtime_config(ws, preferred_token=None):
        if mcp_auth is None:
            return
        token = mcp_auth.ensure_workspace_runtime_config(
            ws.bp_dir,
            host=app.config.get("host", "127.0.0.1"),
            port=app.config.get("port", 5000),
            disallowed_tokens=mcp_auth.workspace_token_set(manager.all_workspaces(), exclude_bp_dir=ws.bp_dir),
            preferred_token=preferred_token,
        )
        app.config.setdefault("mcp_tokens_by_workspace", {})
        app.config["mcp_tokens_by_workspace"][ws.id] = token

    app.config["host"] = host
    app.config["port"] = port
    app.config["base_prepare"] = {
        "running": False,
        "returncode": None,
        "logs": [],
        "started_at": None,
        "finished_at": None,
        "duration_seconds": None,
    }
    global _service_worker_atexit_registered
    if not start_without_project and not _service_worker_atexit_registered:
        atexit.register(service_worker_mod.stop_all_services)
        _service_worker_atexit_registered = True
    if mcp_auth is not None:
        app.config["mcp_tokens_by_workspace"] = mcp_auth.initialize_workspace_runtime_configs(
            manager.all_workspaces(),
            host,
            port,
        )
        for ws in manager.all_workspaces():
            sync_deploy_label_config(ws.bp_dir)

        # Startup reconciliation for all registered workspaces
        for ws in manager.all_workspaces():
            reconcile(ws.bp_dir)
    else:
        app.config["mcp_tokens_by_workspace"] = {}

    # --- Public (unauthenticated) assets allowlist ---------------------
    # These paths must load without a session so the login page can be
    # rendered and styled before the user authenticates.
    PUBLIC_STATIC_FILES = {"login.html", "style.css", "favicon.ico"}
    LEGACY_PRODUCT_STATIC_FILES = {
        "audio.js",
        "commands.js",
        "event-sounds.js",
        "gridGeometry.js",
        "shell_worker_examples.json",
        "utils.js",
    }
    LEGACY_PRODUCT_STATIC_PREFIXES = ("components/",)
    LEGACY_PRODUCT_API_PREFIXES = (
        "/api/commits",
        "/api/files",
        "/api/worker",
        "/api/export",
        "/api/import",
        "/api/service",
    )

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
    def _gate_legacy_product_surface():
        if not app.config.get("start_without_project"):
            return None
        if request.endpoint == "static":
            filename = (request.view_args or {}).get("filename", "")
            if filename in LEGACY_PRODUCT_STATIC_FILES or filename.startswith(LEGACY_PRODUCT_STATIC_PREFIXES):
                abort(404)
            if filename.startswith("manager/"):
                abort(404)
        if request.path.startswith(LEGACY_PRODUCT_API_PREFIXES):
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
        state = app.config.get("base_prepare") or {}
        payload = {
            "running": bool(state.get("running")),
            "returncode": state.get("returncode"),
            "duration_seconds": state.get("duration_seconds"),
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

    def _base_prepare_worker(rebuild=False):
        state = app.config["base_prepare"]
        state["running"] = True
        state["returncode"] = None
        state["logs"] = []
        state["started_at"] = monotonic()
        state["finished_at"] = None
        state["duration_seconds"] = None

        def emit_base_log(line):
            line = str(line or "")
            if not line:
                return
            state.setdefault("logs", []).append(line)
            if len(state["logs"]) > 500:
                del state["logs"][: len(state["logs"]) - 500]
            socketio.emit("base:log", {"line": line}, to="authenticated")

        _emit_base_status({
            "name": "toady-microsandbox-local",
            "prepared": False,
            "state": "preparing",
            "message": "Preparing Microsandbox base...",
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
            state["returncode"] = proc.wait()
            if state["returncode"] == 0:
                emit_base_log("Base preparation finished.")
            else:
                emit_base_log(f"Base preparation exited with code {state['returncode']}.")
        except Exception as exc:
            state["returncode"] = -1
            emit_base_log(f"Base preparation failed: {exc}")
        finally:
            state["running"] = False
            state["finished_at"] = monotonic()
            if state["started_at"] is not None:
                state["duration_seconds"] = round(state["finished_at"] - state["started_at"], 1)
            import asyncio

            status = asyncio.run(toady_base.base_status())
            if state["returncode"] not in (None, 0) and not status.get("prepared"):
                status["state"] = "error"
                status["error"] = status.get("error") or f"prepare exited with code {state['returncode']}"
            status["prepare"] = _base_prepare_payload(include_logs=False)
            _emit_base_status(status)

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

    def _workspace_id_from_args():
        return request.args.get("workspaceId") or startup_id

    def _workspace_id_from_payload(payload):
        return (payload or {}).get("workspaceId") or startup_id

    def _workspace_required_response():
        return jsonify({"error": "No active workspace. Add or select a project first."}), 400

    def _workspace_from_id(ws_id, *, activate=False):
        if not ws_id:
            return None, _workspace_required_response()
        ws = manager.get_or_activate(ws_id) if activate else manager.get(ws_id)
        if ws is None:
            return None, (jsonify({"error": "Unknown workspace"}), 404)
        return ws, None

    def legacy_route(*args, **kwargs):
        if not start_without_project:
            return app.route(*args, **kwargs)

        def decorator(func):
            return func

        return decorator

    @legacy_route("/api/commits")
    @auth.require_auth
    def get_commits():
        """Return git log entries for the active workspace."""
        ws, error = _workspace_from_id(_workspace_id_from_args())
        if error:
            return error
        ws_path = ws.path
        try:
            count = min(max(int(request.args.get("count", 10)), 1), 50)
        except (ValueError, TypeError):
            count = 10
        try:
            offset = max(int(request.args.get("offset", 0)), 0)
        except (ValueError, TypeError):
            offset = 0

        # Field separator (\x1f = ASCII unit separator) and record separator (\x1e = record separator)
        fmt = "%H\x1f%h\x1f%s\x1f%an\x1f%ai\x1f%b\x1e"
        try:
            result = subprocess.run(
                ["git", "log", f"-n{count}", f"--skip={offset}", f"--format={fmt}"],
                capture_output=True, text=True, cwd=ws_path, timeout=10,
            )
        except Exception as e:
            return jsonify({"commits": [], "has_more": False, "error": str(e)}), 500

        if result.returncode != 0:
            return jsonify({"commits": [], "has_more": False, "error": "Not a git repository"})

        commits = []
        for record in result.stdout.split("\x1e"):
            record = record.strip()
            if not record:
                continue
            parts = record.split("\x1f", 5)
            if len(parts) < 5:
                continue
            commits.append({
                "hash": parts[0].strip(),
                "short_hash": parts[1].strip(),
                "subject": parts[2].strip(),
                "author": parts[3].strip(),
                "date": parts[4].strip(),
                "body": parts[5].strip() if len(parts) > 5 else "",
            })

        # Check if more commits exist beyond this page
        try:
            count_result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                capture_output=True, text=True, cwd=ws_path, timeout=5,
            )
            total = int(count_result.stdout.strip()) if count_result.returncode == 0 else 0
        except Exception:
            total = 0
        has_more = (offset + len(commits)) < total

        return jsonify({"commits": commits, "has_more": has_more, "total": total})

    @legacy_route("/api/commits/<commit_hash>/diff")
    @auth.require_auth
    def get_commit_diff(commit_hash):
        """Return the patch for a specific commit in the active workspace."""
        ws, error = _workspace_from_id(_workspace_id_from_args())
        if error:
            return error
        ws_path = ws.path
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit_hash or ""):
            return jsonify({"error": "Invalid commit hash"}), 400
        try:
            result = subprocess.run(
                ["git", "show", "--format=", "--patch", "--no-color", commit_hash],
                capture_output=True, text=True, cwd=ws_path, timeout=10,
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

        if result.returncode != 0:
            return jsonify({"error": "Commit not found"}), 404
        return jsonify({"hash": commit_hash, "diff": result.stdout})

    @legacy_route("/api/files")
    @auth.require_auth
    def file_tree():
        """Return workspace file tree."""
        ws, error = _workspace_from_id(_workspace_id_from_args())
        if error:
            return error
        ws_path = ws.path
        tree = build_file_tree(ws_path)
        return jsonify(tree)

    @legacy_route("/api/files/<path:filepath>")
    @auth.require_auth
    def file_content(filepath):
        """Return file content."""
        ws, error = _workspace_from_id(_workspace_id_from_args())
        if error:
            return error
        ws_path = ws.path
        full_path = os.path.join(ws_path, filepath)
        try:
            ensure_within(full_path, ws_path)
        except ValueError:
            abort(403)

        if not os.path.isfile(full_path):
            abort(404)

        # Determine if binary
        import mimetypes
        mime, _ = mimetypes.guess_type(full_path)

        # Serve the raw file directly (e.g. open HTML in browser)
        if request.args.get("raw"):
            send_kwargs = {"mimetype": mime or "text/plain"}
            if mime in {"text/html", "application/xhtml+xml"}:
                send_kwargs["as_attachment"] = True
                send_kwargs["download_name"] = os.path.basename(full_path)
            return send_file(full_path, **send_kwargs)

        if mime and (mime.startswith("image/") or not _is_textual_mime(mime)):
            return send_file(full_path, mimetype=mime)

        try:
            with open(full_path, "r", errors="replace") as f:
                content = f.read()
            return jsonify({"path": filepath, "content": content, "mime": mime or "text/plain"})
        except Exception:
            abort(500)

    @legacy_route("/api/files/<path:filepath>", methods=["PUT"])
    @auth.require_auth
    def file_write(filepath):
        """Write file content."""
        ws, error = _workspace_from_id(_workspace_id_from_args())
        if error:
            return error
        ws_path = ws.path
        full_path = os.path.join(ws_path, filepath)
        try:
            ensure_within(full_path, ws_path)
        except ValueError:
            abort(403)

        content = request.get_data(as_text=True)
        if len(content) > 1_000_000:
            return jsonify({"error": "File too large (max 1MB)"}), 400
        if request.args.get("create") == "1" and os.path.exists(full_path):
            return jsonify({"error": "File already exists"}), 409

        # Reject binary content
        try:
            content.encode("utf-8")
        except UnicodeEncodeError:
            return jsonify({"error": "Binary files cannot be edited"}), 400

        try:
            atomic_write(full_path, content)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @legacy_route("/api/worker/transfer", methods=["POST"])
    @auth.require_auth
    def worker_transfer():
        """Copy or move a worker between workspaces."""
        from server.transfer import TransferError, transfer_worker
        from server.worker_types import ViewerContext, serialize_layout

        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"error": "invalid JSON body"}), 400

        try:
            result = transfer_worker(
                manager,
                source_workspace_id=data.get("source_workspace_id"),
                source_slot=data.get("source_slot"),
                dest_workspace_id=data.get("dest_workspace_id"),
                dest_slot=data.get("dest_slot"),
                mode=data.get("mode", "copy"),
                copy_profile=bool(data.get("copy_profile", False)),
            )
        except TransferError as e:
            return jsonify({"error": str(e)}), e.status

        # Notify destination workspace clients
        dst_ws = manager.get(data.get("dest_workspace_id"))
        if dst_ws:
            dst_layout = read_json(os.path.join(dst_ws.bp_dir, "layout.json"))
            dst_config = read_json(os.path.join(dst_ws.bp_dir, "config.json"))
            dst_layout = serialize_layout(dst_layout, viewer=ViewerContext(can_edit=True), config=dst_config)
            dst_layout["workspaceId"] = dst_ws.id
            socketio.emit("layout:updated", dst_layout, to=dst_ws.id)

        # On move, also notify source workspace clients
        if data.get("mode") == "move":
            src_ws = manager.get(data.get("source_workspace_id"))
            if src_ws:
                src_layout = read_json(os.path.join(src_ws.bp_dir, "layout.json"))
                src_config = read_json(os.path.join(src_ws.bp_dir, "config.json"))
                src_layout = serialize_layout(src_layout, viewer=ViewerContext(can_edit=True), config=src_config)
                src_layout["workspaceId"] = src_ws.id
                socketio.emit("layout:updated", src_layout, to=src_ws.id)

        return jsonify(result)

    def _export_workspace_zip_bytes(ws):
        mem = BytesIO()
        with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if os.path.isdir(ws.bp_dir):
                for root, _dirs, files in os.walk(ws.bp_dir):
                    for filename in files:
                        full_path = os.path.join(root, filename)
                        rel_path = os.path.relpath(full_path, ws.path).replace(os.sep, "/")
                        if rel_path == ".bullpen/config.json":
                            config = _portable_config(read_json(full_path))
                            zf.writestr(rel_path, json.dumps(config, indent=2))
                            continue
                        zf.write(full_path, rel_path)
        mem.seek(0)
        return mem

    def _workspace_export_meta(ws):
        # Do not expose host filesystem paths in export manifests.
        return {"id": ws.id, "name": ws.name}

    def _worker_export_name(value, fallback="worker"):
        text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip(" .-_")
        return text[:80] or fallback

    def _normalized_worker_slots(ws):
        from server.worker_types import normalize_layout

        layout_path = os.path.join(ws.bp_dir, "layout.json")
        layout = read_json(layout_path) if os.path.exists(layout_path) else {"slots": []}
        config = read_json(os.path.join(ws.bp_dir, "config.json"))
        layout = normalize_layout(layout, config=config)
        slots = layout.get("slots", []) if isinstance(layout, dict) else []
        return slots if isinstance(slots, list) else []

    def _export_workers_zip_bytes(ws, selected_slots=None, selected_slot=None):
        mem = BytesIO()
        created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        slots = _normalized_worker_slots(ws)
        if selected_slots is None:
            export_slots = slots
        else:
            export_slots = selected_slots
        workers_layout = {"slots": export_slots if isinstance(export_slots, list) else []}

        with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(".bullpen/layout.json", json.dumps(workers_layout, indent=2))

            profile_ids = set()
            for slot in workers_layout["slots"]:
                if isinstance(slot, dict) and isinstance(slot.get("profile"), str) and slot.get("profile").strip():
                    profile_ids.add(slot["profile"].strip())
            for profile_id in sorted(profile_ids):
                profile_path = os.path.join(ws.bp_dir, "profiles", f"{profile_id}.json")
                if os.path.exists(profile_path):
                    zf.write(profile_path, f".bullpen/profiles/{profile_id}.json")

            manifest = {
                "schema": "bullpen-workers-export-v1",
                "created_at": created_at,
                "workspace": _workspace_export_meta(ws),
                "profiles": sorted(profile_ids),
            }
            if selected_slot is not None:
                manifest["selection"] = {
                    "slot": int(selected_slot),
                    "count": len(workers_layout["slots"]),
                }
            zf.writestr("bullpen-workers-export.json", json.dumps(manifest, indent=2))
        mem.seek(0)
        return mem

    def _export_all_zip_bytes():
        mem = BytesIO()
        created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for ws in manager.all_workspaces():
                if not os.path.isdir(ws.bp_dir):
                    continue
                for root, _dirs, files in os.walk(ws.bp_dir):
                    for filename in files:
                        full_path = os.path.join(root, filename)
                        rel_path = os.path.relpath(full_path, ws.bp_dir).replace(os.sep, "/")
                        arcname = f"workspaces/{ws.id}/.bullpen/{rel_path}"
                        if rel_path == "config.json":
                            config = _portable_config(read_json(full_path))
                            zf.writestr(arcname, json.dumps(config, indent=2))
                            continue
                        zf.write(full_path, arcname)
            manifest = {
                "schema": "bullpen-export-all-v1",
                "created_at": created_at,
                "workspaces": [_workspace_export_meta(ws) for ws in manager.all_workspaces()],
            }
            zf.writestr("bullpen-export.json", json.dumps(manifest, indent=2))
        mem.seek(0)
        return mem

    def _safe_extract_zip(zf, target_dir):
        total_size = 0
        total_compressed_size = 0
        file_count = 0
        for info in zf.infolist():
            name = (info.filename or "").replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            file_count += 1
            if file_count > _MAX_IMPORT_ARCHIVE_FILES:
                raise ValueError("Archive contains too many files")
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if any(p == ".." for p in parts):
                raise ValueError("Archive contains invalid relative paths")
            if parts and parts[0].endswith(":"):
                raise ValueError("Archive contains invalid absolute paths")
            lower_name = "/".join(parts).lower()
            if any(lower_name.endswith(suffix) for suffix in _NESTED_ARCHIVE_SUFFIXES):
                raise ValueError("Archive contains nested archive files")
            compressed_size = max(0, int(info.compress_size or 0))
            total_compressed_size += max(1, compressed_size)
            total_size += max(0, int(info.file_size or 0))
            if total_size > _MAX_IMPORT_ARCHIVE_BYTES:
                raise ValueError("Archive is too large")
            if info.file_size > max(1, compressed_size) * _MAX_IMPORT_COMPRESSION_RATIO:
                raise ValueError("Archive contains highly compressed entries")
            if total_size > total_compressed_size * _MAX_IMPORT_COMPRESSION_RATIO:
                raise ValueError("Archive compression ratio is too high")
            dest_path = os.path.join(target_dir, *parts)
            ensure_within(dest_path, target_dir)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with zf.open(info, "r") as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

    def _workspace_payload_root(extracted_root):
        explicit = os.path.join(extracted_root, ".bullpen")
        if os.path.isdir(explicit):
            return explicit
        if os.path.exists(os.path.join(extracted_root, "config.json")):
            return extracted_root
        return None

    def _workers_payload_root(extracted_root):
        explicit = os.path.join(extracted_root, ".bullpen")
        if os.path.exists(os.path.join(explicit, "layout.json")):
            return explicit
        if os.path.exists(os.path.join(extracted_root, "layout.json")):
            return extracted_root
        return None

    def _replace_workspace_bp_dir(ws, source_bp_dir):
        from server.init import init_workspace

        bp_dir = ws.bp_dir
        previous_token = mcp_auth.read_workspace_mcp_token(bp_dir)
        if os.path.exists(bp_dir):
            shutil.rmtree(bp_dir)
        shutil.copytree(source_bp_dir, bp_dir)
        init_workspace(ws.path)
        _write_runtime_config(ws, preferred_token=previous_token)
        reconcile(bp_dir)
        state = load_state(bp_dir, ws.path, workspace_display=ws.name)
        state["workspaceId"] = ws.id
        socketio.emit("state:init", state, to=ws.id)
        socketio.emit("files:changed", {"workspaceId": ws.id}, to=ws.id)

    def _replace_workspace_workers(ws, source_bp_dir):
        from server.init import init_workspace
        from server.worker_types import normalize_layout

        source_layout_path = os.path.join(source_bp_dir, "layout.json")
        if not os.path.exists(source_layout_path):
            raise ValueError("Archive does not contain layout.json")

        source_layout = read_json(source_layout_path)
        if not isinstance(source_layout, dict):
            raise ValueError("layout.json must be a JSON object")
        slots = source_layout.get("slots", [])
        if not isinstance(slots, list):
            raise ValueError("layout.json slots must be a list")

        bp_dir = ws.bp_dir
        previous_token = mcp_auth.read_workspace_mcp_token(bp_dir)
        init_workspace(ws.path)
        _write_runtime_config(ws, preferred_token=previous_token)
        config = read_json(os.path.join(bp_dir, "config.json"))
        current_layout = normalize_layout(read_json(os.path.join(bp_dir, "layout.json")), config=config)
        merged_slots = _merge_imported_worker_slots(current_layout.get("slots", []), slots, config=config)
        write_json(os.path.join(bp_dir, "layout.json"), normalize_layout({"slots": merged_slots}, config=config))

        source_profiles_dir = os.path.join(source_bp_dir, "profiles")
        if os.path.isdir(source_profiles_dir):
            target_profiles_dir = os.path.join(bp_dir, "profiles")
            os.makedirs(target_profiles_dir, exist_ok=True)
            for filename in os.listdir(source_profiles_dir):
                if not filename.endswith(".json"):
                    continue
                src_path = os.path.join(source_profiles_dir, filename)
                dst_path = os.path.join(target_profiles_dir, filename)
                shutil.copy2(src_path, dst_path)

        reconcile(bp_dir)
        state = load_state(bp_dir, ws.path, workspace_display=ws.name)
        state["workspaceId"] = ws.id
        socketio.emit("state:init", state, to=ws.id)

    @legacy_route("/api/export/workspace")
    @auth.require_auth
    def export_workspace():
        ws, error = _workspace_from_id(_workspace_id_from_args())
        if error:
            return error
        export_name = f"bullpen-workspace-{ws.name}-{ws.id[:8]}.zip"
        return send_file(
            _export_workspace_zip_bytes(ws),
            mimetype="application/zip",
            as_attachment=True,
            download_name=export_name,
        )

    @legacy_route("/api/export/all")
    @auth.require_auth
    def export_all():
        export_name = f"bullpen-all-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.zip"
        return send_file(
            _export_all_zip_bytes(),
            mimetype="application/zip",
            as_attachment=True,
            download_name=export_name,
        )

    @legacy_route("/api/export/workers")
    @auth.require_auth
    def export_workers():
        ws, error = _workspace_from_id(_workspace_id_from_args())
        if error:
            return error
        export_name = f"bullpen-workers-{ws.name}-{ws.id[:8]}.zip"
        return send_file(
            _export_workers_zip_bytes(ws),
            mimetype="application/zip",
            as_attachment=True,
            download_name=export_name,
        )

    @legacy_route("/api/export/worker")
    @auth.require_auth
    def export_worker():
        ws, error = _workspace_from_id(_workspace_id_from_args())
        if error:
            return error
        try:
            slot = int(request.args.get("slot"))
        except (TypeError, ValueError):
            return jsonify({"error": "slot is required"}), 400
        slots = _normalized_worker_slots(ws)
        if slot < 0 or slot >= len(slots) or not isinstance(slots[slot], dict):
            return jsonify({"error": "Unknown worker slot"}), 404
        worker = slots[slot]
        export_name = f"bullpen-worker-{_worker_export_name(worker.get('name'), f'slot-{slot + 1}')}-{ws.id[:8]}.zip"
        return send_file(
            _export_workers_zip_bytes(ws, [worker], selected_slot=slot),
            mimetype="application/zip",
            as_attachment=True,
            download_name=export_name,
        )

    @legacy_route("/api/service/preview", methods=["POST"])
    @auth.require_auth
    def service_preview():
        from server.worker_types import get_worker_type, normalize_layout, normalize_worker_slot

        payload = request.get_json(silent=True) or {}
        ws, error = _workspace_from_id(_workspace_id_from_payload(payload), activate=True)
        if error:
            return error
        slot = payload.get("slot")
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            return jsonify({"error": "slot is required"}), 400

        config = read_json(os.path.join(ws.bp_dir, "config.json"))
        layout = normalize_layout(read_json(os.path.join(ws.bp_dir, "layout.json")), config=config)
        slots = layout.get("slots", [])
        if slot < 0 or slot >= len(slots) or not slots[slot]:
            return jsonify({"error": "Service worker slot not found"}), 404

        worker = dict(slots[slot])
        if worker.get("type") != "service":
            return jsonify({"error": "Selected worker is not a Service worker"}), 400
        fields = payload.get("fields") or {}
        if not isinstance(fields, dict):
            return jsonify({"error": "fields must be an object"}), 400
        for key, value in fields.items():
            if key not in {"task_queue", "state", "started_at"}:
                worker[key] = value
        worker = normalize_worker_slot(worker, index=slot, config=config)
        errors = get_worker_type("service").validate_config(worker)
        if errors:
            return jsonify({"error": errors[0]}), 400

        try:
            preview = service_worker_mod.resolve_service_preview(worker, ws.path, slot)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        suggested_port = None
        if worker.get("port") is None:
            suggested_port = service_worker_mod.suggest_service_port(layout, ignore_slot=slot)

        return jsonify({
            "cwd": preview["cwd"],
            "procfile_path": preview["procfile_path"],
            "command_source": preview["command_source"],
            "process_names": preview["process_names"],
            "selected_process": preview["selected_process"],
            "suggested_port": suggested_port,
            "raw_command": preview["raw_command"],
            "resolved_command": preview["resolved_command_redacted"],
            "warnings": preview["warnings"],
        })

    @legacy_route("/api/import/workspace", methods=["POST"])
    @auth.require_auth
    def import_workspace():
        ws_id = _workspace_id_from_args()
        ws, error = _workspace_from_id(ws_id)
        if error:
            return error
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return jsonify({"error": "Missing upload file"}), 400
        try:
            with zipfile.ZipFile(upload.stream, "r") as zf:
                with tempfile.TemporaryDirectory(prefix="bullpen_import_") as tmp_dir:
                    _safe_extract_zip(zf, tmp_dir)
                    payload_root = _workspace_payload_root(tmp_dir)
                    if not payload_root:
                        return jsonify({"error": "Archive does not contain a workspace .bullpen payload"}), 400
                    _replace_workspace_bp_dir(ws, payload_root)
        except zipfile.BadZipFile:
            return jsonify({"error": "Invalid zip file"}), 400
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True, "imported": 1, "workspaceId": ws_id})

    @legacy_route("/api/import/workers", methods=["POST"])
    @auth.require_auth
    def import_workers():
        ws_id = _workspace_id_from_args()
        ws, error = _workspace_from_id(ws_id)
        if error:
            return error
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return jsonify({"error": "Missing upload file"}), 400
        try:
            with zipfile.ZipFile(upload.stream, "r") as zf:
                with tempfile.TemporaryDirectory(prefix="bullpen_import_workers_") as tmp_dir:
                    _safe_extract_zip(zf, tmp_dir)
                    payload_root = _workers_payload_root(tmp_dir)
                    if not payload_root:
                        return jsonify({"error": "Archive does not contain a workers payload"}), 400
                    _replace_workspace_workers(ws, payload_root)
        except zipfile.BadZipFile:
            return jsonify({"error": "Invalid zip file"}), 400
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True, "imported": 1, "workspaceId": ws_id})

    @legacy_route("/api/import/all", methods=["POST"])
    @auth.require_auth
    def import_all():
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return jsonify({"error": "Missing upload file"}), 400
        imported = 0
        try:
            with zipfile.ZipFile(upload.stream, "r") as zf:
                with tempfile.TemporaryDirectory(prefix="bullpen_import_all_") as tmp_dir:
                    _safe_extract_zip(zf, tmp_dir)
                    workspaces_dir = os.path.join(tmp_dir, "workspaces")
                    if not os.path.isdir(workspaces_dir):
                        return jsonify({"error": "Archive does not contain a workspaces/ directory"}), 400
                    for ws in manager.all_workspaces():
                        candidate = os.path.join(workspaces_dir, ws.id)
                        if not os.path.isdir(candidate):
                            continue
                        payload_root = _workspace_payload_root(candidate)
                        if not payload_root:
                            continue
                        _replace_workspace_bp_dir(ws, payload_root)
                        imported += 1
        except zipfile.BadZipFile:
            return jsonify({"error": "Invalid zip file"}), 400
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        if imported == 0:
            return jsonify({"error": "No matching workspaces found in archive"}), 400
        return jsonify({"ok": True, "imported": imported})

    @socketio.on("connect")
    def on_connect(auth_data=None):
        # Reject unauthenticated Socket.IO upgrades. Flask-SocketIO makes
        # the HTTP session available here because the cookie is sent with
        # the WebSocket handshake; returning False refuses the connection.
        #
        # The MCP stdio server has no browser session, so it authenticates
        # by passing {"mcp_token": "<token>"} via Socket.IO ``auth``.  The
        # token is written to .bullpen/config.json on startup and is only
        # readable by processes with local filesystem access.
        token = (auth_data or {}).get("mcp_token") if isinstance(auth_data, dict) else None
        mcp_ws_id = (
            mcp_auth.find_workspace_id_for_token(manager.all_workspaces(), token)
            if mcp_auth is not None
            else None
        )
        is_mcp = bool(mcp_ws_id)
        if auth.auth_enabled() and not session.get("authenticated"):
            if not is_mcp:
                return False
        if is_mcp:
            mcp_sids.add(request.sid)
            mcp_sid_workspace[request.sid] = mcp_ws_id
            ws = manager.get_or_activate(mcp_ws_id)
            if not ws:
                mcp_sids.discard(request.sid)
                mcp_sid_workspace.pop(request.sid, None)
                return False
            sync_deploy_label_config(ws.bp_dir)
            join_room(ws.id)
            state = load_state(ws.bp_dir, ws.path)
            state["workspaceId"] = ws.id
            socketio.emit("state:init", state, to=request.sid)
            return

        join_room("authenticated")
        if not start_without_project:
            from server.workspace_manager import projects_root

            socketio.emit("project:settings", {"projectsRoot": projects_root() or ""}, to=request.sid)
            ws = manager.get_or_activate(startup_id)
            if ws:
                join_room(ws.id)
                state = load_state(ws.bp_dir, ws.path, workspace_display=ws.name)
                state["workspaceId"] = ws.id
                socketio.emit("state:init", state, to=request.sid)
            socketio.emit("projects:updated", manager.list_visible_projects(include_path=False), to=request.sid)

    @socketio.on("disconnect")
    def on_disconnect():
        terminal_manager = app.config.get("terminal_manager")
        if terminal_manager:
            terminal_manager.close_for_sid(request.sid)
        with app.config["toady_terminals_lock"]:
            for session_info in app.config["toady_terminals"].values():
                session_info.get("clients", set()).discard(request.sid)
        mcp_sids.discard(request.sid)
        mcp_sid_workspace.pop(request.sid, None)

    @socketio.on("sandbox:list")
    def toady_socket_list_sandboxes(_payload=None):
        import asyncio

        return {"ok": True, "sandboxes": asyncio.run(_sandbox_service().reconcile())}

    @socketio.on("base:status")
    def toady_socket_base_status(_payload=None):
        import asyncio

        status = asyncio.run(toady_base.base_status())
        if app.config["base_prepare"].get("running"):
            status["state"] = "preparing"
            status["message"] = "Preparing Microsandbox base..."
        status["prepare"] = _base_prepare_payload(include_logs=False)
        return {"ok": True, "base": status}

    @socketio.on("base:logs")
    def toady_socket_base_logs(_payload=None):
        return {"ok": True, "prepare": _base_prepare_payload(include_logs=True)}

    @socketio.on("base:prepare")
    def toady_socket_base_prepare(payload=None):
        state = app.config["base_prepare"]
        if state.get("running"):
            return {"ok": True, "started": False, "message": "Base preparation is already running."}
        rebuild = bool((payload or {}).get("rebuild"))
        socketio.start_background_task(_base_prepare_worker, rebuild)
        return {"ok": True, "started": True}

    @socketio.on("sandbox:create")
    def toady_socket_create_sandbox(payload):
        try:
            manifest = _sandbox_service().create_manifest(payload or {})
        except (SandboxServiceError, ValidationError) as exc:
            return _sandbox_error_payload(exc)
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
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
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
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
        _emit_sandboxes_updated()
        socketio.emit("sandbox:status", {"id": manifest["slug"], "status": manifest["last_status"]}, to="authenticated")
        return {"ok": True, "sandbox": manifest}

    @socketio.on("sandbox:destroy")
    def toady_socket_destroy_sandbox(payload):
        import asyncio

        payload = payload or {}
        slug = payload.get("slug") or payload.get("id") or ""
        try:
            deleted = asyncio.run(_sandbox_service().destroy(slug, purge_home=bool(payload.get("purge"))))
        except (SandboxServiceError, ValidationError, RuntimeError) as exc:
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
        if not deleted:
            return {"ok": False, "error": "Unknown sandbox"}
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
                app.config["toady_terminals"][terminal_id] = {
                    "id": terminal_id,
                    "sandbox_id": manifest.slug,
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
            return {
                "ok": True,
                "terminal": _toady_terminal_payload(session_info),
            }
        except (SandboxServiceError, ValidationError, PtyDriverError, ValueError) as exc:
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
            session_info["alive"] = False
            _close_toady_terminal(terminal_id)
            return {"ok": True}
        except (SandboxServiceError, PtyDriverError) as exc:
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
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
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
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
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
            socketio.emit("sandbox:error", {"id": slug, "error": str(exc)}, to=request.sid)
            return _sandbox_error_payload(exc)
        _emit_sandboxes_updated()
        socketio.emit("ports:updated", {"sandbox_id": slug, "ports": ports}, to="authenticated")
        return {"ok": True, "port": mapping, "ports": ports}

    if not start_without_project:
        from server.events import register_events

        register_events(socketio, app)

    # Start time-based scheduler for each workspace
    if not start_without_project:
        from server.scheduler import Scheduler

        for ws in manager.all_workspaces():
            scheduler = Scheduler(ws.bp_dir, socketio, ws_id=ws.id)
            scheduler.start()
            ws.scheduler = scheduler

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


def build_file_tree(workspace):
    """Build file tree excluding .git, node_modules, gitignored paths."""
    excluded = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv"}

    # Try to get gitignored paths
    gitignored = set()
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--directory"],
            capture_output=True, text=True, cwd=workspace, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line:
                    gitignored.add(line.rstrip("/"))
        # Always show .bullpen regardless of .gitignore
        gitignored.discard(".bullpen")
    except Exception:
        pass

    MAX_DEPTH = 20
    MAX_NODES = 10_000
    node_count = [0]  # mutable counter for nested scope

    def walk(path, rel="", depth=0):
        entries = []
        if depth >= MAX_DEPTH or node_count[0] >= MAX_NODES:
            return entries
        try:
            items = sorted(os.listdir(path))
        except PermissionError:
            return entries

        for name in items:
            if node_count[0] >= MAX_NODES:
                break
            if name.startswith(".") and name in excluded:
                continue
            rel_path = os.path.join(rel, name) if rel else name
            if rel_path in gitignored or name in excluded:
                continue
            full = os.path.join(path, name)
            node_count[0] += 1
            if os.path.islink(full):
                # Skip symlinked directories to prevent traversal/loops
                if os.path.isdir(full):
                    continue
                entries.append({"name": name, "path": rel_path, "type": "file"})
            elif os.path.isdir(full):
                children = walk(full, rel_path, depth + 1)
                entries.append({"name": name, "path": rel_path, "type": "dir", "children": children})
            else:
                entries.append({"name": name, "path": rel_path, "type": "file"})
        return entries

    return walk(workspace)


def reconcile(bp_dir):
    """Startup reconciliation: make ticket frontmatter canonical.

    Worker queues are derived indexes. On startup, discard persisted queue
    references, repair interrupted in-progress tasks, and rebuild queues from
    assigned tickets so stale layout state cannot survive a restart.
    """
    from server.worker_types import normalize_layout

    layout_path = os.path.join(bp_dir, "layout.json")
    if not os.path.exists(layout_path):
        return

    config = read_json(os.path.join(bp_dir, "config.json"))
    layout = normalize_layout(read_json(layout_path), config=config)
    slots = layout.get("slots", [])
    if not isinstance(slots, list):
        slots = []
        layout["slots"] = slots

    for slot in slots:
        if slot is None:
            continue
        if slot.get("task_queue"):
            slot["task_queue"] = []
        else:
            slot.setdefault("task_queue", [])
        if slot.get("state") == "working":
            slot["state"] = "idle"

    from server.tasks import task_sort_key, update_task

    def valid_assigned_slot(value):
        if value in (None, ""):
            return None
        try:
            slot_index = int(value)
        except (TypeError, ValueError):
            return None
        if slot_index < 0 or slot_index >= len(slots):
            return None
        if not slots[slot_index]:
            return None
        return slot_index

    def with_reconcile_note(body, note):
        body = body or ""
        if note in body:
            return body
        return body.rstrip() + "\n\n" + note + "\n"

    queued = []
    tasks_dir = os.path.join(bp_dir, "tasks")
    if os.path.isdir(tasks_dir):
        for fname in sorted(os.listdir(tasks_dir)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(tasks_dir, fname)
            try:
                meta, body, slug = read_frontmatter(path)
            except Exception:
                continue
            task_id = slug or fname[:-3]
            status = meta.get("status")
            assigned_slot = valid_assigned_slot(meta.get("assigned_to"))

            if status == "in_progress":
                note = (
                    "**Interrupted run:** Bullpen restarted while this task was "
                    "in progress. Task moved to blocked."
                )
                try:
                    update_task(bp_dir, task_id, {
                        "status": "blocked",
                        "assigned_to": "",
                        "handoff_depth": 0,
                        "body": with_reconcile_note(body, note),
                    })
                except Exception:
                    pass
                continue

            if status != "assigned":
                continue

            if assigned_slot is None:
                if meta.get("assigned_to") not in (None, ""):
                    note = (
                        "**Assignment repair:** Assigned worker no longer exists. "
                        "Task moved to blocked."
                    )
                    try:
                        update_task(bp_dir, task_id, {
                            "status": "blocked",
                            "assigned_to": "",
                            "handoff_depth": 0,
                            "body": with_reconcile_note(body, note),
                        })
                    except Exception:
                        pass
                continue

            queued.append((
                assigned_slot,
                *task_sort_key({**meta, "id": task_id}),
                task_id,
            ))

    queued.sort(key=lambda item: item[:-1])
    for slot_index, *_sort_fields, task_id in queued:
        slots[slot_index].setdefault("task_queue", []).append(task_id)

    write_json(layout_path, normalize_layout(layout, config=config))

    # Check watched columns for idle on_queue workers with unclaimed tasks
    from server import workers as worker_mod
    watched_columns = set()
    for slot in layout.get("slots", []):
        if (slot
                and slot.get("activation") == "on_queue"
                and slot.get("watch_column")
                and slot.get("state") == "idle"
                and not slot.get("paused")):
            watched_columns.add(slot["watch_column"])
    for col in watched_columns:
        worker_mod.check_watch_columns(bp_dir, col)
    worker_mod.drain_runnable_queues(bp_dir)

    workspace = os.path.dirname(bp_dir)
    try:
        from server import worktrees as worktree_mod

        worktree_mod.reconcile_worktrees(workspace, bp_dir)
    except Exception:
        pass


def load_state(bp_dir, workspace, workspace_display=None):
    """Load full app state from .bullpen/ files."""
    from server.profiles import list_profiles
    from server.teams import list_teams
    from server.worker_types import ViewerContext, serialize_layout

    config = read_json(os.path.join(bp_dir, "config.json"))
    if not isinstance(config.get("theme"), str):
        config["theme"] = "dark"
    if config.get("ambient_preset") in ("", False):
        config["ambient_preset"] = None
    elif config.get("ambient_preset") is not None and not isinstance(config.get("ambient_preset"), str):
        config["ambient_preset"] = None
    try:
        ambient_volume = int(config.get("ambient_volume", 40))
    except (TypeError, ValueError):
        ambient_volume = 40
    config["ambient_volume"] = max(0, min(100, ambient_volume))
    layout = read_json(os.path.join(bp_dir, "layout.json"))
    layout = serialize_layout(layout, viewer=ViewerContext(can_edit=True), config=config)

    # Load all tasks
    tasks = []
    tasks_dir = os.path.join(bp_dir, "tasks")
    if os.path.isdir(tasks_dir):
        for fname in sorted(os.listdir(tasks_dir)):
            if fname.endswith(".md"):
                path = os.path.join(tasks_dir, fname)
                meta, body, slug = read_frontmatter(path)
                task = {**meta, "id": slug or fname[:-3], "body": body}
                tasks.append(task)

    profiles = list_profiles(bp_dir)
    teams = list_teams(bp_dir)

    return {
        "workspace": workspace_display or workspace,
        "config": config,
        "layout": layout,
        "tasks": tasks,
        "profiles": profiles,
        "teams": teams,
    }
