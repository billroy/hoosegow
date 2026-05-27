"""Git worktree helpers for per-run ephemeral checkouts."""

import os
import shutil
import subprocess


def branch_name_for_task(task_id):
    """Return the canonical branch name for a ticket."""
    return f"bullpen/{task_id}"


def worktree_path(bp_dir, task_id):
    """Return the canonical worktree path for a ticket."""
    return os.path.join(bp_dir, "worktrees", task_id)


def _git_ok(workspace):
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _branch_exists(workspace, branch_name):
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def setup_worktree(workspace, bp_dir, task_id):
    """Create a fresh worktree for a run.

    If the branch already exists, attach a fresh worktree to that branch.
    Otherwise create the branch during `git worktree add`.
    """
    if not _git_ok(workspace):
        raise RuntimeError("Workspace is not a git repository")

    path = worktree_path(bp_dir, task_id)
    branch_name = branch_name_for_task(task_id)

    os.makedirs(os.path.dirname(path), exist_ok=True)

    if os.path.lexists(path):
        raise RuntimeError(f"worktree path already exists: {path}")

    if _branch_exists(workspace, branch_name):
        argv = ["git", "worktree", "add", path, branch_name]
    else:
        argv = ["git", "worktree", "add", path, "-b", branch_name]

    result = subprocess.run(argv, cwd=workspace, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")

    return {
        "branch_name": branch_name,
        "path": path,
    }


def remove_worktree(workspace, bp_dir, task_id, *, worktree_dir=None):
    """Remove a run worktree without touching the branch lifecycle."""
    if not _git_ok(workspace):
        raise RuntimeError("Workspace is not a git repository")

    path = os.path.abspath(worktree_dir or worktree_path(bp_dir, task_id))
    if not os.path.lexists(path):
        # Best-effort prune in case git still has a stale registry entry.
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        return

    result = subprocess.run(
        ["git", "worktree", "remove", "--force", path],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git worktree remove failed: {result.stderr.strip()}")

    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )


def reconcile_worktrees(workspace, bp_dir):
    """Clean up stale worktree debris for Bullpen-managed paths.

    Returns a list of note strings describing what was cleaned or detected.
    """
    notes = []
    root = os.path.join(bp_dir, "worktrees")
    if not os.path.isdir(root):
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        return notes

    if _git_ok(workspace):
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )

    active = set()
    if _git_ok(workspace):
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("worktree "):
                    active.add(os.path.realpath(line[len("worktree "):].strip()))

    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        real = os.path.realpath(path)
        if real in active:
            continue
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=False)
            notes.append(f"Removed stale worktree directory: {path}")
        else:
            os.unlink(path)
            notes.append(f"Removed stale worktree path: {path}")

    return notes
