#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from asset_runtime import (
    atomic_write_json,
    build_claude_cli_env,
    get_claude_env_file,
    get_claude_model,
    get_claude_settings,
    get_codex_model,
    get_model_cli,
    get_runtime_language,
    get_runtime_paths,
    load_runtime_config,
    personal_memory_enabled,
    runtime_config_path,
    sync_codex_exec_home,
)


TASK_CLUSTER_ALGORITHM_VERSION = 1
TASK_SUMMARY_SCHEMA_VERSION = 1
TASK_SUMMARY_MIGRATION_STATE_VERSION = 1
TASK_SUMMARY_WINDOW_DAYS = 7
TASK_SUMMARY_CONFIDENCE_FOR_GROUPING = {"high", "medium"}
DEFAULT_MODEL_TIMEOUT_SECONDS = 30 * 60
CODEX_EXEC_TIMEOUT_RETURN_CODE = 124

PATHS = get_runtime_paths()
LANGUAGE = get_runtime_language(PATHS)
MODEL_CLI = get_model_cli(PATHS)
CODEX_MODEL = get_codex_model(PATHS)
CLAUDE_MODEL = get_claude_model(PATHS)
CLAUDE_SETTINGS = get_claude_settings(PATHS)
CLAUDE_ENV_FILE = get_claude_env_file(PATHS)
TASK_SUMMARY_SCHEMA_PATH = PATHS.repo_root / "templates" / "window-task-summary-schema.json"


class TaskSummaryModelError(RuntimeError):
    def __init__(self, returncode, stdout="", stderr=""):
        super().__init__(describe_model_failure(stdout, stderr, returncode))
        self.returncode = returncode
        self.stdout = stdout or ""
        self.stderr = stderr or ""


def current_language(language=None):
    return language or LANGUAGE or "zh"


def localized(zh_text, en_text, language=None):
    return en_text if current_language(language) == "en" else zh_text


def current_timestamp(now=None):
    return (now or datetime.now().astimezone()).isoformat()


def task_summary_dir(paths=None):
    paths = paths or PATHS
    return paths.consolidated_dir / "task_summaries"


def task_summary_migration_state_path(paths=None):
    paths = paths or PATHS
    return paths.runtime_dir / "task-summary-migration.json"


def task_summary_artifact_path(date_from, date_to, paths=None):
    return task_summary_dir(paths) / "{}_{}.json".format(date_from, date_to)


def clip_text(value, limit=240):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def parse_date(value):
    return date.fromisoformat(str(value))


def date_strings_ending_at(end_date, days):
    end = parse_date(end_date)
    return [
        (end - timedelta(days=offset)).isoformat()
        for offset in range(max(int(days or 0), 1) - 1, -1, -1)
    ]


def project_label_from_cwd(cwd):
    text = str(cwd or "").strip()
    if not text:
        return ""
    name = Path(text).name or text.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"[_-]+", " ", name).strip() or name


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, payload):
    atomic_write_json(Path(path), payload)


def load_daily_summary(date_str, paths=None):
    paths = paths or PATHS
    path = paths.consolidated_daily_dir / str(date_str) / "summary.json"
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def normalized_summary_pairs(raw_pairs, limit=4):
    pairs = []
    if not isinstance(raw_pairs, list):
        return pairs
    for raw_pair in raw_pairs[:limit]:
        if not isinstance(raw_pair, dict):
            continue
        question = clip_text(raw_pair.get("question") or raw_pair.get("problem") or "", 180)
        conclusion = clip_text(raw_pair.get("conclusion") or raw_pair.get("takeaway") or "", 220)
        if question or conclusion:
            pairs.append({"question": question, "conclusion": conclusion})
    return pairs


