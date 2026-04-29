#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from asset_runtime import (
    atomic_write_json,
    ensure_state_layout,
    get_runtime_language,
    get_runtime_paths,
    sync_codex_exec_home,
)
import build_overview
import nightly_consolidate


PATHS = get_runtime_paths()
LANGUAGE = get_runtime_language(PATHS)
DEFAULT_MEMORY_SUMMARY = PATHS.codex_home / "memories" / "memory_summary.md"
DEFAULT_MEMORY_INDEX = PATHS.codex_home / "memories" / "MEMORY.md"
DEFAULT_OUTPUT = PATHS.runtime_dir / "codex-native-display-cache.json"
DEFAULT_MAX_ITEMS = 80


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate model-polished Chinese display copy for Codex native memory cards."
    )
    parser.add_argument("--memory-summary", default=str(DEFAULT_MEMORY_SUMMARY))
    parser.add_argument("--memory-index", default=str(DEFAULT_MEMORY_INDEX))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--print-only", action="store_true")
    return parser.parse_args()


def compact(text, limit):
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 1)].rstrip() + "…"


def source_text(*parts):
    return build_overview.codex_native_display_source_text(*parts)


def collect_entries(memory_summary_path, memory_index_path, max_items):
    entries = []

    def append(kind, title, body, source_label):
        title = build_overview.normalize_brand_display_text(title)
        body = build_overview.normalize_brand_display_text(body)
        if not title and not body:
            return
        key = build_overview.codex_native_display_cache_key(kind, title or body, body or title)
        entries.append(
            {
                "key": key,
                "kind": kind,
                "source_label": source_label,
                "source_title": compact(title or body, 220),
                "source_body": compact(body or title, 520),
            }
        )

    if memory_summary_path and Path(memory_summary_path).is_file():
        parsed = build_overview.parse_codex_native_memory_summary(
            memory_summary_path,
            language="en",
        )

        for row in parsed.get("preference_rows", []):
            body = source_text(row.get("display_body_en") or row.get("body") or "")
            append("preference", body, body, "User preferences")

        for row in parsed.get("tip_rows", []):
            body = source_text(row.get("display_body_en") or row.get("body") or "")
            append("tip", body, body, "General Tips")

        for row in parsed.get("rows", []):
            title = row.get("display_title_en") or row.get("title") or ""
            body = row.get("display_value_note_en") or row.get("value_note_en") or row.get("value_note") or title
            append("topic", title, body, "What's in Memory")

    if memory_index_path and Path(memory_index_path).is_file():
        index_stats = build_overview.load_codex_memory_index_stats(
            memory_index_path,
            language="en",
        )
        for row in index_stats.get("task_groups", []):
            title = source_text(row.get("display_title_en") or row.get("title") or "")
            body = source_text(
                row.get("display_body_en")
                or row.get("body")
                or row.get("display_body")
                or title
            )
            append("task_group", title, body, "Task Groups")

    deduped = []
    seen = set()
    for entry in entries:
        if entry["key"] in seen:
            continue
        seen.add(entry["key"])
        deduped.append(entry)
        if max_items > 0 and len(deduped) >= max_items:
            break
    return deduped


def display_schema_path():
    path = PATHS.runtime_dir / "codex-native-display-cache.schema.json"
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["key", "title_zh", "body_zh"],
                    "properties": {
                        "key": {"type": "string"},
                        "title_zh": {"type": "string"},
                        "body_zh": {"type": "string"},
                    },
                },
            }
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_prompt(entries):
    return """你是 OpenRelix 面板的中文展示文案整理器。请把 Codex native memory 的英文/混合语言条目改写成通俗、准确、可扫描的中文卡片文案。

要求：
1. 只根据输入内容改写，不要编造新事实。
2. title_zh 必须是中文为主，8 到 28 个汉字左右，不要带“偏好：”“通用 tips：”“任务组：”“主题：”这类类别前缀。
3. body_zh 必须是中文说明，1 句话，尽量说清楚这条记忆对后续执行有什么帮助。
4. 保留命令、路径占位、代码符号、版本号、配置名、产品名等专有名词；可以把英文动作和规则翻译成中文。
5. 不要输出 Markdown，不要输出解释，只输出符合 schema 的 JSON。
6. 每个输入 key 都必须原样返回一条结果。

<entries_json>
{entries_json}
</entries_json>
""".format(entries_json=json.dumps(entries, ensure_ascii=False, indent=2))


