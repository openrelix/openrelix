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

scope: User-level personal asset system design under `~/work/openrelix`, including runtime state placement and nightly behavior.
applies_to: cwd=~/work/openrelix plus user-level Codex state under ~/.codex

## Task 1: Build a local-first personal asset system

### rollout_summary_files

- rollout_summaries/demo.md (updated_at=2026-04-26T12:06:53+00:00)

### keywords

- openrelix, memories, nightly_pipeline.sh, LaunchAgent

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
{"date":"2026-04-26","source":"canonical","bucket":"durable","title":"Default integrated memory mode","memory_type":"procedural","priority":"high","scope":"global","injection_policy":"global_context","value_note":"Local memory stays in the state root, while a compressed bounded summary is synced into host context by default.","keywords":["memory","integrated","state root"]}
{"date":"2026-04-25","source":"canonical","bucket":"session","title":"Backfill command rollout","memory_type":"task","priority":"medium","scope":"global","injection_policy":"global_context","value_note":"Users can copy a multi-day openrelix backfill command from the panel instead of executing shell from the browser.","keywords":["backfill","panel"]}
{"date":"2026-04-24","source":"nightly_codex","bucket":"low_priority","title":"Do not inject this","memory_type":"semantic","priority":"low","value_note":"Low priority items stay out of the bounded context summary.","keywords":["skip"]}
"""


SCOPED_PERSONAL_MEMORY_REGISTRY = """
{"date":"2026-05-06","source":"canonical","bucket":"durable","title":"Global patch preference","memory_type":"procedural","priority":"high","scope":"global","injection_policy":"global_context","value_note":"Use apply_patch first for file edits.","keywords":["patch"]}
{"date":"2026-05-06","source":"canonical","bucket":"durable","title":"Project-only Gradle cleanup","memory_type":"procedural","priority":"high","scope":"project","injection_policy":"project_context","project_label":"Android App","value_note":"Only use this cleanup inside the Android project.","keywords":["gradle"]}
{"date":"2026-05-06","source":"canonical","bucket":"session","title":"Domain-only bridge diagnosis","memory_type":"semantic","priority":"medium","scope":"domain","value_note":"Keep this available through on-demand recall, not global context.","keywords":["bridge"]}
{"date":"2026-05-06","source":"canonical","bucket":"session","title":"Local follow-up","memory_type":"task","priority":"medium","scope":"local","injection_policy":"local_only","value_note":"Keep this out of host context.","keywords":["todo"]}
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
            max_preferences=4,
            max_tips=4,
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
        self.assertEqual(len(parsed["rows"]), 0)
        self.assertIn("No OpenRelix canonical memory entries were selected", result.text)
        self.assertNotIn("Local Codex personal asset system", result.text)

    def test_memory_index_preferences_do_not_enter_host_summary(self):
        budget = build_codex_memory_summary.SummaryBudget(
            target_tokens=420,
            warn_tokens=460,
            max_tokens=520,
            profile_tokens=80,
            preferences_tokens=140,
            tips_tokens=100,
            max_preferences=3,
            max_tips=3,
        )

        result = build_codex_memory_summary.build_memory_summary(
            SAMPLE_MEMORY_INDEX,
            SAMPLE_EXISTING_SUMMARY,
            budget,
        )

        self.assertIn("- Prefer exact runtime evidence and concise action-oriented answers.", result.text)
        self.assertNotIn("- Answer with the exact value first", result.text)
        self.assertNotIn("when the user asks for a concrete config/runtime value", result.text)

    def test_personal_memory_registry_is_bounded_and_included(self):
        budget = build_codex_memory_summary.SummaryBudget(
            target_tokens=620,
            warn_tokens=680,
            max_tokens=760,
            profile_tokens=90,
            preferences_tokens=100,
            tips_tokens=100,
            personal_memory_tokens=220,
            max_preferences=2,
            max_tips=2,
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
        self.assertIn("Default integrated memory mode", result.text)
        self.assertIn("Backfill command rollout", result.text)
        self.assertIn("[global/medium]", result.text)
        self.assertNotIn("[durable/", result.text)
        personal_section = result.text.split("### Local personal memory registry", 1)[1].split("### ", 1)[0]
        self.assertNotIn("  - desc:", personal_section)
        self.assertNotIn("  - learnings:", personal_section)
        self.assertNotIn("Do not inject this", result.text)

    def test_personal_memory_registry_uses_runtime_language_fields(self):
        registry = (
            '{"date":"2026-04-27","source":"canonical","bucket":"durable",'
            '"title":"默认中文标题","title_en":"English runtime title",'
            '"scope":"global","injection_policy":"global_context",'
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

    def test_personal_memory_registry_injects_global_and_project_context(self):
        host_context_items = build_codex_memory_summary.parse_personal_memory_registry(
            SCOPED_PERSONAL_MEMORY_REGISTRY
        )
        all_items = build_codex_memory_summary.parse_personal_memory_registry(
            SCOPED_PERSONAL_MEMORY_REGISTRY,
            host_context_only=False,
        )

        self.assertEqual(
            [item.title for item in host_context_items],
            ["Global patch preference", "Project-only Gradle cleanup"],
        )
        self.assertEqual(len(all_items), 4)
        policies = {item.title: item.injection_policy for item in all_items}
        self.assertEqual(policies["Project-only Gradle cleanup"], "project_context")
        self.assertEqual(policies["Domain-only bridge diagnosis"], "on_demand")
        self.assertEqual(policies["Local follow-up"], "local_only")

    def test_personal_memory_registry_uses_injection_policy_without_bucket(self):
        registry = (
            '{"date":"2026-05-06","source":"canonical",'
            '"title":"Global policy-only memory","memory_type":"procedural","priority":"high",'
            '"scope":"global","injection_policy":"global_context",'
            '"value_note":"This enters global context from policy metadata.","keywords":["global"]}\n'
            '{"date":"2026-05-06","source":"canonical",'
            '"title":"Project policy-only memory","memory_type":"procedural","priority":"high",'
            '"scope":"project","injection_policy":"project_context","project_label":"OpenRelix",'
            '"value_note":"This enters project context from policy metadata.","keywords":["project"]}\n'
        )

        host_context_items = build_codex_memory_summary.parse_personal_memory_registry(registry)
        result = build_codex_memory_summary.build_memory_summary(
            SAMPLE_MEMORY_INDEX,
            SAMPLE_EXISTING_SUMMARY,
            build_codex_memory_summary.SummaryBudget(
                target_tokens=700,
                warn_tokens=760,
                max_tokens=840,
                personal_memory_tokens=320,
                max_personal_memory_items=0,
            ),
            personal_memory_items=host_context_items,
        )

        self.assertEqual(
            [item.title for item in host_context_items],
            ["Global policy-only memory", "Project policy-only memory"],
        )
        self.assertIn("Global policy-only memory - This enters global context from policy metadata.", result.text)
        self.assertIn("Project policy-only memory - This enters project context from policy metadata.", result.text)
        self.assertNotIn("[durable", result.text.lower())

    def test_personal_memory_registry_prefers_hotter_items_without_bucket_bias(self):
        registry = (
            '{"date":"2026-05-06","source":"canonical","bucket":"durable",'
            '"title":"Single high item","memory_type":"procedural","priority":"high",'
            '"scope":"global","injection_policy":"global_context",'
            '"value_note":"Seen once.","keywords":["once"]}\n'
            '{"date":"2026-05-01","source":"canonical","bucket":"session",'
            '"title":"Repeated high item","memory_type":"procedural","priority":"high",'
            '"scope":"project","injection_policy":"project_context","project_label":"OpenRelix",'
            '"value_note":"Older but hotter.","keywords":["hot"]}\n'
            '{"date":"2026-05-02","source":"canonical","bucket":"session",'
            '"title":"Repeated high item","memory_type":"procedural","priority":"high",'
            '"scope":"project","injection_policy":"project_context","project_label":"OpenRelix",'
            '"value_note":"Newer repeat.","keywords":["hot"]}\n'
        )

        items = build_codex_memory_summary.parse_personal_memory_registry(registry)

        self.assertEqual([item.title for item in items], ["Repeated high item", "Single high item"])
        self.assertEqual(items[0].occurrence_count, 2)

    def test_unified_summary_includes_global_and_project_context_without_task_groups(self):
        personal_items = build_codex_memory_summary.parse_personal_memory_registry(
            (
                '{"date":"2026-05-06","source":"canonical","bucket":"durable",'
                '"title":"Global patch preference","memory_type":"procedural","priority":"high",'
                '"scope":"global","injection_policy":"global_context",'
                '"value_note":"Use apply_patch first for file edits.","keywords":["patch"]}\n'
                '{"date":"2026-05-06","source":"canonical","bucket":"durable",'
                '"title":"OpenRelix worktree delivery","memory_type":"procedural","priority":"high",'
                '"scope":"project","injection_policy":"project_context","project_label":"OpenRelix",'
                '"value_note":"Use an isolated worktree for OpenRelix changes.","keywords":["openrelix"]}\n'
                '{"date":"2026-05-06","source":"canonical","bucket":"durable",'
                '"title":"Douyin search workflow","memory_type":"procedural","priority":"high",'
                '"scope":"project","injection_policy":"project_context","project_label":"Douyin",'
                '"value_note":"Use Douyin search workflow for Android search tasks.","keywords":["douyin"]}\n'
            ),
        )
        budget = build_codex_memory_summary.SummaryBudget(
            target_tokens=900,
            warn_tokens=1000,
            max_tokens=1100,
            profile_tokens=90,
            preferences_tokens=100,
            tips_tokens=100,
            personal_memory_tokens=360,
            max_preferences=2,
            max_tips=2,
            max_personal_memory_items=0,
        )

        result = build_codex_memory_summary.build_memory_summary(
            SAMPLE_MEMORY_INDEX,
            SAMPLE_EXISTING_SUMMARY,
            budget,
            personal_memory_items=personal_items,
        )

        self.assertIn("Global patch preference", result.text)
        self.assertIn("OpenRelix worktree delivery", result.text)
        self.assertIn("Douyin search workflow", result.text)
        self.assertNotIn("Local Codex personal asset system", result.text)
        self.assertNotIn("Android scan QR-only cleanup", result.text)

    def test_summary_builds_compact_profile_from_recurring_project_contexts(self):
        personal_items = build_codex_memory_summary.parse_personal_memory_registry(
            (
                '{"date":"2026-05-06","source":"canonical","bucket":"durable",'
                '"title":"Prefer compact summaries","memory_type":"preference","priority":"high",'
                '"scope":"global","injection_policy":"global_context",'
                '"value_note":"Keep host context concise.","keywords":["compact"]}\n'
                '{"date":"2026-05-06","source":"canonical","bucket":"durable",'
                '"title":"OpenRelix worktree delivery","memory_type":"procedural","priority":"high",'
                '"scope":"project","injection_policy":"project_context","project_label":"OpenRelix",'
                '"value_note":"Use an isolated worktree for OpenRelix changes.","keywords":["openrelix"]}\n'
                '{"date":"2026-05-06","source":"canonical","bucket":"durable",'
                '"title":"Douyin search workflow","memory_type":"procedural","priority":"high",'
                '"scope":"project","injection_policy":"project_context","project_label":"Douyin",'
                '"value_note":"Use Douyin search workflow for Android search tasks.","keywords":["douyin"]}\n'
            )
        )
        result = build_codex_memory_summary.build_memory_summary(
            "",
            "",
            build_codex_memory_summary.SummaryBudget(
                target_tokens=900,
                warn_tokens=1000,
                max_tokens=1100,
                profile_tokens=120,
                preferences_tokens=140,
                tips_tokens=140,
                personal_memory_tokens=360,
                max_preferences=3,
                max_tips=3,
                max_personal_memory_items=0,
            ),
            personal_memory_items=personal_items,
        )

        self.assertIn("## User Profile", result.text)
        self.assertIn("OpenRelix", result.text)
        self.assertIn("Douyin", result.text)
        with tempfile.TemporaryDirectory() as tmp_dir:
            summary_path = Path(tmp_dir) / "memory_summary.md"
            summary_path.write_text(result.text, encoding="utf-8")
            parsed = build_overview.parse_codex_native_memory_summary(summary_path)
        self.assertEqual(parsed["counts"]["user_profile"], 1)

    def test_legacy_source_window_memory_stays_out_of_global_host_context(self):
        legacy_registry = """
{"date":"2026-05-06","source":"legacy","bucket":"durable","title":"Project-only legacy item","memory_type":"procedural","priority":"high","value_note":"Has only old source_window_ids metadata.","source_window_ids":["w-project"]}
{"date":"2026-05-06","source":"legacy","bucket":"durable","title":"Global legacy item","memory_type":"procedural","priority":"high","value_note":"No project source."}
"""

        host_context_items = build_codex_memory_summary.parse_personal_memory_registry(
            legacy_registry
        )
        all_items = build_codex_memory_summary.parse_personal_memory_registry(
            legacy_registry,
            host_context_only=False,
        )

        self.assertEqual([item.title for item in host_context_items], [])
        policies = {item.title: item.injection_policy for item in all_items}
        self.assertEqual(policies["Project-only legacy item"], "on_demand")
        self.assertEqual(policies["Global legacy item"], "on_demand")

    def test_memory_summary_rebuild_does_not_let_existing_summary_suppress_canonical_items(self):
        budget = build_codex_memory_summary.SummaryBudget(
            target_tokens=620,
            warn_tokens=680,
            max_tokens=760,
            profile_tokens=90,
            preferences_tokens=100,
            tips_tokens=100,
            personal_memory_tokens=220,
            max_preferences=2,
            max_tips=2,
            max_personal_memory_items=0,
        )
        existing_summary = SAMPLE_EXISTING_SUMMARY + "\n## General Tips\n\n- Use apply_patch first for file edits.\n"
        personal_items = build_codex_memory_summary.parse_personal_memory_registry(
            """
{"date":"2026-05-06","source":"canonical","bucket":"durable","title":"Apply patch first","memory_type":"procedural","priority":"high","scope":"global","injection_policy":"global_context","value_note":"Use apply_patch first for file edits.","keywords":["patch"]}
{"date":"2026-05-06","source":"canonical","bucket":"durable","title":"Keep runtime state outside repos","memory_type":"procedural","priority":"high","scope":"global","injection_policy":"global_context","value_note":"Runtime state should stay outside working repositories.","keywords":["state"]}
"""
        )

        result = build_codex_memory_summary.build_memory_summary(
            SAMPLE_MEMORY_INDEX,
            existing_summary,
            budget,
            personal_memory_items=personal_items,
        )

        self.assertIn("Apply patch first", result.text)
        self.assertIn("Keep runtime state outside repos", result.text)

    def test_memory_summary_dedupes_personal_memory_already_in_host_summary(self):
        budget = build_codex_memory_summary.SummaryBudget(
            target_tokens=620,
            warn_tokens=680,
            max_tokens=760,
            profile_tokens=90,
            preferences_tokens=100,
            tips_tokens=100,
            personal_memory_tokens=220,
            max_preferences=2,
            max_tips=2,
            max_personal_memory_items=0,
        )
        existing_summary = SAMPLE_EXISTING_SUMMARY + "\n## What's in Memory\n\n- Global patch preference\n"
        personal_items = build_codex_memory_summary.parse_personal_memory_registry(
            SCOPED_PERSONAL_MEMORY_REGISTRY,
        )

        result = build_codex_memory_summary.build_memory_summary(
            SAMPLE_MEMORY_INDEX,
            existing_summary,
            budget,
            personal_memory_items=personal_items,
        )

        self.assertIn("### Local personal memory registry", result.text)
        self.assertIn("Project-only Gradle cleanup", result.text)
        self.assertIn("Project-only Gradle cleanup - Only use this cleanup inside the Android project.", result.text)

    def test_top_summary_items_are_not_repeated_in_personal_registry(self):
        budget = build_codex_memory_summary.SummaryBudget(
            target_tokens=700,
            warn_tokens=760,
            max_tokens=840,
            profile_tokens=90,
            preferences_tokens=120,
            tips_tokens=80,
            personal_memory_tokens=280,
            global_memory_tokens=280,
            project_memory_tokens=0,
            max_preferences=1,
            max_tips=0,
            max_personal_memory_items=0,
        )
        personal_items = build_codex_memory_summary.parse_personal_memory_registry(
            """
{"date":"2026-05-08","source":"canonical","bucket":"durable","title":"Apply patch by default","memory_type":"preference","priority":"high","scope":"global","injection_policy":"global_context","value_note":"Use apply_patch for file edits before shell rewrites.","keywords":["patch"]}
{"date":"2026-05-08","source":"canonical","bucket":"durable","title":"Keep runtime state outside repos","memory_type":"semantic","priority":"high","scope":"global","injection_policy":"global_context","value_note":"Runtime data belongs in the external state root.","keywords":["state"]}
"""
        )

        result = build_codex_memory_summary.build_memory_summary(
            SAMPLE_MEMORY_INDEX,
            SAMPLE_EXISTING_SUMMARY,
            budget,
            personal_memory_items=personal_items,
        )

        personal_section = result.text.split("### Local personal memory registry", 1)[1]
        self.assertIn("- Apply patch by default - Use apply_patch for file edits before shell rewrites.", result.text)
        self.assertNotIn("Apply patch by default", personal_section)
        self.assertIn("Keep runtime state outside repos", personal_section)

    def test_summary_omits_boilerplate_profile_and_dedupes_brief_rules(self):
        budget = build_codex_memory_summary.SummaryBudget(
            target_tokens=720,
            warn_tokens=780,
            max_tokens=860,
            profile_tokens=80,
            preferences_tokens=120,
            tips_tokens=220,
            personal_memory_tokens=220,
            max_preferences=4,
            max_tips=6,
            max_personal_memory_items=8,
        )
        personal_items = build_codex_memory_summary.parse_personal_memory_registry(
            """
{"date":"2026-05-08","source":"canonical","bucket":"durable","title":"多 profile 场景要显式保留环境标识","memory_type":"rule","priority":"high","scope":"global","injection_policy":"global_context","value_note":"恢复、跳转、注入等入口不能只看当前 primary 目录；只要不是系统默认 profile，就应保留 `CODEX_HOME` 或等价的 profile 路由信息。","keywords":["多 profile","多 home","CODEX_HOME","路由"]}
{"date":"2026-05-08","source":"canonical","bucket":"durable","title":"多 profile 恢复命令不能省略 CODEX_HOME","memory_type":"rule","priority":"high","scope":"global","injection_policy":"global_context","value_note":"恢复或唤起命令不要只看当前 primary home；只要不是系统默认 Codex profile，或者属于隔离 profile，就必须显式保留 `CODEX_HOME`。","keywords":["resume","CODEX_HOME","profile","多 home","隔离环境"]}
{"date":"2026-05-08","source":"canonical","bucket":"durable","title":"文件修改默认优先 apply_patch","memory_type":"preference","priority":"high","scope":"global","injection_policy":"global_context","value_note":"用户已经明确偏好用 `apply_patch` 做文件修改。","keywords":["apply_patch"]}
"""
        )

        result = build_codex_memory_summary.build_memory_summary(
            "",
            "",
            budget,
            personal_memory_items=personal_items,
        )

        self.assertNotIn("## User Profile", result.text)
        self.assertNotIn("The injected context is compiled", result.text)
        self.assertNotIn("No profile summary is available yet", result.text)
        self.assertEqual(result.text.count("多 profile"), 1)
        self.assertIn("文件修改默认优先 apply_patch", result.text)

    def test_personal_memory_context_lines_stay_compact_and_keep_metadata(self):
        item = build_codex_memory_summary.PersonalMemoryItem(
            title="Very long personal memory title " * 5,
            bucket="durable",
            memory_type="procedural",
            priority="high",
            value_note="Important implementation boundary " * 10,
            occurrence_count=3,
        )

        lines, used_tokens, _ = build_codex_memory_summary.build_personal_memory_lines(
            [item],
            token_budget=240,
            max_items=0,
        )
        text = "\n".join(lines)

        self.assertGreater(used_tokens, 0)
        self.assertIn("[global/high]", text)
        self.assertNotIn("[durable/", text)
        self.assertIn("(seen 3x)", text)
        self.assertIn("…", text)
        self.assertLess(len(text), 260)


if __name__ == "__main__":
    unittest.main()
