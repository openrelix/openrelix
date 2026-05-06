"""Pure helpers for OpenRelix managed memory context policy views."""

from collections import Counter
import re


MEMORY_SCOPE_GLOBAL = "global"
MEMORY_SCOPE_DOMAIN = "domain"
MEMORY_SCOPE_PROJECT = "project"
MEMORY_SCOPE_REPO = "repo"
MEMORY_SCOPE_HOST = "host"
MEMORY_SCOPE_LOCAL = "local"

INJECTION_GLOBAL_CONTEXT = "global_context"
INJECTION_PROJECT_CONTEXT = "project_context"
INJECTION_ON_DEMAND = "on_demand"
INJECTION_LOCAL_ONLY = "local_only"
INJECTION_NEVER = "never"

MEMORY_SCOPE_ALIASES = {
    "all": MEMORY_SCOPE_GLOBAL,
    "common": MEMORY_SCOPE_GLOBAL,
    "cross-scope": MEMORY_SCOPE_GLOBAL,
    "cross_scope": MEMORY_SCOPE_GLOBAL,
    "general": MEMORY_SCOPE_GLOBAL,
    "global-context": MEMORY_SCOPE_GLOBAL,
    "global_context": MEMORY_SCOPE_GLOBAL,
    "personal": MEMORY_SCOPE_GLOBAL,
    "user": MEMORY_SCOPE_GLOBAL,
    "workspace": MEMORY_SCOPE_PROJECT,
    "worktree": MEMORY_SCOPE_PROJECT,
    "project-context": MEMORY_SCOPE_PROJECT,
    "project_context": MEMORY_SCOPE_PROJECT,
    "repository": MEMORY_SCOPE_REPO,
    "host-native": MEMORY_SCOPE_HOST,
    "host_native": MEMORY_SCOPE_HOST,
    "native": MEMORY_SCOPE_HOST,
    "private": MEMORY_SCOPE_LOCAL,
    "state-root": MEMORY_SCOPE_LOCAL,
    "state_root": MEMORY_SCOPE_LOCAL,
}

INJECTION_POLICY_ALIASES = {
    "always": INJECTION_GLOBAL_CONTEXT,
    "global": INJECTION_GLOBAL_CONTEXT,
    "global-context": INJECTION_GLOBAL_CONTEXT,
    "global_context": INJECTION_GLOBAL_CONTEXT,
    "host": INJECTION_GLOBAL_CONTEXT,
    "host-context": INJECTION_GLOBAL_CONTEXT,
    "host_context": INJECTION_GLOBAL_CONTEXT,
    "inject": INJECTION_GLOBAL_CONTEXT,
    "project": INJECTION_PROJECT_CONTEXT,
    "project-context": INJECTION_PROJECT_CONTEXT,
    "project_context": INJECTION_PROJECT_CONTEXT,
    "repo": INJECTION_PROJECT_CONTEXT,
    "repository": INJECTION_PROJECT_CONTEXT,
    "workspace": INJECTION_PROJECT_CONTEXT,
    "demand": INJECTION_ON_DEMAND,
    "on-demand": INJECTION_ON_DEMAND,
    "on_demand": INJECTION_ON_DEMAND,
    "search": INJECTION_ON_DEMAND,
    "retrieval": INJECTION_ON_DEMAND,
    "local": INJECTION_LOCAL_ONLY,
    "local-only": INJECTION_LOCAL_ONLY,
    "local_only": INJECTION_LOCAL_ONLY,
    "off": INJECTION_NEVER,
    "never": INJECTION_NEVER,
    "none": INJECTION_NEVER,
}

INJECTION_POLICY_ORDER = (
    INJECTION_GLOBAL_CONTEXT,
    INJECTION_PROJECT_CONTEXT,
    INJECTION_ON_DEMAND,
    INJECTION_LOCAL_ONLY,
    INJECTION_NEVER,
)

POLICY_LABELS = {
    INJECTION_GLOBAL_CONTEXT: ("全局上下文", "Global Context"),
    INJECTION_PROJECT_CONTEXT: ("项目上下文", "Project Context"),
    INJECTION_ON_DEMAND: ("按需召回", "On-demand Recall"),
    INJECTION_LOCAL_ONLY: ("本地保留", "Local Only"),
    INJECTION_NEVER: ("禁止注入", "Never Inject"),
}

