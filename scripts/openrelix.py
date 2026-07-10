#!/usr/bin/env python3

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import plistlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from asset_runtime import (
    APP_SLUG,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CODEX_MODEL,
    LEGACY_APP_SLUGS,
    atomic_write_json,
    atomic_write_text,
    build_claude_cli_env,
    ensure_state_layout,
    get_activity_host,
    get_claude_env_file,
    get_claude_model,
    get_claude_settings,
    get_codex_model,
    get_model_cli,
    get_memory_mode,
    get_memory_summary_budget,
    get_project_version,
    get_activity_source,
    get_runtime_language,
    get_runtime_paths,
    load_runtime_config,
    normalize_activity_host,
    normalize_activity_source,
    normalize_claude_model,
    normalize_codex_model,
    normalize_language,
    normalize_memory_summary_max_tokens,
    normalize_memory_mode,
    normalize_model_cli,
    PROJECT_PACKAGE_NAME,
    sync_codex_exec_home,
    write_runtime_config,
)
from openrelix_overview import asset_discovery as overview_asset_discovery
from openrelix_overview import codex_profiles as overview_codex_profiles
from openrelix_overview import skill_quarantine as overview_skill_quarantine
from openrelix_overview.token_fetcher import fetch_ccusage_daily, normalize_token_provider
from openrelix_overview.token_usage import build_token_usage_view, normalize_token_group_by
from openrelix_memory_migration import (
    PERSONAL_MEMORY_ALGORITHM_VERSION,
    PERSONAL_MEMORY_MIGRATION_STAGE,
    PERSONAL_MEMORY_MIGRATION_WINDOW_DAYS,
    ensure_memory_migration_state,
    load_memory_migration_state,
    mark_memory_migration_completed,
    mark_memory_migration_failed,
    mark_memory_migration_running,
    migrate_personal_memory_registry,
)
from openrelix_task_summary import (
    TASK_CLUSTER_ALGORITHM_VERSION,
    TASK_SUMMARY_WINDOW_DAYS,
    ensure_task_summary_migration_state,
    load_task_summary_migration_state,
    mark_task_summary_migration_completed,
    mark_task_summary_migration_failed,
    print_task_summary_migration_state,
    resolve_task_summary_dates,
    run_task_summary_for_dates,
)


PATHS = get_runtime_paths()
LANGUAGE = get_runtime_language(PATHS)
MEMORY_MODE = get_memory_mode(PATHS)
ACTIVITY_SOURCE = get_activity_source(PATHS)
ACTIVITY_HOST = get_activity_host(PATHS)
MODEL_CLI = get_model_cli(PATHS)
REPO_ROOT = PATHS.repo_root
REPORTS_DIR = PATHS.reports_dir
ASSET_STATS_LATEST_PATH = REPORTS_DIR / "asset-stats-latest.json"
CONSOLIDATED_DAILY_DIR = PATHS.consolidated_daily_dir
REFRESH_SCRIPT = REPO_ROOT / "scripts" / "refresh_overview.sh"
NIGHTLY_PIPELINE_SCRIPT = REPO_ROOT / "scripts" / "nightly_pipeline.sh"
COLLECT_CODEX_ACTIVITY_SCRIPT = REPO_ROOT / "scripts" / "collect_codex_activity.py"
BUILD_OVERVIEW_SCRIPT = REPO_ROOT / "scripts" / "build_overview.py"
BUILD_CODEX_MEMORY_SUMMARY_SCRIPT = REPO_ROOT / "scripts" / "build_codex_memory_summary.py"
SYNC_HOST_MEMORY_SUMMARY_SCRIPT = REPO_ROOT / "scripts" / "sync_host_memory_summary.py"
BUILD_CODEX_NATIVE_DISPLAY_CACHE_SCRIPT = REPO_ROOT / "scripts" / "build_codex_native_display_cache.py"
CONFIGURE_CODEX_USER_SCRIPT = REPO_ROOT / "install" / "configure_codex_user.py"
BUILD_MACOS_CLIENT_SCRIPT = REPO_ROOT / "scripts" / "build_macos_client.sh"
CLAUDE_MANAGED_MEMORY_START = "<!-- openrelix:shared-memory:start -->"
CLAUDE_MANAGED_MEMORY_END = "<!-- openrelix:shared-memory:end -->"
LEGACY_CODEX_PROFILE_MARKER = "The injected context is compiled from OpenRelix canonical"
LEGACY_CODEX_REGISTRY_MARKER = "### Local personal memory registry"
RENDER_TEMPLATE_SCRIPT = REPO_ROOT / "install" / "render_template.py"
MACOS_CLIENT_APP_NAME = "OpenRelix.app"
NPM_PACKAGE_NAME = PROJECT_PACKAGE_NAME
NPM_LATEST_SPEC = "{}@latest".format(NPM_PACKAGE_NAME)
COMMON_CLI_TOOL_PATHS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
)
TOKEN_LIVE_LABEL = "io.github.openrelix.token-live"
TOKEN_LIVE_PLIST_NAME = "{}.plist".format(TOKEN_LIVE_LABEL)
TOKEN_LIVE_TEMPLATE = REPO_ROOT / "ops" / "launchd" / "{}.tmpl".format(TOKEN_LIVE_PLIST_NAME)
OVERVIEW_REFRESH_LABEL = "io.github.openrelix.overview-refresh"
OVERVIEW_REFRESH_PLIST_NAME = "{}.plist".format(OVERVIEW_REFRESH_LABEL)
NIGHTLY_ORGANIZE_LABEL = "io.github.openrelix.nightly-organize"
NIGHTLY_ORGANIZE_PLIST_NAME = "{}.plist".format(NIGHTLY_ORGANIZE_LABEL)
NIGHTLY_FINALIZE_LABEL = "io.github.openrelix.nightly-finalize-previous-day"
NIGHTLY_FINALIZE_PLIST_NAME = "{}.plist".format(NIGHTLY_FINALIZE_LABEL)
UPDATE_CHECK_LABEL = "io.github.openrelix.update-check"
UPDATE_CHECK_PLIST_NAME = "{}.plist".format(UPDATE_CHECK_LABEL)
TOKEN_LIVE_HEALTH_URL = "http://127.0.0.1:8765/healthz"
TOKEN_LIVE_STARTUP_TIMEOUT_SECONDS = 8.0
STAGE_PRIORITY = {"manual": 0, "preliminary": 1, "final": 2}
MAX_BACKFILL_JOBS = 2
RAW_HISTORY_HYDRATION_DEFAULT_DAYS = 30
RAW_HISTORY_HYDRATION_DAYS_ENV = "OPENRELIX_RAW_HISTORY_WINDOW_DAYS"
RAW_HISTORY_HYDRATION_STAGE = "final"

_ACTIVE_CHILD_PROCESSES = set()
_ACTIVE_CHILD_PROCESSES_LOCK = threading.Lock()


def current_language(language=None):
    return normalize_language(language or LANGUAGE)


def localized(zh_text, en_text, language=None):
    return en_text if current_language(language) == "en" else zh_text


class LocalizedArgumentParser(argparse.ArgumentParser):
    def format_help(self):
        text = super().format_help()
        if current_language() != "zh":
            return text
        replacements = (
            ("usage:", "用法:"),
            ("positional arguments:", "位置参数:"),
            ("optional arguments:", "选项:"),
            ("options:", "选项:"),
            ("show this help message and exit", "显示帮助并退出。"),
        )
        for source, target in replacements:
            text = text.replace(source, target)
        return text


def build_parser():
    parser = LocalizedArgumentParser(
        prog="openrelix",
        description=localized("OpenRelix 命令集。", "OpenRelix command set."),
    )
    subparsers = parser.add_subparsers(dest="command", parser_class=LocalizedArgumentParser)

    review = subparsers.add_parser(
        "review",
        help=localized(
            "运行指定日期的 review 流水线并打印摘要。",
            "Run the review pipeline for a target date and print the summary.",
        ),
    )
    review.add_argument(
        "scope",
        nargs="?",
        default="today",
        choices=["today"],
        help=localized(
            "兼容占位参数；实际目标日期由 --date 控制。",
            "Compatibility placeholder; the target date is controlled by --date.",
        ),
    )
    review.add_argument(
        "--date",
        default=current_date_str(),
        help=localized(
            "目标日期，格式 YYYY-MM-DD。默认今天。",
            "Target date in YYYY-MM-DD. Default: today.",
        ),
    )
    review.add_argument(
        "--stage",
        default="manual",
        choices=["manual", "preliminary", "final"],
        help=localized(
            "写入 nightly summary 的流水线阶段。",
            "Pipeline stage written into the nightly summary.",
        ),
    )
    review.add_argument(
        "--open",
        action="store_true",
        help=localized(
            "完成后打开生成的 review Markdown。",
            "Open the generated review markdown after finishing.",
        ),
    )
    review.add_argument(
        "--json",
        action="store_true",
        help=localized(
            "打印 review summary JSON，而不是人类可读摘要。",
            "Print the review summary JSON instead of a human-readable summary.",
        ),
    )
    review.add_argument(
        "--learn-window-days",
        type=int,
        default=0,
        help=localized(
            "仅本次手动运行生效：生成目标日期记忆前，学习前 N 天的近期窗口摘要。",
            "For this manual run only, learn from recent window summaries in the previous N days before generating memories for the target date.",
        ),
    )
    review.add_argument(
        "--jobs",
        type=int,
        default=1,
        help=localized(
            "补齐历史 preliminary summary 时的并发数，当前最大 2；final 终版整理始终串行。",
            "Concurrency for backfilling historical preliminary summaries, currently capped at 2; final organization always runs serially.",
        ),
    )

    backfill = subparsers.add_parser(
        "backfill",
        help=localized(
            "一键回溯多日 review 流水线。",
            "Backfill the review pipeline for multiple dates.",
        ),
    )
    backfill.add_argument(
        "--dates",
        help=localized(
            "逗号或空格分隔的目标日期列表，格式 YYYY-MM-DD。优先级高于 --from/--days。",
            "Comma- or space-separated target dates in YYYY-MM-DD. Takes precedence over --from/--days.",
        ),
    )
    backfill.add_argument(
        "--from",
        dest="date_from",
        help=localized(
            "起始日期，格式 YYYY-MM-DD。",
            "Start date in YYYY-MM-DD.",
        ),
    )
    backfill.add_argument(
        "--to",
        dest="date_to",
        default=current_date_str(),
        help=localized(
            "结束日期，格式 YYYY-MM-DD。默认今天。",
            "End date in YYYY-MM-DD. Default: today.",
        ),
    )
    backfill.add_argument(
        "--days",
        type=int,
        default=0,
        help=localized(
            "从结束日期向前回溯 N 天；传了 --from 时忽略。",
            "Backfill N days ending at --to; ignored when --from is provided.",
        ),
    )
    backfill.add_argument(
        "--stage",
        default="final",
        choices=["manual", "preliminary", "final"],
        help=localized(
            "写入 nightly summary 的流水线阶段。回溯默认使用 final。",
            "Pipeline stage written into the nightly summary. Backfill defaults to final.",
        ),
    )
    backfill.add_argument(
        "--learn-window-days",
        type=int,
        default=0,
        help=localized(
            "每个目标日期整理前，学习前 N 天的近期窗口摘要。",
            "For each target date, learn from recent window summaries in the previous N days.",
        ),
    )
    backfill.add_argument(
        "--jobs",
        type=int,
        default=1,
        help=localized(
            "并发回溯天数，当前最大 2；preliminary 可并发，final 终版目标日期会串行整理。",
            "Number of dates to backfill concurrently, currently capped at 2; preliminary can run concurrently, while final target dates are organized serially.",
        ),
    )
    backfill.add_argument(
        "--force",
        action="store_true",
        help=localized(
            "即使目标日期已有 summary，也重新回溯。",
            "Re-run even when the target date already has a summary.",
        ),
    )
    backfill.add_argument(
        "--json",
        action="store_true",
        help=localized(
            "打印 JSON 汇总，而不是人类可读摘要。",
            "Print a JSON summary instead of human-readable output.",
        ),
    )

    core = subparsers.add_parser(
        "core",
        help=localized(
            "打印当前 overview 快照里的核心指标。",
            "Print core metrics from the current overview snapshot.",
        ),
    )
    core.add_argument(
        "--json",
        action="store_true",
        help=localized(
            "以 JSON 打印选中的 overview payload。",
            "Print the selected overview payload as JSON.",
        ),
    )

    doctor = subparsers.add_parser(
        "doctor",
        help=localized(
            "检查本机运行环境并给出排障提示。",
            "Check the local runtime environment and print troubleshooting guidance.",
        ),
    )
    doctor.add_argument(
        "--model-check",
        action="store_true",
        help=localized(
            "实际运行一次极小的当前 model_cli 调用，验证模型认证链路。",
            "Run a tiny call through the current model_cli to verify the model authentication path.",
        ),
    )
    doctor.add_argument(
        "--app-server-check",
        action="store_true",
        help=localized(
            "实际启动一次 codex app-server 并读取一个线程页，验证 Codex 客户端采集链路。",
            "Start codex app-server and read one thread page to verify Codex app collection.",
        ),
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        help=localized(
            "以 JSON 打印体检结果。",
            "Print doctor results as JSON.",
        ),
    )

    refresh = subparsers.add_parser(
        "refresh",
        help=localized(
            "刷新活动数据并重建 overview 快照。",
            "Refresh activity and rebuild the overview snapshot.",
        ),
    )
    refresh.add_argument(
        "--json",
        action="store_true",
        help=localized(
            "刷新后以 JSON 打印选中的 overview payload。",
            "Print the selected overview payload as JSON after refresh.",
        ),
    )
    refresh.add_argument(
        "--learn-memory",
        action="store_true",
        help=localized(
            "刷新前调用轻量 review 流水线，生成目标日期窗口摘要；记忆沉淀留给 final 回溯。",
            "Run a lightweight review pipeline before refresh to build target-date window summaries; memory synthesis is deferred to final backfill.",
        ),
    )
    refresh.add_argument(
        "--date",
        default=current_date_str(),
        help=localized(
            "learn-memory 的目标日期，格式 YYYY-MM-DD。默认今天。",
            "Target date for learn-memory in YYYY-MM-DD. Default: today.",
        ),
    )
    refresh.add_argument(
        "--stage",
        default="manual",
        choices=["manual", "preliminary", "final"],
        help=localized(
            "learn-memory 写入 nightly summary 的流水线阶段。",
            "Pipeline stage written by learn-memory.",
        ),
    )
    refresh.add_argument(
        "--learn-window-days",
        type=int,
        default=0,
        help=localized(
            "learn-memory 额外参考前 N 天窗口；默认 0，保持轻量。",
            "For learn-memory, additionally learn from the previous N days; default 0 keeps it lightweight.",
        ),
    )

    update = subparsers.add_parser(
        "update",
        help=localized(
            "检查或安装最新 OpenRelix npm 包。",
            "Check for or install the latest OpenRelix npm package.",
        ),
    )
    update.add_argument(
        "--check",
        action="store_true",
        help=localized(
            "只检查 npm 最新版本，不安装。",
            "Only check the latest npm version; do not install.",
        ),
    )
    update.add_argument(
        "--print-command",
        action="store_true",
        help=localized(
            "只打印将要执行的更新命令。",
            "Only print the update command that would be run.",
        ),
    )
    update.add_argument(
        "--recommended",
        action="store_true",
        help=localized(
            "使用推荐完整后台配置：学习刷新、夜间整理和每日更新检查。",
            "Use the recommended full background setup: learning refresh, nightly organization, and daily update check.",
        ),
    )
    update.add_argument(
        "--force",
        action="store_true",
        help=localized(
            "即使当前版本已是最新，也重新运行安装器。",
            "Run the installer even when the current version appears up to date.",
        ),
    )
    update.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help=localized(
            "不交互确认，直接执行更新命令。",
            "Run the update command without an interactive confirmation.",
        ),
    )
    update.add_argument(
        "--json",
        action="store_true",
        help=localized(
            "以 JSON 打印更新检查结果。",
            "Print update check results as JSON.",
        ),
    )

    memory_migration = subparsers.add_parser(
        "memory-migration",
        help=localized(
            "检查或执行个人记忆算法迁移。",
            "Check or run the personal memory algorithm migration.",
        ),
    )
    memory_migration.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "ensure", "run", "complete"],
        help=localized(
            "status 查看状态；ensure 只写 pending marker；run 执行有界迁移；complete 标记当前算法已迁移。",
            "status prints state; ensure writes a pending marker; run executes bounded migration; complete marks the current algorithm migrated.",
        ),
    )
    memory_migration.add_argument(
        "--window-days",
        type=int,
        default=PERSONAL_MEMORY_MIGRATION_WINDOW_DAYS,
        help=localized(
            "迁移重跑最近 N 天。默认 7。",
            "Re-run the last N days during migration. Default: 7.",
        ),
    )
    memory_migration.add_argument(
        "--force",
        action="store_true",
        help=localized(
            "即使当前算法版本已标记完成，也重新安排或执行迁移。",
            "Schedule or run migration even when the current algorithm version is already marked complete.",
        ),
    )
    memory_migration.add_argument(
        "--if-pending",
        action="store_true",
        help=localized(
            "仅当存在 pending migration 时执行；否则安静退出。",
            "Run only when a pending migration exists; otherwise exit quietly.",
        ),
    )
    memory_migration.add_argument(
        "--quiet",
        action="store_true",
        help=localized(
            "减少迁移过程输出。",
            "Reduce migration output.",
        ),
    )
    memory_migration.add_argument(
        "--json",
        action="store_true",
        help=localized(
            "以 JSON 打印迁移状态。",
            "Print migration state as JSON.",
        ),
    )

    task_summary_migration = subparsers.add_parser(
        "task-summary-migration",
        help=localized(
            "检查或执行并行任务总结迁移。",
            "Check or run the parallel task summary migration.",
        ),
    )
    task_summary_migration.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "ensure", "run"],
        help=localized(
            "status 查看状态；ensure 只写 pending marker；run 基于已有窗口摘要补算任务总结。",
            "status prints state; ensure writes a pending marker; run builds task summaries from existing window summaries.",
        ),
    )
    task_summary_migration.add_argument(
        "--window-days",
        type=int,
        default=TASK_SUMMARY_WINDOW_DAYS,
        help=localized(
            "迁移补算最近 N 天。默认 7。",
            "Summarize the last N days during migration. Default: 7.",
        ),
    )
    task_summary_migration.add_argument(
        "--force",
        action="store_true",
        help=localized(
            "即使当前任务聚合算法版本已标记完成，也重新安排或执行迁移。",
            "Schedule or run migration even when the current task-cluster algorithm version is already marked complete.",
        ),
    )
    task_summary_migration.add_argument(
        "--if-pending",
        action="store_true",
        help=localized(
            "仅当存在 pending migration 时执行；否则安静退出。",
            "Run only when a pending migration exists; otherwise exit quietly.",
        ),
    )
    task_summary_migration.add_argument(
        "--quiet",
        action="store_true",
        help=localized("减少迁移过程输出。", "Reduce migration output."),
    )
    task_summary_migration.add_argument(
        "--json",
        action="store_true",
        help=localized("以 JSON 打印迁移状态。", "Print migration state as JSON."),
    )

    uninstall = subparsers.add_parser(
        "uninstall",
        help=localized(
            "卸载本机 OpenRelix 集成，可选择是否删除本地记忆。",
            "Uninstall local OpenRelix integrations, optionally deleting local memory.",
        ),
    )
    local_memory_group = uninstall.add_mutually_exclusive_group()
    local_memory_group.add_argument(
        "--delete-local-memory",
        action="store_true",
        help=localized(
            "同时删除本地 state root，并移除 host context 里的 OpenRelix 受控块。",
            "Also delete the local state root and remove OpenRelix managed blocks from host context.",
        ),
    )
    local_memory_group.add_argument(
        "--keep-local-memory",
        action="store_true",
        help=localized(
            "保留本地 state root 和 host memory summary，不交互询问。",
            "Keep the local state root and host memory summary without prompting.",
        ),
    )
    uninstall.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help=localized(
            "不交互确认；未显式指定时默认保留本地记忆。",
            "Do not prompt; keep local memory unless explicitly requested.",
        ),
    )
    uninstall.add_argument(
        "--dry-run",
        action="store_true",
        help=localized(
            "只打印将要删除的内容，不实际修改文件。",
            "Print what would be removed without changing files.",
        ),
    )
    uninstall.add_argument(
        "--json",
        action="store_true",
        help=localized("以 JSON 打印卸载结果。", "Print uninstall results as JSON."),
    )

    mode = subparsers.add_parser(
        "mode",
        help=localized(
            "查看或切换 OpenRelix 记忆模式。",
            "Show or switch the OpenRelix memory mode.",
        ),
    )
    mode.add_argument(
        "memory_mode",
        nargs="?",
        help=localized(
            "目标模式：integrated | local-only | off。省略时只显示当前模式。",
            "Target mode: integrated | local-only | off. Omit to show the current mode.",
        ),
    )
    mode.add_argument(
        "--no-refresh",
        action="store_true",
        help=localized(
            "切换后不刷新 overview 和面板。",
            "Do not refresh the overview and panel after switching.",
        ),
    )
    mode.add_argument(
        "--json",
        action="store_true",
        help=localized(
            "以 JSON 打印模式信息。",
            "Print mode information as JSON.",
        ),
    )

    schedule = subparsers.add_parser(
        "schedule",
        help=localized(
            "查看或调整已安装后台任务的运行时间。",
            "Show or adjust installed background job schedules.",
        ),
    )
    schedule.add_argument(
        "--overview-refresh-interval-minutes",
        type=int,
        help=localized(
            "调整面板自动刷新间隔，单位分钟；默认安装值为 60。",
            "Set the panel auto-refresh interval in minutes; the default installed value is 60.",
        ),
    )
    schedule.add_argument(
        "--nightly-organize-time",
        help=localized(
            "调整当天预览整理时间，格式 HH:MM；默认安装值为 23:00。",
            "Set the same-day nightly preview time in HH:MM; the default installed value is 23:00.",
        ),
    )
    schedule.add_argument(
        "--nightly-finalize-time",
        help=localized(
            "调整前一日终版整理时间，格式 HH:MM；默认安装值为 00:10。",
            "Set the previous-day finalize time in HH:MM; the default installed value is 00:10.",
        ),
    )
    schedule.add_argument(
        "--no-bootstrap",
        action="store_true",
        help=localized(
            "只写入 plist，不重新加载 LaunchAgent。",
            "Only write plist files; do not reload LaunchAgents.",
        ),
    )
    schedule.add_argument(
        "--json",
        action="store_true",
        help=localized("以 JSON 打印结果。", "Print result as JSON."),
    )

    config = subparsers.add_parser(
        "config",
        help=localized(
            "查看或更新 OpenRelix 运行配置。",
            "Show or update OpenRelix runtime config.",
        ),
    )
    config.add_argument(
        "--memory-summary-max-tokens",
        type=int,
        help=localized(
            "设置注入 host context 的 bounded summary 最大 token，默认 8000，范围 2000-20000；target / warn 自动派生。",
            "Set the bounded summary max tokens injected into host context. Default 8000, range 2000-20000; target / warning are derived automatically.",
        ),
    )
    config.add_argument(
        "--codex-model",
        help=localized(
            "设置 OpenRelix 内部 codex exec 使用的模型；默认 {}。接受未来模型 ID，也支持 gpt5.4mini 这类常见简写。".format(DEFAULT_CODEX_MODEL),
            "Set the model used by OpenRelix internal codex exec calls. Default: {}. Future model IDs are accepted; common shorthands like gpt5.4mini are also accepted.".format(DEFAULT_CODEX_MODEL),
        ),
    )
    config.add_argument(
        "--claude-model",
        help=localized(
            "设置 OpenRelix 内部 claude -p 使用的模型或别名；默认 {} 表示使用 Claude Code 自己的默认模型/provider。".format(DEFAULT_CLAUDE_MODEL),
            "Set the model or alias used by OpenRelix internal claude -p calls. Default: {} means use Claude Code's own default model/provider.".format(DEFAULT_CLAUDE_MODEL),
        ),
    )
    config.add_argument(
        "--claude-settings",
        help=localized(
            "传给 claude -p 的 --settings 路径或 JSON；用于第三方模型、apiKeyHelper、桥接 provider 等 Claude Code 原生配置。",
            "Path or JSON passed to claude -p --settings; useful for third-party models, apiKeyHelper, bridge providers, and other native Claude Code settings.",
        ),
    )
    config.add_argument(
        "--claude-env-file",
        help=localized(
            "给 claude -p 加载的环境变量文件路径；适合把 ANTHROPIC_BASE_URL / API key helper 等桥接配置放在仓库外。",
            "Path to an env file loaded for claude -p; useful for bridge settings such as ANTHROPIC_BASE_URL or API key helpers outside the repo.",
        ),
    )
    config.add_argument(
        "--model-cli",
        choices=["codex", "claude", "cc"],
        help=localized(
            "设置大模型记忆回溯使用的 CLI：codex | claude。",
            "Set the CLI used for model-backed memory consolidation: codex | claude.",
        ),
    )
    config.add_argument(
        "--activity-host",
        choices=["codex", "claude", "cc", "all"],
        help=localized(
            "设置窗口采集 host：codex | claude | all。默认 all。",
            "Set the window collection host: codex | claude | all. Default: all.",
        ),
    )
    config.add_argument(
        "--activity-source",
        choices=["history", "app-server", "auto"],
        help=localized(
            "设置窗口采集来源：history | app-server | auto。auto 会先尝试 Codex 客户端 app-server，失败时回退 CLI history/session。",
            "Set window collection source: history | app-server | auto. auto tries Codex app-server first, then falls back to CLI history/session.",
        ),
    )
    config.add_argument(
        "--read-codex-app",
        action="store_true",
        help=localized(
            "等价于 --activity-source auto；保留为旧安装命令的兼容别名。",
            "Equivalent to --activity-source auto; kept as a compatibility alias for older install commands.",
        ),
    )
    config.add_argument(
        "--no-refresh",
        action="store_true",
        help=localized(
            "更新配置后不刷新 summary / overview / 面板。",
            "Do not refresh summary / overview / panel after updating config.",
        ),
    )
    config.add_argument(
        "--json",
        action="store_true",
        help=localized("以 JSON 打印配置。", "Print config as JSON."),
    )

    context = subparsers.add_parser(
        "context",
        help=localized(
            "同步统一 host context 摘要。",
            "Sync the unified host-context summary.",
        ),
    )
    context.add_argument(
        "action",
        nargs="?",
        default="sync",
        choices=["sync"],
        help=localized("context 操作。", "Context action."),
    )
    context.add_argument(
        "--json",
        action="store_true",
        help=localized("以 JSON 打印同步结果。", "Print sync result as JSON."),
    )

    models = subparsers.add_parser(
        "models",
        help=localized(
            "列出当前 Codex CLI 可见的模型 catalog。",
            "List the model catalog currently visible to Codex CLI.",
        ),
    )
    models.add_argument(
        "--all",
        action="store_true",
        help=localized(
            "包含隐藏模型条目。",
            "Include hidden model entries.",
        ),
    )
    models.add_argument(
        "--bundled",
        action="store_true",
        help=localized(
            "只读取当前 Codex CLI 随包 catalog，不尝试刷新。",
            "Read only the catalog bundled with the current Codex CLI; do not refresh.",
        ),
    )
    models.add_argument(
        "--json",
        action="store_true",
        help=localized("以 JSON 打印模型列表。", "Print the model list as JSON."),
    )

    tokens = subparsers.add_parser(
        "tokens",
        help=localized(
            "查询 Codex / Claude Code Token 用量。",
            "Query Codex / Claude Code token usage.",
        ),
    )
    tokens.add_argument(
        "--provider",
        choices=["all", "codex", "claude", "cc"],
        default="all",
        help=localized(
            "Token provider：all | codex | claude | cc。默认 all 会合并 Codex 和 Claude Code。",
            "Token provider: all | codex | claude | cc. Default all merges Codex and Claude Code.",
        ),
    )
    tokens.add_argument(
        "--window-days",
        type=int,
        default=7,
        help=localized("查询最近 N 天，默认 7。", "Query the last N days. Default: 7."),
    )
    tokens.add_argument(
        "--start-date",
        default="",
        help=localized("起始日期，格式 YYYY-MM-DD。", "Start date, in YYYY-MM-DD format."),
    )
    tokens.add_argument(
        "--end-date",
        default="",
        help=localized("结束日期，格式 YYYY-MM-DD。", "End date, in YYYY-MM-DD format."),
    )
    tokens.add_argument(
        "--group-by",
        choices=["day", "month"],
        default="day",
        help=localized("展示粒度：day 或 month。", "Display granularity: day or month."),
    )
    tokens.add_argument(
        "--json",
        action="store_true",
        help=localized("以 JSON 打印 Token 视图。", "Print the token view as JSON."),
    )

    asset_stats = subparsers.add_parser(
        "asset-stats",
        help=localized(
            "生成单次资产统计快照并更新面板。",
            "Build a single asset statistics snapshot and update the panel.",
        ),
    )
    asset_stats.add_argument(
        "--date",
        default=current_date_str(),
        help=localized(
            "统计锚点日期，格式 YYYY-MM-DD。默认今天。",
            "Anchor date in YYYY-MM-DD. Default: today.",
        ),
    )
    asset_stats.add_argument(
        "--monthly-months",
        type=int,
        default=6,
        help=localized(
            "月度活动回看月份数，默认 6。",
            "Number of months for monthly activity, default 6.",
        ),
    )
    asset_stats.add_argument(
        "--top-limit",
        type=int,
        default=10,
        help=localized(
            "高频 skills 列表最多保留条数，默认 10。",
            "Maximum top-skill rows to keep, default 10.",
        ),
    )
    asset_stats.add_argument(
        "--no-refresh",
        action="store_true",
        help=localized(
            "只写入统计快照，不重建 overview 和 panel。",
            "Only write the stats snapshot; do not rebuild overview and panel.",
        ),
    )
    asset_stats.add_argument(
        "--json",
        action="store_true",
        help=localized("以 JSON 打印统计快照。", "Print the stats snapshot as JSON."),
    )

    skill_quarantine = subparsers.add_parser(
        "skill-quarantine",
        aliases=["skill-blackroom", "skill-blacklist"],
        help=localized(
            "管理 Skill/MCP 小黑屋。",
            "Manage Skill/MCP quarantine.",
        ),
    )
    skill_quarantine_subparsers = skill_quarantine.add_subparsers(
        dest="subcommand",
        help=localized("子命令。", "Subcommands."),
    )
    skill_quarantine_list = skill_quarantine_subparsers.add_parser(
        "list",
        help=localized("列出所有 Skill/MCP 状态。", "List Skill/MCP quarantine status."),
    )
    skill_quarantine_list.add_argument("--json", action="store_true", help=localized("以 JSON 打印。", "Print JSON."))
    skill_quarantine_suggest = skill_quarantine_subparsers.add_parser(
        "suggest",
        help=localized("查看建议隔离的项目。", "Show suggested quarantine items."),
    )
    skill_quarantine_suggest.add_argument("--json", action="store_true", help=localized("以 JSON 打印。", "Print JSON."))
    skill_quarantine_blocked = skill_quarantine_subparsers.add_parser(
        "blocked",
        help=localized("查看小黑屋。", "Show quarantined items."),
    )
    skill_quarantine_blocked.add_argument("--json", action="store_true", help=localized("以 JSON 打印。", "Print JSON."))
    skill_quarantine_block = skill_quarantine_subparsers.add_parser(
        "block",
        help=localized("手动隔离。", "Manually quarantine an item."),
    )
    skill_quarantine_block.add_argument("entity", help=localized("项目 ID，例如 skill:foo 或 mcp:playwright。", "Entity ID, for example skill:foo or mcp:playwright."))
    skill_quarantine_block.add_argument("--type", choices=["skill", "mcp"], help=localized("未写前缀时指定类型。", "Type to use when the entity has no prefix."))
    skill_quarantine_block.add_argument("--note", default="", help=localized("备注。", "Note."))
    skill_quarantine_block.add_argument("--no-apply", action="store_true", help=localized("只记录状态，不搬移 skill 或改 MCP JSON 配置。", "Only record state; do not move skills or edit MCP JSON config."))
    skill_quarantine_unblock = skill_quarantine_subparsers.add_parser(
        "unblock",
        help=localized("从小黑屋恢复使用。", "Restore an item from quarantine."),
    )
    skill_quarantine_unblock.add_argument("entity", help=localized("项目 ID，例如 skill:foo 或 mcp:playwright。", "Entity ID, for example skill:foo or mcp:playwright."))
    skill_quarantine_unblock.add_argument("--type", choices=["skill", "mcp"], help=localized("未写前缀时指定类型。", "Type to use when the entity has no prefix."))
    skill_quarantine_unblock.add_argument("--no-apply", action="store_true", help=localized("只移除状态，不恢复搬移或配置。", "Only remove state; do not restore moved files or config."))
    skill_quarantine_all = skill_quarantine_subparsers.add_parser(
        "block-all",
        aliases=["auto-clean"],
        help=localized("一键隔离所有建议项。", "Quarantine all suggested items."),
    )
    skill_quarantine_all.add_argument("--dry-run", action="store_true", help=localized("仅预览，不执行。", "Preview only."))
    skill_quarantine_all.add_argument("--yes", "-y", action="store_true", help=localized("跳过确认。", "Skip confirmation."))
    skill_quarantine_all.add_argument("--no-apply", action="store_true", help=localized("只记录状态，不搬移 skill 或改 MCP JSON 配置。", "Only record state; do not move skills or edit MCP JSON config."))
    skill_quarantine_all.add_argument("--json", action="store_true", help=localized("以 JSON 打印。", "Print JSON."))
    skill_quarantine_grace_all = skill_quarantine_subparsers.add_parser(
        "block-grace-all",
        help=localized("一键隔离所有可选项。", "Quarantine all optional items."),
    )
    skill_quarantine_grace_all.add_argument("--dry-run", action="store_true", help=localized("仅预览，不执行。", "Preview only."))
    skill_quarantine_grace_all.add_argument("--yes", "-y", action="store_true", help=localized("跳过确认。", "Skip confirmation."))
    skill_quarantine_grace_all.add_argument("--no-apply", action="store_true", help=localized("只记录状态，不搬移 skill 或改 MCP JSON 配置。", "Only record state; do not move skills or edit MCP JSON config."))
    skill_quarantine_grace_all.add_argument("--json", action="store_true", help=localized("以 JSON 打印。", "Print JSON."))

    index = subparsers.add_parser(
        "index",
        help=localized(
            "管理本地 SQLite 检索索引。",
            "Manage the local SQLite search index.",
        ),
    )
    index.add_argument(
        "action",
        choices=["status", "rebuild", "search-memory", "search-window"],
        help=localized(
            "索引操作。",
            "Index action.",
        ),
    )
    index.add_argument(
        "query",
        nargs="?",
        default="",
        help=localized(
            "search-memory / search-window 的查询文本。",
            "Query text for search-memory / search-window.",
        ),
    )
    index.add_argument("--bucket", help=localized("按 memory bucket 过滤。", "Filter by memory bucket."))
    index.add_argument("--priority", help=localized("按 memory priority 过滤。", "Filter by memory priority."))
    index.add_argument("--scope", help=localized("按 memory scope 过滤。", "Filter by memory scope."))
    index.add_argument(
        "--search-scope",
        choices=sorted(["all", "ai", "raw-question", "raw-conclusion", "id", "project"]),
        default="all",
        help=localized("按窗口搜索范围过滤。", "Filter by window search scope."),
    )
    index.add_argument(
        "--injection-policy",
        help=localized("按 memory injection_policy 过滤。", "Filter by memory injection_policy."),
    )
    index.add_argument("--project", help=localized("按窗口项目名或 cwd 过滤。", "Filter windows by project label or cwd."))
    index.add_argument("--date-from", help=localized("起始日期 YYYY-MM-DD。", "Start date YYYY-MM-DD."))
    index.add_argument("--date-to", help=localized("结束日期 YYYY-MM-DD。", "End date YYYY-MM-DD."))
    index.add_argument("--limit", type=int, default=20, help=localized("最多返回条数。", "Maximum result count."))
    index.add_argument(
        "--json",
        action="store_true",
        help=localized("以 JSON 打印结果。", "Print results as JSON."),
    )

    recall = subparsers.add_parser(
        "recall",
        help=localized(
            "显式检索按需召回记忆。",
            "Explicitly search on-demand recall memories.",
        ),
    )
    recall.add_argument("query", nargs="?", default="", help=localized("召回查询文本。", "Recall query text."))
    recall.add_argument("--scope", help=localized("按 memory scope 过滤。", "Filter by memory scope."))
    recall.add_argument("--limit", type=int, default=8, help=localized("最多返回条数。", "Maximum result count."))
    recall.add_argument(
        "--json",
        action="store_true",
        help=localized("以 JSON 打印结果。", "Print results as JSON."),
    )

    open_cmd = subparsers.add_parser(
        "open",
        help=localized("打开生成产物。", "Open a generated artifact."),
    )
    open_cmd.add_argument(
        "target",
        nargs="?",
        default="panel",
        choices=["panel", "overview", "review", "app"],
        help=localized("要打开的产物。", "Artifact to open."),
    )
    open_cmd.add_argument(
        "--date",
        default=current_date_str(),
        help=localized(
            "open review 使用的目标日期。默认今天。",
            "Target date for 'open review'. Default: today.",
        ),
    )

    app_cmd = subparsers.add_parser(
        "app",
        help=localized(
            "构建或打开轻量 macOS 客户端。",
            "Build or open the lightweight macOS client.",
        ),
    )
    app_cmd.add_argument(
        "--build",
        action="store_true",
        help=localized(
            "即使客户端已存在，也重新构建。",
            "Rebuild the client even when it already exists.",
        ),
    )
    app_cmd.add_argument(
        "--no-open",
        action="store_true",
        help=localized(
            "只构建并打印路径，不打开客户端。",
            "Only build and print the path; do not open the client.",
        ),
    )
    app_cmd.add_argument(
        "--output",
        help=localized(
            "客户端 .app 输出路径；默认安装到 ~/Applications/OpenRelix.app。",
            "Output path for the .app bundle; default is ~/Applications/OpenRelix.app.",
        ),
    )
    app_cmd.add_argument(
        "--print-path",
        action="store_true",
        help=localized(
            "只打印默认客户端路径。",
            "Only print the default client path.",
        ),
    )

    subparsers.add_parser(
        "paths",
        help=localized("打印重要运行路径。", "Print important runtime paths."),
    )
    subparsers.add_parser("help", help=localized("显示帮助。", "Show help."))
    return parser


