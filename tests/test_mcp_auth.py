"""Tests for shared MCP token storage and migration."""

import json
import os

from server import mcp_auth
from server.init import init_workspace
from server.persistence import read_json, write_json


def test_ensure_runtime_config_writes_token_to_shared_secrets_and_not_config(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)

    token = mcp_auth.ensure_workspace_runtime_config(
        bp_dir,
        host="127.0.0.1",
        port=5050,
        preferred_token="token-1",
    )

    config = read_json(os.path.join(bp_dir, "config.json"))
    secrets = read_json(mcp_auth.shared_secrets_path())

    assert token == "token-1"
    assert config["server_host"] == "127.0.0.1"
    assert config["server_port"] == 5050
    assert "mcp_token" not in config
    assert secrets["projects"][os.path.realpath(tmp_workspace)]["mcp_token"] == "token-1"


def test_read_workspace_mcp_token_migrates_legacy_config_token(tmp_workspace):
    bp_dir = init_workspace(tmp_workspace)
    config_path = os.path.join(bp_dir, "config.json")
    config = read_json(config_path)
    config["mcp_token"] = "legacy-token"
    write_json(config_path, config)

    token = mcp_auth.read_workspace_mcp_token(bp_dir)

    migrated_config = read_json(config_path)
    secrets = read_json(mcp_auth.shared_secrets_path())

    assert token == "legacy-token"
    assert "mcp_token" not in migrated_config
    assert secrets["projects"][os.path.realpath(tmp_workspace)]["mcp_token"] == "legacy-token"
