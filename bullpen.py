#!/usr/bin/env python3
"""Bullpen — AI agent team manager."""

import argparse
import os
import sys


LOCALHOST_BINDS = {"127.0.0.1", "localhost", "::1"}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="bullpen",
        description="Bullpen — manage a team of AI coding agents",
    )
    subparsers = parser.add_subparsers(dest="command")
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Run the Bullpen MCP stdio server for an external MCP client",
        description=(
            "Run the Bullpen MCP stdio server for a project. Writes still require "
            "a running Bullpen server so validation, locking, UI updates, and "
            "workspace routing remain consistent."
        ),
    )
    mcp_parser.add_argument(
        "--workspace",
        dest="mcp_workspace",
        default=None,
        help="Path to the workspace directory (default: current directory)",
    )
    mcp_parser.add_argument(
        "--bp-dir",
        dest="mcp_bp_dir",
        help="Path to .bullpen directory (default: --workspace/.bullpen)",
    )
    mcp_parser.add_argument(
        "--host",
        dest="mcp_host",
        help="Bullpen Socket.IO host (default: read from .bullpen/config.json)",
    )
    mcp_parser.add_argument(
        "--port",
        dest="mcp_port",
        type=int,
        help="Bullpen Socket.IO port (default: read from .bullpen/config.json)",
    )
    mcp_token_parser = subparsers.add_parser(
        "mcp-token",
        help="Manage workspace-scoped MCP authentication tokens",
        description=(
            "Inspect or rotate the MCP token stored in a workspace's "
            ".bullpen/config.json runtime config."
        ),
    )
    mcp_token_parser.add_argument(
        "--workspace",
        dest="mcp_token_workspace",
        default=None,
        help="Path to the workspace directory (default: current directory)",
    )
    mcp_token_parser.add_argument(
        "--bp-dir",
        dest="mcp_token_bp_dir",
        help="Path to .bullpen directory (default: --workspace/.bullpen)",
    )
    mcp_token_subparsers = mcp_token_parser.add_subparsers(dest="mcp_token_action", required=True)
    mcp_token_subparsers.add_parser("rotate", help="Rotate the workspace MCP token")
    ticket_parser = subparsers.add_parser(
        "ticket",
        help="Manage Bullpen tickets through the running server",
        description=(
            "Create, update, and list tickets from shell-based agents. Writes use "
            "the same Socket.IO path as the MCP tools so browser clients receive "
            "live board updates. Place --workspace/--bp-dir before the ticket "
            "action, for example: bullpen ticket --workspace /project create ..."
        ),
    )
    ticket_parser.add_argument(
        "--workspace",
        dest="ticket_workspace",
        default=None,
        help="Path to the workspace directory (default: current directory)",
    )
    ticket_parser.add_argument(
        "--bp-dir",
        dest="ticket_bp_dir",
        help="Path to .bullpen directory (default: --workspace/.bullpen)",
    )
    ticket_parser.add_argument(
        "--host",
        dest="ticket_host",
        help="Bullpen Socket.IO host (default: read from .bullpen/config.json)",
    )
    ticket_parser.add_argument(
        "--port",
        dest="ticket_port",
        type=int,
        help="Bullpen Socket.IO port (default: read from .bullpen/config.json)",
    )
    ticket_subparsers = ticket_parser.add_subparsers(dest="ticket_action", required=True)

    ticket_create = ticket_subparsers.add_parser("create", help="Create a ticket")
    ticket_create.add_argument("--title", required=True, help="Ticket title")
    ticket_create.add_argument("--description", help="Markdown description")
    ticket_create.add_argument("--description-file", help="Read markdown description from file")
    ticket_create.add_argument("--type", default="task", choices=["task", "bug", "feature", "chore"])
    ticket_create.add_argument("--priority", default="normal", choices=["low", "normal", "high", "urgent"])
    ticket_create.add_argument("--status", help="Initial ticket status")
    ticket_create.add_argument("--tag", action="append", default=[], dest="tags", help="Ticket tag; repeatable")

    ticket_update = ticket_subparsers.add_parser("update", help="Update a ticket")
    ticket_update.add_argument("--id", required=True, help="Ticket id")
    ticket_update.add_argument("--title", help="New ticket title")
    ticket_update.add_argument("--body", help="Full markdown body")
    ticket_update.add_argument("--body-file", help="Read full markdown body from file")
    ticket_update.add_argument("--type", choices=["task", "bug", "feature", "chore"])
    ticket_update.add_argument("--priority", choices=["low", "normal", "high", "urgent"])
    ticket_update.add_argument("--status", help="New ticket status")
    ticket_update.add_argument("--tag", action="append", dest="tags", help="Replace tags; repeatable")

    ticket_list = ticket_subparsers.add_parser("list", help="List tickets")
    ticket_list.add_argument("--status", help="Optional status filter")

    model_catalog_parser = subparsers.add_parser(
        "model-catalog",
        help="Validate provider model candidates through Bullpen adapters",
        description=(
            "Probe model identifiers through the same CLI adapter path Bullpen uses "
            "for workers and Live Agent chat. This is intended as a host-side "
            "diagnostic before changing provider dropdown catalogs."
        ),
    )
    model_catalog_parser.add_argument(
        "--workspace",
        dest="model_catalog_workspace",
        default=None,
        help="Path to the workspace directory (default: current directory)",
    )
    model_catalog_parser.add_argument(
        "--bp-dir",
        dest="model_catalog_bp_dir",
        help="Optional .bullpen directory for MCP-enabled adapter runs",
    )
    model_catalog_subparsers = model_catalog_parser.add_subparsers(
        dest="model_catalog_action",
        required=True,
    )
    model_catalog_validate = model_catalog_subparsers.add_parser(
        "validate",
        help="Run a tiny prompt against provider/model candidates",
    )
    model_catalog_validate.add_argument(
        "--provider",
        action="append",
        dest="model_catalog_providers",
        help="Provider to validate; repeatable. Defaults to all built-in providers.",
    )
    model_catalog_validate.add_argument(
        "--model",
        action="append",
        dest="model_catalog_models",
        help="Model candidate to validate; repeatable. Applies to each selected provider.",
    )
    model_catalog_validate.add_argument(
        "--timeout",
        type=float,
        default=45,
        dest="model_catalog_timeout",
        help="Seconds to wait per model probe (default: 45)",
    )
    model_catalog_validate.add_argument(
        "--prompt",
        default=None,
        dest="model_catalog_prompt",
        help="Probe prompt (default: 'Reply with exactly: OK')",
    )
    model_catalog_validate.add_argument(
        "--api-catalog",
        action=argparse.BooleanOptionalAction,
        default=False,
        dest="model_catalog_api_catalog",
        help="Also fetch provider API model lists when matching API credentials are set",
    )
    model_catalog_validate.add_argument(
        "--output",
        choices=["json", "text"],
        default="json",
        dest="model_catalog_output",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--workspace",
        default=os.getcwd(),
        help="Path to the workspace directory (default: current directory)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 5000)),
        help="Port to serve on (default: $PORT or 5000)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open a browser on startup",
    )
    parser.add_argument(
        "--start-without-project",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--websocket-debug",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Enable Socket.IO / Engine.IO websocket activity logging "
            "(default: disabled)"
        ),
    )
    parser.add_argument(
        "--set-password",
        nargs="?",
        action="append",
        metavar="USERNAME",
        const="",
        help=(
            "Interactively set/update login password(s). "
            "Repeat to set multiple users, e.g. "
            "`--set-password admin --set-password alice`. "
            "If passed without a value, prompts for username. "
            "Writes hashed credentials to the global .env file and exits "
            "without starting the server."
        ),
    )
    parser.add_argument(
        "--delete-user",
        action="append",
        metavar="USERNAME",
        help=(
            "Delete a user from configured login credentials. "
            "Repeat to delete multiple users. "
            "Can be combined with --set-password."
        ),
    )
    parser.add_argument(
        "--bootstrap-credentials",
        action="store_true",
        help=(
            "Create login credentials from BULLPEN_BOOTSTRAP_USER (default: "
            "'admin') and BULLPEN_BOOTSTRAP_PASSWORD env vars, then exit. "
            "No-op if credentials already exist unless BULLPEN_BOOTSTRAP_FORCE=1. "
            "For headless/scripted deploys."
        ),
    )
    args = parser.parse_args(argv)
    if args.command == "mcp":
        args.workspace = args.mcp_workspace or args.workspace
        args.bp_dir = args.mcp_bp_dir
        args.host = args.mcp_host
        args.port = args.mcp_port
    elif args.command == "mcp-token":
        args.workspace = args.mcp_token_workspace or args.workspace
        args.bp_dir = args.mcp_token_bp_dir
    elif args.command == "ticket":
        args.workspace = args.ticket_workspace or args.workspace
        args.bp_dir = args.ticket_bp_dir
        args.host = args.ticket_host
        args.port = args.ticket_port
    elif args.command == "model-catalog":
        args.workspace = args.model_catalog_workspace or args.workspace
        args.bp_dir = args.model_catalog_bp_dir
    return args


