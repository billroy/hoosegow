"""Event payload validation and sanitization."""

import re
import sys

from server.prompt_hardening import VALID_TRUST_MODES

# Field constraints
MAX_TITLE = 500
MAX_DESCRIPTION = 50_000
MAX_TAG_LEN = 50
MAX_TAGS = 20
MAX_EXPERTISE_PROMPT = 100_000
MAX_WORKER_NOTE = 500
MAX_SLUG = 80
MAX_PAYLOAD_SIZE = 1_000_000  # 1MB
MAX_TERMINAL_INPUT = 64 * 1024

VALID_PRIORITIES = {"low", "normal", "high", "urgent"}
VALID_TYPES = {"task", "bug", "feature", "chore"}
VALID_AGENTS = {"claude", "codex", "gemini"}
VALID_WORKER_COLOR_KEYS = {"claude", "codex", "gemini", "shell", "service", "marker"}
VALID_ACTIVATIONS = {"on_drop", "on_queue", "manual", "at_time", "on_interval"}
VALID_DISPOSITIONS = {"review", "done"}
VALID_THEMES = {
    "dark", "light", "dracula", "nord", "gruvbox", "tokyo-night", "catppuccin",
    "github-dark", "monokai", "one-dark", "everforest", "ayu-dark",
    "material-ocean", "night-owl", "shades-of-purple", "solarized", "panda",
    "cobalt-2", "one-dark-pro", "light-ethereal", "light-stone-teal", "light-ivory-olive",
    "eyeshade", "eyeshade-dark",
}

ID_REGEX = re.compile(r'^[a-zA-Z0-9_-]{1,80}$')
SLUG_REGEX = re.compile(r'^[a-zA-Z0-9_-]{1,80}$')
TERMINAL_ID_REGEX = re.compile(r'^[a-zA-Z0-9_-]{1,100}$')


class ValidationError(Exception):
    pass


def validate_payload_size(data):
    """Reject payloads over 1MB (rough estimate)."""
    import json
    size = len(json.dumps(data, default=str))
    if size > MAX_PAYLOAD_SIZE:
        raise ValidationError(f"Payload too large ({size} bytes, max {MAX_PAYLOAD_SIZE})")


def _str(val, max_len, field_name):
    """Validate and truncate a string field."""
    if val is None:
        return ""
    val = str(val)
    if len(val) > max_len:
        raise ValidationError(f"{field_name} exceeds max length ({len(val)} > {max_len})")
    return val


def _enum(val, allowed, field_name, default=None):
    """Validate an enum field."""
    if val is None:
        return default
    val = str(val).lower()
    if val not in allowed:
        raise ValidationError(f"Invalid {field_name}: '{val}'. Must be one of: {', '.join(sorted(allowed))}")
    return val


def _id(val, field_name="id"):
    """Validate an ID/slug field."""
    if val is None:
        return None
    val = str(val)
    if not ID_REGEX.match(val):
        raise ValidationError(f"Invalid {field_name}: must match [a-zA-Z0-9_-]{{1,80}}")
    return val


def _tags(val):
    """Validate tags list."""
    if val is None:
        return []
    if not isinstance(val, list):
        raise ValidationError("tags must be a list")
    if len(val) > MAX_TAGS:
        raise ValidationError(f"Too many tags ({len(val)} > {MAX_TAGS})")
    result = []
    for t in val:
        t = str(t)
        if len(t) > MAX_TAG_LEN:
            raise ValidationError(f"Tag too long ({len(t)} > {MAX_TAG_LEN})")
        result.append(t)
    return result


def _int(val, field_name, min_val=None, max_val=None):
    """Validate an integer field."""
    if val is None:
        return None
    try:
        val = int(val)
    except (ValueError, TypeError):
        raise ValidationError(f"{field_name} must be an integer")
    if min_val is not None and val < min_val:
        raise ValidationError(f"{field_name} must be >= {min_val}")
    if max_val is not None and val > max_val:
        raise ValidationError(f"{field_name} must be <= {max_val}")
    return val


def validate_task_create(data):
    """Validate task:create payload. Returns sanitized data."""
    validate_payload_size(data)
    result = {
        "title": _str(data.get("title", "Untitled"), MAX_TITLE, "title"),
        "description": _str(data.get("description", ""), MAX_DESCRIPTION, "description"),
        "type": _enum(data.get("type"), VALID_TYPES, "type", default="task"),
        "priority": _enum(data.get("priority"), VALID_PRIORITIES, "priority", default="normal"),
        "tags": _tags(data.get("tags")),
    }
    if "status" in data:
        result["status"] = str(data["status"])
    return result


