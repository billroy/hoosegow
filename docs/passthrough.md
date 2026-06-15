# Sandbox Agent Passthrough CLI Specification

## 1. Purpose

External tools should be able to run agent commands inside an existing
Microsandbox sandbox and receive ordinary command-line behavior: stdin,
stdout/stderr, terminal behavior when requested, and the final exit code.

Phase 0 is intentionally **Hoosegow-independent**. It is a tiny generic wrapper
around `msb exec`, not a Hoosegow server feature and not a Bullpen built-in.

Target shape:

```bash
hoosegow-msb --sandbox fred -- claude -p 'say model slug'
```

Equivalent underlying command:

```bash
msb exec fred -u agent -w /workspace -- claude -p 'say model slug'
```

The wrapper exists so Bullpen and other integrations can depend on one stable
tool with good defaults instead of hand-encoding Microsandbox flags and
workarounds in every caller.

## 2. Goals

- Provide a reusable command-line integration point for Bullpen and other local
  automation.
- Use Microsandbox's approved `msb exec` execution path directly.
- Avoid requiring the Hoosegow web server, Socket.IO, browser auth, or Hoosegow
  manifests for the first integration.
- Support non-interactive agent commands such as `claude -p ...`, `codex ...`,
  `antigravity ...`, and `opencode ...`.
- Support interactive PTY mode when `msb exec -t` is sufficient.
- Preserve remote exit codes and produce predictable wrapper exit codes.
- Keep the implementation small enough to test thoroughly.

## 3. Non-Goals

- Enforcing access control beyond the local OS user and Microsandbox runtime.
- Hiding sandbox names from same-user local processes.
- Creating, preparing, or starting sandboxes in Phase 0.
- Replacing Hoosegow's browser terminal UI.
- Providing Hoosegow audit logs, policy, or session inventory.
- A cloud transport or multi-user remote execution API.

## 4. Trust Boundary

Phase 0 accepts the real local security boundary:

- Any process running as the same Unix user, or otherwise able to access the
  same Microsandbox runtime state, can already call `msb exec <sandbox>`.
- The wrapper does not create new authority. It packages that existing ability
  into a stable integration command.
- Hoosegow server auth is not involved.
- Different Unix users may be constrained by filesystem and runtime
  permissions, but that is outside the wrapper's scope.

This means Phase 0 is an ergonomics and reliability tool, not a mandatory
access-control layer.

## 5. User Stories

1. **Bullpen worker agent isolation.** Bullpen launches an agent inside a named
   sandbox instead of inside the Bullpen container or directly on the host.
2. **Reusable local integration.** Another tool can shell out to the same
   wrapper with a sandbox name and command argv.
3. **Synchronous non-interactive run.** A caller runs:
   `hoosegow-msb --sandbox fred -- claude -p 'say model slug'` and receives stdout,
   stderr, and the agent exit code.
4. **Interactive auth or chat flow.** A caller runs:
   `hoosegow-msb --sandbox fred --tty -- claude` and expects Microsandbox PTY
   behavior.
5. **Stable defaults.** Callers do not need to remember `-u agent`,
   `-w /workspace`, timeout syntax, or PTY detection rules.

## 6. Phase 0 CLI Surface

### 6.1 Command Form

```bash
hoosegow-msb --sandbox fred -- claude -p 'say model slug'
```

Interactive:

```bash
hoosegow-msb --sandbox fred --tty -- claude
```

Explicit guest workspace:

```bash
hoosegow-msb --sandbox fred --workspace /workspace/subproject -- codex exec 'summarize'
```

Bullpen-style invocation:

```bash
hoosegow-msb --sandbox "$HOOSEGOW_SANDBOX" -- claude -p "$PROMPT"
```

### 6.2 Flags

| Flag | Meaning |
|---|---|
| `--sandbox SLUG` | Required Microsandbox sandbox name. |
| `--workspace PATH` | Guest workspace/working directory passed to `msb exec -w`. Default `/workspace`. |
| `--user USER` | Guest user. Default `agent`. |
| `--tty` | Force `msb exec -t`. |
| `--no-tty` | Force no PTY. |
| `--timeout DURATION` | Forward to `msb exec --timeout`, for example `30s`, `5m`, `1h`. |
| `--env KEY=VALUE` | Forward environment variables with `msb exec -e`. Repeatable. |
| `--msb PATH` | Microsandbox CLI path. Default `msb` on `PATH`. |
| `--dry-run` | Print the `msb exec` argv that would run, then exit. |
| `--verbose` | Print wrapper diagnostics to stderr. |

