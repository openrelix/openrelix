#!/usr/bin/env python3

import json
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import build_overview
from asset_runtime import atomic_write_json, ensure_state_layout, get_runtime_paths


PATHS = get_runtime_paths()
RUNTIME_DIR = PATHS.runtime_dir
CACHE_PATH = RUNTIME_DIR / "token-live-cache.json"
CACHE_TTL_SECONDS = 90
FETCH_LOCK = threading.Lock()

OPENRELIX_CLI = PATHS.repo_root / "scripts" / "openrelix.py"
UPDATE_TIMEOUT_SECONDS = 600
UPDATE_LOG_TAIL_LINES = 12
UPDATE_LOCK = threading.Lock()
UPDATE_STATE = {
    "status": "idle",
    "started_at": 0,
    "ended_at": 0,
    "exit_code": None,
    "error": "",
    "log_tail": "",
}

# Shared persistent secret with the panel template (build_overview.read_or_create_update_token).
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
            _UPDATE_TOKEN_CACHE = build_overview.read_or_create_update_token()
    return _UPDATE_TOKEN_CACHE


def is_allowed_panel_origin(origin):
    if not origin:
        return False
    if origin in ALLOWED_PANEL_ORIGIN_EXACT:
        return True
    return any(origin.startswith(prefix) for prefix in ALLOWED_PANEL_ORIGIN_PREFIXES)


def update_state_snapshot():
    with UPDATE_LOCK:
        return dict(UPDATE_STATE)


def _run_update_subprocess():
    cmd = [sys.executable, str(OPENRELIX_CLI), "update", "--yes"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=UPDATE_TIMEOUT_SECONDS,
            cwd=str(PATHS.repo_root),
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        tail = "\n".join(output.splitlines()[-UPDATE_LOG_TAIL_LINES:])
        with UPDATE_LOCK:
            UPDATE_STATE.update({
                "status": "completed" if proc.returncode == 0 else "failed",
                "exit_code": proc.returncode,
                "ended_at": time.time(),
                "log_tail": tail,
                "error": "" if proc.returncode == 0 else "exit_code={}".format(proc.returncode),
            })
    except subprocess.TimeoutExpired:
        with UPDATE_LOCK:
            UPDATE_STATE.update({
                "status": "failed",
                "ended_at": time.time(),
                "error": "timeout",
            })
    except Exception as exc:
        with UPDATE_LOCK:
            UPDATE_STATE.update({
                "status": "failed",
                "ended_at": time.time(),
                "error": str(exc),
            })


def start_update_async():
    with UPDATE_LOCK:
        if UPDATE_STATE["status"] == "running":
            return False, dict(UPDATE_STATE)
        UPDATE_STATE.update({
            "status": "running",
            "started_at": time.time(),
            "ended_at": 0,
            "exit_code": None,
            "error": "",
            "log_tail": "",
        })
        snapshot = dict(UPDATE_STATE)
    threading.Thread(target=_run_update_subprocess, daemon=True).start()
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


def cache_is_fresh(payload, window_days):
    if not payload or payload.get("window_days") != window_days:
        return False
    cached_at_epoch = payload.get("_cached_at_epoch", 0)
    return (time.time() - cached_at_epoch) < CACHE_TTL_SECONDS


def fetch_token_payload(window_days, force_refresh=False):
    cached_payload = load_cache()
    if not force_refresh and cache_is_fresh(cached_payload, window_days):
        cached_result = dict(cached_payload)
        cached_result["served_from_cache"] = True
        cached_result["stale"] = False
        return cached_result

    with FETCH_LOCK:
        cached_payload = load_cache()
        if not force_refresh and cache_is_fresh(cached_payload, window_days):
            cached_result = dict(cached_payload)
            cached_result["served_from_cache"] = True
            cached_result["stale"] = False
            return cached_result

        ccusage_result = build_overview.fetch_ccusage_daily(window_days=window_days)
        token_usage = build_overview.build_token_usage_view(ccusage_result)
        payload = {
            "ok": bool(token_usage.get("available")),
            "stale": False,
            "error": token_usage.get("error", ""),
            "window_days": window_days,
            "served_from_cache": False,
            "token_usage": token_usage,
            "_cached_at_epoch": time.time(),
        }
        if token_usage.get("available"):
            write_cache(payload)
            return payload

        if cached_payload and cached_payload.get("window_days") == window_days:
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
        if parsed.path == "/run-update":
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
                    "endpoint": build_overview.LIVE_TOKEN_ENDPOINT,
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
        try:
            window_days = int(query.get("window_days", [str(build_overview.CCUSAGE_WINDOW_DAYS)])[0])
        except ValueError:
            window_days = build_overview.CCUSAGE_WINDOW_DAYS

        payload = fetch_token_payload(window_days=window_days, force_refresh=force_refresh)
        status_code = 200 if payload.get("ok") or payload.get("stale") else 503
        self._send_json(status_code, payload)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/run-update":
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
        if length:
            try:
                self.rfile.read(min(length, 4096))
            except Exception:
                pass
        started, snapshot = start_update_async()
        snapshot["started_now"] = started
        # Echo back the trusted origin to satisfy CORS; omit ACAO entirely for
        # non-browser callers (no Origin) so we don't lie about who's allowed.
        self._send_json(202 if started else 200, snapshot, allow_origin=origin or None)

    def log_message(self, format_str, *args):
        timestamp = build_overview.current_local_datetime().strftime("%Y-%m-%d %H:%M:%S")
        print("[{}] {}".format(timestamp, format_str % args), flush=True)


def main():
    ensure_state_layout(PATHS)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(
        (build_overview.LIVE_TOKEN_HOST, build_overview.LIVE_TOKEN_PORT),
        TokenLiveHandler,
    )
    print(
        "Token live server listening at {}".format(build_overview.LIVE_TOKEN_ENDPOINT),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
