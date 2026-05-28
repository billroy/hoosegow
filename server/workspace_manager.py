"""Compatibility constants for legacy Bullpen entry points.

Toady no longer uses Bullpen's multi-project workspace manager. The remaining
legacy scripts only import ``GLOBAL_DIR`` to locate shared auth credentials;
the full manager is intentionally gone.
"""

import os


GLOBAL_DIR = os.path.abspath(os.path.expanduser(os.environ.get("TOADY_HOME", "~/.toady")))
REGISTRY_PATH = os.path.join(GLOBAL_DIR, "projects.json")
REGISTRY_VERSION = 1
