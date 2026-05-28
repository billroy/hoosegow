# Toady 0.1.0 Release Checklist

## Verified

- CLI version: `python3 toady.py --version` reports `toady 0.1.0`.
- Focused Toady/auth suite passes: latest run `98 passed`.
- Opt-in real Microsandbox pytest passes on the target dev machine:
  `2 passed, 1090 deselected`.
- Browser smoke passes against `http://127.0.0.1:5855/`.
- Toady mode does not register legacy Bullpen product REST routes.
- Toady mode does not serve legacy Bullpen product static assets.
- Toady mode does not eagerly import legacy Bullpen product modules.
- Prepared base `toady-microsandbox-local` exists locally.

## Remaining Before Tag

- Keep copied Bullpen reference modules/tests/static assets in the private
  `0.1.0` tree as quarantined reference code, not product surface.
- Optionally remeasure base-prep duration with a forced rebuild; current base
  size is recorded, but prep duration is not.
- Re-run final commands immediately before tagging:

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
