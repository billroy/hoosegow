# Apple `container` vs. Microsandbox for Hoosegow

Date: 2026-06-10

## Executive Summary

Apple's [`apple/container`](https://github.com/apple/container) is functionally
close enough to be a credible future backend for Hoosegow on Apple silicon Macs,
but it is not an obvious replacement today. It has a richer OCI/Docker-like CLI,
first-party macOS integration, managed networks, BuildKit image builds, named
volumes, Rosetta support, SSH-agent forwarding, and a documented VM-per-container
architecture. The tradeoff is platform narrowness: it requires Apple silicon and
should be treated as a macOS 26+ supported target, while Hoosegow's current
Microsandbox adapter supports Darwin arm64 and Linux hosts with KVM.

For Hoosegow specifically, Apple `container` would mainly replace the runtime
creation layer. The browser/server/terminal architecture can remain mostly
intact if Hoosegow continues to boot `hoosegow-ptyd` in the guest and reaches it
through a published localhost port. The largest migration work is not port
forwarding or host-directory mounting; those concepts map fairly well. The
harder work is rebuilding the Hoosegow base as an OCI image or Apple-compatible
container configuration, adapting lifecycle state to Apple's API/CLI, validating
permissions of virtiofs-mounted host workspaces, and preserving Hoosegow's
restart-bound port semantics.

Recommendation: do not switch wholesale yet. Add an experimental Apple backend
behind a runtime abstraction and keep Microsandbox as the default until Apple
`container` has been smoke-tested against Hoosegow's terminal, workspace,
published-port, and agent-CLI workflows.

## Current Hoosegow/Microsandbox Model

Hoosegow's runtime adapter lives primarily in:

- `server/microsandbox_runtime.py`
- `server/sandboxes.py`
- `server/sandbox_bootstrap.py`
- `server/sandbox_store.py`

The effective Microsandbox contract is:

- Create one named sandbox per Hoosegow sandbox.
- Start from a prepared snapshot, defaulting to `hoosegow-microsandbox-local`.
- Mount exactly three host paths:
  - `/app` -> project source root, read-only.
  - `/workspace` -> selected workspace root, writable.
  - `/home/agent` -> per-sandbox Hoosegow home, writable and persistent.
- Allocate a controller host port plus user-published dev-server ports from the
  Hoosegow port pool.
- Pass all host-to-guest port mappings into `Sandbox.create(..., ports=...)`.
- Run an in-guest `hoosegow-ptyd` HTTP long-poll controller on the guest
  controller port.
- Detach the sandbox and have the browser talk only to the Hoosegow server, not
  directly to the guest controller.

The adapter is intentionally conservative. It preflights host port availability,
records mappings in the sandbox manifest, marks conflicts with `lsof` owner
details when available, and delays publish/unpublish/reassign changes for
running sandboxes until restart. That restart-bound behavior fits the current
Microsandbox API use: the port set is assembled before `Sandbox.create`.

Microsandbox's Python SDK exposes the primitives Hoosegow currently uses:

- `Sandbox.create(name, snapshot=..., detached=True, cpus=..., memory=...,
  ports=..., volumes=..., network=..., env=...)`
- `Volume.bind(path, readonly=False, noexec=False)`
- `Network.allow_all()` plus `max_connections`
- `Sandbox.exec`/`shell` and lifecycle calls
- `Snapshot.create/get/open`

This is a small API surface, which is good for Hoosegow's current shape.

## Apple `container` Functional Model

Apple `container` is a Swift-based CLI and service stack for running OCI Linux
containers as lightweight VMs on macOS. It consumes and produces OCI-compatible
images, builds with a BuildKit-based builder container, and manages containers,
images, registries, networks, volumes, logs, stats, copy, exec, and lifecycle.

Architecturally, Apple differs from shared-VM tools by running a lightweight VM
per container. Its technical overview describes the benefits as:

- VM isolation for each container.
- Only the required host data is mounted into each VM.
- Boot times comparable to shared-VM container systems.
- Integration with Virtualization.framework, vmnet, XPC, launchd, Keychain, and
  unified logging.

The service layout is also more elaborate than Hoosegow's Microsandbox adapter:

- `container` CLI talks to a client library.
- `container-apiserver` is a launch agent started by `container system start`.
- XPC helpers manage images and networking.
- A `container-runtime-linux` helper manages each container.
- Networking is vmnet-backed.
- Port/socket forwarding is implemented by host-side forwarders.

Functionally, Apple is more Docker-like than Microsandbox:

- `container run/create/exec/logs/inspect/stats/cp`.
- `-p/--publish`, `--publish-socket`, `--mount`, `-v/--volume`, `--tmpfs`.
- Named and anonymous volumes.
- User-defined networks on macOS 26+.
- OCI image build/pull/push/save/load/tag/delete.
- Rosetta for amd64 userspace on arm64 hosts.
- Optional SSH-agent forwarding.

