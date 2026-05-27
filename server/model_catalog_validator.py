"""Validate provider model candidates through Bullpen's adapter path."""

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from server.agents import get_adapter
from server.model_aliases import normalize_model


DEFAULT_PROBE_PROMPT = "Reply with exactly: OK"
OUTPUT_PREVIEW_CHARS = 2000

PROVIDER_MODEL_CANDIDATES = {
    "claude": [
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-5-20250514",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5-20250514",
        "claude-haiku-4-5-20251001",
    ],
    "codex": [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.3-codex",
        "gpt-5.2",
    ],
    "gemini": [
        "flash",
        "flash-lite",
        "gemini-3-flash-preview",
    ],
}


def known_providers():
    """Return providers with built-in probe candidates."""
    return sorted(PROVIDER_MODEL_CANDIDATES)


def candidate_models(provider):
    """Return built-in model candidates for a provider."""
    return list(PROVIDER_MODEL_CANDIDATES.get(provider, []))


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _preview(text, limit=OUTPUT_PREVIEW_CHARS):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[-limit:]


def classify_model_error(provider, text):
    """Classify common provider/model failures for catalog reports."""
    haystack = (text or "").lower()
    if not haystack:
        return None
    if "timed out" in haystack or "timeout" in haystack:
        return "timeout"
    if (
        "requested entity was not found" in haystack
        or "modelnotfound" in haystack
        or "model_not_found" in haystack
        or "model not found" in haystack
        or "not found for api version" in haystack
        or "404" in haystack and "model" in haystack
    ):
        return "not_found"
    if (
        "permission_denied" in haystack
        or "permission denied" in haystack
        or "project has been denied access" in haystack
        or "403" in haystack and "forbidden" in haystack
    ):
        return "permission_denied"
    if (
        "authentication" in haystack
        or "unauthorized" in haystack
        or "api key" in haystack
        or "log in" in haystack
        or "login" in haystack
        or "oauth" in haystack
    ):
        return "auth"
    if (
        "quota" in haystack
        or "rate limit" in haystack
        or "rate_limit" in haystack
        or "resource exhausted" in haystack
        or "capacity" in haystack
        or "too many requests" in haystack
        or "429" in haystack
    ):
        return "quota"
    if "no such file" in haystack or "not found" in haystack:
        return "unavailable"
    if provider == "gemini" and "api error" in haystack:
        return "provider_error"
    return "unknown"


