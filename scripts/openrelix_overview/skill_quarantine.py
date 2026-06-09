"""Skill and MCP quarantine state for OpenRelix.

The quarantine state is local runtime data. Quarantining an item never deletes the
source asset: OpenRelix either moves a global skill out of an injected skill
root, removes a JSON MCP server entry while storing it for restore, or records a
state-only quarantine when the source cannot be safely mutated.
"""

import hashlib
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from collections import OrderedDict
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None

from asset_runtime import atomic_write_json

from . import asset_discovery
from . import mcp_usage


SCHEMA_VERSION = 1
STATE_FILENAME = "skill-mcp-quarantine.json"
VIEW_CACHE_FILENAME = "skill-mcp-quarantine-view.json"
LEGACY_STATE_FILENAME = "skill-mcp-" + "blackroom.json"
PROJECT_SKILL_ROOTS_KEY = "project_skill_roots"
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_GRACE_DAYS = 7
SKILL_FILENAME = "SKILL.md"
SKILL_TYPE = "skill"
MCP_TYPE = "mcp"
SUPPORTED_TYPES = {SKILL_TYPE, MCP_TYPE}
MANUAL_REASON = "manual"
UNUSED_30D_REASON = "unused_30d"
NO_CALLS_REASON = "no_calls"
NEW_GRACE_REASON = "new_grace"
ACTIVE_REASON = "active"
STATE_ONLY_STATUS = "state_only"
MIGRATION_WARNING_STATUSES = {
    "archive_failed",
    "backup_failed",
    "move_failed",
    "config_failed",
    "toml_failed",
    "trash_failed",
    "restore_failed",
    "restore_conflict",
    "restore_missing",
}
STATE_ONLY_WARNING_NOTES = {
    "repo_skill_not_moved",
    "project_skill_not_moved",
    "not_in_known_global_skill_root",
    "toml_config_not_mutated",
    "no_movable_skill_sources",
    "no_mutable_mcp_config",
    "unsupported_config_format",
    "apply_disabled",
}


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _coerce_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_cls):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError:
            pass
    return datetime.now().date()


def _safe_identifier(value):
    text = str(value or "").strip()
    if not text or "/" in text or "\\" in text:
        return ""
    return text


def entity_key(entity_type, identifier):
    entity_type = str(entity_type or "").strip().lower()
    identifier = _safe_identifier(identifier)
    if entity_type not in SUPPORTED_TYPES or not identifier:
        return ""
    return "{}:{}".format(entity_type, identifier)


def split_entity_key(value):
    text = str(value or "").strip()
    if ":" not in text:
        return "", ""
    entity_type, identifier = text.split(":", 1)
    entity_type = entity_type.strip().lower()
    identifier = _safe_identifier(identifier)
    if entity_type not in SUPPORTED_TYPES or not identifier:
        return "", ""
    return entity_type, identifier


def quarantine_state_path(paths):
    return paths.runtime_dir / STATE_FILENAME


def quarantine_view_cache_path(paths):
    return paths.runtime_dir / VIEW_CACHE_FILENAME


def quarantine_action_lock_path(paths):
    return paths.runtime_dir / "skill-mcp-quarantine.lock"


def quarantine_root(paths):
    return paths.runtime_dir / "skill-mcp-quarantine"


def ensure_quarantine_root(paths):
    root = quarantine_root(paths)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return root


@contextmanager
def quarantine_action_lock(paths, timeout_seconds=None, poll_seconds=0.05):
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_path = quarantine_action_lock_path(paths)
    with lock_path.open("a+", encoding="utf-8") as handle:
        locked = False
        if fcntl is not None:
            if timeout_seconds is None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
            else:
                deadline = time.monotonic() + max(float(timeout_seconds or 0), 0.0)
                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                        break
                    except BlockingIOError:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("quarantine_lock_timeout")
                        time_to_sleep = max(min(float(poll_seconds or 0.05), remaining), 0.0)
                        if time_to_sleep:
                            time.sleep(time_to_sleep)
        try:
            yield
        finally:
            if fcntl is not None and locked:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_view_cache(paths):
    payload = _safe_json_load(quarantine_view_cache_path(paths))
    if not payload or payload.get("schema_version") != SCHEMA_VERSION:
        return {}
    return payload


def write_view_cache(paths, view):
    if not isinstance(view, dict):
        return {}
    payload = dict(view)
    payload["schema_version"] = SCHEMA_VERSION
    payload["cached_at"] = _now_iso()
    atomic_write_json(quarantine_view_cache_path(paths), payload)
    return payload


def _empty_state():
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": "",
        PROJECT_SKILL_ROOTS_KEY: [],
        "observed": {},
        "entries": {},
    }


def read_state(paths):
    path = quarantine_state_path(paths)
    if not path.exists():
        legacy_path = paths.runtime_dir / LEGACY_STATE_FILENAME
        if legacy_path.exists():
            path = legacy_path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(payload, dict):
        return _empty_state()
    entries = payload.get("entries")
    if isinstance(entries, list):
        entries = {
            str(entry.get("entity_key") or ""): entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("entity_key")
        }
    if not isinstance(entries, dict):
        entries = {}
    observed = payload.get("observed")
    if not isinstance(observed, dict):
        observed = {}
    project_skill_roots = payload.get(PROJECT_SKILL_ROOTS_KEY)
    if not isinstance(project_skill_roots, list):
        project_skill_roots = []
    normalized = _empty_state()
    normalized.update(payload)
    normalized["schema_version"] = SCHEMA_VERSION
    normalized[PROJECT_SKILL_ROOTS_KEY] = _normalize_project_skill_root_records(project_skill_roots)
    normalized["observed"] = {
        key: row
        for key, row in observed.items()
        if isinstance(row, dict) and split_entity_key(key)[0]
    }
    normalized["entries"] = {
        key: entry
        for key, entry in entries.items()
        if isinstance(entry, dict) and split_entity_key(key)[0]
    }
    return normalized


def write_state(paths, state):
    normalized = _empty_state()
    if isinstance(state, dict):
        normalized.update(state)
    normalized["schema_version"] = SCHEMA_VERSION
    normalized["updated_at"] = _now_iso()
    observed = normalized.get("observed")
    normalized["observed"] = observed if isinstance(observed, dict) else {}
    entries = normalized.get("entries")
    normalized["entries"] = entries if isinstance(entries, dict) else {}
    normalized[PROJECT_SKILL_ROOTS_KEY] = _normalize_project_skill_root_records(
        normalized.get(PROJECT_SKILL_ROOTS_KEY)
    )
    atomic_write_json(quarantine_state_path(paths), normalized)
    return normalized


