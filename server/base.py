"""Hoosegow Microsandbox base preparation extracted from Bullpen."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from server.persistence import read_json, write_json
from server.microsandbox_runtime import (
    SOURCE_IMAGE_DEFAULT,
    MicrosandboxRuntime,
    HoosegowRuntimeError,
    HoosegowSandboxSpec,
)
from server.sandbox_bootstrap import run_sandbox_shell


AGENT_CLI_PACKAGES = {
    "claude": "@anthropic-ai/claude-code",
    "codex": "@openai/codex",
    "antigravity": "@google/antigravity-cli",
    "opencode": "opencode-ai",
}


def base_metadata_path(home: str | Path, base: str = "hoosegow-microsandbox-local") -> Path:
    return Path(home).expanduser().resolve() / "base" / f"{base}-metadata.json"


def read_base_metadata(path: str | Path) -> dict[str, Any] | None:
    try:
        data = read_json(str(path))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_base_metadata(
    path: str | Path,
    *,
    base: str,
    source_image: str,
    versions: dict[str, str],
) -> dict[str, Any]:
    metadata = {
        "schema_version": 1,
        "base": base,
        "source_image": source_image,
        "generation": str(time.time()),
        "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent_versions": dict(sorted(versions.items())),
    }
    write_json(str(path), metadata)
    return metadata


def latest_agent_cli_versions(cache_dir: str | Path | None = None) -> dict[str, str]:
    versions = {}
    cache_context = tempfile.TemporaryDirectory(prefix="hoosegow-npm-cache-") if cache_dir is None else None
    try:
        npm_cache = Path(cache_dir or cache_context.name).expanduser().resolve()
        npm_cache.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "npm_config_cache": str(npm_cache),
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_progress": "false",
            "npm_config_update_notifier": "false",
        }
        for name, package in AGENT_CLI_PACKAGES.items():
            try:
                result = subprocess.run(
                    ["npm", "view", package, "version"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                    env=env,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise HoosegowRuntimeError(f"Could not check latest {name} package version: {exc}") from exc
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                raise HoosegowRuntimeError(
                    f"Could not check latest {name} package version with npm view {package}."
                    + (f" {detail}" if detail else "")
                )
            version = result.stdout.strip()
            if not version:
                raise HoosegowRuntimeError(f"npm view {package} returned an empty version.")
            versions[name] = version
    finally:
        if cache_context is not None:
            cache_context.cleanup()
    return versions


def base_needs_dependency_refresh(metadata: dict[str, Any] | None, latest_versions: dict[str, str]) -> bool:
    if not metadata:
        return True
    current_versions = dict(metadata.get("agent_versions") or {})
    return any(current_versions.get(name) != version for name, version in latest_versions.items())


async def base_status(base: str = "hoosegow-microsandbox-local") -> dict[str, Any]:
    """Return prepared-base availability without creating sandboxes."""
    try:
        runtime = MicrosandboxRuntime()
        await runtime.ensure_installed()
        snapshot = await runtime.get_prepared_base(base)
        if snapshot is None:
            return {
                "name": base,
                "prepared": False,
                "state": "missing",
                "message": f"Prepared Microsandbox base '{base}' was not found.",
            }
        path = getattr(snapshot, "path", None)
        if path is None:
            open_snapshot = getattr(snapshot, "open", None)
            if callable(open_snapshot):
                opened = open_snapshot()
                if hasattr(opened, "__await__"):
                    opened = await opened
                path = getattr(opened, "path", None)
        return {
            "name": base,
            "prepared": True,
            "state": "ready",
            "path": str(path) if path else "",
            "message": f"Prepared Microsandbox base '{base}' is available.",
        }
    except Exception as exc:
        return {
            "name": base,
            "prepared": False,
            "state": "error",
            "error": str(exc),
            "message": "Could not inspect Microsandbox base status.",
        }


def codex_cli_integrity_command() -> str:
    return r'''
command -v bwrap >/dev/null
test -x /usr/local/bin/codex
node --input-type=module - <<'NODE'
import { createRequire } from "node:module";
import { statSync } from "node:fs";

const packageByArch = {
  arm64: "@openai/codex-linux-arm64",
  x64: "@openai/codex-linux-x64",
};
const packageName = packageByArch[process.arch];
if (!packageName) {
  throw new Error(`Unsupported Codex Linux architecture: ${process.arch}`);
}
const require = createRequire("/usr/local/lib/node_modules/@openai/codex/bin/codex.js");
const packageJsonPath = require.resolve(`${packageName}/package.json`);
const packageJsonStat = statSync(packageJsonPath);
if (packageJsonStat.size <= 0) {
  throw new Error(`${packageJsonPath} is empty`);
}
NODE
codex --version
'''


async def run_logged_sandbox_shell(sandbox: Any, command: str, *, label: str) -> Any:
    print(f"==> {label}", flush=True)
    try:
        result = await run_sandbox_shell(sandbox, command, check=True)
    except HoosegowRuntimeError as exc:
        raise HoosegowRuntimeError(f"{label} failed\n{exc}") from exc
    stdout = getattr(result, "stdout_text", "") or getattr(result, "stdout", "")
    stderr = getattr(result, "stderr_text", "") or getattr(result, "stderr", "")
    if stdout.strip():
        print(stdout)
    if stderr.strip():
        print(stderr)
    return result


async def stop_prepare_sandbox(sandbox: Any) -> None:
    if hasattr(sandbox, "stop_and_wait"):
        result = sandbox.stop_and_wait()
        if hasattr(result, "__await__"):
            await result
        return
    stop = getattr(sandbox, "stop", None)
    if callable(stop):
        result = stop()
        if hasattr(result, "__await__"):
            await result
    wait = getattr(sandbox, "wait", None)
    if callable(wait):
        result = wait()
        if hasattr(result, "__await__"):
            await result


async def validate_prepared_base_snapshot(runtime: MicrosandboxRuntime, spec: HoosegowSandboxSpec) -> None:
    validate_name = f"{spec.base}-v"
    sandbox = await runtime.create_base_validation_sandbox(validate_name, spec.base, spec)
    try:
        await run_logged_sandbox_shell(
            sandbox,
            "set -euo pipefail\n" + codex_cli_integrity_command(),
            label="Validating prepared base snapshot",
        )
    finally:
        await runtime.stop(validate_name)
        try:
            await runtime.remove(validate_name)
        except Exception:
            pass


async def prepare_base(
    runtime: MicrosandboxRuntime,
    spec: HoosegowSandboxSpec,
    *,
    source_image: str = SOURCE_IMAGE_DEFAULT,
    source: Path | None = None,
    force: bool = True,
    metadata_path: str | Path | None = None,
    dependency_versions: dict[str, str] | None = None,
) -> None:
    """Prepare a reusable Hoosegow Microsandbox base snapshot.

    This is a direct Bullpen-style prepare-sandbox -> snapshot -> validation
    flow. It is intentionally conservative and keeps the expensive operational
    checks before the app-level sandbox lifecycle is wired.
    """
    source = source or spec.source_root
    prepare_name = f"{spec.base}-prepare"
    if force:
        await runtime.stop(prepare_name)
        try:
            await runtime.remove(prepare_name)
        except Exception:
            pass

    print(f"==> Creating prepare sandbox {prepare_name} from {source_image}", flush=True)
    sandbox = await runtime.create_prepare_sandbox(prepare_name, source_image, source)
    try:
        await run_logged_sandbox_shell(
            sandbox,
            r"""
            set -euo pipefail
            export DEBIAN_FRONTEND=noninteractive
            apt-get update
            apt-get install -y --no-install-recommends \
              bash bubblewrap ca-certificates curl gh git iproute2 jq nano python3 python3-pip python3-venv ripgrep strace tmux
            rm -rf /var/lib/apt/lists/*
            """,
            label="Installing OS packages",
        )
        await run_logged_sandbox_shell(
            sandbox,
            r"""
            set -euo pipefail
            python3 -m venv /opt/hoosegow-venv
            /opt/hoosegow-venv/bin/python -m pip install --upgrade pip
            /opt/hoosegow-venv/bin/python -m pip install --no-cache-dir -r /app/requirements.txt
            /opt/hoosegow-venv/bin/python - <<'PY'
import flask
import flask_socketio
import pyfiglet
PY
            """,
            label="Installing Hoosegow Python dependencies",
        )
        await run_logged_sandbox_shell(
            sandbox,
            r"""
            set -euo pipefail
            export npm_config_audit=false
            export npm_config_fund=false
            export npm_config_progress=false
            npm install -g --no-audit --no-fund --no-progress --omit=dev @anthropic-ai/claude-code
            npm install -g --no-audit --no-fund --no-progress --omit=dev @openai/codex
            npm install -g --no-audit --no-fund --no-progress --omit=dev @google/antigravity-cli
            npm install -g --no-audit --no-fund --no-progress --omit=dev opencode-ai
            """,
            label="Installing agent CLIs",
        )
        await run_logged_sandbox_shell(
            sandbox,
            f"""
            set -euo pipefail
            versions_file=/opt/hoosegow-microsandbox-base-versions.txt
            {{
              python3 --version
              /opt/hoosegow-venv/bin/python -c 'import flask, flask_socketio, pyfiglet'
              git --version
              gh --version
              node --version
              npm --version
              claude --version
              {codex_cli_integrity_command()}
              antigravity --version
              opencode --version
            }} > "$versions_file"
            cat "$versions_file"
            test -s "$versions_file"
            sync
            """,
            label="Verifying prepared base",
        )
        print("==> Stopping prepare sandbox", flush=True)
        await stop_prepare_sandbox(sandbox)
        print(f"==> Creating local snapshot {spec.base}", flush=True)
        await runtime.create_snapshot(prepare_name, spec.base)
        await validate_prepared_base_snapshot(runtime, spec)
        if metadata_path is not None:
            write_base_metadata(
                metadata_path,
                base=spec.base,
                source_image=source_image,
                versions=dependency_versions or latest_agent_cli_versions(),
            )
        print(f"Prepared Microsandbox base: {spec.base}")
    finally:
        try:
            await runtime.remove(prepare_name)
        except Exception:
            pass


async def ensure_prepared_base(runtime: MicrosandboxRuntime, spec: HoosegowSandboxSpec, *, auto_prepare: bool = False) -> None:
    if await runtime.prepared_base_exists(spec.base):
        return
    if not auto_prepare:
        raise HoosegowRuntimeError(
            f"Prepared Microsandbox base '{spec.base}' was not found. "
            "Run: python3 hoosegow.py --prepare-base"
        )
    await prepare_base(runtime, spec, force=True)
