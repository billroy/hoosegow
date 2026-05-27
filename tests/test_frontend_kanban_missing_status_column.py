"""Regression checks for surfacing tickets whose status column is missing."""


def test_kanban_board_renders_synthetic_columns_for_missing_statuses():
    text = open("static/components/KanbanTab.js", encoding="utf-8").read()

    assert "v-for=\"(col, colIdx) in boardColumns\"" in text
    assert "missingColumnLabel(status)" in text
    assert "key: status" in text
    assert "missing: true" in text
