import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from openrelix_overview import codex_profiles


CODEX_DESKTOP_OPEN_PATH = "/open-codex-thread"
DEFAULT_CODEX_APP_BINARY = Path("/Applications/Codex.app/Contents/MacOS/Codex")
DEFAULT_CODEX_HOME = Path.home() / ".codex"
DEFAULT_CODEX_ELECTRON_USER_DATA_PATH = Path.home() / "Library" / "Application Support" / "Codex"


def is_valid_codex_thread_id(value):
    return bool(
        re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            str(value or "").strip(),
        )
    )


def codex_thread_url(thread_id):
    thread_id = str(thread_id or "").strip()
    if not is_valid_codex_thread_id(thread_id):
        return ""
    return "codex://threads/{}".format(quote(thread_id, safe=""))


def build_codex_desktop_resume_command(thread_id, codex_home="", electron_user_data_path="", app_binary=None):
    url = codex_thread_url(thread_id)
    if not url:
        return []
    binary = Path(app_binary or os.environ.get("OPENRELIX_CODEX_APP_BINARY") or DEFAULT_CODEX_APP_BINARY)
    if not binary.exists():
        return []
    return [str(binary), url]


def build_codex_existing_profile_open_command(thread_id):
    url = codex_thread_url(thread_id)
    if not url:
        return []
    return ["open", url]


def build_codex_profile_launch_command(app_binary=None):
    binary = Path(app_binary or os.environ.get("OPENRELIX_CODEX_APP_BINARY") or DEFAULT_CODEX_APP_BINARY)
    if not binary.exists():
        return []
    return [str(binary)]


def same_resolved_path(left, right):
    if not left or not right:
        return False
    return codex_profiles.resolved_path_key(left) == codex_profiles.resolved_path_key(right)


def is_system_codex_profile(codex_home="", electron_user_data_path=""):
    codex_home = str(codex_home or "").strip()
    electron_user_data_path = str(electron_user_data_path or "").strip()
    if codex_home and not same_resolved_path(codex_home, DEFAULT_CODEX_HOME):
        return False
    if electron_user_data_path and not same_resolved_path(
        electron_user_data_path,
        DEFAULT_CODEX_ELECTRON_USER_DATA_PATH,
    ):
        return False
    return True


def should_reuse_running_codex_profile():
    return os.environ.get("OPENRELIX_REUSE_RUNNING_CODEX_APP", "1").strip().lower() not in {"0", "false", "no", "off"}


def focus_codex_process(process_id, timeout=0.5):
    if not process_id or os.environ.get("OPENRELIX_DISABLE_CODEX_PROCESS_FOCUS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    script = (
        'tell application "System Events" to set frontmost of '
        '(first process whose unix id is {}) to true'
    ).format(int(process_id))
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return result.returncode == 0


def open_existing_codex_profile(thread_id, process_id=0):
    if not codex_thread_url(thread_id):
        return {"ok": False, "error": "invalid_codex_thread_id"}
    focused = focus_codex_process(process_id)
    return {
        "ok": True,
        "pid": int(process_id or 0),
        "status": "focused",
        "focus_result": focused,
        "thread_navigation": "profile_focus_only",
        "exact_thread_navigation": False,
    }


def open_system_codex_thread(thread_id):
    command = build_codex_existing_profile_open_command(thread_id)
    if not command:
        return {"ok": False, "error": "invalid_codex_thread_id"}
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        return {"ok": False, "error": "codex_desktop_open_failed", "detail": str(exc)}
    return {
        "ok": True,
        "pid": proc.pid,
        "status": "opened",
        "resume_id": thread_id,
        "used_profile": False,
        "reused_running_profile": False,
        "thread_navigation": "deeplink_open",
        "exact_thread_navigation": True,
    }


def start_codex_desktop_resume(
    thread_id,
    codex_home="",
    electron_user_data_path="",
    paths=None,
    app_binary=None,
):
    thread_id = str(thread_id or "").strip()
    if not is_valid_codex_thread_id(thread_id):
        return {"ok": False, "error": "invalid_codex_thread_id"}

    if is_system_codex_profile(codex_home, electron_user_data_path):
        return open_system_codex_thread(thread_id)

    resolved_profile = None
    if codex_home and paths is not None:
        resolved_profile = codex_profiles.find_profile_for_home(codex_home, paths)
    if resolved_profile:
        electron_user_data_path = electron_user_data_path or resolved_profile.electron_user_data_path
        if resolved_profile.process_id and should_reuse_running_codex_profile():
            snapshot = open_existing_codex_profile(thread_id, process_id=resolved_profile.process_id)
            if snapshot.get("ok"):
                snapshot.update(
                    {
                        "resume_id": thread_id,
                        "used_profile": True,
                        "reused_running_profile": True,
                        "target_process_id": resolved_profile.process_id,
                    }
                )
                return snapshot
    if codex_home and paths is not None and not electron_user_data_path:
        requested = codex_profiles.resolved_path_key(codex_home)
        primary = codex_profiles.resolved_path_key(paths.codex_home)
        if requested != primary:
            return {"ok": False, "error": "codex_desktop_profile_unknown"}

    command = build_codex_profile_launch_command(app_binary=app_binary)
    if not command:
        return {"ok": False, "error": "codex_desktop_app_not_found"}

    env = os.environ.copy()
    if codex_home:
        env["CODEX_HOME"] = str(Path(codex_home).expanduser())
    if electron_user_data_path:
        env["CODEX_ELECTRON_USER_DATA_PATH"] = str(Path(electron_user_data_path).expanduser())
    try:
        proc = subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        return {"ok": False, "error": "codex_desktop_open_failed", "detail": str(exc)}
    return {
        "ok": True,
        "pid": proc.pid,
        "status": "launched",
        "resume_id": thread_id,
        "used_profile": bool(codex_home or electron_user_data_path),
        "reused_running_profile": False,
        "thread_navigation": "profile_launch_only",
        "exact_thread_navigation": False,
    }
