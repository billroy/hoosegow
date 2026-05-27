"""Regression checks for the Bullpen "Go to" worker dropdown."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_go_to_modal_shows_cell_input_and_worker_dropdown():
    text = _read("static/components/BullpenTab.js")
    assert "Cell address" in text
    assert "v-model=\"goToInput\"" in text
    assert "v-model=\"goToWorkerSlot\"" in text
    assert "Select a worker" in text
    assert "@input=\"onGoToCellInput\"" in text
    assert "@change=\"onGoToWorkerSelect\"" in text


def test_go_to_worker_dropdown_options_include_coordinates():
    text = _read("static/components/BullpenTab.js")
    assert "goToWorkerOptions()" in text
    assert "this.colLabel(item.coord.col)" in text
    assert "this.rowLabel(item.coord.row)" in text
    assert ":value=\"String(item.slotIndex)\"" in text


def test_submit_go_to_uses_selected_worker_before_cell_lookup():
    text = _read("static/components/BullpenTab.js")
    assert "if (this.goToWorkerSlot !== '') {" in text
    assert "const slot = Number.parseInt(this.goToWorkerSlot, 10);" in text
    assert "const selected = this.workerItemBySlot[slot];" in text
    assert "const text = (this.goToInput || '').trim();" in text
    assert text.index("const selected = this.workerItemBySlot[slot];") < text.index("const text = (this.goToInput || '').trim();")


def test_go_to_coord_does_not_scroll_when_destination_is_already_visible():
    text = _read("static/components/BullpenTab.js")
    assert "goToCoord(coord) {" in text
    assert "const visibleCols = this.viewportPx.width / this.columnWidth;" in text
    assert "const visibleRows = this.viewportPx.height / this.rowHeight;" in text
    assert "const isVisible =" in text
    assert "if (!isVisible) {" in text
    assert "this.setOrigin({ col: coord.col, row: coord.row });" in text