SCOPE_LABELS = {
    MEMORY_SCOPE_GLOBAL: ("通用", "Global"),
    MEMORY_SCOPE_DOMAIN: ("领域", "Domain"),
    MEMORY_SCOPE_PROJECT: ("项目", "Project"),
    MEMORY_SCOPE_REPO: ("仓库", "Repo"),
    MEMORY_SCOPE_HOST: ("Host", "Host"),
    MEMORY_SCOPE_LOCAL: ("本地", "Local"),
}

MEMORY_SCOPE_KEYS = (
    "scope",
    "memory_scope",
    "context_scope",
    "applicability_scope",
)

INJECTION_POLICY_KEYS = (
    "injection_policy",
    "context_policy",
    "host_context_policy",
    "injection_scope",
)

LEGACY_SYNTHESIS_SOURCES = {
    "legacy",
    "nightly_claude",
    "nightly_codex",
    "openrelix_nightly",
}

APPROVED_GLOBAL_CONTEXT_SOURCES = {
    "canonical",
    "manual",
    "openrelix",
    "user_preference",
}

GLOBAL_CONTEXT_APPROVAL_KEYS = (
    "global_context_approved",
    "host_context_approved",
    "injection_approved",
)

GLOBAL_CONTEXT_CONFIDENCE_KEYS = (
    "global_context_confidence",
    "host_context_confidence",
    "injection_confidence",
)

TRUTHY_VALUES = {"1", "true", "yes", "y", "on", "approved"}
APPROVED_CONFIDENCE_VALUES = {"approved", "canonical", "high", "manual", "trusted"}

MEMORY_HARD_NOISE_PATTERNS = (
    r"0\.2\.5\s*更新请求的重复窗口无结论",
    r"claude.*(?:未登录|问候|退出)",
    r"continue from where you left off",
    r"合影提示词",
    r"家(?:庭)?合影",
    r"多个.*窗口.*(?:未登录|问候|退出)",
    r"本地\s*tgz",
    r"测试工件",
    r"无结论",
    r"没有结论",
    r"暂无结论",
    r"未登录",
    r"问候",
    r"退出",
    r"重复窗口",
    r"\bno conclusion\b",
    r"\bnot logged in\b",
    r"\blogin only\b",
    r"\btest artifact\b",
)

MEMORY_WEAK_PATTERNS = (
    r"^\s*轻量待查",
    r"^\s*lightweight later review",
    r"只是",
    r"当天",
    r"当前任务",
    r"临时",
    r"看了",
    r"问了",
    r"给过",
    r"待查",
    r"\btemporary\b",
)

MEMORY_STRONG_SIGNAL_PATTERNS = (
    r"AGENTS\.md",
    r"apply_patch",
    r"catalog",
    r"global_context",
    r"injection_policy",
    r"state root",
    r"worktree",
    r"(?:不要|不能|必须|应该|应当|应|默认|优先|避免|先|保持|确认|校验|验证|同步|隔离|留在|适合|通过|按需|只读|保留|限制)",
    r"(?:规则|原则|策略|流程|路径|排障|映射|边界|兼容|配置|模型|索引|预算|注入|去重|召回|偏好|习惯|通用|可复用|长期)",
    r"\b(?:avoid|boundary|canonical|dedupe|default|must|prefer|preference|rule|workflow|verify)\b",
)

MEMORY_QUESTION_PATTERNS = (
    r"[?？]",
    r"(?:什么|怎么|怎样|为何|为什么|吗|呢|是否|是不是|要不要|能不能|可不可以)",
    r"(?:帮我|请|想要|想看|看下|看看)",
    r"\b(?:how|what|why|can|should|please)\b",
)


def collapse_whitespace(text):
    return " ".join(str(text or "").split()).strip()


def first_record_value(item, keys):
    if not isinstance(item, dict):
        return ""
    for key in keys:
        value = collapse_whitespace(item.get(key, ""))
        if value:
            return value
    return ""


def has_explicit_memory_scope(item):
    return bool(first_record_value(item, MEMORY_SCOPE_KEYS))


def has_explicit_injection_policy(item):
    return bool(first_record_value(item, INJECTION_POLICY_KEYS))


def has_source_window_refs(item):
    if not isinstance(item, dict):
        return False
    value = item.get("source_window_ids")
    if isinstance(value, (list, tuple)):
        return any(collapse_whitespace(part) for part in value)
    return bool(collapse_whitespace(value))


