# Hoosegow: Functional Specification

> "Toad in a hole for agents." A small, single-binary web app that runs CLI
> coding agents (Claude, Codex, Gemini, opencode, ...) inside Microsandbox
> microVMs and gives the user PTY-backed web terminals into each sandbox.

## 1. Goals and Non-Goals

### Goals
- Keep agents off the host filesystem and kernel by default.
- Provide a frictionless "click -> sandbox -> terminal" experience for one
  user on one machine.
- Vendor/port Bullpen's proven Microsandbox provisioning, base-image
  preparation, dirty runtime workarounds, and xterm.js/Socket.IO terminal stack
  where they fit.
- Ship as a single Python entry point (`hoosegow.py`) with a CDN-loaded Vue 3
  frontend (no build step).

### Non-Goals
- Multi-tenant hosting. Hoosegow is a local developer tool, even when bound to a
  non-loopback interface.
- Tickets, kanban, workers, profiles, stats, commits, scheduling, MCP,
  auto-PR. Those are explicitly cut from Bullpen.
- Remote hosted deployment. No Sprite / Droplet / Docker production story in
  v1.
- Agent orchestration. Hoosegow does not invoke agents itself; the user types
  `claude` / `codex` / etc. into a terminal inside a sandbox.

## 2. User Stories

1. **Isolate an agent session.** "I want to run `claude` inside a sandbox
   mounted at my normal work root, without giving it access to `~/.ssh` or the
   rest of my home directory."
2. **Multiple concurrent sandboxes.** "I want several sandboxes sharing the
   same work root, and I want to switch between them in a single browser tab."
3. **Many terminals per sandbox.** "I want terminals for the dev server, the
   agent, git, tests, logs, and scratch commands, all in the same sandbox."
4. **Browser reattach.** "If I close my browser but leave Hoosegow running, my
   sandboxes and terminals keep running. When I reopen Hoosegow I can reattach."
5. **Tear down cleanly.** "When I'm done, one click destroys the sandbox and
   its scratch storage; the host workspace root is untouched."

## 3. Core Concepts

| Concept | Definition |
|---|---|
| **Workspace root** | A canonical host directory mounted read-write into a sandbox as `/workspace`. This is usually a shared parent directory containing many projects, matching Bullpen Monitor's workspace-root picker, not a single project directory. |
| **Sandbox** | A named Microsandbox microVM with the Hoosegow base image (Python, Node, git, gh, nano, ripgrep, tmux, Claude/Codex/Gemini/opencode CLIs). Has persistent `/home/agent` storage. |
| **Terminal** | A PTY session running inside a sandbox, bridged to a browser xterm.js tab over Socket.IO. |
| **PTY controller** | A small in-sandbox process (`hoosegow-ptyd`) that owns PTYs and exposes a token-protected HTTP control/data API to the host Hoosegow server through a Microsandbox-published localhost port. |
| **Base image** | Reusable Microsandbox snapshot, prepared automatically on first run. Mirrors Bullpen's `deploy-sandbox.py --prepare-base` flow internally. |

A sandbox has 0..N terminals. Multiple sandboxes may mount the same workspace
root concurrently; this is expected to be the common local configuration.

## 4. User Experience

1. Start the server: `python3 hoosegow.py`. Browser opens at
   `http://localhost:6060`.
2. Open the Sandboxes menu and choose **Create sandbox**. Pick a workspace root
   using the server-side directory picker or type a path. This should feel like
   Bullpen Monitor's workspace root selection: choose the top-level work tree
   that contains projects, not a specific project. Give the sandbox a name.
   Click Create.
3. The sandbox appears in the left pane with a status pill (`preparing`,
   `running`, `stopped`, `error`) and any published dev-server URLs.
4. Use a sandbox row action menu to open more terminals. A new xterm.js tab
   opens, already `cd`'d into `/workspace`.
5. Type `claude` (or `codex`, `gemini`, `opencode`) and work normally.

There is no step 6.

### UI Layout

- **Left pane**: compact list of sandboxes. Each row shows name, workspace
  path, status, and an action menu for start, new terminal, stop, details,
  ports, logs, and destroy. The pane boundary is draggable.
- **Main pane**: terminal-first surface with terminal tabs and the active
  xterm.js viewport. Selecting a running sandbox with no open terminal starts
  and focuses a terminal automatically.
- **Top bar**: app title, hamburger menu for sandbox-runtime status/actions, a
  light/dark toggle, and a Socket.IO status dot.
- **Optional Files panel** (stretch): read-only browser of files under the
  sandbox's mounted workspace, scoped to that sandbox.

### Empty / First-Run State

If the Hoosegow base image is not yet prepared, the server starts setup
automatically the first time the app checks base status. The UI should frame
this as a short first-run sandbox-runtime setup delay, not as a command the user
must learn. Sandbox creation remains unavailable until setup succeeds, but the
create modal and hamburger menu show progress and runtime logs. If setup fails,
the hamburger menu exposes retry and rebuild actions.

If auth is enabled, unauthenticated users see the Bullpen-style login page
before the app shell. If auth is disabled, there is no login screen.

## 5. Functional Requirements

### 5.1 Sandbox Lifecycle

- **Create**: name (slug-validated, unique across Hoosegow-known and pre-existing
  Microsandbox instances), workspace root (canonicalized, allowed, existing
  directory, readable and writable), optional vCPU/RAM caps. Shared workspace
  roots are allowed by default and should not trigger a confirmation. Hoosegow
  spins up a Microsandbox instance using the Hoosegow base, mounts the workspace
  root at `/workspace`, mounts a
  per-sandbox persistent home at `/home/agent`, applies the Bullpen-derived
  host FD, guest FD, network, user, CA, IPv6, and CLI bootstrap workarounds in
  §5.9, and reports `running` when a standard Microsandbox exec health check
  returns within 5 seconds. Terminal attach health is reported separately.
