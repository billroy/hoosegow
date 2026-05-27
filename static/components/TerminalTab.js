const TerminalTab = {
  props: {
    terminal: { type: Object, required: true },
    active: { type: Boolean, default: false },
    workspaceId: { type: String, default: null },
  },
  emits: ['terminal-input', 'terminal-resize', 'restart-terminal', 'ready'],
  data() {
    return {
      term: null,
      fitAddon: null,
      resizeObserver: null,
      resizeTimer: null,
      lastSize: null,
    };
  },
  mounted() {
    this.initTerminal();
  },
  beforeUnmount() {
    if (this.resizeObserver) this.resizeObserver.disconnect();
    window.removeEventListener('resize', this.scheduleFit);
    if (this.resizeTimer) clearTimeout(this.resizeTimer);
    if (this.term) this.term.dispose();
  },
  watch: {
    active(value) {
      if (value) {
        this.$nextTick(() => {
          this.fit();
          this.focus();
        });
      }
    },
  },
  methods: {
    initTerminal() {
      const TerminalCtor = window.Terminal || window.xterm?.Terminal;
      const FitCtor = window.FitAddon?.FitAddon || window.FitAddon || window.XTermFitAddon?.FitAddon;
      if (!TerminalCtor || !FitCtor) {
        this.$emit('ready', this.terminal.id);
        return;
      }
      this.term = new TerminalCtor({
        cursorBlink: true,
        convertEol: false,
        scrollback: 5000,
        fontFamily: "'SF Mono', 'Fira Code', 'Cascadia Code', Menlo, Consolas, monospace",
        fontSize: 13,
        lineHeight: 1.25,
        theme: {
          background: getComputedStyle(document.documentElement).getPropertyValue('--terminal-bg').trim() || '#0a0c10',
          foreground: getComputedStyle(document.documentElement).getPropertyValue('--terminal-fg').trim() || '#c8ccd4',
          cursor: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#58a6ff',
        },
      });
      this.fitAddon = new FitCtor();
      this.term.loadAddon(this.fitAddon);
      this.term.open(this.$refs.viewport);
      this.term.onData(data => {
        this.$emit('terminal-input', { terminalId: this.terminal.terminalId, data });
      });
      this.resizeObserver = new ResizeObserver(() => this.scheduleFit());
      this.resizeObserver.observe(this.$refs.viewport);
      window.addEventListener('resize', this.scheduleFit);
      this.$nextTick(() => {
        this.fit();
        if (this.active) this.focus();
        this.$emit('ready', this.terminal.id);
      });
    },
    scheduleFit() {
      if (this.resizeTimer) clearTimeout(this.resizeTimer);
      this.resizeTimer = setTimeout(() => this.fit(), 50);
    },
    fit() {
      if (!this.term || !this.fitAddon) return;
      try {
        this.fitAddon.fit();
        const size = { cols: this.term.cols, rows: this.term.rows };
        if (!this.lastSize || this.lastSize.cols !== size.cols || this.lastSize.rows !== size.rows) {
          this.lastSize = size;
          this.$emit('terminal-resize', {
            terminalId: this.terminal.terminalId,
            cols: size.cols,
            rows: size.rows,
          });
        }
      } catch (_err) {
        // xterm can throw while hidden or before layout is stable; the next
        // activation/resize pass will fit it again.
      }
    },
    focus() {
      if (this.term) this.term.focus();
    },
    write(data) {
      if (this.term) this.term.write(data || '');
    },
    clear() {
      if (this.term) this.term.clear();
    },
  },
  template: `
    <div class="terminal-tab">
      <div class="terminal-toolbar">
        <div class="terminal-status">
          <span class="terminal-status-dot" :class="'status-' + (terminal.status || 'starting')"></span>
          <span>{{ terminal.status || 'starting' }}</span>
          <span v-if="terminal.cwd" class="terminal-cwd">{{ terminal.cwd }}</span>
        </div>
        <button
          v-if="terminal.status === 'exited' || terminal.status === 'error'"
          class="btn btn-sm"
          @click="$emit('restart-terminal', terminal.id)"
        >
          Restart
        </button>
      </div>
      <div v-if="!term" class="terminal-unavailable">
        Terminal assets are unavailable.
      </div>
      <div ref="viewport" class="terminal-container"></div>
    </div>
  `,
};