def validate_task_update(data):
    """Validate task:update payload. Returns sanitized data."""
    validate_payload_size(data)
    task_id = _id(data.get("id"), "id")
    if not task_id:
        raise ValidationError("task:update requires id")

    fields = {}
    if "title" in data:
        fields["title"] = _str(data["title"], MAX_TITLE, "title")
    if "description" in data:
        fields["description"] = _str(data["description"], MAX_DESCRIPTION, "description")
    if "type" in data:
        fields["type"] = _enum(data["type"], VALID_TYPES, "type")
    if "priority" in data:
        fields["priority"] = _enum(data["priority"], VALID_PRIORITIES, "priority")
    if "tags" in data:
        fields["tags"] = _tags(data["tags"])
    if "status" in data:
        fields["status"] = str(data["status"])
    if "assigned_to" in data:
        fields["assigned_to"] = data["assigned_to"]
    if "body" in data:
        fields["body"] = _str(data["body"], MAX_DESCRIPTION, "body")

    return task_id, fields


def validate_id(data, field="id"):
    """Validate a simple {id: ...} payload."""
    val = _id(data.get(field), field)
    if not val:
        raise ValidationError(f"requires {field}")
    return val


def validate_terminal_id(data, field="terminalId"):
    """Validate a terminal identifier."""
    val = data.get(field) if isinstance(data, dict) else None
    if val is None:
        raise ValidationError(f"requires {field}")
    val = str(val)
    if not TERMINAL_ID_REGEX.match(val):
        raise ValidationError(f"Invalid {field}: must match [a-zA-Z0-9_-]{{1,100}}")
    return val


def validate_terminal_size(data):
    """Validate terminal dimensions. Returns (cols, rows)."""
    cols = _int((data or {}).get("cols"), "cols", min_val=20, max_val=300)
    rows = _int((data or {}).get("rows"), "rows", min_val=5, max_val=100)
    if cols is None or rows is None:
        raise ValidationError("requires cols and rows")
    return cols, rows


def validate_terminal_input(data):
    """Validate terminal input data."""
    value = (data or {}).get("data")
    if not isinstance(value, str):
        raise ValidationError("terminal input data must be a string")
    if len(value.encode("utf-8", errors="surrogatepass")) > MAX_TERMINAL_INPUT:
        raise ValidationError(f"terminal input exceeds max length ({MAX_TERMINAL_INPUT} bytes)")
    return value


def validate_slot(data, max_slots=100):
    """Validate a slot index."""
    slot = _int(data.get("slot"), "slot", min_val=0, max_val=max_slots - 1)
    if slot is None:
        raise ValidationError("requires slot")
    return slot


def validate_coord(data, field="coord", limit=100000, required=False):
    """Validate a sparse grid coordinate object."""
    coord = data.get(field)
    if coord is None:
        if required:
            raise ValidationError(f"requires {field}")
        return None
    if not isinstance(coord, dict):
        raise ValidationError(f"{field} must be an object")
    col = _int(coord.get("col"), f"{field}.col", min_val=-limit, max_val=limit)
    row = _int(coord.get("row"), f"{field}.row", min_val=-limit, max_val=limit)
    if col is None or row is None:
        raise ValidationError(f"{field} requires col and row")
    return {"col": col, "row": row}


