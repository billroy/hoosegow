"""PTY-backed web terminal session management."""

import errno
import fcntl
import logging
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import threading
import time
import atexit
import weakref
from dataclasses import dataclass


TERMINAL_OUTPUT_CHUNK = 16 * 1024
TERMINAL_CLOSE_TIMEOUT = 2.0
_MANAGERS = weakref.WeakSet()


def _close_managers_at_exit():
    for manager in list(_MANAGERS):
        manager.close_all()


atexit.register(_close_managers_at_exit)


@dataclass
class TerminalSession:
    workspace_id: str
    terminal_id: str
    owner_sid: str
    cwd: str
    pid: int
    master_fd: int
    process: subprocess.Popen
    reader_thread: threading.Thread | None
    status: str
    created_at: float
    last_seen_at: float
    label: str


class TerminalManager:
    """Manage interactive shell sessions exposed over Socket.IO."""

    def __init__(self, socketio, *, per_workspace_limit=8, per_sid_limit=24):
        self.socketio = socketio
        self.per_workspace_limit = per_workspace_limit
        self.per_sid_limit = per_sid_limit
        self._sessions = {}
        self._lock = threading.RLock()
        _MANAGERS.add(self)

    def list_sessions(self, *, workspace_id, owner_sid):
        with self._lock:
            return [
                self._payload(session)
                for session in self._sessions.values()
                if session.workspace_id == workspace_id and session.owner_sid == owner_sid
            ]

    def create(self, *, workspace_id, terminal_id, owner_sid, cwd, cols, rows):
        with self._lock:
            key = self._key(workspace_id, terminal_id)
            if key in self._sessions:
                raise ValueError("Terminal already exists")
            ws_count = sum(
                1 for session in self._sessions.values()
                if session.workspace_id == workspace_id and session.status == "running"
            )
            sid_count = sum(
                1 for session in self._sessions.values()
                if session.owner_sid == owner_sid and session.status == "running"
            )
            if ws_count >= self.per_workspace_limit:
                raise ValueError(f"Terminal limit reached for this workspace ({self.per_workspace_limit})")
            if sid_count >= self.per_sid_limit:
                raise ValueError(f"Terminal limit reached for this browser session ({self.per_sid_limit})")

            shell = self._select_shell()
            master_fd, slave_fd = pty.openpty()
            try:
                self._resize_fd(master_fd, cols, rows)
                env = os.environ.copy()
                env.update({
                    "TERM": "xterm-256color",
                    "COLORTERM": "truecolor",
                    "BULLPEN_WORKSPACE": cwd,
                    "BULLPEN_TERMINAL": "1",
                })
                proc = subprocess.Popen(
                    [shell],
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

            now = time.time()
            session = TerminalSession(
                workspace_id=workspace_id,
                terminal_id=terminal_id,
                owner_sid=owner_sid,
                cwd=cwd,
                pid=proc.pid,
                master_fd=master_fd,
                process=proc,
                reader_thread=None,
                status="running",
                created_at=now,
                last_seen_at=now,
                label=self._next_label(workspace_id, owner_sid),
            )
            thread = threading.Thread(target=self._reader_loop, args=(key,), daemon=True)
            session.reader_thread = thread
            self._sessions[key] = session
            thread.start()
            return self._payload(session)

    def write(self, *, workspace_id, terminal_id, owner_sid, data):
        session = self._get_owned(workspace_id, terminal_id, owner_sid)
        if not session or session.status != "running":
            raise ValueError("Terminal is not running")
        encoded = data.encode("utf-8", errors="surrogatepass")
        with self._lock:
            session.last_seen_at = time.time()
        os.write(session.master_fd, encoded)

    def resize(self, *, workspace_id, terminal_id, owner_sid, cols, rows):
        session = self._get_owned(workspace_id, terminal_id, owner_sid)
        if not session:
            raise ValueError("Terminal not found")
        if session.status == "running":
            self._resize_fd(session.master_fd, cols, rows)
        with self._lock:
            session.last_seen_at = time.time()

    def close(self, *, workspace_id, terminal_id, owner_sid, emit_closed=True):
        key = self._key(workspace_id, terminal_id)
        with self._lock:
            session = self._sessions.get(key)
            if not session or session.owner_sid != owner_sid:
                return False
        self._terminate_session(key, emit_closed=emit_closed)
        return True

    def restart(self, *, workspace_id, terminal_id, owner_sid, cwd, cols, rows):
        self.close(
            workspace_id=workspace_id,
            terminal_id=terminal_id,
            owner_sid=owner_sid,
            emit_closed=False,
        )
        return self.create(
            workspace_id=workspace_id,
            terminal_id=terminal_id,
            owner_sid=owner_sid,
            cwd=cwd,
            cols=cols,
            rows=rows,
        )

    def close_for_sid(self, owner_sid):
        with self._lock:
            keys = [key for key, session in self._sessions.items() if session.owner_sid == owner_sid]
        for key in keys:
            self._terminate_session(key, emit_closed=False)

    def close_workspace(self, workspace_id):
        with self._lock:
            keys = [key for key, session in self._sessions.items() if session.workspace_id == workspace_id]
        for key in keys:
            self._terminate_session(key, emit_closed=False)

    def close_all(self):
        with self._lock:
            keys = list(self._sessions.keys())
        for key in keys:
            self._terminate_session(key, emit_closed=False)

    def _reader_loop(self, key):
        while True:
            with self._lock:
                session = self._sessions.get(key)
                if not session:
                    return
                fd = session.master_fd
                proc = session.process
                owner_sid = session.owner_sid
                workspace_id = session.workspace_id
                terminal_id = session.terminal_id
            try:
                readable, _, _ = select.select([fd], [], [], 0.2)
                if fd in readable:
                    chunk = os.read(fd, TERMINAL_OUTPUT_CHUNK)
                    if not chunk:
                        break
                    self.socketio.emit(
                        "terminal:output",
                        {
                            "workspaceId": workspace_id,
                            "terminalId": terminal_id,
                            "data": chunk.decode("utf-8", errors="replace"),
                        },
                        to=owner_sid,
                    )
            except OSError as exc:
                if exc.errno in (errno.EIO, errno.EBADF):
                    break
                logging.exception("Terminal reader failed for %s", terminal_id)
                break

            if proc.poll() is not None:
                break

        self._mark_exited(key)

    def _mark_exited(self, key):
        with self._lock:
            session = self._sessions.get(key)
            if not session:
                return
            proc = session.process
            code = proc.poll()
            session.status = "exited"
            try:
                os.close(session.master_fd)
            except OSError:
                pass
            payload = {
                "workspaceId": session.workspace_id,
                "terminalId": session.terminal_id,
                "code": code,
                "signal": None,
                "status": "exited",
            }
            owner_sid = session.owner_sid
        self.socketio.emit("terminal:exit", payload, to=owner_sid)

    def _terminate_session(self, key, *, emit_closed):
        with self._lock:
            session = self._sessions.pop(key, None)
        if not session:
            return

        proc = session.process
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            except OSError:
                try:
                    proc.terminate()
                except OSError:
                    pass
            deadline = time.time() + TERMINAL_CLOSE_TIMEOUT
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.05)
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    try:
                        proc.kill()
                    except OSError:
                        pass
        try:
            os.close(session.master_fd)
        except OSError:
            pass
        if emit_closed:
            self.socketio.emit(
                "terminal:closed",
                {"workspaceId": session.workspace_id, "terminalId": session.terminal_id},
                to=session.owner_sid,
            )

    def _get_owned(self, workspace_id, terminal_id, owner_sid):
        with self._lock:
            session = self._sessions.get(self._key(workspace_id, terminal_id))
            if not session or session.owner_sid != owner_sid:
                return None
            return session

    def _next_label(self, workspace_id, owner_sid):
        existing = [
            session.label for session in self._sessions.values()
            if session.workspace_id == workspace_id and session.owner_sid == owner_sid
        ]
        if "Terminal" not in existing:
            return "Terminal"
        index = 2
        while f"Terminal {index}" in existing:
            index += 1
        return f"Terminal {index}"

    def _payload(self, session):
        return {
            "workspaceId": session.workspace_id,
            "terminalId": session.terminal_id,
            "label": session.label,
            "status": session.status,
            "cwd": session.cwd,
            "pid": session.pid,
        }

    @staticmethod
    def _key(workspace_id, terminal_id):
        return (workspace_id, terminal_id)

    @staticmethod
    def _resize_fd(fd, cols, rows):
        packed = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)

    @staticmethod
    def _select_shell():
        candidates = []
        env_shell = os.environ.get("SHELL")
        if env_shell:
            candidates.append(env_shell)
        candidates.extend(["/bin/zsh", "/bin/bash", "/bin/sh"])
        for shell in candidates:
            if shell and os.path.isfile(shell) and os.access(shell, os.X_OK):
                return shell
        return "/bin/sh"
