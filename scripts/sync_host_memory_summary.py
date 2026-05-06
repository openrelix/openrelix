#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from asset_runtime import (
    atomic_write_text,
    ensure_state_layout,
    get_host_context_targets,
    get_memory_mode,
    get_runtime_paths,
)
from build_codex_memory_summary import build_project_context_filter


PATHS = get_runtime_paths()
BUILD_CODEX_MEMORY_SUMMARY = PATHS.repo_root / "scripts" / "build_codex_memory_summary.py"
MANAGED_START = "<!-- openrelix:shared-memory:start -->"
MANAGED_END = "<!-- openrelix:shared-memory:end -->"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sync one bounded OpenRelix personal memory summary into enabled AI host contexts."
    )
    parser.add_argument(
        "--project-cwd",
        default="",
        help="Compile host context for this project cwd: global memory plus matching project memory.",
    )
    parser.add_argument("--project-key", default="", help="Optional explicit project key for active project matching.")
    parser.add_argument("--project-label", default="", help="Optional active project label for display and matching.")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def safe_project_slug(value):
    text = str(value or "").strip()
    if not text:
        return "project"
    text = text.replace("\\", "/").rstrip("/")
    if "/" in text:
        text = Path(text).name or text
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._")
    return (slug or "project")[:80]


def active_project_payload(project_cwd="", project_key="", project_label=""):
    if not any(str(value or "").strip() for value in (project_cwd, project_key, project_label)):
        return {}
    project_filter = build_project_context_filter(
        project_cwd=project_cwd,
        project_key=project_key,
        project_label=project_label,
    )
    cwd = project_filter.cwd
    label = str(project_label or "").strip() or project_filter.label or (Path(cwd).name if cwd else "")
    key = str(project_key or "").strip() or project_filter.key or safe_project_slug(label or cwd).lower()
    return {
        "cwd": cwd,
        "project_key": key,
        "project_label": label,
        "slug": safe_project_slug(key or label or cwd),
    }


def run_summary_builder(cmd):
    result = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode == 0:
        return
    if result.stdout.strip():
        print(result.stdout.strip(), file=sys.stderr)
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout, stderr=result.stderr)


def build_shared_summary(project_cwd="", project_key="", project_label=""):
    active_project = active_project_payload(project_cwd, project_key, project_label)
    if active_project:
        output_path = PATHS.runtime_dir / "host-context" / "projects" / active_project["slug"] / "memory_summary.md"
    else:
        output_path = PATHS.runtime_dir / "host-context" / "shared-personal-memory-summary.md"
    cmd = [
        sys.executable,
        str(BUILD_CODEX_MEMORY_SUMMARY),
        "--memory-summary",
        str(output_path),
    ]
    if project_cwd:
        cmd.extend(["--project-cwd", str(project_cwd)])
    if project_key:
        cmd.extend(["--project-key", str(project_key)])
    if project_label:
        cmd.extend(["--project-label", str(project_label)])
    run_summary_builder(cmd)
    return output_path, output_path.read_text(encoding="utf-8"), active_project


def codex_memories_feature_state(paths=None):
    paths = paths or PATHS
    config_path = paths.codex_home / "config.toml"
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return "unknown"
    except (OSError, UnicodeDecodeError):
        return "unknown"

    in_features = False
    for raw_line in lines:
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_features = line.strip("[]").strip() == "features"
            continue
        if not in_features:
            continue
        match = re.match(r"^memories\s*=\s*(.+)$", line)
        if not match:
            continue
        value = match.group(1).strip().strip('"').strip("'").lower()
        if value in {"1", "true", "yes", "on"}:
            return "enabled"
        if value in {"0", "false", "no", "off"}:
            return "disabled"
        return "unknown"
    return "unknown"


def sync_codex_summary(summary_text):
    target = PATHS.codex_home / "memories" / "memory_summary.md"
    feature_state = codex_memories_feature_state(PATHS)
    if feature_state == "disabled":
        return {
            "host": "codex",
            "path": str(target),
            "status": "disabled",
            "detail": "codex memories disabled in config.toml",
            "memory_feature": feature_state,
        }
    atomic_write_text(target, summary_text)
    return {
        "host": "codex",
        "path": str(target),
        "status": "synced",
        "memory_feature": feature_state,
    }


