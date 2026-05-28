# Bullpen Excavation Log

This file records the Bullpen code/architecture imported into Toady and the
workarounds that should survive excavation unless a Toady-specific test proves
they are unnecessary.

## Source

- Local source: `/Users/bill/aistuff/bullpen`
- Initial import date: 2026-05-27
- Import style: ordinary file copy, not subtree/vendor metadata

## Initial Imported Paths

| Toady path | Bullpen source path | Initial disposition |
|---|---|---|
| `bullpen.py` | `bullpen.py` | Reference entrypoint during excavation; delete after `toady.py` owns startup. |
| `toady.py` | `bullpen.py` | Working Toady entrypoint seed. Needs rename/cut passes. |
| `deploy-sandbox.py` | `deploy-sandbox.py` | Primary source for Microsandbox runtime, base prep, and dirty workarounds. |
| `deploy/` | `deploy/` | Keep `deploy/microsandbox/` proxy patterns and deployment notes as reference; non-local deploy targets are post-v1 or delete candidates. |
| `server/` | `server/` | Excavate auth, Socket.IO, terminal, validation, persistence, and deploy-adjacent helpers; delete Bullpen product modules. |
| `static/` | `static/` | Excavate CDN Vue shell, login page, xterm.js terminal shape, Socket.IO lifecycle, toasts/style variables. |
| `tests/` | `tests/` | Keep auth, CORS, terminal, deploy, and validation coverage as starting points; archive Bullpen product tests as cuts land. |
| `profiles/` | `profiles/` | Imported for completeness only; expected early delete because Toady has no worker/profile product. |
| `requirements.txt` | `requirements.txt` | Keep initially; trim after product modules are deleted. |
| `README.md`, `LICENSE.md` | `README.md`, `LICENSE.md` | Attribution/reference; rewrite README for Toady before release. |

## Workarounds to Preserve

From `deploy-sandbox.py`:

- Lazy Microsandbox SDK adapter with sync/async normalization.
- Snapshot path extraction and validation-sandbox boot after base preparation.
- Apple Silicon macOS / Linux KVM supported-host gate.
- Host `RLIMIT_NOFILE` lift before sandbox creation.
- Guest `/etc/security/limits.d/*` nofile setup and verification.
- `Network.allow_all()` plus explicit `network.max_connections = 8192`.
- Minimal create-time environment (`HOME`, `USER`, `LOGNAME`) and full runtime
  environment exported inside sandbox commands.
- Runtime user/group creation with host UID/GID awareness and collision
  handling.
- CA environment exports for Python, Node, and Bun.
- Scoped Claude IPv6 mitigation for Microsandbox TLS EOF behavior.
- Codex file-backed auth config, stale tmp cleanup, and native package
  integrity validation.
- Labeled sandbox command wrappers with stdout/stderr normalization and secret
  redaction.
- Host port conflict detection with `lsof` owner diagnostics.
- Detached process launch with post-detach health checks.
- Bullpen's Node HTTP/proxy constraints where Toady needs an in-sandbox
  exposed web process: short upstream connections, hop-by-hop header stripping,
  and explicit WebSocket upgrade handling.

From the P-1 Toady spike:

- Do not use raw generic TCP as the host-to-sandbox PTY bridge through
  Microsandbox published ports on the current target host.
- Use `toady-ptyd` HTTP RPC/long-poll through a Microsandbox-published
  localhost port.

## Early Delete Candidates

- Tickets, tasks, workers, worker profiles, service workers, model catalog,
  MCP, commits, stats, teams, transfers, worktrees, scheduler, Bullpen manager
  orchestration, remote deployment scripts, Docker/Sprite/Droplet production
  paths, and product-specific docs/UI strings.

## Next Excavation Pass

1. Decide whether copied Bullpen reference modules/tests/static assets should
   be deleted before `0.1.0` or kept as private excavation reference.
2. If deleting now, remove the legacy product modules in dependency-aware
   slices and archive or delete the corresponding Bullpen tests.
