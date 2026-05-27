"""Regression checks for draggable pass-direction connection handles on worker cards."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_bullpen_tab_computes_neighbor_slots_and_passes_them_to_card():
    text = _read("static/components/BullpenTab.js")
    assert "neighborSlotsMap()" in text
    assert ":neighbor-slots=\"neighborSlotsMap[item.slotIndex]\"" in text
    # Neighbor detection is coordinate-based in the sparse grid.
    assert "this.itemAtCoord({ col: c.col, row: c.row - 1 })?.slotIndex ?? null" in text
    assert "this.itemAtCoord({ col: c.col, row: c.row + 1 })?.slotIndex ?? null" in text
    assert "this.itemAtCoord({ col: c.col - 1, row: c.row })?.slotIndex ?? null" in text
    assert "this.itemAtCoord({ col: c.col + 1, row: c.row })?.slotIndex ?? null" in text


def test_worker_card_accepts_neighbor_slots_prop():
    text = _read("static/components/WorkerCard.js")
    assert "'neighborSlots'" in text
    assert "canConnect(dir)" in text
    assert "passConnectsToNeighbor" in text


def test_worker_card_renders_drag_handles_only_for_existing_neighbors():
    text = _read("static/components/WorkerCard.js")
    assert "v-for=\"dir in ['up','down','left','right']\"" in text
    assert "v-if=\"canConnect(dir)\"" in text
    assert "class=\"connect-handle\"" in text
    assert "@dragstart.stop=\"onHandleDragStart(dir, $event)\"" in text
    assert "@dragend.stop=\"onHandleDragEnd\"" in text
    assert "class=\"connect-handle-arrow\"" in text
    assert "connectHandleArrow(dir)" in text


def test_worker_card_handle_dragstart_sets_connect_mime_and_target():
    text = _read("static/components/WorkerCard.js")
    assert "onHandleDragStart(dir, e)" in text
    assert "'application/x-worker-connect'" in text
    # Payload must include target slot so dragover handlers can identify the
    # intended drop destination without reading dataTransfer (restricted).
    assert "target: this.neighborSlots[dir]" in text
    assert "window._bullpenConnectDrag" in text


def test_worker_card_onDragOver_highlights_only_intended_target():
    text = _read("static/components/WorkerCard.js")
    # Only preventDefault on the intended neighbor slot so non-targets show
    # a "no-drop" cursor.
    assert "drag.target === this.slotIndex" in text
    assert "this.connectTarget = true" in text
    assert "dropEffect = 'none'" in text


def test_worker_card_onDrop_updates_disposition_for_source_slot():
    text = _read("static/components/WorkerCard.js")
    assert "'application/x-worker-connect'" in text
    assert "saveWorkerConfig" in text
    assert "'pass:' + payload.direction" in text
    assert "slot: payload.source" in text


def test_worker_card_connect_target_class_toggled_on_card():
    text = _read("static/components/WorkerCard.js")
    assert "'connect-target': connectTarget" in text


def test_pass_indicator_gets_connected_class_when_neighbor_exists():
    text = _read("static/components/WorkerCard.js")
    assert "'pass-connected': passConnectsToNeighbor" in text


def test_connect_handle_and_target_styles_exist():
    text = _read("static/style.css")
    assert ".connect-handle {" in text
    assert ".connect-handle-arrow {" in text
    assert ".connect-handle-up {" in text
    assert ".connect-handle-down {" in text
    assert ".connect-handle-left {" in text
    assert ".connect-handle-right {" in text
    # Handles are hidden by default (including pointer-events) so they
    # never obstruct clicks on the card body, and are revealed one at a
    # time via the .connect-handle-active class when the cursor is near
    # the matching edge.
    assert "pointer-events: none;" in text
    assert ".connect-handle.connect-handle-active {" in text
    # Connect-target highlight on the adjacent card during drag
    assert ".worker-card.connect-target {" in text


def test_connect_handles_are_semicircular_inside_the_card():
    text = _read("static/style.css")
    # Flush with the card edge (top/bottom/left/right: 0), not protruding
    # into the gutter (-5px) like the previous square-ish handles.
    assert "border-radius: 0 0 12px 12px" in text  # up handle
    assert "border-radius: 12px 12px 0 0" in text  # down handle
    assert "border-radius: 0 12px 12px 0" in text  # left handle
    assert "border-radius: 12px 0 0 12px" in text  # right handle


def test_worker_card_connect_handles_render_directional_arrows():
    text = _read("static/components/WorkerCard.js")
    assert "connectHandleArrow(dir)" in text
    assert "up: '\\u2191'" in text
    assert "down: '\\u2193'" in text
    assert "left: '\\u2190'" in text
    assert "right: '\\u2192'" in text


def test_connect_handle_arrow_is_centered_inside_handle():
    text = _read("static/style.css")
    assert "display: flex;" in text
    assert "align-items: center;" in text
    assert "justify-content: center;" in text
    assert "font-size: 9px;" in text


def test_worker_card_does_not_force_grab_cursor_on_body():
    # The whole card previously carried cursor: grab, which made the body
    # always look draggable even though clicking the body opens the focus
    # view. The resting body should not display a grab cursor.
    text = _read("static/style.css")
    # Locate the .worker-card {...} block (first one after the section header)
    start = text.index("/* === Worker Card === */")
    end = text.index("}", start) + 1
    block = text[start:end]
    assert "cursor: grab;" not in block


def test_worker_card_exposes_drag_feedback_while_initiating_drag():
    text = _read("static/style.css")
    assert ".worker-card[draggable=\"true\"]:active" in text
    assert ".worker-card.is-dragging" in text
    assert "body.worker-singleton-dragging" in text
    assert "cursor: grabbing;" in text
    assert ".worker-card-header {" in text
    assert "cursor: grab;" in text


def test_worker_card_tracks_hovered_handle_for_edge_reveal():
    text = _read("static/components/WorkerCard.js")
    # Mouse tracking wires up per-edge reveal
    assert "@mousemove=\"onCardMouseMove\"" in text
    assert "@mouseleave=\"onCardMouseLeave\"" in text
    assert "onCardMouseMove(e)" in text
    assert "onCardMouseLeave()" in text
    # Template binds the active class to the hovered direction only
    assert "hoveredHandle === dir" in text
    assert "'connect-handle-active'" in text or "connect-handle-active" in text
    # Data property exists and starts null (nothing shown by default)
    assert "hoveredHandle: null" in text


def test_pass_connected_pill_renders_in_gutter():
    text = _read("static/style.css")
    assert ".pass-indicator.pass-connected {" in text
    # Pills sit outside the card footprint (negative offsets = gutter).
    assert ".pass-up.pass-connected {" in text
    assert ".pass-down.pass-connected {" in text
    assert ".pass-left.pass-connected {" in text
    assert ".pass-right.pass-connected {" in text
    # Card must allow children to render into the gutter
    assert "overflow: visible;" in text


def test_worker_config_modal_disables_pass_options_without_neighbor():
    text = _read("static/components/WorkerConfigModal.js")
    assert "'gridRows'" in text and "'gridCols'" in text
    assert "passAvailability()" in text
    assert ":disabled=\"!passAvailability.up\"" in text
    assert ":disabled=\"!passAvailability.down\"" in text
    assert ":disabled=\"!passAvailability.left\"" in text
    assert ":disabled=\"!passAvailability.right\"" in text


def test_app_wires_grid_rows_cols_into_worker_config_modal():
    text = _read("static/app.js")
    assert ":grid-rows=\"state.config.grid?.rows || 4\"" in text
    assert ":grid-cols=\"state.config.grid?.cols || 6\"" in text
