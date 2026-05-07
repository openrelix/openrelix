#!/usr/bin/env python3

import csv
import hashlib
import json
import os
import re
import shlex
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from html import escape
from pathlib import Path
from urllib.parse import quote, urlparse

from asset_runtime import (
    atomic_write_json,
    atomic_write_text,
    ensure_state_layout,
    get_memory_mode,
    get_memory_summary_budget,
    get_project_version,
    get_runtime_language,
    get_runtime_paths,
    PREVIOUS_PUBLIC_APP_SLUG,
    PROJECT_PACKAGE_NAME,
    render_path,
)
from build_codex_memory_summary import (
    DEFAULT_GLOBAL_MEMORY_TOKENS as MEMORY_SUMMARY_GLOBAL_MEMORY_TOKENS,
    DEFAULT_MAX_PERSONAL_MEMORY_ITEMS as MEMORY_SUMMARY_MAX_PERSONAL_MEMORY_ITEMS,
    DEFAULT_MAX_TOKENS as MEMORY_SUMMARY_MAX_TOKENS,
    DEFAULT_PERSONAL_MEMORY_TOKENS as MEMORY_SUMMARY_PERSONAL_MEMORY_TOKENS,
    DEFAULT_PROJECT_MEMORY_TOKENS as MEMORY_SUMMARY_PROJECT_MEMORY_TOKENS,
    DEFAULT_TARGET_TOKENS as MEMORY_SUMMARY_TARGET_TOKENS,
    DEFAULT_WARN_TOKENS as MEMORY_SUMMARY_WARN_TOKENS,
    PERSONAL_MEMORY_NOTE_LIMIT,
    PERSONAL_MEMORY_TITLE_LIMIT,
    estimate_tokens as estimate_summary_tokens,
    reverse_date_sort_key as memory_summary_reverse_date_sort_key,
)
from openrelix_overview import common as overview_common
from openrelix_overview import contract as overview_contract
from openrelix_overview import asset_discovery as overview_asset_discovery
from openrelix_overview import claude_desktop as overview_claude_desktop
from openrelix_overview import codex_desktop as overview_codex_desktop
from openrelix_overview import finder as overview_finder
from openrelix_overview import i18n as overview_i18n
from openrelix_overview import labels as overview_labels
from openrelix_overview import local_paths as overview_local_paths
from openrelix_overview import memory_context as overview_memory_context
from openrelix_overview import memory_feedback as overview_memory_feedback
from openrelix_overview import mcp_usage as overview_mcp_usage
from openrelix_overview import memory_registry as overview_memory_registry
from openrelix_overview import pipeline_status as overview_pipeline_status
from openrelix_overview import redaction as overview_redaction
from openrelix_overview import token_fetcher as overview_token_fetcher
from openrelix_overview import token_usage as overview_token_usage
from openrelix_overview import update_secret as overview_update_secret
from openrelix_overview.config import (
    CCUSAGE_TIMEZONE,
    CCUSAGE_WINDOW_DAYS,
    LIVE_TOKEN_ENDPOINT,
    LIVE_TOKEN_HOST,
    LIVE_TOKEN_POLL_SECONDS,
    LIVE_TOKEN_PORT,
    LIVE_TOKEN_TIMEOUT_MS,
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
ASSET_STATS_LATEST_PATH = REPORTS_DIR / "asset-stats-latest.json"
CODEX_NATIVE_DISPLAY_CACHE_PATH = PATHS.runtime_dir / "codex-native-display-cache.json"
AUTO_REFRESH_SECONDS = 1800
BACKFILL_LOOKBACK_DAYS = 14
BACKFILL_LEARN_WINDOW_DAYS = 7
PROJECT_GITHUB_URL = "https://github.com/openrelix/openrelix"
WRITE_REPO_PANEL_ENTRYPOINT_ENV = "OPENRELIX_WRITE_REPO_PANEL_ENTRYPOINT"
BRAND_DISPLAY_REPLACEMENTS = (
    ("scripts/openrelix.py.py", "scripts/openrelix.py"),
)
BRAND_DISPLAY_NAME = overview_redaction.BRAND_DISPLAY_NAME
LEGACY_BRAND_PHRASES = overview_redaction.LEGACY_BRAND_PHRASES
PROJECT_CONTEXT_VISIBLE_COUNT = 4
PROJECT_CONTEXT_DEFAULT_DAYS = 1
PROJECT_CONTEXT_MAX_DAYS = 7
SUMMARY_TERM_DEFAULT_DAYS = 1
SUMMARY_TERM_RANGE_DAYS = (1, 7)
MEMORY_USAGE_WINDOW_DAYS = 7
UPDATE_COMMAND_TEXT = "openrelix update --yes --force"
PROJECT_CONTEXT_TOPIC_VISIBLE_COUNT = 4
TOKEN_METRIC_KEYS = {"today_token", "seven_day_token"}
DISCOVERED_KIND_ORDER = overview_asset_discovery.DISCOVERED_KIND_ORDER
DISCOVERED_TYPE_ORDER = overview_asset_discovery.HIGH_LEVEL_TYPE_ORDER
DISCOVERED_NON_SKILL_KINDS = overview_asset_discovery.NON_SKILL_KINDS
MEMORY_BRIEF_TITLE_LIMIT = 42
MEMORY_BRIEF_BODY_LIMIT = 132
MEMORY_BRIEF_FULL_TEXT_LIMIT = 520
PANEL_PATH_LABEL = render_path(REPORTS_DIR / "panel.html")
OVERVIEW_JSON_PATH_LABEL = render_path(REPORTS_DIR / "overview-data.json")
PERSONAL_REDACTION_LABEL = overview_redaction.PERSONAL_REDACTION_LABEL


def _load_brand_icon_data_uri():
    candidate = ROOT / "docs" / "openrelix-icon.png"
    if not candidate.exists():
        return ""
    import base64
    try:
        encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return "data:image/png;base64," + encoded


BRAND_ICON_DATA_URI = _load_brand_icon_data_uri()
LOCAL_PATH_TRAILING_PUNCTUATION = overview_local_paths.LOCAL_PATH_TRAILING_PUNCTUATION
LOCAL_PATH_TOKEN_RE = overview_local_paths.LOCAL_PATH_TOKEN_RE


@lru_cache(maxsize=1)
def personal_redaction_patterns():
    return overview_redaction.load_personal_redaction_patterns(PATHS)


def redact_personal_text(value):
    return overview_redaction.redact_personal_text(
        value,
        patterns=personal_redaction_patterns(),
        redaction_label=PERSONAL_REDACTION_LABEL,
    )


def normalize_brand_display_text(value):
    return overview_redaction.normalize_brand_display_text(
        value,
        brand_replacements=BRAND_DISPLAY_REPLACEMENTS,
        legacy_phrases=LEGACY_BRAND_PHRASES,
        brand_display_name=BRAND_DISPLAY_NAME,
        patterns=personal_redaction_patterns(),
        redaction_label=PERSONAL_REDACTION_LABEL,
    )


def normalize_brand_display_payload(value):
    return overview_redaction.normalize_brand_display_payload(
        value,
        normalize_brand_display_text,
    )

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
    "skill": "skills",
    "skills": "skills",
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
    "技能": "Skills",
    "skills": "Skills",
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
    "工作记忆": "Work memory",
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
    "AI 资产概览链路": "AI asset overview pipeline",
    "Token 图表 Apple 风格配色优化": "Token chart Apple-style color refinement",
    "记忆面板四列展开布局": "Memory panel four-column expanded layout",
    "飞书画板 CLI 能力检查": "Feishu Whiteboard CLI capability check",
    "资产与复盘面板布局优化": "Asset and review panel layout refinement",
    "资产面板 artifact 路径跳转": "Asset panel artifact path links",
    "个人资产系统初始化": "Personal asset system bootstrap",
    "沉淀一套稳定的全局工作方式，约束 Codex 的通用行为和本地资产边界。": (
        "Captures a stable global operating model for Codex behavior and local asset boundaries."
    ),
    "把复盘、方法、模板和流程整理成可持续复用的本地资产。": (
        "Turns reviews, methods, templates, and workflows into sustainable reusable local assets."
    ),
    "把本地资产、复盘和 token 数据整理成一份可直接查看的概览和面板。": (
        "Turns local assets, reviews, and token data into a directly browsable overview and panel."
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

DISPLAY_TYPE = overview_labels.DISPLAY_TYPE
DISPLAY_DOMAIN = overview_labels.DISPLAY_DOMAIN
DISPLAY_SCOPE = overview_labels.DISPLAY_SCOPE
DISPLAY_STATUS = overview_labels.DISPLAY_STATUS
DISPLAY_MEMORY_BUCKET = overview_labels.DISPLAY_MEMORY_BUCKET
DISPLAY_MEMORY_TYPE = overview_labels.DISPLAY_MEMORY_TYPE
DISPLAY_MEMORY_PRIORITY = overview_labels.DISPLAY_MEMORY_PRIORITY
DISPLAY_TYPE_EN = overview_labels.DISPLAY_TYPE_EN
DISPLAY_DOMAIN_EN = overview_labels.DISPLAY_DOMAIN_EN
DISPLAY_SCOPE_EN = overview_labels.DISPLAY_SCOPE_EN
DISPLAY_STATUS_EN = overview_labels.DISPLAY_STATUS_EN
DISPLAY_MEMORY_BUCKET_EN = overview_labels.DISPLAY_MEMORY_BUCKET_EN
DISPLAY_MEMORY_TYPE_EN = overview_labels.DISPLAY_MEMORY_TYPE_EN
MEMORY_TYPE_GROUP_ORDER = overview_labels.MEMORY_TYPE_GROUP_ORDER
DISPLAY_MEMORY_PRIORITY_EN = overview_labels.DISPLAY_MEMORY_PRIORITY_EN

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
    "刷新资产层": "Refresh Asset Layer",
    "正在刷新资产层": "Refreshing Asset Layer",
    "正在刷新资产层，通常需要几十秒…": "Refreshing the asset layer. This usually takes a few dozen seconds...",
    "资产层已刷新，正在重新载入面板。": "Asset layer refreshed. Reloading the panel.",
    "资产层刷新失败，稍后重试。": "Asset layer refresh failed. Try again later.",
    "本地服务未启动。请运行 openrelix open panel 后再刷新资产层。": (
        "The local service is not running. Run openrelix open panel, then refresh the asset layer again."
    ),
    "先展示本地快照，再实时同步最新 Token。": "Showing the local snapshot first, then syncing the latest Token usage.",
    "页面已打开，正在同步最新 Token…": "Page opened. Syncing the latest Token usage...",
    "正在实时查询最新 Token…": "Querying the latest Token usage...",
    "本地 token 服务没有返回可用数据": "The local token service returned no usable data.",
    "ccusage 当前不可用": "ccusage is currently unavailable.",
    "实时 Token 暂时不可用，先展示最近一次成功缓存。": "Live Token data is unavailable. Showing the latest successful cache.",
    "本地 Token 服务未启动。请运行 openrelix open panel 后再点实时刷新。": (
        "The local Token service is not running. Run openrelix open panel, then refresh Token again."
    ),
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
    "收起 MCP 工具": "Collapse MCP tools",
    "用户偏好": "User Preferences",
    "通用 tips": "General Tips",
    "历史任务索引": "Historical Task Index",
    "历史任务": "Historical Task",
    "记忆条目": "Memory Items",
    "来自 User preferences，默认展示前 4 条": "From User preferences. Showing the first 4 by default.",
    "来自 General Tips，默认展示前 4 条": "From General Tips. Showing the first 4 by default.",
    "来自 MEMORY.md，默认展示前 4 条历史任务索引": "From MEMORY.md. Showing the first 4 historical task index entries by default.",
    "来自 User preferences，按卡片样式展示": "From User preferences. Shown as cards.",
    "来自 General Tips，按卡片样式展示": "From General Tips. Shown as cards.",
    "来自 MEMORY.md，按历史任务索引展示": "From MEMORY.md. Shown as a historical task index.",
    "条偏好": "preferences",
    "条 tip": "tips",
    "条历史任务索引": "historical task index entries",
    "登记册资产": "Registry Assets",
    "已发现资产": "Discovered Assets",
    "已发现的 Codex / Claude 资产": "Discovered Codex / Claude Assets",
    "单次资产统计": "Single Asset Stats",
    "在 Finder 中显示": "Reveal in Finder",
    "正在打开": "Opening",
    "已发送": "Sent",
    "打开失败": "Open failed",
    "登记册活跃资产": "Active Registry Assets",
    "任务复盘": "Task Reviews",
    "复用记录": "Usage Events",
    "节省时长": "Time Saved",
    "登记册仓库资产": "Repo-scoped Registry Assets",
    "今日 Token": "Today Token",
    "今日": "Today",
    "近 7 日 Token": "7-day Token",
    "筛选 Token": "Filtered Token",
    "周期成本": "Period Cost",
    "Token 筛选": "Token Filters",
    "来源": "Source",
    "全部": "All",
    "全部来源": "All Sources",
    "粒度": "Granularity",
    "如何登记": "How to register",
    "按日": "Daily",
    "按月": "Monthly",
    "起始日期": "Start Date",
    "结束日期": "End Date",
    "重置": "Reset",
    "Token 构成": "Token Breakdown",
    "Token 消耗趋势": "Token Usage Trend",
    "每月 Token 消耗": "Monthly Token Usage",
    "筛选区间": "Selected Range",
    "Token 速览": "Token Overview",
    "7 日账单": "7-day Bill",
    "7 日均值": "7-day Average",
    "周期账单": "Period Bill",
    "周期日均": "Daily Average",
    "月均值": "Monthly Average",
    "峰值日": "Peak Day",
    "峰值月": "Peak Month",
    "暂无账单数据": "No bill data yet",
    "缓存占输入": "Cache Read / Input",
    "缓存占总输入": "Cache Read / Total Input",
    "缓存读取占总输入": "Cache Read / Total Input",
    "输入": "Input",
    "缓存输入": "Cache Read",
    "缓存读取": "Cache Read",
    "输出": "Output",
    "推理输出": "Reasoning Output",
    "输入详情": "Input Details",
    "缓存详情": "Cache Read Details",
    "输出详情": "Output Details",
    "推理详情": "Reasoning Details",
    "总输入 Token": "Total input tokens",
    "无缓存输入 Token": "Uncached input tokens",
    "暂无可比较日期": "No comparable day yet",
    "长期记忆": "Long-term Memory",
    "工作记忆": "Work Memory",
    "低优先记忆": "Low-priority Memory",
    "低优先级记忆": "Low-priority Memory",
    "个人资产记忆": "Personal Asset Memory",
    "记忆数量": "Memory Counts",
    "上下文策略": "Context Policy",
    "上下文编译": "Overview",
    "总览": "Overview",
    "上下文预算": "Context Budget",
    "全局上下文": "Global Context",
    "项目上下文": "Project Context",
    "通用上下文": "General Context",
    "按需召回": "On-demand Recall",
    "本地保留": "Local Only",
    "OpenRelix canonical memory -> host context 的策略预览": "Policy preview from OpenRelix canonical memory to host context",
    "会进入通用 host context 的个人资产记忆": "Personal asset memories that enter the general host context",
    "按项目、仓库或工作区隔离的记忆": "Memories isolated by project, repo, or workspace",
    "适合检索命中后再使用的领域记忆": "Domain memories used only after retrieval matches",
    "低优先或禁止注入的本地证据": "Local evidence with low priority or disabled injection",
    "总数": "Total",
    "个人资产-长期记忆": "Personal Asset - Long-term Memory",
    "个人资产-工作记忆": "Personal Asset - Work Memory",
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
    "Host context 预算": "Host Context Budget",
    "进入 host context 的记忆": "Memories in Host Context",
    "按当前 bounded summary 预算估算；按类型分组展示。": (
        "Estimated from the current bounded-summary budget and grouped by memory type."
    ),
    "受控": "Bounded",
    "本地": "Local",
    "长期": "Long-term",
    "工作": "Work",
    "低优先": "Low-priority",
    "工作窗口": "Work Windows",
    "工作跟进": "Work Memory",
    "低优先级": "Low-priority",
    "暂无夜间整理结果": "No nightly synthesis yet",
    "选择日期": "Select date",
    "选择整理日期": "Select synthesis date",
    "选择窗口日期": "Select window date",
    "该日期暂无整理结果。": "No synthesis for this date.",
    "未整理": "Not synthesized",
    "缺少整理结果": "Missing synthesis",
    "今日仍在进行中": "Today is still in progress",
    "建议深度回溯": "Recommended deep backfill",
    "该日期还没有整理结果。可以复制命令在终端手动回溯。": (
        "This date has no synthesis yet. Copy the command and run it in a terminal to backfill it."
    ),
    "今天还没结束，当前还没有 30 分钟快速回溯；可先运行今日快速回溯刷新面板，次日会自动生成完整回溯。": (
        "Today is not over yet and no 30-minute quick backfill exists. Run today's quick backfill to refresh the panel; the full backfill will run tomorrow."
    ),
    "今天还没结束，当前保留 30 分钟快速回溯；次日会自动生成完整回溯。": (
        "Today is not over yet, so the 30-minute quick backfill remains active. The full backfill will run tomorrow."
    ),
    "当前是 30 分钟快速回溯，只生成窗口摘要和快速索引，不做记忆沉淀。可以复制命令在终端补跑完整回溯。首次安装后，会自动触发完整回溯，请耐心等待。": (
        "This is the 30-minute quick backfill, so it only generates window summaries and a fast index; memory synthesis is deferred. "
        "Copy the command and run the full backfill in a terminal. After first install, "
        "OpenRelix starts the full backfill automatically; please wait."
    ),
    "单日回溯": "Single-date backfill",
    "30 分钟快速回溯": "30-minute quick backfill",
    "当日预览": "Daily preview",
    "深度回溯": "Deep backfill",
    "多日回溯": "Multi-day backfill",
    "复制命令": "Copy command",
    "已复制回溯命令": "Backfill command copied",
    "复制失败，请手动选择命令。": "Copy failed. Select the command manually.",
    "该日期暂无窗口整理结果。": "No window synthesis for this date.",
    "完整回溯": "Full backfill",
    "手动": "Manual",
    "待生成": "Pending",
    "已生成": "Generated",
    "今日仍有活跃整理": "Active synthesis today",
    "保底摘要": "Fallback summary",
    "窗口": "Windows",
    "低优先级": "Low Priority",
    "整理窗口数": "Synthesized Windows",
    "整理长期记忆": "Long-term Memories",
    "整理工作记忆": "Work Memories",
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
    "近 7 日热词": "Last 7 Days Hot Terms",
    "热词时间范围": "Hot terms date range",
    "资产类型分布": "Asset Type Distribution",
    "月度活动": "Monthly Activity",
    "MCP 使用热度": "MCP Tool Usage",
    "运行视图": "Runtime View",
    "记忆层": "Memory Layer",
    "资产层": "Asset Layer",
    "资产记忆": "Asset Memory",
    "资产层总览": "Asset Layer Overview",
    "这里合并展示本机发现资产、登记册条目、复盘和复用记录，不是注入 host context 的记忆摘要。": (
        "This merges discovered local assets, registry entries, reviews, and reuse records; it is not the memory summary injected into host context."
    ),
    "这里合并展示本机发现资产、手动账本条目、复盘和复用记录，不是注入 host context 的记忆摘要。": (
        "This merges discovered local assets, registry entries, reviews, and reuse records; it is not the memory summary injected into host context."
    ),
    "每日 Token 消耗": "Daily Token Usage",
    "今日 Token 构成": "Today Token Breakdown",
    "当前项目上下文": "Current Project Context",
    "来自本地资产系统的 nightly 整理与结构化登记册。": "From the local asset system's nightly synthesis and structured registry.",
    "来自 Codex 原生 memory summary 与 MEMORY.md。": "From Codex native memory_summary and MEMORY.md.",
    "Codex 原生记忆": "Codex Native Memory",
    "Codex 原生记忆-记忆条目": "Codex Native Memory - Memory Items",
    "Codex 原生记忆-偏好": "Codex Native Memory - Preferences",
    "Codex 原生记忆-通用 tips": "Codex Native Memory - General Tips",
    "Codex 原生记忆-历史任务索引": "Codex Native Memory - Historical Task Index",
    "Claude 原生": "Claude Native",
    "Claude 原生记忆": "Claude Native Memory",
    "Claude Code 原生记忆": "Claude Code Native Memory",
    "Claude Code 原生记忆-记忆条目": "Claude Code Native Memory - Memory Items",
    "Claude Code 原生记忆-偏好": "Claude Code Native Memory - Preferences",
    "Claude Code 原生记忆-通用 tips": "Claude Code Native Memory - General Tips",
    "来自 Claude Code CLAUDE.md 与 projects/*/memory/*.md。": (
        "From Claude Code CLAUDE.md and projects/*/memory/*.md."
    ),
    "来自 CLAUDE.md 和 auto memory 中的偏好条目": (
        "From preferences in CLAUDE.md and auto memory."
    ),
    "来自 CLAUDE.md 和 auto memory 中的通用提示": (
        "From general tips in CLAUDE.md and auto memory."
    ),
    "近 30 天高频技能 Top 10": "Top 10 Skills (last 30 days)",
    "近 30 天高频技能热度": "Skill Hotness (last 30 days)",
    "近 30 天高频 skills Top 10": "Top 10 Skills (last 30 days)",
    "近 30 天高频 skills 热度": "Skill Hotness (last 30 days)",
    "最近复盘": "Recent Reviews",
    "最近复用记录": "Recent Usage Events",
    "最近形成的脱敏任务复盘": "Recent sanitized task reviews",
    "昨夜窗口概览": "Last Night's Window Overview",
    "当日窗口概览": "Today's Window Overview",
    "每日窗口概览": "Daily Window Overview",
    "最近一次窗口概览": "Latest Window Overview",
    "资产": "Asset",
    "名称": "Name",
    "描述": "Description",
    "类型": "Type",
    "项目 / 上下文": "Project / Context",
    "更新时间": "Updated",
    "日期": "Date",
    "资产 ID": "Asset ID",
    "任务": "Task",
    "节省分钟": "Minutes Saved",
    "价值分": "Value Score",
    "证据": "Evidence",
    "调用": "Calls",
    "会话": "Sessions",
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
    "更多记录见": "More records in",
    "原始窗口 JSON": "Raw Window JSON",
    "原始窗口 ID": "Raw Window ID",
    "当前目录": "Current Directory",
    "启动时间": "Started At",
    "原始窗口": "Raw Window",
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
    "高频率": "High Frequency",
    "中频率": "Medium Frequency",
    "语义": "Semantic",
    "流程": "Procedure",
    "事件记忆": "Episodic",
    "规则": "Rule",
    "偏好": "Preference",
    "映射": "Mapping",
    "未分类": "Uncategorized",
    "未标注": "Unlabeled",
    "技能": "Skills",
    "skills": "Skills",
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
    "当前 Token 筛选条件下的总 Token 消耗。": (
        "Total Token usage under the current Token filters."
    ),
    "总量按筛选后的 Codex / Claude Code 来源、起止日期和展示粒度重新汇总。": (
        "Totals are recomputed from the selected Codex / Claude Code source, date range, and granularity."
    ),
    "当前 Token 筛选区间的 ccusage 费用估算。": (
        "ccusage cost estimate for the current Token filter range."
    ),
    "配合左侧总量卡片看周期成本；按月展示时均值会按有数据月份计算。": (
        "Read this with the total card; in monthly mode, averages are calculated across months with data."
    ),
    "ccusage 最新一天的总 Token 消耗。": "Total Token usage on the latest ccusage day.",
    "总量按总输入和输出计算；缓存读取是总输入里命中缓存的部分，推理输出是输出子集。": (
        "Totals are calculated from total input and output; Cache Read is the cached portion of total input, and reasoning output is a subset of output."
    ),
    "ccusage 最近 7 天每日总 Token 的累计值。": "Sum of ccusage daily total Tokens over the last 7 days.",
    "这是滚动 7 日窗口，不是自然周。": "This is a rolling 7-day window, not a calendar week.",
    "最近一次窗口整理里纳入统计的窗口数。": "Number of windows included in the latest window synthesis.",
    "优先来自 daily capture；原始明细缺失时会退回最近一次 nightly summary。": (
        "Uses daily capture first; if raw details are missing, it falls back to the latest nightly summary."
    ),
    "把 ccusage 的日维度数据再加工成 7 日账单、7 日均值、峰值日和缓存读取占总输入等快速判断信号。": (
        "Turns ccusage daily data into quick signals like 7-day estimated bill, 7-day average, peak day, and Cache Read / total-input ratio."
    ),
    "把 ccusage 数据按当前来源、日期范围和展示粒度加工成账单、均值、峰值和缓存读取占比。": (
        "Turns ccusage data into bill, average, peak, and Cache Read ratio signals for the current source, date range, and granularity."
    ),
    "上方两张大卡看总量，速览区看变化和结构，下面的每日 / 今日柱条可以 hover 到具体构成。": (
        "Use the two large cards for totals, the overview for change and structure, and hover the daily/today bars for breakdowns."
    ),
    "上方两张大卡看筛选总量和成本，速览区看周期结构，下面的趋势 / 构成柱条可以 hover 到具体构成。": (
        "Use the two large cards for filtered total and cost, the overview for period structure, and hover the trend / breakdown bars for details."
    ),
    "今日输入柱条对齐 ccusage 表格里的无缓存 Input；缓存读取单独展示为总输入的缓存命中部分。": (
        "The Today input bar matches the uncached Input column in ccusage; Cache Read is shown separately as the cache-hit portion of total input."
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
    "今日和近 7 日并排展示，左边看当天热点，右边看一周趋势。": (
        "Today and the last 7 days are shown side by side: today on the left, weekly trend on the right."
    ),
    "今日 / 近 7 日并排对照": "Today / Last 7 days side by side",
    "左边是当天热词，右边是滚动近 7 日热词。": (
        "The left card shows today's terms; the right card shows the rolling last 7 days."
    ),
    "并排看今日焦点和近 7 日趋势。": "Compare today's focus with the last 7-day trend side by side.",
    "主热词是当前范围内权重最高的词，横条越长代表出现频次越高。": (
        "The primary term is the highest-weighted term in the range; longer bars mean higher frequency."
    ),
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
    "按当前筛选条件展示日维度或月维度 Token 消耗趋势；页面打开后会先显示快照，再尝试刷新实时值。": (
        "Shows daily or monthly Token trends for the current filters; the page shows a snapshot first, then refreshes live values."
    ),
    "ccusage 最新一天的 breakdown。": "The latest daily breakdown from ccusage.",
    "ccusage 当前筛选末端日期或月份的 breakdown。": (
        "The ccusage breakdown for the last date or month in the current filters."
    ),
    "把最新一天的 Token 指标拆成无缓存输入、缓存读取、输出和推理输出。": (
        "Breaks the latest day's Token metrics into uncached input, Cache Read, output, and reasoning output."
    ),
    "把当前筛选末端日期或月份的 Token 指标拆成无缓存输入、缓存读取、输出和推理输出。": (
        "Breaks the last filtered date or month into uncached input, Cache Read, output, and reasoning output."
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
    "同名项目会合并，按讨论数从高到低排序。": (
        "Projects with the same name are merged and sorted by discussion count descending."
    ),
    "这里数的是窗口上下文；资产层的类型与活动面板数的是资产和 skills 读取。": (
        "This counts window context; the Asset Layer type and activity panels count assets and skill reads."
    ),
    "这是按日期切换的每日整理摘要卡，默认展示今天。": (
        "A daily synthesis card switchable by date, defaulting to today."
    ),
    "日期选择器和摘要主结论。": "Date selector and main summary takeaway.",
    "窗口数、个人资产-长期记忆、个人资产-工作记忆、个人资产-低优先级记忆。": (
        "Window count, personal asset long-term memories, personal asset work memories, and personal asset low-priority memories."
    ),
    "最近相关的上下文标签。": "Recently related context labels.",
    "这些数字来自当前整理结果，用来快速判断今天沉淀了多少内容。": (
        "These numbers come from the selected synthesis and help estimate how much was captured that day."
    ),
    "当前登记册中 bucket = durable 的长期记忆，按近 7 日热度排序。": (
        "Long-term memories where bucket = durable in the current registry, sorted by 7-day heat."
    ),
    "state root 下的 registry/memory_entries.jsonl；同一条记忆跨天重复出现时会合并计算。": (
        "registry/memory_entries.jsonl under the state root; repeated memories across days are merged."
    ),
    "这里展示的是当前主视图对应的整理结果；顶部指标卡统计的是 registry/memory_entries.jsonl 的当前数量。": (
        "This shows the synthesis behind the current main view; top metric cards count the current registry/memory_entries.jsonl state."
    ),
    "7 日热度来自可追溯信号：近 7 日直接来源窗口和同一记忆的近期整理日期；不会再用标题、关键词或说明去模糊匹配历史窗口。": (
        "7-day heat uses traceable signals: direct source windows in the last 7 days and recent synthesis dates for the same memory. Titles, keywords, and notes no longer fuzzy-match historical windows."
    ),
    "当前登记册中 bucket = session 的工作记忆，按近 7 日热度排序。": (
        "Work memories where bucket = session in the current registry, sorted by 7-day heat."
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
    "按近 7 日热度排序；同一条记忆跨天重复出现时，会归并展示首次添加和最近更新。": (
        "Sorted by 7-day heat. Repeated memories across days are merged with first-added and latest-updated dates."
    ),
    "基于 registry/memory_entries.jsonl 的整理日志，按记忆签名归并出的当前记忆视图。": (
        "Current memory view grouped by memory signature from registry/memory_entries.jsonl synthesis logs."
    ),
    "按记忆签名归并后，bucket = durable 的个人资产-长期记忆数量。": (
        "Count of Personal Asset - Long-term Memory items after grouping by memory signature where bucket = durable."
    ),
    "按记忆签名归并后，bucket = session 的个人资产-工作记忆数量。": (
        "Count of Personal Asset - Work Memory items after grouping by memory signature where bucket = session."
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
    "7日热度：只统计近 7 日直接来源窗口和近期整理日期；不使用文本相似度估算。": (
        "7-day heat: only counts recent direct source windows and synthesis dates; text similarity is not used."
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
    "读取 MEMORY.md 里的 Task Group 索引，展示历史任务索引和对应来源。": (
        "Reads the Task Group index in MEMORY.md and shows a historical task index with sources."
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
    "工作窗口、长期记忆、工作记忆、低优先级记忆。": (
        "Work windows, long-term memory, work memory, and low-priority memory."
    ),
    "原生记忆偏长期规则、稳定 workflow、历史 rollout 结论。": (
        "Native memory leans toward long-term rules, stable workflows, and historical rollout conclusions."
    ),
    "用户偏好、通用 tips 和历史任务索引已经拆到独立模块。": (
        "User preferences, general tips, and the historical task index are split into separate modules."
    ),
    "cwd / project_label、问题数、结论数。": (
        "cwd / project_label, question count, and conclusion count."
    ),
    "通俗标题、问题结论对、关键词。": "Plain-language title, question/conclusion pairs, and keywords.",
    "已整理窗口可一键切换智能整理与原始信息。": "Organized windows can switch between AI summary and raw info with one click.",
}


def current_language(language=None):
    return overview_i18n.current_language(language, default_language=LANGUAGE)


def is_english(language=None):
    return overview_i18n.is_english(language, default_language=LANGUAGE)


def localized(zh_text, en_text="", language=None):
    return overview_i18n.localized(
        zh_text,
        en_text=en_text,
        language=language,
        translations=PANEL_I18N_EN,
        default_language=LANGUAGE,
    )


def plural_en(count, singular, plural=None):
    return overview_i18n.plural_en(count, singular, plural=plural)


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
    "skill": "供 Codex 在特定场景下调用的 skills，通常对应一个可发现的 SKILL。",
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
            "资产层图表",
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
            "工作",
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
def current_local_datetime():
    return overview_common.current_local_datetime()


def parse_iso_datetime(value):
    return overview_common.parse_iso_datetime(value)


def display_local_datetime(value):
    return overview_common.display_local_datetime(value)


def display_short_local_datetime(value):
    return overview_common.display_short_local_datetime(value)


def resolve_npx_binary():
    return overview_token_fetcher.resolve_npx_binary()


def build_subprocess_env():
    return overview_token_fetcher.build_subprocess_env()


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


def normalize_loaded_memory_item_quality(item):
    stage = str(item.get("stage") or item.get("summary_stage") or "").strip().lower()
    generation = str(item.get("summary_generation") or item.get("model_status") or "").strip().lower()
    if stage == "preliminary" or generation in {"lightweight", "skipped_lightweight"}:
        return None
    if item.get("user_feedback") == overview_memory_feedback.FEEDBACK_DOWNVOTED:
        row = dict(item)
        row["bucket"] = "low_priority"
        row["priority"] = "low"
        row["scope"] = "local"
        row["injection_policy"] = "local_only"
        row.setdefault("storage_quality_score", 0)
        row.setdefault("storage_quality_reason", "user_downvoted")
        return row
    quality = overview_memory_context.memory_storage_quality(item, bucket=item.get("bucket", ""))
    if quality["disposition"] == "drop":
        return None
    row = dict(item)
    row.setdefault("storage_quality_score", quality["score"])
    row.setdefault("storage_quality_reason", quality["reason"])
    if quality["disposition"] == "demote":
        row["bucket"] = "low_priority"
        row["priority"] = "low"
        row["scope"] = "local"
        row["injection_policy"] = "local_only"
    return row


def load_memory_registry_items():
    canonical_path = REGISTRY_DIR / "memory_entries.jsonl"
    legacy_path = REGISTRY_DIR / "memory_items.jsonl"
    rows = []
    if canonical_path.exists() and canonical_path.stat().st_size > 0:
        rows.extend(load_jsonl(canonical_path))
    else:
        rows.extend(load_jsonl(legacy_path))
    feedback_by_key = overview_memory_feedback.load_memory_feedback_map(PATHS)
    return [
        row
        for row in (
            normalize_loaded_memory_item_quality(
                overview_memory_feedback.apply_memory_feedback_map(item, feedback_by_key)
            )
            for item in rows
        )
        if row is not None
    ]


def load_asset_stats_snapshot(path=ASSET_STATS_LATEST_PATH):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def safe_int(value):
    return overview_common.safe_int(value)


def safe_float(value):
    return overview_common.safe_float(value)


def memory_feedback_sort_rank(item):
    feedback = str((item or {}).get("user_feedback") or "").strip()
    if feedback == overview_memory_feedback.FEEDBACK_LIKED:
        return 2
    if feedback == overview_memory_feedback.FEEDBACK_DOWNVOTED:
        return -10
    return 0


def compact_number(value):
    return overview_common.compact_number(value)


def compact_token_zh(value):
    return overview_common.compact_token_zh(value)


def compact_token(value, language=None):
    return overview_common.compact_token(value, language=current_language(language))


def compact_token_k(value):
    return overview_common.compact_token_k(value)


def format_percent(value, digits=0, signed=False):
    return overview_common.format_percent(value, digits=digits, signed=signed)


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


def sort_memory_summary_context_rows(context_rows):
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    bucket_rank = {"durable": 0, "session": 1}

    def sort_key(row):
        return (
            -memory_feedback_sort_rank(row),
            bucket_rank.get(row.get("bucket", ""), 2),
            priority_rank.get(row.get("priority", "medium"), 1),
            memory_summary_reverse_date_sort_key(row.get("updated_at") or row.get("date") or row.get("created_at")),
            -safe_int(row.get("occurrence_count", 0)),
            row.get("title", "") or row.get("display_title", ""),
        )

    return sorted(context_rows or [], key=sort_key)


def select_memory_summary_context_rows(context_rows, max_items, token_budget, include_heading=True):
    if not context_rows or token_budget <= 0:
        return [], 0
    heading_tokens, _ = estimate_summary_tokens("### Local personal memory registry\n") if include_heading else (0, "heuristic")
    used_tokens = heading_tokens
    selected_rows = []
    has_item_cap = max_items > 0
    for row in sort_memory_summary_context_rows(context_rows):
        if has_item_cap and len(selected_rows) >= max_items:
            break
        row_tokens = estimate_memory_row_tokens(row)
        if row_tokens <= 0:
            continue
        if used_tokens + row_tokens > token_budget:
            continue
        used_tokens += row_tokens
        selected_rows.append(row)
    if not selected_rows:
        return [], 0
    return selected_rows, min(used_tokens, token_budget)


def estimate_memory_summary_fit(context_rows, max_items, token_budget):
    selected_rows, used_tokens = select_memory_summary_context_rows(
        context_rows,
        max_items,
        token_budget,
    )
    return len(selected_rows), used_tokens


def memory_summary_row_is_global_context(row):
    return overview_memory_context.host_context_injection_policy_from_record(row) == overview_memory_context.INJECTION_GLOBAL_CONTEXT


def memory_summary_row_is_project_context(row):
    return overview_memory_context.host_context_injection_policy_from_record(row) == overview_memory_context.INJECTION_PROJECT_CONTEXT


def split_memory_summary_context_rows(context_rows):
    global_rows = []
    project_rows = []
    for row in context_rows or []:
        if memory_summary_row_is_global_context(row):
            global_rows.append(row)
        elif memory_summary_row_is_project_context(row):
            project_rows.append(row)
    return global_rows, project_rows


def select_bounded_memory_summary_context_rows(context_rows, summary_budget, max_items):
    global_rows, project_rows = split_memory_summary_context_rows(context_rows)
    global_budget = int(summary_budget.get("global_memory_tokens") or 0)
    project_budget = int(summary_budget.get("project_memory_tokens") or 0)
    personal_budget = int(summary_budget.get("personal_memory_tokens") or 0)
    if global_budget <= 0 and project_budget <= 0:
        if project_rows:
            global_budget = max(100, min(personal_budget, int(round(personal_budget * 0.25))))
            project_budget = max(0, personal_budget - global_budget)
        else:
            global_budget = personal_budget

    selected_global, global_tokens = select_memory_summary_context_rows(
        global_rows,
        max_items,
        global_budget,
        include_heading=True,
    )
    selected_project, project_tokens = select_memory_summary_context_rows(
        project_rows,
        max_items,
        project_budget,
        include_heading=not bool(selected_global),
    )
    return selected_global + selected_project, global_tokens + project_tokens


def build_personal_memory_context_preview(
    memory_registry,
    memory_mode,
    memory_summary_budget=None,
    item_count=None,
):
    if str(memory_mode or "integrated") != "integrated":
        return []
    summary_budget = memory_summary_budget or get_memory_summary_budget(PATHS)
    rows = memory_registry or []
    context_rows = [
        row
        for row in rows
        if overview_memory_context.memory_record_is_host_context_candidate(row)
    ]
    has_candidate_cap = MEMORY_SUMMARY_MAX_PERSONAL_MEMORY_ITEMS > 0
    context_item_limit = (
        min(len(context_rows), MEMORY_SUMMARY_MAX_PERSONAL_MEMORY_ITEMS)
        if has_candidate_cap
        else len(context_rows)
    )
    selected_rows, _ = select_bounded_memory_summary_context_rows(
        context_rows,
        summary_budget,
        context_item_limit,
    )
    if item_count is not None:
        selected_rows = sort_memory_summary_context_rows(selected_rows)[: max(0, safe_int(item_count))]
    return selected_rows


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
    global_memory_budget_tokens = summary_budget.get("global_memory_tokens", MEMORY_SUMMARY_GLOBAL_MEMORY_TOKENS)
    project_memory_budget_tokens = summary_budget.get("project_memory_tokens", MEMORY_SUMMARY_PROJECT_MEMORY_TOKENS)
    enabled = memory_mode != "off"
    rows = memory_registry or []
    row_count = len(rows)
    context_rows = sort_memory_summary_context_rows(
        [
            row
            for row in rows
            if overview_memory_context.memory_record_is_host_context_candidate(row)
        ]
    )
    has_candidate_cap = MEMORY_SUMMARY_MAX_PERSONAL_MEMORY_ITEMS > 0
    context_item_limit = (
        min(len(context_rows), MEMORY_SUMMARY_MAX_PERSONAL_MEMORY_ITEMS)
        if has_candidate_cap
        else len(context_rows)
    )
    selected_context_rows, estimated_personal_memory_tokens = select_bounded_memory_summary_context_rows(
        context_rows,
        summary_budget,
        context_item_limit,
    )
    estimated_context_item_count = len(selected_context_rows)
    count_label_zh = "约"
    count_label_en = "about"
    estimated_tokens = estimated_personal_memory_tokens if memory_mode == "integrated" else 0
    max_tokens_display = compact_token_k(summary_max_tokens)
    target_tokens_display = compact_token_k(summary_target_tokens)
    warn_tokens_display = compact_token_k(summary_warn_tokens)
    personal_budget_display = compact_token_k(personal_memory_budget_tokens)
    global_budget_display = compact_token_k(global_memory_budget_tokens)
    project_budget_display = compact_token_k(project_memory_budget_tokens)
    estimated_personal_display = compact_token_k(estimated_personal_memory_tokens)

    if memory_mode == "integrated":
        mode_label_zh = "Integrated"
        mode_label_en = "Integrated"
        if not context_rows:
            candidate_policy_zh = "当前无可注入候选"
            candidate_policy_en = "no injectable candidates"
        elif has_candidate_cap:
            candidate_policy_zh = "候选上限 {} 条".format(context_item_limit)
            candidate_policy_en = "candidate cap {}".format(context_item_limit)
        else:
            candidate_policy_zh = "候选不设条数上限"
            candidate_policy_en = "no item cap"
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
        caption_zh = "摘要目标 {} / 警戒 {} / 上限 {}；全局 {} / 项目 {}".format(
            target_tokens_display,
            warn_tokens_display,
            max_tokens_display,
            global_budget_display,
            project_budget_display,
        )
        caption_en = "Summary target {} / warning {} / max {}; global {} / project {}".format(
            target_tokens_display,
            warn_tokens_display,
            max_tokens_display,
            global_budget_display,
            project_budget_display,
        )
        status_zh = "受控"
        status_en = "Bounded"
        value_zh = "≈ {}".format(estimated_personal_display)
        value_en = "≈ {}".format(estimated_personal_display)
        meter_percent = min(100, round(estimated_tokens / summary_max_tokens * 100))
    elif memory_mode == "local-only":
        mode_label_zh = "本地记录"
        mode_label_en = "Local-only"
        mode_note_zh = "{} 条只写本地，不注入 host context".format(row_count)
        mode_note_en = "{} items stay local and are not injected into host context".format(row_count)
        caption_zh = "Host context 占用 0K"
        caption_en = "Host context usage 0K"
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
        caption_zh = "Host context 占用 0K"
        caption_en = "Host context usage 0K"
        status_zh = "关闭"
        status_en = "Off"
        value_zh = "0K"
        value_en = "0K"
        meter_percent = 0

    method_note_zh = (
        "面板展示的是 bounded summary 预算状态，不是完整登记册体积；"
        "默认上限 8K；当前 target {}、warn {}、max {} 会随配置的 max 自动派生，全局记忆 {}、当前项目记忆 {}。"
    ).format(
        target_tokens_display,
        warn_tokens_display,
        max_tokens_display,
        global_budget_display,
        project_budget_display,
    )
    method_note_en = (
        "This card shows bounded-summary budget status, not the full registry footprint; "
        "the default max is 8K; current target {}, warning {}, and max {} are derived from the configured max, with {} for global memory and {} for the active project."
    ).format(
        target_tokens_display,
        warn_tokens_display,
        max_tokens_display,
        global_budget_display,
        project_budget_display,
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
        "global_memory_budget_tokens": global_memory_budget_tokens,
        "global_memory_budget_display": global_budget_display,
        "project_memory_budget_tokens": project_memory_budget_tokens,
        "project_memory_budget_display": project_budget_display,
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
    return overview_common.percent_of(part, total)


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


def is_current_local_date(date_value):
    if not date_value:
        return False
    try:
        parsed_date = datetime.fromisoformat(str(date_value)).date()
    except ValueError:
        return False
    return parsed_date == current_local_datetime().date()


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


def load_history_fallback_daily_capture(date_str):
    if not date_str:
        return None
    try:
        import collect_codex_activity

        windows = collect_codex_activity.load_history_windows_for_date(date_str, "manual")
    except Exception:  # noqa: BLE001
        return None
    if not windows:
        return None

    review_like_windows = [window for window in windows if window.get("review_like_window")]
    return {
        "source_kind": "history_fallback",
        "date": date_str,
        "stage": "manual",
        "generated_at": "",
        "timezone": "",
        "collection_source": "history",
        "collection_errors": [],
        "window_count": len(windows),
        "excluded_window_count": 0,
        "review_like_window_count": len(review_like_windows),
        "prompt_count": sum(safe_int(window.get("prompt_count", 0)) for window in windows),
        "conclusion_count": sum(safe_int(window.get("conclusion_count", 0)) for window in windows),
        "windows": windows,
        "excluded_windows": [],
        "review_like_windows": review_like_windows,
    }


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


def make_current_day_preview_command():
    return "openrelix review --stage preliminary --learn-window-days 0"


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
    return overview_token_fetcher.load_token_usage_cache(TOKEN_CACHE_PATH)


def write_token_usage_cache(payload):
    overview_token_fetcher.write_token_usage_cache(payload, TOKEN_CACHE_PATH)


def fetch_ccusage_daily(window_days=CCUSAGE_WINDOW_DAYS, provider="all", start_date=None, end_date=None):
    return overview_token_fetcher.fetch_ccusage_daily(
        window_days=window_days,
        now_func=current_local_datetime,
        resolve_npx_binary_func=resolve_npx_binary,
        env_func=build_subprocess_env,
        provider=provider,
        start_date=start_date,
        end_date=end_date,
    )


def resolve_ccusage_daily(provider="all", window_days=CCUSAGE_WINDOW_DAYS, start_date=None, end_date=None):
    return overview_token_fetcher.resolve_ccusage_daily(
        cache_path=TOKEN_CACHE_PATH,
        fetch_func=fetch_ccusage_daily,
        provider=provider,
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
    )


def make_token_breakdown_detail(label, value, meta="", language=None):
    return overview_token_usage.make_token_breakdown_detail(
        label,
        value,
        meta=meta,
        language=current_language(language),
    )


def split_ccusage_input_tokens(row):
    return overview_token_usage.split_ccusage_input_tokens(row)


def build_token_breakdown_details(row, language=None):
    return overview_token_usage.build_token_breakdown_details(
        row,
        language=current_language(language),
    )


def make_token_summary_card(label, value, caption, tone="neutral"):
    return overview_token_usage.make_token_summary_card(label, value, caption, tone=tone)


def format_usd(value):
    return overview_token_usage.format_usd(value)


def compact_token_with_cost(token_value, cost_value, language=None):
    return overview_token_usage.compact_token_with_cost(
        token_value,
        cost_value,
        language=current_language(language),
    )


def build_token_summary_cards(parsed_rows, trailing_rows, latest, language=None):
    return overview_token_usage.build_token_summary_cards(
        parsed_rows,
        trailing_rows,
        latest,
        language=current_language(language),
    )


def token_daily_tone(value, max_value):
    return overview_token_usage.token_daily_tone(value, max_value)


def token_breakdown_tone(kind):
    return overview_token_usage.token_breakdown_tone(kind)


def recent_token_daily_rows(parsed_rows, window_days=CCUSAGE_WINDOW_DAYS):
    return overview_token_usage.recent_token_daily_rows(
        parsed_rows,
        window_days=window_days,
        now_func=current_local_datetime,
    )


def build_token_usage_view(ccusage_result, language=None, group_by=None, start_date=None, end_date=None):
    return overview_token_usage.build_token_usage_view(
        ccusage_result,
        language=current_language(language),
        now_func=current_local_datetime,
        group_by=group_by,
        start_date=start_date,
        end_date=end_date,
    )


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
    return overview_labels.display_label(
        kind,
        value,
        language=language,
        is_english_func=is_english,
        humanize_func=humanize_identifier,
    )


def display_discovered_asset_kind(value, language=None):
    return overview_labels.display_discovered_asset_kind(
        value,
        language=language,
        is_english_func=is_english,
        humanize_func=humanize_identifier,
    )


def display_memory_bucket(value, language=None):
    return overview_labels.display_memory_bucket(
        value,
        language=language,
        is_english_func=is_english,
        humanize_func=humanize_identifier,
    )


def display_memory_type(value, language=None):
    return overview_labels.display_memory_type(
        value,
        language=language,
        is_english_func=is_english,
        humanize_func=humanize_identifier,
    )


def display_memory_priority(value, language=None):
    return overview_labels.display_memory_priority(
        value,
        language=language,
        is_english_func=is_english,
        humanize_func=humanize_identifier,
    )


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
            r"采集：Codex app-server · 线程来源：(.+)",
            lambda match: "Collection: Codex app-server · thread source: {}".format(match.group(1)),
        ),
        (
            r"采集：Codex app-server",
            lambda match: "Collection: Codex app-server",
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
            r"占总输入 (.+)",
            lambda match: "{} of total input".format(match.group(1)),
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
            r"缓存 (.+) / 总输入 (.+)",
            lambda match: "Cache Read {} / total input {}".format(match.group(1), match.group(2)),
        ),
        (
            r"缓存读取 (.+) / 总输入 (.+)",
            lambda match: "Cache Read {} / total input {}".format(match.group(1), match.group(2)),
        ),
        (
            r"缓存 (.+) / 输入 (.+)",
            lambda match: "Cache Read {} / input {}".format(match.group(1), match.group(2)),
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
            r"直接读取 (.+) 的“What's in Memory”记忆条目(?:，.+)?。",
            lambda match: "Reads memory items from the \"What's in Memory\" section of {}.".format(
                match.group(1)
            ),
        ),
        (
            r"记忆条目 (\d+) 条；用户偏好 (\d+) 条；通用 tips (\d+) 条。",
            lambda match: "{}; {}; {}.".format(
                plural_en(match.group(1), "memory item"),
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
    text = "" if text is None else str(text)
    english_text = normalize_brand_display_text(en_text or panel_english_text(text) or text)
    english_text = "" if english_text is None else str(english_text)
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
    zh_text = "" if zh_text is None else str(zh_text)
    en_text = normalize_brand_display_text(en_text or panel_english_text(zh_text) or "")
    en_text = "" if en_text is None else str(en_text)
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
    return overview_i18n.panel_i18n_json(PANEL_I18N_EN)


def normalize_memory_signature_text(text):
    return overview_memory_registry.normalize_memory_signature_text(text)


def build_memory_group_key(item, bucket=""):
    return overview_memory_registry.build_memory_group_key(item, bucket=bucket)


def memory_usage_direct_window_ids(memory_item):
    window_ids = set()
    for ref in memory_item.get("source_windows", []) or []:
        if isinstance(ref, dict):
            window_id = str(ref.get("window_id") or "").strip()
        else:
            window_id = str(ref or "").strip()
        if window_id:
            window_ids.add(window_id)
    raw_source_window_ids = memory_item.get("source_window_ids", []) or []
    if isinstance(raw_source_window_ids, str):
        raw_source_window_ids = [raw_source_window_ids]
    for ref in raw_source_window_ids:
        window_id = str(ref or "").strip()
        if window_id:
            window_ids.add(window_id)
    return window_ids


def memory_usage_recent_windows(anchor_date, windows):
    anchor = parse_nightly_summary_date({"date": anchor_date})
    recent = []
    for window in windows or []:
        if not isinstance(window, dict):
            continue
        window_id = str(window.get("window_id") or "").strip()
        if not window_id:
            continue
        current = parse_nightly_summary_date({"date": window.get("date", "")})
        if anchor is None or current is None:
            recent.append(window)
            continue
        age_days = (anchor - current).days
        if 0 <= age_days < MEMORY_USAGE_WINDOW_DAYS:
            recent.append(window)
    return recent


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


def build_memory_usage_frequency(memory_item, usage_window_overview, recent_occurrence_dates=None):
    windows = (usage_window_overview or {}).get("windows", [])
    anchor_date = (usage_window_overview or {}).get("date", "") or current_local_datetime().date().isoformat()
    direct_window_ids = memory_usage_direct_window_ids(memory_item)
    recent_windows = memory_usage_recent_windows(anchor_date, windows)
    matched_window_ids = [
        window.get("window_id", "")
        for window in recent_windows
        if window.get("window_id", "") in direct_window_ids
    ]
    direct_matches = len(set(matched_window_ids))

    recent_occurrence_dates = filter_memory_usage_occurrence_dates(
        anchor_date,
        recent_occurrence_dates or [],
    )
    recent_occurrence_count = len(set(recent_occurrence_dates))
    score = direct_matches + recent_occurrence_count * 0.45
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
        "usage_frequency_estimated_window_count": 0,
        "usage_frequency_context_hint_count": 0,
        "usage_frequency_matched_window_count": len(set(matched_window_ids)),
        "usage_frequency_recent_occurrence_count": recent_occurrence_count,
        "usage_frequency_terms": [],
        "usage_frequency_score_kind": "traceable_evidence",
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
    return overview_memory_registry.memory_sort_key(value)


def display_memory_date(value):
    return overview_memory_registry.display_memory_date(value)


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
        "Last {} Hot Terms".format(plural_en(days, "day").title()),
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


def compact_preview_text(text, limit=220, strip_markdown=True):
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
    if strip_markdown:
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


def render_markdown_inline(text):
    raw = str(text or "")
    if not raw:
        return ""

    placeholders = []

    def placeholder(html):
        token = "\0OPENRELIXMD{}\0".format(len(placeholders))
        placeholders.append((token, html))
        return token

    def inline_code_repl(match):
        return placeholder("<code>{}</code>".format(escape(match.group(1))))

    def link_repl(match):
        label = match.group(1).strip()
        url = match.group(2).strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https", "file"}:
            return label
        return placeholder(
            '<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'.format(
                href=escape(url, quote=True),
                label=escape(label),
            )
        )

    raw = re.sub(r"`([^`\n]+)`", inline_code_repl, raw)
    raw = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", link_repl, raw)
    rendered = escape(raw)
    rendered = re.sub(r"\*\*([^*\n][^*\n]*?)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"__([^_\n][^_\n]*?)__", r"<strong>\1</strong>", rendered)
    for token, html in placeholders:
        rendered = rendered.replace(token, html)
    return rendered


def render_markdown_text(text):
    raw = normalize_brand_display_text(str(text or "")).strip()
    if not raw:
        return ""

    blocks = []
    paragraph_lines = []
    unordered_items = []
    ordered_items = []
    quote_lines = []
    code_lines = []
    in_code = False

    def flush_paragraph():
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        paragraph = "\n".join(paragraph_lines).strip()
        if paragraph:
            blocks.append(
                "<p>{}</p>".format(render_markdown_inline(paragraph).replace("\n", "<br>"))
            )
        paragraph_lines = []

    def flush_unordered_items():
        nonlocal unordered_items
        if not unordered_items:
            return
        blocks.append(
            "<ul>{}</ul>".format(
                "".join("<li>{}</li>".format(render_markdown_inline(item)) for item in unordered_items)
            )
        )
        unordered_items = []

    def flush_ordered_items():
        nonlocal ordered_items
        if not ordered_items:
            return
        blocks.append(
            "<ol>{}</ol>".format(
                "".join("<li>{}</li>".format(render_markdown_inline(item)) for item in ordered_items)
            )
        )
        ordered_items = []

    def flush_quote_lines():
        nonlocal quote_lines
        if not quote_lines:
            return
        quote = "\n".join(quote_lines).strip()
        if quote:
            blocks.append(
                "<blockquote>{}</blockquote>".format(
                    render_markdown_inline(quote).replace("\n", "<br>")
                )
            )
        quote_lines = []

    def flush_code_lines():
        nonlocal code_lines
        blocks.append("<pre><code>{}</code></pre>".format(escape("\n".join(code_lines))))
        code_lines = []

    def flush_open_blocks():
        flush_paragraph()
        flush_unordered_items()
        flush_ordered_items()
        flush_quote_lines()

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                flush_code_lines()
                in_code = False
            else:
                flush_open_blocks()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            flush_open_blocks()
            continue
        heading_match = re.match(r"^\s{0,3}(#{1,4})\s+(.+)$", line)
        unordered_match = re.match(r"^\s*[-*+]\s+(.+)$", line)
        ordered_match = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        quote_match = re.match(r"^\s*>\s?(.+)$", line)
        if heading_match:
            flush_open_blocks()
            blocks.append(
                '<p class="window-markdown-heading">{}</p>'.format(
                    render_markdown_inline(heading_match.group(2))
                )
            )
        elif unordered_match:
            flush_paragraph()
            flush_ordered_items()
            flush_quote_lines()
            unordered_items.append(unordered_match.group(1).strip())
        elif ordered_match:
            flush_paragraph()
            flush_unordered_items()
            flush_quote_lines()
            ordered_items.append(ordered_match.group(1).strip())
        elif quote_match:
            flush_paragraph()
            flush_unordered_items()
            flush_ordered_items()
            quote_lines.append(quote_match.group(1).strip())
        else:
            flush_unordered_items()
            flush_ordered_items()
            flush_quote_lines()
            paragraph_lines.append(line)
    if in_code:
        flush_code_lines()
    flush_open_blocks()
    return "".join(blocks)


def split_path_trailing_punctuation(token):
    return overview_local_paths.split_path_trailing_punctuation(token)


def strip_line_column_suffix(path_text):
    return overview_local_paths.strip_line_column_suffix(path_text)


def resolve_local_link_path(raw_path):
    return overview_local_paths.resolve_local_link_path(raw_path)


def build_local_path_anchor(path, label, class_name="path-link"):
    return overview_local_paths.build_local_path_anchor(
        path,
        label,
        class_name=class_name,
        normalize_text_func=normalize_brand_display_text,
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
    normalized_label = normalize_brand_display_text(label)
    safe_label = escape("" if normalized_label is None else str(normalized_label))
    if not target_id:
        return safe_label
    return '<a class="{class_name}" href="#{target_id}">{label}</a>'.format(
        class_name=escape(class_name, quote=True),
        target_id=escape(str(target_id), quote=True),
        label=safe_label,
    )


def render_detected_local_path_token(token, class_name="path-link"):
    return overview_local_paths.render_detected_local_path_token(
        token,
        class_name=class_name,
        normalize_text_func=normalize_brand_display_text,
    )


def linkify_local_paths_html(text, class_name="path-link"):
    return overview_local_paths.linkify_local_paths_html(
        text,
        class_name=class_name,
        normalize_text_func=normalize_brand_display_text,
    )


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

    def append_source_window_ref(container, source_item):
        window_id = str(source_item.get("window_id", "") or "").strip()
        if not window_id:
            return
        refs = container.setdefault("source_windows", [])
        if any(ref.get("window_id") == window_id for ref in refs):
            return
        display_label = str(source_item.get("display_index", "") or "").strip() or window_id[:8]
        title = (
            source_item.get("window_title", "")
            or source_item.get("window_summary", "")
            or source_item.get("thread_title", "")
            or source_item.get("question_summary", "")
            or source_item.get("main_takeaway", "")
        )
        refs.append(
            {
                "window_id": window_id,
                "anchor_id": "window-{}".format(window_id),
                "display_label": display_label,
                "latest_activity_display": source_item.get("latest_activity_display", ""),
                "title": compact_preview_text(title, limit=80),
            }
        )

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
                "source_windows": [],
                "topics": {},
            },
        )

        group["window_count"] += 1
        group["question_count"] += item.get("question_count", 0)
        group["conclusion_count"] += item.get("conclusion_count", 0)
        append_source_window_ref(group, item)

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
                "source_windows": [],
            },
        )
        topic["window_count"] += 1
        topic["question_count"] += item.get("question_count", 0)
        topic["conclusion_count"] += item.get("conclusion_count", 0)
        append_source_window_ref(topic, item)
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
                    "source_windows": topic.get("source_windows", []),
                }
            )
        topics.sort(
            key=lambda item: (
                item.get("window_count", 0),
                item.get("question_count", 0) + item.get("conclusion_count", 0),
                parse_iso_datetime(item.get("latest_activity_at", "")).timestamp()
                if item.get("latest_activity_at")
                else 0,
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
                "source_windows": group.get("source_windows", []),
            }
        )

    rows.sort(
        key=lambda item: (
            item.get("question_count", 0) + item.get("conclusion_count", 0),
            item.get("window_count", 0),
            parse_iso_datetime(item.get("latest_activity_at", "")).timestamp()
            if item.get("latest_activity_at")
            else 0,
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
                "scope": overview_memory_context.memory_scope_from_record(item),
                "injection_policy": overview_memory_context.host_context_injection_policy_from_record(item),
                "project_key": item.get("project_key", ""),
                "project_label": item.get("project_label", ""),
                "title": item.get("title", ""),
                "title_zh": item.get("title_zh", ""),
                "title_en": item.get("title_en", ""),
                "value_note": item.get("value_note", ""),
                "value_note_zh": item.get("value_note_zh", ""),
                "value_note_en": item.get("value_note_en", ""),
                "user_feedback": item.get("user_feedback", ""),
                "user_feedback_updated_at": item.get("user_feedback_updated_at", ""),
                "user_pinned": bool(item.get("user_pinned")),
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
            group["scope"] = overview_memory_context.memory_scope_from_record(item)
            group["injection_policy"] = overview_memory_context.host_context_injection_policy_from_record(item)
            group["project_key"] = item.get("project_key", group.get("project_key", ""))
            group["project_label"] = item.get("project_label", group.get("project_label", ""))
            group["title"] = item.get("title", group["title"])
            group["title_zh"] = item.get("title_zh", group.get("title_zh", ""))
            group["title_en"] = item.get("title_en", group.get("title_en", ""))
            group["value_note"] = item.get("value_note", group["value_note"])
            group["value_note_zh"] = item.get("value_note_zh", group.get("value_note_zh", ""))
            group["value_note_en"] = item.get("value_note_en", group.get("value_note_en", ""))
            group["user_feedback"] = item.get("user_feedback", group.get("user_feedback", ""))
            group["user_feedback_updated_at"] = item.get(
                "user_feedback_updated_at",
                group.get("user_feedback_updated_at", ""),
            )
            group["user_pinned"] = bool(item.get("user_pinned", group.get("user_pinned", False)))
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
        row_scope = group.get("scope", "")
        row_injection_policy = group.get("injection_policy", "")
        row_project_label = group.get("project_label", "")
        if row_scope in {"project", "repo"} and not row_project_label:
            row_project_label = display_context if display_context != "未分类上下文" else ""
        row_project_key = group.get("project_key", "")
        if row_project_label and not row_project_key:
            row_project_key = re.sub(
                r"[^a-z0-9\u4e00-\u9fff]+",
                "-",
                str(row_project_label).lower(),
            ).strip("-")

        row = {
            "memory_key": group["memory_key"],
            "bucket": group["bucket"],
            "display_bucket": display_memory_bucket(group["bucket"], language=language),
            "memory_type": group["memory_type"],
            "display_memory_type": display_memory_type(group["memory_type"], language=language),
            "priority": group["priority"],
            "display_priority": display_memory_priority(group["priority"], language=language),
            "scope": row_scope,
            "injection_policy": row_injection_policy,
            "project_key": row_project_key,
            "project_label": row_project_label,
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
            "user_feedback": group.get("user_feedback", ""),
            "user_feedback_updated_at": group.get("user_feedback_updated_at", ""),
            "user_pinned": bool(group.get("user_pinned")),
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
            memory_feedback_sort_rank(item),
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
    return overview_local_paths.extract_resolved_local_paths(
        text,
        prefer_parent=prefer_parent,
    )


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


# All codex-native rule data ships empty by default. Per-user matching rules
# live at <state_root>/personal_codex_rules.py — that path is outside the
# git repo and the npm package, so personal project names cannot leak in.
# See _load_personal_codex_native_rules() below for the supported keys.
CODEX_NATIVE_TITLE_ZH = {}
CODEX_NATIVE_NOTE_ZH = {}
CODEX_NATIVE_TASK_BODY_ZH = {}
CODEX_NATIVE_BULLET_ZH = {}
CODEX_NATIVE_TOPIC_RULES_ZH = []
CODEX_NATIVE_BULLET_RULES_ZH = []
CODEX_NATIVE_BULLET_TITLE_EN_BY_ZH = {}
CODEX_NATIVE_STRUCTURED_LINE_RE = re.compile(
    r"^\[(?P<bucket>[a-z_]+)/(?P<memory_type>[a-z_]+)/(?P<priority>[a-z_]+)\]\s+(?P<body>.+)$",
    flags=re.IGNORECASE,
)
CODEX_NATIVE_GENERIC_TOPIC_TITLE_ZH = "Codex 原生记忆条目"
CODEX_NATIVE_GENERIC_TOPIC_NOTE_ZH = "来自 Codex 原生记忆的记忆条目；英文原文已折叠，可展开核对来源。"
CODEX_NATIVE_TASK_GROUP_LABEL_RULES_ZH = ()


def _load_personal_codex_native_rules():
    extras = {
        "title": {},
        "note": {},
        "task_body": {},
        "bullet": {},
        "topic_rules": [],
        "bullet_rules": [],
        "bullet_title_en": {},
        "task_group_label_rules": [],
    }
    try:
        path = PATHS.state_root / "personal_codex_rules.py"
    except Exception:
        return extras
    if not path.is_file():
        return extras
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("openrelix_personal_codex_rules", path)
        if spec is None or spec.loader is None:
            return extras
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        return extras
    extras["title"] = dict(getattr(module, "EXTRA_TITLE_ZH", {}) or {})
    extras["note"] = dict(getattr(module, "EXTRA_NOTE_ZH", {}) or {})
    extras["task_body"] = dict(getattr(module, "EXTRA_TASK_BODY_ZH", {}) or {})
    extras["bullet"] = dict(getattr(module, "EXTRA_BULLET_ZH", {}) or {})
    extras["topic_rules"] = list(getattr(module, "EXTRA_TOPIC_RULES_ZH", []) or [])
    extras["bullet_rules"] = list(getattr(module, "EXTRA_BULLET_RULES_ZH", []) or [])
    extras["bullet_title_en"] = dict(getattr(module, "EXTRA_BULLET_TITLE_EN_BY_ZH", {}) or {})
    extras["task_group_label_rules"] = list(
        getattr(module, "EXTRA_TASK_GROUP_LABEL_RULES_ZH", []) or []
    )
    return extras


_PERSONAL_CODEX_NATIVE_RULES = _load_personal_codex_native_rules()


def _codex_native_title(key):
    return CODEX_NATIVE_TITLE_ZH.get(key) or _PERSONAL_CODEX_NATIVE_RULES["title"].get(key)


def _codex_native_note(key):
    return CODEX_NATIVE_NOTE_ZH.get(key) or _PERSONAL_CODEX_NATIVE_RULES["note"].get(key)


def _codex_native_task_body(key):
    return CODEX_NATIVE_TASK_BODY_ZH.get(key) or _PERSONAL_CODEX_NATIVE_RULES["task_body"].get(key)


def _codex_native_task_body_has_key(key):
    return key in CODEX_NATIVE_TASK_BODY_ZH or key in _PERSONAL_CODEX_NATIVE_RULES["task_body"]


def _codex_native_bullet(key):
    return CODEX_NATIVE_BULLET_ZH.get(key) or _PERSONAL_CODEX_NATIVE_RULES["bullet"].get(key)


def _codex_native_bullet_items():
    yield from CODEX_NATIVE_BULLET_ZH.items()
    yield from _PERSONAL_CODEX_NATIVE_RULES["bullet"].items()


def codex_native_translation_key(title):
    return normalize_context_match_text(title)


def codex_native_display_source_text(title="", body=""):
    text = normalize_brand_display_text(
        "\n".join(str(part or "").strip() for part in (title, body) if str(part or "").strip())
    )
    return re.sub(r"`([^`]+)`", r"\1", text)


def codex_native_display_cache_key(kind, title="", body=""):
    source_text = codex_native_display_source_text(title, body)
    digest = hashlib.sha256(
        "{}\0{}".format(kind or "item", source_text).encode("utf-8")
    ).hexdigest()
    return "{}:{}".format(kind or "item", digest[:24])


@lru_cache(maxsize=1)
def load_codex_native_display_cache():
    try:
        payload = json.loads(CODEX_NATIVE_DISPLAY_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return {}
    raw_items = payload.get("items") or {}
    if isinstance(raw_items, list):
        raw_items = {
            item.get("key"): item
            for item in raw_items
            if isinstance(item, dict) and item.get("key")
        }
    if not isinstance(raw_items, dict):
        return {}
    items = {}
    for key, item in raw_items.items():
        if not isinstance(item, dict):
            continue
        title_zh = normalize_brand_display_text(item.get("title_zh", ""))
        body_zh = normalize_brand_display_text(item.get("body_zh", ""))
        if not title_zh and not body_zh:
            continue
        items[str(key)] = {
            "title_zh": title_zh,
            "body_zh": body_zh,
        }
    return items


def codex_native_cached_display(kind, title="", body="", language=None):
    if is_english(language):
        return {}
    key = codex_native_display_cache_key(kind, title, body)
    return load_codex_native_display_cache().get(key, {})


def parse_codex_native_structured_line(line):
    match = CODEX_NATIVE_STRUCTURED_LINE_RE.match(str(line or "").strip())
    if not match:
        return {}
    body = normalize_brand_display_text(match.group("body").strip())
    title, separator, note = body.partition(" - ")
    return {
        "bucket": match.group("bucket").lower(),
        "memory_type": match.group("memory_type").lower(),
        "priority": match.group("priority").lower(),
        "title": title.strip() or body,
        "note": note.strip() if separator else "",
    }


def is_untranslated_english_text(text):
    text = normalize_brand_display_text(str(text or "")).strip()
    return bool(text and not contains_cjk(text) and re.search(r"[A-Za-z]", text))


def codex_native_task_group_labels_zh(title="", keywords=None):
    keyword_text = " ".join(str(keyword or "") for keyword in (keywords or []))
    source_text = normalize_brand_display_text("{} {}".format(title or "", keyword_text))
    haystack = normalize_context_match_text(source_text.replace("_", " ").replace("-", " "))
    labels = []
    rules = list(CODEX_NATIVE_TASK_GROUP_LABEL_RULES_ZH) + list(
        _PERSONAL_CODEX_NATIVE_RULES.get("task_group_label_rules", [])
    )
    for fragments, label in rules:
        if all(normalize_context_match_text(fragment) in haystack for fragment in fragments):
            if label not in labels:
                labels.append(label)
    cjk_candidates = []
    for candidate in [title] + list(keywords or []):
        text = compact_preview_text(normalize_brand_display_text(candidate), limit=32)
        if text and contains_cjk(text) and text not in cjk_candidates:
            cjk_candidates.append(text)
    return (cjk_candidates + labels)[:5]


def generic_codex_native_task_group_title(title="", keywords=None, index=1):
    labels = codex_native_task_group_labels_zh(title, keywords)
    if labels:
        return " / ".join(labels[:3]) + "历史任务"
    source_title = compact_preview_text(normalize_brand_display_text(title), limit=72)
    if source_title:
        return source_title
    for keyword in keywords or []:
        source_keyword = compact_preview_text(normalize_brand_display_text(keyword), limit=72)
        if source_keyword:
            return source_keyword
    return "历史任务索引"


def generic_codex_native_task_group_body(task_count=0, source_count=0, labels=None):
    parts = ["来自 MEMORY.md 的历史任务索引"]
    cleaned_labels = [
        normalize_brand_display_text(str(label or "").strip())
        for label in (labels or [])
        if normalize_brand_display_text(str(label or "").strip())
    ]
    if cleaned_labels:
        parts.append("主题：{}".format("、".join(cleaned_labels[:4])))
    if task_count:
        parts.append("包含 {} 个任务".format(task_count))
    if source_count:
        parts.append("{} 个来源".format(source_count))
    return "；".join(parts) + "。"


def find_codex_native_topic_rule(title, keyword_blob="", desc="", learnings="", detail_heading=""):
    haystack = normalize_brand_display_text(
        " ".join(
            str(part or "")
            for part in (title, keyword_blob, desc, learnings, detail_heading)
            if part
        )
    ).lower()
    compact_haystack = normalize_context_match_text(haystack)
    for rule in list(CODEX_NATIVE_TOPIC_RULES_ZH) + list(_PERSONAL_CODEX_NATIVE_RULES["topic_rules"]):
        fragments = [str(fragment).lower() for fragment in rule.get("fragments", [])]
        if all(fragment in haystack or fragment in compact_haystack for fragment in fragments):
            return rule
    return None


def display_codex_native_context_reference(raw_context):
    context = normalize_brand_display_text(str(raw_context or "").strip().strip("`"))
    if not context:
        return ""
    if context.lower() == "that repo":
        return "这个仓库"
    if "/" in context:
        name = context.rstrip("/").split("/")[-1] or context
        return "{}项目".format(name)
    return context


def split_codex_native_context_prefix(body):
    text = normalize_brand_display_text(str(body or "").strip())
    match = re.match(r"^In\s+(.+?),\s+(.+)$", text, flags=re.IGNORECASE)
    if not match:
        return "", text
    return display_codex_native_context_reference(match.group(1)), match.group(2).strip()


def find_codex_native_bullet_rule(body):
    body_text = normalize_brand_display_text(str(body or ""))
    _, rest = split_codex_native_context_prefix(body_text)
    haystack = "{} {}".format(body_text, rest).lower()
    for rule in list(CODEX_NATIVE_BULLET_RULES_ZH) + list(_PERSONAL_CODEX_NATIVE_RULES["bullet_rules"]):
        if all(str(fragment).lower() in haystack for fragment in rule.get("fragments", [])):
            return rule
    return None


def build_codex_native_bullet_title_en(body, kind, index):
    rule = find_codex_native_bullet_rule(body)
    if rule:
        title = rule["title"]
        return (
            CODEX_NATIVE_BULLET_TITLE_EN_BY_ZH.get(title)
            or _PERSONAL_CODEX_NATIVE_RULES["bullet_title_en"].get(title)
            or title
        )
    return "{} {}".format("Preference" if kind == "preference" else "General tip", index)


def build_codex_native_bullet_title(body, kind, index, language=None):
    if is_english(language):
        return build_codex_native_bullet_title_en(body, kind, index)
    rule = find_codex_native_bullet_rule(body)
    if rule:
        return rule["title"]
    _, rest = split_codex_native_context_prefix(body)
    source = normalize_brand_display_text(rest or body)
    if source:
        return compact_preview_text(source, limit=72)
    default_prefix = "偏好" if kind == "preference" else "通用 tips"
    return "{} {}".format(default_prefix, index)


def build_codex_native_display_body(title, body, language=None, kind=None):
    body = normalize_brand_display_text(body)
    if is_english(language):
        return body

    key = codex_native_translation_key(title)
    task_body_translation = _codex_native_task_body(key)
    if task_body_translation:
        return task_body_translation

    bullet_key = codex_native_translation_key(body)
    direct_bullet = _codex_native_bullet(bullet_key)
    if direct_bullet:
        return direct_bullet
    bullet_tokens = set(bullet_key.split())
    if bullet_tokens:
        for candidate_key, translated in _codex_native_bullet_items():
            candidate_tokens = set(candidate_key.split())
            if not candidate_tokens:
                continue
            body_coverage = len(bullet_tokens & candidate_tokens) / len(bullet_tokens)
            candidate_coverage = len(bullet_tokens & candidate_tokens) / len(candidate_tokens)
            if body_coverage >= 0.86 and candidate_coverage >= 0.72:
                return translated

    rule = find_codex_native_bullet_rule(body)
    if rule:
        context, _ = split_codex_native_context_prefix(body)
        if context:
            return "在{}里，{}".format(context, rule["body"])
        return rule["body"]

    if kind in {"preference", "tip"} and not contains_cjk(body):
        return normalize_brand_display_text(body)

    return normalize_brand_display_text(body)


def build_codex_native_display_title(title, language=None, keyword_blob="", desc="", learnings="", detail_heading=""):
    title = normalize_brand_display_text(title)
    if is_english(language):
        return title
    key = codex_native_translation_key(title)
    title_translation = _codex_native_title(key)
    if title_translation:
        return normalize_brand_display_text(title_translation)
    rule = find_codex_native_topic_rule(
        title,
        keyword_blob=keyword_blob,
        desc=desc,
        learnings=learnings,
        detail_heading=detail_heading,
    )
    if rule:
        return normalize_brand_display_text(rule["title"])
    if is_untranslated_english_text(title):
        return CODEX_NATIVE_GENERIC_TOPIC_TITLE_ZH
    return title


def build_codex_native_display_note(
    title,
    keyword_blob="",
    desc="",
    learnings="",
    detail_heading="",
    language=None,
):
    title_text = normalize_brand_display_text(title)
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
    note_translation = _codex_native_note(key)
    if note_translation:
        note = note_translation
        if keyword_blob:
            note = "{} 关键词：{}。".format(note.rstrip("。"), keyword_blob)
        if detail_heading:
            note = "{} 分组：{}。".format(note.rstrip("。"), detail_heading)
        return normalize_brand_display_text(note)

    rule = find_codex_native_topic_rule(
        title,
        keyword_blob=keyword_blob,
        desc=desc,
        learnings=learnings,
        detail_heading=detail_heading,
    )
    if rule:
        note = rule["body"]
        if keyword_blob:
            note = "{} 关键词：{}。".format(note.rstrip("。"), keyword_blob)
        if detail_heading:
            note = "{} 分组：{}。".format(note.rstrip("。"), detail_heading)
        return normalize_brand_display_text(note)

    note_parts = []
    hidden_english_source = False
    if desc and not is_untranslated_english_text(desc):
        note_parts.append("摘要：{}".format(compact_preview_text(desc, limit=140)))
    elif desc:
        hidden_english_source = True
    if learnings and not is_untranslated_english_text(learnings):
        note_parts.append("经验：{}".format(compact_preview_text(learnings, limit=140)))
    elif learnings:
        hidden_english_source = True
    if keyword_blob and not is_untranslated_english_text(keyword_blob):
        note_parts.append("关键词：{}".format(keyword_blob))
    elif keyword_blob:
        hidden_english_source = True
    if detail_heading and not is_untranslated_english_text(detail_heading):
        note_parts.append("分组：{}".format(detail_heading))
    elif detail_heading:
        hidden_english_source = True
    if note_parts:
        return normalize_brand_display_text("；".join(part for part in note_parts if part))
    if hidden_english_source:
        return CODEX_NATIVE_GENERIC_TOPIC_NOTE_ZH
    if title_text and contains_cjk(title_text):
        return "主题：{}。".format(compact_preview_text(title_text, limit=140).rstrip("。"))
    return "来自 Codex 原生记忆的记忆条目。"


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
            "hidden_personal_memory_items": 0,
        },
    }


OPENRELIX_PERSONAL_MEMORY_SECTION_HEADINGS = {
    "Local personal memory registry",
}


def parse_codex_native_memory_summary(
    memory_summary_path,
    memory_index_path=None,
    known_project_names=None,
    language=None,
    summary_text=None,
):
    language = current_language(language)
    summary_path = Path(memory_summary_path)
    if summary_text is None:
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
    else:
        text = str(summary_text)

    counts = {
        "topic_items": 0,
        "user_preferences": 0,
        "general_tips": 0,
        "source_exists": True,
        "source_readable": True,
        "source_error": "",
        "hidden_personal_memory_items": 0,
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
        display_title = build_codex_native_bullet_title(body, kind, index, language=language)
        english_title = "{} {}".format(
            "Preference" if kind == "preference" else "General tip",
            index,
        )
        display_title_en = build_codex_native_bullet_title_en(body, kind, index)
        display_body = build_codex_native_display_body("", body, language=language, kind=kind)
        display_body_en = compact_preview_text(normalize_brand_display_text(body), limit=220)
        cached_display = codex_native_cached_display(
            kind,
            display_body_en,
            display_body_en,
            language=language,
        )
        if cached_display:
            display_title = cached_display.get("title_zh") or display_title
            display_body = cached_display.get("body_zh") or display_body
        return {
            "kind": kind,
            "display_kind": display_kind,
            "title": english_title,
            "display_title": display_title,
            "display_title_en": display_title_en,
            "body": compact_preview_text(normalize_brand_display_text(body), limit=220),
            "display_body": compact_preview_text(display_body, limit=220),
            "display_body_en": display_body_en,
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
        structured_line = parse_codex_native_structured_line(title_line)
        memory_type = ""
        if structured_line:
            title = structured_line["title"]
            desc = structured_line.get("note") or desc
            keyword_blob = ""
            memory_type = structured_line.get("memory_type", "")
            current_item["priority"] = structured_line.get("priority") or current_item.get("priority", "medium")
        if not memory_type:
            memory_type = classify_codex_native_memory_type(title, desc, learnings)

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
        display_title = build_codex_native_display_title(
            title,
            language=language,
            keyword_blob=keyword_blob,
            desc=desc,
            learnings=learnings,
            detail_heading=current_item.get("detail_heading", ""),
        )
        display_value_note = build_codex_native_display_note(
            title,
            keyword_blob=keyword_blob,
            desc=desc,
            learnings=learnings,
            detail_heading=current_item.get("detail_heading", ""),
            language=language,
        )
        cached_display = codex_native_cached_display(
            "topic",
            compact_preview_text(normalize_brand_display_text(title), limit=140),
            value_note_en or value_note or title,
            language=language,
        )
        if cached_display:
            display_title = cached_display.get("title_zh") or display_title
            display_value_note = cached_display.get("body_zh") or display_value_note

        rows.append(
            {
                "memory_key": "native::{}::{}".format(
                    memory_type,
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
                "memory_type": memory_type,
                "display_memory_type": display_memory_type(
                    memory_type,
                    language=language,
                ),
                "priority": current_item.get("priority", "medium"),
                "display_priority": display_memory_priority(
                    current_item.get("priority", "medium"),
                    language=language,
                ),
                "title": compact_preview_text(normalize_brand_display_text(title), limit=140),
                "display_title": compact_preview_text(display_title, limit=140),
                "display_title_en": compact_preview_text(normalize_brand_display_text(title), limit=140),
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

        if current_h3 in OPENRELIX_PERSONAL_MEMORY_SECTION_HEADINGS:
            if line.startswith("- "):
                counts["hidden_personal_memory_items"] += 1
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


def relabel_native_memory_item_for_claude(item, claude_memory_path, language=None, source_label="CLAUDE.md"):
    language = current_language(language)
    current = dict(item or {})
    source_files = current.get("source_files") or []
    status = source_files[0].get("status") if source_files and isinstance(source_files[0], dict) else None
    source_file = {
        "path": str(claude_memory_path),
        "label": source_label,
    }
    if status:
        source_file["status"] = status
    current["bucket"] = "native"
    current["display_bucket"] = localized("Claude 原生", "Claude Native", language)
    current["display_context"] = localized("Claude Code 原生记忆", "Claude Code Native Memory", language)
    current["context_labels"] = [localized("Claude Code 原生记忆", "Claude Code Native Memory", language)]
    current["source_fact_label"] = localized("来源文件", "Source file", language)
    current["source_files"] = [source_file]
    current["source_windows"] = current.get("source_windows") or []
    current["memory_key"] = str(current.get("memory_key") or "native").replace("native::", "claude-native::", 1)
    for key in ("meta", "submeta_zh", "submeta_en"):
        if current.get(key):
            current[key] = (
                str(current[key])
                .replace("Codex 原生", "Claude 原生")
                .replace("Codex Native", "Claude Native")
                .replace("Codex native", "Claude native")
            )
    return current


def empty_claude_native_memory_summary(source_exists=False, source_readable=False, source_error=""):
    return {
        "rows": [],
        "topic_rows": [],
        "preference_rows": [],
        "tip_rows": [],
        "counts": {
            "topic_items": 0,
            "user_preferences": 0,
            "general_tips": 0,
            "total_items": 0,
            "claude_md_items": 0,
            "auto_memory_items": 0,
            "auto_memory_file_count": 0,
            "auto_memory_project_count": 0,
            "source_exists": source_exists,
            "source_readable": source_readable,
            "source_error": source_error,
        },
    }


CLAUDE_MANAGED_MEMORY_START = "<!-- openrelix:shared-memory:start -->"
CLAUDE_MANAGED_MEMORY_END = "<!-- openrelix:shared-memory:end -->"
CLAUDE_AUTO_MEMORY_LINE_LIMIT = 200
CLAUDE_AUTO_MEMORY_BYTE_LIMIT = 25 * 1024


def strip_claude_managed_memory_text(text):
    if CLAUDE_MANAGED_MEMORY_START not in text or CLAUDE_MANAGED_MEMORY_END not in text:
        return text, False
    before, _, tail = text.partition(CLAUDE_MANAGED_MEMORY_START)
    _, _, after = tail.partition(CLAUDE_MANAGED_MEMORY_END)
    visible_text = "\n\n".join(part.strip() for part in (before, after) if part.strip())
    return (visible_text + "\n" if visible_text else ""), True


def claude_auto_memory_files(claude_home):
    projects_dir = Path(claude_home) / "projects"
    if not projects_dir.is_dir():
        return []
    memory_files = []
    try:
        project_dirs = sorted(path for path in projects_dir.iterdir() if path.is_dir())
    except OSError:
        return []
    for project_dir in project_dirs:
        memory_dir = project_dir / "memory"
        if not memory_dir.is_dir():
            continue
        try:
            for path in sorted(memory_dir.glob("*.md")):
                if path.is_file():
                    memory_files.append(path)
        except OSError:
            continue
    return memory_files


def claude_auto_memory_project_key(memory_path):
    try:
        return Path(memory_path).parent.parent.name
    except IndexError:
        return ""


def claude_auto_memory_project_label(memory_path):
    project_key = claude_auto_memory_project_key(memory_path)
    if not project_key:
        return "Claude Code auto memory"
    if project_key.startswith("-Users-"):
        parts = [part for part in project_key.split("-") if part]
        if len(parts) >= 2:
            return "~/" + "/".join(parts[2:])
    return project_key


def claude_auto_memory_source_label(memory_path):
    path = Path(memory_path)
    project_label = claude_auto_memory_project_label(path)
    if project_label:
        return "auto memory / {} / {}".format(project_label, path.name)
    return "auto memory / {}".format(path.name)


def read_claude_auto_memory_visible_text(memory_path):
    path = Path(memory_path)
    raw = path.read_bytes()
    truncated_bytes = len(raw) > CLAUDE_AUTO_MEMORY_BYTE_LIMIT
    raw = raw[:CLAUDE_AUTO_MEMORY_BYTE_LIMIT]
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    truncated_lines = len(lines) > CLAUDE_AUTO_MEMORY_LINE_LIMIT
    if truncated_lines:
        lines = lines[:CLAUDE_AUTO_MEMORY_LINE_LIMIT]
    return "\n".join(lines), truncated_bytes or truncated_lines


def classify_claude_auto_memory_kind(body, heading="", filename=""):
    text = " ".join(
        normalize_brand_display_text(part).lower()
        for part in (body, heading, filename)
        if normalize_brand_display_text(part)
    )
    if any(
        marker in text
        for marker in (
            "user memory",
            "preference",
            "prefer ",
            "prefers",
            "user prefers",
            " likes ",
            "不要",
            "偏好",
            "喜欢",
            "优先",
        )
    ):
        return "preference"
    if any(
        marker in text
        for marker in (
            "general tip",
            "lesson",
            "feedback",
            "correction",
            "debug",
            "workaround",
            "avoid",
            "remember",
            "注意",
            "排障",
            "经验",
        )
    ):
        return "tip"
    return "topic"


def make_claude_auto_memory_row(
    body,
    memory_path,
    index,
    kind="topic",
    heading="",
    known_project_names=None,
    language=None,
):
    language = current_language(language)
    path = Path(memory_path)
    body_text = compact_preview_text(normalize_brand_display_text(body), limit=260)
    heading_text = normalize_brand_display_text(heading)
    title_seed = heading_text or path.stem.replace("_", " ").replace("-", " ")
    if kind == "preference":
        memory_type = "preference"
        display_type = localized("偏好", "Preference", language)
        display_title = build_codex_native_bullet_title(body_text, "preference", index, language=language)
        display_title_en = build_codex_native_bullet_title_en(body_text, "preference", index)
    elif kind == "tip":
        memory_type = "procedural"
        display_type = localized("通用 tips", "General Tips", language)
        display_title = build_codex_native_bullet_title(body_text, "tip", index, language=language)
        display_title_en = build_codex_native_bullet_title_en(body_text, "tip", index)
    else:
        memory_type = classify_codex_native_memory_type(title_seed, body_text, "")
        display_type = display_memory_type(memory_type, language=language)
        display_title = build_codex_native_display_title(
            title_seed or body_text,
            language=language,
            desc=body_text,
        )
        display_title_en = compact_preview_text(normalize_brand_display_text(title_seed or body_text), limit=140)

    display_body = build_codex_native_display_body(
        display_title_en,
        body_text,
        language=language,
        kind="preference" if kind == "preference" else "tip" if kind == "tip" else "",
    )
    if kind in {"preference", "tip"}:
        cached_display = codex_native_cached_display(
            kind,
            body_text,
            body_text,
            language=language,
        )
    else:
        cached_display = codex_native_cached_display(
            "topic",
            display_title_en,
            body_text,
            language=language,
        )
    if cached_display:
        display_title = cached_display.get("title_zh") or display_title
        display_body = cached_display.get("body_zh") or display_body
    project_label = claude_auto_memory_project_label(path)
    context_labels = collect_context_labels_from_texts(
        [project_label, heading_text, body_text],
        known_project_names,
    )
    return {
        "memory_key": "claude-native::auto::{}::{}".format(
            kind,
            normalize_memory_signature_text("{} {}".format(str(path), body_text)) or "untitled",
        ),
        "bucket": "native",
        "display_bucket": localized("Claude 原生", "Claude Native", language),
        "memory_type": memory_type,
        "display_memory_type": display_type,
        "priority": "medium",
        "display_priority": display_memory_priority("medium", language=language),
        "title": compact_preview_text(normalize_brand_display_text(title_seed or body_text), limit=140),
        "display_title": compact_preview_text(display_title, limit=140),
        "display_title_en": compact_preview_text(display_title_en, limit=140),
        "value_note": body_text,
        "value_note_en": body_text,
        "display_value_note": compact_preview_text(display_body, limit=260),
        "display_value_note_en": body_text,
        "created_at": "",
        "updated_at": "",
        "created_at_display": display_memory_date(""),
        "updated_at_display": display_memory_date(""),
        "occurrence_count": 1,
        "occurrence_label": localized("原生归档", "Native archive", language),
        "display_context": context_labels[0] if context_labels else localized(
            project_label or "Claude Code auto memory",
            project_label or "Claude Code auto memory",
            language,
        ),
        "context_labels": context_labels[:3],
        "source_windows": [],
        "source_window_count": 0,
        "source_fact_label": localized("来源文件", "Source file", language),
        "source_files": [
            {
                "path": str(path),
                "label": claude_auto_memory_source_label(path),
            }
        ],
    }


def parse_claude_auto_memory_file(memory_path, known_project_names=None, language=None):
    path = Path(memory_path)
    text, truncated = read_claude_auto_memory_visible_text(path)
    rows = {"topic": [], "preference": [], "tip": []}
    current_heading = ""
    item_index = 0
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            current_heading = stripped.lstrip("#").strip()
            continue
        if not stripped.startswith(("- ", "* ")):
            continue
        body = stripped[2:].strip()
        if not body:
            continue
        item_index += 1
        kind = classify_claude_auto_memory_kind(body, current_heading, path.name)
        rows[kind].append(
            make_claude_auto_memory_row(
                body,
                path,
                item_index,
                kind=kind,
                heading=current_heading,
                known_project_names=known_project_names,
                language=language,
            )
        )
    return rows, truncated


def parse_claude_native_memory_summary(claude_memory_path, known_project_names=None, language=None, claude_home=None):
    language = current_language(language)
    path = Path(claude_memory_path)
    claude_home_path = Path(claude_home) if claude_home is not None else path.parent
    topic_rows = []
    preference_rows = []
    tip_rows = []
    source_exists = False
    source_readable = True
    source_error = ""
    has_managed_block = False
    claude_md_items = 0
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError) as exc:
        text = ""
        source_exists = True
        source_readable = False
        source_error = exc.__class__.__name__
    else:
        source_exists = True

    if text:
        visible_text, has_managed_block = strip_claude_managed_memory_text(text)
        if visible_text.strip():
            parsed = parse_codex_native_memory_summary(
                path,
                memory_index_path=None,
                known_project_names=known_project_names,
                language=language,
                summary_text=visible_text,
            )
            claude_topic_rows = [
                relabel_native_memory_item_for_claude(row, path, language=language)
                for row in parsed.get("rows", [])
            ]
            claude_preference_rows = [
                relabel_native_memory_item_for_claude(row, path, language=language)
                for row in make_codex_native_brief_memory_items(
                    parsed.get("preference_rows", []),
                    "preference",
                    language=language,
                )
            ]
            claude_tip_rows = [
                relabel_native_memory_item_for_claude(row, path, language=language)
                for row in make_codex_native_brief_memory_items(
                    parsed.get("tip_rows", []),
                    "tip",
                    language=language,
                )
            ]
            topic_rows.extend(claude_topic_rows)
            preference_rows.extend(claude_preference_rows)
            tip_rows.extend(claude_tip_rows)
            claude_md_items = len(claude_topic_rows) + len(claude_preference_rows) + len(claude_tip_rows)

    auto_files = claude_auto_memory_files(claude_home_path)
    auto_project_keys = set()
    auto_truncated_count = 0
    for auto_file in auto_files:
        auto_project_keys.add(claude_auto_memory_project_key(auto_file))
        try:
            parsed_auto, truncated = parse_claude_auto_memory_file(
                auto_file,
                known_project_names=known_project_names,
                language=language,
            )
        except (OSError, UnicodeDecodeError) as exc:
            source_readable = False
            source_error = source_error or exc.__class__.__name__
            continue
        source_exists = True
        if truncated:
            auto_truncated_count += 1
        topic_rows.extend(parsed_auto.get("topic", []))
        preference_rows.extend(parsed_auto.get("preference", []))
        tip_rows.extend(parsed_auto.get("tip", []))

    rows = topic_rows + preference_rows + tip_rows
    counts = {
        "topic_items": len(topic_rows),
        "user_preferences": len(preference_rows),
        "general_tips": len(tip_rows),
        "claude_md_items": claude_md_items,
        "auto_memory_items": len(rows) - claude_md_items,
        "auto_memory_file_count": len(auto_files),
        "auto_memory_project_count": len([key for key in auto_project_keys if key]),
        "auto_memory_truncated_file_count": auto_truncated_count,
        "source_exists": source_exists,
        "source_readable": source_readable,
        "source_error": source_error,
        "managed_block_present": has_managed_block,
    }
    counts["total_items"] = len(rows)
    return {
        "rows": rows,
        "topic_rows": topic_rows,
        "preference_rows": preference_rows,
        "tip_rows": tip_rows,
        "counts": counts,
    }


def build_claude_native_memory_comparison(native_rows, native_counts, summary_path_label, language=None):
    language = current_language(language)
    source_exists = native_counts.get("source_exists", bool(native_rows))
    source_readable = native_counts.get("source_readable", source_exists)
    source_error = native_counts.get("source_error", "")
    managed_block_present = bool(native_counts.get("managed_block_present"))
    auto_memory_file_count = safe_int(native_counts.get("auto_memory_file_count", 0))
    auto_memory_project_count = safe_int(native_counts.get("auto_memory_project_count", 0))
    claude_md_items = safe_int(native_counts.get("claude_md_items", 0))
    auto_memory_items = safe_int(native_counts.get("auto_memory_items", 0))
    if source_error and not source_readable:
        note = localized(
            "无法读取 {}（{}），Claude 原生记忆暂不可展示。".format(summary_path_label, source_error),
            "Unable to read {} ({}); Claude native memory is not displayable yet.".format(summary_path_label, source_error),
            language,
        )
    elif not source_exists:
        note = localized(
            "未检测到 {}；Claude 原生记忆暂不可展示。".format(summary_path_label),
            "{} was not found; Claude native memory is not displayable yet.".format(summary_path_label),
            language,
        )
    elif not source_readable:
        note = localized(
            "已检测到但无法读取 {}。".format(summary_path_label),
            "{} exists but is unreadable.".format(summary_path_label),
            language,
        )
    elif not native_rows:
        note = localized(
            "已读取 {}，但暂未发现可展示的 Claude Code 原生记忆条目。".format(summary_path_label),
            "Read {}, but no displayable Claude Code native memory entries were found yet.".format(summary_path_label),
            language,
        )
    else:
        note_parts = [
            localized(
                "已读取 {}".format(summary_path_label),
                "Read {}".format(summary_path_label),
                language,
            ),
            localized(
                "下方展示 {} 条 Claude Code 原生记忆".format(len(native_rows)),
                "showing {} Claude Code native memory entries below".format(len(native_rows)),
                language,
            ),
        ]
        if auto_memory_items:
            note_parts.append(
                localized(
                    "其中 auto memory {} 条，来自 {} 个项目 / 路径".format(
                        auto_memory_items,
                        auto_memory_project_count,
                    ),
                    "{} auto memory entries from {} projects / paths".format(
                        auto_memory_items,
                        auto_memory_project_count,
                    ),
                    language,
                )
            )
        if claude_md_items:
            note_parts.append(
                localized(
                    "CLAUDE.md 手写条目 {} 条".format(claude_md_items),
                    "{} hand-written CLAUDE.md entries".format(claude_md_items),
                    language,
                )
            )
        if auto_memory_file_count:
            note_parts.append(
                localized(
                    "扫描 auto memory 文件 {} 个".format(auto_memory_file_count),
                    "scanned {} auto memory files".format(auto_memory_file_count),
                    language,
                )
            )
        note = ("; ".join(note_parts) + ".") if is_english(language) else ("；".join(note_parts) + "。")
    return {
        "note": note,
        "native_context_count": 1 if native_rows else 0,
        "shared_context_count": 0,
        "managed_block_hidden": managed_block_present,
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
        zh_meta_keywords = [
            keyword for keyword in keywords[:3] if not is_untranslated_english_text(keyword)
        ]
        if zh_meta_keywords:
            meta_parts.append("关键词 {}".format("、".join(zh_meta_keywords)))
        body = normalize_brand_display_text(current_group.get("scope", "") or current_group.get("applies_to", ""))
        body_en = body
        if not body:
            body = "MEMORY.md 中登记的历史任务索引。"
            body_en = "Historical task index entry registered in MEMORY.md."
        display_title = build_codex_native_display_title(
            current_group.get("title", ""),
            language=language,
            keyword_blob=", ".join(keywords),
            desc=body,
        )
        display_title_en = normalize_brand_display_text(current_group.get("title", ""))
        if (
            not is_english(language)
            and (
                display_title == CODEX_NATIVE_GENERIC_TOPIC_TITLE_ZH
                or is_untranslated_english_text(display_title)
            )
        ):
            display_title = generic_codex_native_task_group_title(
                current_group.get("title", ""),
                keywords,
                len(task_groups) + 1,
            )
        display_body = build_codex_native_display_body(
            current_group.get("title", ""),
            body,
            language=language,
        )
        body_needs_generic_zh = not is_english(language) and is_untranslated_english_text(body)
        title_key = codex_native_translation_key(current_group.get("title", ""))
        has_task_body_translation = _codex_native_task_body_has_key(title_key)
        topic_body = build_codex_native_display_note(
            current_group.get("title", ""),
            keyword_blob=", ".join(keywords[:3]),
            desc=body,
            language=language,
        )
        if (
            not has_task_body_translation
            and not is_english(language)
            and not body_needs_generic_zh
            and topic_body
            and not topic_body.startswith("摘要：")
        ):
            display_body = topic_body
        if (
            not has_task_body_translation
            and (
                body_needs_generic_zh
                or (not is_english(language) and is_untranslated_english_text(display_body))
            )
        ):
            labels = codex_native_task_group_labels_zh(current_group.get("title", ""), keywords)
            display_body = generic_codex_native_task_group_body(
                current_group.get("task_count", 0),
                current_group.get("rollout_reference_count", 0),
                labels,
            )
        cached_display = codex_native_cached_display(
            "task_group",
            compact_preview_text(
                normalize_brand_display_text(current_group.get("title", "")),
                limit=120,
            ),
            compact_preview_text(
                normalize_brand_display_text(body_en or body or current_group.get("title", "")),
                limit=220,
            ),
            language=language,
        )
        if cached_display:
            display_title = cached_display.get("title_zh") or display_title
            display_body = cached_display.get("body_zh") or display_body
        task_groups.append(
            {
                "title": compact_preview_text(normalize_brand_display_text(current_group.get("title", "")), limit=120),
                "display_title": compact_preview_text(display_title, limit=120),
                "display_title_en": compact_preview_text(display_title_en, limit=120),
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
            if line.startswith("## "):
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
    hidden_personal_memory_items = native_counts.get("hidden_personal_memory_items", 0)
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
            "{}，历史任务索引统计暂不可用".format(index_unreadable_note),
            "{}; historical task index stats are unavailable".format(index_unreadable_note),
            language,
        )
    elif index_missing:
        index_unreadable_note = localized(
            "{} 未检测到，历史任务索引统计暂不可用".format(index_path_label),
            "{} was not found; historical task index stats are unavailable".format(index_path_label),
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
            localized("暂无记忆条目", "No memory items", language),
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
        if hidden_personal_memory_items:
            note_parts.append(
                localized(
                    "OpenRelix 本地个人记忆登记册已在原生视图中隐藏",
                    "OpenRelix local personal-memory registry is hidden from this native view",
                    language,
                )
            )
        note = ("; ".join(note_parts) + ".") if is_english(language) else ("；".join(note_parts) + "。")
    else:
        note_parts = [
            localized(
                "下方展示记忆条目 {} 条".format(len(native_rows)),
                "Showing {} memory items below".format(len(native_rows)),
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
                    "历史任务索引 {} 条".format(index_stats["task_group_count"]),
                    "{} historical task index entries".format(index_stats["task_group_count"]),
                    language,
                )
            )
        elif index_unreadable_note:
            note_parts.append(index_unreadable_note)
        if hidden_personal_memory_items:
            note_parts.append(
                localized(
                    "OpenRelix 本地个人记忆登记册已隐藏",
                    "OpenRelix local personal-memory registry is hidden",
                    language,
                )
            )
        note_parts.append(
            localized(
                "偏好、tips、历史任务索引以简短列表展示",
                "preferences, tips, and historical task index entries are shown as compact lists",
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
                "usage_frequency_recent_occurrence_count",
                "usage_frequency_terms",
                "usage_frequency_score_kind",
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
    ai_host = str(raw_window.get("ai_host") or "").strip().lower()
    if ai_host == "claude" or source.startswith("claude_code") or collection_source == "claude-history":
        return "claude-history"
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
            "采集：Codex app-server · 线程来源：{}".format(thread_source),
            "Collection: Codex app-server · thread source: {}".format(thread_source),
            language,
        )
    labels = {
        "app-server": (
            "采集：Codex app-server",
            "Collection: Codex app-server",
        ),
        "history_fallback": (
            "采集：Codex app-server 不可用，已回退 CLI history/session",
            "Collection: Codex app-server unavailable; fell back to CLI history/session",
        ),
        "history": (
            "采集：Codex CLI history/session",
            "Collection: Codex CLI history/session",
        ),
        "claude-history": (
            "采集：Claude Code transcript",
            "Collection: Claude Code transcript",
        ),
        "mixed": (
            "采集：Codex + Claude Code",
            "Collection: Codex + Claude Code",
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


def window_display_summary(raw_window, question_summary="", main_takeaway="", language=None):
    raw_window = raw_window or {}
    app_server = raw_window.get("app_server") or {}
    candidates = (
        raw_window.get("window_summary", ""),
        raw_window.get("thread_title", ""),
        raw_window.get("title", ""),
        app_server.get("preview", ""),
        app_server.get("title", ""),
        question_summary,
        main_takeaway,
    )
    for candidate in candidates:
        summary = compact_preview_text(candidate, limit=140)
        if summary:
            return normalize_brand_display_text(summary)
    return localized("未捕获窗口摘要", "No captured window summary", language)


def window_resume_id(raw_window, window_id=""):
    raw_window = raw_window or {}
    app_server = raw_window.get("app_server") or {}
    return str(
        raw_window.get("resume_id")
        or raw_window.get("session_id")
        or raw_window.get("thread_id")
        or app_server.get("thread_id")
        or window_id
        or raw_window.get("window_id")
        or ""
    ).strip()


def resolved_path_text(path):
    try:
        return str(Path(path).expanduser().resolve(strict=False))
    except OSError:
        return str(Path(path).expanduser())


def is_primary_codex_home(codex_home):
    if not codex_home:
        return True
    return resolved_path_text(codex_home) == resolved_path_text(PATHS.codex_home)


def is_system_codex_profile(codex_home="", codex_electron_user_data_path=""):
    return overview_codex_desktop.is_system_codex_profile(
        codex_home,
        codex_electron_user_data_path,
    )


def codex_resume_command(resume_id, codex_home=""):
    resume_id = str(resume_id or "").strip()
    if not resume_id:
        return ""
    command = "codex resume {}".format(shlex.quote(resume_id))
    if codex_home and not is_primary_codex_home(codex_home):
        return "CODEX_HOME={} {}".format(shlex.quote(str(codex_home)), command)
    return command


def claude_resume_command(resume_id):
    resume_id = str(resume_id or "").strip()
    if not resume_id:
        return ""
    return "claude --resume {}".format(shlex.quote(resume_id))


def window_resume_command(ai_host, resume_id, codex_home=""):
    if ai_host == "claude":
        return claude_resume_command(resume_id)
    return codex_resume_command(resume_id, codex_home=codex_home)


def window_host_label(ai_host, language=None):
    if str(ai_host or "").strip().lower() == "claude":
        return "Claude Code"
    return "Codex"


def is_codex_thread_uuid(value):
    return bool(
        re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            str(value or "").strip(),
        )
    )


def codex_resume_url(resume_id):
    resume_id = str(resume_id or "").strip()
    if not is_codex_thread_uuid(resume_id):
        return ""
    return "codex://threads/{}".format(quote(resume_id, safe=""))


def claude_desktop_resume_action(ai_host, resume_id):
    if str(ai_host or "").strip().lower() != "claude":
        return ""
    if not overview_claude_desktop.is_valid_claude_session_id(resume_id):
        return ""
    if not overview_claude_desktop.claude_desktop_resume_supported(PATHS):
        return ""
    return "claude_desktop"


def normalize_window_summary_pairs(raw_pairs):
    if not isinstance(raw_pairs, list):
        return []
    pairs = []
    for raw_pair in raw_pairs:
        if not isinstance(raw_pair, dict):
            continue
        question = normalize_brand_display_text(
            raw_pair.get("question", "") or raw_pair.get("problem", "")
        ).strip()
        conclusion = normalize_brand_display_text(
            raw_pair.get("conclusion", "") or raw_pair.get("takeaway", "")
        ).strip()
        if question or conclusion:
            pairs.append({"question": question, "conclusion": conclusion})
    return pairs


def window_record_sort_key(item):
    if not isinstance(item, dict):
        return ""
    return str(
        item.get("local_time")
        or item.get("completed_at")
        or item.get("timestamp")
        or item.get("ts")
        or ""
    )


def raw_window_summary_pairs(prompts, conclusions, limit=4):
    if not isinstance(prompts, list):
        prompts = []
    if not isinstance(conclusions, list):
        conclusions = []
    prompt_items = sorted(
        [item for item in prompts if isinstance(item, dict)],
        key=window_record_sort_key,
    )
    conclusion_items = sorted(
        [item for item in conclusions if isinstance(item, dict)],
        key=window_record_sort_key,
    )
    prompt_by_turn = {
        str(item.get("turn_id", "")): item
        for item in prompt_items
        if str(item.get("turn_id", "")).strip()
    }
    conclusion_by_turn = {
        str(item.get("turn_id", "")): item
        for item in conclusion_items
        if str(item.get("turn_id", "")).strip()
    }
    matched_turn_ids = [
        str(item.get("turn_id", ""))
        for item in prompt_items
        if str(item.get("turn_id", "")).strip() in conclusion_by_turn
    ]
    if matched_turn_ids:
        pairs = []
        seen_turn_ids = set()
        for turn_id in matched_turn_ids:
            if turn_id in seen_turn_ids:
                continue
            seen_turn_ids.add(turn_id)
            question = normalize_brand_display_text(
                prompt_by_turn.get(turn_id, {}).get("text", "")
            ).strip()
            answer = normalize_brand_display_text(
                conclusion_by_turn.get(turn_id, {}).get("text", "")
            ).strip()
            if question or answer:
                pairs.append({"question": question, "conclusion": answer})
            if len(pairs) >= limit:
                return pairs
        if pairs:
            return pairs
    row_count = min(max(len(prompt_items), len(conclusion_items)), limit)
    pairs = []
    for index in range(row_count):
        prompt = prompt_items[index] if index < len(prompt_items) else {}
        conclusion = (
            conclusion_items[index]
            if index < len(conclusion_items)
            else {}
        )
        question = normalize_brand_display_text(prompt.get("text", "")).strip()
        answer = normalize_brand_display_text(conclusion.get("text", "")).strip()
        if question or answer:
            pairs.append({"question": question, "conclusion": answer})
    return pairs


MODEL_COMPLETED_STATUSES = {"completed", "ok", "success", "succeeded"}
MODEL_FAILED_STATUSES = {"failed", "error", "fallback"}


def window_summary_status_kind(latest_nightly):
    if not latest_nightly:
        return "raw_fallback"
    generation = str(latest_nightly.get("summary_generation") or "").strip().lower()
    stage = str(latest_nightly.get("stage") or "").strip().lower()
    status = str(
        latest_nightly.get("model_status")
        or latest_nightly.get("last_run_model_status")
        or ""
    ).strip().lower()
    if status in MODEL_FAILED_STATUSES:
        return "raw_fallback"
    if status in MODEL_COMPLETED_STATUSES:
        return "summarized"
    if generation == "lightweight" or status == "skipped_lightweight" or stage == "preliminary":
        return "lightweight"
    if not status:
        return "summarized"
    return "raw_fallback"


def window_summary_model_completed(latest_nightly):
    return window_summary_status_kind(latest_nightly) == "summarized"


def window_summary_status_label(summary_status, language=None):
    if summary_status == "summarized":
        return localized("大模型已做智能整理", "AI-organized", language)
    if summary_status == "lightweight":
        return localized(
            "轻度回溯快速整理，未做大模型总结",
            "Quick lightweight organization; no AI model summary yet",
            language,
        )
    return localized(
        "暂未做二次学习和总结，当前展示原始问题和结论",
        "Codex summary has not run yet; showing raw questions and conclusions",
        language,
    )


def build_window_items_from_daily_capture(daily_capture, latest_nightly=None, language=None):
    language = current_language(language)
    nightly_map = {}
    if latest_nightly:
        for item in latest_nightly.get("window_summaries", []):
            window_id = item.get("window_id", "")
            if window_id:
                nightly_map[window_id] = item

    items = []
    nightly_summary_status = window_summary_status_kind(latest_nightly)
    for raw_window in (daily_capture or {}).get("windows", []):
        window_id = raw_window.get("window_id", "")
        ai_host = str(raw_window.get("ai_host") or "codex").strip().lower()
        if ai_host not in {"codex", "claude"}:
            ai_host = "codex"
        nightly_item = nightly_map.get(window_id, {})
        latest_activity = latest_window_activity(raw_window)
        prompts = raw_window.get("prompts", [])
        conclusions = raw_window.get("conclusions", [])
        first_prompt = prompts[0] if prompts else {}
        last_conclusion = conclusions[-1] if conclusions else {}
        has_organized_summary = bool(nightly_item) and nightly_summary_status in {
            "summarized",
            "lightweight",
        }
        raw_question_summary = first_prompt.get("text", "")
        raw_main_takeaway = last_conclusion.get("text", "") or raw_question_summary
        question_summary = (
            nightly_item.get("question_summary") or raw_question_summary
            if has_organized_summary
            else raw_question_summary
        )
        main_takeaway = (
            nightly_item.get("main_takeaway") or raw_main_takeaway
            if has_organized_summary
            else raw_main_takeaway
        )
        question_summary = normalize_brand_display_text(question_summary)
        main_takeaway = normalize_brand_display_text(main_takeaway)
        nightly_pairs = normalize_window_summary_pairs(nightly_item.get("summary_pairs", []))
        raw_summary_pairs = raw_window_summary_pairs(prompts, conclusions)
        summary_pairs = nightly_pairs if has_organized_summary else raw_summary_pairs
        summary_status = nightly_summary_status if has_organized_summary else "raw_fallback"
        summary_status_label = window_summary_status_label(summary_status, language)
        raw_title = (raw_summary_pairs[0].get("question", "") if raw_summary_pairs else question_summary)
        learned_title = (
            nightly_item.get("window_title")
            or nightly_item.get("window_summary")
            or nightly_item.get("title")
            or question_summary
        )
        window_title = compact_preview_text(
            normalize_brand_display_text(learned_title if has_organized_summary else raw_title),
            limit=100,
        )
        window_summary = window_display_summary(
            raw_window,
            question_summary=question_summary,
            main_takeaway=main_takeaway,
            language=language,
        )
        resume_id = window_resume_id(raw_window, window_id=window_id)
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
        resume_app_action = claude_desktop_resume_action(ai_host, resume_id)
        codex_home = raw_window.get("codex_home", "")
        codex_electron_user_data_path = raw_window.get("codex_electron_user_data_path", "")
        items.append(
            {
                "date": (daily_capture or {}).get("date", ""),
                "ai_host": ai_host,
                "ai_host_label": window_host_label(ai_host, language=language),
                "window_id": window_id,
                "window_id_short": window_id[:8],
                "cwd": cwd,
                "cwd_display": compact_cwd_display(cwd),
                "project_label": project_label,
                "activity_source": activity_source,
                "thread_source": thread_source,
                "window_summary": window_summary,
                "resume_id": resume_id,
                "resume_command": window_resume_command(ai_host, resume_id, codex_home=codex_home),
                "resume_url": codex_resume_url(resume_id) if ai_host == "codex" else "",
                "codex_home": codex_home,
                "codex_electron_user_data_path": codex_electron_user_data_path,
                "resume_app_action": resume_app_action,
                "resume_app_session_id": resume_id if resume_app_action else "",
                "activity_source_label": window_activity_source_label(
                    activity_source,
                    language,
                    thread_source=thread_source,
                ),
                "question_count": raw_window.get("prompt_count", 0),
                "conclusion_count": raw_window.get("conclusion_count", 0),
                "question_summary": question_summary or localized("暂无问题摘要。", "No question summary.", language),
                "main_takeaway": main_takeaway or localized("暂无结论摘要。", "No conclusion summary.", language),
                "summary_pairs": summary_pairs,
                "raw_summary_pairs": raw_summary_pairs,
                "summary_status": summary_status,
                "summary_status_label": summary_status_label,
                "window_title": window_title,
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
    if not daily_capture and target_date:
        daily_capture = load_history_fallback_daily_capture(target_date)

    nightly_map = {}
    if latest_nightly:
        for item in latest_nightly.get("window_summaries", []):
            window_id = item.get("window_id", "")
            if window_id:
                nightly_map[window_id] = item

    if not daily_capture:
        fallback_items = []
        nightly_summary_status = window_summary_status_kind(latest_nightly)
        for item in (latest_nightly or {}).get("window_summaries", []):
            cwd = item.get("cwd", "")
            summary_status = nightly_summary_status
            summary_status_label = window_summary_status_label(summary_status, language)
            ai_host = str(item.get("ai_host") or "codex").strip().lower()
            if ai_host not in {"codex", "claude"}:
                ai_host = "codex"
            resume_id = item.get("resume_id", "") or item.get("window_id", "")
            codex_home = item.get("codex_home", "")
            codex_electron_user_data_path = item.get("codex_electron_user_data_path", "")
            resume_app_action = claude_desktop_resume_action(ai_host, resume_id)
            fallback_items.append(
                {
                    "ai_host": ai_host,
                    "ai_host_label": window_host_label(ai_host, language=language),
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
                    "summary_pairs": normalize_window_summary_pairs(item.get("summary_pairs", [])),
                    "raw_summary_pairs": [],
                    "summary_status": summary_status,
                    "summary_status_label": summary_status_label,
                    "window_title": compact_preview_text(
                        normalize_brand_display_text(
                            item.get("window_title")
                            or item.get("window_summary", "")
                            or item.get("thread_title", "")
                            or item.get("title", "")
                            or item.get("question_summary", "")
                            or localized("未捕获窗口摘要", "No captured window summary", language)
                        ),
                        limit=100,
                    ),
                    "window_summary": normalize_brand_display_text(
                        item.get("window_summary", "")
                        or item.get("thread_title", "")
                        or item.get("title", "")
                        or item.get("question_summary", "")
                        or localized("未捕获窗口摘要", "No captured window summary", language)
                    ),
                    "resume_id": resume_id,
                    "resume_command": window_resume_command(ai_host, resume_id, codex_home=codex_home),
                    "resume_url": codex_resume_url(resume_id) if ai_host == "codex" else "",
                    "codex_home": codex_home,
                    "codex_electron_user_data_path": codex_electron_user_data_path,
                    "resume_app_action": resume_app_action,
                    "resume_app_session_id": resume_id if resume_app_action else "",
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
        "source_kind": daily_capture.get("source_kind", "daily_capture"),
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


def make_discovered_panel_detail_item(row):
    title = row.get("name") or row.get("identifier") or ""
    title_en = panel_english_text(title) or title
    meta = row.get("description") or row.get("identifier") or ""
    meta_en = panel_english_text(meta) or english_freeform_text(meta, fallback_label="Asset")
    return {
        "title": title,
        "title_en": title_en,
        "meta": meta,
        "meta_en": meta_en,
    }


def build_discovered_type_mix_rows(render_rows):
    counts = overview_asset_discovery.high_level_type_counts(render_rows)
    rows = []
    for asset_type in DISCOVERED_TYPE_ORDER:
        value = counts.get(asset_type, 0)
        if value <= 0:
            continue
        details = [
            make_discovered_panel_detail_item(row)
            for row in sorted(
                (item for item in render_rows if item.get("type") == asset_type),
                key=lambda item: str(item.get("identifier") or item.get("name") or "").lower(),
            )
        ]
        rows.append(
            {
                "label": display_discovered_asset_kind(asset_type, language="zh"),
                "label_en": display_discovered_asset_kind(asset_type, language="en"),
                "value": value,
                "details": details,
                "details_heading": "对应资产",
                "details_heading_en": "Related assets",
            }
        )
    return rows


def relative_update_note(value, now=None):
    try:
        updated_at = value if isinstance(value, datetime) else parse_iso_datetime(value)
    except (TypeError, ValueError):
        updated_at = None
    if updated_at is None:
        return ("更新时间未知", "Updated time unknown")
    if updated_at.tzinfo is None:
        updated_at = updated_at.astimezone()
    current_time = now or current_local_datetime()
    if current_time.tzinfo is None:
        current_time = current_time.astimezone()
    seconds = max(0, int((current_time - updated_at).total_seconds()))
    if seconds < 60:
        return ("刚刚更新", "Updated just now")
    minutes = seconds // 60
    if minutes < 60:
        return (
            "{} 分钟前更新".format(minutes),
            "Updated {} minute{} ago".format(minutes, "" if minutes == 1 else "s"),
        )
    hours = minutes // 60
    if hours < 24:
        return (
            "{} 小时前更新".format(hours),
            "Updated {} hour{} ago".format(hours, "" if hours == 1 else "s"),
        )
    days = hours // 24
    return (
        "{} 天前更新".format(days),
        "Updated {} day{} ago".format(days, "" if days == 1 else "s"),
    )


def asset_stats_snapshot_note(snapshot, default_date, now=None):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    has_snapshot = bool(summary)
    if has_snapshot:
        return relative_update_note(snapshot.get("generated_at", ""), now=now)
    return ("等待首次资产刷新", "Waiting for the first asset refresh")


def make_asset_refresh_meta_html(snapshot, default_date, now=None):
    note_zh, note_en = asset_stats_snapshot_note(snapshot, default_date, now=now)
    return '<span class="asset-refresh-meta">{}</span>'.format(
        panel_language_text_html(note_zh, note_en)
    )


def normalized_asset_panels(data):
    panels = dict(data.get("asset_panels") or {})
    mix = data.get("mix") or {}
    render_rows = data.get("asset_panel_rows") or data.get("discovered_asset_rows") or []
    if "type" not in panels:
        panels["type"] = build_discovered_type_mix_rows(render_rows) if render_rows else list(mix.get("type", []))
    if "monthly_activity" not in panels:
        panels["monthly_activity"] = list(mix.get("month", []))
    if "top_skills" not in panels:
        panels["top_skills"] = overview_asset_discovery.top_skill_rows(render_rows, limit=None) if render_rows else []
    return panels


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
            "Note: the panel compares today with the rolling last 7 days. Terms come from each date range's window synthesis, assets, reviews, and usage records.",
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
        "说明：面板并排对照今日和滚动近 7 日。热词来自对应日期范围内的窗口整理、资产、复盘和复用记录。",
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
    memory_items = load_memory_registry_items()
    nightly_candidates = load_nightly_summary_candidates()
    primary_nightly, active_nightly = load_primary_and_active_nightly_summaries()
    display_nightly = select_display_nightly(primary_nightly, active_nightly)
    today = current_local_datetime().date()
    today_date_str = today.isoformat()
    installed_assets = overview_asset_discovery.discover_installed_assets(PATHS)
    discovered_snapshot = overview_asset_discovery.compute_activation_snapshot(
        PATHS,
        installed_assets,
        today,
        monthly_months=6,
    )
    all_discovered_assets = discovered_snapshot["assets"]
    discovered_asset_frequency = discovered_snapshot["frequency_by_key"]
    discovered_monthly_activity = discovered_snapshot["monthly_activity"]
    discovered_assets = overview_asset_discovery.filter_renderable_assets(
        all_discovered_assets,
        discovered_asset_frequency,
    )
    today_nightly = select_best_nightly_summary_for_date(nightly_candidates, today.isoformat())
    if today_nightly:
        display_nightly = today_nightly
    window_anchor_nightly = display_nightly
    today_capture = load_daily_capture(today_date_str)
    today_has_history = today_date_str in set(list_codex_history_dates(lookback_days=1))
    window_target_date = today_date_str if today_nightly or today_capture or today_has_history else ""
    window_source_nightly = window_anchor_nightly if today_nightly or not window_target_date else None
    window_overview = build_window_overview(
        window_source_nightly,
        language=language,
        target_date=window_target_date,
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
    context_memory_preview = build_personal_memory_context_preview(
        memory_registry["rows"],
        memory_mode,
        item_count=personal_memory_token_usage.get("estimated_context_item_count"),
    )
    memory_policy_views = overview_memory_context.build_memory_policy_views(
        memory_registry["rows"],
        selected_global_rows=context_memory_preview,
        token_usage=personal_memory_token_usage,
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
    claude_memory_path = PATHS.claude_home / "CLAUDE.md"
    claude_auto_memory_label = render_path(PATHS.claude_home / "projects" / "*" / "memory" / "*.md")
    claude_memory_path_label = "{} + {}".format(
        render_path(claude_memory_path),
        claude_auto_memory_label,
    )
    claude_native_memory = parse_claude_native_memory_summary(
        claude_memory_path,
        known_project_names=known_project_names,
        language=language,
        claude_home=PATHS.claude_home,
    )
    claude_native_memory_comparison_zh = build_claude_native_memory_comparison(
        claude_native_memory["rows"],
        claude_native_memory["counts"],
        claude_memory_path_label,
        language="zh",
    )
    claude_native_memory_comparison_en = build_claude_native_memory_comparison(
        claude_native_memory["rows"],
        claude_native_memory["counts"],
        claude_memory_path_label,
        language="en",
    )
    claude_native_memory_comparison = (
        claude_native_memory_comparison_en.copy()
        if is_english(language)
        else claude_native_memory_comparison_zh.copy()
    )
    claude_native_memory_comparison["note_zh"] = claude_native_memory_comparison_zh.get("note", "")
    claude_native_memory_comparison["note_en"] = claude_native_memory_comparison_en.get("note", "")
    summary = summarize_assets(assets)
    usage_by_asset, recorded_minutes_saved_total, recent_usage_events = summarize_usage(usage_events)
    enriched_assets = enrich_assets(
        assets,
        usage_by_asset,
        known_project_names,
        window_overview=window_overview,
        language=language,
    )
    discovered_render_rows = overview_asset_discovery.aggregate_renderable_assets(
        discovered_assets,
        discovered_asset_frequency,
    )
    asset_panel_rows = overview_asset_discovery.merge_manual_asset_rows(
        discovered_render_rows,
        enriched_assets,
    )
    discovered_type_mix_rows = build_discovered_type_mix_rows(discovered_render_rows)
    discovered_top_skill_rows = overview_asset_discovery.top_skill_rows(asset_panel_rows, limit=None)
    mcp_usage_view = overview_mcp_usage.build_mcp_usage_view(
        PATHS,
        today,
        lookback_days=30,
        limit=None,
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
    pipeline_status = overview_pipeline_status.load_status(PATHS)
    daily_summary_views = build_daily_summary_views(nightly_candidates, language=language)
    backfill = build_backfill_view(nightly_candidates)
    asset_stats_snapshot = load_asset_stats_snapshot()
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
        today_date_str
        if today_capture or today_has_history
        else ((window_overview or {}).get("date", "") or daily_summary_default_date or today_date_str)
    )
    window_overview_views = build_window_overview_views(
        nightly_candidates,
        selected_date=window_overview_default_date,
        language=language,
    )
    window_overview_views = ensure_window_overview_view(
        window_overview_views,
        window_overview,
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
            "label": localized("登记册资产", "Registry Assets", language),
            "value": len(assets),
            "caption": localized(
                "registry/assets.jsonl 条目；新增 skills 只算已发现资产",
                "registry/assets.jsonl rows; new skills count as discovered only",
                language,
            ),
        },
        {
            "key": "discovered_assets",
            "label": localized("已发现资产", "Discovered Assets", language),
            "value": len(discovered_render_rows),
            "caption": localized(
                "按名称聚合后的可展示资产",
                "Displayable assets after name-based grouping",
                language,
            ),
        },
        {
            "key": "active_assets",
            "label": localized("登记册活跃资产", "Active Registry Assets", language),
            "value": summary["active_assets"],
            "caption": localized(
                "assets.jsonl 中 status=active 的条目",
                "assets.jsonl entries with status=active",
                language,
            ),
        },
        {
            "key": "task_reviews",
            "label": localized("任务复盘", "Task Reviews", language),
            "value": len(reviews),
            "caption": localized("本地保存的脱敏复盘", "Sanitized local reviews", language),
        },
        {
            "key": "repo_scoped_assets",
            "label": localized("登记册仓库资产", "Repo-scoped Registry Assets", language),
            "value": summary["scope_counter"].get("repo", 0),
            "caption": localized(
                "assets.jsonl 中 scope=repo 的条目",
                "assets.jsonl entries with scope=repo",
                language,
            ),
        },
        {
            "key": "today_token",
            "label": localized("筛选 Token", "Filtered Token", language),
            "value": token_usage.get("period_total_tokens_display", token_usage["today_total_tokens_display"]),
            "caption": token_usage.get("range_label") or localized("筛选区间", "Selected Range", language),
            "meta": token_snapshot_note,
            "live": True,
        },
        {
            "key": "seven_day_token",
            "label": localized("周期成本", "Period Cost", language),
            "value": token_usage.get("period_cost_display", token_usage["seven_day_cost_display"]),
            "caption": localized(
                "均值 {} / {} 个有数据{}".format(
                    token_usage.get("period_average_tokens_display", "—"),
                    token_usage.get("active_period_count", 0),
                    token_usage.get("period_unit", "日"),
                ),
                "Average {} / {} active {}".format(
                    token_usage.get("period_average_tokens_display", "—"),
                    token_usage.get("active_period_count", 0),
                    token_usage.get("period_unit", "days"),
                ),
                language,
            ),
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
                "个人资产-工作记忆",
                "Personal Asset - Work Memory",
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
    if active_nightly and display_nightly is not active_nightly:
        active_stage = active_nightly.get("stage", "manual")
        active_stage_label = stage_display_label(active_stage, language=language)
        active_nightly_note = localized(
            "今日另有活跃整理：{} · {}".format(active_nightly.get("date", ""), active_stage_label),
            "Another active synthesis exists today: {} · {}".format(active_nightly.get("date", ""), active_stage_label),
            language,
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
        "schema_version": overview_contract.SCHEMA_VERSION,
        "language": language,
        "generated_at": generated_at,
        "generated_at_iso": generated_at_iso,
        "summary": {
            "total_assets": len(assets),
            "discovered_assets": len(discovered_render_rows),
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
        "discovered_assets": discovered_assets,
        "all_discovered_assets": all_discovered_assets,
        "discovered_asset_frequency": discovered_asset_frequency,
        "discovered_asset_rows": discovered_render_rows,
        "asset_panel_rows": asset_panel_rows,
        "asset_panels": {
            "type": discovered_type_mix_rows,
            "monthly_activity": discovered_monthly_activity,
            "top_skills": discovered_top_skill_rows,
        },
        "mcp_usage": mcp_usage_view,
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
        "pipeline_status": pipeline_status,
        "token_usage": token_usage,
        "daily_summary_views": daily_summary_views,
        "daily_summary_default_date": daily_summary_default_date,
        "daily_summary_select_dates": daily_summary_select_dates,
        "today_date": today_date_str,
        "backfill": backfill,
        "asset_stats_snapshot": asset_stats_snapshot,
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
        "memory_policy_views": memory_policy_views,
        "personal_memory_token_usage": personal_memory_token_usage,
        "context_memory_preview": context_memory_preview,
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
        "claude_native_memory": claude_native_memory["rows"],
        "claude_native_topic_rows": claude_native_memory.get("topic_rows", []),
        "claude_native_preference_rows": claude_native_memory.get("preference_rows", []),
        "claude_native_tip_rows": claude_native_memory.get("tip_rows", []),
        "claude_native_memory_counts": claude_native_memory["counts"],
        "claude_native_memory_comparison": claude_native_memory_comparison,
        "claude_memory_path": str(claude_memory_path),
        "claude_memory_path_label": claude_memory_path_label,
        "nightly_memory_views": nightly_memory_views,
        "reading_guide": [
            localized(
                "看长期可复用资产的增长，而不是看和 AI 聊了多少次。",
                "Track growth in long-lived reusable assets, not chat volume.",
                language,
            ),
            localized(
                "先看「已发现资产」和「高频 skills 热度」：前者说明本机可用资产面，后者说明模型最近真实读了哪些 skills。",
                "Start with Discovered Assets and Skill Hotness: one shows available local assets, the other shows which skills the model actually read recently.",
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
    asset_panels = normalized_asset_panels(data)
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
            "- Registered assets: `{}`".format(data["summary"]["total_assets"]),
            "- Discovered assets: `{}`".format(data["summary"].get("discovered_assets", 0)),
            "- Active registered assets: `{}`".format(data["summary"]["active_assets"]),
            "- Repo-scoped registered assets: `{}`".format(data["summary"].get("repo_scoped_assets", 0)),
            "- Task reviews: `{}`".format(data["summary"]["task_reviews"]),
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
                make_table(Counter({row.get("label_en", row.get("label", "")): row["value"] for row in asset_panels["type"]}), ["Type", "Count"]),
                "",
                "## Monthly Activity",
                "",
                make_table(Counter({row["label"]: row["value"] for row in asset_panels["monthly_activity"]}), ["Month", "Active Skills"]),
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

        claude_native_note = (data.get("claude_native_memory_comparison") or {}).get("note", "")
        claude_memory_label = data.get("claude_memory_path_label") or render_path(PATHS.claude_home / "CLAUDE.md")
        lines.extend(
            [
                "",
                "## Claude Code Native Memory",
                "",
                "- Overview: {}".format(markdown_inline_text(claude_native_note or "No Claude Code native memory summary.")),
                "- Source: {}".format(markdown_inline_text(claude_memory_label)),
                "",
                "| Title | Recently Updated | Related Context | Summary |",
                "| --- | --- | --- | --- |",
            ]
        )
        if data.get("claude_native_memory"):
            for item in data.get("claude_native_memory", [])[:12]:
                lines.append(
                    "| {} | {} | {} | {} |".format(
                        markdown_table_cell(item.get("display_title") or item.get("title", ""), limit=92),
                        markdown_table_cell(item.get("updated_at_display", "")),
                        markdown_table_cell(" / ".join(item.get("context_labels", [])[:2]) or item.get("display_context", "")),
                        markdown_table_cell(item.get("display_value_note") or item.get("value_note", ""), limit=120),
                    )
                )
            hidden_native_count = len(data.get("claude_native_memory", [])) - 12
            if hidden_native_count > 0:
                lines.append("| {} more hidden |  |  | See the HTML panel. |".format(hidden_native_count))
        else:
            lines.append("| None | None | None | None |")

        lines.extend(
            [
                "",
                "## Top 10 Skills (last 30 days)",
                "",
                "| Name | Description | 30d Reads | 30d Sessions |",
                "| --- | --- | --- | --- |",
            ]
        )
        if asset_panels["top_skills"]:
            for asset in asset_panels["top_skills"][:10]:
                lines.append(
                    "| {} | {} | {} | {} |".format(
                        markdown_table_cell(asset.get("name") or asset.get("identifier", ""), limit=80),
                        markdown_table_cell(asset.get("description", ""), limit=90),
                        asset.get("read_events_30d", asset.get("windows_30d", 0)),
                        asset.get("windows_30d", 0),
                    )
                )
        else:
            lines.append("| None | None | 0 | 0 |")

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
        "- 登记册资产：`{}`".format(data["summary"]["total_assets"]),
        "- 已发现资产：`{}`".format(data["summary"].get("discovered_assets", 0)),
        "- 登记册活跃资产：`{}`".format(data["summary"]["active_assets"]),
        "- 登记册仓库资产：`{}`".format(data["summary"].get("repo_scoped_assets", 0)),
        "- 任务复盘：`{}`".format(data["summary"]["task_reviews"]),
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
                Counter({row["label"]: row["value"] for row in asset_panels["type"]}),
                ["类型", "数量"],
                empty_label="暂无",
            ),
            "",
            "## 月度活动",
            "",
            make_table(
                Counter({row["label"]: row["value"] for row in asset_panels["monthly_activity"]}),
                ["月份", "活跃 skills 数"],
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

    claude_native_note = (data.get("claude_native_memory_comparison") or {}).get("note", "")
    claude_memory_label = data.get("claude_memory_path_label") or render_path(PATHS.claude_home / "CLAUDE.md")
    lines.extend(
        [
            "",
            "## Claude Code 原生记忆",
            "",
            "- 概览: {}".format(markdown_inline_text(claude_native_note or "暂无 Claude Code 原生记忆摘要。")),
            "- 来源: {}".format(markdown_inline_text(claude_memory_label)),
            "",
            "| 标题 | 最近更新 | 关联上下文 | 摘要 |",
            "| --- | --- | --- | --- |",
        ]
    )
    if data.get("claude_native_memory"):
        for item in data.get("claude_native_memory", [])[:12]:
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
        hidden_native_count = len(data.get("claude_native_memory", [])) - 12
        if hidden_native_count > 0:
            lines.append("| 另有 {} 条未展示 |  |  | 详见 HTML 面板。 |".format(hidden_native_count))
    else:
        lines.append("| 暂无 | 暂无 | 暂无 | 暂无 |")

    lines.extend(
        [
            "",
            "## 近 30 天高频 skills Top 10",
            "",
            "| 名称 | 描述 | 30 天读取 | 30 天会话 |",
            "| --- | --- | --- | --- |",
        ]
    )

    if asset_panels["top_skills"]:
        for asset in asset_panels["top_skills"][:10]:
            lines.append(
                "| {} | {} | {} | {} |".format(
                    markdown_table_cell(asset.get("name") or asset.get("identifier", ""), limit=80),
                    markdown_table_cell(asset.get("description", ""), limit=90),
                    asset.get("read_events_30d", asset.get("windows_30d", 0)),
                    asset.get("windows_30d", 0),
                )
            )
    else:
        lines.append("| 暂无 | 暂无 | 0 | 0 |")

    lines.extend(["", "## 阅读提示", ""])
    lines.extend("- {}".format(item) for item in data["reading_guide"])
    return "\n".join(lines) + "\n"


def build_csv(data, output_path):
    def csv_value(value):
        if isinstance(value, str):
            return normalize_brand_display_text(value)
        return value

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
                    csv_value(value)
                    for value in [
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
    if not os.environ.get(WRITE_REPO_PANEL_ENTRYPOINT_ENV):
        return
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


def make_token_filter_panel(token_usage):
    provider = overview_token_fetcher.normalize_token_provider(token_usage.get("provider", "all"))
    group_by = overview_token_usage.normalize_token_group_by(token_usage.get("group_by", "day"))
    range_label = escape(str(token_usage.get("range_label", "")))
    provider_options = [
        ("all", "全部", "All"),
        ("codex", "Codex", "Codex"),
        ("claude", "Claude", "Claude"),
    ]
    group_options = [
        ("day", "按日", "Daily"),
        ("month", "按月", "Monthly"),
    ]

    def make_segment_button(value, label_zh, label_en, active_value, name):
        pressed = str(value == active_value).lower()
        active_attr = ' data-active="true"' if value == active_value else ""
        return """
          <button class="token-segment-button" type="button" data-token-{name}="{value}" aria-pressed="{pressed}"{active_attr}>
            {label}
          </button>
        """.format(
            name=escape(name),
            value=escape(value, quote=True),
            pressed=pressed,
            active_attr=active_attr,
            label=panel_language_text_html(label_zh, label_en),
        )

    return """
    <section class="token-filter-panel" id="token-filter-panel">
      <div class="token-filter-head">
        <h2>{title}</h2>
        <span class="token-filter-summary" id="token-filter-summary">{range_label}</span>
      </div>
      <div class="token-filter-grid">
        <div class="token-filter-field token-filter-source">
          <span class="token-filter-label">{source_label}</span>
          <div class="token-segment-group" role="group" aria-label="{source_aria}">
            {provider_buttons}
          </div>
        </div>
        <div class="token-filter-field token-filter-range" data-token-date-field="start">
          <label class="token-filter-label" for="token-start-date">{start_label}</label>
          <input id="token-start-date" class="token-date-input" type="date" value="">
        </div>
        <div class="token-filter-field token-filter-range" data-token-date-field="end">
          <label class="token-filter-label" for="token-end-date">{end_label}</label>
          <input id="token-end-date" class="token-date-input" type="date" value="">
        </div>
        <div class="token-filter-field token-filter-grain">
          <span class="token-filter-label">{grain_label}</span>
          <div class="token-segment-group" role="group" aria-label="{grain_aria}">
            {group_buttons}
          </div>
        </div>
        <button class="token-reset-button" type="button" id="token-reset-button">{reset_label}</button>
      </div>
    </section>
    """.format(
        title=panel_language_text_html("Token 筛选", "Token Filters"),
        range_label=range_label,
        source_label=panel_language_text_html("来源", "Source"),
        source_aria=escape("Token 来源", quote=True),
        provider_buttons="".join(
            make_segment_button(value, label_zh, label_en, provider, "provider")
            for value, label_zh, label_en in provider_options
        ),
        start_label=panel_language_text_html("起始日期", "Start Date"),
        end_label=panel_language_text_html("结束日期", "End Date"),
        grain_label=panel_language_text_html("粒度", "Granularity"),
        grain_aria=escape("Token 粒度", quote=True),
        group_buttons="".join(
            make_segment_button(value, label_zh, label_en, group_by, "group")
            for value, label_zh, label_en in group_options
        ),
        reset_label=panel_language_text_html("重置", "Reset"),
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
    collapsed_label="",
    collapsed_label_en="",
    open_by_default=False,
):
    if not extra_html or extra_count <= 0:
        return primary_html
    if collapsed_label:
        collapsed_label_html = panel_language_text_html(
            collapsed_label,
            collapsed_label_en or panel_english_text(collapsed_label) or collapsed_label,
        )
    else:
        collapsed_label_html = panel_language_text_html(
            "查看更多 {} {}".format(extra_count, item_label),
            "Show {} more {}".format(extra_count, item_label_en or panel_english_text(item_label) or item_label),
        )
    expanded_label_html = panel_language_text_html(
        expanded_label,
        expanded_label_en or panel_english_text(expanded_label) or expanded_label,
    )
    open_attr = " open" if open_by_default else ""
    return """
        {primary_html}
        <details class="content-more"{open_attr}>
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
        open_attr=open_attr,
        collapsed_label=collapsed_label_html,
        expanded_label=expanded_label_html,
        extra_container_class=escape(extra_container_class),
        extra_html=extra_html,
    )


def make_discovered_asset_name_html(row):
    name = str(row.get("name") or row.get("identifier") or "").strip()
    if row.get("click_target"):
        return '<button type="button" class="discovered-skill-name" data-open-finder-path="{path}" data-label="{label}" title="{title}">{name}</button>'.format(
            path=escape(str(row.get("click_target") or ""), quote=True),
            label=escape(name, quote=True),
            title=escape(localized("在 Finder 中显示", "Reveal in Finder", LANGUAGE), quote=True),
            name=escape(name),
        )
    return escape(name)


def make_discovered_description_html(row, limit=60):
    raw_description = normalize_brand_display_text(row.get("description", ""))
    if not raw_description:
        raw_description = "—"
    display_description = compact_preview_text(raw_description, limit=limit)
    return '<span class="asset-discovery-description" title="{title}">{display}</span>'.format(
        title=escape(raw_description, quote=True),
        display=escape(display_description),
    )


def make_source_tag_row(row):
    if row.get("type") != "skill":
        return ""
    tags = []
    for source in row.get("source_labels", []) or []:
        label = source.get("label", "")
        if not label:
            continue
        tags.append(
            '<span class="asset-source-tag">{}</span>'.format(
                panel_language_text_html(label, source.get("label_en", "") or label)
            )
        )
    if not tags:
        return ""
    return '<div class="asset-source-tags">{}</div>'.format(", ".join(tags))


def make_discovered_asset_table_rows(rows):
    def stat_cells(row):
        if row.get("type") != "skill":
            dash = escape("—")
            return dash, dash, dash
        return (
            escape(str(safe_int(row.get("windows_7d", 0)))),
            escape(str(safe_int(row.get("windows_30d", 0)))),
            escape(str(row.get("last_seen") or "—")),
        )

    rendered_rows = []
    for row in rows:
        count_7d, count_30d, last_seen = stat_cells(row)
        rendered_rows.append(
            """
          <tr data-asset-identifier="{identifier}" data-asset-type="{asset_type}">
            <td>
              <div class="asset-discovery-name">{name}</div>
              {source_tags}
            </td>
            <td>{description}</td>
            <td>{count_7d}</td>
            <td>{count_30d}</td>
            <td>{last_seen}</td>
          </tr>
            """.format(
                identifier=escape(str(row.get("identifier") or ""), quote=True),
                asset_type=escape(str(row.get("type") or ""), quote=True),
                name=make_discovered_asset_name_html(row),
                source_tags=make_source_tag_row(row),
                description=make_discovered_description_html(row),
                count_7d=count_7d,
                count_30d=count_30d,
                last_seen=last_seen,
            )
        )
    return "".join(rendered_rows)


def make_discovered_assets_section(render_rows):
    render_rows = list(render_rows or [])

    grouped = defaultdict(list)
    for row in render_rows:
        asset_type = row.get("type", "")
        if asset_type:
            grouped[asset_type].append(row)

    summary_parts = []
    for asset_type in DISCOVERED_TYPE_ORDER:
        count = len(grouped.get(asset_type, []))
        if count <= 0:
            continue
        summary_parts.append(
            '<span class="asset-discovery-summary-item">{label} <strong>{count}</strong></span>'.format(
                label=panel_language_text_html(
                    display_discovered_asset_kind(asset_type, language="zh"),
                    display_discovered_asset_kind(asset_type, language="en"),
                ),
                count=escape(str(count)),
            )
        )
    summary_html = ""
    if summary_parts:
        summary_html = '<div class="asset-discovery-summary">{}</div>'.format(" · ".join(summary_parts))

    last_activity_title = escape(
        "{} / {}".format(
            "模型最近一次读取此 skills SKILL.md 的本地日期；与 skills 的添加或修改时间无关。",
            "Local date when the model most recently read this skill's SKILL.md. Not the skill's added or modified date.",
        ),
        quote=True,
    )

    def render_group(asset_type, rows):
        sorted_rows = sorted(
            rows,
            key=lambda row: (
                -safe_int(row.get("windows_30d", 0)),
                str(row.get("identifier") or row.get("name") or "").lower(),
            ),
        )
        table_html = """
          <table class="asset-discovery-table">
            <thead>
              <tr>
                <th>{name_header}</th>
                <th>{description_header}</th>
                <th title="{count_7d_title}">{count_7d_header}</th>
                <th title="{count_30d_title}">{count_30d_header}</th>
                <th title="{last_activity_title}">{last_activity_header}</th>
              </tr>
            </thead>
            <tbody>
              {rows}
            </tbody>
          </table>
        """.format(
            name_header=panel_language_text_html("名称", "Name"),
            description_header=panel_language_text_html("描述", "Description"),
            count_7d_title=escape(
                "{} / {}".format(
                    "近 7 天，模型实际读取过该 skills 的 SKILL.md 的会话数",
                    "Sessions in the last 7 days where the model read this skill's SKILL.md",
                ),
                quote=True,
            ),
            count_7d_header=panel_language_text_html("7 天", "7d"),
            count_30d_title=escape(
                "{} / {}".format(
                    "近 30 天，模型实际读取过该 skills 的 SKILL.md 的会话数",
                    "Sessions in the last 30 days where the model read this skill's SKILL.md",
                ),
                quote=True,
            ),
            count_30d_header=panel_language_text_html("30 天", "30d"),
            last_activity_title=last_activity_title,
            last_activity_header=panel_language_text_html("最近活动", "Last Activity"),
            rows=make_discovered_asset_table_rows(sorted_rows),
        )
        label_zh = display_discovered_asset_kind(asset_type, language="zh")
        label_en = display_discovered_asset_kind(asset_type, language="en")
        title_zh = "{} · {}".format(label_zh, len(sorted_rows))
        title_en = "{} · {}".format(label_en, len(sorted_rows))
        return wrap_expandable_block(
            "",
            table_html,
            len(sorted_rows),
            label_zh,
            "table-wrap asset-discovery-table-wrap",
            expanded_label=title_zh,
            item_label_en=label_en,
            expanded_label_en=title_en,
            collapsed_label=title_zh,
            collapsed_label_en=title_en,
            open_by_default=len(sorted_rows) <= 8,
        )

    groups_html = []
    for asset_type in DISCOVERED_TYPE_ORDER:
        rows = grouped.get(asset_type, [])
        if rows:
            groups_html.append(render_group(asset_type, rows))

    if not groups_html:
        groups_html.append('<p class="empty">{}</p>'.format(panel_language_text_html("暂无已发现资产。", "No discovered assets.")))

    header = make_panel_header(
        "已发现的 Codex / Claude 资产",
        note_content_html=panel_language_text_html(
            '从本机扫描到的可用资产，以及过去 30 天里被模型真实读取过的项目内 / 跨仓库 skills。skills 按名称聚合，频率统计基于模型读取 SKILL.md 的会话数；非 skills 类显示 "—"。',
            'Assets scanned from this machine, plus project-local and external-repo skills the model actually read in the past 30 days. Skills aggregate by name, frequency counts sessions where the model read SKILL.md; non-skill types render "—".',
        ),
    )
    return """
      <section class="panel discovered-assets-panel" id="discovered-assets-section" data-discovered-assets-label="已发现资产 / Discovered Assets">
        {header}
        {summary}
        <div class="asset-discovery-groups">
          {groups}
        </div>
      </section>
    """.format(
        header=header,
        summary=summary_html,
        groups="".join(groups_html),
    )


def make_asset_stats_snapshot_panel(snapshot, default_date):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    has_snapshot = bool(summary)
    header = make_panel_header("单次资产统计")
    skill_reads = summary.get("skill_reads_30d") if has_snapshot else None
    if skill_reads is None:
        skill_activity_stat = (
            "30 天 skills 会话",
            "30d Skill Sessions",
            summary.get("skill_sessions_30d", "—") if has_snapshot else "—",
            "按会话去重",
            "Deduped by session",
        )
    else:
        skill_activity_stat = (
            "30 天 skills 读取",
            "30d Skill Reads",
            skill_reads,
            "模型读取 SKILL.md",
            "Model SKILL.md reads",
        )

    stat_items = [
        (
            "已发现资产",
            "Discovered",
            summary.get("display_assets", summary.get("renderable_assets", "—")) if has_snapshot else "—",
            "按名称聚合后展示",
            "Displayable after grouping by name",
        ),
        (
            "30 天活跃 skills",
            "30d Active Skills",
            summary.get("active_skills_30d", "—") if has_snapshot else "—",
            "按 skills 名去重",
            "Deduped by skill name",
        ),
        skill_activity_stat,
    ]
    stats_html = []
    for label_zh, label_en, value, meta_zh, meta_en in stat_items:
        display_value = compact_number(value) if isinstance(value, int) else str(value)
        stats_html.append(
            """
            <div class="asset-stats-summary-item">
              <span>{label}</span>
              <strong>{value}</strong>
              <small>{meta}</small>
            </div>
            """.format(
                label=panel_language_text_html(label_zh, label_en),
                value=escape(display_value),
                meta=panel_language_text_html(meta_zh, meta_en),
            )
        )

    return """
      <section class="panel asset-stats-snapshot-panel" id="asset-stats-snapshot-section">
        {header}
        <div class="asset-stats-summary-grid">
          {stats}
        </div>
      </section>
    """.format(
        header=header,
        stats="".join(stats_html),
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
    return primary_rows + extra_rows + toggle_row


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


def make_top_skill_rows(rows, group_id="top-skill-rows"):
    rows = list(rows or [])
    if not rows:
        return '<tr><td colspan="4" class="empty-cell">暂无高频 skills。</td></tr>'

    def render_row(row, row_class="", group_id="", hidden_attr=""):
        attrs = [
            'data-asset-identifier="{}"'.format(escape(str(row.get("identifier") or ""), quote=True)),
            'data-asset-type="skill"',
        ]
        if row_class:
            attrs.append('class="{}"'.format(escape(row_class, quote=True)))
        if group_id:
            attrs.append('data-expand-group="{}"'.format(escape(group_id, quote=True)))
        if hidden_attr:
            attrs.append("hidden")
        return (
            """
            <tr {row_attrs}>
              <td>
                <div class="asset-discovery-name">{name}</div>
              </td>
              <td>{description}</td>
              <td>{reads_30d}</td>
              <td>{sessions_30d}</td>
            </tr>
            """.format(
                row_attrs=" ".join(attrs),
                name=make_discovered_asset_name_html(row),
                description=make_discovered_description_html(row),
                reads_30d=escape(str(safe_int(row.get("read_events_30d", row.get("windows_30d", 0))))),
                sessions_30d=escape(str(safe_int(row.get("windows_30d", 0)))),
            )
        )

    return make_table_expand_rows(
        rows,
        render_row,
        10,
        4,
        "个 skills 热度",
        "收起 skills 热度",
        group_id,
    )


def make_mcp_usage_panel(mcp_usage, help_html=""):
    mcp_usage = mcp_usage or {}
    tools = list(mcp_usage.get("tools") or [])
    lookback_days = safe_int(mcp_usage.get("lookback_days", 30))
    total_calls = safe_int(mcp_usage.get("total_calls", 0))
    active_tools = safe_int(mcp_usage.get("active_tools", 0))
    note_html = panel_language_text_html(
        "近 {} 天，共 {} 次 MCP 调用，{} 个工具有活动".format(lookback_days, total_calls, active_tools),
        "Last {} days, {} MCP calls across {} active tools".format(lookback_days, total_calls, active_tools),
    )

    def render_row(tool, row_class="", group_id="", hidden_attr=""):
        attrs = [
            'data-mcp-name="{}"'.format(escape(str(tool.get("name") or ""), quote=True)),
        ]
        if row_class:
            attrs.append('class="{}"'.format(escape(row_class, quote=True)))
        if group_id:
            attrs.append('data-expand-group="{}"'.format(escape(group_id, quote=True)))
        if hidden_attr:
            attrs.append("hidden")
        description = tool.get("description") or "来自 {} MCP 服务的工具调用。".format(
            tool.get("server") or "MCP"
        )
        description_en = tool.get("description_en") or tool.get("description") or "MCP tool call."
        return """
            <tr {row_attrs}>
              <td>
                <div class="asset-discovery-name">{name}</div>
              </td>
              <td>
                <span class="asset-discovery-description">{description}</span>
              </td>
              <td>{calls}</td>
              <td>{sessions}</td>
            </tr>
            """.format(
            row_attrs=" ".join(attrs),
            name=escape(str(tool.get("label") or tool.get("name") or "")),
            description=panel_language_text_html(description, description_en),
            calls=escape(str(safe_int(tool.get("calls", 0)))),
            sessions=escape(str(safe_int(tool.get("sessions", 0)))),
        )

    rows_html = (
        make_table_expand_rows(
            tools,
            render_row,
            10,
            4,
            "个 MCP 工具",
            "收起 MCP 工具",
            "mcp-usage-rows",
        )
        if tools
        else '<tr><td colspan="4" class="empty-cell">暂无 MCP 调用。</td></tr>'
    )

    return """
    <section class="panel" id="mcp-usage-section">
      {header_html}
      <div class="table-wrap asset-discovery-table-wrap mcp-usage-table-wrap">
        <table class="asset-discovery-table top-skills-table mcp-usage-table">
          <colgroup>
            <col class="top-skills-name-col">
            <col class="top-skills-description-col">
            <col class="top-skills-count-col">
            <col class="top-skills-count-col">
          </colgroup>
          <thead>
            <tr>
              <th>{name_header}</th>
              <th>{description_header}</th>
              <th>{calls_header}</th>
              <th>{sessions_header}</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
      </div>
    </section>
    """.format(
        header_html=make_panel_header(
            "MCP 使用热度",
            help_html=help_html,
            note_content_html=note_html,
        ),
        name_header=panel_language_text_html("名称", "Name"),
        description_header=panel_language_text_html("描述", "Description"),
        calls_header=panel_language_text_html("调用", "Calls"),
        sessions_header=panel_language_text_html("会话", "Sessions"),
        rows_html=rows_html,
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
    max_window_count = max([safe_int(item.get("window_count", 0)) for item in items] or [0])

    def project_discussion_count(item):
        return safe_int(item.get("question_count", 0)) + safe_int(item.get("conclusion_count", 0))

    def render_context_stat(value, label, extra_class=""):
        return """
            <div class="context-stat{extra_class}">
              <strong>{value}</strong>
              <span>{label}</span>
            </div>
            """.format(
            extra_class=escape(extra_class, quote=True),
            value=escape(str(value)),
            label=escape(label),
        )

    def render_source_window_links(source_windows):
        source_windows = source_windows or []
        if not source_windows:
            return ""
        visible_windows = source_windows
        links = []
        for index, ref in enumerate(visible_windows, 1):
            window_id = str(ref.get("window_id", "") or "").strip()
            anchor_id = str(ref.get("anchor_id", "") or "").strip()
            if not anchor_id and window_id:
                anchor_id = "window-{}".format(window_id)
            if not anchor_id:
                continue
            display_label = str(ref.get("display_label", "") or "").strip() or str(index)
            link_label = localized(
                "窗口 {}".format(display_label),
                "Window {}".format(display_label),
                language,
            )
            title_parts = [
                str(ref.get("latest_activity_display", "") or "").strip(),
                str(ref.get("title", "") or "").strip(),
            ]
            title = " · ".join([part for part in title_parts if part])
            links.append(
                '<a class="context-window-link" href="#{anchor_id}" data-window-target="{anchor_id}" title="{title}">{label}</a>'.format(
                    anchor_id=escape(anchor_id, quote=True),
                    title=escape(title or link_label, quote=True),
                    label=escape(link_label),
                )
            )
        if not links:
            return ""
        label = localized("追溯", "Trace", language)
        return """
            <div class="context-window-links">
              <span>{label}</span>
              <div>{links}</div>
            </div>
            """.format(
            label=escape(label),
            links="".join(links),
        )

    def render_task_chips(topics):
        topics = sorted(
            topics or [],
            key=lambda item: (
                safe_int(item.get("window_count", 0)),
                project_discussion_count(item),
                parse_iso_datetime(item.get("latest_activity_at", "")).timestamp()
                if item.get("latest_activity_at")
                else 0,
                str(item.get("label", "")),
            ),
            reverse=True,
        )
        visible_topics = topics[:5]
        hidden_topics = topics[len(visible_topics):]

        def render_task_row(topic):
            label = compact_preview_text(topic.get("label", ""), limit=24)
            if not label:
                label = localized("未命名任务", "Untitled task", language)
            window_label = localized(
                "{} 窗口".format(topic.get("window_count", 0)),
                plural_en(topic.get("window_count", 0), "window"),
                language,
            )
            discussion_label = localized(
                "{} 讨论".format(project_discussion_count(topic)),
                plural_en(project_discussion_count(topic), "discussion"),
                language,
            )
            source_links = render_source_window_links(topic.get("source_windows", []))
            return """
              <div class="context-task-row">
                <div class="context-task-main">
                  <span class="context-task-name">{label}</span>
                  <span class="context-task-count">{window_label}</span>
                  <span class="context-task-count is-muted">{discussion_label}</span>
                </div>
                {source_links}
              </div>
            """.format(
                label=escape(label),
                window_label=escape(window_label),
                discussion_label=escape(discussion_label),
                source_links=source_links,
            )

        if not topics:
            task_rows = '<span class="context-task-empty">{}</span>'.format(
                escape(localized("暂无并行任务", "No parallel tasks", language))
            )
        else:
            task_rows = "".join(render_task_row(topic) for topic in visible_topics)
            if hidden_topics:
                task_rows = wrap_expandable_block(
                    task_rows,
                    "".join(render_task_row(topic) for topic in hidden_topics),
                    len(hidden_topics),
                    localized("个任务", "tasks", language),
                    "context-task-list context-task-more-list content-more-grid",
                    expanded_label=localized("收起更多任务", "Collapse more tasks", language),
                    item_label_en="tasks",
                    expanded_label_en="Collapse more tasks",
                )
        return """
            <div class="context-task-strip">
              <span>{label}</span>
              <div class="context-task-list">{task_rows}</div>
            </div>
            """.format(
            label=escape(localized("并行任务", "Parallel Tasks", language)),
            task_rows=task_rows,
        )

    def render_card(item, index):
        window_count = safe_int(item.get("window_count", 0))
        topic_count = safe_int(item.get("topic_count", len(item.get("topics", []) or [])))
        discussion_count = project_discussion_count(item)
        weight = 0
        if max_window_count > 0 and window_count > 0:
            weight = max(12, min(100, round((window_count / max_window_count) * 100)))
        return """
            <article class="context-card" style="--context-weight: {weight}%;">
              <div class="context-card-rail" aria-hidden="true"><span></span></div>
              <div class="context-project-row">
                <div class="context-card-copy">
                  <div class="context-card-meta">
                    <span class="context-rank">#{index}</span>
                    <span>{recent_activity} {latest_activity}</span>
                  </div>
                  <h3>{label}</h3>
                  <p class="context-card-cwd">{cwd}</p>
                </div>
                <div class="context-card-stats">
                  {topic_count_stat}
                  {window_count_stat}
                  {discussion_count_stat}
                  {latest_activity_stat}
                </div>
              </div>
              <div class="context-project-subrow">
                {tasks}
              </div>
            </article>
            """.format(
                weight=escape(str(weight)),
                index=escape(str(index)),
                recent_activity=escape(localized("最近活动", "Recent activity", language)),
                latest_activity=escape(item.get("latest_activity_display", localized("时间未知", "Unknown time", language))),
                label=escape(item.get("label", "")),
                cwd=escape(item.get("cwd_preview", localized("暂无工作目录", "No working directory", language))),
                topic_count_stat=render_context_stat(topic_count, localized("并行任务", "Tasks", language)),
                window_count_stat=render_context_stat(item.get("window_count", 0), localized("窗口", "Windows", language)),
                discussion_count_stat=render_context_stat(discussion_count, localized("讨论", "Discussions", language)),
                latest_activity_stat=render_context_stat(
                    item.get("latest_activity_display", localized("未知", "Unknown", language)),
                    localized("最近", "Latest", language),
                    " is-time",
                ),
                tasks=render_task_chips(item.get("topics", [])),
            )

    visible_count = PROJECT_CONTEXT_VISIBLE_COUNT
    primary_cards = "".join(
        render_card(item, index)
        for index, item in enumerate(items[:visible_count], 1)
    )
    extra_cards = "".join(
        render_card(item, index)
        for index, item in enumerate(items[visible_count:], visible_count + 1)
    )
    return wrap_expandable_block(
        primary_cards,
        extra_cards,
        len(items) - visible_count,
        localized("组上下文", "contexts", language),
        "project-context-list content-more-grid",
        expanded_label=localized("收起更多上下文", "Collapse more contexts", language),
    )


def make_project_context_overview(view, contexts, days, view_meta, language=None):
    language = current_language(language)
    contexts = contexts or []
    context_count = len(contexts)
    topic_count = sum(safe_int(item.get("topic_count", len(item.get("topics", []) or []))) for item in contexts)
    question_count = sum(safe_int(item.get("question_count", 0)) for item in contexts)
    conclusion_count = sum(safe_int(item.get("conclusion_count", 0)) for item in contexts)
    discussion_count = question_count + conclusion_count
    window_count = safe_int(view.get("window_count", 0))
    headline = localized(
        "{} 个项目，{} 条任务并行".format(context_count, topic_count),
        "{} with {} in parallel".format(
            plural_en(context_count, "context"),
            plural_en(topic_count, "task"),
        ),
        language,
    )

    stat_rows = [
        (context_count, localized("项目", "Contexts", language)),
        (topic_count, localized("并行任务", "Tasks", language)),
        (window_count, localized("窗口", "Windows", language)),
        (discussion_count, localized("讨论", "Discussions", language)),
    ]
    stats_html = "".join(
        """
          <div class="context-map-stat">
            <strong>{value}</strong>
            <span>{label}</span>
          </div>
        """.format(
            value=escape(str(value)),
            label=escape(label),
        )
        for value, label in stat_rows
    )
    note = localized(
        "最近 {} 天共 {} 个窗口、{} 次讨论；项目按讨论数排序，可追溯到窗口明细。".format(
            days,
            window_count,
            discussion_count,
        ),
        "Last {}: {} and {}; projects sorted by discussion count with links back to window details.".format(
            plural_en(days, "day"),
            plural_en(window_count, "window"),
            plural_en(discussion_count, "discussion"),
        ),
        language,
    )
    return """
      <div class="context-map">
        <div class="context-map-copy">
          <div class="context-map-kicker">{kicker}</div>
          <h3>{headline}</h3>
          <p>{note}</p>
          <div class="context-map-meta">{view_meta}</div>
        </div>
        <div class="context-map-signals">
          {stats}
        </div>
      </div>
    """.format(
        kicker=escape(localized("上下文地图", "Context Map", language)),
        headline=escape(headline),
        note=escape(note),
        view_meta=escape(view_meta),
        stats=stats_html,
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
        project_contexts = view.get("project_contexts", [])
        views.append(
            """
            <div class="project-context-view{active}" data-context-view="{days}"{hidden}>
              {overview}
              <div class="project-context-list">
                {cards}
              </div>
            </div>
            """.format(
                active=" is-active" if days == default_days else "",
                days=escape(str(days)),
                hidden="" if days == default_days else " hidden",
                overview=make_project_context_overview(
                    view,
                    project_contexts,
                    days,
                    view_meta,
                    language=language,
                ),
                cards=make_project_context_cards(project_contexts, language=language),
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


def summary_term_registered_count(view):
    return (
        safe_int(view.get("asset_count", 0))
        + safe_int(view.get("review_count", 0))
        + safe_int(view.get("usage_event_count", 0))
    )


def make_summary_term_source_line(view):
    source_dates = view.get("source_dates", []) or []
    if source_dates:
        source_zh = "、".join(source_dates[:3])
        source_en = ", ".join(source_dates[:3])
        if len(source_dates) > 3:
            source_zh = "{} 等 {} 天".format(source_zh, len(source_dates))
            source_en = "{}, and {}".format(source_en, plural_en(len(source_dates), "source date"))
        return panel_language_text_html(
            "来源日期 {}".format(source_zh),
            "Source dates {}".format(source_en),
        )
    return panel_language_text_html("暂无来源日期", "No source dates")


def make_summary_term_stat_pills(view):
    stats = [
        ("窗口", "Windows", view.get("window_count", 0)),
        ("整理", "Syntheses", view.get("nightly_count", 0)),
        ("登记", "Records", summary_term_registered_count(view)),
    ]
    return "".join(
        """
        <span class="term-stat-pill">
          <span>{label}</span>
          <strong>{value}</strong>
        </span>
        """.format(
            label=panel_language_text_html(label_zh, label_en),
            value=escape(str(value)),
        )
        for label_zh, label_en, value in stats
    )


def make_term_rank_list(rows, limit=8):
    rows = rows or []
    if not rows:
        return '<p class="term-empty empty">{}</p>'.format(
            panel_language_text_html("暂无摘要词。", "No summary terms.")
        )

    max_value = max(safe_int(row.get("value", 0)) for row in rows) or 1
    items = []
    for row in rows[:limit]:
        index = len(items)
        value = safe_int(row.get("value", 0))
        weight = value / max_value if max_value else 0
        label = normalize_brand_display_text(str(row.get("label", "")))
        label_en = english_summary_term_label(label)
        prominence = " is-primary" if index == 0 else ""
        items.append(
            """
            <div class="term-rank-item{prominence}" role="listitem" aria-label="{aria_label}" style="--term-level:{level};">
              <span class="term-rank-index">{rank}</span>
              <span class="term-rank-copy">
                <span class="term-rank-label">{label}</span>
                <span class="term-rank-track" aria-hidden="true"></span>
              </span>
              <span class="term-rank-value">{value}</span>
            </div>
            """.format(
                aria_label=escape("{} {} {}".format(label_en or label, value, "weight"), quote=True),
                prominence=prominence,
                level="{:.3f}".format(max(weight, 0.04)),
                rank="{:02d}".format(index + 1),
                label=panel_language_text_html(label, label_en),
                value=escape(str(value)),
            )
        )
    return """
      <div class="term-rank-list" role="list">
        {items}
      </div>
    """.format(
        items="".join(items),
    )


def make_summary_term_card(view):
    days = safe_int(view.get("days", 0))
    rows = view.get("terms", []) or []
    tone_class = "is-today" if days == 1 else "is-weekly"
    kicker_html = (
        panel_language_text_html("今日焦点", "Today focus")
        if days == 1
        else panel_language_text_html("7 日趋势", "7-day trend")
    )
    title_html = panel_language_text_html(
        view.get("title_zh") or view.get("title") or summary_term_title(days, language="zh"),
        view.get("title_en") or summary_term_title(days, language="en"),
    )
    return """
      <article class="term-insight-card {tone}">
        <div class="term-card-head">
          <div class="term-card-title-block">
            <div class="term-card-kicker">{kicker}</div>
            <h3>{title}</h3>
          </div>
          <div class="term-card-count" aria-label="{term_count_label}">
            <strong>{term_count}</strong>
            <span>{term_count_text}</span>
          </div>
        </div>
        {rank_list}
        <div class="term-stat-row">
          {stats}
        </div>
        <div class="term-source-line">{source_line}</div>
      </article>
    """.format(
        tone=tone_class,
        kicker=kicker_html,
        title=title_html,
        term_count_label=escape("{} {}".format(len(rows), "terms"), quote=True),
        term_count=escape(str(len(rows))),
        term_count_text=panel_language_text_html("热词", "terms"),
        stats=make_summary_term_stat_pills(view),
        rank_list=make_term_rank_list(rows),
        source_line=make_summary_term_source_line(view),
    )


def make_summary_term_view_meta(view):
    registered_count = summary_term_registered_count(view)
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
        return '<div class="term-insight-grid"><p class="empty">暂无摘要词。</p></div>'

    day_order = {days: index for index, days in enumerate(SUMMARY_TERM_RANGE_DAYS)}
    ordered_views = [
        view
        for view in sorted(
            views,
            key=lambda item: day_order.get(safe_int(item.get("days", 0)), len(day_order)),
        )
        if safe_int(view.get("days", 0)) in day_order
    ]
    if not ordered_views:
        ordered_views = views[:2]

    cards = [make_summary_term_card(view) for view in ordered_views]

    return """
      <div class="term-insight-grid">
        {cards}
      </div>
    """.format(
        cards="".join(cards),
    )


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
        ("link", "pipeline-section", "运行中", "Pipeline", "当前运行内容", "Current Pipeline"),
        ("group", "记忆层", "Memory Layer"),
        ("link", "memory-section", "个人资产记忆", "Personal Asset Memory", "个人资产记忆", "Personal Asset Memory"),
        ("child", "personal-memory-compiler-section", "总览", "Overview", "个人资产记忆-总览", "Personal Asset Memory - Overview"),
        ("child", "personal-memory-global-section", "通用上下文", "General Context", "个人资产记忆-通用上下文", "Personal Asset Memory - General Context"),
        ("child", "personal-memory-project-section", "项目上下文", "Project Context", "个人资产记忆-项目上下文", "Personal Asset Memory - Project Context"),
        ("child", "personal-memory-on-demand-section", "按需召回", "On-demand Recall", "个人资产记忆-按需召回", "Personal Asset Memory - On-demand Recall"),
        ("child", "personal-memory-local-section", "本地保留", "Local Only", "个人资产记忆-本地保留", "Personal Asset Memory - Local Only"),
        ("link", "codex-native-section", "Codex 原生记忆", "Codex Native Memory", "Codex 原生记忆", "Codex Native Memory"),
        ("link", "claude-native-section", "Claude 原生记忆", "Claude Native Memory", "Claude Code 原生记忆", "Claude Code Native Memory"),
        ("group", "资产层", "Asset Layer"),
        ("link", "asset-overview-section", "总览", "Overview", "资产层总览", "Asset Layer Overview"),
        ("link", "top-assets-section", "skills 热度", "Skill Hotness", "近 30 天高频 skills 热度", "Skill Hotness"),
        ("link", "reviews-section", "复盘记录", "Reviews", "复盘记录", "Reviews"),
        ("link", "project-context-section", "项目上下文", "Context", "项目上下文", "Context"),
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

    title_html = panel_language_text_html("Host context 预算", "Host Context Budget")
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
        aria_label=escape(panel_display_text("Host context 预算"), quote=True),
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
        ("工作", "Work", counts.get("session", 0)),
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


def make_memory_policy_count_widget(policy_views):
    compiler = (policy_views or {}).get("compiler", {})
    total_memories = safe_int(compiler.get("total_count", 0))
    selected_host = safe_int(
        compiler.get("selected_host_context_count", compiler.get("selected_global_count", 0))
    )
    host_candidates = safe_int(
        compiler.get("host_context_candidate_count", compiler.get("global_candidate_count", 0))
    )
    host_value = (
        "{}/{}".format(selected_host, host_candidates)
        if host_candidates and selected_host != host_candidates
        else str(selected_host)
    )
    total_html = panel_language_variant_html(
        "共 {} 条".format(escape(str(total_memories))),
        "{} total".format(escape(str(total_memories))),
    )
    items = [
        ("注入", "Injected", host_value),
        ("通用", "General", safe_int(compiler.get("project_context_count", 0))),
        ("按需", "On-demand", safe_int(compiler.get("on_demand_count", 0))),
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
        <aside class="memory-count-widget memory-policy-widget" aria-label="{aria_label}">
          <div class="memory-count-topline">
            <div class="memory-count-label">{label}</div>
            <div class="memory-count-total">{total}</div>
          </div>
          <div class="memory-count-grid">
            {cards}
          </div>
        </aside>
    """.format(
        aria_label=escape(panel_display_text("上下文策略"), quote=True),
        label=panel_language_text_html("上下文策略", "Context Policy"),
        total=total_html,
        cards="".join(cards),
    )


def make_memory_context_compiler_body(policy_views):
    compiler = (policy_views or {}).get("compiler", {})
    total_count = safe_int(compiler.get("total_count", 0))
    global_context_count = safe_int(compiler.get("global_candidate_count", 0))
    project_count = safe_int(compiler.get("project_context_count", 0))
    on_demand_count = safe_int(compiler.get("on_demand_count", 0))
    meter_percent = max(0, min(100, safe_int(compiler.get("meter_percent", 0))))
    value_display = panel_language_variant_html(
        escape(compiler.get("value_display_zh") or ""),
        escape(compiler.get("value_display_en") or ""),
    )
    status = panel_language_variant_html(
        escape(compiler.get("status_label_zh") or ""),
        escape(compiler.get("status_label_en") or ""),
    )
    mode_note = panel_language_variant_html(
        escape(compiler.get("mode_note_zh") or ""),
        escape(compiler.get("mode_note_en") or ""),
    )

    def stat_card(label_zh, label_en, value, note_zh, note_en):
        return """
          <article class="memory-compiler-stat">
            <span>{label}</span>
            <strong>{value}</strong>
            <small>{note}</small>
          </article>
        """.format(
            label=panel_language_text_html(label_zh, label_en),
            value=escape(str(value)),
            note=panel_language_text_html(note_zh, note_en),
        )

    stats = "".join(
        [
            stat_card(
                "登记册总量",
                "Registry Total",
                total_count,
                "OpenRelix 独立存储的 canonical 条目",
                "Canonical entries stored by OpenRelix",
            ),
            stat_card(
                "通用上下文",
                "General Context",
                global_context_count,
                "会进入通用 host context 的候选",
                "Candidates for the general host context",
            ),
            stat_card(
                "项目上下文",
                "Project Context",
                project_count,
                "按项目、仓库或工作区边界召回",
                "Recalled by project, repo, or workspace boundary",
            ),
            stat_card(
                "按需召回",
                "On-demand",
                on_demand_count,
                "保留索引，需要时再检索",
                "Indexed and retrieved only when needed",
            ),
        ]
    )
    return """
      <div class="memory-compiler-body">
        <div class="memory-compiler-meter">
          <div class="memory-compiler-meter-topline">
            <span>{budget_label}</span>
            <b>{value_display}</b>
            <em>{status}</em>
          </div>
          <div class="memory-token-meter" aria-hidden="true">
            <div class="memory-token-meter-fill" style="width: {meter_percent}%"></div>
          </div>
          <p>{mode_note}</p>
        </div>
        <div class="memory-compiler-grid">
          {stats}
        </div>
      </div>
    """.format(
        budget_label=panel_language_text_html("上下文预算", "Context Budget"),
        value_display=value_display or "—",
        status=status or "—",
        meter_percent=meter_percent,
        mode_note=mode_note or panel_language_text_html("当前没有可注入的个人资产记忆。", "No personal asset memory is currently injectable."),
        stats=stats,
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


def make_memory_cards(items, include_bucket_meta=True, visible_count=4, meta_renderer=None):
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

    def render_detail(title, body_html, en_title=""):
        if not body_html:
            return ""
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

    def source_summary_html(item):
        source_files = item.get("source_files") or []
        if source_files:
            label = ui_text(source_files[0].get("label") or Path(str(source_files[0].get("path", ""))).name)
            if label:
                return escape(label)
        source_windows = item.get("source_windows") or []
        if source_windows:
            label = ui_text(source_windows[0].get("project_label") or source_windows[0].get("cwd_display") or "")
            if label:
                label_en = ui_text(localized_context_label(label, language="en"))
                return panel_language_text_html(label, label_en) if label_en != label else escape(label)
        project_label = ui_text(item.get("project_label") or "")
        if project_label:
            project_label_en = ui_text(localized_context_label(project_label, language="en"))
            return (
                panel_language_text_html(project_label, project_label_en)
                if project_label_en != project_label
                else escape(project_label)
            )
        context = ui_text(item.get("display_context") or "")
        if context:
            context_en = ui_text(localized_context_label(context, language="en"))
            return panel_language_text_html(context, context_en) if context_en != context else escape(context)
        return panel_language_text_html("本地记忆", "Local memory")

    def brief_ui_text(value, limit):
        return compact_preview_text(ui_text(value), limit=limit)

    def text_was_compacted(full_text, brief_text):
        return bool(ui_text(full_text)) and ui_text(full_text) != ui_text(brief_text)

    def render_card(item):
        context_labels = item.get("context_labels", [])
        if not context_labels and item.get("display_context"):
            context_labels = [item.get("display_context")]
        if meta_renderer:
            meta_html = meta_renderer(item)
        else:
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
            meta_html = panel_language_variant_html(
                escape(" · ".join(meta_parts)),
                escape(" · ".join(meta_parts_en)) if meta_parts_en != meta_parts else "",
            )

        created_display = item.get("created_at_display") or display_memory_date(item.get("created_at", ""))
        updated_display = item.get("updated_at_display") or display_memory_date(item.get("updated_at", ""))
        submeta_parts_zh = []
        submeta_parts_en = []
        if has_known_date_display(created_display):
            submeta_parts_zh.append("首次添加 {}".format(format_date_for_language(created_display, "zh")))
            submeta_parts_en.append("First added {}".format(format_date_for_language(created_display, "en")))
        if has_known_date_display(updated_display):
            submeta_parts_zh.append("最近更新 {}".format(format_date_for_language(updated_display, "zh")))
            submeta_parts_en.append("Updated {}".format(format_date_for_language(updated_display, "en")))
        if item.get("usage_frequency_display"):
            window_days = item.get("usage_frequency_window_days", MEMORY_USAGE_WINDOW_DAYS)
            frequency_value = item.get("usage_frequency_display", "0")
            submeta_parts_zh.append("{}日热度 {}".format(window_days, frequency_value))
            submeta_parts_en.append("{}-day heat {}".format(window_days, frequency_value))
        if item.get("occurrence_count", 0) > 1:
            occurrence_label = ui_text(item.get("occurrence_label", "整理命中"))
            occurrence_label_en = english_for_ui_text(occurrence_label)
            submeta_parts_zh.append("{} {} 次".format(occurrence_label, item.get("occurrence_count", 0)))
            submeta_parts_en.append("{} {} times".format(occurrence_label_en, item.get("occurrence_count", 0)))
        if item.get("submeta_zh") or item.get("submeta_en"):
            submeta_html = panel_language_text_html(
                ui_text(item.get("submeta_zh", "")),
                ui_text(item.get("submeta_en", "")),
            )
        else:
            submeta_html = render_submeta_lines(submeta_parts_zh, submeta_parts_en)

        full_display_title = ui_text(item.get("display_title") or item.get("title", ""))
        full_raw_title = ui_text(item.get("display_title_en") or item.get("title", ""))
        display_title = brief_ui_text(full_display_title, MEMORY_BRIEF_TITLE_LIMIT)
        raw_title = brief_ui_text(full_raw_title, MEMORY_BRIEF_TITLE_LIMIT)
        title_html = panel_language_text_html(display_title, raw_title if raw_title != display_title else "")
        full_title_detail = ""
        if text_was_compacted(full_display_title, display_title) or text_was_compacted(full_raw_title, raw_title):
            full_title_detail = render_detail(
                "完整标题",
                panel_language_text_html(
                    full_display_title,
                    full_raw_title if full_raw_title != full_display_title else "",
                ),
                "Full Title",
            )

        full_display_value_note = ui_text(item.get("display_value_note") or item.get("value_note", ""))
        full_raw_value_note = ui_text(
            item.get("display_value_note_en") or item.get("value_note_en") or item.get("value_note", "")
        )
        display_value_note = brief_ui_text(full_display_value_note, MEMORY_BRIEF_BODY_LIMIT)
        raw_value_note = brief_ui_text(full_raw_value_note, MEMORY_BRIEF_BODY_LIMIT)
        value_note_html = panel_language_variant_html(
            linkify_local_paths_html(display_value_note),
            linkify_local_paths_html(raw_value_note) if raw_value_note != display_value_note else "",
        )
        full_note_detail = ""
        if text_was_compacted(full_display_value_note, display_value_note) or text_was_compacted(
            full_raw_value_note,
            raw_value_note,
        ):
            full_note_detail = render_detail(
                "完整说明",
                panel_language_variant_html(
                    linkify_local_paths_html(
                        compact_preview_text(full_display_value_note, limit=MEMORY_BRIEF_FULL_TEXT_LIMIT)
                    ),
                    (
                        linkify_local_paths_html(
                            compact_preview_text(full_raw_value_note, limit=MEMORY_BRIEF_FULL_TEXT_LIMIT)
                        )
                        if full_raw_value_note != full_display_value_note
                        else ""
                    ),
                ),
                "Full Note",
            )

        source_fact_label = ui_text(item.get("source_fact_label", "来源窗口"))
        source_fact_label_en = english_for_ui_text(source_fact_label)
        details = "".join(
            (
                full_title_detail,
                full_note_detail,
                render_detail("更新记录", submeta_html, "Update History") if submeta_html else "",
                render_detail("关联上下文", render_context_chips(context_labels), "Related Context"),
                render_detail("最近工作区", render_cwd_links(item.get("source_windows", [])), "Recent Workspace"),
                render_detail(
                    source_fact_label,
                    render_source_file_links(item.get("source_files", []))
                    if item.get("source_files")
                    else render_source_window_links(item.get("source_windows", [])),
                    source_fact_label_en,
                ),
            )
        )
        details_html = """
          <details class="native-brief-raw memory-brief-details">
            <summary>{label}</summary>
            <div class="memory-card-facts">{details}</div>
          </details>
        """.format(
            label=panel_language_text_html("查看来源与上下文", "Show context and source"),
            details=details,
        )
        feedback_state = ui_text(item.get("user_feedback") or "")
        if feedback_state == "pinned":
            feedback_state = overview_memory_feedback.FEEDBACK_LIKED
        feedback_status_labels = {
            overview_memory_feedback.FEEDBACK_LIKED: ("已标记有用，将优先展示", "Marked useful; prioritized"),
            overview_memory_feedback.FEEDBACK_DOWNVOTED: ("已放入本地保留末尾", "Kept local at lowest priority"),
        }
        feedback_status = feedback_status_labels.get(feedback_state, ("", ""))
        feedback_controls = ""
        memory_key = ui_text(item.get("memory_key") or "")
        if memory_key:
            def feedback_icon(feedback_value):
                if feedback_value == overview_memory_feedback.FEEDBACK_DOWNVOTED:
                    return (
                        '<svg class="memory-feedback-icon" viewBox="0 0 24 24" aria-hidden="true">'
                        '<path d="M17 14V2"></path>'
                        '<path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"></path>'
                        '</svg>'
                    )
                return (
                    '<svg class="memory-feedback-icon" viewBox="0 0 24 24" aria-hidden="true">'
                    '<path d="M7 10v12"></path>'
                    '<path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"></path>'
                    '</svg>'
                )

            def feedback_button(feedback_value, zh_label, en_label):
                active = feedback_state == feedback_value
                return (
                    '<button class="memory-feedback-button{active_class}" type="button" '
                    'data-memory-feedback="{feedback}" data-memory-key="{memory_key}" '
                    'data-memory-title="{memory_title}" aria-pressed="{pressed}">'
                    "{icon}{label}</button>"
                ).format(
                    active_class=" is-active" if active else "",
                    feedback=escape(feedback_value, quote=True),
                    memory_key=escape(memory_key, quote=True),
                    memory_title=escape(full_display_title or full_raw_title, quote=True),
                    pressed="true" if active else "false",
                    icon=feedback_icon(feedback_value),
                    label=panel_language_text_html(zh_label, en_label),
                )

            feedback_controls = """
              <div class="memory-feedback-row" data-memory-feedback-state="{state}">
                {buttons}
                <span class="memory-feedback-status">{status}</span>
              </div>
            """.format(
                state=escape(feedback_state, quote=True),
                buttons="".join(
                    [
                        feedback_button(overview_memory_feedback.FEEDBACK_LIKED, "有用", "Useful"),
                        feedback_button(overview_memory_feedback.FEEDBACK_DOWNVOTED, "无用", "Not useful"),
                    ]
                ),
                status=panel_language_text_html(feedback_status[0], feedback_status[1])
                if feedback_status[0] or feedback_status[1]
                else "",
            )

        return """
          <article class="native-brief-card memory-brief-card">
            <div class="native-brief-topline">
              <span>{meta}</span>
              <span>{source_label}</span>
            </div>
            <h3>{title}</h3>
            <p>{value_note}</p>
            {feedback_controls}
            {details}
          </article>
        """.format(
            meta=meta_html,
            source_label=source_summary_html(item),
            title=title_html or panel_language_text_html("未命名记忆"),
            value_note=value_note_html,
            feedback_controls=feedback_controls,
            details=details_html,
        )

    primary_cards = "".join(render_card(item) for item in items[:visible_count])
    extra_cards = "".join(render_card(item) for item in items[visible_count:])
    return wrap_expandable_block(
        primary_cards,
        extra_cards,
        len(items) - visible_count,
        "条",
        "native-brief-grid memory-grid content-more-grid",
        expanded_label="收起额外条目",
        item_label_en="items",
        expanded_label_en="Collapse extra items",
    )


def context_memory_bucket_label(item, language=None):
    bucket = str(item.get("bucket") or "").strip()
    labels = {
        "durable": ("长期记忆", "Long-term Memory"),
        "session": ("工作记忆", "Work Memory"),
        "low_priority": ("低优先级记忆", "Low-priority Memory"),
    }
    if bucket in labels:
        zh_label, en_label = labels[bucket]
        return localized(zh_label, en_label, language)
    display_bucket = normalize_brand_display_text(
        item.get("display_bucket") or display_memory_bucket(bucket, language=language)
    )
    if is_english(language):
        return panel_english_text(display_bucket) or display_bucket or "Memory"
    display_bucket = display_bucket.replace("个人资产-", "")
    return display_bucket or "记忆"


def context_memory_frequency_label(item, language=None):
    usage_score = safe_float(item.get("usage_frequency_sort_key", item.get("usage_frequency", 0)))
    matched_windows = safe_int(item.get("usage_frequency_matched_window_count", 0))
    direct_windows = safe_int(item.get("usage_frequency_direct_window_count", 0))
    recent_occurrences = safe_int(item.get("usage_frequency_recent_occurrence_count", 0))
    occurrence_count = safe_int(item.get("occurrence_count", 0))
    if direct_windows or matched_windows:
        return localized("直接证据", "Direct Evidence", language)
    if usage_score > 0 or recent_occurrences or occurrence_count >= 3:
        return localized("近期证据", "Recent Evidence", language)
    return localized("待验证", "Needs Evidence", language)


def context_memory_priority_label(item, language=None):
    priority = str(item.get("priority") or "").strip().lower()
    display_priority = normalize_brand_display_text(item.get("display_priority") or "")
    if priority == "high" or display_priority.startswith("高"):
        return localized("高优先", "High Priority", language)
    return localized("中优先", "Medium Priority", language)


def make_context_memory_card_meta(item):
    meta_parts_zh = [
        context_memory_bucket_label(item, language="zh"),
        context_memory_priority_label(item, language="zh"),
        context_memory_frequency_label(item, language="zh"),
    ]
    meta_parts_en = [
        context_memory_bucket_label(item, language="en"),
        context_memory_priority_label(item, language="en"),
        context_memory_frequency_label(item, language="en"),
    ]
    return panel_language_variant_html(
        escape(" · ".join(part for part in meta_parts_zh if part)),
        escape(" · ".join(part for part in meta_parts_en if part)),
    )


def memory_policy_label(item, language=None):
    policy = overview_memory_context.host_context_injection_policy_from_record(item)
    return overview_memory_context.policy_label(policy, language=current_language(language))


def memory_scope_label(item, language=None):
    scope = overview_memory_context.memory_scope_from_record(item)
    return overview_memory_context.scope_label(scope, language=current_language(language))


def make_policy_memory_card_meta(item):
    meta_parts_zh = [
        memory_policy_label(item, language="zh"),
        memory_scope_label(item, language="zh"),
        context_memory_bucket_label(item, language="zh"),
        context_memory_priority_label(item, language="zh"),
    ]
    meta_parts_en = [
        memory_policy_label(item, language="en"),
        memory_scope_label(item, language="en"),
        context_memory_bucket_label(item, language="en"),
        context_memory_priority_label(item, language="en"),
    ]
    return panel_language_variant_html(
        escape(" · ".join(part for part in meta_parts_zh if part)),
        escape(" · ".join(part for part in meta_parts_en if part)),
    )


def make_memory_type_grouped_cards(items, include_bucket_meta=False, meta_renderer=None):
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
              <div class="native-brief-grid memory-grid">
                {cards}
              </div>
            </section>
            """.format(
                title=title_html,
                count=count_html,
                cards=make_memory_cards(
                    sorted_rows,
                    include_bucket_meta=include_bucket_meta,
                    visible_count=4,
                    meta_renderer=meta_renderer,
                ),
            )
        )
    return "".join(sections)


def make_context_memory_type_grouped_cards(items):
    return make_memory_type_grouped_cards(
        items,
        include_bucket_meta=False,
        meta_renderer=make_context_memory_card_meta,
    )


def make_policy_memory_type_grouped_cards(items):
    return make_memory_type_grouped_cards(
        items,
        include_bucket_meta=False,
        meta_renderer=make_policy_memory_card_meta,
    )


def native_meta_to_chinese(meta_text):
    return (
        str(meta_text or "")
        .replace("User preferences", "用户偏好")
        .replace("User Preferences", "用户偏好")
        .replace("General Tips", "通用 tips")
        .replace("Task Groups", "历史任务索引")
        .replace("task group", "历史任务索引")
        .replace("任务组", "历史任务索引")
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
    translated = translated.replace("历史任务索引", "Historical Task Index")
    translated = translated.replace("任务组", "Historical Task Index")
    translated = replace_count(r"(\d+)\s*条Historical Task Index", "historical task index entry", "historical task index entries", translated)
    translated = replace_count(r"(\d+)\s*个Historical Task Index", "historical task index entry", "historical task index entries", translated)
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
            "display_memory_type": localized("历史任务索引", "Historical Task Index", language),
            "default_submeta": localized("Codex 原生 · MEMORY.md 历史任务索引", "Codex Native · MEMORY.md historical task index", language),
            "title_prefix_zh": "历史任务",
            "title_prefix_en": "Historical task",
        },
    }.get(kind, {})

    def split_keywords_for_language(keywords):
        cleaned = [
            normalize_brand_display_text(str(keyword))
            for keyword in (keywords or [])[:5]
            if normalize_brand_display_text(str(keyword))
        ]
        zh_keywords = [keyword for keyword in cleaned if not is_untranslated_english_text(keyword)]
        return zh_keywords, cleaned

    items = []
    for index, row in enumerate(rows, start=1):
        display_title = normalize_brand_display_text(row.get("display_title") or row.get("title") or "{} {}".format(
            kind_config.get("title_prefix_zh", "条目"),
            index,
        ))
        display_title_en = normalize_brand_display_text(
            row.get("display_title_en") or row.get("title_en") or ""
        )
        if not display_title_en and is_untranslated_english_text(display_title):
            display_title_en = display_title
        raw_title = normalize_brand_display_text(row.get("title") or "")
        if not raw_title or raw_title == display_title:
            raw_title = "{} {}".format(kind_config.get("title_prefix_en", "Item"), index)
        raw_title = normalize_brand_display_text(raw_title)
        display_body = normalize_brand_display_text(row.get("display_body") or row.get("body") or row.get("scope", ""))
        body_en = normalize_brand_display_text(
            row.get("display_body_en") or row.get("body") or row.get("scope", "") or display_body
        )
        if not is_english(language) and is_untranslated_english_text(display_title):
            if kind == "task_group":
                display_title = generic_codex_native_task_group_title(
                    row.get("title") or display_title,
                    row.get("keywords", []),
                    index,
                )
            else:
                display_title = "{} {}".format(kind_config.get("title_prefix_zh", "条目"), index)
        if not is_english(language) and is_untranslated_english_text(display_body):
            if kind == "task_group":
                labels = codex_native_task_group_labels_zh(
                    row.get("title") or display_title_en or display_title,
                    row.get("keywords", []),
                )
                display_body = generic_codex_native_task_group_body(
                    row.get("task_count", 0),
                    row.get("rollout_reference_count", 0),
                    labels,
                )
            else:
                display_body = kind_config.get("empty_body_zh", "")
        zh_keywords, en_keywords = split_keywords_for_language(row.get("keywords", []))
        if zh_keywords:
            keyword_text = "、".join(zh_keywords)
            display_body = "{}；关键词：{}".format(display_body, keyword_text) if display_body else "关键词：{}".format(keyword_text)
        if en_keywords:
            body_en = "{}; keywords: {}".format(body_en, ", ".join(en_keywords)) if body_en else "Keywords: {}".format(", ".join(en_keywords))
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
                "display_title_en": display_title_en,
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


def make_codex_native_brief_cards(rows, kind, language=None):
    language = current_language(language)
    rows = rows or []
    if not rows:
        return '<p class="empty">{}</p>'.format(
            escape(localized("暂无。", "None yet.", language))
        )

    kind_config = {
        "preference": {
            "kicker_zh": "用户偏好",
            "kicker_en": "User Preference",
            "default_title_zh": "偏好",
            "default_title_en": "Preference",
            "empty_body_zh": "暂无偏好说明。",
            "empty_body_en": "No preference text.",
        },
        "tip": {
            "kicker_zh": "通用 tips",
            "kicker_en": "General Tip",
            "default_title_zh": "通用 tips",
            "default_title_en": "General tip",
            "empty_body_zh": "暂无通用提示。",
            "empty_body_en": "No general tip text.",
        },
        "task_group": {
            "kicker_zh": "历史任务索引",
            "kicker_en": "Historical Task Index",
            "default_title_zh": "历史任务",
            "default_title_en": "Historical task",
            "empty_body_zh": "MEMORY.md 中登记的历史任务索引。",
            "empty_body_en": "Historical task index entry registered in MEMORY.md.",
        },
    }.get(kind, {})

    def row_source_label(row):
        source_files = row.get("source_files") or []
        for source_file in source_files:
            label = source_file.get("label") or Path(str(source_file.get("path", ""))).name
            if label:
                return normalize_brand_display_text(label)
        return "MEMORY.md" if kind == "task_group" else "memory_summary.md"

    def row_keywords(row):
        return [
            normalize_brand_display_text(str(keyword))
            for keyword in row.get("keywords", [])[:5]
            if normalize_brand_display_text(str(keyword))
        ]

    def split_keywords_for_language(row):
        keywords = row_keywords(row)
        zh_keywords = [keyword for keyword in keywords if not is_untranslated_english_text(keyword)]
        return zh_keywords, keywords

    def render_keyword_chip_html(keywords):
        return "".join(
            '<span class="native-brief-chip">{}</span>'.format(escape(keyword))
            for keyword in keywords
        )

    def render_keywords(row):
        zh_keywords, en_keywords = split_keywords_for_language(row)
        if not zh_keywords and not en_keywords:
            return ""
        zh_html = render_keyword_chip_html(zh_keywords)
        en_html = render_keyword_chip_html(en_keywords)
        if zh_html and en_html and zh_html != en_html:
            return (
                '<div class="native-brief-chip-row" data-lang-only="zh">{}</div>'
                '<div class="native-brief-chip-row" data-lang-only="en">{}</div>'
            ).format(zh_html, en_html)
        if zh_html:
            return '<div class="native-brief-chip-row">{}</div>'.format(zh_html)
        return '<div class="native-brief-chip-row" data-lang-only="en">{}</div>'.format(en_html)

    def render_meta(row):
        meta = normalize_brand_display_text(row.get("meta") or "")
        if not meta or kind != "task_group":
            return ""
        meta_zh = native_meta_to_chinese(meta)
        meta_en = native_meta_to_english(meta_zh)
        return '<div class="native-brief-meta">{}</div>'.format(
            panel_language_text_html(meta_zh, meta_en)
        )

    def render_raw_details(raw_text, display_text, brief_text=""):
        raw_text = normalize_brand_display_text(raw_text)
        display_text = normalize_brand_display_text(display_text)
        brief_text = normalize_brand_display_text(brief_text)
        if not raw_text and not display_text:
            return ""
        if raw_text == display_text and (not brief_text or display_text == brief_text):
            return ""
        detail_text = raw_text if raw_text and raw_text != display_text else display_text
        label_zh = "查看英文原文" if raw_text and raw_text != display_text else "查看完整说明"
        label_en = "Show source text" if raw_text and raw_text != display_text else "Show full note"
        return """
          <details class="native-brief-raw">
            <summary>{label}</summary>
            <p>{raw_text}</p>
          </details>
        """.format(
            label=panel_language_text_html(label_zh, label_en),
            raw_text=escape(compact_preview_text(detail_text, limit=MEMORY_BRIEF_FULL_TEXT_LIMIT)),
        )

    def render_card(row, index):
        fallback_title_zh = "{} {}".format(kind_config.get("default_title_zh", "条目"), index)
        fallback_title_en = "{} {}".format(kind_config.get("default_title_en", "Item"), index)
        display_title_full = normalize_brand_display_text(row.get("display_title") or fallback_title_zh)
        display_title_en = normalize_brand_display_text(row.get("display_title_en") or "")
        raw_title = normalize_brand_display_text(row.get("title") or fallback_title_en)
        if not display_title_en and is_untranslated_english_text(display_title_full):
            display_title_en = display_title_full
        if not is_english(language) and is_untranslated_english_text(display_title_full):
            if kind == "task_group":
                display_title_full = generic_codex_native_task_group_title(
                    row.get("title") or display_title_full,
                    row.get("keywords", []),
                    index,
                )
            else:
                display_title_full = fallback_title_zh
        if not display_title_en and raw_title != display_title_full:
            display_title_en = raw_title
        display_body_full = normalize_brand_display_text(
            row.get("display_body")
            or row.get("body")
            or row.get("scope")
            or kind_config.get("empty_body_zh", "")
        )
        raw_body_full = normalize_brand_display_text(
            row.get("display_body_en")
            or row.get("body_en")
            or row.get("body")
            or row.get("scope")
            or row.get("display_body")
            or kind_config.get("empty_body_en", "")
        )
        if not is_english(language) and is_untranslated_english_text(display_body_full):
            if kind == "task_group":
                labels = codex_native_task_group_labels_zh(
                    row.get("title") or display_title_en or display_title_full,
                    row.get("keywords", []),
                )
                display_body_full = generic_codex_native_task_group_body(
                    row.get("task_count", 0),
                    row.get("rollout_reference_count", 0),
                    labels,
                )
            else:
                display_body_full = kind_config.get("empty_body_zh", "")
        zh_keywords, en_keywords = split_keywords_for_language(row)
        if zh_keywords:
            keyword_text = "、".join(zh_keywords)
            display_body_full = "{}；关键词：{}".format(display_body_full, keyword_text) if display_body_full else "关键词：{}".format(keyword_text)
        if en_keywords:
            raw_body_full = "{}; keywords: {}".format(raw_body_full, ", ".join(en_keywords)) if raw_body_full else "Keywords: {}".format(", ".join(en_keywords))

        display_title = compact_preview_text(display_title_full, limit=MEMORY_BRIEF_TITLE_LIMIT)
        raw_title_brief = compact_preview_text(raw_title, limit=MEMORY_BRIEF_TITLE_LIMIT)
        title_en = compact_preview_text(display_title_en, limit=MEMORY_BRIEF_TITLE_LIMIT) or (
            raw_title_brief if raw_title_brief != display_title else ""
        )
        title_html = panel_language_text_html(display_title, title_en)
        display_body = compact_preview_text(display_body_full, limit=MEMORY_BRIEF_BODY_LIMIT)
        raw_body = compact_preview_text(raw_body_full, limit=MEMORY_BRIEF_BODY_LIMIT)
        body_html = panel_language_variant_html(
            linkify_local_paths_html(display_body),
            linkify_local_paths_html(raw_body) if raw_body != display_body else "",
        )
        source_label = row_source_label(row)
        return """
          <article class="native-brief-card" data-native-kind="{kind}">
            <div class="native-brief-topline">
              <span>{kicker}</span>
              <span>{source_label}</span>
            </div>
            <h3>{title}</h3>
            <p>{body}</p>
            {meta}
            {keywords}
            {raw_details}
          </article>
        """.format(
            kind=escape(kind, quote=True),
            kicker=panel_language_text_html(
                kind_config.get("kicker_zh", "原生记忆"),
                kind_config.get("kicker_en", "Native Memory"),
            ),
            source_label=escape(source_label),
            title=title_html,
            body=body_html,
            meta=render_meta(row),
            keywords=render_keywords(row),
            raw_details=render_raw_details(raw_body_full, display_body_full, display_body),
        )

    visible_count = 4
    primary_cards = "".join(render_card(row, index) for index, row in enumerate(rows[:visible_count], start=1))
    extra_cards = "".join(
        render_card(row, index)
        for index, row in enumerate(rows[visible_count:], start=visible_count + 1)
    )
    return wrap_expandable_block(
        primary_cards,
        extra_cards,
        len(rows) - visible_count,
        "条",
        "native-brief-grid content-more-grid",
        expanded_label="收起额外条目",
        item_label_en="items",
        expanded_label_en="Collapse extra items",
    )


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
            english_count_phrase(session_count, "work memory", "work memories"),
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
        return {"final": "Full backfill", "preliminary": "30-minute quick backfill", "manual": "Manual run"}.get(stage, stage)
    return {"final": "完整回溯", "preliminary": "30 分钟快速回溯", "manual": "手动整理"}.get(stage, stage)


def build_daily_summary_view(nightly, window_overview=None, project_contexts=None, language=None):
    language = current_language(language)
    nightly = nightly or {}
    stage = nightly.get("stage", "")
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
                "工作记忆",
                "Work Memory",
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
    if stage == "preliminary":
        if is_current_local_date(nightly.get("date", "")):
            note_text_zh = "今天仍在进行中，当前是 30 分钟快速回溯结果；只保留窗口摘要和快速索引，次日完整回溯会再生成记忆。"
            note_text_en = "Today is still in progress, so this is only a 30-minute quick backfill; it keeps window summaries and a fast index, and the full backfill will generate memories tomorrow."
        else:
            note_text_zh = "当前是 30 分钟快速回溯结果，只保留窗口摘要和快速索引；可运行完整回溯生成可用记忆和完整总结。"
            note_text_en = "This is the 30-minute quick backfill, so it keeps only window summaries and a fast index; run the full backfill for usable memories and a complete summary."
    elif not nightly:
        note_text_zh = "当前还没有最近一次整理；生成后这里会自动切成摘要卡。"
        note_text_en = "No recent synthesis yet; this area will switch to a summary card after generation."
    elif not any(safe_int(item.get("value", 0)) for item in stats[1:]):
        note_text_zh = "还没有沉淀出记忆条目，先用窗口级概览帮助回看当天上下文。"
        note_text_en = "No memory items were captured yet; use the window overview to review that day's context."
    note_text = localized(note_text_zh, note_text_en, language)

    badges = []
    if stage == "preliminary":
        badges.append({"label": stage_display_label(stage, language=language), "tone": "amber"})
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
    if selected_date and selected_date not in dates:
        dates = [selected_date] + dates
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
        <span class="nightly-date-value" data-date-select-value>{selected_label}</span>
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
        selected_label=escape(display_date(selected_date)),
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
    selected_preliminary = current_view.get("stage") == "preliminary"
    selected_current_missing = (
        selected_missing and not current_view.get("available") and is_current_local_date(selected_date)
    )
    selected_current_preliminary = selected_preliminary and is_current_local_date(selected_date)
    if (selected_missing and not current_view.get("available")) or (
        selected_preliminary and not selected_current_preliminary
    ):
        backfill_panel_hidden = ""
    selected_backfill_command = backfill.get("commands_by_date", {}).get(
        selected_date,
        make_backfill_command(selected_date) if selected_date else "",
    )
    backfill_range_command = backfill.get("range_command", "")
    if selected_current_missing:
        selected_backfill_command = make_current_day_preview_command()
        backfill_range_command = ""
    if selected_current_preliminary:
        selected_backfill_command = ""
        backfill_range_command = ""
    backfill_range_hidden = (
        ""
        if backfill_range_command
        and backfill_range_command != selected_backfill_command
        and not selected_preliminary
        else " hidden"
    )
    if selected_current_missing:
        backfill_title = "今日仍在进行中"
        backfill_note = "今天还没结束，当前还没有 30 分钟快速回溯；可先运行今日快速回溯刷新面板，次日会自动生成完整回溯。"
        backfill_single_label = "30 分钟快速回溯"
    elif selected_current_preliminary:
        backfill_title = "今日仍在进行中"
        backfill_note = "今天还没结束，当前保留 30 分钟快速回溯；次日会自动生成完整回溯。"
        backfill_single_label = "30 分钟快速回溯"
    elif selected_preliminary:
        backfill_title = "建议深度回溯"
        backfill_note = "当前是 30 分钟快速回溯，只生成窗口摘要和快速索引，不做记忆沉淀。可以复制命令在终端补跑完整回溯。首次安装后，会自动触发完整回溯，请耐心等待。"
        backfill_single_label = "完整回溯"
    else:
        backfill_title = "缺少整理结果"
        backfill_note = "该日期还没有整理结果。可以复制命令在终端手动回溯。"
        backfill_single_label = "单日回溯"
    backfill_panel = """
          <div class="nightly-backfill" id="nightly-backfill-panel"{hidden}>
            <div class="nightly-backfill-title" id="nightly-backfill-title">{title}</div>
            <p class="nightly-backfill-note" id="nightly-backfill-note">{note}</p>
            <div class="nightly-backfill-command">
              <div class="nightly-backfill-label" id="nightly-backfill-single-label">{single_label}</div>
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
        title=escape(backfill_title),
        note=escape(backfill_note),
        single_label=escape(backfill_single_label),
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
                compact_preview_text(item.get("text", ""), strip_markdown=False),
                language=language,
                keywords=keywords,
                label=label,
            )
            text_html = render_markdown_text(text) or "<p>{}</p>".format(escape(text))
            rows.append(
                """
                <li class="window-detail-item">
                  {time_html}
                  <div class="window-markdown window-detail-markdown">{text}</div>
                </li>
                """.format(
                    time_html=time_html,
                    text=text_html,
                )
            )
        return "".join(rows)

    def render_resume_actions(
        resume_command,
        resume_url,
        review_prompt_target,
        resume_app_action="",
        resume_app_session_id="",
        codex_home="",
        codex_electron_user_data_path="",
    ):
        copy_button = ""
        if resume_command:
            copy_button = """
          <button
            type="button"
            class="window-resume-button"
            data-window-resume-copy
            data-resume-command="{resume_command}"
            data-label="{copy_label}"
            data-copied-label="{copied_label}"
            data-error-label="{copy_error_label}"
          >{copy_label}</button>""".format(
                resume_command=escape(resume_command, quote=True),
                copy_label=escape(localized("复制 resume 命令", "Copy resume command", language), quote=True),
                copied_label=escape(localized("已复制", "Copied", language), quote=True),
                copy_error_label=escape(localized("复制失败", "Copy failed", language), quote=True),
            )
        open_button = ""
        if resume_command and resume_url:
            codex_profile_scoped = bool(
                str(codex_home or "").strip()
                or str(codex_electron_user_data_path or "").strip()
            )
            codex_system_profile = is_system_codex_profile(
                codex_home,
                codex_electron_user_data_path,
            )
            open_label = localized("在 Codex App 打开", "Open in Codex App", language)
            opened_label = localized("已发送", "Sent", language)
            title_label = localized(
                "用系统 Codex deeplink 打开对应线程",
                "Open the matching thread through the system Codex deeplink",
                language,
            )
            copy_resume_on_switch = ""
            focused_copied_label = opened_label
            if codex_profile_scoped and not codex_system_profile:
                open_label = localized("打开 Codex App", "Open Codex App", language)
                opened_label = localized("已打开", "Opened", language)
                focused_copied_label = localized(
                    "已打开，命令已复制",
                    "Opened, command copied",
                    language,
                )
                title_label = localized(
                    "打开或切到对应 Codex profile，并复制 resume 命令用于精确恢复",
                    "Open or switch to the matching Codex profile and copy the resume command for exact restore",
                    language,
                )
                copy_resume_on_switch = "1"
            open_button = """
          <button
            type="button"
            class="window-resume-button is-secondary"
            data-window-resume-open
            data-codex-url="{resume_url}"
            data-codex-resume-id="{resume_app_session_id}"
            data-codex-home="{codex_home}"
            data-codex-electron-user-data-path="{codex_electron_user_data_path}"
            data-codex-system-profile="{codex_system_profile}"
            data-resume-command="{resume_command}"
            data-copy-resume-on-switch="{copy_resume_on_switch}"
            data-label="{open_label}"
            data-opening-label="{opening_label}"
            data-opened-label="{opened_label}"
            data-focused-copied-label="{focused_copied_label}"
            data-error-label="{error_label}"
            title="{title_label}"
          >{open_label}</button>""".format(
                resume_url=escape(resume_url, quote=True),
                resume_app_session_id=escape(resume_app_session_id, quote=True),
                codex_home=escape(codex_home, quote=True),
                codex_electron_user_data_path=escape(codex_electron_user_data_path, quote=True),
                codex_system_profile="1" if codex_system_profile else "",
                resume_command=escape(resume_command, quote=True),
                copy_resume_on_switch=escape(copy_resume_on_switch, quote=True),
                open_label=escape(open_label, quote=True),
                opening_label=escape(localized("正在打开", "Opening", language), quote=True),
                opened_label=escape(opened_label, quote=True),
                focused_copied_label=escape(focused_copied_label, quote=True),
                error_label=escape(localized("打开失败", "Open failed", language), quote=True),
                title_label=escape(title_label, quote=True),
            )
        elif resume_command and resume_app_action == "claude_desktop" and resume_app_session_id:
            open_button = """
          <button
            type="button"
            class="window-resume-button is-secondary"
            data-window-resume-claude-desktop
            data-claude-resume-id="{resume_app_session_id}"
            data-label="{open_label}"
            data-opening-label="{opening_label}"
            data-opened-label="{opened_label}"
            data-error-label="{error_label}"
          >{open_label}</button>""".format(
                resume_app_session_id=escape(resume_app_session_id, quote=True),
                open_label=escape(localized("在 Claude App 打开", "Open in Claude App", language), quote=True),
                opening_label=escape(localized("正在打开", "Opening", language), quote=True),
                opened_label=escape(localized("已发送", "Sent", language), quote=True),
                error_label=escape(localized("打开失败", "Open failed", language), quote=True),
            )
        review_button = ""
        if review_prompt_target:
            review_button = """
          <button
            type="button"
            class="window-resume-button is-review"
            data-window-review-copy
            data-review-prompt-target="{review_prompt_target}"
            data-label="{review_label}"
            data-copied-label="{review_copied_label}"
            data-error-label="{review_error_label}"
          >{review_label}</button>""".format(
                review_prompt_target=escape(review_prompt_target, quote=True),
                review_label=escape(localized("发起复盘", "Start review", language), quote=True),
                review_copied_label=escape(localized("复盘指令已复制", "Review prompt copied", language), quote=True),
                review_error_label=escape(localized("复制失败", "Copy failed", language), quote=True),
            )
        if not copy_button and not open_button and not review_button:
            return ""
        return """
        <div class="window-resume-actions">
          {copy_button}
          {open_button}
          {review_button}
        </div>
        """.format(
            copy_button=copy_button,
            open_button=open_button,
            review_button=review_button,
        )

    def review_prompt_context_text(text, limit):
        value = compact_preview_text(text, limit=limit, strip_markdown=False)
        return re.sub(
            r"(问题|结论)([0-9一二三四五六七八九十]+)\s*[:：]\s*",
            r"\1 \2 - ",
            value,
        )

    def build_window_review_prompt(
        project_label,
        ai_host_label,
        window_date,
        window_id,
        cwd_raw,
        cwd_display,
        question_summary_display,
        conclusion_summary_display,
        resume_command,
    ):
        question_text = review_prompt_context_text(question_summary_display, limit=420)
        conclusion_text = review_prompt_context_text(conclusion_summary_display, limit=520)
        cwd_text = cwd_raw or cwd_display
        if is_english(language):
            lines = [
                "/memory-review",
                "",
                "Review this source window and prioritize turning it into a reusable asset, not just a log entry.",
                "Decide whether it should become a playbook, skill, template, automation, or no asset.",
                "- Date: {}".format(window_date or "unknown"),
                "- Source: {} / {}".format(project_label or "unknown project", ai_host_label or "unknown host"),
                "- Window ID: {}".format(window_id or "unknown"),
            ]
            if cwd_text:
                lines.append("- CWD: {}".format(cwd_text))
            if resume_command:
                lines.append("- Resume command: {}".format(resume_command))
            if question_text:
                lines.append("- Representative question: {}".format(question_text))
            if conclusion_text:
                lines.append("- Representative conclusion: {}".format(conclusion_text))
            lines.extend(
                [
                    "",
                    "Output the review path, asset decision, and any asset registry or skill draft changes.",
                ]
            )
        else:
            lines = [
                "/memory-review",
                "",
                "请基于这个来源窗口发起人工复盘，重点判断能否沉淀为可复用资产，而不是只记录日志。",
                "请明确它应该成为 playbook、skill、template、automation，还是不沉淀资产。",
                "- 日期：{}".format(window_date or "未知"),
                "- 来源：{} / {}".format(project_label or "未知项目", ai_host_label or "未知 host"),
                "- 原始窗口 ID：{}".format(window_id or "未知"),
            ]
            if cwd_text:
                lines.append("- 当前目录：{}".format(cwd_text))
            if resume_command:
                lines.append("- resume 命令：{}".format(resume_command))
            if question_text:
                lines.append("- 代表问题 - {}".format(question_text))
            if conclusion_text:
                lines.append("- 代表结论 - {}".format(conclusion_text))
            lines.extend(
                [
                    "",
                    "请输出复盘文件路径、资产化结论，以及新增/更新的 asset registry 或 skill 草稿。",
                ]
            )
        return "\n".join(lines)

    def strip_preview_prefix(text):
        return re.sub(
            r"^\s*(?:\*\*)?(问题|结论|Question|Conclusion|Focus|Takeaway)(?:\*\*)?\s*[:：]\s*",
            "",
            str(text or "").strip(),
            flags=re.IGNORECASE,
        )

    def split_labeled_summary(text, labels):
        raw = str(text or "").strip()
        if not raw:
            return []
        label_pattern = "|".join(re.escape(label) for label in labels)
        marker_pattern = re.compile(
            r"(?:^|[\n;；])\s*(?:[-*]\s*)?(?:{})\s*([0-9一二三四五六七八九十]*)\s*[:：]\s*".format(
                label_pattern
            ),
            flags=re.IGNORECASE,
        )
        matches = list(marker_pattern.finditer(raw))
        if not matches:
            return [strip_preview_prefix(raw)]
        entries = []
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
            value = raw[start:end].strip(" ；;\n")
            if value:
                entries.append(strip_preview_prefix(value))
        return entries

    def normalize_summary_pairs(item, question_summary, main_takeaway):
        raw_pairs = item.get("summary_pairs") or item.get("question_conclusion_pairs") or []
        pairs = []
        if isinstance(raw_pairs, list):
            for raw_pair in raw_pairs:
                if not isinstance(raw_pair, dict):
                    continue
                question = strip_preview_prefix(raw_pair.get("question", "") or raw_pair.get("problem", ""))
                conclusion = strip_preview_prefix(raw_pair.get("conclusion", "") or raw_pair.get("takeaway", ""))
                if question or conclusion:
                    pairs.append({"question": question, "conclusion": conclusion})
        if pairs:
            return pairs

        question_items = split_labeled_summary(
            question_summary,
            ["问题", "Question", "Focus"],
        )
        conclusion_items = split_labeled_summary(
            main_takeaway,
            ["结论", "Conclusion", "Takeaway"],
        )
        if not question_items and question_summary:
            question_items = [strip_preview_prefix(question_summary)]
        if not conclusion_items and main_takeaway:
            conclusion_items = [strip_preview_prefix(main_takeaway)]
        row_count = max(len(question_items), len(conclusion_items), 1)
        for index in range(row_count):
            question = question_items[index] if index < len(question_items) else ""
            conclusion = conclusion_items[index] if index < len(conclusion_items) else ""
            pairs.append({"question": question, "conclusion": conclusion})
        return pairs

    def summary_pair_label(label):
        if label == "问题":
            return localized("问题", "Question", language)
        if label == "结论":
            return localized("结论", "Conclusion", language)
        return localized(label, label, language)

    def numbered_text(items, label):
        values = [str(item or "").strip() for item in items if str(item or "").strip()]
        if not values:
            return ""
        if len(values) == 1:
            return values[0]
        separator = "; " if is_english(language) else "；"
        label_text = summary_pair_label(label)
        return separator.join(
            "{}{}{}{}".format(
                label_text,
                " {}".format(index) if is_english(language) else index,
                ": " if is_english(language) else "：",
                value,
            )
            for index, value in enumerate(values, 1)
        )

    def indexed_pair_label(label, index, total):
        label_text = summary_pair_label(label)
        if total <= 1:
            return label_text
        if is_english(language):
            return "{} {}".format(label_text, index)
        return "{}{}".format(label_text, index)

    def strip_pair_boundary_punctuation(text):
        return re.sub(r"\s*[。.!?！？；;:：]+\s*$", "", str(text or "").strip())

    def render_pair_preview_row(label, text):
        text = str(text or "").strip()
        if not text:
            return ""
        text_html = render_markdown_inline(
            compact_preview_text(text, limit=320, strip_markdown=False)
        ) or escape(compact_preview_text(text, limit=320, strip_markdown=False))
        return """
                  <div class="window-card-pair-row">
                    <span class="window-card-pair-label">{label}</span>
                    <span class="window-card-pair-body">{text}</span>
                  </div>
        """.format(
            label=escape(label),
            text=text_html,
        )

    def render_pair_preview(pairs, hide_single_question=False):
        if not pairs:
            return ""
        pair = pairs[0]
        total = len(pairs)
        question = strip_pair_boundary_punctuation(pair.get("question", ""))
        conclusion = str(pair.get("conclusion", "") or "").strip()
        rows = []
        if not (hide_single_question and total == 1):
            rows.append(render_pair_preview_row(indexed_pair_label("问题", 1, total), question))
        rows.append(render_pair_preview_row(indexed_pair_label("结论", 1, total), conclusion))
        rows = [row for row in rows if row]
        if not rows:
            return ""
        return '<div class="window-card-pair-preview">{}</div>'.format("".join(rows))

    def render_summary_pair_timeline(pairs):
        if not pairs:
            pairs = [
                {
                    "question": localized("暂无问题。", "No question.", language),
                    "conclusion": localized("暂无结论。", "No conclusion.", language),
                }
            ]
        rows = []
        for index, pair in enumerate(pairs, 1):
            question = str(pair.get("question", "") or "").strip()
            conclusion = str(pair.get("conclusion", "") or "").strip()
            question_html = render_markdown_text(question) or "<p>{}</p>".format(
                escape(question or localized("暂无问题。", "No question.", language))
            )
            conclusion_html = render_markdown_text(conclusion) or "<p>{}</p>".format(
                escape(conclusion or localized("暂无结论。", "No conclusion.", language))
            )
            pair_count = len(pairs)
            question_label = indexed_pair_label("问题", index, pair_count)
            conclusion_label = indexed_pair_label("结论", index, pair_count)
            rows.append(
                """
                <li class="window-summary-pair-item">
                  <div class="window-summary-pair-row is-question">
                    <span class="window-summary-index">{question_label}</span>
                    <div class="window-markdown window-summary-question">{question}</div>
                  </div>
                  <div class="window-summary-pair-row is-conclusion">
                    <span class="window-summary-index">{conclusion_label}</span>
                    <div class="window-markdown window-summary-conclusion">{conclusion}</div>
                  </div>
                </li>
                """.format(
                    question_label=escape(question_label),
                    conclusion_label=escape(conclusion_label),
                    question=question_html,
                    conclusion=conclusion_html,
                )
            )
        return "".join(rows)

    def render_summary_mode_panel(pairs, mode):
        return """
                <div class="window-summary-mode-panel is-{mode}" data-summary-panel="{mode}">
                  <ol class="window-summary-pair-list">
                    {timeline}
                  </ol>
                </div>
        """.format(
            mode=escape(mode, quote=True),
            timeline=render_summary_pair_timeline(pairs),
        )

    def render_summary_mode_controls(has_raw_pairs, summary_status):
        if not has_raw_pairs:
            return ""
        ai_label = (
            localized("快速整理", "Quick summary", language)
            if summary_status == "lightweight"
            else localized("智能整理", "AI summary", language)
        )
        return """
                  <div class="window-summary-mode-controls" role="group" aria-label="{aria_label}">
                    <button type="button" class="window-summary-mode-button" data-window-summary-mode="ai" aria-pressed="true">{ai_label}</button>
                    <button type="button" class="window-summary-mode-button" data-window-summary-mode="raw" aria-pressed="false">{raw_label}</button>
                  </div>
        """.format(
            aria_label=escape(localized("切换问答视图", "Switch question and conclusion view", language), quote=True),
            ai_label=escape(ai_label),
            raw_label=escape(localized("原始信息", "Raw info", language)),
        )

    cards = []
    for card_index, item in enumerate(window_overview.get("windows", []), 1):
        cwd_raw = item.get("cwd", "")
        window_id = item.get("window_id", "")
        ai_host = str(item.get("ai_host") or "codex").strip().lower()
        if ai_host not in {"codex", "claude"}:
            ai_host = "codex"
        ai_host_label = item.get("ai_host_label") or window_host_label(ai_host, language=language)
        window_id_display = window_id or localized("暂无", "None", language)
        anchor_id = build_window_anchor_id(window_id)
        card_dom_id = anchor_id or "window-card-{}".format(card_index)
        review_prompt_id = "{}-review-prompt".format(card_dom_id)
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
        summary_status = str(item.get("summary_status", "") or "summarized")
        if summary_status == "raw_fallback":
            summary_status_label = localized(
                "暂未做二次学习和总结，当前展示原始问题和结论",
                "Not AI-organized yet; showing raw questions and conclusions",
                language,
            )
        elif summary_status == "summarized":
            summary_status_label = localized("大模型已做智能整理", "AI-organized", language)
        elif summary_status == "lightweight":
            summary_status_label = localized(
                "轻度回溯快速整理，未做大模型总结",
                "Quick lightweight organization; no AI model summary yet",
                language,
            )
        else:
            summary_status_label = str(item.get("summary_status_label", "") or "").strip()
        summary_status_html = ""
        if summary_status_label:
            if summary_status == "summarized":
                status_class = "is-ai"
            elif summary_status == "lightweight":
                status_class = "is-lightweight"
            else:
                status_class = "is-raw"
            summary_status_html = """
                    <div class="window-card-status {status_class}" data-summary-status="{summary_status}">{summary_status_label}</div>
            """.format(
                status_class=escape(status_class, quote=True),
                summary_status=escape(summary_status, quote=True),
                summary_status_label=escape(summary_status_label)
            )
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
        summary_pairs = normalize_summary_pairs(item, question_summary, main_takeaway)
        raw_summary_pairs = normalize_summary_pairs(
            {"summary_pairs": item.get("raw_summary_pairs", [])},
            "",
            "",
        )
        question_summary_display = numbered_text(
            [pair.get("question", "") for pair in summary_pairs],
            "问题",
        )
        conclusion_summary_display = numbered_text(
            [pair.get("conclusion", "") for pair in summary_pairs],
            "结论",
        )
        title_source = item.get("window_title", "")
        if summary_status == "raw_fallback":
            title_source = summary_pairs[0].get("question", "") if summary_pairs else question_summary_display
        window_summary = normalize_brand_display_text(
            compact_preview_text(title_source, limit=100)
        )
        if not window_summary:
            window_summary = normalize_brand_display_text(
                item.get("window_summary", "")
                or item.get("thread_title", "")
                or item.get("title", "")
                or localized("未捕获窗口摘要", "No captured window summary", language)
            )
        resume_id = item.get("resume_id", "") or window_id
        resume_command = item.get("resume_command", "") or window_resume_command(
            ai_host,
            resume_id,
            codex_home=item.get("codex_home", ""),
        )
        resume_url = item.get("resume_url", "") or (codex_resume_url(resume_id) if ai_host == "codex" else "")
        resume_app_action = item.get("resume_app_action", "") or claude_desktop_resume_action(ai_host, resume_id)
        resume_app_session_id = item.get("resume_app_session_id", "") or (resume_id if (resume_app_action or ai_host == "codex") else "")
        review_prompt = build_window_review_prompt(
            project_label,
            ai_host_label,
            window_date,
            window_id,
            cwd_raw,
            cwd_display,
            question_summary_display,
            conclusion_summary_display,
            resume_command,
        )
        resume_actions = render_resume_actions(
            resume_command,
            resume_url,
            review_prompt_id if review_prompt else "",
            resume_app_action=resume_app_action,
            resume_app_session_id=resume_app_session_id,
            codex_home=item.get("codex_home", ""),
            codex_electron_user_data_path=item.get("codex_electron_user_data_path", ""),
        )
        review_prompt_template = ""
        if review_prompt:
            review_prompt_template = """
              <template id="{review_prompt_id}" data-window-review-prompt>{review_prompt}</template>
            """.format(
                review_prompt_id=escape(review_prompt_id, quote=True),
                review_prompt=escape(review_prompt),
            )
        question_count = safe_int(item.get("question_count", len(summary_pairs)))
        if question_count <= 0:
            question_count = len([pair for pair in summary_pairs if str(pair.get("question", "") or "").strip()])
        hide_single_summary_question = (
            summary_status == "summarized"
            and question_count == 1
            and len(summary_pairs) == 1
        )
        main_takeaway_preview_html = render_pair_preview(
            summary_pairs,
            hide_single_question=hide_single_summary_question,
        )
        if not main_takeaway_preview_html and conclusion_summary_display:
            fallback_takeaway = render_markdown_inline(
                compact_preview_text(conclusion_summary_display, limit=360, strip_markdown=False)
            ) or escape(compact_preview_text(conclusion_summary_display, limit=360, strip_markdown=False))
            main_takeaway_preview_html = '<div class="window-card-pair-preview"><div class="window-card-pair-row"><span class="window-card-pair-body">{}</span></div></div>'.format(
                fallback_takeaway
            )
        show_raw_toggle = summary_status in {"summarized", "lightweight"} and bool(raw_summary_pairs)
        initial_summary_mode = "raw" if summary_status == "raw_fallback" else "ai"
        summary_mode_controls_html = render_summary_mode_controls(show_raw_toggle, summary_status)
        summary_mode_panels_html = render_summary_mode_panel(summary_pairs, initial_summary_mode)
        if show_raw_toggle:
            summary_mode_panels_html += render_summary_mode_panel(raw_summary_pairs, "raw")
        raw_window = load_window_record(window_date, window_id)
        raw_window_html = escape(localized("暂无", "None", language))
        raw_window_link_html = ""
        if raw_window and raw_window.get("_path"):
            raw_window_link_html = render_local_path_link(
                raw_window.get("_path", ""),
                label=localized("原始窗口 JSON", "Raw Window JSON", language),
            )
            raw_window_html = raw_window_link_html
        cwd_detail_label = cwd_raw or cwd_display
        cwd_detail_html = render_local_path_link(cwd_raw, label=cwd_detail_label)
        raw_window_source_html = ""
        if raw_window_link_html:
            raw_window_source_html = """
                    <p class="window-detail-source">{raw_record_label} {raw_window_html}</p>
            """.format(
                raw_record_label=escape(localized("原始记录见", "Raw records in", language)),
                raw_window_html=raw_window_html,
            )
        cards.append(
            """
            <details class="window-card" id="{anchor_id}">
              <summary class="window-card-trigger">
                <div class="window-card-head">
                  <div class="window-card-copy">
                    <div class="window-card-label">{project_label} · {ai_host_label} · {window_label}{window_id_separator}{window_id_full}</div>
                    <h3 class="window-card-window-summary">{window_summary}</h3>
                    <div class="window-card-subline">
                      <span class="window-card-path">{activity_source_label}</span>
                      <span class="window-card-cwd">{cwd_label} {cwd_detail_html}</span>
                    </div>
                    {summary_status_html}
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
                <div class="window-card-takeaway window-markdown">
                  {main_takeaway_preview}
                </div>
                <div class="window-card-meta">
                  <span class="window-card-time">{recent_activity} {latest_activity}</span>
                  {resume_actions}
                  <span class="window-card-action">
                    <span class="window-card-action-collapsed">{open_details}</span>
                    <span class="window-card-action-expanded">{collapse_details}</span>
                  </span>
                </div>
                <div class="window-card-keywords">
                  <div class="window-card-summary-label">{keywords_label}</div>
                  <div class="window-keyword-row">
                    {keyword_chips}
                  </div>
                </div>
              </summary>
              {review_prompt_template}
              <div class="window-card-detail">
                <div class="window-summary-mode-root" data-summary-mode="{summary_mode}">
                  <div class="window-summary-mode-head">
                    <div class="window-detail-label">{pair_detail_label}</div>
                    {summary_mode_controls}
                  </div>
                  {summary_mode_panels}
                </div>
                {raw_window_source_html}
              </div>
            </details>
            """.format(
                anchor_id=escape(card_dom_id, quote=True),
                window_summary=escape(window_summary),
                window_label=escape(localized("原始窗口 ID", "Raw Window ID", language)),
                window_id_separator=escape(localized("：", ": ", language)),
                window_id_full=escape(window_id_display),
                project_label=escape(project_label),
                ai_host_label=escape(ai_host_label),
                activity_source_label=escape(activity_source_label),
                cwd_detail_html=cwd_detail_html,
                summary_status_html=summary_status_html,
                question_count=escape(str(item.get("question_count", 0))),
                conclusion_count=escape(str(item.get("conclusion_count", 0))),
                questions_label=escape(localized("问题", "Questions", language)),
                conclusions_label=escape(localized("结论", "Conclusions", language)),
                recent_activity=escape(localized("最近活动", "Recent activity", language)),
                latest_activity=escape(item.get("latest_activity_display", localized("时间未知", "Unknown time", language))),
                started_at=escape(item.get("started_at_display", localized("时间未知", "Unknown time", language))),
                raw_window_html=raw_window_html,
                resume_actions=resume_actions,
                review_prompt_template=review_prompt_template,
                main_takeaway_preview=main_takeaway_preview_html,
                keyword_chips=render_keyword_chips(item.get("keywords", [])),
                summary_mode=initial_summary_mode,
                summary_mode_controls=summary_mode_controls_html,
                summary_mode_panels=summary_mode_panels_html,
                raw_window_source_html=raw_window_source_html,
                open_details=escape(localized("点开看详情", "Open details", language)),
                collapse_details=escape(localized("收起详情", "Collapse details", language)),
                cwd_label=escape(localized("当前目录", "Current Directory", language)),
                keywords_label=escape(localized("关键词", "Keywords", language)),
                pair_detail_label=escape(localized("问题与结论", "Question / Conclusion", language)),
            )
        )
    return "".join(cards)


def pipeline_status_label(status, language=None):
    labels = {
        "running": ("运行中", "Running"),
        "completed": ("已完成", "Completed"),
        "failed": ("失败", "Failed"),
        "idle": ("空闲", "Idle"),
    }
    zh, en = labels.get(str(status or "idle"), (str(status or "idle"), str(status or "idle")))
    return localized(zh, en, language)


def pipeline_status_time_label(payload, language=None):
    status = str((payload or {}).get("status") or "idle")
    if status == "running":
        started = (payload or {}).get("started_at_iso", "")
        return localized("开始于 {}".format(started or "—"), "Started {}".format(started or "—"), language)
    ended = (payload or {}).get("ended_at_iso", "")
    if ended:
        return localized("结束于 {}".format(ended), "Ended {}".format(ended), language)
    return localized("等待运行", "Waiting", language)


def pipeline_history_time_display(value):
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    display = display_local_datetime(normalized)
    if not display:
        return text
    return display[5:]


def pipeline_history_target_label(row, language=None):
    target_date = str((row or {}).get("target_date") or "").strip()
    stage = str((row or {}).get("stage") or "").strip()
    parts = []
    if target_date:
        parts.append(localized("日期 {}".format(target_date), "Date {}".format(target_date), language))
    if stage:
        parts.append(stage_display_label(stage, language=language))
    return " · ".join(parts)


def pipeline_history_meta_labels(row, language=None):
    row = row or {}
    status = row.get("status") or "idle"
    target_label = pipeline_history_target_label(row, language=language)
    started = pipeline_history_time_display(row.get("started_at_iso"))
    ended = pipeline_history_time_display(row.get("ended_at_iso"))
    labels = [pipeline_status_label(status, language=language)]
    labels.append(target_label or localized("日期 —", "Date —", language))
    labels.append(localized("触发 {}".format(started or "—"), "Started {}".format(started or "—"), language))
    labels.append(localized("结束 {}".format(ended or "—"), "Ended {}".format(ended or "—"), language))
    return labels


def pipeline_target_label(payload, language=None):
    payload = payload or {}
    target_bits = [
        payload.get("target_date", ""),
        stage_display_label(payload.get("stage", ""), language=language) if payload.get("stage") else "",
    ]
    return " · ".join(item for item in target_bits if item) or pipeline_status_time_label(payload, language=language)


def make_pipeline_step_track(steps):
    if not steps:
        return '<div class="pipeline-empty">{}</div>'.format(
            panel_language_text_html("暂无 pipeline 步骤。", "No pipeline steps yet.")
        )
    rows = []
    for step in steps:
        status = escape(str(step.get("status") or "pending"), quote=True)
        label = step.get("label") or step.get("key") or ""
        label_en = step.get("label_en") or label
        rows.append(
            """
            <div class="pipeline-step" data-step-status="{status}">
              <span class="pipeline-step-dot" aria-hidden="true"></span>
              <span class="pipeline-step-label">{label}</span>
            </div>
            """.format(
                status=status,
                label=panel_language_text_html(label, label_en),
            )
        )
    return "".join(rows)


def make_pipeline_recent_runs(rows):
    if not rows:
        return '<div class="pipeline-empty">{}</div>'.format(
            panel_language_text_html("暂无近期运行记录。", "No recent runs yet.")
        )
    rendered = []
    for row in rows[:4]:
        title = row.get("title") or row.get("pipeline") or ""
        title_en = row.get("title_en") or title
        status = row.get("status") or "idle"
        meta_html = "".join(
            '<span class="pipeline-history-meta">{}</span>'.format(
                panel_language_text_html(zh_label, en_label)
            )
            for zh_label, en_label in zip(
                pipeline_history_meta_labels(row, language="zh"),
                pipeline_history_meta_labels(row, language="en"),
            )
        )
        rendered.append(
            """
            <div class="pipeline-history-row" data-status="{status}">
              <span class="pipeline-history-title">{title}</span>
              <span class="pipeline-history-meta-list">{meta}</span>
            </div>
            """.format(
                status=escape(str(status), quote=True),
                title=panel_language_text_html(title, title_en),
                meta=meta_html,
            )
        )
    return "".join(rendered)


def make_pipeline_status_panel(status_payload, help_html=""):
    payload = status_payload or {}
    status = str(payload.get("status") or "idle")
    title = payload.get("title") or "OpenRelix Pipeline"
    title_en = payload.get("title_en") or title
    message = payload.get("message") or "暂无正在运行的任务。"
    message_en = payload.get("message_en") or "No active task."
    failure_hint = payload.get("failure_hint") or ""
    failure_hint_en = payload.get("failure_hint_en") or failure_hint
    target_label = pipeline_target_label(payload, language="zh")
    target_label_en = pipeline_target_label(payload, language="en")
    step_index = safe_int(payload.get("current_step_index", 0))
    step_count = safe_int(payload.get("step_count", 0))
    progress_label = "—"
    if step_count:
        progress_label = "{}/{}".format(max(step_index, 1), step_count)
    next_run = payload.get("next_run") or {}
    next_title = next_run.get("title") or "暂无计划任务"
    next_title_en = next_run.get("title_en") or "No scheduled task"
    next_time = next_run.get("next_at_iso") or ""
    next_stage = next_run.get("stage") or ""
    next_stage_label = stage_display_label(next_stage, language="zh") if next_stage else ""
    next_stage_label_en = stage_display_label(next_stage, language="en") if next_stage else ""
    next_meta_parts_zh = [next_time, next_stage_label]
    next_meta_parts_en = [next_time, next_stage_label_en]
    if next_run.get("learn_memory"):
        next_meta_parts_zh.append("含学习刷新")
        next_meta_parts_en.append("includes learning refresh")
        learn_window_days = safe_int(next_run.get("learn_window_days", 0))
        if learn_window_days:
            next_meta_parts_zh.append("{} 天窗口".format(learn_window_days))
            next_meta_parts_en.append("{}-day window".format(learn_window_days))
    next_meta = " · ".join(item for item in next_meta_parts_zh if item) or "—"
    next_meta_en = " · ".join(item for item in next_meta_parts_en if item) or "—"
    return """
    <section class="panel pipeline-panel" id="pipeline-section" data-pipeline-status="{status}">
      {header}
      <div class="pipeline-live-card">
        <div class="pipeline-live-main">
          <span class="pipeline-live-dot" aria-hidden="true"></span>
          <div class="pipeline-live-copy">
            <div class="pipeline-live-title" id="pipeline-live-title">{title}</div>
            <div class="pipeline-live-message" id="pipeline-live-message">{message}</div>
            <div class="pipeline-failure-hint" id="pipeline-failure-hint">{failure_hint}</div>
          </div>
        </div>
        <div class="pipeline-live-meta">
          <span id="pipeline-live-state">{state}</span>
          <span id="pipeline-live-target">{target}</span>
          <span id="pipeline-live-progress">{progress}</span>
        </div>
      </div>
      <div class="pipeline-next-row">
        <div class="pipeline-next-copy">
          <span class="pipeline-next-label">{next_label}</span>
          <strong id="pipeline-next-title">{next_title}</strong>
          <span id="pipeline-next-time">{next_time}</span>
        </div>
        <div class="pipeline-actions">
          <button class="action-button pipeline-run-button" type="button" id="pipeline-run-now-button">
            <span class="button-spinner" aria-hidden="true"></span>
            <span id="pipeline-run-now-label">{run_label}</span>
          </button>
          <span class="asset-refresh-status pipeline-run-status" id="pipeline-run-now-status" role="status" aria-live="polite"></span>
        </div>
      </div>
      <div class="pipeline-step-track" id="pipeline-step-track">
        {steps}
      </div>
      <div class="pipeline-history" id="pipeline-history">
        {history}
      </div>
    </section>
    """.format(
        status=escape(status, quote=True),
        header=make_panel_header(
            "当前运行内容",
            "展示最近一次 OpenRelix pipeline 的实时阶段",
            help_html=help_html,
        ),
        title=panel_language_text_html(title, title_en),
        message=panel_language_text_html(message, message_en),
        failure_hint=panel_language_text_html(failure_hint, failure_hint_en) if failure_hint else "",
        state=panel_language_text_html(
            pipeline_status_label(status, language="zh"),
            pipeline_status_label(status, language="en"),
        ),
        target=panel_language_text_html(target_label, target_label_en),
        progress=escape(progress_label),
        next_label=panel_language_text_html("下一次运行", "Next Run"),
        next_title=panel_language_text_html(next_title, next_title_en),
        next_time=panel_language_text_html(next_meta, next_meta_en),
        run_label=panel_language_text_html("立即运行", "Run Now"),
        steps=make_pipeline_step_track(payload.get("steps", [])),
        history=make_pipeline_recent_runs(payload.get("recent_runs", [])),
    )


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
    dates = set(list_daily_capture_dates()) | set(list_codex_history_dates())
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


def ensure_window_overview_view(window_views, window_overview, selected_date="", language=None):
    views = list(window_views or [])
    window_overview = window_overview or {}
    date_str = window_overview.get("date", "") or selected_date
    if not date_str or not window_overview.get("windows"):
        return views
    if any(view.get("date") == date_str for view in views):
        return views
    view_source = dict(window_overview)
    view_source["date"] = date_str
    views.insert(0, build_window_overview_view(view_source))
    return views


def build_metric_help_sections(metric):
    key = metric.get("key", "")
    caption = metric.get("caption", "")
    meta = metric.get("meta", "")

    help_map = {
        "total_assets": [
            {
                "label": "统计口径",
                "body": {
                    "zh": "仅统计 OpenRelix 登记册里的稳定资产（state root 下的 registry/assets.jsonl），不含本机扫描出的现有资产；后者请看「已发现资产」。",
                    "en": "Counts only stable assets in the OpenRelix registry (registry/assets.jsonl under the state root). It excludes existing assets scanned from this machine; see Discovered Assets for those.",
                },
            },
            {
                "label": "如何登记",
                "body": {
                    "zh": "通常通过 /memory-review 或 memory-review 工作流在复盘时写入 registry/assets.jsonl；也可以直接维护这个 JSONL 登记册。单纯新增 SKILL.md 不会进入这里。",
                    "en": "Usually written through the /memory-review or memory-review workflow during task review; you can also maintain registry/assets.jsonl directly. Adding a SKILL.md alone does not enter this registry.",
                },
            },
            {
                "label": "不包含",
                "body": {
                    "zh": "新增的 skills、raw 对话、日志、报表，以及还没写入登记册的临时内容。",
                    "en": "Newly added skills, raw conversations, logs, reports, and temporary content that has not been written to the registry.",
                },
            },
        ],
        "discovered_assets": [
            {
                "label": "统计什么",
                "body": {
                    "zh": "从本机扫描到的 Codex / Claude skills、提示词、规则、插件、启动项，以及近 30 天里真实读取过的项目内 / 跨仓库 skills。",
                    "en": "Codex / Claude skills, prompts, rules, plugins, launch agents scanned from this machine, plus project-local and external-repo skills actually read in the last 30 days.",
                },
            },
            {
                "label": "怎么算",
                "body": {
                    "zh": "项目本地 skills 和跨仓库 skills 仅在 30 天内至少 2 个会话读取过 SKILL.md 时显示。",
                    "en": "Project-local and external-repo skills are shown only when at least 2 sessions read their SKILL.md in the last 30 days.",
                },
            },
        ],
        "active_assets": [
            {
                "label": "统计什么",
                "body": {
                    "zh": "OpenRelix 登记册中 status = active 的条目数量。",
                    "en": "Number of OpenRelix registry rows where status = active.",
                },
            },
            {
                "label": "和已发现资产的关系",
                "body": {
                    "zh": "它是 registry/assets.jsonl 的子集；自动扫描发现的 skills 不会自动进入这里。",
                    "en": "It is a subset of registry/assets.jsonl; automatically discovered skills do not enter this count by themselves.",
                },
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
        "repo_scoped_assets": [
            {
                "label": "统计什么",
                "body": {
                    "zh": "OpenRelix 登记册中 scope = repo 的条目数量。",
                    "en": "Number of OpenRelix registry rows where scope = repo.",
                },
            },
            {
                "label": "和已发现资产的关系",
                "body": {
                    "zh": "它是 registry/assets.jsonl 的子集；本机发现到的仓库 skills 不会自动算作登记册仓库资产。",
                    "en": "It is a subset of registry/assets.jsonl; repo skills discovered on this machine are not counted as repo-scoped registry assets by themselves.",
                },
            },
        ],
        "today_token": [
            {
                "label": "统计什么",
                "body": "当前 Token 筛选条件下的总 Token 消耗。",
            },
            {
                "label": "怎么算",
                "body": "总量按筛选后的 Codex / Claude Code 来源、起止日期和展示粒度重新汇总。",
            },
        ],
        "seven_day_token": [
            {
                "label": "统计什么",
                "body": "当前 Token 筛选区间的 ccusage 费用估算。",
            },
            {
                "label": "怎么看",
                "body": "配合左侧总量卡片看周期成本；按月展示时均值会按有数据月份计算。",
            },
        ],
        "durable_memories": [
            {
                "label": "统计什么",
                "body": "按记忆签名归并后，bucket = durable 的个人资产-长期记忆数量。",
            },
            {
                "label": "数据来源",
                "body": "state root 下的 registry/memory_entries.jsonl；同一条记忆跨天重复出现时会合并计算。",
            },
        ],
        "session_memories": [
            {
                "label": "统计什么",
                "body": "按记忆签名归并后，bucket = session 的个人资产-工作记忆数量。",
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


def read_panel_package_version():
    return get_project_version(PATHS.repo_root, fallback="")


def make_update_panel_html():
    version = read_panel_package_version()
    version_display = "v{}".format(version) if version else "—"
    return """
          <section
            class="hero-update-card"
            id="openrelix-update-panel"
            aria-labelledby="openrelix-update-title"
            data-update-state="idle"
            data-update-layout="compact"
            data-update-command="{update_command}"
            data-current-version="{current_version}"
          >
            <div class="hero-update-head">
              <div class="hero-update-title-block">
                <p class="hero-update-kicker">{kicker}</p>
                <h2 class="hero-update-title" id="openrelix-update-title">
                  <span class="hero-update-title-full">{title}</span>
                  <span class="hero-update-status-badge" data-update-status-badge>{status_idle}</span>
                </h2>
              </div>
              <span class="hero-update-dot" aria-hidden="true"></span>
            </div>
            <div class="hero-update-compact-line" data-update-compact-line>
              <span data-update-compact-current>{current_version_label}</span>
              <span data-update-compact-last>{last_check_empty}</span>
            </div>
            <div class="hero-update-meta">
              <span>{current_label} <strong data-update-current-label>{current_version_label}</strong></span>
              <span>{last_check_label} <strong data-update-last-check>{last_check_empty}</strong></span>
            </div>
            <p class="hero-update-message" data-update-message>{idle_message}</p>
            <div class="hero-update-command" data-update-command-row hidden>
              <code data-update-command-text>{update_command_text}</code>
            </div>
            <button class="action-button update-primary-button" type="button" data-update-primary>
              <span data-update-primary-label>{check_label}</span>
              <span class="button-spinner" aria-hidden="true"></span>
            </button>
          </section>
    """.format(
        update_command=escape(UPDATE_COMMAND_TEXT, quote=True),
        current_version=escape(version, quote=True),
        current_version_label=escape(version_display),
        update_command_text=escape(UPDATE_COMMAND_TEXT),
        kicker=panel_language_text_html("OpenRelix"),
        title=panel_language_text_html("版本与更新", "Version & Updates"),
        status_idle=panel_language_text_html("未检查", "Not Checked"),
        current_label=panel_language_text_html("当前版本", "Current"),
        last_check_label=panel_language_text_html("上次检查", "Last Check"),
        last_check_empty=panel_language_text_html("未检查", "Not Checked"),
        idle_message=panel_language_text_html(
            "当前版本 {}".format(version_display),
            "Current version {}".format(version_display),
        ),
        check_label=panel_language_text_html("检查更新", "Check Updates"),
    )


def update_token_path():
    return overview_update_secret.update_token_path(PATHS)


def read_or_create_update_token():
    """Persistent shared secret for the local /run-update endpoint.

    Stored under runtime_dir with 0600 perms; both the panel template and
    token_live_server read it. Generated on first call.
    """
    return overview_update_secret.read_or_create_update_token(path=update_token_path())


def build_html(data):
    language = current_language(data.get("language"))
    asset_panels = normalized_asset_panels(data)
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
    window_overview = data.get("window_overview") or {}
    window_overview_default_date = data.get("window_overview_default_date", "")
    window_overview_views = ensure_window_overview_view(
        data.get("window_overview_views", []),
        window_overview,
        selected_date=window_overview_default_date,
        language=language,
    )
    snapshot_payload = json.dumps(
        {
            "generated_at": data["generated_at"],
            "generated_at_iso": data.get("generated_at_iso", ""),
            "token_usage": token_usage,
            "daily_summaries": data.get("daily_summary_views", []),
            "daily_summary_default_date": data.get("daily_summary_default_date", ""),
            "daily_summary_select_dates": data.get("daily_summary_select_dates", []),
            "today_date": data.get("today_date", ""),
            "backfill": data.get("backfill", {}),
            "window_overviews": window_overview_views,
            "window_overview_default_date": window_overview_default_date,
            "pipeline_status": data.get("pipeline_status", {}),
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
    memory_registry = data.get("memory_registry") or []
    memory_policy_views = data.get("memory_policy_views") or overview_memory_context.build_memory_policy_views(
        memory_registry,
        selected_global_rows=data.get("context_memory_preview", []),
        token_usage=data.get("personal_memory_token_usage", {}),
    )
    codex_native_memory = data.get("codex_native_memory") or []
    codex_native_preference_rows = data.get("codex_native_preference_rows") or []
    codex_native_tip_rows = data.get("codex_native_tip_rows") or []
    codex_native_task_groups = data.get("codex_native_task_groups") or []
    codex_native_memory_counts = data.get("codex_native_memory_counts") or {}
    codex_native_memory_comparison = data.get("codex_native_memory_comparison") or {}
    claude_native_memory = data.get("claude_native_memory") or []
    claude_native_topic_rows = data.get("claude_native_topic_rows") or []
    claude_native_preference_rows = data.get("claude_native_preference_rows") or []
    claude_native_tip_rows = data.get("claude_native_tip_rows") or []
    claude_native_memory_counts = data.get("claude_native_memory_counts") or {}
    claude_native_memory_comparison = data.get("claude_native_memory_comparison") or {}
    if claude_native_memory and not any(
        [claude_native_topic_rows, claude_native_preference_rows, claude_native_tip_rows]
    ):
        claude_native_topic_rows = claude_native_memory
    codex_memory_summary_label = data.get("codex_memory_summary_path_label") or render_path(
        PATHS.codex_home / "memories" / "memory_summary.md"
    )
    claude_memory_label = data.get("claude_memory_path_label") or render_path(
        PATHS.claude_home / "CLAUDE.md"
    )
    nightly_note = data.get("nightly_note", nightly.get("date", "暂无夜间整理结果"))
    active_nightly_note = data.get("active_nightly_note", "")
    nightly_window_title = data.get("window_overview_title", derive_nightly_window_title(data["nightly_title"]))
    project_context_views = data.get("project_context_views") or {}
    project_context_views_zh = data.get("project_context_views_zh") or project_context_views
    project_context_views_en = data.get("project_context_views_en") or project_context_views
    project_context_default_days = data.get("project_context_default_days", PROJECT_CONTEXT_DEFAULT_DAYS)
    project_context_note = "项目脉络先于窗口明细展示；可切换最近 1-{} 天".format(
        PROJECT_CONTEXT_MAX_DAYS
    )
    project_context_note_en = "Context map sits before window details; switch last 1-{} days".format(
        PROJECT_CONTEXT_MAX_DAYS
    )
    window_overview_heading, window_overview_note = build_window_overview_heading_note(
        window_overview,
        nightly_window_title,
        language=language,
    )
    daily_summary_title = localized("今天哪些工作能复用？", "What work can be reused today?", language)
    window_overview_date_control = make_window_overview_date_control(
        window_overview_views,
        window_overview_default_date or (window_overview or {}).get("date", ""),
    )
    insight_section_html = """
    <section class="grid">
      <section class="panel">
        {term_cloud_header}
        <div class="term-cloud-area">
          {term_cloud}
        </div>
      </section>
    </section>
    """.format(
        term_cloud_header="{term_cloud_header}",
        term_cloud="{term_cloud}",
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
                <div class="metric-label" data-role="label">{label}</div>
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
        [
            {
                "label": "统计什么",
                "body": {
                    "zh": "按五类高阶资产类型汇总：skills、提示词、Codex 规则、插件、启动项。",
                    "en": "Groups assets into five high-level types: skills, prompts, Codex rules, plugins, and automations.",
                },
            },
            {
                "label": "数据来源",
                "body": {
                    "zh": "包含本机扫描出的 Codex / Claude 资产；如果 registry/assets.jsonl 有登记册条目，也会并入同一组。",
                    "en": "Includes Codex / Claude assets discovered on this machine; registry/assets.jsonl rows are merged when present.",
                },
            },
        ],
        compact=True,
    )
    month_panel_help = make_help_popover(
        "月度活动",
        [
            {
                "label": "统计什么",
                "body": {
                    "zh": "近 6 个月每月被模型实际读取过 SKILL.md 的不同 skills 数，同名 skills 跨来源只算一个；最新月份排在最上方。",
                    "en": "Distinct skills whose SKILL.md files were actually read by the model each month in the last 6 months; same-name skills across sources count once, newest month first.",
                },
            },
            {
                "label": "数据来源",
                "body": {
                    "zh": "来自本机 Codex 会话与 Claude 项目 session 中的 skills 读取事件；登记册资产会并入资产集合，但没有读取事件时不计入月度活动。",
                    "en": "Comes from skill-read events in local Codex sessions and Claude project sessions; registry assets are merged into the asset set, but they do not count as monthly activity without read events.",
                },
            },
        ],
    )
    mcp_usage_help = make_help_popover(
        "MCP 使用热度",
        [
            {
                "label": "MCP 是什么",
                "body": {
                    "zh": "MCP 是 Model Context Protocol。这里可以把它理解成 Codex 可调用的外部工具入口，比如浏览器、Figma、飞书、IDE 索引或本地自动化。",
                    "en": "MCP means Model Context Protocol. In this panel it represents external tools Codex can call, such as browser automation, Figma, Feishu, IDE indexes, or local automations.",
                },
            },
            {
                "label": "统计什么",
                "body": {
                    "zh": "统计近 30 天 Codex 会话里真实 function_call 名称形如 mcp__server__tool 的调用次数，并按 server/tool 聚合。",
                    "en": "Counts real function_call entries in the last 30 days whose names look like mcp__server__tool, grouped by server/tool.",
                },
            },
            {
                "label": "怎么看",
                "body": {
                    "zh": "例如 playwright/browser_navigate 表示 Codex 调过 Playwright 的浏览器导航工具；次数越高，说明这类外部工具在近期任务里越常被用到。",
                    "en": "For example, playwright/browser_navigate means Codex called Playwright's browser navigation tool; higher counts mean that external tool was used more often recently.",
                },
            },
            {
                "label": "隐私边界",
                "body": {
                    "zh": "这里只保留 server/tool 名称、调用次数、命中会话数和最近日期，不展示工具参数或返回内容。",
                    "en": "Only server/tool names, call counts, session counts, and latest dates are shown. Tool arguments and returned content are not displayed.",
                },
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
                "body": "左边是当天热词，右边是滚动近 7 日热词。",
            },
            {
                "label": "怎么看",
                "body": [
                    "主热词是当前范围内权重最高的词，横条越长代表出现频次越高。",
                    "它会随当天窗口整理、资产、复盘或复用记录新增、修改而变化。",
                ],
            },
        ],
    )
    term_cloud_header_html = make_panel_header(
        "今日热词",
        "今日 / 近 7 日并排对照",
        term_cloud_help,
    )
    term_cloud_html = make_summary_term_cloud_views(
        data.get("summary_term_views", []),
        data.get("summary_term_default_days", SUMMARY_TERM_DEFAULT_DAYS),
        language=language,
    )
    insight_section_html = insight_section_html.format(
        term_cloud_header=term_cloud_header_html,
        term_cloud=term_cloud_html,
    )
    token_overview_help = make_help_popover(
        "Token 速览",
        [
            {
                "label": "统计什么",
                "body": "把 ccusage 数据按当前来源、日期范围和展示粒度加工成账单、均值、峰值和缓存读取占比。",
            },
            {
                "label": "怎么看",
                "body": "上方两张大卡看筛选总量和成本，速览区看周期结构，下面的趋势 / 构成柱条可以 hover 到具体构成。",
            },
            {
                "label": "注意",
                "body": "今日输入柱条对齐 ccusage 表格里的无缓存 Input；缓存读取单独展示为总输入的缓存命中部分。",
            },
        ],
    )
    daily_token_help = make_help_popover(
        "Token 消耗趋势",
        [
            {
                "label": "数据来源",
                "body": "ccusage 的日维度统计。",
            },
            {
                "label": "统计什么",
                "body": "按当前筛选条件展示日维度或月维度 Token 消耗趋势；页面打开后会先显示快照，再尝试刷新实时值。",
            },
        ],
    )
    today_token_help = make_help_popover(
        "Token 构成",
        [
            {
                "label": "数据来源",
                "body": "ccusage 当前筛选末端日期或月份的 breakdown。",
            },
            {
                "label": "统计什么",
                "body": "把当前筛选末端日期或月份的 Token 指标拆成无缓存输入、缓存读取、输出和推理输出。",
            },
        ],
    )
    project_context_help = make_help_popover(
        "当前项目上下文",
        [
            {
                "label": "统计什么",
                "body": "最近捕获到的窗口，会先按项目 / 上下文聚合，只保留项目数、并行任务数、窗口数、讨论数和最近活动。",
            },
            {
                "label": "怎么算",
                "body": [
                    "优先从窗口 cwd 推 project_label：先认 Git 根目录，再认常见项目标记。",
                    "cwd 推不出时，才回退到问题摘要、结论摘要和关键词做文本推断。",
                    "同名项目会合并，按讨论数从高到低排序。",
                ],
            },
            {
                "label": "怎么看",
                "body": "顶部地图看总量；项目行看每个项目下有几条任务在并行；追溯入口会锚到下方窗口明细。",
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
                    "工作窗口、长期记忆、工作记忆、低优先级记忆。",
                    "最近相关的上下文标签。",
                ],
            },
            {
                "label": "当前来源",
                "body": window_source_note,
            },
        ],
    )
    memory_compiler_help = make_help_popover(
        "总览",
        [
            {
                "label": "统计什么",
                "body": "把 OpenRelix 独立登记册按注入策略拆分，展示哪些条目会进入 host context，哪些用于按需召回或本地保留。",
            },
            {
                "label": "怎么算",
                "body": "先按 scope 和 injection_policy 归一化；通用上下文和高价值项目上下文会进入 bounded summary 候选。",
            },
        ],
    )
    global_memory_help = make_help_popover(
        "通用上下文",
        [
            {
                "label": "统计什么",
                "body": {
                    "zh": "当前会进入 bounded summary 的通用个人资产记忆；不同项目的记忆不会出现在这里。",
                    "en": "Global personal asset memories that enter the bounded summary; project-specific memories do not appear here.",
                },
            },
            {
                "label": "怎么看",
                "body": {
                    "zh": "先按流程、语义等记忆类型分组，每组默认展示 2 行 2 列，点开卡片可看来源与上下文。",
                    "en": "Items are grouped by memory type such as procedural and semantic; each group shows a 2-by-2 preview by default, and cards expand to show source context.",
                },
            },
        ],
    )
    project_memory_help = make_help_popover(
        "项目上下文",
        [
            {
                "label": "统计什么",
                "body": "绑定项目、仓库或工作区的个人资产记忆；保存在 OpenRelix 登记册，并作为带项目边界的候选进入 bounded host context。",
            },
            {
                "label": "含义",
                "body": "这类条目注入时会保留项目标签，帮助模型识别适用边界，避免把项目规则误当成通用规则。",
            },
        ],
    )
    on_demand_memory_help = make_help_popover(
        "按需召回",
        [
            {
                "label": "统计什么",
                "body": "领域型或检索型记忆，默认不进入 host context，需要任务命中时再召回。",
            },
            {
                "label": "价值",
                "body": "把有用但不应常驻上下文的信息留在独立系统里，降低 token 和错误注入风险。",
            },
        ],
    )
    local_memory_help = make_help_popover(
        "本地保留",
        [
            {
                "label": "统计什么",
                "body": "低优先、本地私有或明确禁止注入的记忆；只作为资产证据保留。",
            },
            {
                "label": "含义",
                "body": "这些条目不会进入 Codex 或 Claude Code 的上下文，主要用于审阅、回溯和后续人工提升。",
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
        "Codex 原生记忆-记忆条目",
        [
            {
                "label": "统计什么",
                "body": "直接读取 {} 的“What's in Memory”记忆条目，但跳过 OpenRelix 的 Local personal memory registry 注入段。".format(
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
                    "用户偏好、通用 tips 和历史任务索引已经拆到独立模块。",
                ],
            },
            {
                "label": "当前计数",
                "body": "记忆条目 {} 条；用户偏好 {} 条；通用 tips {} 条。".format(
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
        "Codex 原生记忆-历史任务索引",
        [
            {
                "label": "统计什么",
                "body": "读取 MEMORY.md 里的 Task Group 索引，展示历史任务索引和对应来源。",
            },
            {
                "label": "怎么看",
                "body": "它更像长期主题目录，不等同于某一天的 nightly memory。",
            },
        ],
    )
    claude_native_memory_note = (
        claude_native_memory_comparison.get("note")
        or "未检测到 {}。".format(claude_memory_label)
    )
    claude_native_memory_note_zh = (
        claude_native_memory_comparison.get("note_zh")
        or claude_native_memory_note
    )
    claude_native_memory_note_en = claude_native_memory_comparison.get("note_en", "")
    claude_native_memory_note_html = panel_language_text_html(
        claude_native_memory_note_zh,
        claude_native_memory_note_en,
    )
    claude_native_topic_help = make_help_popover(
        "Claude Code 原生记忆-记忆条目",
        [
            {
                "label": "统计什么",
                "body": {
                    "zh": "读取 {} 中用户自己写的 CLAUDE.md 上下文，以及 Claude Code 按项目 / 路径生成的 auto memory。".format(
                        claude_memory_label
                    ),
                    "en": "Reads user-authored CLAUDE.md context from {}, plus Claude Code auto memory grouped by project/path.".format(
                        claude_memory_label
                    ),
                },
            },
            {
                "label": "当前计数",
                "body": {
                    "zh": "记忆条目 {} 条；用户偏好 {} 条；通用 tips {} 条。".format(
                        claude_native_memory_counts.get("topic_items", len(claude_native_topic_rows)),
                        claude_native_memory_counts.get("user_preferences", 0),
                        claude_native_memory_counts.get("general_tips", 0),
                    ),
                    "en": "{}; {}; {}.".format(
                        plural_en(
                            claude_native_memory_counts.get("topic_items", len(claude_native_topic_rows)),
                            "memory item",
                        ),
                        plural_en(claude_native_memory_counts.get("user_preferences", 0), "user preference"),
                        plural_en(claude_native_memory_counts.get("general_tips", 0), "general tip"),
                    ),
                },
            },
        ],
    )
    claude_native_preference_help = make_help_popover(
        "Claude Code 原生记忆-偏好",
        [
            {
                "label": "统计什么",
                "body": {
                    "zh": "读取 CLAUDE.md 里用户自写的 User preferences，以及 Claude Code auto memory 中明显属于偏好的条目。",
                    "en": "Reads user-authored User preferences in CLAUDE.md and preference-like entries in Claude Code auto memory.",
                },
            },
            {
                "label": "怎么看",
                "body": {
                    "zh": "这类内容更像用户长期偏好；auto memory 的项目来源会显示在卡片来源里。",
                    "en": "These are closer to long-term user preferences; auto-memory project sources are shown on each card.",
                },
            },
        ],
    )
    claude_native_tip_help = make_help_popover(
        "Claude Code 原生记忆-通用 tips",
        [
            {
                "label": "统计什么",
                "body": {
                    "zh": "读取 CLAUDE.md 里用户自写的 General Tips，以及 Claude Code auto memory 中更像工作方法、排障路径或注意事项的条目。",
                    "en": "Reads user-authored General Tips in CLAUDE.md and auto-memory entries that look like working methods, troubleshooting paths, or cautions.",
                },
            },
            {
                "label": "怎么看",
                "body": {
                    "zh": "这类内容更像稳定的工作方法或排障路径。",
                    "en": "These are usually stable working methods or troubleshooting paths.",
                },
            },
        ],
    )
    codex_native_preference_cards = make_codex_native_brief_cards(
        codex_native_preference_rows,
        "preference",
        language=language,
    )
    codex_native_tip_cards = make_codex_native_brief_cards(
        codex_native_tip_rows,
        "tip",
        language=language,
    )
    codex_native_task_group_cards = make_codex_native_brief_cards(
        codex_native_task_groups,
        "task_group",
        language=language,
    )
    top_assets_help = make_help_popover(
        "近 30 天高频 skills 热度",
        [
            {
                "label": "排序方式",
                "body": "按近 30 天模型读取 SKILL.md 的工具调用次数倒序；默认展示 Top 10，可点击查看更多 skills 热度。",
            },
            {
                "label": "数据来源",
                "body": "skills 来源来自本机扫描和近 30 天项目内 / 跨仓库读取记录；同名 skills 跨来源会合并计数。",
            },
            {
                "label": "点击名称",
                "body": "可点击的 skills 名会打开该行优先来源的 SKILL.md。",
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
                    "通俗标题、问题结论对、关键词。",
                    "已整理窗口可一键切换智能整理与原始信息。",
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
    pipeline_status_help = make_help_popover(
        "当前运行内容",
        [
            {
                "label": "统计什么",
                "body": {
                    "zh": "读取 state root 下的轻量运行状态，只展示 pipeline 名称、阶段、日期和最近结果。",
                    "en": "Reads the lightweight runtime status from the state root and shows only pipeline name, phase, date, and recent result.",
                },
            },
            {
                "label": "隐私边界",
                "body": {
                    "zh": "不展示命令行参数、日志、路径或模型输入输出；本地服务在线时会自动轮询最新状态。",
                    "en": "Does not show command arguments, logs, paths, or model input/output; when the local service is online, the panel polls the latest status.",
                },
            },
        ],
    )
    pipeline_status_panel = make_pipeline_status_panel(
        data.get("pipeline_status", {}),
        help_html=pipeline_status_help,
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
  <meta name="openrelix:version" content="{current_version}" data-pkg="{npm_package}" data-update-endpoint="{update_endpoint}" data-update-status-endpoint="{update_status_endpoint}" data-asset-refresh-endpoint="{asset_refresh_endpoint}" data-codex-desktop-endpoint="{codex_desktop_endpoint}" data-memory-feedback-endpoint="{memory_feedback_endpoint}" data-claude-desktop-endpoint="{claude_desktop_endpoint}" data-finder-open-endpoint="{finder_open_endpoint}" data-update-token="{update_token}">
  <title>{document_title}</title>
  <script>
    (function () {{
      const themeStorageKey = "openrelix-panel-theme";
      const supportedThemes = ["system", "light", "dark"];
      let themeChoice = "system";
      try {{
        const storedTheme = window.localStorage ? window.localStorage.getItem(themeStorageKey) : "";
        themeChoice = supportedThemes.includes(storedTheme) ? storedTheme : "system";
      }} catch (error) {{
        themeChoice = "system";
      }}
      const systemPrefersDark = window.matchMedia
        ? window.matchMedia("(prefers-color-scheme: dark)").matches
        : false;
      const resolvedTheme = themeChoice === "dark" || (themeChoice === "system" && systemPrefersDark)
        ? "dark"
        : "light";
      document.documentElement.setAttribute("data-theme-choice", themeChoice);
      document.documentElement.setAttribute("data-theme", resolvedTheme);
    }})();
  </script>
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

    html[data-theme="dark"],
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
      html[data-theme-choice="system"]:not([data-theme="light"]) {{
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

    a,
    button,
    summary,
    [role="button"] {{
      -webkit-tap-highlight-color: transparent;
    }}

    html {{
      min-height: 100%;
      width: 100%;
      max-width: 100%;
      overflow-x: hidden;
      overflow-x: clip;
      overscroll-behavior-x: none;
      scroll-behavior: smooth;
      background: #f5f5f7;
    }}

    html[data-theme="light"] {{
      background: #f5f5f7;
    }}

    html[data-theme="dark"] {{
      background: #171a21;
    }}

    @media (prefers-color-scheme: dark) {{
      html:not([data-theme="light"]) {{
        background: #171a21;
      }}
    }}

    body {{
      position: relative;
      width: 100%;
      max-width: 100%;
      min-height: 100vh;
      margin: 0;
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Hiragino Sans GB", "Noto Sans SC", "Microsoft YaHei", sans-serif;
      background: linear-gradient(180deg, var(--canvas-top) 0%, var(--bg) 100%);
      overflow-x: hidden;
      overflow-x: clip;
      overscroll-behavior-x: none;
      -webkit-font-smoothing: antialiased;
      text-rendering: geometricPrecision;
    }}

    body[data-language="zh"] [data-lang-only="en"],
    body[data-language="en"] [data-lang-only="zh"] {{
      display: none !important;
    }}

    .app-shell {{
      position: relative;
      width: min(1280px, calc(100vw - 48px));
      max-width: calc(100vw - 48px);
      margin: 0 auto;
      padding: 36px 0 56px;
      overflow-x: clip;
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
      max-width: calc(100vw - 28px);
      max-height: calc(100vh - 36px);
      padding: 14px;
      overflow: auto;
      overscroll-behavior-x: contain;
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
      justify-items: start;
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
      display: inline-flex;
      align-items: center;
      justify-self: start;
      width: max-content;
      inline-size: max-content;
      max-width: 100%;
      max-inline-size: 100%;
      padding: 9px 10px 9px 14px;
      border-radius: 12px;
      background: transparent;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      line-height: 1.25;
      text-decoration: none;
      transition: background 160ms ease, color 160ms ease;
      -webkit-user-select: none;
      user-select: none;
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

    .side-nav-link:active {{
      background: var(--accent-soft-strong);
      color: var(--teal);
    }}

    .side-nav-link:focus {{
      outline: none;
    }}

    .side-nav-link:focus-visible {{
      color: var(--teal);
      box-shadow: inset 0 0 0 1px var(--line-strong);
    }}

    .side-nav-link.is-child {{
      margin-left: 12px;
      max-width: calc(100% - 12px);
      max-inline-size: calc(100% - 12px);
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

    .side-nav-link.is-active:focus-visible {{
      background: var(--accent-soft-strong);
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

    .hero-side {{
      flex: 0 1 420px;
      min-width: min(360px, 100%);
    }}

    .hero-actions {{
      display: flex;
      align-items: center;
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

    .hero-mark {{
      width: 44px;
      height: 44px;
      flex: 0 0 auto;
      border-radius: 10px;
      overflow: hidden;
      align-self: center;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.18);
    }}

    .hero-mark img {{
      width: 100%;
      height: 100%;
      display: block;
      object-fit: cover;
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

    .hero-update-card {{
      width: min(420px, 100%);
      flex: 0 0 100%;
      margin-left: auto;
      min-width: 0;
      display: grid;
      gap: 12px;
      padding: 14px;
      border: 1px solid var(--line-strong);
      border-radius: 18px;
      background: var(--control);
      box-shadow: var(--shadow-soft);
    }}

    .hero-update-card[data-update-layout="compact"] {{
      width: auto;
      max-width: 160px;
      flex: 0 1 auto;
      margin-left: 0;
      display: inline-flex;
      align-items: center;
      gap: 7px;
      min-height: 36px;
      padding: 0 12px;
      border-radius: 999px;
      cursor: pointer;
    }}

    .hero-update-card[data-update-layout="compact"]:hover {{
      background: var(--control-strong);
    }}

    .hero-update-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
      min-width: 0;
    }}

    .hero-update-card[data-update-layout="compact"] .hero-update-head {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}

    .hero-update-title-block {{
      min-width: 0;
    }}

    .hero-update-kicker {{
      margin: 0 0 5px;
      color: var(--teal);
      font-size: 12px;
      font-weight: 800;
      line-height: 1.2;
    }}

    .hero-update-card[data-update-layout="compact"] .hero-update-kicker {{
      display: none;
    }}

    .hero-update-title {{
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      color: var(--ink);
      font-size: 18px;
      line-height: 1.25;
    }}

    .hero-update-card[data-update-layout="compact"] .hero-update-title {{
      flex-wrap: nowrap;
      gap: 0;
      font-size: 12px;
      line-height: 1;
      white-space: nowrap;
    }}

    .hero-update-card[data-update-layout="compact"] .hero-update-title-full {{
      display: none;
    }}

    .hero-update-status-badge {{
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--soft);
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
      line-height: 1;
      padding: 6px 9px;
      white-space: nowrap;
    }}

    .hero-update-card[data-update-layout="compact"] .hero-update-status-badge {{
      border: 0;
      background: transparent;
      color: var(--muted);
      padding: 0;
      font-size: 12px;
      font-weight: 760;
    }}

    .hero-update-dot {{
      width: 10px;
      height: 10px;
      flex: 0 0 auto;
      margin-top: 5px;
      border-radius: 999px;
      background: var(--slate);
      box-shadow: 0 0 0 4px rgba(86, 96, 106, 0.12);
    }}

    .hero-update-card[data-update-layout="compact"] .hero-update-dot {{
      width: 7px;
      height: 7px;
      margin-top: 0;
      box-shadow: 0 0 0 3px rgba(86, 96, 106, 0.12);
    }}

    .hero-update-card[data-update-state="checking"] .hero-update-dot,
    .hero-update-card[data-update-state="running"] .hero-update-dot {{
      background: var(--amber);
      box-shadow: 0 0 0 4px rgba(191, 107, 0, 0.16);
    }}

    .hero-update-card[data-update-state="latest"] .hero-update-dot,
    .hero-update-card[data-update-state="completed"] .hero-update-dot {{
      background: var(--green);
      box-shadow: 0 0 0 4px rgba(36, 138, 61, 0.16);
    }}

    .hero-update-card[data-update-state="available"] .hero-update-dot {{
      background: var(--teal);
      box-shadow: 0 0 0 4px rgba(0, 113, 227, 0.15);
    }}

    .hero-update-card[data-update-state="failed"] .hero-update-dot {{
      background: var(--rose);
      box-shadow: 0 0 0 4px rgba(215, 0, 21, 0.14);
    }}

    .hero-update-meta {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }}

    .hero-update-meta span {{
      min-width: 0;
      overflow-wrap: anywhere;
    }}

    .hero-update-card[data-update-layout="compact"] .hero-update-meta {{
      display: none;
    }}

    .hero-update-compact-line {{
      display: none;
    }}

    .hero-update-card[data-update-layout="compact"] .hero-update-compact-line {{
      display: inline-flex;
      justify-content: flex-end;
      align-items: center;
      gap: 0;
      min-width: 0;
      color: var(--ink);
      font-size: 13px;
      font-weight: 780;
      line-height: 1;
      overflow: hidden;
      white-space: nowrap;
    }}

    .hero-update-card[data-update-layout="compact"] .hero-update-compact-line::before {{
      content: "";
      width: 1px;
      height: 14px;
      flex: 0 0 auto;
      margin: 0 3px 0 1px;
      border-radius: 999px;
      background: var(--line-strong);
    }}

    .hero-update-card[data-update-layout="compact"] [data-update-compact-last] {{
      display: none;
    }}

    .hero-update-card[data-update-layout="compact"] .hero-update-compact-line span {{
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    .hero-update-meta strong {{
      color: var(--ink);
      font-weight: 760;
    }}

    .hero-update-message {{
      margin: 0;
      color: var(--ink);
      font-size: 13px;
      line-height: 1.5;
      overflow-wrap: anywhere;
    }}

    .hero-update-card[data-update-layout="compact"] .hero-update-message {{
      display: none;
    }}

    .hero-update-command {{
      min-width: 0;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--soft);
      color: var(--ink);
      font-size: 12px;
      line-height: 1.4;
      overflow-x: auto;
    }}

    .hero-update-card[data-update-layout="compact"] .hero-update-command {{
      display: none;
    }}

    .hero-update-command code {{
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      white-space: nowrap;
    }}

    .update-primary-button {{
      justify-content: center;
      min-height: 40px;
      width: fit-content;
      max-width: 100%;
      white-space: nowrap;
    }}

    .hero-update-card[data-update-layout="compact"] .update-primary-button {{
      display: none;
    }}

    .update-primary-button[disabled] {{
      cursor: progress;
      opacity: 0.78;
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

    .memory-group-list {{
      display: grid;
      gap: 16px;
      grid-template-columns: 1fr;
      align-items: start;
      min-width: 0;
    }}

    .memory-family {{
      display: grid;
      gap: 18px;
    }}

    .asset-ledger-section {{
      display: grid;
      gap: 18px;
    }}

    .memory-family-head.asset-ledger-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
    }}

    .asset-ledger-actions {{
      display: grid;
      justify-items: end;
      justify-content: flex-end;
      gap: 8px;
      min-width: min(260px, 100%);
    }}

    .asset-refresh-meta {{
      display: block;
      max-width: 300px;
      color: var(--teal);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.4;
      overflow-wrap: anywhere;
      text-align: right;
    }}

    .action-button.asset-refresh-button {{
      flex: 0 0 auto;
      border: 0;
      background: var(--teal);
      color: #ffffff;
      box-shadow: 0 12px 24px rgba(0, 113, 227, 0.18);
      white-space: nowrap;
    }}

    .asset-refresh-status {{
      min-width: 0;
      max-width: 260px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      line-height: 1.4;
      overflow-wrap: anywhere;
      text-align: right;
    }}

    .asset-refresh-status[data-kind="success"] {{
      color: var(--green);
    }}

    .asset-refresh-status[data-kind="error"] {{
      color: var(--rose);
    }}

    .asset-stats-snapshot-panel {{
      display: grid;
      gap: 14px;
    }}

    .asset-stats-summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(156px, 100%), 1fr));
      gap: 10px;
    }}

    .asset-stats-summary-item {{
      display: grid;
      gap: 4px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--control);
    }}

    .asset-stats-summary-item span {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
      line-height: 1.35;
    }}

    .asset-stats-summary-item strong {{
      color: var(--ink);
      font-size: 25px;
      line-height: 1;
    }}

    .asset-stats-summary-item small {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }}

    .asset-discovery-summary {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px 10px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 650;
      line-height: 1.5;
    }}

    .asset-discovery-summary-item strong {{
      color: var(--ink);
      font-weight: 780;
    }}

    .asset-discovery-groups {{
      display: grid;
      gap: 12px;
    }}

    .asset-discovery-table-wrap {{
      margin-top: 10px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--control);
    }}

    .asset-discovery-table {{
      min-width: 720px;
      background: transparent;
    }}

    .asset-discovery-table th,
    .asset-discovery-table td {{
      padding: 9px 10px;
    }}

    .asset-discovery-table th:nth-child(2),
    .asset-discovery-table td:nth-child(2) {{
      width: 40%;
      max-width: 40%;
    }}

    .top-skills-table {{
      table-layout: fixed;
      min-width: 860px;
    }}

    .top-skills-name-col {{
      width: 23%;
    }}

    .top-skills-description-col {{
      width: auto;
    }}

    .top-skills-count-col {{
      width: 76px;
    }}

    .top-skills-table th:nth-child(2),
    .top-skills-table td:nth-child(2) {{
      width: auto;
      max-width: none;
    }}

    .top-skills-table th:nth-child(3),
    .top-skills-table th:nth-child(4),
    .top-skills-table td:nth-child(3),
    .top-skills-table td:nth-child(4) {{
      text-align: right;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }}

    .top-skills-table .content-more-cell {{
      padding-left: 10px;
      padding-right: 10px;
    }}

    .mcp-usage-table .asset-discovery-name {{
      overflow-wrap: anywhere;
    }}

    .asset-discovery-name {{
      font-weight: 700;
      line-height: 1.28;
    }}

    .discovered-skill-name {{
      display: inline;
      padding: 0;
      border: 0;
      background: transparent;
      color: var(--accent-strong);
      cursor: pointer;
      font: inherit;
      font-weight: 750;
      text-align: left;
      text-decoration: none;
    }}

    .discovered-skill-name:hover {{
      text-decoration: underline;
    }}

    .discovered-skill-name:disabled {{
      cursor: wait;
      opacity: 0.68;
    }}

    .asset-source-tags {{
      margin-top: 3px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 600;
      line-height: 1.35;
    }}

    .asset-source-tag {{
      white-space: nowrap;
    }}

    .asset-discovery-description {{
      display: block;
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
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
      grid-template-columns: minmax(0, 1fr) minmax(220px, 0.72fr) minmax(260px, 0.86fr);
      align-items: start;
      gap: 18px;
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

    .memory-compiler-panel {{
      overflow: hidden;
    }}

    .memory-compiler-body {{
      display: grid;
      gap: 16px;
    }}

    .memory-compiler-meter {{
      display: grid;
      gap: 10px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--control);
      min-width: 0;
    }}

    .memory-compiler-meter-topline {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }}

    .memory-compiler-meter-topline span {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 780;
      line-height: 1.35;
    }}

    .memory-compiler-meter-topline b {{
      color: var(--ink);
      font-size: 18px;
      font-weight: 820;
      font-variant-numeric: tabular-nums;
      line-height: 1.1;
      white-space: nowrap;
    }}

    .memory-compiler-meter-topline em {{
      min-height: 24px;
      display: inline-flex;
      align-items: center;
      padding: 0 10px;
      border: 1px solid rgba(52, 199, 89, 0.24);
      border-radius: 999px;
      background: rgba(52, 199, 89, 0.12);
      color: var(--green);
      font-size: 12px;
      font-style: normal;
      font-weight: 760;
      line-height: 1;
      white-space: nowrap;
    }}

    .memory-compiler-meter p {{
      margin: 0;
      color: var(--slate);
      font-size: 12px;
      font-weight: 650;
      line-height: 1.45;
    }}

    .memory-compiler-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }}

    .memory-compiler-stat {{
      min-width: 0;
      padding: 13px 14px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--surface);
    }}

    .memory-compiler-stat span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 780;
      line-height: 1.25;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .memory-compiler-stat strong {{
      display: block;
      margin-top: 8px;
      color: var(--ink);
      font-size: 28px;
      font-weight: 820;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }}

    .memory-compiler-stat small {{
      display: block;
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
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

    .pipeline-panel {{
      display: grid;
      gap: 16px;
    }}

    .pipeline-live-card {{
      display: flex;
      align-items: stretch;
      justify-content: space-between;
      gap: 16px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--metric-card);
      min-width: 0;
    }}

    .pipeline-live-main {{
      display: flex;
      align-items: flex-start;
      gap: 12px;
      min-width: 0;
    }}

    .pipeline-live-dot {{
      flex: 0 0 auto;
      width: 11px;
      height: 11px;
      margin-top: 4px;
      border-radius: 999px;
      background: var(--slate);
      box-shadow: 0 0 0 4px rgba(86, 96, 106, 0.12);
    }}

    .pipeline-panel[data-pipeline-status="running"] .pipeline-live-dot {{
      background: var(--teal);
      box-shadow: 0 0 0 4px rgba(0, 113, 227, 0.12);
      animation: pipelinePulse 1.8s ease-in-out infinite;
    }}

    .pipeline-panel[data-pipeline-status="completed"] .pipeline-live-dot {{
      background: var(--green);
      box-shadow: 0 0 0 4px rgba(36, 138, 61, 0.12);
    }}

    .pipeline-panel[data-pipeline-status="failed"] .pipeline-live-dot {{
      background: var(--rose);
      box-shadow: 0 0 0 4px rgba(215, 0, 21, 0.12);
    }}

    @keyframes pipelinePulse {{
      0%, 100% {{ transform: scale(1); opacity: 1; }}
      50% {{ transform: scale(1.25); opacity: 0.72; }}
    }}

    .pipeline-live-copy {{
      display: grid;
      gap: 5px;
      min-width: 0;
    }}

    .pipeline-live-title {{
      color: var(--ink);
      font-size: 17px;
      font-weight: 760;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }}

    .pipeline-live-message {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }}

    .pipeline-failure-hint {{
      max-width: 760px;
      padding: 9px 11px;
      border: 1px solid rgba(215, 0, 21, 0.16);
      border-radius: 10px;
      background: var(--danger-soft);
      color: var(--rose);
      font-size: 12px;
      font-weight: 650;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }}

    .pipeline-failure-hint:empty {{
      display: none;
    }}

    .pipeline-live-meta {{
      display: flex;
      flex-wrap: wrap;
      align-content: flex-start;
      justify-content: flex-end;
      gap: 6px;
      min-width: min(260px, 100%);
    }}

    .pipeline-live-meta span,
    .pipeline-history-meta {{
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 4px 9px;
      border-radius: 999px;
      background: var(--chip-muted-bg);
      color: var(--slate);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.25;
    }}

    .pipeline-history-meta-list {{
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 6px;
      min-width: min(620px, 100%);
    }}

    .pipeline-next-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--soft);
      min-width: 0;
    }}

    .pipeline-next-copy {{
      display: grid;
      gap: 4px;
      min-width: 0;
    }}

    .pipeline-next-label {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 760;
      letter-spacing: 0;
    }}

    .pipeline-next-copy strong {{
      color: var(--ink);
      font-size: 14px;
      line-height: 1.28;
      overflow-wrap: anywhere;
    }}

    .pipeline-next-copy span:last-child {{
      color: var(--slate);
      font-size: 12px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }}

    .pipeline-actions {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      min-width: min(300px, 100%);
    }}

    .pipeline-run-button {{
      flex: 0 0 auto;
    }}

    .pipeline-run-status {{
      max-width: 180px;
      text-align: right;
    }}

    .pipeline-step-track {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(150px, 100%), 1fr));
      gap: 8px;
    }}

    .pipeline-step {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      min-height: 36px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--soft);
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.25;
    }}

    .pipeline-step-dot {{
      flex: 0 0 auto;
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--line-strong);
    }}

    .pipeline-step[data-step-status="running"] {{
      color: var(--teal);
      background: var(--accent-soft);
      border-color: var(--accent-soft-strong);
    }}

    .pipeline-step[data-step-status="running"] .pipeline-step-dot {{
      background: var(--teal);
    }}

    .pipeline-step[data-step-status="completed"] .pipeline-step-dot {{
      background: var(--green);
    }}

    .pipeline-step[data-step-status="failed"] {{
      color: var(--rose);
      background: var(--danger-soft);
    }}

    .pipeline-step[data-step-status="failed"] .pipeline-step-dot {{
      background: var(--rose);
    }}

    .pipeline-step-label {{
      min-width: 0;
      overflow-wrap: anywhere;
    }}

    .pipeline-history {{
      display: grid;
      gap: 8px;
    }}

    .pipeline-history-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      min-width: 0;
      padding: 10px 12px;
      border-radius: 12px;
      background: var(--soft);
    }}

    .pipeline-history-title {{
      min-width: 0;
      color: var(--ink);
      font-size: 13px;
      font-weight: 720;
      overflow-wrap: anywhere;
    }}

    .pipeline-history-meta {{
      flex: 0 1 auto;
      white-space: normal;
      text-align: right;
    }}

    .pipeline-empty {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }}

    .nightly-shell {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-columns: minmax(0, 1.55fr) minmax(320px, 0.9fr);
      gap: 34px;
      align-items: start;
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
      overflow: hidden;
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
      position: relative;
      z-index: 1;
      flex: 0 0 auto;
      color: var(--muted);
      font-size: 14px;
      font-weight: 650;
      letter-spacing: 0;
      pointer-events: none;
    }}

    .nightly-date-value {{
      position: relative;
      z-index: 1;
      min-width: 96px;
      color: var(--ink);
      font-family: "SF Pro Display", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
      font-size: 16px;
      font-weight: 720;
      line-height: 1.2;
      font-variant-numeric: tabular-nums;
      pointer-events: none;
    }}

    .nightly-date-input {{
      position: absolute;
      inset: 0;
      z-index: 2;
      width: 100%;
      height: 100%;
      appearance: none;
      -webkit-appearance: none;
      border: 0;
      border-radius: inherit;
      background: transparent;
      padding: 0;
      opacity: 0;
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

    .nightly-backfill-command[hidden] {{
      display: none;
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
      height: fit-content;
      padding: 28px;
      border-radius: 28px;
      border: 1px solid var(--line-strong);
      background: var(--soft);
    }}

    .nightly-stat-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(124px, 100%), 1fr));
      gap: 14px;
    }}

    .nightly-stat-card {{
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-height: 108px;
      padding: 20px;
      border-radius: 22px;
      border: 1px solid var(--line);
      background: var(--card);
      box-shadow: var(--shadow-soft);
      min-width: 0;
    }}

    .nightly-stat-label {{
      display: block;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.3;
      letter-spacing: 0;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .nightly-stat-value {{
      color: var(--ink);
      font-family: "SF Pro Display", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
      font-size: 45px;
      font-weight: 600;
      line-height: 1;
      font-variant-numeric: tabular-nums;
      overflow-wrap: anywhere;
      word-break: break-word;
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

    .token-filter-panel {{
      margin: 0 0 18px;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 22px;
      background: var(--panel);
      box-shadow: var(--shadow);
    }}

    .token-filter-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 14px;
    }}

    .token-filter-head h2 {{
      margin: 0;
      color: var(--ink);
      font-size: 18px;
      font-weight: 760;
      letter-spacing: 0;
    }}

    .token-filter-summary {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      line-height: 1.35;
      text-align: right;
    }}

    .token-filter-grid {{
      display: grid;
      grid-template-columns: minmax(260px, 1.25fr) repeat(2, minmax(150px, 0.72fr)) minmax(210px, 0.95fr) auto;
      gap: 12px;
      align-items: end;
    }}

    .token-filter-field {{
      min-width: 0;
      display: grid;
      gap: 8px;
    }}

    .token-filter-range {{
      cursor: pointer;
    }}

    .token-filter-label {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 750;
      line-height: 1;
    }}

    .token-filter-range .token-filter-label {{
      cursor: pointer;
    }}

    .token-segment-group {{
      display: grid;
      grid-auto-flow: column;
      grid-auto-columns: 1fr;
      min-height: 40px;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--soft);
    }}

    .token-segment-button,
    .token-reset-button {{
      min-height: 34px;
      border: 0;
      border-radius: 9px;
      font: inherit;
      font-size: 12px;
      font-weight: 750;
      line-height: 1;
      cursor: pointer;
      transition: background 160ms ease, color 160ms ease, border-color 160ms ease, transform 160ms ease;
    }}

    .token-segment-button {{
      color: var(--muted);
      background: transparent;
      white-space: nowrap;
    }}

    .token-segment-button[aria-pressed="true"] {{
      color: #fff;
      background: linear-gradient(135deg, #0071e3, #4da2ff);
      box-shadow: 0 8px 20px rgba(0, 113, 227, 0.18);
    }}

    .token-date-input {{
      width: 100%;
      min-width: 0;
      height: 40px;
      padding: 0 11px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--soft);
      color: var(--ink);
      font: inherit;
      font-size: 13px;
      font-weight: 650;
      color-scheme: light dark;
      cursor: pointer;
    }}

    .token-date-input::-webkit-calendar-picker-indicator {{
      cursor: pointer;
    }}

    .token-date-input:focus,
    .token-segment-button:focus-visible,
    .token-reset-button:focus-visible {{
      outline: 2px solid rgba(0, 113, 227, 0.38);
      outline-offset: 2px;
    }}

    .token-reset-button {{
      height: 40px;
      padding: 0 16px;
      border: 1px solid var(--line);
      background: var(--soft);
      color: var(--ink);
      white-space: nowrap;
    }}

    .token-reset-button:hover,
    .token-segment-button:hover {{
      transform: translateY(-1px);
    }}

    .token-filter-panel.is-loading .token-date-input,
    .token-filter-panel.is-loading .token-segment-button,
    .token-filter-panel.is-loading .token-reset-button {{
      opacity: 0.62;
      pointer-events: none;
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
      overflow: visible;
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

    .token-cache-write {{
      background: linear-gradient(90deg, #00a6a6 0%, #5eead4 100%);
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
      font-size: 16px;
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
      grid-template-columns: repeat(2, minmax(0, 1fr));
      align-items: start;
    }}

    .memory-grid.content-more-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}

    .native-brief-grid {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      align-items: stretch;
    }}

    .native-brief-grid.content-more-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}

    .native-brief-card {{
      display: grid;
      gap: 10px;
      min-width: 0;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--card);
      overflow: hidden;
    }}

    .native-brief-topline {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }}

    .native-brief-topline span {{
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    .native-brief-card h3 {{
      margin: 0;
      color: var(--ink);
      font-size: 18px;
      line-height: 1.35;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .native-brief-card p {{
      margin: 0;
      color: var(--ink);
      font-size: 14px;
      line-height: 1.7;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .memory-feedback-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }}

    .memory-feedback-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 5px;
      min-height: 28px;
      padding: 5px 10px;
      border: 1px solid var(--line-strong);
      border-radius: 999px;
      background: var(--surface);
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.2;
      cursor: pointer;
      transition: background .15s ease, border-color .15s ease, color .15s ease, opacity .15s ease;
    }}

    .memory-feedback-button:hover {{
      border-color: rgba(0, 113, 227, 0.35);
      color: var(--teal);
    }}

    .memory-feedback-button.is-active {{
      border-color: rgba(0, 113, 227, 0.38);
      background: rgba(0, 113, 227, 0.09);
      color: var(--teal);
    }}

    .memory-feedback-row[data-memory-feedback-state="downvoted"] .memory-feedback-button.is-active {{
      border-color: rgba(198, 40, 40, 0.28);
      background: rgba(198, 40, 40, 0.08);
      color: #a12a2a;
    }}

    .memory-feedback-icon {{
      width: 14px;
      height: 14px;
      flex: 0 0 auto;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}

    .memory-feedback-button:disabled {{
      cursor: default;
      opacity: .58;
    }}

    .memory-feedback-status {{
      min-width: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }}

    .native-brief-chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      min-width: 0;
    }}

    .native-brief-meta {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .native-brief-chip {{
      display: inline-flex;
      min-width: 0;
      max-width: 100%;
      padding: 5px 9px;
      border-radius: 999px;
      border: 1px solid var(--line-strong);
      background: var(--chip-muted-bg);
      color: var(--muted);
      font-size: 12px;
      line-height: 1.3;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    .native-brief-raw {{
      margin-top: 2px;
      border-top: 1px solid var(--line);
      padding-top: 8px;
    }}

    .native-brief-raw summary {{
      cursor: pointer;
      color: var(--teal);
      font-size: 12px;
      font-weight: 700;
      list-style: none;
    }}

    .native-brief-raw summary::-webkit-details-marker {{
      display: none;
    }}

    .native-brief-raw summary::before {{
      content: "+";
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 16px;
      height: 16px;
      margin-right: 6px;
      border-radius: 999px;
      border: 1px solid rgba(0, 113, 227, 0.22);
      background: rgba(0, 113, 227, 0.08);
      line-height: 1;
      font-size: 12px;
    }}

    .memory-brief-details .memory-card-facts {{
      margin-top: 10px;
      gap: 10px;
    }}

    .memory-brief-details .memory-card-value {{
      flex-wrap: wrap;
      overflow: visible;
    }}

    .native-brief-raw[open] summary::before {{
      content: "−";
    }}

    .native-brief-raw p {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.6;
    }}

    .memory-type-group {{
      grid-column: auto;
      display: grid;
      gap: 12px;
      min-width: 0;
    }}

    .memory-type-group + .memory-type-group {{
      margin-top: 0;
      padding-top: 0;
      border-top: 0;
    }}

    .memory-type-group .native-brief-grid,
    .memory-type-group .native-brief-grid.content-more-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
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
      grid-template-columns: repeat(2, minmax(0, 1fr));
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
      gap: 12px;
      grid-template-columns: 1fr;
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

    .project-context-views {{
      display: grid;
      gap: 16px;
    }}

    .project-context-view {{
      display: grid;
      gap: 16px;
    }}

    .project-context-view[hidden] {{
      display: none;
    }}

    .context-map {{
      display: grid;
      grid-template-columns: minmax(0, 1.6fr) minmax(280px, 0.9fr);
      gap: 16px;
      align-items: stretch;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--card);
      padding: 16px;
      overflow: hidden;
    }}

    .context-map-copy {{
      display: grid;
      align-content: center;
      gap: 8px;
      min-width: 0;
    }}

    .context-map-kicker,
    .context-card-section-title {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      letter-spacing: 0;
      line-height: 1.35;
      text-transform: none;
    }}

    .context-map h3 {{
      margin: 0;
      color: var(--ink);
      font-size: 24px;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }}

    .context-map p {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.55;
      overflow-wrap: anywhere;
    }}

    .context-map-meta {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      overflow-wrap: anywhere;
    }}

    .context-map-signals {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      min-width: 0;
    }}

    .context-map-stat {{
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--soft);
      padding: 12px;
      min-width: 0;
    }}

    .context-map-stat strong {{
      display: block;
      color: var(--ink);
      font-size: 24px;
      line-height: 1.05;
      margin-bottom: 5px;
      overflow-wrap: anywhere;
    }}

    .context-map-stat span {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }}

    .context-card {{
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px;
      background: var(--card);
      overflow: hidden;
      display: grid;
      gap: 14px;
      min-width: 0;
    }}

    .context-card-rail {{
      height: 6px;
      border-radius: 999px;
      background: var(--track);
      overflow: hidden;
    }}

    .context-card-rail span {{
      display: block;
      width: var(--context-weight, 12%);
      height: 100%;
      border-radius: inherit;
      background: var(--teal);
    }}

    .context-project-row {{
      display: grid;
      grid-template-columns: minmax(220px, 1.2fr) minmax(420px, 1.6fr);
      gap: 14px;
      align-items: center;
      min-width: 0;
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
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      text-transform: none;
      letter-spacing: 0;
      margin-bottom: 8px;
    }}

    .context-rank {{
      color: var(--teal);
      font-weight: 800;
    }}

    .context-card h3 {{
      margin: 0;
      font-size: 22px;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }}

    .context-card-cwd {{
      margin: 7px 0 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }}

    .context-card-stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(76px, 1fr));
      gap: 8px;
      justify-content: flex-end;
      min-width: 0;
    }}

    .context-stat {{
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--soft);
      padding: 10px;
      text-align: center;
      min-width: 0;
    }}

    .context-stat strong {{
      display: block;
      color: var(--ink);
      font-size: 20px;
      line-height: 1;
      margin-bottom: 5px;
      overflow-wrap: anywhere;
    }}

    .context-stat span {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }}

    .context-stat.is-time strong {{
      font-size: 14px;
      line-height: 1.25;
      white-space: nowrap;
    }}

    .context-project-subrow {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-width: 0;
      flex-wrap: wrap;
    }}

    .context-task-strip {{
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      align-items: start;
      gap: 10px;
      min-width: 0;
      width: 100%;
    }}

    .context-task-strip > span {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.35;
      padding-top: 8px;
      white-space: nowrap;
    }}

    .context-task-list {{
      display: grid;
      gap: 8px;
      min-width: 0;
    }}

    .context-task-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(240px, 0.9fr);
      align-items: center;
      gap: 10px;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--soft);
      padding: 9px 10px;
    }}

    .context-task-main {{
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      flex-wrap: wrap;
    }}

    .context-task-name {{
      color: var(--ink);
      font-size: 13px;
      font-weight: 650;
      line-height: 1.35;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .context-task-count {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      background: var(--card);
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      line-height: 1.2;
      padding: 5px 8px;
      white-space: nowrap;
    }}

    .context-task-count.is-muted,
    .context-task-empty {{
      color: var(--muted);
    }}

    .context-task-list > .content-more {{
      margin-top: 0;
    }}

    .context-task-list .content-more-trigger {{
      padding-top: 2px;
    }}

    .context-task-list .content-more-grid {{
      margin-top: 8px;
    }}

    .context-focus-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}

    .context-focus-item {{
      display: grid;
      gap: 7px;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--soft);
      padding: 12px;
    }}

    .context-focus-item span {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: none;
    }}

    .context-focus-item p {{
      margin: 0;
      color: var(--ink);
      font-size: 14px;
      line-height: 1.58;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    .context-focus-item.is-muted p {{
      color: var(--muted);
    }}

    .context-card-tags {{
      min-width: 0;
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

    .context-window-links {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px 10px;
      min-width: 0;
    }}

    .context-window-links > span {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.35;
    }}

    .context-window-links > div {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      min-width: 0;
    }}

    .context-window-link,
    .context-window-more {{
      display: inline-flex;
      align-items: center;
      max-width: 100%;
      border: 1px solid rgba(0, 113, 227, 0.22);
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--teal);
      font-size: 12px;
      line-height: 1.2;
      padding: 7px 10px;
      text-decoration: none;
      white-space: nowrap;
    }}

    .context-window-link:hover,
    .context-window-link:focus-visible {{
      border-color: rgba(0, 113, 227, 0.48);
      background: var(--accent-soft-strong);
      outline: none;
    }}

    .context-window-more {{
      color: var(--muted);
      background: var(--soft);
      border-color: var(--line);
    }}

    .context-topic-block {{
      border-top: 1px solid var(--line);
      padding-top: 14px;
      display: grid;
      gap: 10px;
    }}

    .context-topic-list {{
      display: grid;
      gap: 10px;
    }}

    .context-topic {{
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--soft);
      padding: 12px;
      display: grid;
      gap: 10px;
      min-width: 0;
    }}

    .context-topic-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
    }}

    .context-topic-meta {{
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0;
      text-transform: none;
      margin-top: 4px;
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

    .context-topic-signals {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }}

    .context-topic .context-focus-item {{
      padding: 10px;
      background: var(--card);
    }}

    .context-topic .context-focus-item p {{
      font-size: 13px;
      line-height: 1.5;
    }}

    .context-topic-footer {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      min-width: 0;
      flex-wrap: wrap;
    }}

    .context-window-links.is-compact {{
      margin-left: auto;
    }}

    .window-card {{
      scroll-margin-top: 24px;
    }}

    .window-card.is-context-highlight {{
      border-color: rgba(0, 113, 227, 0.48);
      box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.12);
    }}

    .window-summary-list {{
      display: grid;
      gap: 14px;
      grid-template-columns: 1fr;
    }}

    .window-summary-pair-list {{
      display: grid;
      gap: 0;
      grid-template-columns: 1fr;
      list-style: none;
      margin: 0;
      padding: 0;
    }}

    .window-summary-mode-root {{
      min-width: 0;
    }}

    .window-summary-mode-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }}

    .window-summary-mode-controls {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--soft);
      padding: 3px;
    }}

    .window-summary-mode-button {{
      appearance: none;
      border: 0;
      border-radius: 999px;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      font: inherit;
      font-size: 12px;
      font-weight: 700;
      line-height: 1;
      padding: 7px 10px;
      white-space: nowrap;
    }}

    .window-summary-mode-root[data-summary-mode="ai"] [data-window-summary-mode="ai"],
    .window-summary-mode-root[data-summary-mode="raw"] [data-window-summary-mode="raw"] {{
      background: var(--card);
      color: var(--ink);
      box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
    }}

    .window-summary-mode-panel {{
      display: none;
    }}

    .window-summary-mode-root[data-summary-mode="ai"] .window-summary-mode-panel[data-summary-panel="ai"],
    .window-summary-mode-root[data-summary-mode="raw"] .window-summary-mode-panel[data-summary-panel="raw"] {{
      display: block;
    }}

    .window-summary-pair-item {{
      border-top: 1px solid var(--line);
      display: grid;
      gap: 8px;
      margin: 0;
      padding: 14px 0;
    }}

    .window-summary-pair-item:first-child {{
      border-top: 0;
      padding-top: 0;
    }}

    .window-summary-pair-item:last-child {{
      padding-bottom: 0;
    }}

    .window-summary-pair-row {{
      display: grid;
      grid-template-columns: auto 1fr;
      align-items: start;
      gap: 10px;
      min-width: 0;
    }}

    .window-summary-index {{
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--control);
      color: var(--muted);
      flex: 0 0 auto;
      font-size: 12px;
      font-weight: 700;
      line-height: 1;
      padding: 6px 8px;
      white-space: nowrap;
    }}

    .window-summary-markdown {{
      font-size: 14px;
      line-height: 1.65;
      min-width: 0;
    }}

    .window-summary-question {{
      font-size: 15px;
      font-weight: 650;
      line-height: 1.62;
      min-width: 0;
    }}

    .window-summary-conclusion {{
      color: var(--ink);
      font-size: 14px;
      line-height: 1.66;
      min-width: 0;
    }}

    .window-summary-pair-row.is-conclusion .window-summary-index {{
      opacity: 0.82;
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

    .window-card-window-summary {{
      margin: 0 0 8px;
      color: var(--ink);
      font-size: 18px;
      line-height: 1.35;
      font-weight: 700;
      overflow-wrap: anywhere;
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 3;
      overflow: hidden;
    }}

    .window-card[open] .window-card-window-summary {{
      display: block;
      overflow: visible;
    }}

    .window-card-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: none;
      letter-spacing: 0;
      margin-bottom: 8px;
      overflow-wrap: anywhere;
    }}

    .window-card-subline {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-width: 0;
      color: var(--muted);
      line-height: 1.5;
      font-size: 13px;
    }}

    .window-card-status {{
      display: inline-flex;
      align-items: center;
      width: fit-content;
      max-width: 100%;
      margin-top: 10px;
      border: 1px solid rgba(214, 143, 0, 0.28);
      border-radius: 999px;
      background: rgba(255, 204, 102, 0.14);
      color: var(--ink);
      font-size: 12px;
      font-weight: 650;
      line-height: 1.35;
      padding: 6px 9px;
      overflow-wrap: anywhere;
    }}

    .window-card-status.is-ai {{
      border-color: rgba(20, 184, 166, 0.32);
      background: rgba(20, 184, 166, 0.12);
    }}

    .window-card-status.is-lightweight {{
      border-color: rgba(59, 130, 246, 0.32);
      background: rgba(59, 130, 246, 0.12);
    }}

    .window-card-status.is-raw {{
      border-color: rgba(214, 143, 0, 0.28);
      background: rgba(255, 204, 102, 0.14);
    }}

    .window-card-path {{
      min-width: 0;
      word-break: break-all;
    }}

    .window-card-cwd {{
      flex: 0 1 auto;
      min-width: 0;
      text-align: right;
      overflow-wrap: anywhere;
    }}

    .window-card-cwd .path-link {{
      font-weight: 600;
    }}

    .window-card-stats {{
      display: grid;
      grid-template-columns: repeat(2, 76px);
      gap: 10px;
      justify-content: flex-end;
      flex: 0 0 auto;
    }}

    .window-stat {{
      width: 76px;
      min-height: 76px;
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

    .window-card-summary p:last-child {{
      margin-bottom: 0;
    }}

    .window-card-takeaway.window-markdown {{
      margin-top: 16px;
      color: var(--ink);
      line-height: 1.62;
      font-size: 16px;
      overflow-wrap: anywhere;
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 4;
      overflow: hidden;
    }}

    .window-card-pair-text {{
      color: var(--ink);
    }}

    .window-card-pair-preview {{
      display: grid;
      gap: 8px;
      min-width: 0;
    }}

    .window-card-pair-row {{
      display: grid;
      grid-template-columns: auto 1fr;
      align-items: start;
      gap: 10px;
      min-width: 0;
    }}

    .window-card-pair-label {{
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--control);
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      line-height: 1;
      padding: 5px 8px;
      white-space: nowrap;
    }}

    .window-card-pair-body {{
      min-width: 0;
      overflow-wrap: anywhere;
    }}

    .window-card-summary-label {{
      margin-bottom: 7px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }}

    .window-card-meta {{
      margin-top: 14px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
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
      margin-left: auto;
      color: var(--teal);
      font-weight: 600;
    }}

    .window-resume-actions {{
      display: inline-flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }}

    .window-resume-button {{
      appearance: none;
      align-items: center;
      border: 1px solid rgba(0, 113, 227, 0.24);
      border-radius: 999px;
      background: rgba(0, 113, 227, 0.1);
      color: var(--teal);
      cursor: pointer;
      display: inline-flex;
      font: inherit;
      font-size: 12px;
      font-weight: 700;
      justify-content: center;
      line-height: 1;
      padding: 8px 10px;
      text-decoration: none;
      white-space: nowrap;
    }}

    .window-resume-button.is-secondary {{
      border-color: var(--line);
      background: var(--soft);
      color: var(--ink);
    }}

    .window-resume-button.is-review {{
      border-color: rgba(20, 184, 166, 0.34);
      background: rgba(20, 184, 166, 0.12);
      color: #087c70;
    }}

    .window-resume-button:hover {{
      transform: translateY(-1px);
    }}

    .window-resume-button[disabled] {{
      cursor: progress;
      opacity: 0.72;
      transform: none;
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

    .window-card-keywords {{
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
    }}

    .window-card-keywords .window-card-summary-label {{
      margin-bottom: 8px;
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

    .window-markdown {{
      color: var(--ink);
      font-size: 13px;
      line-height: 1.65;
      overflow-wrap: anywhere;
    }}

    .window-card-summary.window-markdown {{
      font-size: 14px;
    }}

    .window-markdown p,
    .window-markdown ul,
    .window-markdown ol,
    .window-markdown blockquote,
    .window-markdown pre {{
      margin: 0 0 10px;
    }}

    .window-markdown p:last-child,
    .window-markdown ul:last-child,
    .window-markdown ol:last-child,
    .window-markdown blockquote:last-child,
    .window-markdown pre:last-child {{
      margin-bottom: 0;
    }}

    .window-markdown ul,
    .window-markdown ol {{
      padding-left: 18px;
    }}

    .window-markdown li + li {{
      margin-top: 4px;
    }}

    .window-markdown code {{
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--control);
      padding: 1px 5px;
      font-size: 12px;
      font-family: var(--mono);
    }}

    .window-markdown pre {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--control);
      padding: 10px;
    }}

    .window-markdown pre code {{
      border: 0;
      border-radius: 0;
      background: transparent;
      padding: 0;
    }}

    .window-markdown blockquote {{
      border-left: 3px solid rgba(0, 113, 227, 0.35);
      color: var(--muted);
      padding-left: 10px;
    }}

    .window-markdown-heading {{
      color: var(--ink);
      font-weight: 700;
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
      overflow-wrap: anywhere;
    }}

    .window-detail-time {{
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0;
    }}

    .window-detail-item.empty {{
      color: var(--muted);
    }}

    .window-detail-source {{
      margin: 12px 0 0;
      padding-top: 10px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}

    .window-subdetail {{
      margin-top: 12px;
      padding-top: 10px;
      border-top: 1px solid var(--line);
    }}

    .window-subdetail:first-child {{
      margin-top: 0;
      padding-top: 0;
      border-top: 0;
    }}

    .window-subdetail > summary {{
      list-style: none;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      color: var(--teal);
      font-size: 12px;
      font-weight: 650;
      line-height: 1.35;
    }}

    .window-subdetail > summary::-webkit-details-marker {{
      display: none;
    }}

    .window-subdetail > summary::after {{
      content: "+";
      flex: 0 0 auto;
      width: 18px;
      height: 18px;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--line);
      color: var(--muted);
      background: var(--control);
      font-size: 13px;
      line-height: 1;
    }}

    .window-subdetail[open] > summary::after {{
      content: "-";
    }}

    .window-subdetail p,
    .window-subdetail .window-detail-list {{
      margin-top: 10px;
    }}

    .window-subdetail-count {{
      margin-left: auto;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--control);
      color: var(--muted);
      padding: 2px 7px;
      font-size: 11px;
      font-weight: 500;
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
      min-width: 0;
    }}

    .term-insight-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      align-items: stretch;
    }}

    .term-insight-card {{
      --term-accent-rgb: 0, 113, 227;
      position: relative;
      min-width: 0;
      overflow: hidden;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.42), rgba(255, 255, 255, 0.08)),
        var(--card);
      box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.44),
        0 12px 28px rgba(15, 23, 42, 0.07);
    }}

    .term-insight-card.is-weekly {{
      --term-accent-rgb: 52, 199, 89;
    }}

    body[data-theme="dark"] .term-insight-card {{
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0)),
        var(--card);
      box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.07),
        0 16px 30px rgba(0, 0, 0, 0.24);
    }}

    .term-insight-card::before {{
      content: "";
      position: absolute;
      inset: 0 0 auto;
      height: 2px;
      background: linear-gradient(90deg, rgba(var(--term-accent-rgb), 0.76), rgba(var(--term-accent-rgb), 0));
    }}

    .term-card-head {{
      position: relative;
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 14px;
    }}

    .term-card-title-block {{
      min-width: 0;
    }}

    .term-card-kicker {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
      line-height: 1.2;
    }}

    .term-card-head h3 {{
      margin: 5px 0 0;
      color: var(--ink);
      font-size: 22px;
      font-weight: 780;
      line-height: 1.15;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }}

    .term-card-count {{
      flex: 0 0 auto;
      min-width: 54px;
      padding: 8px 9px;
      border: 1px solid rgba(var(--term-accent-rgb), 0.16);
      border-radius: 13px;
      background: rgba(var(--term-accent-rgb), 0.08);
      text-align: center;
    }}

    .term-card-count strong {{
      display: block;
      color: var(--ink);
      font-size: 22px;
      font-weight: 780;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }}

    .term-card-count span {{
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      line-height: 1;
    }}

    .term-rank-list {{
      display: flex;
      flex-direction: column;
      gap: 14px;
      margin-top: 18px;
    }}

    .term-rank-item {{
      --term-level: 0.1;
      min-width: 0;
      display: grid;
      grid-template-columns: 34px minmax(0, 1fr) minmax(42px, auto);
      align-items: start;
      gap: 10px;
    }}

    .term-rank-item.is-primary {{
      background: transparent;
    }}

    .term-rank-index {{
      padding-top: 1px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
      line-height: 1.35;
      font-variant-numeric: tabular-nums;
      letter-spacing: 0;
    }}

    .term-rank-item.is-primary .term-rank-index {{
      color: rgba(var(--term-accent-rgb), 0.88);
    }}

    .term-rank-copy {{
      min-width: 0;
      display: grid;
      gap: 8px;
    }}

    .term-rank-label {{
      min-width: 0;
      max-width: 100%;
      color: var(--ink);
      font-size: 14px;
      font-weight: 700;
      line-height: 1.35;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }}

    .term-rank-item.is-primary .term-rank-label {{
      font-size: 14px;
      font-weight: 760;
      line-height: 1.35;
    }}

    .term-rank-track {{
      position: relative;
      display: block;
      width: 100%;
      height: 8px;
      overflow: hidden;
      border-radius: 999px;
      background: var(--track);
    }}

    .term-rank-track::after {{
      content: "";
      position: absolute;
      inset: 0;
      border-radius: inherit;
      background: linear-gradient(90deg, rgba(var(--term-accent-rgb), 0.98), rgba(90, 200, 250, 0.88));
      box-shadow: inset 0 -1px 0 rgba(0, 0, 0, 0.08), 0 1px 2px rgba(0, 0, 0, 0.06);
      transform: scaleX(var(--term-level));
      transform-origin: left center;
    }}

    .term-rank-value {{
      display: inline-flex;
      align-items: center;
      justify-content: flex-end;
      min-width: 42px;
      padding-top: 1px;
      color: var(--ink);
      font-size: 14px;
      font-weight: 780;
      line-height: 1.35;
      font-variant-numeric: tabular-nums;
    }}

    body[data-theme="dark"] .term-rank-value {{
      color: var(--ink);
    }}

    .term-stat-row {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 12px;
    }}

    .term-stat-pill {{
      min-width: 0;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--control);
    }}

    .term-stat-pill span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      line-height: 1.15;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .term-stat-pill strong {{
      display: block;
      margin-top: 5px;
      color: var(--ink);
      font-size: 18px;
      font-weight: 780;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }}

    .term-source-line {{
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }}

    .term-empty {{
      margin: 14px 0 0;
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
        width: min(1280px, calc(100vw - 304px));
        max-width: calc(100vw - 304px);
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
        display: inline-flex;
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
        width: min(1280px, calc(100vw - 28px));
        max-width: calc(100vw - 28px);
        margin: 0 auto;
        padding: 24px 0 calc(128px + env(safe-area-inset-bottom));
      }}

      .side-nav {{
        position: fixed;
        top: auto;
        bottom: max(12px, env(safe-area-inset-bottom));
        left: 14px;
        right: 14px;
        width: auto;
        max-height: none;
        margin: 0;
        padding: 10px;
        border-radius: 18px;
        overflow-x: auto;
        overflow-y: hidden;
        z-index: 90;
        -webkit-overflow-scrolling: touch;
        overscroll-behavior-x: contain;
        box-shadow: 0 18px 48px rgba(0, 0, 0, 0.22);
      }}

      .side-nav-title {{
        display: none;
      }}

      .side-nav-list {{
        display: flex;
        align-items: center;
        gap: 6px;
        min-width: max-content;
        white-space: nowrap;
      }}

      .side-nav-group {{
        display: none;
      }}

      .side-nav-link {{
        flex: 0 0 auto;
        display: inline-flex;
        justify-items: start;
        width: auto;
        inline-size: auto;
        max-width: none;
        max-inline-size: none;
        padding: 9px 10px;
      }}

      .side-nav-link.is-child {{
        margin-left: 0;
        max-width: none;
        max-inline-size: none;
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
        white-space: nowrap;
      }}

      .page [id] {{
        scroll-margin-top: 24px;
        scroll-margin-bottom: 128px;
      }}

      .hero-topline {{
        flex-direction: column;
      }}

      .hero-side {{
        flex: none;
        width: 100%;
        min-width: 0;
        justify-items: stretch;
      }}

      .hero-actions {{
        width: 100%;
        justify-content: flex-start;
      }}

      .hero-update-card {{
        width: 100%;
      }}

      .hero-update-card[data-update-layout="compact"] {{
        width: auto;
      }}
    }}

    @media (max-width: 1040px) {{
      .token-filter-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .token-filter-source,
      .token-filter-grain {{
        grid-column: span 2;
      }}

      .token-reset-button {{
        width: 100%;
      }}

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

      .native-brief-grid,
      .native-brief-grid.content-more-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .memory-group-list {{
        grid-template-columns: 1fr;
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

    @media (max-width: 1280px) and (min-width: 721px) {{
      .memory-family-title-row.has-extra {{
        grid-template-columns: minmax(0, 1fr) minmax(220px, 0.72fr);
      }}

      .memory-token-widget {{
        grid-column: 1 / -1;
      }}
    }}

    @media (max-width: 720px) {{
      .app-shell {{
        width: min(1280px, calc(100vw - 28px));
        max-width: calc(100vw - 28px);
        padding: 20px 0 calc(126px + env(safe-area-inset-bottom));
      }}

      .side-nav {{
        bottom: max(10px, env(safe-area-inset-bottom));
        left: 14px;
        right: 14px;
        width: auto;
      }}

      .hero {{
        padding: 20px 18px;
      }}

      h1 {{
        font-size: 30px;
      }}

      .panel {{
        padding: 18px;
      }}

      .panel h2 {{
        font-size: 21px;
      }}

      .pipeline-live-card,
      .pipeline-next-row,
      .pipeline-history-row {{
        display: grid;
      }}

      .pipeline-live-meta {{
        justify-content: flex-start;
        min-width: 0;
      }}

      .pipeline-history-meta-list {{
        justify-content: flex-start;
        min-width: 0;
      }}

      .pipeline-actions {{
        justify-content: flex-start;
        min-width: 0;
      }}

      .pipeline-run-status {{
        text-align: left;
      }}

      .term-insight-grid {{
        grid-template-columns: 1fr;
      }}

      .term-insight-card {{
        padding: 16px;
        border-radius: 18px;
      }}

      .term-card-head h3 {{
        font-size: 20px;
      }}

      .term-rank-list {{
        margin-top: 14px;
        gap: 12px;
      }}

      .term-rank-item {{
        grid-template-columns: 30px minmax(0, 1fr) minmax(40px, auto);
        gap: 8px;
      }}

      .term-rank-item.is-primary .term-rank-label {{
        font-size: 14px;
      }}

      .nightly-panel {{
        padding: 18px;
        border-radius: 20px;
      }}

      .token-filter-head {{
        display: grid;
        gap: 6px;
      }}

      .token-filter-summary {{
        text-align: left;
      }}

      .token-filter-grid {{
        grid-template-columns: 1fr;
      }}

      .token-filter-source,
      .token-filter-grain {{
        grid-column: auto;
      }}

      .token-segment-group {{
        grid-auto-flow: row;
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }}

      .token-filter-grain .token-segment-group {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
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

      .memory-family-head.asset-ledger-head {{
        display: grid;
        gap: 12px;
      }}

      .asset-ledger-actions {{
        justify-content: flex-start;
        justify-items: start;
        min-width: 0;
      }}

      .asset-refresh-meta {{
        max-width: none;
        text-align: left;
      }}

      .asset-refresh-status {{
        max-width: none;
        text-align: left;
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

      .memory-compiler-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .memory-compiler-meter-topline {{
        grid-template-columns: 1fr;
        gap: 8px;
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

      .native-brief-grid,
      .native-brief-grid.content-more-grid {{
        grid-template-columns: 1fr;
      }}

      .memory-type-group .native-brief-grid,
      .memory-type-group .native-brief-grid.content-more-grid {{
        grid-template-columns: 1fr;
      }}

      .memory-group-list {{
        grid-template-columns: 1fr;
      }}

      .review-panel-grid,
      .review-panel-grid.content-more-grid {{
        grid-template-columns: 1fr;
      }}

      .context-map {{
        grid-template-columns: 1fr;
      }}

      .context-map-signals {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .context-project-row {{
        grid-template-columns: 1fr;
      }}

      .context-card-head {{
        flex-direction: column;
      }}

      .context-card-stats {{
        width: 100%;
        min-width: 0;
        justify-content: flex-start;
        flex-basis: auto;
      }}

      .context-project-subrow {{
        align-items: flex-start;
        flex-direction: column;
      }}

      .context-task-strip {{
        grid-template-columns: 1fr;
      }}

      .context-task-strip > span {{
        padding-top: 0;
      }}

      .context-task-row {{
        grid-template-columns: 1fr;
        align-items: start;
      }}

      .context-focus-grid,
      .context-topic-signals {{
        grid-template-columns: 1fr;
      }}

      .context-topic-footer {{
        align-items: flex-start;
        flex-direction: column;
      }}

      .context-window-links.is-compact {{
        margin-left: 0;
      }}

      .window-card-head {{
        flex-direction: column;
      }}

      .window-card-stats {{
        justify-content: flex-start;
      }}

      .window-card-meta {{
        align-items: flex-start;
        flex-direction: column;
      }}

      .window-card-subline {{
        align-items: flex-start;
        flex-direction: column;
      }}

      .window-card-action {{
        margin-left: 0;
      }}

      .window-resume-actions {{
        width: 100%;
      }}

      .window-card-cwd {{
        text-align: left;
      }}

    }}

    @media (max-width: 520px) {{
      .app-shell {{
        width: min(362px, calc(100vw - 28px));
        max-width: min(362px, calc(100vw - 28px));
      }}

      .hero-update-meta {{
        grid-template-columns: 1fr;
      }}

      .hero-update-card[data-update-layout="compact"] {{
        width: 100%;
        max-width: none;
        grid-template-columns: minmax(0, auto) minmax(0, auto);
        justify-content: center;
        border-radius: 999px;
      }}

      .hero-update-card[data-update-layout="compact"] .hero-update-compact-line {{
        justify-content: flex-start;
      }}

      .hero-actions {{
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        align-items: stretch;
      }}

      .language-switch,
      .theme-switch,
      .hero-github-link {{
        width: 100%;
        min-width: 0;
        justify-content: center;
      }}

      .language-option,
      .theme-option {{
        flex: 1 1 auto;
        min-width: 0;
        text-align: center;
      }}

      .hero-github-link {{
        line-height: 1.25;
        text-align: center;
        white-space: normal;
      }}

      .update-primary-button {{
        width: 100%;
      }}

      .context-map-signals,
      .context-card-stats {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .context-stat.is-time strong {{
        white-space: normal;
      }}
    }}
  </style>
</head>
<body data-language="{default_language}">
  {side_nav}
  <div class="app-shell">
  <main class="page">
    <section class="hero" id="overview-top">
      <div class="hero-topline">
        <div class="hero-title-block">
          <p class="eyebrow">{hero_eyebrow}</p>
          <div class="hero-heading-row">
            {hero_mark}
            <h1>{hero_title}</h1>
            <span class="hero-brand-line">{hero_brand_line}</span>
          </div>
          <p class="hero-copy">
            {hero_copy}
          </p>
        </div>
        <div class="hero-side">
          <div class="hero-actions">
            {theme_switch}
            {language_switch}
            {github_button}
            {hero_update_panel}
          </div>
        </div>
      </div>
      <div class="hero-meta">
        <span class="chip">{snapshot_label}{generated_at} · <span id="snapshot-generated-age">刚刚生成</span></span>
      </div>
    </section>

    {nightly_summary_panel}

    {token_filter_panel}

    <section class="grid token-summary-row" id="token-section">
      {token_metric_cards}
      {token_overview_panel}
    </section>

    <section class="grid two-up">
      {daily_token_panel}
      {today_token_panel}
    </section>

    {insight_section_html}

    {pipeline_status_panel}

    <section class="memory-family" id="memory-section" data-openrelix-section="memory_registry">
      {personal_asset_memory_family_header}
      <section class="panel memory-compiler-panel" id="personal-memory-compiler-section">
        {memory_compiler_header}
        {memory_compiler_body}
      </section>

      <section class="panel" id="personal-memory-global-section">
        {global_memory_header}
        <div class="memory-group-list">
          {global_memory_cards}
        </div>
      </section>

      <section class="grid memory-stack">
        <section class="panel" id="personal-memory-project-section">
          {project_memory_header}
          <div class="memory-group-list">
            {project_memory_cards}
          </div>
        </section>

        <section class="panel" id="personal-memory-on-demand-section">
          {on_demand_memory_header}
          <div class="memory-group-list">
            {on_demand_memory_cards}
          </div>
        </section>
      </section>

      <section class="panel" id="personal-memory-local-section">
        {local_memory_header}
        <div class="memory-group-list">
          {local_memory_cards}
        </div>
      </section>

    </section>

    <section class="memory-family" id="codex-native-section">
      {codex_native_memory_family_header}
      <section class="panel" id="codex-native-topic-section">
        {codex_native_topic_header}
        <div class="native-brief-grid memory-grid">
          {codex_native_topic_cards}
        </div>
      </section>

      <section class="panel" id="codex-native-preference-section">
        {codex_native_preference_header}
        <div class="native-brief-grid">
          {codex_native_preference_cards}
        </div>
      </section>

      <section class="panel" id="codex-native-tip-section">
        {codex_native_tip_header}
        <div class="native-brief-grid">
          {codex_native_tip_cards}
        </div>
      </section>

      <section class="panel" id="codex-native-task-group-section">
        {codex_native_task_group_header}
        <div class="native-brief-grid">
          {codex_native_task_group_cards}
        </div>
      </section>
    </section>

    <section class="memory-family" id="claude-native-section">
      {claude_native_memory_family_header}
      <section class="panel" id="claude-native-topic-section">
        {claude_native_topic_header}
        <div class="native-brief-grid memory-grid">
          {claude_native_topic_cards}
        </div>
      </section>

      <section class="panel" id="claude-native-preference-section">
        {claude_native_preference_header}
        <div class="native-brief-grid memory-grid">
          {claude_native_preference_cards}
        </div>
      </section>

      <section class="panel" id="claude-native-tip-section">
        {claude_native_tip_header}
        <div class="native-brief-grid memory-grid">
          {claude_native_tip_cards}
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
        <div class="asset-ledger-actions">
          {asset_refresh_meta_html}
          <button class="action-button asset-refresh-button" type="button" id="asset-layer-refresh-button">
            <span class="button-spinner" aria-hidden="true"></span>
            <span id="asset-layer-refresh-label">{asset_refresh_label}</span>
          </button>
          <span class="asset-refresh-status" id="asset-layer-refresh-status" role="status" aria-live="polite"></span>
        </div>
      </div>
      <section class="grid metrics-grid asset-metrics-grid">
        {asset_metric_cards}
      </section>
      {asset_stats_snapshot_panel}
      <section class="grid two-up">
        {type_panel}
        {month_panel}
      </section>
      <section class="panel" id="top-assets-section">
        {top_assets_header}
        <div class="table-wrap asset-discovery-table-wrap top-skills-table-wrap">
          <table class="asset-discovery-table top-skills-table">
            <colgroup>
              <col class="top-skills-name-col">
              <col class="top-skills-description-col">
              <col class="top-skills-count-col">
              <col class="top-skills-count-col">
            </colgroup>
            <thead>
              <tr>
                <th>{asset_header}</th>
                <th>{description_header}</th>
                <th>{skill_reads_30d_header}</th>
                <th>{skill_sessions_30d_header}</th>
              </tr>
            </thead>
            <tbody>
              {top_skill_rows}
            </tbody>
          </table>
        </div>
      </section>
      {mcp_usage_panel}
      {discovered_assets_section}
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

    <section class="panel" id="project-context-section">
      {project_context_header}
      {project_context_body}
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
        pipelineEndpoint: "http://127.0.0.1:8765/pipeline-status",
        livePollMs: {live_token_poll_ms},
        requestTimeoutMs: {live_token_timeout_ms},
      }};
      function tokenDateInputValue(date) {{
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, "0");
        const day = String(date.getDate()).padStart(2, "0");
        return year + "-" + month + "-" + day;
      }}

      function tokenDefaultDateRange(days) {{
        const resolvedDays = Math.max(Number(days) || 7, 1);
        const end = new Date();
        end.setHours(0, 0, 0, 0);
        const start = new Date(end);
        start.setDate(start.getDate() - resolvedDays + 1);
        return {{
          startDate: tokenDateInputValue(start),
          endDate: tokenDateInputValue(end),
        }};
      }}

      const defaultTokenDateRange = tokenDefaultDateRange(7);
      const state = {{
        tokenUsage: snapshot.token_usage || null,
        tokenRefreshedAt: (snapshot.token_usage && snapshot.token_usage.refreshed_at) || "",
        tokenSourceKind: "snapshot",
        tokenUsageCache: {{}},
        tokenFilters: {{
          provider: (snapshot.token_usage && snapshot.token_usage.provider) || "all",
          startDate: defaultTokenDateRange.startDate,
          endDate: defaultTokenDateRange.endDate,
          groupBy: (snapshot.token_usage && snapshot.token_usage.group_by) || "day",
        }},
        defaultTokenFilters: null,
        selectedNightlyDate: snapshot.daily_summary_default_date || "",
        selectedWindowOverviewDate: snapshot.window_overview_default_date || "",
        pipelineStatus: snapshot.pipeline_status || null,
        refreshStatusKind: "",
        refreshStatusMessageKey: "",
      }};
      state.defaultTokenFilters = Object.assign({{}}, state.tokenFilters);
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
        backfillTitle: document.getElementById("nightly-backfill-title"),
        backfillNote: document.getElementById("nightly-backfill-note"),
        backfillSingleLabel: document.getElementById("nightly-backfill-single-label"),
        backfillSingleCommand: document.getElementById("nightly-backfill-single-command"),
        backfillRange: document.getElementById("nightly-backfill-range"),
        backfillRangeCommand: document.getElementById("nightly-backfill-range-command"),
        backfillStatus: document.getElementById("nightly-backfill-status"),
        backfillCopyButtons: Array.from(document.querySelectorAll("[data-backfill-copy]")),
        assetRefreshButton: document.getElementById("asset-layer-refresh-button"),
        assetRefreshLabel: document.getElementById("asset-layer-refresh-label"),
        assetRefreshStatus: document.getElementById("asset-layer-refresh-status"),
        pipelinePanel: document.getElementById("pipeline-section"),
        pipelineTitle: document.getElementById("pipeline-live-title"),
        pipelineMessage: document.getElementById("pipeline-live-message"),
        pipelineFailureHint: document.getElementById("pipeline-failure-hint"),
        pipelineState: document.getElementById("pipeline-live-state"),
        pipelineTarget: document.getElementById("pipeline-live-target"),
        pipelineProgress: document.getElementById("pipeline-live-progress"),
        pipelineNextTitle: document.getElementById("pipeline-next-title"),
        pipelineNextTime: document.getElementById("pipeline-next-time"),
        pipelineRunButton: document.getElementById("pipeline-run-now-button"),
        pipelineRunLabel: document.getElementById("pipeline-run-now-label"),
        pipelineRunStatus: document.getElementById("pipeline-run-now-status"),
        pipelineStepTrack: document.getElementById("pipeline-step-track"),
        pipelineHistory: document.getElementById("pipeline-history"),
        refreshButton: document.getElementById("token-refresh-button"),
        refreshLabel: document.getElementById("token-refresh-label"),
        refreshStatusText: document.getElementById("token-refresh-status-text"),
        tokenFilterPanel: document.getElementById("token-filter-panel"),
        tokenFilterSummary: document.getElementById("token-filter-summary"),
        tokenProviderButtons: Array.from(document.querySelectorAll("[data-token-provider]")),
        tokenGroupButtons: Array.from(document.querySelectorAll("[data-token-group]")),
        tokenStartDateInput: document.getElementById("token-start-date"),
        tokenEndDateInput: document.getElementById("token-end-date"),
        tokenResetButton: document.getElementById("token-reset-button"),
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

      function localizeValue(zh, en) {{
        return currentLanguage === "en" ? (en || zh || "") : (zh || en || "");
      }}

      function pipelineStatusLabel(status) {{
        const labels = {{
          running: ["运行中", "Running"],
          completed: ["已完成", "Completed"],
          failed: ["失败", "Failed"],
          idle: ["空闲", "Idle"],
          pending: ["等待", "Pending"],
        }};
        const pair = labels[String(status || "idle")] || [String(status || "idle"), String(status || "idle")];
        return localizeValue(pair[0], pair[1]);
      }}

      function pipelineStageLabel(stage) {{
        const labels = {{
          final: ["完整回溯", "Full backfill"],
          preliminary: ["30 分钟快速回溯", "30-minute quick backfill"],
          manual: ["手动整理", "Manual run"],
        }};
        const pair = labels[String(stage || "")] || [String(stage || ""), String(stage || "")];
        return localizeValue(pair[0], pair[1]);
      }}

      function pipelineTargetLabel(payload) {{
        const parts = [];
        if (payload && payload.target_date) parts.push(payload.target_date);
        if (payload && payload.stage) parts.push(pipelineStageLabel(payload.stage));
        if (parts.length) return parts.join(" · ");
        if (payload && payload.status === "running" && payload.started_at_iso) {{
          return (currentLanguage === "en" ? "Started " : "开始于 ") + payload.started_at_iso;
        }}
        if (payload && payload.ended_at_iso) {{
          return (currentLanguage === "en" ? "Ended " : "结束于 ") + payload.ended_at_iso;
        }}
        return currentLanguage === "en" ? "Waiting" : "等待运行";
      }}

      function compactPipelineTime(value) {{
        const text = String(value || "").trim();
        const match = text.match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})[T\\s](\\d{{2}}):(\\d{{2}})(?::(\\d{{2}}))?/);
        if (!match) return text;
        return match[2] + "-" + match[3] + " " + match[4] + ":" + match[5] + (match[6] ? ":" + match[6] : "");
      }}

      function pipelineHistoryTargetLabel(row) {{
        const parts = [];
        if (row && row.target_date) {{
          parts.push((currentLanguage === "en" ? "Date " : "日期 ") + row.target_date);
        }}
        if (row && row.stage) parts.push(pipelineStageLabel(row.stage));
        return parts.join(" · ");
      }}

      function pipelineHistoryMetaLabels(row) {{
        const status = String((row && row.status) || "idle");
        const target = pipelineHistoryTargetLabel(row);
        const started = compactPipelineTime(row && row.started_at_iso);
        const ended = compactPipelineTime(row && row.ended_at_iso);
        return [
          pipelineStatusLabel(status),
          target || (currentLanguage === "en" ? "Date —" : "日期 —"),
          (currentLanguage === "en" ? "Started " : "触发 ") + (started || "—"),
          (currentLanguage === "en" ? "Ended " : "结束 ") + (ended || "—"),
        ];
      }}

      function pipelineNextMeta(nextRun) {{
        if (!nextRun || !nextRun.next_at_iso) {{
          return currentLanguage === "en" ? "No schedule detected" : "未检测到计划任务";
        }}
        const parts = [nextRun.next_at_iso];
        if (nextRun.stage) parts.push(pipelineStageLabel(nextRun.stage));
        if (nextRun.learn_memory) {{
          parts.push(currentLanguage === "en" ? "includes learning refresh" : "含学习刷新");
          const learnWindowDays = Number(nextRun.learn_window_days || 0);
          if (learnWindowDays > 0) {{
            parts.push(currentLanguage === "en"
              ? (learnWindowDays + "-day window")
              : (learnWindowDays + " 天窗口"));
          }}
        }}
        return parts.join(" · ");
      }}

      function setPipelineRunLoading(isLoading) {{
        if (elements.pipelineRunButton) {{
          elements.pipelineRunButton.classList.toggle("is-loading", isLoading);
          elements.pipelineRunButton.disabled = isLoading;
        }}
        if (elements.pipelineRunLabel) {{
          elements.pipelineRunLabel.textContent = isLoading
            ? (currentLanguage === "en" ? "Starting" : "正在启动")
            : (currentLanguage === "en" ? "Run Now" : "立即运行");
        }}
      }}

      function setPipelineRunStatus(kind, message) {{
        if (!elements.pipelineRunStatus) return;
        elements.pipelineRunStatus.dataset.kind = kind || "";
        elements.pipelineRunStatus.textContent = message || "";
      }}

      function renderPipelineSteps(steps) {{
        if (!elements.pipelineStepTrack) return;
        if (!Array.isArray(steps) || !steps.length) {{
          elements.pipelineStepTrack.innerHTML = '<div class="pipeline-empty">' +
            escapeHtml(currentLanguage === "en" ? "No pipeline steps yet." : "暂无 pipeline 步骤。") +
            '</div>';
          return;
        }}
        elements.pipelineStepTrack.innerHTML = steps.map(function (step) {{
          const status = String((step && step.status) || "pending");
          const label = localizeValue((step && step.label) || (step && step.key) || "", (step && step.label_en) || (step && step.label) || "");
          return (
            '<div class="pipeline-step" data-step-status="' + escapeHtml(status) + '">' +
              '<span class="pipeline-step-dot" aria-hidden="true"></span>' +
              '<span class="pipeline-step-label">' + escapeHtml(label) + '</span>' +
            '</div>'
          );
        }}).join("");
      }}

      function renderPipelineHistory(rows) {{
        if (!elements.pipelineHistory) return;
        if (!Array.isArray(rows) || !rows.length) {{
          elements.pipelineHistory.innerHTML = '<div class="pipeline-empty">' +
            escapeHtml(currentLanguage === "en" ? "No recent runs yet." : "暂无近期运行记录。") +
            '</div>';
          return;
        }}
        elements.pipelineHistory.innerHTML = rows.slice(0, 4).map(function (row) {{
          const status = String((row && row.status) || "idle");
          const title = localizeValue((row && row.title) || (row && row.pipeline) || "", (row && row.title_en) || (row && row.title) || "");
          const meta = pipelineHistoryMetaLabels(row).map(function (label) {{
            return '<span class="pipeline-history-meta">' + escapeHtml(label) + '</span>';
          }}).join("");
          return (
            '<div class="pipeline-history-row" data-status="' + escapeHtml(status) + '">' +
              '<span class="pipeline-history-title">' + escapeHtml(title) + '</span>' +
              '<span class="pipeline-history-meta-list">' + meta + '</span>' +
            '</div>'
          );
        }}).join("");
      }}

      function updatePipelineStatus(payload) {{
        if (!elements.pipelinePanel || !payload) return;
        state.pipelineStatus = payload;
        const status = String(payload.status || "idle");
        const stepCount = Number(payload.step_count || 0);
        const stepIndex = Number(payload.current_step_index || 0);
        elements.pipelinePanel.setAttribute("data-pipeline-status", status);
        if (elements.pipelineTitle) {{
          elements.pipelineTitle.textContent = localizeValue(payload.title || "OpenRelix Pipeline", payload.title_en || payload.title || "");
        }}
        if (elements.pipelineMessage) {{
          elements.pipelineMessage.textContent = localizeValue(payload.message || "暂无正在运行的任务。", payload.message_en || "No active task.");
        }}
        if (elements.pipelineFailureHint) {{
          const failureHint = localizeValue(payload.failure_hint || "", payload.failure_hint_en || payload.failure_hint || "");
          elements.pipelineFailureHint.textContent = failureHint;
        }}
        if (elements.pipelineState) {{
          elements.pipelineState.textContent = pipelineStatusLabel(status);
        }}
        if (elements.pipelineTarget) {{
          elements.pipelineTarget.textContent = pipelineTargetLabel(payload);
        }}
        if (elements.pipelineProgress) {{
          elements.pipelineProgress.textContent = stepCount ? (Math.max(stepIndex, 1) + "/" + stepCount) : "—";
        }}
        const nextRun = payload.next_run || {{}};
        if (elements.pipelineNextTitle) {{
          elements.pipelineNextTitle.textContent = localizeValue(nextRun.title || "暂无计划任务", nextRun.title_en || "No scheduled task");
        }}
        if (elements.pipelineNextTime) {{
          elements.pipelineNextTime.textContent = pipelineNextMeta(nextRun);
        }}
        renderPipelineSteps(payload.steps || []);
        renderPipelineHistory(payload.recent_runs || []);
      }}

      async function refreshPipelineStatus() {{
        if (!elements.pipelinePanel) return;
        try {{
          const response = await fetchWithTimeout(config.pipelineEndpoint, Math.min(config.requestTimeoutMs, 5000));
          if (!response.ok) throw new Error("HTTP " + response.status);
          const payload = await response.json();
          updatePipelineStatus(payload);
        }} catch (error) {{
          if (state.pipelineStatus) {{
            updatePipelineStatus(state.pipelineStatus);
          }}
        }}
      }}

      function runPipelineNow() {{
        const endpoint = openrelixMetaAttr("data-asset-refresh-endpoint");
        const token = openrelixMetaAttr("data-update-token");
        if (!endpoint || !token || !window.fetch) {{
          setPipelineRunStatus(
            "error",
            currentLanguage === "en"
              ? "Local service is not running. Open the panel through OpenRelix first."
              : "本地服务未启动。请先通过 OpenRelix 打开面板。"
          );
          return;
        }}
        const headers = {{ "Content-Type": "application/json" }};
        headers["X-OpenRelix-Token"] = token;
        setPipelineRunLoading(true);
        setPipelineRunStatus(
          "loading",
          currentLanguage === "en" ? "Starting pipeline..." : "正在启动任务…"
        );
        fetch(endpoint, {{
          method: "POST",
          headers: headers,
          body: JSON.stringify({{ mode: "pipeline" }})
        }})
          .then(function (response) {{
            return response.json().catch(function () {{
              return null;
            }}).then(function (payload) {{
              if (!response.ok || !payload || payload.ok === false) {{
                throw new Error((payload && payload.error) || ("HTTP " + response.status));
              }}
              return payload;
            }});
          }})
          .then(function (payload) {{
            updatePipelineStatus(payload);
            setPipelineRunStatus(
              "success",
              currentLanguage === "en" ? "Started. Status will update here." : "已启动，状态会在这里更新。"
            );
            window.setTimeout(refreshPipelineStatus, 1200);
          }})
          .catch(function (error) {{
            const message = error && String(error.message || "");
            const alreadyRunning = message.indexOf("pipeline_already_running") >= 0;
            const offline = !message || error.name === "TypeError" || message.indexOf("Failed to fetch") >= 0;
            setPipelineRunStatus(
              "error",
              alreadyRunning
                ? (currentLanguage === "en" ? "A pipeline is already running." : "已有任务正在运行。")
                : (offline
                  ? (currentLanguage === "en" ? "Local service is not running." : "本地服务未启动。")
                  : (currentLanguage === "en" ? "Start failed; try again later." : "启动失败，稍后重试。"))
            );
            refreshPipelineStatus();
          }})
          .finally(function () {{
            setPipelineRunLoading(false);
          }});
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
        match = text.match(/^占总输入 (.+)$/);
        if (match) {{
          return match[1] + " of total input";
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
        match = text.match(/^查看更多 (\\d+) 个 MCP 工具$/);
        if (match) {{
          return "Show " + match[1] + " more MCP tools";
        }}
        match = text.match(/^直接读取 (.+) 的“What's in Memory”记忆条目(?:，.+)?。$/);
        if (match) {{
          return 'Reads memory items from the "What\\'s in Memory" section of ' + match[1] + ".";
        }}
        match = text.match(/^记忆条目 (\\d+) 条；用户偏好 (\\d+) 条；通用 tips (\\d+) 条。$/);
        if (match) {{
          return pluralEn(match[1], "memory item") + "; " +
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
        if (normalized.includes("缓存写入") || normalized.includes("cache write") || normalized.includes("cache creation")) {{
          return currentLanguage === "en" ? "Cache Write" : "缓存写入";
        }}
        if (normalized.includes("缓存读取") || normalized.includes("cache read") || normalized.includes("cached")) {{
          return currentLanguage === "en" ? "Cache Read" : "缓存读取";
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

      function syncDateControlValue(select) {{
        if (!select) {{
          return;
        }}
        const control = select.closest(".nightly-date-control");
        if (!control) {{
          return;
        }}
        const valueNode = control.querySelector("[data-date-select-value]");
        if (!valueNode) {{
          return;
        }}
        const selectedOption = select.selectedOptions && select.selectedOptions.length
          ? select.selectedOptions[0]
          : select.options[select.selectedIndex];
        valueNode.textContent = selectedOption ? selectedOption.textContent : "";
      }}

      function syncDateControlValues() {{
        document.querySelectorAll(".nightly-date-input").forEach(syncDateControlValue);
      }}

      function renderWindowOverview(dateValue) {{
        const view = findWindowOverview(dateValue);
        state.selectedWindowOverviewDate = dateValue || state.selectedWindowOverviewDate;
        if (elements.windowOverviewDateInput && elements.windowOverviewDateInput.value !== dateValue) {{
          elements.windowOverviewDateInput.value = dateValue || "";
        }}
        syncDateControlValue(elements.windowOverviewDateInput);
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

      function snapshotTodayDate() {{
        if (snapshot.today_date) {{
          return snapshot.today_date;
        }}
        if (typeof snapshot.generated_at_iso === "string") {{
          const match = snapshot.generated_at_iso.match(/^\\d{{4}}-\\d{{2}}-\\d{{2}}/);
          if (match) {{
            return match[0];
          }}
        }}
        return "";
      }}

      function isCurrentSnapshotDate(dateValue) {{
        return Boolean(dateValue) && dateValue === snapshotTodayDate();
      }}

      function currentDayPreviewCommand() {{
        return "openrelix review --stage preliminary --learn-window-days 0";
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
        if (isCurrentSnapshotDate(dateValue)) {{
          return currentDayPreviewCommand();
        }}
        const days = backfill.learn_window_days || 7;
        return "openrelix backfill --from " + dateValue + " --to " + dateValue + " --stage final --learn-window-days " + days;
      }}

      function renderBackfillPanel(dateValue, summary) {{
        if (!elements.backfillPanel) {{
          return;
        }}
        const hasSummary = Boolean(summary);
        const isPreliminary = hasSummary && summary.stage === "preliminary";
        const isCurrentDate = isCurrentSnapshotDate(dateValue);
        const isCurrentPreliminary = isPreliminary && isCurrentSnapshotDate(dateValue);
        const missingDates = missingBackfillDates();
        const isCurrentMissing = !hasSummary && isCurrentDate && missingDates.includes(dateValue);
        const shouldShow = Boolean(dateValue) && (
          (isPreliminary && !isCurrentPreliminary) || (!hasSummary && missingDates.includes(dateValue))
        );
        elements.backfillPanel.hidden = !shouldShow;
        if (!shouldShow) {{
          if (elements.backfillStatus) {{
            elements.backfillStatus.textContent = "";
          }}
          return;
        }}
        const singleCommand = commandForBackfillDate(dateValue);
        const rangeCommand = (isPreliminary || isCurrentMissing) ? "" : (backfillState().range_command || "");
        if (elements.backfillTitle) {{
          elements.backfillTitle.textContent = t(isCurrentMissing ? "今日仍在进行中" : (isPreliminary ? "建议深度回溯" : "缺少整理结果"));
        }}
        if (elements.backfillNote) {{
          elements.backfillNote.textContent = t(
            isCurrentMissing
              ? "今天还没结束，当前还没有 30 分钟快速回溯；可先运行今日快速回溯刷新面板，次日会自动生成完整回溯。"
              : isPreliminary
              ? "当前是 30 分钟快速回溯，只生成窗口摘要和快速索引，不做记忆沉淀。可以复制命令在终端补跑完整回溯。首次安装后，会自动触发完整回溯，请耐心等待。"
              : "该日期还没有整理结果。可以复制命令在终端手动回溯。"
          );
        }}
        if (elements.backfillSingleLabel) {{
          elements.backfillSingleLabel.textContent = t(isCurrentMissing ? "30 分钟快速回溯" : (isPreliminary ? "完整回溯" : "单日回溯"));
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
        syncDateControlValue(elements.nightlyDateInput);
        if (!summary) {{
          renderNightlyBadges(null);
          renderBackfillPanel(dateValue, null);
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

        renderBackfillPanel(dateValue, summary);
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
        if (state.pipelineStatus) {{
          updatePipelineStatus(state.pipelineStatus);
        }}
        syncDateControlValues();
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
        document.documentElement.setAttribute("data-theme-choice", currentThemeChoice);
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

      function flashButtonLabel(button, label) {{
        if (!button || !label) {{
          return;
        }}
        const original = button.getAttribute("data-label") || button.textContent || "";
        button.textContent = label;
        window.setTimeout(function () {{
          button.textContent = original;
        }}, 1600);
      }}

      function nativeExternalOpenHandler() {{
        return (
          window.webkit &&
          window.webkit.messageHandlers &&
          window.webkit.messageHandlers.openrelixOpenExternal
        ) ? window.webkit.messageHandlers.openrelixOpenExternal : null;
      }}

      function shouldOpenOutsidePanel(rawHref) {{
        if (!rawHref) {{
          return false;
        }}
        try {{
          const url = new URL(rawHref, window.location.href);
          const protocol = (url.protocol || "").toLowerCase();
          if (!["http:", "https:", "file:", "codex:"].includes(protocol)) {{
            return false;
          }}
          if (protocol === "file:") {{
            const targetPath = url.href.split("#")[0];
            const currentPath = window.location.href.split("#")[0];
            if (targetPath === currentPath) {{
              return false;
            }}
          }}
          return true;
        }} catch (error) {{
          return false;
        }}
      }}

      function postExternalOpen(rawHref) {{
        const handler = nativeExternalOpenHandler();
        if (!handler) {{
          return false;
        }}
        try {{
          handler.postMessage(rawHref);
          return true;
        }} catch (error) {{
          return false;
        }}
      }}

      function openrelixMetaAttr(name) {{
        const meta = document.querySelector('meta[name="openrelix:version"]');
        return meta ? (meta.getAttribute(name) || "").trim() : "";
      }}

      function resetButtonLabelLater(button, label) {{
        window.setTimeout(function () {{
          button.textContent = label;
          button.disabled = false;
        }}, 1600);
      }}

      function openCodexDesktopResume(button) {{
        const resumeId = (button.getAttribute("data-codex-resume-id") || "").trim();
        const codexUrl = (button.getAttribute("data-codex-url") || "").trim();
        const codexHome = (button.getAttribute("data-codex-home") || "").trim();
        const codexElectronUserDataPath = (button.getAttribute("data-codex-electron-user-data-path") || "").trim();
        const isSystemCodexProfile = button.getAttribute("data-codex-system-profile") === "1";
        const resumeCommand = button.getAttribute("data-resume-command") || "";
        const shouldCopyResumeOnSwitch = button.getAttribute("data-copy-resume-on-switch") === "1" && !!resumeCommand;
        const endpoint = openrelixMetaAttr("data-codex-desktop-endpoint");
        const token = openrelixMetaAttr("data-update-token");
        const originalLabel = button.getAttribute("data-label") || button.textContent;
        const openingLabel = button.getAttribute("data-opening-label") || t("正在打开");
        const openedLabel = button.getAttribute("data-opened-label") || t("已发送");
        const focusedCopiedLabel = button.getAttribute("data-focused-copied-label") || openedLabel;
        const errorLabel = button.getAttribute("data-error-label") || t("打开失败");
        let resumeCopyPromise = null;

        function fallbackOpen() {{
          if (codexUrl && (isSystemCodexProfile || (!codexHome && !codexElectronUserDataPath))) {{
            window.location.href = codexUrl;
            return true;
          }}
          return false;
        }}

        if (!resumeId || !endpoint || !window.fetch) {{
          if (!fallbackOpen()) {{
            flashButtonLabel(button, errorLabel);
          }}
          return;
        }}

        button.disabled = true;
        button.textContent = openingLabel;
        if (shouldCopyResumeOnSwitch) {{
          resumeCopyPromise = copyText(resumeCommand).then(function () {{
            return true;
          }}).catch(function () {{
            return false;
          }});
        }}
        const headers = {{ "Content-Type": "application/json" }};
        if (token) {{
          headers["X-OpenRelix-Token"] = token;
        }}
        fetch(endpoint, {{
          method: "POST",
          headers: headers,
          body: JSON.stringify({{
            resume_id: resumeId,
            codex_home: codexHome,
            codex_electron_user_data_path: codexElectronUserDataPath
          }})
        }})
          .then(function (response) {{
            return response.json().catch(function () {{
              return null;
            }}).then(function (payload) {{
              if (!response.ok || !payload || payload.ok === false) {{
                throw new Error((payload && payload.error) || ("HTTP " + response.status));
              }}
              return payload;
            }});
          }})
          .then(function (payload) {{
            if (payload && payload.thread_navigation === "profile_focus_only" && resumeCopyPromise) {{
              return resumeCopyPromise.then(function (copied) {{
                return copied ? focusedCopiedLabel : openedLabel;
              }});
            }}
            return openedLabel;
          }})
          .then(function (nextLabel) {{
            button.textContent = nextLabel;
            resetButtonLabelLater(button, originalLabel);
          }})
          .catch(function () {{
            button.disabled = false;
            button.textContent = originalLabel;
            if (!fallbackOpen()) {{
              flashButtonLabel(button, errorLabel);
            }}
          }});
      }}

      function openClaudeDesktopResume(button) {{
        const resumeId = (button.getAttribute("data-claude-resume-id") || "").trim();
        const endpoint = openrelixMetaAttr("data-claude-desktop-endpoint");
        const token = openrelixMetaAttr("data-update-token");
        const originalLabel = button.getAttribute("data-label") || button.textContent;
        const openingLabel = button.getAttribute("data-opening-label") || t("正在打开");
        const openedLabel = button.getAttribute("data-opened-label") || t("已发送");
        const errorLabel = button.getAttribute("data-error-label") || t("打开失败");
        if (!resumeId || !endpoint || !window.fetch) {{
          flashButtonLabel(button, errorLabel);
          return;
        }}
        button.disabled = true;
        button.textContent = openingLabel;
        const headers = {{ "Content-Type": "application/json" }};
        if (token) {{
          headers["X-OpenRelix-Token"] = token;
        }}
        fetch(endpoint, {{
          method: "POST",
          headers: headers,
          body: JSON.stringify({{ resume_id: resumeId }})
        }})
          .then(function (response) {{
            return response.json().catch(function () {{
              return null;
            }}).then(function (payload) {{
              if (!response.ok || !payload || payload.ok === false) {{
                throw new Error((payload && payload.error) || ("HTTP " + response.status));
              }}
              return payload;
            }});
          }})
          .then(function () {{
            button.textContent = openedLabel;
            resetButtonLabelLater(button, originalLabel);
          }})
          .catch(function () {{
            button.textContent = errorLabel;
            resetButtonLabelLater(button, originalLabel);
          }});
      }}

      function openFinderPath(button) {{
        const path = (button.getAttribute("data-open-finder-path") || "").trim();
        const endpoint = openrelixMetaAttr("data-finder-open-endpoint");
        const token = openrelixMetaAttr("data-update-token");
        if (!path || !endpoint || !window.fetch) {{
          flashButtonLabel(button, t("打开失败"));
          return;
        }}
        const headers = {{ "Content-Type": "application/json" }};
        if (token) {{
          headers["X-OpenRelix-Token"] = token;
        }}
        button.disabled = true;
        fetch(endpoint, {{
          method: "POST",
          headers: headers,
          body: JSON.stringify({{ path: path }})
        }})
          .then(function (response) {{
            return response.json().catch(function () {{
              return null;
            }}).then(function (payload) {{
              if (!response.ok || !payload || payload.ok === false) {{
                throw new Error((payload && payload.error) || ("HTTP " + response.status));
              }}
              return payload;
            }});
          }})
          .then(function () {{
            flashButtonLabel(button, t("已发送"));
          }})
          .catch(function () {{
            flashButtonLabel(button, t("打开失败"));
          }})
          .finally(function () {{
            button.disabled = false;
          }});
      }}

      function memoryFeedbackStatusMessage(feedback) {{
        const state = String(feedback || "");
        if (state === "liked" || state === "pinned") {{
          return currentLanguage === "en" ? "Saved. Refreshing in background." : "已保存，后台刷新中";
        }}
        if (state === "downvoted") {{
          return currentLanguage === "en" ? "Saved to local-only. Refreshing in background." : "已保存到本地保留，后台刷新中";
        }}
        return currentLanguage === "en" ? "Feedback cleared" : "已取消标记";
      }}

      function setMemoryFeedbackStatus(row, message) {{
        const status = row ? row.querySelector(".memory-feedback-status") : null;
        if (status) {{
          status.textContent = message || "";
        }}
      }}

      function updateMemoryFeedbackRow(row, feedback) {{
        if (!row) {{
          return;
        }}
        const state = String(feedback || "") === "pinned" ? "liked" : String(feedback || "");
        row.setAttribute("data-memory-feedback-state", state);
        row.querySelectorAll("[data-memory-feedback]").forEach(function (candidate) {{
          const active = candidate.getAttribute("data-memory-feedback") === state;
          candidate.classList.toggle("is-active", active);
          candidate.setAttribute("aria-pressed", active ? "true" : "false");
        }});
        setMemoryFeedbackStatus(row, memoryFeedbackStatusMessage(state));
      }}

      function moveMemoryFeedbackCard(row, feedback) {{
        if (!row) return;
        const card = row.closest(".memory-brief-card");
        const grid = card ? card.closest(".native-brief-grid") : null;
        if (!card || !grid) return;
        if (feedback === "liked" && grid.firstElementChild !== card) {{
          grid.insertBefore(card, grid.firstElementChild);
        }} else if (feedback === "downvoted") {{
          grid.appendChild(card);
        }}
      }}

      function submitMemoryFeedback(button) {{
        const endpoint = openrelixMetaAttr("data-memory-feedback-endpoint");
        const token = openrelixMetaAttr("data-update-token");
        const row = button.closest(".memory-feedback-row");
        const memoryKey = (button.getAttribute("data-memory-key") || "").trim();
        let feedback = (button.getAttribute("data-memory-feedback") || "").trim();
        if (button.getAttribute("aria-pressed") === "true") {{
          feedback = "neutral";
        }}
        if (!endpoint || !token || !memoryKey || !window.fetch) {{
          setMemoryFeedbackStatus(
            row,
            currentLanguage === "en"
              ? "Local service is not running."
              : "本地服务未启动。"
          );
          return;
        }}
        const buttons = row ? Array.from(row.querySelectorAll("[data-memory-feedback]")) : [button];
        buttons.forEach(function (candidate) {{
          candidate.disabled = true;
        }});
        setMemoryFeedbackStatus(row, currentLanguage === "en" ? "Saving..." : "正在保存…");
        const headers = {{ "Content-Type": "application/json" }};
        headers["X-OpenRelix-Token"] = token;
        fetch(endpoint, {{
          method: "POST",
          headers: headers,
          body: JSON.stringify({{
            memory_key: memoryKey,
            feedback: feedback,
            title: button.getAttribute("data-memory-title") || "",
            source: "panel"
          }})
        }})
          .then(function (response) {{
            return response.json().catch(function () {{
              return null;
            }}).then(function (payload) {{
              if (!response.ok || !payload || payload.ok === false) {{
                throw new Error((payload && payload.error) || ("HTTP " + response.status));
              }}
              return payload;
            }});
          }})
          .then(function (payload) {{
            const savedFeedback = payload && payload.feedback ? payload.feedback.feedback : feedback;
            updateMemoryFeedbackRow(row, savedFeedback);
            moveMemoryFeedbackCard(row, savedFeedback);
            const reloadAfterMs = Number((payload && payload.reload_after_ms) || 0);
            if (reloadAfterMs > 0) {{
              window.setTimeout(function () {{
                window.location.reload();
              }}, Math.max(reloadAfterMs, 1200));
            }}
          }})
          .catch(function () {{
            setMemoryFeedbackStatus(
              row,
              currentLanguage === "en" ? "Save failed" : "保存失败"
            );
          }})
          .finally(function () {{
            buttons.forEach(function (candidate) {{
              candidate.disabled = false;
            }});
          }});
      }}

      function wireMemoryFeedbackActions() {{
        document.addEventListener("click", function (event) {{
          const button = event.target.closest("[data-memory-feedback]");
          if (!button) {{
            return;
          }}
          event.preventDefault();
          event.stopPropagation();
          submitMemoryFeedback(button);
        }});
      }}

      function wireFinderOpenActions() {{
        document.addEventListener("click", function (event) {{
          const button = event.target.closest("[data-open-finder-path]");
          if (!button) {{
            return;
          }}
          event.preventDefault();
          event.stopPropagation();
          openFinderPath(button);
        }});
      }}

      function wireExternalPanelLinks() {{
        document.addEventListener("click", function (event) {{
          const target = event.target;
          const link = target && target.closest ? target.closest("a[href]") : null;
          if (!link) {{
            return;
          }}
          const href = link.href || link.getAttribute("href") || "";
          if (!shouldOpenOutsidePanel(href)) {{
            return;
          }}
          if (postExternalOpen(href)) {{
            event.preventDefault();
            event.stopPropagation();
          }}
        }}, true);
      }}

      function wireWindowResumeActions() {{
        document.addEventListener("click", function (event) {{
          const modeButton = event.target.closest("[data-window-summary-mode]");
          if (modeButton) {{
            event.preventDefault();
            event.stopPropagation();
            const root = modeButton.closest(".window-summary-mode-root");
            const mode = modeButton.getAttribute("data-window-summary-mode") || "ai";
            if (root && (mode === "ai" || mode === "raw")) {{
              root.setAttribute("data-summary-mode", mode);
              root.querySelectorAll("[data-window-summary-mode]").forEach(function (button) {{
                button.setAttribute(
                  "aria-pressed",
                  button.getAttribute("data-window-summary-mode") === mode ? "true" : "false"
                );
              }});
            }}
            return;
          }}
          const copyButton = event.target.closest("[data-window-resume-copy]");
          if (copyButton) {{
            event.preventDefault();
            event.stopPropagation();
            const command = copyButton.getAttribute("data-resume-command") || "";
            copyText(command).then(function () {{
              flashButtonLabel(
                copyButton,
                copyButton.getAttribute("data-copied-label") || t("已复制")
              );
            }}).catch(function () {{
              flashButtonLabel(
                copyButton,
                copyButton.getAttribute("data-error-label") || t("复制失败")
              );
            }});
            return;
          }}
          const reviewButton = event.target.closest("[data-window-review-copy]");
          if (reviewButton) {{
            event.preventDefault();
            event.stopPropagation();
            const promptTarget = reviewButton.getAttribute("data-review-prompt-target") || "";
            const promptNode = promptTarget ? document.getElementById(promptTarget) : null;
            const prompt = promptNode
              ? promptNode.textContent
              : (reviewButton.getAttribute("data-review-prompt") || "");
            copyText(prompt).then(function () {{
              flashButtonLabel(
                reviewButton,
                reviewButton.getAttribute("data-copied-label") || t("已复制")
              );
            }}).catch(function () {{
              flashButtonLabel(
                reviewButton,
                reviewButton.getAttribute("data-error-label") || t("复制失败")
              );
            }});
            return;
          }}
          const claudeDesktopButton = event.target.closest("[data-window-resume-claude-desktop]");
          if (claudeDesktopButton) {{
            event.preventDefault();
            event.stopPropagation();
            openClaudeDesktopResume(claudeDesktopButton);
            return;
          }}
          const openButton = event.target.closest("[data-window-resume-open]");
          if (openButton) {{
            event.preventDefault();
            event.stopPropagation();
            openCodexDesktopResume(openButton);
          }}
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
        function getHashTargetId() {{
          const rawHash = window.location.hash || "";
          if (!rawHash || rawHash.length < 2) {{
            return "";
          }}
          try {{
            return decodeURIComponent(rawHash.slice(1));
          }} catch (error) {{
            return rawHash.slice(1);
          }}
        }}
        function syncActiveFromHash() {{
          const targetId = getHashTargetId();
          if (!targetId || !document.getElementById(targetId)) {{
            return false;
          }}
          setActiveSideNav(targetId);
          return true;
        }}
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
          link.addEventListener("click", function (event) {{
            const targetId = link.getAttribute("data-nav-target") || "";
            const target = document.getElementById(targetId);
            if (!target) {{
              return;
            }}
            event.preventDefault();
            setActiveSideNav(targetId);
            target.scrollIntoView({{ behavior: "smooth", block: "start" }});
            try {{
              window.history.replaceState(null, "", "#" + targetId);
            }} catch (error) {{
              window.location.hash = targetId;
            }}
            window.setTimeout(function () {{
              setActiveSideNav(targetId);
              updateActiveTarget();
            }}, 220);
          }});
        }});
        window.addEventListener("scroll", updateActiveTarget, {{ passive: true }});
        window.addEventListener("resize", updateActiveTarget);
        window.addEventListener("hashchange", function () {{
          syncActiveFromHash();
          window.setTimeout(updateActiveTarget, 220);
        }});
        if (!syncActiveFromHash()) {{
          updateActiveTarget();
        }}
      }}

      function resetPageHorizontalScroll() {{
        const currentX = window.scrollX || document.documentElement.scrollLeft || document.body.scrollLeft || 0;
        if (!currentX) {{
          return;
        }}
        const currentY = window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0;
        window.scrollTo(0, currentY);
        document.documentElement.scrollLeft = 0;
        document.body.scrollLeft = 0;
      }}

      function wireHorizontalScrollLock() {{
        let resetQueued = false;
        function queueReset() {{
          if (resetQueued) {{
            return;
          }}
          resetQueued = true;
          window.requestAnimationFrame(function () {{
            resetQueued = false;
            resetPageHorizontalScroll();
          }});
        }}
        resetPageHorizontalScroll();
        window.setTimeout(resetPageHorizontalScroll, 0);
        window.setTimeout(resetPageHorizontalScroll, 160);
        window.addEventListener("resize", queueReset);
        window.addEventListener("hashchange", function () {{
          window.setTimeout(resetPageHorizontalScroll, 0);
        }});
        window.addEventListener("scroll", function () {{
          const currentX = window.scrollX || document.documentElement.scrollLeft || document.body.scrollLeft || 0;
          if (currentX) {{
            queueReset();
          }}
        }}, {{ passive: true }});
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

      function decodedHashTargetId() {{
        const rawHash = window.location.hash || "";
        if (!rawHash || rawHash.length < 2) {{
          return "";
        }}
        try {{
          return decodeURIComponent(rawHash.slice(1));
        }} catch (error) {{
          return rawHash.slice(1);
        }}
      }}

      function revealWindowCardById(targetId, shouldScroll) {{
        if (!targetId) {{
          return false;
        }}
        const target = document.getElementById(targetId);
        if (!target || !target.classList || !target.classList.contains("window-card")) {{
          return false;
        }}
        target.open = true;
        target.classList.remove("is-context-highlight");
        void target.offsetWidth;
        target.classList.add("is-context-highlight");
        if (shouldScroll) {{
          target.scrollIntoView({{ behavior: "smooth", block: "start" }});
        }}
        window.setTimeout(function () {{
          target.classList.remove("is-context-highlight");
        }}, 2200);
        return true;
      }}

      function wireProjectContextWindowLinks() {{
        document.addEventListener("click", function (event) {{
          const link = event.target.closest("[data-window-target]");
          if (!link) {{
            return;
          }}
          const targetId = link.getAttribute("data-window-target") || "";
          if (!targetId || !document.getElementById(targetId)) {{
            return;
          }}
          event.preventDefault();
          revealWindowCardById(targetId, true);
          try {{
            window.history.replaceState(null, "", "#" + targetId);
          }} catch (error) {{
            window.location.hash = targetId;
          }}
        }});
        window.addEventListener("hashchange", function () {{
          revealWindowCardById(decodedHashTargetId(), false);
        }});
        revealWindowCardById(decodedHashTargetId(), false);
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
        if (elements.tokenFilterPanel) {{
          elements.tokenFilterPanel.classList.toggle("is-loading", isLoading);
        }}
        [
          elements.tokenStartDateInput,
          elements.tokenEndDateInput,
          elements.tokenResetButton,
        ].forEach(function (control) {{
          if (control) {{
            control.disabled = isLoading;
          }}
        }});
        elements.tokenProviderButtons.concat(elements.tokenGroupButtons).forEach(function (button) {{
          button.disabled = isLoading;
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
        if (messageKey === "offline_service") {{
          return t("本地 Token 服务未启动。请运行 openrelix open panel 后再点实时刷新。");
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

      function setAssetRefreshLoading(isLoading) {{
        if (elements.assetRefreshButton) {{
          elements.assetRefreshButton.classList.toggle("is-loading", isLoading);
          elements.assetRefreshButton.disabled = isLoading;
        }}
        if (elements.assetRefreshLabel) {{
          elements.assetRefreshLabel.textContent = isLoading ? t("正在刷新资产层") : t("刷新资产层");
        }}
      }}

      function setAssetRefreshStatus(kind, messageKey) {{
        if (!elements.assetRefreshStatus) {{
          return;
        }}
        elements.assetRefreshStatus.dataset.kind = kind || "";
        elements.assetRefreshStatus.textContent = messageKey ? t(messageKey) : "";
      }}

      function refreshAssetLayer() {{
        const endpoint = openrelixMetaAttr("data-asset-refresh-endpoint");
        const token = openrelixMetaAttr("data-update-token");
        if (!endpoint || !token || !window.fetch) {{
          setAssetRefreshStatus("error", "本地服务未启动。请运行 openrelix open panel 后再刷新资产层。");
          return;
        }}
        const headers = {{ "Content-Type": "application/json" }};
        headers["X-OpenRelix-Token"] = token;
        let shouldReload = false;
        setAssetRefreshLoading(true);
        setAssetRefreshStatus("loading", "正在刷新资产层，通常需要几十秒…");
        fetch(endpoint, {{
          method: "POST",
          headers: headers,
          body: JSON.stringify({{}})
        }})
          .then(function (response) {{
            return response.json().catch(function () {{
              return null;
            }}).then(function (payload) {{
              if (!response.ok || !payload || payload.ok === false) {{
                throw new Error((payload && payload.error) || ("HTTP " + response.status));
              }}
              return payload;
            }});
          }})
          .then(function () {{
            shouldReload = true;
            setAssetRefreshStatus("success", "资产层已刷新，正在重新载入面板。");
            window.setTimeout(function () {{
              window.location.reload();
            }}, 700);
          }})
          .catch(function (error) {{
            const message = error && String(error.message || "");
            const offline = !message || error.name === "TypeError" || message.indexOf("Failed to fetch") >= 0;
            setAssetRefreshStatus(
              "error",
              offline
                ? "本地服务未启动。请运行 openrelix open panel 后再刷新资产层。"
                : "资产层刷新失败，稍后重试。"
            );
          }})
          .finally(function () {{
            if (!shouldReload) {{
              setAssetRefreshLoading(false);
            }}
          }});
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
        if (label.includes("缓存写入") || label.includes("cache write") || label.includes("cache creation")) {{
          return "token-cache-write";
        }}
        if (label.includes("缓存读取") || label.includes("cache read") || label.includes("cached")) {{
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

      function tokenRowContainsText(row, needles) {{
        const haystackParts = [
          row && row.label,
          row && row.title,
          row && row.meta,
          row && row.details_heading,
        ];
        if (row && Array.isArray(row.details)) {{
          row.details.forEach(function (detail) {{
            haystackParts.push(detail && detail.label);
            haystackParts.push(detail && detail.title);
            haystackParts.push(detail && detail.meta);
          }});
        }}
        const haystack = haystackParts
          .filter(function (value) {{ return value !== null && value !== undefined; }})
          .join(" ")
          .toLowerCase();
        return needles.some(function (needle) {{
          return haystack.includes(String(needle).toLowerCase());
        }});
      }}

      function normalizeTodayTokenBreakdown(rows) {{
        const normalized = (Array.isArray(rows) ? rows : []).map(function (row) {{
          const next = Object.assign({{}}, row);
          if (Array.isArray(row.details)) {{
            next.details = row.details.map(function (detail) {{
              return Object.assign({{}}, detail);
            }});
          }}
          return next;
        }});
        const inputIndex = normalized.findIndex(function (row) {{
          const label = String(row.label || "").toLowerCase();
          return (label.includes("输入") || label.includes("input")) &&
            !label.includes("缓存") &&
            !label.includes("cached");
        }});
        const cachedRow = normalized.find(function (row) {{
          const label = String(row.label || "").toLowerCase();
          return (label.includes("缓存读取") || label.includes("cache read") || label.includes("cached")) &&
            !label.includes("缓存写入") &&
            !label.includes("cache write") &&
            !label.includes("cache creation");
        }});
        const cacheCreateRow = normalized.find(function (row) {{
          const label = String(row.label || "").toLowerCase();
          return label.includes("缓存写入") || label.includes("cache write") || label.includes("cache creation");
        }});
        if (inputIndex < 0 || !cachedRow) {{
          return normalized;
        }}
        const inputRow = normalized[inputIndex];
        const inputValue = Number(inputRow.value) || 0;
        const cachedValue = Number(cachedRow.value) || 0;
        const cacheCreateValue = Number(cacheCreateRow && cacheCreateRow.value) || 0;
        const rowAlreadyUncached = tokenRowContainsText(inputRow, ["无缓存", "uncached"]);
        const rowLooksTotalInput = tokenRowContainsText(inputRow, ["总输入", "total input"]);
        if (!rowAlreadyUncached && rowLooksTotalInput && inputValue >= cachedValue + cacheCreateValue && (cachedValue > 0 || cacheCreateValue > 0)) {{
          const uncachedInput = Math.max(inputValue - cachedValue - cacheCreateValue, 0);
          inputRow.value = uncachedInput;
          inputRow.display = compactTokenValue(uncachedInput);
          if (Array.isArray(inputRow.details) && inputRow.details.length) {{
            inputRow.details[0].value = uncachedInput;
            inputRow.details[0].title = (currentLanguage === "en" ? "Input: " : "输入：") + compactTokenValue(uncachedInput);
            inputRow.details[0].meta = currentLanguage === "en" ? "Uncached input tokens" : "无缓存输入 Token";
          }}
          const cachedShare = inputValue > 0 ? (cachedValue / inputValue) * 100 : null;
          if (Array.isArray(cachedRow.details) && cachedRow.details.length) {{
            cachedRow.label = currentLanguage === "en" ? "Cache Read" : "缓存读取";
            cachedRow.details[0].label = currentLanguage === "en" ? "Cache Read" : "缓存读取";
            cachedRow.details[0].title = (currentLanguage === "en" ? "Cache Read: " : "缓存读取：") + compactTokenValue(cachedValue);
            cachedRow.details[0].meta = currentLanguage === "en"
              ? formatPercentValue(cachedShare, 0, false) + " of total input"
              : "占总输入 " + formatPercentValue(cachedShare, 0, false);
          }}
        }}
        return normalized;
      }}

      function normalizeTokenProvider(value) {{
        const text = String(value || "all").toLowerCase().replace(/_/g, "-");
        if (["codex", "claude"].includes(text)) {{
          return text;
        }}
        if (text === "cc" || text === "claude-code") {{
          return "claude";
        }}
        return "all";
      }}

      function normalizeTokenGroupBy(value) {{
        const text = String(value || "day").toLowerCase().replace(/_/g, "-");
        return text === "month" || text === "monthly" ? "month" : "day";
      }}

      function parseTokenInputDate(value) {{
        const match = String(value || "").match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})$/);
        if (!match) {{
          return null;
        }}
        return new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
      }}

      function tokenDateRangeDays(filters) {{
        const activeFilters = filters || {{}};
        const start = parseTokenInputDate(activeFilters.startDate);
        const end = parseTokenInputDate(activeFilters.endDate);
        if (!start || !end || start > end) {{
          return null;
        }}
        const dayMs = 24 * 60 * 60 * 1000;
        return Math.max(Math.round((end - start) / dayMs) + 1, 1);
      }}

      function tokenEffectiveWindowDays(filters, fallbackWindowDays) {{
        return tokenDateRangeDays(filters) || Math.max(Number(fallbackWindowDays) || {window_days}, 1);
      }}

      function tokenRequestCacheKey(filters, windowDays) {{
        const activeFilters = filters || {{}};
        return [
          normalizeTokenProvider(activeFilters.provider),
          String(activeFilters.startDate || ""),
          String(activeFilters.endDate || ""),
          String(tokenEffectiveWindowDays(activeFilters, windowDays)),
        ].join("|");
      }}

      function tokenUsageMatchesRequestFilters(tokenUsage, filters) {{
        if (!tokenUsage) {{
          return false;
        }}
        const activeFilters = filters || {{}};
        const providerMatches =
          normalizeTokenProvider(tokenUsage.provider) === normalizeTokenProvider(activeFilters.provider);
        if (!providerMatches) {{
          return false;
        }}
        if (activeFilters.startDate && String(tokenUsage.range_start || "") !== activeFilters.startDate) {{
          return false;
        }}
        if (activeFilters.endDate && String(tokenUsage.range_end || "") !== activeFilters.endDate) {{
          return false;
        }}
        return true;
      }}

      function getCachedTokenUsage(cacheKey) {{
        const entry = state.tokenUsageCache ? state.tokenUsageCache[cacheKey] : null;
        if (!entry || !entry.tokenUsage) {{
          return null;
        }}
        if (Date.now() - (Number(entry.cachedAt) || 0) > 90 * 1000) {{
          delete state.tokenUsageCache[cacheKey];
          return null;
        }}
        return entry.tokenUsage;
      }}

      function rememberTokenUsage(cacheKey, tokenUsage) {{
        if (!cacheKey || !tokenUsage || !tokenUsage.available) {{
          return;
        }}
        state.tokenUsageCache[cacheKey] = {{
          cachedAt: Date.now(),
          tokenUsage: tokenUsage,
        }};
      }}

      function tokenProviderLabel(provider) {{
        const normalized = normalizeTokenProvider(provider);
        if (normalized === "codex") {{
          return "Codex";
        }}
        if (normalized === "claude") {{
          return "Claude";
        }}
        return currentLanguage === "en" ? "All Sources" : "全部来源";
      }}

      function tokenGroupLabel(groupBy) {{
        return normalizeTokenGroupBy(groupBy) === "month"
          ? (currentLanguage === "en" ? "Monthly" : "按月")
          : (currentLanguage === "en" ? "Daily" : "按日");
      }}

      function tokenFilterRangeLabel(filters, tokenUsage) {{
        const provider = normalizeTokenProvider(filters.provider);
        const groupBy = normalizeTokenGroupBy(filters.groupBy);
        const startDate = String(filters.startDate || "");
        const endDate = String(filters.endDate || "");
        const usageMatchesFilter = tokenUsage &&
          normalizeTokenProvider(tokenUsage.provider) === provider &&
          normalizeTokenGroupBy(tokenUsage.group_by) === groupBy &&
          (!startDate || String(tokenUsage.range_start || "") === startDate) &&
          (!endDate || String(tokenUsage.range_end || "") === endDate);
        if (usageMatchesFilter && tokenUsage.range_label) {{
          return tokenUsage.range_label;
        }}
        let startText = startDate;
        let endText = endDate;
        if (groupBy === "month") {{
          startText = startText ? startText.slice(0, 7) : "";
          endText = endText ? endText.slice(0, 7) : "";
        }}
        if (startText && endText && startText !== endText) {{
          return currentLanguage === "en" ? startText + " to " + endText : startText + " 至 " + endText;
        }}
        return startText || endText || "";
      }}

      function parseTokenMonthContextDate(value) {{
        const match = String(value || "").match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})$/);
        if (!match) {{
          return null;
        }}
        const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
        return Number.isNaN(date.getTime()) ? null : date;
      }}

      function tokenShortDateMonthKey(text, context) {{
        const match = String(text || "").trim().match(/^(\\d{{2}})-(\\d{{2}})$/);
        if (!match) {{
          return "";
        }}
        const month = Number(match[1]);
        const day = Number(match[2]);
        if (month < 1 || month > 12 || day < 1 || day > 31) {{
          return "";
        }}
        const rangeStart = parseTokenMonthContextDate(context && context.range_start);
        const rangeEnd = parseTokenMonthContextDate(context && context.range_end);
        const startYear = rangeStart ? rangeStart.getUTCFullYear() : (rangeEnd ? rangeEnd.getUTCFullYear() : null);
        const endYear = rangeEnd ? rangeEnd.getUTCFullYear() : startYear;
        if (!startYear || !endYear) {{
          return "";
        }}
        for (let year = startYear; year <= endYear; year += 1) {{
          const candidate = new Date(Date.UTC(year, month - 1, day));
          if (
            candidate.getUTCFullYear() !== year ||
            candidate.getUTCMonth() !== month - 1 ||
            candidate.getUTCDate() !== day
          ) {{
            continue;
          }}
          if ((!rangeStart || candidate >= rangeStart) && (!rangeEnd || candidate <= rangeEnd)) {{
            return String(year) + "-" + String(month).padStart(2, "0");
          }}
        }}
        return "";
      }}

      function tokenShortDateIsoKey(text, context) {{
        const match = String(text || "").trim().match(/^(\\d{{2}})-(\\d{{2}})$/);
        if (!match) {{
          return "";
        }}
        const month = Number(match[1]);
        const day = Number(match[2]);
        if (month < 1 || month > 12 || day < 1 || day > 31) {{
          return "";
        }}
        const rangeStart = parseTokenMonthContextDate(context && context.range_start);
        const rangeEnd = parseTokenMonthContextDate(context && context.range_end);
        const startYear = rangeStart ? rangeStart.getUTCFullYear() : (rangeEnd ? rangeEnd.getUTCFullYear() : null);
        const endYear = rangeEnd ? rangeEnd.getUTCFullYear() : startYear;
        if (!startYear || !endYear) {{
          return "";
        }}
        for (let year = startYear; year <= endYear; year += 1) {{
          const candidate = new Date(Date.UTC(year, month - 1, day));
          if (
            candidate.getUTCFullYear() !== year ||
            candidate.getUTCMonth() !== month - 1 ||
            candidate.getUTCDate() !== day
          ) {{
            continue;
          }}
          if ((!rangeStart || candidate >= rangeStart) && (!rangeEnd || candidate <= rangeEnd)) {{
            return String(year) + "-" + String(month).padStart(2, "0") + "-" + String(day).padStart(2, "0");
          }}
        }}
        return "";
      }}

      function tokenRowMonthKey(row, context) {{
        const candidates = [
          row && row.date,
          row && row.raw_date,
          row && row.sort_key,
          row && row.label,
        ];
        for (const candidate of candidates) {{
          const text = String(candidate || "").trim();
          const match = text.match(/^(\\d{{4}})-(\\d{{2}})/);
          if (match) {{
            return match[1] + "-" + match[2];
          }}
          const shortMonth = tokenShortDateMonthKey(text, context);
          if (shortMonth) {{
            return shortMonth;
          }}
        }}
        return "";
      }}

      function tokenRowDayKey(row, context) {{
        const candidates = [
          row && row.date,
          row && row.raw_date,
          row && row.sort_key,
          row && row.label,
        ];
        for (const candidate of candidates) {{
          const text = String(candidate || "").trim();
          const match = text.match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})/);
          if (match) {{
            return match[1] + "-" + match[2] + "-" + match[3];
          }}
          const shortDay = tokenShortDateIsoKey(text, context);
          if (shortDay) {{
            return shortDay;
          }}
        }}
        return "";
      }}

      function tokenRowNumericValue(row, keys) {{
        const source = row || {{}};
        for (const key of keys) {{
          if (Object.prototype.hasOwnProperty.call(source, key)) {{
            const value = Number(source[key]);
            return Number.isFinite(value) ? value : 0;
          }}
        }}
        return null;
      }}

      function tokenRowDetailValue(row, matcher) {{
        const details = Array.isArray(row && row.details) ? row.details : [];
        for (const detail of details) {{
          const label = String((detail && detail.label) || "").toLowerCase();
          const title = String((detail && detail.title) || "").toLowerCase();
          if (matcher(label, title)) {{
            return Number(detail.value) || 0;
          }}
        }}
        return 0;
      }}

      function tokenRowBreakdownValues(row) {{
        const total = tokenRowNumericValue(row, ["totalTokens", "total_tokens", "value"]);
        const cachedDirect = tokenRowNumericValue(row, ["cachedInputTokens", "cached_input_tokens", "cacheReadTokens", "cache_read_tokens"]);
        const cacheCreateDirect = tokenRowNumericValue(row, ["cacheCreationTokens", "cache_creation_tokens", "cacheWriteTokens", "cache_write_tokens"]);
        const outputDirect = tokenRowNumericValue(row, ["outputTokens", "output_tokens"]);
        const reasoningDirect = tokenRowNumericValue(row, ["reasoningOutputTokens", "reasoning_output_tokens"]);
        const totalInputDirect = tokenRowNumericValue(row, ["totalInputTokens", "total_input_tokens", "inputTokens", "input_tokens"]);
        const cached = cachedDirect !== null ? cachedDirect : tokenRowDetailValue(row, function (label, title) {{
          return label.includes("缓存读取") || label.includes("cache read") || label === "cached" || title.startsWith("缓存读取") || title.startsWith("cache read");
        }});
        const cacheCreate = cacheCreateDirect !== null ? cacheCreateDirect : tokenRowDetailValue(row, function (label, title) {{
          return label.includes("缓存写入") || label.includes("cache write") || label.includes("cache creation") || title.startsWith("缓存写入") || title.startsWith("cache write");
        }});
        const inputDirect = tokenRowNumericValue(row, ["uncachedInputTokens", "uncached_input_tokens"]);
        const input = inputDirect !== null
          ? inputDirect
          : (totalInputDirect !== null
            ? Math.max(totalInputDirect - cached - cacheCreate, 0)
            : tokenRowDetailValue(row, function (label, title) {{
              return label === "输入" || label === "input" || title.startsWith("输入") || title.startsWith("input");
            }}));
        const output = outputDirect !== null ? outputDirect : tokenRowDetailValue(row, function (label, title) {{
          return label === "输出" || label === "output" || title.startsWith("输出") || title.startsWith("output");
        }});
        const reasoning = reasoningDirect !== null ? reasoningDirect : tokenRowDetailValue(row, function (label, title) {{
          return label.includes("推理") || label.includes("reasoning") || title.startsWith("推理") || title.startsWith("reasoning");
        }});
        return {{
          total: total !== null ? total : 0,
          input: input,
          cached: cached,
          cacheCreate: cacheCreate,
          output: output,
          reasoning: reasoning,
        }};
      }}

      function buildTokenDetail(label, value, meta) {{
        const sep = currentLanguage === "en" ? ": " : "：";
        return {{
          label: label,
          value: value,
          title: label + sep + compactTokenValue(value),
          meta: meta,
        }};
      }}

      function tokenBreakdownDetailsFromValues(values, heading) {{
        const totalInput = (Number(values.input) || 0) + (Number(values.cached) || 0) + (Number(values.cacheCreate) || 0);
        const total = Number(values.total) || 0;
        const cacheShare = totalInput > 0 ? (Number(values.cached) || 0) / totalInput * 100 : null;
        const cacheCreateShare = total > 0 ? (Number(values.cacheCreate) || 0) / total * 100 : null;
        const outputShare = total > 0 ? (Number(values.output) || 0) / total * 100 : null;
        const reasoningShare = total > 0 ? (Number(values.reasoning) || 0) / total * 100 : null;
        const inputLabel = currentLanguage === "en" ? "Input" : "输入";
        const cacheLabel = currentLanguage === "en" ? "Cache Read" : "缓存读取";
        const cacheCreateLabel = currentLanguage === "en" ? "Cache Write" : "缓存写入";
        const outputLabel = currentLanguage === "en" ? "Output" : "输出";
        const reasoningLabel = currentLanguage === "en" ? "Reasoning output" : "推理输出";
        const details = [
          buildTokenDetail(
            inputLabel,
            Number(values.input) || 0,
            currentLanguage === "en" ? "Uncached input tokens" : "无缓存输入 Token"
          ),
          buildTokenDetail(
            cacheLabel,
            Number(values.cached) || 0,
            currentLanguage === "en"
              ? formatPercentValue(cacheShare, 0, false) + " of total input"
              : "占总输入 " + formatPercentValue(cacheShare, 0, false)
          ),
        ];
        if ((Number(values.cacheCreate) || 0) > 0) {{
          details.push(
            buildTokenDetail(
              cacheCreateLabel,
              Number(values.cacheCreate) || 0,
              currentLanguage === "en"
                ? formatPercentValue(cacheCreateShare, 1, false) + " of total"
                : "占总量 " + formatPercentValue(cacheCreateShare, 1, false)
            )
          );
        }}
        details.push(
          buildTokenDetail(
            outputLabel,
            Number(values.output) || 0,
            currentLanguage === "en"
              ? formatPercentValue(outputShare, 1, false) + " of total"
              : "占总量 " + formatPercentValue(outputShare, 1, false)
          ),
          buildTokenDetail(
            reasoningLabel,
            Number(values.reasoning) || 0,
            currentLanguage === "en"
              ? formatPercentValue(reasoningShare, 1, false) + " of total"
              : "占总量 " + formatPercentValue(reasoningShare, 1, false)
          )
        );
        return details;
      }}

      function buildTokenBreakdownRows(values) {{
        const totalInput = (Number(values.input) || 0) + (Number(values.cached) || 0) + (Number(values.cacheCreate) || 0);
        const total = Number(values.total) || 0;
        const cacheShare = totalInput > 0 ? (Number(values.cached) || 0) / totalInput * 100 : null;
        const cacheCreateShare = total > 0 ? (Number(values.cacheCreate) || 0) / total * 100 : null;
        const outputShare = total > 0 ? (Number(values.output) || 0) / total * 100 : null;
        const reasoningShare = total > 0 ? (Number(values.reasoning) || 0) / total * 100 : null;
        const inputLabel = currentLanguage === "en" ? "Input" : "输入";
        const cacheLabel = currentLanguage === "en" ? "Cache Read" : "缓存读取";
        const cacheCreateLabel = currentLanguage === "en" ? "Cache Write" : "缓存写入";
        const outputLabel = currentLanguage === "en" ? "Output" : "输出";
        const reasoningLabel = currentLanguage === "en" ? "Reasoning output" : "推理输出";
        const rows = [
          {{
            label: inputLabel,
            value: Number(values.input) || 0,
            display: compactTokenValue(values.input),
            tone: "token-input",
            details: [buildTokenDetail(inputLabel, Number(values.input) || 0, currentLanguage === "en" ? "Uncached input tokens" : "无缓存输入 Token")],
            details_heading: currentLanguage === "en" ? "Input details" : "输入详情",
          }},
          {{
            label: cacheLabel,
            value: Number(values.cached) || 0,
            display: compactTokenValue(values.cached),
            tone: "token-cache",
            details: [buildTokenDetail(cacheLabel, Number(values.cached) || 0, currentLanguage === "en" ? formatPercentValue(cacheShare, 0, false) + " of total input" : "占总输入 " + formatPercentValue(cacheShare, 0, false))],
            details_heading: currentLanguage === "en" ? "Cache details" : "缓存详情",
          }},
        ];
        if ((Number(values.cacheCreate) || 0) > 0) {{
          rows.push({{
            label: cacheCreateLabel,
            value: Number(values.cacheCreate) || 0,
            display: compactTokenValue(values.cacheCreate),
            tone: "token-cache-write",
            details: [buildTokenDetail(cacheCreateLabel, Number(values.cacheCreate) || 0, currentLanguage === "en" ? formatPercentValue(cacheCreateShare, 1, false) + " of total" : "占总量 " + formatPercentValue(cacheCreateShare, 1, false))],
            details_heading: currentLanguage === "en" ? "Cache write details" : "缓存写入详情",
          }});
        }}
        rows.push(
          {{
            label: outputLabel,
            value: Number(values.output) || 0,
            display: compactTokenValue(values.output),
            tone: "token-output",
            details: [buildTokenDetail(outputLabel, Number(values.output) || 0, currentLanguage === "en" ? formatPercentValue(outputShare, 1, false) + " of total" : "占总量 " + formatPercentValue(outputShare, 1, false))],
            details_heading: currentLanguage === "en" ? "Output details" : "输出详情",
          }},
          {{
            label: reasoningLabel,
            value: Number(values.reasoning) || 0,
            display: compactTokenValue(values.reasoning),
            tone: "token-reasoning",
            details: [buildTokenDetail(reasoningLabel, Number(values.reasoning) || 0, currentLanguage === "en" ? formatPercentValue(reasoningShare, 1, false) + " of total" : "占总量 " + formatPercentValue(reasoningShare, 1, false))],
            details_heading: currentLanguage === "en" ? "Reasoning details" : "推理详情",
          }},
        );
        return rows;
      }}

      function aggregateDailyRowsByMonth(rows, tokenUsage) {{
        const buckets = new Map();
        (Array.isArray(rows) ? rows : []).forEach(function (row) {{
          const key = tokenRowMonthKey(row, tokenUsage || {{}});
          if (!key) {{
            return;
          }}
          if (!buckets.has(key)) {{
            buckets.set(key, {{
              label: key,
              date: key + "-01",
              raw_date: key,
              sort_key: key,
              group_by: "month",
              day_count: 0,
              active_day_count: 0,
              value: 0,
              costUSD: 0,
              input: 0,
              cached: 0,
              cacheCreate: 0,
              output: 0,
              reasoning: 0,
            }});
          }}
          const bucket = buckets.get(key);
          const rowValues = tokenRowBreakdownValues(row);
          const rowValue = Number(rowValues.total) || 0;
          bucket.day_count += Number(row.day_count) || 1;
          bucket.active_day_count += Number(row.active_day_count) || (rowValue > 0 ? 1 : 0);
          bucket.value += rowValue;
          bucket.costUSD += extractTokenRowCost(row);
          bucket.input += Number(rowValues.input) || 0;
          bucket.cached += Number(rowValues.cached) || 0;
          bucket.cacheCreate += Number(rowValues.cacheCreate) || 0;
          bucket.output += Number(rowValues.output) || 0;
          bucket.reasoning += Number(rowValues.reasoning) || 0;
        }});
        return Array.from(buckets.values()).sort(function (left, right) {{
          return String(left.sort_key).localeCompare(String(right.sort_key));
        }}).map(function (bucket) {{
          const values = {{
            total: bucket.value,
            input: bucket.input,
            cached: bucket.cached,
            cacheCreate: bucket.cacheCreate,
            output: bucket.output,
            reasoning: bucket.reasoning,
          }};
          return Object.assign({{}}, bucket, {{
            totalTokens: bucket.value,
            totalInputTokens: bucket.input + bucket.cached + bucket.cacheCreate,
            uncachedInputTokens: bucket.input,
            cachedInputTokens: bucket.cached,
            cacheCreationTokens: bucket.cacheCreate,
            outputTokens: bucket.output,
            reasoningOutputTokens: bucket.reasoning,
            display: compactTokenWithCostValue(bucket.value, bucket.costUSD),
            token_display: compactTokenValue(bucket.value),
            cost_display: formatUsdValue(bucket.costUSD),
            details: tokenBreakdownDetailsFromValues(values),
            details_heading: currentLanguage === "en"
              ? "Token breakdown for " + bucket.label
              : bucket.label + " Token 构成",
          }});
        }});
      }}

      function appendZeroTokenEndRow(rows, tokenUsage, targetGroup) {{
        const context = tokenUsage || {{}};
        const endDateText = String(
          context.range_end ||
          (state.tokenFilters && state.tokenFilters.endDate) ||
          ""
        );
        const match = endDateText.match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})$/);
        if (!match) {{
          return rows.slice();
        }}
        const group = normalizeTokenGroupBy(targetGroup);
        const nextRows = rows.slice();
        const endIso = match[1] + "-" + match[2] + "-" + match[3];
        const endMonth = match[1] + "-" + match[2];
        if (group === "month") {{
          const hasMonth = nextRows.some(function (row) {{
            return tokenRowMonthKey(row, context) === endMonth;
          }});
          if (hasMonth) {{
            return nextRows;
          }}
        }} else {{
          const hasDay = nextRows.some(function (row) {{
            return tokenRowDayKey(row, context) === endIso;
          }});
          if (hasDay) {{
            return nextRows;
          }}
        }}
        const label = group === "month" ? endMonth : endIso.slice(5);
        const rawDate = group === "month" ? endMonth : endIso;
        const rowDate = group === "month" ? endMonth + "-01" : endIso;
        const zeroValues = {{
          total: 0,
          input: 0,
          cached: 0,
          cacheCreate: 0,
          output: 0,
          reasoning: 0,
        }};
        nextRows.push({{
          label: label,
          date: rowDate,
          raw_date: rawDate,
          sort_key: rawDate,
          group_by: group,
          day_count: 1,
          active_day_count: 0,
          value: 0,
          totalTokens: 0,
          totalInputTokens: 0,
          uncachedInputTokens: 0,
          cachedInputTokens: 0,
          cacheCreationTokens: 0,
          outputTokens: 0,
          reasoningOutputTokens: 0,
          display: compactTokenWithCostValue(0, 0),
          token_display: compactTokenValue(0),
          costUSD: 0,
          cost_display: formatUsdValue(0),
          details: tokenBreakdownDetailsFromValues(zeroValues),
          details_heading: currentLanguage === "en"
            ? "Token breakdown for " + label
            : label + " Token 构成",
          tone: "token-daily-empty",
        }});
        return nextRows.sort(function (left, right) {{
          return String(left.sort_key || "").localeCompare(String(right.sort_key || ""));
        }});
      }}

      function syncTokenFilterControls(tokenUsage) {{
        const filters = state.tokenFilters || {{}};
        const provider = normalizeTokenProvider(filters.provider);
        const groupBy = normalizeTokenGroupBy(filters.groupBy);
        elements.tokenProviderButtons.forEach(function (button) {{
          const active = normalizeTokenProvider(button.getAttribute("data-token-provider")) === provider;
          button.setAttribute("aria-pressed", active ? "true" : "false");
          if (active) {{
            button.setAttribute("data-active", "true");
          }} else {{
            button.removeAttribute("data-active");
          }}
        }});
        elements.tokenGroupButtons.forEach(function (button) {{
          const active = normalizeTokenGroupBy(button.getAttribute("data-token-group")) === groupBy;
          button.setAttribute("aria-pressed", active ? "true" : "false");
          if (active) {{
            button.setAttribute("data-active", "true");
          }} else {{
            button.removeAttribute("data-active");
          }}
        }});
        if (elements.tokenStartDateInput && elements.tokenStartDateInput.value !== (filters.startDate || "")) {{
          elements.tokenStartDateInput.value = filters.startDate || "";
        }}
        if (elements.tokenEndDateInput && elements.tokenEndDateInput.value !== (filters.endDate || "")) {{
          elements.tokenEndDateInput.value = filters.endDate || "";
        }}
        if (elements.tokenFilterSummary) {{
          const rangeLabel = tokenFilterRangeLabel(filters, tokenUsage);
          elements.tokenFilterSummary.textContent = [
            tokenProviderLabel(provider),
            rangeLabel,
            tokenGroupLabel(groupBy),
          ].filter(Boolean).join(" · ");
        }}
      }}

      function setTokenFilterState(nextFilters, shouldRefresh) {{
        const previousFilters = Object.assign({{}}, state.tokenFilters || {{}});
        const previousKey = tokenRequestCacheKey(
          previousFilters,
          (state.tokenUsage && state.tokenUsage.window_days) || {window_days}
        );
        const merged = Object.assign({{}}, state.tokenFilters || {{}}, nextFilters || {{}});
        merged.provider = normalizeTokenProvider(merged.provider);
        merged.groupBy = normalizeTokenGroupBy(merged.groupBy);
        merged.startDate = String(merged.startDate || "").trim();
        merged.endDate = String(merged.endDate || "").trim();
        if (merged.startDate && merged.endDate && merged.startDate > merged.endDate) {{
          const previousStart = merged.startDate;
          merged.startDate = merged.endDate;
          merged.endDate = previousStart;
        }}
        state.tokenFilters = merged;
        syncTokenFilterControls(state.tokenUsage);
        if (shouldRefresh) {{
          const nextKey = tokenRequestCacheKey(
            merged,
            (state.tokenUsage && state.tokenUsage.window_days) || {window_days}
          );
          if (previousKey === nextKey && state.tokenUsage) {{
            updateTokenVisuals(state.tokenUsage, state.tokenSourceKind);
          }} else {{
            refreshTokenUsage(false);
          }}
        }}
      }}

      function resetTokenFilters() {{
        setTokenFilterState(Object.assign({{}}, state.defaultTokenFilters || {{
          provider: "all",
          groupBy: "day",
          startDate: defaultTokenDateRange.startDate,
          endDate: defaultTokenDateRange.endDate,
        }}), true);
      }}

      function openTokenDatePicker(input) {{
        if (!input || input.disabled) {{
          return;
        }}
        try {{
          input.focus({{ preventScroll: true }});
        }} catch (error) {{
          input.focus();
        }}
        if (typeof input.showPicker === "function") {{
          try {{
            input.showPicker();
          }} catch (error) {{}}
        }}
      }}

      function wireTokenDateFieldClicks() {{
        document.querySelectorAll("[data-token-date-field]").forEach(function (field) {{
          const input = field.querySelector(".token-date-input");
          if (!input) {{
            return;
          }}
          field.addEventListener("click", function (event) {{
            if (event.target && event.target.closest && event.target.closest(".token-date-input")) {{
              return;
            }}
            openTokenDatePicker(input);
          }});
          input.addEventListener("click", function () {{
            openTokenDatePicker(input);
          }});
        }});
      }}

      function wireTokenFilters() {{
        elements.tokenProviderButtons.forEach(function (button) {{
          button.addEventListener("click", function () {{
            setTokenFilterState({{ provider: button.getAttribute("data-token-provider") || "all" }}, true);
          }});
        }});
        elements.tokenGroupButtons.forEach(function (button) {{
          button.addEventListener("click", function () {{
            setTokenFilterState({{ groupBy: button.getAttribute("data-token-group") || "day" }}, true);
          }});
        }});
        if (elements.tokenStartDateInput) {{
          elements.tokenStartDateInput.addEventListener("change", function () {{
            setTokenFilterState({{ startDate: elements.tokenStartDateInput.value || "" }}, true);
          }});
        }}
        if (elements.tokenEndDateInput) {{
          elements.tokenEndDateInput.addEventListener("change", function () {{
            setTokenFilterState({{ endDate: elements.tokenEndDateInput.value || "" }}, true);
          }});
        }}
        if (elements.tokenResetButton) {{
          elements.tokenResetButton.addEventListener("click", resetTokenFilters);
        }}
        wireTokenDateFieldClicks();
        syncTokenFilterControls(state.tokenUsage);
      }}

      function deriveTokenSummaryCards(tokenUsage) {{
        const dailyRows = Array.isArray(tokenUsage.daily_rows) ? tokenUsage.daily_rows : [];
        const activeRows = dailyRows.filter(function (row) {{
          return (Number(row.value) || 0) > 0;
        }});
        const latest = dailyRows.length ? dailyRows[dailyRows.length - 1] : null;
        if (!latest) {{
          return [];
        }}

        const cards = [];
        const groupBy = normalizeTokenGroupBy(tokenUsage.group_by);
        const periodUnit = tokenUsage.period_unit || (groupBy === "month"
          ? (currentLanguage === "en" ? "months" : "月")
          : (currentLanguage === "en" ? "days" : "日"));
        const total = dailyRows.reduce(function (sum, row) {{
          return sum + (Number(row.value) || 0);
        }}, 0);
        const totalCost = dailyRows.reduce(function (sum, row) {{
          return sum + extractTokenRowCost(row);
        }}, 0);
        const periodTotal = Number(tokenUsage.period_total_tokens);
        const periodCost = Number(tokenUsage.period_cost_usd);
        const activeCount = Number(tokenUsage.active_period_count) || activeRows.length;
        if (activeRows.length) {{
          cards.push({{
            label: currentLanguage === "en" ? "Period Bill" : "周期账单",
            value: formatUsdValue(Number.isFinite(periodCost) && periodCost > 0 ? periodCost : totalCost),
            caption: currentLanguage === "en"
              ? compactTokenValue(Number.isFinite(periodTotal) && periodTotal > 0 ? periodTotal : total) + " Tokens · ccusage estimate"
              : compactTokenValue(Number.isFinite(periodTotal) && periodTotal > 0 ? periodTotal : total) + " Token · ccusage 估算",
            tone: "neutral",
          }});
        }} else {{
          cards.push({{
            label: currentLanguage === "en" ? "Period Bill" : "周期账单",
            value: "—",
            caption: currentLanguage === "en" ? "No bill data yet" : "暂无账单数据",
            tone: "neutral",
          }});
        }}

        if (activeRows.length) {{
          const periodAverage = Number(tokenUsage.period_average_tokens);
          const average = Number.isFinite(periodAverage) && periodAverage > 0
            ? Math.floor(periodAverage)
            : Math.floor(total / activeRows.length);
          const peak = activeRows.reduce(function (currentPeak, row) {{
            return (Number(row.value) || 0) > (Number(currentPeak.value) || 0) ? row : currentPeak;
          }}, activeRows[0]);
          cards.push({{
            label: groupBy === "month"
              ? (currentLanguage === "en" ? "Monthly Average" : "月均值")
              : (currentLanguage === "en" ? "Daily Average" : "周期日均"),
            value: compactTokenValue(average),
            caption: currentLanguage === "en"
              ? "Across " + activeCount + " " + periodUnit + " with data"
              : "按 " + activeCount + " 个有数据" + periodUnit,
            tone: "neutral",
          }});
          cards.push({{
            label: groupBy === "month"
              ? (currentLanguage === "en" ? "Peak Month" : "峰值月")
              : (currentLanguage === "en" ? "Peak Day" : "峰值日"),
            value: compactTokenValue(Number(peak.value) || 0),
            caption: currentLanguage === "en"
              ? "Peak on " + (peak.label || "")
              : (peak.label || "") + " 最高",
            tone: "neutral",
          }});
        }}

        const latestValues = latest ? tokenRowBreakdownValues(latest) : {{
          input: findTokenBreakdownValue(tokenUsage.today_breakdown, ["输入", "input"]),
          cached: findTokenBreakdownValue(tokenUsage.today_breakdown, ["缓存读取", "cache read", "cached"]),
          cacheCreate: findTokenBreakdownValue(tokenUsage.today_breakdown, ["缓存写入", "cache write", "cache creation"]),
        }};
        const inputTokens = Number(latestValues.input) || 0;
        const cachedInputTokens = Number(latestValues.cached) || 0;
        const cacheCreationTokens = Number(latestValues.cacheCreate) || 0;
        const totalInputTokens = inputTokens + cachedInputTokens + cacheCreationTokens;
        const cachedShare = totalInputTokens > 0 ? (cachedInputTokens / totalInputTokens) * 100 : null;
        cards.push({{
          label: currentLanguage === "en" ? "Cache Read / Total Input" : "缓存读取占总输入",
          value: formatPercentValue(cachedShare, 0, false),
          caption: currentLanguage === "en"
            ? "Cache Read " + compactTokenValue(cachedInputTokens) + " / total input " + compactTokenValue(totalInputTokens)
            : "缓存读取 " + compactTokenValue(cachedInputTokens) + " / 总输入 " + compactTokenValue(totalInputTokens),
          tone: "neutral",
        }});
        return cards;
      }}

      function deriveTokenUsageForGroup(tokenUsage, groupBy, relativeUpdate) {{
        const targetGroup = normalizeTokenGroupBy(groupBy);
        const derived = Object.assign({{}}, tokenUsage || {{}});
        const sourceRows = Array.isArray(derived.daily_rows) ? derived.daily_rows : [];
        const sourceGroup = normalizeTokenGroupBy(derived.group_by);
        const monthContext = Object.assign({{}}, derived, {{
          range_start: derived.range_start || (state.tokenFilters && state.tokenFilters.startDate) || "",
          range_end: derived.range_end || (state.tokenFilters && state.tokenFilters.endDate) || "",
        }});
        let displayRows = targetGroup === "month" && sourceGroup !== "month"
          ? aggregateDailyRowsByMonth(sourceRows, monthContext)
          : sourceRows.slice();
        displayRows = appendZeroTokenEndRow(displayRows, monthContext, targetGroup);
        derived.group_by = targetGroup;
        derived.daily_rows = displayRows;
        const activeRows = displayRows.filter(function (row) {{
          return (Number(row.value) || 0) > 0;
        }});
        const latest = displayRows.length ? displayRows[displayRows.length - 1] : null;
        const total = displayRows.reduce(function (sum, row) {{
          return sum + (Number(row.value) || 0);
        }}, 0);
        const totalCost = displayRows.reduce(function (sum, row) {{
          return sum + extractTokenRowCost(row);
        }}, 0);
        const activeCount = activeRows.length;
        const periodUnit = targetGroup === "month"
          ? (currentLanguage === "en" ? "months" : "月")
          : (currentLanguage === "en" ? "days" : "日");
        const rangeLabel = displayRows.length
          ? (displayRows[0].label === displayRows[displayRows.length - 1].label
            ? displayRows[0].label
            : (currentLanguage === "en"
              ? displayRows[0].label + " to " + displayRows[displayRows.length - 1].label
              : displayRows[0].label + " 至 " + displayRows[displayRows.length - 1].label))
          : (derived.range_label || "");
        derived.period_total_tokens = Number.isFinite(Number(derived.period_total_tokens)) && targetGroup === sourceGroup
          ? Number(derived.period_total_tokens)
          : total;
        derived.period_total_tokens_display = compactTokenValue(derived.period_total_tokens);
        derived.period_cost_usd = Number.isFinite(Number(derived.period_cost_usd)) && targetGroup === sourceGroup
          ? Number(derived.period_cost_usd)
          : totalCost;
        derived.period_cost_display = formatUsdValue(derived.period_cost_usd);
        derived.period_average_tokens = activeCount ? Math.floor(derived.period_total_tokens / activeCount) : 0;
        derived.period_average_tokens_display = compactTokenValue(derived.period_average_tokens);
        derived.period_count = displayRows.length;
        derived.active_period_count = activeCount;
        derived.period_unit = periodUnit;
        derived.range_label = rangeLabel || derived.range_label || "";
        if (latest) {{
          const latestValues = tokenRowBreakdownValues(latest);
          derived.today_total_tokens = latestValues.total;
          derived.today_total_tokens_display = compactTokenValue(latestValues.total);
          derived.today_date_label = latest.label || derived.today_date_label || "";
          derived.current_period_label = latest.label || derived.current_period_label || "";
          derived.today_breakdown = targetGroup === "month"
            ? buildTokenBreakdownRows(latestValues)
            : (Array.isArray(derived.today_breakdown) && derived.today_breakdown.length
              ? derived.today_breakdown
              : buildTokenBreakdownRows(latestValues));
        }} else {{
          derived.today_total_tokens = Number(derived.today_total_tokens) || 0;
          derived.today_total_tokens_display = compactTokenValue(derived.today_total_tokens);
          derived.current_period_label = targetGroup === "month" ? "" : (derived.current_period_label || derived.today_date_label || "");
          derived.today_date_label = targetGroup === "month" ? "" : (derived.today_date_label || "");
          derived.today_breakdown = targetGroup === "month"
            ? []
            : (Array.isArray(derived.today_breakdown) ? derived.today_breakdown : []);
        }}
        const trailing = displayRows.slice(-7);
        const trailingTotal = trailing.reduce(function (sum, row) {{
          return sum + (Number(row.value) || 0);
        }}, 0);
        const trailingCost = trailing.reduce(function (sum, row) {{
          return sum + extractTokenRowCost(row);
        }}, 0);
        derived.seven_day_total_tokens = trailingTotal;
        derived.seven_day_total_tokens_display = compactTokenValue(trailingTotal);
        derived.seven_day_cost_usd = trailingCost;
        derived.seven_day_cost_display = formatUsdValue(trailingCost);
        const providerLabel = derived.provider_label || tokenProviderLabel(derived.provider);
        derived.overview_note = currentLanguage === "en"
          ? (derived.range_label || "") + " · " + activeCount + " " + periodUnit + " with records · " + providerLabel + " · " + relativeUpdate
          : (derived.range_label || "") + " · " + activeCount + " 个有数据" + periodUnit + " · " + providerLabel + " · " + relativeUpdate;
        return derived;
      }}

      function prepareTokenUsageForPanel(tokenUsage, relativeUpdate, groupBy) {{
        const prepared = deriveTokenUsageForGroup(tokenUsage, groupBy, relativeUpdate);
        const allDailyRows = Array.isArray(prepared.daily_rows) ? prepared.daily_rows : [];
        const displayLimit = normalizeTokenGroupBy(prepared.group_by) === "month"
          ? Math.min(Math.max(allDailyRows.length, 1), 12)
          : Math.min(Math.max(allDailyRows.length, {token_daily_display_days}), 31);
        const dailyRows = allDailyRows.slice(-displayLimit);
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
        const todayRows = normalizeTodayTokenBreakdown(prepared.today_breakdown);
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
          const activeDays = (prepared.daily_rows || []).filter(function (row) {{
            return (Number(row.value) || 0) > 0;
          }}).length;
          const unit = prepared.period_unit || (normalizeTokenGroupBy(prepared.group_by) === "month"
            ? (currentLanguage === "en" ? "months" : "月")
            : (currentLanguage === "en" ? "days" : "日"));
          prepared.overview_note = currentLanguage === "en"
            ? (prepared.range_label || "") + " · " + activeDays + " " + unit + " with records · " + relativeUpdate
            : (prepared.range_label || "") + " · " + activeDays + " 个有数据" + unit + " · " + relativeUpdate;
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

      function updateMetricCard(metricKey, value, caption, meta, label) {{
        const card = document.querySelector('[data-metric-key="' + metricKey + '"]');
        if (!card) {{
          return;
        }}
        const labelNode = card.querySelector('[data-role="label"]');
        const valueNode = card.querySelector('[data-role="value"]');
        const captionNode = card.querySelector('[data-role="caption"]');
        const metaNode = card.querySelector('[data-role="meta"]');
        if (labelNode && label) {{
          labelNode.textContent = label;
        }}
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
        const currentFilters = state.tokenFilters || {{}};
        state.tokenFilters = {{
          provider: normalizeTokenProvider(currentFilters.provider || tokenUsage.provider),
          startDate: currentFilters.startDate || "",
          endDate: currentFilters.endDate || "",
          groupBy: normalizeTokenGroupBy(currentFilters.groupBy || tokenUsage.group_by),
        }};
        const relativeUpdate = describeRelativeTime(state.tokenRefreshedAt, "更新");
        const preparedTokenUsage = prepareTokenUsageForPanel(tokenUsage, relativeUpdate, state.tokenFilters.groupBy);
        const periodLabel = preparedTokenUsage.range_label || t("筛选区间");
        const providerLabel = tokenProviderLabel(preparedTokenUsage.provider || state.tokenFilters.provider);
        const periodTokenValue = tokenTotalDisplay(preparedTokenUsage, "period_total_tokens", "period_total_tokens_display");
        const periodCostValue = preparedTokenUsage.period_cost_display || formatUsdValue(preparedTokenUsage.period_cost_usd);
        const averageCaption = currentLanguage === "en"
          ? "Average " + (preparedTokenUsage.period_average_tokens_display || "—") + " / " + (preparedTokenUsage.active_period_count || 0) + " active " + (preparedTokenUsage.period_unit || "days")
          : "均值 " + (preparedTokenUsage.period_average_tokens_display || "—") + " / " + (preparedTokenUsage.active_period_count || 0) + " 个有数据" + (preparedTokenUsage.period_unit || "日");
        updateMetricCard(
          "today_token",
          periodTokenValue,
          periodLabel + " · " + providerLabel,
          relativeUpdate,
          t("筛选 Token")
        );
        updateMetricCard(
          "seven_day_token",
          periodCostValue,
          averageCaption,
          relativeUpdate,
          t("周期成本")
        );
        if (elements.dailyTokenNote) {{
          const trendSourceText = normalizeTokenGroupBy(preparedTokenUsage.group_by) === "month"
            ? (currentLanguage === "en" ? "Data source: ccusage monthly aggregate" : "数据来源：ccusage 月维度聚合")
            : t("数据来源：ccusage 日维度统计");
          elements.dailyTokenNote.textContent = preparedTokenUsage.available
            ? trendSourceText + " · " + relativeUpdate
            : t("暂未获取到 ccusage 的日维度统计");
        }}
        if (elements.todayTokenNote) {{
          elements.todayTokenNote.textContent = t(preparedTokenUsage.current_period_label || preparedTokenUsage.today_date_label || "今日") + " · " + relativeUpdate;
        }}
        if (elements.tokenOverviewNote) {{
          elements.tokenOverviewNote.textContent = preparedTokenUsage.available
            ? (preparedTokenUsage.overview_note || relativeUpdate)
            : t("暂未获取到 ccusage 的日维度统计");
        }}
        syncTokenFilterControls(preparedTokenUsage);
        renderTokenSummaryCards(preparedTokenUsage.summary_cards || []);
        renderBarRows(elements.dailyTokenRows, (preparedTokenUsage.daily_rows || []).slice().reverse(), "token-daily-mid");
        renderBarRows(elements.todayTokenRows, preparedTokenUsage.today_breakdown || [], "token-input");
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

      function isLikelyTokenServiceUnavailable(error) {{
        const name = String((error && error.name) || "");
        const message = String((error && error.message) || "").toLowerCase();
        return name === "TypeError" ||
          name === "AbortError" ||
          message.includes("failed to fetch") ||
          message.includes("load failed") ||
          message.includes("networkerror");
      }}

      async function refreshTokenUsage(forceRefresh) {{
        const filters = state.tokenFilters || {{}};
        const windowDays = tokenEffectiveWindowDays(
          filters,
          (state.tokenUsage && state.tokenUsage.window_days) || {window_days}
        );
        const cacheKey = tokenRequestCacheKey(filters, windowDays);
        if (!forceRefresh) {{
          const cachedTokenUsage = getCachedTokenUsage(cacheKey);
          if (cachedTokenUsage) {{
            updateTokenVisuals(cachedTokenUsage, "cache");
            setStatus("live", "", "live_refreshed");
            return;
          }}
        }}
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
            String(windowDays)
          );
          requestUrl.searchParams.set("provider", normalizeTokenProvider(filters.provider));
          requestUrl.searchParams.set("group_by", "day");
          if (filters.startDate) {{
            requestUrl.searchParams.set("start_date", filters.startDate);
          }}
          if (filters.endDate) {{
            requestUrl.searchParams.set("end_date", filters.endDate);
          }}
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
          rememberTokenUsage(cacheKey, payload.token_usage);
          updateTokenVisuals(payload.token_usage, payload.stale ? "stale" : "live");
          if (payload.stale) {{
            setStatus("warn", "", "warn_stale");
          }} else {{
            setStatus("live", "", "live_refreshed");
          }}
        }} catch (error) {{
          if (!state.tokenUsage && snapshot.token_usage) {{
            updateTokenVisuals(snapshot.token_usage, "snapshot");
          }} else {{
            syncTokenFilterControls(state.tokenUsage);
          }}
          setStatus(
            "offline",
            "",
            isLikelyTokenServiceUnavailable(error) ? "offline_service" : "offline_snapshot"
          );
        }} finally {{
          setLoading(false);
        }}
      }}

      if (state.tokenUsage && tokenUsageMatchesRequestFilters(state.tokenUsage, state.tokenFilters)) {{
        rememberTokenUsage(
          tokenRequestCacheKey(state.tokenFilters, state.tokenUsage.window_days || {window_days}),
          state.tokenUsage
        );
      }}
      wireContentMoreButtons();
      wireProjectContextRangeButtons();
      wireProjectContextWindowLinks();
      wireThemeButtons();
      wireLanguageButtons();
      wireNightlyDateInput();
      wireWindowOverviewDateInput();
      wireBackfillCopyButtons();
      wireFinderOpenActions();
      wireMemoryFeedbackActions();
      wireExternalPanelLinks();
      wireWindowResumeActions();
      wireSideNav();
      wireHorizontalScrollLock();
      wireTokenFilters();
      applyTheme(readStoredTheme(), false);
      applyLanguage(defaultLanguage);
      if (elements.refreshButton) {{
        elements.refreshButton.addEventListener("click", function () {{
          refreshTokenUsage(true);
        }});
      }}
      if (elements.assetRefreshButton) {{
        elements.assetRefreshButton.addEventListener("click", function () {{
          refreshAssetLayer();
        }});
      }}
      if (elements.pipelineRunButton) {{
        elements.pipelineRunButton.addEventListener("click", function () {{
          runPipelineNow();
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
      window.setInterval(refreshPipelineStatus, Math.min(config.livePollMs, 10000));
      refreshTokenUsage(false);
      updatePipelineStatus(state.pipelineStatus || snapshot.pipeline_status || null);
      refreshPipelineStatus();
      window.setTimeout(function () {{
        window.location.reload();
      }}, config.autoReloadMs);
    }})();
  </script>
  <script>
    (function () {{
      try {{
        var panel = document.getElementById("openrelix-update-panel");
        var meta = document.querySelector('meta[name="openrelix:version"]');
        if (!panel || !meta) return;
        var current = String(meta.content || panel.getAttribute("data-current-version") || "").trim();
        var pkg = (meta.getAttribute("data-pkg") || "openrelix").trim() || "openrelix";
        var updateEndpoint = (meta.getAttribute("data-update-endpoint") || "").trim();
        var statusEndpoint = (meta.getAttribute("data-update-status-endpoint") || "").trim();
        var updateToken = (meta.getAttribute("data-update-token") || "").trim();
        var commandText = (panel.getAttribute("data-update-command") || "openrelix update --yes --force").trim();
        var LAST_CHECK_KEY = "openrelix-update-last-check";
        var CHECK_INTERVAL = 6 * 60 * 60 * 1000;
        var searchParams = new URLSearchParams(window.location.search || "");
        var hashParams = new URLSearchParams(String(window.location.hash || "").replace(/^#/, ""));
        var demoLatest = String(
          searchParams.get("openrelix-update-demo") ||
          hashParams.get("openrelix-update-demo") ||
          ""
        ).trim();
        var demoMode = !!demoLatest;
        if (demoLatest === "1" || demoLatest.toLowerCase() === "true") {{
          demoLatest = "9.9.9";
        }}
        var latestVersion = "";
        var mode = "idle";
        var pollTimer = null;
        var lastCheckEpoch = Number(ls(LAST_CHECK_KEY) || 0);
        var els = {{
          badge: panel.querySelector("[data-update-status-badge]"),
          lastCheck: panel.querySelector("[data-update-last-check]"),
          currentLabel: panel.querySelector("[data-update-current-label]"),
          message: panel.querySelector("[data-update-message]"),
          commandRow: panel.querySelector("[data-update-command-row]"),
          commandText: panel.querySelector("[data-update-command-text]"),
          compactCurrent: panel.querySelector("[data-update-compact-current]"),
          compactLast: panel.querySelector("[data-update-compact-last]"),
          primary: panel.querySelector("[data-update-primary]"),
          primaryLabel: panel.querySelector("[data-update-primary-label]")
        }};
        if (els.commandText) {{
          els.commandText.textContent = commandText;
        }}

        function semverKey(v) {{
          var parts = String(v || "").split(/[^0-9]+/).filter(Boolean).map(function (n) {{ return parseInt(n, 10) || 0; }});
          while (parts.length < 3) parts.push(0);
          return parts.slice(0, 3);
        }}
        function isNewer(a, b) {{
          var ak = semverKey(a), bk = semverKey(b);
          for (var i = 0; i < 3; i++) {{ if (ak[i] !== bk[i]) return ak[i] > bk[i]; }}
          return false;
        }}
        function ls(key, value) {{
          try {{
            if (value === undefined) return window.localStorage.getItem(key) || "";
            window.localStorage.setItem(key, String(value));
          }} catch (_) {{ return ""; }}
        }}
        function detectLanguage() {{
          var b = document.body, d = document.documentElement;
          if (b && b.dataset && b.dataset.language === "en") return "en";
          if (d && d.dataset && d.dataset.language === "en") return "en";
          var lang = (d && d.lang) || (navigator.language || "");
          return /^en\b/i.test(lang) ? "en" : "zh";
        }}
        function copyText(text) {{
          try {{
            if (navigator.clipboard && navigator.clipboard.writeText) {{
              navigator.clipboard.writeText(text);
              return true;
            }}
          }} catch (_) {{}}
          try {{
            var ta = document.createElement("textarea");
            ta.value = text;
            ta.setAttribute("readonly", "");
            ta.style.position = "fixed";
            ta.style.opacity = "0";
            document.body.appendChild(ta);
            ta.select();
            document.execCommand("copy");
            document.body.removeChild(ta);
            return true;
          }} catch (_) {{ return false; }}
        }}

        function versionLabel(version) {{
          version = String(version || "").trim();
          return version ? "v" + version.replace(/^v/i, "") : "—";
        }}

        function langPack() {{
          return STR[detectLanguage()] || STR.zh;
        }}

        function formatLastCheck(epoch) {{
          var s = langPack();
          var numericEpoch = Number(epoch || 0);
          if (!numericEpoch) {{
            return s.notChecked;
          }}
          var date = new Date(numericEpoch);
          if (Number.isNaN(date.getTime())) {{
            return s.notChecked;
          }}
          try {{
            return date.toLocaleString(detectLanguage() === "en" ? "en-US" : "zh-CN", {{
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit"
            }});
          }} catch (_) {{
            return date.toISOString().slice(0, 16).replace("T", " ");
          }}
        }}

        function setLastCheck(epoch) {{
          lastCheckEpoch = Number(epoch || Date.now());
          ls(LAST_CHECK_KEY, String(lastCheckEpoch));
          if (els.lastCheck) {{
            els.lastCheck.textContent = formatLastCheck(lastCheckEpoch);
          }}
        }}

        function renderLastCheck() {{
          if (els.lastCheck) {{
            els.lastCheck.textContent = formatLastCheck(lastCheckEpoch);
          }}
        }}

        function setPrimaryLabel(text) {{
          if (els.primaryLabel) {{
            els.primaryLabel.textContent = text;
          }} else if (els.primary) {{
            els.primary.textContent = text;
          }}
        }}

        function setPrimaryLoading(isLoading) {{
          if (!els.primary) {{
            return;
          }}
          els.primary.disabled = !!isLoading;
          els.primary.classList.toggle("is-loading", !!isLoading);
        }}

        var STR = {{
          zh: {{
            notChecked: "未检查",
            idleStatus: "未检查",
            checkingStatus: "检查中",
            latestStatus: "已是最新",
            availableStatus: "有新版本",
            runningStatus: "更新中",
            completedStatus: "已更新",
            failedStatus: "更新失败",
            checkFailedStatus: "检查失败",
            idleMessage: function () {{ return "当前版本 " + versionLabel(current); }},
            checkingMessage: "正在检查最新版本...",
            latestMessage: "当前已是最新版本",
            availableMessage: function (latest) {{ return "发现 OpenRelix " + versionLabel(latest); }},
            runningMessage: "正在安装并重启...",
            completedMessage: "已更新，正在重载",
            failedMessage: "更新未完成，请手动处理",
            checkFailedMessage: "检查失败，请手动处理",
            serverUnavailableMessage: "本地更新服务不可用，请手动处理",
            check: "检查更新",
            checking: "检查中",
            recheck: "重新检查",
            update: "立即更新",
            running: "更新中",
            done: "已更新",
            copy: "复制命令",
            copied: "已复制到剪贴板",
            current: "当前版本",
            lastCheckCompact: function (value) {{ return "检查 " + value; }}
          }},
          en: {{
            notChecked: "Not Checked",
            idleStatus: "Not Checked",
            checkingStatus: "Checking",
            latestStatus: "Up to Date",
            availableStatus: "Update Available",
            runningStatus: "Updating",
            completedStatus: "Updated",
            failedStatus: "Update Failed",
            checkFailedStatus: "Check Failed",
            idleMessage: function () {{ return "Current version " + versionLabel(current); }},
            checkingMessage: "Checking the latest version...",
            latestMessage: "OpenRelix is up to date",
            availableMessage: function (latest) {{ return "OpenRelix " + versionLabel(latest) + " is available"; }},
            runningMessage: "Installing and restarting...",
            completedMessage: "Updated, reloading",
            failedMessage: "Update did not finish. Handle it manually.",
            checkFailedMessage: "Update check failed. Handle it manually.",
            serverUnavailableMessage: "Local update service is unavailable. Handle it manually.",
            check: "Check Updates",
            checking: "Checking",
            recheck: "Check Again",
            update: "Update now",
            running: "Updating",
            done: "Updated",
            copy: "Copy Command",
            copied: "Copied",
            current: "Current",
            lastCheckCompact: function (value) {{ return "Checked " + value; }}
          }}
        }};

        function stateCopy(nextMode, extra) {{
          var s = langPack();
          var latest = (extra && extra.latest) || latestVersion;
          if (latest) {{
            latestVersion = latest;
          }}
          if (nextMode === "checking") {{
            return {{ status: s.checkingStatus, message: s.checkingMessage, button: s.checking, loading: true, showCommand: false, layout: "compact" }};
          }}
          if (nextMode === "latest") {{
            return {{ status: s.latestStatus, message: s.latestMessage, button: s.recheck, loading: false, showCommand: false, layout: "compact" }};
          }}
          if (nextMode === "available") {{
            return {{ status: s.availableStatus, message: s.availableMessage(latestVersion), button: s.update, loading: false, showCommand: false, layout: "expanded" }};
          }}
          if (nextMode === "running") {{
            return {{ status: s.runningStatus, message: s.runningMessage, button: s.running, loading: true, showCommand: false, layout: "expanded" }};
          }}
          if (nextMode === "completed") {{
            return {{ status: s.completedStatus, message: s.completedMessage, button: s.done, loading: false, showCommand: false, layout: "compact" }};
          }}
          if (nextMode === "check_failed") {{
            return {{
              status: s.checkFailedStatus,
              message: (extra && extra.message) || s.checkFailedMessage,
              button: s.recheck,
              loading: false,
              showCommand: false,
              layout: "compact"
            }};
          }}
          if (nextMode === "failed") {{
            return {{
              status: s.failedStatus,
              message: (extra && extra.message) || s.failedMessage,
              button: s.copy,
              loading: false,
              showCommand: true,
              layout: "expanded"
            }};
          }}
          return {{ status: s.idleStatus, message: s.idleMessage(), button: s.check, loading: false, showCommand: false, layout: "compact" }};
        }}

        function updateCompactLine() {{
          var s = langPack();
          if (els.compactCurrent) {{
            els.compactCurrent.textContent = versionLabel(current);
          }}
          if (els.compactLast) {{
            els.compactLast.textContent = s.lastCheckCompact(formatLastCheck(lastCheckEpoch));
          }}
        }}

        function setState(nextMode, extra) {{
          mode = nextMode || "idle";
          extra = extra || {{}};
          panel.setAttribute("data-update-state", mode);
          var copy = stateCopy(mode, extra);
          panel.setAttribute("data-update-layout", copy.layout || "compact");
          if (els.badge) {{
            els.badge.textContent = copy.status;
          }}
          if (els.currentLabel) {{
            els.currentLabel.textContent = versionLabel(current);
          }}
          if (els.message) {{
            els.message.textContent = copy.message;
          }}
          if (els.commandRow) {{
            els.commandRow.hidden = !copy.showCommand;
          }}
          setPrimaryLabel(copy.button);
          setPrimaryLoading(copy.loading);
          renderLastCheck();
          updateCompactLine();
        }}

        function scheduleReload(delayMs) {{
          if (panel.dataset.reloadScheduled === "1") return;
          panel.dataset.reloadScheduled = "1";
          window.setTimeout(function () {{
            window.location.reload();
          }}, Math.max(Number(delayMs) || 1500, 500));
        }}

        function flashCopied() {{
          var previousMode = mode;
          setPrimaryLabel(langPack().copied);
          window.setTimeout(function () {{
            if (mode === previousMode) {{
              setState(mode);
            }}
          }}, 1600);
        }}

        function applyWorkerStatus(data) {{
          if (!data) {{
            return;
          }}
          if (data.status === "completed") {{
            clearUpdatePoll();
            setState("completed");
            scheduleReload(data.reload_after_ms);
            return;
          }}
          if (data.status === "failed") {{
            clearUpdatePoll();
            setState("failed");
            return;
          }}
          if (data.status === "running") {{
            setState("running");
            startUpdatePoll();
          }}
        }}

        function clearUpdatePoll() {{
          if (pollTimer) {{
            window.clearInterval(pollTimer);
            pollTimer = null;
          }}
        }}

        function startUpdatePoll() {{
          if (!statusEndpoint || pollTimer) return;
          pollTimer = window.setInterval(function () {{
            fetch(statusEndpoint, {{ cache: "no-cache" }})
              .then(function (response) {{ return response && response.ok ? response.json() : null; }})
              .then(applyWorkerStatus)
              .catch(function () {{}});
          }}, 2500);
        }}

        function startServerUpdate() {{
          if (!updateEndpoint || !updateToken || !window.fetch) {{
            setState("failed", {{ message: langPack().serverUnavailableMessage }});
            return;
          }}
          setState("running");
          var headers = {{ "Content-Type": "application/json" }};
          headers["X-OpenRelix-Token"] = updateToken;
          fetch(updateEndpoint, {{
            method: "POST",
            headers: headers,
            body: JSON.stringify({{ source: "panel" }})
          }})
            .then(function (response) {{
              return response.json().catch(function () {{ return null; }}).then(function (payload) {{
                if (!response.ok || !payload) {{
                  throw new Error((payload && payload.error) || ("HTTP " + response.status));
                }}
                return payload;
              }});
            }})
            .then(function (payload) {{
              applyWorkerStatus(payload);
              if (payload && payload.status !== "completed" && payload.status !== "failed") {{
                startUpdatePoll();
              }}
            }})
            .catch(function () {{
              clearUpdatePoll();
              setState("failed", {{ message: langPack().serverUnavailableMessage }});
            }});
        }}

        function checkLatest() {{
          if (!window.fetch) {{
            setLastCheck(Date.now());
            setState("check_failed", {{ message: langPack().checkFailedMessage }});
            return;
          }}
          setState("checking");
          var url = "https://registry.npmjs.org/" + encodeURIComponent(pkg) + "/latest";
          fetch(url, {{ headers: {{ Accept: "application/json" }}, cache: "no-cache" }})
            .then(function (r) {{
              if (!r || !r.ok) {{
                throw new Error("HTTP " + (r ? r.status : "0"));
              }}
              return r.json();
            }})
            .then(function (data) {{
              setLastCheck(Date.now());
              var latest = String(data.version || "").trim();
              if (!latest) {{
                setState("check_failed", {{ message: langPack().checkFailedMessage }});
                return;
              }}
              if (isNewer(latest, current)) {{
                setState("available", {{ latest: latest }});
              }} else {{
                setState("latest");
              }}
            }})
            .catch(function () {{
              setLastCheck(Date.now());
              setState("check_failed", {{ message: langPack().checkFailedMessage }});
            }});
        }}

        function runScheduledCheck() {{
          var last = Number(ls(LAST_CHECK_KEY) || 0);
          if (Date.now() - last < CHECK_INTERVAL || mode === "checking" || mode === "running") {{
            return;
          }}
          checkLatest();
        }}

        function refreshRunningUpdateStatus() {{
          if (!statusEndpoint || !window.fetch) return;
          fetch(statusEndpoint, {{ cache: "no-cache" }})
            .then(function (response) {{ return response && response.ok ? response.json() : null; }})
            .then(function (data) {{
              if (data && data.status === "running") {{
                applyWorkerStatus(data);
              }}
            }})
            .catch(function () {{}});
        }}

        function wireLanguageObserver() {{
          if (!window.MutationObserver) return;
          var observer = new MutationObserver(function () {{
            setState(mode);
          }});
          if (document.body) {{
            observer.observe(document.body, {{ attributes: true, attributeFilter: ["data-language"] }});
          }}
          observer.observe(document.documentElement, {{ attributes: true, attributeFilter: ["lang", "data-default-language"] }});
        }}

        function start() {{
          wireLanguageObserver();
          if (els.primary) {{
            els.primary.addEventListener("click", function () {{
              if (mode === "checking" || mode === "running") return;
              if (mode === "available") {{
                startServerUpdate();
                return;
              }}
              if (mode === "failed") {{
                copyText(commandText);
                flashCopied();
                return;
              }}
              if (mode === "completed") {{
                scheduleReload(0);
                return;
              }}
              checkLatest();
            }});
          }}
          panel.addEventListener("click", function (event) {{
            if (event.target && event.target.closest && event.target.closest("[data-update-primary]")) {{
              return;
            }}
            if (panel.getAttribute("data-update-layout") !== "compact") {{
              return;
            }}
            if (mode === "checking" || mode === "running") {{
              return;
            }}
            checkLatest();
          }});
          if (demoMode && demoLatest) {{
            setLastCheck(Date.now());
            setState("available", {{ latest: demoLatest }});
            return;
          }}
          setState("idle");
          refreshRunningUpdateStatus();
          checkLatest();
          window.setInterval(runScheduledCheck, CHECK_INTERVAL);
          document.addEventListener("visibilitychange", function () {{
            if (document.visibilityState === "visible") runScheduledCheck();
          }});
        }}

        if (document.readyState === "loading") {{
          document.addEventListener("DOMContentLoaded", start);
        }} else {{
          start();
        }}
      }} catch (_) {{ /* never break the page */ }}
    }})();
  </script>
</body>
</html>
""".format(
        default_language=language,
        html_language="en" if language == "en" else "zh-CN",
        document_title=escape(localized("OpenRelix 工作台", "OpenRelix Workbench", language)),
        current_version=escape(read_panel_package_version(), quote=True),
        npm_package=escape(PROJECT_PACKAGE_NAME, quote=True),
        update_endpoint=escape("http://{}:{}/run-update".format(LIVE_TOKEN_HOST, LIVE_TOKEN_PORT), quote=True),
        update_status_endpoint=escape("http://{}:{}/update-status".format(LIVE_TOKEN_HOST, LIVE_TOKEN_PORT), quote=True),
        asset_refresh_endpoint=escape("http://{}:{}/run-refresh".format(LIVE_TOKEN_HOST, LIVE_TOKEN_PORT), quote=True),
        codex_desktop_endpoint=escape(
            "http://{}:{}{}".format(
                LIVE_TOKEN_HOST,
                LIVE_TOKEN_PORT,
                overview_codex_desktop.CODEX_DESKTOP_OPEN_PATH,
            ),
            quote=True,
        ),
        memory_feedback_endpoint=escape(
            "http://{}:{}/memory-feedback".format(LIVE_TOKEN_HOST, LIVE_TOKEN_PORT),
            quote=True,
        ),
        claude_desktop_endpoint=escape(
            "http://{}:{}{}".format(
                LIVE_TOKEN_HOST,
                LIVE_TOKEN_PORT,
                overview_claude_desktop.CLAUDE_DESKTOP_OPEN_PATH,
            ),
            quote=True,
        ),
        finder_open_endpoint=escape(
            "http://{}:{}{}".format(
                LIVE_TOKEN_HOST,
                LIVE_TOKEN_PORT,
                overview_finder.FINDER_REVEAL_PATH,
            ),
            quote=True,
        ),
        update_token=escape(read_or_create_update_token(), quote=True),
        generated_at=escape(data["generated_at"]),
        hero_eyebrow=panel_language_text_html("OpenRelix"),
        hero_mark=(
            '<span class="hero-mark" aria-hidden="true"><img src="{}" alt=""></span>'.format(
                escape(BRAND_ICON_DATA_URI, quote=True)
            )
            if BRAND_ICON_DATA_URI
            else ""
        ),
        hero_title=panel_language_text_html("OpenRelix 工作台"),
        hero_brand_line=panel_language_variant_html(
            escape("你的专属AI记忆珍藏"),
            escape("Your personal AI memory relics"),
        ),
        hero_update_panel=make_update_panel_html(),
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
        token_daily_display_days=CCUSAGE_WINDOW_DAYS,
        token_metric_cards="".join(token_metric_cards),
        asset_metric_cards="".join(asset_metric_cards),
        asset_ledger_kicker=panel_language_text_html("资产层", "Asset Layer"),
        asset_ledger_title=panel_language_text_html("资产层总览", "Asset Layer Overview"),
        asset_refresh_label=panel_language_text_html("刷新资产层", "Refresh Asset Layer"),
        asset_refresh_meta_html=make_asset_refresh_meta_html(
            data.get("asset_stats_snapshot", {}),
            current_local_datetime().date().isoformat(),
        ),
        asset_ledger_note=panel_language_text_html(
            "这里合并展示本机发现资产、登记册条目、复盘和复用记录，不是注入 host context 的记忆摘要。",
            "This merges discovered local assets, registry entries, reviews, and reuse records; it is not the memory summary injected into host context.",
        ),
        asset_stats_snapshot_panel=make_asset_stats_snapshot_panel(
            data.get("asset_stats_snapshot", {}),
            current_local_datetime().date().isoformat(),
        ),
        discovered_assets_section=make_discovered_assets_section(
            data.get("discovered_asset_rows", []),
        ),
        token_filter_panel=make_token_filter_panel(token_usage),
        token_overview_panel=make_token_overview_panel(token_usage, token_overview_help),
        type_panel=make_bar_group(
            "资产类型分布",
            asset_panels["type"],
            "teal",
            "包含本机发现资产；登记册条目会并入统计",
            help_html=type_panel_help,
        ),
        month_panel=make_bar_group(
            "月度活动",
            asset_panels["monthly_activity"],
            "slate",
            "近 6 个月，按模型实际读取 SKILL.md 的活跃 skills 去重",
            help_html=month_panel_help,
        ),
        mcp_usage_panel=make_mcp_usage_panel(
            data.get("mcp_usage", {}),
            help_html=mcp_usage_help,
        ),
        insight_section_html=insight_section_html,
        daily_token_panel=make_bar_group(
            "Token 消耗趋势",
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
            "Token 构成",
            token_usage["today_breakdown"],
            "rose",
            token_usage["today_date_label"],
            panel_id="today-token-panel",
            note_id="today-token-note",
            rows_id="today-token-rows",
            extra_classes="token-panel",
            help_html=today_token_help,
        ),
        pipeline_status_panel=pipeline_status_panel,
        nightly_summary_panel=nightly_summary_panel,
        project_context_header=make_panel_header(
            "当前项目上下文",
            help_html=project_context_help,
            note_content_html=panel_language_text_html(
                project_context_note,
                project_context_note_en,
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
            "OpenRelix 独立存储，按策略编译给 Codex / Claude Code；高价值全局和项目记忆会进入 bounded host context。",
            "Stored by OpenRelix and compiled into Codex / Claude Code by policy; high-value global and project memories enter bounded host context.",
        ),
        codex_native_memory_family_header=make_memory_family_header(
            "Codex 原生记忆",
            "Codex Native Memory",
            "来自 Codex 原生 memory summary 与 MEMORY.md。",
            "From Codex native memory_summary and MEMORY.md.",
        ),
        claude_native_memory_family_header=make_memory_family_header(
            "Claude Code 原生记忆",
            "Claude Code Native Memory",
            "来自 Claude Code CLAUDE.md 与 projects/*/memory/*.md。",
            "From Claude Code CLAUDE.md and projects/*/memory/*.md.",
        ),
        memory_compiler_header=make_panel_header(
            "总览",
            "OpenRelix canonical memory -> host context 的策略预览",
            memory_compiler_help,
        ),
        memory_compiler_body=make_memory_context_compiler_body(memory_policy_views),
        global_memory_header=make_panel_header(
            "通用上下文",
            "会进入 host context 的通用个人资产记忆",
            global_memory_help,
        ),
        project_memory_header=make_panel_header(
            "项目上下文",
            "按项目、仓库或工作区隔离，也会参与 bounded host context 注入",
            project_memory_help,
        ),
        on_demand_memory_header=make_panel_header(
            "按需召回",
            "适合检索命中后再使用的领域记忆",
            on_demand_memory_help,
        ),
        local_memory_header=make_panel_header(
            "本地保留",
            "低优先或禁止注入的本地证据",
            local_memory_help,
        ),
        top_assets_header=make_panel_header(
            "近 30 天高频 skills 热度",
            help_html=top_assets_help,
            note_content_html=panel_language_text_html(
                "按模型读取 SKILL.md 的次数排序；默认展示 Top 10",
                "Sorted by SKILL.md reads; Top 10 shown by default",
            ),
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
        global_memory_cards=make_policy_memory_type_grouped_cards(
            memory_policy_views.get("global_context", {}).get("rows", []),
        ),
        project_memory_cards=make_policy_memory_type_grouped_cards(
            memory_policy_views.get("project_context", {}).get("rows", []),
        ),
        on_demand_memory_cards=make_policy_memory_type_grouped_cards(
            memory_policy_views.get("on_demand", {}).get("rows", []),
        ),
        local_memory_cards=make_policy_memory_type_grouped_cards(
            memory_policy_views.get("local_only", {}).get("rows", []),
        ),
        memory_registry_cards=make_memory_cards(memory_registry),
        codex_native_topic_header=make_panel_header(
            "Codex 原生记忆-记忆条目",
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
            "Codex 原生记忆-历史任务索引",
            "来自 MEMORY.md，按历史任务索引展示",
            codex_native_task_group_help,
        ),
        claude_native_topic_header=make_panel_header(
            "Claude Code 原生记忆-记忆条目",
            help_html=claude_native_topic_help,
            note_content_html=claude_native_memory_note_html,
        ),
        claude_native_preference_header=make_panel_header(
            "Claude Code 原生记忆-偏好",
            help_html=claude_native_preference_help,
            note_content_html=panel_language_text_html(
                "来自 CLAUDE.md 和 auto memory 中的偏好条目",
                "From preferences in CLAUDE.md and auto memory.",
            ),
        ),
        claude_native_tip_header=make_panel_header(
            "Claude Code 原生记忆-通用 tips",
            help_html=claude_native_tip_help,
            note_content_html=panel_language_text_html(
                "来自 CLAUDE.md 和 auto memory 中的通用提示",
                "From general tips in CLAUDE.md and auto memory.",
            ),
        ),
        codex_native_topic_cards=make_memory_cards(codex_native_memory),
        codex_native_preference_cards=codex_native_preference_cards,
        codex_native_tip_cards=codex_native_tip_cards,
        codex_native_task_group_cards=codex_native_task_group_cards,
        claude_native_topic_cards=make_memory_cards(claude_native_topic_rows),
        claude_native_preference_cards=make_memory_cards(claude_native_preference_rows),
        claude_native_tip_cards=make_memory_cards(claude_native_tip_rows),
        top_skill_rows=make_top_skill_rows(asset_panels["top_skills"]),
        review_cards=make_review_cards(panel_views.get("reviews", data["reviews"])),
        usage_rows=make_usage_rows(panel_views.get("usage_events", data["usage_events"]), "usage-events"),
        asset_header=panel_language_text_html("名称", "Name"),
        description_header=panel_language_text_html("描述", "Description"),
        count_30d_header=panel_language_text_html("30 天", "30d"),
        skill_reads_30d_header=panel_language_text_html("读取", "Reads"),
        skill_sessions_30d_header=panel_language_text_html("会话", "Sessions"),
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
