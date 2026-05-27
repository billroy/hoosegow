"""Regression checks for ticket title icon rendering."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_kanban_ticket_card_title_renders_ticket_icon():
    text = _read("static/components/TaskCard.js")
    assert "class=\"task-card-title\"" in text
    assert "class=\"ticket-type-icon ticket-type-icon--card\"" in text
    assert "data-lucide=\"tag\"" in text
    assert "class=\"task-card-title-text\"" in text


def test_ticket_list_title_renders_ticket_icon():
    text = _read("static/components/KanbanTab.js")
    assert "class=\"ticket-list-title-wrap\"" in text
    assert "class=\"ticket-type-icon ticket-type-icon--list\"" in text
    assert "data-lucide=\"tag\"" in text
    assert "class=\"ticket-list-title-text\"" in text


def test_ticket_detail_header_renders_ticket_icon():
    text = _read("static/components/TaskDetailPanel.js")
    assert "class=\"detail-title-wrap\"" in text
    assert "class=\"ticket-type-icon ticket-type-icon--detail\"" in text
    assert "data-lucide=\"tag\"" in text
    assert "renderLucideIcons(this.$el);" in text


def test_leftpane_ticket_title_renders_ticket_icon():
    text = _read("static/components/LeftPane.js")
    assert "class=\"inbox-title-wrap\"" in text
    assert "class=\"ticket-type-icon ticket-type-icon--inbox\"" in text
    assert "data-lucide=\"tag\"" in text
    assert "class=\"inbox-title\"" in text


def test_worker_queue_ticket_title_renders_ticket_icon():
    text = _read("static/components/WorkerCard.js")
    assert "class=\"worker-queue-item\"" in text
    assert "class=\"ticket-type-icon ticket-type-icon--worker-queue\"" in text
    assert "data-lucide=\"tag\"" in text
    assert "class=\"worker-queue-title\"" in text


def test_lucide_render_helper_tolerates_vue_comment_roots():
    text = _read("static/utils.js")
    assert "const root = rootEl?.querySelectorAll ? rootEl : document;" in text
    assert "window.lucide.createIcons({ attrs: { 'stroke-width': 2 }, root });" in text


def test_ticket_icon_styles_exist():
    text = _read("static/style.css")
    assert ".ticket-type-icon" in text
    assert ".ticket-type-icon--card" in text
    assert ".ticket-type-icon--list" in text
    assert ".ticket-type-icon--inbox" in text
    assert ".ticket-type-icon--detail" in text
    assert ".ticket-type-icon--worker-queue" in text
    assert ".inbox-title-wrap" in text
    assert ".task-card-title-text" in text
    assert ".ticket-list-title-wrap" in text
    assert ".ticket-list-title-text" in text
    assert ".detail-title-wrap" in text
    assert ".worker-queue-title" in text


def test_kanban_ticket_card_title_supports_three_line_clamp():
    text = _read("static/style.css")
    assert ".task-card-title-text" in text
    assert "white-space: normal;" in text
    assert "-webkit-line-clamp: 3;" in text
    assert "-webkit-box-orient: vertical;" in text
