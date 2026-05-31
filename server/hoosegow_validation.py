"""Validation helpers for Hoosegow sandbox state and API inputs."""

from __future__ import annotations

import os
import re
from pathlib import Path


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
BLOCKED_PATHS = {
    "/",
    "/bin",
    "/boot",
    "/etc",
    "/sbin",
    "/usr",
    "/var",
}
SENSITIVE_HOME_NAMES = {
    ".aws",
    ".config",
    ".gnupg",
    ".ssh",
    ".hoosegow",
}


class ValidationError(ValueError):
    """User-facing validation error."""


def validate_slug(slug: str) -> str:
    slug = (slug or "").strip().lower()
    if not SLUG_RE.fullmatch(slug):
        raise ValidationError("Sandbox slug must match [a-z0-9][a-z0-9-]{0,30}.")
    return slug


def parse_port_pool(value: str) -> tuple[int, int]:
    raw = (value or "").strip()
    if "-" not in raw:
        port = parse_port("port pool", raw)
        return port, port
    left, right = raw.split("-", 1)
    start = parse_port("port pool start", left)
    end = parse_port("port pool end", right)
    if end < start:
        raise ValidationError("Port pool end must be greater than or equal to start.")
    return start, end


def parse_port(name: str, value: str | int) -> int:
    if not str(value).isdigit():
        raise ValidationError(f"{name} must be numeric.")
    port = int(value)
    if port < 1 or port > 65535:
        raise ValidationError(f"{name} must be between 1 and 65535.")
    return port


def normalize_browse_roots(raw_roots: list[str] | tuple[str, ...] | None) -> list[str]:
    roots = list(raw_roots or [])
    if not roots:
        roots = [os.getcwd(), str(Path.home())]
    normalized: list[str] = []
    for root in roots:
        path = os.path.realpath(os.path.abspath(os.path.expanduser(root)))
        if os.path.isdir(path) and path not in normalized:
            normalized.append(path)
    return normalized


def ensure_descendant(path: str, roots: list[str]) -> str:
    real_path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    for root in roots:
        try:
            common = os.path.commonpath([root, real_path])
        except ValueError:
            continue
        if common == root:
            return real_path
    raise ValidationError("Workspace path must be inside an allowed workspace root.")


def validate_workspace_path(path: str, *, browse_roots: list[str], state_home: str) -> tuple[str, list[str]]:
    real_path = ensure_descendant(path, browse_roots)
    if not os.path.isdir(real_path):
        raise ValidationError(f"Workspace path is not a directory: {path}")
    if not os.access(real_path, os.R_OK | os.W_OK):
        raise ValidationError(f"Workspace path must be readable and writable: {path}")

    state_home = os.path.realpath(os.path.abspath(os.path.expanduser(state_home)))
    blocked = set(BLOCKED_PATHS)
    blocked.add(state_home)
    home = os.path.realpath(str(Path.home()))
    for name in SENSITIVE_HOME_NAMES:
        blocked.add(os.path.join(home, name))

    for blocked_path in blocked:
        if blocked_path == "/" and real_path != "/":
            continue
        try:
            common = os.path.commonpath([blocked_path, real_path])
        except ValueError:
            continue
        if common == blocked_path:
            raise ValidationError(f"Workspace path is blocked for safety: {real_path}")

    warnings: list[str] = []
    if real_path == home:
        warnings.append("Workspace is the home directory; typed confirmation is required.")
    return real_path, warnings
