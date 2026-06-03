# Complete Codex Fuckup

This document records a multi-agent failure while trying to fix Hoosegow local
terminal startup behavior. It is intentionally blunt because the user spent far
too much time dealing with avoidable mistakes.

## What The User Wanted

Hoosegow should become a simple, humane terminal manager:

- Left pane manages terminal groups, not individual tabs.
- There is a `Local` group plus one group per sandbox.
- Tabs live inside the selected group in the main terminal area.
- Hoosegow supports local host shells and sandbox shells.
- No process-manager creep, no hidden terminal weirdness.
- Local terminal startup must not show a spurious `%`, including after switching
  away from a local tab and back.

## What Went Wrong

Several Codex agents, including the most recent one, focused on the visible `%`
artifact and failed to read the actual error message that appeared immediately
before it.

The captured raw local zsh startup output was:

```python
b'zsh: locking failed for /Users/bill/.zsh_history: operation not permitted: reading anyway\r\n%                                                                                                                      \r \r\rbill@Blackbird hoosegow % \x1b[?2004h'
```

The important part is not the `%`. The important part is:

```text
zsh: locking failed for /Users/bill/.zsh_history: operation not permitted: reading anyway
```

The actual diagnosis is that zsh cannot lock the user's history file when
Hoosegow launches a local login shell under the current execution environment.
After printing that warning, zsh emits prompt repaint bytes:

```python
b'%                                                                                                                      \r \r\r'
```

That repaint artifact can become visible or stale in xterm, especially when
Hoosegow stores/replays terminal output or recreates the terminal renderer while
switching local tabs.

The agents treated the repaint artifact as the root cause and repeatedly added
filters/sanitizers instead of addressing why zsh was emitting the warning in the
first place. That wasted the user's time and added unnecessary tool marks.

## Bad Changes / Risky Direction

The current working tree may contain useful work, but it also contains changes
that were made while chasing the wrong problem:

- `server/local_pty.py` contains startup prompt spacing filtering.
- `server/app.py` contains local replay/output sanitization.
- `static/app.js` contains local transcript/output normalization.
- Tests were added around these sanitizers.

Some of this may still be useful as defensive handling, but it must no longer be
treated as the primary fix. The primary fix should prevent zsh from producing
the history-lock warning in the first place.

## Root Cause To Investigate

The immediate root cause is:

```text
zsh cannot lock /Users/bill/.zsh_history: operation not permitted
```

Likely contributing factors:

- Hoosegow local PTYs launch the user's login shell, currently likely `zsh -l`.
- The shell inherits an environment and home directory where zsh tries to use
  `/Users/bill/.zsh_history`.
- The process execution environment can read the file but cannot acquire the
  required lock, or the lock operation is blocked by sandbox/permission
  constraints.
- zsh warns, then repaints its prompt; the repaint sequence is what produces the
  misleading `%`.

The next agent must confirm this by running local PTY startup experiments and
checking whether changing zsh history behavior removes both the warning and the
visual artifact.

## Remediation Plan For The Next Agent

Start by apologizing plainly to the user. Do not say "we" caused the issue. The
agents caused it. The user correctly identified that the error message had been
ignored.

Do not start by adding more regexes.

1. Preserve the current worktree before changing anything.
   - Inspect `git status --short --ignored`.
   - Do not delete untracked files.
   - `.DS_Store` should be ignored, not manually removed.

2. Reproduce the raw zsh startup output outside Hoosegow.
   - Launch the same shell under a PTY.
   - Capture raw bytes.
   - Confirm the warning:

     ```text
     zsh: locking failed for /Users/bill/.zsh_history: operation not permitted: reading anyway
     ```

3. Identify a root-cause fix that prevents zsh from touching or locking the real
   host `.zsh_history` for Hoosegow local PTYs.
   Candidate approaches to evaluate:

   - Launch local zsh with a Hoosegow-specific writable history file, such as
     under Hoosegow state or a temp directory.
   - Set zsh history-related environment/options so local Hoosegow PTYs do not
     use `/Users/bill/.zsh_history`.
   - Avoid login-shell startup for local PTYs if that is acceptable to the
     product.
   - Use a shell startup wrapper that configures `HISTFILE` before zsh reads or
     writes history.

4. Prove the chosen root-cause fix with a raw-byte test.
   - The raw startup capture should no longer contain the `.zsh_history` locking
     warning.
   - It should also no longer contain the transient `%` repaint sequence that was
     being misdiagnosed.

5. Then prove the user-visible behavior.
   - Start Hoosegow on an isolated port/state directory.
   - Open the browser.
   - Create Local Term 1.
   - Create Local Term 2.
   - Click back from Term 2 to Term 1.
   - Verify visually and programmatically that there is no stale `%` above the
     first line.

6. Only after the root-cause fix is proven, revisit the sanitizer changes.
   - Decide whether to keep them as defensive handling.
   - Remove any unnecessary scrubbers if they are just noise.
   - Keep tests that prove the actual root-cause behavior, not just regex
     behavior.

7. Run verification.
   - Focused tests for local PTY startup.
   - Browser smoke for the exact Term 2 -> Term 1 flow.
   - Full `pytest`.

## Communication Guidance

The next agent should be appropriately apologetic. The user has spent over an
hour and many iterations on a bug that should have been diagnosed by reading the
first line of captured output.

Do not minimize the failure. Do not blame the user, zsh, xterm, or "we." The
agents missed the obvious diagnostic line and chased symptoms.

Suggested opening:

```text
You were right. The previous agents missed the actual error message and chased
the visible `%` instead of the `.zsh_history` locking failure. I am going to fix
the root cause first: why local zsh cannot lock history under Hoosegow.
```

## Feedback To Codex / OpenAI

This was a very poor agent performance pattern:

- Agents repeatedly chased a visible symptom.
- Agents ignored a literal error message in captured raw output.
- Agents made speculative patches without a grounded diagnosis.
- Agents relied on tests that did not reproduce the user's exact observed
  behavior.
- The user had to identify the root cause by asking whether the agent recognized
  the captured text.

This should be treated as a serious failure mode: when raw diagnostic output is
available, the agent must parse and explain every human-readable error before
writing code.
