#!/usr/bin/env python3

import argparse
import filecmp
import os
from pathlib import Path
import shutil
import sys

from asset_runtime import APP_SLUG, LEGACY_STATE_DIR_NAMES, REPO_ROOT, ensure_state_layout


def parse_args():
    parser = argparse.ArgumentParser(
        description="Move ignored legacy runtime state out of the repo and into the external state root."
    )
    parser.add_argument(
        "--state-dir",
        help="Override the destination state root. Defaults to the active external state root.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned moves without modifying files.",
    )
    return parser.parse_args()


def default_external_state_root() -> Path:
    explicit = os.environ.get("AI_ASSET_STATE_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()

    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_SLUG

    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser().resolve() / APP_SLUG

    return home / ".local" / "state" / APP_SLUG


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_empty_directory(path: Path) -> bool:
    return path.is_dir() and not any(path.iterdir())


def files_match(src: Path, dst: Path) -> bool:
    try:
        return filecmp.cmp(src, dst, shallow=False)
    except OSError:
        return False


def next_conflict_path(src: Path, conflict_root: Path) -> Path:
    relative = src.relative_to(REPO_ROOT)
    candidate = conflict_root / relative
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    index = 1
    while True:
        numbered = candidate.with_name(f"{stem}.repo-legacy-{index}{suffix}")
        if not numbered.exists():
            return numbered
        index += 1


def move_tree(src: Path, dst: Path, dry_run: bool, conflict_root: Path) -> None:
    if src.is_dir():
        if dry_run:
            print(f"DIR  {src} -> {dst}")
        else:
            dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            move_tree(child, dst / child.name, dry_run, conflict_root)
        if not dry_run:
            try:
                src.rmdir()
            except OSError:
                pass
        return

    target = dst
    if target.exists():
        if target.is_dir():
            raise RuntimeError(f"Cannot overwrite directory with file: {target}")
        if target.stat().st_size == 0:
            if dry_run:
                print(f"REPLACE empty file {target} with {src}")
            else:
                target.unlink()
        elif files_match(src, target):
            if dry_run:
                print(f"SKIP identical file {src} (already present at {target})")
            else:
                src.unlink()
            return
        else:
            conflict_target = next_conflict_path(src, conflict_root)
            if dry_run:
                print(f"CONFLICT keep {target}; move {src} -> {conflict_target}")
                return
            conflict_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(conflict_target))
            return

    if dry_run:
        print(f"FILE {src} -> {target}")
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(target))


def main():
    args = parse_args()
    destination = (
        Path(args.state_dir).expanduser().resolve()
        if args.state_dir
        else default_external_state_root()
    )

    if destination == REPO_ROOT or path_is_within(destination, REPO_ROOT):
        print(
            "Refusing to migrate into the repo tree. Set --state-dir to an external path or unset legacy overrides.",
            file=sys.stderr,
        )
        return 1

    planned = []
    for name in LEGACY_STATE_DIR_NAMES:
        src = REPO_ROOT / name
        if not src.exists():
            continue
        if src.is_dir() and is_empty_directory(src):
            continue
        planned.append((src, destination / name))

    if not planned:
        print("No legacy repo-local state detected.")
        return 0

    print(f"Legacy repo-local state will be moved to: {destination}")
    conflict_root = destination / "_legacy_conflicts"
    for src, dst in planned:
        move_tree(src, dst, args.dry_run, conflict_root)

    if args.dry_run:
        return 0

    os.environ["AI_ASSET_STATE_DIR"] = str(destination)
    ensure_state_layout()

    print("Migration completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
