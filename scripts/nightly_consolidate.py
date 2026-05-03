#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from asset_runtime import (
    atomic_write_json,
    atomic_write_text,
    ensure_state_layout,
    get_codex_model,
    get_memory_mode,
    get_runtime_language,
    get_runtime_paths,
    normalize_language,
    personal_memory_enabled,
    sync_codex_exec_home,
)

PATHS = get_runtime_paths()
LANGUAGE = get_runtime_language(PATHS)
MEMORY_MODE = get_memory_mode(PATHS)
CODEX_MODEL = get_codex_model(PATHS)
PERSONAL_MEMORY_ENABLED = personal_memory_enabled(PATHS)
MAIN_CODEX_HOME = PATHS.codex_home
RAW_DIR = PATHS.raw_dir
REGISTRY_DIR = PATHS.registry_dir
CONSOLIDATED_DIR = PATHS.consolidated_daily_dir
CODEX_BIN = PATHS.codex_bin
SCHEMA_PATH = PATHS.schema_path
RUNTIME_DIR = PATHS.nightly_runner_dir
NIGHTLY_CODEX_HOME = PATHS.nightly_codex_home
CLUSTER_VARIANT_SAMPLE_LIMIT = 2
CLUSTER_MIN_NORMALIZED_LENGTH = 24
CLUSTER_LONG_TEXT_LENGTH = 80
CLUSTER_MEDIUM_SIMILARITY_THRESHOLD = 0.91
CLUSTER_LONG_SIMILARITY_THRESHOLD = 0.86
LEARNING_MEMORY_SAMPLE_LIMIT = 10
LEARNING_SUMMARY_SAMPLE_LIMIT = 3
LEARNING_JOURNAL_SAMPLE_LIMIT = 4
LEARNING_WINDOW_SAMPLE_LIMIT = 12
LEARNING_WINDOW_PATTERN_LIMIT = 6
LEARNING_WINDOW_BATCH_SIZE = 20
LEARNING_WINDOW_BATCH_KEYWORD_LIMIT = 8
LEARNING_WINDOW_BATCH_TAKEAWAY_LIMIT = 5
QUALITY_REPLACE_THRESHOLD = 8
SPARSE_WINDOW_THRESHOLD = 3
SPARSE_PROMPT_THRESHOLD = 12
SPARSE_CONCLUSION_THRESHOLD = 4
STAGE_PRIORITY = {"manual": 0, "preliminary": 1, "final": 2}
COMPACT_PAYLOAD_CACHE_VERSION = 1
RECENT_WINDOW_LEARNING_CACHE_VERSION = 1
_COMPACT_PAYLOAD_CACHE = {}
_RECENT_WINDOW_LEARNING_CACHE = {}


class CodexConsolidationError(RuntimeError):
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        super().__init__(describe_codex_failure(self.stdout, self.stderr, returncode))


def current_language(language=None):
    return normalize_language(language or LANGUAGE)


def localized(zh_text, en_text, language=None):
    return en_text if current_language(language) == "en" else zh_text


def sanitize_process_text(text):
    compact = str(text or "")
    if not compact:
        return ""
    compact = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "sk-***", compact)
    compact = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1***", compact, flags=re.IGNORECASE)
    compact = re.sub(r"(refresh_token[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+", r"\1***", compact, flags=re.IGNORECASE)
    compact = re.sub(r"(access_token[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+", r"\1***", compact, flags=re.IGNORECASE)
    return "\n".join(line.rstrip() for line in compact.splitlines() if line.strip())[-1200:]


def describe_codex_failure(stdout, stderr, returncode):
    text = sanitize_process_text("\n".join(part for part in (stdout, stderr) if part))
    if not text:
        return "codex exec failed with exit code {}".format(returncode)
    return text


def codex_failure_hint(error_text, language=None):
    lowered = str(error_text or "").lower()
    if "invalid_issuer" in lowered or "401" in lowered or "unauthorized" in lowered:
        return localized(
            "Codex/OpenAI 认证被拒绝。请先确认 `codex exec` 在普通终端可用；如果使用集体/代理配置，确认 `CODEX_HOME/config.toml` 中的 model_provider/base_url 与 auth.json 一起存在；如果使用官方 OpenAI API key，再清理或替换错误的 `OPENAI_API_KEY` 后重试。",
            "Codex/OpenAI authentication was rejected. First confirm `codex exec` works in a normal terminal. If you use a shared/proxy provider, make sure `CODEX_HOME/config.toml` keeps the matching model_provider/base_url together with auth.json. If you use an official OpenAI API key, clear or replace an invalid `OPENAI_API_KEY`, then retry.",
            language,
        )
    return localized(
        "请先确认 `codex exec` 在该用户机器上可用，再重新运行本次学习刷新。",
        "Confirm `codex exec` works on that user's machine, then rerun this learning refresh.",
        language,
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().astimezone().date().isoformat())
    parser.add_argument("--stage", default="manual", choices=["manual", "preliminary", "final"])
    parser.add_argument(
        "--learn-window-days",
        type=int,
        default=0,
        help="When > 0, learn from recent window summaries in the previous N days for this manual run only.",
    )
    parser.add_argument(
        "--skip-if-unchanged",
        action="store_true",
        help="Skip model consolidation when the raw payload and learning context match the selected summary.",
    )
    return parser.parse_args()


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, payload):
    atomic_write_json(path, payload)