def validate_worker_configure(data, max_slots=100):
    """Validate worker:configure payload. Returns (slot, sanitized_fields)."""
    validate_payload_size(data)
    slot = validate_slot(data, max_slots)
    fields = data.get("fields", {})
    if not isinstance(fields, dict):
        raise ValidationError("fields must be an object")

    sanitized = {}
    consumed = set()
    if "name" in fields:
        sanitized["name"] = _str(fields["name"], MAX_TITLE, "name")
        consumed.add("name")
    if "note" in fields:
        sanitized["note"] = _str(fields["note"], MAX_WORKER_NOTE, "note")
        consumed.add("note")
    if "type" in fields:
        sanitized["type"] = _str(fields["type"], 80, "type").strip() or "ai"
        consumed.add("type")
    if "agent" in fields:
        sanitized["agent"] = _enum(fields["agent"], VALID_AGENTS, "agent")
        consumed.add("agent")
    if "model" in fields:
        sanitized["model"] = _str(fields["model"], 50, "model")
        consumed.add("model")
    if "activation" in fields:
        sanitized["activation"] = _enum(fields["activation"], VALID_ACTIVATIONS, "activation")
        consumed.add("activation")
    if "disposition" in fields:
        sanitized["disposition"] = _str(fields["disposition"], 200, "disposition")
        consumed.add("disposition")
    if "watch_column" in fields:
        sanitized["watch_column"] = fields["watch_column"]
        consumed.add("watch_column")
    if "expertise_prompt" in fields:
        sanitized["expertise_prompt"] = _str(fields["expertise_prompt"], MAX_EXPERTISE_PROMPT, "expertise_prompt")
        consumed.add("expertise_prompt")
    if "trust_mode" in fields:
        sanitized["trust_mode"] = _enum(fields["trust_mode"], VALID_TRUST_MODES, "trust_mode")
        consumed.add("trust_mode")
    if "max_retries" in fields:
        sanitized["max_retries"] = _int(fields["max_retries"], "max_retries", min_val=0, max_val=10)
        consumed.add("max_retries")
    if "use_worktree" in fields:
        sanitized["use_worktree"] = bool(fields["use_worktree"])
        consumed.add("use_worktree")
    if "auto_commit" in fields:
        sanitized["auto_commit"] = bool(fields["auto_commit"])
        consumed.add("auto_commit")
    if "auto_pr" in fields:
        sanitized["auto_pr"] = bool(fields["auto_pr"])
        consumed.add("auto_pr")
    if "trigger_time" in fields:
        val = str(fields["trigger_time"] or "")
        if val and not re.match(r'^\d{2}:\d{2}$', val):
            raise ValidationError("trigger_time must be HH:MM format")
        sanitized["trigger_time"] = val or None
        consumed.add("trigger_time")
    if "trigger_interval_minutes" in fields:
        sanitized["trigger_interval_minutes"] = _int(
            fields["trigger_interval_minutes"], "trigger_interval_minutes", min_val=1, max_val=1440
        )
        consumed.add("trigger_interval_minutes")
    if "trigger_every_day" in fields:
        sanitized["trigger_every_day"] = bool(fields["trigger_every_day"])
        consumed.add("trigger_every_day")
    if "paused" in fields:
        sanitized["paused"] = bool(fields["paused"])
        consumed.add("paused")

    # Type-specific and unknown-type fields are admitted here and canonicalized
    # by the worker type normalization layer. Runtime ownership stays server-only.
    disallowed_runtime = {"task_queue", "state", "started_at"}
    for key, value in fields.items():
        if key in consumed or key in disallowed_runtime:
            continue
        if not isinstance(key, str):
            continue
        sanitized[key] = value

    return slot, sanitized


def validate_grid(data):
    """Validate grid resize data. Returns (rows, cols)."""
    grid = data.get("grid")
    if not grid:
        return None, None
    rows = _int(grid.get("rows"), "rows", min_val=1, max_val=10)
    cols = _int(grid.get("cols"), "cols", min_val=1, max_val=15)
    return rows, cols


# Allowed keys for config:update
VALID_CONFIG_KEYS = {
    "name", "grid", "columns", "agent_timeout_seconds",
    "max_prompt_chars", "auto_commit", "auto_pr", "theme",
    "ambient_preset", "ambient_volume", "provider_colors",
    "worker_automation_paused",
}

HEX_COLOR_REGEX = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')


def _validate_provider_colors(val):
    """Validate provider_colors. None resets to defaults; dict maps agent→hex color."""
    from server.init import DEFAULT_PROVIDER_COLORS
    if val is None:
        return dict(DEFAULT_PROVIDER_COLORS)
    if not isinstance(val, dict):
        raise ValidationError("provider_colors must be an object")
    sanitized = dict(DEFAULT_PROVIDER_COLORS)
    for k, v in val.items():
        if k not in VALID_WORKER_COLOR_KEYS:
            raise ValidationError(f"Unknown key in provider_colors: '{k}'")
        if v is None:
            continue
        if not isinstance(v, str) or not HEX_COLOR_REGEX.match(v):
            raise ValidationError(f"provider_colors['{k}'] must be a hex color (e.g. '#rrggbb')")
        sanitized[k] = v.lower()
    return sanitized


