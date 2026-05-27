const LeftPane = {
  props: ['tasks', 'layout', 'visible', 'config', 'projects', 'projectsLoaded', 'projectsRoot', 'activeWorkspaceId', 'workspaces'],
  emits: ['new-task', 'select-task', 'switch-workspace', 'add-project', 'new-project', 'clone-project', 'remove-project'],
  template: `
    <div class="left-pane" :class="{ collapsed: !visible, resizing: !!resizing }" :style="leftPaneStyle">
      <div
        v-if="visible"
        class="left-pane-resize"
        @pointerdown="onResizeDown"
        @dblclick="resetWidth"
        title="Drag to resize"
      ></div>
      <div class="left-pane-scroll">
      <div v-if="projects" class="left-pane-section" :class="{ 'project-add-only': projects.length === 0 }">
        <div class="section-header">
          <h3>Projects</h3>
          <div class="project-menu-wrap" @click.stop>
            <button class="btn btn-sm" @click="toggleProjectMenu">...</button>
            <div v-if="showEmptyProjectHint" class="project-menu-tooltip" role="status" aria-live="polite">
              <span>Add a project from /workspace to start.</span>
              <button class="btn-icon project-hint-dismiss" title="Dismiss" @click.stop="dismissEmptyProjectHint">&times;</button>
            </div>
            <div v-if="showProjectMenu" class="project-menu">
              <button class="project-menu-item" @click="promptAddProject"><i class="menu-item-icon" data-lucide="folder-open" aria-hidden="true"></i><span class="menu-item-label">Add Project</span></button>
              <button class="project-menu-item" @click="promptNewProject"><i class="menu-item-icon" data-lucide="folder-plus" aria-hidden="true"></i><span class="menu-item-label">New Project</span></button>
              <button class="project-menu-item" @click="promptCloneProject"><i class="menu-item-icon" data-lucide="git-branch-plus" aria-hidden="true"></i><span class="menu-item-label">Clone from Git</span></button>
            </div>
          </div>
        </div>
        <div v-if="projects.length > 0" class="project-list">
          <div v-for="p in projects" :key="p.id"
               class="project-item"
               :class="{ active: p.id === activeWorkspaceId, unavailable: p.available === false }"
               @click="p.available !== false && $emit('switch-workspace', p.id)"
               :title="p.available === false ? 'Directory not found: ' + p.path : ''">
            <span class="project-name">
              <i class="project-label-icon" :data-lucide="p.available === false ? 'folder-x' : 'folder'" aria-hidden="true"></i>
              <span class="project-label-text">{{ p.name }}</span>
            </span>
            <span class="project-item-actions">
              <span v-if="p.available !== false && unseenCount(p.id)" class="project-badge">{{ unseenCount(p.id) }}</span>
              <button
                v-if="canRemoveProject(p)"
                class="btn-icon project-remove-btn"
                :class="{ 'project-remove-btn--visible': p.available === false }"
                title="Remove project"
                @click.stop="confirmRemoveProject(p)"
              >&times;</button>
            </span>
          </div>
        </div>
      </div>
      <div v-if="activeWorkspaceId" class="left-pane-section">
        <div class="section-header">
          <select class="column-select" v-model="selectedColumn">
            <option v-for="col in columns" :key="col.key" :value="col.key">{{ col.label }}</option>
          </select>
          <button class="btn btn-sm" @click="$emit('new-task')">+ New Ticket</button>
        </div>
        <div class="inbox-list">
          <div v-if="filteredTasks.length === 0" class="empty-state">No tickets in {{ selectedColumnLabel }}</div>
          <div v-for="task in filteredTasks" :key="task.id"
               class="inbox-item"
               draggable="true"
               @dragstart="onDragStart($event, task.id)"
               @dragend="onDragEnd"
               @click="$emit('select-task', task.id)">
            <span class="inbox-title-wrap">
              <i class="ticket-type-icon ticket-type-icon--inbox" data-lucide="tag" aria-hidden="true"></i>
              <span class="inbox-title">{{ task.title }}</span>
            </span>
            <span class="badge" :class="'priority-' + (task.priority || 'normal')">{{ task.priority || 'normal' }}</span>
          </div>
        </div>
      </div>
      <div v-if="activeWorkspaceId" class="left-pane-section">
        <div class="section-header">
          <h3>Workers</h3>
        </div>
        <div class="worker-roster">
          <div v-if="!workerList.length" class="empty-state">No workers configured</div>
          <div v-for="w in workerList" :key="w.slot"
               class="roster-item"
               :class="{ 'drag-over': rosterDragSlot === w.slot }"
               :style="{ background: workerColor(w) }"
               @dragover.prevent="onRosterDragOver($event, w)"
               @dragleave="onRosterDragLeave"
               @drop="onRosterDrop($event, w.slot)">
            <span class="roster-name">
              <i class="worker-type-icon worker-type-icon--roster" :data-lucide="workerTypeIcon(w)" aria-hidden="true"></i>
              <span class="roster-label">{{ w.name }}</span>
            </span>
            <span class="status-pill" :class="'status-' + (w.state || 'idle')">{{ workerStatusLabel(w) }}</span>
          </div>
        </div>
      </div>
      </div>
    </div>
  `,
  computed: {
    columns() {
      return this.config?.columns || [{ key: 'inbox', label: 'Inbox' }];
    },
    selectedColumnLabel() {
      const col = this.columns.find(c => c.key === this.selectedColumn);
      return col ? col.label : this.selectedColumn;
    },
    filteredTasks() {
      const weight = { urgent: 0, high: 1, normal: 2, low: 3 };
      return (this.tasks || [])
        .filter(t => t.status === this.selectedColumn)
        .sort((a, b) => {
          const pa = weight[a.priority] ?? weight.normal;
          const pb = weight[b.priority] ?? weight.normal;
          if (pa !== pb) return pa - pb;
          return (b.created_at || '').localeCompare(a.created_at || '');
        });
    },
    workerList() {
      if (!this.layout?.slots) return [];
      const slots = this.layout.slots;
      const cols = Number(this.config?.grid?.cols) || 4;

      const entries = [];
      slots.forEach((s, i) => {
        if (!s) return;
        const row = Number.isFinite(Number(s.row)) ? Number(s.row) : Math.floor(i / cols);
        const col = Number.isFinite(Number(s.col)) ? Number(s.col) : (i % cols);
        entries.push({ slot: i, slotData: s, row, col });
      });

      const normName = (n) => String(n || '').trim().toLowerCase().replace(/\s+/g, ' ');
      const byCoord = new Map();
      const byNormalizedName = new Map();
      entries.forEach(e => {
        byCoord.set(`${e.row},${e.col}`, e);
        const n = normName(e.slotData.name);
        if (n && !byNormalizedName.has(n)) byNormalizedName.set(n, e);
      });

      const gridOrder = entries.slice().sort((a, b) => a.row - b.row || a.col - b.col);

      const passTarget = (e) => {
        const disp = String(e.slotData.disposition || '').trim();
        if (!disp) return null;
        if (disp.startsWith('worker:')) {
          return byNormalizedName.get(normName(disp.slice(7))) || null;
        }
        if (disp.startsWith('pass:')) {
          const dir = disp.slice(5).trim().toLowerCase();
          const delta = { up: [-1, 0], down: [1, 0], left: [0, -1], right: [0, 1] }[dir];
          if (!delta) return null;
          return byCoord.get(`${e.row + delta[0]},${e.col + delta[1]}`) || null;
        }
        return null;
      };

      const visited = new Set();
      const ordered = [];

      for (const m of gridOrder) {
        if (m.slotData.type !== 'marker') continue;
        if (visited.has(m.slot)) continue;

        const groupSet = new Set([m.slot]);
        const queue = [m];

        const enqueue = (e) => {
          if (!e || groupSet.has(e.slot) || visited.has(e.slot)) return;
          if (e.slotData.type === 'marker') return;
          groupSet.add(e.slot);
          queue.push(e);
        };

        const sameColBelow = entries
          .filter(e => e.col === m.col && e.row > m.row)
          .sort((a, b) => a.row - b.row);
        let prevColRow = null;
        for (const e of sameColBelow) {
          if (prevColRow !== null && e.row !== prevColRow + 1) break;
          if (e.slotData.type === 'marker') break;
          enqueue(e);
          prevColRow = e.row;
        }

        const members = [];
        while (queue.length) {
          const cur = queue.shift();
          members.push(cur);
          enqueue(passTarget(cur));
        }

        members.sort((a, b) => a.row - b.row || a.col - b.col);
        for (const g of members) {
          if (visited.has(g.slot)) continue;
          visited.add(g.slot);
          ordered.push(g);
        }
      }

      for (const e of gridOrder) {
        if (visited.has(e.slot)) continue;
        visited.add(e.slot);
        ordered.push(e);
      }

      return ordered.map(e => {
        const s = e.slotData;
        return {
          slot: e.slot,
          name: s.name,
          state: s.state || 'idle',
          retry_at: s.retry_at,
          retry_attempt: s.retry_attempt,
          retry_max: s.retry_max,
          agent: s.agent,
          type: s.type,
          taskQueueLength: Array.isArray(s.task_queue) ? s.task_queue.length : 0,
        };
      });
    },
    projectIconToken() {
      return (this.projects || [])
        .map(p => `${p.id}:${p.available === false ? 'folder-x' : 'folder'}:${p.id === this.activeWorkspaceId ? 'active' : ''}`)
        .join('|');
    },
    inboxIconToken() {
      return this.filteredTasks.map(task => task.id).join('|');
    },
    workerIconToken() {
      return this.workerList
        .map(worker => `${worker.slot}:${this.workerTypeIcon(worker)}`)
        .join('|');
    },
    leftPaneStyle() {
      if (!this.visible) return null;
      return { width: `${this.draggingWidth || this.paneWidth}px` };
    },
    projectCount() {
      return Array.isArray(this.projects) ? this.projects.length : 0;
    }
  },
  watch: {
    columns(cols) {
      if (!cols.some(c => c.key === this.selectedColumn)) {
        this.selectedColumn = 'inbox';
      }
    },
    projectsLoaded: {
      immediate: true,
      handler(loaded) {
        // Only evaluate the empty-state hint once the server has delivered the
        // real project list. The reactive([]) initial value is pre-load and
        // must not be mistaken for "user has zero projects".
        if (!loaded || this.emptyProjectHintInitialized) return;
        this.emptyProjectHintInitialized = true;
        if (this.projectCount === 0 && !this.activeWorkspaceId) {
          this.showProjectMenu = true;
          this.showEmptyProjectHint = true;
          window.dispatchEvent(new Event('bullpen:menu:close-main'));
        }
      }
    },
    projectCount(count) {
      if (count > 0) {
        this.showEmptyProjectHint = false;
        this.showProjectMenu = false;
      }
    },
    activeWorkspaceId(next) {
      if (next) {
        this.showEmptyProjectHint = false;
        this.showProjectMenu = false;
      }
    },
    showProjectMenu(next) {
      if (next) this.$nextTick(() => renderLucideIcons(this.$el));
    },
    projectIconToken() {
      this.$nextTick(() => renderLucideIcons(this.$el));
    },
    inboxIconToken() {
      this.$nextTick(() => renderLucideIcons(this.$el));
    },
    workerIconToken() {
      this.$nextTick(() => renderLucideIcons(this.$el));
    },
  },
  data() {
    return {
      rosterDragSlot: null,
      selectedColumn: 'inbox',
      showProjectMenu: false,
      showEmptyProjectHint: false,
      emptyProjectHintInitialized: false,
      paneWidth: LeftPane._loadPaneWidth(),
      resizing: null,
      draggingWidth: null,
    };
  },
  mounted() {
    document.addEventListener('click', this.onGlobalClick);
    window.addEventListener('bullpen:menu:close-projects', this.onExternalCloseProjectMenu);
    renderLucideIcons(this.$el);
  },
  beforeUnmount() {
    document.removeEventListener('click', this.onGlobalClick);
    window.removeEventListener('bullpen:menu:close-projects', this.onExternalCloseProjectMenu);
    this._teardownResizeListeners();
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  },
  methods: {
    toggleProjectMenu() {
      this.showProjectMenu = !this.showProjectMenu;
      this.showEmptyProjectHint = false;
      if (this.showProjectMenu) {
        window.dispatchEvent(new Event('bullpen:menu:close-main'));
      }
    },
    onGlobalClick() {
      this.showProjectMenu = false;
      this.showEmptyProjectHint = false;
    },
    onExternalCloseProjectMenu() {
      this.showProjectMenu = false;
      this.showEmptyProjectHint = false;
    },
    dismissEmptyProjectHint() {
      this.showEmptyProjectHint = false;
    },
    onDragStart(e, taskId) {
      window.dispatchEvent(new Event('bullpen:task-drag:start'));
      e.dataTransfer.setData(window.BULLPEN_TASK_DND_MIME, taskId);
      e.dataTransfer.setData('text/plain', taskId);
      e.dataTransfer.effectAllowed = 'move';
    },
    onDragEnd() {
      window.dispatchEvent(new Event('bullpen:task-drag:end'));
    },
    agentColor(agent) {
      return agentColor(agent);
    },
    workerColor(worker) {
      return workerColor(worker);
    },
    workerTypeIcon(worker) {
      return getWorkerTypeIcon(worker);
    },
    workerStatusLabel(worker) {
      const state = (worker?.state || 'idle').toUpperCase();
      if (state === 'RETRYING') {
        const attempt = Number(worker?.retry_attempt || 0);
        const max = Number(worker?.retry_max || 0);
        return attempt && max ? `${state} (${attempt}/${max})` : state;
      }
      if (state !== 'WORKING') return state;
      const queueCount = Math.max(1, Number(worker?.taskQueueLength || 0));
      return `${state} (${queueCount})`;
    },
    unseenCount(wsId) {
      if (!this.workspaces || !this.workspaces[wsId]) return 0;
      return this.workspaces[wsId].unseenActivity || 0;
    },
    canRemoveProject(project) {
      return !!(project && project.id && this.projects && this.projects.length > 1);
    },
    activeWorkspacePath() {
      return this.workspaces?.[this.activeWorkspaceId]?.workspace || '';
    },
    defaultCloneParent() {
      const workspace = this.activeWorkspacePath();
      if (!workspace) return '';
      const parts = workspace.split(/[\\/]+/);
      parts.pop();
      const parent = parts.join('/') || (workspace.startsWith('/') ? '/' : '');
      return parent || '';
    },
    projectEntryRoot() {
      const root = String(this.projectsRoot || '').trim();
      return root || this.defaultCloneParent();
    },
    repoNameFromUrl(url) {
      const trimmed = String(url || '').trim().replace(/\/+$/, '');
      const name = trimmed.split('/').filter(Boolean).pop() || '';
      return name.endsWith('.git') ? name.slice(0, -4) : name;
    },
    confirmRemoveProject(project) {
      const name = project?.name || 'this project';
      if (!project?.id) return;
      if (!confirm(`Remove "${name}" from the project list?\n\nThis only unregisters the project from Bullpen. No project files are deleted.`)) {
        return;
      }
      this.$emit('remove-project', project.id);
    },
    promptAddProject() {
      this.showProjectMenu = false;
      this.showEmptyProjectHint = false;
      const root = this.projectEntryRoot();
      const promptText = root
        ? `Enter project directory under ${root} (name or absolute path):`
        : 'Enter absolute path to project directory:';
      const path = prompt(promptText);
      if (path && path.trim()) {
        this.$emit('add-project', path.trim());
      }
    },
    promptNewProject() {
      this.showProjectMenu = false;
      this.showEmptyProjectHint = false;
      const root = this.projectEntryRoot();
      const promptText = root
        ? `Enter new project directory under ${root} (name or absolute path):`
        : 'Enter absolute path for new project directory:';
      const path = prompt(promptText);
      if (path && path.trim()) {
        this.$emit('new-project', path.trim());
      }
    },
    promptCloneProject() {
      this.showProjectMenu = false;
      this.showEmptyProjectHint = false;
      const url = prompt('Enter Git repository URL:');
      if (!url || !url.trim()) return;
      const repoName = this.repoNameFromUrl(url);
      const defaultPath = this.defaultCloneParent() && repoName ? `${this.defaultCloneParent()}/${repoName}` : '';
      const promptText = defaultPath
        ? `Enter absolute path to clone into (leave empty for ${defaultPath}):`
        : 'Enter absolute path to clone into (leave empty for the server default):';
      const path = prompt(promptText);
      this.$emit('clone-project', { url: url.trim(), path: (path || '').trim() || null });
    },
    onRosterDragOver(e, w) {
      const types = e.dataTransfer.types;
      if (types.includes(window.BULLPEN_TASK_DND_MIME) || (window.BULLPEN_TASK_DRAG_ACTIVE && types.includes('text/plain'))) {
        e.dataTransfer.dropEffect = 'move';
        this.rosterDragSlot = w.slot;
      }
    },
    onRosterDragLeave() {
      this.rosterDragSlot = null;
    },
    onRosterDrop(e, slot) {
      e.preventDefault();
      this.rosterDragSlot = null;
      const taskId = e.dataTransfer.getData(window.BULLPEN_TASK_DND_MIME)
        || (window.BULLPEN_TASK_DRAG_ACTIVE ? e.dataTransfer.getData('text/plain') : '');
      if (taskId) {
        this.$root.assignTask(taskId, slot);
      }
    },
    onResizeDown(e) {
      if (e.button !== 0) return;
      if (this.resizing) return;
      e.preventDefault();
      e.stopPropagation();
      this.resizing = {
        startX: e.clientX,
        startWidth: this.paneWidth,
        pointerId: e.pointerId,
      };
      this.draggingWidth = this.paneWidth;
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
      const dx = e.clientX - this.resizing.startX;
      const next = this.resizing.startWidth + dx;
      this.draggingWidth = LeftPane._clampWidth(Math.round(next));
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
        this.paneWidth = LeftPane._clampWidth(dragged);
        try {
          localStorage.setItem('bullpen.leftPaneWidth', String(this.paneWidth));
        } catch (e) { /* ignore */ }
      }
    },
    resetWidth() {
      this.paneWidth = 280;
      try {
        localStorage.setItem('bullpen.leftPaneWidth', '280');
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
    }
  },
  _clampWidth(w) {
    return Math.max(200, Math.min(520, w));
  },
  _loadPaneWidth() {
    try {
      const raw = localStorage.getItem('bullpen.leftPaneWidth');
      if (raw) {
        const n = parseInt(raw, 10);
        if (Number.isFinite(n)) return LeftPane._clampWidth(n);
      }
    } catch (e) { /* ignore */ }
    return 280;
  }
};
