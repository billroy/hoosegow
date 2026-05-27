"""Toady sandbox lifecycle service."""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from typing import Any

from server.microsandbox_runtime import (
    BASE_DEFAULT,
    GUEST_NOFILE_DEFAULT,
    HOST_NOFILE_DEFAULT,
    MEMORY_MIB_DEFAULT,
    NETWORK_MAX_CONNECTIONS_DEFAULT,
    VCPUS_DEFAULT,
    MicrosandboxRuntime,
    ToadyRuntimeError,
    ToadySandboxSpec,
    ensure_host_ports_available,
    host_port_in_use,
)
from server.sandbox_bootstrap import (
    build_runtime_env,
    configure_codex_cli,
    disable_guest_ipv6_for_claude,
    prepare_runtime_dirs,
    start_pty_controller,
    verify_mount_access,
)
from server.sandbox_store import SCHEMA_VERSION, SandboxManifest, SandboxStore
from server.toady_validation import ValidationError, normalize_browse_roots, validate_slug, validate_workspace_path
from server.toady_validation import parse_port_pool


class SandboxServiceError(RuntimeError):
    """User-facing sandbox lifecycle error."""


class SandboxService:
    def __init__(
        self,
        *,
        home: str,
        browse_roots: list[str] | None = None,
        source_root: str | None = None,
        base: str = BASE_DEFAULT,
        default_vcpus: int = VCPUS_DEFAULT,
        default_memory_mib: int = MEMORY_MIB_DEFAULT,
        host_nofile: int = HOST_NOFILE_DEFAULT,
        guest_nofile: int = GUEST_NOFILE_DEFAULT,
        network_max_connections: int = NETWORK_MAX_CONNECTIONS_DEFAULT,
        port_pool: str = "3000-3099",
    ) -> None:
        self.home = os.path.abspath(os.path.expanduser(home))
        self.store = SandboxStore(self.home)
        self.browse_roots = normalize_browse_roots(browse_roots)
        self.source_root = Path(source_root or Path(__file__).resolve().parents[1]).resolve()
        self.base = base
        self.default_vcpus = default_vcpus
        self.default_memory_mib = default_memory_mib
        self.host_nofile = host_nofile
        self.guest_nofile = guest_nofile
        self.network_max_connections = network_max_connections
        self.port_pool = parse_port_pool(port_pool)

    def list(self) -> list[dict[str, Any]]:
        return [manifest.to_dict() for manifest in self.store.list()]

    def get(self, slug: str) -> dict[str, Any] | None:
        slug = validate_slug(slug)
        manifest = self.store.get(slug)
        return None if manifest is None else manifest.to_dict()

    def create_manifest(self, payload: dict[str, Any]) -> dict[str, Any]:
        slug = validate_slug(str(payload.get("slug") or payload.get("name") or ""))
        if self.store.exists(slug):
            raise SandboxServiceError(f"Sandbox already exists: {slug}")
        canonical_workspace_path, warnings = validate_workspace_path(
            str(payload.get("workspace_root") or payload.get("workspace_path") or ""),
            browse_roots=self.browse_roots,
            state_home=self.home,
        )
        if warnings and not payload.get("confirmed_sensitive_workspace"):
            raise SandboxServiceError("Workspace confirmation required: " + "; ".join(warnings))

        home_path = self.store.sandbox_home_path(slug)
        controller_token = secrets.token_urlsafe(32)
        controller_host_port = self._allocate_port()
        manifest = SandboxManifest(
            schema_version=SCHEMA_VERSION,
            slug=slug,
            name=str(payload.get("display_name") or payload.get("name") or slug),
            workspace_path=str(payload.get("workspace_root") or payload.get("workspace_path") or ""),
            canonical_workspace_path=canonical_workspace_path,
            home_path=home_path,
            vcpus=int(payload.get("vcpus") or self.default_vcpus),
            memory_mib=int(payload.get("memory_mib") or self.default_memory_mib),
            controller={
                "guest_port": int(payload.get("controller_guest_port") or 5859),
                "host_port": int(payload.get("controller_host_port") or controller_host_port),
                "token": controller_token,
                "transport": "http-long-poll",
            },
            last_status="configured",
            warnings=warnings,
        )
        return self.store.save(manifest).to_dict()

    def _allocate_port(self, *, exclude: set[int] | None = None) -> int:
        exclude = set(exclude or set())
        used = set(exclude)
        for manifest in self.store.list():
            controller = manifest.controller or {}
            if controller.get("host_port"):
                used.add(int(controller["host_port"]))
            for mapping in manifest.published_ports:
                if mapping.get("host_port"):
                    used.add(int(mapping["host_port"]))
        start, end = self.port_pool
        for port in range(start, end + 1):
            if port in used:
                continue
            if not host_port_in_use(port):
                return port
        raise SandboxServiceError(f"No free ports in Toady port pool {start}-{end}.")

    async def start(self, slug: str) -> dict[str, Any]:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")
        controller = dict(manifest.controller or {})
        host_port = controller.get("host_port")
        guest_port = int(controller.get("guest_port") or 5859)
        if not host_port:
            raise SandboxServiceError("Sandbox controller host port allocation is not implemented yet.")
        ports = {int(host_port): guest_port}
        for mapping in manifest.published_ports:
            ports[int(mapping["host_port"])] = int(mapping["guest_port"])
        ensure_host_ports_available(list(ports))

        spec = ToadySandboxSpec(
            sandbox_name=manifest.slug,
            workspace=Path(manifest.canonical_workspace_path),
            source_root=self.source_root,
            sandbox_home=Path(manifest.home_path),
            base=self.base,
            vcpus=manifest.vcpus,
            memory_mib=manifest.memory_mib,
            host_nofile=self.host_nofile,
            guest_nofile=self.guest_nofile,
            network_max_connections=self.network_max_connections,
            ports=ports,
        )
        build_runtime_env(spec, controller_port=guest_port, controller_token=str(controller.get("token") or ""))
        runtime = MicrosandboxRuntime()
        await runtime.ensure_installed()
        sandbox = await runtime.create(spec)
        try:
            await prepare_runtime_dirs(sandbox, spec)
            await disable_guest_ipv6_for_claude(sandbox)
            await verify_mount_access(sandbox, spec)
            await configure_codex_cli(sandbox, spec)
            await start_pty_controller(sandbox, spec)
        except ToadyRuntimeError:
            manifest.last_status = "error"
            self.store.save(manifest)
            raise

        manifest.last_status = "running"
        manifest.microsandbox_id = manifest.slug
        manifest.updated_at = time.time()
        return self.store.save(manifest).to_dict()

    async def stop(self, slug: str) -> dict[str, Any]:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")
        runtime = MicrosandboxRuntime()
        await runtime.stop(manifest.slug)
        manifest.last_status = "stopped"
        return self.store.save(manifest).to_dict()

    async def destroy(self, slug: str, *, purge_home: bool = False) -> bool:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            return False
        runtime = MicrosandboxRuntime()
        await runtime.stop(manifest.slug)
        await runtime.remove(manifest.slug)
        self.store.delete(manifest.slug)
        if purge_home:
            import shutil

            shutil.rmtree(os.path.dirname(manifest.home_path), ignore_errors=True)
        return True


def browse_roots_from_env() -> list[str]:
    raw = os.environ.get("TOADY_WORKSPACE_ROOTS", "")
    return [item for item in raw.split(os.pathsep) if item] if raw else []
