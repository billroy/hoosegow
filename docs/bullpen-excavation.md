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

Decision for private `0.1.0`: copied Bullpen reference modules/tests/static
assets were kept only until their reusable infrastructure was extracted and
covered by Toady tests. The cleanup passes have now removed the quarantined
frontend/profile/manager artifacts, legacy app surface, detached server
modules, and old deploy-era entry points.

Cleanup sequence:

1. Remove the legacy product modules in dependency-aware slices.
2. Archive or delete the corresponding Bullpen tests.
3. Keep the Toady-mode quarantine tests strict until deletion is complete: no
   legacy REST routes, no legacy product static assets, and no eager imports of
   legacy product modules on the Toady startup path.

## Checkpoint: Phase 1 Cleanup

Completed after the unused-code review:

- Removed the copied Bullpen profile seeds in `profiles/`.
- Removed the legacy componentized Bullpen UI under `static/components/`.
- Removed legacy static helper files that were only loaded by the Bullpen UI:
  `audio.js`, `commands.js`, `event-sounds.js`, `gridGeometry.js`,
  `shell_worker_examples.json`, and `utils.js`.
- Removed the legacy Bullpen manager UI under `static/manager/` and the
  corresponding `server/manager.py` implementation.
- Removed non-Toady remote deployment scaffolding in `deploy/digitalocean/` and
  `deploy/docker/`.
- Removed legacy frontend/manager/docker tests that only defended the deleted
  artifacts.
- Kept `deploy-sandbox.py`, `deploy/microsandbox/`, and `bullpen.py` for a
  later parity-checked removal because the deploy script still validates
  Bullpen source layout and holds historical Microsandbox workaround context.

## Checkpoint: Phase 2 App Surface Cut

Completed after the first artifact-removal pass:

- Removed legacy Bullpen workspace/project branching from `server/app.py`; the
  app factory now always uses the Toady sandbox-state holder.
- Removed legacy Bullpen REST route implementations from the Toady app:
  commits, file browser/editor, worker transfer, export/import, and service
  preview.
- Removed Bullpen Socket.IO startup wiring from the app factory:
  project `state:init`, MCP-token socket auth, legacy terminal manager,
  `server.events` registration, and per-workspace schedulers.
- Removed app-level helper functions that only supported the deleted Bullpen
  routes: deploy-label sync, import/export zip helpers, file-tree building,
  startup reconciliation, and full Bullpen state loading.
- Kept a small explicit 404 guard for removed Bullpen `/api/*` product routes
  so old clients fail clearly instead of falling through to Flask static-route
  method handling.
- Updated auth tests to target the current Toady static/socket surface rather
  than removed `/api/files` and MCP-token behavior.
- Removed app-route-specific tests for deleted commits/files/deploy-label
  helpers and pruned old Socket.IO/service-preview/terminal-manager cases that
  depended on deleted app registrations.

## Checkpoint: Toady Entrypoint Seed

Completed in the first Build Go pass:

- Replaced the copied `toady.py` CLI with a Toady-only server/auth entrypoint.
- Removed visible Bullpen product subcommands from the Toady CLI (`mcp`,
  `mcp-token`, `ticket`, `model-catalog`).
- Set the default Toady web port to `6060`.
- Added the v1 CLI flag surface from the spec and wired
  `--prepare-base` / `--rebuild-base` to the extracted base-prep path.
- Switched the copied global state default from `~/.bullpen` to `~/.toady`.
- Switched auth/env naming to `TOADY_*`, preserving `BULLPEN_*` reads only as
  migration/debug fallbacks inside copied modules.

Current residue after cleanup:

- The repository no longer contains copied Bullpen product modules, product
  static assets, legacy deploy entry points, or their legacy-only tests.
- Toady mode no longer registers legacy product REST routes, serves legacy
  product static assets, constructs the legacy workspace manager, or eagerly
  imports the legacy product event/MCP/service-worker/terminal modules.
- Remaining Bullpen references are provenance notes, attribution, and comments
  identifying extracted implementation workarounds.

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

## Checkpoint: Phase 3 Legacy Server Module Removal

Completed after the app-surface cleanup:

- Removed the detached Bullpen agent, event, MCP, model-catalog, task, team,
  terminal, transfer, usage, worker, worktree, scheduler, profile, validation,
  and service-worker server modules.
- Removed their legacy-only pytest coverage.
- Simplified `server/workspace_manager.py` to a temporary compatibility shim
  that only exposes the old global auth directory constants for the remaining
  legacy `bullpen.py` and `deploy-sandbox.py` entry points.
- Updated auth tests to seed credentials through `TOADY_HOME` instead of
  patching the removed workspace manager.

## Checkpoint: Phase 4 Deploy-Era Removal

Completed after the detached module removal:

- Removed the legacy `bullpen.py` entry point.
- Removed `deploy-sandbox.py` and the obsolete `deploy/microsandbox/`
  Bullpen front proxy.
- Removed the temporary `server/workspace_manager.py` shim because its only
  remaining callers were deleted.
- Removed `tests/test_deploy_sandbox.py`; deploy-sandbox parity is now covered
  by the extracted Toady runtime/base tests and real Microsandbox smoke scripts.
- Trimmed `requirements.txt` to drop `eventlet` and `websocket-client`; Toady
  uses Flask-SocketIO's threading mode with `simple-websocket`.

Remaining cleanup candidates:

- The `scripts/` directory is retained because it contains live PTY and real
  Microsandbox smoke tools used by docs and opt-in tests.
- Historical docs may still mention Bullpen and `deploy-sandbox.py` as
  provenance, but no removed deploy-era file remains in the runtime tree.
