"""Tests for terminal validation."""

import pytest

from server.validation import (
    ValidationError,
    validate_terminal_id,
    validate_terminal_input,
    validate_terminal_size,
)


def test_terminal_validation_accepts_uuid_and_size():
    assert validate_terminal_id({"terminalId": "123e4567-e89b-12d3-a456-426614174000"})
    assert validate_terminal_size({"cols": 120, "rows": 32}) == (120, 32)
    assert validate_terminal_input({"data": "echo ok\n"}) == "echo ok\n"


@pytest.mark.parametrize("payload", [
    {"terminalId": "../bad"},
    {"terminalId": ""},
    {"terminalId": "x" * 101},
])
def test_terminal_validation_rejects_bad_ids(payload):
    with pytest.raises(ValidationError):
        validate_terminal_id(payload)
