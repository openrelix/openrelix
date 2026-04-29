#!/usr/bin/env python3
"""Personal-info safeguard for OpenRelix.

Scans all tracked + untracked-not-ignored files in the repo for patterns that
indicate accidental personal-project leakage (machine-specific absolute paths,
hardcoded credentials, or contributor-defined denylist hits).

Exits 0 if the working tree is clean, 1 otherwise. Prints each hit with
file:line and a redacted label only. Do not echo the matched text: this check
often runs in CI/pre-commit logs, and the match itself can be private.

The check has two layers:

1. Built-in regexes target shapes that should never appear in tracked source:
   /Users/<name>/, /home/<name>/, hardcoded credential markers, and personal
   email tails (@gmail.com etc.). These patterns intentionally target *shape*,
   not topic words, so they won't false-positive on docs that mention "API key"
   as a concept.

2. A per-contributor denylist of regex patterns can live at
   <state_root>/personal_denylist.txt (or override via the
   OPENRELIX_PERSONAL_DENYLIST env var). One regex per line, blank lines and
   "#"-prefixed comments ignored. This file lives outside the repo so each
   contributor's private project names stay private.

Wired into .github/workflows/publish.yml as a release gate; can also be
installed as a local pre-commit hook via scripts/git-hooks/pre-commit.

Run manually:
    python3 scripts/check_personal_info.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_LOCAL_STATE_DIRS = {
    "raw",
    "consolidated",
    "registry",
    "reviews",
    "reports",
    "runtime",
    "log",
}

BUILT_IN_PATTERNS = [
    (r"/Users/[A-Za-z][A-Za-z0-9._-]{1,30}/", "user-home absolute path"),
    (r"/home/[A-Za-z][A-Za-z0-9._-]{1,30}/", "user-home absolute path"),
    (
        r"(?i)(?<![A-Za-z0-9_-])(api[_-]?key|secret|token|access[_-]?key|password|bearer)['\"]?\s*[:=]\s*['\"][A-Za-z0-9_\-+/=]{20,}['\"]",
        "hardcoded credential",
    ),
    (
        r"(?<![A-Za-z0-9._-])[A-Za-z0-9._-]+@(?:gmail|qq|163|outlook|hotmail|yahoo)\.com\b",
        "personal email",
    ),
]

SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
    ".woff", ".woff2", ".ttf", ".otf",
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".tar",
}

# Files that are exempt because they exist *to* describe these patterns.
SELF_REFERENTIAL_PATHS = {
    "scripts/check_personal_info.py",
    "scripts/git-hooks/pre-commit",
}


def state_root_path():
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from asset_runtime import default_state_root  # noqa: WPS433 (dynamic import is intentional)
    except Exception:
        return None
    try:
        return Path(default_state_root())
    except Exception:
        return None


def load_user_denylist():
    candidates = []
    explicit = os.environ.get("OPENRELIX_PERSONAL_DENYLIST")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    state_root = state_root_path()
    if state_root is not None:
        candidates.append(state_root / "personal_denylist.txt")

    seen = set()
    patterns = []
    for path in candidates:
        if not path:
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        for raw in resolved.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append((line, "personal denylist entry"))
    return patterns


def repo_local_state_dirs():
    hits = []
    for name in sorted(REPO_LOCAL_STATE_DIRS):
        path = ROOT / name
        if not path.exists():
            continue
        hits.append(name)
    return hits


def list_repo_files():
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    files = []
    for rel in sorted(set(tracked + untracked)):
        if not rel or rel in SELF_REFERENTIAL_PATHS:
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        files.append((rel, path))
    return files


def scan(files, patterns):
    hits = []
    compiled = []
    for raw_pattern, label in patterns:
        try:
            compiled.append((re.compile(raw_pattern), label, raw_pattern))
        except re.error as exc:
            print(
                "personal-info-check: invalid regex skipped for [{}]: {}".format(label, exc),
                file=sys.stderr,
            )
    for rel, path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for compiled_re, label, raw in compiled:
            for match in compiled_re.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                hits.append((rel, line_no, label, raw))
    return hits


def main():
    patterns = list(BUILT_IN_PATTERNS) + load_user_denylist()
    files = list_repo_files()
    hits = scan(files, patterns)
    state_dirs = repo_local_state_dirs()
    if hits or state_dirs:
        if state_dirs:
            print("personal-info-check: FAIL — repo-local runtime state directory found")
            for name in state_dirs:
                print("  {}/  [repo-local runtime state]".format(name))
            print()
        if hits:
            print("personal-info-check: FAIL — {} hit(s) found".format(len(hits)))
            for rel, line, label, _raw in hits:
                print("  {}:{}  [{}]".format(rel, line, label))
            print()
        print("Refusing to proceed. Keep runtime data in the external state root,")
        print("or update the external personal denylist if a pattern is too aggressive.")
        return 1
    print("personal-info-check: clean ({} files, {} patterns)".format(len(files), len(patterns)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
