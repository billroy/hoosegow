"""File I/O: atomic writes, frontmatter, JSON."""

import json
import os
import tempfile
from pathlib import Path


def atomic_write(path, content):
    """Write content to path atomically via temp file + rename."""
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_path, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path):
    """Read and parse a JSON file."""
    with open(path, "r") as f:
        return json.load(f)


def write_json(path, data):
    """Write data as JSON atomically."""
    atomic_write(path, json.dumps(data, indent=2) + "\n")


def ensure_within(path, root):
    """Ensure resolved path is within root. Raises ValueError on traversal."""
    real_path = Path(os.path.realpath(path))
    real_root = Path(os.path.realpath(root))
    if real_path != real_root and not real_path.is_relative_to(real_root):
        raise ValueError(f"Path {path} escapes root {root}")
    return str(real_path)


def _parse_value(raw):
    """Parse a frontmatter value string into a Python object."""
    val = raw.strip()
    if val == "":
        return ""
    # Array: [item1, item2]
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_parse_item(x) for x in _split_array(inner)]
    return _parse_scalar(val)


def _parse_scalar(val):
    """Parse a scalar value."""
    if val == "true":
        return True
    if val == "false":
        return False
    if val == "null" or val == "~":
        return None
    # Try int
    try:
        return int(val)
    except ValueError:
        pass
    # Try float
    try:
        return float(val)
    except ValueError:
        pass
    return val


def _parse_item(raw):
    """Parse a single array item, handling nested objects."""
    val = raw.strip()
    if val.startswith("{") and val.endswith("}"):
        return _parse_inline_object(val)
    return _parse_scalar(val)


def _parse_inline_object(val):
    """Parse {key: val, key: val} inline object."""
    inner = val[1:-1].strip()
    obj = {}
    for part in _split_array(inner):
        part = part.strip()
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        obj[k.strip()] = _parse_scalar(v.strip())
    return obj


def _split_array(s):
    """Split a string by commas, respecting {} nesting."""
    parts = []
    depth = 0
    current = []
    for ch in s:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _serialize_value(val):
    """Serialize a Python value back to frontmatter string."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        if not val:
            return "[]"
        items = [_serialize_item(x) for x in val]
        return "[" + ", ".join(items) + "]"
    return str(val)


def _serialize_item(val):
    """Serialize a single array item."""
    if isinstance(val, dict):
        parts = [f"{k}: {_serialize_value(v)}" for k, v in val.items()]
        return "{" + ", ".join(parts) + "}"
    return _serialize_value(val)


def read_frontmatter(path):
    """Parse a frontmatter file. Returns (meta_dict, body_string, slug)."""
    with open(path, "r") as f:
        content = f.read()
    return parse_frontmatter(content)


def parse_frontmatter(content):
    """Parse frontmatter content string. Returns (meta_dict, body_string, slug)."""
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, content, None

    meta = {}
    slug = None
    body_start = len(lines)
    last_key = None

    # Find closing ---
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            body_start = i + 1
            break
        # Slug comment
        if line.startswith("# "):
            slug = line[2:].strip()
            continue
        # Array continuation line: "  - {key: val}" or "  - value"
        if line.startswith("  - ") and last_key is not None:
            item_str = line.strip()[2:].strip()  # strip "- " prefix
            # Convert to list on first continuation line if needed
            if not isinstance(meta.get(last_key), list):
                meta[last_key] = []
            meta[last_key].append(_parse_item(item_str))
            continue
        # Key: value
        if ":" in line and not line.startswith(" "):
            key, val = line.split(":", 1)
            key = key.strip()
            meta[key] = _parse_value(val)
            last_key = key

    body = "\n".join(lines[body_start:])
    return meta, body, slug


def write_frontmatter(path, meta, body, slug=None):
    """Write a frontmatter file atomically."""
    content = format_frontmatter(meta, body, slug)
    atomic_write(path, content)


def format_frontmatter(meta, body, slug=None):
    """Format frontmatter content string."""
    lines = ["---"]
    if slug:
        lines.append(f"# {slug}")

    for key, val in meta.items():
        if key == "history" and isinstance(val, list):
            lines.append(f"{key}:")
            for item in val:
                lines.append(f"  - {_serialize_item(item)}")
        else:
            lines.append(f"{key}: {_serialize_value(val)}")

    lines.append("---")
    if body:
        lines.append(body)
    else:
        lines.append("")
    return "\n".join(lines)
