"""Regression checks for slash-splitting in the toolbar quick ticket create."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_commands_split_quick_create_text_splits_at_first_slash():
    text = _read("static/commands.js")
    assert "function splitQuickCreateText(text)" in text
    assert "const slashIdx = raw.indexOf('/');" in text
    assert "title: raw.slice(0, slashIdx).trim()," in text
    assert "description: raw.slice(slashIdx + 1).trim()," in text


def test_app_quick_create_accepts_payload_with_description():
    text = _read("static/app.js")
    assert "function quickCreateTask(payload)" in text
    assert "const title = typeof payload === 'string' ? payload.trim() : (payload?.title || '').trim();" in text
    assert "const description = typeof payload === 'string' ? '' : (payload?.description || '').trim();" in text
    assert "pendingQuickCreates.push({ title, description });" in text
    assert "socket.emit('task:create', _wsData({ title, type: 'task', priority: 'normal', tags: [], description }));" in text


def test_quick_create_input_clears_only_after_create_ack():
    app = _read("static/app.js")
    toolbar = _read("static/components/TopToolbar.js")
    assert "const quickCreateClearToken = ref(0);" in app
    assert "quickCreateClearToken.value++;" in app
    assert ':quick-create-clear-token="quickCreateClearToken"' in app
    assert "quickCreateClearToken() {" in toolbar
    assert "this.quickCreateText = '';" in toolbar


def test_toolbar_quick_create_placeholder_mentions_ticket_and_description():
    text = _read("static/components/TopToolbar.js")
    assert 'placeholder="New ticket / description, or > commands"' in text