def _resolved(path):
    try:
        return Path(path).expanduser().resolve(strict=False)
    except OSError:
        return Path(path).expanduser()


def _absolute_entry_path(path):
    try:
        return Path(path).expanduser().absolute()
    except OSError:
        return Path(path).expanduser()


def _project_root_label(path):
    path = _resolved(path)
    return path.name or path.as_posix()


def _project_root_key(path):
    return _resolved(path).as_posix()


def _normalize_project_skill_root_record(record):
    if isinstance(record, dict):
        raw_path = record.get("path")
        added_at = str(record.get("added_at") or "").strip()
        last_refresh_requested_at = str(record.get("last_refresh_requested_at") or "").strip()
    else:
        raw_path = record
        added_at = ""
        last_refresh_requested_at = ""
    text = str(raw_path or "").strip()
    if not text:
        return None
    path = _resolved(text)
    normalized = {
        "path": path.as_posix(),
        "label": _project_root_label(path),
        "added_at": added_at,
    }
    if last_refresh_requested_at:
        normalized["last_refresh_requested_at"] = last_refresh_requested_at
    return normalized


def _normalize_project_skill_root_records(records):
    normalized = []
    seen = set()
    for record in records or []:
        row = _normalize_project_skill_root_record(record)
        if not row:
            continue
        key = _project_root_key(row["path"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(row)
    return normalized


def list_project_skill_roots(paths):
    return list(read_state(paths).get(PROJECT_SKILL_ROOTS_KEY) or [])


def configured_project_skill_roots(paths):
    return [row["path"] for row in list_project_skill_roots(paths) if row.get("path")]


def add_project_skill_root(paths, raw_path):
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError("missing_project_path")
    path = _resolved(text)
    if not path.exists():
        raise ValueError("project_path_not_found")
    if not path.is_dir():
        raise ValueError("project_path_not_directory")
    state = read_state(paths)
    roots = list(state.get(PROJECT_SKILL_ROOTS_KEY) or [])
    key = _project_root_key(path)
    now = _now_iso()
    changed = False
    next_roots = []
    seen = set()
    for record in roots:
        row = _normalize_project_skill_root_record(record)
        if not row:
            continue
        row_key = _project_root_key(row["path"])
        if row_key in seen:
            continue
        if row_key == key:
            row["last_refresh_requested_at"] = now
            changed = True
        seen.add(row_key)
        next_roots.append(row)
    if key not in seen:
        next_roots.append(
            {
                "path": path.as_posix(),
                "label": _project_root_label(path),
                "added_at": now,
                "last_refresh_requested_at": now,
            }
        )
        changed = True
    state[PROJECT_SKILL_ROOTS_KEY] = next_roots
    if changed:
        write_state(paths, state)
    return {
        "project_root": next(row for row in next_roots if _project_root_key(row["path"]) == key),
        "project_skill_roots": next_roots,
        "changed": changed,
    }


def remove_project_skill_root(paths, raw_path):
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError("missing_project_path")
    key = _project_root_key(text)
    state = read_state(paths)
    roots = list(state.get(PROJECT_SKILL_ROOTS_KEY) or [])
    next_roots = [
        row
        for row in _normalize_project_skill_root_records(roots)
        if _project_root_key(row["path"]) != key
    ]
    changed = len(next_roots) != len(_normalize_project_skill_root_records(roots))
    state[PROJECT_SKILL_ROOTS_KEY] = next_roots
    if changed:
        write_state(paths, state)
    return {"project_skill_roots": next_roots, "changed": changed}


def _is_relative_to(path, root):
    try:
        _resolved(path).relative_to(_resolved(root))
        return True
    except ValueError:
        return False


def _is_entry_relative_to(path, root):
    try:
        _absolute_entry_path(path).relative_to(_absolute_entry_path(root))
        return True
    except ValueError:
        return False


def _path_date(path, follow_symlinks=True):
    if not path:
        return ""
    try:
        path = Path(path)
        stat = path.stat() if follow_symlinks else path.lstat()
    except OSError:
        return ""
    try:
        return datetime.fromtimestamp(float(stat.st_mtime)).date().isoformat()
    except (OSError, ValueError):
        return ""


def _age_days(anchor, added_at):
    if not added_at:
        return None
    try:
        added = datetime.fromisoformat(str(added_at)[:10]).date()
    except ValueError:
        return None
    return max((anchor - added).days, 0)


def _date_min(*values):
    dates = [str(value or "")[:10] for value in values if str(value or "").strip()]
    dates = [value for value in dates if re.match(r"^\d{4}-\d{2}-\d{2}$", value)]
    return min(dates) if dates else ""


def _manifest_parent(path):
    text = str(path or "").strip()
    if not text:
        return None
    manifest = Path(text)
    if manifest.name != "SKILL.md":
        return None
    return manifest.parent


def _source_added_at(sources):
    dates = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        path = source.get("manifest_entry_abspath") or source.get("manifest_abspath") or ""
        parent = _manifest_parent(path)
        date_value = _path_date(parent or path, follow_symlinks=False)
        if date_value:
            dates.append(date_value)
    return max(dates) if dates else ""


def _skill_items(
    paths,
    today,
    installed_assets=None,
    activation_snapshot=None,
    codex_homes=None,
    project_skill_roots=None,
):
    anchor = _coerce_date(today)
    if project_skill_roots is None:
        project_skill_roots = configured_project_skill_roots(paths)
    if activation_snapshot is None:
        installed_assets = (
            installed_assets
            if installed_assets is not None
            else asset_discovery.discover_installed_assets(
                paths,
                codex_homes=codex_homes,
                project_skill_roots=project_skill_roots,
            )
        )
        activation_snapshot = asset_discovery.compute_activation_snapshot(
            paths,
            installed_assets,
            anchor,
            monthly_months=0,
            codex_homes=codex_homes,
            project_skill_roots=project_skill_roots,
        )
    rows = asset_discovery.aggregate_renderable_assets(
        activation_snapshot.get("assets", []),
        activation_snapshot.get("frequency_by_key", {}),
    )
    items = []
    for row in rows:
        if row.get("type") != SKILL_TYPE or row.get("is_manual"):
            continue
        identifier = _safe_identifier(row.get("identifier") or row.get("name"))
        key = entity_key(SKILL_TYPE, identifier)
        if not key:
            continue
        sources = list(row.get("sources") or [])
        added_at = _source_added_at(sources)
        usage_30d = int(row.get("read_events_30d") or row.get("windows_30d") or 0)
        items.append(
            {
                "entity_key": key,
                "entity_type": SKILL_TYPE,
                "identifier": identifier,
                "display_name": row.get("name") or identifier,
                "description": row.get("description") or "",
                "usage_7d": int(row.get("read_events_7d") or row.get("windows_7d") or 0),
                "usage_30d": usage_30d,
                "sessions_30d": int(row.get("windows_30d") or 0),
                "last_used_at": row.get("last_seen") or "",
                "added_at": added_at,
                "age_days": _age_days(anchor, added_at),
                "sources": sources,
                "click_target": row.get("click_target") or "",
            }
        )
    return items


def _safe_json_load(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _json_mcp_sources(path, host):
    payload = _safe_json_load(path)
    if not payload:
        return []
    rows = []
    for section in ("mcpServers", "mcp_servers"):
        servers = payload.get(section)
        if not isinstance(servers, dict):
            continue
        for server, config in servers.items():
            identifier = _safe_identifier(server)
            if identifier:
                row = {
                    "server": identifier,
                    "host": host,
                    "format": "json",
                    "path": str(path),
                    "section": section,
                }
                if isinstance(config, dict) and isinstance(config.get("enabled"), bool):
                    row["enabled"] = bool(config.get("enabled"))
                rows.append(row)
    return rows


_TOML_MCP_SECTION_RE = re.compile(r"^\s*\[mcp_servers\.([^\]]+)\]\s*$")
_TOML_BOOL_RE = re.compile(r"^\s*enabled\s*=\s*(true|false)\s*(?:#.*)?$", re.IGNORECASE)


def _toml_mcp_sources(path, host):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    rows = []
    current = None
    current_enabled = None

    def flush_current():
        if not current:
            return
        row = {
            "server": current,
            "host": host,
            "format": "toml",
            "path": str(path),
            "section": "mcp_servers",
        }
        if current_enabled is not None:
            row["enabled"] = current_enabled
        rows.append(row)

    for line in text.splitlines():
        match = _TOML_MCP_SECTION_RE.match(line)
        if match:
            flush_current()
            raw_name = match.group(1).strip().strip("\"'")
            current = _safe_identifier(raw_name)
            current_enabled = None
            continue
        if line.lstrip().startswith("["):
            flush_current()
            current = None
            current_enabled = None
            continue
        if current:
            enabled_match = _TOML_BOOL_RE.match(line)
            if enabled_match:
                current_enabled = enabled_match.group(1).lower() == "true"
    flush_current()
    return rows


def discover_configured_mcps(paths):
    """Discover configured MCP servers without exposing command args or env."""
    source_specs = [
        (paths.codex_home / "config.toml", "codex", "toml"),
        (paths.codex_home / "mcp.json", "codex", "json"),
        (paths.claude_home / "settings.json", "claude", "json"),
        (paths.claude_home / "claude_desktop_config.json", "claude_desktop", "json"),
        (Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json", "claude_desktop", "json"),
    ]
    by_server = OrderedDict()
    for path, host, fmt in source_specs:
        if not Path(path).exists():
            continue
        sources = _json_mcp_sources(path, host) if fmt == "json" else _toml_mcp_sources(path, host)
        for source in sources:
            server = source["server"]
            row = by_server.setdefault(
                server,
                {
                    "server": server,
                    "entity_key": entity_key(MCP_TYPE, server),
                    "entity_type": MCP_TYPE,
                    "identifier": server,
                    "display_name": server,
                    "config_sources": [],
                    "configured_at": "",
                    "enabled": False,
                },
            )
            row["config_sources"].append(source)
            if source.get("enabled") is not False:
                row["enabled"] = True
            configured_at = _path_date(path)
            if configured_at and configured_at > row.get("configured_at", ""):
                row["configured_at"] = configured_at
    return list(by_server.values())


def _mcp_items(paths, today, mcp_usage_view=None, codex_homes=None, lookback_days=DEFAULT_LOOKBACK_DAYS):
    anchor = _coerce_date(today)
    mcp_usage_view = mcp_usage_view or mcp_usage.build_mcp_usage_view(
        paths,
        anchor,
        lookback_days=lookback_days,
        limit=None,
        codex_homes=codex_homes,
    )
    usage_by_server = {
        row.get("server"): row
        for row in mcp_usage_view.get("servers", []) or []
        if row.get("server")
    }
    items = []
    for row in discover_configured_mcps(paths):
        if row.get("enabled") is False:
            continue
        server = row["server"]
        usage = usage_by_server.get(server, {})
        calls = int(usage.get("calls") or 0)
        items.append(
            {
                "entity_key": row["entity_key"],
                "entity_type": MCP_TYPE,
                "identifier": server,
                "display_name": server,
                "description": "MCP server",
                "usage_7d": 0,
                "usage_30d": calls,
                "sessions_30d": int(usage.get("sessions") or 0),
                "last_used_at": usage.get("last_seen") or "",
                "added_at": "",
                "age_days": None,
                "config_sources": row.get("config_sources", []),
            }
        )
    return items


def _apply_observed_dates(paths, state, items, anchor):
    """Persist first-seen dates so config rewrites do not reset item age."""
    observed = state.setdefault("observed", {})
    today = _coerce_date(anchor).isoformat()
    changed = False
    for item in items:
        key = item.get("entity_key")
        if not key:
            continue
        existing = observed.get(key) if isinstance(observed.get(key), dict) else {}
        candidate = str(item.get("added_at") or "").strip()
        first_seen_at = _date_min(existing.get("first_seen_at"), candidate) or candidate or today
        next_seen = {
            "entity_key": key,
            "entity_type": item.get("entity_type", ""),
            "identifier": item.get("identifier", ""),
            "display_name": item.get("display_name", ""),
            "first_seen_at": first_seen_at,
            "last_seen_at": today,
        }
        if existing != next_seen:
            observed[key] = next_seen
            changed = True
        item["added_at"] = first_seen_at
        item["age_days"] = _age_days(_coerce_date(anchor), first_seen_at)
    if changed:
        write_state(paths, state)
    return items


def _is_grace_item(item, grace_days):
    age = item.get("age_days")
    return age is not None and age < int(grace_days or DEFAULT_GRACE_DAYS)


def _reason_for_item(item):
    if item.get("entity_type") == MCP_TYPE:
        return NO_CALLS_REASON
    return UNUSED_30D_REASON


def _status_for_item(item, entries, grace_days):
    key = item.get("entity_key", "")
    if key in entries:
        return "quarantined", entries[key].get("reason") or MANUAL_REASON
    if int(item.get("usage_30d") or 0) > 0:
        return "active", ACTIVE_REASON
    if _is_grace_item(item, grace_days):
        return "grace", NEW_GRACE_REASON
    return "suggested", _reason_for_item(item)


def _merge_entry_item(item, entry):
    migration_warnings = _migration_warnings(entry.get("isolation_targets", []))
    merged = dict(item or {})
    merged.update(
        {
            "entity_key": entry.get("entity_key") or merged.get("entity_key", ""),
            "entity_type": entry.get("entity_type") or merged.get("entity_type", ""),
            "identifier": entry.get("identifier") or merged.get("identifier", ""),
            "display_name": entry.get("display_name") or merged.get("display_name", ""),
            "reason": entry.get("reason") or merged.get("reason", MANUAL_REASON),
            "blocked_at": entry.get("blocked_at", ""),
            "blocked_by": entry.get("blocked_by", ""),
            "note": entry.get("note", ""),
            "isolation_status": entry.get("isolation_status", STATE_ONLY_STATUS),
            "isolation_targets": entry.get("isolation_targets", []),
            "migration_warnings": migration_warnings,
            "migration_warning_count": len(migration_warnings),
            "status": "quarantined",
        }
    )
    if not merged.get("last_used_at"):
        merged["last_used_at"] = entry.get("last_used_at", "")
    if not merged.get("usage_30d"):
        merged["usage_30d"] = int(entry.get("usage_30d") or 0)
    return merged


def build_quarantine_view(
    paths,
    today=None,
    lookback_days=DEFAULT_LOOKBACK_DAYS,
    grace_days=DEFAULT_GRACE_DAYS,
    installed_assets=None,
    activation_snapshot=None,
    mcp_usage_view=None,
    codex_homes=None,
    project_skill_roots=None,
):
    anchor = _coerce_date(today)
    state = read_state(paths)
    entries = state.get("entries", {})
    if project_skill_roots is None:
        project_skill_roots = configured_project_skill_roots(paths)
    items = _skill_items(
        paths,
        anchor,
        installed_assets=installed_assets,
        activation_snapshot=activation_snapshot,
        codex_homes=codex_homes,
        project_skill_roots=project_skill_roots,
    )
    items.extend(
        _mcp_items(
            paths,
            anchor,
            mcp_usage_view=mcp_usage_view,
            codex_homes=codex_homes,
            lookback_days=lookback_days,
        )
    )
    _apply_observed_dates(paths, state, items, anchor)
    by_key = OrderedDict((item["entity_key"], item) for item in items)
    if _reapply_quarantined_sources(paths, state, by_key.values()):
        state = read_state(paths)
        entries = state.get("entries", {})
    normalized = []
    for item in by_key.values():
        status, reason = _status_for_item(item, entries, grace_days)
        row = dict(item)
        row["status"] = status
        row["reason"] = reason
        row["is_quarantined"] = status == "quarantined"
        if status == "quarantined":
            row = _merge_entry_item(row, entries[row["entity_key"]])
        normalized.append(row)
    for key, entry in entries.items():
        if key in by_key:
            continue
        normalized.append(_merge_entry_item({}, entry))

    suggested = [row for row in normalized if row.get("status") == "suggested"]
    quarantined = [row for row in normalized if row.get("status") == "quarantined"]
    grace = [row for row in normalized if row.get("status") == "grace"]
    active = [row for row in normalized if row.get("status") == "active"]
    migration_warning_count = sum(len(row.get("migration_warnings", []) or []) for row in quarantined)
    sort_key = lambda row: (
        row.get("entity_type", ""),
        -int(row.get("usage_30d") or 0),
        str(row.get("display_name") or row.get("identifier") or "").lower(),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "state_path": str(quarantine_state_path(paths)),
        "quarantine_root": str(ensure_quarantine_root(paths)),
        "project_skill_roots": list_project_skill_roots(paths),
        "lookback_days": int(lookback_days or DEFAULT_LOOKBACK_DAYS),
        "grace_days": int(grace_days or DEFAULT_GRACE_DAYS),
        "generated_at": _now_iso(),
        "items": sorted(normalized, key=sort_key),
        "suggested": sorted(suggested, key=lambda row: (row.get("entity_type", ""), str(row.get("display_name", "")).lower())),
        "quarantined": sorted(quarantined, key=lambda row: (row.get("entity_type", ""), str(row.get("display_name", "")).lower())),
        "grace": sorted(grace, key=lambda row: (row.get("entity_type", ""), str(row.get("display_name", "")).lower())),
        "active": sorted(active, key=sort_key),
        "counts": {
            "suggested": len(suggested),
            "quarantined": len(quarantined),
            "grace": len(grace),
            "active": len(active),
            "skills": sum(1 for row in normalized if row.get("entity_type") == SKILL_TYPE),
            "mcps": sum(1 for row in normalized if row.get("entity_type") == MCP_TYPE),
            "migration_warnings": migration_warning_count,
        },
    }


def resolve_entity(view, reference, entity_type=None):
    raw = str(reference or "").strip()
    if not raw:
        raise ValueError("missing entity")
    if ":" in raw:
        key_type, identifier = split_entity_key(raw)
        key = entity_key(key_type, identifier)
        if not key:
            raise ValueError("invalid entity key: {}".format(raw))
    else:
        entity_type = str(entity_type or "").strip().lower()
        candidates = []
        for item in view.get("items", []):
            if entity_type and item.get("entity_type") != entity_type:
                continue
            names = {
                str(item.get("identifier") or "").lower(),
                str(item.get("display_name") or "").lower(),
            }
            if raw.lower() in names:
                candidates.append(item)
        if len(candidates) != 1:
            if not candidates:
                raise ValueError("entity not found: {}".format(raw))
            raise ValueError("ambiguous entity: {}; use skill:<id> or mcp:<id>".format(raw))
        return candidates[0]
    for item in view.get("items", []):
        if item.get("entity_key") == key:
            return item
    raise ValueError("entity not found: {}".format(raw))


def _target_slug(kind, identifier, original_path):
    digest = hashlib.sha1(str(original_path).encode("utf-8")).hexdigest()[:10]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(identifier or "item")).strip("-") or "item"
    return "{}-{}-{}".format(kind, safe, digest)


def _available_archive_path(path):
    base = Path(path)
    for index in range(1, 1000):
        candidate = base.with_name("{}-previous-{}".format(base.name, index))
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    digest = hashlib.sha1(_now_iso().encode("utf-8")).hexdigest()[:10]
    return base.with_name("{}-previous-{}".format(base.name, digest))


def _atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".{}.tmp-{}".format(path.name, hashlib.sha1(_now_iso().encode("utf-8")).hexdigest()[:10]))
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _backup_config_file(paths, path, kind):
    path = Path(path)
    if not path.exists():
        return ""
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:10]
    suffix = path.suffix or ".conf"
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    backup = quarantine_root(paths) / "mcp-config-backups" / "{}-{}-{}{}".format(kind, path.stem, digest, suffix)
    backup = backup.with_name("{}-{}".format(timestamp, backup.name))
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(path), str(backup))
    return str(backup)


