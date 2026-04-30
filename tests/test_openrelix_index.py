#!/usr/bin/env python3

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asset_runtime  # noqa: E402
import openrelix_index  # noqa: E402


def runtime_paths_for_state(state_root):
    base = asset_runtime.get_runtime_paths()
    state_root = Path(state_root)
    return replace(
        base,
        state_root=state_root,
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
        log_dir=state_root / "log",
    )


class OpenRelixIndexTests(unittest.TestCase):
    def write_fixture_state(self, paths):
        asset_runtime.ensure_state_layout(paths)
        memory_rows = [
            {
                "date": "2026-04-28",
                "language": "en",
                "source": "nightly_codex",
                "bucket": "durable",
                "title": "SQLite memory backend",
                "memory_type": "procedural",
                "priority": "high",
                "value_note": "Use SQLite as a rebuildable sidecar index.",
                "source_window_ids": ["w-index"],
                "keywords": ["sqlite", "index"],
            },
            {
                "date": "2026-04-28",
                "language": "en",
                "source": "nightly_codex",
                "bucket": "session",
                "title": "Search CLI followup",
                "memory_type": "task",
                "priority": "medium",
                "value_note": "Add commands after the backend lands.",
                "source_window_ids": ["w-search"],
                "keywords": ["search", "cli"],
            },
        ]
        memory_path = paths.registry_dir / "memory_items.jsonl"
        memory_path.write_text(
            "\n".join(json.dumps(row) for row in memory_rows)
            + "\n{bad json}\n",
            encoding="utf-8",
        )

        raw_payload = {
            "date": "2026-04-28",
            "stage": "manual",
            "windows": [
                {
                    "date": "2026-04-28",
                    "window_id": "w-index",
                    "cwd": "/tmp/openrelix",
                    "originator": "codex_cli",
                    "source": "history",
                    "started_at": "2026-04-28T10:00:00+08:00",
                    "session_file": "/tmp/session.jsonl",
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "review_like_window": False,
                    "review_related_window": False,
                    "prompts": [{"local_time": "2026-04-28T10:00:00+08:00", "text": "design sqlite index"}],
                    "conclusions": [{"completed_at": "2026-04-28T10:05:00+08:00", "text": "backend implemented"}],
                }
            ],
        }
        (paths.raw_daily_dir / "2026-04-28.json").write_text(
            json.dumps(raw_payload),
            encoding="utf-8",
        )
        raw_window_dir = paths.raw_windows_dir / "2026-04-28"
        raw_window_dir.mkdir(parents=True, exist_ok=True)
        (raw_window_dir / "w-index.json").write_text(
            json.dumps(raw_payload["windows"][0]),
            encoding="utf-8",
        )
        (raw_window_dir / "w-search.json").write_text(
            json.dumps(
                {
                    "date": "2026-04-28",
                    "window_id": "w-search",
                    "cwd": "/tmp/openrelix",
                    "originator": "codex_cli",
                    "source": "history",
                    "started_at": "2026-04-28T11:00:00+08:00",
                    "session_file": "/tmp/session-2.jsonl",
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "prompts": [{"local_time": "2026-04-28T11:00:00+08:00", "text": "add search command"}],
                    "conclusions": [{"completed_at": "2026-04-28T11:05:00+08:00", "text": "search command is next"}],
                }
            ),
            encoding="utf-8",
        )

        summary_dir = paths.consolidated_daily_dir / "2026-04-28"
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / "summary.json").write_text(
            json.dumps(
                {
                    "date": "2026-04-28",
                    "stage": "manual",
                    "window_summaries": [
                        {
                            "window_id": "w-index",
                            "cwd": "/tmp/openrelix",
                            "question_summary": "Design the SQLite index",
                            "main_takeaway": "Use a rebuildable sidecar database.",
                            "keywords": ["sqlite", "sidecar"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_rebuild_indexes_memory_and_windows(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            self.write_fixture_state(paths)
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"

            stats = openrelix_index.rebuild_index(paths, db_path)

            self.assertEqual(stats["memory_rows"], 2)
            self.assertEqual(stats["window_rows"], 2)
            self.assertEqual(stats["daily_summary_rows"], 1)
            self.assertEqual(stats["source_file_rows"], 5)
            self.assertEqual(stats["skipped_memory_rows"], 1)
            status = openrelix_index.index_status(paths, db_path)
            self.assertTrue(status["ok"])
            self.assertFalse(status["stale"])

            memories = openrelix_index.search_memories(
                "sqlite",
                bucket="durable",
                paths=paths,
                db_path=db_path,
            )
            self.assertEqual(len(memories), 1)
            self.assertEqual(memories[0]["title"], "SQLite memory backend")
            self.assertEqual(memories[0]["source_window_ids"], ["w-index"])

            windows = openrelix_index.search_windows(
                "sidecar",
                project="openrelix",
                paths=paths,
                db_path=db_path,
            )
            self.assertEqual(len(windows), 1)
            self.assertEqual(windows[0]["window_id"], "w-index")
            self.assertEqual(windows[0]["main_takeaway"], "Use a rebuildable sidecar database.")

    def test_search_rebuilds_missing_index(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            self.write_fixture_state(paths)
            db_path = Path(tmpdir) / "runtime" / "missing-index.sqlite3"

            self.assertFalse(db_path.exists())
            results = openrelix_index.search_memories("Search CLI", paths=paths, db_path=db_path)

            self.assertTrue(db_path.exists())
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["bucket"], "session")

    def test_status_is_read_only_when_index_is_missing_or_present(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")

            status = openrelix_index.index_status(paths)

            self.assertFalse(status["exists"])
            self.assertFalse(paths.registry_dir.exists())
            self.assertFalse(paths.runtime_dir.exists())

            self.write_fixture_state(paths)
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"
            openrelix_index.rebuild_index(paths, db_path)
            for sidecar in openrelix_index.db_sidecar_paths(db_path):
                self.assertFalse(sidecar.exists())
            before_mtime = db_path.stat().st_mtime_ns

            status = openrelix_index.index_status(paths, db_path)

            self.assertTrue(status["ok"])
            self.assertEqual(db_path.stat().st_mtime_ns, before_mtime)
            for sidecar in openrelix_index.db_sidecar_paths(db_path):
                self.assertFalse(sidecar.exists())

    def test_special_queries_and_metadata_fallback_do_not_crash(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            self.write_fixture_state(paths)
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"
            openrelix_index.rebuild_index(paths, db_path)

            self.assertEqual(
                [row["window_id"] for row in openrelix_index.search_windows("2026-04-28", paths=paths, db_path=db_path)],
                ["w-search", "w-index"],
            )
            self.assertEqual(
                [row["window_id"] for row in openrelix_index.search_windows("/tmp/openrelix", paths=paths, db_path=db_path)],
                ["w-search", "w-index"],
            )
            self.assertEqual(
                [row["title"] for row in openrelix_index.search_memories("high", paths=paths, db_path=db_path)],
                ["SQLite memory backend"],
            )
            self.assertEqual(
                openrelix_index.search_memories('"unterminated query', paths=paths, db_path=db_path),
                [],
            )

    def test_same_window_id_is_indexed_per_date_and_stale_is_detected(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            self.write_fixture_state(paths)
            second_day = {
                "date": "2026-04-29",
                "stage": "manual",
                "windows": [
                    {
                        "date": "2026-04-29",
                        "window_id": "w-index",
                        "cwd": "/tmp/other",
                        "originator": "codex_cli",
                        "source": "history",
                        "started_at": "2026-04-29T10:00:00+08:00",
                        "session_file": "/tmp/session-3.jsonl",
                        "prompt_count": 1,
                        "conclusion_count": 1,
                        "prompts": [{"local_time": "2026-04-29T10:00:00+08:00", "text": "second day"}],
                        "conclusions": [{"completed_at": "2026-04-29T10:05:00+08:00", "text": "same id different date"}],
                    }
                ],
            }
            (paths.raw_daily_dir / "2026-04-29.json").write_text(json.dumps(second_day), encoding="utf-8")
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"

            stats = openrelix_index.rebuild_index(paths, db_path)

            self.assertEqual(stats["window_rows"], 3)
            self.assertEqual(
                len(openrelix_index.search_windows("w-index", paths=paths, db_path=db_path)),
                2,
            )
            memory_path = paths.registry_dir / "memory_items.jsonl"
            memory_path.write_text(
                memory_path.read_text(encoding="utf-8")
                + json.dumps(
                    {
                        "date": "2026-04-29",
                        "source": "nightly_codex",
                        "bucket": "durable",
                        "title": "New stale marker",
                        "memory_type": "semantic",
                        "priority": "high",
                        "value_note": "This should make the index stale.",
                        "source_window_ids": ["w-index"],
                        "keywords": ["stale"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertTrue(openrelix_index.index_status(paths, db_path)["stale"])
            self.assertEqual(
                [row["title"] for row in openrelix_index.search_memories("stale", paths=paths, db_path=db_path)],
                ["New stale marker"],
            )

    def test_failed_atomic_replace_keeps_existing_index(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            self.write_fixture_state(paths)
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"
            openrelix_index.rebuild_index(paths, db_path)
            before = openrelix_index.index_status(paths, db_path)
            wal_path = Path("{}-wal".format(db_path))
            shm_path = Path("{}-shm".format(db_path))
            old_conn = sqlite3.connect(str(db_path))
            old_conn.execute("PRAGMA journal_mode = WAL")
            old_conn.execute("CREATE TABLE IF NOT EXISTS replace_probe(value TEXT)")
            old_conn.execute("INSERT INTO replace_probe(value) VALUES ('old db survives')")
            old_conn.commit()
            self.assertTrue(wal_path.exists())
            self.assertTrue(shm_path.exists())

            memory_path = paths.registry_dir / "memory_items.jsonl"
            memory_path.write_text(
                memory_path.read_text(encoding="utf-8")
                + json.dumps(
                    {
                        "date": "2026-04-29",
                        "source": "nightly_codex",
                        "bucket": "durable",
                        "title": "Replace should fail",
                        "memory_type": "semantic",
                        "priority": "high",
                        "value_note": "The old database must survive.",
                        "source_window_ids": ["w-index"],
                        "keywords": ["replace"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(openrelix_index.os, "replace", side_effect=OSError("simulated replace failure")):
                with self.assertRaises(OSError):
                    openrelix_index.rebuild_index(paths, db_path)

            self.assertTrue(wal_path.exists())
            self.assertTrue(shm_path.exists())
            self.assertEqual(
                old_conn.execute("SELECT value FROM replace_probe").fetchone()[0],
                "old db survives",
            )
            old_conn.close()
            after = openrelix_index.index_status(paths, db_path)
            self.assertEqual(after["source_fingerprint"], before["source_fingerprint"])
            self.assertEqual(after["memory_rows"], before["memory_rows"])
            self.assertTrue(after["ok"])


if __name__ == "__main__":
    unittest.main()
