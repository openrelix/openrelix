#!/usr/bin/env python3
"""One-command OpenViking defaults for OpenRelix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import openrelix_model_runner  # noqa: E402
import openrelix_openviking  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Set up OpenViking defaults, run optional OpenRelix backfill, and push OpenViking summaries."
    )
    parser.add_argument("--url", default=openrelix_openviking.DEFAULT_OPENVIKING_URL)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--account", default=None)
    parser.add_argument("--user", default=None)
    parser.add_argument("--agent-id", default=openrelix_openviking.DEFAULT_OPENVIKING_AGENT_ID)
    parser.add_argument("--timeout", type=float, default=openrelix_openviking.DEFAULT_OPENVIKING_TIMEOUT)
    parser.add_argument("--package", default="openviking")
    parser.add_argument("--install", action="store_true", help="Force OpenViking installation.")
    parser.add_argument("--skip-install", action="store_true", help="Skip OpenViking installation.")
    parser.add_argument("--no-force-reinstall", action="store_true", help="Do not append --force-reinstall.")
    parser.add_argument("--server-init", action="store_true", help="Run interactive openviking-server init.")
    parser.add_argument("--doctor", action="store_true", help="Run openviking-server doctor.")
    parser.add_argument("--date", default=openrelix_openviking.today_str())
    parser.add_argument("--from", dest="date_from")
    parser.add_argument("--to", dest="date_to")
    parser.add_argument("--days", type=int, default=openrelix_openviking.DEFAULT_SETUP_BACKFILL_DAYS)
    parser.add_argument("--project", default="")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--skip-backfill", action="store_true")
    parser.add_argument(
        "--backfill-stage",
        default="final",
        choices=["manual", "preliminary", "final"],
    )
    parser.add_argument("--force-backfill", action="store_true")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--learn-window-days", type=int, default=0)
    parser.add_argument("--no-summarize", action="store_true")
    parser.add_argument("--require-service", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--task-timeout", type=float, default=180.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def install_mode_from_args(args: argparse.Namespace) -> str:
    if args.install:
        return "always"
    if args.skip_install:
        return "never"
    return "auto"


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    payload = openrelix_openviking.setup_openviking_defaults(
        url=args.url,
        api_key=args.api_key,
        account=args.account,
        user=args.user,
        agent_id=args.agent_id,
        timeout=args.timeout,
        write_ovcli=True,
        install_mode=install_mode_from_args(args),
        package=args.package,
        force_reinstall=not args.no_force_reinstall,
        server_init=args.server_init,
        doctor=args.doctor,
        run_backfill=not args.skip_backfill,
        backfill_stage=args.backfill_stage,
        force_backfill=args.force_backfill,
        jobs=args.jobs,
        learn_window_days=args.learn_window_days,
        run_summarize=not args.no_summarize,
        require_service=args.require_service,
        date=args.date,
        date_from=args.date_from,
        date_to=args.date_to,
        days=args.days,
        project=args.project,
        limit=args.limit,
        wait=not args.no_wait,
        task_timeout=args.task_timeout,
        poll_interval=args.poll_interval,
        dry_run=args.dry_run,
    )
    payload = openrelix_model_runner.sanitize_model_input(payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print("OpenViking setup")
    print("- dry_run: {}".format(payload["dry_run"]))
    print("- date_range: {}..{}".format(payload["date_from"], payload["date_to"]))
    for step in payload.get("steps") or []:
        print("- {}: {}".format(step.get("name"), step.get("status")))
        if step.get("reason"):
            print("  reason: {}".format(step["reason"]))
    for item in payload.get("next_steps") or []:
        print("- next: {}".format(item))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