def cache_disabled():
    return str(os.environ.get("OPENRELIX_DISABLE_NIGHTLY_CACHE", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def default_cache_dir():
    return RUNTIME_DIR / "nightly-cache"


def json_fingerprint(payload):
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def file_fingerprint(path):
    path = Path(path)
    if not path.exists():
        return {"exists": False}
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {"exists": True, "error": exc.__class__.__name__}
    return {
        "exists": True,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def cache_file_path(cache_dir, namespace, fingerprint):
    if not cache_dir or cache_disabled():
        return None
    return Path(cache_dir) / namespace / "{}.json".format(fingerprint)


def read_cached_payload(cache_dir, namespace, fingerprint, payload_key):
    cache_path = cache_file_path(cache_dir, namespace, fingerprint)
    if not cache_path or not cache_path.exists():
        return None
    try:
        cached = load_json(cache_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(cached, dict):
        return None
    if cached.get("fingerprint") != fingerprint:
        return None
    return cached.get(payload_key)


def write_cached_payload(cache_dir, namespace, fingerprint, payload_key, payload):
    cache_path = cache_file_path(cache_dir, namespace, fingerprint)
    if not cache_path:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(
            cache_path,
            {
                "fingerprint": fingerprint,
                "generated_at": datetime.now().astimezone().isoformat(),
                payload_key: payload,
            },
        )
    except OSError:
        return


def remember_cached_value(cache, key, payload, max_size=64):
    if cache_disabled():
        return
    if len(cache) >= max_size:
        cache.clear()
    cache[key] = payload


def clip_text(text, limit):
    if text is None:
        return ""
    compact = " ".join(str(text).split())
    return compact[:limit] + ("..." if len(compact) > limit else "")


def normalize_cluster_text(text):
    compact = " ".join(str(text).split())
    if not compact:
        return ""
    compact = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", compact)
    compact = compact.lower()
    for pattern, replacement in (
        (r"file://[^\s)]+", "<path>"),
        (r"(?:/users|/home|~)/[^\s)]+", "<path>"),
        (r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "<id>"),
        (r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ t][0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?\b", "<time>"),
        (r"\b[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?\b", "<time>"),
        (r"\b[0-9a-f]{16,}\b", "<hash>"),
        (r"\b\d{4,}\b", "<num>"),
    ):
        compact = re.sub(pattern, replacement, compact, flags=re.IGNORECASE)
    compact = re.sub(r"[^0-9a-z\u4e00-\u9fff<>]+", "", compact)
    return compact


def build_char_ngrams(text, size=3):
    if not text:
        return set()
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def cluster_match_score(left, right):
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    shorter, longer = sorted((left, right), key=len)
    if (
        len(shorter) >= CLUSTER_MIN_NORMALIZED_LENGTH
        and shorter in longer
        and len(shorter) / max(len(longer), 1) >= 0.72
    ):
        return 0.98
    ratio = SequenceMatcher(None, left, right).ratio()
    if len(left) < CLUSTER_MIN_NORMALIZED_LENGTH or len(right) < CLUSTER_MIN_NORMALIZED_LENGTH:
        return ratio
    left_ngrams = build_char_ngrams(left)
    right_ngrams = build_char_ngrams(right)
    union = left_ngrams | right_ngrams
    if not union:
        return ratio
    jaccard = len(left_ngrams & right_ngrams) / len(union)
    return max(ratio, jaccard)


def is_similar_cluster_match(left, right, score):
    min_length = min(len(left), len(right))
    if min_length < CLUSTER_MIN_NORMALIZED_LENGTH:
        return score >= 1.0
    if min_length >= CLUSTER_LONG_TEXT_LENGTH:
        return score >= CLUSTER_LONG_SIMILARITY_THRESHOLD
    return score >= CLUSTER_MEDIUM_SIMILARITY_THRESHOLD


def append_variant_sample(cluster, sample_text):
    if not sample_text:
        return
    variants = cluster["variant_samples"]
    if sample_text == cluster["representative_text"]:
        return
    if sample_text in variants:
        return
    if len(variants) < CLUSTER_VARIANT_SAMPLE_LIMIT:
        variants.append(sample_text)


def build_text_clusters(rows, text_key, clip_limit):
    clusters = []
    for row in rows:
        raw_text = " ".join(str(row.get(text_key, "")).split())
        if not raw_text:
            continue
        normalized = normalize_cluster_text(raw_text)
        if not normalized:
            continue
        clipped = clip_text(raw_text, clip_limit)
        best_cluster = None
        best_score = 0.0
        for cluster in clusters:
            score = cluster_match_score(normalized, cluster["_normalized_representative"])
            if score > best_score and is_similar_cluster_match(normalized, cluster["_normalized_representative"], score):
                best_cluster = cluster
                best_score = score
        if best_cluster is None:
            clusters.append(
                {
                    "merged_count": 1,
                    "representative_text": clipped,
                    "variant_samples": [],
                    "_normalized_representative": normalized,
                    "_representative_raw": raw_text,
                }
            )
            continue

        best_cluster["merged_count"] += 1
        append_variant_sample(best_cluster, clipped)
        if len(raw_text) > len(best_cluster["_representative_raw"]):
            previous_text = best_cluster["representative_text"]
            best_cluster["_representative_raw"] = raw_text
            best_cluster["_normalized_representative"] = normalized
            best_cluster["representative_text"] = clipped
            append_variant_sample(best_cluster, previous_text)

    compact_clusters = []
    for cluster in clusters:
        compact_clusters.append(
            {
                "merged_count": cluster["merged_count"],
                "representative_text": cluster["representative_text"],
                "variant_samples": cluster["variant_samples"],
            }
        )
    return compact_clusters


def render_cluster_sample(cluster, language=None):
    if cluster["merged_count"] <= 1:
        return cluster["representative_text"]
    if current_language(language) == "en":
        parts = [
            "[merged {} similar items] {}".format(
                cluster["merged_count"],
                cluster["representative_text"],
            )
        ]
        if cluster["variant_samples"]:
            parts.append("variants: {}".format("; ".join(cluster["variant_samples"])))
        return " ".join(parts)

    parts = ["[合并{}条同类项] {}".format(cluster["merged_count"], cluster["representative_text"])]
    if cluster["variant_samples"]:
        parts.append("变体：{}".format("；".join(cluster["variant_samples"])))
    return " ".join(parts)


def compact_payload_cache_input(raw_payload):
    return {
        "date": raw_payload.get("date", ""),
        "window_count": raw_payload.get("window_count", 0),
        "prompt_count": raw_payload.get("prompt_count", 0),
        "conclusion_count": raw_payload.get("conclusion_count", 0),
        "windows": [
            {
                "window_id": window.get("window_id", ""),
                "cwd": window.get("cwd", ""),
                "prompt_count": window.get("prompt_count", 0),
                "conclusion_count": window.get("conclusion_count", 0),
                "prompts": window.get("prompts", []),
                "conclusions": window.get("conclusions", []),
            }
            for window in raw_payload.get("windows", [])
        ],
    }


def compact_payload_fingerprint(raw_payload, language=None):
    payload = {
        "version": COMPACT_PAYLOAD_CACHE_VERSION,
        "language": current_language(language),
        "cluster_settings": {
            "variant_sample_limit": CLUSTER_VARIANT_SAMPLE_LIMIT,
            "min_normalized_length": CLUSTER_MIN_NORMALIZED_LENGTH,
            "long_text_length": CLUSTER_LONG_TEXT_LENGTH,
            "medium_similarity_threshold": CLUSTER_MEDIUM_SIMILARITY_THRESHOLD,
            "long_similarity_threshold": CLUSTER_LONG_SIMILARITY_THRESHOLD,
        },
        "raw": compact_payload_cache_input(raw_payload),
    }
    return json_fingerprint(payload)


def is_valid_compact_payload(payload, raw_payload=None):
    if not isinstance(payload, dict):
        return False
    windows = payload.get("windows")
    if not isinstance(windows, list):
        return False
    for key in ("date", "window_count", "prompt_count", "conclusion_count"):
        if key not in payload:
            return False
    for key in ("window_count", "prompt_count", "conclusion_count"):
        if not isinstance(payload.get(key), int) or payload.get(key) < 0:
            return False
    if raw_payload is not None:
        if payload.get("date") != raw_payload.get("date"):
            return False
        for key in ("window_count", "prompt_count", "conclusion_count"):
            if payload.get(key) != raw_payload.get(key):
                return False
    if len(windows) != payload.get("window_count"):
        return False
    raw_windows = raw_payload.get("windows", []) if raw_payload is not None else None
    if raw_windows is not None and len(windows) != len(raw_windows):
        return False
    for window in windows:
        if not isinstance(window, dict):
            return False
        for key in (
            "window_id",
            "cwd",
            "prompt_count",
            "conclusion_count",
            "prompt_cluster_count",
            "conclusion_cluster_count",
            "prompt_samples",
            "conclusion_samples",
        ):
            if key not in window:
                return False
        for key in (
            "prompt_count",
            "conclusion_count",
            "prompt_cluster_count",
            "conclusion_cluster_count",
        ):
            if not isinstance(window.get(key), int) or window.get(key) < 0:
                return False
        if not isinstance(window.get("prompt_samples"), list):
            return False
        if not isinstance(window.get("conclusion_samples"), list):
            return False
        if not all(isinstance(sample, str) for sample in window.get("prompt_samples", [])):
            return False
        if not all(isinstance(sample, str) for sample in window.get("conclusion_samples", [])):
            return False
        if window.get("prompt_cluster_count") > window.get("prompt_count"):
            return False
        if window.get("conclusion_cluster_count") > window.get("conclusion_count"):
            return False
        if len(window.get("prompt_samples", [])) != window.get("prompt_cluster_count"):
            return False
        if len(window.get("conclusion_samples", [])) != window.get("conclusion_cluster_count"):
            return False
    if raw_windows is not None:
        for cached_window, raw_window in zip(windows, raw_windows):
            for key in ("window_id", "cwd", "prompt_count", "conclusion_count"):
                if cached_window.get(key) != raw_window.get(key):
                    return False
    return True


def build_compact_payload(raw_payload, language=None, cache_dir=None, fingerprint=None):
    language = current_language(language)
    use_cache = not cache_disabled()
    fingerprint = fingerprint or (
        compact_payload_fingerprint(raw_payload, language=language) if use_cache else ""
    )
    if use_cache and fingerprint in _COMPACT_PAYLOAD_CACHE:
        cached_memory = _COMPACT_PAYLOAD_CACHE[fingerprint]
        if is_valid_compact_payload(cached_memory, raw_payload=raw_payload):
            return cached_memory
        _COMPACT_PAYLOAD_CACHE.pop(fingerprint, None)

    cached = read_cached_payload(
        cache_dir,
        "compact-payload",
        fingerprint,
        "compact_payload",
    )
    if is_valid_compact_payload(cached, raw_payload=raw_payload):
        remember_cached_value(_COMPACT_PAYLOAD_CACHE, fingerprint, cached)
        return cached

    windows = []
    for window in raw_payload["windows"]:
        prompt_clusters = build_text_clusters(window["prompts"], "text", 220)
        conclusion_clusters = build_text_clusters(window["conclusions"], "text", 420)
        windows.append(
            {
                "window_id": window["window_id"],
                "cwd": window["cwd"],
                "prompt_count": window["prompt_count"],
                "conclusion_count": window["conclusion_count"],
                "prompt_cluster_count": len(prompt_clusters),
                "conclusion_cluster_count": len(conclusion_clusters),
                "prompt_samples": [
                    render_cluster_sample(cluster, language=language)
                    for cluster in prompt_clusters
                ],
                "conclusion_samples": [
                    render_cluster_sample(cluster, language=language)
                    for cluster in conclusion_clusters
                ],
            }
        )
    compact_payload = {
        "date": raw_payload["date"],
        "window_count": raw_payload["window_count"],
        "prompt_count": raw_payload["prompt_count"],
        "conclusion_count": raw_payload["conclusion_count"],
        "windows": windows,
    }
    remember_cached_value(_COMPACT_PAYLOAD_CACHE, fingerprint, compact_payload)
    write_cached_payload(
        cache_dir,
        "compact-payload",
        fingerprint,
        "compact_payload",
        compact_payload,
    )
    return compact_payload


def build_prompt(raw_payload, language=None):
    return build_prompt_with_learning(raw_payload, {}, language=language)


def build_prompt_with_learning(raw_payload, learning_context, language=None, compact_payload=None):
    language = current_language(language)
    if compact_payload is None:
        compact_payload = build_compact_payload(raw_payload, language=language)
    if language == "en":
        return """You are a nightly organization agent. Your job is to convert the user's questions and the final conclusions from multiple Codex windows on the same day into personal asset results that are readable and searchable the next day.

Organization principles:
1. Assume the user's same-day questions are valuable by default; do not discard them silently.
2. Classify results into long-term reusable memories, short-term work memories, and low-priority memories.
3. Long-term reusable memories should be useful across days. They are usually methods, rules, module mappings, debugging paths, or stable preferences.
4. Short-term work memories are usually tied to the current request, same-day task, or temporary decision, and may expire later.
5. Low-priority memories should still be retained, but with lower priority so they do not dominate attention.
6. Write every generated summary, memory title, value_note, keyword, and next action in English. Preserve source identifiers, file paths, code symbols, command names, and user-provided proper nouns exactly.
7. Do not invent facts. Only organize information from the input prompt and conclusion fields.
8. source_window_ids must only use window_id values that appear in the input.
9. learning_context is only for learning granularity, stability judgement, and avoiding regressions. Do not copy facts from learning_context back into today's result unless they also appear in today's input.
10. If today's input signal is rich but you produce too few long-term or short-term memories, first reconsider whether your classification is too conservative.
11. If learning_context contains recent_window_learning, it represents recent batch summaries and patterns. Use it only to learn which window types deserve durable or session memories; do not import that historical content into today's output.
12. recent_window_learning.coverage / batch_summaries represent full historical-window coverage; window_samples are only representative samples, not the complete historical set.
13. For each window summary, write window_title as a plain-language title under 100 characters. Do not reuse raw window IDs, paths, Markdown, or numbered question labels as the title. Then populate summary_pairs with 1 to many readable question/conclusion pairs. If a window contains multiple distinct questions and conclusions, aggregate related turns but keep each pair one-to-one and ordered from oldest to newest.

Input data follows. This is a compact same-day view: each window conservatively clusters near-duplicate or variant prompt / conclusion text before it is shown to you.
- In prompt_samples / conclusion_samples, a `[merged N similar items]` prefix means the sample represents N similar items.
- When useful, a sample may end with `variants: ...`; those variants only show wording differences and are not a full raw transcript.
Strictly base your output on these clusters. You may learn abstraction granularity and classification style from good historical examples or recent window patterns in learning_context, but you must not copy their business facts.
<learning_context_json>
{learning_json}
</learning_context_json>
<daily_compact_json>
{raw_json}
</daily_compact_json>
""".format(
            learning_json=json.dumps(learning_context, ensure_ascii=False, indent=2),
            raw_json=json.dumps(compact_payload, ensure_ascii=False, indent=2),
        )

    return """你是一个夜间整理代理，负责把同一天内多个 Codex 主窗口里的用户问题与最终结论，整理成第二天可读、可检索的个人资产结果。

整理原则：
1. 默认认为用户当天问的内容都有价值，不要丢弃。
2. 但要分类：长期可复用、短期工作记忆、低优先级记忆。
3. 长期可复用记忆适合跨天复用，通常是方法、规则、模块映射、排障路径、稳定偏好。
4. 短期工作记忆通常和当前需求、当天任务、临时决策有关，后续可能失效。
5. 低优先级记忆也要保留，但优先级调低，不应占据主要注意力。
6. 所有输出都使用中文。
7. 不要编造信息；仅根据输入里的 prompt 和 conclusion 整理。
8. source_window_ids 必须只使用输入中出现过的 window_id。
9. learning_context 只用于学习粒度、稳定性判断和避免回归，不能把其中未在当日输入里出现的事实抄回今天的结果。
10. 如果当日输入信号已经很丰富，但你给出的长期 / 短期记忆过少，要先反思是否归类过于保守。
11. 如果 learning_context 里出现 recent_window_learning，它代表近几天窗口的批次摘要与模式，仅用于学习哪些窗口类型更适合抽象成 durable / session 记忆，不代表这些窗口内容应该直接进入今天的输出。
12. recent_window_learning.coverage / batch_summaries 代表历史窗口的全量覆盖；window_samples 只是少量代表样本，不是历史窗口全集。
13. 每个 window_summaries 项都要填写 window_title 和 summary_pairs。window_title 要用通俗易懂的话概括窗口主题，最好不超过 100 字；不要直接复用原始窗口 ID、路径、Markdown 或“问题1/问题2”这类编号标签当标题。summary_pairs 要聚合成 1 到多个可读的问题/结论对，同一组问题和结论必须一一对应，并按从旧到新的顺序排列。

输入数据如下。注意：这是已经压缩过的当日视图；每个窗口会先把近重复、同类变体的 prompt / conclusion 做保守聚类，再提供给你。
- prompt_samples / conclusion_samples 里，如果样本带有 `[合并N条同类项]` 前缀，表示这一条代表了 N 条相近内容。
- 如有必要，样本尾部会附少量 `变体：...`，只用于提示不同表述，不代表完整原文列表。
你必须严格只基于这些聚类结果整理。当 learning_context 提供了过往“较好结果”的例子或近几天窗口模式时，你可以学习它们的抽象粒度和分类方式，但不能照抄里面的业务事实。
<learning_context_json>
{learning_json}
</learning_context_json>
<daily_compact_json>
{raw_json}
</daily_compact_json>
""".format(
        learning_json=json.dumps(learning_context, ensure_ascii=False, indent=2),
        raw_json=json.dumps(compact_payload, ensure_ascii=False, indent=2)
    )


def parse_summary_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def humanize_context_label(raw_cwd, language=None):
    text = str(raw_cwd or "").strip()
    if not text:
        return localized("未命名工作区", "Unnamed workspace", language)
    path = Path(text).expanduser()
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path

    home = Path.home().resolve()
    if resolved == home:
        return localized("个人工作区", "Personal workspace", language)
    if str(resolved).startswith(str(PATHS.codex_home)):
        return localized("Codex 本地环境", "Codex local environment", language)
    if str(resolved).startswith(str(PATHS.state_root)):
        return "OpenRelix"

    candidate = resolved.name or text.rstrip("/").rsplit("/", 1)[-1]
    compact = re.sub(r"[_-]+", " ", candidate).strip()
    return compact or candidate or text


def load_summary_for_date(date_str):
    path = CONSOLIDATED_DIR / str(date_str) / "summary.json"
    if not path.exists():
        return None
    return load_json(path)


def load_raw_daily_for_date(date_str):
    path = RAW_DIR / "daily" / "{}.json".format(date_str)
    if not path.exists():
        return None
    return load_json(path)


def summarize_window_learning_batch(batch_id, date_str, samples):
    context_counter = Counter()
    keyword_counter = Counter()
    prompt_count = 0
    conclusion_count = 0

    for sample in samples:
        context = sample.get("context", "")
        if context:
            context_counter[context] += 1
        keyword_counter.update(keyword for keyword in sample.get("keywords", []) if keyword)
        prompt_count += sample.get("prompt_count", 0)
        conclusion_count += sample.get("conclusion_count", 0)

    ranked_samples = sorted(
        samples,
        key=lambda item: (
            item.get("_signal_score", 0),
            item.get("prompt_count", 0),
            item.get("context", ""),
        ),
        reverse=True,
    )
    takeaways = []
    for sample in ranked_samples:
        takeaway = sample.get("main_takeaway", "")
        if takeaway and takeaway not in takeaways:
            takeaways.append(takeaway)
        if len(takeaways) >= LEARNING_WINDOW_BATCH_TAKEAWAY_LIMIT:
            break

    return {
        "batch_id": batch_id,
        "date": date_str,
        "window_count": len(samples),
        "prompt_count": prompt_count,
        "conclusion_count": conclusion_count,
        "contexts": [
            {"context": context, "window_count": count}
            for context, count in context_counter.most_common(6)
        ],
        "top_keywords": [
            keyword
            for keyword, _ in keyword_counter.most_common(LEARNING_WINDOW_BATCH_KEYWORD_LIMIT)
        ],
        "sample_takeaways": takeaways,
    }


def build_window_learning_batches(samples):
    samples_by_date = {}
    for sample in samples:
        samples_by_date.setdefault(sample.get("date", ""), []).append(sample)

    batches = []
    for date_str in sorted(samples_by_date.keys(), reverse=True):
        date_samples = sorted(
            samples_by_date[date_str],
            key=lambda item: (
                item.get("_signal_score", 0),
                item.get("prompt_count", 0),
                item.get("context", ""),
            ),
            reverse=True,
        )
        for start in range(0, len(date_samples), LEARNING_WINDOW_BATCH_SIZE):
            chunk = date_samples[start : start + LEARNING_WINDOW_BATCH_SIZE]
            batch_number = start // LEARNING_WINDOW_BATCH_SIZE + 1
            batches.append(
                summarize_window_learning_batch(
                    "{}#{}".format(date_str, batch_number),
                    date_str,
                    chunk,
                )
            )
    return batches


def recent_window_learning_fingerprint(date_str, lookback_days, language=None):
    target_date_obj = parse_summary_date(date_str)
    if target_date_obj is None or lookback_days <= 0:
        return ""

    source_files = []
    for offset in range(1, lookback_days + 1):
        candidate_date = (target_date_obj - timedelta(days=offset)).isoformat()
        raw_path = RAW_DIR / "daily" / "{}.json".format(candidate_date)
        summary_path = CONSOLIDATED_DIR / candidate_date / "summary.json"
        source_files.append(
            {
                "date": candidate_date,
                "raw_daily": file_fingerprint(raw_path),
                "summary": file_fingerprint(summary_path),
            }
        )

    payload = {
        "version": RECENT_WINDOW_LEARNING_CACHE_VERSION,
        "date": date_str,
        "lookback_days": lookback_days,
        "language": current_language(language),
        "raw_dir": str(RAW_DIR),
        "consolidated_dir": str(CONSOLIDATED_DIR),
        "codex_home": str(PATHS.codex_home),
        "state_root": str(PATHS.state_root),
        "limits": {
            "sample_limit": LEARNING_WINDOW_SAMPLE_LIMIT,
            "pattern_limit": LEARNING_WINDOW_PATTERN_LIMIT,
            "batch_size": LEARNING_WINDOW_BATCH_SIZE,
            "batch_keyword_limit": LEARNING_WINDOW_BATCH_KEYWORD_LIMIT,
            "batch_takeaway_limit": LEARNING_WINDOW_BATCH_TAKEAWAY_LIMIT,
        },
        "source_files": source_files,
    }
    return json_fingerprint(payload)


def is_valid_recent_window_learning(payload, lookback_days=None):
    if not isinstance(payload, dict):
        return False
    required_types = {
        "lookback_days": int,
        "scanned_date_count": int,
        "source_dates": list,
        "raw_window_count": int,
        "batch_size": int,
        "batch_count": int,
        "coverage": dict,
        "batch_summaries": list,
        "window_samples": list,
        "context_patterns": list,
    }
    for key, expected_type in required_types.items():
        if not isinstance(payload.get(key), expected_type):
            return False
    if not all(isinstance(date_str, str) for date_str in payload.get("source_dates", [])):
        return False
    if lookback_days is not None and payload.get("lookback_days") != lookback_days:
        return False
    if payload.get("scanned_date_count") != payload.get("lookback_days"):
        return False
    if payload.get("batch_count") != len(payload.get("batch_summaries", [])):
        return False
    if len(payload.get("source_dates", [])) > payload.get("lookback_days"):
        return False
    if len(payload.get("window_samples", [])) > min(
        payload.get("raw_window_count", 0),
        LEARNING_WINDOW_SAMPLE_LIMIT,
    ):
        return False
    if len(payload.get("context_patterns", [])) > LEARNING_WINDOW_PATTERN_LIMIT:
        return False
    coverage = payload.get("coverage", {})
    for key in (
        "scanned_date_count",
        "raw_window_count",
        "source_date_count",
        "context_count",
        "batch_size",
        "batch_count",
        "injected_window_sample_count",
        "injected_pattern_count",
    ):
        if not isinstance(coverage.get(key), int):
            return False
    if not isinstance(coverage.get("source_dates"), list):
        return False
    if not all(isinstance(date_str, str) for date_str in coverage.get("source_dates", [])):
        return False
    if payload.get("source_dates") != coverage.get("source_dates"):
        return False
    if coverage.get("scanned_date_count") != payload.get("scanned_date_count"):
        return False
    if coverage.get("raw_window_count") != payload.get("raw_window_count"):
        return False
    if coverage.get("source_date_count") != len(payload.get("source_dates", [])):
        return False
    if coverage.get("source_date_count") != len(coverage.get("source_dates", [])):
        return False
    if coverage.get("batch_size") != payload.get("batch_size"):
        return False
    if coverage.get("batch_count") != payload.get("batch_count"):
        return False
    if coverage.get("injected_window_sample_count") != len(payload.get("window_samples", [])):
        return False
    if coverage.get("injected_pattern_count") != len(payload.get("context_patterns", [])):
        return False
    if coverage.get("injected_pattern_count") > min(
        coverage.get("context_count", 0),
        LEARNING_WINDOW_PATTERN_LIMIT,
    ):
        return False
    batch_window_count = 0
    source_date_set = set(payload.get("source_dates", []))
    for batch in payload.get("batch_summaries", []):
        if not isinstance(batch, dict):
            return False
        if not isinstance(batch.get("batch_id"), str):
            return False
        if not isinstance(batch.get("date"), str):
            return False
        if batch.get("date") not in source_date_set:
            return False
        for key in ("window_count", "prompt_count", "conclusion_count"):
            if not isinstance(batch.get(key), int):
                return False
            if batch.get(key) < 0:
                return False
        if (
            batch.get("window_count", 0) < 0
            or batch.get("window_count", 0) > payload.get("batch_size", 0)
        ):
            return False
        for key in ("contexts", "top_keywords", "sample_takeaways"):
            if not isinstance(batch.get(key), list):
                return False
        batch_window_count += batch.get("window_count", 0)
    if batch_window_count != payload.get("raw_window_count"):
        return False
    for sample in payload.get("window_samples", []):
        if not isinstance(sample, dict):
            return False
        if sample.get("date") not in source_date_set:
            return False
        for key in ("context", "cwd", "question_summary", "main_takeaway"):
            if not isinstance(sample.get(key), str):
                return False
        for key in ("prompt_count", "conclusion_count"):
            if not isinstance(sample.get(key), int) or sample.get(key) < 0:
                return False
        if not isinstance(sample.get("keywords"), list):
            return False
    for pattern in payload.get("context_patterns", []):
        if not isinstance(pattern, dict):
            return False
        if not isinstance(pattern.get("dates"), list):
            return False
        if not set(pattern.get("dates", [])).issubset(source_date_set):
            return False
        if not isinstance(pattern.get("context"), str):
            return False
        for key in ("window_count", "prompt_count", "conclusion_count"):
            if not isinstance(pattern.get(key), int) or pattern.get(key) < 0:
                return False
        for key in ("top_keywords", "sample_takeaways"):
            if not isinstance(pattern.get(key), list):
                return False
    return True


def build_recent_window_learning(date_str, lookback_days, cache_dir=None):
    if lookback_days <= 0:
        return {}

    target_date_obj = parse_summary_date(date_str)
    if target_date_obj is None:
        return {}

    use_cache = not cache_disabled()
    fingerprint = ""
    if use_cache:
        fingerprint = recent_window_learning_fingerprint(
            date_str,
            lookback_days,
            language=LANGUAGE,
        )
    if use_cache and fingerprint in _RECENT_WINDOW_LEARNING_CACHE:
        cached_memory = _RECENT_WINDOW_LEARNING_CACHE[fingerprint]
        if (
            is_valid_recent_window_learning(cached_memory, lookback_days=lookback_days)
            and recent_window_learning_fingerprint(
                date_str,
                lookback_days,
                language=LANGUAGE,
            )
            == fingerprint
        ):
            return cached_memory
        _RECENT_WINDOW_LEARNING_CACHE.pop(fingerprint, None)

    cached = read_cached_payload(
        cache_dir,
        "recent-window-learning",
        fingerprint,
        "recent_window_learning",
    )
    if (
        is_valid_recent_window_learning(cached, lookback_days=lookback_days)
        and recent_window_learning_fingerprint(
            date_str,
            lookback_days,
            language=LANGUAGE,
        )
        == fingerprint
    ):
        remember_cached_value(_RECENT_WINDOW_LEARNING_CACHE, fingerprint, cached)
        return cached

    samples = []
    grouped = {}
    source_dates = []

    for offset in range(1, lookback_days + 1):
        candidate_date = (target_date_obj - timedelta(days=offset)).isoformat()
        raw_payload = load_raw_daily_for_date(candidate_date)
        if not raw_payload or not raw_payload.get("window_count"):
            continue

        summary_payload = load_summary_for_date(candidate_date) or {}
        summary_by_id = {
            item.get("window_id"): item
            for item in summary_payload.get("window_summaries", [])
            if item.get("window_id")
        }
        source_dates.append(candidate_date)

        for raw_window in raw_payload.get("windows", []):
            if not raw_window.get("prompt_count", 0) and not raw_window.get("conclusion_count", 0):
                continue
            summary_item = summary_by_id.get(raw_window.get("window_id"), {})
            context_label = humanize_context_label(raw_window.get("cwd", ""), language=LANGUAGE)
            question_summary = summary_item.get("question_summary") or fallback_question_summary(
                raw_window,
                language=LANGUAGE,
            )
            main_takeaway = summary_item.get("main_takeaway") or fallback_main_takeaway(
                raw_window,
                language=LANGUAGE,
            )
            keywords = summary_item.get("keywords", [])[:6]
            signal_score = raw_window.get("prompt_count", 0) * 2 + raw_window.get("conclusion_count", 0) * 3
            sample = {
                "date": candidate_date,
                "context": context_label,
                "cwd": raw_window.get("cwd", ""),
                "prompt_count": raw_window.get("prompt_count", 0),
                "conclusion_count": raw_window.get("conclusion_count", 0),
                "question_summary": clip_text(question_summary, 140),
                "main_takeaway": clip_text(main_takeaway, 180),
                "keywords": keywords,
                "_signal_score": signal_score,
            }
            samples.append(sample)

            group = grouped.setdefault(
                context_label,
                {
                    "context": context_label,
                    "window_count": 0,
                    "prompt_count": 0,
                    "conclusion_count": 0,
                    "dates": [],
                    "keyword_counter": Counter(),
                    "takeaway_samples": [],
                },
            )
            group["window_count"] += 1
            group["prompt_count"] += raw_window.get("prompt_count", 0)
            group["conclusion_count"] += raw_window.get("conclusion_count", 0)
            if candidate_date not in group["dates"]:
                group["dates"].append(candidate_date)
            group["keyword_counter"].update(keywords)
            takeaway = clip_text(main_takeaway, 120)
            if takeaway and takeaway not in group["takeaway_samples"]:
                group["takeaway_samples"].append(takeaway)

    batches = build_window_learning_batches(samples)

    samples.sort(
        key=lambda item: (
            item.get("date", ""),
            item.get("_signal_score", 0),
            item.get("context", ""),
        ),
        reverse=True,
    )
    trimmed_samples = []
    for sample in samples[:LEARNING_WINDOW_SAMPLE_LIMIT]:
        current = dict(sample)
        current.pop("_signal_score", None)
        trimmed_samples.append(current)

    patterns = []
    for group in grouped.values():
        patterns.append(
            {
                "context": group["context"],
                "window_count": group["window_count"],
                "prompt_count": group["prompt_count"],
                "conclusion_count": group["conclusion_count"],
                "dates": group["dates"][:lookback_days],
                "top_keywords": [
                    keyword
                    for keyword, _ in group["keyword_counter"].most_common(4)
                    if keyword
                ],
                "sample_takeaways": group["takeaway_samples"][:2],
            }
        )
    patterns.sort(
        key=lambda item: (
            item.get("window_count", 0),
            item.get("conclusion_count", 0),
            item.get("prompt_count", 0),
            item.get("context", ""),
        ),
        reverse=True,
    )

    context_patterns = patterns[:LEARNING_WINDOW_PATTERN_LIMIT]

    learning = {
        "lookback_days": lookback_days,
        "scanned_date_count": lookback_days,
        "source_dates": source_dates[:lookback_days],
        "raw_window_count": len(samples),
        "batch_size": LEARNING_WINDOW_BATCH_SIZE,
        "batch_count": len(batches),
        "coverage": {
            "scanned_date_count": lookback_days,
            "raw_window_count": len(samples),
            "source_date_count": len(source_dates),
            "source_dates": source_dates[:lookback_days],
            "context_count": len(grouped),
            "batch_size": LEARNING_WINDOW_BATCH_SIZE,
            "batch_count": len(batches),
            "injected_window_sample_count": len(trimmed_samples),
            "injected_pattern_count": len(context_patterns),
        },
        "batch_summaries": batches,
        "window_samples": trimmed_samples,
        "context_patterns": context_patterns,
    }
    if use_cache:
        final_fingerprint = recent_window_learning_fingerprint(
            date_str,
            lookback_days,
            language=LANGUAGE,
        )
        if final_fingerprint == fingerprint:
            remember_cached_value(_RECENT_WINDOW_LEARNING_CACHE, fingerprint, learning)
            write_cached_payload(
                cache_dir,
                "recent-window-learning",
                fingerprint,
                "recent_window_learning",
                learning,
            )
    return learning


def load_jsonl(path):
    rows = []
    if not path.exists():
        return rows
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def summary_memory_counts(summary):
    return {
        "durable": len(summary.get("durable_memories", [])),
        "session": len(summary.get("session_memories", [])),
        "low_priority": len(summary.get("low_priority_memories", [])),
    }


def is_sparse_memory_summary(summary, raw_payload):
    counts = summary_memory_counts(summary)
    rich_signal = (
        raw_payload.get("window_count", 0) >= SPARSE_WINDOW_THRESHOLD
        or raw_payload.get("prompt_count", 0) >= SPARSE_PROMPT_THRESHOLD
        or raw_payload.get("conclusion_count", 0) >= SPARSE_CONCLUSION_THRESHOLD
    )
    return rich_signal and counts["durable"] == 0 and counts["session"] == 0


def compute_summary_quality(summary, raw_payload):
    counts = summary_memory_counts(summary)
    score = (
        counts["durable"] * 28
        + counts["session"] * 16
        + counts["low_priority"] * 6
    )
    score += min(len(summary.get("keywords", [])), 8) * 2
    score += min(len(summary.get("next_actions", [])), 5) * 2
    informative_windows = sum(
        1
        for item in summary.get("window_summaries", [])
        if item.get("main_takeaway") or item.get("keywords")
    )
    score += min(informative_windows, raw_payload.get("window_count", 0))
    stage = summary.get("stage", "")
    score += STAGE_PRIORITY.get(stage, 0) * 3

    reasons = []
    if counts["durable"]:
        reasons.append("has_durable_memories")
    if counts["session"]:
        reasons.append("has_session_memories")
    if counts["low_priority"]:
        reasons.append("has_low_priority_memories")
    if summary.get("keywords"):
        reasons.append("has_keywords")
    if summary.get("next_actions"):
        reasons.append("has_next_actions")

    day_summary = summary.get("day_summary", "")
    if (
        "保底摘要" in day_summary
        or "模型归纳阶段失败" in day_summary
        or "fallback summary" in day_summary.lower()
        or "model summarization failed" in day_summary.lower()
    ):
        score -= 60
        reasons.append("fallback_like_summary")
    if is_sparse_memory_summary(summary, raw_payload):
        score -= 60
        reasons.append("rich_input_but_no_durable_or_session")
    if raw_payload.get("window_count", 0) and not summary.get("window_summaries"):
        score -= 25
        reasons.append("missing_window_summaries")
    if raw_payload.get("window_count", 0) and not summary.get("day_summary", "").strip():
        score -= 10
        reasons.append("missing_day_summary")

    return {
        "score": score,
        "counts": counts,
        "stage": stage or "manual",
        "is_sparse": is_sparse_memory_summary(summary, raw_payload),
        "reason_codes": reasons,
    }


def sort_key_for_summary(summary):
    summary_date = parse_summary_date(summary.get("date")) or datetime.min.date()
    generated = summary.get("generated_at", "")
    stage_rank = STAGE_PRIORITY.get(summary.get("stage", ""), 0)
    return (summary_date.isoformat(), stage_rank, generated)


def choose_preferred_summary(existing_summary, candidate_summary, raw_payload):
    candidate_quality = compute_summary_quality(candidate_summary, raw_payload)
    if not existing_summary:
        return candidate_summary, {
            "decision": "accept_candidate",
            "reason": "no_existing_summary",
            "candidate_quality": candidate_quality,
            "selected_quality": candidate_quality,
        }

    existing_quality = compute_summary_quality(existing_summary, raw_payload)
    candidate_primary = (
        candidate_quality["counts"]["durable"] + candidate_quality["counts"]["session"]
    )
    existing_primary = (
        existing_quality["counts"]["durable"] + existing_quality["counts"]["session"]
    )
    candidate_stage_rank = STAGE_PRIORITY.get(candidate_quality["stage"], 0)
    existing_stage_rank = STAGE_PRIORITY.get(existing_quality["stage"], 0)
    candidate_substantive_score = candidate_quality["score"] - candidate_stage_rank * 3
    existing_substantive_score = existing_quality["score"] - existing_stage_rank * 3

    if candidate_quality["score"] > existing_quality["score"] + QUALITY_REPLACE_THRESHOLD:
        reason = "candidate_quality_higher"
        chosen = candidate_summary
    elif (
        candidate_quality["score"] >= existing_quality["score"]
        and candidate_primary > existing_primary
        and not candidate_quality["is_sparse"]
    ):
        reason = "candidate_has_more_primary_memories"
        chosen = candidate_summary
    elif (
        candidate_stage_rank > existing_stage_rank
        and candidate_substantive_score >= existing_substantive_score
        and candidate_primary >= existing_primary
        and not candidate_quality["is_sparse"]
    ):
        reason = "candidate_has_stronger_stage_without_quality_regression"
        chosen = candidate_summary
    elif candidate_quality["score"] == existing_quality["score"]:
        if candidate_primary > existing_primary and not candidate_quality["is_sparse"]:
            reason = "candidate_has_more_primary_memories_on_tie"
            chosen = candidate_summary
        elif (
            candidate_primary == existing_primary
            and candidate_quality["counts"] == existing_quality["counts"]
            and not candidate_quality["is_sparse"]
            and candidate_stage_rank > existing_stage_rank
        ):
            reason = "candidate_has_stronger_stage_on_equal_composition"
            chosen = candidate_summary
        else:
            reason = "keep_existing_equal_quality"
            chosen = existing_summary
    else:
        reason = "keep_existing_quality_gate"
        chosen = existing_summary

    selected_quality = candidate_quality if chosen is candidate_summary else existing_quality
    decision = {
        "decision": "accept_candidate" if chosen is candidate_summary else "keep_existing",
        "reason": reason,
        "candidate_quality": candidate_quality,
        "selected_quality": selected_quality,
    }
    if chosen is existing_summary:
        decision["existing_quality"] = existing_quality
    return chosen, decision


def summarize_learning_reference(summary):
    if not summary:
        return {}
    counts = summary_memory_counts(summary)
    return {
        "date": summary.get("date", ""),
        "stage": summary.get("stage", ""),
        "memory_counts": counts,
        "durable_titles": [item.get("title", "") for item in summary.get("durable_memories", [])[:6]],
        "session_titles": [item.get("title", "") for item in summary.get("session_memories", [])[:6]],
    }


def load_recent_memory_samples(target_date):
    path = REGISTRY_DIR / "memory_items.jsonl"
    target_date_obj = parse_summary_date(target_date)
    samples = []
    seen = set()
    for item in reversed(load_jsonl(path)):
        if item.get("source") != "nightly_codex":
            continue
        if item.get("bucket") not in {"durable", "session"}:
            continue
        item_date = parse_summary_date(item.get("date"))
        if target_date_obj and item_date and item_date >= target_date_obj:
            continue
        dedupe_key = (item.get("bucket"), item.get("title", ""))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        samples.append(
            {
                "date": item.get("date", ""),
                "bucket": item.get("bucket", ""),
                "title": item.get("title", ""),
                "value_note": item.get("value_note", ""),
                "keywords": item.get("keywords", [])[:6],
            }
        )
        if len(samples) >= LEARNING_MEMORY_SAMPLE_LIMIT:
            break
    return samples


def load_recent_summary_samples(target_date):
    target_date_obj = parse_summary_date(target_date)
    samples = []
    for path in sorted(CONSOLIDATED_DIR.glob("*/summary.json"), reverse=True):
        payload = load_json(path)
        payload_date = parse_summary_date(payload.get("date"))
        if target_date_obj and payload_date and payload_date >= target_date_obj:
            continue
        if not any(summary_memory_counts(payload).values()):
            continue
        samples.append(summarize_learning_reference(payload))
        if len(samples) >= LEARNING_SUMMARY_SAMPLE_LIMIT:
            break
    return samples


def load_recent_quality_lessons(target_date):
    path = REGISTRY_DIR / "nightly_learning_journal.jsonl"
    target_date_obj = parse_summary_date(target_date)
    lessons = []
    for item in reversed(load_jsonl(path)):
        item_date = parse_summary_date(item.get("date"))
        if target_date_obj and item_date and item_date >= target_date_obj:
            continue
        if item.get("decision") != "keep_existing":
            continue
        lessons.append(
            {
                "date": item.get("date", ""),
                "stage": item.get("stage", ""),
                "reason": item.get("reason", ""),
                "candidate_score": item.get("candidate_quality", {}).get("score", 0),
                "selected_score": item.get("selected_quality", {}).get("score", 0),
            }
        )
        if len(lessons) >= LEARNING_JOURNAL_SAMPLE_LIMIT:
            break
    return lessons


def build_learning_context(date_str, existing_summary, learn_window_days=0, cache_dir=None):
    if not PERSONAL_MEMORY_ENABLED:
        context = {
            "same_date_reference": None,
            "recent_memory_samples": [],
            "recent_summary_samples": [],
            "recent_quality_lessons": [],
            "memory_mode": MEMORY_MODE,
        }
        if learn_window_days > 0:
            context["recent_window_learning"] = build_recent_window_learning(
                date_str,
                learn_window_days,
                cache_dir=cache_dir,
            )
        return context

    context = {
        "same_date_reference": summarize_learning_reference(existing_summary),
        "recent_memory_samples": load_recent_memory_samples(date_str),
        "recent_summary_samples": load_recent_summary_samples(date_str),
        "recent_quality_lessons": load_recent_quality_lessons(date_str),
        "memory_mode": MEMORY_MODE,
    }
    if learn_window_days > 0:
        context["recent_window_learning"] = build_recent_window_learning(
            date_str,
            learn_window_days,
            cache_dir=cache_dir,
        )
    return context


def build_learning_context_digest(learning_context, learn_window_days):
    recent_window_learning = learning_context.get("recent_window_learning", {})
    coverage = recent_window_learning.get("coverage", {})
    window_samples = recent_window_learning.get("window_samples", [])
    context_patterns = recent_window_learning.get("context_patterns", [])
    batch_summaries = recent_window_learning.get("batch_summaries", [])

    return {
        "same_date_reference": bool(learning_context.get("same_date_reference")),
        "recent_memory_samples": len(learning_context.get("recent_memory_samples", [])),
        "recent_summary_samples": len(learning_context.get("recent_summary_samples", [])),
        "recent_quality_lessons": len(learning_context.get("recent_quality_lessons", [])),
        "recent_window_learning_days": learn_window_days,
        "recent_window_learning_scanned_days": coverage.get(
            "scanned_date_count",
            recent_window_learning.get("scanned_date_count", learn_window_days),
        ),
        "recent_window_learning_source_dates": coverage.get(
            "source_date_count",
            len(recent_window_learning.get("source_dates", [])),
        ),
        "recent_window_learning_windows": coverage.get(
            "raw_window_count",
            recent_window_learning.get("raw_window_count", 0),
        ),
        "recent_window_learning_batches": coverage.get(
            "batch_count",
            recent_window_learning.get("batch_count", len(batch_summaries)),
        ),
        "recent_window_learning_batch_size": coverage.get(
            "batch_size",
            recent_window_learning.get("batch_size", LEARNING_WINDOW_BATCH_SIZE),
        ),
        "recent_window_learning_samples": len(window_samples),
        "recent_window_learning_patterns": len(context_patterns),
    }


def build_learning_input_fingerprint(
    raw_payload,
    learning_context,
    learn_window_days,
    language=None,
    compact_payload=None,
):
    if compact_payload is None:
        compact_payload = build_compact_payload(raw_payload, language=language)
    fingerprint_learning_context = dict(learning_context or {})
    fingerprint_learning_context["same_date_reference"] = None
    payload = {
        "version": 1,
        "language": current_language(language),
        "memory_mode": MEMORY_MODE,
        "personal_memory_enabled": PERSONAL_MEMORY_ENABLED,
        "codex_model": CODEX_MODEL,
        "learn_window_days": max(learn_window_days, 0),
        "daily_compact_payload": compact_payload,
        "learning_context": fingerprint_learning_context,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def summary_matches_learning_input(summary, fingerprint):
    if not summary or not fingerprint:
        return False
    return summary.get("learning_input_fingerprint") == fingerprint


def persist_summary_run(summary_dir, summary, stage, label, language=None):
    runs_dir = summary_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    stem = "{}-{}-{}".format(timestamp, stage, label)
    json_path = runs_dir / "{}.json".format(stem)
    md_path = runs_dir / "{}.md".format(stem)
    write_json(json_path, summary)
    atomic_write_text(md_path, render_markdown(summary, language=language))
    return {"json_path": str(json_path), "md_path": str(md_path)}


def append_learning_journal(entry):
    journal_path = REGISTRY_DIR / "nightly_learning_journal.jsonl"
    existing = []
    if journal_path.exists():
        existing.append(journal_path.read_text(encoding="utf-8").rstrip())
    existing.append(json.dumps(entry, ensure_ascii=False))
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(journal_path, "\n".join(row for row in existing if row) + "\n")


def build_safe_consolidation_prompt(prompt, language=None):
    safety_preamble = localized(
        (
            "这是一个纯整理任务，不是软件工程任务。"
            "禁止调用 shell、web、MCP、apply_patch 或读取任何额外文件。"
            "不要探索环境；唯一合法输入就是下方 learning_context_json 和 daily_compact_json。"
            "直接输出符合 schema 的 JSON。\n\n"
        ),
        (
            "This is an organization-only task, not a software engineering task. "
            "Do not call shell, web, MCP, apply_patch, or read any extra files. "
            "Do not explore the environment; the only valid inputs are the learning_context_json "
            "and daily_compact_json below. "
            "Output only JSON that satisfies the schema.\n\n"
        ),
        language,
    )
    return safety_preamble + prompt


def run_codex_consolidation(prompt, output_path, language=None):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    sync_codex_exec_home(MAIN_CODEX_HOME, NIGHTLY_CODEX_HOME)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(NIGHTLY_CODEX_HOME)
    cmd = [
        CODEX_BIN,
        "exec",
        "--skip-git-repo-check",
        "--cd",
        str(RUNTIME_DIR),
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
        str(SCHEMA_PATH),
        "--output-last-message",
        str(output_path),
        "-",
    ]
    safe_prompt = build_safe_consolidation_prompt(prompt, language=language)
    result = subprocess.run(
        cmd,
        input=safe_prompt,
        text=True,
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        raise CodexConsolidationError(result.returncode, result.stdout, result.stderr)


def fallback_question_summary(window, language=None):
    if window["prompt_count"] == 0:
        return localized("当日没有用户问题。", "No user questions were captured for the day.", language)
    if "prompt_samples" in window and window["prompt_samples"]:
        return clip_text(window["prompt_samples"][0], 120)
    if "prompts" in window and window["prompts"]:
        return clip_text(window["prompts"][0].get("text", ""), 120)
    return localized(
        "当日有用户问题，但当前没有可展示的样本。",
        "User questions were captured, but no displayable sample is available.",
        language,
    )


def fallback_main_takeaway(window, language=None):
    if window["conclusion_count"] > 0:
        if "conclusion_samples" in window and window["conclusion_samples"]:
            return clip_text(window["conclusion_samples"][-1], 160)
        if "conclusions" in window and window["conclusions"]:
            return clip_text(window["conclusions"][-1].get("text", ""), 160)
    if window.get("review_like_window"):
        return localized(
            "该窗口的问题已记录，但结论按 review 窗口策略未进入原始库。",
            "This window's questions were recorded, but its conclusions were excluded by the review-like window policy.",
            language,
        )
    return localized(
        "当日没有保留到可复用结论。",
        "No reusable conclusions were retained for the day.",
        language,
    )


def normalize_summary_pairs(raw_pairs, question_summary="", main_takeaway=""):
    pairs = []
    if isinstance(raw_pairs, list):
        for raw_pair in raw_pairs:
            if not isinstance(raw_pair, dict):
                continue
            question = clip_text(raw_pair.get("question", "") or raw_pair.get("problem", ""), 180)
            conclusion = clip_text(raw_pair.get("conclusion", "") or raw_pair.get("takeaway", ""), 220)
            if question or conclusion:
                pairs.append({"question": question, "conclusion": conclusion})
    if pairs:
        return pairs[:6]
    question_summary = clip_text(question_summary, 180)
    main_takeaway = clip_text(main_takeaway, 220)
    if question_summary or main_takeaway:
        return [{"question": question_summary, "conclusion": main_takeaway}]
    return []


def fallback_window_title(window, question_summary="", language=None):
    return clip_text(
        question_summary or fallback_question_summary(window, language=language),
        100,
    )


def normalize_summary(raw_payload, summary, language=None):
    raw_windows = raw_payload["windows"]
    raw_by_id = {window["window_id"]: window for window in raw_windows}
    provided = {
        item.get("window_id"): item
        for item in summary.get("window_summaries", [])
        if item.get("window_id") in raw_by_id
    }

    normalized_windows = []
    for raw_window in raw_windows:
        current = provided.get(raw_window["window_id"], {})
        question_summary = current.get(
            "question_summary",
            fallback_question_summary(raw_window, language=language),
        )
        main_takeaway = current.get(
            "main_takeaway",
            fallback_main_takeaway(raw_window, language=language),
        )
        window_title = current.get("window_title") or fallback_window_title(
            raw_window,
            question_summary=question_summary,
            language=language,
        )
        normalized_windows.append(
            {
                "window_id": raw_window["window_id"],
                "cwd": raw_window["cwd"],
                "window_title": clip_text(window_title, 100),
                "question_summary": question_summary,
                "question_count": raw_window["prompt_count"],
                "conclusion_count": raw_window["conclusion_count"],
                "keywords": current.get("keywords", [])[:8],
                "main_takeaway": main_takeaway,
                "summary_pairs": normalize_summary_pairs(
                    current.get("summary_pairs", []),
                    question_summary=question_summary,
                    main_takeaway=main_takeaway,
                ),
            }
        )

    valid_window_ids = set(raw_by_id)

    def normalize_memory_items(items):
        normalized = []
        for item in items:
            valid_sources = [window_id for window_id in item.get("source_window_ids", []) if window_id in valid_window_ids]
            if not valid_sources:
                continue
            normalized.append(
                {
                    "title": item.get("title", ""),
                    "memory_type": item.get("memory_type", "semantic"),
                    "priority": item.get("priority", "medium"),
                    "value_note": item.get("value_note", ""),
                    "source_window_ids": valid_sources,
                    "keywords": item.get("keywords", [])[:8],
                }
            )
        return normalized

    summary["window_summaries"] = normalized_windows
    summary["durable_memories"] = normalize_memory_items(summary.get("durable_memories", []))
    summary["session_memories"] = normalize_memory_items(summary.get("session_memories", []))
    summary["low_priority_memories"] = normalize_memory_items(summary.get("low_priority_memories", []))
    summary["raw_window_count"] = raw_payload["window_count"]
    summary["review_like_window_count"] = raw_payload.get("review_like_window_count", 0)
    return summary


def build_fallback_summary(raw_payload, language=None, model_error=None, model_exit_code=None):
    window_summaries = []
    for raw_window in raw_payload["windows"]:
        question_summary = fallback_question_summary(raw_window, language=language)
        main_takeaway = fallback_main_takeaway(raw_window, language=language)
        window_summaries.append(
            {
                "window_id": raw_window["window_id"],
                "cwd": raw_window["cwd"],
                "window_title": fallback_window_title(
                    raw_window,
                    question_summary=question_summary,
                    language=language,
                ),
                "question_summary": question_summary,
                "question_count": raw_window["prompt_count"],
                "conclusion_count": raw_window["conclusion_count"],
                "keywords": [],
                "main_takeaway": main_takeaway,
                "summary_pairs": normalize_summary_pairs(
                    [],
                    question_summary=question_summary,
                    main_takeaway=main_takeaway,
                ),
            }
        )

    summary = {
        "date": raw_payload["date"],
        "day_summary": localized(
            "夜间整理在模型归纳阶段失败，当前结果是基于原始库生成的保底摘要。",
            "Nightly organization failed during model summarization; this is a fallback summary generated from the raw capture.",
            language,
        ),
        "window_summaries": window_summaries,
        "durable_memories": [],
        "session_memories": [],
        "low_priority_memories": [],
        "keywords": [],
        "next_actions": [],
        "raw_window_count": raw_payload["window_count"],
        "review_like_window_count": raw_payload.get("review_like_window_count", 0),
        "model_status": "failed",
    }
    if model_error:
        summary["model_error"] = model_error
        summary["model_error_hint"] = codex_failure_hint(model_error, language=language)
    if model_exit_code is not None:
        summary["model_exit_code"] = model_exit_code
    return summary


def render_markdown(summary, language=None):
    language = current_language(language or summary.get("language"))
    learning_digest = summary.get("learning_context_digest", {})
    if language == "en":
        lines = [
            "# Nightly Organization Result",
            "",
            "Date: `{}`".format(summary["date"]),
            "Stage: `{}`".format(summary.get("stage", "manual")),
        ]
        if learning_digest.get("recent_window_learning_days", 0):
            historical_window_count = learning_digest.get("recent_window_learning_windows", 0)
            batch_count = learning_digest.get("recent_window_learning_batches", 0)
            lines.append(
                "Window learning: previous `{}` days, scanned `{}` days, source dates `{}` days, full historical windows `{}`, batches `{}`, injected samples `{}`, patterns `{}`".format(
                    learning_digest.get("recent_window_learning_days", 0),
                    learning_digest.get("recent_window_learning_scanned_days", 0),
                    learning_digest.get("recent_window_learning_source_dates", 0),
                    historical_window_count,
                    batch_count,
                    learning_digest.get("recent_window_learning_samples", 0),
                    learning_digest.get("recent_window_learning_patterns", 0),
                )
            )
        lines.extend(
            [
                "",
                "## Daily Summary",
                "",
                summary["day_summary"],
                "",
                "## Window Overview",
                "",
                "| Window | CWD | Questions | Conclusions | Summary |",
                "| --- | --- | --- | --- | --- |",
            ]
        )

        for item in summary["window_summaries"]:
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    item["window_id"][:8],
                    item["cwd"],
                    item["question_count"],
                    item["conclusion_count"],
                    item["main_takeaway"].replace("|", "/"),
                )
            )

        def extend_memory_section(title, rows):
            lines.extend(["", "## {}".format(title), ""])
            if not rows:
                lines.append("None.")
                return
            lines.extend(
                [
                    "| Title | Type | Priority | Note | Source Windows |",
                    "| --- | --- | --- | --- | --- |",
                ]
            )
            for row in rows:
                lines.append(
                    "| {} | {} | {} | {} | {} |".format(
                        row["title"],
                        row["memory_type"],
                        row["priority"],
                        row["value_note"].replace("|", "/"),
                        " / ".join(window_id[:8] for window_id in row["source_window_ids"]),
                    )
                )

        extend_memory_section("Long-term Reusable Memories", summary["durable_memories"])
        extend_memory_section("Short-term Work Memories", summary["session_memories"])
        extend_memory_section("Low-priority Memories", summary["low_priority_memories"])

        lines.extend(["", "## Keywords", "", ", ".join(summary["keywords"]) if summary["keywords"] else "None"])
        lines.extend(["", "## Next Steps", ""])
        lines.extend("- {}".format(item) for item in summary["next_actions"])
        return "\n".join(lines) + "\n"

    lines = [
        "# 夜间整理结果",
        "",
        "日期：`{}`".format(summary["date"]),
        "阶段：`{}`".format(summary.get("stage", "manual")),
    ]
    if learning_digest.get("recent_window_learning_days", 0):
        historical_window_count = learning_digest.get("recent_window_learning_windows", 0)
        batch_count = learning_digest.get("recent_window_learning_batches", 0)
        lines.append(
            "窗口学习：近 `{}` 天，扫描 `{}` 天，有窗口日期 `{}` 天，全量历史窗口 `{}` 个，批次 `{}` 个，注入样本 `{}` 个，模式 `{}` 组".format(
                learning_digest.get("recent_window_learning_days", 0),
                learning_digest.get("recent_window_learning_scanned_days", 0),
                learning_digest.get("recent_window_learning_source_dates", 0),
                historical_window_count,
                batch_count,
                learning_digest.get("recent_window_learning_samples", 0),
                learning_digest.get("recent_window_learning_patterns", 0),
            )
        )
    lines.extend(
        [
            "",
            "## 当日摘要",
            "",
            summary["day_summary"],
            "",
            "## 窗口概览",
            "",
            "| 窗口 | 工作目录 | 问题数 | 结论数 | 小结 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )

    for item in summary["window_summaries"]:
        lines.append(
            "| {} | {} | {} | {} | {} |".format(
                item["window_id"][:8],
                item["cwd"],
                item["question_count"],
                item["conclusion_count"],
                item["main_takeaway"].replace("|", "/"),
            )
        )

    def extend_memory_section(title, rows):
        lines.extend(["", "## {}".format(title), ""])
        if not rows:
            lines.append("暂无。")
            return
        lines.extend(
            [
                "| 标题 | 类型 | 优先级 | 说明 | 来源窗口 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in rows:
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    row["title"],
                    row["memory_type"],
                    row["priority"],
                    row["value_note"].replace("|", "/"),
                    " / ".join(window_id[:8] for window_id in row["source_window_ids"]),
                )
            )

    extend_memory_section("长期可复用记忆", summary["durable_memories"])
    extend_memory_section("短期工作记忆", summary["session_memories"])
    extend_memory_section("低优先级记忆", summary["low_priority_memories"])

    lines.extend(["", "## 关键词", "", "、".join(summary["keywords"]) if summary["keywords"] else "暂无"])
    lines.extend(["", "## 下一步", ""])
    lines.extend("- {}".format(item) for item in summary["next_actions"])
    return "\n".join(lines) + "\n"


def upsert_memory_items(date_str, summary):
    if not PERSONAL_MEMORY_ENABLED:
        return

    memory_path = REGISTRY_DIR / "memory_items.jsonl"
    existing = []
    if memory_path.exists():
        for raw_line in memory_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            item = json.loads(line)
            if item.get("date") == date_str and item.get("source") == "nightly_codex":
                continue
            existing.append(item)

    def rows_for(bucket_name, items):
        rows = []
        for item in items:
            rows.append(
                {
                    "date": date_str,
                    "language": summary.get("language", current_language()),
                    "source": "nightly_codex",
                    "bucket": bucket_name,
                    "title": item["title"],
                    "memory_type": item["memory_type"],
                    "priority": item["priority"],
                    "value_note": item["value_note"],
                    "source_window_ids": item["source_window_ids"],
                    "keywords": item["keywords"],
                }
            )
        return rows

    all_rows = (
        existing
        + rows_for("durable", summary["durable_memories"])
        + rows_for("session", summary["session_memories"])
        + rows_for("low_priority", summary["low_priority_memories"])
    )
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        memory_path,
        "\n".join(json.dumps(item, ensure_ascii=False) for item in all_rows) + "\n",
    )


def apply_memory_mode(summary):
    summary["memory_mode"] = MEMORY_MODE
    summary["personal_memory_enabled"] = PERSONAL_MEMORY_ENABLED
    if PERSONAL_MEMORY_ENABLED:
        return summary

    summary["durable_memories"] = []
    summary["session_memories"] = []
    summary["low_priority_memories"] = []
    return summary


def main():
    ensure_state_layout(PATHS)
    args = parse_args()
    date_str = args.date
    stage = args.stage
    language = current_language()
    learn_window_days = max(args.learn_window_days, 0)
    raw_path = RAW_DIR / "daily" / "{}.json".format(date_str)
    if not raw_path.exists():
        raise SystemExit("missing raw daily file: {}".format(raw_path))

    raw_payload = load_json(raw_path)
    summary_dir = CONSOLIDATED_DIR / date_str
    summary_dir.mkdir(parents=True, exist_ok=True)
    output_json_path = summary_dir / "summary.json"
    existing_summary = load_json(output_json_path) if output_json_path.exists() else None
    if existing_summary:
        existing_language = normalize_language(existing_summary.get("language") or "zh")
        if existing_language != language:
            existing_summary = None
    cache_dir = default_cache_dir()
    compact_payload = build_compact_payload(
        raw_payload,
        language=language,
        cache_dir=cache_dir,
    )
    learning_context = build_learning_context(
        date_str,
        existing_summary,
        learn_window_days=learn_window_days,
        cache_dir=cache_dir,
    )
    learning_input_fingerprint = build_learning_input_fingerprint(
        raw_payload,
        learning_context,
        learn_window_days,
        language=language,
        compact_payload=compact_payload,
    )
    if args.skip_if_unchanged and summary_matches_learning_input(
        existing_summary,
        learning_input_fingerprint,
    ):
        print(
            "nightly_consolidate: unchanged input fingerprint for {}; skip model consolidation.".format(
                date_str
            )
        )
        return

    if raw_payload["window_count"] == 0:
        empty_summary = {
            "date": date_str,
            "language": language,
            "stage": stage,
            "codex_model": CODEX_MODEL,
            "day_summary": localized(
                "当日没有可整理的主窗口内容。",
                "No main-window content was available to organize for the day.",
                language,
            ),
            "window_summaries": [],
            "durable_memories": [],
            "session_memories": [],
            "low_priority_memories": [],
            "keywords": [],
            "next_actions": [],
            "generated_at": datetime.now().astimezone().isoformat(),
        }
        empty_summary = apply_memory_mode(empty_summary)
        empty_summary["learning_input_fingerprint"] = learning_input_fingerprint
        empty_summary["learning_context_digest"] = build_learning_context_digest(
            learning_context,
            learn_window_days,
        )
        empty_summary["quality"] = compute_summary_quality(empty_summary, raw_payload)
        empty_summary["selection_decision"] = {
            "decision": "accept_candidate",
            "reason": "empty_raw_payload",
            "learn_window_days": learn_window_days,
        }
        persist_summary_run(summary_dir, empty_summary, stage, "candidate", language=language)
        write_json(output_json_path, empty_summary)
        atomic_write_text(summary_dir / "summary.md", render_markdown(empty_summary, language=language))
        upsert_memory_items(date_str, empty_summary)
        append_learning_journal(
            {
                "date": date_str,
                "stage": stage,
                "decision": "accept_candidate",
                "reason": "empty_raw_payload",
                "candidate_quality": empty_summary["quality"],
                "selected_quality": empty_summary["quality"],
                "raw_window_count": raw_payload["window_count"],
                "codex_model": CODEX_MODEL,
                "learn_window_days": learn_window_days,
            }
        )
        return

    prompt = build_prompt_with_learning(
        raw_payload,
        learning_context,
        language=language,
        compact_payload=compact_payload,
    )

    try:
        run_codex_consolidation(prompt, output_json_path, language=language)
        candidate_summary = load_json(output_json_path)
        candidate_summary = normalize_summary(raw_payload, candidate_summary, language=language)
        candidate_summary["model_status"] = "completed"
    except (CodexConsolidationError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, CodexConsolidationError):
            model_error = str(exc)
            model_exit_code = exc.returncode
        else:
            model_error = describe_codex_failure(getattr(exc, "output", ""), getattr(exc, "stderr", ""), exc.returncode)
            model_exit_code = exc.returncode
        print(
            localized(
                "nightly_consolidate: 模型归纳失败，已生成保底摘要。{}".format(
                    codex_failure_hint(model_error, language=language)
                ),
                "nightly_consolidate: model summarization failed; generated fallback summary. {}".format(
                    codex_failure_hint(model_error, language=language)
                ),
                language,
            ),
            file=sys.stderr,
        )
        candidate_summary = build_fallback_summary(
            raw_payload,
            language=language,
            model_error=model_error,
            model_exit_code=model_exit_code,
        )
    candidate_summary["language"] = language
    candidate_summary["stage"] = stage
    candidate_summary["codex_model"] = CODEX_MODEL
    candidate_summary["generated_at"] = datetime.now().astimezone().isoformat()
    candidate_summary = apply_memory_mode(candidate_summary)
    candidate_summary["learning_input_fingerprint"] = learning_input_fingerprint
    candidate_summary["quality"] = compute_summary_quality(candidate_summary, raw_payload)
    candidate_summary["learning_context_digest"] = build_learning_context_digest(
        learning_context,
        learn_window_days,
    )
    candidate_run = persist_summary_run(summary_dir, candidate_summary, stage, "candidate", language=language)

    selected_summary, decision = choose_preferred_summary(
        existing_summary,
        candidate_summary,
        raw_payload,
    )
    if selected_summary is existing_summary:
        selected_summary = dict(existing_summary)
    else:
        selected_summary = dict(candidate_summary)

    selected_summary["language"] = language
    selected_summary["learning_input_fingerprint"] = learning_input_fingerprint
    selected_summary["quality"] = decision["selected_quality"]
    selected_summary["selection_decision"] = {
        "decision": decision["decision"],
        "reason": decision["reason"],
        "compared_at": datetime.now().astimezone().isoformat(),
        "candidate_run_json_path": candidate_run["json_path"],
        "learn_window_days": learn_window_days,
        "candidate_model_status": candidate_summary.get("model_status", "completed"),
        "codex_model": candidate_summary.get("codex_model", CODEX_MODEL),
    }
    if candidate_summary.get("model_status") == "failed":
        selected_summary["selection_decision"]["candidate_model_error"] = candidate_summary.get("model_error", "")
        selected_summary["selection_decision"]["candidate_model_error_hint"] = candidate_summary.get("model_error_hint", "")
        selected_summary["selection_decision"]["candidate_model_exit_code"] = candidate_summary.get("model_exit_code")
    selected_summary["last_run_model_status"] = candidate_summary.get("model_status", "completed")
    if candidate_summary.get("model_status") == "failed":
        selected_summary["last_run_model_error"] = candidate_summary.get("model_error", "")
        selected_summary["last_run_model_error_hint"] = candidate_summary.get("model_error_hint", "")
        selected_summary["last_run_model_exit_code"] = candidate_summary.get("model_exit_code")
    selected_summary = apply_memory_mode(selected_summary)
    write_json(output_json_path, selected_summary)
    atomic_write_text(summary_dir / "summary.md", render_markdown(selected_summary, language=language))
    upsert_memory_items(date_str, selected_summary)
    append_learning_journal(
        {
            "date": date_str,
            "stage": stage,
            "decision": decision["decision"],
            "reason": decision["reason"],
            "candidate_quality": decision["candidate_quality"],
            "selected_quality": decision["selected_quality"],
            "raw_window_count": raw_payload["window_count"],
            "codex_model": selected_summary.get("codex_model", CODEX_MODEL),
            "learn_window_days": learn_window_days,
            "candidate_run_json_path": candidate_run["json_path"],
            "selected_summary_path": str(output_json_path),
        }
    )


if __name__ == "__main__":
    main()