- **Stop**: graceful shutdown. Terminals attached to the sandbox are terminated
  with a "sandbox stopped" banner in xterm.
- **Start**: bring a stopped sandbox back up using the same workspace, home,
  resource caps, and persisted port mappings. Revalidate the workspace path
  before start.
- **Destroy**: stop + delete sandbox + delete its persistent home after a
  confirm dialog that lists what will be deleted. Host workspace dir is never
  deleted.
- **Rename**: out of scope for v1. Slug is immutable post-create.

### 5.2 Terminals

- **Open**: backend asks the sandbox's PTY controller to allocate a PTY running
  the agent shell (default `/bin/bash -l`; per-sandbox shell choice deferred
  post-v1). Frontend opens an xterm.js instance bridged through the host Hoosegow
  server over Socket.IO.
- **PTY driver contract**: the driver must support raw byte input/output,
  UTF-8 and control-code fidelity, resize, exit status, EOF propagation,
  signal or close-based teardown, foreground-process detection where available,
  and reconnect to a still-running PTY within the same Hoosegow server process.
- **PTY controller architecture**: v1 requires an in-sandbox `hoosegow-ptyd`
  process. The selected host bridge is HTTP, not a raw TCP stream: a P-1 spike
  proved that Microsandbox-published HTTP ports work, while generic raw TCP
  connections can be accepted and then closed before reaching the guest process.
  `hoosegow-ptyd` therefore binds inside the sandbox on a dedicated controller
  port, published by Microsandbox to host `127.0.0.1` only. The host Hoosegow
  server is the only intended client and the only browser-facing web endpoint.
  Every controller request includes a per-sandbox secret. Unix sockets and raw
  newline JSON remain useful for local development diagnostics, but not for the
  v1 host-to-sandbox bridge.
- **Resize**: forward cols/rows on browser viewport changes.
- **Close**: confirm if a foreground process is detected (best-effort: child
  of PTY leader is not the shell); otherwise close PTY and remove tab.
- **Browser disconnect**: keep PTYs alive for a configurable grace period
  (default 60 seconds) so tab refresh does not kill an in-flight agent. After
  the grace period, close idle PTYs. PTYs with foreground processes remain
  alive until explicit close, sandbox stop, or server exit.
- **Reattach**: when the browser reconnects to the same running Hoosegow server,
  the frontend calls `sandbox:terminal:list` over Socket.IO to discover active
  PTYs, then joins each terminal room by ID and receives bounded replay.
- **Per-sandbox limit**: configurable; default 32 terminals per sandbox. v1
  must be tested with 32 simultaneously open terminals in one sandbox.
- **Output buffering**: server-side ring buffer per PTY with both line and byte
  caps (default 10,000 lines and 5 MiB). On overflow, drop oldest output and
  prepend a single truncation marker to the next replay.

### 5.3 Persistence

Hoosegow state lives under `~/.hoosegow/`:

```
~/.hoosegow/
  .env                   # auth users, secret key, production/session settings
  config.json            # global settings (port, theme, base image name, ...)
  sandboxes/
    <slug>.json          # sandbox manifest: slug, name, workspace path,
                         #   home path, resource caps, dev ports,
                         #   controller host/guest port and token,
                         #   created-at, last-status, Microsandbox instance ID
    <slug>/home/         # bind-mounted into sandbox as /home/agent
  base/                  # Microsandbox base snapshot artifacts (if any)
  logs/
    server.log
    sandbox-<slug>.log
```

Sandboxes and sandbox homes persist across Hoosegow server restarts. Terminals do
not. A Hoosegow server exit terminates PTY sessions owned by that server; the
sandbox disk/home state remains. On server boot, Hoosegow reconciles
`sandboxes/*.json` against Microsandbox's view of running VMs and updates
statuses accordingly. Manifests are written atomically (temp + rename).

### 5.4 Configuration

Single CLI: `hoosegow.py`.

| Flag | Default | Description |
|---|---|---|
| `--port` | `6060` | UI port. |
| `--host` | `127.0.0.1` | Bind address. Non-loopback binds require auth. |
| `--home` | `~/.hoosegow` | State directory. |
| `--no-browser` | off | Do not auto-open. |
| `--prepare-base` | off | Build the base snapshot and exit. |
| `--rebuild-base` | off | Force a base rebuild. |
| `--base-image` | `node:22-bookworm` | OCI source for base prep. |
| `--vcpus` | `4` | Default per-sandbox vCPU cap. |
| `--memory-mib` | `4096` | Default per-sandbox RAM cap. |
| `--max-sandboxes` | `8` | Host-wide running-sandbox admission cap. |
| `--max-total-vcpus` | detected cores | Host-wide admitted vCPU cap. |
| `--max-total-memory-mib` | 75% host RAM | Host-wide admitted RAM cap. |
| `--terminal-limit` | `32` | Default per-sandbox terminal cap. |
| `--port-pool` | `3000-3099` | Host-published dev-server port pool. |
| `--workspace-root` | cwd and `$HOME` | Repeatable canonical browse root for the picker. |
| `--host-nofile` | `12000` | Target host `RLIMIT_NOFILE` before creating Microsandbox runtimes. |
| `--guest-nofile` | `65536` | Target in-sandbox `agent` user `RLIMIT_NOFILE`. |
| `--network-max-connections` | `8192` | Microsandbox network max concurrent guest connections. |
| `--shutdown-sandboxes-on-exit` | off | Stop sandboxes when Hoosegow exits. |
| `--set-password [USERNAME]` | off | Bullpen-style interactive set/update of login user(s), then exit. Repeatable. |
| `--delete-user USERNAME` | off | Delete configured login user(s), then exit. Repeatable. |
| `--bootstrap-credentials` | off | Create credentials from `HOOSEGOW_BOOTSTRAP_USER` (default `admin`) and `HOOSEGOW_BOOTSTRAP_PASSWORD`, then exit. |

