"""Shared test fixtures."""

import os
import tempfile
import time

import pytest


@pytest.fixture(autouse=True)
def _isolate_global_registry(tmp_path, monkeypatch):
    """Prevent tests from polluting the user's real Hoosegow state."""
    test_global = str(tmp_path / "bullpen_global")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HOOSEGOW_HOME", test_global)


@pytest.fixture
def tmp_workspace():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory(prefix="bullpen_test_") as d:
        yield d


@pytest.fixture
def tmp_file(tmp_workspace):
    """Return a helper to create a file in the temp workspace."""
    def _make(name, content=""):
        path = os.path.join(tmp_workspace, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path
    return _make
