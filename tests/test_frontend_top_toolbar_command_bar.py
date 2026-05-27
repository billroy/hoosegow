"""Regression checks for top-toolbar command palette behavior."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_top_toolbar_routes_gt_prefixed_entries_to_palette_events():
    text = _read("static/components/TopToolbar.js")
    assert "'run-palette-command'," in text
    assert "'run-palette-input'," in text
    assert "if (this.paletteMode === 'command') {" in text
    assert "this.$emit('run-palette-command', result.command.id, result.args || '');" in text
    assert "this.$emit('run-palette-input', text);" in text
    assert "this.quickCreateText = '';" in text
    assert "this.$emit('quick-create-task', payload);" in text
    assert "text.startsWith('>')" in text
    assert "text.startsWith('?')" in text
    assert "text.startsWith('/')" not in text


def test_app_wires_top_toolbar_palette_events_to_registry_handlers():
    text = _read("static/app.js")
    assert ":palette-commands=\"paletteCommands\"" in text
    assert "@run-palette-command=\"runPaletteCommand\"" in text
    assert "@run-palette-input=\"runPaletteInput\"" in text
    assert "const paletteCommands = computed(() => {" in text
    assert "function runPaletteCommand(commandId, args = '') {" in text
    assert "function runPaletteInput(input) {" in text
    assert "function runCommandBar(input) {" not in text
    assert "Disconnected from Bullpen server. Ticket was not created." in text


def test_command_registry_supports_ticket_and_ui_commands():
    text = _read("static/commands.js")
    assert "id: 'ticket.create'" in text
    assert "id: 'tab.open'" in text
    assert "id: 'tickets.view'" in text
    assert "id: 'tickets.scope'" in text
    assert "id: 'theme.change'" in text
    assert "id: 'ambient.change'" in text
    assert "id: 'volume.set'" in text
    assert "id: 'chat.new'" in text
    assert "id: 'project.import'" in text
    assert "Usage: >view kanban|list" in text
    assert "Usage: >scope live|archived" in text
    assert "Usage: >volume 0-100" in text


def test_command_registry_is_loaded_before_components():
    text = _read("static/index.html")
    assert '<script src="/commands.js"></script>' in text
    assert text.index('<script src="/commands.js"></script>') < text.index('<script src="/components/TopToolbar.js"></script>')


def test_toolbar_teaches_ticket_body_and_gt_command_mode():
    text = _read("static/components/TopToolbar.js")
    assert 'placeholder="New ticket / description, or > commands"' in text
    assert "Use Title / description" in text
    assert "Type > to run Bullpen commands" in text
    assert "Create ticket:" in text
