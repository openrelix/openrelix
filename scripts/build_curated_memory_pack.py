#!/usr/bin/env python3

"""Build the sidecar curated personal memory pack.

This command intentionally does not sync or modify Codex/Claude host context.
It writes a review artifact under the OpenRelix runtime state root so the
curated-memory layer can be evaluated before it becomes an injection source.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from asset_runtime import atomic_write_json, atomic_write_text, default_state_root
from openrelix_overview.curated_memory import build_curated_memory_pack_from_text, render_markdown


CURATED_PACK_FILE = "curated_memory_pack.json"
CURATED_SUMMARY_FILE = "curated-personal-memory-summary.md"


def parse_args():
    parser = argparse.ArgumentParser(description="Build a non-invasive curated personal memory pack.")
    parser.add_argument(
        "--state-dir",
        help="OpenRelix runtime state root. Defaults to the configured user-level state root.",
    )
    parser.add_argument(
        "--registry",
        help="Explicit JSONL memory registry path. Defaults to registry/memory_entries.jsonl, falling back to memory_items.jsonl.",
    )
    parser.add_argument("--json-output", help="Explicit JSON output path.")
    parser.add_argument("--markdown-output", help="Explicit Markdown output path.")
    parser.add_argument("--print-markdown", action="store_true", help="Print Markdown instead of writing it.")
    return parser.parse_args()


def resolve_state_dir(value):
    return Path(value).expanduser().resolve() if value else default_state_root()


def read_text_if_exists(path):
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def display_path(path, state_dir):
    try:
        return str(path.resolve().relative_to(state_dir.resolve()))
    except ValueError:
        return "external-registry/{}".format(path.expanduser().name)


def default_registry_path(state_dir):
    canonical = state_dir / "registry" / "memory_entries.jsonl"
    canonical_text = read_text_if_exists(canonical).strip()
    if canonical_text:
        return canonical, canonical_text + "\n"
    legacy = state_dir / "registry" / "memory_items.jsonl"
    legacy_text = read_text_if_exists(legacy).strip()
    return legacy, (legacy_text + "\n" if legacy_text else "")


def main():
    args = parse_args()
    state_dir = resolve_state_dir(args.state_dir)
    if args.registry:
        registry_path = Path(args.registry).expanduser().resolve()
        registry_text = read_text_if_exists(registry_path)
    else:
        registry_path, registry_text = default_registry_path(state_dir)

    source_label = display_path(registry_path, state_dir)
    pack = build_curated_memory_pack_from_text(registry_text, source_label=source_label)
    markdown = render_markdown(pack)

    json_output = Path(args.json_output).expanduser() if args.json_output else state_dir / "registry" / CURATED_PACK_FILE
    markdown_output = (
        Path(args.markdown_output).expanduser()
        if args.markdown_output
        else state_dir / "runtime" / "host-context" / CURATED_SUMMARY_FILE
    )

    atomic_write_json(json_output, pack)
    if args.print_markdown:
        print(markdown, end="")
    else:
        atomic_write_text(markdown_output, markdown)

    print(
        "curated memory pack entries={} json={} markdown={}".format(
            pack.get("entry_count", 0),
            json_output,
            "stdout" if args.print_markdown else markdown_output,
        )
    )


if __name__ == "__main__":
    main()
