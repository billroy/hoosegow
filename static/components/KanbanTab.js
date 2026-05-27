const KanbanTab = {
  props: ['tasks', 'columns', 'layout', 'viewMode', 'listScope'],
  emits: ['select-task', 'move-task', 'archive-done', 'new-task', 'update-list-scope', 'update-task', 'update-shown-count'],
  components: { TaskCard },
  template: `
    <div v-if="viewMode !== 'list'" class="kanban-board">
      <div v-for="(col, colIdx) in boardColumns" :key="col.key" class="kanban-column"
           @dragover.prevent="onDragOver($event, col.key)"
           @dragleave="onDragLeave($event)"
           @drop.prevent="onDrop($event, col.key)">
        <div class="kanban-column-header" :style="{ borderTopColor: col.color }">
          <div class="column-title">
            <i class="column-icon" :data-lucide="columnIcon(col)" aria-hidden="true"></i>
            <span class="column-label">{{ col.label }}</span>
          </div>
          <span class="column-count">{{ columnTasks(col.key).length }}</span>
          <button v-if="col.key === 'done' && columnTasks(col.key).length"
                  class="btn btn-sm column-archive-btn"
                  @click="$emit('archive-done')"
                  title="Archive all done tickets">Archive</button>
        </div>
        <div class="kanban-column-body">
          <div v-if="columnTasks(col.key).length === 0" class="empty-state kanban-drop-zone">—</div>
          <TaskCard
            v-for="task in columnTasks(col.key)"
            :key="task.id"
            :task="task"
            :layout="layout"
            @select-task="$emit('select-task', $event)"
            @update-task="$emit('update-task', $event)"
          />
        </div>
      </div>
      </div>
      <div v-else class="ticket-list">
        <div class="ticket-list-filters">
        <label class="ticket-list-filter ticket-list-filter-search">
          <span class="ticket-list-filter-label">Search</span>
          <input
            v-model.trim="searchText"
            class="form-input ticket-list-search-input"
            type="search"
            placeholder="Search title, description, tags, id..."
            aria-label="Search tickets"
          >
        </label>
        <label class="ticket-list-filter">
          <span class="ticket-list-filter-label">View</span>
          <select
            :value="listScope || 'live'"
            class="form-select ticket-list-filter-select"
            aria-label="View live or archived tickets"
            @change="$emit('update-list-scope', $event.target.value)"
          >
            <option value="live">Live tickets</option>
            <option value="archived">Archived tickets</option>
          </select>
        </label>
        <label class="ticket-list-filter">
          <span class="ticket-list-filter-label">Priority</span>
          <select v-model="priorityFilter" class="form-select ticket-list-filter-select" aria-label="Filter by priority">
            <option v-for="opt in priorityOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </label>
        <label class="ticket-list-filter">
          <span class="ticket-list-filter-label">Status</span>
          <select v-model="statusFilter" class="form-select ticket-list-filter-select" aria-label="Filter by status">
            <option v-for="opt in statusOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </label>
        <label class="ticket-list-filter">
          <span class="ticket-list-filter-label">Type</span>
          <select v-model="typeFilter" class="form-select ticket-list-filter-select" aria-label="Filter by type">
            <option v-for="opt in typeOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </label>
        <div class="ticket-list-summary" aria-live="polite">
          <span class="ticket-list-summary-label">All ticket time</span>
          <span class="ticket-list-summary-value">{{ formatTaskTime(totalTaskTimeMs) }}</span>
        </div>
      </div>
      <table class="ticket-list-table">
        <thead>
          <tr>
            <th class="ticket-list-col-priority ticket-list-th-sortable" @click="setSort('priority')">
              Priority<span class="sort-indicator">{{ sortIndicator('priority') }}</span>
            </th>
            <th class="ticket-list-col-title ticket-list-th-sortable" @click="setSort('title')">
              Title<span class="sort-indicator">{{ sortIndicator('title') }}</span>
            </th>
            <th class="ticket-list-col-status ticket-list-th-sortable" @click="setSort('status')">
              Status<span class="sort-indicator">{{ sortIndicator('status') }}</span>
            </th>
            <th class="ticket-list-col-type ticket-list-th-sortable" @click="setSort('type')">
              Type<span class="sort-indicator">{{ sortIndicator('type') }}</span>
            </th>
            <th class="ticket-list-col-worker ticket-list-th-sortable" @click="setSort('assigned')">
              Assigned<span class="sort-indicator">{{ sortIndicator('assigned') }}</span>
            </th>
            <th class="ticket-list-col-date ticket-list-th-sortable" @click="setSort('created_at')">
              Created<span class="sort-indicator">{{ sortIndicator('created_at') }}</span>
            </th>
            <th class="ticket-list-col-task-time ticket-list-th-sortable" @click="setSort('task_time_ms')">
              Task Time<span class="sort-indicator">{{ sortIndicator('task_time_ms') }}</span>
            </th>
            <th class="ticket-list-col-tokens ticket-list-th-sortable" @click="setSort('tokens')">
              Tokens<span class="sort-indicator">{{ sortIndicator('tokens') }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="sortedTasks.length === 0">
            <td colspan="8" class="ticket-list-empty">No tickets</td>
          </tr>
          <tr
            v-for="task in sortedTasks"
            :key="task.id"
            class="ticket-list-row"
            @click="onListRowClick(task)"
          >
            <td class="ticket-list-col-priority">
              <span class="badge" :class="'priority-' + (task.priority || 'normal')">{{ task.priority || 'normal' }}</span>
            </td>
            <td class="ticket-list-col-title">
              <div class="ticket-list-title-wrap">
                <i class="ticket-type-icon ticket-type-icon--list" data-lucide="tag" aria-hidden="true"></i>
                <span class="ticket-list-title-text">{{ task.title }}</span>
              </div>
            </td>
            <td class="ticket-list-col-status">
              <span
                class="ticket-list-status-pill"
                :class="statusPillClass(task.status)"
                :style="statusPillStyle(task.status)"
              >{{ columnLabel(task.status) }}</span>
            </td>
            <td class="ticket-list-col-type">
              <span class="badge type-badge" :class="'type-' + (task.type || 'task')">{{ task.type || 'task' }}</span>
            </td>
            <td class="ticket-list-col-worker">{{ workerName(task) || '—' }}</td>
            <td class="ticket-list-col-date">{{ formatDate(task.created_at) }}</td>
            <td class="ticket-list-col-task-time">{{ formatTaskTime(displayTaskTimeMs(task)) }}</td>
            <td class="ticket-list-col-tokens">{{ task.tokens ? formatTokens(task.tokens) : '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  `,
  data() {
    return {
      sortField: 'status',
      sortDir: 'asc',
      searchText: '',
      priorityFilter: 'all',
      statusFilter: 'all',
      typeFilter: 'all',
      nowMs: Date.now(),
      taskTimeTimer: null,
    };
  },
  mounted() {
    this.taskTimeTimer = window.setInterval(() => {
      this.nowMs = Date.now();
    }, 1000);
    renderLucideIcons(this.$el);
  },
  beforeUnmount() {
    if (this.taskTimeTimer) {
      window.clearInterval(this.taskTimeTimer);
      this.taskTimeTimer = null;
    }
  },
  watch: {
    filteredTasks: {
      immediate: true,
      handler(tasks) {
        this.$emit('update-shown-count', Array.isArray(tasks) ? tasks.length : 0);
      },
    },
    iconRenderToken() {
      this.$nextTick(() => renderLucideIcons(this.$el));
    },
  },
  computed: {
    priorityOptions() {
      return [
        { value: 'all', label: 'All priorities' },
        { value: 'urgent', label: 'Urgent' },
        { value: 'high', label: 'High' },
        { value: 'normal', label: 'Normal' },
        { value: 'low', label: 'Low' },
      ];
    },
    statusOptions() {
      const options = [{ value: 'all', label: 'All statuses' }];
      this.boardColumns.forEach(col => {
        options.push({ value: col.key, label: col.label || col.key });
      });
      return options;
    },
    typeOptions() {
      const known = ['task', 'bug', 'feature', 'chore'];
      const seen = new Set(known);
      (this.tasks || []).forEach(task => {
        const type = task?.type;
        if (type) seen.add(type);
      });
      return [{ value: 'all', label: 'All types' }].concat(
        Array.from(seen).sort((a, b) => a.localeCompare(b)).map(value => ({
          value,
          label: value.charAt(0).toUpperCase() + value.slice(1),
        }))
      );
    },
    filteredTasks() {
      const query = (this.searchText || '').trim().toLowerCase();
      return (this.tasks || []).filter(task => {
        if (this.priorityFilter !== 'all' && (task.priority || 'normal') !== this.priorityFilter) return false;
        if (this.statusFilter !== 'all' && (task.status || '') !== this.statusFilter) return false;
        if (this.typeFilter !== 'all' && (task.type || 'task') !== this.typeFilter) return false;
        if (!query) return true;
        const haystack = [
          task.id,
          task.title,
          task.description,
          task.status,
          task.type,
          task.priority,
          this.workerName(task),
          Array.isArray(task.tags) ? task.tags.join(' ') : '',
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();
        return haystack.includes(query);
      });
    },
    totalTaskTimeMs() {
      return (this.tasks || []).reduce((sum, task) => sum + this.displayTaskTimeMs(task), 0);
    },
    sortedTasks() {
      const weight = { urgent: 0, high: 1, normal: 2, low: 3 };
      const colOrder = {};
      this.boardColumns.forEach((c, i) => { colOrder[c.key] = i; });
      const dir = this.sortDir === 'asc' ? 1 : -1;
      return this.filteredTasks.slice().sort((a, b) => {
        let cmp = 0;
        switch (this.sortField) {
          case 'status': {
            const ca = colOrder[a.status] ?? 99;
            const cb = colOrder[b.status] ?? 99;
            cmp = ca - cb;
            if (cmp === 0) {
              const pa = weight[a.priority] ?? weight.normal;
              const pb = weight[b.priority] ?? weight.normal;
              cmp = pa - pb;
            }
            break;
          }
          case 'priority': {
            const pa = weight[a.priority] ?? weight.normal;
            const pb = weight[b.priority] ?? weight.normal;
            cmp = pa - pb;
            break;
          }
          case 'title':
            cmp = (a.title || '').localeCompare(b.title || '');
            break;
          case 'type':
            cmp = (a.type || '').localeCompare(b.type || '');
            break;
          case 'assigned': {
            const wa = this.workerName(a) || '';
            const wb = this.workerName(b) || '';
            cmp = wa.localeCompare(wb);
            break;
          }
          case 'created_at':
            cmp = (a.created_at || '').localeCompare(b.created_at || '');
            break;
          case 'task_time_ms':
            cmp = this.displayTaskTimeMs(a) - this.displayTaskTimeMs(b);
            break;
          case 'tokens':
            cmp = (a.tokens || 0) - (b.tokens || 0);
            break;
        }
        if (cmp === 0) cmp = (a.created_at || '').localeCompare(b.created_at || '');
        return cmp * dir;
      });
    },
    iconRenderToken() {
      const columnIcons = this.boardColumns
        .map(col => `${col.key}:${this.columnIcon(col)}`)
        .join('|');
      const taskIds = this.viewMode === 'list'
        ? this.sortedTasks.map(task => task.id).join('|')
        : (this.tasks || []).map(task => `${task.id}:${task.status || ''}`).join('|');
      return `${this.viewMode}|${columnIcons}|${taskIds}`;
    },
    boardColumns() {
      const cols = (this.columns || []).map(col => ({ ...col }));
      const known = new Set(cols.map(col => col.key));
      (this.tasks || []).forEach(task => {
        const status = task?.status;
        if (!status || known.has(status)) return;
        known.add(status);
        cols.push({
          key: status,
          label: this.missingColumnLabel(status),
          color: '#64748B',
          missing: true,
        });
      });
      return cols;
    }
  },
  methods: {
    isBuiltInStatus(key) {
      return ['inbox', 'assigned', 'in_progress', 'review', 'done', 'blocked'].includes(key);
    },
    statusPillClass(key) {
      return this.isBuiltInStatus(key) ? ('status-col-' + key) : '';
    },
    parseHexColor(color) {
      if (!color || typeof color !== 'string') return null;
      const raw = color.trim();
      if (!raw.startsWith('#')) return null;
      let hex = raw.slice(1);
      if (hex.length === 3) {
        hex = hex.split('').map(ch => ch + ch).join('');
      }
      if (!/^[0-9a-fA-F]{6}$/.test(hex)) return null;
      return {
        r: parseInt(hex.slice(0, 2), 16),
        g: parseInt(hex.slice(2, 4), 16),
        b: parseInt(hex.slice(4, 6), 16),
      };
    },
    statusPillStyle(key) {
      if (this.isBuiltInStatus(key)) return null;
      const col = this.boardColumns.find(c => c.key === key);
      const color = col?.color;
      if (!color) return null;
      const rgb = this.parseHexColor(color);
      if (!rgb) return { color };
      return {
        backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.2)`,
        color,
      };
    },
    columnTasks(key) {
      const weight = { urgent: 0, high: 1, normal: 2, low: 3 };
      return (this.tasks || [])
        .filter(t => t.status === key)
        .sort((a, b) => {
          const pa = weight[a.priority] ?? weight.normal;
          const pb = weight[b.priority] ?? weight.normal;
          if (pa !== pb) return pa - pb;
          return (a.created_at || '').localeCompare(b.created_at || '');
        });
    },
    columnIcon(col) {
      return getColumnIcon(col);
    },
    setSort(field) {
      if (this.sortField === field) {
        this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        this.sortField = field;
        this.sortDir = (field === 'created_at' || field === 'task_time_ms' || field === 'tokens') ? 'desc' : 'asc';
      }
    },
    sortIndicator(field) {
      if (this.sortField !== field) return '';
      return this.sortDir === 'asc' ? ' ↑' : ' ↓';
    },
    formatDate(iso) {
      if (!iso) return '—';
      const d = new Date(iso);
      if (isNaN(d)) return '—';
      return d.toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      });
    },
    columnLabel(key) {
      const col = this.boardColumns.find(c => c.key === key);
      return col ? col.label : this.missingColumnLabel(key);
    },
    missingColumnLabel(key) {
      return key ? `${key} (missing)` : 'Missing status';
    },
    formatTokens(n) {
      if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
      if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
      return String(n);
    },
    displayTaskTimeMs(task) {
      const base = getReportedTaskTimeMs(task);
      const startedMs = Date.parse(task?.active_task_started_at || '');
      if (!Number.isFinite(startedMs)) return base;
      return base + Math.max(this.nowMs - startedMs, 0);
    },
    formatTaskTime(ms) {
      return formatTaskDuration(ms);
    },
    workerName(task) {
      if (task.assigned_to == null) return null;
      const slot = parseInt(task.assigned_to, 10);
      if (isNaN(slot)) return null;
      return this.layout?.slots?.[slot]?.name || null;
    },
    isWorkerColumn(colKey) {
      return colKey === 'assigned' || colKey === 'in_progress';
    },
    taskStatus(taskId) {
      const t = (this.tasks || []).find(t => t.id === taskId);
      return t ? t.status : null;
    },
    onDragOver(e, colKey) {
      if (this.isWorkerColumn(colKey)) {
        e.dataTransfer.dropEffect = 'none';
        return;
      }
      const types = e.dataTransfer.types;
      if (!(types.includes(window.BULLPEN_TASK_DND_MIME) || (window.BULLPEN_TASK_DRAG_ACTIVE && types.includes('text/plain')))) {
        e.dataTransfer.dropEffect = 'none';
        return;
      }
      e.dataTransfer.dropEffect = 'move';
      e.currentTarget.classList.add('drag-over');
    },
    onDragLeave(e) {
      e.currentTarget.classList.remove('drag-over');
    },
    onDrop(e, colKey) {
      e.preventDefault();
      e.currentTarget.classList.remove('drag-over');
      if (this.isWorkerColumn(colKey)) return;
      const taskId = e.dataTransfer.getData(window.BULLPEN_TASK_DND_MIME)
        || (window.BULLPEN_TASK_DRAG_ACTIVE ? e.dataTransfer.getData('text/plain') : '');
      if (!taskId) return;
      const oldStatus = this.taskStatus(taskId);
      if (oldStatus === 'in_progress') {
        if (!confirm('This task has a running agent. Stop the agent and move the task?')) return;
      }
      this.$emit('move-task', { id: taskId, status: colKey });
    },
    onListRowClick(task) {
      if (!task?.id) return;
      this.$emit('select-task', { id: task.id, readOnly: true });
    }
  }
};
