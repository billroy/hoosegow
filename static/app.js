const { createApp, computed, nextTick, reactive, ref, watch } = Vue;

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
    const storedTheme = window.localStorage.getItem('toady-theme');
    const preferredTheme = window.matchMedia?.('(prefers-color-scheme: light)')?.matches ? 'light' : 'dark';
    const theme = ref(storedTheme === 'light' || storedTheme === 'dark' ? storedTheme : preferredTheme);
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
    const baseLogViewer = reactive({
      open: false,
      returncode: null,
      duration_seconds: null,
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
    const basePreparing = computed(() => baseStatus.state === 'preparing');
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
      window.localStorage.setItem('toady-theme', theme.value);
    }

    function toggleTheme() {
      theme.value = theme.value === 'dark' ? 'light' : 'dark';
      applyTheme();
      refreshIcons();
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

    function decodeBase64Replay(value) {
      const binary = window.atob(value || '');
      if (!binary) return '';
      const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
      return new TextDecoder().decode(bytes);
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

    function upsertTerminalRecord(terminalInfo, transcript = '') {
      let record = terminals.find((item) => item.id === terminalInfo.id);
      if (!record) {
        record = {
          id: terminalInfo.id,
          sandbox_id: terminalInfo.sandbox_id,
          cwd: terminalInfo.cwd || '/workspace',
          status: terminalInfo.status || 'running',
          exit_code: terminalInfo.exit_code ?? null,
          transcript,
        };
        terminals.push(record);
      } else {
        record.sandbox_id = terminalInfo.sandbox_id;
        record.cwd = terminalInfo.cwd || record.cwd || '/workspace';
        record.status = terminalInfo.status || record.status || 'running';
        record.exit_code = terminalInfo.exit_code ?? record.exit_code ?? null;
        if (!record.transcript && transcript) record.transcript = transcript;
      }
      return record;
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
      const generatedNameTaken = isGeneratedSandboxName(form.name)
        && sandboxes.some((sandbox) => sandbox.slug === form.name.trim());
      if (!form.name.trim() || generatedNameTaken) {
        form.name = nextSandboxName();
      }
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

    async function joinTerminal(terminalInfo, options = {}) {
      if (!terminalInfo?.id) return null;
      const response = await call('sandbox:terminal:join', { terminal_id: terminalInfo.id });
      const replayText = decodeBase64Replay(response.replay?.data || '');
      const record = upsertTerminalRecord(response.terminal || terminalInfo, replayText);
      if (options.focus) await focusTerminal(record.id);
      return record;
    }

    async function loadTerminalSessions(sandbox = selected.value) {
      if (!sandbox?.slug || !connected.value) return;
      const response = await call('sandbox:terminal:list', { sandbox_id: sandbox.slug });
      const liveIds = new Set((response.terminals || []).map((item) => item.id));
      for (let index = terminals.length - 1; index >= 0; index -= 1) {
        if (terminals[index].sandbox_id === sandbox.slug && !liveIds.has(terminals[index].id)) {
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
        disposeTerminal();
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
      baseLogViewer.open = true;
      await loadBaseLogs();
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
        setToast(baseStatus.message || 'Prepare the Microsandbox base before creating sandboxes.', 'error');
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
        setToast(baseStatus.message || 'Prepare the Microsandbox base before starting sandboxes.', 'error');
        return;
      }
      if (event === 'sandbox:stop' || event === 'sandbox:destroy') {
        await closeSandboxTerminals(sandbox.slug, { silent: true });
      }
      busy.value = true;
      try {
        if (event === 'sandbox:start') setAction(`Starting ${sandbox.slug}...`, sandbox.slug, 'A terminal will open when it is ready.');
        if (event === 'sandbox:stop') setAction(`Stopping ${sandbox.slug}...`, sandbox.slug);
        const response = await call(event, { id: sandbox.slug });
        await loadSandboxes();
        if (event === 'sandbox:start' && response?.sandbox?.last_status === 'running') {
          setAction(`Opening terminal for ${sandbox.slug}...`, sandbox.slug);
          await openTerminal(response.sandbox, { manageBusy: false, manageAction: false });
          setToast('Started and opened a terminal.', 'success');
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

    async function openTerminal(sandbox, options = {}) {
      if (!sandbox || sandbox.last_status !== 'running') {
        setToast('Start the sandbox before opening a terminal.', 'error');
        return;
      }
      if (options.manageBusy !== false) busy.value = true;
      try {
        if (options.manageAction !== false) setAction(`Opening terminal for ${sandbox.slug}...`, sandbox.slug);
        const response = await call('sandbox:terminal:open', {
          sandbox_id: sandbox.slug,
          cols: 100,
          rows: 30,
        });
        const record = upsertTerminalRecord(response.terminal, '');
        await focusTerminal(record.id);
        await nextTick();
        await ensureTerminal();
        setToast(`Terminal ${selectedTerminals.value.length} opened for ${sandbox.slug}.`, 'success');
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
            message: rebuild ? 'Rebuilding Microsandbox base...' : 'Preparing Microsandbox base...',
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

    applyTheme();
    refreshIcons();

    return {
      activeTerminal,
      actionState,
      basename,
      baseLogViewer,
      baseStatus,
      basePreparing,
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
      loadBaseLogs,
      loadBaseStatus,
      loadSandboxes,
      loadTerminalSessions,
      openTerminal,
      openBaseLogs,
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
      selected,
      selectedSlug,
      selectWorkspacePath,
      selectedTerminals,
      sortedSandboxes,
      terminalRef,
      terminals,
      terminalVisible,
      theme,
      toggleTheme,
      toast,
      unpublishPort,
      picker,
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
          <button v-if="baseStatus.prepared" class="tool-button base-rebuild" type="button" title="Rebuild base" :disabled="basePreparing" @click="requestBasePrepare(true)">
            <i data-lucide="hammer"></i><span>Rebuild</span>
          </button>
          <button class="icon-button" type="button" title="Base logs" @click="openBaseLogs">
            <i data-lucide="scroll-text"></i>
          </button>
          <button class="icon-button" type="button" title="Toggle theme" @click="toggleTheme">
            <i :data-lucide="theme === 'dark' ? 'sun' : 'moon'"></i>
          </button>
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
          <span class="status-pill" :data-busy="operationBySandbox[sandbox.slug] ? 'true' : null">
            {{ operationBySandbox[sandbox.slug] || sandbox.last_status }}
          </span>
        </button>
        <div v-if="!sortedSandboxes.length" class="empty-list">No sandboxes</div>
      </aside>

      <main class="workspace">
        <section class="create-band">
          <div class="base-banner" :data-state="baseStatus.state" v-if="!baseStatus.prepared">
            <div>
              <strong>{{ baseStatus.message || 'Microsandbox base is not prepared.' }}</strong>
              <span v-if="baseStatus.error">{{ baseStatus.error }}</span>
              <span v-else>Prepare the base before starting sandboxes.</span>
            </div>
            <span class="base-banner-actions">
              <button v-if="baseStatus.state === 'error'" class="tool-button" type="button" @click="openBaseLogs">
                <i data-lucide="scroll-text"></i><span>Logs</span>
              </button>
              <button class="tool-button" type="button" :disabled="basePreparing" @click="requestBasePrepare(false)">
                <i data-lucide="hammer"></i><span>Prepare</span>
              </button>
            </span>
          </div>
          <div class="base-log" v-if="baseLogs.length">
            <div v-for="(line, index) in baseLogs" :key="index">{{ line }}</div>
          </div>
          <form class="create-form" @submit.prevent="createSandbox">
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
              <span>Create + Start</span>
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
              <span v-else>{{ selected.last_status === 'configured' ? 'Sandbox is configured.' : 'Sandbox is stopped.' }}</span>
              <button v-if="selected.last_status !== 'running'" class="tool-button" type="button" :disabled="!canStartSelected" @click="runSandboxAction('sandbox:start', selected, 'Start requested.')">
                <i data-lucide="play"></i><span>Start</span>
              </button>
            </div>
          </div>

          <dl class="manifest">
            <div><dt>Slug</dt><dd>{{ selected.slug }}</dd></div>
            <div><dt>Home</dt><dd>{{ selected.home_path }}</dd></div>
            <div><dt>Created</dt><dd>{{ formatDate(selected.created_at) }}</dd></div>
          </dl>
        </section>

        <section class="detail empty-detail" v-else>
          <div class="empty-state">
            <i data-lucide="box"></i>
            <h2>No Sandboxes</h2>
            <p>Workspace root required</p>
            <button class="primary-button" type="button" :disabled="!baseStatus.prepared" @click="browseWorkspace(form.workspace_root)">
              <i data-lucide="folder-open"></i><span>Workspace Root</span>
            </button>
          </div>
        </section>
      </main>

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
          <div v-else class="log-empty">No base-prep logs in this server session.</div>
        </section>
      </div>

      <div v-if="toast.message" class="toast" :data-tone="toast.tone">{{ toast.message }}</div>
    </div>
  `,
}).mount('#app');
