#!/usr/bin/env python3

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
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
    ensure_state_layout,
    get_memory_mode,
    get_memory_summary_budget,
    get_activity_source,
    get_runtime_language,
    get_runtime_paths,
    load_runtime_config,
    normalize_activity_source,
    normalize_language,
    normalize_memory_summary_max_tokens,
    normalize_memory_mode,
    sync_codex_exec_home,
    write_runtime_config,
)


PATHS = get_runtime_paths()
LANGUAGE = get_runtime_language(PATHS)
MEMORY_MODE = get_memory_mode(PATHS)
ACTIVITY_SOURCE = get_activity_source(PATHS)
REPO_ROOT = PATHS.repo_root
REPORTS_DIR = PATHS.reports_dir
CONSOLIDATED_DAILY_DIR = PATHS.consolidated_daily_dir
REFRESH_SCRIPT = REPO_ROOT / "scripts" / "refresh_overview.sh"
NIGHTLY_PIPELINE_SCRIPT = REPO_ROOT / "scripts" / "nightly_pipeline.sh"
BUILD_OVERVIEW_SCRIPT = REPO_ROOT / "scripts" / "build_overview.py"
BUILD_CODEX_MEMORY_SUMMARY_SCRIPT = REPO_ROOT / "scripts" / "build_codex_memory_summary.py"
CONFIGURE_CODEX_USER_SCRIPT = REPO_ROOT / "install" / "configure_codex_user.py"
BUILD_MACOS_CLIENT_SCRIPT = REPO_ROOT / "scripts" / "build_macos_client.sh"
MACOS_CLIENT_APP_NAME = "OpenRelix.app"
NPM_PACKAGE_NAME = "openrelix"
NPM_LATEST_SPEC = "{}@latest".format(NPM_PACKAGE_NAME)


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
            "实际运行一次极小的 codex exec，验证模型认证链路。",
            "Run a tiny codex exec call to verify the model authentication path.",
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
            "刷新前调用轻量 review 流水线，立即提炼目标日期记忆并入库。",
            "Run a lightweight review pipeline before refresh to synthesize and store target-date memories immediately.",
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


def resolve_learning_backfill_dates(date_str, learn_window_days):
    dates = learning_window_dates(date_str, learn_window_days)
    if not dates:
        return []

    history_dates = codex_history_dates_for_targets(dates)
    missing_dates = []
    for candidate_date in dates:
        if review_summary_stage(candidate_date) == "final":
            continue
        raw_daily_path = PATHS.raw_daily_dir / "{}.json".format(candidate_date)
        if raw_daily_path.exists() or candidate_date in history_dates:
            missing_dates.append(candidate_date)
    return missing_dates


def review_summary_stage(date_str):
    summary_json_path, _ = review_summary_paths(date_str)
    if not summary_json_path.exists():
        return ""
    try:
        payload = load_json(summary_json_path)
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("stage") or "")


def run_checked(cmd):
    subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)


def run_checked_quiet(cmd):
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
    )
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


def run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
    process = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    message_index = 0
    started_at = time.monotonic()
    next_reminder_at = reminder_seconds
    stdout = ""
    stderr = ""
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
                        "仍在整理: 已等待约 {} 分钟，子流程仍在运行。".format(elapsed_minutes),
                        "Still organizing: waited about {} minutes; the subprocess is still running.".format(elapsed_minutes),
                    ),
                    flush=True,
                )
                next_reminder_at += reminder_seconds

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
    package_json = REPO_ROOT / "package.json"
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    return str(payload.get("version") or "").strip()


def fetch_latest_npm_version(package_name=NPM_PACKAGE_NAME, timeout=8):
    url = "https://registry.npmjs.org/{}/latest".format(package_name)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "openrelix-update-check"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return str(payload.get("version") or "").strip()


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
    return Path.home() / "Library" / "LaunchAgents" / filename


def read_launch_agent(filename):
    path = launch_agent_path(filename)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def launch_agent_exists(filename):
    return launch_agent_path(filename).exists()


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


def detected_update_install_flags():
    flags = []
    overview_text = read_launch_agent("io.github.openrelix.overview-refresh.plist")
    if overview_text:
        if plist_string_value(overview_text, "OPENRELIX_REFRESH_LEARN_MEMORY") == "1":
            flags.append("--enable-learning-refresh")
        else:
            flags.append("--enable-background-services")

    nightly_text = read_launch_agent("io.github.openrelix.nightly-organize.plist")
    if nightly_text:
        flags.append("--enable-nightly")
        keep_awake = plist_string_value(nightly_text, "AI_ASSET_KEEP_AWAKE")
        if keep_awake in {"none", "during-job"}:
            flags.extend(["--keep-awake", keep_awake])
        nightly_time = plist_calendar_time(nightly_text)
        if nightly_time:
            flags.extend(["--nightly-organize-time", nightly_time])

    nightly_finalize_text = read_launch_agent("io.github.openrelix.nightly-finalize-previous-day.plist")
    nightly_finalize_time = plist_calendar_time(nightly_finalize_text)
    if nightly_finalize_time:
        flags.extend(["--nightly-finalize-time", nightly_finalize_time])

    update_check_text = read_launch_agent("io.github.openrelix.update-check.plist")
    if update_check_text:
        flags.append("--enable-update-check")
        update_check_time = plist_calendar_time(update_check_text)
        if update_check_time:
            flags.extend(["--update-check-time", update_check_time])
    return flags


