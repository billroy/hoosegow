"""Codex CLI adapter."""

import json
import os
import shutil
import sys

from server.agents.base import AgentAdapter
from server.usage import extract_codex_usage_event, merge_usage_dicts, merge_usage_max

if sys.platform == "win32":
    _CODEX_SEARCH_PATHS = [
        os.path.expanduser(r"~\AppData\Local\Programs\codex\codex.cmd"),
        os.path.expanduser(r"~\AppData\Roaming\npm\codex.cmd"),
    ]
else:
    _CODEX_SEARCH_PATHS = [
        os.path.expanduser("~/.local/bin/codex"),
        "/usr/local/bin/codex",
        "/opt/homebrew/bin/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
        os.path.expanduser("~/Applications/Codex.app/Contents/Resources/codex"),
    ]


def _is_executable(path):
    """Check if a file is executable (extension-based on Windows, X_OK elsewhere)."""
    if not os.path.isfile(path):
        return False
    if sys.platform == "win32":
        return os.path.splitext(path)[1].lower() in {".exe", ".cmd", ".bat", ".com"}
    return os.access(path, os.X_OK)


def _find_codex():
    """Find the codex binary on PATH or common install locations."""
    configured = os.environ.get("BULLPEN_CODEX_PATH")
    if configured and _is_executable(os.path.expanduser(configured)):
        return os.path.expanduser(configured)

    found = shutil.which("codex")
    if found:
        return found
    for path in _CODEX_SEARCH_PATHS:
        if _is_executable(path):
            return path
    return None


def _codex_execution_flags():
    """Return Codex execution flags for the current runtime environment."""
    sandbox_mode = os.environ.get("BULLPEN_CODEX_SANDBOX", "").strip().lower()
    if sandbox_mode in {"none", "off", "false", "0", "bypass"}:
        return ["--dangerously-bypass-approvals-and-sandbox"]
    if sandbox_mode in {"read-only", "readonly"}:
        return ["--sandbox", "read-only"]
    if sandbox_mode in {"danger-full-access", "danger", "full", "full-access"}:
        return ["--sandbox", "danger-full-access"]
    return ["--sandbox", "workspace-write"]


class CodexAdapter(AgentAdapter):

    @property
    def name(self):
        return "codex"

    def available(self):
        return _find_codex() is not None

    def unavailable_message(self):
        configured = os.environ.get("BULLPEN_CODEX_PATH")
        if configured:
            return (
                "Codex CLI is not available. BULLPEN_CODEX_PATH is set to "
                f"{configured!r}, but that file was not found or is not executable."
            )
        return (
            "Codex CLI is not available. Install the Codex CLI, set "
            "BULLPEN_CODEX_PATH to the codex executable, or install the Codex "
            "desktop app where Bullpen can discover it."
        )

    def build_argv(self, prompt, model, workspace, bp_dir=None):
        codex_bin = _find_codex() or "codex"
        argv = [
            codex_bin,
            "exec",
            "--model", model,
            *_codex_execution_flags(),
            "--json",
            "--skip-git-repo-check",
        ]
        if bp_dir:
            argv.extend(self._mcp_overrides(bp_dir))
        argv.append("-")  # Read prompt from stdin
        # Prompt is delivered via stdin
        return argv

    def _mcp_overrides(self, bp_dir):
        """Return -c overrides to register the bullpen MCP server for this run."""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        server_script = os.path.join(project_root, "server", "mcp_tools.py")
        bp_dir = os.path.abspath(bp_dir)
        from server.persistence import read_json

        bp_config = read_json(os.path.join(bp_dir, "config.json"))
        host = bp_config.get("server_host", "127.0.0.1")
        if host == "0.0.0.0":
            host = "127.0.0.1"
        port = str(bp_config.get("server_port", 5000))
        args = [server_script, "--bp-dir", bp_dir, "--host", host, "--port", port]

        return [
            "-c", f"mcp_servers.bullpen.command={json.dumps(sys.executable)}",
            "-c", f"mcp_servers.bullpen.args={json.dumps(args)}",
            "-c", f"mcp_servers.bullpen.cwd={json.dumps(project_root)}",
            "-c", f"mcp_servers.bullpen.env.PYTHONPATH={json.dumps(project_root)}",
            "-c", "mcp_servers.bullpen.tool_timeout_sec=120",
        ]

    def format_stream_line(self, line):
        """Extract display text from a Codex --json JSONL line."""
        line = line.strip()
        if not line:
            return None
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return line  # Pass through non-JSON lines as-is

        evt_type = obj.get("type", "")

        if evt_type == "item.completed":
            item = obj.get("item", {})
            item_type = item.get("type", "")
            if item_type == "agent_message":
                return item.get("text")
            if item_type == "command_execution":
                cmd = item.get("command", "")
                exit_code = item.get("exit_code")
                parts = [f"$ {cmd}"]
                output = item.get("output", "")
                if output:
                    if len(output) > 2000:
                        output = output[:2000] + "\n[output truncated]"
                    parts.append(output)
                if exit_code and exit_code != 0:
                    parts.append(f"[exit code {exit_code}]")
                return "\n".join(parts)
            if item_type == "file_change":
                path = item.get("path", "")
                action = item.get("action", "modified")
                return f"[{action}] {path}"
            if item_type == "mcp_tool_call":
                tool = item.get("tool", "?")
                return f"[MCP] {tool}"

        if evt_type == "item.started":
            item = obj.get("item", {})
            if item.get("type") == "command_execution":
                return f"$ {item.get('command', '')}"

        # Skip turn.started, turn.completed, thread.started, etc.
        return None

    def parse_output(self, stdout, stderr, exit_code):
        """Parse Codex --json JSONL output, extracting the final message and usage."""
        last_message = ""
        usage = {}
        token_count_usage = {}
        has_error = False
        error_text = ""

        for line in (stdout or "").strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            evt_type = obj.get("type", "")

            extracted = extract_codex_usage_event(obj)
            if evt_type == "token_count":
                token_count_usage = merge_usage_max(token_count_usage, extracted)
            else:
                usage = merge_usage_dicts(usage, extracted)

            if evt_type == "item.completed":
                item = obj.get("item", {})
                if item.get("type") == "agent_message":
                    last_message = item.get("text", "")

            elif evt_type == "turn.failed":
                has_error = True
                error_text = obj.get("error", {}).get("message", "Turn failed")

            elif evt_type == "error":
                has_error = True
                error_text = obj.get("message", "Unknown error")

        # Fallback: if no JSON parsed, use raw stdout/stderr
        if not last_message and not has_error:
            last_message = (stdout or "").strip()
            if not last_message and stderr:
                last_message = stderr.strip()

        # token_count events are snapshots; fold them in via max to avoid
        # double counting against turn.completed usage.
        usage = merge_usage_max(usage, token_count_usage)

        if exit_code != 0 or has_error:
            return {
                "success": False,
                "output": last_message,
                "error": error_text or (stderr.strip() if stderr else "") or f"Exit code {exit_code}",
                "usage": usage,
            }

        return {
            "success": True,
            "output": last_message,
            "error": None,
            "usage": usage,
        }
