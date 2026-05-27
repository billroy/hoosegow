import shutil
import subprocess
from pathlib import Path

import pytest

import server.manager as manager_mod
from server.init import init_workspace
from server.manager import (
    DEFAULT_MICROSANDBOX_BASE,
    ManagerError,
    MicrosandboxRuntimeController,
    PortAllocator,
    ProfileRegistry,
    create_manager_app,
    create_profile,
)
from server.persistence import write_json


def test_create_profile_allocates_ports_and_persists(tmp_path):
    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "bullpen.py").write_text("", encoding="utf-8")

    profile = create_profile(
        registry,
        {
            "displayName": "Local Dev",
            "workspaceRoot": str(workspace),
            "bullpenSource": str(source),
        },
    )

    assert profile["id"] == "local-dev"
    assert profile["runtime"] == "local"
    assert profile["ports"] == {"bullpen": 8080, "app": 3000}
    assert profile["portReservation"]["owner"] == "local-dev"
    assert registry.get("local-dev")["workspaceRoot"] == str(workspace)


def test_port_allocator_skips_reserved_and_listening_ports(tmp_path, monkeypatch):
    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "bullpen.py").write_text("", encoding="utf-8")
    create_profile(
        registry,
        {
            "displayName": "First",
            "workspaceRoot": str(workspace),
            "bullpenSource": str(source),
        },
    )
    monkeypatch.setattr(manager_mod, "is_port_listening", lambda port, *args, **kwargs: int(port) == 8081)
    allocator = PortAllocator(registry, bullpen_range=(8081, 8083), app_range=(3001, 3003))
    allocated = allocator.allocate()
    assert allocated == {"bullpen": 8082, "app": 3002}


def test_create_profile_rejects_deferred_docker_runtime(tmp_path):
    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ManagerError, match="deferred"):
        create_profile(
            registry,
            {
                "displayName": "Docker",
                "runtime": "docker",
                "workspaceRoot": str(workspace),
            },
        )


def test_create_microsandbox_profile(tmp_path):
    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    profile = create_profile(
        registry,
        {
            "displayName": "Sandbox",
            "runtime": "microsandbox",
            "workspaceRoot": str(workspace),
            "adminPassword": "secret-password",
            "vcpus": 6,
            "memoryMiB": 8192,
        },
    )

    assert profile["runtime"] == "microsandbox"
    assert profile["sandboxName"] == "bullpen-sandbox"
    assert profile["sandboxHome"] == profile["instanceHome"]
    assert profile["base"] == DEFAULT_MICROSANDBOX_BASE
    assert profile["auth"]["adminUser"] == "admin"
    assert profile["auth"]["adminPassword"] == "secret-password"
    assert profile["resources"] == {"vcpus": 6, "memoryMiB": 8192}


def test_microsandbox_deployment_info_reports_provider_auth_status(tmp_path):
    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    profile = create_profile(
        registry,
        {
            "displayName": "Sandbox",
            "runtime": "microsandbox",
            "workspaceRoot": str(workspace),
            "adminPassword": "secret-password",
        },
    )

    info = manager_mod.deployment_info(profile)
    assert info["providerAuth"]["claude"]["authenticated"] is False
    assert info["providerAuth"]["codex"]["authenticated"] is False
    assert info["providerAuth"]["git"]["authenticated"] is False

    home = Path(profile["sandboxHome"])
    write_json(str(home / ".claude" / ".credentials.json"), {"claudeAiOauth": {"refreshToken": "token"}})
    write_json(str(home / ".codex" / "auth.json"), {"OPENAI_API_KEY": "token"})
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".config" / "gh" / "hosts.yml").write_text("github.com:\n  oauth_token: token\n", encoding="utf-8")
    (home / ".gitconfig").write_text("[user]\n\tname = Bullpen\n\temail = bullpen@example.test\n", encoding="utf-8")

    info = manager_mod.deployment_info(profile)
    assert info["providerAuth"]["claude"]["authenticated"] is True
    assert info["providerAuth"]["codex"]["authenticated"] is True
    assert info["providerAuth"]["git"]["authenticated"] is True


