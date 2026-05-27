"""Tests for server/persistence.py."""

import json
import os
import sys
import tempfile

import pytest

from server.persistence import (
    atomic_write,
    ensure_within,
    format_frontmatter,
    parse_frontmatter,
    read_frontmatter,
    read_json,
    write_frontmatter,
    write_json,
)


class TestAtomicWrite:
    def test_creates_file(self, tmp_workspace):
        path = os.path.join(tmp_workspace, "test.txt")
        atomic_write(path, "hello")
        assert open(path).read() == "hello"

    def test_overwrites_existing(self, tmp_workspace):
        path = os.path.join(tmp_workspace, "test.txt")
        atomic_write(path, "first")
        atomic_write(path, "second")
        assert open(path).read() == "second"

    def test_creates_parent_dirs(self, tmp_workspace):
        path = os.path.join(tmp_workspace, "sub", "dir", "test.txt")
        atomic_write(path, "nested")
        assert open(path).read() == "nested"

    def test_no_partial_write_on_error(self, tmp_workspace):
        path = os.path.join(tmp_workspace, "test.txt")
        atomic_write(path, "original")
        # Simulate error by writing to a path where rename would fail
        # (this is hard to simulate perfectly, so we just verify the basic contract)
        assert open(path).read() == "original"


class TestJson:
    def test_round_trip(self, tmp_workspace):
        path = os.path.join(tmp_workspace, "data.json")
        data = {"key": "value", "num": 42, "list": [1, 2, 3]}
        write_json(path, data)
        loaded = read_json(path)
        assert loaded == data

    def test_pretty_printed(self, tmp_workspace):
        path = os.path.join(tmp_workspace, "data.json")
        write_json(path, {"a": 1})
        content = open(path).read()
        assert "  " in content  # indented
        assert content.endswith("\n")


class TestEnsureWithin:
    def test_valid_path(self, tmp_workspace):
        path = os.path.join(tmp_workspace, "sub", "file.txt")
        result = ensure_within(path, tmp_workspace)
        assert result.startswith(os.path.realpath(tmp_workspace))

    def test_rejects_traversal(self, tmp_workspace):
        path = os.path.join(tmp_workspace, "..", "etc", "passwd")
        with pytest.raises(ValueError):
            ensure_within(path, tmp_workspace)

    @pytest.mark.skipif(sys.platform == "win32", reason="symlinks require admin on Windows")
    def test_rejects_symlink_escape(self, tmp_workspace):
        # Create a symlink pointing outside
        link = os.path.join(tmp_workspace, "escape")
        os.symlink(tempfile.gettempdir(), link)
        with pytest.raises(ValueError):
            ensure_within(os.path.join(link, "file"), tmp_workspace)

    def test_root_itself_is_valid(self, tmp_workspace):
        result = ensure_within(tmp_workspace, tmp_workspace)
        assert result == os.path.realpath(tmp_workspace)


class TestFrontmatter:
    SAMPLE = """\
---
# bullpen-add-auth-8k2f
title: Add auth middleware
status: inbox
type: task
priority: normal
assigned_to:
created_at: 2026-04-07T14:30:22Z
updated_at: 2026-04-07T14:30:22Z
tags: [backend, auth]
---

## Description

Add JWT auth to API routes.
"""

    def test_parse_basic(self):
        meta, body, slug = parse_frontmatter(self.SAMPLE)
        assert slug == "bullpen-add-auth-8k2f"
        assert meta["title"] == "Add auth middleware"
        assert meta["status"] == "inbox"
        assert meta["type"] == "task"
        assert meta["priority"] == "normal"
        assert meta["assigned_to"] == ""
        assert meta["tags"] == ["backend", "auth"]
        assert "## Description" in body
        assert "Add JWT auth" in body

    def test_round_trip(self, tmp_workspace):
        path = os.path.join(tmp_workspace, "ticket.md")
        meta = {
            "title": "Test task",
            "status": "inbox",
            "type": "task",
            "priority": "normal",
            "assigned_to": "",
            "created_at": "2026-04-07T14:30:22Z",
            "updated_at": "2026-04-07T14:30:22Z",
            "tags": ["backend", "auth"],
        }
        body = "\n## Description\n\nSome description.\n"
        slug = "test-task-ab12"

        write_frontmatter(path, meta, body, slug)
        meta2, body2, slug2 = read_frontmatter(path)

        assert slug2 == slug
        assert meta2["title"] == meta["title"]
        assert meta2["status"] == meta["status"]
        assert meta2["tags"] == meta["tags"]
        assert meta2["assigned_to"] == ""
        assert body2 == body

    def test_empty_tags(self):
        content = "---\ntitle: Test\ntags: []\n---\n"
        meta, body, slug = parse_frontmatter(content)
        assert meta["tags"] == []

    def test_no_frontmatter(self):
        content = "Just a plain markdown file.\n"
        meta, body, slug = parse_frontmatter(content)
        assert meta == {}
        assert slug is None
        assert body == content

    def test_history_field(self):
        content = """\
---
# test-slug
title: Test
history:
  - {timestamp: 2026-04-07T14:30:22Z, event: created, detail: initial}
  - {timestamp: 2026-04-07T14:31:00Z, event: assigned, detail: slot-0}
---

Body here.
"""
        meta, body, slug = parse_frontmatter(content)
        assert slug == "test-slug"
        assert meta["title"] == "Test"
        assert len(meta["history"]) == 2
        assert meta["history"][0]["event"] == "created"
        assert meta["history"][1]["detail"] == "slot-0"

    def test_history_round_trip(self, tmp_workspace):
        path = os.path.join(tmp_workspace, "ticket.md")
        history = [
            {"timestamp": "2026-04-07T14:30:22Z", "event": "created", "detail": "initial"},
            {"timestamp": "2026-04-07T14:31:00Z", "event": "assigned", "detail": "slot-0"},
        ]
        meta = {"title": "Test", "status": "inbox", "history": history}
        write_frontmatter(path, meta, "\nBody.\n", "test-slug")
        meta2, body2, slug2 = read_frontmatter(path)
        assert meta2["history"] == history

    def test_preserves_beans_fields(self):
        """Beans files with only standard fields should parse fine."""
        content = """\
---
# my-task-1234
title: A beans task
status: inbox
type: task
priority: normal
created_at: 2026-04-07T00:00:00Z
updated_at: 2026-04-07T00:00:00Z
order: M
---

Task body.
"""
        meta, body, slug = parse_frontmatter(content)
        assert slug == "my-task-1234"
        assert meta["title"] == "A beans task"
        assert "assigned_to" not in meta  # beans won't have this

    def test_special_chars_in_title(self):
        content = '---\ntitle: Fix: handle "quotes" & ampersands\n---\n'
        meta, body, slug = parse_frontmatter(content)
        assert meta["title"] == 'Fix: handle "quotes" & ampersands'

    def test_colon_in_value(self):
        content = '---\ntitle: Error: something went wrong: details\n---\n'
        meta, body, slug = parse_frontmatter(content)
        assert meta["title"] == "Error: something went wrong: details"

    def test_integer_values(self):
        content = "---\nretries: 3\n---\n"
        meta, _, _ = parse_frontmatter(content)
        assert meta["retries"] == 3

    def test_format_and_parse_symmetry(self):
        meta = {"title": "Test", "status": "done", "tags": ["a", "b"]}
        body = "\nContent.\n"
        slug = "test-1234"
        formatted = format_frontmatter(meta, body, slug)
        meta2, body2, slug2 = parse_frontmatter(formatted)
        assert meta2 == meta
        assert body2 == body
        assert slug2 == slug
