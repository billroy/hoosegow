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
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - unsupported host path.
    resource = None


BASE_DEFAULT = "hoosegow-microsandbox-0.7"
SOURCE_IMAGE_DEFAULT = "node:22-bookworm"
MICROSANDBOX_MIN_VERSION = "0.7.5"
VCPUS_DEFAULT = 4
MEMORY_MIB_DEFAULT = 4096
HOST_NOFILE_DEFAULT = 12000
GUEST_NOFILE_DEFAULT = 65536
NETWORK_MAX_CONNECTIONS_DEFAULT = 4096
NETWORK_MAX_CONNECTIONS_LIMIT = 4096


class HoosegowRuntimeError(RuntimeError):
    """User-facing Microsandbox runtime error."""


@dataclass
class HoosegowSandboxSpec:
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


def comparable_version(value: str) -> tuple[int, int, int]:
    numeric = []
    for part in value.split("."):
        digits = ""
        for char in part:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        numeric.append(int(digits))
    return tuple((numeric + [0, 0, 0])[:3])


def microsandbox_distribution_version(module: Any | None = None) -> str:
    try:
        return metadata.version("microsandbox")
    except metadata.PackageNotFoundError:
        version = getattr(module, "__version__", "")
        return str(version) if version else ""


def validate_microsandbox_version(version: str) -> None:
    if not version:
        raise HoosegowRuntimeError(
            "Could not determine the installed microsandbox package version. "
            "Install the pinned runtime with: python3 -m pip install -r requirements.txt"
        )
    if comparable_version(version) < comparable_version(MICROSANDBOX_MIN_VERSION):
        raise HoosegowRuntimeError(
            f"Hoosegow requires microsandbox >= {MICROSANDBOX_MIN_VERSION} because its runtime adapter "
            "uses grouped snapshots, restore-based startup, and later published-port fixes. "
            f"Installed version: {version}. Upgrade with: python3 -m pip install -r requirements.txt"
        )


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


def validate_network_max_connections(max_connections: int) -> int:
    if max_connections < 1 or max_connections > NETWORK_MAX_CONNECTIONS_LIMIT:
        raise HoosegowRuntimeError(
            "Microsandbox requires network max_connections to be between "
            f"1 and {NETWORK_MAX_CONNECTIONS_LIMIT}; received {max_connections}."
        )
    return max_connections


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
    raise HoosegowRuntimeError(
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


class MicrosandboxRuntime:
    """Lazy SDK adapter copied from Bullpen's deployment path."""

    def __init__(self) -> None:
        try:
            self.module = importlib.import_module("microsandbox")
        except ImportError as exc:
            raise HoosegowRuntimeError(
                "The microsandbox Python package is required. Install it with: "
                "python3 -m pip install -r requirements.txt"
            ) from exc
        validate_microsandbox_version(microsandbox_distribution_version(self.module))

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
            raise HoosegowRuntimeError("The installed microsandbox package is missing the expected SDK API.") from exc

    async def ensure_installed(self) -> None:
        is_installed = getattr(self.module, "is_installed", None)
        install = getattr(self.module, "install", None)
        if not callable(is_installed):
            return
        installed = await maybe(is_installed())
        if installed:
            return
        if not callable(install):
            raise HoosegowRuntimeError("Microsandbox runtime is not installed and this SDK cannot install it.")
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

    async def connect(self, name: str) -> Any:
        sandbox = await self.get(name)
        if sandbox is None:
            raise HoosegowRuntimeError(f"Microsandbox '{name}' was not found.")
        connect = getattr(sandbox, "connect", None)
        if callable(connect):
            return await maybe(connect())
        return sandbox

    async def get_prepared_base(self, base: str) -> Any | None:
        get = getattr(self.Snapshot, "get", None)
        if not callable(get):
            return None
        try:
            return await maybe(get(self.prepared_base_snapshot_name(base)))
        except Exception:
            return None

    @staticmethod
    def prepared_base_snapshot_name(base: str) -> str:
        """Return the Microsandbox 0.7 group-qualified prepared-base name."""
        return f"{base}:{base}"

    async def prepared_base_exists(self, base: str) -> bool:
        return await self.get_prepared_base(base) is not None

    async def prepared_base_snapshot(self, base: str) -> Any:
        snapshot = await self.get_prepared_base(base)
        if snapshot is None:
            raise HoosegowRuntimeError(
                f"Prepared Microsandbox base '{base}' was not found. "
                "Run: python3 hoosegow.py --prepare-base"
            )
        return snapshot

    async def create(self, spec: HoosegowSandboxSpec) -> Any:
        prepared_base = await self.prepared_base_snapshot(spec.base)
        try:
            spec.sandbox_home.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HoosegowRuntimeError(f"Cannot create Microsandbox home directory {spec.sandbox_home}: {exc}") from exc
        volumes = {
            "/app": self.Volume.bind(str(spec.source_root), readonly=True),
            "/workspace": self.Volume.bind(str(spec.workspace)),
            "/home/agent": self.Volume.bind(str(spec.sandbox_home)),
        }
        ensure_host_nofile(spec.host_nofile)
        network = self.Network.allow_all()
        max_connections = validate_network_max_connections(spec.network_max_connections)
        if spec.replace:
            await self.stop(spec.sandbox_name)
            try:
                await self.remove(spec.sandbox_name)
            except Exception:
                pass
        result = self.Sandbox.restore(
            prepared_base,
            name=spec.sandbox_name,
            cpus=spec.vcpus,
            memory=spec.memory_mib,
            ports=dict(spec.ports),
            volumes=volumes,
            network_policy=network.policy,
            max_tcp_connections=max_connections,
            max_udp_connections=max_connections,
        )
        return await maybe(result)

    async def create_prepare_sandbox(self, name: str, source_image: str, source: Path) -> Any:
        if self.Image is None or not hasattr(self.Image, "oci"):
            raise HoosegowRuntimeError("The installed microsandbox package does not expose Image.oci().")
        result = self.Sandbox.create(
            name,
            image=self.Image.oci(source_image),
            replace=True,
            volumes={"/app": self.Volume.bind(str(source), readonly=True)},
            network=self.Network.allow_all(),
        )
        return await maybe(result)

    async def create_base_validation_sandbox(self, name: str, base: str, spec: HoosegowSandboxSpec) -> Any:
        prepared_base = await self.prepared_base_snapshot(base)
        await self.stop(name)
        try:
            await self.remove(name)
        except Exception:
            pass
        ensure_host_nofile(spec.host_nofile)
        network = self.Network.allow_all()
        max_connections = validate_network_max_connections(spec.network_max_connections)
        result = self.Sandbox.restore(
            prepared_base,
            name=name,
            cpus=1,
            memory=1024,
            ports={},
            network_policy=network.policy,
            max_tcp_connections=max_connections,
            max_udp_connections=max_connections,
        )
        return await maybe(result)

    async def create_snapshot(self, sandbox_name: str, base: str) -> None:
        snapshot_name = self.prepared_base_snapshot_name(base)
        remove = getattr(self.Snapshot, "remove", None)
        if callable(remove):
            try:
                await maybe(remove(snapshot_name, force=True))
            except Exception:
                pass
        result = self.Snapshot.create(
            base,
            from_sandbox=sandbox_name,
            group=base,
            labels={"app": "hoosegow", "kind": "microsandbox-base"},
        )
        await maybe(result)
