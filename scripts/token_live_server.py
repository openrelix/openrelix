#!/usr/bin/env python3

import json
import os
import plistlib
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from asset_runtime import atomic_write_json, ensure_state_layout, get_project_version, get_runtime_language, get_runtime_paths
from openrelix_overview.common import current_local_datetime
from openrelix_overview.claude_desktop import (
    CLAUDE_DESKTOP_OPEN_PATH,
    start_claude_desktop_resume,
)
from openrelix_overview.codex_desktop import (
    CODEX_DESKTOP_OPEN_PATH,
    start_codex_desktop_resume,
)
from openrelix_overview.config import (
    CCUSAGE_CACHE_WINDOW_DAYS,
    CCUSAGE_WINDOW_DAYS,
    LIVE_TOKEN_ENDPOINT,
    LIVE_TOKEN_HOST,
    LIVE_TOKEN_PORT,
)
from openrelix_overview.finder import FINDER_REVEAL_PATH, reveal_path_in_finder
from openrelix_overview.memory_feedback import append_memory_feedback
from openrelix_overview.pipeline_status import load_status as load_pipeline_status
from openrelix_overview.token_fetcher import (
    fetch_ccusage_daily,
    normalize_token_provider,
    parse_token_date,
    resolve_token_cache_fetch_range,
    resolve_token_date_range,
    token_result_covers_request,
    token_result_for_provider,
    write_token_usage_cache,
)
from openrelix_overview.token_usage import build_token_usage_view, normalize_token_group_by
from openrelix_overview.update_secret import read_or_create_update_token


PATHS = get_runtime_paths()
LANGUAGE = get_runtime_language(PATHS)
RUNTIME_DIR = PATHS.runtime_dir
SERVICE_VERSION = get_project_version(PATHS.repo_root, fallback="")
SERVICE_SCRIPT_PATH = os.path.realpath(__file__)
CACHE_PATH = RUNTIME_DIR / "token-live-cache.json"
REPORT_TOKEN_CACHE_PATH = PATHS.reports_dir / "token-usage-cache.json"
CACHE_TTL_SECONDS = 90
CACHE_REUSE_SECONDS = 30 * 24 * 60 * 60
FETCH_LOCK = threading.Lock()
CACHE_REFRESH_LOCK = threading.Lock()
CACHE_REFRESH_IN_FLIGHT = set()
CACHE_WRITE_LOCK = threading.Lock()
REFRESH_LOCK = threading.RLock()

OPENRELIX_CLI = PATHS.repo_root / "scripts" / "openrelix.py"
UPDATE_WORKER_SCRIPT = PATHS.repo_root / "scripts" / "openrelix_update_worker.py"
UPDATE_STATUS_PATH = RUNTIME_DIR / "update-status.json"
UPDATE_TIMEOUT_SECONDS = 600
UPDATE_LOG_TAIL_LINES = 12
PANEL_REFRESH_PATH = "/run-refresh"
PANEL_REFRESH_TIMEOUT_SECONDS = 180
PANEL_REFRESH_LOG_TAIL_LINES = 20
MEMORY_FEEDBACK_PATH = "/memory-feedback"
KNOWLEDGE_LARK_DOC_PATH = "/knowledge-lark-doc"
MEMORY_FEEDBACK_REFRESH_TIMEOUT_SECONDS = 120
MEMORY_FEEDBACK_REFRESH_LOCK = threading.RLock()
MEMORY_FEEDBACK_REFRESH_STATE = {
    "status": "idle",
    "started_at": 0,
    "ended_at": 0,
    "exit_code": None,
    "error": "",
}
UPDATE_LOCK = threading.RLock()
UPDATE_STATE = {
    "status": "idle",
    "started_at": 0,
    "ended_at": 0,
    "exit_code": None,
    "error": "",
    "log_tail": "",
}

# Shared persistent secret with the panel template.
# Loaded lazily so plain imports don't touch the filesystem.
_UPDATE_TOKEN_CACHE = None
_UPDATE_TOKEN_LOCK = threading.Lock()
ALLOWED_PANEL_ORIGIN_PREFIXES = ("file://",)
ALLOWED_PANEL_ORIGIN_EXACT = {"null"}
TRUSTED_POST_PATHS = {
    "/run-update",
    PANEL_REFRESH_PATH,
    CODEX_DESKTOP_OPEN_PATH,
    MEMORY_FEEDBACK_PATH,
    KNOWLEDGE_LARK_DOC_PATH,
    CLAUDE_DESKTOP_OPEN_PATH,
    FINDER_REVEAL_PATH,
}


def get_update_token():
    global _UPDATE_TOKEN_CACHE
    if _UPDATE_TOKEN_CACHE is not None:
        return _UPDATE_TOKEN_CACHE
    with _UPDATE_TOKEN_LOCK:
        if _UPDATE_TOKEN_CACHE is None:
            _UPDATE_TOKEN_CACHE = read_or_create_update_token(paths=PATHS)
    return _UPDATE_TOKEN_CACHE