def clear_codex_summary():
    target = PATHS.codex_home / "memories" / "memory_summary.md"
    if target.is_symlink() or target.is_file():
        target.unlink()
        return {"host": "codex", "path": str(target), "status": "removed"}
    if target.exists():
        return {"host": "codex", "path": str(target), "status": "skipped", "detail": "not a file"}
    return {"host": "codex", "path": str(target), "status": "missing"}


def managed_claude_block(summary_text):
    body = summary_text.strip()
    return "\n".join(
        [
            MANAGED_START,
            "# OpenRelix Shared Personal Memory",
            "",
            "This block is generated by OpenRelix from the shared local personal-memory registry.",
            "Edit OpenRelix memory sources instead of editing this managed block by hand.",
            "",
            body,
            MANAGED_END,
            "",
        ]
    )


def replace_managed_block(existing_text, block_text):
    if MANAGED_START in existing_text and MANAGED_END in existing_text:
        before, _, tail = existing_text.partition(MANAGED_START)
        _, _, after = tail.partition(MANAGED_END)
        return before.rstrip() + "\n\n" + block_text.rstrip() + "\n\n" + after.lstrip()
    if existing_text.strip():
        return existing_text.rstrip() + "\n\n" + block_text
    return block_text


def strip_managed_block(existing_text):
    if MANAGED_START not in existing_text or MANAGED_END not in existing_text:
        return existing_text, False
    before, _, tail = existing_text.partition(MANAGED_START)
    _, _, after = tail.partition(MANAGED_END)
    updated = "\n\n".join(part.strip() for part in (before, after) if part.strip())
    return (updated + "\n" if updated else ""), True


def sync_claude_summary(summary_text):
    target = PATHS.claude_home / "CLAUDE.md"
    try:
        existing_text = target.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        existing_text = ""
    atomic_write_text(target, replace_managed_block(existing_text, managed_claude_block(summary_text)))
    return {"host": "claude", "path": str(target), "status": "synced"}


def clear_claude_summary():
    target = PATHS.claude_home / "CLAUDE.md"
    try:
        existing_text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"host": "claude", "path": str(target), "status": "missing"}
    except (OSError, UnicodeDecodeError) as exc:
        return {"host": "claude", "path": str(target), "status": "error", "detail": exc.__class__.__name__}

    updated, removed = strip_managed_block(existing_text)
    if not removed:
        return {"host": "claude", "path": str(target), "status": "kept", "detail": "no managed block"}
    if updated.strip():
        atomic_write_text(target, updated)
    else:
        target.unlink()
    return {"host": "claude", "path": str(target), "status": "removed"}


def main():
    args = parse_args()
    ensure_state_layout(PATHS)
    memory_mode = get_memory_mode(PATHS)
    targets = get_host_context_targets(PATHS) if memory_mode == "integrated" else []
    synced = []
    skipped = []
    cleared = []

    summary_path = None
    summary_text = ""
    active_project = active_project_payload(args.project_cwd, args.project_key, args.project_label)
    if targets:
        summary_path, summary_text, active_project = build_shared_summary(
            project_cwd=args.project_cwd,
            project_key=args.project_key,
            project_label=args.project_label,
        )

    if "codex" in targets:
        codex_result = sync_codex_summary(summary_text)
        if codex_result.get("status") == "synced":
            synced.append(codex_result)
        else:
            skipped.append(codex_result)
    else:
        cleared.append(clear_codex_summary())

    if "claude" in targets:
        synced.append(sync_claude_summary(summary_text))
    else:
        cleared.append(clear_claude_summary())

    payload = {
        "ok": True,
        "memory_mode": memory_mode,
        "summary_path": str(summary_path) if summary_path else "",
        "active_project": active_project,
        "targets": targets,
        "synced": synced,
        "skipped": skipped,
        "cleared": cleared,
    }
    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
