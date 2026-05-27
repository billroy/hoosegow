# PTY Controller Spike

Date: 2026-05-27

## Decision

Toady v1 should use an in-sandbox `toady-ptyd` process with a token-protected
HTTP bridge to the host Toady server.

The production bridge is HTTP RPC plus long-poll event reads over a
Microsandbox-published localhost port:

- `GET /health`
- `POST /rpc`
- `GET /events?id=<pty-id>&since=<seq>&timeout=<seconds>`

PTY payloads are base64 encoded. Browser clients do not connect to this
controller port directly; they connect only to the host Toady Flask/Socket.IO
server.

## Why HTTP

Bullpen's Microsandbox deployment path already proves HTTP services through
published localhost ports. The spike reproduced that with
`scripts/microsandbox_port_smoke.py`.

The same host/guest port mapping did not behave as a generic raw TCP stream on
the target host. A guest raw echo server listened successfully, but the host
client received EOF and the guest process did not observe a client connection.
That failure is captured by `scripts/microsandbox_raw_tcp_smoke.py`. This is
exactly the class of dirty Microsandbox workaround Toady should inherit rather
than rediscover during terminal integration.

## Implemented Proof

- `guest/toady-ptyd.py`
  - opens PTYs with `os.openpty()` and `subprocess.Popen(..., start_new_session=True)`;
  - supports raw newline JSON for local diagnostics;
  - supports HTTP RPC and long-poll events for the Microsandbox bridge;
  - supports `open`, `join`, `write`, `resize`, `status`, `close`, and
    `shutdown`;
  - records event sequence numbers and bounded in-memory event history;
  - reports PTY output and exit status.
- `scripts/pty_controller_smoke.py`
  - local raw newline JSON proof.
- `scripts/pty_controller_http_smoke.py`
  - local HTTP controller proof.
- `scripts/microsandbox_port_smoke.py`
  - proves published HTTP ports work in a throwaway Microsandbox.
- `scripts/microsandbox_raw_tcp_smoke.py`
  - documents current raw TCP published-port failure.
- `scripts/pty_controller_microsandbox_smoke.py`
  - runs `toady-ptyd` inside a throwaway Microsandbox through the HTTP bridge.

## Verification

Passing checks:

```bash
python3 -m py_compile guest/toady-ptyd.py scripts/*.py
python3 scripts/pty_controller_smoke.py
python3 scripts/pty_controller_http_smoke.py
python3 scripts/microsandbox_port_smoke.py
python3 scripts/pty_controller_microsandbox_smoke.py --verbose
```

The Microsandbox scripts default to the Toady prepared base
`toady-microsandbox-local`. Override with `--snapshot <name>` or
`TOADY_MICROSANDBOX_BASE=<name>` when testing an alternate base.

Observed Microsandbox PTY output included:

```text
printf 'TOADY_HTTP_PTYD_SMOKE:%s\n' "$PWD"; exit 11
Microsandbox PTY controller HTTP smoke passed
```

Expected failing diagnostic:

```bash
python3 scripts/microsandbox_raw_tcp_smoke.py
```

Current behavior: the host receives `b''` from the raw TCP connection while the
guest log only shows the listener startup. Keep this test as a regression probe
in case future Microsandbox versions change published-port behavior.

## Follow-Up Work

- Move the host-side HTTP client into `server/pty_driver.py`.
- Allocate and persist a dedicated controller host/guest port and token per
  sandbox.
- Start `toady-ptyd --http 0.0.0.0:<controller-port>` during sandbox bootstrap
  after the Bullpen-derived FD, network, CA, IPv6, user, and Codex setup.
- Add foreground-process detection or explicitly degrade close confirmation to
  best-effort for v1.