def fetch_provider_api_catalog(provider, timeout_seconds=10):
    """Fetch provider API model catalog when credentials are available.

    This intentionally reports skip/error states instead of raising. The API
    catalog is advisory because Bullpen may call a CLI surface with different
    auth and model routing.
    """
    provider = (provider or "").lower()
    if provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return {"status": "skipped", "reason": "GEMINI_API_KEY is not set", "models": []}
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        req = urllib.request.Request(url)
        parser = _parse_gemini_models
    elif provider == "codex":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return {"status": "skipped", "reason": "OPENAI_API_KEY is not set", "models": []}
        req = urllib.request.Request(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        parser = _parse_openai_models
    elif provider == "claude":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return {"status": "skipped", "reason": "ANTHROPIC_API_KEY is not set", "models": []}
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
        parser = _parse_anthropic_models
    else:
        return {"status": "unsupported", "reason": f"No API catalog fetcher for {provider}", "models": []}

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read().decode("utf-8")
        models = sorted(parser(json.loads(body)))
        return {"status": "ok", "reason": None, "models": models}
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        return {"status": "error", "reason": _safe_error_message(e), "models": []}


def _parse_gemini_models(data):
    models = set()
    for model in data.get("models", []):
        name = model.get("name")
        if isinstance(name, str) and name.startswith("models/"):
            models.add(name.split("/", 1)[1])
        base = model.get("baseModelId")
        if isinstance(base, str) and base:
            models.add(base)
    return models


def _parse_openai_models(data):
    return {
        model.get("id")
        for model in data.get("data", [])
        if isinstance(model, dict) and isinstance(model.get("id"), str)
    }


def _parse_anthropic_models(data):
    return {
        model.get("id")
        for model in data.get("data", [])
        if isinstance(model, dict) and isinstance(model.get("id"), str)
    }


def _safe_error_message(err):
    message = str(err)
    for key_name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        key = os.environ.get(key_name)
        if key:
            message = message.replace(key, "<redacted>")
    return message


def validate_model_catalog(
    providers=None,
    models=None,
    workspace=None,
    bp_dir=None,
    timeout_seconds=45,
    prompt=DEFAULT_PROBE_PROMPT,
    include_api_catalog=False,
):
    """Validate provider models through the adapter execution path."""
    providers = [p.lower() for p in (providers or known_providers())]
    workspace = os.path.abspath(workspace or os.getcwd())
    report = {
        "generated_at": _utc_now(),
        "workspace": workspace,
        "prompt": prompt,
        "timeout_seconds": timeout_seconds,
        "include_api_catalog": bool(include_api_catalog),
        "providers": [],
    }
    for provider in providers:
        provider_models = list(models or candidate_models(provider))
        api_catalog = None
        api_model_set = None
        if include_api_catalog:
            api_catalog = fetch_provider_api_catalog(provider)
            api_model_set = set(api_catalog.get("models") or [])
        provider_report = {
            "provider": provider,
            "api_catalog": api_catalog,
            "models": [],
        }
        for model in provider_models:
            result = validate_model_candidate(
                provider,
                model,
                workspace=workspace,
                bp_dir=bp_dir,
                timeout_seconds=timeout_seconds,
                prompt=prompt,
            )
            if api_model_set is not None:
                result["listed"] = result["model"] in api_model_set or result["normalized_model"] in api_model_set
            else:
                result["listed"] = None
            provider_report["models"].append(result)
        report["providers"].append(provider_report)
    return report


def validate_model_candidate(provider, model, workspace, bp_dir=None, timeout_seconds=45, prompt=DEFAULT_PROBE_PROMPT):
    """Run one candidate through the provider adapter and return a report row."""
    started_at = time.monotonic()
    provider = (provider or "").lower()
    normalized_model = normalize_model(provider, model)
    result = {
        "provider": provider,
        "model": model,
        "normalized_model": normalized_model,
        "adapter_available": False,
        "accepted": False,
        "responded": False,
        "success": False,
        "returncode": None,
        "latency_ms": None,
        "error_class": None,
        "error": None,
        "output_preview": "",
        "stdout_preview": "",
        "stderr_preview": "",
    }

    adapter = get_adapter(provider)
    if not adapter:
        result.update({"error_class": "unavailable", "error": f"Unknown provider: {provider}"})
        result["latency_ms"] = _elapsed_ms(started_at)
        return result

    if not adapter.available():
        result.update({"error_class": "unavailable", "error": adapter.unavailable_message()})
        result["latency_ms"] = _elapsed_ms(started_at)
        return result
    result["adapter_available"] = True

    cleanup_path = None
    env = None
    proc = None
    mcp_config_path = None
    try:
        argv = adapter.build_argv(prompt, normalized_model, workspace, bp_dir=bp_dir)
        mcp_config_path = _extract_mcp_config_path(argv)
        prepared_env = adapter.prepare_env(workspace, bp_dir=bp_dir)
        if isinstance(prepared_env, tuple):
            env, cleanup_path = prepared_env
        else:
            env = prepared_env
        proc = subprocess.Popen(
            argv,
            cwd=workspace,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        result["accepted"] = True
        stdin_text = prompt if adapter.prompt_via_stdin() else None
        try:
            stdout, stderr = proc.communicate(stdin_text, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            stdout = _decode_timeout_stream(e.stdout)
            stderr = _decode_timeout_stream(e.stderr)
            try:
                killed_stdout, killed_stderr = proc.communicate(timeout=5)
                stdout = killed_stdout if killed_stdout is not None else stdout
                stderr = killed_stderr if killed_stderr is not None else stderr
            except subprocess.TimeoutExpired as kill_err:
                stdout = stdout or _decode_timeout_stream(kill_err.stdout)
                stderr = stderr or _decode_timeout_stream(kill_err.stderr)
            result.update({
                "returncode": proc.returncode,
                "error_class": "timeout",
                "error": f"Probe timed out after {timeout_seconds} seconds",
                "stdout_preview": _preview(stdout),
                "stderr_preview": _preview(stderr),
            })
            return result

        result["returncode"] = proc.returncode
        result["stdout_preview"] = _preview(stdout)
        result["stderr_preview"] = _preview(stderr)
        parsed = adapter.parse_output(stdout, stderr, proc.returncode)
        output = (parsed.get("output") or "").strip()
        error = (parsed.get("error") or "").strip()
        result["output_preview"] = _preview(output)
        result["responded"] = bool(output)
        result["success"] = bool(parsed.get("success"))
        if not result["success"]:
            combined = "\n".join([error, output, stderr or "", stdout or ""])
            result["error"] = error or f"Exit code {proc.returncode}"
            result["error_class"] = classify_model_error(provider, combined)
    except OSError as e:
        result.update({"error_class": "unavailable", "error": _safe_error_message(e)})
    except Exception as e:
        result.update({"error_class": "unknown", "error": _safe_error_message(e)})
    finally:
        result["latency_ms"] = _elapsed_ms(started_at)
        if cleanup_path:
            try:
                adapter.finalize_env(env, cleanup_path)
            finally:
                shutil.rmtree(cleanup_path, ignore_errors=True)
        if mcp_config_path:
            try:
                os.unlink(mcp_config_path)
            except OSError:
                pass
    return result


def _extract_mcp_config_path(argv):
    for i, arg in enumerate(argv or []):
        if arg == "--mcp-config" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _decode_timeout_stream(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _elapsed_ms(started_at):
    return int(round((time.monotonic() - started_at) * 1000))


def text_summary(report):
    """Render a compact human-readable summary for terminal use."""
    lines = [
        f"Model catalog validation ({report.get('generated_at')})",
        f"Workspace: {report.get('workspace')}",
    ]
    for provider_report in report.get("providers", []):
        provider = provider_report.get("provider")
        lines.append("")
        lines.append(f"{provider}:")
        api_catalog = provider_report.get("api_catalog")
        if api_catalog:
            lines.append(f"  API catalog: {api_catalog.get('status')} ({api_catalog.get('reason') or 'ok'})")
        for row in provider_report.get("models", []):
            status = "ok" if row.get("success") else row.get("error_class") or "failed"
            listed = row.get("listed")
            listed_text = "unknown" if listed is None else str(bool(listed)).lower()
            normalized = row.get("normalized_model")
            suffix = ""
            if normalized and normalized != row.get("model"):
                suffix = f" -> {normalized}"
            lines.append(
                f"  {status:12} listed={listed_text:7} "
                f"{row.get('model')}{suffix} ({row.get('latency_ms')} ms)"
            )
    return "\n".join(lines)
