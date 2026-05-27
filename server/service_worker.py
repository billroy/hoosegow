"""Runtime controller for Service workers."""

from __future__ import annotations

import errno
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, build_opener, Request

from server.persistence import read_json
from server import tasks as task_mod
from server.worker_types import normalize_layout


SERVICE_LOG_DEFAULT_MAX = 5 * 1024 * 1024
SERVICE_SECRET_ENV_MARKERS = (
    "TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL", "PASSPHRASE",
)
RUNNING_STATES = {"running", "healthy", "unhealthy"}
BUSY_STATES = {"starting", "stopping"}
COMMIT_LINE_RE = re.compile(r"^\s*commit\s*:\s*([0-9a-f]{7,40})\b", re.IGNORECASE | re.MULTILINE)
PROCFILE_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*:\s*(.+?)\s*$")
ENV_VAR_REF_RE = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

_controllers = {}
_controllers_lock = threading.Lock()


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_id():
    return datetime.now(timezone.utc).strftime("manual-%Y%m%dT%H%M%S%fZ")


def _emit(socketio, event, payload, ws_id):
    if not socketio:
        return
    if isinstance(payload, dict):
        payload["workspaceId"] = ws_id
    socketio.emit(event, payload, to=ws_id)


def _is_secret_env_name(name):
    upper = str(name or "").upper()
    return any(marker in upper for marker in SERVICE_SECRET_ENV_MARKERS)


def _minimal_env(configured_env):
    if sys.platform == "win32":
        allowed = {"PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"}
        env = {
            key: value for key, value in os.environ.items()
            if key.upper() in allowed and not _is_secret_env_name(key)
        }
    else:
        env = {
            key: value for key, value in os.environ.items()
            if (key in {"PATH", "HOME", "LANG", "TZ"} or key.startswith("LC_"))
            and not _is_secret_env_name(key)
        }

    env.pop("BULLPEN_MCP_TOKEN", None)
    for item in configured_env or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        if key == "BULLPEN_MCP_TOKEN" or key.startswith("BULLPEN_"):
            raise ValueError(f"{key} cannot be configured for Service workers.")
        env[key] = str(item.get("value") or "")
    return env


def _service_port(worker):
    try:
        port = int(worker.get("port"))
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _configured_service_ports(layout, *, ignore_slot=None):
    reserved = set()
    slots = layout.get("slots", []) if isinstance(layout, dict) else []
    for slot_index, worker in enumerate(slots):
        if slot_index == ignore_slot or not isinstance(worker, dict):
            continue
        if worker.get("type") != "service":
            continue
        port = _service_port(worker)
        if port:
            reserved.add(port)
    return reserved


def _port_is_bindable(port):
    if not isinstance(port, int) or port < 1 or port > 65535:
        return False

    targets = [(socket.AF_INET, ("127.0.0.1", port))]
    if getattr(socket, "has_ipv6", False):
        targets.append((socket.AF_INET6, ("::1", port, 0, 0)))

    bound_any = False
    for family, address in targets:
        sock = None
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            if family == socket.AF_INET6:
                try:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                except (AttributeError, OSError):
                    pass
            sock.bind(address)
            bound_any = True
        except OSError as exc:
            if family == socket.AF_INET6 and exc.errno in {
                errno.EADDRNOTAVAIL,
                errno.EAFNOSUPPORT,
                errno.EPROTONOSUPPORT,
            }:
                continue
            return False
        finally:
            if sock is not None:
                sock.close()
    return bound_any


def suggest_service_port(layout, *, ignore_slot=None, start=3000, end=65535):
    start_port = max(1, int(start or 3000))
    end_port = min(65535, int(end or 65535))
    if start_port > end_port:
        return None

    reserved = _configured_service_ports(layout, ignore_slot=ignore_slot)
    for port in range(start_port, end_port + 1):
        if port in reserved:
            continue
        if _port_is_bindable(port):
            return port
    return None


def _resolve_cwd(workspace, configured_cwd):
    configured_cwd = str(configured_cwd or "").strip()
    cwd = os.path.join(workspace, configured_cwd) if configured_cwd and not os.path.isabs(configured_cwd) else (configured_cwd or workspace)
    real = os.path.realpath(cwd)
    root = os.path.realpath(workspace)
    if real != root and not real.startswith(root + os.sep):
        raise ValueError("Service worker cwd escapes the workspace.")
    if not os.path.isdir(real):
        raise ValueError("Service worker cwd does not exist.")
    return real


def _procfile_path(cwd):
    return os.path.join(cwd, "Procfile")


def _parse_procfile(path):
    if not os.path.exists(path):
        raise ValueError(f"Procfile not found in {os.path.dirname(path)}")
    entries = {}
    process_names = []
    warnings = []
    with open(path, "r", encoding="utf-8") as handle:
        for lineno, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = PROCFILE_LINE_RE.match(line)
            if not match:
                warnings.append(f"Ignoring malformed Procfile line {lineno}")
                continue
            name = match.group(1)
            command = match.group(2).strip()
            if name in entries:
                warnings.append(f"Procfile process '{name}' appears multiple times; using first entry.")
                continue
            entries[name] = command
            process_names.append(name)
    return entries, process_names, warnings


