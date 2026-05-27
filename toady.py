#!/usr/bin/env python3
"""Toady — PTY-first sandbox runner for coding agents."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


LOCALHOST_BINDS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_HOME = "~/.toady"
DEFAULT_PORT = 5858


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="toady",
        description="Toady — run coding agents inside Microsandbox terminals",
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("TOADY_PORT", os.environ.get("PORT", DEFAULT_PORT))))
    parser.add_argument("--host", default=os.environ.get("TOADY_HOST", "127.0.0.1"))
    parser.add_argument("--home", default=os.environ.get("TOADY_HOME", DEFAULT_HOME), help="State directory (default: ~/.toady)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser on startup")
    parser.add_argument(
        "--workspace",
        default=os.getcwd(),
        help=argparse.SUPPRESS,  # temporary copied-Bullpen bootstrap input
    )
    parser.add_argument(
        "--workspace-root",
        action="append",
        default=[],
        help="Browse root for future sandbox workspace picker; repeatable",
    )
    parser.add_argument("--prepare-base", action="store_true", help="Build the Toady Microsandbox base image and exit")
    parser.add_argument("--rebuild-base", action="store_true", help="Force a Toady Microsandbox base rebuild")
    parser.add_argument("--base-image", default="node:22-bookworm")
    parser.add_argument("--vcpus", type=int, default=4)
    parser.add_argument("--memory-mib", type=int, default=4096)
    parser.add_argument("--max-sandboxes", type=int, default=8)
    parser.add_argument("--max-total-vcpus", type=int)
    parser.add_argument("--max-total-memory-mib", type=int)
    parser.add_argument("--terminal-limit", type=int, default=32)
    parser.add_argument("--port-pool", default="3000-3099")
    parser.add_argument("--host-nofile", type=int, default=12000)
    parser.add_argument("--guest-nofile", type=int, default=65536)
    parser.add_argument("--network-max-connections", type=int, default=8192)
    parser.add_argument("--shutdown-sandboxes-on-exit", action="store_true")
    parser.add_argument(
        "--websocket-debug",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable Socket.IO / Engine.IO websocket activity logging",
    )
    parser.add_argument(
        "--set-password",
        nargs="?",
        action="append",
        metavar="USERNAME",
        const="",
        help="Interactively set/update login password(s), then exit",
    )
    parser.add_argument(
        "--delete-user",
        action="append",
        metavar="USERNAME",
        help="Delete configured login user(s), then exit",
    )
    parser.add_argument(
        "--bootstrap-credentials",
        action="store_true",
        help="Create login credentials from TOADY_BOOTSTRAP_USER and TOADY_BOOTSTRAP_PASSWORD, then exit",
    )
    return parser.parse_args(argv)


def configure_environment(args: argparse.Namespace) -> str:
    home = os.path.abspath(os.path.expanduser(args.home))
    os.environ["TOADY_HOME"] = home
    if args.workspace_root:
        os.environ["TOADY_WORKSPACE_ROOTS"] = os.pathsep.join(
            os.path.abspath(os.path.expanduser(path)) for path in args.workspace_root
        )
    os.environ["TOADY_PORT_POOL"] = args.port_pool
    os.environ["TOADY_TERMINAL_LIMIT"] = str(max(1, int(args.terminal_limit or 32)))
    os.environ["TOADY_MAX_SANDBOXES"] = str(max(1, int(args.max_sandboxes or 8)))
    if args.max_total_vcpus:
        os.environ["TOADY_MAX_TOTAL_VCPUS"] = str(max(1, int(args.max_total_vcpus)))
    else:
        os.environ.pop("TOADY_MAX_TOTAL_VCPUS", None)
    if args.max_total_memory_mib:
        os.environ["TOADY_MAX_TOTAL_MEMORY_MIB"] = str(max(1, int(args.max_total_memory_mib)))
    else:
        os.environ.pop("TOADY_MAX_TOTAL_MEMORY_MIB", None)
    return home


def set_password_cli(home: str, set_usernames: list[str] | None = None, delete_usernames: list[str] | None = None) -> int:
    import getpass

    from server import auth

    os.makedirs(home, exist_ok=True)
    path = auth.env_path(home)
    existing = auth.parse_env_file(path)
    users = auth.parse_credentials_mapping(existing)

    print(f"Updating Toady login credentials in {path}")
    for requested_username in list(set_usernames or []):
        username = (requested_username or "").strip()
        if not username:
            try:
                username = input("Username: ").strip()
            except EOFError:
                print("Aborted.", file=sys.stderr)
                return 1
        if not username:
            print("Error: username cannot be blank.", file=sys.stderr)
            return 1

        try:
            password = getpass.getpass(f"Password for {username}: ")
            confirm = getpass.getpass(f"Confirm password for {username}: ")
        except EOFError:
            print("Aborted.", file=sys.stderr)
            return 1
        if not password:
            print("Error: password cannot be blank.", file=sys.stderr)
            return 1
        if password != confirm:
            print("Error: passwords did not match.", file=sys.stderr)
            return 1

        users[username] = auth.generate_password_hash(password)
        print(f"Updated password for user '{username}'.")

    for raw_username in list(delete_usernames or []):
        username = (raw_username or "").strip()
        if not username:
            print("Error: --delete-user requires a username.", file=sys.stderr)
            return 1
        if username in users:
            users.pop(username, None)
            print(f"Deleted user '{username}'.")
        else:
            print(f"User '{username}' not found; no change.")

    updated = auth.apply_credentials_mapping(existing, users)
    auth.write_env_file(path, updated)
    print(f"Credentials written to {path} (mode 600). {len(users)} user(s) configured.")
    print("Restart Toady to apply.")
    return 0


def bootstrap_credentials(home: str) -> int:
    from server import auth

    os.makedirs(home, exist_ok=True)
    path = auth.env_path(home)
    existing = auth.parse_env_file(path)
    users = auth.parse_credentials_mapping(existing)
    force = os.environ.get("TOADY_BOOTSTRAP_FORCE", "").strip().lower() in {"1", "true", "yes", "y", "on"}
    if users and not force:
        print(f"Credentials already exist ({len(users)} user(s)); skipping bootstrap.")
        return 0

    password = os.environ.get("TOADY_BOOTSTRAP_PASSWORD", "")
    if not password:
        print("Error: TOADY_BOOTSTRAP_PASSWORD not set.", file=sys.stderr)
        return 1
    username = os.environ.get("TOADY_BOOTSTRAP_USER", "admin").strip() or "admin"

    users[username] = auth.generate_password_hash(password)
    updated = auth.apply_credentials_mapping(existing, users)
    auth.write_env_file(path, updated)
    action = "Updated" if force else "Bootstrapped"
    print(f"{action} credentials for '{username}' in {path}")
    return 0


def require_auth_for_network_bind(host: str, home: str) -> None:
    if host in LOCALHOST_BINDS:
        return

    from server import auth

    auth.load_credentials(home)
    if auth.auth_enabled():
        return

    raise RuntimeError(
        f"refusing to bind to '{host}' without authentication enabled; "
        "run `python3 toady.py --set-password` first"
    )


def run_server(args: argparse.Namespace, home: str) -> int:
    workspace = os.path.abspath(args.workspace)
    if not os.path.isdir(workspace):
        print(f"Error: workspace directory does not exist: {workspace}", file=sys.stderr)
        return 1

    try:
        require_auth_for_network_bind(args.host, home)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    import pyfiglet

    print(pyfiglet.figlet_format("toady"), end="")
    print(f"Toady starting — home: {home}, bootstrap workspace: {workspace}, host: {args.host}, port: {args.port}")

    from server.app import create_app, socketio

    app = create_app(
        workspace,
        no_browser=args.no_browser,
        host=args.host,
        port=args.port,
        websocket_debug=args.websocket_debug,
        start_without_project=True,
        terminal_limit=args.terminal_limit,
    )

    if not args.no_browser:
        import threading
        import webbrowser

        browse_host = "localhost" if args.host == "0.0.0.0" else args.host
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{browse_host}:{args.port}")).start()

    socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)
    return 0


async def run_prepare_base(args: argparse.Namespace, home: str) -> int:
    from server.base import prepare_base
    from server.microsandbox_runtime import (
        BASE_DEFAULT,
        MicrosandboxRuntime,
        ToadyRuntimeError,
        ToadySandboxSpec,
        detect_supported_host,
    )

    if not detect_supported_host():
        print("Error: Microsandbox requires Apple Silicon macOS or Linux with KVM enabled.", file=sys.stderr)
        return 1

    root = Path(__file__).resolve().parent
    spec = ToadySandboxSpec(
        sandbox_name="toady-base-prepare",
        workspace=Path(args.workspace).resolve(),
        source_root=root,
        sandbox_home=Path(home) / "base" / "home",
        base=BASE_DEFAULT,
        vcpus=args.vcpus,
        memory_mib=args.memory_mib,
        host_nofile=args.host_nofile,
        guest_nofile=args.guest_nofile,
        network_max_connections=args.network_max_connections,
    )
    try:
        runtime = MicrosandboxRuntime()
        await runtime.ensure_installed()
        await prepare_base(runtime, spec, source_image=args.base_image, source=root, force=True)
    except ToadyRuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    home = configure_environment(args)

    if args.bootstrap_credentials:
        return bootstrap_credentials(home)
    if args.set_password is not None or args.delete_user:
        return set_password_cli(home, args.set_password or [], args.delete_user or [])
    if args.prepare_base or args.rebuild_base:
        return asyncio.run(run_prepare_base(args, home))
    return run_server(args, home)


if __name__ == "__main__":
    raise SystemExit(main())
