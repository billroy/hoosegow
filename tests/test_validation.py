"""Tests for event validation and sanitization."""

import pytest
from server.validation import (
    ValidationError, validate_task_create, validate_task_update,
    validate_id, validate_slot, validate_worker_configure,
    validate_payload_size, validate_config_update, validate_worker_move,
    validate_layout_update, validate_team_name,
    MAX_TITLE, MAX_DESCRIPTION,
)


class TestTaskCreateValidation:
    def test_valid_create(self):
        result = validate_task_create({
            "title": "Fix bug",
            "description": "Details here",
            "type": "bug",
            "priority": "high",
            "tags": ["backend", "urgent"],
        })
        assert result["title"] == "Fix bug"
        assert result["type"] == "bug"
        assert result["priority"] == "high"
        assert result["tags"] == ["backend", "urgent"]

    def test_defaults(self):
        result = validate_task_create({})
        assert result["title"] == "Untitled"
        assert result["type"] == "task"
        assert result["priority"] == "normal"
        assert result["tags"] == []

    def test_title_too_long(self):
        with pytest.raises(ValidationError, match="title exceeds max length"):
            validate_task_create({"title": "x" * (MAX_TITLE + 1)})

    def test_description_too_long(self):
        with pytest.raises(ValidationError, match="description exceeds max length"):
            validate_task_create({"description": "x" * (MAX_DESCRIPTION + 1)})

    def test_invalid_type(self):
        with pytest.raises(ValidationError, match="Invalid type"):
            validate_task_create({"type": "invalid"})

    def test_invalid_priority(self):
        with pytest.raises(ValidationError, match="Invalid priority"):
            validate_task_create({"priority": "mega"})

    def test_too_many_tags(self):
        with pytest.raises(ValidationError, match="Too many tags"):
            validate_task_create({"tags": [f"tag{i}" for i in range(25)]})

    def test_tag_too_long(self):
        with pytest.raises(ValidationError, match="Tag too long"):
            validate_task_create({"tags": ["x" * 51]})


class TestTaskUpdateValidation:
    def test_valid_update(self):
        task_id, fields = validate_task_update({"id": "my-task-abc1", "title": "New title"})
        assert task_id == "my-task-abc1"
        assert fields["title"] == "New title"

    def test_missing_id(self):
        with pytest.raises(ValidationError, match="requires id"):
            validate_task_update({"title": "No id"})

    def test_invalid_id(self):
        with pytest.raises(ValidationError, match="Invalid id"):
            validate_task_update({"id": "../../../etc/passwd"})

    def test_strips_unknown_fields(self):
        task_id, fields = validate_task_update({"id": "abc", "title": "Ok", "unknown_field": "bad"})
        assert "unknown_field" not in fields

    def test_body_field_passes_through(self):
        task_id, fields = validate_task_update({"id": "abc", "body": "New description"})
        assert fields["body"] == "New description"

    def test_body_too_long(self):
        with pytest.raises(ValidationError):
            validate_task_update({"id": "abc", "body": "x" * (MAX_DESCRIPTION + 1)})


class TestIdValidation:
    def test_valid_id(self):
        assert validate_id({"id": "task-123_abc"}) == "task-123_abc"

    def test_path_traversal_rejected(self):
        with pytest.raises(ValidationError, match="Invalid id"):
            validate_id({"id": "../../secret"})

    def test_slash_rejected(self):
        with pytest.raises(ValidationError, match="Invalid id"):
            validate_id({"id": "task/bad"})

    def test_missing_id(self):
        with pytest.raises(ValidationError, match="requires id"):
            validate_id({})


class TestSlotValidation:
    def test_valid_slot(self):
        assert validate_slot({"slot": 5}) == 5

    def test_negative_slot(self):
        with pytest.raises(ValidationError, match="must be >= 0"):
            validate_slot({"slot": -1})

    def test_slot_too_large(self):
        with pytest.raises(ValidationError, match="must be <="):
            validate_slot({"slot": 200}, max_slots=100)

    def test_missing_slot(self):
        with pytest.raises(ValidationError, match="requires slot"):
            validate_slot({})


