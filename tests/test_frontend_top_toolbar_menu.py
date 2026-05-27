"""Regression checks for top-toolbar main menu import/export wiring."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_toolbar_menu_contains_export_import_actions():
    text = _read("static/components/TopToolbar.js")
    assert "@click=\"toggleMainMenu\"" in text
    assert "class=\"project-menu-item\" @click=\"onExportWorkspace\"><i class=\"menu-item-icon\" data-lucide=\"download\"" in text
    assert "class=\"project-menu-item\" @click=\"onExportWorkers\"><i class=\"menu-item-icon\" data-lucide=\"download\"" in text
    assert "class=\"project-menu-item\" @click=\"onExportAll\"><i class=\"menu-item-icon\" data-lucide=\"download\"" in text
    assert "class=\"project-menu-item\" @click=\"triggerImportWorkspace\"><i class=\"menu-item-icon\" data-lucide=\"upload\"" in text
    assert "class=\"project-menu-item\" @click=\"triggerImportWorkers\"><i class=\"menu-item-icon\" data-lucide=\"upload\"" in text
    assert "class=\"project-menu-item\" @click=\"triggerImportAll\"><i class=\"menu-item-icon\" data-lucide=\"upload\"" in text
    assert "class=\"project-menu-item\" @click=\"onOpenGitHub\"><i class=\"menu-item-icon\" data-lucide=\"git-branch\"" in text
    assert "class=\"project-menu-item\" @click=\"onLogout\"><i class=\"menu-item-icon\" data-lucide=\"log-out\"" in text
    assert "<span class=\"menu-item-label\">Toggle Left Pane</span></button>" in text
    assert "<span class=\"menu-item-label\">Export Project</span></button>" in text
    assert "<span class=\"menu-item-label\">Export Workers</span></button>" in text
    assert "<span class=\"menu-item-label\">Export All</span></button>" in text
    assert "<span class=\"menu-item-label\">Import Project</span></button>" in text
    assert "<span class=\"menu-item-label\">Import Workers</span></button>" in text
    assert "<span class=\"menu-item-label\">Import All</span></button>" in text
    assert "<span class=\"menu-item-label\">Bullpen on GitHub</span></button>" in text
    assert "<span class=\"menu-item-label\">Logout</span></button>" in text
    assert "window.open('https://github.com/billroy/bullpen', '_blank', 'noopener,noreferrer');" in text
    assert "fetch('/login/csrf', { credentials: 'same-origin' });" in text
    assert "form.action = '/logout';" in text
    assert text.index("@click=\"onOpenGitHub\"") < text.index("@click=\"onLogout\"")


def test_toolbar_menu_renders_lucide_icons_after_updates():
    text = _read("static/components/TopToolbar.js")
    assert "mounted() {" in text
    assert "updated() {" in text
    assert "renderLucideIcons(this.$el);" in text
    assert "window.addEventListener('bullpen:menu:close-main', this.onExternalCloseMainMenu);" in text
    assert "window.removeEventListener('bullpen:menu:close-main', this.onExternalCloseMainMenu);" in text
    assert "window.dispatchEvent(new Event('bullpen:menu:close-projects'));" in text
    assert "onExternalCloseMainMenu()" in text


def test_app_wires_toolbar_export_import_events():
    text = _read("static/app.js")
    assert "@export-workspace=\"exportWorkspace\"" in text
    assert "@export-workers=\"exportWorkers\"" in text
    assert "@export-all=\"exportAll\"" in text
    assert "@import-workspace=\"importWorkspace\"" in text
    assert "@import-workers=\"importWorkers\"" in text
    assert "@import-all=\"importAll\"" in text
    assert "await _downloadZip('/api/export/all', 'bullpen-all.zip');" in text
    assert "await _downloadZip(url, 'bullpen-workers.zip');" in text
    assert "async function exportWorker(slot) {" in text
    assert "await _downloadZip(url, 'bullpen-worker.zip');" in text
    assert "await _importZip('/api/import/all', file, 'All-workspace import complete');" in text
    assert "await _importZip(url, file, 'Workers import complete');" in text


def test_worker_colors_menu_includes_marker():
    text = _read("static/components/TopToolbar.js")
    assert "class=\"provider-colors-title\">Worker colors</div>" in text
    assert "['claude','codex','gemini','shell','service','marker']" in text


def test_toolbar_exposes_worker_pause_and_stop_line_controls():
    text = _read("static/components/TopToolbar.js")
    assert "'pause-automation'," in text
    assert "'resume-automation'," in text
    assert "'stop-the-line'," in text
    assert "workerAutomationPaused ? 'Resume automation' : 'Pause automation'" in text
    assert "AUTOMATION PAUSED" in text
    assert "Stop The Line" in text
    assert "window.confirm(" in text


def test_app_wires_toolbar_worker_pause_events():
    text = _read("static/app.js")
    assert ":worker-automation-paused=\"state.config.worker_automation_paused === true\"" in text
    assert "@pause-automation=\"pauseAutomation\"" in text
    assert "@resume-automation=\"resumeAutomation\"" in text
    assert "@stop-the-line=\"stopTheLine\"" in text
    assert "socket.emit('workers:pause_automation'" in text
    assert "socket.emit('workers:resume_automation'" in text
    assert "socket.emit('workers:stop_line'" in text


def test_toolbar_audio_and_worker_color_menus_are_mutually_exclusive():
    text = _read("static/components/TopToolbar.js")
    assert "this.showEventSoundsMenu = false;" in text
    assert "this.showProviderColorsMenu = false;" in text
    assert "toggleProviderColorsMenu()" in text
    assert "toggleEventSoundsMenu()" in text


def test_worker_config_modal_receives_workspace_palette_colors():
    text = _read("static/app.js")
    assert ":provider-colors=\"currentProviderColors\"" in text
    assert ":default-provider-colors=\"defaultProviderColors\"" in text


def test_toolbar_menu_css_anchors_from_left_edge():
    text = _read("static/style.css")
    assert ".toolbar-menu {" in text
    assert "left: 0;" in text
    assert "right: auto;" in text