Everything after `--` is passed as the remote command argv.

### 6.3 Defaults

- `--workspace` defaults to `/workspace`.
- `--user` defaults to `agent`.
- PTY default:
  - Use `--tty` automatically only when stdin and stdout are both TTYs.
  - Use no PTY for pipes, CI, and Bullpen-style non-interactive calls.
- No command means interactive shell if `msb exec` supports that mode:

```bash
hoosegow-msb --sandbox fred --tty
```

## 7. Exit Behavior

- Return the remote command exit code when `msb exec` can provide it.
- Return `124` when the wrapper timeout path is responsible for termination.
- Return `125` for wrapper usage/configuration errors.
- Return `126` when the remote command cannot be spawned.
- Return `127` when `msb` or the remote executable is not found.
- Preserve `msb exec`'s own exit behavior when it is already specific and
  useful.

Diagnostics should be concise and go to stderr. The wrapper should avoid
printing the full prompt or env values in error messages unless `msb` itself
does so.

## 8. Implementation Shape

Phase 0 can be implemented as a small Python script in this repo, for example:

```text
scripts/hoosegow-msb
```

or as a module-backed command:

```text
python3 -m server.msb_passthrough ...
```

The implementation should:

- Parse wrapper flags until `--`.
- Build an argv list, not a shell string.
- Run `msb exec` with `subprocess.run` or `subprocess.Popen`.
- Forward stdin/stdout/stderr naturally by inheriting the current process file
  descriptors.
- Preserve Ctrl-C behavior by letting the foreground process receive the signal
  where possible.
- Exit with the child process return code after mapping wrapper-only failures.
- Never require the Hoosegow web server to be running.

Expected generated argv:

```text
msb exec fred -u agent -w /workspace -- claude -p 'say model slug'
```

With PTY:

```text
msb exec fred -u agent -w /workspace -t -- claude
```

With env and timeout:

```text
msb exec fred -u agent -w /workspace -e FOO=bar --timeout 5m -- claude -p ...
```

## 9. Functional Requirements

- Validate that `--sandbox` is present and non-empty.
- Validate that a command is present unless `--tty` is set and shell attach is
  explicitly allowed.
- Detect missing `msb` and return `127` with install guidance.
- Preserve argv exactly after `--`, including spaces and quotes already handled
  by the caller's shell.
- Forward `--env` values without logging them.
- Support both buffered subprocess completion and live inherited stdio.
- Work from host shells and from containers that can invoke `msb` and reach the
  same Microsandbox runtime.
- Keep code independent from Hoosegow manifest state.

## 10. Container And Integration Notes

The wrapper is intentionally a command-line dependency, not a Bullpen-specific
library.

Bullpen or another integration needs:

1. The wrapper executable available in its environment.
2. `msb` available in that environment, or `--msb PATH` pointing to it.
3. Access to the same Microsandbox runtime state as the target sandbox.
4. A configured sandbox name.

If Bullpen runs inside a container while Microsandbox runs on the host, Phase 0
only works if the container can execute an `msb` client that controls the host
Microsandbox runtime. That may require mounting runtime state, using a host-side
shim, or running the wrapper on the host instead of inside the container. This
is a deployment constraint to test early.

## 11. Deferred Hoosegow-Mediated Options

Phase 0 does not decide the full Hoosegow passthrough architecture. It gives us a
simple integration first, then leaves the richer paths available if we need
policy, UI visibility, lifecycle management, or stronger session semantics.

### Deferred Option B: Hoosegow Socket.IO + `hoosegow-ptyd`

The CLI wrapper talks to the Hoosegow server over Socket.IO, and Hoosegow relays
stdio/PTY events to `hoosegow-ptyd`.

Keep this for:

- Browser-visible sessions.
- Attach/detach behavior.
- Interactive PTY flows that `msb exec -t` cannot satisfy.
- A Hoosegow-managed session inventory.

Defer because it requires more protocol machinery, backpressure handling,
binary framing, guest daemon command APIs, and reconnect semantics.

### Deferred Option F: Hoosegow Server-Side SDK Exec

The CLI wrapper talks to Hoosegow, and Hoosegow uses the Microsandbox SDK exec path
on behalf of the caller.

Keep this for:

- Hoosegow audit logs.
- Hoosegow workspace/sandbox policy.
- Server-mediated lifecycle helpers.
- A future local API for tools that cannot call `msb` directly.

