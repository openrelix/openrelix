#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from asset_runtime import ensure_state_layout, get_runtime_paths


SCHEMA_VERSION = 3
DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class IndexStats:
    db_path: str
    rebuilt_at: str
    fts_enabled: bool
    memory_rows: int
    window_rows: int
    skipped_memory_rows: int
    skipped_daily_files: int
    skipped_window_files: int
    skipped_summary_files: int
    daily_summary_rows: int
    source_file_rows: int
    source_fingerprint: str

    def to_dict(self):
        return {
            "db_path": self.db_path,
            "rebuilt_at": self.rebuilt_at,
            "fts_enabled": self.fts_enabled,
            "memory_rows": self.memory_rows,
            "window_rows": self.window_rows,
            "skipped_memory_rows": self.skipped_memory_rows,
            "skipped_daily_files": self.skipped_daily_files,
            "skipped_window_files": self.skipped_window_files,
            "skipped_summary_files": self.skipped_summary_files,
            "daily_summary_rows": self.daily_summary_rows,
            "source_file_rows": self.source_file_rows,
            "source_fingerprint": self.source_fingerprint,
        }


def default_db_path(paths=None):
    paths = paths or get_runtime_paths()
    return paths.runtime_dir / "openrelix-index.sqlite3"


def db_sidecar_paths(db_path):
    db_path = Path(db_path)
    return [Path("{}{}".format(db_path, suffix)) for suffix in ("-wal", "-shm")]


def cleanup_db_files(db_path):
    for path in [Path(db_path), *db_sidecar_paths(db_path)]:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def cleanup_db_sidecars(db_path):
    for path in db_sidecar_paths(db_path):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def compact_text(value):
    return " ".join(str(value or "").split())


def json_dumps(value):
    return json.dumps(value if value is not None else [], ensure_ascii=False, sort_keys=True)


def normalize_summary_pairs(raw_pairs, question_summary="", main_takeaway=""):
    pairs = []
    if isinstance(raw_pairs, list):
        for raw_pair in raw_pairs:
            if not isinstance(raw_pair, dict):
                continue
            question = compact_text(raw_pair.get("question", "") or raw_pair.get("problem", ""))
            conclusion = compact_text(raw_pair.get("conclusion", "") or raw_pair.get("takeaway", ""))
            if question or conclusion:
                pairs.append({"question": question, "conclusion": conclusion})
    if pairs:
        return pairs
    question_summary = compact_text(question_summary)
    main_takeaway = compact_text(main_takeaway)
    if question_summary or main_takeaway:
        return [{"question": question_summary, "conclusion": main_takeaway}]
    return []


def window_record_sort_key(item):
    if not isinstance(item, dict):
        return ""
    return str(
        item.get("local_time")
        or item.get("completed_at")
        or item.get("timestamp")
        or item.get("ts")
        or ""
    )


def raw_window_summary_pairs(prompts, conclusions, limit=6):
    if not isinstance(prompts, list):
        prompts = []
    if not isinstance(conclusions, list):
        conclusions = []
    prompt_items = sorted(
        [item for item in prompts if isinstance(item, dict)],
        key=window_record_sort_key,
    )
    conclusion_items = sorted(
        [item for item in conclusions if isinstance(item, dict)],
        key=window_record_sort_key,
    )
    prompt_by_turn = {
        str(item.get("turn_id", "")): item
        for item in prompt_items
        if str(item.get("turn_id", "")).strip()
    }
    conclusion_by_turn = {
        str(item.get("turn_id", "")): item
        for item in conclusion_items
        if str(item.get("turn_id", "")).strip()
    }
    matched_turn_ids = [
        str(item.get("turn_id", ""))
        for item in prompt_items
        if str(item.get("turn_id", "")).strip() in conclusion_by_turn
    ]
    if matched_turn_ids:
        pairs = []
        seen_turn_ids = set()
        for turn_id in matched_turn_ids:
            if turn_id in seen_turn_ids:
                continue
            seen_turn_ids.add(turn_id)
            question = compact_text(prompt_by_turn.get(turn_id, {}).get("text", ""))
            answer = compact_text(conclusion_by_turn.get(turn_id, {}).get("text", ""))
            if question or answer:
                pairs.append({"question": question, "conclusion": answer})
            if len(pairs) >= limit:
                return pairs
        if pairs:
            return pairs
    row_count = min(max(len(prompt_items), len(conclusion_items)), limit)
    pairs = []
    for index in range(row_count):
        prompt = prompt_items[index] if index < len(prompt_items) else {}
        conclusion = (
            conclusion_items[index]
            if index < len(conclusion_items)
            else {}
        )
        question = compact_text(prompt.get("text", ""))
        answer = compact_text(conclusion.get("text", ""))
        if question or answer:
            pairs.append({"question": question, "conclusion": answer})
    return pairs


def summary_model_completed(summary):
    status = compact_text(
        (summary or {}).get("model_status")
        or (summary or {}).get("last_run_model_status", "")
    ).lower()
    return status not in {"failed", "error", "fallback"}


def normalize_search_key(text):
    compact = compact_text(text).lower()
    compact = re.sub(r"`+", "", compact)
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", compact)
    return compact.strip()


def memory_group_key(item):
    explicit = compact_text(item.get("memory_key", ""))
    if explicit:
        return explicit
    bucket = compact_text(item.get("bucket", ""))
    memory_type = compact_text(item.get("memory_type", ""))
    title = compact_text(
        item.get("title")
        or item.get("title_zh")
        or item.get("title_en")
        or item.get("value_note")
        or item.get("value_note_zh")
        or item.get("value_note_en")
    )
    return normalize_search_key("{} {} {}".format(bucket, memory_type, title))


