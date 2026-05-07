#!/usr/bin/env python3

import argparse
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from asset_runtime import (
    atomic_write_text,
    ensure_state_layout,
    get_memory_summary_budget,
    get_runtime_language,
    get_runtime_paths,
    memory_summary_budget_from_max,
    normalize_language,
)
from openrelix_overview.memory_context import (
    INJECTION_GLOBAL_CONTEXT,
    INJECTION_PROJECT_CONTEXT,
    MEMORY_SCOPE_GLOBAL,
    first_record_value,
    host_context_injection_policy_from_record,
    memory_record_is_host_context_candidate,
    memory_scope_from_record,
)
from openrelix_overview.memory_feedback import apply_memory_feedback_map, load_memory_feedback_map


PATHS = get_runtime_paths()
LANGUAGE = get_runtime_language(PATHS)
DEFAULT_MEMORY_INDEX = PATHS.codex_home / "memories" / "MEMORY.md"
DEFAULT_MEMORY_SUMMARY = PATHS.runtime_dir / "codex-native-memory-summary.md"
DEFAULT_CANONICAL_MEMORY_REGISTRY = PATHS.registry_dir / "memory_entries.jsonl"
DEFAULT_PERSONAL_MEMORY_REGISTRY = PATHS.registry_dir / "memory_items.jsonl"

DEFAULT_TARGET_TOKENS = 6700
DEFAULT_WARN_TOKENS = 7400
DEFAULT_MAX_TOKENS = 8000
DEFAULT_PROFILE_TOKENS = 280
DEFAULT_PREFERENCES_TOKENS = 950
DEFAULT_TIPS_TOKENS = 950
DEFAULT_ROUTES_TOKENS = 1250
DEFAULT_GLOBAL_MEMORY_TOKENS = 800
DEFAULT_PROJECT_MEMORY_TOKENS = 2400
DEFAULT_PERSONAL_MEMORY_TOKENS = DEFAULT_GLOBAL_MEMORY_TOKENS + DEFAULT_PROJECT_MEMORY_TOKENS
DEFAULT_MAX_PREFERENCES = 10
DEFAULT_MAX_TIPS = 8
DEFAULT_MAX_ROUTE_ITEMS = 10
DEFAULT_MAX_ROUTE_KEYWORDS = 4
DEFAULT_MAX_PERSONAL_MEMORY_ITEMS = 0
PERSONAL_MEMORY_TITLE_LIMIT = 72
PERSONAL_MEMORY_NOTE_LIMIT = 96
PERSONAL_MEMORY_CLI_DUPLICATE_THRESHOLD = 0.72
PERSONAL_MEMORY_INTERNAL_DUPLICATE_THRESHOLD = 0.82

SUMMARY_DEDUPE_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "because",
    "before",
    "current",
    "default",
    "from",
    "into",
    "local",
    "memory",
    "must",
    "only",
    "should",
    "that",
    "the",
    "this",
    "use",
    "user",
    "when",
    "with",
    "上下文",
    "个人",
    "优先",
    "使用",
    "当前",
    "默认",
    "用户",
    "记忆",
    "需要",
    "可以",
}

SECTION_PREFERENCE = "User preferences"
SECTION_TIPS = "General Tips"
SECTION_PROFILE = "User Profile"


@dataclass
class TaskGroup:
    title: str
    scope: str = ""
    applies_to: str = ""
    updated_date: str = ""
    keywords: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    reusable_knowledge: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


@dataclass
class PersonalMemoryItem:
    title: str
    bucket: str = ""
    memory_type: str = ""
    priority: str = "medium"
    value_note: str = ""
    scope: str = MEMORY_SCOPE_GLOBAL
    injection_policy: str = INJECTION_GLOBAL_CONTEXT
    project_key: str = ""
    project_label: str = ""
    updated_at: str = ""
    keywords: list[str] = field(default_factory=list)
    source_systems: list[str] = field(default_factory=list)
    occurrence_count: int = 1


@dataclass(frozen=True)
class SummaryBudget:
    target_tokens: int = DEFAULT_TARGET_TOKENS
    warn_tokens: int = DEFAULT_WARN_TOKENS
    max_tokens: int = DEFAULT_MAX_TOKENS
    profile_tokens: int = DEFAULT_PROFILE_TOKENS
    preferences_tokens: int = DEFAULT_PREFERENCES_TOKENS
    tips_tokens: int = DEFAULT_TIPS_TOKENS
    routes_tokens: int = DEFAULT_ROUTES_TOKENS
    personal_memory_tokens: int = DEFAULT_PERSONAL_MEMORY_TOKENS
    global_memory_tokens: int = DEFAULT_GLOBAL_MEMORY_TOKENS
    project_memory_tokens: int = DEFAULT_PROJECT_MEMORY_TOKENS
    max_preferences: int = DEFAULT_MAX_PREFERENCES
    max_tips: int = DEFAULT_MAX_TIPS
    max_route_items: int = DEFAULT_MAX_ROUTE_ITEMS
    max_route_keywords: int = DEFAULT_MAX_ROUTE_KEYWORDS
    max_personal_memory_items: int = DEFAULT_MAX_PERSONAL_MEMORY_ITEMS


@dataclass(frozen=True)
class ProjectContextFilter:
    cwd: str = ""
    key: str = ""
    label: str = ""
    tokens: frozenset[str] = frozenset()


@dataclass(frozen=True)
class BuildResult:
    text: str
    estimated_tokens: int
    estimator: str
    status: str


