#!/usr/bin/env python3
"""Smoke-test toady-ptyd over its HTTP control/event API."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from pty_controller_smoke import reserve_loopback_port


ROOT = Path(__file__).resolve().parents[1]
PTYD = ROOT / "guest" / "toady-ptyd.py"
TOKEN = "smoke-token"


class HttpPtyClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.seq: dict[str, int] = {}

    def rpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        payload["token"] = self.token
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/rpc",
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            message = json.loads(response.read().decode("utf-8"))
        if message.get("event") == "error":
            raise RuntimeError(f"controller error: {message}")
        return message

    def poll(self, session_id: str, timeout: float = 5.0) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode(
            {
                "id": session_id,
                "since": self.seq.get(session_id, 0),
                "timeout": timeout,
                "token": self.token,
            }
        )
        with urllib.request.urlopen(f"{self.base_url}/events?{query}", timeout=timeout + 2) as response:
            message = json.loads(response.read().decode("utf-8"))
        if message.get("event") == "error":
            raise RuntimeError(f"controller error: {message}")
        next_seq = int(message.get("next_seq") or self.seq.get(session_id, 0))
        for event in message.get("events", []):
            next_seq = max(next_seq, int(event.get("seq") or 0))
        self.seq[session_id] = next_seq
        return list(message.get("events", []))

    def wait_for(self, session_id: str, event_name: str, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for event in self.poll(session_id, timeout=max(0.1, min(1.0, deadline - time.time()))):
                if event.get("event") == event_name:
                    return event
                if event.get("event") == "error":
                    raise RuntimeError(f"controller error: {event}")
        raise TimeoutError(f"timed out waiting for {event_name}")

    def wait_for_output_containing(self, session_id: str, needle: bytes, timeout: float = 5.0) -> bytes:
        deadline = time.time() + timeout
        chunks: list[bytes] = []
        while time.time() < deadline:
            for event in self.poll(session_id, timeout=max(0.1, min(1.0, deadline - time.time()))):
                if event.get("event") == "output":
                    chunks.append(base64.b64decode(event.get("data", "")))
                    combined = b"".join(chunks)
                    if needle in combined:
                        return combined
                elif event.get("event") == "error":
                    raise RuntimeError(f"controller error: {event}")
        combined = b"".join(chunks)
        raise TimeoutError(f"timed out waiting for output {needle!r}; got {combined!r}")

    def wait_for_status(self, session_id: str, status: str, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            payload = self.rpc({"op": "status", "id": session_id})
            if payload.get("status") == status:
                return payload
            time.sleep(0.1)
        raise TimeoutError(f"timed out waiting for status {status!r}")


def wait_for_health(base_url: str, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    last_error: BaseException | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.05)
    raise TimeoutError(f"HTTP controller did not answer at {base_url}/health: {last_error}")


def exercise_http_controller(base_url: str, token: str, cwd: str, *, verbose: bool = False) -> None:
    client = HttpPtyClient(base_url, token)
    opened = client.rpc(
        {
            "op": "open",
            "id": "http-smoke",
            "cwd": cwd,
            "shell": "/bin/bash",
            "cols": 100,
            "rows": 30,
        }
    )
    if opened.get("event") != "opened":
        raise RuntimeError(f"unexpected opened payload: {opened}")
    banner = client.wait_for_output_containing("http-smoke", b"codex login", timeout=10)
    if b"gh auth login" not in banner:
        raise RuntimeError(f"missing auth banner content: {banner!r}")
    client.rpc({"op": "resize", "id": "http-smoke", "cols": 90, "rows": 28})
    command = b"printf 'TOADY_HTTP_PTYD_SMOKE:%s\\n' \"$PWD\"; exit 11\n"
    client.rpc({"op": "write", "id": "http-smoke", "data": base64.b64encode(command).decode("ascii")})
    expected_output = f"TOADY_HTTP_PTYD_SMOKE:{cwd}".encode()
    output = client.wait_for_output_containing("http-smoke", expected_output, timeout=10)
    status = client.wait_for_status("http-smoke", "exited", timeout=10)
    if status.get("exit_code") != 11:
        raise RuntimeError(f"unexpected status payload: {status}")
    if status.get("status") != "exited":
        raise RuntimeError(f"unexpected status payload: {status}")
    second = client.rpc(
        {
            "op": "open",
            "id": "http-smoke-second",
            "cwd": cwd,
            "shell": "/bin/bash",
            "cols": 100,
            "rows": 30,
        }
    )
    if second.get("event") != "opened":
        raise RuntimeError(f"unexpected second opened payload: {second}")
    second_events = client.poll("http-smoke-second", timeout=0.5)
    second_output = b"".join(
        base64.b64decode(event.get("data", ""))
        for event in second_events
        if event.get("event") == "output"
    )
    if b"codex login" in second_output:
        raise RuntimeError("auth banner repeated in second terminal")
    client.rpc({"op": "close", "id": "http-smoke-second"})
    if verbose:
        print(output.decode("utf-8", errors="replace"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    port = reserve_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="toady-ptyd-home-") as home:
        env = os.environ.copy()
        env["HOME"] = home
        proc = subprocess.Popen(
            [sys.executable, str(PTYD), "--http", f"127.0.0.1:{port}", "--token", TOKEN],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            wait_for_health(base_url)
            exercise_http_controller(base_url, TOKEN, str(ROOT), verbose=args.verbose)
            print("PTY controller HTTP smoke passed")
            return 0
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
