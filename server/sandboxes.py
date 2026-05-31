"""Toady sandbox lifecycle service."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from server import base as toady_base
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
    mark_open_fds_close_on_exec,
)
from server.sandbox_bootstrap import (
    build_runtime_env,
    configure_codex_cli,
    detach_sandbox,
    disable_guest_ipv6_for_claude,
    prepare_runtime_dirs,
    result_output_text,
    run_sandbox_shell,
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


_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_BACKUPS = 5
CLOCK_DRIFT_WARNING_SECONDS = 30
CLOCK_SYNC_TARGET_SECONDS = 5


def _host_memory_mib() -> int | None:
    if not hasattr(os, "sysconf"):
        return None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        return None
    if not isinstance(pages, int) or not isinstance(page_size, int):
        return None
    return max(1, int((pages * page_size) / (1024 * 1024)))


def _positive_int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SandboxServiceError("Resource admission limits must be positive integers.") from exc
    if parsed < 1:
        raise SandboxServiceError("Resource admission limits must be positive integers.")
    return parsed


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
        max_sandboxes: int | None = 8,
        max_total_vcpus: int | None = None,
        max_total_memory_mib: int | None = None,
        clock_drift_warning_seconds: int = CLOCK_DRIFT_WARNING_SECONDS,
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
        self.max_sandboxes = _positive_int_or_none(max_sandboxes)
        detected_vcpus = os.cpu_count() or self.default_vcpus
        self.max_total_vcpus = _positive_int_or_none(max_total_vcpus) or max(detected_vcpus, self.default_vcpus)
        host_memory_mib = _host_memory_mib()
        self.max_total_memory_mib = _positive_int_or_none(max_total_memory_mib) or (
            max(int(host_memory_mib * 0.75), self.default_memory_mib) if host_memory_mib else None
        )
        self.clock_drift_warning_seconds = clock_drift_warning_seconds

    def _base_metadata_path(self) -> Path:
        return toady_base.base_metadata_path(self.home, self.base)

    def _sandbox_log_path(self, slug: str) -> str:
        return os.path.join(self.home, "logs", f"sandbox-{slug}.log")

    def _rotate_log_if_needed(self, path: str) -> None:
        try:
            if not os.path.exists(path) or os.path.getsize(path) <= _LOG_MAX_BYTES:
                return
        except OSError:
            return
        for index in range(_LOG_BACKUPS - 1, 0, -1):
            src = f"{path}.{index}"
            dst = f"{path}.{index + 1}"
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except OSError:
                    pass
        try:
            os.replace(path, f"{path}.1")
        except OSError:
            pass

    def _log_sandbox_event(self, slug: str, event: str, **fields: Any) -> None:
        try:
            validate_slug(slug)
        except ValidationError:
            return
        logs_dir = os.path.join(self.home, "logs")
        path = self._sandbox_log_path(slug)
        try:
            os.makedirs(logs_dir, exist_ok=True)
            self._rotate_log_if_needed(path)
            record = {
                "ts": round(time.time(), 3),
                "sandbox": slug,
                "event": event,
            }
            record.update(fields)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            return

    def list(self) -> list[dict[str, Any]]:
        return [manifest.to_dict() for manifest in self.store.list()]

    def _admitted_manifests(self, *, exclude_slug: str | None = None) -> list[SandboxManifest]:
        return [
            manifest
            for manifest in self.store.list()
            if manifest.slug != exclude_slug and manifest.last_status in {"running", "starting"}
        ]

    def _check_resource_admission(self, candidate: SandboxManifest) -> None:
        admitted = self._admitted_manifests(exclude_slug=candidate.slug)
        next_count = len(admitted) + 1
        next_vcpus = sum(manifest.vcpus for manifest in admitted) + candidate.vcpus
        next_memory_mib = sum(manifest.memory_mib for manifest in admitted) + candidate.memory_mib
        failures = []
        if self.max_sandboxes is not None and next_count > self.max_sandboxes:
            failures.append(f"sandboxes {next_count}/{self.max_sandboxes}")
        if self.max_total_vcpus is not None and next_vcpus > self.max_total_vcpus:
            failures.append(f"vCPUs {next_vcpus}/{self.max_total_vcpus}")
        if self.max_total_memory_mib is not None and next_memory_mib > self.max_total_memory_mib:
            failures.append(f"memory {next_memory_mib}/{self.max_total_memory_mib} MiB")
        if not failures:
            return
        running = ", ".join(
            f"{manifest.slug} ({manifest.vcpus} vCPU, {manifest.memory_mib} MiB)"
            for manifest in admitted
        ) or "none"
        raise SandboxServiceError(
            "Resource admission refused: "
            + "; ".join(failures)
            + f". Currently running sandboxes: {running}."
        )

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

    def _clock_payload(
        self,
        *,
        status: str,
        host_epoch: float | None = None,
        guest_epoch: float | None = None,
        drift_seconds: float | None = None,
        synced: bool = False,
        error: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": status,
            "checked_at": round(time.time(), 3),
            "threshold_seconds": self.clock_drift_warning_seconds,
        }
        if host_epoch is not None:
            payload["host_epoch"] = round(host_epoch, 3)
        if guest_epoch is not None:
            payload["guest_epoch"] = round(guest_epoch, 3)
        if drift_seconds is not None:
            payload["drift_seconds"] = round(drift_seconds, 3)
        if synced:
            payload["synced_at"] = payload["checked_at"]
        if error:
            payload["error"] = error
        return payload

    def _save_clock(self, manifest: SandboxManifest, clock: dict[str, Any]) -> dict[str, Any]:
        manifest.clock = clock
        self.store.save(manifest)
        return clock

    async def _guest_epoch(self, sandbox: Any) -> float:
        result = await run_sandbox_shell(sandbox, "date -u +%s", check=True)
        output = result_output_text(result).strip().splitlines()
        if not output:
            raise SandboxServiceError("Sandbox clock command returned no output.")
        try:
            return float(output[-1].strip())
        except ValueError as exc:
            raise SandboxServiceError(f"Sandbox clock command returned an invalid epoch: {output[-1]!r}") from exc

    async def _sample_clock(self, sandbox: Any) -> dict[str, Any]:
        before = time.time()
        guest_epoch = await self._guest_epoch(sandbox)
        after = time.time()
        host_epoch = (before + after) / 2
        drift_seconds = guest_epoch - host_epoch
        status = "drift" if abs(drift_seconds) > self.clock_drift_warning_seconds else "ok"
        return self._clock_payload(
            status=status,
            host_epoch=host_epoch,
            guest_epoch=guest_epoch,
            drift_seconds=drift_seconds,
        )

    async def _set_guest_clock(self, sandbox: Any, host_epoch: int) -> None:
        await run_sandbox_shell(sandbox, f"date -u -s @{int(host_epoch)} >/dev/null", check=True)

    async def _connect_running_sandbox(self, runtime: MicrosandboxRuntime, manifest: SandboxManifest) -> Any:
        if manifest.last_status != "running":
            raise SandboxServiceError(f"Start {manifest.slug} before checking its clock.")
        return await runtime.connect(manifest.slug)

    async def _check_manifest_clock(
        self,
        runtime: MicrosandboxRuntime,
        manifest: SandboxManifest,
        *,
        sandbox: Any | None = None,
        reason: str = "manual",
    ) -> dict[str, Any]:
        try:
            sandbox = sandbox or await self._connect_running_sandbox(runtime, manifest)
            clock = await self._sample_clock(sandbox)
        except Exception as exc:
            clock = self._clock_payload(status="error", error=str(exc))
        self._save_clock(manifest, clock)
        self._log_sandbox_event(
            manifest.slug,
            "clock_checked",
            status=clock.get("status"),
            reason=reason,
            drift_seconds=clock.get("drift_seconds"),
            error=clock.get("error"),
        )
        return clock

    async def _sync_manifest_clock(
        self,
        runtime: MicrosandboxRuntime,
        manifest: SandboxManifest,
        *,
        sandbox: Any | None = None,
        reason: str = "manual",
    ) -> dict[str, Any]:
        try:
            sandbox = sandbox or await self._connect_running_sandbox(runtime, manifest)
            target_epoch = int(time.time())
            await self._set_guest_clock(sandbox, target_epoch)
            clock = await self._sample_clock(sandbox)
            clock["status"] = "synced" if abs(float(clock.get("drift_seconds") or 0)) <= CLOCK_SYNC_TARGET_SECONDS else "drift"
            clock["synced_at"] = round(time.time(), 3)
        except Exception as exc:
            clock = self._clock_payload(status="error", error=str(exc))
        self._save_clock(manifest, clock)
        self._log_sandbox_event(
            manifest.slug,
            "clock_synced" if clock.get("status") != "error" else "clock_sync_failed",
            status=clock.get("status"),
            reason=reason,
            drift_seconds=clock.get("drift_seconds"),
            error=clock.get("error"),
        )
        if clock.get("status") == "error":
            raise SandboxServiceError(f"Could not sync {manifest.slug} clock: {clock.get('error')}")
        return clock

    async def check_clock(self, slug: str) -> dict[str, Any]:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")
        runtime = MicrosandboxRuntime()
        await runtime.ensure_installed()
        await self._check_manifest_clock(runtime, manifest, reason="manual")
        refreshed = self.store.get(manifest.slug) or manifest
        return refreshed.to_dict()

    async def check_running_clocks(self) -> list[dict[str, Any]]:
        runtime: MicrosandboxRuntime | None = None
        checked = []
        for manifest in self.store.list():
            if manifest.last_status != "running":
                continue
            if runtime is None:
                runtime = MicrosandboxRuntime()
                await runtime.ensure_installed()
            await self._check_manifest_clock(runtime, manifest, reason="periodic")
            checked.append((self.store.get(manifest.slug) or manifest).to_dict())
        return checked

    async def sync_clock(self, slug: str) -> dict[str, Any]:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")
        runtime = MicrosandboxRuntime()
        await runtime.ensure_installed()
        await self._sync_manifest_clock(runtime, manifest, reason="manual")
        refreshed = self.store.get(manifest.slug) or manifest
        return refreshed.to_dict()

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
        if self._runtime_instance_exists(slug):
            raise SandboxServiceError(
                f"Sandbox name is already used by an existing Microsandbox instance outside Toady: {slug}"
            )
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
        self._check_resource_admission(manifest)
        saved = self.store.save(manifest).to_dict()
        self._log_sandbox_event(
            slug,
            "created",
            status=saved["last_status"],
            vcpus=saved["vcpus"],
            memory_mib=saved["memory_mib"],
            controller_host_port=(saved.get("controller") or {}).get("host_port"),
        )
        return saved

    def _runtime_instance_exists(self, slug: str) -> bool:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return False
        try:
            return bool(asyncio.run(MicrosandboxRuntime().exists(slug)))
        except Exception:
            return False

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

    def read_logs(self, slug: str, *, limit: int = 500) -> list[str]:
        slug = validate_slug(slug)
        if self.store.get(slug) is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")
        path = self._sandbox_log_path(slug)
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            return []
        return lines[-max(1, int(limit)):]

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
        self._log_sandbox_event(
            manifest.slug,
            "port_published",
            guest_port=guest_port,
            host_port=host_port,
            status=status,
        )
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
        self._log_sandbox_event(
            manifest.slug,
            "port_unpublished",
            guest_port=removed.get("guest_port"),
            host_port=removed.get("host_port"),
            status=removed.get("status"),
        )
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
        self._log_sandbox_event(
            manifest.slug,
            "port_reassigned",
            old_host_port=old_host_port,
            new_host_port=new_host_port,
            guest_port=updated_mapping.get("guest_port"),
            status=updated_mapping.get("status"),
        )
        return updated_mapping

    def _mark_conflicting_published_ports(self, manifest: SandboxManifest, mappings: list[dict[str, Any]]) -> None:
        conflicts = []
        next_mappings = []
        changed = False
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
                changed = changed or conflict != mapping
                next_mappings.append(conflict)
            else:
                clean = dict(mapping)
                clean.pop("conflict", None)
                if clean.get("status") == "conflict":
                    clean["status"] = "active"
                changed = changed or clean != mapping
                next_mappings.append(clean)
        if not conflicts:
            if changed:
                manifest.published_ports = next_mappings
                self.store.save(manifest)
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

    def _base_spec(self) -> ToadySandboxSpec:
        return ToadySandboxSpec(
            sandbox_name="toady-base-prepare",
            workspace=self.source_root,
            source_root=self.source_root,
            sandbox_home=Path(self.home) / "base" / "home",
            base=self.base,
            vcpus=self.default_vcpus,
            memory_mib=self.default_memory_mib,
            host_nofile=self.host_nofile,
            guest_nofile=self.guest_nofile,
            network_max_connections=self.network_max_connections,
        )

    def _record_manifest_runtime(self, manifest: SandboxManifest, metadata: dict[str, Any]) -> SandboxManifest:
        manifest.runtime_generation = str(metadata.get("generation") or "")
        manifest.runtime_versions = dict(metadata.get("agent_versions") or {})
        return self.store.save(manifest)

    async def _remove_runtime_instance(self, runtime: MicrosandboxRuntime, slug: str) -> None:
        await runtime.stop(slug)
        last_error: Exception | None = None
        for attempt in range(6):
            try:
                await runtime.remove(slug)
                return
            except Exception as exc:
                last_error = exc
                message = str(exc).lower()
                if "not found" in message or "no such" in message or "does not exist" in message:
                    return
                if "still running" not in message or attempt == 5:
                    break
                await asyncio.sleep(0.5)
                await runtime.stop(slug)
        if last_error is not None:
            raise last_error

    async def refresh_runtime_dependencies(self, slug: str) -> dict[str, Any]:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")

        latest_versions = toady_base.latest_agent_cli_versions()
        metadata_path = self._base_metadata_path()
        metadata = toady_base.read_base_metadata(metadata_path)
        runtime = MicrosandboxRuntime()
        await runtime.ensure_installed()
        base_exists = await runtime.prepared_base_exists(self.base)
        rebuilt_base = False
        if not base_exists or toady_base.base_needs_dependency_refresh(metadata, latest_versions):
            await toady_base.prepare_base(
                runtime,
                self._base_spec(),
                source=self.source_root,
                force=True,
                metadata_path=metadata_path,
                dependency_versions=latest_versions,
            )
            metadata = toady_base.read_base_metadata(metadata_path)
            rebuilt_base = True
        if not metadata:
            raise SandboxServiceError("Agent CLI update did not produce base metadata.")

        target_generation = str(metadata.get("generation") or "")
        was_running = manifest.last_status == "running"
        already_current = bool(target_generation and manifest.runtime_generation == target_generation)
        if already_current:
            self._log_sandbox_event(
                manifest.slug,
                "runtime_refresh_skipped",
                rebuilt_base=rebuilt_base,
                reason="already_current",
            )
            return {
                "sandbox": manifest.to_dict(),
                "restarted": False,
                "rebuilt_base": rebuilt_base,
                "updated": False,
                "base": metadata,
                "message": f"{manifest.slug} already has the current agent CLIs.",
            }

        self._log_sandbox_event(
            manifest.slug,
            "runtime_refresh_started",
            status=manifest.last_status,
            rebuilt_base=rebuilt_base,
            target_generation=target_generation,
        )
        await self._remove_runtime_instance(runtime, manifest.slug)
        if was_running:
            started = await self.start(manifest.slug)
            refreshed = self.store.get(manifest.slug)
            if refreshed is None:
                raise SandboxServiceError(f"Unknown sandbox after refresh: {manifest.slug}")
            saved = self._record_manifest_runtime(refreshed, metadata).to_dict()
            self._log_sandbox_event(
                manifest.slug,
                "runtime_refreshed",
                status=saved["last_status"],
                restarted=True,
                rebuilt_base=rebuilt_base,
                target_generation=target_generation,
            )
            return {
                "sandbox": saved,
                "restarted": True,
                "rebuilt_base": rebuilt_base,
                "updated": True,
                "base": metadata,
                "message": f"Updated agent CLIs and restarted {manifest.slug}.",
            }

        refreshed = self.store.get(manifest.slug)
        if refreshed is None:
            raise SandboxServiceError(f"Unknown sandbox after refresh: {manifest.slug}")
        saved = self._record_manifest_runtime(refreshed, metadata).to_dict()
        self._log_sandbox_event(
            manifest.slug,
            "runtime_refreshed",
            status=saved["last_status"],
            restarted=False,
            rebuilt_base=rebuilt_base,
            target_generation=target_generation,
        )
        return {
            "sandbox": saved,
            "restarted": False,
            "rebuilt_base": rebuilt_base,
            "updated": True,
            "base": metadata,
            "message": f"Updated agent CLIs for {manifest.slug}; sandbox remains {saved['last_status']}.",
        }

    async def start(self, slug: str) -> dict[str, Any]:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")
        self._check_resource_admission(manifest)
        manifest.last_status = "starting"
        self.store.save(manifest)
        self._log_sandbox_event(manifest.slug, "starting", status=manifest.last_status)
        runtime = MicrosandboxRuntime()
        runtime_ready = False
        created_runtime = False
        try:
            await runtime.ensure_installed()
            runtime_ready = True
            await self._remove_runtime_instance(runtime, manifest.slug)
        except Exception as exc:
            manifest.last_status = "error"
            self.store.save(manifest)
            self._log_sandbox_event(manifest.slug, "start_failed", status=manifest.last_status, error=str(exc))
            raise

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
        mark_open_fds_close_on_exec()
        try:
            sandbox = await runtime.create(spec)
            created_runtime = True
            await self._sync_manifest_clock(runtime, manifest, sandbox=sandbox, reason="start")
            await prepare_runtime_dirs(sandbox, spec)
            await disable_guest_ipv6_for_claude(sandbox)
            await verify_mount_access(sandbox, spec)
            await configure_codex_cli(sandbox, spec)
            await start_pty_controller(sandbox, spec)
            wait_for_controller_health(int(host_port))
            await detach_sandbox(sandbox)
            await verify_detached_sandbox(runtime, spec)
        except Exception as exc:
            if runtime_ready and created_runtime:
                try:
                    await self._remove_runtime_instance(runtime, manifest.slug)
                except Exception as cleanup_exc:
                    self._log_sandbox_event(
                        manifest.slug,
                        "start_cleanup_failed",
                        status="error",
                        error=str(cleanup_exc),
                    )
            manifest.last_status = "error"
            self.store.save(manifest)
            self._log_sandbox_event(manifest.slug, "start_failed", status=manifest.last_status, error=str(exc))
            raise

        manifest.last_status = "running"
        manifest.microsandbox_id = manifest.slug
        manifest.published_ports = [
            {**mapping, "status": "active"}
            for mapping in active_mappings
        ]
        manifest.updated_at = time.time()
        saved = self.store.save(manifest).to_dict()
        self._log_sandbox_event(
            manifest.slug,
            "running",
            status=saved["last_status"],
            controller_host_port=(saved.get("controller") or {}).get("host_port"),
            published_ports=[
                {"guest_port": item.get("guest_port"), "host_port": item.get("host_port")}
                for item in saved.get("published_ports", [])
            ],
        )
        return saved

    async def stop(self, slug: str) -> dict[str, Any]:
        manifest = self.store.get(validate_slug(slug))
        if manifest is None:
            raise SandboxServiceError(f"Unknown sandbox: {slug}")
        runtime = MicrosandboxRuntime()
        await runtime.stop(manifest.slug)
        manifest.last_status = "stopped"
        saved = self.store.save(manifest).to_dict()
        self._log_sandbox_event(manifest.slug, "stopped", status=saved["last_status"])
        return saved

    async def stop_running(self) -> list[dict[str, Any]]:
        runtime: MicrosandboxRuntime | None = None
        stopped = []
        for manifest in self.store.list():
            if manifest.last_status not in {"running", "starting"}:
                continue
            if runtime is None:
                runtime = MicrosandboxRuntime()
            await runtime.stop(manifest.slug)
            manifest.last_status = "stopped"
            manifest.updated_at = time.time()
            saved = self.store.save(manifest).to_dict()
            self._log_sandbox_event(manifest.slug, "stopped_on_exit", status=saved["last_status"])
            stopped.append(saved)
        return stopped

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
        self._log_sandbox_event(manifest.slug, "destroyed", purge_home=bool(purge_home))
        self.store.delete(manifest.slug)
        if purge_home:
            import shutil

            shutil.rmtree(os.path.dirname(manifest.home_path), ignore_errors=True)
        return True


def browse_roots_from_env() -> list[str]:
    raw = os.environ.get("TOADY_WORKSPACE_ROOTS", "")
    return [item for item in raw.split(os.pathsep) if item] if raw else []
