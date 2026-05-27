#!/usr/bin/env python3
"""Bullpen MCP stdio server.

This process exposes ticket tools over JSON-RPC 2.0. It supports both:
- MCP stdio framing (`Content-Length` headers + body)
- newline-delimited JSON messages

Responses are emitted using the same transport format as the request.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import threading
from dataclasses import dataclass
from typing import Any

import io
import os

import socketio

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from server import mcp_auth
from server import tasks as task_store

VALID_TYPES = ("task", "bug", "feature", "chore")
VALID_PRIORITIES = ("low", "normal", "high", "urgent")
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_OPERATION_TIMEOUT_SECONDS = 10.0
MAX_CONNECT_ATTEMPTS = 3

# The MCP protocol uses stdout for framed JSON-RPC messages.  Any stray byte
# on stdout (e.g. from the socketio library logging a connection error)
# corrupts the stream and causes Claude Code to kill this process — which is
# the root cause of the intermittent "MCP not found" failures.
#
# We capture the real stdout *once* at import time, then redirect sys.stdout
# to stderr so that print() / logging from any library is harmless.
_mcp_out: io.RawIOBase | None = None  # set in main()

TOOLS = [
    {
        "name": "create_ticket",
        "description": "Create a new ticket in inbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Ticket title"},
                "description": {"type": "string", "description": "Markdown description", "default": ""},
                "type": {"type": "string", "enum": list(VALID_TYPES), "default": "task"},
                "priority": {"type": "string", "enum": list(VALID_PRIORITIES), "default": "normal"},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "status": {"type": "string", "description": "Initial status (default: inbox)"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "list_tickets",
        "description": "List tickets with optional status filter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional status filter (inbox, assigned, in_progress, review, done, blocked).",
                }
            },
        },
    },
    {
        "name": "list_tasks",
        "description": "Alias for list_tickets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional status filter (inbox, assigned, in_progress, review, done, blocked).",
                }
            },
        },
    },
    {
        "name": "list_tickets_by_title",
        "description": "List tickets whose titles approximately match a query string.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title query used for approximate matching.",
                },
                "status": {
                    "type": "string",
                    "description": "Optional status filter (inbox, assigned, in_progress, review, done, blocked).",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_ticket",
        "description": "Update an existing ticket.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Ticket id"},
                "title": {"type": "string"},
                "body": {"type": "string", "description": "Full markdown body"},
                "type": {"type": "string", "enum": list(VALID_TYPES)},
                "priority": {"type": "string", "enum": list(VALID_PRIORITIES)},
                "status": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id"],
        },
    },
]


def _parse_content_length(header_line: bytes) -> int:
    """Parse a single Content-Length header line."""
    try:
        name, value = header_line.split(b":", 1)
    except ValueError as exc:
        raise ValueError("Invalid Content-Length header") from exc
    if name.strip().lower() != b"content-length":
        raise ValueError("Invalid Content-Length header")
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError("Invalid Content-Length value") from exc
    if parsed < 0:
        raise ValueError("Invalid Content-Length value")
    return parsed


def _read(in_stream: Any | None = None, return_mode: bool = False):
    """Read one JSON-RPC message from stdin.

    Supported formats:
    - MCP framed messages (headers + body) -> mode "framed"
    - newline-delimited JSON -> mode "line"
    """
    if in_stream is None:
        in_stream = sys.stdin.buffer

    while True:
        line = in_stream.readline()
        if not line:
            return None
        if not line.strip():
            continue

        is_header = b":" in line and not line.lstrip().startswith((b"{", b"["))
        if not is_header:
            parsed = json.loads(line.decode("utf-8"))
            if return_mode:
                return parsed, "line"
            return parsed

        headers = [line]
        while True:
            header_line = in_stream.readline()
            if not header_line:
                return None
            if not header_line.strip():
                break
            headers.append(header_line)

        content_length = None
        for header in headers:
            if header.lower().startswith(b"content-length:"):
                content_length = _parse_content_length(header)
                break
        if content_length is None:
            raise ValueError("Missing Content-Length header")

        body = in_stream.read(content_length)
        if body is None or len(body) != content_length:
            return None
        parsed = json.loads(body.decode("utf-8"))
        if return_mode:
            return parsed, "framed"
        return parsed


def _write(msg: dict[str, Any], out_stream: Any | None = None, mode: str = "framed") -> None:
    """Write one JSON-RPC message using the selected transport.

    Always writes to the saved ``_mcp_out`` stream (the *real* stdout captured
    before we redirected sys.stdout to stderr).  This guarantees that library
    code calling ``print()`` can never corrupt the MCP protocol stream.
    """
    if out_stream is None:
        out_stream = _mcp_out or sys.stdout.buffer
    payload = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if mode == "line":
        out_stream.write(payload + b"\n")
    else:
        out_stream.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
        out_stream.write(payload)
    out_stream.flush()


def _result(msg_id: Any, result: dict[str, Any], mode: str = "framed") -> None:
    _write({"jsonrpc": "2.0", "id": msg_id, "result": result}, mode=mode)


def _error(msg_id: Any, code: int, message: str, mode: str = "framed") -> None:
    _write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}, mode=mode)


def _tool_result(msg_id: Any, text: str, is_error: bool = False, mode: str = "framed") -> None:
    _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": is_error}, mode=mode)


@dataclass
class _Pending:
    event: threading.Event
    result: dict[str, Any] | None = None
    error: str | None = None


class BullpenClient:
    """Small socket.io helper for create/update operations."""

    def __init__(self, host: str, port: int, bp_dir: str = ".bullpen"):
        self.host = host
        self.port = port
        self.bp_dir = bp_dir
        self.workspace_path = os.path.realpath(os.path.dirname(os.path.abspath(bp_dir)))
        self._auth_via_token = False
        self.sio = socketio.Client(logger=False, engineio_logger=False, reconnection=False)
        self.connected = False
        self.workspace_id: str | None = None
        self.connect_timeout_seconds = DEFAULT_CONNECT_TIMEOUT_SECONDS
        self.operation_timeout_seconds = DEFAULT_OPERATION_TIMEOUT_SECONDS
        self.last_connect_error: str | None = None
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}

        @self.sio.on("connect")
        def _on_connect() -> None:
            self.connected = True

        @self.sio.on("disconnect")
        def _on_disconnect() -> None:
            self.connected = False

        @self.sio.on("state:init")
        def _on_state_init(data: dict[str, Any]) -> None:
            workspace_id = data.get("workspaceId")
            if not workspace_id:
                return
            if self._auth_via_token:
                self.workspace_id = workspace_id
                return
            workspace = data.get("workspace")
            if isinstance(workspace, str) and workspace:
                if os.path.realpath(workspace) == self.workspace_path:
                    self.workspace_id = workspace_id

        @self.sio.on("task:created")
        def _on_task_created(data: dict[str, Any]) -> None:
            self._resolve("create", result=data)

        @self.sio.on("task:updated")
        def _on_task_updated(data: dict[str, Any]) -> None:
            self._resolve("update", result=data)

        @self.sio.on("error")
        def _on_error(data: dict[str, Any]) -> None:
            message = "Unknown error"
            if isinstance(data, dict):
                message = str(data.get("message", message))
            self._resolve_any_error(message)

    def _read_mcp_token(self) -> str | None:
        """Read the workspace MCP token from the shared secrets store."""
        return mcp_auth.read_workspace_mcp_token(self.bp_dir)

    def _candidate_urls(self) -> list[str]:
        hosts = [self.host]
        if self.host == "0.0.0.0":
            hosts.extend(["127.0.0.1", "localhost"])
        return [f"http://{candidate}:{self.port}" for candidate in hosts]

    def _transport_attempts(self) -> list[list[str]]:
        """Return Socket.IO transports to try, in preference order."""
        return [["websocket"], ["polling"]]

    def _connect_best_effort(self) -> bool:
        if self.connected:
            return True
        token = self._read_mcp_token()
        self._auth_via_token = bool(token)
        auth_data = {"mcp_token": token} if token else None
        errors: list[str] = []
        for attempt in range(MAX_CONNECT_ATTEMPTS):
            for url in self._candidate_urls():
                for transports in self._transport_attempts():
                    try:
                        self.sio.connect(
                            url,
                            wait_timeout=self.connect_timeout_seconds,
                            auth=auth_data,
                            transports=transports,
                        )
                        self.connected = True
                        if self.workspace_id is None:
                            print(
                                "WARNING: MCP connected but the server did not assign a workspace_id",
                                file=sys.stderr,
                            )
                        return True
                    except TypeError:
                        # Compatibility for older socketio client signatures.
                        try:
                            self.sio.connect(url)
                            self.connected = True
                            return True
                        except Exception as exc:
                            errors.append(f"{url}: legacy connect failed: {exc}")
                    except Exception as exc:
                        transport_label = ",".join(transports)
                        errors.append(f"{url} via {transport_label}: {exc}")
                    # Ensure clean state before next attempt.
                    try:
                        self.sio.disconnect()
                    except Exception:
                        pass
                    continue
        self.connected = False
        self.last_connect_error = "; ".join(errors[-4:]) if errors else "No connection attempts succeeded"
        return False

    def _connection_failure_message(self, op: str) -> str:
        secrets_path = mcp_auth.shared_secrets_path()
        token_hint = ""
        if not self._read_mcp_token():
            token_hint = (
                f" No mcp_token was found in {secrets_path}; start or restart Bullpen "
                "for this project so it can write current MCP runtime config and shared secrets."
            )
        detail = f" Last error: {self.last_connect_error}." if self.last_connect_error else ""
        return (
            f"Bullpen socket connection unavailable for {op}. "
            f"Workspace: {self.workspace_path}. "
            f"MCP config: {self.bp_dir}. "
            f"Server: {self.host}:{self.port}."
            f"{token_hint}{detail} "
            "Ensure Bullpen is running for this workspace; external MCP clients should launch "
            "`python3 bullpen.py mcp --workspace <project>` from the Bullpen checkout."
        )

    def _prepare_pending(self, op: str) -> _Pending:
        entry = _Pending(event=threading.Event())
        with self._lock:
            self._pending[op] = entry
        return entry

    def _resolve(self, op: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        with self._lock:
            pending = self._pending.pop(op, None)
        if pending is None:
            return
        pending.result = result
        pending.error = error
        pending.event.set()

    def _resolve_any_error(self, error: str) -> None:
        with self._lock:
            keys = list(self._pending.keys())
        if not keys:
            return
        self._resolve(keys[0], error=error)

    def create_ticket(self, args: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        if not self._connect_best_effort():
            return None, self._connection_failure_message("create_ticket")
        if not self.workspace_id:
            return None, "MCP client has no workspace_id — server did not identify a workspace for this token"

        pending = self._prepare_pending("create")
        payload = {
            "workspaceId": self.workspace_id,
            "title": args.get("title", "Untitled"),
            "description": args.get("description", ""),
            "type": args.get("type", "task"),
            "priority": args.get("priority", "normal"),
            "tags": args.get("tags", []),
        }
        if "status" in args:
            payload["status"] = args["status"]
        self.sio.emit("task:create", payload)
        if not pending.event.wait(timeout=self.operation_timeout_seconds):
            with self._lock:
                self._pending.pop("create", None)
            return None, "Timed out waiting for task:create response"
        if pending.error:
            return None, pending.error
        if pending.result is None:
            return None, "Missing task:create response payload"
        return pending.result, None

    def update_ticket(self, args: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        if not self._connect_best_effort():
            return None, self._connection_failure_message("update_ticket")
        if not self.workspace_id:
            return None, "MCP client has no workspace_id — server did not identify a workspace for this token"

        pending = self._prepare_pending("update")
        payload = dict(args)
        payload["workspaceId"] = self.workspace_id
        self.sio.emit("task:update", payload)
        if not pending.event.wait(timeout=self.operation_timeout_seconds):
            with self._lock:
                self._pending.pop("update", None)
            return None, "Timed out waiting for task:update response"
        if pending.error:
            return None, pending.error
        if pending.result is None:
            return None, "Missing task:update response payload"
        return pending.result, None

    def disconnect(self) -> None:
        try:
            if self.connected:
                self.sio.disconnect()
        except Exception:
            return


def _render_ticket_summary(ticket: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": ticket.get("id"),
        "title": ticket.get("title"),
        "status": ticket.get("status"),
        "type": ticket.get("type"),
        "priority": ticket.get("priority"),
    }


def _normalize_query_text(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _title_matches_query(title: str, query: str) -> bool:
    normalized_title = _normalize_query_text(title)
    normalized_query = _normalize_query_text(query)
    if not normalized_query:
        return False
    if normalized_query in normalized_title:
        return True

    query_tokens = [token for token in normalized_query.split() if len(token) >= 2]
    if query_tokens and all(token in normalized_title for token in query_tokens):
        return True

    if len(normalized_query) < 4:
        return False

    # Fuzzy fallback for minor typos in title searches.
    return difflib.SequenceMatcher(None, normalized_query, normalized_title).ratio() >= 0.5


def handle_call(
    bp_dir: str,
    client: BullpenClient | None,
    msg_id: Any,
    name: str,
    args: dict[str, Any],
    io_mode: str = "framed",
) -> None:
    """Dispatch a tools/call request."""
    if name == "create_ticket":
        title = str(args.get("title", "")).strip()
        if not title:
            _tool_result(msg_id, "Error: title is required", is_error=True, mode=io_mode)
            return
        if client is None:
            _tool_result(msg_id, "Error: create_ticket unavailable", is_error=True, mode=io_mode)
            return
        created, err = client.create_ticket(args)
        if err:
            _tool_result(msg_id, f"Error: {err}", is_error=True, mode=io_mode)
            return
        _tool_result(msg_id, json.dumps(_render_ticket_summary(created or {})), mode=io_mode)
        return

    if name in {"list_tickets", "list_tasks"}:
        status_filter = args.get("status")
        tickets = task_store.list_tasks(bp_dir)
        if status_filter:
            tickets = [item for item in tickets if item.get("status") == status_filter]
        payload = [_render_ticket_summary(ticket) for ticket in tickets]
        _tool_result(msg_id, json.dumps(payload, indent=2), mode=io_mode)
        return

    if name == "list_tickets_by_title":
        title_query = str(args.get("title", "")).strip()
        if not title_query:
            _tool_result(msg_id, "Error: title is required", is_error=True, mode=io_mode)
            return
        status_filter = args.get("status")
        tickets = task_store.list_tasks(bp_dir)
        if status_filter:
            tickets = [item for item in tickets if item.get("status") == status_filter]
        tickets = [
            item for item in tickets
            if _title_matches_query(str(item.get("title", "")), title_query)
        ]
        payload = [_render_ticket_summary(ticket) for ticket in tickets]
        _tool_result(msg_id, json.dumps(payload, indent=2), mode=io_mode)
        return

    if name == "update_ticket":
        ticket_id = str(args.get("id", "")).strip()
        if not ticket_id:
            _tool_result(msg_id, "Error: id is required", is_error=True, mode=io_mode)
            return
        if client is None:
            _tool_result(msg_id, "Error: update_ticket unavailable", is_error=True, mode=io_mode)
            return
        updated, err = client.update_ticket(args)
        if err:
            _tool_result(msg_id, f"Error: {err}", is_error=True, mode=io_mode)
            return
        _tool_result(msg_id, json.dumps(_render_ticket_summary(updated or {})), mode=io_mode)
        return

    _error(msg_id, -32602, f"Unknown tool: {name}", mode=io_mode)


DEFAULT_PROTOCOL_VERSION = "2025-11-25"


def _initialize_result(requested_protocol_version: str | None = None) -> dict[str, Any]:
    protocol_version = requested_protocol_version or DEFAULT_PROTOCOL_VERSION
    return {
        "protocolVersion": protocol_version,
        "capabilities": {
            "tools": {
                "listChanged": False,
            }
        },
        "serverInfo": {
            "name": "bullpen",
            "version": "2.0.0",
        },
    }


def resolve_runtime_args(
    *,
    bp_dir: str | None = None,
    workspace: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> tuple[str, str, int]:
    """Resolve the .bullpen path and server address for an MCP run."""
    if bp_dir:
        resolved_bp_dir = os.path.abspath(bp_dir)
    else:
        workspace_path = os.path.abspath(workspace or os.getcwd())
        resolved_bp_dir = os.path.join(workspace_path, ".bullpen")

    if not os.path.isdir(resolved_bp_dir):
        raise ValueError(f".bullpen directory not found: {resolved_bp_dir}")

    config_path = os.path.join(resolved_bp_dir, "config.json")
    config: dict[str, Any] = {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            if isinstance(loaded, dict):
                config = loaded
    except FileNotFoundError:
        config = {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid MCP config JSON: {config_path}") from exc

    resolved_host = host or str(config.get("server_host") or "127.0.0.1")
    raw_port = port if port is not None else config.get("server_port", 5000)
    try:
        resolved_port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid MCP server port in {config_path}: {raw_port!r}") from exc

    return resolved_bp_dir, resolved_host, resolved_port


def main(bp_dir: str, host: str, port: int) -> None:
    global _mcp_out  # noqa: PLW0603

    # ── Protect the MCP stdio stream ──────────────────────────────────
    # Capture the *real* stdout for MCP I/O, then redirect sys.stdout to
    # stderr.  After this point every print() / logging call from any
    # library (socketio, engineio, etc.) goes to stderr instead of
    # poisoning the MCP framing on stdout.
    _mcp_out = sys.stdout.buffer
    sys.stdout = open(os.devnull, "w") if sys.stderr is None else sys.stderr

    client = BullpenClient(host, port, bp_dir=bp_dir)
    io_mode = "framed"
    try:
        while True:
            try:
                read_result = _read(return_mode=True)
            except Exception:
                # Malformed input — skip, don't die.
                continue
            if read_result is None:
                break
            message, io_mode = read_result

            try:
                method = message.get("method")
                msg_id = message.get("id")

                if method == "initialize":
                    params = message.get("params", {})
                    requested_protocol_version = None
                    if isinstance(params, dict):
                        value = params.get("protocolVersion")
                        if isinstance(value, str) and value:
                            requested_protocol_version = value
                    _result(msg_id, _initialize_result(requested_protocol_version), mode=io_mode)
                    continue

                if method == "notifications/initialized":
                    continue

                if method == "tools/list":
                    _result(msg_id, {"tools": TOOLS}, mode=io_mode)
                    continue

                if method == "tools/call":
                    params = message.get("params", {})
                    name = params.get("name")
                    args = params.get("arguments", {})
                    if not isinstance(name, str):
                        _error(msg_id, -32602, "Invalid tools/call request: missing tool name", mode=io_mode)
                        continue
                    if not isinstance(args, dict):
                        _error(msg_id, -32602, "Invalid tools/call request: arguments must be an object", mode=io_mode)
                        continue
                    handle_call(bp_dir, client, msg_id, name, args, io_mode=io_mode)
                    continue

                if msg_id is not None:
                    _error(msg_id, -32601, f"Method not found: {method}", mode=io_mode)
            except Exception as exc:
                # Return the error over the wire rather than crashing.
                if msg_id is not None:
                    _error(msg_id, -32603, f"Internal error: {exc}", mode=io_mode)
    finally:
        client.disconnect()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bullpen MCP stdio server")
    parser.add_argument("--workspace", help="Project workspace directory; defaults to current directory")
    parser.add_argument("--bp-dir", help="Path to .bullpen directory; defaults to --workspace/.bullpen")
    parser.add_argument("--host", help="Bullpen socket.io host; defaults to .bullpen/config.json")
    parser.add_argument("--port", type=int, help="Bullpen socket.io port; defaults to .bullpen/config.json")
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    cli_args = _build_arg_parser().parse_args(argv)
    try:
        bp_dir, host, port = resolve_runtime_args(
            bp_dir=cli_args.bp_dir,
            workspace=cli_args.workspace,
            host=cli_args.host,
            port=cli_args.port,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    main(bp_dir, host, port)
    return 0


if __name__ == "__main__":
    sys.exit(run_cli())
