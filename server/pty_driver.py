"""HTTP client for the in-sandbox Toady PTY controller."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class PtyDriverError(RuntimeError):
    """User-facing PTY controller error."""


class PtyDriver:
    def __init__(self, *, base_url: str, token: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def rpc(self, op: str, **payload: Any) -> dict[str, Any]:
        message = {"op": op, "token": self.token}
        message.update(payload)
        data = json.dumps(message, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/rpc",
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        return self._read_json(request)

    def open(
        self,
        terminal_id: str,
        *,
        cwd: str = "/workspace",
        shell: str = "/bin/bash",
        cols: int = 100,
        rows: int = 30,
    ) -> dict[str, Any]:
        return self.rpc("open", id=terminal_id, cwd=cwd, shell=shell, cols=cols, rows=rows)

    def write(self, terminal_id: str, data: str) -> dict[str, Any]:
        encoded = base64.b64encode(data.encode("utf-8", errors="surrogatepass")).decode("ascii")
        return self.rpc("write", id=terminal_id, data=encoded)

    def resize(self, terminal_id: str, *, cols: int, rows: int) -> dict[str, Any]:
        return self.rpc("resize", id=terminal_id, cols=cols, rows=rows)

    def status(self, terminal_id: str) -> dict[str, Any]:
        return self.rpc("status", id=terminal_id)

    def close(self, terminal_id: str) -> dict[str, Any]:
        return self.rpc("close", id=terminal_id)

    def poll(self, terminal_id: str, *, since: int, timeout: float = 1.0) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "id": terminal_id,
                "since": int(since or 0),
                "timeout": float(timeout),
                "token": self.token,
            }
        )
        request = urllib.request.Request(f"{self.base_url}/events?{query}", method="GET")
        return self._read_json(request, timeout=max(self.timeout, timeout + 1.0))

    def _read_json(self, request: urllib.request.Request, *, timeout: float | None = None) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = {"error": exc.reason or str(exc)}
            raise PtyDriverError(str(payload.get("error") or payload.get("event") or exc)) from exc
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise PtyDriverError(str(exc)) from exc

        if isinstance(payload, dict) and payload.get("event") == "error":
            raise PtyDriverError(str(payload.get("error") or "PTY controller error"))
        if not isinstance(payload, dict):
            raise PtyDriverError("PTY controller returned an invalid response")
        return payload
