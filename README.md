# Toady

Toady runs coding-agent terminals inside Microsandbox microVMs. It gives you a
local browser UI for creating sandboxes, opening persistent terminals, and
publishing sandbox dev-server ports without exposing the rest of your host
filesystem.

Toady is derived from Bullpen's proven Flask, Socket.IO, auth, terminal, and
Microsandbox deployment work, but it is not a ticket/worker orchestration app.
You type `claude`, `codex`, `gemini`, `opencode`, or ordinary shell commands
inside sandbox terminals yourself.

## Current Status

This is an active first-draft implementation. The local Microsandbox path is
usable for prepare/create/start/terminal/port workflows, but release hardening
and Bullpen product-surface cleanup are still in progress.

## Quick Start

```bash
pip install -r requirements.txt
python3 toady.py --prepare-base
python3 toady.py --workspace-root /path/to/work
```

Open `http://127.0.0.1:5858/` if the browser does not open automatically.

The workspace root is a shared parent work tree mounted into each sandbox at
`/workspace`. Multiple sandboxes can use the same workspace root at the same
time. Toady never deletes the host workspace root when destroying a sandbox.

## Common Flow

1. Prepare the reusable Microsandbox base.
2. Create a sandbox with a sandbox name and workspace root.
3. Toady starts the sandbox automatically.
4. Toady opens the first terminal automatically.
5. Open more terminals or publish dev-server ports as needed.

## CLI Options

| Flag | Default | Description |
|---|---|---|
| `--port` | `5858` | UI port. |
| `--host` | `127.0.0.1` | Bind address. Non-loopback binds require auth. |
| `--home` | `~/.toady` | State directory. |
| `--workspace-root PATH` | cwd and `$HOME` | Repeatable browse root for sandbox workspace roots. |
| `--prepare-base` | off | Build the reusable Microsandbox base snapshot and exit. |
| `--rebuild-base` | off | Force rebuilding the base snapshot. |
| `--base-image` | `node:22-bookworm` | OCI image used for base preparation. |
| `--vcpus` | `4` | Default per-sandbox vCPU cap. |
| `--memory-mib` | `4096` | Default per-sandbox memory cap. |
| `--terminal-limit` | `32` | Maximum terminals per sandbox. |
| `--port-pool` | `3000-3099` | Host ports used for published sandbox dev servers. |
| `--host-nofile` | `12000` | Target host file-descriptor soft limit before Microsandbox operations. |
| `--guest-nofile` | `65536` | Target in-sandbox `agent` user file-descriptor limit. |
| `--network-max-connections` | `8192` | Microsandbox guest network connection cap. |
| `--no-browser` | off | Do not open a browser on startup. |
| `--websocket-debug` | off | Enable Socket.IO and Engine.IO logging. |
| `--set-password [USERNAME]` | off | Set or update local login credentials, then exit. Repeatable. |
| `--delete-user USERNAME` | off | Delete configured login users, then exit. Repeatable. |
| `--bootstrap-credentials` | off | Create credentials from `TOADY_BOOTSTRAP_USER` and `TOADY_BOOTSTRAP_PASSWORD`, then exit. |

## Authentication

By default, if no credentials are configured, Toady runs without a login screen
on loopback. If you bind to a non-loopback host, Toady refuses to start until
authentication is configured.

```bash
python3 toady.py --set-password admin
```

Credentials are stored as password hashes under `~/.toady/.env` with a stable
session secret. For network exposure, put TLS in front of Toady and set
`TOADY_PRODUCTION=1` so secure cookies and forwarded proxy headers are handled.

## Microsandbox Base

The base image is prepared from `node:22-bookworm` by default and includes the
runtime pieces Toady needs inside each sandbox:

- Python and the Toady PTY controller
- Node/npm tooling
- git, gh, ripgrep, and common CLI dependencies
- Claude/Codex/Gemini/opencode CLI setup hooks where available
- Bullpen-derived FD-limit, network-cap, CA, Codex auth, and Claude IPv6
  workarounds

The UI can prepare or rebuild the base. From the CLI:

```bash
python3 toady.py --prepare-base
python3 toady.py --prepare-base --rebuild-base
```

## Development

Focused checks that currently cover the Toady path:

```bash
pytest -q tests/test_toady_sandbox_service.py \
  tests/test_toady_sandbox_events.py \
  tests/test_toady_terminal_events.py
pytest -q tests/test_auth.py tests/test_auth_e2e.py
node --check static/app.js
```

The repo still contains copied Bullpen modules and tests while excavation is in
progress. See `docs/spec.md` and `docs/bullpen-excavation.md` for the current
plan and provenance notes.
