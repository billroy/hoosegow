"""Cross-workspace worker transfer (copy/move)."""

import os

from server.locks import write_lock
from server.persistence import read_json, write_json
from server.profiles import get_profile, create_profile
from server.worker_types import copy_worker_slot, normalize_layout

_AI_TRANSFER_FIELDS = {
    "type", "profile", "name", "agent", "model", "activation", "disposition",
    "watch_column", "expertise_prompt", "trust_mode", "max_retries", "use_worktree",
    "auto_commit", "auto_pr", "trigger_time", "trigger_interval_minutes",
    "trigger_every_day", "color", "avatar",
}
_MAX_TRANSFER_SLOT = 200


class TransferError(Exception):
    """Raised when a transfer cannot proceed."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _safe_cols(config):
    grid = config.get("grid", {}) if isinstance(config, dict) else {}
    try:
        cols = int(grid.get("cols", 4))
    except (TypeError, ValueError):
        cols = 4
    return cols if cols > 0 else 4


def _slot_coord(worker, index, cols):
    if isinstance(worker, dict) and "col" in worker and "row" in worker:
        try:
            return int(worker.get("col", 0)), int(worker.get("row", 0))
        except (TypeError, ValueError):
            pass
    return index % cols, index // cols


def _coord_occupied(slots, coord, cols):
    for i, worker in enumerate(slots):
        if not worker:
            continue
        col, row = _slot_coord(worker, i, cols)
        if col == coord["col"] and row == coord["row"]:
            return True
    return False


def _nearest_empty_coord(slots, start_col, start_row, cols):
    col = int(start_col)
    row = int(start_row)
    while _coord_occupied(slots, {"col": col, "row": row}, cols):
        col += 1
    return {"col": col, "row": row}


def _first_empty_slot(slots):
    for i, worker in enumerate(slots):
        if worker is None:
            return i
    slots.append(None)
    return len(slots) - 1


def transfer_worker(manager, source_workspace_id, source_slot, dest_workspace_id,
                    dest_slot, mode, copy_profile=False):
    """Copy or move a worker between workspaces.

    Returns dict with ``ok``, ``dest_slot``, ``profile_copied``, and ``warnings``.
    Raises ``TransferError`` on validation failure.
    """
    if source_workspace_id == dest_workspace_id:
        raise TransferError("use duplicate for same-workspace copy", 400)

    if mode not in ("copy", "move"):
        raise TransferError("mode must be 'copy' or 'move'", 400)

    # Resolve workspaces — activate from registry if not already loaded
    src_ws = manager.get_or_activate(source_workspace_id)
    if src_ws is None:
        raise TransferError("source workspace not found", 404)

    dst_ws = manager.get_or_activate(dest_workspace_id)
    if dst_ws is None:
        raise TransferError("destination workspace not found", 404)

    warnings = []

    with write_lock:
        # Load source layout
        src_config = read_json(os.path.join(src_ws.bp_dir, "config.json"))
        src_layout = normalize_layout(
            read_json(os.path.join(src_ws.bp_dir, "layout.json")),
            config=src_config,
        )
        src_slots = src_layout.get("slots", [])

        # Validate source slot
        if source_slot is None or source_slot < 0 or source_slot >= len(src_slots):
            raise TransferError("source slot is empty", 400)

        source_worker = src_slots[source_slot]
        if source_worker is None:
            raise TransferError("source slot is empty", 400)

        # Busy worker cannot be moved
        if mode == "move" and source_worker.get("state") != "idle":
            raise TransferError(
                "worker is busy; copy it instead or wait for it to finish", 409)

        # Load destination layout and config
        dst_config = read_json(os.path.join(dst_ws.bp_dir, "config.json"))
        dst_layout = normalize_layout(
            read_json(os.path.join(dst_ws.bp_dir, "layout.json")),
            config=dst_config,
        )
        dst_slots = dst_layout.get("slots", [])
        src_cols = _safe_cols(src_config)
        dst_cols = _safe_cols(dst_config)
        dst_layout["slots"] = dst_slots

        # Resolve destination slot
        if dest_slot is not None:
            if dest_slot < 0 or dest_slot >= _MAX_TRANSFER_SLOT:
                raise TransferError("destination slot is out of range", 400)
            while len(dst_slots) <= dest_slot:
                dst_slots.append(None)
            if dst_slots[dest_slot] is not None:
                raise TransferError("destination slot is occupied", 409)
            target_slot = dest_slot
            target_coord = {"col": dest_slot % dst_cols, "row": dest_slot // dst_cols}
            if _coord_occupied(dst_slots, target_coord, dst_cols):
                raise TransferError("destination coordinate is occupied", 409)
        else:
            target_slot = _first_empty_slot(dst_slots)
            source_col, source_row = _slot_coord(source_worker, source_slot, src_cols)
            target_coord = _nearest_empty_coord(dst_slots, source_col, source_row, dst_cols)

        # Generate unique name in destination
        existing_names = {s.get("name") for s in dst_slots if s and s.get("name")}
        candidate = source_worker.get("name") or "Worker"
        if candidate in existing_names:
            base = candidate
            suffix = 2
            candidate = f"{base} copy"
            while candidate in existing_names:
                candidate = f"{base} copy {suffix}"
                suffix += 1

        # Build the cloned worker with runtime fields reset while preserving
        # type-specific fields for shell and soft-open unknown worker types.
        if str(source_worker.get("type") or "ai") == "ai":
            clone_source = {k: v for k, v in source_worker.items() if k in _AI_TRANSFER_FIELDS}
        else:
            clone_source = source_worker
        clone = copy_worker_slot(clone_source, reset_runtime=True)
        clone["row"] = target_coord["row"]
        clone["col"] = target_coord["col"]
        clone["name"] = candidate

        # --- Warnings for workspace-local references ---

        # disposition: worker:<name> may not resolve in destination
        disposition = clone.get("disposition", "")
        if (disposition.startswith("worker:")
                or disposition.startswith("pass:")
                or disposition.startswith("random:")):
            warnings.append(
                f"disposition '{disposition}' references a workspace-local "
                f"target and may not resolve in the destination"
            )
        elif disposition:
            dst_col_keys = {c["key"] for c in dst_config.get("columns", [])}
            if disposition not in dst_col_keys:
                warnings.append(
                    f"disposition '{disposition}' does not exist in destination workspace"
                )

        # watch_column: check if destination has the column
        watch_col = clone.get("watch_column")
        if watch_col:
            dst_col_keys = {c["key"] for c in dst_config.get("columns", [])}
            if watch_col not in dst_col_keys:
                warnings.append(
                    f"watch_column '{watch_col}' does not exist in destination workspace"
                )

        # --- Profile handling ---
        profile_copied = False
        profile_id = clone.get("profile")
        if profile_id and copy_profile:
            src_profile = get_profile(src_ws.bp_dir, profile_id)
            if src_profile:
                dst_profile = get_profile(dst_ws.bp_dir, profile_id)
                if dst_profile is None:
                    # Copy profile to destination (strip workspaceId if present)
                    profile_data = dict(src_profile)
                    profile_data.pop("workspaceId", None)
                    create_profile(dst_ws.bp_dir, profile_data)
                    profile_copied = True
                else:
                    warnings.append(
                        f"profile '{profile_id}' already exists in destination; skipped"
                    )
            else:
                warnings.append(
                    f"profile '{profile_id}' not found in source workspace"
                )
        elif profile_id and not copy_profile:
            # Check if profile exists in destination; warn if not
            src_profile = get_profile(src_ws.bp_dir, profile_id)
            if src_profile:
                dst_profile = get_profile(dst_ws.bp_dir, profile_id)
                if dst_profile is None:
                    warnings.append(
                        f"profile '{profile_id}' does not exist in destination "
                        f"and was not copied"
                    )

        # --- Write destination first (atomic move safety) ---
        dst_slots[target_slot] = clone
        write_json(os.path.join(dst_ws.bp_dir, "layout.json"), normalize_layout(dst_layout, config=dst_config))

        # --- Clear source on move ---
        if mode == "move":
            src_slots[source_slot] = None
            write_json(os.path.join(src_ws.bp_dir, "layout.json"), normalize_layout(src_layout, config=src_config))

    return {
        "ok": True,
        "dest_slot": target_slot,
        "profile_copied": profile_copied,
        "warnings": warnings,
    }
