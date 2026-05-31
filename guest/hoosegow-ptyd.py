#!/usr/bin/env python3
"""Tiny PTY controller proof for Hoosegow sandboxes.

The daemon listens on a Unix domain socket or loopback TCP socket and speaks
newline-delimited JSON. Binary PTY payloads are base64 encoded so the protocol
stays easy to inspect and bridge through the host Hoosegow server.
"""

from __future__ import annotations

import argparse
import base64
import errno
import fcntl
import http.server
import json
import os
import selectors
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any


READ_CHUNK = 16 * 1024
TERMINATE_GRACE_SECONDS = 2.0


def _json_line(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    cols = max(1, int(cols or 80))
    rows = max(1, int(rows or 24))
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


@dataclass(eq=False)
class Client:
    conn: socket.socket
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    subscriptions: set[str] = field(default_factory=set)

    def send(self, payload: dict[str, Any]) -> None:
        with self.send_lock:
            self.conn.sendall(_json_line(payload))


@dataclass
class PtySession:
    id: str
    pid: int
    fd: int
    process: subprocess.Popen[bytes]
    cwd: str
    shell: str
    created_at: float
    status: str = "running"
    exit_code: int | None = None
    subscribers: set[Client] = field(default_factory=set)
    seq: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)


class PtyController:
    def __init__(
        self,
        *,
        socket_path: str | None = None,
        tcp_host: str | None = None,
        tcp_port: int | None = None,
        http_host: str | None = None,
        http_port: int | None = None,
        token: str | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.http_host = http_host
        self.http_port = http_port
        self.token = token
        self.sessions: dict[str, PtySession] = {}
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.server: Any | None = None

    def serve(self) -> None:
        if self.http_host:
            self._serve_http()
            return
        if self.socket_path:
            parent = os.path.dirname(self.socket_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            try:
                os.unlink(self.socket_path)
            except FileNotFoundError:
                pass
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(self.socket_path)
            os.chmod(self.socket_path, 0o600)
            listen_label = self.socket_path
        else:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.tcp_host or "127.0.0.1", int(self.tcp_port or 0)))
            host, port = server.getsockname()
            listen_label = f"{host}:{port}"
        self.server = server
        server.listen(32)
        server.settimeout(0.2)
        print(f"hoosegow-ptyd listening on {listen_label}", file=sys.stderr, flush=True)

        try:
            while not self.stopping.is_set():
                try:
                    conn, _addr = server.accept()
                except TimeoutError:
                    continue
                except OSError as exc:
                    if self.stopping.is_set() or exc.errno in (errno.EBADF, errno.EINVAL):
                        break
                    raise
                thread = threading.Thread(target=self._client_loop, args=(conn,), daemon=True)
                thread.start()
        finally:
            self.shutdown()

    def _serve_http(self) -> None:
        controller = self

        class Handler(http.server.BaseHTTPRequestHandler):
            server_version = "hoosegow-ptyd/0.1"

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/health":
                    self._send_json({"ok": True})
                    return
                if parsed.path != "/events":
                    self.send_error(404)
                    return
                query = urllib.parse.parse_qs(parsed.query)
                token = query.get("token", [""])[0]
                if controller.token and token != controller.token:
                    self._send_json({"event": "error", "error": "unauthorized"}, status=401)
                    return
                session_id = query.get("id", [""])[0]
                since = int(query.get("since", ["0"])[0] or 0)
                timeout = float(query.get("timeout", ["5"])[0] or 5)
                try:
                    self._send_json(controller.poll_events(session_id, since=since, timeout=timeout))
                except Exception as exc:
                    self._send_json({"event": "error", "error": str(exc)}, status=400)

            def do_POST(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/rpc":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("content-length") or "0")
                    message = json.loads(self.rfile.read(length).decode("utf-8"))
                    if controller.token and message.get("token") != controller.token:
                        self._send_json({"event": "error", "error": "unauthorized"}, status=401)
                        return
                    response = controller.dispatch(message, client=None)
                    self._send_json(response or {"event": "ok"})
                except Exception as exc:
                    self._send_json({"event": "error", "error": str(exc)}, status=400)

            def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = http.server.ThreadingHTTPServer((self.http_host, int(self.http_port or 0)), Handler)
        server.timeout = 0.2
        self.server = server
        host, port = server.server_address
        print(f"hoosegow-ptyd HTTP listening on {host}:{port}", file=sys.stderr, flush=True)
        try:
            while not self.stopping.is_set():
                server.handle_request()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self.stopping.set()
        if self.server is not None:
            try:
                close = getattr(self.server, "close", None)
                if callable(close):
                    close()
                else:
                    server_close = getattr(self.server, "server_close", None)
                    if callable(server_close):
                        server_close()
            except OSError:
                pass
        with self.lock:
            session_ids = list(self.sessions)
        for session_id in session_ids:
            self._close_session(session_id, notify=False)
        try:
            if self.socket_path:
                os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

    def _client_loop(self, conn: socket.socket) -> None:
        client = Client(conn)
        file = conn.makefile("rb")
        try:
            print("client connected", file=sys.stderr, flush=True)
            while not self.stopping.is_set():
                raw = file.readline()
                if not raw:
                    break
                try:
                    message = json.loads(raw.decode("utf-8"))
                    print(f"client op {message.get('op')!r}", file=sys.stderr, flush=True)
                    if self.token and message.get("token") != self.token:
                        client.send({"event": "error", "error": "unauthorized"})
                        continue
                    response = self.dispatch(message, client=client)
                    if response is not None:
                        client.send(response)
                except Exception as exc:  # keep controller alive on bad client input
                    print(f"client error: {exc}", file=sys.stderr, flush=True)
                    client.send({"event": "error", "error": str(exc)})
        finally:
            print("client disconnected", file=sys.stderr, flush=True)
            self._drop_client(client)
            try:
                file.close()
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass

    def dispatch(self, message: dict[str, Any], *, client: Client | None) -> dict[str, Any] | None:
        op = message.get("op")
        if op == "open":
            return self._open(message, client=client)
        elif op == "join":
            return self._join(str(message.get("id") or ""), client=client)
        elif op == "write":
            self._write(str(message.get("id") or ""), str(message.get("data") or ""))
            return None
        elif op == "resize":
            self._resize(str(message.get("id") or ""), int(message.get("cols") or 80), int(message.get("rows") or 24))
            return None
        elif op == "status":
            return self._status(str(message.get("id") or ""))
        elif op == "close":
            self._close_session(str(message.get("id") or ""), notify=True)
            return None
        elif op == "shutdown":
            response = {"event": "shutdown"}
            self.shutdown()
            return response
        else:
            raise ValueError(f"unknown op: {op!r}")

    def _open(self, message: dict[str, Any], *, client: Client | None) -> dict[str, Any]:
        session_id = str(message.get("id") or uuid.uuid4().hex)
        cwd = os.path.abspath(os.path.expanduser(str(message.get("cwd") or os.getcwd())))
        shell = str(message.get("shell") or os.environ.get("SHELL") or "/bin/bash")
        cols = int(message.get("cols") or 80)
        rows = int(message.get("rows") or 24)
        if not os.path.isdir(cwd):
            raise ValueError(f"cwd is not a directory: {cwd}")
        if not os.path.exists(shell):
            raise ValueError(f"shell does not exist: {shell}")

        with self.lock:
            if session_id in self.sessions:
                raise ValueError(f"session already exists: {session_id}")

        master_fd, slave_fd = os.openpty()
        try:
            _set_winsize(master_fd, cols, rows)
            env = os.environ.copy()
            env.setdefault("TERM", "xterm-256color")
            env.setdefault("COLORTERM", "truecolor")
            proc = subprocess.Popen(
                [shell, "-l"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass

        session = PtySession(
            id=session_id,
            pid=proc.pid,
            fd=master_fd,
            process=proc,
            cwd=cwd,
            shell=shell,
            created_at=time.time(),
        )
        with self.lock:
            if client is not None:
                session.subscribers.add(client)
                client.subscriptions.add(session_id)
            self.sessions[session_id] = session
        threading.Thread(target=self._reader_loop, args=(session_id,), daemon=True).start()
        return {"event": "opened", "id": session_id, "pid": proc.pid, "cwd": cwd, "shell": shell}

    def _join(self, session_id: str, *, client: Client | None) -> dict[str, Any]:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                raise ValueError(f"session not found: {session_id}")
            if client is not None:
                session.subscribers.add(client)
                client.subscriptions.add(session_id)
            payload = self._status_payload(session)
        return payload

    def _write(self, session_id: str, data_b64: str) -> None:
        data = base64.b64decode(data_b64.encode("ascii"))
        with self.lock:
            session = self.sessions.get(session_id)
            if not session or session.status != "running":
                raise ValueError(f"session is not running: {session_id}")
            fd = session.fd
        os.write(fd, data)

    def _resize(self, session_id: str, cols: int, rows: int) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                raise ValueError(f"session not found: {session_id}")
            fd = session.fd
        _set_winsize(fd, cols, rows)
        self._broadcast(session_id, {"event": "resized", "id": session_id, "cols": cols, "rows": rows})

    def _status(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                raise ValueError(f"session not found: {session_id}")
            payload = self._status_payload(session)
        return payload

    def _status_payload(self, session: PtySession) -> dict[str, Any]:
        return {
            "event": "status",
            "id": session.id,
            "pid": session.pid,
            "cwd": session.cwd,
            "shell": session.shell,
            "status": session.status,
            "exit_code": session.exit_code,
            "foreground": self._foreground_status(session),
        }

    def _foreground_status(self, session: PtySession) -> dict[str, Any]:
        if session.status != "running":
            return {"supported": True, "busy": False}
        try:
            foreground_pgrp = os.tcgetpgrp(session.fd)
            shell_pgrp = os.getpgid(session.pid)
        except OSError as exc:
            return {"supported": False, "busy": False, "error": str(exc)}
        busy = foreground_pgrp != shell_pgrp
        foreground_pid, command = self._process_group_label(foreground_pgrp, shell_pid=session.pid)
        return {
            "supported": True,
            "busy": busy,
            "pgrp": foreground_pgrp,
            "pid": foreground_pid,
            "command": command,
        }

    def _process_group_label(self, pgrp: int, *, shell_pid: int) -> tuple[int | None, str | None]:
        candidates: list[int] = []
        proc_dir = "/proc"
        if os.path.isdir(proc_dir):
            for entry in os.listdir(proc_dir):
                if not entry.isdigit():
                    continue
                pid = int(entry)
                try:
                    if os.getpgid(pid) == pgrp:
                        candidates.append(pid)
                except OSError:
                    continue
        target = None
        for pid in sorted(candidates):
            if pid != shell_pid:
                target = pid
                break
        if target is None and candidates:
            target = sorted(candidates)[0]
        if target is None:
            return None, None
        return target, self._process_label(target)

    def _process_label(self, pid: int) -> str | None:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                raw = handle.read(4096).replace(b"\x00", b" ").strip()
            if raw:
                return raw.decode("utf-8", errors="replace")
        except OSError:
            pass
        try:
            with open(f"/proc/{pid}/comm", "r", encoding="utf-8", errors="replace") as handle:
                label = handle.read().strip()
            return label or None
        except OSError:
            return None

    def _reader_loop(self, session_id: str) -> None:
        while not self.stopping.is_set():
            with self.lock:
                session = self.sessions.get(session_id)
                if not session or session.status != "running":
                    return
                fd = session.fd
            try:
                selector = selectors.DefaultSelector()
                selector.register(fd, selectors.EVENT_READ)
                events = selector.select(timeout=0.2)
                selector.close()
                if not events:
                    if self._child_exited(session_id):
                        return
                    continue
                chunk = os.read(fd, READ_CHUNK)
                if not chunk:
                    break
                self._broadcast(
                    session_id,
                    {
                        "event": "output",
                        "id": session_id,
                        "data": base64.b64encode(chunk).decode("ascii"),
                    },
                )
            except OSError as exc:
                if exc.errno in (errno.EIO, errno.EBADF):
                    break
                self._broadcast(session_id, {"event": "error", "id": session_id, "error": str(exc)})
                break
        self._mark_exited(session_id)

    def _child_exited(self, session_id: str) -> bool:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session or session.status != "running":
                return True
            proc = session.process
        code = proc.poll()
        if code is None:
            return False
        self._mark_exited(session_id, exit_code=code)
        return True

    def _mark_exited(self, session_id: str, exit_code: int | None = None) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session or session.status == "exited":
                return
            if exit_code is None:
                exit_code = session.process.poll()
                if exit_code is None:
                    try:
                        exit_code = session.process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        exit_code = session.process.poll()
                if exit_code is None:
                    exit_code = 0
            session.status = "exited"
            session.exit_code = exit_code
            try:
                os.close(session.fd)
            except OSError:
                pass
            payload = {"event": "exit", "id": session_id, "exit_code": session.exit_code}
        self._broadcast(session_id, payload)

    def _close_session(self, session_id: str, *, notify: bool) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                return
            pid = session.pid
        try:
            os.killpg(pid, signal.SIGHUP)
        except OSError:
            pass
        deadline = time.time() + TERMINATE_GRACE_SECONDS
        while time.time() < deadline:
            if self._child_exited(session_id):
                break
            time.sleep(0.05)
        with self.lock:
            session = self.sessions.get(session_id)
            still_running = bool(session and session.status == "running")
        if still_running:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
            self._mark_exited(session_id)
        if notify:
            self._broadcast(session_id, {"event": "closed", "id": session_id})

    def _broadcast(self, session_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if session:
                session.seq += 1
                payload = dict(payload)
                payload["seq"] = session.seq
                session.history.append(payload)
                if len(session.history) > 10000:
                    del session.history[: len(session.history) - 10000]
            subscribers = list(session.subscribers) if session else []
        for client in subscribers:
            try:
                client.send(payload)
            except OSError:
                self._drop_client(client)

    def poll_events(self, session_id: str, *, since: int, timeout: float) -> dict[str, Any]:
        deadline = time.time() + min(max(timeout, 0.0), 30.0)
        while True:
            with self.lock:
                session = self.sessions.get(session_id)
                if not session:
                    raise ValueError(f"session not found: {session_id}")
                events = [event for event in session.history if int(event.get("seq") or 0) > since]
                next_seq = session.seq
                status = session.status
                if events or status != "running" or time.time() >= deadline:
                    return {"events": events, "next_seq": next_seq, "status": status}
            time.sleep(0.05)

    def _drop_client(self, client: Client) -> None:
        with self.lock:
            for session_id in list(client.subscriptions):
                session = self.sessions.get(session_id)
                if session:
                    session.subscribers.discard(client)
            client.subscriptions.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description="Hoosegow in-sandbox PTY controller")
    listen = parser.add_mutually_exclusive_group(required=True)
    listen.add_argument("--socket", help="Unix socket path")
    listen.add_argument("--tcp", help="Loopback TCP listener as HOST:PORT")
    listen.add_argument("--http", help="HTTP listener as HOST:PORT")
    parser.add_argument("--token", help="Optional shared secret required on each message")
    args = parser.parse_args()

    tcp_host = None
    tcp_port = None
    if args.tcp:
        tcp_host, raw_port = args.tcp.rsplit(":", 1)
        tcp_port = int(raw_port)
    http_host = None
    http_port = None
    if args.http:
        http_host, raw_port = args.http.rsplit(":", 1)
        http_port = int(raw_port)

    controller = PtyController(
        socket_path=args.socket,
        tcp_host=tcp_host,
        tcp_port=tcp_port,
        http_host=http_host,
        http_port=http_port,
        token=args.token,
    )

    def _stop(_signum: int, _frame: Any) -> None:
        controller.shutdown()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    controller.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
