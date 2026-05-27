"""Task ticket CRUD."""

import os
import re
import secrets
import string
from datetime import datetime, timezone

from server.persistence import (
    ensure_within,
    read_frontmatter,
    write_frontmatter,
)
from server.usage import reported_task_time_ms_value

BASE62 = string.digits + string.ascii_uppercase + string.ascii_lowercase


def _random_suffix(length=4):
    """Generate random base62 suffix."""
    return "".join(secrets.choice(BASE62) for _ in range(length))


def slugify(title):
    """Convert title to URL-safe slug."""
    s = title.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s)
    s = s.strip("-")
    return s[:60] if s else "task"


def generate_slug(title):
    """Generate a unique slug: slugified-title-XXXX."""
    return f"{slugify(title)}-{_random_suffix()}"


# --- CRUD ---

def _tasks_dir(bp_dir):
    return os.path.join(bp_dir, "tasks")


def _archive_dir(bp_dir):
    return os.path.join(_tasks_dir(bp_dir), "archive")


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_task(bp_dir, title, description="", task_type="task", priority="normal", tags=None, status="inbox"):
    """Create a new task ticket. Returns the task dict."""
    slug = generate_slug(title)
    now = _now_iso()

    meta = {
        "title": title,
        "status": status,
        "type": task_type,
        "priority": priority,
        "assigned_to": "",
        "created_at": now,
        "updated_at": now,
        "tags": tags or [],
    }

    body = ""
    if description:
        body = f"\n## Description\n\n{description}\n"

    path = os.path.join(_tasks_dir(bp_dir), f"{slug}.md")
    write_frontmatter(path, meta, body, slug)

    return _with_reported_task_time({**meta, "id": slug, "body": body})


def _with_reported_task_time(task):
    """Attach a non-persisted display/reporting task time field."""
    enriched = dict(task or {})
    enriched["reported_task_time_ms"] = reported_task_time_ms_value(enriched)
    return enriched


def read_task(bp_dir, task_id):
    """Read a single task by ID. Returns task dict or None."""
    path = os.path.join(_tasks_dir(bp_dir), f"{task_id}.md")
    if not os.path.exists(path):
        return None
    ensure_within(path, _tasks_dir(bp_dir))
    meta, body, slug = read_frontmatter(path)
    return _with_reported_task_time({**meta, "id": slug or task_id, "body": body})


def update_task(bp_dir, task_id, fields):
    """Update task fields. Returns updated task dict."""
    path = os.path.join(_tasks_dir(bp_dir), f"{task_id}.md")
    ensure_within(path, _tasks_dir(bp_dir))
    meta, body, slug = read_frontmatter(path)

    # Update body if provided
    if "body" in fields:
        body = fields.pop("body")

    # Merge fields
    for k, v in fields.items():
        if k != "id":  # don't store id in frontmatter
            meta[k] = v

    meta["updated_at"] = _now_iso()
    write_frontmatter(path, meta, body, slug)

    return _with_reported_task_time({**meta, "id": slug or task_id, "body": body})


def delete_task(bp_dir, task_id):
    """Delete a task by ID."""
    path = os.path.join(_tasks_dir(bp_dir), f"{task_id}.md")
    ensure_within(path, _tasks_dir(bp_dir))
    if os.path.exists(path):
        os.remove(path)


def archive_task(bp_dir, task_id):
    """Move a task to the archive subdirectory."""
    tasks_dir = _tasks_dir(bp_dir)
    archive_dir = os.path.join(tasks_dir, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    src = os.path.join(tasks_dir, f"{task_id}.md")
    ensure_within(src, tasks_dir)
    dst = os.path.join(archive_dir, f"{task_id}.md")
    if os.path.exists(src):
        os.replace(src, dst)


def archive_done_tasks(bp_dir):
    """Archive all tasks with status 'done'. Returns list of archived IDs."""
    tasks = list_tasks(bp_dir)
    archived = []
    for t in tasks:
        if t.get("status") == "done":
            archive_task(bp_dir, t["id"])
            archived.append(t["id"])
    return archived


def clear_task_output(bp_dir, task_id):
    """Remove content under ## Agent Output heading."""
    path = os.path.join(_tasks_dir(bp_dir), f"{task_id}.md")
    ensure_within(path, _tasks_dir(bp_dir))
    meta, body, slug = read_frontmatter(path)

    # Remove everything from ## Agent Output onward
    marker = "## Agent Output"
    idx = body.find(marker)
    if idx >= 0:
        body = body[:idx].rstrip() + "\n"

    meta["updated_at"] = _now_iso()
    write_frontmatter(path, meta, body, slug)

    return _with_reported_task_time({**meta, "id": slug or task_id, "body": body})


def _read_tasks_from_dir(path):
    tasks = []
    if not os.path.isdir(path):
        return tasks
    for fname in os.listdir(path):
        if fname.endswith(".md"):
            fpath = os.path.join(path, fname)
            meta, body, slug = read_frontmatter(fpath)
            tasks.append(_with_reported_task_time({**meta, "id": slug or fname[:-3], "body": body}))
    return tasks


PRIORITY_WEIGHT = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


def task_sort_key(task):
    """Priority-first task ordering: highest priority, then eldest, then id."""
    task = task or {}
    return (
        PRIORITY_WEIGHT.get(task.get("priority", "normal"), PRIORITY_WEIGHT["normal"]),
        task.get("created_at", ""),
        task.get("id", ""),
    )


def _sort_tasks(tasks):
    tasks = list(tasks or [])
    tasks.sort(key=task_sort_key)
    return tasks


def sort_task_ids(bp_dir, task_ids):
    """Sort task ids by their current ticket priority ordering.

    Missing tickets are left at the end in their existing relative order so
    callers that repair stale queues can still encounter and remove them.
    """
    indexed = list(enumerate(task_ids or []))
    task_cache = {}

    def _id_key(item):
        idx, task_id = item
        if task_id not in task_cache:
            task_cache[task_id] = read_task(bp_dir, task_id)
        task = task_cache[task_id]
        if not task:
            return (1, idx)
        return (0, *task_sort_key(task))

    return [task_id for _idx, task_id in sorted(indexed, key=_id_key)]


def list_tasks(bp_dir, archived=False):
    """List tasks, sorted by priority then creation time.

    Args:
        archived: When True, list only archived tasks.
    """
    tasks_dir = _archive_dir(bp_dir) if archived else _tasks_dir(bp_dir)
    return _sort_tasks(_read_tasks_from_dir(tasks_dir))
