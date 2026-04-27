#!/usr/bin/env python3

import argparse
import re
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--enable-memories", action="store_true")
    parser.add_argument("--disable-codex-memories", action="store_true")
    parser.add_argument("--enable-history", action="store_true")
    parser.add_argument("--enable-codex-hooks", action="store_true")
    parser.add_argument("--history-max-bytes", type=int, default=268435456)
    return parser.parse_args()


def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def load_lines(path: Path):
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def section_bounds(lines, section_name):
    header = "[{}]".format(section_name)
    start = None
    end = len(lines)
    for index, line in enumerate(lines):
        if line.strip() == header:
            start = index
            break
    if start is None:
        return None, None
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    return start, end


def upsert_key(lines, section_name, key, value):
    start, end = section_bounds(lines, section_name)
    entry = "{} = {}".format(key, value)
    key_pattern = re.compile(r"^\s*{}\s*=".format(re.escape(key)))

    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[{}]".format(section_name), entry])
        return lines

    for index in range(start + 1, end):
        if key_pattern.match(lines[index]):
            lines[index] = entry
            return lines

    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, entry)
    return lines


def main():
    args = parse_args()
    config_path = Path(args.config).expanduser()
    ensure_parent(config_path)
    lines = load_lines(config_path)

    if args.enable_memories:
        lines = upsert_key(lines, "features", "memories", "true")
    if args.disable_codex_memories:
        lines = upsert_key(lines, "features", "memories", "false")
    if args.enable_codex_hooks:
        lines = upsert_key(lines, "features", "codex_hooks", "true")
    if args.enable_history:
        lines = upsert_key(lines, "history", "persistence", '"save-all"')
        lines = upsert_key(lines, "history", "max_bytes", str(args.history_max_bytes))

    output = "\n".join(lines).rstrip()
    if output:
        output += "\n"
    config_path.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