def test_create_microsandbox_profile_uses_selected_base_snapshot(tmp_path):
    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    profile = create_profile(
        registry,
        {
            "displayName": "Sandbox",
            "runtime": "microsandbox",
            "workspaceRoot": str(workspace),
            "adminPassword": "secret-password",
            "base": "bullpen-microsandbox-local-v2",
        },
    )

    assert profile["base"] == "bullpen-microsandbox-local-v2"


def test_create_microsandbox_profile_requires_admin_password(tmp_path):
    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ManagerError, match="adminPassword"):
        create_profile(
            registry,
            {
                "displayName": "Sandbox",
                "runtime": "microsandbox",
                "workspaceRoot": str(workspace),
            },
        )


def test_create_microsandbox_profile_rejects_invalid_resources(tmp_path):
    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ManagerError, match="vcpus"):
        create_profile(
            registry,
            {
                "displayName": "Sandbox",
                "runtime": "microsandbox",
                "workspaceRoot": str(workspace),
                "adminPassword": "secret-password",
                "vcpus": 0,
            },
        )


def test_manager_api_create_profile(tmp_path):
    home = tmp_path / "manager"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "bullpen.py").write_text("", encoding="utf-8")
    app, _socketio = create_manager_app(home=home)

    client = app.test_client()
    response = client.post(
        "/api/profiles",
        json={
            "displayName": "API Instance",
            "workspaceRoot": str(workspace),
            "bullpenSource": str(source),
        },
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["profile"]["id"] == "api-instance"
    assert data["profile"]["ports"]["bullpen"] == 8080

    list_response = client.get("/api/profiles")
    assert list_response.status_code == 200
    assert list_response.get_json()["profiles"][0]["id"] == "api-instance"


def test_manager_api_profiles_include_deployment_info(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    home = tmp_path / "manager"
    workspace_root = tmp_path / "workspace-root"
    project = workspace_root / "project-a"
    project.mkdir(parents=True)
    source = tmp_path / "source"
    source.mkdir()
    (source / "bullpen.py").write_text("", encoding="utf-8")
    bp_dir = Path(init_workspace(str(project)))
    write_json(
        str(bp_dir / "layout.json"),
        {
            "slots": [
                {"type": "ai", "agent": "codex", "model": "gpt-5.3-codex"},
                {"type": "ai", "agent": "claude", "model": "claude-sonnet-4-6"},
                {"type": "shell", "command": "pytest"},
            ]
        },
    )
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True, text=True)
    (project / "README.md").write_text("hello\n", encoding="utf-8")
    app, _socketio = create_manager_app(home=home)

    client = app.test_client()
    create_response = client.post(
        "/api/profiles",
        json={
            "displayName": "Deployment Info",
            "workspaceRoot": str(workspace_root),
            "bullpenSource": str(source),
        },
    )
    assert create_response.status_code == 201

    response = client.get("/api/profiles")

    assert response.status_code == 200
    [profile] = response.get_json()["profiles"]
    info = profile["deploymentInfo"]
    assert info["resources"]["source"] == "host"
    assert {provider["agent"] for provider in info["aiProviders"]} == {"claude", "codex"}
    codex = next(provider for provider in info["aiProviders"] if provider["agent"] == "codex")
    assert codex["model"] == "gpt-5.3-codex"
    assert codex["count"] == 1
    assert codex["workspaces"] == ["project-a"]
    assert info["git"]["repositories"][0]["name"] == "project-a"
    assert info["git"]["repositories"][0]["dirty"] is True


def test_manager_api_lists_microsandbox_base_snapshots(tmp_path, monkeypatch):
    class Snapshot:
        def __init__(self, name, image_ref):
            self.name = name
            self.digest = f"sha256:{name}"
            self.image_ref = image_ref
            self.created_at = 1234
            self.size_bytes = 4096

    class SnapshotApi:
        @staticmethod
        async def list():
            return [
                Snapshot("bullpen-microsandbox-local-v2", "node:22-bookworm"),
                Snapshot(DEFAULT_MICROSANDBOX_BASE, "node:22-bookworm"),
            ]

    class FakeRuntime:
        Snapshot = SnapshotApi

    class FakeModule:
        MicrosandboxRuntime = FakeRuntime

    monkeypatch.setattr(manager_mod, "_load_deploy_sandbox_module", lambda: FakeModule)
    app, _socketio = create_manager_app(home=tmp_path / "manager")

    response = app.test_client().get("/api/microsandbox/base-snapshots")

    assert response.status_code == 200
    data = response.get_json()
    assert [snapshot["name"] for snapshot in data["snapshots"]] == [
        DEFAULT_MICROSANDBOX_BASE,
        "bullpen-microsandbox-local-v2",
    ]
    assert data["snapshots"][0]["imageRef"] == "node:22-bookworm"


def test_manager_serves_empty_favicon(tmp_path):
    app, _socketio = create_manager_app(home=tmp_path / "manager")

    response = app.test_client().get("/favicon.ico")

    assert response.status_code == 204


def test_manager_socketio_uses_threading_mode(tmp_path):
    _app, socketio = create_manager_app(home=tmp_path / "manager")

    assert socketio.server.eio.async_mode == "threading"


def test_manager_serves_vendored_xterm_assets(tmp_path):
    app, _socketio = create_manager_app(home=tmp_path / "manager")

    response = app.test_client().get("/vendor/xterm/xterm.js")

    assert response.status_code == 200
    assert response.content_type.startswith("text/javascript")
    assert len(response.data) > 100000


def test_manager_provider_setup_uses_raw_xterm_input():
    manager_js = Path("static/manager/manager.js").read_text(encoding="utf-8")

    assert "terminal.value.onData" in manager_js
    assert "disableStdin: false" in manager_js
    assert "socketRef.value.emit('manager:pty-input', { sessionId: state.setupSessionId, data })" in manager_js
    assert "terminal.value.onResize" in manager_js
    assert "manager:pty-resize" in manager_js
    assert 'class="terminal-input-row"' not in manager_js
    assert "const matchesStartingProfile = !state.setupSessionId && payload.profileId === state.setupProfileId;" in manager_js
    assert "if (!state.setupSessionId && payload.sessionId) state.setupSessionId = payload.sessionId;" in manager_js


def test_manager_provider_setup_terminal_fits_enclosing_pane():
    manager_js = Path("static/manager/manager.js").read_text(encoding="utf-8")
    manager_css = Path("static/manager/manager.css").read_text(encoding="utf-8")

    assert "terminalResizeObserver.value = new ResizeObserver(() => scheduleTerminalFit())" in manager_js
    assert "function fitTerminal()" in manager_js
    assert "terminal.value.resize(cols, rows);" in manager_js
    assert "syncTerminalPtySize();" in manager_js
    assert "width: 100%;" in manager_css
    assert "height: 100%;" in manager_css


def test_manager_keeps_provider_setup_to_single_live_terminal():
    manager_js = Path("static/manager/manager.js").read_text(encoding="utf-8")

    assert "function startSetupLogPolling" not in manager_js
    assert "function catchUpSetupOutput" not in manager_js
    assert "setupCatchupPoll" not in manager_js
    assert "function syncSetupTranscript" in manager_js
    assert "appendSetupOutput(text.slice(state.setupOutput.length))" in manager_js
    assert "terminal.value.clear();" in manager_js
    assert "terminal-auth" not in manager_js
    assert "terminal-prompt" not in manager_js
    assert '<div class="panel" v-if="showLogPanel(selected)">' in manager_js
    assert "return Boolean(profile && profile.runtime !== 'microsandbox');" in manager_js


def test_manager_hides_provider_setup_terminal_until_used():
    manager_js = Path("static/manager/manager.js").read_text(encoding="utf-8")

    assert "function showSetupPanel(profile)" in manager_js
    assert "(state.setupBusy && state.setupProfileId === profile.id)" in manager_js
    assert "stateLabel(profile) === 'setup-running'" in manager_js
    assert "state.setupProfileId === profile.id" in manager_js
    assert "&& (state.setupSessionId || state.setupOutput || state.setupExit)" in manager_js
    assert '<div class="panel" v-if="showSetupPanel(selected)">' in manager_js
    assert '<div class="panel" v-if="selected.runtime === \'microsandbox\'">' not in manager_js


def test_manager_renders_bullpen_and_app_links():
    manager_js = Path("static/manager/manager.js").read_text(encoding="utf-8")

    assert "function bullpenUrlFor(profile)" in manager_js
    assert "function appUrlFor(profile)" in manager_js
    assert '`http://127.0.0.1:${profile.ports.bullpen}`' in manager_js
    assert '`http://127.0.0.1:${profile.ports.app}`' in manager_js
    assert 'class="instance-links"' not in manager_js
    assert ':href="bullpenUrlFor(profile)" target="_blank" rel="noopener" @click.stop>Bullpen {{ profile.ports.bullpen }}</a>' in manager_js
    assert ':href="appUrlFor(profile)" target="_blank" rel="noopener" @click.stop>App {{ profile.ports.app }}</a>' in manager_js
    assert ':href="bullpenUrlFor(selected)" target="_blank" rel="noopener">{{ bullpenUrlFor(selected) }}</a>' in manager_js
    assert ':href="appUrlFor(selected)" target="_blank" rel="noopener">{{ appUrlFor(selected) }}</a>' in manager_js


def test_manager_renders_selected_deployment_info_rows():
    manager_js = Path("static/manager/manager.js").read_text(encoding="utf-8")
    manager_css = Path("static/manager/manager.css").read_text(encoding="utf-8")

    assert "function cpuText(profile)" in manager_js
    assert "function memoryText(profile)" in manager_js
    assert "function providersText(profile)" in manager_js
    assert "`${value} MiB${suffix}`" in manager_js
    assert "const allowed = new Set(['claude', 'codex', 'git']);" in manager_js
    assert "deploymentInfo(profile).providerAuth || {}" in manager_js
    assert "(deploymentInfo(profile).aiProviders || []).forEach" in manager_js
    assert "if (allowed.has(agent)) providers.set(agent, provider.label || providerLabel(agent));" in manager_js
    assert "providers.set('git', 'Git')" in manager_js
    assert "${authenticated ? 'authenticated' : 'not authenticated'}" in manager_js
    assert "Gemini" not in manager_js
    sandbox_index = manager_js.index('<div class="kv" v-if="selected.sandboxName"><strong>Sandbox</strong><span>{{ selected.sandboxName }}</span></div>')
    bullpen_index = manager_js.index('<strong>Bullpen</strong>')
    assert sandbox_index < bullpen_index
    assert '<div class="kv"><strong>CPU</strong><span>{{ cpuText(selected) }}</span></div>' in manager_js
    assert '<div class="kv"><strong>Memory</strong><span>{{ memoryText(selected) }}</span></div>' in manager_js
    assert '<div class="kv"><strong>Providers</strong><span>{{ providersText(selected) }}</span></div>' in manager_js
    assert '<strong>Configured AI</strong>' not in manager_js
    assert '<div class="kv"><strong>Git</strong>' not in manager_js
    assert '<div class="kv"><strong>Ports</strong>' not in manager_js
    assert ".kv span" in manager_css
    assert "overflow-wrap: anywhere;" in manager_css


def test_manager_create_deployment_lives_in_modal_menu_without_personal_placeholders():
    manager_js = Path("static/manager/manager.js").read_text(encoding="utf-8")

    assert '<div class="panel-title">Deployments</div>' in manager_js
    assert 'aria-label="Deployment actions"' in manager_js
    assert '@click="openCreateModal">Create Deployment</button>' in manager_js
    assert 'class="modal-backdrop"' in manager_js
    assert 'id="create-deployment-title" class="panel-title">Create Deployment</div>' in manager_js
    assert 'placeholder="/path/to/workspace-root"' in manager_js
    assert 'placeholder="/Users/bill/aistuff"' not in manager_js
    assert 'placeholder="bullpen-personal"' not in manager_js
    assert '<label>CPU</label>' in manager_js
    assert '<label>Memory MiB</label>' in manager_js
    assert 'v-model.number="form.vcpus"' in manager_js
    assert 'v-model.number="form.memoryMiB"' in manager_js
    assert '<input type="number" min="1" step="1" v-model.number="form.memoryMiB" required>' in manager_js


def test_manager_create_deployment_uses_base_snapshot_dropdown():
    manager_js = Path("static/manager/manager.js").read_text(encoding="utf-8")
    manager_css = Path("static/manager/manager.css").read_text(encoding="utf-8")

    assert "baseSnapshots: []" in manager_js
    assert "api('/api/microsandbox/base-snapshots')" in manager_js
    assert "const baseSnapshotOptions = computed(() => {" in manager_js
    assert '<select v-model="form.base" :disabled="state.baseSnapshotsLoading" @keydown.enter="openDropdownOnEnter">' in manager_js
    assert 'v-for="snapshot in baseSnapshotOptions"' in manager_js
    assert "{{ baseSnapshotLabel(snapshot) }}" in manager_js
    assert '<input v-model="form.base">' not in manager_js
    assert ".field-error" in manager_css


def test_manager_dropdowns_open_on_enter_key():
    manager_js = Path("static/manager/manager.js").read_text(encoding="utf-8")

    assert "function openDropdownOnEnter(event)" in manager_js
    assert "typeof select.showPicker === 'function'" in manager_js
    assert "event.preventDefault();" in manager_js
    assert "select.showPicker();" in manager_js
    assert '<select v-model="form.runtime" @keydown.enter="openDropdownOnEnter">' in manager_js
    assert '<select v-model="form.base" :disabled="state.baseSnapshotsLoading" @keydown.enter="openDropdownOnEnter">' in manager_js


def test_microsandbox_runtime_builds_deploy_command(tmp_path):
    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profile = create_profile(
        registry,
        {
            "displayName": "Sandbox",
            "runtime": "microsandbox",
            "workspaceRoot": str(workspace),
            "adminPassword": "secret-password",
            "adminUser": "rootish",
            "vcpus": 8,
            "memoryMiB": 12288,
        },
    )

    argv = MicrosandboxRuntimeController(registry).build_argv(profile)

    assert "deploy-sandbox.py" in argv[1]
    assert "--workspace-root" in argv
    assert str(workspace) in argv
    assert "--sandbox-name" in argv
    assert "bullpen-sandbox" in argv
    assert "--admin-user" in argv
    assert "rootish" in argv
    assert "--admin-password" in argv
    assert "secret-password" in argv
    assert "--vcpus" in argv
    assert argv[argv.index("--vcpus") + 1] == "8"
    assert "--memory-mib" in argv
    assert argv[argv.index("--memory-mib") + 1] == "12288"
    assert "--replace" in argv
    assert "--no-open" in argv
    assert argv[argv.index("--provider-setup") + 1] == "skip"

    interactive_argv = MicrosandboxRuntimeController(registry).build_argv(profile, provider_setup="interactive")
    assert interactive_argv[interactive_argv.index("--provider-setup") + 1] == "interactive"


def test_microsandbox_start_runs_deploy_and_sets_state(tmp_path, monkeypatch):
    class FakeProcess:
        pid = 4343

        def wait(self):
            return 0

        def poll(self):
            return 0

    class ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profile = create_profile(
        registry,
        {
            "displayName": "Sandbox",
            "runtime": "microsandbox",
            "workspaceRoot": str(workspace),
            "adminPassword": "secret-password",
        },
    )
    calls = []
    monkeypatch.setattr(manager_mod, "is_port_listening", lambda *args, **kwargs: False)
    monkeypatch.setattr(manager_mod, "wait_for_http_health", lambda *args, **kwargs: True)
    monkeypatch.setattr(manager_mod.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(manager_mod.subprocess, "Popen", lambda argv, **kwargs: calls.append(argv) or FakeProcess())

    started = MicrosandboxRuntimeController(registry).start(profile["id"])

    assert calls
    assert calls[0][calls[0].index("--provider-setup") + 1] == "skip"
    assert started["desiredState"] == "running"
    assert registry.get(profile["id"])["observed"]["state"] == "healthy"


def test_microsandbox_start_returns_starting_before_background_finishes(tmp_path, monkeypatch):
    class DeferredThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            return None

    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profile = create_profile(
        registry,
        {
            "displayName": "Sandbox",
            "runtime": "microsandbox",
            "workspaceRoot": str(workspace),
            "adminPassword": "secret-password",
        },
    )
    monkeypatch.setattr(manager_mod, "is_port_listening", lambda *args, **kwargs: False)
    monkeypatch.setattr(manager_mod.threading, "Thread", DeferredThread)

    started = MicrosandboxRuntimeController(registry).start(profile["id"])

    assert started["desiredState"] == "running"
    assert started["observed"]["state"] == "starting"


def test_microsandbox_active_setup_session_reconnects_to_running_pty(tmp_path):
    class FakeProcess:
        pid = 5151

        def poll(self):
            return None

    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profile = create_profile(
        registry,
        {
            "displayName": "Sandbox",
            "runtime": "microsandbox",
            "workspaceRoot": str(workspace),
            "adminPassword": "secret-password",
        },
    )
    controller = MicrosandboxRuntimeController(registry)
    controller._pty_sessions["session-1"] = {
        "profile_id": profile["id"],
        "master_fd": 123,
        "process": FakeProcess(),
        "log_path": str(tmp_path / "provider-setup.log"),
        "bullpen_port": 8080,
    }

    active = controller.active_setup_session(profile["id"])

    assert active["sessionId"] == "session-1"
    assert active["profile"]["id"] == profile["id"]
    assert active["logPath"].endswith("provider-setup.log")


def test_microsandbox_resize_pty_clamps_and_applies_winsize(tmp_path, monkeypatch):
    class FakeProcess:
        def poll(self):
            return None

    calls = []
    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profile = create_profile(
        registry,
        {
            "displayName": "Sandbox",
            "runtime": "microsandbox",
            "workspaceRoot": str(workspace),
            "adminPassword": "secret-password",
        },
    )
    controller = MicrosandboxRuntimeController(registry)
    controller._pty_sessions["session-1"] = {
        "profile_id": profile["id"],
        "master_fd": 123,
        "process": FakeProcess(),
        "log_path": str(tmp_path / "provider-setup.log"),
        "bullpen_port": 8080,
    }
    monkeypatch.setattr(manager_mod.fcntl, "ioctl", lambda fd, request, winsize: calls.append((fd, request, winsize)))

    controller.resize_pty("session-1", cols=999, rows=1)

    assert calls
    assert calls[0][0] == 123
    assert calls[0][1] == manager_mod.termios.TIOCSWINSZ
    assert manager_mod.struct.unpack("HHHH", calls[0][2])[:2] == (5, 300)


def test_microsandbox_reconcile_marks_interrupted_setup_needs_attention(tmp_path, monkeypatch):
    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profile = create_profile(
        registry,
        {
            "displayName": "Sandbox",
            "runtime": "microsandbox",
            "workspaceRoot": str(workspace),
            "adminPassword": "secret-password",
        },
    )
    profile["observed"]["state"] = "setup-running"
    profile["observed"]["pid"] = 5151
    registry.upsert(profile)
    monkeypatch.setattr(manager_mod, "wait_for_http_health", lambda *args, **kwargs: True)

    [reconciled] = MicrosandboxRuntimeController(registry).reconcile()

    assert reconciled["observed"]["state"] == "needs-attention"
    assert "Provider setup was interrupted" in reconciled["observed"]["lastError"]
    assert "pid" not in reconciled["observed"]


def test_local_runtime_builds_bullpen_command(tmp_path):
    from server.manager import LocalRuntimeController

    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "bullpen.py").write_text("", encoding="utf-8")
    profile = create_profile(
        registry,
        {
            "displayName": "Command",
            "workspaceRoot": str(workspace),
            "bullpenSource": str(source),
        },
    )

    argv = LocalRuntimeController(registry).build_argv(profile)

    assert argv[1] == str(source / "bullpen.py")
    assert "--workspace" in argv
    assert str(workspace) in argv
    assert "--no-browser" in argv


def test_local_runtime_start_sets_desired_state(tmp_path, monkeypatch):
    from server.manager import LocalRuntimeController

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

    registry = ProfileRegistry(tmp_path / "manager")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "bullpen.py").write_text("", encoding="utf-8")
    profile = create_profile(
        registry,
        {
            "displayName": "Start Me",
            "workspaceRoot": str(workspace),
            "bullpenSource": str(source),
        },
    )
    monkeypatch.setattr(manager_mod, "is_port_listening", lambda *args, **kwargs: False)
    monkeypatch.setattr(manager_mod, "wait_for_http_health", lambda *args, **kwargs: True)
    monkeypatch.setattr(manager_mod.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    started = LocalRuntimeController(registry).start(profile["id"])

    assert started["desiredState"] == "running"
    assert started["observed"]["state"] == "healthy"
    assert registry.get(profile["id"])["desiredState"] == "running"
