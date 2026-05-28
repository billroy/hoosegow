# Release Smoke Notes

## 2026-05-28

Host: local Apple Silicon development machine.

Prepared base:

- Name: `toady-microsandbox-local`
- Path: `/Users/bill/.microsandbox/snapshots/toady-microsandbox-local`
- Size: `1.1G` as last measured on 2026-05-27
- Prep duration: not remeasured in this pass.

Passing real Microsandbox smokes:

```bash
TOADY_RUN_REAL_MICROSANDBOX=1 pytest -q -m real_microsandbox
```

Observed output:

```text
2 passed, 1090 deselected in 3.02s
```

Notes:

- The first attempt inside Codex's default sandbox failed before Microsandbox
  because binding an ephemeral localhost port raised `PermissionError: [Errno
  1] Operation not permitted`.
- The same command passed when rerun with the approved external execution path,
  which is required for localhost port-binding real smokes.

Browser smoke:

- URL: `http://127.0.0.1:5855/`
- Final refresh:
  - `pytest -q tests/test_toady_sandbox_service.py tests/test_toady_sandbox_events.py tests/test_toady_terminal_events.py tests/test_toady_product_surface.py tests/test_toady_cli.py tests/test_auth.py tests/test_auth_e2e.py`
    returned `99 passed in 6.83s`.
  - `node --check static/app.js` passed.
  - `python3 scripts/pty_controller_http_smoke.py` passed outside the command
    sandbox, where temporary localhost binding is allowed.
  - `/health` returned `{"ok":true}` when checked outside the command sandbox,
    which is required for reaching the local Flask listener from Codex.
- Verified app title/body loads as Toady.
- Verified hamburger menu exposes runtime readiness, rebuild/retry setup, and
  runtime logs.
- Verified theme toggle is in the header, not the hamburger menu.
- Verified header height is 44px, the hamburger button is visually bare, and
  no selected-sandbox subtitle appears under `Toady`.
- Verified left-pane heading renders the count inline as `Sandboxes (n)`.
- Verified Sandboxes menu exposes create sandbox, sandbox details, published
  ports, and sandbox logs.
- Verified per-sandbox row menu exposes start, new terminal, stop, details,
  ports, logs, and destroy with Lucide menu icons.
- Verified every command in the hamburger, Sandboxes, and row action menus has
  a Lucide menu icon.
- Verified hamburger and Sandboxes menu toggles now hydrate Lucide icons after
  opening, matching the row action menu behavior.
- Verified the old right-pane `Terminal` / `/workspace` title header is removed.
- Verified hamburger, Sandboxes, and row action menus all dismiss when clicking
  outside them.
- Verified selecting a running sandbox with no terminal opens and focuses a
  terminal automatically.
- Verified create, details, and published-port modals open with existing
  sandbox state.
- Verified terminal-focused workspace has no top-level refresh button and no
  visible `Connected` label; Socket.IO state is represented by the status dot.
- Verified the selected-sandbox command header is gone and the terminal surface
  occupies about 93% of the right pane in the narrow in-app browser viewport.
- Verified new PTY sessions no longer inject the first-terminal auth/setup
  banner.
- Verified sandbox list or empty sandbox state is visible.
- Verified selected sandbox `Logs` menu action opens the `Sandbox Logs` modal.
- Verified xterm loads from `/vendor/xterm/xterm.js`.
- Verified xterm CSS loads from `/vendor/xterm/xterm.css`.
- Verified the old `/manager/vendor/xterm/xterm.js` script path is not used.

## 2026-05-27

Host: local Apple Silicon development machine.

Prepared base:

- Name: `toady-microsandbox-local`
- Path: `/Users/bill/.microsandbox/snapshots/toady-microsandbox-local`
- Size: `1.1G`
- Prep duration: not captured for the existing base; the UI now records
  in-session base-prep duration for future rebuilds.

Passing real Microsandbox smokes:

```bash
python3 scripts/microsandbox_port_smoke.py
python3 scripts/pty_controller_microsandbox_smoke.py --verbose
TOADY_RUN_REAL_MICROSANDBOX=1 pytest -q -m real_microsandbox
```

Observed output:

```text
Microsandbox published HTTP port smoke passed
Microsandbox PTY controller HTTP smoke passed
TOADY_HTTP_PTYD_SMOKE:/app
```

Notes:

- The real smoke scripts default to `toady-microsandbox-local`.
- Use `TOADY_MICROSANDBOX_BASE=<name>` or `--snapshot <name>` to target a
  different prepared base.
- `scripts/microsandbox_raw_tcp_smoke.py` remains an expected-failure
  diagnostic for raw TCP published-port behavior.
