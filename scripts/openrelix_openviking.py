#!/usr/bin/env python3
"""OpenViking service integration for OpenRelix memory summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timezone
from datetime import timedelta
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping, Optional
import urllib.error
import urllib.parse
import urllib.request


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from asset_runtime import (  # noqa: E402
    atomic_write_json,
    atomic_write_text,
    ensure_state_layout,
    get_runtime_paths,
    load_runtime_config,
    runtime_config_path,
)
import build_openviking_summaries  # noqa: E402
import openrelix_model_runner  # noqa: E402


DEFAULT_OPENVIKING_URL = "http://localhost:1933"
DEFAULT_OPENVIKING_TIMEOUT = 60.0
DEFAULT_OPENVIKING_AGENT_ID = "openrelix"
OPENVIKING_CLI_CONFIG_ENV = "OPENVIKING_CLI_CONFIG_FILE"
OPENVIKING_CONFIG_DIR = Path.home() / ".openviking"
OPENVIKING_CLI_CONFIG_PATH = OPENVIKING_CONFIG_DIR / "ovcli.conf"
OPENVIKING_EXPORT_REGISTRY = "openviking_memory_exports.jsonl"
MAX_BATCH_MESSAGES = 100
DEFAULT_MESSAGE_CHARS = 12000
DEFAULT_SETUP_BACKFILL_DAYS = 1


class OpenVikingError(RuntimeError):
    """Raised when OpenRelix cannot complete an OpenViking operation."""


@dataclass(frozen=True)
class OpenVikingConnection:
    url: str = DEFAULT_OPENVIKING_URL
    api_key: str = ""
    account: str = ""
    user: str = ""
    agent_id: str = DEFAULT_OPENVIKING_AGENT_ID
    timeout: float = DEFAULT_OPENVIKING_TIMEOUT
    extra_headers: Mapping[str, str] | None = None

    def redacted(self) -> dict:
        payload = {
            "url": self.url,
            "api_key": redact_secret(self.api_key),
            "account": self.account,
            "user": self.user,
            "agent_id": self.agent_id,
            "timeout": self.timeout,
        }
        if self.extra_headers:
            payload["extra_headers"] = {
                key: redact_secret(value) if "key" in key.lower() or "authorization" in key.lower() else value
                for key, value in self.extra_headers.items()
            }
        return payload


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def today_str() -> str:
    return date_cls.today().isoformat()


def compact_text(value: Any) -> str:
    return build_openviking_summaries.compact_text(value)


def json_dumps(row: Mapping[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return rows
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            rows.append(dict(payload))
    return rows


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    rendered = [json_dumps(row) for row in rows]
    atomic_write_text(path, "\n".join(rendered) + ("\n" if rendered else ""))


def upsert_jsonl(path: Path, rows: Iterable[Mapping[str, Any]], key_fields: tuple[str, ...]) -> int:
    existing = read_jsonl(path)
    by_key = {tuple(row.get(field) for field in key_fields): row for row in existing}
    changed = 0
    for row in rows:
        row_dict = dict(row)
        key = tuple(row_dict.get(field) for field in key_fields)
        if by_key.get(key) != row_dict:
            changed += 1
        by_key[key] = row_dict
    write_jsonl(path, by_key.values())
    return changed


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _safe_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number <= 0:
        return default
    return number


def _normalize_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_OPENVIKING_URL
    return text.rstrip("/")


def redact_secret(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return "[set]"


def ovcli_config_path() -> Path:
    explicit = os.environ.get(OPENVIKING_CLI_CONFIG_ENV)
    if explicit:
        return Path(explicit).expanduser()
    return OPENVIKING_CLI_CONFIG_PATH


def load_ovcli_config(path: Optional[Path] = None) -> dict:
    target = path or ovcli_config_path()
    try:
        text = os.path.expandvars(target.read_text(encoding="utf-8-sig"))
        payload = json.loads(text)
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_openviking_connection(paths=None, overrides: Optional[Mapping[str, Any]] = None) -> OpenVikingConnection:
    paths = paths or get_runtime_paths()
    runtime_config = load_runtime_config(paths)
    ovcli_config = load_ovcli_config()
    overrides = dict(overrides or {})

    def choose(runtime_key: str, ovcli_key: str, *env_names: str, default: Any = "") -> Any:
        if runtime_key in overrides and overrides[runtime_key] is not None:
            return overrides[runtime_key]
        env_value = _first_env(*env_names)
        if env_value:
            return env_value
        if runtime_config.get(runtime_key) not in (None, ""):
            return runtime_config.get(runtime_key)
        if ovcli_config.get(ovcli_key) not in (None, ""):
            return ovcli_config.get(ovcli_key)
        return default

    extra_headers = ovcli_config.get("extra_headers") if isinstance(ovcli_config.get("extra_headers"), Mapping) else {}
    return OpenVikingConnection(
        url=_normalize_url(choose("openviking_url", "url", "OPENRELIX_OPENVIKING_URL", "OPENVIKING_URL", default=DEFAULT_OPENVIKING_URL)),
        api_key=str(choose("openviking_api_key", "api_key", "OPENRELIX_OPENVIKING_API_KEY", "OPENVIKING_API_KEY", default="") or ""),
        account=str(choose("openviking_account", "account", "OPENRELIX_OPENVIKING_ACCOUNT", "OPENVIKING_ACCOUNT", default="") or ""),
        user=str(choose("openviking_user", "user", "OPENRELIX_OPENVIKING_USER", "OPENVIKING_USER", default="") or ""),
        agent_id=str(
            choose(
                "openviking_agent_id",
                "agent_id",
                "OPENRELIX_OPENVIKING_AGENT_ID",
                "OPENVIKING_AGENT_ID",
                default=DEFAULT_OPENVIKING_AGENT_ID,
            )
            or DEFAULT_OPENVIKING_AGENT_ID
        ),
        timeout=_safe_float(
            choose(
                "openviking_timeout",
                "timeout",
                "OPENRELIX_OPENVIKING_TIMEOUT",
                "OPENVIKING_TIMEOUT",
                default=DEFAULT_OPENVIKING_TIMEOUT,
            ),
            DEFAULT_OPENVIKING_TIMEOUT,
        ),
        extra_headers={str(key): str(value) for key, value in extra_headers.items()},
    )


def write_openviking_config(
    *,
    paths=None,
    url: Optional[str] = None,
    api_key: Optional[str] = None,
    account: Optional[str] = None,
    user: Optional[str] = None,
    agent_id: Optional[str] = None,
    timeout: Optional[float] = None,
    clear_api_key: bool = False,
    write_ovcli: bool = False,
) -> dict:
    paths = ensure_state_layout(paths or get_runtime_paths())
    config = load_runtime_config(paths)
    config["schema_version"] = int(config.get("schema_version") or 1)
    if url is not None:
        config["openviking_url"] = _normalize_url(url)
    elif not config.get("openviking_url"):
        config["openviking_url"] = DEFAULT_OPENVIKING_URL
    if clear_api_key:
        config["openviking_api_key"] = ""
    elif api_key is not None:
        config["openviking_api_key"] = str(api_key)
    if account is not None:
        config["openviking_account"] = str(account)
    if user is not None:
        config["openviking_user"] = str(user)
    if agent_id is not None:
        config["openviking_agent_id"] = str(agent_id)
    elif not config.get("openviking_agent_id"):
        config["openviking_agent_id"] = DEFAULT_OPENVIKING_AGENT_ID
    if timeout is not None:
        config["openviking_timeout"] = _safe_float(timeout, DEFAULT_OPENVIKING_TIMEOUT)
    elif not config.get("openviking_timeout"):
        config["openviking_timeout"] = DEFAULT_OPENVIKING_TIMEOUT

    atomic_write_json(runtime_config_path(paths), config)

    ovcli_path = ""
    if write_ovcli:
        connection = load_openviking_connection(paths)
        ovcli_payload = {
            "url": connection.url,
            "api_key": connection.api_key or None,
            "account": connection.account or None,
            "user": connection.user or None,
            "agent_id": connection.agent_id or None,
            "timeout": connection.timeout,
        }
        OPENVIKING_CLI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(OPENVIKING_CLI_CONFIG_PATH, ovcli_payload)
        try:
            os.chmod(OPENVIKING_CLI_CONFIG_PATH, 0o600)
        except OSError:
            pass
        ovcli_path = str(OPENVIKING_CLI_CONFIG_PATH)

    redacted = load_openviking_connection(paths).redacted()
    return {
        "config_path": str(runtime_config_path(paths)),
        "ovcli_config_path": ovcli_path,
        "connection": redacted,
    }


def config_preview(paths=None, overrides: Optional[Mapping[str, Any]] = None, write_ovcli: bool = False) -> dict:
    paths = ensure_state_layout(paths or get_runtime_paths())
    connection = load_openviking_connection(paths, overrides=overrides)
    return {
        "config_path": str(runtime_config_path(paths)),
        "ovcli_config_path": str(OPENVIKING_CLI_CONFIG_PATH if write_ovcli else ""),
        "connection": connection.redacted(),
    }


def openviking_version() -> str:
    for package in ("openviking",):
        try:
            return importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
    return ""


def _subprocess_step(
    *,
    name: str,
    command: list[str],
    dry_run: bool = False,
    cwd: Optional[Path] = None,
) -> dict:
    payload = {
        "name": name,
        "command": command,
        "dry_run": bool(dry_run),
        "status": "dry_run" if dry_run else "pending",
        "returncode": None,
    }
    if dry_run:
        return payload
    try:
        completed = subprocess.run(command, cwd=str(cwd) if cwd else None, check=False)
    except FileNotFoundError as exc:
        payload["status"] = "failed"
        payload["error"] = str(exc)
        return payload
    payload["returncode"] = completed.returncode
    payload["status"] = "ok" if completed.returncode == 0 else "failed"
    return payload


def install_openviking(
    *,
    package: str = "openviking",
    python_bin: str = sys.executable,
    force_reinstall: bool = True,
    dry_run: bool = False,
) -> dict:
    cmd = [python_bin, "-m", "pip", "install", package, "--upgrade"]
    if force_reinstall:
        cmd.append("--force-reinstall")
    payload = {
        "command": cmd,
        "dry_run": bool(dry_run),
        "openviking_version_before": openviking_version(),
    }
    if dry_run:
        payload["returncode"] = None
        return payload
    completed = subprocess.run(cmd, check=False)
    payload["returncode"] = completed.returncode
    payload["openviking_version_after"] = openviking_version()
    payload["ok"] = completed.returncode == 0
    if completed.returncode != 0:
        raise OpenVikingError("OpenViking install failed with exit code {}".format(completed.returncode))
    return payload


class OpenVikingHTTPClient:
    def __init__(self, connection: OpenVikingConnection):
        self.connection = connection

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "openrelix-openviking/1",
        }
        if self.connection.extra_headers:
            headers.update(dict(self.connection.extra_headers))
        if self.connection.api_key:
            headers["X-API-Key"] = self.connection.api_key
        if self.connection.account:
            headers["X-OpenViking-Account"] = self.connection.account
        if self.connection.user:
            headers["X-OpenViking-User"] = self.connection.user
        if self.connection.agent_id:
            headers["X-OpenViking-Agent"] = self.connection.agent_id
        return headers

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Mapping[str, Any]] = None,
        query: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        url = self.connection.url.rstrip("/") + "/" + path.lstrip("/")
        if query:
            url = url + "?" + urllib.parse.urlencode({key: value for key, value in query.items() if value is not None})
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=self._headers(), method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=self.connection.timeout) as response:
                response_text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            response_text = exc.read().decode("utf-8", errors="replace")
            raise OpenVikingError("OpenViking HTTP {} for {}: {}".format(exc.code, path, response_text[:500])) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OpenVikingError("OpenViking request failed for {}: {}".format(path, exc)) from exc

        if not response_text.strip():
            return {}
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise OpenVikingError("OpenViking returned non-JSON response for {}: {}".format(path, response_text[:500])) from exc
        if isinstance(payload, Mapping) and payload.get("status") == "error":
            error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
            message = error.get("message") or payload.get("message") or json_dumps(payload)
            raise OpenVikingError("OpenViking error for {}: {}".format(path, message))
        if isinstance(payload, Mapping) and "result" in payload:
            return payload.get("result")
        return payload

    def health(self) -> dict:
        result = self._request("GET", "/health")
        return result if isinstance(result, dict) else {"result": result}

    def create_session(self, session_id: str) -> dict:
        result = self._request("POST", "/api/v1/sessions", {"session_id": session_id})
        return result if isinstance(result, dict) else {}

    def batch_add_messages(self, session_id: str, messages: list[dict]) -> dict:
        result = self._request(
            "POST",
            "/api/v1/sessions/{}/messages/batch".format(urllib.parse.quote(session_id, safe="")),
            {"messages": messages},
        )
        return result if isinstance(result, dict) else {}

    def commit_session(self, session_id: str, keep_recent_count: int = 0) -> dict:
        result = self._request(
            "POST",
            "/api/v1/sessions/{}/commit".format(urllib.parse.quote(session_id, safe="")),
            {"keep_recent_count": keep_recent_count},
        )
        return result if isinstance(result, dict) else {}

    def get_task(self, task_id: str) -> dict:
        result = self._request("GET", "/api/v1/tasks/{}".format(urllib.parse.quote(task_id, safe="")))
        return result if isinstance(result, dict) else {}

    def get_archive(self, session_id: str, archive_id: str) -> dict:
        result = self._request(
            "GET",
            "/api/v1/sessions/{}/archives/{}".format(
                urllib.parse.quote(session_id, safe=""),
                urllib.parse.quote(archive_id, safe=""),
            ),
        )
        return result if isinstance(result, dict) else {}

    def search_find(self, query: str, limit: int = 10) -> Any:
        return self._request("POST", "/api/v1/search/find", {"query": query, "limit": limit})


def openviking_status(paths=None, connection: Optional[OpenVikingConnection] = None) -> dict:
    paths = paths or get_runtime_paths()
    connection = connection or load_openviking_connection(paths)
    payload = {
        "connection": connection.redacted(),
        "config_path": str(runtime_config_path(paths)),
        "ovcli_config_path": str(ovcli_config_path()),
        "python_package_version": openviking_version(),
        "commands": {
            "openviking-server": shutil.which("openviking-server") or "",
            "ov": shutil.which("ov") or "",
        },
        "health": {"ok": False, "error": ""},
    }
    try:
        health = OpenVikingHTTPClient(connection).health()
        payload["health"] = {"ok": bool(health.get("healthy", True)), "response": health}
    except OpenVikingError as exc:
        payload["health"] = {"ok": False, "error": str(exc)}
    return openrelix_model_runner.sanitize_model_input(payload)


def parse_date(value: Optional[str], fallback: Optional[str] = None) -> str:
    text = compact_text(value or fallback or today_str())
    try:
        return date_cls.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD: {}".format(text)) from exc


def resolve_date_window(
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    date: Optional[str] = None,
    days: int = DEFAULT_SETUP_BACKFILL_DAYS,
) -> tuple[str, str]:
    resolved_to = parse_date(date_to, date or today_str())
    if date_from:
        resolved_from = parse_date(date_from, resolved_to)
    else:
        count = max(1, int(days or DEFAULT_SETUP_BACKFILL_DAYS))
        end = date_cls.fromisoformat(resolved_to)
        resolved_from = (end - timedelta(days=count - 1)).isoformat()
    if date_cls.fromisoformat(resolved_from) > date_cls.fromisoformat(resolved_to):
        raise ValueError("date_from cannot be later than date_to")
    return resolved_from, resolved_to


def date_range(date_from: str, date_to: str) -> list[str]:
    start = date_cls.fromisoformat(date_from)
    end = date_cls.fromisoformat(date_to)
    if start > end:
        raise ValueError("date_from cannot be later than date_to")
    return [
        date_cls.fromordinal(start.toordinal() + offset).isoformat()
        for offset in range((end - start).days + 1)
    ]


def daily_summary_path(paths, target_date: str) -> Path:
    nested = paths.consolidated_daily_dir / target_date / "summary.json"
    if nested.exists():
        return nested
    return paths.consolidated_daily_dir / "{}.json".format(target_date)


def load_daily_summary(paths, target_date: str) -> dict:
    path = daily_summary_path(paths, target_date)
    payload = read_json(path)
    if not payload:
        return {}
    payload.setdefault("date", target_date)
    return payload


def _row_date(row: Mapping[str, Any]) -> str:
    return compact_text(row.get("date") or row.get("created_at") or row.get("updated_at"))[:10]


def _matches_project(row: Mapping[str, Any], project: str) -> bool:
    project = project.strip().lower()
    if not project:
        return True
    values = {
        compact_text(row.get("project_key")).lower(),
        compact_text(row.get("project_label")).lower(),
    }
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    values.add(compact_text(metadata.get("project_key")).lower())
    values.add(compact_text(metadata.get("project_label")).lower())
    return project in values


def select_memory_rows(paths, *, date_from: str, date_to: str, project: str = "", limit: int = 50) -> list[dict]:
    rows = read_jsonl(paths.registry_dir / "memory_entries.jsonl")
    if not rows:
        rows = read_jsonl(paths.registry_dir / "memory_items.jsonl")
    selected = []
    for row in rows:
        item_date = _row_date(row)
        if item_date and (item_date < date_from or item_date > date_to):
            continue
        if not _matches_project(row, project):
            continue
        if compact_text(row.get("bucket")) == "low_priority":
            continue
        selected.append(openrelix_model_runner.sanitize_model_input(row))
    selected.sort(
        key=lambda row: (
            _row_date(row),
            compact_text(row.get("priority")),
            compact_text(row.get("title")),
        ),
        reverse=True,
    )
    return selected[: max(0, int(limit or 0))]


def summarize_daily_payload(summary: Mapping[str, Any]) -> dict:
    windows = []
    for window in summary.get("window_summaries") or []:
        if not isinstance(window, Mapping):
            continue
        windows.append({
            "window_id": compact_text(window.get("window_id")),
            "question_summary": compact_text(window.get("question_summary")),
            "main_takeaway": compact_text(window.get("main_takeaway")),
            "project_keys": window.get("project_keys") if isinstance(window.get("project_keys"), list) else [],
        })
        if len(windows) >= 12:
            break
    return {
        "date": compact_text(summary.get("date")),
        "stage": compact_text(summary.get("stage")),
        "day_summary": compact_text(summary.get("day_summary") or summary.get("summary")),
        "durable_memory_count": len(summary.get("durable_memories") or []),
        "session_memory_count": len(summary.get("session_memories") or []),
        "window_summaries": windows,
    }


def render_memory_row(row: Mapping[str, Any]) -> str:
    parts = [
        "date={}".format(_row_date(row) or "-"),
        "scope={}".format(compact_text(row.get("scope")) or "-"),
        "policy={}".format(compact_text(row.get("injection_policy")) or "-"),
        "priority={}".format(compact_text(row.get("priority")) or "-"),
    ]
    project = compact_text(row.get("project_key") or row.get("project_label"))
    if project:
        parts.append("project={}".format(project))
    title = compact_text(row.get("title")) or "(untitled memory)"
    value_note = compact_text(row.get("value_note") or row.get("summary"))
    keywords = row.get("keywords") if isinstance(row.get("keywords"), list) else []
    line = "- {} ({})\n  {}".format(title, ", ".join(parts), value_note or "-")
    if keywords:
        line += "\n  keywords: {}".format(", ".join(compact_text(item) for item in keywords[:8] if compact_text(item)))
    return line


def chunk_text(title: str, items: list[str], *, max_chars: int = DEFAULT_MESSAGE_CHARS) -> list[str]:
    chunks: list[str] = []
    current = title.rstrip() + "\n\n"
    for item in items:
        addition = item.rstrip() + "\n\n"
        if len(current) + len(addition) > max_chars and current.strip() != title.strip():
            chunks.append(current.rstrip())
            current = title.rstrip() + "\n\n" + addition
        else:
            current += addition
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def build_summary_source_packet(
    paths,
    *,
    date_from: str,
    date_to: str,
    project: str = "",
    limit: int = 50,
) -> dict:
    dates = date_range(date_from, date_to)
    daily_summaries = [
        summarize_daily_payload(summary)
        for target_date in dates
        for summary in [load_daily_summary(paths, target_date)]
        if summary
    ]
    memory_rows = select_memory_rows(paths, date_from=date_from, date_to=date_to, project=project, limit=limit)
    packet = {
        "schema_version": 1,
        "source": "openrelix",
        "purpose": "openviking_memory_summary",
        "project": project,
        "date_from": date_from,
        "date_to": date_to,
        "daily_summaries": daily_summaries,
        "memory_rows": memory_rows,
    }
    return openrelix_model_runner.sanitize_model_input(packet)


def source_counts(packet: Mapping[str, Any]) -> dict:
    return {
        "daily_summaries": len(packet.get("daily_summaries") or []),
        "memory_rows": len(packet.get("memory_rows") or []),
        "messages": len(build_openviking_messages(packet)),
    }


def build_openviking_messages(packet: Mapping[str, Any]) -> list[dict]:
    messages = [
        {
            "role": "system",
            "content": (
                "You are receiving an OpenRelix memory summary packet. "
                "Archive it as project-scoped context where applicable, extract durable memories only when evidence is explicit, "
                "and keep draft summaries reviewable before reuse."
            ),
        }
    ]
    daily_items = [
        json.dumps(summary, ensure_ascii=False, sort_keys=True)
        for summary in packet.get("daily_summaries") or []
        if isinstance(summary, Mapping)
    ]
    for chunk in chunk_text("OpenRelix daily summaries", daily_items):
        if compact_text(chunk) != "OpenRelix daily summaries":
            messages.append({"role": "user", "content": chunk})

    memory_items = [
        render_memory_row(row)
        for row in packet.get("memory_rows") or []
        if isinstance(row, Mapping)
    ]
    for chunk in chunk_text("OpenRelix memory registry rows", memory_items):
        if compact_text(chunk) != "OpenRelix memory registry rows":
            messages.append({"role": "user", "content": chunk})

    summary_line = {
        "date_from": packet.get("date_from"),
        "date_to": packet.get("date_to"),
        "project": packet.get("project"),
        "daily_summary_count": len(packet.get("daily_summaries") or []),
        "memory_row_count": len(packet.get("memory_rows") or []),
    }
    messages.append({
        "role": "user",
        "content": "OpenRelix import summary:\n{}".format(json.dumps(summary_line, ensure_ascii=False, sort_keys=True)),
    })
    return messages[:MAX_BATCH_MESSAGES]


def source_fingerprint(packet: Mapping[str, Any]) -> str:
    rendered = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def slug_component(value: str, fallback: str = "all") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or fallback


def default_session_id(packet: Mapping[str, Any], created_at: str) -> str:
    stamp = created_at.replace("-", "").replace(":", "").replace("Z", "Z")
    project = slug_component(compact_text(packet.get("project")), fallback="all")
    digest = source_fingerprint(packet)[:8]
    return "orx-{}-{}-{}-{}-{}".format(
        project,
        compact_text(packet.get("date_from")).replace("-", ""),
        compact_text(packet.get("date_to")).replace("-", ""),
        stamp,
        digest,
    )[:120]


def poll_task(
    client: OpenVikingHTTPClient,
    task_id: str,
    *,
    timeout: float = 180.0,
    interval: float = 2.0,
) -> dict:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        task = client.get_task(task_id)
        status = compact_text(task.get("status")).lower()
        if status == "completed":
            return task
        if status == "failed":
            raise OpenVikingError("OpenViking task failed: {}".format(task.get("error") or task_id))
        if timeout <= 0 or time.monotonic() >= deadline:
            raise OpenVikingError("OpenViking task timed out: {} last_status={}".format(task_id, status or "-"))
        time.sleep(max(0.1, interval))


def archive_id_from_uri(uri: str) -> str:
    text = compact_text(uri).rstrip("/")
    if not text:
        return ""
    return text.split("/")[-1]


def first_from_mappings(mappings: Iterable[Mapping[str, Any]], *keys: str) -> Any:
    for mapping in mappings:
        for key in keys:
            if isinstance(mapping, Mapping) and mapping.get(key) not in (None, ""):
                return mapping.get(key)
    return ""


def openviking_export_row(
    *,
    packet: Mapping[str, Any],
    session_id: str,
    commit_result: Mapping[str, Any],
    task: Mapping[str, Any],
    archive: Mapping[str, Any],
    created_at: str,
) -> dict:
    task_result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
    archive_uri = compact_text(first_from_mappings((commit_result, task_result, archive), "archive_uri", "uri"))
    archive_id = compact_text(first_from_mappings((commit_result, task_result, archive), "archive_id"))
    if not archive_id:
        archive_id = archive_id_from_uri(archive_uri)
    if not archive_uri and archive_id:
        archive_uri = "viking://session/{}/archives/{}".format(session_id, archive_id)
    abstract = compact_text(first_from_mappings((archive, task_result, commit_result), "abstract", "summary"))
    overview = compact_text(first_from_mappings((archive, task_result, commit_result), "overview", "content", "text"))
    if not overview:
        overview = "OpenViking archived {} OpenRelix messages for {} to {}.".format(
            len(build_openviking_messages(packet)),
            packet.get("date_from"),
            packet.get("date_to"),
        )
    if not abstract:
        abstract = overview[:360]
    project = compact_text(packet.get("project"))
    date_from = compact_text(packet.get("date_from"))
    date_to = compact_text(packet.get("date_to"))
    title_scope = project or "all projects"
    return {
        "schema_version": 1,
        "uri": archive_uri,
        "context_type": "openviking.session_archive",
        "level": "L1",
        "title": "OpenViking summary for {} {}..{}".format(title_scope, date_from, date_to),
        "abstract": abstract,
        "overview": overview,
        "session_id": session_id,
        "archive_id": archive_id,
        "task_id": compact_text(first_from_mappings((commit_result, task), "task_id")),
        "score": 0.82,
        "created_at": created_at,
        "updated_at": created_at,
        "metadata": {
            "project_key": project,
            "date_from": date_from,
            "date_to": date_to,
            "input_fingerprint": "sha256:{}".format(source_fingerprint(packet)),
            "daily_summary_count": len(packet.get("daily_summaries") or []),
            "memory_row_count": len(packet.get("memory_rows") or []),
            "message_count": len(build_openviking_messages(packet)),
            "memories_extracted": task_result.get("memories_extracted", task.get("memories_extracted", "")),
        },
    }


def summarize_openrelix_memory(
    *,
    paths=None,
    connection: Optional[OpenVikingConnection] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    project: str = "",
    limit: int = 50,
    session_id: str = "",
    wait: bool = True,
    task_timeout: float = 180.0,
    poll_interval: float = 2.0,
    dry_run: bool = False,
) -> dict:
    paths = ensure_state_layout(paths or get_runtime_paths())
    resolved_to = parse_date(date_to, today_str())
    resolved_from = parse_date(date_from, resolved_to)
    packet = build_summary_source_packet(
        paths,
        date_from=resolved_from,
        date_to=resolved_to,
        project=project,
        limit=limit,
    )
    messages = build_openviking_messages(packet)
    counts = source_counts(packet)
    if counts["daily_summaries"] == 0 and counts["memory_rows"] == 0:
        raise OpenVikingError("No OpenRelix source material found for {}..{}".format(resolved_from, resolved_to))
    created_at = utc_now()
    resolved_session_id = compact_text(session_id) or default_session_id(packet, created_at)
    if dry_run:
        return {
            "dry_run": True,
            "session_id": resolved_session_id,
            "source_counts": counts,
            "message_preview": messages[:3],
            "input_fingerprint": "sha256:{}".format(source_fingerprint(packet)),
        }

    connection = connection or load_openviking_connection(paths)
    client = OpenVikingHTTPClient(connection)
    create_result = client.create_session(resolved_session_id)
    add_result = client.batch_add_messages(resolved_session_id, messages)
    commit_result = client.commit_session(resolved_session_id)
    task_id = compact_text(commit_result.get("task_id"))
    task: dict = {}
    if wait and task_id:
        task = poll_task(client, task_id, timeout=task_timeout, interval=poll_interval)
    archive_uri = compact_text(first_from_mappings((commit_result, task.get("result") or {}), "archive_uri"))
    archive_id = compact_text(first_from_mappings((commit_result, task.get("result") or {}), "archive_id"))
    if not archive_id:
        archive_id = archive_id_from_uri(archive_uri)
    archive: dict = {}
    if archive_id:
        archive = client.get_archive(resolved_session_id, archive_id)

    export_row = openviking_export_row(
        packet=packet,
        session_id=resolved_session_id,
        commit_result=commit_result,
        task=task,
        archive=archive,
        created_at=created_at,
    )
    export_path = paths.registry_dir / OPENVIKING_EXPORT_REGISTRY
    upsert_jsonl(export_path, [export_row], ("uri",))
    summary_payload = build_openviking_summaries.build_openviking_summaries(
        paths=paths,
        source=str(export_path),
        date=resolved_to,
        dry_run=False,
        limit=max(1, int(limit or 1)),
    )
    return {
        "dry_run": False,
        "connection": connection.redacted(),
        "session_id": resolved_session_id,
        "create_result": create_result,
        "add_result": add_result,
        "commit_result": commit_result,
        "task": task,
        "archive_id": export_row.get("archive_id"),
        "archive_uri": export_row.get("uri"),
        "source_counts": counts,
        "export_registry": str(export_path),
        "summary": summary_payload,
    }


def setup_openviking_defaults(
    *,
    paths=None,
    url: str = DEFAULT_OPENVIKING_URL,
    api_key: Optional[str] = None,
    account: Optional[str] = None,
    user: Optional[str] = None,
    agent_id: str = DEFAULT_OPENVIKING_AGENT_ID,
    timeout: float = DEFAULT_OPENVIKING_TIMEOUT,
    write_ovcli: bool = True,
    install_mode: str = "auto",
    package: str = "openviking",
    force_reinstall: bool = True,
    server_init: bool = False,
    doctor: bool = False,
    run_backfill: bool = True,
    backfill_stage: str = "final",
    force_backfill: bool = False,
    jobs: int = 1,
    learn_window_days: int = 0,
    run_summarize: bool = True,
    require_service: bool = False,
    date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    days: int = DEFAULT_SETUP_BACKFILL_DAYS,
    project: str = "",
    limit: int = 50,
    wait: bool = True,
    task_timeout: float = 180.0,
    poll_interval: float = 2.0,
    dry_run: bool = False,
) -> dict:
    paths = ensure_state_layout(paths or get_runtime_paths())
    resolved_from, resolved_to = resolve_date_window(
        date_from=date_from,
        date_to=date_to,
        date=date,
        days=days,
    )
    payload = {
        "dry_run": bool(dry_run),
        "date_from": resolved_from,
        "date_to": resolved_to,
        "project": project,
        "steps": [],
        "next_steps": [],
    }

    overrides = {
        "openviking_url": url,
        "openviking_api_key": api_key,
        "openviking_account": account,
        "openviking_user": user,
        "openviking_agent_id": agent_id,
        "openviking_timeout": timeout,
    }
    if dry_run:
        config_payload = config_preview(paths, overrides=overrides, write_ovcli=write_ovcli)
        config_step = {"name": "config", "status": "dry_run", "result": config_payload}
    else:
        config_payload = write_openviking_config(
            paths=paths,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            agent_id=agent_id,
            timeout=timeout,
            write_ovcli=write_ovcli,
        )
        config_step = {"name": "config", "status": "ok", "result": config_payload}
    payload["steps"].append(config_step)

    normalized_install_mode = str(install_mode or "auto").strip().lower()
    if normalized_install_mode not in {"auto", "always", "never"}:
        raise ValueError("install_mode must be auto, always, or never")
    installed = bool(openviking_version() or shutil.which("openviking-server"))
    should_install = normalized_install_mode == "always" or (
        normalized_install_mode == "auto" and not installed
    )
    if should_install:
        install_payload = install_openviking(
            package=package,
            force_reinstall=force_reinstall,
            dry_run=dry_run,
        )
        payload["steps"].append({
            "name": "install",
            "status": "dry_run" if dry_run else "ok",
            "result": openrelix_model_runner.sanitize_model_input(install_payload),
        })
    else:
        payload["steps"].append({
            "name": "install",
            "status": "skipped",
            "reason": "already_installed" if installed else "disabled",
        })

    if server_init:
        server_init_cmd = ["openviking-server", "init"]
        payload["steps"].append(_subprocess_step(
            name="server_init",
            command=server_init_cmd,
            dry_run=dry_run,
        ))
    else:
        payload["steps"].append({
            "name": "server_init",
            "status": "skipped",
            "reason": "disabled",
        })

    if doctor:
        doctor_cmd = ["openviking-server", "doctor"]
        payload["steps"].append(_subprocess_step(
            name="doctor",
            command=doctor_cmd,
            dry_run=dry_run,
        ))

    if run_backfill:
        backfill_cmd = [
            sys.executable,
            str(paths.repo_root / "scripts" / "openrelix.py"),
            "backfill",
            "--from",
            resolved_from,
            "--to",
            resolved_to,
            "--stage",
            backfill_stage,
            "--jobs",
            str(max(1, int(jobs or 1))),
        ]
        if learn_window_days:
            backfill_cmd.extend(["--learn-window-days", str(max(0, int(learn_window_days)))])
        if force_backfill:
            backfill_cmd.append("--force")
        payload["steps"].append(_subprocess_step(
            name="backfill",
            command=backfill_cmd,
            dry_run=dry_run,
            cwd=paths.repo_root,
        ))
    else:
        payload["steps"].append({
            "name": "backfill",
            "status": "skipped",
            "reason": "disabled",
        })

    connection = load_openviking_connection(paths, overrides=overrides)
    if dry_run:
        health = {"ok": False, "status": "dry_run"}
    else:
        health = openviking_status(paths, connection=connection)["health"]
    payload["steps"].append({"name": "health", "status": "ok" if health.get("ok") else "skipped", "result": health})

    packet = build_summary_source_packet(
        paths,
        date_from=resolved_from,
        date_to=resolved_to,
        project=project,
        limit=limit,
    )
    counts = source_counts(packet)
    payload["source_counts"] = counts

    if not run_summarize:
        payload["steps"].append({"name": "summarize", "status": "skipped", "reason": "disabled"})
    elif counts["daily_summaries"] == 0 and counts["memory_rows"] == 0:
        payload["steps"].append({"name": "summarize", "status": "skipped", "reason": "no_source_material"})
    elif not dry_run and not health.get("ok"):
        reason = health.get("error") or "service_not_healthy"
        if require_service:
            raise OpenVikingError("OpenViking service is not healthy: {}".format(reason))
        payload["steps"].append({"name": "summarize", "status": "skipped", "reason": reason})
    else:
        summary_payload = summarize_openrelix_memory(
            paths=paths,
            connection=connection,
            date_from=resolved_from,
            date_to=resolved_to,
            project=project,
            limit=limit,
            wait=wait,
            task_timeout=task_timeout,
            poll_interval=poll_interval,
            dry_run=dry_run,
        )
        payload["steps"].append({
            "name": "summarize",
            "status": "dry_run" if dry_run else "ok",
            "result": openrelix_model_runner.sanitize_model_input(summary_payload),
        })

    if not server_init:
        payload["next_steps"].append("Run `openviking-server init` if server configuration has not been created yet.")
    if not dry_run and not health.get("ok"):
        payload["next_steps"].append("Start OpenViking with `openviking-server`, then rerun `openrelix openviking setup --skip-backfill`.")
    if counts["daily_summaries"] == 0 and counts["memory_rows"] == 0:
        payload["next_steps"].append("Run OpenRelix review/backfill for the target dates before OpenViking summarization.")
    return openrelix_model_runner.sanitize_model_input(payload)
