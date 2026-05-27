import os

from server.app import sync_deploy_label_config
from server.init import init_workspace
from server.persistence import read_json


def test_sync_deploy_label_persists_env_label(tmp_workspace, monkeypatch):
    bp_dir = init_workspace(tmp_workspace)
    monkeypatch.setenv("BULLPEN_DEPLOY_LABEL", "(Docker:bullpen)")

    sync_deploy_label_config(bp_dir)

    assert read_json(os.path.join(bp_dir, "config.json"))["deploy_label"] == "(Docker:bullpen)"


def test_sync_deploy_label_clears_stale_label_for_non_deploy_runs(tmp_workspace, monkeypatch):
    bp_dir = init_workspace(tmp_workspace)
    monkeypatch.setenv("BULLPEN_DEPLOY_LABEL", "(Microsandbox:bullpen-3)")
    sync_deploy_label_config(bp_dir)
    monkeypatch.delenv("BULLPEN_DEPLOY_LABEL")

    sync_deploy_label_config(bp_dir)

    assert "deploy_label" not in read_json(os.path.join(bp_dir, "config.json"))