If the UI port is known to be blocked by Chromium-based browsers, such as
`6000`, Hoosegow must fail early when auto-opening a browser and must warn when
started with `--no-browser`.

There are no subcommands and no deployment modes. Environment variables:

- `HOOSEGOW_PRODUCTION=1`: trust forwarded proxy headers and mark session cookies
  `Secure` for TLS deployments.
- `HOOSEGOW_ALLOWED_ORIGINS`: comma-separated extra allowed Socket.IO origins.
- `HOOSEGOW_SESSION_DAYS`: persistent login duration, bounded to 1-365 days.
- `HOOSEGOW_BOOTSTRAP_FORCE=1`: overwrite existing bootstrapped credentials.

### 5.5 Base Image

- Built once via a direct port of Bullpen's `deploy-sandbox.py` base-prep
  logic, not a greenfield rewrite. Contents: Python 3, Node 22, bash,
  bubblewrap, ca-certificates, curl, gh, git, iproute2, jq, Python venv
  support, nano, netcat (`nc`), ripgrep, strace, tmux, `claude`, `codex`,
  `gemini`, and `opencode` CLIs.
  Install packages include `@anthropic-ai/claude-code`, `@openai/codex`,
  `@google/gemini-cli`, and `opencode-ai`.
  Agent CLIs are not pre-authenticated; the user authenticates inside the
  running sandbox.
- Base prep uses Bullpen's prepare-sandbox -> local snapshot -> validation
  sequence:
  1. create a temporary Microsandbox from the OCI source image;
  2. install OS packages with `DEBIAN_FRONTEND=noninteractive` and
     `--no-install-recommends`;
  3. install Python dependencies into `/opt/hoosegow-venv`;
  4. install npm CLIs with audit/fund/progress disabled and dev deps omitted;
  5. write `/opt/hoosegow-microsandbox-base-versions.txt`;
  6. stop the prepare sandbox, create the local snapshot, then boot a fresh
     validation sandbox from the snapshot and verify every required CLI.
- Codex validation must keep Bullpen's architecture-specific package integrity
  check for `@openai/codex-linux-arm64` / `@openai/codex-linux-x64`, because a
  `codex --version` alone is not enough to prove the packaged native binary is
  present.
- Hoosegow detects base image absence and starts preparation automatically; manual
  `--prepare-base` remains available for diagnostics.
- Acceptance target: base prep <=10 minutes and base artifacts <=4 GiB on the
  representative dev machine. The first implementation milestone includes a
  measurement script that records the actual values.
- Terminals start directly in the shell. Agent CLI authentication help belongs
  in documentation and setup affordances, not injected terminal output.

### 5.6 Network and Dev-Server Ports

- **Outbound** from sandboxes is allowed by default; agents need it. A
  per-sandbox egress allow-list is deferred post-v1.
- **Inbound** dev-server publishing is explicit. The user clicks **Publish
  Port**, enters an internal TCP port (for example 3000, 5173, 8000), and Hoosegow
  allocates a host port from `--port-pool`.
- A sandbox may have multiple published TCP ports. Port mappings persist in the
  sandbox manifest and are restored on sandbox start if the host port is free.
  If the host port is occupied, Hoosegow marks the mapping `conflict` and offers
  "reassign".
- Published ports are bound to the same interface as Hoosegow itself. A
  loopback-bound Hoosegow cannot accidentally publish a sandbox port to the LAN.
- Sandbox cards show each mapping as `http://<host>:<host_port> -> :<guest_port>`
  with copy/open actions. Hoosegow does not auto-detect framework ports in v1.

### 5.7 Sandbox Naming and Collisions

- Slugs are `[a-z0-9][a-z0-9-]{0,30}`.
- Before creating a Microsandbox VM, Hoosegow lists existing Microsandbox
  instances on the host. If the slug collides with an instance Hoosegow does not
  own (for example a Bullpen instance from a sibling tool), creation is refused
  with an actionable error.
- Sandbox manifests record the Microsandbox instance ID so subsequent
  starts/stops target the correct VM even if names are reused later.

### 5.8 Resource Admission

- Per-sandbox vCPU/RAM caps default from CLI flags and can be overridden in the
  New Sandbox modal.
- Host-wide admission checks run before create/start. If admitting a sandbox
  would exceed `--max-sandboxes`, `--max-total-vcpus`, or
  `--max-total-memory-mib`, Hoosegow refuses the operation with a clear error and
  a list of currently running sandboxes.
- These are planning/admission limits, not a hard anti-DoS guarantee. Runtime
  enforcement remains Microsandbox's vCPU/RAM cap.

### 5.9 Bullpen Microsandbox Deployment Inheritance

Hoosegow reuses the maximum practical amount of Bullpen's `deploy-sandbox.py`
architecture through extracted Hoosegow modules. The legacy deploy script itself
is no longer present in the runtime tree; its reusable pieces live in
`server/microsandbox_runtime.py`, `server/sandbox_bootstrap.py`, and
`server/base.py`. Operational workarounds remain unless a Hoosegow-specific test
proves they are unnecessary.

Required inherited pieces:

- **Microsandbox SDK adapter**: keep Bullpen's `MicrosandboxRuntime` pattern
  that imports the SDK lazily, verifies expected symbols, supports sync or async
  SDK return values, can call `microsandbox.install()` when available, and
  normalizes `Sandbox.get/create/remove/stop`, `Snapshot`, `Volume`, `Network`,
  `Image.oci`, `AttachOptions`, `ExecOptions`, `Stdin`, and event classes.
