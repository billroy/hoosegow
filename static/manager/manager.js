const { createApp, computed, nextTick, onMounted, reactive, ref, watch } = Vue;

createApp({
  setup() {
    const defaultMicrosandboxBase = 'bullpen-microsandbox-local';
    const state = reactive({
      profiles: [],
      selectedId: null,
      loading: false,
      error: '',
      logs: '',
      actionBusy: '',
      setupBusy: false,
      setupSessionId: '',
      setupProfileId: '',
      setupOutput: '',
      setupExit: '',
      createModalOpen: false,
      deploymentMenuOpen: false,
      baseSnapshots: [],
      baseSnapshotsLoading: false,
      baseSnapshotsError: '',
    });
    const socketRef = ref(null);
    const terminalRef = ref(null);
    const terminal = ref(null);
    const terminalDataDisposable = ref(null);
    const terminalResizeDisposable = ref(null);
    const terminalResizeObserver = ref(null);
    const terminalFitTimer = ref(null);

    const form = reactive({
      displayName: 'Local Bullpen',
      workspaceRoot: '',
      runtime: 'local',
      adminUser: 'admin',
      adminPassword: '',
      sandboxName: '',
      base: defaultMicrosandboxBase,
      vcpus: 4,
      memoryMiB: 4096,
      autoStartWhenManagerStarts: false,
    });

    const selected = computed(() => state.profiles.find(profile => profile.id === state.selectedId) || state.profiles[0] || null);
    const baseSnapshotOptions = computed(() => {
      const byName = new Map();
      (state.baseSnapshots || []).forEach((snapshot) => {
        if (snapshot && snapshot.name) byName.set(snapshot.name, snapshot);
      });
      const selectedBase = form.base || defaultMicrosandboxBase;
      if (selectedBase && !byName.has(selectedBase)) {
        byName.set(selectedBase, { name: selectedBase, unavailable: true });
      }
      return Array.from(byName.values());
    });

    function stateLabel(profile) {
      return (profile && profile.observed && profile.observed.state) || 'unknown';
    }

    function bullpenUrlFor(profile) {
      if (!profile || !profile.ports) return '';
      return `http://127.0.0.1:${profile.ports.bullpen}`;
    }

    function appUrlFor(profile) {
      if (!profile || !profile.ports) return '';
      return `http://127.0.0.1:${profile.ports.app}`;
    }

    function deploymentInfo(profile) {
      return (profile && profile.deploymentInfo) || {};
    }

    function cpuText(profile) {
      const resources = deploymentInfo(profile).resources || {};
      const value = Number(resources.vcpus);
      if (!Number.isFinite(value) || value <= 0) return 'Not configured';
      const suffix = resources.source === 'host' ? ' detected on host' : '';
      return `${value} CPU${value === 1 ? '' : 's'}${suffix}`;
    }

    function memoryText(profile) {
      const resources = deploymentInfo(profile).resources || {};
      const value = Number(resources.memoryMiB);
      if (!Number.isFinite(value) || value <= 0) return 'Not configured';
      const suffix = resources.source === 'host' ? ' detected on host' : '';
      return `${value} MiB${suffix}`;
    }

    function providerLabel(provider) {
      const key = String(provider || '').trim().toLowerCase();
      const labels = { claude: 'Claude', codex: 'Codex', git: 'Git' };
      return labels[key] || key.replace(/(^|-)([a-z])/g, (_match, prefix, letter) => `${prefix}${letter.toUpperCase()}`);
    }

    function providersText(profile) {
      const providers = new Map();
      const allowed = new Set(['claude', 'codex', 'git']);
      const auth = deploymentInfo(profile).providerAuth || {};
      const configured = profile && profile.auth && profile.auth.providers;
      if (configured && typeof configured === 'object') {
        Object.entries(configured).forEach(([provider, config]) => {
          const key = String(provider || '').trim().toLowerCase();
          if (allowed.has(key) && (!config || config.enabled !== false)) providers.set(key, providerLabel(key));
        });
      }
      (deploymentInfo(profile).aiProviders || []).forEach((provider) => {
        const agent = String(provider.agent || '').trim().toLowerCase();
        if (allowed.has(agent)) providers.set(agent, provider.label || providerLabel(agent));
      });
      const git = deploymentInfo(profile).git || {};
      if (!git.error && Array.isArray(git.repositories) && git.repositories.length) {
        providers.set('git', 'Git');
      }
      Object.entries(auth).forEach(([provider, status]) => {
        const key = String(provider || '').trim().toLowerCase();
        if (allowed.has(key)) providers.set(key, (status && status.label) || providerLabel(key));
      });
      if (!providers.size) return 'None configured';
      const order = ['claude', 'codex', 'git'];
      return Array.from(providers.entries())
        .sort(([left], [right]) => {
          const leftIndex = order.includes(left) ? order.indexOf(left) : order.length;
          const rightIndex = order.includes(right) ? order.indexOf(right) : order.length;
          return leftIndex - rightIndex || providers.get(left).localeCompare(providers.get(right));
        })
        .map(([key, label]) => {
          const authenticated = Boolean(auth[key] && auth[key].authenticated);
          return `${label}: ${authenticated ? 'authenticated' : 'not authenticated'}`;
        })
        .join(', ');
    }

    function urlFor(profile) {
      return bullpenUrlFor(profile);
    }

    function resetSetupState() {
      state.setupSessionId = '';
      state.setupProfileId = '';
      state.setupOutput = '';
      state.setupExit = '';
      disposeTerminal();
    }

    function hasActiveSetupSession(profile) {
      return Boolean(
        profile
        && stateLabel(profile) === 'setup-running'
        && state.setupSessionId
        && state.setupProfileId === profile.id
        && !state.setupExit
      );
    }

    function showLogPanel(profile) {
      return Boolean(profile && profile.runtime !== 'microsandbox');
    }

    function showSetupPanel(profile) {
      return Boolean(
        profile
        && profile.runtime === 'microsandbox'
        && (
          (state.setupBusy && state.setupProfileId === profile.id)
          || stateLabel(profile) === 'setup-running'
          || (
            state.setupProfileId === profile.id
            && (state.setupSessionId || state.setupOutput || state.setupExit)
          )
        )
      );
    }

    function restorePseudoAnsi(text) {
      return String(text || '').replace(/(^|[^\x1b])\[([0-9;]*)m/g, (_match, prefix, codes) => `${prefix}\x1b[${codes}m`);
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || `Request failed: ${response.status}`);
      }
      return data;
    }

    async function refresh() {
      state.loading = true;
      state.error = '';
      try {
        const data = await api('/api/profiles');
        state.profiles = data.profiles || [];
        if (!state.selectedId && state.profiles.length) {
          state.selectedId = state.profiles[0].id;
        }
        if (selected.value) {
          if (showLogPanel(selected.value)) await loadLogs(selected.value.id);
          await syncSetupSession(selected.value);
        }
      } catch (err) {
        state.error = err.message;
      } finally {
        state.loading = false;
      }
    }

    function baseSnapshotLabel(snapshot) {
      if (!snapshot) return '';
      const source = snapshot.imageRef ? ` / ${snapshot.imageRef}` : '';
      const suffix = snapshot.unavailable ? ' (not listed)' : '';
      return `${snapshot.name}${source}${suffix}`;
    }

    async function loadBaseSnapshots() {
      state.baseSnapshotsLoading = true;
      state.baseSnapshotsError = '';
      try {
        const data = await api('/api/microsandbox/base-snapshots');
        state.baseSnapshots = data.snapshots || [];
        if (form.runtime === 'microsandbox' && !form.base && state.baseSnapshots.length) {
          form.base = state.baseSnapshots[0].name;
        }
      } catch (err) {
        state.baseSnapshotsError = err.message;
        if (!form.base) form.base = defaultMicrosandboxBase;
      } finally {
        state.baseSnapshotsLoading = false;
      }
    }

    function resetCreateForm() {
      form.displayName = 'Local Bullpen';
      form.workspaceRoot = '';
      form.runtime = 'local';
      form.adminUser = 'admin';
      form.adminPassword = '';
      form.sandboxName = '';
      form.base = defaultMicrosandboxBase;
      form.vcpus = 4;
      form.memoryMiB = 4096;
      form.autoStartWhenManagerStarts = false;
    }

    function openCreateModal() {
      state.deploymentMenuOpen = false;
      state.createModalOpen = true;
      if (form.runtime === 'microsandbox') loadBaseSnapshots();
    }

    function closeCreateModal() {
      state.createModalOpen = false;
    }

    function toggleDeploymentMenu() {
      state.deploymentMenuOpen = !state.deploymentMenuOpen;
    }

    function openDropdownOnEnter(event) {
      const select = event && event.currentTarget;
      if (!select || select.disabled) return;
      if (typeof select.showPicker === 'function') {
        event.preventDefault();
        select.showPicker();
      }
    }

    async function createProfile() {
      state.error = '';
      try {
        const data = await api('/api/profiles', {
          method: 'POST',
          body: JSON.stringify(form),
        });
        state.selectedId = data.profile.id;
        resetCreateForm();
        closeCreateModal();
        await refresh();
      } catch (err) {
        state.error = err.message;
      }
    }

    async function action(profile, name) {
      if (!profile) return;
      state.error = '';
      state.actionBusy = `${profile.id}:${name}`;
      try {
        await api(`/api/profiles/${profile.id}/${name}`, { method: 'POST', body: '{}' });
        await refresh();
      } catch (err) {
        state.error = err.message;
      } finally {
        state.actionBusy = '';
      }
    }

    async function deleteProfile(profile) {
      if (!profile) return;
      if (!confirm(`Delete profile "${profile.displayName}"? This will not delete the workspace.`)) return;
      state.error = '';
      try {
        await api(`/api/profiles/${profile.id}`, { method: 'DELETE' });
        state.selectedId = null;
        resetSetupState();
        await refresh();
      } catch (err) {
        state.error = err.message;
      }
    }

    async function setupProviders(profile) {
      if (!profile || profile.runtime !== 'microsandbox') return;
      state.error = '';
      state.setupBusy = true;
      state.setupSessionId = '';
      state.setupProfileId = profile.id;
      state.setupOutput = '';
      state.setupExit = '';
      disposeTerminal();
      try {
        await nextTick();
        await ensureTerminal();
        const data = await api(`/api/profiles/${profile.id}/setup-providers/start`, { method: 'POST', body: '{}' });
        state.setupSessionId = data.sessionId;
        state.setupProfileId = profile.id;
        syncTerminalPtySize();
        if (data.profile) {
          const index = state.profiles.findIndex(item => item.id === data.profile.id);
          if (index >= 0) state.profiles[index] = data.profile;
        }
        await syncSetupTranscript(profile);
        focusSetupInput();
      } catch (err) {
        state.error = err.message;
      } finally {
        state.setupBusy = false;
      }
    }

    async function syncSetupSession(profile) {
      if (!profile || profile.runtime !== 'microsandbox') return;
      if (stateLabel(profile) !== 'setup-running') {
        if (state.setupProfileId === profile.id) resetSetupState();
        return;
      }
      if (state.setupProfileId === profile.id && state.setupSessionId) return;
      try {
        const data = await api(`/api/profiles/${profile.id}/setup-providers/session`);
        state.setupSessionId = data.sessionId || '';
        state.setupProfileId = profile.id;
        state.setupExit = data.sessionId ? '' : 'Setup input channel unavailable';
        syncTerminalPtySize();
        await syncSetupTranscript(profile);
        focusSetupInput();
      } catch (err) {
        state.error = err.message;
      }
    }

    async function ensureTerminal() {
      if (!terminalRef.value || terminal.value || !window.Terminal) return;
      terminal.value = new window.Terminal({
        convertEol: true,
        cursorBlink: true,
        disableStdin: false,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        fontSize: 12,
        lineHeight: 1.25,
        scrollback: 8000,
        theme: {
          background: '#090b10',
          foreground: '#c8ccd4',
          cursor: '#e1e4ed',
          selectionBackground: '#3b4765',
        },
      });
      terminal.value.open(terminalRef.value);
      terminalDataDisposable.value = terminal.value.onData((data) => {
        if (!state.setupSessionId || !socketRef.value || state.setupExit) return;
        socketRef.value.emit('manager:pty-input', { sessionId: state.setupSessionId, data });
      });
      terminalResizeDisposable.value = terminal.value.onResize(({ cols, rows }) => {
        resizeSetupPty(cols, rows);
      });
      if (typeof ResizeObserver !== 'undefined') {
        terminalResizeObserver.value = new ResizeObserver(() => scheduleTerminalFit());
        terminalResizeObserver.value.observe(terminalRef.value);
      }
      window.addEventListener('resize', scheduleTerminalFit);
      await nextTick();
      fitTerminal();
    }

    function disposeTerminal() {
      if (terminalResizeObserver.value) {
        terminalResizeObserver.value.disconnect();
        terminalResizeObserver.value = null;
      }
      window.removeEventListener('resize', scheduleTerminalFit);
      if (terminalFitTimer.value) {
        clearTimeout(terminalFitTimer.value);
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

    function scheduleTerminalFit() {
      if (terminalFitTimer.value) clearTimeout(terminalFitTimer.value);
      terminalFitTimer.value = setTimeout(() => fitTerminal(), 50);
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
      const cols = Math.max(2, Math.floor(width / cell.width));
      const rows = Math.max(2, Math.floor(height / cell.height));
      if (terminal.value.cols !== cols || terminal.value.rows !== rows) {
        terminal.value.resize(cols, rows);
      }
    }

    function resetTerminal() {
      if (!terminal.value) return;
      terminal.value.reset();
      terminal.value.clear();
    }

    async function replayTerminal() {
      await nextTick();
      await ensureTerminal();
      fitTerminal();
      resetTerminal();
      if (terminal.value && state.setupOutput) terminal.value.write(restorePseudoAnsi(state.setupOutput));
    }

    function resizeSetupPty(cols, rows) {
      if (!state.setupSessionId || !socketRef.value || state.setupExit) return;
      socketRef.value.emit('manager:pty-resize', { sessionId: state.setupSessionId, cols, rows });
    }

    function syncTerminalPtySize() {
      if (!terminal.value) return;
      resizeSetupPty(terminal.value.cols, terminal.value.rows);
    }

    async function replaceSetupOutput(text) {
      state.setupOutput = text || '';
      await replayTerminal();
    }

    function appendSetupOutput(text) {
      if (!text) return;
      if (text.length > 16 && state.setupOutput.endsWith(text)) return;
      state.setupOutput += text;
      writeTerminal(text);
    }

    function writeTerminal(text) {
      if (!text) return;
      ensureTerminal().then(() => {
        if (terminal.value) terminal.value.write(restorePseudoAnsi(text));
      });
    }

    async function syncSetupTranscript(profile = selected.value) {
      if (!profile || profile.runtime !== 'microsandbox') return;
      try {
        const data = await api(`/api/profiles/${profile.id}/logs`);
        const text = data.text || '';
        if (!text || text === state.setupOutput) return;
        if (text.startsWith(state.setupOutput)) {
          appendSetupOutput(text.slice(state.setupOutput.length));
          return;
        }
        await replaceSetupOutput(text);
      } catch (_err) {
        // The regular profile refresh path surfaces connection errors.
      }
    }

    async function syncSetupLog(profile = selected.value) {
      if (!profile || !showLogPanel(profile)) return;
      try {
        const data = await api(`/api/profiles/${profile.id}/logs`);
        state.logs = data.text || '';
      } catch (_err) {
        // The regular profile refresh path surfaces connection errors.
      }
    }

    function focusSetupInput() {
      if (terminal.value && state.setupSessionId && !state.setupExit) terminal.value.focus();
    }

    function setupStatusText(profile) {
      if (state.setupExit && state.setupProfileId === profile.id) return state.setupExit;
      if (hasActiveSetupSession(profile)) return 'Interactive session active';
      if (stateLabel(profile) === 'setup-running') return 'Reconnecting to setup session';
      return 'No setup session active';
    }

    async function loadLogs(profileId) {
      try {
        const data = await api(`/api/profiles/${profileId}/logs`);
        state.logs = data.text || '';
      } catch (_err) {
        state.logs = '';
      }
    }

    function openInstance(profile) {
      if (!profile) return;
      window.open(urlFor(profile), '_blank', 'noopener');
    }

    onMounted(() => {
      refresh();
      const socket = io();
      socketRef.value = socket;
      socket.on('manager:updated', (payload) => {
        state.profiles = payload.profiles || [];
        if (selected.value) {
          syncSetupSession(selected.value);
        }
      });
      socket.on('manager:pty-output', (payload) => {
        if (!payload) return;
        const matchesSession = payload.sessionId && payload.sessionId === state.setupSessionId;
        const matchesStartingProfile = !state.setupSessionId && payload.profileId === state.setupProfileId;
        if (!matchesSession && !matchesStartingProfile) return;
        if (!state.setupSessionId && payload.sessionId) state.setupSessionId = payload.sessionId;
        const text = payload.text || '';
        appendSetupOutput(text);
      });
      socket.on('manager:pty-exit', (payload) => {
        if (!payload || payload.sessionId !== state.setupSessionId) return;
        state.setupExit = `Provider setup exited with ${payload.returncode}`;
        if (selected.value && showLogPanel(selected.value)) loadLogs(selected.value.id);
      });
      socket.on('manager:error', (payload) => {
        state.error = (payload && payload.error) || 'Manager error';
      });
    });

    watch(selected, (profile) => {
      if (!profile) return;
      syncSetupLog(profile);
      syncSetupSession(profile);
      if (profile.runtime === 'microsandbox' && !hasActiveSetupSession(profile)) {
        nextTick(() => replayTerminal());
      }
    });

    watch(() => form.runtime, (runtime) => {
      if (runtime === 'microsandbox') loadBaseSnapshots();
    });

    return {
      state,
      form,
      selected,
      baseSnapshotOptions,
      stateLabel,
      showLogPanel,
      showSetupPanel,
      cpuText,
      memoryText,
      providersText,
      baseSnapshotLabel,
      bullpenUrlFor,
      appUrlFor,
      urlFor,
      refresh,
      createProfile,
      openCreateModal,
      closeCreateModal,
      toggleDeploymentMenu,
      openDropdownOnEnter,
      action,
      setupProviders,
      syncSetupSession,
      syncSetupLog,
      focusSetupInput,
      setupStatusText,
      hasActiveSetupSession,
      terminalRef,
      deleteProfile,
      loadLogs,
      openInstance,
    };
  },
  template: `
    <div class="manager-shell">
      <header class="topbar">
        <div class="brand">
          <div class="brand-title">Bullpen Manager</div>
          <div class="brand-subtitle">Local control plane for Bullpen instances</div>
        </div>
        <button @click="refresh" :disabled="state.loading">Refresh</button>
      </header>

      <div class="content">
        <aside class="sidebar" @click="state.deploymentMenuOpen = false">
          <section class="panel deployments-panel">
            <div class="panel-header deployments-header">
              <div class="panel-title">Deployments</div>
              <div class="menu-anchor" @click.stop>
                <button class="icon-button" type="button" aria-label="Deployment actions" @click.stop="toggleDeploymentMenu">...</button>
                <div v-if="state.deploymentMenuOpen" class="menu">
                  <button type="button" @click="openCreateModal">Create Deployment</button>
                </div>
              </div>
            </div>
          </section>

          <div class="instance-list">
            <div
              v-for="profile in state.profiles"
              :key="profile.id"
              class="instance-row"
              :class="{ active: selected && selected.id === profile.id }"
              role="button"
              tabindex="0"
              @click="state.selectedId = profile.id; if (showLogPanel(profile)) loadLogs(profile.id); syncSetupSession(profile)"
              @keydown.enter.prevent="state.selectedId = profile.id; if (showLogPanel(profile)) loadLogs(profile.id); syncSetupSession(profile)"
              @keydown.space.prevent="state.selectedId = profile.id; if (showLogPanel(profile)) loadLogs(profile.id); syncSetupSession(profile)"
            >
              <span class="instance-name">{{ profile.displayName }}</span>
              <span class="instance-meta">
                {{ profile.runtime }} /
                <a :href="bullpenUrlFor(profile)" target="_blank" rel="noopener" @click.stop>Bullpen {{ profile.ports.bullpen }}</a>
                /
                <a :href="appUrlFor(profile)" target="_blank" rel="noopener" @click.stop>App {{ profile.ports.app }}</a>
              </span>
              <span class="status-line"><span class="dot" :class="stateLabel(profile)"></span>{{ stateLabel(profile) }}</span>
            </div>
          </div>
        </aside>

        <main class="main">
          <div v-if="state.error" class="error">{{ state.error }}</div>

          <section v-if="selected" class="detail">
            <div class="panel">
              <div class="panel-header">
                <div>
                  <div class="panel-title">{{ selected.displayName }}</div>
                  <div class="instance-meta">{{ selected.id }} / {{ selected.runtime }}</div>
                </div>
                <div class="status-line"><span class="dot" :class="stateLabel(selected)"></span>{{ stateLabel(selected) }}</div>
              </div>
              <div class="create-form">
                <div class="actions">
                  <button class="primary" @click="action(selected, 'start')" :disabled="state.actionBusy === selected.id + ':start'">
                    {{ state.actionBusy === selected.id + ':start' ? 'Starting...' : 'Start' }}
                  </button>
                  <button @click="action(selected, 'stop')" :disabled="state.actionBusy === selected.id + ':stop'">Stop</button>
                  <button @click="action(selected, 'restart')" :disabled="state.actionBusy === selected.id + ':restart'">Restart</button>
                  <button @click="openInstance(selected)">Open</button>
                  <button
                    v-if="selected.runtime === 'microsandbox'"
                    @click="setupProviders(selected)"
                    :disabled="state.setupBusy || hasActiveSetupSession(selected)"
                  >
                    {{ state.setupBusy || hasActiveSetupSession(selected) ? 'Setting Up...' : 'Setup Providers' }}
                  </button>
                  <button class="danger" @click="deleteProfile(selected)">Delete</button>
                </div>
                <div class="kv" v-if="selected.sandboxName"><strong>Sandbox</strong><span>{{ selected.sandboxName }}</span></div>
                <div class="kv">
                  <strong>Bullpen</strong>
                  <a :href="bullpenUrlFor(selected)" target="_blank" rel="noopener">{{ bullpenUrlFor(selected) }}</a>
                </div>
                <div class="kv">
                  <strong>App</strong>
                  <a :href="appUrlFor(selected)" target="_blank" rel="noopener">{{ appUrlFor(selected) }}</a>
                </div>
                <div class="kv"><strong>Workspace</strong><span>{{ selected.workspaceRoot }}</span></div>
                <div class="kv"><strong>Home</strong><span>{{ selected.instanceHome }}</span></div>
                <div class="kv" v-if="selected.base"><strong>Base</strong><span>{{ selected.base }}</span></div>
                <div class="kv"><strong>CPU</strong><span>{{ cpuText(selected) }}</span></div>
                <div class="kv"><strong>Memory</strong><span>{{ memoryText(selected) }}</span></div>
                <div class="kv"><strong>Providers</strong><span>{{ providersText(selected) }}</span></div>
                <div class="kv" v-if="selected.observed && selected.observed.pid"><strong>PID</strong><span>{{ selected.observed.pid }}</span></div>
                <div class="kv" v-if="selected.observed && selected.observed.lastError"><strong>Error</strong><span>{{ selected.observed.lastError }}</span></div>
              </div>
            </div>

            <div class="panel" v-if="showSetupPanel(selected)">
              <div class="panel-header">
                <div>
                  <div class="panel-title">Provider Setup</div>
                  <div class="instance-meta">{{ setupStatusText(selected) }}</div>
                </div>
              </div>
              <div class="terminal-panel">
                <div ref="terminalRef" class="terminal-box"></div>
              </div>
            </div>

            <div class="panel" v-if="showLogPanel(selected)">
              <div class="panel-header">
                <div class="panel-title">Logs</div>
                <button @click="loadLogs(selected.id); syncSetupLog(selected)">Reload Logs</button>
              </div>
              <pre class="log-box">{{ state.logs || 'No logs yet.' }}</pre>
            </div>
          </section>

          <div v-else class="empty">
            Create a deployment to get started.
          </div>
        </main>
      </div>

      <div v-if="state.createModalOpen" class="modal-backdrop" @click.self="closeCreateModal">
        <section class="modal" role="dialog" aria-modal="true" aria-labelledby="create-deployment-title">
          <div class="panel-header">
            <div id="create-deployment-title" class="panel-title">Create Deployment</div>
            <button type="button" class="icon-button" aria-label="Close" @click="closeCreateModal">x</button>
          </div>
          <form class="create-form" @submit.prevent="createProfile">
            <div class="field">
              <label>Name</label>
              <input v-model="form.displayName" required>
            </div>
            <div class="field">
              <label>Runtime</label>
              <select v-model="form.runtime" @keydown.enter="openDropdownOnEnter">
                <option value="local">Local</option>
                <option value="microsandbox">Microsandbox</option>
                <option value="docker" disabled>Docker (later)</option>
              </select>
            </div>
            <div class="field">
              <label>Workspace Root</label>
              <input v-model="form.workspaceRoot" placeholder="/path/to/workspace-root" required>
            </div>
            <template v-if="form.runtime === 'microsandbox'">
              <div class="field">
                <label>Sandbox Name</label>
                <input v-model="form.sandboxName" placeholder="bullpen-deployment">
              </div>
              <div class="field">
                <label>Base Snapshot</label>
                <select v-model="form.base" :disabled="state.baseSnapshotsLoading" @keydown.enter="openDropdownOnEnter">
                  <option
                    v-for="snapshot in baseSnapshotOptions"
                    :key="snapshot.name"
                    :value="snapshot.name"
                  >
                    {{ baseSnapshotLabel(snapshot) }}
                  </option>
                </select>
                <div v-if="state.baseSnapshotsError" class="field-error">{{ state.baseSnapshotsError }}</div>
              </div>
              <div class="resource-grid">
                <div class="field">
                  <label>CPU</label>
                  <input type="number" min="1" step="1" v-model.number="form.vcpus" required>
                </div>
                <div class="field">
                  <label>Memory MiB</label>
                  <input type="number" min="1" step="1" v-model.number="form.memoryMiB" required>
                </div>
              </div>
              <div class="field">
                <label>Admin User</label>
                <input v-model="form.adminUser">
              </div>
              <div class="field">
                <label>Admin Password</label>
                <input type="password" v-model="form.adminPassword" required>
              </div>
            </template>
            <label class="status-line">
              <input type="checkbox" v-model="form.autoStartWhenManagerStarts" style="width:auto">
              Auto-start when manager starts
            </label>
            <div class="modal-actions">
              <button type="button" @click="closeCreateModal">Cancel</button>
              <button class="primary" type="submit">Create Deployment</button>
            </div>
          </form>
        </section>
      </div>
    </div>
  `,
}).mount('#app');
