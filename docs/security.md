# Toady Security Notes

Toady is a local-first tool for running coding-agent terminals inside
Microsandbox microVMs. Its security model is isolation, not multi-tenant
authorization.

## Scope

Toady protects the host by mounting only a chosen workspace root into each
sandbox at `/workspace`. Projects inside that root are intentionally available
to the sandbox. Host paths outside that root, such as `~/.ssh`, `~/.aws`, and
`~/.toady`, are not mounted.

The sandbox boundary assumes the agent is not exploiting a kernel or
Microsandbox escape. Treat a sandboxed agent as capable of reading and writing
anything inside the selected workspace root.

## Authentication

If no credentials are configured, Toady has no login screen for loopback use.
This is intended for local development.

Configure credentials before binding to a non-loopback host:

```bash
python3 toady.py --set-password admin
```

Toady refuses non-loopback binds unless authentication is enabled. Credentials
are stored as password hashes in `~/.toady/.env`, and sessions use HTTP-only
SameSite cookies backed by a persistent secret in the same env file.

For network exposure, put TLS in front of Toady and set:

```bash
TOADY_PRODUCTION=1
```

## Workspace Roots

Workspace roots must live under configured browse roots. Toady resolves paths
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
- `~/.toady`

Selecting `$HOME` itself requires explicit confirmation at the API layer. The
UI should prefer a narrower work root.

## Terminals

Toady does not expose a host shell. Browser terminals connect to `toady-ptyd`
running inside the sandbox. Controller traffic is token-protected with a
per-sandbox secret stored in the sandbox manifest and injected during sandbox
bootstrap.

Terminal close confirmation for foreground processes is still a v1 watchpoint.
Until foreground detection is completed, treat terminal close as best-effort.

## Published Ports

Published dev-server ports bind to host loopback and are explicitly requested
per sandbox. If a requested host port is already occupied, Toady marks the
mapping as a conflict and reports `lsof` owner details where available.

## State and Destruction

Toady state lives under `~/.toady` by default. Destroying a sandbox stops and
removes the Microsandbox instance and can delete that sandbox's persistent home.
It never deletes the host workspace root.

## Reporting Issues

This project is still in local/private first-draft form. Before public release,
revisit licensing, attribution, dependency review, and a documented disclosure
process.
