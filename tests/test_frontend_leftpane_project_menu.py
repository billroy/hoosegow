"""Regression checks for left-pane project overflow menu wiring."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_leftpane_projects_header_uses_menu_button_with_add_and_new_options():
    text = _read("static/components/LeftPane.js")
    assert "@click=\"toggleProjectMenu\">...</button>" in text
    assert "class=\"project-menu-item\" @click=\"promptAddProject\"><i class=\"menu-item-icon\" data-lucide=\"folder-open\"" in text
    assert "class=\"project-menu-item\" @click=\"promptNewProject\"><i class=\"menu-item-icon\" data-lucide=\"folder-plus\"" in text
    assert "class=\"project-menu-item\" @click=\"promptCloneProject\"><i class=\"menu-item-icon\" data-lucide=\"git-branch-plus\"" in text
    assert "<span class=\"menu-item-label\">Add Project</span></button>" in text
    assert "<span class=\"menu-item-label\">New Project</span></button>" in text
    assert "<span class=\"menu-item-label\">Clone from Git</span></button>" in text


def test_leftpane_empty_project_hint_tooltip_is_wired():
    text = _read("static/components/LeftPane.js")
    assert "v-if=\"showEmptyProjectHint\" class=\"project-menu-tooltip\"" in text
    assert "Add a project from /workspace to start." in text
    assert "dismissEmptyProjectHint" in text
    assert "this.showProjectMenu = true;" in text
    assert "emptyProjectHintInitialized" in text
    # Hint must key off a "server has responded" signal, not the raw projects
    # array — the reactive([]) initial value is indistinguishable from a real
    # empty list and would cause the hint to flash for users who DO have projects.
    assert "projectsLoaded" in text
    assert "this.showEmptyProjectHint = false;" in text


def test_leftpane_accepts_projects_loaded_prop():
    """Regression: the hint must not be driven solely by the projects array.
    If it is, the initial empty reactive array triggers the hint before the
    server response arrives, and users with real projects see a stale hint."""
    text = _read("static/components/LeftPane.js")
    assert "'projectsLoaded'" in text, "LeftPane must declare a projectsLoaded prop"


def test_app_tracks_projects_loaded_and_passes_to_leftpane():
    text = _read("static/app.js")
    assert "projectsLoaded" in text
    # Must be wired through the socket handler so it can only become true after
    # the server confirms the project list.
    assert "projectsLoaded.value = true;" in text
    assert ':projects-loaded="projectsLoaded"' in text


def test_add_project_prompt_shows_projects_root_and_accepts_name():
    left_pane = _read("static/components/LeftPane.js")
    app = _read("static/app.js")

    assert "'projectsRoot'" in left_pane
    assert "projectEntryRoot()" in left_pane
    assert "Enter project directory under ${root} (name or absolute path):" in left_pane
    assert "Enter new project directory under ${root} (name or absolute path):" in left_pane
    assert "socket.on('project:settings'" in app
    assert "projectSettings.projectsRoot" in app
    assert ':projects-root="projectSettings.projectsRoot"' in app


def test_leftpane_projects_header_and_list_conditions_support_single_project():
    text = _read("static/components/LeftPane.js")
    assert "v-if=\"projects\" class=\"left-pane-section\" :class=\"{ 'project-add-only': projects.length === 0 }\"" in text
    assert "<h3>Projects</h3>" in text
    assert "v-if=\"projects.length > 0\" class=\"project-list\"" in text


def test_leftpane_emits_new_project_and_registers_menu_dismiss_listener():
    text = _read("static/components/LeftPane.js")
    assert "document.addEventListener('click', this.onGlobalClick);" in text
    assert "document.removeEventListener('click', this.onGlobalClick);" in text
    assert "window.addEventListener('bullpen:menu:close-projects', this.onExternalCloseProjectMenu);" in text
    assert "window.removeEventListener('bullpen:menu:close-projects', this.onExternalCloseProjectMenu);" in text
    assert "window.dispatchEvent(new Event('bullpen:menu:close-main'));" in text
    assert "onExternalCloseProjectMenu()" in text
    assert "this.$emit('new-project', path.trim());" in text


def test_clone_project_uses_explicit_default_path_prompt():
    text = _read("static/components/LeftPane.js")
    assert "leave empty to clone next to the active project" not in text
    assert "Enter absolute path to clone into (leave empty for ${defaultPath}):" in text
    assert "repoNameFromUrl(url)" in text
    assert "defaultCloneParent()" in text


def test_app_clone_project_attaches_workspace_id_and_shows_progress_toasts():
    text = _read("static/app.js")
    assert "function cloneProject(data) { socket.emit('project:clone', _wsData(data)); }" in text
    assert "withWorkspace(data)" not in text
    assert "socket.on('project:clone:started'" in text
    assert "socket.on('project:clone:succeeded'" in text


def test_switching_projects_joins_project_socket_room():
    text = _read("static/app.js")
    assert "function switchWorkspace(wsId)" in text
    assert "socket.emit('project:join', { workspaceId: wsId });" in text
    assert "_rememberActiveWorkspace(wsId);" in text


def test_switch_workspace_does_not_early_return_on_unjoined_workspace():
    """Regression: the server now sends state:init only for joined workspaces,
    so clicking an unjoined project in the LeftPane used to hit an early
    `if (!workspaces[wsId]) return;` guard before project:join was emitted —
    nothing happened on click. switchWorkspace must proceed for any wsId that
    appears in the project list so project:join can actually be sent."""
    text = _read("static/app.js")
    assert "if (!workspaces[wsId]) return;" not in text


def test_projects_update_restores_remembered_or_first_available_workspace():
    text = _read("static/app.js")
    assert "const ACTIVE_PROJECT_STORAGE_KEY = 'bullpen.activeWorkspaceId';" in text
    assert "localStorage.setItem(ACTIVE_PROJECT_STORAGE_KEY, wsId);" in text
    assert "localStorage.getItem(ACTIVE_PROJECT_STORAGE_KEY)" in text
    assert "function _restoreWorkspaceAfterProjectsUpdate()" in text
    assert "if (activeWorkspaceId.value) return;" in text
    assert "projects.filter(p => p.available !== false).map(p => p.id)" in text
    assert "availableIds.includes(remembered) ? remembered : availableIds[0]" in text
    assert "_restoreWorkspaceAfterProjectsUpdate();" in text


def test_project_menu_styles_exist():
    text = _read("static/style.css")
    assert ".project-menu-tooltip {" in text
    assert ".project-menu-tooltip::after {" in text
    assert ".project-menu {" in text
    assert ".project-menu-item {" in text
    assert ".menu-item-icon {" in text
    assert ".menu-item-label {" in text


def test_project_remove_button_wiring_exists():
    text = _read("static/components/LeftPane.js")
    assert "class=\"btn-icon project-remove-btn\"" in text
    assert "@click.stop=\"confirmRemoveProject(p)\"" in text
    assert "this.$emit('remove-project', project.id);" in text


def test_project_remove_button_styles_exist():
    text = _read("static/style.css")
    assert ".project-remove-btn {" in text
    assert ".project-item:hover .project-remove-btn" in text


def test_project_list_caps_at_six_rows_and_scrolls():
    text = _read("static/style.css")
    assert "--project-visible-rows: 6;" in text
    assert "max-height: calc(" in text
    assert "overflow-y: auto;" in text
