"""Regression checks for the Files tab Ace editor upgrade."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_index_loads_pinned_ace_scripts_with_sri():
    text = _read("static/index.html")
    assert "https://cdn.jsdelivr.net/npm/ace-builds@1.44.0/src-min-noconflict/ace.js" in text
    assert "sha384-L35+Z0msDQr3oTrDusYCefF5a2MY3q7nK5sOTBFKQvjoZi15zWLUhzXntENo8d/5" in text
    assert "https://cdn.jsdelivr.net/npm/ace-builds@1.44.0/src-min-noconflict/ext-searchbox.js" in text
    assert "sha384-LRJtdX7s/2zXGXuVjTTV2HRBTxhSnH1RSz7octXy7QHaXSlmCvRe2esC66Ox4l8o" in text
    assert "ace-builds@latest" not in text


def test_app_passes_theme_to_files_tab():
    text = _read("static/app.js")
    assert ':active-theme="currentTheme"' in text
    assert "window.addEventListener('bullpen:command-palette:open', this.openPaletteOverlay);" in _read("static/components/TopToolbar.js")


def test_files_tab_uses_ace_host_and_removes_custom_find_bar():
    text = _read("static/components/FilesTab.js")
    assert 'ref="aceContainer" class="ace-host"' in text
    assert "file-editor-textarea" not in text
    assert "find-replace-bar" not in text
    assert "showFind" not in text
    assert "findText" not in text
    assert "doReplaceAll" not in text


def test_files_tab_has_new_file_draft_flow():
    text = _read("static/components/FilesTab.js")
    assert 'class="files-tree-menu-wrap"' in text
    assert 'class="files-tree-action"' in text
    assert '@click="toggleFileMenu"' in text
    assert 'v-if="showFileMenu" class="project-menu files-tree-menu"' in text
    assert 'data-lucide="file-plus"' in text
    assert "<span class=\"menu-item-label\">New File</span>" in text
    assert "toggleFileMenu()" in text
    assert "async createNewFile()" in text
    assert "this.showFileMenu = false;" in text
    assert "const raw = prompt('New file name');" in text
    assert "const exists = await this._fileExists(path);" in text
    assert "isNew: true" in text
    assert "this.$nextTick(() => this.startEditing());" in text


def test_files_tab_create_flow_verifies_existence_and_create_only_save():
    text = _read("static/components/FilesTab.js")
    assert "_normalizeNewFileName(raw)" in text
    assert "Enter a file name, not a path." in text
    assert "[\\/\\\\?#\\u0000-\\u001f]" in text
    assert "async _fileExists(path)" in text
    assert "method: 'HEAD'" in text
    assert "if (res.status === 404) return false;" in text
    assert "const params = this.activeFile?.isNew ? { create: '1' } : {};" in text
    assert "this.activeFile.isNew = false;" in text
    assert "if (cancelled?.isNew) this._removeOpenFile(cancelled.path);" in text


def test_files_tab_saves_from_ace_and_keeps_size_guard():
    text = _read("static/components/FilesTab.js")
    assert "const content = this._aceValue();" in text
    assert "body: content" in text
    assert "this.activeFile.content = content;" in text
    assert "this.activeFile.content && this.activeFile.content.length > 1_000_000" in text


def test_files_tab_configures_quiet_ace_editor():
    text = _read("static/components/FilesTab.js")
    for expected in [
        "behavioursEnabled: false",
        "wrapBehavioursEnabled: false",
        "enableAutoIndent: false",
        "enableBasicAutocompletion: false",
        "enableLiveAutocompletion: false",
        "enableSnippets: false",
        "highlightActiveLine: false",
        "highlightGutterLine: false",
        "highlightSelectedWord: false",
        "displayIndentGuides: false",
        "highlightIndentGuides: false",
        "showFoldWidgets: false",
        "useWorker: false",
    ]:
        assert expected in text
    assert "ace/ext/language_tools" not in text


def test_files_tab_has_ace_mode_map_and_theme_watcher():
    text = _read("static/components/FilesTab.js")
    for expected in [
        "'.ts': 'typescript'",
        "'.tsx': 'typescript'",
        "'.yaml': 'yaml'",
        "'.yml': 'yaml'",
        "'.toml': 'toml'",
        "return map[ext] || 'text';",
        "activeTheme()",
        "ace/theme/chrome",
        "ace/theme/tomorrow_night",
    ]:
        assert expected in text


def test_files_styles_replace_textarea_and_hide_bracket_marker():
    text = _read("static/style.css")
    assert ".files-tree-menu-wrap" in text
    assert ".files-tree-menu" in text
    assert ".files-tree-action" in text
    assert ".file-tab.unsaved .file-tab-name" in text
    assert ".ace-host {" in text
    assert ".ace-host .ace_bracket" in text
    assert ".file-editor-error" in text
    assert ".file-editor-textarea" not in text
    assert ".find-replace-bar" not in text
