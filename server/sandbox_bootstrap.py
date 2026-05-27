"""In-sandbox bootstrap helpers extracted from Bullpen deploy-sandbox.py."""

from __future__ import annotations

import os
import re
import shlex
import time
from typing import Any
import urllib.error
import urllib.request

from server.microsandbox_runtime import (
    MicrosandboxRuntime,
    ToadyRuntimeError,
    ToadySandboxSpec,
    maybe,
)


SYSTEM_CA_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt"
SYSTEM_CA_CERT_DIR = "/etc/ssl/certs"
TERMINAL_CONTROLLER_GUEST_PORT_DEFAULT = 5859
SECRET_ENV_NAMES = {
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "TOADY_PTYD_TOKEN",
}


def build_runtime_env(
    spec: ToadySandboxSpec,
    *,
    controller_port: int = TERMINAL_CONTROLLER_GUEST_PORT_DEFAULT,
    controller_token: str = "",
) -> None:
    spec.runtime_env.update(
        {
            "HOME": "/home/agent",
            "USER": "agent",
            "LOGNAME": "agent",
            "TOADY_UID": str(os.getuid()),
            "TOADY_GID": str(os.getgid()),
            "TOADY_HOME": "/home/agent",
            "TOADY_PROJECTS_ROOT": "/workspace",
            "TOADY_DEPLOY_LABEL": f"(Microsandbox:{spec.sandbox_name})",
            "TOADY_CODEX_PATH": "/usr/local/bin/codex",
            "TOADY_PTYD_HOST": "0.0.0.0",
            "TOADY_PTYD_PORT": str(controller_port),
            "TOADY_PTYD_TOKEN": controller_token,
            "TOADY_MICROSANDBOX_HOST_NOFILE": str(spec.host_nofile),
            "TOADY_MICROSANDBOX_GUEST_NOFILE": str(spec.guest_nofile),
            "TOADY_MICROSANDBOX_MAX_CONNECTIONS": str(spec.network_max_connections),
        }
    )


def ca_env_prefix() -> str:
    return "; ".join(
        [
            f"export SSL_CERT_FILE={shlex.quote(SYSTEM_CA_CERT_FILE)}",
            f"export SSL_CERT_DIR={shlex.quote(SYSTEM_CA_CERT_DIR)}",
            f"export NODE_EXTRA_CA_CERTS={shlex.quote(SYSTEM_CA_CERT_FILE)}",
            'export BUN_OPTIONS="${BUN_OPTIONS:+$BUN_OPTIONS }--use-system-ca"',
        ]
    )


async def run_sandbox_shell(sandbox: Any, command: str, *, check: bool = True) -> Any:
    exec_command = getattr(sandbox, "exec", None)
    if callable(exec_command):
        result = exec_command("bash", ["-lc", command])
    else:
        shell = getattr(sandbox, "shell", None)
        if not callable(shell):
            raise ToadyRuntimeError("Microsandbox sandbox object does not expose exec() or shell().")
        result = shell(command)
    result = await maybe(result)
    returncode = getattr(result, "returncode", None)
    if returncode is None:
        returncode = getattr(result, "exit_code", None)
    exit_status = getattr(result, "exit_status", None)
    if returncode is None and exit_status is not None:
        returncode = getattr(exit_status, "code", None)
    success = getattr(result, "success", None)
    failed = returncode not in (None, 0) or success is False
    if check and failed:
        details = result_output_text(result)
        raise ToadyRuntimeError(f"Sandbox command failed: {command}\n{details}")
    return result


