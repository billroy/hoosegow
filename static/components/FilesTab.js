// Shared fetch wrapper that redirects to /login on a 401. Returning null
// lets callers bail out early without crashing on res.json().
async function filesFetch(input, init) {
  const options = init ? { ...init } : {};
  const headers = new Headers(options.headers || {});
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  if (!headers.has('X-Requested-With')) headers.set('X-Requested-With', 'XMLHttpRequest');
  options.headers = headers;
  const res = await fetch(input, options);
  if (res.status === 401) {
    window.location = '/login?next=' +
      encodeURIComponent(window.location.pathname + window.location.search);
    return null;
  }
  return res;
}

const FileTreeNode = {
  name: 'FileTreeNode',
  props: ['node', 'depth', 'activePath'],
  emits: ['select'],
  template: `
    <div>
      <div
        class="tree-node"
        :class="{ active: node.path === activePath, directory: isDir }"
        :style="{ paddingLeft: (depth * 16 + 8) + 'px' }"
        @click="onClick"
      >
        <span class="tree-icon">{{ isDir ? (expanded ? '&#9660;' : '&#9654;') : '&#9679;' }}</span>
        <span class="tree-name">{{ node.name }}</span>
      </div>
      <div v-if="isDir && expanded">
        <FileTreeNode
          v-for="child in node.children"
          :key="child.path"
          :node="child"
          :depth="depth + 1"
          :active-path="activePath"
          @select="$emit('select', $event)"
        />
      </div>
    </div>
  `,
  data() {
    return { expanded: false };
  },
  computed: {
    isDir() { return this.node.type === 'dir'; },
  },
  methods: {
    onClick() {
      if (this.isDir) {
        this.expanded = !this.expanded;
      } else {
        this.$emit('select', this.node);
      }
    },
  },
};

