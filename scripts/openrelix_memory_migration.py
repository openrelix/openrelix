#!/usr/bin/env python3

import json
import re
from datetime import datetime

from asset_runtime import (
    atomic_write_json,
    atomic_write_text,
    load_runtime_config,
    personal_memory_enabled,
    runtime_config_path,
)
from openrelix_overview.memory_registry import build_memory_group_key


PERSONAL_MEMORY_ALGORITHM_VERSION = 3
PERSONAL_MEMORY_MIGRATION_WINDOW_DAYS = 7
PERSONAL_MEMORY_MIGRATION_STAGE = "final"
MEMORY_MIGRATION_STATE_VERSION = 1
CANONICAL_MEMORY_REGISTRY_FILE = "memory_entries.jsonl"
LEGACY_MEMORY_REGISTRY_FILE = "memory_items.jsonl"

LIGHTWEIGHT_MEMORY_TITLE_PATTERNS = (
    r"^\s*轻量待查",
    r"^\s*lightweight later review",
)


def current_timestamp(now=None):
    return (now or datetime.now().astimezone()).isoformat()


def memory_migration_status_path(paths):
    return paths.runtime_dir / "memory-migration.json"


def canonical_memory_registry_path(paths):
    return paths.registry_dir / CANONICAL_MEMORY_REGISTRY_FILE


def legacy_memory_registry_path(paths):
    return paths.registry_dir / LEGACY_MEMORY_REGISTRY_FILE


def load_jsonl_rows(path):
    rows = []
    skipped = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return rows, skipped
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(item, dict):
            skipped += 1
            continue
        rows.append(item)
    return rows, skipped


def memory_registry_row_key(item):
    explicit = str(
        (item or {}).get("memory_key")
        or (item or {}).get("canonical_memory_id")
        or (item or {}).get("canonical_id")
        or ""
    ).strip()
    if explicit:
        return explicit
    return build_memory_group_key(item or {})


def is_lightweight_or_preliminary_memory_row(item):
    stage = str(item.get("stage") or item.get("summary_stage") or "").strip().lower()
    generation = str(
        item.get("summary_generation")
        or item.get("generation")
        or item.get("model_status")
        or ""
    ).strip().lower()
    if stage == "preliminary" or generation in {"lightweight", "skipped_lightweight"}:
        return True
    if item.get("lightweight_memory_deferred") or item.get("lightweight_memory_deferred_reason"):
        return True
    title = " ".join(
        str(item.get(key) or "")
        for key in ("title", "title_zh", "title_en", "value_note", "value_note_zh", "value_note_en")
    )
    return any(re.search(pattern, title, flags=re.IGNORECASE) for pattern in LIGHTWEIGHT_MEMORY_TITLE_PATTERNS)


def normalize_migrated_memory_row(item, migrated_at):
    row = dict(item)
    row.setdefault("memory_key", memory_registry_row_key(row))
    row.setdefault("migrated_from", LEGACY_MEMORY_REGISTRY_FILE)
    row.setdefault("migrated_at", migrated_at)
    row.setdefault("memory_algorithm_version", PERSONAL_MEMORY_ALGORITHM_VERSION)
    return row


def migrate_personal_memory_registry(paths, now=None):
    """Move legacy memory rows into the canonical registry and drop obsolete lightweight rows."""
    canonical_path = canonical_memory_registry_path(paths)
    legacy_path = legacy_memory_registry_path(paths)
    migrated_at = current_timestamp(now=now)

    canonical_rows, skipped_canonical = load_jsonl_rows(canonical_path)
    legacy_rows, skipped_legacy = load_jsonl_rows(legacy_path)
    existing_keys = {memory_registry_row_key(row) for row in canonical_rows if memory_registry_row_key(row)}

    migrated_rows = []
    dropped_lightweight = 0
    duplicate_rows = 0
    for item in legacy_rows:
        if is_lightweight_or_preliminary_memory_row(item):
            dropped_lightweight += 1
            continue
        key = memory_registry_row_key(item)
        if key and key in existing_keys:
            duplicate_rows += 1
            continue
        row = normalize_migrated_memory_row(item, migrated_at)
        row_key = memory_registry_row_key(row)
        if row_key:
            existing_keys.add(row_key)
        migrated_rows.append(row)

    if migrated_rows:
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        all_rows = canonical_rows + migrated_rows
        atomic_write_text(
            canonical_path,
            "\n".join(json.dumps(row, ensure_ascii=False) for row in all_rows) + "\n",
        )

    return {
        "canonical_path": str(canonical_path),
        "legacy_path": str(legacy_path),
        "canonical_rows_before": len(canonical_rows),
        "legacy_rows_seen": len(legacy_rows),
        "migrated_rows": len(migrated_rows),
        "dropped_lightweight_rows": dropped_lightweight,
        "duplicate_rows": duplicate_rows,
        "skipped_invalid_rows": skipped_canonical + skipped_legacy,
    }


