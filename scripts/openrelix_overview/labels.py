"""Display-label mappings for overview asset and memory records."""

DISPLAY_TYPE = {
    "skill": "skills",
    "automation": "自动化",
    "playbook": "方法手册",
    "template": "模板",
    "knowledge_card": "知识卡",
    "review": "复盘",
}

DISPLAY_DOMAIN = {
    "general": "跨场景通用",
    "通用": "跨场景通用",
    "openrelix": "OpenRelix",
    "personal-asset-automation": "个人资产自动化",
    "open-source-branding": "开源品牌",
    "lark": "Lark",
    "android": "Android 开发",
    "Android": "Android 开发",
    "ios": "iOS 开发",
    "web": "Web 开发",
    "frontend": "前端开发",
    "backend": "后端服务",
    "design": "设计协作",
    "research": "研究分析",
    "infra": "基础设施",
    "ops": "工程运维",
    "planning": "规划设计",
    "规划": "规划设计",
    "collaboration": "协作沟通",
    "协作": "协作沟通",
}

DISPLAY_SCOPE = {
    "personal": "仅个人使用",
    "个人": "仅个人使用",
    "repo": "仓库场景复用",
    "仓库": "仓库场景复用",
    "team": "团队共享",
    "团队": "团队共享",
}

DISPLAY_STATUS = {
    "active": "活跃",
    "draft": "草稿",
    "retired": "停用",
}

DISPLAY_MEMORY_BUCKET = {
    "durable": "个人资产-长期记忆",
    "session": "个人资产-工作记忆",
    "low_priority": "个人资产-低优先记忆",
}

DISPLAY_MEMORY_TYPE = {
    "semantic": "语义",
    "procedural": "流程",
    "episodic": "事件记忆",
    "task": "任务",
    "mapping": "映射",
    "preference": "偏好",
    "rule": "规则",
}

DISPLAY_MEMORY_PRIORITY = {
    "high": "重点",
    "medium": "常规",
    "low": "低权重",
}

DISPLAY_TYPE_EN = {
    "skill": "Skill",
    "automation": "Automation",
    "playbook": "Playbook",
    "template": "Template",
    "knowledge_card": "Knowledge Card",
    "review": "Review",
}

DISPLAY_DOMAIN_EN = {
    "general": "Cross-scenario",
    "通用": "Cross-scenario",
    "跨场景通用": "Cross-scenario",
    "openrelix": "OpenRelix",
    "personal-asset-automation": "Personal asset automation",
    "个人资产自动化": "Personal asset automation",
    "open-source-branding": "Open-source branding",
    "开源品牌": "Open-source branding",
    "lark": "Lark",
    "android": "Android",
    "Android": "Android",
    "ios": "iOS",
    "web": "Web",
    "frontend": "Frontend",
    "backend": "Backend",
    "design": "Design",
    "research": "Research",
    "infra": "Infrastructure",
    "ops": "Operations",
    "planning": "Planning",
    "规划": "Planning",
    "collaboration": "Collaboration",
    "协作": "Collaboration",
}

DISPLAY_SCOPE_EN = {
    "personal": "Personal",
    "个人": "Personal",
    "repo": "Repo-scoped",
    "仓库": "Repo-scoped",
    "team": "Team",
    "团队": "Team",
}

DISPLAY_STATUS_EN = {
    "active": "Active",
    "draft": "Draft",
    "retired": "Retired",
}

DISPLAY_MEMORY_BUCKET_EN = {
    "durable": "Personal Asset - Long-term Memory",
    "session": "Personal Asset - Work Memory",
    "low_priority": "Personal Asset - Low-priority Memory",
}

DISPLAY_MEMORY_TYPE_EN = {
    "semantic": "Semantic",
    "procedural": "Procedure",
    "episodic": "Episodic",
    "task": "Task",
    "mapping": "Mapping",
    "preference": "Preference",
    "rule": "Rule",
}

MEMORY_TYPE_GROUP_ORDER = (
    "procedural",
    "semantic",
    "episodic",
    "rule",
    "mapping",
    "preference",
    "task",
)

DISPLAY_MEMORY_PRIORITY_EN = {
    "high": "Important",
    "medium": "Standard",
    "low": "Low Weight",
}

