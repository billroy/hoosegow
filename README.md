# Hoosegow

Current release: `0.1.0`.

Public repository: <https://github.com/billroy/hoosegow>

Hoosegow is a local terminal app for running coding agents inside Microsandbox
microVMs. It gives you a browser UI with persistent sandbox terminals, shared
workspace-root mounts, automatic base-image setup, sandbox logs, and local published
ports for dev servers.

The expected workflow is direct: create a sandbox, get a terminal, type
`claude`, `codex`, `antigravity`, `opencode`, or ordinary shell commands yourself.
Hoosegow does not orchestrate agents, tickets, workers, commits, or PRs.

Hoosegow is derived from Bullpen's proven Flask, Socket.IO, auth, terminal, and
Microsandbox deployment work, including the hard-won runtime workarounds in the
base-prep path. It is intentionally not a Bullpen worker/ticket UI.

## Current Status

`0.1.0` is the first public Hoosegow release. The local Microsandbox path covers:

- automatic base-image setup, rebuild, and logs
- sandbox create, start, stop, destroy, details, and logs
- shared workspace-root selection
- automatic create -> start -> first terminal behavior
- multiple persistent terminals per running sandbox
- browser reattach to terminals while the Hoosegow server keeps running
- published localhost ports for sandbox dev servers
- optional local authentication

Known constraints for this release:

- Hoosegow is a local single-user developer tool.
- Hoosegow pins `microsandbox==0.5.3`. Older Microsandbox releases are missing the
  published-port TCP stall fix Hoosegow depends on.
- Real Microsandbox behavior depends on the pinned host Microsandbox runtime
  and the prepared `hoosegow-microsandbox-local` base.
- Running sandboxes remain alive when Hoosegow exits unless you pass
  `--shutdown-sandboxes-on-exit`.

## Quick Start

```bash
git clone https://github.com/billroy/hoosegow.git
cd hoosegow
pip install -r requirements.txt
python3 hoosegow.py --workspace-root /path/to/work
```

If you installed Hoosegow dependencies before this release, rerun
`pip install -r requirements.txt` so the host `microsandbox` package is upgraded
to the pinned `0.5.3` release.

Open `http://127.0.0.1:6060/` if the browser does not open automatically.

`--workspace-root` should usually point at a shared parent work tree, not one
specific project. For example, use `/Users/bill/aistuff` if that directory
contains many repos. Hoosegow mounts the selected root read-write inside each
sandbox at `/workspace`.

## What You See

The UI is terminal-first.

- The top bar has a minimal hamburger menu, the `Hoosegow` title, a light/dark
  toggle, and a green/red Socket.IO status dot.
- The hamburger menu contains base-image rebuild, retry after setup errors, and
  base logs.
- The left pane is a compact terminal-group list headed `Terminal Groups`. Its
  boundary is draggable.
- Shell groups are where shells run: a `Local` section for host shells, then a
  `Sandboxes` section with one row per sandbox.
- Section header `+` buttons create the thing for that section: a local shell
  under `Local`, or a sandbox under `Sandboxes`.
- Each sandbox row is one line with status, workspace-root basename, open-shell
  count, and its own `...` action menu.
- Sandbox row actions include start, new shell, stop, details, published
  ports, logs, and destroy.
- The right pane is almost entirely terminal space: tabs for the selected shell
  group above the active xterm.js viewport.
- Details, published ports, base logs, sandbox logs, and create are modals.
- Menus use Lucide icons and dismiss when you click away.

## Common Flow

1. Click the `+` in the `Sandboxes` header.
2. Pick or type a workspace root and name the sandbox.
3. Click **Create + Start**.
4. If the reusable base image is not ready yet, Hoosegow sets it up
   automatically and creation waits until setup finishes.
5. Hoosegow creates the sandbox, starts it, opens the first shell, and focuses
   that shell automatically.
6. Use the sandbox row `...` menu for more shells, details, ports, logs,
   agent CLI updates, stop, or destroy.

Use the `+` in the `Local` header for a host-local shell. Use a running sandbox
row's `...` menu for a sandbox shell. Additional shells appear as tabs inside
their selected group, not as separate rows in the left pane. Selecting a sandbox
does not silently open or replace shells.

## Workspaces And Sandboxes

A workspace root is a canonical host directory mounted as `/workspace` inside
the sandbox. Multiple sandboxes can share the same workspace root at the same
time; this is expected to be the common configuration.

