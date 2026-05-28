"""Tiny command-line wrapper around `msb exec` for sandbox agent passthrough."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Sequence


EXIT_TIMEOUT = 124
EXIT_WRAPPER_ERROR = 125
EXIT_SPAWN_ERROR = 126
EXIT_NOT_FOUND = 127


class PassthroughUsageError(ValueError):
    """User-facing wrapper usage error."""


@dataclass(frozen=True)
class PassthroughConfig:
    sandbox: str
    workspace: str = "/workspace"
    user: str = "agent"
    tty: bool | None = None
    timeout: str | None = None
    env: tuple[str, ...] = ()
    msb: str = "msb"
    dry_run: bool = False
    verbose: bool = False
    command: tuple[str, ...] = ()


def split_wrapper_and_command(argv: Sequence[str]) -> tuple[list[str], list[str], bool]:
    args = list(argv)
    if "--" not in args:
        return args, [], False
    separator = args.index("--")
    return args[:separator], args[separator + 1:], True


def parse_args(argv: Sequence[str] | None = None) -> PassthroughConfig:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    wrapper_argv, command, had_separator = split_wrapper_and_command(raw_argv)
    parser = argparse.ArgumentParser(
        prog="toady-msb",
        description="Run a command inside an existing Microsandbox sandbox via msb exec.",
    )
    parser.add_argument("--sandbox", required=True, help="Microsandbox sandbox name")
    parser.add_argument(
        "--workspace",
        default="/workspace",
        help="Guest workspace/working directory passed to msb exec -w (default: /workspace)",
    )
    parser.add_argument("--user", default="agent", help="Guest user passed to msb exec -u (default: agent)")
    tty_group = parser.add_mutually_exclusive_group()
    tty_group.add_argument("--tty", action="store_true", help="Force msb exec -t")
    tty_group.add_argument("--no-tty", action="store_true", help="Force no PTY")
    parser.add_argument("--timeout", help="Forward timeout duration to msb exec --timeout")
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="Forward environment variable")
    parser.add_argument("--msb", default="msb", help="Microsandbox CLI path (default: msb)")
    parser.add_argument("--dry-run", action="store_true", help="Print generated msb argv and exit")
    parser.add_argument("--verbose", action="store_true", help="Print wrapper diagnostics to stderr")
    namespace = parser.parse_args(wrapper_argv)

    if not namespace.sandbox.strip():
        raise PassthroughUsageError("--sandbox cannot be blank")
    if not namespace.workspace.strip():
        raise PassthroughUsageError("--workspace cannot be blank")
    if not namespace.user.strip():
        raise PassthroughUsageError("--user cannot be blank")
    for item in namespace.env:
        if "=" not in item or item.startswith("="):
            raise PassthroughUsageError("--env values must use KEY=VALUE")
    if not command and had_separator:
        raise PassthroughUsageError("missing command after --")
    if command and not had_separator:
        raise PassthroughUsageError("command argv must follow --")

    tty: bool | None
    if namespace.tty:
        tty = True
    elif namespace.no_tty:
        tty = False
    else:
        tty = None

    return PassthroughConfig(
        sandbox=namespace.sandbox,
        workspace=namespace.workspace,
        user=namespace.user,
        tty=tty,
        timeout=namespace.timeout,
        env=tuple(namespace.env),
        msb=namespace.msb,
        dry_run=bool(namespace.dry_run),
        verbose=bool(namespace.verbose),
        command=tuple(command),
    )


def should_allocate_tty(config: PassthroughConfig) -> bool:
    if config.tty is not None:
        return config.tty
    return sys.stdin.isatty() and sys.stdout.isatty()


def build_msb_argv(config: PassthroughConfig) -> list[str]:
    allocate_tty = should_allocate_tty(config)
    if not config.command and not allocate_tty:
        raise PassthroughUsageError("missing command; pass a command after -- or use --tty to attach a shell")

    argv = [
        config.msb,
        "exec",
        config.sandbox,
        "-u",
        config.user,
        "-w",
        config.workspace,
    ]
    for item in config.env:
        argv.extend(["-e", item])
    if config.timeout:
        argv.extend(["--timeout", config.timeout])
    if allocate_tty:
        argv.append("-t")
    if config.command:
        argv.append("--")
        argv.extend(config.command)
    return argv


def shell_join(argv: Sequence[str]) -> str:
    import shlex

    return " ".join(shlex.quote(part) for part in argv)


def run(config: PassthroughConfig) -> int:
    argv = build_msb_argv(config)
    if config.dry_run:
        print(shell_join(argv))
        return 0
    if config.verbose:
        print(f"toady-msb: running {shell_join(argv)}", file=sys.stderr)
    try:
        completed = subprocess.run(argv, check=False)
    except FileNotFoundError:
        print(f"Error: Microsandbox CLI not found: {config.msb}", file=sys.stderr)
        return EXIT_NOT_FOUND
    except PermissionError as exc:
        print(f"Error: cannot execute Microsandbox CLI {config.msb}: {exc}", file=sys.stderr)
        return EXIT_SPAWN_ERROR
    except KeyboardInterrupt:
        return 130
    return int(completed.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        config = parse_args(argv)
        return run(config)
    except PassthroughUsageError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_WRAPPER_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
