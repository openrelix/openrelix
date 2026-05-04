"""ccusage subprocess adapters and cache helpers for token usage."""

import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from asset_runtime import atomic_write_json, get_runtime_paths

from .common import current_local_datetime
from .config import CCUSAGE_TIMEZONE, CCUSAGE_WINDOW_DAYS


TOKEN_PROVIDER_ALIASES = {
    "all": "all",
    "both": "all",
    "merged": "all",
    "codex": "codex",
    "codex-cli": "codex",
    "codex_cli": "codex",
    "cc": "claude",
    "claude": "claude",
    "claude-code": "claude",
    "claude_code": "claude",
}
SUPPORTED_TOKEN_PROVIDERS = ("all", "codex", "claude")


def resolve_npx_binary():
    candidates = [
        shutil.which("npx"),
        "/opt/homebrew/bin/npx",
        "/usr/local/bin/npx",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return "npx"


def build_subprocess_env():
    env = os.environ.copy()
    base_path = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    current_path = env.get("PATH", "")
    env["PATH"] = "{}:{}".format(base_path, current_path) if current_path else base_path
    return env


def default_token_cache_path(paths=None):
    paths = paths or get_runtime_paths()
    return paths.reports_dir / "token-usage-cache.json"


def load_token_usage_cache(cache_path=None):
    path = cache_path or default_token_cache_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def write_token_usage_cache(payload, cache_path=None):
    atomic_write_json(cache_path or default_token_cache_path(), payload)


def normalize_token_provider(provider=None):
    text = str(provider or "all").strip().lower().replace("_", "-")
    return TOKEN_PROVIDER_ALIASES.get(text, text) if text else "all"


def empty_payload():
    return {"daily": [], "totals": {}}


def provider_package(provider):
    provider = normalize_token_provider(provider)
    if provider == "codex":
        return "@ccusage/codex@latest"
    if provider == "claude":
        return "ccusage@latest"
    raise ValueError("provider_package expects codex or claude, got {}".format(provider))


def provider_display_name(provider):
    provider = normalize_token_provider(provider)
    return {"codex": "Codex", "claude": "Claude Code", "all": "Codex + Claude Code"}.get(provider, provider)


def provider_date_arg(date_value, provider):
    provider = normalize_token_provider(provider)
    if provider == "claude":
        return date_value.strftime("%Y%m%d")
    return date_value.isoformat()


def normalize_provider_daily_row(row, provider):
    provider = normalize_token_provider(provider)
    row = row or {}
    if provider == "claude":
        input_tokens = int(row.get("inputTokens") or 0)
        cache_creation_tokens = int(row.get("cacheCreationTokens") or 0)
        cache_read_tokens = int(row.get("cacheReadTokens") or row.get("cachedInputTokens") or 0)
        total_input_tokens = input_tokens + cache_creation_tokens + cache_read_tokens
        output_tokens = int(row.get("outputTokens") or 0)
        total_tokens = int(row.get("totalTokens") or (total_input_tokens + output_tokens))
        return {
            "date": str(row.get("date") or ""),
            "provider": "claude",
            "providerLabel": provider_display_name("claude"),
            "inputTokens": total_input_tokens,
            "cachedInputTokens": cache_read_tokens,
            "cacheCreationTokens": cache_creation_tokens,
            "outputTokens": output_tokens,
            "reasoningOutputTokens": int(row.get("reasoningOutputTokens") or 0),
            "totalTokens": total_tokens,
            "costUSD": float(row.get("costUSD") or row.get("totalCost") or row.get("cost") or 0),
            "models": row.get("models") or {},
            "modelsUsed": row.get("modelsUsed") or [],
            "modelBreakdowns": row.get("modelBreakdowns") or [],
        }

    return {
        "date": str(row.get("date") or ""),
        "provider": "codex",
        "providerLabel": provider_display_name("codex"),
        "inputTokens": int(row.get("inputTokens") or 0),
        "cachedInputTokens": int(row.get("cachedInputTokens") or 0),
        "cacheCreationTokens": int(row.get("cacheCreationTokens") or 0),
        "outputTokens": int(row.get("outputTokens") or 0),
        "reasoningOutputTokens": int(row.get("reasoningOutputTokens") or 0),
        "totalTokens": int(row.get("totalTokens") or 0),
        "costUSD": float(row.get("costUSD") or row.get("totalCost") or 0),
        "models": row.get("models") or {},
        "modelsUsed": row.get("modelsUsed") or [],
        "modelBreakdowns": row.get("modelBreakdowns") or [],
    }


def normalize_provider_payload(payload, provider):
    if isinstance(payload, list):
        daily_rows = payload
        totals = {}
    elif isinstance(payload, dict):
        daily_rows = payload.get("daily") or []
        totals = payload.get("totals") or {}
    else:
        daily_rows = []
        totals = {}

    normalized_daily = [
        normalize_provider_daily_row(row, provider)
        for row in daily_rows
        if isinstance(row, dict)
    ]
    normalized_totals = normalize_provider_daily_row(totals, provider) if isinstance(totals, dict) else {}
    normalized_totals.pop("date", None)
    return {
        "daily": normalized_daily,
        "totals": normalized_totals,
    }


def date_key(raw_date):
    text = str(raw_date or "").strip()
    if not text:
        return ""
    from datetime import datetime

    for fmt in ("%Y-%m-%d", "%Y%m%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def merge_provider_payloads(provider_results):
    merged = {}
    provider_daily = {}
    for provider, result in provider_results.items():
        if not result.get("available"):
            continue
        provider_payload = normalize_provider_payload(result.get("payload") or empty_payload(), provider)
        provider_daily[provider] = provider_payload.get("daily", [])
        for row in provider_payload.get("daily", []):
            key = date_key(row.get("date"))
            if not key:
                continue
            target = merged.setdefault(
                key,
                {
                    "date": key,
                    "provider": "all",
                    "providerLabel": provider_display_name("all"),
                    "providers": {},
                    "inputTokens": 0,
                    "cachedInputTokens": 0,
                    "cacheCreationTokens": 0,
                    "outputTokens": 0,
                    "reasoningOutputTokens": 0,
                    "totalTokens": 0,
                    "costUSD": 0.0,
                },
            )
            target["providers"][provider] = row
            for field in (
                "inputTokens",
                "cachedInputTokens",
                "cacheCreationTokens",
                "outputTokens",
                "reasoningOutputTokens",
                "totalTokens",
            ):
                target[field] += int(row.get(field) or 0)
            target["costUSD"] += float(row.get("costUSD") or 0)

    daily = [merged[key] for key in sorted(merged)]
    totals = {
        "inputTokens": sum(row.get("inputTokens", 0) for row in daily),
        "cachedInputTokens": sum(row.get("cachedInputTokens", 0) for row in daily),
        "cacheCreationTokens": sum(row.get("cacheCreationTokens", 0) for row in daily),
        "outputTokens": sum(row.get("outputTokens", 0) for row in daily),
        "reasoningOutputTokens": sum(row.get("reasoningOutputTokens", 0) for row in daily),
        "totalTokens": sum(row.get("totalTokens", 0) for row in daily),
        "costUSD": sum(float(row.get("costUSD") or 0) for row in daily),
    }
    return {
        "daily": daily,
        "totals": totals,
        "provider_daily": provider_daily,
    }


def fetch_provider_ccusage_daily(
    provider,
    window_days=CCUSAGE_WINDOW_DAYS,
    now_func=current_local_datetime,
    resolve_npx_binary_func=resolve_npx_binary,
    env_func=build_subprocess_env,
    runner=subprocess.run,
):
    provider = normalize_token_provider(provider)
    if provider not in {"codex", "claude"}:
        raise ValueError("Unsupported token provider: {}".format(provider))
    end_date = now_func().date()
    start_date = end_date - timedelta(days=window_days - 1)
    base_cmd = [
        resolve_npx_binary_func(),
        "-y",
        provider_package(provider),
        "daily",
        "-j",
        "--since",
        provider_date_arg(start_date, provider),
        "--until",
        provider_date_arg(end_date, provider),
        "--timezone",
        CCUSAGE_TIMEZONE,
    ]

    attempts = [[], ["--offline"]]
    last_error = None
    for extra_args in attempts:
        try:
            result = runner(
                base_cmd + extra_args,
                capture_output=True,
                text=True,
                check=True,
                env=env_func(),
                timeout=120,
            )
            payload = json.loads(result.stdout)
            return {
                "available": True,
                "provider": provider,
                "provider_label": provider_display_name(provider),
                "payload": normalize_provider_payload(payload, provider),
                "error": "",
                "fetched_at": now_func().isoformat(),
                "window_days": window_days,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)

    return {
        "available": False,
        "provider": provider,
        "provider_label": provider_display_name(provider),
        "payload": empty_payload(),
        "error": last_error or "",
        "fetched_at": now_func().isoformat(),
        "window_days": window_days,
    }


def fetch_ccusage_daily(
    window_days=CCUSAGE_WINDOW_DAYS,
    now_func=current_local_datetime,
    resolve_npx_binary_func=resolve_npx_binary,
    env_func=build_subprocess_env,
    runner=subprocess.run,
    provider="all",
):
    provider = normalize_token_provider(provider)
    if provider in {"codex", "claude"}:
        return fetch_provider_ccusage_daily(
            provider,
            window_days=window_days,
            now_func=now_func,
            resolve_npx_binary_func=resolve_npx_binary_func,
            env_func=env_func,
            runner=runner,
        )
    if provider != "all":
        return {
            "available": False,
            "provider": provider,
            "provider_label": provider,
            "payload": empty_payload(),
            "error": "Unsupported token provider: {}".format(provider),
            "fetched_at": now_func().isoformat(),
            "window_days": window_days,
        }

    provider_results = {
        name: fetch_provider_ccusage_daily(
            name,
            window_days=window_days,
            now_func=now_func,
            resolve_npx_binary_func=resolve_npx_binary_func,
            env_func=env_func,
            runner=runner,
        )
        for name in ("codex", "claude")
    }
    available_results = [result for result in provider_results.values() if result.get("available")]
    errors = [
        "{}: {}".format(result.get("provider_label") or name, result.get("error", ""))
        for name, result in provider_results.items()
        if not result.get("available") and result.get("error")
    ]
    return {
        "available": bool(available_results),
        "provider": "all",
        "provider_label": provider_display_name("all"),
        "provider_results": provider_results,
        "payload": merge_provider_payloads(provider_results) if available_results else empty_payload(),
        "error": "; ".join(errors),
        "fetched_at": now_func().isoformat(),
        "window_days": window_days,
        "partial": bool(available_results and errors),
    }


def resolve_ccusage_daily(
    cache_path=None,
    refresh_requested=None,
    fetch_func=fetch_ccusage_daily,
    provider="all",
):
    if refresh_requested is None:
        refresh_requested = os.environ.get("AI_ASSET_REFRESH_TOKEN") == "1"
    cached = load_token_usage_cache(cache_path)
    provider = normalize_token_provider(provider)
    if cached and not refresh_requested and normalize_token_provider(cached.get("provider")) == provider:
        return cached

    live = fetch_func(provider=provider)
    if live.get("available"):
        write_token_usage_cache(live, cache_path)
        return live

    if cached:
        return cached
    return live
