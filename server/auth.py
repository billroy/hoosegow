"""Minimal local authentication for Toady.

Credentials live in ``GLOBAL_DIR/.env`` as an INI-like key=value file that
we parse manually (no third-party dotenv/YAML libs, per project convention).
When the env file is absent or lacks credentials, auth is disabled and the
server behaves exactly as before.
"""

from __future__ import annotations

import json
import os
import secrets
from functools import wraps
from typing import Callable, Dict, Optional, Tuple

from flask import jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash as _wz_check_password_hash
from werkzeug.security import generate_password_hash as _wz_generate_password_hash


ENV_FILENAME = ".env"

USERNAME_KEY = "TOADY_USERNAME"
PASSWORD_HASH_KEY = "TOADY_PASSWORD_HASH"
USERS_JSON_KEY = "TOADY_USERS_JSON"
SECRET_KEY_KEY = "TOADY_SECRET_KEY"
LEGACY_USERNAME_KEY = "BULLPEN_USERNAME"
LEGACY_PASSWORD_HASH_KEY = "BULLPEN_PASSWORD_HASH"
LEGACY_USERS_JSON_KEY = "BULLPEN_USERS_JSON"
LEGACY_SECRET_KEY_KEY = "BULLPEN_SECRET_KEY"


# Module-level cache populated by load_credentials().
_state: Dict[str, Optional[str]] = {
    "users": {},
    "username": None,
    "password_hash": None,
    "loaded": False,
}


# ---------------------------------------------------------------------------
# Env file parsing (manual — no libs)
# ---------------------------------------------------------------------------


def env_path(global_dir: str) -> str:
    return os.path.join(global_dir, ENV_FILENAME)


def parse_env_file(path: str) -> Dict[str, str]:
    """Parse a simple KEY=VALUE file. Returns {} if the file is missing.

    - Blank lines and lines starting with ``#`` are ignored.
    - Values may optionally be wrapped in single or double quotes, which are
      stripped. No escaping or interpolation is performed.
    - Lines without ``=`` are silently skipped (malformed is never fatal).
    """
    result: Dict[str, str] = {}
    if not os.path.isfile(path):
        return result
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if (len(value) >= 2
                        and value[0] == value[-1]
                        and value[0] in ("'", '"')):
                    value = value[1:-1]
                if key:
                    result[key] = value
    except OSError:
        return {}
    return result


