"""Tests for commit and diff API endpoints."""

import subprocess

from server.app import create_app


def _git(ws, *args):
    return subprocess.run(
        ["git", *args],
        cwd=ws,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(ws):
    _git(ws, "init")
    _git(ws, "config", "user.name", "Test User")
    _git(ws, "config", "user.email", "test@example.com")
    with open(f"{ws}/sample.txt", "w", encoding="utf-8") as f:
        f.write("line one\n")
    _git(ws, "add", "sample.txt")
    _git(ws, "commit", "-m", "initial")
    with open(f"{ws}/sample.txt", "a", encoding="utf-8") as f:
        f.write("line two\n")
    _git(ws, "add", "sample.txt")
    _git(ws, "commit", "-m", "second")


def test_commit_diff_returns_patch(tmp_workspace):
    _init_repo(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()

    commit_hash = _git(tmp_workspace, "rev-parse", "HEAD").stdout.strip()
    res = client.get(f"/api/commits/{commit_hash}/diff")
    assert res.status_code == 200
    body = res.get_json()
    assert body["hash"] == commit_hash
    assert "diff --git" in body["diff"]
    assert "+line two" in body["diff"]


def test_commit_diff_rejects_invalid_hash(tmp_workspace):
    _init_repo(tmp_workspace)
    app = create_app(tmp_workspace, no_browser=True)
    client = app.test_client()

    res = client.get("/api/commits/not-a-hash/diff")
    assert res.status_code == 400
    assert res.get_json()["error"] == "Invalid commit hash"