DISPLAY_DISCOVERED_ASSET_KIND = {
    "skill": "skills",
    "prompt": "提示词",
    "rule": "Codex 规则",
    "plugin": "插件",
    "automation": "启动项",
    "codex_skill": "Codex skills",
    "claude_skill": "Claude skills",
    "repo_skill": "仓库 skills",
    "external_repo_skill": "跨仓库 skills",
    "project_skill": "项目本地 skills",
    "codex_prompt": "自定义提示词",
    "codex_rule": "Codex 规则",
    "claude_plugin": "插件",
    "launch_agent": "启动项",
}

DISPLAY_DISCOVERED_ASSET_KIND_EN = {
    "skill": "Skills",
    "prompt": "Prompts",
    "rule": "Codex Rules",
    "plugin": "Plugins",
    "automation": "Automations",
    "codex_skill": "Codex Skills",
    "claude_skill": "Claude Skills",
    "repo_skill": "Repo Skills",
    "external_repo_skill": "External Repo Skills",
    "project_skill": "Project-Local Skills",
    "codex_prompt": "Codex Prompts",
    "codex_rule": "Codex Rules",
    "claude_plugin": "Claude Plugins",
    "launch_agent": "Launch Agents",
}


def display_label(kind, value, language=None, is_english_func=None, humanize_func=None):
    is_english_func = is_english_func or (lambda current_language: current_language == "en")
    humanize_func = humanize_func or (lambda current_value: str(current_value or ""))
    if is_english_func(language):
        mapping = {
            "type": DISPLAY_TYPE_EN,
            "domain": DISPLAY_DOMAIN_EN,
            "scope": DISPLAY_SCOPE_EN,
            "status": DISPLAY_STATUS_EN,
        }.get(kind, {})
        if value in mapping:
            return mapping[value]
        return humanize_func(value)

    mapping = {
        "type": DISPLAY_TYPE,
        "domain": DISPLAY_DOMAIN,
        "scope": DISPLAY_SCOPE,
        "status": DISPLAY_STATUS,
    }.get(kind, {})
    if value in mapping:
        return mapping[value]
    if kind in {"domain", "scope", "status"}:
        return humanize_func(value)
    return value


def display_memory_bucket(value, language=None, is_english_func=None, humanize_func=None):
    is_english_func = is_english_func or (lambda current_language: current_language == "en")
    humanize_func = humanize_func or (lambda current_value: str(current_value or ""))
    if is_english_func(language):
        if value in DISPLAY_MEMORY_BUCKET_EN:
            return DISPLAY_MEMORY_BUCKET_EN[value]
        return humanize_func(value) or "Uncategorized memory"
    if value in DISPLAY_MEMORY_BUCKET:
        return DISPLAY_MEMORY_BUCKET[value]
    return humanize_func(value) or "未分类记忆"


def display_memory_type(value, language=None, is_english_func=None, humanize_func=None):
    is_english_func = is_english_func or (lambda current_language: current_language == "en")
    humanize_func = humanize_func or (lambda current_value: str(current_value or ""))
    if is_english_func(language):
        if value in DISPLAY_MEMORY_TYPE_EN:
            return DISPLAY_MEMORY_TYPE_EN[value]
        return humanize_func(value) or "Uncategorized"
    if value in DISPLAY_MEMORY_TYPE:
        return DISPLAY_MEMORY_TYPE[value]
    return humanize_func(value) or "未分类"


def display_memory_priority(value, language=None, is_english_func=None, humanize_func=None):
    is_english_func = is_english_func or (lambda current_language: current_language == "en")
    humanize_func = humanize_func or (lambda current_value: str(current_value or ""))
    if is_english_func(language):
        if value in DISPLAY_MEMORY_PRIORITY_EN:
            return DISPLAY_MEMORY_PRIORITY_EN[value]
        return humanize_func(value) or "Unlabeled"
    if value in DISPLAY_MEMORY_PRIORITY:
        return DISPLAY_MEMORY_PRIORITY[value]
    return humanize_func(value) or "未标注"


def display_discovered_asset_kind(value, language=None, is_english_func=None, humanize_func=None):
    is_english_func = is_english_func or (lambda current_language: current_language == "en")
    humanize_func = humanize_func or (lambda current_value: str(current_value or ""))
    if is_english_func(language):
        if value in DISPLAY_DISCOVERED_ASSET_KIND_EN:
            return DISPLAY_DISCOVERED_ASSET_KIND_EN[value]
        return humanize_func(value) or "Unknown Assets"
    if value in DISPLAY_DISCOVERED_ASSET_KIND:
        return DISPLAY_DISCOVERED_ASSET_KIND[value]
    return humanize_func(value) or "未分类资产"