def compact_window_summary(item, date_str):
    window_id = str(item.get("window_id") or "").strip()
    if not window_id:
        return None
    cwd = str(item.get("cwd") or "").strip()
    question_summary = clip_text(item.get("question_summary", ""), 240)
    main_takeaway = clip_text(item.get("main_takeaway", ""), 260)
    return {
        "date": str(date_str),
        "window_id": window_id,
        "cwd": cwd,
        "project_label": project_label_from_cwd(cwd),
        "window_title": clip_text(
            item.get("window_title")
            or item.get("window_summary")
            or item.get("title")
            or question_summary,
            120,
        ),
        "question_summary": question_summary,
        "main_takeaway": main_takeaway,
        "question_count": int(item.get("question_count") or 0),
        "conclusion_count": int(item.get("conclusion_count") or 0),
        "keywords": [clip_text(keyword, 40) for keyword in (item.get("keywords") or [])[:8] if str(keyword or "").strip()],
        "summary_pairs": normalized_summary_pairs(item.get("summary_pairs") or []),
    }


def build_task_summary_input(dates, paths=None):
    paths = paths or PATHS
    windows = []
    source_summary_dates = []
    for date_str in dates:
        summary = load_daily_summary(date_str, paths=paths)
        if not summary:
            continue
        date_value = str(summary.get("date") or date_str)
        date_windows = []
        for item in summary.get("window_summaries") or []:
            if not isinstance(item, dict):
                continue
            compact = compact_window_summary(item, date_value)
            if compact:
                date_windows.append(compact)
        if date_windows:
            source_summary_dates.append(date_value)
            windows.extend(date_windows)
    date_values = list(dates)
    date_from = date_values[0] if date_values else ""
    date_to = date_values[-1] if date_values else ""
    return {
        "schema_version": TASK_SUMMARY_SCHEMA_VERSION,
        "task_cluster_algorithm_version": TASK_CLUSTER_ALGORITHM_VERSION,
        "date_range": {"from": date_from, "to": date_to},
        "source_summary_dates": source_summary_dates,
        "windows": windows,
    }


def input_fingerprint(payload):
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_cluster_id(value, title="", window_ids=None):
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-._")
    if text:
        return text[:80]
    basis = "|".join([str(title or "")] + list(window_ids or []))
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
    return "task-{}".format(digest)


def normalize_confidence(value):
    text = str(value or "").strip().lower()
    return text if text in {"high", "medium", "low"} else "medium"


def normalize_status_tags(value):
    tags = []
    raw_tags = value if isinstance(value, list) else []
    for tag in raw_tags:
        text = clip_text(tag, 16).strip(" ，,。:：/-")
        if text and text not in tags:
            tags.append(text)
    return tags[:8]


def common_value_for_windows(windows_by_id, window_ids, key):
    values = []
    for window_id in window_ids:
        value = str((windows_by_id.get(window_id) or {}).get(key) or "").strip()
        if value and value not in values:
            values.append(value)
    return values[0] if len(values) == 1 else (values[0] if values else "")


def window_scope_key(window):
    return (
        str((window or {}).get("cwd") or "").strip(),
        str((window or {}).get("project_label") or "").strip(),
    )


def filter_window_ids_to_single_scope(windows_by_id, window_ids):
    buckets = []
    positions_by_scope = {}
    for window_id in window_ids:
        scope = window_scope_key(windows_by_id.get(window_id) or {})
        bucket_index = positions_by_scope.get(scope)
        if bucket_index is None:
            positions_by_scope[scope] = len(buckets)
            buckets.append({"scope": scope, "window_ids": []})
        buckets[positions_by_scope[scope]]["window_ids"].append(window_id)
    if not buckets:
        return []
    buckets.sort(key=lambda item: len(item["window_ids"]), reverse=True)
    return list(buckets[0]["window_ids"])


