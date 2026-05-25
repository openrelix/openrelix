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
import subprocess
from typing import Any, Mapping, Optional

from asset_runtime import (
    build_claude_cli_env,
    ensure_state_layout,
    get_claude_env_file,
    get_claude_model,
    get_claude_settings,
    get_codex_model,
    get_model_cli,
    get_runtime_paths,
    sync_codex_exec_home,
)
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


def _process_output_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _extract_json_from_text(text: str) -> Mapping[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty model output")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
        if not match:
            match = re.search(r"(\{.*\})", raw, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(1))
    if isinstance(parsed, dict) and isinstance(parsed.get("result"), str):
        try:
            return _extract_json_from_text(parsed["result"])
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    if not isinstance(parsed, Mapping):
        raise ValueError("model output is not a JSON object")
    return dict(parsed)


def _load_env_file(path_text: str) -> dict:
    env = {}
    path_text = str(path_text or "").strip()
    if not path_text:
        return env
    path = Path(path_text).expanduser()
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        env[key] = value.strip().strip("\"'")
    return env


def build_model_prompt(request: ModelRunRequest) -> str:
    template_path = request.schema_path.parent / "knowledge-doc-rewrite-prompt.md"
    template = template_path.read_text(encoding="utf-8") if template_path.exists() else ""
    return "\n\n".join(
        part
        for part in (
            "Task: {}".format(request.task_name),
            template,
            (
                "Safety: use only the sanitized JSON payload below. Do not call tools, "
                "read files, use network, or infer facts that are not supported by source_refs."
            ),
            "Sanitized input JSON:\n{}".format(build_safe_payload_section(request.payload)),
        )
        if part
    )


def _run_codex_request(request: ModelRunRequest, paths) -> ModelRunResult:
    paths = ensure_state_layout(paths)
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    paths.nightly_codex_home.mkdir(parents=True, exist_ok=True)
    sync_codex_exec_home(paths.codex_home, paths.nightly_codex_home)
    work_dir = paths.runtime_dir / "knowledge-model-runner"
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path = work_dir / "last-message.json"
    if output_path.exists():
        output_path.unlink()
    cmd = [
        paths.codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--cd",
        str(work_dir),
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--disable",
        "memories",
        "--disable",
        "codex_hooks",
        "--model",
        get_codex_model(paths),
        "-c",
        'approval_policy="never"',
        "-c",
        'history.persistence="none"',
        "-c",
        "history.max_bytes=1048576",
        "--output-schema",
        str(request.schema_path),
        "--output-last-message",
        str(output_path),
        "-",
    ]
    env = dict(__import__("os").environ)
    env["CODEX_HOME"] = str(paths.nightly_codex_home)
    try:
        result = subprocess.run(
            cmd,
            input=build_model_prompt(request),
            text=True,
            capture_output=True,
            env=env,
            timeout=request.timeout_seconds if request.timeout_seconds and request.timeout_seconds > 0 else None,
        )
    except subprocess.TimeoutExpired as exc:
        return ModelRunResult(
            status=MODEL_STATUS_RETRYABLE,
            error_hint="codex exec timed out: {}".format(_process_output_to_text(exc.stderr)),
        )
    if result.returncode != 0:
        return ModelRunResult(
            status=classify_model_failure(result.returncode, result.stdout, result.stderr),
            error_hint=sanitize_model_input(result.stderr or result.stdout or "codex exec failed"),
        )
    try:
        output_text = output_path.read_text(encoding="utf-8") if output_path.exists() else result.stdout
        return ModelRunResult(status=MODEL_STATUS_SUCCESS, payload=_extract_json_from_text(output_text))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return ModelRunResult(status=MODEL_STATUS_POISONED, error_hint=str(exc))


def _run_claude_request(request: ModelRunRequest, paths) -> ModelRunResult:
    paths = ensure_state_layout(paths)
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    paths.claude_home.mkdir(parents=True, exist_ok=True)
    cmd = [
        paths.claude_bin,
        "-p",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--tools=",
        "--json-schema",
        request.schema_path.read_text(encoding="utf-8"),
    ]
    claude_model = get_claude_model(paths)
    if claude_model and claude_model != "auto":
        cmd.extend(["--model", claude_model])
    claude_settings = get_claude_settings(paths)
    if claude_settings:
        cmd.extend(["--settings", claude_settings])
    env = build_claude_cli_env(
        claude_home=paths.claude_home,
        env_file_values=_load_env_file(get_claude_env_file(paths)),
    )
    try:
        result = subprocess.run(
            cmd,
            input=build_model_prompt(request),
            text=True,
            capture_output=True,
            env=env,
            cwd=str(paths.runtime_dir),
            timeout=request.timeout_seconds if request.timeout_seconds and request.timeout_seconds > 0 else None,
        )
    except subprocess.TimeoutExpired as exc:
        return ModelRunResult(
            status=MODEL_STATUS_RETRYABLE,
            error_hint="claude -p timed out: {}".format(_process_output_to_text(exc.stderr)),
        )
    if result.returncode != 0:
        return ModelRunResult(
            status=classify_model_failure(result.returncode, result.stdout, result.stderr),
            error_hint=sanitize_model_input(result.stderr or result.stdout or "claude -p failed"),
        )
    try:
        return ModelRunResult(status=MODEL_STATUS_SUCCESS, payload=_extract_json_from_text(result.stdout))
    except (ValueError, json.JSONDecodeError) as exc:
        return ModelRunResult(status=MODEL_STATUS_POISONED, error_hint=str(exc))


def run_model_request(
    request: ModelRunRequest,
    *,
    paths=None,
    model_cli: Optional[str] = None,
) -> ModelRunResult:
    """Run a schema-constrained model request through the configured model CLI."""

    paths = ensure_state_layout(paths or get_runtime_paths())
    selected = model_cli or get_model_cli(paths)
    if selected in {"claude", "cc"}:
        return _run_claude_request(request, paths)
    return _run_codex_request(request, paths)