Defer because Phase 0 can get the core "run an agent in a sandbox" value
without a Hoosegow server, token, HTTP API, or Socket.IO client.

## 12. Archived Considered/Discarded Options

### SSH Into Each Sandbox

Discard for now. SSH gives mature terminal semantics, but adds `sshd`, key
management, host-key churn, extra ports, and a larger attack surface.

### Direct `hoosegow-ptyd` Client

Discard as a public integration surface. It leaks low-level controller
topology and tokens, and it bypasses Microsandbox's approved `msb exec`
interface.

### Raw WebSocket API

Discard for now. It may become useful if Hoosegow later needs a language-neutral
streaming protocol, but Phase 0 should avoid creating a new realtime API.

### Unix Socket Hoosegow API

Archive as a possible future authorization/channel variant. It does not replace
the simpler `msb exec` wrapper for Phase 0.

### In-Sandbox Agent RPC Service

Discard for passthrough. It is too provider-specific and recreates orchestration
inside the sandbox.

### File Drop / Named Pipe Protocol

Discard. It has poor interactive behavior and awkward signal, timeout, and
cleanup semantics.

## 13. Implementation Plan

### Phase 0A: Wrapper Skeleton

- Add `scripts/hoosegow-msb` or equivalent module-backed executable.
- Implement argparse handling for `--sandbox`, `--workspace`, `--user`, `--tty`,
  `--no-tty`, `--timeout`, `--env`, `--msb`, `--dry-run`, and command argv
  after `--`.
- Build `msb exec` argv without shell interpolation.
- Add `--dry-run` tests for every flag combination.
- Add usage/error tests for missing sandbox, missing command, conflicting PTY
  flags, malformed env, and missing `--`.

### Phase 0B: Execution And Exit Codes

- Run `msb exec` with inherited stdio.
- Map missing `msb` to `127`.
- Preserve child return codes.
- Add tests using a fake `msb` executable on `PATH`.
- Test stdout/stderr passthrough, remote success, remote failure, missing
  executable, timeout flag forwarding, and signal/KeyboardInterrupt handling
  where practical.

### Phase 0C: Integration Docs And Smokes

- Document common examples for Claude, Codex, Antigravity, and opencode.
- Document Bullpen invocation shape as a subprocess command, not an embedded
  library API.
- Add a real smoke test gated by an environment variable, for example:

```bash
HOOSEGOW_RUN_REAL_MICROSANDBOX=1 scripts/hoosegow-msb --sandbox demo -- echo ok
```

- Add an interactive manual smoke:

```bash
scripts/hoosegow-msb --sandbox demo --tty -- bash
```

### Phase 1: Optional Wrapper Enhancements

- Add config defaults from env:
  - `HOOSEGOW_MSB_SANDBOX`
  - `HOOSEGOW_MSB_USER`
  - `HOOSEGOW_MSB_WORKSPACE`
  - `HOOSEGOW_MSB_BIN`
- Add `--print-env-example` for integrations.
- Add clearer detection for stopped/missing sandboxes by parsing `msb` errors.
- Add an optional `--json-status` mode for machine-readable wrapper failures.

### Phase 2: Reassess Hoosegow-Mediated Passthrough

Only after Phase 0 is used by Bullpen or another integration:

- Measure whether direct `msb exec` gives adequate streaming, PTY, timeout, and
  exit-code behavior.
- If policy/audit/lifecycle/UI visibility becomes important, revive Deferred
  Option F.
- If richer interactive attach/detach behavior becomes important, revive
  Deferred Option B.

## 14. Open Issues

- **Wrapper name/location**: final command name could be `hoosegow-msb`,
  `hoosegow-exec`, or `sandbox-exec`.
- **Package/distribution**: decide whether this is shipped as a script, Python
  module entry point, or both.
- **Container viability**: test whether Bullpen's actual runtime can invoke
  `msb` against the host Microsandbox runtime.
- **PTY behavior**: verify `msb exec -t` with Claude, Antigravity, Codex, and
  opencode auth/chat flows.
- **Timeout behavior**: confirm how `msb exec --timeout` reports exits and
  whether wrapper-level timeout handling is also needed.
- **Sandbox conventions**: decide how strongly to require `agent` user,
  `/workspace`, and preinstalled CLIs.
- **Secrets**: verify that `msb` diagnostics do not echo env values or prompts
  in common failure paths.
