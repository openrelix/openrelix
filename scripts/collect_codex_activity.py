#!/usr/bin/env python3

import argparse
import json
import os
import re
import select
import subprocess
import time
from datetime import datetime, timedelta

from asset_runtime import atomic_write_json, ensure_state_layout, get_runtime_paths

PATHS = get_runtime_paths()
CODEX_HOME = PATHS.codex_home
HISTORY_PATH = CODEX_HOME / "history.jsonl"
SESSIONS_DIR = CODEX_HOME / "sessions"
RAW_DIR = PATHS.raw_dir

REVIEW_REQUEST_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"(^|[，。；\s])(?:帮我|请|麻烦|需要|能不能|你)?\s*(?:re-?)?review(?:下|一下)?",
        r"代码审查",
        r"审查代码",
        r"帮我打分",
        r"给到\s*10\s*/\s*10",
        r"/subreview",
        r"独立.*review",
        r"独立.*审阅",
    ]
]


def local_date_from_epoch(ts):
    return datetime.fromtimestamp(int(ts)).astimezone().date().isoformat()


def local_datetime_from_epoch(ts):
    return datetime.fromtimestamp(int(ts)).astimezone().isoformat()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().astimezone().date().isoformat())
    parser.add_argument("--stage", default="manual", choices=["manual", "preliminary", "final"])
    parser.add_argument(
        "--activity-source",
        default=os.environ.get("OKEEP_ACTIVITY_SOURCE", os.environ.get("AI_ASSET_ACTIVITY_SOURCE", "history")),
        choices=["history", "app-server", "auto"],
        help=(
            "Activity source. 'history' reads CODEX_HOME JSONL files, "
            "'app-server' reads Codex through codex app-server, and 'auto' "
            "tries app-server before falling back to history."
        ),
    )
    parser.add_argument("--app-server-page-size", type=int, default=100)
    parser.add_argument("--app-server-max-threads", type=int, default=500)
    parser.add_argument("--app-server-timeout", type=float, default=15.0)
    return parser.parse_args()


def load_history_for_date(target_date):
    prompts_by_session = {}
    if not HISTORY_PATH.exists():
        return prompts_by_session

    for raw_line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        item = json.loads(line)
        if local_date_from_epoch(item["ts"]) != target_date:
            continue
        session_id = item["session_id"]
        prompts_by_session.setdefault(session_id, []).append(
            {
                "ts": item["ts"],
                "local_time": local_datetime_from_epoch(item["ts"]),
                "text": item["text"],
            }
        )
    return prompts_by_session


class AppServerError(RuntimeError):
    pass


class CodexAppServerClient:
    def __init__(self, timeout_seconds=15.0):
        self.timeout_seconds = timeout_seconds
        self.next_request_id = 1
        self.process = subprocess.Popen(
            [PATHS.codex_bin, "app-server", "--listen", "stdio://"],
            cwd=str(PATHS.repo_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0,
        )

    def close(self):
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def __enter__(self):
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "openkeepsake",
                        "version": "0.1.0",
                    },
                    "capabilities": {
                        "experimentalApi": True,
                        "optOutNotificationMethods": [],
                    },
                },
            )
            self.notify("initialized")
            return self
        except Exception:
            self.close()
            raise

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def notify(self, method, params=None):
        payload = {"method": method}
        if params is not None:
            payload["params"] = params
        self._write_message(payload)

    def request(self, method, params=None):
        request_id = self.next_request_id
        self.next_request_id += 1
        payload = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._write_message(payload)
        return self._read_response(request_id)

    def _write_message(self, payload):
        if not self.process.stdin:
            raise AppServerError("app-server stdin is closed")
        self.process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def _read_response(self, request_id):
        deadline = time.monotonic() + self.timeout_seconds
        stderr_lines = []
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AppServerError(
                    "app-server exited before response {}: {}".format(
                        request_id,
                        "\n".join(stderr_lines).strip(),
                    )
                )
            readable, _, _ = select.select(
                [self.process.stdout, self.process.stderr],
                [],
                [],
                min(0.2, max(deadline - time.monotonic(), 0.0)),
            )
            for stream in readable:
                line = stream.readline()
                if not line:
                    continue
                if stream is self.process.stderr:
                    stderr_lines.append(line.rstrip())
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AppServerError("invalid app-server JSON response: {}".format(line[:200])) from exc
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise AppServerError("app-server {} failed: {}".format(request_id, message["error"]))
                return message.get("result", {})
        raise AppServerError(
            "timed out waiting for app-server response {}{}".format(
                request_id,
                ": " + "\n".join(stderr_lines).strip() if stderr_lines else "",
            )
        )


def epoch_for_local_date_start(date_str):
    return int(datetime.fromisoformat(date_str + "T00:00:00").astimezone().timestamp())


