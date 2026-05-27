"""Usage and task-time accounting helpers for ticket metadata."""

from datetime import datetime, timezone


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)

TASK_TIME_FIELD = "task_time_ms"
ACTIVE_TASK_TIME_FIELD = "active_task_started_at"

_TOKEN_ALIASES = {
    "input_tokens": ("input_tokens", "prompt_tokens"),
    "cached_input_tokens": (
        "cached_input_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ),
    "output_tokens": ("output_tokens", "completion_tokens"),
    "reasoning_output_tokens": ("reasoning_output_tokens", "reasoning_tokens"),
    "total_tokens": ("total_tokens",),
}

_USAGE_WRAPPER_KEYS = (
    "usage",
    "token_count",
    "info",
    "stats",
    "total_token_usage",
    "last_token_usage",
    "token_usage",
    "tokens",
    "totals",
    "total",
    "last",
)


def _coerce_non_negative_int(value):
    """Convert input to non-negative int or return None."""
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


def _parse_iso8601_utc(value):
    """Parse a Bullpen UTC timestamp string."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def normalize_usage(raw_usage):
    """Normalize provider usage payloads into canonical token fields."""
    if not isinstance(raw_usage, dict):
        return {}

    normalized = {}
    for canonical, aliases in _TOKEN_ALIASES.items():
        total = 0
        seen = False
        for key in aliases:
            if key not in raw_usage:
                continue
            n = _coerce_non_negative_int(raw_usage.get(key))
            if n is None:
                continue
            total += n
            seen = True
        if seen:
            normalized[canonical] = total

    return normalized


def merge_usage_dicts(base, extra):
    """Merge two normalized usage dicts by summing known token fields."""
    out = {}
    for field in TOKEN_FIELDS:
        value = 0
        seen = False
        for src in (base, extra):
            if isinstance(src, dict) and field in src:
                n = _coerce_non_negative_int(src.get(field))
                if n is not None:
                    value += n
                    seen = True
        if seen:
            out[field] = value
    return out


def merge_usage_max(base, extra):
    """Merge two normalized usage dicts by taking per-field max values."""
    out = {}
    for field in TOKEN_FIELDS:
        best = None
        for src in (base, extra):
            if isinstance(src, dict) and field in src:
                n = _coerce_non_negative_int(src.get(field))
                if n is not None:
                    best = n if best is None else max(best, n)
        if best is not None:
            out[field] = best
    return out


def _iter_usage_payload_candidates(value, max_depth=3, _seen=None):
    """Yield nested dict payload candidates that may contain usage fields."""
    if max_depth < 0 or not isinstance(value, dict):
        return

    if _seen is None:
        _seen = set()
    marker = id(value)
    if marker in _seen:
        return
    _seen.add(marker)

    yield value

    for key in _USAGE_WRAPPER_KEYS:
        nested = value.get(key)
        if isinstance(nested, dict):
            yield from _iter_usage_payload_candidates(nested, max_depth=max_depth - 1, _seen=_seen)


def usage_to_legacy_tokens(usage):
    """Return backward-compatible token total for the legacy `tokens` scalar."""
    if not isinstance(usage, dict):
        return 0
    total = _coerce_non_negative_int(usage.get("total_tokens"))
    if total is not None:
        return total

    inp = _coerce_non_negative_int(usage.get("input_tokens")) or 0
    out = _coerce_non_negative_int(usage.get("output_tokens")) or 0
    if inp or out:
        return inp + out
    return 0


def task_time_ms_value(task):
    """Return persisted accumulated task time in milliseconds."""
    if not isinstance(task, dict):
        return 0
    return _coerce_non_negative_int(task.get(TASK_TIME_FIELD)) or 0


def _timestamp_range_ms(items):
    """Return the span in ms across timestamped dict items."""
    if not isinstance(items, list):
        return 0

    stamps = []
    for item in items:
        if not isinstance(item, dict):
            continue
        parsed = _parse_iso8601_utc(item.get("timestamp"))
        if parsed is not None:
            stamps.append(parsed)

    if len(stamps) < 2:
        return 0
    delta_ms = int((max(stamps) - min(stamps)).total_seconds() * 1000)
    return max(delta_ms, 0)


def reported_task_time_ms_value(task):
    """Return persisted task time, or a best-effort estimate for legacy tasks."""
    persisted = task_time_ms_value(task)
    if persisted > 0:
        return persisted
    if not isinstance(task, dict):
        return 0
    usage_span = _timestamp_range_ms(task.get("usage"))
    if usage_span > 0:
        return usage_span
    history_span = _timestamp_range_ms(task.get("history"))
    if history_span > 0:
        return history_span
    return 0


def elapsed_task_time_ms(started_at, ended_at=None):
    """Return elapsed milliseconds between a stored start time and end time."""
    started = _parse_iso8601_utc(started_at)
    if started is None:
        return 0
    ended_dt = ended_at or datetime.now(timezone.utc)
    if isinstance(ended_dt, str):
        ended_dt = _parse_iso8601_utc(ended_dt)
    if ended_dt is None:
        return 0
    delta_ms = int((ended_dt - started).total_seconds() * 1000)
    return max(delta_ms, 0)


def _bucket_provider_model(provider, model):
    """Return normalized provider/model bucket key."""
    p = provider.strip() if isinstance(provider, str) else ""
    m = model.strip() if isinstance(model, str) else ""
    return (p or "unknown", m)


def aggregate_tokens_by_provider_model(usage_entries):
    """Aggregate usage entries into provider/model token totals."""
    if not isinstance(usage_entries, list):
        return []

    buckets = {}
    for item in usage_entries:
        if not isinstance(item, dict):
            continue

        provider, model = _bucket_provider_model(item.get("provider"), item.get("model"))
        key = (provider, model)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = {"provider": provider}
            if model:
                bucket["model"] = model

        merged = merge_usage_dicts(bucket, normalize_usage(item))
        for field, value in merged.items():
            bucket[field] = value
        bucket["tokens"] = usage_to_legacy_tokens(bucket)
        buckets[key] = bucket

    ordered = []
    for provider, model in sorted(buckets.keys()):
        ordered.append(buckets[(provider, model)])
    return ordered


def extract_codex_usage_event(event_obj):
    """Extract normalized usage from a Codex JSON event object."""
    if not isinstance(event_obj, dict):
        return {}

    evt_type = event_obj.get("type")
    if evt_type == "turn.completed":
        return normalize_usage(event_obj.get("usage", {}))

    if evt_type == "token_count":
        usage = {}
        for candidate in _iter_usage_payload_candidates(event_obj):
            normalized = normalize_usage(candidate)
            if normalized:
                # token_count events are snapshots; take max per field across
                # alternate wrappers to avoid double-counting the same event.
                usage = merge_usage_max(usage, normalized)
        return usage

    # item.completed events fire after each tool use / command execution and
    # may carry per-item usage, giving us mid-execution token updates even
    # when periodic token_count events are absent.
    if evt_type == "item.completed":
        item = event_obj.get("item", {})
        usage = normalize_usage(item.get("usage", {}))
        if not usage:
            usage = normalize_usage(event_obj.get("usage", {}))
        return usage

    return {}


def extract_gemini_usage_event(event_obj):
    """Extract normalized usage from Gemini stream/result payloads."""
    if not isinstance(event_obj, dict):
        return {}

    usage = {}

    # JSONL result-style events.
    if event_obj.get("type") == "result":
        usage = merge_usage_dicts(usage, normalize_usage(event_obj.get("usage", {})))

    # Newer Gemini payloads include nested stats by model.
    stats = event_obj.get("stats")
    if isinstance(stats, dict):
        models = stats.get("models")
        saw_model_usage = False
        if isinstance(models, dict):
            for model_bucket in models.values():
                if not isinstance(model_bucket, dict):
                    continue
                tokens = model_bucket.get("tokens") if isinstance(model_bucket.get("tokens"), dict) else model_bucket
                model_usage = _normalize_gemini_tokens(tokens)
                if model_usage:
                    saw_model_usage = True
                    usage = merge_usage_dicts(usage, model_usage)
        # Some payload variants may provide top-level stats.tokens directly.
        if isinstance(stats.get("tokens"), dict):
            usage = merge_usage_dicts(usage, _normalize_gemini_tokens(stats.get("tokens")))
        elif not saw_model_usage:
            usage = merge_usage_dicts(usage, _normalize_gemini_tokens(stats))

    return usage


def _normalize_gemini_tokens(raw_tokens):
    """Normalize Gemini token stats object into canonical fields."""
    if not isinstance(raw_tokens, dict):
        return {}

    # Gemini often reports both "input" and "prompt"; prefer prompt when present.
    input_tokens = _coerce_non_negative_int(raw_tokens.get("prompt"))
    if input_tokens is None:
        input_tokens = _coerce_non_negative_int(raw_tokens.get("input"))
    if input_tokens is None:
        input_tokens = _coerce_non_negative_int(raw_tokens.get("input_tokens"))

    total_tokens = _coerce_non_negative_int(raw_tokens.get("total"))
    if total_tokens is None:
        total_tokens = _coerce_non_negative_int(raw_tokens.get("total_tokens"))
    cached_input_tokens = _coerce_non_negative_int(raw_tokens.get("cached"))
    reasoning_output_tokens = _coerce_non_negative_int(raw_tokens.get("thoughts"))
    candidate_tokens = _coerce_non_negative_int(raw_tokens.get("candidates"))
    tool_tokens = _coerce_non_negative_int(raw_tokens.get("tool"))

    output_tokens = _coerce_non_negative_int(raw_tokens.get("output_tokens"))
    if output_tokens is not None:
        pass
    elif total_tokens is not None and input_tokens is not None:
        output_tokens = max(total_tokens - input_tokens, 0)
    else:
        parts = [
            candidate_tokens or 0,
            reasoning_output_tokens or 0,
            tool_tokens or 0,
        ]
        summed = sum(parts)
        if summed:
            output_tokens = summed

    out = {}
    if input_tokens is not None:
        out["input_tokens"] = input_tokens
    if cached_input_tokens is not None:
        out["cached_input_tokens"] = cached_input_tokens
    if output_tokens is not None:
        out["output_tokens"] = output_tokens
    if reasoning_output_tokens is not None:
        out["reasoning_output_tokens"] = reasoning_output_tokens
    if total_tokens is not None:
        out["total_tokens"] = total_tokens
    return out


def extract_stream_usage_event(provider, event_obj):
    """Extract normalized usage from a live stream event for any provider."""
    if provider == "codex":
        return extract_codex_usage_event(event_obj)
    if provider == "gemini":
        return extract_gemini_usage_event(event_obj)

    if not isinstance(event_obj, dict):
        return {}

    evt_type = event_obj.get("type")

    if evt_type == "result":
        return normalize_usage(event_obj.get("usage", {}))

    # Claude stream-json emits "assistant" events with message.usage after
    # each turn, giving us live token updates throughout execution.
    if provider == "claude" and evt_type == "assistant":
        msg = event_obj.get("message", {})
        return normalize_usage(msg.get("usage", {}))

    return {}


def build_usage_entry(source, provider, model=None, slot=None, usage=None, occurred_at=None):
    """Build one structured per-run ticket usage entry."""
    normalized = normalize_usage(usage)
    if not normalized:
        return None

    entry = {
        "timestamp": occurred_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "provider": provider,
    }
    if model:
        entry["model"] = model
    if slot is not None:
        slot_int = _coerce_non_negative_int(slot)
        if slot_int is not None:
            entry["slot"] = slot_int

    entry.update(normalized)
    return entry


def build_usage_update(task, entry):
    """Create task update payload that appends usage entry and updates tokens."""
    if not isinstance(task, dict) or not isinstance(entry, dict):
        return {}

    existing_usage = task.get("usage")
    if isinstance(existing_usage, list):
        usage_entries = list(existing_usage)
    else:
        usage_entries = []
    usage_entries.append(entry)
    tokens_by_provider_model = aggregate_tokens_by_provider_model(usage_entries)

    prev_tokens = _coerce_non_negative_int(task.get("tokens")) or 0
    return {
        "usage": usage_entries,
        "tokens_by_provider_model": tokens_by_provider_model,
        "tokens": prev_tokens + usage_to_legacy_tokens(entry),
    }


_UNCHANGED = object()


def build_task_time_update(task, elapsed_ms=0, *, active_started_at=_UNCHANGED):
    """Create a task update payload that accumulates active work time."""
    if not isinstance(task, dict):
        return {}

    elapsed = _coerce_non_negative_int(elapsed_ms) or 0
    update = {
        TASK_TIME_FIELD: task_time_ms_value(task) + elapsed,
    }
    if active_started_at is not _UNCHANGED:
        update[ACTIVE_TASK_TIME_FIELD] = active_started_at or ""
    return update
