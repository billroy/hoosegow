"""Regression checks for read-only detail panel behavior from ticket list rows."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_ticket_list_row_click_emits_read_only_selection_payload():
    text = _read("static/components/KanbanTab.js")
    assert "onListRowClick(task)" in text
    assert "$emit('select-task', { id: task.id, readOnly: true })" in text
    assert "if ((this.listScope || 'live') !== 'live') return;" not in text


def test_app_tracks_read_only_task_detail_mode_for_list_selection():
    text = _read("static/app.js")
    assert "const selectedTaskMode = ref('edit'); // 'edit' | 'read'" in text
    assert "const selectedTaskReadOnly = computed(() => {" in text
    assert "if (selectedTaskMode.value === 'read') return true;" in text
    assert ":read-only=\"selectedTaskReadOnly\"" in text


def test_task_detail_panel_has_read_only_rendering_paths():
    text = _read("static/components/TaskDetailPanel.js")
    assert "props: ['task', 'columns', 'readOnly']" in text
    assert "v-if=\"readOnly\" class=\"detail-title detail-title-readonly\"" in text
    assert "v-else class=\"detail-title\" @click=\"startEditTitle\" title=\"Click to edit\"" in text
    assert "v-else class=\"detail-readonly-value\">{{ columnLabel(task.status) }}</span>" in text
    assert "v-if=\"!readOnly\" class=\"detail-footer\"" in text
    assert "detail-metric-pill" in text
    assert "formatTaskTime(displayedTaskTimeMs)" in text
    assert "const base = getReportedTaskTimeMs(this.task);" in text


def test_ticket_detail_read_only_styles_exist():
    text = _read("static/style.css")
    assert ".detail-title-readonly {" in text
    assert ".detail-readonly-value {" in text
    assert ".detail-metric-pill," in text