def _toml_section_bounds(lines, server):
    current_start = None
    current_server = ""
    for index, line in enumerate(lines):
        match = _TOML_MCP_SECTION_RE.match(line)
        if match:
            if current_start is not None and current_server == server:
                return current_start, index
            raw_name = match.group(1).strip().strip("\"'")
            current_start = index
            current_server = _safe_identifier(raw_name)
            continue
        if line.lstrip().startswith("[") and current_start is not None:
            if current_server == server:
                return current_start, index
            current_start = None
            current_server = ""
    if current_start is not None and current_server == server:
        return current_start, len(lines)
    return None, None


def _toml_enabled_line(enabled, reference_line=""):
    newline = "\n" if str(reference_line or "").endswith("\n") else ""
    return "enabled = {}{}".format("true" if enabled else "false", newline)


def _toml_section_with_enabled(section_lines, enabled):
    next_lines = list(section_lines or [])
    replacement = _toml_enabled_line(enabled, next_lines[0] if next_lines else "\n")
    for index, line in enumerate(next_lines[1:], start=1):
        if _TOML_BOOL_RE.match(line):
            indent = re.match(r"^(\s*)", line).group(1)
            newline = "\n" if line.endswith("\n") else ""
            next_lines[index] = "{}enabled = {}{}".format(indent, "true" if enabled else "false", newline)
            return next_lines
    insert_at = 1 if next_lines else 0
    next_lines.insert(insert_at, replacement)
    return next_lines


