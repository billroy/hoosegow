# Toady

Current release: `0.1.0`.

Public repository: <https://github.com/billroy/toady>

Toady is a local terminal app for running coding agents inside Microsandbox
microVMs. It gives you a browser UI with persistent sandbox terminals, shared
workspace-root mounts, automatic runtime setup, sandbox logs, and local published
ports for dev servers.

The expected workflow is direct: create a sandbox, get a terminal, type
`claude`, `codex`, `gemini`, `opencode`, or ordinary shell commands yourself.
Toady does not orchestrate agents, tickets, workers, commits, or PRs.

Toady is derived from Bullpen's proven Flask, Socket.IO, auth, terminal, and
Microsandbox deployment work, including the hard-won runtime workarounds in the
base-prep path. It is intentionally not a Bullpen worker/ticket UI.

## Current Status

`0.1.0` is the first public Toady release. The local Microsandbox path covers:

- automatic sandbox-runtime setup, rebuild, and logs
- sandbox create, start, stop, destroy, details, and logs
- shared workspace-root selection
- automatic create -> start -> first terminal behavior
- multiple persistent terminals per running sandbox
- browser reattach to terminals while the Toady server keeps running
- published localhost ports for sandbox dev servers
- optional local authentication

Known constraints for this release:

- Toady is a local single-user developer tool.
- Real Microsandbox behavior depends on the host Microsandbox runtime and the
  prepared `toady-microsandbox-local` base.
- Running sandboxes remain alive when Toady exits unless you pass
  `--shutdown-sandboxes-on-exit`.

## Quick Start

```bash
git clone https://github.com/billroy/toady.git
cd toady
pip install -r requirements.txt
python3 toady.py --workspace-root /path/to/work
```

Open `http://127.0.0.1:6060/` if the browser does not open automatically.

`--workspace-root` should usually point at a shared parent work tree, not one
specific project. For example, use `/Users/bill/aistuff` if that directory
contains many repos. Toady mounts the selected root read-write inside each
sandbox at `/workspace`.

## What You See

The UI is terminal-first.

- The top bar has a minimal hamburger menu, the `Toady` title, a light/dark
  toggle, and a green/red Socket.IO status dot.
- The hamburger menu contains sandbox runtime readiness, rebuild, retry after
  setup errors, and runtime logs.
- The left pane is a compact sandbox list headed `Sandboxes (n)`. Its boundary
  is draggable.
- The left-pane `...` menu opens create, selected-sandbox details, published
  ports, and sandbox logs.
- Each sandbox row is one line with status, workspace-root basename, and its
  own `...` action menu.
- Sandbox row actions include start, new terminal, stop, details, published
  ports, logs, and destroy.
- The right pane is almost entirely terminal space: terminal tabs at the top,
  active xterm.js viewport below.
- Details, published ports, base logs, sandbox logs, and create are modals.
- Menus use Lucide icons and dismiss when you click away.

## Common Flow

1. Choose **Sandboxes (...) -> Create sandbox**.
2. Pick or type a workspace root and name the sandbox.
3. Click **Create + Start**.
4. If the reusable sandbox runtime is not ready yet, Toady sets it up
   automatically and creation waits until setup finishes.
5. Toady creates the sandbox, starts it, opens the first terminal, and focuses
   that terminal automatically.
6. Use the sandbox row `...` menu for more terminals, details, ports, logs,
   stop, or destroy.

If you select a running sandbox that has no terminal open in the UI, Toady opens
and focuses one automatically. Creating a sandbox also implies starting it and
opening the first terminal.

## Workspaces And Sandboxes

A workspace root is a canonical host directory mounted as `/workspace` inside
the sandbox. Multiple sandboxes can share the same workspace root at the same
time; this is expected to be the common configuration.

Each sandbox also gets persistent scratch home storage mounted at
`/home/agent`. Destroying a sandbox deletes the sandbox and its persistent home,
after confirmation, but never deletes the host workspace root.

Sandboxes persist across Toady server restarts. Terminals are owned by the
currently running Toady server process: browser refresh/reconnect can reattach
to existing terminals, but exiting the Toady server ends its PTY sessions unless
you only care about the sandbox disk/home state.

## Terminals

Terminals are real PTYs inside the sandbox, rendered with xterm.js in the
browser and bridged through Socket.IO to a token-protected in-sandbox
`toady-ptyd` controller.