def is_allowed_panel_origin(origin):
    if not origin:
        return False
    if origin in ALLOWED_PANEL_ORIGIN_EXACT:
        return True
    return any(origin.startswith(prefix) for prefix in ALLOWED_PANEL_ORIGIN_PREFIXES)


def update_state_snapshot():
    persisted = read_update_status()
    if persisted:
        return persisted
    with UPDATE_LOCK:
        return dict(UPDATE_STATE)


def _read_jsonl(path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _find_knowledge_doc(doc_id):
    target = str(doc_id or "").strip()
    if not target:
        return None
    for row in _read_jsonl(PATHS.registry_dir / "knowledge_docs.jsonl"):
        if str(row.get("doc_id") or "") == target:
            return row
    return None


def _first_url(value):
    if isinstance(value, dict):
        for key in ("url", "doc_url", "document_url", "docs_url", "web_url"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                return candidate
        for item in value.values():
            found = _first_url(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _first_url(item)
            if found:
                return found
    return ""


def create_lark_doc_from_knowledge(doc_id):
    doc = _find_knowledge_doc(doc_id)
    if not doc:
        return {"ok": False, "error": "knowledge_doc_not_found"}
    if str(doc.get("status") or "") not in {"reviewed", "published"}:
        return {"ok": False, "error": "knowledge_doc_not_reviewed"}
    body_path = PATHS.state_root / str(doc.get("body_path") or "")
    if not body_path.exists() or not body_path.is_file():
        return {"ok": False, "error": "knowledge_doc_body_not_found", "path": str(body_path)}
    lark_bin = shutil.which("lark-cli") or shutil.which("lark")
    if not lark_bin:
        return {"ok": False, "error": "lark_cli_not_found"}
    title = str(doc.get("title") or doc.get("doc_id") or "OpenRelix Knowledge Doc").strip()
    command_variants = [
        [
            lark_bin,
            "--json",
            "docs",
            "create",
            "--api-version",
            "v2",
            "--doc-format",
            "markdown",
            "--title",
            title,
            "--content",
            "@{}".format(body_path),
        ],
        [
            lark_bin,
            "--json",
            "docs",
            "+create",
            "--api-version",
            "v2",
            "--doc-format",
            "markdown",
            "--title",
            title,
            "--content",
            "@{}".format(body_path),
        ],
    ]
    last_error = ""
    for cmd in command_variants:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "lark_cli_timeout"}
        if result.returncode != 0:
            last_error = (result.stderr or result.stdout or "lark-cli failed")[-1200:]
            continue
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            payload = {"stdout": result.stdout.strip()}
        return {
            "ok": True,
            "doc_id": doc.get("doc_id"),
            "title": title,
            "url": _first_url(payload),
            "payload": payload,
        }
    return {"ok": False, "error": "lark_cli_failed", "detail": last_error}


def process_is_alive(pid):
    try:
        pid_value = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_value <= 0:
        return False
    try:
        os.kill(pid_value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def normalize_update_state(payload):
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status") or "")
    if status == "running":
        pid = payload.get("pid")
        try:
            started_at = float(payload.get("started_at") or 0)
        except (TypeError, ValueError):
            started_at = 0
        # Give a just-spawned worker a small grace period before treating a
        # missing PID as stale; this avoids a race while launchd restarts us.
        if pid and not process_is_alive(pid) and time.time() - started_at > 5:
            stale = dict(payload)
            stale.update({
                "status": "failed",
                "ended_at": time.time(),
                "error": "update_worker_exited",
            })
            write_update_status(stale)
            return stale
    return payload


def read_update_status():
    try:
        payload = json.loads(UPDATE_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return normalize_update_state(payload)


def write_update_status(payload):
    state = dict(payload)
    state["updated_at"] = time.time()
    atomic_write_json(UPDATE_STATUS_PATH, state)
    with UPDATE_LOCK:
        UPDATE_STATE.update(state)


def build_update_worker_command():
    return [
        sys.executable,
        str(UPDATE_WORKER_SCRIPT),
        "--repo-root",
        str(PATHS.repo_root),
        "--status-file",
        str(UPDATE_STATUS_PATH),
        "--state-dir",
        str(PATHS.state_root),
        "--codex-home",
        str(PATHS.codex_home),
        "--python-bin",
        sys.executable,
    ]


def tail_text(text, max_lines=PANEL_REFRESH_LOG_TAIL_LINES):
    lines = str(text or "").splitlines()
    if max_lines > 0 and len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)


def build_panel_refresh_command(target_date=None, asset_layer_only=True):
    command = [
        "/bin/zsh",
        str(PATHS.repo_root / "scripts" / "refresh_overview.sh"),
    ]
    if asset_layer_only:
        command.append("--asset-layer-only")
    if target_date:
        command.extend(["--date", str(target_date)])
    return command


def read_overview_refresh_env():
    plist_path = PATHS.launch_agents_dir / "io.github.openrelix.overview-refresh.plist"
    try:
        with plist_path.open("rb") as handle:
            payload = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}
    env = payload.get("EnvironmentVariables")
    return env if isinstance(env, dict) else {}


def run_panel_refresh(target_date=None):
    target_date = str(target_date or current_local_datetime().date().isoformat())
    started_at = time.time()
    refresh_script = PATHS.repo_root / "scripts" / "refresh_overview.sh"
    if not refresh_script.exists():
        return {
            "ok": False,
            "status": "failed",
            "target_date": target_date,
            "started_at": started_at,
            "ended_at": time.time(),
            "exit_code": None,
            "error": "refresh_script_not_found",
            "script": str(refresh_script),
        }

    env = os.environ.copy()
    env["AI_ASSET_STATE_DIR"] = str(PATHS.state_root)
    env["CODEX_HOME"] = str(PATHS.codex_home)
    env["OPENRELIX_REFRESH_DATE"] = target_date
    env["OPENRELIX_ENABLE_NATIVE_DISPLAY_POLISH"] = "0"
    try:
        completed = subprocess.run(
            build_panel_refresh_command(target_date, asset_layer_only=True),
            cwd=str(PATHS.repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=PANEL_REFRESH_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "status": "failed",
            "target_date": target_date,
            "started_at": started_at,
            "ended_at": time.time(),
            "exit_code": None,
            "error": "refresh_timeout",
            "timeout_seconds": PANEL_REFRESH_TIMEOUT_SECONDS,
            "stdout_tail": tail_text(getattr(exc, "stdout", "")),
            "stderr_tail": tail_text(getattr(exc, "stderr", "")),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "failed",
            "target_date": target_date,
            "started_at": started_at,
            "ended_at": time.time(),
            "exit_code": None,
            "error": str(exc),
        }

    ok = completed.returncode == 0
    return {
        "ok": ok,
        "status": "completed" if ok else "failed",
        "target_date": target_date,
        "started_at": started_at,
        "ended_at": time.time(),
        "exit_code": completed.returncode,
        "error": "" if ok else "refresh_failed",
        "panel_path": str(PATHS.reports_dir / "panel.html"),
        "overview_data_path": str(PATHS.reports_dir / "overview-data.json"),
        "asset_stats_path": str(PATHS.reports_dir / "asset-stats-latest.json"),
        "stdout_tail": tail_text(completed.stdout),
        "stderr_tail": tail_text(completed.stderr),
    }


def run_memory_feedback_refresh():
    started_at = time.time()
    env = os.environ.copy()
    env["AI_ASSET_STATE_DIR"] = str(PATHS.state_root)
    env["CODEX_HOME"] = str(PATHS.codex_home)
    commands = [
        [sys.executable, str(PATHS.repo_root / "scripts" / "sync_host_memory_summary.py")],
        [sys.executable, str(PATHS.repo_root / "scripts" / "build_overview.py")],
    ]
    stdout_parts = []
    stderr_parts = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=str(PATHS.repo_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=MEMORY_FEEDBACK_REFRESH_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "status": "failed",
                "started_at": started_at,
                "ended_at": time.time(),
                "exit_code": None,
                "error": "memory_feedback_refresh_timeout",
                "stdout_tail": tail_text(getattr(exc, "stdout", "")),
                "stderr_tail": tail_text(getattr(exc, "stderr", "")),
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "failed",
                "started_at": started_at,
                "ended_at": time.time(),
                "exit_code": None,
                "error": str(exc),
            }
        stdout_parts.append(completed.stdout)
        stderr_parts.append(completed.stderr)
        if completed.returncode != 0:
            return {
                "ok": False,
                "status": "failed",
                "started_at": started_at,
                "ended_at": time.time(),
                "exit_code": completed.returncode,
                "error": "memory_feedback_refresh_failed",
                "command": command[-1],
                "stdout_tail": tail_text("\n".join(stdout_parts)),
                "stderr_tail": tail_text("\n".join(stderr_parts)),
            }
    return {
        "ok": True,
        "status": "completed",
        "started_at": started_at,
        "ended_at": time.time(),
        "exit_code": 0,
        "panel_path": str(PATHS.reports_dir / "panel.html"),
        "memory_summary_path": str(PATHS.codex_home / "memories" / "memory_summary.md"),
        "stdout_tail": tail_text("\n".join(stdout_parts)),
        "stderr_tail": tail_text("\n".join(stderr_parts)),
    }


def memory_feedback_refresh_snapshot():
    with MEMORY_FEEDBACK_REFRESH_LOCK:
        return dict(MEMORY_FEEDBACK_REFRESH_STATE)


def start_memory_feedback_refresh_async():
    with MEMORY_FEEDBACK_REFRESH_LOCK:
        if MEMORY_FEEDBACK_REFRESH_STATE.get("status") == "running":
            return False, dict(MEMORY_FEEDBACK_REFRESH_STATE)
        snapshot = {
            "ok": True,
            "status": "running",
            "started_at": time.time(),
            "ended_at": 0,
            "exit_code": None,
            "error": "",
        }
        MEMORY_FEEDBACK_REFRESH_STATE.update(snapshot)

    def worker():
        try:
            result = run_memory_feedback_refresh()
        except Exception as exc:
            result = {
                "ok": False,
                "status": "failed",
                "ended_at": time.time(),
                "exit_code": None,
                "error": str(exc),
            }
        with MEMORY_FEEDBACK_REFRESH_LOCK:
            MEMORY_FEEDBACK_REFRESH_STATE.update(result)
            MEMORY_FEEDBACK_REFRESH_STATE["updated_at"] = time.time()

    try:
        thread = threading.Thread(
            target=worker,
            name="openrelix-memory-feedback-refresh",
            daemon=True,
        )
        thread.start()
    except Exception as exc:
        failed = dict(snapshot)
        failed.update(
            {
                "ok": False,
                "status": "failed",
                "ended_at": time.time(),
                "error": str(exc),
            }
        )
        with MEMORY_FEEDBACK_REFRESH_LOCK:
            MEMORY_FEEDBACK_REFRESH_STATE.update(failed)
        return False, failed
    return True, snapshot


def memory_feedback_accepted_payload(feedback, refresh_snapshot, refresh_started):
    return {
        "ok": True,
        "status": "accepted",
        "feedback": feedback,
        "refresh": refresh_snapshot,
        "refresh_started_now": refresh_started,
    }


def start_manual_pipeline_refresh(target_date=None):
    target_date = str(target_date or current_local_datetime().date().isoformat())
    current = load_pipeline_status(PATHS)
    if current.get("status") == "running":
        snapshot = dict(current)
        snapshot["ok"] = False
        snapshot["started_now"] = False
        snapshot["error"] = "pipeline_already_running"
        return False, snapshot

    refresh_script = PATHS.repo_root / "scripts" / "refresh_overview.sh"
    if not refresh_script.exists():
        return False, {
            "ok": False,
            "status": "failed",
            "started_now": False,
            "target_date": target_date,
            "error": "refresh_script_not_found",
        }

    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in read_overview_refresh_env().items()})
    env["AI_ASSET_STATE_DIR"] = str(PATHS.state_root)
    env["CODEX_HOME"] = str(PATHS.codex_home)
    env["OPENRELIX_REFRESH_DATE"] = target_date
    try:
        with REFRESH_LOCK:
            proc = subprocess.Popen(
                build_panel_refresh_command(target_date, asset_layer_only=False),
                cwd=str(PATHS.repo_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
    except Exception as exc:
        return False, {
            "ok": False,
            "status": "failed",
            "started_now": False,
            "target_date": target_date,
            "error": str(exc),
        }

    snapshot = load_pipeline_status(PATHS)
    snapshot.update({
        "ok": True,
        "status": "running",
        "started_now": True,
        "target_date": target_date,
        "pid": proc.pid,
    })
    return True, snapshot


def start_update_async():
    with UPDATE_LOCK:
        current = read_update_status()
        if current and current.get("status") == "running":
            return False, current
        snapshot = {
            "status": "running",
            "started_at": time.time(),
            "ended_at": 0,
            "exit_code": None,
            "error": "",
            "log_tail": "",
            "phase": "queued",
        }
        write_update_status(snapshot)
    try:
        proc = subprocess.Popen(
            build_update_worker_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except Exception as exc:
        failed = dict(snapshot)
        failed.update({
            "status": "failed",
            "ended_at": time.time(),
            "error": str(exc),
        })
        write_update_status(failed)
        return False, failed

    snapshot["pid"] = proc.pid
    snapshot["phase"] = "installing"
    write_update_status(snapshot)
    return True, snapshot


def load_cache():
    payload = None
    if not CACHE_PATH.exists():
        payload = None
    else:
        try:
            payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
    report_entry = load_report_token_cache_entry()
    if report_entry:
        return merge_cache_entries(payload, [report_entry])
    return payload


def cached_ccusage_result(payload):
    if not isinstance(payload, dict):
        return None
    result = payload.get("ccusage_result")
    return result if isinstance(result, dict) else None


def cache_entry_key(payload):
    ccusage_result = cached_ccusage_result(payload) or {}
    provider = normalize_token_provider(ccusage_result.get("provider") or payload.get("provider"))
    range_start = str(ccusage_result.get("range_start") or payload.get("range_start") or "")
    range_end = str(ccusage_result.get("range_end") or payload.get("range_end") or "")
    return "|".join([provider, range_start, range_end])


def merge_cache_entries(payload, extra_entries=()):
    entries = {
        cache_entry_key(entry): entry
        for entry in cache_entries(payload)
        if cache_entry_key(entry)
    }
    for entry in extra_entries:
        key = cache_entry_key(entry)
        if not key:
            continue
        existing = entries.get(key)
        if not existing or float(entry.get("_cached_at_epoch") or 0) >= float(existing.get("_cached_at_epoch") or 0):
            entries[key] = entry
    if not entries:
        return payload
    updated_at_epoch = max(float(entry.get("_cached_at_epoch") or 0) for entry in entries.values())
    return {
        "version": 2,
        "updated_at_epoch": updated_at_epoch,
        "entries": entries,
    }


def cache_entries(payload):
    if not isinstance(payload, dict):
        return []
    entries = payload.get("entries")
    if isinstance(entries, dict):
        return [entry for entry in entries.values() if isinstance(entry, dict)]
    if cached_ccusage_result(payload):
        return [payload]
    return []


def load_report_token_cache_entry(cache_path=None):
    path = cache_path or REPORT_TOKEN_CACHE_PATH
    try:
        ccusage_result = json.loads(path.read_text(encoding="utf-8"))
        cached_at_epoch = path.stat().st_mtime
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(ccusage_result, dict) or not ccusage_result.get("available"):
        return None
    provider = normalize_token_provider(ccusage_result.get("provider"))
    return {
        "ok": True,
        "stale": False,
        "error": ccusage_result.get("error", ""),
        "window_days": ccusage_result.get("window_days", CCUSAGE_WINDOW_DAYS),
        "provider": provider,
        "group_by": "day",
        "range_start": ccusage_result.get("range_start", ""),
        "range_end": ccusage_result.get("range_end", ""),
        "served_from_cache": True,
        "ccusage_result": ccusage_result,
        "_cached_at_epoch": cached_at_epoch,
    }


def write_cache(payload):
    if not isinstance(payload, dict):
        return
    with CACHE_WRITE_LOCK:
        existing_entries = {
            cache_entry_key(entry): entry
            for entry in cache_entries(load_cache())
            if cache_entry_key(entry)
        }
        key = cache_entry_key(payload)
        if key:
            existing_entries[key] = payload
        atomic_write_json(
            CACHE_PATH,
            {
                "version": 2,
                "updated_at_epoch": time.time(),
                "entries": existing_entries,
            },
        )


def cache_age_seconds(payload):
    if not isinstance(payload, dict):
        return None
    try:
        cached_at_epoch = float(payload.get("_cached_at_epoch") or 0)
    except (TypeError, ValueError):
        cached_at_epoch = 0
    if cached_at_epoch <= 0:
        return None
    return max(time.time() - cached_at_epoch, 0)


def build_token_payload_from_result(
    ccusage_result,
    window_days,
    provider,
    start_date=None,
    end_date=None,
    group_by="day",
    served_from_cache=False,
    stale=False,
    cached_at_epoch=None,
):
    provider = normalize_token_provider(provider)
    group_by = normalize_token_group_by(group_by)
    provider_result = token_result_for_provider(ccusage_result, provider) or ccusage_result
    token_usage = build_token_usage_view(
        provider_result,
        language=LANGUAGE,
        group_by=group_by,
        start_date=start_date or None,
        end_date=end_date or None,
    )
    payload = {
        "ok": bool(token_usage.get("available")),
        "stale": bool(stale),
        "error": token_usage.get("error", ""),
        "window_days": window_days,
        "provider": provider,
        "group_by": group_by,
        "range_start": token_usage.get("range_start", start_date),
        "range_end": token_usage.get("range_end", end_date),
        "served_from_cache": bool(served_from_cache),
        "token_usage": token_usage,
        "ccusage_result": ccusage_result,
        "_cached_at_epoch": cached_at_epoch if cached_at_epoch is not None else time.time(),
    }
    return payload


def build_payload_from_cache_entry(payload, window_days, provider, start_date=None, end_date=None, group_by="day"):
    ccusage_result = cached_ccusage_result(payload)
    if not token_result_covers_request(
        ccusage_result,
        provider,
        window_days,
        start_date=start_date,
        end_date=end_date,
    ):
        return None
    age = cache_age_seconds(payload)
    if age is None or age > CACHE_REUSE_SECONDS:
        return None
    return build_token_payload_from_result(
        ccusage_result,
        window_days,
        provider,
        start_date=start_date,
        end_date=end_date,
        group_by=group_by,
        served_from_cache=True,
        stale=age >= CACHE_TTL_SECONDS,
        cached_at_epoch=payload.get("_cached_at_epoch"),
    )


def build_payload_from_cache(payload, window_days, provider, start_date=None, end_date=None, group_by="day"):
    matches = [
        candidate
        for candidate in (
            build_payload_from_cache_entry(
                entry,
                window_days,
                provider,
                start_date=start_date,
                end_date=end_date,
                group_by=group_by,
            )
            for entry in cache_entries(payload)
        )
        if candidate
    ]
    if not matches:
        return None

    def has_token_records(item):
        token_usage = item.get("token_usage") or {}
        if token_usage.get("active_period_count"):
            return True
        if token_usage.get("period_total_tokens") or token_usage.get("today_total_tokens"):
            return True
        return any((row.get("value") or row.get("totalTokens") or 0) for row in token_usage.get("daily_rows") or [])

    def token_time(item):
        token_usage = item.get("token_usage") or {}
        return str(token_usage.get("refreshed_at") or "")

    matches.sort(
        key=lambda item: (
            1 if has_token_records(item) else 0,
            0 if item.get("stale") else 1,
            token_time(item),
            float(item.get("_cached_at_epoch") or 0),
        ),
        reverse=True,
    )
    selected = matches[0]
    today_matches = [
        item
        for item in matches
        if has_token_records(item) and (item.get("token_usage") or {}).get("today_total_tokens")
    ]
    if today_matches:
        latest_today = sorted(
            today_matches,
            key=lambda item: (token_time(item), float(item.get("_cached_at_epoch") or 0)),
            reverse=True,
        )[0]
        latest_usage = latest_today.get("token_usage") or {}
        selected_usage = selected.get("token_usage") or {}
        selected_usage["today_refreshed_at"] = latest_usage.get("refreshed_at") or selected_usage.get("refreshed_at", "")
        selected_usage["today_refreshed_at_display"] = latest_usage.get("refreshed_at_display") or selected_usage.get(
            "refreshed_at_display",
            "",
        )
        selected["token_usage"] = selected_usage
    return selected


def token_cache_refresh_key(window_days, provider, start_date=None, end_date=None):
    fetch_start, fetch_end, _ = resolve_token_cache_fetch_range(
        window_days=window_days,
        start_date=start_date or None,
        end_date=end_date or None,
        cache_window_days=CCUSAGE_CACHE_WINDOW_DAYS,
    )
    provider = normalize_token_provider(provider)
    if provider in {"all", "codex", "claude"}:
        provider = "all"
    return "|".join(
        [
            provider,
            fetch_start.isoformat(),
            fetch_end.isoformat(),
        ]
    )


def refresh_token_cache(window_days, provider, start_date=None, end_date=None, group_by="day"):
    provider = normalize_token_provider(provider)
    group_by = normalize_token_group_by(group_by)
    fetch_provider = "all" if provider in {"all", "codex", "claude"} else provider
    fetch_start, fetch_end, fetch_window_days = resolve_token_cache_fetch_range(
        window_days=window_days,
        start_date=start_date or None,
        end_date=end_date or None,
        cache_window_days=CCUSAGE_CACHE_WINDOW_DAYS,
    )
    ccusage_result = fetch_ccusage_daily(
        window_days=fetch_window_days,
        provider=fetch_provider,
        start_date=fetch_start,
        end_date=fetch_end,
    )
    if fetch_provider == "all" and ccusage_result.get("available"):
        source_payload = build_token_payload_from_result(
            ccusage_result,
            fetch_window_days,
            "all",
            start_date=fetch_start.isoformat(),
            end_date=fetch_end.isoformat(),
            group_by=group_by,
            served_from_cache=False,
            stale=False,
        )
        write_cache(source_payload)
        write_token_usage_cache(ccusage_result, REPORT_TOKEN_CACHE_PATH)
    payload = build_token_payload_from_result(
        ccusage_result,
        window_days,
        provider,
        start_date=start_date or None,
        end_date=end_date or None,
        group_by=group_by,
        served_from_cache=False,
        stale=False,
    )
    if payload.get("token_usage", {}).get("available") and fetch_provider != "all":
        write_cache(payload)
    return payload


def start_token_cache_refresh_async(window_days, provider, start_date=None, end_date=None, group_by="day"):
    key = token_cache_refresh_key(
        window_days,
        provider,
        start_date=start_date,
        end_date=end_date,
    )
    with CACHE_REFRESH_LOCK:
        if key in CACHE_REFRESH_IN_FLIGHT:
            return False
        CACHE_REFRESH_IN_FLIGHT.add(key)

    def worker():
        try:
            refresh_token_cache(
                window_days,
                provider,
                start_date=start_date,
                end_date=end_date,
                group_by=group_by,
            )
        finally:
            with CACHE_REFRESH_LOCK:
                CACHE_REFRESH_IN_FLIGHT.discard(key)

    thread = threading.Thread(
        target=worker,
        name="openrelix-token-cache-refresh",
        daemon=True,
    )
    thread.start()
    return True


def cache_matches_request(
    payload,
    window_days,
    provider,
    start_date=None,
    end_date=None,
    group_by="day",
    now_func=current_local_datetime,
):
    if not payload:
        return False
    for entry in cache_entries(payload):
        ccusage_result = cached_ccusage_result(entry)
        if token_result_covers_request(
            ccusage_result,
            provider,
            window_days,
            start_date=start_date,
            end_date=end_date,
            now_func=now_func,
        ):
            return True
    if (
        normalize_token_provider(payload.get("provider")) != normalize_token_provider(provider)
        or normalize_token_group_by(payload.get("group_by")) != normalize_token_group_by(group_by)
    ):
        return False
    if start_date or end_date:
        requested_start, requested_end, _ = resolve_token_date_range(
            window_days=window_days,
            now_func=now_func,
            start_date=start_date,
            end_date=end_date,
        )
        return (
            parse_token_date(payload.get("range_start")) == requested_start
            and parse_token_date(payload.get("range_end")) == requested_end
        )
    return payload.get("window_days") == window_days


def cache_is_fresh(payload, window_days, provider, start_date=None, end_date=None, group_by="day"):
    cached = build_payload_from_cache(
        payload,
        window_days,
        provider,
        start_date=start_date,
        end_date=end_date,
        group_by=group_by,
    )
    return bool(cached and not cached.get("stale"))


def fetch_token_payload(window_days, force_refresh=False, provider="all", start_date=None, end_date=None, group_by="day"):
    provider = normalize_token_provider(provider)
    group_by = normalize_token_group_by(group_by)
    start_date = str(start_date or "").strip()
    end_date = str(end_date or "").strip()
    cached_payload = load_cache()
    if not force_refresh:
        cached_result = build_payload_from_cache(
            cached_payload,
            window_days,
            provider,
            start_date=start_date,
            end_date=end_date,
            group_by=group_by,
        )
        if cached_result:
            if cached_result.get("stale"):
                start_token_cache_refresh_async(
                    window_days,
                    provider,
                    start_date=start_date,
                    end_date=end_date,
                    group_by=group_by,
                )
            return cached_result

    with FETCH_LOCK:
        cached_payload = load_cache()
        if not force_refresh:
            cached_result = build_payload_from_cache(
                cached_payload,
                window_days,
                provider,
                start_date=start_date,
                end_date=end_date,
                group_by=group_by,
            )
            if cached_result:
                if cached_result.get("stale"):
                    start_token_cache_refresh_async(
                        window_days,
                        provider,
                        start_date=start_date,
                        end_date=end_date,
                        group_by=group_by,
                    )
                return cached_result

        payload = refresh_token_cache(
            window_days,
            provider,
            start_date=start_date or None,
            end_date=end_date or None,
            group_by=group_by,
        )
        if payload.get("token_usage", {}).get("available"):
            return payload

        stale_payload = build_payload_from_cache(
            cached_payload,
            window_days,
            provider,
            start_date=start_date,
            end_date=end_date,
            group_by=group_by,
        )
        if stale_payload:
            stale_payload["ok"] = True
            stale_payload["stale"] = True
            stale_payload["error"] = payload.get("error", "")
            stale_payload["served_from_cache"] = True
            stale_payload["token_usage"]["error"] = payload.get("error", "")
            return stale_payload

        return payload


class TokenLiveHandler(BaseHTTPRequestHandler):
    server_version = "TokenLiveServer/1.0"

    def _send_json(self, status_code, payload, allow_origin="*"):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if allow_origin:
            self.send_header("Access-Control-Allow-Origin", allow_origin)
            if allow_origin != "*":
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-OpenRelix-Token")
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(body)

    def _client_is_local(self):
        client_host = self.client_address[0] if self.client_address else ""
        return client_host.startswith("127.") or client_host == "::1" or client_host == "localhost"

    def do_OPTIONS(self):
        parsed = urlparse(self.path)
        if parsed.path in TRUSTED_POST_PATHS:
            origin = self.headers.get("Origin", "").strip()
            if origin and not is_allowed_panel_origin(origin):
                self._send_json(403, {"ok": False, "error": "forbidden_origin"}, allow_origin=None)
                return
            self._send_json(200, {"ok": True}, allow_origin=origin or "*")
            return
        self._send_json(200, {"ok": True})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "token-live",
                    "version": SERVICE_VERSION,
                    "repo_root": str(PATHS.repo_root),
                    "script_path": SERVICE_SCRIPT_PATH,
                    "endpoint": LIVE_TOKEN_ENDPOINT,
                },
            )
            return

        if parsed.path == "/update-status":
            self._send_json(200, update_state_snapshot())
            return

        if parsed.path == "/pipeline-status":
            self._send_json(200, load_pipeline_status(PATHS))
            return

        if parsed.path != "/token-usage":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return

        query = parse_qs(parsed.query)
        force_refresh = query.get("force", ["0"])[0] == "1"
        provider = normalize_token_provider(query.get("provider", ["all"])[0])
        group_by = normalize_token_group_by(query.get("group_by", ["day"])[0])
        start_date = query.get("start_date", [""])[0].strip()
        end_date = query.get("end_date", [""])[0].strip()
        try:
            window_days = int(query.get("window_days", [str(CCUSAGE_WINDOW_DAYS)])[0])
        except ValueError:
            window_days = CCUSAGE_WINDOW_DAYS

        payload = fetch_token_payload(
            window_days=window_days,
            force_refresh=force_refresh,
            provider=provider,
            start_date=start_date,
            end_date=end_date,
            group_by=group_by,
        )
        status_code = 200 if payload.get("ok") or payload.get("stale") else 503
        self._send_json(status_code, payload)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in TRUSTED_POST_PATHS:
            self._send_json(404, {"ok": False, "error": "not_found"}, allow_origin=None)
            return
        if not self._client_is_local():
            self._send_json(403, {"ok": False, "error": "forbidden_address"}, allow_origin=None)
            return
        origin = self.headers.get("Origin", "").strip()
        # Browsers always send Origin for cross-origin POST. Reject any browser
        # whose Origin is not the panel's file:// context — defends against DNS
        # rebinding or a public site preflighting to localhost.
        if origin and not is_allowed_panel_origin(origin):
            self._send_json(403, {"ok": False, "error": "forbidden_origin"}, allow_origin=None)
            return
        provided_token = self.headers.get("X-OpenRelix-Token", "").strip()
        expected_token = get_update_token()
        if not (expected_token and provided_token and secrets.compare_digest(provided_token, expected_token)):
            self._send_json(403, {"ok": False, "error": "forbidden_token"}, allow_origin=None)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        body = b""
        if length:
            try:
                body = self.rfile.read(min(length, 8192))
            except Exception:
                body = b""
        if parsed.path == CLAUDE_DESKTOP_OPEN_PATH:
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            snapshot = start_claude_desktop_resume(
                payload.get("resume_id", ""),
                cwd=payload.get("cwd", ""),
                paths=PATHS,
            )
            status_code = 202 if snapshot.get("ok") else 400
            if snapshot.get("error") in {"claude_desktop_app_not_found", "claude_cli_not_found"}:
                status_code = 503
            self._send_json(status_code, snapshot, allow_origin=origin or None)
            return
        if parsed.path == CODEX_DESKTOP_OPEN_PATH:
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            snapshot = start_codex_desktop_resume(
                payload.get("resume_id", ""),
                codex_home=payload.get("codex_home", ""),
                electron_user_data_path=payload.get("codex_electron_user_data_path", ""),
                paths=PATHS,
            )
            status_code = 202 if snapshot.get("ok") else 400
            if snapshot.get("error") in {"codex_desktop_app_not_found", "codex_desktop_profile_unknown"}:
                status_code = 503
            self._send_json(status_code, snapshot, allow_origin=origin or None)
            return
        if parsed.path == FINDER_REVEAL_PATH:
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            snapshot = reveal_path_in_finder(payload.get("path", ""))
            status_code = 200 if snapshot.get("ok") else 400
            if snapshot.get("error") in {"finder_unsupported_platform", "finder_open_failed"}:
                status_code = 503
            self._send_json(status_code, snapshot, allow_origin=origin or None)
            return
        if parsed.path == KNOWLEDGE_LARK_DOC_PATH:
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            snapshot = create_lark_doc_from_knowledge(payload.get("doc_id", ""))
            status_code = 200 if snapshot.get("ok") else 400
            if snapshot.get("error") in {"lark_cli_not_found", "lark_cli_timeout", "lark_cli_failed"}:
                status_code = 503
            self._send_json(status_code, snapshot, allow_origin=origin or None)
            return
        if parsed.path == MEMORY_FEEDBACK_PATH:
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            try:
                feedback = append_memory_feedback(
                    PATHS,
                    payload.get("memory_key", ""),
                    payload.get("feedback", ""),
                    title=payload.get("title", ""),
                    source=payload.get("source", "panel"),
                )
            except ValueError as exc:
                self._send_json(
                    400,
                    {"ok": False, "status": "failed", "error": str(exc)},
                    allow_origin=origin or None,
                )
                return
            refresh_started, refresh_snapshot = start_memory_feedback_refresh_async()
            self._send_json(
                202 if refresh_started else 200,
                memory_feedback_accepted_payload(feedback, refresh_snapshot, refresh_started),
                allow_origin=origin or None,
            )
            return
        if parsed.path == PANEL_REFRESH_PATH:
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            requested_date = str(payload.get("date", "") or "").strip()
            mode = str(payload.get("mode", "") or "").strip()
            if mode == "pipeline":
                started, snapshot = start_manual_pipeline_refresh(requested_date or None)
                snapshot["started_now"] = started
                self._send_json(202 if snapshot.get("ok") else 409, snapshot, allow_origin=origin or None)
                return
            snapshot = run_panel_refresh(requested_date or None)
            self._send_json(200 if snapshot.get("ok") else 503, snapshot, allow_origin=origin or None)
            return
        started, snapshot = start_update_async()
        snapshot["started_now"] = started
        # Echo back the trusted origin to satisfy CORS; omit ACAO entirely for
        # non-browser callers (no Origin) so we don't lie about who's allowed.
        self._send_json(202 if started else 200, snapshot, allow_origin=origin or None)

    def log_message(self, format_str, *args):
        timestamp = current_local_datetime().strftime("%Y-%m-%d %H:%M:%S")
        print("[{}] {}".format(timestamp, format_str % args), flush=True)


def main():
    ensure_state_layout(PATHS)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(
        (LIVE_TOKEN_HOST, LIVE_TOKEN_PORT),
        TokenLiveHandler,
    )
    print(
        "Token live server listening at {}".format(LIVE_TOKEN_ENDPOINT),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
