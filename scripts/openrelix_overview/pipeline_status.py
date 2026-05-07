"""Runtime pipeline status helpers for the local panel.

The status file is intentionally small and generic. It records stage names,
dates, and timing only; commands, logs, paths, and tool payloads stay out of the
public-facing report surface.
"""

from __future__ import annotations

import json
import os
import plistlib
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from asset_runtime import atomic_write_json, get_runtime_paths


SCHEMA_VERSION = 1
STATUS_FILE_NAME = "pipeline-status.json"
RECENT_LIMIT = 8
STALE_RUNNING_SECONDS = 12 * 60 * 60

SCHEDULED_JOBS = (
    (
        "io.github.openrelix.overview-refresh",
        "概览刷新",
        "Overview Refresh",
        "refresh_overview",
    ),
    (
        "io.github.openrelix.nightly-organize",
        "夜间预览整理",
        "Nightly Preview",
        "nightly_pipeline",
    ),
    (
        "io.github.openrelix.nightly-finalize-previous-day",
        "前一日终版整理",
        "Previous-day Finalize",
        "nightly_pipeline",
    ),
)

DEFAULT_STEPS = {
    "refresh_overview": [
        ("collect_activity", "采集活动", "Collect Activity"),
        ("sync_summary", "同步摘要", "Sync Summary"),
        ("display_cache", "更新展示缓存", "Update Display Cache"),
        ("asset_stats", "刷新资产层", "Refresh Asset Layer"),
        ("build_panel", "重建面板", "Rebuild Panel"),
        ("rebuild_index", "重建索引", "Rebuild Index"),
    ],
    "asset_layer_refresh": [
        ("asset_stats", "刷新资产层", "Refresh Asset Layer"),
        ("build_panel", "重建面板", "Rebuild Panel"),
    ],
    "nightly_pipeline": [
        ("collect_activity", "采集目标日期", "Collect Target Date"),
        ("collect_learning", "采集学习窗口", "Collect Learning Windows"),
        ("synthesize", "整理总结", "Synthesize"),
        ("rebuild_index", "重建索引", "Rebuild Index"),
        ("sync_summary", "同步摘要", "Sync Summary"),
        ("display_cache", "更新展示缓存", "Update Display Cache"),
        ("asset_stats", "刷新资产层", "Refresh Asset Layer"),
        ("build_panel", "重建面板", "Rebuild Panel"),
    ],
}

PIPELINE_LABELS = {
    "refresh_overview": ("概览刷新", "Overview Refresh"),
    "asset_layer_refresh": ("资产层刷新", "Asset Layer Refresh"),
    "nightly_pipeline": ("记忆整理流水线", "Memory Synthesis Pipeline"),
}

STEP_MESSAGES = {
    "collect_activity": ("正在读取本地 agent 活动窗口。", "Reading local agent activity windows."),
    "collect_learning": ("正在补齐近期学习窗口。", "Collecting recent learning windows."),
    "synthesize": ("正在调用配置的模型整理总结。", "Calling the configured model for synthesis."),
    "rebuild_index": ("正在重建本地检索索引。", "Rebuilding the local search index."),
    "sync_summary": ("正在同步 bounded host context 摘要。", "Syncing the bounded host-context summary."),
    "display_cache": ("正在更新记忆卡展示缓存。", "Updating the memory-card display cache."),
    "asset_stats": ("正在刷新资产层统计。", "Refreshing asset-layer statistics."),
    "build_panel": ("正在生成 overview 数据和 panel.html。", "Generating overview data and panel.html."),
}

FAILURE_HINTS = {
    "process_exited": (
        "任务进程已退出，状态文件没有收到完成信号。可能是脚本被系统停止、服务重启，或最后一步异常结束。",
        "The task process exited before OpenRelix received a completion signal. The script may have been stopped, the service restarted, or the last step may have failed.",
    ),
    "stale_running_status": (
        "任务长时间没有更新状态，已按超时处理。",
        "The task did not update its status for a long time and was marked as timed out.",
    ),
    "refresh_script_not_found": (
        "刷新脚本未找到，当前安装可能没有指向有效的 OpenRelix 代码目录。",
        "The refresh script was not found, so the current installation may not point to a valid OpenRelix checkout.",
    ),
    "refresh_failed": (
        "刷新脚本返回失败。",
        "The refresh script returned a failure.",
    ),
}


def status_path(paths=None):
    paths = paths or get_runtime_paths()
    return paths.runtime_dir / STATUS_FILE_NAME


