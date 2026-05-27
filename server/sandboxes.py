"""Toady sandbox lifecycle service."""

from __future__ import annotations

import asyncio
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
    host_port_owner,
)
from server.sandbox_bootstrap import (
    build_runtime_env,
    configure_codex_cli,
    detach_sandbox,
    disable_guest_ipv6_for_claude,
    prepare_runtime_dirs,
    start_pty_controller,
    verify_detached_sandbox,
    verify_mount_access,
    wait_for_controller_health,
)
from server.sandbox_store import SCHEMA_VERSION, SandboxManifest, SandboxStore
from server.toady_validation import (
    ValidationError,
    ensure_descendant,
    normalize_browse_roots,
    validate_slug,
    validate_workspace_path,
)
from server.toady_validation import parse_port, parse_port_pool


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

    async def reconcile(self) -> list[dict[str, Any]]:
        runtime: MicrosandboxRuntime | None = None
        changed = False
        manifests = self.store.list()
        for manifest in manifests:
            if manifest.last_status not in {"running", "starting"}:
                continue
            if runtime is None:
                runtime = MicrosandboxRuntime()
                await runtime.ensure_installed()
            status = await runtime.status(manifest.slug)
            if status is None or "running" not in status.lower():
                manifest.last_status = "stopped"
                self.store.save(manifest)
                changed = True
        return [manifest.to_dict() for manifest in (self.store.list() if changed else manifests)]

    def get(self, slug: str) -> dict[str, Any] | None:
        slug = validate_slug(slug)
        manifest = self.store.get(slug)
        return None if manifest is None else manifest.to_dict()

    def browse_workspaces(self, path: str | None = None) -> dict[str, Any]:
        roots = list(self.browse_roots)
        if not roots:
            raise SandboxServiceError("No workspace browse roots are configured.")
        current = ensure_descendant(path or roots[0], roots)
        if not os.path.isdir(current):
            raise SandboxServiceError(f"Workspace browse path is not a directory: {path}")

        entries = []
        try:
            with os.scandir(current) as iterator:
                for entry in iterator:
                    try:
                        if not entry.is_dir(follow_symlinks=True):
                            continue
                        real_path = ensure_descendant(entry.path, roots)
                    except (OSError, ValidationError):
                        continue
                    entries.append(
                        {
                            "name": entry.name,
                            "path": real_path,
                            "basename": os.path.basename(real_path) or real_path,
                        }
                    )
        except OSError as exc:
            raise SandboxServiceError(f"Cannot browse workspace path {current}: {exc}") from exc

        entries.sort(key=lambda item: item["name"].lower())
        parent = None
        for root in roots:
            if current == root:
                break
            try:
                if os.path.commonpath([root, current]) == root:
                    parent_path = os.path.dirname(current)
                    parent = parent_path if parent_path and parent_path != current else None
                    break
            except ValueError:
                continue
        return {
            "path": current,
            "parent": parent,
            "roots": [{"name": root, "path": root} for root in roots],
            "entries": entries[:500],
            "truncated": len(entries) > 500,
        }

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

    def _host_port_reserved(self, port: int) -> bool:
        for manifest in self.store.list():
            controller = manifest.controller or {}
            if int(controller.get("host_port") or 0) == port:
                return True
            for mapping in manifest.published_ports:
                if int(mapping.get("host_port") or 0) == port:
                    return True
        return False

    def _ensure_publishable_host_port(self, port: int) -> None:
        if self._host_port_reserved(port):
            raise SandboxServiceError(f"Host port {port} is already reserved by Toady.")
        if host_port_in_use(port):
            owner = host_port_owner(port)
            detail = f"\n{owner}" if owner else ""
            raise SandboxServiceError(f"Host port {port} is already listening.{detail}")

    def list_ports(self, slug: str) -> list[dict[str, Any]]:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")
        return list(manifest.published_ports or [])

    def publish_port(self, slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")
        guest_port = parse_port("guest port", payload.get("guest_port") or payload.get("guestPort") or "")
        host_value = payload.get("host_port") or payload.get("hostPort")
        host_port = parse_port("host port", host_value) if host_value else self._allocate_port()
        self._ensure_publishable_host_port(host_port)

        for mapping in manifest.published_ports:
            if mapping.get("status") == "remove_on_restart":
                continue
            if int(mapping.get("guest_port") or 0) == guest_port:
                raise SandboxServiceError(f"Guest port {guest_port} is already published.")
            if int(mapping.get("host_port") or 0) == host_port:
                raise SandboxServiceError(f"Host port {host_port} is already published.")

        status = "pending_restart" if manifest.last_status == "running" else "active"
        mapping = {
            "guest_port": guest_port,
            "host_port": host_port,
            "status": status,
        }
        manifest.published_ports.append(mapping)
        self.store.save(manifest)
        return mapping

    def unpublish_port(self, slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")
        host_port = parse_port("host port", payload.get("host_port") or payload.get("hostPort") or "")
        kept = []
        removed: dict[str, Any] | None = None
        for mapping in manifest.published_ports:
            if int(mapping.get("host_port") or 0) != host_port:
                kept.append(mapping)
                continue
            removed = dict(mapping)
            if manifest.last_status == "running" and mapping.get("status") == "active":
                updated = dict(mapping)
                updated["status"] = "remove_on_restart"
                kept.append(updated)
                removed = updated
        if removed is None:
            raise SandboxServiceError(f"Host port {host_port} is not published.")
        manifest.published_ports = kept
        self.store.save(manifest)
        return removed

    def reassign_port(self, slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")
        old_host_port = parse_port("host port", payload.get("host_port") or payload.get("hostPort") or "")
        host_value = payload.get("new_host_port") or payload.get("newHostPort")
        new_host_port = parse_port("new host port", host_value) if host_value else self._allocate_port()
        self._ensure_publishable_host_port(new_host_port)

        updated_mapping: dict[str, Any] | None = None
        next_mappings = []
        for mapping in manifest.published_ports:
            if int(mapping.get("host_port") or 0) != old_host_port:
                next_mappings.append(mapping)
                continue
            updated_mapping = {
                "guest_port": int(mapping.get("guest_port") or 0),
                "host_port": new_host_port,
                "status": "pending_restart" if manifest.last_status == "running" else "active",
            }
            next_mappings.append(updated_mapping)
        if updated_mapping is None:
            raise SandboxServiceError(f"Host port {old_host_port} is not published.")
        manifest.published_ports = next_mappings
        self.store.save(manifest)
        return updated_mapping

    def _mark_conflicting_published_ports(self, manifest: SandboxManifest, mappings: list[dict[str, Any]]) -> None:
        conflicts = []
        next_mappings = []
        for mapping in manifest.published_ports:
            if mapping not in mappings:
                next_mappings.append(mapping)
                continue
            host_port = int(mapping.get("host_port") or 0)
            if host_port and host_port_in_use(host_port):
                owner = host_port_owner(host_port)
                conflict = dict(mapping)
                conflict["status"] = "conflict"
                if owner:
                    conflict["conflict"] = owner
                conflicts.append(conflict)
                next_mappings.append(conflict)
            else:
                clean = dict(mapping)
                clean.pop("conflict", None)
                next_mappings.append(clean)
        if not conflicts:
            return
        manifest.published_ports = next_mappings
        manifest.last_status = "error"
        self.store.save(manifest)
        detail = "; ".join(f":{mapping['host_port']} -> :{mapping['guest_port']}" for mapping in conflicts)
        raise SandboxServiceError(f"Published port conflict for {manifest.slug}: {detail}. Reassign the port and start again.")

    def _ensure_controller_endpoint(self, manifest: SandboxManifest) -> dict[str, Any]:
        controller = dict(manifest.controller or {})
        host_port = int(controller.get("host_port") or 0)
        if not host_port or host_port_in_use(host_port):
            controller["host_port"] = self._allocate_port()
        if not controller.get("guest_port"):
            controller["guest_port"] = 5859
        if not controller.get("token"):
            controller["token"] = secrets.token_urlsafe(32)
        controller["transport"] = controller.get("transport") or "http-long-poll"
        if controller != (manifest.controller or {}):
            manifest.controller = controller
            manifest.updated_at = time.time()
            self.store.save(manifest)
        return controller

    async def start(self, slug: str) -> dict[str, Any]:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")
        manifest.last_status = "starting"
        self.store.save(manifest)
        controller = self._ensure_controller_endpoint(manifest)
        host_port = int(controller["host_port"])
        guest_port = int(controller.get("guest_port") or 5859)
        ports = {host_port: guest_port}
        active_mappings = [
            mapping
            for mapping in manifest.published_ports
            if mapping.get("status") != "remove_on_restart"
        ]
        self._mark_conflicting_published_ports(manifest, active_mappings)
        manifest = self.store.get(manifest.slug) or manifest
        active_mappings = [
            mapping
            for mapping in manifest.published_ports
            if mapping.get("status") != "remove_on_restart"
        ]
        for mapping in active_mappings:
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
        try:
            sandbox = await runtime.create(spec)
            await prepare_runtime_dirs(sandbox, spec)
            await disable_guest_ipv6_for_claude(sandbox)
            await verify_mount_access(sandbox, spec)
            await configure_codex_cli(sandbox, spec)
            await start_pty_controller(sandbox, spec)
            wait_for_controller_health(int(host_port))
            await detach_sandbox(sandbox)
            await verify_detached_sandbox(runtime, spec)
        except Exception:
            manifest.last_status = "error"
            self.store.save(manifest)
            raise

        manifest.last_status = "running"
        manifest.microsandbox_id = manifest.slug
        manifest.published_ports = [
            {**mapping, "status": "active"}
            for mapping in active_mappings
        ]
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
        last_error: Exception | None = None
        for attempt in range(6):
            try:
                await runtime.remove(manifest.slug)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if "still running" not in str(exc).lower() or attempt == 5:
                    break
                await asyncio.sleep(0.5)
                await runtime.stop(manifest.slug)
        if last_error is not None:
            raise last_error
        self.store.delete(manifest.slug)
        if purge_home:
            import shutil

            shutil.rmtree(os.path.dirname(manifest.home_path), ignore_errors=True)
        return True


def browse_roots_from_env() -> list[str]:
    raw = os.environ.get("TOADY_WORKSPACE_ROOTS", "")
    return [item for item in raw.split(os.pathsep) if item] if raw else []
