"""Microsandbox runtime adapter extracted from Bullpen deploy-sandbox.py."""

from __future__ import annotations

import importlib
import inspect
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace as dataclass_replace
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - unsupported host path.
    resource = None


BASE_DEFAULT = "toady-microsandbox-local"
SOURCE_IMAGE_DEFAULT = "node:22-bookworm"
VCPUS_DEFAULT = 4
MEMORY_MIB_DEFAULT = 4096
HOST_NOFILE_DEFAULT = 12000
GUEST_NOFILE_DEFAULT = 65536
NETWORK_MAX_CONNECTIONS_DEFAULT = 8192


class ToadyRuntimeError(RuntimeError):
    """User-facing Microsandbox runtime error."""


@dataclass
class ToadySandboxSpec:
    sandbox_name: str
    workspace: Path
    source_root: Path
    sandbox_home: Path
    base: str = BASE_DEFAULT
    vcpus: int = VCPUS_DEFAULT
    memory_mib: int = MEMORY_MIB_DEFAULT
    host_nofile: int = HOST_NOFILE_DEFAULT
    guest_nofile: int = GUEST_NOFILE_DEFAULT
    network_max_connections: int = NETWORK_MAX_CONNECTIONS_DEFAULT
    replace: bool = True
    ports: dict[int, int] = field(default_factory=dict)
    runtime_env: dict[str, str] = field(default_factory=dict)


async def maybe(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def detect_supported_host() -> bool:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        return machine in {"arm64", "aarch64"}
    if system == "Linux":
        return Path("/dev/kvm").exists()
    return False


def ensure_host_nofile(target: int) -> tuple[int, int]:
    if resource is None:
        print(
            f"warn: host RLIMIT_NOFILE is unavailable on this platform; target={target} was not applied",
            file=sys.stderr,
            flush=True,
        )
        return 0, 0
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft >= target:
        return soft, hard
    new_soft = target if hard == resource.RLIM_INFINITY else min(target, hard)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))
    except (OSError, ValueError) as exc:
        print(
            f"warn: could not raise host RLIMIT_NOFILE from soft={soft} hard={hard} "
            f"to target={target}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return soft, hard
    updated_soft, updated_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if updated_soft < target:
        print(
            f"warn: host RLIMIT_NOFILE is soft={updated_soft} hard={updated_hard}, "
            f"below target={target}; Microsandbox runtime may hit host-side FD pressure",
            file=sys.stderr,
            flush=True,
        )
    return updated_soft, updated_hard


def mark_open_fds_close_on_exec(exclude: set[int] | tuple[int, ...] = (0, 1, 2)) -> list[int]:
    """Prevent child runtimes from inheriting already-bound server sockets."""
    excluded = set(exclude)
    candidates: set[int] = set()
    for fd_root in ("/proc/self/fd", "/dev/fd"):
        try:
            names = os.listdir(fd_root)
        except OSError:
            continue
        for name in names:
            try:
                fd = int(name)
            except ValueError:
                continue
            if fd not in excluded:
                candidates.add(fd)
        if candidates:
            break

    marked = []
    for fd in sorted(candidates):
        try:
            if os.get_inheritable(fd):
                os.set_inheritable(fd, False)
                marked.append(fd)
        except OSError:
            continue
    return marked


def network_with_max_connections(network: Any, max_connections: int) -> Any:
    if hasattr(network, "max_connections"):
        try:
            return dataclass_replace(network, max_connections=max_connections)
        except TypeError:
            setattr(network, "max_connections", max_connections)
            return network
    raise ToadyRuntimeError(
        "The installed microsandbox SDK Network object does not expose max_connections; "
        "upgrade microsandbox before using Toady's Microsandbox runtime."
    )


def host_port_in_use(port: int) -> bool:
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                if sock.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def host_port_owner(port: int) -> str:
    if shutil.which("lsof") is None:
        return ""
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) <= 1:
        return ""
    return "\n".join(lines[:6])


def ensure_host_ports_available(ports: list[int] | tuple[int, ...] | set[int]) -> None:
    occupied = [port for port in ports if host_port_in_use(port)]
    if not occupied:
        return
    details = []
    for port in occupied:
        owner = host_port_owner(port)
        if owner:
            details.append(f"Port {port} is already listening:\n{owner}")
        else:
            details.append(f"Port {port} is already listening.")
    raise ToadyRuntimeError(
        "Cannot start Microsandbox because required host port(s) are occupied.\n"
        + "\n\n".join(details)
    )


def wait_for_host_ports_available(ports: list[int] | tuple[int, ...] | set[int], timeout_seconds: int = 10) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not any(host_port_in_use(port) for port in ports):
            return
        time.sleep(0.5)
    ensure_host_ports_available(ports)


def create_time_env(spec: ToadySandboxSpec) -> dict[str, str]:
    """Keep Sandbox.create env small; commands export the full runtime env later."""
    return {
        key: str(spec.runtime_env[key])
        for key in ("HOME", "USER", "LOGNAME")
        if key in spec.runtime_env
    }


