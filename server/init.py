"""First-time copied Bullpen workspace initialization.

This module is retained only for the temporary excavation baseline. Toady
sandbox manifests will replace per-project `.bullpen/` state.
"""

import os
import shutil

from server.persistence import write_json, atomic_write


DEFAULT_PROVIDER_COLORS = {
    "claude": "#da7756",
    "codex": "#5b6fd6",
    "gemini": "#3c7bf4",
    "shell": "#64748b",
    "service": "#0f766e",
    "marker": "#c8b38c",
}

DEFAULT_CONFIG = {
    "name": "Toady",
    "theme": "dark",
    "ambient_preset": None,
    "ambient_volume": 40,
    "provider_colors": dict(DEFAULT_PROVIDER_COLORS),
    "worker_automation_paused": False,
    "grid": {"layout": "medium", "columnWidth": 220, "viewportOrigin": {"col": 0, "row": 0}},
    "columns": [
        {"key": "inbox", "label": "Inbox", "color": "#6B7280"},
        {"key": "assigned", "label": "Assigned", "color": "#3B82F6"},
        {"key": "in_progress", "label": "In Progress", "color": "#8B5CF6"},
        {"key": "review", "label": "Review", "color": "#F59E0B"},
        {"key": "done", "label": "Done", "color": "#10B981"},
        {"key": "blocked", "label": "Blocked", "color": "#EF4444"},
    ],
    "agent_timeout_seconds": 600,
    "max_prompt_chars": 100000,
}

DEFAULT_LAYOUT = {"slots": []}
REQUIRED_DEFAULT_PROFILES = ("unconfigured-worker.json",)


def _default_profiles_dir():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "profiles")


def _copy_profile_if_missing(src_dir, dst_dir, filename):
    src = os.path.join(src_dir, filename)
    dst = os.path.join(dst_dir, filename)
    if os.path.exists(dst) or not os.path.isfile(src):
        return False
    shutil.copy2(src, dst)
    return True


def init_workspace(workspace):
    """Create .bullpen/ directory structure. Idempotent."""
    bp = os.path.join(workspace, ".bullpen")

    # Create directories
    for d in ["tasks", "profiles", "teams", "logs"]:
        os.makedirs(os.path.join(bp, d), exist_ok=True)

    # .gitignore for logs
    gitignore_path = os.path.join(bp, ".gitignore")
    if not os.path.exists(gitignore_path):
        atomic_write(gitignore_path, "logs/\n")

    # config.json — only create if missing
    config_path = os.path.join(bp, "config.json")
    if not os.path.exists(config_path):
        write_json(config_path, DEFAULT_CONFIG)

    # layout.json — only create if missing
    layout_path = os.path.join(bp, "layout.json")
    if not os.path.exists(layout_path):
        write_json(layout_path, DEFAULT_LAYOUT)

    # Workspace prompt — only create if missing
    prompt_path = os.path.join(bp, "workspace_prompt.md")
    if not os.path.exists(prompt_path):
        atomic_write(prompt_path, "")

    # Copy default profiles if profiles dir is empty.
    # If this is an older workspace with custom/partial profiles, backfill
    # required defaults introduced by newer Bullpen versions.
    profiles_dir = os.path.join(bp, "profiles")
    src_profiles = _default_profiles_dir()
    if os.path.isdir(src_profiles):
        if not os.listdir(profiles_dir):
            for f in os.listdir(src_profiles):
                if f.endswith(".json"):
                    shutil.copy2(os.path.join(src_profiles, f), profiles_dir)
        else:
            for filename in REQUIRED_DEFAULT_PROFILES:
                _copy_profile_if_missing(src_profiles, profiles_dir, filename)

    return bp
