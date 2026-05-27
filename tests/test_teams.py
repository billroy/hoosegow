"""Tests for server/teams.py."""

import pytest

from server.init import init_workspace
from server.teams import save_team, load_team, list_teams


@pytest.fixture
def bp_dir(tmp_workspace):
    return init_workspace(tmp_workspace)


class TestTeams:
    def _sample_layout(self):
        return {
            "slots": [
                {
                    "row": 0, "col": 0,
                    "profile": "feature-architect",
                    "name": "Feature Architect",
                    "agent": "claude",
                    "model": "sonnet",
                    "activation": "on_drop",
                    "disposition": "review",
                    "watch_column": None,
                    "expertise_prompt": "You are...",
                    "max_retries": 1,
                    "task_queue": ["task-abc-1234"],
                    "state": "working",
                },
                None,
            ]
        }

    def test_save_and_load(self, bp_dir):
        layout = self._sample_layout()
        save_team(bp_dir, "my-team", layout)

        loaded = load_team(bp_dir, "my-team")
        assert loaded is not None
        assert loaded["slots"][0]["profile"] == "feature-architect"
        assert loaded["slots"][1] is None

    def test_save_strips_task_queue(self, bp_dir):
        layout = self._sample_layout()
        save_team(bp_dir, "test", layout)
        loaded = load_team(bp_dir, "test")

        # task_queue should be empty (stripped on save, restored as [])
        assert loaded["slots"][0]["task_queue"] == []
        # state should be reset to idle
        assert loaded["slots"][0]["state"] == "idle"

    def test_list_teams(self, bp_dir):
        assert list_teams(bp_dir) == []

        save_team(bp_dir, "alpha", {"slots": []})
        save_team(bp_dir, "beta", {"slots": []})

        teams = list_teams(bp_dir)
        assert teams == ["alpha", "beta"]

    def test_load_nonexistent(self, bp_dir):
        assert load_team(bp_dir, "nope") is None

    def test_path_traversal_save_rejected(self, bp_dir):
        """Path traversal in team name should be rejected on save."""
        from server.validation import ValidationError
        with pytest.raises(ValidationError, match="Invalid team name"):
            save_team(bp_dir, "../../etc/evil", {"slots": []})

    def test_path_traversal_load_rejected(self, bp_dir):
        """Path traversal in team name should be rejected on load."""
        from server.validation import ValidationError
        with pytest.raises(ValidationError, match="Invalid team name"):
            load_team(bp_dir, "../../../passwd")