3. If keeping now, keep the Toady-mode quarantine tests strict: no legacy REST
   routes, no legacy product static assets, and no eager imports of legacy
   product modules on the Toady startup path.

## Checkpoint: Toady Entrypoint Seed

Completed in the first Build Go pass:

- Replaced the copied `toady.py` CLI with a Toady-only server/auth entrypoint.
- Removed visible Bullpen product subcommands from the Toady CLI (`mcp`,
  `mcp-token`, `ticket`, `model-catalog`).
- Set the default Toady web port to `5858`.
- Added the v1 CLI flag surface from the spec and wired
  `--prepare-base` / `--rebuild-base` to the extracted base-prep path.
- Switched the copied global state default from `~/.bullpen` to `~/.toady`.
- Switched auth/env naming to `TOADY_*`, preserving `BULLPEN_*` reads only as
  migration/debug fallbacks inside copied modules.

Current residue:

- The repository still contains copied Bullpen product modules, static files,
  and tests as excavation reference.
- Toady mode no longer registers legacy product REST routes, serves legacy
  product static assets, constructs the legacy workspace manager, or eagerly
  imports the legacy product event/MCP/service-worker/terminal modules.
- `bullpen.py` remains as an imported reference file and should not be used as
  the Toady entrypoint.

## Checkpoint: Microsandbox Runtime Extraction

Started in the first Build Go pass:

- Added `server/microsandbox_runtime.py` for the Bullpen-derived SDK adapter,
  host support gate, host FD lift, network `max_connections`, snapshot lookup,
  sandbox create/stop/remove/status, and `lsof`-backed port diagnostics.
- Added `server/sandbox_bootstrap.py` for the Bullpen-derived in-guest setup:
  `agent` user/group creation, `/workspace`, `/home/agent`, `/var/lib/toady`,
  guest FD limits, CA env exports, Claude IPv6 mitigation, Codex file-auth
  setup, sandbox command wrappers, secret redaction, PTY controller launch, and
  post-detach status verification.
- Added `server/base.py` for the Bullpen-style prepare-sandbox -> snapshot ->
  validation flow, including Codex native package integrity checks.
- Wired `python3 toady.py --prepare-base` / `--rebuild-base` to the extracted
  base-prep code path.

Verification so far:

- Extracted modules compile.
- Basic runtime-env import probe confirms `/home/agent` and controller env.
- The prepared base `toady-microsandbox-local` exists locally and the latest
  recorded size is in `docs/release-smokes.md`.
- The Toady Flask/Socket.IO app starts and `/health` returns `200` after the
  extraction.

## Checkpoint: Sandbox Control Plane Direction

Decision after implementation review: sandbox lifecycle should be Socket.IO
first, not REST first.

Rationale:

- Bullpen already uses Socket.IO as its live command/status fabric.
- Sandbox create/start/stop/destroy are long-running, statusful operations
  where every browser tab should see progress and errors without polling.
- Terminal lifecycle and base preparation are already naturally socket-shaped.
- REST remains for health, login/logout, simple read-only queries such as
  picker/base status, and future download/upload flows.

Implemented baseline:

- Added manifest-backed `SandboxService` plus Socket.IO commands:
  `sandbox:list`, `sandbox:create`, `sandbox:get`, `sandbox:start`,
  `sandbox:stop`, `sandbox:destroy`, and `sandbox:logs`.
- Added broadcasts: `sandboxes:updated`, `sandbox:status`, `sandbox:error`,
  and `sandbox:destroyed`.
- Removed the new sandbox REST routes from the Build Go pass before treating
  them as product API.
- Added Socket.IO commands for workspace browsing, base status/preparation/logs,
  dev-port publish/unpublish/reassign, and terminal open/list/join/status/input/
  resize/close.
- Create now implies start, and successful start opens the first terminal in
  the UI.
- Added per-sandbox lifecycle logs under `~/.toady/logs/sandbox-<slug>.log`,
  exposed through the UI Logs action.
- Terminal replay is bounded by both byte and line limits and returns a
  truncation marker on rejoin when output was dropped.
