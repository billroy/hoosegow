"""Claude CLI adapter."""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from server.agents.base import AgentAdapter

# Common install locations for claude CLI
if sys.platform == "win32":
    _CLAUDE_SEARCH_PATHS = [
        os.path.expanduser(r"~\AppData\Local\Programs\claude\claude.cmd"),
        os.path.expanduser(r"~\AppData\Roaming\npm\claude.cmd"),
    ]
else:
    _CLAUDE_SEARCH_PATHS = [
        os.path.expanduser("~/.local/bin/claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]


CLAUDE_OAUTH_EXPIRY_SKEW_SECONDS = 300
_CLAUDE_OAUTH_REFRESH_LOCK = threading.Lock()
_CLAUDE_REFRESH_LOCK_HELD_ENV = "BULLPEN_CLAUDE_REFRESH_LOCK_HELD"
_SYSTEM_CA_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt"
_SYSTEM_CA_CERT_DIR = "/etc/ssl/certs"


def _is_executable(path):
    """Check if a file is executable (extension-based on Windows, X_OK elsewhere)."""
    if not os.path.isfile(path):
        return False
    if sys.platform == "win32":
        return os.path.splitext(path)[1].lower() in {".exe", ".cmd", ".bat", ".com"}
    return os.access(path, os.X_OK)


def _find_claude():
    """Find the claude binary on PATH or common install locations."""
    configured = os.environ.get("BULLPEN_CLAUDE_PATH")
    if configured and _is_executable(os.path.expanduser(configured)):
        return os.path.expanduser(configured)

    found = shutil.which("claude")
    if found:
        return found
    for path in _CLAUDE_SEARCH_PATHS:
        if _is_executable(path):
            return path
    return None


def _make_isolated_tmpdir(prefix):
    """Create a private temp directory, preferring the current TMPDIR root."""
    candidates = [os.environ.get("TMPDIR"), tempfile.gettempdir()]
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            os.makedirs(candidate, exist_ok=True)
            return tempfile.mkdtemp(prefix=prefix, dir=candidate)
        except OSError:
            continue
    return tempfile.mkdtemp(prefix=prefix)


def _claude_source_credentials_path():
    source_config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if source_config_dir:
        return Path(source_config_dir).expanduser() / ".credentials.json"
    return Path.home() / ".claude" / ".credentials.json"


def _remove_claude_auth_overrides(env):
    """Prefer Claude's credentials file over parent-process auth overrides."""
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)


def _copy_claude_credentials(target_config_dir):
    """Copy Claude OAuth credentials without carrying hooks/plugins/session state."""
    source_credentials = _claude_source_credentials_path()
    if not source_credentials.is_file():
        return False

    target = Path(target_config_dir) / ".credentials.json"
    shutil.copy2(source_credentials, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return True


def _oauth_expires_at_seconds(oauth):
    expires_at = oauth.get("expiresAt")
    if not isinstance(expires_at, (int, float)):
        return None
    return expires_at / 1000 if expires_at > 10_000_000_000 else expires_at


def _read_claude_credentials(credentials_path):
    try:
        data = json.loads(Path(credentials_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    return data


def _oauth_has_live_access(oauth, *, now=None):
    if not oauth.get("accessToken"):
        return False
    expires_at = _oauth_expires_at_seconds(oauth)
    if expires_at is None:
        return True
    if now is None:
        now = time.time()
    return expires_at > now + CLAUDE_OAUTH_EXPIRY_SKEW_SECONDS


def _credentials_have_refresh(data):
    oauth = (data or {}).get("claudeAiOauth")
    return isinstance(oauth, dict) and bool(oauth.get("refreshToken"))


def _credentials_have_live_access(data, *, now=None):
    oauth = (data or {}).get("claudeAiOauth")
    return isinstance(oauth, dict) and _oauth_has_live_access(oauth, now=now)


def _merge_missing_refresh_token(target_data, source_data):
    target_oauth = (target_data or {}).get("claudeAiOauth")
    source_oauth = (source_data or {}).get("claudeAiOauth")
    if not isinstance(target_oauth, dict) or not isinstance(source_oauth, dict):
        return target_data
    if target_oauth.get("refreshToken") or not source_oauth.get("refreshToken"):
        return target_data
    merged = dict(target_data)
    merged_oauth = dict(target_oauth)
    merged_oauth["refreshToken"] = source_oauth["refreshToken"]
    merged["claudeAiOauth"] = merged_oauth
    return merged


def _claude_credentials_need_refresh(credentials_path, *, now=None):
    """Return True when launching claude is expected to refresh OAuth credentials."""
    data = _read_claude_credentials(credentials_path)
    if data is None:
        return False
    oauth = data.get("claudeAiOauth")
    if not oauth.get("refreshToken"):
        return False
    if not oauth.get("accessToken"):
        return True
    expires_at = _oauth_expires_at_seconds(oauth)
    if expires_at is None:
        return False
    if now is None:
        now = time.time()
    return expires_at <= now + CLAUDE_OAUTH_EXPIRY_SKEW_SECONDS


def _sync_claude_credentials_back(target_config_dir):
    """Mirror a refreshed access token from the run dir back to source.

    Without this, every Live Agent send copies the same expired-access
    token into a fresh isolated dir; claude refreshes against
    console.anthropic.com on every send, and gets rate-limited (429)
    after enough back-to-back sends — which surfaces as a silent hang.
    """
    source = _claude_source_credentials_path()
    target = Path(target_config_dir) / ".credentials.json"
    if not target.is_file():
        return False
    source_data = _read_claude_credentials(source)
    target_data = _read_claude_credentials(target)
    if target_data is None:
        return False
    if not (
        _credentials_have_refresh(target_data)
        or _credentials_have_live_access(target_data)
    ):
        return False
    sync_data = _merge_missing_refresh_token(target_data, source_data)
    if not (
        _credentials_have_refresh(sync_data)
        or _credentials_have_live_access(sync_data)
    ):
        return False
    try:
        sync_bytes = json.dumps(sync_data, separators=(",", ":")).encode("utf-8")
        if source.is_file() and sync_bytes == source.read_bytes():
            return False
    except OSError:
        pass
    try:
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(sync_bytes)
        try:
            os.chmod(source, 0o600)
        except OSError:
            pass
        return True
    except OSError:
        return False


def _claude_credentials_usable(credentials_path):
    """A credentials file is usable if claude itself can act on it.

    A live accessToken is sufficient; a refreshToken alone is also sufficient
    because claude will mint a new access token on demand. Pre-flighting
    expiresAt strictly here is wrong — it rejects credentials that claude
    would happily refresh, and surfaces a misleading "not authenticated"
    error inside long-lived sandboxes.
    """
    try:
        data = json.loads(Path(credentials_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return False
    return bool(oauth.get("accessToken") or oauth.get("refreshToken"))


class ClaudeAdapter(AgentAdapter):

    @property
    def name(self):
        return "claude"

    def available(self):
        return _find_claude() is not None

    def unavailable_message(self):
        configured = os.environ.get("BULLPEN_CLAUDE_PATH")
        if configured:
            return (
                "Claude CLI is not available. BULLPEN_CLAUDE_PATH is set to "
                f"{configured!r}, but that file was not found or is not executable."
            )
        return (
            "Claude CLI is not available. Install Claude Code CLI and authenticate, "
            "or set BULLPEN_CLAUDE_PATH to the claude executable."
        )

    def build_argv(self, prompt, model, workspace, bp_dir=None):
        claude_bin = _find_claude() or "claude"
        argv = [
            claude_bin,
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--no-session-persistence",
            "--setting-sources", "user",
            "--model", model,
        ]
        if bp_dir:
            config = self._mcp_config(bp_dir)
            argv.extend(["--mcp-config", config])
        # Prompt is delivered via stdin in _run_agent
        return argv

    def prepare_env(self, workspace, bp_dir=None, task_id=None):
        """Run Claude with private temp/config roots for headless launches."""
        run_tmp = _make_isolated_tmpdir("bullpen-claude-")
        source_credentials = _claude_source_credentials_path()
        source_credentials_usable = _claude_credentials_usable(source_credentials)
        refresh_lock_held = False
        try:
            if (
                source_credentials_usable
                and _claude_credentials_need_refresh(source_credentials)
            ):
                _CLAUDE_OAUTH_REFRESH_LOCK.acquire()
                refresh_lock_held = True
            env = os.environ.copy()
            env["TMPDIR"] = run_tmp
            env["TMP"] = run_tmp
            env["TEMP"] = run_tmp
            env["CLAUDE_CODE_TMPDIR"] = run_tmp
            if source_credentials_usable:
                claude_config_dir = os.path.join(run_tmp, "claude-config")
                os.makedirs(claude_config_dir, mode=0o700, exist_ok=True)
                copied_credentials = _copy_claude_credentials(claude_config_dir)
                if copied_credentials:
                    env["CLAUDE_CONFIG_DIR"] = claude_config_dir
            if env.get("CLAUDE_CONFIG_DIR"):
                _remove_claude_auth_overrides(env)
            if os.path.isfile(_SYSTEM_CA_CERT_FILE):
                env.setdefault("SSL_CERT_FILE", _SYSTEM_CA_CERT_FILE)
            if os.path.isdir(_SYSTEM_CA_CERT_DIR):
                env.setdefault("SSL_CERT_DIR", _SYSTEM_CA_CERT_DIR)
            if refresh_lock_held:
                env[_CLAUDE_REFRESH_LOCK_HELD_ENV] = "1"
            return env, run_tmp
        except Exception:
            if refresh_lock_held:
                _CLAUDE_OAUTH_REFRESH_LOCK.release()
            raise

    def finalize_env(self, env, run_tmp):
        """Mirror any refreshed credentials back to the source path.

        Called after the claude subprocess exits and before run_tmp is
        unlinked. Without this, claude rewrites the access token inside
        the isolated config dir on each refresh, and the next invocation
        re-copies the expired source — leading to repeated OAuth refresh
        traffic and eventual 429 rate-limiting.
        """
        config_dir = (env or {}).get("CLAUDE_CONFIG_DIR")
        try:
            if config_dir:
                _sync_claude_credentials_back(config_dir)
        finally:
            if (env or {}).get(_CLAUDE_REFRESH_LOCK_HELD_ENV) == "1":
                _CLAUDE_OAUTH_REFRESH_LOCK.release()

    def _mcp_config(self, bp_dir):
        """Generate a temporary MCP config file pointing to bullpen tools."""
        server_script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcp_tools.py")
        # Project root is parent of server/ — needed on PYTHONPATH so
        # mcp_tools.py can do `from server import tasks`
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # Read server host/port from config
        from server.persistence import read_json
        bp_config = read_json(os.path.join(bp_dir, "config.json"))
        host = bp_config.get("server_host", "127.0.0.1")
        if host == "0.0.0.0":
            # MCP helper is a local client and should connect via loopback.
            host = "127.0.0.1"
        port = str(bp_config.get("server_port", 5000))
        config = {
            "mcpServers": {
                "bullpen": {
                    "command": sys.executable,
                    "args": [server_script, "--bp-dir", bp_dir, "--host", host, "--port", port],
                    "env": {"PYTHONPATH": project_root},
                }
            }
        }
        fd, path = tempfile.mkstemp(suffix=".json", prefix="bullpen-mcp-")
        with os.fdopen(fd, "w") as f:
            json.dump(config, f)
        return path

    def format_stream_line(self, line):
        """Extract display text from a stream-json line."""
        line = line.strip()
        if not line:
            return None
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return line  # Pass through non-JSON lines as-is

        msg_type = obj.get("type")

        if msg_type == "assistant":
            parts = []
            for block in obj.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    # Show a compact summary of the tool call
                    if name == "Bash":
                        parts.append(f"$ {inp.get('command', '')}")
                    elif name == "Edit":
                        parts.append(f"[Edit] {inp.get('file_path', '')}")
                    elif name == "Write":
                        parts.append(f"[Write] {inp.get('file_path', '')}")
                    elif name == "Read":
                        parts.append(f"[Read] {inp.get('file_path', '')}")
                    elif name in ("Glob", "Grep"):
                        parts.append(f"[{name}] {inp.get('pattern', '')}")
                    else:
                        parts.append(f"[{name}]")
            return "\n".join(parts) if parts else None

        if msg_type == "tool":
            # Tool output — show content
            content = obj.get("content", "")
            if isinstance(content, list):
                texts = [item.get("text", "") for item in content if isinstance(item, dict)]
                text = "\n".join(texts)
            else:
                text = str(content)
            # Truncate very long tool output for display
            if len(text) > 2000:
                text = text[:2000] + "\n[output truncated]"
            return text if text else None

        if msg_type == "result":
            return None  # Final result is handled by parse_output

        if msg_type == "system" and obj.get("subtype") == "api_retry":
            # Surface retry storms so a stuck refresh / rate limit does not
            # look like a silent hang. Each api_retry doubles backoff and
            # there can be up to ~10 attempts before claude gives up.
            attempt = obj.get("attempt")
            max_retries = obj.get("max_retries")
            error = obj.get("error") or "unknown"
            status = obj.get("error_status")
            status_part = f" status={status}" if status else ""
            attempt_part = f"{attempt}/{max_retries}" if attempt and max_retries else "?"
            return f"[claude api_retry attempt={attempt_part} error={error}{status_part}]"

        # Skip system/init, rate_limit_event, user echo, etc.
        return None

    def parse_output(self, stdout, stderr, exit_code):
        # In stream-json mode, stdout is multiple JSON lines.
        # The last meaningful line is type=result.
        result_text = ""
        is_error = False
        error_msg = None
        usage = {}

        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") == "result":
                result_text = obj.get("result", "")
                is_error = obj.get("is_error", False)
                usage = obj.get("usage", {})
                if is_error:
                    error_msg = result_text
                break

        # Fallback: if no result line found (e.g. crash), use raw stdout
        if not result_text and not is_error:
            # Try to extract assistant text from the stream
            for line in stdout.strip().splitlines():
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "assistant":
                        for block in obj.get("message", {}).get("content", []):
                            if block.get("type") == "text":
                                result_text += block["text"]
                except (json.JSONDecodeError, KeyError):
                    continue

        if exit_code != 0 and not is_error:
            return {
                "success": False,
                "output": result_text,
                "error": error_msg or stderr.strip() or f"Exit code {exit_code}",
                "usage": usage,
            }

        if is_error:
            return {
                "success": False,
                "output": "",
                "error": error_msg or "Unknown error",
                "usage": usage,
            }

        return {
            "success": True,
            "output": result_text.strip(),
            "error": None,
            "usage": usage,
        }
