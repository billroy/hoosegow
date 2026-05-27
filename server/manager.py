"""Host-local Bullpen manager for launching named Bullpen instances."""

from __future__ import annotations

import json
import asyncio
import fcntl
import importlib.util
import inspect
import os
import pty
import re
import signal
import shutil
import socket
import subprocess
import struct
import sys
import termios
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO

from server.persistence import read_json, write_json


DEFAULT_MANAGER_PORT = 5757
DEFAULT_BULLPEN_PORT = 8080
DEFAULT_APP_PORT = 3000
DEFAULT_BULLPEN_PORT_RANGE = (8081, 8180)
DEFAULT_APP_PORT_RANGE = (3001, 3100)
DEFAULT_MICROSANDBOX_BASE = "bullpen-microsandbox-local"
LOCALHOST = "127.0.0.1"
PROFILE_ID_RE = re.compile(r"[^a-z0-9-]+")
AI_PROVIDER_LABELS = {
    "claude": "Claude",
    "codex": "Codex",
    "gemini": "Gemini",
}


class ManagerError(Exception):
    """Raised when a manager operation cannot be completed."""


def default_manager_home() -> Path:
    configured = os.environ.get("BULLPEN_MANAGER_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".bullpen" / "manager"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def slugify(value: str, *, fallback: str = "instance") -> str:
    slug = PROFILE_ID_RE.sub("-", (value or "").strip().lower()).strip("-")
    return slug or fallback


def positive_int_payload(payload: dict[str, Any], key: str, default: int) -> int:
    raw_value = payload.get(key)
    if raw_value in (None, ""):
        raw_value = default
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ManagerError(f"{key} must be a positive integer") from exc
    if value < 1:
        raise ManagerError(f"{key} must be a positive integer")
    return value


def is_port_listening(port: int, host: str = LOCALHOST, timeout: float = 0.2) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, int(port))) == 0


