"""Tests for web terminal validation and Socket.IO events."""

import os
import time

import pytest

from server.app import create_app, socketio
from server.validation import (
    ValidationError,
    validate_terminal_id,
    validate_terminal_input,
    validate_terminal_size,
)


def _events(client, name, timeout=2.0):
    deadline = time.time() + timeout
    found = []
    while time.time() < deadline:
        found.extend(evt for evt in client.get_received() if evt["name"] == name)
        if found:
            return found
        time.sleep(0.05)
    return found


def test_terminal_validation_accepts_uuid_and_size():
    assert validate_terminal_id({"terminalId": "123e4567-e89b-12d3-a456-426614174000"})
    assert validate_terminal_size({"cols": 120, "rows": 32}) == (120, 32)
    assert validate_terminal_input({"data": "echo ok\n"}) == "echo ok\n"


@pytest.mark.parametrize("payload", [
    {"terminalId": "../bad"},
    {"terminalId": ""},
    {"terminalId": "x" * 101},
])
def test_terminal_validation_rejects_bad_ids(payload):
    with pytest.raises(ValidationError):
        validate_terminal_id(payload)


def test_terminal_create_runs_in_workspace(tmp_workspace):
    if not hasattr(os, "openpty"):
        pytest.skip("PTY support is required")

    app = create_app(tmp_workspace, no_browser=True)
    client = socketio.test_client(app)
    client.get_received()

    terminal_id = "term-test-1"
    client.emit("terminal:create", {"terminalId": terminal_id, "cols": 80, "rows": 24})
    created = _events(client, "terminal:created")
    assert created
    assert os.path.realpath(created[0]["args"][0]["cwd"]) == os.path.realpath(tmp_workspace)

    client.emit("terminal:input", {
        "terminalId": terminal_id,
        "data": "pwd\nexit\n",
    })
    output = []
    deadline = time.time() + 3.0
    while time.time() < deadline:
        for evt in client.get_received():
            if evt["name"] == "terminal:output":
                output.append(evt["args"][0]["data"])
            if evt["name"] == "terminal:exit":
                app.config["terminal_manager"].close_all()
                output_text = "".join(output)
                assert os.path.realpath(tmp_workspace) in output_text or tmp_workspace in output_text
                return
        time.sleep(0.05)

    app.config["terminal_manager"].close_all()
    assert False, "terminal did not exit"


def test_terminal_close_emits_closed(tmp_workspace):
    if not hasattr(os, "openpty"):
        pytest.skip("PTY support is required")

    app = create_app(tmp_workspace, no_browser=True)
    client = socketio.test_client(app)
    client.get_received()

    terminal_id = "term-test-close"
    client.emit("terminal:create", {"terminalId": terminal_id, "cols": 80, "rows": 24})
    assert _events(client, "terminal:created")
    client.emit("terminal:close", {"terminalId": terminal_id})
    assert _events(client, "terminal:closed")