Each sandbox also gets persistent scratch home storage mounted at
`/home/agent`. Destroying a sandbox deletes the sandbox and its persistent home,
after confirmation, but never deletes the host workspace root.

Sandboxes persist across Hoosegow server restarts. Terminals are owned by the
currently running Hoosegow server process: browser refresh/reconnect can reattach
to existing terminals, but exiting the Hoosegow server ends its PTY sessions unless
you only care about the sandbox disk/home state.

## Terminals

Terminals are real PTYs rendered with xterm.js in the browser. Local shells run
as host PTYs owned by the Hoosegow server. Sandbox shells are bridged through
Socket.IO to a token-protected in-sandbox `hoosegow-ptyd` controller.

- New sandbox terminals start directly in the shell at `/workspace`; Hoosegow
  does not inject auth/setup banner text into the terminal.
- New local terminals start in the Hoosegow launch workspace.
- Multiple terminals per sandbox are supported. The default limit is 32 per
  sandbox.
- Terminal tabs persist while the server is running. If the browser reconnects,
  Hoosegow lists active PTYs, rejoins them, and replays bounded scrollback.
- Closing a terminal checks for a foreground process when possible and asks for
  confirmation before closing a busy PTY.
- Stopping or destroying a sandbox closes its terminals.

Agent CLI authentication is provided by the prepared base and persisted sandbox
home where applicable. Run the agent command yourself from the terminal.

## Agent CLI Updates

Agent CLIs such as Claude, Codex, Antigravity, and opencode update frequently. Use a
sandbox row `...` menu and choose **Update agent CLIs** to check npm for newer
CLI versions and apply them to that sandbox only.

- Hoosegow checks package versions first and rebuilds the shared prepared base only
  when an update is available or base metadata is missing.
- The selected sandbox is then refreshed onto the current base without changing
  its workspace root, persistent home, resources, or published ports.
- Running sandboxes restart and reopen a terminal.
- Stopped sandboxes stay stopped, so sandboxes can remain frozen until you
  explicitly refresh them.

## MSB Passthrough Wrapper

Hoosegow also ships a tiny Hoosegow-independent wrapper around `msb exec` for tools
that want to run an agent command inside an existing sandbox without opening the
browser UI or talking to the Hoosegow server.

Dry run the generated Microsandbox command:

```bash
scripts/hoosegow-msb --sandbox demo --no-tty --dry-run -- claude -p 'say model slug'
```

Run a non-interactive command:

```bash
scripts/hoosegow-msb --sandbox demo --no-tty -- claude -p 'say model slug'
```

Run from a guest subworkspace:

```bash
scripts/hoosegow-msb --sandbox demo --workspace /workspace/my-repo --no-tty -- pwd
```

Run an interactive command or auth flow:

```bash
scripts/hoosegow-msb --sandbox demo --tty -- claude
```

Defaults are `--user agent` and `--workspace /workspace`. Without `--tty` or
`--no-tty`, the wrapper auto-allocates a PTY only when stdin and stdout are both
terminals. This wrapper does not enforce Hoosegow auth or policy; it uses the same
local Microsandbox authority as `msb exec`.

See `docs/passthrough.md` for the Phase 0 passthrough spec and deferred
Hoosegow-mediated options.

## Published Ports

Use **Published ports** from the Sandboxes menu or a sandbox row menu to expose
a web server running inside the sandbox through `127.0.0.1` on the host.

The default host-port pool is `3000-3099`. You can publish a guest port to an
automatic host port or request a specific host port. Port rows include open,
copy URL, reassign-conflict, and unpublish actions. Some mapping changes apply
on sandbox restart, depending on current sandbox state.

## Base Image

The reusable base image is prepared automatically from `node:22-bookworm` by
default and is named `hoosegow-microsandbox-local`.

The base includes the shared pieces Hoosegow expects inside each sandbox:

- Python and the Hoosegow PTY controller
- Node/npm tooling
- git, gh, nano, ripgrep, tmux, and common CLI dependencies
- Claude Code, Codex, Antigravity, and opencode CLIs installed globally
- Bullpen-derived file-descriptor, network-cap, CA, Codex auth, and Claude IPv6
  workarounds

