import json
import os
from pathlib import Path
import subprocess
import sys

from server.app import create_app
from server.app import socketio


ROOT = Path(__file__).resolve().parents[1]


def test_toady_mode_hides_legacy_product_rest_apis(tmp_path):
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    client = app.test_client()

    assert client.get("/health").status_code == 200
    assert client.get("/api/commits").status_code == 404
    assert client.get("/api/files").status_code == 404
    assert client.post("/api/worker/transfer").status_code == 404
    assert client.get("/api/export/workspace").status_code == 404
    assert client.post("/api/import/workspace").status_code == 404
    assert client.post("/api/service/preview").status_code == 404


def test_toady_mode_does_not_register_legacy_product_rest_routes(tmp_path):
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )

    rules = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/api/commits" not in rules
    assert "/api/files" not in rules
    assert "/api/worker/transfer" not in rules
    assert "/api/export/workspace" not in rules
    assert "/api/import/workspace" not in rules
    assert "/api/service/preview" not in rules


def test_toady_mode_hides_legacy_product_static_assets(tmp_path):
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    client = app.test_client()

    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200
    assert client.get("/vendor/xterm/xterm.js").status_code == 200
    assert client.get("/manager/vendor/xterm/xterm.js").status_code == 404
    assert client.get("/components/BullpenTab.js").status_code == 404
    assert client.get("/commands.js").status_code == 404
    assert client.get("/manager/manager.js").status_code == 404
    assert client.get("/manager/index.html").status_code == 404


def test_toady_mode_served_shell_has_no_legacy_product_copy(tmp_path):
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    client = app.test_client()

    for path in ("/", "/app.js", "/style.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert b"Bullpen" not in response.data
        assert b"bullpen" not in response.data


def test_toady_mode_does_not_activate_legacy_project_registry(tmp_path):
    legacy_project = tmp_path / "legacy-project"
    legacy_project.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "projects.json").write_text(
        json.dumps({
            "version": 1,
            "projects": [
                {"id": "legacy-id", "name": "Legacy", "path": str(legacy_project)},
            ],
        }),
        encoding="utf-8",
    )

    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(state),
        start_without_project=True,
    )
    client = socketio.test_client(app)
    received_events = {event["name"] for event in client.get_received()}
    client.disconnect()

    assert app.config["manager"].all_workspaces() == []
    assert not (legacy_project / ".bullpen").exists()
    assert "state:init" not in received_events
    assert "projects:updated" not in received_events


