# Codex Handoff 1

Workspace: `/Users/bill/aistuff/hoosegow`

## Current State

Current git state at handoff:

```text
 M README.md
 M server/app.py
 M static/app.js
 M static/style.css
 M tests/test_hoosegow_product_surface.py
 M tests/test_hoosegow_terminal_events.py
?? .DS_Store
?? server/local_pty.py
?? tests/test_hoosegow_terminal_ui_contract.py
?? tests/test_local_pty.py
```

Temporary dev server on port `6062` was stopped before this handoff.

## User Goal

Hoosegow should become a simple humane terminal manager:

- Left pane manages terminal groups, not individual tabs.
- There is a `Local` group plus one group per sandbox.
- Tabs live inside the selected group in the main terminal area.
- Hoosegow supports local host shells and sandbox shells.
- No process manager creep, no AI/agent layer, no hidden terminal weirdness.
- Local terminal startup must not show a spurious `%`, including after switching away and back.

## Implemented So Far

Added local PTY support in [`server/local_pty.py`](../server/local_pty.py). It opens host shells via PTY and exposes `open/write/resize/status/close/poll/close_all`.

Integrated local terminals into [`server/app.py`](../server/app.py):

- `terminal:local:open`
- `terminal:list`
- terminal records now have `kind`, `label`, and `number`
- local terminals have their own numbering
- sandbox terminals still use existing socket events
- partial/latest change: imported `sanitize_zsh_startup_prompt_spacing` and applies it in `_hoosegow_terminal_replay()` for local replay. This last replay-sanitizing change was not tested after the stop.

Changed UI in [`static/app.js`](../static/app.js) and [`static/style.css`](../static/style.css):

- Sidebar header is now `Terminal Groups`.
- `Local` section has its own header plus.
- `Sandboxes` section has its own header plus.
- Local row shows `No open shells` / `1 open shell`.
- Sandbox row shows workspace basename plus open-shell count.
- Terminal tabs are in the main pane only.
- Removed `LOCAL`/sandbox pill from terminal header.
- Tab labels restored to `Term 1`, `Term 2` rather than `shell`.
- Terminal open now estimates visible terminal dimensions instead of hardcoded `100x30`.

Docs were updated in [`README.md`](../README.md).

## Tests Added Or Changed

New UI contract tests: [`tests/test_hoosegow_terminal_ui_contract.py`](../tests/test_hoosegow_terminal_ui_contract.py)

Covers:

- sidebar lists groups, not tabs
- tabs live inside selected group
- labels use `Term n`
- no `LOCAL`/sandbox authority pill
- no auto-replacement behavior

New local PTY tests: [`tests/test_local_pty.py`](../tests/test_local_pty.py)

Covers:

- current zsh startup `% + spaces + carriage returns` filter
- split PTY chunk case
- does not strip after user input
- does not strip non-zsh shells

Expanded terminal event tests: [`tests/test_hoosegow_terminal_events.py`](../tests/test_hoosegow_terminal_events.py)

Covers:

- local terminal open/list
- local numbering non-renumbering
- local limit/close behavior
- separate local/sandbox numbering

Product surface assertions were updated in [`tests/test_hoosegow_product_surface.py`](../tests/test_hoosegow_product_surface.py).

## Last Known Test Status

Before the final partial replay-sanitization change in [`server/app.py`](../server/app.py), full suite was:

```text
197 passed, 2 skipped
```

After that, tests were not rerun because the user stopped the work. Next session should rerun at least:

```bash
pytest tests/test_local_pty.py tests/test_hoosegow_terminal_events.py tests/test_hoosegow_terminal_ui_contract.py
pytest
```

## Current Bug / Unresolved

User reports: "Create Local Terminal still has a spurious `%` after switching away from a local tab and back."

Fresh local open was browser-smoked as clean, and one switch-away/back attempt with a fresh terminal also appeared clean. The user still sees the bug, so that smoke is insufficient.

Most likely areas:

- server replay buffer contains old raw zsh startup sequence
- client `record.transcript` contains old raw startup sequence
- xterm replay path re-renders the artifact even when live stream looked clean
- existing running terminals from before the filter may still poison replay
- current tests do not simulate switch away/back through the actual browser lifecycle

## What Not To Do

Do not keep sprinkling sanitizers blindly.

The next session should first create a regression test that fails for the actual user-visible transition:

1. Open Hoosegow in browser.
2. Create local shell.
3. Switch to sandbox group.
4. Switch back to local group.
5. Assert terminal visible text/rows do not include an isolated first-line `%`.

If browser tooling is too awkward, create a narrower replay test that constructs a local terminal record with a transcript containing:

```text
%        ...spaces... \r \r\r
bill@Blackbird hoosegow %
```

Then verify the replay path shown on focus cannot render the isolated `%`.

## Important Partial Change

[`server/app.py`](../server/app.py) currently imports and uses `sanitize_zsh_startup_prompt_spacing()` in `_hoosegow_terminal_replay()` for local terminals. This was added right before the stop and is unverified. The failed client-side sanitizer patch did not land.
