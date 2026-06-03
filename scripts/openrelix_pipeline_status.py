#!/usr/bin/env python3

import argparse
import json
import sys

from asset_runtime import get_runtime_paths
from openrelix_overview import pipeline_status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Update the OpenRelix runtime pipeline status.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--pipeline", required=True)
    start.add_argument("--target-date", default="")
    start.add_argument("--stage", default="")
    start.add_argument("--pid", type=int, default=0)
    start.add_argument("--json", action="store_true")

    step = subparsers.add_parser("step")
    step.add_argument("--run-id", required=True)
    step.add_argument("--step", required=True)
    step.add_argument("--message", default="")
    step.add_argument("--message-en", default="")

    tokens = subparsers.add_parser("tokens")
    tokens.add_argument("--run-id", required=True)
    tokens.add_argument("--input-tokens", type=int, default=0)
    tokens.add_argument("--output-tokens", type=int, default=0)
    tokens.add_argument("--cached-input-tokens", type=int, default=0)
    tokens.add_argument("--source", default="estimate")
    tokens.add_argument("--model", default="")

    finish = subparsers.add_parser("finish")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--status", choices=["completed", "failed"], required=True)
    finish.add_argument("--exit-code", type=int)
    finish.add_argument("--error", default="")

    status = subparsers.add_parser("status")
    status.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    paths = get_runtime_paths()

    if args.command == "start":
        payload = pipeline_status.start_run(
            args.pipeline,
            target_date=args.target_date,
            stage=args.stage,
            pid=args.pid or None,
            paths=paths,
        )
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(payload.get("run_id", ""))
        return 0

    if args.command == "step":
        pipeline_status.update_step(
            args.run_id,
            args.step,
            message=args.message,
            message_en=args.message_en,
            paths=paths,
        )
        return 0

    if args.command == "tokens":
        pipeline_status.record_token_usage(
            args.run_id,
            input_tokens=args.input_tokens,
            output_tokens=args.output_tokens,
            cached_input_tokens=args.cached_input_tokens,
            source=args.source,
            model=args.model,
            paths=paths,
        )
        return 0

    if args.command == "finish":
        pipeline_status.finish_run(
            args.run_id,
            status=args.status,
            exit_code=args.exit_code,
            error=args.error,
            paths=paths,
        )
        return 0

    payload = pipeline_status.load_status(paths)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
