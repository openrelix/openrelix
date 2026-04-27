#!/usr/bin/env python3

import argparse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--set", action="append", default=[])
    return parser.parse_args()


def parse_replacements(items):
    mapping = {}
    for item in items:
        key, separator, value = item.partition("=")
        if not separator:
            raise SystemExit("replacement must look like KEY=VALUE: {}".format(item))
        mapping[key] = value
    return mapping


def main():
    args = parse_args()
    template_path = Path(args.template).expanduser()
    output_path = Path(args.output).expanduser()
    replacements = parse_replacements(args.set)

    text = template_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace("__{}__".format(key), value)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
