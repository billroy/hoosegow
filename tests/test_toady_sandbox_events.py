from server.app import create_app, socketio


def test_socket_create_allows_shared_workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr("server.sandboxes.host_port_in_use", lambda _port: False)
    workspace_root = tmp_path / "work"
    workspace_root.mkdir()
    app = create_app(
        str(tmp_path),
        no_browser=True,
        global_dir=str(tmp_path / "state"),
        start_without_project=True,
    )
    service = app.config["toady_sandboxes"]
    service.browse_roots = [str(tmp_path)]
    service.port_pool = (63100, 63105)
    client = socketio.test_client(app)
    client.get_received()

    first = client.emit(
        "sandbox:create",
        {"name": "sandbox", "workspace_root": str(workspace_root)},
        callback=True,
    )
    manifest = service.store.get("sandbox")
    manifest.last_status = "running"
    service.store.save(manifest)
    second = client.emit(
        "sandbox:create",
        {"name": "sandbox-2", "workspace_root": str(workspace_root)},
        callback=True,
    )

    client.disconnect()
    assert first["ok"] is True
    assert second["ok"] is True
    assert first["sandbox"]["canonical_workspace_path"] == second["sandbox"]["canonical_workspace_path"]