const FilesTab = {
  props: ['filesVersion', 'workspaceId', 'activeTheme'],
  template: `
    <div class="files-container">
      <div class="files-tree-pane">
        <div class="files-tree-header">
          <span>Workspace Files</span>
          <div class="files-tree-menu-wrap" @click.stop>
            <button class="files-tree-action" @click="toggleFileMenu" title="File actions" aria-label="File actions">...</button>
            <div v-if="showFileMenu" class="project-menu files-tree-menu">
              <button class="project-menu-item" @click="createNewFile">
                <i class="menu-item-icon" data-lucide="file-plus" aria-hidden="true"></i>
                <span class="menu-item-label">New File</span>
              </button>
            </div>
          </div>
        </div>
        <div v-if="loadingTree" class="empty-state">Loading...</div>
        <div v-else-if="treeError" class="empty-state">{{ treeError }}</div>
        <div class="files-tree-body" v-else-if="tree.length">
          <FileTreeNode
            v-for="node in tree"
            :key="node.path"
            :node="node"
            :depth="0"
            :active-path="activeFile?.path"
            @select="openFile"
          />
        </div>
        <div v-else class="empty-state">No files found</div>
      </div>
      <div class="files-viewer-pane">
        <div class="files-tab-bar" v-if="openFiles.length">
          <div
            v-for="f in openFiles"
            :key="f.path"
            class="file-tab"
            :class="{ active: activeFile?.path === f.path, unsaved: f.isNew }"
            @click="switchToFile(f)"
          >
            <span class="file-tab-name">{{ f.isNew ? '* ' + f.name : f.name }}</span>
            <button class="file-tab-close" @click.stop="closeFile(f.path)">&times;</button>
          </div>
        </div>
        <div class="files-viewer-body" v-if="activeFile">
          <!-- Editor toolbar -->
          <div class="file-editor-toolbar">
            <template v-if="!editing">
              <button v-if="canEdit" class="btn btn-sm" @click="startEditing">Edit</button>
              <a class="btn btn-sm file-download-button" :href="downloadUrl" :download="activeFile.name" title="Download">
                <i data-lucide="download" aria-hidden="true"></i>
                <span>Download</span>
              </a>
            </template>
            <template v-if="editing">
              <button class="btn btn-sm btn-primary" @click="saveEdit">Save</button>
              <button class="btn btn-sm" @click="cancelEdit">Cancel</button>
            </template>
          </div>
          <!-- Edit mode -->
          <div v-if="editing" class="file-edit-container">
            <div v-if="editorError" class="file-editor-error">{{ editorError }}</div>
            <div v-show="!editorError" ref="aceContainer" class="ace-host"></div>
          </div>
          <!-- Image -->
          <div v-else-if="isImage" class="file-view-image">
            <img :src="_filesUrl(activeFile.path)" :alt="activeFile.name" />
          </div>
          <!-- PDF -->
          <div v-else-if="isPdf" class="file-view-pdf">
            <embed :src="_filesUrl(activeFile.path, { raw: '1' })" type="application/pdf" width="100%" height="100%" />
          </div>
          <!-- HTML preview -->
          <div v-else-if="isHtml" class="file-view-html">
            <div class="file-view-toggle">
              <button class="btn btn-sm" :class="{ active: viewMode === 'preview' }" @click="viewMode = 'preview'">Preview</button>
              <button class="btn btn-sm" :class="{ active: viewMode === 'source' }" @click="viewMode = 'source'">Source</button>
            </div>
            <iframe v-if="viewMode === 'preview'" sandbox :srcdoc="activeFile.content" class="html-iframe"></iframe>
            <pre v-else class="file-source"><code v-html="highlightedCode"></code></pre>
          </div>
          <!-- Markdown -->
          <div v-else-if="isMarkdown" class="file-view-markdown">
            <div class="file-view-toggle">
              <button class="btn btn-sm" :class="{ active: viewMode === 'preview' }" @click="viewMode = 'preview'">Preview</button>
              <button class="btn btn-sm" :class="{ active: viewMode === 'source' }" @click="viewMode = 'source'">Source</button>
            </div>
            <div v-if="viewMode === 'preview'" class="markdown-body" v-html="renderedMarkdown"></div>
            <pre v-else class="file-source"><code v-html="highlightedCode"></code></pre>
          </div>
          <!-- Source code -->
          <div v-else class="file-view-source">
            <pre class="file-source"><code v-html="highlightedCode"></code></pre>
          </div>
        </div>
        <div v-else class="files-viewer-empty">
          <div class="empty-state">Select a file to view</div>
        </div>
      </div>
    </div>
  `,
  components: { FileTreeNode },
  data() {
    return {
      tree: [],
      loadingTree: false,
      treeError: '',
      openFiles: [],
      activeFile: null,
      viewMode: 'preview',
      editing: false,
      editContent: '',
      editorError: '',
      showFileMenu: false,
      _ace: null,
    };
  },
  computed: {
    ext() {
      if (!this.activeFile) return '';
      const name = this.activeFile.name;
      const dot = name.lastIndexOf('.');
      return dot >= 0 ? name.substring(dot).toLowerCase() : '';
    },
    isImage() {
      return ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.bmp'].includes(this.ext);
    },
    isPdf() {
      return this.ext === '.pdf';
    },
    isHtml() {
      return this.ext === '.html' || this.ext === '.htm';
    },
    isMarkdown() {
      return this.ext === '.md';
    },
    isTextFile() {
      return !this.isImage && !this.isPdf;
    },
    canEdit() {
      if (!this.activeFile || !this.isTextFile) return false;
      // Size guard: skip if content > 1MB
      if (this.activeFile.content && this.activeFile.content.length > 1_000_000) return false;
      return true;
    },
    downloadUrl() {
      if (!this.activeFile) return '#';
      return this._filesUrl(this.activeFile.path, { raw: '1' });
    },
    renderedMarkdown() {
      if (!this.activeFile?.content) return '';
      const md = window.markdownit({ html: false, linkify: true, typographer: true });
      return md.render(this.activeFile.content);
    },
    prismLang() {
      const map = {
        '.js': 'javascript', '.mjs': 'javascript', '.jsx': 'javascript',
        '.ts': 'javascript', '.tsx': 'javascript',
        '.py': 'python',
        '.json': 'json',
        '.css': 'css', '.scss': 'css',
        '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash',
        '.html': 'markup', '.htm': 'markup', '.xml': 'markup', '.svg': 'markup',
        '.md': 'markdown',
      };
      return map[this.ext] || null;
    },
    highlightedCode() {
      if (!this.activeFile?.content) return '';
      const lang = this.prismLang;
      if (lang && Prism.languages[lang]) {
        return Prism.highlight(this.activeFile.content, Prism.languages[lang], lang);
      }
      return this.escapeHtml(this.activeFile.content);
    },
  },
  watch: {
    activeFile() {
      this._destroyAceEditor();
      this.viewMode = 'preview';
      this.editing = false;
    },
    filesVersion() {
      this.loadTree();
      if (this.activeFile && !this.editing) {
        this.reloadActiveFile();
      }
    },
    workspaceId(newId, oldId) {
      if (newId === oldId) return;
      this._destroyAceEditor();
      this.openFiles = [];
      this.activeFile = null;
      this.editing = false;
      this.showFileMenu = false;
      this.loadTree();
    },
    activeTheme() {
      this._setAceTheme();
    },
  },
  mounted() {
    document.addEventListener('click', this.onGlobalClick);
    this.loadTree();
    this.$nextTick(() => renderLucideIcons(this.$el));
  },
  unmounted() {
    document.removeEventListener('click', this.onGlobalClick);
    this._destroyAceEditor();
  },
  updated() {
    renderLucideIcons(this.$el);
  },
  methods: {
    toggleFileMenu() {
      this.showFileMenu = !this.showFileMenu;
    },
    onGlobalClick() {
      this.showFileMenu = false;
    },
    _filesUrl(path, params = {}) {
      const base = path ? '/api/files/' + encodeURI(path) : '/api/files';
      const query = new URLSearchParams(params);
      if (this.workspaceId) query.set('workspaceId', this.workspaceId);
      const suffix = query.toString();
      return suffix ? base + '?' + suffix : base;
    },
    async loadTree() {
      this.loadingTree = true;
      this.treeError = '';
      try {
        const res = await filesFetch(this._filesUrl());
        if (!res) return;
        if (!res.ok) {
          this.tree = [];
          this.treeError = 'Could not load files';
          return;
        }
        this.tree = await res.json();
      } catch (e) {
        console.error('Failed to load file tree', e);
        this.tree = [];
        this.treeError = 'Could not load files';
      } finally {
        this.loadingTree = false;
      }
    },
    async openFile(node) {
      if (node.type !== 'file') return;
      const ext = this.getExt(node.name);
      const existing = this.openFiles.find(f => f.path === node.path);
      if (existing) {
        this.activeFile = existing;
        // Re-fetch content from disk unless currently editing
        if (!this.editing) this.reloadActiveFile();
        return;
      }
      // Images/PDFs don't need content fetch
      if (['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.bmp', '.pdf'].includes(ext)) {
        const file = { path: node.path, name: node.name, content: null };
        this.openFiles.push(file);
        this.activeFile = file;
        return;
      }
      try {
        const res = await filesFetch(this._filesUrl(node.path));
        if (!res) return;
        if (!res.ok) throw new Error('Failed to load');
        const data = await res.json();
        const file = { path: node.path, name: node.name, content: data.content };
        this.openFiles.push(file);
        this.activeFile = file;
      } catch (e) {
        console.error('Failed to open file', e);
      }
    },
    async createNewFile() {
      this.showFileMenu = false;
      if (this.editing) {
        if (!confirm('Discard unsaved changes?')) return;
        this._destroyAceEditor();
        this.editing = false;
      }
      const raw = prompt('New file name');
      const path = this._normalizeNewFileName(raw);
      if (!path) return;
      if (this.openFiles.some(f => f.path === path)) {
        alert('That file is already open.');
        return;
      }
      const exists = await this._fileExists(path);
      if (exists === null) return;
      if (exists) {
        alert('A file already exists at that path.');
        return;
      }
      const file = {
        path,
        name: path.split('/').pop(),
        content: '',
        isNew: true,
      };
      this.openFiles.push(file);
      this.activeFile = file;
      this.$nextTick(() => this.startEditing());
    },
    switchToFile(f) {
      if (this.editing) {
        if (!confirm('Discard unsaved changes?')) return;
        const discarded = this.activeFile;
        this._destroyAceEditor();
        this.editing = false;
        if (discarded?.isNew) {
          this._removeOpenFile(discarded.path);
          if (discarded.path === f.path) return;
        }
      }
      this.activeFile = f;
      this.reloadActiveFile();
    },
    closeFile(path) {
      if (this.editing && this.activeFile?.path === path) {
        if (!confirm('Discard unsaved changes?')) return;
        this._destroyAceEditor();
        this.editing = false;
      }
      const idx = this.openFiles.findIndex(f => f.path === path);
      if (idx < 0) return;
      this.openFiles.splice(idx, 1);
      if (this.activeFile?.path === path) {
        this.activeFile = this.openFiles[Math.min(idx, this.openFiles.length - 1)] || null;
      }
    },
    startEditing() {
      this.editContent = this.activeFile.content || '';
      this.editing = true;
      this.editorError = '';
      this.$nextTick(() => this._createAceEditor());
    },
    async saveEdit() {
      try {
        const content = this._aceValue();
        const params = this.activeFile?.isNew ? { create: '1' } : {};
        const res = await filesFetch(this._filesUrl(this.activeFile.path, params), {
          method: 'PUT',
          headers: { 'Content-Type': 'text/plain' },
          body: content,
        });
        if (!res) return;
        if (!res.ok) {
          const data = await res.json();
          alert('Save failed: ' + (data.error || 'Unknown error'));
          return;
        }
        this.activeFile.content = content;
        this.activeFile.isNew = false;
        this.editContent = content;
        this._destroyAceEditor();
        this.editing = false;
        this.loadTree();
      } catch (e) {
        alert('Save failed: ' + e.message);
      }
    },
    cancelEdit() {
      const cancelled = this.activeFile;
      this._destroyAceEditor();
      this.editing = false;
      if (cancelled?.isNew) this._removeOpenFile(cancelled.path);
    },
    async reloadActiveFile() {
      if (!this.activeFile || this.isImage || this.isPdf) return;
      try {
        const res = await filesFetch(this._filesUrl(this.activeFile.path));
        if (!res) return;
        if (!res.ok) return;
        const data = await res.json();
        this.activeFile.content = data.content;
      } catch (e) {
        // Silently skip reload failures
      }
    },
    getExt(name) {
      const dot = name.lastIndexOf('.');
      return dot >= 0 ? name.substring(dot).toLowerCase() : '';
    },
    _removeOpenFile(path) {
      const idx = this.openFiles.findIndex(f => f.path === path);
      if (idx < 0) return;
      this.openFiles.splice(idx, 1);
      if (this.activeFile?.path === path) {
        this.activeFile = this.openFiles[Math.min(idx, this.openFiles.length - 1)] || null;
      }
    },
    _normalizeNewFileName(raw) {
      const name = String(raw || '').trim();
      if (!name) return '';
      if (/[\/\\?#\u0000-\u001f]/.test(name) || name === '.' || name === '..') {
        alert('Enter a file name, not a path.');
        return '';
      }
      return name;
    },
    async _fileExists(path) {
      try {
        const res = await filesFetch(this._filesUrl(path), { method: 'HEAD' });
        if (!res) return null;
        if (res.status === 404) return false;
        if (res.ok) return true;
        alert('Could not verify whether that file exists.');
        return null;
      } catch (e) {
        alert('Could not verify whether that file exists.');
        return null;
      }
    },
    _createAceEditor() {
      if (!this.editing) return;
      if (!window.ace) {
        this.editorError = 'Editor failed to load.';
        return;
      }
      const container = this.$refs.aceContainer;
      if (!container) return;
      this._destroyAceEditor();
      this.editorError = '';
      window.ace.config.set('basePath', 'https://cdn.jsdelivr.net/npm/ace-builds@1.44.0/src-min-noconflict/');
      this._ace = window.ace.edit(container, {
        mode: `ace/mode/${this._aceModeForExt(this.ext)}`,
        theme: this._aceThemeForActiveTheme(),
        fontSize: 13,
        showPrintMargin: false,
        useWorker: false,
        wrap: true,
        behavioursEnabled: false,
        wrapBehavioursEnabled: false,
        enableAutoIndent: false,
        enableBasicAutocompletion: false,
        enableLiveAutocompletion: false,
        enableSnippets: false,
        highlightActiveLine: false,
        highlightGutterLine: false,
        highlightSelectedWord: false,
        displayIndentGuides: false,
        highlightIndentGuides: false,
        showFoldWidgets: false,
      });
      this._ace.setValue(this.editContent, -1);
      this._installAceCommands();
      this._ace.focus();
      this._ace.resize(true);
    },
    _installAceCommands() {
      if (!this._ace) return;
      this._ace.commands.addCommand({
        name: 'bullpenSave',
        bindKey: { win: 'Ctrl-S', mac: 'Command-S' },
        exec: () => this.saveEdit(),
      });
      this._ace.commands.addCommand({
        name: 'bullpenReplace',
        bindKey: { win: 'Ctrl-H', mac: 'Command-H' },
        exec: (editor) => {
          const searchbox = window.ace?.require?.('ace/ext/searchbox');
          if (searchbox?.Search) searchbox.Search(editor, true);
        },
      });
      this._ace.commands.addCommand({
        name: 'bullpenCommandPalette',
        bindKey: { win: 'Ctrl-K', mac: 'Command-K' },
        exec: () => window.dispatchEvent(new Event('bullpen:command-palette:open')),
      });
    },
    _destroyAceEditor() {
      if (!this._ace) return;
      this.editContent = this._ace.getValue();
      this._ace.destroy();
      this._ace = null;
      if (this.$refs.aceContainer) this.$refs.aceContainer.textContent = '';
    },
    _setAceMode() {
      if (!this._ace) return;
      this._ace.session.setMode(`ace/mode/${this._aceModeForExt(this.ext)}`);
      this._ace.session.setUseWorker(false);
    },
    _setAceTheme() {
      if (!this._ace) return;
      this._ace.setTheme(this._aceThemeForActiveTheme());
    },
    _aceValue() {
      return this._ace ? this._ace.getValue() : this.editContent;
    },
    _aceModeForExt(ext) {
      const map = {
        '.js': 'javascript', '.mjs': 'javascript', '.jsx': 'javascript',
        '.ts': 'typescript', '.tsx': 'typescript',
        '.py': 'python',
        '.json': 'json',
        '.css': 'css', '.scss': 'css',
        '.html': 'html', '.htm': 'html',
        '.xml': 'xml', '.svg': 'xml',
        '.md': 'markdown', '.markdown': 'markdown',
        '.sh': 'sh', '.bash': 'sh', '.zsh': 'sh',
        '.yaml': 'yaml', '.yml': 'yaml',
        '.toml': 'toml',
      };
      return map[ext] || 'text';
    },
    _aceThemeForActiveTheme() {
      const lightThemes = new Set(['light', 'light-ethereal', 'light-stone-teal', 'light-ivory-olive', 'eyeshade']);
      return lightThemes.has(this.activeTheme) ? 'ace/theme/chrome' : 'ace/theme/tomorrow_night';
    },
    escapeHtml(str) {
      return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },
  }
};
