"""Regression checks for ticket-detail commit diff click-through."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_task_detail_linkifies_commit_hashes_in_agent_output():
    text = _read("static/components/TaskDetailPanel.js")
    assert "open-commit-diff" in text
    assert "parsedAgentOutputLines" in text
    assert "Commit:\\s*" in text
    assert "detail-output-commit" in text
    assert "openCommitDiff(line.commitHash)" in text


def test_task_detail_commit_clickthrough_is_wired_to_root_app():
    text = _read("static/app.js")
    assert "const requestedCommitDiffHash = ref('');" in text
    assert "openCommitDiffFromTicket(hash)" in text
    assert "activeTab.value = 'commits';" in text
    assert "@open-commit-diff=\"openCommitDiffFromTicket\"" in text