def _disable_toml_mcp_server(path, server):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return False, {"status": "toml_failed", "error": str(exc)}
    lines = text.splitlines(keepends=True)
    start, end = _toml_section_bounds(lines, server)
    if start is None:
        return False, {"status": "missing"}
    original_section = "".join(lines[start:end])
    next_section_lines = _toml_section_with_enabled(lines[start:end], False)
    next_text = "".join(lines[:start] + next_section_lines + lines[end:])
    try:
        _atomic_write_text(path, next_text)
    except OSError as exc:
        return False, {"status": "toml_failed", "error": str(exc)}
    return True, {
        "status": "toml_disabled",
        "saved_config_text": original_section,
        "disabled_config_text": "".join(next_section_lines),
    }


def _restore_toml_mcp_target(target):
    path = Path(target.get("path") or "")
    server = target.get("server")
    saved_config_text = str(target.get("saved_config_text") or "")
    disabled_config_text = str(target.get("disabled_config_text") or "")
    if not saved_config_text:
        return False, "restore_missing"
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeDecodeError):
        text = ""
    lines = text.splitlines(keepends=True)
    start, end = _toml_section_bounds(lines, server)
    if start is None:
        next_text = text
        if next_text and not next_text.endswith("\n"):
            next_text += "\n"
        next_text += saved_config_text
    else:
        current_section = "".join(lines[start:end])
        if disabled_config_text and current_section != disabled_config_text:
            return False, "restore_conflict"
        next_text = "".join(lines[:start]) + saved_config_text + "".join(lines[end:])
    _atomic_write_text(path, next_text)
    return True, "restored"


