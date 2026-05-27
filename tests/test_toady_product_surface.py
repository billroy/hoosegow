import json

from server.app import create_app
from server.app import socketio


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
    assert client.get("/manager/vendor/xterm/xterm.js").status_code == 200
    assert client.get("/components/BullpenTab.js").status_code == 404
    assert client.get("/commands.js").status_code == 404
    assert client.get("/manager/manager.js").status_code == 404
    assert client.get("/manager/index.html").status_code == 404


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