- New terminals start directly in the shell at `/workspace`; Toady does not
  inject auth/setup banner text into the terminal.
- Multiple terminals per sandbox are supported. The default limit is 32 per
  sandbox.
- Terminal tabs persist while the server is running. If the browser reconnects,
  Toady lists active PTYs, rejoins them, and replays bounded scrollback.
- Closing a terminal checks for a foreground process when possible and asks for
  confirmation before closing a busy PTY.
- Stopping or destroying a sandbox closes its terminals.

Agent CLI authentication is provided by the prepared base and persisted sandbox
home where applicable. Run the agent command yourself from the terminal.

## MSB Passthrough Wrapper

Toady also ships a tiny Toady-independent wrapper around `msb exec` for tools
that want to run an agent command inside an existing sandbox without opening the
browser UI or talking to the Toady server.

Dry run the generated Microsandbox command:

```bash
scripts/toady-msb --sandbox demo --no-tty --dry-run -- claude -p 'say model slug'
```

Run a non-interactive command:

```bash
scripts/toady-msb --sandbox demo --no-tty -- claude -p 'say model slug'
```

Run from a guest subworkspace:

```bash
scripts/toady-msb --sandbox demo --workspace /workspace/my-repo --no-tty -- pwd
```

Run an interactive command or auth flow:

```bash
scripts/toady-msb --sandbox demo --tty -- claude
```

Defaults are `--user agent` and `--workspace /workspace`. Without `--tty` or
`--no-tty`, the wrapper auto-allocates a PTY only when stdin and stdout are both
terminals. This wrapper does not enforce Toady auth or policy; it uses the same
local Microsandbox authority as `msb exec`.

See `docs/passthrough.md` for the Phase 0 passthrough spec and deferred
Toady-mediated options.

## Published Ports

Use **Published ports** from the Sandboxes menu or a sandbox row menu to expose
a web server running inside the sandbox through `127.0.0.1` on the host.

The default host-port pool is `3000-3099`. You can publish a guest port to an
automatic host port or request a specific host port. Port rows include open,
copy URL, reassign-conflict, and unpublish actions. Some mapping changes apply
on sandbox restart, depending on current sandbox state.

## Base Image

The reusable base image is prepared automatically from `node:22-bookworm` by
default and is named `toady-microsandbox-local`.

The base includes the runtime pieces Toady expects inside each sandbox:

- Python and the Toady PTY controller
- Node/npm tooling
- git, gh, ripgrep, and common CLI dependencies
- Claude Code, Codex, Gemini, and opencode CLIs installed globally
- Bullpen-derived file-descriptor, network-cap, CA, Codex auth, and Claude IPv6
  workarounds

Normal users do not need to prepare it manually. On first launch, the UI asks
for base status, the server notices a missing snapshot, and setup starts in the
background. While that happens, sandbox creation shows a short setup delay and
runtime logs are available from the hamburger menu.

Manual setup and rebuild commands remain available for diagnostics:

```bash
python3 toady.py --prepare-base
python3 toady.py --prepare-base --rebuild-base
```

You can also rebuild and inspect runtime logs from the hamburger menu. If setup
fails, the menu exposes a retry action.

## CLI Options

