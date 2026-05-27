const { createApp, computed, nextTick, reactive, ref } = Vue;

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

createApp({
  setup() {
    const socket = io({ transports: ['websocket', 'polling'] });
    const connected = ref(false);
    const busy = ref(false);
    const selectedSlug = ref('');
    const sandboxes = reactive([]);
    const baseStatus = reactive({
      prepared: false,
      state: 'checking',
      name: 'toady-microsandbox-local',
      message: 'Checking Microsandbox base...',
    });
    const baseLogs = reactive([]);
    const form = reactive({
      name: '',
      workspace_root: '',
      vcpus: 4,
      memory_mib: 4096,
    });
    const portForm = reactive({
      guest_port: 3000,
      host_port: '',
    });
    const toast = reactive({ message: '', tone: 'info' });
    const activeTerminal = reactive({
      id: '',
      sandbox_id: '',
      cwd: '',
      status: 'closed',
      exit_code: null,
    });
    const terminals = reactive([]);
    const terminalRef = ref(null);
    const terminal = ref(null);
    const terminalDataDisposable = ref(null);
    const terminalResizeDisposable = ref(null);
    const terminalResizeObserver = ref(null);
    const terminalFitTimer = ref(null);
    const terminalTextDecoders = new Map();

    const selected = computed(() => sandboxes.find((sandbox) => sandbox.slug === selectedSlug.value) || sandboxes[0] || null);
    const sortedSandboxes = computed(() => [...sandboxes].sort((a, b) => a.slug.localeCompare(b.slug)));
    const canStartSelected = computed(() => Boolean(selected.value && baseStatus.prepared && !busy.value));
    const canOpenTerminal = computed(() => Boolean(selected.value && selected.value.last_status === 'running' && !busy.value));
    const terminalVisible = computed(() => Boolean(
      selected.value
      && activeTerminal.id
      && activeTerminal.sandbox_id === selected.value.slug
      && activeTerminal.status !== 'closed'
    ));
    const selectedTerminals = computed(() => terminals.filter((item) => item.sandbox_id === selected.value?.slug));

    function setToast(message, tone = 'info') {
      toast.message = message;
      toast.tone = tone;
      window.clearTimeout(setToast._timer);
      setToast._timer = window.setTimeout(() => {
        toast.message = '';
      }, 4200);
    }

    function refreshIcons() {
      nextTick(() => {
        if (window.lucide) window.lucide.createIcons();
      });
    }

    function currentTerminalRecord() {
      return terminals.find((item) => item.id === activeTerminal.id) || null;
    }

    function syncActiveTerminal(record) {
      activeTerminal.id = record?.id || '';
      activeTerminal.sandbox_id = record?.sandbox_id || '';
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

    function terminalCellSize() {
      if (!terminalRef.value) return null;
      const probe = document.createElement('span');
      probe.textContent = 'W';
      probe.style.position = 'absolute';
      probe.style.visibility = 'hidden';
      probe.style.whiteSpace = 'pre';
      probe.style.fontFamily = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
      probe.style.fontSize = '12px';
      probe.style.lineHeight = '15px';
      terminalRef.value.appendChild(probe);
      const rect = probe.getBoundingClientRect();
      probe.remove();
      if (!rect.width || !rect.height) return null;
      return { width: rect.width, height: rect.height };
    }

    function fitTerminal() {
      terminalFitTimer.value = null;
      if (!terminal.value || !terminalRef.value) return;
      const cell = terminalCellSize();
      if (!cell) return;
      const styles = getComputedStyle(terminalRef.value);
      const width = terminalRef.value.clientWidth - parseFloat(styles.paddingLeft) - parseFloat(styles.paddingRight);
      const height = terminalRef.value.clientHeight - parseFloat(styles.paddingTop) - parseFloat(styles.paddingBottom);
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

    async function ensureTerminal() {
      if (!terminalRef.value || terminal.value) return;
      if (!window.Terminal) {
        setToast('Terminal renderer did not load.', 'error');
        return;
      }
      terminal.value = new window.Terminal({
        convertEol: true,
        cursorBlink: true,
        disableStdin: false,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        fontSize: 12,
        lineHeight: 1.25,
        scrollback: 8000,
        theme: {
          background: '#07090c',
          foreground: '#d7dde7',
          cursor: '#e9edf5',
          selectionBackground: '#3b5366',
        },
      });
      terminal.value.open(terminalRef.value);
      terminalDataDisposable.value = terminal.value.onData((data) => {
        if (!activeTerminal.id || activeTerminal.status !== 'running') return;
        socket.emit('sandbox:terminal:input', { terminal_id: activeTerminal.id, data });
      });
      terminalResizeDisposable.value = terminal.value.onResize(({ cols, rows }) => {
        if (!activeTerminal.id || activeTerminal.status !== 'running') return;
        socket.emit('sandbox:terminal:resize', { terminal_id: activeTerminal.id, cols, rows });
      });
      if (typeof ResizeObserver !== 'undefined') {
        terminalResizeObserver.value = new ResizeObserver(() => scheduleTerminalFit());
        terminalResizeObserver.value.observe(terminalRef.value);
      }
      window.addEventListener('resize', scheduleTerminalFit);
      await nextTick();
      const record = currentTerminalRecord();
      if (record?.transcript) terminal.value.write(record.transcript);
      fitTerminal();
      terminal.value.focus();
    }

    function disposeTerminal() {
      if (terminalResizeObserver.value) {
        terminalResizeObserver.value.disconnect();
        terminalResizeObserver.value = null;
      }
      window.removeEventListener('resize', scheduleTerminalFit);
      if (terminalFitTimer.value) {
        window.clearTimeout(terminalFitTimer.value);
        terminalFitTimer.value = null;
      }
      if (terminalDataDisposable.value) {
        terminalDataDisposable.value.dispose();
        terminalDataDisposable.value = null;
      }
      if (terminalResizeDisposable.value) {
        terminalResizeDisposable.value.dispose();
        terminalResizeDisposable.value = null;
      }
      if (terminal.value) {
        terminal.value.dispose();
        terminal.value = null;
      }
    }

    function replaceSandboxes(nextSandboxes) {
      sandboxes.splice(0, sandboxes.length, ...(Array.isArray(nextSandboxes) ? nextSandboxes : []));
      if (!selectedSlug.value && sandboxes.length) selectedSlug.value = sandboxes[0].slug;
      if (selectedSlug.value && !sandboxes.some((sandbox) => sandbox.slug === selectedSlug.value)) {
        selectedSlug.value = sandboxes[0]?.slug || '';
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

    async function loadBaseStatus() {
      const response = await call('base:status');
      Object.assign(baseStatus, response.base || {});
      refreshIcons();
    }

    async function createSandbox() {
      if (!form.name.trim() || !form.workspace_root.trim()) {
        setToast('Name and workspace are required.', 'error');
        return;
      }
      busy.value = true;
      try {
        const response = await call('sandbox:create', {
          name: form.name.trim(),
          workspace_root: form.workspace_root.trim(),
          vcpus: Number(form.vcpus) || 4,
          memory_mib: Number(form.memory_mib) || 4096,
        });
        selectedSlug.value = response.sandbox.slug;
        form.name = '';
        form.workspace_root = '';
        await loadSandboxes();
        setToast(`Created ${response.sandbox.slug}.`, 'success');
      } catch (error) {
        setToast(error.message, 'error');
      } finally {
        busy.value = false;
      }
    }

    async function runSandboxAction(event, sandbox, successMessage) {
      if (!sandbox) return;
      if (event === 'sandbox:start' && !baseStatus.prepared) {
        setToast(baseStatus.message || 'Prepare the Microsandbox base before starting sandboxes.', 'error');
        return;
      }
      if (event === 'sandbox:stop' || event === 'sandbox:destroy') {
        await closeSandboxTerminals(sandbox.slug, { silent: true });
      }
      busy.value = true;
      try {
        await call(event, { id: sandbox.slug });
        await loadSandboxes();
        setToast(successMessage, 'success');
      } catch (error) {
        setToast(error.message, 'error');
      } finally {
        busy.value = false;
      }
    }

    async function openTerminal(sandbox) {
      if (!sandbox || sandbox.last_status !== 'running') {
        setToast('Start the sandbox before opening a terminal.', 'error');
        return;
      }
      busy.value = true;
      try {
        const response = await call('sandbox:terminal:open', {
          sandbox_id: sandbox.slug,
          cols: 100,
          rows: 30,
        });
        const record = {
          id: response.terminal.id,
          sandbox_id: response.terminal.sandbox_id,
          cwd: response.terminal.cwd || '/workspace',
          status: 'running',
          exit_code: null,
          transcript: '',
        };
        terminals.push(record);
        await focusTerminal(record.id);
        await nextTick();
        await ensureTerminal();
        setToast(`Terminal ${selectedTerminals.value.length} opened for ${sandbox.slug}.`, 'success');
      } catch (error) {
        setToast(error.message, 'error');
      } finally {
        busy.value = false;
        refreshIcons();
      }
    }

    async function focusTerminal(terminalId) {
      const record = terminals.find((item) => item.id === terminalId);
      if (!record) return;
      if (activeTerminal.id === terminalId) {
        if (terminal.value) terminal.value.focus();
        return;
      }
      disposeTerminal();
      syncActiveTerminal(record);
      await nextTick();
      await ensureTerminal();
      refreshIcons();
    }

    async function closeTerminal(options = {}) {
      const terminalId = options.terminalId || activeTerminal.id;
      if (terminalId && options.remote !== false) {
        socket.emit('sandbox:terminal:close', { terminal_id: terminalId });
      }
      const index = terminals.findIndex((item) => item.id === terminalId);
      const wasActive = terminalId === activeTerminal.id;
      const sandboxId = index >= 0 ? terminals[index].sandbox_id : activeTerminal.sandbox_id;
      if (index >= 0) terminals.splice(index, 1);
      terminalTextDecoders.delete(terminalId);
      if (wasActive) {
        disposeTerminal();
        const nextRecord = terminals.find((item) => item.sandbox_id === sandboxId) || null;
        syncActiveTerminal(nextRecord);
        if (nextRecord) {
          await nextTick();
          await ensureTerminal();
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
      if (mapping.status === 'pending_restart') return 'after restart';
      if (mapping.status === 'remove_on_restart') return 'removing on restart';
      return mapping.status || 'active';
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
      const confirmed = window.confirm(`Destroy ${sandbox.slug}?`);
      if (!confirmed) return;
      await closeSandboxTerminals(sandbox.slug, { silent: true });
      busy.value = true;
      try {
        await call('sandbox:destroy', { id: sandbox.slug, purge: true });
        await loadSandboxes();
        setToast(`Destroyed ${sandbox.slug}.`, 'success');
      } catch (error) {
        setToast(error.message, 'error');
      } finally {
        busy.value = false;
      }
    }

    async function requestBasePrepare() {
      try {
        const response = await call('base:prepare');
        if (response.started) {
          baseLogs.splice(0, baseLogs.length);
          setToast('Base preparation started.', 'success');
          Object.assign(baseStatus, {
            prepared: false,
            state: 'preparing',
            message: 'Preparing Microsandbox base...',
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
        await Promise.all([loadBaseStatus(), loadSandboxes()]);
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
      if (payload?.id === selectedSlug.value) selectedSlug.value = '';
      loadSandboxes().catch((error) => setToast(error.message, 'error'));
    });
    socket.on('sandbox:terminal:output', (payload) => {
      const record = terminals.find((item) => item.id === payload?.terminal_id);
      if (!record) return;
      try {
        const text = decodeBase64Text(payload.data, payload.terminal_id);
        record.transcript = `${record.transcript || ''}${text}`;
        if (record.transcript.length > 200000) record.transcript = record.transcript.slice(-200000);
        if (terminal.value && payload.terminal_id === activeTerminal.id) terminal.value.write(text);
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
      if (payload.terminal_id === activeTerminal.id) {
        activeTerminal.status = 'exited';
        activeTerminal.exit_code = payload.exit_code;
        if (terminal.value) terminal.value.write(text);
      }
    });
    socket.on('sandbox:terminal:error', (payload) => {
      if (payload?.terminal_id && payload.terminal_id !== activeTerminal.id) return;
      const message = payload?.error || 'Terminal error';
      if (terminal.value && payload?.terminal_id === activeTerminal.id) {
        terminal.value.write(`\r\n[terminal error: ${message}]\r\n`);
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
      refreshIcons();
    });
    socket.on('base:log', (payload) => {
      const line = payload?.line || '';
      if (!line) return;
      baseLogs.push(line);
      if (baseLogs.length > 300) baseLogs.splice(0, baseLogs.length - 300);
    });

    refreshIcons();

    return {
      activeTerminal,
      basename,
      baseStatus,
      baseLogs,
      busy,
      canOpenTerminal,
      canStartSelected,
      closeTerminal,
      connected,
      createSandbox,
      destroySandbox,
      form,
      formatDate,
      loadBaseStatus,
      loadSandboxes,
      openTerminal,
      copyPortUrl,
      openPort,
      portForm,
      portStatusText,
      portUrl,
      publishPort,
      focusTerminal,
      requestBasePrepare,
      runSandboxAction,
      selected,
      selectedSlug,
      selectedTerminals,
      sortedSandboxes,
      terminalRef,
      terminals,
      terminalVisible,
      toast,
      unpublishPort,
    };
  },
  template: `
    <div class="toady-shell">
      <header class="topbar">
        <div class="brand">
          <div class="brand-mark">T</div>
          <div>
            <h1>Toady</h1>
            <p>Microsandbox terminals</p>
          </div>
        </div>
        <div class="topbar-actions">
          <span class="base-chip" :data-state="baseStatus.state">
            <i data-lucide="box"></i>
            {{ baseStatus.prepared ? 'Base ready' : 'Base missing' }}
          </span>
          <span class="connection" :class="{ online: connected }">
            <span class="dot"></span>{{ connected ? 'Connected' : 'Offline' }}
          </span>
          <button class="icon-button" type="button" title="Refresh" @click="loadSandboxes">
            <i data-lucide="refresh-cw"></i>
          </button>
        </div>
      </header>

      <aside class="sidebar">
        <div class="sidebar-heading">
          <h2>Sandboxes</h2>
          <span>{{ sortedSandboxes.length }}</span>
        </div>
        <button
          v-for="sandbox in sortedSandboxes"
          :key="sandbox.slug"
          type="button"
          class="sandbox-row"
          :class="{ active: selectedSlug === sandbox.slug }"
          @click="selectedSlug = sandbox.slug"
        >
          <span class="status-dot" :data-status="sandbox.last_status"></span>
          <span class="sandbox-main">
            <strong>{{ sandbox.name || sandbox.slug }}</strong>
            <small>{{ basename(sandbox.canonical_workspace_path) }}</small>
          </span>
          <span class="status-pill">{{ sandbox.last_status }}</span>
        </button>
        <div v-if="!sortedSandboxes.length" class="empty-list">No sandboxes</div>
      </aside>

      <main class="workspace">
        <section class="create-band">
          <div class="base-banner" :data-state="baseStatus.state" v-if="!baseStatus.prepared">
            <div>
              <strong>{{ baseStatus.message || 'Microsandbox base is not prepared.' }}</strong>
              <span>Prepare the base before starting sandboxes.</span>
            </div>
            <button class="tool-button" type="button" @click="requestBasePrepare">
              <i data-lucide="hammer"></i><span>Prepare</span>
            </button>
          </div>
          <div class="base-log" v-if="baseLogs.length">
            <div v-for="(line, index) in baseLogs" :key="index">{{ line }}</div>
          </div>
          <form class="create-form" @submit.prevent="createSandbox">
            <label>
              <span>Name</span>
              <input v-model="form.name" autocomplete="off" placeholder="project-demo">
            </label>
            <label class="path-input">
              <span>Workspace</span>
              <input v-model="form.workspace_root" autocomplete="off" placeholder="/Users/bill/aistuff/toady">
            </label>
            <label>
              <span>vCPU</span>
              <input v-model.number="form.vcpus" type="number" min="1" max="32">
            </label>
            <label>
              <span>RAM MiB</span>
              <input v-model.number="form.memory_mib" type="number" min="512" step="512">
            </label>
            <button class="primary-button" type="submit" :disabled="busy">
              <i data-lucide="plus"></i>
              <span>Create</span>
            </button>
          </form>
        </section>

        <section class="detail" v-if="selected">
          <div class="detail-header">
            <div>
              <h2>{{ selected.name || selected.slug }}</h2>
              <p>{{ selected.canonical_workspace_path }}</p>
            </div>
            <div class="detail-actions">
              <button class="tool-button" type="button" :disabled="!canStartSelected" @click="runSandboxAction('sandbox:start', selected, 'Start requested.')">
                <i data-lucide="play"></i><span>Start</span>
              </button>
              <button class="primary-button" type="button" :disabled="!canOpenTerminal" @click="openTerminal(selected)">
                <i data-lucide="terminal"></i><span>New Terminal</span>
              </button>
              <button class="tool-button" type="button" :disabled="busy" @click="runSandboxAction('sandbox:stop', selected, 'Stopped.')">
                <i data-lucide="square"></i><span>Stop</span>
              </button>
              <button class="danger-button" type="button" :disabled="busy" @click="destroySandbox(selected)">
                <i data-lucide="trash-2"></i><span>Destroy</span>
              </button>
            </div>
          </div>

          <div class="metric-grid">
            <div class="metric">
              <span>Status</span>
              <strong>{{ selected.last_status }}</strong>
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
          </div>

          <section class="ports-panel">
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
                  <button class="icon-button" type="button" title="Open port" :disabled="mapping.status !== 'active'" @click="openPort(mapping)">
                    <i data-lucide="external-link"></i>
                  </button>
                  <button class="icon-button" type="button" title="Copy URL" @click="copyPortUrl(mapping)">
                    <i data-lucide="copy"></i>
                  </button>
                  <button class="icon-button" type="button" title="Unpublish" :disabled="busy" @click="unpublishPort(selected, mapping)">
                    <i data-lucide="x"></i>
                  </button>
                </span>
              </div>
            </div>
            <div v-else class="port-empty">No published ports</div>
          </section>

          <div class="terminal-surface">
            <div class="terminal-title">
              <span class="terminal-title-text">
                <i data-lucide="terminal"></i>
                <span>Terminal</span>
                <small v-if="terminalVisible">{{ activeTerminal.cwd }}</small>
              </span>
              <button class="tool-button terminal-new" type="button" :disabled="!canOpenTerminal" @click="openTerminal(selected)">
                <i data-lucide="plus"></i><span>New</span>
              </button>
            </div>
            <div v-if="selectedTerminals.length" class="terminal-tabs">
              <div
                v-for="(term, index) in selectedTerminals"
                :key="term.id"
                class="terminal-tab"
                :class="{ active: term.id === activeTerminal.id }"
                :data-status="term.status"
                role="button"
                tabindex="0"
                @click="focusTerminal(term.id)"
                @keydown.enter.prevent="focusTerminal(term.id)"
                @keydown.space.prevent="focusTerminal(term.id)"
              >
                <span>Term {{ index + 1 }}</span>
                <small>{{ term.status }}</small>
                <button class="terminal-tab-close" type="button" title="Close terminal" @click.stop="closeTerminal({ terminalId: term.id })">
                  <i data-lucide="x"></i>
                </button>
              </div>
            </div>
            <div v-if="terminalVisible" ref="terminalRef" class="terminal-viewport"></div>
            <div v-else class="terminal-placeholder">
              <button v-if="selected.last_status === 'running'" class="primary-button" type="button" :disabled="busy" @click="openTerminal(selected)">
                <i data-lucide="terminal"></i><span>Open Terminal</span>
              </button>
              <span v-else>Start the sandbox to open a terminal</span>
            </div>
          </div>

          <dl class="manifest">
            <div><dt>Slug</dt><dd>{{ selected.slug }}</dd></div>
            <div><dt>Home</dt><dd>{{ selected.home_path }}</dd></div>
            <div><dt>Created</dt><dd>{{ formatDate(selected.created_at) }}</dd></div>
          </dl>
        </section>

        <section class="detail empty-detail" v-else>
          <div class="terminal-surface">
            <div class="terminal-placeholder"><span>No sandbox selected</span></div>
          </div>
        </section>
      </main>

      <div v-if="toast.message" class="toast" :data-tone="toast.tone">{{ toast.message }}</div>
    </div>
  `,
}).mount('#app');
