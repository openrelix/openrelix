"""ccusage subprocess adapters and cache helpers for token usage."""

import json
import os
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from asset_runtime import atomic_write_json, get_runtime_paths

from .common import current_local_datetime
from .config import CCUSAGE_CACHE_WINDOW_DAYS, CCUSAGE_TIMEZONE, CCUSAGE_WINDOW_DAYS


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


def parse_token_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def resolve_token_date_range(window_days=CCUSAGE_WINDOW_DAYS, now_func=current_local_datetime, start_date=None, end_date=None):
    resolved_end = parse_token_date(end_date) or now_func().date()
    resolved_start = parse_token_date(start_date)
    if not resolved_start:
        resolved_window_days = max(int(window_days or CCUSAGE_WINDOW_DAYS), 1)
        resolved_start = resolved_end - timedelta(days=resolved_window_days - 1)
    if resolved_start > resolved_end:
        resolved_start, resolved_end = resolved_end, resolved_start
    resolved_window_days = max((resolved_end - resolved_start).days + 1, 1)
    return resolved_start, resolved_end, resolved_window_days


def resolve_token_cache_fetch_range(
    window_days=CCUSAGE_WINDOW_DAYS,
    now_func=current_local_datetime,
    start_date=None,
    end_date=None,
    cache_window_days=CCUSAGE_CACHE_WINDOW_DAYS,
):
    requested_start, requested_end, requested_window_days = resolve_token_date_range(
        window_days=window_days,
        now_func=now_func,
        start_date=start_date,
        end_date=end_date,
    )
    cache_days = max(int(cache_window_days or CCUSAGE_CACHE_WINDOW_DAYS), requested_window_days, 1)
    cache_start = min(requested_start, requested_end - timedelta(days=cache_days - 1))
    cache_window_days = max((requested_end - cache_start).days + 1, 1)
    return cache_start, requested_end, cache_window_days


def provider_package(provider):
    provider = normalize_token_provider(provider)
    if provider in {"codex", "claude"}:
        return "ccusage@latest"
    raise ValueError("provider_package expects codex or claude, got {}".format(provider))


def provider_command_args(provider):
    provider = normalize_token_provider(provider)
    if provider == "codex":
        return ["codex", "daily"]
    if provider == "claude":
        return ["claude", "daily"]
    raise ValueError("provider_command_args expects codex or claude, got {}".format(provider))


def provider_display_name(provider):
    provider = normalize_token_provider(provider)
    return {"codex": "Codex", "claude": "Claude Code", "all": "Codex + Claude Code"}.get(provider, provider)


def provider_date_arg(date_value, provider):
    # ccusage 20.0.0 accepts compact dates consistently across Codex and
    # Claude; hyphenated Codex --until values can produce an empty result.
    return date_value.strftime("%Y%m%d")