def normalize_source_name(value):
    return str(value or "").strip().lower().replace("-", "_")


def memory_source_names_from_record(item):
    if not isinstance(item, dict):
        return set()
    values = []
    for key in ("source", "source_system", "host_source"):
        value = collapse_whitespace(item.get(key, ""))
        if value:
            values.append(value)
    for key in ("source_systems", "host_sources", "source_hosts"):
        value = item.get(key)
        if isinstance(value, (list, tuple, set)):
            values.extend(part for part in value if collapse_whitespace(part))
        elif collapse_whitespace(value):
            values.append(value)
    return {normalize_source_name(value) for value in values if normalize_source_name(value)}


def record_truthy(item, keys):
    if not isinstance(item, dict):
        return False
    return any(str(item.get(key, "")).strip().lower() in TRUTHY_VALUES for key in keys)


def memory_record_has_global_context_approval(item):
    if not isinstance(item, dict):
        return False
    if record_truthy(item, GLOBAL_CONTEXT_APPROVAL_KEYS):
        return True
    for key in GLOBAL_CONTEXT_CONFIDENCE_KEYS:
        value = str(item.get(key, "")).strip().lower().replace("-", "_")
        if value in APPROVED_CONFIDENCE_VALUES:
            return True
    sources = memory_source_names_from_record(item)
    if sources & APPROVED_GLOBAL_CONTEXT_SOURCES:
        return True
    return bool(
        first_record_value(
            item,
            (
                "canonical_memory_id",
                "canonical_id",
            ),
        )
    )


def memory_record_needs_global_context_approval(item):
    return bool(memory_source_names_from_record(item) & LEGACY_SYNTHESIS_SOURCES)


def memory_record_is_low_priority(item):
    if not isinstance(item, dict):
        return False
    return str(item.get("bucket") or "").strip() == "low_priority" or str(
        item.get("priority") or ""
    ).strip().lower() == "low"


def memory_record_text_blob(item):
    if not isinstance(item, dict):
        return ""
    parts = [
        item.get("title", ""),
        item.get("title_zh", ""),
        item.get("title_en", ""),
        item.get("value_note", ""),
        item.get("value_note_zh", ""),
        item.get("value_note_en", ""),
    ]
    keywords = item.get("keywords") or []
    if isinstance(keywords, (list, tuple, set)):
        parts.extend(keywords)
    else:
        parts.append(keywords)
    return collapse_whitespace(" ".join(str(part or "") for part in parts))


def regex_any(patterns, text):
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def memory_source_window_count(item):
    if not isinstance(item, dict):
        return 0
    value = item.get("source_window_ids") or item.get("source_windows") or []
    if isinstance(value, (list, tuple, set)):
        return len([part for part in value if collapse_whitespace(part)])
    return 1 if collapse_whitespace(value) else 0


def memory_storage_quality(item, bucket=""):
    """Classify whether a generated memory should be stored, demoted, or dropped."""
    if not isinstance(item, dict):
        return {"disposition": "drop", "score": 0, "reason": "invalid"}

    bucket = str(bucket or item.get("bucket") or "").strip()
    title = collapse_whitespace(item.get("title") or item.get("display_title") or "")
    note = collapse_whitespace(item.get("value_note") or item.get("display_value_note") or "")
    blob = memory_record_text_blob(item)
    lowered_blob = blob.lower()
    if not title and not note:
        return {"disposition": "drop", "score": 0, "reason": "empty"}
    if regex_any(MEMORY_HARD_NOISE_PATTERNS, lowered_blob):
        return {"disposition": "drop", "score": 0, "reason": "hard_noise"}
    if re.search(r"^\s*(?:轻量待查|lightweight later review)", title, flags=re.IGNORECASE):
        return {"disposition": "drop", "score": 0, "reason": "lightweight_later_review"}

    score = 0
    reasons = []
    memory_type = str(item.get("memory_type") or "").strip().lower()
    priority = str(item.get("priority") or "").strip().lower()

    if len(title) >= 8:
        score += 1
    if note and note != title and len(note) >= 18:
        score += 1
    if memory_type in {"preference", "procedural", "procedure", "rule", "mapping", "workflow"}:
        score += 2
        reasons.append("type")
    elif memory_type == "semantic":
        score += 1
    elif memory_type == "task":
        score -= 1

    if priority == "high":
        score += 2
        reasons.append("priority")
    elif priority == "medium":
        score += 1
    elif priority == "low":
        score -= 1

    if regex_any(MEMORY_STRONG_SIGNAL_PATTERNS, blob):
        score += 3
        reasons.append("strong_signal")
    if memory_source_window_count(item) >= 2:
        score += 1
    try:
        if int(item.get("occurrence_count") or 0) >= 2:
            score += 1
    except (TypeError, ValueError):
        pass
    if regex_any(MEMORY_WEAK_PATTERNS, lowered_blob):
        score -= 2
    if regex_any(MEMORY_QUESTION_PATTERNS, lowered_blob) and "strong_signal" not in reasons:
        score -= 2

    if bucket == "low_priority":
        if score <= -2:
            return {"disposition": "drop", "score": score, "reason": "weak_low_priority"}
        return {"disposition": "keep", "score": score, "reason": "low_priority"}

    threshold = 4 if bucket == "durable" else 3
    if score >= threshold:
        return {"disposition": "keep", "score": score, "reason": ",".join(reasons) or "useful"}
    if score <= 0:
        return {"disposition": "drop", "score": score, "reason": "low_signal"}
    return {"disposition": "demote", "score": score, "reason": "below_primary_threshold"}