- **Prepared snapshot plumbing**: use Bullpen's base existence lookup,
  snapshot-path extraction, prepare-sandbox creation, validation-sandbox
  creation, snapshot creation, and cleanup pattern.
- **Supported-host gate**: fail early unless the host is Apple Silicon macOS or
  Linux with KVM, matching Bullpen's Microsandbox assumptions.
- **Host FD mitigation**: call the Bullpen-derived `ensure_host_nofile()`
  before final sandbox creation. Default target is 12000. If the soft limit
  cannot be raised, warn loudly; do not silently continue as if capacity is
  normal.
- **Guest FD mitigation**: write `/etc/security/limits.d/hoosegow-fd.conf` for the
  `agent` user with soft/hard `nofile=65536`, then verify via
  `su -s /bin/bash agent -c 'ulimit -Sn; ulimit -Hn'`. This preserves Bullpen's
  fix for FD pressure surfacing as misleading TLS, DNS, and filesystem errors.
- **Network cap mitigation**: set `Network.max_connections` to
  `--network-max-connections` (default 8192) instead of accepting the SDK
  default. If the installed SDK does not expose `max_connections`, fail with an
  actionable Microsandbox upgrade error.
- **Runtime user creation**: create an `agent` user/group inside the sandbox
  with UID/GID from the invoking host user where practical, matching Bullpen's
  group-collision handling. Prepare `/workspace`, `/home/agent/logs`,
  `/home/agent/bin`, `/home/agent/.codex`, and `/var/lib/hoosegow`; validate
  write access after ownership changes.
- **Small create-time env**: keep Bullpen's pattern of passing only minimal
  env (`HOME`, `USER`, `LOGNAME`) to `Sandbox.create`, then exporting the full
  runtime env inside exec/attach commands. This avoids SDK/runtime env bloat.
- **Command helpers**: port Bullpen's `run_sandbox_shell`,
  `run_configured_sandbox_shell`, `run_as_<user>`, output normalization, labeled
  error wrapping, and secret redaction. Hoosegow logs must redact bootstrap
  passwords and common provider tokens.
- **CA environment**: set `SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`,
  `SSL_CERT_DIR=/etc/ssl/certs`, `NODE_EXTRA_CA_CERTS` to the system bundle,
  and `BUN_OPTIONS=--use-system-ca` for agent auth and verification commands.
- **Claude IPv6 mitigation**: apply Bullpen's scoped guest IPv6 disablement
  before Claude auth/verification, and for v1 apply it during sandbox bootstrap
  so terminal-launched Claude uses IPv4. This exists because Microsandbox guest
  IPv6 has produced TLS EOFs that Claude reports as certificate failures.
- **Codex file-auth setup**: initialize `/home/agent/.codex/config.toml` with
  `cli_auth_credentials_store = "file"`, clear stale Codex tmp/lock state
  copied from Bullpen's flow, and verify the real Codex binary path. Hoosegow does
  not serialize all Codex invocations in v1, but it should keep the file-backed
  auth setting.
- **Localhost callback bridge helper**: keep Bullpen's URL detection and
  sandbox-local callback delivery helper available for future provider-login
  flows. Hoosegow's terminal-first UX may make this less visible, but the helper
  should be ported rather than rediscovered.
- **PTY controller launch point**: extend Bullpen's sandbox bootstrap flow to
  install and start `hoosegow-ptyd` inside each sandbox after runtime directories,
  FD limits, network caps, CA env, IPv6 mitigation, and Codex config are in
  place. Start it with the HTTP transport on an internal controller port
  published to host loopback only; do not attempt a raw TCP controller bridge
  through Microsandbox published ports.
- **Health and detach checks**: use Bullpen-style wait loops for HTTP health
  where applicable, and verify a sandbox is still running after detach/start.
- **Port diagnostics**: keep Bullpen's host-port-in-use and `lsof` owner
  diagnostic pattern for port-pool conflicts so errors tell the user what is
  listening.

Bullpen's Node front proxy is not required for Hoosegow v1 because Hoosegow itself is
the host web server, but its useful constraints still apply to any future
in-sandbox proxy: cache static assets, keep upstream connections short-lived,
strip hop-by-hop headers, and handle WebSocket upgrades explicitly.

## 6. Security Model

Hoosegow's value proposition is isolation, so this section is load-bearing.

### Threat Model

- **Adversary**: a misbehaving or actively malicious agent process running in a
  sandbox. Assumes the agent is not a kernel-exploit-grade adversary.
- **Assets to protect**: host filesystem outside the chosen workspace root,
  shell credentials, browser cookies, ssh keys, unrelated work outside the
  mounted root, and host kernel.

### Controls

1. **Microsandbox microVM isolation**: kernel boundary between agent and host.
2. **Filesystem scoping**: the only host directory mounted into a sandbox is
   the user-selected workspace root. Projects inside that root are intentionally
   visible to the sandbox; `~/.ssh`, `~/.aws`, and other host paths outside the
   root are not mounted.
3. **No host shell**: Hoosegow deliberately does not expose a host-side terminal.
   Every terminal runs inside a sandbox.
4. **Bullpen-style optional auth with network-bind guard**:
   - Credentials are configured explicitly via `--set-password` or
     `--bootstrap-credentials`. If no credentials exist, auth is disabled and
     local loopback use has no login screen.
   - If `--host` is not loopback (`127.0.0.1`, `localhost`, `::1`), Hoosegow
     refuses to start unless auth credentials already exist.
   - Auth supports multiple local users, stored as password hashes in
     `~/.hoosegow/.env` mode 0600. This is not multi-tenancy; all authenticated
     users control the same local Hoosegow instance.
   - Browser sessions are cookie-based, HTTP-only, SameSite=Lax, persistent by
     default for 30 days, and backed by a stable secret key in `~/.hoosegow/.env`.
   - Login has CSRF protection, session fixation protection, and in-process
     per-IP/per-user throttling.
   - For non-localhost use, TLS is expected in front of Hoosegow (Caddy, nginx,
     Cloudflare Tunnel, etc.). `HOOSEGOW_PRODUCTION=1` enables secure cookies and
     forwarded-proxy handling.