## Port Bindings

### Hoosegow on Microsandbox

Hoosegow persists port mappings as manifest data:

```json
{"guest_port": 5173, "host_port": 63101, "status": "active"}
```

On start, `SandboxService.start()` builds:

```python
ports = {controller_host_port: controller_guest_port}
for mapping in active_mappings:
    ports[int(mapping["host_port"])] = int(mapping["guest_port"])
```

Then it passes the complete mapping into:

```python
Sandbox.create(..., ports=dict(spec.ports), ...)
```

Implications:

- Hoosegow owns host-port allocation.
- Host ports are loopback-oriented in UI and docs.
- Conflicts are detected before runtime creation.
- A running sandbox cannot be dynamically mutated through the current Hoosegow
  abstraction; changes become `pending_restart` or `remove_on_restart`.
- Hoosegow has observed Microsandbox-published HTTP ports working, but raw TCP
  behavior was suspect enough to document an expected-failing raw TCP diagnostic
  in `docs/pty-controller-spike.md`.

### Apple `container`

Apple exposes published ports through `-p/--publish`:

```bash
container run -d --rm -p 127.0.0.1:8080:8000 node:latest ...
```

The documented format is:

```text
[host-ip:]host-port:container-port[/protocol]
```

It supports TCP and UDP, IPv4 and IPv6 loopback examples, and port ranges. In
source, a publish rule is represented by `PublishPort`:

- `hostAddress`
- `hostPort`
- `containerPort`
- `proto`
- `count`

The parser defaults an omitted host address to `0.0.0.0`, rejects ports `0` and
`1`, validates ranges, rejects unequal host/container range sizes, and caps a
container at 64 publish descriptors. Overlapping host ports are rejected before
creation.

The forwarding implementation is host-side SwiftNIO. `TCPForwarder` binds a
proxy listener at the host address/port and adds a `ConnectHandler` that connects
to the container-side address. `UDPForwarder` has a parallel implementation.

### Port Binding Comparison

Apple is ahead functionally:

- Explicit host IP binding.
- TCP and UDP.
- IPv6 syntax.
- Ranges.
- Socket publishing.
- Structured validation in the runtime's own configuration.

Hoosegow/Microsandbox is simpler and more controlled:

- One mapping shape: host port -> guest port.
- Hoosegow's manifest is the source of truth.
- Hoosegow can reserve, report, and reassign ports consistently across UI/API.
- Current terminal bridge is already tuned around Microsandbox's HTTP-port
  behavior.

For Hoosegow, Apple `container` should be used with explicit
`127.0.0.1:host:guest/tcp` publish specs. Do not rely on Apple's default
`0.0.0.0` host address, because Hoosegow's security docs promise loopback
published dev-server ports.

The migration risk is not basic forwarding; it is lifecycle semantics. If
Apple does not support mutating `publishedPorts` on a running container through
the public API, Hoosegow's current restart-required status model can be retained.
If Apple later supports live publish/unpublish, Hoosegow should still keep the
current explicit UX unless it is changed deliberately.

## File System Bindings

### Hoosegow on Microsandbox

Hoosegow creates three direct bind mounts:

```python
volumes = {
    "/app": Volume.bind(str(spec.source_root), readonly=True),
    "/workspace": Volume.bind(str(spec.workspace)),
    "/home/agent": Volume.bind(str(spec.sandbox_home)),
}
```

Security and product behavior depend on this:

- Only the selected workspace root is exposed at `/workspace`.
- Project code is exposed read-only at `/app`.
- Per-sandbox home persists under Hoosegow state, not inside the ephemeral
  sandbox root.
- Host secrets such as `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config`, and
  `~/.hoosegow` are not mounted unless the user selects an unsafe workspace and
  confirms it.

### Apple `container`

Apple has two related but different storage concepts:

1. Host directory sharing through `--volume` or `--mount`.
2. Managed named/anonymous volumes backed by ext4 image files.

For host directory sharing, the how-to shows:

```bash
container run --volume ${HOME}/Desktop/assets:/content/assets ...
container run --mount source=${HOME}/Desktop/assets,target=/content/assets ...
```

In source, mount parsing treats `type=bind` as `virtiofs`. It validates that a
bind source exists and, for `--mount`, is a directory. `ro`/`readonly` is mapped
to a mount option. `Filesystem.virtiofs(source:destination:options:)` resolves
the host source to an absolute path.