def _interpolate_env_refs(text, env, *, context_label="command", warn_on_unset=True):
    warnings = []
    warned = set()

    def _replace(match):
        if match.group(0) == "$$":
            return "$"
        name = match.group(1) or match.group(2) or ""
        value = env.get(name)
        if value is None:
            if warn_on_unset and name not in warned:
                warnings.append(f"{context_label}: ${name} is unset; substituting empty string.")
                warned.add(name)
            return ""
        return str(value)

    return ENV_VAR_REF_RE.sub(_replace, str(text or "")), warnings


def _redact_command_for_log(command, env):
    redacted = str(command or "")
    secret_values = []
    for key, value in (env or {}).items():
        if not _is_secret_env_name(key):
            continue
        value = str(value or "")
        if value:
            secret_values.append(value)
    for value in sorted(secret_values, key=len, reverse=True):
        redacted = redacted.replace(value, "••••")
    return redacted


def _service_command_source(worker):
    source = str(worker.get("command_source") or "manual").strip()
    return source if source in {"manual", "procfile"} else "manual"


def _build_service_env(worker, workspace, slot_index, order=None):
    env = _minimal_env(worker.get("env"))
    port = _service_port(worker)
    if port and "PORT" not in env:
        env["PORT"] = str(port)
    order_id = _service_order_id(order) if order is not None else ""
    env.update({
        "BULLPEN_WORKSPACE": workspace,
        "BULLPEN_SERVICE_SLOT": str(slot_index),
        "BULLPEN_SERVICE_NAME": str(worker.get("name") or ""),
        "BULLPEN_SERVICE_ORDER_ID": order_id,
    })
    env.update(_ticket_env((order or {}).get("task")))
    return env


def resolve_service_preview(worker, workspace, slot_index, order=None):
    cwd = _resolve_cwd(workspace, worker.get("cwd"))
    env = _build_service_env(worker, workspace, slot_index, order=order)
    command_source = _service_command_source(worker)
    warnings = []
    procfile_path = _procfile_path(cwd)
    process_names = []
    selected_process = None

    if command_source == "procfile":
        selected_process = str(worker.get("procfile_process") or "web").strip() or "web"
        entries, process_names, parse_warnings = _parse_procfile(procfile_path)
        warnings.extend(parse_warnings)
        if selected_process not in entries:
            raise ValueError(f"Procfile has no '{selected_process}:' process")
        raw_command = entries[selected_process]
    else:
        raw_command = str(worker.get("command") or "").strip()
        if not raw_command:
            raise ValueError("Service workers require a command.")

    resolved_command, interpolation_warnings = _interpolate_env_refs(
        raw_command,
        env,
        context_label="command",
        warn_on_unset=True,
    )
    warnings.extend(interpolation_warnings)

    pre_start = str(worker.get("pre_start") or "").strip()
    resolved_pre_start = ""
    pre_start_warnings = []
    if pre_start:
        resolved_pre_start, pre_start_warnings = _interpolate_env_refs(
            pre_start,
            env,
            context_label="pre_start",
            warn_on_unset=True,
        )
        warnings.extend(pre_start_warnings)

    health_command = str(worker.get("health_command") or "").strip()
    resolved_health_command = ""
    if health_command:
        resolved_health_command, _ = _interpolate_env_refs(
            health_command,
            env,
            context_label="health_command",
            warn_on_unset=False,
        )

    return {
        "cwd": cwd,
        "procfile_path": procfile_path,
        "command_source": command_source,
        "process_names": process_names,
        "selected_process": selected_process,
        "raw_command": raw_command,
        "resolved_command": resolved_command,
        "resolved_command_redacted": _redact_command_for_log(resolved_command, env),
        "pre_start": pre_start,
        "resolved_pre_start": resolved_pre_start,
        "resolved_pre_start_redacted": _redact_command_for_log(resolved_pre_start, env),
        "health_command": health_command,
        "resolved_health_command": resolved_health_command,
        "warnings": warnings,
        "env": env,
    }


def _popen_kwargs():
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _terminate_tree(proc, *, graceful=True):
    if not proc or proc.poll() is not None:
        return
    if sys.platform == "win32":
        if graceful:
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
                return
            except Exception:
                pass
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        return
    try:
        pgid = os.getpgid(proc.pid)
        sig = signal.SIGTERM if graceful else signal.SIGKILL
        if pgid != os.getpgrp():
            os.killpg(pgid, sig)
            return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        if graceful:
            proc.terminate()
        else:
            proc.kill()
    except OSError:
        pass


def _command_argv(command):
    if sys.platform == "win32":
        return ["cmd.exe", "/c", command]
    return ["/bin/sh", "-c", command]


