"""Tests for cross-workspace worker transfer."""

import os
import json
from unittest.mock import patch

import pytest

from server.init import init_workspace
from server.persistence import read_json, write_json
from server.profiles import create_profile, get_profile
from server.transfer import transfer_worker, TransferError
from server.workspace_manager import WorkspaceManager


@pytest.fixture
def two_workspaces(tmp_path):
    """Create two initialized workspaces with a shared WorkspaceManager."""
    ws_a = str(tmp_path / "project_a")
    ws_b = str(tmp_path / "project_b")
    os.makedirs(ws_a)
    os.makedirs(ws_b)

    manager = WorkspaceManager(global_dir=str(tmp_path / "global"))
    id_a = manager.register_project(ws_a, name="Project A")
    id_b = manager.register_project(ws_b, name="Project B")

    return manager, id_a, id_b


def _make_worker(name="TestWorker", **overrides):
    """Return a minimal worker dict."""
    w = {
        "row": 0, "col": 0,
        "name": name,
        "profile": None,
        "agent": "claude",
        "model": "claude-sonnet-4-6",
        "activation": "on_drop",
        "disposition": "review",
        "watch_column": None,
        "expertise_prompt": "You are a test worker.",
        "max_retries": 1,
        "use_worktree": False,
        "auto_commit": False,
        "auto_pr": False,
        "trigger_time": None,
        "trigger_interval_minutes": None,
        "trigger_every_day": False,
        "last_trigger_time": None,
        "paused": False,
        "task_queue": [],
        "state": "idle",
    }
    w.update(overrides)
    return w


def _set_worker(bp_dir, slot_index, worker):
    """Place a worker into a layout slot."""
    layout = read_json(os.path.join(bp_dir, "layout.json"))
    while len(layout["slots"]) <= slot_index:
        layout["slots"].append(None)
    layout["slots"][slot_index] = worker
    write_json(os.path.join(bp_dir, "layout.json"), layout)


