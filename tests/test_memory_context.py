#!/usr/bin/env python3

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from openrelix_overview import memory_context  # noqa: E402


class MemoryContextPolicyTests(unittest.TestCase):
    def test_project_rows_default_to_project_context(self):
        row = {
            "bucket": "durable",
            "priority": "high",
            "project_label": "OpenRelix",
        }

        self.assertEqual(memory_context.memory_scope_from_record(row), "project")
        self.assertEqual(
            memory_context.host_context_injection_policy_from_record(row),
            "project_context",
        )
        self.assertFalse(memory_context.memory_record_is_global_context(row))
        self.assertTrue(memory_context.memory_record_is_host_context_candidate(row))

    def test_legacy_source_window_rows_stay_on_demand_without_approval(self):
        row = {
            "bucket": "session",
            "priority": "medium",
            "source": "legacy",
            "source_window_ids": ["w-project"],
        }

        self.assertEqual(memory_context.memory_scope_from_record(row), "project")
        self.assertEqual(
            memory_context.host_context_injection_policy_from_record(row),
            "on_demand",
        )
        self.assertFalse(memory_context.memory_record_is_global_context(row))
        self.assertFalse(memory_context.memory_record_is_host_context_candidate(row))

    def test_quality_gated_nightly_project_rows_can_enter_host_context(self):
        row = {
            "bucket": "session",
            "priority": "high",
            "source": "nightly_codex",
            "scope": "project",
            "injection_policy": "project_context",
            "storage_quality_score": 6,
            "storage_quality_reason": "type,priority,strong_signal",
            "source_window_ids": ["w-project"],
        }

        self.assertEqual(
            memory_context.host_context_injection_policy_from_record(row),
            "project_context",
        )
        self.assertTrue(memory_context.memory_record_is_host_context_candidate(row))

    def test_low_priority_legacy_rows_stay_local(self):
        row = {
            "bucket": "low_priority",
            "priority": "low",
            "source_window_ids": ["w-project"],
        }

        self.assertEqual(memory_context.memory_scope_from_record(row), "local")
        self.assertEqual(
            memory_context.host_context_injection_policy_from_record(row),
            "local_only",
        )

    def test_low_priority_explicit_global_rows_stay_local(self):
        row = {
            "source": "canonical",
            "bucket": "durable",
            "priority": "low",
            "scope": "global",
            "injection_policy": "global_context",
        }

        self.assertEqual(
            memory_context.host_context_injection_policy_from_record(row),
            "local_only",
        )
        self.assertFalse(memory_context.memory_record_is_global_context(row))
        self.assertFalse(memory_context.memory_record_is_host_context_candidate(row))

    def test_policy_views_split_global_project_on_demand_and_local(self):
        rows = [
            {"bucket": "durable", "title": "Global", "source": "canonical", "scope": "global"},
            {"bucket": "session", "title": "Project", "project_key": "openrelix"},
            {"bucket": "durable", "title": "Domain", "scope": "domain"},
            {"bucket": "low_priority", "title": "Local", "scope": "global"},
            {"bucket": "session", "title": "Never", "injection_policy": "never"},
        ]

        views = memory_context.build_memory_policy_views(
            rows,
            selected_global_rows=[rows[0], rows[1]],
            token_usage={
                "enabled": True,
                "estimated_tokens": 42,
                "estimated_context_item_count": 2,
                "meter_percent": 3,
            },
        )

        self.assertEqual(views["compiler"]["total_count"], 5)
        self.assertEqual(views["compiler"]["global_candidate_count"], 1)
        self.assertEqual(views["compiler"]["host_context_candidate_count"], 2)
        self.assertEqual(views["compiler"]["selected_global_count"], 1)
        self.assertEqual(views["compiler"]["selected_host_context_count"], 2)
        self.assertEqual(views["compiler"]["project_context_count"], 1)
        self.assertEqual(views["compiler"]["on_demand_count"], 1)
        self.assertEqual(views["compiler"]["local_count"], 2)
        self.assertEqual([row["title"] for row in views["global_context"]["rows"]], ["Global"])
        self.assertEqual([row["title"] for row in views["host_context"]["rows"]], ["Global", "Project"])
        self.assertEqual([row["title"] for row in views["project_context"]["rows"]], ["Project"])
        self.assertEqual([row["title"] for row in views["on_demand"]["rows"]], ["Domain"])
        self.assertEqual([row["title"] for row in views["local_only"]["rows"]], ["Local", "Never"])

    def test_legacy_global_rows_require_explicit_approval(self):
        row = {
            "source": "nightly_codex",
            "bucket": "durable",
            "priority": "high",
            "title": "Legacy global",
            "scope": "global",
            "injection_policy": "global_context",
        }

        self.assertEqual(
            memory_context.host_context_injection_policy_from_record(row),
            "on_demand",
        )
        self.assertFalse(memory_context.memory_record_is_global_context(row))
        self.assertFalse(memory_context.memory_record_is_host_context_candidate(row))

    def test_approved_canonical_global_rows_can_enter_host_context(self):
        row = {
            "source": "canonical",
            "bucket": "durable",
            "priority": "high",
            "title": "Global preference",
            "scope": "global",
            "injection_policy": "global_context",
        }

        self.assertEqual(
            memory_context.host_context_injection_policy_from_record(row),
            "global_context",
        )
        self.assertTrue(memory_context.memory_record_is_global_context(row))
        self.assertTrue(memory_context.memory_record_is_host_context_candidate(row))

    def test_memory_storage_quality_drops_obvious_noise(self):
        quality = memory_context.memory_storage_quality(
            {
                "bucket": "low_priority",
                "priority": "low",
                "title": "多个 Claude Code 窗口只是未登录、问候或退出",
                "value_note": "这些窗口没有可复用结论。",
            }
        )

        self.assertEqual(quality["disposition"], "drop")
        self.assertEqual(quality["reason"], "hard_noise")

    def test_memory_storage_quality_demotes_low_signal_primary_memory(self):
        quality = memory_context.memory_storage_quality(
            {
                "bucket": "durable",
                "priority": "medium",
                "memory_type": "task",
                "title": "看了面板",
                "value_note": "当天看了面板。",
                "source_window_ids": ["w1"],
            }
        )

        self.assertIn(quality["disposition"], {"demote", "drop"})

    def test_memory_storage_quality_keeps_reusable_rules(self):
        quality = memory_context.memory_storage_quality(
            {
                "bucket": "durable",
                "priority": "high",
                "memory_type": "procedural",
                "title": "OpenRelix bugfix 默认独立 worktree",
                "value_note": "处理 OpenRelix bugfix 时，必须先切独立 worktree 并跑校验。",
                "source_window_ids": ["w1"],
            }
        )

        self.assertEqual(quality["disposition"], "keep")
        self.assertGreaterEqual(quality["score"], 4)

    def test_policy_views_caps_selected_count_to_current_global_candidates(self):
        rows = [
            {"bucket": "session", "title": "Project", "project_key": "openrelix"},
            {"bucket": "low_priority", "title": "Local"},
        ]

        views = memory_context.build_memory_policy_views(
            rows,
            selected_global_rows=[],
            token_usage={"estimated_context_item_count": 28},
        )

        self.assertEqual(views["compiler"]["global_candidate_count"], 0)
        self.assertEqual(views["compiler"]["host_context_candidate_count"], 1)
        self.assertEqual(views["compiler"]["selected_global_count"], 0)
        self.assertEqual(views["compiler"]["selected_host_context_count"], 1)


if __name__ == "__main__":
    unittest.main()
