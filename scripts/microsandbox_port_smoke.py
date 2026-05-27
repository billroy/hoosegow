#!/usr/bin/env python3
"""Smoke-test a Microsandbox published host port with plain HTTP."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import microsandbox


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "bullpen-microsandbox-local"


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


def reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http(port: int, timeout: float = 20.0) -> str:
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + timeout
    last_error: BaseException | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return response.read(200).decode("utf-8", errors="replace")
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise TimeoutError(f"HTTP server did not answer at {url}: {last_error}")


async def snapshot_path() -> str:
    snapshot = await maybe(microsandbox.Snapshot.get(SNAPSHOT))
    path = getattr(snapshot, "path", None)
    if not path:
        opened = getattr(snapshot, "open", None)
        if callable(opened):
            path = getattr(await maybe(opened()), "path", None)
    if not path:
        raise RuntimeError(f"snapshot {SNAPSHOT!r} has no path")
    return str(path)


async def run(args: argparse.Namespace) -> int:
    sandbox_name = args.name
    host_port = args.port or reserve_loopback_port()

    with tempfile.TemporaryDirectory(prefix="toady-msb-port-", dir="/private/tmp") as home:
        await stop_remove(sandbox_name)
        sandbox = await maybe(
            microsandbox.Sandbox.create(
                sandbox_name,
                snapshot=await snapshot_path(),
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
                "cd /app; "
                f"nohup python3 -m http.server {host_port} --bind 0.0.0.0 "
                ">/tmp/toady-http.log 2>&1 &"
            )
            await maybe(sandbox.exec("bash", ["-lc", command]))
            body = wait_for_http(host_port, timeout=args.timeout)
            if "Directory listing" not in body:
                raise RuntimeError(f"unexpected HTTP response body prefix: {body[:80]!r}")
            print(f"Microsandbox published HTTP port smoke passed on port {host_port}")
            return 0
        except BaseException:
            try:
                result = await maybe(sandbox.exec("bash", ["-lc", "cat /tmp/toady-http.log 2>/dev/null || true"]))
                stdout = getattr(result, "stdout_text", "") or getattr(result, "stdout", "")
                stderr = getattr(result, "stderr_text", "") or getattr(result, "stderr", "")
                if stdout:
                    print("--- guest /tmp/toady-http.log ---", file=sys.stderr)
                    print(stdout, file=sys.stderr)
                if stderr:
                    print("--- guest log stderr ---", file=sys.stderr)
                    print(stderr, file=sys.stderr)
            except Exception as log_exc:
                print(f"Could not read guest HTTP log: {log_exc}", file=sys.stderr)
            raise
        finally:
            if args.keep:
                print(f"Keeping sandbox {sandbox_name}")
            else:
                await stop_remove(sandbox_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="toady-port-smoke")
    parser.add_argument("--port", type=int)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
