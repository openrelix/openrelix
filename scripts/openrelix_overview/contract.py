"""Lightweight contract checks for generated overview reports."""

import argparse
import json
from pathlib import Path


SCHEMA_VERSION = 1

REQUIRED_TOP_LEVEL_KEYS = (
    "schema_version",
    "language",
    "generated_at",
    "summary",
    "metrics",
    "mix",
    "assets",
    "reviews",
    "usage_events",
    "summary_terms",
    "summary_term_views",
    "token_usage",
    "window_overview",
    "window_overview_views",
    "memory_registry",
    "nightly_memory_views",
    "codex_native_memory",
    "codex_native_memory_counts",
    "claude_native_memory",
    "claude_native_memory_counts",
)

OVERVIEW_MARKERS = {
    "overview.md": ("OpenRelix",),
    "overview.csv": ("id,title,type",),
    "panel.html": (
        'meta name="openrelix:version"',
        "app-shell",
        "token_usage",
        "memory_registry",
        "window_overview",
    ),
}


def overview_data_path(state_dir):
    return Path(state_dir) / "reports" / "overview-data.json"


def load_overview_data(state_dir):
    path = overview_data_path(state_dir)
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_overview_data(data):
    errors = []
    if not isinstance(data, dict):
        return ["overview-data.json must contain a JSON object"]

    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        errors.append(
            "schema_version must be {}, got {}".format(
                SCHEMA_VERSION,
                repr(version),
            )
        )

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in data:
            errors.append("missing top-level key: {}".format(key))

    language = data.get("language")
    if language not in {"zh", "en"}:
        errors.append("language must be 'zh' or 'en'")

    type_checks = {
        "summary": dict,
        "metrics": list,
        "mix": dict,
        "assets": dict,
        "reviews": list,
        "usage_events": list,
        "summary_terms": list,
        "summary_term_views": list,
        "token_usage": dict,
        "window_overview": (dict, type(None)),
        "window_overview_views": list,
        "memory_registry": list,
        "nightly_memory_views": dict,
        "codex_native_memory": list,
        "codex_native_memory_counts": dict,
        "claude_native_memory": list,
        "claude_native_memory_counts": dict,
    }
    for key, expected_types in type_checks.items():
        if not isinstance(expected_types, tuple):
            expected_types = (expected_types,)
        if key in data and not isinstance(data.get(key), expected_types):
            type_names = " or ".join(expected_type.__name__ for expected_type in expected_types)
            errors.append("{} must be {}".format(key, type_names))

    token_usage = data.get("token_usage")
    if isinstance(token_usage, dict):
        for key in ("available", "daily_rows", "today_breakdown"):
            if key not in token_usage:
                errors.append("token_usage missing key: {}".format(key))

    summary = data.get("summary")
    if isinstance(summary, dict):
        for key in ("total_assets", "active_assets", "daily_window_count"):
            if key not in summary:
                errors.append("summary missing key: {}".format(key))

    return errors


def validate_report_markers(state_dir):
    errors = []
    reports_dir = Path(state_dir) / "reports"
    for file_name, markers in OVERVIEW_MARKERS.items():
        path = reports_dir / file_name
        if not path.is_file():
            errors.append("missing report file: {}".format(path))
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append("could not read {}: {}".format(path, exc))
            continue
        for marker in markers:
            if marker not in content:
                errors.append("{} missing marker: {}".format(file_name, marker))
    return errors


def validate_state_dir(state_dir, check_reports=True):
    state_dir = Path(state_dir)
    errors = []
    path = overview_data_path(state_dir)
    if not path.is_file():
        errors.append("missing overview data: {}".format(path))
        return {"ok": False, "errors": errors}

    try:
        data = load_overview_data(state_dir)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append("could not load {}: {}".format(path, exc))
        return {"ok": False, "errors": errors}

    errors.extend(validate_overview_data(data))
    if check_reports:
        errors.extend(validate_report_markers(state_dir))
    return {"ok": not errors, "errors": errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate generated OpenRelix overview reports.")
    parser.add_argument("--state-dir", required=True, help="OpenRelix state root containing reports/.")
    parser.add_argument(
        "--data-only",
        action="store_true",
        help="Only validate overview-data.json and skip report marker checks.",
    )
    args = parser.parse_args(argv)
    result = validate_state_dir(args.state_dir, check_reports=not args.data_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