def current_date_str():
    return datetime.now().astimezone().date().isoformat()


def parse_date_arg(value, label):
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise SystemExit(localized(
            "{} 必须是 YYYY-MM-DD: {}".format(label, value),
            "{} must be YYYY-MM-DD: {}".format(label, value),
        )) from exc


def resolve_backfill_dates(args):
    if args.dates:
        raw_dates = [
            item
            for item in re.split(r"[,\s]+", str(args.dates).strip())
            if item
        ]
        if not raw_dates:
            raise SystemExit(localized(
                "--dates 不能为空。",
                "--dates cannot be empty.",
            ))
        parsed_dates = sorted({parse_date_arg(item, "--dates") for item in raw_dates})
        return [item.isoformat() for item in parsed_dates]

    end_date = parse_date_arg(args.date_to, "--to")
    if args.date_from:
        start_date = parse_date_arg(args.date_from, "--from")
    elif args.days > 0:
        start_date = end_date - timedelta(days=args.days - 1)
    else:
        raise SystemExit(localized(
            "backfill 需要 --from 或 --days。",
            "backfill requires --from or --days.",
        ))

    if start_date > end_date:
        raise SystemExit(localized(
            "--from 不能晚于 --to。",
            "--from cannot be later than --to.",
        ))

    total_days = (end_date - start_date).days + 1
    return [
        (start_date + timedelta(days=offset)).isoformat()
        for offset in range(total_days)
    ]


def learning_window_dates(date_str, learn_window_days):
    days = max(int(learn_window_days or 0), 0)
    if days <= 0:
        return []
    target_date = parse_date_arg(date_str, "--date")
    return [
        (target_date - timedelta(days=offset)).isoformat()
        for offset in range(days, 0, -1)
    ]


def unique_ordered(items):
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def learning_window_dates_for_targets(date_strs, learn_window_days, exclude_dates=None):
    excluded = set(exclude_dates or [])
    dates = []
    for date_str in date_strs:
        for learning_date in learning_window_dates(date_str, learn_window_days):
            if learning_date not in excluded:
                dates.append(learning_date)
    return unique_ordered(dates)


def codex_history_dates_for_targets(target_dates):
    targets = set(target_dates)
    if not targets:
        return set()

    history_path = PATHS.codex_home / "history.jsonl"
    if not history_path.exists():
        return set()

    found = set()
    try:
        raw_lines = history_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return set()

    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            date_str = datetime.fromtimestamp(int(item["ts"])).astimezone().date().isoformat()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if date_str in targets:
            found.add(date_str)
            if found == targets:
                break
    return found


def resolve_learning_backfill_dates(date_str, learn_window_days, requested_stage="final"):
    dates = learning_window_dates(date_str, learn_window_days)
    if not dates:
        return []

    history_dates = codex_history_dates_for_targets(dates)
    missing_dates = []
    for candidate_date in dates:
        needs_run, _, _ = review_summary_needs_run(candidate_date, requested_stage)
        if not needs_run:
            continue
        raw_daily_path = PATHS.raw_daily_dir / "{}.json".format(candidate_date)
        if raw_daily_path.exists() or candidate_date in history_dates:
            missing_dates.append(candidate_date)
    return missing_dates


def resolve_learning_backfill_dates_for_targets(date_strs, learn_window_days, exclude_dates=None, requested_stage="final"):
    excluded = set(exclude_dates or [])
    missing_dates = []
    for date_str in date_strs:
        for candidate_date in resolve_learning_backfill_dates(date_str, learn_window_days, requested_stage=requested_stage):
            if candidate_date not in excluded:
                missing_dates.append(candidate_date)
    return unique_ordered(missing_dates)


def review_summary_stage(date_str):
    summary_json_path, _ = review_summary_paths(date_str)
    if not summary_json_path.exists():
        return ""
    try:
        payload = load_json(summary_json_path)
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("stage") or "")


def has_reusable_lightweight_compact(date_str):
    raw_daily_path = PATHS.raw_daily_dir / "{}.json".format(date_str)
    compact_path = CONSOLIDATED_DAILY_DIR / date_str / "compact_payload.json"
    if not raw_daily_path.exists() or not compact_path.exists():
        return False
    return stage_rank(review_summary_stage(date_str)) >= STAGE_PRIORITY["preliminary"]


def stage_rank(stage):
    return STAGE_PRIORITY.get(str(stage or ""), -1)


def normalize_backfill_jobs(value):
    try:
        jobs = int(value)
    except (TypeError, ValueError):
        jobs = 1
    return max(1, min(jobs, MAX_BACKFILL_JOBS))


def effective_backfill_jobs(stage, jobs):
    if stage == "final":
        return 1
    return normalize_backfill_jobs(jobs)


def review_summary_needs_run(date_str, requested_stage, force=False):
    summary_json_path, summary_md_path = review_summary_paths(date_str)
    info = {
        "summary_json": str(summary_json_path),
        "summary_md": str(summary_md_path),
        "exists": summary_json_path.exists(),
        "stage": "",
    }
    if force:
        return True, "force", info
    if not summary_json_path.exists():
        return True, "missing_summary", info
    try:
        payload = load_json(summary_json_path)
    except (OSError, json.JSONDecodeError):
        return True, "invalid_summary", info
    info["stage"] = str(payload.get("stage") or "")
    if stage_rank(info["stage"]) < stage_rank(requested_stage):
        return True, "existing_stage_below_requested", info
    return False, "existing_stage_satisfies_request", info


def interruptible_popen_kwargs():
    if os.name == "posix":
        return {"start_new_session": True}
    return {}


def process_descendant_pids(root_pid):
    if os.name != "posix":
        return []
    try:
        output = subprocess.check_output(["ps", "-Ao", "pid=,ppid="], text=True)
    except (OSError, subprocess.SubprocessError):
        return []

    children_by_parent = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        children_by_parent.setdefault(ppid, []).append(pid)

    descendants = []
    stack = list(children_by_parent.get(root_pid, []))
    while stack:
        pid = stack.pop()
        descendants.append(pid)
        stack.extend(children_by_parent.get(pid, []))
    return descendants


def register_child_process(process):
    with _ACTIVE_CHILD_PROCESSES_LOCK:
        _ACTIVE_CHILD_PROCESSES.add(process)


def unregister_child_process(process):
    with _ACTIVE_CHILD_PROCESSES_LOCK:
        _ACTIVE_CHILD_PROCESSES.discard(process)


def child_signal_targets(process):
    if process.poll() is not None:
        return set(), set()
    if os.name != "posix":
        return set(), {process.pid}

    current_pgid = os.getpgrp()
    process_groups = set()
    individual_pids = set()
    for pid in [process.pid] + process_descendant_pids(process.pid):
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            continue
        except OSError:
            continue
        if pgid == current_pgid:
            individual_pids.add(pid)
        else:
            process_groups.add(pgid)
    return process_groups, individual_pids


def signal_child_targets(process_groups, individual_pids, signal_number):
    if os.name == "posix":
        for pgid in list(process_groups):
            try:
                os.killpg(pgid, signal_number)
            except ProcessLookupError:
                process_groups.discard(pgid)
            except OSError:
                process_groups.discard(pgid)
        for pid in list(individual_pids):
            try:
                os.kill(pid, signal_number)
            except ProcessLookupError:
                individual_pids.discard(pid)
            except OSError:
                individual_pids.discard(pid)
        return

    for pid in list(individual_pids):
        try:
            os.kill(pid, signal_number)
        except ProcessLookupError:
            individual_pids.discard(pid)
        except OSError:
            individual_pids.discard(pid)


def child_targets_alive(process_groups, individual_pids):
    if os.name == "posix":
        for pgid in list(process_groups):
            try:
                os.killpg(pgid, 0)
                return True
            except ProcessLookupError:
                process_groups.discard(pgid)
            except OSError:
                process_groups.discard(pgid)
        for pid in list(individual_pids):
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                individual_pids.discard(pid)
            except OSError:
                individual_pids.discard(pid)
        return False

    for pid in list(individual_pids):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            individual_pids.discard(pid)
        except OSError:
            individual_pids.discard(pid)
    return False


def send_signal_to_child_tree(process, signal_number):
    process_groups, individual_pids = child_signal_targets(process)
    if not process_groups and not individual_pids:
        return
    signal_child_targets(process_groups, individual_pids, signal_number)


def stop_child_process_tree(process):
    process_groups, individual_pids = child_signal_targets(process)
    if not process_groups and not individual_pids:
        return
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal_child_targets(process_groups, individual_pids, signal_number)
        try:
            process.wait(timeout=2)
            if not child_targets_alive(process_groups, individual_pids):
                return
        except subprocess.TimeoutExpired:
            if not child_targets_alive(process_groups, individual_pids):
                return
    if hasattr(signal, "SIGKILL"):
        signal_child_targets(process_groups, individual_pids, signal.SIGKILL)
    else:
        process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()


