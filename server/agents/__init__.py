"""Agent adapter registry."""

from server.agents.claude_adapter import ClaudeAdapter
from server.agents.codex_adapter import CodexAdapter
from server.agents.gemini_adapter import GeminiAdapter


_adapters = {
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "gemini": GeminiAdapter(),
}


def get_adapter(name):
    """Get an agent adapter by name."""
    return _adapters.get(name)


def register_adapter(name, adapter):
    """Register a custom adapter (for testing)."""
    _adapters[name] = adapter


def list_adapters():
    """List all registered adapter names."""
    return list(_adapters.keys())