def target_date_epoch_range(date_str):
    start = epoch_for_local_date_start(date_str)
    return start, start + 24 * 60 * 60


def input_content_to_text(content):
    parts = []
    for item in content or []:
        item_type = item.get("type")
        if item_type == "text":
            text = item.get("text", "")
            if text:
                parts.append(text)
        elif item_type == "image":
            parts.append("[Image]")
        elif item_type == "localImage":
            parts.append("[Local image]")
        elif item_type == "skill":
            name = item.get("name") or "skill"
            parts.append("[Skill: {}]".format(name))
        elif item_type == "mention":
            name = item.get("name") or "mention"
            parts.append("[Mention: {}]".format(name))
    return "\n".join(parts).strip()


def turn_user_text(turn):
    texts = []
    for item in turn.get("items", []):
        if item.get("type") != "userMessage":
            continue
        text = input_content_to_text(item.get("content", []))
        if text:
            texts.append(text)
    return "\n".join(texts).strip()


def turn_last_agent_message(turn):
    fallback = ""
    for item in turn.get("items", []):
        if item.get("type") != "agentMessage":
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        fallback = text
        if item.get("phase") == "final":
            return text
    return fallback


def turn_prompt_epoch(thread, turn):
    return turn.get("startedAt") or thread.get("createdAt") or thread.get("updatedAt")


def app_server_thread_to_window(thread, target_date, stage):
    prompts = []
    conclusions = []
    raw_conclusion_count = 0

    for turn in thread.get("turns", []):
        prompt_epoch = turn_prompt_epoch(thread, turn)
        if prompt_epoch and local_date_from_epoch(prompt_epoch) == target_date:
            prompt_text = turn_user_text(turn)
            if prompt_text:
                prompts.append(
                    {
                        "ts": int(prompt_epoch),
                        "local_time": local_datetime_from_epoch(prompt_epoch),
                        "text": prompt_text,
                    }
                )

        completed_at = turn.get("completedAt")
        if completed_at is None:
            continue
        turn_prompt_date = local_date_from_epoch(prompt_epoch) if prompt_epoch else ""
        if not completed_at_belongs_to_target(completed_at, target_date, stage, turn_prompt_date):
            continue
        last_message = turn_last_agent_message(turn)
        if not last_message:
            continue
        raw_conclusion_count += 1
        if looks_like_review_conclusion(last_message):
            continue
        conclusions.append(
            {
                "turn_id": turn.get("id", ""),
                "completed_at": local_datetime_from_epoch(completed_at),
                "text": last_message,
            }
        )

    if not prompts:
        return None

    source = thread.get("source") or "app-server"
    metadata = {
        "window_id": thread.get("id", ""),
        "cwd": thread.get("cwd", ""),
        "originator": "codex_app_server",
        "source": "codex_app_server:{}".format(source),
        "started_at": local_datetime_from_epoch(thread.get("createdAt")) if thread.get("createdAt") else "",
        "session_file": thread.get("path") or "",
        "app_server": {
            "thread_id": thread.get("id", ""),
            "thread_source": source,
            "model_provider": thread.get("modelProvider", ""),
            "cli_version": thread.get("cliVersion", ""),
            "preview": thread.get("preview", ""),
            "updated_at": local_datetime_from_epoch(thread.get("updatedAt")) if thread.get("updatedAt") else "",
        },
    }
    return build_window_payload(target_date, metadata, prompts, conclusions, raw_conclusion_count)


def load_app_server_windows_for_date(target_date, stage, page_size=100, max_threads=500, timeout_seconds=15.0):
    start_epoch, end_epoch = target_date_epoch_range(target_date)
    windows = []
    inspected_threads = 0
    cursor = None

    with CodexAppServerClient(timeout_seconds=timeout_seconds) as client:
        while inspected_threads < max_threads:
            params = {
                "limit": max(1, min(page_size, max_threads - inspected_threads)),
                "sortDirection": "desc",
                "sortKey": "updated_at",
            }
            if cursor:
                params["cursor"] = cursor
            response = client.request("thread/list", params)
            threads = response.get("data", [])
            if not threads:
                break

            stop_after_page = False
            for thread in threads:
                inspected_threads += 1
                created_at = thread.get("createdAt") or 0
                updated_at = thread.get("updatedAt") or created_at
                if updated_at < start_epoch and created_at < start_epoch:
                    stop_after_page = True
                    continue
                if created_at >= end_epoch:
                    continue
                read_response = client.request(
                    "thread/read",
                    {
                        "threadId": thread.get("id", ""),
                        "includeTurns": True,
                    },
                )
                hydrated_thread = read_response.get("thread", thread)
                window = app_server_thread_to_window(hydrated_thread, target_date, stage)
                if window:
                    windows.append(window)

            cursor = response.get("nextCursor")
            if stop_after_page or not cursor:
                break
    return windows