def test_toady_mode_does_not_eagerly_import_legacy_product_modules(tmp_path):
    script = """
import json
import sys
from server.app import create_app

create_app(sys.argv[1], no_browser=True, global_dir=sys.argv[2], start_without_project=True)
legacy_modules = [
    "server.events",
    "server.mcp_auth",
    "server.service_worker",
    "server.terminal",
    "server.scheduler",
    "server.transfer",
    "server.profiles",
    "server.teams",
    "server.worker_types",
    "server.worktrees",
    "server.workspace_manager",
    "server.init",
]
print(json.dumps([name for name in legacy_modules if name in sys.modules]))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(tmp_path / "state")],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_legacy_deploy_entrypoints_are_not_shipped():
    for relative in (
        "bullpen.py",
        "deploy-sandbox.py",
        "deploy/microsandbox/bullpen-proxy.js",
        "server/workspace_manager.py",
    ):
        assert not (ROOT / relative).exists()


def test_server_log_records_http_and_socket_events_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    workspace_root = tmp_path / "work"
    workspace_root.mkdir()
    state = tmp_path / "state"
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(state),
        start_without_project=True,
    )
    service = app.config["toady_sandboxes"]
    service.browse_roots = [str(tmp_path)]
    service.port_pool = (63100, 63105)

    response = app.test_client().get("/health")
    client = socketio.test_client(app)
    created = client.emit(
        "sandbox:create",
        {"name": "demo", "workspace_root": str(workspace_root)},
        callback=True,
    )
    client.disconnect()

    log_path = state / "logs" / "server.log"
    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    body = log_path.read_text(encoding="utf-8")

    assert response.headers["X-Toady-Request-Id"]
    assert created["ok"] is True
    assert any(record["event"] == "http_request" and record["path"] == "/health" for record in records)
    assert any(
        record["event"] == "socket_event"
        and record["socket_event"] == "sandbox:create"
        and record["sandbox_id"] == "demo"
        for record in records
    )
    assert created["sandbox"]["controller"]["token"] not in body
    assert str(workspace_root) not in body


def test_base_logs_are_available_after_reconnect(tmp_path):
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    app.config["base_prepare"].update({
        "logs": ["first line", "second line"],
        "returncode": 0,
        "duration_seconds": 12.3,
    })

    first_client = socketio.test_client(app)
    first_client.disconnect()
    second_client = socketio.test_client(app)
    response = second_client.emit("base:logs", callback=True)
    second_client.disconnect()

    assert response == {
        "ok": True,
        "prepare": {
            "running": False,
            "returncode": 0,
            "duration_seconds": 12.3,
            "automatic": False,
            "rebuild": False,
            "logs": ["first line", "second line"],
        },
    }


def test_base_status_auto_starts_missing_base_setup(tmp_path, monkeypatch):
    async def fake_base_status():
        return {
            "name": "toady-microsandbox-local",
            "prepared": False,
            "state": "missing",
            "message": "missing",
        }

    started = []

    def fake_start_background_task(function, *args):
        started.append((function.__name__, args))
        return None

    monkeypatch.setattr("server.app.toady_base.base_status", fake_base_status)
    monkeypatch.setattr("server.app.socketio.start_background_task", fake_start_background_task)
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    client = socketio.test_client(app)
    response = client.emit("base:status", callback=True)
    client.disconnect()

    assert response["ok"] is True
    assert started == [("_base_prepare_worker", (False, True))]
    assert response["base"]["state"] == "preparing"
    assert response["base"]["message"] == "Setting up sandbox runtime..."
    assert response["base"]["prepare"]["running"] is True
    assert response["base"]["prepare"]["automatic"] is True


def test_toady_shell_has_theme_toggle_assets():
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "toady-theme" in app_js
    assert "toggleTheme" in app_js
    assert "TERMINAL_THEMES" in app_js
    assert "applyTerminalTheme" in app_js
    assert "terminal.value.options.theme = currentTerminalTheme()" in app_js
    assert "theme: currentTerminalTheme()" in app_js
    assert "background: '#f8fafc'" in app_js
    assert "foreground: '#17202a'" in app_js
    assert ':data-lucide="theme ===' in app_js
    assert 'title="Toggle theme"' in app_js
    assert "header-icon-button" in app_js
    assert "header-menu-button" in app_js
    assert '<span class="menu-item-label">Toggle theme</span>' not in app_js
    assert "{{ selected ? selected.slug" not in app_js
    assert 'html[data-theme="light"]' in css
    assert "--terminal-bg: #f8fafc;" in css
    assert "--terminal-text: #17202a;" in css
    assert "background: var(--terminal-bg);" in css
    assert "color: var(--terminal-text);" in css


def test_toady_shell_has_clear_empty_and_error_states():
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "baseStatus.error" in app_js
    assert "No Sandboxes" not in app_js
    assert "Workspace root required" not in app_js
    assert '<i data-lucide="plus"></i><span>Create Sandbox</span>' in app_js
    assert '<span>Workspace root</span>' in app_js
    assert "Publish :{{ portForm.guest_port }}" in app_js
    assert "Sandbox Runtime" not in app_js
    assert "menu-section" not in app_js
    assert ".menu-section" not in css
    assert ".menu-label" not in css
    assert "Runtime logs" in app_js
    assert "Waiting for setup" in app_js
    assert "Toady is doing this automatically." in app_js
    assert "Prepare the base from the main menu" not in app_js
    assert "sandbox:logs" in app_js
    assert "Sandbox Logs" in app_js
    assert "openCreateModal" in app_js
    assert "openDetailsModal" in app_js
    assert "openPortsModal" in app_js
    assert '@click="openDetailsModal()"' not in app_js
    assert '@click="openPortsModal()"' not in app_js
    assert '@click="openSandboxLogs(selected)"' not in app_js
    assert "Sandbox details" not in app_js
    assert "Sandbox logs" not in app_js
    assert '@click="openDetailsModal(sandbox)"' in app_js
    assert '@click="openPortsModal(sandbox)"' in app_js
    assert '@click="openSandboxLogs(sandbox)"' in app_js
    assert "toggleSandboxActionMenu" in app_js
    assert "toggleMainMenu" in app_js
    assert "toggleSandboxMenu" in app_js
    assert "sandboxActionMenuSlug" in app_js
    assert "menu-item-icon" in app_js
    assert "menu-item-label" in app_js
    assert app_js.count('<button class="menu-item') == app_js.count('class="menu-item-icon"')
    assert "New terminal" in app_js
    assert "terminal-tab-add" in app_js
    assert ':disabled="!canOpenTerminal"' in app_js
    assert '@click="openTerminal(selected)"' in app_js
    assert "Use + to open a terminal." not in app_js
    assert "Opening terminal..." in app_js
    assert "shouldAutoReplace" in app_js
    assert "options.autoReplace !== false" in app_js
    assert "await openTerminal(selectedSandbox, { manageBusy: false, manageAction: false, silent: true })" in app_js
    assert "await closeTerminal({ ...options, autoReplace: false, terminalId })" in app_js
    assert "Sandboxes ({{ sortedSandboxes.length }})" in app_js
    assert "closeMenusOnOutsideClick" in app_js
    assert "document.addEventListener('click', closeMenusOnOutsideClick)" in app_js
    assert 'title="Main menu" @click.stop="toggleMainMenu"' in app_js
    assert 'title="Sandbox menu" @click.stop="toggleSandboxMenu"' in app_js
    assert "if (!focused && sandbox.last_status === 'running')" in app_js
    assert "silent: true" in app_js
    assert "terminalReplayMuted" in app_js
    assert "terminalReplayToken" in app_js
    assert "convertEol: false" in app_js
    assert "terminal.value?._core?._renderService?.dimensions?.css?.cell" in app_js
    assert "if (terminalReplayMuted.value) return;" in app_js
    assert "await writeTerminalReplay(record.transcript)" in app_js
    assert "if (terminalReplayToken.value === replayToken) terminalReplayMuted.value = false;" in app_js
    assert "connection-dot" in app_js
    assert "Connected" not in app_js
    assert 'title="Refresh"' not in app_js
    assert "Toady sandbox terminal" not in (ROOT / "guest" / "toady-ptyd.py").read_text(encoding="utf-8")
    assert "terminal-title" not in app_js
    assert ".terminal-title" not in css
    assert "beginSidebarResize" in app_js
    assert "toady-sidebar-collapsed" in app_js
    assert "sidebarCollapsed" in app_js
    assert "toggleSidebar" in app_js
    assert "Show sandboxes" in app_js
    assert "Hide sandboxes" in app_js
    assert "panel-left-open" in app_js
    assert "panel-left-close" in app_js
    assert "if (sidebarCollapsed.value) return;" in app_js
    assert "sidebar-collapsed" in css
    assert ".toady-shell.sidebar-collapsed .workspace" in css
    assert "grid-column: 1 / -1;" in css
    assert ".empty-state" in css
    assert ".menu-panel" in css
    assert "pointer-events: none;" in css
    assert "pointer-events: auto;" in css
    assert "--primary: #1d4ed8;" in css
    assert "--primary-text: #ffffff;" in css
    assert "background: var(--primary);" in css
    assert "color: var(--primary-text);" in css
    assert ".sidebar-resizer" in css
    assert ".menu-panel .menu-item-icon" in css
    assert ".menu-panel .menu-item-label" in css
    assert ".sidebar-heading > span" in css
    assert ".sidebar-heading span {" not in css
    assert "grid-template-columns: 20px minmax(0, 1fr);" in css
    assert "font-size: 15px;" in css
    assert "width: 18px;" in css
    assert "height: 18px;" in css
    assert "font: inherit;" in css
    assert ".terminal-tab-add" in css


def test_real_microsandbox_smokes_default_to_toady_base():
    for relative in (
        "scripts/microsandbox_port_smoke.py",
        "scripts/microsandbox_raw_tcp_smoke.py",
        "scripts/pty_controller_microsandbox_smoke.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert 'SNAPSHOT_DEFAULT = "toady-microsandbox-local"' in text
        assert "TOADY_MICROSANDBOX_BASE" in text
        assert "bullpen-microsandbox-local" not in text


def test_toady_base_installs_declared_agent_clis():
    base_py = (ROOT / "server" / "base.py").read_text(encoding="utf-8")

    assert "npm install -g --no-audit --no-fund --no-progress --omit=dev @anthropic-ai/claude-code" in base_py
    assert "npm install -g --no-audit --no-fund --no-progress --omit=dev @openai/codex" in base_py
    assert "npm install -g --no-audit --no-fund --no-progress --omit=dev @google/gemini-cli" in base_py
    assert "npm install -g --no-audit --no-fund --no-progress --omit=dev opencode-ai" in base_py
    assert "claude --version" in base_py
    assert "codex --version" in base_py
    assert "gemini --version" in base_py
    assert "opencode --version" in base_py
