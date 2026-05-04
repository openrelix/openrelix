#!/usr/bin/env python3

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from asset_runtime import atomic_write_json, ensure_state_layout, get_runtime_language, get_runtime_paths
from openrelix_overview.common import current_local_datetime
from openrelix_overview.claude_desktop import (
    CLAUDE_DESKTOP_OPEN_PATH,
    start_claude_desktop_resume,
)
from openrelix_overview.config import (
    CCUSAGE_WINDOW_DAYS,
    LIVE_TOKEN_ENDPOINT,
    LIVE_TOKEN_HOST,
    LIVE_TOKEN_PORT,
)
from openrelix_overview.token_fetcher import (
    fetch_ccusage_daily,
    normalize_token_provider,
    parse_token_date,
    resolve_token_date_range,
)
from openrelix_overview.token_usage import build_token_usage_view, normalize_token_group_by
from openrelix_overview.update_secret import read_or_create_update_token


PATHS = get_runtime_paths()
LANGUAGE = get_runtime_language(PATHS)
RUNTIME_DIR = PATHS.runtime_dir
CACHE_PATH = RUNTIME_DIR / "token-live-cache.json"
CACHE_TTL_SECONDS = 90
FETCH_LOCK = threading.Lock()

OPENRELIX_CLI = PATHS.repo_root / "scripts" / "openrelix.py"
UPDATE_WORKER_SCRIPT = PATHS.repo_root / "scripts" / "openrelix_update_worker.py"
UPDATE_STATUS_PATH = RUNTIME_DIR / "update-status.json"
UPDATE_TIMEOUT_SECONDS = 600
UPDATE_LOG_TAIL_LINES = 12
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
    if not CACHE_PATH.exists():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_cache(payload):
    atomic_write_json(CACHE_PATH, payload)


def cache_matches_request(
    payload,
    window_days,
    provider,
    start_date=None,
    end_date=None,
    group_by="day",
    now_func=current_local_datetime,
):
    if (
        not payload
        or normalize_token_provider(payload.get("provider")) != normalize_token_provider(provider)
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
    if not cache_matches_request(
        payload,
        window_days,
        provider,
        start_date=start_date,
        end_date=end_date,
        group_by=group_by,
    ):
        return False
    cached_at_epoch = payload.get("_cached_at_epoch", 0)
    return (time.time() - cached_at_epoch) < CACHE_TTL_SECONDS


def fetch_token_payload(window_days, force_refresh=False, provider="all", start_date=None, end_date=None, group_by="day"):
    provider = normalize_token_provider(provider)
    group_by = normalize_token_group_by(group_by)
    start_date = str(start_date or "").strip()
    end_date = str(end_date or "").strip()
    cached_payload = load_cache()
    if not force_refresh and cache_is_fresh(
        cached_payload,
        window_days,
        provider,
        start_date=start_date,
        end_date=end_date,
        group_by=group_by,
    ):
        cached_result = dict(cached_payload)
        cached_result["served_from_cache"] = True
        cached_result["stale"] = False
        return cached_result

    with FETCH_LOCK:
        cached_payload = load_cache()
        if not force_refresh and cache_is_fresh(
            cached_payload,
            window_days,
            provider,
            start_date=start_date,
            end_date=end_date,
            group_by=group_by,
        ):
            cached_result = dict(cached_payload)
            cached_result["served_from_cache"] = True
            cached_result["stale"] = False
            return cached_result

        ccusage_result = fetch_ccusage_daily(
            window_days=window_days,
            provider=provider,
            start_date=start_date or None,
            end_date=end_date or None,
        )
        token_usage = build_token_usage_view(
            ccusage_result,
            language=LANGUAGE,
            group_by=group_by,
            start_date=start_date or None,
            end_date=end_date or None,
        )
        payload = {
            "ok": bool(token_usage.get("available")),
            "stale": False,
            "error": token_usage.get("error", ""),
            "window_days": window_days,
            "provider": provider,
            "group_by": group_by,
            "range_start": token_usage.get("range_start", start_date),
            "range_end": token_usage.get("range_end", end_date),
            "served_from_cache": False,
            "token_usage": token_usage,
            "_cached_at_epoch": time.time(),
        }
        if token_usage.get("available"):
            write_cache(payload)
            return payload

        if cache_matches_request(
            cached_payload,
            window_days,
            provider,
            start_date=start_date,
            end_date=end_date,
            group_by=group_by,
        ):
            stale_payload = dict(cached_payload)
            stale_payload["ok"] = True
            stale_payload["stale"] = True
            stale_payload["error"] = token_usage.get("error", "")
            stale_payload["served_from_cache"] = True
            if "token_usage" in stale_payload:
                stale_payload["token_usage"]["error"] = token_usage.get("error", "")
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
        self.end_headers()
        self.wfile.write(body)

    def _client_is_local(self):
        client_host = self.client_address[0] if self.client_address else ""
        return client_host.startswith("127.") or client_host == "::1" or client_host == "localhost"

    def do_OPTIONS(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/run-update", CLAUDE_DESKTOP_OPEN_PATH}:
            origin = self.headers.get("Origin", "").strip()
            if not is_allowed_panel_origin(origin):
                self._send_json(403, {"ok": False, "error": "forbidden_origin"}, allow_origin=None)
                return
            self._send_json(200, {"ok": True}, allow_origin=origin)
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
                    "endpoint": LIVE_TOKEN_ENDPOINT,
                },
            )
            return

        if parsed.path == "/update-status":
            self._send_json(200, update_state_snapshot())
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
        if parsed.path not in {"/run-update", CLAUDE_DESKTOP_OPEN_PATH}:
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
            snapshot = start_claude_desktop_resume(payload.get("resume_id", ""), paths=PATHS)
            status_code = 202 if snapshot.get("ok") else 400
            if snapshot.get("error") in {"claude_desktop_app_not_found", "claude_cli_not_found"}:
                status_code = 503
            self._send_json(status_code, snapshot, allow_origin=origin or None)
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