def wait_for_http_health(port: int, *, timeout_seconds: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://{LOCALHOST}:{port}/health"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return True
        except (OSError, URLError):
            pass
        time.sleep(0.25)
    return False


def profiles_payload(registry: "ProfileRegistry") -> list[dict[str, Any]]:
    return [profile_payload(profile) for profile in registry.profiles()]


def profile_payload(profile: dict[str, Any]) -> dict[str, Any]:
    payload = dict(profile)
    payload["deploymentInfo"] = deployment_info(profile)
    return payload


def microsandbox_base_snapshots_payload() -> list[dict[str, Any]]:
    """Return local Microsandbox snapshots that can be used as sandbox bases."""

    async def _list_snapshots() -> list[dict[str, Any]]:
        module = _load_deploy_sandbox_module()
        runtime = module.MicrosandboxRuntime()
        list_snapshots = getattr(runtime.Snapshot, "list", None)
        if not callable(list_snapshots):
            return []
        result = list_snapshots()
        if inspect.isawaitable(result):
            result = await result
        snapshots = []
        for snapshot in result or []:
            name = str(getattr(snapshot, "name", "") or "").strip()
            if not name:
                continue
            snapshots.append(
                {
                    "name": name,
                    "digest": str(getattr(snapshot, "digest", "") or ""),
                    "imageRef": str(getattr(snapshot, "image_ref", "") or ""),
                    "createdAt": getattr(snapshot, "created_at", None),
                    "sizeBytes": getattr(snapshot, "size_bytes", None),
                }
            )
        snapshots.sort(key=lambda item: (item["name"] != DEFAULT_MICROSANDBOX_BASE, item["name"]))
        return snapshots

    try:
        return asyncio.run(_list_snapshots())
    except Exception as exc:
        raise ManagerError(f"Unable to list Microsandbox base snapshots: {exc}") from exc


def deployment_info(profile: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(profile.get("workspaceRoot") or "").expanduser()
    return {
        "resources": resource_info(profile),
        "aiProviders": configured_ai_providers(workspace),
        "providerAuth": provider_auth_info(profile),
        "git": git_info(workspace),
    }


def resource_info(profile: dict[str, Any]) -> dict[str, Any]:
    resources = profile.get("resources") if isinstance(profile.get("resources"), dict) else {}
    if resources:
        return {
            "source": "configured",
            "vcpus": _safe_positive_int(resources.get("vcpus")),
            "memoryMiB": _safe_positive_int(resources.get("memoryMiB")),
        }
    return {
        "source": "host",
        "vcpus": os.cpu_count(),
        "memoryMiB": _host_memory_mib(),
    }


def configured_ai_providers(workspace_root: Path) -> list[dict[str, Any]]:
    provider_models: dict[tuple[str, str], dict[str, Any]] = {}
    for bp_dir in _candidate_bp_dirs(workspace_root):
        layout_path = bp_dir / "layout.json"
        try:
            layout = read_json(str(layout_path))
        except Exception:
            continue
        slots = layout.get("slots") if isinstance(layout, dict) else []
        if not isinstance(slots, list):
            continue
        workspace_name = bp_dir.parent.name
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            if str(slot.get("type") or "ai").strip() != "ai":
                continue
            agent = str(slot.get("agent") or "claude").strip().lower() or "claude"
            model = str(slot.get("model") or "").strip()
            key = (agent, model)
            item = provider_models.setdefault(
                key,
                {
                    "agent": agent,
                    "label": AI_PROVIDER_LABELS.get(agent, agent.title()),
                    "model": model,
                    "count": 0,
                    "workspaces": [],
                },
            )
            item["count"] += 1
            if workspace_name not in item["workspaces"]:
                item["workspaces"].append(workspace_name)
    return sorted(provider_models.values(), key=lambda item: (item["agent"], item["model"]))


def provider_auth_info(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    home = provider_home(profile)
    return {
        "claude": {
            "label": "Claude",
            "authenticated": claude_credentials_authenticated(home),
        },
        "codex": {
            "label": "Codex",
            "authenticated": codex_credentials_authenticated(home),
        },
        "git": {
            "label": "Git",
            "authenticated": git_credentials_authenticated(home),
        },
    }


def provider_home(profile: dict[str, Any]) -> Path:
    if profile.get("runtime") == "local":
        return Path.home()
    return Path(profile.get("sandboxHome") or profile.get("instanceHome") or "").expanduser()


def claude_credentials_authenticated(home: Path) -> bool:
    try:
        data = read_json(str(home / ".claude" / ".credentials.json"))
    except Exception:
        return False
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    return isinstance(oauth, dict) and bool(oauth.get("accessToken") or oauth.get("refreshToken"))


def codex_credentials_authenticated(home: Path) -> bool:
    try:
        data = read_json(str(home / ".codex" / "auth.json"))
    except Exception:
        return False
    return isinstance(data, dict) and bool(data)


def git_credentials_authenticated(home: Path) -> bool:
    gh_hosts = home / ".config" / "gh" / "hosts.yml"
    git_config = home / ".gitconfig"
    try:
        hosts_text = gh_hosts.read_text(encoding="utf-8")
        git_config_text = git_config.read_text(encoding="utf-8")
    except Exception:
        return False
    return (
        "github.com" in hosts_text
        and "oauth_token:" in hosts_text
        and "name =" in git_config_text
        and "email =" in git_config_text
    )


def git_info(workspace_root: Path) -> dict[str, Any]:
    if not workspace_root.is_dir():
        return {"repositories": [], "error": "Workspace path is unavailable"}
    if shutil.which("git") is None:
        return {"repositories": [], "error": "git is not installed"}
    repositories = []
    seen: set[str] = set()
    for candidate in _candidate_repo_dirs(workspace_root):
        repo = _git_repository_info(candidate, workspace_root)
        top_level = repo.get("topLevel")
        if not top_level or top_level in seen:
            continue
        seen.add(top_level)
        repositories.append(repo)
    return {"repositories": repositories}


def _safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _host_memory_mib() -> int | None:
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                return int(result.stdout.strip()) // (1024 * 1024)
        except Exception:
            return None
    if hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return int(pages * page_size) // (1024 * 1024)
        except (OSError, ValueError, TypeError):
            return None
    return None


def _candidate_bp_dirs(workspace_root: Path, *, limit: int = 50) -> list[Path]:
    if not workspace_root.is_dir():
        return []
    candidates: list[Path] = []
    root_bp = workspace_root / ".bullpen"
    if (root_bp / "layout.json").is_file():
        candidates.append(root_bp)
    try:
        children = sorted(workspace_root.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        return candidates
    for child in children:
        if len(candidates) >= limit:
            break
        child_bp = child / ".bullpen"
        if child.is_dir() and (child_bp / "layout.json").is_file():
            candidates.append(child_bp)
    return candidates


def _candidate_repo_dirs(workspace_root: Path, *, limit: int = 50) -> list[Path]:
    candidates = [workspace_root]
    try:
        children = sorted(workspace_root.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        return candidates
    for child in children:
        if len(candidates) >= limit:
            break
        if child.is_dir():
            candidates.append(child)
    return candidates


def _git_repository_info(candidate: Path, workspace_root: Path) -> dict[str, Any]:
    top_level = _git_output(candidate, "rev-parse", "--show-toplevel")
    if not top_level:
        return {}
    branch = _git_output(candidate, "branch", "--show-current")
    short_hash = _git_output(candidate, "rev-parse", "--short", "HEAD")
    dirty_output = _git_output(candidate, "status", "--porcelain", allow_empty=True)
    try:
        name = Path(top_level).resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        name = Path(top_level).name
    if name in ("", "."):
        name = Path(top_level).name
    return {
        "name": name,
        "topLevel": top_level,
        "branch": branch,
        "shortHash": short_hash,
        "dirty": bool(dirty_output),
    }


def _git_output(cwd: Path, *args: str, allow_empty: bool = False) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=3,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return "" if allow_empty else ""
    return result.stdout.strip()


@dataclass
class ManagerPaths:
    home: Path

    @property
    def profiles_path(self) -> Path:
        return self.home / "profiles.json"

    @property
    def static_dir(self) -> Path:
        return repo_root() / "static" / "manager"


class ProfileRegistry:
    """Persistent registry for managed Bullpen instance profiles."""

    def __init__(self, home: Path | None = None):
        self.paths = ManagerPaths((home or default_manager_home()).expanduser())
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            path = self.paths.profiles_path
            if not path.exists():
                return {"schemaVersion": 1, "profiles": []}
            data = read_json(str(path))
            if not isinstance(data, dict):
                raise ManagerError("Manager registry is invalid")
            profiles = data.get("profiles")
            if not isinstance(profiles, list):
                data["profiles"] = []
            data.setdefault("schemaVersion", 1)
            return data

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            self.paths.home.mkdir(parents=True, exist_ok=True)
            write_json(str(self.paths.profiles_path), data)

    def profiles(self) -> list[dict[str, Any]]:
        return list(self.load().get("profiles", []))

    def get(self, profile_id: str) -> dict[str, Any]:
        for profile in self.profiles():
            if profile.get("id") == profile_id:
                return profile
        raise ManagerError(f"Unknown profile: {profile_id}")

    def upsert(self, profile: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self.load()
            profiles = data.get("profiles", [])
            for index, existing in enumerate(profiles):
                if existing.get("id") == profile.get("id"):
                    profiles[index] = profile
                    data["profiles"] = profiles
                    self.save(data)
                    return profile
            profiles.append(profile)
            data["profiles"] = profiles
            self.save(data)
            return profile

    def delete(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            data = self.load()
            profiles = data.get("profiles", [])
            kept = [profile for profile in profiles if profile.get("id") != profile_id]
            if len(kept) == len(profiles):
                raise ManagerError(f"Unknown profile: {profile_id}")
            data["profiles"] = kept
            self.save(data)
            return data


class PortAllocator:
    """Allocate Bullpen/app port pairs with registry and socket deconfliction."""

    def __init__(
        self,
        registry: ProfileRegistry,
        *,
        bullpen_range: tuple[int, int] = DEFAULT_BULLPEN_PORT_RANGE,
        app_range: tuple[int, int] = DEFAULT_APP_PORT_RANGE,
    ):
        self.registry = registry
        self.bullpen_range = bullpen_range
        self.app_range = app_range

    def reserved_ports(self, *, exclude_profile_id: str | None = None) -> set[int]:
        reserved: set[int] = set()
        for profile in self.registry.profiles():
            if exclude_profile_id and profile.get("id") == exclude_profile_id:
                continue
            ports = profile.get("ports") or {}
            for value in ports.values():
                try:
                    reserved.add(int(value))
                except (TypeError, ValueError):
                    continue
        return reserved

    def allocate(self, *, exclude_profile_id: str | None = None) -> dict[str, int]:
        reserved = self.reserved_ports(exclude_profile_id=exclude_profile_id)
        candidates = [(DEFAULT_BULLPEN_PORT, DEFAULT_APP_PORT)]
        start_bullpen, end_bullpen = self.bullpen_range
        start_app, end_app = self.app_range
        span = min(end_bullpen - start_bullpen, end_app - start_app)
        candidates.extend((start_bullpen + offset, start_app + offset) for offset in range(span + 1))
        for bullpen_port, app_port in candidates:
            if bullpen_port == app_port:
                continue
            if bullpen_port in reserved or app_port in reserved:
                continue
            if is_port_listening(bullpen_port) or is_port_listening(app_port):
                continue
            return {"bullpen": bullpen_port, "app": app_port}
        raise ManagerError("No free Bullpen/app port pair found in configured ranges")

    def classify_profile_ports(self, profile: dict[str, Any]) -> dict[str, Any]:
        ports = profile.get("ports") or {}
        result = {}
        for key in ("bullpen", "app"):
            port = ports.get(key)
            if port is None:
                continue
            listening = is_port_listening(int(port))
            observed_state = (profile.get("observed") or {}).get("state")
            managed = observed_state in {"running", "healthy", "starting"} or bool(profile.get("observed", {}).get("pid"))
            result[key] = {
                "port": int(port),
                "state": "listening-managed" if listening and managed else ("listening-unmanaged" if listening else "reserved"),
            }
        return result


def _mark_profile_observed(
    registry: ProfileRegistry,
    profile: dict[str, Any],
    *,
    state: str,
    pid: int | None = None,
    log_path: str | None = None,
    last_error: str | None = None,
    desired_state: str | None = None,
) -> dict[str, Any]:
    updated = dict(profile)
    if desired_state:
        updated["desiredState"] = desired_state
        updated["updatedAt"] = now_ts()
    observed = dict(updated.get("observed") or {})
    observed["state"] = state
    observed["updatedAt"] = now_ts()
    if pid:
        observed["pid"] = pid
    else:
        observed.pop("pid", None)
    if log_path:
        observed["logPath"] = log_path
    if last_error:
        observed["lastError"] = last_error
    elif state not in {"needs-attention", "unhealthy"}:
        observed.pop("lastError", None)
    updated["observed"] = observed
    registry.upsert(updated)
    return updated


class LocalRuntimeController:
    """Start and stop host-local Bullpen child processes."""

    def __init__(self, registry: ProfileRegistry, socketio: SocketIO | None = None):
        self.registry = registry
        self.socketio = socketio
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.RLock()

    def build_argv(self, profile: dict[str, Any]) -> list[str]:
        source = Path(profile.get("process", {}).get("bullpenSource") or str(repo_root())).expanduser()
        script = source / "bullpen.py"
        if not script.is_file():
            raise ManagerError(f"Bullpen source does not contain bullpen.py: {source}")
        workspace = Path(profile.get("workspaceRoot") or "").expanduser()
        if not workspace.is_dir():
            raise ManagerError(f"Workspace path does not exist: {workspace}")
        port = int((profile.get("ports") or {}).get("bullpen") or DEFAULT_BULLPEN_PORT)
        return [
            sys.executable,
            str(script),
            "--workspace",
            str(workspace),
            "--host",
            LOCALHOST,
            "--port",
            str(port),
            "--no-browser",
        ]

    def start(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            profile = self.registry.get(profile_id)
            if profile.get("runtime") != "local":
                raise ManagerError("Only local profiles are implemented in Gen 1")
            process = self._processes.get(profile_id)
            if process and process.poll() is None:
                return self._mark_observed(profile, state="running", pid=process.pid, desired_state="running")

            ports = profile.get("ports") or {}
            bullpen_port = int(ports.get("bullpen") or DEFAULT_BULLPEN_PORT)
            if is_port_listening(bullpen_port):
                observed = profile.get("observed") or {}
                if wait_for_http_health(bullpen_port, timeout_seconds=1.0):
                    return self._mark_observed(profile, state="running", pid=observed.get("pid"), desired_state="running")
                raise ManagerError(f"Port {bullpen_port} is already occupied")

            instance_home = Path(profile.get("instanceHome") or self.registry.paths.home / "instances" / profile_id / "home").expanduser()
            logs_dir = instance_home / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = logs_dir / "bullpen.log"
            argv = self.build_argv(profile)
            env = os.environ.copy()
            env["BULLPEN_DEPLOY_LABEL"] = f"(Manager:{profile_id})"
            env["BULLPEN_MANAGER_PROFILE_ID"] = profile_id
            log_file = open(log_path, "a", encoding="utf-8")
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=str(Path(profile.get("process", {}).get("bullpenSource") or str(repo_root())).expanduser()),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    env=env,
                    start_new_session=True,
                )
            finally:
                log_file.close()
            self._processes[profile_id] = process
            profile = self._mark_observed(profile, state="starting", pid=process.pid, log_path=str(log_path), desired_state="running")
            if wait_for_http_health(bullpen_port):
                profile = self._mark_observed(profile, state="healthy", pid=process.pid, log_path=str(log_path), desired_state="running")
            else:
                profile = self._mark_observed(profile, state="needs-attention", pid=process.pid, log_path=str(log_path), desired_state="running")
            self._emit_update()
            return profile

    def stop(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            profile = self.registry.get(profile_id)
            process = self._processes.get(profile_id)
            pid = (profile.get("observed") or {}).get("pid")
            if process and process.poll() is None:
                self._terminate_process(process)
            elif pid:
                self._terminate_pid(int(pid))
            profile = self._mark_observed(profile, state="stopped", pid=None, desired_state="stopped")
            self._processes.pop(profile_id, None)
            self._emit_update()
            return profile

    def restart(self, profile_id: str) -> dict[str, Any]:
        self.stop(profile_id)
        return self.start(profile_id)

    def reconcile(self) -> list[dict[str, Any]]:
        updated = []
        for profile in self.registry.profiles():
            if profile.get("runtime") != "local":
                updated.append(profile)
                continue
            observed = profile.get("observed") or {}
            pid = observed.get("pid")
            port = int((profile.get("ports") or {}).get("bullpen") or DEFAULT_BULLPEN_PORT)
            state = "stopped"
            if pid and self._pid_running(int(pid)):
                state = "healthy" if wait_for_http_health(port, timeout_seconds=0.5) else "unhealthy"
            profile = self._mark_observed(profile, state=state, pid=pid if state != "stopped" else None)
            if profile.get("desiredState") == "running" and profile.get("startup", {}).get("autoStartWhenManagerStarts") and state == "stopped":
                try:
                    profile = self.start(profile["id"])
                except ManagerError as exc:
                    profile = self._mark_observed(profile, state="needs-attention", last_error=str(exc))
            updated.append(profile)
        self._emit_update()
        return updated

    def _mark_observed(
        self,
        profile: dict[str, Any],
        *,
        state: str,
        pid: int | None = None,
        log_path: str | None = None,
        last_error: str | None = None,
        desired_state: str | None = None,
    ) -> dict[str, Any]:
        return _mark_profile_observed(
            self.registry,
            profile,
            state=state,
            pid=pid,
            log_path=log_path,
            last_error=last_error,
            desired_state=desired_state,
        )

    def _emit_update(self) -> None:
        if self.socketio:
            self.socketio.emit("manager:updated", {"profiles": profiles_payload(self.registry)})

    def _terminate_process(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return
            time.sleep(0.1)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _terminate_pid(self, pid: int) -> None:
        if not self._pid_running(pid):
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return

    def _pid_running(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


class MicrosandboxRuntimeController:
    """Start and stop Bullpen instances through deploy-sandbox.py."""

    def __init__(self, registry: ProfileRegistry, socketio: SocketIO | None = None):
        self.registry = registry
        self.socketio = socketio
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen] = {}
        self._pty_sessions: dict[str, dict[str, Any]] = {}

    def build_argv(self, profile: dict[str, Any], *, provider_setup: str = "skip") -> list[str]:
        workspace = Path(profile.get("workspaceRoot") or "").expanduser()
        if not workspace.is_dir():
            raise ManagerError(f"Workspace path does not exist: {workspace}")
        deploy_script = repo_root() / "deploy-sandbox.py"
        if not deploy_script.is_file():
            raise ManagerError(f"deploy-sandbox.py not found: {deploy_script}")
        ports = profile.get("ports") or {}
        auth = profile.get("auth") or {}
        resources = profile.get("resources") or {}
        admin_password = auth.get("adminPassword")
        if not admin_password:
            raise ManagerError("Microsandbox profiles require auth.adminPassword until secret storage is implemented")
        return [
            sys.executable,
            str(deploy_script),
            "--workspace-root",
            str(workspace),
            "--sandbox-name",
            str(profile.get("sandboxName") or profile.get("id")),
            "--bullpen-port",
            str(int(ports.get("bullpen") or DEFAULT_BULLPEN_PORT)),
            "--app-port",
            str(int(ports.get("app") or DEFAULT_APP_PORT)),
            "--admin-user",
            str(auth.get("adminUser") or "admin"),
            "--admin-password",
            str(admin_password),
            "--base",
            str(profile.get("base") or DEFAULT_MICROSANDBOX_BASE),
            "--vcpus",
            str(int(resources.get("vcpus") or 4)),
            "--memory-mib",
            str(int(resources.get("memoryMiB") or 4096)),
            "--sandbox-home",
            str(Path(profile.get("sandboxHome") or profile.get("instanceHome")).expanduser()),
            "--replace",
            "--no-open",
            "--provider-setup",
            provider_setup,
        ]

    def start(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            profile = self.registry.get(profile_id)
            if profile.get("runtime") != "microsandbox":
                raise ManagerError("Microsandbox controller can only start microsandbox profiles")
            process = self._processes.get(profile_id)
            if process and process.poll() is None:
                return _mark_profile_observed(self.registry, profile, state="starting", desired_state="running")
            ports = profile.get("ports") or {}
            bullpen_port = int(ports.get("bullpen") or DEFAULT_BULLPEN_PORT)
            if is_port_listening(bullpen_port) and not wait_for_http_health(bullpen_port, timeout_seconds=1.0):
                raise ManagerError(f"Port {bullpen_port} is already occupied")

            instance_home = Path(profile.get("instanceHome") or self.registry.paths.home / "instances" / profile_id / "home").expanduser()
            logs_dir = instance_home / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = logs_dir / "manager-microsandbox.log"
            argv = self.build_argv(profile)
            profile = _mark_profile_observed(
                self.registry,
                profile,
                state="starting",
                log_path=str(log_path),
                desired_state="running",
            )
            self._emit_update()
            env = os.environ.copy()
            env["BULLPEN_MANAGER_PROFILE_ID"] = profile_id
            thread = threading.Thread(
                target=self._deploy_background,
                args=(profile_id, argv, env, log_path, bullpen_port),
                daemon=True,
            )
            thread.start()
            return profile

    def setup_providers(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            profile = self.registry.get(profile_id)
            if profile.get("runtime") != "microsandbox":
                raise ManagerError("Provider setup is only available for microsandbox profiles")
            active = self.active_setup_session(profile_id)
            if active.get("sessionId"):
                return active

            ports = profile.get("ports") or {}
            bullpen_port = int(ports.get("bullpen") or DEFAULT_BULLPEN_PORT)
            instance_home = Path(profile.get("instanceHome") or self.registry.paths.home / "instances" / profile_id / "home").expanduser()
            logs_dir = instance_home / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = logs_dir / "provider-setup.log"
            argv = self.build_argv(profile, provider_setup="interactive")
            env = os.environ.copy()
            env["BULLPEN_MANAGER_PROFILE_ID"] = profile_id
            session_id = uuid.uuid4().hex
            master_fd, slave_fd = pty.openpty()
            self._resize_pty_fd(slave_fd, cols=120, rows=30)
            log_file = open(log_path, "w", encoding="utf-8")
            try:
                log_file.write(f"\n[{now_ts()}] {' '.join(self._redacted_argv(argv))}\n")
                log_file.flush()
                process = subprocess.Popen(
                    argv,
                    cwd=str(repo_root()),
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    env=env,
                    start_new_session=True,
                    close_fds=True,
                )
            except Exception:
                log_file.close()
                os.close(master_fd)
                os.close(slave_fd)
                raise
            os.close(slave_fd)
            self._pty_sessions[session_id] = {
                "profile_id": profile_id,
                "master_fd": master_fd,
                "process": process,
                "log_path": str(log_path),
                "bullpen_port": bullpen_port,
            }
            profile = _mark_profile_observed(
                self.registry,
                profile,
                state="setup-running",
                pid=process.pid,
                log_path=str(log_path),
                desired_state="running",
            )
            self._emit_update()
            thread = threading.Thread(
                target=self._read_pty_session,
                args=(session_id, log_file),
                daemon=True,
            )
            thread.start()
            return {"sessionId": session_id, "profile": profile}

    def active_setup_session(self, profile_id: str) -> dict[str, Any]:
        profile = self.registry.get(profile_id)
        if profile.get("runtime") != "microsandbox":
            raise ManagerError("Provider setup is only available for microsandbox profiles")
        with self._lock:
            for session_id, session in self._pty_sessions.items():
                process = session.get("process")
                if session.get("profile_id") == profile_id and process and process.poll() is None:
                    return {
                        "sessionId": session_id,
                        "profile": profile,
                        "logPath": session.get("log_path"),
                    }
        return {"sessionId": "", "profile": profile}

    def write_pty(self, session_id: str, data: str) -> None:
        with self._lock:
            session = self._pty_sessions.get(session_id)
            if not session:
                raise ManagerError("Unknown or completed provider setup session")
            process = session.get("process")
            if process and process.poll() is not None:
                raise ManagerError("Provider setup session is no longer running")
            master_fd = int(session["master_fd"])
        if data:
            os.write(master_fd, data.encode("utf-8"))

    def resize_pty(self, session_id: str, cols: int, rows: int) -> None:
        cols = max(20, min(int(cols or 120), 300))
        rows = max(5, min(int(rows or 30), 120))
        with self._lock:
            session = self._pty_sessions.get(session_id)
            if not session:
                raise ManagerError("Unknown or completed provider setup session")
            process = session.get("process")
            if process and process.poll() is not None:
                raise ManagerError("Provider setup session is no longer running")
            master_fd = int(session["master_fd"])
        self._resize_pty_fd(master_fd, cols=cols, rows=rows)

    @staticmethod
    def _resize_pty_fd(fd: int, *, cols: int, rows: int) -> None:
        winsize = struct.pack("HHHH", int(rows), int(cols), 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    def stop(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            profile = self.registry.get(profile_id)
            if profile.get("runtime") != "microsandbox":
                raise ManagerError("Microsandbox controller can only stop microsandbox profiles")
            for session in list(self._pty_sessions.values()):
                process = session.get("process")
                if session.get("profile_id") == profile_id and process and process.poll() is None:
                    self._terminate_process(process)
            process = self._processes.pop(profile_id, None)
            if process and process.poll() is None:
                self._terminate_process(process)
            try:
                self._stop_sandbox(str(profile.get("sandboxName") or profile_id))
            except Exception as exc:
                profile = _mark_profile_observed(
                    self.registry,
                    profile,
                    state="needs-attention",
                    last_error=f"Microsandbox stop failed: {exc}",
                )
                self._emit_update()
                raise ManagerError(str(exc)) from exc
            profile = _mark_profile_observed(self.registry, profile, state="stopped", desired_state="stopped")
            self._emit_update()
            return profile

    def restart(self, profile_id: str) -> dict[str, Any]:
        try:
            self.stop(profile_id)
        except ManagerError:
            pass
        return self.start(profile_id)

    def reconcile(self) -> list[dict[str, Any]]:
        updated = []
        for profile in self.registry.profiles():
            if profile.get("runtime") != "microsandbox":
                updated.append(profile)
                continue
            setup_pid = self._profile_setup_pid(profile.get("id"))
            if setup_pid:
                profile = _mark_profile_observed(self.registry, profile, state="setup-running", pid=setup_pid)
                updated.append(profile)
                continue
            observed_state = (profile.get("observed") or {}).get("state")
            if observed_state == "setup-running":
                profile = _mark_profile_observed(
                    self.registry,
                    profile,
                    state="needs-attention",
                    last_error=(
                        "Provider setup was interrupted while Bullpen Manager was not connected. "
                        "Use Setup Providers to retry, or Stop to clean up the sandbox."
                    ),
                )
                updated.append(profile)
                continue
            port = int((profile.get("ports") or {}).get("bullpen") or DEFAULT_BULLPEN_PORT)
            if wait_for_http_health(port, timeout_seconds=0.5):
                state = "healthy"
            elif is_port_listening(port):
                state = "unhealthy"
            else:
                state = "stopped"
            profile = _mark_profile_observed(self.registry, profile, state=state)
            if profile.get("desiredState") == "running" and profile.get("startup", {}).get("autoStartWhenManagerStarts") and state == "stopped":
                try:
                    profile = self.start(profile["id"])
                except ManagerError as exc:
                    profile = _mark_profile_observed(self.registry, profile, state="needs-attention", last_error=str(exc))
            updated.append(profile)
        self._emit_update()
        return updated

    def _deploy_background(
        self,
        profile_id: str,
        argv: list[str],
        env: dict[str, str],
        log_path: Path,
        bullpen_port: int,
    ) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"\n[{now_ts()}] {' '.join(self._redacted_argv(argv))}\n")
                process = subprocess.Popen(
                    argv,
                    cwd=str(repo_root()),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    env=env,
                    start_new_session=True,
                )
                self._processes[profile_id] = process
                returncode = process.wait()
            profile = self.registry.get(profile_id)
            self._processes.pop(profile_id, None)
            if profile.get("desiredState") == "stopped":
                _mark_profile_observed(
                    self.registry,
                    profile,
                    state="stopped",
                    log_path=str(log_path),
                    desired_state="stopped",
                )
                self._emit_update()
                return
            if returncode != 0:
                _mark_profile_observed(
                    self.registry,
                    profile,
                    state="needs-attention",
                    log_path=str(log_path),
                    last_error=f"deploy-sandbox.py exited with {returncode}",
                    desired_state="running",
                )
                self._emit_update()
                return
            state = "healthy" if wait_for_http_health(bullpen_port, timeout_seconds=8) else "unhealthy"
            _mark_profile_observed(
                self.registry,
                profile,
                state=state,
                log_path=str(log_path),
                desired_state="running",
            )
            self._emit_update()
        except Exception as exc:
            try:
                profile = self.registry.get(profile_id)
                _mark_profile_observed(
                    self.registry,
                    profile,
                    state="needs-attention",
                    log_path=str(log_path),
                    last_error=str(exc),
                    desired_state="running",
                )
                self._emit_update()
            except Exception:
                pass

    def _profile_setup_pid(self, profile_id: str | None) -> int | None:
        for session in self._pty_sessions.values():
            process = session.get("process")
            if session.get("profile_id") == profile_id and process and process.poll() is None:
                return int(process.pid)
        return None

    def _read_pty_session(self, session_id: str, log_file: Any) -> None:
        session = self._pty_sessions.get(session_id)
        if not session:
            log_file.close()
            return
        master_fd = int(session["master_fd"])
        process = session["process"]
        profile_id = str(session["profile_id"])
        bullpen_port = int(session["bullpen_port"])
        log_path = str(session["log_path"])
        try:
            while True:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                log_file.write(text)
                log_file.flush()
                if self.socketio:
                    self.socketio.emit(
                        "manager:pty-output",
                        {"sessionId": session_id, "profileId": profile_id, "text": text},
                    )
            returncode = process.wait()
            try:
                profile = self.registry.get(profile_id)
                if profile.get("desiredState") == "stopped":
                    state = "stopped"
                    last_error = None
                elif returncode == 0:
                    state = "healthy" if wait_for_http_health(bullpen_port, timeout_seconds=8) else "unhealthy"
                    last_error = None
                else:
                    state = "needs-attention"
                    last_error = f"Provider setup exited with {returncode}"
                _mark_profile_observed(
                    self.registry,
                    profile,
                    state=state,
                    log_path=log_path,
                    last_error=last_error,
                    desired_state=profile.get("desiredState"),
                )
            except Exception:
                pass
            if self.socketio:
                self.socketio.emit(
                    "manager:pty-exit",
                    {"sessionId": session_id, "profileId": profile_id, "returncode": returncode},
                )
                self._emit_update()
        finally:
            with self._lock:
                self._pty_sessions.pop(session_id, None)
            try:
                os.close(master_fd)
            except OSError:
                pass
            log_file.close()

    def _terminate_process(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return
            time.sleep(0.1)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _stop_sandbox(self, sandbox_name: str) -> None:
        module = _load_deploy_sandbox_module()

        async def _stop() -> None:
            runtime = module.MicrosandboxRuntime()
            await runtime.ensure_installed()
            await runtime.stop(sandbox_name)
            try:
                await runtime.remove(sandbox_name)
            except Exception:
                pass

        asyncio.run(_stop())

    def _redacted_argv(self, argv: list[str]) -> list[str]:
        redacted = []
        skip_next = False
        for index, arg in enumerate(argv):
            if skip_next:
                redacted.append("[REDACTED]")
                skip_next = False
                continue
            redacted.append(arg)
            if arg == "--admin-password" and index < len(argv) - 1:
                skip_next = True
        return redacted

    def _emit_update(self) -> None:
        if self.socketio:
            self.socketio.emit("manager:updated", {"profiles": profiles_payload(self.registry)})


class InstanceRuntimeController:
    """Dispatch profile lifecycle actions to runtime-specific controllers."""

    def __init__(self, registry: ProfileRegistry, socketio: SocketIO | None = None):
        self.registry = registry
        self.local = LocalRuntimeController(registry, socketio=socketio)
        self.microsandbox = MicrosandboxRuntimeController(registry, socketio=socketio)

    def _controller_for(self, profile_id: str):
        profile = self.registry.get(profile_id)
        runtime = profile.get("runtime") or "local"
        if runtime == "local":
            return self.local
        if runtime == "microsandbox":
            return self.microsandbox
        raise ManagerError(f"{runtime} profiles are specified but not implemented")

    def start(self, profile_id: str) -> dict[str, Any]:
        return self._controller_for(profile_id).start(profile_id)

    def stop(self, profile_id: str) -> dict[str, Any]:
        return self._controller_for(profile_id).stop(profile_id)

    def restart(self, profile_id: str) -> dict[str, Any]:
        return self._controller_for(profile_id).restart(profile_id)

    def setup_providers(self, profile_id: str) -> dict[str, Any]:
        profile = self.registry.get(profile_id)
        if profile.get("runtime") != "microsandbox":
            raise ManagerError("Provider setup is only available for microsandbox profiles")
        return self.microsandbox.setup_providers(profile_id)

    def active_setup_session(self, profile_id: str) -> dict[str, Any]:
        profile = self.registry.get(profile_id)
        if profile.get("runtime") != "microsandbox":
            raise ManagerError("Provider setup is only available for microsandbox profiles")
        return self.microsandbox.active_setup_session(profile_id)

    def write_pty(self, session_id: str, data: str) -> None:
        self.microsandbox.write_pty(session_id, data)

    def resize_pty(self, session_id: str, cols: int, rows: int) -> None:
        self.microsandbox.resize_pty(session_id, cols=cols, rows=rows)

    def reconcile(self) -> list[dict[str, Any]]:
        self.local.reconcile()
        self.microsandbox.reconcile()
        return self.registry.profiles()


def _load_deploy_sandbox_module():
    path = repo_root() / "deploy-sandbox.py"
    spec = importlib.util.spec_from_file_location("bullpen_deploy_sandbox", path)
    if spec is None or spec.loader is None:
        raise ManagerError(f"Cannot load deploy-sandbox.py from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def create_profile(registry: ProfileRegistry, payload: dict[str, Any]) -> dict[str, Any]:
    runtime = (payload.get("runtime") or "local").strip().lower()
    if runtime not in {"local", "microsandbox", "docker"}:
        raise ManagerError("runtime must be local, microsandbox, or docker")
    if runtime == "docker":
        raise ManagerError("docker profiles are specified but deferred")

    display_name = (payload.get("displayName") or payload.get("name") or f"{runtime.title()} Bullpen").strip()
    profile_id = slugify(payload.get("id") or display_name)
    existing_ids = {profile.get("id") for profile in registry.profiles()}
    if profile_id in existing_ids:
        profile_id = f"{profile_id}-{uuid.uuid4().hex[:6]}"

    workspace = Path(payload.get("workspaceRoot") or "").expanduser()
    if not workspace.is_dir():
        raise ManagerError("workspaceRoot must be an existing directory")
    source = Path(payload.get("bullpenSource") or str(repo_root())).expanduser()
    if runtime == "local" and not (source / "bullpen.py").is_file():
        raise ManagerError("bullpenSource must contain bullpen.py")

    allocator = PortAllocator(registry)
    ports = payload.get("ports") if isinstance(payload.get("ports"), dict) else allocator.allocate()
    now = now_ts()
    instance_home = registry.paths.home / "instances" / profile_id / "home"
    profile = _base_profile(
        profile_id=profile_id,
        display_name=display_name,
        runtime=runtime,
        workspace=workspace,
        instance_home=instance_home,
        ports=ports,
        auto_start=bool(payload.get("autoStartWhenManagerStarts", False)),
        created_at=now,
    )
    if runtime == "local":
        profile["process"] = {"python": sys.executable, "bullpenSource": str(source.resolve())}
    elif runtime == "microsandbox":
        admin_password = str(payload.get("adminPassword") or "").strip()
        if not admin_password:
            raise ManagerError("adminPassword is required for microsandbox profiles")
        sandbox_name = slugify(payload.get("sandboxName") or f"bullpen-{profile_id}", fallback=f"bullpen-{profile_id}")
        sandbox_home = Path(payload.get("sandboxHome") or instance_home).expanduser()
        profile.update(
            {
                "sandboxName": sandbox_name,
                "base": str(payload.get("base") or DEFAULT_MICROSANDBOX_BASE),
                "sandboxHome": str(sandbox_home),
                "auth": {
                    "adminUser": str(payload.get("adminUser") or "admin"),
                    "adminPassword": admin_password,
                    "storage": "plaintext-mvp",
                },
                "resources": {
                    "vcpus": positive_int_payload(payload, "vcpus", 4),
                    "memoryMiB": positive_int_payload(payload, "memoryMiB", 4096),
                },
            }
        )
    registry.upsert(profile)
    return profile


def _base_profile(
    *,
    profile_id: str,
    display_name: str,
    runtime: str,
    workspace: Path,
    instance_home: Path,
    ports: dict[str, Any],
    auto_start: bool,
    created_at: str,
) -> dict[str, Any]:
    profile = {
        "schemaVersion": 1,
        "id": profile_id,
        "displayName": display_name,
        "runtime": runtime,
        "desiredState": "stopped",
        "workspaceRoot": str(workspace.resolve()),
        "instanceHome": str(instance_home),
        "ports": {"bullpen": int(ports["bullpen"]), "app": int(ports["app"])},
        "portReservation": {"owner": profile_id, "updatedAt": created_at, "source": "auto"},
        "startup": {
            "autoStartWhenManagerStarts": auto_start,
            "restartIfUnhealthy": True,
            "openBrowserOnManualStart": True,
        },
        "observed": {"state": "stopped", "updatedAt": created_at},
        "createdAt": created_at,
        "updatedAt": created_at,
    }
    return profile


def create_manager_app(
    *,
    home: Path | None = None,
    websocket_debug: bool = False,
) -> tuple[Flask, SocketIO]:
    registry = ProfileRegistry(home)
    socketio = SocketIO(
        async_mode="threading",
        logger=websocket_debug,
        engineio_logger=websocket_debug,
        cors_allowed_origins=[],
    )
    runtime = InstanceRuntimeController(registry, socketio=socketio)
    app = Flask(__name__, static_folder=None)
    app.config["MANAGER_REGISTRY"] = registry
    app.config["MANAGER_RUNTIME"] = runtime

    @app.route("/")
    def index():
        return send_from_directory(registry.paths.static_dir, "index.html")

    @app.route("/manager.css")
    def manager_css():
        return send_from_directory(registry.paths.static_dir, "manager.css")

    @app.route("/manager.js")
    def manager_js():
        return send_from_directory(registry.paths.static_dir, "manager.js")

    @app.route("/vendor/xterm/<path:filename>")
    def xterm_vendor(filename):
        return send_from_directory(str(registry.paths.static_dir / "vendor" / "xterm"), filename)

    @app.route("/health")
    def health():
        return jsonify({"ok": True})

    @app.route("/favicon.ico")
    def favicon():
        return ("", 204)

    @app.route("/api/profiles", methods=["GET"])
    def api_profiles():
        runtime.reconcile()
        return jsonify({"profiles": profiles_payload(registry)})

    @app.route("/api/microsandbox/base-snapshots")
    def api_microsandbox_base_snapshots():
        try:
            return jsonify({"snapshots": microsandbox_base_snapshots_payload()})
        except ManagerError as exc:
            return jsonify({"error": str(exc), "snapshots": []}), 503

    @app.route("/api/profiles", methods=["POST"])
    def api_create_profile():
        try:
            profile = create_profile(registry, request.get_json(silent=True) or {})
            socketio.emit("manager:updated", {"profiles": profiles_payload(registry)})
            return jsonify({"profile": profile_payload(profile)}), 201
        except ManagerError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/profiles/<profile_id>", methods=["DELETE"])
    def api_delete_profile(profile_id):
        try:
            runtime.stop(profile_id)
        except ManagerError:
            pass
        try:
            registry.delete(profile_id)
            socketio.emit("manager:updated", {"profiles": profiles_payload(registry)})
            return jsonify({"ok": True})
        except ManagerError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.route("/api/profiles/<profile_id>/<action>", methods=["POST"])
    def api_profile_action(profile_id, action):
        try:
            if action == "start":
                profile = runtime.start(profile_id)
            elif action == "stop":
                profile = runtime.stop(profile_id)
            elif action == "restart":
                profile = runtime.restart(profile_id)
            elif action == "open":
                profile = registry.get(profile_id)
                port = int((profile.get("ports") or {}).get("bullpen") or DEFAULT_BULLPEN_PORT)
                webbrowser.open(f"http://{LOCALHOST}:{port}")
            else:
                return jsonify({"error": f"Unknown action: {action}"}), 404
            return jsonify({"profile": profile_payload(profile)})
        except ManagerError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/profiles/<profile_id>/setup-providers/start", methods=["POST"])
    def api_setup_providers(profile_id):
        try:
            result = runtime.setup_providers(profile_id)
            if isinstance(result.get("profile"), dict):
                result = dict(result)
                result["profile"] = profile_payload(result["profile"])
            return jsonify(result)
        except ManagerError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/profiles/<profile_id>/setup-providers/session")
    def api_setup_provider_session(profile_id):
        try:
            result = runtime.active_setup_session(profile_id)
            if isinstance(result.get("profile"), dict):
                result = dict(result)
                result["profile"] = profile_payload(result["profile"])
            return jsonify(result)
        except ManagerError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/profiles/<profile_id>/logs")
    def api_profile_logs(profile_id):
        try:
            profile = registry.get(profile_id)
        except ManagerError as exc:
            return jsonify({"error": str(exc)}), 404
        log_path = (profile.get("observed") or {}).get("logPath")
        if not log_path:
            log_path = str(Path(profile.get("instanceHome") or "") / "logs" / "bullpen.log")
        path = Path(log_path).expanduser()
        if not path.exists():
            return jsonify({"text": ""})
        text = path.read_text(encoding="utf-8", errors="replace")
        return jsonify({"text": text[-20000:]})

    @app.route("/api/ports")
    def api_ports():
        allocator = PortAllocator(registry)
        return jsonify({
            "profiles": [
                {"id": profile.get("id"), "ports": allocator.classify_profile_ports(profile)}
                for profile in registry.profiles()
            ]
        })

    @socketio.on("connect")
    def on_connect():
        socketio.emit("manager:updated", {"profiles": profiles_payload(registry)})

    @socketio.on("manager:pty-input")
    def on_pty_input(payload):
        try:
            data = payload or {}
            runtime.write_pty(str(data.get("sessionId") or ""), str(data.get("data") or ""))
        except ManagerError as exc:
            socketio.emit("manager:error", {"error": str(exc)})

    @socketio.on("manager:pty-resize")
    def on_pty_resize(payload):
        try:
            data = payload or {}
            runtime.resize_pty(
                str(data.get("sessionId") or ""),
                int(data.get("cols") or 120),
                int(data.get("rows") or 30),
            )
        except (ManagerError, TypeError, ValueError) as exc:
            socketio.emit("manager:error", {"error": str(exc)})

    socketio.init_app(app)
    runtime.reconcile()
    return app, socketio
