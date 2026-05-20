#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


UPDATE_TIMEOUT_SECONDS = 1800
UPDATE_LOG_TAIL_LINES = 80


def current_timestamp():
    return datetime.now().astimezone().isoformat()


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def tail_text(text, line_count=UPDATE_LOG_TAIL_LINES):
    lines = str(text or "").splitlines()
    return "\n".join(lines[-line_count:])


def output_says_up_to_date(text):
    normalized = str(text or "").lower()
    return (
        "当前已是最新版本" in normalized
        or "already up to date" in normalized
        or "openrelix is up to date" in normalized
    )


def write_status(status_file, **fields):
    payload = {
        "status": fields.pop("status"),
        "pid": os.getpid(),
        "updated_at": time.time(),
        "updated_at_iso": current_timestamp(),
    }
    payload.update(fields)
    atomic_write_json(status_file, payload)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Run a detached OpenRelix panel update.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--python-bin", default=sys.executable)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root).expanduser().resolve()
    status_file = Path(args.status_file).expanduser().resolve()
    python_bin = args.python_bin or sys.executable
    openrelix_cli = repo_root / "scripts" / "openrelix.py"
    command = [
        python_bin,
        str(openrelix_cli),
        "update",
        "--yes",
        "--force",
    ]
    env = os.environ.copy()
    env["AI_ASSET_STATE_DIR"] = str(Path(args.state_dir).expanduser())
    env["CODEX_HOME"] = str(Path(args.codex_home).expanduser())
    env["OPENRELIX_UPDATE_SOURCE"] = "panel"

    started_at = time.time()
    write_status(
        status_file,
        status="running",
        started_at=started_at,
        started_at_iso=current_timestamp(),
        command=" ".join(command),
        phase="installing",
        log_tail="",
        error="",
    )

    try:
        proc = subprocess.run(
            command,
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=UPDATE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        write_status(
            status_file,
            status="failed",
            started_at=started_at,
            ended_at=time.time(),
            exit_code=None,
            error="timeout",
            log_tail=tail_text(output),
            reload_after_ms=0,
        )
        return 1
    except Exception as exc:
        write_status(
            status_file,
            status="failed",
            started_at=started_at,
            ended_at=time.time(),
            exit_code=None,
            error=str(exc),
            log_tail="",
            reload_after_ms=0,
        )
        return 1

    output = (proc.stdout or "") + (proc.stderr or "")
    succeeded = proc.returncode == 0
    reinstall_failed = (not succeeded) and output_says_up_to_date(output)
    error = ""
    if not succeeded:
        if reinstall_failed:
            error = "reinstall_failed_exit_code={}".format(proc.returncode)
        else:
            error = "exit_code={}".format(proc.returncode)
    write_status(
        status_file,
        status="completed" if succeeded else ("reinstall_failed" if reinstall_failed else "failed"),
        started_at=started_at,
        ended_at=time.time(),
        exit_code=proc.returncode,
        error=error,
        log_tail=tail_text(output),
        reload_after_ms=1500 if succeeded else 0,
    )
    return 0 if succeeded or reinstall_failed else proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
