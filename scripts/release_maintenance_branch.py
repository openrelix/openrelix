#!/usr/bin/env python3
"""Resolve release-line maintenance branches for OpenRelix releases."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int


def parse_version(value: str, *, tag: bool = False) -> Version | None:
    text = str(value or "").strip()
    match = (TAG_RE if tag else VERSION_RE).fullmatch(text)
    if not match:
        return None
    return Version(*(int(part) for part in match.groups()))


def maintenance_branch_for_version(version: str) -> str:
    parsed = parse_version(version)
    if not parsed or parsed.patch != 0:
        return ""
    return "bugfix/version_{}_{}".format(parsed.major, parsed.minor)


def release_tags_before(version: str, tags: Iterable[str]) -> list[tuple[Version, str]]:
    target = parse_version(version)
    if not target:
        return []
    candidates: list[tuple[Version, str]] = []
    for tag in tags:
        parsed = parse_version(tag, tag=True)
        if parsed and parsed < target:
            candidates.append((parsed, tag.strip()))
    return sorted(candidates)


def previous_maintenance_branch_for_version(
    version: str,
    tags: Iterable[str],
) -> tuple[str, str]:
    if not maintenance_branch_for_version(version):
        return "", ""
    previous = release_tags_before(version, tags)
    if not previous:
        return "", ""
    parsed, tag = previous[-1]
    return "bugfix/version_{}_{}".format(parsed.major, parsed.minor), tag


def read_package_version(repo_root: Path) -> str:
    package_json = repo_root / "package.json"
    payload = json.loads(package_json.read_text(encoding="utf-8"))
    return str(payload.get("version") or "").strip()


def list_git_tags(repo_root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "tag", "--list"],
        cwd=str(repo_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def write_github_output(path: str, values: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write("{}={}\n".format(key, value))


def resolve_outputs(version: str, tags: Iterable[str]) -> dict[str, str]:
    branch = maintenance_branch_for_version(version)
    previous_branch, previous_tag = previous_maintenance_branch_for_version(version, tags)
    return {
        "should_create": "true" if branch else "false",
        "branch": branch,
        "previous_branch": previous_branch,
        "previous_tag": previous_tag,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve OpenRelix release maintenance branch outputs.",
    )
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--version", default="")
    parser.add_argument("--github-output", default="")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    version = args.version.strip() or read_package_version(repo_root)
    outputs = resolve_outputs(version, list_git_tags(repo_root))
    write_github_output(args.github_output, outputs)
    for key, value in outputs.items():
        print("{}={}".format(key, value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
