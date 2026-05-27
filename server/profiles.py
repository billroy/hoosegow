"""Profile loading and management."""

import os

from server.persistence import ensure_within, read_json, write_json
from server.validation import _id


def _profiles_dir(bp_dir):
    return os.path.join(bp_dir, "profiles")


def list_profiles(bp_dir):
    """List all profiles. Returns list of profile dicts."""
    profiles_dir = _profiles_dir(bp_dir)
    if not os.path.isdir(profiles_dir):
        return []

    profiles = []
    for fname in sorted(os.listdir(profiles_dir)):
        if fname.endswith(".json"):
            path = os.path.join(profiles_dir, fname)
            profiles.append(read_json(path))
    return profiles


def get_profile(bp_dir, profile_id):
    """Get a single profile by ID. Returns dict or None."""
    _id(profile_id, "profile_id")
    path = os.path.join(_profiles_dir(bp_dir), f"{profile_id}.json")
    ensure_within(path, _profiles_dir(bp_dir))
    if not os.path.exists(path):
        return None
    return read_json(path)


def create_profile(bp_dir, data):
    """Create a new profile. Returns the profile dict."""
    profile_id = data.get("id")
    if not profile_id:
        raise ValueError("Profile must have an id")
    _id(profile_id, "profile_id")
    path = os.path.join(_profiles_dir(bp_dir), f"{profile_id}.json")
    ensure_within(path, _profiles_dir(bp_dir))
    write_json(path, data)
    return data
