#!/usr/bin/env python3

import csv
import json
import math
import os
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta
from functools import lru_cache
from html import escape
from pathlib import Path
from urllib.parse import unquote, urlparse

from asset_runtime import (
    atomic_write_json,
    atomic_write_text,
    ensure_state_layout,
    get_memory_mode,
    get_memory_summary_budget,
    get_runtime_language,
    get_runtime_paths,
    normalize_language,
    PREVIOUS_PUBLIC_APP_SLUG,
    render_path,
)
from build_codex_memory_summary import (
    DEFAULT_MAX_PERSONAL_MEMORY_ITEMS as MEMORY_SUMMARY_MAX_PERSONAL_MEMORY_ITEMS,
    DEFAULT_MAX_TOKENS as MEMORY_SUMMARY_MAX_TOKENS,
    DEFAULT_PERSONAL_MEMORY_TOKENS as MEMORY_SUMMARY_PERSONAL_MEMORY_TOKENS,
    DEFAULT_TARGET_TOKENS as MEMORY_SUMMARY_TARGET_TOKENS,
    DEFAULT_WARN_TOKENS as MEMORY_SUMMARY_WARN_TOKENS,
    PERSONAL_MEMORY_NOTE_LIMIT,
    PERSONAL_MEMORY_TITLE_LIMIT,
    estimate_tokens as estimate_summary_tokens,
)

PATHS = get_runtime_paths()
LANGUAGE = get_runtime_language(PATHS)
ROOT = PATHS.repo_root
REGISTRY_DIR = PATHS.registry_dir
REPORTS_DIR = PATHS.reports_dir
REVIEWS_DIR = PATHS.reviews_dir
CONSOLIDATED_DIR = PATHS.consolidated_daily_dir
RAW_DAILY_DIR = PATHS.raw_daily_dir
TOKEN_CACHE_PATH = REPORTS_DIR / "token-usage-cache.json"
CCUSAGE_TIMEZONE = "Asia/Shanghai"
CCUSAGE_WINDOW_DAYS = 14
AUTO_REFRESH_SECONDS = 1800
BACKFILL_LOOKBACK_DAYS = 14
BACKFILL_LEARN_WINDOW_DAYS = 7
LIVE_TOKEN_HOST = "127.0.0.1"
LIVE_TOKEN_PORT = 8765
LIVE_TOKEN_ENDPOINT = "http://{}:{}/token-usage".format(LIVE_TOKEN_HOST, LIVE_TOKEN_PORT)
LIVE_TOKEN_POLL_SECONDS = 300
LIVE_TOKEN_TIMEOUT_MS = 20000
PROJECT_GITHUB_URL = "https://github.com/openrelix/openrelix"
BRAND_DISPLAY_REPLACEMENTS = (
    ("scripts/openrelix.py.py", "scripts/openrelix.py"),
)
PROJECT_CONTEXT_VISIBLE_COUNT = 4
PROJECT_CONTEXT_DEFAULT_DAYS = 1
PROJECT_CONTEXT_MAX_DAYS = 7
SUMMARY_TERM_DEFAULT_DAYS = 1
SUMMARY_TERM_RANGE_DAYS = (1, 3, 7)
MEMORY_USAGE_WINDOW_DAYS = 7
PROJECT_CONTEXT_TOPIC_VISIBLE_COUNT = 4
TOKEN_METRIC_KEYS = {"today_token", "seven_day_token"}
PANEL_PATH_LABEL = render_path(REPORTS_DIR / "panel.html")
OVERVIEW_JSON_PATH_LABEL = render_path(REPORTS_DIR / "overview-data.json")
LOCAL_PATH_TRAILING_PUNCTUATION = ".,;!?)]}\"'"
LOCAL_PATH_TOKEN_RE = re.compile(
    r"(file://[^\s<>\"']+|~/[^\s<>\"']+|/[^\s<>\"']+)"
)


def normalize_brand_display_text(value):
    if not isinstance(value, str):
        return value
    text = value
    for source, target in BRAND_DISPLAY_REPLACEMENTS:
        text = text.replace(source, target)
    return text


def normalize_brand_display_payload(value):
    if isinstance(value, dict):
        return {
            normalize_brand_display_text(key): normalize_brand_display_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_brand_display_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(normalize_brand_display_payload(item) for item in value)
    return normalize_brand_display_text(value)

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "to",
    "as",
    "or",
    "new",
    "created",
    "system",
    "agents",
    "agnostic",
    "bootstrap",
    "scoped",
    "durable",
    "file",
    "files",
    "first",
    "used",
    "use",
    "into",
    "only",
    "when",
    "your",
    "local",
    "simple",
    "readable",
    "personal",
    "asset",
    "assets",
    "overview",
    "panel",
    "summary",
    "value",
    "note",
    "general",
    "active",
    "repo",
    "work",
    "task",
    "tasks",
    "helped",
    "capture",
    "reusable",
    "rebuild",
    "reporting",
    "designed",
    "stored",
}

TERM_ALIASES = {
    "codex": "Codex",
    "global": "全局",
    "librarian": "整理",
    "layer": "分层",
    "skill": "技能",
    "skills": "技能",
    "playbook": "方法",
    "automation": "自动化",
    "template": "模板",
    "workflow": "流程",
    "knowledge": "知识沉淀",
    "review": "复盘",
    "reviews": "复盘",
    "automation": "自动化",
    "config": "配置",
    "memory": "记忆",
    "memories": "记忆",
    "local": "本地",
    "reporting": "输出整理",
    "summary": "总结",
    "overview": "概览",
    "panel": "面板",
    "git": "Git",
    "repo": "仓库",
    "workflow": "流程",
    "knowledge": "知识沉淀",
    "operating": "工作方式",
    "model": "模型",
    "dashboard": "概览",
    "library": "资产库",
    "openrelix": "OpenRelix",
    PREVIOUS_PUBLIC_APP_SLUG: "OpenRelix",
    "douyin": "Douyin",
    "github": "GitHub",
    "launchagent": "LaunchAgent",
    "subreview": "subreview",
    "ppe": "PPE",
    "asr": "ASR",
    "scancamera": "ScanCamera",
}

SUMMARY_TERM_LABEL_EN = {
    "全局": "Global",
    "分层": "Layering",
    "整理": "Librarian",
    "技能": "Skill",
    "方法": "Playbook",
    "自动化": "Automation",
    "模板": "Template",
    "流程": "Workflow",
    "知识沉淀": "Knowledge",
    "复盘": "Review",
    "配置": "Config",
    "记忆": "Memory",
    "本地": "Local",
    "输出整理": "Reporting",
    "总结": "Summary",
    "概览": "Overview",
    "面板": "Panel",
    "仓库": "Repo",
    "工作方式": "Operating model",
    "模型": "Model",
    "资产库": "Asset library",
    "个人资产自动化": "Personal asset automation",
    "今日热词": "Today Hot Terms",
    "近 3 日热词": "Last 3 Days Hot Terms",
    "近 7 日热词": "Last 7 Days Hot Terms",
    "新人必备": "Newcomer essentials",
    "常用工具": "Common tools",
    "技术博客": "Technical blog",
    "看板": "Board",
    "埋点": "Instrumentation",
    "百度一下": "Baidu search",
    "实验": "Experiment",
    "扫一扫": "Scan",
    "效能": "Productivity",
    "AI工具": "AI tools",
    "AI经验": "AI experience",
    "工具": "Tools",
    "经验": "Experience",
}

CONTEXT_KEYWORD_EN = {
    "改名": "Rename",
    "上线梳理": "Release prep",
    "发布": "Release",
    "开源": "Open source",
    "短期记忆": "Short-term memory",
    "长期记忆": "Long-term memory",
    "记忆机制": "Memory mechanism",
    "注入预算": "Injection budget",
    "使用方式": "Usage",
    "商标": "Trademark",
    "中文商标": "Chinese trademark",
    "性能": "Performance",
    "适配成本": "Adapter cost",
    "对齐回滚": "Alignment rollback",
    "多 CLI": "multi-CLI",
    "多语言": "i18n",
    "后台服务": "Background service",
    "安装配置": "Install config",
    "锚点": "Anchor",
    "排版密度": "Layout density",
    "面板可视化": "Panel visualization",
    "数据同步": "Data sync",
    "记忆注入": "Memory injection",
    "预算": "Budget",
    "路线图": "Roadmap",
    "品牌": "Brand",
    "品牌升级": "Brand upgrade",
    "首版发布": "First release",
    "运行机制": "Runtime model",
    "窗口": "Window",
    "窗口学习": "Window learning",
}

FREEFORM_TEXT_EN = {
    "未知": "Unknown",
    "通用": "General",
    "个人资产自动化": "Personal asset automation",
    "协作沟通": "Collaboration",
    "Codex 全局工作手册": "Codex global operating manual",
    "个人资产整理技能": "Personal asset librarian skill",
    "抖音工作资产整理技能": "Douyin work asset librarian skill",
    "AI 资产概览链路": "AI asset overview pipeline",
    "Codex /subreview:run 外部评审循环": "Codex /subreview:run external review loop",
    "Token 图表 Apple 风格配色优化": "Token chart Apple-style color refinement",
    "记忆面板四列展开布局": "Memory panel four-column expanded layout",
    "飞书画板 CLI 能力检查": "Feishu Whiteboard CLI capability check",
    "资产与复盘面板布局优化": "Asset and review panel layout refinement",
    "资产面板 artifact 路径跳转": "Asset panel artifact path links",
    "Codex 独立评审命令落地": "Codex independent review command rollout",
    "个人资产系统初始化": "Personal asset system bootstrap",
    "沉淀一套稳定的全局工作方式，约束 Codex 的通用行为和本地资产边界。": (
        "Captures a stable global operating model for Codex behavior and local asset boundaries."
    ),
    "把复盘、方法、模板和流程整理成可持续复用的本地资产。": (
        "Turns reviews, methods, templates, and workflows into sustainable reusable local assets."
    ),
    "把抖音相关工作的经验沉淀为可复用条目，同时避免给仓库增加 git 负担。": (
        "Captures Douyin work experience as reusable entries without adding git burden to the repo."
    ),
    "把本地资产、复盘和 token 数据整理成一份可直接查看的概览和面板。": (
        "Turns local assets, reviews, and token data into a directly browsable overview and panel."
    ),
    "在 Codex CLI 里用 /subreview:run 驱动一个独立 Codex reviewer 评分，并让主 agent 迭代修复直到通过评审。": (
        "Uses /subreview:run in Codex CLI to drive an independent reviewer and iterate fixes until review passes."
    ),
}

FREEFORM_PHRASE_EN = {
    "个人资产": "personal asset",
    "资产概览": "asset overview",
    "概览链路": "overview pipeline",
    "工作手册": "operating manual",
    "整理技能": "librarian skill",
    "工作资产": "work asset",
    "复盘面板": "review panel",
    "资产面板": "asset panel",
    "记忆面板": "memory panel",
    "四列展开": "four-column expanded",
    "配色优化": "color refinement",
    "能力检查": "capability check",
    "路径跳转": "path links",
    "命令落地": "command rollout",
    "系统初始化": "system bootstrap",
    "独立评审": "independent review",
    "外部评审": "external review",
    "飞书画板": "Feishu Whiteboard",
    "抖音": "Douyin",
    "图表": "chart",
    "风格": "style",
    "配色": "color",
    "优化": "refinement",
    "链路": "pipeline",
    "概览": "overview",
    "面板": "panel",
    "资产": "asset",
    "记忆": "memory",
    "复盘": "review",
    "自动化": "automation",
    "技能": "skill",
    "方法": "playbook",
    "模板": "template",
    "通用": "general",
    "协作沟通": "collaboration",
}

DISPLAY_TYPE = {
    "skill": "技能",
    "automation": "自动化",
    "playbook": "方法",
    "template": "模板",
    "knowledge_card": "知识卡",
    "review": "复盘",
}

DISPLAY_DOMAIN = {
    "general": "跨场景通用",
    "通用": "跨场景通用",
    "douyin": "Douyin",
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
    "session": "个人资产-短期工作记忆",
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
    "high": "高优先",
    "medium": "中优先",
    "low": "低优先",
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
    "douyin": "Douyin",
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
    "session": "Personal Asset - Short-term Work Memory",
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
    "high": "High Priority",
    "medium": "Medium Priority",
    "low": "Low Priority",
}

PANEL_DEFAULT_LANGUAGE = LANGUAGE
PANEL_I18N_EN = {
    "OpenRelix 工作台": "OpenRelix Workbench",
    "OpenRelix": "OpenRelix",
    "只保留当前有效的复用信号：最近整理、核心指标，以及可继续下钻的窗口、记忆和资产明细。": (
        "Keep the currently useful reuse signals: recent synthesis, core metrics, "
        "and drill-down window, memory, and asset details."
    ),
    "阅读提示": "Reading Guide",
    "说明": "Help",
    "系统": "System",
    "浅色": "Light",
    "深色": "Dark",
    "面板快照": "Snapshot",
    "刚刚生成": "Generated just now",
    "实时刷新 Token": "Refresh Token",
    "正在查询 Token": "Checking Token",
    "先展示本地快照，再实时同步最新 Token。": "Showing the local snapshot first, then syncing the latest Token usage.",
    "页面已打开，正在同步最新 Token…": "Page opened. Syncing the latest Token usage...",
    "正在实时查询最新 Token…": "Querying the latest Token usage...",
    "本地 token 服务没有返回可用数据": "The local token service returned no usable data.",
    "ccusage 当前不可用": "ccusage is currently unavailable.",
    "实时 Token 暂时不可用，先展示最近一次成功缓存。": "Live Token data is unavailable. Showing the latest successful cache.",
    "实时 Token 不可用，当前展示": "Live Token data is unavailable. Showing",
    "的本地快照。": "local snapshot.",
    "Token 已刷新，": "Token refreshed, ",
    "更新。": "updated.",
    "数据来源：ccusage 日维度统计": "Source: ccusage daily stats",
    "来自 ccusage": "From ccusage",
    "暂未获取到 ccusage 的日维度统计": "ccusage daily stats are unavailable",
    "暂无数据。": "No data.",
    "暂无。": "None.",
    "暂无资产。": "No assets.",
    "暂无复盘。": "No reviews.",
    "暂无复用记录。": "No usage records.",
    "复盘文件": "Review File",
    "暂无摘要词。": "No summary terms.",
    "暂无窗口整理结果。": "No window synthesis results.",
    "暂无可归纳的项目上下文。": "No project context available.",
    "暂无关键词": "No keywords",
    "未分类上下文": "Uncategorized context",
    "暂无来源窗口": "No source window",
    "暂无来源文件": "No source file",
    "暂无工作区": "No workspace",
    "未命名记忆": "Untitled memory",
    "个人工作区": "Personal workspace",
    "Codex 本地环境": "Codex local environment",
    "个人资产系统": "Personal assets system",
    "CLI / 本地效率": "CLI / local productivity",
    "工作区": "Workspace",
    "窗口": "Window",
    "Codex 原生": "Codex Native",
    "时间未知": "Unknown date",
    "查看更多": "Show more",
    "收起更多": "Collapse",
    "查看更多内容": "Show more",
    "收起更多内容": "Collapse",
    "收起额外条目": "Collapse extra items",
    "收起更多资产": "Collapse more assets",
    "收起更多复盘": "Collapse more reviews",
    "收起更多记录": "Collapse more records",
    "收起更多上下文": "Collapse more contexts",
    "用户偏好": "User Preferences",
    "通用 tips": "General Tips",
    "任务组": "Task Group",
    "来自 User preferences，默认展示前 4 条": "From User preferences. Showing the first 4 by default.",
    "来自 General Tips，默认展示前 4 条": "From General Tips. Showing the first 4 by default.",
    "来自 MEMORY.md，默认展示前 4 个任务组": "From MEMORY.md. Showing the first 4 task groups by default.",
    "来自 User preferences，按卡片样式展示": "From User preferences. Shown as cards.",
    "来自 General Tips，按卡片样式展示": "From General Tips. Shown as cards.",
    "来自 MEMORY.md，按任务组展示": "From MEMORY.md. Shown as task groups.",
    "条偏好": "preferences",
    "条 tip": "tips",
    "个任务组": "task groups",
    "资产总数": "Total Assets",
    "活跃资产": "Active Assets",
    "任务复盘": "Task Reviews",
    "复用记录": "Usage Events",
    "节省时长": "Time Saved",
    "仓库场景资产": "Repo-scoped Assets",
    "今日 Token": "Today Token",
    "今日": "Today",
    "近 7 日 Token": "7-day Token",
    "Token 速览": "Token Overview",
    "7 日账单": "7-day Bill",
    "7 日均值": "7-day Average",
    "峰值日": "Peak Day",
    "缓存占输入": "Cached / Input",
    "输入": "Input",
    "缓存输入": "Cached Input",
    "输出": "Output",
    "推理输出": "Reasoning Output",
    "输入详情": "Input Details",
    "缓存详情": "Cache Details",
    "输出详情": "Output Details",
    "推理详情": "Reasoning Details",
    "总输入 Token": "Total input tokens",
    "暂无可比较日期": "No comparable day yet",
    "长期记忆": "Long-term Memory",
    "短期记忆": "Short-term Memory",
    "短期工作记忆": "Short-term Work Memory",
    "低优先记忆": "Low-priority Memory",
    "低优先级记忆": "Low-priority Memory",
    "个人资产记忆": "Personal Asset Memory",
    "记忆数量": "Memory Counts",
    "总数": "Total",
    "个人资产-长期记忆": "Personal Asset - Long-term Memory",
    "个人资产-短期记忆": "Personal Asset - Short-term Memory",
    "个人资产-短期工作记忆": "Personal Asset - Short-term Work Memory",
    "个人资产-低优先记忆": "Personal Asset - Low-priority Memory",
    "个人资产-低优先级记忆": "Personal Asset - Low-priority Memory",
    "每日窗口数": "Daily Windows",
    "资产注册表中的稳定条目": "Stable entries in the asset registry",
    "当前仍在使用的条目": "Entries still in active use",
    "本地保存的脱敏复盘": "Sanitized local reviews",
    "被记录下来的复用时刻": "Recorded reuse events",
    "复用带来的累计节省分钟数": "Total minutes saved by reuse",
    "绑定某个仓库或场景的条目": "Entries bound to a repo or scenario",
    "最近 7 天累计消耗": "Total usage in the last 7 days",
    "夜间整理沉淀出的长期可复用记忆": "Long-term reusable memories from nightly synthesis",
    "与当前需求相关的工作记忆": "Work memories related to the current task",
    "保留但优先级较低的内容": "Retained lower-priority content",
    "最近一次整理结果": "Latest Synthesis",
    "昨夜整理结果": "Last Night's Synthesis",
    "当日整理预览": "Today's Synthesis Preview",
    "今日整理结果": "Today's Synthesis",
    "每日整理结果": "Daily Synthesis",
    "每日资产账本": "Daily Asset Ledger",
    "今天哪些工作能复用？": "What work can be reused today?",
    "每日摘要": "Daily Summary",
    "Codex context 预算": "Codex Context Budget",
    "受控": "Bounded",
    "本地": "Local",
    "长期": "Long-term",
    "短期": "Short-term",
    "低优先": "Low-priority",
    "工作窗口": "Work Windows",
    "短期跟进": "Short-term Follow-ups",
    "低优先级": "Low-priority",
    "暂无夜间整理结果": "No nightly synthesis yet",
    "选择日期": "Select date",
    "选择整理日期": "Select synthesis date",
    "选择窗口日期": "Select window date",
    "该日期暂无整理结果。": "No synthesis for this date.",
    "未整理": "Not synthesized",
    "缺少整理结果": "Missing synthesis",
    "该日期还没有整理结果。可以复制命令在终端手动回溯。": (
        "This date has no synthesis yet. Copy the command and run it in a terminal to backfill it."
    ),
    "单日回溯": "Single-date backfill",
    "多日回溯": "Multi-day backfill",
    "复制命令": "Copy command",
    "已复制回溯命令": "Backfill command copied",
    "复制失败，请手动选择命令。": "Copy failed. Select the command manually.",
    "该日期暂无窗口整理结果。": "No window synthesis for this date.",
    "终版": "Final",
    "预览": "Preview",
    "手动": "Manual",
    "待生成": "Pending",
    "已生成": "Generated",
    "今日仍有活跃整理": "Active synthesis today",
    "保底摘要": "Fallback summary",
    "窗口": "Windows",
    "低优先级": "Low Priority",
    "整理窗口数": "Synthesized Windows",
    "整理长期记忆": "Long-term Memories",
    "整理短期记忆": "Short-term Memories",
    "整理低优先": "Low-priority Memories",
    "今日摘要": "Today Summary",
    "相关上下文": "Related Contexts",
    "日期": "Date",
    "关键指标": "Key Metrics",
    "活跃上下文": "Active Contexts",
    "整理日期": "Synthesis Date",
    "结构信号": "Structure Signals",
    "本期摘要词": "Summary Terms",
    "今日热词": "Today Hot Terms",
    "近 3 日热词": "Last 3 Days Hot Terms",
    "近 7 日热词": "Last 7 Days Hot Terms",
    "热词时间范围": "Hot terms date range",
    "本期小结": "Current Summary",
    "资产类型分布": "Asset Type Distribution",
    "项目 / 上下文分布": "Project / Context Distribution",
    "月度新增": "Monthly Additions",
    "适用层级": "Scope",
    "运行视图": "Runtime View",
    "记忆层": "Memory Layer",
    "资产层": "Asset Layer",
    "资产记忆": "Asset Memory",
    "账本概览": "Ledger Overview",
    "资产账本概览": "Asset Ledger Overview",
    "这里看的是已经登记到本地账本里的资产、复盘和复用记录，不是注入 Codex context 的记忆摘要。": (
        "This shows assets, reviews, and reuse records registered in the local ledger, not the memory summary injected into Codex context."
    ),
    "每日 Token 消耗": "Daily Token Usage",
    "今日 Token 构成": "Today Token Breakdown",
    "当前项目上下文": "Current Project Context",
    "来自本地资产系统的 nightly 整理与结构化登记册。": "From the local asset system's nightly synthesis and structured registry.",
    "来自 Codex 原生 memory summary 与 MEMORY.md。": "From Codex native memory_summary and MEMORY.md.",
    "Codex 原生记忆": "Codex Native Memory",
    "Codex 原生记忆-主题项": "Codex Native Memory - Topics",
    "Codex 原生记忆-偏好": "Codex Native Memory - Preferences",
    "Codex 原生记忆-通用 tips": "Codex Native Memory - General Tips",
    "Codex 原生记忆-任务组": "Codex Native Memory - Task Groups",
    "最近更新的资产": "Recently Updated Assets",
    "复用价值较高的资产": "High-value Reusable Assets",
    "最近复盘": "Recent Reviews",
    "最近复用记录": "Recent Usage Events",
    "最近形成的脱敏任务复盘": "Recent sanitized task reviews",
    "昨夜窗口概览": "Last Night's Window Overview",
    "当日窗口概览": "Today's Window Overview",
    "每日窗口概览": "Daily Window Overview",
    "最近一次窗口概览": "Latest Window Overview",
    "资产": "Asset",
    "类型": "Type",
    "项目 / 上下文": "Project / Context",
    "适用层级": "Scope",
    "更新时间": "Updated",
    "日期": "Date",
    "资产 ID": "Asset ID",
    "任务": "Task",
    "节省分钟": "Minutes Saved",
    "价值分": "Value Score",
    "估算节省": "Estimated Saved",
    "证据": "Evidence",
    "问题": "Questions",
    "结论": "Conclusions",
    "问题摘要": "Question Summary",
    "结论摘要": "Conclusion Summary",
    "窗口信息": "Window Info",
    "关键词": "Keywords",
    "最近问题": "Recent Questions",
    "最近结论": "Recent Conclusions",
    "最近活动": "Recent Activity",
    "点开看详情": "Open details",
    "收起详情": "Collapse details",
    "原始窗口 JSON": "Raw Window JSON",
    "会话 JSONL": "Session JSONL",
    "原始窗口 ID": "Raw Window ID",
    "当前目录": "Current Directory",
    "启动时间": "Started At",
    "原始窗口": "Raw Window",
    "会话文件": "Session File",
    "最近工作区": "Recent Workspace",
    "代表问题": "Representative Question",
    "最近结论": "Recent Takeaway",
    "需求 / 主题": "Need / Topic",
    "来源窗口": "Source Window",
    "来源文件": "Source File",
    "关联上下文": "Related Context",
    "首次添加": "First Added",
    "最近更新": "Recently Updated",
    "原生归档": "Native Archive",
    "整理命中": "Synthesis Hits",
    "高优先": "High Priority",
    "中优先": "Medium Priority",
    "语义": "Semantic",
    "流程": "Procedure",
    "事件记忆": "Episodic",
    "规则": "Rule",
    "偏好": "Preference",
    "映射": "Mapping",
    "未分类": "Uncategorized",
    "未标注": "Unlabeled",
    "技能": "Skill",
    "自动化": "Automation",
    "方法": "Playbook",
    "模板": "Template",
    "知识卡": "Knowledge Card",
    "跨场景通用": "Cross-scenario",
    "Android 开发": "Android",
    "iOS 开发": "iOS",
    "Web 开发": "Web",
    "前端开发": "Frontend",
    "后端服务": "Backend",
    "设计协作": "Design",
    "研究分析": "Research",
    "基础设施": "Infrastructure",
    "工程运维": "Operations",
    "规划设计": "Planning",
    "协作沟通": "Collaboration",
    "仅个人使用": "Personal",
    "仓库场景复用": "Repo-scoped",
    "团队共享": "Team",
    "活跃": "Active",
    "草稿": "Draft",
    "停用": "Retired",
    "统计什么": "What it measures",
    "类型说明": "Type guide",
    "数据来源": "Source",
    "怎么算": "How it is calculated",
    "怎么看": "How to read it",
    "注意": "Note",
    "含义": "Meaning",
    "包含什么": "What it includes",
    "当前来源": "Current source",
    "当前计数": "Current counts",
    "关系": "Relationship",
    "区别": "Difference",
    "排序方式": "Sort order",
    "列含义": "Column meaning",
    "生成方式": "How it is generated",
    "来源": "Source",
    "不包含": "Excluded",
    "补充信息": "Additional info",
    "当前说明": "Current note",
    "标签含义": "Label meaning",
    "和上面的区别": "Difference from above",
    "为什么会看到 Codex 本地环境": "Why Codex local environment appears",
    "语言切换": "Language switch",
    "配色切换": "Theme switch",
    "页面导览": "Page navigation",
    "高价值": "High value",
    "中价值": "Medium value",
    "观察中": "Watching",
    "从资产、标签和复盘内容中提炼": "Extracted from assets, tags, and reviews",
    "从全量资产登记册、复盘和复用记录中提炼": (
        "Extracted from the full asset registry, reviews, and usage records"
    ),
    "方便快速浏览当前阶段的沉淀情况": "A quick read on the current asset state",
    "已登记到资产注册表的稳定资产总数。": "Total stable assets registered in the asset registry.",
    "state root 下的 registry/assets.jsonl。": "registry/assets.jsonl under the state root.",
    "raw 对话、日志、报表，以及还没登记成资产的临时内容。": (
        "Raw conversations, logs, reports, and temporary content that has not been registered as an asset."
    ),
    "状态为 active 的资产数量。": "Number of assets whose status is active.",
    "活跃表示当前仍建议继续复用，不代表当天一定刚被使用。": (
        "Active means the asset is still recommended for reuse; it does not mean it was used today."
    ),
    "本地保存的脱敏任务复盘数量。": "Number of sanitized task reviews saved locally.",
    "state root 下的 reviews/ 目录；卡片里的“复盘文件”可以直接打开对应 Markdown。": (
        "The reviews/ directory under the state root; the Review File link opens the corresponding Markdown."
    ),
    "已经被记录下来的资产复用事件总数。": "Total recorded asset reuse events.",
    "state root 下的 registry/usage_events.jsonl。": "registry/usage_events.jsonl under the state root.",
    "按复用记录和近期工作命中自动估算的分钟数": (
        "Minutes estimated from reuse events and recent work matches"
    ),
    "按显式复用记录、近期窗口命中和资产类型基准自动估算的节省分钟数。": (
        "Estimated minutes saved from explicit reuse records, recent window matches, and asset-type baselines."
    ),
    "这不是精确测速；它用于排序和趋势观察，原始 usage event 里的 minutes_saved 只作为强证据之一。": (
        "This is not an exact benchmark; it is for ranking and trend observation, with minutes_saved in raw usage events as one strong signal."
    ),
    "scope = repo 的资产数量。": "Number of assets where scope = repo.",
    "这类资产通常绑定某个仓库、模块或固定工作场景。": (
        "These assets are usually tied to a repo, module, or fixed work scenario."
    ),
    "ccusage 最新一天的总 Token 消耗。": "Total Token usage on the latest ccusage day.",
    "输入、缓存输入、输出和推理输出都会计入总量。": (
        "Input, cached input, output, and reasoning output are all included in the total."
    ),
    "ccusage 最近 7 天每日总 Token 的累计值。": "Sum of ccusage daily total Tokens over the last 7 days.",
    "这是滚动 7 日窗口，不是自然周。": "This is a rolling 7-day window, not a calendar week.",
    "最近一次窗口整理里纳入统计的窗口数。": "Number of windows included in the latest window synthesis.",
    "优先来自 daily capture；原始明细缺失时会退回最近一次 nightly summary。": (
        "Uses daily capture first; if raw details are missing, it falls back to the latest nightly summary."
    ),
    "把 ccusage 的日维度数据再加工成 7 日账单、7 日均值、峰值日和缓存占输入等快速判断信号。": (
        "Turns ccusage daily data into quick signals like 7-day estimated bill, 7-day average, peak day, and cached/input ratio."
    ),
    "上方两张大卡看总量，速览区看变化和结构，下面的每日 / 今日柱条可以 hover 到具体构成。": (
        "Use the two large cards for totals, the overview for change and structure, and hover the daily/today bars for breakdowns."
    ),
    "缓存输入是输入 Token 的子集，不应和输入、输出直接相加。": (
        "Cached input is a subset of input tokens and should not be added directly to input and output."
    ),
    "来自资产注册表的稳定条目": "Stable entries from the asset registry",
    "统计来自 assets.jsonl 的全部稳定资产，不限当前仓库；只有已登记的条目会进入这里，raw、log、report 和单次对话不会计入。": (
        "Counts all stable assets from assets.jsonl, not only the current repo. Only registered entries appear here; "
        "raw captures, logs, reports, and one-off chats are excluded."
    ),
    "根据资产路径与最近工作自动归纳": "Inferred from asset paths and recent work",
    "按复用层级分类": "Grouped by reuse scope",
    "可切换最近 1-7 天；项目内按需求 / 主题二次归类": (
        "Switch between the last 1-7 days; each project is grouped by need/topic."
    ),
    "按窗口区分当天问题与结论": "Questions and conclusions grouped by window",
    "可跨天复用的条目": "Reusable across days",
    "更偏当天任务推进": "More relevant to today's work",
    "保留但优先级较低": "Retained with lower priority",
    "最近一次变更的资产条目": "Assets changed most recently",
    "按复用记录和手工复用次数排序": "Sorted by recorded and manual reuse",
    "按自动估算价值分排序": "Sorted by automatically estimated value score",
    "用于证明某个已有条目在任务里发挥了作用": "Shows where an existing asset was reused in real work",
    "看长期可复用资产的增长，而不是看和 AI 聊了多少次。": (
        "Track growth in long-lived reusable assets, not chat volume."
    ),
    "优先关注复用证据和估算节省，这两个指标最能体现沉淀是否有效。": (
        "Prioritize reuse evidence and estimated saved time; they best show whether the system is working."
    ),
    "复盘内容最好能对应到交付、排障、评审质量或风险控制中的具体价值。": (
        "Reviews are most useful when tied to delivery, debugging, review quality, or risk control."
    ),
    "只有当条目稳定、低风险、适合共享时，再从个人范围提升到仓库或团队范围。": (
        "Promote entries from personal to repo or team scope only when stable, low-risk, and shareable."
    ),
    "对照“Codex 原生记忆”和“个人资产记忆”看：前者偏模型长期记忆，后者偏夜间整理和来源追踪。": (
        "Compare Codex Native Memory with Personal Asset Memory: the former is closer to long-term model memory, "
        "while the latter is nightly synthesis with source tracing."
    ),
    "统计口径": "Counting rule",
    "对应项目 / 条目": "Related projects / items",
    "当前优先使用原始 daily capture。": "Currently using the raw daily capture first.",
    "当前缺少原始 daily capture，已退回最近一次 nightly summary。": (
        "Raw daily capture is missing; falling back to the latest nightly summary."
    ),
    "当前还没有最近一次整理；生成后这里会自动切成摘要卡。": (
        "No recent synthesis yet; this area will switch to a summary card after generation."
    ),
    "还没有沉淀出记忆条目，先用窗口级概览帮助回看当天上下文。": (
        "No memory items were captured yet; use the window overview to review that day's context."
    ),
    "每条已登记资产最终落到哪个项目 / 上下文标签。这里数的是资产条目，不是窗口数。": (
        "Shows the project/context label assigned to each registered asset. This counts asset entries, not windows."
    ),
    "先看 artifact_paths：如果能识别出真实仓库项目，就直接记仓库名。": (
        "Check artifact_paths first: if a real repo project is identifiable, use the repo name."
    ),
    "仓库项目推不出时，优先使用资产自己的 domain 作为业务归属。": (
        "When the repo project cannot be inferred, prefer the asset's own domain as its context."
    ),
    "只有 repo project 和 domain 都不足以归类时，才从 title、value_note、notes、tags、source_task 做文本推断；再不行才回退到 ~/.codex、state root 这类特殊上下文。": (
        "Only when repo project and domain are insufficient, infer from title, value_note, notes, tags, and source_task; then fall back to special contexts such as ~/.codex or the state root."
    ),
    "只有在业务项目和 domain 都无法归类时，且资产文件实际落在 ~/.codex 下，例如 skills、prompts、scripts、config，才会算到 Codex 本地环境。": (
        "Codex local environment is used only when no business project/domain fits and the asset lives under ~/.codex, such as skills, prompts, scripts, or config."
    ),
    "按资产的 created_at 月份统计新增条目数。": "Counts new entries by the asset created_at month.",
    "这里看的是首次登记时间，不是最近更新时间。": "This uses first registration time, not latest update time.",
    "按 scope 字段统计资产的复用范围。": "Counts asset reuse scope by the scope field.",
    "仅个人使用：更偏个人习惯、环境配置或私有工作方式。": (
        "Personal: mostly personal habits, environment config, or private working style."
    ),
    "仓库场景复用：绑定某个仓库、业务线或固定场景。": (
        "Repo-scoped: tied to a repo, business line, or fixed scenario."
    ),
    "团队共享：适合多人共同遵守或复用。": "Team: suitable for multiple people to follow or reuse.",
    "从资产标题、类型、领域、备注、复盘文本和复用记录里抽词。": (
        "Extracted from asset titles, types, domains, notes, review text, and usage events."
    ),
    "从所选日期范围内的窗口整理、资产标题、领域、备注、复盘文本和复用记录里抽词。": (
        "Extracted from window synthesis, asset titles, domains, notes, review text, and usage records in the selected date range."
    ),
    "时间范围": "Time range",
    "不是固定最近几天；这里是当前 state root 里已登记内容的全量快照。": (
        "This is not a fixed recent-day window; it is the full current snapshot of registered content in the state root."
    ),
    "默认展示今日热词，可切换近 3 日和近 7 日。": (
        "Shows today by default and can switch to the last 3 or 7 days."
    ),
    "默认今日，可切换近 3 日 / 近 7 日": "Today by default; switch to last 3 / 7 days",
    "它会随资产、复盘或复用记录新增、修改而变化；每日整理请看“今日摘要 / 每日窗口概览”。": (
        "It changes as assets, reviews, or usage records are added or updated; use Today Summary / Daily Window Overview for daily synthesis."
    ),
    "它会随当天窗口整理、资产、复盘或复用记录新增、修改而变化。": (
        "It changes as today's window synthesis, assets, reviews, or usage records are added or updated."
    ),
    "字越大代表出现频次越高。这是主题提示，不代表严格的主题建模结果。": (
        "Larger text means higher frequency. This is a topic hint, not strict topic modeling."
    ),
    "按当前资产数量、活跃状态、最近上下文、Token 和夜间整理结果拼出几条快速结论。": (
        "Builds quick takeaways from asset counts, active status, recent context, Token usage, and nightly synthesis."
    ),
    "它适合快速扫一眼，不替代下面的明细面板。": (
        "Use it for a quick scan; it does not replace the detail panels below."
    ),
    "ccusage 的日维度统计。": "ccusage daily stats.",
    "按日期展示最近几天的 Token 消耗趋势；页面打开后会先显示快照，再尝试刷新实时值。": (
        "Shows recent Token usage by date; the page shows a snapshot first, then tries to refresh live values."
    ),
    "ccusage 最新一天的 breakdown。": "The latest daily breakdown from ccusage.",
    "把最新一天的总 Token 拆成输入、缓存输入、输出和推理输出。": (
        "Breaks the latest day's total Token usage into input, cached input, output, and reasoning output."
    ),
    "最近捕获到的窗口，会先按项目 / 上下文聚合，再展示每组的窗口数、问题数和结论数。": (
        "Recent captured windows are grouped by project/context, then shown with window, question, and conclusion counts."
    ),
    "优先从窗口 cwd 推 project_label：先认 Git 根目录，再认常见项目标记。": (
        "Infer project_label from the window cwd first: Git roots first, then common project markers."
    ),
    "cwd 推不出时，才回退到问题摘要、结论摘要和关键词做文本推断。": (
        "Only if cwd is insufficient, fall back to question summaries, conclusion summaries, and keywords."
    ),
    "同名项目会合并，按最近活动时间排序。": (
        "Projects with the same name are merged and sorted by latest activity."
    ),
    "这里数的是窗口上下文；上面的 项目 / 上下文分布 数的是资产条目。": (
        "This counts window context; Project / Context Distribution above counts asset entries."
    ),
    "这是按日期切换的每日整理摘要卡，默认展示今天。": (
        "A daily synthesis card switchable by date, defaulting to today."
    ),
    "日期选择器和摘要主结论。": "Date selector and main summary takeaway.",
    "窗口数、个人资产-长期记忆、个人资产-短期记忆、个人资产-低优先级记忆。": (
        "Window count, personal asset long-term memories, personal asset short-term memories, and personal asset low-priority memories."
    ),
    "最近相关的上下文标签。": "Recently related context labels.",
    "这些数字来自当前整理结果，用来快速判断今天沉淀了多少内容。": (
        "These numbers come from the selected synthesis and help estimate how much was captured that day."
    ),
    "当前登记册中 bucket = durable 的长期记忆，按近 7 日估算使用频率排序。": (
        "Long-term memories where bucket = durable in the current registry, sorted by estimated 7-day usage frequency."
    ),
    "state root 下的 registry/memory_items.jsonl；同一条记忆跨天重复出现时会合并计算。": (
        "registry/memory_items.jsonl under the state root; repeated memories across days are merged."
    ),
    "这里展示的是当前主视图对应的整理结果；顶部指标卡统计的是 registry/memory_items.jsonl 的当前数量。": (
        "This shows the synthesis behind the current main view; top metric cards count the current registry/memory_items.jsonl state."
    ),
    "频率来自近 7 日窗口匹配：来源窗口直接命中权重最高，标题、关键词、说明与历史窗口摘要匹配会按相关度加权，项目上下文只做小幅加分。": (
        "Frequency comes from matching the last 7 days of windows: direct source windows carry the highest weight; title, keywords, and notes are weighted by relevance to historical summaries; project context adds only a small boost."
    ),
    "当前登记册中 bucket = session 的短期工作记忆，按近 7 日估算使用频率排序。": (
        "Short-term work memories where bucket = session in the current registry, sorted by estimated 7-day usage frequency."
    ),
    "更偏当前需求推进，未必适合长期沉淀。": (
        "More relevant to the current task and not always worth long-term capture."
    ),
    "这类内容对当前任务推进有帮助，但未必适合长期沉淀。": (
        "These help the current task but may not be suitable for long-term capture."
    ),
    "最近一次 nightly summary 里的 low_priority bucket 条目。": (
        "Low-priority bucket items from the latest nightly summary."
    ),
    "保留但优先级较低，通常不是第一推荐路径。": (
        "Retained with lower priority and usually not the primary recommended path."
    ),
    "保留但优先级较低，通常不作为主路径提示。": (
        "Retained with lower priority and usually not the primary path."
    ),
    "按近 7 日估算使用频率排序；同一条记忆跨天重复出现时，会归并展示首次添加和最近更新。": (
        "Sorted by estimated 7-day usage frequency. Repeated memories across days are merged with first-added and latest-updated dates."
    ),
    "基于 registry/memory_items.jsonl 的整理日志，按记忆签名归并出的当前记忆视图。": (
        "Current memory view grouped by memory signature from registry/memory_items.jsonl synthesis logs."
    ),
    "按记忆签名归并后，bucket = durable 的个人资产-长期记忆数量。": (
        "Count of Personal Asset - Long-term Memory items after grouping by memory signature where bucket = durable."
    ),
    "按记忆签名归并后，bucket = session 的个人资产-短期工作记忆数量。": (
        "Count of Personal Asset - Short-term Work Memory items after grouping by memory signature where bucket = session."
    ),
    "按记忆签名归并后，bucket = low_priority 的个人资产-低优先记忆数量。": (
        "Count of Personal Asset - Low-priority Memory items after grouping by memory signature where bucket = low_priority."
    ),
    "它和个人资产记忆都来自本地 Codex 工作，但前者更接近模型会读取的长期摘要，后者是夜间整理后的结构化日志。": (
        "It and Personal Asset Memory both come from local Codex work, but the former is closer to model-readable long-term summaries while the latter is structured nightly synthesis."
    ),
    "个人资产记忆偏近期窗口整理、来源追踪、工作区定位。": (
        "Personal Asset Memory focuses on recent window synthesis, source tracing, and workspace location."
    ),
    "按和个人资产-长期记忆一致的卡片样式展示，便于和 nightly 整理出的记忆对齐比较。": (
        "Uses the same card style as Personal Asset - Long-term Memory, so it can be compared with nightly memory."
    ),
    "首次添加：这条记忆第一次进入整理日志的日期。": (
        "First added: the date this memory first entered the synthesis log."
    ),
    "最近更新：最近一次被 nightly 整理再次命中的日期。": (
        "Recently updated: the latest date this memory was hit again by nightly synthesis."
    ),
    "7日频率：近 7 日窗口中直接来源、文本相关和上下文相关的加权结果。": (
        "7-day frequency: a weighted result from direct sources, text relevance, and context relevance in the last 7 days of windows."
    ),
    "如果当前页还能定位到来源窗口，会提供页内跳转；否则回退到原始窗口 JSON 或本地工作区链接。": (
        "If the source window can be located on this page, an in-page jump is shown; otherwise it falls back to the raw window JSON or local workspace link."
    ),
    "原生记忆偏长期规则、稳定 workflow、历史偏好；nightly 记忆偏最近窗口整理结果。": (
        "Native memory leans toward long-term rules, stable workflows, and historical preferences; nightly memory leans toward recent window synthesis."
    ),
    "看差异时，优先看来源文件和上下文标签，不要只看数量。": (
        "When comparing, prioritize source files and context labels, not just counts."
    ),
    "直接读取 Codex 原生 memory summary 里的 User preferences。": (
        "Reads User preferences directly from the Codex native memory summary."
    ),
    "直接读取 Codex 原生 memory summary 里的 General Tips。": (
        "Reads General Tips directly from the Codex native memory summary."
    ),
    "更偏通用工作方法和排障路径，和偏好模块分开看。": (
        "Mostly general working methods and troubleshooting paths; read it separately from preferences."
    ),
    "读取 MEMORY.md 里的 Task Group 索引，展示历史任务组和对应来源。": (
        "Reads the Task Group index in MEMORY.md and shows historical task groups with their sources."
    ),
    "它更像长期主题目录，不等同于某一天的 nightly memory。": (
        "This is closer to a long-term topic directory, not a single day's nightly memory."
    ),
    "按 updated_at 倒序，展示最近改动过的资产。": (
        "Sorted by updated_at descending, showing recently changed assets."
    ),
    "按自动估算价值分倒序；分数由显式复用、近期窗口命中、估算节省分钟、资产类型基准和最近维护信号组成。": (
        "Sorted by estimated value score descending; the score combines explicit reuse, recent window matches, estimated saved minutes, asset-type baselines, and recent maintenance signals."
    ),
    "价值分衡量“这个资产是否持续减少重复工作或降低出错成本”；估算节省是分钟级近似，不需要用户手工维护 reuse_count。": (
        "Value score estimates whether the asset keeps reducing repeated work or error cost; estimated saved time is a minute-level approximation and does not require manually maintaining reuse_count."
    ),
    "显式复用记录权重最高；窗口命中是弱证据；没有直接证据的资产只保留类型和维护活跃度带来的潜在价值。": (
        "Explicit reuse records carry the highest weight; window matches are weaker evidence; assets without direct evidence keep only potential value from type and maintenance activity."
    ),
    "按复盘里的日期和任务名倒序展示最近条目。": (
        "Sorted by review date and task name descending, newest first."
    ),
    "按 date、asset_id、task 倒序展示最近事件。": (
        "Sorted by date, asset_id, and task descending, newest first."
    ),
    "它证明某个已有资产在实际任务里起过作用，但不等于自动精确量化收益。": (
        "It proves an existing asset was useful in real work, but it is not an automatic exact ROI measurement."
    ),
    "最近一次窗口整理里的窗口级明细。每张卡对应一个窗口，而不是一个资产。": (
        "Window-level details from the latest window synthesis. Each card represents one window, not one asset."
    ),
    "工作窗口、长期记忆、短期跟进、低优先级记忆。": (
        "Work windows, long-term memory, short-term follow-ups, and low-priority memory."
    ),
    "原生记忆偏长期规则、稳定 workflow、历史 rollout 结论。": (
        "Native memory leans toward long-term rules, stable workflows, and historical rollout conclusions."
    ),
    "用户偏好、通用 tips 和任务组已经拆到独立模块。": (
        "User preferences, general tips, and task groups are split into separate modules."
    ),
    "项目 / 上下文：资产最终归到的 display_context。": (
        "Project / Context: the final display_context assigned to the asset."
    ),
    "适用层级：scope 的展示值。": "Scope: the display value of scope.",
    "复用记录：这个资产已经被记录过多少次 usage event。": (
        "Usage events: how many times this asset has been recorded in usage events."
    ),
    "cwd / project_label、问题数、结论数。": (
        "cwd / project_label, question count, and conclusion count."
    ),
    "问题摘要、结论摘要、关键词。": "Question summary, conclusion summary, and keywords.",
    "最近问题和最近结论片段。": "Recent question and recent conclusion snippets.",
}


def current_language(language=None):
    return normalize_language(language or LANGUAGE)


def is_english(language=None):
    return current_language(language) == "en"


def localized(zh_text, en_text="", language=None):
    if not is_english(language):
        return zh_text
    return en_text or PANEL_I18N_EN.get(str(zh_text or ""), str(zh_text or ""))


def plural_en(count, singular, plural=None):
    number = safe_int(count)
    word = singular if number == 1 else (plural or "{}s".format(singular))
    return "{} {}".format(number, word)


CONTEXT_LABEL_EN = {
    "个人工作区": "Personal workspace",
    "Codex 本地环境": "Codex local environment",
    "个人资产系统": "Personal assets system",
    "CLI / 本地效率": "CLI / local productivity",
    "未分类上下文": "Uncategorized context",
    "暂无工作目录": "No working directory",
    "时间未知": "Unknown time",
}
CONTEXT_LABEL_ZH = {value: key for key, value in CONTEXT_LABEL_EN.items()}


def canonical_context_label_zh(label):
    value = str(label or "")
    return CONTEXT_LABEL_ZH.get(value, value)


def localized_context_label(label, language=None):
    label = normalize_brand_display_text(label)
    zh_label = normalize_brand_display_text(canonical_context_label_zh(label))
    en_label = normalize_brand_display_text(CONTEXT_LABEL_EN.get(zh_label, str(label or "")))
    return localized(zh_label, en_label, language)


def localized_topic_label(label, language=None):
    label = normalize_brand_display_text(label)
    return localized(label, normalize_brand_display_text(CONTEXT_TOPIC_LABEL_EN.get(str(label or ""), str(label or ""))), language)


def contains_cjk(text):
    return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))


def localized_context_keyword(keyword, language=None):
    text = normalize_brand_display_text(str(keyword or "")).strip()
    if not text or not is_english(language):
        return text
    if text in CONTEXT_KEYWORD_EN:
        return CONTEXT_KEYWORD_EN[text]
    for source, target in CONTEXT_KEYWORD_EN.items():
        text = text.replace(source, target)
    return text


def english_context_preview(text, keywords=None, label="Focus"):
    normalized = normalize_brand_display_text(str(text or ""))
    if not contains_cjk(normalized):
        return normalized

    terms = []
    for keyword in keywords or []:
        candidate = localized_context_keyword(keyword, language="en")
        if candidate and candidate not in terms and not contains_cjk(candidate):
            terms.append(candidate)

    for source, target in CONTEXT_KEYWORD_EN.items():
        if source in normalized and target not in terms:
            terms.append(target)

    for token in re.findall(r"[A-Za-z][A-Za-z0-9+#./-]{1,}", normalized):
        candidate = normalize_brand_display_text(token)
        if candidate.lower() in STOPWORDS:
            continue
        if candidate not in terms:
            terms.append(candidate)

    if terms:
        return "{}: {}.".format(label, ", ".join(terms[:6]))
    return "{}: captured from the original Chinese window.".format(label)


def localized_record_field(item, field, language=None, default=""):
    if not isinstance(item, dict):
        return default

    if is_english(language):
        candidates = (
            "{}_en".format(field),
            "display_{}_en".format(field),
            field,
        )
    else:
        candidates = (
            "{}_zh".format(field),
            "display_{}".format(field),
            field,
        )

    for key in candidates:
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return normalize_brand_display_text(text)
    return normalize_brand_display_text(default)


ASSET_TYPE_DESCRIPTIONS = {
    "skill": "供 Codex 在特定场景下调用的技能包，通常对应一个可发现的 SKILL。",
    "automation": "脚本、命令、后台任务或流水线，能替你自动完成一段固定工作。",
    "playbook": "工作手册、规则、检查清单、排障路径等可复用的方法论。",
    "template": "可直接复用的结构化模板，例如文档模板、提示词模板或条目录入模板。",
    "knowledge_card": "较小颗粒的知识卡片，用来记录稳定结论、模块映射或判断规则。",
    "review": "经过脱敏整理、可回看复用的任务复盘。",
}
ASSET_TYPE_DESCRIPTIONS_EN = {
    "skill": "A callable Codex skill package for a specific scenario, usually backed by a discoverable SKILL.",
    "automation": "A script, command, background job, or pipeline that automates a fixed workflow.",
    "playbook": "Reusable methods such as operating guides, rules, checklists, and troubleshooting paths.",
    "template": "Reusable structured templates such as docs, prompts, or entry formats.",
    "knowledge_card": "A compact knowledge card for stable conclusions, module mappings, or decision rules.",
    "review": "A sanitized task review that can be revisited and reused.",
}
ASSET_TYPE_GUIDE_ORDER = (
    "playbook",
    "automation",
    "skill",
    "template",
    "knowledge_card",
    "review",
)
ASSET_VALUE_BASE_MINUTES = {
    "automation": 18,
    "skill": 14,
    "template": 12,
    "playbook": 10,
    "review": 8,
    "knowledge_card": 6,
}
ASSET_VALUE_BASE_SCORE = {
    "automation": 22,
    "skill": 18,
    "template": 16,
    "playbook": 14,
    "review": 10,
    "knowledge_card": 8,
}
ASSET_VALUE_COMPLEXITY_RULES = (
    (("review", "审阅", "cr", "评审", "10/10", "subreview"), 1.25),
    (("debug", "排障", "bug", "fix", "修复", "错误", "报错", "失败"), 1.22),
    (("automation", "自动化", "pipeline", "launchagent", "脚本", "nightly"), 1.18),
    (("dashboard", "panel", "overview", "可视化", "面板", "概览"), 1.14),
    (("docs", "document", "whiteboard", "collaboration", "文档", "白板", "协作"), 1.1),
)
ASSET_VALUE_STOP_TERMS = {
    "ai",
    "asset",
    "assets",
    "codex",
    "general",
    "local",
    "personal",
    "skill",
    "skills",
    "workflow",
    "复用",
    "资产",
    "技能",
    "方法",
}

CONTEXT_TEXT_RULES = [
    (
        "个人资产系统",
        (
            "个人资产",
            "资产系统",
            "资产概览",
            "assets.jsonl",
            "usage_events",
            "memory_items",
            "overview",
            "panel",
            "nightly",
        ),
    ),
    (
        "CLI / 本地效率",
        (
            "iterm",
            "cli",
            "终端",
            "快捷键",
            "shell",
            "zsh",
            "bash",
            "行首",
            "行尾",
        ),
    ),
    (
        "Codex 本地环境",
        (
            ".codex",
            "codex",
            "mcp",
            "agents.md",
            "config.toml",
            "plugin",
            "marketplace",
            "auth.json",
            "token",
        ),
    ),
]

CONTEXT_TOPIC_RULES = [
    (
        "移动端扫描/录制链路",
        (
            "扫一扫",
            "scan",
            "二维码",
            "录制",
            "record",
            "长按录制",
        ),
    ),
    (
        "移动端编译/类型错误",
        (
            "[KMP_CLI_LOG]",
            "unresolved reference",
            "compile",
            "编译",
            "飘红",
            "报错",
        ),
    ),
    (
        "性能与体验评审",
        (
            "视觉搜索",
            "视搜",
            "visual search",
            "visualsearch",
            "blur",
            "blurProgress",
            "性能",
        ),
    ),
    (
        "实验参数与请求文档",
        (
            "实验参数",
            "首刷参数",
            "请求前置",
            "推全",
            "技术文档",
        ),
    ),
    (
        "近 7 天窗口学习",
        (
            "近 7 天",
            "learn-window",
            "窗口学习",
            "全量历史窗口",
            "全量读取",
            "补采",
        ),
    ),
    (
        "面板可视化与数据同步",
        (
            "面板",
            "panel",
            "overview",
            "dashboard",
            "可视化",
            "当前项目上下文",
            "项目 / 上下文分布",
            "数据同步",
            "重叠",
            "折线图",
            "token",
            "loading",
        ),
    ),
    (
        "记忆机制与注入预算",
        (
            "memory",
            "记忆",
            "长期",
            "短期",
            "低优",
            "注入",
            "预算",
            "原生",
        ),
    ),
    (
        "独立 Review 流程",
        (
            "subreview",
            "/subReview",
            "独立 codex",
            "独立的codex",
            "独立 reviewer",
            "独立评审",
            "10/10",
            "评审意见",
            "反复审阅",
        ),
    ),
    (
        "开源评审与发布准备",
        (
            "开源评审",
            "适合开源",
            "公开发布",
            "README",
            "installer",
        ),
    ),
    (
        "个人资产自动化运行",
        (
            "hook",
            "每晚",
            "LaunchAgent",
            "nightly",
            "自动拉起来",
        ),
    ),
    (
        "代码清理与本地提交",
        (
            "提交",
            "删除空行",
            "EOF",
            "final newline",
            "本地提交",
        ),
    ),
    (
        "IDE 索引排障",
        (
            "Android Studio",
            "IDE 索引",
            "indexed search",
            "definition",
            "usage",
            "服务本身是好的",
        ),
    ),
    (
        "项目规则与 AGENTS",
        (
            "AGENTS.md",
            "读取规则",
            "project-doc",
        ),
    ),
    (
        "协作文档工具",
        (
            "docs",
            "document",
            "whiteboard",
            "白板",
            "协作",
        ),
    ),
    (
        "CLI 使用习惯",
        (
            "iTerms",
            "行首",
            "行尾",
            "删除代码",
            "选中部分文字",
            "光标",
        ),
    ),
    (
        "Codex 命令参数",
        (
            "--latest",
        ),
    ),
]

CONTEXT_TOPIC_LABEL_EN = {
    "移动端扫描/录制链路": "Mobile scan / recording workflow",
    "移动端编译/类型错误": "Mobile compile / type errors",
    "性能与体验评审": "Performance / UX review",
    "实验参数与请求文档": "Experiment parameters / request docs",
    "近 7 天窗口学习": "7-day window learning",
    "面板可视化与数据同步": "Panel visualization / data sync",
    "记忆机制与注入预算": "Memory mechanism / injection budget",
    "独立 Review 流程": "Independent review workflow",
    "开源评审与发布准备": "Open-source review / release prep",
    "个人资产自动化运行": "Personal asset automation",
    "代码清理与本地提交": "Code cleanup / local commits",
    "IDE 索引排障": "IDE index troubleshooting",
    "项目规则与 AGENTS": "Project rules / AGENTS",
    "协作文档工具": "Collaboration document tools",
    "CLI 使用习惯": "CLI usage habits",
    "Codex 命令参数": "Codex command arguments",
}

CONTEXT_TOPIC_GENERIC_KEYWORDS = {
    "继续任务",
    "关联窗口",
    "暂无关键词",
    "窗口",
    "任务",
    "问题",
    "结论",
}
CONTEXT_TOPIC_NOISY_MARKERS = (
    "[kmp_cli_log]",
    "file://",
    "unresolved reference",
    "traceback",
    "exception:",
    "error:",
    "e: file:",
)

PROJECT_ROOT_MARKERS = (
    ".git",
    ".hg",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "Makefile",
    "Gemfile",
    "composer.json",
    "mix.exs",
)
PROJECT_ROOT_SUFFIXES = (".xcodeproj", ".xcworkspace")
GENERIC_PATH_PARTS = {
    "",
    ".",
    "..",
    "users",
    "home",
    "work",
    "workspace",
    "workspaces",
    "repo",
    "repos",
    "project",
    "projects",
    "code",
    "src",
}
GENERIC_PROJECT_LEAF_NAMES = {
    "android",
    "app",
    "apps",
    "backend",
    "client",
    "clients",
    "frontend",
    "ios",
    "lib",
    "libs",
    "package",
    "packages",
    "pkg",
    "server",
    "services",
    "src",
    "web",
}
NON_PROJECT_CONTEXT_LABELS = {
    "Codex 本地环境",
    "个人资产系统",
    "个人工作区",
    "未分类上下文",
}
ACRONYM_LABELS = {
    "ai": "AI",
    "api": "API",
    "asr": "ASR",
    "cd": "CD",
    "ci": "CI",
    "cli": "CLI",
    "ios": "iOS",
    "mcp": "MCP",
    "qa": "QA",
    "sdk": "SDK",
    "ui": "UI",
    "ux": "UX",
}
BRAND_DISPLAY_NAME = "OpenRelix"
LEGACY_BRAND_PHRASES = (
    "AI Personal Assets System",
    "AI personal assets system",
    "AI-Personal-Assets",
    "AI Personal Assets",
    "AI personal assets",
    "AI个人资产系统",
    "AI个人资产",
    "AI 个人资产系统",
    "AI 个人资产",
    "ai-personal-assets",
)


def normalize_brand_display_text(value):
    text = str(value or "")
    if not text:
        return text
    for source, target in BRAND_DISPLAY_REPLACEMENTS:
        text = text.replace(source, target)
    for phrase in LEGACY_BRAND_PHRASES:
        text = text.replace(phrase, BRAND_DISPLAY_NAME)
    text = re.sub(r"\bAPA\b", BRAND_DISPLAY_NAME, text)
    return text


def current_local_datetime():
    return datetime.now().astimezone()


def parse_iso_datetime(value):
    if not value:
        return None
    normalized = value
    if isinstance(normalized, str) and normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed.astimezone()


def display_local_datetime(value):
    parsed = value if isinstance(value, datetime) else parse_iso_datetime(value)
    if parsed is None:
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def display_short_local_datetime(value):
    parsed = value if isinstance(value, datetime) else parse_iso_datetime(value)
    if parsed is None:
        return ""
    return parsed.strftime("%m-%d %H:%M")


def resolve_npx_binary():
    candidates = [
        shutil.which("npx"),
        "/opt/homebrew/bin/npx",
        "/usr/local/bin/npx",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return "npx"


def build_subprocess_env():
    env = os.environ.copy()
    base_path = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    current_path = env.get("PATH", "")
    env["PATH"] = "{}:{}".format(base_path, current_path) if current_path else base_path
    return env


def load_jsonl(path: Path):
    rows = []
    if not path.exists():
        return rows
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def compact_number(value):
    number = safe_int(value)
    abs_number = abs(number)
    if abs_number >= 1_000_000_000:
        return "{:.1f}B".format(number / 1_000_000_000)
    if abs_number >= 1_000_000:
        return "{:.1f}M".format(number / 1_000_000)
    if abs_number >= 1_000:
        return "{:.1f}K".format(number / 1_000)
    return str(number)


def compact_token_zh(value):
    number = safe_int(value)
    abs_number = abs(number)
    if abs_number >= 100_000_000:
        return "{:.1f}亿".format(number / 100_000_000)
    if abs_number >= 10_000:
        return "{:.1f}万".format(number / 10_000)
    return str(number)


def compact_token(value, language=None):
    if is_english(language):
        return compact_number(value)
    return compact_token_zh(value)


def compact_token_k(value):
    number = safe_int(value)
    if number == 0:
        return "0K"
    value_k = number / 1000
    if number % 1000 == 0:
        return "{}K".format(number // 1000)
    return "{:.1f}K".format(value_k)


def format_percent(value, digits=0, signed=False):
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if signed and number > 0 else ""
    return "{}{:.{digits}f}%".format(sign, number, digits=digits)


def compact_signed_token(value, language=None):
    number = safe_int(value)
    if number == 0:
        return compact_token(0, language=language)
    prefix = "+" if number > 0 else "-"
    return "{}{}".format(prefix, compact_token(abs(number), language=language))


def rough_text_token_count(text):
    text = str(text or "")
    if not text.strip():
        return 0

    cjk_chars = 0
    other_chars = 0
    for char in text:
        if char.isspace():
            continue
        codepoint = ord(char)
        if (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
        ):
            cjk_chars += 1
        else:
            other_chars += 1
    return cjk_chars + ((other_chars + 3) // 4)


def estimate_memory_row_tokens(row):
    title = compact_preview_text(
        row.get("display_title") or row.get("title", ""),
        limit=PERSONAL_MEMORY_TITLE_LIMIT,
    )
    value_note = compact_preview_text(
        row.get("display_value_note") or row.get("value_note", ""),
        limit=PERSONAL_MEMORY_NOTE_LIMIT,
    )
    meta = "{}/{}/{}".format(
        row.get("bucket") or "unknown",
        row.get("memory_type") or "semantic",
        row.get("priority") or "medium",
    )
    line = "- [{}] {}".format(meta, title)
    if value_note:
        line = "{} - {}".format(line, value_note)
    tokens, _ = estimate_summary_tokens(line)
    return tokens


def estimate_memory_summary_fit(context_rows, max_items, token_budget):
    heading_tokens, _ = estimate_summary_tokens("### Local personal memory registry\n")
    used_tokens = heading_tokens
    fit_count = 0
    for row in context_rows[:max_items]:
        row_tokens = estimate_memory_row_tokens(row)
        if row_tokens <= 0:
            continue
        if used_tokens + row_tokens > token_budget:
            continue
        used_tokens += row_tokens
        fit_count += 1
    return fit_count, min(used_tokens, token_budget)


def read_personal_memory_summary_usage(summary_path):
    if not summary_path:
        return None
    path = Path(summary_path)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    marker = "### Local personal memory registry"
    if marker not in text:
        return {"count": 0, "tokens": 0}
    section = text.split(marker, 1)[1].split("\n### ", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("- ")]
    section_text = "{}\n\n{}\n".format(marker, "\n".join(lines))
    tokens, _ = estimate_summary_tokens(section_text)
    return {"count": len(lines), "tokens": tokens}


def build_personal_memory_token_usage(
    memory_registry,
    memory_mode,
    language=None,
    memory_summary_path=None,
    memory_summary_budget=None,
):
    language = current_language(language)
    memory_mode = str(memory_mode or "integrated")
    summary_budget = memory_summary_budget or get_memory_summary_budget(PATHS)
    summary_target_tokens = summary_budget["target_tokens"]
    summary_warn_tokens = summary_budget["warn_tokens"]
    summary_max_tokens = summary_budget["max_tokens"]
    personal_memory_budget_tokens = summary_budget["personal_memory_tokens"]
    enabled = memory_mode != "off"
    rows = memory_registry or []
    row_count = len(rows)
    context_rows = [row for row in rows if row.get("bucket") in {"durable", "session"}]
    has_candidate_cap = MEMORY_SUMMARY_MAX_PERSONAL_MEMORY_ITEMS > 0
    context_item_limit = (
        min(len(context_rows), MEMORY_SUMMARY_MAX_PERSONAL_MEMORY_ITEMS)
        if has_candidate_cap
        else len(context_rows)
    )
    estimated_context_item_count, estimated_personal_memory_tokens = estimate_memory_summary_fit(
        context_rows,
        context_item_limit,
        personal_memory_budget_tokens,
    )
    actual_summary_usage = (
        read_personal_memory_summary_usage(memory_summary_path)
        if memory_mode == "integrated"
        else None
    )
    count_label_zh = "约"
    count_label_en = "about"
    if actual_summary_usage is not None:
        count_label_zh = "实际"
        count_label_en = "actual"
        estimated_context_item_count = actual_summary_usage["count"]
        estimated_personal_memory_tokens = min(
            actual_summary_usage["tokens"],
            personal_memory_budget_tokens,
        )
    estimated_tokens = estimated_personal_memory_tokens if memory_mode == "integrated" else 0
    max_tokens_display = compact_token_k(summary_max_tokens)
    target_tokens_display = compact_token_k(summary_target_tokens)
    warn_tokens_display = compact_token_k(summary_warn_tokens)
    personal_budget_display = compact_token_k(personal_memory_budget_tokens)
    estimated_personal_display = compact_token_k(estimated_personal_memory_tokens)

    if memory_mode == "integrated":
        mode_label_zh = "Integrated"
        mode_label_en = "Integrated"
        candidate_policy_zh = (
            "候选上限 {} 条".format(context_item_limit)
            if has_candidate_cap
            else "候选不设条数上限"
        )
        candidate_policy_en = (
            "candidate cap {}".format(context_item_limit)
            if has_candidate_cap
            else "no item cap"
        )
        mode_note_zh = "{} 条留本地，{} {} 条进摘要（{}）".format(
            row_count,
            count_label_zh,
            estimated_context_item_count,
            candidate_policy_zh,
        )
        mode_note_en = "{} stay local; {} {} enter the summary ({})".format(
            row_count,
            count_label_en,
            estimated_context_item_count,
            candidate_policy_en,
        )
        caption_zh = "摘要目标 {} / 警戒 {} / 上限 {}".format(
            target_tokens_display,
            warn_tokens_display,
            max_tokens_display,
        )
        caption_en = "Summary target {} / warning {} / max {}".format(
            target_tokens_display,
            warn_tokens_display,
            max_tokens_display,
        )
        status_zh = "受控"
        status_en = "Bounded"
        value_zh = "≈ {}".format(estimated_personal_display)
        value_en = "≈ {}".format(estimated_personal_display)
        meter_percent = min(100, round(estimated_tokens / summary_max_tokens * 100))
    elif memory_mode == "local-only":
        mode_label_zh = "本地记录"
        mode_label_en = "Local-only"
        mode_note_zh = "{} 条只写本地，不注入 Codex context".format(row_count)
        mode_note_en = "{} items stay local and are not injected into Codex context".format(row_count)
        caption_zh = "Codex context 占用 0K"
        caption_en = "Codex context usage 0K"
        status_zh = "本地"
        status_en = "Local"
        value_zh = "0K"
        value_en = "0K"
        meter_percent = 0
    else:
        mode_label_zh = "关闭"
        mode_label_en = "Off"
        mode_note_zh = "个人记忆已关闭"
        mode_note_en = "Personal memory is off"
        caption_zh = "Codex context 占用 0K"
        caption_en = "Codex context usage 0K"
        status_zh = "关闭"
        status_en = "Off"
        value_zh = "0K"
        value_en = "0K"
        meter_percent = 0

    method_note_zh = (
        "面板展示的是 bounded summary 预算状态，不是完整登记册体积；"
        "默认上限 5K；当前 target {}、warn {}、max {} 会随配置的 max 自动派生，个人记忆分区预算 {}。"
    ).format(
        target_tokens_display,
        warn_tokens_display,
        max_tokens_display,
        personal_budget_display,
    )
    method_note_en = (
        "This card shows bounded-summary budget status, not the full registry footprint; "
        "the default max is 5K; current target {}, warning {}, and max {} are derived from the configured max, with {} for personal memories."
    ).format(
        target_tokens_display,
        warn_tokens_display,
        max_tokens_display,
        personal_budget_display,
    )

    return {
        "enabled": enabled,
        "memory_mode": memory_mode,
        "mode_label": localized(mode_label_zh, mode_label_en, language),
        "mode_label_zh": mode_label_zh,
        "mode_label_en": mode_label_en,
        "mode_note": localized(mode_note_zh, mode_note_en, language),
        "mode_note_zh": mode_note_zh,
        "mode_note_en": mode_note_en,
        "estimated_tokens": estimated_tokens,
        "estimated_tokens_display": compact_token_k(estimated_tokens),
        "estimated_tokens_display_zh": compact_token_k(estimated_tokens),
        "estimated_tokens_display_en": compact_token_k(estimated_tokens),
        "value_display": localized(value_zh, value_en, language),
        "value_display_zh": value_zh,
        "value_display_en": value_en,
        "status_label": localized(status_zh, status_en, language),
        "status_label_zh": status_zh,
        "status_label_en": status_en,
        "meter_percent": meter_percent,
        "target_tokens": summary_target_tokens,
        "warn_tokens": summary_warn_tokens,
        "max_tokens": summary_max_tokens,
        "max_tokens_display": max_tokens_display,
        "personal_memory_budget_tokens": personal_memory_budget_tokens,
        "personal_memory_budget_display": personal_budget_display,
        "estimated_personal_memory_tokens": estimated_personal_memory_tokens,
        "estimated_personal_memory_display": estimated_personal_display,
        "context_candidate_count": len(context_rows),
        "context_item_limit": context_item_limit,
        "estimated_context_item_count": estimated_context_item_count,
        "item_count": row_count,
        "caption": localized(caption_zh, caption_en, language),
        "caption_zh": caption_zh,
        "caption_en": caption_en,
        "method_note": localized(method_note_zh, method_note_en, language),
        "method_note_zh": method_note_zh,
        "method_note_en": method_note_en,
    }


def percent_of(part, total):
    total = safe_int(total)
    if total <= 0:
        return None
    return (safe_int(part) / total) * 100


def counter_to_rows(counter):
    return [
        {"label": key, "value": value}
        for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def load_reviews():
    reviews = []
    for path in REVIEWS_DIR.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        review = {
            "path": str(path),
            "date": path.name[:10] if len(path.name) >= 10 else "",
            "task": path.stem,
            "domain": "",
            "repo": "",
            "text": text,
        }
        for line in text.splitlines():
            if line.startswith("- Date:"):
                review["date"] = line.partition(":")[2].strip()
            elif line.startswith("- Task:"):
                review["task"] = line.partition(":")[2].strip()
            elif line.startswith("- Domain:"):
                review["domain"] = line.partition(":")[2].strip()
            elif line.startswith("- Repo:"):
                review["repo"] = line.partition(":")[2].strip()
        reviews.append(review)
    return sorted(reviews, key=lambda item: (item["date"], item["task"]), reverse=True)


def load_nightly_summary_candidates():
    if not CONSOLIDATED_DIR.exists():
        return []
    candidates = []
    for path in CONSOLIDATED_DIR.glob("*/summary.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_path"] = str(path)
        candidates.append(payload)
    return candidates


def parse_nightly_summary_date(payload):
    try:
        return datetime.fromisoformat(payload["date"]).date()
    except (KeyError, TypeError, ValueError):
        return None


def nightly_summary_sort_key(payload):
    summary_date = parse_nightly_summary_date(payload)
    generated = parse_iso_datetime(payload.get("generated_at", ""))
    generated_sort = generated.isoformat() if generated else ""
    stage_rank = 2 if payload.get("stage") == "final" else 1 if payload.get("stage") == "preliminary" else 0
    summary_date_sort = summary_date.isoformat() if summary_date else ""
    return (summary_date_sort, stage_rank, generated_sort)


def active_nightly_sort_key(payload):
    generated = parse_iso_datetime(payload.get("generated_at", ""))
    generated_sort = generated.isoformat() if generated else ""
    stage_rank = 2 if payload.get("stage") == "manual" else 1 if payload.get("stage") == "preliminary" else 0
    summary_date = parse_nightly_summary_date(payload)
    summary_date_sort = summary_date.isoformat() if summary_date else ""
    return (summary_date_sort, stage_rank, generated_sort)


def daily_nightly_sort_key(payload):
    generated = parse_iso_datetime(payload.get("generated_at", ""))
    generated_sort = generated.isoformat() if generated else ""
    stage_rank = {
        "final": 3,
        "manual": 2,
        "preliminary": 1,
    }.get(payload.get("stage"), 0)
    summary_date = parse_nightly_summary_date(payload)
    summary_date_sort = summary_date.isoformat() if summary_date else ""
    return (summary_date_sort, stage_rank, generated_sort)


def select_best_nightly_summary_for_date(candidates, date_str):
    matches = [
        payload
        for payload in candidates
        if parse_nightly_summary_date(payload) is not None
        and parse_nightly_summary_date(payload).isoformat() == date_str
    ]
    if not matches:
        return None
    return sorted(matches, key=daily_nightly_sort_key)[-1]


def select_primary_and_active_nightly_summaries(candidates, today=None):
    valid_candidates = [
        payload for payload in candidates
        if parse_nightly_summary_date(payload) is not None
    ]
    if not valid_candidates:
        return None, None
    today = today or current_local_datetime().date()
    yesterday = today - timedelta(days=1)

    yesterday_summaries = [
        payload for payload in valid_candidates
        if parse_nightly_summary_date(payload) == yesterday
    ]
    yesterday_finals = [
        payload for payload in yesterday_summaries
        if payload.get("stage") == "final"
    ]
    today_active_summaries = [
        payload
        for payload in valid_candidates
        if parse_nightly_summary_date(payload) == today and payload.get("stage") in {"preliminary", "manual"}
    ]

    primary = None
    if yesterday_finals:
        primary = sorted(yesterday_finals, key=nightly_summary_sort_key)[-1]
    elif yesterday_summaries:
        primary = sorted(yesterday_summaries, key=nightly_summary_sort_key)[-1]
    elif today_active_summaries:
        primary = sorted(today_active_summaries, key=active_nightly_sort_key)[-1]
    else:
        previous_finals = [
            payload for payload in valid_candidates
            if parse_nightly_summary_date(payload) < today and payload.get("stage") == "final"
        ]
        if previous_finals:
            primary = sorted(previous_finals, key=nightly_summary_sort_key)[-1]
        else:
            primary = sorted(valid_candidates, key=nightly_summary_sort_key)[-1]

    active = None
    if today_active_summaries:
        latest_active = sorted(today_active_summaries, key=active_nightly_sort_key)[-1]
        if not primary or primary.get("_path") != latest_active.get("_path"):
            active = latest_active
    return primary, active


def load_primary_and_active_nightly_summaries():
    candidates = load_nightly_summary_candidates()
    return select_primary_and_active_nightly_summaries(candidates)


def load_latest_nightly_summary():
    primary, _ = load_primary_and_active_nightly_summaries()
    return primary


def select_memory_view_nightly(primary_nightly, active_nightly):
    memory_keys = ("durable_memories", "session_memories", "low_priority_memories")
    if active_nightly and any(active_nightly.get(key) for key in memory_keys):
        return active_nightly
    return primary_nightly


def select_display_nightly(primary_nightly, active_nightly):
    if active_nightly and active_nightly.get("date"):
        memory_view = select_memory_view_nightly(primary_nightly, active_nightly)
        if memory_view is active_nightly:
            return active_nightly
    return primary_nightly


def derive_window_overview_title(source_summary, today=None):
    if not source_summary:
        return "最近一次窗口概览"
    today = today or current_local_datetime().date()
    summary_date = parse_nightly_summary_date(source_summary)
    stage = source_summary.get("stage", "")
    if summary_date == today - timedelta(days=1):
        return "昨夜窗口概览"
    if summary_date == today and stage in {"preliminary", "manual"}:
        return "当日窗口概览"
    return "最近一次窗口概览"


def load_daily_capture(date_str=""):
    if date_str:
        candidate = RAW_DAILY_DIR / "{}.json".format(date_str)
        if candidate.exists():
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            payload["_path"] = str(candidate)
            return payload
        return None

    if not RAW_DAILY_DIR.exists():
        return None

    candidates = []
    for path in RAW_DAILY_DIR.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_path"] = str(path)
        candidates.append(payload)

    if not candidates:
        return None

    return sorted(
        candidates,
        key=lambda item: (item.get("date", ""), item.get("generated_at", "")),
    )[-1]


def list_daily_capture_dates():
    if not RAW_DAILY_DIR.exists():
        return []
    dates = []
    for path in RAW_DAILY_DIR.glob("*.json"):
        date_str = path.stem
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
            dates.append(date_str)
    return sorted(set(dates), reverse=True)


def date_from_epoch(ts):
    return datetime.fromtimestamp(int(ts)).astimezone().date().isoformat()


def list_codex_history_dates(lookback_days=BACKFILL_LOOKBACK_DAYS):
    history_path = PATHS.codex_home / "history.jsonl"
    if not history_path.exists():
        return []

    today = current_local_datetime().date()
    cutoff = today - timedelta(days=max(lookback_days, 1) - 1)
    dates = set()
    try:
        lines = history_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            date_str = date_from_epoch(item["ts"])
            parsed = datetime.fromisoformat(date_str).date()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if parsed >= cutoff:
            dates.add(date_str)
    return sorted(dates, reverse=True)


def shell_quote(value):
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:=+-]+", text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


def make_backfill_command(start_date, end_date=None, learn_window_days=BACKFILL_LEARN_WINDOW_DAYS):
    end_date = end_date or start_date
    parts = [
        "openrelix",
        "backfill",
        "--from",
        start_date,
        "--to",
        end_date,
        "--stage",
        "final",
        "--learn-window-days",
        str(learn_window_days),
    ]
    return " ".join(shell_quote(part) for part in parts)


def make_backfill_dates_command(dates, learn_window_days=BACKFILL_LEARN_WINDOW_DAYS):
    dates = [date_str for date_str in dates if date_str]
    if not dates:
        return ""
    parts = [
        "openrelix",
        "backfill",
        "--dates",
        ",".join(sorted(dates)),
        "--stage",
        "final",
        "--learn-window-days",
        str(learn_window_days),
    ]
    return " ".join(shell_quote(part) for part in parts)


def build_backfill_view(nightly_candidates, lookback_days=BACKFILL_LOOKBACK_DAYS):
    summary_dates = set()
    for payload in nightly_candidates or []:
        parsed = parse_nightly_summary_date(payload)
        if parsed is not None:
            summary_dates.add(parsed.isoformat())

    candidate_dates = set(list_daily_capture_dates()) | set(list_codex_history_dates(lookback_days=lookback_days))
    missing_dates = sorted(candidate_dates - summary_dates, reverse=True)
    range_command = ""
    if missing_dates:
        range_command = make_backfill_dates_command(missing_dates)

    return {
        "lookback_days": lookback_days,
        "learn_window_days": BACKFILL_LEARN_WINDOW_DAYS,
        "missing_dates": missing_dates,
        "range_command": range_command,
        "commands_by_date": {
            date_str: make_backfill_command(date_str)
            for date_str in missing_dates
        },
    }


def load_token_usage_cache():
    if not TOKEN_CACHE_PATH.exists():
        return None
    try:
        return json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def write_token_usage_cache(payload):
    atomic_write_json(TOKEN_CACHE_PATH, payload)


def fetch_ccusage_daily(window_days=CCUSAGE_WINDOW_DAYS):
    end_date = current_local_datetime().date()
    start_date = end_date - timedelta(days=window_days - 1)
    base_cmd = [
        resolve_npx_binary(),
        "-y",
        "@ccusage/codex@latest",
        "daily",
        "-j",
        "--since",
        start_date.isoformat(),
        "--until",
        end_date.isoformat(),
        "--timezone",
        CCUSAGE_TIMEZONE,
    ]

    attempts = [[], ["--offline"]]
    last_error = None
    for extra_args in attempts:
        try:
            result = subprocess.run(
                base_cmd + extra_args,
                capture_output=True,
                text=True,
                check=True,
                env=build_subprocess_env(),
                timeout=120,
            )
            payload = json.loads(result.stdout)
            return {
                "available": True,
                "payload": payload,
                "error": "",
                "fetched_at": current_local_datetime().isoformat(),
                "window_days": window_days,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)

    return {
        "available": False,
        "payload": {"daily": [], "totals": {}},
        "error": last_error or "",
        "fetched_at": current_local_datetime().isoformat(),
        "window_days": window_days,
    }


def resolve_ccusage_daily():
    refresh_requested = os.environ.get("AI_ASSET_REFRESH_TOKEN") == "1"
    cached = load_token_usage_cache()
    if cached and not refresh_requested:
        return cached

    live = fetch_ccusage_daily()
    if live.get("available"):
        write_token_usage_cache(live)
        return live

    if cached:
        return cached
    return live


def make_token_breakdown_detail(label, value, meta="", language=None):
    return {
        "label": label,
        "value": safe_int(value),
        "title": "{}：{}".format(label, compact_token(value, language=language)),
        "meta": meta,
    }


def build_token_breakdown_details(row, language=None):
    language = current_language(language)
    total_tokens = row.get("totalTokens", 0)
    input_tokens = row.get("inputTokens", 0)
    cached_input_tokens = row.get("cachedInputTokens", 0)
    output_tokens = row.get("outputTokens", 0)
    reasoning_output_tokens = row.get("reasoningOutputTokens", 0)
    cached_share = percent_of(cached_input_tokens, input_tokens)
    output_share = percent_of(output_tokens, total_tokens)
    reasoning_share = percent_of(reasoning_output_tokens, total_tokens)
    details = [
        make_token_breakdown_detail(
            localized("输入", "Input", language),
            input_tokens,
            localized("总输入 Token", "Total input tokens", language),
            language=language,
        ),
        make_token_breakdown_detail(
            localized("缓存输入", "Cached input", language),
            cached_input_tokens,
            localized(
                "占输入 {}".format(format_percent(cached_share)),
                "{} of input".format(format_percent(cached_share)),
                language,
            ),
            language=language,
        ),
        make_token_breakdown_detail(
            localized("输出", "Output", language),
            output_tokens,
            localized(
                "占总量 {}".format(format_percent(output_share, digits=1)),
                "{} of total".format(format_percent(output_share, digits=1)),
                language,
            ),
            language=language,
        ),
        make_token_breakdown_detail(
            localized("推理输出", "Reasoning output", language),
            reasoning_output_tokens,
            localized(
                "占总量 {}".format(format_percent(reasoning_share, digits=1)),
                "{} of total".format(format_percent(reasoning_share, digits=1)),
                language,
            ),
            language=language,
        ),
    ]
    cost = row.get("costUSD")
    if isinstance(cost, (int, float)) and cost > 0:
        details.append(
            {
                "title": localized("费用估算：${:.2f}".format(cost), "Estimated cost: ${:.2f}".format(cost), language),
                "meta": localized("来自 ccusage", "From ccusage", language),
            }
        )
    return details


def make_token_summary_card(label, value, caption, tone="neutral"):
    return {
        "label": label,
        "value": value,
        "caption": caption,
        "tone": tone,
    }


def format_usd(value):
    amount = safe_float(value)
    if amount <= 0 or not math.isfinite(amount):
        return "—"
    rounded = Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return "${:,}".format(int(rounded))


def compact_token_with_cost(token_value, cost_value, language=None):
    token_display = compact_token(token_value, language=language)
    cost_display = format_usd(cost_value)
    if cost_display == "—":
        return token_display
    return "{} · {}".format(token_display, cost_display)


def build_token_summary_cards(parsed_rows, trailing_rows, latest, language=None):
    language = current_language(language)
    if not latest:
        return []

    active_trailing_rows = [row for row in trailing_rows if row.get("totalTokens", 0) > 0]
    summary_cards = []

    if active_trailing_rows:
        seven_day_total = sum(row["totalTokens"] for row in active_trailing_rows)
        seven_day_cost = sum(safe_float(row.get("costUSD")) for row in active_trailing_rows)
        seven_day_average = sum(row["totalTokens"] for row in active_trailing_rows) // len(active_trailing_rows)
        peak_row = max(active_trailing_rows, key=lambda row: row["totalTokens"])
        summary_cards.extend(
            [
                make_token_summary_card(
                    localized("7 日账单", "7-day bill", language),
                    format_usd(seven_day_cost),
                    localized(
                        "{} Token · ccusage 估算".format(compact_token(seven_day_total, language=language)),
                        "{} Tokens · ccusage estimate".format(compact_token(seven_day_total, language=language)),
                        language,
                    ),
                ),
                make_token_summary_card(
                    localized("7 日均值", "7-day average", language),
                    compact_token(seven_day_average, language=language),
                    localized(
                        "按 {} 个有数据日".format(len(active_trailing_rows)),
                        "Across {} days with data".format(len(active_trailing_rows)),
                        language,
                    ),
                ),
                make_token_summary_card(
                    localized("峰值日", "Peak day", language),
                    compact_token(peak_row["totalTokens"], language=language),
                    localized(
                        "{} 最高".format(peak_row["date_label"]),
                        "Peak on {}".format(peak_row["date_label"]),
                        language,
                    ),
                ),
            ]
        )
    else:
        summary_cards.append(
            make_token_summary_card(
                localized("7 日账单", "7-day bill", language),
                "—",
                localized("暂无 7 日账单数据", "No 7-day bill data yet", language),
            )
        )

    cached_share = percent_of(latest["cachedInputTokens"], latest["inputTokens"])
    summary_cards.append(
        make_token_summary_card(
            localized("缓存占输入", "Cached / input", language),
            format_percent(cached_share),
            localized(
                "缓存 {} / 输入 {}".format(
                    compact_token(latest["cachedInputTokens"], language=language),
                    compact_token(latest["inputTokens"], language=language),
                ),
                "Cached {} / input {}".format(
                    compact_token(latest["cachedInputTokens"], language=language),
                    compact_token(latest["inputTokens"], language=language),
                ),
                language,
            ),
            "neutral",
        )
    )
    return summary_cards


def token_daily_tone(value, max_value):
    value = safe_int(value)
    max_value = max(safe_int(max_value), 1)
    if value <= 0:
        return "token-daily-empty"

    ratio = value / max_value
    if ratio >= 0.85:
        return "token-daily-high"
    if ratio >= 0.45:
        return "token-daily-mid"
    return "token-daily-low"


def token_breakdown_tone(kind):
    return {
        "input": "token-input",
        "cached_input": "token-cache",
        "output": "token-output",
        "reasoning_output": "token-reasoning",
    }.get(kind, "token-input")


def build_token_usage_view(ccusage_result, language=None):
    language = current_language(language)
    refreshed_at = ccusage_result.get("fetched_at", "")
    refreshed_at_display = display_local_datetime(refreshed_at)
    if not ccusage_result["available"]:
        return {
            "available": False,
            "error": ccusage_result.get("error", ""),
            "daily_rows": [],
            "today_breakdown": [],
            "today_total_tokens": None,
            "today_total_tokens_display": "—",
            "seven_day_total_tokens": None,
            "seven_day_total_tokens_display": "—",
            "seven_day_cost_usd": None,
            "seven_day_cost_display": "—",
            "today_date_label": localized("今日", "Today", language),
            "summary_cards": [],
            "overview_note": localized(
                "等待实时刷新 Token 统计",
                "Waiting for live Token stats",
                language,
            ),
            "refreshed_at": refreshed_at,
            "refreshed_at_display": refreshed_at_display,
            "window_days": ccusage_result.get("window_days", CCUSAGE_WINDOW_DAYS),
        }

    raw_rows = ccusage_result.get("payload", {}).get("daily", [])
    parsed_rows = []
    for row in raw_rows:
        raw_date = row.get("date", "")
        try:
            parsed_date = datetime.strptime(raw_date, "%b %d, %Y")
            label = parsed_date.strftime("%m-%d")
        except ValueError:
            parsed_date = None
            label = raw_date
        parsed_rows.append(
            {
                "raw_date": raw_date,
                "date_label": label,
                "sort_key": parsed_date.isoformat() if parsed_date else raw_date,
                "inputTokens": safe_int(row.get("inputTokens", 0)),
                "cachedInputTokens": safe_int(row.get("cachedInputTokens", 0)),
                "outputTokens": safe_int(row.get("outputTokens", 0)),
                "reasoningOutputTokens": safe_int(row.get("reasoningOutputTokens", 0)),
                "totalTokens": safe_int(row.get("totalTokens", 0)),
                "display_total_tokens": compact_token(row.get("totalTokens", 0), language=language),
                "costUSD": safe_float(row.get("costUSD", 0)),
            }
        )

    parsed_rows.sort(key=lambda item: item["sort_key"])
    max_daily_tokens = max((row["totalTokens"] for row in parsed_rows), default=0)
    latest = parsed_rows[-1] if parsed_rows else None
    trailing = parsed_rows[-7:]
    seven_day_total = sum(item["totalTokens"] for item in trailing)
    seven_day_cost = sum(safe_float(item.get("costUSD")) for item in trailing)
    active_trailing_count = sum(1 for item in trailing if item["totalTokens"] > 0)
    overview_note = localized(
        "近 {} 天中 {} 天有记录 · {}".format(
            min(7, ccusage_result.get("window_days", CCUSAGE_WINDOW_DAYS)),
            active_trailing_count,
            refreshed_at_display or "等待实时刷新",
        ),
        "{} days with records in the last {} days · {}".format(
            active_trailing_count,
            min(7, ccusage_result.get("window_days", CCUSAGE_WINDOW_DAYS)),
            refreshed_at_display or "waiting for live refresh",
        ),
        language,
    )

    today_breakdown = []
    if latest:
        cached_share = percent_of(latest["cachedInputTokens"], latest["inputTokens"])
        output_share = percent_of(latest["outputTokens"], latest["totalTokens"])
        reasoning_share = percent_of(latest["reasoningOutputTokens"], latest["totalTokens"])
        today_breakdown = [
            {
                "label": localized("输入", "Input", language),
                "value": latest["inputTokens"],
                "display": compact_token(latest["inputTokens"], language=language),
                "tone": token_breakdown_tone("input"),
                "details": [
                    {
                        "label": localized("输入", "Input", language),
                        "value": latest["inputTokens"],
                        "title": localized("输入：{}".format(compact_token(latest["inputTokens"], language=language)), "Input: {}".format(compact_token(latest["inputTokens"], language=language)), language),
                        "meta": localized("总输入 Token", "Total input tokens", language),
                    },
                ],
                "details_heading": localized("输入详情", "Input details", language),
            },
            {
                "label": localized("缓存输入", "Cached input", language),
                "value": latest["cachedInputTokens"],
                "display": compact_token(latest["cachedInputTokens"], language=language),
                "tone": token_breakdown_tone("cached_input"),
                "details": [
                    {
                        "label": localized("缓存输入", "Cached input", language),
                        "value": latest["cachedInputTokens"],
                        "title": localized("缓存输入：{}".format(compact_token(latest["cachedInputTokens"], language=language)), "Cached input: {}".format(compact_token(latest["cachedInputTokens"], language=language)), language),
                        "meta": localized(
                            "占输入 {}".format(format_percent(cached_share)),
                            "{} of input".format(format_percent(cached_share)),
                            language,
                        ),
                    },
                ],
                "details_heading": localized("缓存详情", "Cache details", language),
            },
            {
                "label": localized("输出", "Output", language),
                "value": latest["outputTokens"],
                "display": compact_token(latest["outputTokens"], language=language),
                "tone": token_breakdown_tone("output"),
                "details": [
                    {
                        "label": localized("输出", "Output", language),
                        "value": latest["outputTokens"],
                        "title": localized("输出：{}".format(compact_token(latest["outputTokens"], language=language)), "Output: {}".format(compact_token(latest["outputTokens"], language=language)), language),
                        "meta": localized(
                            "占总量 {}".format(format_percent(output_share, digits=1)),
                            "{} of total".format(format_percent(output_share, digits=1)),
                            language,
                        ),
                    },
                ],
                "details_heading": localized("输出详情", "Output details", language),
            },
            {
                "label": localized("推理输出", "Reasoning output", language),
                "value": latest["reasoningOutputTokens"],
                "display": compact_token(latest["reasoningOutputTokens"], language=language),
                "tone": token_breakdown_tone("reasoning_output"),
                "details": [
                    {
                        "label": localized("推理输出", "Reasoning output", language),
                        "value": latest["reasoningOutputTokens"],
                        "title": localized("推理输出：{}".format(compact_token(latest["reasoningOutputTokens"], language=language)), "Reasoning output: {}".format(compact_token(latest["reasoningOutputTokens"], language=language)), language),
                        "meta": localized(
                            "占总量 {}".format(format_percent(reasoning_share, digits=1)),
                            "{} of total".format(format_percent(reasoning_share, digits=1)),
                            language,
                        ),
                    },
                ],
                "details_heading": localized("推理详情", "Reasoning details", language),
            },
        ]

    return {
        "available": True,
        "error": "",
        "daily_rows": [
            {
                "label": row["date_label"],
                "value": row["totalTokens"],
                "display": compact_token_with_cost(row["totalTokens"], row.get("costUSD"), language=language),
                "token_display": row["display_total_tokens"],
                "costUSD": row.get("costUSD", 0),
                "cost_display": format_usd(row.get("costUSD")),
                "tone": token_daily_tone(row["totalTokens"], max_daily_tokens),
                "details": build_token_breakdown_details(row, language=language),
                "details_heading": localized(
                    "{} Token 构成".format(row["date_label"]),
                    "Token breakdown for {}".format(row["date_label"]),
                    language,
                ),
            }
            for row in parsed_rows
        ],
        "today_breakdown": today_breakdown,
        "today_total_tokens": latest["totalTokens"] if latest else 0,
        "today_total_tokens_display": compact_token(latest["totalTokens"], language=language) if latest else "0",
        "seven_day_total_tokens": seven_day_total,
        "seven_day_total_tokens_display": compact_token(seven_day_total, language=language),
        "seven_day_cost_usd": seven_day_cost,
        "seven_day_cost_display": format_usd(seven_day_cost),
        "today_date_label": latest["date_label"] if latest else localized("今日", "Today", language),
        "summary_cards": build_token_summary_cards(parsed_rows, trailing, latest, language=language),
        "overview_note": overview_note,
        "refreshed_at": refreshed_at,
        "refreshed_at_display": refreshed_at_display,
        "window_days": ccusage_result.get("window_days", CCUSAGE_WINDOW_DAYS),
    }


def normalize_term(raw):
    text = raw.strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in TERM_ALIASES:
        return TERM_ALIASES[lowered]
    if lowered in STOPWORDS:
        return ""
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]+", text):
        return text if len(text) <= 5 and text.isupper() else text.capitalize()
    return text


def display_label(kind, value, language=None):
    if is_english(language):
        mapping = {
            "type": DISPLAY_TYPE_EN,
            "domain": DISPLAY_DOMAIN_EN,
            "scope": DISPLAY_SCOPE_EN,
            "status": DISPLAY_STATUS_EN,
        }.get(kind, {})
        if value in mapping:
            return mapping[value]
        return humanize_identifier(value)

    mapping = {
        "type": DISPLAY_TYPE,
        "domain": DISPLAY_DOMAIN,
        "scope": DISPLAY_SCOPE,
        "status": DISPLAY_STATUS,
    }.get(kind, {})
    if value in mapping:
        return mapping[value]
    if kind in {"domain", "scope", "status"}:
        return humanize_identifier(value)
    return value


def display_memory_bucket(value, language=None):
    if is_english(language):
        if value in DISPLAY_MEMORY_BUCKET_EN:
            return DISPLAY_MEMORY_BUCKET_EN[value]
        return humanize_identifier(value) or "Uncategorized memory"
    if value in DISPLAY_MEMORY_BUCKET:
        return DISPLAY_MEMORY_BUCKET[value]
    return humanize_identifier(value) or "未分类记忆"


def display_memory_type(value, language=None):
    if is_english(language):
        if value in DISPLAY_MEMORY_TYPE_EN:
            return DISPLAY_MEMORY_TYPE_EN[value]
        return humanize_identifier(value) or "Uncategorized"
    if value in DISPLAY_MEMORY_TYPE:
        return DISPLAY_MEMORY_TYPE[value]
    return humanize_identifier(value) or "未分类"


def display_memory_priority(value, language=None):
    if is_english(language):
        if value in DISPLAY_MEMORY_PRIORITY_EN:
            return DISPLAY_MEMORY_PRIORITY_EN[value]
        return humanize_identifier(value) or "Unlabeled"
    if value in DISPLAY_MEMORY_PRIORITY:
        return DISPLAY_MEMORY_PRIORITY[value]
    return humanize_identifier(value) or "未标注"


def panel_english_text(value):
    text = str(value or "")
    if text in PANEL_I18N_EN:
        return PANEL_I18N_EN[text]
    if text in FREEFORM_TEXT_EN:
        return FREEFORM_TEXT_EN[text]

    dynamic_patterns = (
        (
            r"快照时间 (.+)",
            lambda match: "Snapshot time {}".format(match.group(1)),
        ),
        (
            r"采集：Codex app-server（预览） · 线程来源：(.+)",
            lambda match: "Collection: Codex app-server (preview) · thread source: {}".format(match.group(1)),
        ),
        (
            r"采集：Codex app-server（预览）",
            lambda match: "Collection: Codex app-server (preview)",
        ),
        (
            r"采集：Codex CLI history/session",
            lambda match: "Collection: Codex CLI history/session",
        ),
        (
            r"采集：整理摘要",
            lambda match: "Collection: synthesis summary",
        ),
        (
            r"(.+) 的总消耗",
            lambda match: "Total for {}".format(match.group(1)),
        ),
        (
            r"(.+) · 未整理",
            lambda match: "{} · Not synthesized".format(match.group(1)),
        ),
        (
            r"(?:当日|每日)窗口概览 · (\d+)",
            lambda match: "Daily Window Overview · {}".format(match.group(1)),
        ),
        (
            r"昨夜窗口概览 · (\d+)",
            lambda match: "Last Night's Window Overview · {}".format(match.group(1)),
        ),
        (
            r"最近一次窗口概览 · (\d+)",
            lambda match: "Latest Window Overview · {}".format(match.group(1)),
        ),
        (
            r"(.+) 的 Token 总消耗为 (.+)，近 7 日累计为 (.+)。",
            lambda match: "Token usage for {} is {}; the 7-day total is {}.".format(
                match.group(1),
                match.group(2),
                match.group(3),
            ),
        ),
        (
            r"原始记录分钟数 (\d+)",
            lambda match: "Recorded minutes {}".format(match.group(1)),
        ),
        (
            r"当前 (\d+) 条",
            lambda match: "Current {}".format(plural_en(match.group(1), "item")),
        ),
        (
            r"例如 (.+)",
            lambda match: "Examples: {}".format(match.group(1)),
        ),
        (
            r"占输入 (.+)",
            lambda match: "{} of input".format(match.group(1)),
        ),
        (
            r"占总量 (.+)",
            lambda match: "{} of total".format(match.group(1)),
        ),
        (
            r"费用估算：\$(.+)",
            lambda match: "Estimated cost: ${}".format(match.group(1)),
        ),
        (
            r"(.+) · 上一日 (.+)",
            lambda match: "{} · previous {}".format(match.group(1), match.group(2)),
        ),
        (
            r"按 (\d+) 个有数据日",
            lambda match: "Across {} days with data".format(match.group(1)),
        ),
        (
            r"(.+) 最高",
            lambda match: "Peak on {}".format(match.group(1)),
        ),
        (
            r"缓存 (.+) / 输入 (.+)",
            lambda match: "Cached {} / input {}".format(match.group(1), match.group(2)),
        ),
        (
            r"近 7 天中 (\d+) 天有记录 · (.+)",
            lambda match: "{} days with records in the last 7 days · {}".format(
                match.group(1),
                match.group(2),
            ),
        ),
        (
            r"未检测到 (.+)。",
            lambda match: "{} not found.".format(match.group(1)),
        ),
        (
            r"最近 (\d+) 天",
            lambda match: "Last {}".format(plural_en(match.group(1), "day")),
        ),
        (
            r"(\d+) 个窗口",
            lambda match: plural_en(match.group(1), "window"),
        ),
        (
            r"(\d+) 窗口",
            lambda match: plural_en(match.group(1), "window"),
        ),
        (
            r"(\d+) 个问题",
            lambda match: plural_en(match.group(1), "question"),
        ),
        (
            r"(\d+) 个结论",
            lambda match: plural_en(match.group(1), "conclusion"),
        ),
        (
            r"(\d+) 个主题",
            lambda match: plural_en(match.group(1), "topic"),
        ),
        (
            r"扫描 (\d+) 天 · 有窗口日期 (\d+) 天 · (\d+) 个窗口 · (.+)",
            lambda match: "Scanned {} · {} · {} · {}".format(
                plural_en(match.group(1), "day"),
                plural_en(match.group(2), "source date"),
                plural_en(match.group(3), "window"),
                match.group(4),
            ),
        ),
        (
            r"可切换最近 1-(\d+) 天；项目内按需求 / 主题二次归类",
            lambda match: (
                "Switch between the last 1-{} days; each project is grouped by need/topic.".format(
                    match.group(1)
                )
            ),
        ),
        (
            r"共 (\d+) 个窗口，原始明细缺失，当前仅展示整理摘要",
            lambda match: "{} windows; raw details are missing, showing synthesis summaries only.".format(
                match.group(1)
            ),
        ),
        (
            r"共 (\d+) 个窗口，按最新活动排序，可点开看详情",
            lambda match: "{} windows, sorted by latest activity. Open cards for details.".format(
                match.group(1)
            ),
        ),
        (
            r"共 (\d+) 条当前记忆；支持跳到来源窗口或打开本地工作区。",
            lambda match: "{} current memories; jump to source windows or open local workspaces.".format(
                match.group(1)
            ),
        ),
        (
            r"共 (\d+) 条个人资产记忆；支持跳到来源窗口或打开本地工作区。",
            lambda match: "{} personal asset memories; jump to source windows or open local workspaces.".format(
                match.group(1)
            ),
        ),
        (
            r"直接读取 (.+) 的“What's in Memory”主题项。",
            lambda match: "Reads topic items from the \"What's in Memory\" section of {}.".format(
                match.group(1)
            ),
        ),
        (
            r"主题项 (\d+) 条；用户偏好 (\d+) 条；通用 tips (\d+) 条。",
            lambda match: "{}; {}; {}.".format(
                plural_en(match.group(1), "topic item"),
                plural_en(match.group(2), "user preference"),
                plural_en(match.group(3), "general tip"),
            ),
        ),
    )
    for pattern, renderer in dynamic_patterns:
        match = re.fullmatch(pattern, text)
        if match:
            return renderer(match)
    return ""


def english_summary_term_label(value):
    text = normalize_brand_display_text(str(value or ""))
    if not text:
        return ""
    if text in SUMMARY_TERM_LABEL_EN:
        return SUMMARY_TERM_LABEL_EN[text]
    if text in PANEL_I18N_EN:
        return PANEL_I18N_EN[text]
    return text


def panel_display_text(value, language=None, en_text=""):
    text = normalize_brand_display_text(value)
    english_text = normalize_brand_display_text(en_text or panel_english_text(text) or text)
    return localized(text, english_text, language)


def panel_language_variant_html(zh_html, en_html):
    if not en_html or en_html == zh_html:
        return zh_html
    return (
        '<span data-lang-only="zh">{zh_html}</span>'
        '<span data-lang-only="en">{en_html}</span>'
    ).format(
        zh_html=zh_html,
        en_html=en_html,
    )


def panel_language_block_html(zh_html, en_html):
    if not en_html or en_html == zh_html:
        return zh_html
    return (
        '<div data-lang-only="zh">{zh_html}</div>'
        '<div data-lang-only="en">{en_html}</div>'
    ).format(
        zh_html=zh_html,
        en_html=en_html,
    )


def panel_language_text_html(zh_text, en_text=""):
    zh_text = normalize_brand_display_text(zh_text)
    en_text = normalize_brand_display_text(en_text or panel_english_text(zh_text) or "")
    return panel_language_variant_html(escape(zh_text), escape(en_text))


def english_freeform_text(value, fallback_label="", keywords=None):
    text = normalize_brand_display_text(str(value or "")).strip()
    if not text:
        return ""
    if not contains_cjk(text):
        return text
    if text in FREEFORM_TEXT_EN:
        return FREEFORM_TEXT_EN[text]
    if text in PANEL_I18N_EN:
        return PANEL_I18N_EN[text]

    candidate = text
    replacements = {}
    replacements.update(PANEL_I18N_EN)
    replacements.update(SUMMARY_TERM_LABEL_EN)
    replacements.update(CONTEXT_KEYWORD_EN)
    replacements.update(FREEFORM_PHRASE_EN)
    replacements.update(FREEFORM_TEXT_EN)
    for source, target in sorted(replacements.items(), key=lambda item: len(str(item[0])), reverse=True):
        source = str(source or "")
        target = str(target or "")
        if source and source in candidate:
            candidate = candidate.replace(source, target)
    for source, target in (
        ("、", ", "),
        ("，", ", "),
        ("；", "; "),
        ("：", ": "),
        ("（", " ("),
        ("）", ")"),
        ("“", '"'),
        ("”", '"'),
        ("。", "."),
    ):
        candidate = candidate.replace(source, target)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    candidate = re.sub(r"\s+([,;:.])", r"\1", candidate)
    if candidate and not contains_cjk(candidate):
        return candidate

    terms = []
    for keyword in keywords or []:
        translated = english_freeform_text(keyword)
        if translated and not contains_cjk(translated) and translated not in terms:
            terms.append(translated)
    for mapping in (FREEFORM_TEXT_EN, FREEFORM_PHRASE_EN, CONTEXT_KEYWORD_EN, SUMMARY_TERM_LABEL_EN, PANEL_I18N_EN):
        for source, target in mapping.items():
            if source in text and target and target not in terms and not contains_cjk(target):
                terms.append(target)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+#./:-]{1,}", text):
        normalized = normalize_brand_display_text(token)
        if normalized.lower() in STOPWORDS:
            continue
        if normalized not in terms:
            terms.append(normalized)
    if terms:
        summary = ", ".join(terms[:6])
        return "{}: {}".format(fallback_label, summary) if fallback_label else summary
    return fallback_label or ""


def english_record_text(item, field, fallback_label=""):
    if not isinstance(item, dict):
        return fallback_label or ""
    preferred = localized_record_field(item, field, language="en", default="")
    if preferred and not contains_cjk(preferred):
        return preferred
    source = preferred or localized_record_field(item, field, language="zh", default=item.get(field, ""))
    return english_freeform_text(source, fallback_label=fallback_label, keywords=item.get("tags", []))


def panel_i18n_json():
    return json.dumps(PANEL_I18N_EN, ensure_ascii=False).replace("</", "<\\/")


def normalize_memory_signature_text(text):
    compact = " ".join(str(text or "").split()).strip().lower()
    if not compact:
        return ""
    compact = re.sub(r"[`\"'“”‘’]+", "", compact)
    return compact


def build_memory_group_key(item, bucket=""):
    bucket_value = bucket or item.get("bucket", "") or "unknown"
    memory_type = item.get("memory_type", "") or "semantic"
    primary_text = item.get("title", "") or item.get("value_note", "")
    normalized = normalize_memory_signature_text(primary_text) or "untitled"
    return "{}::{}::{}".format(bucket_value, memory_type, normalized)


MEMORY_USAGE_STOP_TERMS = ASSET_VALUE_STOP_TERMS | {
    "current",
    "dashboard",
    "default",
    "openrelix",
    "overview",
    "panel",
    "project",
    "today",
    "tomorrow",
    "window",
    "windows",
    "工作台",
    "当天",
    "当前",
    "默认",
    "今天",
    "品牌",
    "展示页",
    "工作",
    "项目",
    "面板",
    "用户",
    "需要",
    "可以",
    "已经",
    "应该",
    "一次",
}


def cjk_usage_ngrams(text, min_size=3, max_size=4, limit=18):
    terms = []
    for run in re.findall(r"[\u4e00-\u9fff]{%d,}" % min_size, str(text or "")):
        max_n = min(max_size, len(run))
        for size in range(max_n, min_size - 1, -1):
            for index in range(0, len(run) - size + 1):
                term = run[index : index + size]
                if term not in MEMORY_USAGE_STOP_TERMS and term not in terms:
                    terms.append(term)
                    if len(terms) >= limit:
                        return terms
    return terms


def memory_usage_search_terms(item):
    raw_terms = [
        item.get("display_title", ""),
        item.get("title", ""),
        item.get("title_zh", ""),
        item.get("title_en", ""),
        item.get("display_value_note", ""),
        item.get("value_note", ""),
        item.get("value_note_zh", ""),
        item.get("value_note_en", ""),
    ]
    raw_terms.extend(item.get("keywords", []) or [])

    terms = []
    for raw_term in raw_terms:
        raw_text = str(raw_term or "")
        for term in cjk_usage_ngrams(raw_text):
            if term not in terms:
                terms.append(term)
        normalized = normalize_value_match_text(raw_text)
        compact = normalized.replace(" ", "")
        if 6 <= len(compact) <= 48 and compact not in MEMORY_USAGE_STOP_TERMS:
            terms.append(compact)
        for part in normalized.split():
            if part in MEMORY_USAGE_STOP_TERMS:
                continue
            has_cjk = bool(re.search(r"[\u4e00-\u9fff]", part))
            min_length = 2 if has_cjk else 4
            if len(part) >= min_length and len(part) <= 48:
                terms.append(part)

    deduped = []
    for term in terms:
        if term and term not in deduped:
            deduped.append(term)
    return deduped[:32]


def memory_usage_window_text(window):
    parts = [
        window.get("project_label", ""),
        window.get("cwd_display", ""),
        window.get("cwd", ""),
        context_window_text(window),
    ]
    return " ".join(part for part in parts if part)


def memory_context_matches_window(memory_item, window):
    labels = list(memory_item.get("context_labels", []) or [])
    if memory_item.get("display_context"):
        labels.append(memory_item.get("display_context", ""))
    project_label = normalize_value_match_text(window.get("project_label", ""))
    cwd_text = normalize_value_match_text(window.get("cwd_display", "") or window.get("cwd", ""))
    for label in labels:
        normalized = normalize_value_match_text(label)
        if normalized and (normalized in project_label or normalized in cwd_text):
            return True
    return False


def memory_usage_recency_weight(anchor_date, window_date):
    anchor = parse_nightly_summary_date({"date": anchor_date})
    current = parse_nightly_summary_date({"date": window_date})
    if anchor is None or current is None:
        return 1.0
    age_days = max((anchor - current).days, 0)
    return max(0.55, 1.0 - min(age_days, MEMORY_USAGE_WINDOW_DAYS - 1) * 0.07)


def filter_memory_usage_occurrence_dates(anchor_date, occurrence_dates):
    anchor = parse_nightly_summary_date({"date": anchor_date})
    if anchor is None:
        return []

    recent_dates = []
    for raw_date in occurrence_dates or []:
        current = parse_nightly_summary_date({"date": str(raw_date or "")[:10]})
        if current is None:
            continue
        age_days = (anchor - current).days
        if 0 <= age_days < MEMORY_USAGE_WINDOW_DAYS:
            recent_dates.append(current.isoformat())
    return recent_dates


def estimate_memory_window_likelihood(memory_item, window, terms, direct_window_ids):
    window_id = window.get("window_id", "")
    direct = bool(window_id and window_id in direct_window_ids)
    compact_text = compact_value_match_text(memory_usage_window_text(window))
    matched_terms = [term for term in terms if term and term in compact_text]
    strong_matches = [
        term
        for term in matched_terms
        if len(term) >= 6 or bool(re.search(r"[\u4e00-\u9fff]{3,}", term))
    ]
    context_match = memory_context_matches_window(memory_item, window)

    likelihood = 1.0 if direct else 0.0
    if len(matched_terms) >= 4:
        likelihood = max(likelihood, 0.85)
    elif len(matched_terms) >= 3:
        likelihood = max(likelihood, 0.72)
    elif len(matched_terms) >= 2:
        likelihood = max(likelihood, 0.58)
    elif strong_matches:
        likelihood = max(likelihood, 0.42)

    if context_match and matched_terms:
        likelihood = min(1.0, likelihood + 0.1)

    return {
        "window_id": window_id,
        "direct": direct,
        "likelihood": likelihood,
        "matched_terms": matched_terms[:5],
        "context_match": context_match,
    }


def build_memory_usage_frequency(memory_item, usage_window_overview, recent_occurrence_dates=None):
    windows = (usage_window_overview or {}).get("windows", [])
    anchor_date = (usage_window_overview or {}).get("date", "") or current_local_datetime().date().isoformat()
    source_windows = memory_item.get("source_windows", []) or []
    direct_window_ids = {
        ref.get("window_id", "")
        for ref in source_windows
        if ref.get("window_id", "")
    }
    terms = memory_usage_search_terms(memory_item)

    score = 0.0
    direct_matches = 0
    estimated_matches = 0
    context_hints = 0
    matched_window_ids = []

    for window in windows:
        result = estimate_memory_window_likelihood(memory_item, window, terms, direct_window_ids)
        likelihood = result["likelihood"]
        if likelihood <= 0:
            continue
        weighted = likelihood * memory_usage_recency_weight(anchor_date, window.get("date", ""))
        score += weighted
        if result["direct"]:
            direct_matches += 1
        elif likelihood >= 0.42:
            estimated_matches += 1
        else:
            context_hints += 1
        if result["window_id"]:
            matched_window_ids.append(result["window_id"])

    recent_occurrence_dates = filter_memory_usage_occurrence_dates(
        anchor_date,
        recent_occurrence_dates or [],
    )
    occurrence_floor = len(set(recent_occurrence_dates)) * 0.45
    score = max(score, occurrence_floor)
    score = round(score, 2)

    if score >= 10:
        display_score = str(int(round(score)))
    else:
        display_score = "{:.1f}".format(score).rstrip("0").rstrip(".")
    if not display_score:
        display_score = "0"

    return {
        "usage_frequency": score,
        "usage_frequency_display": display_score,
        "usage_frequency_window_days": MEMORY_USAGE_WINDOW_DAYS,
        "usage_frequency_direct_window_count": direct_matches,
        "usage_frequency_estimated_window_count": estimated_matches,
        "usage_frequency_context_hint_count": context_hints,
        "usage_frequency_matched_window_count": len(set(matched_window_ids)),
        "usage_frequency_terms": terms[:12],
        "usage_frequency_sort_key": score,
    }


def memory_usage_sort_key(item):
    return (
        safe_float(item.get("usage_frequency_sort_key", item.get("usage_frequency", 0))),
        safe_int(item.get("usage_frequency_matched_window_count", 0)),
        safe_int(item.get("occurrence_count", 0)),
        memory_sort_key(item.get("updated_at", "")),
        memory_sort_key(item.get("created_at", "")),
        item.get("title", ""),
    )


def sort_memory_rows_by_usage(rows):
    return sorted(rows, key=memory_usage_sort_key, reverse=True)


def memory_sort_key(value):
    parsed = parse_iso_datetime(value)
    if parsed is not None:
        return parsed.isoformat()
    return str(value or "")


def display_memory_date(value):
    parsed = parse_iso_datetime(value)
    if parsed is not None:
        return parsed.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else (text or "时间未知")


def extract_terms_from_text(text):
    terms = []
    if not text:
        return terms

    for chunk in re.findall(r"[\u4e00-\u9fff]{2,10}", text):
        normalized = normalize_term(chunk)
        if normalized:
            terms.append(normalized)

    raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,40}", text)
    for token in raw_tokens:
        parts = re.split(r"[_-]+", token)
        for part in parts:
            lowered = part.lower()
            if lowered not in TERM_ALIASES and not re.fullmatch(r"[A-Z]{2,5}", part):
                continue
            normalized = normalize_term(part)
            if normalized:
                terms.append(normalized)

    return terms


SUMMARY_TERM_NOISY_TOKENS = {
    "users",
    "entry",
    "entries",
    "used",
    "safe",
    "simple",
    "active",
    "personal",
    "general",
    "scope",
    "summary",
    "value",
    "note",
}


def add_summary_text_terms(counter, text, weight=1):
    for term in extract_terms_from_text(str(text or "")):
        counter[term] += weight


def add_summary_keyword_term(counter, keyword, weight=2):
    text = normalize_brand_display_text(str(keyword or "")).strip()
    if not text:
        return
    normalized = normalize_term(text)
    if normalized:
        counter[normalized] += weight
        return
    add_summary_text_terms(counter, text, weight=weight)


def prune_summary_term_counter(counter):
    noisy = {
        token.lower()
        for token in SUMMARY_TERM_NOISY_TOKENS
    }
    for token in list(counter.keys()):
        if token.lower() in noisy:
            del counter[token]


def summary_counter_rows(counter, limit=18):
    prune_summary_term_counter(counter)
    rows = [
        {"label": key, "value": value}
        for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        if value > 0
    ]
    return rows[:limit]


def add_asset_summary_terms(counter, asset):
    sources = [
        asset.get("display_title", ""),
        asset.get("title", ""),
        asset.get("type", ""),
        asset.get("domain", ""),
        asset.get("scope", ""),
        asset.get("display_value_note", ""),
        asset.get("value_note", ""),
        asset.get("display_notes", ""),
        asset.get("notes", ""),
        " ".join(asset.get("tags", [])),
    ]
    for source in sources:
        add_summary_text_terms(counter, source)


def add_review_summary_terms(counter, review):
    sources = [
        review.get("task", ""),
        review.get("domain", ""),
        review.get("repo", ""),
        review.get("text", ""),
    ]
    for source in sources:
        add_summary_text_terms(counter, source)


def add_usage_event_summary_terms(counter, event):
    sources = [
        event.get("display_task", ""),
        event.get("task", ""),
        event.get("display_note", ""),
        event.get("note", ""),
        event.get("asset_id", ""),
    ]
    for source in sources:
        add_summary_text_terms(counter, source)


def add_window_summary_terms(counter, window):
    sources = [
        window.get("project_label", ""),
        window.get("cwd_display", ""),
        window.get("question_summary", ""),
        window.get("main_takeaway", ""),
        " ".join(row.get("text", "") for row in window.get("recent_prompts", [])),
        " ".join(row.get("text", "") for row in window.get("recent_conclusions", [])),
    ]
    for keyword in window.get("keywords", []) or []:
        add_summary_keyword_term(counter, keyword, weight=2)
    for source in sources:
        add_summary_text_terms(counter, source)


def add_nightly_summary_terms(counter, nightly):
    if not nightly:
        return
    for keyword in nightly.get("keywords", []) or []:
        add_summary_keyword_term(counter, keyword, weight=3)
    sources = [
        nightly.get("day_summary", ""),
        nightly.get("summary", ""),
    ]
    for source in sources:
        add_summary_text_terms(counter, source)
    for window in nightly.get("window_summaries", []) or []:
        add_window_summary_terms(counter, window)
    for key in ("durable_memories", "session_memories", "low_priority_memories"):
        for item in nightly.get(key, []) or []:
            for keyword in item.get("keywords", []) or []:
                add_summary_keyword_term(counter, keyword, weight=2)
            for source in (
                item.get("title", ""),
                item.get("value_note", ""),
                item.get("source_task", ""),
            ):
                add_summary_text_terms(counter, source)


def build_summary_terms(
    assets,
    reviews,
    usage_events,
    nightly_payloads=None,
    window_overview=None,
):
    counter = Counter()

    for asset in assets:
        add_asset_summary_terms(counter, asset)
    for review in reviews:
        add_review_summary_terms(counter, review)
    for event in usage_events:
        add_usage_event_summary_terms(counter, event)
    for nightly in nightly_payloads or []:
        add_nightly_summary_terms(counter, nightly)
    for window in (window_overview or {}).get("windows", []) or []:
        add_window_summary_terms(counter, window)

    return summary_counter_rows(counter)


def parse_record_date(value):
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = parse_nightly_summary_date({"date": text[:10]})
    return parsed.isoformat() if parsed else ""


def record_date_values(record, keys):
    dates = []
    for key in keys:
        date_str = parse_record_date(record.get(key, ""))
        if date_str and date_str not in dates:
            dates.append(date_str)
    return dates


def filter_records_by_dates(records, date_set, keys):
    return [
        record
        for record in records
        if any(date_str in date_set for date_str in record_date_values(record, keys))
    ]


def nightly_payloads_for_dates(candidates, date_values):
    payloads = []
    seen_dates = set()
    for date_str in date_values:
        if not date_str or date_str in seen_dates:
            continue
        payload = select_best_nightly_summary_for_date(candidates or [], date_str)
        if payload:
            payloads.append(payload)
            seen_dates.add(date_str)
    return payloads


def summary_term_range_label(days, language=None):
    days = safe_int(days) or SUMMARY_TERM_DEFAULT_DAYS
    if days == 1:
        return localized("今日", "Today", language)
    return localized("近 {} 日".format(days), "Last {}".format(plural_en(days, "day")), language)


def summary_term_range_label_html(days):
    days = safe_int(days) or SUMMARY_TERM_DEFAULT_DAYS
    if days == 1:
        return panel_language_text_html("今日", "Today")
    return panel_language_text_html(
        "近 {} 日".format(days),
        "Last {}".format(plural_en(days, "day")),
    )


def summary_term_title(days, language=None):
    days = safe_int(days) or SUMMARY_TERM_DEFAULT_DAYS
    if days == 1:
        return localized("今日热词", "Today Hot Terms", language)
    return localized(
        "近 {} 日热词".format(days),
        "Last {} Hot Terms".format(plural_en(days, "day")),
        language,
    )


def build_summary_term_views(
    assets,
    reviews,
    usage_events,
    nightly_candidates,
    anchor_date,
    latest_nightly=None,
    language=None,
):
    language = current_language(language)
    views = []
    for days in SUMMARY_TERM_RANGE_DAYS:
        date_values = date_strings_ending_at(anchor_date, days)
        date_set = set(date_values)
        range_assets = filter_records_by_dates(assets, date_set, ("updated_at", "created_at", "date"))
        range_reviews = filter_records_by_dates(reviews, date_set, ("date",))
        range_usage_events = filter_records_by_dates(usage_events, date_set, ("date", "created_at", "updated_at"))
        nightly_payloads = nightly_payloads_for_dates(nightly_candidates, date_values)
        window_overview = build_context_window_overview_for_days(
            anchor_date,
            days,
            latest_nightly=latest_nightly,
            language=language,
        )
        rows = build_summary_terms(
            range_assets,
            range_reviews,
            range_usage_events,
            nightly_payloads=nightly_payloads,
            window_overview=window_overview,
        )
        source_dates = sorted(
            set(window_overview.get("source_dates", []))
            | {
                parse_nightly_summary_date(payload).isoformat()
                for payload in nightly_payloads
                if parse_nightly_summary_date(payload) is not None
            }
            | {
                date_str
                for record in range_assets
                + range_reviews
                + range_usage_events
                for date_str in record_date_values(record, ("date", "created_at", "updated_at"))
            },
            reverse=True,
        )
        views.append(
            {
                "days": days,
                "label": summary_term_range_label(days, language=language),
                "label_zh": summary_term_range_label(days, language="zh"),
                "label_en": summary_term_range_label(days, language="en"),
                "title": summary_term_title(days, language=language),
                "title_zh": summary_term_title(days, language="zh"),
                "title_en": summary_term_title(days, language="en"),
                "terms": rows,
                "source_dates": source_dates,
                "scanned_dates": date_values,
                "window_count": window_overview.get("window_count", 0),
                "asset_count": len(range_assets),
                "review_count": len(range_reviews),
                "usage_event_count": len(range_usage_events),
                "nightly_count": len(nightly_payloads),
            }
        )
    return views


def default_summary_term_view(summary_term_views):
    for view in summary_term_views or []:
        if safe_int(view.get("days", 0)) == SUMMARY_TERM_DEFAULT_DAYS:
            return view
    return (summary_term_views or [{}])[0] if summary_term_views else {}


def compact_preview_text(text, limit=220):
    normalized = str(text or "")
    if not normalized:
        return ""

    def shorten_preview_path(token):
        match = re.match(r"^(.*?)([.,;!?)]*)$", token)
        core = match.group(1) if match else token
        suffix = match.group(2) if match else ""
        prefix = ""
        body = core
        if core.startswith("file://"):
            prefix = "file://"
            body = core[len(prefix) :]
        if len(body) <= 32 or "/" not in body:
            return token
        leaf = body.rstrip("/").rsplit("/", 1)[-1]
        return "{}…/{}{}".format(prefix, leaf, suffix)

    # Strip markdown-only noise so compact previews read like UI copy instead of raw transcripts.
    normalized = re.sub(r"\[Image #\d+\]", "", normalized)
    normalized = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", normalized)
    normalized = re.sub(r"`([^`]+)`", r"\1", normalized)
    normalized = re.sub(
        r"file://[^\s)]+",
        lambda match: shorten_preview_path(match.group(0)),
        normalized,
    )
    normalized = re.sub(
        r"/Users/[^\s)]+",
        lambda match: shorten_preview_path(match.group(0)),
        normalized,
    )
    normalized = normalized.replace("|", " / ")
    normalized = re.sub(r"\s+", " ", normalized.strip())
    normalized = normalize_brand_display_text(normalized)
    if not normalized:
        return ""
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 1, 1)].rstrip() + "…"


def split_path_trailing_punctuation(token):
    core = str(token or "")
    suffix = ""
    while core and core[-1] in LOCAL_PATH_TRAILING_PUNCTUATION:
        suffix = core[-1] + suffix
        core = core[:-1]
    return core, suffix


def strip_line_column_suffix(path_text):
    candidate = str(path_text or "")
    while True:
        stripped = re.sub(r":\d+$", "", candidate)
        if stripped == candidate:
            return candidate
        candidate = stripped


def resolve_local_link_path(raw_path):
    candidate = str(raw_path or "").strip()
    if not candidate:
        return None

    if candidate.startswith("file://"):
        parsed = urlparse(candidate)
        if parsed.scheme != "file":
            return None
        candidate = unquote(parsed.path or "")
        if parsed.netloc and parsed.netloc not in {"", "localhost"}:
            candidate = "//{}{}".format(parsed.netloc, candidate)

    candidate = strip_line_column_suffix(candidate)
    if candidate.startswith("~/"):
        path = Path(candidate).expanduser()
    else:
        path = Path(candidate)

    if not path.is_absolute():
        return None

    try:
        if not path.exists():
            return None
        return path.resolve()
    except OSError:
        return None


def build_local_path_anchor(path, label, class_name="path-link"):
    resolved = resolve_local_link_path(path) if not isinstance(path, Path) else path.resolve()
    safe_label = escape(normalize_brand_display_text(label))
    if not resolved:
        return safe_label
    return (
        '<a class="{class_name}" href="{href}" target="_blank" rel="noopener noreferrer" title="{title}">{label}</a>'
    ).format(
        class_name=escape(class_name, quote=True),
        href=escape(resolved.as_uri(), quote=True),
        title=escape(str(resolved), quote=True),
        label=safe_label,
    )


def render_local_path_link(path, label=None, class_name="path-link"):
    display_label = path if label is None else label
    return build_local_path_anchor(path, display_label, class_name=class_name)


def resolve_asset_primary_artifact_path(asset):
    for raw_path in asset.get("artifact_paths", []) or []:
        resolved = resolve_local_link_path(raw_path)
        if resolved:
            return resolved
    return None


def render_asset_title_link(asset):
    title = (
        localized_record_field(asset, "title", default="")
        or asset.get("title", "")
        or asset.get("id", "")
        or "未命名资产"
    )
    title_en = (
        english_record_text(asset, "title", fallback_label="Asset")
        or humanize_identifier(asset.get("id", ""))
        or "Untitled asset"
    )
    resolved = resolve_asset_primary_artifact_path(asset)
    if not resolved:
        return panel_language_text_html(title, title_en)
    return panel_language_variant_html(
        build_local_path_anchor(
            resolved,
            title,
            class_name="path-link asset-title-link",
        ),
        build_local_path_anchor(
            resolved,
            title_en,
            class_name="path-link asset-title-link",
        ),
    )


def render_jump_link(target_id, label, class_name="path-link"):
    safe_label = escape(normalize_brand_display_text(label))
    if not target_id:
        return safe_label
    return '<a class="{class_name}" href="#{target_id}">{label}</a>'.format(
        class_name=escape(class_name, quote=True),
        target_id=escape(str(target_id), quote=True),
        label=safe_label,
    )


def render_detected_local_path_token(token, class_name="path-link"):
    core, suffix = split_path_trailing_punctuation(token)
    resolved = resolve_local_link_path(core)
    if not resolved:
        return None
    return "{}{}".format(
        build_local_path_anchor(resolved, core, class_name=class_name),
        escape(suffix),
    )


def linkify_local_paths_html(text, class_name="path-link"):
    raw = str(text or "")
    if not raw:
        return ""

    pieces = []
    cursor = 0
    matched = False
    for match in LOCAL_PATH_TOKEN_RE.finditer(raw):
        rendered = render_detected_local_path_token(match.group(0), class_name=class_name)
        if rendered is None:
            continue
        matched = True
        pieces.append(escape(raw[cursor:match.start()]))
        pieces.append(rendered)
        cursor = match.end()

    if not matched:
        return escape(raw)

    pieces.append(escape(raw[cursor:]))
    return "".join(pieces)


def latest_window_activity(window):
    candidates = [window.get("started_at", "")]
    candidates.extend(item.get("local_time", "") for item in window.get("prompts", []))
    candidates.extend(item.get("completed_at", "") for item in window.get("conclusions", []))
    parsed = [parse_iso_datetime(value) for value in candidates if value]
    parsed = [value for value in parsed if value]
    return max(parsed) if parsed else None


def localize_window_preview_text(text, language=None, keywords=None, label="Focus"):
    text = normalize_brand_display_text(str(text or ""))
    if is_english(language):
        return english_context_preview(text, keywords or [], label=label)
    return text


def assign_window_display_indices(items):
    total = len(items or [])
    for offset, item in enumerate(items or []):
        item["display_index"] = total - offset


def make_window_preview_items(rows, time_key, limit, fallback):
    previews = []
    for row in reversed(rows[-limit:]):
        text = normalize_brand_display_text(compact_preview_text(row.get("text", "")))
        if not text:
            continue
        previews.append(
            {
                "time": display_short_local_datetime(row.get(time_key, "")),
                "text": text,
            }
        )
    if previews:
        return previews
    return [{"time": "", "text": normalize_brand_display_text(fallback)}]


def path_is_within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def trim_path_parts(parts, limit=3):
    visible = [part for part in parts if part]
    if not visible:
        return ""
    if len(visible) <= limit:
        return "/".join(visible)
    return "/".join((visible[0], "…", visible[-1]))


def humanize_identifier(value):
    text = str(value or "").strip().strip("/")
    if not text:
        return ""

    pieces = [piece for piece in re.split(r"[_\-\s]+", text) if piece]
    if not pieces:
        return text

    rendered = []
    for piece in pieces:
        lowered = piece.lower()
        if lowered in ACRONYM_LABELS:
            rendered.append(ACRONYM_LABELS[lowered])
        elif piece.isupper() or any(char.isupper() for char in piece[1:]):
            rendered.append(piece)
        else:
            rendered.append(piece.capitalize())
    return normalize_brand_display_text(" ".join(rendered))


def detect_special_context_from_path(path):
    if path_is_within(path, ROOT):
        return humanize_identifier(ROOT.name)
    if path_is_within(path, PATHS.codex_home):
        return "Codex 本地环境"
    if PATHS.state_root != ROOT and path_is_within(path, PATHS.state_root):
        return "OpenRelix"
    return ""


def infer_fallback_project_segment(path):
    parts = path.parts
    home = Path.home()
    if path_is_within(path, home):
        parts = path.relative_to(home).parts

    filtered = [
        part
        for part in parts
        if part and part != Path(parts[0]).anchor and part.lower() not in GENERIC_PATH_PARTS
    ]
    if not filtered:
        return ""
    if path_is_within(path, home) and len(filtered) == 1:
        return ""

    candidate = filtered[-1]
    if candidate.lower() in GENERIC_PROJECT_LEAF_NAMES and len(filtered) >= 2:
        candidate = filtered[-2]
    return candidate


def has_project_root_marker(directory):
    if not directory.exists() or not directory.is_dir():
        return False
    if any((directory / marker).exists() for marker in PROJECT_ROOT_MARKERS):
        return True
    try:
        for child in directory.iterdir():
            if child.name.endswith(PROJECT_ROOT_SUFFIXES):
                return True
    except OSError:
        return False
    return False


@lru_cache(maxsize=512)
def detect_project_root(raw_path):
    if not raw_path:
        return None

    path = Path(str(raw_path)).expanduser()
    special_context = detect_special_context_from_path(path)
    if special_context == humanize_identifier(ROOT.name):
        return ROOT
    if special_context:
        return None

    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path

    special_context = detect_special_context_from_path(resolved)
    if special_context == humanize_identifier(ROOT.name):
        return ROOT
    if special_context:
        return None

    for ancestor in (resolved, *resolved.parents):
        if (ancestor / ".git").exists():
            return ancestor

    for ancestor in (resolved, *resolved.parents):
        if has_project_root_marker(ancestor):
            return ancestor
        if ancestor == Path.home():
            break
    return None


def infer_repo_name_from_path(raw_path):
    if not raw_path:
        return ""

    path = Path(str(raw_path)).expanduser()
    special_context = detect_special_context_from_path(path)
    if special_context:
        return special_context

    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path

    special_context = detect_special_context_from_path(resolved)
    if special_context:
        return special_context

    project_root = detect_project_root(raw_path)
    if project_root:
        return humanize_identifier(project_root.name)

    fallback_segment = infer_fallback_project_segment(resolved)
    if fallback_segment:
        return humanize_identifier(fallback_segment)
    return ""


def compact_cwd_display(raw_path):
    if not raw_path:
        return "暂无工作目录"

    path = Path(str(raw_path)).expanduser()
    special_context = detect_special_context_from_path(path)
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path

    if not special_context:
        special_context = detect_special_context_from_path(resolved)
    project_label = infer_repo_name_from_path(raw_path)
    if special_context and special_context != humanize_identifier(ROOT.name):
        base_root = PATHS.codex_home if special_context == "Codex 本地环境" else PATHS.state_root
        try:
            relative = resolved.relative_to(base_root)
        except ValueError:
            relative = Path()
        suffix = trim_path_parts(relative.parts, limit=3)
        return "{} / {}".format(special_context, suffix) if suffix else special_context

    project_root = detect_project_root(raw_path)
    if project_root:
        try:
            relative = resolved.relative_to(project_root)
        except ValueError:
            relative = Path()
        suffix = trim_path_parts(relative.parts, limit=3)
        return "{} / {}".format(project_label, suffix) if suffix else project_label

    fallback_segment = infer_fallback_project_segment(resolved)
    if fallback_segment:
        label = humanize_identifier(fallback_segment)
        filtered_parts = [part for part in resolved.parts if part and part != resolved.anchor]
        try:
            index = filtered_parts.index(fallback_segment)
        except ValueError:
            index = -1
        if index >= 0:
            suffix = trim_path_parts(filtered_parts[index + 1 :], limit=3)
            return "{} / {}".format(label, suffix) if suffix else label
        return label

    home = Path.home()
    if path_is_within(resolved, home):
        relative = resolved.relative_to(home)
        return "~/{}".format(trim_path_parts(relative.parts, limit=3))

    filtered_parts = [part for part in resolved.parts if part and part != resolved.anchor]
    return trim_path_parts(filtered_parts, limit=4) or str(resolved)


def collect_known_project_names(window_overview):
    names = []
    for item in (window_overview or {}).get("windows", []):
        project_name = infer_repo_name_from_path(item.get("cwd", ""))
        if (
            project_name
            and project_name not in NON_PROJECT_CONTEXT_LABELS
            and project_name not in names
        ):
            names.append(project_name)
    root_label = humanize_identifier(ROOT.name)
    if root_label not in names:
        names.append(root_label)
    return names


def infer_context_label_from_text(text, known_project_names=None):
    lowered = " ".join((text or "").split()).lower()
    if not lowered:
        return ""

    for project_name in known_project_names or []:
        if project_name and project_name.lower() in lowered:
            return project_name

    for label, keywords in CONTEXT_TEXT_RULES:
        if any(keyword.lower() in lowered for keyword in keywords):
            return label
    return ""


def context_window_text(item):
    parts = [
        item.get("question_summary", ""),
        item.get("main_takeaway", ""),
        " ".join(item.get("keywords", [])),
        " ".join(row.get("text", "") for row in item.get("recent_prompts", [])),
        " ".join(row.get("text", "") for row in item.get("recent_conclusions", [])),
    ]
    return " ".join(part for part in parts if part)


def normalize_context_topic_key(label):
    compact = re.sub(r"\s+", "", str(label or "").lower())
    compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", compact)
    return compact or "untitled"


def useful_context_keywords(keywords):
    useful = []
    for keyword in keywords or []:
        text = str(keyword or "").strip()
        if not text or text in CONTEXT_TOPIC_GENERIC_KEYWORDS:
            continue
        if text not in useful:
            useful.append(text)
    return useful


def is_noisy_context_topic_candidate(text):
    candidate = str(text or "").strip()
    if not candidate:
        return True

    lowered = candidate.lower()
    if lowered in {"暂无问题摘要。", "暂无结论摘要。", "no question summary.", "no conclusion summary."}:
        return True
    if candidate.startswith("-"):
        return True
    if any(marker in lowered for marker in CONTEXT_TOPIC_NOISY_MARKERS):
        return True
    if "://" in candidate:
        return True
    if candidate.count("/") >= 4 or candidate.count(":") >= 3:
        return True

    dense_tokens = re.findall(r"[A-Za-z0-9_./:-]{18,}", candidate)
    return len(dense_tokens) >= 2


def fallback_context_topic_label(item, language=None):
    keywords = useful_context_keywords(item.get("keywords", []))
    if keywords:
        display_keywords = [localized_context_keyword(keyword, language=language) for keyword in keywords[:2]]
        display_keywords = [keyword for keyword in display_keywords if keyword]
        return localized(
            "其他需求：{}".format(" / ".join(keywords[:2])),
            "Other needs: {}".format(" / ".join(display_keywords or keywords[:2])),
            language,
        )

    for field, empty_text in (
        ("question_summary", localized("暂无问题摘要。", "No question summary.", language)),
        ("main_takeaway", localized("暂无结论摘要。", "No conclusion summary.", language)),
    ):
        candidate = compact_preview_text(item.get(field, ""), limit=28)
        if candidate and candidate != empty_text and not is_noisy_context_topic_candidate(candidate):
            return localized(
                "其他需求：{}".format(candidate),
                "Other needs: {}".format(candidate),
                language,
            )

    return localized("其他需求", "Other needs", language)


def infer_context_topic_label(item, language=None):
    text = context_window_text(item)
    lowered = " ".join(text.split()).lower()
    for label, keywords in CONTEXT_TOPIC_RULES:
        if any(keyword.lower() in lowered for keyword in keywords):
            return localized_topic_label(label, language)

    return fallback_context_topic_label(item, language=language)


def resolve_asset_context(asset, known_project_names):
    artifact_paths = asset.get("artifact_paths", []) or []
    fallback_special_context = ""
    for raw_path in artifact_paths:
        project_name = infer_repo_name_from_path(raw_path)
        if project_name:
            if project_name not in NON_PROJECT_CONTEXT_LABELS:
                return project_name
            if not fallback_special_context:
                fallback_special_context = project_name

    fallback = display_label("domain", asset.get("domain", ""))
    if fallback:
        return fallback

    text_sources = [
        asset.get("display_title", ""),
        asset.get("title", ""),
        asset.get("display_value_note", ""),
        asset.get("value_note", ""),
        asset.get("display_notes", ""),
        asset.get("notes", ""),
        asset.get("display_source_task", ""),
        asset.get("source_task", ""),
        " ".join(asset.get("tags", [])),
        " ".join(str(path) for path in artifact_paths),
    ]
    inferred = infer_context_label_from_text(" ".join(text_sources), known_project_names)
    if inferred:
        return inferred

    return fallback_special_context or "未分类上下文"


def build_project_contexts(window_overview, language=None):
    language = current_language(language)
    if not window_overview or not window_overview.get("windows"):
        return []

    known_project_names = collect_known_project_names(window_overview)
    groups = {}

    for item in window_overview.get("windows", []):
        text_sources = [
            item.get("question_summary", ""),
            item.get("main_takeaway", ""),
            " ".join(item.get("keywords", [])),
            " ".join(row.get("text", "") for row in item.get("recent_prompts", [])),
            " ".join(row.get("text", "") for row in item.get("recent_conclusions", [])),
        ]
        label = item.get("project_label") or infer_repo_name_from_path(item.get("cwd", ""))
        if not label:
            label = infer_context_label_from_text(" ".join(text_sources), known_project_names)
        if not label:
            label = localized_context_label("个人工作区", language)
        label = localized_context_label(label, language)

        key = label.lower()
        group = groups.setdefault(
            key,
            {
                "label": label,
                "window_count": 0,
                "question_count": 0,
                "conclusion_count": 0,
                "latest_activity_at": "",
                "latest_activity_display": localized("时间未知", "Unknown time", language),
                "cwd_samples": [],
                "keywords": [],
                "summary_candidates": [],
                "question_samples": [],
                "takeaway_samples": [],
                "topics": {},
            },
        )

        group["window_count"] += 1
        group["question_count"] += item.get("question_count", 0)
        group["conclusion_count"] += item.get("conclusion_count", 0)

        cwd = item.get("cwd_display", "")
        if cwd and cwd not in group["cwd_samples"]:
            group["cwd_samples"].append(cwd)

        for keyword in item.get("keywords", []):
            display_keyword = localized_context_keyword(keyword, language=language)
            if display_keyword and display_keyword not in group["keywords"]:
                group["keywords"].append(display_keyword)

        question_preview = compact_preview_text(item.get("question_summary", ""), limit=140)
        if is_english(language):
            question_preview = english_context_preview(
                question_preview,
                item.get("keywords", []),
                label="Focus",
            )
        if question_preview and question_preview not in group["question_samples"]:
            group["question_samples"].append(question_preview)

        takeaway_preview = compact_preview_text(item.get("main_takeaway", ""), limit=160)
        if is_english(language):
            takeaway_preview = english_context_preview(
                takeaway_preview,
                item.get("keywords", []),
                label="Takeaway",
            )
        if takeaway_preview and takeaway_preview not in group["takeaway_samples"]:
            group["takeaway_samples"].append(takeaway_preview)

        for summary in (takeaway_preview, question_preview):
            compact = compact_preview_text(summary, limit=120)
            if compact and compact not in group["summary_candidates"]:
                group["summary_candidates"].append(compact)

        topic_label = infer_context_topic_label(item, language=language)
        topic_key = normalize_context_topic_key(topic_label)
        topic = group["topics"].setdefault(
            topic_key,
            {
                "label": topic_label,
                "window_count": 0,
                "question_count": 0,
                "conclusion_count": 0,
                "latest_activity_at": "",
                "latest_activity_display": localized("时间未知", "Unknown time", language),
                "keywords": [],
                "question_samples": [],
                "takeaway_samples": [],
            },
        )
        topic["window_count"] += 1
        topic["question_count"] += item.get("question_count", 0)
        topic["conclusion_count"] += item.get("conclusion_count", 0)
        for keyword in item.get("keywords", []):
            display_keyword = localized_context_keyword(keyword, language=language)
            if display_keyword and display_keyword not in topic["keywords"]:
                topic["keywords"].append(display_keyword)
        if question_preview and question_preview not in topic["question_samples"]:
            topic["question_samples"].append(question_preview)
        if takeaway_preview and takeaway_preview not in topic["takeaway_samples"]:
            topic["takeaway_samples"].append(takeaway_preview)
        topic_latest = parse_iso_datetime(topic["latest_activity_at"])
        item_latest = parse_iso_datetime(item.get("latest_activity_at", ""))
        if item_latest and (topic_latest is None or item_latest > topic_latest):
            topic["latest_activity_at"] = item_latest.isoformat()
            topic["latest_activity_display"] = item.get(
                "latest_activity_display",
                localized("时间未知", "Unknown time", language),
            )

        current_latest = parse_iso_datetime(group["latest_activity_at"])
        item_latest = parse_iso_datetime(item.get("latest_activity_at", ""))
        if item_latest and (current_latest is None or item_latest > current_latest):
            group["latest_activity_at"] = item_latest.isoformat()
            group["latest_activity_display"] = item.get(
                "latest_activity_display",
                localized("时间未知", "Unknown time", language),
            )

    rows = []
    for group in groups.values():
        question_preview = group["question_samples"][0] if group["question_samples"] else localized(
            "暂无代表问题。",
            "No representative question.",
            language,
        )
        takeaway_preview = group["takeaway_samples"][0] if group["takeaway_samples"] else localized(
            "暂无代表结论。",
            "No representative conclusion.",
            language,
        )
        summary_parts = []
        if question_preview:
            summary_parts.append(localized("问题：{}".format(question_preview), "Question: {}".format(question_preview), language))
        if takeaway_preview and takeaway_preview != question_preview:
            summary_parts.append(localized("结论：{}".format(takeaway_preview), "Conclusion: {}".format(takeaway_preview), language))
        topics = []
        for topic in group["topics"].values():
            topic_question = (
                topic["question_samples"][0]
                if topic["question_samples"]
                else localized("暂无代表问题。", "No representative question.", language)
            )
            topic_takeaway = (
                topic["takeaway_samples"][0]
                if topic["takeaway_samples"]
                else localized("暂无代表结论。", "No representative conclusion.", language)
            )
            topics.append(
                {
                    "label": topic["label"],
                    "window_count": topic["window_count"],
                    "question_count": topic["question_count"],
                    "conclusion_count": topic["conclusion_count"],
                    "latest_activity_at": topic["latest_activity_at"],
                    "latest_activity_display": topic["latest_activity_display"],
                    "question_preview": topic_question,
                    "takeaway_preview": topic_takeaway,
                    "keywords": useful_context_keywords(topic["keywords"])[:4],
                }
            )
        topics.sort(
            key=lambda item: (
                parse_iso_datetime(item.get("latest_activity_at", "")).timestamp()
                if item.get("latest_activity_at")
                else 0,
                item.get("window_count", 0),
                item.get("question_count", 0),
                item.get("label", ""),
            ),
            reverse=True,
        )
        rows.append(
            {
                "label": group["label"],
                "window_count": group["window_count"],
                "question_count": group["question_count"],
                "conclusion_count": group["conclusion_count"],
                "latest_activity_at": group["latest_activity_at"],
                "latest_activity_display": group["latest_activity_display"],
                "cwd_preview": " / ".join(group["cwd_samples"][:2]) or localized("暂无工作目录", "No working directory", language),
                "summary": (
                    ("; " if is_english(language) else "；").join(summary_parts[:2])
                    or localized("暂无可展示摘要。", "No displayable summary.", language)
                ),
                "question_preview": question_preview,
                "takeaway_preview": takeaway_preview,
                "keywords": group["keywords"][:4],
                "topic_count": len(topics),
                "topics": topics,
            }
        )

    rows.sort(
        key=lambda item: (
            parse_iso_datetime(item.get("latest_activity_at", "")).timestamp()
            if item.get("latest_activity_at")
            else 0,
            item.get("question_count", 0),
            item.get("conclusion_count", 0),
            item.get("label", ""),
        ),
        reverse=True,
    )
    return rows


def build_window_anchor_id(window_id):
    if not window_id:
        return ""
    return "window-{}".format(window_id)


@lru_cache(maxsize=2048)
def load_window_record(date_str, window_id):
    if not date_str or not window_id:
        return None
    path = PATHS.raw_windows_dir / str(date_str) / "{}.json".format(window_id)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["_path"] = str(path)
    return payload


def build_window_lookup(window_overview):
    lookup = {}
    if not window_overview:
        return lookup
    for item in window_overview.get("windows", []):
        window_id = item.get("window_id", "")
        if not window_id:
            continue
        enriched = dict(item)
        enriched["anchor_id"] = build_window_anchor_id(window_id)
        lookup[window_id] = enriched
    return lookup


def build_memory_source_window_ref(date_str, window_id, window_lookup, known_project_names=None):
    if not window_id:
        return {}

    current = (window_lookup or {}).get(window_id, {})
    raw_window = load_window_record(date_str, window_id)
    cwd = current.get("cwd") or (raw_window or {}).get("cwd", "")

    project_label = current.get("project_label") or infer_repo_name_from_path(cwd)
    if not project_label:
        text_sources = [
            current.get("question_summary", ""),
            current.get("main_takeaway", ""),
            " ".join(current.get("keywords", [])),
        ]
        if raw_window:
            text_sources.extend(
                [
                    " ".join(item.get("text", "") for item in raw_window.get("prompts", [])[-2:]),
                    " ".join(
                        item.get("text", "") for item in raw_window.get("conclusions", [])[-2:]
                    ),
                ]
            )
        project_label = infer_context_label_from_text(
            " ".join(text_sources),
            known_project_names,
        )
    if not project_label:
        project_label = "个人工作区"

    latest_activity_at = current.get("latest_activity_at", "")
    if not latest_activity_at and raw_window:
        latest_activity = latest_window_activity(raw_window)
        latest_activity_at = latest_activity.isoformat() if latest_activity else ""

    latest_activity_display = current.get("latest_activity_display") or display_short_local_datetime(
        latest_activity_at
    )
    if not latest_activity_display:
        latest_activity_display = "时间未知"

    cwd_display = current.get("cwd_display") or compact_cwd_display(cwd)
    return {
        "window_id": window_id,
        "window_id_short": window_id[:8],
        "date": date_str,
        "project_label": project_label,
        "cwd": cwd,
        "cwd_display": cwd_display or cwd or "暂无工作目录",
        "latest_activity_at": latest_activity_at,
        "latest_activity_display": latest_activity_display,
        "display_index": current.get("display_index"),
        "anchor_id": current.get("anchor_id", ""),
        "raw_path": current.get("raw_path") or (raw_window or {}).get("_path", ""),
        "session_file": (raw_window or {}).get("session_file", ""),
    }


def build_memory_registry(memory_items, window_overview, usage_window_overview=None, language=None):
    language = current_language(language)
    window_lookup = build_window_lookup(window_overview)
    known_project_names = collect_known_project_names(window_overview)
    groups = {}

    for item in memory_items:
        memory_key = item.get("memory_key") or build_memory_group_key(item)
        group = groups.setdefault(
            memory_key,
            {
                "memory_key": memory_key,
                "bucket": item.get("bucket", ""),
                "memory_type": item.get("memory_type", ""),
                "priority": item.get("priority", "medium"),
                "title": item.get("title", ""),
                "title_zh": item.get("title_zh", ""),
                "title_en": item.get("title_en", ""),
                "value_note": item.get("value_note", ""),
                "value_note_zh": item.get("value_note_zh", ""),
                "value_note_en": item.get("value_note_en", ""),
                "created_at": "",
                "updated_at": "",
                "occurrence_count": 0,
                "_latest_sort": "",
                "_latest_date": "",
                "_latest_source_window_ids": [],
                "_all_source_windows": {},
                "_context_labels": [],
                "_occurrence_dates": [],
            },
        )

        date_str = item.get("date", "") or item.get("updated_at", "") or item.get("created_at", "")
        group["occurrence_count"] += 1
        if date_str:
            group["_occurrence_dates"].append(date_str)

        if not group["created_at"] or memory_sort_key(date_str) < memory_sort_key(group["created_at"]):
            group["created_at"] = date_str
        if not group["updated_at"] or memory_sort_key(date_str) > memory_sort_key(group["updated_at"]):
            group["updated_at"] = date_str

        current_sort = memory_sort_key(date_str)
        if current_sort >= group["_latest_sort"]:
            group["_latest_sort"] = current_sort
            group["_latest_date"] = date_str
            group["priority"] = item.get("priority", group["priority"])
            group["title"] = item.get("title", group["title"])
            group["title_zh"] = item.get("title_zh", group.get("title_zh", ""))
            group["title_en"] = item.get("title_en", group.get("title_en", ""))
            group["value_note"] = item.get("value_note", group["value_note"])
            group["value_note_zh"] = item.get("value_note_zh", group.get("value_note_zh", ""))
            group["value_note_en"] = item.get("value_note_en", group.get("value_note_en", ""))
            group["_latest_source_window_ids"] = list(item.get("source_window_ids", []))

        for window_id in item.get("source_window_ids", []):
            ref = build_memory_source_window_ref(
                date_str,
                window_id,
                window_lookup,
                known_project_names,
            )
            if not ref:
                continue
            existing_ref = group["_all_source_windows"].get(window_id)
            if existing_ref is None or memory_sort_key(ref.get("date", "")) >= memory_sort_key(
                existing_ref.get("date", "")
            ):
                group["_all_source_windows"][window_id] = ref
            label = ref.get("project_label", "")
            if label and label not in group["_context_labels"]:
                group["_context_labels"].append(label)

    rows = []
    by_key = {}
    for group in groups.values():
        all_source_windows = sorted(
            group["_all_source_windows"].values(),
            key=lambda item: (
                memory_sort_key(item.get("date", "")),
                memory_sort_key(item.get("latest_activity_at", "")),
                item.get("window_id", ""),
            ),
            reverse=True,
        )

        source_windows = []
        for window_id in group.get("_latest_source_window_ids", []):
            ref = build_memory_source_window_ref(
                group.get("_latest_date", ""),
                window_id,
                window_lookup,
                known_project_names,
            )
            if ref and ref.get("window_id") and ref["window_id"] not in {
                row.get("window_id") for row in source_windows
            }:
                source_windows.append(ref)
        if not source_windows:
            source_windows = all_source_windows[:3]

        context_labels = list(group["_context_labels"])
        if not context_labels:
            inferred = infer_context_label_from_text(
                " ".join(
                    (
                        group.get("title", ""),
                        group.get("value_note", ""),
                        " ".join(row.get("project_label", "") for row in source_windows),
                    )
                ),
                known_project_names,
            )
            if inferred:
                context_labels.append(inferred)

        display_context = context_labels[0] if context_labels else "未分类上下文"
        cwd_preview = " / ".join(
            [
                row.get("cwd_display", "")
                for row in source_windows
                if row.get("cwd_display", "")
            ][:2]
        ) or display_context

        row = {
            "memory_key": group["memory_key"],
            "bucket": group["bucket"],
            "display_bucket": display_memory_bucket(group["bucket"], language=language),
            "memory_type": group["memory_type"],
            "display_memory_type": display_memory_type(group["memory_type"], language=language),
            "priority": group["priority"],
            "display_priority": display_memory_priority(group["priority"], language=language),
            "title": group["title"],
            "display_title": localized_record_field(group, "title", language=language, default=group["title"]),
            "display_title_en": localized_record_field(group, "title", language="en", default=group["title"]),
            "value_note": group["value_note"],
            "display_value_note": localized_record_field(
                group,
                "value_note",
                language=language,
                default=group["value_note"],
            ),
            "display_value_note_en": localized_record_field(
                group,
                "value_note",
                language="en",
                default=group["value_note"],
            ),
            "created_at": group["created_at"],
            "updated_at": group["updated_at"],
            "created_at_display": display_memory_date(group["created_at"]),
            "updated_at_display": display_memory_date(group["updated_at"]),
            "occurrence_count": group["occurrence_count"],
            "display_context": display_context,
            "context_labels": context_labels[:3],
            "cwd_preview": cwd_preview,
            "source_windows": source_windows[:3],
            "source_window_count": len(all_source_windows),
        }
        usage_row = dict(row)
        usage_row["source_windows"] = all_source_windows
        row.update(
            build_memory_usage_frequency(
                usage_row,
                usage_window_overview,
                recent_occurrence_dates=group.get("_occurrence_dates", []),
            )
        )
        rows.append(row)
        by_key[row["memory_key"]] = row

    rows.sort(
        key=lambda item: (
            item.get("bucket", "") in {"durable", "session"},
            item.get("usage_frequency_sort_key", 0),
            item.get("usage_frequency_matched_window_count", 0),
            item.get("occurrence_count", 0),
            memory_sort_key(item.get("updated_at", "")),
            memory_sort_key(item.get("created_at", "")),
            item.get("title", ""),
        ),
        reverse=True,
    )
    return {
        "rows": rows,
        "by_key": by_key,
        "counts": Counter(item.get("bucket", "") for item in rows),
    }


def extract_resolved_local_paths(text, prefer_parent=False):
    paths = []
    seen = set()
    for match in LOCAL_PATH_TOKEN_RE.finditer(str(text or "")):
        resolved = resolve_local_link_path(match.group(0))
        if not resolved:
            continue
        is_file = False
        if prefer_parent:
            try:
                is_file = resolved.is_file()
            except OSError:
                is_file = False
        target = resolved.parent if prefer_parent and is_file else resolved
        key = str(target)
        if key in seen:
            continue
        seen.add(key)
        paths.append(key)
    return paths


def normalize_context_match_text(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def collect_context_labels_from_texts(texts, known_project_names=None):
    combined = " ".join(part for part in texts if part)
    lowered = combined.lower()
    normalized_combined = normalize_context_match_text(combined)
    compact_combined = normalized_combined.replace(" ", "")
    labels = []

    for path in extract_resolved_local_paths(combined, prefer_parent=False):
        label = infer_repo_name_from_path(path)
        if label and label not in labels:
            labels.append(label)

    for project_name in known_project_names or []:
        normalized_project = normalize_context_match_text(project_name)
        compact_project = normalized_project.replace(" ", "")
        if not project_name or not normalized_project:
            continue
        if (
            normalized_project in normalized_combined
            or compact_project in compact_combined
            or project_name.lower() in lowered
        ) and project_name not in labels:
            labels.append(project_name)

    for label, keywords in CONTEXT_TEXT_RULES:
        if any(keyword.lower() in lowered for keyword in keywords) and label not in labels:
            labels.append(label)

    if not labels:
        inferred = infer_context_label_from_text(combined, known_project_names)
        if inferred:
            labels.append(inferred)

    return labels[:3]


def classify_codex_native_memory_type(title, desc="", learnings=""):
    combined = " ".join((title, desc, learnings)).lower()
    if any(
        keyword in combined
        for keyword in ("rule", "rules", "偏好", "preference", "约束", "guardrail", "边界")
    ):
        return "rule"
    if any(
        keyword in combined
        for keyword in (
            "mapping",
            "映射",
            "contract",
            "接口",
            "scope",
            "applies_to",
            "labeling",
        )
    ):
        return "mapping"
    if any(
        keyword in combined
        for keyword in (
            "workflow",
            "loop",
            "launchagent",
            "setup",
            "install",
            "installer",
            "runtime",
            "pipeline",
            "schedule",
            "scheduling",
            "验证",
            "验证路径",
            "排障",
            "review",
            "cleanup",
            "fallback",
            "路径",
            "how to",
        )
    ):
        return "procedural"
    return "semantic"


CODEX_NATIVE_TITLE_ZH = {
    "local codex personal asset system genericization and launchagent runtime": "本地 OpenRelix 系统、通用化与 LaunchAgent 运行时",
    "codex local configuration mcp setup token usage and plugin marketplace inspection": "Codex 本地配置、MCP、Token 使用与插件市场排查",
    "subreview run live contract and independent codex review loop": "/subreview:run 现场契约与 Codex 独立评审循环",
}
CODEX_NATIVE_NOTE_ZH = {
    "local codex personal asset system genericization and launchagent runtime": "个人资产系统的分层设计，覆盖通用化、外部 state root 和 LaunchAgent 运行边界。",
    "codex local configuration mcp setup token usage and plugin marketplace inspection": "本机 Codex 环境的 MCP 配置、Token 使用证据和插件市场排查方法。",
    "subreview run live contract and independent codex review loop": "Codex 独立评审循环的现场契约，包含 /subreview:run、临时 git snapshot 和复核路径。",
}
CODEX_NATIVE_TASK_BODY_ZH = {
    "local codex personal asset system genericization and launchagent runtime": "用户级个人资产系统，覆盖 dashboard、本地记忆运行时、state root 和 LaunchAgent 行为。",
}
CODEX_NATIVE_BULLET_ZH = {
    "when runtime behavior depends on the current device state or ui default to live inspection early": "当运行时行为依赖当前设备状态或 UI 时，优先尽早做现场检查。",
    "separate repo code tasks from user level codex ai personal assets tasks early the correct search surface is different": "先区分 repo 代码任务和用户级 Codex / OpenRelix 任务，两者搜索面不同。",
}


def codex_native_translation_key(title):
    return normalize_context_match_text(title)


def build_codex_native_display_body(title, body, language=None):
    body = normalize_brand_display_text(body)
    if is_english(language):
        return body

    key = codex_native_translation_key(title)
    if key in CODEX_NATIVE_TASK_BODY_ZH:
        return CODEX_NATIVE_TASK_BODY_ZH[key]

    bullet_key = codex_native_translation_key(body)
    if bullet_key in CODEX_NATIVE_BULLET_ZH:
        return CODEX_NATIVE_BULLET_ZH[bullet_key]
    bullet_tokens = set(bullet_key.split())
    if bullet_tokens:
        for candidate_key, translated in CODEX_NATIVE_BULLET_ZH.items():
            candidate_tokens = set(candidate_key.split())
            if not candidate_tokens:
                continue
            body_coverage = len(bullet_tokens & candidate_tokens) / len(bullet_tokens)
            candidate_coverage = len(bullet_tokens & candidate_tokens) / len(candidate_tokens)
            if body_coverage >= 0.86 and candidate_coverage >= 0.72:
                return translated

    return normalize_brand_display_text(body)


def build_codex_native_display_title(title, language=None):
    title = normalize_brand_display_text(title)
    if is_english(language):
        return title
    return normalize_brand_display_text(CODEX_NATIVE_TITLE_ZH.get(codex_native_translation_key(title), title))


def build_codex_native_display_note(
    title,
    keyword_blob="",
    desc="",
    learnings="",
    detail_heading="",
    language=None,
):
    keyword_blob = normalize_brand_display_text(keyword_blob)
    desc = normalize_brand_display_text(desc)
    learnings = normalize_brand_display_text(learnings)
    detail_heading = normalize_brand_display_text(detail_heading)
    if is_english(language):
        note_parts = []
        if desc:
            note_parts.append("Summary: {}".format(compact_preview_text(desc, limit=140)))
        if learnings:
            note_parts.append("Lessons: {}".format(compact_preview_text(learnings, limit=140)))
        if keyword_blob:
            note_parts.append("Keywords: {}".format(keyword_blob))
        if detail_heading:
            note_parts.append("Group: {}".format(detail_heading))
        return normalize_brand_display_text("; ".join(part for part in note_parts if part) or "Native memory summary")

    key = codex_native_translation_key(title)
    if key in CODEX_NATIVE_NOTE_ZH:
        note = CODEX_NATIVE_NOTE_ZH[key]
        if keyword_blob:
            note = "{} 关键词：{}。".format(note.rstrip("。"), keyword_blob)
        if detail_heading:
            note = "{} 分组：{}。".format(note.rstrip("。"), detail_heading)
        return normalize_brand_display_text(note)

    note_parts = []
    if desc:
        note_parts.append("摘要：{}".format(compact_preview_text(desc, limit=140)))
    if learnings:
        note_parts.append("经验：{}".format(compact_preview_text(learnings, limit=140)))
    if keyword_blob:
        note_parts.append("关键词：{}".format(keyword_blob))
    if detail_heading:
        note_parts.append("分组：{}".format(detail_heading))
    return normalize_brand_display_text("；".join(part for part in note_parts if part) or "原生记忆摘要")


def empty_codex_native_memory_summary(source_exists=False, source_readable=False, source_error=""):
    return {
        "rows": [],
        "preference_rows": [],
        "tip_rows": [],
        "counts": {
            "topic_items": 0,
            "user_preferences": 0,
            "general_tips": 0,
            "source_exists": source_exists,
            "source_readable": source_readable,
            "source_error": source_error,
        },
    }


def parse_codex_native_memory_summary(
    memory_summary_path,
    memory_index_path=None,
    known_project_names=None,
    language=None,
):
    language = current_language(language)
    summary_path = Path(memory_summary_path)
    try:
        text = summary_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_codex_native_memory_summary()
    except (OSError, UnicodeDecodeError) as exc:
        return empty_codex_native_memory_summary(
            source_exists=True,
            source_readable=False,
            source_error=exc.__class__.__name__,
        )

    counts = {
        "topic_items": 0,
        "user_preferences": 0,
        "general_tips": 0,
        "source_exists": True,
        "source_readable": True,
        "source_error": "",
    }

    rows = []
    preference_rows = []
    tip_rows = []
    current_h2 = ""
    current_h3 = ""
    current_h4 = ""
    current_date = ""
    current_item = None

    def make_summary_bullet_row(kind, index, body):
        section_label = "User preferences" if kind == "preference" else "General Tips"
        section_label_zh = "用户偏好" if kind == "preference" else "通用 tips"
        display_kind = localized(
            "偏好" if kind == "preference" else "通用 tips",
            "Preference" if kind == "preference" else "General tip",
            language,
        )
        display_body = build_codex_native_display_body("", body, language=language)
        display_body_en = normalize_brand_display_text(body)
        return {
            "kind": kind,
            "display_kind": display_kind,
            "title": localized(
                "{} {}".format(display_kind, index),
                "{} {}".format(display_kind, index),
                language,
            ),
            "display_title": localized(
                "{} {}".format(display_kind, index),
                "{} {}".format(display_kind, index),
                language,
            ),
            "body": compact_preview_text(normalize_brand_display_text(body), limit=220),
            "display_body": compact_preview_text(display_body, limit=220),
            "display_body_en": compact_preview_text(display_body_en, limit=220),
            "meta": localized(
                "Codex 原生 · {}".format(section_label_zh),
                "Codex Native · {}".format(section_label),
                language,
            ),
            "source_files": [
                {
                    "path": str(summary_path),
                    "label": "memory_summary.md",
                }
            ],
        }

    def flush_current_item():
        nonlocal current_item
        if not current_item:
            return

        title_line = current_item.get("title_line", "")
        title, _, keyword_blob = title_line.partition(": ")
        title = title.strip() or title_line
        keyword_blob = keyword_blob.strip()
        desc = current_item.get("desc", "").strip()
        learnings = current_item.get("learnings", "").strip()

        note_parts = []
        note_parts_en = []
        if desc:
            note_parts.append(desc)
            note_parts_en.append(desc)
        if learnings:
            note_parts.append(learnings)
            note_parts_en.append(learnings)
        if keyword_blob:
            note_parts.append("关键词: {}".format(keyword_blob))
            note_parts_en.append("Keywords: {}".format(keyword_blob))
        if current_item.get("detail_heading"):
            note_parts.append("分组: {}".format(current_item["detail_heading"]))
            note_parts_en.append("Group: {}".format(current_item["detail_heading"]))

        text_sources = [
            current_item.get("section_heading", ""),
            current_item.get("detail_heading", ""),
            title,
            keyword_blob,
            desc,
            learnings,
        ]
        context_labels = collect_context_labels_from_texts(text_sources, known_project_names)
        cwd_refs = [
            {
                "cwd": path,
                "cwd_display": compact_cwd_display(path),
            }
            for path in extract_resolved_local_paths(" ".join(text_sources), prefer_parent=True)
        ]
        value_note = normalize_brand_display_text("；".join(part for part in note_parts if part))
        value_note_en = normalize_brand_display_text("; ".join(part for part in note_parts_en if part))
        if not value_note:
            value_note = current_item.get("section_heading", "") or "原生记忆摘要"
            value_note_en = current_item.get("section_heading", "") or "Native memory summary"
            value_note = normalize_brand_display_text(value_note)
            value_note_en = normalize_brand_display_text(value_note_en)
        display_title = build_codex_native_display_title(title, language=language)
        display_value_note = build_codex_native_display_note(
            title,
            keyword_blob=keyword_blob,
            desc=desc,
            learnings=learnings,
            detail_heading=current_item.get("detail_heading", ""),
            language=language,
        )

        rows.append(
            {
                "memory_key": "native::{}::{}".format(
                    classify_codex_native_memory_type(title, desc, learnings),
                    normalize_memory_signature_text(
                        "{} {} {} {} {}".format(
                            context_labels[0] if context_labels else "",
                            current_item.get("date", ""),
                            current_item.get("detail_heading", ""),
                            current_item.get("line_number", ""),
                            title_line,
                        )
                    )
                    or "untitled",
                ),
                "bucket": "native",
                "display_bucket": localized("Codex 原生", "Codex Native", language),
                "memory_type": classify_codex_native_memory_type(title, desc, learnings),
                "display_memory_type": display_memory_type(
                    classify_codex_native_memory_type(title, desc, learnings),
                    language=language,
                ),
                "priority": current_item.get("priority", "medium"),
                "display_priority": display_memory_priority(
                    current_item.get("priority", "medium"),
                    language=language,
                ),
                "title": compact_preview_text(normalize_brand_display_text(title), limit=140),
                "display_title": compact_preview_text(display_title, limit=140),
                "value_note": value_note,
                "value_note_en": value_note_en,
                "display_value_note": display_value_note,
                "display_value_note_en": normalize_brand_display_text(value_note_en),
                "created_at": current_item.get("date", ""),
                "updated_at": current_item.get("date", ""),
                "created_at_display": display_memory_date(current_item.get("date", "")),
                "updated_at_display": display_memory_date(current_item.get("date", "")),
                "occurrence_count": 1,
                "occurrence_label": localized("原生归档", "Native archive", language),
                "display_context": context_labels[0] if context_labels else localized(
                    "未分类上下文",
                    "Uncategorized context",
                    language,
                ),
                "context_labels": context_labels[:3],
                "source_windows": cwd_refs[:3],
                "source_window_count": len(cwd_refs),
                "source_fact_label": localized("来源文件", "Source file", language),
                "source_files": [
                    {
                        "path": str(summary_path),
                        "label": "memory_summary.md",
                    }
                ],
            }
        )
        counts["topic_items"] += 1
        current_item = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        stripped = line.strip()
        if line.startswith("## "):
            flush_current_item()
            current_h2 = line[3:].strip()
            current_h3 = ""
            current_h4 = ""
            current_date = ""
            continue

        if current_h2 == "User preferences" and line.startswith("- "):
            counts["user_preferences"] += 1
            preference_rows.append(
                make_summary_bullet_row(
                    "preference",
                    counts["user_preferences"],
                    stripped[2:].strip(),
                )
            )
            continue
        if current_h2 == "General Tips" and line.startswith("- "):
            counts["general_tips"] += 1
            tip_rows.append(
                make_summary_bullet_row(
                    "tip",
                    counts["general_tips"],
                    stripped[2:].strip(),
                )
            )
            continue
        if current_h2 != "What's in Memory":
            continue

        if line.startswith("### "):
            flush_current_item()
            current_h3 = line[4:].strip()
            current_h4 = ""
            current_date = ""
            continue

        if line.startswith("#### "):
            flush_current_item()
            current_h4 = line[5:].strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", current_h4):
                current_date = current_h4
            else:
                current_date = ""
            continue

        if stripped.startswith("- "):
            bullet_body = stripped[2:].strip()
            if bullet_body.startswith("desc:") and current_item:
                current_item["desc"] = bullet_body.partition(":")[2].strip()
                continue
            if bullet_body.startswith("learnings:") and current_item:
                current_item["learnings"] = bullet_body.partition(":")[2].strip()
                continue
            if not line.startswith("- "):
                if current_item:
                    if current_item.get("learnings"):
                        current_item["learnings"] = "{} {}".format(
                            current_item["learnings"],
                            bullet_body,
                        ).strip()
                    else:
                        current_item["desc"] = "{} {}".format(
                            current_item.get("desc", ""),
                            bullet_body,
                        ).strip()
                continue

            flush_current_item()
            detail_heading = ""
            if current_h4 and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", current_h4):
                detail_heading = current_h4
            priority = "low" if current_h3 == "Older Memory Topics" else "medium"
            current_item = {
                "section_heading": current_h3,
                "detail_heading": detail_heading,
                "date": current_date,
                "priority": priority,
                "title_line": bullet_body,
                "line_number": line_number,
                "desc": "",
                "learnings": "",
            }
            continue

        if current_item and stripped:
            if current_item.get("learnings"):
                current_item["learnings"] = "{} {}".format(current_item["learnings"], stripped).strip()
            elif current_item.get("desc"):
                current_item["desc"] = "{} {}".format(current_item["desc"], stripped).strip()

    flush_current_item()

    rows.sort(
        key=lambda item: (
            memory_sort_key(item.get("updated_at", "")),
            item.get("title", ""),
        ),
        reverse=True,
    )
    return {
        "rows": rows,
        "preference_rows": preference_rows,
        "tip_rows": tip_rows,
        "counts": counts,
    }


def load_codex_memory_index_stats(memory_index_path, language=None):
    language = current_language(language)
    path = Path(memory_index_path)
    stats = {
        "task_group_count": 0,
        "rollout_reference_count": 0,
        "task_groups": [],
        "source_exists": False,
        "source_readable": False,
        "source_error": "",
    }
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return stats
    except (OSError, UnicodeDecodeError) as exc:
        stats["source_exists"] = True
        stats["source_error"] = exc.__class__.__name__
        return stats

    stats["source_exists"] = True
    task_group_count = 0
    rollout_reference_count = 0
    task_groups = []
    current_group = None
    current_section = ""

    def flush_current_group():
        nonlocal current_group
        if not current_group:
            return

        keywords = [normalize_brand_display_text(keyword) for keyword in current_group.get("keywords", [])]
        meta_parts = [
            "{} 个任务".format(current_group.get("task_count", 0)),
            "{} 个来源".format(current_group.get("rollout_reference_count", 0)),
        ]
        if keywords:
            meta_parts.append("关键词 {}".format("、".join(keywords[:3])))
        body = normalize_brand_display_text(current_group.get("scope", "") or current_group.get("applies_to", ""))
        body_en = body
        if not body:
            body = "MEMORY.md 中登记的历史任务组。"
            body_en = "Historical task group registered in MEMORY.md."
        display_title = build_codex_native_display_title(current_group.get("title", ""), language=language)
        display_body = build_codex_native_display_body(
            current_group.get("title", ""),
            body,
            language=language,
        )
        task_groups.append(
            {
                "title": compact_preview_text(normalize_brand_display_text(current_group.get("title", "")), limit=120),
                "display_title": compact_preview_text(display_title, limit=120),
                "body": compact_preview_text(body, limit=220),
                "display_body": compact_preview_text(display_body, limit=220),
                "display_body_en": compact_preview_text(normalize_brand_display_text(body_en), limit=220),
                "meta": normalize_brand_display_text("；".join(meta_parts)),
                "keywords": keywords[:5],
                "task_count": current_group.get("task_count", 0),
                "rollout_reference_count": current_group.get("rollout_reference_count", 0),
                "source_files": [
                    {
                        "path": str(path),
                        "label": "MEMORY.md",
                    }
                ],
            }
        )
        current_group = None

    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("# Task Group:"):
            flush_current_group()
            task_group_count += 1
            current_group = {
                "title": line.partition(":")[2].strip(),
                "scope": "",
                "applies_to": "",
                "task_count": 0,
                "rollout_reference_count": 0,
                "keywords": [],
            }
            current_section = ""
            continue
        if current_group:
            if line.startswith("scope:"):
                current_group["scope"] = line.partition(":")[2].strip()
                continue
            if line.startswith("applies_to:"):
                current_group["applies_to"] = line.partition(":")[2].strip()
                continue
            if line.startswith("## Task "):
                current_group["task_count"] += 1
                current_section = ""
                continue
            if line.startswith("### "):
                current_section = line[4:].strip().lower()
                continue
            if current_section == "keywords" and line.startswith("- "):
                keywords = [
                    item.strip()
                    for item in line[2:].split(",")
                    if item.strip()
                ]
                for keyword in keywords:
                    if keyword not in current_group["keywords"]:
                        current_group["keywords"].append(keyword)
        if "rollout_summaries/" in line:
            rollout_reference_count += 1
            if current_group:
                current_group["rollout_reference_count"] += 1
    flush_current_group()
    stats["task_group_count"] = task_group_count
    stats["rollout_reference_count"] = rollout_reference_count
    stats["task_groups"] = task_groups
    stats["source_readable"] = True
    return stats


def build_codex_native_memory_comparison(
    native_rows,
    nightly_rows,
    native_counts,
    index_stats,
    summary_path_label="Codex 原生记忆摘要文件",
    index_path_label="Codex 原生记忆索引文件",
    language=None,
):
    language = current_language(language)
    native_context_counter = Counter()
    nightly_context_counter = Counter()

    def comparison_context_labels(item):
        labels = [
            label
            for label in item.get("context_labels", [])
            if label and label not in {"未分类上下文", "Uncategorized context"}
        ]
        if labels:
            return labels
        fallback = item.get("display_context", "")
        if fallback and fallback not in {"未分类上下文", "Uncategorized context"}:
            return [fallback]
        return []

    for item in native_rows:
        labels = comparison_context_labels(item)
        for label in labels:
            native_context_counter[label] += 1

    for item in nightly_rows:
        labels = comparison_context_labels(item)
        for label in labels:
            nightly_context_counter[label] += 1

    shared_contexts = sorted(
        (
            {
                "label": label,
                "native_count": native_context_counter[label],
                "nightly_count": nightly_context_counter[label],
            }
            for label in native_context_counter
            if label in nightly_context_counter
        ),
        key=lambda item: (
            -(item["native_count"] + item["nightly_count"]),
            item["label"],
        ),
    )
    shared_labels = [item["label"] for item in shared_contexts[:3]]

    source_exists = native_counts.get("source_exists", bool(native_rows))
    source_readable = native_counts.get("source_readable", source_exists)
    source_error = native_counts.get("source_error", "")
    index_source_error = index_stats.get("source_error", "")
    index_unreadable = (
        index_source_error and not index_stats.get("source_readable", False)
    ) or (index_stats.get("source_exists") and not index_stats.get("source_readable", True))
    index_missing = "source_exists" in index_stats and not index_stats.get("source_exists") and not index_source_error
    index_unreadable_note = ""
    if index_unreadable:
        index_unreadable_note = localized(
            "{} 无法读取".format(index_path_label),
            "{} is unreadable".format(index_path_label),
            language,
        )
        if index_source_error:
            index_unreadable_note = "{}（{}）".format(index_unreadable_note, index_source_error)
        index_unreadable_note = localized(
            "{}，任务组统计暂不可用".format(index_unreadable_note),
            "{}; task group stats are unavailable".format(index_unreadable_note),
            language,
        )
    elif index_missing:
        index_unreadable_note = localized(
            "{} 未检测到，任务组统计暂不可用".format(index_path_label),
            "{} was not found; task group stats are unavailable".format(index_path_label),
            language,
        )
    if source_error and not source_readable:
        note_parts = [
            localized(
                "无法读取 {}（{}），当前仍以 nightly 整理结果为主".format(
                    summary_path_label,
                    source_error,
                ),
                "Unable to read {} ({}); the view is still based on nightly synthesis".format(
                    summary_path_label,
                    source_error,
                ),
                language,
            )
        ]
        if index_unreadable_note:
            note_parts.append(index_unreadable_note)
        note = ("; ".join(note_parts) + ".") if is_english(language) else ("；".join(note_parts) + "。")
    elif not source_exists:
        note_parts = [
            localized(
                "未检测到 {}".format(summary_path_label),
                "{} was not found".format(summary_path_label),
                language,
            )
        ]
        if index_unreadable_note:
            note_parts.append(index_unreadable_note)
        note = ("; ".join(note_parts) + ".") if is_english(language) else ("；".join(note_parts) + "。")
    elif not source_readable:
        note = localized(
            "已检测到但无法读取 {}，当前仍以 nightly 整理结果为主。".format(summary_path_label),
            "{} exists but is unreadable; the view is still based on nightly synthesis.".format(summary_path_label),
            language,
        )
    elif not native_rows:
        note_parts = [
            localized("已读取 {}".format(summary_path_label), "Read {}".format(summary_path_label), language),
            localized("暂无 What's in Memory 主题项", "No What's in Memory topic items", language),
            localized(
                "偏好 {} 条".format(native_counts.get("user_preferences", 0)),
                "{} preferences".format(native_counts.get("user_preferences", 0)),
                language,
            ),
            localized(
                "通用 tips {} 条".format(native_counts.get("general_tips", 0)),
                "{} general tips".format(native_counts.get("general_tips", 0)),
                language,
            ),
        ]
        if index_unreadable_note:
            note_parts.append(index_unreadable_note)
        note = ("; ".join(note_parts) + ".") if is_english(language) else ("；".join(note_parts) + "。")
    else:
        note_parts = [
            localized(
                "下方展示主题项 {} 条".format(len(native_rows)),
                "Showing {} topic items below".format(len(native_rows)),
                language,
            ),
            localized(
                "偏好 {} 条".format(native_counts.get("user_preferences", 0)),
                "{} preferences".format(native_counts.get("user_preferences", 0)),
                language,
            ),
            localized(
                "通用 tips {} 条".format(native_counts.get("general_tips", 0)),
                "{} general tips".format(native_counts.get("general_tips", 0)),
                language,
            ),
        ]
        if index_stats.get("task_group_count"):
            note_parts.append(
                localized(
                    "{} 个任务组".format(index_stats["task_group_count"]),
                    "{} task groups".format(index_stats["task_group_count"]),
                    language,
                )
            )
        elif index_unreadable_note:
            note_parts.append(index_unreadable_note)
        note_parts.append(
            localized(
                "偏好、tips、任务组以简短列表展示",
                "preferences, tips, and task groups are shown as compact lists",
                language,
            )
        )
        if shared_labels:
            shared_labels_en = [
                localized_context_label(label, language="en") for label in shared_labels
            ]
            note_parts.append(
                localized(
                    "共享上下文 {}".format("、".join(shared_labels)),
                    "shared contexts {}".format(", ".join(shared_labels_en)),
                    language,
                )
            )
        note_parts.append(
            localized(
                "原生偏长期规则，nightly 偏近期整理",
                "native memory leans toward long-term rules; nightly memory leans toward recent synthesis",
                language,
            )
        )
        note = ("; ".join(note_parts) + ".") if is_english(language) else ("；".join(note_parts) + "。")

    return {
        "note": note,
        "shared_contexts": shared_contexts,
        "shared_context_count": len(shared_contexts),
        "native_context_count": len(native_context_counter),
        "nightly_context_count": len(nightly_context_counter),
    }


def build_codex_native_memory_highlight(native_counts, native_comparison, summary_path_label, language=None):
    language = current_language(language)
    note = (native_comparison or {}).get("note", "")
    note = note.rstrip(".") if is_english(language) else note.rstrip("。")
    if native_counts.get("source_error") and not native_counts.get("source_readable", False):
        return localized(
            "Codex 原生记忆摘要暂不可用：{}。".format(note),
            "Codex native memory summary is unavailable: {}.".format(note),
            language,
        )
    if native_counts.get("source_exists"):
        if not native_counts.get("source_readable", True):
            return localized(
                "Codex 原生记忆摘要暂不可用：{}。".format(note),
                "Codex native memory summary is unavailable: {}.".format(note),
                language,
            )
        if native_counts.get("topic_items", 0) > 0:
            return localized(
                "Codex 原生记忆已接入可视化：{}。".format(note),
                "Codex native memory is visible in the panel: {}.".format(note),
                language,
            )
        return localized(
            "Codex 原生记忆摘要已读取：{}。".format(note),
            "Codex native memory summary was read: {}.".format(note),
            language,
        )
    if note:
        return localized(
            "Codex 原生记忆摘要暂不可用：{}。".format(note),
            "Codex native memory summary is unavailable: {}.".format(note),
            language,
        )
    return localized(
        "尚未读到 {}，当前仍以 nightly 整理结果为主。".format(summary_path_label),
        "{} has not been read yet; the view is still based on nightly synthesis.".format(summary_path_label),
        language,
    )


def markdown_table_cell(value, limit=None):
    text = compact_preview_text(value, limit=limit or 240)
    text = text.replace("\r", " ").replace("\n", " ").replace("|", "/")
    return escape(text, quote=False)


def markdown_inline_text(value, limit=1000):
    text = compact_preview_text(value, limit=limit)
    return escape(text, quote=False)


def enrich_nightly_memory_items(
    items,
    bucket,
    memory_registry,
    window_overview,
    default_date="",
    usage_window_overview=None,
):
    window_lookup = build_window_lookup(window_overview)
    known_project_names = collect_known_project_names(window_overview)
    registry_by_key = (memory_registry or {}).get("by_key", {})
    rows = []

    for item in items:
        memory_key = build_memory_group_key(item, bucket=bucket)
        current = dict(item)
        current["memory_key"] = memory_key
        current["bucket"] = bucket
        current["title"] = normalize_brand_display_text(current.get("title", ""))
        current["title_zh"] = normalize_brand_display_text(current.get("title_zh", ""))
        current["title_en"] = normalize_brand_display_text(current.get("title_en", ""))
        current["value_note"] = normalize_brand_display_text(current.get("value_note", ""))
        current["value_note_zh"] = normalize_brand_display_text(current.get("value_note_zh", ""))
        current["value_note_en"] = normalize_brand_display_text(current.get("value_note_en", ""))
        current["display_title"] = localized_record_field(
            current,
            "title",
            default=current.get("title", ""),
        )
        current["display_title_en"] = localized_record_field(
            current,
            "title",
            language="en",
            default=current.get("title", ""),
        )
        current["display_value_note"] = localized_record_field(
            current,
            "value_note",
            default=current.get("value_note", ""),
        )
        current["display_value_note_en"] = localized_record_field(
            current,
            "value_note",
            language="en",
            default=current.get("value_note", ""),
        )
        current["display_bucket"] = display_memory_bucket(bucket)
        current["display_memory_type"] = display_memory_type(item.get("memory_type", ""))
        current["display_priority"] = display_memory_priority(item.get("priority", ""))

        registry_row = registry_by_key.get(memory_key)
        if registry_row:
            current["created_at"] = registry_row.get("created_at", "")
            current["updated_at"] = registry_row.get("updated_at", "")
            current["created_at_display"] = registry_row.get("created_at_display", "时间未知")
            current["updated_at_display"] = registry_row.get("updated_at_display", "时间未知")
            current["occurrence_count"] = registry_row.get("occurrence_count", 1)
            current["display_context"] = registry_row.get("display_context", "未分类上下文")
            current["context_labels"] = registry_row.get("context_labels", [])
            current["cwd_preview"] = registry_row.get("cwd_preview", "")
            current["source_windows"] = registry_row.get("source_windows", [])
            current["source_window_count"] = registry_row.get("source_window_count", 0)
            for key in (
                "usage_frequency",
                "usage_frequency_display",
                "usage_frequency_window_days",
                "usage_frequency_direct_window_count",
                "usage_frequency_estimated_window_count",
                "usage_frequency_context_hint_count",
                "usage_frequency_matched_window_count",
                "usage_frequency_terms",
                "usage_frequency_sort_key",
            ):
                current[key] = registry_row.get(key, 0 if key.endswith("_count") else registry_row.get(key, ""))
            rows.append(current)
            continue

        source_windows = []
        for window_id in item.get("source_window_ids", []):
            ref = build_memory_source_window_ref(
                default_date,
                window_id,
                window_lookup,
                known_project_names,
            )
            if ref:
                source_windows.append(ref)

        context_labels = []
        for ref in source_windows:
            label = ref.get("project_label", "")
            if label and label not in context_labels:
                context_labels.append(label)
        if not context_labels:
            inferred = infer_context_label_from_text(
                " ".join(
                    (
                        current.get("title", ""),
                        current.get("value_note", ""),
                        " ".join(current.get("keywords", [])),
                    )
                ),
                known_project_names,
            )
            if inferred:
                context_labels.append(inferred)

        current["created_at"] = default_date
        current["updated_at"] = default_date
        current["created_at_display"] = display_memory_date(default_date)
        current["updated_at_display"] = display_memory_date(default_date)
        current["occurrence_count"] = 1
        current["display_context"] = context_labels[0] if context_labels else "未分类上下文"
        current["context_labels"] = context_labels[:3]
        current["cwd_preview"] = " / ".join(
            [ref.get("cwd_display", "") for ref in source_windows if ref.get("cwd_display", "")][:2]
        ) or current["display_context"]
        current["source_windows"] = source_windows[:3]
        current["source_window_count"] = len(source_windows)
        usage_current = dict(current)
        usage_current["source_windows"] = source_windows
        current.update(
            build_memory_usage_frequency(
                usage_current,
                usage_window_overview,
                recent_occurrence_dates=[default_date] if default_date else [],
            )
        )
        rows.append(current)

    return sort_memory_rows_by_usage(rows)


def build_memory_bucket_view(
    bucket,
    memory_registry,
    memory_view_nightly,
    window_overview,
    memory_view_date,
    usage_window_overview=None,
):
    registry_rows = [
        row
        for row in (memory_registry or {}).get("rows", [])
        if row.get("bucket") == bucket
    ]
    if registry_rows:
        return sort_memory_rows_by_usage(registry_rows)

    summary_key = {
        "durable": "durable_memories",
        "session": "session_memories",
        "low_priority": "low_priority_memories",
    }.get(bucket, "{}_memories".format(bucket))
    return enrich_nightly_memory_items(
        (memory_view_nightly or {}).get(summary_key, []),
        bucket,
        memory_registry,
        window_overview,
        default_date=memory_view_date,
        usage_window_overview=usage_window_overview,
    )


def normalize_window_activity_source(raw_window=None, daily_capture=None):
    raw_window = raw_window or {}
    daily_capture = daily_capture or {}
    source = str(raw_window.get("source") or "").strip()
    collection_source = str(daily_capture.get("collection_source") or "").strip()
    if raw_window.get("app_server") or source.startswith("codex_app_server") or collection_source == "app-server":
        return "app-server"
    if collection_source == "history_fallback":
        return "history_fallback"
    if source in {"cli", "history"} or collection_source == "history":
        return "history"
    if collection_source:
        return collection_source
    return source or "history"


def window_activity_source_label(activity_source, language=None, thread_source=""):
    thread_source = str(thread_source or "").strip()
    if activity_source == "app-server" and thread_source:
        return localized(
            "采集：Codex app-server（预览） · 线程来源：{}".format(thread_source),
            "Collection: Codex app-server (preview) · thread source: {}".format(thread_source),
            language,
        )
    labels = {
        "app-server": (
            "采集：Codex app-server（预览）",
            "Collection: Codex app-server (preview)",
        ),
        "history_fallback": (
            "采集：Codex app-server 不可用，已回退 CLI history/session",
            "Collection: Codex app-server unavailable; fell back to CLI history/session",
        ),
        "history": (
            "采集：Codex CLI history/session",
            "Collection: Codex CLI history/session",
        ),
        "nightly_summary": (
            "采集：整理摘要",
            "Collection: synthesis summary",
        ),
    }
    zh_text, en_text = labels.get(
        activity_source,
        ("采集：Codex 活动记录", "Collection: Codex activity records"),
    )
    return localized(zh_text, en_text, language)


def build_window_items_from_daily_capture(daily_capture, latest_nightly=None, language=None):
    language = current_language(language)
    nightly_map = {}
    if latest_nightly:
        for item in latest_nightly.get("window_summaries", []):
            window_id = item.get("window_id", "")
            if window_id:
                nightly_map[window_id] = item

    items = []
    for raw_window in (daily_capture or {}).get("windows", []):
        window_id = raw_window.get("window_id", "")
        nightly_item = nightly_map.get(window_id, {})
        latest_activity = latest_window_activity(raw_window)
        prompts = raw_window.get("prompts", [])
        conclusions = raw_window.get("conclusions", [])
        first_prompt = prompts[0] if prompts else {}
        last_conclusion = conclusions[-1] if conclusions else {}
        question_summary = nightly_item.get("question_summary") or compact_preview_text(
            first_prompt.get("text", ""),
            limit=180,
        )
        main_takeaway = nightly_item.get("main_takeaway") or compact_preview_text(
            last_conclusion.get("text", "") or first_prompt.get("text", ""),
            limit=200,
        )
        question_summary = normalize_brand_display_text(question_summary)
        main_takeaway = normalize_brand_display_text(main_takeaway)
        cwd = raw_window.get("cwd", "")
        project_label = infer_repo_name_from_path(cwd)
        if not project_label:
            project_label = infer_context_label_from_text(
                " ".join(filter(None, [question_summary, main_takeaway])),
                collect_known_project_names({"windows": items}) if items else [],
            )
        if not project_label:
            project_label = localized_context_label("个人工作区", language)
        project_label = normalize_brand_display_text(project_label)
        activity_source = normalize_window_activity_source(raw_window, daily_capture)
        thread_source = (raw_window.get("app_server") or {}).get("thread_source", "")
        items.append(
            {
                "date": (daily_capture or {}).get("date", ""),
                "window_id": window_id,
                "window_id_short": window_id[:8],
                "cwd": cwd,
                "cwd_display": compact_cwd_display(cwd),
                "project_label": project_label,
                "activity_source": activity_source,
                "thread_source": thread_source,
                "activity_source_label": window_activity_source_label(
                    activity_source,
                    language,
                    thread_source=thread_source,
                ),
                "question_count": raw_window.get("prompt_count", 0),
                "conclusion_count": raw_window.get("conclusion_count", 0),
                "question_summary": question_summary or localized("暂无问题摘要。", "No question summary.", language),
                "main_takeaway": main_takeaway or localized("暂无结论摘要。", "No conclusion summary.", language),
                "keywords": [normalize_brand_display_text(keyword) for keyword in nightly_item.get("keywords", [])],
                "latest_activity_at": latest_activity.isoformat() if latest_activity else "",
                "latest_activity_display": display_short_local_datetime(latest_activity) if latest_activity else localized("时间未知", "Unknown time", language),
                "started_at_display": display_short_local_datetime(raw_window.get("started_at", "")) or localized("时间未知", "Unknown time", language),
                "recent_prompts": make_window_preview_items(
                    prompts,
                    "local_time",
                    limit=3,
                    fallback=localized("暂无问题记录。", "No question records.", language),
                ),
                "recent_conclusions": make_window_preview_items(
                    conclusions,
                    "completed_at",
                    limit=2,
                    fallback=localized("暂无结论记录。", "No conclusion records.", language),
                ),
            }
        )
    return items


def build_window_overview(latest_nightly, language=None, target_date=""):
    language = current_language(language)
    target_date = target_date or (latest_nightly.get("date", "") if latest_nightly else "")
    daily_capture = load_daily_capture(target_date) if target_date else load_daily_capture()

    nightly_map = {}
    if latest_nightly:
        for item in latest_nightly.get("window_summaries", []):
            window_id = item.get("window_id", "")
            if window_id:
                nightly_map[window_id] = item

    if not daily_capture:
        fallback_items = []
        for item in (latest_nightly or {}).get("window_summaries", []):
            cwd = item.get("cwd", "")
            fallback_items.append(
                {
                    "window_id": item.get("window_id", ""),
                    "window_id_short": item.get("window_id", "")[:8],
                    "cwd": cwd,
                    "cwd_display": compact_cwd_display(cwd),
                    "project_label": normalize_brand_display_text(
                        infer_repo_name_from_path(cwd) or localized_context_label("个人工作区", language)
                    ),
                    "activity_source": "nightly_summary",
                    "activity_source_label": window_activity_source_label("nightly_summary", language),
                    "question_count": item.get("question_count", 0),
                    "conclusion_count": item.get("conclusion_count", 0),
                    "question_summary": normalize_brand_display_text(item.get("question_summary", "")),
                    "main_takeaway": normalize_brand_display_text(item.get("main_takeaway", "")),
                    "keywords": [normalize_brand_display_text(keyword) for keyword in item.get("keywords", [])],
                    "latest_activity_at": "",
                    "latest_activity_display": localized("时间未知", "Unknown time", language),
                    "started_at_display": localized("时间未知", "Unknown time", language),
                    "recent_prompts": [{"time": "", "text": localized("未找到原始问题记录。", "Raw question records were not found.", language)}],
                    "recent_conclusions": [{"time": "", "text": localized("未找到原始结论记录。", "Raw conclusion records were not found.", language)}],
                }
            )
        if not fallback_items:
            return None
        fallback_items.sort(
            key=lambda item: (
                item.get("question_count", 0),
                item.get("conclusion_count", 0),
                item.get("window_id", ""),
            ),
            reverse=True,
        )
        assign_window_display_indices(fallback_items)
        return {
            "date": target_date or "",
            "window_count": len(fallback_items),
            "excluded_window_count": 0,
            "review_like_window_count": (latest_nightly or {}).get("review_like_window_count", 0),
            "source_kind": "nightly_summary",
            "windows": fallback_items,
        }

    items = build_window_items_from_daily_capture(daily_capture, latest_nightly, language=language)

    items.sort(
        key=lambda item: (
            parse_iso_datetime(item.get("latest_activity_at", "")).timestamp()
            if item.get("latest_activity_at")
            else 0,
            item.get("question_count", 0),
            item.get("conclusion_count", 0),
            item.get("window_id", ""),
        ),
        reverse=True,
    )

    assign_window_display_indices(items)

    return {
        "date": daily_capture.get("date", target_date or ""),
        "window_count": daily_capture.get("window_count", len(items)),
        "excluded_window_count": daily_capture.get("excluded_window_count", 0),
        "review_like_window_count": daily_capture.get("review_like_window_count", 0),
        "source_kind": "daily_capture",
        "windows": items,
    }


def date_strings_ending_at(anchor_date, days):
    parsed = parse_nightly_summary_date({"date": anchor_date})
    if parsed is None:
        return []
    return [
        (parsed - timedelta(days=offset)).isoformat()
        for offset in range(max(days, 0))
    ]


def build_context_window_overview_for_days(anchor_date, days, latest_nightly=None, language=None):
    language = current_language(language)
    scanned_dates = date_strings_ending_at(anchor_date, days)
    windows = []
    source_dates = []
    excluded_window_count = 0
    review_like_window_count = 0

    for date_str in scanned_dates:
        daily_capture = load_daily_capture(date_str)
        if not daily_capture:
            continue
        capture_latest_nightly = latest_nightly if date_str == anchor_date else None
        date_windows = build_window_items_from_daily_capture(
            daily_capture,
            capture_latest_nightly,
            language=language,
        )
        if date_windows:
            source_dates.append(date_str)
            windows.extend(date_windows)
        excluded_window_count += daily_capture.get("excluded_window_count", 0)
        review_like_window_count += daily_capture.get("review_like_window_count", 0)

    windows.sort(
        key=lambda item: (
            parse_iso_datetime(item.get("latest_activity_at", "")).timestamp()
            if item.get("latest_activity_at")
            else 0,
            item.get("question_count", 0),
            item.get("conclusion_count", 0),
            item.get("window_id", ""),
        ),
        reverse=True,
    )
    assign_window_display_indices(windows)

    return {
        "date": anchor_date,
        "days": days,
        "scanned_date_count": len(scanned_dates),
        "source_date_count": len(source_dates),
        "source_dates": source_dates,
        "window_count": len(windows),
        "excluded_window_count": excluded_window_count,
        "review_like_window_count": review_like_window_count,
        "source_kind": "daily_capture_range",
        "windows": windows,
    }


def build_project_context_views(anchor_date, latest_nightly=None, max_days=PROJECT_CONTEXT_MAX_DAYS, language=None):
    language = current_language(language)
    views = {}
    for days in range(1, max_days + 1):
        window_overview = build_context_window_overview_for_days(
            anchor_date,
            days,
            latest_nightly=latest_nightly,
            language=language,
        )
        contexts = build_project_contexts(window_overview, language=language)
        views[str(days)] = {
            "days": days,
            "scanned_date_count": window_overview.get("scanned_date_count", days),
            "source_date_count": window_overview.get("source_date_count", 0),
            "source_dates": window_overview.get("source_dates", []),
            "window_count": window_overview.get("window_count", 0),
            "context_count": len(contexts),
            "project_contexts": contexts,
        }
    return views


def summarize_assets(assets):
    type_counter = Counter()
    domain_counter = Counter()
    monthly_counter = Counter()
    scope_counter = Counter()
    status_counter = Counter()
    active_assets = 0

    for asset in assets:
        type_counter[asset.get("type", "unknown")] += 1
        domain_counter[asset.get("domain", "unknown")] += 1
        scope_counter[asset.get("scope", "unknown")] += 1
        status_counter[asset.get("status", "unknown")] += 1
        created_at = asset.get("created_at", "")
        month = created_at[:7] if len(created_at) >= 7 else "unknown"
        monthly_counter[month] += 1
        if asset.get("status") == "active":
            active_assets += 1

    return {
        "type_counter": type_counter,
        "domain_counter": domain_counter,
        "monthly_counter": monthly_counter,
        "scope_counter": scope_counter,
        "status_counter": status_counter,
        "active_assets": active_assets,
    }


def summarize_usage(events):
    usage_by_asset = defaultdict(list)
    minutes_saved_total = 0
    for event in events:
        asset_id = event.get("asset_id", "unknown")
        usage_by_asset[asset_id].append(event)
        minutes_saved_total += safe_int(event.get("minutes_saved", 0))
    recent_events = sorted(
        events,
        key=lambda item: (item.get("date", ""), item.get("asset_id", ""), item.get("task", "")),
        reverse=True,
    )
    return usage_by_asset, minutes_saved_total, recent_events


def normalize_value_match_text(value):
    text = str(value or "").lower()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_value_match_text(value):
    return normalize_value_match_text(value).replace(" ", "")


def asset_value_search_terms(asset):
    raw_terms = [
        asset.get("id", ""),
        asset.get("display_title", ""),
        asset.get("title", ""),
        asset.get("display_source_task", ""),
        asset.get("source_task", ""),
        asset.get("domain", ""),
        asset.get("display_notes", ""),
        asset.get("notes", ""),
    ]
    raw_terms.extend(asset.get("tags", []) or [])
    for raw_path in asset.get("artifact_paths", []) or []:
        path = Path(str(raw_path or ""))
        raw_terms.extend([path.stem, path.name])

    terms = []
    for raw_term in raw_terms:
        normalized = normalize_value_match_text(raw_term)
        compact = normalized.replace(" ", "")
        if compact and len(compact) >= 6 and compact not in ASSET_VALUE_STOP_TERMS:
            terms.append(compact)
        for part in normalized.split():
            if part in ASSET_VALUE_STOP_TERMS:
                continue
            has_cjk = bool(re.search(r"[\u4e00-\u9fff]", part))
            min_length = 2 if has_cjk else 4
            if len(part) >= min_length:
                terms.append(part)

    deduped = []
    for term in terms:
        if term not in deduped:
            deduped.append(term)
    return deduped[:14]


def asset_matches_text(asset, text, terms=None):
    terms = terms if terms is not None else asset_value_search_terms(asset)
    if not terms:
        return False, []
    compact_text = compact_value_match_text(text)
    matched = [term for term in terms if term and term in compact_text]
    if not matched:
        return False, []

    strong_terms = {
        compact_value_match_text(asset.get("id", "")),
        compact_value_match_text(asset.get("display_title", "")),
        compact_value_match_text(asset.get("title", "")),
        compact_value_match_text(asset.get("display_source_task", "")),
        compact_value_match_text(asset.get("source_task", "")),
    }
    if any(term in strong_terms and len(term) >= 6 for term in matched):
        return True, matched
    if len(matched) >= 2:
        return True, matched
    return False, matched


def estimate_asset_minutes(asset_type, evidence_text="", confidence=1.0):
    base_minutes = ASSET_VALUE_BASE_MINUTES.get(asset_type, 8)
    text = str(evidence_text or "").lower()
    multiplier = 1.0
    for keywords, weight in ASSET_VALUE_COMPLEXITY_RULES:
        if any(keyword.lower() in text for keyword in keywords):
            multiplier = max(multiplier, weight)
    return max(2, int(round(base_minutes * multiplier * confidence)))


def estimate_asset_reuse_value(asset, tracked_events, window_overview, language=None):
    asset_type = asset.get("type", "")
    explicit_usage_count = len(tracked_events)
    explicit_minutes = 0
    inferred_event_minutes = 0
    signals = []

    for event in tracked_events:
        recorded_minutes = safe_int(event.get("minutes_saved", 0))
        event_text = " ".join(
            str(event.get(key, ""))
            for key in ("task", "note", "asset_id")
        )
        if recorded_minutes > 0:
            explicit_minutes += recorded_minutes
        else:
            inferred_event_minutes += estimate_asset_minutes(asset_type, event_text)

    if explicit_usage_count:
        signals.append(
            localized(
                "显式复用记录 {} 次".format(explicit_usage_count),
                "{} explicit reuse event{}".format(
                    explicit_usage_count,
                    "" if explicit_usage_count == 1 else "s",
                ),
                language,
            )
        )
    if inferred_event_minutes:
        signals.append(
            localized(
                "缺失分钟数的复用记录已按任务复杂度估算",
                "usage events without minutes were estimated by task complexity",
                language,
            )
        )

    search_terms = asset_value_search_terms(asset)
    implicit_matches = []
    for window in (window_overview or {}).get("windows", []):
        window_text = context_window_text(window)
        is_match, matched_terms = asset_matches_text(asset, window_text, search_terms)
        if not is_match:
            continue
        implicit_matches.append(
            {
                "window_id": window.get("window_id", ""),
                "matched_terms": matched_terms[:3],
                "text": window_text,
            }
        )

    implicit_minutes = sum(
        estimate_asset_minutes(asset_type, item.get("text", ""), confidence=0.38)
        for item in implicit_matches[:5]
    )
    if implicit_matches:
        signals.append(
            localized(
                "近期窗口命中 {} 次".format(len(implicit_matches)),
                "{} recent window match{}".format(
                    len(implicit_matches),
                    "" if len(implicit_matches) == 1 else "es",
                ),
                language,
            )
        )

    if asset_type in ASSET_VALUE_BASE_SCORE:
        signals.append(
            localized(
                "{} 类型有固定复用基准".format(display_label("type", asset_type, language="zh")),
                "{} carries a reusable baseline".format(
                    display_label("type", asset_type, language="en")
                ),
                language,
            )
        )

    updated_at = parse_iso_datetime(asset.get("updated_at", ""))
    recency_score = 0
    if updated_at:
        age_days = max((current_local_datetime() - updated_at).days, 0)
        if age_days <= 7:
            recency_score = 8
        elif age_days <= 30:
            recency_score = 4
    if recency_score:
        signals.append(localized("最近仍在维护", "recently maintained", language))

    estimated_minutes = explicit_minutes + inferred_event_minutes + implicit_minutes
    score = (
        ASSET_VALUE_BASE_SCORE.get(asset_type, 6)
        + explicit_usage_count * 24
        + len(implicit_matches[:5]) * 11
        + min(estimated_minutes, 120) * 0.45
        + recency_score
    )
    if asset.get("scope") in {"repo", "team"}:
        score += 3
    score = int(round(min(score, 100)))

    if score >= 70:
        level = localized("高价值", "High", language)
    elif score >= 42:
        level = localized("中价值", "Medium", language)
    else:
        level = localized("观察中", "Watch", language)

    if explicit_usage_count or implicit_matches:
        reason = localized(
            "按显式复用、近期窗口命中和任务复杂度自动估算。",
            "Estimated from explicit reuse, recent window matches, and task complexity.",
            language,
        )
    else:
        reason = localized(
            "暂无直接复用证据，当前主要按资产类型和维护活跃度估算潜在价值。",
            "No direct reuse evidence yet; current value is mainly based on asset type and maintenance recency.",
            language,
        )

    return {
        "estimated_value_score": score,
        "estimated_value_level": level,
        "estimated_minutes_saved": estimated_minutes,
        "estimated_minutes_saved_display": localized(
            "{} 分钟".format(estimated_minutes),
            "{} min".format(estimated_minutes),
            language,
        ),
        "value_evidence_count": explicit_usage_count + len(implicit_matches),
        "implicit_reuse_matches": len(implicit_matches),
        "explicit_usage_count": explicit_usage_count,
        "value_signals": signals[:5],
        "value_evidence_label": localized(
            "显式 {} / 窗口 {} / 估算 {}".format(
                explicit_usage_count,
                len(implicit_matches),
                "{} 分钟".format(estimated_minutes),
            ),
            "explicit {} / windows {} / estimated {}".format(
                explicit_usage_count,
                len(implicit_matches),
                "{} min".format(estimated_minutes),
            ),
            language,
        ),
        "value_reason": reason,
        "value_search_terms": search_terms,
    }


def enrich_assets(assets, usage_by_asset, known_project_names, window_overview=None, language=None):
    enriched = []
    for asset in assets:
        tracked_events = usage_by_asset.get(asset.get("id", ""), [])
        item = dict(asset)
        item["display_title"] = localized_record_field(
            asset,
            "title",
            language=language,
            default=asset.get("title", "") or asset.get("id", "") or "未命名资产",
        )
        item["display_title_en"] = localized_record_field(
            asset,
            "title",
            language="en",
            default=asset.get("title", "") or item["display_title"],
        )
        item["display_value_note"] = localized_record_field(
            asset,
            "value_note",
            language=language,
            default=asset.get("value_note", ""),
        )
        item["display_value_note_en"] = localized_record_field(
            asset,
            "value_note",
            language="en",
            default=asset.get("value_note", "") or item["display_value_note"],
        )
        item["display_source_task"] = localized_record_field(
            asset,
            "source_task",
            language=language,
            default=asset.get("source_task", ""),
        )
        item["display_source_task_en"] = localized_record_field(
            asset,
            "source_task",
            language="en",
            default=asset.get("source_task", "") or item["display_source_task"],
        )
        item["display_notes"] = localized_record_field(
            asset,
            "notes",
            language=language,
            default=asset.get("notes", ""),
        )
        item["display_notes_en"] = localized_record_field(
            asset,
            "notes",
            language="en",
            default=asset.get("notes", "") or item["display_notes"],
        )
        for key, field, fallback_label in (
            ("display_title_en", "title", "Asset"),
            ("display_value_note_en", "value_note", "Value note"),
            ("display_source_task_en", "source_task", "Task"),
            ("display_notes_en", "notes", "Notes"),
        ):
            if contains_cjk(item.get(key, "")):
                item[key] = english_record_text(asset, field, fallback_label=fallback_label)
        item["display_type"] = display_label("type", asset.get("type", ""), language=language)
        item["display_domain"] = display_label("domain", asset.get("domain", ""), language=language)
        item["display_scope"] = display_label("scope", asset.get("scope", ""), language=language)
        item["display_status"] = display_label("status", asset.get("status", ""), language=language)
        item["display_type_en"] = display_label("type", asset.get("type", ""), language="en")
        item["display_domain_en"] = display_label("domain", asset.get("domain", ""), language="en")
        item["display_scope_en"] = display_label("scope", asset.get("scope", ""), language="en")
        item["display_status_en"] = display_label("status", asset.get("status", ""), language="en")
        item["display_context"] = resolve_asset_context(asset, known_project_names)
        item["display_context_en"] = (
            panel_english_text(item["display_context"])
            or localized_context_label(item["display_context"], language="en")
            or item["display_domain_en"]
        )
        if contains_cjk(item["display_context_en"]):
            item["display_context_en"] = english_freeform_text(
                item["display_context"],
                fallback_label=item["display_domain_en"] or "Context",
            )
        item["manual_reuse_count"] = safe_int(asset.get("reuse_count", 0))
        item["tracked_usage_events"] = len(tracked_events)
        item["tracked_minutes_saved"] = sum(
            safe_int(event.get("minutes_saved", 0)) for event in tracked_events
        )
        item["minutes_saved_total"] = safe_int(asset.get("minutes_saved_total", 0))
        item["artifact_paths"] = asset.get("artifact_paths", [])
        item["tags"] = asset.get("tags", [])
        value_view = estimate_asset_reuse_value(
            item,
            tracked_events,
            window_overview,
            language=language,
        )
        value_view_en = estimate_asset_reuse_value(
            item,
            tracked_events,
            window_overview,
            language="en",
        )
        item.update(value_view)
        for key in (
            "estimated_value_level",
            "estimated_minutes_saved_display",
            "value_evidence_label",
            "value_reason",
        ):
            item["{}_en".format(key)] = value_view_en.get(key, value_view.get(key, ""))
        item["value_signals_en"] = value_view_en.get("value_signals", value_view.get("value_signals", []))
        enriched.append(item)
    return enriched


def enrich_usage_events(events, language=None):
    enriched = []
    for event in events:
        item = dict(event)
        item["display_task"] = localized_record_field(
            event,
            "task",
            language=language,
            default=event.get("task", ""),
        )
        item["display_task_en"] = localized_record_field(
            event,
            "task",
            language="en",
            default=event.get("task", "") or item["display_task"],
        )
        item["display_note"] = localized_record_field(
            event,
            "note",
            language=language,
            default=event.get("note", ""),
        )
        item["display_note_en"] = localized_record_field(
            event,
            "note",
            language="en",
            default=event.get("note", "") or item["display_note"],
        )
        if contains_cjk(item.get("display_task_en", "")):
            item["display_task_en"] = english_record_text(event, "task", fallback_label="Usage task")
        if contains_cjk(item.get("display_note_en", "")):
            item["display_note_en"] = english_record_text(event, "note", fallback_label="Usage note")
        enriched.append(item)
    return enriched


def build_asset_type_guide(assets):
    assets_by_type = defaultdict(list)
    for asset in assets:
        asset_type = asset.get("type", "")
        if not asset_type:
            continue
        assets_by_type[asset_type].append(asset)

    ordered_types = [
        asset_type for asset_type in ASSET_TYPE_GUIDE_ORDER if assets_by_type.get(asset_type)
    ]
    ordered_types.extend(
        asset_type
        for asset_type in sorted(assets_by_type)
        if asset_type not in ASSET_TYPE_GUIDE_ORDER
    )

    guide_rows = []
    for asset_type in ordered_types:
        guide_rows.append(
            {
                "label": display_label("type", asset_type),
                "label_en": display_label("type", asset_type, language="en"),
                "description": ASSET_TYPE_DESCRIPTIONS.get(
                    asset_type, "已登记到资产注册表中的稳定资产类型。"
                ),
                "description_en": ASSET_TYPE_DESCRIPTIONS_EN.get(
                    asset_type, "Stable asset type registered in the asset registry."
                ),
                "count": len(assets_by_type[asset_type]),
                "examples": [
                    asset.get("display_title") or asset.get("title", "")
                    for asset in assets_by_type[asset_type]
                    if asset.get("display_title") or asset.get("title", "")
                ][:2],
                "examples_en": [
                    asset.get("display_title_en") or asset.get("title", "")
                    for asset in assets_by_type[asset_type]
                    if asset.get("display_title_en") or asset.get("title", "")
                ][:2],
            }
        )
    return guide_rows


def make_asset_detail_item(asset):
    title = asset.get("display_title") or asset.get("title") or asset.get("id") or "未命名资产"
    title_en = asset.get("display_title_en") or asset.get("title_en") or ""
    if not title_en or contains_cjk(title_en):
        title_en = english_record_text(asset, "title", fallback_label="Asset")
    meta_parts = []
    meta_parts_en = []
    for value, en_value in (
        (
            asset.get("display_type") or display_label("type", asset.get("type", "")),
            asset.get("display_type_en") or display_label("type", asset.get("type", ""), language="en"),
        ),
        (
            asset.get("display_scope") or display_label("scope", asset.get("scope", "")),
            asset.get("display_scope_en") or display_label("scope", asset.get("scope", ""), language="en"),
        ),
        (
            asset.get("display_context") or asset.get("display_domain") or asset.get("domain", ""),
            asset.get("display_context_en")
            or asset.get("display_domain_en")
            or display_label("domain", asset.get("domain", ""), language="en"),
        ),
    ):
        if value and value not in meta_parts:
            meta_parts.append(value)
        if en_value and en_value not in meta_parts_en:
            meta_parts_en.append(en_value)
    return {
        "title": title,
        "title_en": title_en,
        "meta": " / ".join(meta_parts),
        "meta_en": english_freeform_text(
            " / ".join(meta_parts_en),
            fallback_label="Details",
        ),
    }


def build_asset_mix_rows(assets, key_fn, label_fn=None, label_en_fn=None):
    grouped_assets = defaultdict(list)
    label_fn = label_fn or (lambda value: value)
    label_en_fn = label_en_fn or (lambda value: panel_english_text(label_fn(value)) or label_fn(value))

    for asset in assets:
        key = key_fn(asset) or "unknown"
        grouped_assets[key].append(asset)

    rows = []
    for key, group_assets in grouped_assets.items():
        label = label_fn(key) or str(key or "unknown")
        label_en = label_en_fn(key) or panel_english_text(label) or label
        detail_assets = sorted(
            group_assets,
            key=lambda item: (item.get("title", ""), item.get("id", "")),
        )
        rows.append(
            {
                "label": label,
                "label_en": label_en,
                "value": len(group_assets),
                "details": [make_asset_detail_item(asset) for asset in detail_assets],
                "details_heading": "对应项目 / 条目",
                "details_heading_en": "Related projects / items",
            }
        )

    rows.sort(key=lambda item: (-item["value"], item["label"]))
    return rows


def make_table(counter, headers, empty_label="none"):
    rows = counter_to_rows(counter)
    if not rows:
        return "| {} |\n| {} |\n| {} |".format(
            " | ".join(headers),
            " | ".join(["---"] * len(headers)),
            " | ".join([empty_label] + ["0"] * (len(headers) - 1)),
        )

    lines = [
        "| {} |".format(" | ".join(headers)),
        "| {} |".format(" | ".join(["---"] * len(headers))),
    ]
    for row in rows:
        lines.append("| {} | {} |".format(row["label"], row["value"]))
    return "\n".join(lines)


def summary_term_view_for_days(data, days):
    for view in data.get("summary_term_views", []) or []:
        if safe_int(view.get("days", 0)) == safe_int(days):
            return view
    if safe_int(days) == SUMMARY_TERM_DEFAULT_DAYS:
        return {"terms": data.get("summary_terms", [])}
    return {}


def format_summary_term_labels(rows, separator, empty_label):
    labels = [str(row.get("label", "")).strip() for row in rows or [] if str(row.get("label", "")).strip()]
    return separator.join(labels) if labels else empty_label


def build_summary_term_markdown_lines(data, language=None):
    language = current_language(language or data.get("language"))
    if is_english(language):
        lines = [
            "## Today Hot Terms",
            "",
            "Note: the default panel view is today; the panel can switch to the last 3 or 7 days. Terms come from the selected date range's window synthesis, assets, reviews, and usage records.",
            "",
        ]
        for days in SUMMARY_TERM_RANGE_DAYS:
            view = summary_term_view_for_days(data, days)
            label = "Today" if days == 1 else "Last {}".format(plural_en(days, "day"))
            lines.append(
                "- {}: {}".format(
                    label,
                    format_summary_term_labels(view.get("terms", []), ", ", "None"),
                )
            )
        return lines

    lines = [
        "## 今日热词",
        "",
        "说明：默认展示今日；面板可切换近 3 日和近 7 日。热词来自对应日期范围内的窗口整理、资产、复盘和复用记录。",
        "",
    ]
    for days in SUMMARY_TERM_RANGE_DAYS:
        view = summary_term_view_for_days(data, days)
        label = "今日" if days == 1 else "近 {} 日".format(days)
        lines.append(
            "- {}：{}".format(
                label,
                format_summary_term_labels(view.get("terms", []), "、", "暂无"),
            )
        )
    return lines


def sort_top_assets(enriched_assets):
    return sorted(
        enriched_assets,
        key=lambda asset: (
            asset.get("estimated_value_score", 0),
            asset.get("estimated_minutes_saved", 0),
            asset.get("value_evidence_count", 0),
            asset.get("updated_at", ""),
        ),
        reverse=True,
    )


def build_data(assets, usage_events, reviews, language=None):
    language = current_language(language)
    memory_items = load_jsonl(REGISTRY_DIR / "memory_items.jsonl")
    nightly_candidates = load_nightly_summary_candidates()
    primary_nightly, active_nightly = load_primary_and_active_nightly_summaries()
    display_nightly = select_display_nightly(primary_nightly, active_nightly)
    today = current_local_datetime().date()
    today_nightly = select_best_nightly_summary_for_date(nightly_candidates, today.isoformat())
    if today_nightly:
        display_nightly = today_nightly
    window_anchor_nightly = display_nightly
    today_capture = load_daily_capture(today.isoformat())
    window_overview = build_window_overview(
        window_anchor_nightly if today_nightly or not today_capture else None,
        language=language,
        target_date=today.isoformat() if today_capture else "",
    )
    memory_usage_anchor_date = (
        (window_overview or {}).get("date")
        or (window_anchor_nightly or {}).get("date")
        or today.isoformat()
    )
    memory_usage_window_overview = build_context_window_overview_for_days(
        memory_usage_anchor_date,
        MEMORY_USAGE_WINDOW_DAYS,
        latest_nightly=window_anchor_nightly,
        language=language,
    )
    memory_registry = build_memory_registry(
        memory_items,
        window_overview,
        usage_window_overview=memory_usage_window_overview,
        language=language,
    )
    memory_mode = get_memory_mode(PATHS)
    codex_memory_dir = PATHS.codex_home / "memories"
    codex_memory_summary_path = codex_memory_dir / "memory_summary.md"
    codex_memory_index_path = codex_memory_dir / "MEMORY.md"
    personal_memory_token_usage = build_personal_memory_token_usage(
        memory_registry["rows"],
        memory_mode,
        language=language,
        memory_summary_path=codex_memory_summary_path,
    )
    known_project_names = collect_known_project_names(window_overview)
    codex_memory_summary_path_label = render_path(codex_memory_summary_path)
    codex_memory_index_path_label = render_path(codex_memory_index_path)
    codex_native_memory = parse_codex_native_memory_summary(
        codex_memory_summary_path,
        memory_index_path=codex_memory_index_path,
        known_project_names=known_project_names,
        language=language,
    )
    codex_memory_index_stats = load_codex_memory_index_stats(codex_memory_index_path, language=language)
    if codex_memory_index_stats.get("source_readable"):
        index_source_file = {"path": str(codex_memory_index_path), "label": "MEMORY.md"}
    elif codex_memory_index_stats.get("source_exists"):
        index_source_file = {
            "path": str(codex_memory_index_path),
            "label": localized("MEMORY.md 无法读取", "MEMORY.md unreadable", language),
            "status": "unreadable",
        }
    else:
        index_source_file = {
            "path": str(codex_memory_index_path),
            "label": localized("MEMORY.md 未检测到", "MEMORY.md not found", language),
            "status": "missing",
        }
    for item in codex_native_memory["rows"]:
        item.setdefault("source_files", []).append(index_source_file)
    codex_native_memory_comparison_zh = build_codex_native_memory_comparison(
        codex_native_memory["rows"],
        memory_registry["rows"],
        codex_native_memory["counts"],
        codex_memory_index_stats,
        summary_path_label=codex_memory_summary_path_label,
        index_path_label=codex_memory_index_path_label,
        language="zh",
    )
    codex_native_memory_comparison_en = build_codex_native_memory_comparison(
        codex_native_memory["rows"],
        memory_registry["rows"],
        codex_native_memory["counts"],
        codex_memory_index_stats,
        summary_path_label=codex_memory_summary_path_label,
        index_path_label=codex_memory_index_path_label,
        language="en",
    )
    codex_native_memory_comparison = (
        codex_native_memory_comparison_en.copy()
        if is_english(language)
        else codex_native_memory_comparison_zh.copy()
    )
    codex_native_memory_comparison["note_zh"] = codex_native_memory_comparison_zh.get("note", "")
    codex_native_memory_comparison["note_en"] = codex_native_memory_comparison_en.get("note", "")
    summary = summarize_assets(assets)
    usage_by_asset, recorded_minutes_saved_total, recent_usage_events = summarize_usage(usage_events)
    enriched_assets = enrich_assets(
        assets,
        usage_by_asset,
        known_project_names,
        window_overview=window_overview,
        language=language,
    )
    localized_usage_events = enrich_usage_events(recent_usage_events, language=language)
    minutes_saved_total = sum(
        safe_int(asset.get("estimated_minutes_saved", 0)) for asset in enriched_assets
    )
    project_context_anchor_date = (
        (window_overview or {}).get("date")
        or (window_anchor_nightly or {}).get("date")
        or current_local_datetime().date().isoformat()
    )
    project_context_views_zh = build_project_context_views(
        project_context_anchor_date,
        latest_nightly=window_anchor_nightly,
        language="zh",
    )
    project_context_views_en = build_project_context_views(
        project_context_anchor_date,
        latest_nightly=window_anchor_nightly,
        language="en",
    )
    project_context_views = project_context_views_en if is_english(language) else project_context_views_zh
    selected_project_context_view = (
        project_context_views.get(str(PROJECT_CONTEXT_DEFAULT_DAYS))
        or next(iter(project_context_views.values()), {})
    )
    project_contexts = selected_project_context_view.get("project_contexts", [])
    asset_type_guide = build_asset_type_guide(enriched_assets)
    summary_term_views = build_summary_term_views(
        enriched_assets,
        reviews,
        localized_usage_events,
        nightly_candidates,
        today.isoformat(),
        latest_nightly=window_anchor_nightly,
        language=language,
    )
    summary_terms = default_summary_term_view(summary_term_views).get("terms", [])
    token_usage = build_token_usage_view(resolve_ccusage_daily(), language=language)
    daily_summary_views = build_daily_summary_views(nightly_candidates, language=language)
    backfill = build_backfill_view(nightly_candidates)
    daily_summary_select_dates = sorted(
        {
            view.get("date", "")
            for view in daily_summary_views
            if view.get("date")
        }
        | set(backfill.get("missing_dates", [])),
        reverse=True,
    )
    daily_summary_default_date = (
        today.isoformat()
        if any(view.get("date") == today.isoformat() for view in daily_summary_views)
        else (display_nightly or {}).get("date", "")
    )
    if not daily_summary_default_date and daily_summary_views:
        daily_summary_default_date = daily_summary_views[0].get("date", "")
    if not daily_summary_default_date and daily_summary_select_dates:
        daily_summary_default_date = daily_summary_select_dates[0]
    window_overview_default_date = (
        today.isoformat()
        if today_capture
        else ((window_overview or {}).get("date", "") or daily_summary_default_date)
    )
    window_overview_views = build_window_overview_views(
        nightly_candidates,
        selected_date=window_overview_default_date,
        language=language,
    )
    if not window_overview_default_date and window_overview_views:
        window_overview_default_date = window_overview_views[0].get("date", "")
    generated_now = current_local_datetime()
    generated_at = generated_now.strftime("%Y-%m-%d %H:%M:%S")
    generated_at_iso = generated_now.isoformat()
    token_snapshot_note = (
        localized(
            "快照时间 {}".format(token_usage["refreshed_at_display"]),
            "Snapshot time {}".format(token_usage["refreshed_at_display"]),
            language,
        )
        if token_usage.get("refreshed_at_display")
        else localized("等待实时刷新", "Waiting for live refresh", language)
    )
    daily_window_count = (window_overview or {}).get("window_count", 0)
    daily_window_caption = localized(
        "最近一次整理捕获的窗口数",
        "Windows captured by the latest synthesis",
        language,
    )
    daily_window_meta = ""
    daily_window_date = (window_overview or {}).get("date", "")
    if daily_window_date:
        daily_window_caption = localized(
            "{} 捕获的窗口数".format(daily_window_date),
            "Windows captured on {}".format(daily_window_date),
            language,
        )
    recent_assets_all = sorted(
        enriched_assets,
        key=lambda asset: (asset.get("updated_at", ""), asset.get("title", "")),
        reverse=True,
    )
    recent_assets = recent_assets_all[:10]

    top_assets_all = sort_top_assets(enriched_assets)
    top_assets = top_assets_all[:10]

    metrics = [
        {
            "key": "total_assets",
            "label": localized("资产总数", "Total Assets", language),
            "value": len(assets),
            "caption": localized("资产注册表中的稳定条目", "Stable entries in the asset registry", language),
        },
        {
            "key": "active_assets",
            "label": localized("活跃资产", "Active Assets", language),
            "value": summary["active_assets"],
            "caption": localized("当前仍在使用的条目", "Entries still in active use", language),
        },
        {
            "key": "task_reviews",
            "label": localized("任务复盘", "Task Reviews", language),
            "value": len(reviews),
            "caption": localized("本地保存的脱敏复盘", "Sanitized local reviews", language),
        },
        {
            "key": "tracked_usage_events",
            "label": localized("复用记录", "Usage Events", language),
            "value": len(usage_events),
            "caption": localized("被记录下来的复用时刻", "Recorded reuse events", language),
        },
        {
            "key": "tracked_minutes_saved",
            "label": localized("估算节省", "Estimated Saved", language),
            "value": minutes_saved_total,
            "caption": localized(
                "按复用记录和近期工作命中自动估算的分钟数",
                "Minutes estimated from reuse events and recent work matches",
                language,
            ),
            "meta": localized(
                "原始记录分钟数 {}".format(recorded_minutes_saved_total),
                "Recorded minutes {}".format(recorded_minutes_saved_total),
                language,
            ),
        },
        {
            "key": "repo_scoped_assets",
            "label": localized("仓库场景资产", "Repo-scoped Assets", language),
            "value": summary["scope_counter"].get("repo", 0),
            "caption": localized("绑定某个仓库或场景的条目", "Entries bound to a repo or scenario", language),
        },
        {
            "key": "today_token",
            "label": localized("今日 Token", "Today Token", language),
            "value": token_usage["today_total_tokens_display"],
            "caption": localized(
                "{} 的总消耗".format(token_usage["today_date_label"]),
                "Total for {}".format(token_usage["today_date_label"]),
                language,
            ),
            "meta": token_snapshot_note,
            "live": True,
        },
        {
            "key": "seven_day_token",
            "label": localized("近 7 日 Token", "7-day Token", language),
            "value": token_usage["seven_day_total_tokens_display"],
            "caption": localized("最近 7 天累计消耗", "Total usage in the last 7 days", language),
            "meta": token_snapshot_note,
            "live": True,
        },
        {
            "key": "durable_memories",
            "label": localized(
                "个人资产-长期记忆",
                "Personal Asset - Long-term Memory",
                language,
            ),
            "value": memory_registry["counts"].get("durable", 0),
            "caption": localized("夜间整理沉淀出的长期可复用记忆", "Long-term reusable memories from nightly synthesis", language),
        },
        {
            "key": "session_memories",
            "label": localized(
                "个人资产-短期记忆",
                "Personal Asset - Short-term Memory",
                language,
            ),
            "value": memory_registry["counts"].get("session", 0),
            "caption": localized("与当前需求相关的工作记忆", "Work memories related to the current task", language),
        },
        {
            "key": "low_priority_memories",
            "label": localized(
                "个人资产-低优先记忆",
                "Personal Asset - Low-priority Memory",
                language,
            ),
            "value": memory_registry["counts"].get("low_priority", 0),
            "caption": localized("保留但优先级较低的内容", "Retained lower-priority content", language),
        },
        {
            "key": "daily_window_count",
            "label": localized("每日窗口数", "Daily Windows", language),
            "value": daily_window_count,
            "caption": daily_window_caption,
            "meta": daily_window_meta or None,
        },
    ]

    highlights = [
        localized(
            "当前已沉淀 {} 个可复用资产，其中 {} 个仍处于活跃状态。".format(
                len(assets), summary["active_assets"]
            ),
            "{} reusable assets are registered; {} are still active.".format(
                len(assets), summary["active_assets"]
            ),
            language,
        ),
        localized(
            "已形成 {} 篇任务复盘，后续会按复用证据自动估算节省时长。".format(len(reviews)),
            "{} task reviews are stored; saved time is now estimated from reuse evidence.".format(len(reviews)),
            language,
        ),
    ]
    if project_contexts:
        highlights.append(
            localized(
                "最近活跃的项目 / 上下文包括 {}。".format(
                    "、".join(item["label"] for item in project_contexts[:3])
                ),
                "Recent active projects / contexts include {}.".format(
                    ", ".join(item["label"] for item in project_contexts[:3])
                ),
                language,
            )
        )
    else:
        highlights.append(
            localized(
                "仓库场景资产 {} 个，当前以资产沉淀和近期工作上下文为主线。".format(
                    summary["scope_counter"].get("repo", 0)
                ),
                "{} repo-scoped assets are registered; current focus is asset capture and recent work context.".format(
                    summary["scope_counter"].get("repo", 0)
                ),
                language,
            )
        )
    token_highlight = ""
    if token_usage["available"]:
        token_highlight = localized(
            "{} 的 Token 总消耗为 {}，近 7 日累计为 {}。".format(
                token_usage["today_date_label"],
                token_usage["today_total_tokens_display"],
                token_usage["seven_day_total_tokens_display"],
            ),
            "{} Token usage is {}; the last 7 days total {}.".format(
                token_usage["today_date_label"],
                token_usage["today_total_tokens_display"],
                token_usage["seven_day_total_tokens_display"],
            ),
            language,
        )
        highlights.append(token_highlight)
    else:
        token_highlight = localized(
            "本地未取到 ccusage 的日维度 Token 数据，面板其余部分仍可正常使用。",
            "ccusage daily Token data is unavailable locally; the rest of the panel still works.",
            language,
        )
        highlights.append(token_highlight)

    nightly_title = localized("每日整理结果", "Daily Synthesis", language)
    nightly_note = localized("暂无夜间整理结果", "No nightly synthesis yet", language)
    active_nightly_note = ""
    window_overview_title = (
        "当日窗口概览"
        if (window_overview or {}).get("date") == generated_now.date().isoformat()
        else derive_window_overview_title(window_anchor_nightly, generated_now.date())
    )
    window_overview_title = localized(
        window_overview_title,
        {
            "昨夜窗口概览": "Last Night's Window Overview",
            "当日窗口概览": "Today's Window Overview",
            "最近一次窗口概览": "Latest Window Overview",
        }.get(window_overview_title, window_overview_title),
        language,
    )
    if display_nightly:
        stage = display_nightly.get("stage", "manual")
        stage_label = stage_display_label(stage, language=language)
        nightly_note = "{} · {}".format(display_nightly["date"], stage_label)
        highlights.append(
            localized(
                "{} 的整理结果已经生成，当前个人资产-长期记忆 {} 条、个人资产-短期记忆 {} 条、个人资产-低优先记忆 {} 条。".format(
                    display_nightly["date"],
                    len(display_nightly.get("durable_memories", [])),
                    len(display_nightly.get("session_memories", [])),
                    len(display_nightly.get("low_priority_memories", [])),
                ),
                "{} synthesis is available: {} personal asset long-term memories, {} personal asset short-term memories, and {} personal asset low-priority memories.".format(
                    display_nightly["date"],
                    len(display_nightly.get("durable_memories", [])),
                    len(display_nightly.get("session_memories", [])),
                    len(display_nightly.get("low_priority_memories", [])),
                ),
                language,
            )
        )
    if active_nightly and display_nightly is not active_nightly:
        active_stage = active_nightly.get("stage", "manual")
        active_stage_label = (
            {"final": "Final", "preliminary": "Preview", "manual": "Manual"}.get(active_stage, active_stage)
            if is_english(language)
            else {"final": "终版", "preliminary": "预览", "manual": "手动"}.get(active_stage, active_stage)
        )
        active_nightly_note = localized(
            "今日另有活跃整理：{} · {}".format(active_nightly.get("date", ""), active_stage_label),
            "Another active synthesis exists today: {} · {}".format(active_nightly.get("date", ""), active_stage_label),
            language,
        )
        highlights.append(
            localized(
                "{}，主视图仍优先保留更稳定的整理结果。".format(active_nightly_note),
                "{}; the main view still keeps the more stable synthesis as primary.".format(active_nightly_note),
                language,
            )
        )
    highlights.append(
        build_codex_native_memory_highlight(
            codex_native_memory["counts"],
            codex_native_memory_comparison,
            codex_memory_summary_path_label,
            language=language,
        )
    )

    memory_view_nightly = select_memory_view_nightly(primary_nightly, active_nightly)
    memory_view_date = (memory_view_nightly or {}).get("date") or (primary_nightly or {}).get("date", "")
    nightly_memory_views = {
        "durable": build_memory_bucket_view(
            "durable",
            memory_registry,
            memory_view_nightly,
            window_overview,
            memory_view_date,
            usage_window_overview=memory_usage_window_overview,
        ),
        "session": build_memory_bucket_view(
            "session",
            memory_registry,
            memory_view_nightly,
            window_overview,
            memory_view_date,
            usage_window_overview=memory_usage_window_overview,
        ),
        "low_priority": build_memory_bucket_view(
            "low_priority",
            memory_registry,
            memory_view_nightly,
            window_overview,
            memory_view_date,
            usage_window_overview=memory_usage_window_overview,
        ),
    }

    return {
        "language": language,
        "generated_at": generated_at,
        "generated_at_iso": generated_at_iso,
        "summary": {
            "total_assets": len(assets),
            "active_assets": summary["active_assets"],
            "task_reviews": len(reviews),
            "tracked_usage_events": len(usage_events),
            "tracked_minutes_saved": minutes_saved_total,
            "repo_scoped_assets": summary["scope_counter"].get("repo", 0),
            "daily_window_count": daily_window_count,
        },
        "metrics": metrics,
        "mix": {
            "type": build_asset_mix_rows(
                enriched_assets,
                lambda asset: asset.get("type", "unknown"),
                lambda value: display_label("type", value, language=language),
                lambda value: display_label("type", value, language="en"),
            ),
            "domain": build_asset_mix_rows(
                enriched_assets,
                lambda asset: asset.get("domain", "unknown"),
                lambda value: display_label("domain", value, language=language),
                lambda value: display_label("domain", value, language="en"),
            ),
            "context": build_asset_mix_rows(
                enriched_assets,
                lambda asset: asset.get("display_context", "未分类上下文"),
                lambda value: value,
                lambda value: panel_english_text(value) or localized_context_label(value, language="en"),
            ),
            "month": build_asset_mix_rows(
                enriched_assets,
                lambda asset: (
                    asset.get("created_at", "")[:7]
                    if len(asset.get("created_at", "")) >= 7
                    else "unknown"
                ),
            ),
            "scope": build_asset_mix_rows(
                enriched_assets,
                lambda asset: asset.get("scope", "unknown"),
                lambda value: display_label("scope", value, language=language),
                lambda value: display_label("scope", value, language="en"),
            ),
            "status": [
                {
                    "label": display_label("status", row["label"], language=language),
                    "label_en": display_label("status", row["label"], language="en"),
                    "value": row["value"],
                }
                for row in counter_to_rows(summary["status_counter"])
            ],
        },
        "assets": {
            "recent": recent_assets,
            "top": top_assets,
        },
        "reviews": reviews[:8],
        "usage_events": localized_usage_events[:10],
        "panel_views": {
            "recent_assets": recent_assets_all,
            "top_assets": top_assets_all,
            "reviews": reviews,
            "usage_events": localized_usage_events,
        },
        "summary_terms": summary_terms,
        "summary_term_default_days": SUMMARY_TERM_DEFAULT_DAYS,
        "summary_term_views": summary_term_views,
        "highlights": highlights,
        "token_highlight": token_highlight,
        "token_usage": token_usage,
        "daily_summary_views": daily_summary_views,
        "daily_summary_default_date": daily_summary_default_date,
        "daily_summary_select_dates": daily_summary_select_dates,
        "backfill": backfill,
        "window_overview_views": window_overview_views,
        "window_overview_default_date": window_overview_default_date,
        "memory_usage_window_days": MEMORY_USAGE_WINDOW_DAYS,
        "memory_usage_window": {
            "date": memory_usage_window_overview.get("date", ""),
            "window_count": memory_usage_window_overview.get("window_count", 0),
            "source_dates": memory_usage_window_overview.get("source_dates", []),
        },
        "asset_type_scope_note": localized(
            "统计来自 assets.jsonl 的全部稳定资产，不限当前仓库；只有已登记的条目会进入这里，raw、log、report 和单次对话不会计入。",
            "Counts all stable assets from assets.jsonl, not only the current repo. Only registered entries appear here; raw captures, logs, reports, and one-off chats are excluded.",
            language,
        ),
        "asset_type_guide": asset_type_guide,
        "nightly": display_nightly,
        "primary_nightly": primary_nightly,
        "active_nightly": active_nightly,
        "nightly_title": nightly_title,
        "nightly_note": nightly_note,
        "active_nightly_note": active_nightly_note,
        "window_overview_title": window_overview_title,
        "project_contexts": project_contexts,
        "project_context_views": project_context_views,
        "project_context_views_zh": project_context_views_zh,
        "project_context_views_en": project_context_views_en,
        "project_context_default_days": PROJECT_CONTEXT_DEFAULT_DAYS,
        "window_overview": window_overview,
        "memory_items": memory_items,
        "memory_registry": memory_registry["rows"],
        "personal_memory_token_usage": personal_memory_token_usage,
        "codex_native_memory": codex_native_memory["rows"],
        "codex_native_preference_rows": codex_native_memory.get("preference_rows", []),
        "codex_native_tip_rows": codex_native_memory.get("tip_rows", []),
        "codex_native_task_groups": codex_memory_index_stats.get("task_groups", []),
        "codex_native_memory_counts": codex_native_memory["counts"],
        "codex_native_memory_comparison": codex_native_memory_comparison,
        "codex_memory_summary_path": str(codex_memory_summary_path),
        "codex_memory_index_path": str(codex_memory_index_path),
        "codex_memory_summary_path_label": codex_memory_summary_path_label,
        "codex_memory_index_path_label": codex_memory_index_path_label,
        "nightly_memory_views": nightly_memory_views,
        "reading_guide": [
            localized(
                "看长期可复用资产的增长，而不是看和 AI 聊了多少次。",
                "Track growth in long-lived reusable assets, not chat volume.",
                language,
            ),
            localized(
                "优先关注复用证据和估算节省，这两个指标最能体现沉淀是否有效。",
                "Prioritize reuse evidence and estimated saved time; they best show whether the system is working.",
                language,
            ),
            localized(
                "复盘内容最好能对应到交付、排障、评审质量或风险控制中的具体价值。",
                "Reviews are most useful when tied to delivery, debugging, review quality, or risk control.",
                language,
            ),
            localized(
                "只有当条目稳定、低风险、适合共享时，再从个人范围提升到仓库或团队范围。",
                "Promote entries from personal to repo or team scope only when stable, low-risk, and shareable.",
                language,
            ),
            localized(
                "对照“Codex 原生记忆”和“个人资产记忆”看：前者偏模型长期记忆，后者偏夜间整理和来源追踪。",
                "Compare Codex Native Memory with Personal Asset Memory: the former is closer to long-term model memory, while the latter is nightly synthesis with source tracing.",
                language,
            ),
        ],
    }


def build_markdown(data):
    language = current_language(data.get("language"))
    token_usage = data["token_usage"]
    nightly = data["nightly"] or {}
    active_nightly_note = data.get("active_nightly_note", "")
    if is_english(language):
        lines = [
            "# OpenRelix Overview",
            "",
            "Generated at: `{}`".format(data["generated_at"]),
            "",
            "Visual panel: `{}`".format(PANEL_PATH_LABEL),
            "",
            "## Key Metrics",
            "",
            "- Total assets: `{}`".format(data["summary"]["total_assets"]),
            "- Active assets: `{}`".format(data["summary"]["active_assets"]),
            "- Task reviews: `{}`".format(data["summary"]["task_reviews"]),
            "- Usage events: `{}`".format(data["summary"]["tracked_usage_events"]),
            "- Estimated saved time: `{}`".format(data["summary"]["tracked_minutes_saved"]),
            "- Daily windows: `{}`".format(data["summary"]["daily_window_count"]),
            "- Today Token: `{}`".format(token_usage["today_total_tokens_display"]),
            "- 7-day Token: `{}`".format(token_usage["seven_day_total_tokens_display"]),
            "",
        ]
        lines.extend(build_summary_term_markdown_lines(data, language=language) + ["", "## Daily Token Usage", ""])

        if token_usage["available"]:
            lines.extend(["| Date | Total Token |", "| --- | --- |"])
            for row in token_usage["daily_rows"]:
                lines.append("| {} | {} |".format(row["label"], row.get("display", compact_token(row["value"], language=language))))
        else:
            lines.append("ccusage daily data is unavailable.")

        window_overview = data.get("window_overview") or {}
        if nightly:
            nightly_window_title = data.get("window_overview_title", "Latest Window Overview")
            lines.extend(
                [
                    "",
                    "## {}".format(data["nightly_title"]),
                    "",
                    "Synthesis note: `{}`".format(data["nightly_note"]),
                ]
            )
            if active_nightly_note:
                lines.extend(["", "Active synthesis: `{}`".format(active_nightly_note)])
            lines.extend(
                [
                    "",
                    nightly.get("day_summary", ""),
                    "",
                    "### {}".format(nightly_window_title),
                    "",
                    "| Window | Project / Workspace | Questions | Conclusions | Summary |",
                    "| --- | --- | --- | --- | --- |",
                ]
            )
            for item in window_overview.get("windows", [])[:10]:
                lines.append(
                    "| {} | {} | {} | {} | {} |".format(
                        item.get("display_index", item.get("window_id", "")[:8]),
                        item.get("cwd_display", item.get("cwd", "")),
                        item.get("question_count", 0),
                        item.get("conclusion_count", 0),
                        item.get("main_takeaway", "").replace("|", "/"),
                    )
                )

        lines.extend(
            [
                "",
                "## Asset Type Distribution",
                "",
                make_table(Counter({row["label"]: row["value"] for row in data["mix"]["type"]}), ["Type", "Count"]),
                "",
                "## Monthly Additions",
                "",
                make_table(Counter({row["label"]: row["value"] for row in data["mix"]["month"]}), ["Month", "Count"]),
                "",
                "## Scope",
                "",
                make_table(Counter({row["label"]: row["value"] for row in data["mix"].get("scope", [])}), ["Scope", "Count"]),
                "",
                "## Project / Context Distribution",
                "",
                make_table(Counter({row["label"]: row["value"] for row in data["mix"]["context"]}), ["Project / Context", "Count"]),
                "",
                "## Current Project Context",
                "",
                "| Project / Context | Windows | Questions | Conclusions | Summary |",
                "| --- | --- | --- | --- | --- |",
            ]
        )

        if data.get("project_contexts"):
            for item in data.get("project_contexts", [])[:8]:
                lines.append(
                    "| {} | {} | {} | {} | {} |".format(
                        item.get("label", ""),
                        item.get("window_count", 0),
                        item.get("question_count", 0),
                        item.get("conclusion_count", 0),
                        item.get("summary", "").replace("|", "/"),
                    )
                )
        else:
            lines.append("| None | 0 | 0 | 0 | No displayable summary. |")

        native_note = (data.get("codex_native_memory_comparison") or {}).get("note", "")
        codex_memory_summary_label = data.get("codex_memory_summary_path_label") or render_path(
            PATHS.codex_home / "memories" / "memory_summary.md"
        )
        codex_memory_index_label = data.get("codex_memory_index_path_label") or render_path(
            PATHS.codex_home / "memories" / "MEMORY.md"
        )
        lines.extend(
            [
                "",
                "## Codex Native Memory",
                "",
                "- Overview: {}".format(markdown_inline_text(native_note or "No native memory summary.")),
                "- Source: {} and {}".format(
                    markdown_inline_text(codex_memory_summary_label),
                    markdown_inline_text(codex_memory_index_label),
                ),
                "",
                "| Title | Recently Updated | Related Context | Summary |",
                "| --- | --- | --- | --- |",
            ]
        )
        if data.get("codex_native_memory"):
            for item in data.get("codex_native_memory", [])[:12]:
                lines.append(
                    "| {} | {} | {} | {} |".format(
                        markdown_table_cell(item.get("display_title") or item.get("title", ""), limit=92),
                        markdown_table_cell(item.get("updated_at_display", "")),
                        markdown_table_cell(" / ".join(item.get("context_labels", [])[:2]) or item.get("display_context", "")),
                        markdown_table_cell(item.get("display_value_note") or item.get("value_note", ""), limit=120),
                    )
                )
            hidden_native_count = len(data.get("codex_native_memory", [])) - 12
            if hidden_native_count > 0:
                lines.append("| {} more hidden |  |  | See the HTML panel. |".format(hidden_native_count))
        else:
            lines.append("| None | None | None | None |")

        lines.extend(
            [
                "",
                "## Recently Updated Assets",
                "",
                "| Title | Type | Project / Context | Scope | Updated |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        if data["assets"]["recent"]:
            for asset in data["assets"]["recent"]:
                lines.append(
                    "| {} | {} | {} | {} | {} |".format(
                        asset.get("display_title") or asset.get("title", ""),
                        asset.get("display_type", asset.get("type", "")),
                        asset.get("display_context", asset.get("display_domain", asset.get("domain", ""))),
                        asset.get("display_scope", asset.get("scope", "")),
                        asset.get("updated_at", ""),
                    )
                )
        else:
            lines.append("| None | None | None | None | None |")

        lines.extend(
            [
                "",
                "## High-value Reusable Assets",
                "",
                "| Title | Value Score | Estimated Saved | Evidence | Note |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        if data["assets"]["top"]:
            for asset in data["assets"]["top"]:
                lines.append(
                    "| {} | {} | {} | {} | {} |".format(
                        asset.get("display_title") or asset.get("title", ""),
                        asset.get("estimated_value_score", 0),
                        asset.get("estimated_minutes_saved_display", ""),
                        asset.get("value_evidence_label", ""),
                        (asset.get("display_value_note") or asset.get("value_note", "")).replace("|", "/"),
                    )
                )
        else:
            lines.append("| None | 0 | 0 min | None | None |")

        lines.extend(["", "## Reading Guide", ""])
        lines.extend("- {}".format(item) for item in data["reading_guide"])
        return "\n".join(lines) + "\n"

    lines = [
        "# OpenRelix 工作台",
        "",
        "生成时间：`{}`".format(data["generated_at"]),
        "",
        "可视化面板：`{}`".format(PANEL_PATH_LABEL),
        "",
        "## 核心指标",
        "",
        "- 资产总数：`{}`".format(data["summary"]["total_assets"]),
        "- 活跃资产：`{}`".format(data["summary"]["active_assets"]),
        "- 任务复盘：`{}`".format(data["summary"]["task_reviews"]),
        "- 复用记录：`{}`".format(data["summary"]["tracked_usage_events"]),
        "- 估算节省：`{}`".format(data["summary"]["tracked_minutes_saved"]),
        "- 每日窗口数：`{}`".format(data["summary"]["daily_window_count"]),
        "- 今日 Token：`{}`".format(token_usage["today_total_tokens_display"]),
        "- 近 7 日 Token：`{}`".format(token_usage["seven_day_total_tokens_display"]),
        "",
    ]
    lines.extend(build_summary_term_markdown_lines(data, language=language) + ["", "## 每日 Token 消耗", ""])

    if token_usage["available"]:
        lines.extend(["| 日期 | 总 Token |", "| --- | --- |"])
        for row in token_usage["daily_rows"]:
            lines.append("| {} | {} |".format(row["label"], row.get("display", compact_token_zh(row["value"]))))
    else:
        lines.append("暂未获取到 ccusage 日维度数据。")

    window_overview = data.get("window_overview") or {}
    if nightly:
        nightly_window_title = data.get("window_overview_title", derive_nightly_window_title(data["nightly_title"]))
        lines.extend(
            [
                "",
                "## {}".format(data["nightly_title"]),
                "",
                "整理说明：`{}`".format(data["nightly_note"]),
            ]
        )
        if active_nightly_note:
            lines.extend(
                [
                    "",
                    "活跃整理：`{}`".format(active_nightly_note),
                ]
            )
        lines.extend(
            [
                "",
                nightly.get("day_summary", ""),
                "",
                "### {}".format(nightly_window_title),
                "",
                "| 窗口 | 项目 / 工作区 | 问题数 | 结论数 | 小结 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in window_overview.get("windows", [])[:10]:
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    item.get("display_index", item.get("window_id", "")[:8]),
                    item.get("cwd_display", item.get("cwd", "")),
                    item.get("question_count", 0),
                    item.get("conclusion_count", 0),
                    item.get("main_takeaway", "").replace("|", "/"),
                )
            )

    lines.extend(
        [
            "",
            "## 资产类型分布",
            "",
            make_table(
                Counter({row["label"]: row["value"] for row in data["mix"]["type"]}),
                ["类型", "数量"],
                empty_label="暂无",
            ),
            "",
            "## 月度新增",
            "",
            make_table(
                Counter({row["label"]: row["value"] for row in data["mix"]["month"]}),
                ["月份", "数量"],
                empty_label="暂无",
            ),
            "",
            "## 适用层级",
            "",
            make_table(
                Counter({row["label"]: row["value"] for row in data["mix"].get("scope", [])}),
                ["适用层级", "数量"],
                empty_label="暂无",
            ),
            "",
            "## 项目 / 上下文分布",
            "",
            make_table(
                Counter({row["label"]: row["value"] for row in data["mix"]["context"]}),
                ["项目 / 上下文", "数量"],
                empty_label="暂无",
            ),
            "",
            "## 当前项目上下文",
            "",
            "| 项目 / 上下文 | 窗口数 | 问题数 | 结论数 | 摘要 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )

    if data.get("project_contexts"):
        for item in data.get("project_contexts", [])[:8]:
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    item.get("label", ""),
                    item.get("window_count", 0),
                    item.get("question_count", 0),
                    item.get("conclusion_count", 0),
                    item.get("summary", "").replace("|", "/"),
                )
            )
    else:
        lines.append("| 暂无 | 0 | 0 | 0 | 暂无可展示摘要。 |")

    native_note = (data.get("codex_native_memory_comparison") or {}).get("note", "")
    codex_memory_summary_label = data.get("codex_memory_summary_path_label") or render_path(
        PATHS.codex_home / "memories" / "memory_summary.md"
    )
    codex_memory_index_label = data.get("codex_memory_index_path_label") or render_path(
        PATHS.codex_home / "memories" / "MEMORY.md"
    )
    lines.extend(
        [
            "",
            "## Codex 原生记忆",
            "",
            "- 概览: {}".format(markdown_inline_text(native_note or "暂无原生记忆摘要。")),
            "- 来源: {} 与 {}".format(
                markdown_inline_text(codex_memory_summary_label),
                markdown_inline_text(codex_memory_index_label),
            ),
            "",
            "| 标题 | 最近更新 | 关联上下文 | 摘要 |",
            "| --- | --- | --- | --- |",
        ]
    )

    if data.get("codex_native_memory"):
        for item in data.get("codex_native_memory", [])[:12]:
            lines.append(
                "| {} | {} | {} | {} |".format(
                    markdown_table_cell(item.get("display_title") or item.get("title", ""), limit=92),
                    markdown_table_cell(item.get("updated_at_display", "")),
                    markdown_table_cell(
                        " / ".join(item.get("context_labels", [])[:2]) or item.get("display_context", "")
                    ),
                    markdown_table_cell(item.get("display_value_note") or item.get("value_note", ""), limit=120),
                )
            )
        hidden_native_count = len(data.get("codex_native_memory", [])) - 12
        if hidden_native_count > 0:
            lines.append("| 另有 {} 条未展示 |  |  | 详见 HTML 面板。 |".format(hidden_native_count))
    else:
        lines.append("| 暂无 | 暂无 | 暂无 | 暂无 |")

    lines.extend(
        [
            "",
            "## 最近更新的资产",
            "",
            "| 标题 | 类型 | 项目 / 上下文 | 适用层级 | 更新时间 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )

    if data["assets"]["recent"]:
        for asset in data["assets"]["recent"]:
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    asset.get("display_title") or asset.get("title", ""),
                    asset.get("display_type", asset.get("type", "")),
                    asset.get("display_context", asset.get("display_domain", asset.get("domain", ""))),
                    asset.get("display_scope", asset.get("scope", "")),
                    asset.get("updated_at", ""),
                )
            )
    else:
        lines.append("| 暂无 | 暂无 | 暂无 | 暂无 | 暂无 |")

    lines.extend(
        [
            "",
            "## 复用价值较高的资产",
            "",
            "| 标题 | 价值分 | 估算节省 | 证据 | 说明 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )

    if data["assets"]["top"]:
        for asset in data["assets"]["top"]:
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    asset.get("display_title") or asset.get("title", ""),
                    asset.get("estimated_value_score", 0),
                    asset.get("estimated_minutes_saved_display", ""),
                    asset.get("value_evidence_label", ""),
                    (asset.get("display_value_note") or asset.get("value_note", "")).replace("|", "/"),
                )
            )
    else:
        lines.append("| 暂无 | 0 | 0 分钟 | 暂无 | 暂无 |")

    lines.extend(["", "## 阅读提示", ""])
    lines.extend("- {}".format(item) for item in data["reading_guide"])
    return "\n".join(lines) + "\n"


def build_csv(data, output_path):
    with output_path.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(
            [
                "id",
                "title",
                "type",
                "display_type",
                "domain",
                "display_domain",
                "scope",
                "display_scope",
                "status",
                "display_status",
                "created_at",
                "updated_at",
                "reuse_count",
                "tracked_usage_events",
                "tracked_minutes_saved",
                "estimated_value_score",
                "estimated_minutes_saved",
                "value_evidence_count",
                "value_note",
                "artifact_paths",
                "tags",
            ]
        )
        for asset in data["assets"]["recent"] + [
            asset
            for asset in data["assets"]["top"]
            if asset["id"] not in {item["id"] for item in data["assets"]["recent"]}
        ]:
            writer.writerow(
                [
                    asset.get("id", ""),
                    asset.get("display_title") or asset.get("title", ""),
                    asset.get("type", ""),
                    asset.get("display_type", asset.get("type", "")),
                    asset.get("domain", ""),
                    asset.get("display_domain", asset.get("domain", "")),
                    asset.get("scope", ""),
                    asset.get("display_scope", asset.get("scope", "")),
                    asset.get("status", ""),
                    asset.get("display_status", asset.get("status", "")),
                    asset.get("created_at", ""),
                    asset.get("updated_at", ""),
                    asset.get("manual_reuse_count", 0),
                    asset.get("tracked_usage_events", 0),
                    asset.get("tracked_minutes_saved", 0),
                    asset.get("estimated_value_score", 0),
                    asset.get("estimated_minutes_saved", 0),
                    asset.get("value_evidence_count", 0),
                    asset.get("display_value_note") or asset.get("value_note", ""),
                    "; ".join(asset.get("artifact_paths", [])),
                    ", ".join(asset.get("tags", [])),
                ]
            )


def make_bar_detail_popover(details, heading="对应项目 / 条目", heading_en=""):
    if not details:
        return "", ""

    items = []
    aria_titles = []
    aria_titles_en = []
    for detail in details:
        if isinstance(detail, dict):
            title = str(detail.get("title", "") or "").strip()
            meta = str(detail.get("meta", "") or "").strip()
            title_en = str(detail.get("title_en", "") or "").strip()
            meta_en = str(detail.get("meta_en", "") or "").strip()
        else:
            title = str(detail or "").strip()
            meta = ""
            title_en = ""
            meta_en = ""
        if not title:
            continue
        if not title_en or contains_cjk(title_en):
            title_en = english_freeform_text(title, fallback_label="Item")
        if meta and (not meta_en or contains_cjk(meta_en)):
            meta_en = english_freeform_text(meta, fallback_label="Details")
        aria_titles.append(title)
        aria_titles_en.append(title_en)
        meta_html = ""
        if meta:
            meta_html = '<span class="bar-detail-meta">{}</span>'.format(
                panel_language_text_html(meta, meta_en)
            )
        items.append(
            """
              <span class="bar-detail-item">
                <span class="bar-detail-title">{title}</span>
                {meta_html}
              </span>
            """.format(
                title=panel_language_text_html(title, title_en),
                meta_html=meta_html,
            )
        )

    if not items:
        return "", ""

    heading_en = heading_en or panel_english_text(heading) or english_freeform_text(heading, fallback_label="Details")
    aria_source_heading = heading_en if heading_en else heading
    aria_source_titles = [item for item in aria_titles_en if item] or aria_titles
    aria_label = "{}: {}".format(aria_source_heading, ", ".join(aria_source_titles[:8]))
    if len(aria_titles) > 8:
        aria_label = "{}, {} more".format(aria_label, len(aria_titles) - 8)
    return (
        """
        <span class="bar-detail-popover" role="tooltip">
          <span class="bar-detail-heading">{heading}</span>
          <span class="bar-detail-list">
            {items}
          </span>
        </span>
        """.format(
            heading=panel_language_text_html(heading, heading_en),
            items="".join(items),
        ),
        aria_label,
    )


def make_bar_value(value, details=None, heading="对应项目 / 条目", heading_en=""):
    popover_html, aria_label = make_bar_detail_popover(details, heading=heading, heading_en=heading_en)
    if not popover_html:
        return "<strong>{}</strong>".format(escape(str(value)))

    return """
      <strong class="bar-value has-details" tabindex="0" aria-label="{aria_label}">
        <span class="bar-value-number">{value}</span>
        {popover_html}
      </strong>
    """.format(
        aria_label=escape(aria_label, quote=True),
        value=escape(str(value)),
        popover_html=popover_html,
    )


def safe_css_class(value, fallback=""):
    candidate = str(value or "").strip()
    fallback_candidate = str(fallback or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]+", candidate):
        return candidate
    if re.fullmatch(r"[A-Za-z0-9_-]+", fallback_candidate):
        return fallback_candidate
    return ""


def make_bar_rows(rows, accent_class):
    if not rows:
        return '<p class="empty">暂无数据。</p>'

    max_value = max(row["value"] for row in rows) or 1
    items = []
    for row in rows:
        width = int((row["value"] / max_value) * 100)
        value = row.get("display", compact_number(row["value"]))
        tone = safe_css_class(row.get("tone"), accent_class)
        label_text = str(row["label"])
        label_en = row.get("label_en") or panel_english_text(label_text) or english_freeform_text(
            label_text,
            fallback_label="Label",
        )
        if contains_cjk(label_en):
            label_en = english_freeform_text(label_text, fallback_label="Label")
        items.append(
            """
            <div class="bar-row">
              <div class="bar-copy">
                <span>{label}</span>
                {value_html}
              </div>
              <div class="bar-track">
                <div class="bar-fill {accent}" style="width:{width}%"></div>
              </div>
            </div>
            """.format(
                label=panel_language_text_html(
                    label_text,
                    label_en,
                ),
                value_html=make_bar_value(
                    value,
                    row.get("details"),
                    heading=row.get("details_heading", "对应项目 / 条目"),
                    heading_en=row.get("details_heading_en", ""),
                ),
                accent=escape(tone, quote=True),
                width=width,
            )
        )
    return "".join(items)


def make_help_popover(title, sections, compact=False, language=None):
    if not sections:
        return ""
    language = current_language(language)

    def render_help_text(value):
        if isinstance(value, dict):
            zh_text = value.get("zh", "") or value.get("text", "") or value.get("body", "")
            en_text = value.get("en", "") or value.get("text_en", "") or value.get("body_en", "")
            return panel_language_text_html(zh_text, en_text)
        return escape(panel_display_text(value, language))

    section_html = []
    for section in sections:
        label = str(section.get("label", "") or "").strip()
        body = section.get("body", "")
        if not body:
            continue

        if isinstance(body, (list, tuple)):
            body_html = """
            <ul class="module-help-list">
              {items}
            </ul>
            """.format(
                items="".join(
                    "<li>{}</li>".format(render_help_text(item))
                    for item in body
                    if (str(item).strip() if not isinstance(item, dict) else any(str(v).strip() for v in item.values()))
                )
            )
        else:
            body_html = '<p class="module-help-copy">{}</p>'.format(render_help_text(body))

        label_html = ""
        if label:
            label_html = '<div class="module-help-section-label">{}</div>'.format(
                escape(panel_display_text(label, language))
            )

        section_html.append(
            """
            <section class="module-help-section">
              {label_html}
              {body_html}
            </section>
            """.format(
                label_html=label_html,
                body_html=body_html,
            )
        )

    if not section_html:
        return ""

    classes = "module-help"
    if compact:
        classes = "{} is-compact".format(classes)

    title_text = panel_display_text(title, language)
    help_label = localized("说明", "Help", language)
    return """
      <div class="{classes}">
        <button class="module-help-trigger" type="button" aria-label="{title} {help_label}" title="{title} {help_label}">?</button>
        <div class="module-help-card" role="tooltip">
          <div class="module-help-title">{title}</div>
          <div class="module-help-sections">
            {section_html}
          </div>
        </div>
      </div>
    """.format(
        classes=classes,
        title=escape(title_text),
        help_label=escape(help_label),
        section_html="".join(section_html),
    )


def make_panel_header(
    title,
    note="",
    help_html="",
    note_id="",
    note_content_html="",
    title_id="",
    extra_meta_html="",
    language=None,
):
    language = current_language(language)
    note_html = ""
    if note_content_html:
        note_attrs = ' id="{}"'.format(escape(note_id)) if note_id else ""
        note_html = '<div class="panel-note"{}>{}</div>'.format(
            note_attrs,
            note_content_html,
        )
    elif note:
        note_attrs = ' id="{}"'.format(escape(note_id)) if note_id else ""
        note_html = '<div class="panel-note"{}>{}</div>'.format(
            note_attrs,
            escape(panel_display_text(note, language)),
        )

    meta_html = ""
    if extra_meta_html or note_html or help_html:
        meta_html = """
        <div class="panel-head-meta">
          {extra_meta_html}
          {note_html}
          {help_html}
        </div>
        """.format(
            extra_meta_html=extra_meta_html,
            note_html=note_html,
            help_html=help_html,
        )
    title_attrs = ' id="{}"'.format(escape(title_id, quote=True)) if title_id else ""

    return """
      <div class="panel-head">
        <h2{title_attrs}>{title}</h2>
        {meta_html}
      </div>
    """.format(
        title_attrs=title_attrs,
        title=escape(panel_display_text(title, language)),
        meta_html=meta_html,
    )


def build_asset_type_help_sections(scope_note, rows):
    sections = []
    if scope_note:
        sections.append({"label": "统计口径", "body": scope_note})

    type_rows = []
    for row in rows:
        examples = row.get("examples", []) or []
        examples_en = row.get("examples_en", []) or []
        examples_en = [
            english_freeform_text(example, fallback_label="Asset") if contains_cjk(example) else example
            for example in examples_en
        ]
        parts = [
            "{}：{}".format(row.get("label", ""), row.get("description", "")),
            "当前 {} 条".format(row.get("count", 0)),
        ]
        parts_en = [
            "{}: {}".format(
                row.get("label_en", "") or panel_english_text(row.get("label", "")),
                row.get("description_en", "") or panel_english_text(row.get("description", "")),
            ),
            "Current {}".format(plural_en(row.get("count", 0), "item")),
        ]
        if examples:
            parts.append("例如 {}".format("、".join(examples)))
        if examples_en:
            parts_en.append("Examples: {}".format(", ".join(examples_en)))
        type_rows.append({"zh": "；".join(parts), "en": "; ".join(parts_en)})

    if type_rows:
        sections.append({"label": "类型说明", "body": type_rows})
    return sections


def build_report_redirect_html(title, target_path):
    target_uri = target_path.resolve().as_uri()
    target_label = render_path(target_path)
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta http-equiv="refresh" content="0; url={target_uri}" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f5f7;
      --card: rgba(255, 255, 255, 0.86);
      --text: #1d1d1f;
      --muted: #6e6e73;
      --accent: #0071e3;
      --border: rgba(0, 0, 0, 0.08);
      font-family: "SF Pro Text", "PingFang SC", sans-serif;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: linear-gradient(180deg, #fbfbfd 0%, var(--bg) 100%);
      color: var(--text);
    }}
    main {{
      width: min(640px, calc(100vw - 32px));
      padding: 28px 32px;
      border-radius: 24px;
      background: var(--card);
      border: 1px solid var(--border);
      box-shadow: 0 18px 42px rgba(0, 0, 0, 0.08);
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 28px;
      line-height: 1.2;
    }}
    p {{
      margin: 0 0 12px;
      line-height: 1.6;
      color: var(--muted);
    }}
    a {{
      color: var(--accent);
      word-break: break-all;
    }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <p>仓库里的这个入口只保留兼容跳转，不再直接承载实时数据。</p>
    <p>页面会自动跳到当前状态目录中的最新报表；如果浏览器没有自动跳转，可以手动打开下面这个路径。</p>
    <p><a href="{target_uri}">{target_label}</a></p>
    <p>项目页：<a href="{project_github_url}" target="_blank" rel="noopener noreferrer">openrelix/openrelix</a>，欢迎点星支持。</p>
  </main>
  <script>
    window.location.replace({target_uri_json});
  </script>
</body>
</html>
""".format(
        title=escape(title),
        target_uri=escape(target_uri, quote=True),
        target_label=escape(target_label),
        target_uri_json=json.dumps(target_uri),
        project_github_url=escape(PROJECT_GITHUB_URL, quote=True),
    )


def remove_legacy_dashboard_outputs():
    report_dirs = {REPORTS_DIR, PATHS.repo_root / "reports"}
    for reports_dir in report_dirs:
        for filename in (
            "dashboard-data.json",
            "dashboard.md",
            "dashboard.html",
            "dashboard.csv",
        ):
            path = reports_dir / filename
            if path.exists():
                path.unlink()


def write_repo_panel_entrypoint():
    repo_reports_dir = PATHS.repo_root / "reports"
    if repo_reports_dir.resolve() == REPORTS_DIR.resolve():
        return

    repo_reports_dir.mkdir(parents=True, exist_ok=True)
    (repo_reports_dir / "panel.html").write_text(
        build_report_redirect_html("OpenRelix 工作台", REPORTS_DIR / "panel.html"),
        encoding="utf-8",
    )


def make_bar_group(
    title,
    rows,
    accent_class,
    note="",
    panel_id="",
    note_id="",
    rows_id="",
    extra_classes="",
    help_html="",
):
    panel_attrs = ' id="{}"'.format(escape(panel_id)) if panel_id else ""
    rows_attrs = ' id="{}"'.format(escape(rows_id)) if rows_id else ""
    panel_classes = "panel"
    if extra_classes:
        panel_classes = "{} {}".format(panel_classes, escape(extra_classes))

    return """
    <section class="{panel_classes}"{panel_attrs}>
      {header_html}
      <div class="bar-group"{rows_attrs}>
        {items}
      </div>
    </section>
    """.format(
        panel_classes=panel_classes,
        panel_attrs=panel_attrs,
        header_html=make_panel_header(title, note, help_html, note_id=note_id),
        rows_attrs=rows_attrs,
        items=make_bar_rows(rows, accent_class),
    )


def make_token_summary_cards_html(cards):
    if not cards:
        return '<p class="empty">暂无数据。</p>'

    items = []
    for card in cards:
        tone = card.get("tone", "neutral")
        if tone not in {"up", "down", "neutral"}:
            tone = "neutral"
        items.append(
            """
            <div class="token-stat is-{tone}">
              <div class="token-stat-label">{label}</div>
              <div class="token-stat-value">{value}</div>
              <div class="token-stat-caption">{caption}</div>
            </div>
            """.format(
                tone=escape(tone),
                label=escape(str(card.get("label", ""))),
                value=escape(str(card.get("value", ""))),
                caption=escape(str(card.get("caption", ""))),
            )
        )
    return "".join(items)


def make_token_overview_panel(token_usage, help_html=""):
    return """
    <section class="panel token-overview-panel" id="token-overview-panel">
      {header_html}
      <div class="token-stat-grid" id="token-summary-cards">
        {summary_cards}
      </div>
    </section>
    """.format(
        header_html=make_panel_header(
            "Token 速览",
            token_usage.get("overview_note", ""),
            help_html,
            note_id="token-overview-note",
        ),
        summary_cards=make_token_summary_cards_html(token_usage.get("summary_cards", [])),
    )


def wrap_expandable_block(
    primary_html,
    extra_html,
    extra_count,
    item_label,
    extra_container_class,
    expanded_label="收起更多内容",
    item_label_en="",
    expanded_label_en="",
):
    if not extra_html or extra_count <= 0:
        return primary_html
    collapsed_label_html = panel_language_text_html(
        "查看更多 {} {}".format(extra_count, item_label),
        "Show {} more {}".format(extra_count, item_label_en or panel_english_text(item_label) or item_label),
    )
    expanded_label_html = panel_language_text_html(
        expanded_label,
        expanded_label_en or panel_english_text(expanded_label) or expanded_label,
    )
    return """
        {primary_html}
        <details class="content-more">
          <summary class="content-more-trigger">
            <span class="content-more-collapsed">{collapsed_label}</span>
            <span class="content-more-expanded">{expanded_label}</span>
          </summary>
          <div class="{extra_container_class}">
            {extra_html}
          </div>
        </details>
    """.format(
        primary_html=primary_html,
        collapsed_label=collapsed_label_html,
        expanded_label=expanded_label_html,
        extra_container_class=escape(extra_container_class),
        extra_html=extra_html,
    )


def make_table_expand_rows(
    rows,
    render_row,
    visible_count,
    column_count,
    item_label,
    expanded_label,
    group_id,
):
    primary_rows = "".join(render_row(row) for row in rows[:visible_count])
    if len(rows) <= visible_count:
        return primary_rows

    extra_rows = "".join(
        render_row(row, row_class="content-more-extra-row", group_id=group_id, hidden_attr=" hidden")
        for row in rows[visible_count:]
    )
    toggle_label = escape("查看更多 {} {}".format(len(rows) - visible_count, item_label))
    expanded_label = escape(expanded_label)
    toggle_row = """
        <tr class="content-more-row">
          <td colspan="{column_count}" class="content-more-cell">
            <button
              class="content-more-button"
              type="button"
              data-expand-group="{group_id}"
              data-collapsed-label="{collapsed_label}"
              data-expanded-label="{expanded_label}"
              aria-expanded="false"
            >{collapsed_label}</button>
          </td>
        </tr>
    """.format(
        column_count=column_count,
        group_id=escape(group_id),
        collapsed_label=toggle_label,
        expanded_label=expanded_label,
    )
    return primary_rows + toggle_row + extra_rows


def make_asset_rows(rows, group_id="asset-rows"):
    if not rows:
        return '<tr><td colspan="6" class="empty-cell">暂无资产。</td></tr>'

    def render_row(row, row_class="", group_id="", hidden_attr=""):
        row_class_attr = ' class="{}"'.format(escape(row_class)) if row_class else ""
        if group_id:
            row_class_attr = ' class="{}" data-expand-group="{}"{}'.format(
                escape(row_class),
                escape(group_id),
                hidden_attr,
            )
        impact = row.get("display_value_note") or row.get("value_note", "")
        impact_en = row.get("display_value_note_en") or row.get("value_note_en", "")
        if impact and (not impact_en or contains_cjk(impact_en)):
            impact_en = english_freeform_text(impact, fallback_label="Value note")
        context = row.get("display_context", row.get("display_domain", row.get("domain", "")))
        context_en = row.get("display_context_en") or row.get("display_domain_en", "")
        if context and (not context_en or contains_cjk(context_en)):
            context_en = english_freeform_text(context, fallback_label="Context")
        return """
            <tr{row_class_attr}>
              <td>
                <div class="table-title">{title}</div>
                <div class="table-subtle">{impact}</div>
              </td>
              <td>{type}</td>
              <td>{context}</td>
              <td>{scope}</td>
              <td>{updated_at}</td>
              <td>{tracked_usage_events}</td>
            </tr>
            """.format(
                title=render_asset_title_link(row),
                impact=panel_language_text_html(impact, impact_en),
                type=panel_language_text_html(
                    row.get("display_type", row.get("type", "")),
                    row.get("display_type_en", ""),
                ),
                context=panel_language_text_html(context, context_en),
                scope=panel_language_text_html(
                    row.get("display_scope", row.get("scope", "")),
                    row.get("display_scope_en", ""),
                ),
                updated_at=escape(row.get("updated_at", "")),
                tracked_usage_events=escape(str(row.get("tracked_usage_events", 0))),
                row_class_attr=row_class_attr,
            )

    return make_table_expand_rows(
        rows,
        render_row,
        10,
        6,
        "条资产",
        "收起更多资产",
        group_id,
    )


def make_top_asset_rows(rows, group_id="top-asset-rows"):
    if not rows:
        return '<tr><td colspan="4" class="empty-cell">暂无资产。</td></tr>'

    def render_row(row, row_class="", group_id="", hidden_attr=""):
        row_class_attr = ' class="{}"'.format(escape(row_class)) if row_class else ""
        if group_id:
            row_class_attr = ' class="{}" data-expand-group="{}"{}'.format(
                escape(row_class),
                escape(group_id),
                hidden_attr,
            )
        signals = row.get("value_signals", []) or []
        signals_en = row.get("value_signals_en", []) or []
        signal_text = "；".join(signals[:3]) or row.get("value_reason", "")
        signal_text_en = "; ".join(signals_en[:3]) or row.get("value_reason_en", "")
        if signal_text and (not signal_text_en or contains_cjk(signal_text_en)):
            signal_text_en = english_freeform_text(signal_text, fallback_label="Signals")
        context = row.get("display_context", row.get("display_domain", row.get("domain", "")))
        context_en = row.get("display_context_en") or row.get("display_domain_en", "")
        if context and (not context_en or contains_cjk(context_en)):
            context_en = english_freeform_text(context, fallback_label="Context")
        note = row.get("display_value_note") or row.get("value_reason", "")
        note_en = row.get("display_value_note_en") or row.get("value_reason_en", "")
        if note and (not note_en or contains_cjk(note_en)):
            note_en = english_freeform_text(note, fallback_label="Value note")
        reason_parts = [part for part in (context, note) if part]
        reason_parts_en = [part for part in (context_en, note_en) if part]
        return """
            <tr{row_class_attr}>
              <td>
                <div class="table-title">{title}</div>
                <div class="table-subtle">{reason}</div>
              </td>
              <td>
                <strong class="value-score">{score}</strong>
                <div class="table-subtle">{level}</div>
              </td>
              <td>{estimated_minutes}</td>
              <td>
                <div>{evidence}</div>
                <div class="table-subtle">{signals}</div>
              </td>
            </tr>
            """.format(
                title=render_asset_title_link(row),
                reason=panel_language_text_html(" · ".join(reason_parts), " · ".join(reason_parts_en)),
                score=escape(str(row.get("estimated_value_score", 0))),
                level=panel_language_text_html(
                    row.get("estimated_value_level", ""),
                    row.get("estimated_value_level_en", ""),
                ),
                estimated_minutes=panel_language_text_html(
                    row.get("estimated_minutes_saved_display", ""),
                    row.get("estimated_minutes_saved_display_en", ""),
                ),
                evidence=panel_language_text_html(
                    row.get("value_evidence_label", ""),
                    row.get("value_evidence_label_en", ""),
                ),
                signals=panel_language_text_html(signal_text, signal_text_en),
                row_class_attr=row_class_attr,
            )

    return make_table_expand_rows(
        rows,
        render_row,
        10,
        4,
        "条资产",
        "收起更多资产",
        group_id,
    )


def make_review_cards(reviews):
    if not reviews:
        return '<p class="empty">暂无复盘。</p>'

    def render_card(review):
        raw_path = review.get("path", "")
        file_label = Path(raw_path).name if raw_path else "复盘文件"
        if raw_path:
            try:
                file_label = str(Path(raw_path).resolve().relative_to(REVIEWS_DIR.parent.resolve()))
            except (OSError, ValueError):
                pass
        file_html = build_local_path_anchor(
            raw_path,
            file_label,
            class_name="path-link path-link-subtle",
        )
        repo_html = linkify_local_paths_html(review.get("repo", ""))
        repo_block = (
            """
              <div>
                <span>{repo_label}</span>
                <p>{repo}</p>
              </div>
            """.format(
                repo_label=panel_language_text_html("项目 / 上下文"),
                repo=repo_html,
            )
            if review.get("repo")
            else ""
        )
        domain = review.get("domain", "") or "未知"
        domain_en = english_freeform_text(domain, fallback_label="Unknown")
        task = review.get("task", "") or "未命名复盘"
        task_en = english_freeform_text(task, fallback_label="Task review")
        return """
            <article class="review-card">
              <div class="review-meta">{date} · {domain}</div>
              <h3>{task}</h3>
              <div class="review-card-links">
                <div>
                  <span>{file_label}</span>
                  {file}
                </div>
                {repo}
              </div>
            </article>
            """.format(
                date=escape(review.get("date", "")),
                domain=panel_language_text_html(domain, domain_en),
                task=panel_language_text_html(task, task_en),
                file_label=panel_language_text_html("复盘文件"),
                file=file_html,
                repo=repo_block,
            )

    visible_count = 8
    primary_cards = "".join(render_card(review) for review in reviews[:visible_count])
    extra_cards = "".join(render_card(review) for review in reviews[visible_count:])
    return wrap_expandable_block(
        primary_cards,
        extra_cards,
        len(reviews) - visible_count,
        "篇复盘",
        "review-grid review-panel-grid content-more-grid",
        expanded_label="收起更多复盘",
        item_label_en="reviews",
        expanded_label_en="Collapse more reviews",
    )


def make_project_context_cards(items, language=None):
    language = current_language(language)
    if not items:
        return '<p class="empty">{}</p>'.format(
            escape(localized("暂无可归纳的项目上下文。", "No project context available.", language))
        )

    def render_keyword_chips(keywords):
        if not keywords:
            return '<span class="context-chip is-muted">{}</span>'.format(
                escape(localized("暂无关键词", "No keywords", language))
            )
        return "".join(
            '<span class="context-chip">{}</span>'.format(
                escape(localized_context_keyword(keyword, language=language))
            )
            for keyword in keywords[:4]
        )

    def render_fact(title, body):
        return """
            <div class="context-card-fact">
              <dt>{title}</dt>
              <dd>{body}</dd>
            </div>
            """.format(
            title=escape(title),
            body=escape(body),
        )

    def render_topic(topic):
        keyword_chips = render_keyword_chips(topic.get("keywords", []))
        return """
            <article class="context-topic">
              <div class="context-topic-head">
                <div>
                  <div class="context-topic-meta">{recent_activity} {latest_activity}</div>
                  <h4>{label}</h4>
                </div>
                <span class="context-topic-count">{window_count_label}</span>
              </div>
              <p>{question}</p>
              <p class="context-topic-takeaway">{takeaway}</p>
              <div class="context-chip-row">{keyword_chips}</div>
            </article>
            """.format(
            recent_activity=escape(localized("最近活动", "Recent activity", language)),
                latest_activity=escape(topic.get("latest_activity_display", localized("时间未知", "Unknown time", language))),
                label=escape(topic.get("label", "")),
                window_count_label=escape(
                    localized(
                        "{} 窗口".format(topic.get("window_count", 0)),
                        plural_en(topic.get("window_count", 0), "window"),
                        language,
                    )
                ),
            question=escape(topic.get("question_preview", localized("暂无代表问题。", "No representative question.", language))),
            takeaway=escape(topic.get("takeaway_preview", localized("暂无代表结论。", "No representative conclusion.", language))),
            keyword_chips=keyword_chips,
        )

    def render_topics(topics):
        if not topics:
            return ""
        visible_topics = topics[:PROJECT_CONTEXT_TOPIC_VISIBLE_COUNT]
        hidden_topics = topics[len(visible_topics):]
        topic_list_html = '<div class="context-topic-list">{}</div>'.format(
            "".join(render_topic(topic) for topic in visible_topics)
        )
        if hidden_topics:
            topic_list_html = wrap_expandable_block(
                topic_list_html,
                "".join(render_topic(topic) for topic in hidden_topics),
                len(hidden_topics),
                localized("个主题", "topics", language),
                "context-topic-list context-topic-more-list content-more-grid",
                expanded_label=localized("收起更多主题", "Collapse more topics", language),
                item_label_en="topics",
                expanded_label_en="Collapse more topics",
            )
        return """
              <div class="context-topic-block">
                <div class="context-card-kicker">{topic_label}</div>
                {topics}
              </div>
            """.format(
            topics=topic_list_html,
            topic_label=escape(localized("需求 / 主题", "Need / Topic", language)),
        )

    def render_card(item):
        return """
            <article class="context-card">
              <div class="context-card-head">
                <div class="context-card-copy">
                  <div class="context-card-meta">{recent_activity} {latest_activity}</div>
                  <h3>{label}</h3>
                </div>
                <div class="context-card-stats">
                  <span class="context-badge">{window_count_label}</span>
                  <span class="context-badge">{question_count_label}</span>
                  <span class="context-badge">{conclusion_count_label}</span>
                </div>
              </div>
              <dl class="context-card-facts">
                {facts}
              </dl>
              <div class="context-card-tags">
                <span class="context-card-kicker">{keywords_label}</span>
                <div class="context-chip-row">{keyword_chips}</div>
              </div>
              {topics}
            </article>
            """.format(
                recent_activity=escape(localized("最近活动", "Recent activity", language)),
                latest_activity=escape(item.get("latest_activity_display", localized("时间未知", "Unknown time", language))),
                label=escape(item.get("label", "")),
                window_count_label=escape(localized("{} 个窗口".format(item.get("window_count", 0)), plural_en(item.get("window_count", 0), "window"), language)),
                question_count_label=escape(localized("{} 个问题".format(item.get("question_count", 0)), plural_en(item.get("question_count", 0), "question"), language)),
                conclusion_count_label=escape(localized("{} 个结论".format(item.get("conclusion_count", 0)), plural_en(item.get("conclusion_count", 0), "conclusion"), language)),
                facts="".join(
                    (
                        render_fact(localized("最近工作区", "Recent Workspace", language), item.get("cwd_preview", localized("暂无工作目录", "No working directory", language))),
                        render_fact(localized("代表问题", "Representative Question", language), item.get("question_preview", localized("暂无代表问题。", "No representative question.", language))),
                        render_fact(localized("最近结论", "Recent Takeaway", language), item.get("takeaway_preview", localized("暂无代表结论。", "No representative conclusion.", language))),
                    )
                ),
                keywords_label=escape(localized("关键词", "Keywords", language)),
                keyword_chips=render_keyword_chips(item.get("keywords", [])),
                topics=render_topics(item.get("topics", [])),
            )

    visible_count = PROJECT_CONTEXT_VISIBLE_COUNT
    primary_cards = "".join(render_card(item) for item in items[:visible_count])
    extra_cards = "".join(render_card(item) for item in items[visible_count:])
    return wrap_expandable_block(
        primary_cards,
        extra_cards,
        len(items) - visible_count,
        localized("组上下文", "contexts", language),
        "project-context-list content-more-grid",
        expanded_label=localized("收起更多上下文", "Collapse more contexts", language),
    )


def make_project_context_body(project_context_views, default_days=PROJECT_CONTEXT_DEFAULT_DAYS, language=None):
    language = current_language(language)
    if not project_context_views:
        return '<p class="empty">{}</p>'.format(
            escape(localized("暂无可归纳的项目上下文。", "No project context available.", language))
        )

    ordered_days = sorted(
        (safe_int(days) for days in project_context_views.keys()),
        key=lambda value: value,
    )
    ordered_days = [days for days in ordered_days if days > 0]
    default_days = safe_int(default_days) or PROJECT_CONTEXT_DEFAULT_DAYS
    if str(default_days) not in project_context_views and ordered_days:
        default_days = ordered_days[0]

    controls = "".join(
        """
        <button class="context-range-button{active}" type="button" data-context-days="{days}" aria-pressed="{pressed}">
          {label}
        </button>
            """.format(
                days=escape(str(days)),
                label=escape(localized("最近 {} 天".format(days), "Last {}".format(plural_en(days, "day")), language)),
                active=" is-active" if days == default_days else "",
                pressed="true" if days == default_days else "false",
            )
        for days in ordered_days
    )

    views = []
    for days in ordered_days:
        view = project_context_views.get(str(days), {})
        source_dates = view.get("source_dates", [])
        source_joiner = ", " if is_english(language) else "、"
        source_label = source_joiner.join(source_dates[:3])
        if len(source_dates) > 3:
            source_label = localized(
                "{} 等 {} 天".format(source_label, len(source_dates)),
                "{}, and {}".format(source_label, plural_en(len(source_dates), "source date")),
                language,
            )
        if not source_label:
            source_label = localized("暂无有窗口日期", "No source dates", language)
        view_meta = localized(
            "扫描 {} 天 · 有窗口日期 {} 天 · {} 个窗口 · {}".format(
                view.get("scanned_date_count", days),
                view.get("source_date_count", 0),
                view.get("window_count", 0),
                source_label,
            ),
            "Scanned {} · {} · {} · {}".format(
                plural_en(view.get("scanned_date_count", days), "day"),
                plural_en(view.get("source_date_count", 0), "source date"),
                plural_en(view.get("window_count", 0), "window"),
                source_label,
            ),
            language,
        )
        views.append(
            """
            <div class="project-context-view{active}" data-context-view="{days}"{hidden}>
              <div class="project-context-view-meta">{view_meta}</div>
              <div class="project-context-list">
                {cards}
              </div>
            </div>
            """.format(
                active=" is-active" if days == default_days else "",
                days=escape(str(days)),
                hidden="" if days == default_days else " hidden",
                view_meta=escape(view_meta),
                cards=make_project_context_cards(view.get("project_contexts", []), language=language),
            )
        )

    return """
      <div class="context-range-control" role="group" aria-label="{aria_label}">
        {controls}
      </div>
      <div class="project-context-views">
        {views}
      </div>
    """.format(
        controls=controls,
        views="".join(views),
        aria_label=escape(localized("项目上下文时间范围", "Project context date range", language), quote=True),
    )


def make_usage_rows(events, group_id="usage-rows"):
    if not events:
        return '<tr><td colspan="4" class="empty-cell">暂无复用记录。</td></tr>'

    def render_row(event, row_class="", group_id="", hidden_attr=""):
        row_class_attr = ' class="{}"'.format(escape(row_class)) if row_class else ""
        if group_id:
            row_class_attr = ' class="{}" data-expand-group="{}"{}'.format(
                escape(row_class),
                escape(group_id),
                hidden_attr,
            )
        task = event.get("display_task") or event.get("task", "")
        task_en = event.get("display_task_en") or ""
        if task and (not task_en or contains_cjk(task_en)):
            task_en = english_freeform_text(task, fallback_label="Usage task")
        return """
            <tr{row_class_attr}>
              <td>{date}</td>
              <td>{asset_id}</td>
              <td>{task}</td>
              <td>{minutes_saved}</td>
            </tr>
            """.format(
                date=escape(event.get("date", "")),
                asset_id=escape(event.get("asset_id", "")),
                task=panel_language_text_html(task, task_en),
                minutes_saved=escape(str(event.get("minutes_saved", 0))),
                row_class_attr=row_class_attr,
            )

    return make_table_expand_rows(
        events,
        render_row,
        10,
        4,
        "条记录",
        "收起更多记录",
        group_id,
    )


def make_term_cloud(rows):
    if not rows:
        return '<p class="empty">暂无摘要词。</p>'

    max_value = max(row["value"] for row in rows) or 1
    chips = []
    for row in rows:
        scale = 0.9 + (row["value"] / max_value) * 0.8
        label = normalize_brand_display_text(str(row["label"]))
        label_html = panel_language_text_html(label, english_summary_term_label(label))
        chips.append(
            """
            <span class="term-chip" style="font-size:{size}rem">
              {label}
              <em>{value}</em>
            </span>
            """.format(
                size="{:.2f}".format(scale),
                label=label_html,
                value=escape(str(row["value"])),
            )
        )
    return "".join(chips)


def make_summary_term_view_meta(view):
    registered_count = (
        safe_int(view.get("asset_count", 0))
        + safe_int(view.get("review_count", 0))
        + safe_int(view.get("usage_event_count", 0))
    )
    source_dates = view.get("source_dates", []) or []
    if source_dates:
        source_zh = "、".join(source_dates[:3])
        source_en = ", ".join(source_dates[:3])
        if len(source_dates) > 3:
            source_zh = "{} 等 {} 天".format(source_zh, len(source_dates))
            source_en = "{}, and {}".format(source_en, plural_en(len(source_dates), "source date"))
    else:
        source_zh = "暂无来源日期"
        source_en = "No source dates"
    meta_zh = "{} 个窗口 · {} 个整理 · {} 条登记记录 · {}".format(
        view.get("window_count", 0),
        view.get("nightly_count", 0),
        registered_count,
        source_zh,
    )
    meta_en = "{} · {} · {} · {}".format(
        plural_en(view.get("window_count", 0), "window"),
        plural_en(view.get("nightly_count", 0), "synthesis", "syntheses"),
        plural_en(registered_count, "registered record"),
        source_en,
    )
    return panel_language_text_html(meta_zh, meta_en)


def make_summary_term_cloud_views(summary_term_views, default_days=SUMMARY_TERM_DEFAULT_DAYS, language=None):
    views = summary_term_views or []
    if not views:
        return '<div class="term-cloud">{}</div>'.format(make_term_cloud([]))

    default_days = safe_int(default_days) or SUMMARY_TERM_DEFAULT_DAYS
    if all(safe_int(view.get("days", 0)) != default_days for view in views):
        default_days = safe_int(views[0].get("days", SUMMARY_TERM_DEFAULT_DAYS))

    controls = []
    view_html = []
    for view in views:
        days = safe_int(view.get("days", 0))
        is_active = days == default_days
        controls.append(
            """
            <button class="term-range-button{active}" type="button" data-term-days="{days}" aria-pressed="{pressed}">
              {label}
            </button>
            """.format(
                active=" is-active" if is_active else "",
                days=escape(str(days)),
                pressed="true" if is_active else "false",
                label=summary_term_range_label_html(days),
            )
        )
        view_html.append(
            """
            <div class="term-cloud-view{active}" data-term-view="{days}"{hidden}>
              <div class="term-cloud-meta">{meta}</div>
              <div class="term-cloud">
                {cloud}
              </div>
            </div>
            """.format(
                active=" is-active" if is_active else "",
                days=escape(str(days)),
                hidden="" if is_active else " hidden",
                meta=make_summary_term_view_meta(view),
                cloud=make_term_cloud(view.get("terms", [])),
            )
        )

    return """
      <div class="term-range-control" role="group" aria-label="{aria_label}">
        {controls}
      </div>
      <div class="term-cloud-views">
        {views}
      </div>
    """.format(
        aria_label=escape(panel_display_text("热词时间范围", language)),
        controls="".join(controls),
        views="".join(view_html),
    )


def make_highlight_list(items, token_highlight=""):
    rendered = []
    for item in items:
        item_attrs = ' id="token-highlight"' if token_highlight and item == token_highlight else ""
        rendered.append("<li{}>{}</li>".format(item_attrs, escape(item)))
    return "".join(rendered)


def make_language_switch(language=None):
    language = current_language(language)
    zh_active = language == "zh"
    en_active = language == "en"
    return """
        <div class="language-switch" role="group" aria-label="语言切换">
          <button class="language-option{zh_class}" type="button" data-language-option="zh" aria-pressed="{zh_pressed}">中文</button>
          <button class="language-option{en_class}" type="button" data-language-option="en" aria-pressed="{en_pressed}">EN</button>
        </div>
    """.format(
        zh_class=" is-active" if zh_active else "",
        en_class=" is-active" if en_active else "",
        zh_pressed="true" if zh_active else "false",
        en_pressed="true" if en_active else "false",
    )


def make_theme_switch():
    return """
        <div class="theme-switch" role="group" aria-label="配色切换">
          <button class="theme-option is-active" type="button" data-theme-option="system" aria-pressed="true">系统</button>
          <button class="theme-option" type="button" data-theme-option="light" aria-pressed="false">浅色</button>
          <button class="theme-option" type="button" data-theme-option="dark" aria-pressed="false">深色</button>
        </div>
    """


def make_side_nav():
    entries = [
        ("group", "运行视图", "Runtime View"),
        ("link", "overview-top", "总览", "Overview", "总览", "Overview"),
        ("link", "nightly-summary", "整理摘要", "Synthesis", "整理摘要", "Synthesis"),
        ("link", "token-section", "Token", "Token", "Token", "Token"),
        ("link", "project-context-section", "项目上下文", "Context", "项目上下文", "Context"),
        ("group", "记忆层", "Memory Layer"),
        ("link", "memory-section", "个人资产记忆", "Personal Asset Memory", "个人资产记忆", "Personal Asset Memory"),
        ("child", "personal-memory-durable-section", "长期记忆", "Long-term Memory", "个人资产-长期记忆", "Personal Asset - Long-term Memory"),
        ("child", "personal-memory-session-section", "短期工作记忆", "Short-term Work Memory", "个人资产-短期工作记忆", "Personal Asset - Short-term Work Memory"),
        ("child", "personal-memory-low-priority-section", "低优先级记忆", "Low-priority Memory", "个人资产-低优先级记忆", "Personal Asset - Low-priority Memory"),
        ("link", "codex-native-section", "Codex 原生记忆", "Codex Native Memory", "Codex 原生记忆", "Codex Native Memory"),
        ("child", "codex-native-topic-section", "主题项", "Topics", "Codex 原生记忆-主题项", "Codex Native Memory - Topics"),
        ("child", "codex-native-preference-section", "用户偏好", "User Preferences", "Codex 原生记忆-用户偏好", "Codex Native Memory - User Preferences"),
        ("child", "codex-native-tip-section", "通用 tips", "General Tips", "Codex 原生记忆-通用 tips", "Codex Native Memory - General Tips"),
        ("child", "codex-native-task-group-section", "任务组", "Task Groups", "Codex 原生记忆-任务组", "Codex Native Memory - Task Groups"),
        ("group", "资产层", "Asset Layer"),
        ("link", "asset-overview-section", "账本概览", "Ledger Overview", "资产注册表概览", "Asset Registry Overview"),
        ("link", "assets-section", "资产明细", "Assets", "资产明细", "Assets"),
        ("link", "top-assets-section", "复用价值", "Reuse Value", "复用价值", "Reuse Value"),
        ("link", "reviews-section", "复盘记录", "Reviews", "复盘记录", "Reviews"),
        ("link", "window-overview-section", "窗口明细", "Windows", "窗口明细", "Windows"),
    ]
    links = []
    link_index = 0
    for entry in entries:
        if entry[0] == "group":
            _, zh_label, en_label = entry
            links.append(
                """
                  <div class="side-nav-group">{label}</div>
                """.format(label=panel_language_text_html(zh_label, en_label))
            )
            continue
        entry_kind, target_id, zh_label, en_label, zh_title, en_title = entry
        link_index += 1
        links.append(
            """
              <a class="side-nav-link{active_class}" href="#{target_id}" data-nav-target="{target_id}" title="{title_attr}"{current_attr}>
                <span class="side-nav-label">{label}</span>
              </a>
            """.format(
                active_class=(" is-active" if link_index == 1 else "") + (" is-child" if entry_kind == "child" else ""),
                target_id=escape(target_id, quote=True),
                title_attr=escape("{} / {}".format(zh_title, en_title), quote=True),
                current_attr=' aria-current="true"' if link_index == 1 else "",
                label=panel_language_text_html(zh_label, en_label),
            )
        )

    return """
      <aside class="side-nav" aria-label="页面导览">
        <div class="side-nav-title">{title}</div>
        <nav class="side-nav-list" aria-label="页面导览">
          {links}
        </nav>
      </aside>
    """.format(
        title=panel_language_text_html("导览", "Guide"),
        links="".join(links),
    )


def make_personal_memory_token_widget(token_usage):
    token_usage = token_usage or {}
    if not token_usage.get("enabled"):
        return ""

    title_html = panel_language_text_html("Codex context 预算", "Codex Context Budget")
    value_html = panel_language_variant_html(
        escape(token_usage.get("value_display_zh") or token_usage.get("value_display", "")),
        escape(token_usage.get("value_display_en") or token_usage.get("value_display", "")),
    )
    status_html = panel_language_variant_html(
        escape(token_usage.get("status_label_zh") or token_usage.get("status_label", "")),
        escape(token_usage.get("status_label_en") or token_usage.get("status_label", "")),
    )
    caption_html = panel_language_text_html(
        token_usage.get("caption_zh") or token_usage.get("caption", ""),
        token_usage.get("caption_en", ""),
    )
    mode_html = panel_language_variant_html(
        escape(token_usage.get("mode_note_zh") or token_usage.get("mode_note", "")),
        escape(token_usage.get("mode_note_en") or token_usage.get("mode_note", "")),
    )
    method_note = token_usage.get("method_note_zh") or token_usage.get("method_note", "")
    meter_percent = max(0, min(100, safe_int(token_usage.get("meter_percent", 0))))
    return """
        <aside class="memory-token-widget" aria-label="{aria_label}" title="{title}">
          <div class="memory-token-topline">
            <div class="memory-token-label">{label}</div>
            <div class="memory-token-status">{status}</div>
          </div>
          <div class="memory-token-main">
            <div class="memory-token-value">{value}</div>
            <div class="memory-token-budget">
              <div class="memory-token-meter" aria-hidden="true">
                <div class="memory-token-meter-fill" style="width: {meter_percent}%"></div>
              </div>
              <div class="memory-token-caption">{caption}</div>
            </div>
          </div>
          <div class="memory-token-mode">{mode}</div>
        </aside>
    """.format(
        aria_label=escape(panel_display_text("Codex context 预算"), quote=True),
        title=escape(method_note, quote=True),
        label=title_html,
        status=status_html,
        value=value_html,
        meter_percent=meter_percent,
        caption=caption_html,
        mode=mode_html,
    )


def make_personal_memory_count_widget(memory_registry):
    rows = memory_registry or []
    counts = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        bucket = str(row.get("bucket") or "").strip()
        if bucket:
            counts[bucket] += 1

    total_memories = (
        counts.get("durable", 0)
        + counts.get("session", 0)
        + counts.get("low_priority", 0)
    )
    total_html = panel_language_variant_html(
        "共 {} 条".format(escape(str(total_memories))),
        "{} total".format(escape(str(total_memories))),
    )
    items = [
        ("总数", "Total", total_memories),
        ("长期", "Long-term", counts.get("durable", 0)),
        ("短期", "Short-term", counts.get("session", 0)),
        ("低优先", "Low-priority", counts.get("low_priority", 0)),
    ]
    cards = []
    for label_zh, label_en, value in items:
        cards.append(
            """
              <div class="memory-count-item">
                <span>{label}</span>
                <b>{value}</b>
              </div>
            """.format(
                label=panel_language_text_html(label_zh, label_en),
                value=escape(str(value)),
            )
        )

    return """
        <aside class="memory-count-widget" aria-label="{aria_label}">
          <div class="memory-count-topline">
            <div class="memory-count-label">{label}</div>
            <div class="memory-count-total">{total}</div>
          </div>
          <div class="memory-count-grid">
            {cards}
          </div>
        </aside>
    """.format(
        aria_label=escape(panel_display_text("记忆数量"), quote=True),
        label=panel_language_text_html("记忆数量", "Memory Counts"),
        total=total_html,
        cards="".join(cards),
    )


def make_memory_family_header(title_zh, title_en, note_zh, note_en, extra_html=""):
    extra_class = " has-extra" if extra_html else ""
    return """
      <div class="memory-family-head">
        <div class="memory-family-title-row{extra_class}">
          <div class="memory-family-title-copy">
            <p class="section-kicker">{kicker}</p>
            <h2>{title}</h2>
            <p class="memory-family-note">{note}</p>
          </div>
          {extra_html}
        </div>
      </div>
    """.format(
        extra_class=extra_class,
        kicker=panel_language_text_html("记忆", "Memory"),
        title=panel_language_text_html(title_zh, title_en),
        note=panel_language_text_html(note_zh, note_en),
        extra_html=extra_html,
    )


def make_memory_cards(items, include_bucket_meta=True):
    if not items:
        return '<p class="empty">暂无。</p>'

    def ui_text(value):
        return normalize_brand_display_text(value)

    def english_for_ui_text(value):
        return ui_text(panel_english_text(value) or value)

    def format_date_for_language(value, language):
        text = str(value or "")
        if language == "en":
            return english_for_ui_text(text)
        return text

    def has_known_date_display(value):
        text = str(value or "").strip()
        if not text:
            return False
        lowered = text.lower()
        unknown_markers = {
            "时间未知",
            "更新时间未知",
            "unknown time",
            "unknown date",
            "time unknown",
            "update time unknown",
            "generation time unknown",
        }
        return text not in unknown_markers and lowered not in unknown_markers and "未知" not in text

    def render_fact(title, body_html, en_title=""):
        return """
            <div class="memory-card-fact">
              <div class="memory-card-label">{title}</div>
              <div class="memory-card-value">{body}</div>
            </div>
            """.format(
            title=panel_language_text_html(title, en_title or english_for_ui_text(title)),
            body=body_html,
        )

    def render_submeta_lines(zh_lines, en_lines):
        def render_lines(lines):
            return "".join(
                '<span class="memory-card-submeta-line">{}</span>'.format(
                    escape(ui_text(line))
                )
                for line in lines
                if line
            )

        return panel_language_variant_html(
            render_lines(zh_lines),
            render_lines(en_lines),
        )

    def render_context_chips(labels):
        if not labels:
            return '<span class="memory-chip is-muted">{}</span>'.format(
                panel_language_text_html("未分类上下文")
            )
        chips = []
        for label in labels[:3]:
            label = ui_text(label)
            label_en = ui_text(localized_context_label(label, language="en"))
            chips.append(
                '<span class="memory-chip">{}</span>'.format(
                    panel_language_text_html(label, label_en)
                    if label_en and label_en != label
                    else escape(label)
                )
            )
        return "".join(chips)

    def render_source_window_links(source_windows):
        if not source_windows:
            return '<span class="memory-chip is-muted">{}</span>'.format(
                panel_language_text_html("暂无来源窗口")
            )

        links = []
        for ref in source_windows[:3]:
            if ref.get("display_index"):
                label = "{} · 窗口 {}".format(
                    ui_text(ref.get("project_label", "工作区")),
                    ref.get("display_index"),
                )
            else:
                label = "{} · {}".format(
                    ui_text(ref.get("project_label", "工作区")),
                    ref.get("window_id_short", "窗口"),
                )
            if ref.get("anchor_id"):
                links.append(
                    render_jump_link(
                        ref.get("anchor_id", ""),
                        label,
                        class_name="memory-chip memory-chip-link",
                    )
                )
            elif ref.get("raw_path"):
                links.append(
                    build_local_path_anchor(
                        ref.get("raw_path", ""),
                        label,
                        class_name="memory-chip memory-chip-link",
                    )
                )
            else:
                links.append('<span class="memory-chip is-muted">{}</span>'.format(escape(label)))
        return "".join(links)

    def render_source_file_links(source_files):
        if not source_files:
            return '<span class="memory-chip is-muted">{}</span>'.format(
                panel_language_text_html("暂无来源文件")
            )
        links = []
        for item in source_files[:3]:
            if item.get("status") in {"missing", "unreadable"}:
                raw_label = ui_text(item.get("label", item.get("path", "")))
                en_label = (
                    panel_english_text(raw_label)
                    or str(raw_label).replace("无法读取", "unreadable").replace("未检测到", "not found")
                )
                en_label = ui_text(en_label)
                links.append(
                    '<span class="memory-chip is-muted" title="{title}">{label}</span>'.format(
                        title=escape(item.get("path", ""), quote=True),
                        label=panel_language_text_html(raw_label, en_label),
                    )
                )
                continue
            links.append(
                build_local_path_anchor(
                    item.get("path", ""),
                    ui_text(item.get("label", item.get("path", ""))),
                    class_name="memory-chip memory-chip-link",
                )
            )
        return "".join(links)

    def render_cwd_links(source_windows):
        links = []
        seen = set()
        for ref in source_windows:
            cwd = ref.get("cwd", "")
            if not cwd or cwd in seen:
                continue
            seen.add(cwd)
            links.append(
                build_local_path_anchor(
                    cwd,
                    ui_text(ref.get("cwd_display", cwd)),
                    class_name="memory-chip memory-chip-link",
                )
            )
            if len(links) >= 2:
                break
        if not links:
            return '<span class="memory-chip is-muted">{}</span>'.format(
                panel_language_text_html("暂无工作区")
            )
        return "".join(links)

    def render_card(item):
        context_labels = item.get("context_labels", [])
        if not context_labels and item.get("display_context"):
            context_labels = [item.get("display_context")]
        meta_parts = []
        if include_bucket_meta:
            meta_parts.append(
                ui_text(item.get("display_bucket") or display_memory_bucket(item.get("bucket", "")))
            )
        meta_parts.extend(
            [
                ui_text(item.get("display_memory_type") or display_memory_type(item.get("memory_type", ""))),
                ui_text(item.get("display_priority") or display_memory_priority(item.get("priority", ""))),
            ]
        )
        meta_parts = [part for part in meta_parts if part]
        meta_parts_en = [english_for_ui_text(part) for part in meta_parts]
        created_display = item.get("created_at_display") or display_memory_date(item.get("created_at", ""))
        updated_display = item.get("updated_at_display") or display_memory_date(item.get("updated_at", ""))
        submeta_parts_zh = []
        submeta_parts_en = []
        if has_known_date_display(created_display):
            submeta_parts_zh.append(
                "首次添加 {}".format(format_date_for_language(created_display, "zh"))
            )
            submeta_parts_en.append(
                "First added {}".format(format_date_for_language(created_display, "en"))
            )
        if has_known_date_display(updated_display):
            submeta_parts_zh.append(
                "最近更新 {}".format(format_date_for_language(updated_display, "zh"))
            )
            submeta_parts_en.append(
                "Updated {}".format(format_date_for_language(updated_display, "en"))
            )
        if item.get("usage_frequency_display"):
            window_days = item.get("usage_frequency_window_days", MEMORY_USAGE_WINDOW_DAYS)
            frequency_value = item.get("usage_frequency_display", "0")
            submeta_parts_zh.append("{}日频率 {}".format(window_days, frequency_value))
            submeta_parts_en.append("{}-day frequency {}".format(window_days, frequency_value))
        if item.get("occurrence_count", 0) > 1:
            occurrence_label = ui_text(item.get("occurrence_label", "整理命中"))
            occurrence_label_en = english_for_ui_text(occurrence_label)
            submeta_parts_zh.append(
                "{} {} 次".format(
                    occurrence_label,
                    item.get("occurrence_count", 0),
                )
            )
            submeta_parts_en.append("{} {} times".format(occurrence_label_en, item.get("occurrence_count", 0)))
        if item.get("submeta_zh") or item.get("submeta_en"):
            submeta_html = panel_language_text_html(
                ui_text(item.get("submeta_zh", "")),
                ui_text(item.get("submeta_en", "")),
            )
        else:
            submeta_html = render_submeta_lines(submeta_parts_zh, submeta_parts_en)
        submeta_block = (
            '<div class="review-submeta memory-card-submeta">{}</div>'.format(submeta_html)
            if submeta_html
            else ""
        )

        source_fact_label = ui_text(item.get("source_fact_label", "来源窗口"))
        source_fact_label_en = english_for_ui_text(source_fact_label)
        facts = "".join(
            (
                render_fact("关联上下文", render_context_chips(context_labels), "Related Context"),
                render_fact("最近工作区", render_cwd_links(item.get("source_windows", [])), "Recent Workspace"),
                render_fact(
                    source_fact_label,
                    render_source_file_links(item.get("source_files", []))
                    if item.get("source_files")
                    else render_source_window_links(item.get("source_windows", [])),
                    source_fact_label_en,
                ),
            )
        )
        display_value_note = ui_text(item.get("display_value_note") or item.get("value_note", ""))
        raw_value_note = (
            item.get("display_value_note_en")
            or item.get("value_note_en")
            or item.get("value_note", "")
        )
        raw_value_note = ui_text(raw_value_note)
        value_note_html = panel_language_variant_html(
            linkify_local_paths_html(display_value_note),
            linkify_local_paths_html(raw_value_note) if raw_value_note != display_value_note else "",
        )
        display_title = ui_text(item.get("display_title") or item.get("title", ""))
        raw_title = ui_text(item.get("title", ""))
        title_html = panel_language_text_html(display_title, raw_title if raw_title != display_title else "")
        card_class = "review-card memory-card"
        return """
            <article class="{card_class}">
              <div class="review-meta">{meta}</div>
              {submeta_block}
              <h3>{title}</h3>
              <p>{value_note}</p>
              <div class="memory-card-facts">
                {facts}
              </div>
            </article>
            """.format(
            card_class=escape(card_class, quote=True),
            meta=panel_language_variant_html(
                escape(" · ".join(meta_parts)),
                escape(" · ".join(meta_parts_en)) if meta_parts_en != meta_parts else "",
            ),
            submeta_block=submeta_block,
            title=title_html or panel_language_text_html("未命名记忆"),
            value_note=value_note_html,
            facts=facts,
        )

    primary_cards = "".join(render_card(item) for item in items[:8])
    extra_cards = "".join(render_card(item) for item in items[8:])
    return wrap_expandable_block(
        primary_cards,
        extra_cards,
        len(items) - 8,
        "条",
        "review-grid memory-grid content-more-grid",
        expanded_label="收起额外条目",
        item_label_en="items",
        expanded_label_en="Collapse extra items",
    )


def make_memory_type_grouped_cards(items, include_bucket_meta=False):
    if not items:
        return '<p class="empty">暂无。</p>'

    grouped = defaultdict(list)
    for item in items:
        memory_type = str(item.get("memory_type") or "").strip()
        if not memory_type:
            memory_type = str(item.get("display_memory_type") or "").strip() or "uncategorized"
        grouped[memory_type].append(item)

    order_index = {value: index for index, value in enumerate(MEMORY_TYPE_GROUP_ORDER)}

    def group_sort_key(item):
        memory_type, rows = item
        best_usage = max(
            (safe_float(row.get("usage_frequency_sort_key", row.get("usage_frequency", 0))) for row in rows),
            default=0.0,
        )
        return (
            order_index.get(memory_type, len(order_index)),
            -best_usage,
            display_memory_type(memory_type),
        )

    sections = []
    for memory_type, rows in sorted(grouped.items(), key=group_sort_key):
        sorted_rows = sort_memory_rows_by_usage(rows)
        title_html = panel_language_text_html(
            display_memory_type(memory_type),
            display_memory_type(memory_type, language="en"),
        )
        count_html = panel_language_text_html(
            "{} 条".format(len(sorted_rows)),
            "{} {}".format(len(sorted_rows), "item" if len(sorted_rows) == 1 else "items"),
        )
        sections.append(
            """
            <section class="memory-type-group">
              <div class="memory-type-head">
                <h3>{title}</h3>
                <span>{count}</span>
              </div>
              <div class="review-grid memory-grid">
                {cards}
              </div>
            </section>
            """.format(
                title=title_html,
                count=count_html,
                cards=make_memory_cards(sorted_rows, include_bucket_meta=include_bucket_meta),
            )
        )
    return "".join(sections)


def native_meta_to_chinese(meta_text):
    return (
        str(meta_text or "")
        .replace("User preferences", "用户偏好")
        .replace("User Preferences", "用户偏好")
        .replace("General Tips", "通用 tips")
        .replace("Task Groups", "任务组")
    )


def native_meta_to_english(meta_text):
    meta_text = str(meta_text or "")
    explicit = panel_english_text(meta_text)
    if explicit:
        return explicit

    def replace_count(pattern, singular, plural, text):
        def repl(match):
            count = int(match.group(1))
            noun = singular if count == 1 else plural
            return "{} {}".format(count, noun)

        return re.sub(pattern, repl, text)

    translated = meta_text.replace("Codex 原生", "Codex Native")
    translated = translated.replace("用户偏好", "User Preferences")
    translated = translated.replace("通用 tips", "General Tips")
    translated = translated.replace("任务组", "Task Groups")
    translated = replace_count(r"(\d+)\s*个任务组", "task group", "task groups", translated)
    translated = replace_count(r"(\d+)\s*个任务", "task", "tasks", translated)
    translated = replace_count(r"(\d+)\s*个来源", "source", "sources", translated)
    translated = translated.replace("关键词", "keywords")
    translated = translated.replace("；", "; ")
    return translated


def make_codex_native_brief_memory_items(rows, kind, language=None):
    language = current_language(language)
    rows = rows or []
    kind_config = {
        "preference": {
            "memory_type": "preference",
            "display_memory_type": localized("偏好", "Preference", language),
            "default_submeta": localized("Codex 原生 · 用户偏好", "Codex Native · User Preferences", language),
            "title_prefix_zh": "偏好",
            "title_prefix_en": "Preference",
        },
        "tip": {
            "memory_type": "tip",
            "display_memory_type": localized("通用 tips", "General Tips", language),
            "default_submeta": localized("Codex 原生 · 通用 tips", "Codex Native · General Tips", language),
            "title_prefix_zh": "通用 tips",
            "title_prefix_en": "General tip",
        },
        "task_group": {
            "memory_type": "task",
            "display_memory_type": localized("任务组", "Task Group", language),
            "default_submeta": localized("Codex 原生 · MEMORY.md 任务组", "Codex Native · MEMORY.md task group", language),
            "title_prefix_zh": "任务组",
            "title_prefix_en": "Task group",
        },
    }.get(kind, {})

    items = []
    for index, row in enumerate(rows, start=1):
        display_title = normalize_brand_display_text(row.get("display_title") or row.get("title") or "{} {}".format(
            kind_config.get("title_prefix_zh", "条目"),
            index,
        ))
        raw_title = normalize_brand_display_text(row.get("title") or "")
        if not raw_title or raw_title == display_title:
            raw_title = "{} {}".format(kind_config.get("title_prefix_en", "Item"), index)
        raw_title = normalize_brand_display_text(raw_title)
        display_body = normalize_brand_display_text(row.get("display_body") or row.get("body") or row.get("scope", ""))
        body_en = normalize_brand_display_text(
            row.get("display_body_en") or row.get("body") or row.get("scope", "") or display_body
        )
        if row.get("keywords"):
            keywords = [normalize_brand_display_text(keyword) for keyword in row.get("keywords", [])[:5]]
            keyword_text = "、".join(keywords)
            display_body = "{}；关键词：{}".format(display_body, keyword_text) if display_body else "关键词：{}".format(keyword_text)
            body_en = "{}; keywords: {}".format(body_en, ", ".join(keywords)) if body_en else "Keywords: {}".format(", ".join(keywords))
        source_files = row.get("source_files") or [
            {
                "path": "",
                "label": "MEMORY.md" if kind == "task_group" else "memory_summary.md",
                "status": "missing",
            }
        ]

        submeta_zh = native_meta_to_chinese(
            row.get("meta") or kind_config.get("default_submeta", "Codex 原生")
        )
        submeta_zh = normalize_brand_display_text(submeta_zh)
        submeta_en = native_meta_to_english(submeta_zh)
        if submeta_en == submeta_zh:
            submeta_en = kind_config.get("default_submeta", submeta_zh)
        submeta_en = normalize_brand_display_text(submeta_en)

        items.append(
            {
                "bucket": "native",
                "display_bucket": localized("Codex 原生", "Codex Native", language),
                "memory_type": kind_config.get("memory_type", kind),
                "display_memory_type": kind_config.get("display_memory_type", kind),
                "priority": "medium",
                "display_priority": localized("中优先", "Medium Priority", language),
                "title": raw_title,
                "display_title": display_title,
                "value_note": body_en,
                "value_note_en": body_en,
                "display_value_note": display_body,
                "display_value_note_en": body_en,
                "submeta_zh": submeta_zh,
                "submeta_en": submeta_en,
                "display_context": localized("Codex 原生记忆", "Codex Native Memory", language),
                "context_labels": [localized("Codex 原生记忆", "Codex Native Memory", language)],
                "source_fact_label": localized("来源文件", "Source file", language),
                "source_files": source_files,
                "source_windows": [],
            }
        )
    return items


def derive_nightly_window_title(nightly_title):
    if nightly_title == "昨夜整理结果":
        return "昨夜窗口概览"
    if nightly_title == "当日整理预览":
        return "当日窗口概览"
    return "最近一次窗口概览"


def split_nightly_summary(text, max_parts=6):
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    return [
        part.strip("；。 ")
        for part in re.split(r"[；。]\s*", normalized)
        if part.strip("；。 ")
    ][:max_parts]


def contains_cjk(text):
    return bool(re.search(r"[\u3400-\u9fff]", str(text or "")))


def english_count_phrase(count, singular, plural=""):
    count = safe_int(count)
    return "{} {}".format(count, singular if count == 1 else (plural or "{}s".format(singular)))


def extract_english_summary_terms(text, limit=6):
    terms = []
    seen = set()
    stop_terms = {
        "and",
        "for",
        "from",
        "the",
        "with",
        "today",
        "summary",
        "synthesis",
    }

    def add_term(value):
        term = re.sub(r"\s+", " ", str(value or "")).strip(" ,.;:，。；：")
        if not term:
            return
        lowered = term.lower()
        if lowered in stop_terms or lowered in seen:
            return
        seen.add(lowered)
        terms.append(term)

    for match in re.findall(r"`([^`]{2,80})`", str(text or "")):
        add_term(match)
        if len(terms) >= limit:
            return terms

    for match in re.findall(r"\b[A-Za-z][A-Za-z0-9_./:-]{2,}\b", str(text or "")):
        add_term(match)
        if len(terms) >= limit:
            break
    return terms[:limit]


def build_daily_summary_english_parts(nightly, summary_text, window_count, context_labels_en):
    if not nightly:
        return ["No synthesis has been generated yet."]

    explicit_summary = str(
        nightly.get("day_summary_en")
        or nightly.get("summary_en")
        or nightly.get("display_day_summary_en")
        or ""
    ).strip()
    if explicit_summary:
        return split_nightly_summary(explicit_summary)
    if summary_text and not contains_cjk(summary_text):
        return split_nightly_summary(summary_text)

    date_text = nightly.get("date") or "Selected date"
    durable_count = len(nightly.get("durable_memories", []))
    session_count = len(nightly.get("session_memories", []))
    low_priority_count = len(nightly.get("low_priority_memories", []))
    memory_phrase = ", ".join(
        [
            english_count_phrase(durable_count, "long-term memory", "long-term memories"),
            english_count_phrase(session_count, "short-term follow-up", "short-term follow-ups"),
            english_count_phrase(low_priority_count, "low-priority item", "low-priority items"),
        ]
    )
    lead = "{} synthesis captured {} and {}.".format(
        date_text,
        english_count_phrase(window_count, "work window", "work windows"),
        memory_phrase,
    )
    focus_terms = extract_english_summary_terms(summary_text)
    if focus_terms:
        lead = "{} Main focus: {}.".format(lead, ", ".join(focus_terms[:4]))

    details = []
    stage = nightly.get("stage", "")
    if stage:
        details.append("Stage: {}.".format(stage_display_label(stage, language="en")))
    if context_labels_en:
        details.append("Related contexts: {}.".format(", ".join(context_labels_en[:4])))
    if focus_terms:
        details.append("Source terms: {}.".format(", ".join(focus_terms)))
    return [lead] + details


def stage_display_label(stage, language=None):
    if is_english(language):
        return {"final": "Final", "preliminary": "Preview", "manual": "Manual"}.get(stage, stage)
    return {"final": "终版", "preliminary": "预览", "manual": "手动"}.get(stage, stage)


def build_daily_summary_view(nightly, window_overview=None, project_contexts=None, language=None):
    language = current_language(language)
    nightly = nightly or {}
    summary_text_zh = normalize_brand_display_text(
        re.sub(r"\s+", " ", str(nightly.get("day_summary", "") or "")).strip()
    )
    if not summary_text_zh:
        summary_text_zh = "夜间整理结果尚未生成。"
    summary_parts_zh = [
        normalize_brand_display_text(item)
        for item in split_nightly_summary(summary_text_zh)
    ]
    lead_text_zh = summary_parts_zh[0] if summary_parts_zh else summary_text_zh
    window_overview = window_overview or {}
    project_contexts = project_contexts or []

    nightly_window_count = nightly.get(
        "raw_window_count",
        window_overview.get("window_count", len(nightly.get("window_summaries", []))),
    )
    context_labels_raw = [
        normalize_brand_display_text(item.get("label", localized_context_label("未分类上下文", language)))
        for item in project_contexts[:4]
    ]
    context_labels_zh = [
        localized_context_label(label, language="zh")
        for label in context_labels_raw
    ]
    context_labels_en = [
        localized_context_label(label, language="en")
        for label in context_labels_raw
    ]
    summary_parts_en = build_daily_summary_english_parts(
        nightly,
        summary_text_zh,
        nightly_window_count,
        context_labels_en,
    )
    summary_parts_en = [normalize_brand_display_text(item) for item in summary_parts_en]
    lead_text_en = summary_parts_en[0] if summary_parts_en else "No synthesis has been generated yet."
    lead_text = localized(lead_text_zh, lead_text_en, language)
    detail_parts = localized(summary_parts_zh[1:], summary_parts_en[1:], language)
    stats = [
        {"label": localized("工作窗口", "Work Windows", language), "value": nightly_window_count},
        {
            "label": localized(
                "长期记忆",
                "Long-term Memory",
                language,
            ),
            "value": len(nightly.get("durable_memories", [])),
        },
        {
            "label": localized(
                "短期跟进",
                "Short-term Follow-ups",
                language,
            ),
            "value": len(nightly.get("session_memories", [])),
        },
        {
            "label": localized(
                "低优先级",
                "Low-priority Memory",
                language,
            ),
            "value": len(nightly.get("low_priority_memories", [])),
        },
    ]

    note_text_zh = "这些数字来自当前整理结果，用来快速判断今天沉淀了多少内容。"
    note_text_en = "These numbers come from the selected synthesis and help estimate how much was captured that day."
    if not nightly:
        note_text_zh = "当前还没有最近一次整理；生成后这里会自动切成摘要卡。"
        note_text_en = "No recent synthesis yet; this area will switch to a summary card after generation."
    elif not any(safe_int(item.get("value", 0)) for item in stats[1:]):
        note_text_zh = "还没有沉淀出记忆条目，先用窗口级概览帮助回看当天上下文。"
        note_text_en = "No memory items were captured yet; use the window overview to review that day's context."
    note_text = localized(note_text_zh, note_text_en, language)

    stage = nightly.get("stage", "")
    badges = []
    if stage == "preliminary":
        badges.append({"label": localized("预览", "Preview", language), "tone": "amber"})
    elif not nightly:
        badges.append({"label": localized("待生成", "Pending", language), "tone": "slate"})
    if "失败" in summary_text_zh or "保底" in summary_text_zh:
        badges.append({"label": localized("保底摘要", "Fallback summary", language), "tone": "rose"})

    return {
        "available": bool(nightly),
        "date": nightly.get("date", ""),
        "stage": stage,
        "stage_label": stage_display_label(stage, language=language),
        "lead_text": lead_text,
        "lead_text_zh": lead_text_zh,
        "lead_text_en": lead_text_en,
        "detail_parts": detail_parts,
        "detail_parts_zh": summary_parts_zh[1:],
        "detail_parts_en": summary_parts_en[1:],
        "context_labels": localized(
            context_labels_zh,
            context_labels_en,
            language,
        ),
        "context_labels_zh": context_labels_zh,
        "context_labels_en": context_labels_en,
        "stats": stats,
        "note_text": note_text,
        "note_text_zh": note_text_zh,
        "note_text_en": note_text_en,
        "badges": badges,
    }


def build_daily_summary_views(candidates, language=None):
    language = current_language(language)
    by_date = {}
    for payload in candidates:
        parsed = parse_nightly_summary_date(payload)
        if parsed is None:
            continue
        date_str = parsed.isoformat()
        current = by_date.get(date_str)
        if current is None or daily_nightly_sort_key(payload) > daily_nightly_sort_key(current):
            by_date[date_str] = payload

    views = []
    for date_str in sorted(by_date.keys(), reverse=True):
        payload = by_date[date_str]
        window_overview = build_window_overview(payload, language=language)
        project_contexts = build_project_contexts(window_overview, language=language) if window_overview else []
        views.append(
            build_daily_summary_view(
                payload,
                window_overview=window_overview,
                project_contexts=project_contexts,
                language=language,
            )
        )
    return views


def make_date_select_control(control_id, aria_label, dates, selected_date, date_status=None):
    dates = [date for date in dates if date]
    selected_date = selected_date or (dates[0] if dates else "")
    date_status = date_status or {}

    def display_date(date_str):
        label = str(date_str or "").replace("-", "/")
        status = date_status.get(date_str, "")
        if status:
            label = "{} · {}".format(label, status)
        return label

    options = "".join(
        '<option value="{date}"{selected}>{label}</option>'.format(
            date=escape(date),
            selected=" selected" if date == selected_date else "",
            label=escape(display_date(date)),
        )
        for date in dates
    )
    disabled = " disabled" if not dates else ""
    return """
      <label class="nightly-date-control" for="{control_id}">
        <span class="nightly-date-label">日期</span>
        <select
          class="nightly-date-input"
          id="{control_id}"
          aria-label="{aria_label}"
          {disabled}
        >
          {options}
        </select>
      </label>
    """.format(
        control_id=escape(control_id, quote=True),
        aria_label=escape(aria_label, quote=True),
        disabled=disabled,
        options=options,
    )


def make_daily_summary_date_control(summary_views, selected_date, selectable_dates=None, missing_dates=None):
    dates = selectable_dates or [view.get("date", "") for view in summary_views if view.get("date")]
    missing_dates = set(missing_dates or [])
    return make_date_select_control(
        "nightly-date-input",
        "选择整理日期",
        dates,
        selected_date,
        date_status={
            date: "未整理"
            for date in missing_dates
        },
    )


def make_window_overview_date_control(window_views, selected_date):
    dates = [view.get("date", "") for view in window_views if view.get("date")]
    return make_date_select_control(
        "window-overview-date-input",
        "选择窗口日期",
        dates,
        selected_date,
    )


def make_nightly_summary_panel(
    nightly_title,
    nightly_note,
    active_nightly_note,
    nightly,
    window_overview,
    project_contexts,
    help_html="",
    summary_views=None,
    selected_date="",
    selectable_dates=None,
    backfill=None,
):
    nightly = nightly or {}
    summary_views = summary_views or []
    backfill = backfill or {}
    selected_date = selected_date or nightly.get("date", "")
    current_view = build_daily_summary_view(
        nightly,
        window_overview=window_overview,
        project_contexts=project_contexts,
    )
    if summary_views:
        matched_view = next(
            (view for view in summary_views if view.get("date") == selected_date),
            None,
        )
        if matched_view:
            current_view = matched_view
        elif selected_date:
            current_view = build_daily_summary_view(
                {},
                window_overview=window_overview,
                project_contexts=project_contexts,
            )
            current_view["date"] = selected_date

    badges = []
    for badge in current_view.get("badges", []):
        badges.append(
            '<span class="nightly-badge is-{}">{}</span>'.format(
                escape(badge.get("tone", "slate")),
                escape(badge.get("label", "")),
            )
        )
    badge_row_html = """
        <div class="nightly-badge-row" id="nightly-badge-row"{hidden}>
          {badges}
        </div>
    """.format(
        hidden=" hidden" if not badges else "",
        badges="".join(badges),
    )

    stat_cards = "".join(
        """
        <article class="nightly-stat-card">
          <div class="nightly-stat-label">{label}</div>
          <div class="nightly-stat-value">{value}</div>
        </article>
        """.format(
            label=escape(item.get("label", "")),
            value=escape(str(item.get("value", ""))),
        )
        for item in current_view.get("stats", [])
    )

    detail_items = "".join(
        '<li class="nightly-detail-item">{}</li>'.format(escape(item))
        for item in current_view.get("detail_parts", [])
    )
    detail_list = """
        <ul class="nightly-detail-list" id="nightly-detail-list"{hidden}>
          {items}
        </ul>
    """.format(
        hidden=" hidden" if not detail_items else "",
        items=detail_items,
    )

    context_chips = "".join(
        '<span class="nightly-context-chip">{}</span>'.format(escape(label))
        for label in current_view.get("context_labels", [])
    )
    context_block = """
        <div class="nightly-context-block" id="nightly-context-block"{hidden}>
          <div class="nightly-context-label">相关上下文</div>
          <div class="nightly-context-row" id="nightly-context-row">
            {chips}
          </div>
        </div>
    """.format(
        hidden=" hidden" if not context_chips else "",
        chips=context_chips,
    )

    note_html = ""
    if not nightly and nightly_note:
        note_html = '<p class="nightly-note">{}</p>'.format(escape(nightly_note))
    date_control = make_daily_summary_date_control(
        summary_views,
        selected_date,
        selectable_dates=selectable_dates,
        missing_dates=backfill.get("missing_dates", []),
    )
    backfill_panel_hidden = " hidden"
    selected_missing = selected_date in set(backfill.get("missing_dates", []))
    if selected_missing and not current_view.get("available"):
        backfill_panel_hidden = ""
    selected_backfill_command = backfill.get("commands_by_date", {}).get(
        selected_date,
        make_backfill_command(selected_date) if selected_date else "",
    )
    backfill_range_command = backfill.get("range_command", "")
    backfill_range_hidden = "" if backfill_range_command and backfill_range_command != selected_backfill_command else " hidden"
    backfill_panel = """
          <div class="nightly-backfill" id="nightly-backfill-panel"{hidden}>
            <div class="nightly-backfill-title">缺少整理结果</div>
            <p class="nightly-backfill-note" id="nightly-backfill-note">该日期还没有整理结果。可以复制命令在终端手动回溯。</p>
            <div class="nightly-backfill-command">
              <div class="nightly-backfill-label">单日回溯</div>
              <code id="nightly-backfill-single-command">{single_command}</code>
              <button type="button" class="nightly-backfill-copy" data-backfill-copy="single">复制命令</button>
            </div>
            <div class="nightly-backfill-command" id="nightly-backfill-range"{range_hidden}>
              <div class="nightly-backfill-label">多日回溯</div>
              <code id="nightly-backfill-range-command">{range_command}</code>
              <button type="button" class="nightly-backfill-copy" data-backfill-copy="range">复制命令</button>
            </div>
            <p class="nightly-backfill-status" id="nightly-backfill-status" aria-live="polite"></p>
          </div>
    """.format(
        hidden=backfill_panel_hidden,
        single_command=escape(selected_backfill_command),
        range_command=escape(backfill_range_command),
        range_hidden=backfill_range_hidden,
    )
    return """
    <section id="nightly-summary" class="panel nightly-panel">
      <div class="nightly-shell">
        <div class="nightly-copy">
          <div class="nightly-kicker-row">
            <div class="nightly-kicker">{kicker}</div>
            {badge_row_html}
          </div>
          <div class="nightly-headline-row">
            <div class="nightly-title-block">
              <div class="nightly-title-row">
                <div class="nightly-title-main">
                  <h2 id="nightly-summary-title">{title}</h2>
                  {date_control}
                </div>
                {help_html}
              </div>
              {note_html}
            </div>
          </div>
          <p class="nightly-lead" id="nightly-lead">{lead_text}</p>
          {detail_list}
          {context_block}
          {backfill_panel}
        </div>
        <aside class="nightly-rail">
          <div class="nightly-rail-label">关键指标</div>
          <div class="nightly-stat-grid" id="nightly-stat-grid">
            {stat_cards}
          </div>
          <p class="nightly-rail-note" id="nightly-rail-note">{note_text}</p>
        </aside>
      </div>
    </section>
    """.format(
        badge_row_html=badge_row_html,
        kicker=panel_language_text_html("每日资产账本", "Daily Asset Ledger"),
        title=escape(nightly_title),
        date_control=date_control,
        help_html=help_html,
        note_html=note_html,
        lead_text=escape(current_view.get("lead_text", "")),
        detail_list=detail_list,
        context_block=context_block,
        backfill_panel=backfill_panel,
        stat_cards=stat_cards,
        note_text=escape(current_view.get("note_text", "")),
    )


def make_window_summary_cards(window_overview, language=None):
    language = current_language(language)
    if not window_overview or not window_overview.get("windows"):
        return '<p class="empty">{}</p>'.format(
            escape(localized("暂无窗口整理结果。", "No window synthesis results.", language))
        )
    window_date = window_overview.get("date", "")

    def render_keyword_chips(keywords):
        if not keywords:
            return '<span class="window-keyword empty-keyword">{}</span>'.format(
                escape(localized("暂无关键词", "No keywords", language))
            )
        return "".join(
            '<span class="window-keyword">{}</span>'.format(
                escape(localized_context_keyword(keyword, language=language))
            )
            for keyword in keywords[:6]
        )

    def render_preview_items(items, label, keywords=None):
        rows = []
        for item in items:
            time_html = ""
            if item.get("time"):
                time_html = '<span class="window-detail-time">{}</span>'.format(
                    escape(item["time"])
                )
            text = localize_window_preview_text(
                compact_preview_text(item.get("text", "")),
                language=language,
                keywords=keywords,
                label=label,
            )
            rows.append(
                """
                <li class="window-detail-item">
                  {time_html}
                  <span>{text}</span>
                </li>
                """.format(
                    time_html=time_html,
                    text=escape(text),
                )
            )
        return "".join(rows)

    cards = []
    for item in window_overview.get("windows", []):
        cwd_raw = item.get("cwd", "")
        window_id = item.get("window_id", "")
        anchor_id = build_window_anchor_id(window_id)
        cwd_display = item.get("cwd_display", cwd_raw)
        activity_source_label = window_activity_source_label(
            item.get("activity_source", "history"),
            language,
            thread_source=item.get("thread_source", ""),
        )
        project_label = normalize_brand_display_text(
            item.get("project_label", localized_context_label("个人工作区", language))
        )
        if is_english(language):
            project_label = localized_context_label(project_label, language)
            if contains_cjk(project_label):
                project_label = english_freeform_text(project_label, fallback_label="Project")
        question_summary = localize_window_preview_text(
            item.get("question_summary", ""),
            language=language,
            keywords=item.get("keywords", []),
            label="Focus",
        )
        main_takeaway = localize_window_preview_text(
            item.get("main_takeaway", ""),
            language=language,
            keywords=item.get("keywords", []),
            label="Takeaway",
        )
        raw_window = load_window_record(window_date, window_id)
        raw_window_html = escape(localized("暂无", "None", language))
        session_file_html = escape(localized("暂无", "None", language))
        if raw_window and raw_window.get("_path"):
            raw_window_html = render_local_path_link(
                raw_window.get("_path", ""),
                label=localized("原始窗口 JSON", "Raw Window JSON", language),
            )
        if raw_window and raw_window.get("session_file"):
            session_file_html = render_local_path_link(
                raw_window.get("session_file", ""),
                label=localized("会话 JSONL", "Session JSONL", language),
            )
        cwd_detail_label = cwd_raw or cwd_display
        cwd_detail_html = render_local_path_link(cwd_raw, label=cwd_detail_label)
        cards.append(
            """
            <details class="window-card" id="{anchor_id}">
              <summary class="window-card-trigger">
                <div class="window-card-head">
                  <div class="window-card-copy">
                    <div class="window-card-label">{project_label} · {window_label} {display_index}</div>
                    <p class="window-card-path">{activity_source_label}</p>
                  </div>
                  <div class="window-card-stats">
                    <div class="window-stat">
                      <strong>{question_count}</strong>
                      <span>{questions_label}</span>
                    </div>
                    <div class="window-stat">
                      <strong>{conclusion_count}</strong>
                      <span>{conclusions_label}</span>
                    </div>
                  </div>
                </div>
                <p class="window-card-summary">{main_takeaway}</p>
                <div class="window-card-meta">
                  <span class="window-card-time">{recent_activity} {latest_activity}</span>
                  <span class="window-card-action">
                    <span class="window-card-action-collapsed">{open_details}</span>
                    <span class="window-card-action-expanded">{collapse_details}</span>
                  </span>
                </div>
              </summary>
              <div class="window-card-detail">
                <div class="window-detail-grid">
                  <section class="window-detail-block">
                    <div class="window-detail-label">{question_summary_label}</div>
                    <p>{question_summary}</p>
                  </section>
                  <section class="window-detail-block">
                    <div class="window-detail-label">{conclusion_summary_label}</div>
                    <p>{main_takeaway}</p>
                  </section>
                </div>
                <div class="window-detail-grid compact">
                  <section class="window-detail-block">
                    <div class="window-detail-label">{window_info_label}</div>
                    <ul class="window-detail-list">
                      <li class="window-detail-item"><span>{raw_window_id_label} {window_id_full}</span></li>
                      <li class="window-detail-item"><span>{project_workspace_label} {project_label}</span></li>
                      <li class="window-detail-item"><span>{activity_source_detail_label} {activity_source_label}</span></li>
                      <li class="window-detail-item"><span>{cwd_label} {cwd_detail_html}</span></li>
                      <li class="window-detail-item"><span>{started_at_label} {started_at}</span></li>
                      <li class="window-detail-item"><span>{recent_activity} {latest_activity}</span></li>
                      <li class="window-detail-item"><span>{raw_window_label} {raw_window_html}</span></li>
                      <li class="window-detail-item"><span>{session_file_label} {session_file_html}</span></li>
                    </ul>
                  </section>
                  <section class="window-detail-block">
                    <div class="window-detail-label">{keywords_label}</div>
                    <div class="window-keyword-row">
                      {keyword_chips}
                    </div>
                  </section>
                </div>
                <div class="window-detail-grid">
                  <section class="window-detail-block">
                    <div class="window-detail-label">{recent_questions_label}</div>
                    <ul class="window-detail-list">
                      {recent_prompts}
                    </ul>
                  </section>
                  <section class="window-detail-block">
                    <div class="window-detail-label">{recent_conclusions_label}</div>
                    <ul class="window-detail-list">
                      {recent_conclusions}
                    </ul>
                  </section>
                </div>
              </div>
            </details>
            """.format(
                anchor_id=escape(anchor_id, quote=True),
                display_index=escape(str(item.get("display_index", ""))),
                window_label=escape(localized("窗口", "Window", language)),
                window_id_full=escape(item.get("window_id", "")),
                project_label=escape(project_label),
                activity_source_label=escape(activity_source_label),
                cwd_detail_html=cwd_detail_html,
                question_count=escape(str(item.get("question_count", 0))),
                conclusion_count=escape(str(item.get("conclusion_count", 0))),
                questions_label=escape(localized("问题", "Questions", language)),
                conclusions_label=escape(localized("结论", "Conclusions", language)),
                recent_activity=escape(localized("最近活动", "Recent activity", language)),
                latest_activity=escape(item.get("latest_activity_display", localized("时间未知", "Unknown time", language))),
                started_at=escape(item.get("started_at_display", localized("时间未知", "Unknown time", language))),
                raw_window_html=raw_window_html,
                session_file_html=session_file_html,
                question_summary=escape(question_summary),
                main_takeaway=escape(main_takeaway),
                keyword_chips=render_keyword_chips(item.get("keywords", [])),
                recent_prompts=render_preview_items(
                    item.get("recent_prompts", []),
                    "Question",
                    keywords=item.get("keywords", []),
                ),
                recent_conclusions=render_preview_items(
                    item.get("recent_conclusions", []),
                    "Conclusion",
                    keywords=item.get("keywords", []),
                ),
                open_details=escape(localized("点开看详情", "Open details", language)),
                collapse_details=escape(localized("收起详情", "Collapse details", language)),
                question_summary_label=escape(localized("问题摘要", "Question Summary", language)),
                conclusion_summary_label=escape(localized("结论摘要", "Conclusion Summary", language)),
                window_info_label=escape(localized("窗口信息", "Window Info", language)),
                raw_window_id_label=escape(localized("原始窗口 ID", "Raw Window ID", language)),
                project_workspace_label=escape(localized("项目 / 工作区", "Project / Workspace", language)),
                activity_source_detail_label=escape(localized("活动来源", "Activity Source", language)),
                cwd_label=escape(localized("当前目录", "Current Directory", language)),
                started_at_label=escape(localized("启动时间", "Started At", language)),
                raw_window_label=escape(localized("原始窗口", "Raw Window", language)),
                session_file_label=escape(localized("会话文件", "Session File", language)),
                keywords_label=escape(localized("关键词", "Keywords", language)),
                recent_questions_label=escape(localized("最近问题", "Recent Questions", language)),
                recent_conclusions_label=escape(localized("最近结论", "Recent Conclusions", language)),
            )
        )
    return "".join(cards)


def build_window_overview_heading_note(window_overview, title, language=None):
    language = current_language(language)
    window_overview = window_overview or {}
    heading = title
    note = localized("按窗口区分当天问题与结论", "Questions and conclusions grouped by window", language)
    window_count = safe_int(window_overview.get("window_count", 0))
    if window_count:
        heading = "{} · {}".format(title, window_count)
        if window_overview.get("source_kind") == "nightly_summary":
            note = localized(
                "共 {} 个窗口，原始明细缺失，当前仅展示整理摘要".format(window_count),
                "{}; raw details are missing, so only synthesis summaries are shown".format(
                    plural_en(window_count, "window")
                ),
                language,
            )
        else:
            note = localized(
                "共 {} 个窗口，按最新活动排序，可点开看详情".format(window_count),
                "{} sorted by latest activity. Open a card for details".format(
                    plural_en(window_count, "window")
                ),
                language,
            )
    return heading, note


def build_window_overview_view(window_overview, title_zh="当日窗口概览", title_en="Daily Window Overview"):
    window_overview = window_overview or {}
    heading_zh, note_zh = build_window_overview_heading_note(window_overview, title_zh, language="zh")
    heading_en, note_en = build_window_overview_heading_note(window_overview, title_en, language="en")
    return {
        "date": window_overview.get("date", ""),
        "window_count": window_overview.get("window_count", 0),
        "source_kind": window_overview.get("source_kind", ""),
        "heading": heading_zh,
        "heading_zh": heading_zh,
        "heading_en": heading_en,
        "note": note_zh,
        "note_zh": note_zh,
        "note_en": note_en,
        "cards_html": make_window_summary_cards(window_overview, language="zh"),
        "cards_html_zh": make_window_summary_cards(window_overview, language="zh"),
        "cards_html_en": make_window_summary_cards(window_overview, language="en"),
    }


def build_window_overview_views(candidates, selected_date="", language=None):
    dates = set(list_daily_capture_dates())
    for payload in candidates or []:
        parsed = parse_nightly_summary_date(payload)
        if parsed is not None:
            dates.add(parsed.isoformat())

    views = []
    for date_str in sorted(dates, reverse=True):
        nightly = select_best_nightly_summary_for_date(candidates or [], date_str)
        window_overview = build_window_overview(nightly, target_date=date_str, language=language)
        if not window_overview:
            continue
        views.append(build_window_overview_view(window_overview))

    if selected_date and not any(view.get("date") == selected_date for view in views):
        nightly = select_best_nightly_summary_for_date(candidates or [], selected_date)
        window_overview = build_window_overview(nightly, target_date=selected_date, language=language)
        if window_overview:
            views.insert(0, build_window_overview_view(window_overview))
    return views


def build_metric_help_sections(metric):
    key = metric.get("key", "")
    caption = metric.get("caption", "")
    meta = metric.get("meta", "")

    help_map = {
        "total_assets": [
            {
                "label": "统计什么",
                "body": "已登记到资产注册表的稳定资产总数。",
            },
            {
                "label": "数据来源",
                "body": "state root 下的 registry/assets.jsonl。",
            },
            {
                "label": "不包含",
                "body": "raw 对话、日志、报表，以及还没登记成资产的临时内容。",
            },
        ],
        "active_assets": [
            {
                "label": "统计什么",
                "body": "状态为 active 的资产数量。",
            },
            {
                "label": "怎么看",
                "body": "活跃表示当前仍建议继续复用，不代表当天一定刚被使用。",
            },
        ],
        "task_reviews": [
            {
                "label": "统计什么",
                "body": "本地保存的脱敏任务复盘数量。",
            },
            {
                "label": "数据来源",
                "body": "state root 下的 reviews/ 目录；卡片里的“复盘文件”可以直接打开对应 Markdown。",
            },
        ],
        "tracked_usage_events": [
            {
                "label": "统计什么",
                "body": "已经被记录下来的资产复用事件总数。",
            },
            {
                "label": "数据来源",
                "body": "state root 下的 registry/usage_events.jsonl。",
            },
        ],
        "tracked_minutes_saved": [
            {
                "label": "统计什么",
                "body": "按显式复用记录、近期窗口命中和资产类型基准自动估算的节省分钟数。",
            },
            {
                "label": "怎么看",
                "body": "这不是精确测速；它用于排序和趋势观察，原始 usage event 里的 minutes_saved 只作为强证据之一。",
            },
        ],
        "repo_scoped_assets": [
            {
                "label": "统计什么",
                "body": "scope = repo 的资产数量。",
            },
            {
                "label": "含义",
                "body": "这类资产通常绑定某个仓库、模块或固定工作场景。",
            },
        ],
        "today_token": [
            {
                "label": "统计什么",
                "body": "ccusage 最新一天的总 Token 消耗。",
            },
            {
                "label": "怎么算",
                "body": "输入、缓存输入、输出和推理输出都会计入总量。",
            },
        ],
        "seven_day_token": [
            {
                "label": "统计什么",
                "body": "ccusage 最近 7 天每日总 Token 的累计值。",
            },
            {
                "label": "怎么看",
                "body": "这是滚动 7 日窗口，不是自然周。",
            },
        ],
        "durable_memories": [
            {
                "label": "统计什么",
                "body": "按记忆签名归并后，bucket = durable 的个人资产-长期记忆数量。",
            },
            {
                "label": "数据来源",
                "body": "state root 下的 registry/memory_items.jsonl；同一条记忆跨天重复出现时会合并计算。",
            },
        ],
        "session_memories": [
            {
                "label": "统计什么",
                "body": "按记忆签名归并后，bucket = session 的个人资产-短期工作记忆数量。",
            },
            {
                "label": "含义",
                "body": "更偏当前需求推进，未必适合长期沉淀。",
            },
        ],
        "low_priority_memories": [
            {
                "label": "统计什么",
                "body": "按记忆签名归并后，bucket = low_priority 的个人资产-低优先记忆数量。",
            },
            {
                "label": "含义",
                "body": "保留但优先级较低，通常不作为主路径提示。",
            },
        ],
        "daily_window_count": [
            {
                "label": "统计什么",
                "body": "最近一次窗口整理里纳入统计的窗口数。",
            },
            {
                "label": "怎么算",
                "body": "优先来自 daily capture；原始明细缺失时会退回最近一次 nightly summary。",
            },
        ],
    }

    sections = list(help_map.get(key, []))
    if caption:
        sections.append({"label": "当前说明", "body": caption})
    if meta:
        sections.append({"label": "补充信息", "body": meta})
    return sections


def build_html(data):
    language = current_language(data.get("language"))
    base_make_help_popover = globals()["make_help_popover"]
    base_make_panel_header = globals()["make_panel_header"]

    def make_help_popover(title, sections, compact=False):
        return base_make_help_popover(title, sections, compact=compact, language=language)

    def make_panel_header(
        title,
        note="",
        help_html="",
        note_id="",
        note_content_html="",
        title_id="",
        extra_meta_html="",
    ):
        return base_make_panel_header(
            title,
            note=note,
            help_html=help_html,
            note_id=note_id,
            note_content_html=note_content_html,
            title_id=title_id,
            extra_meta_html=extra_meta_html,
            language=language,
        )

    token_usage = data["token_usage"]
    snapshot_payload = json.dumps(
        {
            "generated_at": data["generated_at"],
            "generated_at_iso": data.get("generated_at_iso", ""),
            "token_usage": token_usage,
            "daily_summaries": data.get("daily_summary_views", []),
            "daily_summary_default_date": data.get("daily_summary_default_date", ""),
            "daily_summary_select_dates": data.get("daily_summary_select_dates", []),
            "backfill": data.get("backfill", {}),
            "window_overviews": data.get("window_overview_views", []),
            "window_overview_default_date": data.get("window_overview_default_date", ""),
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    theme_switch = make_theme_switch()
    language_switch = make_language_switch(language)
    token_note = (
        localized("数据来源：ccusage 日维度统计", "Source: ccusage daily stats", language)
        if token_usage["available"]
        else localized("暂未获取到 ccusage 的日维度统计", "ccusage daily stats are unavailable", language)
    )
    nightly = data["nightly"] or {}
    panel_views = data.get("panel_views", {})
    project_contexts = data.get("project_contexts") or []
    window_overview = data.get("window_overview") or {}
    memory_registry = data.get("memory_registry") or []
    codex_native_memory = data.get("codex_native_memory") or []
    codex_native_preference_rows = data.get("codex_native_preference_rows") or []
    codex_native_tip_rows = data.get("codex_native_tip_rows") or []
    codex_native_task_groups = data.get("codex_native_task_groups") or []
    codex_native_memory_counts = data.get("codex_native_memory_counts") or {}
    codex_native_memory_comparison = data.get("codex_native_memory_comparison") or {}
    codex_memory_summary_label = data.get("codex_memory_summary_path_label") or render_path(
        PATHS.codex_home / "memories" / "memory_summary.md"
    )
    nightly_note = data.get("nightly_note", nightly.get("date", "暂无夜间整理结果"))
    active_nightly_note = data.get("active_nightly_note", "")
    nightly_window_title = data.get("window_overview_title", derive_nightly_window_title(data["nightly_title"]))
    project_context_views = data.get("project_context_views") or {}
    project_context_views_zh = data.get("project_context_views_zh") or project_context_views
    project_context_views_en = data.get("project_context_views_en") or project_context_views
    project_context_default_days = data.get("project_context_default_days", PROJECT_CONTEXT_DEFAULT_DAYS)
    project_context_note = "可切换最近 1-{} 天；项目内按需求 / 主题二次归类".format(
        PROJECT_CONTEXT_MAX_DAYS
    )
    window_overview_heading, window_overview_note = build_window_overview_heading_note(
        window_overview,
        nightly_window_title,
        language=language,
    )
    daily_summary_title = localized("今天哪些工作能复用？", "What work can be reused today?", language)
    window_overview_date_control = make_window_overview_date_control(
        data.get("window_overview_views", []),
        data.get("window_overview_default_date", (window_overview or {}).get("date", "")),
    )
    show_highlights_panel = not nightly
    insight_section_class = "grid two-up" if show_highlights_panel else "grid"
    highlights_panel_html = ""
    if show_highlights_panel:
        highlights_panel_html = """
      <section class="panel">
        {highlights_header}
        <ul class="highlight-list">
          {highlights}
        </ul>
      </section>
        """.format(
            highlights_header="{highlights_header}",
            highlights="{highlights}",
        )
    insight_section_html = """
    <section class="{section_class}">
      <section class="panel">
        {term_cloud_header}
        <div class="term-cloud-area">
          {term_cloud}
        </div>
      </section>
      {highlights_panel_html}
    </section>
    """.format(
        section_class=insight_section_class,
        term_cloud_header="{term_cloud_header}",
        term_cloud="{term_cloud}",
        highlights_panel_html=highlights_panel_html,
    )

    token_metric_cards = []
    asset_metric_cards = []
    hidden_metric_keys = set()
    if nightly:
        hidden_metric_keys.update(
            {"durable_memories", "session_memories", "low_priority_memories", "daily_window_count"}
        )
    for metric in data["metrics"]:
        if metric.get("key") in hidden_metric_keys:
            continue
        card_classes = "metric-card"
        if metric.get("live"):
            card_classes = "{} live-metric-card".format(card_classes)
        card_attrs = []
        if metric.get("key"):
            card_attrs.append(' data-metric-key="{}"'.format(escape(metric["key"])))
        if metric.get("live"):
            card_attrs.append(' data-live-card="true"')
        metric_meta = ""
        if metric.get("meta"):
            metric_meta = '<div class="metric-meta" data-role="meta">{}</div>'.format(
                escape(metric["meta"])
            )
        metric_footer = metric_meta
        if metric.get("key") == "today_token":
            if not metric_footer:
                metric_footer = '<div class="metric-meta" data-role="meta"></div>'
            metric_footer = """
              <div class="metric-footer token-refresh-footer">
                {metric_meta}
                <button class="action-button" type="button" id="token-refresh-button">
                  <span class="button-spinner" aria-hidden="true"></span>
                  <span id="token-refresh-label">实时刷新 Token</span>
                </button>
              </div>
              <div class="token-refresh-card-status" id="token-refresh-status">
                <span id="token-refresh-status-text">先展示本地快照，再实时同步最新 Token。</span>
              </div>
            """.format(metric_meta=metric_footer)
        metric_help = make_help_popover(
            metric.get("label", ""),
            build_metric_help_sections(metric),
            compact=True,
        )
        card_html = """
            <article class="{card_classes}"{card_attrs}>
              <div class="metric-head">
                <div class="metric-label">{label}</div>
                {metric_help}
              </div>
              <div class="metric-value" data-role="value">{value}</div>
              <div class="metric-caption" data-role="caption">{caption}</div>
              {metric_footer}
            </article>
            """.format(
                card_classes=card_classes,
                card_attrs="".join(card_attrs),
                label=escape(metric["label"]),
                metric_help=metric_help,
                value=escape(str(metric["value"])),
                caption=escape(metric["caption"]),
                metric_footer=metric_footer,
            )
        if metric.get("key") in TOKEN_METRIC_KEYS:
            token_metric_cards.append(card_html)
        else:
            asset_metric_cards.append(card_html)
    window_source_note = "当前优先使用原始 daily capture。"
    if window_overview.get("source_kind") == "nightly_summary":
        window_source_note = "当前缺少原始 daily capture，已退回最近一次 nightly summary。"
    type_panel_help = make_help_popover(
        "资产类型分布",
        build_asset_type_help_sections(
            data.get("asset_type_scope_note", ""),
            data.get("asset_type_guide", []),
        ),
        compact=True,
    )
    context_panel_help = make_help_popover(
        "项目 / 上下文分布",
        [
            {
                "label": "统计什么",
                "body": "每条已登记资产最终落到哪个项目 / 上下文标签。这里数的是资产条目，不是窗口数。",
            },
            {
                "label": "怎么算",
                "body": [
                    "先看 artifact_paths：如果能识别出真实仓库项目，就直接记仓库名。",
                    "仓库项目推不出时，优先使用资产自己的 domain 作为业务归属。",
                    "只有 repo project 和 domain 都不足以归类时，才从 title、value_note、notes、tags、source_task 做文本推断；再不行才回退到 ~/.codex、state root 这类特殊上下文。",
                ],
            },
            {
                "label": "为什么会看到 Codex 本地环境",
                "body": "只有在业务项目和 domain 都无法归类时，且资产文件实际落在 ~/.codex 下，例如 skills、prompts、scripts、config，才会算到 Codex 本地环境。",
            },
        ],
        compact=True,
    )
    month_panel_help = make_help_popover(
        "月度新增",
        [
            {
                "label": "统计什么",
                "body": "按资产的 created_at 月份统计新增条目数。",
            },
            {
                "label": "注意",
                "body": "这里看的是首次登记时间，不是最近更新时间。",
            },
        ],
    )
    scope_panel_help = make_help_popover(
        "适用层级",
        [
            {
                "label": "统计什么",
                "body": "按 scope 字段统计资产的复用范围。",
            },
            {
                "label": "标签含义",
                "body": [
                    "仅个人使用：更偏个人习惯、环境配置或私有工作方式。",
                    "仓库场景复用：绑定某个仓库、业务线或固定场景。",
                    "团队共享：适合多人共同遵守或复用。",
                ],
            },
        ],
    )
    term_cloud_help = make_help_popover(
        "今日热词",
        [
            {
                "label": "来源",
                "body": "从所选日期范围内的窗口整理、资产标题、领域、备注、复盘文本和复用记录里抽词。",
            },
            {
                "label": "时间范围",
                "body": "默认展示今日热词，可切换近 3 日和近 7 日。",
            },
            {
                "label": "怎么看",
                "body": [
                    "字越大代表出现频次越高。这是主题提示，不代表严格的主题建模结果。",
                    "它会随当天窗口整理、资产、复盘或复用记录新增、修改而变化。",
                ],
            },
        ],
    )
    highlights_help = make_help_popover(
        "本期小结",
        [
            {
                "label": "生成方式",
                "body": "按当前资产数量、活跃状态、最近上下文、Token 和夜间整理结果拼出几条快速结论。",
            },
            {
                "label": "怎么看",
                "body": "它适合快速扫一眼，不替代下面的明细面板。",
            },
        ],
    )
    term_cloud_header_html = make_panel_header(
        "今日热词",
        "默认今日，可切换近 3 日 / 近 7 日",
        term_cloud_help,
    )
    term_cloud_html = make_summary_term_cloud_views(
        data.get("summary_term_views", []),
        data.get("summary_term_default_days", SUMMARY_TERM_DEFAULT_DAYS),
        language=language,
    )
    highlights_header_html = make_panel_header(
        "本期小结",
        "方便快速浏览当前阶段的沉淀情况",
        highlights_help,
    )
    highlights_html = make_highlight_list(data["highlights"], data.get("token_highlight", ""))
    insight_section_html = insight_section_html.format(
        term_cloud_header=term_cloud_header_html,
        term_cloud=term_cloud_html,
        highlights_header=highlights_header_html,
        highlights=highlights_html,
    )
    token_overview_help = make_help_popover(
        "Token 速览",
        [
            {
                "label": "统计什么",
                "body": "把 ccusage 的日维度数据再加工成 7 日账单、7 日均值、峰值日和缓存占输入等快速判断信号。",
            },
            {
                "label": "怎么看",
                "body": "上方两张大卡看总量，速览区看变化和结构，下面的每日 / 今日柱条可以 hover 到具体构成。",
            },
            {
                "label": "注意",
                "body": "缓存输入是输入 Token 的子集，不应和输入、输出直接相加。",
            },
        ],
    )
    daily_token_help = make_help_popover(
        "每日 Token 消耗",
        [
            {
                "label": "数据来源",
                "body": "ccusage 的日维度统计。",
            },
            {
                "label": "统计什么",
                "body": "按日期展示最近几天的 Token 消耗趋势；页面打开后会先显示快照，再尝试刷新实时值。",
            },
        ],
    )
    today_token_help = make_help_popover(
        "今日 Token 构成",
        [
            {
                "label": "数据来源",
                "body": "ccusage 最新一天的 breakdown。",
            },
            {
                "label": "统计什么",
                "body": "把最新一天的总 Token 拆成输入、缓存输入、输出和推理输出。",
            },
        ],
    )
    project_context_help = make_help_popover(
        "当前项目上下文",
        [
            {
                "label": "统计什么",
                "body": "最近捕获到的窗口，会先按项目 / 上下文聚合，再展示每组的窗口数、问题数和结论数。",
            },
            {
                "label": "怎么算",
                "body": [
                    "优先从窗口 cwd 推 project_label：先认 Git 根目录，再认常见项目标记。",
                    "cwd 推不出时，才回退到问题摘要、结论摘要和关键词做文本推断。",
                    "同名项目会合并，按最近活动时间排序。",
                ],
            },
            {
                "label": "和上面的区别",
                "body": "这里数的是窗口上下文；上面的 项目 / 上下文分布 数的是资产条目。",
            },
        ],
    )
    nightly_summary_help = make_help_popover(
        daily_summary_title,
        [
            {
                "label": "统计什么",
                "body": "这是按日期切换的每日整理摘要卡，默认展示今天。",
            },
            {
                "label": "包含什么",
                "body": [
                    "日期选择器和摘要主结论。",
                    "工作窗口、长期记忆、短期跟进、低优先级记忆。",
                    "最近相关的上下文标签。",
                ],
            },
            {
                "label": "当前来源",
                "body": window_source_note,
            },
        ],
    )
    durable_memory_help = make_help_popover(
        "个人资产-长期记忆",
        [
            {
                "label": "统计什么",
                "body": "当前登记册中 bucket = durable 的长期记忆，按近 7 日估算使用频率排序。",
            },
            {
                "label": "怎么算",
                "body": "频率来自近 7 日窗口匹配：来源窗口直接命中权重最高，标题、关键词、说明与历史窗口摘要匹配会按相关度加权，项目上下文只做小幅加分。",
            },
        ],
    )
    session_memory_help = make_help_popover(
        "个人资产-短期工作记忆",
        [
            {
                "label": "统计什么",
                "body": "当前登记册中 bucket = session 的短期工作记忆，按近 7 日估算使用频率排序。",
            },
            {
                "label": "含义",
                "body": "这类内容对当前任务推进有帮助，但未必适合长期沉淀。",
            },
        ],
    )
    low_priority_memory_help = make_help_popover(
        "个人资产-低优先级记忆",
        [
            {
                "label": "统计什么",
                "body": "最近一次 nightly summary 里的 low_priority bucket 条目。",
            },
            {
                "label": "含义",
                "body": "保留但优先级较低，通常不是第一推荐路径。",
            },
        ],
    )
    codex_native_memory_note = (
        codex_native_memory_comparison.get("note")
        or "未检测到 {}。".format(codex_memory_summary_label)
    )
    codex_native_memory_note_zh = (
        codex_native_memory_comparison.get("note_zh")
        or codex_native_memory_note
    )
    codex_native_memory_note_en = codex_native_memory_comparison.get("note_en", "")
    codex_native_memory_note_html = panel_language_text_html(
        codex_native_memory_note_zh,
        codex_native_memory_note_en,
    )
    codex_native_topic_help = make_help_popover(
        "Codex 原生记忆-主题项",
        [
            {
                "label": "统计什么",
                "body": "直接读取 {} 的“What's in Memory”主题项。".format(
                    codex_memory_summary_label
                ),
            },
            {
                "label": "关系",
                "body": "它和个人资产记忆都来自本地 Codex 工作，但前者更接近模型会读取的长期摘要，后者是夜间整理后的结构化日志。",
            },
            {
                "label": "区别",
                "body": [
                    "原生记忆偏长期规则、稳定 workflow、历史 rollout 结论。",
                    "个人资产记忆偏近期窗口整理、来源追踪、工作区定位。",
                    "用户偏好、通用 tips 和任务组已经拆到独立模块。",
                ],
            },
            {
                "label": "当前计数",
                "body": "主题项 {} 条；用户偏好 {} 条；通用 tips {} 条。".format(
                    len(codex_native_memory),
                    codex_native_memory_counts.get("user_preferences", 0),
                    codex_native_memory_counts.get("general_tips", 0),
                ),
            },
        ],
    )
    codex_native_preference_help = make_help_popover(
        "Codex 原生记忆-偏好",
        [
            {
                "label": "统计什么",
                "body": "直接读取 Codex 原生 memory summary 里的 User preferences。",
            },
            {
                "label": "怎么看",
                "body": "按和个人资产-长期记忆一致的卡片样式展示，便于和 nightly 整理出的记忆对齐比较。",
            },
        ],
    )
    codex_native_tip_help = make_help_popover(
        "Codex 原生记忆-通用 tips",
        [
            {
                "label": "统计什么",
                "body": "直接读取 Codex 原生 memory summary 里的 General Tips。",
            },
            {
                "label": "怎么看",
                "body": "更偏通用工作方法和排障路径，和偏好模块分开看。",
            },
        ],
    )
    codex_native_task_group_help = make_help_popover(
        "Codex 原生记忆-任务组",
        [
            {
                "label": "统计什么",
                "body": "读取 MEMORY.md 里的 Task Group 索引，展示历史任务组和对应来源。",
            },
            {
                "label": "怎么看",
                "body": "它更像长期主题目录，不等同于某一天的 nightly memory。",
            },
        ],
    )
    codex_native_preference_cards = make_memory_cards(
        make_codex_native_brief_memory_items(
            codex_native_preference_rows,
            "preference",
            language=language,
        )
    )
    codex_native_tip_cards = make_memory_cards(
        make_codex_native_brief_memory_items(
            codex_native_tip_rows,
            "tip",
            language=language,
        )
    )
    codex_native_task_group_cards = make_memory_cards(
        make_codex_native_brief_memory_items(
            codex_native_task_groups,
            "task_group",
            language=language,
        )
    )
    recent_assets_help = make_help_popover(
        "最近更新的资产",
        [
            {
                "label": "排序方式",
                "body": "按 updated_at 倒序，展示最近改动过的资产。",
            },
            {
                "label": "列含义",
                "body": [
                    "项目 / 上下文：资产最终归到的 display_context。",
                    "适用层级：scope 的展示值。",
                    "复用记录：这个资产已经被记录过多少次 usage event。",
                ],
            },
        ],
    )
    top_assets_help = make_help_popover(
        "复用价值较高的资产",
        [
            {
                "label": "排序方式",
                "body": "按自动估算价值分倒序；分数由显式复用、近期窗口命中、估算节省分钟、资产类型基准和最近维护信号组成。",
            },
            {
                "label": "怎么看",
                "body": "价值分衡量“这个资产是否持续减少重复工作或降低出错成本”；估算节省是分钟级近似，不需要用户手工维护 reuse_count。",
            },
            {
                "label": "证据",
                "body": "显式复用记录权重最高；窗口命中是弱证据；没有直接证据的资产只保留类型和维护活跃度带来的潜在价值。",
            },
        ],
    )
    reviews_help = make_help_popover(
        "最近复盘",
        [
            {
                "label": "数据来源",
                "body": "state root 下的 reviews/ 目录；卡片里的“复盘文件”可以直接打开对应 Markdown。",
            },
            {
                "label": "排序方式",
                "body": "按复盘里的日期和任务名倒序展示最近条目。",
            },
        ],
    )
    usage_help = make_help_popover(
        "最近复用记录",
        [
            {
                "label": "数据来源",
                "body": "state root 下的 registry/usage_events.jsonl。",
            },
            {
                "label": "排序方式",
                "body": "按 date、asset_id、task 倒序展示最近事件。",
            },
            {
                "label": "怎么看",
                "body": "它证明某个已有资产在实际任务里起过作用，但不等于自动精确量化收益。",
            },
        ],
    )
    window_overview_help = make_help_popover(
        window_overview_heading,
        [
            {
                "label": "统计什么",
                "body": "最近一次窗口整理里的窗口级明细。每张卡对应一个窗口，而不是一个资产。",
            },
            {
                "label": "包含什么",
                "body": [
                    "cwd / project_label、问题数、结论数。",
                    "问题摘要、结论摘要、关键词。",
                    "最近问题和最近结论片段。",
                ],
            },
            {
                "label": "当前来源",
                "body": window_source_note,
            },
        ],
    )
    nightly_summary_panel = make_nightly_summary_panel(
        daily_summary_title,
        nightly_note,
        active_nightly_note,
        nightly,
        window_overview,
        project_contexts,
        help_html=nightly_summary_help,
        summary_views=data.get("daily_summary_views", []),
        selected_date=data.get("daily_summary_default_date", ""),
        selectable_dates=data.get("daily_summary_select_dates", []),
        backfill=data.get("backfill", {}),
    )
    author_link_html = (
        '<a href="https://www.npmjs.com/~kk_kais" '
        'target="_blank" rel="noopener noreferrer">kk_kais</a>'
    )
    github_link_html = (
        '<a href="{url}" target="_blank" rel="noopener noreferrer">'
        'openrelix/openrelix</a>'
    ).format(url=escape(PROJECT_GITHUB_URL, quote=True))
    github_button = """
        <a class="hero-github-link" href="{url}" target="_blank" rel="noopener noreferrer">
          {label}
        </a>
    """.format(
        url=escape(PROJECT_GITHUB_URL, quote=True),
        label=panel_language_text_html("GitHub 点星支持", "Star on GitHub"),
    )
    panel_footer_notice = panel_language_variant_html(
        (
            "MIT License. Copyright (c) 2026 {author}. "
            "本面板由 OpenRelix 在本地生成，与 OpenAI 无官方关联。"
            "项目页：{github}，欢迎点星支持。"
        ).format(author=author_link_html, github=github_link_html),
        (
            "MIT License. Copyright (c) 2026 {author}. "
            "This panel is generated locally by OpenRelix. "
            "Unofficial and not affiliated with OpenAI. "
            "Project: {github}. Stars are welcome."
        ).format(author=author_link_html, github=github_link_html),
    )

    return """<!DOCTYPE html>
<html lang="{html_language}" data-default-language="{default_language}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{document_title}</title>
  <style>
    :root {{
      color-scheme: light;
      --canvas-top: #fbfbfd;
      --bg: #f5f5f7;
      --surface: rgba(255, 255, 255, 0.86);
      --paper: rgba(255, 255, 255, 0.78);
      --control: rgba(255, 255, 255, 0.72);
      --control-strong: rgba(255, 255, 255, 0.9);
      --elevated: rgba(255, 255, 255, 0.96);
      --card: rgba(255, 255, 255, 0.68);
      --metric-card: rgba(255, 255, 255, 0.82);
      --soft: rgba(245, 245, 247, 0.82);
      --chip-bg: rgba(255, 255, 255, 0.55);
      --chip-muted-bg: rgba(245, 245, 247, 0.82);
      --accent-soft: rgba(0, 113, 227, 0.08);
      --accent-soft-strong: rgba(0, 113, 227, 0.14);
      --danger-soft: rgba(184, 100, 94, 0.1);
      --hover-bg: rgba(0, 0, 0, 0.06);
      --track: rgba(0, 0, 0, 0.07);
      --ink: #1d1d1f;
      --muted: #6e6e73;
      --line: rgba(0, 0, 0, 0.08);
      --line-strong: rgba(0, 0, 0, 0.12);
      --teal: #0071e3;
      --amber: #bf6b00;
      --slate: #56606a;
      --rose: #d70015;
      --green: #248a3d;
      --shadow: 0 18px 42px rgba(0, 0, 0, 0.08);
      --shadow-soft: 0 8px 24px rgba(0, 0, 0, 0.05);
    }}

    body[data-theme="dark"] {{
      color-scheme: dark;
      --canvas-top: #111318;
      --bg: #171a21;
      --surface: rgba(31, 35, 44, 0.9);
      --paper: rgba(28, 32, 40, 0.84);
      --control: rgba(39, 44, 55, 0.86);
      --control-strong: rgba(48, 54, 66, 0.95);
      --elevated: rgba(33, 38, 48, 0.98);
      --card: rgba(35, 40, 50, 0.78);
      --metric-card: rgba(35, 40, 50, 0.92);
      --soft: rgba(43, 48, 58, 0.74);
      --chip-bg: rgba(47, 53, 65, 0.86);
      --chip-muted-bg: rgba(50, 56, 68, 0.9);
      --accent-soft: rgba(102, 170, 255, 0.16);
      --accent-soft-strong: rgba(102, 170, 255, 0.22);
      --danger-soft: rgba(255, 111, 125, 0.15);
      --hover-bg: rgba(255, 255, 255, 0.09);
      --track: rgba(255, 255, 255, 0.12);
      --ink: #f4f5f7;
      --muted: #a6adbb;
      --line: rgba(255, 255, 255, 0.12);
      --line-strong: rgba(255, 255, 255, 0.18);
      --teal: #66aaff;
      --amber: #ffb866;
      --slate: #c4cad6;
      --rose: #ff6f7d;
      --green: #67d982;
      --shadow: 0 18px 42px rgba(0, 0, 0, 0.38);
      --shadow-soft: 0 8px 24px rgba(0, 0, 0, 0.28);
    }}

    @media (prefers-color-scheme: dark) {{
      body[data-theme-choice="system"]:not([data-theme="light"]) {{
        color-scheme: dark;
        --canvas-top: #111318;
        --bg: #171a21;
        --surface: rgba(31, 35, 44, 0.9);
        --paper: rgba(28, 32, 40, 0.84);
        --control: rgba(39, 44, 55, 0.86);
        --control-strong: rgba(48, 54, 66, 0.95);
        --elevated: rgba(33, 38, 48, 0.98);
        --card: rgba(35, 40, 50, 0.78);
        --metric-card: rgba(35, 40, 50, 0.92);
        --soft: rgba(43, 48, 58, 0.74);
        --chip-bg: rgba(47, 53, 65, 0.86);
        --chip-muted-bg: rgba(50, 56, 68, 0.9);
        --accent-soft: rgba(102, 170, 255, 0.16);
        --accent-soft-strong: rgba(102, 170, 255, 0.22);
        --danger-soft: rgba(255, 111, 125, 0.15);
        --hover-bg: rgba(255, 255, 255, 0.09);
        --track: rgba(255, 255, 255, 0.12);
        --ink: #f4f5f7;
        --muted: #a6adbb;
        --line: rgba(255, 255, 255, 0.12);
        --line-strong: rgba(255, 255, 255, 0.18);
        --teal: #66aaff;
        --amber: #ffb866;
        --slate: #c4cad6;
        --rose: #ff6f7d;
        --green: #67d982;
        --shadow: 0 18px 42px rgba(0, 0, 0, 0.38);
        --shadow-soft: 0 8px 24px rgba(0, 0, 0, 0.28);
      }}
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Hiragino Sans GB", "Noto Sans SC", "Microsoft YaHei", sans-serif;
      background: linear-gradient(180deg, var(--canvas-top) 0%, var(--bg) 100%);
      -webkit-font-smoothing: antialiased;
      text-rendering: geometricPrecision;
    }}

    body[data-language="zh"] [data-lang-only="en"],
    body[data-language="en"] [data-lang-only="zh"] {{
      display: none !important;
    }}

    html {{
      scroll-behavior: smooth;
    }}

    .app-shell {{
      position: relative;
      width: min(1280px, calc(100% - 48px));
      margin: 0 auto;
      padding: 36px 0 56px;
    }}

    .page {{
      max-width: 1280px;
      width: 100%;
      min-width: 0;
      margin: 0 auto;
      padding: 0;
    }}

    .side-nav {{
      position: fixed;
      top: 36px;
      left: max(20px, calc((100vw - 1280px) / 2 - 232px));
      z-index: 40;
      width: 212px;
      max-height: calc(100vh - 36px);
      padding: 14px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 22px;
      background: var(--paper);
      box-shadow: var(--shadow-soft);
      backdrop-filter: blur(18px);
    }}

    .side-nav-title {{
      margin: 2px 4px 10px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.25;
    }}

    .side-nav-list {{
      display: grid;
      gap: 4px;
    }}

    .side-nav-group {{
      margin: 12px 8px 3px;
      color: var(--subtle);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0;
      line-height: 1.2;
    }}

    .side-nav-group:first-child {{
      margin-top: 0;
    }}

    .side-nav-link {{
      position: relative;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      align-items: center;
      padding: 9px 10px 9px 14px;
      border-radius: 12px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      line-height: 1.25;
      text-decoration: none;
      transition: background 160ms ease, color 160ms ease;
    }}

    .side-nav-link::before {{
      content: "";
      position: absolute;
      left: 7px;
      top: 9px;
      bottom: 9px;
      width: 3px;
      border-radius: 999px;
      background: transparent;
    }}

    .side-nav-label {{
      min-width: 0;
      overflow-wrap: anywhere;
      white-space: normal;
    }}

    .side-nav-link:hover {{
      background: var(--accent-soft);
      color: var(--teal);
    }}

    .side-nav-link.is-child {{
      margin-left: 12px;
      padding: 7px 9px 7px 14px;
      border-radius: 10px;
      color: var(--subtle);
      font-size: 12px;
      font-weight: 680;
    }}

    .side-nav-link.is-child::before {{
      left: 6px;
      top: 8px;
      bottom: 8px;
      width: 2px;
    }}

    .side-nav-link.is-active {{
      background: var(--accent-soft-strong);
      color: var(--teal);
    }}

    .side-nav-link.is-active::before {{
      background: var(--teal);
    }}

    .page [id] {{
      scroll-margin-top: 22px;
    }}

    .page > section + section {{
      margin-top: 18px;
    }}

    .panel-footer {{
      margin-top: 20px;
      padding: 18px 8px 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.7;
      text-align: center;
    }}

    .panel-footer a {{
      color: var(--teal);
      font-weight: 700;
      text-decoration: none;
    }}

    .panel-footer a:hover {{
      text-decoration: underline;
      text-underline-offset: 3px;
    }}

    .hero {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 26px;
      box-shadow: var(--shadow);
      padding: 22px 24px;
      position: relative;
      overflow: visible;
      backdrop-filter: blur(18px);
      min-width: 0;
    }}

    .hero::after {{
      display: none;
    }}

    .hero > * {{
      position: relative;
      z-index: 1;
    }}

    .hero-topline {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
    }}

    .hero-title-block {{
      min-width: 0;
    }}

    .hero-actions {{
      display: flex;
      align-items: flex-start;
      justify-content: flex-end;
      gap: 10px;
      flex: 0 0 auto;
      flex-wrap: wrap;
    }}

    .language-switch,
    .theme-switch {{
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--control);
      box-shadow: var(--shadow-soft);
    }}

    .language-option,
    .theme-option {{
      appearance: none;
      border: 0;
      border-radius: 999px;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      font: inherit;
      font-size: 13px;
      font-weight: 700;
      line-height: 1;
      padding: 8px 10px;
      white-space: nowrap;
    }}

    .language-option:hover,
    .theme-option:hover {{
      color: var(--teal);
    }}

    .language-option.is-active,
    .theme-option.is-active {{
      background: var(--teal);
      color: #ffffff;
      box-shadow: 0 8px 16px rgba(0, 113, 227, 0.18);
    }}

    .hero-github-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 36px;
      padding: 9px 13px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--control);
      color: var(--teal);
      font-size: 13px;
      font-weight: 700;
      line-height: 1;
      text-decoration: none;
      white-space: nowrap;
      box-shadow: var(--shadow-soft);
    }}

    .hero-github-link:hover {{
      border-color: rgba(0, 113, 227, 0.26);
      background: var(--control-strong);
    }}

    .eyebrow {{
      margin: 0 0 10px;
      letter-spacing: 0;
      color: var(--teal);
      font-size: 12px;
      font-weight: 700;
    }}

    h1, h2, h3 {{
      font-family: inherit;
      font-weight: 700;
      margin: 0;
    }}

    .hero-heading-row {{
      display: flex;
      align-items: flex-end;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 10px;
      min-width: 0;
    }}

    h1 {{
      font-size: 36px;
      line-height: 1.12;
    }}

    .hero-brand-line {{
      display: inline-flex;
      align-items: center;
      width: fit-content;
      max-width: 100%;
      margin: 0;
      padding: 6px 10px;
      border: 1px solid rgba(0, 113, 227, 0.22);
      border-radius: 999px;
      background: var(--control-strong);
      color: var(--teal);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.2;
      overflow-wrap: anywhere;
    }}

    .hero-copy {{
      max-width: 760px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.55;
      overflow-wrap: anywhere;
    }}

    .hero-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 16px;
      color: var(--muted);
      font-size: 13px;
    }}

    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--chip-bg);
      padding: 8px 12px;
      max-width: 100%;
      overflow-wrap: anywhere;
    }}

    .action-button {{
      appearance: none;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 0;
      border-radius: 999px;
      background: var(--teal);
      color: #ffffff;
      padding: 10px 16px;
      font-size: 14px;
      cursor: pointer;
      box-shadow: 0 12px 24px rgba(0, 113, 227, 0.18);
    }}

    .action-button.secondary {{
      background: var(--control);
      color: var(--ink);
      border: 1px solid var(--line);
      box-shadow: none;
    }}

    .action-button.is-loading {{
      pointer-events: none;
      opacity: 0.92;
    }}

    .button-spinner {{
      display: none;
      width: 14px;
      height: 14px;
      border-radius: 999px;
      border: 2px solid rgba(255, 255, 255, 0.45);
      border-top-color: #ffffff;
      animation: spin 0.8s linear infinite;
    }}

    .action-button.secondary .button-spinner {{
      border-color: rgba(30, 36, 39, 0.18);
      border-top-color: var(--ink);
    }}

    .action-button.is-loading .button-spinner {{
      display: inline-flex;
    }}
    #token-refresh-status-text {{
      min-width: 0;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .grid {{
      display: grid;
      gap: 18px;
    }}

    .metrics-grid {{
      grid-template-columns: repeat(auto-fit, minmax(min(176px, 100%), 1fr));
    }}

    .two-up {{
      grid-template-columns: repeat(auto-fit, minmax(min(340px, 100%), 1fr));
    }}

    .memory-stack {{
      grid-template-columns: 1fr;
    }}

    .memory-family {{
      display: grid;
      gap: 18px;
    }}

    .asset-ledger-section {{
      display: grid;
      gap: 18px;
    }}

    .memory-family-head {{
      display: grid;
      padding: 2px 4px 0;
    }}

    .memory-family-title-row {{
      display: block;
    }}

    .memory-family-title-row.has-extra {{
      display: grid;
      grid-template-columns: minmax(260px, 0.7fr) minmax(320px, 0.85fr) minmax(380px, 440px);
      align-items: start;
      gap: 24px;
    }}

    .memory-family-title-copy {{
      min-width: 0;
    }}

    .memory-family-head .section-kicker {{
      margin: 0;
      color: var(--teal);
      font-size: 12px;
      font-weight: 800;
      line-height: 1.25;
    }}

    .memory-family-head h2 {{
      margin: 0;
      font-size: 28px;
      line-height: 1.18;
    }}

    .memory-family-note {{
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }}

    .memory-token-widget {{
      width: 100%;
      min-width: 0;
      padding: 14px 16px;
      border: 1px solid var(--line-strong);
      border-radius: 18px;
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.42), rgba(255, 255, 255, 0)),
        var(--surface);
      box-shadow: var(--shadow-soft);
      backdrop-filter: blur(18px);
    }}

    body[data-theme="dark"] .memory-token-widget {{
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0)),
        var(--surface);
    }}

    .memory-count-widget {{
      width: 100%;
      min-width: 0;
      padding: 14px 16px;
      border: 1px solid var(--line-strong);
      border-radius: 18px;
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.42), rgba(255, 255, 255, 0)),
        var(--surface);
      box-shadow: var(--shadow-soft);
      backdrop-filter: blur(18px);
    }}

    body[data-theme="dark"] .memory-count-widget {{
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0)),
        var(--surface);
    }}

    .memory-count-topline {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }}

    .memory-count-label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      line-height: 1.2;
    }}

    .memory-count-total {{
      flex: 0 0 auto;
      color: var(--slate);
      font-size: 12px;
      font-weight: 720;
      line-height: 1.2;
      white-space: nowrap;
    }}

    .memory-count-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }}

    .memory-count-item {{
      min-width: 0;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--control);
    }}

    .memory-count-item span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .memory-count-item b {{
      display: block;
      margin-top: 7px;
      color: var(--ink);
      font-size: 26px;
      font-weight: 780;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }}

    .memory-token-topline {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}

    .memory-token-label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      line-height: 1.2;
    }}

    .memory-token-status {{
      flex: 0 0 auto;
      min-height: 24px;
      display: inline-flex;
      align-items: center;
      padding: 0 10px;
      border: 1px solid rgba(52, 199, 89, 0.24);
      border-radius: 999px;
      background: rgba(52, 199, 89, 0.12);
      color: var(--green);
      font-size: 12px;
      font-weight: 760;
      line-height: 1;
    }}

    .memory-token-value {{
      color: var(--ink);
      font-size: 30px;
      font-weight: 820;
      line-height: 1.05;
      letter-spacing: 0;
      font-variant-numeric: tabular-nums;
    }}

    .memory-token-main {{
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 14px;
      align-items: center;
      margin-top: 8px;
    }}

    .memory-token-budget {{
      min-width: 0;
    }}

    .memory-token-meter {{
      overflow: hidden;
      height: 6px;
      border-radius: 999px;
      background: var(--track);
    }}

    .memory-token-meter-fill {{
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #34c759 0%, #5ac8fa 100%);
    }}

    .memory-token-caption {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.35;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .memory-token-mode {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}

    .memory-token-mode {{
      color: var(--slate);
      font-weight: 650;
    }}

    .panel {{
      position: relative;
      z-index: 0;
      min-width: 0;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 22px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }}

    .panel h2 {{
      font-size: 22px;
      line-height: 1.18;
    }}

    .token-panel .panel-head {{
      margin-bottom: 12px;
    }}

    .panel:has(.module-help:hover),
    .panel:has(.module-help-trigger:focus-visible),
    .panel:has(.bar-value.has-details:hover),
    .panel:has(.bar-value.has-details:focus) {{
      z-index: 70;
    }}

    .nightly-panel {{
      margin-top: 18px;
      position: relative;
      overflow: visible;
      color: var(--ink);
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 28px;
      padding: 28px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(20px);
    }}

    .nightly-panel::before,
    .nightly-panel::after {{
      display: none;
    }}

    .nightly-panel::before {{
      display: none;
    }}

    .nightly-panel::after {{
      display: none;
    }}

    .nightly-shell {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-columns: minmax(0, 1.55fr) minmax(320px, 0.9fr);
      gap: 34px;
      align-items: stretch;
    }}

    .nightly-copy {{
      display: grid;
      gap: 18px;
      align-content: start;
      min-width: 0;
    }}

    .nightly-kicker-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }}

    .nightly-kicker {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 760;
      letter-spacing: 0;
    }}

    .nightly-badge-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .nightly-badge {{
      display: inline-flex;
      align-items: center;
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--soft);
      color: var(--ink);
      font-size: 12px;
      font-weight: 600;
      line-height: 1.25;
      max-width: 100%;
      white-space: normal;
    }}

    .nightly-badge.is-teal {{
      background: rgba(52, 199, 89, 0.12);
      border-color: rgba(52, 199, 89, 0.24);
      color: var(--green);
    }}

    .nightly-badge.is-amber {{
      background: rgba(255, 159, 10, 0.14);
      border-color: rgba(255, 159, 10, 0.26);
      color: var(--amber);
    }}

    .nightly-badge.is-rose {{
      background: rgba(255, 69, 58, 0.12);
      border-color: rgba(255, 69, 58, 0.24);
      color: var(--rose);
    }}

    .nightly-badge.is-slate {{
      background: rgba(120, 120, 128, 0.12);
      border-color: rgba(120, 120, 128, 0.2);
      color: var(--slate);
    }}

    .nightly-badge.is-outline {{
      background: var(--control);
      color: var(--muted);
    }}

    .nightly-headline-row {{
      display: block;
    }}

    .nightly-title-block {{
      min-width: 0;
    }}

    .nightly-title-block h2 {{
      margin: 0;
      max-width: none;
      flex: 0 0 auto;
      color: var(--ink);
      font-size: 38px;
      font-weight: 760;
      line-height: 1.12;
    }}

    .nightly-title-main {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 18px;
      justify-content: start;
      min-width: 0;
    }}

    .nightly-note {{
      margin: 12px 0 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }}

    .nightly-date-control {{
      position: relative;
      display: inline-flex;
      align-items: center;
      gap: 12px;
      flex: 0 0 auto;
      max-width: 100%;
      min-height: 44px;
      padding: 0 42px 0 18px;
      border-radius: 999px;
      border: 1px solid var(--line-strong);
      background: var(--control);
      box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.86),
        0 8px 20px rgba(0, 0, 0, 0.06);
      cursor: pointer;
      transition: border-color 160ms ease, box-shadow 160ms ease, background 160ms ease;
      backdrop-filter: blur(18px);
    }}

    .nightly-date-control:hover {{
      border-color: rgba(0, 113, 227, 0.22);
      background: var(--control-strong);
    }}

    .nightly-date-control:focus-within {{
      border-color: rgba(0, 113, 227, 0.4);
      box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.86),
        0 0 0 4px rgba(0, 113, 227, 0.14),
        0 10px 22px rgba(0, 0, 0, 0.08);
    }}

    .nightly-date-control::after {{
      content: "";
      position: absolute;
      right: 18px;
      top: 50%;
      width: 7px;
      height: 7px;
      border-right: 1.5px solid var(--muted);
      border-bottom: 1.5px solid var(--muted);
      transform: translateY(-62%) rotate(45deg);
      pointer-events: none;
    }}

    .nightly-date-label {{
      flex: 0 0 auto;
      color: var(--muted);
      font-size: 14px;
      font-weight: 650;
      letter-spacing: 0;
    }}

    .nightly-date-input {{
      appearance: none;
      -webkit-appearance: none;
      border: 0;
      background: transparent;
      min-width: 96px;
      padding: 0;
      color: var(--ink);
      font-family: "SF Pro Display", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
      font-size: 16px;
      font-weight: 720;
      line-height: 1.2;
      font-variant-numeric: tabular-nums;
      outline: none;
      cursor: pointer;
    }}

    .nightly-date-input:focus-visible {{
      outline: none;
    }}

    .nightly-date-stage {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
    }}

    .nightly-lead {{
      margin: 0;
      max-width: 760px;
      color: var(--ink);
      font-family: inherit;
      font-size: 22px;
      font-weight: 760;
      line-height: 1.45;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .nightly-detail-list {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: 14px;
      max-width: 760px;
      min-width: 0;
    }}

    .nightly-backfill {{
      display: grid;
      gap: 10px;
      max-width: 760px;
      padding: 14px;
      border: 1px solid rgba(0, 113, 227, 0.16);
      border-radius: 16px;
      background: rgba(0, 113, 227, 0.06);
    }}

    .nightly-backfill[hidden] {{
      display: none;
    }}

    .nightly-backfill-title {{
      color: var(--ink);
      font-size: 14px;
      font-weight: 700;
      line-height: 1.35;
    }}

    .nightly-backfill-note,
    .nightly-backfill-status {{
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
    }}

    .nightly-backfill-command {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      min-width: 0;
    }}

    .nightly-backfill-label {{
      grid-column: 1 / -1;
      color: var(--slate);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.2;
    }}

    .nightly-backfill-command code {{
      min-width: 0;
      overflow: auto;
      padding: 9px 10px;
      border-radius: 10px;
      background: var(--control);
      color: var(--ink);
      font-size: 12px;
      line-height: 1.45;
      white-space: nowrap;
    }}

    .nightly-backfill-copy {{
      appearance: none;
      border: 1px solid rgba(0, 113, 227, 0.22);
      border-radius: 999px;
      background: var(--control);
      color: var(--teal);
      cursor: pointer;
      font-size: 12px;
      font-weight: 700;
      line-height: 1;
      padding: 9px 12px;
      white-space: nowrap;
    }}

    .nightly-backfill-copy:hover {{
      background: var(--control-strong);
      border-color: rgba(0, 113, 227, 0.34);
    }}

    .nightly-detail-item {{
      position: relative;
      padding-left: 28px;
      color: var(--muted);
      font-size: 17px;
      line-height: 1.58;
      overflow-wrap: anywhere;
      word-break: break-word;
      max-width: 100%;
    }}

    .nightly-detail-item::before {{
      content: "";
      position: absolute;
      left: 0;
      top: 0.72em;
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--teal);
    }}

    .nightly-context-block {{
      display: grid;
      gap: 10px;
    }}

    .nightly-context-label,
    .nightly-rail-label {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 720;
      letter-spacing: 0;
    }}

    .nightly-rail-label {{
      font-size: 16px;
      font-weight: 760;
    }}

    .nightly-context-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}

    .nightly-context-chip {{
      display: inline-flex;
      align-items: center;
      min-height: 38px;
      padding: 0 16px;
      border-radius: 999px;
      border: 1px solid rgba(0, 113, 227, 0.16);
      background: rgba(0, 113, 227, 0.08);
      color: var(--teal);
      font-size: 15px;
      font-weight: 650;
      line-height: 1;
    }}

    .nightly-rail {{
      display: grid;
      gap: 22px;
      align-content: start;
      min-width: 0;
      padding: 28px;
      border-radius: 28px;
      border: 1px solid var(--line-strong);
      background: var(--soft);
      min-height: 100%;
    }}

    .nightly-stat-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}

    .nightly-stat-card {{
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-height: 118px;
      padding: 22px;
      border-radius: 22px;
      border: 1px solid var(--line);
      background: var(--card);
      box-shadow: var(--shadow-soft);
    }}

    .nightly-stat-label {{
      display: block;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.3;
      letter-spacing: 0;
    }}

    .nightly-stat-value {{
      color: var(--ink);
      font-family: "SF Pro Display", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
      font-size: 45px;
      font-weight: 600;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }}

    .nightly-rail-note {{
      margin: 0;
      color: var(--muted);
      font-size: 16px;
      line-height: 1.58;
    }}

    .panel-head {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 16px;
    }}

    .panel-head-meta {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
      gap: 10px;
    }}

    .panel-note {{
      color: var(--muted);
      font-size: 13px;
    }}

    .module-help {{
      position: relative;
      flex: 0 0 auto;
      z-index: 6;
    }}

    .module-help:hover,
    .module-help:focus-within {{
      z-index: 80;
    }}

    .module-help-trigger {{
      appearance: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      padding: 0;
      border-radius: 999px;
      border: 1px solid var(--line-strong);
      background: var(--control);
      color: var(--slate);
      cursor: pointer;
      font-family: inherit;
      font-size: 15px;
      font-weight: 700;
      line-height: 1;
      box-shadow: 0 8px 16px rgba(0, 0, 0, 0.08);
    }}

    .module-help.is-compact .module-help-trigger {{
      width: 24px;
      height: 24px;
      font-size: 13px;
    }}

    .module-help-trigger:hover {{
      border-color: rgba(0, 113, 227, 0.24);
      color: var(--teal);
    }}

    .module-help-trigger:focus-visible {{
      outline: 2px solid rgba(0, 113, 227, 0.28);
      outline-offset: 2px;
    }}

    .module-help-card {{
      position: absolute;
      top: calc(100% + 10px);
      right: 0;
      z-index: 28;
      width: min(320px, calc(100vw - 44px));
      padding: 14px;
      border-radius: 18px;
      border: 1px solid var(--line-strong);
      background: var(--elevated);
      color: var(--ink);
      box-shadow: 0 22px 44px rgba(0, 0, 0, 0.16);
      backdrop-filter: blur(14px);
      opacity: 0;
      visibility: hidden;
      pointer-events: none;
      transform: translateY(6px);
      transition:
        opacity 120ms ease,
        transform 120ms ease,
        visibility 0s linear 120ms;
    }}

    .module-help:hover .module-help-card,
    .module-help-trigger:focus-visible + .module-help-card {{
      display: grid;
      gap: 12px;
      opacity: 1;
      visibility: visible;
      pointer-events: auto;
      transform: translateY(0);
      transition-delay: 0s;
    }}

    .module-help-card::before {{
      content: "";
      position: absolute;
      top: -7px;
      right: 10px;
      width: 12px;
      height: 12px;
      border-left: 1px solid var(--line-strong);
      border-top: 1px solid var(--line-strong);
      background: var(--elevated);
      transform: rotate(45deg);
    }}

    .metric-card .module-help-card {{
      right: auto;
      left: 0;
    }}

    .metric-card .module-help-card::before {{
      right: auto;
      left: 10px;
    }}

    .module-help-title {{
      color: var(--ink);
      font-size: 14px;
      font-weight: 700;
      line-height: 1.4;
    }}

    .module-help-sections {{
      display: grid;
      gap: 10px;
      max-height: min(60vh, 520px);
      overflow: auto;
    }}

    .module-help-section {{
      display: grid;
      gap: 6px;
    }}

    .module-help-section + .module-help-section {{
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }}

    .module-help-section-label {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: none;
      letter-spacing: 0;
    }}

    .module-help-copy {{
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.65;
      overflow-wrap: anywhere;
    }}

    .module-help-list {{
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
      display: grid;
      gap: 6px;
    }}

    .module-help-list li {{
      overflow-wrap: anywhere;
    }}

    @media (min-width: 900px) {{
      .nightly-title-row .module-help-card {{
        top: 0;
        right: auto;
        left: calc(100% + 12px);
        transform: translateX(8px);
      }}

      .nightly-title-row .module-help:hover .module-help-card,
      .nightly-title-row .module-help-trigger:focus-visible + .module-help-card {{
        transform: translateX(0);
      }}

      .nightly-title-row .module-help-card::before {{
        top: 8px;
        right: auto;
        left: -7px;
        border-top: 0;
        border-right: 0;
        border-left: 1px solid var(--line-strong);
        border-bottom: 1px solid var(--line-strong);
      }}
    }}

    .metric-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 18px;
    }}

    .metric-head .metric-label {{
      margin-bottom: 0;
    }}

    .nightly-title-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}

    .metric-card {{
      display: flex;
      flex-direction: column;
      background: var(--metric-card);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 18px;
      min-height: 136px;
      box-shadow: var(--shadow-soft);
    }}

    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0;
      margin-bottom: 18px;
    }}

    .metric-value {{
      font-family: "SF Pro Display", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
      font-size: 40px;
      font-weight: 600;
      line-height: 1;
      margin-bottom: 10px;
      font-variant-numeric: tabular-nums;
    }}

    .metric-caption {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }}

    .metric-meta {{
      min-height: 18px;
      color: var(--teal);
      font-size: 12px;
      margin-top: auto;
    }}

    .metric-footer {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 12px;
      margin-top: auto;
      min-width: 0;
    }}

    .metric-footer .metric-meta {{
      margin-top: 0;
      min-width: 0;
      overflow-wrap: anywhere;
    }}

    .token-refresh-footer {{
      padding-top: 10px;
    }}

    .token-refresh-footer .action-button {{
      flex: 0 0 auto;
      padding: 8px 13px;
      font-size: 13px;
      box-shadow: 0 10px 20px rgba(0, 113, 227, 0.16);
    }}

    .token-refresh-card-status {{
      min-height: 18px;
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
      overflow-wrap: anywhere;
    }}

    .live-metric-card.is-loading .metric-value,
    .live-metric-card.is-loading .metric-caption,
    .live-metric-card.is-loading .metric-meta {{
      opacity: 0.58;
    }}

    .bar-group {{
      display: flex;
      flex-direction: column;
      gap: 14px;
    }}

    .bar-row {{
      position: relative;
    }}

    .bar-copy {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-size: 14px;
      margin-bottom: 8px;
      line-height: 1.35;
    }}

    .bar-copy > span {{
      min-width: 0;
      overflow-wrap: anywhere;
    }}

    .bar-copy strong {{
      flex: 0 0 auto;
      font-variant-numeric: tabular-nums;
    }}

    .bar-value.has-details {{
      position: relative;
      display: inline-flex;
      align-items: center;
      justify-content: flex-end;
      min-width: 34px;
      margin: -3px -6px;
      padding: 3px 6px;
      border-radius: 8px;
      cursor: default;
      outline: none;
      transition: background 160ms ease, color 160ms ease;
    }}

    .bar-value.has-details:hover,
    .bar-value.has-details:focus {{
      background: var(--hover-bg);
      color: var(--teal);
    }}

    .bar-value.has-details:focus-visible {{
      box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.18);
    }}

    .bar-detail-popover {{
      position: absolute;
      right: 0;
      top: calc(100% + 8px);
      z-index: 36;
      width: min(320px, calc(100vw - 48px));
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--elevated);
      box-shadow: var(--shadow);
      color: var(--ink);
      font-weight: 400;
      text-align: left;
      opacity: 0;
      visibility: hidden;
      pointer-events: none;
      transform: translateY(-4px);
      transition: opacity 160ms ease, transform 160ms ease, visibility 160ms ease;
      backdrop-filter: blur(18px);
    }}

    .bar-value.has-details:hover .bar-detail-popover,
    .bar-value.has-details:focus .bar-detail-popover {{
      opacity: 1;
      visibility: visible;
      transform: translateY(0);
    }}

    .bar-detail-heading {{
      display: block;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.25;
    }}

    .bar-detail-list {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-height: 240px;
      margin: 0;
      padding: 0;
      overflow: auto;
      list-style: none;
    }}

    .bar-detail-item {{
      display: block;
    }}

    .bar-detail-title {{
      display: block;
      color: var(--ink);
      font-size: 13px;
      font-weight: 700;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }}

    .bar-detail-meta {{
      display: block;
      margin-top: 2px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 500;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }}

    .bar-track {{
      height: 8px;
      border-radius: 999px;
      background: var(--track);
      overflow: hidden;
    }}

    .bar-fill {{
      height: 100%;
      border-radius: 999px;
      transition: width 180ms ease, background 180ms ease;
    }}

    .token-panel {{
      transition: border-color 180ms ease, transform 180ms ease;
    }}

    .token-panel .bar-track {{
      background: var(--track);
    }}

    .token-panel .bar-fill {{
      box-shadow: inset 0 -1px 0 rgba(0, 0, 0, 0.08), 0 1px 2px rgba(0, 0, 0, 0.06);
    }}

    .token-panel.is-loading {{
      border-color: rgba(0, 113, 227, 0.22);
      transform: translateY(-1px);
    }}

    .token-panel.is-loading .bar-copy strong,
    .token-panel.is-loading .panel-note {{
      opacity: 0.62;
    }}

    .token-panel.is-loading .bar-track {{
      opacity: 0.55;
    }}

    .token-panel.is-loading .bar-fill {{
      animation: breathe 1.1s ease-in-out infinite alternate;
    }}

    .token-overview-panel {{
      display: grid;
      gap: 18px;
      overflow: hidden;
      transition: border-color 180ms ease, transform 180ms ease;
    }}

    .token-overview-panel.is-loading {{
      border-color: rgba(0, 113, 227, 0.22);
      transform: translateY(-1px);
    }}

    .token-summary-row {{
      display: grid;
      grid-template-columns: repeat(2, minmax(176px, 0.42fr)) minmax(420px, 1.16fr);
      gap: 18px;
      align-items: stretch;
    }}

    .token-stat-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 0;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--soft);
      overflow: hidden;
    }}

    .token-stat {{
      min-width: 0;
      padding: 16px;
      border-right: 1px solid var(--line);
    }}

    .token-stat:last-child {{
      border-right: 0;
    }}

    .token-stat-label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.25;
    }}

    .token-stat-value {{
      margin-top: 10px;
      color: var(--ink);
      font-family: "SF Pro Display", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
      font-size: 28px;
      font-weight: 650;
      line-height: 1;
      font-variant-numeric: tabular-nums;
      overflow-wrap: normal;
      white-space: nowrap;
    }}

    .token-stat.is-up .token-stat-value {{
      color: var(--rose);
    }}

    .token-stat.is-down .token-stat-value {{
      color: var(--green);
    }}

    .token-stat-caption {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }}

    .token-overview-panel.is-loading .token-stat-value,
    .token-overview-panel.is-loading .token-stat-caption {{
      opacity: 0.64;
    }}

    .teal {{
      background: linear-gradient(90deg, #0071e3, #64a8ff);
    }}

    .amber {{
      background: linear-gradient(90deg, #bf6b00, #ffb340);
    }}

    .slate {{
      background: linear-gradient(90deg, #56606a, #a1a1a6);
    }}

    .rose {{
      background: linear-gradient(90deg, #d70015, #ff6b72);
    }}

    .token-daily-high {{
      background: linear-gradient(90deg, #007aff 0%, #64d2ff 100%);
    }}

    .token-daily-mid {{
      background: linear-gradient(90deg, #30b0c7 0%, #5ac8fa 100%);
    }}

    .token-daily-low {{
      background: linear-gradient(90deg, #8e8e93 0%, #d1d1d6 100%);
    }}

    .token-daily-empty {{
      background: rgba(142, 142, 147, 0.35);
    }}

    .token-input {{
      background: linear-gradient(90deg, #007aff 0%, #64d2ff 100%);
    }}

    .token-cache {{
      background: linear-gradient(90deg, #34c759 0%, #a4f2b0 100%);
    }}

    .token-output {{
      background: linear-gradient(90deg, #ff9f0a 0%, #ffd60a 100%);
    }}

    .token-reasoning {{
      background: linear-gradient(90deg, #af52de 0%, #bf8cff 100%);
    }}

    .table-wrap {{
      overflow-x: auto;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
    }}

    th, td {{
      text-align: left;
      padding: 14px 12px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      font-size: 14px;
    }}

    th {{
      color: var(--muted);
      font-size: 12px;
      text-transform: none;
      letter-spacing: 0;
    }}

    .table-title {{
      font-weight: 700;
      margin-bottom: 6px;
    }}

    .table-subtle {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }}

    .value-score {{
      display: inline-block;
      color: var(--teal);
      font-size: 18px;
      line-height: 1.1;
      font-variant-numeric: tabular-nums;
    }}

    .review-grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }}

    .review-grid.content-more-grid {{
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    }}

    .review-panel-grid,
    .review-panel-grid.content-more-grid {{
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }}

    .memory-grid {{
      grid-template-columns: repeat(4, minmax(0, 1fr));
      align-items: start;
    }}

    .memory-grid.content-more-grid {{
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }}

    .memory-type-group {{
      grid-column: 1 / -1;
      display: grid;
      gap: 12px;
      min-width: 0;
    }}

    .memory-type-group + .memory-type-group {{
      margin-top: 4px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
    }}

    .memory-type-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      min-width: 0;
    }}

    .memory-type-head h3 {{
      margin: 0;
      font-size: 16px;
      line-height: 1.35;
    }}

    .memory-type-head span {{
      flex: 0 0 auto;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }}

    .two-up .memory-grid,
    .two-up .memory-grid.content-more-grid {{
      grid-template-columns: 1fr;
    }}

    .memory-stack .memory-grid,
    .memory-stack .memory-grid.content-more-grid {{
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }}

    .review-card {{
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 16px;
      background: var(--card);
      min-width: 0;
      overflow: hidden;
    }}

    .review-meta {{
      color: var(--muted);
      font-size: 12px;
      text-transform: none;
      letter-spacing: 0;
      margin-bottom: 8px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .review-card h3 {{
      font-size: 22px;
      margin-bottom: 8px;
      line-height: 1.35;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .review-card p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.5;
      font-size: 13px;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .review-card-links {{
      display: grid;
      gap: 8px;
      margin-top: 10px;
      min-width: 0;
    }}

    .review-card-links > div {{
      display: grid;
      gap: 4px;
      min-width: 0;
    }}

    .review-card-links > div > span {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0;
      line-height: 1.3;
    }}

    .review-card-links a {{
      width: fit-content;
      max-width: 100%;
    }}

    .review-submeta {{
      margin: -2px 0 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.6;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .memory-card-submeta {{
      display: block;
      white-space: normal;
      overflow: visible;
      text-overflow: clip;
      line-height: 1.45;
    }}

    .memory-card-submeta [data-lang-only] {{
      display: grid;
      gap: 2px;
    }}

    .memory-card-submeta-line {{
      display: block;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    .memory-card-facts {{
      margin-top: 14px;
      display: grid;
      gap: 12px;
    }}

    .memory-card-fact {{
      display: grid;
      gap: 6px;
      min-width: 0;
    }}

    .memory-card-label {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: none;
      letter-spacing: 0;
    }}

    .memory-card-value {{
      color: var(--ink);
      font-size: 13px;
      line-height: 1.65;
      display: flex;
      flex-wrap: nowrap;
      gap: 8px;
      min-width: 0;
      overflow: hidden;
    }}

    .memory-chip {{
      display: inline-flex;
      align-items: center;
      flex: 0 1 auto;
      min-width: 0;
      max-width: 100%;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid var(--line-strong);
      background: var(--chip-muted-bg);
      color: var(--ink);
      font-size: 12px;
      line-height: 1.3;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .memory-chip.is-muted {{
      color: var(--muted);
    }}

    .memory-chip-link {{
      text-decoration: none;
      transition: border-color 0.18s ease, background 0.18s ease, color 0.18s ease;
    }}

    .memory-chip-link:hover,
    .memory-chip-link:focus-visible {{
      color: var(--teal);
      border-color: rgba(0, 113, 227, 0.42);
      background: var(--accent-soft-strong);
      outline: none;
    }}

    .path-link {{
      color: var(--teal);
      text-decoration: none;
      text-underline-offset: 2px;
      border-bottom: 1px dashed rgba(0, 113, 227, 0.3);
      transition: color 0.18s ease, border-color 0.18s ease, background 0.18s ease;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .path-link:hover,
    .path-link:focus-visible {{
      color: var(--teal);
      border-bottom-color: rgba(0, 113, 227, 0.58);
      background: var(--accent-soft);
      outline: none;
    }}

    .path-link-subtle {{
      color: inherit;
      border-bottom-color: rgba(94, 103, 109, 0.28);
    }}

    .content-more,
    .memory-more {{
      grid-column: 1 / -1;
      margin-top: 2px;
    }}

    .content-more-trigger,
    .memory-more-trigger {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      list-style: none;
      color: var(--teal);
      font-size: 13px;
      font-weight: 600;
      padding: 8px 2px 2px;
    }}

    .content-more-trigger::-webkit-details-marker,
    .memory-more-trigger::-webkit-details-marker {{
      display: none;
    }}

    .content-more-trigger::before,
    .memory-more-trigger::before {{
      content: "+";
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      border-radius: 999px;
      border: 1px solid rgba(0, 113, 227, 0.22);
      background: rgba(0, 113, 227, 0.08);
      line-height: 1;
      font-size: 14px;
    }}

    .content-more[open] .content-more-trigger::before,
    .memory-more[open] .memory-more-trigger::before {{
      content: "−";
    }}

    .content-more-expanded,
    .memory-more-expanded {{
      display: none;
    }}

    .content-more[open] .content-more-collapsed,
    .memory-more[open] .memory-more-collapsed {{
      display: none;
    }}

    .content-more[open] .content-more-expanded,
    .memory-more[open] .memory-more-expanded {{
      display: inline;
    }}

    .content-more-grid,
    .memory-more-grid {{
      margin-top: 12px;
    }}

    .content-more-row > td {{
      border-bottom: 0;
      padding-top: 12px;
      padding-bottom: 0;
    }}

    .content-more-cell {{
      padding-left: 0;
      padding-right: 0;
    }}

    .content-more-button {{
      appearance: none;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 0;
      background: transparent;
      padding: 8px 2px 2px;
      color: var(--teal);
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
    }}

    .content-more-button::before {{
      content: "+";
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      border-radius: 999px;
      border: 1px solid rgba(0, 113, 227, 0.22);
      background: rgba(0, 113, 227, 0.08);
      line-height: 1;
      font-size: 14px;
    }}

    .content-more-button[aria-expanded="true"]::before {{
      content: "−";
    }}

    .content-more-extra-row[hidden] {{
      display: none;
    }}

    .content-more-table-wrap {{
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--card);
      overflow: hidden;
    }}

    .content-more-table {{
      min-width: 100%;
      background: transparent;
    }}

    .project-context-list {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      align-items: stretch;
    }}

    .context-range-control {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 16px;
    }}

    .context-range-button {{
      border: 1px solid var(--line-strong);
      border-radius: 999px;
      background: var(--control);
      color: var(--muted);
      padding: 8px 12px;
      font: inherit;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
    }}

    .context-range-button.is-active {{
      background: var(--accent-soft-strong);
      border-color: rgba(0, 113, 227, 0.28);
      color: var(--teal);
    }}

    .project-context-view[hidden] {{
      display: none;
    }}

    .project-context-view-meta {{
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 13px;
    }}

    .context-card {{
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 18px;
      background: var(--card);
      overflow: hidden;
    }}

    .context-card-head {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
    }}

    .context-card-copy {{
      min-width: 0;
      flex: 1 1 auto;
    }}

    .context-card-meta {{
      color: var(--muted);
      font-size: 12px;
      text-transform: none;
      letter-spacing: 0;
      margin-bottom: 8px;
    }}

    .context-card h3 {{
      margin: 0;
      font-size: 22px;
    }}

    .context-card-stats {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }}

    .context-badge {{
      padding: 8px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--soft);
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}

    .context-card-facts {{
      display: grid;
      gap: 12px;
      margin: 16px 0 0;
    }}

    .context-card-fact {{
      padding-top: 12px;
      border-top: 1px solid var(--line);
    }}

    .context-card-fact:first-child {{
      padding-top: 0;
      border-top: 0;
    }}

    .context-card-fact dt {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: none;
    }}

    .context-card-fact dd {{
      margin: 6px 0 0;
      color: var(--ink);
      font-size: 14px;
      line-height: 1.65;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .context-card-tags {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
      flex-wrap: wrap;
    }}

    .context-card-tags .context-card-kicker {{
      line-height: 1;
    }}

    .context-card-kicker {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: none;
    }}

    .context-chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      min-width: 0;
    }}

    .context-chip {{
      display: inline-flex;
      align-items: center;
      padding: 7px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--teal);
      font-size: 12px;
      line-height: 1;
      max-width: 100%;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .context-chip.is-muted {{
      background: var(--track);
      color: var(--muted);
    }}

    .context-topic-block {{
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
    }}

    .context-topic-list {{
      display: grid;
      gap: 10px;
      margin-top: 10px;
    }}

    .context-topic {{
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--soft);
      padding: 12px;
    }}

    .context-topic-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
    }}

    .context-topic-meta {{
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0;
      text-transform: none;
      margin-bottom: 4px;
    }}

    .context-topic h4 {{
      margin: 0;
      font-size: 15px;
      line-height: 1.35;
    }}

    .context-topic-count {{
      flex: 0 0 auto;
      border-radius: 999px;
      background: var(--control);
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      padding: 6px 9px;
      white-space: nowrap;
    }}

    .context-topic p {{
      margin: 0 0 8px;
      color: var(--ink);
      font-size: 13px;
      line-height: 1.55;
      overflow-wrap: anywhere;
    }}

    .context-topic-takeaway {{
      color: var(--muted) !important;
    }}

    .context-topic-more {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 10px;
    }}

    .window-summary-list {{
      display: grid;
      gap: 14px;
      grid-template-columns: 1fr;
    }}

    .window-card {{
      border: 1px solid var(--line);
      border-radius: 20px;
      background: var(--card);
      overflow: hidden;
    }}

    .window-card[open] {{
      border-color: rgba(0, 113, 227, 0.24);
      background: var(--card);
    }}

    .window-card-trigger {{
      display: block;
      padding: 18px;
      cursor: pointer;
      list-style: none;
    }}

    .window-card-trigger::-webkit-details-marker {{
      display: none;
    }}

    .window-card-head {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
    }}

    .window-card-copy {{
      min-width: 0;
      flex: 1 1 auto;
    }}

    .window-card-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: none;
      letter-spacing: 0;
      margin-bottom: 8px;
    }}

    .window-card-path {{
      margin: 0;
      color: var(--muted);
      line-height: 1.5;
      font-size: 13px;
      word-break: break-all;
    }}

    .window-card-stats {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      justify-content: flex-end;
    }}

    .window-stat {{
      min-width: 76px;
      padding: 10px 12px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: var(--soft);
      text-align: center;
    }}

    .window-stat strong {{
      display: block;
      font-size: 20px;
      line-height: 1;
      margin-bottom: 4px;
    }}

    .window-stat span {{
      color: var(--muted);
      font-size: 12px;
    }}

    .window-card-summary {{
      margin: 16px 0 0;
      color: var(--ink);
      line-height: 1.65;
      font-size: 14px;
    }}

    .window-card-meta {{
      margin-top: 14px;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 10px 16px;
      color: var(--muted);
      font-size: 12px;
    }}

    .window-card-time {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}

    .window-card-time::before {{
      content: "";
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--teal);
      opacity: 0.8;
    }}

    .window-card-action {{
      color: var(--teal);
      font-weight: 600;
    }}

    .window-card-action-expanded {{
      display: none;
    }}

    .window-card[open] .window-card-action-collapsed {{
      display: none;
    }}

    .window-card[open] .window-card-action-expanded {{
      display: inline;
    }}

    .window-card-detail {{
      border-top: 1px solid var(--line);
      padding: 0 18px 18px;
    }}

    .window-detail-grid {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      margin-top: 16px;
    }}

    .window-detail-grid.compact {{
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    }}

    .window-detail-block {{
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--soft);
      padding: 14px;
      min-width: 0;
    }}

    .window-detail-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: none;
      letter-spacing: 0;
      margin-bottom: 10px;
    }}

    .window-detail-block p {{
      margin: 0;
      color: var(--ink);
      line-height: 1.65;
      font-size: 13px;
    }}

    .window-detail-list {{
      margin: 0;
      padding: 0;
      list-style: none;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}

    .window-detail-item {{
      display: flex;
      flex-direction: column;
      gap: 4px;
      color: var(--ink);
      font-size: 13px;
      line-height: 1.55;
    }}

    .window-detail-time {{
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0;
    }}

    .window-keyword-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .window-keyword {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      background: var(--control);
      border: 1px solid var(--line);
      padding: 6px 10px;
      color: var(--ink);
      font-size: 12px;
    }}

    .window-keyword.empty-keyword {{
      color: var(--muted);
    }}

    .term-cloud-area {{
      display: flex;
      flex-direction: column;
      gap: 14px;
    }}

    .term-range-control {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .term-range-button {{
      border: 1px solid var(--line-strong);
      border-radius: 999px;
      background: var(--control);
      color: var(--muted);
      padding: 8px 12px;
      font: inherit;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
    }}

    .term-range-button.is-active {{
      background: var(--accent-soft-strong);
      border-color: rgba(0, 113, 227, 0.28);
      color: var(--teal);
    }}

    .term-cloud-view[hidden] {{
      display: none;
    }}

    .term-cloud-meta {{
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 13px;
    }}

    .term-cloud {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: flex-start;
    }}

    .term-chip {{
      display: inline-flex;
      align-items: baseline;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 999px;
      background: var(--control);
      border: 1px solid var(--line-strong);
      line-height: 1;
      color: var(--ink);
    }}

    .term-chip em {{
      font-style: normal;
      font-size: 0.7em;
      color: var(--muted);
    }}

    .highlight-list {{
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      line-height: 1.8;
    }}

    .guide-list {{
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      line-height: 1.7;
    }}

    .empty, .empty-cell {{
      color: var(--muted);
    }}

    @keyframes spin {{
      to {{
        transform: rotate(360deg);
      }}
    }}

    @keyframes pulse {{
      0% {{
        box-shadow: 0 0 0 0 rgba(0, 113, 227, 0.28);
      }}

      70% {{
        box-shadow: 0 0 0 10px rgba(0, 113, 227, 0);
      }}

      100% {{
        box-shadow: 0 0 0 0 rgba(0, 113, 227, 0);
      }}
    }}

    @keyframes breathe {{
      from {{
        transform: scaleX(0.97);
      }}

      to {{
        transform: scaleX(1);
      }}
    }}

    @media (max-width: 1784px) {{
      .app-shell {{
        width: min(1280px, calc(100% - 304px));
        margin-left: 264px;
        margin-right: 24px;
      }}

      .side-nav {{
        top: 24px;
        left: 12px;
        width: 212px;
        max-height: calc(100vh - 48px);
        padding: 12px;
        border-radius: 18px;
      }}

      .side-nav-title {{
        display: block;
        margin: 0 4px 8px;
        text-align: left;
        font-size: 11px;
      }}

      .side-nav-link {{
        grid-template-columns: minmax(0, 1fr);
        justify-items: start;
        padding: 9px 9px 9px 13px;
        border-radius: 11px;
        font-size: 12px;
      }}

      .side-nav-group {{
        margin: 10px 8px 3px;
        font-size: 10px;
      }}

      .side-nav-label {{
        position: static;
        max-width: none;
        padding: 0;
        border: 0;
        border-radius: 0;
        background: transparent;
        box-shadow: none;
        color: inherit;
        opacity: 1;
        pointer-events: auto;
        transform: none;
      }}

      .side-nav-link.is-child {{
        margin-left: 10px;
        padding: 7px 9px 7px 13px;
        font-size: 11px;
      }}
    }}

    @media (max-width: 1120px) {{
      .app-shell {{
        width: min(1280px, calc(100% - 28px));
        margin: 0 auto;
        padding-top: 14px;
      }}

      .side-nav {{
        position: sticky;
        top: 0;
        left: auto;
        right: auto;
        width: min(1280px, calc(100% - 28px));
        max-height: none;
        margin: 14px auto -6px;
        padding: 10px;
        border-radius: 18px;
        overflow-x: auto;
      }}

      .side-nav-list {{
        display: flex;
        gap: 6px;
        white-space: nowrap;
      }}

      .side-nav-group {{
        display: none;
      }}

      .side-nav-link {{
        flex: 0 0 auto;
        grid-template-columns: auto;
        justify-items: start;
        padding: 9px 10px;
      }}

      .side-nav-link.is-child {{
        margin-left: 0;
        font-size: 12px;
      }}

      .side-nav-link::before {{
        display: none;
      }}

      .side-nav-label {{
        position: static;
        max-width: none;
        padding: 0;
        border: 0;
        border-radius: 0;
        background: transparent;
        box-shadow: none;
        color: inherit;
        opacity: 1;
        pointer-events: auto;
        transform: none;
      }}

      .page [id] {{
        scroll-margin-top: 82px;
      }}
    }}

    @media (max-width: 1040px) {{
      .token-summary-row {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .token-overview-panel {{
        grid-column: 1 / -1;
      }}

      .token-stat-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .token-stat:nth-child(2n) {{
        border-right: 0;
      }}

      .token-stat:nth-child(-n + 2) {{
        border-bottom: 1px solid var(--line);
      }}

      .memory-grid,
      .memory-grid.content-more-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .review-panel-grid,
      .review-panel-grid.content-more-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .two-up .memory-grid,
      .two-up .memory-grid.content-more-grid {{
        grid-template-columns: 1fr;
      }}

      .memory-family-title-row.has-extra {{
        grid-template-columns: 1fr;
      }}
    }}

    @media (max-width: 720px) {{
      .app-shell {{
        width: min(1280px, calc(100% - 28px));
        padding: 20px 0 40px;
      }}

      .side-nav {{
        width: min(1280px, calc(100% - 28px));
      }}

      .hero {{
        padding: 20px 18px;
      }}

      h1 {{
        font-size: 30px;
      }}

      .hero-topline {{
        flex-direction: column;
      }}

      .hero-actions {{
        width: 100%;
        justify-content: flex-start;
      }}

      .panel {{
        padding: 18px;
      }}

      .panel h2 {{
        font-size: 21px;
      }}

      .nightly-panel {{
        padding: 18px;
        border-radius: 20px;
      }}

      .token-summary-row {{
        grid-template-columns: 1fr;
      }}

      .token-overview-panel {{
        grid-column: auto;
      }}

      .token-stat-grid {{
        grid-template-columns: 1fr;
      }}

      .token-stat {{
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}

      .token-stat:last-child {{
        border-bottom: 0;
      }}

      .panel-head-meta {{
        width: 100%;
        justify-content: space-between;
      }}

      .module-help-card {{
        width: min(320px, calc(100vw - 32px));
      }}

      .nightly-shell {{
        grid-template-columns: 1fr;
      }}

      .nightly-kicker-row {{
        align-items: flex-start;
        flex-direction: column;
      }}

      .nightly-badge-row {{
        width: 100%;
      }}

      .nightly-headline-row {{
        flex-direction: column;
      }}

      .nightly-title-row {{
        width: 100%;
        align-items: flex-start;
      }}

      .nightly-title-main {{
        align-items: flex-start;
        gap: 10px;
      }}

      .nightly-date-control {{
        min-width: 0;
        width: fit-content;
      }}

      .nightly-title-block h2 {{
        max-width: none;
        font-size: 28px;
      }}

      .nightly-lead {{
        font-size: 18px;
        line-height: 1.45;
      }}

      .nightly-detail-item {{
        font-size: 15px;
      }}

      .nightly-rail {{
        padding: 18px;
        border-radius: 18px;
      }}

      th, td {{
        padding: 12px 8px;
      }}

      .memory-family-head h2 {{
        font-size: 24px;
      }}

      .memory-family-title-row {{
        display: block;
      }}

      .memory-family-title-row.has-extra {{
        grid-template-columns: 1fr;
        gap: 12px;
      }}

      .memory-token-widget {{
        width: 100%;
        max-width: none;
      }}

      .memory-count-widget {{
        width: 100%;
        max-width: none;
      }}

      .memory-count-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .memory-token-main {{
        grid-template-columns: 1fr;
        gap: 8px;
      }}

      .memory-token-caption {{
        white-space: normal;
      }}

      .memory-grid,
      .memory-grid.content-more-grid {{
        grid-template-columns: 1fr;
      }}

      .review-panel-grid,
      .review-panel-grid.content-more-grid {{
        grid-template-columns: 1fr;
      }}

      .context-card-head {{
        flex-direction: column;
      }}

      .context-card-stats {{
        justify-content: flex-start;
      }}

      .window-card-head {{
        flex-direction: column;
      }}

      .window-card-stats {{
        justify-content: flex-start;
      }}
    }}
  </style>
</head>
<body data-language="{default_language}" data-theme-choice="system">
  {side_nav}
  <div class="app-shell">
  <main class="page">
    <section class="hero" id="overview-top">
      <div class="hero-topline">
        <div class="hero-title-block">
          <p class="eyebrow">{hero_eyebrow}</p>
          <div class="hero-heading-row">
            <h1>{hero_title}</h1>
            <span class="hero-brand-line">{hero_brand_line}</span>
          </div>
          <p class="hero-copy">
            {hero_copy}
          </p>
        </div>
        <div class="hero-actions">
          {theme_switch}
          {language_switch}
          {github_button}
        </div>
      </div>
      <div class="hero-meta">
        <span class="chip">{snapshot_label}{generated_at} · <span id="snapshot-generated-age">刚刚生成</span></span>
      </div>
    </section>

    {nightly_summary_panel}

    <section class="grid token-summary-row" id="token-section">
      {token_metric_cards}
      {token_overview_panel}
    </section>

    <section class="grid two-up">
      {daily_token_panel}
      {today_token_panel}
    </section>

    {insight_section_html}

    <section class="panel" id="project-context-section">
      {project_context_header}
      {project_context_body}
    </section>

    <section class="memory-family" id="memory-section">
      {personal_asset_memory_family_header}
      <section class="grid memory-stack">
        <section class="panel" id="personal-memory-durable-section">
          {durable_memory_header}
          <div class="review-grid memory-grid">
            {durable_memory_cards}
          </div>
        </section>

        <section class="panel" id="personal-memory-session-section">
          {session_memory_header}
          <div class="review-grid memory-grid">
            {session_memory_cards}
          </div>
        </section>
      </section>

      <section class="panel" id="personal-memory-low-priority-section">
        {low_priority_memory_header}
        <div class="review-grid memory-grid">
          {low_priority_memory_cards}
        </div>
      </section>

    </section>

    <section class="memory-family" id="codex-native-section">
      {codex_native_memory_family_header}
      <section class="panel" id="codex-native-topic-section">
        {codex_native_topic_header}
        <div class="review-grid memory-grid">
          {codex_native_topic_cards}
        </div>
      </section>

      <section class="panel" id="codex-native-preference-section">
        {codex_native_preference_header}
        <div class="review-grid memory-grid">
          {codex_native_preference_cards}
        </div>
      </section>

      <section class="panel" id="codex-native-tip-section">
        {codex_native_tip_header}
        <div class="review-grid memory-grid">
          {codex_native_tip_cards}
        </div>
      </section>

      <section class="panel" id="codex-native-task-group-section">
        {codex_native_task_group_header}
        <div class="review-grid memory-grid">
          {codex_native_task_group_cards}
        </div>
      </section>
    </section>

    <section class="asset-ledger-section" id="asset-overview-section">
      <div class="memory-family-head asset-ledger-head">
        <div class="memory-family-title-copy">
          <p class="section-kicker">{asset_ledger_kicker}</p>
          <h2>{asset_ledger_title}</h2>
          <p class="memory-family-note">{asset_ledger_note}</p>
        </div>
      </div>
      <section class="grid metrics-grid asset-metrics-grid">
        {asset_metric_cards}
      </section>
    </section>

    <section class="grid two-up">
      {type_panel}
      {month_panel}
    </section>

    <section class="grid two-up">
      {scope_panel}
      {domain_panel}
    </section>

    <section class="panel" id="assets-section">
      {recent_assets_header}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
	              <th>{asset_header}</th>
	              <th>{type_header}</th>
	              <th>{context_header}</th>
	              <th>{scope_header}</th>
	              <th>{updated_header}</th>
	              <th>{usage_header_text}</th>
            </tr>
          </thead>
          <tbody>
            {recent_asset_rows}
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" id="top-assets-section">
      {top_assets_header}
      <div class="table-wrap">
        <table>
          <thead>
          <tr>
	            <th>{asset_header}</th>
	            <th>{value_score_header}</th>
	            <th>{estimated_saved_header}</th>
	            <th>{evidence_header}</th>
          </tr>
          </thead>
          <tbody>
            {top_asset_rows}
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" id="reviews-section">
      {reviews_header}
      <div class="review-grid review-panel-grid">
        {review_cards}
      </div>
    </section>

    <section class="panel">
      {usage_header}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
	              <th>{date_header}</th>
	              <th>{asset_id_header}</th>
	              <th>{task_header}</th>
	              <th>{minutes_saved_header}</th>
            </tr>
          </thead>
          <tbody>
            {usage_rows}
          </tbody>
        </table>
      </div>
    </section>

    <section class="grid" id="window-overview-section">
      <section class="panel window-overview-panel" id="window-overview-panel">
        {window_overview_header}
        <div class="window-summary-list" id="window-summary-list">
          {nightly_window_cards}
        </div>
      </section>
    </section>

    <footer class="panel-footer">
      <div>{panel_footer_notice}</div>
    </footer>
  </main>
  </div>
  <script>
    (function () {{
      const snapshot = {snapshot_payload};
      const translations = {panel_i18n_json};
      const defaultLanguage = "{default_language}";
      const supportedLanguages = ["zh", "en"];
      const supportedThemes = ["system", "light", "dark"];
      const themeStorageKey = "openrelix-panel-theme";
      const systemDarkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
      let currentLanguage = defaultLanguage;
      let currentThemeChoice = "system";
      const config = {{
        autoReloadMs: {auto_refresh_ms},
        liveEndpoint: {live_token_endpoint},
        livePollMs: {live_token_poll_ms},
        requestTimeoutMs: {live_token_timeout_ms},
      }};
      const state = {{
        tokenUsage: snapshot.token_usage || null,
        tokenRefreshedAt: (snapshot.token_usage && snapshot.token_usage.refreshed_at) || "",
        tokenSourceKind: "snapshot",
        selectedNightlyDate: snapshot.daily_summary_default_date || "",
        selectedWindowOverviewDate: snapshot.window_overview_default_date || "",
        refreshStatusKind: "",
        refreshStatusMessageKey: "",
      }};
      const elements = {{
        snapshotAge: document.getElementById("snapshot-generated-age"),
        nightlyDateInput: document.getElementById("nightly-date-input"),
        windowOverviewDateInput: document.getElementById("window-overview-date-input"),
        windowOverviewTitle: document.getElementById("window-overview-title"),
        windowOverviewNote: document.getElementById("window-overview-note"),
        windowSummaryList: document.getElementById("window-summary-list"),
        nightlyBadgeRow: document.getElementById("nightly-badge-row"),
        nightlyLead: document.getElementById("nightly-lead"),
        nightlyDetailList: document.getElementById("nightly-detail-list"),
        nightlyContextBlock: document.getElementById("nightly-context-block"),
        nightlyContextRow: document.getElementById("nightly-context-row"),
        nightlyStatGrid: document.getElementById("nightly-stat-grid"),
        nightlyRailNote: document.getElementById("nightly-rail-note"),
        backfillPanel: document.getElementById("nightly-backfill-panel"),
        backfillNote: document.getElementById("nightly-backfill-note"),
        backfillSingleCommand: document.getElementById("nightly-backfill-single-command"),
        backfillRange: document.getElementById("nightly-backfill-range"),
        backfillRangeCommand: document.getElementById("nightly-backfill-range-command"),
        backfillStatus: document.getElementById("nightly-backfill-status"),
        backfillCopyButtons: Array.from(document.querySelectorAll("[data-backfill-copy]")),
        refreshButton: document.getElementById("token-refresh-button"),
        refreshLabel: document.getElementById("token-refresh-label"),
        refreshStatusText: document.getElementById("token-refresh-status-text"),
        tokenHighlight: document.getElementById("token-highlight"),
        tokenOverviewPanel: document.getElementById("token-overview-panel"),
        tokenOverviewNote: document.getElementById("token-overview-note"),
        tokenSummaryCards: document.getElementById("token-summary-cards"),
        dailyTokenPanel: document.getElementById("daily-token-panel"),
        dailyTokenNote: document.getElementById("daily-token-note"),
        dailyTokenRows: document.getElementById("daily-token-rows"),
        todayTokenPanel: document.getElementById("today-token-panel"),
        todayTokenNote: document.getElementById("today-token-note"),
        todayTokenRows: document.getElementById("today-token-rows"),
        sideNavLinks: Array.from(document.querySelectorAll("[data-nav-target]")),
      }};
      const liveCards = Array.from(document.querySelectorAll("[data-live-card='true']"));

      function escapeHtml(value) {{
        return String(value)
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;");
      }}

      function pluralEn(count, singular, plural) {{
        const number = Number(count) || 0;
        const word = number === 1 ? singular : (plural || singular + "s");
        return number + " " + word;
      }}

      function dynamicTranslation(key) {{
        const text = String(key || "");
        let match = text.match(/^快照时间 (.+)$/);
        if (match) {{
          return "Snapshot time " + match[1];
        }}
        match = text.match(/^(.+) 的总消耗$/);
        if (match) {{
          return "Total for " + match[1];
        }}
        match = text.match(/^原始记录分钟数 (\\d+)$/);
        if (match) {{
          return "Recorded minutes " + match[1];
        }}
        match = text.match(/^占输入 (.+)$/);
        if (match) {{
          return match[1] + " of input";
        }}
        match = text.match(/^占总量 (.+)$/);
        if (match) {{
          return match[1] + " of total";
        }}
        match = text.match(/^费用估算：\\$(.+)$/);
        if (match) {{
          return "Estimated cost: $" + match[1];
        }}
        match = text.match(/^(.+) · 未整理$/);
        if (match) {{
          return match[1] + " · Not synthesized";
        }}
        match = text.match(/^(?:当日|每日)窗口概览 · (\\d+)$/);
        if (match) {{
          return "Daily Window Overview · " + match[1];
        }}
        match = text.match(/^昨夜窗口概览 · (\\d+)$/);
        if (match) {{
          return "Last Night's Window Overview · " + match[1];
        }}
        match = text.match(/^最近一次窗口概览 · (\\d+)$/);
        if (match) {{
          return "Latest Window Overview · " + match[1];
        }}
        match = text.match(/^未检测到 (.+)。$/);
        if (match) {{
          return match[1] + " not found.";
        }}
        match = text.match(/^最近 (\\d+) 天$/);
        if (match) {{
          return "Last " + pluralEn(match[1], "day");
        }}
        match = text.match(/^(\\d+) 个窗口$/);
        if (match) {{
          return pluralEn(match[1], "window");
        }}
        match = text.match(/^(\\d+) 窗口$/);
        if (match) {{
          return pluralEn(match[1], "window");
        }}
        match = text.match(/^(\\d+) 个问题$/);
        if (match) {{
          return pluralEn(match[1], "question");
        }}
        match = text.match(/^(\\d+) 个结论$/);
        if (match) {{
          return pluralEn(match[1], "conclusion");
        }}
        match = text.match(/^(\\d+) 个主题$/);
        if (match) {{
          return pluralEn(match[1], "topic");
        }}
        match = text.match(/^扫描 (\\d+) 天 · 有窗口日期 (\\d+) 天 · (\\d+) 个窗口 · (.+)$/);
        if (match) {{
          return "Scanned " + pluralEn(match[1], "day") +
            " · " + pluralEn(match[2], "source date") +
            " · " + pluralEn(match[3], "window") +
            " · " + match[4];
        }}
        match = text.match(/^直接读取 (.+) 的“What's in Memory”主题项。$/);
        if (match) {{
          return 'Reads topic items from the "What\\'s in Memory" section of ' + match[1] + ".";
        }}
        match = text.match(/^主题项 (\\d+) 条；用户偏好 (\\d+) 条；通用 tips (\\d+) 条。$/);
        if (match) {{
          return pluralEn(match[1], "topic item") + "; " +
            pluralEn(match[2], "user preference") + "; " +
            pluralEn(match[3], "general tip") + ".";
        }}
        return "";
      }}

      function t(value) {{
        const key = String(value || "");
        if (currentLanguage === "en") {{
          return translations[key] || dynamicTranslation(key) || key;
        }}
        return key;
      }}

      function translateAttributeValue(value) {{
        const key = String(value || "");
        if (!key || currentLanguage !== "en") {{
          return key;
        }}
        const direct = translations[key] || dynamicTranslation(key);
        if (direct) {{
          return direct;
        }}
        const helpMatch = key.match(/^(.+)\\s+说明$/);
        if (helpMatch) {{
          return t(helpMatch[1]) + " " + t("说明");
        }}
        return key;
      }}

      function translateStaticAttributes() {{
        document.querySelectorAll("[aria-label], [title]").forEach(function (element) {{
          ["aria-label", "title"].forEach(function (attr) {{
            if (!element.hasAttribute(attr)) {{
              return;
            }}
            const storeAttr = "data-i18n-original-" + attr;
            if (!element.hasAttribute(storeAttr)) {{
              element.setAttribute(storeAttr, element.getAttribute(attr) || "");
            }}
            const originalValue = element.getAttribute(storeAttr) || "";
            element.setAttribute(attr, translateAttributeValue(originalValue));
          }});
        }});
      }}

      function tokenTotalDisplay(tokenUsage, rawKey, displayKey) {{
        const rawValue = tokenUsage ? tokenUsage[rawKey] : null;
        const numericValue = Number(rawValue);
        if (rawValue !== null && rawValue !== undefined && rawValue !== "" && Number.isFinite(numericValue)) {{
          return compactTokenValue(numericValue);
        }}
        return tokenUsage && tokenUsage[displayKey] ? tokenUsage[displayKey] : "—";
      }}

      function tokenBreakdownLabel(rawLabel) {{
        const normalized = String(rawLabel || "").toLowerCase();
        if (normalized.includes("缓存") || normalized.includes("cached")) {{
          return currentLanguage === "en" ? "Cached Input" : "缓存输入";
        }}
        if (normalized.includes("推理") || normalized.includes("reasoning")) {{
          return currentLanguage === "en" ? "Reasoning Output" : "推理输出";
        }}
        if (normalized.includes("输出") || normalized.includes("output")) {{
          return currentLanguage === "en" ? "Output" : "输出";
        }}
        if (normalized.includes("输入") || normalized.includes("input")) {{
          return currentLanguage === "en" ? "Input" : "输入";
        }}
        return t(rawLabel || "");
      }}

      function localizeTokenDetailsHeading(value) {{
        const text = String(value || "");
        const zhMatch = text.match(/^(.+) Token 构成$/);
        if (currentLanguage === "en" && zhMatch) {{
          return "Token breakdown for " + zhMatch[1];
        }}
        const enMatch = text.match(/^Token breakdown for (.+)$/i);
        if (currentLanguage !== "en" && enMatch) {{
          return enMatch[1] + " Token 构成";
        }}
        return t(text);
      }}

      function tokenDetailTitle(detail) {{
        if (!detail || typeof detail !== "object") {{
          return String(detail || "");
        }}
        if (detail.value !== null && detail.value !== undefined && detail.value !== "") {{
          return tokenBreakdownLabel(detail.label || detail.title || "") +
            (currentLanguage === "en" ? ": " : "：") +
            compactTokenValue(detail.value);
        }}
        return t(detail.title || "");
      }}

      function translateStaticText() {{
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        const nodes = [];
        while (walker.nextNode()) {{
          const node = walker.currentNode;
          const rawValue = node.nodeValue || "";
          const trimmedValue = rawValue.trim();
          const key = node.__i18nKey || trimmedValue;
          const translatedValue = translations[key] || dynamicTranslation(key) || "";
          if (!key || (!translatedValue && !node.__i18nKey)) {{
            continue;
          }}
          if (!node.__i18nKey) {{
            node.__i18nKey = key;
            node.__i18nPrefix = rawValue.match(/^\\s*/)[0];
            node.__i18nSuffix = rawValue.match(/\\s*$/)[0];
          }}
          nodes.push(node);
        }}
        nodes.forEach(function (node) {{
          const nextValue = currentLanguage === "en"
            ? (translations[node.__i18nKey] || dynamicTranslation(node.__i18nKey) || node.__i18nKey)
            : node.__i18nKey;
          node.nodeValue = (node.__i18nPrefix || "") + nextValue + (node.__i18nSuffix || "");
        }});
      }}

      function findDailySummary(dateValue) {{
        const summaries = Array.isArray(snapshot.daily_summaries) ? snapshot.daily_summaries : [];
        return summaries.find(function (summary) {{
          return summary && summary.date === dateValue;
        }}) || null;
      }}

      function findWindowOverview(dateValue) {{
        const views = Array.isArray(snapshot.window_overviews) ? snapshot.window_overviews : [];
        return views.find(function (view) {{
          return view && view.date === dateValue;
        }}) || null;
      }}

      function getLocalizedWindowOverviewText(view, key) {{
        if (!view) {{
          return "";
        }}
        const localizedKey = currentLanguage === "en" ? key + "_en" : key + "_zh";
        return view[localizedKey] || view[key] || "";
      }}

      function renderWindowOverview(dateValue) {{
        const view = findWindowOverview(dateValue);
        state.selectedWindowOverviewDate = dateValue || state.selectedWindowOverviewDate;
        if (elements.windowOverviewDateInput && elements.windowOverviewDateInput.value !== dateValue) {{
          elements.windowOverviewDateInput.value = dateValue || "";
        }}
        if (!view) {{
          if (elements.windowOverviewTitle) {{
            elements.windowOverviewTitle.textContent = t("当日窗口概览");
          }}
          if (elements.windowOverviewNote) {{
            elements.windowOverviewNote.textContent = t("该日期暂无窗口整理结果。");
          }}
          if (elements.windowSummaryList) {{
            elements.windowSummaryList.innerHTML = '<p class="empty">' + escapeHtml(t("暂无窗口整理结果。")) + '</p>';
          }}
          return;
        }}
        if (elements.windowOverviewTitle) {{
          elements.windowOverviewTitle.textContent = getLocalizedWindowOverviewText(view, "heading");
        }}
        if (elements.windowOverviewNote) {{
          elements.windowOverviewNote.textContent = getLocalizedWindowOverviewText(view, "note");
        }}
        if (elements.windowSummaryList) {{
          elements.windowSummaryList.innerHTML = currentLanguage === "en"
            ? (view.cards_html_en || view.cards_html || "")
            : (view.cards_html_zh || view.cards_html || "");
        }}
      }}

      function renderNightlyBadges(summary) {{
        if (!elements.nightlyBadgeRow) {{
          return;
        }}
        const badges = summary && Array.isArray(summary.badges) ? summary.badges : [];
        elements.nightlyBadgeRow.innerHTML = badges.map(function (badge) {{
          const rawTone = String((badge && badge.tone) || "slate");
          const tone = rawTone.replace(/[^a-z0-9_-]/gi, "") || "slate";
          const label = badge && badge.label ? t(badge.label) : "";
          return '<span class="nightly-badge is-' + tone + '">' + escapeHtml(label) + '</span>';
        }}).join("");
        elements.nightlyBadgeRow.hidden = !badges.length;
      }}

      function getLocalizedSummaryText(summary, key) {{
        if (!summary) {{
          return "";
        }}
        const localizedKey = currentLanguage === "en" ? key + "_en" : key + "_zh";
        const value = summary[localizedKey] || summary[key] || "";
        return t(value);
      }}

      function getLocalizedSummaryList(summary, key) {{
        if (!summary) {{
          return [];
        }}
        const localizedKey = currentLanguage === "en" ? key + "_en" : key + "_zh";
        const values = Array.isArray(summary[localizedKey])
          ? summary[localizedKey]
          : (Array.isArray(summary[key]) ? summary[key] : []);
        return values.map(function (value) {{
          return t(value || "");
        }});
      }}

      function backfillState() {{
        return snapshot.backfill && typeof snapshot.backfill === "object" ? snapshot.backfill : {{}};
      }}

      function missingBackfillDates() {{
        const value = backfillState().missing_dates;
        return Array.isArray(value) ? value : [];
      }}

      function commandForBackfillDate(dateValue) {{
        const backfill = backfillState();
        const commands = backfill.commands_by_date && typeof backfill.commands_by_date === "object"
          ? backfill.commands_by_date
          : {{}};
        if (commands[dateValue]) {{
          return commands[dateValue];
        }}
        if (!dateValue) {{
          return "";
        }}
        const days = backfill.learn_window_days || 7;
        return "openrelix backfill --from " + dateValue + " --to " + dateValue + " --stage final --learn-window-days " + days;
      }}

      function renderBackfillPanel(dateValue, hasSummary) {{
        if (!elements.backfillPanel) {{
          return;
        }}
        const missingDates = missingBackfillDates();
        const shouldShow = Boolean(dateValue) && !hasSummary && missingDates.includes(dateValue);
        elements.backfillPanel.hidden = !shouldShow;
        if (!shouldShow) {{
          if (elements.backfillStatus) {{
            elements.backfillStatus.textContent = "";
          }}
          return;
        }}
        const singleCommand = commandForBackfillDate(dateValue);
        const rangeCommand = backfillState().range_command || "";
        if (elements.backfillNote) {{
          elements.backfillNote.textContent = t("该日期还没有整理结果。可以复制命令在终端手动回溯。");
        }}
        if (elements.backfillSingleCommand) {{
          elements.backfillSingleCommand.textContent = singleCommand;
        }}
        if (elements.backfillRangeCommand) {{
          elements.backfillRangeCommand.textContent = rangeCommand;
        }}
        if (elements.backfillRange) {{
          elements.backfillRange.hidden = !rangeCommand || rangeCommand === singleCommand;
        }}
        if (elements.backfillStatus) {{
          elements.backfillStatus.textContent = "";
        }}
      }}

      function copyText(value) {{
        if (!value) {{
          return Promise.reject(new Error("empty"));
        }}
        if (navigator.clipboard && navigator.clipboard.writeText) {{
          return navigator.clipboard.writeText(value);
        }}
        return new Promise(function (resolve, reject) {{
          const textarea = document.createElement("textarea");
          textarea.value = value;
          textarea.setAttribute("readonly", "readonly");
          textarea.style.position = "fixed";
          textarea.style.top = "-1000px";
          document.body.appendChild(textarea);
          textarea.select();
          try {{
            const ok = document.execCommand("copy");
            document.body.removeChild(textarea);
            ok ? resolve() : reject(new Error("copy_failed"));
          }} catch (error) {{
            document.body.removeChild(textarea);
            reject(error);
          }}
        }});
      }}

      function renderNightlySummary(dateValue) {{
        const summary = findDailySummary(dateValue);
        state.selectedNightlyDate = dateValue || state.selectedNightlyDate;
        if (elements.nightlyDateInput && elements.nightlyDateInput.value !== dateValue) {{
          elements.nightlyDateInput.value = dateValue || "";
        }}
        if (!summary) {{
          renderNightlyBadges(null);
          renderBackfillPanel(dateValue, false);
          if (elements.nightlyLead) {{
            elements.nightlyLead.textContent = t("该日期暂无整理结果。");
          }}
          if (elements.nightlyDetailList) {{
            elements.nightlyDetailList.innerHTML = "";
            elements.nightlyDetailList.hidden = true;
          }}
          if (elements.nightlyContextBlock) {{
            elements.nightlyContextBlock.hidden = true;
          }}
          if (elements.nightlyStatGrid) {{
            elements.nightlyStatGrid.innerHTML = "";
          }}
          if (elements.nightlyRailNote) {{
            elements.nightlyRailNote.textContent = t("该日期暂无整理结果。");
          }}
          return;
        }}

        renderBackfillPanel(dateValue, true);
        renderNightlyBadges(summary);
        if (elements.nightlyLead) {{
          elements.nightlyLead.textContent = getLocalizedSummaryText(summary, "lead_text");
        }}
        const detailParts = getLocalizedSummaryList(summary, "detail_parts");
        if (elements.nightlyDetailList) {{
          elements.nightlyDetailList.innerHTML = detailParts.map(function (item) {{
            return '<li class="nightly-detail-item">' + escapeHtml(item || "") + '</li>';
          }}).join("");
            elements.nightlyDetailList.hidden = !detailParts.length;
        }}
        const contextLabels = getLocalizedSummaryList(summary, "context_labels");
        if (elements.nightlyContextRow) {{
          elements.nightlyContextRow.innerHTML = contextLabels.map(function (label) {{
            return '<span class="nightly-context-chip">' + escapeHtml(label || "") + '</span>';
          }}).join("");
        }}
        if (elements.nightlyContextBlock) {{
          elements.nightlyContextBlock.hidden = !contextLabels.length;
        }}
        const stats = Array.isArray(summary.stats) ? summary.stats : [];
        if (elements.nightlyStatGrid) {{
          elements.nightlyStatGrid.innerHTML = stats.map(function (item) {{
            return (
              '<article class="nightly-stat-card">' +
                '<div class="nightly-stat-label">' + escapeHtml(t(item.label || "")) + '</div>' +
                '<div class="nightly-stat-value">' + escapeHtml(String(item.value || 0)) + '</div>' +
              '</article>'
            );
          }}).join("");
        }}
        if (elements.nightlyRailNote) {{
          elements.nightlyRailNote.textContent = getLocalizedSummaryText(summary, "note_text");
        }}
      }}

      function applyLanguage(language) {{
        currentLanguage = supportedLanguages.includes(language) ? language : defaultLanguage;
        document.documentElement.lang = currentLanguage === "en" ? "en" : "zh-CN";
        document.body.setAttribute("data-language", currentLanguage);
        document.title = t("OpenRelix 工作台");
        document.querySelectorAll("[data-language-option]").forEach(function (button) {{
          const isActive = button.getAttribute("data-language-option") === currentLanguage;
          button.classList.toggle("is-active", isActive);
          button.setAttribute("aria-pressed", isActive ? "true" : "false");
        }});
        updateSnapshotAge();
        if (state.tokenUsage) {{
          updateTokenVisuals(state.tokenUsage, state.tokenSourceKind);
        }}
        refreshStatusLanguage();
        if (state.selectedNightlyDate) {{
          renderNightlySummary(state.selectedNightlyDate);
        }}
        if (state.selectedWindowOverviewDate) {{
          renderWindowOverview(state.selectedWindowOverviewDate);
        }}
        translateStaticText();
        translateStaticAttributes();
      }}

      function readStoredTheme() {{
        try {{
          const stored = window.localStorage ? window.localStorage.getItem(themeStorageKey) : "";
          return supportedThemes.includes(stored) ? stored : "system";
        }} catch (error) {{
          return "system";
        }}
      }}

      function writeStoredTheme(theme) {{
        try {{
          if (window.localStorage) {{
            window.localStorage.setItem(themeStorageKey, theme);
          }}
        }} catch (error) {{
          // File URLs or privacy settings can block localStorage; the control still works for this page load.
        }}
      }}

      function resolveTheme(theme) {{
        if (theme === "dark" || theme === "light") {{
          return theme;
        }}
        return systemDarkQuery && systemDarkQuery.matches ? "dark" : "light";
      }}

      function applyTheme(theme, persist) {{
        currentThemeChoice = supportedThemes.includes(theme) ? theme : "system";
        const resolvedTheme = resolveTheme(currentThemeChoice);
        document.body.setAttribute("data-theme-choice", currentThemeChoice);
        document.body.setAttribute("data-theme", resolvedTheme);
        document.documentElement.setAttribute("data-theme", resolvedTheme);
        document.querySelectorAll("[data-theme-option]").forEach(function (button) {{
          const isActive = button.getAttribute("data-theme-option") === currentThemeChoice;
          button.classList.toggle("is-active", isActive);
          button.setAttribute("aria-pressed", isActive ? "true" : "false");
        }});
        if (persist) {{
          writeStoredTheme(currentThemeChoice);
        }}
      }}

      function wireThemeButtons() {{
        document.querySelectorAll("[data-theme-option]").forEach(function (button) {{
          button.addEventListener("click", function () {{
            applyTheme(button.getAttribute("data-theme-option"), true);
          }});
        }});
        if (systemDarkQuery) {{
          const onSystemThemeChange = function () {{
            if (currentThemeChoice === "system") {{
              applyTheme("system", false);
            }}
          }};
          if (typeof systemDarkQuery.addEventListener === "function") {{
            systemDarkQuery.addEventListener("change", onSystemThemeChange);
          }} else if (typeof systemDarkQuery.addListener === "function") {{
            systemDarkQuery.addListener(onSystemThemeChange);
          }}
        }}
      }}

      function wireLanguageButtons() {{
        document.querySelectorAll("[data-language-option]").forEach(function (button) {{
          button.addEventListener("click", function () {{
            applyLanguage(button.getAttribute("data-language-option"));
          }});
        }});
      }}

      function wireNightlyDateInput() {{
        if (!elements.nightlyDateInput) {{
          return;
        }}
        ["input", "change"].forEach(function (eventName) {{
          elements.nightlyDateInput.addEventListener(eventName, function () {{
            renderNightlySummary(elements.nightlyDateInput.value || "");
          }});
        }});
      }}

      function wireWindowOverviewDateInput() {{
        if (!elements.windowOverviewDateInput) {{
          return;
        }}
        ["input", "change"].forEach(function (eventName) {{
          elements.windowOverviewDateInput.addEventListener(eventName, function () {{
            renderWindowOverview(elements.windowOverviewDateInput.value || "");
            translateStaticText();
          }});
        }});
      }}

      function wireBackfillCopyButtons() {{
        if (!elements.backfillCopyButtons.length) {{
          return;
        }}
        elements.backfillCopyButtons.forEach(function (button) {{
          button.addEventListener("click", function () {{
            const target = button.getAttribute("data-backfill-copy");
            const source = target === "range" ? elements.backfillRangeCommand : elements.backfillSingleCommand;
            const command = source ? source.textContent : "";
            copyText(command).then(function () {{
              if (elements.backfillStatus) {{
                elements.backfillStatus.textContent = t("已复制回溯命令");
              }}
            }}).catch(function () {{
              if (elements.backfillStatus) {{
                elements.backfillStatus.textContent = t("复制失败，请手动选择命令。");
              }}
            }});
          }});
        }});
      }}

      function setActiveSideNav(targetId) {{
        if (!targetId || !elements.sideNavLinks.length) {{
          return;
        }}
        elements.sideNavLinks.forEach(function (link) {{
          const isActive = link.getAttribute("data-nav-target") === targetId;
          link.classList.toggle("is-active", isActive);
          if (isActive) {{
            link.setAttribute("aria-current", "true");
          }} else {{
            link.removeAttribute("aria-current");
          }}
        }});
      }}

      function wireSideNav() {{
        if (!elements.sideNavLinks.length) {{
          return;
        }}
        const targets = elements.sideNavLinks.map(function (link) {{
          return document.getElementById(link.getAttribute("data-nav-target") || "");
        }}).filter(Boolean);
        function updateActiveTarget() {{
          if (!targets.length) {{
            return;
          }}
          const activationLine = window.innerHeight * 0.32;
          let activeTarget = targets[0];
          targets.forEach(function (target) {{
            if (target.getBoundingClientRect().top <= activationLine) {{
              activeTarget = target;
            }}
          }});
          setActiveSideNav(activeTarget.id);
        }}
        elements.sideNavLinks.forEach(function (link) {{
          link.addEventListener("click", function () {{
            setActiveSideNav(link.getAttribute("data-nav-target") || "");
            window.setTimeout(updateActiveTarget, 120);
          }});
        }});
        window.addEventListener("scroll", updateActiveTarget, {{ passive: true }});
        window.addEventListener("resize", updateActiveTarget);
        updateActiveTarget();
      }}

      function wireContentMoreButtons() {{
        const buttons = Array.from(document.querySelectorAll(".content-more-button"));
        buttons.forEach(function (button) {{
          button.addEventListener("click", function () {{
            const group = button.getAttribute("data-expand-group");
            if (!group) {{
              return;
            }}
            const rows = Array.from(document.querySelectorAll('.content-more-extra-row[data-expand-group="' + group + '"]'));
            const expanded = button.getAttribute("aria-expanded") === "true";
            rows.forEach(function (row) {{
              row.hidden = expanded;
            }});
            button.setAttribute("aria-expanded", expanded ? "false" : "true");
            button.textContent = expanded
              ? t(button.getAttribute("data-collapsed-label") || "查看更多")
              : t(button.getAttribute("data-expanded-label") || "收起更多");
          }});
        }});
      }}

      function wireProjectContextRangeButtons() {{
        const buttons = Array.from(document.querySelectorAll(".context-range-button"));
        const views = Array.from(document.querySelectorAll("[data-context-view]"));
        buttons.forEach(function (button) {{
          button.addEventListener("click", function () {{
            const selectedDays = button.getAttribute("data-context-days");
            buttons.forEach(function (candidate) {{
              const isActive = candidate.getAttribute("data-context-days") === selectedDays;
              candidate.classList.toggle("is-active", isActive);
              candidate.setAttribute("aria-pressed", isActive ? "true" : "false");
            }});
            views.forEach(function (view) {{
              const isActive = view.getAttribute("data-context-view") === selectedDays;
              view.hidden = !isActive;
              view.classList.toggle("is-active", isActive);
            }});
          }});
        }});
      }}

      function wireSummaryTermRangeButtons() {{
        const buttons = Array.from(document.querySelectorAll(".term-range-button"));
        const views = Array.from(document.querySelectorAll("[data-term-view]"));
        buttons.forEach(function (button) {{
          button.addEventListener("click", function () {{
            const selectedDays = button.getAttribute("data-term-days");
            buttons.forEach(function (candidate) {{
              const isActive = candidate.getAttribute("data-term-days") === selectedDays;
              candidate.classList.toggle("is-active", isActive);
              candidate.setAttribute("aria-pressed", isActive ? "true" : "false");
            }});
            views.forEach(function (view) {{
              const isActive = view.getAttribute("data-term-view") === selectedDays;
              view.hidden = !isActive;
              view.classList.toggle("is-active", isActive);
            }});
          }});
        }});
      }}

      function describeRelativeTime(isoValue, actionText) {{
        const normalizedAction = String(actionText || "");
        const isGeneratedAction = normalizedAction === "生成" || normalizedAction === "generated";
        const isUpdatedAction = normalizedAction === "更新" || normalizedAction === "updated";
        const parsed = isoValue ? new Date(isoValue) : null;
        if (!parsed || Number.isNaN(parsed.getTime())) {{
          if (currentLanguage === "en") {{
            return isGeneratedAction
              ? "generation time unknown"
              : isUpdatedAction
                ? "update time unknown"
                : "time unknown";
          }}
          return actionText ? "更新时间未知" : "时间未知";
        }}
        const diffMs = Math.max(0, Date.now() - parsed.getTime());
        const minuteMs = 60 * 1000;
        const hourMs = 60 * minuteMs;
        const dayMs = 24 * hourMs;
        let prefix = "刚刚";
        if (diffMs >= dayMs) {{
          const days = Math.floor(diffMs / dayMs);
          prefix = currentLanguage === "en"
            ? days + (days === 1 ? " day ago" : " days ago")
            : days + " 天前";
        }} else if (diffMs >= hourMs) {{
          const hours = Math.floor(diffMs / hourMs);
          prefix = currentLanguage === "en"
            ? hours + (hours === 1 ? " hour ago" : " hours ago")
            : hours + " 小时前";
        }} else if (diffMs >= minuteMs) {{
          const minutes = Math.floor(diffMs / minuteMs);
          prefix = currentLanguage === "en"
            ? minutes + (minutes === 1 ? " minute ago" : " minutes ago")
            : minutes + " 分钟前";
        }} else if (currentLanguage === "en") {{
          prefix = "just now";
        }}
        if (currentLanguage === "en") {{
          if (isGeneratedAction) {{
            return "generated " + prefix;
          }}
          if (isUpdatedAction) {{
            return "updated " + prefix;
          }}
          return prefix;
        }}
        return actionText ? prefix + actionText : prefix;
      }}

      function updateSnapshotAge() {{
        if (elements.snapshotAge) {{
          elements.snapshotAge.textContent = describeRelativeTime(snapshot.generated_at_iso, "生成");
        }}
      }}

      function setLoading(isLoading) {{
        if (elements.refreshButton) {{
          elements.refreshButton.classList.toggle("is-loading", isLoading);
          elements.refreshButton.disabled = isLoading;
        }}
        if (elements.refreshLabel) {{
          elements.refreshLabel.textContent = isLoading ? t("正在查询 Token") : t("实时刷新 Token");
        }}
        [elements.tokenOverviewPanel, elements.dailyTokenPanel, elements.todayTokenPanel].forEach(function (panel) {{
          if (panel) {{
            panel.classList.toggle("is-loading", isLoading);
          }}
        }});
        liveCards.forEach(function (card) {{
          card.classList.toggle("is-loading", isLoading);
        }});
      }}

      function tokenRefreshStatusText(messageKey) {{
        if (messageKey === "loading_force") {{
          return t("正在实时查询最新 Token…");
        }}
        if (messageKey === "loading_page") {{
          return t("页面已打开，正在同步最新 Token…");
        }}
        if (messageKey === "warn_stale") {{
          return t("实时 Token 暂时不可用，先展示最近一次成功缓存。");
        }}
        if (messageKey === "live_refreshed") {{
          return currentLanguage === "en"
            ? "Token refreshed " + describeRelativeTime(state.tokenRefreshedAt, "") + "."
            : "Token 已刷新，" + describeRelativeTime(state.tokenRefreshedAt, "更新") + "。";
        }}
        if (messageKey === "offline_snapshot") {{
          const snapshotTime = (snapshot.token_usage && snapshot.token_usage.refreshed_at) || snapshot.generated_at_iso;
          return currentLanguage === "en"
            ? "Live Token data is unavailable. Showing the local snapshot from " +
              describeRelativeTime(snapshotTime, "updated") +
              "."
            : "实时 Token 不可用，当前展示 " +
              describeRelativeTime(snapshotTime, "更新") +
              " 的本地快照。";
        }}
        return t(messageKey || "");
      }}

      function refreshStatusLanguage() {{
        if (elements.refreshStatusText && state.refreshStatusMessageKey) {{
          elements.refreshStatusText.textContent = tokenRefreshStatusText(state.refreshStatusMessageKey);
        }}
      }}

      function setStatus(kind, text, messageKey) {{
        state.refreshStatusKind = kind;
        state.refreshStatusMessageKey = messageKey || "";
        if (elements.refreshStatusText) {{
          elements.refreshStatusText.textContent = messageKey ? tokenRefreshStatusText(messageKey) : text;
        }}
      }}

      function compactTokenValue(value) {{
        const number = Number(value) || 0;
        const absNumber = Math.abs(number);
        if (currentLanguage === "en") {{
          if (absNumber >= 1000000000) {{
            return (number / 1000000000).toFixed(1) + "B";
          }}
          if (absNumber >= 1000000) {{
            return (number / 1000000).toFixed(1) + "M";
          }}
          if (absNumber >= 1000) {{
            return (number / 1000).toFixed(1) + "K";
          }}
          return String(Math.round(number));
        }}
        if (absNumber >= 100000000) {{
          return (number / 100000000).toFixed(1) + "亿";
        }}
        if (absNumber >= 10000) {{
          return (number / 10000).toFixed(1) + "万";
        }}
        return String(Math.round(number));
      }}

      function compactSignedTokenValue(value) {{
        const number = Number(value) || 0;
        if (number === 0) {{
          return compactTokenValue(0);
        }}
        return (number > 0 ? "+" : "-") + compactTokenValue(Math.abs(number));
      }}

      function formatPercentValue(value, digits, signed) {{
        if (value === null || value === undefined || Number.isNaN(Number(value))) {{
          return "—";
        }}
        const number = Number(value);
        const sign = signed && number > 0 ? "+" : "";
        return sign + number.toFixed(digits || 0) + "%";
      }}

      function formatUsdValue(value) {{
        if (value === null || value === undefined || Number.isNaN(Number(value))) {{
          return "—";
        }}
        const number = Number(value);
        if (number <= 0) {{
          return "—";
        }}
        return "$" + number.toLocaleString("en-US", {{
          minimumFractionDigits: 0,
          maximumFractionDigits: 0,
        }});
      }}

      function parseUsdFromText(text) {{
        const match = String(text || "").match(/\\$\\s*([0-9][0-9,]*(?:\\.\\d+)?)/);
        if (!match) {{
          return 0;
        }}
        return Number(match[1].replace(/,/g, "")) || 0;
      }}

      function extractTokenRowCost(row) {{
        if (!row) {{
          return 0;
        }}
        const directCost = Number(row.costUSD);
        if (directCost > 0) {{
          return directCost;
        }}
        const displayCost = parseUsdFromText(row.cost_display);
        if (displayCost > 0) {{
          return displayCost;
        }}
        const details = Array.isArray(row.details) ? row.details : [];
        for (const detail of details) {{
          const detailCost = parseUsdFromText(
            (detail && typeof detail === "object")
              ? [detail.title, detail.meta, detail.label].filter(Boolean).join(" ")
              : detail
          );
          if (detailCost > 0) {{
            return detailCost;
          }}
        }}
        return 0;
      }}

      function compactTokenWithCostValue(tokenValue, costValue) {{
        const tokenDisplay = compactTokenValue(tokenValue);
        const costDisplay = formatUsdValue(costValue);
        if (costDisplay === "—") {{
          return tokenDisplay;
        }}
        return tokenDisplay + " · " + costDisplay;
      }}

      function findTokenBreakdownValue(rows, labels) {{
        const candidates = Array.isArray(rows) ? rows : [];
        const needles = labels.map(function (label) {{
          return String(label).toLowerCase();
        }});
        const match = candidates.find(function (row) {{
          const label = String(row.label || "").toLowerCase();
          return needles.some(function (needle) {{
            return label.includes(needle);
          }});
        }});
        return match ? Number(match.value) || 0 : 0;
      }}

      function sanitizeCssClass(value, fallback) {{
        const candidate = String(value || "").trim();
        const fallbackCandidate = String(fallback || "").trim();
        if (/^[a-z0-9_-]+$/i.test(candidate)) {{
          return candidate;
        }}
        if (/^[a-z0-9_-]+$/i.test(fallbackCandidate)) {{
          return fallbackCandidate;
        }}
        return "";
      }}

      function deriveDailyTokenTone(value, maxValue) {{
        const numericValue = Number(value) || 0;
        const numericMax = Math.max(Number(maxValue) || 0, 1);
        if (numericValue <= 0) {{
          return "token-daily-empty";
        }}
        const ratio = numericValue / numericMax;
        if (ratio >= 0.85) {{
          return "token-daily-high";
        }}
        if (ratio >= 0.45) {{
          return "token-daily-mid";
        }}
        return "token-daily-low";
      }}

      function deriveTokenBreakdownTone(row) {{
        const label = String(row && row.label ? row.label : "").toLowerCase();
        if (label.includes("缓存") || label.includes("cached")) {{
          return "token-cache";
        }}
        if (label.includes("推理") || label.includes("reasoning")) {{
          return "token-reasoning";
        }}
        if (label.includes("输出") || label.includes("output")) {{
          return "token-output";
        }}
        if (label.includes("输入") || label.includes("input")) {{
          return "token-input";
        }}
        return "token-input";
      }}

      function deriveTokenSummaryCards(tokenUsage) {{
        const dailyRows = Array.isArray(tokenUsage.daily_rows) ? tokenUsage.daily_rows : [];
        const trailingRows = dailyRows.slice(-7).filter(function (row) {{
          return (Number(row.value) || 0) > 0;
        }});
        const latest = dailyRows.length ? dailyRows[dailyRows.length - 1] : null;
        if (!latest) {{
          return [];
        }}

        const cards = [];
        const total = trailingRows.reduce(function (sum, row) {{
          return sum + (Number(row.value) || 0);
        }}, 0);
        const totalCost = trailingRows.reduce(function (sum, row) {{
          return sum + extractTokenRowCost(row);
        }}, 0);
        if (trailingRows.length) {{
          cards.push({{
            label: currentLanguage === "en" ? "7-day Bill" : "7 日账单",
            value: formatUsdValue(tokenUsage.seven_day_cost_usd || totalCost),
            caption: currentLanguage === "en"
              ? compactTokenValue(tokenUsage.seven_day_total_tokens || total) + " Tokens · ccusage estimate"
              : compactTokenValue(tokenUsage.seven_day_total_tokens || total) + " Token · ccusage 估算",
            tone: "neutral",
          }});
        }} else {{
          cards.push({{
            label: currentLanguage === "en" ? "7-day Bill" : "7 日账单",
            value: "—",
            caption: currentLanguage === "en" ? "No 7-day bill data yet" : "暂无 7 日账单数据",
            tone: "neutral",
          }});
        }}

        if (trailingRows.length) {{
          const average = Math.floor(total / trailingRows.length);
          const peak = trailingRows.reduce(function (currentPeak, row) {{
            return (Number(row.value) || 0) > (Number(currentPeak.value) || 0) ? row : currentPeak;
          }}, trailingRows[0]);
          cards.push({{
            label: currentLanguage === "en" ? "7-day Average" : "7 日均值",
            value: compactTokenValue(average),
            caption: currentLanguage === "en"
              ? "Across " + trailingRows.length + " days with data"
              : "按 " + trailingRows.length + " 个有数据日",
            tone: "neutral",
          }});
          cards.push({{
            label: currentLanguage === "en" ? "Peak Day" : "峰值日",
            value: compactTokenValue(Number(peak.value) || 0),
            caption: currentLanguage === "en"
              ? "Peak on " + (peak.label || "")
              : (peak.label || "") + " 最高",
            tone: "neutral",
          }});
        }}

        const inputTokens = findTokenBreakdownValue(tokenUsage.today_breakdown, ["输入", "input"]);
        const cachedInputTokens = findTokenBreakdownValue(tokenUsage.today_breakdown, ["缓存", "cached"]);
        const cachedShare = inputTokens > 0 ? (cachedInputTokens / inputTokens) * 100 : null;
        cards.push({{
          label: currentLanguage === "en" ? "Cached / Input" : "缓存占输入",
          value: formatPercentValue(cachedShare, 0, false),
          caption: currentLanguage === "en"
            ? "Cached " + compactTokenValue(cachedInputTokens) + " / input " + compactTokenValue(inputTokens)
            : "缓存 " + compactTokenValue(cachedInputTokens) + " / 输入 " + compactTokenValue(inputTokens),
          tone: "neutral",
        }});
        return cards;
      }}

      function prepareTokenUsageForPanel(tokenUsage, relativeUpdate) {{
        const prepared = Object.assign({{}}, tokenUsage || {{}});
        const dailyRows = Array.isArray(prepared.daily_rows) ? prepared.daily_rows : [];
        const dailyMax = dailyRows.reduce(function (currentMax, row) {{
          return Math.max(currentMax, Number(row.value) || 0);
        }}, 0);
        prepared.daily_rows = dailyRows.map(function (row) {{
          const rowCost = extractTokenRowCost(row);
          return Object.assign({{}}, row, {{
            token_display: row.token_display || compactTokenValue(row.value),
            costUSD: Number(row.costUSD) > 0 ? Number(row.costUSD) : rowCost,
            cost_display: row.cost_display || formatUsdValue(rowCost),
            display: compactTokenWithCostValue(row.value, rowCost),
            tone: row.tone || deriveDailyTokenTone(row.value, dailyMax),
          }});
        }});
        const todayRows = Array.isArray(prepared.today_breakdown) ? prepared.today_breakdown : [];
        prepared.today_breakdown = todayRows.map(function (row) {{
          return Object.assign({{}}, row, {{
            label: tokenBreakdownLabel(row.label),
            display: compactTokenValue(row.value),
            details_heading: row.details_heading ? t(row.details_heading) : "",
            tone: row.tone || deriveTokenBreakdownTone(row),
          }});
        }});
        prepared.summary_cards = deriveTokenSummaryCards(prepared);
        if (!prepared.overview_note || currentLanguage === "en") {{
          const activeDays = (prepared.daily_rows || []).slice(-7).filter(function (row) {{
            return (Number(row.value) || 0) > 0;
          }}).length;
          prepared.overview_note = currentLanguage === "en"
            ? activeDays + " days with records in the last 7 days · " + relativeUpdate
            : "近 7 天中 " + activeDays + " 天有记录 · " + relativeUpdate;
        }}
        return prepared;
      }}

      function renderBarValue(row, display) {{
        const details = Array.isArray(row.details) ? row.details : [];
        if (!details.length) {{
          return '<strong>' + escapeHtml(display) + '</strong>';
        }}
        const heading = row.details_heading ? localizeTokenDetailsHeading(row.details_heading) : "对应项目 / 条目";
        const detailItems = details.map(function (detail) {{
          const title = tokenDetailTitle(detail);
          const meta = typeof detail === "object" ? t(detail.meta || "") : "";
          if (!title) {{
            return "";
          }}
          return (
            '<span class="bar-detail-item">' +
              '<span class="bar-detail-title">' + escapeHtml(title) + '</span>' +
              (meta ? '<span class="bar-detail-meta">' + escapeHtml(meta) + '</span>' : '') +
            '</span>'
          );
        }}).join("");
        if (!detailItems) {{
          return '<strong>' + escapeHtml(display) + '</strong>';
        }}
        const ariaTitles = details
          .map(function (detail) {{
            return tokenDetailTitle(detail);
          }})
          .filter(Boolean)
          .slice(0, 8)
          .join("、");
        return (
          '<strong class="bar-value has-details" tabindex="0" aria-label="' +
            escapeHtml(heading + "：" + ariaTitles) +
          '">' +
            '<span class="bar-value-number">' + escapeHtml(display) + '</span>' +
            '<span class="bar-detail-popover" role="tooltip">' +
              '<span class="bar-detail-heading">' + escapeHtml(heading) + '</span>' +
              '<span class="bar-detail-list">' + detailItems + '</span>' +
            '</span>' +
          '</strong>'
        );
      }}

      function renderTokenSummaryCards(cards) {{
        if (!elements.tokenSummaryCards) {{
          return;
        }}
        if (!cards || !cards.length) {{
          elements.tokenSummaryCards.innerHTML = '<p class="empty">' + escapeHtml(t("暂无数据。")) + '</p>';
          return;
        }}
        elements.tokenSummaryCards.innerHTML = cards.map(function (card) {{
          const tone = ["up", "down", "neutral"].includes(card.tone) ? card.tone : "neutral";
          return (
            '<div class="token-stat is-' + tone + '">' +
              '<div class="token-stat-label">' + escapeHtml(t(card.label || "")) + '</div>' +
              '<div class="token-stat-value">' + escapeHtml(card.value || "—") + '</div>' +
              '<div class="token-stat-caption">' + escapeHtml(t(card.caption || "")) + '</div>' +
            '</div>'
          );
        }}).join("");
      }}

      function renderBarRows(container, rows, accentClass) {{
        if (!container) {{
          return;
        }}
        if (!rows || !rows.length) {{
          container.innerHTML = '<p class="empty">' + escapeHtml(t("暂无数据。")) + '</p>';
          return;
        }}
        const maxValue = rows.reduce(function (currentMax, row) {{
          return Math.max(currentMax, Number(row.value) || 0);
        }}, 0) || 1;
        container.innerHTML = rows.map(function (row) {{
          const width = Math.max(0, Math.round(((Number(row.value) || 0) / maxValue) * 100));
          const display = row.display || String(row.value || 0);
          const tone = sanitizeCssClass(row.tone || accentClass, accentClass);
          return (
            '<div class="bar-row">' +
              '<div class="bar-copy">' +
                '<span>' + escapeHtml(row.label || "") + '</span>' +
                renderBarValue(row, display) +
              '</div>' +
              '<div class="bar-track">' +
                '<div class="bar-fill ' + tone + '" style="width:' + width + '%"></div>' +
              '</div>' +
            '</div>'
          );
        }}).join("");
      }}

      function updateMetricCard(metricKey, value, caption, meta) {{
        const card = document.querySelector('[data-metric-key="' + metricKey + '"]');
        if (!card) {{
          return;
        }}
        const valueNode = card.querySelector('[data-role="value"]');
        const captionNode = card.querySelector('[data-role="caption"]');
        const metaNode = card.querySelector('[data-role="meta"]');
        if (valueNode) {{
          valueNode.textContent = value;
        }}
        if (captionNode) {{
          captionNode.textContent = caption;
        }}
        if (metaNode) {{
          metaNode.textContent = meta;
        }}
      }}

      function updateTokenVisuals(tokenUsage, sourceKind) {{
        if (!tokenUsage) {{
          return;
        }}
        state.tokenUsage = tokenUsage;
        state.tokenRefreshedAt = tokenUsage.refreshed_at || state.tokenRefreshedAt;
        state.tokenSourceKind = sourceKind || state.tokenSourceKind;
        const relativeUpdate = describeRelativeTime(state.tokenRefreshedAt, "更新");
        const preparedTokenUsage = prepareTokenUsageForPanel(tokenUsage, relativeUpdate);
        const todayLabel = t(tokenUsage.today_date_label || "今日");
        const todayTokenValue = tokenTotalDisplay(tokenUsage, "today_total_tokens", "today_total_tokens_display");
        const sevenDayTokenValue = tokenTotalDisplay(tokenUsage, "seven_day_total_tokens", "seven_day_total_tokens_display");
        updateMetricCard(
          "today_token",
          todayTokenValue,
          currentLanguage === "en" ? "Total for " + todayLabel : todayLabel + " 的总消耗",
          relativeUpdate
        );
        updateMetricCard(
          "seven_day_token",
          sevenDayTokenValue,
          t("最近 7 天累计消耗"),
          relativeUpdate
        );
        if (elements.dailyTokenNote) {{
          elements.dailyTokenNote.textContent = tokenUsage.available
            ? t("数据来源：ccusage 日维度统计") + " · " + relativeUpdate
            : t("暂未获取到 ccusage 的日维度统计");
        }}
        if (elements.todayTokenNote) {{
          elements.todayTokenNote.textContent = todayLabel + " · " + relativeUpdate;
        }}
        if (elements.tokenOverviewNote) {{
          elements.tokenOverviewNote.textContent = preparedTokenUsage.available
            ? (preparedTokenUsage.overview_note || relativeUpdate)
            : t("暂未获取到 ccusage 的日维度统计");
        }}
        renderTokenSummaryCards(preparedTokenUsage.summary_cards || []);
        renderBarRows(elements.dailyTokenRows, (preparedTokenUsage.daily_rows || []).slice().reverse(), "token-daily-mid");
        renderBarRows(elements.todayTokenRows, preparedTokenUsage.today_breakdown || [], "token-input");
        if (elements.tokenHighlight) {{
          if (tokenUsage.available) {{
            if (currentLanguage === "en") {{
              elements.tokenHighlight.textContent =
                todayLabel + " Token usage is " +
                todayTokenValue +
                "; the last 7 days total " +
                sevenDayTokenValue +
                ". (" + relativeUpdate + ")";
            }} else {{
              elements.tokenHighlight.textContent =
                todayLabel + " 的 Token 总消耗为 " +
                todayTokenValue +
                "，近 7 日累计为 " +
                sevenDayTokenValue +
                "。（" + relativeUpdate + "）";
            }}
          }} else {{
            elements.tokenHighlight.textContent = currentLanguage === "en"
              ? "ccusage daily Token data is unavailable locally; the rest of the panel still works."
              : "本地未取到 ccusage 的日维度 Token 数据，面板其余部分仍可正常使用。";
          }}
        }}
        translateStaticText();
      }}

      function fetchWithTimeout(url, timeoutMs) {{
        const controller = new AbortController();
        const timeoutId = window.setTimeout(function () {{
          controller.abort();
        }}, timeoutMs);
        return fetch(url, {{
          method: "GET",
          cache: "no-store",
          signal: controller.signal,
        }}).finally(function () {{
          window.clearTimeout(timeoutId);
        }});
      }}

      async function refreshTokenUsage(forceRefresh) {{
        setLoading(true);
        setStatus(
          "loading",
          "",
          forceRefresh ? "loading_force" : "loading_page"
        );
        try {{
          const requestUrl = new URL(config.liveEndpoint);
          requestUrl.searchParams.set(
            "window_days",
            String((state.tokenUsage && state.tokenUsage.window_days) || {window_days})
          );
          if (forceRefresh) {{
            requestUrl.searchParams.set("force", "1");
          }}
          const response = await fetchWithTimeout(requestUrl.toString(), config.requestTimeoutMs);
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || ("HTTP " + response.status));
          }}
          if (!payload || !payload.token_usage) {{
            throw new Error("本地 token 服务没有返回可用数据");
          }}
          if (!payload.token_usage.available && !payload.stale) {{
            throw new Error(payload.error || "ccusage 当前不可用");
          }}
          updateTokenVisuals(payload.token_usage, payload.stale ? "stale" : "live");
          if (payload.stale) {{
            setStatus("warn", "", "warn_stale");
          }} else {{
            setStatus("live", "", "live_refreshed");
          }}
        }} catch (error) {{
          updateTokenVisuals(snapshot.token_usage, "snapshot");
          setStatus("offline", "", "offline_snapshot");
        }} finally {{
          setLoading(false);
        }}
      }}

      wireContentMoreButtons();
      wireProjectContextRangeButtons();
      wireSummaryTermRangeButtons();
      wireThemeButtons();
      wireLanguageButtons();
      wireNightlyDateInput();
      wireWindowOverviewDateInput();
      wireBackfillCopyButtons();
      wireSideNav();
      applyTheme(readStoredTheme(), false);
      applyLanguage(defaultLanguage);
      if (elements.refreshButton) {{
        elements.refreshButton.addEventListener("click", function () {{
          refreshTokenUsage(true);
        }});
      }}
      window.setInterval(updateSnapshotAge, 60 * 1000);
      window.setInterval(function () {{
        if (state.tokenUsage) {{
          updateTokenVisuals(state.tokenUsage, state.tokenSourceKind);
        }}
      }}, 60 * 1000);
      window.setInterval(function () {{
        refreshTokenUsage(false);
      }}, config.livePollMs);
      refreshTokenUsage(false);
      window.setTimeout(function () {{
        window.location.reload();
      }}, config.autoReloadMs);
    }})();
  </script>
</body>
</html>
""".format(
        default_language=language,
        html_language="en" if language == "en" else "zh-CN",
        document_title=escape(localized("OpenRelix 工作台", "OpenRelix Workbench", language)),
        generated_at=escape(data["generated_at"]),
        hero_eyebrow=panel_language_text_html("OpenRelix"),
        hero_title=panel_language_text_html("OpenRelix 工作台"),
        hero_brand_line=panel_language_variant_html(
            escape("你的专属AI记忆珍藏"),
            escape("Your personal AI memory keepsake"),
        ),
        hero_copy=panel_language_text_html(
            "只保留当前有效的复用信号：最近整理、核心指标，以及可继续下钻的窗口、记忆和资产明细。"
        ),
        snapshot_label=panel_language_text_html("面板快照：", "Snapshot:"),
        side_nav=make_side_nav(),
        theme_switch=theme_switch,
        language_switch=language_switch,
        github_button=github_button,
        panel_footer_notice=panel_footer_notice,
        panel_path_label=escape(PANEL_PATH_LABEL),
        overview_json_path_label=escape(OVERVIEW_JSON_PATH_LABEL),
        snapshot_payload=snapshot_payload,
        panel_i18n_json=panel_i18n_json(),
        auto_refresh_ms=AUTO_REFRESH_SECONDS * 1000,
        live_token_endpoint=json.dumps(LIVE_TOKEN_ENDPOINT),
        live_token_poll_ms=LIVE_TOKEN_POLL_SECONDS * 1000,
        live_token_timeout_ms=LIVE_TOKEN_TIMEOUT_MS,
        window_days=token_usage.get("window_days", CCUSAGE_WINDOW_DAYS),
        token_metric_cards="".join(token_metric_cards),
        asset_metric_cards="".join(asset_metric_cards),
        asset_ledger_kicker=panel_language_text_html("资产层", "Asset Layer"),
        asset_ledger_title=panel_language_text_html("资产账本概览", "Asset Ledger Overview"),
        asset_ledger_note=panel_language_text_html(
            "这里看的是已经登记到本地账本里的资产、复盘和复用记录，不是注入 Codex context 的记忆摘要。",
            "This shows assets, reviews, and reuse records registered in the local ledger, not the memory summary injected into Codex context.",
        ),
        token_overview_panel=make_token_overview_panel(token_usage, token_overview_help),
        type_panel=make_bar_group(
            "资产类型分布",
            data["mix"]["type"],
            "teal",
            "来自资产注册表的稳定条目",
            help_html=type_panel_help,
        ),
        domain_panel=make_bar_group(
            "项目 / 上下文分布",
            data["mix"]["context"],
            "amber",
            "根据资产路径与最近工作自动归纳",
            help_html=context_panel_help,
        ),
        month_panel=make_bar_group(
            "月度新增",
            data["mix"]["month"],
            "slate",
            help_html=month_panel_help,
        ),
        scope_panel=make_bar_group(
            "适用层级",
            data["mix"]["scope"],
            "rose",
            "按复用层级分类",
            help_html=scope_panel_help,
        ),
        insight_section_html=insight_section_html,
        daily_token_panel=make_bar_group(
            "每日 Token 消耗",
            list(reversed(token_usage["daily_rows"])),
            "slate",
            token_note,
            panel_id="daily-token-panel",
            note_id="daily-token-note",
            rows_id="daily-token-rows",
            extra_classes="token-panel",
            help_html=daily_token_help,
        ),
        today_token_panel=make_bar_group(
            "今日 Token 构成",
            token_usage["today_breakdown"],
            "rose",
            token_usage["today_date_label"],
            panel_id="today-token-panel",
            note_id="today-token-note",
            rows_id="today-token-rows",
            extra_classes="token-panel",
            help_html=today_token_help,
        ),
        nightly_summary_panel=nightly_summary_panel,
        project_context_header=make_panel_header(
            "当前项目上下文",
            help_html=project_context_help,
            note_content_html=panel_language_text_html(
                project_context_note,
                panel_english_text(project_context_note),
            ),
        ),
        project_context_body=panel_language_block_html(
            make_project_context_body(
                project_context_views_zh,
                project_context_default_days,
                language="zh",
            ),
            make_project_context_body(
                project_context_views_en,
                project_context_default_days,
                language="en",
            ),
        ),
        personal_asset_memory_family_header=make_memory_family_header(
            "个人资产记忆",
            "Personal Asset Memory",
            "来自本地资产系统的 nightly 整理与结构化登记册。",
            "From the local asset system's nightly synthesis and structured registry.",
            extra_html=(
                make_personal_memory_count_widget(
                    data.get("memory_registry", []),
                )
                + make_personal_memory_token_widget(
                    data.get("personal_memory_token_usage", {})
                )
            ),
        ),
        codex_native_memory_family_header=make_memory_family_header(
            "Codex 原生记忆",
            "Codex Native Memory",
            "来自 Codex 原生 memory summary 与 MEMORY.md。",
            "From Codex native memory_summary and MEMORY.md.",
        ),
        durable_memory_header=make_panel_header(
            "个人资产-长期记忆",
            "可跨天复用的条目",
            durable_memory_help,
        ),
        session_memory_header=make_panel_header(
            "个人资产-短期工作记忆",
            "更偏当天任务推进",
            session_memory_help,
        ),
        low_priority_memory_header=make_panel_header(
            "个人资产-低优先级记忆",
            "保留但优先级较低",
            low_priority_memory_help,
        ),
        recent_assets_header=make_panel_header(
            "最近更新的资产",
            "最近一次变更的资产条目",
            recent_assets_help,
        ),
        top_assets_header=make_panel_header(
            "复用价值较高的资产",
            "按自动估算价值分排序",
            top_assets_help,
        ),
        reviews_header=make_panel_header(
            "最近复盘",
            "最近形成的脱敏任务复盘",
            reviews_help,
        ),
        usage_header=make_panel_header(
            "最近复用记录",
            "用于证明某个已有条目在任务里发挥了作用",
            usage_help,
        ),
        window_overview_header=make_panel_header(
            window_overview_heading,
            window_overview_note,
            window_overview_help,
            note_id="window-overview-note",
            title_id="window-overview-title",
            extra_meta_html=window_overview_date_control,
        ),
        nightly_window_cards=make_window_summary_cards(window_overview),
        durable_memory_cards=make_memory_type_grouped_cards(
            data.get("nightly_memory_views", {}).get("durable", []),
            include_bucket_meta=False,
        ),
        session_memory_cards=make_memory_type_grouped_cards(
            data.get("nightly_memory_views", {}).get("session", []),
            include_bucket_meta=False,
        ),
        low_priority_memory_cards=make_memory_type_grouped_cards(
            data.get("nightly_memory_views", {}).get("low_priority", []),
            include_bucket_meta=False,
        ),
        memory_registry_cards=make_memory_cards(memory_registry),
        codex_native_topic_header=make_panel_header(
            "Codex 原生记忆-主题项",
            help_html=codex_native_topic_help,
        ),
        codex_native_preference_header=make_panel_header(
            "Codex 原生记忆-偏好",
            "来自 User preferences，按卡片样式展示",
            codex_native_preference_help,
        ),
        codex_native_tip_header=make_panel_header(
            "Codex 原生记忆-通用 tips",
            "来自 General Tips，按卡片样式展示",
            codex_native_tip_help,
        ),
        codex_native_task_group_header=make_panel_header(
            "Codex 原生记忆-任务组",
            "来自 MEMORY.md，按任务组展示",
            codex_native_task_group_help,
        ),
        codex_native_topic_cards=make_memory_cards(codex_native_memory),
        codex_native_preference_cards=codex_native_preference_cards,
        codex_native_tip_cards=codex_native_tip_cards,
        codex_native_task_group_cards=codex_native_task_group_cards,
        recent_asset_rows=make_asset_rows(panel_views.get("recent_assets", data["assets"]["recent"]), "recent-assets"),
        top_asset_rows=make_top_asset_rows(panel_views.get("top_assets", data["assets"]["top"]), "top-assets"),
        review_cards=make_review_cards(panel_views.get("reviews", data["reviews"])),
        usage_rows=make_usage_rows(panel_views.get("usage_events", data["usage_events"]), "usage-events"),
        asset_header=panel_language_text_html("资产"),
        type_header=panel_language_text_html("类型"),
        context_header=panel_language_text_html("项目 / 上下文"),
        scope_header=panel_language_text_html("适用层级"),
        updated_header=panel_language_text_html("更新时间"),
        usage_header_text=panel_language_text_html("复用记录"),
        value_score_header=panel_language_text_html("价值分"),
        estimated_saved_header=panel_language_text_html("估算节省"),
        evidence_header=panel_language_text_html("证据"),
        date_header=panel_language_text_html("日期"),
        asset_id_header=panel_language_text_html("资产 ID"),
        task_header=panel_language_text_html("任务"),
        minutes_saved_header=panel_language_text_html("节省分钟"),
    )


def main():
    ensure_state_layout(PATHS)
    assets = load_jsonl(REGISTRY_DIR / "assets.jsonl")
    usage_events = load_jsonl(REGISTRY_DIR / "usage_events.jsonl")
    reviews = load_reviews()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    data = build_data(assets, usage_events, reviews, language=LANGUAGE)
    overview_json = REPORTS_DIR / "overview-data.json"
    overview_md = REPORTS_DIR / "overview.md"
    overview_csv = REPORTS_DIR / "overview.csv"
    panel_html = REPORTS_DIR / "panel.html"
    overview_json_content = json.dumps(
        normalize_brand_display_payload(data),
        ensure_ascii=False,
        indent=2,
    )
    overview_md_content = normalize_brand_display_text(build_markdown(data))
    panel_html_content = normalize_brand_display_text(build_html(data))

    atomic_write_text(overview_json, overview_json_content + "\n")
    atomic_write_text(overview_md, overview_md_content)
    atomic_write_text(panel_html, panel_html_content)
    build_csv(data, overview_csv)
    remove_legacy_dashboard_outputs()
    write_repo_panel_entrypoint()


if __name__ == "__main__":
    main()
