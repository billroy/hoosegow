import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _app_js():
    return (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def _slice_between(text, start, end):
    return text[text.index(start) : text.index(end, text.index(start))]


def _function_body(text, name):
    match = re.search(rf"    (?:async )?function {name}\([^)]*\) \{{", text)
    assert match, f"{name} function not found"
    body_start = match.end()
    depth = 1
    index = body_start
    while index < len(text) and depth:
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
        index += 1
    assert depth == 0, f"{name} function body was not balanced"
    return text[body_start : index - 1]


def test_terminal_sidebar_lists_groups_not_individual_tabs():
    app_js = _app_js()
    aside = _slice_between(app_js, "<aside class=\"sidebar\">", "</aside>")
    local_section = _slice_between(aside, '<div class="shell-group">', '<div class="shell-group">\n          <div class="shell-group-header">\n            <span>Sandboxes</span>')
    local_row = _slice_between(local_section, 'class="shell-group-row"', "</div>")

    assert "Terminal Groups" in aside
    assert "Shells (" not in aside
    assert "v-for=\"group in localGroups\"" in local_section
    assert "<strong>{{ group.label }}</strong>" in local_section
    assert "{{ shellCountLabel(terminalsForLocalGroup(group).length) }}" in aside
    assert "No open shells" in app_js
    assert "v-for=\"sandbox in sortedSandboxes\"" in aside
    assert "class=\"sandbox-row\"" in aside
    assert "selectSandboxGroup(sandbox)" in aside
    assert "Create local group" in aside
    assert "openLocalGroupModal" in aside
    assert 'title="Create local group" :disabled="!canOpenLocalTerminal"' in aside
    assert "Local group actions" in local_section
    assert "toggleLocalGroupActionMenu(group.id)" in local_section
    assert "localGroupActionMenuId === group.id" in local_section
    assert "openLocalTerminal({ localGroup: group })" in local_section
    assert "destroyLocalGroup(group)" in local_section
    assert "group.id === DEFAULT_LOCAL_GROUP_ID" in local_section
    assert "New shell" in aside
    assert "New Shell" in local_section
    assert "Create sandbox" in aside
    assert "{{ basename(sandbox.canonical_workspace_path) }} / {{ shellCountLabel(terminalsForSandbox(sandbox).length) }}" in aside
    assert "title=\"Sandbox menu\"" not in aside
    assert "toggleSandboxMenu" not in aside
    assert "title=\"Create local group\"" not in local_row

    assert "v-for=\"term in" not in aside
    assert "terminal-tab" not in aside
    assert "terminalLabel(term)" not in aside
    assert "Term {{ term.number" not in aside
    assert "Create Local Group" in app_js
    assert "v-model=\"localGroupForm.label\"" in app_js
    assert 'type="submit" :disabled="!canOpenLocalTerminal"' in app_js


def test_terminal_tabs_live_inside_selected_group_workspace():
    app_js = _app_js()
    main = _slice_between(app_js, "<main class=\"workspace\">", "</main>")

    assert "class=\"terminal-tabs\"" in main
    assert "v-for=\"term in selectedGroupTerminals\"" in main
    assert "{{ terminalLabel(term) }}" in main
    assert "focusTerminal(term.id)" in main
    assert "closeTerminal({ terminalId: term.id })" in main
    assert "selectedGroupKind === 'local' ? openLocalTerminal() : openTerminal(selected)" in main
    assert "selectedGroupKind === 'local' ? 'New local terminal' : 'New sandbox terminal'" in main
    assert "terminal-group-title" not in main
    assert "terminal-authority" not in main
    assert "selectedGroupKind === 'local' ? 'LOCAL'" not in main

    assert "class=\"terminal-stack\"" in main
    assert "v-for=\"term in terminals\"" in main
    assert ":ref=\"terminalHostRef(term.id)\"" in main
    assert "v-for=\"term in localTerminals\"" not in main
    assert "v-for=\"term in terminalsForSandbox" not in main


def test_default_terminal_labels_are_unique_numbered_terms_not_shell_shell_shell():
    app_js = _app_js()
    body = _function_body(app_js, "terminalLabel")

    assert "if (!term) return 'Term ?';" in body
    assert "if (term.label && term.label !== 'shell') return term.label;" in body
    assert "return `Term ${term.number || '?'}`;" in body
    assert "{{ terminalLabel(term) }}" in app_js
    assert "<span>{{ term.label }}</span>" not in app_js
    assert "<span>{{ term.label || 'shell' }}</span>" not in app_js
    assert "<span>shell</span>" not in app_js


def test_selected_terminal_group_filters_tabs_by_group_not_flat_workspace():
    app_js = _app_js()
    create_local_group_body = _function_body(app_js, "createLocalGroup")
    destroy_local_group_body = _function_body(app_js, "destroyLocalGroup")

    assert "const selectedGroupKind = ref('local');" in app_js
    assert "const selectedLocalGroupId = ref(DEFAULT_LOCAL_GROUP_ID);" in app_js
    assert "const localTerminals = computed(() => terminals.filter((item) => item.kind === 'local'));" in app_js
    assert (
        "const selectedGroupTerminals = computed(() => (\n"
        "      selectedGroupKind.value === 'local' ? terminalsForLocalGroup(selectedLocalGroup.value) : terminalsForSandbox(selected.value)\n"
        "    ));"
    ) in app_js
    assert "function terminalsForLocalGroup(group)" in app_js
    assert (
        "return localTerminals.value.filter((item) => (item.local_group_id || DEFAULT_LOCAL_GROUP_ID) === group?.id);"
    ) in app_js
    assert (
        "return terminals.filter((item) => item.kind === 'sandbox' && item.sandbox_id === sandbox?.slug);"
    ) in app_js

    focus_body = _function_body(app_js, "focusTerminal")
    assert "const record = await openLocalTerminal({ localGroup: group, manageBusy: false, silent: true });" in create_local_group_body
    assert "if (record) setToast(`${group.label} created.`, 'success');" in create_local_group_body
    assert "if (!group?.id || group.id === DEFAULT_LOCAL_GROUP_ID) return;" in destroy_local_group_body
    assert "await closeLocalGroupTerminals(group.id, { silent: true });" in destroy_local_group_body
    assert "localGroups.splice(index, 1);" in destroy_local_group_body
    assert "saveLocalGroups();" in destroy_local_group_body
    assert "selectedGroupKind.value = 'local';" in focus_body
    assert "selectedLocalGroupId.value = record.local_group_id || DEFAULT_LOCAL_GROUP_ID;" in focus_body
    assert "selectedGroupKind.value = 'sandbox';" in focus_body
    assert "selectedSlug.value = record.sandbox_id || selectedSlug.value;" in focus_body


def test_terminal_session_loading_does_not_auto_replace_or_flatten_tabs():
    app_js = _app_js()
    load_body = _function_body(app_js, "loadTerminalSessions")
    close_body = _function_body(app_js, "closeTerminal")

    assert "const response = await call('terminal:list');" in load_body
    assert "await joinTerminal(terminalInfo, { focus: !focused });" in load_body
    assert "await openTerminal(" not in load_body
    assert "await openLocalTerminal(" not in load_body
    assert "shouldAutoReplace" not in app_js
    assert "autoReplace" not in app_js

    assert "const nextRecord = selectedGroupTerminals.value[0] || null;" in close_body
    assert "await openTerminal(" not in close_body
    assert "await openLocalTerminal(" not in close_body


def test_terminal_switching_preserves_renderer_instead_of_replaying_transcript():
    app_js = _app_js()
    join_body = _function_body(app_js, "joinTerminal")
    focus_body = _function_body(app_js, "focusTerminal")
    ensure_body = _function_body(app_js, "ensureTerminal")
    renderer_body = _function_body(app_js, "ensureTerminalRenderer")
    activation_body = _function_body(app_js, "activateTerminalRenderer")
    close_body = _function_body(app_js, "closeTerminal")
    output_handler = _slice_between(
        app_js,
        "socket.on('sandbox:terminal:output', (payload) => {",
        "socket.on('sandbox:terminal:exit', (payload) => {",
    )

    assert "const terminalHosts = new Map();" in app_js
    assert "const terminalRenderers = new Map();" in app_js
    assert "function setTerminalHost(terminalId, element)" in app_js
    assert "function terminalHostRef(terminalId)" in app_js
    assert "v-for=\"term in terminals\"" in app_js
    assert ":ref=\"terminalHostRef(term.id)\"" in app_js
    assert "class=\"terminal-stack\"" in app_js

    assert "replaceTranscript: true" in join_body
    assert "const existing = terminalRenderers.get(record.id);" in renderer_body
    assert "if (existing) return existing;" in renderer_body
    assert "writeTerminalReplay" not in renderer_body
    assert "await activateTerminalRenderer(record, { replay: options.replay !== false });" in ensure_body
    assert "const rendererExists = Boolean(record?.id && terminalRenderers.has(record.id));" in activation_body
    assert "fitTerminal();" in activation_body
    assert "if (options.replay && !rendererExists && record.transcript)" in activation_body
    assert "await writeTerminalReplay(record.id, record.transcript);" in activation_body
    assert activation_body.index("fitTerminal();") < activation_body.index("await writeTerminalReplay(record.id, record.transcript);")

    assert "disposeTerminal();" not in focus_body
    assert "await ensureTerminal({ replay: false });" in focus_body
    assert "await ensureTerminal();" in focus_body
    assert "disposeTerminal(terminalId);" in close_body
    assert "const renderer = terminalRenderers.get(payload.terminal_id);" in output_handler
    assert "renderer.terminal.write(text)" in output_handler

    assert "sanitizeLocalStartupPromptSpacing" not in app_js
    assert "normalizeTerminalTranscript" not in app_js
    assert "normalizeTerminalOutput" not in app_js


def test_sidebar_and_tab_commands_keep_separate_semantics():
    app_js = _app_js()
    aside = _slice_between(app_js, "<aside class=\"sidebar\">", "</aside>")
    main = _slice_between(app_js, "<main class=\"workspace\">", "</main>")

    assert "@click.stop=\"openLocalGroupModal\"" in aside
    assert "@click=\"closeMenus(); openTerminal(sandbox)\"" in aside
    assert "@click=\"selectedGroupKind === 'local' ? openLocalTerminal() : openTerminal(selected)\"" in main

    assert "openCreateModal" in aside
    assert "openLocalGroupModal" in aside
    assert "toggleLocalGroupActionMenu" in aside
    assert "openCreateModal" not in main
    assert "Create starts the sandbox and opens the first terminal." in app_js


def test_dialogs_share_enter_accept_and_escape_cancel_keyboard_contract():
    app_js = _app_js()
    close_body = _function_body(app_js, "closeActiveDialog")
    accept_body = _function_body(app_js, "acceptActiveDialog")
    keydown_body = _function_body(app_js, "handleDialogKeydown")

    assert "document.addEventListener('keydown', handleDialogKeydown)" in app_js
    assert "document.removeEventListener('keydown', handleDialogKeydown)" in app_js
    assert "event.key === 'Escape' && closeActiveDialog()" in keydown_body
    assert "event.key === 'Enter' && acceptActiveDialog(event)" in keydown_body
    assert "event.preventDefault();" in keydown_body
    assert "event.stopPropagation();" in keydown_body

    for modal_state in (
        "sandboxLogViewer.open",
        "baseLogViewer.open",
        "portsModalOpen.value",
        "detailsModalOpen.value",
        "createModalOpen.value",
        "localGroupModalOpen.value",
    ):
        assert modal_state in close_body
        assert modal_state in accept_body

    assert "if (picker.open) {" in close_body
    assert "picker.open = false;" in close_body
    assert "createSandbox();" in accept_body
    assert "createLocalGroup();" in accept_body
    assert "if (picker.path && !picker.loading) selectWorkspacePath(picker.path);" in accept_body
    assert "eventTargetUsesNativeEnter(event)" in accept_body
    assert "target.closest('button, input, select, textarea, [contenteditable=\"true\"]')" in app_js
