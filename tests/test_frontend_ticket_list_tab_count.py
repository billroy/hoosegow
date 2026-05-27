"""Regression checks for the Tickets tab count in list view."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_ticket_list_emits_filtered_shown_count():
    text = _read("static/components/KanbanTab.js")
    assert "emits: ['select-task', 'move-task', 'archive-done', 'new-task', 'update-list-scope', 'update-task', 'update-shown-count']" in text
    assert "filteredTasks: {" in text
    assert "this.$emit('update-shown-count', Array.isArray(tasks) ? tasks.length : 0);" in text


def test_app_uses_ticket_list_shown_count_in_tab_label():
    text = _read("static/app.js")
    assert "const ticketListShownCount = ref(null);" in text
    assert "const shownTicketCount = Number.isFinite(ticketListShownCount.value) ? ticketListShownCount.value : visibleTicketTasks.value.length;" in text
    assert "const ticketsLabel = ticketsViewMode.value === 'list' ? `Tickets (${shownTicketCount})` : 'Tickets';" in text
    assert "@update-shown-count=\"setTicketListShownCount\"" in text
