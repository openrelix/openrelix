"""User actions for the daily work asset desk."""

from datetime import datetime
import json

from asset_runtime import atomic_write_text


WORK_ASSET_FEEDBACK_FILE = "work_asset_feedback.jsonl"
WORK_ASSET_FEEDBACK_PATH = "/work-asset-feedback"

ACTION_CAPTURE = "capture"
ACTION_MERGE = "merge"
ACTION_IGNORE = "ignore"
ACTION_REVIEW = "review"
ACTION_DONE = "done"
ACTION_SNOOZE = "snooze"

STATE_PENDING = "pending_review"
STATE_RESOLVED = "resolved"
STATE_MERGE_SUGGESTED = "merge_suggested"
STATE_IGNORED = "ignored"
STATE_DONE = "done"
STATE_SNOOZED = "snoozed"

ACTION_TO_STATE = {
    ACTION_CAPTURE: STATE_RESOLVED,
    ACTION_MERGE: STATE_MERGE_SUGGESTED,
    ACTION_IGNORE: STATE_IGNORED,
    ACTION_REVIEW: STATE_PENDING,
    ACTION_DONE: STATE_DONE,
    ACTION_SNOOZE: STATE_SNOOZED,
}

STATE_VALUES = {
    STATE_PENDING,
    STATE_RESOLVED,
    STATE_MERGE_SUGGESTED,
    STATE_IGNORED,
    STATE_DONE,
    STATE_SNOOZED,
}


def current_timestamp(now=None):
    return (now or datetime.now().astimezone()).isoformat()


def work_asset_feedback_path(paths):
    return paths.registry_dir / WORK_ASSET_FEEDBACK_FILE


def normalize_action(value):
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "沉淀": ACTION_CAPTURE,
        "capture": ACTION_CAPTURE,
        "captured": ACTION_CAPTURE,
        "resolve": ACTION_CAPTURE,
        "resolved": ACTION_CAPTURE,
        "合并": ACTION_MERGE,
        "merge": ACTION_MERGE,
        "merge_suggested": ACTION_MERGE,
        "忽略": ACTION_IGNORE,
        "ignore": ACTION_IGNORE,
        "ignored": ACTION_IGNORE,
        "待确认": ACTION_REVIEW,
        "review": ACTION_REVIEW,
        "pending": ACTION_REVIEW,
        "pending_review": ACTION_REVIEW,
        "完成": ACTION_DONE,
        "done": ACTION_DONE,
        "complete": ACTION_DONE,
        "completed": ACTION_DONE,
        "稍后": ACTION_SNOOZE,
        "snooze": ACTION_SNOOZE,
        "snoozed": ACTION_SNOOZE,
    }
    return aliases.get(text, text if text in ACTION_TO_STATE else "")


def normalize_action_state(value):
    text = str(value or "").strip().lower().replace("-", "_")
    return text if text in STATE_VALUES else ""


def state_for_action(action):
    normalized = normalize_action(action)
    return ACTION_TO_STATE.get(normalized, "")


def load_work_asset_feedback_rows(paths):
    path = work_asset_feedback_path(paths)
    rows = []
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return rows
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id") or "").strip()
        action = normalize_action(item.get("action"))
        state = normalize_action_state(item.get("state")) or state_for_action(action)
        if not candidate_id or not action or not state:
            continue
        current = dict(item)
        current["candidate_id"] = candidate_id
        current["action"] = action
        current["state"] = state
        rows.append(current)
    return rows


def load_work_asset_feedback_map(paths):
    feedback_by_id = {}
    for row in load_work_asset_feedback_rows(paths):
        feedback_by_id[row["candidate_id"]] = row
    return feedback_by_id


def append_work_asset_feedback(
    paths,
    candidate_id,
    action,
    title="",
    kind="",
    project_key="",
    source_outcome_id="",
    source_window_ids=None,
    source="panel",
    now=None,
):
    normalized_action = normalize_action(action)
    state = state_for_action(normalized_action)
    if not normalized_action or not state:
        raise ValueError("invalid_work_asset_action")
    key = str(candidate_id or "").strip()
    if not key:
        raise ValueError("missing_work_asset_candidate_id")
    if isinstance(source_window_ids, str):
        source_window_ids = [item.strip() for item in source_window_ids.split(",")]
    row = {
        "candidate_id": key,
        "action": normalized_action,
        "state": state,
        "title": str(title or "").strip()[:180],
        "kind": str(kind or "").strip()[:64],
        "project_key": str(project_key or "").strip()[:96],
        "source_outcome_id": str(source_outcome_id or "").strip()[:160],
        "source_window_ids": [
            str(item or "").strip()[:160]
            for item in (source_window_ids or [])
            if str(item or "").strip()
        ][:12],
        "source": str(source or "panel").strip() or "panel",
        "updated_at": current_timestamp(now=now),
    }
    path = work_asset_feedback_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    try:
        existing = path.read_text(encoding="utf-8").rstrip()
    except (OSError, UnicodeDecodeError):
        existing = ""
    payload = json.dumps(row, ensure_ascii=False)
    atomic_write_text(path, "\n".join(part for part in (existing, payload) if part) + "\n")
    return row