def completed_at_belongs_to_target(completed_at_epoch, target_date, stage, turn_prompt_date=None):
    completed_dt = datetime.fromtimestamp(int(completed_at_epoch)).astimezone()
    target_dt = datetime.fromisoformat(target_date)
    target_date_obj = target_dt.date()
    if completed_dt.date() == target_date_obj:
        return True
    if stage == "final":
        next_day = target_date_obj + timedelta(days=1)
        if completed_dt.date() == next_day and turn_prompt_date == target_date:
            return True
    return False


def looks_like_review_request(text):
    if not text:
        return False
    normalized = " ".join(str(text).split())
    return any(pattern.search(normalized) for pattern in REVIEW_REQUEST_PATTERNS)


def looks_like_review_conclusion(text):
    normalized = " ".join(str(text).split())
    if not normalized:
        return False
    review_patterns = [
        re.compile(r"\*\*Review\*\*", re.IGNORECASE),
        re.compile(r"\*\*Findings\*\*", re.IGNORECASE),
        re.compile(r"^\s*review findings", re.IGNORECASE),
        re.compile(r"^\s*有，独立 reviewer 给了", re.IGNORECASE),
        re.compile(r"^\s*独立 Codex 做完 review 后", re.IGNORECASE),
        re.compile(r"overall score", re.IGNORECASE),
        re.compile(r"^\s*score\s*[:：]", re.IGNORECASE),
        re.compile(r"^\s*评分\s*[:：]", re.IGNORECASE),
        re.compile(r"^\s*总体评分", re.IGNORECASE),
        re.compile(r"^\s*10\s*/\s*10", re.IGNORECASE),
    ]
    return any(pattern.search(normalized) for pattern in review_patterns)


def find_session_file(session_id):
    matches = list(SESSIONS_DIR.rglob("*{}*.jsonl".format(session_id)))
    return matches[0] if matches else None


def build_turn_prompt_map(session_file):
    turn_prompts = {}
    turn_prompt_meta = {}
    current_turn_id = None
    for raw_line in session_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        item = json.loads(line)
        item_type = item.get("type")
        payload = item.get("payload", {})

        if item_type == "event_msg" and payload.get("type") == "task_started":
            current_turn_id = payload.get("turn_id")
        elif item_type == "turn_context":
            current_turn_id = payload.get("turn_id")
        elif item_type == "event_msg" and payload.get("type") == "user_message":
            if current_turn_id:
                turn_prompts.setdefault(current_turn_id, []).append(payload.get("message", ""))
                turn_prompt_meta.setdefault(
                    current_turn_id,
                    {
                        "first_timestamp": item.get("timestamp", ""),
                    },
                )
    result = {}
    for turn_id, messages in turn_prompts.items():
        result[turn_id] = {
            "text": "\n".join(text for text in messages if text),
            "first_timestamp": turn_prompt_meta.get(turn_id, {}).get("first_timestamp", ""),
        }
    return result


def load_session_metadata_and_conclusions(session_id, target_date, stage):
    session_file = find_session_file(session_id)
    metadata = {
        "window_id": session_id,
        "cwd": "",
        "originator": "",
        "source": "",
        "started_at": "",
        "session_file": str(session_file) if session_file else "",
    }
    conclusions = []
    if not session_file or not session_file.exists():
        return metadata, conclusions, 0
    turn_prompt_map = build_turn_prompt_map(session_file)
    raw_conclusion_count = 0

    for raw_line in session_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        item = json.loads(line)
        item_type = item.get("type")
        payload = item.get("payload", {})

        if item_type == "session_meta":
            metadata.update(
                {
                    "cwd": payload.get("cwd", ""),
                    "originator": payload.get("originator", ""),
                    "source": payload.get("source", ""),
                    "started_at": payload.get("timestamp", ""),
                }
            )
        elif item_type == "event_msg" and payload.get("type") == "task_complete":
            completed_at = payload.get("completed_at")
            if completed_at is None:
                continue
            turn_id = payload.get("turn_id", "")
            turn_prompt = turn_prompt_map.get(turn_id, {})
            turn_prompt_date = ""
            if turn_prompt.get("first_timestamp"):
                try:
                    turn_prompt_date = datetime.fromisoformat(
                        turn_prompt["first_timestamp"].replace("Z", "+00:00")
                    ).astimezone().date().isoformat()
                except ValueError:
                    turn_prompt_date = ""
            if not completed_at_belongs_to_target(completed_at, target_date, stage, turn_prompt_date):
                continue
            last_message = payload.get("last_agent_message", "")
            if last_message is None or not str(last_message).strip():
                continue
            raw_conclusion_count += 1
            if looks_like_review_conclusion(last_message):
                continue
            conclusions.append(
                {
                    "turn_id": turn_id,
                    "completed_at": local_datetime_from_epoch(completed_at),
                    "text": last_message,
                }
            )

    return metadata, conclusions, raw_conclusion_count


