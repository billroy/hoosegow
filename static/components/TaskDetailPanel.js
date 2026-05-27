const TaskDetailPanel = {
  props: ['task', 'columns', 'readOnly'],
  emits: ['close', 'update', 'delete', 'archive', 'clear-output', 'toast', 'open-commit-diff'],
  data() {
    return {
      editing: false,
      editBody: '',
      editingTitle: false,
      editTitle: '',
      editingTags: false,
      editTagsValue: '',
      panelWidth: TaskDetailPanel._loadPanelWidth(),
      resizing: null,
      draggingWidth: null,
      nowMs: Date.now(),
      taskTimeTimer: null,
    };
  },
  computed: {
    _outputMarkerIndex() {
      // Shell workers write under "## Worker Output"; AI workers write under
      // "## Agent Output". A single ticket may hit both (e.g. Shell success
      // then a subsequent AI pass). Pick the earliest marker so everything
      // after it renders together in the Output section.
      if (!this.task?.body) return { idx: -1, marker: '' };
      const candidates = ['## Agent Output', '## Worker Output'];
      let best = { idx: -1, marker: '' };
      for (const marker of candidates) {
        const idx = this.task.body.indexOf(marker);
        if (idx < 0) continue;
        if (best.idx < 0 || idx < best.idx) best = { idx, marker };
      }
      return best;
    },
    agentOutput() {
      const { idx, marker } = this._outputMarkerIndex;
      if (idx < 0) return '';
      return this.task.body.substring(idx + marker.length).trim();
    },
    bodyWithoutOutput() {
      if (!this.task?.body) return '';
      const { idx } = this._outputMarkerIndex;
      if (idx < 0) return this.task.body;
      return this.task.body.substring(0, idx).trim();
    },
    renderedBody() {
      if (!this.bodyWithoutOutput) return '<p class="empty-state">No description</p>';
      return window.markdownit({ html: false }).render(this.bodyWithoutOutput);
    },
    parsedAgentOutputLines() {
      if (!this.agentOutput) return [];
      return this.agentOutput.split('\n').map((line, idx) => {
        const match = line.match(/^(.*?\bCommit:\s*)([0-9a-f]{7,40})(\b.*)$/i);
        if (!match) {
          return { id: idx, prefix: line, commitHash: null, suffix: '' };
        }
        return {
          id: idx,
          prefix: match[1] || '',
          commitHash: match[2] || null,
          suffix: match[3] || '',
        };
      });
    },
    displayedTaskTimeMs() {
      const base = getReportedTaskTimeMs(this.task);
      const startedMs = Date.parse(this.task?.active_task_started_at || '');
      if (!Number.isFinite(startedMs)) return base;
      return base + Math.max(this.nowMs - startedMs, 0);
    }
  },
  watch: {
    'task.id'() {
      this.editing = false;
      this.editingTitle = false;
      this.editingTags = false;
      this.$nextTick(() => renderLucideIcons(this.$el));
    },
    readOnly(nextValue) {
      if (!nextValue) return;
      this.editing = false;
      this.editingTitle = false;
      this.editingTags = false;
      this.$nextTick(() => renderLucideIcons(this.$el));
    }
  },
  template: `
    <div v-if="task" class="detail-panel" :style="{ width: (draggingWidth || panelWidth) + 'px' }">
      <div class="detail-panel-resize" @pointerdown="onResizeDown" @dblclick="resetWidth" title="Drag to resize"></div>
      <div class="detail-header">
        <div v-if="editingTitle" class="detail-title-edit">
          <input class="form-input detail-title-input" v-model="editTitle"
                 @keyup.enter="saveTitle" @keyup.escape="cancelTitle"
                 @keydown.meta.enter="saveTitle" @keydown.ctrl.enter="saveTitle" ref="titleInput" />
          <div class="detail-title-actions">
            <button class="btn btn-sm" @click="cancelTitle">Cancel</button>
            <button class="btn btn-sm btn-primary" @click="saveTitle">Save</button>
          </div>
        </div>
        <div v-else class="detail-title-wrap">
          <i class="ticket-type-icon ticket-type-icon--detail" data-lucide="tag" aria-hidden="true"></i>
          <h2 v-if="readOnly" class="detail-title detail-title-readonly">{{ task.title }}</h2>
          <h2 v-else class="detail-title" @click="startEditTitle" title="Click to edit">{{ task.title }}</h2>
        </div>
        <button class="btn btn-icon" @click="$emit('close')">&times;</button>
      </div>

      <div class="detail-scroll">
        <div class="detail-meta">
          <label class="form-label form-label-inline">
            Status
            <select v-if="!readOnly" class="form-select" :value="task.status"
                    @change="$emit('update', { id: task.id, status: $event.target.value })">
              <option v-for="col in columns" :key="col.key" :value="col.key">{{ col.label }}</option>
            </select>
            <span v-else class="detail-readonly-value">{{ columnLabel(task.status) }}</span>
          </label>
          <label class="form-label form-label-inline">
            Type
            <select v-if="!readOnly" class="form-select" :value="task.type"
                    @change="$emit('update', { id: task.id, type: $event.target.value })">
              <option value="task">Task</option>
              <option value="bug">Bug</option>
              <option value="feature">Feature</option>
              <option value="chore">Chore</option>
            </select>
            <span v-else class="detail-readonly-value">{{ task.type || 'task' }}</span>
          </label>
          <label class="form-label form-label-inline">
            Priority
            <select v-if="!readOnly" class="form-select" :value="task.priority"
                    @change="$emit('update', { id: task.id, priority: $event.target.value })">
              <option value="low">Low</option>
              <option value="normal">Normal</option>
              <option value="high">High</option>
              <option value="urgent">Urgent</option>
            </select>
            <span v-else class="detail-readonly-value">{{ task.priority || 'normal' }}</span>
          </label>
        </div>

        <div class="detail-tags-section">
          <div v-if="editingTags && !readOnly">
            <input class="form-input" v-model="editTagsValue" placeholder="comma-separated tags"
                   @keyup.enter="saveTags" @keyup.escape="cancelTags" />
            <div class="detail-edit-actions">
              <button class="btn btn-sm" @click="cancelTags">Cancel</button>
              <button class="btn btn-sm btn-primary" @click="saveTags">Save</button>
            </div>
          </div>
          <div v-else class="detail-tags">
            <span class="badge type-badge" v-for="tag in task.tags" :key="tag">{{ tag }}</span>
            <span v-if="readOnly && (!task.tags || !task.tags.length)" class="empty-state">No tags</span>
            <button v-if="!readOnly" class="btn btn-sm" @click="startEditTags">{{ task.tags && task.tags.length ? 'Edit Tags' : 'Add Tags' }}</button>
          </div>
        </div>

        <div class="detail-id">
          <code>{{ task.id }}</code>
          <button class="btn btn-sm detail-copy-id" @click="copyId" title="Copy ticket ID">Copy ID</button>
          <span class="detail-metric-pill" title="Total active worker time">{{ formatTaskTime(displayedTaskTimeMs) }}</span>
          <span v-if="task.tokens" class="token-count" title="Total tokens used by agents">{{ formatTokens(task.tokens) }}</span>
        </div>

        <div class="detail-body">
          <div class="detail-section-header">
            <h3>Description</h3>
            <button v-if="!readOnly" class="btn btn-sm" @click="toggleEdit">{{ editing ? 'Preview' : 'Edit' }}</button>
          </div>
          <div v-if="editing && !readOnly">
            <textarea class="form-textarea detail-editor" v-model="editBody" rows="12" @keydown.meta.enter="saveEdit" @keydown.ctrl.enter="saveEdit"></textarea>
            <div class="detail-edit-actions">
              <button class="btn btn-sm" @click="cancelEdit">Cancel</button>
              <button class="btn btn-sm btn-primary" @click="saveEdit">Save</button>
            </div>
          </div>
          <div v-else class="detail-rendered" v-html="renderedBody"></div>
        </div>

        <div class="detail-output" v-if="agentOutput">
          <div class="detail-section-header">
            <h3>Worker Output</h3>
            <button v-if="!readOnly" class="btn btn-sm btn-danger" @click="$emit('clear-output', task.id)">Clear</button>
          </div>
          <pre class="detail-output-content"><span v-for="line in parsedAgentOutputLines" :key="line.id" class="detail-output-line"><template v-if="line.commitHash">{{ line.prefix }}<button class="detail-output-commit" @click="openCommitDiff(line.commitHash)">{{ line.commitHash }}</button>{{ line.suffix }}</template><template v-else>{{ line.prefix }}</template></span></pre>
        </div>

        <div v-if="!readOnly" class="detail-footer">
          <button class="btn btn-danger btn-sm" @click="confirmDelete">Delete Ticket</button>
          <button v-if="task.status === 'done'" class="btn btn-sm" @click="$emit('archive', task.id); $emit('close')">Archive</button>
        </div>
      </div>
    </div>
  `,
  mounted() {
    this.taskTimeTimer = window.setInterval(() => {
      this.nowMs = Date.now();
    }, 1000);
    renderLucideIcons(this.$el);
  },
  methods: {
    startEditTitle() {
      if (this.readOnly) return;
      this.editTitle = this.task.title;
      this.editingTitle = true;
      this.$nextTick(() => this.$refs.titleInput?.focus());
    },
    saveTitle() {
      const title = this.editTitle.trim();
      if (title && title !== this.task.title) {
        this.$emit('update', { id: this.task.id, title });
      }
      this.editingTitle = false;
    },
    cancelTitle() {
      this.editingTitle = false;
    },
    startEditTags() {
      if (this.readOnly) return;
      this.editTagsValue = (this.task.tags || []).join(', ');
      this.editingTags = true;
    },
    saveTags() {
      const tags = this.editTagsValue.split(',').map(t => t.trim()).filter(Boolean);
      this.$emit('update', { id: this.task.id, tags });
      this.editingTags = false;
    },
    cancelTags() {
      this.editingTags = false;
    },
    toggleEdit() {
      if (this.readOnly) return;
      if (!this.editing) {
        this.editBody = this.bodyWithoutOutput;
        this.editing = true;
      } else {
        this.editing = false;
      }
    },
    cancelEdit() {
      this.editing = false;
    },
    saveEdit() {
      let newBody = this.editBody;
      // Preserve agent output if any
      if (this.agentOutput) {
        newBody = newBody.trimEnd() + '\n\n## Agent Output\n' + this.agentOutput;
      }
      this.$emit('update', { id: this.task.id, body: newBody });
      this.editing = false;
    },
    confirmDelete() {
      if (confirm('Delete this task? This cannot be undone.')) {
        this.$emit('delete', this.task.id);
        this.$emit('close');
      }
    },
    async copyId() {
      const id = this.task?.id || '';
      if (!id) return;
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(id);
        } else {
          this.copyIdFallback(id);
        }
        this.$emit('toast', 'Ticket ID copied', 'success');
      } catch (e) {
        try {
          this.copyIdFallback(id);
          this.$emit('toast', 'Ticket ID copied', 'success');
        } catch (fallbackError) {
          this.$emit('toast', 'Could not copy ticket ID', 'error');
        }
      }
    },
    copyIdFallback(id) {
      const el = document.createElement('textarea');
      el.value = id;
      el.setAttribute('readonly', '');
      el.style.position = 'fixed';
      el.style.left = '-9999px';
      document.body.appendChild(el);
      el.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(el);
      if (!ok) throw new Error('copy failed');
    },
    formatTokens(n) {
      if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M tok';
      if (n >= 1000) return (n / 1000).toFixed(1) + 'k tok';
      return n + ' tok';
    },
    formatTaskTime(ms) {
      return formatTaskDuration(ms);
    },
    columnLabel(key) {
      const col = (this.columns || []).find(c => c.key === key);
      return col ? col.label : (key || '—');
    },
    openCommitDiff(hash) {
      if (!hash) return;
      this.$emit('open-commit-diff', hash);
    },
    onResizeDown(e) {
      if (e.button !== 0) return;
      if (this.resizing) return;
      e.preventDefault();
      e.stopPropagation();
      this.resizing = {
        startX: e.clientX,
        startWidth: this.panelWidth,
        pointerId: e.pointerId,
      };
      this.draggingWidth = this.panelWidth;
      this._resizeMoveHandler = (ev) => this.onResizeMove(ev);
      this._resizeUpHandler = (ev) => this.onResizeUp(ev);
      window.addEventListener('pointermove', this._resizeMoveHandler);
      window.addEventListener('pointerup', this._resizeUpHandler);
      window.addEventListener('pointercancel', this._resizeUpHandler);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    },
    onResizeMove(e) {
      if (!this.resizing) return;
      if (e.pointerId !== this.resizing.pointerId) return;
      // Dragging the left edge: moving left (negative dx) widens the panel.
      const dx = e.clientX - this.resizing.startX;
      const next = this.resizing.startWidth - dx;
      this.draggingWidth = TaskDetailPanel._clampWidth(Math.round(next));
    },
    onResizeUp(e) {
      if (!this.resizing) return;
      if (e && e.pointerId !== this.resizing.pointerId) return;
      this._teardownResizeListeners();
      const dragged = this.draggingWidth;
      this.resizing = null;
      this.draggingWidth = null;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      if (dragged != null) {
        this.panelWidth = TaskDetailPanel._clampWidth(dragged);
        try {
          localStorage.setItem('bullpen.detailPanelWidth', String(this.panelWidth));
        } catch (e) { /* ignore */ }
      }
    },
    resetWidth() {
      this.panelWidth = 380;
      try {
        localStorage.setItem('bullpen.detailPanelWidth', '380');
      } catch (e) { /* ignore */ }
    },
    _teardownResizeListeners() {
      if (this._resizeMoveHandler) {
        window.removeEventListener('pointermove', this._resizeMoveHandler);
        window.removeEventListener('pointerup', this._resizeUpHandler);
        window.removeEventListener('pointercancel', this._resizeUpHandler);
        this._resizeMoveHandler = null;
        this._resizeUpHandler = null;
      }
    },
  },
  beforeUnmount() {
    if (this.taskTimeTimer) {
      window.clearInterval(this.taskTimeTimer);
      this.taskTimeTimer = null;
    }
    this._teardownResizeListeners();
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  },
  _clampWidth(w) {
    return Math.max(280, Math.min(900, w));
  },
  _loadPanelWidth() {
    try {
      const raw = localStorage.getItem('bullpen.detailPanelWidth');
      if (raw) {
        const n = parseInt(raw, 10);
        if (Number.isFinite(n)) return Math.max(280, Math.min(900, n));
      }
    } catch (e) { /* ignore */ }
    return 380;
  },
};