def _path_snapshot(path):
    text = str(path or "").strip()
    snapshot = {
        "path": text,
        "exists": False,
        "is_symlink": False,
        "kind": "missing",
    }
    if not text:
        return snapshot
    path = Path(text)
    try:
        snapshot["is_symlink"] = path.is_symlink()
        snapshot["exists"] = path.exists() or snapshot["is_symlink"]
        if snapshot["is_symlink"]:
            snapshot["kind"] = "symlink"
            try:
                snapshot["link_target"] = os.readlink(path)
            except OSError:
                snapshot["link_target"] = ""
            snapshot["link_target_resolved"] = _resolved(path).as_posix()
        elif path.is_dir():
            snapshot["kind"] = "directory"
        elif path.is_file():
            snapshot["kind"] = "file"
        elif snapshot["exists"]:
            snapshot["kind"] = "other"
        try:
            stat = path.lstat()
            snapshot["mode"] = oct(stat.st_mode & 0o777)
            snapshot["mtime"] = datetime.fromtimestamp(float(stat.st_mtime)).isoformat(timespec="seconds")
            if snapshot["kind"] in {"file", "symlink"}:
                snapshot["size"] = int(stat.st_size)
        except OSError:
            pass
    except OSError:
        return snapshot
    return snapshot


def _fallback_skill_manifest_entry(paths, item, source):
    if not isinstance(source, dict):
        return ""
    identifier = _safe_identifier(item.get("identifier") or source.get("identifier"))
    if not identifier:
        return ""
    kind = str(source.get("kind") or "")
    source_root = str(source.get("source_root") or "")
    candidates = []
    if kind in {"codex", "codex_skill"}:
        if "memories/skills" in source_root:
            candidates.append(paths.codex_home / "memories" / "skills" / identifier / SKILL_FILENAME)
        candidates.append(paths.codex_home / "skills" / identifier / SKILL_FILENAME)
    elif kind == "claude_skill":
        candidates.append(Path.home() / ".claude" / "skills" / identifier / SKILL_FILENAME)
    elif kind == "agent_skill":
        candidates.append(Path.home() / ".agents" / "skills" / identifier / SKILL_FILENAME)
    for candidate in candidates:
        parent = candidate.parent
        if candidate.exists() or candidate.is_symlink() or parent.exists() or parent.is_symlink():
            return candidate.as_posix()
    return ""