def stop_active_child_processes():
    with _ACTIVE_CHILD_PROCESSES_LOCK:
        processes = list(_ACTIVE_CHILD_PROCESSES)
    for process in processes:
        stop_child_process_tree(process)


def stop_active_child_processes_for_signal(signum, _frame):
    stop_active_child_processes()
    raise SystemExit(128 + signum)


def install_termination_signal_handlers():
    if os.name != "posix":
        return
    for signal_name in ("SIGHUP", "SIGTERM"):
        signal_number = getattr(signal, signal_name, None)
        if signal_number is None:
            continue
        signal.signal(signal_number, stop_active_child_processes_for_signal)


def run_checked(cmd):
    subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)


def run_capture_interruptible(cmd):
    process = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **interruptible_popen_kwargs(),
    )
    register_child_process(process)
    try:
        stdout, stderr = process.communicate()
    except KeyboardInterrupt:
        stop_child_process_tree(process)
        raise
    finally:
        unregister_child_process(process)
    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)


def run_checked_quiet(cmd):
    result = run_capture_interruptible(cmd)
    if result.returncode == 0:
        return result
    print(
        localized(
            "子流程执行失败，保留原始输出用于排查：",
            "Subprocess failed; raw output follows for debugging:",
        ),
        file=sys.stderr,
    )
    if result.stdout.strip():
        print(result.stdout.strip(), file=sys.stderr)
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    raise subprocess.CalledProcessError(
        result.returncode,
        cmd,
        output=result.stdout,
        stderr=result.stderr,
    )


def run_warning_only(cmd, warning):
    result = run_capture_interruptible(cmd)
    if result.returncode == 0:
        return True
    print(warning, file=sys.stderr)
    return False


def run_warning_only_with_progress(cmd, warning, progress_messages):
    try:
        run_checked_with_progress(
            cmd,
            progress_messages,
            reminder_zh="仍在刷新: 已等待约 {} 分钟，当前同步步骤仍在运行。",
            reminder_en="Still refreshing: waited about {} minutes; the current sync step is still running.",
        )
        return True
    except subprocess.CalledProcessError:
        print(warning, file=sys.stderr)
        return False


def run_checked_with_progress(
    cmd,
    progress_messages,
    interval_seconds=20,
    reminder_seconds=60,
    reminder_zh="仍在整理: 已等待约 {} 分钟，子流程仍在运行。",
    reminder_en="Still organizing: waited about {} minutes; the subprocess is still running.",
):
    process = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **interruptible_popen_kwargs(),
    )
    register_child_process(process)
    message_index = 0
    started_at = time.monotonic()
    next_reminder_at = reminder_seconds
    stdout = ""
    stderr = ""
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=interval_seconds)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - started_at
                if message_index < len(progress_messages):
                    print(progress_messages[message_index], flush=True)
                    message_index += 1
                elif elapsed >= next_reminder_at:
                    elapsed_minutes = max(1, int(round(elapsed / 60.0)))
                    print(
                        localized(
                            reminder_zh.format(elapsed_minutes),
                            reminder_en.format(elapsed_minutes),
                        ),
                        flush=True,
                    )
                    next_reminder_at += reminder_seconds
    except KeyboardInterrupt:
        stop_child_process_tree(process)
        raise
    finally:
        unregister_child_process(process)

    if process.returncode != 0:
        print(
            localized(
                "子流程执行失败，保留原始输出用于排查：",
                "Subprocess failed; raw output follows for debugging:",
            ),
            file=sys.stderr,
        )
        if stdout.strip():
            print(stdout.strip(), file=sys.stderr)
        if stderr.strip():
            print(stderr.strip(), file=sys.stderr)
        raise subprocess.CalledProcessError(
            process.returncode,
            cmd,
            output=stdout,
            stderr=stderr,
        )


def read_local_package_version():
    return get_project_version(REPO_ROOT, fallback="")


def cli_tool_search_path():
    current = os.environ.get("PATH") or os.defpath
    parts = [part for part in current.split(os.pathsep) if part]
    for path in COMMON_CLI_TOOL_PATHS:
        if path not in parts:
            parts.append(path)
    return os.pathsep.join(parts)


def cli_tool_env():
    env = os.environ.copy()
    env["PATH"] = cli_tool_search_path()
    return env


def resolve_cli_tool(tool_name):
    return shutil.which(tool_name, path=cli_tool_search_path())


def fetch_latest_npm_version(package_name=NPM_PACKAGE_NAME, timeout=8):
    url = "https://registry.npmjs.org/{}/latest".format(package_name)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "openrelix-update-check"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return str(payload.get("version") or "").strip()


