"""Regression checks for Marker worker frontend wiring."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_marker_worker_type_is_registered_in_shared_utils():
    text = _read("static/utils.js")
    assert "'marker'" in text
    assert "function isMarkerWorker(worker)" in text
    assert "if (isMarkerWorker(worker)) return 'square-dot';" in text
    assert "if (isMarkerWorker(worker)) return 'Marker';" in text


def test_marker_worker_create_flow_and_modal_fields_exist():
    tab = _read("static/components/BullpenTab.js")
    modal = _read("static/components/WorkerConfigModal.js")

    assert "libraryMode === 'marker'" in tab
    assert "@click=\"addMarkerWorker()\"" in tab
    assert "type: 'marker'," in tab
    assert "note: ''" in tab
    assert "Eval</span>" not in tab

    assert "isMarker()" in modal
    assert "<span v-if=\"isMarker\" class=\"worker-type-badge\">Marker</span>" in modal
    assert "<div class=\"form-label\">\n            <span>Card Color</span>" in modal
    assert "class=\"worker-color-override-trigger\"" in modal
    assert "ref=\"workerColorTrigger\"" in modal
    assert "Restore Default" in modal
    assert "Use the palette button to pick any per-card override." in modal
    assert "Pick override" not in modal
    assert "Override active" not in modal
    assert "worker-color-override-code" not in modal
    assert "Pass tickets to" in modal
    assert "v-model=\"form.icon\"" not in modal
    assert "placeholder=\"marker or #c8b38c\"" not in modal


def test_worker_modal_preserves_color_override_on_save():
    modal = _read("static/components/WorkerConfigModal.js")

    assert "fields.color = String(fields.color || '').trim();" in modal
    marker_branch = modal.split("if (this.isMarker) {", 1)[1].split("} else if (this.isShell || this.isService) {", 1)[0]
    assert "fields.color = String(fields.color || '').trim();" in marker_branch
    assert "delete fields.color;" not in marker_branch
    non_marker = modal.split("} else if (this.isShell || this.isService) {", 1)[1]
    shell_service_branch, ai_branch = non_marker.split("// Drop non-AI fields from AI payloads.", 1)
    assert "delete fields.color;" not in shell_service_branch
    assert "delete fields.color;" not in ai_branch
    assert "onRestoreDefaultColor()" in modal
    assert "workerColorPickerInput: null," in modal
    assert "getWorkerColorPickerAnchor()" in modal
    assert "buildWorkerColorPickerInput(anchor)" in modal
    assert "teardownWorkerColorPicker()" in modal
    assert "document.body.appendChild(input);" in modal
    assert "requestAnimationFrame(() => {" in modal


def test_marker_worker_card_renders_note_without_output_focus_affordances():
    text = _read("static/components/WorkerCard.js")
    style = _read("static/style.css")

    assert "v-else-if=\"isMarker\" class=\"worker-card-empty worker-card-empty--marker\"" in text
    assert "markerNote()" in text
    assert "if (this.isMarker) return 'Marker';" in text
    assert ".worker-card-empty--marker {" in style
    assert ".worker-card-note {" in style
    assert ".worker-color-override-controls {" in style
    assert ".worker-color-override-trigger {" in style


def test_marker_worker_card_does_not_offer_run_menu_action():
    text = _read("static/components/WorkerCard.js")
    can_start = text.split("canStart() {", 1)[1].split("runMenuLabel()", 1)[0]

    assert "if (this.isMarker) return false;" in can_start