def load_memory_migration_state(paths):
    path = memory_migration_status_path(paths)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_memory_migration_state(paths, **fields):
    state = load_memory_migration_state(paths)
    state.update(fields)
    state["schema_version"] = MEMORY_MIGRATION_STATE_VERSION
    state["algorithm_version"] = PERSONAL_MEMORY_ALGORITHM_VERSION
    state["updated_at"] = current_timestamp()
    atomic_write_json(memory_migration_status_path(paths), state)
    return state


def runtime_personal_memory_algorithm_version(paths):
    config = load_runtime_config(paths)
    try:
        return int(config.get("personal_memory_algorithm_version") or 0)
    except (TypeError, ValueError):
        return 0


def write_runtime_personal_memory_algorithm_version(paths, version=PERSONAL_MEMORY_ALGORITHM_VERSION):
    config = load_runtime_config(paths)
    config["schema_version"] = int(config.get("schema_version") or 1)
    config["personal_memory_algorithm_version"] = int(version)
    config["personal_memory_algorithm_migrated_at"] = current_timestamp()
    atomic_write_json(runtime_config_path(paths), config)
    return config


def has_existing_personal_memory_state(paths):
    candidates = (
        canonical_memory_registry_path(paths),
        legacy_memory_registry_path(paths),
        paths.consolidated_daily_dir,
    )
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return True
        if path.is_dir():
            try:
                next(path.iterdir())
            except StopIteration:
                continue
            except OSError:
                continue
            return True
    return False


def should_schedule_memory_migration(paths, force=False):
    if not personal_memory_enabled(paths):
        return False
    if force:
        return True
    if not has_existing_personal_memory_state(paths):
        return False
    return runtime_personal_memory_algorithm_version(paths) < PERSONAL_MEMORY_ALGORITHM_VERSION


def ensure_memory_migration_state(paths, window_days=PERSONAL_MEMORY_MIGRATION_WINDOW_DAYS, force=False):
    if not personal_memory_enabled(paths):
        return write_memory_migration_state(
            paths,
            status="skipped",
            reason="personal_memory_disabled",
            window_days=int(window_days),
            stage=PERSONAL_MEMORY_MIGRATION_STAGE,
        )

    if not has_existing_personal_memory_state(paths) and not force:
        write_runtime_personal_memory_algorithm_version(paths)
        return write_memory_migration_state(
            paths,
            status="skipped",
            reason="no_existing_personal_memory_state",
            window_days=int(window_days),
            stage=PERSONAL_MEMORY_MIGRATION_STAGE,
        )

    if should_schedule_memory_migration(paths, force=force):
        previous_version = runtime_personal_memory_algorithm_version(paths)
        return write_memory_migration_state(
            paths,
            status="pending",
            reason="algorithm_version_changed",
            previous_algorithm_version=previous_version,
            target_algorithm_version=PERSONAL_MEMORY_ALGORITHM_VERSION,
            window_days=int(window_days),
            stage=PERSONAL_MEMORY_MIGRATION_STAGE,
        )

    return write_memory_migration_state(
        paths,
        status="completed",
        reason="already_current",
        window_days=int(window_days),
        stage=PERSONAL_MEMORY_MIGRATION_STAGE,
    )


def memory_migration_is_pending(paths):
    state = ensure_memory_migration_state(paths)
    return state.get("status") == "pending"


def mark_memory_migration_running(
    paths,
    dates,
    window_days=PERSONAL_MEMORY_MIGRATION_WINDOW_DAYS,
    registry_migration=None,
):
    fields = {}
    if registry_migration is not None:
        fields["registry_migration"] = registry_migration
    return write_memory_migration_state(
        paths,
        status="running",
        reason="algorithm_version_changed",
        dates=list(dates),
        window_days=int(window_days),
        stage=PERSONAL_MEMORY_MIGRATION_STAGE,
        started_at=current_timestamp(),
        **fields,
    )


def mark_memory_migration_completed(
    paths,
    dates,
    window_days=PERSONAL_MEMORY_MIGRATION_WINDOW_DAYS,
    registry_migration=None,
):
    fields = {}
    if registry_migration is not None:
        fields["registry_migration"] = registry_migration
    write_runtime_personal_memory_algorithm_version(paths)
    return write_memory_migration_state(
        paths,
        status="completed",
        reason="migration_completed",
        dates=list(dates),
        window_days=int(window_days),
        stage=PERSONAL_MEMORY_MIGRATION_STAGE,
        completed_at=current_timestamp(),
        **fields,
    )


def mark_memory_migration_failed(paths, dates, error, window_days=PERSONAL_MEMORY_MIGRATION_WINDOW_DAYS):
    return write_memory_migration_state(
        paths,
        status="failed",
        reason="migration_failed",
        dates=list(dates),
        window_days=int(window_days),
        stage=PERSONAL_MEMORY_MIGRATION_STAGE,
        error=str(error or ""),
        failed_at=current_timestamp(),
    )
