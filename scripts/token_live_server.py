#!/usr/bin/env python3

import json
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

    def _send_json(self, status_code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
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
