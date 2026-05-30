"""Atomic Toady sandbox manifest persistence."""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from server.persistence import read_json, write_json


SCHEMA_VERSION = 1


@dataclass
class SandboxManifest:
    schema_version: int
    slug: str
    name: str
    workspace_path: str
    canonical_workspace_path: str
    home_path: str
    vcpus: int
    memory_mib: int
    published_ports: list[dict[str, Any]] = field(default_factory=list)
    controller: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_status: str = "configured"
    microsandbox_id: str | None = None
    runtime_generation: str = ""
    runtime_versions: dict[str, str] = field(default_factory=dict)
    clock: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SandboxManifest":
        return cls(
            schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
            slug=str(data.get("slug") or ""),
            name=str(data.get("name") or data.get("slug") or ""),
            workspace_path=str(data.get("workspace_path") or ""),
            canonical_workspace_path=str(data.get("canonical_workspace_path") or data.get("workspace_path") or ""),
            home_path=str(data.get("home_path") or ""),
            vcpus=int(data.get("vcpus") or 4),
            memory_mib=int(data.get("memory_mib") or 4096),
            published_ports=list(data.get("published_ports") or []),
            controller=dict(data.get("controller") or {}),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            last_status=str(data.get("last_status") or "configured"),
            microsandbox_id=data.get("microsandbox_id"),
            runtime_generation=str(data.get("runtime_generation") or ""),
            runtime_versions=dict(data.get("runtime_versions") or {}),
            clock=dict(data.get("clock") or {}),
            warnings=list(data.get("warnings") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SandboxStore:
    def __init__(self, home: str) -> None:
        self.home = os.path.abspath(os.path.expanduser(home))
        self.sandboxes_dir = os.path.join(self.home, "sandboxes")
        os.makedirs(self.sandboxes_dir, exist_ok=True)

    def manifest_path(self, slug: str) -> str:
        return os.path.join(self.sandboxes_dir, f"{slug}.json")

    def sandbox_home_path(self, slug: str) -> str:
        return os.path.join(self.sandboxes_dir, slug, "home")

    def exists(self, slug: str) -> bool:
        return os.path.exists(self.manifest_path(slug))

    def list(self) -> list[SandboxManifest]:
        manifests: list[SandboxManifest] = []
        for path in sorted(Path(self.sandboxes_dir).glob("*.json")):
            try:
                manifests.append(SandboxManifest.from_dict(read_json(str(path))))
            except (OSError, ValueError, TypeError):
                continue
        return manifests

    def get(self, slug: str) -> SandboxManifest | None:
        path = self.manifest_path(slug)
        if not os.path.exists(path):
            return None
        return SandboxManifest.from_dict(read_json(path))

    def save(self, manifest: SandboxManifest) -> SandboxManifest:
        manifest.updated_at = time.time()
        os.makedirs(os.path.dirname(manifest.home_path), exist_ok=True)
        os.makedirs(manifest.home_path, exist_ok=True)
        write_json(self.manifest_path(manifest.slug), manifest.to_dict())
        return manifest

    def delete(self, slug: str) -> bool:
        path = self.manifest_path(slug)
        if not os.path.exists(path):
            return False
        os.unlink(path)
        return True
