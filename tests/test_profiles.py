"""Tests for server/profiles.py."""

import os

import pytest

from server.init import init_workspace
from server.profiles import list_profiles, get_profile, create_profile


@pytest.fixture
def bp_dir(tmp_workspace):
    return init_workspace(tmp_workspace)


class TestProfiles:
    def test_list_defaults(self, bp_dir):
        profiles = list_profiles(bp_dir)
        assert len(profiles) == 25
        ids = {p["id"] for p in profiles}
        assert "feature-architect" in ids
        assert "code-reviewer" in ids
        assert "bug-triager" in ids
        assert "unconfigured-worker" in ids

    def test_all_profiles_have_required_fields(self, bp_dir):
        profiles = list_profiles(bp_dir)
        for p in profiles:
            assert "id" in p, f"Missing id in {p}"
            assert "name" in p, f"Missing name in {p.get('id')}"
            assert "default_agent" in p, f"Missing default_agent in {p.get('id')}"
            assert "default_model" in p, f"Missing default_model in {p.get('id')}"
            assert "color_hint" in p, f"Missing color_hint in {p.get('id')}"
            assert "expertise_prompt" in p, f"Missing expertise_prompt in {p.get('id')}"
            assert len(p["expertise_prompt"]) > 50, f"Expertise prompt too short in {p['id']}"

    def test_get_profile(self, bp_dir):
        p = get_profile(bp_dir, "feature-architect")
        assert p is not None
        assert p["name"] == "Feature Architect"

    def test_unconfigured_worker_uses_on_drop_activation(self, bp_dir):
        p = get_profile(bp_dir, "unconfigured-worker")
        assert p is not None
        assert p["default_activation"] == "on_drop"

    def test_get_nonexistent(self, bp_dir):
        assert get_profile(bp_dir, "nonexistent") is None

    def test_create_custom(self, bp_dir):
        data = {
            "id": "custom-worker",
            "name": "Custom Worker",
            "default_agent": "claude",
            "default_model": "sonnet",
            "color_hint": "pink",
            "expertise_prompt": "You are a custom worker that does custom things.",
        }
        result = create_profile(bp_dir, data)
        assert result == data

        # Verify file created
        path = os.path.join(bp_dir, "profiles", "custom-worker.json")
        assert os.path.exists(path)

        # Verify appears in list
        profiles = list_profiles(bp_dir)
        assert len(profiles) == 26
        ids = {p["id"] for p in profiles}
        assert "custom-worker" in ids

    def test_create_without_id_raises(self, bp_dir):
        with pytest.raises(ValueError):
            create_profile(bp_dir, {"name": "No ID"})

    def test_path_traversal_get_rejected(self, bp_dir):
        """Path traversal in profile_id should be rejected."""
        from server.validation import ValidationError
        with pytest.raises(ValidationError, match="Invalid profile_id"):
            get_profile(bp_dir, "../../etc/passwd")

    def test_path_traversal_create_rejected(self, bp_dir):
        """Path traversal in profile create should be rejected."""
        from server.validation import ValidationError
        with pytest.raises(ValidationError, match="Invalid profile_id"):
            create_profile(bp_dir, {"id": "../../../evil", "name": "Evil"})

    def test_profiles_in_state_init(self, bp_dir):
        """Verify profiles are included in state:init."""
        import tempfile
        from server.app import create_app, socketio

        with tempfile.TemporaryDirectory() as ws:
            app = create_app(ws)
            client = socketio.test_client(app)
            received = client.get_received()
            state_init = None
            for evt in received:
                if evt["name"] == "state:init":
                    state_init = evt["args"][0]
            client.disconnect()

            assert state_init is not None
            assert "profiles" in state_init
            assert len(state_init["profiles"]) == 25
