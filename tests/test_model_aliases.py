"""Tests for provider model alias normalization."""

from server.model_aliases import normalize_model


def test_normalize_legacy_claude_haiku_slug():
    assert normalize_model("claude", "claude-haiku-4-6") == "claude-haiku-4-5-20251001"
    assert normalize_model("claude", "claude-haiku-4-5-20250414") == "claude-haiku-4-5-20251001"


def test_normalize_keeps_unknown_models_unchanged():
    assert normalize_model("claude", "claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert normalize_model("codex", "gpt-5.4") == "gpt-5.4"


def test_normalize_legacy_gemini_models():
    assert normalize_model("gemini", "auto-gemini-2.5") == "flash"
    assert normalize_model("gemini", "gemini-2.0-flash") == "flash"
    assert normalize_model("gemini", "gemini-2.5-flash") == "flash"
    assert normalize_model("gemini", "gemini-2.5-flash-lite") == "flash-lite"
    assert normalize_model("gemini", "gemini-3.5-flash") == "flash"
    assert normalize_model("gemini", "gemini-pro-2.5") == "flash"
    assert normalize_model("gemini", "gemini-2.5-pro") == "flash"
    assert normalize_model("gemini", "gemini-3-pro-preview") == "flash"
    assert normalize_model("gemini", "pro") == "flash"
