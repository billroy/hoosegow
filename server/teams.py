"""Team save/load."""

import os

from server.persistence import ensure_within, read_json, write_json
from server.validation import _id
from server.worker_types import copy_worker_slot, normalize_layout


def _teams_dir(bp_dir):
    return os.path.join(bp_dir, "teams")


def save_team(bp_dir, name, layout):
    """Save current layout as a named team. Strips task_queue from slots."""
    config = read_json(os.path.join(bp_dir, "config.json"))
    raw_slots = layout.get("slots", []) if isinstance(layout, dict) and isinstance(layout.get("slots"), list) else []
    layout = normalize_layout(layout, config=config)
    team_layout = {"slots": []}
    for slot in layout.get("slots", []):
        if slot is None:
            team_layout["slots"].append(None)
        else:
            # Copy slot but exclude runtime state
            saved = {
                k: v for k, v in copy_worker_slot(slot, reset_runtime=False).items()
                if k not in ("task_queue", "state")
            }
            team_layout["slots"].append(saved)
    while len(team_layout["slots"]) < len(raw_slots):
        team_layout["slots"].append(None)

    _id(name, "team name")
    path = os.path.join(_teams_dir(bp_dir), f"{name}.json")
    ensure_within(path, _teams_dir(bp_dir))
    write_json(path, team_layout)
    return team_layout


def load_team(bp_dir, name):
    """Load a named team. Returns layout dict or None."""
    _id(name, "team name")
    path = os.path.join(_teams_dir(bp_dir), f"{name}.json")
    ensure_within(path, _teams_dir(bp_dir))
    if not os.path.exists(path):
        return None
    team = read_json(path)
    # Re-add runtime fields
    config = read_json(os.path.join(bp_dir, "config.json"))
    raw_slots = team.get("slots", []) if isinstance(team, dict) and isinstance(team.get("slots"), list) else []
    team = normalize_layout(team, config=config)
    while len(team.get("slots", [])) < len(raw_slots):
        team.setdefault("slots", []).append(None)
    for slot in team.get("slots", []):
        if slot is not None:
            slot.setdefault("task_queue", [])
            slot.setdefault("state", "idle")
            slot["task_queue"] = []
            slot["state"] = "idle"
    return team


def list_teams(bp_dir):
    """List saved team names."""
    teams_dir = _teams_dir(bp_dir)
    if not os.path.isdir(teams_dir):
        return []
    return sorted(
        f[:-5] for f in os.listdir(teams_dir) if f.endswith(".json")
    )