def effective_host_context_policy(item, policy):
    if policy != INJECTION_NEVER and memory_record_is_low_priority(item):
        return INJECTION_LOCAL_ONLY
    if policy != INJECTION_GLOBAL_CONTEXT:
        return policy
    if memory_record_needs_global_context_approval(item) and not memory_record_has_global_context_approval(item):
        return INJECTION_ON_DEMAND
    return policy


def normalize_memory_scope(value):
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return ""
    return MEMORY_SCOPE_ALIASES.get(text, text)


def memory_scope_from_record(item):
    explicit_scope = first_record_value(item, MEMORY_SCOPE_KEYS)
    scope = normalize_memory_scope(explicit_scope)
    if scope:
        return scope

    if str(item.get("bucket") or "").strip() == "low_priority" or str(
        item.get("priority") or ""
    ).strip().lower() == "low":
        return MEMORY_SCOPE_LOCAL

    if any(collapse_whitespace(item.get(key, "")) for key in ("project_key", "project_label", "repo", "cwd")):
        return MEMORY_SCOPE_PROJECT
    if has_source_window_refs(item):
        return MEMORY_SCOPE_PROJECT
    return MEMORY_SCOPE_GLOBAL


def normalize_injection_policy(value):
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return ""
    return INJECTION_POLICY_ALIASES.get(text, text)


def default_injection_policy_for_scope(scope, bucket="", priority=""):
    bucket = str(bucket or "").strip()
    priority = str(priority or "").strip().lower()
    if bucket == "low_priority" or priority == "low":
        return INJECTION_LOCAL_ONLY
    if scope == MEMORY_SCOPE_GLOBAL:
        return INJECTION_GLOBAL_CONTEXT
    if scope in {MEMORY_SCOPE_PROJECT, MEMORY_SCOPE_REPO, MEMORY_SCOPE_HOST}:
        return INJECTION_PROJECT_CONTEXT
    if scope == MEMORY_SCOPE_DOMAIN:
        return INJECTION_ON_DEMAND
    return INJECTION_LOCAL_ONLY


def host_context_injection_policy_from_record(item):
    explicit_policy = first_record_value(item, INJECTION_POLICY_KEYS)
    policy = normalize_injection_policy(explicit_policy)
    if policy:
        return effective_host_context_policy(item, policy)
    return effective_host_context_policy(
        item,
        default_injection_policy_for_scope(
            memory_scope_from_record(item),
            bucket=item.get("bucket", "") if isinstance(item, dict) else "",
            priority=item.get("priority", "") if isinstance(item, dict) else "",
        ),
    )


def memory_record_is_global_context(item):
    if not isinstance(item, dict):
        return False
    if str(item.get("bucket") or "").strip() not in {"durable", "session"}:
        return False
    return host_context_injection_policy_from_record(item) == INJECTION_GLOBAL_CONTEXT


def policy_label(policy, language="zh"):
    label = POLICY_LABELS.get(policy, (policy or "", policy or ""))
    return label[1] if language == "en" else label[0]


def scope_label(scope, language="zh"):
    label = SCOPE_LABELS.get(scope, (scope or "", scope or ""))
    return label[1] if language == "en" else label[0]