5. **Socket.IO origin/auth checks**: WebSocket upgrades require an authenticated
   session when auth is enabled and must come from loopback, same-origin,
   forwarded same-origin, or `HOOSEGOW_ALLOWED_ORIGINS`.
6. **CSRF checks**: state-changing REST calls require same-origin plus an
   authenticated session when auth is enabled. Login/logout use CSRF tokens.
7. **Canonical workspace-root validation**:
   - All picker and free-text paths are expanded and resolved with `realpath`.
   - Paths must be descendants of one configured browse root from
     `--workspace-root`.
   - Symlink escapes outside browse roots are rejected.
   - Hard-reject `/`, `/etc`, `/var`, `/usr`, `/bin`, `/sbin`, `/boot`,
     `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config`, `~/.hoosegow`, and any ancestor
     of `~/.hoosegow`.
   - Warn and require typed confirmation for `$HOME` itself and for paths whose
     ancestors contain sensitive directories.
   - Revalidate on sandbox create and start.
8. **Process boundary on host**: the Hoosegow server runs as the invoking user. No
   setuid, no root requirement from Hoosegow itself.
9. **Agent auth tokens stay inside the sandbox** as a consequence of the
   sandbox model. Hoosegow neither reads nor writes agent auth files.
10. **No arbitrary host command execution endpoints**: HTTP/WS API accepts only
    sandbox CRUD, terminal CRUD, PTY forwarding, directory listing rooted at
    allowed paths, dev-port mapping, and base-image prep.

### Things Explicitly Not Secured in v1

- Resource exhaustion inside a sandbox beyond Microsandbox's CPU/RAM caps.
- Side-channel attacks across sandboxes on the same host.
- Network egress filtering. An agent in a sandbox can talk to the open internet.
- Plain HTTP on untrusted networks. Remote exposure must put TLS in front.

## 7. Architecture

```
hoosegow.py                  # CLI entry, arg parsing, browser launch
server/
  app.py                  # Flask + Flask-SocketIO factory, routes
  auth.py                 # Bullpen-style password/session middleware
  microsandbox_runtime.py # Ported Bullpen SDK adapter + base snapshot helpers
  sandboxes.py            # Sandbox lifecycle, Microsandbox bindings
  sandbox_bootstrap.py    # Ported deploy-sandbox runtime setup workarounds
  pty_driver.py           # PTY-inside-VM driver interface + implementation
  terminals.py            # PTY session manager, Socket.IO bridge, replay
  base.py                 # Base-image preparation + status detection
  persistence.py          # Atomic writes for ~/.hoosegow state
  validation.py           # Path / slug / port / resource validation
  picker.py               # Server-side directory listing for picker
static/
  index.html              # CDN Vue/Socket.IO/Lucide plus vendored xterm.js
  login.html              # Public only when auth is enabled
  app.js                  # Top-level Vue app, sandbox + tab state
  components/
    SandboxList.vue
    TerminalTab.vue
    NewSandboxModal.vue
    PublishPortModal.vue
    BasePrepView.vue
  style.css
guest/
  hoosegow-ptyd.py           # In-sandbox PTY controller installed in base image
```

- **Backend**: Flask + Flask-SocketIO (threading async mode), borrowed from
  Bullpen's known-good setup.
- **Transport**: Socket.IO for terminal I/O and sandbox status events; REST for
  sandbox, terminal, picker, base, and port CRUD.
- **Frontend**: Vue 3, Socket.IO client, and Lucide from CDN. No npm build.
  xterm.js is vendored under `static/vendor/xterm/`.
- **Sandboxing**: Microsandbox Python SDK.
- **Host-to-controller bridge**: token-protected HTTP RPC and long-poll event
  reads from host Hoosegow to `hoosegow-ptyd`, over a Microsandbox-published
  localhost-only port. Browser traffic still goes only to the host Hoosegow
  Flask/Socket.IO server.

### Relationship to Bullpen

Hoosegow is not a slimmed-down Bullpen; it is a different product that shares
specific subsystems. Bullpen's usage model is "drive an agent by ticket";
Hoosegow's is "work with the agent in a terminal." Concretely:

- **Reused, largely unchanged**: base-image preparation pipeline,
  Microsandbox SDK adapter, host/guest FD fixes, network cap fixes, runtime
  user setup, CA/IPv6/Codex auth workarounds, port diagnostics, and sandbox
  command helpers from `deploy-sandbox.py`.
- **Reused, adapted**: Flask/Socket.IO setup, local auth model, CDN Vue shape,
  and xterm.js terminal bridge.
- **New**: a PTY that runs inside the sandbox VM rather than on the host.
- **Removed wholesale**: tickets, workers, kanban, stats, commits, MCP, the
  Manager/host-orchestration model. None of this has a Hoosegow analog.

### Implementation Strategy

Implementation starts from a short PTY-controller spike, then Bullpen
excavation. It does not start from an empty tree. The goal is to preserve
Bullpen's infrastructure scars and delete Bullpen's product model.

After the P-1 PTY controller spike, copy the Bullpen tree into Hoosegow, then make
small, test-backed cuts. Current implementation status: the excavation is
complete for the v1 runtime; copied Bullpen product modules, the legacy
`bullpen.py` entry point, `deploy-sandbox.py`, and deploy-only tests have been
removed after extraction.

