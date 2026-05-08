"""Deterministic curated personal memory pack builder.

This module is intentionally side-effect free.  It compiles a review artifact
from ``registry/memory_entries.jsonl`` but does not change host-context
injection behavior.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re

from .memory_context import (
    INJECTION_GLOBAL_CONTEXT,
    INJECTION_LOCAL_ONLY,
    INJECTION_NEVER,
    INJECTION_ON_DEMAND,
    INJECTION_PROJECT_CONTEXT,
    MEMORY_SCOPE_GLOBAL,
    MEMORY_SCOPE_LOCAL,
    MEMORY_SCOPE_PROJECT,
    MEMORY_SCOPE_REPO,
    collapse_whitespace,
    host_context_injection_policy_from_record,
    memory_scope_from_record,
)


SCHEMA_VERSION = 1
DEFAULT_SOURCE_LABEL = "registry/memory_entries.jsonl"

SECTION_USER_PROFILE = "user_profile"
SECTION_STABLE_PREFERENCES = "stable_preferences"
SECTION_OPERATING_RULES = "operating_rules"
SECTION_PROJECT_PLAYBOOKS = "project_playbooks"
SECTION_TASK_GROUPS = "task_groups"
SECTION_LOCAL_VOLATILE = "local_volatile_notes"

SECTION_ORDER = (
    SECTION_USER_PROFILE,
    SECTION_STABLE_PREFERENCES,
    SECTION_OPERATING_RULES,
    SECTION_PROJECT_PLAYBOOKS,
    SECTION_TASK_GROUPS,
    SECTION_LOCAL_VOLATILE,
)

SECTION_TITLES = {
    SECTION_USER_PROFILE: "User Profile",
    SECTION_STABLE_PREFERENCES: "Stable Preferences",
    SECTION_OPERATING_RULES: "Operating Rules",
    SECTION_PROJECT_PLAYBOOKS: "Project Playbooks",
    SECTION_TASK_GROUPS: "Task Groups",
    SECTION_LOCAL_VOLATILE: "Local / Volatile Notes",
}

RULE_TYPES = {"preference", "procedural", "procedure", "rule", "workflow", "mapping"}
LOCAL_PRIVACY_PATTERN = re.compile(
    r"\b(token|cookie|secret|credential|account|private|local-only|local_only)\b"
    r"|(?:本机.{0,12}账号|私有|隐私|账号|密钥|凭证|令牌|仅保留)",
    re.IGNORECASE,
)
TIMELINE_PATTERN = re.compile(
    r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b"
    r"|\bv?\d+\.\d+\.\d+\b"
    r"|\b(today|yesterday|tomorrow|this week|released|published|shipped)\b"
    r"|(?:今天|昨天|明天|当天|本周|已发布|已完成|已落地|已合入)",
    re.IGNORECASE,
)
COMPLETED_TASK_PATTERN = re.compile(
    r"\b(released|published|shipped|merged|completed|done|landed)\b"
    r"|(?:已发布|已完成|已落地|已合入|已提交|已同步|已经)",
    re.IGNORECASE,
)
TRUNCATION_PATTERN = re.compile(r"\.\.\.|…")
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
SECRET_PATTERN = re.compile(
    r"(?i)\b(token|cookie|secret|password|passwd|credential|api[_-]?key)\b\s*[:=]\s*([^\s,;]+)"
)
LABELED_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)\b(app\s*key|api[_ -]?key|token|secret|credential)\b"
    r"([^`，,。.;；\n]{0,40})"
    r"(`?[A-Za-z0-9_-]{12,}`?)"
)
BACKTICKED_LONG_VALUE_PATTERN = re.compile(r"`[A-Za-z0-9_-]{12,}`")
PRIVATE_HOME_PATTERN = re.compile(r"/Users/[^/\s]+")
PROJECT_TERM_PATTERN = re.compile(
    r"\b(OpenRelix|Douyin|Android|Gradle|LaunchAgent|npm|GitHub Release|ASR|PCM|VERecorder)\b"
    r"|(?:抖音|长按|录制|抽帧|回溯|安装器|面板|看板)",
    re.IGNORECASE,
)


def normalize_key(text):
    compact = collapse_whitespace(text).lower()
    compact = re.sub(r"[`\"'“”‘’]+", "", compact)
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", compact)
    return compact.strip()


def text_blob(row):
    parts = [
        row.get("title", ""),
        row.get("title_zh", ""),
        row.get("title_en", ""),
        row.get("display_title", ""),
        row.get("value_note", ""),
        row.get("value_note_zh", ""),
        row.get("value_note_en", ""),
        row.get("display_value_note", ""),
    ]
    keywords = row.get("keywords") or []
    if isinstance(keywords, (list, tuple, set)):
        parts.extend(keywords)
    else:
        parts.append(keywords)
    return collapse_whitespace(" ".join(str(part or "") for part in parts))


def primary_text(row, key):
    for candidate in (
        key,
        "{}_zh".format(key),
        "{}_en".format(key),
        "display_{}".format(key),
        "display_{}_zh".format(key),
        "display_{}_en".format(key),
    ):
        value = collapse_whitespace(row.get(candidate, ""))
        if value:
            return value
    return ""


def safe_list(value):
    if isinstance(value, (list, tuple, set)):
        return [collapse_whitespace(item) for item in value if collapse_whitespace(item)]
    text = collapse_whitespace(value)
    return [text] if text else []


def unique_preserve_order(values):
    seen = set()
    result = []
    for value in values:
        text = collapse_whitespace(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def source_entry_id(row, source_label, line_number):
    for key in ("memory_id", "canonical_memory_id", "canonical_id", "memory_key", "id"):
        value = collapse_whitespace(row.get(key, ""))
        if value:
            return value
    fingerprint_src = "\n".join(
        (
            normalize_key(primary_text(row, "title")),
            normalize_key(primary_text(row, "value_note")),
            normalize_key(row.get("project_key", "")),
            normalize_key(row.get("scope", "")),
            normalize_key(row.get("injection_policy", "")),
        )
    )
    digest = hashlib.sha1(fingerprint_src.encode("utf-8")).hexdigest()[:12]
    return "{}:sha1:{}".format(source_label, digest)


def parse_registry_text(text, source_label=DEFAULT_SOURCE_LABEL):
    entries = []
    diagnostics = {"malformed_lines": []}
    for line_number, raw_line in enumerate(str(text or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            diagnostics["malformed_lines"].append(line_number)
            continue
        if not isinstance(row, dict):
            diagnostics["malformed_lines"].append(line_number)
            continue
        normalized = dict(row)
        normalized["_source_entry_id"] = source_entry_id(normalized, source_label, line_number)
        normalized["_source_line"] = line_number
        entries.append(normalized)
    return entries, diagnostics


def source_window_ids(row):
    return unique_preserve_order(safe_list(row.get("source_window_ids")) + safe_list(row.get("source_windows")))


def source_dates(row):
    return unique_preserve_order(
        safe_list(row.get("source_dates"))
        + safe_list(row.get("date"))
        + safe_list(row.get("updated_at"))
    )


def evidence_count_for_row(row):
    return max(
        1,
        safe_int(row.get("occurrence_count"), 0),
        len(source_window_ids(row)),
        len(safe_list(row.get("evidence_contexts"))),
    )


def project_label(row):
    for key in ("project_label", "project_key", "repo", "cwd_display"):
        value = collapse_whitespace(row.get(key, ""))
        if value:
            return value
    return "Unscoped Project"


def priority_rank(value):
    return {"high": 0, "medium": 1, "low": 2}.get(str(value or "").lower(), 1)


def is_local_or_privacy_like(row, scope, policy):
    if policy in {INJECTION_LOCAL_ONLY, INJECTION_NEVER}:
        return True
    if scope == MEMORY_SCOPE_LOCAL:
        return True
    if str(row.get("bucket") or "").strip() == "low_priority":
        return True
    if str(row.get("priority") or "").strip().lower() == "low":
        return True
    return bool(LOCAL_PRIVACY_PATTERN.search(text_blob(row)))


def is_timeline_like(row):
    blob = text_blob(row)
    if not blob:
        return False
    if TIMELINE_PATTERN.search(blob):
        return True
    return (
        str(row.get("memory_type") or "").strip().lower() == "task"
        and safe_int(row.get("occurrence_count"), 1) <= 1
        and bool(COMPLETED_TASK_PATTERN.search(blob))
    )


def has_truncation_marker(row):
    return bool(TRUNCATION_PATTERN.search(text_blob(row)))


def redact_text(value):
    text = EMAIL_PATTERN.sub("[redacted-email]", collapse_whitespace(value))
    text = SECRET_PATTERN.sub(lambda match: "{}=[redacted]".format(match.group(1)), text)
    text = LABELED_SECRET_VALUE_PATTERN.sub(lambda match: "{}{}[redacted]".format(match.group(1), match.group(2)), text)
    if re.search(r"(?i)\b(app\s*key|api[_ -]?key|token|secret|credential)\b|(?:密钥|令牌)", text):
        text = BACKTICKED_LONG_VALUE_PATTERN.sub("[redacted]", text)
    text = PRIVATE_HOME_PATTERN.sub("~/", text)
    return text


def sanitized_render_text(value):
    text = TRUNCATION_PATTERN.sub("", collapse_whitespace(value))
    return redact_text(text)


def possible_cross_project_leakage(row, scope, policy):
    if scope != MEMORY_SCOPE_GLOBAL and policy != INJECTION_GLOBAL_CONTEXT:
        return False
    return bool(PROJECT_TERM_PATTERN.search(text_blob(row)))


def is_user_profile_like(row):
    blob = text_blob(row)
    memory_type = str(row.get("memory_type") or "").strip().lower()
    if memory_type == "profile":
        return True
    return bool(
        re.search(
            r"\b(user profile|works across|working style)\b|(?:用户画像|工作方式|工作领域|偏好画像)",
            blob,
            re.IGNORECASE,
        )
    )


def has_rule_signal(row):
    blob = text_blob(row)
    memory_type = str(row.get("memory_type") or "").strip().lower()
    if memory_type in RULE_TYPES:
        return True
    return bool(
        re.search(
            r"\b(must|prefer|default|workflow|rule|avoid|verify|keep)\b"
            r"|(?:必须|优先|默认|不要|不能|避免|规则|流程|校验|验证|保持)",
            blob,
            re.IGNORECASE,
        )
    )


def classify_section(row):
    scope = memory_scope_from_record(row)
    policy = host_context_injection_policy_from_record(row)
    if is_local_or_privacy_like(row, scope, policy) or is_timeline_like(row):
        return SECTION_LOCAL_VOLATILE, scope, policy
    memory_type = str(row.get("memory_type") or "").strip().lower()
    if scope == MEMORY_SCOPE_GLOBAL and policy == INJECTION_GLOBAL_CONTEXT:
        if is_user_profile_like(row):
            return SECTION_USER_PROFILE, scope, policy
        if memory_type == "preference" or re.search(r"\bprefer|preference\b|(?:偏好|习惯)", text_blob(row), re.I):
            return SECTION_STABLE_PREFERENCES, scope, policy
        if has_rule_signal(row):
            return SECTION_OPERATING_RULES, scope, policy
        return SECTION_TASK_GROUPS, scope, policy
    if scope in {MEMORY_SCOPE_PROJECT, MEMORY_SCOPE_REPO} or policy == INJECTION_PROJECT_CONTEXT:
        if has_rule_signal(row):
            return SECTION_PROJECT_PLAYBOOKS, scope, policy
        return SECTION_TASK_GROUPS, scope, policy
    if policy == INJECTION_ON_DEMAND:
        return SECTION_TASK_GROUPS, scope, policy
    return SECTION_LOCAL_VOLATILE, scope, policy


def canonical_topic_key(row, section, scope, policy):
    blob = normalize_key(text_blob(row))
    compact = blob.replace(" ", "")
    if "codex_home" in blob or "codexhome" in compact:
        if re.search(r"profile|home|router|routing|gui|resume|恢复|路由|隔离|多", blob, re.I):
            return "topic:multi_codex_home_routing"
    if "worktree" in blob or "工作树" in blob:
        if re.search(r"feature|bugfix|main|origin|merge|修|开发|独立|合入|分支", blob, re.I):
            return "topic:worktree_first_delivery"
    if re.search(r"long task|长任务|耗时|backfill|回溯|首次|安装", blob, re.I):
        if re.search(r"light|quick|preliminary|deep|final|轻量|快速|深度|完整", blob, re.I):
            return "topic:long_task_light_then_deep"
    if "apply_patch" in blob:
        return "topic:apply_patch_first"
    title_key = normalize_key(primary_text(row, "title") or primary_text(row, "value_note"))
    if title_key:
        return "literal:{}:{}:{}:{}".format(section, scope, policy, title_key[:120])
    return "literal:{}:{}:{}:{}".format(section, scope, policy, row.get("_source_entry_id", "unknown"))


def token_set(text):
    normalized = normalize_key(text)
    tokens = set()
    for token in normalized.split():
        if len(token) >= 3:
            tokens.add(token[:32])
        if re.search(r"[\u4e00-\u9fff]", token):
            compact = token[:40]
            for size in (4, 3):
                if len(compact) < size:
                    continue
                for index in range(0, len(compact) - size + 1):
                    tokens.add(compact[index : index + size])
    return tokens


def text_similarity(left, right):
    left_tokens = token_set(left)
    right_tokens = token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    if overlap == 0:
        return 0.0
    return max(overlap / len(left_tokens | right_tokens), overlap / min(len(left_tokens), len(right_tokens)))


def should_merge_groups(left, right):
    if left["canonical_key"] == right["canonical_key"]:
        return True
    if left["section"] != right["section"]:
        return False
    if left.get("project_label", "") != right.get("project_label", ""):
        return False
    return text_similarity(left.get("_match_text", ""), right.get("_match_text", "")) >= 0.84


def row_to_group(row, section, scope, policy):
    title = primary_text(row, "title") or "Untitled memory"
    value_note = primary_text(row, "value_note")
    source_id = row["_source_entry_id"]
    windows = source_window_ids(row)
    dates = source_dates(row)
    diagnostics = []
    if is_timeline_like(row):
        diagnostics.append("timeline_like")
    if is_local_or_privacy_like(row, scope, policy):
        diagnostics.append("local_privacy_like")
    if possible_cross_project_leakage(row, scope, policy):
        diagnostics.append("possible_cross_project_leakage")
    if has_truncation_marker(row):
        diagnostics.append("truncation_marker")
    canonical_key = canonical_topic_key(row, section, scope, policy)
    return {
        "section": section,
        "canonical_key": canonical_key,
        "title": title,
        "value_note": value_note,
        "scope": scope,
        "injection_policy": policy,
        "project_label": project_label(row) if section == SECTION_PROJECT_PLAYBOOKS else "",
        "memory_type": str(row.get("memory_type") or "semantic"),
        "priority": str(row.get("priority") or "medium"),
        "evidence_count": evidence_count_for_row(row),
        "source_entry_ids": [source_id],
        "source_window_ids": windows,
        "source_dates": dates,
        "diagnostics": diagnostics,
        "_rank": (
            priority_rank(row.get("priority")),
            -safe_int(row.get("storage_quality_score"), 0),
            -evidence_count_for_row(row),
            source_id,
        ),
        "_match_text": "{} {}".format(title, value_note),
    }


def merge_group(target, incoming):
    target["evidence_count"] += incoming["evidence_count"]
    target["source_entry_ids"] = unique_preserve_order(target["source_entry_ids"] + incoming["source_entry_ids"])
    target["source_window_ids"] = unique_preserve_order(target["source_window_ids"] + incoming["source_window_ids"])
    target["source_dates"] = unique_preserve_order(target["source_dates"] + incoming["source_dates"])
    target["diagnostics"] = unique_preserve_order(target["diagnostics"] + incoming["diagnostics"])
    if incoming["_rank"] < target["_rank"]:
        for key in ("title", "value_note", "memory_type", "priority", "scope", "injection_policy", "project_label"):
            target[key] = incoming[key]
        target["_rank"] = incoming["_rank"]
    return target


def finalize_group(group):
    cleaned = {key: value for key, value in group.items() if not key.startswith("_")}
    cleaned["title"] = sanitized_render_text(cleaned["title"])
    cleaned["value_note"] = sanitized_render_text(cleaned["value_note"])
    for key in ("source_entry_ids", "source_window_ids", "source_dates", "diagnostics"):
        cleaned[key] = sorted(unique_preserve_order(cleaned.get(key) or []))
    return cleaned


def build_diagnostics(groups, parse_diagnostics):
    duplicate_clusters = []
    timeline_like_entries = []
    local_privacy_like_entries = []
    cross_project_entries = []
    truncation_entries = []
    for group in groups:
        source_ids = group["source_entry_ids"]
        if len(source_ids) > 1:
            duplicate_clusters.append(
                {
                    "canonical_key": group["canonical_key"],
                    "section": group["section"],
                    "evidence_count": group["evidence_count"],
                    "source_entry_ids": source_ids,
                }
            )
        if "timeline_like" in group["diagnostics"]:
            timeline_like_entries.extend(source_ids)
        if "local_privacy_like" in group["diagnostics"]:
            local_privacy_like_entries.extend(source_ids)
        if "possible_cross_project_leakage" in group["diagnostics"]:
            cross_project_entries.extend(source_ids)
        if "truncation_marker" in group["diagnostics"]:
            truncation_entries.extend(source_ids)
    return {
        "duplicate_clusters": duplicate_clusters,
        "timeline_like_entries": unique_preserve_order(timeline_like_entries),
        "local_privacy_like_entries": unique_preserve_order(local_privacy_like_entries),
        "possible_cross_project_leakage": unique_preserve_order(cross_project_entries),
        "truncation_markers": unique_preserve_order(truncation_entries),
        "malformed_lines": parse_diagnostics.get("malformed_lines", []),
    }


def build_curated_memory_pack(entries, parse_diagnostics=None):
    parse_diagnostics = parse_diagnostics or {"malformed_lines": []}
    groups = []
    for row in entries:
        section, scope, policy = classify_section(row)
        incoming = row_to_group(row, section, scope, policy)
        for group in groups:
            if should_merge_groups(group, incoming):
                merge_group(group, incoming)
                break
        else:
            groups.append(incoming)

    finalized_groups = [finalize_group(group) for group in groups]
    finalized_groups.sort(
        key=lambda item: (
            SECTION_ORDER.index(item["section"]),
            item.get("project_label", ""),
            priority_rank(item.get("priority")),
            item.get("title", ""),
            item["source_entry_ids"][0] if item.get("source_entry_ids") else "",
        )
    )
    sections = {section: [] for section in SECTION_ORDER}
    for group in finalized_groups:
        sections[group["section"]].append(group)
    if not sections[SECTION_USER_PROFILE]:
        labels = unique_preserve_order(
            [
                item.get("project_label") or item.get("project_key") or ""
                for item in finalized_groups
                if item.get("section") == SECTION_PROJECT_PLAYBOOKS
            ]
        )
        if labels:
            profile_note = (
                "Recurring work appears across {}. Use the stable preferences and operating "
                "rules before applying project-specific playbooks."
            ).format(", ".join(labels[:4]))
            sections[SECTION_USER_PROFILE].append(
                {
                    "section": SECTION_USER_PROFILE,
                    "canonical_key": "synthetic:user_profile",
                    "title": "Recurring work profile",
                    "value_note": profile_note,
                    "scope": MEMORY_SCOPE_GLOBAL,
                    "injection_policy": INJECTION_GLOBAL_CONTEXT,
                    "project_label": "",
                    "memory_type": "profile",
                    "priority": "medium",
                    "evidence_count": 0,
                    "source_entry_ids": [],
                    "source_window_ids": [],
                    "source_dates": [],
                    "diagnostics": ["synthetic"],
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": DEFAULT_SOURCE_LABEL,
        "model_calls": 0,
        "entry_count": len(entries),
        "sections": sections,
        "diagnostics": build_diagnostics(finalized_groups, parse_diagnostics),
    }


def build_curated_memory_pack_from_text(text, source_label=DEFAULT_SOURCE_LABEL):
    entries, parse_diagnostics = parse_registry_text(text, source_label=source_label)
    pack = build_curated_memory_pack(entries, parse_diagnostics=parse_diagnostics)
    pack["source"] = source_label
    return pack


def format_source_refs(item):
    refs = ["evidence {}".format(item.get("evidence_count", 0))]
    source_ids = item.get("source_entry_ids") or []
    if source_ids:
        refs.append("sources: {}".format(", ".join(source_ids)))
    windows = item.get("source_window_ids") or []
    if windows:
        refs.append("windows: {}".format(", ".join(windows)))
    return "; ".join(refs)


def render_item(item):
    title = sanitized_render_text(item.get("title", "")) or "Untitled memory"
    note = sanitized_render_text(item.get("value_note", ""))
    body = title if not note else "{} - {}".format(title, note)
    return "- {} [{}]".format(body, format_source_refs(item))


def render_markdown(pack):
    lines = [
        "# Curated Personal Memory Pack",
        "",
        "Non-invasive artifact generated from registry/memory_entries.jsonl. Model calls: 0.",
    ]
    sections = pack.get("sections", {})
    for section in SECTION_ORDER:
        lines.extend(["", "## {}".format(SECTION_TITLES[section]), ""])
        items = sections.get(section) or []
        if not items:
            lines.append("- No curated entries.")
            continue
        if section == SECTION_PROJECT_PLAYBOOKS:
            grouped = defaultdict(list)
            for item in items:
                grouped[item.get("project_label") or "Unscoped Project"].append(item)
            for label in sorted(grouped):
                lines.extend(["### {}".format(sanitized_render_text(label)), ""])
                for item in grouped[label]:
                    lines.append(render_item(item))
                lines.append("")
            if lines and lines[-1] == "":
                lines.pop()
            continue
        for item in items:
            lines.append(render_item(item))

    diagnostics = pack.get("diagnostics") or {}
    lines.extend(["", "## Quality Diagnostics", ""])
    lines.append("- Duplicate clusters: {}".format(len(diagnostics.get("duplicate_clusters") or [])))
    lines.append("- Timeline-like entries: {}".format(len(diagnostics.get("timeline_like_entries") or [])))
    lines.append("- Local/privacy-like entries: {}".format(len(diagnostics.get("local_privacy_like_entries") or [])))
    lines.append(
        "- Possible cross-project leakage: {}".format(
            len(diagnostics.get("possible_cross_project_leakage") or [])
        )
    )
    lines.append("- Truncation markers: {}".format(len(diagnostics.get("truncation_markers") or [])))
    if diagnostics.get("malformed_lines"):
        lines.append("- Malformed registry lines: {}".format(", ".join(map(str, diagnostics["malformed_lines"]))))
    return "\n".join(lines).rstrip() + "\n"
