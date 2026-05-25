#!/usr/bin/env python3
"""Build local OpenRelix knowledge document drafts from consolidated state."""

from __future__ import annotations

import argparse
import contextlib
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, Mapping, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from asset_runtime import atomic_write_json, atomic_write_text, ensure_state_layout, get_runtime_paths
import openrelix_model_runner
from openrelix_overview import knowledge_docs


DOC_REQUIRED_FIELDS = (
    "schema_version",
    "algorithm_version",
    "doc_id",
    "version",
    "status",
    "knowledge_type",
    "title",
    "summary",
    "body_path",
    "body_sections",
    "canonical_key",
    "source_fingerprint",
    "source_refs",
    "project_key",
    "project_label",
    "scope",
    "sensitivity",
    "quality_score",
    "reviewer_state",
    "redaction_status",
    "model_status",
    "visibility",
    "conflict_of_doc_ids",
    "created_at",
    "updated_at",
)
BODY_SECTION_REQUIRED_FIELDS = (
    "context",
    "decision",
    "procedure",
    "evidence",
    "limits",
    "next_actions",
)
SOURCE_REF_REQUIRED_FIELDS = ("summary_dates", "window_ids", "memory_ids", "review_paths")
VISIBILITY_REQUIRED_FIELDS = ("panel", "default_search", "host_context", "trust_level")
QUALITY_THRESHOLD = 0.50


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def today_str() -> str:
    return date_cls.today().isoformat()


def parse_date(value: str, label: str = "date") -> date_cls:
    try:
        return date_cls.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("{} must use YYYY-MM-DD: {}".format(label, value)) from exc


def resolve_date_range(date: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None) -> tuple[str, str, list[str]]:
    if date_from or date_to:
        start = parse_date(date_from or date or today_str(), "--from")
        end = parse_date(date_to or date or date_from or today_str(), "--to")
    else:
        start = parse_date(date or today_str(), "--date")
        end = start
    if start > end:
        raise ValueError("--from cannot be later than --to")
    days = [
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
    ]
    return start.isoformat(), end.isoformat(), days