def normalize_task_summary_payload(raw_payload, task_input, model_cli="", language=None):
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    valid_window_ids = {window["window_id"] for window in task_input.get("windows", [])}
    windows_by_id = {window["window_id"]: window for window in task_input.get("windows", [])}
    assigned_window_ids = set()
    clusters = []
    raw_clusters = [cluster for cluster in payload.get("project_task_clusters") or [] if isinstance(cluster, dict)]
    raw_clusters.sort(
        key=lambda cluster: (
            {"high": 3, "medium": 2, "low": 1}.get(normalize_confidence(cluster.get("confidence")), 2),
            len(cluster.get("source_window_ids") or []),
        ),
        reverse=True,
    )
    for raw_cluster in raw_clusters:
        source_window_ids = []
        for raw_window_id in raw_cluster.get("source_window_ids") or []:
            window_id = str(raw_window_id or "").strip()
            if window_id in valid_window_ids and window_id not in source_window_ids and window_id not in assigned_window_ids:
                source_window_ids.append(window_id)
        source_window_ids = filter_window_ids_to_single_scope(windows_by_id, source_window_ids)
        if not source_window_ids:
            continue
        title = clip_text(raw_cluster.get("task_title") or raw_cluster.get("label") or "", 48).strip()
        if not title:
            title = localized("未命名并行任务", "Untitled parallel task", language)
        confidence = normalize_confidence(raw_cluster.get("confidence"))
        cluster_id = normalize_cluster_id(raw_cluster.get("cluster_id"), title=title, window_ids=source_window_ids)
        project_label = clip_text(common_value_for_windows(windows_by_id, source_window_ids, "project_label") or raw_cluster.get("project_label"), 80)
        cwd = clip_text(common_value_for_windows(windows_by_id, source_window_ids, "cwd") or raw_cluster.get("cwd"), 240)
        task_summary = clip_text(raw_cluster.get("task_summary") or "", 220)
        clusters.append(
            {
                "cluster_id": cluster_id,
                "project_label": project_label,
                "cwd": cwd,
                "task_title": title,
                "task_summary": task_summary,
                "source_window_ids": source_window_ids,
                "status_tags": normalize_status_tags(raw_cluster.get("status_tags")),
                "confidence": confidence,
            }
        )
        assigned_window_ids.update(source_window_ids)
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    clusters.sort(
        key=lambda item: (
            len(item["source_window_ids"]),
            confidence_rank.get(item["confidence"], 0),
            item["task_title"],
        ),
        reverse=True,
    )
    return {
        "schema_version": TASK_SUMMARY_SCHEMA_VERSION,
        "task_cluster_algorithm_version": TASK_CLUSTER_ALGORITHM_VERSION,
        "date_range": task_input.get("date_range") or {"from": "", "to": ""},
        "source_summary_dates": task_input.get("source_summary_dates") or [],
        "source_window_ids": task_input_window_ids(task_input),
        "project_task_clusters": clusters,
        "source_fingerprint": input_fingerprint(task_input),
        "generated_at": current_timestamp(),
        "model_status": "completed",
        "model_cli": model_cli or MODEL_CLI,
    }


def task_input_window_ids(task_input):
    return [
        str(window.get("window_id") or "").strip()
        for window in task_input.get("windows", [])
        if str(window.get("window_id") or "").strip()
    ]


def failed_task_summary_payload(task_input, error=None, model_cli="", language=None):
    return {
        "schema_version": TASK_SUMMARY_SCHEMA_VERSION,
        "task_cluster_algorithm_version": TASK_CLUSTER_ALGORITHM_VERSION,
        "date_range": task_input.get("date_range") or {"from": "", "to": ""},
        "source_summary_dates": task_input.get("source_summary_dates") or [],
        "source_window_ids": task_input_window_ids(task_input),
        "project_task_clusters": [],
        "source_fingerprint": input_fingerprint(task_input),
        "generated_at": current_timestamp(),
        "model_status": "failed",
        "model_cli": model_cli or MODEL_CLI,
        "error": safe_task_summary_error_message(error, language=language),
    }


