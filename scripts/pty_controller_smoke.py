#!/usr/bin/env python3
"""Smoke-test the local hoosegow-ptyd protocol over loopback TCP."""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PTYD = ROOT / "guest" / "hoosegow-ptyd.py"
TOKEN = "smoke-token"


class PtyClient:
    def __init__(self, address: tuple[str, int], token: str) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect(address)
        self.file = self.socket.makefile("rb")
        self.token = token

    def close(self) -> None:
        self.file.close()
        self.socket.close()

    def send(self, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload["token"] = self.token
        self.socket.sendall((json.dumps(payload) + "\n").encode("utf-8"))

    def recv(self, timeout: float = 5.0) -> dict[str, Any]:
        self.socket.settimeout(timeout)
        line = self.file.readline()
        if not line:
            raise RuntimeError("controller closed connection")
        return json.loads(line.decode("utf-8"))

    def wait_for(self, event: str, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self.recv(timeout=max(0.1, deadline - time.time()))
            if message.get("event") == event:
                return message
            if message.get("event") == "error":
                raise RuntimeError(f"controller error: {message}")
        raise TimeoutError(f"timed out waiting for {event}")

    def wait_for_output_containing(self, needle: bytes, timeout: float = 5.0) -> bytes:
        deadline = time.time() + timeout
        chunks: list[bytes] = []
        while time.time() < deadline:
            message = self.recv(timeout=max(0.1, deadline - time.time()))
            if message.get("event") == "output":
                chunk = base64.b64decode(message.get("data", ""))
                chunks.append(chunk)
                combined = b"".join(chunks)
                if needle in combined:
                    return combined
            elif message.get("event") == "error":
                raise RuntimeError(f"controller error: {message}")
        combined = b"".join(chunks)
        raise TimeoutError(f"timed out waiting for output {needle!r}; got {combined!r}")


def reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_tcp(address: tuple[str, int], timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.1)
            if sock.connect_ex(address) == 0:
                return
        time.sleep(0.05)
    raise TimeoutError(f"tcp listener did not appear: {address[0]}:{address[1]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="hoosegow-ptyd-smoke-", dir="/private/tmp") as home:
        port = reserve_loopback_port()
        address = ("127.0.0.1", port)
        env = os.environ.copy()
        env["HOME"] = home
        proc = subprocess.Popen(
            [sys.executable, str(PTYD), "--tcp", f"127.0.0.1:{port}", "--token", TOKEN],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            wait_for_tcp(address)
            client = PtyClient(address, TOKEN)
            try:
                client.send(
                    {
                        "op": "open",
                        "id": "smoke",
                        "cwd": str(ROOT),
                        "shell": "/bin/bash",
                        "cols": 100,
                        "rows": 30,
                    }
                )
                opened = client.wait_for("opened")
                if opened.get("id") != "smoke":
                    raise RuntimeError(f"unexpected opened payload: {opened}")

                client.send({"op": "resize", "id": "smoke", "cols": 90, "rows": 28})
                client.wait_for("resized")

                command = b"printf 'HOOSEGOW_PTYD_SMOKE:%s\\n' \"$PWD\"; exit 7\n"
                client.send(
                    {
                        "op": "write",
                        "id": "smoke",
                        "data": base64.b64encode(command).decode("ascii"),
                    }
                )
                output = client.wait_for_output_containing(b"HOOSEGOW_PTYD_SMOKE:")
                exited = client.wait_for("exit")
                if exited.get("exit_code") != 7:
                    raise RuntimeError(f"unexpected exit payload: {exited}")

                client.send({"op": "status", "id": "smoke"})
                status = client.wait_for("status")
                if status.get("status") != "exited":
                    raise RuntimeError(f"unexpected status payload: {status}")

                if args.verbose:
                    print(output.decode("utf-8", errors="replace"))
                print("PTY controller smoke passed")
                return 0
            finally:
                client.close()
        finally:
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate(timeout=2)
            if args.verbose and (stdout or stderr):
                if stdout:
                    print(stdout)
                if stderr:
                    print(stderr, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
