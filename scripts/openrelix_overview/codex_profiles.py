import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


CODEX_APP_SERVER_BIN_ENV = "OPENRELIX_CODEX_APP_SERVER_BIN"
CODEX_APP_SERVER_BINARY_SUFFIX = "/Contents/Resources/codex"


@dataclass(frozen=True)
class CodexProfile:
    codex_home: Path
    electron_user_data_path: str = ""
    source: str = "configured"
    process_id: int = 0


def expand_path(value):
    return Path(str(value)).expanduser()


def resolved_path_key(path):
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(Path(path).expanduser().absolute())


def read_env_value(text, key):
    pattern = re.compile(
        r"(?:^|\s){}=(.*?)(?=\s[A-Za-z_][A-Za-z0-9_]*=|$)".format(re.escape(key))
    )
    match = pattern.search(text or "")
    return match.group(1).strip() if match else ""


def read_arg_value(text, key):
    pattern = re.compile(r"(?:^|\s){}=(.*?)(?=\s--[A-Za-z0-9_.-]+=|\s[A-Za-z_][A-Za-z0-9_]*=|$)".format(re.escape(key)))
    match = pattern.search(text or "")
    return match.group(1).strip() if match else ""


def split_process_line(raw_line):
    line = str(raw_line or "").strip()
    match = re.match(r"^(\d+)\s+(.+)$", line)
    if not match:
        return 0, line
    try:
        process_id = int(match.group(1))
    except ValueError:
        process_id = 0
    return process_id, match.group(2).strip()


def parse_codex_profiles_from_process_text(text):
    profiles = []
    for raw_line in str(text or "").splitlines():
        process_id, line = split_process_line(raw_line)
        if not line or "CODEX_HOME=" not in line:
            continue
        if "/Contents/MacOS/Codex Helper" in line:
            continue
        if "/Contents/MacOS/Codex" not in line or "CODEX_ELECTRON_USER_DATA_PATH=" not in line:
            continue
        codex_home = read_env_value(line, "CODEX_HOME")
        if not codex_home:
            continue
        electron_user_data = read_env_value(line, "CODEX_ELECTRON_USER_DATA_PATH")
        if not electron_user_data:
            electron_user_data = read_arg_value(line, "--user-data-dir")
        profiles.append(
            CodexProfile(
                codex_home=expand_path(codex_home),
                electron_user_data_path=electron_user_data,
                source="running",
                process_id=process_id,
            )
        )
    return profiles


def discover_running_codex_profiles(timeout=0.75):
    if sys.platform != "darwin":
        return []
    if os.environ.get("OPENRELIX_DISABLE_RUNNING_CODEX_DISCOVERY", "").strip().lower() in {"1", "true", "yes", "on"}:
        return []
    try:
        result = subprocess.run(
            ["ps", "axeww", "-o", "pid=", "-o", "command="],
            text=True,
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return parse_codex_profiles_from_process_text(result.stdout)


def parse_codex_app_server_binaries_from_process_text(text):
    binaries = []
    seen = set()
    for raw_line in str(text or "").splitlines():
        _, command = split_process_line(raw_line)
        if not re.search(r"(?:^|\s)app-server(?:\s|$)", command):
            continue
        match = re.match(
            r"^(.+?{})(?:\s|$)".format(re.escape(CODEX_APP_SERVER_BINARY_SUFFIX)),
            command,
        )
        if not match:
            continue
        binary = match.group(1).strip()
        if binary in seen:
            continue
        seen.add(binary)
        binaries.append(binary)
    return binaries


def discover_running_codex_app_server_binaries(timeout=0.75):
    if sys.platform != "darwin":
        return []
    if os.environ.get("OPENRELIX_DISABLE_RUNNING_CODEX_DISCOVERY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return []
    try:
        result = subprocess.run(
            ["ps", "axww", "-o", "pid=", "-o", "command="],
            text=True,
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return parse_codex_app_server_binaries_from_process_text(result.stdout)


def common_codex_app_server_binaries(home=None):
    home = Path(home or Path.home()).expanduser()
    app_names = ("Codex.app", "ChatGPT.app")
    roots = (Path("/Applications"), home / "Applications")
    return [
        str(root / app_name / "Contents" / "Resources" / "codex")
        for root in roots
        for app_name in app_names
    ]


def resolve_codex_app_server_binary(fallback, env=None, include_running=True):
    env = os.environ if env is None else env
    explicit = str(env.get(CODEX_APP_SERVER_BIN_ENV, "") or "").strip()
    if explicit:
        return str(Path(explicit).expanduser())

    candidates = []
    if include_running:
        candidates.extend(discover_running_codex_app_server_binaries())
    common_candidates = [Path(item).expanduser() for item in common_codex_app_server_binaries()]
    common_candidates.sort(
        key=lambda path: path.stat().st_mtime if path.is_file() else 0,
        reverse=True,
    )
    candidates.extend(str(path) for path in common_candidates)
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return str(fallback)


def parse_path_list(value):
    raw = str(value or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    separators = r"[,\n]+"
    if "," not in raw and "\n" not in raw and os.pathsep in raw:
        separators = re.escape(os.pathsep)
    return [part.strip() for part in re.split(separators, raw) if part.strip()]


def configured_codex_profiles(primary_codex_home, config=None, env=None):
    env = env or os.environ
    config = config or {}
    profiles = [CodexProfile(codex_home=expand_path(primary_codex_home), source="primary")]
    for key in ("codex_homes", "extra_codex_homes"):
        value = config.get(key)
        if isinstance(value, list):
            profiles.extend(CodexProfile(codex_home=expand_path(item), source="config") for item in value if str(item).strip())
        elif isinstance(value, str):
            profiles.extend(CodexProfile(codex_home=expand_path(item), source="config") for item in parse_path_list(value))
    for key in ("OPENRELIX_CODEX_HOMES", "OPENRELIX_EXTRA_CODEX_HOMES", "AI_ASSET_CODEX_HOMES"):
        profiles.extend(CodexProfile(codex_home=expand_path(item), source="env") for item in parse_path_list(env.get(key, "")))
    return profiles


def merge_codex_profiles(profiles: Iterable[CodexProfile]):
    by_home = {}
    order = []
    for profile in profiles:
        if not profile or not profile.codex_home:
            continue
        key = resolved_path_key(profile.codex_home)
        if key not in by_home:
            by_home[key] = profile
            order.append(key)
            continue
        existing = by_home[key]
        by_home[key] = CodexProfile(
            codex_home=existing.codex_home,
            electron_user_data_path=existing.electron_user_data_path or profile.electron_user_data_path,
            source=existing.source if existing.source == "primary" else profile.source or existing.source,
            process_id=existing.process_id or profile.process_id,
        )
    return [by_home[key] for key in order]


def collect_codex_profiles(paths, config=None, include_running=True):
    profiles = list(configured_codex_profiles(paths.codex_home, config=config))
    if include_running:
        profiles.extend(discover_running_codex_profiles())
    return merge_codex_profiles(profiles)


def find_profile_for_home(codex_home, paths, config=None, include_running=True) -> Optional[CodexProfile]:
    if not codex_home:
        return None
    target = resolved_path_key(codex_home)
    for profile in collect_codex_profiles(paths, config=config, include_running=include_running):
        if resolved_path_key(profile.codex_home) == target:
            return profile
    return None
