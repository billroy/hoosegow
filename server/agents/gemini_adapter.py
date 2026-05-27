"""Gemini CLI adapter."""

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from server.agents.base import AgentAdapter
from server.usage import extract_gemini_usage_event, merge_usage_dicts

if sys.platform == "win32":
    _GEMINI_SEARCH_PATHS = [
        os.path.expanduser(r"~\AppData\Local\Programs\gemini\gemini.cmd"),
        os.path.expanduser(r"~\AppData\Roaming\npm\gemini.cmd"),
    ]
else:
    _GEMINI_SEARCH_PATHS = [
        os.path.expanduser("~/.local/bin/gemini"),
        "/usr/local/bin/gemini",
        "/opt/homebrew/bin/gemini",
    ]


def _is_executable(path):
    """Check if a file is executable (extension-based on Windows, X_OK elsewhere)."""
    if not os.path.isfile(path):
        return False
    if sys.platform == "win32":
        return os.path.splitext(path)[1].lower() in {".exe", ".cmd", ".bat", ".com"}
    return os.access(path, os.X_OK)


def _find_gemini():
    """Find the gemini binary on PATH or common install locations."""
    configured = os.environ.get("BULLPEN_GEMINI_PATH")
    if configured and _is_executable(os.path.expanduser(configured)):
        return os.path.expanduser(configured)

    found = shutil.which("gemini")
    if found:
        return found
    for path in _GEMINI_SEARCH_PATHS:
        if _is_executable(path):
            return path
    return None


def _parse_dotenv_line(line):
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return key, value.replace('\\"', '"').replace("\\\\", "\\")


def _load_gemini_env_file():
    """Load sandbox-local Gemini headless auth env from ~/.gemini/.env."""
    env_path = Path.home() / ".gemini" / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    allowed = {
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_GENAI_USE_GCA",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_APPLICATION_CREDENTIALS",
    }
    env = {}
    for line in lines:
        parsed = _parse_dotenv_line(line)
        if not parsed:
            continue
        key, value = parsed
        if key in allowed and value:
            env[key] = value
    return env