- Keep first: `server/auth.py`, the relevant Flask/Socket.IO app bootstrap,
  Socket.IO origin policy, login page, terminal UI component, terminal
  validation, static Vue/CDN shape, deploy-sandbox Microsandbox runtime code,
  base-prep code, and runtime workarounds.
- Excavate or split first: `deploy-sandbox.py` into `microsandbox_runtime.py`,
  `sandbox_bootstrap.py`, and `base.py`; replace `server/terminal.py` with an
  inside-VM PTY driver.
- Delete early: tickets/tasks, workers, service workers, MCP, profiles, model
  catalog, commits, stats, kanban, teams, transfers, worktrees, prompt
  hardening, and Bullpen-specific CLI subcommands.
- Replace with Hoosegow concepts: sandbox registry, sandbox manifests, workspace
  picker, resource admission, dev-port publishing, terminal tabs scoped to
  sandboxes.

The rule of thumb is: infrastructure survives until a Hoosegow test proves it can
be removed; product features die unless the Hoosegow spec names them.

## 8. API Sketch

REST is intentionally small and boring: health, auth, static/file fetches,
and any future download/upload-style operation that fits HTTP better than a
live command acknowledgement.

```
GET    /health
GET    /login
GET    /login/csrf
POST   /login
POST   /logout

```

Socket.IO is the primary control plane for sandbox lifecycle, terminal
lifecycle, dev-port publishing, and base preparation. These operations are
stateful, long-running, and naturally broadcast status to every connected tab;
this matches the Bullpen architecture better than REST-first CRUD.

```
sandbox:list       {}                                    client -> server ack
sandbox:create     { name, workspace_root, vcpus?, memory_mib? } client -> server ack
sandbox:get        { id }                                client -> server ack
sandbox:start      { id }                                client -> server ack
sandbox:stop       { id }                                client -> server ack
sandbox:destroy    { id, purge? }                        client -> server ack
sandboxes:updated  { sandboxes }                         server -> client
sandbox:status     { id, status, terminal_status? }      server -> client
sandbox:error      { id, error }                         server -> client
sandbox:destroyed  { id }                                server -> client

sandbox:terminal:list   { sandbox_id? }                  client -> server ack
sandbox:terminal:open   { sandbox_id, cols, rows }       client -> server ack
sandbox:terminal:join   { terminal_id }                  client -> server ack
sandbox:terminal:input  { terminal_id, data }            client -> server
sandbox:terminal:resize { terminal_id, cols, rows }      client -> server
sandbox:terminal:close  { terminal_id }                  client -> server ack
terminal:replay    { term_id, data, truncated }          server -> client
terminal:output    { term_id, data }                     server -> client
terminal:exit      { term_id, exit_code }                server -> client

port:list          { sandbox_id }                        client -> server ack
port:publish       { sandbox_id, guest_port, host_port? } client -> server ack
port:unpublish     { sandbox_id, host_port }             client -> server ack
ports:updated      { sandbox_id, ports }                 server -> client

base:status        {}                                    client -> server ack
base:prepare       { rebuild? }                          client -> server ack
base:log           { line }                              server -> client
workspace:browse   { path? }                             client -> server ack
```

## 9. Observability

- `~/.hoosegow/logs/server.log`: structured JSON-lines server log with request IDs
  and sandbox IDs.
- `~/.hoosegow/logs/sandbox-<slug>.log`: per-sandbox Microsandbox lifecycle events
  (create, start, stop, exit code, errors).
- Log rotation: size-based, 10 MiB x 5 files per log.
- No telemetry, no crash reporting, no external network calls from the Hoosegow
  process itself except those required for base-image preparation.
- Logs must not include PTY payloads, passwords, session cookies, auth tokens,
  full directory-picker listings, or agent credential file contents. Debug mode
  may log additional structural metadata but never secrets or PTY text.

## 10. Testability

- **Unit tests** for path validation, slug validation, port-pool allocation,
  resource admission, persistence, manifest reconciliation, auth, and terminal
  session-manager state.
- **Integration tests (host-only)**: fake Microsandbox driver using local
  subprocesses in temp dirs. CI can run end-to-end flows without a microVM.
- **Real Microsandbox tests**: tagged suite gated on Microsandbox availability.
- **PTY tests**: byte-level fidelity for input/output, UTF-8, control codes,
  resize, EOF, exit status, replay truncation, and 32-terminal concurrency.
- **Auth tests**: no-login loopback when no credentials exist; refusal to start
  on non-loopback without credentials; login/logout; throttling; CSRF; secure
  cookies in `HOOSEGOW_PRODUCTION=1`; Socket.IO auth/origin rejection.
- **Browser smoke tests**: prepare/fake base -> create sandbox -> open terminal
  -> run `echo hello` -> publish a port -> tear down.

## 11. Detailed Implementation Plan

### P-1 - PTY Controller Spike (0.5-1 day, do first)

Objective: prove the required in-sandbox PTY controller shape before the
Bullpen excavation begins. Status: complete for the first build spike; details
live in `docs/pty-controller-spike.md`.

Decision: Hoosegow v1 defaults to an in-sandbox `hoosegow-ptyd` process. The host
server remains the only browser-facing web server. The spike exists to settle
protocol details and to check whether Microsandbox's `attach()` / `exec_stream()`
can simplify the controller, not to postpone the controller decision.

Tasks:

- Built `guest/hoosegow-ptyd.py`: open a PTY with `os.openpty()` +
  `subprocess.Popen()`, run `/bin/bash -l`, stream base64 PTY bytes, resize,
  close, record bounded event history, and report exit status.
- Implemented two transports: raw newline JSON for local diagnostics and HTTP
  RPC plus long-poll `/events` for the production Microsandbox bridge.