def looks_like_review_window(prompt_entries):
    hits = 0
    for entry in prompt_entries:
        text = entry["text"]
        if looks_like_review_request(text):
            hits += 1
    if not prompt_entries:
        return False
    first_prompt_text = prompt_entries[0]["text"]
    if len(prompt_entries) <= 8 and looks_like_review_request(first_prompt_text):
        return True
    ratio = hits / len(prompt_entries)
    return hits >= 2 and ratio >= 0.6


def build_window_payload(target_date, metadata, prompts, conclusions, raw_conclusion_count):
    review_related_window = looks_like_review_window(prompts)
    filtered_review_conclusion_count = max(0, raw_conclusion_count - len(conclusions))
    review_like_window = filtered_review_conclusion_count > 0
    window_payload = {
        "date": target_date,
        "window_id": metadata["window_id"],
        "cwd": metadata["cwd"],
        "originator": metadata["originator"],
        "source": metadata["source"],
        "started_at": metadata["started_at"],
        "session_file": metadata["session_file"],
        "prompt_count": len(prompts),
        "conclusion_count": len(conclusions),
        "raw_conclusion_count": raw_conclusion_count,
        "review_like_window": review_like_window,
        "review_related_window": review_related_window,
        "filtered_review_conclusion_count": filtered_review_conclusion_count,
        "conclusion_policy": "review_prompts_collected_but_review_conclusions_filtered" if review_like_window else "included",
        "prompts": prompts,
        "conclusions": conclusions,
    }
    if metadata.get("app_server"):
        window_payload["app_server"] = metadata["app_server"]
    return window_payload


def load_history_windows_for_date(target_date, stage):
    prompts_by_session = load_history_for_date(target_date)
    windows = []
    for session_id, prompts in sorted(prompts_by_session.items(), key=lambda item: item[0]):
        metadata, conclusions, raw_conclusion_count = load_session_metadata_and_conclusions(session_id, target_date, stage)
        windows.append(build_window_payload(target_date, metadata, prompts, conclusions, raw_conclusion_count))
    return windows


def review_like_window_rows(windows):
    rows = []
    for window in windows:
        if not window["review_like_window"]:
            continue
        rows.append(
            {
                "window_id": window["window_id"],
                "reason": "review_like_window",
                "cwd": window["cwd"],
                "prompt_count": window["prompt_count"],
                "raw_conclusion_count": window["raw_conclusion_count"],
                "filtered_review_conclusion_count": window["filtered_review_conclusion_count"],
            }
        )
    return rows


def write_json(path, payload):
    atomic_write_json(path, payload)


def main():
    ensure_state_layout(PATHS)
    args = parse_args()
    target_date = args.date
    stage = args.stage
    collection_source = args.activity_source
    collection_errors = []
    excluded_windows = []

    if args.activity_source in {"app-server", "auto"}:
        try:
            windows = load_app_server_windows_for_date(
                target_date,
                stage,
                page_size=args.app_server_page_size,
                max_threads=args.app_server_max_threads,
                timeout_seconds=args.app_server_timeout,
            )
            collection_source = "app-server"
        except (AppServerError, OSError, subprocess.SubprocessError) as exc:
            if args.activity_source == "app-server":
                raise
            collection_errors.append("app-server unavailable: {}".format(exc))
            windows = load_history_windows_for_date(target_date, stage)
            collection_source = "history_fallback"
    else:
        windows = load_history_windows_for_date(target_date, stage)
        collection_source = "history"

    review_like_windows = review_like_window_rows(windows)

    for window_payload in windows:
        write_json(
            RAW_DIR / "windows" / target_date / "{}.json".format(window_payload["window_id"]),
            window_payload,
        )

    daily_payload = {
        "date": target_date,
        "stage": stage,
        "generated_at": datetime.now().astimezone().isoformat(),
        "timezone": str(datetime.now().astimezone().tzinfo),
        "collection_source": collection_source,
        "collection_errors": collection_errors,
        "window_count": len(windows),
        "excluded_window_count": len(excluded_windows),
        "review_like_window_count": len(review_like_windows),
        "prompt_count": sum(window["prompt_count"] for window in windows),
        "conclusion_count": sum(window["conclusion_count"] for window in windows),
        "windows": windows,
        "excluded_windows": excluded_windows,
        "review_like_windows": review_like_windows,
    }
    write_json(RAW_DIR / "daily" / "{}.json".format(target_date), daily_payload)


if __name__ == "__main__":
    main()