def normalize_memory_context_row(row):
    if not isinstance(row, dict):
        return None
    policy = host_context_injection_policy_from_record(row)
    if policy not in INJECTION_POLICY_ORDER:
        policy = INJECTION_LOCAL_ONLY
    scope = memory_scope_from_record(row)
    normalized = dict(row)
    normalized["scope"] = scope
    normalized["injection_policy"] = policy
    return normalized


def _policy_count(policy_counts, *policies):
    return sum(policy_counts.get(policy, 0) for policy in policies)


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_memory_policy_views(memory_rows, selected_global_rows=None, token_usage=None):
    rows = [
        normalized
        for normalized in (normalize_memory_context_row(row) for row in (memory_rows or []))
        if normalized is not None
    ]
    policy_rows = {policy: [] for policy in INJECTION_POLICY_ORDER}
    scope_counts = Counter()
    bucket_counts = Counter()
    for row in rows:
        policy = row.get("injection_policy") or INJECTION_LOCAL_ONLY
        if policy not in policy_rows:
            policy = INJECTION_LOCAL_ONLY
            row["injection_policy"] = policy
        policy_rows[policy].append(row)
        scope_counts[row.get("scope") or "unknown"] += 1
        bucket_counts[row.get("bucket") or "unknown"] += 1

    global_candidate_rows = [row for row in rows if memory_record_is_global_context(row)]
    if selected_global_rows is None:
        global_rows = list(global_candidate_rows)
    else:
        global_rows = [
            normalized
            for normalized in (normalize_memory_context_row(row) for row in selected_global_rows)
            if normalized is not None
        ]

    policy_counts = Counter(row.get("injection_policy") or INJECTION_LOCAL_ONLY for row in rows)
    local_rows = policy_rows[INJECTION_LOCAL_ONLY] + policy_rows[INJECTION_NEVER]
    token_usage = token_usage or {}
    selected_global_count = len(global_rows)
    if "estimated_context_item_count" in token_usage:
        selected_global_count = _safe_int(
            token_usage.get("estimated_context_item_count"),
            selected_global_count,
        )
    selected_global_count = min(selected_global_count, len(global_candidate_rows))
    compiler = {
        "total_count": len(rows),
        "global_candidate_count": len(global_candidate_rows),
        "selected_global_count": selected_global_count,
        "preview_global_count": len(global_rows),
        "project_context_count": policy_counts.get(INJECTION_PROJECT_CONTEXT, 0),
        "on_demand_count": policy_counts.get(INJECTION_ON_DEMAND, 0),
        "local_count": _policy_count(policy_counts, INJECTION_LOCAL_ONLY, INJECTION_NEVER),
        "never_count": policy_counts.get(INJECTION_NEVER, 0),
        "estimated_tokens": token_usage.get("estimated_tokens", 0),
        "max_tokens": token_usage.get("max_tokens", 0),
        "meter_percent": token_usage.get("meter_percent", 0),
        "mode_label": token_usage.get("mode_label", ""),
        "mode_note_zh": token_usage.get("mode_note_zh", token_usage.get("mode_note", "")),
        "mode_note_en": token_usage.get("mode_note_en", ""),
        "status_label_zh": token_usage.get("status_label_zh", token_usage.get("status_label", "")),
        "status_label_en": token_usage.get("status_label_en", ""),
        "value_display_zh": token_usage.get("value_display_zh", token_usage.get("value_display", "")),
        "value_display_en": token_usage.get("value_display_en", token_usage.get("value_display", "")),
        "enabled": bool(token_usage.get("enabled", True)),
        "policy_counts": dict(policy_counts),
        "scope_counts": dict(scope_counts),
        "bucket_counts": dict(bucket_counts),
    }
    return {
        "compiler": compiler,
        "global_context": {
            "rows": global_rows,
            "candidate_rows": global_candidate_rows,
            "count": len(global_rows),
            "candidate_count": len(global_candidate_rows),
        },
        "project_context": {
            "rows": policy_rows[INJECTION_PROJECT_CONTEXT],
            "count": policy_counts.get(INJECTION_PROJECT_CONTEXT, 0),
        },
        "on_demand": {
            "rows": policy_rows[INJECTION_ON_DEMAND],
            "count": policy_counts.get(INJECTION_ON_DEMAND, 0),
        },
        "local_only": {
            "rows": local_rows,
            "count": len(local_rows),
        },
        "rows_by_policy": policy_rows,
    }