Normal users do not need to prepare it manually. On first launch, the UI asks
for base status, the server notices a missing snapshot, and setup starts in the
background. While that happens, sandbox creation shows a short setup delay and
base logs are available from the hamburger menu.

Manual setup and rebuild commands remain available for diagnostics:

```bash
python3 hoosegow.py --prepare-base
python3 hoosegow.py --prepare-base --rebuild-base
```

You can also rebuild the base image and inspect base logs from the hamburger menu. If setup
fails, the menu exposes a retry action.

## CLI Options

| Flag | Default | Description |
|---|---|---|
| `--port` | `6060` | UI port. |
| `--host` | `127.0.0.1` | Bind address. Non-loopback binds require auth. |
| `--home` | `~/.hoosegow` | State directory. |
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
| `--shutdown-sandboxes-on-exit` | off | Stop running sandboxes when Hoosegow exits. |
| `--websocket-debug` / `--no-websocket-debug` | off | Enable or disable Socket.IO and Engine.IO logging. |
| `--set-password [USERNAME]` | off | Set or update local login credentials, then exit. Repeatable. |
| `--delete-user USERNAME` | off | Delete configured login users, then exit. Repeatable. |
| `--bootstrap-credentials` | off | Create credentials from `HOOSEGOW_BOOTSTRAP_USER` and `HOOSEGOW_BOOTSTRAP_PASSWORD`, then exit. |
| `--version` | n/a | Print the Hoosegow version and exit. |

Hoosegow rejects browser-blocked ports, such as `6000`, when it would otherwise
open a browser tab. With `--no-browser`, it still starts but prints a warning.

## Authentication

By default, if no credentials are configured, Hoosegow runs without a login screen
on loopback. If you bind to a non-loopback host, Hoosegow refuses to start until
authentication is configured.

```bash
python3 hoosegow.py --set-password admin
```

Credentials are stored as password hashes under `~/.hoosegow/.env` with a stable
session secret.

Environment variables:

- `HOOSEGOW_PRODUCTION=1`: trust forwarded proxy headers and mark session cookies
  `Secure` for TLS deployments.
- `HOOSEGOW_ALLOWED_ORIGINS`: comma-separated extra allowed Socket.IO origins.
- `HOOSEGOW_SESSION_DAYS`: persistent login duration, bounded to 1-365 days.
- `HOOSEGOW_BOOTSTRAP_USER`: username for `--bootstrap-credentials`; default
  `admin`.
- `HOOSEGOW_BOOTSTRAP_PASSWORD`: password for `--bootstrap-credentials`.
- `HOOSEGOW_BOOTSTRAP_FORCE=1`: overwrite existing bootstrapped credentials.

For network exposure, put TLS in front of Hoosegow and set `HOOSEGOW_PRODUCTION=1`.
Hoosegow is still designed as a local single-user developer tool, not a multi-user
hosted service.

## State Layout

Hoosegow state lives under `~/.hoosegow/` by default:

```text
~/.hoosegow/
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

Focused checks for the Hoosegow path:

```bash
pytest -q tests/test_hoosegow_sandbox_service.py \
  tests/test_hoosegow_sandbox_events.py \
  tests/test_hoosegow_terminal_events.py \
  tests/test_hoosegow_product_surface.py \
  tests/test_hoosegow_cli.py tests/test_msb_passthrough.py \
  tests/test_auth.py tests/test_auth_e2e.py
node --check static/app.js
```

Real Microsandbox smokes use the prepared base `hoosegow-microsandbox-local` by
default and require `microsandbox==0.5.3` or newer on the host:

```bash
python3 scripts/microsandbox_port_smoke.py
python3 scripts/pty_controller_microsandbox_smoke.py --verbose
```

Set `HOOSEGOW_MICROSANDBOX_BASE` or pass `--snapshot` to test another base. The
latest local run is recorded in `docs/release-smokes.md`.

The same real smokes are available as an opt-in pytest target:

```bash
HOOSEGOW_RUN_REAL_MICROSANDBOX=1 pytest -q -m real_microsandbox
```

The `0.1.0` tree has removed the copied Bullpen product modules and legacy
deploy script after extracting the Microsandbox runtime workarounds into Hoosegow
modules. Hoosegow mode does not register the legacy product REST routes, serve
legacy product static assets, or eagerly import legacy product modules. See
`docs/spec.md` and `docs/bullpen-excavation.md` for provenance notes.