def localized(zh_text, en_text):
    return zh_text if LANGUAGE == "zh" else en_text


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Build a bounded summary from the Codex native memory index and "
            "the local personal memory registry. "
            "By default this writes into the personal-assets runtime directory, "
            "not into host homes. Pass --memory-summary explicitly when syncing "
            "the compressed summary into host context."
        )
    )
    parser.add_argument("--memory-index", default=str(DEFAULT_MEMORY_INDEX))
    parser.add_argument("--memory-summary", default=str(DEFAULT_MEMORY_SUMMARY))
    parser.add_argument(
        "--canonical-memory-registry",
        default=str(DEFAULT_CANONICAL_MEMORY_REGISTRY),
        help="OpenRelix Memory System v2 canonical registry. Used before the legacy registry when present.",
    )
    parser.add_argument("--personal-memory-registry", default=str(DEFAULT_PERSONAL_MEMORY_REGISTRY))
    parser.add_argument(
        "--project-cwd",
        default="",
        help="Build an active host-context summary for this project cwd, including global memory plus matching project memory.",
    )
    parser.add_argument(
        "--project-key",
        default="",
        help="Optional explicit project key used for project-memory matching.",
    )
    parser.add_argument(
        "--project-label",
        default="",
        help="Optional display label used for project-memory matching.",
    )
    parser.add_argument("--target-tokens", type=int)
    parser.add_argument("--warn-tokens", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--profile-tokens", type=int, default=DEFAULT_PROFILE_TOKENS)
    parser.add_argument("--preferences-tokens", type=int, default=DEFAULT_PREFERENCES_TOKENS)
    parser.add_argument("--tips-tokens", type=int, default=DEFAULT_TIPS_TOKENS)
    parser.add_argument("--routes-tokens", type=int, default=DEFAULT_ROUTES_TOKENS)
    parser.add_argument("--personal-memory-tokens", type=int)
    parser.add_argument("--global-memory-tokens", type=int)
    parser.add_argument("--project-memory-tokens", type=int)
    parser.add_argument("--max-preferences", type=int, default=DEFAULT_MAX_PREFERENCES)
    parser.add_argument("--max-tips", type=int, default=DEFAULT_MAX_TIPS)
    parser.add_argument("--max-route-items", type=int, default=DEFAULT_MAX_ROUTE_ITEMS)
    parser.add_argument("--max-route-keywords", type=int, default=DEFAULT_MAX_ROUTE_KEYWORDS)
    parser.add_argument(
        "--max-personal-memory-items",
        type=int,
        default=DEFAULT_MAX_PERSONAL_MEMORY_ITEMS,
        help="Maximum personal memory items to consider. Use 0 for no item cap; token budget still applies.",
    )
    parser.add_argument("--no-personal-memory", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    return parser.parse_args()


def collapse_whitespace(text):
    return " ".join(str(text or "").split())


def clip_text(text, limit):
    compact = collapse_whitespace(text)
    if not compact:
        return ""
    if len(compact) <= limit:
        return compact
    return compact[: max(limit - 1, 1)].rstrip() + "…"


def strip_task_refs(text):
    compact = collapse_whitespace(text)
    compact = re.sub(r"\s*\[Task[^\]]+\]", "", compact)
    return compact.strip()


def split_keywords(text):
    return [part.strip() for part in re.split(r"[，,]\s*", text) if part.strip()]


def updated_date_from_rollout_bullet(text):
    match = re.search(r"updated_at=(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else ""


def normalize_summary_key(text):
    compact = collapse_whitespace(text).lower()
    compact = re.sub(r"`+", "", compact)
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", compact)
    return compact.strip()


def normalize_project_token(text):
    compact = collapse_whitespace(str(text or ""))
    if not compact:
        return ""
    compact = compact.strip("`'\"")
    compact = re.sub(r"^(?:cwd|repo|repository|project|workspace|path)\s*=\s*", "", compact, flags=re.IGNORECASE)
    compact = compact.replace("\\", "/").lower()
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff/._~-]+", "-", compact)
    compact = re.sub(r"-+", "-", compact)
    return compact.strip("-")


def project_token_candidates(value):
    text = collapse_whitespace(value)
    if not text:
        return set()
    tokens = {normalize_project_token(text)}
    stripped = text.strip("`'\"")
    if stripped.startswith("cwd="):
        stripped = stripped.partition("=")[2].strip()
        tokens.add(normalize_project_token(stripped))
    if stripped.startswith("~/") or stripped.startswith("/"):
        try:
            path = Path(stripped).expanduser().resolve()
        except OSError:
            path = Path(stripped).expanduser()
        tokens.add(normalize_project_token(str(path)))
        if path.name:
            tokens.add(normalize_project_token(path.name))
    return {token for token in tokens if token}


def git_marker_identity_tokens(project_root):
    tokens = set()
    git_marker = project_root / ".git"
    if git_marker.is_dir():
        tokens.add(normalize_project_token(project_root.name))
        return tokens
    if not git_marker.is_file():
        return tokens
    try:
        text = git_marker.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return tokens
    match = re.search(r"gitdir:\s*(.+)", text)
    if not match:
        return tokens
    gitdir = Path(match.group(1).strip()).expanduser()
    if not gitdir.is_absolute():
        gitdir = (project_root / gitdir).resolve()
    parts = list(gitdir.parts)
    for index, part in enumerate(parts):
        if part == ".git" and index > 0:
            tokens.add(normalize_project_token(parts[index - 1]))
            break
    if "worktrees" in parts and parts[-1:]:
        tokens.add(normalize_project_token(parts[-1]))
    return tokens


def git_marker_project_label(project_root):
    git_marker = project_root / ".git"
    if git_marker.is_dir():
        return project_root.name
    if not git_marker.is_file():
        return ""
    try:
        text = git_marker.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    match = re.search(r"gitdir:\s*(.+)", text)
    if not match:
        return ""
    gitdir = Path(match.group(1).strip()).expanduser()
    if not gitdir.is_absolute():
        gitdir = (project_root / gitdir).resolve()
    parts = list(gitdir.parts)
    for index, part in enumerate(parts):
        if part == ".git" and index > 0:
            return parts[index - 1]
    return ""


def find_git_project_root(path):
    try:
        candidate = Path(path).expanduser().resolve()
    except OSError:
        candidate = Path(path).expanduser()
    if not candidate.is_dir():
        candidate = candidate.parent
    for parent in (candidate, *candidate.parents):
        if (parent / ".git").exists():
            return parent
    return candidate


def build_project_context_filter(project_cwd="", project_key="", project_label=""):
    if not any(collapse_whitespace(value) for value in (project_cwd, project_key, project_label)):
        return ProjectContextFilter()

    tokens = set()
    resolved_cwd = ""
    detected_label = ""
    if project_cwd:
        try:
            cwd_path = Path(project_cwd).expanduser().resolve()
        except OSError:
            cwd_path = Path(project_cwd).expanduser()
        resolved_cwd = str(cwd_path)
        tokens.update(project_token_candidates(resolved_cwd))
        if cwd_path.name:
            detected_label = cwd_path.name
            tokens.add(normalize_project_token(cwd_path.name))
        project_root = find_git_project_root(cwd_path)
        if project_root:
            detected_label = git_marker_project_label(project_root) or project_root.name or detected_label
            tokens.update(project_token_candidates(str(project_root)))
            tokens.add(normalize_project_token(project_root.name))
            tokens.update(git_marker_identity_tokens(project_root))

    label = collapse_whitespace(project_label) or detected_label
    key = collapse_whitespace(project_key) or normalize_project_token(label)
    tokens.update(project_token_candidates(project_key))
    tokens.update(project_token_candidates(project_label))
    tokens.update(project_token_candidates(label))
    tokens.add(normalize_project_token(key))

    tokens = {token for token in tokens if token and token not in {"project", "workspace", "repo", "repository"}}
    return ProjectContextFilter(
        cwd=resolved_cwd or collapse_whitespace(project_cwd),
        key=key,
        label=label,
        tokens=frozenset(tokens),
    )


def project_filter_is_active(project_filter):
    return bool(project_filter and project_filter.tokens)


def project_text_matches_filter(value, project_filter):
    if not project_filter_is_active(project_filter):
        return True
    value_tokens = project_token_candidates(value)
    if value_tokens & set(project_filter.tokens):
        return True
    normalized = normalize_project_token(value)
    if not normalized:
        return False
    for token in project_filter.tokens:
        if len(token) >= 4 and (token in normalized or normalized in token):
            return True
    return False


def record_project_candidate_texts(item):
    candidates = []
    for key in (
        "project_key",
        "project_label",
        "repo",
        "repository",
        "cwd",
        "cwd_display",
        "workspace",
        "workspace_label",
        "applies_to",
    ):
        value = item.get(key)
        if collapse_whitespace(value):
            candidates.append(str(value))
    for window in item.get("source_windows") or []:
        if not isinstance(window, dict):
            continue
        for key in ("project_key", "project_label", "cwd", "cwd_display", "repo", "repository"):
            value = window.get(key)
            if collapse_whitespace(value):
                candidates.append(str(value))
    return candidates


def memory_record_applies_to_project(item, scope, injection_policy, project_filter):
    if not project_filter_is_active(project_filter):
        return scope == MEMORY_SCOPE_GLOBAL or injection_policy == INJECTION_GLOBAL_CONTEXT
    if scope == MEMORY_SCOPE_GLOBAL or injection_policy == INJECTION_GLOBAL_CONTEXT:
        return True
    if injection_policy != INJECTION_PROJECT_CONTEXT:
        return False
    candidates = record_project_candidate_texts(item)
    if not candidates:
        return False
    return any(project_text_matches_filter(candidate, project_filter) for candidate in candidates)


PROJECT_SCOPED_GROUP_HINT_RE = re.compile(
    r"(?:\bcwd\s*=|~/|/[^\s]*|openrelix|douyin|android|gradle|kotlin|java|repo|repository|project|workspace|worktree)",
    re.IGNORECASE,
)


def task_group_applies_to_project(group, project_filter):
    if not project_filter_is_active(project_filter):
        return True
    texts = [group.title, group.scope, group.applies_to]
    if any(project_text_matches_filter(text, project_filter) for text in texts):
        return True
    combined = collapse_whitespace(" ".join(texts))
    if not PROJECT_SCOPED_GROUP_HINT_RE.search(combined):
        return True
    return False


def filter_task_groups_for_project(groups, project_filter):
    if not project_filter_is_active(project_filter):
        return groups
    return [group for group in groups if task_group_applies_to_project(group, project_filter)]


def summary_match_tokens(text):
    normalized = normalize_summary_key(text)
    tokens = set()
    for token in normalized.split():
        if token in SUMMARY_DEDUPE_STOPWORDS:
            continue
        if re.search(r"[\u4e00-\u9fff]", token):
            compact = re.sub(r"\s+", "", token)
            if len(compact) >= 2 and compact not in SUMMARY_DEDUPE_STOPWORDS:
                tokens.add(compact[:24])
            for size in (4, 3, 2):
                if len(compact) < size:
                    continue
                for index in range(0, len(compact) - size + 1):
                    part = compact[index : index + size]
                    if part not in SUMMARY_DEDUPE_STOPWORDS:
                        tokens.add(part)
                    if len(tokens) >= 48:
                        return tokens
            continue
        if len(token) >= 4:
            tokens.add(token[:32])
    return tokens


def summary_text_similarity(left, right):
    left_key = normalize_summary_key(left)
    right_key = normalize_summary_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    shorter, longer = sorted((left_key, right_key), key=len)
    if len(shorter) >= 12 and shorter in longer:
        return 0.92
    left_tokens = summary_match_tokens(left_key)
    right_tokens = summary_match_tokens(right_key)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    if overlap == 0:
        return 0.0
    jaccard = overlap / len(left_tokens | right_tokens)
    containment = overlap / min(len(left_tokens), len(right_tokens))
    return max(jaccard, containment * 0.85)


def capitalize_rule(text):
    if not text:
        return ""
    if "a" <= text[:1] <= "z":
        return text[:1].upper() + text[1:]
    return text


def extract_preference_rule(text):
    cleaned = strip_task_refs(text)
    for token in ("->", "→", "=>"):
        if token in cleaned:
            cleaned = cleaned.split(token)[-1].strip()
            break
    return clip_text(capitalize_rule(cleaned), 180)


def extract_tip_rule(text):
    cleaned = strip_task_refs(text)
    return clip_text(cleaned, 190)


def parse_markdown_sections(text):
    sections = {}
    current = ""
    buffer = []
    for raw_line in text.splitlines():
        if raw_line.startswith("## "):
            if current:
                sections[current] = "\n".join(buffer).strip()
            current = raw_line[3:].strip()
            buffer = []
            continue
        if current:
            buffer.append(raw_line.rstrip())
    if current:
        sections[current] = "\n".join(buffer).strip()
    return sections


def parse_bullets(section_text):
    bullets = []
    current = []
    for raw_line in section_text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("- "):
            if current:
                bullets.append(collapse_whitespace(" ".join(current)))
            current = [stripped[2:].strip()]
            continue
        if current and stripped and not raw_line.startswith("#"):
            current.append(stripped)
            continue
        if current and not stripped:
            bullets.append(collapse_whitespace(" ".join(current)))
            current = []
    if current:
        bullets.append(collapse_whitespace(" ".join(current)))
    return [bullet for bullet in bullets if bullet]


def parse_profile_paragraphs(section_text):
    paragraphs = []
    current = []
    for raw_line in section_text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            if current:
                paragraphs.append(collapse_whitespace(" ".join(current)))
                current = []
            continue
        if stripped.startswith("#"):
            continue
        current.append(stripped)
    if current:
        paragraphs.append(collapse_whitespace(" ".join(current)))
    return [paragraph for paragraph in paragraphs if paragraph]


def parse_memory_index(text):
    groups = []
    current_group = None
    current_section = ""
    current_bullet_parts = []

    def flush_bullet():
        nonlocal current_bullet_parts
        if not current_group or not current_bullet_parts:
            current_bullet_parts = []
            return
        bullet_text = strip_task_refs(" ".join(current_bullet_parts))
        current_bullet_parts = []
        if not bullet_text:
            return
        if current_section == "preferences":
            current_group.preferences.append(bullet_text)
        elif current_section == "reusable":
            current_group.reusable_knowledge.append(bullet_text)
        elif current_section == "failures":
            current_group.failures.append(bullet_text)

    def flush_group():
        nonlocal current_group
        flush_bullet()
        if current_group:
            groups.append(current_group)
        current_group = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if line.startswith("# Task Group: "):
            flush_group()
            current_group = TaskGroup(title=line.partition(":")[2].strip())
            current_section = ""
            continue
        if current_group is None:
            continue
        if line.startswith("scope:"):
            flush_bullet()
            current_group.scope = line.partition(":")[2].strip()
            current_section = ""
            continue
        if line.startswith("applies_to:"):
            flush_bullet()
            current_group.applies_to = line.partition(":")[2].strip()
            current_section = ""
            continue
        if line.startswith("### rollout_summary_files"):
            flush_bullet()
            current_section = "rollout"
            continue
        if line.startswith("### keywords"):
            flush_bullet()
            current_section = "keywords"
            continue
        if line.startswith("## User preferences"):
            flush_bullet()
            current_section = "preferences"
            continue
        if line.startswith("## Reusable knowledge"):
            flush_bullet()
            current_section = "reusable"
            continue
        if line.startswith("## Failures and how to do differently"):
            flush_bullet()
            current_section = "failures"
            continue
        if line.startswith("## "):
            flush_bullet()
            current_section = ""
            continue
        if not stripped:
            continue
        if stripped.startswith("- "):
            flush_bullet()
            bullet_body = stripped[2:].strip()
            if current_section == "keywords":
                for keyword in split_keywords(bullet_body):
                    if keyword not in current_group.keywords:
                        current_group.keywords.append(keyword)
                continue
            if current_section == "rollout":
                updated_date = updated_date_from_rollout_bullet(bullet_body)
                if updated_date and updated_date > current_group.updated_date:
                    current_group.updated_date = updated_date
                continue
            if current_section in {"preferences", "reusable", "failures"}:
                current_bullet_parts = [bullet_body]
            continue
        if current_section == "rollout":
            updated_date = updated_date_from_rollout_bullet(stripped)
            if updated_date and updated_date > current_group.updated_date:
                current_group.updated_date = updated_date
            continue
        if current_bullet_parts and current_section in {"preferences", "reusable", "failures"}:
            current_bullet_parts.append(stripped)

    flush_group()
    return groups


def memory_date_value(item):
    return str(
        item.get("updated_at")
        or item.get("date")
        or item.get("created_at")
        or ""
    )


def reverse_date_sort_key(value):
    digits = re.sub(r"\D", "", str(value or ""))[:14]
    if not digits:
        return 0
    return -int(digits.ljust(14, "0"))


def current_language(language=None):
    return normalize_language(language or LANGUAGE)


def localized_record_value(item, field, language=None):
    if not isinstance(item, dict):
        return ""
    language = current_language(language)
    candidates = (
        (
            "{}_en".format(field),
            "display_{}_en".format(field),
            field,
            "{}_zh".format(field),
        )
        if language == "en"
        else (
            "{}_zh".format(field),
            "display_{}".format(field),
            field,
            "{}_en".format(field),
        )
    )
    for key in candidates:
        value = collapse_whitespace(item.get(key, ""))
        if value:
            return value
    return ""


def normalize_string_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        values = value
    elif isinstance(value, tuple):
        values = list(value)
    else:
        values = re.split(r"[，,]\s*", str(value))
    normalized = []
    for item in values:
        text = collapse_whitespace(item)
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def memory_source_systems_from_record(item):
    source_systems = normalize_string_list(
        item.get("source_systems")
        or item.get("host_sources")
        or item.get("source_hosts")
    )
    source = collapse_whitespace(item.get("source", ""))
    if source and source not in source_systems:
        source_systems.append(source)
    return source_systems


def merge_string_lists(left, right, limit=8):
    merged = list(left or [])
    for item in right or []:
        if item and item not in merged:
            merged.append(item)
    return merged[:limit]


def personal_memory_group_key(item, bucket, title, scope, injection_policy):
    explicit_id = first_record_value(
        item,
        (
            "memory_id",
            "canonical_memory_id",
            "canonical_id",
            "memory_key",
        ),
    )
    if explicit_id:
        return normalize_summary_key(explicit_id)
    return normalize_summary_key(
        "{} {} {} {}".format(bucket, scope, injection_policy, title)
    )


def parse_personal_memory_registry(
    text,
    language=None,
    host_context_only=True,
    project_filter=None,
    feedback_by_key=None,
):
    language = current_language(language)
    grouped = {}
    if feedback_by_key is None:
        feedback_by_key = load_memory_feedback_map(PATHS)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        item = apply_memory_feedback_map(item, feedback_by_key)

        bucket = str(item.get("bucket") or "").strip()
        if bucket not in {"durable", "session"}:
            continue

        title = localized_record_value(item, "title", language=language)
        if not title:
            continue

        scope = memory_scope_from_record(item)
        injection_policy = host_context_injection_policy_from_record(item)
        if host_context_only:
            if not memory_record_is_host_context_candidate(item):
                continue
            if not memory_record_applies_to_project(item, scope, injection_policy, project_filter):
                continue

        value_note = localized_record_value(item, "value_note", language=language)
        keywords = item.get("keywords") or []
        if not isinstance(keywords, list):
            keywords = split_keywords(str(keywords))
        keywords = [collapse_whitespace(keyword) for keyword in keywords if collapse_whitespace(keyword)]
        updated_at = memory_date_value(item)
        source_systems = memory_source_systems_from_record(item)
        key = personal_memory_group_key(item, bucket, title, scope, injection_policy)
        current = grouped.get(key)
        if current is None:
            grouped[key] = PersonalMemoryItem(
                title=title,
                bucket=bucket,
                memory_type=str(item.get("memory_type") or "semantic"),
                priority=str(item.get("priority") or "medium"),
                value_note=value_note,
                scope=scope,
                injection_policy=injection_policy,
                project_key=str(item.get("project_key") or ""),
                project_label=str(item.get("project_label") or ""),
                updated_at=updated_at,
                keywords=keywords[:6],
                source_systems=source_systems[:6],
                occurrence_count=1,
            )
            continue

        current.occurrence_count += 1
        current.source_systems = merge_string_lists(current.source_systems, source_systems)
        if updated_at >= current.updated_at:
            current.title = title
            current.memory_type = str(item.get("memory_type") or current.memory_type or "semantic")
            current.priority = str(item.get("priority") or current.priority or "medium")
            current.value_note = value_note or current.value_note
            current.scope = scope or current.scope
            current.injection_policy = injection_policy or current.injection_policy
            current.project_key = str(item.get("project_key") or current.project_key or "")
            current.project_label = str(item.get("project_label") or current.project_label or "")
            current.updated_at = updated_at
            current.keywords = keywords[:6] or current.keywords

    priority_rank = {"high": 0, "medium": 1, "low": 2}
    bucket_rank = {"durable": 0, "session": 1}
    return sorted(
        grouped.values(),
        key=lambda item: (
            bucket_rank.get(item.bucket, 2),
            priority_rank.get(item.priority, 1),
            reverse_date_sort_key(item.updated_at),
            -item.occurrence_count,
            item.title,
        ),
        reverse=False,
    )


def load_optional_tiktoken():
    try:
        import tiktoken  # type: ignore
    except Exception:
        return None, "heuristic"

    for encoding_name in ("o200k_base", "cl100k_base"):
        try:
            return tiktoken.get_encoding(encoding_name), "tiktoken:{}".format(encoding_name)
        except Exception:
            continue
    return None, "heuristic"


def estimate_tokens(text):
    encoding, method = load_optional_tiktoken()
    if encoding is not None:
        try:
            return len(encoding.encode(text)), method
        except Exception:
            pass

    total = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if "\u4e00" <= char <= "\u9fff":
            total += 1
            index += 1
            continue
        if char.isascii():
            next_index = index + 1
            while next_index < len(text):
                next_char = text[next_index]
                if next_char.isspace() or not next_char.isascii():
                    break
                next_index += 1
            segment = text[index:next_index]
            total += max(1, math.ceil(len(segment) / 3))
            index = next_index
            continue
        total += 1
        index += 1
    return total, "heuristic:ascii_div3_cjk1"


def fit_paragraphs(paragraphs, token_budget):
    lines = []
    used_tokens = 0
    estimator = "heuristic"
    for paragraph in paragraphs:
        if not paragraph:
            continue
        line_tokens, estimator = estimate_tokens(paragraph)
        if lines and used_tokens + line_tokens > token_budget:
            break
        if not lines and line_tokens > token_budget:
            continue
        if lines:
            lines.append("")
        lines.append(paragraph)
        used_tokens += line_tokens
    return lines, used_tokens, estimator


def fit_bullets(items, token_budget, max_items):
    lines = []
    used_tokens = 0
    estimator = "heuristic"
    for item in items:
        if len(lines) >= max_items:
            break
        bullet = "- {}".format(item)
        bullet_tokens, estimator = estimate_tokens(bullet)
        if lines and used_tokens + bullet_tokens > token_budget:
            break
        if not lines and bullet_tokens > token_budget:
            continue
        lines.append(bullet)
        used_tokens += bullet_tokens
    return lines, used_tokens, estimator


def dedupe_preserve_order(items):
    deduped = []
    seen = set()
    for item in items:
        normalized = normalize_summary_key(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(item)
    return deduped


PERSONAL_ASSET_ROUTE_HEADING = "Personal asset workflows + user-level Codex state"
ANDROID_PROJECT_ROUTE_HEADING = "Android project workflows"
CROSS_SCOPE_ROUTE_HEADING = "Cross-scope workflow (user-level Codex + project workflows)"


def classify_route_heading(group):
    combined = collapse_whitespace("{} {} {}".format(group.title, group.scope, group.applies_to)).lower()
    if "cross-scope" in combined:
        return CROSS_SCOPE_ROUTE_HEADING
    if (
        ("repo/" in combined or "project" in combined or "android" in combined)
        and ("openrelix" in combined or "personal asset" in combined)
        and ("cwd=" in combined or "user-level codex" in combined)
    ):
        return CROSS_SCOPE_ROUTE_HEADING
    if "local codex state under ~/.codex" in combined or "user-level codex" in combined:
        return PERSONAL_ASSET_ROUTE_HEADING
    if "openrelix" in combined or "personal asset" in combined:
        return PERSONAL_ASSET_ROUTE_HEADING
    if "android" in combined or "gradle" in combined or "kotlin" in combined or "java" in combined:
        return ANDROID_PROJECT_ROUTE_HEADING
    return CROSS_SCOPE_ROUTE_HEADING


def compact_route_desc(group):
    scope = strip_task_refs(group.scope)
    if not scope:
        return ""
    scope = re.sub(r"^Use when ", "", scope, flags=re.IGNORECASE)
    return clip_text(scope, 170)


def compact_route_learning(group):
    if not group.reusable_knowledge:
        return ""
    return clip_text(group.reusable_knowledge[0], 180)


def build_route_lines(groups, token_budget, max_items, max_keywords):
    grouped_lines = {}
    heading_order = []
    used_tokens = 0
    total_items = 0
    estimator = "heuristic"

    for group in groups:
        if total_items >= max_items:
            break
        heading = classify_route_heading(group)
        if heading not in grouped_lines:
            grouped_lines[heading] = []
            heading_order.append(heading)

        keywords = ", ".join(group.keywords[:max_keywords])
        title_line = group.title
        if keywords:
            title_line = "{}: {}".format(title_line, keywords)
        desc = compact_route_desc(group)
        learning = compact_route_learning(group)

        candidate_variants = []
        if desc and learning:
            candidate_variants.append(
                [
                    "- {}".format(title_line),
                    "  - desc: {}".format(desc),
                    "  - learnings: {}".format(learning),
                ]
            )
        if desc:
            candidate_variants.append(
                [
                    "- {}".format(title_line),
                    "  - desc: {}".format(desc),
                ]
            )
        if learning:
            candidate_variants.append(
                [
                    "- {}".format(title_line),
                    "  - learnings: {}".format(learning),
                ]
            )
        candidate_variants.append(["- {}".format(title_line)])

        chosen_lines = None
        for candidate_lines in candidate_variants:
            candidate_text = "\n".join(candidate_lines)
            candidate_tokens, estimator = estimate_tokens(candidate_text)
            heading_tokens = 0
            if not grouped_lines[heading]:
                heading_tokens, estimator = estimate_tokens("### {}\n".format(heading))
            if used_tokens + heading_tokens + candidate_tokens > token_budget:
                continue
            chosen_lines = candidate_lines
            used_tokens += heading_tokens + candidate_tokens
            break

        if not chosen_lines:
            continue

        grouped_lines[heading].append(chosen_lines)
        total_items += 1

    rendered = []
    for heading in heading_order:
        if not grouped_lines[heading]:
            continue
        if rendered:
            rendered.append("")
        rendered.append("### {}".format(heading))
        rendered.append("")
        for entry in grouped_lines[heading]:
            rendered.extend(entry)

    return rendered, used_tokens, estimator


def personal_memory_item_match_keys(item):
    keys = set()
    for value in (item.title, item.value_note):
        normalized = normalize_summary_key(value)
        if normalized:
            keys.add(normalized)
    combined = normalize_summary_key("{} {}".format(item.title, item.value_note))
    if combined:
        keys.add(combined)
    return keys


def summary_bullet_match_keys(text):
    cleaned = strip_task_refs(text)
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", cleaned).strip()
    if cleaned.startswith("- "):
        cleaned = cleaned[2:].strip()
    keys = set()
    for value in (cleaned, cleaned.split(" - ", 1)[0], cleaned.split(":", 1)[0]):
        normalized = normalize_summary_key(value)
        if normalized:
            keys.add(normalized)
    return keys


def existing_summary_match_keys(existing_sections):
    keys = set()
    for section_text in existing_sections.values():
        for bullet in parse_bullets(section_text):
            keys.update(summary_bullet_match_keys(bullet))
    return keys


def existing_summary_match_texts(existing_sections):
    texts = []
    for section_text in existing_sections.values():
        for bullet in parse_bullets(section_text):
            cleaned = strip_task_refs(bullet)
            if cleaned and cleaned not in texts:
                texts.append(cleaned)
    return texts


def personal_memory_item_match_text(item):
    return collapse_whitespace("{} {}".format(item.title, item.value_note))


def personal_memory_item_is_similar_to_texts(item, texts, threshold):
    item_text = personal_memory_item_match_text(item)
    return any(summary_text_similarity(item_text, text) >= threshold for text in texts or [])


def build_personal_memory_lines(
    items,
    token_budget,
    max_items,
    blocked_match_keys=None,
    blocked_match_texts=None,
    selected_match_texts=None,
    include_heading=True,
):
    if not items or token_budget <= 0:
        return [], 0, "heuristic"

    blocked_match_keys = blocked_match_keys or set()
    blocked_match_texts = blocked_match_texts or []
    selected_match_texts = selected_match_texts if selected_match_texts is not None else []
    rendered = ["### Local personal memory registry", ""] if include_heading else []
    heading_tokens, estimator = estimate_tokens("\n".join(rendered)) if include_heading else (0, "heuristic")
    used_tokens = heading_tokens
    item_count = 0
    has_item_cap = max_items > 0

    for item in items:
        if has_item_cap and item_count >= max_items:
            break

        if personal_memory_item_match_keys(item) & blocked_match_keys:
            continue
        if personal_memory_item_is_similar_to_texts(
            item,
            blocked_match_texts,
            PERSONAL_MEMORY_CLI_DUPLICATE_THRESHOLD,
        ):
            continue
        if personal_memory_item_is_similar_to_texts(
            item,
            selected_match_texts,
            PERSONAL_MEMORY_INTERNAL_DUPLICATE_THRESHOLD,
        ):
            continue

        title = clip_text(item.title, PERSONAL_MEMORY_TITLE_LIMIT)
        note = clip_text(item.value_note, PERSONAL_MEMORY_NOTE_LIMIT)
        compact_meta_parts = [
            item.bucket or "unknown",
            item.memory_type or "semantic",
            item.priority or "medium",
        ]
        context_label = item.project_label or item.project_key or (
            item.scope if item.scope and item.scope != MEMORY_SCOPE_GLOBAL else ""
        )
        if context_label:
            compact_meta_parts.append(clip_text(context_label, 28))
        compact_meta = "/".join(compact_meta_parts)
        repeat_suffix = " (seen {}x)".format(item.occurrence_count) if item.occurrence_count > 1 else ""

        candidate_variants = []
        if note:
            candidate_variants.append(
                [
                    "- [{}] {} - {}{}".format(
                        compact_meta,
                        title,
                        note,
                        repeat_suffix,
                    )
                ]
            )
            candidate_variants.append(
                [
                    "- {} - {}{}".format(
                        title,
                        clip_text(note, 72),
                        repeat_suffix,
                    )
                ]
            )
        candidate_variants.append(["- [{}] {}{}".format(compact_meta, title, repeat_suffix)])
        candidate_variants.append(["- {}{}".format(title, repeat_suffix)])

        chosen_lines = None
        for candidate_lines in candidate_variants:
            candidate_text = "\n".join(candidate_lines)
            candidate_tokens, estimator = estimate_tokens(candidate_text)
            if used_tokens + candidate_tokens > token_budget:
                continue
            chosen_lines = candidate_lines
            used_tokens += candidate_tokens
            break
        if not chosen_lines:
            continue

        rendered.extend(chosen_lines)
        selected_match_texts.append(personal_memory_item_match_text(item))
        item_count += 1

    if item_count == 0:
        return [], 0, estimator
    return rendered, used_tokens, estimator


def personal_memory_item_is_global_context(item):
    return item.scope == MEMORY_SCOPE_GLOBAL or item.injection_policy == INJECTION_GLOBAL_CONTEXT


def personal_memory_item_is_project_context(item):
    return item.injection_policy == INJECTION_PROJECT_CONTEXT and not personal_memory_item_is_global_context(item)


def split_personal_memory_items(personal_memory_items):
    global_items = []
    project_items = []
    for item in personal_memory_items or []:
        if personal_memory_item_is_global_context(item):
            global_items.append(item)
        elif personal_memory_item_is_project_context(item):
            project_items.append(item)
    return global_items, project_items


def resolved_personal_memory_budgets(budget, has_project_items):
    global_budget = max(0, int(budget.global_memory_tokens or 0))
    project_budget = max(0, int(budget.project_memory_tokens or 0))
    if global_budget or project_budget:
        return global_budget, project_budget if has_project_items else 0

    total_budget = max(0, int(budget.personal_memory_tokens or 0))
    if not has_project_items:
        return total_budget, 0
    global_budget = max(100, min(total_budget, int(round(total_budget * 0.25))))
    return global_budget, max(0, total_budget - global_budget)


def generate_profile_paragraphs(groups):
    contexts = []
    if any(classify_route_heading(group) == ANDROID_PROJECT_ROUTE_HEADING for group in groups):
        contexts.append("Android project work")
    if any(
        classify_route_heading(group) == PERSONAL_ASSET_ROUTE_HEADING
        for group in groups
    ):
        contexts.append("user-level Codex and personal-asset workflows")
    if any(classify_route_heading(group) == CROSS_SCOPE_ROUTE_HEADING for group in groups):
        contexts.append("cross-scope review and workflow routing")

    if not contexts:
        contexts.append("local Codex workflows")

    primary_contexts = ", ".join(contexts[:-1]) + (" and " + contexts[-1] if len(contexts) > 1 else contexts[0])
    return [
        "The user works mainly across {}.".format(primary_contexts),
        "They prefer direct edits when the target state is clear, but want path or runtime evidence before code when the contract is still fuzzy.",
        "For review tasks, default to findings-first output; keep durable rules repo-agnostic and keep runtime state outside working repos whenever possible.",
    ]


def build_profile_lines(existing_sections, groups, token_budget):
    paragraphs = parse_profile_paragraphs(existing_sections.get(SECTION_PROFILE, ""))
    if not paragraphs:
        paragraphs = generate_profile_paragraphs(groups)
    else:
        paragraphs = [clip_text(paragraph, 320) for paragraph in paragraphs[:3]]
    return fit_paragraphs(paragraphs, token_budget)


def build_preference_lines(existing_sections, groups, token_budget, max_items):
    candidates = []
    candidates.extend(parse_bullets(existing_sections.get(SECTION_PREFERENCE, "")))
    for group in groups:
        candidates.extend(extract_preference_rule(item) for item in group.preferences)
    cleaned = [item for item in dedupe_preserve_order(candidates) if item]
    return fit_bullets(cleaned, token_budget, max_items)


def build_tip_lines(existing_sections, groups, token_budget, max_items):
    candidates = []
    candidates.extend(parse_bullets(existing_sections.get(SECTION_TIPS, "")))
    for group in groups:
        candidates.extend(extract_tip_rule(item) for item in group.reusable_knowledge)
    cleaned = [item for item in dedupe_preserve_order(candidates) if item]
    return fit_bullets(cleaned, token_budget, max_items)


def render_summary(profile_lines, preference_lines, tip_lines, personal_memory_lines, route_lines):
    lines = ["## User Profile", ""]
    if profile_lines:
        lines.extend(profile_lines)
    else:
        lines.append("No durable profile summary is available yet.")

    lines.extend(["", "## User preferences", ""])
    if preference_lines:
        lines.extend(preference_lines)
    else:
        lines.append("- Prefer exact runtime evidence and concise action-oriented answers.")

    lines.extend(["", "## General Tips", ""])
    if tip_lines:
        lines.extend(tip_lines)
    else:
        lines.append("- Keep durable rules in the full memory index and keep the injected summary compact.")

    lines.extend(["", "## What's in Memory", ""])
    combined_route_lines = []
    if personal_memory_lines:
        combined_route_lines.extend(personal_memory_lines)
    if route_lines:
        if combined_route_lines:
            combined_route_lines.append("")
        combined_route_lines.extend(route_lines)

    if combined_route_lines:
        lines.extend(combined_route_lines)
    else:
        lines.extend(
            [
                "### Current Topics",
                "",
                "- Native memory index is present but does not yet expose enough structured task groups to route from.",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def render_with_budgets(groups, existing_sections, personal_memory_items, budget):
    blocked_memory_keys = existing_summary_match_keys(existing_sections)
    blocked_memory_texts = existing_summary_match_texts(existing_sections)
    profile_lines, _, _ = build_profile_lines(existing_sections, groups, budget.profile_tokens)
    preference_lines, _, _ = build_preference_lines(
        existing_sections,
        groups,
        budget.preferences_tokens,
        budget.max_preferences,
    )
    tip_lines, _, _ = build_tip_lines(
        existing_sections,
        groups,
        budget.tips_tokens,
        budget.max_tips,
    )
    route_lines, _, _ = build_route_lines(
        groups,
        budget.routes_tokens,
        budget.max_route_items,
        budget.max_route_keywords,
    )
    global_memory_items, project_memory_items = split_personal_memory_items(personal_memory_items)
    global_memory_tokens, project_memory_tokens = resolved_personal_memory_budgets(
        budget,
        bool(project_memory_items),
    )
    selected_personal_match_texts = []
    global_personal_memory_lines, _, _ = build_personal_memory_lines(
        global_memory_items,
        global_memory_tokens,
        budget.max_personal_memory_items,
        blocked_match_keys=blocked_memory_keys,
        blocked_match_texts=blocked_memory_texts,
        selected_match_texts=selected_personal_match_texts,
    )
    project_personal_memory_lines, _, _ = build_personal_memory_lines(
        project_memory_items,
        project_memory_tokens,
        budget.max_personal_memory_items,
        blocked_match_keys=blocked_memory_keys,
        blocked_match_texts=blocked_memory_texts,
        selected_match_texts=selected_personal_match_texts,
        include_heading=not bool(global_personal_memory_lines),
    )
    personal_memory_lines = global_personal_memory_lines + project_personal_memory_lines
    return render_summary(
        profile_lines,
        preference_lines,
        tip_lines,
        personal_memory_lines,
        route_lines,
    )


def tighten_budget(budget):
    return SummaryBudget(
        target_tokens=max(1200, budget.target_tokens - 300),
        warn_tokens=max(1600, budget.warn_tokens - 300),
        max_tokens=max(2000, budget.max_tokens - 300),
        profile_tokens=max(120, budget.profile_tokens - 40),
        preferences_tokens=max(400, budget.preferences_tokens - 120),
        tips_tokens=max(400, budget.tips_tokens - 120),
        routes_tokens=max(400, budget.routes_tokens - 180),
        personal_memory_tokens=max(300, budget.personal_memory_tokens - 100),
        global_memory_tokens=(
            0 if budget.global_memory_tokens <= 0 else max(200, budget.global_memory_tokens - 50)
        ),
        project_memory_tokens=(
            0 if budget.project_memory_tokens <= 0 else max(300, budget.project_memory_tokens - 100)
        ),
        max_preferences=max(4, budget.max_preferences - 1),
        max_tips=max(4, budget.max_tips - 1),
        max_route_items=max(4, budget.max_route_items - 1),
        max_route_keywords=max(2, budget.max_route_keywords - 1),
        max_personal_memory_items=(
            0
            if budget.max_personal_memory_items <= 0
            else max(3, budget.max_personal_memory_items - 1)
        ),
    )


def build_memory_summary(memory_index_text, existing_summary_text, budget, personal_memory_items=None, project_filter=None):
    groups = filter_task_groups_for_project(parse_memory_index(memory_index_text), project_filter)
    existing_sections = parse_markdown_sections(existing_summary_text)
    personal_memory_items = personal_memory_items or []
    summary_text = render_with_budgets(groups, existing_sections, personal_memory_items, budget)
    estimated_tokens, estimator = estimate_tokens(summary_text)

    current_budget = budget
    while estimated_tokens > current_budget.target_tokens:
        tightened = tighten_budget(current_budget)
        if tightened == current_budget:
            break
        current_budget = tightened
        summary_text = render_with_budgets(groups, existing_sections, personal_memory_items, current_budget)
        estimated_tokens, estimator = estimate_tokens(summary_text)

    status = "ok"
    if estimated_tokens > current_budget.max_tokens:
        status = "over_budget"
    elif estimated_tokens > current_budget.warn_tokens:
        status = "warning"
    return BuildResult(
        text=summary_text,
        estimated_tokens=estimated_tokens,
        estimator=estimator,
        status=status,
    )


def load_text_if_exists(path):
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_memory_registry_text(canonical_path, legacy_path):
    canonical_text = load_text_if_exists(canonical_path).strip()
    if canonical_text:
        return canonical_text + "\n"
    legacy_text = load_text_if_exists(legacy_path).strip()
    if legacy_text:
        return legacy_text + "\n"
    return ""


def build_budget_from_args(args):
    runtime_budget = (
        memory_summary_budget_from_max(args.max_tokens)
        if args.max_tokens is not None
        else get_memory_summary_budget(PATHS)
    )
    return SummaryBudget(
        target_tokens=args.target_tokens if args.target_tokens is not None else runtime_budget["target_tokens"],
        warn_tokens=args.warn_tokens if args.warn_tokens is not None else runtime_budget["warn_tokens"],
        max_tokens=runtime_budget["max_tokens"],
        profile_tokens=args.profile_tokens,
        preferences_tokens=args.preferences_tokens,
        tips_tokens=args.tips_tokens,
        routes_tokens=args.routes_tokens,
        personal_memory_tokens=(
            args.personal_memory_tokens
            if args.personal_memory_tokens is not None
            else runtime_budget["personal_memory_tokens"]
        ),
        global_memory_tokens=(
            args.global_memory_tokens
            if args.global_memory_tokens is not None
            else (0 if args.personal_memory_tokens is not None else runtime_budget["global_memory_tokens"])
        ),
        project_memory_tokens=(
            args.project_memory_tokens
            if args.project_memory_tokens is not None
            else (0 if args.personal_memory_tokens is not None else runtime_budget["project_memory_tokens"])
        ),
        max_preferences=args.max_preferences,
        max_tips=args.max_tips,
        max_route_items=args.max_route_items,
        max_route_keywords=args.max_route_keywords,
        max_personal_memory_items=args.max_personal_memory_items,
    )


def main():
    ensure_state_layout(PATHS)
    args = parse_args()
    memory_index_path = Path(args.memory_index).expanduser()
    memory_summary_path = Path(args.memory_summary).expanduser()
    canonical_memory_registry_path = Path(args.canonical_memory_registry).expanduser()
    personal_memory_registry_path = Path(args.personal_memory_registry).expanduser()
    project_filter = build_project_context_filter(
        project_cwd=args.project_cwd,
        project_key=args.project_key,
        project_label=args.project_label,
    )

    memory_index_text = load_text_if_exists(memory_index_path)
    existing_summary_text = load_text_if_exists(memory_summary_path)
    personal_memory_text = (
        ""
        if args.no_personal_memory
        else load_memory_registry_text(canonical_memory_registry_path, personal_memory_registry_path)
    )
    personal_memory_items = parse_personal_memory_registry(
        personal_memory_text,
        language=LANGUAGE,
        project_filter=project_filter,
    )
    if not memory_index_text and not existing_summary_text and not personal_memory_items:
        print(
            localized(
                "已跳过：未找到记忆索引、已有摘要或个人记忆登记册",
                "skip: no memory index, existing summary, or personal memory registry found",
            )
        )
        return

    budget = build_budget_from_args(args)
    result = build_memory_summary(
        memory_index_text,
        existing_summary_text,
        budget,
        personal_memory_items=personal_memory_items,
        project_filter=project_filter,
    )
    if result.status == "over_budget":
        raise SystemExit(
            localized(
                "生成的记忆摘要仍超过预算：{} > {} ({})",
                "generated memory summary is still over budget: {} > {} ({})",
            ).format(
                result.estimated_tokens,
                budget.max_tokens,
                result.estimator,
            )
        )

    if args.print_only:
        print(result.text, end="")
    else:
        atomic_write_text(memory_summary_path, result.text)

    if LANGUAGE == "zh":
        print(
            "记忆摘要 状态={} 估算_tokens={} 估算器={} 目标={} 警戒={} 上限={}".format(
                result.status,
                result.estimated_tokens,
                result.estimator,
                budget.target_tokens,
                budget.warn_tokens,
                budget.max_tokens,
            )
        )
    else:
        print(
            "memory_summary status={} estimated_tokens={} estimator={} target={} warn={} max={}".format(
                result.status,
                result.estimated_tokens,
                result.estimator,
                budget.target_tokens,
                budget.warn_tokens,
                budget.max_tokens,
            )
        )


if __name__ == "__main__":
    main()