class MicrosandboxRuntime:
    """Lazy SDK adapter copied from Bullpen's deployment path."""

    def __init__(self) -> None:
        try:
            self.module = importlib.import_module("microsandbox")
        except ImportError as exc:
            raise ToadyRuntimeError(
                "The microsandbox Python package is required. Install it with: "
                "python3 -m pip install microsandbox"
            ) from exc

        try:
            self.Sandbox = getattr(self.module, "Sandbox")
            self.Snapshot = getattr(self.module, "Snapshot")
            self.Volume = getattr(self.module, "Volume")
            self.Network = getattr(self.module, "Network")
            self.Image = getattr(self.module, "Image", None)
            self.AttachOptions = getattr(self.module, "AttachOptions", None)
            self.ExecOptions = getattr(self.module, "ExecOptions", None)
            self.Stdin = getattr(self.module, "Stdin", None)
            self.StdoutEvent = getattr(self.module, "StdoutEvent", None)
            self.StderrEvent = getattr(self.module, "StderrEvent", None)
            self.ExitedEvent = getattr(self.module, "ExitedEvent", None)
        except AttributeError as exc:
            raise ToadyRuntimeError("The installed microsandbox package is missing the expected SDK API.") from exc

    async def ensure_installed(self) -> None:
        is_installed = getattr(self.module, "is_installed", None)
        install = getattr(self.module, "install", None)
        if not callable(is_installed):
            return
        installed = await maybe(is_installed())
        if installed:
            return
        if not callable(install):
            raise ToadyRuntimeError("Microsandbox runtime is not installed and this SDK cannot install it.")
        await maybe(install())

    async def exists(self, name: str) -> bool:
        return await self.get(name) is not None

    async def get(self, name: str) -> Any | None:
        get = getattr(self.Sandbox, "get", None)
        if not callable(get):
            return None
        try:
            return await maybe(get(name))
        except Exception:
            return None

    async def stop(self, name: str) -> None:
        sandbox = await self.get(name)
        if sandbox is None:
            return
        stop = getattr(sandbox, "stop", None)
        if callable(stop):
            await maybe(stop())

    async def remove(self, name: str) -> None:
        remove = getattr(self.Sandbox, "remove", None)
        if callable(remove):
            await maybe(remove(name))

    async def status(self, name: str) -> str | None:
        sandbox = await self.get(name)
        if sandbox is None:
            return None
        status = getattr(sandbox, "status", None)
        if callable(status):
            status = await maybe(status())
        return None if status is None else str(status)

    async def get_prepared_base(self, base: str) -> Any | None:
        get = getattr(self.Snapshot, "get", None)
        if not callable(get):
            return None
        try:
            return await maybe(get(base))
        except Exception:
            return None

    async def prepared_base_exists(self, base: str) -> bool:
        return await self.get_prepared_base(base) is not None

    async def prepared_base_snapshot_path(self, base: str) -> str:
        snapshot = await self.get_prepared_base(base)
        if snapshot is None:
            raise ToadyRuntimeError(
                f"Prepared Microsandbox base '{base}' was not found. "
                "Run: python3 toady.py --prepare-base"
            )
        path = getattr(snapshot, "path", None)
        if path is None:
            open_snapshot = getattr(snapshot, "open", None)
            if callable(open_snapshot):
                opened = await maybe(open_snapshot())
                path = getattr(opened, "path", None)
        if not path:
            raise ToadyRuntimeError(f"Prepared Microsandbox base '{base}' has no local snapshot path.")
        return str(path)

    async def create(self, spec: ToadySandboxSpec) -> Any:
        prepared_base = await self.prepared_base_snapshot_path(spec.base)
        try:
            spec.sandbox_home.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ToadyRuntimeError(f"Cannot create Microsandbox home directory {spec.sandbox_home}: {exc}") from exc
        volumes = {
            "/app": self.Volume.bind(str(spec.source_root), readonly=True),
            "/workspace": self.Volume.bind(str(spec.workspace)),
            "/home/agent": self.Volume.bind(str(spec.sandbox_home)),
        }
        ensure_host_nofile(spec.host_nofile)
        network = network_with_max_connections(self.Network.allow_all(), spec.network_max_connections)
        result = self.Sandbox.create(
            spec.sandbox_name,
            snapshot=prepared_base,
            detached=True,
            replace=bool(spec.replace),
            cpus=spec.vcpus,
            memory=spec.memory_mib,
            ports=dict(spec.ports),
            volumes=volumes,
            network=network,
            env=create_time_env(spec),
        )
        return await maybe(result)

    async def create_prepare_sandbox(self, name: str, source_image: str, source: Path) -> Any:
        if self.Image is None or not hasattr(self.Image, "oci"):
            raise ToadyRuntimeError("The installed microsandbox package does not expose Image.oci().")
        result = self.Sandbox.create(
            name,
            image=self.Image.oci(source_image),
            replace=True,
            volumes={"/app": self.Volume.bind(str(source), readonly=True)},
            network=self.Network.allow_all(),
        )
        return await maybe(result)

    async def create_base_validation_sandbox(self, name: str, base: str, spec: ToadySandboxSpec) -> Any:
        prepared_base = await self.prepared_base_snapshot_path(base)
        await self.stop(name)
        try:
            await self.remove(name)
        except Exception:
            pass
        ensure_host_nofile(spec.host_nofile)
        network = network_with_max_connections(self.Network.allow_all(), spec.network_max_connections)
        result = self.Sandbox.create(
            name,
            snapshot=prepared_base,
            detached=True,
            replace=True,
            cpus=1,
            memory=1024,
            ports={},
            network=network,
            env={"HOME": "/root", "USER": "root", "LOGNAME": "root"},
        )
        return await maybe(result)

    async def create_snapshot(self, sandbox_name: str, base: str) -> None:
        result = self.Snapshot.create(
            sandbox_name,
            name=base,
            force=True,
            labels={"app": "toady", "kind": "microsandbox-base"},
        )
        await maybe(result)