class TestTransferCopyBasic:
    def test_copy_basic(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        _set_worker(bp_a, 0, _make_worker("Alpha"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

        assert result["ok"] is True
        assert isinstance(result["dest_slot"], int)

        # Source unchanged
        src_layout = read_json(os.path.join(bp_a, "layout.json"))
        assert src_layout["slots"][0] is not None
        assert src_layout["slots"][0]["name"] == "Alpha"

        # Destination has clone with runtime fields reset
        bp_b = manager.get_bp_dir(id_b)
        dst_layout = read_json(os.path.join(bp_b, "layout.json"))
        clone = dst_layout["slots"][result["dest_slot"]]
        assert clone["name"] == "Alpha"
        assert clone["state"] == "idle"
        assert clone["task_queue"] == []
        assert clone["last_trigger_time"] is None
        assert clone["paused"] is False
        assert clone["expertise_prompt"] == "You are a test worker."


class TestTransferMoveBasic:
    def test_move_basic(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        _set_worker(bp_a, 0, _make_worker("Alpha"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "move")

        assert result["ok"] is True

        # Source slot cleared
        src_layout = read_json(os.path.join(bp_a, "layout.json"))
        assert len(src_layout["slots"]) == 0 or src_layout["slots"][0] is None

        # Destination has clone
        bp_b = manager.get_bp_dir(id_b)
        dst_layout = read_json(os.path.join(bp_b, "layout.json"))
        clone = dst_layout["slots"][result["dest_slot"]]
        assert clone["name"] == "Alpha"


class TestTransferAutoSlot:
    def test_auto_slot(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        bp_b = manager.get_bp_dir(id_b)

        _set_worker(bp_a, 0, _make_worker("Alpha"))

        # Fill slot 0 in destination
        _set_worker(bp_b, 0, _make_worker("Existing"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

        # Should go to slot 1 (first empty)
        assert result["dest_slot"] == 1


class TestTransferUnknownWorkspace:
    def test_unknown_source(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        _set_worker(bp_a, 0, _make_worker("Alpha"))

        with pytest.raises(TransferError, match="source workspace not found"):
            transfer_worker(manager, "bad-uuid", 0, id_b, None, "copy")

    def test_unknown_dest(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        _set_worker(bp_a, 0, _make_worker("Alpha"))

        with pytest.raises(TransferError, match="destination workspace not found"):
            transfer_worker(manager, id_a, 0, "bad-uuid", None, "copy")


class TestTransferSameWorkspace:
    def test_same_workspace(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        _set_worker(bp_a, 0, _make_worker("Alpha"))

        with pytest.raises(TransferError, match="same-workspace"):
            transfer_worker(manager, id_a, 0, id_a, None, "copy")


class TestTransferSafeLandingZone:
    def test_copy_appends_safe_slot_when_configured_grid_is_full(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        bp_b = manager.get_bp_dir(id_b)

        _set_worker(bp_a, 0, _make_worker("Alpha"))

        # Fill all destination slots
        config_b = read_json(os.path.join(bp_b, "config.json"))
        rows = config_b.get("grid", {}).get("rows", 4)
        cols = config_b.get("grid", {}).get("cols", 6)
        total = rows * cols
        for i in range(total):
            _set_worker(bp_b, i, _make_worker(
                f"Worker{i}", row=i // cols, col=i % cols))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

        assert result["dest_slot"] == total
        dst_layout = read_json(os.path.join(bp_b, "layout.json"))
        clone = dst_layout["slots"][result["dest_slot"]]
        assert clone["name"] == "Alpha"
        assert clone["row"] == 0
        assert clone["col"] == cols

    def test_move_appends_safe_slot_when_configured_grid_is_full(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        bp_b = manager.get_bp_dir(id_b)

        _set_worker(bp_a, 0, _make_worker("Alpha"))

        config_b = read_json(os.path.join(bp_b, "config.json"))
        rows = config_b.get("grid", {}).get("rows", 4)
        cols = config_b.get("grid", {}).get("cols", 6)
        total = rows * cols
        for i in range(total):
            _set_worker(bp_b, i, _make_worker(
                f"Worker{i}", row=i // cols, col=i % cols))

        result = transfer_worker(manager, id_a, 0, id_b, None, "move")

        assert result["dest_slot"] == total
        src_layout = read_json(os.path.join(bp_a, "layout.json"))
        assert len(src_layout["slots"]) == 0 or src_layout["slots"][0] is None
        dst_layout = read_json(os.path.join(bp_b, "layout.json"))
        clone = dst_layout["slots"][result["dest_slot"]]
        assert clone["name"] == "Alpha"
        assert clone["row"] == 0
        assert clone["col"] == cols

    def test_copy_uses_unoccupied_visual_coordinate_when_slot_index_coord_collides(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        bp_b = manager.get_bp_dir(id_b)

        _set_worker(bp_a, 0, _make_worker("Alpha", row=5, col=5))
        _set_worker(bp_b, 0, _make_worker("Existing", row=5, col=5))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

        assert result["dest_slot"] == 1
        dst_layout = read_json(os.path.join(bp_b, "layout.json"))
        clone = dst_layout["slots"][result["dest_slot"]]
        assert clone["row"] == 5
        assert clone["col"] == 6


class TestTransferDestSlotOccupied:
    def test_dest_occupied(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        bp_b = manager.get_bp_dir(id_b)

        _set_worker(bp_a, 0, _make_worker("Alpha"))
        _set_worker(bp_b, 2, _make_worker("Existing"))

        with pytest.raises(TransferError, match="destination slot is occupied"):
            transfer_worker(manager, id_a, 0, id_b, 2, "copy")


class TestTransferBusyWorker:
    def test_busy_move_rejected(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        _set_worker(bp_a, 0, _make_worker("Alpha", state="working"))

        with pytest.raises(TransferError, match="worker is busy"):
            transfer_worker(manager, id_a, 0, id_b, None, "move")

    def test_busy_copy_allowed(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        _set_worker(bp_a, 0, _make_worker("Alpha", state="working",
                                           task_queue=["task-1"]))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

        assert result["ok"] is True
        # Clone should have runtime state reset
        bp_b = manager.get_bp_dir(id_b)
        dst_layout = read_json(os.path.join(bp_b, "layout.json"))
        clone = dst_layout["slots"][result["dest_slot"]]
        assert clone["state"] == "idle"
        assert clone["task_queue"] == []


class TestTransferProfile:
    def test_profile_copied(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        bp_b = manager.get_bp_dir(id_b)

        # Create a profile in source
        create_profile(bp_a, {
            "id": "custom-prof",
            "name": "Custom Profile",
            "default_agent": "claude",
            "default_model": "claude-sonnet-4-6",
            "color_hint": "blue",
            "expertise_prompt": "You are custom.",
            "workspaceId": "src-ws",
        })
        _set_worker(bp_a, 0, _make_worker("Alpha", profile="custom-prof"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy",
                                 copy_profile=True)

        assert result["profile_copied"] is True
        # Profile exists in destination
        dst_profile = get_profile(bp_b, "custom-prof")
        assert dst_profile is not None
        assert dst_profile["name"] == "Custom Profile"
        # workspaceId stripped
        assert "workspaceId" not in dst_profile

    def test_profile_not_copied_warning(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)

        create_profile(bp_a, {
            "id": "custom-prof",
            "name": "Custom Profile",
            "default_agent": "claude",
            "default_model": "claude-sonnet-4-6",
            "color_hint": "blue",
            "expertise_prompt": "You are custom.",
        })
        _set_worker(bp_a, 0, _make_worker("Alpha", profile="custom-prof"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy",
                                 copy_profile=False)

        assert result["profile_copied"] is False
        assert any("not copied" in w for w in result["warnings"])

    def test_profile_skip_existing(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        bp_b = manager.get_bp_dir(id_b)

        profile_data = {
            "id": "custom-prof",
            "name": "Custom Profile",
            "default_agent": "claude",
            "default_model": "claude-sonnet-4-6",
            "color_hint": "blue",
            "expertise_prompt": "You are custom.",
        }
        create_profile(bp_a, profile_data)
        create_profile(bp_b, {**profile_data, "name": "Existing Different"})

        _set_worker(bp_a, 0, _make_worker("Alpha", profile="custom-prof"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy",
                                 copy_profile=True)

        assert result["profile_copied"] is False
        assert any("already exists" in w for w in result["warnings"])
        # Destination profile unchanged
        dst_profile = get_profile(bp_b, "custom-prof")
        assert dst_profile["name"] == "Existing Different"


class TestTransferWatchColumnWarning:
    def test_watch_column_mismatch(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)

        _set_worker(bp_a, 0, _make_worker("Alpha", watch_column="custom_col",
                                           activation="on_queue"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

        assert any("custom_col" in w for w in result["warnings"])

    def test_watch_column_present_no_warning(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)

        # "inbox" is a default column
        _set_worker(bp_a, 0, _make_worker("Alpha", watch_column="inbox",
                                           activation="on_queue"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

        assert not any("watch_column" in w for w in result["warnings"])


class TestTransferDispositionColumnWarning:
    def test_disposition_column_mismatch(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)

        _set_worker(bp_a, 0, _make_worker("Alpha", disposition="custom_done"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

        assert any("disposition 'custom_done'" in w for w in result["warnings"])

    def test_disposition_column_present_no_warning(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)

        _set_worker(bp_a, 0, _make_worker("Alpha", disposition="review"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

        assert not any("does not exist in destination workspace" in w for w in result["warnings"])


class TestTransferAtomicity:
    def test_failed_dest_write_preserves_source(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        _set_worker(bp_a, 0, _make_worker("Alpha"))

        with patch("server.transfer.write_json", side_effect=IOError("disk full")):
            with pytest.raises(IOError):
                transfer_worker(manager, id_a, 0, id_b, None, "move")

        # Source must still have the worker
        src_layout = read_json(os.path.join(bp_a, "layout.json"))
        assert src_layout["slots"][0] is not None
        assert src_layout["slots"][0]["name"] == "Alpha"


class TestTransferNameDedup:
    def test_name_dedup(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)
        bp_b = manager.get_bp_dir(id_b)

        _set_worker(bp_a, 0, _make_worker("Alpha"))
        # Destination already has "Alpha"
        _set_worker(bp_b, 0, _make_worker("Alpha"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

        bp_b_layout = read_json(os.path.join(bp_b, "layout.json"))
        clone = bp_b_layout["slots"][result["dest_slot"]]
        assert clone["name"] == "Alpha copy"


class TestTransferDispositionWarning:
    def test_worker_disposition_warning(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)

        _set_worker(bp_a, 0, _make_worker("Alpha", disposition="worker:Beta"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

        assert any("disposition" in w and "worker:Beta" in w
                    for w in result["warnings"])

    def test_pass_disposition_warning(self, two_workspaces):
        manager, id_a, id_b = two_workspaces
        bp_a = manager.get_bp_dir(id_a)

        _set_worker(bp_a, 0, _make_worker("Alpha", disposition="pass:right"))

        result = transfer_worker(manager, id_a, 0, id_b, None, "copy")

        assert any("disposition" in w and "pass:right" in w
                    for w in result["warnings"])


class TestTransferEmptySourceSlot:
    def test_empty_slot(self, two_workspaces):
        manager, id_a, id_b = two_workspaces

        with pytest.raises(TransferError, match="source slot is empty"):
            transfer_worker(manager, id_a, 0, id_b, None, "copy")

    def test_out_of_range_slot(self, two_workspaces):
        manager, id_a, id_b = two_workspaces

        with pytest.raises(TransferError, match="source slot is empty"):
            transfer_worker(manager, id_a, 999, id_b, None, "copy")