def compact_text(value: Any) -> str:
    return knowledge_docs.compact_text(value)


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _json_dumps(row: Mapping[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@contextlib.contextmanager
def registry_lock(paths):
    paths.registry_dir.mkdir(parents=True, exist_ok=True)
    lock_path = paths.registry_dir / ".knowledge-registry.lock"
    with lock_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def upsert_jsonl(path: Path, rows: Iterable[Mapping[str, Any]], key_fields: tuple[str, ...]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_raw = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    preserved_bad_lines = []
    existing_rows = []
    for raw_line in existing_raw:
        if not raw_line.strip():
            continue
        try:
            existing_rows.append(json.loads(raw_line))
        except json.JSONDecodeError:
            preserved_bad_lines.append(raw_line)

    by_key = {
        tuple(row.get(field) for field in key_fields): row
        for row in existing_rows
    }
    changed = 0
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        if by_key.get(key) != dict(row):
            changed += 1
        by_key[key] = dict(row)

    rendered = list(preserved_bad_lines)
    rendered.extend(_json_dumps(row) for row in by_key.values())
    atomic_write_text(path, "\n".join(rendered) + ("\n" if rendered else ""))
    return changed


def daily_summary_path(paths, target_date: str) -> Path:
    nested = paths.consolidated_daily_dir / target_date / "summary.json"
    if nested.exists():
        return nested
    return paths.consolidated_daily_dir / "{}.json".format(target_date)


def load_daily_summary(paths, target_date: str) -> Mapping[str, Any]:
    path = daily_summary_path(paths, target_date)
    if not path.exists():
        return {"date": target_date, "window_summaries": [], "day_summary": ""}
    return read_json(path)


def load_daily_summaries(paths, target_dates: list[str]) -> list[Mapping[str, Any]]:
    return [load_daily_summary(paths, target_date) for target_date in target_dates]


def load_memory_rows(paths) -> list[dict]:
    canonical = paths.registry_dir / "memory_entries.jsonl"
    legacy = paths.registry_dir / "memory_items.jsonl"
    if canonical.exists() and canonical.stat().st_size > 0:
        return read_jsonl(canonical)
    return read_jsonl(legacy)


def infer_knowledge_type(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("bug", "fix", "error", "fail", "failure", "diagnose", "troubleshoot")):
        return "troubleshooting"
    if any(word in lowered for word in ("decision", "decide", "choose", "policy")):
        return "decision"
    if any(word in lowered for word in ("procedure", "workflow", "steps", "runbook")):
        return "procedure"
    return "project_context"


def matching_memory_rows(window: Mapping[str, Any], memory_rows: list[dict]) -> list[dict]:
    window_id = str(window.get("window_id") or "")
    if not window_id:
        return []
    return [
        row for row in memory_rows
        if window_id in {str(item) for item in row.get("source_window_ids") or []}
    ]


def candidate_quality(window: Mapping[str, Any], memories: list[Mapping[str, Any]]) -> float:
    score = 0.15
    if compact_text(window.get("main_takeaway")):
        score += 0.25
    if compact_text(window.get("question_summary")):
        score += 0.15
    if window.get("summary_pairs"):
        score += 0.15
    if memories:
        score += 0.20
    if window.get("keywords"):
        score += 0.10
    return min(1.0, round(score, 2))


def candidate_id_for(canonical_key: str, source_fingerprint: str) -> str:
    digest = hashlib.sha1("{}|{}".format(canonical_key, source_fingerprint).encode("utf-8")).hexdigest()[:12]
    return "kcand-{}".format(digest)


def memory_project_value(memories: list[Mapping[str, Any]], field: str, default: str = "") -> str:
    for row in memories:
        value = compact_text(row.get(field))
        if value:
            return value
    return default


def source_refs_for(target_date: str, window: Mapping[str, Any], memories: list[Mapping[str, Any]]) -> dict:
    window_id = compact_text(window.get("window_id"))
    memory_ids = [
        compact_text(row.get("memory_id") or row.get("memory_key") or row.get("id"))
        for row in memories
    ]
    return {
        "summary_dates": [target_date],
        "window_ids": [window_id] if window_id else [],
        "memory_ids": [item for item in memory_ids if item],
        "review_paths": [],
    }


def extract_candidates(summary: Mapping[str, Any], memory_rows: list[dict], target_date: str) -> list[dict]:
    sanitized_summary = openrelix_model_runner.sanitize_model_input(summary)
    sanitized_memories = openrelix_model_runner.sanitize_model_input(memory_rows)
    candidates = []
    for window in sanitized_summary.get("window_summaries") or []:
        if not isinstance(window, Mapping):
            continue
        memories = matching_memory_rows(window, sanitized_memories)
        quality = candidate_quality(window, memories)
        title = compact_text(window.get("window_title")) or compact_text(window.get("question_summary")) or "Knowledge draft"
        knowledge_type = infer_knowledge_type(
            " ".join(
                compact_text(part)
                for part in (
                    title,
                    window.get("question_summary"),
                    window.get("main_takeaway"),
                    " ".join(str(item) for item in window.get("keywords") or []),
                )
            )
        )
        project_key = memory_project_value(memories, "project_key", "openrelix")
        project_label = memory_project_value(memories, "project_label", "OpenRelix")
        refs = source_refs_for(target_date, window, memories)
        fingerprint_payload = {
            "schema_version": knowledge_docs.KNOWLEDGE_DOC_SCHEMA_VERSION,
            "algorithm_version": knowledge_docs.KNOWLEDGE_DOC_ALGORITHM_VERSION,
            "prompt_template": "knowledge-doc-rewrite-prompt@1",
            "date": target_date,
            "window": window,
            "memories": memories,
            "source_refs": refs,
        }
        source_fingerprint = knowledge_docs.source_fingerprint(fingerprint_payload)
        candidate = {
            "schema_version": knowledge_docs.KNOWLEDGE_DOC_SCHEMA_VERSION,
            "algorithm_version": knowledge_docs.KNOWLEDGE_DOC_ALGORITHM_VERSION,
            "candidate_id": "",
            "date": target_date,
            "decision": "draft" if quality >= QUALITY_THRESHOLD else "rejected",
            "knowledge_type": knowledge_type,
            "title": title,
            "summary": compact_text(window.get("main_takeaway")) or compact_text(window.get("question_summary")),
            "canonical_key": "",
            "source_fingerprint": source_fingerprint,
            "project_key": project_key,
            "project_label": project_label,
            "scope": "project" if project_key else "global",
            "sensitivity": "internal",
            "quality_score": quality,
            "source_refs": refs,
            "redaction_status": "source_safe",
            "model_status": openrelix_model_runner.MODEL_STATUS_NOT_RUN,
            "reason": "Reusable summary evidence met the MVP quality threshold."
            if quality >= QUALITY_THRESHOLD
            else "Evidence is too weak for a reusable knowledge draft.",
            "_source_window": window,
        }
        candidate["canonical_key"] = knowledge_docs.canonical_key(candidate)
        candidate["candidate_id"] = candidate_id_for(candidate["canonical_key"], source_fingerprint)
        candidates.append(candidate)
    return candidates


def extract_candidates_for_summaries(
    summaries: list[Mapping[str, Any]],
    memory_rows: list[dict],
    target_dates: list[str],
    project_key: str = "",
) -> list[dict]:
    candidates = []
    wanted_project = compact_text(project_key)
    for target_date, summary in zip(target_dates, summaries):
        for candidate in extract_candidates(summary, memory_rows, target_date):
            if wanted_project and compact_text(candidate.get("project_key")) != wanted_project:
                continue
            candidates.append(candidate)
    return candidates


def public_candidate(row: Mapping[str, Any]) -> dict:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def evidence_labels(source_refs: Mapping[str, Any]) -> list[str]:
    labels = []
    labels.extend("summary:{}".format(item) for item in source_refs.get("summary_dates") or [])
    labels.extend("window:{}".format(item) for item in source_refs.get("window_ids") or [])
    labels.extend("memory:{}".format(item) for item in source_refs.get("memory_ids") or [])
    labels.extend("review:{}".format(item) for item in source_refs.get("review_paths") or [])
    return labels


def render_markdown(doc: Mapping[str, Any]) -> str:
    body = doc["body_sections"]
    lines = [
        "# {}".format(doc["title"]),
        "",
        "Status: {}".format(doc["status"]),
        "",
        compact_text(doc.get("summary")),
        "",
        "## Context",
        "",
        compact_text(body.get("context")),
        "",
        "## Decision",
        "",
        compact_text(body.get("decision")),
        "",
        "## Procedure",
        "",
    ]
    for item in body.get("procedure") or []:
        lines.append("- {}".format(compact_text(item)))
    lines.extend(["", "## Evidence", ""])
    for item in body.get("evidence") or []:
        lines.append("- {}".format(compact_text(item)))
    lines.extend(["", "## Limits", "", compact_text(body.get("limits")), "", "## Next Actions", ""])
    for item in body.get("next_actions") or []:
        lines.append("- {}".format(compact_text(item)))
    return "\n".join(lines).rstrip() + "\n"


def deterministic_doc_from_candidate(candidate: Mapping[str, Any], created_at: str) -> dict:
    source_window = candidate.get("_source_window") or {}
    source_refs = candidate["source_refs"]
    title = compact_text(candidate["title"])
    body_sections = {
        "context": compact_text(source_window.get("question_summary"))
        or "A consolidated OpenRelix summary identified reusable project knowledge.",
        "decision": compact_text(source_window.get("main_takeaway"))
        or "Keep this as a local draft until review confirms the evidence.",
        "procedure": [
            compact_text(pair.get("conclusion") or pair.get("question"))
            for pair in source_window.get("summary_pairs") or []
            if isinstance(pair, Mapping) and compact_text(pair.get("conclusion") or pair.get("question"))
        ],
        "evidence": evidence_labels(source_refs),
        "limits": "Draft knowledge generated from consolidated summaries only; review before publishing or reuse.",
        "next_actions": ["Review source references before changing the document status."],
    }
    if not body_sections["procedure"]:
        body_sections["procedure"] = [compact_text(candidate.get("summary")) or "Review the source references."]
    doc = {
        "schema_version": knowledge_docs.KNOWLEDGE_DOC_SCHEMA_VERSION,
        "algorithm_version": knowledge_docs.KNOWLEDGE_DOC_ALGORITHM_VERSION,
        "doc_id": knowledge_docs.doc_id_for(candidate["canonical_key"], candidate["source_fingerprint"]),
        "version": 1,
        "status": "draft",
        "knowledge_type": candidate["knowledge_type"],
        "title": title,
        "summary": compact_text(candidate["summary"]),
        "body_path": "",
        "body_sections": body_sections,
        "canonical_key": candidate["canonical_key"],
        "source_fingerprint": candidate["source_fingerprint"],
        "source_refs": source_refs,
        "project_key": compact_text(candidate.get("project_key")),
        "project_label": compact_text(candidate.get("project_label")),
        "scope": candidate.get("scope") or "project",
        "sensitivity": candidate.get("sensitivity") or "internal",
        "quality_score": candidate["quality_score"],
        "reviewer_state": "needs_review",
        "redaction_status": "publish_safe",
        "model_status": openrelix_model_runner.MODEL_STATUS_NOT_RUN,
        "visibility": knowledge_docs.visibility_policy("draft"),
        "conflict_of_doc_ids": [],
        "created_at": created_at,
        "updated_at": created_at,
    }
    year = str((candidate.get("date") or created_at)[:4])
    slug = knowledge_docs.slug_component(title)
    doc["body_path"] = "knowledge/docs/{}/{}.md".format(year, slug)
    return openrelix_model_runner.sanitize_model_input(doc)


def validate_doc_payload(payload: Mapping[str, Any]) -> list[str]:
    errors = []
    for field in DOC_REQUIRED_FIELDS:
        if field not in payload:
            errors.append("missing required field: {}".format(field))
    extra_fields = sorted(set(payload) - set(DOC_REQUIRED_FIELDS))
    for field in extra_fields:
        errors.append("unexpected field: {}".format(field))
    if errors:
        return errors

    if payload["schema_version"] != knowledge_docs.KNOWLEDGE_DOC_SCHEMA_VERSION:
        errors.append("schema_version must be {}".format(knowledge_docs.KNOWLEDGE_DOC_SCHEMA_VERSION))
    if payload["algorithm_version"] != knowledge_docs.KNOWLEDGE_DOC_ALGORITHM_VERSION:
        errors.append("algorithm_version must be {}".format(knowledge_docs.KNOWLEDGE_DOC_ALGORITHM_VERSION))
    if payload["status"] not in knowledge_docs.DOC_STATUSES:
        errors.append("invalid status: {}".format(payload["status"]))
    if payload["knowledge_type"] not in knowledge_docs.KNOWLEDGE_TYPES:
        errors.append("invalid knowledge_type: {}".format(payload["knowledge_type"]))
    if payload["model_status"] not in openrelix_model_runner.MODEL_STATUSES:
        errors.append("invalid model_status: {}".format(payload["model_status"]))
    if not isinstance(payload["quality_score"], (int, float)) or not 0 <= float(payload["quality_score"]) <= 1:
        errors.append("quality_score must be between 0 and 1")

    body = payload["body_sections"]
    if not isinstance(body, Mapping):
        errors.append("body_sections must be an object")
    else:
        for field in BODY_SECTION_REQUIRED_FIELDS:
            if field not in body:
                errors.append("missing required body_sections field: {}".format(field))
        for field in set(body) - set(BODY_SECTION_REQUIRED_FIELDS):
            errors.append("unexpected body_sections field: {}".format(field))

    source_refs = payload["source_refs"]
    if not isinstance(source_refs, Mapping):
        errors.append("source_refs must be an object")
    else:
        for field in SOURCE_REF_REQUIRED_FIELDS:
            if field not in source_refs:
                errors.append("missing required source_refs field: {}".format(field))
        for field in set(source_refs) - set(SOURCE_REF_REQUIRED_FIELDS):
            errors.append("unexpected source_refs field: {}".format(field))

    visibility = payload["visibility"]
    if not isinstance(visibility, Mapping):
        errors.append("visibility must be an object")
    else:
        for field in VISIBILITY_REQUIRED_FIELDS:
            if field not in visibility:
                errors.append("missing required visibility field: {}".format(field))
        for field in set(visibility) - set(VISIBILITY_REQUIRED_FIELDS):
            errors.append("unexpected visibility field: {}".format(field))
        if visibility.get("host_context") is not False:
            errors.append("visibility.host_context must be false")
    return errors


def build_model_request(paths, candidates: list[dict], summary: Mapping[str, Any]) -> openrelix_model_runner.ModelRunRequest:
    created_at = utc_now()
    draft_docs = [deterministic_doc_from_candidate(candidate, created_at) for candidate in candidates]
    payload = {
        "schema_version": knowledge_docs.KNOWLEDGE_DOC_SCHEMA_VERSION,
        "algorithm_version": knowledge_docs.KNOWLEDGE_DOC_ALGORITHM_VERSION,
        "candidates": [public_candidate(candidate) for candidate in candidates],
        "draft_docs": draft_docs,
        "summary": summary,
    }
    return openrelix_model_runner.ModelRunRequest(
        task_name="knowledge-doc-rewrite",
        schema_path=paths.templates_dir / "knowledge-doc-schema.json",
        payload=openrelix_model_runner.sanitize_model_input(payload),
    )


def normalize_model_docs(payload: Mapping[str, Any]) -> list[dict]:
    if "docs" in payload and isinstance(payload["docs"], list):
        return [dict(item) for item in payload["docs"] if isinstance(item, Mapping)]
    return [dict(payload)]


def run_id_for(target_date: str, summary: Mapping[str, Any]) -> str:
    digest = hashlib.sha1(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:8]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "knowledge-{}-{}-{}".format(target_date, stamp, digest)


def write_run_artifact(paths, run_id: str, payload: Mapping[str, Any], *, dry_run: bool = False) -> Path:
    run_json = paths.state_root / "knowledge" / "runs" / run_id / "run.json"
    if not dry_run:
        atomic_write_json(run_json, openrelix_model_runner.sanitize_model_input(payload))
    return run_json


def build_knowledge_docs(
    paths=None,
    date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    project_key: str = "",
    dry_run: bool = False,
    auto_confirm: bool = False,
    model_runner: Optional[Callable[[openrelix_model_runner.ModelRunRequest], openrelix_model_runner.ModelRunResult]] = None,
) -> dict:
    paths = ensure_state_layout(paths or get_runtime_paths())
    range_from, range_to, target_dates = resolve_date_range(date=date, date_from=date_from, date_to=date_to)
    target_date = range_to
    summaries = load_daily_summaries(paths, target_dates)
    summary = {
        "date": target_date,
        "date_range": {"from": range_from, "to": range_to},
        "summaries": summaries,
        "window_summaries": [
            window
            for daily_summary in summaries
            for window in (daily_summary.get("window_summaries") or [])
            if isinstance(window, Mapping)
        ],
    }
    memory_rows = load_memory_rows(paths)
    candidates = [
        candidate
        for candidate in extract_candidates_for_summaries(
            summaries,
            memory_rows,
            target_dates,
            project_key=project_key,
        )
        if candidate["decision"] == "draft"
    ]
    run_id = run_id_for(target_date, summary)
    created_at = utc_now()
    result = {
        "date": target_date,
        "date_range": {"from": range_from, "to": range_to},
        "run_id": run_id,
        "run_artifact": "",
        "dry_run": bool(dry_run),
        "auto_confirm": bool(auto_confirm),
        "created_candidates": 0,
        "created_docs": 0,
        "failed_runs": 0,
        "candidate_ids": [],
        "doc_ids": [],
        "status": "success",
    }

    if not candidates:
        run_payload = {
            "run_id": run_id,
            "date": target_date,
            "status": "success",
            "model_status": openrelix_model_runner.MODEL_STATUS_NOT_RUN,
            "created_candidates": 0,
            "created_docs": 0,
            "message": "No candidate met the MVP quality threshold.",
        }
        run_artifact = write_run_artifact(paths, run_id, run_payload, dry_run=dry_run)
        result["run_artifact"] = str(run_artifact)
        return result

    docs = []
    model_status = openrelix_model_runner.MODEL_STATUS_NOT_RUN
    if model_runner is None:
        docs = [deterministic_doc_from_candidate(candidate, created_at) for candidate in candidates]
    else:
        request = build_model_request(paths, candidates, summary)
        model_result = model_runner(request)
        model_status = model_result.status
        if model_result.status != openrelix_model_runner.MODEL_STATUS_SUCCESS:
            run_payload = {
                "run_id": run_id,
                "date": target_date,
                "status": "failed",
                "model_status": model_result.status,
                "error_hint": compact_text(model_result.error_hint),
                "created_candidates": 0,
                "created_docs": 0,
            }
            run_artifact = write_run_artifact(paths, run_id, run_payload, dry_run=dry_run)
            result.update(
                {
                    "run_artifact": str(run_artifact),
                    "failed_runs": 1,
                    "status": "failed",
                }
            )
            return result
        docs = normalize_model_docs(openrelix_model_runner.sanitize_model_input(model_result.payload or {}))
        for doc in docs:
            doc["model_status"] = model_status

    validation_errors = []
    for doc in docs:
        validation_errors.extend(validate_doc_payload(doc))
    if validation_errors:
        run_payload = {
            "run_id": run_id,
            "date": target_date,
            "status": "failed",
            "model_status": openrelix_model_runner.MODEL_STATUS_POISONED,
            "error_hint": "; ".join(validation_errors[:5]),
            "created_candidates": 0,
            "created_docs": 0,
        }
        run_artifact = write_run_artifact(paths, run_id, run_payload, dry_run=dry_run)
        result.update(
            {
                "run_artifact": str(run_artifact),
                "failed_runs": 1,
                "status": "failed",
            }
        )
        return result

    public_candidates = [public_candidate(candidate) for candidate in candidates]
    if not dry_run:
        for doc in docs:
            body_path = paths.state_root / doc["body_path"]
            atomic_write_text(body_path, render_markdown(doc))
        with registry_lock(paths):
            upsert_jsonl(
                paths.registry_dir / "knowledge_candidates.jsonl",
                public_candidates,
                ("candidate_id",),
            )
            upsert_jsonl(
                paths.registry_dir / "knowledge_docs.jsonl",
                docs,
                ("doc_id", "version"),
            )

    result.update(
        {
            "created_candidates": len(public_candidates),
            "created_docs": len(docs),
            "candidate_ids": [candidate["candidate_id"] for candidate in public_candidates],
            "doc_ids": [doc["doc_id"] for doc in docs],
        }
    )
    run_payload = {
        "run_id": run_id,
        "date": target_date,
        "status": "success",
        "model_status": model_status,
        "created_candidates": len(public_candidates),
        "created_docs": len(docs),
        "candidate_ids": result["candidate_ids"],
        "doc_ids": result["doc_ids"],
    }
    run_artifact = write_run_artifact(paths, run_id, run_payload, dry_run=dry_run)
    result["run_artifact"] = str(run_artifact)
    return result


def list_knowledge_docs(paths=None, limit: int = 20) -> list[dict]:
    paths = ensure_state_layout(paths or get_runtime_paths())
    rows = read_jsonl(paths.registry_dir / "knowledge_docs.jsonl")
    rows.sort(key=lambda row: (str(row.get("updated_at") or ""), str(row.get("doc_id") or "")), reverse=True)
    return rows[: max(0, limit)]


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    rendered = [_json_dumps(row) for row in rows]
    atomic_write_text(path, "\n".join(rendered) + ("\n" if rendered else ""))


def reviewer_state_for_status(status: str) -> str:
    if status == "rejected":
        return "rejected"
    if status in {"reviewed", "published"}:
        return "reviewed"
    return "needs_review"


def update_knowledge_doc_status(paths=None, doc_id: str = "", status: str = "", version: Optional[int] = None) -> dict:
    paths = ensure_state_layout(paths or get_runtime_paths())
    target_doc_id = compact_text(doc_id)
    target_status = compact_text(status)
    if not target_doc_id:
        raise ValueError("doc_id is required")
    if target_status not in knowledge_docs.DOC_STATUSES:
        raise ValueError("unsupported knowledge doc status: {}".format(target_status))

    registry_path = paths.registry_dir / "knowledge_docs.jsonl"
    with registry_lock(paths):
        rows = read_jsonl(registry_path)
        matches = [
            (index, row)
            for index, row in enumerate(rows)
            if row.get("doc_id") == target_doc_id and (version is None or safe_int(row.get("version")) == version)
        ]
        if not matches:
            raise ValueError("knowledge doc not found: {}".format(target_doc_id))
        index, doc = sorted(matches, key=lambda item: safe_int(item[1].get("version")), reverse=True)[0]
        current_status = compact_text(doc.get("status") or "draft")
        if current_status == target_status:
            updated = dict(doc)
        else:
            if not knowledge_docs.can_transition_doc(current_status, target_status):
                raise ValueError("cannot transition knowledge doc from {} to {}".format(current_status, target_status))
            updated = dict(doc)
            updated["status"] = target_status
            updated["reviewer_state"] = reviewer_state_for_status(target_status)
            updated["visibility"] = knowledge_docs.visibility_policy(target_status)
            updated["updated_at"] = utc_now()
            rows[index] = updated
            write_jsonl(registry_path, rows)
        if updated.get("body_path"):
            atomic_write_text(paths.state_root / updated["body_path"], render_markdown(updated))
    return updated


def knowledge_status(paths=None) -> dict:
    paths = ensure_state_layout(paths or get_runtime_paths())
    candidates = read_jsonl(paths.registry_dir / "knowledge_candidates.jsonl")
    docs = read_jsonl(paths.registry_dir / "knowledge_docs.jsonl")
    runs_dir = paths.state_root / "knowledge" / "runs"
    status_counts = {}
    for row in docs:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "state_root": str(paths.state_root),
        "candidate_rows": len(candidates),
        "doc_rows": len(docs),
        "doc_status_counts": status_counts,
        "run_count": len(list(runs_dir.glob("*/run.json"))) if runs_dir.exists() else 0,
        "host_context_enabled": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build local OpenRelix knowledge document drafts.")
    parser.add_argument("--date", default=today_str(), help="Target date in YYYY-MM-DD.")
    parser.add_argument("--from", dest="date_from", help="Start date in YYYY-MM-DD for project/range builds.")
    parser.add_argument("--to", dest="date_to", help="End date in YYYY-MM-DD for project/range builds.")
    parser.add_argument("--project", dest="project_key", default="", help="Optional project_key filter.")
    parser.add_argument("--dry-run", action="store_true", help="Compute without writing registries or docs.")
    parser.add_argument("--auto-confirm", action="store_true", help="Reserved for future review promotion.")
    parser.add_argument("--deterministic", action="store_true", help="Use deterministic local draft rendering without LLM.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    model_runner = None if args.deterministic else openrelix_model_runner.run_model_request
    result = build_knowledge_docs(
        date=args.date,
        date_from=args.date_from,
        date_to=args.date_to,
        project_key=args.project_key,
        dry_run=args.dry_run,
        auto_confirm=args.auto_confirm,
        model_runner=model_runner,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("knowledge docs: {} created, {} failed".format(result["created_docs"], result["failed_runs"]))
        print(result["run_artifact"])
    return 0 if result["failed_runs"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
