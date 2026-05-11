#!/usr/bin/env python3

import os
import pty
import re
import select
import shutil
import subprocess
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path

from asset_runtime import (
    build_claude_cli_env,
    default_claude_binary,
    get_claude_env_file,
    get_claude_settings,
    get_runtime_paths,
)


CLAUDE_DESKTOP_OPEN_PATH = "/open-claude-desktop"
CLAUDE_SESSION_ID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
CLAUDE_DESKTOP_BUNDLE_IDS = (
    "com.anthropic.claudefordesktop",
    "com.anthropic.claude",
)


def is_valid_claude_session_id(value):
    return bool(CLAUDE_SESSION_ID_RE.fullmatch(str(value or "").strip()))


def _configured_app_paths():
    explicit = os.environ.get("OPENRELIX_CLAUDE_APP_PATH", "").strip()
    paths = []
    if explicit:
        paths.append(Path(explicit).expanduser())
    paths.extend(
        [
            Path("/Applications/Claude.app"),
            Path.home() / "Applications" / "Claude.app",
        ]
    )
    return paths


@lru_cache(maxsize=1)
def claude_desktop_app_installed():
    if sys.platform != "darwin":
        return False
    for app_path in _configured_app_paths():
        if app_path.exists():
            return True

    mdfind = shutil.which("mdfind") or "/usr/bin/mdfind"
    if not Path(mdfind).exists():
        return False
    for bundle_id in CLAUDE_DESKTOP_BUNDLE_IDS:
        try:
            result = subprocess.run(
                [mdfind, "kMDItemCFBundleIdentifier == '{}'".format(bundle_id)],
                text=True,
                capture_output=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.stdout.strip():
            return True
    return False


def resolve_claude_cli_binary(paths=None):
    paths = paths or get_runtime_paths()
    candidates = [
        str(getattr(paths, "claude_bin", "") or ""),
        default_claude_binary(),
        shutil.which("claude") or "",
        "/opt/homebrew/bin/claude",
        "/usr/local/bin/claude",
    ]
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if not candidate:
            continue
        if os.path.isabs(candidate):
            candidate_path = Path(candidate)
            if candidate_path.exists() and os.access(candidate_path, os.X_OK):
                return str(candidate_path)
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return ""


def claude_desktop_resume_supported(paths=None):
    return bool(claude_desktop_app_installed() and resolve_claude_cli_binary(paths))


def _load_env_file(path):
    if not path:
        return {}
    env_path = Path(str(path)).expanduser()
    if not env_path.exists():
        return {}
    values = {}
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def build_claude_desktop_resume_env(paths=None, base_env=None):
    paths = paths or get_runtime_paths()
    return build_claude_cli_env(
        base_env=base_env,
        claude_home=paths.claude_home,
        env_file_values=_load_env_file(get_claude_env_file(paths)),
    )


def build_claude_desktop_resume_command(resume_id, paths=None, claude_bin=None):
    resume_id = str(resume_id or "").strip()
    if not is_valid_claude_session_id(resume_id):
        raise ValueError("invalid_resume_id")
    paths = paths or get_runtime_paths()
    resolved_bin = claude_bin or resolve_claude_cli_binary(paths)
    if not resolved_bin:
        raise FileNotFoundError("claude")
    cmd = [resolved_bin, "--resume", resume_id]
    settings = get_claude_settings(paths)
    if settings:
        cmd.extend(["--settings", settings])
    return cmd


def _drain_pty(master_fd, duration_seconds):
    deadline = time.time() + max(float(duration_seconds or 0), 0)
    while time.time() < deadline:
        timeout = min(0.2, max(deadline - time.time(), 0))
        try:
            readable, _, _ = select.select([master_fd], [], [], timeout)
        except (OSError, ValueError):
            return
        if not readable:
            continue
        try:
            os.read(master_fd, 4096)
        except OSError:
            return


def _terminate_process(process):
    if not process or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _run_pipe_resume(cmd, env, cwd, wait_after_seconds):
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
        cwd=str(cwd),
        start_new_session=True,
    )
    try:
        if process.stdin:
            process.stdin.write("/desktop\n")
            process.stdin.flush()
            process.stdin.close()
        time.sleep(wait_after_seconds)
    finally:
        _terminate_process(process)


def _run_pty_resume(cmd, env, cwd, input_delay_seconds, wait_after_seconds):
    master_fd = None
    slave_fd = None
    process = None
    try:
        master_fd, slave_fd = pty.openpty()
        process = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            cwd=str(cwd),
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)
        slave_fd = None
        _drain_pty(master_fd, input_delay_seconds)
        os.write(master_fd, b"/desktop\r")
        _drain_pty(master_fd, wait_after_seconds)
    finally:
        if slave_fd is not None:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
        _terminate_process(process)


def _run_claude_desktop_resume(cmd, env, cwd, input_delay_seconds=1.5, wait_after_seconds=8):
    try:
        _run_pty_resume(cmd, env, cwd, input_delay_seconds, wait_after_seconds)
    except Exception:
        _run_pipe_resume(cmd, env, cwd, wait_after_seconds)


def start_claude_desktop_resume(resume_id, paths=None):
    paths = paths or get_runtime_paths()
    resume_id = str(resume_id or "").strip()
    if not is_valid_claude_session_id(resume_id):
        return {"ok": False, "error": "invalid_resume_id"}
    if not claude_desktop_app_installed():
        return {"ok": False, "error": "claude_desktop_app_not_found"}
    claude_bin = resolve_claude_cli_binary(paths)
    if not claude_bin:
        return {"ok": False, "error": "claude_cli_not_found"}
    try:
        cmd = build_claude_desktop_resume_command(resume_id, paths=paths, claude_bin=claude_bin)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc) or "invalid_claude_resume_command"}
    env = build_claude_desktop_resume_env(paths)
    cwd = paths.runtime_dir
    cwd.mkdir(parents=True, exist_ok=True)
    worker = threading.Thread(
        target=_run_claude_desktop_resume,
        args=(cmd, env, cwd),
        daemon=True,
    )
    worker.start()
    return {"ok": True, "status": "starting", "resume_id": resume_id}
