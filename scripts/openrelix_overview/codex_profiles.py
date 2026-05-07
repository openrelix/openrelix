import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class CodexProfile:
    codex_home: Path
    electron_user_data_path: str = ""
    source: str = "configured"


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


def parse_codex_profiles_from_process_text(text):
    profiles = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or "CODEX_HOME=" not in line:
            continue
        if "Codex.app" not in line and "codex app-server" not in line and "CODEX_ELECTRON_USER_DATA_PATH=" not in line:
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
            ["ps", "axeww", "-o", "command="],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return parse_codex_profiles_from_process_text(result.stdout)


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
