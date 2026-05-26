#!/usr/bin/env python3
"""Build local OpenViking summary documents from OpenViking export rows."""

from __future__ import annotations

import argparse
from datetime import date as date_cls
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from asset_runtime import atomic_write_json, atomic_write_text, ensure_state_layout, get_runtime_paths
import openrelix_model_runner
from openrelix_overview import knowledge_docs


DEFAULT_SOURCE_REGISTRY = "openviking_memory_exports.jsonl"
SUMMARY_DOC_REGISTRY = "openviking_summary_docs.jsonl"
QUALITY_FALLBACK = 0.75


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def today_str() -> str:
    return date_cls.today().isoformat()


def compact_text(value: Any) -> str:
    return knowledge_docs.compact_text(value)


def safe_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def json_dumps(row: Mapping[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            rows.append(dict(value))
        elif isinstance(value, list):
            rows.extend(dict(item) for item in value if isinstance(item, Mapping))
    return rows


def read_source_payload(path: Path) -> Any:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix == ".jsonl":
        return read_jsonl(path)
    return json.loads(text)


def looks_like_openviking_item(value: Mapping[str, Any]) -> bool:
    return any(
        key in value
        for key in (
            "uri",
            "abstract",
            "overview",
            "context_type",
            "level",
            "session_id",
            "archive_id",
            "memory_diff",
        )
    )


def iter_openviking_items(value: Any) -> Iterable[dict]:
    if isinstance(value, list):
        for item in value:
            yield from iter_openviking_items(item)
        return
    if not isinstance(value, Mapping):
        return
    if looks_like_openviking_item(value):
        yield dict(value)
    for key in (
        "data",
        "items",
        "results",
        "summaries",
        "memories",
        "resources",
        "skills",
        "added",
        "updated",
        "memory_diff",
    ):
        nested = value.get(key)
        if isinstance(nested, (list, Mapping)):
            yield from iter_openviking_items(nested)


def default_feishu_export() -> dict:
    return {
        "status": "not_configured",
        "doc_url": "",
        "doc_token": "",
        "updated_at": "",
        "error_hint": "",
    }


def default_visibility() -> dict:
    return {
        "panel": True,
        "default_search": False,
        "host_context": False,
        "trust_level": "draft",
    }


def first_text(row: Mapping[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and compact_text(value):
            return compact_text(value)
    return ""


def text_preview(value: Any, limit: int = 360) -> str:
    text = compact_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def row_timestamp(row: Mapping[str, Any], fallback: str) -> str:
    for key in ("updated_at", "created_at", "timestamp", "committed_at"):
        value = compact_text(row.get(key))
        if value:
            return value
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    for key in ("updated_at", "created_at", "timestamp", "committed_at"):
        value = compact_text(metadata.get(key))
        if value:
            return value
    return fallback


def row_year(row: Mapping[str, Any], target_date: str, created_at: str) -> str:
    for value in (target_date, row_timestamp(row, ""), created_at):
        text = compact_text(value)
        if len(text) >= 4 and text[:4].isdigit():
            return text[:4]
    return today_str()[:4]


def source_fingerprint(row: Mapping[str, Any]) -> str:
    sanitized = openrelix_model_runner.sanitize_model_input(row)
    return "sha256:{}".format(hashlib.sha256(json_dumps(sanitized).encode("utf-8")).hexdigest())


def doc_id_for(fingerprint: str) -> str:
    return "ovdoc-{}".format(hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12])


def source_refs_for(row: Mapping[str, Any], source_path: Path) -> dict:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}

    def values(*keys: str) -> list[str]:
        found = []
        for key in keys:
            raw = row.get(key)
            if raw is None:
                raw = metadata.get(key)
            if isinstance(raw, list):
                found.extend(compact_text(item) for item in raw if compact_text(item))
            elif compact_text(raw):
                found.append(compact_text(raw))
        return list(dict.fromkeys(found))

    return {
        "openviking_uris": values("uri", "memory_uri", "resource_uri"),
        "session_ids": values("session_id", "session"),
        "archive_ids": values("archive_id", "archive"),
        "task_ids": values("task_id", "commit_task_id"),
        "levels": values("level"),
        "context_types": values("context_type", "type"),
        "source_files": [source_path.name],
    }


def normalize_openviking_summary_doc(
    row: Mapping[str, Any],
    *,
    source_path: Path,
    target_date: str,
    created_at: str,
) -> dict:
    row = openrelix_model_runner.sanitize_model_input(dict(row))
    fingerprint = source_fingerprint(row)
    doc_id = doc_id_for(fingerprint)
    uri = compact_text(row.get("uri") or row.get("memory_uri") or row.get("resource_uri"))
    title = first_text(row, ("title", "name", "label"))
    if not title:
        title = text_preview(row.get("abstract") or row.get("overview") or uri or doc_id, 80)
    if not title:
        title = "OpenViking summary"
    abstract = first_text(row, ("abstract", "summary"))
    overview = first_text(row, ("overview", "content", "text", "markdown"))
    summary = text_preview(overview or abstract or title)
    project_key = compact_text(row.get("project_key") or (row.get("metadata") or {}).get("project_key"))
    project_label = compact_text(row.get("project_label") or (row.get("metadata") or {}).get("project_label"))
    scope = "project" if project_key or project_label else "openviking"
    canonical_scope = project_key or project_label or "openviking"
    canonical_key = "{}:openviking_summary:{}".format(
        knowledge_docs.slug_component(canonical_scope, fallback="openviking"),
        knowledge_docs.slug_component(title),
    )
    score = safe_number(row.get("score"), QUALITY_FALLBACK)
    if score > 1:
        score = QUALITY_FALLBACK
    source_refs = source_refs_for(row, source_path)
    year = row_year(row, target_date, created_at)
    slug = knowledge_docs.slug_component(title)
    return {
        "schema_version": 1,
        "algorithm_version": 1,
        "doc_id": doc_id,
        "version": 1,
        "status": "draft",
        "summary_type": "openviking_summary",
        "title": title,
        "summary": summary,
        "body_path": "openviking/docs/{}/{}.md".format(year, slug),
        "canonical_key": canonical_key,
        "source_fingerprint": fingerprint,
        "source_refs": source_refs,
        "source_contexts": [
            {
                "source": "openviking",
                "uri": uri,
                "context_type": compact_text(row.get("context_type")),
                "level": compact_text(row.get("level")),
                "overview": overview,
                "abstract": abstract,
            }
        ],
        "project_key": project_key,
        "project_label": project_label,
        "generation_mode": "openviking_summary",
        "scope": scope,
        "sensitivity": "internal",
        "quality_score": max(0.0, min(1.0, score)),
        "reviewer_state": "needs_review",
        "redaction_status": "publish_safe",
        "model_status": "success",
        "visibility": default_visibility(),
        "feishu_export": default_feishu_export(),
        "created_at": row_timestamp(row, created_at),
        "updated_at": row_timestamp(row, created_at),
        "openviking": {
            "uri": uri,
            "context_type": compact_text(row.get("context_type")),
            "level": compact_text(row.get("level")),
            "relations": row.get("relations") if isinstance(row.get("relations"), list) else [],
        },
        "body_sections": {
            "abstract": abstract,
            "overview": overview,
            "evidence": [
                "{}: {}".format(label, ", ".join(values))
                for label, values in source_refs.items()
                if values
            ],
            "limits": "OpenViking summary draft; review before publishing or injecting into host context.",
        },
    }


def render_markdown(doc: Mapping[str, Any]) -> str:
    body = doc.get("body_sections") or {}
    lines = [
        "# {}".format(compact_text(doc.get("title")) or compact_text(doc.get("doc_id"))),
        "",
        "Status: {}".format(compact_text(doc.get("status")) or "draft"),
        "Source: OpenViking",
        "",
        compact_text(doc.get("summary")),
        "",
        "## Overview",
        "",
        compact_text(body.get("overview")) or compact_text(body.get("abstract")) or compact_text(doc.get("summary")),
        "",
        "## Abstract",
        "",
        compact_text(body.get("abstract")) or "-",
        "",
        "## Evidence",
        "",
    ]
    for item in body.get("evidence") or []:
        lines.append("- {}".format(compact_text(item)))
    lines.extend(["", "## Limits", "", compact_text(body.get("limits")) or "-"])
    return "\n".join(lines).rstrip() + "\n"


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    rendered = [json_dumps(row) for row in rows]
    atomic_write_text(path, "\n".join(rendered) + ("\n" if rendered else ""))


def upsert_jsonl(path: Path, rows: Iterable[Mapping[str, Any]], key_fields: tuple[str, ...]) -> int:
    existing = read_jsonl(path)
    by_key = {tuple(row.get(field) for field in key_fields): row for row in existing}
    changed = 0
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        if by_key.get(key) != dict(row):
            changed += 1
        by_key[key] = dict(row)
    write_jsonl(path, by_key.values())
    return changed


def run_id_for(source_path: Path, created_at: str) -> str:
    digest = hashlib.sha1(str(source_path).encode("utf-8")).hexdigest()[:8]
    stamp = created_at.replace("-", "").replace(":", "").replace("+", "").replace("Z", "Z")
    return "openviking-summary-{}-{}".format(stamp, digest)


def build_openviking_summaries(
    paths=None,
    source: Optional[str] = None,
    date: Optional[str] = None,
    dry_run: bool = False,
    limit: int = 50,
) -> dict:
    paths = ensure_state_layout(paths or get_runtime_paths())
    if source:
        source_path = Path(source).expanduser()
        if not source_path.is_absolute():
            source_path = Path.cwd() / source_path
    else:
        source_path = paths.registry_dir / DEFAULT_SOURCE_REGISTRY
    created_at = utc_now()
    target_date = compact_text(date) or today_str()
    payload = read_source_payload(source_path) if source_path.exists() else []
    raw_items = list(iter_openviking_items(payload))
    docs = [
        normalize_openviking_summary_doc(
            item,
            source_path=source_path,
            target_date=target_date,
            created_at=created_at,
        )
        for item in raw_items[: max(0, int(limit or 0))]
    ]
    if not dry_run:
        for doc in docs:
            atomic_write_text(paths.state_root / doc["body_path"], render_markdown(doc))
        upsert_jsonl(paths.registry_dir / SUMMARY_DOC_REGISTRY, docs, ("doc_id", "version"))
    run_id = run_id_for(source_path, created_at)
    run_artifact = paths.state_root / "openviking" / "runs" / run_id / "run.json"
    run_payload = {
        "run_id": run_id,
        "source": str(source_path),
        "status": "success",
        "created_docs": len(docs),
        "doc_ids": [doc["doc_id"] for doc in docs],
        "created_at": created_at,
        "dry_run": bool(dry_run),
    }
    if not dry_run:
        atomic_write_json(run_artifact, openrelix_model_runner.sanitize_model_input(run_payload))
    return dict(run_payload, run_artifact=str(run_artifact))


def list_openviking_summaries(paths=None, limit: int = 20) -> list[dict]:
    paths = ensure_state_layout(paths or get_runtime_paths())
    rows = read_jsonl(paths.registry_dir / SUMMARY_DOC_REGISTRY)
    rows.sort(key=lambda row: (str(row.get("updated_at") or ""), str(row.get("doc_id") or "")), reverse=True)
    return rows[: max(0, int(limit or 0))]


def openviking_summary_status(paths=None) -> dict:
    paths = ensure_state_layout(paths or get_runtime_paths())
    return {
        "state_root": str(paths.state_root),
        "source_registry": str(paths.registry_dir / DEFAULT_SOURCE_REGISTRY),
        "doc_registry": str(paths.registry_dir / SUMMARY_DOC_REGISTRY),
        "doc_rows": len(read_jsonl(paths.registry_dir / SUMMARY_DOC_REGISTRY)),
        "run_count": len(list((paths.state_root / "openviking" / "runs").glob("*/run.json"))),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build OpenViking summary docs.")
    parser.add_argument("--source", default="", help="OpenViking export JSON/JSONL path.")
    parser.add_argument("--date", default=today_str(), help="Target date, YYYY-MM-DD.")
    parser.add_argument("--dry-run", action="store_true", help="Compute without writing docs or registries.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum source items to convert.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_openviking_summaries(
        source=args.source,
        date=args.date,
        dry_run=args.dry_run,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("OpenViking summaries: {} docs".format(payload["created_docs"]))
        print("run_artifact: {}".format(payload["run_artifact"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