def normalize_provider_daily_row(row, provider):
    provider = normalize_token_provider(provider)
    row = row or {}
    input_tokens = int(row.get("inputTokens") or 0)
    cache_creation_tokens = int(row.get("cacheCreationTokens") or 0)
    cache_read_tokens = int(row.get("cacheReadTokens") or row.get("cachedInputTokens") or 0)
    total_input_tokens = input_tokens + cache_creation_tokens + cache_read_tokens
    output_tokens = int(row.get("outputTokens") or 0)
    total_tokens = int(row.get("totalTokens") or (total_input_tokens + output_tokens))
    return {
        "date": str(row.get("date") or row.get("period") or ""),
        "provider": provider,
        "providerLabel": provider_display_name(provider),
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


def build_provider_totals_from_rows(rows, provider):
    rows = list(rows or [])
    return {
        "provider": normalize_token_provider(provider),
        "providerLabel": provider_display_name(provider),
        "inputTokens": sum(int(row.get("inputTokens") or 0) for row in rows),
        "cachedInputTokens": sum(int(row.get("cachedInputTokens") or 0) for row in rows),
        "cacheCreationTokens": sum(int(row.get("cacheCreationTokens") or 0) for row in rows),
        "outputTokens": sum(int(row.get("outputTokens") or 0) for row in rows),
        "reasoningOutputTokens": sum(int(row.get("reasoningOutputTokens") or 0) for row in rows),
        "totalTokens": sum(int(row.get("totalTokens") or 0) for row in rows),
        "costUSD": sum(float(row.get("costUSD") or 0) for row in rows),
        "models": {},
        "modelsUsed": [],
        "modelBreakdowns": [],
    }


def filter_provider_payload_by_date(payload, provider, start_date=None, end_date=None):
    provider_payload = ensure_provider_payload(payload, provider)
    start = parse_token_date(start_date)
    end = parse_token_date(end_date)
    filtered_daily = []
    for row in provider_payload.get("daily", []):
        row_date = parse_token_date(row.get("date"))
        if not row_date:
            continue
        if start and row_date < start:
            continue
        if end and row_date > end:
            continue
        filtered_daily.append(row)
    return {
        "daily": filtered_daily,
        "totals": build_provider_totals_from_rows(filtered_daily, provider),
    }


def should_fallback_to_unfiltered_codex_payload(provider, payload):
    return normalize_token_provider(provider) == "codex" and not (payload or {}).get("daily")


def ensure_provider_payload(payload, provider):
    if isinstance(payload, dict):
        daily_rows = payload.get("daily") or []
        if all(isinstance(row, dict) and row.get("provider") for row in daily_rows):
            return payload
    return normalize_provider_payload(payload, provider)


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
        provider_payload = ensure_provider_payload(result.get("payload") or empty_payload(), provider)
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


def token_result_for_provider(result, provider):
    provider = normalize_token_provider(provider)
    if not isinstance(result, dict):
        return None
    result_provider = normalize_token_provider(result.get("provider"))
    if result_provider == provider:
        return dict(result)
    if result_provider == "all" and provider in {"codex", "claude"}:
        provider_result = (result.get("provider_results") or {}).get(provider)
        if isinstance(provider_result, dict) and provider_result.get("available"):
            return dict(provider_result)
    return None


def token_result_covers_request(
    result,
    provider,
    window_days,
    start_date=None,
    end_date=None,
    now_func=current_local_datetime,
):
    candidate = token_result_for_provider(result, provider)
    if not candidate or not candidate.get("available"):
        return False
    requested_start, requested_end, _ = resolve_token_date_range(
        window_days=window_days,
        now_func=now_func,
        start_date=start_date,
        end_date=end_date,
    )
    cached_start = parse_token_date(candidate.get("range_start"))
    cached_end = parse_token_date(candidate.get("range_end"))
    if not cached_start or not cached_end:
        return False
    return cached_start <= requested_start and cached_end >= requested_end


def token_result_with_request_range(
    result,
    window_days=CCUSAGE_WINDOW_DAYS,
    start_date=None,
    end_date=None,
    now_func=current_local_datetime,
):
    if not isinstance(result, dict):
        return result
    requested_start, requested_end, requested_window_days = resolve_token_date_range(
        window_days=window_days,
        now_func=now_func,
        start_date=start_date,
        end_date=end_date,
    )
    scoped = dict(result)
    scoped["range_start"] = requested_start.isoformat()
    scoped["range_end"] = requested_end.isoformat()
    scoped["window_days"] = requested_window_days
    return scoped


def fetch_provider_ccusage_daily(
    provider,
    window_days=CCUSAGE_WINDOW_DAYS,
    now_func=current_local_datetime,
    resolve_npx_binary_func=resolve_npx_binary,
    env_func=build_subprocess_env,
    runner=subprocess.run,
    start_date=None,
    end_date=None,
):
    provider = normalize_token_provider(provider)
    if provider not in {"codex", "claude"}:
        raise ValueError("Unsupported token provider: {}".format(provider))
    start_date, end_date, resolved_window_days = resolve_token_date_range(
        window_days=window_days,
        now_func=now_func,
        start_date=start_date,
        end_date=end_date,
    )
    base_cmd = [
        resolve_npx_binary_func(),
        "-y",
        provider_package(provider),
        *provider_command_args(provider),
        "-j",
    ]
    date_args = [
        "--since",
        provider_date_arg(start_date, provider),
        "--until",
        provider_date_arg(end_date, provider),
    ]
    timezone_args = [
        "--timezone",
        CCUSAGE_TIMEZONE,
    ]

    def run_ccusage_command(command):
        last_command_error = None
        for extra_args in ([], ["--offline"]):
            try:
                result = runner(
                    command + extra_args,
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env_func(),
                    timeout=120,
                )
                return normalize_provider_payload(json.loads(result.stdout), provider), None
            except Exception as exc:  # noqa: BLE001
                last_command_error = str(exc)
        return None, last_command_error

    payload, last_error = run_ccusage_command(base_cmd + date_args + timezone_args)
    if payload is not None:
        fallback = ""
        if should_fallback_to_unfiltered_codex_payload(provider, payload):
            full_payload, _fallback_error = run_ccusage_command(base_cmd + timezone_args)
            if full_payload is not None:
                payload = filter_provider_payload_by_date(full_payload, provider, start_date, end_date)
                fallback = "unfiltered_local_range"
        return {
            "available": True,
            "provider": provider,
            "provider_label": provider_display_name(provider),
            "payload": payload,
            "error": "",
            "fallback": fallback,
            "fetched_at": now_func().isoformat(),
            "window_days": resolved_window_days,
            "range_start": start_date.isoformat(),
            "range_end": end_date.isoformat(),
        }

    return {
        "available": False,
        "provider": provider,
        "provider_label": provider_display_name(provider),
        "payload": empty_payload(),
        "error": last_error or "",
        "fetched_at": now_func().isoformat(),
        "window_days": resolved_window_days,
        "range_start": start_date.isoformat(),
        "range_end": end_date.isoformat(),
    }


def fetch_ccusage_daily(
    window_days=CCUSAGE_WINDOW_DAYS,
    now_func=current_local_datetime,
    resolve_npx_binary_func=resolve_npx_binary,
    env_func=build_subprocess_env,
    runner=subprocess.run,
    provider="all",
    start_date=None,
    end_date=None,
):
    provider = normalize_token_provider(provider)
    resolved_start, resolved_end, resolved_window_days = resolve_token_date_range(
        window_days=window_days,
        now_func=now_func,
        start_date=start_date,
        end_date=end_date,
    )
    if provider in {"codex", "claude"}:
        return fetch_provider_ccusage_daily(
            provider,
            window_days=resolved_window_days,
            now_func=now_func,
            resolve_npx_binary_func=resolve_npx_binary_func,
            env_func=env_func,
            runner=runner,
            start_date=resolved_start,
            end_date=resolved_end,
        )
    if provider != "all":
        return {
            "available": False,
            "provider": provider,
            "provider_label": provider,
            "payload": empty_payload(),
            "error": "Unsupported token provider: {}".format(provider),
            "fetched_at": now_func().isoformat(),
            "window_days": resolved_window_days,
            "range_start": resolved_start.isoformat(),
            "range_end": resolved_end.isoformat(),
        }

    provider_results = {
        name: fetch_provider_ccusage_daily(
            name,
            window_days=resolved_window_days,
            now_func=now_func,
            resolve_npx_binary_func=resolve_npx_binary_func,
            env_func=env_func,
            runner=runner,
            start_date=resolved_start,
            end_date=resolved_end,
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
        "window_days": resolved_window_days,
        "range_start": resolved_start.isoformat(),
        "range_end": resolved_end.isoformat(),
        "partial": bool(available_results and errors),
    }


def token_cache_matches_request(
    cached,
    provider,
    window_days,
    start_date=None,
    end_date=None,
    now_func=current_local_datetime,
):
    if not cached:
        return False
    if token_result_covers_request(
        cached,
        provider,
        window_days,
        start_date=start_date,
        end_date=end_date,
        now_func=now_func,
    ):
        return True
    if normalize_token_provider(cached.get("provider")) != normalize_token_provider(provider):
        return False
    requested_start = parse_token_date(start_date)
    requested_end = parse_token_date(end_date)
    if requested_start or requested_end:
        requested_start, requested_end, _ = resolve_token_date_range(
            window_days=window_days,
            now_func=now_func,
            start_date=start_date,
            end_date=end_date,
        )
        cached_start = parse_token_date(cached.get("range_start"))
        cached_end = parse_token_date(cached.get("range_end"))
        return cached_start == requested_start and cached_end == requested_end
    return int(cached.get("window_days") or 0) == max(int(window_days or CCUSAGE_WINDOW_DAYS), 1)


def resolve_ccusage_daily(
    cache_path=None,
    refresh_requested=None,
    fetch_func=fetch_ccusage_daily,
    provider="all",
    window_days=CCUSAGE_WINDOW_DAYS,
    start_date=None,
    end_date=None,
):
    if refresh_requested is None:
        refresh_requested = os.environ.get("AI_ASSET_REFRESH_TOKEN") == "1"
    cached = load_token_usage_cache(cache_path)
    provider = normalize_token_provider(provider)
    if cached and not refresh_requested and token_cache_matches_request(
        cached,
        provider,
        window_days,
        start_date=start_date,
        end_date=end_date,
    ):
        return token_result_with_request_range(
            token_result_for_provider(cached, provider) or cached,
            window_days=window_days,
            start_date=start_date,
            end_date=end_date,
        )

    fetch_start, fetch_end, fetch_window_days = resolve_token_cache_fetch_range(
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
    )
    live = fetch_func(
        provider=provider,
        window_days=fetch_window_days,
        start_date=fetch_start,
        end_date=fetch_end,
    )
    if live.get("available"):
        write_token_usage_cache(live, cache_path)
        return token_result_with_request_range(
            token_result_for_provider(live, provider) or live,
            window_days=window_days,
            start_date=start_date,
            end_date=end_date,
        )

    if cached and token_cache_matches_request(
        cached,
        provider,
        window_days,
        start_date=start_date,
        end_date=end_date,
    ):
        return token_result_with_request_range(
            token_result_for_provider(cached, provider) or cached,
            window_days=window_days,
            start_date=start_date,
            end_date=end_date,
        )
    return live