def now_epoch():
    return time.time()


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


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


def _load_raw(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _load_plist(path):
    try:
        with Path(path).open("rb") as handle:
            payload = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _iso_for_datetime(value):
    return value.astimezone().isoformat(timespec="seconds")


def _coerce_epoch(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def _datetime_from_epoch(value, now):
    value = _coerce_epoch(value)
    if not value:
        return None
    return datetime.fromtimestamp(value, tz=now.tzinfo)


def _matches_interval_job(row, label, pipeline, stage, learn_memory):
    if not isinstance(row, dict):
        return False
    row_pipeline = str(row.get("pipeline") or "")
    row_stage = str(row.get("stage") or "")
    expected = [(pipeline, stage)]
    if label == "io.github.openrelix.overview-refresh" and learn_memory:
        expected.insert(0, ("nightly_pipeline", stage))
    for expected_pipeline, expected_stage in expected:
        if row_pipeline != expected_pipeline:
            continue
        if expected_stage and row_stage != expected_stage:
            continue
        return True
    return False


def _latest_interval_anchor(status_payload, label, pipeline, stage, learn_memory):
    if not isinstance(status_payload, dict):
        return 0.0
    rows = []
    if status_payload.get("pipeline"):
        rows.append(status_payload)
    rows.extend(status_payload.get("recent_runs") or [])
    latest = 0.0
    for row in rows:
        if not _matches_interval_job(row, label, pipeline, stage, learn_memory):
            continue
        anchor = (
            _coerce_epoch(row.get("ended_at"))
            or _coerce_epoch(row.get("updated_at"))
            or _coerce_epoch(row.get("started_at"))
        )
        latest = max(latest, anchor)
    return latest


def _schedule_next_interval(now, seconds, anchor=None):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    base = _datetime_from_epoch(anchor, now) if anchor else None
    return (base or now) + timedelta(seconds=seconds)


def _schedule_next_calendar(now, calendar):
    if not isinstance(calendar, dict):
        return None
    try:
        hour = int(calendar.get("Hour", now.hour))
        minute = int(calendar.get("Minute", 0))
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def scheduled_runs(paths=None, now=None, status_payload=None):
    paths = paths or get_runtime_paths()
    now = now or datetime.now().astimezone()
    rows = []
    for label, title, title_en, pipeline in SCHEDULED_JOBS:
        plist_path = paths.launch_agents_dir / "{}.plist".format(label)
        payload = _load_plist(plist_path)
        if not payload:
            continue
        env = payload.get("EnvironmentVariables") if isinstance(payload.get("EnvironmentVariables"), dict) else {}
        learn_memory = str(env.get("OPENRELIX_REFRESH_LEARN_MEMORY", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        stage = str(env.get("OPENRELIX_REFRESH_STAGE") or "")
        try:
            learn_window_days = int(env.get("OPENRELIX_REFRESH_LEARN_WINDOW_DAYS") or 0)
        except (TypeError, ValueError):
            learn_window_days = 0
        next_at = None
        schedule_kind = ""
        interval = payload.get("StartInterval")
        interval_anchor = 0.0
        if interval:
            interval_anchor = _latest_interval_anchor(status_payload, label, pipeline, stage, learn_memory)
            next_at = _schedule_next_interval(now, interval, anchor=interval_anchor)
            schedule_kind = "interval"
        if next_at is None:
            next_at = _schedule_next_calendar(now, payload.get("StartCalendarInterval"))
            schedule_kind = "calendar" if next_at is not None else ""
        if next_at is None:
            continue
        row = {
            "label": label,
            "title": title,
            "title_en": title_en,
            "pipeline": pipeline,
            "next_at": next_at.timestamp(),
            "next_at_iso": _iso_for_datetime(next_at),
            "schedule_kind": schedule_kind,
            "interval_seconds": int(interval or 0),
            "stage": stage,
            "learn_memory": learn_memory,
            "learn_window_days": learn_window_days,
        }
        if interval_anchor:
            row["interval_anchor_at"] = interval_anchor
            row["interval_anchor_at_iso"] = _iso_for_datetime(
                _datetime_from_epoch(interval_anchor, now)
            )
        rows.append(row)
    rows.sort(key=lambda item: item.get("next_at") or 0)
    return rows


def next_scheduled_run(paths=None, now=None):
    rows = scheduled_runs(paths=paths, now=now)
    return rows[0] if rows else {}


def _sanitize_recent_runs(rows):
    sanitized = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sanitized.append({
            "run_id": str(row.get("run_id") or "")[:80],
            "pipeline": str(row.get("pipeline") or "")[:80],
            "title": str(row.get("title") or "")[:120],
            "title_en": str(row.get("title_en") or "")[:120],
            "status": str(row.get("status") or "")[:40],
            "target_date": str(row.get("target_date") or "")[:24],
            "stage": str(row.get("stage") or "")[:40],
            "started_at": row.get("started_at"),
            "started_at_iso": str(row.get("started_at_iso") or "")[:40],
            "ended_at": row.get("ended_at"),
            "ended_at_iso": str(row.get("ended_at_iso") or "")[:40],
            "exit_code": row.get("exit_code"),
        })
        if len(sanitized) >= RECENT_LIMIT:
            break
    return sanitized


def load_status(paths=None):
    path = status_path(paths)
    payload = _load_raw(path)
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload.setdefault("status", "idle")
    payload.setdefault("steps", [])
    payload["recent_runs"] = _sanitize_recent_runs(payload.get("recent_runs", []))
    if payload.get("status") == "running":
        started_at = float(payload.get("started_at") or 0)
        if payload.get("pid") and not process_is_alive(payload.get("pid")):
            payload = finish_run(
                payload.get("run_id", ""),
                status="failed",
                exit_code=None,
                error="process_exited",
                paths=paths,
                existing=payload,
            )
        elif started_at and now_epoch() - started_at > STALE_RUNNING_SECONDS:
            payload = finish_run(
                payload.get("run_id", ""),
                status="failed",
                exit_code=None,
                error="stale_running_status",
                paths=paths,
                existing=payload,
            )
    payload = attach_failure_hint(payload)
    payload["scheduled_runs"] = scheduled_runs(paths, status_payload=payload)
    payload["next_run"] = payload["scheduled_runs"][0] if payload["scheduled_runs"] else {}
    return payload


def write_status(payload, paths=None):
    path = status_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)
    return payload


def pipeline_label(pipeline):
    return PIPELINE_LABELS.get(pipeline, (pipeline.replace("_", " ").title(), pipeline.replace("_", " ").title()))


def default_steps(pipeline):
    return [
        {
            "key": key,
            "label": label,
            "label_en": label_en,
            "status": "pending",
            "started_at": None,
            "started_at_iso": "",
            "ended_at": None,
            "ended_at_iso": "",
        }
        for key, label, label_en in DEFAULT_STEPS.get(pipeline, [])
    ]


def step_message(step_key):
    return STEP_MESSAGES.get(step_key, ("正在运行。", "Running."))


def current_step_labels(payload):
    step_key = str((payload or {}).get("current_step") or "")
    for step in (payload or {}).get("steps", []) or []:
        if step.get("key") == step_key:
            label = str(step.get("label") or step_key or "未知阶段")
            label_en = str(step.get("label_en") or step.get("label") or step_key or "Unknown step")
            return label, label_en
    return step_key or "未知阶段", step_key or "Unknown step"


def attach_failure_hint(payload):
    if not isinstance(payload, dict):
        return payload
    if payload.get("status") != "failed":
        payload.pop("failure_hint", None)
        payload.pop("failure_hint_en", None)
        payload.pop("failure_code", None)
        payload.pop("failure_step_label", None)
        payload.pop("failure_step_label_en", None)
        return payload
    error_code = str(payload.get("error") or "").strip()
    exit_code = payload.get("exit_code")
    step_label, step_label_en = current_step_labels(payload)
    reason_zh, reason_en = FAILURE_HINTS.get(
        error_code,
        (
            "任务运行失败。",
            "The task failed.",
        ),
    )
    exit_zh = "退出码：{}。".format(exit_code) if exit_code not in (None, "", 0) else ""
    exit_en = "Exit code: {}. ".format(exit_code) if exit_code not in (None, "", 0) else ""
    retry_zh = "可以稍后点击“立即运行”重试；如果反复失败，请查看本地 OpenRelix 日志中的对应时间段。"
    retry_en = "You can click Run Now to retry later; if it keeps failing, check the matching time range in the local OpenRelix logs."
    payload["failure_code"] = error_code or ("exit_code_{}".format(exit_code) if exit_code not in (None, "", 0) else "")
    payload["failure_step_label"] = step_label
    payload["failure_step_label_en"] = step_label_en
    payload["failure_hint"] = "失败阶段：{}。{}{}{}".format(step_label, reason_zh, exit_zh, retry_zh)
    payload["failure_hint_en"] = "Failed step: {}. {} {}{}".format(step_label_en, reason_en, exit_en, retry_en)
    return payload


def start_run(pipeline, target_date="", stage="", run_id="", pid=None, paths=None):
    title, title_en = pipeline_label(pipeline)
    existing = load_status(paths)
    steps = default_steps(pipeline)
    current_step = steps[0]["key"] if steps else ""
    message, message_en = step_message(current_step)
    started = now_epoch()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id or "{}-{}-{}".format(pipeline, os.getpid(), uuid.uuid4().hex[:8]),
        "pipeline": pipeline,
        "title": title,
        "title_en": title_en,
        "status": "running",
        "target_date": str(target_date or ""),
        "stage": str(stage or ""),
        "pid": int(pid or os.getpid()),
        "current_step": current_step,
        "current_step_index": 1 if steps else 0,
        "step_count": len(steps),
        "message": message,
        "message_en": message_en,
        "started_at": started,
        "started_at_iso": now_iso(),
        "updated_at": started,
        "updated_at_iso": now_iso(),
        "ended_at": None,
        "ended_at_iso": "",
        "exit_code": None,
        "error": "",
        "steps": steps,
        "recent_runs": existing.get("recent_runs", []),
    }
    if current_step:
        return update_step(payload["run_id"], current_step, paths=paths, existing=payload)
    return write_status(payload, paths)


def update_step(run_id, step_key, message="", message_en="", paths=None, existing=None):
    payload = dict(existing or load_status(paths))
    if run_id and payload.get("run_id") and payload.get("run_id") != run_id:
        return payload
    steps = list(payload.get("steps") or [])
    step_index = 0
    current_time = now_epoch()
    for index, step in enumerate(steps, start=1):
        if step.get("key") == step_key:
            step_index = index
            step["status"] = "running"
            step["started_at"] = step.get("started_at") or current_time
            step["started_at_iso"] = step.get("started_at_iso") or now_iso()
            step["ended_at"] = None
            step["ended_at_iso"] = ""
        elif step_index == 0 and step.get("status") in {"pending", ""}:
            step["status"] = "pending"
        elif step_index and step.get("status") == "pending":
            step["status"] = "pending"
        elif step_index == 0 and step.get("status") == "running":
            step["status"] = "completed"
            step["ended_at"] = step.get("ended_at") or current_time
            step["ended_at_iso"] = step.get("ended_at_iso") or now_iso()
    default_message, default_message_en = step_message(step_key)
    payload.update({
        "status": "running",
        "current_step": step_key,
        "current_step_index": step_index,
        "step_count": len(steps),
        "message": message or default_message,
        "message_en": message_en or default_message_en,
        "updated_at": current_time,
        "updated_at_iso": now_iso(),
        "steps": steps,
    })
    return write_status(payload, paths)


def finish_run(run_id, status="completed", exit_code=0, error="", paths=None, existing=None):
    payload = dict(existing or load_status(paths))
    if run_id and payload.get("run_id") and payload.get("run_id") != run_id:
        return payload
    status = "completed" if str(status) == "completed" else "failed"
    current_time = now_epoch()
    for step in payload.get("steps", []) or []:
        if step.get("status") == "running":
            step["status"] = status
            step["ended_at"] = current_time
            step["ended_at_iso"] = now_iso()
        elif status == "completed" and step.get("status") == "pending":
            step["status"] = "completed"
            step["started_at"] = step.get("started_at") or current_time
            step["started_at_iso"] = step.get("started_at_iso") or now_iso()
            step["ended_at"] = step.get("ended_at") or current_time
            step["ended_at_iso"] = step.get("ended_at_iso") or now_iso()
    payload.update({
        "status": status,
        "message": "运行完成。" if status == "completed" else "运行失败。",
        "message_en": "Run completed." if status == "completed" else "Run failed.",
        "updated_at": current_time,
        "updated_at_iso": now_iso(),
        "ended_at": current_time,
        "ended_at_iso": now_iso(),
        "exit_code": exit_code,
        "error": str(error or "")[:160],
    })
    payload = attach_failure_hint(payload)
    recent_entry = {
        key: payload.get(key)
        for key in (
            "run_id",
            "pipeline",
            "title",
            "title_en",
            "status",
            "target_date",
            "stage",
            "started_at",
            "started_at_iso",
            "ended_at",
            "ended_at_iso",
            "exit_code",
        )
    }
    payload["recent_runs"] = _sanitize_recent_runs([recent_entry] + list(payload.get("recent_runs") or []))
    return write_status(payload, paths)
