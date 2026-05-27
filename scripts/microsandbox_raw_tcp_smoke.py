#!/usr/bin/env python3
"""Smoke-test a raw TCP published port through Microsandbox."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import microsandbox

from pty_controller_smoke import reserve_loopback_port


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DEFAULT = "toady-microsandbox-local"


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
        try:
            await maybe(sandbox.stop())
        except Exception:
            pass
    try:
        await maybe(microsandbox.Sandbox.remove(name))
    except Exception:
        pass


async def snapshot_path(snapshot_name: str) -> str:
    snapshot = await maybe(microsandbox.Snapshot.get(snapshot_name))
    path = getattr(snapshot, "path", None)
    if not path:
        opened = getattr(snapshot, "open", None)
        if callable(opened):
            path = getattr(await maybe(opened()), "path", None)
    if not path:
        raise RuntimeError(f"snapshot {snapshot_name!r} has no path")
    return str(path)


def wait_for_echo(port: int, timeout: float = 20.0) -> bytes:
    deadline = time.time() + timeout
    last_error: BaseException | None = None
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
                sock.settimeout(2)
                sock.sendall(b"toady-raw-smoke\n")
                return sock.recv(1024)
        except OSError as exc:
            last_error = exc
        time.sleep(0.2)
    raise TimeoutError(f"raw TCP echo did not answer on 127.0.0.1:{port}: {last_error}")


async def run(args: argparse.Namespace) -> int:
    sandbox_name = args.name
    host_port = args.port or reserve_loopback_port()

    with tempfile.TemporaryDirectory(prefix="toady-msb-raw-", dir="/private/tmp") as home:
        await stop_remove(sandbox_name)
        sandbox = await maybe(
            microsandbox.Sandbox.create(
                sandbox_name,
                snapshot=await snapshot_path(args.snapshot),
                detached=True,
                replace=True,
                cpus=1,
                memory=1024,
                ports={host_port: host_port},
                volumes={
                    "/app": microsandbox.Volume.bind(str(ROOT), readonly=True),
                    "/home/agent": microsandbox.Volume.bind(home),
                },
                network=microsandbox.Network.allow_all(),
                env={"HOME": "/home/agent", "USER": "agent", "LOGNAME": "agent"},
            )
        )
        try:
            command = (
                "set -e; "
                f"nohup python3 /app/scripts/raw_tcp_echo_server.py --port {host_port} "
                ">/tmp/toady-raw.log 2>&1 &"
            )
            await maybe(sandbox.exec("bash", ["-lc", command]))
            response = wait_for_echo(host_port, timeout=args.timeout)
            if response != b"ECHO:toady-raw-smoke\n":
                raise RuntimeError(f"unexpected raw TCP response: {response!r}")
            print(f"Microsandbox raw TCP port smoke passed on port {host_port}")
            return 0
        except BaseException:
            try:
                result = await maybe(sandbox.exec("bash", ["-lc", "cat /tmp/toady-raw.log 2>/dev/null || true"]))
                stdout = getattr(result, "stdout_text", "") or getattr(result, "stdout", "")
                stderr = getattr(result, "stderr_text", "") or getattr(result, "stderr", "")
                if stdout:
                    print("--- guest /tmp/toady-raw.log ---", file=sys.stderr)
                    print(stdout, file=sys.stderr)
                if stderr:
                    print("--- guest log stderr ---", file=sys.stderr)
                    print(stderr, file=sys.stderr)
            except Exception as log_exc:
                print(f"Could not read guest raw TCP log: {log_exc}", file=sys.stderr)
            raise
        finally:
            if args.keep:
                print(f"Keeping sandbox {sandbox_name}")
            else:
                await stop_remove(sandbox_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="toady-raw-smoke")
    parser.add_argument("--snapshot", default=os.environ.get("TOADY_MICROSANDBOX_BASE", SNAPSHOT_DEFAULT))
    parser.add_argument("--port", type=int)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