def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def read_json_file(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def iter_jsonl(path):
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            yield line_no, None
            continue
        yield line_no, payload if isinstance(payload, dict) else None


def source_file_kind(paths, path):
    if path == paths.registry_dir / "memory_items.jsonl":
        return "memory_items"
    try:
        path.relative_to(paths.raw_daily_dir)
        return "raw_daily"
    except ValueError:
        pass
    try:
        path.relative_to(paths.raw_windows_dir)
        return "raw_window"
    except ValueError:
        pass
    try:
        path.relative_to(paths.consolidated_daily_dir)
        return "daily_summary"
    except ValueError:
        return "unknown"


def collect_source_files(paths):
    candidates = [paths.registry_dir / "memory_items.jsonl"]
    if paths.raw_daily_dir.exists():
        candidates.extend(sorted(paths.raw_daily_dir.glob("*.json")))
    if paths.raw_windows_dir.exists():
        candidates.extend(sorted(paths.raw_windows_dir.glob("*/*.json")))
    if paths.consolidated_daily_dir.exists():
        candidates.extend(sorted(paths.consolidated_daily_dir.glob("*/summary.json")))

    rows = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append(
            {
                "path": str(path),
                "kind": source_file_kind(paths, path),
                "mtime_ns": int(stat.st_mtime_ns),
                "size_bytes": int(stat.st_size),
            }
        )
    return rows


def source_fingerprint(source_files):
    digest = hashlib.sha256()
    for item in sorted(source_files, key=lambda row: row["path"]):
        digest.update(item["path"].encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(item["kind"].encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(str(item["mtime_ns"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(item["size_bytes"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def latest_activity_at(window):
    candidates = []
    for item in window.get("prompts", []):
        candidates.append(item.get("local_time", ""))
    for item in window.get("conclusions", []):
        candidates.append(item.get("completed_at", ""))
    if window.get("started_at"):
        candidates.append(window.get("started_at"))
    return max((compact_text(item) for item in candidates if compact_text(item)), default="")


def infer_project_label(cwd):
    text = compact_text(cwd)
    if not text:
        return ""
    path = Path(text)
    name = path.name or str(path)
    if name in {"", "/", str(Path.home())}:
        return ""
    return name


def summary_maps(paths):
    by_window_key = {}
    daily_rows = []
    skipped = 0
    root = paths.consolidated_daily_dir
    if not root.exists():
        return by_window_key, daily_rows, skipped
    for path in sorted(root.glob("*/summary.json")):
        payload = read_json_file(path)
        if not isinstance(payload, dict):
            skipped += 1
            continue
        date_str = compact_text(payload.get("date", path.parent.name))
        stage = compact_text(payload.get("stage", ""))
        model_status = compact_text(payload.get("model_status") or payload.get("last_run_model_status", ""))
        keywords = payload.get("keywords", [])
        if not isinstance(keywords, list):
            keywords = []
        next_actions = payload.get("next_actions", [])
        if not isinstance(next_actions, list):
            next_actions = []
        daily_rows.append(
            {
                "date": date_str,
                "language": compact_text(payload.get("language", "")),
                "stage": stage,
                "generated_at": compact_text(payload.get("generated_at", "")),
                "day_summary": compact_text(payload.get("day_summary", "")),
                "keywords_json": json_dumps(keywords),
                "next_actions_json": json_dumps(next_actions),
                "raw_window_count": safe_int(payload.get("raw_window_count", 0)),
                "review_like_window_count": safe_int(payload.get("review_like_window_count", 0)),
                "model_status": model_status,
                "memory_mode": compact_text(payload.get("memory_mode", "")),
                "learning_input_fingerprint": compact_text(payload.get("learning_input_fingerprint", "")),
                "quality_json": json_dumps(payload.get("quality", {})),
                "selection_decision_json": json_dumps(payload.get("selection_decision", {})),
                "source_file": str(path),
                "search_text": compact_text(
                    " ".join(
                        [
                            date_str,
                            stage,
                            compact_text(payload.get("day_summary", "")),
                            " ".join(str(keyword) for keyword in keywords),
                            " ".join(str(action) for action in next_actions),
                        ]
                    )
                ),
            }
        )
        for item in payload.get("window_summaries", []):
            if not isinstance(item, dict):
                continue
            window_id = compact_text(item.get("window_id", ""))
            if not window_id:
                continue
            current = dict(item)
            current["summary_date"] = date_str
            current["summary_stage"] = stage
            current["model_status"] = model_status
            by_window_key[(date_str, window_id)] = current
    return by_window_key, daily_rows, skipped


def window_search_text(window, summary):
    summary_pairs = normalize_summary_pairs(
        summary.get("summary_pairs", []),
        question_summary=summary.get("question_summary", ""),
        main_takeaway=summary.get("main_takeaway", ""),
    )
    pieces = [
        window.get("window_id", ""),
        window.get("date", ""),
        window.get("cwd", ""),
        window.get("source", ""),
        window.get("originator", ""),
        summary.get("window_title", ""),
        summary.get("question_summary", ""),
        summary.get("main_takeaway", ""),
        summary.get("summary_status", ""),
        " ".join(summary.get("keywords", []) if isinstance(summary.get("keywords"), list) else []),
    ]
    for pair in summary_pairs:
        pieces.append(pair.get("question", ""))
        pieces.append(pair.get("conclusion", ""))
    pieces.extend(item.get("text", "") for item in window.get("prompts", []) if isinstance(item, dict))
    pieces.extend(item.get("text", "") for item in window.get("conclusions", []) if isinstance(item, dict))
    return compact_text(" ".join(str(piece or "") for piece in pieces))


def normalize_window(window, raw_path, summary=None):
    summary = summary or {}
    cwd = compact_text(window.get("cwd", ""))
    prompt_count = safe_int(window.get("prompt_count", len(window.get("prompts", []))))
    conclusion_count = safe_int(window.get("conclusion_count", len(window.get("conclusions", []))))
    keywords = summary.get("keywords", [])
    if not isinstance(keywords, list):
        keywords = []
    prompts = window.get("prompts", [])
    conclusions = window.get("conclusions", [])
    first_prompt_text = ""
    if prompts and isinstance(prompts[0], dict):
        first_prompt_text = prompts[0].get("text", "")
    last_conclusion_text = ""
    if conclusions and isinstance(conclusions[-1], dict):
        last_conclusion_text = conclusions[-1].get("text", "")
    summary_has_content = bool(
        summary.get("question_summary")
        or summary.get("main_takeaway")
        or summary.get("summary_pairs")
        or summary.get("window_title")
    )
    has_summary = summary_model_completed(summary) and summary_has_content
    if has_summary:
        question_summary = compact_text(summary.get("question_summary", "") or first_prompt_text)
        main_takeaway = compact_text(summary.get("main_takeaway", "") or last_conclusion_text or first_prompt_text)
    else:
        question_summary = compact_text(first_prompt_text)
        main_takeaway = compact_text(last_conclusion_text or first_prompt_text)
    summary_status = "summarized" if has_summary else "raw_fallback"
    if has_summary:
        summary_pairs = normalize_summary_pairs(
            summary.get("summary_pairs", []),
            question_summary=question_summary,
            main_takeaway=main_takeaway,
        )
    else:
        summary_pairs = raw_window_summary_pairs(prompts, conclusions)
    raw_summary_pairs = raw_window_summary_pairs(prompts, conclusions)
    window_title = compact_text(
        (summary.get("window_title", "") if has_summary else "")
        or (question_summary if has_summary else (raw_summary_pairs[0].get("question", "") if raw_summary_pairs else first_prompt_text))
    )[:240]
    search_summary = dict(summary)
    search_summary.update(
        {
            "window_title": window_title,
            "question_summary": question_summary,
            "main_takeaway": main_takeaway,
            "summary_pairs": summary_pairs,
            "summary_status": summary_status,
        }
    )
    return {
        "window_id": compact_text(window.get("window_id", "")),
        "date": compact_text(window.get("date", summary.get("summary_date", ""))),
        "stage": compact_text(summary.get("summary_stage", "")),
        "cwd": cwd,
        "project_label": infer_project_label(cwd),
        "source": compact_text(window.get("source", "")),
        "originator": compact_text(window.get("originator", "")),
        "started_at": compact_text(window.get("started_at", "")),
        "latest_activity_at": latest_activity_at(window),
        "session_file": compact_text(window.get("session_file", "")),
        "raw_path": str(raw_path),
        "prompt_count": prompt_count,
        "conclusion_count": conclusion_count,
        "raw_conclusion_count": safe_int(window.get("raw_conclusion_count", 0)),
        "review_like_window": 1 if window.get("review_like_window") else 0,
        "review_related_window": 1 if window.get("review_related_window") else 0,
        "filtered_review_conclusion_count": safe_int(window.get("filtered_review_conclusion_count", 0)),
        "conclusion_policy": compact_text(window.get("conclusion_policy", "")),
        "window_title": window_title,
        "question_summary": question_summary,
        "main_takeaway": main_takeaway,
        "summary_status": summary_status,
        "summary_pairs_json": json_dumps(summary_pairs),
        "raw_summary_pairs_json": json_dumps(raw_summary_pairs),
        "keywords_json": json_dumps(keywords),
        "prompts_json": json_dumps(window.get("prompts", [])),
        "conclusions_json": json_dumps(window.get("conclusions", [])),
        "search_text": window_search_text(window, search_summary),
    }


def normalize_memory_item(item, source_file, source_line):
    keywords = item.get("keywords", [])
    if not isinstance(keywords, list):
        keywords = [part.strip() for part in str(keywords).split(",") if part.strip()]
    source_window_ids = item.get("source_window_ids", [])
    if not isinstance(source_window_ids, list):
        source_window_ids = []
    title = compact_text(item.get("title", ""))
    title_zh = compact_text(item.get("title_zh", ""))
    title_en = compact_text(item.get("title_en", ""))
    value_note = compact_text(item.get("value_note", ""))
    value_note_zh = compact_text(item.get("value_note_zh", ""))
    value_note_en = compact_text(item.get("value_note_en", ""))
    search_text = compact_text(
        " ".join(
            [
                title,
                title_zh,
                title_en,
                value_note,
                value_note_zh,
                value_note_en,
                " ".join(str(keyword) for keyword in keywords),
                compact_text(item.get("bucket", "")),
                compact_text(item.get("memory_type", "")),
                compact_text(item.get("priority", "")),
            ]
        )
    )
    return {
        "date": compact_text(item.get("date", "")),
        "language": compact_text(item.get("language", "")),
        "source": compact_text(item.get("source", "")),
        "bucket": compact_text(item.get("bucket", "")),
        "memory_type": compact_text(item.get("memory_type", "")),
        "priority": compact_text(item.get("priority", "medium")),
        "title": title,
        "title_zh": title_zh,
        "title_en": title_en,
        "value_note": value_note,
        "value_note_zh": value_note_zh,
        "value_note_en": value_note_en,
        "keywords_json": json_dumps(keywords),
        "source_window_ids_json": json_dumps(source_window_ids),
        "memory_key": memory_group_key(item),
        "source_file": str(source_file),
        "source_line": int(source_line),
        "search_text": search_text,
        "_source_window_ids": [compact_text(window_id) for window_id in source_window_ids if compact_text(window_id)],
    }


def connect(db_path):
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def connect_readonly(db_path):
    db_path = Path(db_path).expanduser()
    uri = "file:{}?mode=ro".format(quote(str(db_path), safe="/:"))
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def detect_fts5(conn):
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.openrelix_fts_probe USING fts5(value)")
        conn.execute("DROP TABLE temp.openrelix_fts_probe")
        return True
    except sqlite3.DatabaseError:
        return False


def create_schema(conn, fts_enabled):
    conn.executescript(
        """
        CREATE TABLE metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE source_files (
          path TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          mtime_ns INTEGER NOT NULL,
          size_bytes INTEGER NOT NULL,
          indexed_at TEXT NOT NULL
        );

        CREATE TABLE daily_summaries (
          date TEXT PRIMARY KEY,
          language TEXT,
          stage TEXT,
          generated_at TEXT,
          day_summary TEXT,
          keywords_json TEXT NOT NULL DEFAULT '[]',
          next_actions_json TEXT NOT NULL DEFAULT '[]',
          raw_window_count INTEGER NOT NULL DEFAULT 0,
          review_like_window_count INTEGER NOT NULL DEFAULT 0,
          model_status TEXT,
          memory_mode TEXT,
          learning_input_fingerprint TEXT,
          quality_json TEXT NOT NULL DEFAULT '{}',
          selection_decision_json TEXT NOT NULL DEFAULT '{}',
          source_file TEXT,
          search_text TEXT
        );

        CREATE TABLE memory_items (
          id INTEGER PRIMARY KEY,
          date TEXT,
          language TEXT,
          source TEXT,
          bucket TEXT,
          memory_type TEXT,
          priority TEXT,
          title TEXT,
          title_zh TEXT,
          title_en TEXT,
          value_note TEXT,
          value_note_zh TEXT,
          value_note_en TEXT,
          keywords_json TEXT NOT NULL DEFAULT '[]',
          source_window_ids_json TEXT NOT NULL DEFAULT '[]',
          memory_key TEXT,
          source_file TEXT NOT NULL,
          source_line INTEGER NOT NULL,
          search_text TEXT,
          UNIQUE(source_file, source_line)
        );

        CREATE TABLE memory_source_windows (
          memory_id INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
          source_date TEXT,
          window_id TEXT NOT NULL,
          PRIMARY KEY(memory_id, source_date, window_id)
        );

        CREATE TABLE windows (
          id INTEGER PRIMARY KEY,
          window_id TEXT NOT NULL,
          date TEXT,
          stage TEXT,
          cwd TEXT,
          project_label TEXT,
          source TEXT,
          originator TEXT,
          started_at TEXT,
          latest_activity_at TEXT,
          session_file TEXT,
          raw_path TEXT,
          prompt_count INTEGER NOT NULL DEFAULT 0,
          conclusion_count INTEGER NOT NULL DEFAULT 0,
          raw_conclusion_count INTEGER NOT NULL DEFAULT 0,
          review_like_window INTEGER NOT NULL DEFAULT 0,
          review_related_window INTEGER NOT NULL DEFAULT 0,
          filtered_review_conclusion_count INTEGER NOT NULL DEFAULT 0,
          conclusion_policy TEXT,
          window_title TEXT,
          question_summary TEXT,
          main_takeaway TEXT,
          summary_status TEXT NOT NULL DEFAULT 'raw_fallback',
          summary_pairs_json TEXT NOT NULL DEFAULT '[]',
          raw_summary_pairs_json TEXT NOT NULL DEFAULT '[]',
          keywords_json TEXT NOT NULL DEFAULT '[]',
          prompts_json TEXT NOT NULL DEFAULT '[]',
          conclusions_json TEXT NOT NULL DEFAULT '[]',
          search_text TEXT,
          UNIQUE(date, window_id)
        );

        CREATE TABLE window_messages (
          window_row_id INTEGER NOT NULL REFERENCES windows(id) ON DELETE CASCADE,
          kind TEXT NOT NULL,
          ordinal INTEGER NOT NULL,
          turn_id TEXT,
          event_time TEXT,
          text TEXT,
          PRIMARY KEY(window_row_id, kind, ordinal)
        );

        CREATE INDEX idx_memory_items_date ON memory_items(date);
        CREATE INDEX idx_memory_items_bucket_priority ON memory_items(bucket, priority);
        CREATE INDEX idx_memory_items_key ON memory_items(memory_key);
        CREATE INDEX idx_memory_source_windows_window ON memory_source_windows(source_date, window_id);
        CREATE INDEX idx_windows_date ON windows(date);
        CREATE INDEX idx_windows_window_id ON windows(window_id);
        CREATE INDEX idx_windows_project ON windows(project_label);
        CREATE INDEX idx_windows_latest_activity ON windows(latest_activity_at);
        CREATE INDEX idx_window_messages_kind_time ON window_messages(kind, event_time);
        CREATE INDEX idx_daily_summaries_stage_date ON daily_summaries(stage, date);
        """
    )
    if fts_enabled:
        conn.executescript(
            """
            CREATE VIRTUAL TABLE memory_fts USING fts5(
              title,
              value_note,
              keywords
            );

            CREATE VIRTUAL TABLE window_fts USING fts5(
              cwd,
              window_title,
              question_summary,
              main_takeaway,
              summary_pairs,
              prompts,
              conclusions,
              keywords
            );

            CREATE VIRTUAL TABLE daily_summary_fts USING fts5(
              day_summary,
              keywords,
              next_actions
            );
            """
        )


def reset_schema(conn):
    conn.executescript(
        """
        DROP TABLE IF EXISTS memory_fts;
        DROP TABLE IF EXISTS window_fts;
        DROP TABLE IF EXISTS daily_summary_fts;
        DROP TABLE IF EXISTS window_messages;
        DROP TABLE IF EXISTS memory_source_windows;
        DROP TABLE IF EXISTS memory_items;
        DROP TABLE IF EXISTS windows;
        DROP TABLE IF EXISTS daily_summaries;
        DROP TABLE IF EXISTS source_files;
        DROP TABLE IF EXISTS metadata;
        """
    )


def insert_metadata(conn, key, value):
    conn.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
        (key, str(value)),
    )


def insert_source_file(conn, row, indexed_at):
    conn.execute(
        """
        INSERT INTO source_files(path, kind, mtime_ns, size_bytes, indexed_at)
        VALUES (:path, :kind, :mtime_ns, :size_bytes, :indexed_at)
        """,
        {**row, "indexed_at": indexed_at},
    )


def insert_daily_summary(conn, row, fts_enabled):
    conn.execute(
        """
        INSERT INTO daily_summaries(
          date, language, stage, generated_at, day_summary, keywords_json,
          next_actions_json, raw_window_count, review_like_window_count,
          model_status, memory_mode, learning_input_fingerprint, quality_json,
          selection_decision_json, source_file, search_text
        )
        VALUES (
          :date, :language, :stage, :generated_at, :day_summary, :keywords_json,
          :next_actions_json, :raw_window_count, :review_like_window_count,
          :model_status, :memory_mode, :learning_input_fingerprint, :quality_json,
          :selection_decision_json, :source_file, :search_text
        )
        """,
        row,
    )
    if fts_enabled:
        conn.execute(
            """
            INSERT INTO daily_summary_fts(rowid, day_summary, keywords, next_actions)
            VALUES (
              (SELECT rowid FROM daily_summaries WHERE date = ?),
              ?, ?, ?
            )
            """,
            (
                row["date"],
                row["day_summary"],
                " ".join(json.loads(row["keywords_json"])),
                " ".join(json.loads(row["next_actions_json"])),
            ),
        )


def insert_memory(conn, row, fts_enabled):
    cursor = conn.execute(
        """
        INSERT INTO memory_items(
          date, language, source, bucket, memory_type, priority,
          title, title_zh, title_en, value_note, value_note_zh, value_note_en,
          keywords_json, source_window_ids_json, memory_key, source_file,
          source_line, search_text
        )
        VALUES (
          :date, :language, :source, :bucket, :memory_type, :priority,
          :title, :title_zh, :title_en, :value_note, :value_note_zh, :value_note_en,
          :keywords_json, :source_window_ids_json, :memory_key, :source_file,
          :source_line, :search_text
        )
        """,
        row,
    )
    memory_id = cursor.lastrowid
    for window_id in row["_source_window_ids"]:
        conn.execute(
            "INSERT OR IGNORE INTO memory_source_windows(memory_id, source_date, window_id) VALUES (?, ?, ?)",
            (memory_id, row["date"], window_id),
        )
    if fts_enabled:
        conn.execute(
            """
            INSERT INTO memory_fts(rowid, title, value_note, keywords)
            VALUES (?, ?, ?, ?)
            """,
            (
                memory_id,
                compact_text(" ".join([row["title"], row["title_zh"], row["title_en"]])),
                compact_text(" ".join([row["value_note"], row["value_note_zh"], row["value_note_en"]])),
                " ".join(json.loads(row["keywords_json"])),
            ),
        )


def insert_window(conn, row, fts_enabled):
    cursor = conn.execute(
        """
        INSERT INTO windows(
          window_id, date, stage, cwd, project_label, source, originator,
          started_at, latest_activity_at, session_file, raw_path, prompt_count,
          conclusion_count, raw_conclusion_count, review_like_window,
          review_related_window, filtered_review_conclusion_count,
          conclusion_policy, window_title, question_summary, main_takeaway, summary_status,
          summary_pairs_json, raw_summary_pairs_json, keywords_json, prompts_json,
          conclusions_json, search_text
        )
        VALUES (
          :window_id, :date, :stage, :cwd, :project_label, :source, :originator,
          :started_at, :latest_activity_at, :session_file, :raw_path, :prompt_count,
          :conclusion_count, :raw_conclusion_count, :review_like_window,
          :review_related_window, :filtered_review_conclusion_count,
          :conclusion_policy, :window_title, :question_summary, :main_takeaway, :summary_status,
          :summary_pairs_json, :raw_summary_pairs_json, :keywords_json, :prompts_json,
          :conclusions_json, :search_text
        )
        ON CONFLICT(date, window_id) DO UPDATE SET
          stage=excluded.stage,
          cwd=excluded.cwd,
          project_label=excluded.project_label,
          source=excluded.source,
          originator=excluded.originator,
          started_at=excluded.started_at,
          latest_activity_at=excluded.latest_activity_at,
          session_file=excluded.session_file,
          raw_path=excluded.raw_path,
          prompt_count=excluded.prompt_count,
          conclusion_count=excluded.conclusion_count,
          raw_conclusion_count=excluded.raw_conclusion_count,
          review_like_window=excluded.review_like_window,
          review_related_window=excluded.review_related_window,
          filtered_review_conclusion_count=excluded.filtered_review_conclusion_count,
          conclusion_policy=excluded.conclusion_policy,
          window_title=excluded.window_title,
          question_summary=excluded.question_summary,
          main_takeaway=excluded.main_takeaway,
          summary_status=excluded.summary_status,
          summary_pairs_json=excluded.summary_pairs_json,
          raw_summary_pairs_json=excluded.raw_summary_pairs_json,
          keywords_json=excluded.keywords_json,
          prompts_json=excluded.prompts_json,
          conclusions_json=excluded.conclusions_json,
          search_text=excluded.search_text
        """,
        row,
    )
    window_row = conn.execute(
        "SELECT id FROM windows WHERE date = ? AND window_id = ?",
        (row["date"], row["window_id"]),
    ).fetchone()
    row_id = int(window_row["id"] if window_row else cursor.lastrowid)
    conn.execute("DELETE FROM window_messages WHERE window_row_id = ?", (row_id,))
    for ordinal, item in enumerate(json.loads(row["prompts_json"])):
        if not isinstance(item, dict):
            continue
        conn.execute(
            """
            INSERT INTO window_messages(window_row_id, kind, ordinal, turn_id, event_time, text)
            VALUES (?, 'prompt', ?, ?, ?, ?)
            """,
            (
                row_id,
                ordinal,
                compact_text(item.get("turn_id", "")),
                compact_text(item.get("local_time") or item.get("completed_at") or item.get("ts", "")),
                compact_text(item.get("text", "")),
            ),
        )
    for ordinal, item in enumerate(json.loads(row["conclusions_json"])):
        if not isinstance(item, dict):
            continue
        conn.execute(
            """
            INSERT INTO window_messages(window_row_id, kind, ordinal, turn_id, event_time, text)
            VALUES (?, 'conclusion', ?, ?, ?, ?)
            """,
            (
                row_id,
                ordinal,
                compact_text(item.get("turn_id", "")),
                compact_text(item.get("completed_at") or item.get("local_time") or item.get("ts", "")),
                compact_text(item.get("text", "")),
            ),
        )
    if fts_enabled:
        conn.execute("DELETE FROM window_fts WHERE rowid = ?", (row_id,))
        conn.execute(
            """
            INSERT INTO window_fts(
              rowid, cwd, window_title, question_summary, main_takeaway,
              summary_pairs, prompts, conclusions, keywords
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                row["cwd"],
                row["window_title"],
                row["question_summary"],
                row["main_takeaway"],
                compact_text(
                    " ".join(
                        "{} {}".format(item.get("question", ""), item.get("conclusion", ""))
                        for item in json.loads(row["summary_pairs_json"])
                        if isinstance(item, dict)
                    )
                ),
                compact_text(" ".join(item.get("text", "") for item in json.loads(row["prompts_json"]) if isinstance(item, dict))),
                compact_text(" ".join(item.get("text", "") for item in json.loads(row["conclusions_json"]) if isinstance(item, dict))),
                " ".join(json.loads(row["keywords_json"])),
            ),
        )


def load_daily_window_rows(paths, summaries):
    rows = []
    skipped = 0
    seen = set()
    if not paths.raw_daily_dir.exists():
        return rows, skipped, seen
    for path in sorted(paths.raw_daily_dir.glob("*.json")):
        payload = read_json_file(path)
        if not isinstance(payload, dict):
            skipped += 1
            continue
        for window in payload.get("windows", []):
            if not isinstance(window, dict):
                continue
            window_id = compact_text(window.get("window_id", ""))
            if not window_id:
                continue
            date_str = compact_text(window.get("date", payload.get("date", "")))
            seen.add((date_str, window_id))
            raw_window_path = paths.raw_windows_dir / date_str / "{}.json".format(window_id)
            rows.append(normalize_window(window, raw_window_path if raw_window_path.exists() else path, summaries.get((date_str, window_id))))
    return rows, skipped, seen


def load_standalone_window_rows(paths, summaries, seen_window_keys):
    rows = []
    skipped = 0
    if not paths.raw_windows_dir.exists():
        return rows, skipped
    for path in sorted(paths.raw_windows_dir.glob("*/*.json")):
        payload = read_json_file(path)
        if not isinstance(payload, dict):
            skipped += 1
            continue
        window_id = compact_text(payload.get("window_id", ""))
        date_str = compact_text(payload.get("date", path.parent.name))
        if not window_id or (date_str, window_id) in seen_window_keys:
            continue
        rows.append(normalize_window(payload, path, summaries.get((date_str, window_id))))
    return rows, skipped


def rebuild_index(paths=None, db_path=None):
    paths = ensure_state_layout(paths or get_runtime_paths())
    db_path = Path(db_path or default_db_path(paths)).expanduser()
    tmp_db_path = db_path.with_name(".{}-{}.tmp".format(db_path.name, os.getpid()))
    cleanup_db_files(tmp_db_path)
    rebuilt_at = datetime.now().astimezone().isoformat()
    conn = None
    try:
        conn = connect(tmp_db_path)
        fts_enabled = detect_fts5(conn)
        with conn:
            reset_schema(conn)
            create_schema(conn, fts_enabled)
            source_files = collect_source_files(paths)
            fingerprint = source_fingerprint(source_files)
            for row in source_files:
                insert_source_file(conn, row, rebuilt_at)

            skipped_memory_rows = 0
            memory_rows = 0
            memory_path = paths.registry_dir / "memory_items.jsonl"
            for line_no, item in iter_jsonl(memory_path) or []:
                if not isinstance(item, dict):
                    skipped_memory_rows += 1
                    continue
                row = normalize_memory_item(item, memory_path, line_no)
                insert_memory(conn, row, fts_enabled)
                memory_rows += 1

            summaries, daily_summary_rows, skipped_summary_files = summary_maps(paths)
            for row in daily_summary_rows:
                insert_daily_summary(conn, row, fts_enabled)
            window_rows = 0
            daily_rows, skipped_daily_files, seen_window_ids = load_daily_window_rows(paths, summaries)
            for row in daily_rows:
                insert_window(conn, row, fts_enabled)
                window_rows += 1
            standalone_rows, skipped_window_files = load_standalone_window_rows(paths, summaries, seen_window_ids)
            for row in standalone_rows:
                insert_window(conn, row, fts_enabled)
                window_rows += 1

            insert_metadata(conn, "schema_version", SCHEMA_VERSION)
            insert_metadata(conn, "rebuilt_at", rebuilt_at)
            insert_metadata(conn, "fts_enabled", "1" if fts_enabled else "0")
            insert_metadata(conn, "memory_rows", memory_rows)
            insert_metadata(conn, "window_rows", window_rows)
            insert_metadata(conn, "daily_summary_rows", len(daily_summary_rows))
            insert_metadata(conn, "source_file_rows", len(source_files))
            insert_metadata(conn, "source_fingerprint", fingerprint)
            insert_metadata(conn, "skipped_memory_rows", skipped_memory_rows)
            insert_metadata(conn, "skipped_daily_files", skipped_daily_files)
            insert_metadata(conn, "skipped_window_files", skipped_window_files)
            insert_metadata(conn, "skipped_summary_files", skipped_summary_files)
        conn.close()
        conn = None
        os.replace(tmp_db_path, db_path)
        cleanup_db_sidecars(db_path)
    finally:
        if conn is not None:
            conn.close()
        cleanup_db_files(tmp_db_path)

    return IndexStats(
        db_path=str(db_path),
        rebuilt_at=rebuilt_at,
        fts_enabled=fts_enabled,
        memory_rows=memory_rows,
        window_rows=window_rows,
        skipped_memory_rows=skipped_memory_rows,
        skipped_daily_files=skipped_daily_files,
        skipped_window_files=skipped_window_files,
        skipped_summary_files=skipped_summary_files,
        daily_summary_rows=len(daily_summary_rows),
        source_file_rows=len(source_files),
        source_fingerprint=fingerprint,
    ).to_dict()


def load_metadata(conn):
    try:
        rows = conn.execute("SELECT key, value FROM metadata").fetchall()
    except sqlite3.DatabaseError:
        return {}
    return {row["key"]: row["value"] for row in rows}


def index_status(paths=None, db_path=None):
    paths = paths or get_runtime_paths()
    db_path = Path(db_path or default_db_path(paths)).expanduser()
    payload = {
        "db_path": str(db_path),
        "exists": db_path.exists(),
        "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "schema_version": None,
        "fts_enabled": False,
        "memory_rows": 0,
        "window_rows": 0,
        "daily_summary_rows": 0,
        "source_file_rows": 0,
        "rebuilt_at": "",
        "source_fingerprint": "",
        "current_source_fingerprint": source_fingerprint(collect_source_files(paths)),
        "stale": False,
        "ok": False,
    }
    if not db_path.exists():
        return payload
    try:
        with connect_readonly(db_path) as conn:
            metadata = load_metadata(conn)
            payload.update(
                {
                    "schema_version": safe_int(metadata.get("schema_version")),
                    "fts_enabled": metadata.get("fts_enabled") == "1",
                    "memory_rows": safe_int(metadata.get("memory_rows")),
                    "window_rows": safe_int(metadata.get("window_rows")),
                    "daily_summary_rows": safe_int(metadata.get("daily_summary_rows")),
                    "source_file_rows": safe_int(metadata.get("source_file_rows")),
                    "rebuilt_at": metadata.get("rebuilt_at", ""),
                    "source_fingerprint": metadata.get("source_fingerprint", ""),
                    "ok": safe_int(metadata.get("schema_version")) == SCHEMA_VERSION,
                }
            )
            payload["stale"] = payload["source_fingerprint"] != payload["current_source_fingerprint"]
    except sqlite3.DatabaseError as exc:
        payload["error"] = str(exc)
    return payload


def ensure_index(paths=None, db_path=None):
    paths = paths or get_runtime_paths()
    status = index_status(paths, db_path)
    if status.get("ok") and not status.get("stale"):
        return status
    rebuild_index(paths, db_path)
    return index_status(paths, db_path)


def decode_json_list(value):
    try:
        payload = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def row_to_memory(row):
    return {
        "id": row["id"],
        "date": row["date"],
        "bucket": row["bucket"],
        "memory_type": row["memory_type"],
        "priority": row["priority"],
        "title": row["title"],
        "title_zh": row["title_zh"],
        "title_en": row["title_en"],
        "value_note": row["value_note"],
        "value_note_zh": row["value_note_zh"],
        "value_note_en": row["value_note_en"],
        "keywords": decode_json_list(row["keywords_json"]),
        "source_window_ids": decode_json_list(row["source_window_ids_json"]),
        "memory_key": row["memory_key"],
        "source_file": row["source_file"],
        "source_line": row["source_line"],
    }


def row_to_window(row):
    return {
        "window_id": row["window_id"],
        "date": row["date"],
        "stage": row["stage"],
        "cwd": row["cwd"],
        "project_label": row["project_label"],
        "source": row["source"],
        "originator": row["originator"],
        "started_at": row["started_at"],
        "latest_activity_at": row["latest_activity_at"],
        "session_file": row["session_file"],
        "raw_path": row["raw_path"],
        "prompt_count": row["prompt_count"],
        "conclusion_count": row["conclusion_count"],
        "review_like_window": bool(row["review_like_window"]),
        "review_related_window": bool(row["review_related_window"]),
        "raw_conclusion_count": row["raw_conclusion_count"],
        "filtered_review_conclusion_count": row["filtered_review_conclusion_count"],
        "conclusion_policy": row["conclusion_policy"],
        "window_title": row["window_title"],
        "question_summary": row["question_summary"],
        "main_takeaway": row["main_takeaway"],
        "summary_status": row["summary_status"],
        "summary_pairs": decode_json_list(row["summary_pairs_json"]),
        "raw_summary_pairs": decode_json_list(row["raw_summary_pairs_json"]),
        "keywords": decode_json_list(row["keywords_json"]),
    }


def like_pattern(query):
    escaped = str(query or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%{}%".format(escaped)


def build_filter_clause(filters, params):
    clauses = []
    for column, value in filters:
        if value is None or value == "":
            continue
        clauses.append("{} = ?".format(column))
        params.append(value)
    return clauses


def execute_memory_like(conn, query, clauses, params, limit):
    like_clauses = list(clauses)
    like_params = list(params)
    sql = "SELECT m.* FROM memory_items m"
    if query:
        like_clauses.append("m.search_text LIKE ? ESCAPE '\\'")
        like_params.append(like_pattern(query))
    if like_clauses:
        sql += " WHERE " + " AND ".join(like_clauses)
    sql += " ORDER BY m.date DESC, m.id DESC LIMIT ?"
    like_params.append(limit)
    return conn.execute(sql, like_params).fetchall()


def execute_window_like(conn, query, clauses, params, limit):
    like_clauses = list(clauses)
    like_params = list(params)
    sql = "SELECT w.* FROM windows w"
    if query:
        like_clauses.append("w.search_text LIKE ? ESCAPE '\\'")
        like_params.append(like_pattern(query))
    if like_clauses:
        sql += " WHERE " + " AND ".join(like_clauses)
    sql += " ORDER BY w.latest_activity_at DESC, w.window_id LIMIT ?"
    like_params.append(limit)
    return conn.execute(sql, like_params).fetchall()


def search_memories(query="", *, bucket=None, priority=None, date_from=None, date_to=None, limit=DEFAULT_LIMIT, paths=None, db_path=None):
    paths = paths or get_runtime_paths()
    ensure_index(paths, db_path)
    db_path = Path(db_path or default_db_path(paths)).expanduser()
    limit = max(1, min(int(limit or DEFAULT_LIMIT), 200))
    with connect(db_path) as conn:
        metadata = load_metadata(conn)
        params = []
        clauses = build_filter_clause((("m.bucket", bucket), ("m.priority", priority)), params)
        if date_from:
            clauses.append("m.date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("m.date <= ?")
            params.append(date_to)
        if query and metadata.get("fts_enabled") == "1":
            sql = "SELECT m.* FROM memory_fts f JOIN memory_items m ON m.id = f.rowid"
            clauses.append("memory_fts MATCH ?")
            params.append(query)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY bm25(memory_fts), m.date DESC, m.id DESC LIMIT ?"
            try:
                fts_rows = conn.execute(sql, [*params, limit]).fetchall()
            except sqlite3.DatabaseError:
                fts_rows = []
            params.pop()
            clauses.pop()
            like_rows = execute_memory_like(conn, query, clauses, params, limit)
            rows = []
            seen = set()
            for row in list(fts_rows) + list(like_rows):
                row_id = row["id"]
                if row_id in seen:
                    continue
                seen.add(row_id)
                rows.append(row)
                if len(rows) >= limit:
                    break
            return [row_to_memory(row) for row in rows]
        else:
            return [row_to_memory(row) for row in execute_memory_like(conn, query, clauses, params, limit)]


def search_windows(query="", *, project=None, date_from=None, date_to=None, limit=DEFAULT_LIMIT, paths=None, db_path=None):
    paths = paths or get_runtime_paths()
    ensure_index(paths, db_path)
    db_path = Path(db_path or default_db_path(paths)).expanduser()
    limit = max(1, min(int(limit or DEFAULT_LIMIT), 200))
    with connect(db_path) as conn:
        metadata = load_metadata(conn)
        params = []
        clauses = []
        if project:
            clauses.append("(w.project_label = ? OR w.cwd LIKE ? ESCAPE '\\')")
            params.extend([project, like_pattern(project)])
        if date_from:
            clauses.append("w.date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("w.date <= ?")
            params.append(date_to)
        if query and metadata.get("fts_enabled") == "1":
            sql = "SELECT w.* FROM window_fts f JOIN windows w ON w.id = f.rowid"
            clauses.append("window_fts MATCH ?")
            params.append(query)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY bm25(window_fts), w.latest_activity_at DESC, w.window_id LIMIT ?"
            try:
                fts_rows = conn.execute(sql, [*params, limit]).fetchall()
            except sqlite3.DatabaseError:
                fts_rows = []
            params.pop()
            clauses.pop()
            like_rows = execute_window_like(conn, query, clauses, params, limit)
            rows = []
            seen = set()
            for row in list(fts_rows) + list(like_rows):
                row_id = row["id"]
                if row_id in seen:
                    continue
                seen.add(row_id)
                rows.append(row)
                if len(rows) >= limit:
                    break
            return [row_to_window(row) for row in rows]
        else:
            return [row_to_window(row) for row in execute_window_like(conn, query, clauses, params, limit)]


def parse_args():
    parser = argparse.ArgumentParser(description="Build and query the OpenRelix SQLite sidecar index.")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("rebuild")
    subparsers.add_parser("status")
    memory = subparsers.add_parser("search-memory")
    memory.add_argument("query", nargs="?", default="")
    memory.add_argument("--bucket")
    memory.add_argument("--priority")
    memory.add_argument("--date-from")
    memory.add_argument("--date-to")
    memory.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    window = subparsers.add_parser("search-window")
    window.add_argument("query", nargs="?", default="")
    window.add_argument("--project")
    window.add_argument("--date-from")
    window.add_argument("--date-to")
    window.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.command == "rebuild":
        print(json.dumps(rebuild_index(), ensure_ascii=False, indent=2))
        return
    if args.command == "status":
        print(json.dumps(index_status(), ensure_ascii=False, indent=2))
        return
    if args.command == "search-memory":
        print(
            json.dumps(
                search_memories(
                    args.query,
                    bucket=args.bucket,
                    priority=args.priority,
                    date_from=args.date_from,
                    date_to=args.date_to,
                    limit=args.limit,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "search-window":
        print(
            json.dumps(
                search_windows(
                    args.query,
                    project=args.project,
                    date_from=args.date_from,
                    date_to=args.date_to,
                    limit=args.limit,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    raise SystemExit("missing command")


if __name__ == "__main__":
    main()
