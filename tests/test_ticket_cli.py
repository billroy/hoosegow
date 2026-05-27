"""Tests for the server-backed ticket CLI used by shell agents."""

import json
import os

import bullpen
from server.init import init_workspace
from server.tasks import create_task


class RecordingTicketClient:
    def __init__(self):
        self.created = None
        self.updated = None
        self.disconnected = False

    def create_ticket(self, payload):
        self.created = payload
        return {
            "id": "created-123",
            "title": payload["title"],
            "status": payload.get("status", "inbox"),
            "type": payload["type"],
            "priority": payload["priority"],
        }, None

    def update_ticket(self, payload):
        self.updated = payload
        return {
            "id": payload["id"],
            "title": payload.get("title", "Existing"),
            "status": payload.get("status", "review"),
            "type": payload.get("type", "task"),
            "priority": payload.get("priority", "normal"),
        }, None

    def disconnect(self):
        self.disconnected = True


def test_ticket_cli_create_uses_socket_client(tmp_workspace, monkeypatch, capsys):
    init_workspace(tmp_workspace)
    client = RecordingTicketClient()
    monkeypatch.setattr(bullpen, "_ticket_client", lambda *_args: client)

    args = bullpen.parse_args([
        "ticket", "--workspace", tmp_workspace,
        "create",
        "--title", "CLI create",
        "--description", "Created through server path",
        "--status", "review",
        "--tag", "codex",
    ])

    assert bullpen.run_ticket_cli(args) == 0

    assert client.created == {
        "title": "CLI create",
        "description": "Created through server path",
        "type": "task",
        "priority": "normal",
        "tags": ["codex"],
        "status": "review",
    }
    assert client.disconnected is True
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == "created-123"
    assert out["status"] == "review"


def test_ticket_cli_create_reads_description_file(tmp_workspace, tmp_path, monkeypatch, capsys):
    init_workspace(tmp_workspace)
    description_path = tmp_path / "description.md"
    description_path.write_text("## Notes\n\nCreated from a file.\n", encoding="utf-8")
    client = RecordingTicketClient()
    monkeypatch.setattr(bullpen, "_ticket_client", lambda *_args: client)

    args = bullpen.parse_args([
        "ticket", "--workspace", tmp_workspace,
        "create",
        "--title", "CLI create from file",
        "--description-file", str(description_path),
        "--status", "review",
    ])

    assert bullpen.run_ticket_cli(args) == 0

    assert client.created == {
        "title": "CLI create from file",
        "description": "## Notes\n\nCreated from a file.\n",
        "type": "task",
        "priority": "normal",
        "tags": [],
        "status": "review",
    }
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == "created-123"


def test_ticket_cli_update_reads_body_file(tmp_workspace, tmp_path, monkeypatch, capsys):
    init_workspace(tmp_workspace)
    body_path = tmp_path / "body.md"
    body_path.write_text("## Analysis\n\nUse the live server.\n", encoding="utf-8")
    client = RecordingTicketClient()
    monkeypatch.setattr(bullpen, "_ticket_client", lambda *_args: client)

    args = bullpen.parse_args([
        "ticket", "--workspace", tmp_workspace,
        "update",
        "--id", "ticket-1",
        "--status", "review",
        "--body-file", str(body_path),
    ])

    assert bullpen.run_ticket_cli(args) == 0

    assert client.updated == {
        "id": "ticket-1",
        "status": "review",
        "body": "## Analysis\n\nUse the live server.\n",
    }
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == "ticket-1"
    assert out["status"] == "review"


def test_ticket_cli_list_reads_without_socket(tmp_workspace, monkeypatch, capsys):
    bp_dir = init_workspace(tmp_workspace)
    created = create_task(bp_dir, "List me", status="review")

    def fail_client(*_args):
        raise AssertionError("list must not open a Socket.IO client")

    monkeypatch.setattr(bullpen, "_ticket_client", fail_client)

    args = bullpen.parse_args([
        "ticket", "--workspace", tmp_workspace,
        "list",
        "--status", "review",
    ])

    assert bullpen.run_ticket_cli(args) == 0

    tickets = json.loads(capsys.readouterr().out)
    assert [ticket["id"] for ticket in tickets] == [created["id"]]


def test_ticket_cli_rejects_missing_bullpen_directory(tmp_path, capsys):
    missing_workspace = os.fspath(tmp_path / "not-initialized")
    os.makedirs(missing_workspace)

    args = bullpen.parse_args([
        "ticket", "--workspace", missing_workspace,
        "create",
        "--title", "Nope",
    ])

    assert bullpen.run_ticket_cli(args) == 1

    err = capsys.readouterr().err
    assert ".bullpen directory not found" in err