- Proved plain Microsandbox published HTTP works with a throwaway HTTP server.
- Proved raw generic TCP is not a viable bridge through the published-port
  path on the target host: a raw echo server listens in the guest, but host
  connections receive EOF without the guest server seeing a client.
- Proved `hoosegow-ptyd --http 0.0.0.0:<port>` inside a throwaway Microsandbox
  can be reached through host `127.0.0.1:<port>` and can run a PTY command,
  resize, emit output, return EOF, and preserve exit status.
- Deferred SDK `attach()` / `exec_stream()` probing; the HTTP controller path
  is good enough to start Bullpen excavation and should remain the default
  unless the SDK later proves a strictly simpler full PTY contract.

Verification gate:

- `python3 -m py_compile guest/hoosegow-ptyd.py scripts/*.py`
- `python3 scripts/pty_controller_smoke.py`
- `python3 scripts/pty_controller_http_smoke.py`
- `python3 scripts/microsandbox_port_smoke.py`
- `python3 scripts/microsandbox_raw_tcp_smoke.py` currently documents the raw
  TCP failure mode and is expected to fail until Microsandbox behavior changes.
- `python3 scripts/pty_controller_microsandbox_smoke.py --verbose`

The real Microsandbox scripts default to `hoosegow-microsandbox-local`; use
`HOOSEGOW_MICROSANDBOX_BASE` or `--snapshot` to target another prepared base.

### P0 - Bullpen Excavation Baseline (0.5-1 day)

Objective: create a Hoosegow codebase by copying Bullpen and proving the copied
baseline still runs before any cuts.

Tasks:

- Copy Bullpen source into the Hoosegow repo as ordinary files. Do not use
  `git subtree` for v1; preserve provenance through `docs/bullpen-excavation.md`
  entries that record original Bullpen paths and notable commits/workarounds.
- Rename executable entrypoint to `hoosegow.py`, state root to `~/.hoosegow`, env
  prefixes to `HOOSEGOW_`, user-facing strings to Hoosegow, and app port to 6060.
- Keep Bullpen's `requirements.txt`, Flask/Socket.IO setup, static CDN shape,
  login page, auth tests, Socket.IO CORS tests, terminal tests, and
  deploy-sandbox tests initially; remove deploy-only tests once the extracted
  Hoosegow runtime/base modules carry equivalent coverage.
- Add an explicit `docs/bullpen-excavation.md` map listing copied files,
  original Bullpen paths, retained subsystems, deleted subsystems, renamed
  environment variables, and known dirty workarounds retained from
  `deploy-sandbox.py`.

Verification gate:

- `python3 -m py_compile hoosegow.py server/*.py`
- Auth tests still pass after `BULLPEN_` -> `HOOSEGOW_` renaming.
- App starts on `127.0.0.1:6060`, `/health` returns 200, and login behavior
  matches Bullpen's optional-auth model.

### P1 - Remove Bullpen Product Surface (1-2 days)

Objective: delete ticket/worker product code while keeping the server alive.

Tasks:

- Remove CLI subcommands for `mcp`, `mcp-token`, and `ticket`; keep only server
  startup, auth management, base-image flags, and sandbox flags.
- Remove backend modules for tasks, workers, service workers, MCP, profiles,
  model catalog, commits, stats, teams, transfers, worktrees, scheduler, and
  Bullpen manager orchestration.
- Remove frontend tabs/components for Kanban, tickets, workers, commits, stats,
  files, live-agent chat, and worker focus.
- Keep and simplify shared UI pieces: top bar, left pane shell, toast
  container, terminal tab, login page, style variables, Socket.IO connection
  lifecycle.
- Replace Bullpen's initial state payload with a minimal Hoosegow app-state
  payload: auth/user status, base status, sandbox list, active terminals.

Verification gate:

- No imports of deleted Bullpen modules.
- App still starts, serves static assets, connects Socket.IO, and shows an
  empty Hoosegow shell.
- Remaining tests are either passing or intentionally moved to
  `tests/bullpen_archived/` for reference.

### P2 - Extract Microsandbox Infrastructure (1-2 days)

Objective: turn `deploy-sandbox.py` from a Bullpen deploy script into Hoosegow's
runtime substrate, then delete the legacy script.

Tasks:

- Split copied `deploy-sandbox.py` into:
  - `server/microsandbox_runtime.py`: SDK adapter, sync/async normalization,
    snapshot lookup, create/remove/stop/get, port diagnostics, supported-host
    detection.
  - `server/sandbox_bootstrap.py`: `agent` user creation, FD limits, network
    caps, CA env, Claude IPv6 mitigation, Codex file-auth setup, mount checks.
  - `server/base.py`: prepare sandbox, OS package install, Python venv install,
    npm CLI install, version manifest, snapshot creation, validation sandbox.
- Rename Bullpen paths/users/env vars to Hoosegow equivalents:
  `/home/bullpen` -> `/home/agent`, `/var/lib/bullpen` -> `/var/lib/hoosegow`,
  `/opt/bullpen-venv` -> `/opt/hoosegow-venv`.
- Preserve defaults: host nofile 12000, guest nofile 65536,
  network max connections 8192, source image `node:22-bookworm`.
- Keep deploy-time secret redaction and labeled sandbox command errors.
- Keep the localhost callback bridge helper even if not exposed in the v1 UI.

Verification gate:

- Ported runtime/base tests pass against fake SDK objects.
- `--prepare-base` reaches the expected Microsandbox SDK calls in fake-driver
  tests.
- Codex integrity command, Claude IPv6 mitigation, FD-limit setup, and
  network-cap mutation are unit-covered.
- The legacy `deploy-sandbox.py` file and Bullpen proxy are absent from the
  runtime tree after extraction.

### P3 - Hoosegow Persistence and Validation (1 day)