class GeminiAdapter(AgentAdapter):

    @property
    def name(self):
        return "gemini"

    def available(self):
        return _find_gemini() is not None

    def unavailable_message(self):
        configured = os.environ.get("BULLPEN_GEMINI_PATH")
        if configured:
            return (
                "Gemini CLI is not available. BULLPEN_GEMINI_PATH is set to "
                f"{configured!r}, but that file was not found or is not executable."
            )
        return (
            "Gemini CLI is not available. Install Gemini CLI and authenticate, "
            "or set BULLPEN_GEMINI_PATH to the gemini executable."
        )

    def build_argv(self, prompt, model, workspace, bp_dir=None):
        gemini_bin = _find_gemini() or "gemini"
        argv = [
            gemini_bin,
            "--model", model,
            "--output-format", "stream-json",
            "--allowed-mcp-server-names", "bullpen",
            "--approval-mode", "yolo",
            "--prompt", prompt,
        ]
        return argv

    def prepare_env(self, workspace, bp_dir=None, task_id=None):
        """Inject Bullpen MCP settings for this Gemini run without touching the project."""
        if not bp_dir:
            return None

        run_tmp = tempfile.mkdtemp(prefix="bullpen-gemini-")
        settings_path = os.path.join(run_tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(self._mcp_settings(bp_dir), f)

        env = os.environ.copy()
        env.update(_load_gemini_env_file())
        env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = settings_path
        return env, run_tmp

    def _mcp_settings(self, bp_dir):
        """Return a Gemini CLI settings.json fragment for the Bullpen MCP server."""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        server_script = os.path.join(project_root, "server", "mcp_tools.py")
        bp_dir = os.path.abspath(bp_dir)

        from server.persistence import read_json

        bp_config = read_json(os.path.join(bp_dir, "config.json"))
        host = bp_config.get("server_host", "127.0.0.1")
        if host == "0.0.0.0":
            host = "127.0.0.1"
        port = str(bp_config.get("server_port", 5000))

        return {
            "security": {
                "folderTrust": {
                    "enabled": False,
                },
            },
            "mcp": {
                "allowed": ["bullpen"],
            },
            "mcpServers": {
                "bullpen": {
                    "command": sys.executable,
                    "args": [
                        server_script,
                        "--bp-dir", bp_dir,
                        "--host", host,
                        "--port", port,
                    ],
                    "cwd": project_root,
                    "env": {
                        "PYTHONPATH": project_root,
                    },
                    "timeout": 120000,
                    "trust": True,
                    "includeTools": [
                        "list_tickets",
                        "list_tasks",
                        "list_tickets_by_title",
                        "create_ticket",
                        "update_ticket",
                    ],
                },
            },
        }

    def prompt_via_stdin(self):
        """Gemini headless mode receives the prompt through --prompt.

        Passing both --prompt and stdin causes Gemini CLI to append the stdin
        content to the prompt, which duplicates the user's turn.
        """
        return False

    def format_stream_line(self, line):
        """Extract display text from Gemini output when possible."""
        line = line.strip()
        if not line:
            return None

        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return line

        msg_type = obj.get("type")
        if msg_type in ("init", "result", "tool_use", "tool_result", "error"):
            return None
        if msg_type == "message" and obj.get("role") == "user":
            return None

        response = obj.get("response")
        if isinstance(response, str) and response.strip():
            return response.strip()

        text = obj.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        message = obj.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()

        content = obj.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            texts = [item.get("text", "") for item in content if isinstance(item, dict)]
            merged = "\n".join([t for t in texts if t])
            if merged:
                return merged

        # Metadata payloads (session/stats/tools/files) are non-display.
        if any(key in obj for key in ("session_id", "stats", "tools", "files")):
            return None

        return line

    def parse_output(self, stdout, stderr, exit_code):
        """Parse Gemini output; supports JSONL result lines and plain text fallback."""
        usage = {}
        is_error = False
        error_msg = ""
        result_text = ""
        non_json_lines = []
        saw_json = False

        for raw_line in (stdout or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                non_json_lines.append(raw_line)
                continue
            saw_json = True

            usage = merge_usage_dicts(usage, extract_gemini_usage_event(obj))

            if obj.get("type") == "message" and obj.get("role") == "user":
                continue

            if obj.get("type") in ("init", "tool_use", "tool_result", "error"):
                continue

            if obj.get("type") == "result":
                is_error = bool(obj.get("is_error"))
                result_text = (
                    obj.get("result")
                    or obj.get("response")
                    or obj.get("text")
                    or obj.get("message")
                    or ""
                )
                if is_error:
                    error_msg = result_text or obj.get("error", "")
                continue

            response = obj.get("response")
            if isinstance(response, str) and response.strip():
                result_text = response.strip()
                continue

            text = obj.get("text")
            if isinstance(text, str) and text:
                non_json_lines.append(text)
                continue
            message = obj.get("message")
            if isinstance(message, str) and message:
                non_json_lines.append(message)
                continue
            content = obj.get("content")
            if isinstance(content, str) and content:
                non_json_lines.append(content)
                continue

            if any(key in obj for key in ("session_id", "stats", "tools", "files")):
                continue

            non_json_lines.append(raw_line)

        if not result_text and non_json_lines:
            result_text = "\n".join(non_json_lines).strip()

        if exit_code != 0 or is_error:
            return {
                "success": False,
                "output": result_text,
                "error": error_msg or stderr.strip() or f"Exit code {exit_code}",
                "usage": usage,
            }

        if not result_text and not saw_json:
            result_text = (stdout or "").strip() or (stderr or "").strip()

        return {
            "success": True,
            "output": result_text.strip(),
            "error": None,
            "usage": usage,
        }
