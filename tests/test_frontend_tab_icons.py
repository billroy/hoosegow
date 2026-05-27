"""Regression checks for tab selector icon rendering."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_app_shell_tabs_define_relevant_icons():
    text = _read("static/app.js")
    assert "const ticketsLabel = ticketsViewMode.value === 'list' ? `Tickets (${shownTicketCount})` : 'Tickets';" in text
    assert "{ id: 'tasks', label: ticketsLabel, icon: 'tag' }" in text
    assert "{ id: 'workers', label: workersLabel, icon: 'bot' }" in text
    assert "{ id: 'files', label: 'Files', icon: 'folder' }" in text
    assert "{ id: 'stats', label: 'Stats', icon: 'chart-no-axes-column' }" in text
    assert "{ id: 'commits', label: 'Commits', icon: 'git-commit' }" in text
    assert "isChat: true, canClose: wsChatTabs.length > 1, icon: 'message-square'" in text
    assert "isFocus: true, slotIndex: ft.slotIndex, icon: 'terminal'" in text


def test_tab_buttons_render_lucide_icons_and_hydrate_at_root():
    text = _read("static/app.js")
    assert "class=\"tab-btn-label\"" in text
    assert "class=\"tab-label-icon\"" in text
    assert ":data-lucide=\"tab.icon || tabIcon(tab)\"" in text
    assert "mounted() {" in text
    assert "updated() {" in text
    assert "renderLucideIcons(this.$el);" in text


def test_tab_icon_styles_exist():
    text = _read("static/style.css")
    assert ".tab-btn-label" in text
    assert ".tab-label-icon" in text
    assert ".tab-label-text" in text
    assert "width: 14px;" in text
    assert "height: 14px;" in text
