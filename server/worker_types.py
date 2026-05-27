"""Worker type registry and canonical slot helpers."""

from __future__ import annotations

import copy
from dataclasses import dataclass

from server.model_aliases import normalize_model
from server.prompt_hardening import normalize_trust_mode, TRUST_MODE_TRUSTED, TRUST_MODE_UNTRUSTED


VALID_WORKER_TYPES = {"ai", "shell", "service", "marker", "eval"}
RUNTIME_FIELDS = {"task_queue", "state", "started_at"}
SERVICE_TICKET_ACTIONS = {"start-if-stopped-else-restart", "restart", "start-if-stopped"}
SERVICE_HEALTH_TYPES = {"none", "http", "shell"}
SERVICE_CRASH_POLICIES = {"stay-crashed"}
SERVICE_COMMAND_SOURCES = {"manual", "procfile"}
SERVICE_LOG_MAX_BYTES_DEFAULT = 5 * 1024 * 1024


@dataclass(frozen=True)
class ViewerContext:
    can_edit: bool = True


class WorkerType:
    type_id = "unknown"

    def validate_config(self, slot):
        return []

    def default_icon(self):
        return "bot"

    def default_color(self):
        return None

    def runnable(self):
        return True


class AIWorkerType(WorkerType):
    type_id = "ai"

    def default_icon(self):
        return "bot"


class ShellWorkerType(WorkerType):
    type_id = "shell"

    def validate_config(self, slot):
        if not str(slot.get("command") or "").strip():
            return ["Shell workers require a command."]
        return []

    def default_icon(self):
        return "terminal"

    def default_color(self):
        return "neutral"


class ServiceWorkerType(WorkerType):
    type_id = "service"

    def validate_config(self, slot):
        errors = []
        command_source = str(slot.get("command_source") or "manual")
        if command_source not in SERVICE_COMMAND_SOURCES:
            errors.append("Service workers require a valid command source.")
        if command_source != "procfile" and not str(slot.get("command") or "").strip():
            errors.append("Service workers require a command.")
        for item in slot.get("env") or []:
            key = str((item or {}).get("key") or "").strip()
            if key == "BULLPEN_MCP_TOKEN" or key.startswith("BULLPEN_"):
                errors.append(f"{key} cannot be configured for Service workers.")
        port = slot.get("port")
        if port not in (None, ""):
            try:
                port_num = int(port)
            except (TypeError, ValueError):
                errors.append("Service worker port must be an integer.")
            else:
                if port_num < 1 or port_num > 65535:
                    errors.append("Service worker port must be between 1 and 65535.")
        health_type = str(slot.get("health_type") or "none")
        if health_type == "http" and not str(slot.get("health_url") or "").strip():
            errors.append("HTTP health checks require a URL.")
        if health_type == "shell" and not str(slot.get("health_command") or "").strip():
            errors.append("Shell health checks require a command.")
        return errors

    def default_icon(self):
        return "server-cog"

    def default_color(self):
        return "service"

    def runnable(self):
        return True


class MarkerWorkerType(WorkerType):
    type_id = "marker"

    def validate_config(self, slot):
        if not str(slot.get("name") or "").strip():
            return ["Marker workers require a name."]
        return []

    def default_icon(self):
        return "square-dot"

    def default_color(self):
        return "marker"

    def runnable(self):
        return True


class EvalWorkerType(WorkerType):
    type_id = "eval"

    def validate_config(self, slot):
        return ["Eval workers are not yet implemented."]

    def default_icon(self):
        return "flask-conical"

    def runnable(self):
        return False


class UnknownWorkerType(WorkerType):
    def __init__(self, type_id):
        self.type_id = type_id

    def validate_config(self, slot):
        return [f"Worker type not installed: {self.type_id}"]

    def default_icon(self):
        return "circle-help"

    def runnable(self):
        return False


WORKER_TYPES = {
    "ai": AIWorkerType(),
    "shell": ShellWorkerType(),
    "service": ServiceWorkerType(),
    "marker": MarkerWorkerType(),
    "eval": EvalWorkerType(),
}