def build_task_summary_prompt(task_input, language=None):
    if current_language(language) == "en":
        return """You are OpenRelix's parallel-task aggregation agent.

Group already summarized AI-coding windows into project-level business task clusters. The input is not raw chat; it is a compact set of model-written window summaries that have already been generated by OpenRelix.

Rules:
1. Group only windows from the same project/cwd when they clearly serve the same user-facing feature, business goal, bug, workflow, or deliverable.
2. Do not group by process-only words such as compile, commit, install, continue, pull main, test, local cleanup, or "where were we". Put those in status_tags only when useful.
3. task_title must be a concise business/feature title. Prefer concrete nouns from the product feature over generic action verbs.
4. If uncertain, keep windows separate or use low confidence. Avoid over-merging.
5. source_window_ids must only use window_id values from the input.
6. Every window may belong to at most one cluster. Omit noise windows that do not form a meaningful task.
7. Output JSON only and satisfy the provided schema.

<window_summary_json>
{payload}
</window_summary_json>
""".format(payload=json.dumps(task_input, ensure_ascii=False, indent=2))

    return """你是 OpenRelix 的并行任务聚合代理。

你的任务是把“已经由大模型整理过的窗口摘要”进一步聚合成项目内的业务任务簇。输入不是原始聊天记录，而是 OpenRelix 已经生成好的 window_summaries 压缩视图。

规则：
1. 只在同一个项目 / cwd 内聚合；只有当多个窗口明显服务于同一个面向用户的功能、业务目标、缺陷、工作流或交付物时才合并。
2. 不要按过程词聚合，例如：编译、提交、安装、继续任务、拉 main、测试、本地清理、刚才聊到哪儿了。这些只能作为 status_tags。
3. task_title 必须是简洁的功能/业务标题，优先使用产品功能名、对象名和问题域，不要使用泛泛动作词。
4. 不确定就拆开，或标为 low confidence；宁可少聚合，不要乱合并。
5. source_window_ids 只能引用输入中出现的 window_id。
6. 每个窗口最多进入一个 cluster；无法形成有意义任务的噪声窗口可以省略。
7. 只输出符合 schema 的 JSON。

<window_summary_json>
{payload}
</window_summary_json>
""".format(payload=json.dumps(task_input, ensure_ascii=False, indent=2))


def safe_task_summary_prompt(prompt, language=None):
    return localized(
        (
            "这是一个纯整理任务，不是软件工程任务。禁止调用 shell、web、MCP、apply_patch 或读取任何额外文件。"
            "不要探索环境；唯一合法输入就是下方 window_summary_json。直接输出符合 schema 的 JSON。\n\n"
        ),
        (
            "This is an organization-only task, not a software engineering task. Do not call shell, web, MCP, "
            "apply_patch, or read any extra files. Do not explore the environment; the only valid input is "
            "window_summary_json below. Output only JSON that satisfies the schema.\n\n"
        ),
        language,
    ) + prompt


def process_output_to_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def safe_task_summary_error_message(value=None, returncode=None, language=None):
    text = process_output_to_text(value).lower()
    hints = []
    categorized = False
    if returncode:
        hints.append("exit code {}".format(returncode))
    if "timed out" in text or "timeout" in text:
        hints.append(localized("模型调用超时", "model command timed out", language))
        categorized = True
    elif "invalid_issuer" in text or "401" in text or "unauthorized" in text:
        hints.append(localized("模型认证失败", "model authentication failed", language))
        categorized = True
    elif "rate limit" in text or "429" in text:
        hints.append(localized("模型限流", "model rate limited", language))
        categorized = True
    elif "not found" in text or "no such file" in text:
        hints.append(localized("模型命令不可用", "model command unavailable", language))
        categorized = True
    if not categorized:
        hints.append(localized("模型生成失败，已隐藏原始输出", "model generation failed; raw output suppressed", language))
    return "; ".join(hints)


def describe_model_failure(stdout, stderr, returncode):
    text = "\n".join(part for part in (process_output_to_text(stderr), process_output_to_text(stdout)) if part)
    return safe_task_summary_error_message(text, returncode=returncode)


def iter_json_values_from_text(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return
    try:
        yield json.loads(raw_text)
    except json.JSONDecodeError:
        pass
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", raw_text, flags=re.IGNORECASE | re.DOTALL):
        try:
            yield json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw_text):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(raw_text[index:])
        except json.JSONDecodeError:
            continue
        yield value