def run_mcp_cli(args):
    """Run the MCP stdio server using workspace-oriented CLI arguments."""
    from server import mcp_tools

    try:
        bp_dir, host, port = mcp_tools.resolve_runtime_args(
            bp_dir=args.bp_dir,
            workspace=args.workspace,
            host=args.host,
            port=args.port,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    mcp_tools.main(bp_dir, host, port)
    return 0


def run_mcp_token_cli(args):
    """Rotate workspace-scoped MCP tokens from the shell."""
    import json

    from server import mcp_auth, mcp_tools

    try:
        bp_dir, host, port = mcp_tools.resolve_runtime_args(
            bp_dir=args.bp_dir,
            workspace=args.workspace,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.mcp_token_action != "rotate":
        print(f"Error: unknown mcp-token action {args.mcp_token_action}", file=sys.stderr)
        return 1

    mcp_auth.rotate_workspace_mcp_token(bp_dir, host=host, port=port)
    print(json.dumps({"ok": True, "workspace": os.path.abspath(args.workspace), "bp_dir": bp_dir}, indent=2))
    return 0


def _read_cli_text(value, file_path, field_name):
    """Read optional CLI text from either a flag value or a file."""
    if value is not None and file_path:
        raise ValueError(f"--{field_name} and --{field_name}-file are mutually exclusive")
    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    return value


def _ticket_client(host, port, bp_dir):
    from server.mcp_tools import BullpenClient
    return BullpenClient(host, port, bp_dir=bp_dir)


def _ticket_summary(ticket):
    return {
        "id": ticket.get("id"),
        "title": ticket.get("title"),
        "status": ticket.get("status"),
        "type": ticket.get("type"),
        "priority": ticket.get("priority"),
    }


def run_ticket_cli(args):
    """Run server-backed ticket operations for shell-based agent sessions."""
    import json

    from server import tasks as task_store
    from server import mcp_tools

    try:
        bp_dir, host, port = mcp_tools.resolve_runtime_args(
            bp_dir=args.bp_dir,
            workspace=args.workspace,
            host=args.host,
            port=args.port,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.ticket_action == "list":
        tickets = task_store.list_tasks(bp_dir)
        if args.status:
            tickets = [ticket for ticket in tickets if ticket.get("status") == args.status]
        print(json.dumps([_ticket_summary(ticket) for ticket in tickets], indent=2))
        return 0

    try:
        description = _read_cli_text(
            getattr(args, "description", None),
            getattr(args, "description_file", None),
            "description",
        )
        body = _read_cli_text(
            getattr(args, "body", None),
            getattr(args, "body_file", None),
            "body",
        )
    except (OSError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    client = _ticket_client(host, port, bp_dir)
    try:
        if args.ticket_action == "create":
            payload = {
                "title": args.title,
                "description": description or "",
                "type": args.type,
                "priority": args.priority,
                "tags": args.tags or [],
            }
            if args.status:
                payload["status"] = args.status
            ticket, err = client.create_ticket(payload)
        elif args.ticket_action == "update":
            payload = {"id": args.id}
            for key in ("title", "type", "priority", "status"):
                value = getattr(args, key, None)
                if value is not None:
                    payload[key] = value
            if body is not None:
                payload["body"] = body
            if args.tags is not None:
                payload["tags"] = args.tags
            ticket, err = client.update_ticket(payload)
        else:
            print(f"Error: unknown ticket action {args.ticket_action}", file=sys.stderr)
            return 1
    finally:
        client.disconnect()

    if err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    print(json.dumps(_ticket_summary(ticket or {}), indent=2))
    return 0


def run_model_catalog_cli(args):
    """Run host-side model catalog validation probes."""
    import json

    from server.model_catalog_validator import (
        DEFAULT_PROBE_PROMPT,
        text_summary,
        validate_model_catalog,
    )

    if args.model_catalog_action != "validate":
        print(f"Error: unknown model-catalog action {args.model_catalog_action}", file=sys.stderr)
        return 1

    workspace = os.path.abspath(args.workspace)
    if not os.path.isdir(workspace):
        print(f"Error: workspace directory does not exist: {workspace}", file=sys.stderr)
        return 1

    report = validate_model_catalog(
        providers=args.model_catalog_providers,
        models=args.model_catalog_models,
        workspace=workspace,
        bp_dir=args.bp_dir,
        timeout_seconds=args.model_catalog_timeout,
        prompt=args.model_catalog_prompt or DEFAULT_PROBE_PROMPT,
        include_api_catalog=args.model_catalog_api_catalog,
    )
    if args.model_catalog_output == "text":
        print(text_summary(report))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def set_password_cli(set_usernames=None, delete_usernames=None):
    """Prompt for username/password updates, write hashed credentials to the
    global .env file. Never echoes the password. Never accepts the
    password via a CLI flag (shell history leakage)."""
    import getpass

    from server import auth
    from server.workspace_manager import GLOBAL_DIR

    os.makedirs(GLOBAL_DIR, exist_ok=True)
    path = auth.env_path(GLOBAL_DIR)

    existing = auth.parse_env_file(path)
    users = auth.parse_credentials_mapping(existing)
    set_usernames = list(set_usernames or [])
    delete_usernames = list(delete_usernames or [])

    print(f"Updating Bullpen login credentials in {path}")

    for requested_username in set_usernames:
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

    for raw_username in delete_usernames:
        username = (raw_username or "").strip()
        if not username:
            print("Error: --delete-user requires a username.", file=sys.stderr)
            return 1
        if username in users:
            users.pop(username, None)
            print(f"Deleted user '{username}'.")
        else:
            print(f"User '{username}' not found; no change.", file=sys.stderr)

    updated = auth.apply_credentials_mapping(existing, users)
    auth.write_env_file(path, updated)
    if users:
        print(f"Credentials written to {path} (mode 600). {len(users)} user(s) configured.")
    else:
        print(f"Credentials written to {path} (mode 600). No users configured.")
    print("Restart Bullpen to apply.")
    return 0


def bootstrap_credentials():
    """Create credentials from env vars for headless deploys.

    Reads BULLPEN_BOOTSTRAP_USER (default: 'admin') and
    BULLPEN_BOOTSTRAP_PASSWORD.  Writes hashed credentials and exits.
    No-op if credentials already exist unless BULLPEN_BOOTSTRAP_FORCE=1
    (idempotent restarts by default).
    """
    from server import auth
    from server.workspace_manager import GLOBAL_DIR

    os.makedirs(GLOBAL_DIR, exist_ok=True)
    path = auth.env_path(GLOBAL_DIR)

    existing = auth.parse_env_file(path)
    users = auth.parse_credentials_mapping(existing)
    force = os.environ.get("BULLPEN_BOOTSTRAP_FORCE", "").strip().lower() in {
        "1", "true", "yes", "y", "on"
    }
    if users and not force:
        print(f"Credentials already exist ({len(users)} user(s)); skipping bootstrap.")
        return 0

    password = os.environ.get("BULLPEN_BOOTSTRAP_PASSWORD", "")
    if not password:
        print("Error: BULLPEN_BOOTSTRAP_PASSWORD not set.", file=sys.stderr)
        return 1

    username = os.environ.get("BULLPEN_BOOTSTRAP_USER", "admin").strip()
    if not username:
        username = "admin"

    users[username] = auth.generate_password_hash(password)
    updated = auth.apply_credentials_mapping(existing, users)
    auth.write_env_file(path, updated)
    action = "Updated" if force else "Bootstrapped"
    print(f"{action} credentials for '{username}' in {path}")
    return 0


def require_auth_for_network_bind(host):
    """Require auth when binding beyond localhost."""
    if host in LOCALHOST_BINDS:
        return

    from server import auth
    from server.workspace_manager import GLOBAL_DIR

    auth.load_credentials(GLOBAL_DIR)
    if auth.auth_enabled():
        return

    raise RuntimeError(
        f"refusing to bind to '{host}' without authentication enabled; "
        "run `python3 bullpen.py --set-password` first"
    )


def main():
    args = parse_args()

    if args.command == "mcp":
        sys.exit(run_mcp_cli(args))
    if args.command == "mcp-token":
        sys.exit(run_mcp_token_cli(args))
    if args.command == "ticket":
        sys.exit(run_ticket_cli(args))
    if args.command == "model-catalog":
        sys.exit(run_model_catalog_cli(args))

    if args.bootstrap_credentials:
        sys.exit(bootstrap_credentials())

    if args.set_password is not None or args.delete_user:
        sys.exit(set_password_cli(args.set_password or [], args.delete_user or []))

    workspace = os.path.abspath(args.workspace)

    if not os.path.isdir(workspace):
        print(f"Error: workspace directory does not exist: {workspace}", file=sys.stderr)
        sys.exit(1)

    try:
        require_auth_for_network_bind(args.host)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    import pyfiglet

    print(pyfiglet.figlet_format("bullpen"), end="")
    print(f"Bullpen starting — workspace: {workspace}, host: {args.host}, port: {args.port}")

    from server.app import create_app, socketio

    app = create_app(
        workspace,
        no_browser=args.no_browser,
        host=args.host,
        port=args.port,
        websocket_debug=args.websocket_debug,
        start_without_project=args.start_without_project,
    )

    if not args.no_browser:
        import webbrowser
        import threading
        browse_host = "localhost" if args.host == "0.0.0.0" else args.host
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{browse_host}:{args.port}")).start()

    socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
