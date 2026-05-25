#!/usr/bin/env python3
"""Shared model-runner contracts for OpenRelix model-backed jobs.

This module deliberately starts as an interface and safety boundary.  Feature
builders can depend on the redaction and failure classification rules without
copying the larger Codex/Claude execution code from existing nightly jobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
from typing import Any, Mapping, Optional

from openrelix_overview.curated_memory import redact_text


MODEL_STATUS_SUCCESS = "success"
MODEL_STATUS_RETRYABLE = "retryable"
MODEL_STATUS_POISONED = "poisoned"
MODEL_STATUS_NOT_RUN = "not_run"
MODEL_STATUSES = (
    MODEL_STATUS_SUCCESS,
    MODEL_STATUS_RETRYABLE,
    MODEL_STATUS_POISONED,
    MODEL_STATUS_NOT_RUN,
)

DEFAULT_MODEL_TIMEOUT_SECONDS = 30 * 60

BEARER_TOKEN_PATTERN = re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]{12,}")
HOME_PATH_PATTERN = re.compile(r"(?:/Users|/home)/[^/\s<>\"']+")
PRIVATE_URL_PATTERN = re.compile(r"https?://[^\\\s<>\"']+")


@dataclass(frozen=True)
class ModelRunRequest:
    """Portable request shape for schema-constrained model jobs."""

    task_name: str
    schema_path: Path
    payload: Mapping[str, Any]
    language: str = "zh"
    timeout_seconds: int = DEFAULT_MODEL_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ModelRunResult:
    """Portable model result shape for callers that need retry semantics."""

    status: str
    payload: Optional[Mapping[str, Any]] = None
    error_hint: str = ""


def sanitize_model_input(value: Any) -> Any:
    """Return a prompt-safe copy with obvious private details removed."""

    if isinstance(value, dict):
        return {str(key): sanitize_model_input(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_model_input(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_model_input(item) for item in value]
    if not isinstance(value, str):
        return value

    text = redact_text(value)
    text = BEARER_TOKEN_PATTERN.sub(r"\1 ***", text)
    text = HOME_PATH_PATTERN.sub("~", text)

    def redact_url(match):
        url = match.group(0)
        lowered = url.lower()
        if (
            lowered.startswith("https://openrelix.org")
            or lowered.startswith("https://github.com/openrelix/")
            or lowered.startswith("http://localhost")
            or lowered.startswith("http://127.")
        ):
            return url
        return "<link>"

    return PRIVATE_URL_PATTERN.sub(redact_url, text)


def sanitized_json(value: Any) -> str:
    """Serialize sanitized model input deterministically for prompts or logs."""

    return json.dumps(
        sanitize_model_input(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def classify_model_failure(returncode: Optional[int] = None, stdout: str = "", stderr: str = "") -> str:
    """Classify model execution failures into MVP status buckets."""

    text = "\n".join(part for part in (stdout or "", stderr or "") if part).lower()
    if "invalid json" in text or "json parse" in text or "schema" in text or "validation" in text:
        return MODEL_STATUS_POISONED
    if returncode == 0:
        return MODEL_STATUS_SUCCESS
    if returncode == 124 or "timed out" in text or "timeout" in text:
        return MODEL_STATUS_RETRYABLE
    if "rate limit" in text or "too many requests" in text or "temporarily unavailable" in text:
        return MODEL_STATUS_RETRYABLE
    return MODEL_STATUS_RETRYABLE


def build_safe_payload_section(payload: Mapping[str, Any]) -> str:
    """Render the only payload block a model prompt should reference."""

    return sanitized_json(payload)
