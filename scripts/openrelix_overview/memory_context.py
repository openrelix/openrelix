"""Pure helpers for OpenRelix managed memory context policy views."""

from collections import Counter


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
        return policy
    return default_injection_policy_for_scope(
        memory_scope_from_record(item),
        bucket=item.get("bucket", "") if isinstance(item, dict) else "",
        priority=item.get("priority", "") if isinstance(item, dict) else "",
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
