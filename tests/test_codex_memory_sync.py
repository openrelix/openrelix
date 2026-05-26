#!/usr/bin/env python3

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asset_runtime  # noqa: E402
import codex_memory_sync  # noqa: E402
from openrelix_overview import codex_profiles  # noqa: E402


def runtime_paths_for_state(state_root, codex_home):
    base = asset_runtime.get_runtime_paths()
    state_root = Path(state_root)
    return replace(
        base,
        state_root=state_root,
        codex_home=Path(codex_home),
        raw_dir=state_root / "raw",
        raw_daily_dir=state_root / "raw" / "daily",
        raw_windows_dir=state_root / "raw" / "windows",
        registry_dir=state_root / "registry",
        reviews_dir=state_root / "reviews",
        reports_dir=state_root / "reports",
        consolidated_dir=state_root / "consolidated",
        consolidated_daily_dir=state_root / "consolidated" / "daily",
        runtime_dir=state_root / "runtime",
        nightly_runner_dir=state_root / "runtime" / "nightly-runner",
        nightly_codex_home=state_root / "runtime" / "codex-nightly-home",
        nightly_claude_home=state_root / "runtime" / "claude-nightly-home",
        log_dir=state_root / "log",
    )


def read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_codex_history(codex_home, day="2026-04-28"):
    session_id = "019dcefe-37f1-7a83-a8a6-720bd6b79d7f"
    prompt_ts = int(datetime.fromisoformat(day + "T04:00:00+00:00").timestamp())
    complete_ts = int(datetime.fromisoformat(day + "T04:05:00+00:00").timestamp())
    history_path = codex_home / "history.jsonl"
    session_path = codex_home / "sessions" / "2026" / "04" / "28" / "rollout-{}.jsonl".format(session_id)
    history_path.parent.mkdir(parents=True)
    session_path.parent.mkdir(parents=True)
    history_path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "ts": prompt_ts,
                "text": "Summarize OpenRelix Codex memory archive.",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    session_rows = [
        {
            "type": "session_meta",
            "payload": {
                "cwd": "/tmp/openrelix",
                "originator": "codex",
                "source": "cli",
                "timestamp": day + "T04:00:00Z",
            },
        },
        {"type": "turn_context", "payload": {"turn_id": "turn-1"}},
        {
            "type": "event_msg",
            "timestamp": day + "T04:00:00Z",
            "payload": {"type": "user_message", "message": "Summarize OpenRelix Codex memory archive."},
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-1",
                "completed_at": complete_ts,
                "last_agent_message": "The Codex memory archive should be fixed and visualized.",
            },
        },
    ]
    session_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in session_rows) + "\n",
        encoding="utf-8",
    )
    return session_id


class CodexMemorySyncTests(unittest.TestCase):
    def test_sync_archives_codex_windows_docs_and_registry(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex-home"
            session_id = write_codex_history(codex_home)
            paths = runtime_paths_for_state(root / "state", codex_home)
            profile = codex_profiles.CodexProfile(codex_home=codex_home, source="test")

            payload = codex_memory_sync.sync_codex_memory_archive(
                paths=paths,
                dates=["2026-04-28"],
                profiles=[profile],
                source="history",
            )

            self.assertEqual(payload["synced_window_count"], 1)
            memory_root = asset_runtime.get_codex_memory_root(paths)
            self.assertEqual(Path(payload["codex_memory_root"]), memory_root)
            windows = read_jsonl(memory_root / "windows.jsonl")
            self.assertEqual(windows[0]["window_id"], session_id)
            self.assertEqual(windows[0]["prompt_count"], 1)
            self.assertIn("fixed and visualized", windows[0]["conclusion_preview"])

            docs = read_jsonl(paths.registry_dir / "codex_memory_docs.jsonl")
            self.assertEqual(docs[0]["doc_id"], "codex-memory-2026-04-28")
            self.assertEqual(docs[0]["body_path"], "codex-memory/docs/2026-04-28.md")
            self.assertIn(session_id, docs[0]["source_refs"]["window_ids"])
            self.assertTrue((memory_root / "docs" / "2026-04-28.md").exists())

    def test_discover_dates_and_incremental_schedule_are_low_cost(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex-home"
            write_codex_history(codex_home)
            paths = runtime_paths_for_state(root / "state", codex_home)
            asset_runtime.ensure_state_layout(paths)
            profile = codex_profiles.CodexProfile(codex_home=codex_home, source="test")

            dates = codex_memory_sync.discover_codex_memory_dates(paths=paths, profiles=[profile], source="history")
            self.assertEqual(dates, ["2026-04-28"])

            schedule = codex_memory_sync.install_cron_schedule(
                paths=paths,
                interval_minutes=30,
                stage="preliminary",
                source="history",
                summarize=True,
                install=False,
            )
            self.assertFalse(schedule["installed"])
            self.assertIn("codex-memory incremental", schedule["entry"])
            self.assertIn("--activity-source 'history'", schedule["entry"])
            self.assertIn("--summarize", schedule["entry"])
            self.assertTrue((asset_runtime.get_codex_memory_root(paths) / "incremental.cron").exists())


if __name__ == "__main__":
    raise SystemExit(unittest.main())
