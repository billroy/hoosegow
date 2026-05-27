/**
 * Event Sound Dispatcher.
 *
 * Maps socket events to short synthesized sounds through window.ambientAudio.
 * Subscribes to the Socket.IO connection, debounces rapid-fire events, and
 * suppresses the flood that normally arrives right after state:init.
 *
 * See docs/event-sounds.md for the event→sound mapping.
 */
(function () {
  const STORAGE_KEY = 'bullpen.eventSounds';
  const DEBOUNCE_MS = 120;
  const GLOBAL_MIN_GAP_MS = 250;
  const READY_DELAY_MS = 50;

  // Severity ordering for burst collapse: higher number wins.
  const SEVERITY = {
    error: 6,
    done: 5,
    start: 4,
    create: 3,
    delete: 2,
    revert: 1,
    move: 0,
    toast: 0,
  };

  const DEFAULT_FLAGS = {
    enabled: true,
    taskCreated: true,
    taskStarted: true,
    taskDone: true,
    taskDeleted: true,
    taskReverted: false,
    taskMoved: true,
    workerError: true,
    serverError: true,
    toast: true,
  };

  function _loadFlags() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return { ...DEFAULT_FLAGS };
      const parsed = JSON.parse(raw);
      return { ...DEFAULT_FLAGS, ...(parsed && typeof parsed === 'object' ? parsed : {}) };
    } catch (e) {
      return { ...DEFAULT_FLAGS };
    }
  }

  function _saveFlags(flags) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(flags));
    } catch (e) {}
  }

  const EventSounds = {
    flags: _loadFlags(),
    _ready: false,
    _recent: new Map(),          // `${kind}:${id}` → ts
    _lastGlobal: 0,
    _pendingBurst: null,         // { kind, playFn, severity, timer }
    _taskStatus: new Map(),      // taskId → last-known status
    _taskAssigned: new Map(),    // taskId → last-known assigned_to
    _layoutStates: new Map(),    // slotIndex → last-known worker state

    getFlags() {
      return { ...this.flags };
    },

    setFlag(key, value) {
      if (!(key in DEFAULT_FLAGS)) return;
      this.flags[key] = !!value;
      _saveFlags(this.flags);
    },

    resetFlags() {
      this.flags = { ...DEFAULT_FLAGS };
      _saveFlags(this.flags);
    },

    /** Seed task status map from a state:init payload and arm the dispatcher. */
    primeFromState(data) {
      const tasks = Array.isArray(data?.tasks) ? data.tasks : [];
      for (const t of tasks) {
        if (t && t.id) {
          this._taskStatus.set(t.id, t.status || null);
          this._taskAssigned.set(t.id, t.assigned_to ?? null);
        }
      }
      const slots = Array.isArray(data?.layout?.slots) ? data.layout.slots : [];
      slots.forEach((slot, i) => {
        this._layoutStates.set(i, slot ? (slot.state || 'idle') : null);
      });
      // Delay briefly so trailing replay messages don't squeak through.
      setTimeout(() => { this._ready = true; }, READY_DELAY_MS);
    },

    /** Called on every task:updated; returns status + assignment diffs. */
    recordTaskUpdate(task) {
      if (!task || !task.id) return null;
      const prevStatus = this._taskStatus.get(task.id);
      const nextStatus = task.status || null;
      const prevAssigned = this._taskAssigned.has(task.id)
        ? this._taskAssigned.get(task.id)
        : undefined;
      const nextAssigned = task.assigned_to ?? null;
      this._taskStatus.set(task.id, nextStatus);
      this._taskAssigned.set(task.id, nextAssigned);
      const statusChanged = prevStatus !== nextStatus;
      const assignedChanged = prevAssigned !== undefined && prevAssigned !== nextAssigned;
      if (!statusChanged && !assignedChanged) return null;
      return {
        prev: prevStatus,
        next: nextStatus,
        statusChanged,
        assignedChanged,
        prevAssigned,
        nextAssigned,
      };
    },

    recordTaskCreate(task) {
      if (task && task.id) {
        this._taskStatus.set(task.id, task.status || null);
        this._taskAssigned.set(task.id, task.assigned_to ?? null);
      }
    },

    recordTaskDelete(id) {
      if (id) {
        this._taskStatus.delete(id);
        this._taskAssigned.delete(id);
      }
    },

    /** Diff a layout:updated against last-known slot states; returns list of transitions. */
    diffLayout(layout) {
      const slots = Array.isArray(layout?.slots) ? layout.slots : [];
      const transitions = [];
      slots.forEach((slot, i) => {
        const prev = this._layoutStates.get(i);
        const next = slot ? (slot.state || 'idle') : null;
        if (prev !== next) transitions.push({ slot: i, prev, next });
        this._layoutStates.set(i, next);
      });
      return transitions;
    },

    /**
     * Attempt to fire a sound. Applies all policy gates.
     * @param {string} kind       Logical event kind (for severity + flag lookup)
     * @param {string} dedupeId   Entity id for per-entity debounce (pass '' for global)
     * @param {string} flagKey    Flag name in this.flags to check
     * @param {Function} playFn   Called to actually produce the sound
     */
    _fire(kind, dedupeId, flagKey, playFn) {
      if (!this._ready) return;
      if (!this.flags.enabled) return;
      if (flagKey && !this.flags[flagKey]) return;
      if (!window.ambientAudio) return;

      const now = Date.now();
      const dedupeKey = `${kind}:${dedupeId || ''}`;
      const lastSame = this._recent.get(dedupeKey) || 0;
      if (now - lastSame < DEBOUNCE_MS) return;
      this._recent.set(dedupeKey, now);

      const severity = SEVERITY[kind] ?? 0;
      const gap = now - this._lastGlobal;

      const wrapped = () => {
        try { window.ambientAudio._duckAmbient(6, 300); } catch (e) {}
        try { playFn(); } catch (e) {}
        this._lastGlobal = Date.now();
      };

      if (gap >= GLOBAL_MIN_GAP_MS && !this._pendingBurst) {
        wrapped();
        return;
      }

      // Inside rate-limit window: collapse to the highest-severity choice.
      if (this._pendingBurst && severity <= this._pendingBurst.severity) return;

      if (this._pendingBurst) {
        clearTimeout(this._pendingBurst.timer);
      }

      const delay = Math.max(0, GLOBAL_MIN_GAP_MS - gap);
      const burst = { kind, severity, play: wrapped };
      burst.timer = setTimeout(() => {
        this._pendingBurst = null;
        burst.play();
      }, delay);
      this._pendingBurst = burst;
    },

    /** Attach all socket listeners. Safe to call once at app startup. */
    init(socket) {
      if (!socket || this._attached) return;
      this._attached = true;

      socket.on('state:init', (data) => {
        // Always reseed so task/worker diffs are relative to fresh state.
        this._ready = false;
        this.primeFromState(data);
      });

      socket.on('task:created', (task) => {
        this.recordTaskCreate(task);
        this._fire('create', task?.id, 'taskCreated', () => window.ambientAudio.playSpawn());
      });

      socket.on('task:updated', (task) => {
        const diff = this.recordTaskUpdate(task);
        if (!diff) return;
        const id = task.id;
        if (diff.statusChanged) {
          if (diff.next === 'in_progress') {
            this._fire('start', id, 'taskStarted', () => window.ambientAudio.playStart());
            return;
          } else if (diff.next === 'done') {
            this._fire('done', id, 'taskDone', () => window.ambientAudio.playDone());
            return;
          } else if (diff.next === 'blocked') {
            this._fire('error', id, 'workerError', () => window.ambientAudio.playError());
            return;
          } else if (diff.next === 'inbox' && diff.prev && diff.prev !== 'inbox') {
            this._fire('revert', id, 'taskReverted', () => window.ambientAudio.playRevert());
            return;
          }
        }
        if (diff.assignedChanged || diff.statusChanged) {
          this._fire('move', id, 'taskMoved', () => window.ambientAudio.playMove());
        }
      });

      socket.on('task:deleted', (data) => {
        const id = data?.id;
        this.recordTaskDelete(id);
        this._fire('delete', id, 'taskDeleted', () => window.ambientAudio.playDespawn());
      });

      socket.on('layout:updated', (layout) => {
        const transitions = this.diffLayout(layout);
        for (const t of transitions) {
          if (t.prev === 'idle' && t.next === 'working') {
            this._fire('start', `slot:${t.slot}`, 'taskStarted', () => window.ambientAudio.playStart());
            return;   // one sound per layout event
          }
        }
      });

      socket.on('error', (data) => {
        this._fire('error', data?.code || 'server', 'serverError', () => window.ambientAudio.playError());
      });

      socket.on('toast', (data) => {
        const kind = data?.type || data?.level || 'info';
        if (kind === 'error') {
          this._fire('error', data?.message || 'toast', 'toast', () => window.ambientAudio.playError());
        } else {
          this._fire('toast', data?.message || 'toast', 'toast', () => window.ambientAudio.playToast());
        }
      });
    },
  };

  window.EventSounds = EventSounds;
  window.EVENT_SOUND_FLAGS_DEFAULTS = { ...DEFAULT_FLAGS };
  window.EVENT_SOUND_LABELS = [
    { key: 'taskCreated',  label: 'Ticket created',  preview: 'playSpawn' },
    { key: 'taskStarted',  label: 'Ticket started',  preview: 'playStart' },
    { key: 'taskDone',     label: 'Ticket done',     preview: 'playDone' },
    { key: 'taskDeleted',  label: 'Ticket deleted',  preview: 'playDespawn' },
    { key: 'taskReverted', label: 'Ticket reverted to inbox', preview: 'playRevert' },
    { key: 'taskMoved',    label: 'Ticket moved',    preview: 'playMove' },
    { key: 'workerError',  label: 'Worker error',  preview: 'playError' },
    { key: 'serverError',  label: 'Server error',  preview: 'playError' },
    { key: 'toast',        label: 'Toast notification', preview: 'playToast' },
  ];
})();