def build_update_install_command(recommended=False):
    cmd = [
        "npx",
        "-y",
        NPM_LATEST_SPEC,
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


def ensure_overview_snapshot():
    overview_path = REPORTS_DIR / "overview-data.json"
    if overview_path.exists():
        return overview_path
    run_checked([sys.executable, str(BUILD_OVERVIEW_SCRIPT)])
    return overview_path


def sync_review_outputs():
    if get_memory_mode(PATHS) == "integrated":
        run_checked_quiet(
            [
                sys.executable,
                str(BUILD_CODEX_MEMORY_SUMMARY_SCRIPT),
                "--memory-summary",
                str(PATHS.codex_home / "memories" / "memory_summary.md"),
            ]
        )
    run_checked_quiet([sys.executable, str(BUILD_OVERVIEW_SCRIPT)])


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
    print(json.dumps(payload, ensure_ascii=False, indent=2))


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
                "窗口: {} | 长期记忆: {} | 短期记忆: {} | 低优先记忆: {}".format(
                    nightly.get("raw_window_count", len(nightly.get("window_summaries", []))),
                    len(nightly.get("durable_memories", [])),
                    len(nightly.get("session_memories", [])),
                    len(nightly.get("low_priority_memories", [])),
                ),
                "Windows: {} | Long-term: {} | Short-term: {} | Low-priority: {}".format(
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


def run_doctor_model_check():
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


def run_codex_app_server_help_check():
    return subprocess.run(
        [PATHS.codex_bin, "app-server", "--help"],
        text=True,
        capture_output=True,
        timeout=10,
    )


def run_doctor_app_server_check():
    with TemporaryDirectory(prefix="openrelix-app-server-check-") as tmpdir:
        env = dict(os.environ)
        env["AI_ASSET_STATE_DIR"] = tmpdir
        env["CODEX_HOME"] = str(PATHS.codex_home)
        env["CODEX_BIN"] = str(PATHS.codex_bin)
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
        "activity_source",
        "ok",
        ACTIVITY_SOURCE,
        localized(
            "默认 auto 会优先读取 Codex 客户端 app-server，失败时回退 CLI history/session。",
            "Default auto reads Codex app-server first, then falls back to CLI history/session.",
        ),
    )

    if command_exists(PATHS.codex_bin):
        try:
            result = run_codex_app_server_help_check()
            output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
            if result.returncode == 0:
                append_doctor_check(checks, "codex_app_server_command", "ok", output.splitlines()[0] if output else "available")
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
        if not command_exists(PATHS.codex_bin):
            append_doctor_check(
                checks,
                "codex_app_server_probe",
                "fail",
                str(PATHS.codex_bin),
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
        if not command_exists(PATHS.codex_bin):
            append_doctor_check(
                checks,
                "codex_exec_model_check",
                "fail",
                str(PATHS.codex_bin),
                localized("先修复 codex_bin，再运行 --model-check。", "Fix codex_bin first, then rerun --model-check."),
            )
        else:
            try:
                result = run_doctor_model_check()
                output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
                if result.returncode == 0:
                    append_doctor_check(checks, "codex_exec_model_check", "ok", output[-300:])
                else:
                    append_doctor_check(
                        checks,
                        "codex_exec_model_check",
                        "fail",
                        output[-600:] or "codex exec failed with exit code {}".format(result.returncode),
                        localized(
                            "重新登录 Codex；如果使用集体/代理配置，同时检查 config.toml 的 model_provider/base_url；如果使用官方 key，再检查 OPENAI_API_KEY。",
                            "Log in to Codex again. If you use a shared/proxy provider, also check config.toml model_provider/base_url; if you use an official key, check OPENAI_API_KEY.",
                        ),
                    )
            except subprocess.TimeoutExpired:
                append_doctor_check(
                    checks,
                    "codex_exec_model_check",
                    "fail",
                    "codex exec timed out after 45 seconds",
                    localized("先确认 codex exec 在终端可交互运行。", "Confirm codex exec can run interactively in a terminal."),
                )
    else:
        append_doctor_check(
            checks,
            "codex_exec_model_check",
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


def command_review(args):
    if args.learn_window_days > 0:
        backfill_dates = resolve_learning_backfill_dates(args.date, args.learn_window_days)
        if backfill_dates:
            if not args.json:
                print(
                    localized(
                        "同步回溯: 近 {} 天有 {} 天缺失或非 final，先按 final 生成；该阶段不递归扩展学习窗口。".format(
                            args.learn_window_days,
                            len(backfill_dates),
                        ),
                        "Backfill sync: {} daily reports are missing or non-final in the last {} days; generating them as final first without recursively expanding the learning window.".format(
                            len(backfill_dates),
                            args.learn_window_days,
                        ),
                    )
                )
                print("{}: {}".format(localized("日期", "Dates"), ", ".join(backfill_dates)))
            sync_results = run_backfill_dates(
                backfill_dates,
                "final",
                learn_window_days=0,
                force=True,
                verbose=not args.json,
            )
            if not args.json:
                completed = sum(1 for item in sync_results if item["status"] == "completed")
                skipped = sum(1 for item in sync_results if item["status"] == "skipped_existing")
                print(
                    localized(
                        "同步回溯完成: 完成 {} 天 | 跳过 {} 天".format(completed, skipped),
                        "Backfill sync completed: completed {} | skipped {}".format(completed, skipped),
                    )
                )
                print("")
        elif not args.json:
            print(
                localized(
                    "同步回溯: 近 {} 天 final 日报已齐，或没有可回溯窗口。".format(args.learn_window_days),
                    "Backfill sync: final daily reports for the last {} days are already complete, or no source windows are available.".format(args.learn_window_days),
                )
            )

    cmd = ["/bin/zsh", str(NIGHTLY_PIPELINE_SCRIPT), args.date, args.stage]
    if args.learn_window_days > 0:
        cmd.extend(["--learn-window-days", str(args.learn_window_days)])
    if args.json:
        run_checked_with_progress(cmd, [])
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
    if not args.json:
        print(localized("刷新中: 同步 Codex context 摘要和面板。", "Refreshing: syncing Codex context summary and panel."))
    sync_review_outputs()
    if not args.json:
        print(localized("生成完成: 读取摘要。", "Generation complete: reading summary."))
    summary_json_path, summary_md_path = review_summary_paths(args.date)
    if not summary_json_path.exists():
        raise SystemExit(localized(
            "missing review summary: {}".format(summary_json_path),
            "missing review summary: {}".format(summary_json_path),
        ))

    summary = load_json(summary_json_path)
    model_failed = summary_has_model_failure(summary)
    if args.json:
        print_json(summary)
        if model_failed:
            raise SystemExit(1)
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
                "窗口: {} | 长期记忆: {} | 短期记忆: {} | 低优先记忆: {}".format(
                    summary.get("raw_window_count", len(summary.get("window_summaries", []))),
                    len(summary.get("durable_memories", [])),
                    len(summary.get("session_memories", [])),
                    len(summary.get("low_priority_memories", [])),
                ),
                "Windows: {} | Long-term: {} | Short-term: {} | Low-priority: {}".format(
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
        if model_failed:
            print("")
            print_model_failure_warning(summary, args.date)

    if args.open:
        open_path(summary_md_path)
    if model_failed:
        raise SystemExit(1)


def run_backfill_dates(dates, stage, learn_window_days=0, force=False, verbose=True):
    results = []

    for index, date_str in enumerate(dates, start=1):
        summary_json_path, summary_md_path = review_summary_paths(date_str)
        if summary_json_path.exists() and not force:
            results.append(
                {
                    "date": date_str,
                    "status": "skipped_existing",
                    "summary_json": str(summary_json_path),
                    "summary_md": str(summary_md_path),
                }
            )
            if verbose:
                print("[{}/{}] {} {}".format(index, len(dates), date_str, localized("已存在，跳过。", "exists; skipped.")))
            continue

        cmd = ["/bin/zsh", str(NIGHTLY_PIPELINE_SCRIPT), date_str, stage]
        if learn_window_days > 0:
            cmd.extend(["--learn-window-days", str(learn_window_days)])

        if verbose:
            print("[{}/{}] {} {}".format(index, len(dates), date_str, localized("开始回溯。", "started.")))
        run_checked_with_progress(
            cmd,
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

        results.append(
            {
                "date": date_str,
                "status": "completed",
                "summary_json": str(summary_json_path),
                "summary_md": str(summary_md_path),
            }
        )
        if verbose:
            print("[{}/{}] {} {}".format(index, len(dates), date_str, localized("完成。", "completed.")))

    return results


def command_backfill(args):
    dates = resolve_backfill_dates(args)
    if not args.json:
        print(localized("回溯开始", "Backfill started"))
        print("{}: {} -> {}".format(localized("日期范围", "Date range"), dates[0], dates[-1]))
        print("{}: {}".format(localized("阶段", "Stage"), args.stage))
        if args.learn_window_days > 0:
            print("{}: {} days".format(localized("窗口学习", "Window learning"), args.learn_window_days))

    results = run_backfill_dates(
        dates,
        args.stage,
        learn_window_days=args.learn_window_days,
        force=args.force,
        verbose=not args.json,
    )

    if args.json:
        print_json({"dates": results})
        return

    completed = sum(1 for item in results if item["status"] == "completed")
    skipped = sum(1 for item in results if item["status"] == "skipped_existing")
    print("")
    print(localized("回溯完成", "Backfill completed"))
    print("{}: {} | {}: {}".format(localized("完成", "Completed"), completed, localized("跳过", "Skipped"), skipped))
    print("- panel: {}".format(REPORTS_DIR / "panel.html"))
    print("- overview: {}".format(REPORTS_DIR / "overview.md"))


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
    command = build_update_install_command(recommended=args.recommended)
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

    if not shutil.which("npx"):
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

    subprocess.run(command, cwd=str(REPO_ROOT), check=True)


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


def memory_summary_budget_payload(config=None):
    config = config or load_runtime_config(PATHS)
    budget = get_memory_summary_budget(PATHS)
    return {
        "activity_source": normalize_activity_source(config.get("activity_source")),
        "memory_summary_max_tokens": budget["max_tokens"],
        "memory_summary_target_tokens": budget["target_tokens"],
        "memory_summary_warn_tokens": budget["warn_tokens"],
        "personal_memory_budget_tokens": budget["personal_memory_tokens"],
        "config_path": str(PATHS.runtime_dir / "config.json"),
        "configured_memory_summary_max_tokens": config.get("memory_summary_max_tokens"),
    }


def command_config(args):
    requested_max_tokens = args.memory_summary_max_tokens
    requested_activity_source = "auto" if args.read_codex_app else args.activity_source
    if requested_max_tokens is None and requested_activity_source is None:
        payload = memory_summary_budget_payload()
        if args.json:
            print_json(payload)
            return
        print(localized("OpenRelix 运行配置", "OpenRelix runtime config"))
        print("- activity_source: {}".format(payload["activity_source"]))
        print("- memory_summary_max_tokens: {}".format(payload["memory_summary_max_tokens"]))
        print("- memory_summary_target_tokens: {}".format(payload["memory_summary_target_tokens"]))
        print("- memory_summary_warn_tokens: {}".format(payload["memory_summary_warn_tokens"]))
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

    config = write_runtime_config(
        activity_source=normalized_activity_source,
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
    print("- memory_summary_max_tokens: {}".format(payload["memory_summary_max_tokens"]))
    print("- memory_summary_target_tokens: {}".format(payload["memory_summary_target_tokens"]))
    print("- memory_summary_warn_tokens: {}".format(payload["memory_summary_warn_tokens"]))
    print("- personal_memory_budget_tokens: {}".format(payload["personal_memory_budget_tokens"]))
    print("- config: {}".format(payload["config_path"]))
    if refreshed:
        print(localized("- summary、overview 和面板已刷新。", "- Summary, overview, and panel refreshed."))
    else:
        print(localized("- 未刷新；需要时运行 openrelix refresh。", "- Not refreshed; run openrelix refresh when needed."))


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
    target_path = resolve_open_target(args.target, args.date)
    open_path(target_path)
    print(target_path)


def command_paths():
    today_summary_json, today_summary_md = review_summary_paths(current_date_str())
    command_path = os.environ.get("AI_ASSET_COMMAND_PATH")
    print(localized("运行路径", "Runtime paths"))
    print("- repo_root: {}".format(REPO_ROOT))
    print("- state_root: {}".format(PATHS.state_root))
    print("- codex_home: {}".format(PATHS.codex_home))
    print("- language: {}".format(LANGUAGE))
    print("- memory_mode: {}".format(MEMORY_MODE))
    print("- command: {}".format(Path(command_path).resolve() if command_path else Path(sys.argv[0]).resolve()))
    print("- panel: {}".format(REPORTS_DIR / "panel.html"))
    print("- overview: {}".format(REPORTS_DIR / "overview.md"))
    print("- today_review_json: {}".format(today_summary_json))
    print("- today_review_md: {}".format(today_summary_md))


def main():
    ensure_state_layout(PATHS)
    parser = build_parser()
    args = parser.parse_args()
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
    if args.command == "mode":
        command_mode(args)
        return
    if args.command == "config":
        command_config(args)
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
    main()
