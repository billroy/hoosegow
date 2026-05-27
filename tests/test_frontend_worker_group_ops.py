"""Regression checks for worker-group drag/drop and clipboard operations."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_app_exposes_group_worker_socket_events():
    text = _read("static/app.js")
    assert "function moveWorkerGroup(moves)" in text
    assert "socket.emit('worker:move_group'" in text
    assert "function pasteWorkerGroup(items)" in text
    assert "socket.emit('worker:paste_group'" in text


def test_bullpen_tab_builds_pass_reachable_groups_for_drag_and_copy():
    text = _read("static/components/BullpenTab.js")
    assert "workerGroupSlots(startSlot)" in text
    assert "selectedWorkerSlots: []" in text
    assert "expandSelectionSlots(slots)" in text
    assert "passTargetsForSlot(slotIndex)" in text
    assert "buildGroupMovePlan(sourceSlot, destinationCoord," in text
    assert "canDropWorkerAtSlot(sourceSlot, targetSlot, e)" in text
    assert "moveWorkerGroupToCoord(sourceSlot, coord)" in text
    assert "this.$root.moveWorkerGroup(plan.moves)" in text
    assert "Copied worker group (" in text


def test_bullpen_tab_supports_range_multiple_selection():
    text = _read("static/components/BullpenTab.js")
    assert "selectionAnchor: null" in text
    assert "isMultipleSelectionActive()" in text
    assert "slotsInRange(a, b)" in text
    assert "updateRangeSelection(anchor, active)" in text
    assert "if (e.shiftKey) {" in text
    assert "this.updateRangeSelection(anchor, next);" in text
    assert "selectionMoved: false" in text
    assert "this.dragStart.selectionMoved = true;" in text
    assert ":multiple-selection-active=\"isMultipleSelectionActive\"" in text


def test_bullpen_tab_group_detection_includes_inbound_pass_links():
    text = _read("static/components/BullpenTab.js")
    assert "passTargetsBySlot()" in text
    assert "passSourcesBySlot()" in text
    assert "passSourcesForSlot(slotIndex)" in text
    assert "return this.passSourcesBySlot[target] || [];" in text
    assert "new Set([...this.passTargetsForSlot(slot), ...this.passSourcesForSlot(slot)])" in text


def test_bullpen_tab_worker_drop_reuses_expanded_move_plan():
    text = _read("static/components/BullpenTab.js")
    assert "this.selectedWorkerSlots = plan.slots.slice();" in text
    assert "this.selectedWorkerSlots = this.expandSelectionSlots(plan.slots);" not in text


def test_worker_grid_avoids_broad_icon_rerenders_on_layout_updates():
    app = _read("static/app.js")
    tab = _read("static/components/BullpenTab.js")
    assert "updated() {\n    renderLucideIcons(this.$el);" not in app
    assert "updated() {\n    renderLucideIcons(this.$el);" not in tab
    assert "this.$nextTick(() => renderLucideIcons(this.$el));" in app
    assert "this.$nextTick(() => renderLucideIcons(this.$el));" in tab


def test_worker_grid_caches_dragover_drop_target_and_viewport_rect():
    text = _read("static/components/BullpenTab.js")
    assert "dragViewportRect: null" in text
    assert "lastDropTargetKey: ''" in text
    assert "const rect = this.dragViewportRect || this.$refs.viewport.getBoundingClientRect();" in text
    assert "if (coord && this.lastDropTargetKey === key) return true;" in text
    assert "this.lastDropTargetKey = key;" in text
    assert "this.dragViewportRect = null;" in text


def test_worker_cards_use_parent_task_lookup_map():
    app = _read("static/app.js")
    tab = _read("static/components/BullpenTab.js")
    card = _read("static/components/WorkerCard.js")
    assert "const taskById = computed(() => {" in app
    assert ":task-by-id=\"taskById\"" in app
    assert ":task-by-id=\"taskById\"" in tab
    assert "'taskById'" in card
    assert "lookupTask(id)" in card
    assert "this.taskById && this.taskById[id]" in card
    assert "const t = this.lookupTask(id);" in card
    assert "const task = this.lookupTask(this.worker.task_queue[0]);" in card


def test_worker_card_avoids_menu_icon_render_on_every_update():
    text = _read("static/components/WorkerCard.js")
    assert "updated() {\n    if (this.$refs.menu) renderLucideIcons(this.$refs.menu);" not in text
    assert "menuIconToken()" in text
    assert "if (this.showMenu) this.$nextTick(() => renderLucideIcons(this.$refs.menu));" in text


def test_mounted_surfaces_avoid_generic_updated_icon_rerenders():
    for rel_path in [
        "static/components/LeftPane.js",
        "static/components/KanbanTab.js",
        "static/components/TaskDetailPanel.js",
        "static/components/StatsTab.js",
    ]:
      text = _read(rel_path)
      assert "updated() {\n    renderLucideIcons(this.$el);" not in text


def test_worker_card_only_runs_elapsed_timer_when_status_needs_it():
    text = _read("static/components/WorkerCard.js")
    assert "needsElapsedTimer()" in text
    assert "this.syncElapsedTimer();" in text
    assert "setInterval(() => this.updateElapsed(), 1000)" in text
    assert "this._timer = setInterval(() => this.updateElapsed(), 1000);\n    this.updateElapsed();" not in text


def test_server_worker_group_move_uses_coordinate_occupancy_map():
    text = _read("server/events.py")
    assert "def _coord_occupancy_map(layout, cols=4):" in text
    assert "occupied_by_coord = _coord_occupancy_map(layout, cols=cols)" in text
    assert "occupied_slot = occupied_by_coord.get((coord[\"col\"], coord[\"row\"]))" in text
    assert "occupied_slot = _coord_occupied(layout, move[\"to_coord\"], cols=cols)" not in text


def test_bullpen_tab_pastes_group_workers_with_relative_offsets():
    text = _read("static/components/BullpenTab.js")
    assert "clipboardTargetsForCoord(coord)" in text
    assert "this.$root.pasteWorkerGroup(targets)" in text
    assert "Cannot paste worker group here" in text
    assert "if (!this.clipboardWorker || !coord || !this.isWritableCoord(coord)) return false;" in text
    assert "if (this.itemAtCoord(target.coord)) return false;" in text


def test_bullpen_tab_worker_clipboard_preserves_shell_fields():
    text = _read("static/components/BullpenTab.js")
    assert "workerFieldsForClipboard(worker)" in text
    assert "'type', 'profile'" in text
    assert "'command', 'cwd', 'timeout_seconds', 'ticket_delivery', 'env'" in text
    assert "'pre_start', 'ticket_action', 'startup_grace_seconds', 'startup_timeout_seconds'" in text
    assert "'health_type', 'health_url', 'health_command', 'health_interval_seconds'" in text
    assert "copy[key] = JSON.parse(JSON.stringify(worker[key]));" in text


def test_worker_card_uses_group_drag_payload_and_delegates_drop_validation():
    text = _read("static/components/WorkerCard.js")
    assert "'buildWorkerDragPayload'" in text
    assert "'buildWorkerDragImage'" in text
    assert "'canDropWorkerAtSlot'" in text
    assert "'dropWorkerOnSlot'" in text
    assert "'updateSingletonWorkerDrag'" in text
    assert "'endSingletonWorkerDrag'" in text
    assert "'cancelSingletonWorkerDrag'" in text
    assert "'application/x-worker-group'" in text
    assert "@pointerdown=\"onPointerDown\"" in text
    assert "@pointermove=\"onPointerMove\"" in text
    assert "@lostpointercapture=\"onPointerLostCapture\"" in text
    assert "pointerWorkerDrag" in text
    assert "shiftDragIntent" in text
    assert "const singleton = !!(e.shiftKey || this.shiftDragIntent)" in text
    assert "window._bullpenWorkerDrag = payload" in text
    assert "this.buildWorkerDragPayload(this.slotIndex, {" in text
    assert "clientX: e.clientX" in text
    assert "clientY: e.clientY" in text
    assert "this.buildWorkerDragImage(this.slotIndex" in text
    assert "e.dataTransfer.setDragImage(dragImage.element, offsetX, offsetY)" in text
    assert "removeDragImage()" in text
    assert "this.canDropWorkerAtSlot(source, this.slotIndex, e)" in text
    assert "const handled = this.dropWorkerOnSlot(dragSource, this.slotIndex, e)" in text
    assert "if (handled) {" in text


def test_worker_card_keeps_per_worker_menu_items_enabled_during_group_selection():
    text = _read("static/components/WorkerCard.js")
    assert "'multipleSelectionActive'" in text
    assert "'delete-worker'" in text
    assert "class=\"worker-menu-item\" @click=\"menuEdit\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuRun\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuRestart\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuWatch\"" in text
    assert "class=\"worker-menu-item\" :disabled=\"!serviceSiteUrl\" @click=\"menuOpenSite\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuStop\"" in text
    assert "if (this.multipleSelectionActive) return;\n      this.closeMenuAndRestoreFocus();\n      this.$emit('configure', this.slotIndex);" not in text
    assert "if (this.multipleSelectionActive) return;\n      this.closeMenuAndRestoreFocus();\n      this.$root.startWorkerSlot(this.slotIndex);" not in text


def test_worker_card_still_disables_group_ambiguous_menu_items():
    text = _read("static/components/WorkerCard.js")
    assert "v-if=\"canPauseWorker && !isPaused\"" in text
    assert "v-if=\"canPauseWorker && isPaused\"" in text
    assert ":disabled=\"multipleSelectionActive\" @click=\"menuPause\"" in text
    assert ":disabled=\"multipleSelectionActive\" @click=\"menuUnpause\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuDuplicate\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuCopyWorker\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuExportWorker\"" in text
    assert "class=\"worker-menu-item worker-menu-danger\" :disabled=\"multipleSelectionActive\" @click=\"menuDelete\"" in text
    assert ":disabled=\"multipleSelectionActive\" @click=\"menuDelete\"" in text
    assert "if (this.multipleSelectionActive) return;" in text
    assert "this.$emit('delete-worker', this.slotIndex);" in text


def test_worker_card_blocks_run_during_automation_pause():
    text = _read("static/components/WorkerCard.js")
    assert "automationPausedForWorker()" in text
    assert "this.$root?.state?.config?.worker_automation_paused === true" in text
    assert "canStart && !isPaused && !automationPausedForWorker" in text


def test_bullpen_tab_deletes_selected_worker_group_from_menu():
    text = _read("static/components/BullpenTab.js")
    assert "@delete-worker=\"deleteWorkerFromMenu\"" in text
    assert "deleteWorkerFromMenu(slot)" in text
    assert "this.selectedWorkerSlots.includes(source) && this.selectedWorkerSlots.length > 1" in text
    assert "this.$root.removeWorkers(slots)" in text
    assert "this.$root.removeWorker(source)" in text


def test_bullpen_tab_builds_composite_drag_image_for_worker_groups():
    text = _read("static/components/BullpenTab.js")
    assert ":build-worker-drag-image=\"buildWorkerDragImage\"" in text
    assert "buildWorkerDragImage(slotIndex, pointer = {}," in text
    assert "worker-group-drag-image" in text
    assert "workerElementForSlot(slotIndex)" in text
    assert "cardEl ? cardEl.cloneNode(true)" in text


def test_worker_drag_uses_pointer_projected_drop_coordinates():
    text = _read("static/components/BullpenTab.js")
    assert "pointerOffset" in text
    assert "singleton: true" in text
    assert "_workerDragPointerOffset(slotIndex, pointer = {})" in text
    assert "_workerDragCoordFromEvent(e)" in text
    assert "const x = e.clientX - rect.left - this.headerWidth - offsetX" in text
    assert "const y = e.clientY - rect.top - this.headerHeight - offsetY" in text
    assert "canDropWorkerAtSlot(sourceSlot, targetSlot, e)" in text
    assert "dropWorkerOnSlot(sourceSlot, targetSlot, e)" in text
    assert "const dropCoord = this._workerDragCoordFromEvent(e) || coord" in text
    assert "const coord = this._workerDragCoordFromEvent(e)" in text


def test_shift_worker_drag_uses_single_card_move_or_swap_semantics():
    text = _read("static/components/BullpenTab.js")
    assert "_isSingletonWorkerDrag()" in text
    assert "return !!window._bullpenWorkerDrag || types.includes('application/x-worker-slot')" in text
    assert "moveWorkerDragToCoord(sourceSlot, coord)" in text
    assert "moveSingleWorkerToCoord(sourceSlot, coord)" in text
    assert "updateSingletonWorkerDrag(sourceSlot, e)" in text
    assert "endSingletonWorkerDrag(sourceSlot, e)" in text
    assert "cancelSingletonWorkerDrag()" in text
    assert "if (this._isSingletonWorkerDrag()) {" in text
    assert "this.$root.moveWorker(source, occupied.slotIndex)" in text
    assert "this.$root.moveWorker(source, coord)" in text
    assert ":update-singleton-worker-drag=\"updateSingletonWorkerDrag\"" in text
    assert ":end-singleton-worker-drag=\"endSingletonWorkerDrag\"" in text
    assert ":cancel-singleton-worker-drag=\"cancelSingletonWorkerDrag\"" in text