def _skill_source_target(paths, item, source):
    if isinstance(source, dict):
        manifest = source.get("manifest_abspath", "")
        manifest_entry = source.get("manifest_entry_abspath") or _fallback_skill_manifest_entry(paths, item, source) or manifest
    else:
        manifest = ""
        manifest_entry = ""
    resolved_parent = _manifest_parent(manifest)
    entry_parent = _manifest_parent(manifest_entry) or resolved_parent
    target = {
        "type": SKILL_TYPE,
        "kind": source.get("kind", "") if isinstance(source, dict) else "",
        "original_path": str(entry_parent or ""),
        "original_resolved_path": str(_resolved(resolved_parent or entry_parent)) if (resolved_parent or entry_parent) else "",
        "status": STATE_ONLY_STATUS,
    }
    if entry_parent:
        target["original_snapshot"] = _path_snapshot(entry_parent)
    if not entry_parent:
        target["note"] = "missing_manifest_path"
        return target
    source_kind = str(source.get("kind", "") if isinstance(source, dict) else "")
    allowed_roots = [
        paths.codex_home / "skills",
        paths.codex_home / "memories" / "skills",
        Path.home() / ".claude" / "skills",
        Path.home() / ".agents" / "skills",
    ]
    is_global_entry = any(_is_entry_relative_to(entry_parent, root) for root in allowed_roots)
    if _is_relative_to(resolved_parent or entry_parent, paths.repo_root) and not is_global_entry:
        target["note"] = "repo_skill_not_moved"
        return target
    if source_kind in {"project_skill", "external_repo_skill"} and not is_global_entry:
        target["note"] = "project_skill_not_moved"
        return target
    if not is_global_entry:
        target["note"] = "not_in_known_global_skill_root"
        return target
    target["quarantine_path"] = str(
        quarantine_root(paths)
        / "skills"
        / _target_slug(source.get("kind", "skill"), item.get("identifier"), entry_parent)
    )
    return target


def _apply_skill_quarantine(paths, item):
    targets = []
    for source in item.get("sources", []) or []:
        target = _skill_source_target(paths, item, source)
        original = Path(target.get("original_path") or "")
        quarantine_path = target.get("quarantine_path") or ""
        if not quarantine_path:
            targets.append(target)
            continue
        target["original_snapshot"] = _path_snapshot(original)
        quarantine = Path(quarantine_path)
        if not original.exists() and not original.is_symlink():
            target["status"] = "already_moved" if quarantine.exists() or quarantine.is_symlink() else "missing"
            targets.append(target)
            continue
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        if quarantine.exists() or quarantine.is_symlink():
            archive_path = _available_archive_path(quarantine)
            try:
                shutil.move(str(quarantine), str(archive_path))
                target["archived_quarantine_path"] = str(archive_path)
            except OSError as exc:
                target["status"] = "archive_failed"
                target["error"] = str(exc)
                targets.append(target)
                continue
        try:
            shutil.move(str(original), str(quarantine))
            target["status"] = "moved"
        except OSError as exc:
            target["status"] = "move_failed"
            target["error"] = str(exc)
        targets.append(target)
    return targets or [{"type": SKILL_TYPE, "status": STATE_ONLY_STATUS, "note": "no_movable_skill_sources"}]


def _apply_mcp_quarantine(paths, item):
    targets = []
    for source in item.get("config_sources", []) or []:
        target = dict(source)
        target["type"] = MCP_TYPE
        target["status"] = STATE_ONLY_STATUS
        path = Path(source.get("path") or "")
        server = item.get("identifier")
        if source.get("format") == "toml":
            try:
                target["backup_path"] = _backup_config_file(paths, path, "toml")
            except OSError as exc:
                target["status"] = "backup_failed"
                target["error"] = str(exc)
                targets.append(target)
                continue
            ok, result = _disable_toml_mcp_server(path, server)
            target.update(result)
            targets.append(target)
            continue
        if source.get("format") != "json":
            target["note"] = "unsupported_config_format"
            targets.append(target)
            continue
        section = source.get("section") or "mcpServers"
        payload = _safe_json_load(path)
        servers = payload.get(section) if isinstance(payload, dict) else None
        if not isinstance(servers, dict) or server not in servers:
            target["status"] = "missing"
            targets.append(target)
            continue
        try:
            target["backup_path"] = _backup_config_file(paths, path, "json")
        except OSError as exc:
            target["status"] = "backup_failed"
            target["error"] = str(exc)
            targets.append(target)
            continue
        target["saved_config"] = servers.pop(server)
        try:
            atomic_write_json(path, payload)
            target["status"] = "config_isolated"
        except OSError as exc:
            target["status"] = "config_failed"
            target["error"] = str(exc)
        targets.append(target)
    return targets or [{"type": MCP_TYPE, "status": STATE_ONLY_STATUS, "note": "no_mutable_mcp_config"}]


def _isolation_status(targets):
    statuses = {target.get("status") for target in targets or []}
    if "moved" in statuses or "already_moved" in statuses:
        return "moved"
    if "config_isolated" in statuses:
        return "config_isolated"
    if "toml_disabled" in statuses:
        return "toml_disabled"
    if statuses and statuses <= {STATE_ONLY_STATUS, "missing", "already_moved"}:
        return STATE_ONLY_STATUS
    return sorted(statuses)[0] if statuses else STATE_ONLY_STATUS


def _target_warning_status(target):
    restore_status = str(target.get("restore_status") or "")
    if restore_status in MIGRATION_WARNING_STATUSES:
        return restore_status
    return str(target.get("status") or "")


def _target_needs_warning(target):
    status = _target_warning_status(target)
    note = str(target.get("note") or "")
    if status in MIGRATION_WARNING_STATUSES:
        return True
    if status == STATE_ONLY_STATUS and note in STATE_ONLY_WARNING_NOTES:
        return True
    return False


def _migration_warnings(targets):
    warnings = []
    for target in targets or []:
        if not isinstance(target, dict) or not _target_needs_warning(target):
            continue
        warning = {
            "type": target.get("type") or "",
            "kind": target.get("kind") or target.get("host") or "",
            "status": _target_warning_status(target),
            "note": target.get("note") or "",
        }
        if target.get("error"):
            warning["error"] = str(target.get("error"))
        if target.get("server"):
            warning["server"] = target.get("server")
        warnings.append(warning)
    return warnings


