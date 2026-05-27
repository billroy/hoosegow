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
            "logs": ["first line", "second line"],
        },
    }


def test_toady_shell_has_theme_toggle_assets():
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "toady-theme" in app_js
    assert "toggleTheme" in app_js
    assert ':data-lucide="theme ===' in app_js
    assert 'html[data-theme="light"]' in css


def test_toady_shell_has_clear_empty_and_error_states():
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "baseStatus.error" in app_js
    assert "No Sandboxes" in app_js
    assert "Workspace Root" in app_js
    assert "Publish :{{ portForm.guest_port }}" in app_js
    assert ".empty-state" in css
    assert ".base-banner-actions" in css


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
