const ColumnManagerModal = {
  props: ['visible', 'columns', 'tasks'],
  emits: ['close', 'save'],
  data() {
    return {
      localColumns: [],
      newLabel: '',
      newColor: '#6B7280',
      showAddForm: false,
      dragSrcIdx: null,
      dragOverIdx: null,
      pendingDelete: null,  // { idx, moveToKey }
      ticketMigrations: [],  // [{ fromKey, toKey }] accumulated during this edit session
    };
  },
  watch: {
    visible(v) {
      if (v) this.reset();
    }
  },
  computed: {
    ticketsInColumn() {
      const map = {};
      for (const col of this.localColumns) {
        map[col.key] = (this.tasks || []).filter(t => t.status === col.key).length;
      }
      return map;
    },
    canSave() {
      return this.localColumns.length > 0 && this.localColumns.every(c => c.label.trim());
    },
  },
  methods: {
    onPrimaryShortcut(e) {
      e.preventDefault();
      this.save();
    },
    reset() {
      this.localColumns = (this.columns || []).map(c => ({ ...c }));
      this.newLabel = '';
      this.newColor = '#6B7280';
      this.showAddForm = false;
      this.dragSrcIdx = null;
      this.dragOverIdx = null;
      this.pendingDelete = null;
      this.ticketMigrations = [];
    },
    generateKey(label) {
      const existing = this.localColumns.map(c => c.key);
      const base = label.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '').slice(0, 30) || 'column';
      let key = base;
      let i = 2;
      while (existing.includes(key)) { key = base + '_' + i; i++; }
      return key;
    },
    addColumn() {
      const label = this.newLabel.trim();
      if (!label) return;
      const key = this.generateKey(label);
      this.localColumns.push({ key, label, color: this.newColor });
      this.newLabel = '';
      this.newColor = '#6B7280';
      this.showAddForm = false;
    },
    openAddForm() {
      this.showAddForm = true;
      this.$nextTick(() => {
        if (this.$refs.newLabelInput) this.$refs.newLabelInput.focus();
      });
    },
    isWorkerColumn(key) {
      return key === 'assigned' || key === 'in_progress';
    },
    requestDelete(idx) {
      if (this.localColumns.length <= 1) return;
      const col = this.localColumns[idx];
      if (this.isWorkerColumn(col.key)) return;
      const count = this.ticketsInColumn[col.key] || 0;
      if (count > 0) {
        const fallback = this.localColumns.find((_, i) => i !== idx);
        this.pendingDelete = { idx, moveToKey: fallback ? fallback.key : '' };
      } else {
        this.localColumns.splice(idx, 1);
        this.pendingDelete = null;
      }
    },
    confirmDelete() {
      if (!this.pendingDelete) return;
      const { idx, moveToKey } = this.pendingDelete;
      const fromKey = this.localColumns[idx].key;
      if (moveToKey && moveToKey !== fromKey) {
        this.ticketMigrations.push({ fromKey, toKey: moveToKey });
      }
      this.localColumns.splice(idx, 1);
      this.pendingDelete = null;
    },
    cancelDelete() {
      this.pendingDelete = null;
    },
    otherColumns(excludeIdx) {
      return this.localColumns.filter((_, i) => i !== excludeIdx);
    },
    onDragStart(e, idx) {
      this.dragSrcIdx = idx;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', String(idx));
    },
    onDragOver(e, idx) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      this.dragOverIdx = idx;
    },
    onDragLeave(e) {
      // only clear if leaving to outside the row
      if (!e.currentTarget.contains(e.relatedTarget)) {
        this.dragOverIdx = null;
      }
    },
    onDrop(e, idx) {
      e.preventDefault();
      if (this.dragSrcIdx === null || this.dragSrcIdx === idx) {
        this.dragSrcIdx = null;
        this.dragOverIdx = null;
        return;
      }
      const moved = this.localColumns.splice(this.dragSrcIdx, 1)[0];
      // after splice, the target index may have shifted
      const insertAt = this.dragSrcIdx < idx ? idx - 1 : idx;
      this.localColumns.splice(insertAt, 0, moved);
      this.dragSrcIdx = null;
      this.dragOverIdx = null;
    },
    onDragEnd() {
      this.dragSrcIdx = null;
      this.dragOverIdx = null;
    },
    save() {
      if (!this.canSave) return;
      this.$emit('save', {
        columns: this.localColumns.map(c => ({ key: c.key, label: c.label.trim(), color: c.color })),
        ticketMigrations: this.ticketMigrations,
      });
    },
  },
  template: `
    <div v-if="visible" class="modal-overlay" @keydown.escape="$emit('close')" @keydown.meta.enter="onPrimaryShortcut" tabindex="0" @mousedown.self="$emit('close')">
      <div class="modal column-manager-modal">
        <div class="modal-header">
          <h2>Manage Columns</h2>
          <button class="btn btn-icon" @click="$emit('close')">&times;</button>
        </div>
        <div class="modal-body">
          <div class="col-manager-list">
            <div
              v-for="(col, idx) in localColumns"
              :key="col.key"
              class="col-manager-row"
              :class="{ 'col-drag-over': dragOverIdx === idx && dragSrcIdx !== idx }"
              draggable="true"
              @dragstart="onDragStart($event, idx)"
              @dragover="onDragOver($event, idx)"
              @dragleave="onDragLeave($event)"
              @drop="onDrop($event, idx)"
              @dragend="onDragEnd"
            >
              <span class="col-drag-handle" title="Drag to reorder">⠿</span>
              <input type="color" v-model="col.color" class="col-color-input" :title="col.color">
              <input type="text" v-model="col.label" class="form-input col-label-input" placeholder="Column name">
              <span class="col-ticket-count" :title="ticketsInColumn[col.key] + ' ticket(s)'">{{ ticketsInColumn[col.key] || 0 }}</span>
              <button
                class="btn btn-icon col-delete-btn"
                @click="requestDelete(idx)"
                :disabled="localColumns.length <= 1 || isWorkerColumn(col.key)"
                :title="isWorkerColumn(col.key) ? 'Worker-managed column cannot be deleted' : 'Delete column'"
              >&times;</button>
            </div>

            <div v-if="pendingDelete" class="col-delete-confirm">
              <span>{{ ticketsInColumn[localColumns[pendingDelete.idx]?.key] || 0 }} ticket(s) will be moved to:</span>
              <select class="form-select col-move-select" v-model="pendingDelete.moveToKey">
                <option v-for="col in otherColumns(pendingDelete.idx)" :key="col.key" :value="col.key">{{ col.label }}</option>
              </select>
              <button class="btn btn-sm btn-danger" @click="confirmDelete">Delete</button>
              <button class="btn btn-sm" @click="cancelDelete">Cancel</button>
            </div>
          </div>

          <div v-if="showAddForm" class="col-add-form">
            <input type="color" v-model="newColor" class="col-color-input" :title="newColor">
            <input
              type="text"
              v-model="newLabel"
              class="form-input col-label-input"
              placeholder="Column name"
              @keydown.enter="addColumn"
              @keydown.escape="showAddForm = false"
              ref="newLabelInput"
            >
            <button class="btn btn-sm btn-primary" @click="addColumn" :disabled="!newLabel.trim()">Add</button>
            <button class="btn btn-sm" @click="showAddForm = false">Cancel</button>
          </div>
          <button v-else class="btn btn-sm col-add-btn" @click="openAddForm">+ Add Column</button>
        </div>
        <div class="modal-footer">
          <button class="btn" @click="$emit('close')">Cancel</button>
          <div class="modal-footer-right">
            <button class="btn btn-primary" @click="save" :disabled="!canSave">Save</button>
          </div>
        </div>
      </div>
    </div>
  `,
};