def build_safe_display_prompt(prompt):
    safety_preamble = (
        "这是一个纯文案整理任务，不是软件工程任务。"
        "禁止调用 shell、web、MCP、apply_patch 或读取任何额外文件。"
        "不要探索环境；唯一合法输入就是下方 entries_json。"
        "不要输出 Markdown，不要输出解释；直接输出符合 schema 的 JSON。\n\n"
    )
    return safety_preamble + prompt


def empty_payload(memory_summary_path, status="empty", error=""):
    return {
        "version": 1,
        "language": LANGUAGE,
        "status": status,
        "source": str(memory_summary_path),
        "items": {},
        "error": error,
    }


def run_codex_display_generation(entries, output_path):
    schema_path = display_schema_path()
    raw_output_path = PATHS.runtime_dir / "codex-native-display-cache.raw.json"
    PATHS.nightly_runner_dir.mkdir(parents=True, exist_ok=True)
    sync_codex_exec_home(PATHS.codex_home, PATHS.nightly_codex_home)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(PATHS.nightly_codex_home)
    cmd = [
        str(PATHS.codex_bin),
        "exec",
        "--skip-git-repo-check",
        "--cd",
        str(PATHS.nightly_runner_dir),
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--disable",
        "memories",
        "--disable",
        "codex_hooks",
        "-c",
        'approval_policy="never"',
        "-c",
        'history.persistence="none"',
        "-c",
        "history.max_bytes=1048576",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(raw_output_path),
        "-",
    ]
    prompt = build_safe_display_prompt(build_prompt(entries))
    result = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        raise nightly_consolidate.CodexConsolidationError(
            result.returncode,
            result.stdout,
            result.stderr,
        )
    payload = json.loads(raw_output_path.read_text(encoding="utf-8"))
    items = {}
    expected_keys = {entry["key"] for entry in entries}
    for item in payload.get("items", []):
        key = item.get("key")
        if key not in expected_keys:
            continue
        title_zh = build_overview.normalize_brand_display_text(item.get("title_zh", ""))
        body_zh = build_overview.normalize_brand_display_text(item.get("body_zh", ""))
        if not title_zh and not body_zh:
            continue
        items[key] = {
            "title_zh": title_zh,
            "body_zh": body_zh,
        }
    missing_keys = sorted(expected_keys - set(items))
    return {
        "version": 1,
        "language": "zh",
        "status": "ok" if not missing_keys else "partial",
        "source": str(output_path),
        "items": items,
        "missing_keys": missing_keys,
    }


def main():
    args = parse_args()
    ensure_state_layout(PATHS)
    memory_summary_path = Path(args.memory_summary).expanduser()
    memory_index_path = Path(args.memory_index).expanduser()
    output_path = Path(args.output).expanduser()
    if LANGUAGE != "zh":
        payload = empty_payload(memory_summary_path, status="skipped_non_zh")
    elif not memory_summary_path.is_file() and not memory_index_path.is_file():
        payload = empty_payload(memory_summary_path, status="missing_source")
    else:
        entries = collect_entries(memory_summary_path, memory_index_path, max(args.max_items, 0))
        if not entries:
            payload = empty_payload(memory_summary_path)
        else:
            try:
                payload = run_codex_display_generation(entries, output_path)
                payload["source"] = str(memory_summary_path)
            except Exception as exc:
                payload = empty_payload(
                    memory_summary_path,
                    status="model_failed",
                    error=str(exc),
                )

    if args.print_only:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(output_path, payload)
    return 0 if payload.get("status") != "model_failed" else 1


if __name__ == "__main__":
    sys.exit(main())
