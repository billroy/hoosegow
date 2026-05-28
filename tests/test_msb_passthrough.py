import os

import pytest

from server import msb_passthrough


def test_builds_default_msb_exec_argv_without_tty(monkeypatch):
    monkeypatch.setattr(msb_passthrough.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(msb_passthrough.sys.stdout, "isatty", lambda: False)

    config = msb_passthrough.parse_args([
        "--sandbox",
        "fred",
        "--",
        "claude",
        "-p",
        "say model slug",
    ])

    assert msb_passthrough.build_msb_argv(config) == [
        "msb",
        "exec",
        "fred",
        "-u",
        "agent",
        "-w",
        "/workspace",
        "--",
        "claude",
        "-p",
        "say model slug",
    ]


def test_builds_workspace_env_timeout_and_tty_argv():
    config = msb_passthrough.parse_args([
        "--sandbox",
        "fred",
        "--workspace",
        "/workspace/subproject",
        "--user",
        "root",
        "--tty",
        "--timeout",
        "5m",
        "--env",
        "FOO=bar",
        "--env",
        "EMPTY=",
        "--msb",
        "/opt/bin/msb",
        "--",
        "bash",
    ])

    assert msb_passthrough.build_msb_argv(config) == [
        "/opt/bin/msb",
        "exec",
        "fred",
        "-u",
        "root",
        "-w",
        "/workspace/subproject",
        "-e",
        "FOO=bar",
        "-e",
        "EMPTY=",
        "--timeout",
        "5m",
        "-t",
        "--",
        "bash",
    ]


def test_auto_tty_when_stdin_and_stdout_are_ttys(monkeypatch):
    monkeypatch.setattr(msb_passthrough.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(msb_passthrough.sys.stdout, "isatty", lambda: True)
    config = msb_passthrough.parse_args(["--sandbox", "fred"])

    assert msb_passthrough.build_msb_argv(config) == [
        "msb",
        "exec",
        "fred",
        "-u",
        "agent",
        "-w",
        "/workspace",
        "-t",
    ]


def test_no_command_without_effective_tty_is_usage_error(monkeypatch):
    monkeypatch.setattr(msb_passthrough.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(msb_passthrough.sys.stdout, "isatty", lambda: False)
    config = msb_passthrough.parse_args(["--sandbox", "fred"])

    with pytest.raises(msb_passthrough.PassthroughUsageError, match="missing command"):
        msb_passthrough.build_msb_argv(config)


def test_rejects_blank_sandbox_and_malformed_env():
    with pytest.raises(msb_passthrough.PassthroughUsageError, match="sandbox"):
        msb_passthrough.parse_args(["--sandbox", " ", "--", "echo"])
    with pytest.raises(msb_passthrough.PassthroughUsageError, match="KEY=VALUE"):
        msb_passthrough.parse_args(["--sandbox", "fred", "--env", "BAD", "--", "echo"])
    with pytest.raises(msb_passthrough.PassthroughUsageError, match="KEY=VALUE"):
        msb_passthrough.parse_args(["--sandbox", "fred", "--env", "=bad", "--", "echo"])


def test_missing_command_after_separator_is_usage_error():
    with pytest.raises(msb_passthrough.PassthroughUsageError, match="missing command after --"):
        msb_passthrough.parse_args(["--sandbox", "fred", "--"])


def test_conflicting_tty_flags_exit_from_argparse():
    with pytest.raises(SystemExit):
        msb_passthrough.parse_args(["--sandbox", "fred", "--tty", "--no-tty", "--", "echo"])


def test_dry_run_prints_quoted_argv(capsys):
    config = msb_passthrough.PassthroughConfig(
        sandbox="fred",
        tty=False,
        dry_run=True,
        command=("claude", "-p", "say model slug"),
    )

    assert msb_passthrough.run(config) == 0

    assert capsys.readouterr().out.strip() == "msb exec fred -u agent -w /workspace -- claude -p 'say model slug'"


def test_run_preserves_fake_msb_exit_code(tmp_path):
    fake_msb = tmp_path / "msb"
    fake_msb.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_MSB_ARGV\"\n"
        "printf 'remote out\\n'\n"
        "printf 'remote err\\n' >&2\n"
        "exit 7\n",
        encoding="utf-8",
    )
    fake_msb.chmod(0o755)
    argv_path = tmp_path / "argv.txt"
    config = msb_passthrough.PassthroughConfig(
        sandbox="fred",
        tty=False,
        msb=str(fake_msb),
        command=("echo", "ok"),
    )
    old_value = os.environ.get("FAKE_MSB_ARGV")
    os.environ["FAKE_MSB_ARGV"] = str(argv_path)
    try:
        assert msb_passthrough.run(config) == 7
    finally:
        if old_value is None:
            os.environ.pop("FAKE_MSB_ARGV", None)
        else:
            os.environ["FAKE_MSB_ARGV"] = old_value

    assert argv_path.read_text(encoding="utf-8").splitlines() == [
        "exec",
        "fred",
        "-u",
        "agent",
        "-w",
        "/workspace",
        "--",
        "echo",
        "ok",
    ]


def test_missing_msb_returns_127(capsys):
    config = msb_passthrough.PassthroughConfig(
        sandbox="fred",
        tty=False,
        msb="/definitely/missing/msb",
        command=("echo", "ok"),
    )

    assert msb_passthrough.run(config) == 127
    assert "Microsandbox CLI not found" in capsys.readouterr().err
