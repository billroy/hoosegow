"""Regression checks for ticket list search and filter controls."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_ticket_list_renders_search_and_filter_controls():
    text = _read("static/components/KanbanTab.js")
    assert "class=\"ticket-list-filters\"" in text
    assert ":value=\"listScope || 'live'\"" in text
    assert "@emit('update-list-scope'" not in text
    assert "@change=\"$emit('update-list-scope', $event.target.value)\"" in text
    assert "v-model.trim=\"searchText\"" in text
    assert "v-model=\"priorityFilter\"" in text
    assert "v-model=\"statusFilter\"" in text
    assert "v-model=\"typeFilter\"" in text
    assert "All ticket time" in text
    assert "setSort('task_time_ms')" in text


def test_ticket_list_filters_are_applied_before_sorting():
    text = _read("static/components/KanbanTab.js")
    assert "filteredTasks()" in text
    assert "priorityFilter !== 'all'" in text
    assert "statusFilter !== 'all'" in text
    assert "typeFilter !== 'all'" in text
    assert "return this.filteredTasks.slice().sort" in text
    assert "totalTaskTimeMs()" in text
    assert "displayTaskTimeMs(task)" in text
    assert "const base = getReportedTaskTimeMs(task);" in text


def test_ticket_list_filter_styles_exist():
    text = _read("static/style.css")
    assert ".ticket-list-filters" in text
    assert ".ticket-list-filter" in text
    assert ".ticket-list-filter-label" in text
    assert ".ticket-list-search-input" in text
    assert ".ticket-list-filter-select" in text
    assert ".ticket-list-summary" in text
    assert ".ticket-list-col-task-time," in text