def extract_task_summary_payload(value):
    if isinstance(value, dict) and "project_task_clusters" in value:
        return value
    if isinstance(value, dict) and isinstance(value.get("result"), str):
        for nested in iter_json_values_from_text(value.get("result")):
            found = extract_task_summary_payload(nested)
            if found is not None:
                return found
    if isinstance(value, str):
        for nested in iter_json_values_from_text(value):
            found = extract_task_summary_payload(nested)
            if found is not None:
                return found
    return None


def parse_env_file_value(raw_value):
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path):
    env = {}
    if not path:
        return env
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return env
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            env[key] = parse_env_file_value(value)
    return env


def run_codex_task_summary(prompt, output_path, paths=None, language=None, timeout_seconds=None):
    paths = paths or PATHS
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    sync_codex_exec_home(paths.codex_home, paths.nightly_codex_home)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(paths.nightly_codex_home)
    timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
    cmd = [
        paths.codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--cd",
        str(paths.runtime_dir),
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--disable",
        "memories",
        "--disable",
        "codex_hooks",
        "--model",
        CODEX_MODEL,
        "-c",
        'approval_policy="never"',
        "-c",
        'history.persistence="none"',
        "-c",
        "history.max_bytes=1048576",
        "--output-schema",
        str(TASK_SUMMARY_SCHEMA_PATH),
        "--output-last-message",
        str(output_path),
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            input=safe_task_summary_prompt(prompt, language=language),
            text=True,
            capture_output=True,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = "\n".join(
            part
            for part in (
                process_output_to_text(exc.stderr),
                "codex exec timed out after {} seconds".format(timeout),
            )
            if part
        )
        raise TaskSummaryModelError(CODEX_EXEC_TIMEOUT_RETURN_CODE, process_output_to_text(exc.stdout), stderr) from exc
    if result.returncode != 0:
        raise TaskSummaryModelError(result.returncode, result.stdout, result.stderr)
    try:
        return load_json(output_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskSummaryModelError(1, result.stdout, "codex exec did not write valid task summary JSON") from exc


def run_claude_task_summary(prompt, output_path, paths=None, language=None, timeout_seconds=None):
    paths = paths or PATHS
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    paths.claude_home.mkdir(parents=True, exist_ok=True)
    timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
    env = build_claude_cli_env(
        claude_home=paths.claude_home,
        env_file_values=load_env_file(CLAUDE_ENV_FILE),
    )
    cmd = [
        paths.claude_bin,
        "-p",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--tools=",
    ]
    if CLAUDE_MODEL and CLAUDE_MODEL != "auto":
        cmd.extend(["--model", CLAUDE_MODEL])
    if CLAUDE_SETTINGS:
        cmd.extend(["--settings", CLAUDE_SETTINGS])
    cmd.extend(["--json-schema", TASK_SUMMARY_SCHEMA_PATH.read_text(encoding="utf-8")])
    try:
        result = subprocess.run(
            cmd,
            input=safe_task_summary_prompt(prompt, language=language),
            text=True,
            capture_output=True,
            env=env,
            timeout=timeout,
            cwd=str(paths.runtime_dir),
        )
    except subprocess.TimeoutExpired as exc:
        stderr = "\n".join(
            part
            for part in (
                process_output_to_text(exc.stderr),
                "claude -p timed out after {} seconds".format(timeout),
            )
            if part
        )
        raise TaskSummaryModelError(CODEX_EXEC_TIMEOUT_RETURN_CODE, process_output_to_text(exc.stdout), stderr) from exc
    if result.returncode != 0:
        raise TaskSummaryModelError(result.returncode, result.stdout, result.stderr)
    payload = extract_task_summary_payload(result.stdout)
    if payload is None:
        raise TaskSummaryModelError(1, result.stdout, "claude -p did not return valid task summary JSON")
    write_json(output_path, payload)
    return payload


def run_model_task_summary(prompt, output_path, paths=None, language=None, timeout_seconds=None):
    if MODEL_CLI == "claude":
        return run_claude_task_summary(
            prompt,
            output_path,
            paths=paths,
            language=language,
            timeout_seconds=timeout_seconds,
        )
    return run_codex_task_summary(
        prompt,
        output_path,
        paths=paths,
        language=language,
        timeout_seconds=timeout_seconds,
    )


def artifact_is_current(path, fingerprint):
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("task_cluster_algorithm_version") == TASK_CLUSTER_ALGORITHM_VERSION
        and payload.get("source_fingerprint") == fingerprint
        and payload.get("model_status") == "completed"
    )


def run_task_summary_for_dates(dates, paths=None, force=False, language=None, timeout_seconds=None):
    paths = paths or PATHS
    language = current_language(language)
    date_values = [str(date_str) for date_str in dates if str(date_str or "").strip()]
    if not date_values:
        return {"status": "skipped", "reason": "no_dates", "artifact": ""}
    task_input = build_task_summary_input(date_values, paths=paths)
    fingerprint = input_fingerprint(task_input)
    output_path = task_summary_artifact_path(date_values[0], date_values[-1], paths=paths)
    if not task_input.get("windows"):
        return {"status": "skipped", "reason": "no_window_summaries", "artifact": str(output_path)}
    if not force and artifact_is_current(output_path, fingerprint):
        return {"status": "skipped_existing", "reason": "artifact_current", "artifact": str(output_path)}
    prompt = build_task_summary_prompt(task_input, language=language)
    candidate_path = output_path.with_suffix(".candidate.tmp")
    try:
        raw_payload = run_model_task_summary(
            prompt,
            candidate_path,
            paths=paths,
            language=language,
            timeout_seconds=timeout_seconds or DEFAULT_MODEL_TIMEOUT_SECONDS,
        )
    except TaskSummaryModelError as exc:
        task_summary_dir(paths).mkdir(parents=True, exist_ok=True)
        write_json(
            output_path,
            failed_task_summary_payload(
                task_input,
                error=str(exc),
                model_cli=MODEL_CLI,
                language=language,
            ),
        )
        raise
    finally:
        try:
            candidate_path.unlink()
        except OSError:
            pass
    normalized = normalize_task_summary_payload(
        raw_payload,
        task_input,
        model_cli=MODEL_CLI,
        language=language,
    )
    task_summary_dir(paths).mkdir(parents=True, exist_ok=True)
    write_json(output_path, normalized)
    return {
        "status": "completed",
        "reason": "generated",
        "artifact": str(output_path),
        "cluster_count": len(normalized.get("project_task_clusters") or []),
        "window_count": len(task_input.get("windows") or []),
    }


def resolve_task_summary_dates(window_days=TASK_SUMMARY_WINDOW_DAYS, end_date=None):
    return date_strings_ending_at(end_date or date.today().isoformat(), max(int(window_days or 1), 1))


def load_task_summary_migration_state(paths=None):
    try:
        payload = load_json(task_summary_migration_state_path(paths))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_task_summary_migration_state(paths=None, **fields):
    paths = paths or PATHS
    state = load_task_summary_migration_state(paths)
    state.update(fields)
    status = str(state.get("status") or "")
    if status in {"pending", "running", "skipped", "completed"}:
        state.pop("error", None)
        state.pop("failed_at", None)
    if status in {"pending", "running", "skipped", "failed"}:
        state.pop("completed_at", None)
    state["schema_version"] = TASK_SUMMARY_MIGRATION_STATE_VERSION
    state["task_cluster_algorithm_version"] = TASK_CLUSTER_ALGORITHM_VERSION
    state["updated_at"] = current_timestamp()
    write_json(task_summary_migration_state_path(paths), state)
    return state


def runtime_task_cluster_algorithm_version(paths=None):
    config = load_runtime_config(paths)
    try:
        return int(config.get("task_cluster_algorithm_version") or 0)
    except (TypeError, ValueError):
        return 0


def write_runtime_task_cluster_algorithm_version(paths=None, version=TASK_CLUSTER_ALGORITHM_VERSION):
    paths = paths or PATHS
    config = load_runtime_config(paths)
    config["schema_version"] = int(config.get("schema_version") or 1)
    config["task_cluster_algorithm_version"] = int(version)
    config["task_cluster_algorithm_migrated_at"] = current_timestamp()
    write_json(runtime_config_path(paths), config)
    return config


def has_existing_window_summaries(paths=None):
    paths = paths or PATHS
    try:
        summary_paths = sorted(paths.consolidated_daily_dir.glob("*/summary.json"), reverse=True)
    except OSError:
        return False
    for summary_path in summary_paths:
        try:
            payload = load_json(summary_path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("window_summaries"):
            return True
    return False


def should_schedule_task_summary_migration(paths=None, force=False):
    paths = paths or PATHS
    if not personal_memory_enabled(paths):
        return False
    if force:
        return True
    if not has_existing_window_summaries(paths):
        return False
    return runtime_task_cluster_algorithm_version(paths) < TASK_CLUSTER_ALGORITHM_VERSION


def ensure_task_summary_migration_state(paths=None, window_days=TASK_SUMMARY_WINDOW_DAYS, force=False):
    paths = paths or PATHS
    if not personal_memory_enabled(paths):
        return write_task_summary_migration_state(
            paths,
            status="skipped",
            reason="personal_memory_disabled",
            window_days=int(window_days),
        )
    if not has_existing_window_summaries(paths) and not force:
        write_runtime_task_cluster_algorithm_version(paths)
        return write_task_summary_migration_state(
            paths,
            status="skipped",
            reason="no_existing_window_summaries",
            window_days=int(window_days),
        )
    if should_schedule_task_summary_migration(paths, force=force):
        return write_task_summary_migration_state(
            paths,
            status="pending",
            reason="task_cluster_algorithm_version_changed",
            previous_task_cluster_algorithm_version=runtime_task_cluster_algorithm_version(paths),
            target_task_cluster_algorithm_version=TASK_CLUSTER_ALGORITHM_VERSION,
            window_days=int(window_days),
        )
    return write_task_summary_migration_state(
        paths,
        status="completed",
        reason="already_current",
        window_days=int(window_days),
    )


def mark_task_summary_migration_completed(paths=None, dates=None, window_days=TASK_SUMMARY_WINDOW_DAYS, result=None):
    paths = paths or PATHS
    write_runtime_task_cluster_algorithm_version(paths)
    return write_task_summary_migration_state(
        paths,
        status="completed",
        reason="migration_completed",
        dates=list(dates or []),
        window_days=int(window_days),
        result=result or {},
        completed_at=current_timestamp(),
    )


def mark_task_summary_migration_failed(paths=None, dates=None, error=None, window_days=TASK_SUMMARY_WINDOW_DAYS):
    return write_task_summary_migration_state(
        paths,
        status="failed",
        reason="migration_failed",
        dates=list(dates or []),
        window_days=int(window_days),
        error=safe_task_summary_error_message(error),
        failed_at=current_timestamp(),
    )


def print_task_summary_migration_state(state):
    print(localized("并行任务总结迁移状态", "Parallel task summary migration status"))
    print("- status: {}".format(state.get("status") or "unknown"))
    print("- task_cluster_algorithm_version: {}".format(state.get("task_cluster_algorithm_version") or TASK_CLUSTER_ALGORITHM_VERSION))
    print("- window_days: {}".format(state.get("window_days") or TASK_SUMMARY_WINDOW_DAYS))
    if state.get("reason"):
        print("- reason: {}".format(state.get("reason")))
    if state.get("dates"):
        print("- dates: {}".format(", ".join(state.get("dates") or [])))
    result = state.get("result") or {}
    if isinstance(result, dict) and result:
        print("- result: {}".format(result.get("status", "")))
        if result.get("artifact"):
            print("- artifact: {}".format(result.get("artifact")))
        if result.get("cluster_count") is not None:
            print("- clusters: {}".format(result.get("cluster_count")))
    print("- state_file: {}".format(task_summary_migration_state_path()))


def command_migration(args):
    window_days = max(int(args.window_days or TASK_SUMMARY_WINDOW_DAYS), 1)
    if args.action == "status":
        state = load_task_summary_migration_state()
        if not state:
            state = ensure_task_summary_migration_state(window_days=window_days, force=False)
        if args.json:
            print(json.dumps(state, ensure_ascii=False, indent=2))
        else:
            print_task_summary_migration_state(state)
        return
    if args.action == "ensure":
        state = ensure_task_summary_migration_state(window_days=window_days, force=args.force)
        if args.json:
            print(json.dumps(state, ensure_ascii=False, indent=2))
        elif not args.quiet:
            print_task_summary_migration_state(state)
        return

    state = ensure_task_summary_migration_state(window_days=window_days, force=args.force)
    if args.if_pending and state.get("status") != "pending":
        if args.json:
            print(json.dumps(state, ensure_ascii=False, indent=2))
        elif not args.quiet:
            print_task_summary_migration_state(state)
        return
    if state.get("status") != "pending" and not args.force:
        if args.json:
            print(json.dumps(state, ensure_ascii=False, indent=2))
        elif not args.quiet:
            print_task_summary_migration_state(state)
        return

    dates = resolve_task_summary_dates(window_days=window_days, end_date=args.to)
    write_task_summary_migration_state(
        status="running",
        reason="task_cluster_algorithm_version_changed",
        dates=dates,
        window_days=window_days,
        started_at=current_timestamp(),
    )
    try:
        result = run_task_summary_for_dates(
            dates,
            force=True,
            timeout_seconds=args.model_timeout_seconds,
        )
        completed = mark_task_summary_migration_completed(dates=dates, window_days=window_days, result=result)
    except Exception as exc:
        failed = mark_task_summary_migration_failed(dates=dates, error=str(exc), window_days=window_days)
        if args.json:
            print(json.dumps(failed, ensure_ascii=False, indent=2))
        elif not args.quiet:
            print_task_summary_migration_state(failed)
        raise SystemExit(getattr(exc, "returncode", 1) or 1) from exc
    if args.json:
        print(json.dumps(completed, ensure_ascii=False, indent=2))
    elif not args.quiet:
        print_task_summary_migration_state(completed)


def command_run(args):
    if args.dates:
        dates = [part for part in re.split(r"[\s,]+", args.dates.strip()) if part]
    else:
        dates = resolve_task_summary_dates(window_days=args.window_days, end_date=args.to)
    result = run_task_summary_for_dates(
        dates,
        force=args.force,
        timeout_seconds=args.model_timeout_seconds,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif not args.quiet:
        print(localized("并行任务总结完成", "Parallel task summary complete"))
        for key in ("status", "reason", "artifact", "cluster_count", "window_count"):
            if result.get(key) is not None:
                print("- {}: {}".format(key, result.get(key)))
    if result.get("status") == "failed":
        raise SystemExit(1)


def build_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run")
    run.add_argument("--dates", help="Comma- or space-separated YYYY-MM-DD dates.")
    run.add_argument("--to", default=date.today().isoformat())
    run.add_argument("--window-days", type=int, default=TASK_SUMMARY_WINDOW_DAYS)
    run.add_argument("--force", action="store_true")
    run.add_argument("--quiet", action="store_true")
    run.add_argument("--json", action="store_true")
    run.add_argument("--model-timeout-seconds", type=int, default=DEFAULT_MODEL_TIMEOUT_SECONDS)

    migration = subparsers.add_parser("migration")
    migration.add_argument("action", nargs="?", default="status", choices=["status", "ensure", "run"])
    migration.add_argument("--to", default=date.today().isoformat())
    migration.add_argument("--window-days", type=int, default=TASK_SUMMARY_WINDOW_DAYS)
    migration.add_argument("--force", action="store_true")
    migration.add_argument("--if-pending", action="store_true")
    migration.add_argument("--quiet", action="store_true")
    migration.add_argument("--json", action="store_true")
    migration.add_argument("--model-timeout-seconds", type=int, default=DEFAULT_MODEL_TIMEOUT_SECONDS)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    if args.command == "run":
        command_run(args)
        return
    if args.command == "migration":
        command_migration(args)
        return
    parser.print_help()


if __name__ == "__main__":
    main()
