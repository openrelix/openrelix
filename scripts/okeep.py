#!/usr/bin/env python3

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from asset_runtime import (
    ensure_state_layout,
    get_memory_mode,
    get_runtime_language,
    get_runtime_paths,
    normalize_language,
    normalize_memory_mode,
    write_runtime_config,
)


PATHS = get_runtime_paths()
LANGUAGE = get_runtime_language(PATHS)
MEMORY_MODE = get_memory_mode(PATHS)
REPO_ROOT = PATHS.repo_root
REPORTS_DIR = PATHS.reports_dir
CONSOLIDATED_DAILY_DIR = PATHS.consolidated_daily_dir
REFRESH_SCRIPT = REPO_ROOT / "scripts" / "refresh_overview.sh"
NIGHTLY_PIPELINE_SCRIPT = REPO_ROOT / "scripts" / "nightly_pipeline.sh"
BUILD_OVERVIEW_SCRIPT = REPO_ROOT / "scripts" / "build_overview.py"
CONFIGURE_CODEX_USER_SCRIPT = REPO_ROOT / "install" / "configure_codex_user.py"


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
        prog="okeep",
        description=localized("OpenKeepsake 命令集。", "OpenKeepsake command set."),
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

    mode = subparsers.add_parser(
        "mode",
        help=localized(
            "查看或切换 OpenKeepsake 记忆模式。",
            "Show or switch the OpenKeepsake memory mode.",
        ),
    )
    mode.add_argument(
        "memory_mode",
        nargs="?",
        help=localized(
            "目标模式：codex-context | local-only | off。省略时只显示当前模式。",
            "Target mode: codex-context | local-only | off. Omit to show the current mode.",
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

    open_cmd = subparsers.add_parser(
        "open",
        help=localized("打开生成产物。", "Open a generated artifact."),
    )
    open_cmd.add_argument(
        "target",
        nargs="?",
        default="panel",
        choices=["panel", "overview", "review"],
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


def ensure_overview_snapshot():
    overview_path = REPORTS_DIR / "overview-data.json"
    if overview_path.exists():
        return overview_path
    run_checked([sys.executable, str(BUILD_OVERVIEW_SCRIPT)])
    return overview_path


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_overview():
    overview_path = ensure_overview_snapshot()
    return load_json(overview_path)


def review_summary_paths(date_str):
    summary_dir = CONSOLIDATED_DAILY_DIR / date_str
    return summary_dir / "summary.json", summary_dir / "summary.md"


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
        print(localized("生成完成: 读取摘要。", "Generation complete: reading summary."))
    summary_json_path, summary_md_path = review_summary_paths(args.date)
    if not summary_json_path.exists():
        raise SystemExit(localized(
            "missing review summary: {}".format(summary_json_path),
            "missing review summary: {}".format(summary_json_path),
        ))

    summary = load_json(summary_json_path)
    if args.json:
        print_json(summary)
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

    if args.open:
        open_path(summary_md_path)


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
    run_checked(["/bin/zsh", str(REFRESH_SCRIPT)])
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
    print(localized("概览已刷新", "Overview refreshed"))
    print("")
    print_core_summary(data)


def memory_mode_label(memory_mode):
    labels = {
        "codex-context": localized(
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
    if memory_mode == "codex-context":
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
        print(localized("当前 OpenKeepsake 记忆模式", "Current OpenKeepsake memory mode"))
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

    print(localized("OpenKeepsake 记忆模式已更新", "OpenKeepsake memory mode updated"))
    print("- memory_mode: {}".format(config.get("memory_mode")))
    print("- {}".format(memory_mode_label(config.get("memory_mode"))))
    print("- config: {}".format(PATHS.runtime_dir / "config.json"))
    if codex_config_updated:
        print("- codex_config: {}".format(PATHS.codex_home / "config.toml"))
    if args.no_refresh:
        print(localized("- 未刷新 overview；需要时运行 okeep refresh。", "- Overview not refreshed; run okeep refresh when needed."))
    else:
        print(localized("- overview 和面板已刷新。", "- Overview and panel refreshed."))


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


def command_open(args):
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
    if args.command == "refresh":
        command_refresh(args)
        return
    if args.command == "mode":
        command_mode(args)
        return
    if args.command == "open":
        command_open(args)
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
