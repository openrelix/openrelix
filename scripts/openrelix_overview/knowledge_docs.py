"""Contracts and deterministic helpers for knowledge document assets."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping


KNOWLEDGE_DOC_SCHEMA_VERSION = 1
KNOWLEDGE_DOC_ALGORITHM_VERSION = 1

KNOWLEDGE_TYPES = (
    "troubleshooting",
    "decision",
    "procedure",
    "project_context",
)

CANDIDATE_STATUSES = ("candidate", "deferred", "rejected", "draft")
DOC_STATUSES = ("draft", "reviewed", "published", "superseded", "rejected")

CANDIDATE_TRANSITIONS = {
    "candidate": {"deferred", "rejected", "draft"},
    "deferred": {"candidate", "rejected", "draft"},
    "draft": set(),
    "rejected": set(),
}

DOC_TRANSITIONS = {
    "draft": {"reviewed", "rejected"},
    "reviewed": {"published", "rejected", "draft"},
    "published": {"superseded"},
    "superseded": set(),
    "rejected": set(),
}

VISIBILITY_BY_STATUS = {
    "draft": {
        "panel": True,
        "default_search": False,
        "host_context": False,
        "trust_level": "draft",
    },
    "reviewed": {
        "panel": True,
        "default_search": False,
        "host_context": False,
        "trust_level": "reviewed",
    },
    "published": {
        "panel": True,
        "default_search": True,
        "host_context": False,
        "trust_level": "reviewed",
    },
    "superseded": {
        "panel": False,
        "default_search": False,
        "host_context": False,
        "trust_level": "archived",
    },
    "rejected": {
        "panel": False,
        "default_search": False,
        "host_context": False,
        "trust_level": "rejected",
    },
}

SLUG_FALLBACK = "untitled"


def compact_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def slug_component(value, fallback=SLUG_FALLBACK) -> str:
    text = compact_text(value).lower()
    text = re.sub(r"[`\"'“”‘’]+", "", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text).strip("-")
    return text or fallback


def normalize_knowledge_type(value) -> str:
    text = slug_component(value, fallback="project_context").replace("-", "_")
    if text in {"project-context", "project"}:
        text = "project_context"
    return text if text in KNOWLEDGE_TYPES else "project_context"


def canonical_key(row: Mapping[str, object]) -> str:
    """Return the stable phase-one canonical key.

    Formula: ``slug(project_key or scope/global):knowledge_type:slug(title)``.
    This keeps MVP dedupe project-scoped and avoids cross-project merging.
    """

    scope = row.get("project_key") or row.get("project_label") or row.get("scope") or "global"
    project = slug_component(scope, fallback="global")
    knowledge_type = normalize_knowledge_type(row.get("knowledge_type"))
    title = slug_component(row.get("title"), fallback="untitled")
    return "{}:{}:{}".format(project, knowledge_type, title)


def source_fingerprint(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:{}".format(hashlib.sha256(canonical.encode("utf-8")).hexdigest())


def doc_id_for(canonical_key_value: str, source_fingerprint_value: str) -> str:
    digest = hashlib.sha1(
        "{}|{}".format(canonical_key_value, source_fingerprint_value).encode("utf-8")
    ).hexdigest()[:12]
    return "kdoc-{}".format(digest)


def can_transition_candidate(current: str, target: str) -> bool:
    return str(target) in CANDIDATE_TRANSITIONS.get(str(current), set())


def can_transition_doc(current: str, target: str) -> bool:
    return str(target) in DOC_TRANSITIONS.get(str(current), set())


def visibility_policy(status: str) -> dict:
    policy = VISIBILITY_BY_STATUS.get(str(status), VISIBILITY_BY_STATUS["draft"])
    return dict(policy)


def knowledge_registry_paths(paths) -> dict:
    return {
        "knowledge_dir": paths.state_root / "knowledge",
        "docs_dir": paths.state_root / "knowledge" / "docs",
        "runs_dir": paths.state_root / "knowledge" / "runs",
        "candidate_registry": paths.registry_dir / "knowledge_candidates.jsonl",
        "doc_registry": paths.registry_dir / "knowledge_docs.jsonl",
    }
