"""Tests for server/init.py."""

import json
import os

from server.init import init_workspace, DEFAULT_CONFIG


class TestInitWorkspace:
    def test_creates_structure(self, tmp_workspace):
        bp = init_workspace(tmp_workspace)
        assert os.path.isdir(bp)
        assert os.path.isdir(os.path.join(bp, "tasks"))
        assert os.path.isdir(os.path.join(bp, "profiles"))
        assert os.path.isdir(os.path.join(bp, "teams"))
        assert os.path.isdir(os.path.join(bp, "logs"))

    def test_creates_config(self, tmp_workspace):
        bp = init_workspace(tmp_workspace)
        config_path = os.path.join(bp, "config.json")
        assert os.path.isfile(config_path)
        with open(config_path) as f:
            config = json.load(f)
        assert config == DEFAULT_CONFIG

    def test_creates_layout(self, tmp_workspace):
        bp = init_workspace(tmp_workspace)
        layout_path = os.path.join(bp, "layout.json")
        assert os.path.isfile(layout_path)
        with open(layout_path) as f:
            layout = json.load(f)
        assert layout == {"slots": []}

    def test_creates_prompt_files(self, tmp_workspace):
        bp = init_workspace(tmp_workspace)
        assert os.path.isfile(os.path.join(bp, "workspace_prompt.md"))
        assert not os.path.exists(os.path.join(bp, "bullpen_prompt.md"))

    def test_creates_gitignore(self, tmp_workspace):
        bp = init_workspace(tmp_workspace)
        gitignore = os.path.join(bp, ".gitignore")
        assert os.path.isfile(gitignore)
        assert "logs/" in open(gitignore).read()

    def test_idempotent(self, tmp_workspace):
        bp1 = init_workspace(tmp_workspace)
        # Modify config to verify it's not overwritten
        config_path = os.path.join(bp1, "config.json")
        with open(config_path) as f:
            config = json.load(f)
        config["name"] = "Modified"
        with open(config_path, "w") as f:
            json.dump(config, f)

        bp2 = init_workspace(tmp_workspace)
        assert bp1 == bp2
        with open(config_path) as f:
            config2 = json.load(f)
        assert config2["name"] == "Modified"  # not overwritten

    def test_backfills_required_profiles_in_nonempty_profile_dir(self, tmp_workspace):
        bp = init_workspace(tmp_workspace)
        profiles_dir = os.path.join(bp, "profiles")
        required = os.path.join(profiles_dir, "unconfigured-worker.json")
        assert os.path.exists(required)
        os.remove(required)
        # Keep directory non-empty to simulate legacy workspaces that have
        # custom profiles but predate required defaults.
        assert os.listdir(profiles_dir)

        init_workspace(tmp_workspace)

        assert os.path.exists(required)

    def test_config_has_expected_columns(self, tmp_workspace):
        bp = init_workspace(tmp_workspace)
        with open(os.path.join(bp, "config.json")) as f:
            config = json.load(f)
        keys = [c["key"] for c in config["columns"]]
        assert keys == ["inbox", "assigned", "in_progress", "review", "done", "blocked"]
