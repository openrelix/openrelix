"""ccusage subprocess adapter and cache helpers for token usage."""

import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from asset_runtime import atomic_write_json, get_runtime_paths

from .common import current_local_datetime
from .config import CCUSAGE_TIMEZONE, CCUSAGE_WINDOW_DAYS


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


def fetch_ccusage_daily(
    window_days=CCUSAGE_WINDOW_DAYS,
    now_func=current_local_datetime,
    resolve_npx_binary_func=resolve_npx_binary,
    env_func=build_subprocess_env,
    runner=subprocess.run,
):
    end_date = now_func().date()
    start_date = end_date - timedelta(days=window_days - 1)
    base_cmd = [
        resolve_npx_binary_func(),
        "-y",
        "@ccusage/codex@latest",
        "daily",
        "-j",
        "--since",
        start_date.isoformat(),
        "--until",
        end_date.isoformat(),
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
                "payload": payload,
                "error": "",
                "fetched_at": now_func().isoformat(),
                "window_days": window_days,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)

    return {
        "available": False,
        "payload": {"daily": [], "totals": {}},
        "error": last_error or "",
        "fetched_at": now_func().isoformat(),
        "window_days": window_days,
    }


def resolve_ccusage_daily(
    cache_path=None,
    refresh_requested=None,
    fetch_func=fetch_ccusage_daily,
):
    if refresh_requested is None:
        refresh_requested = os.environ.get("AI_ASSET_REFRESH_TOKEN") == "1"
    cached = load_token_usage_cache(cache_path)
    if cached and not refresh_requested:
        return cached

    live = fetch_func()
    if live.get("available"):
        write_token_usage_cache(live, cache_path)
        return live

    if cached:
        return cached
    return live