| Flag | Default | Description |
|---|---|---|
| `--port` | `6060` | UI port. |
| `--host` | `127.0.0.1` | Bind address. Non-loopback binds require auth. |
| `--home` | `~/.toady` | State directory. |
| `--no-browser` | off | Do not open a browser on startup. |
| `--workspace-root PATH` | cwd and `$HOME` | Repeatable browse root for sandbox workspace-root picker. |
| `--prepare-base` | off | Manually build the reusable Microsandbox base snapshot and exit. Normally automatic. |
| `--rebuild-base` | off | Force rebuilding the base snapshot. |
| `--base-image IMAGE` | `node:22-bookworm` | OCI image used for base preparation. |
| `--vcpus N` | `4` | Default per-sandbox vCPU cap. |
| `--memory-mib N` | `4096` | Default per-sandbox memory cap. |
| `--max-sandboxes N` | `8` | Host-wide running-sandbox admission cap. |
| `--max-total-vcpus N` | detected cores | Host-wide admitted vCPU cap. |
| `--max-total-memory-mib N` | 75% host RAM | Host-wide admitted RAM cap. |
| `--terminal-limit N` | `32` | Maximum terminals per sandbox. |
| `--port-pool RANGE` | `3000-3099` | Host ports used for published sandbox dev servers. |
| `--host-nofile N` | `12000` | Target host file-descriptor soft limit before Microsandbox operations. |
| `--guest-nofile N` | `65536` | Target in-sandbox `agent` user file-descriptor limit. |
| `--network-max-connections N` | `8192` | Microsandbox guest network connection cap. |
| `--shutdown-sandboxes-on-exit` | off | Stop running sandboxes when Toady exits. |
| `--websocket-debug` / `--no-websocket-debug` | off | Enable or disable Socket.IO and Engine.IO logging. |
| `--set-password [USERNAME]` | off | Set or update local login credentials, then exit. Repeatable. |
| `--delete-user USERNAME` | off | Delete configured login users, then exit. Repeatable. |
| `--bootstrap-credentials` | off | Create credentials from `TOADY_BOOTSTRAP_USER` and `TOADY_BOOTSTRAP_PASSWORD`, then exit. |
| `--version` | n/a | Print the Toady version and exit. |

Toady rejects browser-blocked ports, such as `6000`, when it would otherwise
open a browser tab. With `--no-browser`, it still starts but prints a warning.

## Authentication

By default, if no credentials are configured, Toady runs without a login screen
on loopback. If you bind to a non-loopback host, Toady refuses to start until
authentication is configured.

```bash
python3 toady.py --set-password admin
```

Credentials are stored as password hashes under `~/.toady/.env` with a stable
session secret.

Environment variables:

- `TOADY_PRODUCTION=1`: trust forwarded proxy headers and mark session cookies
  `Secure` for TLS deployments.
- `TOADY_ALLOWED_ORIGINS`: comma-separated extra allowed Socket.IO origins.
- `TOADY_SESSION_DAYS`: persistent login duration, bounded to 1-365 days.
- `TOADY_BOOTSTRAP_USER`: username for `--bootstrap-credentials`; default
  `admin`.
- `TOADY_BOOTSTRAP_PASSWORD`: password for `--bootstrap-credentials`.
- `TOADY_BOOTSTRAP_FORCE=1`: overwrite existing bootstrapped credentials.

For network exposure, put TLS in front of Toady and set `TOADY_PRODUCTION=1`.
Toady is still designed as a local single-user developer tool, not a multi-user
hosted service.

## State Layout

Toady state lives under `~/.toady/` by default:

```text
~/.toady/
  .env                   # auth users, secret key, production/session settings
  config.json            # global settings
  sandboxes/
    <slug>.json          # sandbox manifest
    <slug>/home/         # bind-mounted into sandbox as /home/agent
  base/                  # base-prep artifacts, if any
  logs/
    server.log
    sandbox-<slug>.log
```

Sandbox manifests include workspace path, home path, resource caps, controller
port/token, published ports, creation time, and last known status. Manifests are
written atomically.

## Development

Focused checks for the Toady path:

```bash
pytest -q tests/test_toady_sandbox_service.py \
  tests/test_toady_sandbox_events.py \
  tests/test_toady_terminal_events.py \
  tests/test_toady_product_surface.py \
  tests/test_toady_cli.py tests/test_msb_passthrough.py \
  tests/test_auth.py tests/test_auth_e2e.py
node --check static/app.js
```

Real Microsandbox smokes use the prepared base `toady-microsandbox-local` by
default:

```bash
python3 scripts/microsandbox_port_smoke.py
python3 scripts/pty_controller_microsandbox_smoke.py --verbose
```

Set `TOADY_MICROSANDBOX_BASE` or pass `--snapshot` to test another base. The
latest local run is recorded in `docs/release-smokes.md`.

The same real smokes are available as an opt-in pytest target:

```bash
TOADY_RUN_REAL_MICROSANDBOX=1 pytest -q -m real_microsandbox
```

The `0.1.0` tree has removed the copied Bullpen product modules and legacy
deploy script after extracting the Microsandbox runtime workarounds into Toady
modules. Toady mode does not register the legacy product REST routes, serve
legacy product static assets, or eagerly import legacy product modules. See
`docs/spec.md` and `docs/bullpen-excavation.md` for provenance notes.
