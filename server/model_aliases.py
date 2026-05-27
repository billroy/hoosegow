"""Model alias normalization for provider/model selections."""


_MODEL_ALIASES = {
    "claude": {
        # Legacy/non-existent slug kept in older layouts and docs.
        "claude-haiku-4-6": "claude-haiku-4-5-20251001",
        "claude-haiku-4-5-20250414": "claude-haiku-4-5-20251001",
    },
    "gemini": {
        # Gemini CLI 0.37.x routes through 2.5/3-era models. Older Bullpen
        # dropdowns exposed API-only, concrete, or stale slugs that the Gemini
        # CLI path rejects or routes less reliably than aliases. Route them to
        # the CLI's Flash aliases, which have been the most reliable headless
        # options in local probes.
        "auto-gemini-2.5": "flash",
        "gemini-2.0-flash": "flash",
        "gemini-2.5-flash": "flash",
        "gemini-2.5-flash-lite": "flash-lite",
        "gemini-3.5-flash": "flash",
        "gemini-pro-2.5": "flash",
        "gemini-2.5-pro": "flash",
        "gemini-3-pro-preview": "flash",
        "pro": "flash",
    },
}


def normalize_model(provider, model):
    """Return a canonical model slug for the provider."""
    if not provider or not model:
        return model
    aliases = _MODEL_ALIASES.get(str(provider).lower(), {})
    return aliases.get(model, model)
