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
    const form = reactive({
      name: '',
      workspace_root: '',
      vcpus: 4,
      memory_mib: 4096,
    });
    const toast = reactive({ message: '', tone: 'info' });

    const selected = computed(() => sandboxes.find((sandbox) => sandbox.slug === selectedSlug.value) || sandboxes[0] || null);
    const sortedSandboxes = computed(() => [...sandboxes].sort((a, b) => a.slug.localeCompare(b.slug)));

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

    async function destroySandbox(sandbox) {
      if (!sandbox) return;
      const confirmed = window.confirm(`Destroy ${sandbox.slug}?`);
      if (!confirmed) return;
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

    socket.on('connect', async () => {
      connected.value = true;
      try {
        await loadSandboxes();
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

    refreshIcons();

    return {
      basename,
      busy,
      connected,
      createSandbox,
      destroySandbox,
      form,
      formatDate,
      loadSandboxes,
      runSandboxAction,
      selected,
      selectedSlug,
      sortedSandboxes,
      toast,
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
              <button class="tool-button" type="button" :disabled="busy" @click="runSandboxAction('sandbox:start', selected, 'Start requested.')">
                <i data-lucide="play"></i><span>Start</span>
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

          <div class="terminal-surface">
            <div class="terminal-title">
              <i data-lucide="terminal"></i>
              <span>Terminal surface</span>
            </div>
            <div class="terminal-placeholder">
              <span>{{ selected.last_status === 'running' ? 'Ready' : 'Stopped' }}</span>
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
