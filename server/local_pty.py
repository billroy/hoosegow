"""Local host PTY driver for Hoosegow terminals."""

from __future__ import annotations

import base64
import fcntl
import os
import pty
import selectors
import signal
import struct
import subprocess
import termios
import threading
import time
from dataclasses import dataclass, field
from typing import Any


READ_CHUNK = 16 * 1024
TERMINATE_GRACE_SECONDS = 2.0


class LocalPtyError(RuntimeError):
    """User-facing local PTY error."""


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    cols = max(1, int(cols or 80))
    rows = max(1, int(rows or 24))
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


@dataclass
class LocalPtySession:
    id: str
    process: subprocess.Popen[bytes]
    fd: int
    cwd: str
    shell: str
    created_at: float
    status: str = "running"
    exit_code: int | None = None
    seq: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    condition: threading.Condition = field(default_factory=threading.Condition)


class LocalPtyDriver:
    """Small in-process PTY controller with the same shape as PtyDriver."""

    def __init__(self, *, history_limit: int = 4096) -> None:
        self.history_limit = max(128, int(history_limit or 4096))
        self.sessions: dict[str, LocalPtySession] = {}
        self.lock = threading.RLock()

    def open(
        self,
        terminal_id: str,
        *,
        cwd: str,
        shell: str,
        cols: int = 100,
        rows: int = 30,
    ) -> dict[str, Any]:
        cwd = os.path.abspath(os.path.expanduser(cwd or os.getcwd()))
        shell = os.path.abspath(os.path.expanduser(shell or os.environ.get("SHELL") or "/bin/sh"))
        if not os.path.isdir(cwd):
            raise LocalPtyError(f"cwd is not a directory: {cwd}")
        if not os.path.exists(shell):
            raise LocalPtyError(f"shell does not exist: {shell}")

        with self.lock:
            if terminal_id in self.sessions:
                raise LocalPtyError(f"terminal already exists: {terminal_id}")

        master_fd, slave_fd = pty.openpty()
        try:
            _set_winsize(master_fd, cols, rows)
            env = os.environ.copy()
            env.setdefault("TERM", "xterm-256color")
            env.setdefault("COLORTERM", "truecolor")
            process = subprocess.Popen(
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

        session = LocalPtySession(
            id=terminal_id,
            process=process,
            fd=master_fd,
            cwd=cwd,
            shell=shell,
            created_at=time.time(),
        )
        with self.lock:
            self.sessions[terminal_id] = session
        thread = threading.Thread(target=self._read_loop, args=(terminal_id,), daemon=True)
        thread.start()
        return {
            "event": "opened",
            "id": terminal_id,
            "pid": process.pid,
            "cwd": cwd,
            "shell": shell,
            "status": "running",
        }

    def write(self, terminal_id: str, data: str) -> dict[str, Any]:
        session = self._session(terminal_id)
        if session.status != "running":
            raise LocalPtyError("terminal is not running")
        os.write(session.fd, data.encode("utf-8", errors="surrogatepass"))
        return {"event": "ok"}

    def resize(self, terminal_id: str, *, cols: int, rows: int) -> dict[str, Any]:
        session = self._session(terminal_id)
        _set_winsize(session.fd, cols, rows)
        return {"event": "ok"}

    def status(self, terminal_id: str) -> dict[str, Any]:
        session = self._session(terminal_id)
        return {
            "event": "status",
            "id": terminal_id,
            "pid": session.process.pid,
            "cwd": session.cwd,
            "status": session.status,
            "exit_code": session.exit_code,
            "foreground": {"busy": False, "command": os.path.basename(session.shell)},
        }

    def close(self, terminal_id: str) -> dict[str, Any]:
        with self.lock:
            session = self.sessions.pop(terminal_id, None)
        if not session:
            return {"event": "closed"}
        self._terminate(session)
        return {"event": "closed"}

    def close_all(self) -> None:
        with self.lock:
            terminal_ids = list(self.sessions)
        for terminal_id in terminal_ids:
            self.close(terminal_id)

    def poll(self, terminal_id: str, *, since: int, timeout: float = 1.0) -> dict[str, Any]:
        session = self._session(terminal_id)
        deadline = time.time() + max(0.0, float(timeout or 0))
        with session.condition:
            while session.seq <= since and session.status == "running":
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                session.condition.wait(timeout=remaining)
            events = [event for event in session.events if int(event.get("seq") or 0) > int(since or 0)]
            return {
                "event": "events",
                "events": events,
                "next_seq": session.seq,
            }

    def _session(self, terminal_id: str) -> LocalPtySession:
        with self.lock:
            session = self.sessions.get(terminal_id)
        if not session:
            raise LocalPtyError("Unknown local terminal session.")
        return session

    def _append_event(self, session: LocalPtySession, payload: dict[str, Any]) -> None:
        with session.condition:
            session.seq += 1
            payload["seq"] = session.seq
            session.events.append(payload)
            if len(session.events) > self.history_limit:
                del session.events[: len(session.events) - self.history_limit]
            session.condition.notify_all()

    def _read_loop(self, terminal_id: str) -> None:
        try:
            session = self._session(terminal_id)
        except LocalPtyError:
            return
        selector = selectors.DefaultSelector()
        selector.register(session.fd, selectors.EVENT_READ)
        try:
            while session.status == "running":
                for _key, _mask in selector.select(timeout=0.2):
                    try:
                        chunk = os.read(session.fd, READ_CHUNK)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        self._mark_exit(session)
                        return
                    encoded = base64.b64encode(chunk).decode("ascii")
                    self._append_event(session, {"event": "output", "data": encoded})
                if session.process.poll() is not None:
                    self._mark_exit(session)
                    return
        finally:
            try:
                selector.close()
            except OSError:
                pass

    def _mark_exit(self, session: LocalPtySession) -> None:
        returncode = session.process.poll()
        if returncode is None:
            try:
                returncode = session.process.wait(timeout=0)
            except subprocess.TimeoutExpired:
                returncode = None
        session.status = "exited"
        session.exit_code = int(returncode or 0)
        try:
            os.close(session.fd)
        except OSError:
            pass
        self._append_event(session, {"event": "exit", "exit_code": session.exit_code})

    def _terminate(self, session: LocalPtySession) -> None:
        session.status = "closed"
        try:
            os.killpg(session.process.pid, signal.SIGHUP)
        except OSError:
            pass
        try:
            session.process.wait(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(session.process.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            os.close(session.fd)
        except OSError:
            pass
        with session.condition:
            session.condition.notify_all()
