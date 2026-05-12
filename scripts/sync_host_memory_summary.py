#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys

from asset_runtime import (
    atomic_write_text,
    ensure_state_layout,
    get_host_context_targets,
    get_memory_mode,
    get_runtime_paths,
)


PATHS = get_runtime_paths()
BUILD_CODEX_MEMORY_SUMMARY = PATHS.repo_root / "scripts" / "build_codex_memory_summary.py"
MANAGED_START = "<!-- openrelix:shared-memory:start -->"
MANAGED_END = "<!-- openrelix:shared-memory:end -->"
LEGACY_CODEX_PROFILE_MARKER = "The injected context is compiled from OpenRelix canonical"
LEGACY_CODEX_REGISTRY_MARKER = "### Local personal memory registry"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sync one bounded OpenRelix personal memory summary into enabled AI host contexts."
    )
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


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


def build_host_context_summary():
    output_path = PATHS.runtime_dir / "host-context" / "memory_summary.md"
    cmd = [
        sys.executable,
        str(BUILD_CODEX_MEMORY_SUMMARY),
        "--memory-summary",
        str(output_path),
    ]
    run_summary_builder(cmd)
    if not output_path.exists():
        return output_path, ""
    return output_path, output_path.read_text(encoding="utf-8")


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


def path_exists_or_symlink(path):
    return path.exists() or path.is_symlink()


def managed_codex_block(summary_text):
    body = summary_text.strip()
    return "\n".join(
        [
            MANAGED_START,
            "# OpenRelix Personal Memory",
            "",
            body,
            MANAGED_END,
            "",
        ]
    )


def is_legacy_openrelix_codex_summary(existing_text):
    if MANAGED_START in existing_text or MANAGED_END in existing_text:
        return False
    text = existing_text.lstrip()
    return (
        text.startswith("## User Profile")
        and LEGACY_CODEX_PROFILE_MARKER in text
        and LEGACY_CODEX_REGISTRY_MARKER in text
    )


def replace_codex_managed_block(existing_text, block_text):
    if is_legacy_openrelix_codex_summary(existing_text):
        return block_text, True
    return replace_managed_block(existing_text, block_text), False


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
    if target.exists() and not (target.is_file() or target.is_symlink()):
        return {
            "host": "codex",
            "path": str(target),
            "status": "skipped",
            "detail": "memory_summary.md path is not a file",
            "memory_feature": feature_state,
        }
    try:
        existing_text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing_text = ""
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "host": "codex",
            "path": str(target),
            "status": "error",
            "detail": exc.__class__.__name__,
            "memory_feature": feature_state,
        }

    updated, migrated = replace_codex_managed_block(existing_text, managed_codex_block(summary_text))
    atomic_write_text(target, updated)
    result = {
        "host": "codex",
        "path": str(target),
        "status": "synced",
        "memory_feature": feature_state,
    }
    if migrated:
        result["detail"] = "migrated legacy OpenRelix full-file summary into managed block"
    return result


def clear_codex_summary():
    target = PATHS.codex_home / "memories" / "memory_summary.md"
    if not path_exists_or_symlink(target):
        return {"host": "codex", "path": str(target), "status": "missing"}
    if target.exists() and not (target.is_file() or target.is_symlink()):
        return {"host": "codex", "path": str(target), "status": "skipped", "detail": "not a file"}
    try:
        existing_text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"host": "codex", "path": str(target), "status": "error", "detail": exc.__class__.__name__}

    if is_legacy_openrelix_codex_summary(existing_text):
        target.unlink()
        return {"host": "codex", "path": str(target), "status": "removed", "detail": "legacy OpenRelix full-file summary"}

    updated, removed = strip_managed_block(existing_text)
    if not removed:
        return {"host": "codex", "path": str(target), "status": "kept", "detail": "no managed block"}
    if updated.strip():
        atomic_write_text(target, updated)
    else:
        target.unlink()
    return {"host": "codex", "path": str(target), "status": "removed"}


def managed_claude_block(summary_text):
    body = summary_text.strip()
    return "\n".join(
        [
            MANAGED_START,
            "# OpenRelix Personal Memory",
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
        parts = [part for part in (before.rstrip(), block_text.rstrip(), after.lstrip()) if part]
        return "\n\n".join(parts) + "\n"
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
    if targets:
        summary_path, summary_text = build_host_context_summary()
    has_summary = bool(summary_text.strip())

    if "codex" in targets:
        if has_summary:
            codex_result = sync_codex_summary(summary_text)
            if codex_result.get("status") == "synced":
                synced.append(codex_result)
            else:
                skipped.append(codex_result)
        else:
            cleared.append(clear_codex_summary())
    else:
        cleared.append(clear_codex_summary())

    if "claude" in targets:
        if has_summary:
            synced.append(sync_claude_summary(summary_text))
        else:
            cleared.append(clear_claude_summary())
    else:
        cleared.append(clear_claude_summary())

    payload = {
        "ok": True,
        "memory_mode": memory_mode,
        "summary_path": str(summary_path) if summary_path else "",
        "summary_kind": "unified",
        "targets": targets,
        "synced": synced,
        "skipped": skipped,
        "cleared": cleared,
    }
    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
