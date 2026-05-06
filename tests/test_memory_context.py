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

    def test_legacy_source_window_rows_default_to_project_context(self):
        row = {
            "bucket": "session",
            "priority": "medium",
            "source_window_ids": ["w-project"],
        }

        self.assertEqual(memory_context.memory_scope_from_record(row), "project")
        self.assertEqual(
            memory_context.host_context_injection_policy_from_record(row),
            "project_context",
        )
        self.assertFalse(memory_context.memory_record_is_global_context(row))

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

    def test_policy_views_split_global_project_on_demand_and_local(self):
        rows = [
            {"bucket": "durable", "title": "Global", "scope": "global"},
            {"bucket": "session", "title": "Project", "project_key": "openrelix"},
            {"bucket": "durable", "title": "Domain", "scope": "domain"},
            {"bucket": "low_priority", "title": "Local", "scope": "global"},
            {"bucket": "session", "title": "Never", "injection_policy": "never"},
        ]

        views = memory_context.build_memory_policy_views(
            rows,
            selected_global_rows=[rows[0]],
            token_usage={"enabled": True, "estimated_tokens": 42, "meter_percent": 3},
        )

        self.assertEqual(views["compiler"]["total_count"], 5)
        self.assertEqual(views["compiler"]["global_candidate_count"], 1)
        self.assertEqual(views["compiler"]["selected_global_count"], 1)
        self.assertEqual(views["compiler"]["project_context_count"], 1)
        self.assertEqual(views["compiler"]["on_demand_count"], 1)
        self.assertEqual(views["compiler"]["local_count"], 2)
        self.assertEqual([row["title"] for row in views["global_context"]["rows"]], ["Global"])
        self.assertEqual([row["title"] for row in views["project_context"]["rows"]], ["Project"])
        self.assertEqual([row["title"] for row in views["on_demand"]["rows"]], ["Domain"])
        self.assertEqual([row["title"] for row in views["local_only"]["rows"]], ["Local", "Never"])

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
        self.assertEqual(views["compiler"]["selected_global_count"], 0)


if __name__ == "__main__":
    unittest.main()