def _load_service_slot(bp_dir, slot_index):
    config = read_json(os.path.join(bp_dir, "config.json"))
    layout = normalize_layout(read_json(os.path.join(bp_dir, "layout.json")), config=config)
    slots = layout.get("slots", [])
    if slot_index is None or slot_index < 0 or slot_index >= len(slots) or not slots[slot_index]:
        raise ValueError("Service worker slot not found.")
    worker = slots[slot_index]
    if worker.get("type") != "service":
        raise ValueError("Selected worker is not a Service worker.")
    return worker


def _service_order_id(order):
    return str((order or {}).get("id") or _event_id())


def _ticket_commit(task):
    if not task:
        return ""
    explicit = str(task.get("commit") or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", explicit):
        return explicit
    match = COMMIT_LINE_RE.search(task.get("body") or "")
    return match.group(1) if match else ""


def _ticket_env(task):
    task = task or {}
    return {
        "BULLPEN_SERVICE_COMMIT": _ticket_commit(task),
        "BULLPEN_TICKET_ID": str(task.get("id") or ""),
        "BULLPEN_TICKET_TITLE": str(task.get("title") or ""),
        "BULLPEN_TICKET_STATUS": str(task.get("status") or ""),
        "BULLPEN_TICKET_PRIORITY": str(task.get("priority") or ""),
        "BULLPEN_TICKET_TAGS": ",".join(str(tag) for tag in (task.get("tags") or [])),
    }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _ip_allowed_for_health(host, ip_text):
    if host.casefold() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _validate_health_url(url):
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HTTP health checks require an http:// or https:// URL.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        resolved = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"HTTP health host did not resolve: {exc}") from exc
    addrs = {item[4][0] for item in resolved}
    if not addrs or not all(_ip_allowed_for_health(parsed.hostname, addr) for addr in addrs):
        raise ValueError("HTTP health checks are limited to local/private addresses.")
    return url


def _log_artifact(bp_dir, path):
    return os.path.relpath(path, os.path.dirname(bp_dir)).replace(os.sep, "/")


def _append_service_history(bp_dir, task_id, event, worker, slot_index, row):
    task = task_mod.read_task(bp_dir, task_id)
    if not task:
        return
    history = list(task.get("history") or [])
    history.append({
        "timestamp": _now_iso(),
        "event": event,
        "worker_type": "service",
        "worker_name": worker.get("name", "Service"),
        "worker_slot": slot_index,
        "task_id": task_id,
        **row,
    })
    task_mod.update_task(bp_dir, task_id, {"history": history})


def _service_order_still_active(bp_dir, slot_index, task_id):
    task = task_mod.read_task(bp_dir, task_id)
    if not task:
        return False
    try:
        worker = _load_service_slot(bp_dir, slot_index)
    except Exception:
        return False
    queue = worker.get("task_queue") or []
    assigned_value = task.get("assigned_to")
    assigned_to = "" if assigned_value is None else str(assigned_value)
    return (
        bool(queue)
        and queue[0] == task_id
        and assigned_to == str(slot_index)
        and task.get("status") in {"assigned", "in_progress"}
    )


def run_service_order(bp_dir, slot_index, socketio=None, ws_id=None):
    """Start a ticket-triggered Service worker order from the normal queue."""
    from server import workers as worker_mod
    from server.worker_types import get_worker_type

    try:
        layout = worker_mod._load_layout(bp_dir)
    except FileNotFoundError:
        return
    slots = layout.get("slots", [])
    if slot_index >= len(slots):
        return
    worker = slots[slot_index]
    if not worker or worker.get("type") != "service":
        return
    if not worker.get("task_queue"):
        return

    errors = get_worker_type("service").validate_config(worker)
    if errors:
        worker_mod._block_agent_start_failure(
            bp_dir, slot_index, worker["task_queue"][0], errors[0], socketio, ws_id,
        )
        return

    begun = worker_mod._begin_run(bp_dir, slot_index, socketio=socketio, ws_id=ws_id)
    if begun is None:
        return
    layout, worker, task, task_id = begun
    worker_snapshot = dict(worker)
    worker_mod._commit_run_start(bp_dir, slot_index, task_id, socketio, ws_id)

    thread = threading.Thread(
        target=_run_service_order_thread,
        args=(bp_dir, slot_index, task_id, worker_snapshot, socketio, ws_id),
        daemon=True,
    )
    thread.start()


def _run_service_order_thread(bp_dir, slot_index, task_id, worker_snapshot, socketio, ws_id):
    from server import workers as worker_mod

    started = time.time()
    task = task_mod.read_task(bp_dir, task_id)
    order = {
        "id": task_id,
        "task": task,
    }
    _append_service_history(
        bp_dir,
        task_id,
        "service_order_started",
        worker_snapshot,
        slot_index,
        {
            "action": worker_snapshot.get("ticket_action", "start-if-stopped-else-restart"),
            "log_artifact": _log_artifact(bp_dir, os.path.join(bp_dir, "logs", "services", f"slot-{slot_index}", "service.log")),
        },
    )

    try:
        controller = get_controller(bp_dir, ws_id, slot_index, socketio)
        result = controller.run_ticket_order(order, worker_snapshot)
        duration_ms = int((time.time() - started) * 1000)
        if not _service_order_still_active(bp_dir, slot_index, task_id):
            controller._write_log(
                f"[bullpen] service order {task_id} finished after cancellation; ticket left unchanged\n",
                emit=True,
            )
            return
        if result.get("ok"):
            snapshot = controller.state_snapshot()
            _append_service_history(
                bp_dir,
                task_id,
                "service_order_succeeded",
                worker_snapshot,
                slot_index,
                {
                    "action": result.get("action"),
                    "state": snapshot.get("state"),
                    "health": snapshot.get("health"),
                    "pid": snapshot.get("pid"),
                    "duration_ms": duration_ms,
                    "exit_code": snapshot.get("exit_code"),
                    "reason": None,
                    "log_artifact": _log_artifact(bp_dir, controller.log_path),
                    "config_hash": snapshot.get("active_config_hash"),
                },
            )
            worker_mod._on_agent_success(
                bp_dir,
                slot_index,
                task_id,
                "",
                socketio,
                agent_cwd=None,
                ws_id=ws_id,
                usage={},
                disposition_override=worker_snapshot.get("disposition", "review"),
                output_appender=lambda _worker: None,
                allow_auto_actions=False,
            )
            return

        reason = result.get("reason") or "Service order failed."
        _append_service_history(
            bp_dir,
            task_id,
            "service_order_failed",
            worker_snapshot,
            slot_index,
            {
                "action": result.get("action"),
                "state": result.get("state"),
                "health": result.get("health"),
                "pid": result.get("pid"),
                "duration_ms": duration_ms,
                "exit_code": result.get("exit_code"),
                "reason": reason,
                "log_artifact": _log_artifact(bp_dir, controller.log_path),
            },
        )
        worker_mod._on_agent_error(
            bp_dir,
            slot_index,
            task_id,
            reason,
            socketio,
            output="",
            ws_id=ws_id,
            non_retryable=False,
            max_retries_override=worker_snapshot.get("max_retries", 1),
        )
    except Exception as exc:
        duration_ms = int((time.time() - started) * 1000)
        if not _service_order_still_active(bp_dir, slot_index, task_id):
            try:
                get_controller(bp_dir, ws_id, slot_index, socketio)._write_log(
                    f"[bullpen] service order {task_id} canceled: {exc}\n",
                    emit=True,
                )
            except Exception:
                pass
            return
        _append_service_history(
            bp_dir,
            task_id,
            "service_order_failed",
            worker_snapshot,
            slot_index,
            {
                "action": worker_snapshot.get("ticket_action", "start-if-stopped-else-restart"),
                "state": "crashed",
                "health": None,
                "pid": None,
                "duration_ms": duration_ms,
                "exit_code": None,
                "reason": str(exc),
                "log_artifact": _log_artifact(bp_dir, os.path.join(bp_dir, "logs", "services", f"slot-{slot_index}", "service.log")),
            },
        )
        worker_mod._on_agent_error(
            bp_dir,
            slot_index,
            task_id,
            str(exc),
            socketio,
            output="",
            ws_id=ws_id,
            non_retryable=False,
            max_retries_override=worker_snapshot.get("max_retries", 1),
        )


class ServiceWorkerController:
    def __init__(self, bp_dir, ws_id, slot_index, socketio=None):
        self.bp_dir = bp_dir
        self.workspace = os.path.dirname(bp_dir)
        self.ws_id = ws_id
        self.slot_index = int(slot_index)
        self.socketio = socketio
        self._lock = threading.RLock()
        self._log_lock = threading.Lock()
        self._op_thread = None
        self._reader_thread = None
        self._proc = None
        self._pre_start_proc = None
        self._cancel = threading.Event()
        self._order_cancel = threading.Event()
        self._state = "stopped"
        self._health = None
        self._pid = None
        self._started_at = None
        self._exit_code = None
        self._active_config_hash = None
        self._config_hash = None
        self._last_error = None
        self._active_order_id = None
        self._health_thread = None

    @property
    def log_dir(self):
        return os.path.join(self.bp_dir, "logs", "services", f"slot-{self.slot_index}")

    @property
    def log_path(self):
        return os.path.join(self.log_dir, "service.log")

    @property
    def rotated_log_path(self):
        return os.path.join(self.log_dir, "service.log.1")

    def state_snapshot(self):
        with self._lock:
            return {
                "slot": self.slot_index,
                "state": self._state,
                "health": self._health,
                "pid": self._pid,
                "started_at": self._started_at,
                "exit_code": self._exit_code,
                "config_hash": self._config_hash,
                "active_config_hash": self._active_config_hash,
                "last_error": self._last_error,
            }

    def emit_state(self):
        _emit(self.socketio, "service:state", self.state_snapshot(), self.ws_id)

    def emit_log(self, lines, *, catchup=False, reset=False):
        if isinstance(lines, str):
            lines = [lines]
        payload = {
            "slot": self.slot_index,
            "lines": [line.rstrip("\n") for line in lines],
            "catchup": bool(catchup),
            "reset": bool(reset),
        }
        _emit(self.socketio, "service:log", payload, self.ws_id)

    def start(self):
        thread = threading.Thread(target=self._start_sequence, daemon=True)
        with self._lock:
            if self._state in ("starting", "running", "stopping"):
                self.emit_state()
                return False
            self._cancel.clear()
            self._state = "starting"
            self._pid = None
            self._started_at = None
            self._last_error = None
            self._op_thread = thread
            self.emit_state()
        thread.start()
        return True

    def restart(self):
        thread = threading.Thread(target=self._restart_sequence, daemon=True)
        with self._lock:
            if self._state in ("starting", "stopping"):
                self.emit_state()
                return False
            self._cancel.clear()
            self._state = "stopping" if self._state == "running" else "starting"
            self._last_error = None
            self._op_thread = thread
            self.emit_state()
        thread.start()
        return True

    def stop(self):
        self._cancel.set()
        with self._lock:
            if self._state == "stopped":
                self.emit_state()
                return False
            self._state = "stopping"
            self._last_error = None
            self.emit_state()
            pre_start_proc = self._pre_start_proc
            proc = self._proc
            timeout = self._current_stop_timeout()
        if pre_start_proc and pre_start_proc.poll() is None:
            _terminate_tree(pre_start_proc, graceful=True)
        if proc and proc.poll() is None:
            _terminate_tree(proc, graceful=True)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _terminate_tree(proc, graceful=False)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        with self._lock:
            if not self._proc or self._proc.poll() is not None:
                self._state = "stopped"
                self._pid = None
                self._started_at = None
                self._exit_code = self._proc.returncode if self._proc else self._exit_code
                self._proc = None
            self._pre_start_proc = None
        self._write_log("[bullpen] service stopped\n", emit=True)
        self.emit_state()
        return True

    def cancel_order(self, order_id):
        with self._lock:
            if self._active_order_id != order_id:
                return False
            state = self._state
        self._write_log(f"[bullpen] service order {order_id} canceled\n", emit=True)
        if state in BUSY_STATES:
            self._order_cancel.set()
            self._cancel.set()
            pre_start_proc = self._pre_start_proc
            if pre_start_proc and pre_start_proc.poll() is None:
                _terminate_tree(pre_start_proc, graceful=True)
        return True

    def tail(self, max_bytes=65536):
        try:
            max_bytes = max(1, min(int(max_bytes or 65536), 1024 * 1024))
        except (TypeError, ValueError):
            max_bytes = 65536
        chunks = []
        for path in (self.rotated_log_path, self.log_path):
            if os.path.exists(path):
                with open(path, "rb") as handle:
                    chunks.append(handle.read())
        data = b"".join(chunks)[-max_bytes:]
        text = data.decode("utf-8", errors="replace")
        self.emit_state()
        self.emit_log(text.splitlines(), catchup=True, reset=True)

    def _current_stop_timeout(self):
        try:
            worker = _load_service_slot(self.bp_dir, self.slot_index)
            return max(0, int(worker.get("stop_timeout_seconds", 5)))
        except Exception:
            return 5

    def _set_state(self, state, **updates):
        with self._lock:
            self._state = state
            for key, value in updates.items():
                setattr(self, f"_{key}", value)
        self.emit_state()

    def _start_sequence(self, order=None, worker_snapshot=None, result=None):
        order_id = _service_order_id(order)
        try:
            from server.worker_types import get_worker_type

            worker = worker_snapshot or _load_service_slot(self.bp_dir, self.slot_index)
            errors = get_worker_type("service").validate_config(worker)
            if errors:
                raise ValueError(errors[0])
            self._config_hash = self._service_config_hash(worker)
            self._active_config_hash = self._config_hash
            self._exit_code = None
            self._last_error = None
            self._set_state("starting", pid=None, started_at=None)
            self._rotate_log(force=True)
            self._write_log(f"[bullpen] starting service order {order_id}\n", emit=True)

            deadline = time.monotonic() + max(1, int(worker.get("startup_timeout_seconds", 60)))
            preview = resolve_service_preview(worker, self.workspace, self.slot_index, order=order)
            cwd = preview["cwd"]
            env = preview["env"]
            command = preview["resolved_command"]
            for warning in preview["warnings"]:
                self._write_log(f"[bullpen] {warning}\n", emit=True)
            if preview["command_source"] == "procfile":
                self._write_log(
                    f"[bullpen] procfile {preview['selected_process']}: {preview['resolved_command_redacted']}\n",
                    emit=True,
                )
            else:
                self._write_log(f"[bullpen] command: {preview['resolved_command_redacted']}\n", emit=True)

            pre_start = preview["resolved_pre_start"]
            if pre_start:
                self._write_log(f"[bullpen] pre-start: {preview['resolved_pre_start_redacted']}\n", emit=True)
                self._run_pre_start(pre_start, cwd, env, deadline)

            if self._cancel.is_set():
                self._set_state("stopped", pid=None, started_at=None)
                if result is not None:
                    result.update({"ok": False, "reason": "Service start was canceled."})
                return False

            proc = subprocess.Popen(
                _command_argv(command),
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                **_popen_kwargs(),
            )
            with self._lock:
                self._proc = proc
                self._pid = proc.pid
                self._started_at = _now_iso()
            self._write_log(f"[bullpen] spawned pid {proc.pid}\n", emit=True)
            self._reader_thread = threading.Thread(target=self._drain_output, args=(proc,), daemon=True)
            self._reader_thread.start()

            if self._health_type(worker) == "none":
                grace = max(0, int(worker.get("startup_grace_seconds", 2)))
                while time.monotonic() < deadline and grace > 0:
                    if self._cancel.is_set() or proc.poll() is not None:
                        break
                    step = min(0.1, grace)
                    time.sleep(step)
                    grace -= step
                if self._cancel.is_set():
                    self.stop()
                    if result is not None:
                        result.update({"ok": False, "reason": "Service start was canceled."})
                    return False
                if proc.poll() is not None:
                    raise RuntimeError(f"Service exited during startup with code {proc.returncode}.")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Service startup timed out.")
                self._set_state("running", health=None)
                self._write_log("[bullpen] service running\n", emit=True)
            else:
                self._wait_for_initial_health(worker, cwd, env, proc, deadline)
            threading.Thread(target=self._monitor, args=(proc,), daemon=True).start()
            if self._health_type(worker) != "none":
                self._health_thread = threading.Thread(
                    target=self._health_monitor,
                    args=(worker, cwd, env, proc),
                    daemon=True,
                )
                self._health_thread.start()
            if result is not None:
                result.update({"ok": True, "state": self._state, "pid": proc.pid, "exit_code": None})
            return True
        except Exception as exc:
            self._last_error = str(exc)
            self._write_log(f"[bullpen] service start failed: {exc}\n", emit=True)
            self._cleanup_failed_start()
            self._set_state("crashed", pid=None, started_at=None)
            if result is not None:
                result.update({"ok": False, "reason": str(exc), "state": "crashed", "exit_code": self._exit_code})
            return False

    def _restart_sequence(self, order=None, worker_snapshot=None, result=None):
        self.stop()
        if self._order_cancel.is_set():
            if result is not None:
                result.update({"ok": False, "reason": "Service order was canceled."})
            return False
        self._cancel.clear()
        return self._start_sequence(order=order, worker_snapshot=worker_snapshot, result=result)

    def run_ticket_order(self, order, worker_snapshot):
        order_id = _service_order_id(order)
        action = str(worker_snapshot.get("ticket_action") or "start-if-stopped-else-restart")
        if action not in {"start-if-stopped-else-restart", "restart", "start-if-stopped"}:
            action = "start-if-stopped-else-restart"

        with self._lock:
            state = self._state
        if state in BUSY_STATES:
            reason = f"Service is busy ({state})."
            self._write_log(f"[bullpen] service order {order_id} failed: {reason}\n", emit=True)
            return {"ok": False, "reason": reason, "state": state, "action": action}

        if action == "start-if-stopped" and state in RUNNING_STATES:
            self._write_log(f"[bullpen] service order {order_id}: service already {state}\n", emit=True)
            self.emit_state()
            return {
                "ok": True,
                "state": state,
                "health": self._health,
                "pid": self._pid,
                "exit_code": self._exit_code,
                "action": action,
            }

        result = {"action": action}
        with self._lock:
            self._active_order_id = order_id
        self._order_cancel.clear()
        try:
            if action == "restart" or (action == "start-if-stopped-else-restart" and state in RUNNING_STATES):
                self._restart_sequence(order=order, worker_snapshot=worker_snapshot, result=result)
            else:
                self._start_sequence(order=order, worker_snapshot=worker_snapshot, result=result)
        finally:
            with self._lock:
                if self._active_order_id == order_id:
                    self._active_order_id = None
        result.setdefault("action", action)
        snapshot = self.state_snapshot()
        result.setdefault("state", snapshot.get("state"))
        result.setdefault("health", snapshot.get("health"))
        result.setdefault("pid", snapshot.get("pid"))
        result.setdefault("exit_code", snapshot.get("exit_code"))
        return result

    def _health_type(self, worker):
        health_type = str(worker.get("health_type") or "none")
        return health_type if health_type in {"http", "shell"} else "none"

    def _wait_for_initial_health(self, worker, cwd, env, proc, deadline):
        interval = max(0.1, float(worker.get("health_interval_seconds") or 5))
        last_reason = "Health check has not run."
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                self.stop()
                raise RuntimeError("Service start was canceled.")
            if proc.poll() is not None:
                raise RuntimeError(f"Service exited during startup with code {proc.returncode}.")
            ok, reason = self._check_health(worker, cwd, env)
            if ok:
                self._set_state("healthy", health="healthy", last_error=None)
                self._write_log("[bullpen] service healthy\n", emit=True)
                return
            last_reason = reason
            with self._lock:
                self._health = "unhealthy"
                self._last_error = reason
            self.emit_state()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining, 1.0))
        raise TimeoutError(f"Service health check timed out: {last_reason}")

    def _health_monitor(self, worker, cwd, env, proc):
        interval = max(0.1, float(worker.get("health_interval_seconds") or 5))
        threshold = max(1, int(worker.get("health_failure_threshold") or 3))
        failures = 0
        while proc.poll() is None:
            time.sleep(interval)
            with self._lock:
                if self._proc is not proc or self._state in {"stopped", "stopping", "crashed"}:
                    return
            ok, reason = self._check_health(worker, cwd, env)
            if ok:
                failures = 0
                with self._lock:
                    changed = self._state != "healthy" or self._health != "healthy"
                if changed:
                    self._set_state("healthy", health="healthy", last_error=None)
                    self._write_log("[bullpen] service healthy\n", emit=True)
            else:
                failures += 1
                if failures >= threshold:
                    with self._lock:
                        changed = self._state != "unhealthy" or self._last_error != reason
                    if changed:
                        self._set_state("unhealthy", health="unhealthy", last_error=reason)
                        self._write_log(f"[bullpen] service unhealthy: {reason}\n", emit=True)

    def _check_health(self, worker, cwd, env):
        health_type = self._health_type(worker)
        timeout = max(0.1, float(worker.get("health_timeout_seconds") or 2))
        if health_type == "http":
            return self._check_http_health(worker.get("health_url"), timeout)
        if health_type == "shell":
            return self._check_shell_health(worker.get("health_command"), cwd, env, timeout)
        return True, "No health check configured."

    def _check_http_health(self, url, timeout):
        try:
            _validate_health_url(url)
            opener = build_opener(_NoRedirect)
            request = Request(url, method="GET", headers={"User-Agent": "Bullpen-Service-Health/1"})
            with opener.open(request, timeout=timeout) as response:
                status = int(getattr(response, "status", response.getcode()))
            if 200 <= status < 300:
                return True, f"HTTP {status}"
            return False, f"HTTP {status}"
        except Exception as exc:
            return False, str(exc)

    def _check_shell_health(self, command, cwd, env, timeout):
        command = str(command or "").strip()
        if not command:
            return False, "Shell health checks require a command."
        command, _ = _interpolate_env_refs(
            command,
            env,
            context_label="health_command",
            warn_on_unset=False,
        )
        try:
            proc = subprocess.Popen(
                _command_argv(command),
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                **_popen_kwargs(),
            )
        except Exception as exc:
            return False, str(exc)
        try:
            output, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_tree(proc, graceful=True)
            try:
                output, _ = proc.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                _terminate_tree(proc, graceful=False)
                output, _ = proc.communicate()
            if output:
                lines = [f"[health] {line}" for line in output.splitlines()]
                self._write_log("\n".join(lines) + "\n", emit=True)
            return False, "Shell health check timed out."
        if output:
            lines = [f"[health] {line}" for line in output.splitlines()]
            self._write_log("\n".join(lines) + "\n", emit=True)
        if proc.returncode == 0:
            return True, "Shell health check passed."
        return False, f"Shell health check exited {proc.returncode}."

    def _run_pre_start(self, command, cwd, env, deadline):
        self._write_log("[bullpen] running pre-start\n", emit=True)
        proc = subprocess.Popen(
            _command_argv(command),
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            **_popen_kwargs(),
        )
        with self._lock:
            self._pre_start_proc = proc
        reader = threading.Thread(target=self._drain_output, args=(proc,), daemon=True)
        reader.start()
        while proc.poll() is None:
            if self._cancel.is_set():
                _terminate_tree(proc, graceful=True)
                break
            if time.monotonic() >= deadline:
                _terminate_tree(proc, graceful=True)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    _terminate_tree(proc, graceful=False)
                raise TimeoutError("Pre-start timed out.")
            time.sleep(0.05)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _terminate_tree(proc, graceful=False)
            proc.wait()
        with self._lock:
            self._pre_start_proc = None
        if self._cancel.is_set():
            return
        if proc.returncode != 0:
            raise RuntimeError(f"Pre-start exited with code {proc.returncode}.")
        self._write_log("[bullpen] pre-start complete\n", emit=True)

    def _drain_output(self, proc):
        try:
            for line in proc.stdout:
                self._write_log(line, emit=True)
        except (ValueError, OSError):
            pass

    def _monitor(self, proc):
        proc.wait()
        with self._lock:
            if self._proc is not proc:
                return
            self._exit_code = proc.returncode
            was_stopping = self._state == "stopping" or self._cancel.is_set()
            self._proc = None
            self._pid = None
            self._started_at = None
            self._state = "stopped" if was_stopping else "crashed"
        if was_stopping:
            self._write_log(f"[bullpen] service exited with code {proc.returncode}\n", emit=True)
        else:
            self._write_log(f"[bullpen] service crashed with code {proc.returncode}\n", emit=True)
        self.emit_state()

    def _cleanup_failed_start(self):
        with self._lock:
            proc = self._proc
            pre = self._pre_start_proc
            for candidate in (proc, pre):
                if candidate and candidate.poll() is not None:
                    self._exit_code = candidate.returncode
            self._proc = None
            self._pre_start_proc = None
            self._pid = None
            self._started_at = None
        for candidate in (pre, proc):
            if candidate and candidate.poll() is None:
                _terminate_tree(candidate, graceful=True)
                try:
                    candidate.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    _terminate_tree(candidate, graceful=False)

    def _rotate_log(self, *, force=False, incoming_len=0):
        os.makedirs(self.log_dir, exist_ok=True)
        with self._log_lock:
            should_rotate = False
            if os.path.exists(self.log_path):
                current_size = os.path.getsize(self.log_path)
                should_rotate = (force and current_size > 0) or current_size + incoming_len > self._log_max_bytes()
            if not should_rotate:
                return
            try:
                if os.path.exists(self.rotated_log_path):
                    os.remove(self.rotated_log_path)
                os.replace(self.log_path, self.rotated_log_path)
            except OSError:
                pass

    def _write_log(self, text, *, emit=False):
        if text is None:
            return
        if not text.endswith("\n"):
            text += "\n"
        data_len = len(text.encode("utf-8", errors="replace"))
        self._rotate_log(incoming_len=data_len)
        os.makedirs(self.log_dir, exist_ok=True)
        with self._log_lock:
            with open(self.log_path, "a", encoding="utf-8", errors="replace") as handle:
                handle.write(text)
        if emit:
            self.emit_log(text.splitlines())

    def _log_max_bytes(self):
        try:
            worker = _load_service_slot(self.bp_dir, self.slot_index)
            return max(1024, int(worker.get("log_max_bytes", SERVICE_LOG_DEFAULT_MAX)))
        except Exception:
            return SERVICE_LOG_DEFAULT_MAX

    def _service_config_hash(self, worker):
        # Phase 2 needs a stable comparable token; a full canonical helper can
        # grow here as ticket orders and health checks land.
        import hashlib
        import json
        fields = {
            key: worker.get(key)
            for key in (
                "command", "command_source", "procfile_process", "port",
                "cwd", "env", "pre_start", "ticket_action",
                "disposition", "max_retries", "startup_grace_seconds",
                "startup_timeout_seconds", "health_type", "health_url",
                "health_command", "health_interval_seconds",
                "health_timeout_seconds", "health_failure_threshold",
                "on_crash", "stop_timeout_seconds", "log_max_bytes",
            )
        }
        payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_controller(bp_dir, ws_id, slot, socketio=None):
    key = (ws_id, os.path.realpath(bp_dir), int(slot))
    with _controllers_lock:
        controller = _controllers.get(key)
        if controller is None:
            controller = ServiceWorkerController(bp_dir, ws_id, int(slot), socketio=socketio)
            _controllers[key] = controller
        elif socketio is not None:
            controller.socketio = socketio
        return controller