For managed volumes, Apple has `container volume create/list/inspect/delete`.
The default local driver creates an ext4 `volume.img`, defaulting to 512 GB
unless a size is specified. Named volumes may be auto-created by `-v
name:/path`; anonymous volumes are created by `-v /path` or
`--mount type=volume,dst=/path` and are not auto-cleaned up with `--rm`.

### File System Binding Comparison

For Hoosegow's core mounts, Apple host directory mounts map well:

- `/app`: `--mount type=bind,source=<source_root>,target=/app,readonly`
- `/workspace`: `--mount type=bind,source=<workspace>,target=/workspace`
- `/home/agent`: `--mount type=bind,source=<sandbox_home>,target=/home/agent`

However, there are important differences to test:

- Apple docs show a host-mounted file appearing as `root root` in the guest. The
  current Hoosegow bootstrap creates an `agent` user with the host UID/GID and
  expects write access to `/workspace` and `/home/agent`. We need a real
  Apple-backend smoke test for UID/GID behavior, file creation, chmod/chown, and
  editor tooling.
- Apple's managed volumes are attractive for opaque persistent state, but they
  are not a replacement for Hoosegow's host-visible `/home/agent` if users or
  support tooling need to inspect that directory from macOS.
- Hoosegow should keep its own browse-root and blocked-path validation. Apple's
  parser checks existence and syntax, not Hoosegow's product-level security
  policy.
- Apple's `--ssh` convenience should not be enabled by default. Hoosegow's
  current security posture intentionally avoids mounting host secrets.

## Architectural Fit

Apple fits Hoosegow where Hoosegow wants:

- Per-sandbox VM isolation.
- Local-first Mac developer workflow.
- OCI base images instead of bespoke snapshot bootstrapping.
- Native macOS lifecycle, networking, logging, and install story.
- A richer underlying feature set for future user requests.

Apple conflicts or adds work where Hoosegow currently benefits from
Microsandbox's small Python API:

- Hoosegow is Python; Apple exposes a CLI and Swift packages, not a Python SDK.
  The first backend would probably shell out to `container` and parse JSON.
- Hoosegow's prepared Microsandbox snapshot must become an OCI image or custom
  init/container setup.
- The detached terminal contract needs to be revalidated with Apple `exec`,
  published ports, process lifetime, and signal behavior.
- Apple needs a system service started with `container system start`.
- Apple is Mac-only; Microsandbox gives Hoosegow a plausible Linux/KVM path.

## Maturity Estimate

As of 2026-06-10, Apple `container` has just reached `1.0.0` on 2026-06-09. The
repository is highly visible and active: roughly 28k stars, hundreds of issues,
dozens of open pull requests, and over 600 commits. The 1.0.0 release notes are
substantial and include new features, fixes, output-shape cleanup, and explicit
breaking CLI/API changes.

Maturity read:

- **Project energy:** high.
- **Institutional backing:** high.
- **API stability:** improved by 1.0.0, but still young in practice. The README
  project-status text still warns about pre-1.0-style instability, suggesting
  docs/status messaging may lag the release or that the team still expects fast
  movement.
- **Operational maturity:** promising but not battle-tested in this project.
- **Platform maturity:** narrow. Official support is Apple silicon on macOS 26;
  the docs mention macOS 15 operation with networking limitations, and
  maintainers say older macOS issues generally need to reproduce on macOS 26.
- **Feature maturity for Hoosegow:** unproven until we run Hoosegow's real
  terminal, published-port, file-permission, and agent CLI smokes.

I would classify it as "credible early production for Mac-only developer use,"
not yet "boring infrastructure dependency" for Hoosegow.

## Pros of Switching

- First-party Apple stack optimized for Apple silicon.
- OCI images and registries instead of Microsandbox-specific snapshots.
- Broader user familiarity through Docker-like CLI concepts.
- Better documented port publish semantics: host IP, TCP/UDP, IPv6, ranges.
- Managed networks and volumes are already in the product surface.
- Potentially better long-term ecosystem gravity than Microsandbox.
- Rosetta support could simplify running amd64 userland images on arm64 Macs.
- Built-in `container cp`, `logs`, `stats`, structured inspect/list output, and
  registry support may reduce custom code over time.

## Cons of Switching

- Loses Linux/KVM host support unless Microsandbox remains.
- Requires Apple silicon and should be considered macOS 26+ for supported use;
  the macOS 15 path has documented networking limitations.
- Requires installing and running Apple's system service.
- No native Python SDK path is apparent; shelling out to CLI is slower and more
  brittle than the current Python SDK.
- Need to rebuild the Hoosegow base as OCI and maintain image build/pull logic.
- Need to validate `agent` UID/GID, workspace write behavior, file ownership,
  and symlink behavior under Apple virtiofs mounts.
