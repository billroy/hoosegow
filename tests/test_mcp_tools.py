"""Tests for MCP tool server behaviors."""

import io
import json
import os
import subprocess
import sys

from server import mcp_auth
from server import mcp_tools
from server.init import init_workspace
from server.tasks import create_task


class _DummySio:
    def __init__(self):
        self.handlers = {}

    def on(self, name):
        def _register(fn):
            self.handlers[name] = fn
            return fn
        return _register

    def connect(self, url, **_kwargs):
        raise RuntimeError(f"connect failed: {url}")

    def disconnect(self):
        return None

    def emit(self, *_args, **_kwargs):
        raise AssertionError("emit should not be called when disconnected")


class _NoConnectSio:
    def __init__(self):
        self.handlers = {}

    def on(self, name):
        def _register(fn):
            self.handlers[name] = fn
            return fn
        return _register

    def connect(self, *_args, **_kwargs):
        raise AssertionError("connect must not run during MCP initialize/tools/list")

    def disconnect(self):
        return None


class _NeverAckSio:
    def __init__(self):
        self.handlers = {}

    def on(self, name):
        def _register(fn):
            self.handlers[name] = fn
            return fn
        return _register

    def connect(self, *_args, **_kwargs):
        return None

    def disconnect(self):
        return None

    def emit(self, *_args, **_kwargs):
        # Intentionally never publish task:* ack events.
        return None


class _ConnectedClient:
    def __init__(self, *_args, **_kwargs):
        self.connected = True

    def disconnect(self):
        return None


class _RecordingSio:
    def __init__(self):
        self.handlers = {}
        self.connect_calls = []

    def on(self, name):
        def _register(fn):
            self.handlers[name] = fn
            return fn
        return _register

    def connect(self, *args, **kwargs):
        self.connect_calls.append((args, kwargs))
        if "connect" in self.handlers:
            self.handlers["connect"]()
        if "state:init" in self.handlers:
            self.handlers["state:init"]({"workspaceId": "ws-1"})

    def disconnect(self):
        return None

    def emit(self, *_args, **_kwargs):
        return None


class _FallbackRecordingSio(_RecordingSio):
    def connect(self, *args, **kwargs):
        self.connect_calls.append((args, kwargs))
        if kwargs.get("transports") == ["websocket"]:
            raise RuntimeError("websocket transport unavailable")
        if "connect" in self.handlers:
            self.handlers["connect"]()
        if "state:init" in self.handlers:
            self.handlers["state:init"]({"workspaceId": "ws-1"})


class _MultiWorkspaceSio(_RecordingSio):
    def __init__(self, target_workspace):
        super().__init__()
        self.target_workspace = target_workspace

    def connect(self, *args, **kwargs):
        self.connect_calls.append((args, kwargs))
        if "connect" in self.handlers:
            self.handlers["connect"]()
        if "state:init" in self.handlers:
            self.handlers["state:init"]({
                "workspaceId": "other-ws",
                "workspace": "/tmp/other-project",
            })
            self.handlers["state:init"]({
                "workspaceId": "target-ws",
                "workspace": self.target_workspace,
            })
            self.handlers["state:init"]({
                "workspaceId": "late-other-ws",
                "workspace": "/tmp/late-other-project",
            })


def _frame(msg, content_type_first=False):
    payload = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    headers = []
    if content_type_first:
        headers.append(b"Content-Type: application/json\r\n")
    headers.append(b"Content-Length: " + str(len(payload)).encode("ascii") + b"\r\n")
    return b"".join(headers) + b"\r\n" + payload


def _parse_framed_messages(raw):
    stream = io.BytesIO(raw)
    out = []
    while True:
        header = stream.readline()
        if not header:
            break
        if not header.strip():
            continue
        assert header.lower().startswith(b"content-length:")
        length = int(header.split(b":", 1)[1].strip())
        assert stream.readline() == b"\r\n"
        body = stream.read(length)
        out.append(json.loads(body.decode("utf-8")))
    return out


def _parse_line_messages(raw):
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line.decode("utf-8")))
    return out


