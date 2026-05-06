"""Render-time discovery for local Codex and Claude assets.

This module reads installed skills, prompts, rules, plugins, and launchd
templates without writing to the asset ledger. Activation frequency is inferred
from recent session history when the model actually read a SKILL.md file. The
scanner classifies skill manifests by path, so the CLI that produced the
session is only transport; the manifest location decides the asset kind.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from collections import OrderedDict
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path

from . import redaction as overview_redaction


HIGH_LEVEL_TYPE = {
    "codex_skill": "skill",
    "claude_skill": "skill",
    "repo_skill": "skill",
    "external_repo_skill": "skill",
    "project_skill": "skill",
    "codex_prompt": "prompt",
    "codex_rule": "rule",
    "claude_plugin": "plugin",
    "launch_agent": "automation",
}
HIGH_LEVEL_TYPE_ORDER = (
    "skill",
    "prompt",
    "rule",
    "plugin",
    "automation",
)
DISCOVERED_KIND_ORDER = (
    "codex_skill",
    "claude_skill",
    "repo_skill",
    "external_repo_skill",
    "project_skill",
    "codex_prompt",
    "codex_rule",
    "claude_plugin",
    "launch_agent",
)
INSTALLED_DISCOVERY_KINDS = (
    "codex_skill",
    "claude_skill",
    "repo_skill",
    "codex_prompt",
    "codex_rule",
    "claude_plugin",
    "launch_agent",
)
SKILL_ASSET_KINDS = {
    "codex_skill",
    "claude_skill",
    "repo_skill",
    "external_repo_skill",
    "project_skill",
}
ORGANIC_SKILL_KINDS = {"external_repo_skill", "project_skill"}
NOISE_GATED_KINDS = ORGANIC_SKILL_KINDS
NON_SKILL_KINDS = set(INSTALLED_DISCOVERY_KINDS) - SKILL_ASSET_KINDS

SKILL_FILENAME = "SKILL.md"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
_SKILL_MANIFEST_RE = re.compile(
    r"(?P<path>(?:~|/|\.\.?/)?[^\s'\"`<>|;]*?(?:\.agents/skills|skills)/"
    r"(?P<name>[\w.-]+)/SKILL\.md)"
)
_EXEC_COMMAND_NAMES = {"exec_command", "functions.exec_command"}
_CODEX_SKILL_COMMAND_RG_PATTERN = r'SKILL\.md.*(?:\\"cmd\\"|"cmd")|(?:\\"cmd\\"|"cmd").*SKILL\.md'


def asset_key(kind, identifier):
    return "{}:{}".format(kind, identifier)


def _high_level_type(kind):
    value = str(kind or "").strip()
    if value in HIGH_LEVEL_TYPE:
        return HIGH_LEVEL_TYPE[value]
    if value in HIGH_LEVEL_TYPE_ORDER:
        return value
    if value in {"playbook", "template", "knowledge_card", "review"}:
        return "skill"
    return ""


def zero_frequency():
    return {"windows_7d": 0, "windows_30d": 0, "last_seen": None}


def _kind_index(kind):
    try:
        return DISCOVERED_KIND_ORDER.index(kind)
    except ValueError:
        return len(DISCOVERED_KIND_ORDER)


def _safe_identifier(value):
    identifier = str(value or "").strip()
    if not identifier or identifier.startswith(".") or identifier == "__MACOSX":
        return ""
    if "/" in identifier or "\\" in identifier:
        return ""
    return identifier


def _is_expected_identifier(value):
    identifier = _safe_identifier(value)
    return bool(identifier and _IDENTIFIER_RE.match(identifier))


def _safe_json_load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _redact_display_text(value):
    return overview_redaction.redact_personal_text(str(value or ""))


def _normalize_frontmatter_value(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().strip("\"'")


def _parse_skill_frontmatter_text(text):
    lines = str(text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    end_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}

    values = {}
    current_key = ""
    for raw_line in lines[1:end_index]:
        if not raw_line.strip():
            if current_key == "description":
                values.setdefault(current_key, []).append("")
            continue

        key_match = _FRONTMATTER_KEY_RE.match(raw_line.strip())
        if key_match:
            key = key_match.group(1).strip().lower()
            raw_value = key_match.group(2).strip()
            current_key = key if key in {"name", "description"} else ""
            if current_key:
                if raw_value in {"|", ">"}:
                    raw_value = ""
                values[current_key] = [raw_value]
            continue

        if current_key == "description":
            values.setdefault(current_key, []).append(raw_line.strip())

    parsed = {}
    for key, pieces in values.items():
        normalized = _normalize_frontmatter_value(" ".join(piece for piece in pieces if piece is not None))
        if normalized:
            parsed[key] = normalized
    return parsed


def parse_skill_frontmatter(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    return _parse_skill_frontmatter_text(text)


def _resolved(path):
    try:
        return Path(path).expanduser().resolve(strict=False)
    except OSError:
        return Path(path).expanduser()


def _is_relative_to(path, root):
    try:
        _resolved(path).relative_to(_resolved(root))
        return True
    except ValueError:
        return False


def _direct_skill_under_root(path, root, identifier):
    try:
        rel = _resolved(path).relative_to(_resolved(root))
    except ValueError:
        return False
    return rel.parts == (identifier, SKILL_FILENAME)


def _default_codex_home():
    return Path.home() / ".codex"


def _codex_home_label(paths):
    return "~/.codex" if _resolved(paths.codex_home) == _resolved(_default_codex_home()) else "$CODEX_HOME"


def _path_label(path, root, root_label):
    try:
        rel = _resolved(path).relative_to(_resolved(root))
        return "{}/{}".format(root_label.rstrip("/"), rel.as_posix())
    except ValueError:
        return _safe_manifest_label(str(path))


def _safe_manifest_label(raw_path):
    text = str(raw_path or "").strip().strip("\"'")
    if not text:
        return ""
    text = text.replace("\\ ", " ")
    home = _resolved(Path.home())

    try:
        path = Path(text).expanduser()
    except (OSError, RuntimeError):
        path = None

    if path is not None and path.is_absolute():
        resolved = _resolved(path)
        try:
            rel = resolved.relative_to(home)
            return "~/{}".format(rel.as_posix())
        except ValueError:
            normalized = resolved.as_posix()
    else:
        normalized = text.replace("\\", "/")

    agents_match = re.search(r"(?:^|/)\.agents/skills/([^/]+)/SKILL\.md$", normalized)
    if agents_match:
        return ".../.agents/skills/{}/SKILL.md".format(agents_match.group(1))
    skills_match = re.search(r"(?:^|/)skills/([^/]+)/SKILL\.md$", normalized)
    if skills_match:
        prefix = "" if normalized.startswith("skills/") else ".../"
        return "{}skills/{}/SKILL.md".format(prefix, skills_match.group(1))
    return Path(normalized).name or normalized


def _asset_row(kind, identifier, name="", description="", source_root="", manifest_path="", manifest_abspath=""):
    identifier = _safe_identifier(identifier)
    if not identifier:
        return None
    manifest_abspath_text = ""
    if manifest_abspath:
        manifest_abspath_text = _resolved(manifest_abspath).as_posix()
    return {
        "asset_key": asset_key(kind, identifier),
        "kind": kind,
        "identifier": identifier,
        "name": _redact_display_text(name or identifier),
        "description": _redact_display_text(description or ""),
        "source_root": _redact_display_text(source_root or ""),
        "manifest_path": _redact_display_text(manifest_path or ""),
        "manifest_abspath": manifest_abspath_text,
    }


def _skill_asset_row(kind, identifier, manifest_path, source_root, manifest_label):
    frontmatter = parse_skill_frontmatter(manifest_path)
    return _asset_row(
        kind,
        identifier,
        name=frontmatter.get("name") or identifier,
        description=frontmatter.get("description", ""),
        source_root=source_root,
        manifest_path=manifest_label,
        manifest_abspath=manifest_path,
    )


def _iter_dir_entries(root):
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                yield entry
    except OSError:
        return


def _discover_skill_dir(root, kind, source_root):
    assets = []
    for entry in _iter_dir_entries(root):
        identifier = _safe_identifier(entry.name)
        if not identifier:
            continue
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        manifest = Path(entry.path) / SKILL_FILENAME
        try:
            if not manifest.is_file():
                continue
        except OSError:
            continue
        row = _skill_asset_row(
            kind,
            identifier,
            manifest,
            source_root,
            _path_label(manifest, root, source_root),
        )
        if row:
            assets.append(row)
    return assets


def _discover_codex_prompts(paths):
    root = paths.codex_home / "prompts"
    source_root = "{}/prompts".format(_codex_home_label(paths))
    assets = []
    for entry in _iter_dir_entries(root):
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        path = Path(entry.path)
        if path.suffix != ".md":
            continue
        identifier = _safe_identifier(path.stem)
        if not identifier:
            continue
        row = _asset_row(
            "codex_prompt",
            identifier,
            name=identifier,
            source_root=source_root,
            manifest_path=_path_label(path, root, source_root),
            manifest_abspath=path,
        )
        if row:
            assets.append(row)
    return assets


def _discover_codex_rules(paths):
    root = paths.codex_home / "rules"
    source_root = "{}/rules".format(_codex_home_label(paths))
    assets = []
    for entry in _iter_dir_entries(root):
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        path = Path(entry.path)
        if path.suffix != ".rules":
            continue
        identifier = _safe_identifier(path.stem)
        if not identifier:
            continue
        row = _asset_row(
            "codex_rule",
            identifier,
            name=identifier,
            source_root=source_root,
            manifest_path=_path_label(path, root, source_root),
            manifest_abspath=path,
        )
        if row:
            assets.append(row)
    return assets


def _iter_claude_plugins(payload):
    if not isinstance(payload, dict):
        return []
    plugins = payload.get("plugins", payload)
    rows = []
    if isinstance(plugins, dict):
        for plugin_id, value in plugins.items():
            rows.append((plugin_id, value if isinstance(value, dict) else {}))
    elif isinstance(plugins, list):
        for value in plugins:
            if not isinstance(value, dict):
                continue
            plugin_id = value.get("id") or value.get("name") or value.get("plugin_id")
            rows.append((plugin_id, value))
    return rows


def _discover_claude_plugins():
    root = Path.home() / ".claude" / "plugins"
    manifest = root / "installed_plugins.json"
    payload = _safe_json_load(manifest)
    assets = []
    for plugin_id, value in _iter_claude_plugins(payload):
        identifier = _safe_identifier(plugin_id)
        if not identifier:
            continue
        row = _asset_row(
            "claude_plugin",
            identifier,
            name=value.get("name") or identifier,
            description=value.get("description", ""),
            source_root="~/.claude/plugins",
            manifest_path=_path_label(manifest, root, "~/.claude/plugins"),
            manifest_abspath=manifest,
        )
        if row:
            assets.append(row)
    return assets


def _launch_agents_source_root(paths):
    default_root = Path.home() / "Library" / "LaunchAgents"
    return "~/Library/LaunchAgents" if _resolved(paths.launch_agents_dir) == _resolved(default_root) else "LaunchAgents"


def _discover_launch_agents(paths):
    if sys.platform != "darwin":
        return []
    root = paths.launch_agents_dir
    source_root = _launch_agents_source_root(paths)
    assets = []
    for entry in _iter_dir_entries(root):
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        path = Path(entry.path)
        if path.suffix != ".plist" or not path.name.startswith("com.openrelix."):
            continue
        identifier = _safe_identifier(path.stem)
        if not identifier:
            continue
        row = _asset_row(
            "launch_agent",
            identifier,
            name=identifier,
            source_root=source_root,
            manifest_path=_path_label(path, root, source_root),
            manifest_abspath=path,
        )
        if row:
            assets.append(row)
    return assets


def discover_installed_assets(paths):
    """Discover static assets installed on this machine.

    The returned rows are display-safe and do not include raw absolute paths.
    Missing directories and malformed plugin manifests are treated as empty.
    """
    codex_skill_root = paths.codex_home / "skills"
    claude_skill_root = Path.home() / ".claude" / "skills"
    repo_skill_root = paths.repo_skill_root
    discovered = []
    discovered.extend(
        _discover_skill_dir(
            codex_skill_root,
            "codex_skill",
            "{}/skills".format(_codex_home_label(paths)),
        )
    )
    discovered.extend(_discover_skill_dir(claude_skill_root, "claude_skill", "~/.claude/skills"))
    discovered.extend(_discover_skill_dir(repo_skill_root, "repo_skill", ".agents/skills"))
    discovered.extend(_discover_codex_prompts(paths))
    discovered.extend(_discover_codex_rules(paths))
    discovered.extend(_discover_claude_plugins())
    discovered.extend(_discover_launch_agents(paths))
    return _dedupe_and_sort_assets(discovered)


def _dedupe_and_sort_assets(assets):
    deduped = OrderedDict()
    for asset in assets or []:
        key = asset.get("asset_key") or asset_key(asset.get("kind", ""), asset.get("identifier", ""))
        if not key or ":" not in key or key in deduped:
            continue
        row = dict(asset)
        row["asset_key"] = key
        deduped[key] = row
    return sorted(
        deduped.values(),
        key=lambda row: (_kind_index(row.get("kind", "")), str(row.get("identifier", "")).lower()),
    )


def _coerce_date(today):
    if isinstance(today, datetime):
        return today.date()
    if isinstance(today, date_cls):
        return today
    if isinstance(today, str) and today.strip():
        try:
            return datetime.fromisoformat(today.strip()).date()
        except ValueError:
            pass
    return datetime.now().date()


def _parse_json_line(line):
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _parse_arguments(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _skill_manifest_hits(text):
    hits = []
    for match in _SKILL_MANIFEST_RE.finditer(str(text or "")):
        raw_path = match.group("path").strip().strip("\"'")
        identifier = _safe_identifier(match.group("name"))
        if raw_path and identifier:
            hits.append((raw_path, identifier))
    return hits


def _absolute_path_from_raw(raw_path):
    text = str(raw_path or "").strip()
    if text.startswith("~/") or text.startswith("/"):
        return Path(text).expanduser()
    return None


def classify_skill_manifest_path(raw_path, paths):
    """Return (kind, identifier) for a SKILL.md path, or None if unsupported."""
    hits = _skill_manifest_hits(raw_path)
    if hits:
        path_text, identifier = hits[0]
    else:
        path_text = str(raw_path or "").strip().strip("\"'")
        match = re.search(r"(?:^|/)(?:\.agents/)?skills/([^/]+)/SKILL\.md$", path_text)
        if not match:
            return None
        identifier = _safe_identifier(match.group(1))
    if not _is_expected_identifier(identifier):
        return None

    absolute_path = _absolute_path_from_raw(path_text)
    if absolute_path is not None:
        codex_skill_root = paths.codex_home / "skills"
        claude_skill_root = Path.home() / ".claude" / "skills"
        repo_skill_root = paths.repo_skill_root
        if _direct_skill_under_root(absolute_path, codex_skill_root, identifier):
            return ("codex_skill", identifier)
        if _direct_skill_under_root(absolute_path, claude_skill_root, identifier):
            return ("claude_skill", identifier)
        if _direct_skill_under_root(absolute_path, repo_skill_root, identifier):
            return ("repo_skill", identifier)

    normalized = path_text.replace("\\", "/")
    if re.search(r"(?:^|/)\.agents/skills/{}/SKILL\.md$".format(re.escape(identifier)), normalized):
        return ("external_repo_skill", identifier)
    if re.search(r"(?:^|/)skills/{}/SKILL\.md$".format(re.escape(identifier)), normalized):
        return ("project_skill", identifier)
    return None


def _organic_source_root(kind):
    if kind == "external_repo_skill":
        return ".../.agents/skills"
    if kind == "project_skill":
        return ".../skills"
    return ""


def _organic_asset_from_manifest(kind, identifier, raw_path, cache):
    cache_key = (kind, identifier)
    if cache_key not in cache:
        frontmatter = {}
        path = _absolute_path_from_raw(raw_path)
        if path is not None:
            frontmatter = parse_skill_frontmatter(path)
        cache[cache_key] = frontmatter
    frontmatter = cache.get(cache_key, {})
    return _asset_row(
        kind,
        identifier,
        name=frontmatter.get("name") or identifier,
        description=frontmatter.get("description", ""),
        source_root=_organic_source_root(kind),
        manifest_path=_safe_manifest_label(raw_path),
        manifest_abspath=path if path is not None else "",
    )


def _hit_key_and_row(raw_path, paths):
    classified = classify_skill_manifest_path(raw_path, paths)
    if not classified:
        return None
    kind, identifier = classified
    return {
        "asset_key": asset_key(kind, identifier),
        "kind": kind,
        "identifier": identifier,
        "raw_path": raw_path,
    }


def _scan_codex_session(session_path, paths):
    hits = OrderedDict()
    try:
        payload = Path(session_path).read_bytes()
    except OSError:
        return OrderedDict()
    cmd_markers = (b'"cmd"', b'\\"cmd\\"')
    if (
        b"SKILL.md" not in payload
        or b"exec_command" not in payload
        or not any(marker in payload for marker in cmd_markers)
    ):
        return hits
    for raw_line in payload.splitlines():
        if (
            b"SKILL.md" not in raw_line
            or b"exec_command" not in raw_line
            or not any(marker in raw_line for marker in cmd_markers)
        ):
            continue
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for hit in _hits_from_codex_json_line(line, paths):
            if hit and hit["asset_key"] not in hits:
                hits[hit["asset_key"]] = hit
    return hits


def _parse_local_timestamp_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.date()


def _scan_claude_session(session_path, paths, mtime_date):
    hits = OrderedDict()
    session_date = None
    try:
        payload = Path(session_path).read_bytes()
    except OSError:
        return (None, OrderedDict())
    if b"SKILL.md" not in payload or b"Read" not in payload:
        return (None, OrderedDict())
    for raw_line in payload.splitlines():
        if session_date is None:
            try:
                first_line = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                first_line = ""
            maybe_obj = _parse_json_line(first_line)
            if isinstance(maybe_obj, dict):
                session_date = _parse_local_timestamp_date(maybe_obj.get("timestamp"))

        if b"SKILL.md" not in raw_line or b"Read" not in raw_line:
            continue
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            continue
        obj = _parse_json_line(line)
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "tool_use" or item.get("name") != "Read":
                continue
            input_payload = item.get("input")
            file_path = input_payload.get("file_path") if isinstance(input_payload, dict) else ""
            for raw_path, _identifier in _skill_manifest_hits(file_path):
                hit = _hit_key_and_row(raw_path, paths)
                if hit and hit["asset_key"] not in hits:
                    hits[hit["asset_key"]] = hit
    return (session_date or mtime_date, hits)


def _iter_codex_sessions_for_date(paths, session_date):
    root = paths.codex_home / "sessions" / session_date.strftime("%Y") / session_date.strftime("%m") / session_date.strftime("%d")
    for entry in _iter_dir_entries(root):
        if not entry.name.startswith("rollout-") or not entry.name.endswith(".jsonl"):
            continue
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        yield Path(entry.path)


def _codex_session_date_from_path(path, paths):
    try:
        rel = _resolved(path).relative_to(_resolved(paths.codex_home / "sessions"))
    except ValueError:
        return None
    if len(rel.parts) < 4:
        return None
    try:
        return date_cls(int(rel.parts[0]), int(rel.parts[1]), int(rel.parts[2]))
    except ValueError:
        return None


def _iter_codex_session_month_roots(paths, start, anchor):
    roots = []
    month_index = start.year * 12 + start.month - 1
    end_index = anchor.year * 12 + anchor.month - 1
    while month_index <= end_index:
        year = month_index // 12
        month = month_index % 12 + 1
        root = paths.codex_home / "sessions" / "{:04d}".format(year) / "{:02d}".format(month)
        if root.is_dir():
            roots.append(root)
        month_index += 1
    return roots


def _hits_from_codex_json_line(line, paths):
    obj = _parse_json_line(line)
    if not isinstance(obj, dict) or obj.get("type") != "response_item":
        return []
    payload_obj = obj.get("payload")
    if not isinstance(payload_obj, dict):
        return []
    if payload_obj.get("type") != "function_call" or payload_obj.get("name") not in _EXEC_COMMAND_NAMES:
        return []
    args = _parse_arguments(payload_obj.get("arguments"))
    cmd = args.get("cmd", "")
    if not isinstance(cmd, str) or "SKILL.md" not in cmd:
        return []
    hits = []
    for raw_path, _identifier in _skill_manifest_hits(cmd):
        hit = _hit_key_and_row(raw_path, paths)
        if hit:
            hits.append(hit)
    return hits


def _scan_codex_sessions_with_rg(paths, lookback_start, anchor):
    if not shutil.which("rg"):
        return None
    roots = _iter_codex_session_month_roots(paths, lookback_start, anchor)
    if not roots:
        return []
    command = [
        "rg",
        "--json",
        "--glob",
        "rollout-*.jsonl",
        "-e",
        _CODEX_SKILL_COMMAND_RG_PATTERN,
    ]
    command.extend(str(root) for root in roots)
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode not in {0, 1}:
        return None

    sessions = OrderedDict()
    for line in result.stdout.splitlines():
        match_obj = _parse_json_line(line)
        if not isinstance(match_obj, dict) or match_obj.get("type") != "match":
            continue
        data = match_obj.get("data")
        if not isinstance(data, dict):
            continue
        path_payload = data.get("path")
        path_text = path_payload.get("text") if isinstance(path_payload, dict) else ""
        if not path_text:
            continue
        session_path = Path(path_text)
        session_date = _codex_session_date_from_path(session_path, paths)
        if not session_date or session_date < lookback_start or session_date > anchor:
            continue
        lines_payload = data.get("lines")
        json_line = lines_payload.get("text") if isinstance(lines_payload, dict) else ""
        if not json_line:
            continue
        session_hits = sessions.setdefault(session_path, (session_date, OrderedDict()))[1]
        for hit in _hits_from_codex_json_line(json_line, paths):
            if hit["asset_key"] not in session_hits:
                session_hits[hit["asset_key"]] = hit
    return list(sessions.values())


def _iter_claude_session_files(anchor, lookback_start=None):
    projects_root = Path.home() / ".claude" / "projects"
    if lookback_start is None:
        lookback_start = anchor - timedelta(days=29)
    for project_entry in _iter_dir_entries(projects_root):
        try:
            if not project_entry.is_dir():
                continue
        except OSError:
            continue
        for entry in _iter_dir_entries(Path(project_entry.path)):
            if not entry.name.endswith(".jsonl"):
                continue
            try:
                if not entry.is_file():
                    continue
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            mtime_date = datetime.fromtimestamp(mtime).date()
            if mtime_date < lookback_start:
                continue
            yield Path(entry.path), mtime_date


def _record_activation(date_value, hits, assets_by_key, frequency_by_key, frontmatter_cache, anchor):
    if not date_value or date_value > anchor or date_value < anchor - timedelta(days=29):
        return
    seven_day_start = anchor - timedelta(days=6)
    date_text = date_value.isoformat()
    for key, hit in hits.items():
        if key not in assets_by_key:
            if hit.get("kind") not in ORGANIC_SKILL_KINDS:
                continue
            row = _organic_asset_from_manifest(
                hit["kind"],
                hit["identifier"],
                hit["raw_path"],
                frontmatter_cache,
            )
            if not row:
                continue
            assets_by_key[key] = row
        stats = frequency_by_key.setdefault(key, zero_frequency())
        stats["windows_30d"] += 1
        if date_value >= seven_day_start:
            stats["windows_7d"] += 1
        if not stats.get("last_seen") or date_text > stats["last_seen"]:
            stats["last_seen"] = date_text


def _month_start(anchor, month_offset=0):
    month_index = anchor.year * 12 + anchor.month - 1 - int(month_offset or 0)
    year = month_index // 12
    month = month_index % 12 + 1
    return date_cls(year, month, 1)


def _recent_month_labels(anchor, months):
    month_count = max(int(months or 0), 0)
    if month_count <= 0:
        return []
    return [_month_start(anchor, offset).strftime("%Y-%m") for offset in reversed(range(month_count))]


def _record_monthly_activity(date_value, hits, monthly_activity):
    if not date_value:
        return
    month_label = date_value.strftime("%Y-%m")
    if month_label not in monthly_activity:
        return
    for hit in hits.values():
        if _high_level_type(hit.get("kind")) == "skill":
            identifier = _safe_identifier(hit.get("identifier", ""))
            if identifier:
                monthly_activity[month_label].add(identifier)


def compute_activation_snapshot(paths, installed, today, monthly_months=6):
    """Return discovered assets, 30-day frequency, and optional monthly activity.

    Frequency semantics remain the same as compute_activations_and_extend:
    windows_7d/windows_30d count deduped sessions within the last 7/30 days.
    monthly_activity is a separate six-month view of distinct active skill
    identifiers, collapsed across skill sub-kinds.
    """
    anchor = _coerce_date(today)
    month_labels = _recent_month_labels(anchor, monthly_months)
    monthly_activity = OrderedDict((label, set()) for label in month_labels)
    if month_labels:
        lookback_start = _month_start(anchor, max(int(monthly_months or 0) - 1, 0))
    else:
        lookback_start = anchor - timedelta(days=29)

    assets_by_key = OrderedDict()
    frequency_by_key = {}
    for asset in _dedupe_and_sort_assets(installed):
        key = asset.get("asset_key")
        assets_by_key[key] = dict(asset)
        frequency_by_key[key] = zero_frequency()

    frontmatter_cache = {}
    lookback_days = max((anchor - lookback_start).days + 1, 30)
    rg_codex_sessions = _scan_codex_sessions_with_rg(paths, lookback_start, anchor)
    if rg_codex_sessions is not None:
        for session_date, hits in rg_codex_sessions:
            _record_monthly_activity(session_date, hits, monthly_activity)
            _record_activation(session_date, hits, assets_by_key, frequency_by_key, frontmatter_cache, anchor)
    else:
        for offset in range(lookback_days):
            session_date = anchor - timedelta(days=offset)
            if session_date < lookback_start:
                continue
            for session_path in _iter_codex_sessions_for_date(paths, session_date):
                hits = _scan_codex_session(session_path, paths)
                _record_monthly_activity(session_date, hits, monthly_activity)
                _record_activation(session_date, hits, assets_by_key, frequency_by_key, frontmatter_cache, anchor)

    for session_path, mtime_date in _iter_claude_session_files(anchor, lookback_start=lookback_start):
        session_date, hits = _scan_claude_session(session_path, paths, mtime_date)
        _record_monthly_activity(session_date, hits, monthly_activity)
        _record_activation(session_date, hits, assets_by_key, frequency_by_key, frontmatter_cache, anchor)

    for key in assets_by_key:
        frequency_by_key.setdefault(key, zero_frequency())
    monthly_rows = [
        {
            "label": label,
            "label_en": label,
            "value": len(monthly_activity.get(label, set())),
        }
        for label in month_labels
    ]
    return {
        "assets": _dedupe_and_sort_assets(assets_by_key.values()),
        "frequency_by_key": frequency_by_key,
        "monthly_activity": monthly_rows,
    }


def compute_activations_and_extend(paths, installed, today):
    """Return discovered assets plus real SKILL.md activation frequencies.

    The returned frequency keys are named windows_7d/windows_30d for backward
    compatibility with the panel, but each count is a deduped session count.
    """
    snapshot = compute_activation_snapshot(paths, installed, today, monthly_months=0)
    return (snapshot["assets"], snapshot["frequency_by_key"])


def filter_renderable_assets(assets, frequency_by_key):
    visible = []
    for asset in assets or []:
        kind = asset.get("kind", "")
        key = asset.get("asset_key", "")
        stats = frequency_by_key.get(key, {})
        if kind in NOISE_GATED_KINDS and int(stats.get("windows_30d") or 0) < 2:
            continue
        visible.append(asset)
    return _dedupe_and_sort_assets(visible)


def _stats_for_asset(asset, frequency_by_key):
    stats = dict(frequency_by_key.get(asset.get("asset_key", ""), {}) or {})
    return {
        "windows_7d": int(stats.get("windows_7d") or 0),
        "windows_30d": int(stats.get("windows_30d") or 0),
        "last_seen": stats.get("last_seen") or "",
    }


def _source_tag_for_asset(asset):
    kind = asset.get("kind", "")
    if kind == "repo_skill":
        return ("<repo>/.agents/skills", "<repo>/.agents/skills")
    if kind == "external_repo_skill":
        return ("跨仓库", "External repo")
    if kind == "project_skill":
        return ("项目本地", "Project-local")
    label = str(asset.get("source_root", "") or "").strip()
    if not label:
        label = str(asset.get("manifest_path", "") or "").strip()
    label = label or kind
    return (label, label)


def _skill_source_row(asset, frequency_by_key):
    stats = _stats_for_asset(asset, frequency_by_key)
    label, label_en = _source_tag_for_asset(asset)
    row = dict(asset)
    row.update(stats)
    row["source_label"] = label
    row["source_label_en"] = label_en
    return row


def _source_sort_key(source):
    return (
        int(source.get("windows_30d") or 0),
        str(source.get("last_seen") or ""),
        int(source.get("windows_7d") or 0),
        -_kind_index(source.get("kind", "")),
    )


def _best_source(sources):
    if not sources:
        return {}
    return sorted(sources, key=_source_sort_key, reverse=True)[0]


def _best_openable_source(sources):
    openable = [source for source in sources or [] if source.get("manifest_abspath")]
    return _best_source(openable)


def _latest_seen(sources):
    values = [str(source.get("last_seen") or "") for source in sources if source.get("last_seen")]
    return max(values) if values else ""


def _merge_source_labels(sources):
    labels = []
    for source in sorted(sources, key=lambda item: (_kind_index(item.get("kind", "")), item.get("source_label", ""))):
        label = source.get("source_label", "")
        label_en = source.get("source_label_en", "") or label
        key = (label, label_en)
        if label and key not in labels:
            labels.append(key)
    return [{"label": label, "label_en": label_en} for label, label_en in labels]


def aggregate_renderable_assets(assets, frequency_by_key):
    """Collapse discovered rows to high-level render rows.

    Skills aggregate by identifier across all skill sub-kinds. Other high-level
    types keep one row per discovered asset while using the five-way type label.
    """
    frequency_by_key = frequency_by_key or {}
    skill_groups = OrderedDict()
    rows = []

    for asset in _dedupe_and_sort_assets(assets):
        high_type = _high_level_type(asset.get("kind", ""))
        if not high_type:
            continue
        if high_type == "skill":
            identifier = _safe_identifier(asset.get("identifier", ""))
            if not identifier:
                continue
            skill_groups.setdefault(identifier, []).append(_skill_source_row(asset, frequency_by_key))
            continue

        stats = _stats_for_asset(asset, frequency_by_key)
        rows.append(
            {
                "asset_key": "{}:{}".format(high_type, asset.get("identifier", "")),
                "type": high_type,
                "kind": asset.get("kind", ""),
                "identifier": asset.get("identifier", ""),
                "name": asset.get("name") or asset.get("identifier", ""),
                "description": asset.get("description", ""),
                "windows_7d": stats["windows_7d"],
                "windows_30d": stats["windows_30d"],
                "last_seen": stats["last_seen"],
                "click_target": "",
                "sources": [],
                "source_labels": [],
                "is_manual": False,
            }
        )

    for identifier, sources in skill_groups.items():
        best = _best_source(sources)
        openable = _best_openable_source(sources)
        description_source = best if best.get("description") else next(
            (source for source in sorted(sources, key=_source_sort_key, reverse=True) if source.get("description")),
            best,
        )
        rows.append(
            {
                "asset_key": "skill:{}".format(identifier),
                "type": "skill",
                "kind": "skill",
                "identifier": identifier,
                "name": best.get("name") or identifier,
                "description": description_source.get("description", ""),
                "windows_7d": sum(int(source.get("windows_7d") or 0) for source in sources),
                "windows_30d": sum(int(source.get("windows_30d") or 0) for source in sources),
                "last_seen": _latest_seen(sources),
                "click_target": openable.get("manifest_abspath", ""),
                "sources": sources,
                "source_labels": _merge_source_labels(sources),
                "is_manual": False,
            }
        )

    return sorted(
        rows,
        key=lambda row: (
            HIGH_LEVEL_TYPE_ORDER.index(row.get("type", "skill"))
            if row.get("type") in HIGH_LEVEL_TYPE_ORDER
            else len(HIGH_LEVEL_TYPE_ORDER),
            str(row.get("identifier") or row.get("name") or "").lower(),
        ),
    )


def manual_asset_render_row(asset):
    asset_type = _high_level_type(asset.get("type", ""))
    if not asset_type:
        asset_type = "skill"
    identifier = _safe_identifier(asset.get("id", "")) or _safe_identifier(asset.get("title", ""))
    if not identifier:
        identifier = str(asset.get("id") or asset.get("title") or "manual").strip() or "manual"
    name = asset.get("display_title") or asset.get("title") or identifier
    description = (
        asset.get("display_value_note")
        or asset.get("value_note")
        or asset.get("display_notes")
        or asset.get("notes")
        or asset.get("display_source_task")
        or asset.get("source_task")
        or ""
    )
    return {
        "asset_key": "manual:{}:{}".format(asset_type, identifier),
        "type": asset_type,
        "kind": asset.get("type", ""),
        "identifier": identifier,
        "name": name,
        "description": description,
        "windows_7d": 0,
        "windows_30d": 0,
        "last_seen": "",
        "click_target": "",
        "sources": [],
        "source_labels": [{"label": "assets.jsonl", "label_en": "assets.jsonl"}],
        "is_manual": True,
    }


def merge_manual_asset_rows(render_rows, manual_assets):
    rows = list(render_rows or [])
    rows.extend(manual_asset_render_row(asset) for asset in manual_assets or [])
    return rows


def high_level_type_counts(render_rows):
    counts = OrderedDict((asset_type, 0) for asset_type in HIGH_LEVEL_TYPE_ORDER)
    for row in render_rows or []:
        asset_type = _high_level_type(row.get("type", ""))
        if asset_type in counts:
            counts[asset_type] += 1
    return counts


def top_skill_rows(render_rows, limit=10):
    rows = [
        row
        for row in render_rows or []
        if _high_level_type(row.get("type", "")) == "skill"
    ]
    return sorted(
        rows,
        key=lambda row: (
            -int(row.get("windows_30d") or 0),
            str(row.get("identifier") or row.get("name") or "").lower(),
        ),
    )[:limit]


def build_asset_stats_snapshot(paths, target_date, generated_at=None, monthly_months=6, top_limit=10):
    """Build a single-date asset statistics snapshot without writing state."""
    anchor = _coerce_date(target_date)
    installed_assets = discover_installed_assets(paths)
    activation_snapshot = compute_activation_snapshot(
        paths,
        installed_assets,
        anchor,
        monthly_months=monthly_months,
    )
    all_assets = activation_snapshot["assets"]
    frequency_by_key = activation_snapshot["frequency_by_key"]
    renderable_assets = filter_renderable_assets(all_assets, frequency_by_key)
    render_rows = aggregate_renderable_assets(renderable_assets, frequency_by_key)
    skill_rows = [row for row in render_rows if _high_level_type(row.get("type", "")) == "skill"]
    type_counts = high_level_type_counts(render_rows)
    generated_at = generated_at or datetime.now().astimezone().isoformat(timespec="seconds")

    return {
        "schema_version": 1,
        "date": anchor.isoformat(),
        "generated_at": str(generated_at),
        "lookback": {
            "windows_7d_start": (anchor - timedelta(days=6)).isoformat(),
            "windows_30d_start": (anchor - timedelta(days=29)).isoformat(),
            "monthly_months": int(monthly_months or 0),
        },
        "summary": {
            "installed_assets": len(installed_assets),
            "all_discovered_assets": len(all_assets),
            "renderable_assets": len(renderable_assets),
            "display_assets": len(render_rows),
            "active_skills_7d": sum(1 for row in skill_rows if int(row.get("windows_7d") or 0) > 0),
            "active_skills_30d": sum(1 for row in skill_rows if int(row.get("windows_30d") or 0) > 0),
            "skill_sessions_7d": sum(int(row.get("windows_7d") or 0) for row in skill_rows),
            "skill_sessions_30d": sum(int(row.get("windows_30d") or 0) for row in skill_rows),
        },
        "type_counts": [
            {
                "type": asset_type,
                "value": type_counts.get(asset_type, 0),
            }
            for asset_type in HIGH_LEVEL_TYPE_ORDER
            if type_counts.get(asset_type, 0) > 0
        ],
        "monthly_activity": activation_snapshot["monthly_activity"],
        "top_skills": top_skill_rows(render_rows, limit=top_limit),
    }
