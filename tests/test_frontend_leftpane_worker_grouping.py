"""Regression checks for left-pane worker list grouping by marker."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEFTPANE_PATH = ROOT / "static" / "components" / "LeftPane.js"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_leftpane_workerlist_groups_marker_workers_textual_invariants():
    text = _read("static/components/LeftPane.js")
    assert "if (m.slotData.type !== 'marker') continue;" in text
    assert "disp.startsWith('worker:')" in text
    assert "disp.startsWith('pass:')" in text
    assert "e.col === m.col && e.row > m.row" in text
    assert "for (const e of gridOrder)" in text


def _harness(layout: dict, config: dict) -> list:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    script = """
const fs = require('fs');
const src = fs.readFileSync(%(path)s, 'utf8');
const startMarker = 'workerList() {';
const start = src.indexOf(startMarker);
if (start < 0) throw new Error('workerList not found');
let depth = 0;
let i = start + startMarker.length - 1;
for (; i < src.length; i++) {
  const ch = src[i];
  if (ch === '{') depth++;
  else if (ch === '}') { depth--; if (depth === 0) break; }
}
const body = src.slice(start + startMarker.length, i);
const compute = new Function('layout', 'config',
  'const self = { layout, config };' +
  body.replace(/this\\./g, 'self.')
);
const layout = %(layout)s;
const config = %(config)s;
const result = compute(layout, config);
process.stdout.write(JSON.stringify(result.map(w => w.name)));
""" % {
        "path": json.dumps(str(LEFTPANE_PATH)),
        "layout": json.dumps(layout),
        "config": json.dumps(config),
    }
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_leftpane_workerlist_orders_marker_groups_then_loose():
    layout = {
        "slots": [
            {"name": "M1", "type": "marker", "row": 0, "col": 0, "disposition": ""},
            {"name": "A",  "type": "ai",     "row": 1, "col": 0, "disposition": "worker:C"},
            {"name": "M2", "type": "marker", "row": 0, "col": 3, "disposition": ""},
            {"name": "B",  "type": "ai",     "row": 1, "col": 3, "disposition": ""},
            {"name": "C",  "type": "ai",     "row": 5, "col": 5, "disposition": ""},
            {"name": "L1", "type": "ai",     "row": 7, "col": 1, "disposition": ""},
            {"name": "L2", "type": "ai",     "row": 7, "col": 2, "disposition": ""},
        ]
    }
    out = _harness(layout, {"grid": {"cols": 6}})
    assert out == ["M1", "A", "C", "M2", "B", "L1", "L2"]


def test_leftpane_workerlist_pass_direction_groups_neighbor():
    layout = {
        "slots": [
            {"name": "Loose", "type": "ai",     "row": 0, "col": 5, "disposition": ""},
            {"name": "M",     "type": "marker", "row": 0, "col": 0, "disposition": ""},
            {"name": "Below", "type": "ai",     "row": 1, "col": 0, "disposition": "pass:right"},
            {"name": "Right", "type": "ai",     "row": 1, "col": 1, "disposition": ""},
        ]
    }
    out = _harness(layout, {"grid": {"cols": 6}})
    assert out == ["M", "Below", "Right", "Loose"]


def test_leftpane_workerlist_below_stops_at_row_gap():
    # Workers not immediately contiguous with a marker must not be pulled into its group.
    # Spec Desk at col=1 owns rows 2-5; waterfall workers at rows 11-12 in the same
    # column must NOT be absorbed even though there is no marker between them.
    layout = {
        "slots": [
            {"name": "Spec Desk", "type": "marker", "row": 1, "col": 1, "disposition": ""},
            {"name": "Near A",    "type": "ai",     "row": 2, "col": 1, "disposition": ""},
            {"name": "Near B",    "type": "ai",     "row": 3, "col": 1, "disposition": ""},
            # gap: rows 4-10 empty in col 1
            {"name": "Far A",     "type": "ai",     "row": 11, "col": 1, "disposition": ""},
            {"name": "Far B",     "type": "ai",     "row": 12, "col": 1, "disposition": ""},
        ]
    }
    out = _harness(layout, {"grid": {"cols": 6}})
    # Near A and Near B belong to Spec Desk; Far A and Far B have no marker so they
    # fall to the loose tail — but they must NOT appear inside Spec Desk's block.
    assert out[:3] == ["Spec Desk", "Near A", "Near B"]
    assert set(out[3:]) == {"Far A", "Far B"}


def test_leftpane_workerlist_below_stops_at_next_marker_in_column():
    layout = {
        "slots": [
            {"name": "M1", "type": "marker", "row": 0, "col": 0, "disposition": ""},
            {"name": "A",  "type": "ai",     "row": 1, "col": 0, "disposition": ""},
            {"name": "M2", "type": "marker", "row": 2, "col": 0, "disposition": ""},
            {"name": "B",  "type": "ai",     "row": 3, "col": 0, "disposition": ""},
        ]
    }
    out = _harness(layout, {"grid": {"cols": 4}})
    # M1's group must not absorb M2 or B; M2 must own B.
    assert out == ["M1", "A", "M2", "B"]
