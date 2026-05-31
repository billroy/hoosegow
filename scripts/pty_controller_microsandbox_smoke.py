#!/usr/bin/env python3
"""Run the PTY controller proof inside a throwaway Microsandbox."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import secrets
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Any

import microsandbox

from pty_controller_http_smoke import exercise_http_controller, wait_for_health
from pty_controller_smoke import reserve_loopback_port


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DEFAULT = "hoosegow-microsandbox-local"


async def maybe(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def stop_remove(name: str) -> None:
    try:
        sandbox = await maybe(microsandbox.Sandbox.get(name))
    except Exception:
        sandbox = None
    if sandbox is not None:
        failure: BaseException | None = None
        try:
            await maybe(sandbox.stop())
        except Exception:
            pass
    try:
        await maybe(microsandbox.Sandbox.remove(name))
    except Exception:
        pass


async def run(args: argparse.Namespace) -> int:
    sandbox_name = args.name
    host_port = args.port or reserve_loopback_port()
    token = secrets.token_urlsafe(24)
    base_url = f"http://127.0.0.1:{host_port}"

    snapshot_name = args.snapshot
    snapshot = await maybe(microsandbox.Snapshot.get(snapshot_name))
    snapshot_path = getattr(snapshot, "path", None)
    if not snapshot_path:
        opened = getattr(snapshot, "open", None)
        if callable(opened):
            snapshot_path = getattr(await maybe(opened()), "path", None)
    if not snapshot_path:
        raise RuntimeError(f"snapshot {snapshot_name!r} has no path")

    with tempfile.TemporaryDirectory(prefix="hoosegow-msb-home-", dir="/private/tmp") as home:
        await stop_remove(sandbox_name)
        volumes = {
            "/app": microsandbox.Volume.bind(str(ROOT), readonly=True),
            "/home/agent": microsandbox.Volume.bind(home),
        }
        network = microsandbox.Network.allow_all()
        if hasattr(network, "max_connections"):
            try:
                network.max_connections = 8192
            except Exception:
                pass
        sandbox = await maybe(
            microsandbox.Sandbox.create(
                sandbox_name,
                snapshot=snapshot_path,
                detached=True,
                replace=True,
                cpus=2,
                memory=2048,
                ports={host_port: host_port},
                volumes=volumes,
                network=network,
                env={"HOME": "/home/agent", "USER": "agent", "LOGNAME": "agent"},
            )
        )
        try:
            command = (
                "set -e; "
                "mkdir -p /var/lib/hoosegow; "
                f"nohup python3 /app/guest/hoosegow-ptyd.py --http 0.0.0.0:{host_port} --token {shlex.quote(token)} "
                ">/tmp/hoosegow-ptyd.log 2>&1 &"
            )
            result = sandbox.exec("bash", ["-lc", command])
            await maybe(result)
            wait_for_health(base_url, timeout=20)
            exercise_http_controller(base_url, token, "/app", verbose=args.verbose)
            print(f"Microsandbox PTY controller HTTP smoke passed on port {host_port}")
            return 0
        except BaseException as exc:
            failure = exc
            try:
                result = sandbox.exec("bash", ["-lc", "cat /tmp/hoosegow-ptyd.log 2>/dev/null || true"])
                result = await maybe(result)
                stdout = getattr(result, "stdout_text", "") or getattr(result, "stdout", "")
                stderr = getattr(result, "stderr_text", "") or getattr(result, "stderr", "")
                if stdout:
                    print("--- guest /tmp/hoosegow-ptyd.log ---", file=sys.stderr)
                    print(stdout, file=sys.stderr)
                if stderr:
                    print("--- guest log stderr ---", file=sys.stderr)
                    print(stderr, file=sys.stderr)
            except Exception as log_exc:
                print(f"Could not read guest controller log: {log_exc}", file=sys.stderr)
            raise
        finally:
            if args.keep:
                print(f"Keeping sandbox {sandbox_name}")
            else:
                await stop_remove(sandbox_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="hoosegow-ptyd-smoke")
    parser.add_argument("--snapshot", default=os.environ.get("HOOSEGOW_MICROSANDBOX_BASE", SNAPSHOT_DEFAULT))
    parser.add_argument("--port", type=int)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
