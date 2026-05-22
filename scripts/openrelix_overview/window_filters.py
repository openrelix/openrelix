"""Shared work-window filtering rules."""

from pathlib import Path
import re


CLAUDE_MEM_OBSERVER_REASON = "claude_mem_observer_session"
KNOWLEDGE_AUTOMATION_REASON = "knowledge_automation_session"

CLAUDE_MEM_PATH_MARKERS = (
    ".claude-mem/observer-sessions",
    "claude-mem-observer-sessions",
)

CLAUDE_MEM_PROMPT_PREFIXES = (
    "you are a claude-mem",
    "hello memory agent",
)

KNOWLEDGE_AUTOMATION_TITLE_MARKERS = (
    "knowledge base",
    "知识库",
    "openviking",
    "byterag",
    "memory",
    "asset",
)


def compact_text(value):
    return " ".join(str(value or "").split())


def normalize_path_text(value):
    text = compact_text(value).replace("\\", "/")
    try:
        text = str(Path(text).expanduser()).replace("\\", "/")
    except (OSError, RuntimeError, ValueError):
        pass
    return text.lower()


def first_prompt_text(window):
    prompts = window.get("prompts", []) if isinstance(window, dict) else []
    if not isinstance(prompts, list):
        return ""
    for item in prompts:
        if isinstance(item, dict):
            text = compact_text(item.get("text", ""))
            if text:
                return text
    return ""


def window_metadata_text(window, raw_path=""):
    if not isinstance(window, dict):
        return normalize_path_text(raw_path)
    pieces = [
        raw_path,
        window.get("cwd", ""),
        window.get("session_file", ""),
        window.get("source", ""),
        window.get("originator", ""),
        window.get("window_summary", ""),
        window.get("thread_title", ""),
    ]
    claude_code = window.get("claude_code", {})
    if isinstance(claude_code, dict):
        pieces.extend(
            [
                claude_code.get("path", ""),
                claude_code.get("summary", ""),
            ]
        )
    app_server = window.get("app_server", {})
    if isinstance(app_server, dict):
        pieces.extend(
            [
                app_server.get("preview", ""),
                app_server.get("thread_source", ""),
            ]
        )
    return normalize_path_text(" ".join(str(piece or "") for piece in pieces))


def looks_like_claude_mem_observer_window(window, raw_path=""):
    metadata_text = window_metadata_text(window, raw_path=raw_path)
    if any(marker in metadata_text for marker in CLAUDE_MEM_PATH_MARKERS):
        return True

    prompt = compact_text(first_prompt_text(window)).lower()
    if any(prompt.startswith(prefix) for prefix in CLAUDE_MEM_PROMPT_PREFIXES):
        return True

    return "observed_from_primary_session" in prompt and "claude-mem" in prompt


def looks_like_knowledge_automation_window(window):
    prompt = compact_text(first_prompt_text(window)).lower()
    if not prompt.startswith("automation:") or "automation id:" not in prompt:
        return False
    title = prompt.split("automation id:", 1)[0]
    if re.search(r"\bkb\b", title):
        return True
    return any(marker in title for marker in KNOWLEDGE_AUTOMATION_TITLE_MARKERS)


def window_exclusion_reason(window, raw_path=""):
    if looks_like_knowledge_automation_window(window):
        return KNOWLEDGE_AUTOMATION_REASON
    if looks_like_claude_mem_observer_window(window, raw_path=raw_path):
        return CLAUDE_MEM_OBSERVER_REASON
    return ""


def is_excluded_window(window, raw_path=""):
    return bool(window_exclusion_reason(window, raw_path=raw_path))


def excluded_window_record(window, reason):
    return {
        "window_id": compact_text(window.get("window_id", "")),
        "reason": compact_text(reason),
        "ai_host": compact_text(window.get("ai_host", "")),
        "cwd": compact_text(window.get("cwd", "")),
        "source": compact_text(window.get("source", "")),
        "prompt_count": int(window.get("prompt_count", 0) or 0),
        "conclusion_count": int(window.get("conclusion_count", 0) or 0),
    }