def get_worker_type(type_id):
    type_id = str(type_id or "ai").strip() or "ai"
    return WORKER_TYPES.get(type_id, UnknownWorkerType(type_id))


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _default_cols(config):
    grid = config.get("grid", {}) if isinstance(config, dict) else {}
    cols = _safe_int(grid.get("cols", 4), 4)
    return cols if cols > 0 else 4


def _default_coord(index, config):
    cols = _default_cols(config)
    return index % cols, index // cols


def _normalize_env(env):
    if not isinstance(env, list):
        return []
    normalized = []
    for item in env:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        normalized.append({"key": key, "value": str(item.get("value") or "")})
    return normalized


def _normalize_service_port(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _trim_trailing_empty_slots(slots):
    """Drop trailing empty entries while preserving intentional interior holes."""
    trimmed = list(slots)
    while trimmed and trimmed[-1] is None:
        trimmed.pop()
    return trimmed


def normalize_worker_slot(raw, *, index, config):
    """Return a canonical worker slot, preserving unknown fields."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None

    slot = copy.deepcopy(raw)
    type_id = slot.get("type")
    if not isinstance(type_id, str) or not type_id.strip():
        type_id = "ai"
    else:
        type_id = type_id.strip()
    slot["type"] = type_id

    default_col, default_row = _default_coord(index, config)
    slot["row"] = _safe_int(slot.get("row"), default_row)
    slot["col"] = _safe_int(slot.get("col"), default_col)
    slot["name"] = str(slot.get("name") or "Worker")
    default_activation = "manual" if type_id == "service" else "on_drop"
    slot["activation"] = str(slot.get("activation") or default_activation)
    if type_id == "marker" and "disposition" in raw:
        slot["disposition"] = str(raw.get("disposition") or "")
    else:
        slot["disposition"] = str(slot.get("disposition") or "review")
    slot.setdefault("watch_column", None)
    slot["max_retries"] = max(0, _safe_int(slot.get("max_retries"), 1))
    slot.setdefault("trigger_time", None)
    slot.setdefault("trigger_interval_minutes", None)
    slot["trigger_every_day"] = bool(slot.get("trigger_every_day", False))
    slot.setdefault("last_trigger_time", None)
    slot["paused"] = bool(slot.get("paused", False))
    if not isinstance(slot.get("task_queue"), list):
        slot["task_queue"] = []
    slot["state"] = str(slot.get("state") or "idle")

    if type_id == "ai":
        slot["agent"] = str(slot.get("agent") or "claude")
        slot["model"] = normalize_model(slot["agent"], slot.get("model") or "claude-sonnet-4-6")
        slot["expertise_prompt"] = str(slot.get("expertise_prompt") or "")
        slot["trust_mode"] = normalize_trust_mode(slot.get("trust_mode"), default=TRUST_MODE_TRUSTED)
        slot["use_worktree"] = bool(slot.get("use_worktree", False))
        slot["auto_commit"] = bool(slot.get("auto_commit", False))
        slot["auto_pr"] = bool(slot.get("auto_pr", False))
        if slot["trust_mode"] == TRUST_MODE_UNTRUSTED:
            slot["auto_commit"] = False
            slot["auto_pr"] = False
    elif type_id == "shell":
        slot["command"] = str(slot.get("command") or "")
        slot["cwd"] = str(slot.get("cwd") or "")
        slot["timeout_seconds"] = max(1, min(_safe_int(slot.get("timeout_seconds"), 60), 600))
        delivery = str(slot.get("ticket_delivery") or "stdin-json")
        if delivery not in ("stdin-json", "env-vars", "argv-json"):
            delivery = "stdin-json"
        slot["ticket_delivery"] = delivery
        slot["env"] = _normalize_env(slot.get("env"))
    elif type_id == "service":
        command_source = str(slot.get("command_source") or "manual")
        if command_source not in SERVICE_COMMAND_SOURCES:
            command_source = "manual"
        slot["command"] = str(slot.get("command") or "")
        slot["command_source"] = command_source
        slot["procfile_process"] = str(slot.get("procfile_process") or "web").strip() or "web"
        slot["port"] = _normalize_service_port(slot.get("port"))
        slot["cwd"] = str(slot.get("cwd") or "")
        slot["pre_start"] = str(slot.get("pre_start") or "")
        action = str(slot.get("ticket_action") or "start-if-stopped-else-restart")
        if action not in SERVICE_TICKET_ACTIONS:
            action = "start-if-stopped-else-restart"
        slot["ticket_action"] = action
        slot["startup_grace_seconds"] = max(0, min(_safe_int(slot.get("startup_grace_seconds"), 2), 3600))
        slot["startup_timeout_seconds"] = max(1, min(_safe_int(slot.get("startup_timeout_seconds"), 60), 86400))
        health_type = str(slot.get("health_type") or "none")
        if health_type not in SERVICE_HEALTH_TYPES:
            health_type = "none"
        slot["health_type"] = health_type
        slot["health_url"] = str(slot.get("health_url") or "")
        slot["health_command"] = str(slot.get("health_command") or "")
        slot["health_interval_seconds"] = max(1, min(_safe_int(slot.get("health_interval_seconds"), 5), 3600))
        slot["health_timeout_seconds"] = max(1, min(_safe_int(slot.get("health_timeout_seconds"), 2), 3600))
        slot["health_failure_threshold"] = max(1, min(_safe_int(slot.get("health_failure_threshold"), 3), 100))
        on_crash = str(slot.get("on_crash") or "stay-crashed")
        if on_crash not in SERVICE_CRASH_POLICIES:
            on_crash = "stay-crashed"
        slot["on_crash"] = on_crash
        slot["stop_timeout_seconds"] = max(0, min(_safe_int(slot.get("stop_timeout_seconds"), 5), 3600))
        slot["log_max_bytes"] = max(1024, min(_safe_int(slot.get("log_max_bytes"), SERVICE_LOG_MAX_BYTES_DEFAULT), 1024 * 1024 * 1024))
        slot["env"] = _normalize_env(slot.get("env"))
    elif type_id == "marker":
        slot["note"] = str(slot.get("note") or "")
        slot["icon"] = "square-dot"
        slot["color"] = str(slot.get("color") or "marker")
        slot["max_retries"] = 0

    return slot


def normalize_layout(layout, *, config):
    """Normalize every slot in a layout object."""
    if not isinstance(layout, dict):
        layout = {}
    normalized = dict(layout)
    raw_slots = normalized.get("slots", [])
    if not isinstance(raw_slots, list):
        raw_slots = []
    normalized_slots = [
        normalize_worker_slot(slot, index=i, config=config)
        for i, slot in enumerate(raw_slots)
    ]
    normalized["slots"] = _trim_trailing_empty_slots(normalized_slots)
    return normalized


def serialize_worker_slot(slot, *, viewer):
    """Serialize a slot for a client, redacting sensitive fields if needed."""
    if slot is None:
        return None
    out = copy.deepcopy(slot)
    if out.get("type") in ("shell", "service") and not viewer.can_edit:
        for key in ("command", "pre_start", "health_command"):
            if key in out:
                out[key] = "<redacted>"
        if isinstance(out.get("env"), list):
            redacted = []
            for item in out["env"]:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "")
                if key:
                    redacted.append({"key": key, "value": "<redacted>"})
            out["env"] = redacted
    return out


def serialize_layout(layout, *, viewer, config=None):
    if config is not None:
        layout = normalize_layout(layout, config=config)
    serialized = dict(layout if isinstance(layout, dict) else {})
    slots = serialized.get("slots", [])
    if not isinstance(slots, list):
        slots = []
    serialized["slots"] = [serialize_worker_slot(slot, viewer=viewer) for slot in slots]
    return serialized


def copy_worker_slot(slot, *, reset_runtime):
    """Copy a worker slot, optionally resetting runtime-only fields."""
    copied = copy.deepcopy(slot)
    if reset_runtime:
        copied["task_queue"] = []
        copied["state"] = "idle"
        copied["last_trigger_time"] = None
        copied["paused"] = False
        copied.pop("started_at", None)
    return copied