def _reapply_quarantined_sources(paths, state, items):
    entries = state.setdefault("entries", {})
    changed = False
    for item in items:
        key = item.get("entity_key")
        entry = entries.get(key)
        if not entry:
            continue
        if item.get("entity_type") == SKILL_TYPE:
            targets = _apply_skill_quarantine(paths, item)
        elif item.get("entity_type") == MCP_TYPE:
            targets = _apply_mcp_quarantine(paths, item)
        else:
            continue
        status = _isolation_status(targets)
        if targets != entry.get("isolation_targets") or status != entry.get("isolation_status"):
            entry["isolation_targets"] = targets
            entry["isolation_status"] = status
            entry["migration_warnings"] = _migration_warnings(targets)
            entry["migration_warning_count"] = len(entry["migration_warnings"])
            entry["last_reapplied_at"] = _now_iso()
            entries[key] = entry
            changed = True
    if changed:
        write_state(paths, state)
    return changed


def block_entity(
    paths,
    reference,
    entity_type=None,
    today=None,
    reason=MANUAL_REASON,
    blocked_by="user",
    note="",
    apply=True,
    view=None,
    codex_homes=None,
):
    view = view or build_quarantine_view(paths, today=today, codex_homes=codex_homes)
    item = resolve_entity(view, reference, entity_type=entity_type)
    state = read_state(paths)
    entries = state.setdefault("entries", {})
    key = item["entity_key"]
    entry = dict(entries.get(key) or {})
    entry.update(
        {
            "entity_key": key,
            "entity_type": item.get("entity_type"),
            "identifier": item.get("identifier"),
            "display_name": item.get("display_name") or item.get("identifier"),
            "reason": reason or MANUAL_REASON,
            "blocked_by": blocked_by or "user",
            "blocked_at": entry.get("blocked_at") or _now_iso(),
            "last_used_at": item.get("last_used_at") or "",
            "usage_30d": int(item.get("usage_30d") or 0),
            "note": note or entry.get("note", ""),
        }
    )
    if apply:
        targets = (
            _apply_skill_quarantine(paths, item)
            if item.get("entity_type") == SKILL_TYPE
            else _apply_mcp_quarantine(paths, item)
        )
    else:
        targets = [{"type": item.get("entity_type"), "status": STATE_ONLY_STATUS, "note": "apply_disabled"}]
    entry["isolation_targets"] = targets
    entry["isolation_status"] = _isolation_status(targets)
    entry["migration_warnings"] = _migration_warnings(targets)
    entry["migration_warning_count"] = len(entry["migration_warnings"])
    entries[key] = entry
    write_state(paths, state)
    return entry


def _block_all_bucket(
    paths,
    bucket,
    result_key,
    note,
    default_reason=None,
    blocked_by="system",
    today=None,
    lookback_days=DEFAULT_LOOKBACK_DAYS,
    grace_days=DEFAULT_GRACE_DAYS,
    dry_run=False,
    apply=True,
    view=None,
    codex_homes=None,
):
    view = view or build_quarantine_view(
        paths,
        today=today,
        lookback_days=lookback_days,
        grace_days=grace_days,
        codex_homes=codex_homes,
    )
    rows = list(view.get(bucket) or [])
    if dry_run:
        return {"blocked": [], result_key: rows, "dry_run": True}
    blocked = []
    for item in rows:
        blocked.append(
            block_entity(
                paths,
                item["entity_key"],
                today=today,
                reason=default_reason or item.get("reason") or _reason_for_item(item),
                blocked_by=blocked_by,
                note=note,
                apply=apply,
                view=view,
                codex_homes=codex_homes,
            )
        )
    return {"blocked": blocked, result_key: rows, "dry_run": False}


def block_all_suggestions(
    paths,
    today=None,
    lookback_days=DEFAULT_LOOKBACK_DAYS,
    grace_days=DEFAULT_GRACE_DAYS,
    dry_run=False,
    apply=True,
    view=None,
    codex_homes=None,
):
    return _block_all_bucket(
        paths,
        "suggested",
        "suggested",
        "block_all_suggestions",
        today=today,
        lookback_days=lookback_days,
        grace_days=grace_days,
        dry_run=dry_run,
        apply=apply,
        view=view,
        codex_homes=codex_homes,
    )


def block_all_grace(
    paths,
    today=None,
    lookback_days=DEFAULT_LOOKBACK_DAYS,
    grace_days=DEFAULT_GRACE_DAYS,
    dry_run=False,
    apply=True,
    view=None,
    codex_homes=None,
):
    return _block_all_bucket(
        paths,
        "grace",
        "grace",
        "block_all_grace",
        default_reason=MANUAL_REASON,
        blocked_by="user",
        today=today,
        lookback_days=lookback_days,
        grace_days=grace_days,
        dry_run=dry_run,
        apply=apply,
        view=view,
        codex_homes=codex_homes,
    )


def _restore_skill_target(target):
    original = Path(target.get("original_path") or "")
    quarantine = Path(target.get("quarantine_path") or "")
    snapshot = target.get("original_snapshot") if isinstance(target.get("original_snapshot"), dict) else {}
    if target.get("status") not in {"moved", "already_moved"}:
        return True, target.get("status", STATE_ONLY_STATUS)
    if original.exists() or original.is_symlink():
        return False, "restore_conflict"
    if not quarantine.exists() and not quarantine.is_symlink():
        if snapshot.get("kind") == "symlink" and snapshot.get("link_target"):
            original.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(str(snapshot.get("link_target")), original)
            except OSError:
                return False, "restore_missing"
            return True, "restored"
        return False, "restore_missing"
    original.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(quarantine), str(original))
    return True, "restored"


def _restore_mcp_target(target):
    if target.get("status") == "toml_disabled":
        return _restore_toml_mcp_target(target)
    if target.get("status") != "config_isolated":
        return True, target.get("status", STATE_ONLY_STATUS)
    path = Path(target.get("path") or "")
    section = target.get("section") or "mcpServers"
    server = target.get("server")
    payload = _safe_json_load(path) or {}
    servers = payload.setdefault(section, {})
    if not isinstance(servers, dict):
        return False, "restore_conflict"
    if server in servers:
        return False, "restore_conflict"
    servers[server] = target.get("saved_config", {})
    atomic_write_json(path, payload)
    return True, "restored"


def _trash_destination(path, trash_root):
    trash_root = Path(trash_root)
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:10]
    stem = "{}-{}-{}".format(Path(path).name or "openrelix-item", timestamp, digest)
    candidate = trash_root / stem
    if not candidate.exists() and not candidate.is_symlink():
        return candidate
    for index in range(1, 1000):
        next_candidate = trash_root / "{}-{}".format(stem, index)
        if not next_candidate.exists() and not next_candidate.is_symlink():
            return next_candidate
    return trash_root / "{}-{}".format(stem, hashlib.sha1(_now_iso().encode("utf-8")).hexdigest()[:10])


