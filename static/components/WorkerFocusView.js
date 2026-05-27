const WorkerFocusView = {
  props: {
    worker: { type: Object, default: null },
    slotIndex: { type: Number, required: true },
    task: { type: Object, default: null },
    outputLines: { type: Array, default: () => [] },
  },
  emits: ['stop', 'close'],
  template: `
    <div class="focus-view">
      <div class="focus-header">
        <div class="focus-header-left">
          <span class="focus-task-title">{{ task?.title || (isService ? 'Service log' : 'No task') }}</span>
          <span v-if="task?.type" class="status-pill">{{ task.type }}</span>
          <span v-if="task?.priority" class="status-pill" :class="'priority-' + task.priority">{{ task.priority }}</span>
          <span class="focus-worker-name">{{ worker?.name || 'Worker ' + slotIndex }}</span>
        </div>
        <div class="focus-header-right">
          <button v-if="isWorking" class="btn btn-sm btn-danger" @click="$emit('stop')">Stop</button>
          <button class="btn btn-sm" @click="$emit('close')">&times;</button>
        </div>
      </div>
      <div v-if="showDescription" class="focus-description">
        <div class="focus-description-text" v-html="renderedDescription"></div>
      </div>
      <div class="focus-toggle-desc" @click="showDescription = !showDescription">
        {{ showDescription ? '▾ Hide description' : '▸ Show description' }}
      </div>
      <div class="focus-terminal" ref="terminal" @scroll="onScroll">
        <pre class="focus-output">{{ outputText }}<span v-if="isWorking" class="focus-cursor">|</span></pre>
      </div>
      <div class="focus-status-bar">
        <span class="focus-status-state">
          <span class="status-pill" :class="'status-' + workerState">
            {{ workerState.toUpperCase() }}
          </span>
          <span v-if="workerState === 'retrying' && retryCountdown">{{ retryCountdown }} until retry</span>
          <span v-if="isWorking && elapsed">{{ elapsed }} elapsed</span>
          <span v-if="!isWorking && !isService && workerState === 'idle'">Completed</span>
        </span>
        <span class="focus-status-lines">{{ outputLines.length.toLocaleString() }} lines</span>
      </div>
    </div>
  `,
  data() {
    return {
      showDescription: false,
      autoScroll: true,
      elapsed: '',
      _timer: null,
    };
  },
  computed: {
    isWorking() {
      return ['working', 'retrying', 'starting', 'running', 'healthy', 'unhealthy'].includes(this.workerState);
    },
    isService() {
      return this.worker?.type === 'service';
    },
    workerState() {
      return this.worker?.service_state?.state || this.worker?.state || 'idle';
    },
    outputText() {
      return (this.outputLines || []).join('\n');
    },
    retryCountdown() {
      if (this.workerState !== 'retrying') return '';
      const retryAt = this.worker?.retry_at ? new Date(this.worker.retry_at).getTime() : NaN;
      if (!Number.isFinite(retryAt)) return '';
      const remaining = Math.max(0, Math.ceil((retryAt - Date.now()) / 1000));
      return `${remaining}s`;
    },
    renderedDescription() {
      if (!this.task?.body) return '';
      if (window.markdownit) {
        const md = window.markdownit();
        return md.render(this.task.body);
      }
      return this.task.body;
    },
  },
  watch: {
    'outputLines.length'() {
      if (this.autoScroll) {
        this.$nextTick(() => this.scrollToBottom());
      }
    },
  },
  mounted() {
    this._timer = setInterval(() => this.updateElapsed(), 1000);
    this.updateElapsed();
    this.$nextTick(() => this.scrollToBottom());
  },
  beforeUnmount() {
    if (this._timer) clearInterval(this._timer);
  },
  methods: {
    scrollToBottom() {
      const el = this.$refs.terminal;
      if (el) el.scrollTop = el.scrollHeight;
    },
    onScroll() {
      const el = this.$refs.terminal;
      if (!el) return;
      const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
      this.autoScroll = atBottom;
    },
    updateElapsed() {
      if (!this.worker?.started_at) {
        this.elapsed = '';
        return;
      }
      const start = new Date(this.worker.started_at).getTime();
      const now = Date.now();
      const secs = Math.floor((now - start) / 1000);
      if (secs < 0) { this.elapsed = ''; return; }
      const m = Math.floor(secs / 60);
      const s = secs % 60;
      this.elapsed = m > 0 ? `${m}m ${s}s` : `${s}s`;
    },
  },
};
