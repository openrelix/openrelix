#!/usr/bin/env python3

import json
from datetime import datetime

from asset_runtime import (
    atomic_write_json,
    load_runtime_config,
    personal_memory_enabled,
    runtime_config_path,
)


PERSONAL_MEMORY_ALGORITHM_VERSION = 2
PERSONAL_MEMORY_MIGRATION_WINDOW_DAYS = 7
PERSONAL_MEMORY_MIGRATION_STAGE = "final"
MEMORY_MIGRATION_STATE_VERSION = 1


def current_timestamp(now=None):
    return (now or datetime.now().astimezone()).isoformat()


def memory_migration_status_path(paths):
    return paths.runtime_dir / "memory-migration.json"


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
        paths.registry_dir / "memory_entries.jsonl",
        paths.registry_dir / "memory_items.jsonl",
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


def mark_memory_migration_running(paths, dates, window_days=PERSONAL_MEMORY_MIGRATION_WINDOW_DAYS):
    return write_memory_migration_state(
        paths,
        status="running",
        reason="algorithm_version_changed",
        dates=list(dates),
        window_days=int(window_days),
        stage=PERSONAL_MEMORY_MIGRATION_STAGE,
        started_at=current_timestamp(),
    )


def mark_memory_migration_completed(paths, dates, window_days=PERSONAL_MEMORY_MIGRATION_WINDOW_DAYS):
    write_runtime_personal_memory_algorithm_version(paths)
    return write_memory_migration_state(
        paths,
        status="completed",
        reason="migration_completed",
        dates=list(dates),
        window_days=int(window_days),
        stage=PERSONAL_MEMORY_MIGRATION_STAGE,
        completed_at=current_timestamp(),
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
