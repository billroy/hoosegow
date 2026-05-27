"""CLI startup security checks."""

import builtins
import json

import pytest

import bullpen
from server import auth


def test_require_auth_for_network_bind_rejects_wildcard_without_password(tmp_path, monkeypatch):
    monkeypatch.setattr("server.workspace_manager.GLOBAL_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="refusing to bind to '0.0.0.0'"):
        bullpen.require_auth_for_network_bind("0.0.0.0")


def test_require_auth_for_network_bind_allows_loopback_without_password(tmp_path, monkeypatch):
    monkeypatch.setattr("server.workspace_manager.GLOBAL_DIR", str(tmp_path))

    bullpen.require_auth_for_network_bind("127.0.0.1")
    bullpen.require_auth_for_network_bind("localhost")
    bullpen.require_auth_for_network_bind("::1")


def test_require_auth_for_network_bind_allows_network_bind_with_password(tmp_path, monkeypatch):
    monkeypatch.setattr("server.workspace_manager.GLOBAL_DIR", str(tmp_path))
    auth.write_env_file(
        auth.env_path(str(tmp_path)),
        {
            auth.USERNAME_KEY: "admin",
            auth.PASSWORD_HASH_KEY: auth.generate_password_hash("hunter2"),
        },
    )

    bullpen.require_auth_for_network_bind("0.0.0.0")


def test_parse_args_accepts_multiple_set_password_and_delete_user():
    args = bullpen.parse_args(
        [
            "--set-password",
            "admin",
            "--set-password",
            "alice",
            "--delete-user",
            "legacy",
        ]
    )
    assert args.set_password == ["admin", "alice"]
    assert args.delete_user == ["legacy"]


def test_parse_args_websocket_debug_defaults_false():
    args = bullpen.parse_args([])
    assert args.websocket_debug is False


def test_parse_args_supports_websocket_debug():
    args = bullpen.parse_args(["--websocket-debug"])
    assert args.websocket_debug is True


def test_parse_args_supports_mcp_subcommand():
    args = bullpen.parse_args(["mcp", "--workspace", "/tmp/project", "--host", "127.0.0.1", "--port", "5050"])

    assert args.command == "mcp"
    assert args.workspace == "/tmp/project"
    assert args.host == "127.0.0.1"
    assert args.port == 5050


def test_parse_args_supports_mcp_token_rotate_subcommand():
    args = bullpen.parse_args(["mcp-token", "--workspace", "/tmp/project", "rotate"])

    assert args.command == "mcp-token"
    assert args.workspace == "/tmp/project"
    assert args.mcp_token_action == "rotate"


def test_parse_args_mcp_preserves_global_workspace_before_subcommand():
    args = bullpen.parse_args(["--workspace", "/tmp/project", "mcp"])

    assert args.command == "mcp"
    assert args.workspace == "/tmp/project"
    assert args.host is None
    assert args.port is None


def test_run_mcp_cli_resolves_workspace_and_calls_mcp_main(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    bp_dir = workspace / ".bullpen"
    bp_dir.mkdir(parents=True)
    (bp_dir / "config.json").write_text(
        '{"server_host":"127.0.0.1","server_port":5055}\n',
        encoding="utf-8",
    )
    called = {}

    def fake_main(resolved_bp_dir, host, port):
        called["args"] = (resolved_bp_dir, host, port)

    monkeypatch.setattr("server.mcp_tools.main", fake_main)

    args = bullpen.parse_args(["mcp", "--workspace", str(workspace)])
    rc = bullpen.run_mcp_cli(args)

    assert rc == 0
    assert called["args"] == (str(bp_dir), "127.0.0.1", 5055)


def test_run_mcp_token_cli_rotates_workspace_token(tmp_path, capsys, monkeypatch):
    workspace = tmp_path / "project"
    bp_dir = workspace / ".bullpen"
    bp_dir.mkdir(parents=True)
    config_path = bp_dir / "config.json"
    config_path.write_text(
        '{"server_host":"127.0.0.1","server_port":5055}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    from server import mcp_auth
    mcp_auth.ensure_workspace_runtime_config(str(bp_dir), host="127.0.0.1", port=5055, preferred_token="token-old")

    args = bullpen.parse_args(["mcp-token", "--workspace", str(workspace), "rotate"])
    rc = bullpen.run_mcp_token_cli(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["bp_dir"] == str(bp_dir)
    updated = json.loads(config_path.read_text(encoding="utf-8"))
    assert updated["server_host"] == "127.0.0.1"
    assert updated["server_port"] == 5055
    assert "mcp_token" not in updated
    assert mcp_auth.read_workspace_mcp_token(str(bp_dir)) != "token-old"


def test_set_password_cli_add_and_delete_users(tmp_path, monkeypatch):
    monkeypatch.setattr("server.workspace_manager.GLOBAL_DIR", str(tmp_path))
    existing = auth.apply_credentials_mapping(
        {},
        {"old": auth.generate_password_hash("oldpw")},
    )
    auth.write_env_file(auth.env_path(str(tmp_path)), existing)

    prompts = iter(["newpass", "newpass"])

    def fake_getpass(_prompt):
        return next(prompts)

    monkeypatch.setattr("getpass.getpass", fake_getpass)
    monkeypatch.setattr(builtins, "input", lambda _p: "ignored")

    rc = bullpen.set_password_cli(set_usernames=["new"], delete_usernames=["old"])
    assert rc == 0

    auth.reset_auth_cache()
    auth.load_credentials(str(tmp_path))
    users = auth.get_users()
    assert "new" in users
    assert "old" not in users


def test_bootstrap_credentials_force_updates_existing_user(tmp_path, monkeypatch):
    monkeypatch.setattr("server.workspace_manager.GLOBAL_DIR", str(tmp_path))
    existing = auth.apply_credentials_mapping(
        {},
        {"admin": auth.generate_password_hash("oldpass")},
    )
    auth.write_env_file(auth.env_path(str(tmp_path)), existing)

    monkeypatch.setenv("BULLPEN_BOOTSTRAP_USER", "admin")
    monkeypatch.setenv("BULLPEN_BOOTSTRAP_PASSWORD", "newpass")
    monkeypatch.setenv("BULLPEN_BOOTSTRAP_FORCE", "1")

    rc = bullpen.bootstrap_credentials()
    assert rc == 0

    auth.reset_auth_cache()
    auth.load_credentials(str(tmp_path))
    assert auth.check_password("newpass", auth.get_password_hash("admin"))
    assert not auth.check_password("oldpass", auth.get_password_hash("admin"))