def validate_config_update(data):
    """Validate config:update payload. Returns sanitized dict of allowed keys."""
    validate_payload_size(data)
    sanitized = {}
    for k, v in data.items():
        if k == "workspaceId":
            continue
        if k not in VALID_CONFIG_KEYS:
            raise ValidationError(f"Unknown config key: '{k}'")
        if k == "theme":
            sanitized[k] = _enum(v, VALID_THEMES, "theme")
            continue
        if k == "ambient_volume":
            sanitized[k] = _int(v, "ambient_volume", min_val=0, max_val=100)
            continue
        if k == "ambient_preset":
            if v in (None, ""):
                sanitized[k] = None
            else:
                sanitized[k] = _id(v, "ambient_preset")
            continue
        if k == "provider_colors":
            sanitized[k] = _validate_provider_colors(v)
            continue
        if k == "worker_automation_paused":
            sanitized[k] = bool(v)
            continue
        sanitized[k] = v
    return sanitized


def validate_worker_move(data, max_slots=200):
    """Validate worker:move payload. Returns (from_slot, to_slot)."""
    from_slot = _int(data.get("from"), "from", min_val=0, max_val=max_slots - 1)
    if from_slot is None:
        raise ValidationError("worker:move requires from")
    to_coord = validate_coord(data, "to_coord")
    to_slot = _int(data.get("to"), "to", min_val=0, max_val=max_slots - 1)
    if to_coord is None and to_slot is None:
        raise ValidationError("worker:move requires to or to_coord")
    return from_slot, to_slot, to_coord


def validate_worker_move_group(data, max_slots=200, max_moves=200):
    """Validate worker:move_group payload. Returns sanitized move list."""
    validate_payload_size(data)
    moves = data.get("moves")
    if not isinstance(moves, list) or not moves:
        raise ValidationError("worker:move_group requires non-empty moves")
    if len(moves) > max_moves:
        raise ValidationError(f"worker:move_group moves exceeds max length ({len(moves)} > {max_moves})")

    seen_slots = set()
    seen_coords = set()
    sanitized = []
    for idx, move in enumerate(moves):
        if not isinstance(move, dict):
            raise ValidationError(f"moves[{idx}] must be an object")
        slot = _int(move.get("slot"), f"moves[{idx}].slot", min_val=0, max_val=max_slots - 1)
        if slot is None:
            raise ValidationError(f"moves[{idx}] requires slot")
        to_coord = validate_coord(move, "to_coord", required=True)
        coord_key = (to_coord["col"], to_coord["row"])
        if slot in seen_slots:
            raise ValidationError(f"Duplicate slot in moves: {slot}")
        if coord_key in seen_coords:
            raise ValidationError(
                f"Duplicate target coordinate in moves: ({to_coord['col']}, {to_coord['row']})"
            )
        seen_slots.add(slot)
        seen_coords.add(coord_key)
        sanitized.append({"slot": slot, "to_coord": to_coord})
    return sanitized


def validate_worker_paste_group(data, max_items=200):
    """Validate worker:paste_group payload. Returns sanitized item list."""
    validate_payload_size(data)
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValidationError("worker:paste_group requires non-empty items")
    if len(items) > max_items:
        raise ValidationError(f"worker:paste_group items exceeds max length ({len(items)} > {max_items})")

    seen_coords = set()
    sanitized = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError(f"items[{idx}] must be an object")
        coord = validate_coord(item, "coord", required=True)
        worker = item.get("worker")
        if not isinstance(worker, dict):
            raise ValidationError(f"items[{idx}].worker must be an object")
        coord_key = (coord["col"], coord["row"])
        if coord_key in seen_coords:
            raise ValidationError(
                f"Duplicate target coordinate in items: ({coord['col']}, {coord['row']})"
            )
        seen_coords.add(coord_key)
        sanitized.append({"coord": coord, "worker": worker})
    return sanitized


def validate_layout_update(data):
    """Validate layout:update payload. Returns validated grid dict or None."""
    validate_payload_size(data)
    if "grid" not in data:
        return None
    grid = data["grid"]
    if not isinstance(grid, dict):
        raise ValidationError("grid must be an object")
    rows = _int(grid.get("rows"), "rows", min_val=1, max_val=10)
    cols = _int(grid.get("cols"), "cols", min_val=1, max_val=15)
    result = {}
    if rows is not None:
        result["rows"] = rows
    if cols is not None:
        result["cols"] = cols
    return result


def validate_team_name(name):
    """Validate a team name (used as filename). Returns sanitized name."""
    if not name:
        raise ValidationError("requires team name")
    return _id(name, "team name")
