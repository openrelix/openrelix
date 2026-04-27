#!/usr/bin/env python3

import tempfile
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_codex_memory_summary  # noqa: E402
import build_overview  # noqa: E402


SAMPLE_MEMORY_INDEX = """# Task Group: Local Codex personal asset system, genericization, and LaunchAgent runtime

scope: User-level personal asset system design under `~/work/openkeepsake`, including runtime state placement and nightly behavior.
applies_to: cwd=~/work/openkeepsake plus user-level Codex state under ~/.codex

## Task 1: Build a local-first personal asset system

### rollout_summary_files

- rollout_summaries/demo.md (updated_at=2026-04-26T12:06:53+00:00)

### keywords

- openkeepsake, memories, nightly_pipeline.sh, LaunchAgent

## User preferences

- when the user asks for a concrete config/runtime value -> answer with the exact value first [Task 1]
- when repo-scoped behavior would add git noise -> keep personal-asset plumbing in user-level storage [Task 1]

## Reusable knowledge

- The system is not hook-driven. `nightly_pipeline.sh` chains collection, consolidation, and overview rebuild [Task 1]
- Locked macOS sessions are fine for LaunchAgents; logout is not [Task 1]

# Task Group: Android scan QR-only cleanup and dead component removal

scope: Review and cleanup of scan/QR experiment removals, dead component chains, and QR-only mounts in Android scan modules.
applies_to: cwd=~/work/android-app

## Task 1: Review scan experiment removal and identify dead component mounts

### rollout_summary_files

- rollout_summaries/demo-2.md (updated_at=2026-04-26T18:06:53+00:00)

### keywords

- ScanRecordRootScene, ScanCoreLogicComponent, QR-only, proguard

## User preferences

- when the user says “清理吧” -> proceed with the deletion once the dead chain is confirmed [Task 1]

## Reusable knowledge

- The QR tab is QR-only; check mounts before deleting code and sweep non-source residue too [Task 1]
"""


SAMPLE_EXISTING_SUMMARY = """## User Profile

The user works across user-level Codex workflows and Android project tasks.

They prefer direct edits when the target state is clear.

## User preferences

- Prefer runtime verification over code-only inference.

## General Tips

- Keep the injected summary smaller than the full memory index.
"""


SAMPLE_PERSONAL_MEMORY_REGISTRY = """
{"date":"2026-04-26","source":"nightly_codex","bucket":"durable","title":"Default context memory mode","memory_type":"procedural","priority":"high","value_note":"Local memory stays in the state root, while a compressed bounded summary is synced into Codex context by default.","keywords":["memory","codex-context","state root"]}
{"date":"2026-04-25","source":"nightly_codex","bucket":"session","title":"Backfill command rollout","memory_type":"task","priority":"medium","value_note":"Users can copy a multi-day okeep backfill command from the panel instead of executing shell from the browser.","keywords":["backfill","panel"]}
{"date":"2026-04-24","source":"nightly_codex","bucket":"low_priority","title":"Do not inject this","memory_type":"semantic","priority":"low","value_note":"Low priority items stay out of the bounded context summary.","keywords":["skip"]}
"""


class MemorySummaryBuilderTests(unittest.TestCase):
    def test_build_memory_summary_respects_budget_and_stays_parseable(self):
        budget = build_codex_memory_summary.SummaryBudget(
            target_tokens=520,
            warn_tokens=560,
            max_tokens=620,
            profile_tokens=90,
            preferences_tokens=120,
            tips_tokens=120,
            routes_tokens=180,
            max_preferences=4,
            max_tips=4,
            max_route_items=4,
            max_route_keywords=3,
        )

        result = build_codex_memory_summary.build_memory_summary(
            SAMPLE_MEMORY_INDEX,
            SAMPLE_EXISTING_SUMMARY,
            budget,
        )

        self.assertNotEqual(result.status, "over_budget")
        self.assertLessEqual(result.estimated_tokens, budget.max_tokens)
        self.assertIn("## User preferences", result.text)
        self.assertIn("## General Tips", result.text)
        self.assertIn("## What's in Memory", result.text)

        with tempfile.TemporaryDirectory() as tmp_dir:
            summary_path = Path(tmp_dir) / "memory_summary.md"
            index_path = Path(tmp_dir) / "MEMORY.md"
            summary_path.write_text(result.text, encoding="utf-8")
            index_path.write_text(SAMPLE_MEMORY_INDEX, encoding="utf-8")
            parsed = build_overview.parse_codex_native_memory_summary(summary_path, index_path)

        self.assertGreater(parsed["counts"]["user_preferences"], 0)
        self.assertGreater(parsed["counts"]["general_tips"], 0)
        self.assertGreater(len(parsed["rows"]), 0)

    def test_preference_rules_keep_action_side_of_arrow(self):
        budget = build_codex_memory_summary.SummaryBudget(
            target_tokens=420,
            warn_tokens=460,
            max_tokens=520,
            profile_tokens=80,
            preferences_tokens=140,
            tips_tokens=100,
            routes_tokens=120,
            max_preferences=3,
            max_tips=3,
            max_route_items=2,
            max_route_keywords=2,
        )

        result = build_codex_memory_summary.build_memory_summary(
            SAMPLE_MEMORY_INDEX,
            SAMPLE_EXISTING_SUMMARY,
            budget,
        )

        self.assertIn("- Answer with the exact value first", result.text)
        self.assertNotIn("when the user asks for a concrete config/runtime value", result.text)

    def test_personal_memory_registry_is_bounded_and_included(self):
        budget = build_codex_memory_summary.SummaryBudget(
            target_tokens=620,
            warn_tokens=680,
            max_tokens=760,
            profile_tokens=90,
            preferences_tokens=100,
            tips_tokens=100,
            routes_tokens=120,
            personal_memory_tokens=220,
            max_preferences=2,
            max_tips=2,
            max_route_items=2,
            max_route_keywords=2,
            max_personal_memory_items=2,
        )
        personal_items = build_codex_memory_summary.parse_personal_memory_registry(
            SAMPLE_PERSONAL_MEMORY_REGISTRY
        )

        result = build_codex_memory_summary.build_memory_summary(
            SAMPLE_MEMORY_INDEX,
            SAMPLE_EXISTING_SUMMARY,
            budget,
            personal_memory_items=personal_items,
        )

        self.assertNotEqual(result.status, "over_budget")
        self.assertIn("### Local personal memory registry", result.text)
        self.assertIn("Default context memory mode", result.text)
        self.assertIn("bucket=durable", result.text)
        self.assertNotIn("Do not inject this", result.text)

    def test_personal_memory_registry_uses_runtime_language_fields(self):
        registry = (
            '{"date":"2026-04-27","source":"nightly_codex","bucket":"durable",'
            '"title":"默认中文标题","title_en":"English runtime title",'
            '"memory_type":"semantic","priority":"high","value_note":"默认中文说明",'
            '"value_note_en":"English runtime note","keywords":["language"]}\n'
        )

        english_items = build_codex_memory_summary.parse_personal_memory_registry(
            registry,
            language="en",
        )
        chinese_items = build_codex_memory_summary.parse_personal_memory_registry(
            registry,
            language="zh",
        )

        self.assertEqual(english_items[0].title, "English runtime title")
        self.assertEqual(english_items[0].value_note, "English runtime note")
        self.assertEqual(chinese_items[0].title, "默认中文标题")
        self.assertEqual(chinese_items[0].value_note, "默认中文说明")


if __name__ == "__main__":
    unittest.main()