def result_output_text(result: Any) -> str:
    def normalize(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if value is None:
            return ""
        return str(value)

    stdout = normalize(getattr(result, "stdout_text", "") or getattr(result, "stdout", ""))
    stderr = normalize(getattr(result, "stderr_text", "") or getattr(result, "stderr", ""))
    return "\n".join(part for part in (stdout, stderr) if part)


def redact_text(text: str, spec: ToadySandboxSpec | None = None) -> str:
    redacted = text
    if spec is not None:
        for key in SECRET_ENV_NAMES:
            value = spec.runtime_env.get(key)
            if value:
                redacted = redacted.replace(str(value), "[REDACTED]")
    return redacted


def sandbox_env_prefix(spec: ToadySandboxSpec) -> str:
    exports = []
    for key, value in sorted(spec.runtime_env.items()):
        exports.append(f"export {key}={shlex.quote(str(value))}")
    return "; ".join(exports)


async def run_configured_sandbox_shell(
    sandbox: Any,
    spec: ToadySandboxSpec,
    command: str,
    *,
    check: bool = True,
    label: str | None = None,
) -> Any:
    try:
        return await run_sandbox_shell(sandbox, f"{sandbox_env_prefix(spec)}; {command}", check=check)
    except ToadyRuntimeError as exc:
        message = redact_text(str(exc), spec)
        if label and message.startswith("Sandbox command failed: "):
            _first, _sep, details = message.partition("\n")
            message = f"Sandbox command failed: {label}"
            if details:
                message = f"{message}\n{details}"
        raise ToadyRuntimeError(message) from exc


async def run_as_agent(sandbox: Any, spec: ToadySandboxSpec, command: str, *, check: bool = True, label: str | None = None) -> Any:
    configured = f"{sandbox_env_prefix(spec)}; {command}"
    wrapped = f"su -s /bin/bash agent -c {shlex.quote(configured)}"
    try:
        return await run_sandbox_shell(sandbox, wrapped, check=check)
    except ToadyRuntimeError as exc:
        message = redact_text(str(exc), spec)
        if label and message.startswith("Sandbox command failed: "):
            _first, _sep, details = message.partition("\n")
            message = f"Sandbox command failed: {label}"
            if details:
                message = f"{message}\n{details}"
        raise ToadyRuntimeError(message) from exc


async def prepare_runtime_dirs(sandbox: Any, spec: ToadySandboxSpec) -> None:
    command = r'''set -e
uid="${TOADY_UID:-1000}"
gid="${TOADY_GID:-1000}"
if ! getent group agent >/dev/null 2>&1; then
  if getent group "$gid" >/dev/null 2>&1; then
    group_name="$(getent group "$gid" | cut -d: -f1)"
  else
    groupadd --gid "$gid" agent
    group_name="agent"
  fi
else
  group_name="agent"
fi
if ! id agent >/dev/null 2>&1; then
  useradd --uid "$uid" --gid "$group_name" --home-dir /home/agent --shell /bin/bash agent
fi
actual_uid="$(id -u agent)"
if [ "$actual_uid" != "$uid" ]; then
  echo "Existing agent user has uid $actual_uid, expected $uid." >&2
  exit 1
fi
mkdir -p /workspace /home/agent/logs /home/agent/bin /home/agent/.codex /var/lib/toady
chown agent:"$group_name" /home/agent/logs /home/agent/bin /home/agent/.codex
chown -R agent:"$group_name" /var/lib/toady
chmod 700 /var/lib/toady 2>/dev/null || true
mkdir -p /etc/security/limits.d
cat > /etc/security/limits.d/toady-fd.conf <<'LIMITS_EOF'
agent soft nofile __GUEST_NOFILE__
agent hard nofile __GUEST_NOFILE__
LIMITS_EOF
chmod 644 /etc/security/limits.d/toady-fd.conf
su -s /bin/bash agent -c 'test -w /home/agent && test -w /home/agent/logs && test -w /home/agent/bin && test -w /home/agent/.codex'
soft_nofile="$(su -s /bin/bash agent -c 'ulimit -Sn')"
hard_nofile="$(su -s /bin/bash agent -c 'ulimit -Hn')"
if [ "$soft_nofile" -lt __GUEST_NOFILE__ ] || [ "$hard_nofile" -lt __GUEST_NOFILE__ ]; then
  echo "warn: agent RLIMIT_NOFILE is soft=$soft_nofile hard=$hard_nofile, expected soft=__GUEST_NOFILE__ hard=__GUEST_NOFILE__; pam_limits may not be enforcing limits.d" >&2
fi
'''.replace("__GUEST_NOFILE__", str(spec.guest_nofile))
    await run_configured_sandbox_shell(sandbox, spec, command, label="prepare Microsandbox runtime user")


async def disable_guest_ipv6_for_claude(sandbox: Any) -> None:
    command = r'''set -e
mkdir -p /etc/sysctl.d
cat > /etc/sysctl.d/99-toady-claude-ipv4.conf <<'SYSCTL_EOF'
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.eth0.disable_ipv6 = 1
SYSCTL_EOF
if command -v sysctl >/dev/null 2>&1; then
  for key in net.ipv6.conf.all.disable_ipv6 net.ipv6.conf.default.disable_ipv6 net.ipv6.conf.eth0.disable_ipv6; do
    sysctl -w "$key=1" >/dev/null
  done
else
  for name in all default eth0; do
    path="/proc/sys/net/ipv6/conf/${name}/disable_ipv6"
    [ -e "$path" ] || continue
    printf '1' > "$path"
  done
fi
for name in all default eth0; do
  path="/proc/sys/net/ipv6/conf/${name}/disable_ipv6"
  [ -e "$path" ] || continue
  value="$(cat "$path")"
  if [ "$value" != 1 ]; then
    echo "Failed to disable guest IPv6 for Claude: $path is $value" >&2
    exit 1
  fi
done
echo "Disabled guest IPv6 for Claude auth due to Microsandbox IPv6 TLS EOFs." >&2
'''
    await run_sandbox_shell(sandbox, command)


async def verify_mount_access(sandbox: Any, spec: ToadySandboxSpec) -> None:
    command = "set -e\ntest -w /workspace\ntest -w /home/agent\n"
    await run_as_agent(sandbox, spec, command, label="verify Microsandbox mount access")


async def configure_codex_cli(sandbox: Any, spec: ToadySandboxSpec) -> None:
    command = r'''set -e
mkdir -p /home/agent/.codex /home/agent/.codex/tmp/arg0
rm -f /home/agent/bin/codex
rm -rf /var/lib/toady/codex-home /var/lib/toady/codex.lock
rm -rf /home/agent/.codex/tmp/arg0/codex-arg0*
config_file="/home/agent/.codex/config.toml"
touch "$config_file"
if grep -Eq '^[[:space:]]*cli_auth_credentials_store[[:space:]]*=' "$config_file"; then
  sed -i 's/^[[:space:]]*cli_auth_credentials_store[[:space:]]*=.*/cli_auth_credentials_store = "file"/' "$config_file"
else
  printf '\ncli_auth_credentials_store = "file"\n' >> "$config_file"
fi
real_codex="${TOADY_CODEX_PATH:-$(command -v codex)}"
if [ -z "$real_codex" ] || [ ! -x "$real_codex" ]; then
  echo "Unable to locate real Codex CLI" >&2
  exit 1
fi
chown agent:"$(id -gn agent)" /home/agent/.codex /home/agent/.codex/config.toml
chown -R agent:"$(id -gn agent)" /home/agent/.codex/tmp
su -s /bin/bash agent -c 'test -x "$TOADY_CODEX_PATH" && test -w /home/agent/.codex && grep -Eq "^[[:space:]]*cli_auth_credentials_store[[:space:]]*=[[:space:]]*\"file\"" /home/agent/.codex/config.toml'
'''
    await run_configured_sandbox_shell(sandbox, spec, command, label="configure Codex CLI")


async def start_pty_controller(sandbox: Any, spec: ToadySandboxSpec) -> None:
    command = (
        "set -e; "
        "mkdir -p /home/agent/logs /var/lib/toady; "
        ": > /home/agent/logs/toady-ptyd.log; "
        "cd /app; "
        'nohup python3 /app/guest/toady-ptyd.py --http "${TOADY_PTYD_HOST}:${TOADY_PTYD_PORT}" --token "$TOADY_PTYD_TOKEN" '
        ">/home/agent/logs/toady-ptyd.log 2>&1 &"
    )
    await run_as_agent(sandbox, spec, command, label="start PTY controller")


def wait_for_controller_health(host_port: int, timeout_seconds: int = 20) -> None:
    url = f"http://127.0.0.1:{host_port}/health"
    deadline = time.time() + timeout_seconds
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise ToadyRuntimeError(f"Toady PTY controller health check failed for {url}: {last_error}")


async def detach_sandbox(sandbox: Any) -> None:
    detach = getattr(sandbox, "detach", None)
    if not callable(detach):
        raise ToadyRuntimeError("Installed Microsandbox SDK does not expose sandbox.detach().")
    await maybe(detach())


async def verify_detached_sandbox(runtime: MicrosandboxRuntime, spec: ToadySandboxSpec) -> None:
    status = await runtime.status(spec.sandbox_name)
    if status is not None and "running" not in status.lower():
        raise ToadyRuntimeError(f"Microsandbox '{spec.sandbox_name}' is not running after detach (status: {status}).")


_URL_RE = re.compile(r"https?://\S+")


def extract_urls(text: str) -> list[str]:
    return [match.group(0).rstrip(").,]") for match in _URL_RE.finditer(text or "")]
