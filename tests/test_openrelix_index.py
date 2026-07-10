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
        nightly_claude_home=state_root / "runtime" / "claude-nightly-home",
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
                            "window_title": "SQLite index backend",
                            "question_summary": "Design the SQLite index",
                            "main_takeaway": "Use a rebuildable sidecar database.",
                            "summary_pairs": [
                                {
                                    "question": "Should OpenRelix use SQLite for window search?",
                                    "conclusion": "Use a rebuildable sidecar database.",
                                }
                            ],
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
            self.assertEqual(windows[0]["window_title"], "SQLite index backend")
            self.assertEqual(windows[0]["main_takeaway"], "Use a rebuildable sidecar database.")
            self.assertEqual(windows[0]["summary_status"], "summarized")
            self.assertEqual(
                windows[0]["summary_pairs"],
                [
                    {
                        "question": "Should OpenRelix use SQLite for window search?",
                        "conclusion": "Use a rebuildable sidecar database.",
                    }
                ],
            )

            pair_windows = openrelix_index.search_windows(
                "Should OpenRelix use SQLite",
                paths=paths,
                db_path=db_path,
            )
            self.assertEqual([item["window_id"] for item in pair_windows], ["w-index"])

            raw_windows = openrelix_index.search_windows(
                "search command is next",
                paths=paths,
                db_path=db_path,
            )
            self.assertEqual([item["window_id"] for item in raw_windows], ["w-search"])
            self.assertEqual(raw_windows[0]["summary_status"], "raw_fallback")
            self.assertEqual(raw_windows[0]["window_title"], "add search command")
            self.assertEqual(
                raw_windows[0]["summary_pairs"],
                [{"question": "add search command", "conclusion": "search command is next"}],
            )
            self.assertEqual(
                raw_windows[0]["raw_summary_pairs"],
                [{"question": "add search command", "conclusion": "search command is next"}],
            )

            raw_question_windows = openrelix_index.search_windows(
                "add search",
                search_scope="raw-question",
                paths=paths,
                db_path=db_path,
            )
            self.assertEqual([item["window_id"] for item in raw_question_windows], ["w-search"])
            self.assertEqual(raw_question_windows[0]["matched_messages"][0]["kind"], "prompt")
            self.assertEqual(raw_question_windows[0]["matched_messages"][0]["text"], "add search command")

    def test_ensure_state_layout_keeps_rebuilt_index_fresh(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            self.write_fixture_state(paths)
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"
            openrelix_index.rebuild_index(paths, db_path)

            asset_runtime.ensure_state_layout(paths)

            self.assertFalse(openrelix_index.index_status(paths, db_path)["stale"])

    def test_all_window_search_orders_fts_matches_by_recent_activity(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            asset_runtime.ensure_state_layout(paths)
            raw_window_dir = paths.raw_windows_dir / "2026-06-02"
            raw_window_dir.mkdir(parents=True, exist_ok=True)
            older_window = {
                "date": "2026-05-09",
                "window_id": "w-older-strong-match",
                "cwd": "/tmp/openrelix",
                "originator": "codex_cli",
                "source": "history",
                "started_at": "2026-05-09T09:00:00+08:00",
                "prompt_count": 1,
                "conclusion_count": 1,
                "prompts": [
                    {
                        "local_time": "2026-05-09T09:00:00+08:00",
                        "text": "rankingterm rankingterm rankingterm rankingterm older recap",
                    }
                ],
                "conclusions": [
                    {
                        "completed_at": "2026-05-09T09:05:00+08:00",
                        "text": "rankingterm rankingterm older conclusion",
                    }
                ],
            }
            newer_window = {
                "date": "2026-06-02",
                "window_id": "w-newer-light-match",
                "cwd": "/tmp/openrelix",
                "originator": "codex_cli",
                "source": "history",
                "started_at": "2026-06-02T10:00:00+08:00",
                "prompt_count": 1,
                "conclusion_count": 1,
                "prompts": [
                    {
                        "local_time": "2026-06-02T10:00:00+08:00",
                        "text": "rankingterm newer recap",
                    }
                ],
                "conclusions": [
                    {
                        "completed_at": "2026-06-02T10:05:00+08:00",
                        "text": "newer conclusion",
                    }
                ],
            }
            for item in (older_window, newer_window):
                (raw_window_dir / "{}.json".format(item["window_id"])).write_text(
                    json.dumps(item),
                    encoding="utf-8",
                )
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"
            openrelix_index.rebuild_index(paths, db_path)

            windows = openrelix_index.search_windows(
                "rankingterm",
                search_scope="all",
                paths=paths,
                db_path=db_path,
                limit=2,
            )

            self.assertEqual(
                [item["window_id"] for item in windows],
                ["w-newer-light-match", "w-older-strong-match"],
            )

    def test_rebuild_skips_claude_mem_observer_windows(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            asset_runtime.ensure_state_layout(paths)
            normal_window = {
                "date": "2026-05-21",
                "window_id": "w-normal",
                "ai_host": "codex",
                "cwd": "/tmp/openrelix",
                "originator": "codex_app_server",
                "source": "codex_app_server:vscode",
                "started_at": "2026-05-21T10:00:00+08:00",
                "prompt_count": 1,
                "conclusion_count": 1,
                "prompts": [{"local_time": "2026-05-21T10:00:00+08:00", "text": "normal work"}],
                "conclusions": [{"completed_at": "2026-05-21T10:01:00+08:00", "text": "normal result"}],
            }
            observer_window = {
                "date": "2026-05-21",
                "window_id": "claude-observer",
                "ai_host": "claude",
                "cwd": "/tmp/.claude-mem/observer-sessions",
                "originator": "claude_code",
                "source": "claude_code:jsonl",
                "session_file": "/tmp/.claude/projects/claude-mem-observer-sessions/session.jsonl",
                "started_at": "2026-05-21T11:00:00+08:00",
                "prompt_count": 1,
                "conclusion_count": 1,
                "prompts": [
                    {
                        "local_time": "2026-05-21T11:00:00+08:00",
                        "text": "You are a Claude-Mem, a specialized observer tool.",
                    }
                ],
                "conclusions": [{"completed_at": "2026-05-21T11:01:00+08:00", "text": "stored"}],
            }
            automation_window = {
                "date": "2026-05-21",
                "window_id": "codex-automation",
                "ai_host": "codex",
                "cwd": "/tmp/search-kb",
                "originator": "codex_app_server",
                "source": "codex_app_server:vscode",
                "started_at": "2026-05-21T12:00:00+08:00",
                "prompt_count": 1,
                "conclusion_count": 1,
                "prompts": [
                    {
                        "local_time": "2026-05-21T12:00:00+08:00",
                        "text": "Automation: Refresh Search Android KB\nAutomation ID: refresh",
                    }
                ],
                "conclusions": [{"completed_at": "2026-05-21T12:01:00+08:00", "text": "refreshed"}],
            }
            raw_payload = {
                "date": "2026-05-21",
                "windows": [normal_window, observer_window, automation_window],
            }
            (paths.raw_daily_dir / "2026-05-21.json").write_text(
                json.dumps(raw_payload),
                encoding="utf-8",
            )
            raw_window_dir = paths.raw_windows_dir / "2026-05-21"
            raw_window_dir.mkdir(parents=True, exist_ok=True)
            (raw_window_dir / "w-normal.json").write_text(json.dumps(normal_window), encoding="utf-8")
            (raw_window_dir / "claude-observer.json").write_text(
                json.dumps(observer_window),
                encoding="utf-8",
            )
            (raw_window_dir / "codex-automation.json").write_text(
                json.dumps(automation_window),
                encoding="utf-8",
            )
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"

            stats = openrelix_index.rebuild_index(paths, db_path)
            observer_results = openrelix_index.search_windows(
                "Claude-Mem",
                paths=paths,
                db_path=db_path,
            )
            normal_results = openrelix_index.search_windows(
                "normal result",
                paths=paths,
                db_path=db_path,
            )
            automation_results = openrelix_index.search_windows(
                "Refresh Search Android KB",
                paths=paths,
                db_path=db_path,
            )

            self.assertEqual(stats["window_rows"], 1)
            self.assertEqual(observer_results, [])
            self.assertEqual(automation_results, [])
            self.assertEqual([row["window_id"] for row in normal_results], ["w-normal"])

    def test_window_id_scope_prioritizes_real_window_and_can_exclude_observers(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            self.write_fixture_state(paths)
            target_id = "019e49ee-8333-79d2-8029-0cc3696117c5"
            window_dir = paths.raw_windows_dir / "2026-05-21"
            window_dir.mkdir(parents=True, exist_ok=True)
            (window_dir / "{}.json".format(target_id)).write_text(
                json.dumps(
                    {
                        "date": "2026-05-21",
                        "window_id": target_id,
                        "cwd": "/tmp/openviking",
                        "originator": "codex_app_server",
                        "source": "codex_app_server:vscode",
                        "started_at": "2026-05-21T17:47:08+08:00",
                        "prompt_count": 1,
                        "conclusion_count": 1,
                        "prompts": [{"local_time": "2026-05-21T17:47:08+08:00", "text": "install OpenViking"}],
                        "conclusions": [{"completed_at": "2026-05-21T18:06:22+08:00", "text": "OpenViking configured"}],
                    }
                ),
                encoding="utf-8",
            )
            (window_dir / "claude-observer.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-21",
                        "window_id": "claude-observer",
                        "cwd": "/tmp/observer-sessions",
                        "originator": "claude_code",
                        "source": "claude_code:jsonl",
                        "started_at": "2026-05-21T22:24:17+08:00",
                        "prompt_count": 1,
                        "conclusion_count": 1,
                        "review_related_window": True,
                        "prompts": [
                            {
                                "local_time": "2026-05-21T22:24:17+08:00",
                                "text": "Hello memory agent, observe window {}".format(target_id),
                            }
                        ],
                        "conclusions": [{"completed_at": "2026-05-21T22:25:51+08:00", "text": "observer summary"}],
                    }
                ),
                encoding="utf-8",
            )
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"
            openrelix_index.rebuild_index(paths, db_path)

            id_results = openrelix_index.search_windows(
                target_id,
                search_scope="id",
                include_review_related=False,
                paths=paths,
                db_path=db_path,
            )
            all_results = openrelix_index.search_windows(
                target_id,
                include_review_related=False,
                paths=paths,
                db_path=db_path,
            )

            self.assertEqual([row["window_id"] for row in id_results], [target_id])
            self.assertNotIn("claude-observer", [row["window_id"] for row in all_results])
            self.assertEqual(all_results[0]["window_id"], target_id)

    def test_rebuild_indexes_canonical_memory_entries(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            self.write_fixture_state(paths)
            canonical_path = paths.registry_dir / "memory_entries.jsonl"
            canonical_path.write_text(
                json.dumps(
                    {
                        "date": "2026-05-06",
                        "language": "en",
                        "source": "canonical",
                        "bucket": "durable",
                        "title": "Canonical global memory",
                        "memory_type": "procedural",
                        "priority": "high",
                        "scope": "global",
                        "injection_policy": "global_context",
                        "project_key": "",
                        "project_label": "",
                        "value_note": "Independent memory storage should still be searchable.",
                        "source_window_ids": ["w-index"],
                        "keywords": ["canonical"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"

            stats = openrelix_index.rebuild_index(paths, db_path)
            results = openrelix_index.search_memories("canonical", paths=paths, db_path=db_path)

            self.assertEqual(stats["memory_rows"], 1)
            self.assertEqual(results[0]["title"], "Canonical global memory")
            self.assertEqual(results[0]["scope"], "global")
            self.assertEqual(results[0]["injection_policy"], "global_context")

    def test_search_memories_filters_on_demand_policy(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            asset_runtime.ensure_state_layout(paths)
            canonical_path = paths.registry_dir / "memory_entries.jsonl"
            canonical_path.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in [
                        {
                            "date": "2026-05-06",
                            "source": "canonical",
                            "bucket": "session",
                            "title": "Bridge diagnosis recall",
                            "memory_type": "semantic",
                            "priority": "medium",
                            "scope": "domain",
                            "injection_policy": "on_demand",
                            "value_note": "Retrieve this bridge diagnosis only when explicitly searched.",
                            "keywords": ["bridge", "recall"],
                        },
                        {
                            "date": "2026-05-06",
                            "source": "canonical",
                            "bucket": "durable",
                            "title": "Bridge global rule",
                            "memory_type": "procedural",
                            "priority": "high",
                            "scope": "global",
                            "injection_policy": "global_context",
                            "value_note": "This should not appear in on-demand-only search.",
                            "keywords": ["bridge"],
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"

            results = openrelix_index.search_memories(
                "bridge",
                injection_policy="on_demand",
                paths=paths,
                db_path=db_path,
            )

            self.assertEqual([row["title"] for row in results], ["Bridge diagnosis recall"])

    def test_failed_model_summary_keeps_window_raw_fallback_in_index(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            asset_runtime.ensure_state_layout(paths)
            raw_payload = {
                "date": "2026-04-30",
                "windows": [
                    {
                        "date": "2026-04-30",
                        "window_id": "w-failed-summary",
                        "cwd": "/tmp/openrelix",
                        "started_at": "2026-04-30T10:00:00+08:00",
                        "prompt_count": 2,
                        "conclusion_count": 2,
                        "prompts": [
                            {
                                "turn_id": "t1",
                                "local_time": "2026-04-30T10:01:00+08:00",
                                "text": "raw first question",
                            },
                            {
                                "turn_id": "t2",
                                "local_time": "2026-04-30T10:03:00+08:00",
                                "text": "raw second question",
                            },
                        ],
                        "conclusions": [
                            {
                                "turn_id": "t2",
                                "completed_at": "2026-04-30T10:04:00+08:00",
                                "text": "raw second conclusion",
                            },
                            {
                                "turn_id": "t1",
                                "completed_at": "2026-04-30T10:02:00+08:00",
                                "text": "raw first conclusion",
                            },
                        ],
                    }
                ],
            }
            (paths.raw_daily_dir / "2026-04-30.json").write_text(
                json.dumps(raw_payload),
                encoding="utf-8",
            )
            summary_dir = paths.consolidated_daily_dir / "2026-04-30"
            summary_dir.mkdir(parents=True, exist_ok=True)
            (summary_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "date": "2026-04-30",
                        "stage": "manual",
                        "model_status": "failed",
                        "window_summaries": [
                            {
                                "window_id": "w-failed-summary",
                                "cwd": "/tmp/openrelix",
                                "window_title": "model fallback title should not mark learned",
                                "question_summary": "model fallback question",
                                "main_takeaway": "model fallback conclusion",
                                "summary_pairs": [
                                    {
                                        "question": "model fallback pair",
                                        "conclusion": "model fallback answer",
                                    }
                                ],
                                "keywords": ["fallback"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"

            openrelix_index.rebuild_index(paths, db_path)
            windows = openrelix_index.search_windows(
                "raw first conclusion",
                paths=paths,
                db_path=db_path,
            )

            self.assertEqual([item["window_id"] for item in windows], ["w-failed-summary"])
            self.assertEqual(windows[0]["summary_status"], "raw_fallback")
            self.assertEqual(windows[0]["window_title"], "raw first question")
            self.assertEqual(
                windows[0]["summary_pairs"],
                [
                    {"question": "raw first question", "conclusion": "raw first conclusion"},
                    {"question": "raw second question", "conclusion": "raw second conclusion"},
                ],
            )
            self.assertEqual(windows[0]["summary_pairs"], windows[0]["raw_summary_pairs"])

    def test_lightweight_summary_is_indexed_without_model_completed_status(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            asset_runtime.ensure_state_layout(paths)
            raw_payload = {
                "date": "2026-05-03",
                "windows": [
                    {
                        "date": "2026-05-03",
                        "window_id": "w-lightweight",
                        "cwd": "/tmp/openrelix",
                        "started_at": "2026-05-03T23:40:00+08:00",
                        "prompt_count": 1,
                        "conclusion_count": 1,
                        "prompts": [
                            {
                                "local_time": "2026-05-03T23:41:00+08:00",
                                "text": "raw lightweight question",
                            }
                        ],
                        "conclusions": [
                            {
                                "completed_at": "2026-05-03T23:42:00+08:00",
                                "text": "raw lightweight conclusion",
                            }
                        ],
                    }
                ],
            }
            (paths.raw_daily_dir / "2026-05-03.json").write_text(
                json.dumps(raw_payload),
                encoding="utf-8",
            )
            summary_dir = paths.consolidated_daily_dir / "2026-05-03"
            summary_dir.mkdir(parents=True, exist_ok=True)
            (summary_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-03",
                        "stage": "preliminary",
                        "model_status": "skipped_lightweight",
                        "summary_generation": "lightweight",
                        "window_summaries": [
                            {
                                "window_id": "w-lightweight",
                                "cwd": "/tmp/openrelix",
                                "window_title": "quick lightweight title",
                                "question_summary": "quick lightweight question",
                                "main_takeaway": "quick lightweight conclusion",
                                "summary_pairs": [
                                    {
                                        "question": "quick lightweight question",
                                        "conclusion": "quick lightweight conclusion",
                                    }
                                ],
                                "keywords": ["lightweight"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"

            openrelix_index.rebuild_index(paths, db_path)
            windows = openrelix_index.search_windows(
                "quick lightweight conclusion",
                paths=paths,
                db_path=db_path,
            )

            self.assertEqual([item["window_id"] for item in windows], ["w-lightweight"])
            self.assertEqual(windows[0]["summary_status"], "lightweight")
            self.assertEqual(windows[0]["window_title"], "quick lightweight title")
            self.assertEqual(
                windows[0]["summary_pairs"],
                [
                    {
                        "question": "quick lightweight question",
                        "conclusion": "quick lightweight conclusion",
                    }
                ],
            )

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

    def test_search_windows_can_skip_rebuild_for_readonly_panel_queries(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            self.write_fixture_state(paths)
            db_path = Path(tmpdir) / "runtime" / "test-index.sqlite3"
            openrelix_index.rebuild_index(paths, db_path)
            before_mtime = db_path.stat().st_mtime_ns

            memory_path = paths.registry_dir / "memory_items.jsonl"
            memory_path.write_text(
                memory_path.read_text(encoding="utf-8")
                + json.dumps(
                    {
                        "date": "2026-04-29",
                        "source": "nightly_codex",
                        "bucket": "durable",
                        "title": "Stale marker",
                        "memory_type": "semantic",
                        "priority": "high",
                        "value_note": "Readonly window search should not rebuild.",
                        "source_window_ids": ["w-index"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertTrue(openrelix_index.index_status(paths, db_path)["stale"])
            with mock.patch.object(openrelix_index, "rebuild_index") as rebuild:
                windows = openrelix_index.search_windows(
                    "sidecar",
                    paths=paths,
                    db_path=db_path,
                    rebuild=False,
                )

            rebuild.assert_not_called()
            self.assertEqual([row["window_id"] for row in windows], ["w-index"])
            self.assertEqual(db_path.stat().st_mtime_ns, before_mtime)

    def test_search_windows_without_rebuild_does_not_create_missing_index(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            self.write_fixture_state(paths)
            db_path = Path(tmpdir) / "runtime" / "missing-index.sqlite3"

            with mock.patch.object(openrelix_index, "rebuild_index") as rebuild:
                windows = openrelix_index.search_windows(
                    "sidecar",
                    paths=paths,
                    db_path=db_path,
                    rebuild=False,
                )

            rebuild.assert_not_called()
            self.assertEqual(windows, [])
            self.assertFalse(db_path.exists())

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
