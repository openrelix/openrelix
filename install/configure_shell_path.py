#!/usr/bin/env python3

import argparse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--path-entry", required=True)
    parser.add_argument("--marker", default="openkeepsake")
    return parser.parse_args()


def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def build_block(marker: str, path_entry: str):
    return "\n".join(
        [
            "# >>> {} >>>".format(marker),
            'export PATH="{}:$PATH"'.format(path_entry),
            "# <<< {} <<<".format(marker),
        ]
    )


def upsert_block(text: str, block: str, marker: str):
    start = "# >>> {} >>>".format(marker)
    end = "# <<< {} <<<".format(marker)
    lines = text.splitlines()

    start_index = None
    end_index = None
    for index, line in enumerate(lines):
        if line.strip() == start:
            start_index = index
            break

    if start_index is not None:
        for index in range(start_index + 1, len(lines)):
            if lines[index].strip() == end:
                end_index = index
                break

    if start_index is not None and end_index is not None:
        new_lines = lines[:start_index] + block.splitlines() + lines[end_index + 1 :]
        return "\n".join(new_lines).rstrip() + "\n"

    if text and not text.endswith("\n"):
        text += "\n"
    if text.strip():
        text += "\n"
    return text + block + "\n"


def main():
    args = parse_args()
    config_path = Path(args.config).expanduser()
    ensure_parent(config_path)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    block = build_block(args.marker, str(Path(args.path_entry).expanduser()))
    updated = upsert_block(existing, block, args.marker)
    config_path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
