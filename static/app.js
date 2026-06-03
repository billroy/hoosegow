const { createApp, computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } = Vue;

function formatDate(seconds) {
  if (!seconds) return '';
  return new Date(seconds * 1000).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function basename(path) {
  if (!path) return '';
  const clean = String(path).replace(/\/+$/, '');
  return clean.split('/').pop() || clean;
}

const TERMINAL_THEMES = {
  dark: {
    background: '#07090c',
    foreground: '#d7dde7',
    cursor: '#e9edf5',
    selectionBackground: '#3b5366',
    black: '#111820',
    red: '#e06c75',
    green: '#98c379',
    yellow: '#d19a66',
    blue: '#61afef',
    magenta: '#c678dd',
    cyan: '#56b6c2',
    white: '#d7dde7',
    brightBlack: '#5c6370',
    brightWhite: '#ffffff',
  },
  light: {
    background: '#f8fafc',
    foreground: '#17202a',
    cursor: '#1d4ed8',
    selectionBackground: '#bfd7ff',
    black: '#17202a',
    red: '#b4232f',
    green: '#237447',
    yellow: '#8a5a00',
    blue: '#1d4ed8',
    magenta: '#8b3bb3',
    cyan: '#0f6f7e',
    white: '#f8fafc',
    brightBlack: '#667085',
    brightWhite: '#ffffff',
  },
};

createApp({
  setup() {
    const socket = io({ transports: ['websocket', 'polling'] });
    const storedTheme = window.localStorage.getItem('hoosegow-theme');
    const preferredTheme = window.matchMedia?.('(prefers-color-scheme: light)')?.matches ? 'light' : 'dark';
    const theme = ref(storedTheme === 'light' || storedTheme === 'dark' ? storedTheme : preferredTheme);
    const connected = ref(false);
    const busy = ref(false);
    let clockCheckTimer = null;
    const selectedSlug = ref('');
    const selectedGroupKind = ref('local');
    const sandboxes = reactive([]);
    const baseStatus = reactive({
      prepared: false,
      state: 'checking',
      name: 'hoosegow-microsandbox-local',
      message: 'Checking base image...',
    });
    const baseLogs = reactive([]);
    const baseLogViewer = reactive({
      open: false,
      returncode: null,
      duration_seconds: null,
    });
    const sandboxLogs = reactive([]);
    const sandboxLogViewer = reactive({
      open: false,
      sandbox_id: '',
      title: '',
    });
    const form = reactive({
      name: 'sandbox',
      workspace_root: '',
      vcpus: 4,
      memory_mib: 4096,
    });
    const portForm = reactive({
      guest_port: 3000,
      host_port: '',
    });
    const picker = reactive({
      open: false,
      loading: false,
      path: '',
      parent: '',
      roots: [],
      entries: [],
      truncated: false,
      error: '',
    });
    const actionState = reactive({
      active: false,
      sandbox_id: '',
      label: '',
      detail: '',
    });
    const operationBySandbox = reactive({});
    const toast = reactive({ message: '', tone: 'info' });
    const authState = reactive({
      authenticated: false,
      csrf_token: '',
    });
    const activeTerminal = reactive({
      id: '',
      kind: '',
      sandbox_id: '',
      label: '',
      cwd: '',
      status: 'closed',
      exit_code: null,
    });
    const terminals = reactive([]);
    const terminal = ref(null);
    const terminalHosts = new Map();
    const terminalRenderers = new Map();
    const terminalFitTimer = ref(null);
    const terminalBellContext = ref(null);
    const terminalBellLastAt = ref(0);
    const terminalTextDecoders = new Map();
    const mainMenuOpen = ref(false);
    const sandboxActionMenuSlug = ref('');
    const createModalOpen = ref(false);
    const detailsModalOpen = ref(false);
    const portsModalOpen = ref(false);
    const storedSidebarWidth = Number(window.localStorage.getItem('hoosegow-sidebar-width') || 308);
    const sidebarWidth = ref(Math.max(220, Math.min(460, storedSidebarWidth || 308)));
    const sidebarCollapsed = ref(window.localStorage.getItem('hoosegow-sidebar-collapsed') === 'true');
    const sidebarResize = reactive({
      active: false,
      startX: 0,
      startWidth: 0,
    });

    const selected = computed(() => sandboxes.find((sandbox) => sandbox.slug === selectedSlug.value) || sandboxes[0] || null);
    const sortedSandboxes = computed(() => [...sandboxes].sort((a, b) => a.slug.localeCompare(b.slug)));
    const basePreparing = computed(() => baseStatus.state === 'preparing');
    const canStartSelected = computed(() => Boolean(selected.value && baseStatus.prepared && !busy.value));
    const canOpenLocalTerminal = computed(() => Boolean(connected.value && !busy.value));
    const canOpenTerminal = computed(() => Boolean(selected.value && selected.value.last_status === 'running' && !busy.value));
    const localTerminals = computed(() => terminals.filter((item) => item.kind === 'local'));
    const selectedGroupTerminals = computed(() => (
      selectedGroupKind.value === 'local' ? localTerminals.value : terminalsForSandbox(selected.value)
    ));
    const terminalVisible = computed(() => Boolean(
      activeTerminal.id
      && activeTerminal.status !== 'closed'
      && selectedGroupTerminals.value.some((item) => item.id === activeTerminal.id)
    ));
    const selectedGroupLabel = computed(() => (
      selectedGroupKind.value === 'local' ? 'Local' : (selected.value?.name || selected.value?.slug || 'Sandbox')
    ));

    function setToast(message, tone = 'info') {
      toast.message = message;
      toast.tone = tone;
      window.clearTimeout(setToast._timer);
      setToast._timer = window.setTimeout(() => {
        toast.message = '';
      }, 4200);
    }

    function closeMenus() {
      mainMenuOpen.value = false;
      sandboxActionMenuSlug.value = '';
    }

    function closeMenusOnOutsideClick(event) {
      if (event.target?.closest?.('.menu-wrap')) return;
      closeMenus();
    }

    function toggleSandboxActionMenu(slug) {
      mainMenuOpen.value = false;
      sandboxActionMenuSlug.value = sandboxActionMenuSlug.value === slug ? '' : slug;
      refreshIcons();
    }

    function toggleMainMenu() {
      mainMenuOpen.value = !mainMenuOpen.value;
      sandboxActionMenuSlug.value = '';
      refreshIcons();
    }

    function toggleSidebar() {
      sidebarCollapsed.value = !sidebarCollapsed.value;
      window.localStorage.setItem('hoosegow-sidebar-collapsed', String(sidebarCollapsed.value));
      closeMenus();
      scheduleTerminalFit();
      refreshIcons();
    }

    async function loadAuthState() {
      try {
        const response = await fetch('/login/csrf', {
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
        });
        if (!response.ok) return;
        const payload = await response.json();
        authState.authenticated = Boolean(payload.auth_enabled);
        authState.csrf_token = payload.csrf_token || '';
      } catch (_error) {
        authState.authenticated = false;
        authState.csrf_token = '';
      }
    }

    function openGithub() {
      closeMenus();
      window.open('https://github.com/billroy/hoosegow', '_blank', 'noopener,noreferrer');
    }

    function logout() {
      closeMenus();
      const form = document.createElement('form');
      form.method = 'POST';
      form.action = '/logout';
      form.style.display = 'none';
      const csrf = document.createElement('input');
      csrf.type = 'hidden';
      csrf.name = 'csrf_token';
      csrf.value = authState.csrf_token || '';
      form.appendChild(csrf);
      document.body.appendChild(form);
      form.submit();
    }

    function openCreateModal() {
      closeMenus();
      if (!form.name.trim()) form.name = nextSandboxName();
      createModalOpen.value = true;
      refreshIcons();
    }

    function openDetailsModal(sandbox = selected.value) {
      if (!sandbox?.slug) return;
      selectedSlug.value = sandbox.slug;
      closeMenus();
      detailsModalOpen.value = true;
      refreshIcons();
    }

    function openPortsModal(sandbox = selected.value) {
      if (!sandbox?.slug) return;
      selectedSlug.value = sandbox.slug;
      closeMenus();
      portsModalOpen.value = true;
      refreshIcons();
    }

    function beginSidebarResize(event) {
      if (sidebarCollapsed.value) return;
      sidebarResize.active = true;
      sidebarResize.startX = event.clientX;
      sidebarResize.startWidth = sidebarWidth.value;
      window.addEventListener('pointermove', updateSidebarResize);
      window.addEventListener('pointerup', endSidebarResize);
      event.currentTarget?.setPointerCapture?.(event.pointerId);
      event.preventDefault();
    }

    function updateSidebarResize(event) {
      if (!sidebarResize.active) return;
      const nextWidth = sidebarResize.startWidth + event.clientX - sidebarResize.startX;
      sidebarWidth.value = Math.max(220, Math.min(460, Math.round(nextWidth)));
      scheduleTerminalFit();
    }

    function endSidebarResize() {
      if (!sidebarResize.active) return;
      sidebarResize.active = false;
      window.removeEventListener('pointermove', updateSidebarResize);
      window.removeEventListener('pointerup', endSidebarResize);
      window.localStorage.setItem('hoosegow-sidebar-width', String(sidebarWidth.value));
      scheduleTerminalFit();
    }

    function setAction(label, sandboxId = '', detail = '') {
      actionState.active = true;
      actionState.sandbox_id = sandboxId || '';
      actionState.label = label || '';
      actionState.detail = detail || '';
      if (sandboxId) operationBySandbox[sandboxId] = label;
      refreshIcons();
    }

    function clearAction(sandboxId = '') {
      if (!sandboxId || actionState.sandbox_id === sandboxId) {
        actionState.active = false;
        actionState.sandbox_id = '';
        actionState.label = '';
        actionState.detail = '';
      }
      if (sandboxId) delete operationBySandbox[sandboxId];
      refreshIcons();
    }

    function refreshIcons() {
      nextTick(() => {
        if (window.lucide) window.lucide.createIcons();
      });
    }

    function applyTheme() {
      document.documentElement.dataset.theme = theme.value;
      window.localStorage.setItem('hoosegow-theme', theme.value);
      applyTerminalTheme();
    }

    function toggleTheme() {
      theme.value = theme.value === 'dark' ? 'light' : 'dark';
      applyTheme();
      refreshIcons();
    }

    function currentTerminalTheme() {
      return TERMINAL_THEMES[theme.value] || TERMINAL_THEMES.dark;
    }

    function applyTerminalTheme() {
      for (const renderer of terminalRenderers.values()) {
        renderer.terminal.options.theme = currentTerminalTheme();
        if (renderer.terminal.rows) renderer.terminal.refresh(0, renderer.terminal.rows - 1);
      }
    }

    function currentTerminalRecord() {
      return terminals.find((item) => item.id === activeTerminal.id) || null;
    }

    function syncActiveTerminal(record) {
      activeTerminal.id = record?.id || '';
      activeTerminal.kind = record?.kind || '';
      activeTerminal.sandbox_id = record?.sandbox_id || '';
      activeTerminal.label = record?.label || '';
      activeTerminal.cwd = record?.cwd || '';
      activeTerminal.status = record?.status || 'closed';
      activeTerminal.exit_code = record?.exit_code ?? null;
    }

    function decodeBase64Text(value, terminalId) {
      const binary = window.atob(value || '');
      if (!binary) return '';
      const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
      if (!terminalTextDecoders.has(terminalId)) {
        terminalTextDecoders.set(terminalId, new TextDecoder());
      }
      return terminalTextDecoders.get(terminalId).decode(bytes, { stream: true });
    }

    function decodeBase64Replay(value) {
      const binary = window.atob(value || '');
      if (!binary) return '';
      const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
      return new TextDecoder().decode(bytes);
    }

    function isTerminalQueryResponse(data) {
      if (!data || data.length > 256) return false;
      const csiResponse = /^\x1b\[(?:[?>]?[0-9;]*)[cRt]$/;
      const oscColorResponse = /^\x1b\](?:4;\d+|1[012]);(?:rgb:[0-9a-fA-F/]+|#[0-9a-fA-F]{6})(?:\x07|\x1b\\)$/;
      return csiResponse.test(data) || oscColorResponse.test(data);
    }

    function nextSandboxName() {
      const used = new Set(sandboxes.map((sandbox) => sandbox.slug));
      if (!used.has('sandbox')) return 'sandbox';
      for (let index = 2; index < 1000; index += 1) {
        const candidate = `sandbox-${index}`;
        if (!used.has(candidate)) return candidate;
      }
      return `sandbox-${Date.now().toString(36).slice(-5)}`;
    }

    function isGeneratedSandboxName(value) {
      return /^sandbox(?:-\d+)?$/.test(String(value || '').trim());
    }

    function upsertTerminalRecord(terminalInfo, transcript = '', options = {}) {
      let record = terminals.find((item) => item.id === terminalInfo.id);
      const terminalKind = terminalInfo.kind || (terminalInfo.sandbox_id ? 'sandbox' : 'local');
      if (!record) {
        record = {
          id: terminalInfo.id,
          kind: terminalKind,
          sandbox_id: terminalInfo.sandbox_id || '',
          label: terminalInfo.label || 'shell',
          number: terminalInfo.number ?? null,
          cwd: terminalInfo.cwd || (terminalKind === 'local' ? '' : '/workspace'),
          status: terminalInfo.status || 'running',
          exit_code: terminalInfo.exit_code ?? null,
          transcript,
        };
        terminals.push(record);
      } else {
        record.kind = terminalKind || record.kind || 'sandbox';
        record.sandbox_id = terminalInfo.sandbox_id || record.sandbox_id || '';
        record.label = terminalInfo.label || record.label || 'shell';
        record.number = terminalInfo.number ?? record.number ?? null;
        record.cwd = terminalInfo.cwd || record.cwd || '/workspace';
        record.status = terminalInfo.status || record.status || 'running';
        record.exit_code = terminalInfo.exit_code ?? record.exit_code ?? null;
        if (options.replaceTranscript) {
          record.transcript = transcript;
        } else if (!record.transcript && transcript) {
          record.transcript = transcript;
        }
      }
      return record;
    }

    function terminalsForSandbox(sandbox) {
      return terminals.filter((item) => item.kind === 'sandbox' && item.sandbox_id === sandbox?.slug);
    }

    function terminalLabel(term) {
      if (!term) return 'Term ?';
      if (term.label && term.label !== 'shell') return term.label;
      return `Term ${term.number || '?'}`;
    }

    function terminalStatusLabel(status) {
      if (status === 'exited') return 'exited';
      if (status === 'error') return 'error';
      return '';
    }

    function shellCountLabel(count) {
      if (!count) return 'No open shells';
      return `${count} open shell${count === 1 ? '' : 's'}`;
    }

    async function selectLocalGroup() {
      selectedGroupKind.value = 'local';
      const first = localTerminals.value[0] || null;
      if (first) {
        await focusTerminal(first.id);
      } else {
        deactivateTerminal();
        syncActiveTerminal(null);
      }
      refreshIcons();
    }

    async function selectSandboxGroup(sandbox) {
      if (!sandbox?.slug) return;
      selectedGroupKind.value = 'sandbox';
      selectedSlug.value = sandbox.slug;
      const first = terminalsForSandbox(sandbox)[0] || null;
      if (first) {
        await focusTerminal(first.id);
      } else {
        deactivateTerminal();
        syncActiveTerminal(null);
      }
      refreshIcons();
    }

    function activeTerminalHost() {
      return terminalHosts.get(activeTerminal.id) || null;
    }

    function setTerminalHost(terminalId, element) {
      if (element) {
        terminalHosts.set(terminalId, element);
      } else {
        terminalHosts.delete(terminalId);
      }
    }

    function terminalCellSize(anchor = activeTerminalHost()) {
      const renderedCell = terminal.value?._core?._renderService?.dimensions?.css?.cell;
      if (renderedCell?.width > 0 && renderedCell?.height > 0) {
        return { width: renderedCell.width, height: renderedCell.height };
      }
      const probeHost = anchor || document.body;
      if (!probeHost) return null;
      const probe = document.createElement('span');
      probe.textContent = 'W';
      probe.style.position = 'absolute';
      probe.style.visibility = 'hidden';
      probe.style.whiteSpace = 'pre';
      probe.style.fontFamily = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
      probe.style.fontSize = '12px';
      probe.style.lineHeight = '15px';
      probeHost.appendChild(probe);
      const rect = probe.getBoundingClientRect();
      probe.remove();
      if (!rect.width || !rect.height) return null;
      return { width: rect.width, height: rect.height };
    }

    function initialTerminalSize() {
      if (terminal.value?.cols && terminal.value?.rows) {
        return { cols: terminal.value.cols, rows: terminal.value.rows };
      }
      const surface = document.querySelector('.terminal-surface');
      const tabs = document.querySelector('.terminal-tabs');
      const cell = terminalCellSize(document.body);
      if (!surface || !cell) return { cols: 80, rows: 24 };
      const width = Math.max(0, surface.clientWidth - 8);
      const height = Math.max(0, surface.clientHeight - (tabs?.offsetHeight || 30) - 8);
      return {
        cols: Math.max(20, Math.floor(width / cell.width)),
        rows: Math.max(5, Math.floor(height / cell.height)),
      };
    }

    function fitTerminal() {
      terminalFitTimer.value = null;
      const host = activeTerminalHost();
      if (!terminal.value || !host) return;
      const cell = terminalCellSize(host);
      if (!cell) return;
      const styles = getComputedStyle(host);
      const width = host.clientWidth - parseFloat(styles.paddingLeft) - parseFloat(styles.paddingRight);
      const height = host.clientHeight - parseFloat(styles.paddingTop) - parseFloat(styles.paddingBottom);
      const cols = Math.max(20, Math.floor(width / cell.width));
      const rows = Math.max(5, Math.floor(height / cell.height));
      if (terminal.value.cols !== cols || terminal.value.rows !== rows) {
        terminal.value.resize(cols, rows);
      }
    }

    function scheduleTerminalFit() {
      if (terminalFitTimer.value) window.clearTimeout(terminalFitTimer.value);
      terminalFitTimer.value = window.setTimeout(() => fitTerminal(), 50);
    }

    function terminalAudioContext() {
      if (terminalBellContext.value) return terminalBellContext.value;
      const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextCtor) return null;
      terminalBellContext.value = new AudioContextCtor();
      return terminalBellContext.value;
    }

    function unlockTerminalBellAudio() {
      const context = terminalAudioContext();
      if (!context || context.state !== 'suspended') return;
      context.resume().catch(() => {});
    }

    function playTerminalBellTone(context) {
      try {
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        const start = context.currentTime;
        const stop = start + 0.09;
        oscillator.type = 'sine';
        oscillator.frequency.setValueAtTime(880, start);
        oscillator.frequency.exponentialRampToValueAtTime(660, stop);
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(0.08, start + 0.012);
        gain.gain.exponentialRampToValueAtTime(0.0001, stop);
        oscillator.connect(gain);
        gain.connect(context.destination);
        oscillator.start(start);
        oscillator.stop(stop);
        oscillator.onended = () => {
          oscillator.disconnect();
          gain.disconnect();
        };
      } catch (_error) {
        // Ignore browsers that expose Web Audio but refuse playback.
      }
    }

    function synthesizeTerminalBell(terminalId) {
      const renderer = terminalRenderers.get(terminalId);
      if (renderer?.replayMuted) return;
      const now = window.performance?.now?.() ?? Date.now();
      if (now - terminalBellLastAt.value < 70) return;
      terminalBellLastAt.value = now;
      const context = terminalAudioContext();
      if (!context) return;
      if (context.state === 'suspended') {
        context.resume().then(() => playTerminalBellTone(context)).catch(() => {});
        return;
      }
      playTerminalBellTone(context);
    }

    async function ensureTerminalRenderer(record, options = {}) {
      if (!record?.id) return null;
      await nextTick();
      const host = terminalHosts.get(record.id);
      if (!host) return null;
      const existing = terminalRenderers.get(record.id);
      if (existing) return existing;
      if (!window.Terminal) {
        setToast('Terminal renderer did not load.', 'error');
        return null;
      }
      const xterm = new window.Terminal({
        convertEol: false,
        cursorBlink: true,
        disableStdin: false,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        fontSize: 12,
        lineHeight: 1.25,
        scrollback: 8000,
        theme: currentTerminalTheme(),
      });
      const renderer = {
        terminal: xterm,
        dataDisposable: null,
        bellDisposable: null,
        resizeDisposable: null,
        resizeObserver: null,
        replayMuted: false,
        replayToken: 0,
      };
      terminalRenderers.set(record.id, renderer);
      xterm.open(host);
      renderer.dataDisposable = xterm.onData((data) => {
        unlockTerminalBellAudio();
        if (renderer.replayMuted) return;
        const currentRecord = terminals.find((item) => item.id === record.id);
        if (!currentRecord || currentRecord.status !== 'running') return;
        if (isTerminalQueryResponse(data)) {
          socket.emit('sandbox:terminal:input', {
            terminal_id: record.id,
            data,
            terminal_query_response: true,
          });
          return;
        }
        socket.emit('sandbox:terminal:input', { terminal_id: record.id, data });
      });
      renderer.bellDisposable = xterm.onBell(() => synthesizeTerminalBell(record.id));
      renderer.resizeDisposable = xterm.onResize(({ cols, rows }) => {
        const currentRecord = terminals.find((item) => item.id === record.id);
        if (!currentRecord || currentRecord.status !== 'running') return;
        socket.emit('sandbox:terminal:resize', { terminal_id: record.id, cols, rows });
      });
      if (typeof ResizeObserver !== 'undefined') {
        renderer.resizeObserver = new ResizeObserver(() => {
          if (activeTerminal.id === record.id) scheduleTerminalFit();
        });
        renderer.resizeObserver.observe(host);
      }
      if (options.replay && record.transcript) {
        await writeTerminalReplay(record.id, record.transcript);
      }
      return renderer;
    }

    async function ensureTerminal(options = {}) {
      const record = currentTerminalRecord();
      if (!record) return;
      const renderer = await ensureTerminalRenderer(record, { replay: options.replay !== false });
      if (!renderer) return;
      terminal.value = renderer.terminal;
      fitTerminal();
      renderer.terminal.focus();
    }

    function writeTerminalReplay(terminalId, transcript) {
      const renderer = terminalRenderers.get(terminalId);
      if (!renderer?.terminal || !transcript) return Promise.resolve();
      const replayTerminal = renderer.terminal;
      const replayToken = renderer.replayToken + 1;
      renderer.replayToken = replayToken;
      renderer.replayMuted = true;
      return new Promise((resolve, reject) => {
        try {
          replayTerminal.write(transcript, () => {
            if (renderer.replayToken === replayToken) renderer.replayMuted = false;
            resolve();
          });
        } catch (error) {
          if (renderer.replayToken === replayToken) renderer.replayMuted = false;
          reject(error);
        }
      });
    }

    function deactivateTerminal() {
      terminal.value = null;
      if (terminalFitTimer.value) window.clearTimeout(terminalFitTimer.value);
      terminalFitTimer.value = null;
    }

    function disposeTerminal(terminalId) {
      const id = terminalId || activeTerminal.id;
      const renderer = terminalRenderers.get(id);
      if (!renderer) return;
      renderer.replayToken += 1;
      renderer.replayMuted = false;
      renderer.resizeObserver?.disconnect?.();
      renderer.dataDisposable?.dispose?.();
      renderer.bellDisposable?.dispose?.();
      renderer.resizeDisposable?.dispose?.();
      renderer.terminal?.dispose?.();
      terminalRenderers.delete(id);
      if (terminal.value === renderer.terminal) deactivateTerminal();
    }

    function disposeAllTerminals() {
      for (const terminalId of Array.from(terminalRenderers.keys())) {
        disposeTerminal(terminalId);
      }
      terminalHosts.clear();
    }

    function replaceSandboxes(nextSandboxes) {
      sandboxes.splice(0, sandboxes.length, ...(Array.isArray(nextSandboxes) ? nextSandboxes : []));
      const generatedNameTaken = isGeneratedSandboxName(form.name)
        && sandboxes.some((sandbox) => sandbox.slug === form.name.trim());
      if (!form.name.trim() || generatedNameTaken) {
        form.name = nextSandboxName();
      }
      if (!selectedSlug.value && sandboxes.length) selectedSlug.value = sandboxes[0].slug;
      if (selectedSlug.value && !sandboxes.some((sandbox) => sandbox.slug === selectedSlug.value)) {
        selectedSlug.value = sandboxes[0]?.slug || '';
        if (!selectedSlug.value || selectedGroupKind.value === 'sandbox') selectedGroupKind.value = 'local';
      }
      refreshIcons();
    }

    function call(event, payload = {}) {
      return new Promise((resolve, reject) => {
        socket.timeout(12000).emit(event, payload, (err, response) => {
          if (err) {
            reject(new Error('Socket request timed out'));
            return;
          }
          if (!response || response.ok === false) {
            reject(new Error(response?.error || 'Request failed'));
            return;
          }
          resolve(response);
        });
      });
    }

    async function loadSandboxes() {
      const response = await call('sandbox:list');
      replaceSandboxes(response.sandboxes);
    }

    async function joinTerminal(terminalInfo, options = {}) {
      if (!terminalInfo?.id) return null;
      const response = await call('sandbox:terminal:join', { terminal_id: terminalInfo.id });
      const replayText = decodeBase64Replay(response.replay?.data || '');
      const record = upsertTerminalRecord(response.terminal || terminalInfo, replayText, { replaceTranscript: true });
      if (options.focus) await focusTerminal(record.id);
      return record;
    }

    async function loadTerminalSessions() {
      if (!connected.value) return;
      const response = await call('terminal:list');
      const liveIds = new Set((response.terminals || []).map((item) => item.id));
      for (let index = terminals.length - 1; index >= 0; index -= 1) {
        if (!liveIds.has(terminals[index].id)) {
          disposeTerminal(terminals[index].id);
          terminalTextDecoders.delete(terminals[index].id);
          terminals.splice(index, 1);
        }
      }
      let focused = Boolean(activeTerminal.id && liveIds.has(activeTerminal.id));
      for (const terminalInfo of response.terminals || []) {
        await joinTerminal(terminalInfo, { focus: !focused });
        focused = true;
      }
      if (activeTerminal.id && !terminals.some((item) => item.id === activeTerminal.id)) {
        syncActiveTerminal(null);
        deactivateTerminal();
      }
    }

    async function openLocalTerminal(options = {}) {
      if (!connected.value) {
        setToast('Socket is not connected.', 'error');
        return;
      }
      if (options.manageBusy !== false) busy.value = true;
      try {
        await nextTick();
        const size = initialTerminalSize();
        const response = await call('terminal:local:open', {
          cols: size.cols,
          rows: size.rows,
        });
        const record = upsertTerminalRecord(response.terminal, '');
        await focusTerminal(record.id);
        await nextTick();
        await ensureTerminal();
        if (!options.silent) setToast(`${terminalLabel(record)} opened.`, 'success');
      } catch (error) {
        setToast(error.message, 'error');
      } finally {
        if (options.manageBusy !== false) busy.value = false;
        refreshIcons();
      }
    }

    async function loadBaseStatus() {
      const response = await call('base:status');
      Object.assign(baseStatus, response.base || {});
      Object.assign(baseLogViewer, response.base?.prepare || {});
      refreshIcons();
    }

    async function loadBaseLogs() {
      const response = await call('base:logs');
      const prepare = response.prepare || {};
      baseLogs.splice(0, baseLogs.length, ...(prepare.logs || []));
      Object.assign(baseLogViewer, {
        returncode: prepare.returncode ?? null,
        duration_seconds: prepare.duration_seconds ?? null,
      });
      refreshIcons();
    }

    async function openBaseLogs() {
      closeMenus();
      baseLogViewer.open = true;
      await loadBaseLogs();
    }

    async function loadSandboxLogs(sandbox = selected.value) {
      if (!sandbox?.slug) return;
      const response = await call('sandbox:logs', { id: sandbox.slug });
      sandboxLogs.splice(0, sandboxLogs.length, ...(response.logs || []));
      Object.assign(sandboxLogViewer, {
        sandbox_id: response.sandbox_id || sandbox.slug,
        title: sandbox.name || sandbox.slug,
      });
      refreshIcons();
    }

    async function openSandboxLogs(sandbox = selected.value) {
      if (!sandbox?.slug) return;
      closeMenus();
      sandboxLogViewer.open = true;
      await loadSandboxLogs(sandbox);
    }

    async function loadWorkspaceDefaults() {
      if (form.workspace_root.trim()) return;
      const response = await call('workspace:browse');
      const browse = response.browse || {};
      form.workspace_root = browse.path || browse.roots?.[0]?.path || '';
    }

    async function createSandbox(options = {}) {
      if (!form.name.trim() || !form.workspace_root.trim()) {
        setToast('Name and workspace root are required.', 'error');
        return;
      }
      if (!baseStatus.prepared) {
        setToast(baseStatus.message || 'Base image is setting up automatically.', 'info');
        return;
      }
      busy.value = true;
      let workflowSlug = '';
      try {
        setAction('Creating sandbox...', '', form.workspace_root.trim());
        const workspaceRoot = form.workspace_root.trim();
        const response = await call('sandbox:create', {
          name: form.name.trim(),
          workspace_root: workspaceRoot,
          vcpus: Number(form.vcpus) || 4,
          memory_mib: Number(form.memory_mib) || 4096,
          confirmed_sensitive_workspace: Boolean(options.confirmedSensitiveWorkspace),
        });
        selectedSlug.value = response.sandbox.slug;
        workflowSlug = response.sandbox.slug;
        form.name = '';
        form.workspace_root = '';
        setAction(`Starting ${response.sandbox.slug}...`, response.sandbox.slug, 'Create starts the sandbox automatically.');
        setToast(`Starting ${response.sandbox.slug}...`, 'info');
        const started = await call('sandbox:start', { id: response.sandbox.slug });
        await loadSandboxes();
        setAction(`Opening terminal for ${response.sandbox.slug}...`, response.sandbox.slug, 'Create opens the first terminal automatically.');
        await openTerminal(started.sandbox, { manageBusy: false, manageAction: false });
        createModalOpen.value = false;
        picker.open = false;
        setToast(`Created ${response.sandbox.slug} and opened a terminal.`, 'success');
      } catch (error) {
        if (!options.confirmedSensitiveWorkspace && error.message.includes('Workspace confirmation required')) {
          const workspaceRoot = form.workspace_root.trim();
          const typed = window.prompt(`${error.message}\n\nType the workspace root to continue:\n${workspaceRoot}`);
          if (typed === workspaceRoot) {
            await createSandbox({ confirmedSensitiveWorkspace: true });
          } else if (typed !== null) {
            setToast('Workspace confirmation did not match.', 'error');
          }
          return;
        }
        setToast(error.message, 'error');
      } finally {
        busy.value = false;
        clearAction(workflowSlug);
      }
    }

    async function browseWorkspace(path = '') {
      picker.open = true;
      picker.loading = true;
      picker.error = '';
      try {
        const response = await call('workspace:browse', { path: path || undefined });
        const browse = response.browse || {};
        picker.path = browse.path || '';
        picker.parent = browse.parent || '';
        picker.roots = browse.roots || [];
        picker.entries = browse.entries || [];
        picker.truncated = Boolean(browse.truncated);
      } catch (error) {
        picker.error = error.message;
        setToast(error.message, 'error');
      } finally {
        picker.loading = false;
        refreshIcons();
      }
    }

    function selectWorkspacePath(path) {
      form.workspace_root = path || picker.path;
      if (!form.name.trim()) form.name = nextSandboxName();
      picker.open = false;
      refreshIcons();
    }

    async function runSandboxAction(event, sandbox, successMessage) {
      if (!sandbox) return;
      if (event === 'sandbox:start' && !baseStatus.prepared) {
        setToast(baseStatus.message || 'Base image is setting up automatically.', 'info');
        return;
      }
      if (event === 'sandbox:stop' || event === 'sandbox:destroy' || event === 'sandbox:refresh-runtime') {
        await closeSandboxTerminals(sandbox.slug, { silent: true });
      }
      busy.value = true;
      try {
        if (event === 'sandbox:start') setAction(`Starting ${sandbox.slug}...`, sandbox.slug, 'A terminal will open when it is ready.');
        if (event === 'sandbox:stop') setAction(`Stopping ${sandbox.slug}...`, sandbox.slug);
        if (event === 'sandbox:clock:sync') setAction(`Syncing ${sandbox.slug} clock...`, sandbox.slug);
        if (event === 'sandbox:refresh-runtime') {
          setAction(
            `Updating ${sandbox.slug} agent CLIs...`,
            sandbox.slug,
            'Checks CLI versions first and rebuilds the shared base only when needed.'
          );
        }
        const response = await call(event, { id: sandbox.slug });
        await loadSandboxes();
        if (event === 'sandbox:start' && response?.sandbox?.last_status === 'running') {
          setAction(`Opening terminal for ${sandbox.slug}...`, sandbox.slug);
          await openTerminal(response.sandbox, { manageBusy: false, manageAction: false });
          setToast('Started and opened a terminal.', 'success');
          return;
        }
        if (event === 'sandbox:refresh-runtime' && response?.sandbox?.last_status === 'running') {
          setAction(`Opening terminal for ${sandbox.slug}...`, sandbox.slug);
          await openTerminal(response.sandbox, { manageBusy: false, manageAction: false });
          setToast(response.message || 'Agent CLIs updated and terminal reopened.', 'success');
          return;
        }
        if (event === 'sandbox:refresh-runtime') {
          setToast(response.message || successMessage, 'success');
          return;
        }
        if (event === 'sandbox:clock:sync') {
          setToast(successMessage || 'Clock synced.', 'success');
          return;
        }
        setToast(successMessage, 'success');
      } catch (error) {
        setToast(error.message, 'error');
      } finally {
        busy.value = false;
        clearAction(sandbox.slug);
      }
    }

    function clockStatusText(clock) {
      if (!clock?.status || clock.status === 'unknown') return 'Not checked';
      if (clock.status === 'synced') return 'Synced';
      if (clock.status === 'ok') return 'OK';
      if (clock.status === 'error') return 'Check failed';
      const drift = Number(clock.drift_seconds || 0);
      const direction = drift < 0 ? 'behind' : 'ahead';
      return `${Math.abs(drift).toFixed(0)}s ${direction}`;
    }

    async function checkRunningClocks() {
      if (!connected.value) return;
      try {
        await call('sandbox:clock:check', {});
      } catch (error) {
        setToast(error.message, 'error');
      }
    }

    async function openTerminal(sandbox, options = {}) {
      if (!sandbox || sandbox.last_status !== 'running') {
        setToast('Start the sandbox before opening a terminal.', 'error');
        return;
      }
      if (options.manageBusy !== false) busy.value = true;
      try {
        if (options.manageAction !== false) setAction(`Opening terminal for ${sandbox.slug}...`, sandbox.slug);
        await nextTick();
        const size = initialTerminalSize();
        const response = await call('sandbox:terminal:open', {
          sandbox_id: sandbox.slug,
          cols: size.cols,
          rows: size.rows,
        });
        const record = upsertTerminalRecord(response.terminal, '');
        await focusTerminal(record.id);
        await nextTick();
        await ensureTerminal();
        if (!options.silent) {
          setToast(`${terminalLabel(record)} opened for ${sandbox.slug}.`, 'success');
        }
      } catch (error) {
        setToast(error.message, 'error');
      } finally {
        if (options.manageBusy !== false) busy.value = false;
        if (options.manageAction !== false) clearAction(sandbox.slug);
        refreshIcons();
      }
    }

    async function focusTerminal(terminalId) {
      const record = terminals.find((item) => item.id === terminalId);
      if (!record) return;
      if (record.kind === 'local') {
        selectedGroupKind.value = 'local';
      } else {
        selectedGroupKind.value = 'sandbox';
        selectedSlug.value = record.sandbox_id || selectedSlug.value;
      }
      if (activeTerminal.id === terminalId) {
        await ensureTerminal({ replay: false });
        if (terminal.value) terminal.value.focus();
        return;
      }
      syncActiveTerminal(record);
      await nextTick();
      await ensureTerminal();
      refreshIcons();
    }

    async function foregroundProcessForTerminal(terminalId) {
      try {
        const response = await call('sandbox:terminal:status', { terminal_id: terminalId });
        const foreground = response.status?.foreground || {};
        return foreground.busy ? foreground : null;
      } catch (_error) {
        return null;
      }
    }

    async function closeTerminal(options = {}) {
      const terminalId = options.terminalId || activeTerminal.id;
      if (terminalId && options.remote !== false && !options.force) {
        const foreground = await foregroundProcessForTerminal(terminalId);
        if (foreground) {
          const label = foreground.command ? `\n\nForeground process: ${foreground.command}` : '';
          const confirmed = window.confirm(
            `Close this terminal while a foreground process is running?${label}\n\n`
            + 'The process will be sent SIGHUP and may be killed if it does not exit.'
          );
          if (!confirmed) return;
        }
      }
      if (terminalId && options.remote !== false) {
        socket.emit('sandbox:terminal:close', { terminal_id: terminalId });
      }
      const index = terminals.findIndex((item) => item.id === terminalId);
      const wasActive = terminalId === activeTerminal.id;
      disposeTerminal(terminalId);
      if (index >= 0) terminals.splice(index, 1);
      terminalTextDecoders.delete(terminalId);
      if (wasActive) {
        const nextRecord = selectedGroupTerminals.value[0] || null;
        syncActiveTerminal(nextRecord);
        if (nextRecord) {
          await nextTick();
          await ensureTerminal();
        } else {
          deactivateTerminal();
        }
      }
      if (!options.silent) setToast('Terminal closed.', 'info');
      await nextTick();
      refreshIcons();
    }

    async function closeSandboxTerminals(sandboxId, options = {}) {
      const ids = terminals
        .filter((item) => item.sandbox_id === sandboxId)
        .map((item) => item.id);
      for (const terminalId of ids) {
        await closeTerminal({ ...options, terminalId });
      }
    }

    function portUrl(mapping) {
      if (!mapping?.host_port) return '';
      return `http://127.0.0.1:${mapping.host_port}`;
    }

    function portStatusText(mapping) {
      if (!mapping) return '';
      if (mapping.status === 'pending_restart') return 'activates on restart';
      if (mapping.status === 'remove_on_restart') return 'active until restart';
      if (mapping.status === 'conflict') return 'conflict';
      return mapping.status || 'active';
    }

    function portIsLive(mapping, sandbox = selected.value) {
      return Boolean(
        mapping
        && sandbox?.last_status === 'running'
        && (mapping.status === 'active' || mapping.status === 'remove_on_restart')
      );
    }

    async function publishPort(sandbox) {
      if (!sandbox) return;
      const guestPort = Number(portForm.guest_port);
      const hostPort = String(portForm.host_port || '').trim();
      if (!Number.isInteger(guestPort) || guestPort < 1 || guestPort > 65535) {
        setToast('Guest port must be between 1 and 65535.', 'error');
        return;
      }
      busy.value = true;
      try {
        const response = await call('port:publish', {
          sandbox_id: sandbox.slug,
          guest_port: guestPort,
          host_port: hostPort || undefined,
        });
        portForm.host_port = '';
        await loadSandboxes();
        const restartNote = response.port?.status === 'pending_restart' ? ' Restart the sandbox to activate it.' : '';
        setToast(`Published :${guestPort}.${restartNote}`, 'success');
      } catch (error) {
        setToast(error.message, 'error');
      } finally {
        busy.value = false;
      }
    }

    async function unpublishPort(sandbox, mapping) {
      if (!sandbox || !mapping) return;
      busy.value = true;
      try {
        const response = await call('port:unpublish', {
          sandbox_id: sandbox.slug,
          host_port: mapping.host_port,
        });
        await loadSandboxes();
        const restartNote = response.port?.status === 'remove_on_restart' ? ' Restart the sandbox to remove the live mapping.' : '';
        setToast(`Unpublished :${mapping.guest_port}.${restartNote}`, 'success');
      } catch (error) {
        setToast(error.message, 'error');
      } finally {
        busy.value = false;
      }
    }

    async function reassignPort(sandbox, mapping) {
      if (!sandbox || !mapping) return;
      busy.value = true;
      try {
        const response = await call('port:reassign', {
          sandbox_id: sandbox.slug,
          host_port: mapping.host_port,
        });
        await loadSandboxes();
        const restartNote = response.port?.status === 'pending_restart' ? ' Restart the sandbox to activate it.' : '';
        setToast(`Reassigned :${mapping.guest_port} to :${response.port.host_port}.${restartNote}`, 'success');
      } catch (error) {
        setToast(error.message, 'error');
      } finally {
        busy.value = false;
      }
    }

    async function copyPortUrl(mapping) {
      const url = portUrl(mapping);
      if (!url) return;
      try {
        await navigator.clipboard.writeText(url);
        setToast('Port URL copied.', 'success');
      } catch (_error) {
        setToast(url, 'info');
      }
    }

    function openPort(mapping) {
      const url = portUrl(mapping);
      if (!url) return;
      window.open(url, '_blank', 'noopener');
    }

    async function destroySandbox(sandbox) {
      if (!sandbox) return;
      const confirmed = window.confirm(
        `Destroy ${sandbox.slug}?\n\n`
        + `Deletes sandbox home:\n${sandbox.home_path}\n\n`
        + `Does not delete workspace root:\n${sandbox.canonical_workspace_path}`
      );
      if (!confirmed) return;
      await closeSandboxTerminals(sandbox.slug, { silent: true });
      busy.value = true;
      try {
        setAction(`Destroying ${sandbox.slug}...`, sandbox.slug);
        await call('sandbox:destroy', { id: sandbox.slug, purge: true });
        await loadSandboxes();
        setToast(`Destroyed ${sandbox.slug}.`, 'success');
      } catch (error) {
        setToast(error.message, 'error');
      } finally {
        busy.value = false;
        clearAction(sandbox.slug);
      }
    }

    async function requestBasePrepare(rebuild = false) {
      try {
        closeMenus();
        const response = await call('base:prepare', { rebuild: Boolean(rebuild) });
        if (response.started) {
          baseLogs.splice(0, baseLogs.length);
          baseLogViewer.open = true;
          baseLogViewer.returncode = null;
          baseLogViewer.duration_seconds = null;
          setToast(rebuild ? 'Base rebuild started.' : 'Base preparation started.', 'success');
          Object.assign(baseStatus, {
            prepared: false,
            state: 'preparing',
            message: rebuild ? 'Rebuilding base image...' : 'Preparing base image...',
          });
        } else {
          setToast(response.message || 'Base preparation is already running.', 'info');
        }
      } catch (error) {
        setToast(error.message, 'error');
      }
    }

    socket.on('connect', async () => {
      connected.value = true;
      try {
        await Promise.all([loadBaseStatus(), loadBaseLogs(), loadSandboxes(), loadWorkspaceDefaults()]);
        checkRunningClocks().catch((error) => setToast(error.message, 'error'));
        await loadTerminalSessions();
      } catch (error) {
        setToast(error.message, 'error');
      }
    });
    socket.on('disconnect', () => {
      connected.value = false;
    });
    socket.on('sandboxes:updated', (payload) => {
      replaceSandboxes(payload?.sandboxes || []);
    });
    socket.on('sandbox:error', (payload) => {
      setToast(payload?.error || 'Sandbox error', 'error');
    });
    socket.on('sandbox:status', () => {
      loadSandboxes().catch((error) => setToast(error.message, 'error'));
    });
    socket.on('sandbox:destroyed', (payload) => {
      if (payload?.id === selectedSlug.value) {
        selectedSlug.value = '';
        selectedGroupKind.value = 'local';
        for (const record of terminals.filter((item) => item.sandbox_id === payload.id)) {
          disposeTerminal(record.id);
        }
        syncActiveTerminal(localTerminals.value[0] || null);
      }
      loadSandboxes().catch((error) => setToast(error.message, 'error'));
    });
    socket.on('sandbox:terminal:output', (payload) => {
      const record = terminals.find((item) => item.id === payload?.terminal_id);
      if (!record) return;
      try {
        const text = decodeBase64Text(payload.data, payload.terminal_id);
        record.transcript = `${record.transcript || ''}${text}`;
        if (record.transcript.length > 200000) record.transcript = record.transcript.slice(-200000);
        const renderer = terminalRenderers.get(payload.terminal_id);
        if (renderer?.terminal && text) renderer.terminal.write(text);
      } catch (error) {
        setToast(error.message, 'error');
      }
    });
    socket.on('sandbox:terminal:exit', (payload) => {
      const record = terminals.find((item) => item.id === payload?.terminal_id);
      if (!record) return;
      record.status = 'exited';
      record.exit_code = payload.exit_code;
      const text = `\r\n[terminal exited: ${payload.exit_code ?? 0}]\r\n`;
      record.transcript = `${record.transcript || ''}${text}`;
      const renderer = terminalRenderers.get(payload.terminal_id);
      if (renderer?.terminal) renderer.terminal.write(text);
      if (payload.terminal_id === activeTerminal.id) {
        activeTerminal.status = 'exited';
        activeTerminal.exit_code = payload.exit_code;
      }
    });
    socket.on('sandbox:terminal:error', (payload) => {
      const record = terminals.find((item) => item.id === payload?.terminal_id);
      if (record) record.status = 'error';
      if (payload?.terminal_id === activeTerminal.id) activeTerminal.status = 'error';
      if (payload?.terminal_id && payload.terminal_id !== activeTerminal.id) return;
      const message = payload?.error || 'Terminal error';
      const renderer = terminalRenderers.get(payload?.terminal_id);
      if (renderer?.terminal) {
        renderer.terminal.write(`\r\n[terminal error: ${message}]\r\n`);
      }
      setToast(message, 'error');
    });
    socket.on('sandbox:terminal:closed', (payload) => {
      closeTerminal({ silent: true, remote: false, terminalId: payload?.terminal_id });
    });
    socket.on('ports:updated', (payload) => {
      const sandbox = sandboxes.find((item) => item.slug === payload?.sandbox_id);
      if (sandbox) sandbox.published_ports = payload.ports || [];
      refreshIcons();
    });
    socket.on('base:status', (payload) => {
      Object.assign(baseStatus, payload?.base || {});
      Object.assign(baseLogViewer, payload?.base?.prepare || {});
      refreshIcons();
    });
    socket.on('base:log', (payload) => {
      const line = payload?.line || '';
      if (!line) return;
      baseLogs.push(line);
      if (baseLogs.length > 300) baseLogs.splice(0, baseLogs.length - 300);
    });

    watch(selectedSlug, () => {
      if (!connected.value) return;
      loadTerminalSessions().catch((error) => setToast(error.message, 'error'));
    });

    onMounted(() => {
      document.addEventListener('click', closeMenusOnOutsideClick);
      window.addEventListener('resize', scheduleTerminalFit);
      clockCheckTimer = window.setInterval(checkRunningClocks, 5 * 60 * 1000);
      loadAuthState();
    });

    onBeforeUnmount(() => {
      document.removeEventListener('click', closeMenusOnOutsideClick);
      window.removeEventListener('resize', scheduleTerminalFit);
      if (clockCheckTimer) window.clearInterval(clockCheckTimer);
      disposeAllTerminals();
    });

    applyTheme();
    refreshIcons();

    return {
      activeTerminal,
      actionState,
      authState,
      basename,
      baseLogViewer,
      baseStatus,
      basePreparing,
      baseLogs,
      beginSidebarResize,
      sandboxLogViewer,
      sandboxLogs,
      busy,
      canOpenTerminal,
      canOpenLocalTerminal,
      canStartSelected,
      closeTerminal,
      closeMenus,
      closeMenusOnOutsideClick,
      clockStatusText,
      connected,
      createModalOpen,
      createSandbox,
      detailsModalOpen,
      destroySandbox,
      form,
      formatDate,
      loadBaseLogs,
      loadBaseStatus,
      loadSandboxes,
      loadSandboxLogs,
      loadTerminalSessions,
      openTerminal,
      openLocalTerminal,
      openCreateModal,
      openDetailsModal,
      openBaseLogs,
      openGithub,
      openPortsModal,
      openSandboxLogs,
      operationBySandbox,
      copyPortUrl,
      openPort,
      browseWorkspace,
      portForm,
      portIsLive,
      portStatusText,
      portUrl,
      publishPort,
      reassignPort,
      focusTerminal,
      requestBasePrepare,
      runSandboxAction,
      sandboxActionMenuSlug,
      sidebarCollapsed,
      selected,
      selectedGroupKind,
      selectedGroupLabel,
      selectedGroupTerminals,
      selectedSlug,
      selectLocalGroup,
      selectSandboxGroup,
      setTerminalHost,
      selectWorkspacePath,
      sidebarWidth,
      sortedSandboxes,
      terminalStatusLabel,
      terminalLabel,
      shellCountLabel,
      terminals,
      terminalsForSandbox,
      localTerminals,
      terminalVisible,
      theme,
      toggleTheme,
      toggleSidebar,
      toggleMainMenu,
      toggleSandboxActionMenu,
      toast,
      logout,
      unpublishPort,
      mainMenuOpen,
      portsModalOpen,
      picker,
    };
  },
  template: `
    <div class="hoosegow-shell" :class="{ 'sidebar-collapsed': sidebarCollapsed }" :style="{ '--sidebar-width': sidebarWidth + 'px' }">
      <header class="topbar">
        <div class="brand">
          <div class="menu-wrap">
            <button class="icon-button header-menu-button" type="button" title="Main menu" @click.stop="toggleMainMenu">
              <i data-lucide="menu"></i>
            </button>
            <div v-if="mainMenuOpen" class="menu-panel main-menu">
              <button class="menu-item" v-if="baseStatus.state === 'error'" type="button" :disabled="basePreparing" @click="requestBasePrepare(false)">
                <i class="menu-item-icon" data-lucide="hammer"></i><span class="menu-item-label">Retry setup</span>
              </button>
              <button class="menu-item" type="button" :disabled="basePreparing" @click="requestBasePrepare(true)">
                <i class="menu-item-icon" data-lucide="refresh-ccw"></i><span class="menu-item-label">Rebuild base image</span>
              </button>
              <button class="menu-item" type="button" @click="openBaseLogs">
                <i class="menu-item-icon" data-lucide="scroll-text"></i><span class="menu-item-label">Base logs</span>
              </button>
              <div class="menu-divider" aria-hidden="true"></div>
              <button class="menu-item" type="button" @click="openGithub">
                <i class="menu-item-icon" data-lucide="external-link"></i><span class="menu-item-label">Hoosegow on GitHub</span>
              </button>
              <button class="menu-item" v-if="authState.authenticated" type="button" @click="logout">
                <i class="menu-item-icon" data-lucide="log-out"></i><span class="menu-item-label">Logout</span>
              </button>
            </div>
          </div>
          <div>
            <h1>Hoosegow</h1>
          </div>
        </div>
        <div class="topbar-actions">
          <button class="icon-button header-icon-button" type="button" title="Toggle theme" @click="toggleTheme">
            <i :data-lucide="theme === 'dark' ? 'sun' : 'moon'"></i>
          </button>
          <span class="connection-dot" :class="{ online: connected }" :title="connected ? 'Socket connected' : 'Socket offline'"></span>
        </div>
      </header>

      <aside class="sidebar">
        <div class="sidebar-heading">
          <h2>Terminal Groups</h2>
          <span class="sidebar-heading-actions">
            <button
              class="icon-button tiny pane-toggle-button"
              type="button"
              title="Hide shells"
              aria-pressed="false"
              @click="toggleSidebar"
            >
              <i data-lucide="panel-left-close"></i>
            </button>
          </span>
        </div>
        <div class="shell-group">
          <div class="shell-group-header">
            <span>Local</span>
            <button class="row-add-button" type="button" title="New local shell" :disabled="!canOpenLocalTerminal" @click.stop="openLocalTerminal">
              <i data-lucide="plus"></i>
            </button>
          </div>
          <div
            class="shell-group-row"
            :class="{ active: selectedGroupKind === 'local' }"
            role="button"
            tabindex="0"
            @click="selectLocalGroup"
            @keydown.enter.prevent="selectLocalGroup"
            @keydown.space.prevent="selectLocalGroup"
          >
            <span class="status-dot" :data-status="localTerminals.length ? 'running' : 'closed'"></span>
            <span class="shell-group-main">
              <strong>Local</strong>
              <small>{{ shellCountLabel(localTerminals.length) }}</small>
            </span>
          </div>
        </div>
        <div class="shell-group">
          <div class="shell-group-header">
            <span>Sandboxes</span>
            <button class="row-add-button" type="button" title="Create sandbox" @click="openCreateModal">
              <i data-lucide="plus"></i>
            </button>
          </div>
          <div v-for="sandbox in sortedSandboxes" :key="sandbox.slug" class="sandbox-shell-group">
            <div class="sandbox-row" :class="{ active: selectedGroupKind === 'sandbox' && selectedSlug === sandbox.slug }">
              <button type="button" class="sandbox-select" @click="selectSandboxGroup(sandbox)">
                <span class="status-dot" :data-status="sandbox.last_status"></span>
                <span class="sandbox-main">
                  <strong>{{ sandbox.name || sandbox.slug }}</strong>
                  <small>{{ basename(sandbox.canonical_workspace_path) }} / {{ shellCountLabel(terminalsForSandbox(sandbox).length) }}</small>
                </span>
                <span v-if="sandbox.clock?.status === 'drift'" class="clock-warning" :title="'Clock ' + clockStatusText(sandbox.clock)">
                  <i data-lucide="clock"></i>
                </span>
                <span class="status-pill" :data-busy="operationBySandbox[sandbox.slug] ? 'true' : null">
                  {{ operationBySandbox[sandbox.slug] || sandbox.last_status }}
                </span>
              </button>
              <span class="menu-wrap sandbox-row-menu">
                <button class="icon-button tiny" type="button" title="Sandbox actions" @click.stop="toggleSandboxActionMenu(sandbox.slug)">
                  <i data-lucide="ellipsis"></i>
                </button>
                <div v-if="sandboxActionMenuSlug === sandbox.slug" class="menu-panel row-action-menu">
                  <button class="menu-item" type="button" :disabled="sandbox.last_status !== 'running' || busy" @click="closeMenus(); openTerminal(sandbox)">
                    <i class="menu-item-icon" data-lucide="terminal"></i><span class="menu-item-label">New shell</span>
                  </button>
                  <button class="menu-item" type="button" :disabled="!baseStatus.prepared || busy || sandbox.last_status === 'running'" @click="closeMenus(); runSandboxAction('sandbox:start', sandbox, 'Start requested.')">
                    <i class="menu-item-icon" data-lucide="play"></i><span class="menu-item-label">Start</span>
                  </button>
                  <button class="menu-item" type="button" :disabled="busy || sandbox.last_status !== 'running'" @click="closeMenus(); runSandboxAction('sandbox:stop', sandbox, 'Stopped.')">
                    <i class="menu-item-icon" data-lucide="square"></i><span class="menu-item-label">Stop</span>
                  </button>
                  <div class="menu-divider" aria-hidden="true"></div>
                  <button class="menu-item" type="button" @click="openDetailsModal(sandbox)">
                    <i class="menu-item-icon" data-lucide="info"></i><span class="menu-item-label">Details</span>
                  </button>
                  <button class="menu-item" type="button" @click="openPortsModal(sandbox)">
                    <i class="menu-item-icon" data-lucide="radio-tower"></i><span class="menu-item-label">Published ports</span>
                  </button>
                  <button class="menu-item" type="button" @click="openSandboxLogs(sandbox)">
                    <i class="menu-item-icon" data-lucide="scroll-text"></i><span class="menu-item-label">Logs</span>
                  </button>
                  <div class="menu-divider" aria-hidden="true"></div>
                  <button class="menu-item" type="button" :disabled="busy || sandbox.last_status !== 'running'" @click="closeMenus(); runSandboxAction('sandbox:clock:sync', sandbox, 'Clock synced.')">
                    <i class="menu-item-icon" data-lucide="clock"></i><span class="menu-item-label">Sync clock</span>
                  </button>
                  <button class="menu-item" type="button" :disabled="busy" @click="closeMenus(); runSandboxAction('sandbox:refresh-runtime', sandbox, 'Agent CLIs are current.')">
                    <i class="menu-item-icon" data-lucide="package-check"></i><span class="menu-item-label">Update agent CLIs</span>
                  </button>
                  <button class="menu-item menu-item-danger" type="button" :disabled="busy" @click="closeMenus(); destroySandbox(sandbox)">
                    <i class="menu-item-icon" data-lucide="trash-2"></i><span class="menu-item-label">Destroy</span>
                  </button>
                </div>
              </span>
            </div>
          </div>
          <div v-if="!sortedSandboxes.length" class="shell-empty">No sandboxes</div>
        </div>
      </aside>
      <div class="sidebar-resizer" role="separator" title="Resize sidebar" @pointerdown="beginSidebarResize"></div>

      <main class="workspace">
        <section class="detail">
          <div class="terminal-surface">
            <div class="terminal-tabs">
              <button
                v-if="sidebarCollapsed"
                class="terminal-sidebar-toggle"
                type="button"
                title="Show shells"
                aria-pressed="true"
                @click="toggleSidebar"
              >
                <i data-lucide="panel-left-open"></i>
              </button>
              <div
                v-for="term in selectedGroupTerminals"
                :key="term.id"
                class="terminal-tab"
                :class="{ active: term.id === activeTerminal.id, 'has-status': terminalStatusLabel(term.status) }"
                :data-status="term.status"
                role="button"
                tabindex="0"
                @click="focusTerminal(term.id)"
                @keydown.enter.prevent="focusTerminal(term.id)"
                @keydown.space.prevent="focusTerminal(term.id)"
              >
                <span>{{ terminalLabel(term) }}</span>
                <small v-if="terminalStatusLabel(term.status)">{{ terminalStatusLabel(term.status) }}</small>
                <button class="terminal-tab-close" type="button" title="Close terminal" @click.stop="closeTerminal({ terminalId: term.id })">
                  <i data-lucide="x"></i>
                </button>
              </div>
              <button
                class="terminal-tab-add"
                type="button"
                :title="selectedGroupKind === 'local' ? 'New local terminal' : 'New sandbox terminal'"
                :disabled="selectedGroupKind === 'local' ? !canOpenLocalTerminal : !canOpenTerminal"
                @click="selectedGroupKind === 'local' ? openLocalTerminal() : openTerminal(selected)"
              >
                <i data-lucide="plus"></i>
              </button>
            </div>
            <div v-show="terminalVisible" class="terminal-stack">
              <div
                v-for="term in terminals"
                :key="'terminal-host-' + term.id"
                :ref="(element) => setTerminalHost(term.id, element)"
                class="terminal-viewport"
                :class="{ active: term.id === activeTerminal.id }"
              ></div>
            </div>
            <div v-else class="terminal-placeholder">
              <div class="terminal-empty">
                <strong>{{ selectedGroupLabel }}</strong>
                <small>No shells open in this group.</small>
                <button v-if="selectedGroupKind === 'local'" class="tool-button" type="button" :disabled="!canOpenLocalTerminal" @click="openLocalTerminal">
                  <i data-lucide="terminal"></i><span>New Local Shell</span>
                </button>
                <button v-else class="tool-button" type="button" :disabled="!canOpenTerminal" @click="openTerminal(selected)">
                  <i data-lucide="terminal"></i><span>New Sandbox Shell</span>
                </button>
              </div>
            </div>
          </div>
        </section>
      </main>

      <div v-if="createModalOpen" class="modal-backdrop" @click.self="createModalOpen = false">
        <section class="modal-panel create-modal">
          <header class="modal-header">
            <div>
              <h2>Create Sandbox</h2>
              <p>Create starts the sandbox and opens the first terminal.</p>
            </div>
            <button class="icon-button" type="button" title="Close" @click="createModalOpen = false">
              <i data-lucide="x"></i>
            </button>
          </header>
          <div class="modal-body">
            <div class="base-banner" :data-state="baseStatus.state" v-if="!baseStatus.prepared">
              <div>
                <strong>{{ baseStatus.message || 'Setting up base image...' }}</strong>
                <span v-if="baseStatus.error">{{ baseStatus.error }}</span>
                <span v-else>Hoosegow is doing this automatically. Sandbox creation will be available when setup finishes.</span>
              </div>
            </div>
            <form class="create-form modal-create-form" @submit.prevent="createSandbox">
              <label>
                <span>Sandbox name</span>
                <input v-model="form.name" autocomplete="off" placeholder="sandbox">
              </label>
              <label class="path-input">
                <span>Workspace root</span>
                <span class="path-control">
                  <input v-model="form.workspace_root" autocomplete="off" placeholder="/Users/bill/aistuff">
                  <button class="icon-button" type="button" title="Browse workspace roots" @click="browseWorkspace(form.workspace_root)">
                    <i data-lucide="folder-open"></i>
                  </button>
                </span>
              </label>
              <label>
                <span>vCPU</span>
                <input v-model.number="form.vcpus" type="number" min="1" max="32">
              </label>
              <label>
                <span>RAM MiB</span>
                <input v-model.number="form.memory_mib" type="number" min="512" step="512">
              </label>
              <button class="primary-button" type="submit" :disabled="busy || !baseStatus.prepared">
                <i data-lucide="plus"></i>
                <span>{{ baseStatus.prepared ? 'Create + Start' : 'Waiting for setup' }}</span>
              </button>
            </form>
            <div v-if="actionState.active" class="operation-strip">
              <i data-lucide="loader-circle"></i>
              <span>
                <strong>{{ actionState.label }}</strong>
                <small v-if="actionState.detail">{{ actionState.detail }}</small>
              </span>
            </div>
            <div v-if="picker.open" class="picker-panel">
              <div class="picker-toolbar">
                <div>
                  <strong>{{ picker.path || 'Workspace roots' }}</strong>
                  <span v-if="picker.truncated">First 500 directories shown</span>
                  <span v-else>Select the top-level work tree to mount at /workspace.</span>
                </div>
                <button class="icon-button" type="button" title="Close picker" @click="picker.open = false">
                  <i data-lucide="x"></i>
                </button>
              </div>
              <div class="picker-roots" v-if="picker.roots.length">
                <button v-for="root in picker.roots" :key="root.path" type="button" class="tool-button" @click="browseWorkspace(root.path)">
                  <i data-lucide="hard-drive"></i><span>{{ root.path }}</span>
                </button>
              </div>
              <div class="picker-actions">
                <button class="tool-button" type="button" :disabled="!picker.parent || picker.loading" @click="browseWorkspace(picker.parent)">
                  <i data-lucide="corner-up-left"></i><span>Parent</span>
                </button>
                <button class="primary-button" type="button" :disabled="!picker.path || picker.loading" @click="selectWorkspacePath(picker.path)">
                  <i data-lucide="check"></i><span>Select</span>
                </button>
              </div>
              <div v-if="picker.error" class="picker-error">{{ picker.error }}</div>
              <div v-else-if="picker.loading" class="picker-empty">Loading...</div>
              <div v-else-if="!picker.entries.length" class="picker-empty">No child directories</div>
              <div v-else class="picker-list">
                <button v-for="entry in picker.entries" :key="entry.path" type="button" class="picker-row" @click="browseWorkspace(entry.path)">
                  <i data-lucide="folder"></i>
                  <span>{{ entry.name }}</span>
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>

      <div v-if="detailsModalOpen && selected" class="modal-backdrop" @click.self="detailsModalOpen = false">
        <section class="modal-panel">
          <header class="modal-header">
            <div>
              <h2>Sandbox Details</h2>
              <p>{{ selected.slug }}</p>
            </div>
            <button class="icon-button" type="button" title="Close" @click="detailsModalOpen = false">
              <i data-lucide="x"></i>
            </button>
          </header>
          <div class="modal-body">
            <div class="metric-grid">
              <div class="metric">
                <span>Status</span>
                <strong>{{ operationBySandbox[selected.slug] || selected.last_status }}</strong>
              </div>
              <div class="metric">
                <span>vCPU</span>
                <strong>{{ selected.vcpus }}</strong>
              </div>
              <div class="metric">
                <span>RAM</span>
                <strong>{{ selected.memory_mib }} MiB</strong>
              </div>
              <div class="metric">
                <span>Controller</span>
                <strong>:{{ selected.controller?.host_port || '-' }}</strong>
              </div>
              <div class="metric" :data-alert="selected.clock?.status === 'drift' ? 'true' : null">
                <span>Clock</span>
                <strong>{{ clockStatusText(selected.clock) }}</strong>
              </div>
            </div>
            <dl class="manifest">
              <div><dt>Workspace</dt><dd>{{ selected.canonical_workspace_path }}</dd></div>
              <div><dt>Home</dt><dd>{{ selected.home_path }}</dd></div>
              <div><dt>Created</dt><dd>{{ formatDate(selected.created_at) }}</dd></div>
            </dl>
          </div>
        </section>
      </div>

      <div v-if="portsModalOpen && selected" class="modal-backdrop" @click.self="portsModalOpen = false">
        <section class="modal-panel">
          <header class="modal-header">
            <div>
              <h2>Published Ports</h2>
              <p>{{ selected.slug }}</p>
            </div>
            <button class="icon-button" type="button" title="Close" @click="portsModalOpen = false">
              <i data-lucide="x"></i>
            </button>
          </header>
          <div class="modal-body">
            <section class="ports-panel modal-ports">
              <div class="ports-header">
                <div>
                  <h3>Published Ports</h3>
                  <p>Expose a sandbox web server through a local host port.</p>
                </div>
                <form class="port-form" @submit.prevent="publishPort(selected)">
                  <label>
                    <span>Guest</span>
                    <input v-model.number="portForm.guest_port" type="number" min="1" max="65535">
                  </label>
                  <label>
                    <span>Host</span>
                    <input v-model="portForm.host_port" inputmode="numeric" placeholder="auto">
                  </label>
                  <button class="tool-button" type="submit" :disabled="busy">
                    <i data-lucide="radio-tower"></i><span>Publish</span>
                  </button>
                </form>
              </div>
              <div v-if="selected.published_ports?.length" class="port-list">
                <div v-for="mapping in selected.published_ports" :key="mapping.host_port" class="port-row" :data-status="mapping.status">
                  <span class="port-main">
                    <strong>{{ portUrl(mapping) }}</strong>
                    <small>:{{ mapping.host_port }} -> :{{ mapping.guest_port }} / {{ portStatusText(mapping) }}</small>
                  </span>
                  <span class="port-actions">
                    <button class="icon-button" type="button" title="Open port" :disabled="!portIsLive(mapping, selected)" @click="openPort(mapping)">
                      <i data-lucide="external-link"></i>
                    </button>
                    <button class="icon-button" type="button" title="Copy URL" @click="copyPortUrl(mapping)">
                      <i data-lucide="copy"></i>
                    </button>
                    <button v-if="mapping.status === 'conflict'" class="icon-button" type="button" title="Reassign port" :disabled="busy" @click="reassignPort(selected, mapping)">
                      <i data-lucide="shuffle"></i>
                    </button>
                    <button class="icon-button" type="button" title="Unpublish" :disabled="busy" @click="unpublishPort(selected, mapping)">
                      <i data-lucide="x"></i>
                    </button>
                  </span>
                </div>
              </div>
              <div v-else class="port-empty">
                <span>No published ports</span>
                <button class="tool-button" type="button" :disabled="busy" @click="publishPort(selected)">
                  <i data-lucide="radio-tower"></i><span>Publish :{{ portForm.guest_port }}</span>
                </button>
              </div>
            </section>
          </div>
        </section>
      </div>

      <div v-if="baseLogViewer.open" class="modal-backdrop" @click.self="baseLogViewer.open = false">
        <section class="modal-panel log-viewer">
          <header class="modal-header">
            <div>
              <h2>Base Logs</h2>
              <p>
                <span v-if="basePreparing">Preparing now</span>
                <span v-else-if="baseLogViewer.returncode !== null">Exit {{ baseLogViewer.returncode }}</span>
                <span v-else>Idle</span>
                <span v-if="baseLogViewer.duration_seconds !== null"> / {{ baseLogViewer.duration_seconds }}s</span>
              </p>
            </div>
            <span class="modal-actions">
              <button class="icon-button" type="button" title="Refresh logs" @click="loadBaseLogs">
                <i data-lucide="refresh-cw"></i>
              </button>
              <button class="icon-button" type="button" title="Close logs" @click="baseLogViewer.open = false">
                <i data-lucide="x"></i>
              </button>
            </span>
          </header>
          <div v-if="baseLogs.length" class="log-output">
            <div v-for="(line, index) in baseLogs" :key="index">{{ line }}</div>
          </div>
          <div v-else class="log-empty">No base setup logs in this server session.</div>
        </section>
      </div>

      <div v-if="sandboxLogViewer.open" class="modal-backdrop" @click.self="sandboxLogViewer.open = false">
        <section class="modal-panel log-viewer">
          <header class="modal-header">
            <div>
              <h2>Sandbox Logs</h2>
              <p>{{ sandboxLogViewer.title || sandboxLogViewer.sandbox_id }}</p>
            </div>
            <span class="modal-actions">
              <button class="icon-button" type="button" title="Refresh logs" @click="loadSandboxLogs(selected)">
                <i data-lucide="refresh-cw"></i>
              </button>
              <button class="icon-button" type="button" title="Close logs" @click="sandboxLogViewer.open = false">
                <i data-lucide="x"></i>
              </button>
            </span>
          </header>
          <div v-if="sandboxLogs.length" class="log-output">
            <div v-for="(line, index) in sandboxLogs" :key="index">{{ line }}</div>
          </div>
          <div v-else class="log-empty">No lifecycle logs for this sandbox yet.</div>
        </section>
      </div>

      <div v-if="toast.message" class="toast" :data-tone="toast.tone">{{ toast.message }}</div>
    </div>
  `,
}).mount('#app');