- Need to validate long-running detached PTY controller behavior over Apple's
  host-side forwarders.
- Apple's default publish host address is broader than Hoosegow wants, so the
  adapter must always bind explicitly to loopback.
- Apple is moving quickly; early 1.0 adoption may create churn.
- Current Microsandbox path is already tailored around Hoosegow's exact control
  plane and has passing local and real-runtime smokes.

## Simultaneous Support

Simultaneous support is practical and preferable to a hard switch.

Suggested runtime abstraction:

```python
class SandboxRuntime:
    async def ensure_installed(self) -> None: ...
    async def create(self, spec: HoosegowSandboxSpec) -> Any: ...
    async def stop(self, name: str) -> None: ...
    async def remove(self, name: str) -> None: ...
    async def get(self, name: str) -> Any | None: ...
    async def connect(self, name: str) -> Any: ...
    async def status(self, name: str) -> str | None: ...
```

Then add backend-specific implementations:

- `MicrosandboxRuntime`: current code, default.
- `AppleContainerRuntime`: experimental, selected by config/env/CLI flag.

Keep the Hoosegow manifest shape backend-neutral:

- `runtime_backend`: `"microsandbox"` or `"apple-container"`.
- `runtime_id`: backend container name/id.
- `published_ports`: same Hoosegow mapping model.
- `mounts`: keep implicit Hoosegow mount contract rather than exposing raw
  backend mount syntax.

Portability policy:

- Default to Microsandbox on Linux.
- Default to Microsandbox on macOS until Apple backend passes real smokes.
- Offer Apple backend only when `container system version` succeeds, host is
  Apple silicon, and macOS is 26+.
- Do not migrate existing sandboxes in place. Recreate them per backend.

The terminal design can stay shared. Both backends should run the same
`hoosegow-ptyd` inside the guest and publish only the controller HTTP port to
host loopback.

## Migration Path

1. Add a backend interface without changing UI behavior.
2. Add Apple host detection and read-only diagnostics.
3. Build a Hoosegow OCI base image equivalent to `hoosegow-microsandbox-local`.
4. Implement Apple `create` by shelling out to `container create/run` with JSON
   inspect/list parsing.
5. Map mounts explicitly:
   - `/app` read-only host bind.
   - `/workspace` writable host bind.
   - `/home/agent` writable host bind.
6. Map ports explicitly as `127.0.0.1:<host>:<guest>/tcp`.
7. Run the existing real-runtime smoke suite against Apple:
   - workspace read/write and symlink escape checks;
   - terminal open/write/resize/status/close;
   - controller health through published port;
   - HTTP dev-server published port;
   - raw TCP diagnostic;
   - start/stop/delete/recreate;
   - file ownership and permissions under `/workspace` and `/home/agent`.
8. Only after that, consider making Apple the default on supported Macs.

## Bottom Line

Apple `container` is strategically worth supporting. It gives Hoosegow a
first-party Mac runtime with a strong OCI story and a richer long-term feature
surface. It should not replace Microsandbox immediately because Hoosegow's
current Microsandbox path is narrow, known, and already integrated, while Apple
is very new, Mac-only, and unproven against Hoosegow's terminal and workspace
permission assumptions.

The best move is dual support: keep Microsandbox as the stable backend and add
Apple `container` as an experimental backend for macOS 26 Apple silicon users.

## Sources

- Apple `container` README and project status:
  <https://github.com/apple/container>
- Apple `container` 1.0.0 release notes:
  <https://github.com/apple/container/releases/tag/1.0.0>
- Apple technical overview:
  <https://github.com/apple/container/blob/main/docs/technical-overview.md>
- Apple how-to docs for host mounts and published ports:
  <https://github.com/apple/container/blob/main/docs/how-to.md>
- Apple command reference for `--publish`, `--mount`, `--volume`, networks, and
  volumes:
  <https://github.com/apple/container/blob/main/docs/command-reference.md>
- Apple source inspected:
  `Sources/Services/ContainerAPIService/Client/Parser.swift`,
  `Sources/Services/ContainerAPIService/Client/Utility.swift`,
  `Sources/ContainerResource/Container/PublishPort.swift`,
  `Sources/ContainerResource/Container/Filesystem.swift`,
  `Sources/SocketForwarder/TCPForwarder.swift`,
  `Sources/SocketForwarder/UDPForwarder.swift`,
  `Sources/Services/ContainerAPIService/Server/Volumes/VolumesService.swift`.
- Hoosegow source inspected:
  `server/microsandbox_runtime.py`, `server/sandboxes.py`,
  `server/sandbox_bootstrap.py`, `server/sandbox_store.py`,
  `docs/security.md`, `docs/pty-controller-spike.md`,
  `docs/release-checklist.md`.
