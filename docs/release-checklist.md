# Toady 0.1.0 Release Checklist

## Verified

- CLI version: `python3 toady.py --version` reports `toady 0.1.0`.
- Host Microsandbox dependency is pinned in `requirements.txt` as
  `microsandbox==0.5.2`; this includes the published-port TCP stall fix needed
  by Toady.
- Focused Toady/auth suite passes: latest run `99 passed in 6.83s`.
- Frontend syntax check passes: `node --check static/app.js`.
- Local health smoke passes: `http://127.0.0.1:5855/health` returns
  `{"ok":true}`.
- Opt-in real Microsandbox pytest passes on the target dev machine:
  `2 passed, 1090 deselected`.
- Browser smoke passes against `http://127.0.0.1:5855/`, including the
  minimized hamburger runtime menu, compact header theme toggle, Sandboxes count,
  hydrated icons in all menus, click-away menu dismissal, per-sandbox action
  menu, create/details/ports modals, compact sandbox list, automatic terminal
  focus for running sandboxes, terminal-focused workspace without the old
  terminal title header, connection dot, and xterm assets.
- Toady mode does not register legacy Bullpen product REST routes.
- Toady mode does not serve legacy Bullpen product static assets.
- Toady mode does not eagerly import legacy Bullpen product modules.
- Prepared base `toady-microsandbox-local` exists locally.

## Ready For Tag

- The first cleanup pass has removed copied Bullpen profile seeds, legacy
  component UI assets, the Bullpen manager surface, non-Toady remote deployment
  scaffolding, and their legacy-only tests.
- The second cleanup pass has removed legacy Bullpen REST routes, project
  Socket.IO startup, MCP-token socket auth, legacy terminal-manager wiring, and
  Bullpen app-state helpers from `server/app.py`.
- The third cleanup pass has removed detached Bullpen server modules and
  legacy-only tests.
- The fourth cleanup pass has removed the legacy `bullpen.py` entry point,
  `deploy-sandbox.py`, the Bullpen Microsandbox front proxy, the temporary
  workspace-manager shim, the deploy-sandbox tests, and unused
  `eventlet`/`websocket-client` dependencies.
- Remaining copied Bullpen references are historical docs and attribution, not
  runtime or test code.
- Base-prep duration was not remeasured because a forced rebuild is optional
  and not a release blocker; current base size is recorded.
- Final commands to rerun immediately before tagging, if anything changes:

```bash
pytest -q tests/test_toady_sandbox_service.py \
  tests/test_toady_sandbox_events.py \
  tests/test_toady_terminal_events.py \
  tests/test_toady_product_surface.py \
  tests/test_toady_cli.py \
  tests/test_auth.py tests/test_auth_e2e.py
node --check static/app.js
TOADY_RUN_REAL_MICROSANDBOX=1 pytest -q -m real_microsandbox
```