Objective: replace Bullpen workspace state with Hoosegow sandbox manifests.

Tasks:

- Implement atomic `~/.hoosegow/config.json` and `sandboxes/<slug>.json` writes.
- Implement sandbox manifest schema: slug, display name, workspace path,
  canonical workspace path, home path, resource caps, published ports,
  created-at, last-status, Microsandbox instance ID.
- Implement startup reconciliation against Microsandbox instances.
- Implement slug validation, canonical workspace path validation, browse-root
  validation, symlink escape rejection, blocklist/typed-confirm warnings,
  port-pool validation, and resource admission.
- Keep Bullpen's validation style and tests where directly reusable.

Verification gate:

- Unit tests cover manifest atomicity, reconciliation states, slug collisions,
  path edge cases, resource-admission failures, and port conflicts.

### P4 - Sandbox Lifecycle API and UI (2-3 days)

Objective: create/start/stop/destroy sandboxes without terminals.

Tasks:

- Implement Socket.IO commands for sandbox list/create/get/start/stop/destroy.
- Implement base status and base preparation over Socket.IO, streaming
  `base:log` events to all authenticated tabs.
- Implement server-side workspace picker over Socket.IO, rooted at configured
  browse roots, with the UX copied from Bullpen Monitor's workspace-root
  selection model.
- Implement left pane sandbox cards, New Sandbox modal, base-prep first-run
  view, status pills, resource readouts, and destroy confirmation.
- Implement slug collision checks against Hoosegow manifests and foreign
  Microsandbox instances.
- Treat shared workspace roots as normal. Do not block or warn when multiple
  sandboxes use the same root.

Verification gate:

- Fake-driver browser smoke: base ready -> create sandbox -> stop -> start ->
  destroy, with all lifecycle actions driven through Socket.IO.
- Host-only integration tests do not require real Microsandbox.

### P5 - Dev-Port Publishing (1 day)

Objective: expose sandbox dev servers through explicit port mappings.

Tasks:

- Implement persisted mappings `{guest_port, host_port, status}`.
- Allocate host ports from `--port-pool`; report `conflict` if occupied.
- Restore mappings on start when possible; offer reassign when not.
- Add Publish Port modal and mapping chips with open/copy/delete actions.
- Use Bullpen-style `lsof` diagnostics in API errors.

Verification gate:

- Unit tests cover allocation, conflict, restore, delete, and reassign.
- Fake-driver test confirms mapping state survives stop/start.

### P6 - PTY Controller Integration (2-3 days)

Objective: turn the P-1 proof into the production terminal driver.

Tasks:

- Add `guest/hoosegow-ptyd.py` to the base image and start it during sandbox
  bootstrap.
- Implement `server/pty_driver.py` as the host-side client for `hoosegow-ptyd`.
- Define controller operations over HTTP: create PTY (`POST /rpc op=open`),
  read stream (`GET /events?id=&since=&timeout=`), write bytes, resize, close,
  query foreground state, query status, and shutdown.
- Add controller authentication/authorization at the transport layer using a
  per-sandbox secret stored in the sandbox manifest and injected at bootstrap.
- Record the controller contract in tests before wiring the browser UI.

Verification gate:

- Real Microsandbox terminal smoke runs `echo hello` through `hoosegow-ptyd`.
- Controller refuses connections without the per-sandbox secret.
- PTY survives browser disconnect while the Hoosegow server remains running.

### P7 - Terminal Manager and Browser Reattach (2-4 days)

Objective: adapt Bullpen's terminal stack from host PTYs to inside-VM PTYs.

Tasks:

- Keep Bullpen's `TerminalManager` threading, locking, event naming patterns,
  cleanup-at-exit, resize validation, and xterm.js component shape.
- Replace `pty.openpty()` / `subprocess.Popen()` with `PtyDriver.open()` for
  inside-sandbox sessions.
- Add per-PTY ring buffers with line and byte caps.
- Add Socket.IO terminal discovery and `sandbox:terminal:join` replay.
- Implement browser-disconnect grace period and foreground-process close
  confirmation where the driver can report foreground state.
- Raise per-sandbox terminal limit to 32 and remove owner-SID-only ownership so
  reconnecting browsers can rediscover terminals.

Verification gate:

- Fake-driver terminal tests cover open/input/output/resize/close/replay.
- Real Microsandbox terminal smoke runs `echo hello`.
- 32 terminals can be opened in one sandbox without server errors.

### P8 - Polish and Release Hardening (2-3 days)

Objective: make the first release usable and documented.

Tasks:

- Add theme toggle, toasts, logs viewer, base rebuild UI, and clear empty/error
  states.
- Write README, `docs/security.md`, and `docs/bullpen-excavation.md`.
- Measure base prep time and artifact size opportunistically; record results
  but do not block early implementation on the 10 minute / 4 GiB target.
- Run tagged real-Microsandbox integration tests on the target dev machine.
- Cut `0.1.0`.

Verification gate:

- Browser smoke covers prepare/create/terminal/port/destroy.
- Real-Microsandbox suite passes or has documented host prerequisite skips.
- No unresolved `Bullpen` user-facing strings remain except attribution/docs.

### Post-v1
- Optional Files panel.
- Per-sandbox network egress allow-list.
- Per-sandbox shell choice (zsh, fish).
- Snapshot / clone sandbox.
- "Offline" toggle.
- Sandbox rename.

## 12. Issues to Resolve

No open product decisions are blocking implementation planning.

Implementation watchpoints:

1. **Base-image budget.** Low priority during excavation. Measure prep time and
   artifact size before v1; trim or raise the target only if the measured cost
   is painful.
2. **License/attribution.** Deferred while private/local. Revisit before public
   release or distribution.

---

End of spec.
