#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping, Optional

from asset_runtime import (
    atomic_write_json,
    atomic_write_text,
    codex_memory_state_path,
    codex_memory_windows_path,
    ensure_state_layout,
    get_codex_memory_root,
    get_runtime_paths,
    render_path,
)
import collect_codex_activity


PATHS = get_runtime_paths()
CODEX_MEMORY_DOCS_REGISTRY = "codex_memory_docs.jsonl"
CODEX_MEMORY_WINDOWS_REGISTRY = "codex_memory_windows.jsonl"
CRON_MARKER = "# openrelix-codex-memory-incremental"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def write_jsonl(path: Path, rows: Iterable[Mapping]) -> None:
    text = "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    atomic_write_text(path, text)


def compact_text(value, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit > 0 and len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def date_range(start: str, end: str) -> list[str]:
    start_date = datetime.fromisoformat(start).date()
    end_date = datetime.fromisoformat(end).date()
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


def date_from_epoch(value) -> str:
    try:
        return collect_codex_activity.local_date_from_epoch(int(value))
    except (TypeError, ValueError, OSError):
        return ""


def profile_codex_home(profile) -> Path:
    return collect_codex_activity.profile_codex_home(profile)


def profile_row(profile) -> dict:
    return {
        "codex_home": str(profile_codex_home(profile)),
        "electron_user_data_path": str(collect_codex_activity.profile_value(profile, "electron_user_data_path", "") or ""),
        "source": str(collect_codex_activity.profile_value(profile, "source", "") or ""),
    }


def default_profiles(paths=None):
    return collect_codex_activity.discover_codex_profiles()


def history_dates_for_profile(profile) -> set[str]:
    dates = set()
    history_path = profile_codex_home(profile) / "history.jsonl"
    if not history_path.exists():
        return dates
    try:
        lines = history_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return dates
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        date_str = date_from_epoch(item.get("ts"))
        if date_str:
            dates.add(date_str)
    return dates


def session_path_date(session_file: Path) -> str:
    parts = list(session_file.parts)
    for index, part in enumerate(parts):
        if part != "sessions" or index + 3 >= len(parts):
            continue
        year, month, day = parts[index + 1:index + 4]
        if re.fullmatch(r"\d{4}", year) and re.fullmatch(r"\d{2}", month) and re.fullmatch(r"\d{2}", day):
            candidate = "{}-{}-{}".format(year, month, day)
            try:
                datetime.fromisoformat(candidate)
            except ValueError:
                return ""
            return candidate
    return ""


def session_dates_for_profile(profile) -> set[str]:
    dates = set()
    sessions_dir = profile_codex_home(profile) / "sessions"
    if not sessions_dir.exists():
        return dates
    try:
        session_files = list(sessions_dir.rglob("*.jsonl"))
    except OSError:
        return dates
    for session_file in session_files:
        date_str = session_path_date(session_file)
        if date_str:
            dates.add(date_str)
    return dates


def app_server_dates_for_profile(profile, page_size=100, max_threads=500, timeout_seconds=15.0) -> set[str]:
    dates = set()
    try:
        with collect_codex_activity.CodexAppServerClient(timeout_seconds=timeout_seconds, profile=profile) as client:
            inspected_threads = 0
            cursor = None
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
                for thread in threads:
                    inspected_threads += 1
                    for key in ("createdAt", "updatedAt"):
                        date_str = date_from_epoch(thread.get(key))
                        if date_str:
                            dates.add(date_str)
                cursor = response.get("nextCursor")
                if not cursor:
                    break
    except (collect_codex_activity.AppServerError, OSError, subprocess.SubprocessError):
        return dates
    return dates


def discover_codex_memory_dates(paths=None, profiles=None, source: str = "auto") -> list[str]:
    profiles = list(profiles or default_profiles(paths))
    dates = set()
    for profile in profiles:
        dates.update(history_dates_for_profile(profile))
        dates.update(session_dates_for_profile(profile))
        if source in {"auto", "app-server"}:
            dates.update(app_server_dates_for_profile(profile))
    return sorted(dates)


def resolve_dates(
    *,
    dates: Optional[Iterable[str]] = None,
    from_date: str = "",
    to_date: str = "",
    days: int = 0,
    all_history: bool = False,
    source: str = "auto",
    paths=None,
    profiles=None,
) -> list[str]:
    explicit_dates = [str(item).strip()[:10] for item in (dates or []) if str(item).strip()]
    if explicit_dates:
        return sorted(dict.fromkeys(explicit_dates))
    if all_history:
        return discover_codex_memory_dates(paths=paths, profiles=profiles, source=source)
    today = datetime.now().astimezone().date()
    if from_date:
        end = to_date or today.isoformat()
        return date_range(from_date[:10], end[:10])
    if days and days > 0:
        start = today - timedelta(days=int(days) - 1)
        return date_range(start.isoformat(), today.isoformat())
    return [today.isoformat()]


def window_archive_id(window: Mapping) -> str:
    raw = json.dumps(
        {
            "ai_host": window.get("ai_host", "codex"),
            "window_id": window.get("window_id", ""),
            "date": window.get("date", ""),
            "codex_home": window.get("codex_home", ""),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def window_fingerprint(window: Mapping) -> str:
    raw = json.dumps(window, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def first_prompt_text(window: Mapping, limit=180) -> str:
    prompts = [item for item in window.get("prompts") or [] if isinstance(item, dict)]
    for prompt in prompts:
        text = compact_text(prompt.get("text"), limit=limit)
        if text:
            return text
    return ""


def first_conclusion_text(window: Mapping, limit=240) -> str:
    conclusions = [item for item in window.get("conclusions") or [] if isinstance(item, dict)]
    for conclusion in reversed(conclusions):
        text = compact_text(conclusion.get("text"), limit=limit)
        if text:
            return text
    return ""


def window_title(window: Mapping) -> str:
    for key in ("thread_title", "window_summary"):
        text = compact_text(window.get(key), limit=160)
        if text:
            return text
    prompt = first_prompt_text(window, limit=120)
    if prompt:
        return prompt
    return str(window.get("window_id") or "Codex window")


def archive_row_from_window(window: Mapping, generated_at: str) -> dict:
    archive_id = window_archive_id(window)
    title = window_title(window)
    return {
        "schema_version": 1,
        "archive_id": archive_id,
        "source": "codex_memory_archive",
        "ai_host": window.get("ai_host", "codex"),
        "date": window.get("date", ""),
        "window_id": window.get("window_id", ""),
        "resume_id": window.get("resume_id", "") or window.get("window_id", ""),
        "thread_id": window.get("thread_id", ""),
        "title": title,
        "prompt_preview": first_prompt_text(window),
        "conclusion_preview": first_conclusion_text(window),
        "cwd": window.get("cwd", ""),
        "codex_home": window.get("codex_home", ""),
        "codex_profile_source": window.get("codex_profile_source", ""),
        "codex_electron_user_data_path": window.get("codex_electron_user_data_path", ""),
        "session_file": window.get("session_file", ""),
        "started_at": window.get("started_at", ""),
        "prompt_count": int(window.get("prompt_count") or len(window.get("prompts") or [])),
        "conclusion_count": int(window.get("conclusion_count") or len(window.get("conclusions") or [])),
        "raw_conclusion_count": int(window.get("raw_conclusion_count") or 0),
        "review_like_window": bool(window.get("review_like_window")),
        "review_related_window": bool(window.get("review_related_window")),
        "fingerprint": window_fingerprint(window),
        "updated_at": generated_at,
    }


def row_sort_key(row: Mapping) -> tuple:
    return (
        str(row.get("date") or ""),
        str(row.get("started_at") or ""),
        str(row.get("archive_id") or ""),
    )


def merge_archive_rows(existing_rows: Iterable[Mapping], new_rows: Iterable[Mapping]) -> list[dict]:
    by_id = {}
    for row in existing_rows:
        archive_id = str(row.get("archive_id") or "")
        if archive_id:
            by_id[archive_id] = dict(row)
    for row in new_rows:
        archive_id = str(row.get("archive_id") or "")
        if archive_id:
            by_id[archive_id] = dict(row)
    return sorted(by_id.values(), key=row_sort_key)


def load_codex_windows_for_date(
    date_str: str,
    stage: str,
    profiles,
    source: str = "auto",
    page_size: int = 100,
    max_threads: int = 500,
    timeout_seconds: float = 15.0,
) -> tuple[list[dict], str, list[str]]:
    windows = []
    errors = []
    app_server_profile_count = 0
    fallback_profile_count = 0
    for profile in profiles:
        if source in {"auto", "app-server"}:
            try:
                windows.extend(
                    collect_codex_activity.load_app_server_windows_for_date(
                        date_str,
                        stage,
                        page_size=page_size,
                        max_threads=max_threads,
                        timeout_seconds=timeout_seconds,
                        profile=profile,
                    )
                )
                app_server_profile_count += 1
                continue
            except (collect_codex_activity.AppServerError, OSError, subprocess.SubprocessError) as exc:
                message = "{}: {}".format(
                    collect_codex_activity.codex_collection_error_prefix(profile),
                    collect_codex_activity.app_server_unavailable_message(exc, timeout_seconds),
                )
                errors.append(message)
                if source == "app-server":
                    continue
        windows.extend(collect_codex_activity.load_history_windows_for_date(date_str, stage, profile=profile))
        fallback_profile_count += 1
    if source == "history":
        collection_source = "history"
    elif fallback_profile_count and not app_server_profile_count:
        collection_source = "history_fallback"
    elif fallback_profile_count:
        collection_source = "mixed"
    else:
        collection_source = "app-server"
    return collect_codex_activity.dedupe_windows(windows), collection_source, errors


def state_relative_path(paths, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(paths.state_root.resolve()))
    except ValueError:
        return str(path.resolve())


def render_daily_markdown(date_str: str, rows: list[Mapping], profiles: list[Mapping], generated_at: str) -> str:
    lines = [
        "# Codex window memory {}".format(date_str),
        "",
        "Generated at: {}".format(generated_at),
        "",
        "## Summary",
        "",
        "- Windows: {}".format(len(rows)),
        "- Prompts: {}".format(sum(int(row.get("prompt_count") or 0) for row in rows)),
        "- Conclusions: {}".format(sum(int(row.get("conclusion_count") or 0) for row in rows)),
        "- Codex profiles: {}".format(len(profiles)),
        "",
        "## Profiles",
        "",
    ]
    if profiles:
        for profile in profiles:
            lines.append("- `{}` ({})".format(profile.get("codex_home", ""), profile.get("source", "") or "profile"))
    else:
        lines.append("- No Codex profiles were discovered.")
    lines.extend(["", "## Windows", ""])
    if not rows:
        lines.append("No Codex windows were found for this date.")
    for index, row in enumerate(rows, start=1):
        lines.extend(
            [
                "### {}. {}".format(index, row.get("title") or row.get("window_id") or "Window"),
                "",
                "- Window: `{}`".format(row.get("window_id", "")),
                "- CWD: `{}`".format(row.get("cwd", "") or "-"),
                "- Prompts: {} | Conclusions: {}".format(row.get("prompt_count", 0), row.get("conclusion_count", 0)),
            ]
        )
        if row.get("prompt_preview"):
            lines.extend(["", "Prompt preview:", "", "> {}".format(row["prompt_preview"])])
        if row.get("conclusion_preview"):
            lines.extend(["", "Conclusion preview:", "", "> {}".format(row["conclusion_preview"])])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def slug_text(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip().lower()).strip("-._")
    return text or "codex"


def project_label_from_cwd(cwd: str) -> str:
    return Path(str(cwd or "")).name or "Codex"


def project_key_from_cwd(cwd: str) -> str:
    return slug_text(project_label_from_cwd(cwd))


def rows_by_project(rows: Iterable[Mapping]) -> dict[str, list[Mapping]]:
    grouped: dict[str, list[Mapping]] = {}
    for row in rows:
        project_key = project_key_from_cwd(str(row.get("cwd") or ""))
        grouped.setdefault(project_key, []).append(row)
    return grouped


def render_project_markdown(date_str: str, project_key: str, rows: list[Mapping], generated_at: str) -> str:
    project_label = project_label_from_cwd(str(rows[0].get("cwd") or project_key)) if rows else project_key
    lines = [
        "# Codex project memory {} · {}".format(date_str, project_label),
        "",
        "Generated at: {}".format(generated_at),
        "",
        "## Project summary",
        "",
        "- Project: `{}`".format(project_label),
        "- Project key: `{}`".format(project_key),
        "- Windows: {}".format(len(rows)),
        "- Prompts: {}".format(sum(int(row.get("prompt_count") or 0) for row in rows)),
        "- Conclusions: {}".format(sum(int(row.get("conclusion_count") or 0) for row in rows)),
        "",
        "## LLM-ready evidence",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        lines.extend([
            "### {}. {}".format(index, row.get("title") or row.get("window_id") or "Window"),
            "",
            "- Window: `{}`".format(row.get("window_id", "")),
            "- CWD: `{}`".format(row.get("cwd", "") or "-"),
        ])
        if row.get("prompt_preview"):
            lines.extend(["", "Prompt:", "", "> {}".format(row["prompt_preview"])])
        if row.get("conclusion_preview"):
            lines.extend(["", "Conclusion:", "", "> {}".format(row["conclusion_preview"])])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def daily_doc_row(paths, date_str: str, rows: list[Mapping], profiles: list[Mapping], body_path: Path, generated_at: str) -> dict:
    window_ids = [str(row.get("window_id") or "") for row in rows if row.get("window_id")]
    archive_ids = [str(row.get("archive_id") or "") for row in rows if row.get("archive_id")]
    source_contexts = []
    for row in rows[:12]:
        source_contexts.append(
            {
                "source": "codex_memory_archive",
                "ai_host": row.get("ai_host", "codex"),
                "date": row.get("date", date_str),
                "window_id": row.get("window_id", ""),
                "title": row.get("title", ""),
                "project_label": Path(str(row.get("cwd") or "")).name or "Codex",
                "main_takeaway": row.get("conclusion_preview") or row.get("prompt_preview") or "",
            }
        )
    return {
        "schema_version": 1,
        "algorithm_version": 1,
        "doc_id": "codex-memory-{}".format(date_str),
        "version": 1,
        "status": "draft",
        "summary_type": "codex_memory_archive",
        "knowledge_type": "codex_memory_archive",
        "title": "Codex window memory {}".format(date_str),
        "summary": "{} Codex windows archived from {} profiles.".format(len(rows), len(profiles)),
        "body_path": state_relative_path(paths, body_path),
        "source_refs": {
            "summary_dates": [date_str],
            "window_ids": window_ids,
            "archive_ids": archive_ids,
            "profile_homes": [str(profile.get("codex_home") or "") for profile in profiles],
        },
        "source_contexts": source_contexts,
        "source_range": {"from": date_str, "to": date_str},
        "project_key": "codex",
        "project_label": "Codex",
        "scope": "local",
        "reviewer_state": "needs_review",
        "visibility": {"panel": True, "trust_level": "draft"},
        "updated_at": generated_at,
    }


def project_doc_row(paths, date_str: str, project_key: str, rows: list[Mapping], body_path: Path, generated_at: str) -> dict:
    project_label = project_label_from_cwd(str(rows[0].get("cwd") or project_key)) if rows else project_key
    window_ids = [str(row.get("window_id") or "") for row in rows if row.get("window_id")]
    archive_ids = [str(row.get("archive_id") or "") for row in rows if row.get("archive_id")]
    source_contexts = []
    for row in rows[:12]:
        source_contexts.append({
            "source": "codex_memory_project_summary",
            "ai_host": row.get("ai_host", "codex"),
            "date": row.get("date", date_str),
            "window_id": row.get("window_id", ""),
            "title": row.get("title", ""),
            "project_label": project_label,
            "main_takeaway": row.get("conclusion_preview") or row.get("prompt_preview") or "",
        })
    return {
        "schema_version": 1,
        "algorithm_version": 1,
        "doc_id": "codex-memory-{}-project-{}".format(date_str, project_key),
        "version": 1,
        "status": "draft",
        "summary_type": "codex_memory_project_summary",
        "knowledge_type": "codex_memory_project_summary",
        "title": "Codex project memory {} · {}".format(date_str, project_label),
        "summary": "{} Codex windows summarized for project {}.".format(len(rows), project_label),
        "body_path": state_relative_path(paths, body_path),
        "source_refs": {
            "summary_dates": [date_str],
            "window_ids": window_ids,
            "archive_ids": archive_ids,
            "project_keys": [project_key],
        },
        "source_contexts": source_contexts,
        "source_range": {"from": date_str, "to": date_str},
        "project_key": project_key,
        "project_label": project_label,
        "scope": "project",
        "reviewer_state": "needs_review",
        "visibility": {"panel": True, "trust_level": "draft"},
        "updated_at": generated_at,
    }


def write_daily_outputs(paths, date_str: str, windows: list[Mapping], rows: list[Mapping], profiles: list[Mapping], generated_at: str) -> dict:
    root = get_codex_memory_root(paths)
    daily_path = root / "daily" / "{}.json".format(date_str)
    body_path = root / "docs" / "{}.md".format(date_str)
    daily_payload = {
        "schema_version": 1,
        "date": date_str,
        "generated_at": generated_at,
        "window_count": len(windows),
        "prompt_count": sum(int(window.get("prompt_count") or 0) for window in windows),
        "conclusion_count": sum(int(window.get("conclusion_count") or 0) for window in windows),
        "profiles": profiles,
        "windows": windows,
        "archive_rows": rows,
    }
    atomic_write_json(daily_path, daily_payload)
    atomic_write_text(body_path, render_daily_markdown(date_str, rows, profiles, generated_at))
    return daily_doc_row(paths, date_str, rows, profiles, body_path, generated_at)


def write_project_outputs(paths, date_str: str, rows: list[Mapping], generated_at: str) -> list[dict]:
    root = get_codex_memory_root(paths)
    docs = []
    for project_key, project_rows in sorted(rows_by_project(rows).items()):
        body_path = root / "docs" / "projects" / project_key / "{}.md".format(date_str)
        atomic_write_text(body_path, render_project_markdown(date_str, project_key, list(project_rows), generated_at))
        docs.append(project_doc_row(paths, date_str, project_key, list(project_rows), body_path, generated_at))
    return docs


def sync_codex_memory_archive(
    *,
    paths=None,
    dates: Iterable[str],
    profiles=None,
    stage: str = "final",
    source: str = "auto",
) -> dict:
    paths = ensure_state_layout(paths or PATHS)
    generated_at = now_iso()
    profiles = list(profiles or default_profiles(paths))
    profile_rows = [profile_row(profile) for profile in profiles]
    all_new_rows = []
    daily_docs = []
    daily_results = []

    for date_str in sorted(dict.fromkeys(str(item)[:10] for item in dates if str(item).strip())):
        windows, collection_source, collection_errors = load_codex_windows_for_date(
            date_str,
            stage,
            profiles,
            source=source,
        )
        rows = [archive_row_from_window(window, generated_at) for window in windows]
        all_new_rows.extend(rows)
        doc = write_daily_outputs(paths, date_str, windows, rows, profile_rows, generated_at)
        daily_docs.append(doc)
        daily_docs.extend(write_project_outputs(paths, date_str, rows, generated_at))
        daily_results.append(
            {
                "date": date_str,
                "window_count": len(windows),
                "prompt_count": sum(int(window.get("prompt_count") or 0) for window in windows),
                "conclusion_count": sum(int(window.get("conclusion_count") or 0) for window in windows),
                "collection_source": collection_source,
                "collection_errors": collection_errors,
                "doc_id": doc["doc_id"],
                "body_path": doc["body_path"],
            }
        )

    windows_path = codex_memory_windows_path(paths)
    merged_rows = merge_archive_rows(read_jsonl(windows_path), all_new_rows)
    write_jsonl(windows_path, merged_rows)
    write_jsonl(paths.registry_dir / CODEX_MEMORY_WINDOWS_REGISTRY, merged_rows)

    existing_docs = [
        row
        for row in read_jsonl(paths.registry_dir / CODEX_MEMORY_DOCS_REGISTRY)
        if str(row.get("doc_id") or "") not in {doc["doc_id"] for doc in daily_docs}
    ]
    merged_docs = sorted(existing_docs + daily_docs, key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    write_jsonl(paths.registry_dir / CODEX_MEMORY_DOCS_REGISTRY, merged_docs)

    state = read_json(codex_memory_state_path(paths))
    state.update(
        {
            "schema_version": 1,
            "updated_at": generated_at,
            "codex_memory_root": str(get_codex_memory_root(paths)),
            "archive_window_count": len(merged_rows),
            "doc_count": len(merged_docs),
            "last_dates": [item["date"] for item in daily_results],
            "last_date": daily_results[-1]["date"] if daily_results else state.get("last_date", ""),
            "last_window_count": sum(item["window_count"] for item in daily_results),
            "profiles": profile_rows,
            "activity_source": source,
        }
    )
    atomic_write_json(codex_memory_state_path(paths), state)
    return {
        "codex_memory_root": str(get_codex_memory_root(paths)),
        "windows_path": str(windows_path),
        "windows_registry": str(paths.registry_dir / CODEX_MEMORY_WINDOWS_REGISTRY),
        "docs_registry": str(paths.registry_dir / CODEX_MEMORY_DOCS_REGISTRY),
        "dates": daily_results,
        "date_count": len(daily_results),
        "synced_window_count": sum(item["window_count"] for item in daily_results),
        "archive_window_count": len(merged_rows),
        "doc_count": len(merged_docs),
        "profiles": profile_rows,
        "activity_source": source,
        "generated_at": generated_at,
    }


def codex_memory_status(paths=None) -> dict:
    paths = ensure_state_layout(paths or PATHS)
    root = get_codex_memory_root(paths)
    rows = read_jsonl(codex_memory_windows_path(paths))
    docs = read_jsonl(paths.registry_dir / CODEX_MEMORY_DOCS_REGISTRY)
    dates = sorted({str(row.get("date") or "") for row in rows if row.get("date")})
    profiles = {}
    for row in rows:
        home = str(row.get("codex_home") or "")
        if home:
            profiles[home] = profiles.get(home, 0) + 1
    state = read_json(codex_memory_state_path(paths))
    return {
        "codex_memory_root": str(root),
        "windows_path": str(codex_memory_windows_path(paths)),
        "state_path": str(codex_memory_state_path(paths)),
        "archive_window_count": len(rows),
        "doc_count": len(docs),
        "date_count": len(dates),
        "first_date": dates[0] if dates else "",
        "last_date": dates[-1] if dates else "",
        "profile_count": len(profiles),
        "profiles": [{"codex_home": key, "window_count": value} for key, value in sorted(profiles.items())],
        "last_sync": state.get("updated_at", ""),
        "env_hint": "Set OPENRELIX_CODEX_MEMORY_ROOT={} in another environment to read the same archive.".format(root),
    }


def incremental_dates(paths=None, profiles=None, days: int = 2, lookback_days: int = 1) -> list[str]:
    paths = paths or PATHS
    today = datetime.now().astimezone().date()
    state = read_json(codex_memory_state_path(paths))
    last_date = str(state.get("last_date") or "")[:10]
    if last_date:
        try:
            start = datetime.fromisoformat(last_date).date() - timedelta(days=max(0, int(lookback_days)))
        except ValueError:
            start = today - timedelta(days=max(1, int(days)) - 1)
    else:
        start = today - timedelta(days=max(1, int(days)) - 1)
    return date_range(start.isoformat(), today.isoformat())


def sync_incremental(paths=None, profiles=None, stage: str = "preliminary", days: int = 2, lookback_days: int = 1, source: str = "auto") -> dict:
    paths = paths or PATHS
    dates = incremental_dates(paths=paths, profiles=profiles, days=days, lookback_days=lookback_days)
    return sync_codex_memory_archive(paths=paths, dates=dates, profiles=profiles, stage=stage, source=source)


def cron_command(paths, *, interval_minutes: int, stage: str, days: int, lookback_days: int, source: str = "auto", summarize: bool = True) -> str:
    minute = "*/{}".format(max(1, int(interval_minutes)))
    summarize_flag = " --summarize" if summarize else ""
    command = (
        "cd {repo} && {python} scripts/openrelix.py codex-memory incremental "
        "--stage {stage} --activity-source {source} --days {days} --lookback-days {lookback_days}{summarize_flag} --refresh "
        ">> {log} 2>&1 {marker}"
    ).format(
        repo=sh_quote(str(paths.repo_root)),
        python=sh_quote(sys.executable or "python3"),
        stage=sh_quote(stage),
        source=sh_quote(source),
        days=int(days),
        lookback_days=int(lookback_days),
        summarize_flag=summarize_flag,
        log=sh_quote(str(paths.log_dir / "codex-memory-incremental.log")),
        marker=CRON_MARKER,
    )
    return "{} * * * * {}".format(minute, command)


def sh_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def install_cron_schedule(
    *,
    paths=None,
    interval_minutes: int = 15,
    stage: str = "preliminary",
    days: int = 2,
    lookback_days: int = 1,
    source: str = "auto",
    summarize: bool = True,
    install: bool = False,
) -> dict:
    paths = ensure_state_layout(paths or PATHS)
    entry = cron_command(
        paths,
        interval_minutes=interval_minutes,
        stage=stage,
        days=days,
        lookback_days=lookback_days,
        source=source,
        summarize=summarize,
    )
    schedule_path = get_codex_memory_root(paths) / "incremental.cron"
    atomic_write_text(schedule_path, entry + "\n")
    payload = {
        "scheduler": "cron",
        "installed": False,
        "crontab_available": bool(shutil.which("crontab")),
        "entry": entry,
        "schedule_path": str(schedule_path),
    }
    if not install:
        return payload
    if not shutil.which("crontab"):
        payload["error"] = "crontab command not found; install manually from schedule_path."
        return payload
    current = subprocess.run(["crontab", "-l"], text=True, capture_output=True, check=False)
    existing_lines = []
    if current.returncode == 0:
        existing_lines = current.stdout.splitlines()
    kept_lines = [line for line in existing_lines if CRON_MARKER not in line]
    kept_lines.append(entry)
    new_crontab = "\n".join(kept_lines).rstrip() + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    payload["installed"] = True
    return payload


def parse_args():
    parser = argparse.ArgumentParser(description="Archive Codex windows into a fixed OpenRelix memory root.")
    parser.add_argument("action", choices=["status", "sync", "backfill", "incremental", "schedule"], nargs="?", default="status")
    parser.add_argument("--date", action="append", dest="dates", default=[])
    parser.add_argument("--from", dest="from_date", default="")
    parser.add_argument("--to", dest="to_date", default="")
    parser.add_argument("--days", type=int, default=0)
    parser.add_argument("--all-history", action="store_true")
    parser.add_argument("--stage", choices=["manual", "preliminary", "final"], default="final")
    parser.add_argument("--activity-source", choices=["history", "app-server", "auto"], default="auto")
    parser.add_argument("--lookback-days", type=int, default=1)
    parser.add_argument("--interval-minutes", type=int, default=15)
    parser.add_argument("--summarize", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    paths = ensure_state_layout(PATHS)
    if args.action == "status":
        payload = codex_memory_status(paths=paths)
    elif args.action in {"sync", "backfill"}:
        dates = resolve_dates(
            dates=args.dates,
            from_date=args.from_date,
            to_date=args.to_date,
            days=args.days,
            all_history=args.all_history,
            source=args.activity_source,
            paths=paths,
        )
        payload = sync_codex_memory_archive(paths=paths, dates=dates, stage=args.stage, source=args.activity_source)
    elif args.action == "incremental":
        payload = sync_incremental(
            paths=paths,
            stage=args.stage,
            days=args.days or 2,
            lookback_days=args.lookback_days,
            source=args.activity_source,
        )
    else:
        payload = install_cron_schedule(
            paths=paths,
            interval_minutes=args.interval_minutes,
            stage=args.stage,
            days=args.days or 2,
            lookback_days=args.lookback_days,
            source=args.activity_source,
            summarize=args.summarize,
            install=args.install,
        )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for key, value in payload.items():
            print("{}: {}".format(key, value))


if __name__ == "__main__":
    main()