def start_service(bp_dir, ws_id, slot, socketio=None):
    worker = _load_service_slot(bp_dir, slot)
    if worker.get("paused"):
        _emit(socketio, "toast", {
            "message": f"{worker.get('name', 'Service worker')} cannot start: worker paused",
            "level": "info",
        }, ws_id)
        return False
    return get_controller(bp_dir, ws_id, slot, socketio).start()


def stop_service(bp_dir, ws_id, slot, socketio=None):
    return get_controller(bp_dir, ws_id, slot, socketio).stop()


def restart_service(bp_dir, ws_id, slot, socketio=None):
    worker = _load_service_slot(bp_dir, slot)
    if worker.get("paused"):
        _emit(socketio, "toast", {
            "message": f"{worker.get('name', 'Service worker')} cannot restart: worker paused",
            "level": "info",
        }, ws_id)
        return False
    return get_controller(bp_dir, ws_id, slot, socketio).restart()


def tail_service(bp_dir, ws_id, slot, socketio=None, max_bytes=65536):
    return get_controller(bp_dir, ws_id, slot, socketio).tail(max_bytes=max_bytes)


def cancel_service_order(bp_dir, ws_id, slot, task_id, socketio=None):
    return get_controller(bp_dir, ws_id, slot, socketio).cancel_order(task_id)


def stop_workspace_services(ws_id, *, wait=True):
    with _controllers_lock:
        controllers = [controller for (key_ws_id, _, _), controller in _controllers.items() if key_ws_id == ws_id]
    _stop_controllers(controllers, wait=wait)


def emit_workspace_states(bp_dir, ws_id, socketio=None):
    """Re-emit known service states for controllers in one workspace."""
    root = os.path.realpath(bp_dir)
    with _controllers_lock:
        controllers = [
            controller
            for (key_ws_id, key_bp_dir, _), controller in _controllers.items()
            if key_ws_id == ws_id and key_bp_dir == root
        ]
    for controller in controllers:
        if socketio is not None:
            controller.socketio = socketio
        controller.emit_state()


def stop_all_services(*, wait=True):
    with _controllers_lock:
        controllers = list(_controllers.values())
    _stop_controllers(controllers, wait=wait)


def _stop_controllers(controllers, *, wait=True):
    threads = []
    for controller in controllers:
        thread = threading.Thread(target=controller.stop, daemon=True)
        thread.start()
        threads.append(thread)
    if wait:
        for thread in threads:
            thread.join(timeout=10)