class TestWorkerConfigureValidation:
    def test_valid_configure(self):
        slot, fields = validate_worker_configure({
            "slot": 0,
            "fields": {
                "name": "My Worker",
                "agent": "claude",
                "activation": "on_drop",
                "disposition": "review",
                "max_retries": 3,
            }
        })
        assert slot == 0
        assert fields["name"] == "My Worker"
        assert fields["agent"] == "claude"
        assert fields["max_retries"] == 3

    def test_invalid_agent(self):
        with pytest.raises(ValidationError, match="Invalid agent"):
            validate_worker_configure({"slot": 0, "fields": {"agent": "gpt4"}})

    def test_gemini_agent_allowed(self):
        _, fields = validate_worker_configure({"slot": 0, "fields": {"agent": "gemini"}})
        assert fields["agent"] == "gemini"

    def test_disposition_accepts_any_string(self):
        """Disposition accepts any string (column key or worker: target)."""
        _, fields = validate_worker_configure({"slot": 0, "fields": {"disposition": "review"}})
        assert fields["disposition"] == "review"
        _, fields = validate_worker_configure({"slot": 0, "fields": {"disposition": "worker:My Worker"}})
        assert fields["disposition"] == "worker:My Worker"

    def test_expertise_prompt_too_long(self):
        with pytest.raises(ValidationError, match="expertise_prompt exceeds"):
            validate_worker_configure({"slot": 0, "fields": {"expertise_prompt": "x" * 100_001}})

    def test_trust_mode_accepts_supported_values(self):
        _, fields = validate_worker_configure({"slot": 0, "fields": {"trust_mode": "untrusted"}})
        assert fields["trust_mode"] == "untrusted"

    def test_trust_mode_rejects_unknown_values(self):
        with pytest.raises(ValidationError, match="Invalid trust_mode"):
            validate_worker_configure({"slot": 0, "fields": {"trust_mode": "mystery"}})

    def test_retries_out_of_range(self):
        with pytest.raises(ValidationError, match="must be <="):
            validate_worker_configure({"slot": 0, "fields": {"max_retries": 99}})


class TestPayloadSize:
    def test_oversized_payload_rejected(self):
        with pytest.raises(ValidationError, match="Payload too large"):
            validate_payload_size({"data": "x" * 1_100_000})


class TestConfigUpdate:
    def test_valid_keys_accepted(self):
        result = validate_config_update({"name": "test", "auto_commit": True, "theme": "nord", "workspaceId": "ws1"})
        assert result == {"name": "test", "auto_commit": True, "theme": "nord"}
        assert "workspaceId" not in result

    def test_unknown_key_rejected(self):
        with pytest.raises(ValidationError, match="Unknown config key"):
            validate_config_update({"evil_key": "payload"})

    def test_grid_key_accepted(self):
        result = validate_config_update({"grid": {"rows": 3, "cols": 6}})
        assert "grid" in result

    def test_invalid_theme_rejected(self):
        with pytest.raises(ValidationError, match="Invalid theme"):
            validate_config_update({"theme": "mystery"})

    def test_ambient_fields_accepted(self):
        result = validate_config_update({"ambient_preset": "forest_rain", "ambient_volume": 55})
        assert result == {"ambient_preset": "forest_rain", "ambient_volume": 55}

    def test_ambient_preset_can_be_cleared(self):
        result = validate_config_update({"ambient_preset": ""})
        assert result == {"ambient_preset": None}

    def test_ambient_volume_bounds(self):
        with pytest.raises(ValidationError, match="ambient_volume must be <="):
            validate_config_update({"ambient_volume": 999})

    @pytest.mark.parametrize("theme", [
        "shades-of-purple", "solarized", "panda", "cobalt-2", "one-dark-pro",
        "light-ethereal", "light-stone-teal", "light-ivory-olive", "eyeshade-dark",
    ])
    def test_new_themes_accepted(self, theme):
        result = validate_config_update({"theme": theme})
        assert result == {"theme": theme}


class TestWorkerMove:
    def test_valid_move(self):
        from_s, to_s, to_coord = validate_worker_move({"from": 0, "to": 5})
        assert from_s == 0
        assert to_s == 5
        assert to_coord is None

    def test_valid_coordinate_move(self):
        from_s, to_s, to_coord = validate_worker_move({"from": 0, "to_coord": {"col": -2, "row": 9}})
        assert from_s == 0
        assert to_s is None
        assert to_coord == {"col": -2, "row": 9}

    def test_missing_from(self):
        with pytest.raises(ValidationError, match="requires from"):
            validate_worker_move({"to": 5})

    def test_negative_slot(self):
        with pytest.raises(ValidationError, match="must be >= 0"):
            validate_worker_move({"from": -1, "to": 5})

    def test_slot_too_large(self):
        with pytest.raises(ValidationError, match="must be <="):
            validate_worker_move({"from": 0, "to": 200})


class TestLayoutUpdate:
    def test_valid_grid(self):
        result = validate_layout_update({"grid": {"rows": 3, "cols": 6}})
        assert result == {"rows": 3, "cols": 6}

    def test_no_grid(self):
        assert validate_layout_update({}) is None

    def test_invalid_grid_type(self):
        with pytest.raises(ValidationError, match="grid must be an object"):
            validate_layout_update({"grid": "not-a-dict"})

    def test_grid_rows_out_of_range(self):
        with pytest.raises(ValidationError, match="must be <="):
            validate_layout_update({"grid": {"rows": 99, "cols": 6}})


class TestTeamName:
    def test_valid_name(self):
        assert validate_team_name("my-team") == "my-team"

    def test_empty_name(self):
        with pytest.raises(ValidationError, match="requires team name"):
            validate_team_name("")

    def test_none_name(self):
        with pytest.raises(ValidationError, match="requires team name"):
            validate_team_name(None)

    def test_traversal_name(self):
        with pytest.raises(ValidationError, match="Invalid team name"):
            validate_team_name("../../etc/passwd")
