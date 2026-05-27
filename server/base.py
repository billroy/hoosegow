"""Toady Microsandbox base preparation extracted from Bullpen."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.microsandbox_runtime import (
    SOURCE_IMAGE_DEFAULT,
    MicrosandboxRuntime,
    ToadyRuntimeError,
    ToadySandboxSpec,
)
from server.sandbox_bootstrap import run_sandbox_shell


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
    except ToadyRuntimeError as exc:
        raise ToadyRuntimeError(f"{label} failed\n{exc}") from exc
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


async def validate_prepared_base_snapshot(runtime: MicrosandboxRuntime, spec: ToadySandboxSpec) -> None:
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
    spec: ToadySandboxSpec,
    *,
    source_image: str = SOURCE_IMAGE_DEFAULT,
    source: Path | None = None,
    force: bool = True,
) -> None:
    """Prepare a reusable Toady Microsandbox base snapshot.

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
              bash bubblewrap ca-certificates curl gh git iproute2 jq python3 python3-pip python3-venv ripgrep strace
            rm -rf /var/lib/apt/lists/*
            """,
            label="Installing OS packages",
        )
        await run_logged_sandbox_shell(
            sandbox,
            r"""
            set -euo pipefail
            python3 -m venv /opt/toady-venv
            /opt/toady-venv/bin/python -m pip install --upgrade pip
            /opt/toady-venv/bin/python -m pip install --no-cache-dir -r /app/requirements.txt
            /opt/toady-venv/bin/python - <<'PY'
import flask
import flask_socketio
import pyfiglet
PY
            """,
            label="Installing Toady Python dependencies",
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
            npm install -g --no-audit --no-fund --no-progress --omit=dev @google/gemini-cli
            """,
            label="Installing agent CLIs",
        )
        await run_logged_sandbox_shell(
            sandbox,
            f"""
            set -euo pipefail
            versions_file=/opt/toady-microsandbox-base-versions.txt
            {{
              python3 --version
              /opt/toady-venv/bin/python -c 'import flask, flask_socketio, pyfiglet'
              git --version
              gh --version
              node --version
              npm --version
              claude --version
              {codex_cli_integrity_command()}
              gemini --version
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
        print(f"Prepared Microsandbox base: {spec.base}")
    finally:
        try:
            await runtime.remove(prepare_name)
        except Exception:
            pass


async def ensure_prepared_base(runtime: MicrosandboxRuntime, spec: ToadySandboxSpec, *, auto_prepare: bool = False) -> None:
    if await runtime.prepared_base_exists(spec.base):
        return
    if not auto_prepare:
        raise ToadyRuntimeError(
            f"Prepared Microsandbox base '{spec.base}' was not found. "
            "Run: python3 toady.py --prepare-base"
        )
    await prepare_base(runtime, spec, force=True)
