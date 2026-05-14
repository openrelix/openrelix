"""User feedback helpers for OpenRelix managed memory rows."""

from datetime import datetime
import json

from asset_runtime import atomic_write_text

from .memory_registry import build_memory_group_key


MEMORY_FEEDBACK_FILE = "memory_feedback.jsonl"
FEEDBACK_LIKED = "liked"
FEEDBACK_DOWNVOTED = "downvoted"
FEEDBACK_NEUTRAL = "neutral"
FEEDBACK_VALUES = {
    FEEDBACK_LIKED,
    FEEDBACK_DOWNVOTED,
    FEEDBACK_NEUTRAL,
}
POSITIVE_FEEDBACK = {FEEDBACK_LIKED}


def current_timestamp(now=None):
    return (now or datetime.now().astimezone()).isoformat()


def memory_feedback_path(paths):
    return paths.registry_dir / MEMORY_FEEDBACK_FILE


def normalize_feedback(value):
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "pin": FEEDBACK_LIKED,
        "pinned": FEEDBACK_LIKED,
        "固定": FEEDBACK_LIKED,
        "钉住": FEEDBACK_LIKED,
        "like": FEEDBACK_LIKED,
        "liked": FEEDBACK_LIKED,
        "up": FEEDBACK_LIKED,
        "upvote": FEEDBACK_LIKED,
        "赞": FEEDBACK_LIKED,
        "有用": FEEDBACK_LIKED,
        "down": FEEDBACK_DOWNVOTED,
        "downvote": FEEDBACK_DOWNVOTED,
        "downvoted": FEEDBACK_DOWNVOTED,
        "踩": FEEDBACK_DOWNVOTED,
        "无用": FEEDBACK_DOWNVOTED,
        "clear": FEEDBACK_NEUTRAL,
        "none": FEEDBACK_NEUTRAL,
        "neutral": FEEDBACK_NEUTRAL,
        "取消": FEEDBACK_NEUTRAL,
    }
    return aliases.get(text, text if text in FEEDBACK_VALUES else "")


def memory_key_for_record(item):
    if not isinstance(item, dict):
        return ""
    return str(item.get("memory_key") or build_memory_group_key(item) or "").strip()


def load_memory_feedback_rows(paths):
    path = memory_feedback_path(paths)
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
        memory_key = str(item.get("memory_key") or "").strip()
        feedback = normalize_feedback(item.get("feedback"))
        if not memory_key or not feedback:
            continue
        current = dict(item)
        current["memory_key"] = memory_key
        current["feedback"] = feedback
        rows.append(current)
    return rows


def load_memory_feedback_map(paths):
    feedback_by_key = {}
    for row in load_memory_feedback_rows(paths):
        feedback_by_key[row["memory_key"]] = row
    return feedback_by_key


def append_memory_feedback(paths, memory_key, feedback, title="", source="panel", now=None):
    normalized = normalize_feedback(feedback)
    if not normalized:
        raise ValueError("invalid_memory_feedback")
    key = str(memory_key or "").strip()
    if not key:
        raise ValueError("missing_memory_key")
    row = {
        "memory_key": key,
        "feedback": normalized,
        "title": str(title or "").strip()[:180],
        "source": str(source or "panel").strip() or "panel",
        "updated_at": current_timestamp(now=now),
    }
    path = memory_feedback_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    try:
        existing = path.read_text(encoding="utf-8").rstrip()
    except (OSError, UnicodeDecodeError):
        existing = ""
    payload = json.dumps(row, ensure_ascii=False)
    atomic_write_text(path, "\n".join(part for part in (existing, payload) if part) + "\n")
    return row


def clear_memory_feedback(item, feedback=None):
    if not isinstance(item, dict):
        return item
    row = dict(item)
    row["user_feedback"] = ""
    row["user_feedback_updated_at"] = (
        str(feedback.get("updated_at") or "") if isinstance(feedback, dict) else ""
    )
    row["user_pinned"] = False
    return row


def apply_memory_feedback(item, feedback=None):
    if not isinstance(item, dict):
        return item
    if not feedback:
        return item
    state = normalize_feedback(feedback.get("feedback") if isinstance(feedback, dict) else feedback)
    if not state:
        return item
    if state == FEEDBACK_NEUTRAL:
        return clear_memory_feedback(item, feedback)

    row = dict(item)
    row["memory_key"] = memory_key_for_record(item)
    row["user_feedback"] = state
    row["user_feedback_updated_at"] = (
        str(feedback.get("updated_at") or "") if isinstance(feedback, dict) else ""
    )

    if state == FEEDBACK_DOWNVOTED:
        row["bucket"] = "low_priority"
        row["priority"] = "low"
        row["scope"] = "local"
        row["injection_policy"] = "local_only"
        row["global_context_approved"] = False
        row["host_context_approved"] = False
        return row

    row["priority"] = "high"
    row["bucket"] = "durable"
    row["global_context_approved"] = True
    row["host_context_approved"] = True
    row["user_pinned"] = False
    current_policy = str(row.get("injection_policy") or "").strip()
    if current_policy in {"", "on_demand", "local_only", "never"}:
        scope = str(row.get("scope") or "").strip()
        if scope in {"project", "repo", "host"} or row.get("project_key") or row.get("project_label"):
            row["injection_policy"] = "project_context"
        else:
            row["scope"] = scope or "global"
            row["injection_policy"] = "global_context"
    return row


def apply_memory_feedback_map(item, feedback_by_key):
    key = memory_key_for_record(item)
    if not key:
        return item
    return apply_memory_feedback(item, (feedback_by_key or {}).get(key))
