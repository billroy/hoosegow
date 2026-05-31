# Hoosegow Security Notes

Hoosegow is a local-first tool for running coding-agent terminals inside
Microsandbox microVMs. Its security model is isolation, not multi-tenant
authorization.

## Scope

Hoosegow protects the host by mounting only a chosen workspace root into each
sandbox at `/workspace`. Projects inside that root are intentionally available
to the sandbox. Host paths outside that root, such as `~/.ssh`, `~/.aws`, and
`~/.hoosegow`, are not mounted.

The sandbox boundary assumes the agent is not exploiting a kernel or
Microsandbox escape. Treat a sandboxed agent as capable of reading and writing
anything inside the selected workspace root.

## Authentication

If no credentials are configured, Hoosegow has no login screen for loopback use.
This is intended for local development.

Configure credentials before binding to a non-loopback host:

```bash
python3 hoosegow.py --set-password admin
```

Hoosegow refuses non-loopback binds unless authentication is enabled. Credentials
are stored as password hashes in `~/.hoosegow/.env`, and sessions use HTTP-only
SameSite cookies backed by a persistent secret in the same env file.

For network exposure, put TLS in front of Hoosegow and set:

```bash
HOOSEGOW_PRODUCTION=1
```

## Workspace Roots

Workspace roots must live under configured browse roots. Hoosegow resolves paths
with `realpath` and rejects symlink escapes outside the allowed roots.

Blocked paths include:

- `/`
- `/bin`
- `/boot`
- `/etc`
- `/sbin`
- `/usr`
- `/var`
- `~/.ssh`
- `~/.aws`
- `~/.gnupg`
- `~/.config`
- `~/.hoosegow`

Selecting `$HOME` itself requires explicit confirmation at the API layer. The
UI should prefer a narrower work root.

## Terminals

Hoosegow does not expose a host shell. Browser terminals connect to `hoosegow-ptyd`
running inside the sandbox. Controller traffic is token-protected with a
per-sandbox secret stored in the sandbox manifest and injected during sandbox
bootstrap.

Terminal close confirmation uses `hoosegow-ptyd` foreground process-group status
where the guest can report it. Treat the process name as best-effort diagnostic
metadata, not a security boundary.

## Published Ports

Published dev-server ports bind to host loopback and are explicitly requested
per sandbox. If a requested host port is already occupied, Hoosegow marks the
mapping as a conflict and reports `lsof` owner details where available.

## State and Destruction

Hoosegow state lives under `~/.hoosegow` by default. Destroying a sandbox stops and
removes the Microsandbox instance and can delete that sandbox's persistent home.
It never deletes the host workspace root.

## Reporting Issues

This project is still in local/private first-draft form. Before public release,
revisit licensing, attribution, dependency review, and a documented disclosure
process.