def write_env_file(path: str, mapping: Dict[str, str]) -> None:
    """Write ``mapping`` to ``path`` as KEY=VALUE lines, chmod 600 on POSIX.

    The file is created with restricted permissions from the start so that
    the hashed password is never briefly world-readable. On Windows the
    chmod call is a no-op, which matches Python's behavior.
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    # Open with O_CREAT | O_WRONLY | O_TRUNC and mode 0o600 so new files are
    # created with restrictive perms atomically.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for key, value in mapping.items():
                f.write(f"{key}={value}\n")
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    # Ensure mode is 600 even if the file already existed.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Credential loading / state
# ---------------------------------------------------------------------------


def reset_auth_cache() -> None:
    """Clear cached credential state. Call at the top of ``create_app`` so
    each test (or re-initialization) re-reads its own isolated global dir."""
    _state["users"] = {}
    _state["username"] = None
    _state["password_hash"] = None
    _state["loaded"] = False


def _extract_users(data: Dict[str, str]) -> Dict[str, str]:
    users: Dict[str, str] = {}
    raw_users = data.get(USERS_JSON_KEY) or data.get(LEGACY_USERS_JSON_KEY)
    if raw_users:
        try:
            parsed = json.loads(raw_users)
        except (TypeError, ValueError):
            parsed = {}
        if isinstance(parsed, dict):
            for raw_name, raw_hash in parsed.items():
                if not isinstance(raw_name, str):
                    continue
                username = raw_name.strip()
                if not username:
                    continue
                if isinstance(raw_hash, str) and raw_hash:
                    users[username] = raw_hash

    # Backward-compat: if a legacy single-user entry exists, include it.
    legacy_user = (data.get(USERNAME_KEY) or data.get(LEGACY_USERNAME_KEY) or "").strip()
    legacy_hash = (data.get(PASSWORD_HASH_KEY) or data.get(LEGACY_PASSWORD_HASH_KEY) or "").strip()
    if legacy_user and legacy_hash and legacy_user not in users:
        users[legacy_user] = legacy_hash
    return users


def parse_credentials_mapping(data: Dict[str, str]) -> Dict[str, str]:
    """Return normalized username->password-hash mapping from env data."""
    return _extract_users(data)


def apply_credentials_mapping(env_data: Dict[str, str], users: Dict[str, str]) -> Dict[str, str]:
    """Merge users into ``env_data``, keeping unrelated keys untouched.

    Stores users in ``TOADY_USERS_JSON`` and keeps
    ``TOADY_USERNAME``/``TOADY_PASSWORD_HASH`` in sync for simple inspection.
    """
    updated = dict(env_data)
    # Normalize + drop invalid entries.
    cleaned: Dict[str, str] = {}
    for raw_name, raw_hash in users.items():
        if not isinstance(raw_name, str):
            continue
        username = raw_name.strip()
        if not username:
            continue
        if isinstance(raw_hash, str) and raw_hash:
            cleaned[username] = raw_hash

    if not cleaned:
        updated.pop(USERS_JSON_KEY, None)
        updated.pop(USERNAME_KEY, None)
        updated.pop(PASSWORD_HASH_KEY, None)
        return updated

    updated[USERS_JSON_KEY] = json.dumps(cleaned, separators=(",", ":"), sort_keys=True)
    primary = sorted(cleaned.keys())[0]
    updated[USERNAME_KEY] = primary
    updated[PASSWORD_HASH_KEY] = cleaned[primary]
    return updated


def load_credentials(global_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """Load credentials from ``global_dir/.env``.

    Returns a backward-compatible primary ``(username, password_hash)``
    tuple for callers that still expect a single-user shape. Auth is
    disabled when no usable credentials are found. Never raises for parse
    errors.
    """
    data = parse_env_file(env_path(global_dir))
    users = _extract_users(data)
    _state["users"] = users
    if not users:
        _state["username"] = None
        _state["password_hash"] = None
    else:
        username = sorted(users.keys())[0]
        password_hash = users[username]
        _state["username"] = username
        _state["password_hash"] = password_hash
    _state["loaded"] = True
    return _state["username"], _state["password_hash"]


def auth_enabled() -> bool:
    """True iff credentials were successfully loaded."""
    return bool(_state["users"])


def get_username() -> Optional[str]:
    return _state["username"]


def get_users() -> Dict[str, str]:
    return dict(_state["users"])


def get_password_hash(username: str) -> Optional[str]:
    if not username:
        return None
    return _state["users"].get(username)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def generate_password_hash(plain: str) -> str:
    """Werkzeug password hash. Uses the current default scheme."""
    return _wz_generate_password_hash(plain)


def check_password(plain: str, hashed: Optional[str]) -> bool:
    """Constant-time password comparison via Werkzeug."""
    if not hashed:
        return False
    try:
        return _wz_check_password_hash(hashed, plain)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Flask SECRET_KEY bootstrap
# ---------------------------------------------------------------------------


def load_or_create_secret_key(global_dir: str) -> str:
    """Return the Flask SECRET_KEY, generating and persisting one if needed.

    The key is stored alongside credentials in ``GLOBAL_DIR/.env`` so that
    sessions survive restarts. When generating a new key we preserve any
    existing entries in the file.
    """
    path = env_path(global_dir)
    data = parse_env_file(path)
    existing = data.get(SECRET_KEY_KEY) or data.get(LEGACY_SECRET_KEY_KEY)
    if existing:
        return existing
    new_key = secrets.token_hex(32)
    data[SECRET_KEY_KEY] = new_key
    write_env_file(path, data)
    return new_key


# ---------------------------------------------------------------------------
# Request classification + decorator
# ---------------------------------------------------------------------------


def is_xhr_request(req) -> bool:
    """Return True for XHR / JSON API requests that should get 401 JSON
    instead of a 302 redirect to the login page.

    We treat a request as XHR if:
      - it carries the legacy ``X-Requested-With: XMLHttpRequest`` header, OR
      - its ``Accept`` header prefers JSON over HTML (i.e. mentions
        ``application/json`` and does not mention ``text/html``).
    """
    xrw = req.headers.get("X-Requested-With", "")
    if xrw.lower() == "xmlhttprequest":
        return True
    accept = req.headers.get("Accept", "") or ""
    accept_l = accept.lower()
    if "application/json" in accept_l and "text/html" not in accept_l:
        return True
    return False


def require_auth(view: Callable) -> Callable:
    """Decorator: gate a view on ``session['authenticated']``.

    When auth is disabled globally this is a pass-through so the existing
    developer experience (no env file → no login) is preserved.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not auth_enabled():
            return view(*args, **kwargs)
        if session.get("authenticated"):
            return view(*args, **kwargs)
        if is_xhr_request(request):
            return jsonify({"error": "authentication required"}), 401
        # Preserve the requested path so we can redirect back after login.
        next_url = request.full_path if request.query_string else request.path
        if next_url.endswith("?"):
            next_url = next_url[:-1]
        return redirect(url_for("login") + f"?next={next_url}")

    return wrapper


# ---------------------------------------------------------------------------
# CSRF (login form only)
# ---------------------------------------------------------------------------


def generate_csrf_token() -> str:
    """Return a CSRF token, creating one in the session if missing."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def validate_csrf_token(submitted: Optional[str]) -> bool:
    """Constant-time comparison against the session token."""
    expected = session.get("csrf_token")
    if not expected or not submitted:
        return False
    return secrets.compare_digest(str(expected), str(submitted))
