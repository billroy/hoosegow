const BullpenTab = {
  UNCONFIGURED_PROFILE_ID: 'unconfigured-worker',
  HEADER_WIDTH: 40,
  HEADER_HEIGHT: 24,
  MINIMAP_HEADER_PX: 30,
  props: ['layout', 'config', 'profiles', 'tasks', 'taskById', 'workspace', 'workspaceId', 'multipleWorkspaces', 'minimapCollapsed'],
  emits: ['add-worker', 'configure-worker', 'select-task', 'open-focus', 'transfer-worker', 'set-minimap-collapsed'],
  components: { WorkerCard },
  data() {
    return {
      showLibrary: false,
      libraryMode: 'ai',
      shellExamples: [],
      shellExamplesLoaded: false,
      showGoTo: false,
      goToInput: '',
      goToWorkerSlot: '',
      goToError: '',
      showHelp: false,
      selectedAddCoord: null,
      hoveredCoord: null,
      selectedCell: null,
      selectionAnchor: null,
      selectedWorkerSlots: [],
      dragOverCoord: null,
      dropTargetCoords: [],
      emptyMenuCoord: null,
      emptyMenuPos: null,
      clipboardWorker: null,
      viewportOrigin: { col: 0, row: 0 },
      viewportPx: { width: 0, height: 0 },
      dragStart: null,
      isPanning: false,
      liveMessage: '',
      columnResize: null,
      draggingColumnWidth: null,
      pendingColumnWidth: null,
      rowResize: null,
      draggingRowHeight: null,
      pendingRowHeight: null,
      cardVerticalResize: null,
      expandedWorkerCardSlot: null,
      expandedWorkerCardDelta: 0,
      resizeTooltip: null,
      dragViewportRect: null,
      lastDropTargetKey: '',
      workspaceViewportOrigins: {},
    };
  },
  template: `
    <div class="bullpen-grid-container">
      <Teleport to="#worker-tab-toolbar-slot">
        <button class="btn btn-sm" @click="jumpHome">Home</button>
        <button class="btn btn-sm" @click="fitOccupied">Fit</button>
      </Teleport>

      <div class="worker-grid-viewport"
           ref="viewport"
           tabindex="0"
           role="grid"
           :aria-label="'Worker grid at column ' + Math.floor(viewportOrigin.col) + ', row ' + Math.floor(viewportOrigin.row)"
           @wheel="onWheel"
           @mousemove="onViewportMouseMove"
           @mouseleave="hoveredCoord = null"
           @pointerdown="onViewportPointerDown"
           @pointermove="onViewportPointerMove"
           @pointerup="onViewportPointerUp"
           @pointercancel="onViewportPointerUp"
           @keydown="onKeydown">
        <div class="worker-grid-corner" :style="cornerStyle"></div>
        <div class="worker-grid-column-headers" :style="columnHeaderAreaStyle" @pointerdown.stop>
          <div v-for="c in visibleColumns" :key="c.col"
               class="worker-grid-column-header"
               :class="{
                 'is-origin': c.col === 0,
                 'is-selected': selectedCell && c.col === selectedCell.col,
               }"
               :style="{ left: c.x + 'px', width: columnWidth + 'px' }">
            <span class="worker-grid-header-label">{{ c.label }}</span>
            <div class="worker-grid-column-resize"
                 :class="{ active: columnResize }"
                 title="Drag to resize columns"
                 @pointerdown="onColumnResizeDown"
                 @click.stop
                 @dblclick.stop="resetColumnWidth"></div>
          </div>
        </div>
        <div class="worker-grid-row-headers" :style="rowHeaderAreaStyle" @pointerdown.stop>
          <div v-for="r in visibleRows" :key="r.row"
               class="worker-grid-row-header"
               :class="{
                 'is-origin': r.row === 0,
                 'is-selected': selectedCell && r.row === selectedCell.row,
               }"
               :style="{ top: r.y + 'px', height: rowHeight + 'px' }">
            <span class="worker-grid-header-label">{{ r.label }}</span>
            <div class="worker-grid-row-resize"
                 :class="{ active: rowResize }"
                 title="Drag to resize rows"
                 @pointerdown="onRowResizeDown"
                 @click.stop
                 @dblclick.stop="resetRowHeight"></div>
          </div>
        </div>
        <div class="worker-grid-canvas" :style="canvasStyle"
             @dragover="onCanvasDragOver"
             @dragleave="onCanvasDragLeave"
             @drop.prevent="onCanvasDrop">
          <WorkerCard
            v-for="item in visibleWorkers"
            :key="item.slotIndex"
            :ref="el => setWorkerRef(el, item.slotIndex)"
            :style="cardStyle(item)"
            :class="{ selected: isSelected(item.coord), 'worker-card--expanded': cardHeightForSlot(item.slotIndex) > rowHeight }"
            :worker="item.worker"
            :slot-index="item.slotIndex"
            :tasks="tasks"
            :task-by-id="taskById"
            :output-lines="$root.outputLinesForSlot(item.slotIndex, workspaceId)"
            :multiple-workspaces="multipleWorkspaces"
            :neighbor-slots="neighborSlotsMap[item.slotIndex]"
            :layout-mode="layoutMode"
            :card-height="cardHeightForSlot(item.slotIndex)"
            :is-selected="isSelected(item.coord)"
            :multiple-selection-active="isMultipleSelectionActive"
            :is-vertical-resizing="cardVerticalResize && cardVerticalResize.slotIndex === item.slotIndex"
            :workspace-id="workspaceId"
            :request-output-catchup="$root.requestOutputCatchup"
            :build-worker-drag-payload="buildWorkerDragPayload"
            :build-worker-drag-image="buildWorkerDragImage"
            :can-drop-worker-at-slot="canDropWorkerAtSlot"
            :drop-worker-on-slot="dropWorkerOnSlot"
            :update-singleton-worker-drag="updateSingletonWorkerDrag"
            :end-singleton-worker-drag="endSingletonWorkerDrag"
            :cancel-singleton-worker-drag="cancelSingletonWorkerDrag"
            :aria-rowindex="ariaRowIndex(item.coord)"
            :aria-colindex="ariaColIndex(item.coord)"
            :aria-label="'Worker ' + item.worker.name + ' at column ' + item.coord.col + ', row ' + item.coord.row"
            @click.capture="onWorkerClick($event, item)"
            @configure="$emit('configure-worker', $event)"
            @select-task="$emit('select-task', $event)"
            @open-focus="$emit('open-focus', $event)"
            @transfer="$emit('transfer-worker', $event)"
            @copy-worker="copyWorker"
            @delete-worker="deleteWorkerFromMenu"
            @vertical-resize-start="onCardVerticalResizeStart(item, $event)"
            @menu-opened="selectWorker(item, { preserveMultiple: true })"
            @menu-closed="focusViewport"
          />

          <div v-for="cell in visibleDropTargetOverlays"
               :key="'drop-' + cell.col + '-' + cell.row"
               class="worker-grid-drop-target-overlay"
               :style="cell.style"
               aria-hidden="true"></div>

          <div v-if="ghostCell"
               class="grid-slot empty-slot worker-grid-ghost-cell"
               :class="{ selected: isSelected(ghostCell), 'drag-over': isDragOverGhost(ghostCell) }"
               :style="ghostStyle"
               role="gridcell"
               tabindex="-1"
               :aria-rowindex="ariaRowIndex(ghostCell)"
               :aria-colindex="ariaColIndex(ghostCell)"
               :aria-label="'Empty cell at column ' + ghostCell.col + ', row ' + ghostCell.row"
               @click.stop="openEmptyMenu(ghostCell, $event)"
               @dragover="onEmptyDragOver($event, ghostCell)"
               @drop.stop.prevent="onDropOnEmpty($event, ghostCell)">
            <button class="empty-slot-menu-btn" draggable="false" title="Empty cell actions" @click.stop="openEmptyMenu(ghostCell, $event)">&hellip;</button>
          </div>
          <div v-if="ghostCell && emptyMenuOpenFor(ghostCell)"
               class="worker-menu empty-slot-menu"
               :style="emptyMenuStyle"
               ref="emptyMenu"
               tabindex="-1"
               @keydown="onEmptyMenuKeydown"
               @click.stop>
            <button class="worker-menu-item" @click="openLibraryForCoord(ghostCell)"><i class="menu-item-icon" data-lucide="user-plus" aria-hidden="true"></i><span class="menu-item-label">Add Worker</span></button>
            <button class="worker-menu-item" :disabled="!canPasteAt(ghostCell)" @click="pasteWorkerFromMenu(ghostCell)"><i class="menu-item-icon" data-lucide="clipboard" aria-hidden="true"></i><span class="menu-item-label">Paste Worker</span></button>
          </div>
        </div>

        <div class="worker-minimap" :class="{ collapsed: minimapCollapsed }">
          <button
            class="worker-minimap-toggle"
            @click="toggleMinimap"
            :title="minimapCollapsed ? 'Show minimap' : 'Hide minimap'"
            :aria-label="minimapCollapsed ? 'Show minimap' : 'Hide minimap'"
          >▣</button>
          <template v-if="!minimapCollapsed">
            <div class="worker-minimap-map" ref="minimap" @click="onMinimapClick">
              <div v-for="dot in minimapDots" :key="dot.key" class="worker-minimap-dot" :style="dot.style"></div>
              <div class="worker-minimap-viewport" :style="minimapViewportStyle"></div>
            </div>
            <div class="worker-minimap-arrows" aria-label="Pan worker grid">
              <button class="minimap-arrow minimap-arrow-up" @click="nudge(0, -1)" title="Pan up">↑</button>
              <button class="minimap-arrow minimap-arrow-left" @click="nudge(-1, 0)" title="Pan left">←</button>
              <button class="minimap-arrow minimap-arrow-right" @click="nudge(1, 0)" title="Pan right">→</button>
              <button class="minimap-arrow minimap-arrow-down" @click="nudge(0, 1)" title="Pan down">↓</button>
            </div>
          </template>
        </div>

        <div class="sr-only" aria-live="polite">{{ liveMessage }}</div>
      </div>

      <div v-if="resizeTooltip"
           class="worker-grid-resize-tooltip"
           :style="{ left: resizeTooltip.x + 'px', top: resizeTooltip.y + 'px' }">
        {{ resizeTooltip.text }}
      </div>

      <div
        v-if="showGoTo"
        class="modal-overlay"
        @click.self="closeGoTo"
        @keydown.escape="closeGoTo"
        tabindex="0"
      >
        <div class="modal" style="min-width: 320px;">
          <div class="modal-header">
            <h2>Go to</h2>
            <button class="btn btn-icon" @click="closeGoTo">&times;</button>
          </div>
          <div class="modal-body">
            <form @submit.prevent="submitGoTo">
              <label class="form-label">
                Cell address
                <input
                  ref="goToInputEl"
                  type="text"
                  v-model="goToInput"
                  class="form-input"
                  placeholder="AA122"
                  style="width: 100%; box-sizing: border-box;"
                  @input="onGoToCellInput"
                  @keydown.escape.stop="closeGoTo"
                />
              </label>
              <label class="form-label" style="margin-top: 10px;">
                Worker
                <select
                  v-model="goToWorkerSlot"
                  class="form-select"
                  style="width: 100%; box-sizing: border-box;"
                  @change="onGoToWorkerSelect"
                >
                  <option value="">Select a worker</option>
                  <option
                    v-for="item in goToWorkerOptions"
                    :key="item.slotIndex"
                    :value="String(item.slotIndex)"
                  >
                    {{ item.label }}
                  </option>
                </select>
              </label>
              <div v-if="goToError" style="color: var(--color-danger, #c33); margin-top: 6px; font-size: 12px;">
                {{ goToError }}
              </div>
              <div style="margin-top: 10px; display: flex; gap: 8px; justify-content: flex-end;">
                <button type="button" class="btn btn-sm" @click="closeGoTo">Cancel</button>
                <button type="submit" class="btn btn-sm btn-primary">Go</button>
              </div>
            </form>
          </div>
        </div>
      </div>

      <div
        v-if="showHelp"
        class="modal-overlay"
        @click.self="closeHelp"
        @keydown.escape="closeHelp"
        tabindex="0"
        ref="helpOverlay"
      >
        <div class="modal" style="min-width: 420px;">
          <div class="modal-header">
            <h2>Keyboard shortcuts</h2>
            <button class="btn btn-icon" @click="closeHelp">&times;</button>
          </div>
          <div class="modal-body">
            <table style="width: 100%; border-collapse: collapse;">
              <tbody>
                <tr v-for="row in helpShortcuts" :key="row.keys">
                  <td style="padding: 4px 12px 4px 0; white-space: nowrap; font-family: monospace;">{{ row.keys }}</td>
                  <td style="padding: 4px 0;">{{ row.desc }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div
        v-if="showLibrary"
        class="modal-overlay"
        @click.self="closeLibrary"
        @keydown.escape="closeLibrary"
        tabindex="0"
        ref="libraryOverlay"
      >
        <div class="modal worker-library-modal">
          <div class="modal-header">
            <h2>Add Worker</h2>
            <button class="btn btn-icon" @click="closeLibrary">&times;</button>
          </div>
          <div class="worker-type-tabs" role="tablist" aria-label="Worker type">
            <button class="worker-type-tab" :class="{ active: libraryMode === 'ai' }"
                    role="tab" :aria-selected="libraryMode === 'ai'"
                    @click="libraryMode = 'ai'">
              <i data-lucide="bot" aria-hidden="true"></i>
              <span>AI</span>
            </button>
            <button class="worker-type-tab" :class="{ active: libraryMode === 'shell' }"
                    role="tab" :aria-selected="libraryMode === 'shell'"
                    @click="libraryMode = 'shell'">
              <i data-lucide="terminal" aria-hidden="true"></i>
              <span>Shell</span>
            </button>
            <button class="worker-type-tab" :class="{ active: libraryMode === 'service' }"
                    role="tab" :aria-selected="libraryMode === 'service'"
                    @click="libraryMode = 'service'">
              <i data-lucide="server-cog" aria-hidden="true"></i>
              <span>Service</span>
            </button>
            <button class="worker-type-tab" :class="{ active: libraryMode === 'marker' }"
                    role="tab" :aria-selected="libraryMode === 'marker'"
                    @click="libraryMode = 'marker'">
              <i data-lucide="square-dot" aria-hidden="true"></i>
              <span>Marker</span>
            </button>
          </div>
          <div v-if="libraryMode === 'ai'" class="modal-body profile-library">
            <div v-for="p in sortedProfiles" :key="p.id"
                 class="profile-item"
                 @click="addFromLibrary(p.id)">
              <span class="profile-name">{{ p.name }}</span>
              <span class="profile-agent">{{ p.default_agent }}/{{ p.default_model }}</span>
            </div>
          </div>
          <div v-else-if="libraryMode === 'shell'" class="modal-body profile-library">
            <div class="profile-item profile-item--blank"
                 @click="addShellWorker()">
              <span class="profile-name">Blank shell worker</span>
              <span class="profile-agent">configure from scratch</span>
            </div>
            <div v-for="ex in platformShellExamples" :key="ex.id"
                 class="profile-item"
                 :title="ex.description"
                 @click="addShellWorker(ex)">
              <span class="profile-name">{{ ex.name }}</span>
              <span class="profile-agent">{{ ex.description }}</span>
            </div>
          </div>
          <div v-else-if="libraryMode === 'service'" class="modal-body profile-library">
            <div class="profile-item profile-item--blank"
                 @click="addServiceWorker()">
              <span class="profile-name">Blank service worker</span>
              <span class="profile-agent">long-running process</span>
            </div>
          </div>
          <div v-else-if="libraryMode === 'marker'" class="modal-body profile-library">
            <div class="profile-item profile-item--blank"
                 @click="addMarkerWorker()">
              <span class="profile-name">Blank marker worker</span>
              <span class="profile-agent">label, jump target, pass-through</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  `,
  computed: {
    helpShortcuts() {
      const mod = (navigator.platform || '').toLowerCase().includes('mac') ? '⌘' : 'Ctrl';
      return [
        { keys: '?', desc: 'Show this help' },
        { keys: 'Arrow keys', desc: 'Move selection between cells' },
        { keys: 'Enter', desc: 'Open worker menu (or empty-cell menu)' },
        { keys: 'Delete / Backspace', desc: 'Remove selected worker' },
        { keys: 'Home', desc: 'Jump to origin (A1)' },
        { keys: 'F', desc: 'Fit view to occupied cells' },
        { keys: `${mod}+G`, desc: 'Go to worker or cell (e.g. AA122)' },
        { keys: `${mod}+C`, desc: 'Copy selected worker' },
        { keys: `${mod}+V`, desc: 'Paste worker into selected cell' },
        { keys: 'Escape', desc: 'Close open menu or modal' },
      ];
    },
    gridConfig() { return this.config?.grid || {}; },
    legacyCols() {
      const n = Number(this.gridConfig.cols);
      return Number.isFinite(n) && n > 0 ? Math.floor(n) : 4;
    },
    layoutMode() {
      return this.rowHeight < 40 ? 'small' : 'medium';
    },
    columnWidth() {
      if (this.draggingColumnWidth !== null) {
        return this.clampColumnWidth(this.draggingColumnWidth);
      }
      if (this.pendingColumnWidth !== null) {
        return this.clampColumnWidth(this.pendingColumnWidth);
      }
      const raw = Number(this.gridConfig.columnWidth);
      const n = Number.isFinite(raw) ? raw : 220;
      return this.clampColumnWidth(n);
    },
    headerWidth() { return this.$options.HEADER_WIDTH; },
    headerHeight() { return this.$options.HEADER_HEIGHT; },
    rowHeight() {
      if (this.draggingRowHeight !== null) {
        return this.clampRowHeight(this.draggingRowHeight);
      }
      if (this.pendingRowHeight !== null) {
        return this.clampRowHeight(this.pendingRowHeight);
      }
      const raw = Number(this.gridConfig.rowHeight);
      if (Number.isFinite(raw)) {
        return this.clampRowHeight(raw);
      }
      return 140;
    },
    cardSize() {
      return { width: this.columnWidth, height: this.rowHeight };
    },
    workerItems() {
      const slots = this.layout?.slots || [];
      return slots.map((worker, slotIndex) => {
        if (!worker) return null;
        return { worker, slotIndex, coord: this.coordForSlot(worker, slotIndex) };
      }).filter(Boolean);
    },
    goToWorkerOptions() {
      return this.workerItems.map(item => ({
        slotIndex: item.slotIndex,
        label: `${item.worker?.name || `Slot ${item.slotIndex + 1}`} (${this.colLabel(item.coord.col)}${this.rowLabel(item.coord.row)})`,
      }));
    },
    workerItemBySlot() {
      const map = {};
      for (const item of this.workerItems) map[item.slotIndex] = item;
      return map;
    },
    occupiedMap() {
      const map = {};
      for (const item of this.workerItems) map[GridGeometry.coordKey(item.coord.col, item.coord.row)] = item;
      return map;
    },
    visibleRange() {
      const range = GridGeometry.visibleRange(this.viewportOrigin, this.viewportPx, this.cardSize);
      return GridGeometry.overscanRange(range, 2);
    },
    visibleWorkers() {
      const r = this.visibleRange;
      return this.workerItems.filter(item =>
        item.coord.col >= r.colStart && item.coord.col <= r.colEnd &&
        item.coord.row >= r.rowStart && item.coord.row <= r.rowEnd
      );
    },
    canvasStyle() {
      const x = -((this.viewportOrigin.col % 1) * this.columnWidth);
      const y = -((this.viewportOrigin.row % 1) * this.rowHeight);
      return {
        '--worker-col-width': this.columnWidth + 'px',
        '--worker-row-height': this.rowHeight + 'px',
        left: this.headerWidth + 'px',
        top: this.headerHeight + 'px',
        backgroundPosition: `${x}px ${y}px`,
      };
    },
    cornerStyle() {
      return {
        width: this.headerWidth + 'px',
        height: this.headerHeight + 'px',
      };
    },
    columnHeaderAreaStyle() {
      return {
        left: this.headerWidth + 'px',
        height: this.headerHeight + 'px',
      };
    },
    rowHeaderAreaStyle() {
      return {
        top: this.headerHeight + 'px',
        width: this.headerWidth + 'px',
      };
    },
    visibleColumns() {
      const r = this.visibleRange;
      const out = [];
      for (let c = Math.max(0, r.colStart); c <= r.colEnd; c++) {
        out.push({
          col: c,
          label: this.colLabel(c),
          x: (c - this.viewportOrigin.col) * this.columnWidth,
        });
      }
      return out;
    },
    visibleRows() {
      const r = this.visibleRange;
      const out = [];
      for (let rr = Math.max(0, r.rowStart); rr <= r.rowEnd; rr++) {
        out.push({
          row: rr,
          label: this.rowLabel(rr),
          y: (rr - this.viewportOrigin.row) * this.rowHeight,
        });
      }
      return out;
    },
    ghostCell() {
      const coord = this.dragOverCoord || this.emptyMenuCoord || this.selectedCell || this.hoveredCoord;
      if (!coord || !this.isWritableCoord(coord) || this.itemAtCoord(coord)) return null;
      const r = this.visibleRange;
      if (coord.col < r.colStart || coord.col > r.colEnd || coord.row < r.rowStart || coord.row > r.rowEnd) return null;
      return coord;
    },
    ghostStyle() {
      if (!this.ghostCell) return {};
      const p = GridGeometry.coordToPixel(this.ghostCell.col, this.ghostCell.row, this.viewportOrigin, this.cardSize);
      return this.insetBoxStyle(p.x, p.y, this.columnWidth, this.rowHeight);
    },
    visibleDropTargetOverlays() {
      const coords = this.dropTargetCoords;
      if (!coords || !coords.length) return [];
      const r = this.visibleRange;
      const out = [];
      for (const c of coords) {
        if (!c) continue;
        if (c.col < r.colStart || c.col > r.colEnd || c.row < r.rowStart || c.row > r.rowEnd) continue;
        const p = GridGeometry.coordToPixel(c.col, c.row, this.viewportOrigin, this.cardSize);
        out.push({
          col: c.col,
          row: c.row,
          style: this.insetBoxStyle(p.x, p.y, this.columnWidth, this.rowHeight),
        });
      }
      return out;
    },
    emptyMenuStyle() {
      if (this.emptyMenuPos) {
        return { position: 'fixed', top: this.emptyMenuPos.y + 'px', left: this.emptyMenuPos.x + 'px' };
      }
      if (!this.ghostCell) return {};
      const p = GridGeometry.coordToPixel(this.ghostCell.col, this.ghostCell.row, this.viewportOrigin, this.cardSize);
      const rect = this.$refs.viewport?.getBoundingClientRect();
      const x = (rect?.left || 0) + this.headerWidth + p.x + this.columnWidth / 2;
      const y = (rect?.top || 0) + this.headerHeight + p.y + this.rowHeight / 2;
      return { position: 'fixed', top: y + 'px', left: x + 'px' };
    },
    sortedProfiles() {
      const pin = this.$options.UNCONFIGURED_PROFILE_ID;
      return (this.profiles || []).slice().sort((a, b) => {
        if (a?.id === pin && b?.id !== pin) return -1;
        if (b?.id === pin && a?.id !== pin) return 1;
        return (a?.name || '').localeCompare(b?.name || '');
      });
    },
    platformShellExamples() {
      const isWin = (navigator.platform || '').toLowerCase().includes('win');
      const current = isWin ? 'windows' : 'posix';
      return (this.shellExamples || []).filter(ex => !ex.platforms || ex.platforms.includes(current));
    },
    neighborSlotsMap() {
      const map = {};
      for (const item of this.workerItems) {
        const c = item.coord;
        map[item.slotIndex] = {
          up: this.itemAtCoord({ col: c.col, row: c.row - 1 })?.slotIndex ?? null,
          down: this.itemAtCoord({ col: c.col, row: c.row + 1 })?.slotIndex ?? null,
          left: this.itemAtCoord({ col: c.col - 1, row: c.row })?.slotIndex ?? null,
          right: this.itemAtCoord({ col: c.col + 1, row: c.row })?.slotIndex ?? null,
        };
      }
      return map;
    },
    passTargetsBySlot() {
      const map = {};
      for (const item of this.workerItems) {
        const disposition = String(item.worker?.disposition || '');
        if (!disposition.startsWith('pass:')) {
          map[item.slotIndex] = [];
          continue;
        }
        const passDir = disposition.slice(5);
        const neighbors = this.neighborSlotsMap[item.slotIndex] || {};
        if (['up', 'down', 'left', 'right'].includes(passDir)) {
          const target = neighbors[passDir];
          map[item.slotIndex] = Number.isInteger(target) ? [target] : [];
          continue;
        }
        if (passDir === 'random') {
          const out = [];
          for (const dir of ['up', 'down', 'left', 'right']) {
            const target = neighbors[dir];
            if (Number.isInteger(target) && !out.includes(target)) out.push(target);
          }
          map[item.slotIndex] = out;
          continue;
        }
        map[item.slotIndex] = [];
      }
      return map;
    },
    passSourcesBySlot() {
      const map = {};
      for (const item of this.workerItems) map[item.slotIndex] = [];
      for (const item of this.workerItems) {
        for (const target of this.passTargetsBySlot[item.slotIndex] || []) {
          if (!map[target]) map[target] = [];
          if (!map[target].includes(item.slotIndex)) map[target].push(item.slotIndex);
        }
      }
      return map;
    },
    selectedWorkerSlot() {
      const coord = this.selectedCell;
      return coord ? this.itemAtCoord(coord)?.slotIndex ?? null : null;
    },
    isMultipleSelectionActive() {
      return Array.isArray(this.selectedWorkerSlots) && this.selectedWorkerSlots.length > 1;
    },
    occupiedBounds() {
      return GridGeometry.occupiedBounds(this.workerItems.map(item => item.coord));
    },
    minimapBounds() {
      const b = this.occupiedBounds;
      const visible = GridGeometry.visibleRange(this.viewportOrigin, this.viewportPx, this.cardSize);
      const colMin = Math.max(0, Math.min(b?.colMin ?? 0, visible.colStart) - 2);
      const colMax = Math.max(b?.colMax ?? 3, visible.colEnd) + 2;
      const rowMin = Math.max(0, Math.min(b?.rowMin ?? 0, visible.rowStart) - 2);
      const rowMax = Math.max(b?.rowMax ?? 4, visible.rowEnd) + 2;
      return { colMin, colMax, rowMin, rowMax };
    },
    minimapScale() {
      const b = this.minimapBounds;
      const cols = Math.max(1, b.colMax - b.colMin + 1);
      const rows = Math.max(1, b.rowMax - b.rowMin + 1);
      const headerFrac = this.$options.MINIMAP_HEADER_PX / Math.max(1, this.columnWidth);
      const scaleX = Math.min(160 / cols, 120 / (rows * headerFrac));
      const scaleY = scaleX * headerFrac;
      return { x: scaleX, y: scaleY };
    },
    minimapDots() {
      const b = this.minimapBounds;
      const { x: sx, y: sy } = this.minimapScale;
      const w = Math.max(2, sx - 0.5);
      const h = Math.max(2, sy - 0.5);
      const radius = Math.max(1, Math.min(2, Math.min(w, h) * 0.3));
      return this.workerItems.map(item => ({
        key: item.slotIndex,
        style: {
          left: ((item.coord.col - b.colMin) * sx) + 'px',
          top: ((item.coord.row - b.rowMin) * sy) + 'px',
          width: w + 'px',
          height: h + 'px',
          borderRadius: radius + 'px',
          background: workerColor(item.worker),
        },
      }));
    },
    minimapViewportStyle() {
      const b = this.minimapBounds;
      const scale = this.minimapScale;
      return {
        left: ((this.viewportOrigin.col - b.colMin) * scale.x) + 'px',
        top: ((this.viewportOrigin.row - b.rowMin) * scale.y) + 'px',
        width: Math.max(2, (this.viewportPx.width / this.columnWidth) * scale.x) + 'px',
        height: Math.max(2, (this.viewportPx.height / this.rowHeight) * scale.y) + 'px',
      };
    },
  },
  watch: {
    rowHeight(next) {
      const clamped = Math.max(0, Math.min(this.expandedWorkerCardDelta, Math.max(0, 480 - next)));
      if (clamped !== this.expandedWorkerCardDelta) this.expandedWorkerCardDelta = clamped;
      if (clamped === 0 && this.expandedWorkerCardSlot !== null && !this.cardVerticalResize) {
        this.expandedWorkerCardSlot = null;
      }
    },
    selectedWorkerSlot(next) {
      if (next === this.expandedWorkerCardSlot) return;
      if (this.cardVerticalResize) return;
      this.clearExpandedWorkerCard();
    },
    workspaceId() {
      this.restoreWorkspaceViewportOrigin();
    },
    workspace: {
      handler() {
        this.selectA1();
      },
    },
    gridConfig: {
      deep: true,
      handler() {
        this.reconcilePendingGridSize();
      },
    },
  },
  mounted() {
    this.updateViewportSize();
    this.restoreWorkspaceViewportOrigin();
    this._resizeObserver = new ResizeObserver(() => this.updateViewportSize());
    if (this.$refs.viewport) this._resizeObserver.observe(this.$refs.viewport);
    this.selectA1();
    renderLucideIcons(this.$el);
  },
  updated() {
    this.$nextTick(() => renderLucideIcons(this.$el));
  },
  beforeUnmount() {
    this._resizeObserver?.disconnect();
    this._teardownColumnResizeListeners?.();
    this._teardownRowResizeListeners?.();
    this._teardownCardVerticalResizeListeners?.();
  },
  methods: {
    clampColumnWidth(value) {
      const n = Number(value);
      const base = Number.isFinite(n) ? n : 220;
      return Math.max(140, Math.min(480, Math.round(base / 20) * 20));
    },
    clampRowHeight(value) {
      const n = Number(value);
      const base = Number.isFinite(n) ? n : 140;
      return Math.max(32, Math.min(480, Math.round(base)));
    },
    reconcilePendingGridSize() {
      if (this.pendingColumnWidth !== null) {
        const raw = Number(this.gridConfig.columnWidth);
        if (Number.isFinite(raw) && this.clampColumnWidth(raw) === this.clampColumnWidth(this.pendingColumnWidth)) {
          this.pendingColumnWidth = null;
        }
      }
      if (this.pendingRowHeight !== null) {
        const raw = Number(this.gridConfig.rowHeight);
        if (Number.isFinite(raw) && this.clampRowHeight(raw) === this.clampRowHeight(this.pendingRowHeight)) {
          this.pendingRowHeight = null;
        }
      }
    },
    coordForSlot(worker, slotIndex) {
      const col = Number(worker?.col);
      const row = Number(worker?.row);
      if (Number.isFinite(col) && Number.isFinite(row)) return { col: Math.trunc(col), row: Math.trunc(row) };
      return GridGeometry.indexToCoord(slotIndex, this.legacyCols);
    },
    itemAtCoord(coord) {
      return this.occupiedMap[GridGeometry.coordKey(coord.col, coord.row)] || null;
    },
    isWritableCoord(coord) {
      const limit = GridGeometry.DEFAULT_COORD_LIMIT;
      return coord && coord.col >= 0 && coord.col <= limit && coord.row >= 0 && coord.row <= limit;
    },
    clampedOrigin(origin) {
      return GridGeometry.clampOriginToBounds(origin, this.viewportPx, this.cardSize);
    },
    updateViewportSize() {
      const el = this.$refs.viewport;
      if (!el) return;
      this.viewportPx = {
        width: Math.max(0, el.clientWidth - this.headerWidth),
        height: Math.max(0, el.clientHeight - this.headerHeight),
      };
      this.viewportOrigin = this.clampedOrigin(this.viewportOrigin);
    },
    viewportOriginFromConfig() {
      const value = this.config?.grid?.viewportOrigin;
      return this.clampedOrigin({
        col: Number.isFinite(Number(value?.col)) ? Number(value.col) : 0,
        row: Number.isFinite(Number(value?.row)) ? Number(value.row) : 0,
      });
    },
    restoreWorkspaceViewportOrigin() {
      const key = this.workspaceId || '__default__';
      const cached = this.workspaceViewportOrigins[key];
      this.viewportOrigin = cached
        ? this.clampedOrigin(cached)
        : this.viewportOriginFromConfig();
    },
    rememberWorkspaceViewportOrigin() {
      const key = this.workspaceId || '__default__';
      this.workspaceViewportOrigins[key] = { ...this.viewportOrigin };
    },
    persistGrid(partial = {}) {
      const grid = {
        ...(this.config?.grid || {}),
        columnWidth: this.columnWidth,
        rowHeight: this.rowHeight,
        ...partial,
      };
      delete grid.viewportOrigin;
      this.$root.updateConfig({ grid });
    },
    cardExpansionLimit() {
      return Math.max(0, 480 - this.rowHeight);
    },
    cardExpansionDeltaForSlot(slotIndex) {
      if (this.expandedWorkerCardSlot !== slotIndex) return 0;
      return Math.max(0, Math.min(this.expandedWorkerCardDelta, this.cardExpansionLimit()));
    },
    cardHeightForSlot(slotIndex) {
      return this.rowHeight + this.cardExpansionDeltaForSlot(slotIndex);
    },
    insetBoxStyle(x, y, width, height) {
      const minWidth = 64;
      const minHeight = 24;
      const insetX = Math.min(1, Math.max(0, (width - minWidth) / 2));
      const insetY = Math.min(1, Math.max(0, (height - minHeight) / 2));
      return {
        left: (x + insetX) + 'px',
        top: (y + insetY) + 'px',
        width: Math.max(minWidth, width - insetX * 2) + 'px',
        height: Math.max(minHeight, height - insetY * 2) + 'px',
      };
    },
    cardStyle(item) {
      const p = GridGeometry.coordToPixel(item.coord.col, item.coord.row, this.viewportOrigin, this.cardSize);
      const expanded = this.cardExpansionDeltaForSlot(item.slotIndex);
      return {
        position: 'absolute',
        ...this.insetBoxStyle(p.x, p.y, this.columnWidth, this.cardHeightForSlot(item.slotIndex)),
        zIndex: expanded > 0 ? 6 : null,
      };
    },
    setOrigin(origin, persist = true) {
      this.viewportOrigin = this.clampedOrigin(origin);
      if (persist) this.rememberWorkspaceViewportOrigin();
    },
    nudge(dc, dr) {
      this.setOrigin({ col: this.viewportOrigin.col + dc, row: this.viewportOrigin.row + dr });
    },
    jumpHome() {
      this.setOrigin({ col: 0, row: 0 });
    },
    fitOccupied() {
      const b = this.occupiedBounds;
      if (!b) {
        this.jumpHome();
        return;
      }
      this.setOrigin({ col: b.colMin - 1, row: b.rowMin - 1 });
    },
    onWheel(e) {
      e.preventDefault();
      e.stopPropagation();
      const dx = e.shiftKey ? e.deltaY : e.deltaX;
      const dy = e.shiftKey ? 0 : e.deltaY;
      this.setOrigin({
        col: this.viewportOrigin.col + dx / this.columnWidth,
        row: this.viewportOrigin.row + dy / this.rowHeight,
      });
    },
    coordFromEvent(e) {
      const rect = this.dragViewportRect || this.$refs.viewport.getBoundingClientRect();
      const x = e.clientX - rect.left - this.headerWidth;
      const y = e.clientY - rect.top - this.headerHeight;
      return GridGeometry.pixelToCoord(x, y, this.viewportOrigin, this.cardSize);
    },
    onViewportMouseMove(e) {
      if (this.isPanning) return;
      const coord = this.coordFromEvent(e);
      this.hoveredCoord = this.itemAtCoord(coord) ? null : coord;
    },
    onViewportPointerDown(e) {
      if (e.button !== 0 && e.button !== 1) return;
      if (e.target.closest('.worker-menu, button, input, select, textarea')) return;
      if (e.target.closest('.worker-card') && e.pointerType !== 'touch') return;
      const coord = this.coordFromEvent(e);
      const isTouch = e.pointerType === 'touch';
      this.dragStart = {
        x: e.clientX,
        y: e.clientY,
        button: e.button,
        selection: e.button === 0 && !isTouch,
        selectionMoved: false,
        origin: { ...this.viewportOrigin },
        coord,
      };
      if (this.dragStart.selection) {
        this.selectionAnchor = e.shiftKey && this.selectionAnchor ? { ...this.selectionAnchor } : { ...coord };
        this.updateRangeSelection(this.selectionAnchor, coord);
      }
      this.$refs.viewport.setPointerCapture?.(e.pointerId);
    },
    onViewportPointerMove(e) {
      if (!this.dragStart) return;
      const dx = e.clientX - this.dragStart.x;
      const dy = e.clientY - this.dragStart.y;
      if (!this.isPanning && Math.hypot(dx, dy) <= 5) return;
      if (this.dragStart.selection) {
        const coord = this.coordFromEvent(e);
        this.dragStart.selectionMoved = true;
        this.updateRangeSelection(this.selectionAnchor || this.dragStart.coord, coord);
        this.ensureCoordVisible(coord);
        return;
      }
      this.isPanning = true;
      this.setOrigin({
        col: this.dragStart.origin.col - dx / this.columnWidth,
        row: this.dragStart.origin.row - dy / this.rowHeight,
      });
    },
    onViewportPointerUp(e) {
      if (!this.dragStart) return;
      const wasPanning = this.isPanning;
      const coord = this.dragStart.coord;
      const selectionMoved = this.dragStart.selectionMoved;
      this.dragStart = null;
      this.isPanning = false;
      this.$refs.viewport.releasePointerCapture?.(e.pointerId);
      if (wasPanning) {
        window._bullpenSuppressWorkerClickUntil = Date.now() + 250;
      }
      if (selectionMoved) {
        this.focusViewport();
        return;
      }
      if (!wasPanning && !this.itemAtCoord(coord) && this.isWritableCoord(coord)) {
        this.selectCell(coord);
        this.focusViewport();
      }
    },
    selectWorker(item, options = {}) {
      this.selectedCell = item.coord;
      this.selectionAnchor = { ...item.coord };
      if (!(options && options.preserveMultiple && this.selectedWorkerSlots.includes(item.slotIndex))) {
        this.selectedWorkerSlots = this.expandSelectionSlots([item.slotIndex]);
      }
      this.emptyMenuCoord = null;
      this.liveMessage = `Selected worker ${item.worker.name} at column ${item.coord.col}, row ${item.coord.row}`;
    },
    selectCell(coord) {
      this.selectedCell = { ...coord };
      this.selectionAnchor = { ...coord };
      const item = this.itemAtCoord(coord);
      this.selectedWorkerSlots = item ? this.expandSelectionSlots([item.slotIndex]) : [];
      this.emptyMenuCoord = null;
      this.emptyMenuPos = null;
      this.liveMessage = item
        ? `Selected worker ${item.worker.name} at column ${coord.col}, row ${coord.row}`
        : `Empty cell at column ${coord.col}, row ${coord.row}`;
    },
    selectA1() {
      this.selectedCell = { col: 0, row: 0 };
      this.selectionAnchor = { col: 0, row: 0 };
      this.selectedWorkerSlots = [];
      this.emptyMenuCoord = null;
      this.emptyMenuPos = null;
      this.focusViewport();
    },
    onWorkerClick(e, item) {
      if (window._bullpenSuppressWorkerClickUntil && Date.now() < window._bullpenSuppressWorkerClickUntil) return;
      if (e.target.closest('.card-height-resize-handle, .connect-handle, .status-pill, .worker-menu-btn, .worker-menu, button, input, select, textarea')) {
        return;
      }
      if (e.shiftKey) {
        const anchor = this.selectionAnchor || this.selectedCell || item.coord;
        this.updateRangeSelection(anchor, item.coord);
      } else if (e.metaKey || e.ctrlKey) {
        this.toggleWorkerSelection(item);
      } else {
        this.selectWorker(item);
      }
      this.focusViewport();
    },
    clearExpandedWorkerCard() {
      this.expandedWorkerCardSlot = null;
      this.expandedWorkerCardDelta = 0;
    },
    focusViewport() {
      this.$nextTick(() => {
        const el = this.$refs.viewport;
        if (el && document.activeElement !== el) el.focus({ preventScroll: true });
      });
    },
    isSelected(coord) {
      if (!coord) return false;
      const item = this.itemAtCoord(coord);
      if (item && this.selectedWorkerSlots.includes(item.slotIndex)) return true;
      return !!(this.selectedCell && this.selectedCell.col === coord.col && this.selectedCell.row === coord.row);
    },
    expandSelectionSlots(slots) {
      const out = new Set();
      for (const raw of slots || []) {
        const slot = Number(raw);
        if (!Number.isInteger(slot) || !this.workerItemBySlot[slot]) continue;
        for (const member of this.workerGroupSlots(slot)) out.add(member);
      }
      return Array.from(out);
    },
    slotsInRange(a, b) {
      if (!a || !b) return [];
      const colMin = Math.min(a.col, b.col);
      const colMax = Math.max(a.col, b.col);
      const rowMin = Math.min(a.row, b.row);
      const rowMax = Math.max(a.row, b.row);
      return this.workerItems
        .filter(item => item.coord.col >= colMin && item.coord.col <= colMax && item.coord.row >= rowMin && item.coord.row <= rowMax)
        .map(item => item.slotIndex);
    },
    updateRangeSelection(anchor, active) {
      if (!anchor || !active || !this.isWritableCoord(active)) return;
      this.selectionAnchor = { ...anchor };
      this.selectedCell = { ...active };
      this.emptyMenuCoord = null;
      this.emptyMenuPos = null;
      this.selectedWorkerSlots = this.expandSelectionSlots(this.slotsInRange(anchor, active));
      const count = this.selectedWorkerSlots.length;
      this.liveMessage = count > 1
        ? `Selected ${count} workers`
        : `Selected range ending at column ${active.col}, row ${active.row}`;
    },
    toggleWorkerSelection(item) {
      const current = new Set(this.selectedWorkerSlots);
      const group = this.workerGroupSlots(item.slotIndex);
      const selected = group.some(slot => current.has(slot));
      for (const slot of group) {
        if (selected) current.delete(slot);
        else current.add(slot);
      }
      this.selectedCell = { ...item.coord };
      this.selectionAnchor = { ...item.coord };
      this.selectedWorkerSlots = Array.from(current);
      this.emptyMenuCoord = null;
      this.emptyMenuPos = null;
      this.liveMessage = this.selectedWorkerSlots.length > 1
        ? `Selected ${this.selectedWorkerSlots.length} workers`
        : `Selected worker ${item.worker.name} at column ${item.coord.col}, row ${item.coord.row}`;
    },
    isDragOverGhost(coord) {
      return !!(this.dragOverCoord && coord &&
        this.dragOverCoord.col === coord.col &&
        this.dragOverCoord.row === coord.row);
    },
    onKeydown(e) {
      const t = e.target;
      const inTextInput = t && (t.isContentEditable || (typeof t.matches === 'function' && t.matches('input, textarea, select')));
      if (this.emptyMenuCoord && !inTextInput && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (['ArrowUp', 'ArrowDown', 'Home', 'End', 'Escape'].includes(e.key)) {
          this.onEmptyMenuKeydown(e);
          return;
        }
      }
      if (!inTextInput && !e.metaKey && !e.ctrlKey && !e.altKey && e.key === '?') {
        e.preventDefault();
        this.openHelp();
        return;
      }
      if (!inTextInput && (e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'g') {
        e.preventDefault();
        this.openGoTo();
        return;
      }
      if (!inTextInput && (e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'c') {
        const item = this.selectedWorkerSlots.length
          ? this.workerItemBySlot[this.selectedWorkerSlots[0]]
          : this.itemAtCoord(this.selectedCell);
        if (!item) return;
        e.preventDefault();
        this.copyWorker(item.slotIndex);
        return;
      }
      if (!inTextInput && (e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'v') {
        if (!this.selectedCell || !this.clipboardWorker || !this.isWritableCoord(this.selectedCell)) return;
        e.preventDefault();
        this.pasteWorker(this.selectedCell, { allowReplaceSingle: true });
        return;
      }
      if (!inTextInput && !e.metaKey && !e.ctrlKey && (e.key === 'Delete' || e.key === 'Backspace')) {
        if (this.isMultipleSelectionActive) return;
        if (!this.selectedCell) return;
        const item = this.itemAtCoord(this.selectedCell);
        if (!item) return;
        e.preventDefault();
        this.$root.removeWorker(item.slotIndex);
        return;
      }
      if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
        const step = {
          ArrowUp: { dc: 0, dr: -1, dir: 'up' },
          ArrowDown: { dc: 0, dr: 1, dir: 'down' },
          ArrowLeft: { dc: -1, dr: 0, dir: 'left' },
          ArrowRight: { dc: 1, dr: 0, dir: 'right' },
        }[e.key];
        const origin = this.selectedCell || {
          col: Math.floor(this.viewportOrigin.col + (this.viewportPx.width / this.columnWidth) / 2),
          row: Math.floor(this.viewportOrigin.row + (this.viewportPx.height / this.rowHeight) / 2),
        };
        const next = { col: origin.col + step.dc, row: origin.row + step.dr };
        if (!this.isWritableCoord(next)) {
          this.flashBoundary(step.dir);
          return;
        }
        e.preventDefault();
        if (e.shiftKey) {
          const anchor = this.selectionAnchor || origin;
          this.updateRangeSelection(anchor, next);
          this.ensureCoordVisible(next);
          return;
        }
        const item = this.itemAtCoord(next);
        if (item) {
          this.selectWorker(item);
        } else {
          this.selectCell(next);
        }
        this.ensureCoordVisible(next);
        return;
      }
      if (e.key === 'Home') {
        e.preventDefault();
        this.jumpHome();
      } else if (e.key.toLowerCase() === 'f') {
        e.preventDefault();
        this.fitOccupied();
      } else if (e.key === 'Escape') {
        if (this.emptyMenuCoord) {
          e.preventDefault();
          this.closeEmptyMenu({ focusViewport: true });
        }
      } else if (e.key === 'Enter') {
        const coord = this.selectedCell || this.ghostCell;
        if (!coord) return;
        const item = this.itemAtCoord(coord);
        if (item) {
          e.preventDefault();
          const card = this.workerRefs && this.workerRefs[item.slotIndex];
          if (card && typeof card.openMenuAndFocus === 'function') {
            card.openMenuAndFocus();
          }
        } else {
          e.preventDefault();
          this.openEmptyMenu(coord);
        }
      }
    },
    setWorkerRef(el, slotIndex) {
      if (!this.workerRefs) this.workerRefs = {};
      if (el) {
        this.workerRefs[slotIndex] = el;
      } else {
        delete this.workerRefs[slotIndex];
      }
    },
    openGoTo() {
      this.showGoTo = true;
      this.goToInput = '';
      this.goToWorkerSlot = '';
      this.goToError = '';
      this.$nextTick(() => this.$refs.goToInputEl?.focus());
    },
    closeGoTo() {
      this.showGoTo = false;
      this.goToWorkerSlot = '';
      this.goToError = '';
      this.$nextTick(() => this.$refs.viewport?.focus());
    },
    onGoToCellInput() {
      this.goToError = '';
      if ((this.goToInput || '').trim()) this.goToWorkerSlot = '';
    },
    onGoToWorkerSelect() {
      this.goToError = '';
      if (this.goToWorkerSlot) this.goToInput = '';
    },
    openHelp() {
      this.showHelp = true;
    },
    closeHelp() {
      this.showHelp = false;
      this.$nextTick(() => this.$refs.viewport?.focus());
    },
    openHelp() {
      this.showHelp = true;
      this.$nextTick(() => this.$refs.helpOverlay?.focus());
    },
    closeHelp() {
      this.showHelp = false;
      this.$nextTick(() => this.$refs.viewport?.focus());
    },
    parseCellRef(text) {
      const m = /^\s*([A-Za-z]+)\s*(\d+)\s*$/.exec(text);
      if (!m) return null;
      let col = 0;
      for (const ch of m[1].toUpperCase()) {
        col = col * 26 + (ch.charCodeAt(0) - 64);
      }
      col -= 1;
      const row = parseInt(m[2], 10) - 1;
      if (col < 0 || row < 0) return null;
      return { col, row };
    },
    goToCoord(coord) {
      const visibleCols = this.viewportPx.width / this.columnWidth;
      const visibleRows = this.viewportPx.height / this.rowHeight;
      const isVisible =
        coord.col + 1 > this.viewportOrigin.col &&
        coord.col < this.viewportOrigin.col + visibleCols &&
        coord.row + 1 > this.viewportOrigin.row &&
        coord.row < this.viewportOrigin.row + visibleRows;
      if (!isVisible) {
        this.setOrigin({ col: coord.col, row: coord.row });
      }
      const item = this.itemAtCoord(coord);
      if (item) {
        this.selectWorker(item);
      } else {
        this.selectedCell = { ...coord };
        this.emptyMenuCoord = null;
        this.emptyMenuPos = null;
      }
    },
    submitGoTo() {
      if (this.goToWorkerSlot !== '') {
        const slot = Number.parseInt(this.goToWorkerSlot, 10);
        const selected = this.workerItemBySlot[slot];
        if (selected) {
          this.goToCoord(selected.coord);
          this.closeGoTo();
          return;
        }
      }
      const text = (this.goToInput || '').trim();
      if (!text) { this.closeGoTo(); return; }
      const cellRef = this.parseCellRef(text);
      if (cellRef && this.isWritableCoord(cellRef)) {
        this.goToCoord(cellRef);
        this.closeGoTo();
        return;
      }
      const needle = text.toLowerCase();
      let match = this.workerItems.find(it => (it.worker?.name || '').toLowerCase() === needle);
      if (!match) {
        match = this.workerItems.find(it => (it.worker?.name || '').toLowerCase().startsWith(needle));
      }
      if (!match) {
        match = this.workerItems.find(it => (it.worker?.name || '').toLowerCase().includes(needle));
      }
      if (match) {
        this.goToCoord(match.coord);
        this.closeGoTo();
        return;
      }
      this.goToError = `No worker or cell matches "${text}"`;
    },
    ensureCoordVisible(coord) {
      let col = this.viewportOrigin.col;
      let row = this.viewportOrigin.row;
      const visibleCols = this.viewportPx.width / this.columnWidth;
      const visibleRows = this.viewportPx.height / this.rowHeight;
      if (coord.col < col) col = coord.col;
      if (coord.row < row) row = coord.row;
      if (coord.col + 1 > col + visibleCols) col = coord.col + 1 - visibleCols;
      if (coord.row + 1 > row + visibleRows) row = coord.row + 1 - visibleRows;
      this.setOrigin({ col, row });
    },
    flashBoundary(dir) {
      const el = this.$refs.viewport;
      if (!el) return;
      el.classList.remove('boundary-up', 'boundary-down', 'boundary-left', 'boundary-right');
      void el.offsetWidth;
      el.classList.add('boundary-' + dir);
      setTimeout(() => el.classList.remove('boundary-' + dir), 260);
    },
    ariaRowIndex(coord) {
      return Math.max(1, Math.floor(coord.row - this.visibleRange.rowStart + 1));
    },
    ariaColIndex(coord) {
      return Math.max(1, Math.floor(coord.col - this.visibleRange.colStart + 1));
    },
    emptyMenuItems() {
      const menu = this.$refs.emptyMenu;
      if (!menu || typeof menu.querySelectorAll !== 'function') return [];
      return Array.from(menu.querySelectorAll('.worker-menu-item:not([disabled])'));
    },
    closeEmptyMenu(options = {}) {
      const focusViewport = options && options.focusViewport === true;
      this.emptyMenuCoord = null;
      this.emptyMenuPos = null;
      if (focusViewport) {
        this.$nextTick(() => this.$refs.viewport?.focus());
      }
    },
    openEmptyMenu(coord, e) {
      if (!this.isWritableCoord(coord) || this.itemAtCoord(coord)) return;
      this.selectedCell = { ...coord };
      this.emptyMenuCoord = { ...coord };
      if (e && Number.isFinite(e.clientX) && Number.isFinite(e.clientY)) {
        this.emptyMenuPos = { x: e.clientX, y: e.clientY };
      } else {
        this.emptyMenuPos = null;
      }
      this.liveMessage = `Empty cell at column ${coord.col}, row ${coord.row}`;
      this.$nextTick(() => {
        const menu = this.$refs.emptyMenu;
        if (menu && typeof menu.focus === 'function') menu.focus();
        const [first] = this.emptyMenuItems();
        if (first && typeof first.focus === 'function') first.focus();
      });
    },
    onEmptyMenuKeydown(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        this.closeEmptyMenu({ focusViewport: true });
        return;
      }
      const items = this.emptyMenuItems();
      if (!items.length) return;
      const currentIdx = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        e.stopPropagation();
        items[(currentIdx + 1) % items.length].focus();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        e.stopPropagation();
        items[currentIdx <= 0 ? items.length - 1 : currentIdx - 1].focus();
      } else if (e.key === 'Home') {
        e.preventDefault();
        e.stopPropagation();
        items[0].focus();
      } else if (e.key === 'End') {
        e.preventDefault();
        e.stopPropagation();
        items[items.length - 1].focus();
      } else if (e.key === 'Enter' || e.key === ' ') {
        e.stopPropagation();
      }
    },
    emptyMenuOpenFor(coord) {
      return !!(this.emptyMenuCoord && coord && this.emptyMenuCoord.col === coord.col && this.emptyMenuCoord.row === coord.row);
    },
    openLibraryForCoord(coord) {
      this.selectedAddCoord = { ...coord };
      this.libraryMode = 'ai';
      this.showLibrary = true;
      this.emptyMenuCoord = null;
      this.emptyMenuPos = null;
      this.loadShellExamples();
      this.$nextTick(() => this.$refs.libraryOverlay?.focus());
    },
    async loadShellExamples() {
      if (this.shellExamplesLoaded) return;
      this.shellExamplesLoaded = true;
      try {
        const res = await fetch('/shell_worker_examples.json', { credentials: 'same-origin' });
        if (!res.ok) return;
        const data = await res.json();
        this.shellExamples = Array.isArray(data?.examples) ? data.examples : [];
      } catch (_err) {
        this.shellExamplesLoaded = false;
      }
    },
    closeLibrary() {
      this.showLibrary = false;
      this.selectedAddCoord = null;
      this.$nextTick(() => this.$refs.viewport?.focus());
    },
    createWorkerAndOpenConfig({ type, profile, fields }) {
      const slot = this.nextEmptySlotIndex();
      this.$emit('add-worker', {
        coord: this.selectedAddCoord,
        profile,
        type,
        fields,
      });
      this.$emit('configure-worker', slot);
      this.closeLibrary();
    },
    addFromLibrary(profileId) {
      this.createWorkerAndOpenConfig({ profile: profileId, type: 'ai' });
    },
    addShellWorker(example) {
      const fields = { name: 'Shell worker', activation: 'on_drop' };
      if (example && typeof example === 'object') {
        if (example.name) fields.name = example.name;
        if (example.command) fields.command = example.command;
        if (example.ticket_delivery) fields.ticket_delivery = example.ticket_delivery;
        if (example.disposition) fields.disposition = example.disposition;
        if (Number.isFinite(example.max_retries)) fields.max_retries = example.max_retries;
        if (Array.isArray(example.env)) {
          fields.env = example.env
            .filter(e => e && e.key)
            .map(e => ({ key: String(e.key), value: String(e.value || '') }));
        }
      }
      this.createWorkerAndOpenConfig({
        type: 'shell',
        fields,
      });
    },
    addServiceWorker() {
      this.createWorkerAndOpenConfig({
        type: 'service',
        fields: {
          name: 'Service worker',
          activation: 'manual',
          ticket_action: 'start-if-stopped-else-restart',
        },
      });
    },
    addMarkerWorker() {
      this.createWorkerAndOpenConfig({
        type: 'marker',
        fields: {
          name: 'Marker',
          note: '',
          activation: 'on_drop',
          disposition: 'review',
        },
      });
    },
    nextEmptySlotIndex() {
      const slots = this.layout?.slots || [];
      const idx = slots.findIndex(slot => !slot);
      return idx >= 0 ? idx : slots.length;
    },
    workerFieldsForClipboard(worker) {
      const fields = ['type', 'profile', 'name', 'note', 'agent', 'model', 'activation', 'disposition', 'watch_column', 'expertise_prompt', 'trust_mode',
        'max_retries', 'use_worktree', 'auto_commit', 'auto_pr', 'trigger_time', 'trigger_interval_minutes',
        'trigger_every_day', 'command', 'cwd', 'timeout_seconds', 'ticket_delivery', 'env',
        'pre_start', 'ticket_action', 'startup_grace_seconds', 'startup_timeout_seconds',
        'health_type', 'health_url', 'health_command', 'health_interval_seconds',
        'health_timeout_seconds', 'health_failure_threshold', 'on_crash',
        'stop_timeout_seconds', 'log_max_bytes', 'color', 'avatar'];
      const copy = {};
      for (const key of fields) {
        if (worker[key] !== undefined) copy[key] = JSON.parse(JSON.stringify(worker[key]));
      }
      return copy;
    },
    passTargetsForSlot(slotIndex) {
      const item = this.workerItemBySlot[slotIndex];
      if (!item) return [];
      return this.passTargetsBySlot[slotIndex] || [];
    },
    passSourcesForSlot(slotIndex) {
      const target = Number(slotIndex);
      if (!Number.isInteger(target) || !this.workerItemBySlot[target]) return [];
      return this.passSourcesBySlot[target] || [];
    },
    workerGroupSlots(startSlot) {
      const root = Number(startSlot);
      if (!Number.isInteger(root) || !this.workerItemBySlot[root]) return [];
      const visited = new Set();
      const stack = [root];
      const group = [];
      while (stack.length) {
        const slot = stack.pop();
        if (visited.has(slot)) continue;
        if (!this.workerItemBySlot[slot]) continue;
        visited.add(slot);
        group.push(slot);
        const neighbors = new Set([...this.passTargetsForSlot(slot), ...this.passSourcesForSlot(slot)]);
        for (const next of neighbors) {
          if (!visited.has(next)) stack.push(next);
        }
      }
      return group;
    },
    buildWorkerDragPayload(slotIndex, options = {}) {
      const source = Number(slotIndex);
      const pointerOffset = this._workerDragPointerOffset(source, options);
      if (options.singleton) return { source, group: [source], pointerOffset, singleton: true };
      const group = this.selectedWorkerSlots.includes(source)
        ? this.expandSelectionSlots(this.selectedWorkerSlots)
        : this.workerGroupSlots(source);
      return { source, group: group.length ? group : [source], pointerOffset, singleton: false };
    },
    buildWorkerDragImage(slotIndex, pointer = {}, options = {}) {
      const source = Number(slotIndex);
      const sourceItem = this.workerItemBySlot[source];
      if (!sourceItem) return null;
      const slots = options.singleton
        ? [source]
        : (this.selectedWorkerSlots.includes(source) ? this.expandSelectionSlots(this.selectedWorkerSlots) : this.workerGroupSlots(source));
      if (!slots.length) return null;
      const items = slots.map(slot => this.workerItemBySlot[slot]).filter(Boolean);
      if (!items.length) return null;
      const cols = items.map(item => item.coord.col);
      const rows = items.map(item => item.coord.row);
      const minCol = Math.min(...cols);
      const maxCol = Math.max(...cols);
      const minRow = Math.min(...rows);
      const maxRow = Math.max(...rows);
      const width = (maxCol - minCol + 1) * this.columnWidth;
      const height = (maxRow - minRow + 1) * this.rowHeight;
      const root = document.createElement('div');
      root.className = 'worker-group-drag-image';
      root.style.width = `${width}px`;
      root.style.height = `${height}px`;
      const sourceDx = (sourceItem.coord.col - minCol) * this.columnWidth;
      const sourceDy = (sourceItem.coord.row - minRow) * this.rowHeight;
      for (const item of items) {
        const slot = item.slotIndex;
        const left = (item.coord.col - minCol) * this.columnWidth;
        const top = (item.coord.row - minRow) * this.rowHeight;
        const cardEl = this.workerElementForSlot(slot);
        const node = cardEl ? cardEl.cloneNode(true) : document.createElement('div');
        if (!cardEl) {
          node.className = 'worker-card worker-card-drag-placeholder';
          node.textContent = item.worker?.name || `Slot ${slot + 1}`;
        }
        node.classList.add('worker-card-drag-clone');
        node.style.position = 'absolute';
        node.style.left = `${left}px`;
        node.style.top = `${top}px`;
        node.style.width = `${this.columnWidth}px`;
        node.style.height = `${this.rowHeight}px`;
        node.style.margin = '0';
        root.appendChild(node);
      }
      document.body.appendChild(root);
      const pointerOffset = this._workerDragPointerOffset(source, pointer);
      const pointerDx = pointerOffset.x;
      const pointerDy = pointerOffset.y;
      return {
        element: root,
        offsetX: sourceDx + pointerDx,
        offsetY: sourceDy + pointerDy,
      };
    },
    _workerDragPointerOffset(slotIndex, pointer = {}) {
      const source = Number(slotIndex);
      const sourceEl = this.workerElementForSlot(source);
      let x = this.columnWidth / 2;
      let y = this.rowHeight / 2;
      if (sourceEl && Number.isFinite(Number(pointer.clientX)) && Number.isFinite(Number(pointer.clientY))) {
        const rect = sourceEl.getBoundingClientRect();
        x = Math.max(0, Math.min(rect.width, Number(pointer.clientX) - rect.left));
        y = Math.max(0, Math.min(rect.height, Number(pointer.clientY) - rect.top));
      }
      return { x, y };
    },
    workerElementForSlot(slotIndex) {
      const ref = this.workerRefs?.[slotIndex];
      if (!ref) return null;
      if (ref.$el) return ref.$el;
      return typeof ref.getBoundingClientRect === 'function' ? ref : null;
    },
    _workerDragSource(e) {
      const raw = e?.dataTransfer?.getData?.('application/x-worker-slot');
      if (raw !== '' && raw != null) {
        const source = Number(raw);
        if (Number.isInteger(source)) return source;
      }
      const fallback = Number(window._bullpenWorkerDrag?.source);
      return Number.isInteger(fallback) ? fallback : null;
    },
    _dragGroupSlots() {
      const payload = window._bullpenWorkerDrag;
      return Array.isArray(payload?.group) ? payload.group : null;
    },
    _isSingletonWorkerDrag() {
      return window._bullpenWorkerDrag?.singleton === true;
    },
    _isWorkerDrag(e) {
      const types = Array.from(e?.dataTransfer?.types || []);
      return !!window._bullpenWorkerDrag || types.includes('application/x-worker-slot') || types.includes('application/x-worker-group');
    },
    _workerDragCoordFromEvent(e) {
      const offset = window._bullpenWorkerDrag?.pointerOffset;
      const offsetX = Number(offset?.x);
      const offsetY = Number(offset?.y);
      if (!Number.isFinite(offsetX) || !Number.isFinite(offsetY)) return this.coordFromEvent(e);
      const rect = this.dragViewportRect || this.$refs.viewport.getBoundingClientRect();
      const x = e.clientX - rect.left - this.headerWidth - offsetX;
      const y = e.clientY - rect.top - this.headerHeight - offsetY;
      return GridGeometry.pixelToCoord(x, y, this.viewportOrigin, this.cardSize);
    },
    buildGroupMovePlan(sourceSlot, destinationCoord, overrideSlots) {
      const source = Number(sourceSlot);
      if (!Number.isInteger(source) || !destinationCoord || !this.isWritableCoord(destinationCoord)) return null;
      const anchor = this.workerItemBySlot[source];
      if (!anchor) return null;
      const slots = overrideSlots || this.workerGroupSlots(source);
      if (!slots.length) return null;
      const groupSet = new Set(slots);
      const deltaCol = destinationCoord.col - anchor.coord.col;
      const deltaRow = destinationCoord.row - anchor.coord.row;
      const coordKeys = new Set();
      const moves = [];
      for (const slot of slots) {
        const item = this.workerItemBySlot[slot];
        if (!item) return null;
        const target = { col: item.coord.col + deltaCol, row: item.coord.row + deltaRow };
        if (!this.isWritableCoord(target)) return null;
        const key = GridGeometry.coordKey(target.col, target.row);
        if (coordKeys.has(key)) return null;
        coordKeys.add(key);
        const occupied = this.itemAtCoord(target);
        if (occupied && !groupSet.has(occupied.slotIndex)) return null;
        moves.push({ slot, to_coord: target });
      }
      return { source, slots, moves };
    },
    canDropWorkerAtSlot(sourceSlot, targetSlot, e) {
      if (e && !this.dragViewportRect) this.dragViewportRect = this.$refs.viewport.getBoundingClientRect();
      const coord = e ? this._workerDragCoordFromEvent(e) : null;
      if (coord) return this._setDropTarget(sourceSlot, coord);
      const target = this.workerItemBySlot[targetSlot];
      if (!target) return false;
      return this._setDropTarget(sourceSlot, target.coord);
    },
    canDropWorkerAtCoord(sourceSlot, coord) {
      if (this._isSingletonWorkerDrag()) {
        const source = Number(sourceSlot);
        return Number.isInteger(source) && !!this.workerItemBySlot[source] && !!coord && this.isWritableCoord(coord);
      }
      return !!this.buildGroupMovePlan(sourceSlot, coord, this._dragGroupSlots());
    },
    dropWorkerOnSlot(sourceSlot, targetSlot, e) {
      const coord = e ? this._workerDragCoordFromEvent(e) : null;
      this._clearDropTarget();
      this.dragViewportRect = null;
      if (coord) return this.moveWorkerDragToCoord(sourceSlot, coord);
      const target = this.workerItemBySlot[targetSlot];
      if (!target) return false;
      return this.moveWorkerDragToCoord(sourceSlot, target.coord);
    },
    updateSingletonWorkerDrag(sourceSlot, e) {
      if (e && !this.dragViewportRect) this.dragViewportRect = this.$refs.viewport.getBoundingClientRect();
      const source = Number(sourceSlot);
      const coord = this._workerDragCoordFromEvent(e);
      if (Number.isInteger(source) && coord && this._setDropTarget(source, coord)) {
        this.hoveredCoord = coord;
        return true;
      }
      this.hoveredCoord = null;
      this._clearDropTarget();
      return false;
    },
    endSingletonWorkerDrag(sourceSlot, e) {
      const source = Number(sourceSlot);
      const coord = this._workerDragCoordFromEvent(e);
      this.hoveredCoord = null;
      this._clearDropTarget();
      this.dragViewportRect = null;
      if (!Number.isInteger(source) || !coord) return false;
      return this.moveSingleWorkerToCoord(source, coord);
    },
    cancelSingletonWorkerDrag() {
      this.hoveredCoord = null;
      this._clearDropTarget();
      this.dragViewportRect = null;
    },
    moveWorkerDragToCoord(sourceSlot, coord) {
      if (this._isSingletonWorkerDrag()) {
        return this.moveSingleWorkerToCoord(sourceSlot, coord);
      }
      return this.moveWorkerGroupToCoord(sourceSlot, coord);
    },
    moveSingleWorkerToCoord(sourceSlot, coord) {
      const source = Number(sourceSlot);
      const sourceItem = this.workerItemBySlot[source];
      if (!Number.isInteger(source) || !sourceItem || !coord || !this.isWritableCoord(coord)) return false;
      const occupied = this.itemAtCoord(coord);
      if (occupied && occupied.slotIndex === source) return true;
      if (occupied) {
        this.$root.moveWorker(source, occupied.slotIndex);
      } else {
        this.$root.moveWorker(source, coord);
      }
      this.hoveredCoord = null;
      this.emptyMenuCoord = null;
      this.selectedCell = { ...coord };
      this.selectionAnchor = { ...coord };
      this.selectedWorkerSlots = [source];
      return true;
    },
    moveWorkerGroupToCoord(sourceSlot, coord) {
      const plan = this.buildGroupMovePlan(sourceSlot, coord, this._dragGroupSlots());
      if (!plan || !plan.moves.length) return false;
      const changed = plan.moves.some(move => {
        const item = this.workerItemBySlot[move.slot];
        return item && (item.coord.col !== move.to_coord.col || item.coord.row !== move.to_coord.row);
      });
      if (!changed) return true;
      if (typeof this.$root.moveWorkerGroup === 'function') {
        this.$root.moveWorkerGroup(plan.moves);
      } else if (plan.moves.length === 1) {
        this.$root.moveWorker(plan.moves[0].slot, plan.moves[0].to_coord);
      } else {
        for (const move of plan.moves) this.$root.moveWorker(move.slot, move.to_coord);
      }
      this.hoveredCoord = null;
      this.emptyMenuCoord = null;
      const sourceMove = plan.moves.find(m => Number(m.slot) === Number(sourceSlot));
      if (sourceMove && sourceMove.to_coord) {
        this.selectedCell = { ...sourceMove.to_coord };
      } else {
        this.selectedCell = { ...coord };
      }
      this.selectionAnchor = { ...this.selectedCell };
      this.selectedWorkerSlots = plan.slots.slice();
      return true;
    },
    copyWorker(slot) {
      const source = this.workerItemBySlot[slot];
      if (!source) return;
      const slots = this.selectedWorkerSlots.includes(Number(slot)) && this.selectedWorkerSlots.length
        ? this.expandSelectionSlots(this.selectedWorkerSlots)
        : this.workerGroupSlots(slot);
      const workers = slots.map(memberSlot => {
        const item = this.workerItemBySlot[memberSlot];
        if (!item) return null;
        return {
          offset: {
            col: item.coord.col - source.coord.col,
            row: item.coord.row - source.coord.row,
          },
          worker: this.workerFieldsForClipboard(item.worker),
        };
      }).filter(Boolean);
      if (!workers.length) return;
      this.clipboardWorker = {
        anchor: { ...source.coord },
        workers,
      };
      this.liveMessage = workers.length > 1
        ? `Copied worker group (${workers.length}) from ${source.worker.name}`
        : `Copied worker ${source.worker.name}`;
    },
    deleteWorkerFromMenu(slot) {
      const source = Number(slot);
      if (!Number.isInteger(source) || !this.workerItemBySlot[source]) return;
      const slots = this.selectedWorkerSlots.includes(source) && this.selectedWorkerSlots.length > 1
        ? this.expandSelectionSlots(this.selectedWorkerSlots)
        : [source];
      if (slots.length > 1 && typeof this.$root.removeWorkers === 'function') {
        this.$root.removeWorkers(slots);
      } else {
        this.$root.removeWorker(source);
      }
    },
    clipboardTargetsForCoord(coord) {
      if (!this.clipboardWorker || !Array.isArray(this.clipboardWorker.workers)) return [];
      return this.clipboardWorker.workers.map(entry => ({
        coord: {
          col: coord.col + Number(entry?.offset?.col || 0),
          row: coord.row + Number(entry?.offset?.row || 0),
        },
        worker: entry?.worker || {},
      }));
    },
    canPasteAt(coord) {
      if (!this.clipboardWorker || !coord || !this.isWritableCoord(coord)) return false;
      const targets = this.clipboardTargetsForCoord(coord);
      if (!targets.length) return false;
      const seen = new Set();
      for (const target of targets) {
        if (!this.isWritableCoord(target.coord)) return false;
        const key = GridGeometry.coordKey(target.coord.col, target.coord.row);
        if (seen.has(key)) return false;
        seen.add(key);
        if (this.itemAtCoord(target.coord)) return false;
      }
      return true;
    },
    pasteWorkerFromMenu(coord) {
      this.pasteWorker(coord);
      this.closeEmptyMenu({ focusViewport: true });
    },
    pasteWorker(coord, options = {}) {
      if (!this.clipboardWorker || !coord || !this.isWritableCoord(coord)) return;
      const targets = this.clipboardTargetsForCoord(coord);
      if (!targets.length) return;
      const single = targets.length === 1 ? targets[0] : null;
      if (single && options.allowReplaceSingle) {
        const existing = this.itemAtCoord(single.coord);
        if (existing) {
          const existingName = existing.worker?.name || `Slot ${existing.slotIndex + 1}`;
          const pasteName = single.worker?.name || 'pasted worker';
          if (!confirm(`Replace worker "${existingName}" with "${pasteName}"?`)) return;
          this.$root.pasteWorkerConfig({ coord: single.coord, worker: single.worker, replace: true });
          this.emptyMenuCoord = null;
          return;
        }
      }
      const blocked = targets.find(target => !this.isWritableCoord(target.coord) || this.itemAtCoord(target.coord));
      if (blocked) {
        this.liveMessage = 'Cannot paste worker group here';
        return;
      }
      if (targets.length === 1) {
        this.$root.pasteWorkerConfig({ coord: targets[0].coord, worker: targets[0].worker });
      } else if (typeof this.$root.pasteWorkerGroup === 'function') {
        this.$root.pasteWorkerGroup(targets);
      } else {
        for (const target of targets) {
          this.$root.pasteWorkerConfig({ coord: target.coord, worker: target.worker });
        }
      }
      this.emptyMenuCoord = null;
    },
    _setDropTarget(source, coord) {
      const singleton = this._isSingletonWorkerDrag();
      const key = `${singleton ? 'single' : 'group'}:${source}:${coord?.col},${coord?.row}`;
      if (coord && this.lastDropTargetKey === key) return true;
      if (this._isSingletonWorkerDrag()) {
        if (!this.canDropWorkerAtCoord(source, coord)) {
          this.dragOverCoord = null;
          this.dropTargetCoords = [];
          this.lastDropTargetKey = '';
          return false;
        }
        this.lastDropTargetKey = key;
        this.dragOverCoord = { ...coord };
        this.dropTargetCoords = [{ col: coord.col, row: coord.row }];
        return true;
      }
      const plan = this.buildGroupMovePlan(source, coord, this._dragGroupSlots());
      if (!plan) {
        this.dragOverCoord = null;
        this.dropTargetCoords = [];
        this.lastDropTargetKey = '';
        return false;
      }
      this.lastDropTargetKey = key;
      this.dragOverCoord = { ...coord };
      this.dropTargetCoords = plan.moves.map(m => ({ col: m.to_coord.col, row: m.to_coord.row }));
      return true;
    },
    _clearDropTarget() {
      this.dragOverCoord = null;
      this.dropTargetCoords = [];
      this.lastDropTargetKey = '';
    },
    onEmptyDragOver(e, coord) {
      if (!this._isWorkerDrag(e)) return;
      const source = this._workerDragSource(e);
      const dropCoord = this._workerDragCoordFromEvent(e) || coord;
      if (Number.isInteger(source) && this._setDropTarget(source, dropCoord)) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
      } else {
        e.dataTransfer.dropEffect = 'none';
        this._clearDropTarget();
      }
    },
    onDropOnEmpty(e, coord) {
      const source = this._workerDragSource(e);
      const dropCoord = this._workerDragCoordFromEvent(e) || coord;
      this._clearDropTarget();
      this.dragViewportRect = null;
      if (!Number.isInteger(source)) return false;
      return this.moveWorkerDragToCoord(source, dropCoord);
    },
    onCanvasDragOver(e) {
      if (!this._isWorkerDrag(e)) return;
      if (!this.dragViewportRect) this.dragViewportRect = this.$refs.viewport.getBoundingClientRect();
      const source = this._workerDragSource(e);
      const coord = this._workerDragCoordFromEvent(e);
      if (Number.isInteger(source) && coord && this._setDropTarget(source, coord)) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        this.hoveredCoord = coord;
      } else {
        e.dataTransfer.dropEffect = 'none';
        this.hoveredCoord = null;
        this._clearDropTarget();
      }
    },
    onCanvasDrop(e) {
      if (!this._isWorkerDrag(e)) return;
      const src = this._workerDragSource(e);
      const coord = this._workerDragCoordFromEvent(e);
      this.hoveredCoord = null;
      this._clearDropTarget();
      this.dragViewportRect = null;
      if (!Number.isInteger(src) || !coord) return;
      this.moveWorkerDragToCoord(src, coord);
    },
    onCanvasDragLeave(e) {
      const related = e && e.relatedTarget;
      const canvas = e && e.currentTarget;
      if (related && canvas && typeof canvas.contains === 'function' && canvas.contains(related)) return;
      this._clearDropTarget();
      this.dragViewportRect = null;
    },
    colLabel(col) {
      if (!Number.isFinite(col)) return '';
      if (col < 0) return '-' + this.colLabel(-col - 1);
      let s = '';
      let n = Math.floor(col);
      while (true) {
        s = String.fromCharCode(65 + (n % 26)) + s;
        n = Math.floor(n / 26) - 1;
        if (n < 0) break;
      }
      return s;
    },
    rowLabel(row) {
      if (!Number.isFinite(row)) return '';
      return String(Math.floor(row) + 1);
    },
    onColumnResizeDown(e) {
      if (e.button !== 0) return;
      if (this.columnResize) return;
      e.preventDefault();
      e.stopPropagation();
      this.columnResize = {
        startX: e.clientX,
        startWidth: this.columnWidth,
        pointerId: e.pointerId,
      };
      this.draggingColumnWidth = this.columnWidth;
      this.updateResizeTooltip(e, `${this.columnWidth}px wide`);
      this._colResizeMoveHandler = (ev) => this.onColumnResizeMove(ev);
      this._colResizeUpHandler = (ev) => this.onColumnResizeUp(ev);
      window.addEventListener('pointermove', this._colResizeMoveHandler);
      window.addEventListener('pointerup', this._colResizeUpHandler);
      window.addEventListener('pointercancel', this._colResizeUpHandler);
    },
    onColumnResizeMove(e) {
      if (!this.columnResize) return;
      if (e.pointerId !== this.columnResize.pointerId) return;
      const dx = e.clientX - this.columnResize.startX;
      const next = this.columnResize.startWidth + dx;
      this.draggingColumnWidth = Math.max(140, Math.min(480, Math.round(next)));
      this.updateResizeTooltip(e, `${this.draggingColumnWidth}px wide`);
    },
    onColumnResizeUp(e) {
      if (!this.columnResize) return;
      if (e && e.pointerId !== this.columnResize.pointerId) return;
      this._teardownColumnResizeListeners();
      const dragged = this.draggingColumnWidth;
      this.columnResize = null;
      this.draggingColumnWidth = null;
      this.resizeTooltip = null;
      if (dragged != null) {
        const final = this.clampColumnWidth(dragged);
        this.pendingColumnWidth = final;
        this.persistGrid({ columnWidth: final });
      }
    },
    _teardownColumnResizeListeners() {
      if (this._colResizeMoveHandler) {
        window.removeEventListener('pointermove', this._colResizeMoveHandler);
        window.removeEventListener('pointerup', this._colResizeUpHandler);
        window.removeEventListener('pointercancel', this._colResizeUpHandler);
        this._colResizeMoveHandler = null;
        this._colResizeUpHandler = null;
      }
    },
    resetColumnWidth() {
      this._teardownColumnResizeListeners();
      this.columnResize = null;
      this.draggingColumnWidth = null;
      this.pendingColumnWidth = 220;
      this.resizeTooltip = null;
      this.persistGrid({ columnWidth: 220 });
    },
    onRowResizeDown(e) {
      if (e.button !== 0) return;
      if (this.rowResize) return;
      e.preventDefault();
      e.stopPropagation();
      this.rowResize = {
        startY: e.clientY,
        startHeight: this.rowHeight,
        pointerId: e.pointerId,
      };
      this.draggingRowHeight = this.rowHeight;
      this.updateResizeTooltip(e, `${this.rowHeight}px tall`);
      this._rowResizeMoveHandler = (ev) => this.onRowResizeMove(ev);
      this._rowResizeUpHandler = (ev) => this.onRowResizeUp(ev);
      window.addEventListener('pointermove', this._rowResizeMoveHandler);
      window.addEventListener('pointerup', this._rowResizeUpHandler);
      window.addEventListener('pointercancel', this._rowResizeUpHandler);
    },
    onRowResizeMove(e) {
      if (!this.rowResize) return;
      if (e.pointerId !== this.rowResize.pointerId) return;
      const dy = e.clientY - this.rowResize.startY;
      const next = this.rowResize.startHeight + dy;
      this.draggingRowHeight = Math.max(32, Math.min(480, Math.round(next)));
      this.updateResizeTooltip(e, `${this.draggingRowHeight}px tall`);
    },
    onRowResizeUp(e) {
      if (!this.rowResize) return;
      if (e && e.pointerId !== this.rowResize.pointerId) return;
      this._teardownRowResizeListeners();
      const dragged = this.draggingRowHeight;
      this.rowResize = null;
      this.draggingRowHeight = null;
      this.resizeTooltip = null;
      if (dragged != null) {
        const final = this.clampRowHeight(dragged);
        this.pendingRowHeight = final;
        this.persistGrid({ rowHeight: final });
      }
    },
    _teardownRowResizeListeners() {
      if (this._rowResizeMoveHandler) {
        window.removeEventListener('pointermove', this._rowResizeMoveHandler);
        window.removeEventListener('pointerup', this._rowResizeUpHandler);
        window.removeEventListener('pointercancel', this._rowResizeUpHandler);
        this._rowResizeMoveHandler = null;
        this._rowResizeUpHandler = null;
      }
    },
    resetRowHeight() {
      this._teardownRowResizeListeners();
      this.rowResize = null;
      this.draggingRowHeight = null;
      this.pendingRowHeight = 140;
      this.resizeTooltip = null;
      this.persistGrid({ rowHeight: 140 });
    },
    onCardVerticalResizeStart(item, e) {
      if (!item || e.button !== 0) return;
      if (this.cardVerticalResize) return;
      this.selectWorker(item);
      const startDelta = this.cardExpansionDeltaForSlot(item.slotIndex);
      this.expandedWorkerCardSlot = item.slotIndex;
      this.expandedWorkerCardDelta = startDelta;
      this.cardVerticalResize = {
        slotIndex: item.slotIndex,
        startY: e.clientY,
        startDelta,
        pointerId: e.pointerId,
      };
      this.updateResizeTooltip(e, `${this.cardHeightForSlot(item.slotIndex)}px visible`);
      this._cardResizeMoveHandler = (ev) => this.onCardVerticalResizeMove(ev);
      this._cardResizeUpHandler = (ev) => this.onCardVerticalResizeUp(ev);
      window.addEventListener('pointermove', this._cardResizeMoveHandler);
      window.addEventListener('pointerup', this._cardResizeUpHandler);
      window.addEventListener('pointercancel', this._cardResizeUpHandler);
    },
    onCardVerticalResizeMove(e) {
      if (!this.cardVerticalResize) return;
      if (e.pointerId !== this.cardVerticalResize.pointerId) return;
      const dy = e.clientY - this.cardVerticalResize.startY;
      const next = this.cardVerticalResize.startDelta + dy;
      this.expandedWorkerCardDelta = Math.max(0, Math.min(this.cardExpansionLimit(), Math.round(next)));
      this.updateResizeTooltip(e, `${this.cardHeightForSlot(this.cardVerticalResize.slotIndex)}px visible`);
    },
    onCardVerticalResizeUp(e) {
      if (!this.cardVerticalResize) return;
      if (e && e.pointerId !== this.cardVerticalResize.pointerId) return;
      this._teardownCardVerticalResizeListeners();
      this.cardVerticalResize = null;
      this.resizeTooltip = null;
      if (this.expandedWorkerCardDelta <= 0) this.clearExpandedWorkerCard();
    },
    _teardownCardVerticalResizeListeners() {
      if (this._cardResizeMoveHandler) {
        window.removeEventListener('pointermove', this._cardResizeMoveHandler);
        window.removeEventListener('pointerup', this._cardResizeUpHandler);
        window.removeEventListener('pointercancel', this._cardResizeUpHandler);
        this._cardResizeMoveHandler = null;
        this._cardResizeUpHandler = null;
      }
    },
    updateResizeTooltip(e, text) {
      this.resizeTooltip = { x: e.clientX + 14, y: e.clientY + 14, text };
    },
    toggleMinimap() {
      this.$emit('set-minimap-collapsed', !this.minimapCollapsed);
    },
    onMinimapClick(e) {
      const rect = e.currentTarget.getBoundingClientRect();
      const b = this.minimapBounds;
      const scale = this.minimapScale;
      const col = b.colMin + (e.clientX - rect.left) / scale.x;
      const row = b.rowMin + (e.clientY - rect.top) / scale.y;
      this.setOrigin({
        col: col - (this.viewportPx.width / this.columnWidth) / 2,
        row: row - (this.viewportPx.height / this.rowHeight) / 2,
      });
    },
  }
};
