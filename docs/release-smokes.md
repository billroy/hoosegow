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
- Verified app title/body loads as Toady.
- Verified sandbox list or empty sandbox state is visible.
- Verified selected sandbox `Logs` action opens the `Sandbox Logs` modal.
- Verified xterm loads from `/vendor/xterm/xterm.js`.
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