def _move_path_to_trash(path, trash_root=None):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return {"path": str(path), "status": "missing"}
    trash_root = Path(trash_root or (Path.home() / ".Trash"))
    trash_root.mkdir(parents=True, exist_ok=True)
    destination = _trash_destination(path, trash_root)
    shutil.move(str(path), str(destination))
    return {"path": str(path), "trash_path": str(destination), "status": "trashed"}


def _deletable_target_paths(paths, target):
    root = quarantine_root(paths)
    root_entry = _absolute_entry_path(root)
    values = []
    seen = set()
    for field in ("quarantine_path", "archived_quarantine_path", "backup_path"):
        raw_path = str(target.get(field) or "").strip()
        if not raw_path:
            continue
        candidate = Path(raw_path)
        try:
            candidate_entry = _absolute_entry_path(candidate)
            candidate_entry.relative_to(root_entry)
        except ValueError:
            continue
        if candidate_entry == root_entry:
            continue
        key = candidate_entry.as_posix()
        if key in seen:
            continue
        seen.add(key)
        values.append(candidate)
    return values


def _entity_key_from_reference(reference, entity_type=None):
    raw = str(reference or "").strip()
    if ":" in raw:
        key_type, identifier = split_entity_key(raw)
        key = entity_key(key_type, identifier)
    else:
        key = entity_key(entity_type, raw)
    if not key:
        raise ValueError("invalid entity key: {}".format(raw))
    return key


def delete_quarantined_entity(paths, reference, entity_type=None, trash_root=None):
    state = read_state(paths)
    key = _entity_key_from_reference(reference, entity_type=entity_type)
    entry = state.get("entries", {}).get(key)
    if not entry:
        raise ValueError("entity is not quarantined: {}".format(reference))
    trash_results = []
    ok = True
    for target in entry.get("isolation_targets", []) or []:
        if not isinstance(target, dict):
            continue
        for path in _deletable_target_paths(paths, target):
            try:
                trash_results.append(_move_path_to_trash(path, trash_root=trash_root))
            except OSError as exc:
                ok = False
                trash_results.append({
                    "path": str(path),
                    "status": "trash_failed",
                    "error": str(exc),
                })
    if ok:
        state.get("entries", {}).pop(key, None)
    else:
        failed_targets = [
            {"type": entry.get("entity_type"), "status": row.get("status"), "error": row.get("error"), "path": row.get("path")}
            for row in trash_results
            if row.get("status") == "trash_failed"
        ]
        entry["migration_warnings"] = _migration_warnings(failed_targets)
        entry["migration_warning_count"] = len(entry["migration_warnings"])
        state.setdefault("entries", {})[key] = entry
    write_state(paths, state)
    return {
        "ok": ok,
        "entity_key": key,
        "deleted": ok,
        "trash_targets": trash_results,
        "migration_warnings": _migration_warnings([
            {"type": entry.get("entity_type"), "status": row.get("status"), "error": row.get("error")}
            for row in trash_results
            if row.get("status") == "trash_failed"
        ]),
    }


def unblock_entity(paths, reference, entity_type=None, apply=True, today=None, view=None, codex_homes=None):
    state = read_state(paths)
    view = view or build_quarantine_view(paths, today=today, codex_homes=codex_homes)
    item = resolve_entity(view, reference, entity_type=entity_type)
    key = item["entity_key"]
    entry = state.get("entries", {}).get(key)
    if not entry:
        raise ValueError("entity is not quarantined: {}".format(reference))
    restore_results = []
    ok = True
    if apply:
        for target in entry.get("isolation_targets", []) or []:
            try:
                if target.get("type") == SKILL_TYPE:
                    target_ok, status = _restore_skill_target(target)
                elif target.get("type") == MCP_TYPE:
                    target_ok, status = _restore_mcp_target(target)
                else:
                    target_ok, status = True, target.get("status", STATE_ONLY_STATUS)
            except OSError as exc:
                target_ok, status = False, "restore_failed"
                target = dict(target)
                target["error"] = str(exc)
            ok = ok and target_ok
            restored_target = dict(target)
            restored_target["restore_status"] = status
            restore_results.append(restored_target)
    if ok:
        state.get("entries", {}).pop(key, None)
    else:
        entry["isolation_targets"] = restore_results
        entry["isolation_status"] = "restore_failed"
        entry["migration_warnings"] = _migration_warnings(restore_results)
        entry["migration_warning_count"] = len(entry["migration_warnings"])
        state.setdefault("entries", {})[key] = entry
    write_state(paths, state)
    warnings = _migration_warnings(restore_results)
    return {"ok": ok, "entity_key": key, "restore_targets": restore_results, "migration_warnings": warnings}


def filter_asset_rows(rows, state_or_view):
    entries = _entries_from_state_or_view(state_or_view)
    blocked_skill_ids = {
        split_entity_key(key)[1]
        for key in entries
        if split_entity_key(key)[0] == SKILL_TYPE
    }
    return [
        row
        for row in rows or []
        if not (row.get("type") == SKILL_TYPE and str(row.get("identifier") or "") in blocked_skill_ids)
    ]


def filter_mcp_usage_view(view, state_or_view):
    entries = _entries_from_state_or_view(state_or_view)
    blocked_servers = {
        split_entity_key(key)[1]
        for key in entries
        if split_entity_key(key)[0] == MCP_TYPE
    }
    if not blocked_servers:
        return view
    filtered = dict(view or {})
    filtered["tools"] = [
        row for row in filtered.get("tools", []) or [] if row.get("server") not in blocked_servers
    ]
    filtered["servers"] = [
        row for row in filtered.get("servers", []) or [] if row.get("server") not in blocked_servers
    ]
    filtered["active_tools"] = len(filtered["tools"])
    filtered["active_servers"] = len(filtered["servers"])
    filtered["total_calls"] = sum(int(row.get("calls") or 0) for row in filtered["tools"])
    return filtered


def _entries_from_state_or_view(value):
    if not isinstance(value, dict):
        return {}
    if "entries" in value and isinstance(value.get("entries"), dict):
        return value.get("entries", {})
    return {
        row.get("entity_key"): row
        for row in value.get("quarantined", []) or []
        if row.get("entity_key")
    }
