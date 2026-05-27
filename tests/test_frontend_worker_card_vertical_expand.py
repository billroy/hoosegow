"""Regression checks for per-card vertical expansion in the worker grid."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_worker_card_accepts_selected_card_height_and_resize_props():
    text = _read("static/components/WorkerCard.js")
    assert "'cardHeight'" in text
    assert "'isSelected'" in text
    assert "'isVerticalResizing'" in text
    assert "'workspaceId'" in text
    assert "'requestOutputCatchup'" in text
    assert "'vertical-resize-start'" in text
    assert "effectiveLayoutMode()" in text
    assert "this.ensureOutputCatchup();" in text
    assert "outputRequestToken()" in text
    assert "this.requestOutputCatchup(this.slotIndex, {" in text


def test_worker_card_bottom_hover_prefers_vertical_resize_outside_pass_down_zone():
    text = _read("static/components/WorkerCard.js")
    assert "v-if=\"showsVerticalResizeControl\"" in text
    assert "hasExpandableCardContent()" in text
    assert "return this.isPaused || this.taskQueueCount > 0 || this.workerState !== 'idle';" in text
    assert "showsVerticalResizeControl()" in text
    assert "return this.isSelected && this.hasExpandableCardContent;" in text
    assert "updateCardHoverState(e)" in text
    assert "onCardMouseMove(e)" in text
    assert "this.updateCardHoverState(e);" in text
    assert "onPointerMove(e)" in text
    assert "const downHandleZone = this.canConnect('down') && Math.abs(x - (rect.width / 2)) <= 18;" in text
    assert "if (this.showsVerticalResizeControl && y >= rect.height - threshold && !downHandleZone) {" in text
    assert "this.hoveredVerticalResize = true;" in text
    assert "this.hoveredHandle = null;" in text
    assert "if (!this.showsVerticalResizeControl || e.button !== 0) return;" in text


def test_bullpen_tab_tracks_ephemeral_expanded_height_for_selected_card_only():
    text = _read("static/components/BullpenTab.js")
    assert "cardVerticalResize: null" in text
    assert "expandedWorkerCardSlot: null" in text
    assert "expandedWorkerCardDelta: 0" in text
    assert "selectedWorkerSlot()" in text
    assert "cardHeightForSlot(slotIndex)" in text
    assert "if (next === this.expandedWorkerCardSlot) return;" in text
    assert "this.clearExpandedWorkerCard();" in text


def test_bullpen_tab_wires_resize_events_and_clamps_to_global_height_limits():
    text = _read("static/components/BullpenTab.js")
    assert ":card-height=\"cardHeightForSlot(item.slotIndex)\"" in text
    assert ":is-vertical-resizing=\"cardVerticalResize && cardVerticalResize.slotIndex === item.slotIndex\"" in text
    assert ":output-lines=\"$root.outputLinesForSlot(item.slotIndex, workspaceId)\"" in text
    assert ":workspace-id=\"workspaceId\"" in text
    assert ":request-output-catchup=\"$root.requestOutputCatchup\"" in text
    assert "@vertical-resize-start=\"onCardVerticalResizeStart(item, $event)\"" in text
    assert "cardExpansionLimit()" in text
    assert "Math.max(0, 480 - this.rowHeight)" in text
    assert "this.expandedWorkerCardDelta = Math.max(0, Math.min(this.cardExpansionLimit(), Math.round(next)));" in text


def test_card_vertical_resize_styles_exist():
    text = _read("static/style.css")
    assert ".card-height-resize-handle {" in text
    assert "cursor: row-resize;" in text
    assert ".card-height-resize-handle.card-height-resize-handle-active {" in text