def test_mcp_tools_script_help_runs_without_pythonpath():
    root = os.path.dirname(os.path.dirname(__file__))
    result = subprocess.run(
        [sys.executable, os.path.join(root, "server", "mcp_tools.py"), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--workspace" in result.stdout


def test_list_tasks_alias_returns_ticket_summary(tmp_workspace, monkeypatch):
    bp_dir = init_workspace(tmp_workspace)
    created = create_task(bp_dir, "MCP alias test")

    captured = {}

    def fake_tool_result(msg_id, text, is_error=False, mode="framed"):
        captured["id"] = msg_id
        captured["text"] = text
        captured["is_error"] = is_error

    monkeypatch.setattr(mcp_tools, "_tool_result", fake_tool_result)

    mcp_tools.handle_call(bp_dir, client=None, msg_id=7, name="list_tasks", args={})

    assert captured["id"] == 7
    assert captured["is_error"] is False
    summary = json.loads(captured["text"])
    ids = {item["id"] for item in summary}
    assert created["id"] in ids


def test_list_tickets_by_title_returns_approximate_matches(tmp_workspace, monkeypatch):
    bp_dir = init_workspace(tmp_workspace)
    matched = create_task(bp_dir, "SocketIO CORS rejects reverse-proxy connections")
    create_task(bp_dir, "Add kanban column icons")

    captured = {}

    def fake_tool_result(msg_id, text, is_error=False, mode="framed"):
        captured["id"] = msg_id
        captured["text"] = text
        captured["is_error"] = is_error

    monkeypatch.setattr(mcp_tools, "_tool_result", fake_tool_result)

    mcp_tools.handle_call(
        bp_dir,
        client=None,
        msg_id=8,
        name="list_tickets_by_title",
        args={"title": "sockt cors reverse proxy"},
    )

    assert captured["id"] == 8
    assert captured["is_error"] is False
    summary = json.loads(captured["text"])
    ids = {item["id"] for item in summary}
    assert matched["id"] in ids
    assert len(summary) == 1


def test_list_tickets_by_title_requires_title(tmp_workspace, monkeypatch):
    bp_dir = init_workspace(tmp_workspace)

    captured = {}

    def fake_tool_result(msg_id, text, is_error=False, mode="framed"):
        captured["id"] = msg_id
        captured["text"] = text
        captured["is_error"] = is_error

    monkeypatch.setattr(mcp_tools, "_tool_result", fake_tool_result)

    mcp_tools.handle_call(bp_dir, client=None, msg_id=9, name="list_tickets_by_title", args={})

    assert captured["id"] == 9
    assert captured["is_error"] is True
    assert captured["text"] == "Error: title is required"


def test_bullpen_client_degrades_when_socket_unavailable(monkeypatch):
    monkeypatch.setattr(
        mcp_tools.socketio,
        "Client",
        lambda **_kwargs: _DummySio(),
    )

    client = mcp_tools.BullpenClient("127.0.0.1", 5050)
    assert client.connected is False

    task, err = client.create_ticket({"title": "x"})
    assert task is None
    assert "unavailable" in err
    assert "Server: 127.0.0.1:5050" in err
    assert "Last error:" in err
    assert "python3 bullpen.py mcp --workspace <project>" in err


def test_bullpen_client_adds_loopback_candidates_for_wildcard_host(monkeypatch):
    monkeypatch.setattr(
        mcp_tools.socketio,
        "Client",
        lambda **_kwargs: _DummySio(),
    )

    client = mcp_tools.BullpenClient("0.0.0.0", 5050)
    urls = client._candidate_urls()

    assert "http://0.0.0.0:5050" in urls
    assert "http://127.0.0.1:5050" in urls
    assert "http://localhost:5050" in urls


def test_bullpen_client_create_ticket_times_out_when_ack_missing(monkeypatch):
    monkeypatch.setattr(
        mcp_tools.socketio,
        "Client",
        lambda **_kwargs: _NeverAckSio(),
    )

    client = mcp_tools.BullpenClient("127.0.0.1", 5050)
    client.workspace_id = "fake-ws"
    client.operation_timeout_seconds = 0.01
    task, err = client.create_ticket({"title": "x"})

    assert task is None
    assert err == "Timed out waiting for task:create response"


def test_bullpen_client_disables_reconnect_and_prefers_websocket(monkeypatch, tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    mcp_auth.ensure_workspace_runtime_config(bp_dir, preferred_token="token-1")

    created_clients = []

    def fake_client(**kwargs):
        created_clients.append(kwargs)
        return _RecordingSio()

    monkeypatch.setattr(mcp_tools.socketio, "Client", fake_client)

    client = mcp_tools.BullpenClient("127.0.0.1", 5050, bp_dir=bp_dir)

    assert created_clients == [{
        "logger": False,
        "engineio_logger": False,
        "reconnection": False,
    }]
    assert client._connect_best_effort() is True
    assert client.sio.connect_calls == [(
        ("http://127.0.0.1:5050",),
        {
            "wait_timeout": client.connect_timeout_seconds,
            "auth": {"mcp_token": "token-1"},
            "transports": ["websocket"],
        },
    )]


def test_bullpen_client_falls_back_to_polling_when_websocket_unavailable(monkeypatch, tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)

    monkeypatch.setattr(
        mcp_tools.socketio,
        "Client",
        lambda **_kwargs: _FallbackRecordingSio(),
    )

    client = mcp_tools.BullpenClient("127.0.0.1", 5050, bp_dir=bp_dir)

    assert client._connect_best_effort() is True
    assert client.sio.connect_calls == [
        (
            ("http://127.0.0.1:5050",),
            {
                "wait_timeout": client.connect_timeout_seconds,
                "auth": None,
                "transports": ["websocket"],
            },
        ),
        (
            ("http://127.0.0.1:5050",),
            {
                "wait_timeout": client.connect_timeout_seconds,
                "auth": None,
                "transports": ["polling"],
            },
        ),
    ]


def test_bullpen_client_selects_workspace_matching_bp_dir(monkeypatch, tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)

    monkeypatch.setattr(
        mcp_tools.socketio,
        "Client",
        lambda **_kwargs: _MultiWorkspaceSio(tmp_workspace),
    )

    client = mcp_tools.BullpenClient("127.0.0.1", 5050, bp_dir=bp_dir)

    assert client._connect_best_effort() is True
    assert client.workspace_id == "target-ws"


def test_bullpen_client_trusts_server_workspace_id_for_token_auth(monkeypatch, tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    mcp_auth.ensure_workspace_runtime_config(bp_dir, preferred_token="token-1")

    monkeypatch.setattr(
        mcp_tools.socketio,
        "Client",
        lambda **_kwargs: _RecordingSio(),
    )

    client = mcp_tools.BullpenClient("127.0.0.1", 5050, bp_dir=bp_dir)

    assert client._connect_best_effort() is True
    assert client.workspace_id == "ws-1"


def test_resolve_runtime_args_reads_workspace_config(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    config_path = bp_dir + "/config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    config["server_host"] = "127.0.0.1"
    config["server_port"] = 5099
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f)

    resolved_bp_dir, host, port = mcp_tools.resolve_runtime_args(workspace=tmp_workspace)

    assert resolved_bp_dir == bp_dir
    assert host == "127.0.0.1"
    assert port == 5099


def test_resolve_runtime_args_allows_explicit_host_port_override(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)

    resolved_bp_dir, host, port = mcp_tools.resolve_runtime_args(
        bp_dir=bp_dir,
        host="localhost",
        port=6060,
    )

    assert resolved_bp_dir == bp_dir
    assert host == "localhost"
    assert port == 6060


def test_resolve_runtime_args_requires_bullpen_dir(tmp_path):
    missing = tmp_path / "missing-project"

    try:
        mcp_tools.resolve_runtime_args(workspace=str(missing))
    except ValueError as exc:
        assert ".bullpen directory not found" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_read_parses_mcp_content_length_frame():
    payload = b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
    framed = b"Content-Length: " + str(len(payload)).encode("ascii") + b"\r\n\r\n" + payload

    msg = mcp_tools._read(io.BytesIO(framed))

    assert msg["method"] == "initialize"
    assert msg["id"] == 1


def test_read_returns_line_mode_for_newline_json():
    raw = b'{"jsonrpc":"2.0","id":4,"method":"tools/list","params":{}}\n'
    parsed, mode = mcp_tools._read(io.BytesIO(raw), return_mode=True)

    assert mode == "line"
    assert parsed["method"] == "tools/list"
    assert parsed["id"] == 4


def test_read_parses_mcp_frame_when_content_type_precedes_length():
    msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    parsed = mcp_tools._read(io.BytesIO(_frame(msg, content_type_first=True)))

    assert parsed["method"] == "tools/list"
    assert parsed["id"] == 2


def test_write_emits_mcp_content_length_frame():
    out = io.BytesIO()
    mcp_tools._write({"jsonrpc": "2.0", "id": 9, "result": {"ok": True}}, out_stream=out)
    raw = out.getvalue()

    header, body = raw.split(b"\r\n\r\n", 1)
    assert header.startswith(b"Content-Length:")
    declared_len = int(header.split(b":", 1)[1].strip())
    assert declared_len == len(body)
    parsed = json.loads(body.decode("utf-8"))
    assert parsed["id"] == 9
    assert parsed["result"]["ok"] is True


def test_write_emits_line_json_when_mode_line():
    out = io.BytesIO()
    mcp_tools._write({"jsonrpc": "2.0", "id": 10, "result": {"ok": True}}, out_stream=out, mode="line")
    parsed = _parse_line_messages(out.getvalue())

    assert len(parsed) == 1
    assert parsed[0]["id"] == 10
    assert parsed[0]["result"]["ok"] is True


def test_initialize_result_echoes_requested_protocol_version():
    result = mcp_tools._initialize_result("2024-11-05")

    assert result["protocolVersion"] == "2024-11-05"


def test_initialize_result_uses_default_protocol_without_request():
    result = mcp_tools._initialize_result()

    assert result["protocolVersion"] == mcp_tools.DEFAULT_PROTOCOL_VERSION


def test_main_processes_framed_initialize_tools_and_list_tasks(tmp_workspace, monkeypatch):
    bp_dir = init_workspace(tmp_workspace)
    created = create_task(bp_dir, "MCP integration test")

    req = b"".join([
        _frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}),
        _frame({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
        _frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        _frame({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_tasks", "arguments": {}},
        }, content_type_first=True),
    ])

    in_stream = io.BytesIO(req)
    out_stream = io.BytesIO()
    monkeypatch.setattr(mcp_tools, "BullpenClient", _ConnectedClient)
    monkeypatch.setattr(mcp_tools.sys, "stdin", io.TextIOWrapper(in_stream, encoding="utf-8"))
    stdout = io.TextIOWrapper(out_stream, encoding="utf-8")
    monkeypatch.setattr(mcp_tools.sys, "stdout", stdout)

    mcp_tools.main(bp_dir, "127.0.0.1", 5050)
    stdout.flush()
    responses = _parse_framed_messages(out_stream.getvalue())

    assert [r["id"] for r in responses] == [1, 2, 3]
    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    tool_names = {t["name"] for t in responses[1]["result"]["tools"]}
    assert "list_tasks" in tool_names
    assert "list_tickets_by_title" in tool_names
    summary = json.loads(responses[2]["result"]["content"][0]["text"])
    ids = {item["id"] for item in summary}
    assert created["id"] in ids


def test_main_initialize_and_tools_list_do_not_require_socket_connect(tmp_workspace, monkeypatch):
    bp_dir = init_workspace(tmp_workspace)
    req = b"".join([
        _frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        _frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
    ])

    in_stream = io.BytesIO(req)
    out_stream = io.BytesIO()
    monkeypatch.setattr(
        mcp_tools.socketio,
        "Client",
        lambda **_kwargs: _NoConnectSio(),
    )
    monkeypatch.setattr(mcp_tools.sys, "stdin", io.TextIOWrapper(in_stream, encoding="utf-8"))
    stdout = io.TextIOWrapper(out_stream, encoding="utf-8")
    monkeypatch.setattr(mcp_tools.sys, "stdout", stdout)

    mcp_tools.main(bp_dir, "127.0.0.1", 5050)
    stdout.flush()
    responses = _parse_framed_messages(out_stream.getvalue())

    assert [r["id"] for r in responses] == [1, 2]


def test_main_processes_line_json_initialize_tools_and_list_tasks(tmp_workspace, monkeypatch):
    bp_dir = init_workspace(tmp_workspace)
    created = create_task(bp_dir, "MCP line-json test")

    req = b"\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode("utf-8"),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}).encode("utf-8"),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode("utf-8"),
        json.dumps({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_tasks", "arguments": {}},
        }).encode("utf-8"),
        b"",
    ])

    in_stream = io.BytesIO(req)
    out_stream = io.BytesIO()
    monkeypatch.setattr(mcp_tools, "BullpenClient", _ConnectedClient)
    monkeypatch.setattr(mcp_tools.sys, "stdin", io.TextIOWrapper(in_stream, encoding="utf-8"))
    stdout = io.TextIOWrapper(out_stream, encoding="utf-8")
    monkeypatch.setattr(mcp_tools.sys, "stdout", stdout)

    mcp_tools.main(bp_dir, "127.0.0.1", 5050)
    stdout.flush()
    responses = _parse_line_messages(out_stream.getvalue())

    assert [r["id"] for r in responses] == [1, 2, 3]
    tool_names = {t["name"] for t in responses[1]["result"]["tools"]}
    assert "list_tasks" in tool_names
    assert "list_tickets_by_title" in tool_names
    summary = json.loads(responses[2]["result"]["content"][0]["text"])
    ids = {item["id"] for item in summary}
    assert created["id"] in ids
