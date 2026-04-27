#!/usr/bin/env python3

import argparse
import csv
from dataclasses import replace
from datetime import date, datetime
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_overview  # noqa: E402
import okeep  # noqa: E402
import asset_runtime  # noqa: E402
import nightly_consolidate  # noqa: E402


def make_memory(title, memory_type="semantic", priority="medium"):
    return {
        "title": title,
        "memory_type": memory_type,
        "priority": priority,
        "value_note": title,
        "source_window_ids": ["w1"],
        "keywords": [title],
    }


def make_window_summary():
    return [
        {
            "window_id": "w1",
            "cwd": "/tmp/demo",
            "question_summary": "demo",
            "question_count": 1,
            "conclusion_count": 1,
            "keywords": [],
            "main_takeaway": "",
        }
    ]


class TextCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    @property
    def text(self):
        return " ".join(part.strip() for part in self.parts if part.strip())


class VisibleTextCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index] == tag:
                self.stack = self.stack[:index]
                return

    def handle_data(self, data):
        if any(tag in {"script", "style", "code"} for tag in self.stack):
            return
        text = " ".join(data.split())
        if text:
            self.parts.append(text)

    @property
    def text(self):
        return " ".join(self.parts)


class NightlyLogicTests(unittest.TestCase):
    def test_entrypoint_module_imports_do_not_create_state_layout(self):
        with TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            env = dict(os.environ)
            env["AI_ASSET_STATE_DIR"] = str(state_dir)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        "sys.path.insert(0, 'scripts'); "
                        "import okeep, build_codex_memory_summary, build_overview, "
                        "collect_codex_activity, nightly_consolidate, token_live_server"
                    ),
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(state_dir.exists())

    def test_runtime_language_config_persists_and_normalizes(self):
        self.assertEqual(asset_runtime.normalize_language("zh-CN"), "zh")
        self.assertEqual(asset_runtime.normalize_language("english"), "en")
        with self.assertRaises(ValueError):
            asset_runtime.normalize_language("fr", strict=True)
        self.assertEqual(asset_runtime.normalize_memory_mode(None), "codex-context")
        self.assertEqual(asset_runtime.normalize_memory_mode(""), "codex-context")
        self.assertEqual(asset_runtime.normalize_memory_mode("record-memory-only"), "local-only")
        self.assertEqual(asset_runtime.normalize_memory_mode("codex"), "codex-context")
        self.assertEqual(asset_runtime.normalize_memory_mode("disabled"), "off")
        with self.assertRaises(ValueError):
            asset_runtime.normalize_memory_mode("cloud", strict=True)

        with TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ,
                {"AI_ASSET_STATE_DIR": tmpdir, "AI_ASSET_LANGUAGE": "", "AI_ASSET_MEMORY_MODE": ""},
            ):
                paths = asset_runtime.get_runtime_paths()
                asset_runtime.ensure_state_layout(paths)
                config = asset_runtime.write_runtime_config(language="en", memory_mode="codex", paths=paths)

                self.assertEqual(config["language"], "en")
                self.assertEqual(config["memory_mode"], "codex-context")
                self.assertTrue(config["personal_memory_enabled"])
                self.assertTrue(config["codex_context_enabled"])
                self.assertEqual(asset_runtime.get_runtime_language(paths), "en")
                self.assertEqual(asset_runtime.get_memory_mode(paths), "codex-context")
                self.assertTrue(asset_runtime.personal_memory_enabled(paths))
                self.assertTrue(asset_runtime.codex_context_enabled(paths))
                self.assertEqual(
                    json.loads((paths.runtime_dir / "config.json").read_text(encoding="utf-8"))["language"],
                    "en",
                )
                config = asset_runtime.write_runtime_config(memory_mode="off", paths=paths)
                self.assertEqual(config["memory_mode"], "off")
                self.assertFalse(config["personal_memory_enabled"])
                self.assertFalse(config["codex_context_enabled"])

    def test_default_state_root_prefers_legacy_slug_only_when_new_root_is_absent(self):
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            app_support = home / "Library" / "Application Support"
            old_root = app_support / "ai-personal-assets"
            new_root = app_support / "openkeepsake"
            old_root.mkdir(parents=True)

            with mock.patch.dict(
                os.environ,
                {"AI_ASSET_STATE_DIR": "", "AI_ASSET_USE_REPO_STATE": ""},
                clear=False,
            ), mock.patch.object(asset_runtime.Path, "home", return_value=home), mock.patch.object(
                asset_runtime.sys, "platform", "darwin"
            ):
                self.assertEqual(asset_runtime.default_state_root(), old_root)
                new_root.mkdir()
                self.assertEqual(asset_runtime.default_state_root(), new_root)

    def test_english_prompt_and_markdown_use_runtime_language(self):
        raw_payload = {
            "date": "2026-04-27",
            "window_count": 1,
            "prompt_count": 2,
            "conclusion_count": 1,
            "windows": [
                {
                    "window_id": "w1",
                    "cwd": "/tmp/demo",
                    "prompt_count": 2,
                    "conclusion_count": 1,
                    "prompts": [
                        {"text": "Install language choice should affect summaries."},
                        {"text": "Install language choice should affect summaries."},
                    ],
                    "conclusions": [{"text": "Persist language in runtime config."}],
                }
            ],
        }

        prompt = nightly_consolidate.build_prompt_with_learning(raw_payload, {}, language="en")
        self.assertIn("Write every generated summary", prompt)
        self.assertIn("[merged 2 similar items]", prompt)
        self.assertNotIn("所有输出都使用中文", prompt)

        fallback = nightly_consolidate.build_fallback_summary(raw_payload, language="en")
        fallback["language"] = "en"
        fallback["stage"] = "manual"
        markdown = nightly_consolidate.render_markdown(fallback, language="en")
        self.assertIn("# Nightly Organization Result", markdown)
        self.assertIn("Long-term Reusable Memories", markdown)

    def test_safe_consolidation_prompt_uses_runtime_language(self):
        prompt = "<daily_compact_json>{}</daily_compact_json>"

        english_prompt = nightly_consolidate.build_safe_consolidation_prompt(prompt, language="en")
        self.assertIn("This is an organization-only task", english_prompt)
        self.assertIn("Output only JSON", english_prompt)
        self.assertNotIn("这是一个纯整理任务", english_prompt)

        chinese_prompt = nightly_consolidate.build_safe_consolidation_prompt(prompt, language="zh")
        self.assertIn("这是一个纯整理任务", chinese_prompt)
        self.assertIn("直接输出符合 schema 的 JSON", chinese_prompt)

    def test_okeep_help_uses_runtime_language(self):
        with mock.patch.object(okeep, "LANGUAGE", "zh"):
            help_text = okeep.build_parser().format_help()
        self.assertIn("OpenKeepsake 命令集", help_text)
        self.assertIn("运行指定日期的 review 流水线并打印摘要", help_text)
        self.assertIn("位置参数", help_text)
        self.assertIn("显示帮助并退出", help_text)
        self.assertNotIn("Run today's review pipeline", help_text)
        self.assertNotIn("optional arguments", help_text)

        with mock.patch.object(okeep, "LANGUAGE", "en"):
            help_text = okeep.build_parser().format_help()
        self.assertIn("OpenKeepsake command set", help_text)
        self.assertIn("Run the review pipeline for a target date", help_text)

    def test_okeep_core_summary_uses_chinese_review_label(self):
        stream = io.StringIO()
        data = {
            "generated_at": "2026-04-28 00:12",
            "metrics": [],
            "nightly": {
                "date": "2026-04-27",
                "day_summary": "开源发布、面板可视化、Codex 记忆分层。",
                "raw_window_count": 1,
                "window_summaries": [],
                "durable_memories": [],
                "session_memories": [],
                "low_priority_memories": [],
            },
        }

        with mock.patch.object(okeep, "LANGUAGE", "zh"), mock.patch("sys.stdout", stream):
            okeep.print_core_summary(data)

        output = stream.getvalue()
        self.assertIn("今日复盘", output)
        self.assertNotIn("今日 Review", output)

    def test_choose_preferred_summary_keeps_existing_on_equal_score_tie(self):
        raw_payload = {
            "window_count": 3,
            "prompt_count": 12,
            "conclusion_count": 4,
        }
        existing = {
            "date": "2026-04-26",
            "generated_at": "2026-04-26T23:00:00+08:00",
            "stage": "preliminary",
            "day_summary": "existing",
            "window_summaries": make_window_summary(),
            "durable_memories": [make_memory("durable-win", memory_type="procedural")],
            "session_memories": [],
            "low_priority_memories": [],
            "keywords": [],
            "next_actions": [],
        }
        candidate = {
            "date": "2026-04-26",
            "generated_at": "2026-04-27T00:10:00+08:00",
            "stage": "preliminary",
            "day_summary": "candidate",
            "window_summaries": make_window_summary(),
            "durable_memories": [],
            "session_memories": [make_memory("session-win", memory_type="task")],
            "low_priority_memories": [
                make_memory("low-a", priority="low"),
                make_memory("low-b", priority="low"),
            ],
            "keywords": [],
            "next_actions": [],
        }

        existing_quality = nightly_consolidate.compute_summary_quality(existing, raw_payload)
        candidate_quality = nightly_consolidate.compute_summary_quality(candidate, raw_payload)
        self.assertEqual(existing_quality["score"], candidate_quality["score"])

        chosen, decision = nightly_consolidate.choose_preferred_summary(
            existing,
            candidate,
            raw_payload,
        )
        self.assertIs(chosen, existing)
        self.assertEqual(decision["decision"], "keep_existing")
        self.assertEqual(decision["reason"], "keep_existing_equal_quality")

    def test_choose_preferred_summary_promotes_final_without_quality_regression(self):
        raw_payload = {
            "window_count": 3,
            "prompt_count": 12,
            "conclusion_count": 4,
        }
        existing = {
            "date": "2026-04-26",
            "generated_at": "2026-04-26T23:00:00+08:00",
            "stage": "manual",
            "day_summary": "existing",
            "window_summaries": make_window_summary(),
            "durable_memories": [make_memory("durable-win", memory_type="procedural")],
            "session_memories": [make_memory("session-win", memory_type="task")],
            "low_priority_memories": [],
            "keywords": ["memory"],
            "next_actions": [],
        }
        candidate = dict(existing)
        candidate["generated_at"] = "2026-04-27T00:10:00+08:00"
        candidate["stage"] = "final"
        candidate["day_summary"] = "candidate"

        chosen, decision = nightly_consolidate.choose_preferred_summary(
            existing,
            candidate,
            raw_payload,
        )

        self.assertIs(chosen, candidate)
        self.assertEqual(decision["decision"], "accept_candidate")
        self.assertEqual(
            decision["reason"],
            "candidate_has_stronger_stage_without_quality_regression",
        )
        self.assertEqual(chosen["stage"], "final")

    def test_selector_keeps_yesterday_primary_and_manual_as_active(self):
        candidates = [
            {
                "date": "2026-04-26",
                "stage": "final",
                "generated_at": "2026-04-27T00:12:00+08:00",
                "_path": "/tmp/2026-04-26/summary.json",
            },
            {
                "date": "2026-04-27",
                "stage": "preliminary",
                "generated_at": "2026-04-27T23:00:00+08:00",
                "_path": "/tmp/2026-04-27/preliminary.json",
            },
            {
                "date": "2026-04-27",
                "stage": "manual",
                "generated_at": "2026-04-27T11:00:00+08:00",
                "_path": "/tmp/2026-04-27/manual.json",
            },
        ]

        primary, active = build_overview.select_primary_and_active_nightly_summaries(
            candidates,
            today=date(2026, 4, 27),
        )
        self.assertIsNotNone(primary)
        self.assertIsNotNone(active)
        self.assertEqual(primary["date"], "2026-04-26")
        self.assertEqual(primary["stage"], "final")
        self.assertEqual(active["date"], "2026-04-27")
        self.assertEqual(active["stage"], "manual")

    def test_memory_view_nightly_uses_active_only_when_memory_payload_exists(self):
        primary = {
            "date": "2026-04-26",
            "stage": "final",
            "durable_memories": [make_memory("stable")],
        }
        partial_active = {
            "date": "2026-04-27",
            "stage": "manual",
            "durable_memories": [],
            "session_memories": [],
            "low_priority_memories": [],
        }
        populated_active = {
            "date": "2026-04-27",
            "stage": "manual",
            "session_memories": [make_memory("active")],
        }

        self.assertIs(build_overview.select_memory_view_nightly(primary, partial_active), primary)
        self.assertIs(build_overview.select_memory_view_nightly(primary, populated_active), populated_active)

    def test_memory_view_falls_back_to_primary_when_active_has_no_memory_payload(self):
        primary = {"date": "2026-04-26", "stage": "final"}
        active = {"date": "2026-04-27", "stage": "manual"}

        self.assertIs(build_overview.select_memory_view_nightly(primary, active), primary)
        self.assertIs(build_overview.select_memory_view_nightly(primary, None), primary)

    def test_display_nightly_prefers_active_when_present(self):
        primary = {"date": "2026-04-26", "stage": "final"}
        active = {
            "date": "2026-04-27",
            "stage": "manual",
            "session_memories": [make_memory("active")],
        }

        self.assertIs(build_overview.select_display_nightly(primary, active), active)
        self.assertIs(build_overview.select_display_nightly(primary, None), primary)

    def test_asset_table_title_links_to_artifact_without_visible_path(self):
        with TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / "asset.md"
            artifact.write_text("demo", encoding="utf-8")

            html = build_overview.make_asset_rows(
                [
                    {
                        "id": "demo_asset",
                        "title": "Demo Asset",
                        "value_note": "Reusable demo asset.",
                        "display_type": "方法",
                        "display_context": "Demo",
                        "display_scope": "仅个人使用",
                        "updated_at": "2026-04-27",
                        "tracked_usage_events": 0,
                        "artifact_paths": [str(artifact)],
                    }
                ]
            )

            self.assertIn('href="{}"'.format(artifact.resolve().as_uri()), html)
            collector = TextCollector()
            collector.feed(html)
            self.assertIn("Demo Asset", collector.text)
            self.assertNotIn(str(artifact), collector.text)

    def test_review_cards_link_to_review_markdown_file(self):
        old_reviews_dir = build_overview.REVIEWS_DIR
        with TemporaryDirectory() as tmpdir:
            reviews_dir = Path(tmpdir) / "reviews"
            review_path = reviews_dir / "2026" / "2026-04-27-demo-review.md"
            review_path.parent.mkdir(parents=True)
            review_path.write_text("# Demo Review\n", encoding="utf-8")

            try:
                build_overview.REVIEWS_DIR = reviews_dir
                html = build_overview.make_review_cards(
                    [
                        {
                            "date": "2026-04-27",
                            "domain": "demo",
                            "task": "Demo Review",
                            "path": str(review_path),
                            "repo": "",
                        }
                    ]
                )
            finally:
                build_overview.REVIEWS_DIR = old_reviews_dir

            self.assertIn('href="{}"'.format(review_path.resolve().as_uri()), html)
            collector = TextCollector()
            collector.feed(html)
            self.assertIn("复盘文件", collector.text)
            self.assertIn("reviews/2026/2026-04-27-demo-review.md", collector.text)

    def test_project_contexts_include_second_level_topics(self):
        window_overview = {
            "date": "2026-04-27",
            "windows": [
                {
                    "project_label": "Android App",
                    "cwd": "/tmp/android-app",
                    "cwd_display": "Android App",
                    "question_count": 1,
                    "conclusion_count": 1,
                    "question_summary": "扫描录制链路还没有打通",
                    "main_takeaway": "录制栈未接完整",
                    "keywords": ["扫一扫", "录制"],
                    "latest_activity_at": "2026-04-27T10:00:00+08:00",
                    "latest_activity_display": "04-27 10:00",
                    "recent_prompts": [],
                    "recent_conclusions": [],
                },
                {
                    "project_label": "Android App",
                    "cwd": "/tmp/android-app",
                    "cwd_display": "Android App",
                    "question_count": 1,
                    "conclusion_count": 1,
                    "question_summary": "视觉搜索 blur 性能需要判断",
                    "main_takeaway": "blurProgress=0 的 blur view 常显值得修",
                    "keywords": ["视搜", "blur"],
                    "latest_activity_at": "2026-04-27T11:00:00+08:00",
                    "latest_activity_display": "04-27 11:00",
                    "recent_prompts": [],
                    "recent_conclusions": [],
                },
            ],
        }

        contexts = build_overview.build_project_contexts(window_overview)

        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["topic_count"], 2)
        topic_labels = {topic["label"] for topic in contexts[0]["topics"]}
        self.assertIn("移动端扫描/录制链路", topic_labels)
        self.assertIn("性能与体验评审", topic_labels)

    def test_context_topic_prefers_domain_rules_and_filters_noisy_titles(self):
        self.assertEqual(
            build_overview.infer_context_topic_label(
                {
                    "question_summary": "帮我 review 长按录制为什么断了",
                    "main_takeaway": "",
                    "keywords": [],
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            ),
            "移动端扫描/录制链路",
        )
        self.assertEqual(
            build_overview.infer_context_topic_label(
                {
                    "question_summary": "[KMP_CLI_LOG] e: file://tmp/MainScreen.kt:1104:19 Unresolved reference 'observe'.",
                    "main_takeaway": "",
                    "keywords": [],
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            ),
            "移动端编译/类型错误",
        )
        self.assertEqual(
            build_overview.infer_context_topic_label(
                {
                    "question_summary": "--latest",
                    "main_takeaway": "",
                    "keywords": [],
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            ),
            "Codex 命令参数",
        )

    def test_project_context_views_scan_recent_days_and_group_windows(self):
        old_raw_daily_dir = build_overview.RAW_DAILY_DIR
        try:
            with TemporaryDirectory() as tmpdir:
                raw_daily_dir = Path(tmpdir)
                raw_daily_dir.mkdir(parents=True, exist_ok=True)
                build_overview.RAW_DAILY_DIR = raw_daily_dir
                for date_str, prompt in [
                    ("2026-04-26", "近 7 天窗口学习需要全量读取"),
                    ("2026-04-27", "面板可视化需要二次归类"),
                ]:
                    (raw_daily_dir / "{}.json".format(date_str)).write_text(
                        json.dumps(
                            {
                                "date": date_str,
                                "window_count": 1,
                                "windows": [
                                    {
                                        "window_id": date_str,
                                        "cwd": "/tmp/OpenKeepsake",
                                        "started_at": "{}T09:00:00+08:00".format(date_str),
                                        "prompt_count": 1,
                                        "conclusion_count": 1,
                                        "prompts": [
                                            {
                                                "local_time": "{}T09:01:00+08:00".format(date_str),
                                                "text": prompt,
                                            }
                                        ],
                                        "conclusions": [
                                            {
                                                "completed_at": "{}T09:02:00+08:00".format(date_str),
                                                "text": prompt,
                                            }
                                        ],
                                    }
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )

                views = build_overview.build_project_context_views("2026-04-27", max_days=2)
        finally:
            build_overview.RAW_DAILY_DIR = old_raw_daily_dir

        self.assertEqual(views["1"]["window_count"], 1)
        self.assertEqual(views["2"]["window_count"], 2)
        self.assertEqual(views["2"]["source_date_count"], 2)
        self.assertEqual(views["2"]["project_contexts"][0]["topic_count"], 2)

    def test_project_context_hidden_topics_are_expandable(self):
        topics = [
            {
                "label": "Topic {}".format(index),
                "window_count": 1,
                "latest_activity_display": "04-27 1{}:00".format(index),
                "question_preview": "Question {}".format(index),
                "takeaway_preview": "Takeaway {}".format(index),
                "keywords": ["kw{}".format(index)],
            }
            for index in range(6)
        ]

        cards_html = build_overview.make_project_context_cards(
            [
                {
                    "label": "OpenKeepsake",
                    "window_count": 6,
                    "question_count": 6,
                    "conclusion_count": 6,
                    "latest_activity_display": "04-27 20:00",
                    "cwd_preview": "/tmp/OpenKeepsake",
                    "question_preview": "面板可视化需要二次归类",
                    "takeaway_preview": "项目上下文需要支持展开",
                    "keywords": ["panel"],
                    "topics": topics,
                }
            ]
        )

        self.assertEqual(cards_html.count('<article class="context-topic">'), 6)
        self.assertIn("查看更多 2 个主题", cards_html)
        self.assertIn("Show 2 more topics", cards_html)
        self.assertIn("收起更多主题", cards_html)
        self.assertNotIn("窗口明细中展开", cards_html)
        self.assertLess(cards_html.index("Topic 3"), cards_html.index("查看更多 2 个主题"))
        self.assertGreater(cards_html.index("Topic 4"), cards_html.index("查看更多 2 个主题"))

    def test_parse_nightly_summary_date_fails_closed(self):
        self.assertIsNone(build_overview.parse_nightly_summary_date({"date": "bad-date"}))

    def test_parse_codex_native_memory_summary_keeps_command_titles_intact(self):
        sample_summary = """## User preferences

- Prefer exact values first.

## General Tips

- Keep the global layer repo-agnostic.

## What's in Memory

### OpenKeepsake + user-level Codex state

#### 2026-04-26

- `/subreview:run` live contract and independent Codex review loop: /subreview:run, codex exec, temp git repo, 10/10
  - desc: Cross-scope workflow memory for external Codex review requests under a openkeepsake workspace and ~/.codex.
  - learnings: Treat /subreview:run as the validated live entrypoint.
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            index_path = tmp / "MEMORY.md"
            summary_path.write_text(sample_summary, encoding="utf-8")
            index_path.write_text("# Task Group: Demo\n- rollout_summaries/demo.md\n", encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(
                summary_path,
                memory_index_path=index_path,
                known_project_names=["OpenKeepsake", "Android App"],
            )

        self.assertEqual(parsed["counts"]["user_preferences"], 1)
        self.assertEqual(parsed["counts"]["general_tips"], 1)
        self.assertEqual([row["body"] for row in parsed["preference_rows"]], ["Prefer exact values first."])
        self.assertEqual([row["body"] for row in parsed["tip_rows"]], ["Keep the global layer repo-agnostic."])
        self.assertEqual(len(parsed["preference_rows"]), 1)
        self.assertEqual(len(parsed["tip_rows"]), 1)
        self.assertIn("Prefer exact values first", parsed["preference_rows"][0]["body"])
        self.assertIn("Keep the global layer repo-agnostic", parsed["tip_rows"][0]["body"])
        self.assertEqual(len(parsed["rows"]), 1)
        row = parsed["rows"][0]
        self.assertIn("/subreview:run", row["title"])
        self.assertIn("Codex 独立评审循环", row["display_title"])
        self.assertIn("临时 git snapshot", row["display_value_note"])
        self.assertEqual(row["created_at"], "2026-04-26")
        self.assertIn("OpenKeepsake", row["context_labels"])
        self.assertEqual(row["source_fact_label"], "来源文件")

    def test_codex_native_memory_known_english_topics_get_chinese_display_copy(self):
        sample_summary = """## What's in Memory

### OpenKeepsake + user-level Codex state

- Local Codex personal asset system, genericization, and LaunchAgent runtime: OpenKeepsake, AGENTS.md, memories, dashboard
  - desc: User-level personal asset system design under a openkeepsake workspace.
  - learnings: The layered setup separates global rules, repo rules, and local state.

- Codex local configuration, MCP setup, token usage, and plugin marketplace inspection: ~/.codex/config.toml, codex mcp add
  - desc: Machine-specific Codex setup facts for MCP configuration and token usage evidence.

        """

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path)

        display_by_title = {row["title"]: row for row in parsed["rows"]}
        local_row = display_by_title["Local Codex personal asset system, genericization, and LaunchAgent runtime"]
        codex_config_row = display_by_title[
            "Codex local configuration, MCP setup, token usage, and plugin marketplace inspection"
        ]
        self.assertEqual(local_row["display_title"], "本地 OpenKeepsake 系统、通用化与 LaunchAgent 运行时")
        self.assertIn("个人资产系统的分层设计", local_row["display_value_note"])
        self.assertEqual(codex_config_row["display_title"], "Codex 本地配置、MCP、Token 使用与插件市场排查")
        self.assertIn("本机 Codex 环境", codex_config_row["display_value_note"])

    def test_codex_native_memory_summary_bullets_get_chinese_display_body(self):
        sample_summary = """## User preferences

- When runtime behavior depends on the current device state or UI, default to live inspection early.

## General Tips

- Separate repo-code tasks from user-level Codex / OpenKeepsake tasks early; the correct search surface is different.
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="zh")

        self.assertEqual(
            parsed["preference_rows"][0]["display_body"],
            "当运行时行为依赖当前设备状态或 UI 时，优先尽早做现场检查。",
        )
        self.assertEqual(
            parsed["tip_rows"][0]["display_body"],
            "先区分 repo 代码任务和用户级 Codex / OpenKeepsake 任务，两者搜索面不同。",
        )
        self.assertIn("When runtime behavior depends", parsed["preference_rows"][0]["display_body_en"])

    def test_codex_native_memory_english_mode_preserves_english_display_copy(self):
        sample_summary = """## What's in Memory

### OpenKeepsake + user-level Codex state

- Local Codex personal asset system, genericization, and LaunchAgent runtime: OpenKeepsake, AGENTS.md, memories, dashboard
  - desc: User-level personal asset system design under a openkeepsake workspace.
  - learnings: The layered setup separates global rules, repo rules, and local state.
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="en")

        row = parsed["rows"][0]
        self.assertEqual(
            row["display_title"],
            "Local Codex personal asset system, genericization, and LaunchAgent runtime",
        )
        self.assertIn("Summary:", row["display_value_note"])
        self.assertIn("Lessons:", row["display_value_note"])
        self.assertEqual(row["source_fact_label"], "Source file")

    def test_codex_native_memory_keys_include_date_and_detail_context(self):
        sample_summary = """## What's in Memory

### Shared context

#### 2026-04-26

- Repeated title: codex, dashboard

#### 2026-04-27

- Repeated title: codex, dashboard

#### 2026-04-28

- Repeated title: first keyword set
- Repeated title: second keyword set
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path)

        keys = [row["memory_key"] for row in parsed["rows"]]
        self.assertEqual(len(keys), 4)
        self.assertEqual(len(set(keys)), 4)
        self.assertTrue(any("first keyword set" in key for key in keys))
        self.assertTrue(any("second keyword set" in key for key in keys))

    def test_codex_native_memory_counts_only_top_level_preferences_and_tips(self):
        sample_summary = """## User preferences

- Prefer exact values first.
  - Nested detail should not count as another preference.

## General Tips

- Keep the global layer repo-agnostic.
  - Nested detail should not count as another tip.
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path)

        self.assertEqual(parsed["counts"]["user_preferences"], 1)
        self.assertEqual(parsed["counts"]["general_tips"], 1)
        self.assertEqual([row["body"] for row in parsed["preference_rows"]], ["Prefer exact values first."])
        self.assertEqual([row["body"] for row in parsed["tip_rows"]], ["Keep the global layer repo-agnostic."])

    def test_codex_native_memory_nested_bullets_do_not_create_phantom_items(self):
        sample_summary = """## What's in Memory

### /tmp/demo

#### 2026-04-26

- Parent native memory: codex, dashboard
  - Nested detail belongs to the parent item.
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path)

        self.assertEqual(len(parsed["rows"]), 1)
        self.assertEqual(parsed["counts"]["topic_items"], 1)
        self.assertIn("Nested detail belongs", parsed["rows"][0]["value_note"])

    def test_codex_native_memory_card_note_uses_english_generated_labels(self):
        sample_summary = """## What's in Memory

### Shared context

#### Detail group

- Native title: codex, dashboard
  - desc: Stable summary.
  - learnings: Useful workflow.
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path)

        row = parsed["rows"][0]
        self.assertIn("关键词: codex, dashboard", row["value_note"])
        self.assertIn("分组: Detail group", row["value_note"])
        self.assertIn("Keywords: codex, dashboard", row["value_note_en"])
        self.assertIn("Group: Detail group", row["value_note_en"])

        cards_html = build_overview.make_memory_cards(parsed["rows"])

        self.assertIn("Keywords: codex, dashboard", cards_html)
        self.assertIn("Group: Detail group", cards_html)
        english_start = cards_html.index("Keywords: codex, dashboard")
        english_fragment = cards_html[english_start : cards_html.index("</span>", english_start)]
        self.assertNotIn("关键词", english_fragment)
        self.assertNotIn("分组", english_fragment)

    def test_codex_native_memory_non_date_detail_heading_does_not_reuse_previous_date(self):
        sample_summary = """## What's in Memory

### /tmp/demo

#### 2026-04-26

- Dated native memory: codex, dashboard

#### Detail group

- Undated native memory: codex, dashboard
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path)

        rows_by_title = {row["title"]: row for row in parsed["rows"]}
        self.assertEqual(rows_by_title["Dated native memory"]["created_at"], "2026-04-26")
        self.assertEqual(rows_by_title["Undated native memory"]["created_at"], "")
        self.assertEqual(rows_by_title["Undated native memory"]["created_at_display"], "时间未知")

    def test_codex_native_memory_path_extraction_handles_is_file_errors(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            source_file = tmp / "demo.txt"
            source_file.write_text("demo", encoding="utf-8")
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(
                """## What's in Memory

### /tmp/demo

- Demo path memory: {}
""".format(source_file),
                encoding="utf-8",
            )
            resolved_source = source_file.resolve()
            original_is_file = Path.is_file

            def is_file_unavailable(path, *args, **kwargs):
                if Path(path) == resolved_source:
                    raise PermissionError("denied")
                return original_is_file(path, *args, **kwargs)

            with mock.patch.object(Path, "is_file", is_file_unavailable):
                parsed = build_overview.parse_codex_native_memory_summary(summary_path)

        self.assertEqual(len(parsed["rows"]), 1)
        self.assertEqual(parsed["rows"][0]["source_windows"][0]["cwd"], str(resolved_source))

    def test_build_codex_native_memory_comparison_summarizes_shared_contexts(self):
        comparison = build_overview.build_codex_native_memory_comparison(
            [
                {
                    "context_labels": ["OpenKeepsake"],
                    "display_context": "OpenKeepsake",
                }
            ],
            [
                {
                    "context_labels": ["OpenKeepsake"],
                    "display_context": "OpenKeepsake",
                },
                {
                    "context_labels": ["Android App"],
                    "display_context": "Android App",
                },
            ],
            {"user_preferences": 2, "general_tips": 1},
            {"task_group_count": 3, "rollout_reference_count": 4},
        )

        self.assertEqual(comparison["shared_context_count"], 1)
        self.assertIn("主题项 1 条", comparison["note"])
        self.assertIn("共享上下文 OpenKeepsake", comparison["note"])

    def test_codex_native_memory_comparison_localizes_generated_shared_contexts_in_english(self):
        comparison = build_overview.build_codex_native_memory_comparison(
            [
                {
                    "context_labels": ["个人资产系统"],
                    "display_context": "个人资产系统",
                }
            ],
            [
                {
                    "context_labels": ["个人资产系统"],
                    "display_context": "个人资产系统",
                }
            ],
            {"topic_items": 1, "user_preferences": 0, "general_tips": 0, "source_exists": True, "source_readable": True},
            {},
            language="en",
        )

        self.assertIn("shared contexts Personal assets system", comparison["note"])
        self.assertNotIn("shared contexts 个人资产系统", comparison["note"])

    def test_codex_native_memory_comparison_ignores_uncategorized_context_fallback(self):
        comparison = build_overview.build_codex_native_memory_comparison(
            [
                {
                    "context_labels": [],
                    "display_context": "未分类上下文",
                }
            ],
            [
                {
                    "context_labels": [],
                    "display_context": "未分类上下文",
                }
            ],
            {"user_preferences": 0, "general_tips": 0, "source_exists": True, "source_readable": True},
            {},
        )

        self.assertEqual(comparison["shared_context_count"], 0)
        self.assertNotIn("共享上下文 未分类上下文", comparison["note"])

    def test_codex_native_memory_comparison_filters_uncategorized_context_label(self):
        comparison = build_overview.build_codex_native_memory_comparison(
            [
                {
                    "context_labels": ["未分类上下文"],
                    "display_context": "未分类上下文",
                }
            ],
            [
                {
                    "context_labels": ["未分类上下文"],
                    "display_context": "未分类上下文",
                }
            ],
            {"user_preferences": 0, "general_tips": 0, "source_exists": True, "source_readable": True},
            {},
        )

        self.assertEqual(comparison["shared_context_count"], 0)
        self.assertNotIn("共享上下文 未分类上下文", comparison["note"])

    def test_codex_native_memory_comparison_distinguishes_empty_source_from_missing(self):
        sample_summary = """## User preferences

- Prefer exact values first.

## General Tips

- Keep the global layer repo-agnostic.
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path)
            empty_comparison = build_overview.build_codex_native_memory_comparison(
                parsed["rows"],
                [],
                parsed["counts"],
                {},
                summary_path_label="custom-codex/memories/memory_summary.md",
            )

            missing = build_overview.parse_codex_native_memory_summary(tmp / "missing.md")
            missing_comparison = build_overview.build_codex_native_memory_comparison(
                missing["rows"],
                [],
                missing["counts"],
                {},
                summary_path_label="custom-codex/memories/memory_summary.md",
            )

        self.assertTrue(parsed["counts"]["source_exists"])
        self.assertIn("已读取 custom-codex/memories/memory_summary.md", empty_comparison["note"])
        self.assertIn("暂无 What's in Memory 主题项", empty_comparison["note"])
        self.assertNotIn("未检测到", empty_comparison["note"])
        self.assertFalse(missing["counts"]["source_exists"])
        self.assertIn("未检测到 custom-codex/memories/memory_summary.md", missing_comparison["note"])

    def test_codex_native_memory_comparison_reports_index_when_summary_missing(self):
        missing_comparison = build_overview.build_codex_native_memory_comparison(
            [],
            [],
            {"topic_items": 0, "user_preferences": 0, "general_tips": 0, "source_exists": False},
            {"source_exists": False, "source_readable": False, "source_error": ""},
            summary_path_label="custom-codex/memories/memory_summary.md",
            index_path_label="custom-codex/memories/MEMORY.md",
        )

        self.assertIn("未检测到 custom-codex/memories/memory_summary.md", missing_comparison["note"])
        self.assertIn("custom-codex/memories/MEMORY.md 未检测到", missing_comparison["note"])

    def test_codex_native_memory_highlight_preserves_empty_source_state(self):
        empty_highlight = build_overview.build_codex_native_memory_highlight(
            {"topic_items": 0, "user_preferences": 1, "general_tips": 1, "source_exists": True},
            {
                "note": (
                    "已读取 custom-codex/memories/memory_summary.md；"
                    "暂无 What's in Memory 主题项；偏好 1 条；通用 tips 1 条。"
                )
            },
            "custom-codex/memories/memory_summary.md",
        )
        missing_highlight = build_overview.build_codex_native_memory_highlight(
            {"topic_items": 0, "user_preferences": 0, "general_tips": 0, "source_exists": False},
            {},
            "custom-codex/memories/memory_summary.md",
        )
        missing_with_index_note = build_overview.build_codex_native_memory_highlight(
            {"topic_items": 0, "user_preferences": 0, "general_tips": 0, "source_exists": False},
            {
                "note": (
                    "未检测到 custom-codex/memories/memory_summary.md；"
                    "custom-codex/memories/MEMORY.md 未检测到，任务组统计暂不可用。"
                )
            },
            "custom-codex/memories/memory_summary.md",
        )

        self.assertIn("Codex 原生记忆摘要已读取", empty_highlight)
        self.assertIn("暂无 What's in Memory 主题项", empty_highlight)
        self.assertNotIn("尚未读到", empty_highlight)
        self.assertIn("尚未读到 custom-codex/memories/memory_summary.md", missing_highlight)
        self.assertIn("Codex 原生记忆摘要暂不可用", missing_with_index_note)
        self.assertIn("custom-codex/memories/MEMORY.md 未检测到", missing_with_index_note)

    def test_unreadable_codex_native_memory_summary_fails_closed(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text("## What's in Memory\n\n- demo memory\n", encoding="utf-8")
            original_read_text = Path.read_text

            def unreadable_summary(path, *args, **kwargs):
                if Path(path) == summary_path:
                    raise PermissionError("denied")
                return original_read_text(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", unreadable_summary):
                parsed = build_overview.parse_codex_native_memory_summary(summary_path)

            comparison = build_overview.build_codex_native_memory_comparison(
                parsed["rows"],
                [],
                parsed["counts"],
                {},
                summary_path_label="custom-codex/memories/memory_summary.md",
            )
            highlight = build_overview.build_codex_native_memory_highlight(
                parsed["counts"],
                comparison,
                "custom-codex/memories/memory_summary.md",
            )

        self.assertEqual(parsed["rows"], [])
        self.assertTrue(parsed["counts"]["source_exists"])
        self.assertFalse(parsed["counts"]["source_readable"])
        self.assertIn("无法读取 custom-codex/memories/memory_summary.md", comparison["note"])
        self.assertIn("暂不可用", highlight)

    def test_invalid_utf8_codex_native_memory_summary_fails_closed(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_bytes(b"\xff\xfe\xfa")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path)
            comparison = build_overview.build_codex_native_memory_comparison(
                parsed["rows"],
                [],
                parsed["counts"],
                {},
                summary_path_label="custom-codex/memories/memory_summary.md",
            )

        self.assertEqual(parsed["rows"], [])
        self.assertTrue(parsed["counts"]["source_exists"])
        self.assertFalse(parsed["counts"]["source_readable"])
        self.assertEqual(parsed["counts"]["source_error"], "UnicodeDecodeError")
        self.assertIn("无法读取 custom-codex/memories/memory_summary.md", comparison["note"])

    def test_codex_native_memory_summary_exists_false_return_still_reads_file(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text("## What's in Memory\n\n- Demo readable memory\n", encoding="utf-8")
            original_exists = Path.exists

            def exists_false(path, *args, **kwargs):
                if Path(path) == summary_path:
                    return False
                return original_exists(path, *args, **kwargs)

            with mock.patch.object(Path, "exists", exists_false):
                parsed = build_overview.parse_codex_native_memory_summary(summary_path)

            comparison = build_overview.build_codex_native_memory_comparison(
                parsed["rows"],
                [],
                parsed["counts"],
                {},
                summary_path_label="custom-codex/memories/memory_summary.md",
            )

        self.assertEqual(len(parsed["rows"]), 1)
        self.assertTrue(parsed["counts"]["source_exists"])
        self.assertTrue(parsed["counts"]["source_readable"])
        self.assertIn("主题项 1 条", comparison["note"])
        self.assertNotIn("未检测到", comparison["note"])

    def test_unreadable_codex_memory_index_keeps_overview_available(self):
        sample_summary = """## What's in Memory

### /tmp/demo

#### 2026-04-26

- Demo native memory: codex, dashboard
  - desc: Demo source.
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            index_path = tmp / "MEMORY.md"
            summary_path.write_text(sample_summary, encoding="utf-8")
            index_path.write_text("# Task Group: Demo\n", encoding="utf-8")
            original_read_text = Path.read_text

            def unreadable_index(path, *args, **kwargs):
                if Path(path) == index_path:
                    raise PermissionError("denied")
                return original_read_text(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", unreadable_index):
                parsed = build_overview.parse_codex_native_memory_summary(
                    summary_path,
                    memory_index_path=index_path,
                )
                index_stats = build_overview.load_codex_memory_index_stats(index_path)

            comparison = build_overview.build_codex_native_memory_comparison(
                parsed["rows"],
                [],
                parsed["counts"],
                index_stats,
                summary_path_label="custom-codex/memories/memory_summary.md",
                index_path_label="custom-codex/memories/MEMORY.md",
            )

        self.assertEqual(len(parsed["rows"]), 1)
        self.assertTrue(parsed["counts"]["source_readable"])
        self.assertTrue(index_stats["source_exists"])
        self.assertFalse(index_stats["source_readable"])
        self.assertEqual(index_stats["task_group_count"], 0)
        self.assertIn("custom-codex/memories/MEMORY.md 无法读取", comparison["note"])
        self.assertIn("任务组统计暂不可用", comparison["note"])

    def test_codex_memory_index_exists_false_return_still_reads_file(self):
        sample_summary = """## What's in Memory

### /tmp/demo

- Demo native memory: codex, dashboard
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            index_path = tmp / "MEMORY.md"
            summary_path.write_text(sample_summary, encoding="utf-8")
            index_path.write_text("# Task Group: Demo\n", encoding="utf-8")
            original_exists = Path.exists

            def exists_false(path, *args, **kwargs):
                if Path(path) == index_path:
                    return False
                return original_exists(path, *args, **kwargs)

            with mock.patch.object(Path, "exists", exists_false):
                parsed = build_overview.parse_codex_native_memory_summary(
                    summary_path,
                    memory_index_path=index_path,
                )
                index_stats = build_overview.load_codex_memory_index_stats(index_path)

            comparison = build_overview.build_codex_native_memory_comparison(
                parsed["rows"],
                [],
                parsed["counts"],
                index_stats,
                summary_path_label="custom-codex/memories/memory_summary.md",
                index_path_label="custom-codex/memories/MEMORY.md",
            )

        self.assertEqual(len(parsed["rows"]), 1)
        self.assertTrue(index_stats["source_exists"])
        self.assertTrue(index_stats["source_readable"])
        self.assertEqual(index_stats["source_error"], "")
        self.assertEqual(index_stats["task_group_count"], 1)
        self.assertEqual(len(index_stats["task_groups"]), 1)
        self.assertEqual(index_stats["task_groups"][0]["title"], "Demo")
        self.assertIn("1 个任务组", comparison["note"])

    def test_codex_memory_index_exposes_task_group_rows(self):
        sample_index = """# Task Group: Local Codex personal asset system, genericization, and LaunchAgent runtime

scope: Asset dashboard and local memory runtime.
applies_to: cwd=/tmp/OpenKeepsake

## Task 1: Build overview

### rollout_summary_files

- rollout_summaries/demo.md (thread_id=demo)

### keywords

- OpenKeepsake, dashboard, memory
"""

        with TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "MEMORY.md"
            index_path.write_text(sample_index, encoding="utf-8")

            index_stats = build_overview.load_codex_memory_index_stats(index_path)

        self.assertEqual(index_stats["task_group_count"], 1)
        self.assertEqual(index_stats["rollout_reference_count"], 1)
        self.assertEqual(len(index_stats["task_groups"]), 1)
        row = index_stats["task_groups"][0]
        self.assertEqual(row["title"], "Local Codex personal asset system, genericization, and LaunchAgent runtime")
        self.assertEqual(row["display_title"], "本地 OpenKeepsake 系统、通用化与 LaunchAgent 运行时")
        self.assertIn("Asset dashboard", row["body"])
        self.assertIn("用户级个人资产系统", row["display_body"])
        self.assertIn("Asset dashboard", row["display_body_en"])
        self.assertEqual(row["task_count"], 1)
        self.assertEqual(row["rollout_reference_count"], 1)
        self.assertIn("dashboard", row["keywords"])

    def test_codex_memory_index_task_group_fallback_body_is_bilingual(self):
        sample_index = """# Task Group: Legacy group

## Task 1: Existing work
"""

        with TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "MEMORY.md"
            index_path.write_text(sample_index, encoding="utf-8")

            index_stats = build_overview.load_codex_memory_index_stats(index_path)

        row = index_stats["task_groups"][0]
        self.assertEqual(row["display_body"], "MEMORY.md 中登记的历史任务组。")
        self.assertEqual(row["display_body_en"], "Historical task group registered in MEMORY.md.")

        rows = [dict(row, title="Legacy group {}".format(index)) for index in range(9)]
        cards_html = build_overview.make_memory_cards(
            build_overview.make_codex_native_brief_memory_items(rows, "task_group")
        )

        self.assertIn(
            '<p><span data-lang-only="zh">MEMORY.md 中登记的历史任务组。</span><span data-lang-only="en">Historical task group registered in MEMORY.md.</span></p>',
            cards_html,
        )
        self.assertIn("查看更多 1 条", cards_html)
        self.assertIn("Show 1 more items", cards_html)
        self.assertNotIn("native-brief-heading", cards_html)

    def test_invalid_utf8_codex_memory_index_keeps_overview_available(self):
        sample_summary = """## What's in Memory

### /tmp/demo

- Demo native memory: codex, dashboard
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            index_path = tmp / "MEMORY.md"
            summary_path.write_text(sample_summary, encoding="utf-8")
            index_path.write_bytes(b"\xff\xfe\xfa")

            parsed = build_overview.parse_codex_native_memory_summary(
                summary_path,
                memory_index_path=index_path,
            )
            index_stats = build_overview.load_codex_memory_index_stats(index_path)
            comparison = build_overview.build_codex_native_memory_comparison(
                parsed["rows"],
                [],
                parsed["counts"],
                index_stats,
                summary_path_label="custom-codex/memories/memory_summary.md",
                index_path_label="custom-codex/memories/MEMORY.md",
            )

        self.assertEqual(len(parsed["rows"]), 1)
        self.assertTrue(index_stats["source_exists"])
        self.assertFalse(index_stats["source_readable"])
        self.assertEqual(index_stats["source_error"], "UnicodeDecodeError")
        self.assertIn("custom-codex/memories/MEMORY.md 无法读取", comparison["note"])

    def test_missing_codex_memory_index_is_reported(self):
        sample_summary = """## What's in Memory

### /tmp/demo

- Demo native memory: codex, dashboard
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            index_path = tmp / "missing-MEMORY.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(
                summary_path,
                memory_index_path=index_path,
            )
            index_stats = build_overview.load_codex_memory_index_stats(index_path)
            comparison = build_overview.build_codex_native_memory_comparison(
                parsed["rows"],
                [],
                parsed["counts"],
                index_stats,
                summary_path_label="custom-codex/memories/memory_summary.md",
                index_path_label="custom-codex/memories/MEMORY.md",
            )

        self.assertEqual(len(parsed["rows"]), 1)
        self.assertFalse(index_stats["source_exists"])
        self.assertIn("custom-codex/memories/MEMORY.md 未检测到", comparison["note"])
        self.assertIn("任务组统计暂不可用", comparison["note"])

    def test_codex_native_memory_without_date_heading_uses_unknown_date(self):
        sample_summary = """## What's in Memory

### /tmp/demo

- Demo native memory: codex, dashboard
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")
            parsed = build_overview.parse_codex_native_memory_summary(summary_path)

        self.assertEqual(len(parsed["rows"]), 1)
        self.assertTrue(parsed["counts"]["source_readable"])
        self.assertEqual(parsed["rows"][0]["created_at"], "")
        self.assertEqual(parsed["rows"][0]["created_at_display"], "时间未知")

    def test_build_markdown_renders_codex_native_memory_section(self):
        native_rows = [
            {
                "title": "Native | [demo](https://example.invalid) <b> `memory`",
                "updated_at_display": "2026-04-26",
                "context_labels": ["AI | Personal Assets"],
                "display_context": "OpenKeepsake",
                "value_note": "Demo | value note.",
            }
        ]
        for index in range(13):
            native_rows.append(
                {
                    "title": "Extra native memory {}".format(index),
                    "updated_at_display": "2026-04-26",
                    "context_labels": ["OpenKeepsake"],
                    "display_context": "OpenKeepsake",
                    "value_note": "Extra value note.",
                }
            )

        markdown = build_overview.build_markdown(
            {
                "generated_at": "2026-04-27 15:00",
                "token_usage": {
                    "available": False,
                    "today_total_tokens_display": "0",
                    "seven_day_total_tokens_display": "0",
                },
                "nightly": {},
                "summary": {
                    "total_assets": 0,
                    "active_assets": 0,
                    "task_reviews": 0,
                    "tracked_usage_events": 0,
                    "tracked_minutes_saved": "0 min",
                    "daily_window_count": 0,
                },
                "summary_terms": [],
                "mix": {"type": [], "context": [], "month": []},
                "project_contexts": [],
                "memory_registry": [],
                "codex_native_memory_comparison": {
                    "note": "主题项 1 条；原生偏长期规则，nightly 偏近期整理。",
                    "note_zh": "主题项 1 条；原生偏长期规则，nightly 偏近期整理。",
                    "note_en": "1 topic item; native memory leans toward long-term rules.",
                },
                "codex_memory_summary_path_label": "custom|codex/<x>/memory_summary.md",
                "codex_memory_index_path_label": "custom`codex`/MEMORY.md",
                "codex_native_memory": native_rows,
                "assets": {"recent": [], "top": []},
                "reading_guide": [],
            }
        )

        self.assertIn("## Codex 原生记忆", markdown)
        self.assertIn("Native / demo &lt;b&gt; memory", markdown)
        self.assertIn("AI / Personal Assets", markdown)
        self.assertIn("Demo / value note.", markdown)
        self.assertNotIn("[demo](", markdown)
        self.assertNotIn("<b>", markdown)
        self.assertIn("另有 2 条未展示", markdown)
        self.assertIn("custom / codex/&lt;x&gt;/memory_summary.md", markdown)
        self.assertIn("customcodex/MEMORY.md", markdown)

    def test_build_markdown_prefers_chinese_codex_native_display_fields(self):
        markdown = build_overview.build_markdown(
            {
                "generated_at": "2026-04-27 15:00",
                "token_usage": {
                    "available": False,
                    "today_total_tokens_display": "0",
                    "seven_day_total_tokens_display": "0",
                },
                "nightly": {},
                "summary": {
                    "total_assets": 0,
                    "active_assets": 0,
                    "task_reviews": 0,
                    "tracked_usage_events": 0,
                    "tracked_minutes_saved": "0 min",
                    "daily_window_count": 0,
                },
                "summary_terms": [],
                "mix": {"type": [], "context": [], "month": []},
                "project_contexts": [],
                "memory_registry": [],
                "codex_native_memory_comparison": {"note": "主题项 1 条。"},
                "codex_memory_summary_path_label": "custom-codex/memories/memory_summary.md",
                "codex_memory_index_path_label": "custom-codex/memories/MEMORY.md",
                "codex_native_memory": [
                    {
                        "title": "Local Codex personal asset system, genericization, and LaunchAgent runtime",
                        "display_title": "本地 OpenKeepsake 系统、通用化与 LaunchAgent 运行时",
                        "updated_at_display": "2026-04-26",
                        "context_labels": ["OpenKeepsake"],
                        "display_context": "OpenKeepsake",
                        "value_note": "English note.",
                        "display_value_note": "中文摘要。",
                    }
                ],
                "assets": {"recent": [], "top": []},
                "reading_guide": [],
            }
        )

        self.assertIn("本地 OpenKeepsake 系统、通用化与 LaunchAgent 运行时", markdown)
        self.assertIn("中文摘要", markdown)
        self.assertNotIn("English note", markdown)

    def test_build_markdown_zh_empty_mix_rows_use_chinese_placeholder(self):
        markdown = build_overview.build_markdown(
            {
                "language": "zh",
                "generated_at": "2026-04-27 15:00",
                "token_usage": {
                    "available": False,
                    "today_total_tokens_display": "0",
                    "seven_day_total_tokens_display": "0",
                    "daily_rows": [],
                },
                "nightly": {},
                "summary": {
                    "total_assets": 0,
                    "active_assets": 0,
                    "task_reviews": 0,
                    "tracked_usage_events": 0,
                    "tracked_minutes_saved": "0 分钟",
                    "daily_window_count": 0,
                },
                "summary_terms": [],
                "mix": {"type": [], "context": [], "month": [], "scope": []},
                "project_contexts": [],
                "memory_registry": [],
                "codex_native_memory_comparison": {"note": "暂无原生记忆。"},
                "codex_native_memory": [],
                "assets": {"recent": [], "top": []},
                "reading_guide": [],
            }
        )

        self.assertIn("| 暂无 | 0 |", markdown)
        self.assertIn("| 暂无 | 暂无 | 暂无 | 暂无 | 暂无 |", markdown)
        self.assertIn("| 暂无 | 0 | 0 分钟 | 暂无 | 暂无 |", markdown)
        self.assertNotIn("| none | 0 |", markdown)

    def test_build_markdown_sanitizes_codex_native_memory_fail_closed_note(self):
        markdown = build_overview.build_markdown(
            {
                "generated_at": "2026-04-27 15:00",
                "token_usage": {
                    "available": False,
                    "today_total_tokens_display": "0",
                    "seven_day_total_tokens_display": "0",
                },
                "nightly": {},
                "summary": {
                    "total_assets": 0,
                    "active_assets": 0,
                    "task_reviews": 0,
                    "tracked_usage_events": 0,
                    "tracked_minutes_saved": "0 min",
                    "daily_window_count": 0,
                },
                "summary_terms": [],
                "mix": {"type": [], "context": [], "month": []},
                "project_contexts": [],
                "memory_registry": [],
                "codex_native_memory_comparison": {
                    "note": "无法读取 bad|path <x> `code`，当前仍以 nightly 整理结果为主。"
                },
                "codex_memory_summary_path_label": "custom-codex/memories/memory_summary.md",
                "codex_memory_index_path_label": "custom-codex/memories/MEMORY.md",
                "codex_native_memory": [],
                "assets": {"recent": [], "top": []},
                "reading_guide": [],
            }
        )

        self.assertIn("无法读取 bad / path &lt;x&gt; code", markdown)
        self.assertNotIn("bad|path <x> `code`", markdown)

    def test_build_data_wires_codex_native_memory_and_missing_index_note(self):
        old_paths = build_overview.PATHS
        old_registry_dir = build_overview.REGISTRY_DIR
        old_consolidated_dir = build_overview.CONSOLIDATED_DIR
        old_raw_daily_dir = build_overview.RAW_DAILY_DIR
        old_resolve_ccusage_daily = build_overview.resolve_ccusage_daily
        try:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                state_root = tmp / "state"
                codex_home = tmp / "codex-home"
                memory_dir = codex_home / "memories"
                memory_dir.mkdir(parents=True)
                (memory_dir / "memory_summary.md").write_text(
                    """## What's in Memory

### /tmp/demo

#### 2026-04-26

- Build data native memory: codex, dashboard
""",
                    encoding="utf-8",
                )
                registry_dir = state_root / "registry"
                registry_dir.mkdir(parents=True)
                (registry_dir / "memory_items.jsonl").write_text("", encoding="utf-8")
                consolidated_dir = state_root / "consolidated" / "daily"
                raw_daily_dir = state_root / "raw" / "daily"
                consolidated_dir.mkdir(parents=True)
                raw_daily_dir.mkdir(parents=True)

                build_overview.PATHS = replace(old_paths, state_root=state_root, codex_home=codex_home)
                build_overview.REGISTRY_DIR = registry_dir
                build_overview.CONSOLIDATED_DIR = consolidated_dir
                build_overview.RAW_DAILY_DIR = raw_daily_dir
                build_overview.resolve_ccusage_daily = lambda: {
                    "available": False,
                    "payload": {"daily": [], "totals": {}},
                    "error": "",
                    "fetched_at": "",
                    "window_days": 14,
                }

                data = build_overview.build_data([], [], [])
        finally:
            build_overview.PATHS = old_paths
            build_overview.REGISTRY_DIR = old_registry_dir
            build_overview.CONSOLIDATED_DIR = old_consolidated_dir
            build_overview.RAW_DAILY_DIR = old_raw_daily_dir
            build_overview.resolve_ccusage_daily = old_resolve_ccusage_daily

        self.assertEqual(len(data["codex_native_memory"]), 1)
        self.assertIn("Build data native memory", data["codex_native_memory"][0]["title"])
        source_labels = [
            source.get("label")
            for source in data["codex_native_memory"][0].get("source_files", [])
        ]
        self.assertIn("memory_summary.md", source_labels)
        self.assertIn("MEMORY.md 未检测到", source_labels)
        source_statuses = [
            source.get("status")
            for source in data["codex_native_memory"][0].get("source_files", [])
        ]
        self.assertIn("missing", source_statuses)
        self.assertIn("MEMORY.md 未检测到", data["codex_native_memory_comparison"]["note"])
        html = build_overview.build_html(data)
        self.assertIn("MEMORY.md 未检测到", html)
        self.assertIn('<span class="memory-chip is-muted"', html)
        self.assertNotIn(">MEMORY.md 未检测到</a>", html)

    def test_build_data_uses_primary_date_when_active_memory_view_has_no_date(self):
        old_registry_dir = build_overview.REGISTRY_DIR
        old_consolidated_dir = build_overview.CONSOLIDATED_DIR
        old_raw_daily_dir = build_overview.RAW_DAILY_DIR
        old_resolve_ccusage_daily = build_overview.resolve_ccusage_daily
        old_load_nightly = build_overview.load_primary_and_active_nightly_summaries
        try:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                registry_dir = tmp / "registry"
                registry_dir.mkdir(parents=True)
                (registry_dir / "memory_items.jsonl").write_text("", encoding="utf-8")
                consolidated_dir = tmp / "consolidated" / "daily"
                raw_daily_dir = tmp / "raw" / "daily"
                consolidated_dir.mkdir(parents=True)
                raw_daily_dir.mkdir(parents=True)

                build_overview.REGISTRY_DIR = registry_dir
                build_overview.CONSOLIDATED_DIR = consolidated_dir
                build_overview.RAW_DAILY_DIR = raw_daily_dir
                build_overview.resolve_ccusage_daily = lambda: {
                    "available": False,
                    "payload": {"daily": [], "totals": {}},
                    "error": "",
                    "fetched_at": "",
                    "window_days": 14,
                }
                build_overview.load_primary_and_active_nightly_summaries = lambda: (
                    {
                        "date": "2026-04-26",
                        "stage": "final",
                        "durable_memories": [make_memory("primary")],
                    },
                    {
                        "stage": "manual",
                        "session_memories": [make_memory("active")],
                    },
                )

                data = build_overview.build_data([], [], [])
        finally:
            build_overview.REGISTRY_DIR = old_registry_dir
            build_overview.CONSOLIDATED_DIR = old_consolidated_dir
            build_overview.RAW_DAILY_DIR = old_raw_daily_dir
            build_overview.resolve_ccusage_daily = old_resolve_ccusage_daily
            build_overview.load_primary_and_active_nightly_summaries = old_load_nightly

        self.assertEqual(data["nightly_memory_views"]["session"][0]["created_at"], "2026-04-26")

    def test_markdown_table_cell_is_table_safe(self):
        cell = build_overview.markdown_table_cell("a|b\n[c](https://example.invalid) <tag> `code`")

        self.assertIn("a / b", cell)
        self.assertIn("c &lt;tag&gt; code", cell)
        self.assertNotIn("|", cell)
        self.assertNotIn("\n", cell)

    def test_build_html_renders_codex_native_memory_panel(self):
        html = build_overview.build_html(
            {
                "generated_at": "2026-04-27 15:00",
                "generated_at_iso": "2026-04-27T15:00:00+08:00",
                "token_usage": {
                    "available": False,
                    "daily_rows": [],
                    "today_breakdown": [],
                    "today_date_label": "今日",
                },
                "nightly": {},
                "nightly_title": "夜间整理",
                "summary_terms": [],
                "highlights": [],
                "metrics": [],
                "mix": {"type": [], "context": [], "month": [], "scope": []},
                "project_contexts": [],
                "window_overview": {},
                "memory_registry": [],
                "nightly_memory_views": {"durable": [], "session": [], "low_priority": []},
                "codex_native_memory_counts": {
                    "topic_items": 1,
                    "user_preferences": 0,
                    "general_tips": 0,
                    "source_exists": True,
                    "source_readable": True,
                },
                "codex_native_memory_comparison": {
                    "note": "主题项 1 条；原生偏长期规则，nightly 偏近期整理。",
                    "note_zh": "主题项 1 条；原生偏长期规则，nightly 偏近期整理。",
                    "note_en": "1 topic item; native memory leans toward long-term rules.",
                },
                "codex_memory_summary_path_label": "custom-codex/memories/memory_summary.md",
                "codex_native_memory": [
                    {
                        "title": "Local Codex personal asset system, genericization, and LaunchAgent runtime",
                        "display_title": "本地 OpenKeepsake 系统、通用化与 LaunchAgent 运行时",
                        "display_bucket": "Codex 原生",
                        "display_memory_type": "语义",
                        "display_priority": "中优先",
                        "created_at_display": "2026-04-26",
                        "updated_at_display": "2026-04-26",
                        "occurrence_label": "原生归档",
                        "context_labels": ["OpenKeepsake"],
                        "display_context": "OpenKeepsake",
                        "value_note": "Demo value note.",
                        "display_value_note": "中文卡片摘要。",
                        "source_windows": [],
                        "source_files": [],
                    }
                ],
                "codex_native_preference_rows": [
                    {
                        "display_title": "偏好 1",
                        "display_body": "直接给出关键结论。",
                        "meta": "Codex 原生 · User preferences",
                    }
                ],
                "codex_native_tip_rows": [
                    {
                        "display_title": "通用 tips 1",
                        "display_body": "优先用 rg 查找文件。",
                        "meta": "Codex 原生 · General Tips",
                    }
                ],
                "codex_native_task_groups": [
                    {
                        "display_title": "Local asset system",
                        "display_body": "Asset dashboard and memory runtime.",
                        "meta": "1 个任务；1 个来源",
                        "keywords": ["dashboard"],
                    }
                ],
                "assets": {"recent": [], "top": []},
                "reviews": [],
                "usage_events": [],
                "reading_guide": [],
            }
        )

        self.assertIn("Codex 原生记忆-主题项", html)
        self.assertIn("Codex 原生记忆-偏好", html)
        self.assertIn("Codex 原生记忆-通用 tips", html)
        self.assertIn("Codex 原生记忆-任务组", html)
        self.assertNotIn("memory-card-native", html)
        self.assertNotIn("memory-native-strip", html)
        self.assertIn("本地 OpenKeepsake 系统、通用化与 LaunchAgent 运行时", html)
        self.assertIn("中文卡片摘要", html)
        self.assertIn("Demo value note", html)
        self.assertIn('data-lang-only="en"', html)
        self.assertNotIn(
            '<div class="panel-note"><span data-lang-only="zh">主题项 1 条；原生偏长期规则，nightly 偏近期整理。</span><span data-lang-only="en">1 topic item; native memory leans toward long-term rules.</span></div>',
            html,
        )
        self.assertIn("用户偏好", html)
        self.assertIn("直接给出关键结论。", html)
        self.assertIn("通用 tips", html)
        self.assertIn("优先用 rg 查找文件。", html)
        self.assertIn("任务组", html)
        self.assertIn("Task Groups", html)
        self.assertIn("Local asset system", html)
        self.assertIn("Codex 原生 · 用户偏好", html)
        self.assertIn("Codex Native · User Preferences", html)
        self.assertIn("Codex 原生 · 通用 tips", html)
        self.assertIn("Codex Native · General Tips", html)
        self.assertIn("Codex 原生记忆-任务组", html)
        self.assertIn("关键词：dashboard", html)
        self.assertIn("1 task; 1 source", html)
        self.assertNotIn("1 tasks; 1 sources", html)
        self.assertIn(
            '<div class="review-submeta"><span data-lang-only="zh">首次添加 2026-04-26 · 最近更新 2026-04-26</span><span data-lang-only="en">First added 2026-04-26 · Updated 2026-04-26</span></div>',
            html,
        )
        self.assertIn(
            '<div class="memory-card-label"><span data-lang-only="zh">关联上下文</span><span data-lang-only="en">Related Context</span></div>',
            html,
        )
        self.assertIn(
            '<div class="memory-card-label"><span data-lang-only="zh">最近工作区</span><span data-lang-only="en">Recent Workspace</span></div>',
            html,
        )
        self.assertIn(
            '<div class="memory-card-label"><span data-lang-only="zh">来源窗口</span><span data-lang-only="en">Source Window</span></div>',
            html,
        )
        self.assertIn("Preference 1", html)

    def test_personal_memory_token_widget_shows_bounded_context_budget(self):
        usage = build_overview.build_personal_memory_token_usage(
            [
                {
                    "display_bucket": "个人资产-长期记忆",
                    "bucket": "durable",
                    "display_memory_type": "流程",
                    "display_priority": "高优先",
                    "display_title": "面板区块重叠优先检查顶层 section 间距",
                    "display_value_note": "当面板看起来像模块重叠时，先排查顶层 section 的垂直间距与容器 margin 归属。",
                    "display_context": "OpenKeepsake",
                    "context_labels": ["OpenKeepsake"],
                }
            ],
            "codex-context",
        )

        self.assertTrue(usage["enabled"])
        self.assertEqual(usage["item_count"], 1)
        self.assertGreater(usage["estimated_tokens"], 20)
        self.assertEqual(usage["max_tokens"], 5000)
        self.assertEqual(usage["max_tokens_display"], "5K")
        self.assertEqual(usage["value_display_zh"], "≤ 5K")
        self.assertIn("Codex context", usage["mode_label"])
        self.assertIn("1 条留本地，最多 1 条进摘要", usage["mode_note_zh"])
        widget = build_overview.make_personal_memory_token_widget(usage)
        self.assertIn("memory-token-widget", widget)
        self.assertIn("Codex context 预算", widget)
        self.assertIn("≤ 5K", widget)
        self.assertIn("摘要目标 4.2K / 警戒 4.6K / 上限 5K", widget)
        self.assertIn("1 条留本地，最多 1 条进摘要", widget)

        disabled = build_overview.build_personal_memory_token_usage([], "off")
        self.assertFalse(disabled["enabled"])
        self.assertEqual(build_overview.make_personal_memory_token_widget(disabled), "")

    def test_personal_memory_count_widget_shows_memory_counts(self):
        widget = build_overview.make_personal_memory_count_widget(
            [
                {"bucket": "durable"},
                {"bucket": "durable"},
                {"bucket": "session"},
                {"bucket": "low_priority"},
            ]
        )

        self.assertIn("memory-count-widget", widget)
        self.assertIn("记忆数量", widget)
        self.assertIn("共 4 条", widget)
        self.assertIn("总数", widget)
        self.assertIn("长期", widget)
        self.assertIn(">2</b>", widget)
        self.assertIn("短期", widget)
        self.assertIn("低优先", widget)

    def test_memory_card_generated_fallback_context_chip_is_bilingual(self):
        cards_html = build_overview.make_memory_cards(
            [
                {
                    "title": "Native memory",
                    "display_title": "原生记忆",
                    "value_note": "Native note.",
                    "display_value_note": "原生摘要。",
                    "display_context": "未分类上下文",
                    "context_labels": [],
                    "bucket": "native",
                    "memory_type": "semantic",
                    "priority": "medium",
                }
            ]
        )

        self.assertIn(
            '<span class="memory-chip"><span data-lang-only="zh">未分类上下文</span><span data-lang-only="en">Uncategorized context</span></span>',
            cards_html,
        )

    def test_memory_card_generated_context_rule_chips_are_bilingual(self):
        cards_html = build_overview.make_memory_cards(
            [
                {
                    "title": "Native memory",
                    "display_title": "原生记忆",
                    "value_note": "Native note.",
                    "display_value_note": "原生摘要。",
                    "context_labels": ["OpenKeepsake", "个人资产系统", "Codex 本地环境"],
                    "bucket": "native",
                    "memory_type": "semantic",
                    "priority": "medium",
                }
            ]
        )

        self.assertIn('<span class="memory-chip">OpenKeepsake</span>', cards_html)
        self.assertIn(
            '<span class="memory-chip"><span data-lang-only="zh">个人资产系统</span><span data-lang-only="en">Personal assets system</span></span>',
            cards_html,
        )
        self.assertIn(
            '<span class="memory-chip"><span data-lang-only="zh">Codex 本地环境</span><span data-lang-only="en">Codex local environment</span></span>',
            cards_html,
        )

    def test_build_html_language_switch_defaults_to_chinese(self):
        html = build_overview.build_html(
            {
                "generated_at": "2026-04-27 15:00",
                "generated_at_iso": "2026-04-27T15:00:00+08:00",
                "token_usage": {
                    "available": False,
                    "daily_rows": [],
                    "today_breakdown": [],
                    "today_date_label": "今日",
                },
                "nightly": {},
                "nightly_title": "夜间整理",
                "summary_terms": [],
                "highlights": [],
                "metrics": [],
                "mix": {"type": [], "context": [], "month": [], "scope": []},
                "project_contexts": [],
                "window_overview": {},
                "memory_registry": [],
                "nightly_memory_views": {"durable": [], "session": [], "low_priority": []},
                "codex_native_memory_counts": {
                    "topic_items": 0,
                    "user_preferences": 0,
                    "general_tips": 0,
                    "source_exists": False,
                    "source_readable": False,
                },
                "codex_native_memory_comparison": {"note": "暂无原生记忆。"},
                "codex_native_memory": [],
                "assets": {"recent": [], "top": []},
                "reviews": [],
                "usage_events": [],
                "reading_guide": [],
            }
        )

        self.assertIn('<html lang="zh-CN" data-default-language="zh">', html)
        self.assertIn('<body data-language="zh" data-theme-choice="system">', html)
        self.assertIn('data-language-option="zh" aria-pressed="true"', html)
        self.assertIn('data-language-option="en" aria-pressed="false"', html)
        self.assertIn('"OpenKeepsake 工作台": "OpenKeepsake Workbench"', html)
        self.assertIn(
            '<span class="hero-brand-line"><span data-lang-only="zh">本地优先的 AI 记忆工作台</span><span data-lang-only="en">Local-first memory workbench for AI agents</span></span>',
            html,
        )
        self.assertIn("applyLanguage(defaultLanguage);", html)
        self.assertIn("refreshStatusLanguage();", html)
        self.assertIn('setStatus("live", "", "live_refreshed");', html)
        self.assertIn("window.localStorage", html)

    def test_build_html_language_switch_respects_english_default(self):
        html = build_overview.build_html(
            {
                "language": "en",
                "generated_at": "2026-04-27 15:00",
                "generated_at_iso": "2026-04-27T15:00:00+08:00",
                "token_usage": {
                    "available": False,
                    "daily_rows": [],
                    "today_breakdown": [],
                    "today_date_label": "Today",
                },
                "nightly": {},
                "nightly_title": "Nightly Synthesis",
                "summary_terms": [],
                "highlights": [],
                "metrics": [],
                "mix": {"type": [], "context": [], "month": [], "scope": []},
                "project_contexts": [],
                "window_overview": {},
                "memory_registry": [],
                "nightly_memory_views": {"durable": [], "session": [], "low_priority": []},
                "codex_native_memory_counts": {
                    "topic_items": 0,
                    "user_preferences": 0,
                    "general_tips": 0,
                    "source_exists": False,
                    "source_readable": False,
                },
                "codex_native_memory_comparison": {"note": "No native memory."},
                "codex_native_memory": [],
                "assets": {"recent": [], "top": []},
                "reviews": [],
                "usage_events": [],
                "reading_guide": [],
            }
        )

        self.assertIn('<html lang="en" data-default-language="en">', html)
        self.assertIn('<body data-language="en" data-theme-choice="system">', html)
        self.assertIn('data-language-option="zh" aria-pressed="false"', html)
        self.assertIn('data-language-option="en" aria-pressed="true"', html)

    def test_build_html_reformats_token_units_on_language_switch(self):
        html = build_overview.build_html(
            {
                "generated_at": "2026-04-27 15:00",
                "generated_at_iso": "2026-04-27T15:00:00+08:00",
                "token_usage": {
                    "available": True,
                    "daily_rows": [
                        {
                            "label": "04-27",
                            "value": 180000000,
                            "display": "1.8亿",
                            "tone": "token-daily-high",
                            "details": [
                                {"label": "输入", "value": 160000000, "title": "输入：1.6亿", "meta": "总输入 Token"}
                            ],
                            "details_heading": "04-27 Token 构成",
                        }
                    ],
                    "today_breakdown": [
                        {
                            "label": "输入",
                            "value": 42443000,
                            "display": "4244.3万",
                            "tone": "token-input",
                            "details": [
                                {"label": "输入", "value": 42443000, "title": "输入：4244.3万", "meta": "总输入 Token"}
                            ],
                            "details_heading": "输入详情",
                        }
                    ],
                    "today_total_tokens": 42586000,
                    "today_total_tokens_display": "4258.6万",
                    "seven_day_total_tokens": 3900000000,
                    "seven_day_total_tokens_display": "39.0亿",
                    "today_date_label": "04-27",
                    "summary_cards": [],
                    "overview_note": "近 7 天中 1 天有记录 · 刚刚更新",
                    "refreshed_at": "2026-04-27T15:00:00+08:00",
                    "window_days": 14,
                },
                "nightly": {},
                "nightly_title": "夜间整理",
                "summary_terms": [],
                "highlights": [],
                "metrics": [],
                "mix": {"type": [], "context": [], "month": [], "scope": []},
                "project_contexts": [],
                "window_overview": {},
                "memory_registry": [],
                "nightly_memory_views": {"durable": [], "session": [], "low_priority": []},
                "codex_native_memory_counts": {
                    "topic_items": 0,
                    "user_preferences": 0,
                    "general_tips": 0,
                    "source_exists": False,
                    "source_readable": False,
                },
                "codex_native_memory_comparison": {"note": "暂无原生记忆。"},
                "codex_native_memory": [],
                "assets": {"recent": [], "top": []},
                "reviews": [],
                "usage_events": [],
                "reading_guide": [],
            }
        )

        self.assertIn('const todayTokenValue = tokenTotalDisplay(tokenUsage, "today_total_tokens", "today_total_tokens_display");', html)
        self.assertIn('const sevenDayTokenValue = tokenTotalDisplay(tokenUsage, "seven_day_total_tokens", "seven_day_total_tokens_display");', html)
        self.assertIn("display: compactTokenValue(row.value)", html)
        self.assertIn("prepared.summary_cards = deriveTokenSummaryCards(prepared);", html)
        self.assertNotIn('updateMetricCard(\n          "today_token",\n          tokenUsage.today_total_tokens_display', html)

    def test_product_showcase_chinese_default_has_localized_visible_labels(self):
        html = (ROOT / "docs" / "product-showcase.html").read_text(encoding="utf-8")
        collector = VisibleTextCollector()
        collector.feed(html)
        visible_text = collector.text

        for phrase in [
            "Pain Points",
            "What It Is",
            "Source repo",
            "State root",
            "Context policy",
            "Ownership",
            "Product Tour",
            "Dashboard",
            "Memory Layers",
            "Memory Modes",
            "Context Distribution",
            "General workflows",
            "Project workspace",
            "Review and follow-up",
            "Collect",
            "Classify",
            "Register",
            "Visualize",
            "Usage Tips",
            "Open Source Boundary",
            "Installer / Skills / Templates",
            "Registry / Reviews / Raw / Reports",
            "Secrets / Tokens / Cookies / Raw Logs",
            "MIT License",
            "Copyright",
            "Warranty",
            "A local-first personal asset system",
            "Product previews on this page use sanitized sample data.",
        ]:
            self.assertNotIn(phrase, visible_text)

        for phrase in [
            "它是什么",
            "源码仓库",
            "功能导览",
            "记忆分层",
            "上下文分布",
            "采集",
            "可视化",
            "快速上手",
            "安装器 / 技能 / 模板",
            "MIT 授权",
            "本页产品预览使用脱敏示例数据。",
        ]:
            self.assertIn(phrase, visible_text)

    def test_product_showcase_english_translation_covers_chinese_leaf_tags(self):
        html = (ROOT / "docs" / "product-showcase.html").read_text(encoding="utf-8")

        for phrase in ["记忆候选", "通用流程", "项目工作区", "复盘跟进"]:
            self.assertIn('"' + phrase + '":', html)

    def test_product_showcase_anchor_targets_clear_sticky_nav(self):
        html = (ROOT / "docs" / "product-showcase.html").read_text(encoding="utf-8")

        self.assertIn("--anchor-offset: 92px;", html)
        self.assertIn("scroll-padding-top: var(--anchor-offset);", html)
        self.assertIn("scroll-margin-top: var(--anchor-offset);", html)

    def test_build_overview_import_does_not_create_state_layout(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state_root = tmp / "state"
            codex_home = tmp / "codex-home"
            env = os.environ.copy()
            env["AI_ASSET_STATE_DIR"] = str(state_root)
            env["CODEX_HOME"] = str(codex_home)
            env["PYTHONDONTWRITEBYTECODE"] = "1"

            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        "sys.path.insert(0, {!r}); "
                        "import build_overview; "
                        "print(build_overview.PATHS.state_root)"
                    ).format(str(ROOT / "scripts")),
                ],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertFalse(state_root.exists())
            self.assertFalse((state_root / "registry" / "assets.jsonl").exists())

    def test_build_html_does_not_render_reading_guide_control(self):
        html = build_overview.build_html(
            {
                "generated_at": "2026-04-27 15:00",
                "generated_at_iso": "2026-04-27T15:00:00+08:00",
                "token_usage": {
                    "available": False,
                    "daily_rows": [],
                    "today_breakdown": [],
                    "today_date_label": "今日",
                },
                "nightly": {},
                "nightly_title": "夜间整理",
                "summary_terms": [],
                "highlights": [],
                "metrics": [],
                "mix": {"type": [], "context": [], "month": [], "scope": []},
                "project_contexts": [],
                "window_overview": {},
                "memory_registry": [],
                "nightly_memory_views": {"durable": [], "session": [], "low_priority": []},
                "codex_native_memory_counts": {
                    "topic_items": 0,
                    "user_preferences": 0,
                    "general_tips": 0,
                    "source_exists": False,
                    "source_readable": False,
                },
                "codex_native_memory_comparison": {"note": "暂无原生记忆。"},
                "codex_native_memory": [],
                "assets": {"recent": [], "top": []},
                "reviews": [],
                "usage_events": [],
                "reading_guide": ["看长期可复用资产的增长。"],
            }
        )

        self.assertNotIn('class="hero-guide"', html)
        self.assertNotIn('id="hero-guide-trigger"', html)
        self.assertNotIn('id="hero-reading-guide"', html)
        self.assertNotIn("看长期可复用资产的增长。", html)
        self.assertNotIn("<h2>阅读提示</h2>", html)

    def test_hero_reading_guide_code_is_not_rendered(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")

        self.assertNotIn("hero-guide", source)
        self.assertNotIn("hero-reading-guide", source)
        self.assertNotIn("wireReadingGuideButton", source)

    def test_extra_review_grid_keeps_card_width_aligned(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")

        self.assertIn(".review-grid.content-more-grid {{", source)
        self.assertIn("grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));", source)

    def test_top_assets_and_recent_reviews_use_requested_layouts(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")
        main_start = source.index("{nightly_summary_panel}")
        main_template = source[
            main_start : source.index("</main>", main_start)
        ]
        top_start = main_template.index("{top_assets_header}")
        review_start = main_template.index("{reviews_header}")
        top_section = main_template[
            main_template.rfind("<section", 0, top_start) : main_template.index("</section>", top_start)
        ]
        review_section = main_template[
            main_template.rfind("<section", 0, review_start) : main_template.index("</section>", review_start)
        ]

        self.assertLess(top_start, review_start)
        self.assertNotIn('class="grid two-up"', main_template[top_start:review_start])
        self.assertIn("<table>", top_section)
        self.assertIn("{top_asset_rows}", top_section)
        self.assertIn('class="review-grid review-panel-grid"', review_section)
        self.assertIn("{review_cards}", review_section)

        review_css = source[source.index(".review-panel-grid,") : source.index(".memory-grid {{")]
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr));", review_css)

        cards_html = build_overview.make_review_cards(
            [
                {
                    "date": "2026-04-27",
                    "domain": "demo",
                    "task": "Review {}".format(index),
                    "path": "",
                    "repo": "",
                }
                for index in range(9)
            ]
        )
        self.assertEqual(cards_html.count('<article class="review-card">'), 9)
        self.assertIn("查看更多 1 篇复盘", cards_html)
        self.assertLess(cards_html.index("Review 7"), cards_html.index("查看更多 1 篇复盘"))
        self.assertGreater(cards_html.index("Review 8"), cards_html.index("查看更多 1 篇复盘"))

    def test_memory_sections_stack_and_cards_use_four_columns_with_two_visible_rows(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")
        main_start = source.index("{nightly_summary_panel}")
        main_template = source[
            main_start : source.index("</main>", main_start)
        ]
        stack_start = main_template.index('class="grid memory-stack"')
        memory_stack = main_template[
            stack_start : main_template.index("{low_priority_memory_header}", stack_start)
        ]

        self.assertIn("{durable_memory_header}", memory_stack)
        self.assertIn("{session_memory_header}", memory_stack)
        self.assertNotIn('class="grid two-up"', memory_stack)
        self.assertIn("{low_priority_memory_header}", main_template)
        self.assertIn("{memory_registry_header}", main_template)

        stack_css = source[source.index(".memory-stack {{") : source.index(".review-card {{")]
        self.assertIn("grid-template-columns: 1fr;", stack_css)
        self.assertIn(".memory-stack .memory-grid,", stack_css)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr));", stack_css)

        cards_html = build_overview.make_memory_cards(
            [
                {
                    "title": "Memory {}".format(index),
                    "value_note": "demo",
                    "bucket": "durable",
                    "memory_type": "semantic",
                    "priority": "high",
                }
                for index in range(9)
            ]
        )
        self.assertEqual(cards_html.count('<article class="review-card memory-card">'), 9)
        self.assertIn("查看更多 1 条", cards_html)
        self.assertLess(cards_html.index("Memory 7"), cards_html.index("查看更多 1 条"))
        self.assertGreater(cards_html.index("Memory 8"), cards_html.index("查看更多 1 条"))

    def test_build_html_keeps_requested_dashboard_section_order(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")
        main_start = source.index("{nightly_summary_panel}")
        main_template = source[
            main_start : source.index("</main>", main_start)
        ]

        self.assertIn('class="grid token-summary-row"', main_template)
        self.assertLess(main_template.index("{token_metric_cards}"), main_template.index("{daily_token_panel}"))
        self.assertLess(main_template.index("{token_overview_panel}"), main_template.index("{daily_token_panel}"))
        self.assertLess(main_template.index("{daily_token_panel}"), main_template.index("{insight_section_html}"))
        self.assertLess(main_template.index("{project_context_body}"), main_template.index("{asset_metric_cards}"))
        self.assertLess(main_template.index("{project_context_body}"), main_template.index("{durable_memory_header}"))
        self.assertLess(main_template.index("{durable_memory_header}"), main_template.index("{asset_metric_cards}"))
        for header in (
            "{codex_native_topic_header}",
            "{codex_native_preference_header}",
            "{codex_native_tip_header}",
            "{codex_native_task_group_header}",
        ):
            self.assertLess(main_template.index(header), main_template.index("{asset_metric_cards}"))
        self.assertLess(main_template.index("{asset_metric_cards}"), main_template.index("{type_panel}"))
        self.assertLess(main_template.index("{asset_metric_cards}"), main_template.index("{window_overview_header}"))
        self.assertLess(main_template.index("{type_panel}"), main_template.index("{month_panel}"))
        self.assertLess(main_template.index("{month_panel}"), main_template.index("{scope_panel}"))
        self.assertLess(main_template.index("{scope_panel}"), main_template.index("{domain_panel}"))
        self.assertLess(main_template.index("{usage_rows}"), main_template.index("{window_overview_header}"))

    def test_build_html_uses_light_system_dashboard_style(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")

        self.assertIn("--bg: #f5f5f7;", source)
        self.assertIn("background: var(--surface);", source)
        self.assertIn("font-family: -apple-system, BlinkMacSystemFont", source)
        self.assertNotIn("linear-gradient(135deg, #182225", source)
        self.assertNotIn("radial-gradient", source)
        self.assertNotIn("font-size: clamp", source)
        self.assertNotIn("letter-spacing: 0.08em", source)

    def test_nightly_summary_hides_internal_stage_and_review_like_badges(self):
        html = build_overview.make_nightly_summary_panel(
            "每日整理结果",
            "2026-04-27 · 手动",
            "",
            {
                "date": "2026-04-27",
                "stage": "manual",
                "day_summary": "今天的高信号主题集中在两块。结论已经沉淀。",
                "raw_window_count": 15,
                "durable_memories": [1],
                "session_memories": [1],
                "low_priority_memories": [1],
                "review_like_window_count": 1,
            },
            {"window_count": 15},
            [],
            summary_views=[
                {
                    "date": "2026-04-27",
                    "lead_text": "今天的高信号主题集中在两块",
                    "detail_parts": ["结论已经沉淀"],
                    "context_labels": ["OpenKeepsake"],
                    "stats": [
                        {"label": "窗口", "value": 15},
                        {"label": "长期记忆", "value": 1},
                        {"label": "短期记忆", "value": 1},
                        {"label": "低优先级", "value": 1},
                    ],
                    "note_text": "这些数字来自当前整理结果，用来快速判断今天沉淀了多少内容。",
                    "badges": [],
                }
            ],
            selected_date="2026-04-27",
        )

        self.assertNotIn(">手动<", html)
        self.assertNotIn("review-like", html)
        self.assertIn("<h2 id=\"nightly-summary-title\">每日整理结果</h2>", html)
        self.assertIn('id="nightly-date-input"', html)
        self.assertIn("<select", html)
        self.assertNotIn('type="date"', html)
        self.assertIn('value="2026-04-27" selected>2026/04/27</option>', html)
        self.assertNotIn('class="nightly-meta-row"', html)
        self.assertLess(html.index('id="nightly-summary-title"'), html.index('id="nightly-date-input"'))
        self.assertLess(html.index('id="nightly-date-input"'), html.index('id="nightly-lead"'))

    def test_window_overview_date_control_reuses_daily_summary_style(self):
        html = build_overview.make_window_overview_date_control(
            [
                {"date": "2026-04-27"},
                {"date": "2026-04-26"},
            ],
            "2026-04-26",
        )

        self.assertIn('class="nightly-date-control"', html)
        self.assertIn('class="nightly-date-input"', html)
        self.assertIn('id="window-overview-date-input"', html)
        self.assertIn('aria-label="选择窗口日期"', html)
        self.assertIn('value="2026-04-26" selected>2026/04/26</option>', html)

    def test_backfill_dates_parser_accepts_non_contiguous_dates(self):
        args = argparse.Namespace(
            dates="2026-04-24,2026-04-21 2026-04-23",
            date_from=None,
            date_to="2026-04-27",
            days=0,
        )

        self.assertEqual(
            okeep.resolve_backfill_dates(args),
            ["2026-04-21", "2026-04-23", "2026-04-24"],
        )

    def test_okeep_mode_updates_runtime_config_without_reinstalling(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir(parents=True)
            codex_home = root / "codex"
            paths = replace(okeep.PATHS, state_root=root, runtime_dir=runtime_dir, codex_home=codex_home)
            args = argparse.Namespace(memory_mode="local-only", no_refresh=True, json=True)

            with mock.patch.object(okeep, "PATHS", paths), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                okeep.command_mode(args)

            config = json.loads((runtime_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["memory_mode"], "local-only")
            self.assertTrue(config["personal_memory_enabled"])
            self.assertFalse(config["codex_context_enabled"])
            codex_config = (codex_home / "config.toml").read_text(encoding="utf-8")
            self.assertIn("memories = false", codex_config)
            self.assertIn('persistence = "save-all"', codex_config)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["memory_mode"], "local-only")
            self.assertTrue(payload["codex_config_updated"])
            self.assertFalse(payload["refreshed"])

    def test_learning_window_dates_are_chronological(self):
        self.assertEqual(
            okeep.learning_window_dates("2026-04-27", 7),
            [
                "2026-04-20",
                "2026-04-21",
                "2026-04-22",
                "2026-04-23",
                "2026-04-24",
                "2026-04-25",
                "2026-04-26",
            ],
        )

    def test_learning_backfill_dates_include_only_missing_dates_with_sources(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            raw_daily_dir = root / "raw" / "daily"
            raw_daily_dir.mkdir(parents=True)
            codex_home = root / "codex"
            codex_home.mkdir()
            consolidated_daily_dir = root / "consolidated" / "daily"
            (consolidated_daily_dir / "2026-04-21").mkdir(parents=True)
            (consolidated_daily_dir / "2026-04-23").mkdir(parents=True)

            (raw_daily_dir / "2026-04-20.json").write_text("{}", encoding="utf-8")
            (raw_daily_dir / "2026-04-21.json").write_text("{}", encoding="utf-8")
            (raw_daily_dir / "2026-04-23.json").write_text("{}", encoding="utf-8")
            (consolidated_daily_dir / "2026-04-21" / "summary.json").write_text(
                json.dumps({"stage": "final"}),
                encoding="utf-8",
            )
            (consolidated_daily_dir / "2026-04-23" / "summary.json").write_text(
                json.dumps({"stage": "manual"}),
                encoding="utf-8",
            )
            history_ts = int(datetime(2026, 4, 22, 12, 0, 0).timestamp())
            (codex_home / "history.jsonl").write_text(
                json.dumps({"ts": history_ts}) + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(
                okeep,
                "PATHS",
                replace(okeep.PATHS, raw_daily_dir=raw_daily_dir, codex_home=codex_home),
            ), mock.patch.object(okeep, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir):
                self.assertEqual(
                    okeep.resolve_learning_backfill_dates("2026-04-27", 7),
                    ["2026-04-20", "2026-04-22", "2026-04-23"],
                )

    def test_review_auto_backfills_before_target_review(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            target_dir = consolidated_daily_dir / "2026-04-27"
            target_dir.mkdir(parents=True)
            (target_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "date": "2026-04-27",
                        "stage": "manual",
                        "day_summary": "done",
                        "window_summaries": [],
                        "durable_memories": [],
                        "session_memories": [],
                        "low_priority_memories": [],
                    }
                ),
                encoding="utf-8",
            )

            calls = []

            def fake_backfill_dates(dates, stage, learn_window_days=0, force=False, verbose=True):
                calls.append(("backfill", dates, stage, learn_window_days, force, verbose))
                return [
                    {
                        "date": date_str,
                        "status": "completed",
                        "summary_json": "",
                        "summary_md": "",
                    }
                    for date_str in dates
                ]

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                calls.append(("review", cmd))

            args = argparse.Namespace(
                date="2026-04-27",
                stage="manual",
                open=False,
                json=False,
                learn_window_days=7,
            )

            with mock.patch.object(okeep, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                okeep,
                "resolve_learning_backfill_dates",
                return_value=["2026-04-20", "2026-04-21"],
            ), mock.patch.object(
                okeep,
                "run_backfill_dates",
                side_effect=fake_backfill_dates,
            ), mock.patch.object(
                okeep,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch("sys.stdout", new_callable=io.StringIO):
                okeep.command_review(args)

            self.assertEqual(calls[0][0], "backfill")
            self.assertEqual(calls[0][1], ["2026-04-20", "2026-04-21"])
            self.assertEqual(calls[0][2], "final")
            self.assertEqual(calls[0][3], 0)
            self.assertIs(calls[0][4], True)
            self.assertEqual(calls[1][0], "review")

    def test_nightly_summary_panel_shows_copyable_backfill_command_for_missing_date(self):
        html = build_overview.make_nightly_summary_panel(
            "每日整理结果",
            "暂无夜间整理结果",
            "",
            {},
            {"window_count": 0},
            [],
            summary_views=[],
            selected_date="2026-04-24",
            selectable_dates=["2026-04-24"],
            backfill={
                "missing_dates": ["2026-04-24"],
                "range_command": "okeep backfill --dates '2026-04-24' --stage final --learn-window-days 7",
                "commands_by_date": {
                    "2026-04-24": "okeep backfill --from 2026-04-24 --to 2026-04-24 --stage final --learn-window-days 7",
                },
            },
        )

        self.assertIn("2026/04/24 · 未整理", html)
        self.assertIn('id="nightly-backfill-panel"', html)
        self.assertIn("缺少整理结果", html)
        self.assertIn("okeep backfill --from 2026-04-24 --to 2026-04-24", html)
        self.assertIn("data-backfill-copy=\"single\"", html)

    def test_build_html_wires_window_overview_date_views(self):
        html = build_overview.build_html(
            {
                "generated_at": "2026-04-27 15:00",
                "generated_at_iso": "2026-04-27T15:00:00+08:00",
                "token_usage": {
                    "available": False,
                    "daily_rows": [],
                    "today_breakdown": [],
                    "today_date_label": "今日",
                },
                "nightly": {},
                "nightly_title": "每日整理结果",
                "summary_terms": [],
                "highlights": [],
                "metrics": [],
                "mix": {"type": [], "context": [], "month": [], "scope": []},
                "project_contexts": [],
                "window_overview": {
                    "date": "2026-04-26",
                    "window_count": 1,
                    "source_kind": "daily_capture",
                    "windows": [],
                },
                "window_overview_views": [
                    {
                        "date": "2026-04-26",
                        "heading": "当日窗口概览 · 1",
                        "heading_zh": "当日窗口概览 · 1",
                        "heading_en": "Daily Window Overview · 1",
                        "note": "共 1 个窗口，按最新活动排序，可点开看详情",
                        "note_zh": "共 1 个窗口，按最新活动排序，可点开看详情",
                        "note_en": "1 window sorted by latest activity. Open a card for details",
                        "cards_html": "<p>旧窗口</p>",
                        "cards_html_zh": "<p>旧窗口</p>",
                        "cards_html_en": "<p>Old window</p>",
                    }
                ],
                "window_overview_default_date": "2026-04-26",
                "memory_registry": [],
                "nightly_memory_views": {"durable": [], "session": [], "low_priority": []},
                "daily_summary_views": [],
                "daily_summary_default_date": "",
                "codex_native_memory_counts": {
                    "topic_items": 0,
                    "user_preferences": 0,
                    "general_tips": 0,
                    "source_exists": True,
                    "source_readable": True,
                },
                "codex_native_memory_comparison": {
                    "note": "",
                    "note_zh": "",
                    "note_en": "",
                },
                "codex_memory_summary_path_label": "custom-codex/memories/memory_summary.md",
                "codex_native_memory": [],
                "codex_native_preference_rows": [],
                "codex_native_tip_rows": [],
                "codex_native_task_groups": [],
                "assets": {"recent": [], "top": []},
                "reviews": [],
                "usage_events": [],
                "reading_guide": [],
            }
        )

        self.assertIn('id="window-overview-date-input"', html)
        self.assertIn('id="window-overview-title"', html)
        self.assertIn('id="window-overview-note"', html)
        self.assertIn('id="window-summary-list"', html)
        self.assertIn('"window_overview_default_date": "2026-04-26"', html)
        self.assertIn('"cards_html_zh"', html)
        self.assertIn("旧窗口", html)
        self.assertIn("function renderWindowOverview(dateValue)", html)
        self.assertIn("wireWindowOverviewDateInput();", html)

    def test_daily_summary_view_carries_bilingual_dynamic_fields(self):
        view = build_overview.build_daily_summary_view(
            {
                "date": "2026-04-27",
                "stage": "final",
                "day_summary": "今天沉淀了新的记忆。",
                "raw_window_count": 2,
                "durable_memories": [1],
                "session_memories": [],
                "low_priority_memories": [],
            },
            {"window_count": 2},
            [
                {"label": "个人资产系统"},
                {"label": "Codex 本地环境"},
            ],
        )

        self.assertEqual(view["context_labels"], ["个人资产系统", "Codex 本地环境"])
        self.assertEqual(view["context_labels_zh"], ["个人资产系统", "Codex 本地环境"])
        self.assertEqual(view["context_labels_en"], ["Personal assets system", "Codex local environment"])
        self.assertEqual(view["lead_text"], "今天沉淀了新的记忆")
        self.assertIn("2026-04-27 synthesis captured 2 work windows", view["lead_text_en"])
        self.assertIn("Related contexts: Personal assets system, Codex local environment.", view["detail_parts_en"])
        self.assertEqual(
            view["note_text_en"],
            "These numbers come from the selected synthesis and help estimate how much was captured that day.",
        )

    def test_build_html_daily_summary_payload_supports_english_switch_for_generated_fields(self):
        summary_view = build_overview.build_daily_summary_view(
            {
                "date": "2026-04-27",
                "stage": "final",
                "day_summary": "今天沉淀了新的记忆。",
                "raw_window_count": 2,
                "durable_memories": [1],
                "session_memories": [],
                "low_priority_memories": [],
            },
            {"window_count": 2},
            [
                {"label": "个人资产系统"},
                {"label": "Codex 本地环境"},
            ],
        )
        html = build_overview.build_html(
            {
                "generated_at": "2026-04-27 15:00",
                "generated_at_iso": "2026-04-27T15:00:00+08:00",
                "token_usage": {
                    "available": False,
                    "daily_rows": [],
                    "today_breakdown": [],
                    "today_date_label": "今日",
                },
                "nightly": {},
                "nightly_title": "每日整理结果",
                "summary_terms": [],
                "highlights": [],
                "metrics": [],
                "mix": {"type": [], "context": [], "month": [], "scope": []},
                "project_contexts": [],
                "window_overview": {},
                "memory_registry": [],
                "nightly_memory_views": {"durable": [], "session": [], "low_priority": []},
                "daily_summary_views": [summary_view],
                "daily_summary_default_date": "2026-04-27",
                "codex_native_memory_counts": {
                    "topic_items": 0,
                    "user_preferences": 0,
                    "general_tips": 0,
                    "source_exists": True,
                    "source_readable": True,
                },
                "codex_native_memory_comparison": {
                    "note": "",
                    "note_zh": "",
                    "note_en": "",
                },
                "codex_memory_summary_path_label": "custom-codex/memories/memory_summary.md",
                "codex_native_memory": [],
                "codex_native_preference_rows": [],
                "codex_native_tip_rows": [],
                "codex_native_task_groups": [],
                "assets": {"recent": [], "top": []},
                "reviews": [],
                "usage_events": [],
                "reading_guide": [],
            }
        )

        self.assertIn('"context_labels_en": ["Personal assets system", "Codex local environment"]', html)
        self.assertIn(
            '"note_text_en": "These numbers come from the selected synthesis and help estimate how much was captured that day."',
            html,
        )
        self.assertIn('"lead_text_en": "2026-04-27 synthesis captured 2 work windows', html)
        self.assertIn('getLocalizedSummaryText(summary, "lead_text")', html)
        self.assertIn('getLocalizedSummaryList(summary, "detail_parts")', html)
        self.assertIn('getLocalizedSummaryList(summary, "context_labels")', html)
        self.assertIn('getLocalizedSummaryText(summary, "note_text")', html)

    def test_daily_token_panel_uses_bar_rows_newest_first(self):
        rows = [
            {"label": "04-26", "value": 1090000000, "display": "10.9亿", "tone": "token-daily-high"},
            {"label": "04-27", "value": 380000000, "display": "3.8亿", "tone": "token-daily-low"},
        ]
        html = build_overview.make_bar_group(
            "每日 Token 消耗",
            list(reversed(rows)),
            "slate",
            rows_id="daily-token-rows",
        )

        self.assertIn('<div class="bar-group" id="daily-token-rows">', html)
        self.assertLess(html.index(">04-27<"), html.index(">04-26<"))
        self.assertIn("width:100%", html)
        self.assertIn("bar-fill token-daily-high", html)
        self.assertIn("bar-fill token-daily-low", html)
        self.assertNotIn("trend-", html)

    def test_token_usage_view_includes_overview_and_hover_details(self):
        view = build_overview.build_token_usage_view(
            {
                "available": True,
                "payload": {
                    "daily": [
                        {
                            "date": "Apr 26, 2026",
                            "inputTokens": 1000,
                            "cachedInputTokens": 250,
                            "outputTokens": 100,
                            "reasoningOutputTokens": 50,
                            "totalTokens": 1150,
                        },
                        {
                            "date": "Apr 27, 2026",
                            "inputTokens": 2000,
                            "cachedInputTokens": 1500,
                            "outputTokens": 300,
                            "reasoningOutputTokens": 100,
                            "totalTokens": 2400,
                        },
                    ]
                },
                "error": "",
                "fetched_at": "2026-04-27T12:00:00+08:00",
                "window_days": 14,
            },
            language="zh",
        )

        self.assertEqual(view["today_total_tokens"], 2400)
        self.assertIn("近 7 天中 2 天有记录", view["overview_note"])
        self.assertIn("较上一日", [card["label"] for card in view["summary_cards"]])
        self.assertIn("缓存占输入", [card["label"] for card in view["summary_cards"]])
        self.assertIn("details", view["daily_rows"][-1])
        self.assertIn("占输入", view["daily_rows"][-1]["details"][1]["meta"])
        self.assertIn("details", view["today_breakdown"][1])
        self.assertEqual(view["daily_rows"][0]["tone"], "token-daily-mid")
        self.assertEqual(view["daily_rows"][-1]["tone"], "token-daily-high")
        self.assertEqual(
            [row["tone"] for row in view["today_breakdown"]],
            ["token-input", "token-cache", "token-output", "token-reasoning"],
        )

    def test_bar_rows_render_hover_details_when_available(self):
        html = build_overview.make_bar_group(
            "资产类型分布",
            [
                {
                    "label": "自动化",
                    "value": 2,
                    "details": [
                        {
                            "title": "AI 资产概览链路",
                            "meta": "自动化 / 仅个人使用 / OpenKeepsake",
                        },
                        {
                            "title": "夜间整理流水线",
                            "meta": "自动化 / 仅个人使用 / Codex 本地环境",
                        },
                    ],
                }
            ],
            "teal",
        )

        self.assertIn('class="bar-value has-details"', html)
        self.assertIn('tabindex="0"', html)
        self.assertIn('class="bar-detail-popover"', html)
        self.assertIn("AI 资产概览链路", html)
        self.assertIn("夜间整理流水线", html)

    def test_asset_mix_rows_include_detail_items(self):
        assets = [
            {
                "id": "asset-a",
                "title": "A Skill",
                "type": "skill",
                "scope": "personal",
                "domain": "general",
                "display_type": "技能",
                "display_scope": "仅个人使用",
                "display_context": "OpenKeepsake",
            },
            {
                "id": "asset-b",
                "title": "B Skill",
                "type": "skill",
                "scope": "repo",
                "domain": "android",
                "display_type": "技能",
                "display_scope": "仓库场景复用",
                "display_context": "Android App",
            },
        ]

        rows = build_overview.build_asset_mix_rows(
            assets,
            lambda asset: asset.get("type", "unknown"),
            lambda value: build_overview.display_label("type", value),
        )

        self.assertEqual(rows[0]["label"], "技能")
        self.assertEqual(rows[0]["value"], 2)
        self.assertEqual([item["title"] for item in rows[0]["details"]], ["A Skill", "B Skill"])
        self.assertIn("OpenKeepsake", rows[0]["details"][0]["meta"])

    def test_chinese_language_prefers_localized_asset_and_usage_fields(self):
        asset = {
            "id": "lark_whiteboard_cli_playbook",
            "title": "Lark Whiteboard CLI Playbook",
            "display_title": "English display title",
            "title_zh": "飞书画板 CLI 方法",
            "type": "playbook",
            "scope": "personal",
            "domain": "collaboration",
            "status": "active",
            "updated_at": "2026-04-27",
            "value_note": "Verified the local render and dry-run upload path.",
            "display_value_note": "English display note.",
            "value_note_zh": "已验证本地渲染和 dry-run 上传路径。",
            "source_task": "lark-cli whiteboard-cli capability check",
            "source_task_zh": "飞书画板 CLI 能力检查",
            "notes": "Sanitized command-level workflow only.",
            "notes_zh": "只保留脱敏后的命令级流程。",
        }
        event = {
            "date": "2026-04-27",
            "asset_id": "lark_whiteboard_cli_playbook",
            "task": "lark-cli whiteboard-cli capability check",
            "task_zh": "飞书画板 CLI 能力检查",
            "minutes_saved": 10,
            "note": "Existing skill provided the workflow.",
            "note_zh": "已有技能提供了验证流程。",
        }

        enriched = build_overview.enrich_assets(
            [asset],
            {"lark_whiteboard_cli_playbook": [event]},
            [],
            language="zh",
        )[0]
        enriched_event = build_overview.enrich_usage_events([event], language="zh")[0]

        self.assertEqual(enriched["display_title"], "飞书画板 CLI 方法")
        self.assertEqual(enriched["display_title_en"], "Lark Whiteboard CLI Playbook")
        self.assertEqual(enriched["display_value_note"], "已验证本地渲染和 dry-run 上传路径。")
        self.assertEqual(enriched["display_source_task"], "飞书画板 CLI 能力检查")
        self.assertEqual(enriched_event["display_task"], "飞书画板 CLI 能力检查")

        asset_rows = build_overview.make_asset_rows([enriched])
        usage_rows = build_overview.make_usage_rows([enriched_event])
        self.assertIn("飞书画板 CLI 方法", asset_rows)
        self.assertIn("已验证本地渲染和 dry-run 上传路径。", asset_rows)
        self.assertNotIn("Lark Whiteboard CLI Playbook", asset_rows)
        self.assertNotIn("English display title", asset_rows)
        self.assertIn("飞书画板 CLI 能力检查", usage_rows)
        self.assertNotIn("lark-cli whiteboard-cli capability check", usage_rows)

    def test_asset_csv_keeps_canonical_enum_columns_and_display_columns(self):
        data = {
            "assets": {
                "recent": [
                    {
                        "id": "demo",
                        "display_title": "飞书画板 CLI 方法",
                        "type": "playbook",
                        "display_type": "方法",
                        "domain": "collaboration",
                        "display_domain": "协作沟通",
                        "scope": "personal",
                        "display_scope": "仅个人使用",
                        "status": "active",
                        "display_status": "活跃",
                        "display_value_note": "中文说明。",
                    }
                ],
                "top": [],
            }
        }

        with TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "overview.csv"
            build_overview.build_csv(data, output_path)
            rows = list(csv.DictReader(output_path.read_text(encoding="utf-8").splitlines()))

        self.assertEqual(rows[0]["title"], "飞书画板 CLI 方法")
        self.assertEqual(rows[0]["type"], "playbook")
        self.assertEqual(rows[0]["display_type"], "方法")
        self.assertEqual(rows[0]["domain"], "collaboration")
        self.assertEqual(rows[0]["display_domain"], "协作沟通")
        self.assertEqual(rows[0]["scope"], "personal")
        self.assertEqual(rows[0]["display_scope"], "仅个人使用")
        self.assertEqual(rows[0]["status"], "active")
        self.assertEqual(rows[0]["display_status"], "活跃")

    def test_memory_review_instructions_follow_runtime_language_for_storage(self):
        skill_text = (ROOT / ".agents" / "skills" / "memory-review" / "SKILL.md").read_text(encoding="utf-8")
        prompt_text = (ROOT / "install" / "templates" / "codex-prompts" / "memory-review.md.tmpl").read_text(
            encoding="utf-8"
        )

        self.assertIn("Resolve runtime language", skill_text)
        self.assertIn("asset `title` / `source_task` / `value_note` / `notes`", skill_text)
        self.assertIn("usage-event `task` / `note`", prompt_text)

    def test_asset_value_estimation_uses_events_and_recent_windows(self):
        asset = {
            "id": "ai_asset_overview_pipeline",
            "title": "AI 资产概览链路",
            "type": "automation",
            "scope": "personal",
            "domain": "general",
            "updated_at": "2026-04-27",
            "tags": ["overview", "panel"],
            "artifact_paths": ["/tmp/OpenKeepsake/scripts/build_overview.py"],
            "display_type": "自动化",
        }
        events = [
            {
                "asset_id": "ai_asset_overview_pipeline",
                "task": "asset panel hover detail",
                "minutes_saved": 0,
                "note": "Reused the overview panel pipeline.",
            }
        ]
        window_overview = {
            "windows": [
                {
                    "window_id": "w1",
                    "question_summary": "AI 资产概览链路 panel 需要增加价值分估算",
                    "main_takeaway": "build_overview.py 自动估算复用价值",
                    "keywords": ["panel", "overview"],
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            ]
        }

        enriched = build_overview.enrich_assets(
            [asset],
            {"ai_asset_overview_pipeline": events},
            [],
            window_overview=window_overview,
            language="zh",
        )[0]

        self.assertGreater(enriched["estimated_value_score"], 50)
        self.assertGreater(enriched["estimated_minutes_saved"], 0)
        self.assertEqual(enriched["explicit_usage_count"], 1)
        self.assertEqual(enriched["implicit_reuse_matches"], 1)
        self.assertIn("显式复用记录 1 次", enriched["value_signals"])

    def test_top_asset_ranking_ignores_manual_reuse_counters(self):
        manual_only_asset = {
            "id": "manual_only",
            "title": "Manual Only Asset",
            "type": "automation",
            "scope": "personal",
            "domain": "general",
            "updated_at": "2026-04-27",
            "reuse_count": 9999,
            "minutes_saved_total": 9999,
            "tags": ["manual"],
        }
        evidenced_asset = {
            "id": "auto_evidence",
            "title": "Panel Evidence Asset",
            "type": "playbook",
            "scope": "personal",
            "domain": "general",
            "updated_at": "2026-04-27",
            "reuse_count": 0,
            "minutes_saved_total": 0,
            "tags": ["panel", "overview"],
        }
        events = {
            "auto_evidence": [
                {
                    "asset_id": "auto_evidence",
                    "task": "panel overview value ranking review",
                    "minutes_saved": 0,
                    "note": "Reused panel overview evidence.",
                }
            ]
        }
        window_overview = {
            "windows": [
                {
                    "window_id": "w1",
                    "question_summary": "Panel Evidence Asset 需要支持 overview 价值排序",
                    "main_takeaway": "panel overview evidence drove the ranking",
                    "keywords": ["panel", "overview"],
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            ]
        }

        enriched = build_overview.enrich_assets(
            [manual_only_asset, evidenced_asset],
            events,
            [],
            window_overview=window_overview,
            language="zh",
        )
        ranked = build_overview.sort_top_assets(enriched)

        self.assertEqual(ranked[0]["id"], "auto_evidence")
        self.assertEqual(ranked[0]["manual_reuse_count"], 0)
        self.assertEqual(ranked[1]["manual_reuse_count"], 9999)

    def test_live_token_refresh_keeps_daily_rows_as_newest_first_bars(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")

        self.assertIn(
            'renderBarRows(elements.dailyTokenRows, (preparedTokenUsage.daily_rows || []).slice().reverse(), "token-daily-mid");',
            source,
        )
        self.assertIn("sanitizeCssClass(row.tone || accentClass, accentClass)", source)
        self.assertIn(".token-input {{", source)
        self.assertIn(".token-reasoning {{", source)
        self.assertNotIn("renderLineChart", source)

    def test_help_popover_keeps_contrast_and_avoids_hero_title(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")

        self.assertIn("      color: var(--ink);\n      box-shadow:", source)
        self.assertIn(".module-help-title {{\n      color: var(--ink);", source)
        self.assertIn("      z-index: 40;\n      width: 212px;", source)
        self.assertIn("width: min(320px, calc(100vw - 44px));", source)
        self.assertIn("@media (min-width: 900px) {{", source)
        self.assertIn(".nightly-title-row .module-help-card {{", source)
        self.assertIn("left: calc(100% + 12px);", source)
        self.assertIn("transform: translateX(8px);", source)

    def test_recent_window_learning_batches_all_windows_but_caps_samples(self):
        old_raw_dir = nightly_consolidate.RAW_DIR
        old_consolidated_dir = nightly_consolidate.CONSOLIDATED_DIR
        try:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                nightly_consolidate.RAW_DIR = tmp / "raw"
                nightly_consolidate.CONSOLIDATED_DIR = tmp / "consolidated" / "daily"

                raw_daily_dir = nightly_consolidate.RAW_DIR / "daily"
                raw_daily_dir.mkdir(parents=True)
                windows = []
                for index in range(25):
                    windows.append(
                        {
                            "window_id": "w{}".format(index),
                            "cwd": "/tmp/project-{}".format(index % 3),
                            "prompt_count": 1,
                            "conclusion_count": 1,
                            "prompts": [{"text": "question {}".format(index)}],
                            "conclusions": [{"text": "takeaway {}".format(index)}],
                        }
                    )
                (raw_daily_dir / "2026-04-26.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-26",
                            "window_count": len(windows),
                            "windows": windows,
                        }
                    ),
                    encoding="utf-8",
                )

                learning = nightly_consolidate.build_recent_window_learning("2026-04-27", 1)
        finally:
            nightly_consolidate.RAW_DIR = old_raw_dir
            nightly_consolidate.CONSOLIDATED_DIR = old_consolidated_dir

        self.assertEqual(learning["raw_window_count"], 25)
        self.assertEqual(learning["coverage"]["raw_window_count"], 25)
        self.assertEqual(learning["batch_count"], 2)
        self.assertEqual(len(learning["batch_summaries"]), 2)
        self.assertEqual(sum(batch["window_count"] for batch in learning["batch_summaries"]), 25)
        self.assertEqual(len(learning["window_samples"]), nightly_consolidate.LEARNING_WINDOW_SAMPLE_LIMIT)

        digest = nightly_consolidate.build_learning_context_digest(
            {"recent_window_learning": learning},
            1,
        )
        self.assertEqual(digest["recent_window_learning_scanned_days"], 1)
        self.assertEqual(digest["recent_window_learning_source_dates"], 1)
        self.assertEqual(digest["recent_window_learning_windows"], 25)
        self.assertEqual(digest["recent_window_learning_batches"], 2)

    def test_format_learning_digest_reports_full_coverage_without_window_details(self):
        summary = {
                "learning_context_digest": {
                    "recent_window_learning_days": 7,
                    "recent_window_learning_scanned_days": 7,
                    "recent_window_learning_source_dates": 4,
                    "recent_window_learning_windows": 48,
                    "recent_window_learning_batches": 5,
                    "recent_window_learning_samples": 12,
                "recent_window_learning_patterns": 6,
            }
        }

        line = okeep.format_learning_digest(summary)

        self.assertEqual(
            line,
            "窗口学习: 近 7 天 | 扫描: 7 天 | 有窗口日期: 4 天 | 全量历史窗口: 48 | 批次: 5 | 注入样本: 12 | 模式: 6",
        )
        self.assertNotIn("w1", line)


if __name__ == "__main__":
    unittest.main()
