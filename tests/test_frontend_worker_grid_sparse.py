"""Regression checks for sparse worker grid implementation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_grid_geometry_loaded_before_bullpen_tab():
    text = _read("static/index.html")
    assert '<script src="/gridGeometry.js"></script>' in text
    assert text.index('/gridGeometry.js') < text.index('/components/BullpenTab.js')


def test_vue_cdn_is_pinned_and_has_matching_sri():
    text = _read("static/index.html")
    assert 'https://unpkg.com/vue@3.5.33/dist/vue.global.prod.js' in text
    assert 'integrity="sha384-DwljJiymYj3bq9J96m+aFUFsrBcnOhe+J38t7gF769KS7nPRyMft5bWqWkhwEFUW"' in text
    assert 'https://unpkg.com/vue@3/dist/vue.global.prod.js' not in text


def test_bullpen_tab_uses_sparse_coordinate_rendering():
    text = _read("static/components/BullpenTab.js")
    assert "v-for=\"item in visibleWorkers\"" in text
    assert "coordForSlot(worker, slotIndex)" in text
    assert "GridGeometry.coordToPixel" in text
    assert "GridGeometry.visibleRange" in text
    assert "GridGeometry.overscanRange(range, 2)" in text
    assert "GridGeometry.coordKey(item.coord.col, item.coord.row)" in text


def test_empty_cells_use_single_ghost_target_and_clipboard_does_not_materialize_all_cells():
    text = _read("static/components/BullpenTab.js")
    assert "v-if=\"ghostCell\"" in text
    assert "worker-grid-ghost-cell" in text
    assert "class=\"empty-slot-menu-btn\" draggable=\"false\"" in text
    assert "one reusable" not in text  # implementation should be structural, not comment-only
    assert "this.clipboardWorker" in text
    assert "canPasteAt(coord)" in text
    assert "A non-empty pane clipboard" not in text


def test_worker_drag_over_empty_cell_tracks_valid_drop_target_for_ghost_highlight():
    text = _read("static/components/BullpenTab.js")
    assert "dragOverCoord: null" in text
    assert ":class=\"{ selected: isSelected(ghostCell), 'drag-over': isDragOverGhost(ghostCell) }\"" in text
    assert "const coord = this.dragOverCoord || this.emptyMenuCoord || this.selectedCell || this.hoveredCoord;" in text
    assert "this.dragOverCoord = { ...coord };" in text
    assert "this.dragOverCoord = null;" in text
    assert "@dragleave=\"onCanvasDragLeave\"" in text
    assert "onCanvasDragLeave(e)" in text
    assert "canvas.contains(related)" in text


def test_empty_cell_menu_supports_keyboard_navigation_when_opened_from_grid_selection():
    text = _read("static/components/BullpenTab.js")
    assert "ref=\"emptyMenu\"" in text
    assert "tabindex=\"-1\"" in text
    assert "@keydown=\"onEmptyMenuKeydown\"" in text
    assert "if (this.emptyMenuCoord && !inTextInput && !e.metaKey && !e.ctrlKey && !e.altKey) {" in text
    assert "this.onEmptyMenuKeydown(e);" in text
    assert "const menu = this.$refs.emptyMenu;" in text
    assert "const [first] = this.emptyMenuItems();" in text
    assert "onEmptyMenuKeydown(e)" in text
    assert "items[(currentIdx + 1) % items.length].focus();" in text
    assert "this.closeEmptyMenu({ focusViewport: true });" in text


def test_empty_cell_click_is_selected_and_enter_targets_current_empty_cell():
    text = _read("static/components/BullpenTab.js")
    assert "if (!wasPanning && !this.itemAtCoord(coord) && this.isWritableCoord(coord)) {" in text
    assert "this.selectedCell = { ...coord };" in text
    assert "} else if (e.key === 'Enter') {" in text
    assert "const coord = this.selectedCell || this.ghostCell;" in text
    assert "this.openEmptyMenu(coord);" in text


def test_grid_controls_replace_legacy_rows_cols_selector():
    app = _read("static/app.js")
    bullpen = _read("static/components/BullpenTab.js")
    assert "onTabBarGridResize" not in app
    assert "gridOptions" not in app
    assert "columnWidth" in bullpen
    # SML layout buttons and Width box were removed from the toolbar; row
    # height is now driven solely by dragging the row resize handle.
    assert "worker-layout-buttons" not in bullpen
    assert "worker-width-control" not in bullpen
    assert "setLayoutMode" not in bullpen
    assert "onWidthChange" not in bullpen


def test_worker_grid_resize_uses_pending_size_until_config_echo():
    text = _read("static/components/BullpenTab.js")
    assert "pendingColumnWidth: null" in text
    assert "pendingRowHeight: null" in text
    assert "if (this.pendingColumnWidth !== null)" in text
    assert "if (this.pendingRowHeight !== null)" in text
    assert "reconcilePendingGridSize()" in text
    assert "this.pendingColumnWidth = final;" in text
    assert "this.pendingRowHeight = final;" in text


def test_worker_card_has_header_status_and_copy_worker_menu():
    text = _read("static/components/WorkerCard.js")
    assert "worker-card-header-status" in text
    assert "Copy Worker" in text
    assert "menuCopyWorker()" in text
    assert "$emit('copy-worker', this.slotIndex)" in text
    assert "Export Worker" in text
    assert "menuExportWorker()" in text
    assert "$root.exportWorker(this.slotIndex)" in text
    assert "effectiveLayoutMode()" in text
    assert "v-if=\"effectiveLayoutMode !== 'small'\"" in text


def test_worker_grid_styles_define_viewport_minimap_and_fixed_card_overflow():
    text = _read("static/style.css")
    assert ".worker-grid-viewport" in text
    assert "touch-action: none;" in text
    assert ".worker-minimap" in text
    assert ".worker-grid-ghost-cell" in text
    assert ".worker-grid-ghost-cell.drag-over {" in text
    assert ".worker-card-header-status" in text
    assert "overflow: hidden;" in text
    # .worker-card must NOT set `contain: layout` or `contain: paint` — both
    # make the card a containing block for position:fixed descendants, which
    # breaks the header kebab menu (positioned via viewport coords from
    # getBoundingClientRect). `contain: paint` would also clip the
    # pass-connected indicator pills that render into the grid gutter.
    assert "contain: layout" not in text
    assert "contain: paint" not in text


def test_worker_grid_touch_pointer_pans_from_cards_instead_of_selecting():
    text = _read("static/components/BullpenTab.js")
    assert "if (e.target.closest('.worker-card') && e.pointerType !== 'touch') return;" in text
    assert "const isTouch = e.pointerType === 'touch';" in text
    assert "selection: e.button === 0 && !isTouch" in text
    assert "window._bullpenSuppressWorkerClickUntil = Date.now() + 250;" in text


def test_worker_grid_viewport_origin_is_local_to_window():
    text = _read("static/components/BullpenTab.js")
    assert "workspaceViewportOrigins: {}" in text
    assert "rememberWorkspaceViewportOrigin()" in text
    assert "delete grid.viewportOrigin;" in text
    assert "this.$root.updateConfig({ grid });" in text
    assert "this.persistGrid({ viewportOrigin: this.viewportOrigin })" not in text
    assert "'config.grid.viewportOrigin'" not in text


def test_grid_headers_highlight_selected_cell_not_fixed_origin():
    tab = _read("static/components/BullpenTab.js")
    css = _read("static/style.css")

    assert "'is-selected': selectedCell && c.col === selectedCell.col" in tab
    assert "'is-selected': selectedCell && r.row === selectedCell.row" in tab
    assert ".worker-grid-column-header.is-selected {" in css
    assert ".worker-grid-row-header.is-selected {" in css
    assert ".worker-grid-column-header.is-origin {\n  color: var(--text-secondary);" in css
    assert ".worker-grid-row-header.is-origin {\n  color: var(--text-secondary);" in css


def test_selected_grid_cells_stay_below_headers_when_scrolled_under_them():
    css = _read("static/style.css")

    assert ".worker-grid-column-headers {\n  position: absolute;" in css
    assert ".worker-grid-row-headers {\n  position: absolute;" in css
    assert ".worker-card.selected,\n.worker-grid-ghost-cell.selected {\n  box-shadow: 0 0 0 2px var(--accent);\n  z-index: 1;\n}" in css
    assert ".worker-grid-column-headers {\n  position: absolute;\n  top: 0;\n  right: 0;\n  overflow: hidden;\n  background: var(--bg-secondary);\n  box-shadow: 0 1px 0 var(--border);\n  z-index: 2;" in css
    assert ".worker-grid-row-headers {\n  position: absolute;\n  left: 0;\n  bottom: 0;\n  overflow: hidden;\n  background: var(--bg-secondary);\n  box-shadow: 1px 0 0 var(--border);\n  z-index: 2;" in css


def test_worker_grid_header_separators_paint_on_cell_edges():
    css = _read("static/style.css")

    assert ".worker-grid-corner {\n  position: absolute;\n  top: 0;\n  left: 0;\n  background: var(--bg-secondary);\n  box-shadow: 1px 0 0 var(--border), 0 1px 0 var(--border);" in css
    assert ".worker-grid-column-header {\n  position: absolute;" in css
    assert "  color: var(--text-secondary);\n  box-shadow: 1px 0 0 var(--border);\n  box-sizing: border-box;" in css
    assert ".worker-grid-row-header {\n  position: absolute;" in css
    assert "  color: var(--text-secondary);\n  box-shadow: 0 1px 0 var(--border);\n  box-sizing: border-box;" in css


def test_worker_grid_cards_have_fixed_one_pixel_inset():
    tab = _read("static/components/BullpenTab.js")
    css = _read("static/style.css")

    assert "debugCardInset" not in tab
    assert "worker-grid-debug-inset" not in tab
    assert "insetBoxStyle(x, y, width, height)" in tab
    assert "const minWidth = 64;" in tab
    assert "const minHeight = 24;" in tab
    assert "const insetX = Math.min(1, Math.max(0, (width - minWidth) / 2));" in tab
    assert "const insetY = Math.min(1, Math.max(0, (height - minHeight) / 2));" in tab
    assert "style: this.insetBoxStyle(p.x, p.y, this.columnWidth, this.rowHeight)" in tab
    assert "...this.insetBoxStyle(p.x, p.y, this.columnWidth, this.cardHeightForSlot(item.slotIndex))" in tab
    assert "worker-grid-debug-inset" not in css
    assert ".worker-card.worker-card--small {\n  background: transparent !important;\n  border-color: transparent;\n}" in css


def test_minimap_bounds_clamp_to_a1_origin():
    tab = _read("static/components/BullpenTab.js")
    assert "const colMin = Math.max(0, Math.min(b?.colMin ?? 0, visible.colStart) - 2);" in tab
    assert "const rowMin = Math.max(0, Math.min(b?.rowMin ?? 0, visible.rowStart) - 2);" in tab


def test_minimap_arrows_use_explicit_compass_layout():
    tab = _read("static/components/BullpenTab.js")
    css = _read("static/style.css")

    assert "minimap-arrow minimap-arrow-up" in tab
    assert "minimap-arrow minimap-arrow-left" in tab
    assert "minimap-arrow minimap-arrow-right" in tab
    assert "minimap-arrow minimap-arrow-down" in tab
    assert "grid-template-areas:" in css
    assert '". up ."' in css
    assert '"left . right"' in css
    assert '". down ."' in css
    assert ".worker-minimap-arrows .minimap-arrow-up {" in css
    assert ".worker-minimap-arrows .minimap-arrow-down {" in css


def test_minimap_nodes_use_worker_header_colors():
    tab = _read("static/components/BullpenTab.js")
    css = _read("static/style.css")

    assert "class=\"worker-minimap-dot\" :style=\"dot.style\"" in tab
    assert "background: workerColor(item.worker)" in tab
    assert "worker-minimap-dot.status-working" not in css
    assert "worker-minimap-dot.status-queued" not in css