def check_npm_package_version_installable(package_name, version, timeout=12):
    npm_bin = resolve_cli_tool("npm")
    if not npm_bin or not version:
        return ""
    spec = "{}@{}".format(package_name, version)
    try:
        proc = subprocess.run(
            [npm_bin, "view", spec, "version"],
            cwd=str(REPO_ROOT),
            env=cli_tool_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode == 0 and proc.stdout.strip() == version:
        return ""
    output = "\n".join(line for line in ((proc.stderr or "") + (proc.stdout or "")).splitlines()[-8:] if line)
    lowered = output.lower()
    if "etarget" in lowered or "notarget" in lowered or "no matching version" in lowered:
        return output
    return ""


def semantic_version_key(version):
    parts = re.split(r"[^0-9]+", str(version or ""))
    numeric = [int(part) for part in parts if part != ""]
    return tuple((numeric + [0, 0, 0])[:3])


def compare_versions(current, latest):
    current_key = semantic_version_key(current)
    latest_key = semantic_version_key(latest)
    if current_key < latest_key:
        return -1
    if current_key > latest_key:
        return 1
    return 0


def launch_agent_path(filename):
    return PATHS.launch_agents_dir / filename


def read_launch_agent(filename):
    path = launch_agent_path(filename)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def launch_agent_exists(filename):
    return launch_agent_path(filename).exists()


def resolve_python_bin_for_launch_agent():
    return os.environ.get("PYTHON_BIN") or shutil.which("python3") or sys.executable


def token_live_health_payload(timeout=0.75):
    request = urllib.request.Request(
        TOKEN_LIVE_HEALTH_URL,
        headers={"Accept": "application/json", "User-Agent": "openrelix-cli"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except (OSError, TimeoutError, UnicodeDecodeError, urllib.error.URLError):
        return None
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def token_live_health_matches_current(payload):
    if not isinstance(payload, dict):
        return False
    if not (bool(payload.get("ok")) and payload.get("service") == "token-live"):
        return False

    current_version = read_local_package_version()
    service_version = str(payload.get("version") or "").strip()
    if current_version and service_version != current_version:
        return False

    expected_script_path = (REPO_ROOT / "scripts" / "token_live_server.py").resolve()
    service_script_path = str(payload.get("script_path") or "").strip()
    if not service_script_path:
        return False
    try:
        if Path(service_script_path).expanduser().resolve() != expected_script_path:
            return False
    except OSError:
        return False
    return True


def token_live_health_ok(timeout=0.75):
    return token_live_health_matches_current(token_live_health_payload(timeout=timeout))


def parse_openrelix_token_live_processes(ps_output, current_pid=None):
    current_pid = os.getpid() if current_pid is None else current_pid
    matches = []
    for raw_line in str(ps_output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == current_pid:
            continue
        command = parts[1]
        lower_command = command.lower()
        if "token_live_server.py" in lower_command and "openrelix" in lower_command:
            matches.append((pid, command))
    return matches


def token_live_pid_alive(pid, kill_func=os.kill):
    try:
        kill_func(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def stop_stale_token_live_processes(
    ps_runner=subprocess.run,
    kill_func=os.kill,
    sleep_func=time.sleep,
    alive_func=None,
):
    try:
        result = ps_runner(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
    except (OSError, UnicodeDecodeError):
        return []

    alive_func = alive_func or (lambda pid: token_live_pid_alive(pid, kill_func=kill_func))
    stopped = []
    for pid, _command in parse_openrelix_token_live_processes(getattr(result, "stdout", "")):
        try:
            kill_func(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except OSError:
            continue
        stopped.append(pid)

    if not stopped:
        return []

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not any(alive_func(pid) for pid in stopped):
            return stopped
        sleep_func(0.1)

    for pid in stopped:
        if not alive_func(pid):
            continue
        try:
            kill_func(pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            continue
    return stopped


def render_token_live_launch_agent():
    if not RENDER_TEMPLATE_SCRIPT.exists() or not TOKEN_LIVE_TEMPLATE.exists():
        raise FileNotFoundError("missing token-live launchd template")

    python_bin = resolve_python_bin_for_launch_agent()
    plist_path = launch_agent_path(TOKEN_LIVE_PLIST_NAME)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    PATHS.log_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            python_bin,
            str(RENDER_TEMPLATE_SCRIPT),
            "--template",
            str(TOKEN_LIVE_TEMPLATE),
            "--output",
            str(plist_path),
            "--set",
            "REPO_ROOT={}".format(REPO_ROOT),
            "--set",
            "STATE_ROOT={}".format(PATHS.state_root),
            "--set",
            "PYTHON_BIN={}".format(python_bin),
            "--set",
            "CODEX_HOME={}".format(PATHS.codex_home),
        ],
        check=True,
    )
    subprocess.run(
        ["/usr/bin/plutil", "-lint", str(plist_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return plist_path


def bootstrap_token_live_launch_agent(plist_path):
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", "gui/{}/{}".format(uid, TOKEN_LIVE_LABEL)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["launchctl", "bootout", "gui/{}".format(uid), str(plist_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    stop_stale_token_live_processes()
    subprocess.run(["launchctl", "bootstrap", "gui/{}".format(uid), str(plist_path)], check=True)
    subprocess.run(
        ["launchctl", "kickstart", "-k", "gui/{}/{}".format(uid, TOKEN_LIVE_LABEL)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_token_live_health(timeout_seconds=TOKEN_LIVE_STARTUP_TIMEOUT_SECONDS):
    deadline = time.monotonic() + max(float(timeout_seconds or 0), 0.0)
    while time.monotonic() <= deadline:
        if token_live_health_ok(timeout=0.5):
            return True
        time.sleep(0.25)
    return token_live_health_ok(timeout=0.5)


def ensure_token_live_service(verbose=True):
    if token_live_health_ok():
        return True
    if sys.platform != "darwin" or not shutil.which("launchctl"):
        return False
    try:
        plist_path = render_token_live_launch_agent()
        bootstrap_token_live_launch_agent(plist_path)
        if wait_for_token_live_health():
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    if verbose:
        print(
            localized(
                "本地 Token 服务未启动；已保留离线快照。可运行 openrelix install --enable-background-services 修复后台服务。",
                "The local Token service is not running; the panel will keep using the offline snapshot. Run openrelix install --enable-background-services to repair background services.",
            ),
            file=sys.stderr,
        )
    return False


def plist_string_value(text, key):
    pattern = r"<key>{}</key>\s*<string>(.*?)</string>".format(re.escape(key))
    match = re.search(pattern, text, flags=re.DOTALL)
    return match.group(1).strip() if match else ""


def plist_calendar_time(text):
    hour_match = re.search(r"<key>Hour</key>\s*<integer>(\d+)</integer>", text)
    minute_match = re.search(r"<key>Minute</key>\s*<integer>(\d+)</integer>", text)
    if not hour_match or not minute_match:
        return ""
    return "{:02d}:{:02d}".format(int(hour_match.group(1)), int(minute_match.group(1)))


def plist_interval_minutes(text):
    interval_match = re.search(r"<key>StartInterval</key>\s*<integer>(\d+)</integer>", text)
    if not interval_match:
        return None
    seconds = int(interval_match.group(1))
    if seconds <= 0:
        return None
    return max(1, int(round(seconds / 60)))


def detected_update_install_flags():
    flags = []
    overview_text = read_launch_agent(OVERVIEW_REFRESH_PLIST_NAME)
    if overview_text:
        if plist_string_value(overview_text, "OPENRELIX_REFRESH_LEARN_MEMORY") == "1":
            flags.append("--enable-learning-refresh")
        else:
            flags.append("--enable-background-services")
        overview_interval = plist_interval_minutes(overview_text)
        if overview_interval:
            flags.extend(["--overview-refresh-interval-minutes", str(overview_interval)])

    nightly_text = read_launch_agent(NIGHTLY_ORGANIZE_PLIST_NAME)
    if nightly_text:
        flags.append("--enable-nightly")
        keep_awake = plist_string_value(nightly_text, "AI_ASSET_KEEP_AWAKE")
        if keep_awake in {"none", "during-job"}:
            flags.extend(["--keep-awake", keep_awake])
        nightly_time = plist_calendar_time(nightly_text)
        if nightly_time:
            flags.extend(["--nightly-organize-time", nightly_time])

    nightly_finalize_text = read_launch_agent(NIGHTLY_FINALIZE_PLIST_NAME)
    nightly_finalize_time = plist_calendar_time(nightly_finalize_text)
    if nightly_finalize_time:
        flags.extend(["--nightly-finalize-time", nightly_finalize_time])

    update_check_text = read_launch_agent(UPDATE_CHECK_PLIST_NAME)
    if update_check_text:
        flags.append("--enable-update-check")
        update_check_time = plist_calendar_time(update_check_text)
        if update_check_time:
            flags.extend(["--update-check-time", update_check_time])
    return flags


def build_update_install_command(recommended=False, npx_bin=None, package_spec=None):
    cmd = [
        npx_bin or "npx",
        "-y",
        package_spec or NPM_LATEST_SPEC,
        "install",
        "--state-dir",
        str(PATHS.state_root),
        "--codex-home",
        str(PATHS.codex_home),
        "--language",
        LANGUAGE,
        "--memory-mode",
        MEMORY_MODE,
        "--activity-source",
        ACTIVITY_SOURCE,
    ]
    if recommended:
        cmd.extend(
            [
                "--enable-learning-refresh",
                "--enable-nightly",
                "--keep-awake",
                "during-job",
                "--enable-update-check",
                "--update-check-time",
                "09:30",
            ]
        )
    else:
        cmd.extend(detected_update_install_flags())
    return cmd


def update_install_cwd():
    path = PATHS.runtime_dir / "npm-update"
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return Path(tempfile.gettempdir())


def ensure_overview_snapshot():
    overview_path = REPORTS_DIR / "overview-data.json"
    if overview_path.exists():
        return overview_path
    run_checked([sys.executable, str(BUILD_OVERVIEW_SCRIPT)])
    return overview_path


def rebuild_sqlite_index_if_available(verbose=False):
    if os.environ.get("OPENRELIX_DISABLE_SQLITE_INDEX_REBUILD", "0") == "1":
        return
    index_script = REPO_ROOT / "scripts" / "openrelix_index.py"
    if not index_script.exists():
        return
    cmd = [sys.executable, str(index_script), "rebuild"]
    warning = "openrelix: sqlite index rebuild failed; JSONL/raw outputs remain authoritative."
    if verbose:
        run_warning_only_with_progress(
            cmd,
            warning,
            [
                localized(
                    "仍在刷新: 正在重建搜索索引，历史窗口较多时可能需要几分钟。",
                    "Still refreshing: rebuilding the search index; this may take a few minutes with many historical windows.",
                ),
                localized(
                    "仍在刷新: 索引重建还在运行，完成后会继续同步摘要和面板。",
                    "Still refreshing: index rebuild is still running; summary and panel sync will continue afterward.",
                ),
            ],
        )
        return
    run_warning_only(cmd, warning)


def build_codex_native_display_cache_if_enabled(verbose=False):
    display_polish = os.environ.get("OPENRELIX_ENABLE_NATIVE_DISPLAY_POLISH", "auto").strip().lower()
    if display_polish in {"0", "false", "no", "off", "disabled"}:
        return
    if display_polish in {"auto", ""} and get_runtime_language(PATHS) != "zh":
        return
    if display_polish not in {"1", "true", "yes", "on", "enabled", "auto", ""}:
        return
    if get_memory_mode(PATHS) != "integrated":
        return
    if not BUILD_CODEX_NATIVE_DISPLAY_CACHE_SCRIPT.exists():
        return
    cmd = [sys.executable, str(BUILD_CODEX_NATIVE_DISPLAY_CACHE_SCRIPT)]
    warning = "openrelix: codex native display polish failed; using source-text fallback."
    if verbose:
        run_warning_only_with_progress(
            cmd,
            warning,
            [
                localized(
                    "仍在刷新: 正在整理中文记忆卡展示缓存。",
                    "Still refreshing: polishing the Chinese memory-card display cache.",
                ),
                localized(
                    "仍在刷新: 展示缓存还在生成，完成后会继续重建面板。",
                    "Still refreshing: display cache generation is still running; panel rebuild will continue afterward.",
                ),
            ],
        )
        return
    run_warning_only(cmd, warning)


def task_summary_migration_disabled():
    return str(os.environ.get("OPENRELIX_DISABLE_TASK_SUMMARY_MIGRATION", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def run_task_summary_migration_if_needed(verbose=False):
    if task_summary_migration_disabled():
        return
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "openrelix.py"),
        "task-summary-migration",
        "run",
        "--if-pending",
        "--quiet",
    ]
    warning = "openrelix: task summary migration failed; existing rule-based task grouping remains available."
    if verbose:
        run_warning_only_with_progress(
            cmd,
            warning,
            [
                localized(
                    "仍在刷新: 正在基于已整理窗口补算并行任务总结。",
                    "Still refreshing: generating parallel task summaries from organized windows.",
                ),
                localized(
                    "仍在刷新: 并行任务总结还在生成，完成后会继续重建面板。",
                    "Still refreshing: parallel task summaries are still being generated; panel rebuild will continue afterward.",
                ),
            ],
        )
        return
    run_warning_only(cmd, warning)


def run_task_summary_for_dates_warning_only(dates, verbose=False):
    if task_summary_migration_disabled():
        return
    normalized_dates = [str(date_str) for date_str in dates or [] if str(date_str or "").strip()]
    if not normalized_dates:
        return
    state = ensure_task_summary_migration_state(PATHS, window_days=TASK_SUMMARY_WINDOW_DAYS, force=False)
    if state.get("status") == "pending":
        return
    try:
        result = run_task_summary_for_dates(normalized_dates, paths=PATHS, force=False)
    except Exception as exc:
        print(
            localized(
                "openrelix: 并行任务总结生成失败；已继续使用规则聚合，可稍后重试。",
                "openrelix: task summary generation failed; rule-based task grouping remains available. Retry later.",
            ),
            file=sys.stderr,
        )
        return
    if verbose and result.get("status") == "completed":
        print(
            localized(
                "并行任务总结已更新: {} 个任务簇。".format(result.get("cluster_count", 0)),
                "Parallel task summaries updated: {} task clusters.".format(result.get("cluster_count", 0)),
            ),
            flush=True,
        )


def sync_review_outputs(include_index=False, include_native_display=False, verbose=False):
    if verbose:
        print(
            localized(
                "刷新提示: 最后同步会更新搜索索引、host context 摘要和面板；历史数据较多时可能需要几分钟，请保持终端打开。",
                "Refresh note: final sync updates the search index, host context summary, and panel; with more history this can take a few minutes, so keep this terminal open.",
            ),
            flush=True,
        )
    if include_index:
        if verbose:
            print(localized("刷新中 [1/5]: 重建搜索索引。", "Refreshing [1/5]: rebuilding the search index."), flush=True)
        rebuild_sqlite_index_if_available(verbose=verbose)
    if verbose:
        print(
            localized(
                "刷新中 [2/5]: 同步或清理 host context 摘要。",
                "Refreshing [2/5]: syncing or clearing the host context summary.",
            ),
            flush=True,
        )
    cmd = [
        sys.executable,
        str(SYNC_HOST_MEMORY_SUMMARY_SCRIPT),
    ]
    if verbose and get_memory_mode(PATHS) == "integrated":
        run_checked_with_progress(
            cmd,
            [
                localized(
                    "仍在刷新: 正在汇总可注入 Codex / Claude Code 的记忆摘要。",
                    "Still refreshing: building the memory summary that Codex / Claude Code can inject.",
                ),
                localized(
                    "仍在刷新: host context 摘要还在生成，完成后会继续更新面板。",
                    "Still refreshing: host context summary is still being generated; panel update will continue afterward.",
                ),
            ],
            reminder_zh="仍在刷新: 已等待约 {} 分钟，host context 摘要仍在生成。",
            reminder_en="Still refreshing: waited about {} minutes; host context summary is still being generated.",
        )
    else:
        run_checked_quiet(cmd)
    if include_native_display and verbose:
        print(
            localized(
                "刷新中 [3/5]: 更新记忆卡展示缓存。",
                "Refreshing [3/5]: updating memory-card display cache.",
            ),
            flush=True,
        )
    if include_native_display:
        build_codex_native_display_cache_if_enabled(verbose=verbose)
    elif verbose:
        print(
            localized(
                "刷新中 [3/5]: 跳过记忆卡展示缓存。",
                "Refreshing [3/5]: skipping memory-card display cache.",
            ),
            flush=True,
        )
    if verbose:
        print(localized("刷新中 [4/5]: 补算并行任务总结。", "Refreshing [4/5]: generating parallel task summaries."), flush=True)
    run_task_summary_migration_if_needed(verbose=verbose)
    if verbose:
        print(localized("刷新中 [5/5]: 重建 overview 和面板。", "Refreshing [5/5]: rebuilding overview and panel."), flush=True)
    cmd = [sys.executable, str(BUILD_OVERVIEW_SCRIPT)]
    if verbose:
        run_checked_with_progress(
            cmd,
            [
                localized(
                    "仍在刷新: 正在生成 overview 数据和 panel.html。",
                    "Still refreshing: generating overview data and panel.html.",
                ),
                localized(
                    "仍在刷新: 面板重建还在运行，完成后会显示最终结果。",
                    "Still refreshing: panel rebuild is still running; final results will be available afterward.",
                ),
            ],
            reminder_zh="仍在刷新: 已等待约 {} 分钟，面板重建仍在运行。",
            reminder_en="Still refreshing: waited about {} minutes; panel rebuild is still running.",
        )
        print(
            localized(
                "刷新完成: 面板和摘要已更新；如果浏览器面板或 OpenRelix app 已经打开，请手动刷新当前页面或 app。",
                "Refresh complete: panel and summary are updated; if the browser panel or OpenRelix app is already open, refresh it manually.",
            ),
            flush=True,
        )
    else:
        run_checked_quiet(cmd)


def print_preliminary_ready_message():
    print("")
    print(
        localized(
            "轻度回溯已完成，OpenRelix 现在可以先使用了；如果浏览器面板或 app 已经打开，手动刷新即可看到快速总结。后续深度回溯会继续补全更准确的终版记忆和日报。",
            "Lightweight backfill is complete. OpenRelix is ready to use now; if the browser panel or app is already open, refresh it to see the quick summary. A later deep backfill will fill in more accurate final memories and daily summaries.",
        )
    )


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_overview():
    overview_path = ensure_overview_snapshot()
    return load_json(overview_path)


def review_summary_paths(date_str):
    summary_dir = CONSOLIDATED_DAILY_DIR / date_str
    return summary_dir / "summary.json", summary_dir / "summary.md"


def load_review_summary_if_available(date_str):
    summary_json_path, _ = review_summary_paths(date_str)
    if not summary_json_path.exists():
        return None
    try:
        return load_json(summary_json_path)
    except (OSError, json.JSONDecodeError):
        return None


def summary_has_model_failure(summary):
    if not summary:
        return False
    if summary.get("last_run_model_status") == "failed" or summary.get("model_status") == "failed":
        return True
    decision = summary.get("selection_decision") or {}
    return decision.get("candidate_model_status") == "failed"


def failed_result_exit_code(results):
    if not results:
        return 0
    try:
        return max(1, int(results[0].get("returncode", 1)))
    except (TypeError, ValueError):
        return 1


def summary_model_failure_hint(summary):
    if not summary:
        return ""
    for key in ("last_run_model_error_hint", "model_error_hint"):
        if summary.get(key):
            return str(summary.get(key))
    decision = summary.get("selection_decision") or {}
    return str(decision.get("candidate_model_error_hint") or "")


def print_model_failure_warning(summary, date_str):
    hint = summary_model_failure_hint(summary)
    print(
        localized(
            "学习刷新未完整成功：模型归纳失败，当前只生成了保底摘要。",
            "Learning refresh did not fully succeed: model summarization failed, so only a fallback summary was generated.",
        ),
        file=sys.stderr,
    )
    if hint:
        print(hint, file=sys.stderr)
    print(
        localized(
            "修复认证后重试：openrelix refresh --learn-memory --date {}".format(date_str),
            "After fixing authentication, retry: openrelix refresh --learn-memory --date {}".format(date_str),
        ),
        file=sys.stderr,
    )


def print_json(payload):
    try:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    except BrokenPipeError:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
        raise SystemExit(0)


def format_metric_row(metric):
    parts = ["{}: {}".format(metric.get("label", metric.get("key", "")), metric.get("value", "—"))]
    caption = metric.get("caption")
    meta = metric.get("meta")
    if caption:
        parts.append(caption)
    if meta:
        parts.append(meta)
    return " | ".join(parts)


def format_learning_digest(summary, fallback_days=0):
    digest = summary.get("learning_context_digest") or {}
    days = digest.get("recent_window_learning_days") or fallback_days
    if not days:
        return None
    if "recent_window_learning_windows" in digest:
        return localized(
            "窗口学习: 近 {} 天 | 扫描: {} 天 | 有窗口日期: {} 天 | 全量历史窗口: {} | 批次: {} | 注入样本: {} | 模式: {}".format(
                days,
                digest.get("recent_window_learning_scanned_days", days),
                digest.get("recent_window_learning_source_dates", 0),
                digest.get("recent_window_learning_windows", 0),
                digest.get("recent_window_learning_batches", 0),
                digest.get("recent_window_learning_samples", 0),
                digest.get("recent_window_learning_patterns", 0),
            ),
            "Window learning: last {} days | scanned: {} days | source dates: {} days | full windows: {} | batches: {} | injected samples: {} | patterns: {}".format(
                days,
                digest.get("recent_window_learning_scanned_days", days),
                digest.get("recent_window_learning_source_dates", 0),
                digest.get("recent_window_learning_windows", 0),
                digest.get("recent_window_learning_batches", 0),
                digest.get("recent_window_learning_samples", 0),
                digest.get("recent_window_learning_patterns", 0),
            ),
        )
    return localized("窗口学习: 近 {} 天".format(days), "Window learning: last {} days".format(days))


def print_core_summary(data):
    print(localized("核心数据", "Core Data"))
    print("{}: {}".format(localized("快照时间", "Snapshot time"), data.get("generated_at", "—")))
    print("")
    for metric in data.get("metrics", []):
        print("- {}".format(format_metric_row(metric)))

    nightly = data.get("nightly") or {}
    if nightly:
        print("")
        print(localized("今日复盘", "Today Review"))
        print("{}: {}".format(localized("日期", "Date"), nightly.get("date", "—")))
        print("{}: {}".format(localized("摘要", "Summary"), nightly.get("day_summary", "—")))
        print(
            localized(
                "窗口: {} | 长期记忆: {} | 工作记忆: {} | 低优先记忆: {}".format(
                    nightly.get("raw_window_count", len(nightly.get("window_summaries", []))),
                    len(nightly.get("durable_memories", [])),
                    len(nightly.get("session_memories", [])),
                    len(nightly.get("low_priority_memories", [])),
                ),
                "Windows: {} | Long-term: {} | Work: {} | Low-priority: {}".format(
                    nightly.get("raw_window_count", len(nightly.get("window_summaries", []))),
                    len(nightly.get("durable_memories", [])),
                    len(nightly.get("session_memories", [])),
                    len(nightly.get("low_priority_memories", [])),
                ),
            )
        )

    print("")
    print(localized("入口", "Entrypoints"))
    print("- panel: {}".format(REPORTS_DIR / "panel.html"))
    print("- overview: {}".format(REPORTS_DIR / "overview.md"))


def append_doctor_check(checks, name, status, detail="", action=""):
    checks.append(
        {
            "name": name,
            "status": status,
            "detail": detail,
            "action": action,
        }
    )


def command_exists(command):
    command_text = str(command or "")
    if not command_text:
        return False
    command_path = Path(command_text).expanduser()
    if command_path.is_absolute() or "/" in command_text:
        return command_path.exists() and os.access(command_path, os.X_OK)
    return shutil.which(command_text) is not None


def run_doctor_codex_model_check():
    PATHS.nightly_runner_dir.mkdir(parents=True, exist_ok=True)
    sync_codex_exec_home(PATHS.codex_home, PATHS.nightly_codex_home)

    env = dict(os.environ)
    env["CODEX_HOME"] = str(PATHS.nightly_codex_home)
    return subprocess.run(
        [
            PATHS.codex_bin,
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
            "--model",
            get_codex_model(PATHS),
            "-c",
            'approval_policy="never"',
            "-c",
            'history.persistence="none"',
            "-c",
            "history.max_bytes=1048576",
            "-",
        ],
        input="Reply exactly: OPENRELIX_DOCTOR_OK\n",
        text=True,
        capture_output=True,
        timeout=45,
        env=env,
    )


def parse_env_file_value(raw_value):
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_claude_env_file(path):
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


def build_doctor_claude_env():
    return build_claude_cli_env(
        claude_home=PATHS.claude_home,
        env_file_values=load_claude_env_file(get_claude_env_file(PATHS)),
    )


def build_doctor_claude_command():
    cmd = [
        PATHS.claude_bin,
        "-p",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--tools=",
    ]
    claude_model = get_claude_model(PATHS)
    if claude_model and claude_model != "auto":
        cmd.extend(["--model", claude_model])
    claude_settings = get_claude_settings(PATHS)
    if claude_settings:
        cmd.extend(["--settings", claude_settings])
    return cmd


def run_doctor_claude_model_check():
    PATHS.nightly_runner_dir.mkdir(parents=True, exist_ok=True)
    PATHS.claude_home.mkdir(parents=True, exist_ok=True)
    env = build_doctor_claude_env()
    return subprocess.run(
        build_doctor_claude_command(),
        input="Reply exactly: OPENRELIX_DOCTOR_OK\n",
        text=True,
        capture_output=True,
        timeout=45,
        env=env,
        cwd=str(PATHS.nightly_runner_dir),
    )


def run_doctor_model_check():
    if get_model_cli(PATHS) == "claude":
        return run_doctor_claude_model_check()
    return run_doctor_codex_model_check()


def doctor_model_check_detail(model_cli, output):
    text = str(output or "").strip()
    if not text:
        return ""
    if "OPENRELIX_DOCTOR_OK" in text:
        return "OPENRELIX_DOCTOR_OK"
    if model_cli == "claude":
        for line in text.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                result = str(payload.get("result") or "").strip()
                if "OPENRELIX_DOCTOR_OK" in result:
                    return "OPENRELIX_DOCTOR_OK"
                if result:
                    return result[-300:]
    return text[-300:]


def run_codex_app_server_help_check():
    app_server_bin = overview_codex_profiles.resolve_codex_app_server_binary(PATHS.codex_bin)
    return subprocess.run(
        [app_server_bin, "app-server", "--help"],
        text=True,
        capture_output=True,
        timeout=10,
    )


def run_doctor_app_server_check():
    app_server_bin = overview_codex_profiles.resolve_codex_app_server_binary(PATHS.codex_bin)
    with TemporaryDirectory(prefix="openrelix-app-server-check-") as tmpdir:
        env = dict(os.environ)
        env["AI_ASSET_STATE_DIR"] = tmpdir
        env["CODEX_HOME"] = str(PATHS.codex_home)
        env["CODEX_BIN"] = str(PATHS.codex_bin)
        env[overview_codex_profiles.CODEX_APP_SERVER_BIN_ENV] = app_server_bin
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "collect_codex_activity.py"),
                "--date",
                current_date_str(),
                "--stage",
                "manual",
                "--activity-source",
                "app-server",
                "--app-server-max-threads",
                "1",
                "--app-server-timeout",
                "8",
            ],
            text=True,
            capture_output=True,
            timeout=15,
            env=env,
        )


def sqlite_index_status_payload():
    import openrelix_index

    return openrelix_index.index_status(PATHS)


def append_sqlite_index_doctor_check(checks):
    try:
        status = sqlite_index_status_payload()
    except Exception as exc:
        append_doctor_check(
            checks,
            "sqlite_index",
            "warn",
            str(exc),
            localized(
                "运行 `openrelix index rebuild` 重建本地检索索引；JSONL/raw 仍是权威数据。",
                "Run `openrelix index rebuild` to rebuild the local search index; JSONL/raw remains authoritative.",
            ),
        )
        return

    detail = "path={db_path} exists={exists} schema={schema_version} memories={memory_rows} windows={window_rows} stale={stale}".format(
        **status
    )
    if status.get("error"):
        append_doctor_check(
            checks,
            "sqlite_index",
            "warn",
            "{} error={}".format(detail, status["error"]),
            localized(
                "索引库可删除重建：运行 `openrelix index rebuild`。",
                "The index database is rebuildable: run `openrelix index rebuild`.",
            ),
        )
        return
    if not status.get("exists"):
        append_doctor_check(
            checks,
            "sqlite_index",
            "warn",
            detail,
            localized(
                "尚未生成检索索引；运行 `openrelix index rebuild`。",
                "Search index has not been generated; run `openrelix index rebuild`.",
            ),
        )
        return
    if not status.get("ok") or status.get("stale"):
        append_doctor_check(
            checks,
            "sqlite_index",
            "warn",
            detail,
            localized(
                "索引已过期或 schema 不匹配；运行 `openrelix index rebuild`。",
                "Index is stale or schema mismatched; run `openrelix index rebuild`.",
            ),
        )
        return
    append_doctor_check(
        checks,
        "sqlite_index",
        "ok",
        detail,
    )


def command_doctor(args):
    checks = []

    append_doctor_check(
        checks,
        "state_root",
        "ok" if PATHS.state_root.exists() and os.access(PATHS.state_root, os.W_OK) else "fail",
        str(PATHS.state_root),
        localized("确认 state root 存在且当前用户可写。", "Make sure the state root exists and is writable by this user."),
    )
    append_doctor_check(
        checks,
        "codex_home",
        "ok" if PATHS.codex_home.exists() and os.access(PATHS.codex_home, os.W_OK) else "fail",
        str(PATHS.codex_home),
        localized("确认 CODEX_HOME 存在且当前用户可写。", "Make sure CODEX_HOME exists and is writable by this user."),
    )
    append_doctor_check(
        checks,
        "codex_bin",
        "ok" if command_exists(PATHS.codex_bin) else "fail",
        str(PATHS.codex_bin),
        localized("安装 Codex CLI，或通过 CODEX_BIN 指向可执行文件。", "Install Codex CLI, or point CODEX_BIN to the executable."),
    )
    append_doctor_check(
        checks,
        "claude_bin",
        "ok" if command_exists(PATHS.claude_bin) else "warn",
        str(PATHS.claude_bin),
        localized("如需用 Claude Code 做记忆回溯，请安装 Claude Code CLI，或通过 CLAUDE_BIN 指向可执行文件。", "Install Claude Code CLI, or point CLAUDE_BIN to the executable if you want Claude Code-backed memory consolidation."),
    )
    append_doctor_check(
        checks,
        "model_cli",
        "ok",
        get_model_cli(PATHS),
        localized(
            "OpenRelix 记忆回溯会使用这里配置的 CLI；可运行 openrelix config --model-cli codex|claude 切换。",
            "OpenRelix memory consolidation uses this configured CLI; switch with openrelix config --model-cli codex|claude.",
        ),
    )
    append_doctor_check(
        checks,
        "codex_model",
        "ok",
        get_codex_model(PATHS),
        localized(
            "OpenRelix 内部模型调用会通过 codex exec --model 显式指定；不改你的全局 Codex 默认模型。",
            "OpenRelix internal model calls pass codex exec --model explicitly; your global Codex default model is not changed.",
        ),
    )
    append_doctor_check(
        checks,
        "claude_model",
        "ok",
        get_claude_model(PATHS),
        localized(
            "仅当 model_cli=claude 时用于 OpenRelix 内部 claude -p 调用；auto 表示不传 --model，使用 Claude Code 自己的 provider/model。",
            "Used only when model_cli=claude for OpenRelix internal claude -p calls; auto means no --model is passed, so Claude Code chooses its own provider/model.",
        ),
    )
    claude_settings = get_claude_settings(PATHS)
    append_doctor_check(
        checks,
        "claude_settings",
        "ok" if not claude_settings or claude_settings.startswith("{") or Path(claude_settings).exists() else "warn",
        claude_settings or "(default)",
        localized(
            "第三方模型 / 桥接 provider 可通过 openrelix config --claude-settings <path-or-json> 传给 claude -p。",
            "Third-party models and bridge providers can be passed to claude -p with openrelix config --claude-settings <path-or-json>.",
        ),
    )
    claude_env_file = get_claude_env_file(PATHS)
    append_doctor_check(
        checks,
        "claude_env_file",
        "ok" if not claude_env_file or Path(claude_env_file).exists() else "warn",
        claude_env_file or "(none)",
        localized(
            "需要环境变量桥接时，把变量放进仓库外 env 文件，并用 openrelix config --claude-env-file <path> 指向它。",
            "When bridge mode depends on environment variables, put them in an env file outside the repo and point OpenRelix to it with openrelix config --claude-env-file <path>.",
        ),
    )
    append_doctor_check(
        checks,
        "activity_source",
        "ok",
        ACTIVITY_SOURCE,
        localized(
            "默认 auto 会优先读取 Codex 客户端 app-server，失败时回退 CLI history/session。",
            "Default auto reads Codex app-server first, then falls back to CLI history/session.",
        ),
    )
    append_doctor_check(
        checks,
        "activity_host",
        "ok",
        ACTIVITY_HOST,
        localized(
            "默认 all 会同时读取 Codex 与 Claude Code 窗口，并在 raw window 中保留 ai_host。",
            "Default all reads both Codex and Claude Code windows and preserves ai_host in raw windows.",
        ),
    )
    append_sqlite_index_doctor_check(checks)

    app_server_bin = overview_codex_profiles.resolve_codex_app_server_binary(PATHS.codex_bin)
    if command_exists(app_server_bin):
        try:
            result = run_codex_app_server_help_check()
            output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
            if result.returncode == 0:
                detail = output.splitlines()[0] if output else "available"
                append_doctor_check(
                    checks,
                    "codex_app_server_command",
                    "ok",
                    "{} via {}".format(detail, app_server_bin),
                )
            else:
                append_doctor_check(
                    checks,
                    "codex_app_server_command",
                    "warn",
                    output[-600:] or "codex app-server --help failed with exit code {}".format(result.returncode),
                    localized(
                        "升级 Codex CLI，或把 activity source 固定为 history。",
                        "Upgrade Codex CLI, or pin the activity source to history.",
                    ),
                )
        except (subprocess.TimeoutExpired, OSError) as exc:
            append_doctor_check(
                checks,
                "codex_app_server_command",
                "warn",
                str(exc),
                localized(
                    "升级 Codex CLI，或把 activity source 固定为 history。",
                    "Upgrade Codex CLI, or pin the activity source to history.",
                ),
            )

    if getattr(args, "app_server_check", False):
        if not command_exists(app_server_bin):
            append_doctor_check(
                checks,
                "codex_app_server_probe",
                "fail",
                str(app_server_bin),
                localized("先修复 codex_bin，再运行 --app-server-check。", "Fix codex_bin first, then rerun --app-server-check."),
            )
        else:
            try:
                result = run_doctor_app_server_check()
                output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
                if result.returncode == 0:
                    append_doctor_check(checks, "codex_app_server_probe", "ok", "app-server protocol probe completed")
                else:
                    append_doctor_check(
                        checks,
                        "codex_app_server_probe",
                        "fail",
                        output[-900:] or "app-server probe failed with exit code {}".format(result.returncode),
                        localized(
                            "升级 Codex CLI；或运行 `openrelix config --activity-source history` 强制使用 CLI history/session。",
                            "Upgrade Codex CLI, or run `openrelix config --activity-source history` to force CLI history/session.",
                        ),
                    )
            except subprocess.TimeoutExpired:
                append_doctor_check(
                    checks,
                    "codex_app_server_probe",
                    "fail",
                    "app-server probe timed out after 15 seconds",
                    localized(
                        "先确认 `codex app-server --listen stdio://` 在终端可启动。",
                        "Confirm `codex app-server --listen stdio://` can start in a terminal.",
                    ),
                )
    else:
        append_doctor_check(
            checks,
            "codex_app_server_probe",
            "warn",
            localized("未执行 app-server 协议探测。", "App-server protocol probe was not run."),
            localized(
                "需要验证 Codex 客户端采集时运行 openrelix doctor --app-server-check。",
                "Run openrelix doctor --app-server-check to verify Codex app collection.",
            ),
        )

    auth_path = PATHS.codex_home / "auth.json"
    append_doctor_check(
        checks,
        "codex_auth_file",
        "ok" if auth_path.exists() else "warn",
        str(auth_path),
        localized("如果需要模型学习刷新，请先完成 Codex 登录。", "For model-backed learning refresh, complete Codex login first."),
    )

    if os.environ.get("OPENAI_API_KEY"):
        append_doctor_check(
            checks,
            "openai_api_key_env",
            "warn",
            "OPENAI_API_KEY is set",
            localized(
                "如果遇到 401 / invalid_issuer，先临时 unset OPENAI_API_KEY，或换成有效的 OpenAI API key 后重试。",
                "If you hit 401 / invalid_issuer, temporarily unset OPENAI_API_KEY or replace it with a valid OpenAI API key before retrying.",
            ),
        )
    else:
        append_doctor_check(checks, "openai_api_key_env", "ok", "OPENAI_API_KEY is not set")

    codex_config_path = PATHS.codex_home / "config.toml"
    if codex_config_path.exists():
        try:
            config_text = codex_config_path.read_text(encoding="utf-8")
        except OSError as exc:
            append_doctor_check(
                checks,
                "codex_config_file",
                "warn",
                str(codex_config_path),
                localized("无法读取 config.toml：{}".format(exc), "Could not read config.toml: {}".format(exc)),
            )
        else:
            provider_match = re.search(r'(?m)^\s*model_provider\s*=\s*"([^"]+)"', config_text)
            provider_detail = "model_provider={}".format(provider_match.group(1)) if provider_match else "config.toml present"
            append_doctor_check(
                checks,
                "codex_config_file",
                "ok",
                provider_detail,
                localized(
                    "集体/代理配置需要 auth.json 和 config.toml 一起保留。",
                    "Shared/proxy providers need auth.json and config.toml to stay together.",
                ),
            )
    else:
        append_doctor_check(
            checks,
            "codex_config_file",
            "warn",
            str(codex_config_path),
            localized(
                "如果使用集体/代理配置，请确认 config.toml 中的 model_provider/base_url 没有丢失。",
                "If you use a shared/proxy provider, make sure config.toml still has the matching model_provider/base_url.",
            ),
        )

    latest_summary = load_review_summary_if_available(current_date_str())
    if summary_has_model_failure(latest_summary):
        append_doctor_check(
            checks,
            "latest_learning_run",
            "fail",
            summary_model_failure_hint(latest_summary),
            localized(
                "修复认证后重新运行 openrelix refresh --learn-memory --learn-window-days 7。",
                "After fixing authentication, rerun openrelix refresh --learn-memory --learn-window-days 7.",
            ),
        )
    else:
        append_doctor_check(checks, "latest_learning_run", "ok", localized("未发现今天的模型失败记录。", "No model failure recorded for today."))

    if args.model_check:
        selected_cli = get_model_cli(PATHS)
        selected_bin = PATHS.claude_bin if selected_cli == "claude" else PATHS.codex_bin
        if not command_exists(selected_bin):
            append_doctor_check(
                checks,
                "model_cli_check",
                "fail",
                str(selected_bin),
                localized("先修复当前 model_cli 对应的 CLI 路径，再运行 --model-check。", "Fix the CLI path for the current model_cli first, then rerun --model-check."),
            )
        else:
            try:
                result = run_doctor_model_check()
                output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
                if result.returncode == 0:
                    append_doctor_check(
                        checks,
                        "model_cli_check",
                        "ok",
                        doctor_model_check_detail(selected_cli, output),
                    )
                else:
                    append_doctor_check(
                        checks,
                        "model_cli_check",
                        "fail",
                        output[-600:] or "{} model check failed with exit code {}".format(selected_cli, result.returncode),
                        localized(
                            "修复当前 model_cli 的认证或桥接配置；Claude Code 可用会员/API，也可用 --claude-settings 或 --claude-env-file 指向第三方 provider 配置。",
                            "Fix auth or bridge settings for the current model_cli; Claude Code can use membership/API auth or third-party provider settings via --claude-settings or --claude-env-file.",
                        ),
                    )
            except subprocess.TimeoutExpired:
                append_doctor_check(
                    checks,
                    "model_cli_check",
                    "fail",
                    "{} model check timed out after 45 seconds".format(selected_cli),
                    localized("先确认当前 model_cli 在终端可运行。", "Confirm the current model_cli can run in a terminal."),
                )
    else:
        append_doctor_check(
            checks,
            "model_cli_check",
            "warn",
            localized("未执行模型认证检查。", "Model authentication check was not run."),
            localized("需要验证 401 / invalid_issuer 时运行 openrelix doctor --model-check。", "Run openrelix doctor --model-check to verify 401 / invalid_issuer issues."),
        )

    payload = {
        "ok": not any(check["status"] == "fail" for check in checks),
        "checks": checks,
    }
    if args.json:
        print_json(payload)
    else:
        print(localized("OpenRelix 体检", "OpenRelix Doctor"))
        for check in checks:
            print("[{}] {}: {}".format(check["status"], check["name"], check["detail"]))
            if check.get("action") and check["status"] != "ok":
                print("  {}".format(check["action"]))
    if not payload["ok"]:
        raise SystemExit(1)


def pipeline_command(
    date_str,
    stage,
    learn_window_days=0,
    defer_global_refresh=False,
    skip_learning_collect=False,
    reuse_lightweight=False,
    skip_if_unchanged=True,
):
    cmd = ["/bin/zsh", str(NIGHTLY_PIPELINE_SCRIPT), date_str, stage]
    if learn_window_days > 0:
        cmd.extend(["--learn-window-days", str(learn_window_days)])
    if defer_global_refresh:
        cmd.append("--defer-global-refresh")
    if skip_learning_collect:
        cmd.append("--skip-learning-collect")
    if reuse_lightweight:
        cmd.append("--reuse-lightweight")
    if skip_if_unchanged:
        cmd.append("--skip-if-unchanged")
    else:
        cmd.append("--no-skip-if-unchanged")
    return cmd


def ensure_learning_windows_final(date_strs, learn_window_days, verbose=True, defer_global_refresh=False, jobs=1):
    if learn_window_days <= 0:
        return []
    target_dates = list(date_strs)
    backfill_dates = resolve_learning_backfill_dates_for_targets(
        target_dates,
        learn_window_days,
        exclude_dates=target_dates,
    )
    if not backfill_dates:
        if verbose:
            print(
                localized(
                    "同步回溯: 近 {} 天 final 日报已齐，或没有可回溯窗口。".format(learn_window_days),
                    "Backfill sync: final daily reports for the last {} days are already complete, or no source windows are available.".format(learn_window_days),
                )
            )
        return []
    if verbose:
        print(
            localized(
                "同步回溯: 近 {} 天有 {} 天缺失或非 final，先按 final 生成；该阶段不递归扩展学习窗口。".format(
                    learn_window_days,
                    len(backfill_dates),
                ),
                "Backfill sync: {} daily reports are missing or non-final in the last {} days; generating them as final first without recursively expanding the learning window.".format(
                    len(backfill_dates),
                    learn_window_days,
                ),
            )
        )
        print("{}: {}".format(localized("日期", "Dates"), ", ".join(backfill_dates)))
    sync_kwargs = {
        "learn_window_days": 0,
        "force": False,
        "ensure_learning_final": False,
        "verbose": verbose,
        "jobs": jobs,
    }
    if defer_global_refresh:
        sync_kwargs["defer_global_refresh"] = True
    sync_results = run_backfill_dates(backfill_dates, "final", **sync_kwargs)
    if verbose:
        completed = sum(1 for item in sync_results if item["status"] == "completed")
        skipped = sum(1 for item in sync_results if item["status"] == "skipped_existing")
        failed = sum(1 for item in sync_results if item["status"] == "failed")
        print(
            localized(
                "同步回溯完成: 完成 {} 天 | 跳过 {} 天 | 失败 {} 天".format(completed, skipped, failed),
                "Backfill sync completed: completed {} | skipped {} | failed {}".format(
                    completed,
                    skipped,
                    failed,
                ),
            )
        )
        print("")
    return sync_results


def ensure_learning_windows_preliminary(date_strs, learn_window_days, verbose=True, defer_global_refresh=False, jobs=1):
    if learn_window_days <= 0:
        return []
    target_dates = list(date_strs)
    backfill_dates = resolve_learning_backfill_dates_for_targets(
        target_dates,
        learn_window_days,
        exclude_dates=target_dates,
        requested_stage="preliminary",
    )
    if not backfill_dates:
        if verbose:
            print(
                localized(
                    "轻量回溯: 近 {} 天 preliminary 日报已齐，或没有可回溯窗口。".format(learn_window_days),
                    "Lightweight backfill: preliminary daily reports for the last {} days are already complete, or no source windows are available.".format(learn_window_days),
                )
            )
        return []
    if verbose:
        print(
            localized(
                "轻量回溯: 近 {} 天有 {} 天缺失轻量日报，先按 preliminary 生成；深度 final 只作用于目标日期。".format(
                    learn_window_days,
                    len(backfill_dates),
                ),
                "Lightweight backfill: {} daily reports are missing preliminary coverage in the last {} days; generating preliminary first while final stays on the target date.".format(
                    len(backfill_dates),
                    learn_window_days,
                ),
            )
        )
        print("{}: {}".format(localized("日期", "Dates"), ", ".join(backfill_dates)))
    sync_kwargs = {
        "learn_window_days": 0,
        "force": False,
        "ensure_learning_final": False,
        "verbose": verbose,
        "jobs": jobs,
    }
    if defer_global_refresh:
        sync_kwargs["defer_global_refresh"] = True
    sync_results = run_backfill_dates(backfill_dates, "preliminary", **sync_kwargs)
    if verbose:
        completed = sum(1 for item in sync_results if item["status"] == "completed")
        skipped = sum(1 for item in sync_results if item["status"] == "skipped_existing")
        failed = sum(1 for item in sync_results if item["status"] == "failed")
        print(
            localized(
                "轻量回溯完成: 完成 {} 天 | 跳过 {} 天 | 失败 {} 天".format(completed, skipped, failed),
                "Lightweight backfill completed: completed {} | skipped {} | failed {}".format(
                    completed,
                    skipped,
                    failed,
                ),
            )
        )
        print("")
    return sync_results


def ensure_learning_window_final(date_str, learn_window_days, verbose=True, defer_global_refresh=False, jobs=1):
    return ensure_learning_windows_final(
        [date_str],
        learn_window_days,
        verbose=verbose,
        defer_global_refresh=defer_global_refresh,
        jobs=jobs,
    )


def precollect_learning_window_sources(date_strs, learn_window_days, verbose=True):
    collect_dates = learning_window_dates_for_targets(
        date_strs,
        learn_window_days,
        exclude_dates=set(date_strs),
    )
    if not collect_dates:
        return []
    if verbose:
        print(
            localized(
                "预采集学习窗口: {} 天历史窗口只采集一次。".format(len(collect_dates)),
                "Pre-collecting learning window: collecting {} historical dates once.".format(len(collect_dates)),
            )
        )
    for index, date_str in enumerate(collect_dates, start=1):
        if verbose:
            print(
                "[{}/{}] {} {}".format(
                    index,
                    len(collect_dates),
                    date_str,
                    localized(
                        "复用轻量层。" if has_reusable_lightweight_compact(date_str) else "采集历史窗口。",
                        "reusing lightweight layer." if has_reusable_lightweight_compact(date_str) else "collecting historical windows.",
                    ),
                )
            )
        if has_reusable_lightweight_compact(date_str):
            continue
        run_checked_quiet(
            [
                sys.executable,
                str(COLLECT_CODEX_ACTIVITY_SCRIPT),
                "--date",
                date_str,
                "--stage",
                "final",
            ]
        )
    return collect_dates


def raw_history_hydration_window_days():
    raw_value = os.environ.get(RAW_HISTORY_HYDRATION_DAYS_ENV, "")
    if not str(raw_value).strip():
        return RAW_HISTORY_HYDRATION_DEFAULT_DAYS
    try:
        return max(0, int(raw_value))
    except ValueError:
        return RAW_HISTORY_HYDRATION_DEFAULT_DAYS


def raw_daily_capture_stage(date_str):
    raw_daily_path = PATHS.raw_daily_dir / "{}.json".format(date_str)
    if not raw_daily_path.exists():
        return ""
    try:
        payload = load_json(raw_daily_path)
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("stage") or "")


def raw_daily_needs_final_hydration(date_str):
    stage = raw_daily_capture_stage(date_str)
    if not stage:
        return True
    return STAGE_PRIORITY.get(stage, 0) < STAGE_PRIORITY[RAW_HISTORY_HYDRATION_STAGE]


def raw_history_hydration_dates(target_dates, days=None):
    if not target_dates:
        return []
    window_days = raw_history_hydration_window_days() if days is None else max(0, int(days or 0))
    if window_days <= 0:
        return []
    end_date = max(parse_date_arg(date_str, "--date") for date_str in target_dates)
    start_date = end_date - timedelta(days=window_days - 1)
    collect_dates = []
    for offset in range(window_days):
        date_str = (start_date + timedelta(days=offset)).isoformat()
        if raw_daily_needs_final_hydration(date_str):
            collect_dates.append(date_str)
    return collect_dates


def hydrate_raw_history_windows(target_dates, days=None, verbose=True):
    collect_dates = raw_history_hydration_dates(target_dates, days=days)
    if not collect_dates:
        return []
    if verbose:
        print(
            localized(
                "历史窗口补采集: {} 天 raw 窗口只采集，不做模型总结。".format(len(collect_dates)),
                "Raw history hydration: collecting {} daily raw window files without model summarization.".format(
                    len(collect_dates)
                ),
            )
        )
    results = []
    for index, date_str in enumerate(collect_dates, start=1):
        if verbose:
            print(
                "[{}/{}] {} {}".format(
                    index,
                    len(collect_dates),
                    date_str,
                    localized("采集历史窗口。", "collecting raw windows."),
                )
            )
        try:
            run_checked_quiet(
                [
                    sys.executable,
                    str(COLLECT_CODEX_ACTIVITY_SCRIPT),
                    "--date",
                    date_str,
                    "--stage",
                    RAW_HISTORY_HYDRATION_STAGE,
                ]
            )
        except subprocess.CalledProcessError as exc:
            results.append(
                {
                    "date": date_str,
                    "status": "failed",
                    "stage": RAW_HISTORY_HYDRATION_STAGE,
                    "returncode": exc.returncode,
                }
            )
            continue
        results.append(
            {
                "date": date_str,
                "status": "completed",
                "stage": RAW_HISTORY_HYDRATION_STAGE,
            }
        )
    return results


def command_review(args):
    learning_sync_results = []
    if args.stage == "final" and args.learn_window_days > 0:
        learning_sync_results = ensure_learning_windows_preliminary(
            [args.date],
            args.learn_window_days,
            verbose=not args.json,
            defer_global_refresh=True,
            jobs=args.jobs,
        )
        precollect_learning_window_sources(
            [args.date],
            args.learn_window_days,
            verbose=not args.json,
        )
    elif args.learn_window_days > 0:
        learning_sync_results = ensure_learning_window_final(
            args.date,
            args.learn_window_days,
            verbose=not args.json,
            defer_global_refresh=True,
            jobs=args.jobs,
        )

    cmd = pipeline_command(
        args.date,
        args.stage,
        args.learn_window_days,
        defer_global_refresh=True,
        skip_learning_collect=args.stage == "final" and args.learn_window_days > 0,
        reuse_lightweight=args.stage == "final" and has_reusable_lightweight_compact(args.date),
        skip_if_unchanged=True,
    )
    pipeline_error = None
    if args.json:
        try:
            run_checked_with_progress(cmd, [])
        except subprocess.CalledProcessError as exc:
            pipeline_error = exc
    else:
        print(localized("复盘开始", "Review started"))
        print("{}: {}".format(localized("日期", "Date"), args.date))
        print("{}: {}".format(localized("阶段", "Stage"), args.stage))
        print(localized(
            "采集中: 读取目标日期的 Codex 窗口。",
            "Collecting: reading Codex windows for the target date.",
        ))
        if args.learn_window_days > 0:
            print(
                localized(
                    "窗口学习: 将补采并全量读取近 {} 天历史窗口，按批次压缩；命令行只输出进度和汇总。".format(
                        args.learn_window_days
                    ),
                    "Window learning: backfilling and reading the last {} days of historical windows, then compressing by batch; the CLI prints only progress and summaries.".format(
                        args.learn_window_days
                    ),
                )
            )
        print(localized(
            "整理中: 生成结构化摘要，历史窗口明细不会直接打印。",
            "Organizing: generating a structured summary; historical window details will not be printed directly.",
        ))
        try:
            run_checked_with_progress(
                cmd,
                [
                    localized(
                        "仍在整理: 正在归纳目标日期窗口和历史批次学习结果。",
                        "Still organizing: summarizing target-date windows and historical batch learning.",
                    ),
                    localized(
                        "仍在整理: 正在写入 review、记忆摘要和面板数据。",
                        "Still organizing: writing review, memory summary, and panel data.",
                    ),
                    localized(
                        "仍在整理: 子流程还在运行，继续等待。",
                        "Still organizing: subprocess is still running; waiting.",
                    ),
                ],
            )
        except subprocess.CalledProcessError as exc:
            pipeline_error = exc
    if not args.json:
        print(localized("刷新中: 同步 host context 摘要和面板。", "Refreshing: syncing host context summary and panel."))
    run_task_summary_for_dates_warning_only(
        resolve_task_summary_dates(
            window_days=max(args.learn_window_days or 1, 1),
            end_date=args.date,
        ),
        verbose=not args.json,
    )
    sync_review_outputs(include_index=True, include_native_display=True, verbose=not args.json)
    if not args.json:
        print(localized("生成完成: 读取摘要。", "Generation complete: reading summary."))
    summary_json_path, summary_md_path = review_summary_paths(args.date)
    if not summary_json_path.exists():
        if pipeline_error is not None:
            raise pipeline_error
        raise SystemExit(localized(
            "missing review summary: {}".format(summary_json_path),
            "missing review summary: {}".format(summary_json_path),
        ))

    summary = load_json(summary_json_path)
    model_failed = summary_has_model_failure(summary)
    learning_failed_results = [item for item in learning_sync_results if item["status"] == "failed"]
    exit_code = pipeline_error.returncode if pipeline_error is not None else 0
    failure_exit_code = exit_code or failed_result_exit_code(learning_failed_results) or (1 if model_failed else 0)
    if args.json:
        print_json(summary)
        if failure_exit_code:
            raise SystemExit(failure_exit_code)
    else:
        print(localized("复盘已完成", "Review completed"))
        print("{}: {}".format(localized("日期", "Date"), summary.get("date", args.date)))
        print("{}: {}".format(localized("阶段", "Stage"), summary.get("stage", args.stage)))
        learning_line = format_learning_digest(summary, args.learn_window_days)
        if learning_line:
            print(learning_line)
        print("{}: {}".format(localized("摘要", "Summary"), summary.get("day_summary", "—")))
        print(
            localized(
                "窗口: {} | 长期记忆: {} | 工作记忆: {} | 低优先记忆: {}".format(
                    summary.get("raw_window_count", len(summary.get("window_summaries", []))),
                    len(summary.get("durable_memories", [])),
                    len(summary.get("session_memories", [])),
                    len(summary.get("low_priority_memories", [])),
                ),
                "Windows: {} | Long-term: {} | Work: {} | Low-priority: {}".format(
                    summary.get("raw_window_count", len(summary.get("window_summaries", []))),
                    len(summary.get("durable_memories", [])),
                    len(summary.get("session_memories", [])),
                    len(summary.get("low_priority_memories", [])),
                ),
            )
        )
        print("")
        print(localized("输出", "Outputs"))
        print("- review: {}".format(summary_md_path))
        print("- panel: {}".format(REPORTS_DIR / "panel.html"))
        print("- overview: {}".format(REPORTS_DIR / "overview.md"))
        if args.stage == "preliminary" and not failure_exit_code:
            print_preliminary_ready_message()
        if model_failed:
            print("")
            print_model_failure_warning(summary, args.date)
        if learning_failed_results:
            print("")
            print(localized("历史回溯失败日期", "Failed historical backfill dates"))
            for item in learning_failed_results[:5]:
                print("- {} (exit {})".format(item["date"], item.get("returncode", 1)))

    if args.open:
        open_path(summary_md_path)
    if failure_exit_code:
        raise SystemExit(failure_exit_code)


def run_backfill_dates(
    dates,
    stage,
    learn_window_days=0,
    force=False,
    ensure_learning_final=True,
    defer_global_refresh=False,
    verbose=True,
    jobs=1,
):
    target_dates = list(dates)
    total_dates = len(target_dates)
    dependency_failures = []
    runnable_dates = [
        date_str
        for date_str in target_dates
        if review_summary_needs_run(date_str, stage, force=force)[0]
    ]
    batch_learning_ready = False
    skip_learning_collect = False

    if stage == "final" and target_dates and runnable_dates and ensure_learning_final and learn_window_days > 0:
        learning_sync_results = ensure_learning_windows_preliminary(
            target_dates,
            learn_window_days,
            verbose=verbose,
            defer_global_refresh=True,
            jobs=jobs,
        )
        dependency_failures.extend(
            {
                **item,
                "dependency": "learning_window_preliminary",
            }
            for item in learning_sync_results
            if item["status"] == "failed"
        )
    elif stage != "final" and target_dates and runnable_dates and ensure_learning_final and learn_window_days > 0:
        learning_sync_results = ensure_learning_windows_final(
            target_dates,
            learn_window_days,
            verbose=verbose,
            defer_global_refresh=True,
            jobs=jobs,
        )
        dependency_failures.extend(
            {
                **item,
                "dependency": "learning_window",
            }
            for item in learning_sync_results
            if item["status"] == "failed"
        )
        batch_learning_ready = True

    if stage == "final" and runnable_dates and learn_window_days > 0:
        precollect_learning_window_sources(runnable_dates, learn_window_days, verbose=verbose)
        skip_learning_collect = True

    parallel_jobs = effective_backfill_jobs(stage, jobs)
    can_parallelize = (
        parallel_jobs > 1
        and learn_window_days <= 0
        and defer_global_refresh
        and len(runnable_dates) > 1
    )
    indexed_results = [None for _ in target_dates]
    work_items = []

    for index, date_str in enumerate(target_dates, start=1):
        if stage != "final" and ensure_learning_final and learn_window_days > 0 and not batch_learning_ready:
            learning_sync_results = ensure_learning_window_final(
                date_str,
                learn_window_days,
                verbose=verbose,
                jobs=jobs,
            )
            dependency_failures.extend(
                {
                    **item,
                    "dependency": "learning_window",
                }
                for item in learning_sync_results
                if item["status"] == "failed"
            )

        summary_json_path, summary_md_path = review_summary_paths(date_str)
        needs_run, skip_reason, summary_info = review_summary_needs_run(date_str, stage, force=force)
        if not needs_run:
            indexed_results[index - 1] = {
                "date": date_str,
                "status": "skipped_existing",
                "reason": skip_reason,
                "existing_stage": summary_info.get("stage", ""),
                "requested_stage": stage,
                "summary_json": str(summary_json_path),
                "summary_md": str(summary_md_path),
            }
            if verbose:
                print(
                    "[{}/{}] {} {}".format(
                        index,
                        total_dates,
                        date_str,
                        localized(
                            "已有 {} summary，跳过。".format(summary_info.get("stage") or stage),
                            "existing {} summary; skipped.".format(summary_info.get("stage") or stage),
                        ),
                    )
                )
            continue

        cmd = pipeline_command(
            date_str,
            stage,
            learn_window_days,
            defer_global_refresh=defer_global_refresh,
            skip_learning_collect=skip_learning_collect,
            reuse_lightweight=stage == "final" and has_reusable_lightweight_compact(date_str),
            skip_if_unchanged=not force,
        )
        work_items.append(
            {
                "index": index,
                "date": date_str,
                "cmd": cmd,
                "reason": skip_reason,
                "summary_json": str(summary_json_path),
                "summary_md": str(summary_md_path),
            }
        )

    if can_parallelize and verbose and work_items:
        print(
            localized(
                "并发回溯: jobs={}，每个日期独立生成 {}，汇总刷新会在最后串行执行。".format(parallel_jobs, stage),
                "Parallel backfill: jobs={}; each date generates its {} summary independently, and global refresh runs serially at the end.".format(parallel_jobs, stage),
            )
        )

    def run_work_item(item):
        date_str = item["date"]
        index = item["index"]
        if verbose:
            print("[{}/{}] {} {}".format(index, total_dates, date_str, localized("开始回溯。", "started.")), flush=True)
        pipeline_error = None
        try:
            run_checked_with_progress(
                item["cmd"],
                [] if not verbose else [
                    localized(
                        "{} 仍在整理: 正在归纳窗口和历史批次学习结果。".format(date_str),
                        "{} still organizing: summarizing windows and historical batch learning.".format(date_str),
                    ),
                    localized(
                        "{} 仍在整理: 正在写入 summary、记忆和面板数据。".format(date_str),
                        "{} still organizing: writing summary, memories, and panel data.".format(date_str),
                    ),
                ],
            )
        except subprocess.CalledProcessError as exc:
            pipeline_error = exc
        if pipeline_error is not None:
            result = {
                "date": date_str,
                "status": "failed",
                "reason": item["reason"],
                "requested_stage": stage,
                "summary_json": item["summary_json"],
                "summary_md": item["summary_md"],
                "returncode": pipeline_error.returncode,
            }
            if verbose:
                print("[{}/{}] {} {}".format(index, total_dates, date_str, localized("失败。", "failed.")), flush=True)
            return index - 1, result
        result = {
            "date": date_str,
            "status": "completed",
            "reason": item["reason"],
            "requested_stage": stage,
            "summary_json": item["summary_json"],
            "summary_md": item["summary_md"],
        }
        if verbose:
            print("[{}/{}] {} {}".format(index, total_dates, date_str, localized("完成。", "completed.")), flush=True)
        return index - 1, result

    if can_parallelize:
        with ThreadPoolExecutor(max_workers=parallel_jobs) as executor:
            future_map = {executor.submit(run_work_item, item): item for item in work_items}
            try:
                for future in as_completed(future_map):
                    result_index, result = future.result()
                    indexed_results[result_index] = result
            except KeyboardInterrupt:
                stop_active_child_processes()
                for future in future_map:
                    future.cancel()
                raise
    else:
        try:
            for item in work_items:
                result_index, result = run_work_item(item)
                indexed_results[result_index] = result
        except KeyboardInterrupt:
            stop_active_child_processes()
            raise

    return dependency_failures + [item for item in indexed_results if item is not None]


def command_backfill(args):
    dates = resolve_backfill_dates(args)
    if not args.json:
        print(localized("回溯开始", "Backfill started"))
        print("{}: {} -> {}".format(localized("日期范围", "Date range"), dates[0], dates[-1]))
        print("{}: {}".format(localized("阶段", "Stage"), args.stage))
        effective_jobs = effective_backfill_jobs(args.stage, args.jobs)
        if effective_jobs > 1:
            print("{}: {}".format(localized("并发", "Jobs"), effective_jobs))
        elif args.stage == "final" and normalize_backfill_jobs(args.jobs) > 1:
            print(localized("深度 final 回溯将按串行执行。", "Final deep backfill will run serially."))
        if args.learn_window_days > 0:
            print("{}: {} days".format(localized("窗口学习", "Window learning"), args.learn_window_days))

    results = run_backfill_dates(
        dates,
        args.stage,
        learn_window_days=args.learn_window_days,
        force=args.force,
        ensure_learning_final=True,
        defer_global_refresh=True,
        verbose=not args.json,
        jobs=args.jobs,
    )
    completed = sum(1 for item in results if item["status"] == "completed")
    failed_results = [item for item in results if item["status"] == "failed"]
    raw_history_results = []
    if args.stage == "final" and not failed_results:
        raw_history_results = hydrate_raw_history_windows(dates, verbose=not args.json)
    raw_history_completed = any(item["status"] == "completed" for item in raw_history_results)
    raw_history_failed_results = [item for item in raw_history_results if item["status"] == "failed"]
    if completed or failed_results or raw_history_completed:
        if not args.json:
            print(
                localized(
                    "刷新中: 汇总更新索引、host context 摘要和面板；这一步可能需要几分钟。",
                    "Refreshing: updating index, host context summary, and panel once; this may take a few minutes.",
                )
            )
        run_task_summary_for_dates_warning_only(dates, verbose=not args.json)
        sync_review_outputs(include_index=True, include_native_display=True, verbose=not args.json)

    if args.json:
        print_json({"dates": results, "raw_history_hydration": raw_history_results})
        if failed_results:
            raise SystemExit(failed_result_exit_code(failed_results))
        return

    skipped = sum(1 for item in results if item["status"] == "skipped_existing")
    failed = len(failed_results)
    print("")
    print(localized("回溯完成", "Backfill completed"))
    print(
        "{}: {} | {}: {} | {}: {}".format(
            localized("完成", "Completed"),
            completed,
            localized("跳过", "Skipped"),
            skipped,
            localized("失败", "Failed"),
            failed,
        )
    )
    print("- panel: {}".format(REPORTS_DIR / "panel.html"))
    print("- overview: {}".format(REPORTS_DIR / "overview.md"))
    if args.stage == "preliminary" and completed and not failed_results:
        print_preliminary_ready_message()
    if failed_results:
        print("")
        print(localized("失败日期", "Failed dates"))
        for item in failed_results[:5]:
            print("- {} (exit {})".format(item["date"], item.get("returncode", 1)))
        raise SystemExit(failed_result_exit_code(failed_results))
    if raw_history_results:
        print("")
        print(
            localized(
                "历史窗口补采集: 完成 {} | 失败 {}".format(
                    sum(1 for item in raw_history_results if item["status"] == "completed"),
                    len(raw_history_failed_results),
                ),
                "Raw history hydration: completed {} | failed {}".format(
                    sum(1 for item in raw_history_results if item["status"] == "completed"),
                    len(raw_history_failed_results),
                ),
            )
        )
    if raw_history_failed_results:
        print("")
        print(localized("历史窗口补采集失败日期", "Failed raw history hydration dates"))
        for item in raw_history_failed_results[:5]:
            print("- {} (exit {})".format(item["date"], item.get("returncode", 1)))


def command_core(args):
    data = load_overview()
    if args.json:
        payload = {
            "generated_at": data.get("generated_at"),
            "summary": data.get("summary"),
            "metrics": data.get("metrics"),
            "token_usage": data.get("token_usage"),
            "nightly": data.get("nightly"),
        }
        print_json(payload)
        return
    print_core_summary(data)


def command_refresh(args):
    cmd = ["/bin/zsh", str(REFRESH_SCRIPT)]
    if args.learn_memory:
        cmd.extend(["--learn-memory", "--date", args.date, "--stage", args.stage])
        if args.learn_window_days > 0:
            cmd.extend(["--learn-window-days", str(args.learn_window_days)])
    run_checked(cmd)
    data = load_overview()
    learn_summary = load_review_summary_if_available(args.date) if args.learn_memory else None
    model_failed = summary_has_model_failure(learn_summary)
    if args.json:
        payload = {
            "generated_at": data.get("generated_at"),
            "summary": data.get("summary"),
            "metrics": data.get("metrics"),
            "token_usage": data.get("token_usage"),
            "nightly": data.get("nightly"),
            "learn_memory": bool(args.learn_memory),
        }
        if args.learn_memory:
            payload["learn_memory_status"] = "model_failed" if model_failed else "completed"
            if model_failed:
                payload["learn_memory_error_hint"] = summary_model_failure_hint(learn_summary)
        print_json(payload)
        if model_failed:
            raise SystemExit(1)
        return
    if args.learn_memory:
        if model_failed:
            print_model_failure_warning(learn_summary, args.date)
            print(localized("概览已刷新，但记忆提炼未完整完成", "Overview refreshed, but memory synthesis did not fully complete"))
        else:
            print(localized("记忆已提炼并刷新概览", "Memory synthesized and overview refreshed"))
    else:
        print(localized("概览已刷新", "Overview refreshed"))
    print("")
    print_core_summary(data)
    if model_failed:
        raise SystemExit(1)


def prompt_confirm_update(command_text):
    if not sys.stdin.isatty():
        return False
    answer = input(localized("执行更新命令？[y/N] ", "Run the update command? [y/N] ")).strip().lower()
    return answer in {"y", "yes"}


def command_update(args):
    local_version = read_local_package_version()
    latest_version = ""
    update_error = ""
    try:
        latest_version = fetch_latest_npm_version()
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        update_error = str(exc)

    comparison = compare_versions(local_version, latest_version) if latest_version else 0
    update_available = bool(latest_version and comparison < 0)
    status = "unknown"
    if latest_version:
        status = "update_available" if update_available else "up_to_date"
    npx_bin = resolve_cli_tool("npx")
    package_spec = "{}@{}".format(NPM_PACKAGE_NAME, latest_version) if latest_version else NPM_LATEST_SPEC
    command = build_update_install_command(recommended=args.recommended, npx_bin=npx_bin, package_spec=package_spec)
    command_text = shlex.join(command)
    payload = {
        "package": NPM_PACKAGE_NAME,
        "current_version": local_version,
        "latest_version": latest_version,
        "status": status,
        "update_available": update_available,
        "error": update_error,
        "command": command_text,
    }

    if args.json:
        print_json(payload)
    else:
        print(localized("OpenRelix 更新检查", "OpenRelix Update Check"))
        print("- {}: {}".format(localized("当前版本", "Current version"), local_version or "unknown"))
        print("- {}: {}".format(localized("最新版本", "Latest version"), latest_version or "unknown"))
        if update_error:
            print("- {}: {}".format(localized("检查失败", "Check failed"), update_error))
        elif update_available:
            print("- {}".format(localized("发现可用更新。", "An update is available.")))
        else:
            print("- {}".format(localized("当前已是最新版本。", "Already up to date.")))
        print("- {}: {}".format(localized("更新命令", "Update command"), command_text))

    if args.check or args.print_command:
        return

    if not latest_version and not args.force:
        if not args.json:
            print(localized(
                "未能确认 npm 最新版本；如需强制重装，请加 --force --yes。",
                "Could not confirm the latest npm version; add --force --yes to reinstall anyway.",
            ))
        return

    if latest_version and not update_available and not args.force:
        return

    if not npx_bin:
        raise SystemExit(localized(
            "未找到 npx；请先安装 Node.js/npm，或手动运行上面的更新命令。",
            "npx was not found; install Node.js/npm first, or run the update command above manually.",
        ))

    if not args.yes and not prompt_confirm_update(command_text):
        if not args.json:
            print(localized(
                "已取消。需要无人值守更新时使用 openrelix update --yes。",
                "Cancelled. Use openrelix update --yes for unattended updates.",
            ))
        return

    installability_error = check_npm_package_version_installable(NPM_PACKAGE_NAME, latest_version) if latest_version else ""
    if installability_error:
        raise SystemExit("{}\n{}".format(
            localized(
                "当前 npm registry 暂时还不能安装 {}@{}；可能是镜像同步延迟。请稍后重试，或临时切到官方 npm registry 后再运行更新。".format(
                    NPM_PACKAGE_NAME,
                    latest_version,
                ),
                "The current npm registry cannot install {}@{} yet; it may still be syncing. Retry later, or temporarily switch to the official npm registry before updating.".format(
                    NPM_PACKAGE_NAME,
                    latest_version,
                ),
            ),
            installability_error,
        ))

    try:
        subprocess.run(command, cwd=str(update_install_cwd()), env=cli_tool_env(), check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(localized(
            "自动重装未完成，退出码 {}。上面的更新命令可手动重试。".format(exc.returncode),
            "Automatic reinstall did not finish, exit code {}. Retry the update command above manually.".format(exc.returncode),
        ))


def resolve_memory_migration_dates(window_days, end_date=None):
    days = max(int(window_days or 0), 1)
    end = parse_date_arg(end_date or current_date_str(), "--to")
    start = end - timedelta(days=days - 1)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(days)]


def print_memory_migration_state(state):
    print(localized("个人记忆迁移状态", "Personal memory migration status"))
    print("- status: {}".format(state.get("status") or "unknown"))
    print("- algorithm_version: {}".format(state.get("algorithm_version") or PERSONAL_MEMORY_ALGORITHM_VERSION))
    print("- window_days: {}".format(state.get("window_days") or PERSONAL_MEMORY_MIGRATION_WINDOW_DAYS))
    if state.get("reason"):
        print("- reason: {}".format(state.get("reason")))
    if state.get("previous_algorithm_version") is not None:
        print("- previous_algorithm_version: {}".format(state.get("previous_algorithm_version")))
    if state.get("dates"):
        print("- dates: {}".format(", ".join(state.get("dates") or [])))
    registry_migration = state.get("registry_migration") or {}
    if isinstance(registry_migration, dict) and registry_migration:
        print("- registry_migrated_rows: {}".format(registry_migration.get("migrated_rows", 0)))
        print("- registry_dropped_lightweight_rows: {}".format(registry_migration.get("dropped_lightweight_rows", 0)))
    print("- state_file: {}".format(PATHS.runtime_dir / "memory-migration.json"))


def command_memory_migration(args):
    window_days = max(int(args.window_days or PERSONAL_MEMORY_MIGRATION_WINDOW_DAYS), 1)

    if args.action == "status":
        state = load_memory_migration_state(PATHS)
        if not state:
            state = ensure_memory_migration_state(PATHS, window_days=window_days, force=False)
        if args.json:
            print_json(state)
        else:
            print_memory_migration_state(state)
        return

    if args.action == "ensure":
        state = ensure_memory_migration_state(PATHS, window_days=window_days, force=args.force)
        if args.json:
            print_json(state)
        elif not args.quiet:
            print_memory_migration_state(state)
        return

    if args.action == "complete":
        dates = resolve_memory_migration_dates(window_days)
        registry_migration = migrate_personal_memory_registry(PATHS)
        state = mark_memory_migration_completed(
            PATHS,
            dates,
            window_days=window_days,
            registry_migration=registry_migration,
        )
        if args.json:
            print_json(state)
        elif not args.quiet:
            print_memory_migration_state(state)
        return

    state = ensure_memory_migration_state(PATHS, window_days=window_days, force=args.force)
    if args.if_pending and state.get("status") != "pending":
        if args.json:
            print_json(state)
        elif not args.quiet:
            print_memory_migration_state(state)
        return
    if state.get("status") != "pending" and not args.force:
        if args.json:
            print_json(state)
        elif not args.quiet:
            print_memory_migration_state(state)
        return

    dates = resolve_memory_migration_dates(window_days)
    registry_migration = migrate_personal_memory_registry(PATHS)
    mark_memory_migration_running(
        PATHS,
        dates,
        window_days=window_days,
        registry_migration=registry_migration,
    )
    if not args.quiet:
        print(localized("个人记忆迁移开始", "Personal memory migration started"))
        print("- algorithm_version: {}".format(PERSONAL_MEMORY_ALGORITHM_VERSION))
        print("- stage: {}".format(PERSONAL_MEMORY_MIGRATION_STAGE))
        print("- dates: {}".format(", ".join(dates)))
        print("- skip_if_unchanged: false")
        print("- registry_migrated_rows: {}".format(registry_migration.get("migrated_rows", 0)))
        print("- registry_dropped_lightweight_rows: {}".format(registry_migration.get("dropped_lightweight_rows", 0)))
    try:
        results = run_backfill_dates(
            dates,
            PERSONAL_MEMORY_MIGRATION_STAGE,
            learn_window_days=window_days,
            force=True,
            ensure_learning_final=True,
            defer_global_refresh=True,
            verbose=not args.quiet and not args.json,
            jobs=1,
        )
        failed_results = [item for item in results if item.get("status") == "failed"]
        if failed_results:
            raise subprocess.CalledProcessError(failed_result_exit_code(failed_results), "memory-migration")
        sync_review_outputs(include_index=True, include_native_display=True, verbose=not args.quiet and not args.json)
    except Exception as exc:
        failed_state = mark_memory_migration_failed(PATHS, dates, exc, window_days=window_days)
        if args.json:
            print_json(failed_state)
        elif not args.quiet:
            print_memory_migration_state(failed_state)
        raise SystemExit(getattr(exc, "returncode", 1) or 1) from exc

    completed_state = mark_memory_migration_completed(
        PATHS,
        dates,
        window_days=window_days,
        registry_migration=registry_migration,
    )
    if args.json:
        print_json(completed_state)
    elif not args.quiet:
        print_memory_migration_state(completed_state)


def command_task_summary_migration(args):
    window_days = max(int(args.window_days or TASK_SUMMARY_WINDOW_DAYS), 1)

    if args.action == "status":
        state = load_task_summary_migration_state(PATHS)
        if not state:
            state = ensure_task_summary_migration_state(PATHS, window_days=window_days, force=False)
        if args.json:
            print_json(state)
        else:
            print_task_summary_migration_state(state)
        return

    if args.action == "ensure":
        state = ensure_task_summary_migration_state(PATHS, window_days=window_days, force=args.force)
        if args.json:
            print_json(state)
        elif not args.quiet:
            print_task_summary_migration_state(state)
        return

    state = ensure_task_summary_migration_state(PATHS, window_days=window_days, force=args.force)
    if args.if_pending and state.get("status") != "pending":
        if args.json:
            print_json(state)
        elif not args.quiet:
            print_task_summary_migration_state(state)
        return
    if state.get("status") != "pending" and not args.force:
        if args.json:
            print_json(state)
        elif not args.quiet:
            print_task_summary_migration_state(state)
        return

    dates = resolve_task_summary_dates(window_days=window_days)
    if not args.quiet:
        print(localized("并行任务总结迁移开始", "Parallel task summary migration started"))
        print("- task_cluster_algorithm_version: {}".format(TASK_CLUSTER_ALGORITHM_VERSION))
        print("- dates: {}".format(", ".join(dates)))
    try:
        result = run_task_summary_for_dates(dates, paths=PATHS, force=True)
        state = mark_task_summary_migration_completed(
            PATHS,
            dates=dates,
            window_days=window_days,
            result=result,
        )
    except Exception as exc:
        failed_state = mark_task_summary_migration_failed(
            PATHS,
            dates=dates,
            error=str(exc),
            window_days=window_days,
        )
        if args.json:
            print_json(failed_state)
        elif not args.quiet:
            print_task_summary_migration_state(failed_state)
        raise SystemExit(getattr(exc, "returncode", 1) or 1) from exc
    if args.json:
        print_json(state)
    elif not args.quiet:
        print_task_summary_migration_state(state)


def memory_mode_label(memory_mode):
    labels = {
        "integrated": localized(
            "全开：本地记忆 + host 上下文轻量摘要",
            "Full: local memory + lightweight host-context summary",
        ),
        "local-only": localized(
            "本地存储：只写本地，不注入 host 上下文",
            "Local storage: write locally without host-context injection",
        ),
        "off": localized(
            "禁用：只做资产可视化，不写个人记忆",
            "Disabled: asset visualization only, no personal memory writes",
        ),
    }
    return labels.get(memory_mode, memory_mode)


def codex_config_args_for_memory_mode(memory_mode):
    if memory_mode == "integrated":
        return ["--enable-memories", "--enable-history", "--history-max-bytes", "268435456"]
    if memory_mode == "local-only":
        return ["--disable-codex-memories", "--enable-history", "--history-max-bytes", "268435456"]
    return []


def configure_codex_for_memory_mode(memory_mode):
    config_args = codex_config_args_for_memory_mode(memory_mode)
    if not config_args:
        return False
    run_checked(
        [
            sys.executable,
            str(CONFIGURE_CODEX_USER_SCRIPT),
            "--config",
            str(PATHS.codex_home / "config.toml"),
            *config_args,
        ]
    )
    return True


def command_mode(args):
    requested_mode = args.memory_mode
    if not requested_mode:
        if args.json:
            print_json(
                {
                    "memory_mode": MEMORY_MODE,
                    "label": memory_mode_label(MEMORY_MODE),
                    "config_path": str(PATHS.runtime_dir / "config.json"),
                }
            )
            return
        print(localized("当前 OpenRelix 记忆模式", "Current OpenRelix memory mode"))
        print("- memory_mode: {}".format(MEMORY_MODE))
        print("- {}".format(memory_mode_label(MEMORY_MODE)))
        return

    try:
        normalized_mode = normalize_memory_mode(requested_mode, strict=True)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    config = write_runtime_config(memory_mode=normalized_mode, paths=PATHS)
    codex_config_updated = configure_codex_for_memory_mode(normalized_mode)
    if not args.no_refresh:
        run_checked(["/bin/zsh", str(REFRESH_SCRIPT)])

    if args.json:
        print_json(
            {
                "memory_mode": config.get("memory_mode"),
                "personal_memory_enabled": config.get("personal_memory_enabled"),
                "codex_context_enabled": config.get("codex_context_enabled"),
                "codex_config_updated": codex_config_updated,
                "refreshed": not args.no_refresh,
                "config_path": str(PATHS.runtime_dir / "config.json"),
            }
        )
        return

    print(localized("OpenRelix 记忆模式已更新", "OpenRelix memory mode updated"))
    print("- memory_mode: {}".format(config.get("memory_mode")))
    print("- {}".format(memory_mode_label(config.get("memory_mode"))))
    print("- config: {}".format(PATHS.runtime_dir / "config.json"))
    if codex_config_updated:
        print("- codex_config: {}".format(PATHS.codex_home / "config.toml"))
    if args.no_refresh:
        print(localized("- 未刷新 overview；需要时运行 openrelix refresh。", "- Overview not refreshed; run openrelix refresh when needed."))
    else:
        print(localized("- overview 和面板已刷新。", "- Overview and panel refreshed."))


def parse_schedule_time(value, option_name):
    text = str(value or "").strip()
    if not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", text):
        raise SystemExit(
            localized(
                "{} 必须使用 24 小时 HH:MM 格式，例如 23:00 或 00:10。".format(option_name),
                "{} must use 24-hour HH:MM format, for example 23:00 or 00:10.".format(option_name),
            )
        )
    hour, minute = text.split(":", 1)
    return int(hour), int(minute), text


def load_plist_file(path):
    try:
        with path.open("rb") as handle:
            payload = plistlib.load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(
            localized(
                "未找到 LaunchAgent：{}。请先确认后台服务已安装。".format(path),
                "LaunchAgent not found: {}. Confirm background services are installed first.".format(path),
            )
        ) from exc
    except (OSError, plistlib.InvalidFileException) as exc:
        raise SystemExit(
            localized(
                "无法读取 LaunchAgent：{} ({})".format(path, exc),
                "Could not read LaunchAgent: {} ({})".format(path, exc),
            )
        ) from exc
    return payload


def write_plist_file(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".plist", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            plistlib.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def plist_calendar_time_from_payload(payload):
    value = payload.get("StartCalendarInterval")
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, dict):
        return ""
    hour = value.get("Hour")
    minute = value.get("Minute")
    if hour is None or minute is None:
        return ""
    return "{:02d}:{:02d}".format(int(hour), int(minute))


def launch_agent_status(label, plist_name, kind):
    path = launch_agent_path(plist_name)
    row = {
        "label": label,
        "path": str(path),
        "installed": path.exists(),
        "kind": kind,
    }
    if not path.exists():
        return row
    payload = load_plist_file(path)
    if kind == "interval":
        seconds = int(payload.get("StartInterval") or 0)
        row["interval_seconds"] = seconds
        row["interval_minutes"] = max(1, int(round(seconds / 60))) if seconds > 0 else 0
    else:
        row["time"] = plist_calendar_time_from_payload(payload)
    return row


def schedule_payload():
    return {
        "overview_refresh": launch_agent_status(OVERVIEW_REFRESH_LABEL, OVERVIEW_REFRESH_PLIST_NAME, "interval"),
        "nightly_organize": launch_agent_status(NIGHTLY_ORGANIZE_LABEL, NIGHTLY_ORGANIZE_PLIST_NAME, "calendar"),
        "nightly_finalize": launch_agent_status(NIGHTLY_FINALIZE_LABEL, NIGHTLY_FINALIZE_PLIST_NAME, "calendar"),
        "update_check": launch_agent_status(UPDATE_CHECK_LABEL, UPDATE_CHECK_PLIST_NAME, "calendar"),
    }


def set_plist_interval_minutes(plist_name, minutes):
    if minutes is None:
        return None
    if minutes < 1:
        raise SystemExit(
            localized(
                "--overview-refresh-interval-minutes 必须大于等于 1。",
                "--overview-refresh-interval-minutes must be at least 1.",
            )
        )
    path = launch_agent_path(plist_name)
    payload = load_plist_file(path)
    payload["StartInterval"] = int(minutes) * 60
    write_plist_file(path, payload)
    return path


def set_plist_calendar_time(plist_name, hour, minute):
    path = launch_agent_path(plist_name)
    payload = load_plist_file(path)
    payload["StartCalendarInterval"] = {"Hour": int(hour), "Minute": int(minute)}
    write_plist_file(path, payload)
    return path


def reload_launch_agent(label, plist_path):
    if sys.platform != "darwin" or not shutil.which("launchctl"):
        return False
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", "gui/{}/{}".format(uid, label)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["launchctl", "bootout", "gui/{}".format(uid), str(plist_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(["launchctl", "bootstrap", "gui/{}".format(uid), str(plist_path)], check=True)
    return True


def print_schedule_payload(payload, updated=None, reloaded=None):
    updated = set(updated or [])
    reloaded = set(reloaded or [])
    print(localized("OpenRelix 后台任务时间", "OpenRelix background schedule"))
    overview = payload.get("overview_refresh") or {}
    nightly = payload.get("nightly_organize") or {}
    finalize = payload.get("nightly_finalize") or {}
    update_check = payload.get("update_check") or {}
    print(
        "- overview-refresh: {}".format(
            localized(
                "每 {} 分钟".format(overview.get("interval_minutes") or "unknown"),
                "every {} minutes".format(overview.get("interval_minutes") or "unknown"),
            )
            if overview.get("installed")
            else localized("未安装", "not installed")
        )
    )
    print("- nightly preview: {}".format(nightly.get("time") if nightly.get("installed") else localized("未安装", "not installed")))
    print("- nightly finalize: {}".format(finalize.get("time") if finalize.get("installed") else localized("未安装", "not installed")))
    print("- update check: {}".format(update_check.get("time") if update_check.get("installed") else localized("未安装", "not installed")))
    if updated:
        print("- {}: {}".format(localized("已更新", "Updated"), ", ".join(sorted(updated))))
    if reloaded:
        print("- {}: {}".format(localized("已重新加载", "Reloaded"), ", ".join(sorted(reloaded))))


def command_schedule(args):
    updated = []
    reloaded = []

    if args.overview_refresh_interval_minutes is not None:
        path = set_plist_interval_minutes(
            OVERVIEW_REFRESH_PLIST_NAME,
            int(args.overview_refresh_interval_minutes),
        )
        updated.append(OVERVIEW_REFRESH_LABEL)
        if not args.no_bootstrap and reload_launch_agent(OVERVIEW_REFRESH_LABEL, path):
            reloaded.append(OVERVIEW_REFRESH_LABEL)

    if args.nightly_organize_time:
        hour, minute, _ = parse_schedule_time(args.nightly_organize_time, "--nightly-organize-time")
        path = set_plist_calendar_time(NIGHTLY_ORGANIZE_PLIST_NAME, hour, minute)
        updated.append(NIGHTLY_ORGANIZE_LABEL)
        if not args.no_bootstrap and reload_launch_agent(NIGHTLY_ORGANIZE_LABEL, path):
            reloaded.append(NIGHTLY_ORGANIZE_LABEL)

    if args.nightly_finalize_time:
        hour, minute, _ = parse_schedule_time(args.nightly_finalize_time, "--nightly-finalize-time")
        path = set_plist_calendar_time(NIGHTLY_FINALIZE_PLIST_NAME, hour, minute)
        updated.append(NIGHTLY_FINALIZE_LABEL)
        if not args.no_bootstrap and reload_launch_agent(NIGHTLY_FINALIZE_LABEL, path):
            reloaded.append(NIGHTLY_FINALIZE_LABEL)

    payload = schedule_payload()
    if args.json:
        payload["updated"] = updated
        payload["reloaded"] = reloaded
        print_json(payload)
        return
    print_schedule_payload(payload, updated=updated, reloaded=reloaded)


def memory_summary_budget_payload(config=None):
    config = config or load_runtime_config(PATHS)
    budget = get_memory_summary_budget(PATHS)
    return {
        "activity_source": get_activity_source(PATHS),
        "activity_host": get_activity_host(PATHS),
        "model_cli": get_model_cli(PATHS),
        "codex_model": get_codex_model(PATHS),
        "claude_model": get_claude_model(PATHS),
        "claude_settings": get_claude_settings(PATHS),
        "claude_env_file": get_claude_env_file(PATHS),
        "memory_summary_max_tokens": budget["max_tokens"],
        "memory_summary_target_tokens": budget["target_tokens"],
        "memory_summary_warn_tokens": budget["warn_tokens"],
        "personal_memory_budget_tokens": budget["personal_memory_tokens"],
        "global_memory_budget_tokens": budget["global_memory_tokens"],
        "project_memory_budget_tokens": budget["project_memory_tokens"],
        "config_path": str(PATHS.runtime_dir / "config.json"),
        "configured_model_cli": config.get("model_cli"),
        "configured_codex_model": config.get("codex_model"),
        "configured_claude_model": config.get("claude_model"),
        "configured_claude_settings": config.get("claude_settings"),
        "configured_claude_env_file": config.get("claude_env_file"),
        "configured_activity_host": config.get("activity_host"),
        "configured_memory_summary_max_tokens": config.get("memory_summary_max_tokens"),
    }


def command_config(args):
    requested_max_tokens = args.memory_summary_max_tokens
    requested_activity_source = "auto" if args.read_codex_app else args.activity_source
    requested_activity_host = getattr(args, "activity_host", None)
    requested_model_cli = getattr(args, "model_cli", None)
    requested_codex_model = getattr(args, "codex_model", None)
    requested_claude_model = getattr(args, "claude_model", None)
    requested_claude_settings = getattr(args, "claude_settings", None)
    requested_claude_env_file = getattr(args, "claude_env_file", None)
    if (
        requested_max_tokens is None
        and requested_activity_source is None
        and requested_activity_host is None
        and requested_model_cli is None
        and requested_codex_model is None
        and requested_claude_model is None
        and requested_claude_settings is None
        and requested_claude_env_file is None
    ):
        payload = memory_summary_budget_payload()
        if args.json:
            print_json(payload)
            return
        print(localized("OpenRelix 运行配置", "OpenRelix runtime config"))
        print("- activity_source: {}".format(payload["activity_source"]))
        print("- activity_host: {}".format(payload["activity_host"]))
        print("- model_cli: {}".format(payload["model_cli"]))
        print("- codex_model: {}".format(payload["codex_model"]))
        print("- claude_model: {}".format(payload["claude_model"]))
        print("- claude_settings: {}".format(payload["claude_settings"] or "(default)"))
        print("- claude_env_file: {}".format(payload["claude_env_file"] or "(none)"))
        print("- memory_summary_max_tokens: {}".format(payload["memory_summary_max_tokens"]))
        print("- memory_summary_target_tokens: {}".format(payload["memory_summary_target_tokens"]))
        print("- memory_summary_warn_tokens: {}".format(payload["memory_summary_warn_tokens"]))
        print("- global_memory_budget_tokens: {}".format(payload["global_memory_budget_tokens"]))
        print("- project_memory_budget_tokens: {}".format(payload["project_memory_budget_tokens"]))
        print("- personal_memory_budget_tokens: {}".format(payload["personal_memory_budget_tokens"]))
        print("- config: {}".format(payload["config_path"]))
        return

    normalized_max_tokens = None
    if requested_max_tokens is not None:
        try:
            normalized_max_tokens = normalize_memory_summary_max_tokens(requested_max_tokens, strict=True)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    normalized_activity_source = None
    if requested_activity_source is not None:
        try:
            normalized_activity_source = normalize_activity_source(requested_activity_source, strict=True)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    normalized_activity_host = None
    if requested_activity_host is not None:
        try:
            normalized_activity_host = normalize_activity_host(requested_activity_host, strict=True)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    normalized_model_cli = None
    if requested_model_cli is not None:
        try:
            normalized_model_cli = normalize_model_cli(requested_model_cli, strict=True)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    normalized_codex_model = None
    if requested_codex_model is not None:
        try:
            normalized_codex_model = normalize_codex_model(requested_codex_model, strict=True)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    normalized_claude_model = None
    if requested_claude_model is not None:
        try:
            normalized_claude_model = normalize_claude_model(requested_claude_model, strict=True)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    config = write_runtime_config(
        activity_source=normalized_activity_source,
        activity_host=normalized_activity_host,
        model_cli=normalized_model_cli,
        codex_model=normalized_codex_model,
        claude_model=normalized_claude_model,
        claude_settings=requested_claude_settings,
        claude_env_file=requested_claude_env_file,
        memory_summary_max_tokens=normalized_max_tokens,
        paths=PATHS,
    )
    refreshed = False
    if not args.no_refresh:
        run_checked(["/bin/zsh", str(REFRESH_SCRIPT)])
        refreshed = True

    payload = memory_summary_budget_payload(config)
    payload["refreshed"] = refreshed
    if args.json:
        print_json(payload)
        return

    print(localized("OpenRelix 运行配置已更新", "OpenRelix runtime config updated"))
    print("- activity_source: {}".format(payload["activity_source"]))
    print("- activity_host: {}".format(payload["activity_host"]))
    print("- model_cli: {}".format(payload["model_cli"]))
    print("- codex_model: {}".format(payload["codex_model"]))
    print("- claude_model: {}".format(payload["claude_model"]))
    print("- claude_settings: {}".format(payload["claude_settings"] or "(default)"))
    print("- claude_env_file: {}".format(payload["claude_env_file"] or "(none)"))
    print("- memory_summary_max_tokens: {}".format(payload["memory_summary_max_tokens"]))
    print("- memory_summary_target_tokens: {}".format(payload["memory_summary_target_tokens"]))
    print("- memory_summary_warn_tokens: {}".format(payload["memory_summary_warn_tokens"]))
    print("- global_memory_budget_tokens: {}".format(payload["global_memory_budget_tokens"]))
    print("- project_memory_budget_tokens: {}".format(payload["project_memory_budget_tokens"]))
    print("- personal_memory_budget_tokens: {}".format(payload["personal_memory_budget_tokens"]))
    print("- config: {}".format(payload["config_path"]))
    if refreshed:
        print(localized("- summary、overview 和面板已刷新。", "- Summary, overview, and panel refreshed."))
    else:
        print(localized("- 未刷新；需要时运行 openrelix refresh。", "- Not refreshed; run openrelix refresh when needed."))


def command_context(args):
    cmd = [
        sys.executable,
        str(SYNC_HOST_MEMORY_SUMMARY_SCRIPT),
        "--print-json",
    ]
    result = run_checked_quiet(cmd)
    payload = json.loads(result.stdout or "{}")
    if args.json:
        print_json(payload)
        return

    print(localized("OpenRelix host context 已同步", "OpenRelix host context synced"))
    print("- summary: {}".format(payload.get("summary_path") or ""))
    synced = payload.get("synced") or []
    skipped = payload.get("skipped") or []
    if synced:
        print("- synced: {}".format(", ".join("{}:{}".format(item.get("host"), item.get("status")) for item in synced)))
    if skipped:
        print("- skipped: {}".format(", ".join("{}:{}".format(item.get("host"), item.get("status")) for item in skipped)))
    print(localized(
        "- 下次启动 Codex / Claude Code 时会读取这份 unified summary；已打开的窗口通常需要新开或重启后才稳定生效。",
        "- The next Codex / Claude Code start reads this unified summary; already-open windows usually need a new session or restart for stable effect.",
    ))


def sanitize_codex_model_entry(model):
    reasoning_levels = []
    for item in model.get("supported_reasoning_levels") or []:
        if isinstance(item, dict):
            effort = item.get("effort")
        else:
            effort = item
        if effort:
            reasoning_levels.append(str(effort))
    return {
        "slug": str(model.get("slug") or ""),
        "display_name": str(model.get("display_name") or model.get("slug") or ""),
        "description": str(model.get("description") or ""),
        "default_reasoning_level": str(model.get("default_reasoning_level") or ""),
        "supported_reasoning_levels": reasoning_levels,
        "supported_in_api": bool(model.get("supported_in_api")),
        "visibility": str(model.get("visibility") or ""),
        "priority": model.get("priority"),
    }


def load_codex_model_catalog(include_hidden=False, bundled=False):
    cmd = [PATHS.codex_bin, "debug", "models"]
    if bundled:
        cmd.append("--bundled")
    env = dict(os.environ)
    env["CODEX_HOME"] = str(PATHS.codex_home)
    result = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        timeout=20,
        env=env,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0:
        raise SystemExit(output[-1200:] or "codex debug models failed with exit code {}".format(result.returncode))
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit("codex debug models returned invalid JSON: {}".format(exc)) from exc

    models = []
    for model in payload.get("models") or []:
        if not isinstance(model, dict):
            continue
        entry = sanitize_codex_model_entry(model)
        if not entry["slug"]:
            continue
        if not include_hidden and entry["visibility"] != "list":
            continue
        models.append(entry)
    models.sort(key=lambda item: (item["priority"] is None, item["priority"] or 0, item["slug"]))
    return {
        "source": "codex debug models --bundled" if bundled else "codex debug models",
        "configured_model": get_codex_model(PATHS),
        "recommended_default": DEFAULT_CODEX_MODEL,
        "models": models,
    }


def command_models(args):
    payload = load_codex_model_catalog(include_hidden=args.all, bundled=args.bundled)
    if args.json:
        print_json(payload)
        return

    print(localized("Codex 模型 catalog", "Codex model catalog"))
    print("- source: {}".format(payload["source"]))
    print("- configured_model: {}".format(payload["configured_model"]))
    print("- recommended_default: {}".format(payload["recommended_default"]))
    print(localized("- 提示: 可用性以本机 Codex 登录和 provider 为准；切换命令是 openrelix config --codex-model <model>。", "- Note: availability depends on the local Codex login and provider; switch with openrelix config --codex-model <model>."))
    for model in payload["models"]:
        label = model["display_name"] or model["slug"]
        description = model["description"]
        reasoning = ",".join(model["supported_reasoning_levels"])
        suffix_parts = []
        if model["default_reasoning_level"]:
            suffix_parts.append("default_reasoning={}".format(model["default_reasoning_level"]))
        if reasoning:
            suffix_parts.append("reasoning={}".format(reasoning))
        if model["visibility"] and model["visibility"] != "list":
            suffix_parts.append("visibility={}".format(model["visibility"]))
        suffix = " [{}]".format(" | ".join(suffix_parts)) if suffix_parts else ""
        print("- {} ({}){}".format(model["slug"], label, suffix))


def command_tokens(args):
    provider = normalize_token_provider(getattr(args, "provider", "all"))
    if provider not in {"all", "codex", "claude"}:
        raise SystemExit("Unsupported token provider: {}".format(getattr(args, "provider", "")))
    window_days = max(int(getattr(args, "window_days", 7) or 7), 1)
    start_date = str(getattr(args, "start_date", "") or "").strip()
    end_date = str(getattr(args, "end_date", "") or "").strip()
    group_by = normalize_token_group_by(getattr(args, "group_by", "day"))
    result = fetch_ccusage_daily(
        window_days=window_days,
        provider=provider,
        start_date=start_date or None,
        end_date=end_date or None,
    )
    view = build_token_usage_view(
        result,
        language=LANGUAGE,
        group_by=group_by,
        start_date=start_date or None,
        end_date=end_date or None,
    )
    payload = {
        "ok": bool(view.get("available")),
        "provider": provider,
        "provider_label": view.get("provider_label", result.get("provider_label", "")),
        "window_days": window_days,
        "start_date": start_date,
        "end_date": end_date,
        "group_by": group_by,
        "token_usage": view,
        "error": view.get("error", ""),
    }
    if args.json:
        print_json(payload)
        return

    print(localized("OpenRelix Token 用量", "OpenRelix token usage"))
    print("- provider: {} ({})".format(provider, payload["provider_label"]))
    print("- range: {}".format(view.get("range_label") or window_days))
    print("- group_by: {}".format(group_by))
    if not view.get("available"):
        print("- status: unavailable")
        if view.get("error"):
            print("- error: {}".format(view.get("error")))
        return
    if view.get("partial") and view.get("error"):
        print("- status: partial ({})".format(view.get("error")))
    else:
        print("- status: ok")
    print("- total: {} · {}".format(view.get("period_total_tokens_display"), view.get("period_cost_display")))
    print("- latest: {} ({})".format(view.get("today_total_tokens_display"), view.get("today_date_label")))
    for row in view.get("daily_rows", []):
        print("- {}: {}".format(row.get("label"), row.get("display")))
        provider_label = row.get("provider_label", "")
        if provider_label:
            print("  provider: {}".format(provider_label))


def normalize_positive_int(value, label):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(localized(
            "{} 必须是整数: {}".format(label, value),
            "{} must be an integer: {}".format(label, value),
        )) from exc
    if parsed <= 0:
        raise SystemExit(localized(
            "{} 必须大于 0: {}".format(label, value),
            "{} must be greater than 0: {}".format(label, value),
        ))
    return parsed


def command_asset_stats(args):
    target_date = parse_date_arg(args.date, "--date")
    monthly_months = normalize_positive_int(args.monthly_months, "--monthly-months")
    top_limit = normalize_positive_int(args.top_limit, "--top-limit")
    runtime_config = load_runtime_config(PATHS)
    codex_profiles = overview_codex_profiles.collect_codex_profiles(
        PATHS,
        config=runtime_config,
        include_running=True,
    )
    codex_homes = [profile.codex_home for profile in codex_profiles]
    project_skill_roots = overview_skill_quarantine.configured_project_skill_roots(PATHS)
    snapshot = overview_asset_discovery.build_asset_stats_snapshot(
        PATHS,
        target_date,
        monthly_months=monthly_months,
        top_limit=top_limit,
        codex_homes=codex_homes,
        project_skill_roots=project_skill_roots,
    )
    snapshot["command"] = "openrelix asset-stats --date {}".format(snapshot["date"])
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(ASSET_STATS_LATEST_PATH, snapshot)

    refreshed = False
    if not args.no_refresh:
        if not args.json:
            print(localized("刷新中: 重建 overview 和面板。", "Refreshing: rebuilding overview and panel."), flush=True)
        run_checked_quiet([sys.executable, str(BUILD_OVERVIEW_SCRIPT)])
        refreshed = True

    if args.json:
        print_json(
            {
                "snapshot_path": str(ASSET_STATS_LATEST_PATH),
                "panel_path": str(REPORTS_DIR / "panel.html"),
                "refreshed": refreshed,
                "snapshot": snapshot,
            }
        )
        return

    summary = snapshot.get("summary", {})
    print(localized("资产统计快照已生成", "Asset stats snapshot generated"))
    print("- date: {}".format(snapshot.get("date", "")))
    print("- snapshot: {}".format(ASSET_STATS_LATEST_PATH))
    print("- panel: {}".format(REPORTS_DIR / "panel.html"))
    print("- discovered_assets: {}".format(summary.get("renderable_assets", 0)))
    print("- active_skills_30d: {}".format(summary.get("active_skills_30d", 0)))
    print("- skill_reads_30d: {}".format(summary.get("skill_reads_30d", summary.get("skill_sessions_30d", 0))))
    print("- skill_sessions_30d: {}".format(summary.get("skill_sessions_30d", 0)))


def collect_asset_codex_homes():
    runtime_config = load_runtime_config(PATHS)
    codex_profiles = overview_codex_profiles.collect_codex_profiles(
        PATHS,
        config=runtime_config,
        include_running=True,
    )
    return [profile.codex_home for profile in codex_profiles]


def format_quarantine_row(row):
    return "{key:<34} {typ:<5} {uses:<5} {last:<12} {status:<14} {reason}".format(
        key=str(row.get("entity_key") or "")[:34],
        typ=str(row.get("entity_type") or "")[:5],
        uses=str(row.get("usage_30d", 0)),
        last=str(row.get("last_used_at") or "-")[:12],
        status=str(row.get("isolation_status") or row.get("status") or "")[:14],
        reason=str(row.get("reason") or ""),
    )


def print_quarantine_rows(title, rows):
    print(title)
    print("-" * 92)
    if not rows:
        print(localized("暂无。", "None."))
        return
    print("{:<34} {:<5} {:<5} {:<12} {:<14} {}".format("entity", "type", "30d", "last", "status", "reason"))
    for row in rows:
        print(format_quarantine_row(row))


def command_skill_quarantine(args):
    codex_homes = collect_asset_codex_homes()
    today = datetime.now().astimezone().date()
    view = overview_skill_quarantine.build_quarantine_view(
        PATHS,
        today=today,
        codex_homes=codex_homes,
    )
    subcommand = args.subcommand or "list"
    if subcommand == "list":
        if args.json:
            print_json(view)
            return
        counts = view.get("counts", {})
        print(localized("Skill/MCP 小黑屋", "Skill/MCP Quarantine"))
        print("- suggested: {}".format(counts.get("suggested", 0)))
        print("- quarantined: {}".format(counts.get("quarantined", 0)))
        print("- grace: {}".format(counts.get("grace", 0)))
        print_quarantine_rows(localized("建议隔离", "Suggested"), view.get("suggested", []))
        print()
        print_quarantine_rows(localized("可选隔离", "Optional Isolation"), view.get("grace", []))
        print()
        print_quarantine_rows(localized("小黑屋", "Quarantine"), view.get("quarantined", []))
        return
    if subcommand == "suggest":
        rows = view.get("suggested", [])
        if args.json:
            print_json({"suggested": rows, "count": len(rows)})
            return
        print_quarantine_rows(localized("建议隔离", "Suggested"), rows)
        return
    if subcommand == "blocked":
        rows = view.get("quarantined", [])
        if args.json:
            print_json({"quarantined": rows, "count": len(rows)})
            return
        print_quarantine_rows(localized("小黑屋", "Quarantine"), rows)
        return
    if subcommand == "block":
        with overview_skill_quarantine.quarantine_action_lock(PATHS):
            entry = overview_skill_quarantine.block_entity(
                PATHS,
                args.entity,
                entity_type=getattr(args, "type", None),
                today=today,
                note=getattr(args, "note", ""),
                apply=not getattr(args, "no_apply", False),
                view=view,
                codex_homes=codex_homes,
            )
        print(localized("已隔离: {}", "Quarantined: {}").format(entry.get("entity_key")))
        print("- isolation_status: {}".format(entry.get("isolation_status")))
        print("- state: {}".format(overview_skill_quarantine.quarantine_state_path(PATHS)))
        return
    if subcommand == "unblock":
        with overview_skill_quarantine.quarantine_action_lock(PATHS):
            result = overview_skill_quarantine.unblock_entity(
                PATHS,
                args.entity,
                entity_type=getattr(args, "type", None),
                today=today,
                apply=not getattr(args, "no_apply", False),
                codex_homes=codex_homes,
            )
        print(localized("已恢复使用: {}", "Restored: {}").format(result.get("entity_key")))
        print("- ok: {}".format(result.get("ok")))
        return
    if subcommand in {"block-all", "auto-clean"}:
        preview = overview_skill_quarantine.block_all_suggestions(
            PATHS,
            today=today,
            dry_run=True,
            codex_homes=codex_homes,
        )
        suggestions = preview.get("suggested", [])
        if args.dry_run:
            if args.json:
                print_json(preview)
            else:
                print_quarantine_rows(localized("将隔离", "Will quarantine"), suggestions)
            return
        if suggestions and not args.yes:
            answer = input(localized(
                "确认移入 {} 个建议项？输入 yes 继续: ".format(len(suggestions)),
                "Quarantine {} suggested items? Type yes to continue: ".format(len(suggestions)),
            ))
            if answer.strip().lower() != "yes":
                print(localized("已取消。", "Cancelled."))
                return
        with overview_skill_quarantine.quarantine_action_lock(PATHS):
            result = overview_skill_quarantine.block_all_suggestions(
                PATHS,
                today=today,
                apply=not getattr(args, "no_apply", False),
                codex_homes=codex_homes,
            )
        if args.json:
            print_json(result)
            return
        print(localized("已隔离: {} 项", "Quarantined: {} items").format(len(result.get("blocked", []))))
        print("- state: {}".format(overview_skill_quarantine.quarantine_state_path(PATHS)))
        return
    if subcommand == "block-grace-all":
        preview = overview_skill_quarantine.block_all_grace(
            PATHS,
            today=today,
            dry_run=True,
            codex_homes=codex_homes,
        )
        grace_rows = preview.get("grace", [])
        if args.dry_run:
            if args.json:
                print_json(preview)
            else:
                print_quarantine_rows(localized("将隔离可选项", "Will quarantine optional items"), grace_rows)
            return
        if grace_rows and not args.yes:
            answer = input(localized(
                "确认移入 {} 个可选项？输入 yes 继续: ".format(len(grace_rows)),
                "Quarantine {} optional items? Type yes to continue: ".format(len(grace_rows)),
            ))
            if answer.strip().lower() != "yes":
                print(localized("已取消。", "Cancelled."))
                return
        with overview_skill_quarantine.quarantine_action_lock(PATHS):
            result = overview_skill_quarantine.block_all_grace(
                PATHS,
                today=today,
                apply=not getattr(args, "no_apply", False),
                codex_homes=codex_homes,
            )
        if args.json:
            print_json(result)
            return
        print(localized("已隔离可选项: {} 项", "Quarantined optional items: {}").format(len(result.get("blocked", []))))
        print("- state: {}".format(overview_skill_quarantine.quarantine_state_path(PATHS)))
        return
    raise SystemExit(localized("不支持的子命令: {}", "Unsupported subcommand: {}").format(subcommand))


def print_index_results(kind, rows):
    if not rows:
        print(localized("未找到结果。", "No results."))
        return
    for row in rows:
        if kind == "memory":
            title = row.get("title") or row.get("title_zh") or row.get("title_en") or row.get("memory_key") or "(untitled)"
            note = row.get("value_note") or row.get("value_note_zh") or row.get("value_note_en") or ""
            print("- {} [{} / {} / {}]".format(
                title,
                row.get("bucket") or "-",
                row.get("memory_type") or "-",
                row.get("priority") or "-",
            ))
            if row.get("date"):
                print("  date: {}".format(row["date"]))
            if note:
                print("  note: {}".format(note))
            if row.get("keywords"):
                print("  keywords: {}".format(", ".join(str(item) for item in row["keywords"])))
            if row.get("source_window_ids"):
                print("  windows: {}".format(", ".join(str(item) for item in row["source_window_ids"])))
        else:
            title = row.get("question_summary") or row.get("main_takeaway") or row.get("window_id") or "(window)"
            print("- {} [{}]".format(title, row.get("window_id") or "-"))
            print("  date: {} cwd: {}".format(row.get("date") or "-", row.get("cwd") or "-"))
            if row.get("main_takeaway"):
                print("  takeaway: {}".format(row["main_takeaway"]))
            if row.get("keywords"):
                print("  keywords: {}".format(", ".join(str(item) for item in row["keywords"])))


def command_index(args):
    import openrelix_index

    if args.action == "status":
        payload = openrelix_index.index_status(PATHS)
        if args.json:
            print_json(payload)
            return
        print(localized("OpenRelix SQLite 检索索引", "OpenRelix SQLite search index"))
        print("- db_path: {}".format(payload["db_path"]))
        print("- exists: {}".format(payload["exists"]))
        print("- ok: {}".format(payload["ok"]))
        print("- stale: {}".format(payload["stale"]))
        print("- schema_version: {}".format(payload["schema_version"]))
        print("- fts_enabled: {}".format(payload["fts_enabled"]))
        print("- memory_rows: {}".format(payload["memory_rows"]))
        print("- window_rows: {}".format(payload["window_rows"]))
        print("- daily_summary_rows: {}".format(payload["daily_summary_rows"]))
        print("- rebuilt_at: {}".format(payload["rebuilt_at"] or "-"))
        if payload.get("error"):
            print("- error: {}".format(payload["error"]))
        return

    if args.action == "rebuild":
        payload = openrelix_index.rebuild_index(PATHS)
        if args.json:
            print_json(payload)
            return
        print(localized("已重建 OpenRelix SQLite 检索索引", "Rebuilt the OpenRelix SQLite search index"))
        print("- db_path: {}".format(payload["db_path"]))
        print("- fts_enabled: {}".format(payload["fts_enabled"]))
        print("- memory_rows: {}".format(payload["memory_rows"]))
        print("- window_rows: {}".format(payload["window_rows"]))
        print("- daily_summary_rows: {}".format(payload["daily_summary_rows"]))
        print("- source_file_rows: {}".format(payload["source_file_rows"]))
        return

    if args.limit <= 0:
        raise SystemExit(localized("--limit 必须大于 0。", "--limit must be greater than 0."))

    if args.action == "search-memory":
        rows = openrelix_index.search_memories(
            args.query,
            bucket=args.bucket,
            priority=args.priority,
            scope=getattr(args, "scope", None),
            injection_policy=getattr(args, "injection_policy", None),
            date_from=args.date_from,
            date_to=args.date_to,
            limit=args.limit,
            paths=PATHS,
        )
        if args.json:
            print_json({"results": rows})
            return
        print_index_results("memory", rows)
        return

    if args.action == "search-window":
        rows = openrelix_index.search_windows(
            args.query,
            project=args.project,
            date_from=args.date_from,
            date_to=args.date_to,
            search_scope=getattr(args, "search_scope", "all"),
            limit=args.limit,
            paths=PATHS,
        )
        if args.json:
            print_json({"results": rows})
            return
        print_index_results("window", rows)
        return

    raise SystemExit(localized(
        "不支持的索引操作: {}".format(args.action),
        "unsupported index action: {}".format(args.action),
    ))


def command_recall(args):
    import openrelix_index

    if args.limit <= 0:
        raise SystemExit(localized("--limit 必须大于 0。", "--limit must be greater than 0."))
    rows = openrelix_index.search_memories(
        args.query,
        scope=args.scope,
        injection_policy="on_demand",
        limit=args.limit,
        paths=PATHS,
    )
    if args.json:
        print_json({"results": rows})
        return
    print(localized("按需召回记忆", "On-demand recall memories"))
    print_index_results("memory", rows)


def resolve_open_target(target, date_str):
    if target == "panel":
        return REPORTS_DIR / "panel.html"
    if target == "overview":
        return REPORTS_DIR / "overview.md"
    if target == "review":
        _, review_md_path = review_summary_paths(date_str)
        return review_md_path
    raise SystemExit(localized(
        "不支持的打开目标: {}".format(target),
        "unsupported open target: {}".format(target),
    ))


def open_path(path):
    if not path.exists():
        raise SystemExit(localized(
            "缺少产物: {}".format(path),
            "missing artifact: {}".format(path),
        ))

    if sys.platform == "darwin":
        cmd = shutil.which("open")
    else:
        cmd = shutil.which("xdg-open")

    if not cmd:
        raise SystemExit(localized(
            "当前平台未找到可用打开器",
            "no opener found for this platform",
        ))

    subprocess.run([cmd, str(path)], check=True)


def staged_macos_client_app_path():
    return PATHS.runtime_dir / "mac-app" / MACOS_CLIENT_APP_NAME


def default_macos_client_app_path():
    return Path.home() / "Applications" / MACOS_CLIENT_APP_NAME


def first_config_line(path):
    if not path.exists() or not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                return value
    except (OSError, UnicodeDecodeError):
        return ""
    return ""


def inherit_macos_client_analytics_env(env, app_path):
    resources = Path(app_path) / "Contents" / "Resources"
    if not env.get("OPENRELIX_ANALYTICS_ENDPOINT"):
        endpoint = first_config_line(resources / "OpenRelixAnalyticsEndpoint.txt")
        if endpoint:
            env["OPENRELIX_ANALYTICS_ENDPOINT"] = endpoint
    if not env.get("OPENRELIX_ANALYTICS_TOKEN"):
        token = first_config_line(resources / "OpenRelixAnalyticsToken.txt")
        if token:
            env["OPENRELIX_ANALYTICS_TOKEN"] = token


def remove_macos_client_app(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def sync_macos_client_app(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    remove_macos_client_app(destination)
    if shutil.which("ditto"):
        subprocess.run(["ditto", str(source), str(destination)], check=True)
    else:
        shutil.copytree(source, destination)
    lsregister = Path(
        "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
        "LaunchServices.framework/Support/lsregister"
    )
    if lsregister.exists():
        subprocess.run(
            [str(lsregister), "-f", str(destination)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def uninstall_launch_agent_labels():
    suffixes = (
        "overview-refresh",
        "token-live",
        "nightly-organize",
        "nightly-finalize-previous-day",
        "update-check",
    )
    prefixes = (
        "io.github.openrelix",
        "io.github.open" + "keepsake",
        "io.github.ai-personal-assets",
        "io.github.codex-personal-assets",
    )
    return ["{}.{}".format(prefix, suffix) for prefix in prefixes for suffix in suffixes]


def record_uninstall_action(actions, action, target, status, detail=""):
    actions.append(
        {
            "action": action,
            "target": str(target),
            "status": status,
            "detail": str(detail or ""),
        }
    )


def path_exists_or_symlink(path):
    return path.exists() or path.is_symlink()


def remove_path_for_uninstall(path, action, actions, dry_run=False, record_missing=True):
    path = Path(path).expanduser()
    if not path_exists_or_symlink(path):
        if record_missing:
            record_uninstall_action(actions, action, path, "missing")
        return
    if dry_run:
        record_uninstall_action(actions, action, path, "would_remove")
        return
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    except OSError as exc:
        record_uninstall_action(actions, action, path, "error", exc)
        return
    record_uninstall_action(actions, action, path, "removed")


def bootout_launch_agent(label, plist_path, dry_run=False):
    if dry_run or sys.platform != "darwin" or not shutil.which("launchctl"):
        return
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", "gui/{}/{}".format(uid, label)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if plist_path.exists():
        subprocess.run(
            ["launchctl", "bootout", "gui/{}".format(uid), str(plist_path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def remove_launch_agents_for_uninstall(actions, dry_run=False):
    for label in uninstall_launch_agent_labels():
        plist_path = launch_agent_path("{}.plist".format(label))
        bootout_launch_agent(label, plist_path, dry_run=dry_run)
        remove_path_for_uninstall(plist_path, "launch_agent", actions, dry_run=dry_run, record_missing=False)


def managed_shell_rc_candidates():
    candidates = [
        Path.home() / ".zshrc",
        Path.home() / ".bashrc",
        Path.home() / ".profile",
    ]
    shell_path = os.environ.get("SHELL")
    if shell_path:
        shell_name = Path(shell_path).name
        if shell_name == "zsh":
            candidates.insert(0, Path.home() / ".zshrc")
        elif shell_name == "bash":
            candidates.insert(0, Path.home() / ".bashrc")
    seen = set()
    unique = []
    for candidate in candidates:
        key = str(candidate.expanduser())
        if key not in seen:
            unique.append(candidate.expanduser())
            seen.add(key)
    return unique


def strip_managed_shell_path_block(text, marker="openrelix"):
    start = "# >>> {} >>>".format(marker)
    end = "# <<< {} <<<".format(marker)
    lines = text.splitlines()
    output = []
    removed = False
    index = 0
    while index < len(lines):
        if lines[index].strip() != start:
            output.append(lines[index])
            index += 1
            continue
        end_index = None
        for candidate in range(index + 1, len(lines)):
            if lines[candidate].strip() == end:
                end_index = candidate
                break
        if end_index is None:
            output.append(lines[index])
            index += 1
            continue
        removed = True
        index = end_index + 1

    if not removed:
        return text, False
    stripped = "\n".join(output).rstrip()
    return (stripped + "\n" if stripped else ""), True


def remove_shell_path_blocks_for_uninstall(actions, dry_run=False):
    for rc_path in managed_shell_rc_candidates():
        if not rc_path.exists():
            continue
        try:
            existing = rc_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            record_uninstall_action(actions, "shell_path_block", rc_path, "error", exc)
            continue
        updated, removed = strip_managed_shell_path_block(existing)
        if not removed:
            continue
        if dry_run:
            record_uninstall_action(actions, "shell_path_block", rc_path, "would_remove")
            continue
        try:
            rc_path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            record_uninstall_action(actions, "shell_path_block", rc_path, "error", exc)
            continue
        record_uninstall_action(actions, "shell_path_block", rc_path, "removed")


def openrelix_command_candidates():
    candidates = []
    command_path = os.environ.get("AI_ASSET_COMMAND_PATH")
    if command_path:
        candidates.append(Path(command_path).expanduser())
    which_path = shutil.which("openrelix")
    if which_path:
        candidates.append(Path(which_path))
    for directory in (
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path.home() / ".local" / "bin",
        Path.home() / "bin",
    ):
        candidates.append(directory / "openrelix")

    seen = set()
    unique = []
    for candidate in candidates:
        key = str(candidate.expanduser().resolve(strict=False))
        if key not in seen:
            unique.append(candidate.expanduser())
            seen.add(key)
    return unique


def is_managed_openrelix_command(path):
    if not path_exists_or_symlink(path) or path.is_dir():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return (
        "AI_ASSET_COMMAND_PATH" in text
        and "scripts/openrelix.py" in text
        and "OPENRELIX_ACTIVITY_SOURCE" in text
    )


def remove_global_commands_for_uninstall(actions, dry_run=False):
    for command_path in openrelix_command_candidates():
        if not path_exists_or_symlink(command_path):
            continue
        if not is_managed_openrelix_command(command_path):
            record_uninstall_action(actions, "global_command", command_path, "kept", "not an OpenRelix installer-managed command")
            continue
        remove_path_for_uninstall(command_path, "global_command", actions, dry_run=dry_run)


def remove_user_skill_for_uninstall(actions, dry_run=False):
    skill_path = PATHS.user_skill_root / "memory-review"
    if not path_exists_or_symlink(skill_path):
        record_uninstall_action(actions, "codex_skill", skill_path, "missing")
        return
    expected = PATHS.repo_skill_root / "memory-review"
    if skill_path.is_symlink() and skill_path.resolve(strict=False) == expected.resolve(strict=False):
        remove_path_for_uninstall(skill_path, "codex_skill", actions, dry_run=dry_run)
        return
    if skill_path.is_symlink():
        target = skill_path.resolve(strict=False)
        target_text = str(target)
        if target_text.endswith("/.agents/skills/memory-review") and "openrelix" in target_text.lower():
            remove_path_for_uninstall(skill_path, "codex_skill", actions, dry_run=dry_run)
            return
    record_uninstall_action(actions, "codex_skill", skill_path, "kept", "not the installer-managed symlink")


def is_managed_memory_review_prompt(path):
    if not path.exists() or path.is_dir():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "Installed state root:" in text and "installed OpenRelix system" in text


def remove_custom_prompt_for_uninstall(actions, dry_run=False):
    prompt_path = PATHS.codex_home / "prompts" / "memory-review.md"
    if not path_exists_or_symlink(prompt_path):
        record_uninstall_action(actions, "codex_prompt", prompt_path, "missing")
        return
    if is_managed_memory_review_prompt(prompt_path):
        remove_path_for_uninstall(prompt_path, "codex_prompt", actions, dry_run=dry_run)
        return
    record_uninstall_action(actions, "codex_prompt", prompt_path, "kept", "not the installer-managed prompt")


def path_is_relative_to(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def dangerous_state_root_delete_reason(path):
    path = Path(path).expanduser().resolve()
    home = Path.home().resolve()
    dangerous_exact = {
        Path("/").resolve(),
        home,
        REPO_ROOT.resolve(),
        PATHS.codex_home.resolve(),
        PATHS.claude_home.resolve(),
    }
    if path in dangerous_exact:
        return "refusing to delete a protected root"
    if path_is_relative_to(path, REPO_ROOT):
        return "refusing to delete a path inside the source repository"
    if path_is_relative_to(path, PATHS.codex_home):
        return "refusing to delete a path inside CODEX_HOME"
    if path_is_relative_to(path, PATHS.claude_home):
        return "refusing to delete a path inside CLAUDE_HOME"
    return ""


def state_root_for_slug(slug):
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / slug
    state_home = Path(os.environ.get("XDG_STATE_HOME", home / ".local" / "state")).expanduser()
    return state_home / slug


def local_memory_roots_for_uninstall():
    candidates = [PATHS.state_root]
    candidates.extend(state_root_for_slug(slug) for slug in (APP_SLUG, *LEGACY_APP_SLUGS))
    seen = set()
    roots = []
    for candidate in candidates:
        key = str(Path(candidate).expanduser().resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        roots.append(Path(candidate).expanduser())
    return roots


def should_delete_local_memory(args):
    if args.delete_local_memory:
        return True
    if args.keep_local_memory or args.yes or args.dry_run:
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False

    print(localized(
        "是否同时删除本地记忆？这会删除 state root，并移除 host context 里的 OpenRelix 受控块。",
        "Delete local memory too? This removes the state root and OpenRelix managed blocks from host context.",
    ))
    print("- state_root: {}".format(PATHS.state_root))
    print("- codex_summary: {}".format(PATHS.codex_home / "memories" / "memory_summary.md"))
    print("- claude_summary: {}".format(PATHS.claude_home / "CLAUDE.md"))
    answer = input(localized("删除本地记忆？[y/N]: ", "Delete local memory? [y/N]: ")).strip().lower()
    return answer in {"y", "yes", "是", "是的", "好", "好的", "1"}


def strip_managed_memory_block(text):
    if CLAUDE_MANAGED_MEMORY_START not in text or CLAUDE_MANAGED_MEMORY_END not in text:
        return text, False
    before, _, tail = text.partition(CLAUDE_MANAGED_MEMORY_START)
    _, _, after = tail.partition(CLAUDE_MANAGED_MEMORY_END)
    updated = "\n\n".join(part.strip() for part in (before, after) if part.strip())
    return (updated + "\n" if updated else ""), True


def strip_managed_claude_memory_block(text):
    return strip_managed_memory_block(text)


def is_legacy_openrelix_codex_summary(text):
    if CLAUDE_MANAGED_MEMORY_START in text or CLAUDE_MANAGED_MEMORY_END in text:
        return False
    stripped = text.lstrip()
    return (
        stripped.startswith("## User Profile")
        and LEGACY_CODEX_PROFILE_MARKER in stripped
        and LEGACY_CODEX_REGISTRY_MARKER in stripped
    )


def remove_codex_memory_summary_for_uninstall(actions, dry_run=False):
    summary_path = PATHS.codex_home / "memories" / "memory_summary.md"
    if not path_exists_or_symlink(summary_path):
        record_uninstall_action(actions, "codex_memory_summary", summary_path, "missing")
        return
    if summary_path.exists() and not (summary_path.is_file() or summary_path.is_symlink()):
        record_uninstall_action(actions, "codex_memory_summary", summary_path, "kept", "memory_summary.md path is not a file")
        return
    try:
        existing = summary_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        record_uninstall_action(actions, "codex_memory_summary", summary_path, "error", exc)
        return

    if is_legacy_openrelix_codex_summary(existing):
        if dry_run:
            record_uninstall_action(actions, "codex_memory_summary", summary_path, "would_remove", "legacy OpenRelix full-file summary")
            return
        try:
            summary_path.unlink()
        except OSError as exc:
            record_uninstall_action(actions, "codex_memory_summary", summary_path, "error", exc)
            return
        record_uninstall_action(actions, "codex_memory_summary", summary_path, "removed", "legacy OpenRelix full-file summary")
        return

    updated, removed = strip_managed_memory_block(existing)
    if not removed:
        record_uninstall_action(actions, "codex_memory_summary", summary_path, "kept", "no OpenRelix managed block")
        return
    if dry_run:
        record_uninstall_action(actions, "codex_memory_summary", summary_path, "would_remove", "managed block")
        return
    try:
        if updated.strip():
            atomic_write_text(summary_path, updated)
        else:
            summary_path.unlink()
    except OSError as exc:
        record_uninstall_action(actions, "codex_memory_summary", summary_path, "error", exc)
        return
    record_uninstall_action(actions, "codex_memory_summary", summary_path, "removed", "managed block")


def remove_claude_memory_summary_for_uninstall(actions, dry_run=False):
    summary_path = PATHS.claude_home / "CLAUDE.md"
    if not path_exists_or_symlink(summary_path):
        record_uninstall_action(actions, "claude_memory_summary", summary_path, "missing")
        return
    if summary_path.is_dir():
        record_uninstall_action(actions, "claude_memory_summary", summary_path, "kept", "CLAUDE.md path is a directory")
        return
    try:
        existing = summary_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        record_uninstall_action(actions, "claude_memory_summary", summary_path, "error", exc)
        return
    updated, removed = strip_managed_claude_memory_block(existing)
    if not removed:
        record_uninstall_action(actions, "claude_memory_summary", summary_path, "kept", "no OpenRelix managed block")
        return
    if dry_run:
        record_uninstall_action(actions, "claude_memory_summary", summary_path, "would_remove", "managed block")
        return
    try:
        if updated.strip():
            atomic_write_text(summary_path, updated)
        else:
            summary_path.unlink()
    except OSError as exc:
        record_uninstall_action(actions, "claude_memory_summary", summary_path, "error", exc)
        return
    record_uninstall_action(actions, "claude_memory_summary", summary_path, "removed", "managed block")


def remove_local_memory_for_uninstall(actions, dry_run=False):
    for state_root in local_memory_roots_for_uninstall():
        blocked_reason = dangerous_state_root_delete_reason(state_root)
        if blocked_reason:
            record_uninstall_action(actions, "local_memory", state_root, "blocked", blocked_reason)
        else:
            remove_path_for_uninstall(state_root, "local_memory", actions, dry_run=dry_run, record_missing=False)

    remove_codex_memory_summary_for_uninstall(actions, dry_run=dry_run)
    remove_claude_memory_summary_for_uninstall(actions, dry_run=dry_run)


def uninstall_status_label(status):
    labels = {
        "removed": localized("已删除", "removed"),
        "missing": localized("不存在", "missing"),
        "kept": localized("已保留", "kept"),
        "would_remove": localized("将删除", "would remove"),
        "blocked": localized("已阻止", "blocked"),
        "error": localized("失败", "error"),
    }
    return labels.get(status, status)


def print_uninstall_result(actions, delete_local_memory, dry_run=False):
    print(localized(
        "OpenRelix 卸载预览" if dry_run else "OpenRelix 卸载完成",
        "OpenRelix uninstall preview" if dry_run else "OpenRelix uninstall complete",
    ))
    for item in actions:
        detail = " ({})".format(item["detail"]) if item.get("detail") else ""
        print("- {} {}: {}{}".format(
            uninstall_status_label(item["status"]),
            item["action"],
            item["target"],
            detail,
        ))
    if not delete_local_memory:
        print(localized(
            "本地记忆已保留；需要彻底删除时运行 openrelix uninstall --delete-local-memory。",
            "Local memory was kept; run openrelix uninstall --delete-local-memory for a full purge.",
        ))


def command_uninstall(args):
    delete_local_memory = should_delete_local_memory(args)
    actions = []
    dry_run = bool(args.dry_run)

    remove_launch_agents_for_uninstall(actions, dry_run=dry_run)
    if sys.platform == "darwin":
        remove_path_for_uninstall(default_macos_client_app_path(), "macos_app", actions, dry_run=dry_run)
    remove_global_commands_for_uninstall(actions, dry_run=dry_run)
    remove_user_skill_for_uninstall(actions, dry_run=dry_run)
    remove_custom_prompt_for_uninstall(actions, dry_run=dry_run)
    remove_shell_path_blocks_for_uninstall(actions, dry_run=dry_run)
    if delete_local_memory:
        remove_local_memory_for_uninstall(actions, dry_run=dry_run)

    payload = {
        "dry_run": dry_run,
        "delete_local_memory": delete_local_memory,
        "state_root": str(PATHS.state_root),
        "codex_home": str(PATHS.codex_home),
        "actions": actions,
    }
    if args.json:
        print_json(payload)
    else:
        print_uninstall_result(actions, delete_local_memory, dry_run=dry_run)

    if any(item["status"] == "error" for item in actions):
        raise SystemExit(1)


def command_app(args):
    if sys.platform != "darwin":
        raise SystemExit(localized(
            "macOS 客户端只能在 macOS 上构建和打开。",
            "The macOS client can only be built and opened on macOS.",
        ))

    output_explicit = bool(args.output)
    app_path = Path(args.output).expanduser() if output_explicit else default_macos_client_app_path()
    if not app_path.is_absolute():
        app_path = Path.cwd() / app_path
    app_path = app_path.resolve()

    if getattr(args, "print_path", False):
        print(app_path)
        return

    should_build = getattr(args, "build", False) or not app_path.exists()
    if should_build:
        if not BUILD_MACOS_CLIENT_SCRIPT.exists():
            raise SystemExit(localized(
                "缺少 macOS 客户端构建脚本: {}".format(BUILD_MACOS_CLIENT_SCRIPT),
                "missing macOS client build script: {}".format(BUILD_MACOS_CLIENT_SCRIPT),
            ))
        env = os.environ.copy()
        env.setdefault("AI_ASSET_STATE_DIR", str(PATHS.state_root))
        build_output_path = app_path if output_explicit else staged_macos_client_app_path()
        inherit_macos_client_analytics_env(env, app_path)
        subprocess.run(
            [
                str(BUILD_MACOS_CLIENT_SCRIPT),
                "--output",
                str(build_output_path),
                "--state-root",
                str(PATHS.state_root),
            ],
            check=True,
            env=env,
        )
        if not output_explicit:
            sync_macos_client_app(build_output_path, app_path)

    if not getattr(args, "no_open", False):
        ensure_token_live_service()
        open_path(app_path)
    print(app_path)


def command_open(args):
    if args.target == "app":
        command_app(argparse.Namespace(
            build=False,
            no_open=False,
            output=None,
            print_path=False,
        ))
        return
    if args.target == "panel":
        ensure_token_live_service()
    target_path = resolve_open_target(args.target, args.date)
    open_path(target_path)
    print(target_path)


def command_paths():
    import openrelix_index

    today_summary_json, today_summary_md = review_summary_paths(current_date_str())
    command_path = os.environ.get("AI_ASSET_COMMAND_PATH")
    print(localized("运行路径", "Runtime paths"))
    print("- repo_root: {}".format(REPO_ROOT))
    print("- state_root: {}".format(PATHS.state_root))
    print("- codex_home: {}".format(PATHS.codex_home))
    print("- claude_home: {}".format(PATHS.claude_home))
    print("- language: {}".format(LANGUAGE))
    print("- memory_mode: {}".format(MEMORY_MODE))
    print("- model_cli: {}".format(MODEL_CLI))
    print("- activity_host: {}".format(ACTIVITY_HOST))
    print("- command: {}".format(Path(command_path).resolve() if command_path else Path(sys.argv[0]).resolve()))
    print("- panel: {}".format(REPORTS_DIR / "panel.html"))
    print("- overview: {}".format(REPORTS_DIR / "overview.md"))
    print("- index_db: {}".format(openrelix_index.default_db_path(PATHS)))
    print("- today_review_json: {}".format(today_summary_json))
    print("- today_review_md: {}".format(today_summary_md))


def main():
    parser = build_parser()
    args = parser.parse_args()
    read_only_index_status = args.command == "index" and getattr(args, "action", None) == "status"
    read_only_model_catalog = args.command == "models"
    read_only_token_query = args.command == "tokens"
    if args.command != "uninstall" and not read_only_index_status and not read_only_model_catalog and not read_only_token_query:
        ensure_state_layout(PATHS)
    if args.command in (None, "help"):
        parser.print_help()
        return

    if args.command == "review":
        command_review(args)
        return
    if args.command == "backfill":
        command_backfill(args)
        return
    if args.command == "core":
        command_core(args)
        return
    if args.command == "doctor":
        command_doctor(args)
        return
    if args.command == "refresh":
        command_refresh(args)
        return
    if args.command == "update":
        command_update(args)
        return
    if args.command == "memory-migration":
        command_memory_migration(args)
        return
    if args.command == "task-summary-migration":
        command_task_summary_migration(args)
        return
    if args.command == "uninstall":
        command_uninstall(args)
        return
    if args.command == "mode":
        command_mode(args)
        return
    if args.command == "schedule":
        command_schedule(args)
        return
    if args.command == "config":
        command_config(args)
        return
    if args.command == "context":
        command_context(args)
        return
    if args.command == "models":
        command_models(args)
        return
    if args.command == "tokens":
        command_tokens(args)
        return
    if args.command == "asset-stats":
        command_asset_stats(args)
        return
    if args.command in {"skill-quarantine", "skill-blackroom", "skill-blacklist"}:
        command_skill_quarantine(args)
        return
    if args.command == "index":
        command_index(args)
        return
    if args.command == "recall":
        command_recall(args)
        return
    if args.command == "open":
        command_open(args)
        return
    if args.command == "app":
        command_app(args)
        return
    if args.command == "paths":
        command_paths()
        return
    raise SystemExit(localized(
        "不支持的命令: {}".format(args.command),
        "unsupported command: {}".format(args.command),
    ))


if __name__ == "__main__":
    try:
        install_termination_signal_handlers()
        main()
    except KeyboardInterrupt:
        stop_active_child_processes()
        raise SystemExit(130)
