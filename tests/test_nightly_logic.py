#!/usr/bin/env python3

import argparse
import csv
from dataclasses import replace
from datetime import date, datetime
import http.client
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_overview  # noqa: E402
import build_codex_native_display_cache  # noqa: E402
import check_personal_info  # noqa: E402
import openrelix  # noqa: E402
import openrelix_memory_migration  # noqa: E402
import openrelix_task_summary  # noqa: E402
import openrelix_update_worker  # noqa: E402
import asset_runtime  # noqa: E402
import nightly_consolidate  # noqa: E402
import sync_host_memory_summary  # noqa: E402
import token_live_server  # noqa: E402
from openrelix_overview import contract as overview_contract  # noqa: E402
from openrelix_overview import claude_desktop  # noqa: E402
from openrelix_overview import codex_desktop as overview_codex_desktop  # noqa: E402
from openrelix_overview import curated_memory as overview_curated_memory  # noqa: E402
from openrelix_overview import finder as overview_finder  # noqa: E402
from openrelix_overview import memory_context as overview_memory_context  # noqa: E402
from openrelix_overview import memory_feedback as overview_memory_feedback  # noqa: E402
from openrelix_overview import token_fetcher  # noqa: E402


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


def make_runtime_paths_for_test(root):
    root = Path(root)
    return replace(
        openrelix.PATHS,
        state_root=root,
        codex_home=root / "codex-home",
        claude_home=root / "claude-home",
        claude_bin=str(root / "bin" / "claude"),
        raw_dir=root / "raw",
        raw_daily_dir=root / "raw" / "daily",
        raw_windows_dir=root / "raw" / "windows",
        registry_dir=root / "registry",
        reviews_dir=root / "reviews",
        reports_dir=root / "reports",
        consolidated_dir=root / "consolidated",
        consolidated_daily_dir=root / "consolidated" / "daily",
        runtime_dir=root / "runtime",
        nightly_runner_dir=root / "runtime" / "nightly-runner",
        nightly_codex_home=root / "runtime" / "codex-nightly-home",
        nightly_claude_home=root / "runtime" / "claude-nightly-home",
        log_dir=root / "log",
    )


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
    @staticmethod
    def _empty_personal_codex_rules():
        return {
            "title": {},
            "note": {},
            "task_body": {},
            "bullet": {},
            "topic_rules": [],
            "bullet_rules": [],
            "bullet_title_en": {},
            "task_group_label_rules": [],
        }

    def setUp(self):
        original = build_overview._PERSONAL_CODEX_NATIVE_RULES
        build_overview._PERSONAL_CODEX_NATIVE_RULES = self._empty_personal_codex_rules()
        self.addCleanup(lambda: setattr(build_overview, "_PERSONAL_CODEX_NATIVE_RULES", original))
        original_display_cache_path = build_overview.CODEX_NATIVE_DISPLAY_CACHE_PATH
        display_cache_tmpdir = TemporaryDirectory()
        self.addCleanup(display_cache_tmpdir.cleanup)
        build_overview.CODEX_NATIVE_DISPLAY_CACHE_PATH = Path(display_cache_tmpdir.name) / "missing-display-cache.json"
        self.addCleanup(
            lambda: setattr(
                build_overview,
                "CODEX_NATIVE_DISPLAY_CACHE_PATH",
                original_display_cache_path,
            )
        )
        build_overview.load_codex_native_display_cache.cache_clear()
        self.addCleanup(build_overview.load_codex_native_display_cache.cache_clear)

    def test_codex_native_default_rule_tables_stay_empty(self):
        self.assertEqual(check_personal_info.codex_native_rule_table_hits(), [])
        for name in check_personal_info.CODEX_NATIVE_DEFAULT_RULE_TABLES:
            self.assertFalse(getattr(build_overview, name), name)

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
                        "import importlib, pkgutil, sys\n"
                        "sys.path.insert(0, 'scripts')\n"
                        "import openrelix, build_codex_memory_summary, build_overview, "
                        "collect_codex_activity, nightly_consolidate, token_live_server\n"
                        "import openrelix_overview\n"
                        "for module in pkgutil.walk_packages(openrelix_overview.__path__, "
                        "openrelix_overview.__name__ + '.'):\n"
                        "    importlib.import_module(module.name)\n"
                    ),
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(state_dir.exists())

    def test_token_live_server_does_not_import_full_overview_builder(self):
        with TemporaryDirectory() as tmpdir:
            env = dict(os.environ)
            env["AI_ASSET_STATE_DIR"] = str(Path(tmpdir) / "state")
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        "sys.path.insert(0, 'scripts'); "
                        "import token_live_server; "
                        "print('build_overview' in sys.modules)"
                    ),
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "False")

    def test_overview_contract_validates_generated_report_shape(self):
        with TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            reports_dir = state_dir / "reports"
            reports_dir.mkdir(parents=True)
            overview_data = {
                "schema_version": overview_contract.SCHEMA_VERSION,
                "language": "zh",
                "generated_at": "2026-05-04 12:00:00",
                "summary": {
                    "total_assets": 0,
                    "active_assets": 0,
                    "daily_window_count": 0,
                },
                "metrics": [],
                "mix": {},
                "assets": {},
                "reviews": [],
                "usage_events": [],
                "summary_terms": [],
                "summary_term_views": [],
                "pipeline_status": {},
                "token_usage": {
                    "available": False,
                    "daily_rows": [],
                    "today_breakdown": [],
                },
                "window_overview": {},
                "window_overview_views": [],
                "memory_registry": [],
                "memory_policy_views": {},
                "nightly_memory_views": {},
                "codex_native_memory": [],
                "codex_native_memory_counts": {},
                "claude_native_memory": [],
                "claude_native_memory_counts": {},
            }
            (reports_dir / "overview-data.json").write_text(
                json.dumps(overview_data),
                encoding="utf-8",
            )
            (reports_dir / "overview.md").write_text("# OpenRelix Overview\n", encoding="utf-8")
            (reports_dir / "overview.csv").write_text("id,title,type\n", encoding="utf-8")
            (reports_dir / "panel.html").write_text(
                '<meta name="openrelix:version"><div class="app-shell">'
                "token_usage pipeline_status memory_registry window_overview</main>",
                encoding="utf-8",
            )

            result = overview_contract.validate_state_dir(state_dir)

            self.assertTrue(result["ok"], result["errors"])

    def test_overview_contract_validates_curated_pack_shape_when_present(self):
        data = {
            "schema_version": overview_contract.SCHEMA_VERSION,
            "language": "zh",
            "generated_at": "2026-05-04 12:00:00",
            "summary": {"total_assets": 0, "active_assets": 0, "daily_window_count": 0},
            "metrics": [],
            "mix": {},
            "assets": {},
            "reviews": [],
            "usage_events": [],
            "summary_terms": [],
            "summary_term_views": [],
            "pipeline_status": {},
            "token_usage": {"available": False, "daily_rows": [], "today_breakdown": []},
            "window_overview": {},
            "window_overview_views": [],
            "memory_registry": [],
            "memory_policy_views": {},
            "nightly_memory_views": {},
            "codex_native_memory": [],
            "codex_native_memory_counts": {},
            "claude_native_memory": [],
            "claude_native_memory_counts": {},
            "curated_memory_pack": {
                "schema_version": 1,
                "source": "registry/memory_entries.jsonl",
                "model_calls": 0,
                "entry_count": 0,
                "sections": {},
                "diagnostics": {},
                "artifact": {},
            },
        }

        self.assertEqual(overview_contract.validate_overview_data(data), [])

    def test_curated_memory_preview_builds_without_writing_sidecar(self):
        with TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            registry_dir = state_dir / "registry"
            host_dir = state_dir / "runtime" / "host-context"
            registry_dir.mkdir(parents=True)
            host_dir.mkdir(parents=True)
            (registry_dir / "memory_entries.jsonl").write_text(
                json.dumps(
                    {
                        "source": "canonical",
                        "scope": "global",
                        "injection_policy": "global_context",
                        "memory_type": "workflow",
                        "priority": "high",
                        "title": "文件修改默认优先 apply_patch",
                        "value_note": "修改文件时先使用 apply_patch。",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            json_path = registry_dir / "curated_memory_pack.json"
            markdown_path = host_dir / "curated-personal-memory-summary.md"

            with mock.patch.object(build_overview, "REGISTRY_DIR", registry_dir):
                with mock.patch.object(build_overview, "CURATED_MEMORY_PACK_PATH", json_path):
                    with mock.patch.object(build_overview, "CURATED_MEMORY_SUMMARY_PATH", markdown_path):
                        pack = build_overview.build_curated_memory_pack_preview()

            self.assertEqual(pack["entry_count"], 1)
            self.assertEqual(pack["model_calls"], 0)
            self.assertFalse(json_path.exists())
            self.assertFalse(markdown_path.exists())
            self.assertIn("registry/memory_entries.jsonl", pack["source"])
            self.assertNotIn("source_path", pack)
            self.assertNotIn("json_path", pack["artifact"])
            self.assertNotIn("markdown_path", pack["artifact"])
            labels = [
                pack.get("source_path_label", ""),
                pack["artifact"].get("json_path_label", ""),
                pack["artifact"].get("markdown_path_label", ""),
            ]
            self.assertEqual(labels[0], "registry/memory_entries.jsonl")
            self.assertEqual(labels[1], "registry/curated_memory_pack.json")
            self.assertEqual(labels[2], "runtime/host-context/curated-personal-memory-summary.md")
            for label in labels:
                self.assertNotIn(str(state_dir), label)
                self.assertNotIn("/Users/", label)

    def test_curated_memory_preview_respects_normalized_memory_policy(self):
        pack = build_overview.build_curated_memory_pack_preview(
            [
                {
                    "source": "canonical",
                    "bucket": "low_priority",
                    "scope": "local",
                    "injection_policy": "local_only",
                    "memory_type": "preference",
                    "priority": "low",
                    "title": "不要把这条放进全局记忆",
                    "value_note": "用户已标记无用，应只作为本地低优先记录。",
                    "user_feedback": overview_memory_feedback.FEEDBACK_DOWNVOTED,
                }
            ]
        )

        stable_titles = [
            item["title"]
            for item in pack["sections"].get("stable_preferences", [])
        ]
        local_titles = [
            item["title"]
            for item in pack["sections"].get("local_volatile_notes", [])
        ]
        self.assertNotIn("不要把这条放进全局记忆", stable_titles)
        self.assertIn("不要把这条放进全局记忆", local_titles)

    def test_curated_memory_preview_replays_feedback_after_grouping(self):
        pack = build_overview.build_curated_memory_pack_preview(
            [
                {
                    "source": "canonical",
                    "scope": "global",
                    "injection_policy": "global_context",
                    "memory_type": "workflow",
                    "priority": "high",
                    "title": "长任务更适合先给快速可用层",
                    "value_note": "面对回溯先给轻量结果。",
                    "memory_key": "normal-source-key",
                },
                {
                    "source": "canonical",
                    "scope": "global",
                    "injection_policy": "global_context",
                    "memory_type": "workflow",
                    "priority": "medium",
                    "title": "长任务先轻量后深度",
                    "value_note": "遇到长任务先轻量，再补深度。",
                    "memory_key": "liked-source-key",
                },
            ],
            feedback_by_key={
                "liked-source-key": {
                    "memory_key": "liked-source-key",
                    "feedback": "liked",
                    "updated_at": "2026-05-09T12:00:00+08:00",
                }
            },
        )

        rules = pack["sections"][overview_curated_memory.SECTION_OPERATING_RULES]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["user_feedback"], "liked")
        self.assertIn("liked-source-key", rules[0]["source_memory_keys"])

    def test_curated_memory_preview_sorts_liked_items_first_after_feedback(self):
        pack = build_overview.build_curated_memory_pack_preview(
            [
                {
                    "source": "canonical",
                    "scope": "project",
                    "injection_policy": "project_context",
                    "project_label": "OpenRelix",
                    "memory_type": "workflow",
                    "priority": "high",
                    "title": "普通高优规则",
                    "value_note": "默认排序会靠前。",
                    "memory_key": "normal-project-key",
                },
                {
                    "source": "canonical",
                    "scope": "project",
                    "injection_policy": "project_context",
                    "project_label": "OpenRelix",
                    "memory_type": "workflow",
                    "priority": "medium",
                    "title": "用户标记有用规则",
                    "value_note": "点击有用后应排到最前。",
                    "memory_key": "liked-project-key",
                },
            ],
            feedback_by_key={
                "liked-project-key": {
                    "memory_key": "liked-project-key",
                    "feedback": "liked",
                    "updated_at": "2026-05-09T12:00:00+08:00",
                }
            },
        )

        playbooks = pack["sections"][overview_curated_memory.SECTION_PROJECT_PLAYBOOKS]
        self.assertEqual([item["title"] for item in playbooks[:2]], ["用户标记有用规则", "普通高优规则"])
        self.assertEqual(playbooks[0]["user_feedback"], "liked")

    def test_curated_memory_preview_clears_liked_feedback_with_neutral(self):
        pack = build_overview.build_curated_memory_pack_preview(
            [
                {
                    "source": "canonical",
                    "scope": "project",
                    "injection_policy": "project_context",
                    "project_label": "OpenRelix",
                    "memory_type": "workflow",
                    "priority": "high",
                    "title": "普通高优规则",
                    "value_note": "默认排序会靠前。",
                    "memory_key": "normal-project-key",
                },
                {
                    "source": "canonical",
                    "scope": "project",
                    "injection_policy": "project_context",
                    "project_label": "OpenRelix",
                    "memory_type": "workflow",
                    "priority": "medium",
                    "title": "已取消有用规则",
                    "value_note": "取消有用后不应继续按已标记排序。",
                    "memory_key": "neutral-project-key",
                    "user_feedback": "liked",
                },
            ],
            feedback_by_key={
                "neutral-project-key": {
                    "memory_key": "neutral-project-key",
                    "feedback": "neutral",
                    "updated_at": "2026-05-09T12:30:00+08:00",
                }
            },
        )

        playbooks = pack["sections"][overview_curated_memory.SECTION_PROJECT_PLAYBOOKS]
        self.assertEqual([item["title"] for item in playbooks[:2]], ["普通高优规则", "已取消有用规则"])
        self.assertEqual(playbooks[1]["user_feedback"], "")
        self.assertEqual(playbooks[1]["user_feedback_updated_at"], "2026-05-09T12:30:00+08:00")

    def test_curated_memory_panel_localizes_playbooks_and_expands_with_feedback(self):
        sections = {section: [] for section in overview_curated_memory.SECTION_ORDER}
        sections[overview_curated_memory.SECTION_PROJECT_PLAYBOOKS] = [
            {
                "section": overview_curated_memory.SECTION_PROJECT_PLAYBOOKS,
                "title": "项目打法 {}".format(index),
                "value_note": "可复用做法 {}".format(index),
                "project_label": "OpenRelix",
                "injection_policy": "project_context",
                "memory_key": "memory-key-{}".format(index),
                "user_feedback": "liked" if index == 0 else "",
            }
            for index in range(5)
        ]
        html = build_overview.make_curated_memory_panel_body(
            {
                "schema_version": 1,
                "source": "registry/memory_entries.jsonl",
                "model_calls": 0,
                "entry_count": 5,
                "sections": sections,
                "diagnostics": {},
                "artifact": {},
            }
        )

        self.assertIn("项目工作手册", html)
        self.assertNotIn("项目 Playbooks", html)
        self.assertIn("查看更多 2 条", html)
        self.assertIn('data-collapse-details', html)
        self.assertIn('class="content-more-collapse-button"', html)
        self.assertIn('class="content-more"', html)
        self.assertIn('class="curated-memory-memory-badge"', html)
        self.assertIn("记忆已注入", html)
        self.assertIn('data-memory-feedback="liked" data-memory-key="memory-key-0"', html)
        self.assertIn('data-memory-feedback="downvoted" data-memory-key="memory-key-0"', html)
        self.assertNotIn("旁路整理预览", html)
        self.assertNotIn("输入条目", html)
        self.assertNotIn("质量信号", html)
        self.assertNotIn("curated-memory-diagnostic-grid", html)

    def test_curated_memory_panel_hides_downvoted_items(self):
        sections = {section: [] for section in overview_curated_memory.SECTION_ORDER}
        sections[overview_curated_memory.SECTION_PROJECT_PLAYBOOKS] = [
            {
                "section": overview_curated_memory.SECTION_PROJECT_PLAYBOOKS,
                "title": "可见项目打法 {}".format(index),
                "value_note": "可复用做法 {}".format(index),
                "project_label": "OpenRelix",
                "injection_policy": "project_context",
                "memory_key": "visible-memory-key-{}".format(index),
            }
            for index in range(4)
        ] + [
            {
                "section": overview_curated_memory.SECTION_PROJECT_PLAYBOOKS,
                "title": "被标记无用的项目打法",
                "value_note": "这条不应继续占据个人资产记忆面板。",
                "project_label": "OpenRelix",
                "injection_policy": "project_context",
                "memory_key": "downvoted-memory-key",
                "user_feedback": overview_memory_feedback.FEEDBACK_DOWNVOTED,
            }
        ]

        html = build_overview.make_curated_memory_panel_body(
            {
                "schema_version": 1,
                "source": "registry/memory_entries.jsonl",
                "model_calls": 0,
                "entry_count": 5,
                "sections": sections,
                "diagnostics": {},
                "artifact": {},
            }
        )

        self.assertIn("4 条", html)
        self.assertIn("查看更多 1 条", html)
        self.assertIn("可见项目打法 3", html)
        self.assertNotIn("被标记无用的项目打法", html)
        self.assertNotIn("downvoted-memory-key", html)

    def test_curated_memory_badge_only_marks_injected_policy(self):
        sections = {section: [] for section in overview_curated_memory.SECTION_ORDER}
        sections[overview_curated_memory.SECTION_LOCAL_VOLATILE] = [
            {
                "section": overview_curated_memory.SECTION_LOCAL_VOLATILE,
                "title": "本地保留的有用记忆",
                "value_note": "只在本地保留。",
                "injection_policy": "local_only",
                "memory_key": "local-liked-memory",
                "user_feedback": "liked",
            }
        ]

        html = build_overview.make_curated_memory_panel_body(
            {
                "schema_version": 1,
                "source": "registry/memory_entries.jsonl",
                "model_calls": 0,
                "entry_count": 1,
                "sections": sections,
                "diagnostics": {},
                "artifact": {},
            }
        )

        self.assertIn("本地保留的有用记忆", html)
        self.assertNotIn('class="curated-memory-memory-badge"', html)
        self.assertNotIn("记忆已注入", html)

    def test_curated_memory_synthetic_profile_is_labeled_as_summary_not_injected(self):
        sections = {section: [] for section in overview_curated_memory.SECTION_ORDER}
        sections[overview_curated_memory.SECTION_USER_PROFILE] = [
            {
                "section": overview_curated_memory.SECTION_USER_PROFILE,
                "title": "Recurring work profile",
                "value_note": "Recurring work appears across OpenRelix and Douyin.",
                "injection_policy": "global_context",
                "diagnostics": ["synthetic"],
            }
        ]

        html = build_overview.make_curated_memory_panel_body(
            {
                "schema_version": 1,
                "source": "registry/memory_entries.jsonl",
                "model_calls": 0,
                "entry_count": 0,
                "sections": sections,
                "diagnostics": {},
                "artifact": {},
            }
        )

        self.assertIn("Recurring work profile", html)
        self.assertIn("自动汇总", html)
        self.assertIn("is-synthetic", html)
        self.assertNotIn("记忆已注入", html)

    def test_runtime_language_config_persists_and_normalizes(self):
        self.assertEqual(asset_runtime.normalize_language("zh-CN"), "zh")
        self.assertEqual(asset_runtime.normalize_language("english"), "en")
        with self.assertRaises(ValueError):
            asset_runtime.normalize_language("fr", strict=True)
        self.assertEqual(asset_runtime.normalize_memory_mode(None), "integrated")
        self.assertEqual(asset_runtime.normalize_memory_mode(""), "integrated")
        self.assertEqual(asset_runtime.normalize_memory_mode("record-memory-only"), "local-only")
        self.assertEqual(asset_runtime.normalize_memory_mode("codex"), "integrated")
        self.assertEqual(asset_runtime.normalize_memory_mode("codex-context"), "integrated")
        self.assertEqual(asset_runtime.normalize_memory_mode("disabled"), "off")
        with self.assertRaises(ValueError):
            asset_runtime.normalize_memory_mode("cloud", strict=True)
        self.assertEqual(asset_runtime.normalize_activity_source(None), "auto")
        self.assertEqual(asset_runtime.normalize_activity_source("codex_app_server"), "app-server")
        self.assertEqual(asset_runtime.normalize_activity_source("read-codex-app"), "auto")
        with self.assertRaises(ValueError):
            asset_runtime.normalize_activity_source("browser", strict=True)
        self.assertEqual(asset_runtime.normalize_activity_host(None), "all")
        self.assertEqual(asset_runtime.normalize_activity_host("cc"), "claude")
        self.assertEqual(asset_runtime.normalize_activity_host("both"), "all")
        with self.assertRaises(ValueError):
            asset_runtime.normalize_activity_host("browser", strict=True)
        self.assertEqual(asset_runtime.normalize_model_cli(None), "codex")
        self.assertEqual(asset_runtime.normalize_model_cli("cc"), "claude")
        with self.assertRaises(ValueError):
            asset_runtime.normalize_model_cli("browser", strict=True)
        self.assertEqual(asset_runtime.normalize_claude_model(None), "auto")
        self.assertEqual(asset_runtime.normalize_claude_model("default"), "auto")
        self.assertEqual(asset_runtime.normalize_claude_model("opus"), "opus")
        with self.assertRaises(ValueError):
            asset_runtime.normalize_claude_model("bad model", strict=True)
        self.assertEqual(asset_runtime.normalize_host_context_targets("codex,cc"), ["codex", "claude"])
        self.assertEqual(asset_runtime.normalize_codex_model(None), "gpt-5.4-mini")
        self.assertEqual(asset_runtime.normalize_codex_model("gpt5.4mini"), "gpt-5.4-mini")
        self.assertEqual(asset_runtime.normalize_codex_model("gpt5.5"), "gpt-5.5")
        with self.assertRaises(ValueError):
            asset_runtime.normalize_codex_model("bad model", strict=True)
        self.assertEqual(asset_runtime.normalize_memory_summary_max_tokens(None), 8000)
        self.assertEqual(asset_runtime.normalize_memory_summary_max_tokens("8000"), 8000)
        with self.assertRaises(ValueError):
            asset_runtime.normalize_memory_summary_max_tokens("1000", strict=True)

        with TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ,
                {
                    "AI_ASSET_STATE_DIR": tmpdir,
                    "AI_ASSET_LANGUAGE": "",
                    "AI_ASSET_MEMORY_MODE": "",
                    "OPENRELIX_ACTIVITY_SOURCE": "",
                    "AI_ASSET_ACTIVITY_SOURCE": "",
                    "OPENRELIX_ACTIVITY_HOST": "",
                    "AI_ASSET_ACTIVITY_HOST": "",
                    "OPENRELIX_MODEL_CLI": "",
                    "AI_ASSET_MODEL_CLI": "",
                    "OPENRELIX_CODEX_MODEL": "",
                    "AI_ASSET_CODEX_MODEL": "",
                    "OPENRELIX_CLAUDE_MODEL": "",
                    "AI_ASSET_CLAUDE_MODEL": "",
                    "OPENRELIX_CLAUDE_SETTINGS": "",
                    "AI_ASSET_CLAUDE_SETTINGS": "",
                    "OPENRELIX_CLAUDE_ENV_FILE": "",
                    "AI_ASSET_CLAUDE_ENV_FILE": "",
                },
            ):
                paths = asset_runtime.get_runtime_paths()
                asset_runtime.ensure_state_layout(paths)
                claude_settings_path = Path(tmpdir) / "claude-settings.json"
                claude_env_path = Path(tmpdir) / "claude.env"
                config = asset_runtime.write_runtime_config(
                    language="en",
                    memory_mode="codex",
                    activity_source="auto",
                    activity_host="cc",
                    model_cli="cc",
                    codex_model="gpt5.4mini",
                    claude_model="opus",
                    claude_settings=str(claude_settings_path),
                    claude_env_file=str(claude_env_path),
                    memory_summary_max_tokens=8000,
                    paths=paths,
                )

                self.assertEqual(config["language"], "en")
                self.assertEqual(config["memory_mode"], "integrated")
                self.assertEqual(config["activity_source"], "auto")
                self.assertEqual(config["activity_host"], "claude")
                self.assertEqual(config["model_cli"], "claude")
                self.assertEqual(config["codex_model"], "gpt-5.4-mini")
                self.assertEqual(config["claude_model"], "opus")
                self.assertEqual(config["claude_settings"], str(claude_settings_path.resolve()))
                self.assertEqual(config["claude_env_file"], str(claude_env_path.resolve()))
                self.assertEqual(config["host_context_targets"], ["codex", "claude"])
                self.assertEqual(config["memory_summary_max_tokens"], 8000)
                self.assertTrue(config["personal_memory_enabled"])
                self.assertTrue(config["codex_context_enabled"])
                self.assertEqual(asset_runtime.get_memory_summary_budget(paths)["max_tokens"], 8000)
                self.assertEqual(asset_runtime.get_memory_summary_budget(paths)["global_memory_tokens"], 800)
                self.assertEqual(asset_runtime.get_memory_summary_budget(paths)["project_memory_tokens"], 2400)
                self.assertEqual(asset_runtime.get_memory_summary_budget(paths)["personal_memory_tokens"], 3200)
                self.assertEqual(asset_runtime.get_runtime_language(paths), "en")
                self.assertEqual(asset_runtime.get_memory_mode(paths), "integrated")
                self.assertEqual(asset_runtime.get_activity_source(paths), "auto")
                self.assertEqual(asset_runtime.get_activity_host(paths), "claude")
                self.assertEqual(asset_runtime.get_model_cli(paths), "claude")
                self.assertEqual(asset_runtime.get_codex_model(paths), "gpt-5.4-mini")
                self.assertEqual(asset_runtime.get_claude_model(paths), "opus")
                self.assertEqual(asset_runtime.get_claude_settings(paths), str(claude_settings_path.resolve()))
                self.assertEqual(asset_runtime.get_claude_env_file(paths), str(claude_env_path.resolve()))
                self.assertEqual(asset_runtime.get_host_context_targets(paths), ["codex", "claude"])
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

    def test_personal_denylist_redacts_generated_display_text(self):
        with TemporaryDirectory() as tmpdir:
            denylist = Path(tmpdir) / "personal_denylist.txt"
            denylist.write_text("PrivateProject\n私有项目\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"OPENRELIX_PERSONAL_DENYLIST": str(denylist)},
                clear=False,
            ):
                build_overview.personal_redaction_patterns.cache_clear()
                try:
                    self.assertEqual(
                        build_overview.normalize_brand_display_text("PrivateProject dashboard"),
                        "Work project dashboard",
                    )
                    self.assertEqual(
                        build_overview.normalize_brand_display_text("来自私有项目的复盘"),
                        "来自Work project的复盘",
                    )
                finally:
                    build_overview.personal_redaction_patterns.cache_clear()

    def test_brand_display_normalization_preserves_json_value_types(self):
        payload = {"schema_version": 1, "available": True, "items": [{"count": 2}]}

        normalized = build_overview.normalize_brand_display_payload(payload)

        self.assertEqual(normalized["schema_version"], 1)
        self.assertIs(normalized["available"], True)
        self.assertEqual(normalized["items"][0]["count"], 2)

    def test_text_rendering_boundaries_accept_non_string_values(self):
        local_link = build_overview.render_local_path_link(Path.cwd())
        jump_link = build_overview.render_jump_link("section-a", 123)

        self.assertIn("path-link", local_link)
        self.assertIn(str(Path.cwd()), local_link)
        self.assertIn(">123</a>", jump_link)
        self.assertEqual(build_overview.panel_display_text(123), "123")

    def test_redaction_preserves_public_project_links_in_href(self):
        html = (
            '<a href="https://www.npmjs.com/~kk_kais" target="_blank">kk_kais</a> '
            'const url = "https://registry.npmjs.org/" + encodeURIComponent(pkg) + "/latest"; '
            '<a href="https://registry.npmjs.org/@private-scope/internal-tool/latest">private package</a> '
            '<a href="https://example.com/private">private</a>'
        )

        redacted = build_overview.normalize_brand_display_text(html)

        self.assertIn('href="https://www.npmjs.com/~kk_kais"', redacted)
        self.assertIn('"https://registry.npmjs.org/"', redacted)
        self.assertNotIn("https://registry.npmjs.org/@private-scope/internal-tool/latest", redacted)
        self.assertIn('href="<link>"', redacted)

    def test_repo_panel_entrypoint_is_not_written_by_default(self):
        old_paths = build_overview.PATHS
        old_reports_dir = build_overview.REPORTS_DIR
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            state_reports = root / "state" / "reports"
            repo_root.mkdir()
            state_reports.mkdir(parents=True)
            try:
                build_overview.PATHS = replace(old_paths, repo_root=repo_root)
                build_overview.REPORTS_DIR = state_reports
                with mock.patch.dict(
                    os.environ,
                    {build_overview.WRITE_REPO_PANEL_ENTRYPOINT_ENV: ""},
                    clear=False,
                ):
                    build_overview.write_repo_panel_entrypoint()
                self.assertFalse((repo_root / "reports").exists())
            finally:
                build_overview.PATHS = old_paths
                build_overview.REPORTS_DIR = old_reports_dir

    def test_default_state_root_prefers_legacy_slug_only_when_new_root_is_absent(self):
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            app_support = home / "Library" / "Application Support"
            old_root = app_support / ("open" + "keepsake")
            new_root = app_support / "openrelix"
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
        self.assertEqual(
            fallback["window_summaries"][0]["window_title"],
            "Install language choice should affect summaries.",
        )
        self.assertEqual(
            fallback["window_summaries"][0]["summary_pairs"],
            [
                {
                    "question": "Install language choice should affect summaries.",
                    "conclusion": "Persist language in runtime config.",
                }
            ],
        )
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

    def test_token_fetcher_merges_codex_and_claude_daily_usage(self):
        def fake_now():
            return datetime.fromisoformat("2026-05-04T10:00:00+08:00")

        commands = []

        def fake_runner(cmd, **kwargs):
            commands.append(cmd)
            package = cmd[2]
            provider_command = tuple(cmd[3:5])
            self.assertEqual(package, "ccusage@latest")
            if provider_command == ("codex", "daily"):
                payload = {
                    "daily": [
                        {
                            "period": "2026-05-04",
                            "inputTokens": 100,
                            "cacheReadTokens": 20,
                            "outputTokens": 30,
                            "reasoningOutputTokens": 5,
                            "totalTokens": 135,
                            "totalCost": 1.25,
                        }
                    ]
                }
            elif provider_command == ("claude", "daily"):
                payload = {
                    "daily": [
                        {
                            "date": "20260504",
                            "inputTokens": 50,
                            "cacheCreationTokens": 10,
                            "cacheReadTokens": 5,
                            "outputTokens": 20,
                            "totalTokens": 85,
                            "totalCost": 0.5,
                        }
                    ]
                }
            else:
                raise AssertionError(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

        result = token_fetcher.fetch_ccusage_daily(
            window_days=2,
            now_func=fake_now,
            resolve_npx_binary_func=lambda: "npx",
            env_func=lambda: {},
            runner=fake_runner,
            provider="all",
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["provider"], "all")
        self.assertIn("codex", result["provider_results"])
        self.assertIn("claude", result["provider_results"])
        merged_row = result["payload"]["daily"][0]
        self.assertEqual(merged_row["date"], "2026-05-04")
        self.assertEqual(merged_row["totalTokens"], 235)
        self.assertEqual(merged_row["inputTokens"], 185)
        self.assertEqual(merged_row["cachedInputTokens"], 25)
        self.assertAlmostEqual(merged_row["costUSD"], 1.75)
        self.assertEqual(merged_row["providers"]["codex"]["inputTokens"], 120)
        self.assertEqual(merged_row["providers"]["codex"]["totalTokens"], 150)
        self.assertEqual(merged_row["providers"]["claude"]["provider"], "claude")
        self.assertEqual([command[3:5] for command in commands], [["codex", "daily"], ["claude", "daily"]])

    def test_token_fetcher_accepts_explicit_date_range(self):
        commands = []

        def fake_now():
            return datetime.fromisoformat("2026-05-04T10:00:00+08:00")

        def fake_runner(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"daily": []}),
                stderr="",
            )

        result = token_fetcher.fetch_ccusage_daily(
            window_days=7,
            now_func=fake_now,
            resolve_npx_binary_func=lambda: "npx",
            env_func=lambda: {},
            runner=fake_runner,
            provider="claude",
            start_date="2026-04-01",
            end_date="2026-04-30",
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["range_start"], "2026-04-01")
        self.assertEqual(result["range_end"], "2026-04-30")
        self.assertEqual(result["window_days"], 30)
        self.assertEqual(commands[0][2:5], ["ccusage@latest", "claude", "daily"])
        self.assertIn("--since", commands[0])
        self.assertEqual(commands[0][commands[0].index("--since") + 1], "20260401")
        self.assertEqual(commands[0][commands[0].index("--until") + 1], "20260430")

    def test_token_fetcher_uses_compact_codex_date_range_for_ccusage(self):
        commands = []

        def fake_now():
            return datetime.fromisoformat("2026-05-04T10:00:00+08:00")

        def fake_runner(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"daily": []}),
                stderr="",
            )

        token_fetcher.fetch_ccusage_daily(
            window_days=7,
            now_func=fake_now,
            resolve_npx_binary_func=lambda: "npx",
            env_func=lambda: {},
            runner=fake_runner,
            provider="codex",
            start_date="2026-04-01",
            end_date="2026-04-30",
        )

        self.assertEqual(commands[0][2:5], ["ccusage@latest", "codex", "daily"])
        self.assertEqual(commands[0][commands[0].index("--since") + 1], "20260401")
        self.assertEqual(commands[0][commands[0].index("--until") + 1], "20260430")

    def test_token_fetcher_falls_back_to_unfiltered_codex_payload_when_date_filter_is_empty(self):
        commands = []

        def fake_now():
            return datetime.fromisoformat("2026-05-07T10:00:00+08:00")

        def fake_runner(cmd, **kwargs):
            commands.append(cmd)
            if "--since" in cmd:
                payload = {"daily": []}
            else:
                payload = {
                    "daily": [
                        {
                            "date": "2026-04-30",
                            "inputTokens": 10,
                            "outputTokens": 1,
                            "totalTokens": 11,
                        },
                        {
                            "date": "2026-05-03",
                            "inputTokens": 20,
                            "outputTokens": 2,
                            "totalTokens": 22,
                        },
                        {
                            "date": "2026-05-08",
                            "inputTokens": 30,
                            "outputTokens": 3,
                            "totalTokens": 33,
                        },
                    ]
                }
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

        result = token_fetcher.fetch_ccusage_daily(
            window_days=7,
            now_func=fake_now,
            resolve_npx_binary_func=lambda: "npx",
            env_func=lambda: {},
            runner=fake_runner,
            provider="codex",
            start_date="2026-05-01",
            end_date="2026-05-07",
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["fallback"], "unfiltered_local_range")
        self.assertEqual(len(commands), 2)
        self.assertIn("--since", commands[0])
        self.assertNotIn("--since", commands[1])
        self.assertEqual(result["payload"]["daily"][0]["date"], "2026-05-03")
        self.assertEqual(result["payload"]["daily"][0]["totalTokens"], 22)
        self.assertEqual(result["payload"]["totals"]["totalTokens"], 22)

    def test_token_cache_matches_open_ended_date_range(self):
        def fake_now():
            return datetime.fromisoformat("2026-05-04T10:00:00+08:00")

        payload = {
            "available": True,
            "provider": "codex",
            "window_days": 34,
            "range_start": "2026-04-01",
            "range_end": "2026-05-04",
        }

        self.assertTrue(
            token_fetcher.token_cache_matches_request(
                payload,
                "codex",
                7,
                start_date="2026-04-01",
                now_func=fake_now,
            )
        )
        self.assertTrue(
            token_live_server.cache_matches_request(
                dict(payload, group_by="month"),
                7,
                "codex",
                start_date="2026-04-01",
                group_by="month",
                now_func=fake_now,
            )
        )
        self.assertTrue(
            token_fetcher.token_cache_matches_request(
                payload,
                "codex",
                7,
                end_date="2026-05-03",
                now_func=fake_now,
            )
        )
        self.assertFalse(
            token_fetcher.token_cache_matches_request(
                payload,
                "codex",
                7,
                start_date="2026-03-31",
                end_date="2026-05-03",
                now_func=fake_now,
            )
        )

    def test_token_resolver_uses_month_cache_for_provider_switch(self):
        cached = {
            "available": True,
            "provider": "all",
            "provider_label": "Codex + Claude Code",
            "window_days": 30,
            "range_start": "2026-04-05",
            "range_end": "2026-05-04",
            "payload": {"daily": []},
            "provider_results": {
                "codex": {
                    "available": True,
                    "provider": "codex",
                    "provider_label": "Codex",
                    "window_days": 30,
                    "range_start": "2026-04-05",
                    "range_end": "2026-05-04",
                    "payload": {
                        "daily": [
                            {
                                "date": "2026-05-04",
                                "provider": "codex",
                                "inputTokens": 100,
                                "cachedInputTokens": 20,
                                "outputTokens": 10,
                                "totalTokens": 110,
                                "costUSD": 1.0,
                            }
                        ]
                    },
                    "error": "",
                },
                "claude": {
                    "available": True,
                    "provider": "claude",
                    "provider_label": "Claude Code",
                    "window_days": 30,
                    "range_start": "2026-04-05",
                    "range_end": "2026-05-04",
                    "payload": {"daily": []},
                    "error": "",
                },
            },
            "error": "",
        }

        with TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "token-cache.json"
            cache_path.write_text(json.dumps(cached), encoding="utf-8")
            result = token_fetcher.resolve_ccusage_daily(
                cache_path=cache_path,
                refresh_requested=False,
                fetch_func=mock.Mock(side_effect=AssertionError("should not fetch")),
                provider="codex",
                window_days=7,
                start_date="2026-05-01",
                end_date="2026-05-04",
            )

        self.assertEqual(result["provider"], "codex")
        self.assertEqual(result["range_start"], "2026-05-01")
        self.assertEqual(result["range_end"], "2026-05-04")
        self.assertEqual(result["window_days"], 4)
        self.assertEqual(result["payload"]["daily"][0]["totalTokens"], 110)

    def test_token_resolver_fetches_month_cache_for_short_range(self):
        calls = []

        def fake_fetch(**kwargs):
            calls.append(kwargs)
            return {
                "available": True,
                "provider": kwargs.get("provider", "all"),
                "provider_label": "Codex + Claude Code",
                "payload": {"daily": []},
                "error": "",
                "window_days": kwargs.get("window_days"),
                "range_start": kwargs.get("start_date").isoformat(),
                "range_end": kwargs.get("end_date").isoformat(),
            }

        with TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "token-cache.json"
            result = token_fetcher.resolve_ccusage_daily(
                cache_path=cache_path,
                refresh_requested=False,
                fetch_func=fake_fetch,
                provider="all",
                window_days=4,
                start_date="2026-05-01",
                end_date="2026-05-04",
            )
            cached = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(calls[0]["start_date"], date(2026, 4, 5))
        self.assertEqual(calls[0]["end_date"], date(2026, 5, 4))
        self.assertEqual(calls[0]["window_days"], 30)
        self.assertEqual(cached["range_start"], "2026-04-05")
        self.assertEqual(result["range_start"], "2026-05-01")
        self.assertEqual(result["window_days"], 4)

    def test_token_live_server_reuses_month_cache_for_provider_switch(self):
        cached = {
            "ok": True,
            "provider": "all",
            "group_by": "day",
            "window_days": 7,
            "range_start": "2026-05-05",
            "range_end": "2026-05-11",
            "_cached_at_epoch": token_live_server.time.time(),
            "ccusage_result": {
                "available": True,
                "provider": "all",
                "provider_label": "Codex + Claude Code",
                "window_days": 30,
                "range_start": "2026-04-12",
                "range_end": "2026-05-11",
                "payload": {"daily": []},
                "provider_results": {
                    "codex": {
                        "available": True,
                        "provider": "codex",
                        "provider_label": "Codex",
                        "window_days": 30,
                        "range_start": "2026-04-12",
                        "range_end": "2026-05-11",
                        "payload": {
                            "daily": [
                                {
                                    "date": "2026-05-10",
                                    "provider": "codex",
                                    "inputTokens": 100,
                                    "cachedInputTokens": 25,
                                    "outputTokens": 20,
                                    "reasoningOutputTokens": 5,
                                    "totalTokens": 125,
                                    "costUSD": 2.0,
                                }
                            ]
                        },
                        "error": "",
                    }
                },
                "error": "",
            },
        }

        with mock.patch.object(token_live_server, "load_cache", return_value=cached), mock.patch.object(
            token_live_server,
            "fetch_ccusage_daily",
            side_effect=AssertionError("should not fetch"),
        ):
            payload = token_live_server.fetch_token_payload(
                window_days=7,
                provider="codex",
                start_date="2026-05-05",
                end_date="2026-05-11",
                group_by="day",
            )

        self.assertTrue(payload["served_from_cache"])
        self.assertFalse(payload["stale"])
        self.assertEqual(payload["provider"], "codex")
        self.assertEqual(payload["token_usage"]["provider"], "codex")
        self.assertEqual(payload["token_usage"]["range_start"], "2026-05-05")
        self.assertEqual(payload["token_usage"]["range_end"], "2026-05-11")
        self.assertEqual(payload["token_usage"]["daily_rows"][0]["value"], 125)

    def test_token_live_server_returns_stale_cache_and_refreshes_in_background(self):
        cached = {
            "ok": True,
            "provider": "codex",
            "group_by": "day",
            "window_days": 7,
            "range_start": "2026-05-05",
            "range_end": "2026-05-11",
            "_cached_at_epoch": token_live_server.time.time() - token_live_server.CACHE_TTL_SECONDS - 1,
            "ccusage_result": {
                "available": True,
                "provider": "codex",
                "provider_label": "Codex",
                "window_days": 30,
                "range_start": "2026-04-12",
                "range_end": "2026-05-11",
                "payload": {
                    "daily": [
                        {
                            "date": "2026-05-11",
                            "provider": "codex",
                            "inputTokens": 100,
                            "cachedInputTokens": 10,
                            "outputTokens": 20,
                            "reasoningOutputTokens": 0,
                            "totalTokens": 120,
                            "costUSD": 2.0,
                        }
                    ]
                },
                "error": "",
            },
        }

        with mock.patch.object(token_live_server, "load_cache", return_value=cached), mock.patch.object(
            token_live_server,
            "start_token_cache_refresh_async",
            return_value=True,
        ) as start_refresh, mock.patch.object(
            token_live_server,
            "fetch_ccusage_daily",
            side_effect=AssertionError("stale cache should return immediately"),
        ):
            payload = token_live_server.fetch_token_payload(
                window_days=7,
                provider="codex",
                start_date="2026-05-05",
                end_date="2026-05-11",
                group_by="day",
            )

        self.assertTrue(payload["served_from_cache"])
        self.assertTrue(payload["stale"])
        self.assertEqual(payload["token_usage"]["today_total_tokens"], 120)
        start_refresh.assert_called_once()

    def test_token_live_force_refresh_returns_cache_and_refreshes_in_background(self):
        cached = {
            "ok": True,
            "provider": "all",
            "group_by": "day",
            "window_days": 30,
            "range_start": "2026-04-01",
            "range_end": "2026-06-08",
            "_cached_at_epoch": token_live_server.time.time(),
            "ccusage_result": {
                "available": True,
                "provider": "all",
                "provider_label": token_fetcher.provider_display_name("all"),
                "window_days": 69,
                "range_start": "2026-04-01",
                "range_end": "2026-06-08",
                "payload": {
                    "daily": [
                        {
                            "date": "2026-04-03",
                            "provider": "all",
                            "inputTokens": 100,
                            "cachedInputTokens": 10,
                            "outputTokens": 20,
                            "reasoningOutputTokens": 0,
                            "totalTokens": 120,
                            "costUSD": 2.0,
                        }
                    ]
                },
                "error": "",
            },
        }

        with mock.patch.object(token_live_server, "load_cache", return_value=cached), mock.patch.object(
            token_live_server,
            "start_token_cache_refresh_async",
            return_value=True,
        ) as start_refresh, mock.patch.object(
            token_live_server,
            "fetch_ccusage_daily",
            side_effect=AssertionError("force refresh should not block when cache covers the request"),
        ):
            payload = token_live_server.fetch_token_payload(
                window_days=30,
                force_refresh=True,
                provider="all",
                start_date="2026-04-01",
                end_date="2026-06-08",
                group_by="month",
            )

        self.assertTrue(payload["served_from_cache"])
        self.assertTrue(payload["stale"])
        self.assertTrue(payload["refreshing"])
        self.assertTrue(payload["token_usage"]["refreshing"])
        self.assertEqual(payload["token_usage"]["range_start"], "2026-04-01")
        self.assertEqual(payload["token_usage"]["range_end"], "2026-06-08")
        self.assertEqual(payload["token_usage"]["daily_rows"][0]["label"], "2026-04")
        start_refresh.assert_called_once()

    def test_token_live_cache_keeps_provider_entries_separate(self):
        def result(provider, total_tokens):
            return {
                "available": True,
                "provider": provider,
                "provider_label": token_fetcher.provider_display_name(provider),
                "window_days": 30,
                "range_start": "2026-04-12",
                "range_end": "2026-05-11",
                "payload": {
                    "daily": [
                        {
                            "date": "2026-05-11",
                            "provider": provider,
                            "inputTokens": total_tokens,
                            "cachedInputTokens": 0,
                            "outputTokens": 0,
                            "reasoningOutputTokens": 0,
                            "totalTokens": total_tokens,
                            "costUSD": 1.0,
                        }
                    ]
                },
                "error": "",
            }

        all_result = result("all", 300)
        all_result["provider_results"] = {
            "codex": result("codex", 100),
            "claude": result("claude", 200),
        }

        with TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "token-live-cache.json"
            report_cache_path = Path(tmpdir) / "token-usage-cache.json"
            old_cache_path = token_live_server.CACHE_PATH
            old_report_cache_path = token_live_server.REPORT_TOKEN_CACHE_PATH
            token_live_server.CACHE_PATH = cache_path
            token_live_server.REPORT_TOKEN_CACHE_PATH = report_cache_path
            try:
                token_live_server.write_cache(
                    token_live_server.build_token_payload_from_result(
                        all_result,
                        30,
                        "all",
                        start_date="2026-04-12",
                        end_date="2026-05-11",
                    )
                )
                token_live_server.write_cache(
                    token_live_server.build_token_payload_from_result(
                        result("claude", 200),
                        3,
                        "claude",
                        start_date="2026-05-09",
                        end_date="2026-05-11",
                    )
                )
                cached = token_live_server.load_cache()
            finally:
                token_live_server.CACHE_PATH = old_cache_path
                token_live_server.REPORT_TOKEN_CACHE_PATH = old_report_cache_path

        self.assertEqual(cached["version"], 2)
        self.assertIn("all|2026-04-12|2026-05-11", cached["entries"])
        self.assertIn("claude|2026-04-12|2026-05-11", cached["entries"])
        all_payload = token_live_server.build_payload_from_cache(
            cached,
            7,
            "all",
            start_date="2026-05-05",
            end_date="2026-05-11",
        )
        claude_payload = token_live_server.build_payload_from_cache(
            cached,
            3,
            "claude",
            start_date="2026-05-09",
            end_date="2026-05-11",
        )

        self.assertIsNotNone(all_payload)
        self.assertEqual(all_payload["provider"], "all")
        self.assertEqual(all_payload["token_usage"]["today_total_tokens"], 300)
        self.assertIsNotNone(claude_payload)
        self.assertEqual(claude_payload["provider"], "claude")
        self.assertEqual(claude_payload["token_usage"]["today_total_tokens"], 200)

    def test_token_live_cache_uses_report_cache_when_live_slot_is_different_provider(self):
        def result(provider, total_tokens):
            return {
                "available": True,
                "provider": provider,
                "provider_label": token_fetcher.provider_display_name(provider),
                "window_days": 30,
                "range_start": "2026-04-12",
                "range_end": "2026-05-11",
                "payload": {
                    "daily": [
                        {
                            "date": "2026-05-11",
                            "provider": provider,
                            "inputTokens": total_tokens,
                            "cachedInputTokens": 0,
                            "outputTokens": 0,
                            "reasoningOutputTokens": 0,
                            "totalTokens": total_tokens,
                            "costUSD": 1.0,
                        }
                    ]
                },
                "error": "",
            }

        all_result = result("all", 300)
        all_result["provider_results"] = {
            "codex": result("codex", 100),
            "claude": result("claude", 200),
        }

        with TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "token-live-cache.json"
            report_cache_path = Path(tmpdir) / "token-usage-cache.json"
            report_cache_path.write_text(json.dumps(all_result), encoding="utf-8")
            cache_path.write_text(
                json.dumps(
                    token_live_server.build_token_payload_from_result(
                        result("claude", 200),
                        3,
                        "claude",
                        start_date="2026-05-09",
                        end_date="2026-05-11",
                    )
                ),
                encoding="utf-8",
            )
            old_cache_path = token_live_server.CACHE_PATH
            old_report_cache_path = token_live_server.REPORT_TOKEN_CACHE_PATH
            token_live_server.CACHE_PATH = cache_path
            token_live_server.REPORT_TOKEN_CACHE_PATH = report_cache_path
            try:
                cached = token_live_server.load_cache()
            finally:
                token_live_server.CACHE_PATH = old_cache_path
                token_live_server.REPORT_TOKEN_CACHE_PATH = old_report_cache_path

        self.assertIn("claude|2026-04-12|2026-05-11", cached["entries"])
        self.assertIn("all|2026-04-12|2026-05-11", cached["entries"])
        all_payload = token_live_server.build_payload_from_cache(
            cached,
            7,
            "all",
            start_date="2026-05-05",
            end_date="2026-05-11",
        )

        self.assertIsNotNone(all_payload)
        self.assertEqual(all_payload["provider"], "all")
        self.assertEqual(all_payload["token_usage"]["today_total_tokens"], 300)

    def test_token_live_cache_prefers_non_empty_covering_entry(self):
        def result(range_start, total_tokens, fetched_at="2026-05-18T10:00:00+08:00"):
            daily = []
            if total_tokens:
                daily.append(
                    {
                        "date": "2026-05-18",
                        "provider": "all",
                        "inputTokens": total_tokens,
                        "cachedInputTokens": 0,
                        "outputTokens": 0,
                        "reasoningOutputTokens": 0,
                        "totalTokens": total_tokens,
                        "costUSD": 1.0,
                    }
                )
            return {
                "available": True,
                "provider": "all",
                "provider_label": token_fetcher.provider_display_name("all"),
                "window_days": 30,
                "range_start": range_start,
                "range_end": "2026-05-18",
                "payload": {"daily": daily},
                "error": "",
                "fetched_at": fetched_at,
            }

        empty_week = token_live_server.build_token_payload_from_result(
            result("2026-05-12", 0),
            7,
            "all",
            start_date="2026-05-12",
            end_date="2026-05-18",
        )
        empty_week["_cached_at_epoch"] = token_live_server.time.time()
        month_with_data = token_live_server.build_token_payload_from_result(
            result("2026-04-19", 300),
            30,
            "all",
            start_date="2026-04-19",
            end_date="2026-05-18",
        )
        month_with_data["_cached_at_epoch"] = token_live_server.time.time() - 10
        cached = {
            "version": 2,
            "entries": {
                "all|2026-05-12|2026-05-18": empty_week,
                "all|2026-04-19|2026-05-18": month_with_data,
            },
        }

        payload = token_live_server.build_payload_from_cache(
            cached,
            7,
            "all",
            start_date="2026-05-12",
            end_date="2026-05-18",
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["token_usage"]["today_total_tokens"], 300)
        self.assertEqual(payload["token_usage"]["active_period_count"], 1)

    def test_token_live_cache_prefers_newer_ccusage_data_timestamp(self):
        def result(range_start, total_tokens, fetched_at):
            return {
                "available": True,
                "provider": "all",
                "provider_label": token_fetcher.provider_display_name("all"),
                "window_days": 30,
                "range_start": range_start,
                "range_end": "2026-05-18",
                "payload": {
                    "daily": [
                        {
                            "date": "2026-05-18",
                            "provider": "all",
                            "inputTokens": total_tokens,
                            "cachedInputTokens": 0,
                            "outputTokens": 0,
                            "reasoningOutputTokens": 0,
                            "totalTokens": total_tokens,
                            "costUSD": 1.0,
                        }
                    ]
                },
                "error": "",
                "fetched_at": fetched_at,
            }

        older_week = token_live_server.build_token_payload_from_result(
            result("2026-05-12", 200, "2026-05-18T10:00:00+08:00"),
            7,
            "all",
            start_date="2026-05-12",
            end_date="2026-05-18",
        )
        older_week["_cached_at_epoch"] = token_live_server.time.time()
        newer_month = token_live_server.build_token_payload_from_result(
            result("2026-04-19", 300, "2026-05-18T10:05:00+08:00"),
            30,
            "all",
            start_date="2026-04-19",
            end_date="2026-05-18",
        )
        newer_month["_cached_at_epoch"] = token_live_server.time.time() - 10
        cached = {
            "version": 2,
            "entries": {
                "all|2026-05-12|2026-05-18": older_week,
                "all|2026-04-19|2026-05-18": newer_month,
            },
        }

        payload = token_live_server.build_payload_from_cache(
            cached,
            7,
            "all",
            start_date="2026-05-12",
            end_date="2026-05-18",
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["token_usage"]["today_total_tokens"], 300)
        self.assertEqual(payload["token_usage"]["refreshed_at"], "2026-05-18T10:05:00+08:00")
        self.assertEqual(payload["token_usage"]["today_refreshed_at"], "2026-05-18T10:05:00+08:00")

    def test_token_live_force_refresh_uses_month_all_source_and_updates_report_cache(self):
        def result(provider, total_tokens):
            return {
                "available": True,
                "provider": provider,
                "provider_label": token_fetcher.provider_display_name(provider),
                "window_days": 30,
                "range_start": "2026-04-19",
                "range_end": "2026-05-18",
                "payload": {
                    "daily": [
                        {
                            "date": "2026-05-18",
                            "provider": provider,
                            "inputTokens": total_tokens,
                            "cachedInputTokens": 0,
                            "outputTokens": 0,
                            "reasoningOutputTokens": 0,
                            "totalTokens": total_tokens,
                            "costUSD": 1.0,
                        }
                    ]
                },
                "error": "",
                "fetched_at": "2026-05-18T10:05:00+08:00",
            }

        all_result = result("all", 300)
        all_result["provider_results"] = {
            "codex": result("codex", 100),
            "claude": result("claude", 200),
        }
        captured = {}

        def fake_fetch(**kwargs):
            captured.update(kwargs)
            return all_result

        with TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "token-live-cache.json"
            report_cache_path = Path(tmpdir) / "token-usage-cache.json"
            old_cache_path = token_live_server.CACHE_PATH
            old_report_cache_path = token_live_server.REPORT_TOKEN_CACHE_PATH
            token_live_server.CACHE_PATH = cache_path
            token_live_server.REPORT_TOKEN_CACHE_PATH = report_cache_path
            try:
                with mock.patch.object(token_live_server, "fetch_ccusage_daily", side_effect=fake_fetch):
                    payload = token_live_server.refresh_token_cache(
                        7,
                        "codex",
                        start_date="2026-05-12",
                        end_date="2026-05-18",
                    )
                cached = token_live_server.load_cache()
                report_cached = json.loads(report_cache_path.read_text(encoding="utf-8"))
            finally:
                token_live_server.CACHE_PATH = old_cache_path
                token_live_server.REPORT_TOKEN_CACHE_PATH = old_report_cache_path

        self.assertEqual(captured["provider"], "all")
        self.assertEqual(captured["window_days"], 30)
        self.assertEqual(captured["start_date"].isoformat(), "2026-04-19")
        self.assertEqual(captured["end_date"].isoformat(), "2026-05-18")
        self.assertEqual(payload["provider"], "codex")
        self.assertEqual(payload["token_usage"]["provider"], "codex")
        self.assertEqual(payload["token_usage"]["range_start"], "2026-05-12")
        self.assertEqual(payload["token_usage"]["range_end"], "2026-05-18")
        self.assertEqual(payload["token_usage"]["today_total_tokens"], 100)
        self.assertIn("all|2026-04-19|2026-05-18", cached["entries"])
        self.assertEqual(report_cached["provider"], "all")
        self.assertEqual(report_cached["range_start"], "2026-04-19")
        self.assertEqual(report_cached["range_end"], "2026-05-18")

    def test_token_live_background_refresh_does_not_wait_for_fetch_lock(self):
        started = token_live_server.threading.Event()
        release = token_live_server.threading.Event()

        def fake_refresh(*args, **kwargs):
            started.set()
            release.wait(2)
            return {}

        old_in_flight = set(token_live_server.CACHE_REFRESH_IN_FLIGHT)
        token_live_server.CACHE_REFRESH_IN_FLIGHT.clear()
        try:
            with token_live_server.FETCH_LOCK, mock.patch.object(
                token_live_server,
                "refresh_token_cache",
                side_effect=fake_refresh,
            ):
                self.assertTrue(
                    token_live_server.start_token_cache_refresh_async(
                        7,
                        "all",
                        start_date="2026-05-05",
                        end_date="2026-05-11",
                    )
                )
                self.assertTrue(started.wait(1))
        finally:
            release.set()
            token_live_server.CACHE_REFRESH_IN_FLIGHT.clear()
            token_live_server.CACHE_REFRESH_IN_FLIGHT.update(old_in_flight)

    def test_token_resolver_does_not_fall_back_to_mismatched_cache(self):
        cached = {
            "available": True,
            "provider": "all",
            "window_days": 7,
            "payload": {"daily": []},
        }

        def fake_fetch(**kwargs):
            return {
                "available": False,
                "provider": kwargs.get("provider", "codex"),
                "provider_label": kwargs.get("provider", "codex"),
                "payload": {"daily": []},
                "error": "ccusage failed",
                "window_days": kwargs.get("window_days", 7),
            }

        with TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "token-cache.json"
            cache_path.write_text(json.dumps(cached), encoding="utf-8")

            result = token_fetcher.resolve_ccusage_daily(
                cache_path=cache_path,
                refresh_requested=False,
                fetch_func=fake_fetch,
                provider="codex",
                window_days=7,
            )

        self.assertFalse(result["available"])
        self.assertEqual(result["provider"], "codex")
        self.assertEqual(result["error"], "ccusage failed")

    def test_run_codex_consolidation_recreates_broken_auth_symlink(self):
        old_main_codex_home = nightly_consolidate.MAIN_CODEX_HOME
        old_nightly_codex_home = nightly_consolidate.NIGHTLY_CODEX_HOME
        old_runtime_dir = nightly_consolidate.RUNTIME_DIR
        old_codex_bin = nightly_consolidate.CODEX_BIN
        old_schema_path = nightly_consolidate.SCHEMA_PATH
        old_codex_model = nightly_consolidate.CODEX_MODEL
        try:
            with TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                main_codex_home = root / "main-codex-home"
                nightly_codex_home = root / "nightly-codex-home"
                runtime_dir = root / "runtime"
                schema_path = root / "schema.json"
                output_path = root / "out" / "summary.json"
                main_codex_home.mkdir()
                nightly_codex_home.mkdir()
                schema_path.write_text("{}", encoding="utf-8")
                (main_codex_home / "auth.json").write_text("{}", encoding="utf-8")
                (main_codex_home / "config.toml").write_text(
                    'model_provider = "DySearchTeam"\n'
                    'model = "gpt-5.4"\n'
                    "\n"
                    "[model_providers.DySearchTeam]\n"
                    'base_url = "https://proxy.example/api/modelhub/online/"\n'
                    "\n"
                    "[mcp_servers.notion]\n"
                    'url = "https://mcp.notion.com/mcp"\n'
                    "\n"
                    "[plugins.\"browser-use@openai-bundled\"]\n"
                    "enabled = true\n",
                    encoding="utf-8",
                )
                (nightly_codex_home / "auth.json").symlink_to(root / "missing-auth.json")
                (nightly_codex_home / "config.toml").write_text('model = "stale"\n', encoding="utf-8")

                nightly_consolidate.MAIN_CODEX_HOME = main_codex_home
                nightly_consolidate.NIGHTLY_CODEX_HOME = nightly_codex_home
                nightly_consolidate.RUNTIME_DIR = runtime_dir
                nightly_consolidate.CODEX_BIN = sys.executable
                nightly_consolidate.SCHEMA_PATH = schema_path
                nightly_consolidate.CODEX_MODEL = "gpt-5.4-mini"

                completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                with mock.patch.object(nightly_consolidate.subprocess, "run", return_value=completed) as run:
                    nightly_consolidate.run_codex_consolidation(
                        "prompt",
                        output_path,
                        language="zh",
                        timeout_seconds=321,
                    )

                auth_link = nightly_codex_home / "auth.json"
                self.assertTrue(auth_link.is_symlink())
                self.assertEqual(Path(os.readlink(auth_link)), main_codex_home / "auth.json")
                nightly_config = nightly_codex_home / "config.toml"
                self.assertFalse(nightly_config.is_symlink())
                nightly_config_text = nightly_config.read_text(encoding="utf-8")
                self.assertIn("DySearchTeam", nightly_config_text)
                self.assertNotIn("mcp_servers", nightly_config_text)
                self.assertNotIn("[plugins", nightly_config_text)
                command = run.call_args.args[0]
                self.assertIn("--sandbox", command)
                self.assertIn("read-only", command)
                self.assertIn("--disable", command)
                self.assertIn("--model", command)
                self.assertEqual(command[command.index("--model") + 1], "gpt-5.4-mini")
                self.assertIn('approval_policy="never"', command)
                self.assertIn('history.persistence="none"', command)
                self.assertEqual(run.call_args.kwargs["timeout"], 321)
        finally:
            nightly_consolidate.MAIN_CODEX_HOME = old_main_codex_home
            nightly_consolidate.NIGHTLY_CODEX_HOME = old_nightly_codex_home
            nightly_consolidate.RUNTIME_DIR = old_runtime_dir
            nightly_consolidate.CODEX_BIN = old_codex_bin
            nightly_consolidate.SCHEMA_PATH = old_schema_path
            nightly_consolidate.CODEX_MODEL = old_codex_model

    def test_run_model_consolidation_dispatches_to_configured_claude_cli(self):
        old_model_cli = nightly_consolidate.MODEL_CLI
        try:
            nightly_consolidate.MODEL_CLI = "claude"
            with TemporaryDirectory() as tmpdir:
                output_path = Path(tmpdir) / "summary.json"
                with mock.patch.object(nightly_consolidate, "run_claude_consolidation") as claude_run, mock.patch.object(
                    nightly_consolidate,
                    "run_codex_consolidation",
                ) as codex_run:
                    nightly_consolidate.run_model_consolidation(
                        "prompt",
                        output_path,
                        language="en",
                        timeout_seconds=12,
                    )

                claude_run.assert_called_once_with(
                    "prompt",
                    output_path,
                    language="en",
                    timeout_seconds=12,
                )
                codex_run.assert_not_called()
        finally:
            nightly_consolidate.MODEL_CLI = old_model_cli

    def test_claude_result_payload_accepts_json_schema_result(self):
        payload = {
            "type": "result",
            "is_error": False,
            "result": json.dumps({"date": "2026-05-04", "window_summaries": []}),
        }

        parsed = nightly_consolidate.claude_result_payload(json.dumps(payload))

        self.assertEqual(parsed["date"], "2026-05-04")

    def test_claude_result_payload_extracts_fenced_summary_result(self):
        summary = {
            "date": "2026-05-04",
            "day_summary": "ok",
            "window_summaries": [],
        }
        payload = {
            "type": "result",
            "is_error": False,
            "result": "Here is the summary:\n```json\n{}\n```".format(json.dumps(summary)),
        }

        parsed = nightly_consolidate.claude_result_payload(json.dumps(payload))

        self.assertEqual(parsed["day_summary"], "ok")

    def test_claude_result_payload_extracts_embedded_summary_before_wrapper_tail(self):
        summary = {
            "date": "2026-05-04",
            "day_summary": "ok",
            "window_summaries": [],
            "durable_memories": [],
        }
        payload = {
            "type": "result",
            "is_error": False,
            "result": "{}\n{}".format(
                json.dumps(summary),
                json.dumps({"terminal_reason": "completed", "uuid": "example"}),
            ),
        }

        parsed = nightly_consolidate.claude_result_payload(json.dumps(payload))

        self.assertEqual(parsed["date"], "2026-05-04")
        self.assertIn("durable_memories", parsed)

    def test_claude_result_payload_unwraps_content_blocks(self):
        summary = {
            "date": "2026-05-04",
            "day_summary": "ok",
            "window_summaries": [],
        }
        payload = {
            "type": "result",
            "is_error": False,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(summary)},
                ],
            },
        }

        parsed = nightly_consolidate.claude_result_payload(json.dumps(payload))

        self.assertEqual(parsed["date"], "2026-05-04")

    def test_build_claude_cli_env_drops_inherited_config_dir(self):
        env = asset_runtime.build_claude_cli_env(
            {
                "CLAUDE_HOME": "/tmp/claude-data",
                "CLAUDE_CONFIG_DIR": "/tmp/claude-data",
                "PATH": "/usr/bin",
            },
            claude_home=Path("/tmp/claude-data"),
        )

        self.assertEqual(env["CLAUDE_HOME"], "/tmp/claude-data")
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_build_claude_cli_env_keeps_explicit_env_file_config_dir(self):
        env = asset_runtime.build_claude_cli_env(
            {"CLAUDE_CONFIG_DIR": "/tmp/inherited"},
            claude_home=Path("/tmp/claude-data"),
            env_file_values={"CLAUDE_CONFIG_DIR": "/tmp/explicit-config"},
        )

        self.assertEqual(env["CLAUDE_HOME"], "/tmp/claude-data")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/tmp/explicit-config")

    def test_default_claude_binary_prefers_user_local_binary(self):
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            user_bin = home / ".local" / "bin" / "claude"
            path_bin = home / "homebrew" / "bin" / "claude"
            user_bin.parent.mkdir(parents=True)
            path_bin.parent.mkdir(parents=True)
            user_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            path_bin.write_text("#!/bin/sh\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"CLAUDE_BIN": ""}, clear=False), mock.patch.object(
                asset_runtime.Path,
                "home",
                return_value=home,
            ), mock.patch.object(asset_runtime.shutil, "which", return_value=str(path_bin)):
                self.assertEqual(asset_runtime.default_claude_binary(), str(user_bin))

    def test_default_codex_home_preserves_explicit_symlink_path(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            real_home = root / "codex-real"
            link_home = root / ".codex"
            real_home.mkdir()
            link_home.symlink_to(real_home, target_is_directory=True)

            with mock.patch.dict(os.environ, {"CODEX_HOME": str(link_home)}, clear=False):
                self.assertEqual(asset_runtime.default_codex_home(), link_home)

    def test_sync_codex_exec_home_tolerates_auth_symlink_race(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            main_codex_home = root / "main-codex-home"
            nightly_codex_home = root / "nightly-codex-home"
            main_codex_home.mkdir()
            nightly_codex_home.mkdir()
            source = main_codex_home / "auth.json"
            source.write_text("{}", encoding="utf-8")
            original_symlink_to = Path.symlink_to

            def racing_symlink_to(path, target, *args, **kwargs):
                if path == nightly_codex_home / "auth.json" and not path.exists() and not path.is_symlink():
                    original_symlink_to(path, target, *args, **kwargs)
                    raise FileExistsError(str(path))
                return original_symlink_to(path, target, *args, **kwargs)

            with mock.patch.object(Path, "symlink_to", racing_symlink_to):
                asset_runtime.sync_codex_exec_home(main_codex_home, nightly_codex_home)

            auth_link = nightly_codex_home / "auth.json"
            self.assertTrue(auth_link.is_symlink())
            self.assertEqual(Path(os.readlink(auth_link)), source)

    def test_sanitize_codex_exec_config_drops_interactive_extensions(self):
        source_config = """
model_provider = "DySearchTeam"
model = "gpt-5.5"
notify = ["/tmp/notifier"]

[model_providers.DySearchTeam]
name = "DySearchTeam"
base_url = "https://proxy.example/api/modelhub/online/"
wire_api = "responses"

[mcp_servers.notion]
url = "https://mcp.notion.com/mcp"

[features]
memories = true
codex_hooks = true

[plugins."browser-use@openai-bundled"]
enabled = true

[projects."/tmp/demo"]
trust_level = "trusted"
"""

        sanitized = asset_runtime.sanitize_codex_exec_config(source_config)

        self.assertIn('model_provider = "DySearchTeam"', sanitized)
        self.assertIn("[model_providers.DySearchTeam]", sanitized)
        self.assertIn('base_url = "https://proxy.example/api/modelhub/online/"', sanitized)
        self.assertNotIn("mcp_servers", sanitized)
        self.assertNotIn("notify", sanitized)
        self.assertNotIn("[features]", sanitized)
        self.assertNotIn("[plugins", sanitized)
        self.assertNotIn("[projects", sanitized)

    def test_sanitize_codex_exec_config_ignores_profile_model_provider(self):
        source_config = """
model_provider = "chatgpt_http"

[profiles.gpt55_stable]
model = "gpt-5.5"
model_provider = "chatgpt_http"

[model_providers.chatgpt_http]
name = "ChatGPT HTTP"
base_url = "https://chatgpt.example/backend-api/codex"
wire_api = "responses"
"""

        sanitized = asset_runtime.sanitize_codex_exec_config(source_config)

        self.assertEqual(sanitized.count('model_provider = "chatgpt_http"'), 1)
        self.assertNotIn("[profiles.gpt55_stable]", sanitized)
        self.assertIn("[model_providers.chatgpt_http]", sanitized)

    def test_nightly_output_schema_is_strict_for_codex_exec(self):
        schema = json.loads((ROOT / "templates" / "nightly-summary-schema.json").read_text(encoding="utf-8"))

        def walk(node, path="$"):
            if isinstance(node, dict):
                if node.get("type") == "object" and node.get("additionalProperties") is False:
                    properties = set((node.get("properties") or {}).keys())
                    required = set(node.get("required") or [])
                    self.assertEqual(required, properties, path)
                for key, value in node.items():
                    walk(value, "{}.{}".format(path, key))
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, "{}[{}]".format(path, index))

        walk(schema)

    def test_run_codex_consolidation_converts_timeout_to_model_error(self):
        old_main_codex_home = nightly_consolidate.MAIN_CODEX_HOME
        old_nightly_codex_home = nightly_consolidate.NIGHTLY_CODEX_HOME
        old_runtime_dir = nightly_consolidate.RUNTIME_DIR
        old_codex_bin = nightly_consolidate.CODEX_BIN
        old_schema_path = nightly_consolidate.SCHEMA_PATH
        try:
            with TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                main_codex_home = root / "main-codex-home"
                nightly_codex_home = root / "nightly-codex-home"
                runtime_dir = root / "runtime"
                schema_path = root / "schema.json"
                output_path = root / "out" / "summary.json"
                main_codex_home.mkdir()
                schema_path.write_text("{}", encoding="utf-8")

                nightly_consolidate.MAIN_CODEX_HOME = main_codex_home
                nightly_consolidate.NIGHTLY_CODEX_HOME = nightly_codex_home
                nightly_consolidate.RUNTIME_DIR = runtime_dir
                nightly_consolidate.CODEX_BIN = sys.executable
                nightly_consolidate.SCHEMA_PATH = schema_path

                timeout = subprocess.TimeoutExpired(
                    cmd=["codex", "exec"],
                    timeout=3,
                    output="partial output",
                    stderr="still running",
                )
                with mock.patch.object(nightly_consolidate.subprocess, "run", side_effect=timeout):
                    with self.assertRaises(nightly_consolidate.CodexConsolidationError) as raised:
                        nightly_consolidate.run_codex_consolidation(
                            "prompt",
                            output_path,
                            language="zh",
                            timeout_seconds=3,
                        )

                self.assertEqual(raised.exception.returncode, nightly_consolidate.CODEX_EXEC_TIMEOUT_RETURN_CODE)
                self.assertIn("timed out after 3 seconds", str(raised.exception))
        finally:
            nightly_consolidate.MAIN_CODEX_HOME = old_main_codex_home
            nightly_consolidate.NIGHTLY_CODEX_HOME = old_nightly_codex_home
            nightly_consolidate.RUNTIME_DIR = old_runtime_dir
            nightly_consolidate.CODEX_BIN = old_codex_bin
            nightly_consolidate.SCHEMA_PATH = old_schema_path

    def test_default_codex_exec_timeout_is_30_minutes(self):
        with mock.patch.dict(os.environ, {"OPENRELIX_CODEX_EXEC_TIMEOUT_SECONDS": ""}, clear=False):
            self.assertEqual(nightly_consolidate.default_codex_exec_timeout_seconds(), 30 * 60)

    def test_openrelix_help_uses_runtime_language(self):
        with mock.patch.object(openrelix, "LANGUAGE", "zh"):
            help_text = openrelix.build_parser().format_help()
        self.assertIn("OpenRelix 命令集", help_text)
        self.assertIn("运行指定日期的 review 流水线并打印摘要", help_text)
        self.assertIn("asset-stats", help_text)
        self.assertIn("位置参数", help_text)
        self.assertIn("显示帮助并退出", help_text)
        self.assertNotIn("Run today's review pipeline", help_text)
        self.assertNotIn("optional arguments", help_text)

        with mock.patch.object(openrelix, "LANGUAGE", "en"):
            help_text = openrelix.build_parser().format_help()
        self.assertIn("OpenRelix command set", help_text)
        self.assertIn("Run the review pipeline for a target date", help_text)
        self.assertIn("Build a single asset statistics snapshot", help_text)

    def test_openrelix_core_summary_uses_chinese_review_label(self):
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

        with mock.patch.object(openrelix, "LANGUAGE", "zh"), mock.patch("sys.stdout", stream):
            openrelix.print_core_summary(data)

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
        self.assertIn("移动端性能与体验评审", topic_labels)

    def test_project_context_window_links_carry_hover_tooltip_metadata(self):
        window_overview = {
            "date": "2026-05-08",
            "windows": [
                {
                    "window_id": "w-scan",
                    "display_index": 7,
                    "project_label": "Demo Project",
                    "cwd": "/tmp/demo-project",
                    "cwd_display": "Demo Project",
                    "window_title": "移动端录制链路",
                    "question_count": 3,
                    "conclusion_count": 2,
                    "question_summary": "长按录制链路怎么恢复",
                    "main_takeaway": "先停底层录制，再清理片段状态。",
                    "keywords": ["录制", "状态"],
                    "latest_activity_at": "2026-05-08T17:06:00+08:00",
                    "latest_activity_display": "05-08 17:06",
                    "started_at_display": "05-08 16:50",
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            ],
        }

        contexts = build_overview.build_project_contexts(window_overview)
        source_window = contexts[0]["topics"][0]["source_windows"][0]
        html = build_overview.make_project_context_cards(contexts)

        self.assertEqual(source_window["display_label"], "7")
        self.assertEqual(source_window["started_at_display"], "05-08 16:50")
        self.assertEqual(source_window["takeaway"], "先停底层录制，再清理片段状态。")
        self.assertIn('data-window-tooltip="true"', html)
        self.assertIn('data-window-tooltip-title="移动端录制链路"', html)
        self.assertIn('data-window-tooltip-takeaway="先停底层录制，再清理片段状态。"', html)
        self.assertIn('data-window-tooltip-created="05-08 16:50"', html)
        self.assertIn('data-window-tooltip-meta="3 问题 · 2 结论"', html)
        self.assertIn(">移动端录制链路<", html)
        self.assertNotIn(">窗口 7<", html)

    def test_project_contexts_sort_projects_by_discussion_count(self):
        window_overview = {
            "date": "2026-04-27",
            "windows": [
                {
                    "project_label": "Quiet Latest",
                    "cwd": "/tmp/quiet",
                    "cwd_display": "Quiet Latest",
                    "question_count": 1,
                    "conclusion_count": 1,
                    "question_summary": "最近但讨论少",
                    "main_takeaway": "讨论少",
                    "keywords": [],
                    "latest_activity_at": "2026-04-27T12:00:00+08:00",
                    "latest_activity_display": "04-27 12:00",
                    "recent_prompts": [],
                    "recent_conclusions": [],
                },
                {
                    "project_label": "Busy Older",
                    "cwd": "/tmp/busy",
                    "cwd_display": "Busy Older",
                    "question_count": 5,
                    "conclusion_count": 4,
                    "question_summary": "更早但讨论多",
                    "main_takeaway": "讨论多",
                    "keywords": [],
                    "latest_activity_at": "2026-04-27T10:00:00+08:00",
                    "latest_activity_display": "04-27 10:00",
                    "recent_prompts": [],
                    "recent_conclusions": [],
                },
            ],
        }

        contexts = build_overview.build_project_contexts(window_overview)

        self.assertEqual([item["label"] for item in contexts], ["Busy Older", "Quiet Latest"])

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
        self.assertNotEqual(
            build_overview.infer_context_topic_label(
                {
                    "project_label": "OpenRelix",
                    "cwd": "/tmp/openrelix",
                    "question_summary": "把核心功能做成真实页面演示录屏，追加到汇报文档里",
                    "main_takeaway": "已经生成 4 个独立 mp4 功能演示，放到 OpenRelix 文档最后。",
                    "keywords": [],
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            ),
            "移动端扫描/录制链路",
        )
        self.assertNotEqual(
            build_overview.infer_context_topic_label(
                {
                    "project_label": "OpenRelix",
                    "cwd": "/tmp/openrelix",
                    "question_summary": "项目与窗口标题展示修正",
                    "main_takeaway": (
                        "项目和窗口看起来对不上的根因是过宽的分类规则和不稳定的追溯展示，"
                        "OpenRelix 的录屏窗口被误分到移动端扫描/录制链路。"
                    ),
                    "keywords": [],
                    "recent_prompts": [],
                    "recent_conclusions": [
                        {
                            "text": "收紧移动端扫描/录制链路的聚合规则，并把追溯 chip 改成窗口标题。"
                        }
                    ],
                }
            ),
            "移动端扫描/录制链路",
        )
        self.assertEqual(
            build_overview.infer_context_topic_label(
                {
                    "project_label": "OpenRelix",
                    "cwd": "/tmp/openrelix",
                    "question_summary": "用户希望把产品规划写给不熟背景的人看，后续还追加了演示录屏。",
                    "main_takeaway": "OpenRelix 的产品规划、阶段说明和演示素材都被整理成对外材料。",
                    "keywords": ["产品规划", "截图", "录屏", "路线图"],
                    "recent_prompts": [],
                    "recent_conclusions": [
                        {"text": "正文包含历史窗口检索，但任务本身是产品文档和真实演示素材。"}
                    ],
                }
            ),
            "产品文档与演示素材",
        )
        self.assertEqual(
            build_overview.infer_context_topic_label(
                {
                    "project_label": "OpenRelix",
                    "cwd": "/tmp/openrelix",
                    "question_summary": "用户给了飞书文档链接，希望按照当前逻辑更新一版开发者指南。",
                    "main_takeaway": "开发者指南已经刷新到 0.3.1 口径，补了当前真实逻辑和边界。",
                    "keywords": ["开发者指南", "0.3.1", "记忆边界", "fallback"],
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            ),
            "OpenRelix 文档与发布",
        )
        self.assertEqual(
            build_overview.infer_context_topic_label(
                {
                    "project_label": "OpenRelix",
                    "cwd": "/tmp/openrelix",
                    "question_summary": "用户从 worktree 打包后发现深度回溯窗口没有智能总结。",
                    "main_takeaway": "历史窗口没做智能总结的根因是跨天渲染层没接历史 summary。",
                    "keywords": ["历史 summary", "跨天", "窗口筛选", "打包"],
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            ),
            "历史窗口摘要修复",
        )
        self.assertEqual(
            build_overview.infer_context_topic_label(
                {
                    "project_label": "OpenRelix",
                    "cwd": "/tmp/openrelix",
                    "question_summary": "用户要求把日期弹层做得更像液态玻璃。",
                    "main_takeaway": "OpenRelix 的日期弹层和本机运行态都做了修正。",
                    "keywords": ["液态玻璃", "日期选择器", "build_overview", "LaunchAgent"],
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            ),
            "面板交互与视觉优化",
        )
        self.assertEqual(
            build_overview.infer_context_topic_label(
                {
                    "project_label": "Douyin",
                    "cwd": "/tmp/Douyin",
                    "question_summary": "视搜结果页点击内容反馈后没有删除对应卡片。",
                    "main_takeaway": "反馈页只打开 schema 不删卡的问题已定位到 JSB 路由。",
                    "keywords": ["视搜", "反馈", "删卡", "bugfix"],
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            ),
            "移动端反馈交互修复",
        )

    def test_business_task_meta_keeps_process_words_as_status_tags(self):
        item = {
            "window_title": "修复 FeatureOrbit Bridge42 main lane",
            "question_summary": "修复 FeatureOrbit Bridge42 main lane",
            "main_takeaway": "本地编译通过，并完成本地提交。",
            "keywords": [],
            "recent_prompts": [],
            "recent_conclusions": [
                {"text": "[KMP_CLI_LOG] compile passed"},
                {"text": "git commit completed"},
            ],
        }

        topic_label = build_overview.infer_context_topic_label(item)
        task_meta = build_overview.infer_business_task_meta(item, topic_label=topic_label)

        self.assertEqual(topic_label, "移动端编译/类型错误")
        self.assertEqual(task_meta["label"], "FeatureOrbit Bridge42 main lane")
        self.assertEqual(task_meta["key"], "featureorbitbridge42mainlane")
        self.assertIn("featureorbit", task_meta["terms"])
        self.assertIn("bridge42", task_meta["terms"])
        self.assertIn("编译", task_meta["status_tags"])
        self.assertIn("提交", task_meta["status_tags"])

        followup_meta = build_overview.infer_business_task_meta(
            {
                "window_title": "继续任务，之前断网了",
                "question_summary": "继续任务，之前断网了",
                "main_takeaway": "编译通过。",
                "keywords": [],
                "recent_prompts": [],
                "recent_conclusions": [],
            },
            topic_label="移动端编译/类型错误",
        )
        self.assertEqual(followup_meta["label"], "之前断网了")

    def test_project_context_groups_exact_business_task_across_process_topics(self):
        windows = []
        for index, (title, takeaway) in enumerate(
            [
                ("修复 FeatureOrbit Bridge42 main lane", "本地编译通过。"),
                ("FeatureOrbit Bridge42 main lane", "已完成本地提交。"),
                ("FeatureOrbit fallback view", "完成。"),
            ],
            1,
        ):
            windows.append(
                {
                    "window_id": "w{}".format(index),
                    "project_label": "GenericApp",
                    "cwd": "/tmp/generic-app",
                    "cwd_display": "GenericApp",
                    "window_title": title,
                    "question_summary": title,
                    "main_takeaway": takeaway,
                    "keywords": [],
                    "question_count": 1,
                    "conclusion_count": 1,
                    "latest_activity_at": "2026-05-14T10:0{}:00+08:00".format(index),
                    "latest_activity_display": "05-14 10:0{}".format(index),
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            )

        contexts = build_overview.build_project_contexts({"windows": windows})

        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["topic_count"], 2)
        topic_counts = {topic["label"]: topic["window_count"] for topic in contexts[0]["topics"]}
        self.assertEqual(topic_counts["FeatureOrbit Bridge42 main lane"], 2)
        self.assertEqual(topic_counts["FeatureOrbit fallback view"], 1)

    def test_task_summary_normalizes_clusters_from_existing_window_summaries(self):
        task_input = {
            "date_range": {"from": "2026-05-14", "to": "2026-05-14"},
            "source_summary_dates": ["2026-05-14"],
            "windows": [
                {
                    "window_id": "w-resou-a",
                    "cwd": "/tmp/staggerOpt",
                    "project_label": "staggerOpt",
                    "window_title": "图文重搜 GS BTM 参数修复",
                    "question_summary": "图文重搜 GS BTM 参数修复",
                    "main_takeaway": "编译通过。",
                },
                {
                    "window_id": "w-resou-b",
                    "cwd": "/tmp/staggerOpt",
                    "project_label": "staggerOpt",
                    "window_title": "视频重搜空态改为复用绿搜",
                    "question_summary": "视频重搜空态改为复用绿搜",
                    "main_takeaway": "完成验证。",
                },
            ],
        }
        model_payload = {
            "project_task_clusters": [
                {
                    "cluster_id": "resou-empty-state",
                    "project_label": "staggerOpt",
                    "cwd": "/tmp/staggerOpt",
                    "task_title": "重搜空态与参数链路修复",
                    "task_summary": "图文重搜和视频重搜都围绕同一条重搜链路。",
                    "source_window_ids": ["w-resou-a", "w-resou-b", "missing"],
                    "status_tags": ["编译", "验证"],
                    "confidence": "high",
                }
            ]
        }

        normalized = openrelix_task_summary.normalize_task_summary_payload(model_payload, task_input)

        self.assertEqual(normalized["task_cluster_algorithm_version"], openrelix_task_summary.TASK_CLUSTER_ALGORITHM_VERSION)
        self.assertEqual(len(normalized["project_task_clusters"]), 1)
        cluster = normalized["project_task_clusters"][0]
        self.assertEqual(cluster["task_title"], "重搜空态与参数链路修复")
        self.assertEqual(cluster["source_window_ids"], ["w-resou-a", "w-resou-b"])
        self.assertEqual(normalized["source_window_ids"], ["w-resou-a", "w-resou-b"])
        self.assertIn("编译", cluster["status_tags"])
        self.assertIn("source_fingerprint", normalized)

    def test_task_summary_drops_cross_scope_model_cluster_members(self):
        task_input = {
            "date_range": {"from": "2026-05-14", "to": "2026-05-14"},
            "source_summary_dates": ["2026-05-14"],
            "windows": [
                {
                    "window_id": "w-openrelix",
                    "cwd": "/tmp/openrelix",
                    "project_label": "OpenRelix",
                    "window_title": "面板任务聚合",
                    "question_summary": "面板任务聚合",
                    "main_takeaway": "完成。",
                },
                {
                    "window_id": "w-douyin",
                    "cwd": "/tmp/douyin",
                    "project_label": "Douyin",
                    "window_title": "移动端扫描链路",
                    "question_summary": "移动端扫描链路",
                    "main_takeaway": "完成。",
                },
            ],
        }
        model_payload = {
            "project_task_clusters": [
                {
                    "cluster_id": "mixed-projects",
                    "project_label": "Wrong",
                    "cwd": "/tmp/wrong",
                    "task_title": "错误跨项目聚合",
                    "source_window_ids": ["w-openrelix", "w-douyin"],
                    "confidence": "high",
                }
            ]
        }

        normalized = openrelix_task_summary.normalize_task_summary_payload(model_payload, task_input)

        self.assertEqual(len(normalized["project_task_clusters"]), 1)
        cluster = normalized["project_task_clusters"][0]
        self.assertEqual(cluster["source_window_ids"], ["w-openrelix"])
        self.assertEqual(cluster["project_label"], "OpenRelix")
        self.assertEqual(cluster["cwd"], "/tmp/openrelix")

    def test_task_summary_model_failure_messages_suppress_raw_output(self):
        error = openrelix_task_summary.describe_model_failure(
            stdout="partial summary PRIVATE_PATH token sk-testsecret123456",
            stderr='{"access_token":"secret-token","task":"internal work"}',
            returncode=1,
        )

        self.assertIn("exit code 1", error)
        self.assertIn("原始输出", error)
        self.assertNotIn("sk-testsecret", error)
        self.assertNotIn("access_token", error)
        self.assertNotIn("PRIVATE_PATH", error)

    def test_task_summary_migration_state_clears_stale_failure_fields(self):
        with TemporaryDirectory() as tmpdir:
            paths = make_runtime_paths_for_test(tmpdir)
            openrelix_task_summary.write_task_summary_migration_state(
                paths,
                status="failed",
                error="model failed",
                failed_at="2026-05-19T01:00:00+08:00",
            )

            state = openrelix_task_summary.write_task_summary_migration_state(
                paths,
                status="completed",
                reason="migration_completed",
            )

            self.assertEqual(state["status"], "completed")
            self.assertNotIn("error", state)
            self.assertNotIn("failed_at", state)

    def test_project_context_prefers_model_task_cluster(self):
        windows = []
        for index, title in enumerate(
            [
                "图文重搜 GS BTM 参数修复",
                "视频重搜空态改为复用绿搜",
            ],
            1,
        ):
            windows.append(
                {
                    "window_id": "w{}".format(index),
                    "project_label": "staggerOpt",
                    "cwd": "/tmp/staggerOpt",
                    "cwd_display": "staggerOpt",
                    "window_title": title,
                    "question_summary": title,
                    "main_takeaway": "编译和验证完成。",
                    "keywords": [],
                    "question_count": 1,
                    "conclusion_count": 1,
                    "latest_activity_at": "2026-05-14T10:0{}:00+08:00".format(index),
                    "latest_activity_display": "05-14 10:0{}".format(index),
                    "recent_prompts": [],
                    "recent_conclusions": [],
                    "task_cluster_id": "resou-empty-state",
                    "task_cluster_label": "重搜空态与参数链路修复",
                    "task_cluster_confidence": "high",
                    "task_cluster_source": "model",
                }
            )

        contexts = build_overview.build_project_contexts({"windows": windows})

        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["topic_count"], 1)
        self.assertEqual(contexts[0]["topics"][0]["label"], "重搜空态与参数链路修复")
        self.assertEqual(contexts[0]["topics"][0]["window_count"], 2)

    def test_window_task_cluster_index_reads_runtime_artifact(self):
        old_paths = build_overview.PATHS
        try:
            with TemporaryDirectory() as tmpdir:
                paths = make_runtime_paths_for_test(tmpdir)
                artifact_dir = paths.consolidated_dir / "task_summaries"
                artifact_dir.mkdir(parents=True, exist_ok=True)
                (artifact_dir / "2026-05-14_2026-05-14.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "task_cluster_algorithm_version": build_overview.TASK_CLUSTER_ALGORITHM_VERSION,
                            "date_range": {"from": "2026-05-14", "to": "2026-05-14"},
                            "source_summary_dates": ["2026-05-14"],
                            "source_fingerprint": "demo",
                            "project_task_clusters": [
                                {
                                    "cluster_id": "resou-empty-state",
                                    "project_label": "staggerOpt",
                                    "cwd": "/tmp/staggerOpt",
                                    "task_title": "重搜空态与参数链路修复",
                                    "task_summary": "",
                                    "source_window_ids": ["w1"],
                                    "status_tags": [],
                                    "confidence": "medium",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                build_overview.PATHS = paths
                build_overview.load_window_task_cluster_index.cache_clear()

                items = [{"window_id": "w1"}]
                build_overview.apply_window_task_clusters(items)

                self.assertEqual(items[0]["task_cluster_id"], "resouemptystate")
                self.assertEqual(items[0]["task_cluster_label"], "重搜空态与参数链路修复")
                self.assertEqual(items[0]["task_cluster_source"], "model")
        finally:
            build_overview.PATHS = old_paths
            build_overview.load_window_task_cluster_index.cache_clear()

    def test_newer_task_summary_artifact_tombstones_stale_clusters(self):
        old_paths = build_overview.PATHS
        try:
            with TemporaryDirectory() as tmpdir:
                paths = make_runtime_paths_for_test(tmpdir)
                artifact_dir = paths.consolidated_dir / "task_summaries"
                artifact_dir.mkdir(parents=True, exist_ok=True)
                older = artifact_dir / "2026-05-10_2026-05-16.json"
                newer = artifact_dir / "2026-05-11_2026-05-17.json"
                older.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "task_cluster_algorithm_version": build_overview.TASK_CLUSTER_ALGORITHM_VERSION,
                            "date_range": {"from": "2026-05-10", "to": "2026-05-16"},
                            "source_summary_dates": ["2026-05-16"],
                            "source_window_ids": ["w1"],
                            "source_fingerprint": "old",
                            "project_task_clusters": [
                                {
                                    "cluster_id": "old-resou",
                                    "project_label": "staggerOpt",
                                    "cwd": "/tmp/staggerOpt",
                                    "task_title": "旧重搜任务",
                                    "task_summary": "",
                                    "source_window_ids": ["w1"],
                                    "status_tags": [],
                                    "confidence": "high",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                newer.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "task_cluster_algorithm_version": build_overview.TASK_CLUSTER_ALGORITHM_VERSION,
                            "date_range": {"from": "2026-05-11", "to": "2026-05-17"},
                            "source_summary_dates": ["2026-05-17"],
                            "source_window_ids": ["w1"],
                            "source_fingerprint": "new",
                            "project_task_clusters": [
                                {
                                    "cluster_id": "new-low-confidence",
                                    "project_label": "staggerOpt",
                                    "cwd": "/tmp/staggerOpt",
                                    "task_title": "新低置信任务",
                                    "task_summary": "",
                                    "source_window_ids": ["w1"],
                                    "status_tags": [],
                                    "confidence": "low",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                os.utime(older, (100, 100))
                os.utime(newer, (200, 200))
                build_overview.PATHS = paths
                build_overview.load_window_task_cluster_index.cache_clear()

                items = [{"window_id": "w1"}]
                build_overview.apply_window_task_clusters(items)

                self.assertNotIn("task_cluster_id", items[0])
        finally:
            build_overview.PATHS = old_paths
            build_overview.load_window_task_cluster_index.cache_clear()

    def test_task_summary_failure_writes_tombstone_artifact(self):
        with TemporaryDirectory() as tmpdir:
            paths = make_runtime_paths_for_test(tmpdir)
            summary_dir = paths.consolidated_daily_dir / "2026-05-14"
            summary_dir.mkdir(parents=True, exist_ok=True)
            (summary_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-14",
                        "window_summaries": [
                            {
                                "window_id": "w1",
                                "cwd": "/tmp/staggerOpt",
                                "window_title": "图文重搜参数修复",
                                "question_summary": "图文重搜参数修复",
                                "main_takeaway": "模型失败时回退规则聚合。",
                                "question_count": 1,
                                "conclusion_count": 1,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            def fail_after_candidate(_, output_path, **__):
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_text(
                    '{"project_task_clusters":[{"task_title":"raw private output"}]}',
                    encoding="utf-8",
                )
                raise openrelix_task_summary.TaskSummaryModelError(
                    1,
                    stdout="PRIVATE_OUTPUT sk-testsecret123456",
                    stderr='{"access_token":"secret-token"}',
                )

            with mock.patch.object(openrelix_task_summary, "run_model_task_summary", side_effect=fail_after_candidate):
                with self.assertRaises(openrelix_task_summary.TaskSummaryModelError):
                    openrelix_task_summary.run_task_summary_for_dates(
                        ["2026-05-14"],
                        paths=paths,
                        force=True,
                    )

            artifact = paths.consolidated_dir / "task_summaries" / "2026-05-14_2026-05-14.json"
            self.assertFalse((paths.consolidated_dir / "task_summaries" / "2026-05-14_2026-05-14.candidate.tmp").exists())
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(payload["model_status"], "failed")
            self.assertEqual(payload["source_window_ids"], ["w1"])
            self.assertEqual(payload["project_task_clusters"], [])
            self.assertIn("error", payload)
            self.assertNotIn("PRIVATE_OUTPUT", payload["error"])
            self.assertNotIn("access_token", payload["error"])

    def test_window_task_cluster_index_ignores_candidate_artifacts(self):
        old_paths = build_overview.PATHS
        try:
            with TemporaryDirectory() as tmpdir:
                paths = make_runtime_paths_for_test(tmpdir)
                artifact_dir = paths.consolidated_dir / "task_summaries"
                artifact_dir.mkdir(parents=True, exist_ok=True)
                (artifact_dir / "2026-05-14_2026-05-14.candidate.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "task_cluster_algorithm_version": build_overview.TASK_CLUSTER_ALGORITHM_VERSION,
                            "date_range": {"from": "2026-05-14", "to": "2026-05-14"},
                            "source_summary_dates": ["2026-05-14"],
                            "project_task_clusters": [
                                {
                                    "cluster_id": "raw-candidate",
                                    "project_label": "Wrong",
                                    "cwd": "/tmp/wrong",
                                    "task_title": "未归一化候选输出",
                                    "task_summary": "",
                                    "source_window_ids": ["w1"],
                                    "status_tags": [],
                                    "confidence": "high",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                build_overview.PATHS = paths
                build_overview.load_window_task_cluster_index.cache_clear()

                items = [{"window_id": "w1"}]
                build_overview.apply_window_task_clusters(items)

                self.assertNotIn("task_cluster_id", items[0])
        finally:
            build_overview.PATHS = old_paths
            build_overview.load_window_task_cluster_index.cache_clear()

    def test_task_summary_artifact_order_prefers_generated_at_over_mtime(self):
        old_paths = build_overview.PATHS
        try:
            with TemporaryDirectory() as tmpdir:
                paths = make_runtime_paths_for_test(tmpdir)
                artifact_dir = paths.consolidated_dir / "task_summaries"
                artifact_dir.mkdir(parents=True, exist_ok=True)
                older = artifact_dir / "2026-05-10_2026-05-16.json"
                newer = artifact_dir / "2026-05-11_2026-05-17.json"
                older.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "task_cluster_algorithm_version": build_overview.TASK_CLUSTER_ALGORITHM_VERSION,
                            "date_range": {"from": "2026-05-10", "to": "2026-05-16"},
                            "source_summary_dates": ["2026-05-16"],
                            "source_window_ids": ["w1"],
                            "source_fingerprint": "old",
                            "generated_at": "2026-05-16T23:00:00+08:00",
                            "project_task_clusters": [
                                {
                                    "cluster_id": "old-resou",
                                    "project_label": "staggerOpt",
                                    "cwd": "/tmp/staggerOpt",
                                    "task_title": "旧重搜任务",
                                    "task_summary": "",
                                    "source_window_ids": ["w1"],
                                    "status_tags": [],
                                    "confidence": "high",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                newer.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "task_cluster_algorithm_version": build_overview.TASK_CLUSTER_ALGORITHM_VERSION,
                            "date_range": {"from": "2026-05-11", "to": "2026-05-17"},
                            "source_summary_dates": ["2026-05-17"],
                            "source_window_ids": ["w1"],
                            "source_fingerprint": "new",
                            "generated_at": "2026-05-17T01:00:00+08:00",
                            "project_task_clusters": [
                                {
                                    "cluster_id": "new-low-confidence",
                                    "project_label": "staggerOpt",
                                    "cwd": "/tmp/staggerOpt",
                                    "task_title": "新低置信任务",
                                    "task_summary": "",
                                    "source_window_ids": ["w1"],
                                    "status_tags": [],
                                    "confidence": "low",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                os.utime(older, (300, 300))
                os.utime(newer, (100, 100))
                build_overview.PATHS = paths
                build_overview.load_window_task_cluster_index.cache_clear()

                items = [{"window_id": "w1"}]
                build_overview.apply_window_task_clusters(items)

                self.assertNotIn("task_cluster_id", items[0])
        finally:
            build_overview.PATHS = old_paths
            build_overview.load_window_task_cluster_index.cache_clear()

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
                                        "cwd": "/tmp/OpenRelix",
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

    def test_project_context_topics_render_as_compact_task_chips(self):
        topics = [
            {
                "label": "Topic {}".format(index),
                "window_count": index,
                "latest_activity_display": "04-27 1{}:00".format(index),
                "question_preview": "Question {}".format(index),
                "takeaway_preview": "Takeaway {}".format(index),
                "keywords": ["kw{}".format(index)],
            }
            for index in range(1, 8)
        ]

        cards_html = build_overview.make_project_context_cards(
            [
                {
                    "label": "OpenRelix",
                    "window_count": 6,
                    "question_count": 6,
                    "conclusion_count": 6,
                    "latest_activity_display": "04-27 20:00",
                    "cwd_preview": "/tmp/OpenRelix",
                    "question_preview": "面板可视化需要二次归类",
                    "takeaway_preview": "项目上下文需要支持展开",
                    "keywords": ["panel"],
                    "topics": topics,
                }
            ]
        )

        self.assertNotIn('<article class="context-topic">', cards_html)
        self.assertIn("并行任务", cards_html)
        self.assertIn("Topic 7", cards_html)
        self.assertIn("Topic 3", cards_html)
        self.assertIn("查看更多 2 个任务", cards_html)
        self.assertLess(cards_html.index("Topic 7"), cards_html.index("Topic 6"))
        self.assertLess(cards_html.index("Topic 3"), cards_html.index("查看更多 2 个任务"))
        self.assertGreater(cards_html.index("Topic 2"), cards_html.index("查看更多 2 个任务"))
        self.assertNotIn("Question 7", cards_html)
        self.assertNotIn("Takeaway 7", cards_html)
        self.assertNotIn("窗口明细中展开", cards_html)

    def test_project_context_cards_show_three_projects_then_more(self):
        cards_html = build_overview.make_project_context_cards(
            [
                {
                    "label": "Project {}".format(index),
                    "window_count": index,
                    "question_count": index,
                    "conclusion_count": 0,
                    "latest_activity_display": "05-08 1{}:00".format(index),
                    "cwd_preview": "/tmp/project-{}".format(index),
                    "topics": [],
                }
                for index in range(1, 5)
            ]
        )

        self.assertIn("Project 1", cards_html)
        self.assertIn("Project 2", cards_html)
        self.assertIn("Project 3", cards_html)
        self.assertIn("Project 4", cards_html)
        self.assertIn("查看更多 1 个项目", cards_html)
        self.assertIn("收起更多项目", cards_html)
        self.assertLess(cards_html.index("Project 3"), cards_html.index("查看更多 1 个项目"))
        self.assertGreater(cards_html.index("Project 4"), cards_html.index("查看更多 1 个项目"))
        self.assertNotIn("查看更多 1 组窗口总览", cards_html)

    def test_project_context_cards_link_to_source_windows(self):
        cards_html = build_overview.make_project_context_cards(
            [
                {
                    "label": "OpenRelix",
                    "window_count": 1,
                    "question_count": 1,
                    "conclusion_count": 1,
                    "latest_activity_display": "05-07 21:40",
                    "cwd_preview": "OpenRelix",
                    "question_preview": "项目上下文需要更清晰",
                    "takeaway_preview": "来源窗口要能跳到窗口明细",
                    "keywords": ["openrelix"],
                    "source_windows": [
                        {
                            "window_id": "w-context",
                            "anchor_id": "window-w-context",
                            "display_label": "3",
                            "latest_activity_display": "05-07 21:40",
                            "title": "来源窗口联动",
                        }
                    ],
                    "topics": [
                        {
                            "label": "面板可视化",
                            "window_count": 1,
                            "latest_activity_display": "05-07 21:40",
                            "question_preview": "如何快速看懂项目脉络",
                            "takeaway_preview": "先看地图再追溯窗口",
                            "keywords": ["panel"],
                            "source_windows": [
                                {
                                    "window_id": "w-context",
                                    "anchor_id": "window-w-context",
                                    "display_label": "3",
                                    "title": "窗口筛选联动",
                                },
                                {
                                    "window_id": "w-followup",
                                    "anchor_id": "window-w-followup",
                                    "display_label": "4",
                                    "title": "追溯标题展示",
                                },
                                {
                                    "window_id": "w-third",
                                    "anchor_id": "window-w-third",
                                    "display_label": "5",
                                    "title": "项目聚合修复",
                                },
                                {
                                    "window_id": "w-fourth",
                                    "anchor_id": "window-w-fourth",
                                    "display_label": "6",
                                    "title": "窗口标题截断",
                                },
                                {
                                    "window_id": "w-fifth",
                                    "anchor_id": "window-w-fifth",
                                    "display_label": "7",
                                    "title": "超出两行展开",
                                }
                            ],
                        }
                    ],
                }
            ]
        )

        self.assertIn('href="#window-w-context"', cards_html)
        self.assertIn('href="#window-w-followup"', cards_html)
        self.assertIn('href="#window-w-fifth"', cards_html)
        self.assertIn('data-window-target="window-w-context"', cards_html)
        self.assertIn(">窗口筛选联动<", cards_html)
        self.assertIn("查看更多 1 个窗口", cards_html)
        self.assertIn("追溯", cards_html)

    def test_project_context_cards_limit_topics_and_expand_extra(self):
        topics = [
            {
                "label": "任务 {}".format(index),
                "window_count": 7 - index,
                "question_count": 1,
                "conclusion_count": 0,
                "latest_activity_display": "05-07 21:4{}".format(index),
                "source_windows": [],
            }
            for index in range(1, 7)
        ]
        cards_html = build_overview.make_project_context_cards(
            [
                {
                    "label": "OpenRelix",
                    "window_count": 6,
                    "question_count": 6,
                    "conclusion_count": 0,
                    "latest_activity_display": "05-07 21:40",
                    "cwd_preview": "OpenRelix",
                    "topics": topics,
                }
            ]
        )

        self.assertIn("任务 1", cards_html)
        self.assertIn("任务 5", cards_html)
        self.assertIn("任务 6", cards_html)
        self.assertIn("查看更多 1 个任务", cards_html)
        self.assertIn("收起更多任务", cards_html)
        self.assertLess(cards_html.index("任务 5"), cards_html.index("查看更多 1 个任务"))
        self.assertGreater(cards_html.index("任务 6"), cards_html.index("查看更多 1 个任务"))

    def test_parse_nightly_summary_date_fails_closed(self):
        self.assertIsNone(build_overview.parse_nightly_summary_date({"date": "bad-date"}))

    def test_parse_codex_native_memory_summary_keeps_command_titles_intact(self):
        self._use_personal_codex_rules(
            title={
                "example review live contract and independent cli review loop": "示例独立审阅流程",
            },
            note={
                "example review live contract and independent cli review loop": "记录示例命令入口、临时 git snapshot 和评分闭环。",
            },
        )

        sample_summary = """## User preferences

- Prefer exact values first.

## General Tips

- Keep the global layer repo-agnostic.

## What's in Memory

### OpenRelix + user-level Codex state

#### 2026-04-26

- `/example:review` live contract and independent CLI review loop: /example:review, codex exec, temp git repo, 10/10
  - desc: Cross-scope workflow memory for external review requests under an example workspace.
  - learnings: Treat /example:review as the validated live entrypoint.
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
                known_project_names=["OpenRelix", "Android App"],
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
        self.assertIn("/example:review", row["title"])
        self.assertIn("示例独立审阅流程", row["display_title"])
        self.assertIn("临时 git snapshot", row["display_value_note"])
        self.assertEqual(row["created_at"], "2026-04-26")
        self.assertIn("OpenRelix", row["context_labels"])
        self.assertEqual(row["source_fact_label"], "来源文件")

    def _use_personal_codex_rules(self, **extras):
        """Inject test fixtures into build_overview._PERSONAL_CODEX_NATIVE_RULES for one test.

        The engine ships with empty defaults; rule data lives outside the repo.
        These helpers let us exercise the matching logic with synthetic fixtures
        that contain no real personal project names.
        """
        base = self._empty_personal_codex_rules()
        base.update(extras)
        original = build_overview._PERSONAL_CODEX_NATIVE_RULES
        build_overview._PERSONAL_CODEX_NATIVE_RULES = base
        self.addCleanup(lambda: setattr(build_overview, "_PERSONAL_CODEX_NATIVE_RULES", original))

    def test_codex_native_memory_known_english_topics_get_chinese_display_copy(self):
        self._use_personal_codex_rules(
            title={
                "openrelix fixture topic alpha key": "示例主题 A",
                "openrelix fixture topic beta key": "示例主题 B",
            },
            note={
                "openrelix fixture topic alpha key": "覆盖示例主题 A 的样例描述。",
                "openrelix fixture topic beta key": "覆盖示例主题 B 的样例描述。",
            },
            topic_rules=[
                {
                    "fragments": ["openrelix-fixture-topic-rule", "sample retrieval"],
                    "title": "示例主题规则",
                    "body": "通过规则匹配命中的示例主题描述。",
                },
            ],
        )

        sample_summary = """## What's in Memory

### Sample fixtures

- OpenRelix fixture topic alpha key: marker-alpha
  - desc: Sample topic alpha description.
  - learnings: Sample topic alpha learning.

- OpenRelix fixture topic beta key: marker-beta
  - desc: Sample topic beta description.

- OpenRelix-fixture-topic-rule sample retrieval token: marker-rule
  - desc: Sample topic exercising rule-based matching.

        """

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path)

        display_by_title = {row["title"]: row for row in parsed["rows"]}
        alpha_row = display_by_title["OpenRelix fixture topic alpha key"]
        beta_row = display_by_title["OpenRelix fixture topic beta key"]
        rule_row = display_by_title["OpenRelix-fixture-topic-rule sample retrieval token"]
        self.assertEqual(alpha_row["display_title"], "示例主题 A")
        self.assertIn("覆盖示例主题 A 的样例描述", alpha_row["display_value_note"])
        self.assertEqual(beta_row["display_title"], "示例主题 B")
        self.assertIn("覆盖示例主题 B 的样例描述", beta_row["display_value_note"])
        self.assertEqual(rule_row["display_title"], "示例主题规则")
        self.assertIn("通过规则匹配命中", rule_row["display_value_note"])

    def test_codex_native_structured_memory_uses_generic_chinese_fallback(self):
        sample_summary = """## What's in Memory

### Recent Memory Topics

- [durable/semantic/high] Example release validation rule - Keep public release notes minimal and verify package contents before publishing.
"""

        with TemporaryDirectory() as tmpdir:
            summary_path = Path(tmpdir) / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="zh")

        self.assertEqual(len(parsed["rows"]), 1)
        row = parsed["rows"][0]
        self.assertEqual(row["memory_type"], "semantic")
        self.assertEqual(row["priority"], "high")
        self.assertEqual(row["display_title"], "Codex 原生记忆条目")
        self.assertIn("英文原文已折叠", row["display_value_note"])
        self.assertIn("Example release validation rule", row["display_title_en"])
        self.assertIn("Keep public release notes minimal", row["display_value_note_en"])

    def test_codex_native_structured_chinese_memory_keeps_meaningful_title_and_body(self):
        sample_summary = """## What's in Memory

### Recent Memory Topics

- [durable/semantic/high] 正常 git push 和 npm publish 不会上传 OpenRelix state root
"""

        with TemporaryDirectory() as tmpdir:
            summary_path = Path(tmpdir) / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="zh")

        row = parsed["rows"][0]
        self.assertEqual(row["display_title"], "正常 git push 和 npm publish 不会上传 OpenRelix state root")
        self.assertEqual(
            row["display_value_note"],
            "主题：正常 git push 和 npm publish 不会上传 OpenRelix state root。",
        )
        self.assertNotIn("[durable/semantic/high]", row["display_title"])
        self.assertNotEqual(row["display_value_note"], "原生记忆摘要")

    def test_codex_native_memory_hides_local_personal_memory_registry_section(self):
        sample_summary = """## What's in Memory

### Local personal memory registry

- [durable/semantic/high] Injected OpenRelix personal memory - should not be native.

### Recent Memory Topics

- Native host memory topic
"""

        with TemporaryDirectory() as tmpdir:
            summary_path = Path(tmpdir) / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="zh")
            comparison = build_overview.build_codex_native_memory_comparison(
                parsed["rows"],
                [],
                parsed["counts"],
                {},
                language="zh",
            )

        self.assertEqual(len(parsed["rows"]), 1)
        self.assertEqual(parsed["rows"][0]["title"], "Native host memory topic")
        self.assertEqual(parsed["counts"]["hidden_personal_memory_items"], 1)
        self.assertIn("个人记忆登记册已隐藏", comparison["note"])
        self.assertNotIn("Injected OpenRelix personal memory", json.dumps(parsed, ensure_ascii=False))

    def test_codex_native_memory_summary_bullets_get_chinese_display_body(self):
        self._use_personal_codex_rules(
            bullet={
                "openrelix fixture bullet alpha key default sample exercise": "示例 bullet 文案 A：用于验证 BULLET 直接命中。",
                "openrelix fixture bullet beta key correct sample exercise": "示例 bullet 文案 B：用于验证 BULLET 直接命中。",
            },
        )

        sample_summary = """## User preferences

- OpenRelix fixture bullet alpha key default sample exercise.

## General Tips

- OpenRelix fixture bullet beta key correct sample exercise.
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="zh")

        self.assertEqual(
            parsed["preference_rows"][0]["display_body"],
            "示例 bullet 文案 A：用于验证 BULLET 直接命中。",
        )
        self.assertEqual(
            parsed["tip_rows"][0]["display_body"],
            "示例 bullet 文案 B：用于验证 BULLET 直接命中。",
        )
        self.assertIn("OpenRelix fixture bullet alpha", parsed["preference_rows"][0]["display_body_en"])

    def test_codex_native_memory_preferences_get_readable_chinese_explanations(self):
        self._use_personal_codex_rules(
            bullet_rules=[
                {
                    "fragments": ["openrelix-fixture-default", "sample-keep-moving"],
                    "title": "示例-默认推进",
                    "body": "命中第一条规则：示例文案验证默认推进偏好的提取。",
                },
                {
                    "fragments": ["openrelix-fixture-window", "sample-context-binding"],
                    "title": "示例-上下文绑定",
                    "body": "命中第二条规则：示例文案验证上下文前缀拼接。",
                },
            ],
            bullet_title_en={
                "示例-默认推进": "Sample Default Rule",
                "示例-上下文绑定": "Sample Context-Bound Rule",
            },
        )

        sample_summary = """## User preferences

- A bullet that exercises openrelix-fixture-default and sample-keep-moving for default rule matching.
- In `/path/to/example-project`, openrelix-fixture-window with sample-context-binding exercises context prefix wrapping.
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="zh")

        self.assertEqual(parsed["preference_rows"][0]["display_title"], "示例-默认推进")
        self.assertEqual(parsed["preference_rows"][0]["display_title_en"], "Sample Default Rule")
        self.assertIn("命中第一条规则", parsed["preference_rows"][0]["display_body"])
        self.assertEqual(parsed["preference_rows"][1]["display_title"], "示例-上下文绑定")
        self.assertEqual(parsed["preference_rows"][1]["display_title_en"], "Sample Context-Bound Rule")
        self.assertIn("在example-project项目里", parsed["preference_rows"][1]["display_body"])
        self.assertIn("命中第二条规则", parsed["preference_rows"][1]["display_body"])
        self.assertEqual(parsed["preference_rows"][0]["title"], "Preference 1")

    def test_codex_native_memory_preferences_without_rules_keep_source_meaning(self):
        sample_summary = """## User preferences

- When the target state is clear, default to direct edits and concrete outputs instead of long proposal mode.

## General Tips

- Use local browser checks for product pages when browser tooling is available.
"""

        with TemporaryDirectory() as tmpdir:
            summary_path = Path(tmpdir) / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="zh")

        preference = parsed["preference_rows"][0]
        tip = parsed["tip_rows"][0]
        self.assertIn("When the target state is clear", preference["display_title"])
        self.assertIn("direct edits and concrete outputs", preference["display_body"])
        self.assertNotIn("偏好：", preference["display_title"])
        self.assertNotIn("这条偏好来自", preference["display_body"])
        self.assertIn("Use local browser checks", tip["display_title"])
        self.assertIn("browser tooling is available", tip["display_body"])
        self.assertNotIn("通用 tips：", tip["display_title"])
        self.assertNotIn("这条通用提示来自", tip["display_body"])

    def test_codex_native_brief_cards_do_not_show_empty_when_translation_cache_misses(self):
        sample_summary = """## General Tips

- In the observed memory-system-v2 worktree, project/repo/domain memory was policy-gated in the registry/index.
"""

        with TemporaryDirectory() as tmpdir:
            summary_path = Path(tmpdir) / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="zh")
            html = build_overview.make_codex_native_brief_cards(
                parsed["tip_rows"],
                "tip",
                language="zh",
            )

        self.assertNotIn("暂无通用提示", html)
        self.assertIn("中文展示缓存暂未命中", html)
        self.assertIn("查看英文原文", html)
        self.assertIn("project/repo/domain memory", html)

    def test_codex_native_summary_contributes_general_context_rows(self):
        sample_summary = """## User Profile

The user prefers concrete runtime evidence before broad explanations.

## User preferences

- Prefer worktree-first delivery for OpenRelix changes.

## General Tips

- Verify rendered panel output after overview changes.

## What's in Memory

### OpenRelix

- Panel verification workflow
  - desc: Rendered panel output is stronger evidence than source-only review.
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            cache_path = tmp / "codex-native-display-cache.json"
            summary_path.write_text(sample_summary, encoding="utf-8")
            cache_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": {
                            build_overview.codex_native_display_cache_key(
                                "profile",
                                "The user prefers concrete runtime evidence before broad explanations.",
                                "The user prefers concrete runtime evidence before broad explanations.",
                            ): {
                                "title_zh": "用户重视运行证据",
                                "body_zh": "用户更希望先看到真实运行证据，再展开解释。",
                            },
                            build_overview.codex_native_display_cache_key(
                                "preference",
                                "Prefer worktree-first delivery for OpenRelix changes.",
                                "Prefer worktree-first delivery for OpenRelix changes.",
                            ): {
                                "title_zh": "OpenRelix先用worktree",
                                "body_zh": "OpenRelix 改动默认先在独立 worktree 里交付。",
                            },
                            build_overview.codex_native_display_cache_key(
                                "tip",
                                "Verify rendered panel output after overview changes.",
                                "Verify rendered panel output after overview changes.",
                            ): {
                                "title_zh": "面板改动后看渲染",
                                "body_zh": "overview 改动后要验证真实渲染出的 panel。",
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with mock.patch.object(build_overview, "CODEX_NATIVE_DISPLAY_CACHE_PATH", cache_path):
                build_overview.load_codex_native_display_cache.cache_clear()
                parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="zh")
                rows = build_overview.make_codex_native_global_context_rows(parsed, language="zh")

        titles = [row["display_title"] for row in rows]
        self.assertIn("用户习惯与工作方式", titles)
        self.assertIn("用户偏好摘要", titles)
        self.assertIn("通用 tips 与经验", titles)
        self.assertIn("历史经验索引", titles)
        notes = "\n".join(row["display_value_note"] for row in rows)
        self.assertIn("用户更希望先看到真实运行证据", notes)
        self.assertIn("独立 worktree", notes)
        self.assertIn("真实渲染出的 panel", notes)
        self.assertTrue(all(row["scope"] == "global" for row in rows))
        self.assertTrue(all(row["injection_policy"] == "global_context" for row in rows))

    def test_codex_native_memory_preferences_use_model_display_cache(self):
        preference_source = "When the target state is clear, default to direct edits and concrete outputs instead of long proposal mode."
        tip_source = "Use local browser checks for product pages when browser tooling is available."
        sample_summary = """## User preferences

- {preference_source}

## General Tips

- {tip_source}
""".format(preference_source=preference_source, tip_source=tip_source)

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            cache_path = tmp / "codex-native-display-cache.json"
            summary_path.write_text(sample_summary, encoding="utf-8")
            cache_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": {
                            build_overview.codex_native_display_cache_key(
                                "preference",
                                preference_source,
                                preference_source,
                            ): {
                                "title_zh": "目标明确时直接改",
                                "body_zh": "需求已经清楚时，直接给出改动和结果，少停留在方案讨论。",
                            },
                            build_overview.codex_native_display_cache_key(
                                "tip",
                                tip_source,
                                tip_source,
                            ): {
                                "title_zh": "产品页优先浏览器验证",
                                "body_zh": "产品页改动后，优先用本地浏览器检查真实展示效果。",
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with mock.patch.object(build_overview, "CODEX_NATIVE_DISPLAY_CACHE_PATH", cache_path):
                build_overview.load_codex_native_display_cache.cache_clear()
                parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="zh")

        preference = parsed["preference_rows"][0]
        tip = parsed["tip_rows"][0]
        self.assertEqual(preference["display_title"], "目标明确时直接改")
        self.assertEqual(preference["display_body"], "需求已经清楚时，直接给出改动和结果，少停留在方案讨论。")
        self.assertEqual(tip["display_title"], "产品页优先浏览器验证")
        self.assertEqual(tip["display_body"], "产品页改动后，优先用本地浏览器检查真实展示效果。")
        self.assertNotIn("偏好：", preference["display_title"])
        self.assertNotIn("通用 tips：", tip["display_title"])

    def test_global_context_panel_uses_personal_memory_not_codex_native_summary(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")

        self.assertIn(
            'global_context_display_rows = list(memory_policy_views.get("global_context", {}).get("rows", []))',
            source,
        )
        self.assertNotIn(
            "global_context_display_rows = merge_global_context_display_rows(\n        memory_policy_views.get(\"global_context\", {}).get(\"rows\", []),",
            source,
        )

    def test_codex_native_display_cache_prompt_uses_entries_contract(self):
        prompt = build_codex_native_display_cache.build_safe_display_prompt(
            build_codex_native_display_cache.build_prompt(
                [
                    {
                        "key": "preference:example",
                        "kind": "preference",
                        "source_label": "User preferences",
                        "source_title": "Prefer direct edits",
                        "source_body": "Prefer direct edits when the goal is clear.",
                    }
                ]
            )
        )

        self.assertIn("唯一合法输入就是下方 entries_json", prompt)
        self.assertIn("<entries_json>", prompt)
        self.assertNotIn("learning_context_json", prompt)
        self.assertNotIn("daily_compact_json", prompt)

    def test_codex_native_display_cache_marks_missing_model_keys_partial(self):
        entries = [
            {
                "key": "preference:one",
                "kind": "preference",
                "source_label": "User preferences",
                "source_title": "Prefer direct edits",
                "source_body": "Prefer direct edits.",
            },
            {
                "key": "tip:two",
                "kind": "tip",
                "source_label": "General Tips",
                "source_title": "Use browser checks",
                "source_body": "Use browser checks.",
            },
        ]

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            class FakePaths:
                runtime_dir = tmp / "runtime"
                nightly_runner_dir = tmp / "runner"
                codex_home = tmp / "codex-home"
                nightly_codex_home = tmp / "nightly-codex-home"
                codex_bin = Path("/bin/echo")

            def fake_run(*args, **kwargs):
                FakePaths.runtime_dir.mkdir(parents=True, exist_ok=True)
                (FakePaths.runtime_dir / "codex-native-display-cache.raw.json").write_text(
                    json.dumps(
                        {
                            "items": [
                                {
                                    "key": "preference:one",
                                    "title_zh": "直接改动",
                                    "body_zh": "目标明确时直接给出改动。",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(args[0], 0, "", "")

            with mock.patch.object(build_codex_native_display_cache, "PATHS", FakePaths), mock.patch.object(
                build_codex_native_display_cache,
                "sync_codex_exec_home",
                lambda *_args, **_kwargs: None,
            ), mock.patch.object(build_codex_native_display_cache.subprocess, "run", side_effect=fake_run):
                payload = build_codex_native_display_cache.run_codex_display_generation(
                    entries,
                    tmp / "cache.json",
                )

        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["missing_keys"], ["tip:two"])
        self.assertEqual(payload["items"]["preference:one"]["title_zh"], "直接改动")

    def test_codex_native_display_cache_reuses_existing_items(self):
        entries = [
            {
                "key": "preference:one",
                "kind": "preference",
                "source_label": "User preferences",
                "source_title": "Prefer direct edits",
                "source_body": "Prefer direct edits.",
            }
        ]
        existing_payload = {
            "version": 1,
            "items": {
                "preference:one": {
                    "title_zh": "直接改动",
                    "body_zh": "目标明确时直接给出改动。",
                }
            },
        }

        missing_entries = build_codex_native_display_cache.entries_missing_display(
            entries,
            existing_payload,
        )
        payload = build_codex_native_display_cache.merge_display_payload(
            entries,
            existing_payload,
            {},
            "/tmp/memory_summary.md",
        )

        self.assertEqual(missing_entries, [])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["missing_keys"], [])
        self.assertEqual(payload["items"]["preference:one"]["title_zh"], "直接改动")

    def test_codex_native_display_cache_collects_claude_auto_memory_entries(self):
        preference_source = "Prefer apply_patch for file edits."
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            claude_home = tmp / ".claude"
            project_memory_dir = claude_home / "projects" / "-Users-ray-openrelix" / "memory"
            project_memory_dir.mkdir(parents=True)
            (project_memory_dir / "MEMORY.md").write_text(
                "# User Memory\n\n- {}\n".format(preference_source),
                encoding="utf-8",
            )

            entries = build_codex_native_display_cache.collect_entries(
                tmp / "missing-memory_summary.md",
                tmp / "missing-MEMORY.md",
                20,
                claude_memory_path=claude_home / "CLAUDE.md",
                claude_home=claude_home,
            )

        expected_key = build_overview.codex_native_display_cache_key(
            "preference",
            preference_source,
            preference_source,
        )
        entry_by_key = {entry["key"]: entry for entry in entries}
        self.assertIn(expected_key, entry_by_key)
        self.assertEqual(
            entry_by_key[expected_key]["source_label"],
            "Claude Code native preferences",
        )

    def test_codex_native_memory_topic_cache_key_matches_compacted_title(self):
        long_title = "OpenRelix release validation package website checklist " * 4
        body = "Sample release validation note."
        sample_summary = """## What's in Memory

### Sample fixtures

- {long_title}
  - desc: {body}
""".format(long_title=long_title, body=body)

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            cache_path = tmp / "codex-native-display-cache.json"
            summary_path.write_text(sample_summary, encoding="utf-8")
            cache_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": {
                            build_overview.codex_native_display_cache_key(
                                "topic",
                                build_overview.compact_preview_text(
                                    build_overview.normalize_brand_display_text(long_title),
                                    limit=140,
                                ),
                                body,
                            ): {
                                "title_zh": "发布验证清单",
                                "body_zh": "沉淀发布、包内容和网站检查的验证经验。",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with mock.patch.object(build_overview, "CODEX_NATIVE_DISPLAY_CACHE_PATH", cache_path):
                build_overview.load_codex_native_display_cache.cache_clear()
                parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="zh")

        row = parsed["rows"][0]
        self.assertEqual(row["display_title"], "发布验证清单")
        self.assertEqual(row["display_value_note"], "沉淀发布、包内容和网站检查的验证经验。")

    def test_codex_native_memory_tips_get_readable_chinese_explanations(self):
        self._use_personal_codex_rules(
            bullet_rules=[
                {
                    "fragments": ["openrelix-fixture-contracts", "sample-readme-runbook"],
                    "title": "示例-先读契约",
                    "body": "命中第一条规则：开始前先读契约文件。",
                },
                {
                    "fragments": ["openrelix-fixture-state-files", "sample-orchestration-layer"],
                    "title": "示例-状态机",
                    "body": "命中第二条规则：长流程落到状态文件里，不能靠聊天上下文硬扛。",
                },
                {
                    "fragments": ["openrelix-fixture-text-layer", "sample-pdf-routing"],
                    "title": "示例-文本层优先",
                    "body": "命中第三条规则：处理 PDF 时先判断文件有没有可用文本层。",
                },
            ],
        )

        sample_summary = """## General Tips

- In `/path/to/example-project`, openrelix-fixture-contracts emphasises sample-readme-runbook discipline before each run.
- In that repo, openrelix-fixture-state-files form the sample-orchestration-layer; durable state files keep long runs alive.
- For sample documents, openrelix-fixture-text-layer with sample-pdf-routing decides whether to OCR.
"""

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            summary_path = tmp / "memory_summary.md"
            summary_path.write_text(sample_summary, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(summary_path, language="zh")

        self.assertEqual(parsed["tip_rows"][0]["display_title"], "示例-先读契约")
        self.assertIn("开始前先读契约文件", parsed["tip_rows"][0]["display_body"])
        self.assertEqual(parsed["tip_rows"][1]["display_title"], "示例-状态机")
        self.assertIn("在这个仓库里", parsed["tip_rows"][1]["display_body"])
        self.assertIn("长流程落到状态文件里", parsed["tip_rows"][1]["display_body"])
        self.assertEqual(parsed["tip_rows"][2]["display_title"], "示例-文本层优先")
        self.assertIn("先判断文件有没有可用文本层", parsed["tip_rows"][2]["display_body"])
        self.assertIn("openrelix-fixture-contracts", parsed["tip_rows"][0]["display_body_en"])

    def test_codex_native_brief_cards_are_compact_and_keep_source_text_collapsed(self):
        rows = [
            {
                "display_title": "示例-默认推进",
                "display_title_en": "Sample Default Rule",
                "title": "Preference 1",
                "display_body": "命中第一条规则：示例文案验证默认推进偏好的提取。",
                "display_body_en": "Sample bullet body for rule-based default fixture.",
                "source_files": [{"path": "/tmp/memory_summary.md", "label": "memory_summary.md"}],
            }
        ]

        html = build_overview.make_codex_native_brief_cards(rows, "preference", language="zh")

        self.assertIn("native-brief-card", html)
        self.assertIn("示例-默认推进", html)
        self.assertIn("Sample Default Rule", html)
        self.assertIn("查看英文原文", html)
        self.assertIn("Sample bullet body for rule-based default fixture", html)
        self.assertNotIn("关联上下文", html)
        self.assertNotIn("最近工作区", html)

    def test_codex_native_brief_cards_keep_english_keywords_out_of_chinese_body(self):
        rows = [
            {
                "display_title": "Example task group",
                "display_body": "Release checklist and package validation.",
                "display_body_en": "Release checklist and package validation.",
                "meta": "1 个任务；1 个来源",
                "keywords": ["Release checklist", "package manifest"],
                "task_count": 1,
                "rollout_reference_count": 1,
            }
        ]

        html = build_overview.make_codex_native_brief_cards(rows, "task_group", language="zh")

        self.assertIn("Example task group", html)
        self.assertNotIn("历史任务 1", html)
        self.assertIn("来自 MEMORY.md 的历史任务索引", html)
        self.assertNotIn("关键词：Release checklist", html)
        self.assertNotIn('data-lang-only="zh">Release checklist', html)
        self.assertIn('data-lang-only="en"><span class="native-brief-chip">Release checklist</span>', html)
        self.assertIn("keywords: Release checklist, package manifest", html)

    def test_codex_native_memory_english_mode_preserves_english_display_copy(self):
        sample_summary = """## What's in Memory

### OpenRelix + user-level Codex state

- Example dashboard, generic rules, and LaunchAgent runtime: OpenRelix, AGENTS.md, memories, dashboard
  - desc: Example local-first dashboard design under a public workspace.
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
            "Example dashboard, generic rules, and LaunchAgent runtime",
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
                    "context_labels": ["OpenRelix"],
                    "display_context": "OpenRelix",
                }
            ],
            [
                {
                    "context_labels": ["OpenRelix"],
                    "display_context": "OpenRelix",
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
        self.assertIn("记忆条目 1 条", comparison["note"])
        self.assertIn("共享上下文 OpenRelix", comparison["note"])

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
        self.assertIn("暂无记忆条目", empty_comparison["note"])
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

        self.assertEqual(parsed["rows"], [])
        self.assertTrue(parsed["counts"]["source_exists"])
        self.assertFalse(parsed["counts"]["source_readable"])
        self.assertIn("无法读取 custom-codex/memories/memory_summary.md", comparison["note"])

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

    def test_invalid_utf8_personal_memory_summary_usage_fails_closed(self):
        with TemporaryDirectory() as tmpdir:
            summary_path = Path(tmpdir) / "memory_summary.md"
            summary_path.write_bytes(b"\xff\xfe\xfa")

            usage = build_overview.build_personal_memory_token_usage(
                [
                    {
                        "bucket": "durable",
                        "memory_type": "semantic",
                        "priority": "high",
                        "display_title": "A",
                        "display_value_note": "compact note",
                    }
                ],
                "integrated",
                memory_summary_path=summary_path,
                memory_summary_budget=asset_runtime.memory_summary_budget_from_max(5000),
            )

        self.assertTrue(usage["enabled"])
        self.assertGreater(usage["estimated_context_item_count"], 0)
        self.assertIn("约", usage["mode_note_zh"])

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
        self.assertIn("记忆条目 1 条", comparison["note"])
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
        self.assertIn("历史任务索引统计暂不可用", comparison["note"])

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
        self.assertIn("历史任务索引 1 条", comparison["note"])

    def test_codex_memory_index_exposes_task_group_rows(self):
        self._use_personal_codex_rules(
            title={
                "example dashboard and launchagent runtime": "示例面板与 LaunchAgent 运行时",
            },
            task_body={
                "example dashboard and launchagent runtime": "示例面板与本地运行时。",
            },
        )

        sample_index = """# Task Group: Example dashboard and LaunchAgent runtime

scope: Example dashboard and local runtime.
applies_to: cwd=/tmp/OpenRelix

## Task 1: Build overview

### rollout_summary_files

- rollout_summaries/demo.md (thread_id=demo)

### keywords

- example, dashboard, memory

## User preferences

- voiceover_template.md should not be parsed as a keyword after the keyword section closes.
"""

        with TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "MEMORY.md"
            index_path.write_text(sample_index, encoding="utf-8")

            index_stats = build_overview.load_codex_memory_index_stats(index_path)

        self.assertEqual(index_stats["task_group_count"], 1)
        self.assertEqual(index_stats["rollout_reference_count"], 1)
        self.assertEqual(len(index_stats["task_groups"]), 1)
        row = index_stats["task_groups"][0]
        self.assertEqual(row["title"], "Example dashboard and LaunchAgent runtime")
        self.assertEqual(row["display_title"], "示例面板与 LaunchAgent 运行时")
        self.assertIn("Example dashboard", row["body"])
        self.assertIn("示例面板与本地运行时", row["display_body"])
        self.assertIn("Example dashboard", row["display_body_en"])
        self.assertEqual(row["task_count"], 1)
        self.assertEqual(row["rollout_reference_count"], 1)
        self.assertIn("dashboard", row["keywords"])
        self.assertNotIn("voiceover_template.md should not be parsed as a keyword after the keyword section closes.", row["keywords"])

    def test_codex_memory_index_english_task_group_keeps_source_title_without_cache(self):
        sample_index = """# Task Group: Example release surface and package validation

scope: Release checklist, package manifest, and public website validation.

## Task 1: Validate package

### rollout_summary_files

- rollout_summaries/demo.md (thread_id=demo)
"""

        with TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "MEMORY.md"
            index_path.write_text(sample_index, encoding="utf-8")

            index_stats = build_overview.load_codex_memory_index_stats(index_path, language="zh")

        row = index_stats["task_groups"][0]
        self.assertEqual(row["display_title"], "Example release surface and package validation")
        self.assertNotIn("历史任务 1", row["display_title"])
        self.assertIn("来自 MEMORY.md 的历史任务索引", row["display_body"])
        self.assertIn("包含 1 个任务", row["display_body"])
        self.assertIn("1 个来源", row["display_body"])
        self.assertIn("Release checklist", row["display_body_en"])

    def test_codex_memory_index_task_group_uses_model_display_cache(self):
        title = "Example release surface and package validation " * 4
        body = "Release checklist, package manifest, and public website validation. " * 5
        sample_index = """# Task Group: {title}

scope: {body}

## Task 1: Validate package

### rollout_summary_files

- rollout_summaries/demo.md (thread_id=demo)
""".format(title=title, body=body)

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            index_path = tmp / "MEMORY.md"
            cache_path = tmp / "codex-native-display-cache.json"
            index_path.write_text(sample_index, encoding="utf-8")
            cache_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": {
                            build_overview.codex_native_display_cache_key(
                                "task_group",
                                build_overview.compact_preview_text(
                                    build_overview.normalize_brand_display_text(title),
                                    limit=120,
                                ),
                                build_overview.compact_preview_text(
                                    build_overview.normalize_brand_display_text(body),
                                    limit=220,
                                ),
                            ): {
                                "title_zh": "发布检查与包验证",
                                "body_zh": "这个历史任务索引沉淀发布清单、包配置和公开页面验证经验。",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with mock.patch.object(build_overview, "CODEX_NATIVE_DISPLAY_CACHE_PATH", cache_path):
                build_overview.load_codex_native_display_cache.cache_clear()
                index_stats = build_overview.load_codex_memory_index_stats(index_path, language="zh")

        row = index_stats["task_groups"][0]
        self.assertEqual(row["display_title"], "发布检查与包验证")
        self.assertEqual(row["display_body"], "这个历史任务索引沉淀发布清单、包配置和公开页面验证经验。")
        self.assertNotIn("历史任务索引", row["display_title"])
        self.assertNotIn("来自 MEMORY.md 的历史任务索引", row["display_body"])

    def test_codex_memory_index_task_group_uses_external_label_rules(self):
        self._use_personal_codex_rules(
            task_group_label_rules=[
                (("release", "surface"), "发布面"),
                (("package", "validation"), "包检查"),
            ],
        )
        sample_index = """# Task Group: Example release surface and package validation

scope: Release checklist, package manifest, and public website validation.

## Task 1: Validate package

### rollout_summary_files

- rollout_summaries/demo.md (thread_id=demo)
"""

        with TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "MEMORY.md"
            index_path.write_text(sample_index, encoding="utf-8")

            index_stats = build_overview.load_codex_memory_index_stats(index_path, language="zh")

        row = index_stats["task_groups"][0]
        self.assertEqual(row["display_title"], "发布面 / 包检查历史任务")
        self.assertIn("主题：发布面、包检查", row["display_body"])
        self.assertNotIn("Release checklist", row["display_body"])

    def test_codex_memory_index_task_group_fallback_body_is_bilingual(self):
        sample_index = """# Task Group: Legacy group

## Task 1: Existing work
"""

        with TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "MEMORY.md"
            index_path.write_text(sample_index, encoding="utf-8")

            index_stats = build_overview.load_codex_memory_index_stats(index_path)

        row = index_stats["task_groups"][0]
        self.assertEqual(row["display_body"], "MEMORY.md 中登记的历史任务索引。")
        self.assertEqual(row["display_body_en"], "Historical task index entry registered in MEMORY.md.")

        rows = [dict(row, title="Legacy group {}".format(index)) for index in range(9)]
        cards_html = build_overview.make_memory_cards(
            build_overview.make_codex_native_brief_memory_items(rows, "task_group")
        )

        self.assertIn(
            '<p><span data-lang-only="zh">MEMORY.md 中登记的历史任务索引。</span><span data-lang-only="en">Historical task index entry registered in MEMORY.md.</span></p>',
            cards_html,
        )
        self.assertIn("查看更多 5 条", cards_html)
        self.assertIn("Show 5 more items", cards_html)
        self.assertNotIn("native-brief-heading", cards_html)

    def test_parse_claude_native_memory_summary_hides_openrelix_managed_block(self):
        sample = """# User Claude instructions

Keep my own note.

## What's in Memory

### Claude Code local notes

- Claude bridge mode reminder: cc, bridge, provider
  - desc: User-authored Claude memory should stay visible as Claude native memory.

## User preferences

- This user-owned Claude note must stay outside OpenRelix shared memory.

## General Tips

- Check the active provider before assuming Claude Code login state.

<!-- openrelix:shared-memory:start -->
# OpenRelix Shared Personal Memory

## User preferences

- Prefer worktree-first OpenRelix changes.

## General Tips

- Keep personal state outside the repo.

## What's in Memory

### Recent Memory Topics

- OpenRelix shared personal memory: claude, codex
  - desc: One local registry is injected into both host contexts.
  - learnings: Claude Code reads the managed block in CLAUDE.md.
<!-- openrelix:shared-memory:end -->
"""

        with TemporaryDirectory() as tmpdir:
            claude_path = Path(tmpdir) / "CLAUDE.md"
            claude_path.write_text(sample, encoding="utf-8")

            parsed = build_overview.parse_claude_native_memory_summary(
                claude_path,
                known_project_names=["OpenRelix"],
                language="zh",
            )
            comparison = build_overview.build_claude_native_memory_comparison(
                parsed["rows"],
                parsed["counts"],
                "CLAUDE.md",
                language="zh",
            )

        self.assertEqual(parsed["counts"]["topic_items"], 1)
        self.assertEqual(parsed["counts"]["user_preferences"], 1)
        self.assertEqual(parsed["counts"]["general_tips"], 1)
        self.assertEqual(parsed["counts"]["total_items"], 3)
        self.assertEqual(len(parsed["topic_rows"]), 1)
        self.assertEqual(len(parsed["preference_rows"]), 1)
        self.assertEqual(len(parsed["tip_rows"]), 1)
        self.assertTrue(parsed["counts"]["managed_block_present"])
        self.assertTrue(all(row["source_files"][0]["label"] == "CLAUDE.md" for row in parsed["rows"]))
        self.assertIn("Claude", parsed["rows"][0]["display_bucket"])
        self.assertIn("Claude Code 原生记忆", comparison["note"])
        self.assertNotIn("注入", comparison["note"])
        self.assertIn("Claude bridge mode reminder", parsed["topic_rows"][0]["title"])
        self.assertIn("Check the active provider", parsed["tip_rows"][0]["value_note"])
        self.assertNotIn("OpenRelix shared personal memory", json.dumps(parsed, ensure_ascii=False))

    def test_parse_codex_native_memory_summary_hides_openrelix_managed_block(self):
        sample = """## User Profile

Native Codex profile.

## User preferences

- Keep this native Codex preference.

<!-- openrelix:shared-memory:start -->
# OpenRelix Personal Memory

## User preferences

- OpenRelix injected preference should stay out of native cards.

## What's in Memory

### Local personal memory registry

- OpenRelix injected memory should stay out of native cards.
<!-- openrelix:shared-memory:end -->
"""

        with TemporaryDirectory() as tmpdir:
            summary_path = Path(tmpdir) / "memory_summary.md"
            summary_path.write_text(sample, encoding="utf-8")

            parsed = build_overview.parse_codex_native_memory_summary(
                summary_path,
                language="zh",
            )

        self.assertEqual(parsed["counts"]["user_preferences"], 1)
        self.assertEqual(len(parsed["preference_rows"]), 1)
        self.assertTrue(parsed["counts"]["managed_block_present"])
        self.assertIn("Keep this native Codex preference", parsed["preference_rows"][0]["body"])
        self.assertNotIn("OpenRelix injected", json.dumps(parsed, ensure_ascii=False))

    def test_parse_claude_native_memory_summary_only_managed_block_has_no_native_rows(self):
        sample = """<!-- openrelix:shared-memory:start -->
# OpenRelix Shared Personal Memory

## What's in Memory

### Recent Memory Topics

- OpenRelix injected memory should stay out of native cards.
<!-- openrelix:shared-memory:end -->
"""

        with TemporaryDirectory() as tmpdir:
            claude_path = Path(tmpdir) / "CLAUDE.md"
            claude_path.write_text(sample, encoding="utf-8")

            parsed = build_overview.parse_claude_native_memory_summary(
                claude_path,
                language="zh",
            )
            comparison = build_overview.build_claude_native_memory_comparison(
                parsed["rows"],
                parsed["counts"],
                "CLAUDE.md",
                language="zh",
            )

        self.assertEqual(parsed["rows"], [])
        self.assertTrue(parsed["counts"]["managed_block_present"])
        self.assertIn("暂未发现可展示的 Claude Code 原生记忆条目", comparison["note"])
        self.assertNotIn("注入", comparison["note"])

    def test_parse_claude_native_memory_summary_reads_project_auto_memory(self):
        sample = """<!-- openrelix:shared-memory:start -->
# OpenRelix Shared Personal Memory

- OpenRelix injected memory should stay out of native cards.
<!-- openrelix:shared-memory:end -->
"""

        with TemporaryDirectory() as tmpdir:
            claude_home = Path(tmpdir) / ".claude"
            project_memory_dir = claude_home / "projects" / "-Users-ray-openrelix" / "memory"
            project_memory_dir.mkdir(parents=True)
            claude_path = claude_home / "CLAUDE.md"
            claude_path.write_text(sample, encoding="utf-8")
            (project_memory_dir / "MEMORY.md").write_text(
                """# User Memory

- Prefer apply_patch for file edits.
- Debug Claude Code auto memory by checking the project memory directory.
""",
                encoding="utf-8",
            )
            (project_memory_dir / "reference_build.md").write_text(
                """# Build reference

- Build overview after changing panel fields.
""",
                encoding="utf-8",
            )

            parsed = build_overview.parse_claude_native_memory_summary(
                claude_path,
                known_project_names=["OpenRelix"],
                language="zh",
                claude_home=claude_home,
            )
            comparison = build_overview.build_claude_native_memory_comparison(
                parsed["rows"],
                parsed["counts"],
                "~/.claude/CLAUDE.md + ~/.claude/projects/*/memory/*.md",
                language="zh",
            )

        self.assertEqual(parsed["counts"]["claude_md_items"], 0)
        self.assertEqual(parsed["counts"]["auto_memory_items"], 3)
        self.assertEqual(parsed["counts"]["auto_memory_file_count"], 2)
        self.assertEqual(parsed["counts"]["auto_memory_project_count"], 1)
        self.assertEqual(parsed["counts"]["user_preferences"], 2)
        self.assertEqual(parsed["counts"]["topic_items"], 1)
        self.assertTrue(parsed["counts"]["managed_block_present"])
        self.assertIn("auto memory", parsed["rows"][0]["source_files"][0]["label"])
        self.assertIn("~/openrelix", parsed["rows"][0]["source_files"][0]["label"])
        self.assertIn("auto memory 3 条", comparison["note"])
        self.assertIn("1 个项目 / 路径", comparison["note"])
        self.assertNotIn("OpenRelix injected memory", json.dumps(parsed, ensure_ascii=False))

    def test_claude_auto_memory_uses_model_display_cache_in_chinese(self):
        preference_source = "Prefer apply_patch for file edits."
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            claude_home = tmp / ".claude"
            project_memory_dir = claude_home / "projects" / "-Users-ray-openrelix" / "memory"
            project_memory_dir.mkdir(parents=True)
            claude_path = claude_home / "CLAUDE.md"
            (project_memory_dir / "MEMORY.md").write_text(
                "# User Memory\n\n- {}\n".format(preference_source),
                encoding="utf-8",
            )
            cache_path = tmp / "codex-native-display-cache.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": {
                            build_overview.codex_native_display_cache_key(
                                "preference",
                                preference_source,
                                preference_source,
                            ): {
                                "title_zh": "文件编辑优先补丁",
                                "body_zh": "编辑文件时优先使用 apply_patch，只有不适合打补丁时才回退到其他写入方式。",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with mock.patch.object(build_overview, "CODEX_NATIVE_DISPLAY_CACHE_PATH", cache_path):
                build_overview.load_codex_native_display_cache.cache_clear()
                parsed = build_overview.parse_claude_native_memory_summary(
                    claude_path,
                    language="zh",
                    claude_home=claude_home,
                )

        preference = parsed["preference_rows"][0]
        self.assertEqual(preference["display_title"], "文件编辑优先补丁")
        self.assertEqual(
            preference["display_value_note"],
            "编辑文件时优先使用 apply_patch，只有不适合打补丁时才回退到其他写入方式。",
        )
        self.assertEqual(preference["display_value_note_en"], preference_source)

    def test_sync_host_memory_summary_preserves_user_claude_file_content(self):
        block = sync_host_memory_summary.managed_claude_block("## What's in Memory\n\n- Shared item\n")
        existing = "# User notes\n\nKeep this line.\n"

        first = sync_host_memory_summary.replace_managed_block(existing, block)
        second = sync_host_memory_summary.replace_managed_block(first, sync_host_memory_summary.managed_claude_block("## Updated\n"))

        self.assertIn("Keep this line.", second)
        self.assertIn("## Updated", second)
        self.assertNotIn("- Shared item", second)
        self.assertEqual(second.count(sync_host_memory_summary.MANAGED_START), 1)

    def test_build_host_context_summary_tolerates_empty_initial_state(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = replace(
                sync_host_memory_summary.PATHS,
                runtime_dir=root / "runtime",
            )

            with (
                mock.patch.object(sync_host_memory_summary, "PATHS", paths),
                mock.patch.object(sync_host_memory_summary, "run_summary_builder", return_value=None),
            ):
                summary_path, summary_text = sync_host_memory_summary.build_host_context_summary()

            self.assertEqual(summary_path, paths.runtime_dir / "host-context" / "memory_summary.md")
            self.assertEqual(summary_text, "")

    def test_sync_host_memory_summary_clears_managed_blocks_when_summary_is_empty(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = replace(
                sync_host_memory_summary.PATHS,
                codex_home=root / "codex-home",
                claude_home=root / "claude-home",
                runtime_dir=root / "runtime",
            )
            codex_summary = paths.codex_home / "memories" / "memory_summary.md"
            claude_summary = paths.claude_home / "CLAUDE.md"
            codex_summary.parent.mkdir(parents=True)
            claude_summary.parent.mkdir(parents=True)
            codex_summary.write_text(
                "## User Profile\n\n"
                "Native Codex memory.\n\n"
                + sync_host_memory_summary.managed_codex_block("## What's in Memory\n\n- stale shared item\n"),
                encoding="utf-8",
            )
            claude_summary.write_text(
                "# User notes\n\n"
                + sync_host_memory_summary.managed_claude_block("## What's in Memory\n\n- stale shared item\n"),
                encoding="utf-8",
            )

            with (
                mock.patch.object(sync_host_memory_summary, "PATHS", paths),
                mock.patch.object(sync_host_memory_summary, "get_memory_mode", return_value="integrated"),
                mock.patch.object(sync_host_memory_summary, "get_host_context_targets", return_value=["codex", "claude"]),
                mock.patch.object(
                    sync_host_memory_summary,
                    "build_host_context_summary",
                    return_value=(paths.runtime_dir / "host-context" / "memory_summary.md", ""),
                ),
                mock.patch.object(sys, "argv", ["sync_host_memory_summary.py"]),
            ):
                sync_host_memory_summary.main()

            codex_text = codex_summary.read_text(encoding="utf-8")
            claude_text = claude_summary.read_text(encoding="utf-8")
            self.assertIn("Native Codex memory.", codex_text)
            self.assertIn("# User notes", claude_text)
            self.assertNotIn(sync_host_memory_summary.MANAGED_START, codex_text)
            self.assertNotIn(sync_host_memory_summary.MANAGED_START, claude_text)

    def test_sync_host_memory_summary_preserves_codex_native_memory_content(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = replace(
                sync_host_memory_summary.PATHS,
                codex_home=root / "codex-home",
            )
            codex_summary = paths.codex_home / "memories" / "memory_summary.md"
            codex_summary.parent.mkdir(parents=True)
            codex_summary.write_text("## User Profile\n\nNative Codex memory.\n", encoding="utf-8")
            shared_summary = (
                "## User Profile\n\n"
                "The injected context is compiled from OpenRelix canonical memory.\n\n"
                "## What's in Memory\n\n"
                "### Local personal memory registry\n\n"
                "- Shared item\n"
            )

            with mock.patch.object(sync_host_memory_summary, "PATHS", paths):
                first_result = sync_host_memory_summary.sync_codex_summary(shared_summary)
                second_result = sync_host_memory_summary.sync_codex_summary("## Updated\n\n- New shared item\n")
                updated = codex_summary.read_text(encoding="utf-8")

            self.assertEqual(first_result["status"], "synced")
            self.assertEqual(second_result["status"], "synced")
            self.assertNotIn("migrated legacy", second_result.get("detail", ""))
            self.assertIn("Native Codex memory.", updated)
            self.assertIn("## Updated", updated)
            self.assertIn("- New shared item", updated)
            self.assertNotIn("- Shared item", updated)
            self.assertEqual(updated.count(sync_host_memory_summary.MANAGED_START), 1)

    def test_sync_host_memory_summary_migrates_legacy_codex_full_file_summary(self):
        legacy_openrelix_summary = (
            "## User Profile\n\n"
            "The injected context is compiled from OpenRelix canonical memory.\n\n"
            "## What's in Memory\n\n"
            "### Local personal memory registry\n\n"
            "- stale shared item\n"
        )

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = replace(
                sync_host_memory_summary.PATHS,
                codex_home=root / "codex-home",
            )
            codex_summary = paths.codex_home / "memories" / "memory_summary.md"
            codex_summary.parent.mkdir(parents=True)
            codex_summary.write_text(legacy_openrelix_summary, encoding="utf-8")

            with mock.patch.object(sync_host_memory_summary, "PATHS", paths):
                result = sync_host_memory_summary.sync_codex_summary("## Updated\n\n- New shared item\n")
                updated = codex_summary.read_text(encoding="utf-8")

            self.assertEqual(result["status"], "synced")
            self.assertIn("migrated legacy", result["detail"])
            self.assertIn(sync_host_memory_summary.MANAGED_START, updated)
            self.assertIn("## Updated", updated)
            self.assertNotIn("- stale shared item", updated)
            self.assertNotIn("The injected context is compiled from OpenRelix canonical memory.", updated)

    def test_sync_host_memory_summary_clear_helpers_remove_managed_surfaces(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = replace(
                sync_host_memory_summary.PATHS,
                codex_home=root / "codex-home",
                claude_home=root / "claude-home",
            )
            codex_summary = paths.codex_home / "memories" / "memory_summary.md"
            claude_summary = paths.claude_home / "CLAUDE.md"
            codex_summary.parent.mkdir(parents=True)
            claude_summary.parent.mkdir(parents=True)
            codex_summary.write_text(
                "## User Profile\n\n"
                "Native Codex memory.\n\n"
                + sync_host_memory_summary.managed_codex_block("## What's in Memory\n\n- stale shared item\n"),
                encoding="utf-8",
            )
            claude_summary.write_text(
                "# User notes\n\n"
                + sync_host_memory_summary.managed_claude_block("## What's in Memory\n\n- stale shared item\n"),
                encoding="utf-8",
            )

            with mock.patch.object(sync_host_memory_summary, "PATHS", paths):
                codex_result = sync_host_memory_summary.clear_codex_summary()
                claude_result = sync_host_memory_summary.clear_claude_summary()
                claude_text = claude_summary.read_text(encoding="utf-8")

            self.assertEqual(codex_result["status"], "removed")
            self.assertEqual(claude_result["status"], "removed")
            self.assertTrue(codex_summary.exists())
            self.assertIn("Native Codex memory.", codex_summary.read_text(encoding="utf-8"))
            self.assertNotIn(sync_host_memory_summary.MANAGED_START, codex_summary.read_text(encoding="utf-8"))
            self.assertTrue(claude_summary.exists())
            self.assertIn("# User notes", claude_text)
            self.assertNotIn(sync_host_memory_summary.MANAGED_START, claude_text)

    def test_sync_host_memory_summary_clear_codex_keeps_unmanaged_native_file(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = replace(
                sync_host_memory_summary.PATHS,
                codex_home=root / "codex-home",
            )
            codex_summary = paths.codex_home / "memories" / "memory_summary.md"
            codex_summary.parent.mkdir(parents=True)
            codex_summary.write_text("## User Profile\n\nNative Codex memory.\n", encoding="utf-8")

            with mock.patch.object(sync_host_memory_summary, "PATHS", paths):
                result = sync_host_memory_summary.clear_codex_summary()
                updated = codex_summary.read_text(encoding="utf-8")

            self.assertEqual(result["status"], "kept")
            self.assertIn("no managed block", result["detail"])
            self.assertIn("Native Codex memory.", updated)

    def test_sync_host_memory_summary_skips_codex_when_memories_are_disabled(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = replace(
                sync_host_memory_summary.PATHS,
                codex_home=root / "codex-home",
            )
            config_path = paths.codex_home / "config.toml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text("[features]\nmemories = false\n", encoding="utf-8")

            with mock.patch.object(sync_host_memory_summary, "PATHS", paths):
                result = sync_host_memory_summary.sync_codex_summary("## What's in Memory\n\n- Should not sync\n")

            self.assertEqual(result["status"], "disabled")
            self.assertEqual(result["memory_feature"], "disabled")
            self.assertFalse((paths.codex_home / "memories" / "memory_summary.md").exists())

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
        self.assertIn("历史任务索引统计暂不可用", comparison["note"])

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
                "display_context": "OpenRelix",
                "value_note": "Demo | value note.",
            }
        ]
        for index in range(13):
            native_rows.append(
                {
                    "title": "Extra native memory {}".format(index),
                    "updated_at_display": "2026-04-26",
                    "context_labels": ["OpenRelix"],
                    "display_context": "OpenRelix",
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
                    "note": "记忆条目 1 条；原生偏长期规则，nightly 偏近期整理。",
                    "note_zh": "记忆条目 1 条；原生偏长期规则，nightly 偏近期整理。",
                    "note_en": "1 memory item; native memory leans toward long-term rules.",
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
                "codex_native_memory_comparison": {"note": "记忆条目 1 条。"},
                "codex_memory_summary_path_label": "custom-codex/memories/memory_summary.md",
                "codex_memory_index_path_label": "custom-codex/memories/MEMORY.md",
                "codex_native_memory": [
                    {
                        "title": "Example dashboard and LaunchAgent runtime",
                        "display_title": "示例面板与 LaunchAgent 运行时",
                        "updated_at_display": "2026-04-26",
                        "context_labels": ["Example"],
                        "display_context": "Example",
                        "value_note": "English note.",
                        "display_value_note": "中文摘要。",
                    }
                ],
                "assets": {"recent": [], "top": []},
                "reading_guide": [],
            }
        )

        self.assertIn("示例面板与 LaunchAgent 运行时", markdown)
        self.assertIn("中文摘要", markdown)
        self.assertNotIn("English note", markdown)

    def test_summary_term_views_default_to_today_and_last_seven_days(self):
        assets = [
            {
                "title": "今日资产 OpenRelix",
                "updated_at": "2026-04-28T10:00:00+08:00",
                "created_at": "2026-04-28T10:00:00+08:00",
            },
            {
                "title": "旧资产 LegacyProject",
                "updated_at": "2026-04-26T10:00:00+08:00",
                "created_at": "2026-04-26T10:00:00+08:00",
            },
        ]
        reviews = [
            {
                "date": "2026-04-27",
                "task": "近三日复盘 subreview",
                "domain": "",
                "repo": "",
                "text": "",
            }
        ]
        usage_events = [
            {
                "date": "2026-04-22",
                "task": "七日使用 ASR",
                "note": "",
                "asset_id": "asr-playbook",
            }
        ]
        nightly_candidates = [
            {
                "date": "2026-04-28",
                "stage": "final",
                "keywords": ["OpenRelix", "今日特性"],
                "window_summaries": [],
            },
            {
                "date": "2026-04-26",
                "stage": "final",
                "keywords": ["LegacyProject"],
                "window_summaries": [],
            },
        ]

        with mock.patch.object(
            build_overview,
            "build_context_window_overview_for_days",
        ) as mock_window_overview:
            mock_window_overview.side_effect = lambda anchor, days, **_: {
                "source_dates": build_overview.date_strings_ending_at(anchor, days),
                "window_count": 0,
                "windows": [],
            }
            views = build_overview.build_summary_term_views(
                assets,
                reviews,
                usage_events,
                nightly_candidates,
                "2026-04-28",
                latest_nightly=nightly_candidates[0],
            )

        self.assertEqual([view["days"] for view in views], [1, 7])
        self.assertEqual(build_overview.default_summary_term_view(views)["days"], 1)

        today_terms = {row["label"] for row in views[0]["terms"]}
        seven_day_terms = {row["label"] for row in views[1]["terms"]}

        self.assertIn("OpenRelix", today_terms)
        self.assertNotIn("Legacyproject", today_terms)
        self.assertIn("Legacyproject", seven_day_terms)
        self.assertIn("subreview", seven_day_terms)
        self.assertIn("ASR", seven_day_terms)

    def test_summary_term_card_uses_rank_list_instead_of_bubble_map(self):
        html = build_overview.make_summary_term_card(
            {
                "days": 1,
                "title_zh": "今日热词",
                "title_en": "Today Hot Terms",
                "terms": [
                    {"label": "OpenRelix", "value": 123},
                    {"label": "AI", "value": 19},
                    {"label": "Codex", "value": 18},
                ],
                "source_dates": ["2026-04-29"],
                "window_count": 6,
                "nightly_count": 1,
                "asset_count": 0,
                "review_count": 0,
                "usage_event_count": 0,
            }
        )

        self.assertIn('class="term-rank-list"', html)
        self.assertIn('class="term-rank-item is-primary"', html)
        self.assertIn("--term-level:1.000", html)
        self.assertIn("01", html)
        self.assertIn("OpenRelix", html)
        self.assertIn("term-rank-track", html)
        self.assertNotIn("term-bubble-map", html)

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
        self.assertIn("## 月度活动", markdown)
        self.assertIn("## 近 30 天高频 skills Top 10", markdown)
        self.assertIn("| 暂无 | 暂无 | 0 | 0 |", markdown)
        self.assertNotIn("## 最近更新的资产", markdown)
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
                build_overview.resolve_ccusage_daily = lambda *args, **kwargs: {
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
        metric_keys = [metric.get("key") for metric in data["metrics"]]
        metric_labels = [metric.get("label") for metric in data["metrics"]]
        self.assertNotIn("tracked_usage_events", metric_keys)
        self.assertNotIn("tracked_minutes_saved", metric_keys)
        self.assertIn("登记册资产", metric_labels)
        self.assertIn("登记册活跃资产", metric_labels)
        self.assertIn("登记册仓库资产", metric_labels)
        self.assertIn("MEMORY.md 未检测到", data["codex_native_memory_comparison"]["note"])
        html = build_overview.build_html(data)
        self.assertNotIn('<div class="metric-label" data-role="label">复用记录</div>', html)
        self.assertNotIn('<div class="metric-label" data-role="label">估算节省</div>', html)
        self.assertIn("单纯新增 SKILL.md 不会进入这里", html)
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
                build_overview.resolve_ccusage_daily = lambda *args, **kwargs: {
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

    def test_memory_registry_sorts_durable_items_by_7_day_usage_frequency(self):
        usage_window_overview = {
            "date": "2026-04-28",
            "days": 7,
            "windows": [
                {
                    "date": "2026-04-28",
                    "window_id": "w-runtime",
                    "project_label": "OpenRelix",
                    "cwd_display": "OpenRelix",
                    "question_summary": "安装语言需要写入 runtime config",
                    "main_takeaway": "panel 默认语言要和 runtime config 端到端一致",
                    "keywords": ["runtime config", "panel", "语言"],
                },
                {
                    "date": "2026-04-27",
                    "window_id": "w-panel",
                    "project_label": "OpenRelix",
                    "cwd_display": "OpenRelix",
                    "question_summary": "面板默认语言和安装语言不一致",
                    "main_takeaway": "重新按 runtime config 刷新 overview 和 panel",
                    "keywords": ["overview", "panel"],
                },
                {
                    "date": "2026-04-26",
                    "window_id": "w-unrelated",
                    "project_label": "LegacyProject",
                    "cwd_display": "LegacyProject",
                    "question_summary": "Search module ASR log_id 排查",
                    "main_takeaway": "只保留必要 AB 读取",
                    "keywords": ["ASR", "log_id"],
                },
            ],
        }
        memory_items = [
            {
                "date": "2026-04-28",
                "source": "nightly_codex",
                "bucket": "durable",
                "title": "商标材料归档前先确认发布边界",
                "memory_type": "semantic",
                "priority": "medium",
                "value_note": "商标文档与发布材料要分开归档。",
                "source_window_ids": [],
                "keywords": ["商标", "发布"],
            },
            {
                "date": "2026-04-28",
                "source": "nightly_codex",
                "bucket": "durable",
                "title": "安装语言应写入 runtime config 并校验 panel 一致",
                "memory_type": "procedural",
                "priority": "high",
                "value_note": "安装语言、runtime config、overview 与 panel 默认语言要保持端到端一致。",
                "source_window_ids": ["w-runtime"],
                "keywords": ["runtime config", "panel", "语言"],
            },
        ]

        registry = build_overview.build_memory_registry(
            memory_items,
            usage_window_overview,
            usage_window_overview=usage_window_overview,
        )
        durable_rows = [row for row in registry["rows"] if row["bucket"] == "durable"]

        self.assertEqual(
            durable_rows[0]["title"],
            "安装语言应写入 runtime config 并校验 panel 一致",
        )
        self.assertGreater(durable_rows[0]["usage_frequency"], durable_rows[1]["usage_frequency"])
        self.assertGreaterEqual(durable_rows[0]["usage_frequency_direct_window_count"], 1)
        self.assertEqual(durable_rows[0]["usage_frequency_estimated_window_count"], 0)
        self.assertEqual(durable_rows[0]["usage_frequency_score_kind"], "traceable_evidence")
        self.assertEqual(durable_rows[0]["usage_frequency_window_days"], 7)

    def test_memory_registry_infers_project_scope_for_quality_gated_source_windows(self):
        window_overview = {
            "date": "2026-04-28",
            "windows": [
                {
                    "date": "2026-04-28",
                    "window_id": "w-runtime",
                    "project_label": "OpenRelix",
                    "cwd_display": "OpenRelix",
                    "cwd": str(ROOT),
                    "question_summary": "memory UI refactor",
                    "main_takeaway": "project memories stay scoped",
                    "keywords": ["memory"],
                }
            ],
        }
        registry = build_overview.build_memory_registry(
            [
                {
                    "date": "2026-04-28",
                    "source": "nightly_codex",
                    "bucket": "durable",
                    "title": "旧格式项目记忆",
                    "memory_type": "procedural",
                    "priority": "high",
                    "value_note": "旧格式只有 source_window_ids。",
                    "source_window_ids": ["w-runtime"],
                    "storage_quality_score": 6,
                    "storage_quality_reason": "type,priority,strong_signal",
                }
            ],
            window_overview,
            usage_window_overview=window_overview,
        )

        row = registry["rows"][0]
        self.assertEqual(row["scope"], "project")
        self.assertEqual(row["injection_policy"], "project_context")
        self.assertEqual(row["project_label"], "OpenRelix")
        self.assertFalse(build_overview.overview_memory_context.memory_record_is_global_context(row))
        self.assertTrue(build_overview.overview_memory_context.memory_record_is_host_context_candidate(row))

    def test_memory_scope_metadata_promotes_generic_generated_rules_to_global(self):
        summary = {
            "language": "zh",
            "window_summaries": [
                {
                    "window_id": "w1",
                    "cwd": str(ROOT),
                }
            ],
        }
        item = {
            "title": "编辑文件默认优先用 apply_patch",
            "memory_type": "procedural",
            "priority": "high",
            "value_note": "文件修改的稳定偏好是优先用 apply_patch。",
            "source_window_ids": ["w1"],
            "keywords": ["apply_patch"],
        }

        self.assertEqual(
            nightly_consolidate.memory_scope_metadata(summary, item, "durable"),
            {
                "scope": "global",
                "injection_policy": "global_context",
                "project_key": "",
                "project_label": "",
            },
        )

    def test_memory_scope_metadata_keeps_project_specific_rules_project_scoped(self):
        summary = {
            "language": "zh",
            "window_summaries": [
                {
                    "window_id": "w1",
                    "cwd": str(ROOT),
                }
            ],
        }
        item = {
            "title": "OpenRelix 的 worktree 与合并默认规则",
            "memory_type": "procedural",
            "priority": "high",
            "value_note": "做 bugfix/feature 时先起独立 worktree，只推 origin/main。",
            "source_window_ids": ["w1"],
            "keywords": ["worktree"],
        }

        metadata = nightly_consolidate.memory_scope_metadata(summary, item, "durable")

        self.assertEqual(metadata["scope"], "project")
        self.assertEqual(metadata["injection_policy"], "project_context")
        self.assertTrue(metadata["project_label"])

    def test_memory_usage_frequency_ignores_occurrences_outside_7_day_window(self):
        usage_window_overview = {"date": "2026-04-28", "days": 7, "windows": []}

        stale = build_overview.build_memory_usage_frequency(
            {"title": "stale memory", "value_note": "old repeated item"},
            usage_window_overview,
            recent_occurrence_dates=["2026-04-01", "2026-04-02", "2026-04-03"],
        )
        recent = build_overview.build_memory_usage_frequency(
            {"title": "recent memory", "value_note": "recent repeated item"},
            usage_window_overview,
            recent_occurrence_dates=["2026-04-28", "2026-04-27", "2026-04-20"],
        )

        self.assertEqual(stale["usage_frequency"], 0)
        self.assertEqual(recent["usage_frequency"], 0.9)

    def test_upsert_memory_items_filters_noise_and_demotes_weak_rows(self):
        old_registry_dir = nightly_consolidate.REGISTRY_DIR
        try:
            with TemporaryDirectory() as tmpdir:
                nightly_consolidate.REGISTRY_DIR = Path(tmpdir) / "registry"
                summary = {
                    "language": "zh",
                    "window_summaries": [
                        {
                            "window_id": "w1",
                            "cwd": "/tmp/openrelix",
                        }
                    ],
                    "durable_memories": [
                        {
                            "title": "OpenRelix bugfix 默认独立 worktree",
                            "memory_type": "procedural",
                            "priority": "high",
                            "value_note": "处理 OpenRelix bugfix 时，必须先切独立 worktree 并跑校验。",
                            "source_window_ids": ["w1"],
                            "keywords": ["worktree"],
                        },
                        {
                            "title": "面板布局记录项需要整理",
                            "memory_type": "task",
                            "priority": "medium",
                            "value_note": "面板布局还有一些零散细节需要后续人工整理归档。",
                            "source_window_ids": ["w1"],
                            "keywords": ["panel"],
                        },
                        {
                            "title": "多个 Claude Code 窗口只是未登录、问候或退出",
                            "memory_type": "task",
                            "priority": "low",
                            "value_note": "这些窗口没有可复用结论。",
                            "source_window_ids": ["w1"],
                            "keywords": ["claude"],
                        },
                    ],
                    "session_memories": [],
                    "low_priority_memories": [],
                }

                nightly_consolidate.upsert_memory_items("2026-05-06", summary)
                rows = [
                    json.loads(line)
                    for line in (nightly_consolidate.REGISTRY_DIR / "memory_entries.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ]

            rows_by_title = {row["title"]: row for row in rows}
            self.assertEqual(rows_by_title["OpenRelix bugfix 默认独立 worktree"]["bucket"], "durable")
            self.assertEqual(rows_by_title["面板布局记录项需要整理"]["bucket"], "low_priority")
            self.assertEqual(rows_by_title["面板布局记录项需要整理"]["injection_policy"], "local_only")
            self.assertNotIn("多个 Claude Code 窗口只是未登录、问候或退出", rows_by_title)
            self.assertIn("storage_quality_score", rows_by_title["OpenRelix bugfix 默认独立 worktree"])
        finally:
            nightly_consolidate.REGISTRY_DIR = old_registry_dir

    def test_memory_registry_sorts_session_items_by_recent_usage_not_lifetime_occurrences(self):
        usage_window_overview = {
            "date": "2026-04-28",
            "days": 7,
            "windows": [
                {
                    "date": "2026-04-28",
                    "window_id": "w-session",
                    "project_label": "OpenRelix",
                    "cwd_display": "OpenRelix",
                    "question_summary": "refresh learn-memory should forward learn window days",
                    "main_takeaway": "refresh --learn-memory calls the nightly pipeline explicitly",
                    "keywords": ["learn-memory", "refresh"],
                }
            ],
        }
        memory_items = [
            {
                "date": "2026-04-{:02d}".format(day),
                "source": "nightly_codex",
                "bucket": "session",
                "title": "旧任务反复出现但近期未使用",
                "memory_type": "task",
                "priority": "medium",
                "value_note": "旧任务重复很多次。",
                "source_window_ids": [],
                "keywords": ["旧任务"],
            }
            for day in range(1, 11)
        ]
        memory_items.append(
            {
                "date": "2026-04-28",
                "source": "nightly_codex",
                "bucket": "session",
                "title": "refresh learn-memory 参数转发",
                "memory_type": "task",
                "priority": "high",
                "value_note": "refresh --learn-memory 应显式调用 nightly pipeline 并传递窗口天数。",
                "source_window_ids": ["w-session"],
                "keywords": ["learn-memory", "refresh"],
            }
        )

        registry = build_overview.build_memory_registry(
            memory_items,
            usage_window_overview,
            usage_window_overview=usage_window_overview,
        )
        session_rows = [row for row in registry["rows"] if row["bucket"] == "session"]

        self.assertEqual(session_rows[0]["title"], "refresh learn-memory 参数转发")
        self.assertGreater(session_rows[0]["usage_frequency"], session_rows[1]["usage_frequency"])
        self.assertEqual(session_rows[1]["usage_frequency"], 0)

    def test_markdown_table_cell_is_table_safe(self):
        cell = build_overview.markdown_table_cell("a|b\n[c](https://example.invalid) <tag> `code`")

        self.assertIn("a / b", cell)
        self.assertIn("c &lt;tag&gt; code", cell)
        self.assertNotIn("|", cell)
        self.assertNotIn("\n", cell)

    def test_redaction_preserves_clickable_file_href_attributes(self):
        home_path = "/" + "Users" + "/example"
        home_href = "file://" + home_path
        payload = (
            'const snapshot = {{"html":"'
            '<a href=\\"{}\\" target=\\"_blank\\" title=\\"{}\\">home</a>'
            '"}};'
        ).format(home_href, home_path)

        redacted = build_overview.normalize_brand_display_text(payload)

        self.assertIn('href=\\"{}\\" target=\\"_blank\\"'.format(home_href), redacted)
        self.assertIn('title=\\"~\\"', redacted)
        self.assertNotIn('href=\\"file://~\\" target=', redacted)
        self.assertNotIn('title=\\"~" target=', redacted)

    def test_file_href_redaction_placeholder_does_not_collide_with_visible_text(self):
        fixture_path = "/" + "Users" + "/example/demo.json"
        fixture_href = "file://" + fixture_path
        payload = (
            'visible __OPENRELIX_FILE_HREF_0__ '
            '<a href="{}" title="{}">demo</a>'.format(fixture_href, fixture_path)
        )

        redacted = build_overview.normalize_brand_display_text(payload)

        self.assertIn("visible __OPENRELIX_FILE_HREF_0__", redacted)
        self.assertIn('href="{}"'.format(fixture_href), redacted)
        self.assertIn('title="~', redacted)

    def test_redaction_preserves_finder_open_path_attributes(self):
        skill_path = "/" + "Users" + "/example/AI-Personal-Assets/.agents/skills/foo/SKILL.md"
        payload = (
            '<button data-open-finder-path="{}" title="{}">AI-Personal-Assets skill</button>'
        ).format(skill_path, skill_path)

        redacted = build_overview.normalize_brand_display_text(payload)

        self.assertIn('data-open-finder-path="{}"'.format(skill_path), redacted)
        self.assertIn(">OpenRelix skill</button>", redacted)
        self.assertIn('title="~/OpenRelix', redacted)

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
                    "note": "记忆条目 1 条；原生偏长期规则，nightly 偏近期整理。",
                    "note_zh": "记忆条目 1 条；原生偏长期规则，nightly 偏近期整理。",
                    "note_en": "1 memory item; native memory leans toward long-term rules.",
                },
                "codex_memory_summary_path_label": "custom-codex/memories/memory_summary.md",
                "codex_native_memory": [
                    {
                        "title": "Example dashboard and LaunchAgent runtime",
                        "display_title": "示例面板与 LaunchAgent 运行时",
                        "display_bucket": "Codex 原生",
                        "display_memory_type": "语义",
                        "display_priority": "中优先",
                        "created_at_display": "2026-04-26",
                        "updated_at_display": "2026-04-26",
                        "occurrence_label": "原生归档",
                        "context_labels": ["Example"],
                        "display_context": "Example",
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
                        "display_title": "Example historical work",
                        "display_body": "Example dashboard and memory runtime.",
                        "meta": "1 个任务；1 个来源",
                        "keywords": ["dashboard"],
                    }
                ],
                "claude_memory_path_label": "custom-claude/CLAUDE.md",
                "claude_native_memory_counts": {
                    "topic_items": 1,
                    "user_preferences": 1,
                    "general_tips": 1,
                    "source_exists": True,
                    "source_readable": True,
                    "managed_block_present": True,
                },
                "claude_native_memory_comparison": {
                    "note": "已读取 custom-claude/CLAUDE.md + custom-claude/projects/*/memory/*.md；下方展示 3 条 Claude Code 原生记忆。",
                    "note_zh": "已读取 custom-claude/CLAUDE.md + custom-claude/projects/*/memory/*.md；下方展示 3 条 Claude Code 原生记忆。",
                    "note_en": "Read custom-claude/CLAUDE.md + custom-claude/projects/*/memory/*.md; showing 3 Claude Code native memory entries below.",
                },
                "claude_native_memory": [],
                "claude_native_topic_rows": [
                    {
                        "title": "Claude native project note",
                        "display_title": "Claude 项目记忆",
                        "display_bucket": "Claude 原生",
                        "display_memory_type": "语义",
                        "display_priority": "中优先",
                        "created_at_display": "2026-04-27",
                        "updated_at_display": "2026-04-27",
                        "occurrence_label": "原生归档",
                        "context_labels": ["Claude"],
                        "display_context": "Claude",
                        "value_note": "User-authored Claude note.",
                        "display_value_note": "用户自写 Claude 记忆。",
                        "source_windows": [],
                        "source_files": [],
                    }
                ],
                "claude_native_preference_rows": [
                    {
                        "title": "Claude preference",
                        "display_title": "Claude 偏好",
                        "display_bucket": "Claude 原生",
                        "display_memory_type": "偏好",
                        "display_priority": "中优先",
                        "value_note": "Keep Claude answers concise.",
                        "display_value_note": "Claude 回答保持简洁。",
                        "source_windows": [],
                        "source_files": [],
                    }
                ],
                "claude_native_tip_rows": [
                    {
                        "title": "Claude tip",
                        "display_title": "Claude 通用 tip",
                        "display_bucket": "Claude 原生",
                        "display_memory_type": "通用 tips",
                        "display_priority": "中优先",
                        "value_note": "Check bridge mode before assuming login.",
                        "display_value_note": "先检查桥接模式再判断登录态。",
                        "source_windows": [],
                        "source_files": [],
                    }
                ],
                "assets": {"recent": [], "top": []},
                "reviews": [],
                "usage_events": [],
                "reading_guide": [],
            }
        )

        self.assertIn("Codex 原生记忆-记忆条目", html)
        self.assertIn("Codex 原生记忆-偏好", html)
        self.assertIn("Codex 原生记忆-通用 tips", html)
        self.assertIn("Codex 原生记忆-历史任务索引", html)
        self.assertNotIn("memory-card-native", html)
        self.assertNotIn("memory-native-strip", html)
        self.assertIn("示例面板与 LaunchAgent 运行时", html)
        self.assertIn("中文卡片摘要", html)
        self.assertIn("Demo value note", html)
        self.assertIn('data-lang-only="en"', html)
        self.assertNotIn(
            '<div class="panel-note"><span data-lang-only="zh">记忆条目 1 条；原生偏长期规则，nightly 偏近期整理。</span><span data-lang-only="en">1 memory item; native memory leans toward long-term rules.</span></div>',
            html,
        )
        self.assertIn("用户偏好", html)
        self.assertIn("直接给出关键结论。", html)
        self.assertIn("通用 tips", html)
        self.assertIn("优先用 rg 查找文件。", html)
        self.assertIn("历史任务索引", html)
        self.assertIn("Historical Task Index", html)
        self.assertNotIn("历史任务 1", html)
        self.assertIn("Example historical work", html)
        self.assertIn("native-brief-card", html)
        self.assertIn("User Preference", html)
        self.assertIn("General Tip", html)
        self.assertIn("Codex 原生记忆-历史任务索引", html)
        self.assertNotIn("关键词：dashboard", html)
        self.assertIn("keywords: dashboard", html)
        self.assertIn("1 task; 1 source", html)
        self.assertNotIn("1 tasks; 1 sources", html)
        self.assertIn("查看来源与上下文", html)
        self.assertIn("Show context and source", html)
        self.assertIn("首次添加 2026-04-26", html)
        self.assertIn("First added 2026-04-26", html)
        self.assertIn("关联上下文", html)
        self.assertIn("最近工作区", html)
        self.assertIn("来源窗口", html)
        self.assertIn("Preference 1", html)
        self.assertIn("Claude Code 原生记忆-记忆条目", html)
        self.assertIn("Claude Code 原生记忆-偏好", html)
        self.assertIn("Claude Code 原生记忆-通用 tips", html)
        self.assertIn("Claude Code Native Memory - Memory Items", html)
        self.assertIn("Claude Code Native Memory - Preferences", html)
        self.assertIn("Claude Code Native Memory - General Tips", html)
        self.assertIn("From Claude Code CLAUDE.md and projects/*/memory/*.md.", html)
        self.assertIn("From preferences in CLAUDE.md and auto memory.", html)
        self.assertIn("From general tips in CLAUDE.md and auto memory.", html)
        self.assertIn("Claude 项目记忆", html)
        self.assertIn("Claude native project note", html)
        self.assertIn("用户自写 Claude 记忆", html)
        self.assertIn("User-authored Claude note", html)
        self.assertIn("Claude 偏好", html)
        self.assertIn("Claude preference", html)
        self.assertIn("Claude 回答保持简洁", html)
        self.assertIn("Keep Claude answers concise", html)
        self.assertIn("Claude 通用 tip", html)
        self.assertIn("Claude tip", html)
        self.assertIn("先检查桥接模式再判断登录态", html)
        self.assertIn("Check bridge mode before assuming login", html)
        self.assertIn("Claude Native", html)
        self.assertNotIn("OpenRelix 注入", html)
        self.assertNotIn("OpenRelix-injected", html)
        self.assertNotIn("claude-native-memory-section", html)

    def test_personal_memory_token_widget_shows_bounded_context_budget(self):
        test_summary_budget = asset_runtime.memory_summary_budget_from_max(None)
        usage = build_overview.build_personal_memory_token_usage(
            [
                {
                    "display_bucket": "个人资产-长期记忆",
                    "bucket": "durable",
                    "display_memory_type": "流程",
                    "display_priority": "高优先",
                    "display_title": "面板区块重叠优先检查顶层 section 间距",
                    "display_value_note": "当面板看起来像模块重叠时，先排查顶层 section 的垂直间距与容器 margin 归属。",
                    "display_context": "OpenRelix",
                    "context_labels": ["OpenRelix"],
                }
            ],
            "integrated",
            memory_summary_budget=test_summary_budget,
        )

        self.assertTrue(usage["enabled"])
        self.assertEqual(usage["item_count"], 1)
        self.assertGreater(usage["estimated_tokens"], 20)
        self.assertEqual(usage["max_tokens"], 8000)
        self.assertEqual(usage["max_tokens_display"], "8K")
        self.assertTrue(usage["value_display_zh"].startswith("≈ "))
        self.assertLess(usage["meter_percent"], 10)
        self.assertIn("Integrated", usage["mode_label"])
        self.assertIn("1 条留本地，约 1 条进摘要（候选不设条数上限）", usage["mode_note_zh"])
        widget = build_overview.make_personal_memory_token_widget(usage)
        self.assertIn("memory-token-widget", widget)
        self.assertIn("Host context 预算", widget)
        self.assertIn("≈ ", widget)
        self.assertIn("摘要目标 6.7K / 警戒 7.4K / 上限 8K；全局 0.8K / 项目 2.4K", widget)
        self.assertIn("1 条留本地，约 1 条进摘要（候选不设条数上限）", widget)

        many_usage = build_overview.build_personal_memory_token_usage(
            [
                {
                    "bucket": "durable" if index % 2 == 0 else "session",
                    "memory_type": "semantic",
                    "priority": "medium",
                    "display_title": "记忆 {}".format(index),
                    "display_value_note": "压缩后的摘要说明 {}".format(index),
                }
                for index in range(20)
            ],
            "integrated",
            memory_summary_budget=test_summary_budget,
        )
        self.assertEqual(many_usage["context_item_limit"], 20)
        self.assertEqual(many_usage["estimated_context_item_count"], 20)
        self.assertIn("20 条留本地，约 20 条进摘要（候选不设条数上限）", many_usage["mode_note_zh"])

        with TemporaryDirectory() as tmpdir:
            summary_path = Path(tmpdir) / "memory_summary.md"
            summary_path.write_text(
                "## What's in Memory\n\n"
                "### Local personal memory registry\n\n"
                "- [durable/semantic/high] A - compact note\n"
                "- [session/task/medium] B - compact note\n"
                "\n### Other\n\n- C\n",
                encoding="utf-8",
            )
            actual_usage = build_overview.build_personal_memory_token_usage(
                many_usage_rows := [
                    {
                        "bucket": "durable",
                        "memory_type": "semantic",
                        "priority": "high",
                        "display_title": "A",
                        "display_value_note": "compact note",
                    }
                    for _ in range(8)
                ],
                "integrated",
                memory_summary_path=summary_path,
                memory_summary_budget=test_summary_budget,
            )
        self.assertEqual(len(many_usage_rows), 8)
        self.assertEqual(actual_usage["estimated_context_item_count"], 8)
        self.assertIn("8 条留本地，约 8 条进摘要", actual_usage["mode_note_zh"])

        with TemporaryDirectory() as tmpdir:
            summary_path = Path(tmpdir) / "memory_summary.md"
            summary_path.write_text(
                "## What's in Memory\n\n"
                "### Local personal memory registry\n\n"
                "- [durable/semantic/high] Stale - old host context note\n"
                "\n### Other\n\n- C\n",
                encoding="utf-8",
            )
            stale_actual_usage = build_overview.build_personal_memory_token_usage(
                [
                    {
                        "bucket": "durable",
                        "memory_type": "semantic",
                        "priority": "high",
                        "project_key": "openrelix",
                        "display_title": "Project only",
                        "display_value_note": "Do not inject globally.",
                    }
                ],
                "integrated",
                memory_summary_path=summary_path,
                memory_summary_budget=test_summary_budget,
            )
        self.assertEqual(stale_actual_usage["context_candidate_count"], 1)
        self.assertEqual(stale_actual_usage["estimated_context_item_count"], 1)
        self.assertGreater(stale_actual_usage["estimated_tokens"], 0)
        self.assertGreater(stale_actual_usage["estimated_personal_memory_tokens"], 0)
        self.assertIn("1 条留本地，约 1 条进摘要", stale_actual_usage["mode_note_zh"])

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
        self.assertIn("工作", widget)
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
                    "context_labels": ["OpenRelix", "个人资产系统", "Codex 本地环境"],
                    "bucket": "native",
                    "memory_type": "semantic",
                    "priority": "medium",
                }
            ]
        )

        self.assertIn('<span class="memory-chip">OpenRelix</span>', cards_html)
        self.assertIn(
            '<span class="memory-chip"><span data-lang-only="zh">个人资产系统</span><span data-lang-only="en">Personal assets system</span></span>',
            cards_html,
        )
        self.assertIn(
            '<span class="memory-chip"><span data-lang-only="zh">Codex 本地环境</span><span data-lang-only="en">Codex local environment</span></span>',
            cards_html,
        )

    def test_grouped_memory_cards_can_hide_redundant_bucket_meta(self):
        row = {
            "title": "Stable memory",
            "display_title": "稳定记忆",
            "value_note": "Stable note.",
            "display_value_note": "稳定摘要。",
            "display_bucket": "个人资产-长期记忆",
            "display_memory_type": "语义",
            "display_priority": "高优先",
            "bucket": "durable",
            "memory_type": "semantic",
            "priority": "high",
        }

        cards_html = build_overview.make_memory_cards([row], include_bucket_meta=False)

        self.assertNotIn("个人资产-长期记忆", cards_html)
        self.assertNotIn("Personal Asset - Long-term Memory", cards_html)
        self.assertIn("语义 · 高优先", cards_html)
        self.assertIn("Semantic · High Priority", cards_html)

        default_cards_html = build_overview.make_memory_cards([row])
        self.assertIn("个人资产-长期记忆", default_cards_html)
        self.assertIn("Personal Asset - Long-term Memory", default_cards_html)

    def test_memory_type_grouped_cards_group_by_type(self):
        cards_html = build_overview.make_memory_type_grouped_cards(
            [
                {
                    "title": "Semantic memory",
                    "display_title": "语义记忆",
                    "value_note": "Semantic note.",
                    "display_value_note": "语义摘要。",
                    "memory_type": "semantic",
                    "display_memory_type": "语义",
                    "priority": "medium",
                    "usage_frequency_sort_key": 1,
                },
                {
                    "title": "Procedure memory",
                    "display_title": "流程记忆",
                    "value_note": "Procedure note.",
                    "display_value_note": "流程摘要。",
                    "memory_type": "procedural",
                    "display_memory_type": "流程",
                    "priority": "high",
                    "usage_frequency_sort_key": 2,
                },
            ],
            include_bucket_meta=False,
        )

        self.assertIn('class="memory-type-group"', cards_html)
        self.assertLess(cards_html.index(">流程<"), cards_html.index(">语义<"))
        self.assertIn("Procedure", cards_html)
        self.assertIn("Semantic", cards_html)
        self.assertNotIn("个人资产-长期记忆", cards_html)

    def test_context_memory_grouped_cards_show_context_meta_and_expand(self):
        cards_html = build_overview.make_context_memory_type_grouped_cards(
            [
                {
                    "title": "Procedure memory",
                    "display_title": "流程记忆",
                    "value_note": "Procedure note.",
                    "display_value_note": "流程摘要。",
                    "bucket": "durable",
                    "memory_type": "procedural",
                    "priority": "high",
                    "usage_frequency_sort_key": 1.2,
                    "usage_frequency_matched_window_count": 2,
                    "occurrence_count": 3,
                }
            ]
            + [
                {
                    "title": "Semantic memory {}".format(index),
                    "display_title": "语义记忆 {}".format(index),
                    "value_note": "Semantic note {}.".format(index),
                    "display_value_note": "语义摘要 {}。".format(index),
                    "bucket": "session",
                    "memory_type": "semantic",
                    "priority": "low" if index == 0 else "medium",
                    "usage_frequency_sort_key": 0,
                }
                for index in range(5)
            ]
            + [
                {
                    "title": "Low priority memory",
                    "display_title": "低优先记忆",
                    "value_note": "Low priority note.",
                    "display_value_note": "低优先摘要。",
                    "bucket": "low_priority",
                    "memory_type": "mapping",
                    "priority": "medium",
                    "usage_frequency_sort_key": 0,
                }
            ],
        )

        self.assertIn('class="memory-type-group"', cards_html)
        self.assertLess(cards_html.index(">流程<"), cards_html.index(">语义<"))
        self.assertIn("长期记忆 · 重点 · 直接证据", cards_html)
        self.assertIn("Long-term Memory · Important · Direct Evidence", cards_html)
        self.assertIn("工作记忆 · 常规 · 待验证", cards_html)
        self.assertIn("低优先级记忆 · 常规 · 待验证", cards_html)
        self.assertNotIn(" · 低优先 · ", cards_html)
        self.assertIn("查看来源与上下文", cards_html)
        self.assertIn("查看更多 1 条", cards_html)

    def test_context_memory_cards_render_feedback_controls(self):
        cards_html = build_overview.make_context_memory_type_grouped_cards(
            [
                {
                    "memory_key": "memory-feedback-demo",
                    "title": "Useful memory",
                    "display_title": "有用记忆",
                    "value_note": "Useful note.",
                    "display_value_note": "有用摘要。",
                    "bucket": "durable",
                    "memory_type": "procedural",
                    "priority": "high",
                    "user_feedback": "liked",
                }
            ]
        )

        self.assertNotIn('data-memory-feedback="pinned"', cards_html)
        self.assertIn('data-memory-feedback="liked"', cards_html)
        self.assertIn('data-memory-feedback="downvoted"', cards_html)
        self.assertIn('class="memory-feedback-icon"', cards_html)
        self.assertIn('data-memory-key="memory-feedback-demo"', cards_html)
        self.assertIn('data-memory-feedback-state="liked"', cards_html)
        self.assertIn('data-memory-feedback="liked" data-memory-key="memory-feedback-demo"', cards_html)
        self.assertIn('aria-pressed="true"', cards_html)

    def test_policy_memory_cards_sort_by_heat_and_use_new_tags(self):
        cards_html = build_overview.make_policy_memory_cards(
            [
                {
                    "title": "Low heat task",
                    "display_title": "低热任务",
                    "value_note": "Task note.",
                    "display_value_note": "任务摘要。",
                    "bucket": "durable",
                    "scope": "project",
                    "injection_policy": "project_context",
                    "memory_type": "task",
                    "priority": "medium",
                    "usage_frequency_sort_key": 1,
                },
                {
                    "title": "High heat procedure",
                    "display_title": "高热流程",
                    "value_note": "Procedure note.",
                    "display_value_note": "流程摘要。",
                    "bucket": "durable",
                    "scope": "project",
                    "injection_policy": "project_context",
                    "memory_type": "procedural",
                    "priority": "high",
                    "usage_frequency_sort_key": 9,
                },
            ]
            + [
                {
                    "title": "Extra procedure {}".format(index),
                    "display_title": "额外流程 {}".format(index),
                    "value_note": "Extra note.",
                    "display_value_note": "额外摘要。",
                    "bucket": "durable",
                    "scope": "project",
                    "injection_policy": "project_context",
                    "memory_type": "procedural",
                    "priority": "high",
                    "usage_frequency_sort_key": 0.5,
                }
                for index in range(3)
            ]
        )

        self.assertNotIn('class="memory-type-group"', cards_html)
        self.assertLess(cards_html.index("高热流程"), cards_html.index("低热任务"))
        self.assertEqual(cards_html.count("native-brief-grid memory-grid content-more-grid"), 2)
        self.assertIn("项目上下文", cards_html)
        self.assertIn("流程", cards_html)
        self.assertIn("任务", cards_html)
        self.assertIn("重点", cards_html)
        self.assertIn("常规", cards_html)
        self.assertIn("热度 9", cards_html)
        self.assertNotIn('<span data-lang-only="zh">项目</span><span data-lang-only="en">Project</span>', cards_html)
        self.assertNotIn("长期记忆", cards_html)
        self.assertNotIn("Long-term Memory", cards_html)

    def test_local_retention_memory_cards_merge_on_demand_without_type_groups(self):
        cards_html = build_overview.make_local_retention_memory_cards(
            [
                {
                    "title": "On demand item",
                    "display_title": "按需条目",
                    "value_note": "On demand note.",
                    "display_value_note": "按需摘要。",
                    "bucket": "session",
                    "scope": "domain",
                    "injection_policy": "on_demand",
                    "memory_type": "semantic",
                    "priority": "medium",
                    "usage_frequency_sort_key": 9,
                },
                {
                    "title": "Local item",
                    "display_title": "本地条目",
                    "value_note": "Local note.",
                    "display_value_note": "本地摘要。",
                    "bucket": "low_priority",
                    "scope": "local",
                    "injection_policy": "local_only",
                    "memory_type": "episodic",
                    "priority": "low",
                    "usage_frequency_sort_key": 8,
                },
            ]
            + [
                {
                    "title": "Extra retained {}".format(index),
                    "display_title": "额外保留 {}".format(index),
                    "value_note": "Extra note.",
                    "display_value_note": "额外摘要。",
                    "bucket": "session",
                    "scope": "local",
                    "injection_policy": "never",
                    "memory_type": "task",
                    "priority": "medium",
                    "usage_frequency_sort_key": 0.5,
                }
                for index in range(3)
            ]
        )

        self.assertNotIn('class="memory-type-group"', cards_html)
        self.assertIn("本地保留", cards_html)
        self.assertNotIn("按需召回", cards_html)
        self.assertLess(cards_html.index("按需条目"), cards_html.index("本地条目"))
        self.assertIn("查看更多 1 条", cards_html)
        self.assertEqual(cards_html.count("native-brief-grid memory-grid content-more-grid"), 2)

    def test_memory_feedback_adjusts_host_context_policy(self):
        base = {
            "memory_key": "feedback-policy-demo",
            "bucket": "session",
            "priority": "medium",
            "scope": "global",
            "injection_policy": "on_demand",
            "title": "默认用 apply_patch 修改文件",
            "value_note": "当用户要求修改文件时，应优先使用 apply_patch，保持局部改动。",
        }

        self.assertEqual(overview_memory_feedback.normalize_feedback("pinned"), "liked")
        liked = overview_memory_feedback.apply_memory_feedback(base, "liked")
        self.assertEqual(liked["priority"], "high")
        self.assertEqual(liked["bucket"], "durable")
        self.assertEqual(
            overview_memory_context.host_context_injection_policy_from_record(liked),
            overview_memory_context.INJECTION_GLOBAL_CONTEXT,
        )
        self.assertTrue(overview_memory_context.memory_record_has_global_context_approval(liked))
        neutral = overview_memory_feedback.apply_memory_feedback(
            liked,
            {"feedback": "neutral", "updated_at": "2026-05-09T12:30:00+08:00"},
        )
        self.assertEqual(neutral["user_feedback"], "")
        self.assertEqual(neutral["user_feedback_updated_at"], "2026-05-09T12:30:00+08:00")
        self.assertFalse(neutral["user_pinned"])

        downvoted = overview_memory_feedback.apply_memory_feedback(base, "downvoted")
        self.assertEqual(downvoted["bucket"], "low_priority")
        self.assertEqual(downvoted["priority"], "low")
        self.assertEqual(
            overview_memory_context.host_context_injection_policy_from_record(downvoted),
            overview_memory_context.INJECTION_LOCAL_ONLY,
        )
        self.assertTrue(overview_memory_context.memory_record_is_low_priority(downvoted))

    def test_downvoted_memory_stays_last_in_local_only_view(self):
        views = overview_memory_context.build_memory_policy_views(
            [
                {
                    "title": "Normal local",
                    "bucket": "session",
                    "priority": "medium",
                    "scope": "local",
                    "injection_policy": "local_only",
                },
                {
                    "title": "Downvoted local",
                    "bucket": "low_priority",
                    "priority": "low",
                    "scope": "local",
                    "injection_policy": "local_only",
                    "user_feedback": "downvoted",
                },
                {
                    "title": "Never local",
                    "bucket": "session",
                    "priority": "medium",
                    "scope": "local",
                    "injection_policy": "never",
                },
            ]
        )

        self.assertEqual(views["local_only"]["rows"][-1]["title"], "Downvoted local")

    def test_memory_context_policy_labels_use_general_and_project_context(self):
        self.assertEqual(
            overview_memory_context.policy_label(overview_memory_context.INJECTION_GLOBAL_CONTEXT, language="zh"),
            "通用上下文",
        )
        self.assertEqual(
            overview_memory_context.policy_label(overview_memory_context.INJECTION_GLOBAL_CONTEXT, language="en"),
            "General Context",
        )
        self.assertEqual(
            overview_memory_context.policy_label(overview_memory_context.INJECTION_PROJECT_CONTEXT, language="zh"),
            "项目上下文",
        )
        self.assertEqual(
            overview_memory_context.policy_label(overview_memory_context.INJECTION_PROJECT_CONTEXT, language="en"),
            "Project Context",
        )

    def test_memory_context_compiler_cards_use_curated_metrics(self):
        sections = {section: [] for section in overview_curated_memory.SECTION_ORDER}
        sections[overview_curated_memory.SECTION_STABLE_PREFERENCES] = [
            {"title": "稳定偏好", "value_note": "长期稳定。"},
        ]
        sections[overview_curated_memory.SECTION_OPERATING_RULES] = [
            {"title": "操作规则", "value_note": "可直接复用。"},
        ]
        sections[overview_curated_memory.SECTION_TASK_GROUPS] = [
            {
                "title": "跨项目疑似",
                "value_note": "需要确认范围。",
                "diagnostics": ["possible_cross_project_leakage"],
            },
        ]
        sections[overview_curated_memory.SECTION_LOCAL_VOLATILE] = [
            {
                "title": "时效内容",
                "value_note": "适合本地保留。",
                "diagnostics": ["timeline_like"],
            },
        ]
        html = build_overview.make_memory_context_compiler_body(
            {
                "compiler": {
                    "total_count": 86,
                    "global_candidate_count": 25,
                    "host_context_candidate_count": 73,
                    "project_context_count": 48,
                    "on_demand_count": 4,
                }
            },
            {
                "sections": sections,
                "diagnostics": {
                    "duplicate_clusters": [
                        {"canonical_key": "topic:one", "source_entry_ids": ["a", "b"]},
                    ],
                },
            },
        )

        self.assertIn("整理后记忆", html)
        self.assertIn("稳定可用", html)
        self.assertIn("待确认", html)
        self.assertIn("已合并重复", html)
        self.assertIn(">4</strong>", html)
        self.assertIn(">2</strong>", html)
        self.assertIn(">1</strong>", html)
        self.assertNotIn("通用上下文", html)
        self.assertNotIn(">73</strong>", html)

    def test_context_memory_preview_only_uses_integrated_context_candidates(self):
        budget = asset_runtime.memory_summary_budget_from_max(5000)
        rows = [
            {
                "bucket": "session",
                "memory_type": "semantic",
                "priority": "high",
                "display_title": "高频短期记忆",
                "display_value_note": "高频短期摘要。",
                "usage_frequency_sort_key": 9,
                "updated_at": "2026-04-29",
                "occurrence_count": 10,
            },
            {
                "bucket": "durable",
                "memory_type": "procedural",
                "priority": "high",
                "display_title": "项目专属记忆",
                "display_value_note": "这个记忆只能在项目上下文里使用。",
                "scope": "project",
                "injection_policy": "project_context",
                "usage_frequency_sort_key": 99,
                "updated_at": "2026-04-30",
                "occurrence_count": 20,
            },
            {
                "bucket": "durable",
                "memory_type": "procedural",
                "priority": "high",
                "display_title": "长期高优记忆",
                "display_value_note": "长期高优摘要。",
                "usage_frequency_sort_key": 0,
                "updated_at": "2026-04-20",
                "occurrence_count": 1,
            },
            {
                "bucket": "low_priority",
                "memory_type": "semantic",
                "priority": "medium",
                "display_title": "低优先记忆",
                "display_value_note": "低优先摘要。",
            },
        ]

        preview = build_overview.build_personal_memory_context_preview(
            rows,
            "integrated",
            memory_summary_budget=budget,
            item_count=1,
        )

        self.assertEqual([row["display_title"] for row in preview], ["项目专属记忆"])
        usage = build_overview.build_personal_memory_token_usage(
            rows,
            "integrated",
            memory_summary_budget=budget,
        )
        self.assertEqual(usage["context_candidate_count"], 3)
        self.assertEqual(
            build_overview.build_personal_memory_context_preview(
                rows,
                "local-only",
                memory_summary_budget=budget,
            ),
            [],
        )

    def test_episodic_memory_type_is_localized(self):
        self.assertEqual(build_overview.display_memory_type("episodic", language="zh"), "事件记忆")
        self.assertEqual(build_overview.display_memory_type("episodic", language="en"), "Episodic")

        cards_html = build_overview.make_memory_type_grouped_cards(
            [
                {
                    "title": "Episodic memory",
                    "display_title": "事件记忆",
                    "value_note": "Event note.",
                    "display_value_note": "事件摘要。",
                    "memory_type": "episodic",
                    "display_memory_type": "事件记忆",
                    "priority": "medium",
                }
            ],
            include_bucket_meta=False,
        )

        self.assertIn(">事件记忆<", cards_html)
        self.assertIn(">Episodic<", cards_html)
        self.assertIn("事件记忆 · 常规", cards_html)
        self.assertIn("Episodic · Standard", cards_html)

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
        self.assertIn('<body data-language="zh">', html)
        self.assertNotIn('<body data-language="zh" data-theme-choice="system">', html)
        self.assertIn('data-language-option="zh" aria-pressed="true"', html)
        self.assertIn('data-language-option="en" aria-pressed="false"', html)
        self.assertIn("data-memory-feedback-endpoint=", html)
        self.assertIn("wireMemoryFeedbackActions();", html)
        self.assertIn("findMemoryFeedbackTopGrid", html)
        self.assertIn("findCuratedMemoryTopList", html)
        self.assertIn("moveCuratedMemoryItem(row, feedback)", html)
        self.assertIn("removeCuratedMemoryItem(row);", html)
        self.assertIn("rememberMemoryFeedback(memoryKey, savedFeedback);", html)
        self.assertIn("applyStoredMemoryFeedback();", html)
        self.assertIn('if (state === "neutral")', html)
        self.assertIn("store[memoryKey] = { feedback: state, updated_at: Date.now() };", html)
        self.assertIn("removeMemoryFeedbackCard(row);", html)
        self.assertIn("已标记有用，并置顶展示", html)
        self.assertIn("已取消有用/无用标记", html)
        self.assertNotIn("const reloadAfterMs = Number((payload && payload.reload_after_ms) || 0);", html)
        self.assertIn('"OpenRelix 工作台": "OpenRelix Workbench"', html)
        self.assertIn(
            '<span class="hero-brand-line"><span data-lang-only="zh">你的专属AI记忆珍藏</span><span data-lang-only="en">Your personal AI memory relics</span></span>',
            html,
        )
        package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertNotIn("hero-version-line", html)
        self.assertIn(
            'data-current-version="{}"'.format(package_json["version"]),
            html,
        )
        self.assertIn('id="openrelix-update-panel"', html)
        self.assertIn('data-update-layout="compact"', html)
        self.assertIn('data-update-command="openrelix update --yes --force"', html)
        self.assertIn("版本与更新", html)
        self.assertIn("Version &amp; Updates", html)
        self.assertIn("重装未完成", html)
        self.assertIn("Reinstall Incomplete", html)
        self.assertIn('data.status === "reinstall_failed"', html)
        self.assertIn('data-update-last-check', html)
        self.assertIn('data-update-compact-current', html)
        self.assertIn('data-update-primary', html)
        self.assertIn('data-update-command-row hidden', html)
        self.assertIn("openrelix-update-demo", html)
        self.assertIn("runScheduledCheck", html)
        self.assertIn("applyLanguage(defaultLanguage);", html)
        self.assertIn("refreshStatusLanguage();", html)
        self.assertIn('setStatus("live", "", "live_refreshed");', html)
        self.assertIn("offline_service", html)
        self.assertIn("requestNativeTokenServiceStart", html)
        self.assertIn("openrelixEnsureTokenLive", html)
        self.assertIn("starting_service", html)
        self.assertIn("正在通过 OpenRelix App 启动本地 Token 服务，然后会自动重试", html)
        self.assertIn("本地 Token 服务暂时不可用，已继续展示本地快照", html)
        self.assertIn("OpenRelix App 会自动尝试恢复后台服务", html)
        self.assertIn("OpenRelix.app will try to restore the background service automatically.", html)
        self.assertNotIn("本地 Token 服务未启动。请运行 openrelix open panel 后再点实时刷新。", html)
        self.assertIn("serviceUnavailable && requestNativeTokenServiceStart()", html)
        self.assertIn("window.localStorage", html)
        self.assertIn("openrelix-token-usage-source-v1", html)
        self.assertIn("tokenSourceDateRange", html)
        self.assertIn('requestUrl.searchParams.set("start_date", sourceRange.startDate);', html)
        self.assertNotIn("side-nav-sublabel", html)
        self.assertIn("personal-memory-compiler-section", html)
        self.assertIn("personal-memory-curated-section", html)
        self.assertNotIn('data-nav-target="personal-memory-compiler-section"', html)
        self.assertNotIn('data-nav-target="personal-memory-curated-section"', html)
        self.assertNotIn("旁路整理预览", html)
        self.assertNotIn("不改变注入", html)
        self.assertIn("总览", html)
        self.assertNotIn("personal-memory-global-section", html)
        self.assertNotIn("personal-memory-project-section", html)
        self.assertNotIn("personal-memory-on-demand-section", html)
        self.assertNotIn("personal-memory-local-section", html)
        self.assertIn("codex-native-topic-section", html)
        self.assertNotIn('data-nav-target="codex-native-topic-section"', html)
        self.assertNotIn('data-nav-target="codex-native-preference-section"', html)
        self.assertNotIn('data-nav-target="claude-native-tip-section"', html)
        self.assertIn('data-nav-target="asset-overview-section"', html)
        self.assertIn('data-nav-target="top-assets-section"', html)
        self.assertNotIn('data-nav-target="asset-stats-snapshot-section"', html)
        self.assertIn('data-nav-target="token-filter-panel"', html)
        self.assertNotIn('data-nav-target="token-section"', html)
        self.assertIn("skills 热度", html)
        self.assertNotIn("本期小结", html)
        self.assertNotIn("highlight-list", html)

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
        self.assertIn('<body data-language="en">', html)
        self.assertNotIn('<body data-language="en" data-theme-choice="system">', html)
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
                                {"label": "输入", "value": 160000000, "title": "输入：1.6亿", "meta": "无缓存输入 Token"}
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
                                {"label": "输入", "value": 42443000, "title": "输入：4244.3万", "meta": "无缓存输入 Token"}
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

        self.assertIn("const todayTokenValue = preparedTokenUsage.today_total_tokens_display", html)
        self.assertIn("const todayCostValue = preparedTokenUsage.today_cost_display", html)
        self.assertIn("function tokenRequestCacheKey(filters, windowDays)", html)
        self.assertIn("function tokenDateRangeForDays(days, endDateValue)", html)
        self.assertIn("function tokenDefaultDateRange(days)", html)
        self.assertIn("const defaultTokenDateRange = tokenDefaultDateRange(7);", html)
        self.assertIn("requestTimeoutMs: 90000,", html)
        self.assertIn("function tokenEffectiveWindowDays(filters, fallbackWindowDays)", html)
        self.assertIn("const fallbackRange = tokenDateRangeForDays(30, requestedEndDate);", html)
        self.assertIn("let startDate = activeFilters.startDate || fallbackRange.startDate;", html)
        self.assertIn("fallbackRange.startDate < startDate", html)
        self.assertNotIn("return tokenDateRangeForDays(30, endDate);", html)
        self.assertIn("function tokenUsageMatchesRequestFilters(tokenUsage, filters)", html)
        self.assertIn("function tokenShortDateMonthKey(text, context)", html)
        self.assertIn("function tokenShortDateIsoKey(text, context)", html)
        self.assertIn("function tokenRowDayKey(row, context)", html)
        self.assertIn("return tokenRowDayKey(row, context) === endIso;", html)
        self.assertIn("function aggregateDailyRowsByMonth(rows, tokenUsage)", html)
        self.assertIn("const monthContext = Object.assign", html)
        self.assertIn("function normalizeTokenDisplayRow(row)", html)
        self.assertIn("const normalizedRows = filteredRows.map(normalizeTokenDisplayRow);", html)
        self.assertIn("aggregateDailyRowsByMonth(normalizedRows, monthContext)", html)
        self.assertIn("function tokenRowBreakdownValues(row)", html)
        self.assertIn("function dailySummaryTokenValueForDate(dateValue)", html)
        self.assertIn("function resolveNightlyStatValue(summary, item)", html)
        self.assertIn("const value = resolveNightlyStatValue(summary, item);", html)
        self.assertIn("renderNightlySummary(state.selectedNightlyDate);", html)
        self.assertIn("cacheCreationTokens", html)
        self.assertIn("token-cache-write", html)
        self.assertIn('requestUrl.searchParams.set("provider", normalizeTokenProvider(filters.provider));', html)
        self.assertIn('requestUrl.searchParams.set("group_by", "day");', html)
        self.assertIn("function tokenFilterRangeLabel(filters, tokenUsage)", html)
        self.assertIn('data-token-range-days="1"', html)
        self.assertIn('data-token-range-days="3"', html)
        self.assertIn('data-token-range-days="7"', html)
        self.assertIn('data-token-range-days="30"', html)
        self.assertIn('data-token-range-days="7" aria-pressed="true" data-active="true"', html)
        self.assertIn('token-filter-grid token-filter-top-grid', html)
        self.assertIn('token-filter-grid token-filter-bottom-grid', html)
        self.assertIn('tokenRangeButtons: Array.from(document.querySelectorAll("[data-token-range-days]"))', html)
        self.assertIn("setTokenFilterState(tokenDateRangeForDays(days, endDate), true);", html)
        self.assertIn('data-token-date-field="start"', html)
        self.assertIn('data-token-date-field="end"', html)
        self.assertIn('id="token-start-date"', html)
        self.assertIn('<input id="token-start-date" class="token-date-input" type="hidden" value="">', html)
        self.assertIn('data-date-picker-button="token:start"', html)
        self.assertIn('data-date-picker-button="token:end"', html)
        self.assertIn('data-date-picker-popover="token" hidden', html)
        self.assertIn("startDate: defaultTokenDateRange.startDate,", html)
        self.assertIn("endDate: defaultTokenDateRange.endDate,", html)
        self.assertIn('datePickerButtons: Array.from(document.querySelectorAll("[data-date-picker-button]"))', html)
        self.assertIn("function wireDatePickers()", html)
        self.assertIn("data-date-picker-option", html)
        self.assertIn(".token-filter-panel {\n      position: relative;\n      overflow: visible;", html)
        self.assertIn("color-mix(in srgb, var(--elevated) 88%", html)
        self.assertIn("tokenRequestSeq: 0,", html)
        self.assertIn("tokenRefreshInFlight: false,", html)
        self.assertIn("tokenRefreshQueued: false,", html)
        self.assertIn("tokenStaleRetryDelayMs: 8000,", html)
        self.assertIn("const requestSeq = state.tokenRequestSeq + 1;", html)
        self.assertIn('elements.tokenFilterPanel.setAttribute("aria-busy", isLoading ? "true" : "false");', html)
        self.assertIn("function requestStillCurrent()", html)
        self.assertIn("function tokenUsageVisualSignature(tokenUsage)", html)
        self.assertIn('String(filters.startDate || ""),', html)
        self.assertIn('String(filters.endDate || ""),', html)
        self.assertIn("async function performTokenUsageRefresh(forceRefresh)", html)
        self.assertIn("state.tokenRefreshQueued = true;", html)
        self.assertIn("refreshing_cache", html)
        self.assertIn("已先展示缓存，后台正在同步最新 Token", html)
        self.assertIn("if (payload.refreshing)", html)
        self.assertIn("cacheKey === tokenRequestCacheKey(state.tokenFilters || {}, windowDays)", html)
        self.assertIn("state.tokenStaleRetryDelayMs = Math.min(delayMs * 2, 60000);", html)
        self.assertIn("}, 3500);", html)
        self.assertNotIn(".token-filter-panel.is-loading .token-segment-button", html)
        self.assertIn("function extractTokenRowCost(row)", html)
        self.assertIn("display: compactTokenWithCostValue(row.value, rowCost)", html)
        self.assertIn("prepared.summary_cards = deriveTokenSummaryCards(prepared);", html)
        self.assertIn("周期 Token 速览", html)
        self.assertIn('updateMetricCard(\n          "today_cost",', html)
        self.assertIn("function dailySummaryTokenValueForDate(dateValue)", html)
        self.assertIn("function resolveNightlyStatValue(summary, item)", html)
        self.assertIn("function nightlyStatCardClass(item)", html)
        self.assertIn("const value = resolveNightlyStatValue(summary, item);", html)
        self.assertIn("renderNightlySummary(state.selectedNightlyDate);", html)
        self.assertIn('updateTokenVisuals(state.tokenUsage, state.tokenSourceKind);', html)
        self.assertNotIn("rowDate.slice(0, 10) === endIso", html)
        self.assertNotIn('data-metric-key="seven_day_token"', html)
        self.assertNotIn("缓存读取占总输入", html)

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

    def test_dashboard_clamps_root_horizontal_overflow(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")

        self.assertIn("overflow-x: clip;", source)
        self.assertIn("overscroll-behavior-x: none;", source)
        self.assertIn("width: min(1280px, calc(100vw - 48px));", source)
        self.assertIn("width: min(1280px, calc(100vw - 304px));", source)
        self.assertIn("width: min(1280px, calc(100vw - 28px));", source)
        mobile_nav_css = source[source.index("@media (max-width: 1120px)") : source.index("@media (max-width: 1040px)")]
        self.assertIn(".hero-topline {{", mobile_nav_css)
        self.assertIn("flex-direction: column;", mobile_nav_css)
        self.assertIn(".hero-actions {{", mobile_nav_css)
        self.assertIn("width: 100%;", mobile_nav_css)
        self.assertIn("overscroll-behavior-x: contain;", mobile_nav_css)
        self.assertIn("padding: 24px 0 calc(128px + env(safe-area-inset-bottom));", mobile_nav_css)
        self.assertIn("top: auto;", mobile_nav_css)
        self.assertIn("bottom: max(12px, env(safe-area-inset-bottom));", mobile_nav_css)
        self.assertIn("scroll-margin-bottom: 128px;", mobile_nav_css)

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
        self.assertIn('class="asset-discovery-table top-skills-table"', top_section)
        self.assertIn('class="top-skills-trend-col"', top_section)
        self.assertIn("{asset_filter_panel}", main_template)
        self.assertIn("{top_skill_rows}", top_section)
        self.assertIn('class="review-grid review-panel-grid"', review_section)
        self.assertIn("{review_cards}", review_section)
        self.assertIn(".top-skills-table {{", source)
        self.assertIn("table-layout: fixed;", source)

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

    def test_memory_sections_stack_and_cards_use_two_column_brief_cards(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")
        main_start = source.index("{nightly_summary_panel}")
        main_template = source[
            main_start : source.index("</main>", main_start)
        ]
        memory_start = main_template.index('id="memory-section"')
        memory_section = main_template[
            memory_start : main_template.index('id="codex-native-section"', memory_start)
        ]

        self.assertIn("{personal_asset_memory_family_header}", memory_section)
        self.assertIn("{memory_compiler_header}", main_template)
        self.assertIn("{memory_compiler_body}", memory_section)
        self.assertIn("{curated_memory_header}", memory_section)
        self.assertIn("{curated_memory_body}", memory_section)
        self.assertNotIn("{on_demand_memory_header}", memory_section)
        self.assertNotIn('class="grid two-up"', memory_section)
        self.assertNotIn('class="review-grid memory-grid"', memory_section)
        self.assertNotIn("{memory_registry_header}", main_template)
        self.assertNotIn("{memory_registry_cards}", main_template)

        stack_css = source[source.index(".memory-stack {{") : source.index(".review-card {{")]
        self.assertIn("grid-template-columns: 1fr;", stack_css)
        self.assertIn(".memory-group-list {{", stack_css)
        self.assertIn(".memory-stack .memory-grid,", stack_css)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", stack_css)

        cards_html = build_overview.make_memory_cards(
            [
                {
                    "title": "Memory {}".format(index),
                    "value_note": (
                        "demo " * 50
                        if index == 0
                        else "demo"
                    ),
                    "bucket": "durable",
                    "memory_type": "semantic",
                    "priority": "high",
                }
                for index in range(9)
            ]
        )
        self.assertEqual(cards_html.count('<article class="native-brief-card memory-brief-card">'), 9)
        self.assertIn("查看更多 5 条", cards_html)
        self.assertIn("完整说明", cards_html)
        self.assertIn("Full Note", cards_html)
        self.assertLess(cards_html.index("Memory 3"), cards_html.index("查看更多 5 条"))
        self.assertGreater(cards_html.index("Memory 4"), cards_html.index("查看更多 5 条"))

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
        self.assertLess(main_template.index("{memory_compiler_header}"), main_template.index("{asset_metric_cards}"))
        for header in (
            "{codex_native_topic_header}",
            "{codex_native_preference_header}",
            "{codex_native_tip_header}",
            "{codex_native_task_group_header}",
        ):
            self.assertLess(main_template.index(header), main_template.index("{asset_metric_cards}"))
        self.assertLess(main_template.index("{asset_metric_cards}"), main_template.index("{asset_filter_panel}"))
        self.assertLess(main_template.index("{asset_filter_panel}"), main_template.index("{asset_stats_snapshot_panel}"))
        self.assertLess(main_template.index("{asset_stats_snapshot_panel}"), main_template.index("{top_assets_header}"))
        self.assertLess(main_template.index("{asset_metric_cards}"), main_template.index("{window_overview_header}"))
        self.assertNotIn("{type_panel}", main_template)
        self.assertNotIn("{month_panel}", main_template)
        self.assertLess(main_template.index("{top_skill_rows}"), main_template.index("{mcp_usage_panel}"))
        self.assertLess(main_template.index("{mcp_usage_panel}"), main_template.index("{discovered_assets_section}"))
        self.assertNotIn("{scope_panel}", main_template)
        self.assertNotIn("{domain_panel}", main_template)
        self.assertLess(main_template.index("{pipeline_status_panel}"), main_template.index("{memory_compiler_header}"))
        self.assertLess(main_template.index("{asset_metric_cards}"), main_template.index("{project_context_body}"))
        self.assertLess(main_template.index("{discovered_assets_section}"), main_template.index("{project_context_body}"))
        self.assertLess(main_template.index('id="window-layer-section"'), main_template.index("{project_context_body}"))
        self.assertLess(main_template.index("{reviews_header}"), main_template.index("{project_context_body}"))
        self.assertLess(main_template.index("{usage_rows}"), main_template.index("{window_overview_header}"))
        self.assertLess(main_template.index("{usage_rows}"), main_template.index("{project_context_body}"))
        self.assertLess(main_template.index("{project_context_body}"), main_template.index("{window_overview_header}"))

    def test_build_html_keeps_finder_reveal_endpoint_for_path_buttons(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")

        self.assertIn("data-finder-open-endpoint", source)
        self.assertIn("data-asset-refresh-endpoint", source)
        self.assertIn("data-open-finder-path", source)
        self.assertIn("asset-layer-refresh-button", source)
        self.assertIn("刷新资产层", source)
        self.assertIn("function refreshAssetLayer", source)
        self.assertIn("function openFinderPath", source)
        self.assertIn("wireFinderOpenActions", source)
        self.assertIn("memory-family-head asset-ledger-head", source)
        self.assertIn("memory-family-head.asset-ledger-head", source)
        self.assertIn("action-button asset-refresh-button", source)
        self.assertIn("asset-refresh-meta", source)
        self.assertIn("{asset_refresh_meta_html}", source)

    def test_skill_quarantine_action_updates_rows_without_static_reload(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")
        start = source.index("function submitSkillQuarantineAction")
        end = source.index("function wireSkillQuarantineActions", start)
        handler_source = source[start:end]
        apply_start = source.index("function applySkillQuarantineSuccess")
        apply_end = source.index("function submitSkillQuarantineAction", apply_start)
        apply_source = source[apply_start:apply_end]

        self.assertIn("applySkillQuarantineSuccess(action, button, payload)", handler_source)
        self.assertIn('action === "block-grace-all"', source)
        self.assertIn("skillQuarantinePayloadWarningCount(payload)", handler_source)
        self.assertIn('skillQuarantineMessage("savedWithWarnings")', handler_source)
        self.assertIn("skillQuarantineActionButtonLabel(action, button)", handler_source)
        self.assertIn("skillQuarantineKeysForBucket(bulkBucket)", handler_source)
        self.assertIn("requestPayload.entity_keys", handler_source)
        self.assertIn('skillQuarantineMessage("stale")', handler_source)
        self.assertIn('skillQuarantineMessage("partial")', handler_source)
        self.assertNotIn("window.confirm", handler_source)
        self.assertIn('applySkillQuarantineBlockAllSuccess(payload, "grace")', apply_source)
        self.assertIn("skillQuarantineBlockedEntries(payload)", source)
        self.assertIn("findSkillQuarantineRow(entityKey)", source)
        self.assertIn("rememberSkillQuarantineAction", source)
        self.assertIn("applyStoredSkillQuarantineActions()", source)
        self.assertIn("skill-quarantine-warning-panel", source)
        self.assertIn("skill-quarantine-warning-inline", source)
        self.assertIn("skill-quarantine-table-title", source)
        self.assertIn("skill-quarantine-table-count", source)
        self.assertIn("skillQuarantineCountText", source)
        self.assertIn("Skill/MCP 小黑屋", source)
        self.assertIn("一键隔离可选项", source)
        self.assertIn("打开小黑屋文件夹", source)
        self.assertIn('data-label-zh="打开小黑屋文件夹"', source)
        self.assertIn("隔离目录", source)
        self.assertIn("skill-quarantine-metric-action", source)
        self.assertIn("skill-quarantine-path-hint", source)
        self.assertIn("grid-template-rows: auto auto", source)
        self.assertIn("justify-content: center", source)
        self.assertIn(".skill-quarantine-metric-body > span", source)
        self.assertIn(".skill-quarantine-metric-action .action-button span", source)
        self.assertIn("opacity: 1", source)
        self.assertNotIn(".skill-quarantine-metric span,", source)
        self.assertIn("skill_quarantine_display_path", source)
        self.assertLess(source.index("{suggested_metric}"), source.index("{grace_metric}"))
        self.assertLess(source.index("{grace_metric}"), source.index("{quarantined_metric}"))
        self.assertNotIn("skill-quarantine-actions", source)
        self.assertNotIn("skill-quarantine-action-set", source)
        self.assertNotIn(".skill-quarantine-metric.is-quarantined strong", source)
        self.assertIn("只登记小黑屋状态", source)
        self.assertIn("来源不在可安全搬移目录，未搬文件", source)
        self.assertIn("隔离提示", source)
        self.assertGreater(source.index("{warning_summary}"), source.index("{quarantined_rows}</tbody>"))
        self.assertIn("创建天数", source)
        self.assertIn("超过 7 天保护期且近 30 天未使用", source)
        self.assertIn("新增 7 天内未使用", source)
        self.assertIn("已停用且默认不再注入", source)
        self.assertIn("确认删除这条小黑屋记录", source)
        self.assertIn("确认删除", source)
        self.assertIn("恢复使用", source)
        self.assertNotIn("放行", source)
        self.assertIn("skill-quarantine-delete-popover-row", source)
        self.assertIn("skill-quarantine-delete-popover-actions", source)
        self.assertIn('data-skill-quarantine-confirm-submit", "true"', source)
        self.assertIn('data-skill-quarantine-action", "cancel-delete"', source)
        self.assertIn("showSkillQuarantineDeletePopover(button)", source)
        self.assertIn('postPanelAnalyticsEvent("skill_quarantine_action"', source)
        self.assertIn('module_id: "skill_quarantine"', source)
        self.assertIn("skillQuarantineAnalyticsAction(action)", source)
        self.assertIn("skill_quarantine_warning_count", source)
        self.assertIn("postSkillQuarantineAnalytics(action, analyticsResult, payload, applyResult, bulkBucket)", source)
        self.assertIn('postSkillQuarantineAnalytics(action, "failed"', source)
        self.assertIn("isSkillQuarantineStaleBackendError(action, error)", source)
        self.assertIn('requestNativeTokenServiceStart("skill-quarantine", endpoint, true)', source)
        self.assertIn("runSkillQuarantineRequest(true)", source)
        self.assertIn("本地后台已重启，正在重试小黑屋操作", source)
        self.assertIn('button.getAttribute("data-skill-quarantine-confirm-submit") === "true"', source)
        self.assertIn('submitSkillQuarantineAction("delete", entityKey, sourceButton)', source)
        self.assertNotIn("confirm-delete", source)
        self.assertIn('data-skill-quarantine-action="delete"', source)
        self.assertIn('action === "delete"', source)
        self.assertIn("applySkillQuarantineDeleteSuccess", source)
        self.assertIn("已提交 {{count}} 项，后台处理中", source)
        self.assertNotIn("skill-quarantine-table-head span", source)
        self.assertNotIn("一键隔离" + "缓" + "冲期项", source)
        self.assertIn("migration_warnings", source)
        self.assertNotIn("window.confirm", source)
        self.assertNotIn('setSkillQuarantineStatus(skillQuarantineMessage("confirmDelete")', source)
        self.assertNotIn("window.location.reload()", handler_source)

    def test_skill_quarantine_actions_are_accepted_into_background_worker(self):
        source = (ROOT / "scripts" / "token_live_server.py").read_text(encoding="utf-8")

        self.assertIn("start_manual_pipeline_refresh(target_date=None, asset_layer_only=False)", source)
        self.assertIn("start_manual_pipeline_refresh_background(asset_layer_only=True)", source)
        self.assertIn("skill_quarantine_response_needs_refresh(action, response)", source)
        self.assertIn("BACKGROUND_SKILL_QUARANTINE_ACTIONS", source)
        self.assertIn("skill_quarantine_accepted_response(", source)
        self.assertIn("start_skill_quarantine_action_async(", source)
        self.assertIn('response["task"] = task_snapshot', source)
        self.assertIn("self._send_json(202, response", source)
        self.assertIn("with quarantine_action_lock(PATHS):", source)
        self.assertIn("skill_quarantine_block_result_warning_count(result)", source)
        self.assertIn('"migration_warning_count"', source)
        self.assertIn("selected_skill_quarantine_keys(payload)", source)
        self.assertIn("block_selected_skill_quarantine_entities(entity_keys", source)
        self.assertIn("delete_quarantined_entity(PATHS, entity_key)", source)
        self.assertIn('"blocked"', source)
        self.assertIn('"failed_count"', source)

    def test_skill_quarantine_refresh_is_only_needed_after_state_changes(self):
        self.assertTrue(token_live_server.skill_quarantine_response_needs_refresh("block", {"ok": True}))
        self.assertTrue(token_live_server.skill_quarantine_response_needs_refresh("unblock", {"ok": True}))
        self.assertTrue(token_live_server.skill_quarantine_response_needs_refresh("delete", {"ok": True}))
        self.assertTrue(
            token_live_server.skill_quarantine_response_needs_refresh(
                "block-grace-all",
                {"ok": True, "blocked_count": 1},
            )
        )
        self.assertTrue(
            token_live_server.skill_quarantine_response_needs_refresh(
                "remove-project-skill-root",
                {"ok": True, "changed": True},
            )
        )
        self.assertFalse(
            token_live_server.skill_quarantine_response_needs_refresh(
                "block-grace-all",
                {"ok": True, "blocked_count": 0},
            )
        )
        self.assertFalse(
            token_live_server.skill_quarantine_response_needs_refresh(
                "add-project-skill-root",
                {"ok": True, "changed": False},
            )
        )
        self.assertFalse(token_live_server.skill_quarantine_response_needs_refresh("block", {"ok": False}))

    def test_skill_quarantine_accepted_response_returns_rows_for_optimistic_ui(self):
        cached_view = {
            "grace": [
                {
                    "entity_key": "skill:optional-a",
                    "entity_type": "skill",
                    "identifier": "optional-a",
                    "display_name": "optional-a",
                    "usage_30d": 0,
                },
                {
                    "entity_key": "skill:optional-b",
                    "entity_type": "skill",
                    "identifier": "optional-b",
                    "display_name": "optional-b",
                    "usage_30d": 0,
                },
            ]
        }

        response = token_live_server.skill_quarantine_accepted_response(
            "block-grace-all",
            entity_keys=["skill:optional-a"],
            cached_view=cached_view,
        )
        fallback_response = token_live_server.skill_quarantine_accepted_response(
            "block-grace-all",
            entity_keys=[],
            cached_view=cached_view,
        )
        delete_response = token_live_server.skill_quarantine_accepted_response(
            "delete",
            entity_key="skill:optional-a",
            cached_view=cached_view,
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["status"], "accepted")
        self.assertEqual(response["blocked_count"], 1)
        self.assertEqual(response["blocked"][0]["entity_key"], "skill:optional-a")
        self.assertEqual(response["blocked"][0]["isolation_status"], "state_only")
        self.assertEqual(fallback_response["blocked_count"], 2)
        self.assertEqual(delete_response["status"], "accepted")
        self.assertEqual(delete_response["result"]["entity_key"], "skill:optional-a")

    def test_skill_quarantine_action_worker_returns_before_action_finishes(self):
        release_worker = token_live_server.threading.Event()
        worker_started = token_live_server.threading.Event()
        calls = []

        def fake_action(action, entity_key="", entity_keys=None, payload=None, cached_view=None):
            calls.append((action, entity_key, tuple(entity_keys or [])))
            worker_started.set()
            release_worker.wait(1)
            return {"ok": True, "action": action}

        with mock.patch.object(token_live_server, "run_skill_quarantine_action", side_effect=fake_action):
            started_at = token_live_server.time.monotonic()
            started, snapshot = token_live_server.start_skill_quarantine_action_async(
                "block-grace-all",
                entity_keys=["skill:optional-a"],
            )
            elapsed = token_live_server.time.monotonic() - started_at
            self.assertTrue(worker_started.wait(0.2))

        release_worker.set()
        self.assertTrue(started)
        self.assertEqual(snapshot["status"], "queued")
        self.assertLess(elapsed, 0.2)
        self.assertEqual(calls[0], ("block-grace-all", "", ("skill:optional-a",)))

    def test_skill_quarantine_background_refresh_returns_before_worker_finishes(self):
        release_worker = token_live_server.threading.Event()
        worker_started = token_live_server.threading.Event()
        calls = []

        def fake_refresh(target_date=None, asset_layer_only=False):
            calls.append((target_date, asset_layer_only))
            worker_started.set()
            release_worker.wait(1)
            return True, {"ok": True, "status": "running"}

        with mock.patch.object(token_live_server, "load_pipeline_status", return_value={"status": "completed"}), mock.patch.object(
            token_live_server,
            "start_manual_pipeline_refresh",
            side_effect=fake_refresh,
        ):
            started_at = token_live_server.time.monotonic()
            started, snapshot = token_live_server.start_manual_pipeline_refresh_background(
                target_date="2026-06-08",
                asset_layer_only=True,
            )
            elapsed = token_live_server.time.monotonic() - started_at
            self.assertTrue(worker_started.wait(0.2))

        release_worker.set()
        self.assertTrue(started)
        self.assertEqual(snapshot["status"], "queued")
        self.assertLess(elapsed, 0.2)
        self.assertEqual(snapshot["target_date"], "2026-06-08")

    def test_build_html_uses_light_system_dashboard_style(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")

        self.assertIn("--bg: #f5f5f7;", source)
        self.assertIn("background: var(--surface);", source)
        self.assertIn("document.documentElement.setAttribute(\"data-theme-choice\", themeChoice);", source)
        self.assertIn("document.documentElement.setAttribute(\"data-theme-choice\", currentThemeChoice);", source)
        self.assertIn("html[data-theme=\"dark\"],", source)
        self.assertIn("background: #f5f5f7;", source)
        self.assertIn("font-family: -apple-system, BlinkMacSystemFont", source)
        self.assertNotIn("linear-gradient(135deg, #182225", source)
        self.assertNotIn("radial-gradient", source)
        self.assertNotIn("font-size: clamp", source)
        self.assertNotIn("letter-spacing: 0.08em", source)

    def test_nightly_summary_ledger_layout_fits_content_height(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")
        nightly_start = source.index(".nightly-shell {{")
        nightly_css = source[nightly_start : source.index(".panel-head {{", nightly_start)]

        self.assertIn("align-items: start;", nightly_css)
        self.assertIn("height: fit-content;", nightly_css)
        self.assertIn("grid-template-columns: repeat(auto-fit, minmax(min(124px, 100%), 1fr));", nightly_css)
        self.assertIn("padding: 16px;", nightly_css)
        self.assertIn(".nightly-stat-card.is-token-metric .nightly-stat-value {{", nightly_css)
        self.assertIn("font-size: 30px;", nightly_css)
        self.assertIn(".nightly-backfill-command[hidden] {{", nightly_css)
        self.assertNotIn("min-height: 100%;", nightly_css)

    def test_build_html_prepaint_light_preference_is_not_overridden_by_body(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")

        self.assertIn('document.documentElement.setAttribute("data-theme", resolvedTheme);', source)
        self.assertIn('html[data-theme-choice="system"]:not([data-theme="light"])', source)
        self.assertNotIn('body[data-theme-choice="system"]:not([data-theme="light"])', source)
        self.assertNotIn('<body data-language="{default_language}" data-theme-choice="system">', source)
        self.assertIn('<body data-language="{default_language}">', source)

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
                    "context_labels": ["OpenRelix"],
                    "stats": [
                        {"label": "窗口", "value": 15},
                        {"label": "通用上下文", "value": 1},
                        {"label": "项目上下文", "value": 1},
                        {"label": "今日 Token", "value": "1.4亿"},
                    ],
                    "note_text": "这些数字按新记忆策略和今日 Token 快照展示：通用和项目都会进 host context，并分别受预算控制。",
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
        self.assertIn('class="nightly-stat-card is-token-metric"', html)
        self.assertLess(html.index('id="nightly-summary-title"'), html.index('id="nightly-date-input"'))
        self.assertLess(html.index('id="nightly-date-input"'), html.index('id="nightly-lead"'))

    def test_window_filter_panel_reuses_token_filter_style(self):
        html = build_overview.make_window_filter_panel("2026-05-06", "2026-05-08")

        self.assertIn('id="window-filter-panel"', html)
        self.assertIn('class="token-filter-panel window-filter-panel"', html)
        self.assertIn('id="window-filter-summary"', html)
        self.assertIn('id="window-start-date"', html)
        self.assertIn('type="hidden" value="2026-05-06"', html)
        self.assertIn('value="2026-05-06"', html)
        self.assertIn('id="window-end-date"', html)
        self.assertIn('type="hidden" value="2026-05-08"', html)
        self.assertIn('value="2026-05-08"', html)
        self.assertIn('class="apple-date-button"', html)
        self.assertIn('data-date-picker-button="window:start"', html)
        self.assertIn('data-window-date-button="start"', html)
        self.assertIn('data-window-date-display="start">2026/05/06</span>', html)
        self.assertIn('data-date-picker-button="window:end"', html)
        self.assertIn('data-window-date-button="end"', html)
        self.assertIn('data-window-date-display="end">2026/05/08</span>', html)
        self.assertIn('id="window-date-popover"', html)
        self.assertIn('data-date-picker-popover="window" hidden', html)
        self.assertIn('id="window-date-grid"', html)
        self.assertIn('data-window-range-days="1"', html)
        self.assertIn('data-window-range-days="3" aria-pressed="true" data-active="true"', html)
        self.assertIn('id="window-search-trigger"', html)
        self.assertIn('aria-haspopup="dialog"', html)
        self.assertIn('aria-controls="window-search-live"', html)
        self.assertIn('id="window-search-reset"', html)
        self.assertIn('删除搜索并恢复未检索状态', html)
        self.assertIn('id="window-search-live" role="dialog" aria-modal="false"', html)
        self.assertIn('id="window-search-close"', html)
        self.assertIn('class="window-search-close-label"', html)
        self.assertIn(">关闭</span>", html)
        self.assertIn('data-window-search-range-days="1"', html)
        self.assertIn('data-window-search-range-days="3" aria-pressed="true" data-active="true"', html)
        self.assertIn('id="window-search-start-date"', html)
        self.assertIn('data-date-picker-button="window-search:start"', html)
        self.assertIn('data-date-picker-display="window-search:start"', html)
        self.assertIn('type="hidden" value="2026-05-06"', html)
        self.assertIn('id="window-search-end-date"', html)
        self.assertIn('data-date-picker-button="window-search:end"', html)
        self.assertIn('data-date-picker-display="window-search:end"', html)
        self.assertIn('id="window-search-date-popover"', html)
        self.assertIn('data-date-picker-popover="window-search" hidden', html)
        self.assertIn('data-date-picker-grid="window-search"', html)
        self.assertIn('for="window-search-query"', html)
        self.assertIn('id="window-search-query"', html)
        self.assertIn('type="text" role="searchbox"', html)
        self.assertIn('id="window-search-submit"', html)
        self.assertIn('class="window-search-submit"', html)
        self.assertIn('class="window-search-submit-label"', html)
        self.assertIn('class="window-search-trigger-icon"', html)
        self.assertIn('class="window-search-input-icon"', html)
        self.assertIn('window-search-strip-scope-group', html)
        self.assertIn('data-window-search-active', html)
        self.assertNotIn('id="window-search-clear"', html)
        self.assertIn('data-window-search-scope="all" aria-pressed="true" data-active="true"', html)
        self.assertIn('data-window-search-scope="ai"', html)
        self.assertIn('data-window-search-scope="raw-question"', html)
        self.assertIn('data-window-search-scope="raw-conclusion"', html)
        self.assertIn('data-window-search-scope="id"', html)
        self.assertIn('data-window-search-scope="project"', html)
        self.assertLess(html.index('data-window-search-scope="project"'), html.index('data-window-search-scope="ai"'))
        self.assertIn('data-window-range-days="7"', html)
        self.assertIn('data-window-range-days="30"', html)
        self.assertNotIn('id="window-overview-date-input"', html)
        self.assertNotIn('class="nightly-date-control"', html)

    def test_asset_filter_panel_reuses_token_filter_style(self):
        html = build_overview.make_asset_filter_panel("2026-04-10", "2026-05-09")

        self.assertIn('id="asset-filter-panel"', html)
        self.assertIn('class="token-filter-panel asset-filter-panel"', html)
        self.assertIn('id="asset-filter-summary"', html)
        self.assertIn('id="asset-start-date"', html)
        self.assertIn('type="hidden" value="2026-04-10"', html)
        self.assertIn('data-date-picker-button="asset:start"', html)
        self.assertIn('data-date-picker-display="asset:start">2026/04/10</span>', html)
        self.assertIn('id="asset-end-date"', html)
        self.assertIn('type="hidden" value="2026-05-09"', html)
        self.assertIn('data-date-picker-button="asset:end"', html)
        self.assertIn('data-date-picker-display="asset:end">2026/05/09</span>', html)
        self.assertIn('data-date-picker-popover="asset" hidden', html)
        self.assertIn('data-asset-range-days="1"', html)
        self.assertIn('data-asset-range-days="3"', html)
        self.assertIn('data-asset-range-days="7"', html)
        self.assertIn('data-asset-range-days="30" aria-pressed="true" data-active="true"', html)

    def test_filter_window_overview_by_date_range_updates_counts(self):
        overview = {
            "windows": [
                {"date": "2026-05-08", "window_id": "w3"},
                {"date": "2026-05-07", "window_id": "w2"},
                {"date": "2026-05-05", "window_id": "w1"},
            ]
        }

        filtered = build_overview.filter_window_overview_by_date_range(
            overview,
            "2026-05-07",
            "2026-05-08",
        )

        self.assertEqual(filtered["window_count"], 2)
        self.assertEqual(filtered["source_dates"], ["2026-05-08", "2026-05-07"])
        self.assertEqual(filtered["source_date_count"], 2)
        self.assertEqual([item["window_id"] for item in filtered["windows"]], ["w3", "w2"])
        self.assertEqual([item["display_index"] for item in filtered["windows"]], [2, 1])

    def test_window_cards_show_activity_source_instead_of_repeating_workspace(self):
        thread_id = "019dcefe-37f1-7a83-a8a6-720bd6b79d7f"
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            raw_windows_dir = root / "raw" / "windows"
            (raw_windows_dir / "2026-04-28").mkdir(parents=True)
            (raw_windows_dir / "2026-04-28" / "{}.json".format(thread_id)).write_text(
                json.dumps({"window_id": thread_id}),
                encoding="utf-8",
            )
            paths = replace(build_overview.PATHS, raw_windows_dir=raw_windows_dir)
            build_overview.load_window_record.cache_clear()
            try:
                with mock.patch.object(build_overview, "PATHS", paths):
                    html = build_overview.make_window_summary_cards(
                        {
                            "date": "2026-04-28",
                            "windows": [
                                {
                                    "window_id": thread_id,
                                    "display_index": 1,
                                    "cwd": str(root),
                                    "cwd_display": root.name,
                                    "project_label": "OpenRelix",
                                    "activity_source": "app-server",
                                    "thread_source": "cli",
                                    "activity_source_label": "采集：Codex app-server · 线程来源：cli",
                                    "window_summary": "Codex 侧栏标题",
                                    "resume_id": thread_id,
                                    "resume_command": "codex resume {}".format(thread_id),
                                    "resume_url": "codex://threads/{}".format(thread_id),
                                    "question_count": 1,
                                    "conclusion_count": 1,
                                    "question_summary": "问题",
                                    "main_takeaway": "**结论**：执行 `codex resume {}`".format(thread_id),
                                    "keywords": ["窗口"],
                                    "latest_activity_display": "刚刚",
                                    "started_at_display": "刚刚",
                                    "recent_prompts": [{"time": "刚刚", "text": "问题原文"}],
                                    "recent_conclusions": [{"time": "刚刚", "text": "结论原文"}],
                                }
                            ],
                        }
                    )
            finally:
                build_overview.load_window_record.cache_clear()

        self.assertIn("OpenRelix · Codex · 原始窗口 ID：{}".format(thread_id), html)
        self.assertLess(
            html.index("OpenRelix · Codex · 原始窗口 ID：{}".format(thread_id)),
            html.index('class="window-card-takeaway window-markdown"'),
        )
        self.assertNotIn('class="window-card-title-label"', html)
        self.assertNotIn("OpenRelix · 窗口 1", html)
        self.assertIn("采集：Codex app-server · 线程来源：cli", html)
        self.assertIn('class="window-card-cwd"', html)
        self.assertIn("当前目录 <a", html)
        self.assertIn("data-window-resume-copy", html)
        self.assertIn('data-resume-command="codex resume {}"'.format(thread_id), html)
        self.assertIn("data-window-resume-open", html)
        self.assertIn('data-codex-url="codex://threads/{}"'.format(thread_id), html)
        self.assertIn('data-codex-resume-id="{}"'.format(thread_id), html)
        self.assertNotIn("data-window-review-copy", html)
        self.assertNotIn("发起复盘", html)
        self.assertNotIn("data-window-review-prompt", html)
        self.assertNotIn("重点判断能否沉淀为可复用资产", html)
        self.assertIn("原始窗口 ID：{}".format(thread_id), html)
        self.assertIn("执行", html)
        self.assertIn("<code>codex resume {}</code>".format(thread_id), html)
        self.assertNotIn('<p class="window-card-path"><a', html)
        self.assertIn('class="window-card-takeaway window-markdown"', html)
        summary_html = html[
            html.index('<summary class="window-card-trigger">') : html.index("</summary>")
        ]
        self.assertNotIn("问题摘要", summary_html)
        self.assertNotIn("结论摘要", summary_html)
        self.assertNotIn("问题：", summary_html)
        self.assertNotIn("结论：", summary_html)
        self.assertIn('class="window-card-keywords"', summary_html)
        self.assertNotIn("<details", summary_html)
        self.assertLess(
            html.index('class="window-card-keywords"'),
            html.index('class="window-card-detail"'),
        )
        self.assertNotIn("<li class=\"window-detail-item\"><span>原始窗口 ID", html)
        self.assertNotIn("窗口信息", html)
        self.assertNotIn("查看完整结论摘要", html)
        self.assertNotIn('class="window-subdetail', html)
        self.assertNotIn("最近问题", html)
        self.assertNotIn("最近结论", html)
        self.assertIn("问题与结论", html)
        self.assertNotIn("问题总结", html)
        self.assertNotIn("结论总结", html)
        self.assertIn("大模型已做智能整理", html)
        self.assertIn("原始记录见", html)
        self.assertIn("原始窗口 JSON", html)
        self.assertIn("窗口创建 刚刚", html)
        self.assertLess(html.index("窗口创建 刚刚"), html.index("最近活动 刚刚"))
        self.assertIn('data-window-started-display="刚刚"', html)
        self.assertIn('data-window-search-ai="', html)
        self.assertIn('data-window-search-raw-question="', html)
        self.assertIn('data-window-search-raw-conclusion="', html)
        self.assertIn('data-window-search-id="', html)
        self.assertIn('data-window-search-project="', html)
        self.assertIn('data-window-search-all="', html)
        self.assertIn(thread_id, html[html.index('data-window-search-id="') : html.index('data-window-search-project="')])
        self.assertIn('data-window-display-label="1"', html)
        self.assertIn('data-window-takeaway="**结论**：执行 codex resume', html)
        self.assertIn('data-window-topic=', html)
        self.assertIn('data-window-task-key=', html)
        self.assertIn('data-window-task-label=', html)
        self.assertIn('data-window-status-tags=', html)
        self.assertIn('class="window-summary-pair-list"', html)
        self.assertIn('class="window-summary-pair-item"', html)
        self.assertNotIn("会话文件", html)
        self.assertNotIn("会话 JSONL", html)

    def test_window_cards_default_to_twenty_visible_items(self):
        windows = []
        for index in range(21):
            windows.append(
                {
                    "date": "2026-05-08",
                    "window_id": "w{}".format(index),
                    "cwd": "/tmp/openrelix",
                    "cwd_display": "openrelix",
                    "project_label": "OpenRelix",
                    "activity_source": "history",
                    "window_summary": "窗口 {}".format(index),
                    "question_count": 1,
                    "conclusion_count": 1,
                    "question_summary": "问题 {}".format(index),
                    "main_takeaway": "结论 {}".format(index),
                    "keywords": ["窗口"],
                    "latest_activity_at": "2026-05-08T10:{:02d}:00+08:00".format(index),
                    "latest_activity_display": "05-08 10:{:02d}".format(index),
                    "started_at": "2026-05-08T09:{:02d}:00+08:00".format(index),
                    "started_at_display": "05-08 09:{:02d}".format(index),
                    "recent_prompts": [],
                    "recent_conclusions": [],
                }
            )

        html = build_overview.make_window_summary_cards(
            {"date": "2026-05-08", "windows": windows},
            initial_start_date="2026-05-08",
            initial_end_date="2026-05-08",
            initial_visible_count=20,
        )

        self.assertEqual(html.count('data-window-card="true"'), 21)
        visible_start = html.index('id="window-w19"')
        visible_tag = html[visible_start : html.index(">", visible_start) + 1]
        self.assertNotIn(" hidden", visible_tag)
        hidden_start = html.index('id="window-w20"')
        hidden_tag = html[hidden_start : html.index(">", hidden_start) + 1]
        self.assertIn(" hidden", hidden_tag)
        self.assertIn('data-window-date="2026-05-08"', html)
        self.assertIn('data-window-display-label="21"', html)

    def test_window_cards_show_multiple_summary_pairs(self):
        html = build_overview.make_window_summary_cards(
            {
                "date": "2026-04-28",
                "windows": [
                    {
                        "window_id": "w-pairs",
                        "display_index": 1,
                        "project_label": "OpenRelix",
                        "window_title": "SQLite 检索底层改造",
                        "question_count": 2,
                        "conclusion_count": 2,
                        "question_summary": "问题1：SQLite 是否值得切；问题2：搜索 UI 是否先做",
                        "main_takeaway": "结论1：先切底层索引；结论2：UI 后置",
                        "summary_pairs": [
                            {"question": "SQLite 是否值得切", "conclusion": "先切底层索引"},
                            {"question": "搜索 UI 是否先做", "conclusion": "UI 后置"},
                        ],
                        "raw_summary_pairs": [
                            {"question": "原始问法", "conclusion": "原始答复"},
                        ],
                        "summary_status": "summarized",
                        "summary_status_label": "大模型已做智能整理",
                        "keywords": ["sqlite", "搜索"],
                        "latest_activity_display": "刚刚",
                        "started_at_display": "刚刚",
                        "recent_prompts": [],
                        "recent_conclusions": [],
                    }
                ],
            }
        )

        self.assertIn("SQLite 检索底层改造", html)
        self.assertIn("大模型已做智能整理", html)
        self.assertIn("智能整理", html)
        self.assertIn("原始信息", html)
        self.assertIn("原始问法", html)
        self.assertIn("原始答复", html)
        ai_search_attr = html[html.index('data-window-search-ai="') : html.index('data-window-search-raw-question="')]
        self.assertIn("搜索 UI 是否先做", ai_search_attr)
        self.assertIn("UI 后置", ai_search_attr)
        raw_question_attr = html[
            html.index('data-window-search-raw-question="') : html.index('data-window-search-raw-conclusion="')
        ]
        raw_conclusion_attr = html[
            html.index('data-window-search-raw-conclusion="') : html.index('data-window-search-id="')
        ]
        self.assertIn("原始问法", raw_question_attr)
        self.assertIn("原始答复", raw_conclusion_attr)
        detail_html = html[html.index('class="window-card-detail"') :]
        summary_html = html[
            html.index('<summary class="window-card-trigger">') : html.index("</summary>")
        ]
        self.assertIn('class="window-card-pair-preview"', summary_html)
        self.assertIn("问题1", summary_html)
        self.assertIn("SQLite 是否值得切", summary_html)
        self.assertIn("结论1", summary_html)
        self.assertIn("先切底层索引", summary_html)
        self.assertNotIn("问题2：搜索 UI 是否先做", summary_html)
        self.assertNotIn("结论2：UI 后置", summary_html)
        for text in ["问题1", "SQLite 是否值得切", "结论1", "先切底层索引", "问题2", "搜索 UI 是否先做", "结论2", "UI 后置"]:
            self.assertIn(text, detail_html)
        self.assertLess(detail_html.index("问题1"), detail_html.index("SQLite 是否值得切"))
        self.assertLess(detail_html.index("SQLite 是否值得切"), detail_html.index("结论1"))
        self.assertLess(detail_html.index("结论1"), detail_html.index("先切底层索引"))
        self.assertLess(detail_html.index("先切底层索引"), detail_html.index("问题2"))
        self.assertLess(detail_html.index("问题2"), detail_html.index("搜索 UI 是否先做"))
        self.assertLess(detail_html.index("搜索 UI 是否先做"), detail_html.index("结论2"))
        self.assertLess(detail_html.index("结论2"), detail_html.index("UI 后置"))
        self.assertNotIn("最近问题", html)
        self.assertNotIn("最近结论", html)

    def test_window_cards_hide_single_ai_question_from_result_preview(self):
        html = build_overview.make_window_summary_cards(
            {
                "date": "2026-04-28",
                "windows": [
                    {
                        "window_id": "w-single-ai",
                        "display_index": 1,
                        "project_label": "OpenRelix",
                        "window_title": "整理后的标题",
                        "question_count": 1,
                        "conclusion_count": 1,
                        "question_summary": "单个问题正文",
                        "main_takeaway": "单个结论正文",
                        "summary_pairs": [
                            {"question": "单个问题正文", "conclusion": "单个结论正文"},
                        ],
                        "summary_status": "summarized",
                        "summary_status_label": "大模型已做智能整理",
                        "keywords": [],
                        "latest_activity_display": "刚刚",
                        "started_at_display": "刚刚",
                        "recent_prompts": [],
                        "recent_conclusions": [],
                    }
                ],
            }
        )

        summary_html = html[
            html.index('<summary class="window-card-trigger">') : html.index("</summary>")
        ]
        detail_html = html[html.index('class="window-card-detail"') :]
        self.assertIn("大模型已做智能整理", summary_html)
        self.assertIn('class="window-card-pair-preview"', summary_html)
        self.assertNotIn('class="window-card-pair-label">问题</span>', summary_html)
        self.assertNotIn("单个问题正文", summary_html)
        self.assertIn('class="window-card-pair-label">结论</span>', summary_html)
        self.assertIn("单个结论正文", summary_html)
        self.assertIn("单个问题正文", detail_html)
        self.assertIn("单个结论正文", detail_html)

    def test_window_cards_mark_raw_fallback_when_summary_not_ready(self):
        html = build_overview.make_window_summary_cards(
            {
                "date": "2026-04-28",
                "windows": [
                    {
                        "window_id": "w-raw",
                        "display_index": 1,
                        "project_label": "OpenRelix",
                        "question_count": 2,
                        "conclusion_count": 2,
                        "question_summary": "原始问题1；原始问题2",
                        "main_takeaway": "原始结论1；原始结论2",
                        "summary_pairs": [
                            {"question": "原始问题1", "conclusion": "原始结论1"},
                            {"question": "原始问题2", "conclusion": "原始结论2"},
                        ],
                        "summary_status": "raw_fallback",
                        "summary_status_label": "暂未做二次学习和总结，当前展示原始问题和结论",
                        "keywords": [],
                        "latest_activity_display": "刚刚",
                        "started_at_display": "刚刚",
                        "recent_prompts": [{"time": "刚刚", "text": "原始问题"}],
                        "recent_conclusions": [{"time": "刚刚", "text": "原始结论"}],
                    }
                ],
            }
        )

        self.assertIn("暂未做二次学习和总结", html)
        self.assertIn('data-summary-status="raw_fallback"', html)
        self.assertIn("原始问题1", html)
        self.assertIn("原始结论1", html)
        summary_html = html[
            html.index('<summary class="window-card-trigger">') : html.index("</summary>")
        ]
        self.assertIn("<h3 class=\"window-card-window-summary\">原始问题1</h3>", summary_html)
        self.assertIn("问题1", summary_html)
        self.assertIn("原始问题1", summary_html)
        self.assertIn("结论1", summary_html)
        self.assertIn("原始结论1", summary_html)
        self.assertNotIn("问题2：原始问题2", summary_html)
        self.assertNotIn("结论2：原始结论2", summary_html)
        self.assertNotIn("原始信息", summary_html)
        detail_html = html[html.index('class="window-card-detail"') :]
        self.assertIn('data-summary-mode="raw"', detail_html)
        self.assertIn("问题2", detail_html)
        self.assertIn("原始问题2", detail_html)
        self.assertIn("结论2", detail_html)
        self.assertIn("原始结论2", detail_html)

    def test_window_cards_mark_lightweight_summary_without_model_badge(self):
        html = build_overview.make_window_summary_cards(
            {
                "date": "2026-05-03",
                "windows": [
                    {
                        "window_id": "w-lightweight",
                        "display_index": 1,
                        "project_label": "OpenRelix",
                        "window_title": "停止后台的回溯",
                        "question_count": 1,
                        "conclusion_count": 1,
                        "question_summary": "问题1：停止后台的回溯",
                        "main_takeaway": "结论1：已停止后台回溯任务。",
                        "summary_pairs": [
                            {
                                "question": "停止后台的回溯",
                                "conclusion": "已停止后台回溯任务。",
                            }
                        ],
                        "raw_summary_pairs": [
                            {
                                "question": "停止后台的回溯",
                                "conclusion": "我终止的是这棵进程树。",
                            }
                        ],
                        "summary_status": "lightweight",
                        "keywords": ["openrelix", "回溯任务"],
                        "latest_activity_display": "05-03 23:50",
                        "started_at_display": "05-03 23:40",
                        "recent_prompts": [],
                        "recent_conclusions": [],
                    }
                ],
            }
        )

        self.assertIn("轻度回溯快速整理，未做大模型总结", html)
        self.assertIn('data-summary-status="lightweight"', html)
        self.assertIn("快速整理", html)
        self.assertIn("原始信息", html)
        self.assertNotIn("大模型已做智能整理", html)
        self.assertNotIn("AI-organized", html)

    def test_window_overview_keeps_lightweight_summary_separate_from_model_summary(self):
        daily_capture = {
            "date": "2026-05-03",
            "windows": [
                {
                    "date": "2026-05-03",
                    "window_id": "w-lightweight",
                    "cwd": "/tmp/openrelix",
                    "source": "app_server",
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "prompts": [{"local_time": "2026-05-03T23:40:00+08:00", "text": "原始问题"}],
                    "conclusions": [
                        {"completed_at": "2026-05-03T23:50:00+08:00", "text": "原始结论"}
                    ],
                }
            ],
        }
        latest_nightly = {
            "date": "2026-05-03",
            "stage": "preliminary",
            "model_status": "skipped_lightweight",
            "summary_generation": "lightweight",
            "window_summaries": [
                {
                    "window_id": "w-lightweight",
                    "cwd": "/tmp/openrelix",
                    "window_title": "轻量整理标题",
                    "question_summary": "问题1：轻量整理问题",
                    "main_takeaway": "结论1：轻量整理结论",
                    "summary_pairs": [
                        {
                            "question": "轻量整理问题",
                            "conclusion": "轻量整理结论",
                        }
                    ],
                    "keywords": ["openrelix"],
                }
            ],
        }

        items = build_overview.build_window_items_from_daily_capture(
            daily_capture,
            latest_nightly=latest_nightly,
            language="zh",
        )

        self.assertEqual(items[0]["summary_status"], "lightweight")
        self.assertEqual(items[0]["window_title"], "轻量整理标题")
        self.assertIn("轻量整理问题", items[0]["question_summary"])
        self.assertIn("轻量整理结论", items[0]["main_takeaway"])
        self.assertIn("未做大模型总结", items[0]["summary_status_label"])

    def test_window_cards_hide_codex_app_button_for_non_uuid_resume_id(self):
        html = build_overview.make_window_summary_cards(
            {
                "date": "2026-04-28",
                "windows": [
                    {
                        "window_id": "thread-name",
                        "display_index": 1,
                        "project_label": "OpenRelix",
                        "resume_id": "thread-name",
                        "resume_command": "codex resume thread-name",
                        "resume_url": build_overview.codex_resume_url("thread-name"),
                        "question_count": 1,
                        "conclusion_count": 1,
                        "question_summary": "问题",
                        "main_takeaway": "结论",
                        "keywords": [],
                        "latest_activity_display": "刚刚",
                        "started_at_display": "刚刚",
                        "recent_prompts": [],
                        "recent_conclusions": [],
                    }
                ],
            }
        )

        self.assertIn("data-window-resume-copy", html)
        self.assertIn('data-resume-command="codex resume thread-name"', html)
        self.assertNotIn("data-window-resume-open", html)
        self.assertNotIn("data-codex-url=", html)

    def test_window_cards_include_codex_home_for_profile_aware_resume(self):
        thread_id = "019dcefe-37f1-7a83-a8a6-720bd6b79d7f"
        codex_home = "/tmp/openrelix-codex-home"
        electron_user_data = "/tmp/OpenRelix Codex Profile"
        html = build_overview.make_window_summary_cards(
            {
                "date": "2026-04-28",
                "windows": [
                    {
                        "window_id": thread_id,
                        "display_index": 1,
                        "project_label": "OpenRelix",
                        "resume_id": thread_id,
                        "resume_command": build_overview.window_resume_command(
                            "codex",
                            thread_id,
                            codex_home=codex_home,
                        ),
                        "resume_url": build_overview.codex_resume_url(thread_id),
                        "codex_home": codex_home,
                        "codex_electron_user_data_path": electron_user_data,
                        "question_count": 1,
                        "conclusion_count": 1,
                        "question_summary": "问题",
                        "main_takeaway": "结论",
                        "keywords": [],
                        "latest_activity_display": "刚刚",
                        "started_at_display": "刚刚",
                        "recent_prompts": [],
                        "recent_conclusions": [],
                    }
                ],
            }
        )

        self.assertIn("data-window-resume-open", html)
        self.assertIn('data-codex-home="/tmp/openrelix-codex-home"', html)
        self.assertIn('data-codex-electron-user-data-path="/tmp/OpenRelix Codex Profile"', html)
        self.assertIn('data-codex-system-profile=""', html)
        self.assertIn('data-copy-resume-on-switch="1"', html)
        self.assertIn(
            'data-resume-command="CODEX_HOME=/tmp/openrelix-codex-home codex resume {}'.format(thread_id),
            html,
        )
        self.assertIn("打开 Codex App", html)
        self.assertIn("已打开，命令已复制", html)
        self.assertIn('CODEX_HOME=/tmp/openrelix-codex-home codex resume {}'.format(thread_id), html)

    def test_window_cards_include_codex_home_for_primary_isolated_profile_resume(self):
        thread_id = "019dcefe-37f1-7a83-a8a6-720bd6b79d7f"
        codex_home = Path("/tmp/openrelix-codex-home")
        electron_user_data = "/tmp/OpenRelix Codex Profile"
        paths = replace(build_overview.PATHS, codex_home=codex_home)
        with mock.patch.object(build_overview, "PATHS", paths):
            html = build_overview.make_window_summary_cards(
                {
                    "date": "2026-04-28",
                    "windows": [
                        {
                            "window_id": thread_id,
                            "display_index": 1,
                            "project_label": "OpenRelix",
                            "resume_id": thread_id,
                            "resume_url": build_overview.codex_resume_url(thread_id),
                            "codex_home": str(codex_home),
                            "codex_electron_user_data_path": electron_user_data,
                            "question_count": 1,
                            "conclusion_count": 1,
                            "question_summary": "问题",
                            "main_takeaway": "结论",
                            "keywords": [],
                            "latest_activity_display": "刚刚",
                            "started_at_display": "刚刚",
                            "recent_prompts": [],
                            "recent_conclusions": [],
                        }
                    ],
                }
            )

        self.assertIn(
            'data-resume-command="CODEX_HOME=/tmp/openrelix-codex-home codex resume {}'.format(thread_id),
            html,
        )
        self.assertIn('data-copy-resume-on-switch="1"', html)
        self.assertIn('CODEX_HOME=/tmp/openrelix-codex-home codex resume {}'.format(thread_id), html)

    def test_window_cards_use_deeplink_for_system_codex_resume(self):
        thread_id = "019dcefe-37f1-7a83-a8a6-720bd6b79d7f"
        html = build_overview.make_window_summary_cards(
            {
                "date": "2026-04-28",
                "windows": [
                    {
                        "window_id": thread_id,
                        "display_index": 1,
                        "project_label": "OpenRelix",
                        "resume_id": thread_id,
                        "resume_command": build_overview.window_resume_command("codex", thread_id),
                        "resume_url": build_overview.codex_resume_url(thread_id),
                        "question_count": 1,
                        "conclusion_count": 1,
                        "question_summary": "问题",
                        "main_takeaway": "结论",
                        "keywords": [],
                        "latest_activity_display": "刚刚",
                        "started_at_display": "刚刚",
                        "recent_prompts": [],
                        "recent_conclusions": [],
                    }
                ],
            }
        )

        self.assertIn("data-window-resume-open", html)
        self.assertIn('data-codex-url="codex://threads/{}"'.format(thread_id), html)
        self.assertIn('data-codex-system-profile="1"', html)
        self.assertIn('data-copy-resume-on-switch=""', html)
        self.assertIn("在 Codex App 打开", html)
        self.assertNotIn("命令已复制", html)

    def test_window_cards_show_claude_app_button_when_desktop_resume_supported(self):
        session_id = "c5ffea1c-8cf8-4dd2-a7ac-bf11f4dfa12b"
        with mock.patch.object(
            build_overview.overview_claude_desktop,
            "claude_desktop_resume_supported",
            return_value=True,
        ):
            html = build_overview.make_window_summary_cards(
                {
                    "date": "2026-04-28",
                    "windows": [
                        {
                            "ai_host": "claude",
                            "window_id": session_id,
                            "display_index": 1,
                            "project_label": "OpenRelix",
                            "resume_id": session_id,
                            "question_count": 1,
                            "conclusion_count": 1,
                            "question_summary": "问题",
                            "main_takeaway": "结论",
                            "keywords": [],
                            "latest_activity_display": "刚刚",
                            "started_at_display": "刚刚",
                            "recent_prompts": [],
                            "recent_conclusions": [],
                        }
                    ],
                }
            )

        self.assertIn("data-window-resume-copy", html)
        self.assertIn('data-resume-command="claude --resume {}"'.format(session_id), html)
        self.assertIn("data-window-resume-claude-desktop", html)
        self.assertIn('data-claude-resume-id="{}"'.format(session_id), html)
        self.assertIn("在 Claude App 打开", html)
        self.assertNotIn("data-codex-url=", html)

    def test_window_cards_hide_claude_app_button_when_desktop_resume_unavailable(self):
        session_id = "c5ffea1c-8cf8-4dd2-a7ac-bf11f4dfa12b"
        with mock.patch.object(
            build_overview.overview_claude_desktop,
            "claude_desktop_resume_supported",
            return_value=False,
        ):
            html = build_overview.make_window_summary_cards(
                {
                    "date": "2026-04-28",
                    "windows": [
                        {
                            "ai_host": "claude",
                            "window_id": session_id,
                            "display_index": 1,
                            "project_label": "OpenRelix",
                            "resume_id": session_id,
                            "question_count": 1,
                            "conclusion_count": 1,
                            "question_summary": "问题",
                            "main_takeaway": "结论",
                            "keywords": [],
                            "latest_activity_display": "刚刚",
                            "started_at_display": "刚刚",
                            "recent_prompts": [],
                            "recent_conclusions": [],
                        }
                    ],
                }
            )

        self.assertIn("data-window-resume-copy", html)
        self.assertIn('data-resume-command="claude --resume {}"'.format(session_id), html)
        self.assertNotIn("data-window-resume-claude-desktop", html)
        self.assertNotIn("在 Claude App 打开", html)

    def test_window_cards_hide_claude_app_button_for_non_uuid_resume_id(self):
        with mock.patch.object(
            build_overview.overview_claude_desktop,
            "claude_desktop_resume_supported",
            return_value=True,
        ):
            html = build_overview.make_window_summary_cards(
                {
                    "date": "2026-04-28",
                    "windows": [
                        {
                            "ai_host": "claude",
                            "window_id": "thread-name",
                            "display_index": 1,
                            "project_label": "OpenRelix",
                            "resume_id": "thread-name",
                            "question_count": 1,
                            "conclusion_count": 1,
                            "question_summary": "问题",
                            "main_takeaway": "结论",
                            "keywords": [],
                            "latest_activity_display": "刚刚",
                            "started_at_display": "刚刚",
                            "recent_prompts": [],
                            "recent_conclusions": [],
                        }
                    ],
                }
            )

        self.assertIn("data-window-resume-copy", html)
        self.assertNotIn("data-window-resume-claude-desktop", html)
        self.assertNotIn("data-claude-resume-id=", html)

    def test_window_markdown_renderer_escapes_unsafe_html(self):
        html = build_overview.render_markdown_text(
            "**加粗** `cmd` foo_bar_baz\n\n- 第一项\n- <script>alert(1)</script>"
        )

        self.assertIn("<strong>加粗</strong>", html)
        self.assertIn("<code>cmd</code>", html)
        self.assertIn("foo_bar_baz", html)
        self.assertNotIn("<em>bar</em>", html)
        self.assertIn("<ul>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_window_overview_display_index_counts_down_from_latest_window(self):
        old_raw_daily_dir = build_overview.RAW_DAILY_DIR
        try:
            with TemporaryDirectory() as tmpdir:
                raw_daily_dir = Path(tmpdir)
                raw_daily_dir.mkdir(parents=True, exist_ok=True)
                build_overview.RAW_DAILY_DIR = raw_daily_dir
                (raw_daily_dir / "2026-04-28.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-28",
                            "window_count": 2,
                            "windows": [
                                {
                                    "window_id": "older",
                                    "cwd": "/tmp/OpenRelix",
                                    "started_at": "2026-04-28T09:00:00+08:00",
                                    "prompt_count": 1,
                                    "conclusion_count": 1,
                                    "prompts": [
                                        {
                                            "local_time": "2026-04-28T09:01:00+08:00",
                                            "text": "旧窗口",
                                        }
                                    ],
                                    "conclusions": [
                                        {
                                            "completed_at": "2026-04-28T09:02:00+08:00",
                                            "text": "旧结论",
                                        }
                                    ],
                                },
                                {
                                    "window_id": "newer",
                                    "cwd": "/tmp/OpenRelix",
                                    "started_at": "2026-04-28T10:00:00+08:00",
                                    "prompt_count": 1,
                                    "conclusion_count": 1,
                                    "prompts": [
                                        {
                                            "local_time": "2026-04-28T10:01:00+08:00",
                                            "text": "新窗口",
                                        }
                                    ],
                                    "conclusions": [
                                        {
                                            "completed_at": "2026-04-28T10:02:00+08:00",
                                            "text": "新结论",
                                        }
                                    ],
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

                overview = build_overview.build_window_overview(
                    None,
                    target_date="2026-04-28",
                )
        finally:
            build_overview.RAW_DAILY_DIR = old_raw_daily_dir

        self.assertEqual(
            [(item["window_id"], item["display_index"]) for item in overview["windows"]],
            [("newer", 2), ("older", 1)],
        )

    def test_context_window_overview_uses_historical_date_summary(self):
        old_raw_daily_dir = build_overview.RAW_DAILY_DIR
        old_consolidated_dir = build_overview.CONSOLIDATED_DIR
        try:
            with TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                raw_daily_dir = root / "raw" / "daily"
                consolidated_dir = root / "consolidated" / "daily"
                raw_daily_dir.mkdir(parents=True, exist_ok=True)
                (consolidated_dir / "2026-05-07").mkdir(parents=True, exist_ok=True)
                build_overview.RAW_DAILY_DIR = raw_daily_dir
                build_overview.CONSOLIDATED_DIR = consolidated_dir

                (raw_daily_dir / "2026-05-11.json").write_text(
                    json.dumps({"date": "2026-05-11", "window_count": 0, "windows": []}),
                    encoding="utf-8",
                )
                (raw_daily_dir / "2026-05-07.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-05-07",
                            "window_count": 1,
                            "windows": [
                                {
                                    "window_id": "w-history",
                                    "cwd": "/tmp/OpenRelix",
                                    "started_at": "2026-05-07T09:00:00+08:00",
                                    "prompt_count": 1,
                                    "conclusion_count": 1,
                                    "prompts": [
                                        {
                                            "local_time": "2026-05-07T09:01:00+08:00",
                                            "text": "raw question with noisy file path",
                                        }
                                    ],
                                    "conclusions": [
                                        {
                                            "completed_at": "2026-05-07T09:02:00+08:00",
                                            "text": "raw conclusion with implementation details",
                                        }
                                    ],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                (consolidated_dir / "2026-05-07" / "summary.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-05-07",
                            "stage": "final",
                            "model_status": "completed",
                            "window_summaries": [
                                {
                                    "window_id": "w-history",
                                    "cwd": "/tmp/OpenRelix",
                                    "window_title": "Historical AI title",
                                    "question_summary": "Historical AI question",
                                    "question_count": 1,
                                    "conclusion_count": 1,
                                    "main_takeaway": "Historical AI takeaway",
                                    "summary_pairs": [
                                        {
                                            "question": "Historical AI pair question",
                                            "conclusion": "Historical AI pair conclusion",
                                        }
                                    ],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

                overview = build_overview.build_context_window_overview_for_days(
                    "2026-05-11",
                    5,
                    latest_nightly={"date": "2026-05-11", "stage": "preliminary", "window_summaries": []},
                )
        finally:
            build_overview.RAW_DAILY_DIR = old_raw_daily_dir
            build_overview.CONSOLIDATED_DIR = old_consolidated_dir

        item = next(window for window in overview["windows"] if window["window_id"] == "w-history")
        self.assertEqual(item["summary_status"], "summarized")
        self.assertEqual(item["window_title"], "Historical AI title")
        self.assertEqual(item["question_summary"], "Historical AI question")
        self.assertEqual(item["main_takeaway"], "Historical AI takeaway")
        self.assertEqual(item["summary_pairs"][0]["question"], "Historical AI pair question")
        self.assertEqual(item["raw_summary_pairs"][0]["question"], "raw question with noisy file path")

    def test_window_overview_uses_history_fallback_when_daily_capture_missing(self):
        history_capture = {
            "source_kind": "history_fallback",
            "date": "2026-05-03",
            "stage": "manual",
            "collection_source": "history",
            "window_count": 1,
            "excluded_window_count": 0,
            "review_like_window_count": 0,
            "windows": [
                {
                    "date": "2026-05-03",
                    "window_id": "w-history",
                    "cwd": "/tmp/OpenRelix",
                    "source": "history",
                    "started_at": "2026-05-03T09:00:00+08:00",
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "prompts": [
                        {
                            "local_time": "2026-05-03T09:01:00+08:00",
                            "text": "首次安装时展示历史窗口",
                        }
                    ],
                    "conclusions": [
                        {
                            "completed_at": "2026-05-03T09:02:00+08:00",
                            "text": "使用 raw fallback 卡片展示。",
                        }
                    ],
                }
            ],
        }

        with mock.patch.object(build_overview, "load_daily_capture", return_value=None), mock.patch.object(
            build_overview,
            "load_history_fallback_daily_capture",
            return_value=history_capture,
        ):
            overview = build_overview.build_window_overview(None, target_date="2026-05-03")

        self.assertEqual(overview["source_kind"], "history_fallback")
        self.assertEqual(overview["window_count"], 1)
        self.assertEqual(overview["windows"][0]["window_id"], "w-history")
        self.assertEqual(overview["windows"][0]["summary_status"], "raw_fallback")
        self.assertIn("Codex CLI history/session", overview["windows"][0]["activity_source_label"])
        self.assertIn("首次安装时展示历史窗口", overview["windows"][0]["question_summary"])

    def test_window_overview_views_include_codex_history_dates(self):
        history_capture = {
            "source_kind": "history_fallback",
            "date": "2026-05-03",
            "stage": "manual",
            "collection_source": "history",
            "window_count": 1,
            "excluded_window_count": 0,
            "review_like_window_count": 0,
            "windows": [
                {
                    "date": "2026-05-03",
                    "window_id": "w-history",
                    "cwd": "/tmp/OpenRelix",
                    "source": "history",
                    "started_at": "2026-05-03T09:00:00+08:00",
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "prompts": [{"local_time": "2026-05-03T09:01:00+08:00", "text": "历史窗口"}],
                    "conclusions": [{"completed_at": "2026-05-03T09:02:00+08:00", "text": "历史结论"}],
                }
            ],
        }

        with mock.patch.object(build_overview, "list_daily_capture_dates", return_value=[]), mock.patch.object(
            build_overview,
            "list_codex_history_dates",
            return_value=["2026-05-03"],
        ), mock.patch.object(build_overview, "load_daily_capture", return_value=None), mock.patch.object(
            build_overview,
            "load_history_fallback_daily_capture",
            return_value=history_capture,
        ):
            views = build_overview.build_window_overview_views([], selected_date="2026-05-03")

        self.assertEqual([view["date"] for view in views], ["2026-05-03"])
        self.assertIn("历史窗口", views[0]["cards_html"])

    def test_english_window_cards_localize_source_and_chinese_summaries(self):
        html = build_overview.make_window_summary_cards(
            {
                "date": "2026-04-28",
                "windows": [
                    {
                        "window_id": "w2",
                        "display_index": 2,
                        "cwd": "/tmp/OpenRelix",
                        "cwd_display": "OpenRelix",
                        "project_label": "OpenRelix",
                        "activity_source": "app-server",
                        "thread_source": "cli",
                        "activity_source_label": "采集：Codex app-server · 线程来源：cli",
                        "question_count": 1,
                        "conclusion_count": 1,
                        "question_summary": "窗口编号应该倒序",
                        "main_takeaway": "英文卡片不应混入中文来源",
                        "keywords": ["窗口"],
                        "latest_activity_display": "04-28 16:48",
                        "started_at_display": "04-28 16:00",
                        "recent_prompts": [{"time": "04-28 16:01", "text": "窗口编号应该倒序"}],
                        "recent_conclusions": [{"time": "04-28 16:02", "text": "英文卡片不应混入中文来源"}],
                    }
                ],
            },
            language="en",
        )

        self.assertIn("OpenRelix · Codex · Raw Window ID: w2", html)
        self.assertNotIn("OpenRelix · Window 2", html)
        self.assertIn("Collection: Codex app-server · thread source: cli", html)
        self.assertIn("Window.", html)
        self.assertIn(">Window<", html)
        self.assertIn("Question / Conclusion", html)
        self.assertIn("AI-organized", html)
        self.assertNotIn("大模型已做智能整理", html)
        self.assertNotIn("暂未做二次学习", html)
        self.assertNotIn("Question Summary", html)
        self.assertNotIn("Conclusion Summary", html)
        self.assertNotIn("Recent Questions", html)
        self.assertNotIn("Recent Conclusions", html)
        self.assertNotIn("Show Recent Questions", html)
        self.assertNotIn("Show Recent Conclusions", html)
        self.assertNotIn("采集：", html)

    def test_backfill_dates_parser_accepts_non_contiguous_dates(self):
        args = argparse.Namespace(
            dates="2026-04-24,2026-04-21 2026-04-23",
            date_from=None,
            date_to="2026-04-27",
            days=0,
        )

        self.assertEqual(
            openrelix.resolve_backfill_dates(args),
            ["2026-04-21", "2026-04-23", "2026-04-24"],
        )

    def test_openrelix_mode_updates_runtime_config_without_reinstalling(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir(parents=True)
            codex_home = root / "codex"
            paths = replace(openrelix.PATHS, state_root=root, runtime_dir=runtime_dir, codex_home=codex_home)
            args = argparse.Namespace(memory_mode="local-only", no_refresh=True, json=True)

            with mock.patch.object(openrelix, "PATHS", paths), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                openrelix.command_mode(args)

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

    def test_memory_migration_ensure_marks_existing_state_pending(self):
        with TemporaryDirectory() as tmpdir:
            paths = make_runtime_paths_for_test(Path(tmpdir) / "state")
            asset_runtime.write_runtime_config(memory_mode="integrated", paths=paths)
            paths.registry_dir.mkdir(parents=True)
            (paths.registry_dir / "memory_items.jsonl").write_text(
                '{"title":"old memory"}\n',
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {"AI_ASSET_MEMORY_MODE": ""}, clear=False):
                state = openrelix_memory_migration.ensure_memory_migration_state(paths)

            self.assertEqual(state["status"], "pending")
            self.assertEqual(state["reason"], "algorithm_version_changed")
            self.assertEqual(state["previous_algorithm_version"], 0)
            config = asset_runtime.load_runtime_config(paths)
            self.assertEqual(int(config.get("personal_memory_algorithm_version") or 0), 0)

    def test_memory_migration_ensure_marks_fresh_state_current(self):
        with TemporaryDirectory() as tmpdir:
            paths = make_runtime_paths_for_test(Path(tmpdir) / "state")
            asset_runtime.write_runtime_config(memory_mode="integrated", paths=paths)

            with mock.patch.dict(os.environ, {"AI_ASSET_MEMORY_MODE": ""}, clear=False):
                state = openrelix_memory_migration.ensure_memory_migration_state(paths)

            self.assertEqual(state["status"], "skipped")
            self.assertEqual(state["reason"], "no_existing_personal_memory_state")
            config = asset_runtime.load_runtime_config(paths)
            self.assertEqual(
                config["personal_memory_algorithm_version"],
                openrelix_memory_migration.PERSONAL_MEMORY_ALGORITHM_VERSION,
            )

    def test_memory_migration_run_forces_recent_backfill_and_marks_complete(self):
        with TemporaryDirectory() as tmpdir:
            paths = make_runtime_paths_for_test(Path(tmpdir) / "state")
            asset_runtime.write_runtime_config(memory_mode="integrated", paths=paths)
            paths.registry_dir.mkdir(parents=True)
            (paths.registry_dir / "memory_items.jsonl").write_text(
                '{"title":"old memory"}\n',
                encoding="utf-8",
            )
            args = argparse.Namespace(
                action="run",
                window_days=3,
                force=False,
                if_pending=False,
                quiet=True,
                json=False,
            )

            with mock.patch.dict(os.environ, {"AI_ASSET_MEMORY_MODE": ""}, clear=False), mock.patch.object(
                openrelix,
                "PATHS",
                paths,
            ), mock.patch.object(
                openrelix,
                "run_backfill_dates",
                return_value=[
                    {
                        "date": "2026-05-04",
                        "status": "completed",
                        "requested_stage": "final",
                    }
                ],
            ) as run_backfill, mock.patch.object(openrelix, "sync_review_outputs") as sync_outputs:
                openrelix.command_memory_migration(args)

            run_backfill.assert_called_once()
            self.assertEqual(run_backfill.call_args.args[1], "final")
            self.assertEqual(run_backfill.call_args.kwargs["learn_window_days"], 3)
            self.assertTrue(run_backfill.call_args.kwargs["force"])
            sync_outputs.assert_called_once_with(
                include_index=True,
                include_native_display=True,
                verbose=False,
            )
            state = openrelix_memory_migration.load_memory_migration_state(paths)
            self.assertEqual(state["status"], "completed")
            config = asset_runtime.load_runtime_config(paths)
            self.assertEqual(
                config["personal_memory_algorithm_version"],
                openrelix_memory_migration.PERSONAL_MEMORY_ALGORITHM_VERSION,
            )

    def test_memory_migration_moves_legacy_rows_and_drops_lightweight_rows(self):
        with TemporaryDirectory() as tmpdir:
            paths = make_runtime_paths_for_test(Path(tmpdir) / "state")
            paths.registry_dir.mkdir(parents=True)
            legacy_path = paths.registry_dir / "memory_items.jsonl"
            legacy_path.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in [
                        {
                            "date": "2026-05-05",
                            "source": "nightly_codex",
                            "bucket": "durable",
                            "title": "Useful final rule",
                            "memory_type": "procedural",
                            "priority": "high",
                            "value_note": "Keep this final reusable rule.",
                        },
                        {
                            "date": "2026-05-05",
                            "source": "nightly_codex",
                            "bucket": "session",
                            "title": "Lightweight later review placeholder",
                            "stage": "preliminary",
                            "summary_generation": "lightweight",
                            "value_note": "Old quick-pass memory should be dropped.",
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            stats = openrelix_memory_migration.migrate_personal_memory_registry(paths)
            canonical_rows = [
                json.loads(line)
                for line in (paths.registry_dir / "memory_entries.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(stats["migrated_rows"], 1)
            self.assertEqual(stats["dropped_lightweight_rows"], 1)
            self.assertEqual([row["title"] for row in canonical_rows], ["Useful final rule"])
            self.assertEqual(canonical_rows[0]["memory_algorithm_version"], openrelix_memory_migration.PERSONAL_MEMORY_ALGORITHM_VERSION)

    def test_learning_context_includes_memory_feedback_examples(self):
        old_paths = nightly_consolidate.PATHS
        old_registry_dir = nightly_consolidate.REGISTRY_DIR
        try:
            with TemporaryDirectory() as tmpdir:
                paths = make_runtime_paths_for_test(Path(tmpdir) / "state")
                asset_runtime.ensure_state_layout(paths)
                nightly_consolidate.PATHS = paths
                nightly_consolidate.REGISTRY_DIR = paths.registry_dir
                liked_row = {
                    "date": "2026-05-05",
                    "source": "canonical",
                    "bucket": "durable",
                    "title": "Preferred reusable workflow",
                    "memory_type": "procedural",
                    "priority": "high",
                    "value_note": "This is the level of reusable workflow memory the user liked.",
                    "keywords": ["workflow"],
                }
                downvoted_row = {
                    "date": "2026-05-05",
                    "source": "canonical",
                    "bucket": "session",
                    "title": "One-off noisy note",
                    "memory_type": "task",
                    "priority": "medium",
                    "value_note": "This is too one-off and should guide future filtering.",
                    "keywords": ["noise"],
                }
                (paths.registry_dir / "memory_entries.jsonl").write_text(
                    json.dumps(liked_row, ensure_ascii=False)
                    + "\n"
                    + json.dumps(downvoted_row, ensure_ascii=False)
                    + "\n",
                    encoding="utf-8",
                )
                overview_memory_feedback.append_memory_feedback(
                    paths,
                    overview_memory_feedback.memory_key_for_record(liked_row),
                    "liked",
                    title=liked_row["title"],
                )
                overview_memory_feedback.append_memory_feedback(
                    paths,
                    overview_memory_feedback.memory_key_for_record(downvoted_row),
                    "downvoted",
                    title=downvoted_row["title"],
                )

                context = nightly_consolidate.build_learning_context(
                    "2026-05-06",
                    existing_summary={},
                    learn_window_days=0,
                )
                prompt = nightly_consolidate.build_prompt_with_learning(
                    {
                        "date": "2026-05-06",
                        "window_count": 0,
                        "prompt_count": 0,
                        "conclusion_count": 0,
                        "windows": [],
                    },
                    context,
                    language="zh",
                )

            examples = context["memory_feedback_examples"]
            self.assertEqual(examples["liked_examples"][0]["title"], "Preferred reusable workflow")
            self.assertEqual(examples["downvoted_examples"][0]["title"], "One-off noisy note")
            self.assertIn("memory_feedback_examples", prompt)
            self.assertIn("有用", prompt)
            self.assertIn("无用", prompt)
        finally:
            nightly_consolidate.PATHS = old_paths
            nightly_consolidate.REGISTRY_DIR = old_registry_dir

    def test_openrelix_config_updates_memory_summary_max_tokens(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir(parents=True)
            paths = replace(openrelix.PATHS, state_root=root, runtime_dir=runtime_dir)
            args = argparse.Namespace(
                memory_summary_max_tokens=8000,
                activity_source=None,
                codex_model=None,
                read_codex_app=False,
                no_refresh=True,
                json=True,
            )

            with mock.patch.object(openrelix, "PATHS", paths), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                openrelix.command_config(args)

            config = json.loads((runtime_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["memory_summary_max_tokens"], 8000)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["memory_summary_max_tokens"], 8000)
            self.assertEqual(payload["global_memory_budget_tokens"], 800)
            self.assertEqual(payload["project_memory_budget_tokens"], 2400)
            self.assertEqual(payload["personal_memory_budget_tokens"], 3200)
            self.assertFalse(payload["refreshed"])

    def test_openrelix_config_updates_activity_source(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir(parents=True)
            paths = replace(openrelix.PATHS, state_root=root, runtime_dir=runtime_dir)
            args = argparse.Namespace(
                memory_summary_max_tokens=None,
                activity_source=None,
                codex_model=None,
                read_codex_app=True,
                no_refresh=True,
                json=True,
            )

            with mock.patch.object(openrelix, "PATHS", paths), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                openrelix.command_config(args)

            config = json.loads((runtime_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["activity_source"], "auto")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["activity_source"], "auto")
            self.assertFalse(payload["refreshed"])

    def test_openrelix_config_updates_codex_model(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir(parents=True)
            paths = replace(openrelix.PATHS, state_root=root, runtime_dir=runtime_dir)
            args = argparse.Namespace(
                memory_summary_max_tokens=None,
                activity_source=None,
                codex_model="gpt5.4mini",
                read_codex_app=False,
                no_refresh=True,
                json=True,
            )

            with mock.patch.dict(
                os.environ,
                {"OPENRELIX_CODEX_MODEL": "", "AI_ASSET_CODEX_MODEL": ""},
                clear=False,
            ), mock.patch.object(openrelix, "PATHS", paths), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                openrelix.command_config(args)

            config = json.loads((runtime_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["codex_model"], "gpt-5.4-mini")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["codex_model"], "gpt-5.4-mini")
            self.assertEqual(payload["configured_codex_model"], "gpt-5.4-mini")
            self.assertFalse(payload["refreshed"])

    def test_openrelix_config_updates_claude_host_and_model_cli(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir(parents=True)
            paths = replace(openrelix.PATHS, state_root=root, runtime_dir=runtime_dir)
            args = argparse.Namespace(
                memory_summary_max_tokens=None,
                activity_source=None,
                activity_host="cc",
                model_cli="cc",
                codex_model=None,
                claude_model="opus",
                claude_settings='{"env":{"OPENRELIX_PROVIDER":"bridge"}}',
                claude_env_file=str(root / "claude.env"),
                read_codex_app=False,
                no_refresh=True,
                json=True,
            )

            with mock.patch.dict(
                os.environ,
                {
                    "OPENRELIX_ACTIVITY_HOST": "",
                    "AI_ASSET_ACTIVITY_HOST": "",
                    "OPENRELIX_MODEL_CLI": "",
                    "AI_ASSET_MODEL_CLI": "",
                    "OPENRELIX_CLAUDE_MODEL": "",
                    "AI_ASSET_CLAUDE_MODEL": "",
                    "OPENRELIX_CLAUDE_SETTINGS": "",
                    "AI_ASSET_CLAUDE_SETTINGS": "",
                    "OPENRELIX_CLAUDE_ENV_FILE": "",
                    "AI_ASSET_CLAUDE_ENV_FILE": "",
                },
                clear=False,
            ), mock.patch.object(openrelix, "PATHS", paths), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                openrelix.command_config(args)

            config = json.loads((runtime_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["activity_host"], "claude")
            self.assertEqual(config["model_cli"], "claude")
            self.assertEqual(config["claude_model"], "opus")
            self.assertEqual(config["claude_settings"], '{"env":{"OPENRELIX_PROVIDER":"bridge"}}')
            self.assertEqual(config["claude_env_file"], str((root / "claude.env").resolve()))
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["activity_host"], "claude")
            self.assertEqual(payload["model_cli"], "claude")
            self.assertEqual(payload["claude_model"], "opus")
            self.assertEqual(payload["claude_settings"], '{"env":{"OPENRELIX_PROVIDER":"bridge"}}')
            self.assertEqual(payload["claude_env_file"], str((root / "claude.env").resolve()))
            self.assertFalse(payload["refreshed"])

    def test_openrelix_open_panel_ensures_token_live_service(self):
        args = argparse.Namespace(target="panel", date="2026-04-29")
        calls = []

        with mock.patch.object(openrelix, "REPORTS_DIR", Path("/tmp/openrelix-reports")), mock.patch.object(
            openrelix,
            "ensure_token_live_service",
            side_effect=lambda: calls.append("ensure"),
        ), mock.patch.object(
            openrelix,
            "open_path",
            side_effect=lambda path: calls.append(("open", path)),
        ), mock.patch(
            "sys.stdout",
            new_callable=io.StringIO,
        ):
            openrelix.command_open(args)

        self.assertEqual(
            calls,
            [
                "ensure",
                ("open", Path("/tmp/openrelix-reports") / "panel.html"),
            ],
        )

    def test_openrelix_app_ensures_token_live_service_before_opening(self):
        with TemporaryDirectory() as tmpdir:
            app_path = Path(tmpdir) / "OpenRelix.app"
            app_path.mkdir()
            args = argparse.Namespace(
                output=str(app_path),
                build=False,
                no_open=False,
                print_path=False,
            )
            calls = []

            with mock.patch.object(openrelix.sys, "platform", "darwin"), mock.patch.object(
                openrelix,
                "ensure_token_live_service",
                side_effect=lambda: calls.append("ensure"),
            ), mock.patch.object(
                openrelix,
                "open_path",
                side_effect=lambda path: calls.append(("open", path)),
            ), mock.patch(
                "sys.stdout",
                new_callable=io.StringIO,
            ):
                openrelix.command_app(args)

        self.assertEqual(calls, ["ensure", ("open", app_path.resolve())])

    def test_token_live_health_requires_current_version_and_script_path(self):
        expected_script = (openrelix.REPO_ROOT / "scripts" / "token_live_server.py").resolve()

        self.assertTrue(
            openrelix.token_live_health_matches_current(
                {
                    "ok": True,
                    "service": "token-live",
                    "version": openrelix.read_local_package_version(),
                    "script_path": str(expected_script),
                }
            )
        )
        self.assertFalse(openrelix.token_live_health_matches_current({"ok": True, "service": "token-live"}))
        self.assertFalse(
            openrelix.token_live_health_matches_current(
                {
                    "ok": True,
                    "service": "token-live",
                    "version": "0.0.1",
                    "script_path": str(expected_script),
                }
            )
        )
        self.assertFalse(
            openrelix.token_live_health_matches_current(
                {
                    "ok": True,
                    "service": "token-live",
                    "version": openrelix.read_local_package_version(),
                    "script_path": "/tmp/old-openrelix/scripts/token_live_server.py",
                }
            )
        )

    def test_token_live_health_endpoint_reports_version_and_script_path(self):
        server = token_live_server.ThreadingHTTPServer(("127.0.0.1", 0), token_live_server.TokenLiveHandler)
        thread = token_live_server.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            conn.request("GET", "/healthz")
            response = conn.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            conn.close()
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["service"], "token-live")
        self.assertEqual(payload["version"], token_live_server.SERVICE_VERSION)
        self.assertEqual(payload["repo_root"], str(token_live_server.PATHS.repo_root))
        self.assertEqual(payload["script_path"], token_live_server.SERVICE_SCRIPT_PATH)

    def test_skill_quarantine_delete_post_is_accepted_by_http_handler(self):
        server = token_live_server.ThreadingHTTPServer(("127.0.0.1", 0), token_live_server.TokenLiveHandler)
        thread = token_live_server.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        body = json.dumps({"action": "delete", "entity_key": "skill:delete-me"}).encode("utf-8")
        try:
            with mock.patch.object(token_live_server, "get_update_token", return_value="test-token"), mock.patch.object(
                token_live_server,
                "read_view_cache",
                return_value={"quarantined": [{"entity_key": "skill:delete-me"}]},
            ), mock.patch.object(
                token_live_server,
                "start_skill_quarantine_action_async",
                return_value=(True, {"ok": True, "status": "queued", "started_now": True}),
            ) as start_action:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
                conn.request(
                    "POST",
                    token_live_server.SKILL_QUARANTINE_PATH,
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-OpenRelix-Token": "test-token",
                    },
                )
                response = conn.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                conn.close()
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(response.status, 202)
        self.assertEqual(payload["action"], "delete")
        self.assertEqual(payload["status"], "accepted")
        self.assertEqual(payload["task"]["status"], "queued")
        start_action.assert_called_once()

    def test_ensure_token_live_service_bootstraps_when_health_check_fails(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = make_runtime_paths_for_test(root)
            plist_path = root / "LaunchAgents" / openrelix.TOKEN_LIVE_PLIST_NAME

            with mock.patch.object(openrelix, "PATHS", paths), mock.patch.object(
                openrelix.sys,
                "platform",
                "darwin",
            ), mock.patch.object(
                openrelix.shutil,
                "which",
                return_value="/bin/launchctl",
            ), mock.patch.object(
                openrelix,
                "token_live_health_ok",
                side_effect=[False, True],
            ), mock.patch.object(
                openrelix,
                "render_token_live_launch_agent",
                return_value=plist_path,
            ) as render, mock.patch.object(
                openrelix,
                "bootstrap_token_live_launch_agent",
            ) as bootstrap:
                self.assertTrue(openrelix.ensure_token_live_service(verbose=False))

            render.assert_called_once_with()
            bootstrap.assert_called_once_with(plist_path)

    def test_stop_stale_token_live_processes_targets_only_openrelix_server(self):
        ps_output = "\n".join(
            [
                " 123 /usr/bin/python3 /opt/homebrew/lib/node_modules/openrelix/scripts/token_live_server.py",
                " 124 /usr/bin/python3 /tmp/other/token_live_server.py",
                " 125 /usr/bin/python3 /opt/homebrew/lib/node_modules/openrelix/scripts/openrelix.py",
            ]
        )
        calls = []

        def fake_runner(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=ps_output, stderr="")

        def fake_kill(pid, signal_number):
            calls.append((pid, signal_number))

        stopped = openrelix.stop_stale_token_live_processes(
            ps_runner=fake_runner,
            kill_func=fake_kill,
            sleep_func=lambda _seconds: None,
            alive_func=lambda _pid: False,
        )

        self.assertEqual(stopped, [123])
        self.assertEqual(calls, [(123, signal.SIGTERM)])

    def test_token_live_bootstrap_stops_stale_process_before_starting(self):
        events = []

        def fake_run(cmd, **kwargs):
            events.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def fake_stop():
            events.append("stop-stale-token-live")
            return [123]

        with mock.patch.object(openrelix.os, "getuid", return_value=501), mock.patch.object(
            openrelix.subprocess,
            "run",
            side_effect=fake_run,
        ), mock.patch.object(
            openrelix,
            "stop_stale_token_live_processes",
            side_effect=fake_stop,
        ):
            openrelix.bootstrap_token_live_launch_agent(Path("/tmp/token-live.plist"))

        self.assertEqual(events[2], "stop-stale-token-live")
        self.assertEqual(events[3][:3], ["launchctl", "bootstrap", "gui/501"])

    def test_claude_desktop_resume_command_uses_settings_without_forcing_model(self):
        session_id = "c5ffea1c-8cf8-4dd2-a7ac-bf11f4dfa12b"
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = make_runtime_paths_for_test(root)
            claude_bin = root / "bin" / "claude"
            claude_bin.parent.mkdir(parents=True)
            claude_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            claude_bin.chmod(0o755)

            with mock.patch.object(
                claude_desktop,
                "get_claude_settings",
                return_value='{"env":{"OPENRELIX_PROVIDER":"bridge"}}',
            ):
                command = claude_desktop.build_claude_desktop_resume_command(session_id, paths=paths)

        self.assertEqual(command[:3], [str(claude_bin), "--resume", session_id])
        self.assertIn("--settings", command)
        self.assertIn('{"env":{"OPENRELIX_PROVIDER":"bridge"}}', command)
        self.assertNotIn("--model", command)

    def test_claude_desktop_resume_start_rejects_invalid_or_missing_requirements(self):
        session_id = "c5ffea1c-8cf8-4dd2-a7ac-bf11f4dfa12b"
        with TemporaryDirectory() as tmpdir:
            paths = make_runtime_paths_for_test(Path(tmpdir))

            invalid = claude_desktop.start_claude_desktop_resume("not-a-uuid", paths=paths)
            self.assertFalse(invalid["ok"])
            self.assertEqual(invalid["error"], "invalid_resume_id")

            with mock.patch.object(
                claude_desktop,
                "claude_desktop_app_installed",
                return_value=False,
            ):
                missing_app = claude_desktop.start_claude_desktop_resume(session_id, paths=paths)

        self.assertFalse(missing_app["ok"])
        self.assertEqual(missing_app["error"], "claude_desktop_app_not_found")

    def test_claude_desktop_resume_start_launches_background_worker(self):
        session_id = "c5ffea1c-8cf8-4dd2-a7ac-bf11f4dfa12b"
        with TemporaryDirectory() as tmpdir:
            paths = make_runtime_paths_for_test(Path(tmpdir))
            with mock.patch.object(
                claude_desktop,
                "claude_desktop_app_installed",
                return_value=True,
            ), mock.patch.object(
                claude_desktop,
                "resolve_claude_cli_binary",
                return_value="/opt/homebrew/bin/claude",
            ), mock.patch.object(
                claude_desktop,
                "build_claude_desktop_resume_command",
                return_value=["/opt/homebrew/bin/claude", "--resume", session_id],
            ), mock.patch.object(
                claude_desktop,
                "build_claude_desktop_resume_env",
                return_value={},
            ), mock.patch.object(
                claude_desktop.threading,
                "Thread",
            ) as thread_cls:
                thread = mock.Mock()
                thread_cls.return_value = thread
                result = claude_desktop.start_claude_desktop_resume(session_id, paths=paths)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "starting")
        thread_cls.assert_called_once()
        thread.start.assert_called_once()

    def test_codex_desktop_resume_rejects_unknown_non_primary_home(self):
        thread_id = "019dcefe-37f1-7a83-a8a6-720bd6b79d7f"
        paths = argparse.Namespace(codex_home="/tmp/primary-codex-home")

        with mock.patch.object(overview_codex_desktop.codex_profiles, "find_profile_for_home", return_value=None):
            result = overview_codex_desktop.start_codex_desktop_resume(
                thread_id,
                codex_home="/tmp/other-codex-home",
                paths=paths,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "codex_desktop_profile_unknown")

    def test_codex_desktop_resume_launches_isolated_profile_without_thread_url(self):
        thread_id = "019dcefe-37f1-7a83-a8a6-720bd6b79d7f"
        with TemporaryDirectory() as tmpdir:
            app_binary = Path(tmpdir) / "Codex"
            app_binary.write_text("#!/bin/sh\n", encoding="utf-8")
            process = mock.Mock(pid=4321)
            with mock.patch.object(overview_codex_desktop.subprocess, "Popen", return_value=process) as popen:
                result = overview_codex_desktop.start_codex_desktop_resume(
                    thread_id,
                    codex_home="/tmp/other-codex-home",
                    electron_user_data_path="/tmp/Codex Profile",
                    app_binary=app_binary,
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["pid"], 4321)
        self.assertEqual(result["thread_navigation"], "profile_launch_only")
        self.assertFalse(result["exact_thread_navigation"])
        command = popen.call_args.args[0]
        env = popen.call_args.kwargs["env"]
        self.assertEqual(command, [str(app_binary)])
        self.assertEqual(env["CODEX_HOME"], "/tmp/other-codex-home")
        self.assertEqual(env["CODEX_ELECTRON_USER_DATA_PATH"], "/tmp/Codex Profile")

    def test_codex_desktop_resume_opens_system_profile_deeplink(self):
        thread_id = "019dcefe-37f1-7a83-a8a6-720bd6b79d7f"
        process = mock.Mock(pid=9876)
        with mock.patch.object(overview_codex_desktop.subprocess, "Popen", return_value=process) as popen:
            result = overview_codex_desktop.start_codex_desktop_resume(thread_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["pid"], 9876)
        self.assertEqual(result["thread_navigation"], "deeplink_open")
        self.assertTrue(result["exact_thread_navigation"])
        self.assertFalse(result["used_profile"])
        self.assertEqual(popen.call_args.args[0], ["open", "codex://threads/{}".format(thread_id)])

    def test_codex_desktop_resume_focuses_running_profile_without_opening_url(self):
        thread_id = "019dcefe-37f1-7a83-a8a6-720bd6b79d7f"
        paths = argparse.Namespace(codex_home="/tmp/primary-codex-home")
        profile = overview_codex_desktop.codex_profiles.CodexProfile(
            codex_home=Path("/tmp/other-codex-home"),
            electron_user_data_path="/tmp/Codex Profile",
            source="running",
            process_id=2468,
        )
        with mock.patch.object(
            overview_codex_desktop.codex_profiles,
            "find_profile_for_home",
            return_value=profile,
        ), mock.patch.object(
            overview_codex_desktop,
            "focus_codex_process",
            return_value=True,
        ) as focus, mock.patch.object(
            overview_codex_desktop.subprocess,
            "Popen",
        ) as popen:
            result = overview_codex_desktop.start_codex_desktop_resume(
                thread_id,
                codex_home="/tmp/other-codex-home",
                paths=paths,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "focused")
        self.assertEqual(result["thread_navigation"], "profile_focus_only")
        self.assertFalse(result["exact_thread_navigation"])
        self.assertTrue(result["reused_running_profile"])
        self.assertEqual(result["target_process_id"], 2468)
        focus.assert_called_once_with(2468)
        popen.assert_not_called()

    def test_finder_reveal_uses_macos_open_R_for_existing_path(self):
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "SKILL.md"
            target.write_text("# skill\n", encoding="utf-8")
            process = mock.Mock()
            with mock.patch.object(overview_finder.sys, "platform", "darwin"), mock.patch.object(
                overview_finder.subprocess,
                "Popen",
                return_value=process,
            ) as popen:
                result = overview_finder.reveal_path_in_finder(str(target))

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "opening")
        command = popen.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/open", "-R"])
        self.assertEqual(command[2], str(target.absolute()))

    def test_finder_reveal_preserves_symlink_entry_path(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "real-skill"
            target.mkdir()
            link = root / "isolated-skill"
            link.symlink_to(target, target_is_directory=True)
            process = mock.Mock()
            with mock.patch.object(overview_finder.sys, "platform", "darwin"), mock.patch.object(
                overview_finder.subprocess,
                "Popen",
                return_value=process,
            ) as popen:
                result = overview_finder.reveal_path_in_finder(str(link))

        self.assertTrue(result["ok"])
        command = popen.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/open", "-R"])
        self.assertEqual(command[2], str(link.absolute()))

    def test_finder_reveal_rejects_missing_or_relative_path(self):
        with TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing" / "SKILL.md"
            relative = "skills/foo/SKILL.md"

            self.assertEqual(overview_finder.reveal_path_in_finder(str(missing))["error"], "path_not_found")
            self.assertEqual(overview_finder.reveal_path_in_finder(relative)["error"], "path_not_found")

    def test_token_live_trusts_local_panel_post_endpoints(self):
        self.assertIn(overview_codex_desktop.CODEX_DESKTOP_OPEN_PATH, token_live_server.TRUSTED_POST_PATHS)
        self.assertIn(overview_finder.FINDER_REVEAL_PATH, token_live_server.TRUSTED_POST_PATHS)
        self.assertIn(token_live_server.PANEL_REFRESH_PATH, token_live_server.TRUSTED_POST_PATHS)
        self.assertIn(token_live_server.MEMORY_FEEDBACK_PATH, token_live_server.TRUSTED_POST_PATHS)

    def test_token_live_allows_no_origin_preflight_for_trusted_local_posts(self):
        server = token_live_server.ThreadingHTTPServer(("127.0.0.1", 0), token_live_server.TokenLiveHandler)
        thread = token_live_server.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            connection.request(
                "OPTIONS",
                token_live_server.MEMORY_FEEDBACK_PATH,
                headers={
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type,x-openrelix-token",
                },
            )
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(response.status, 200, body)
        self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "*")
        self.assertEqual(response.getheader("Access-Control-Allow-Private-Network"), "true")

    def test_token_live_rejects_untrusted_preflight_origin(self):
        server = token_live_server.ThreadingHTTPServer(("127.0.0.1", 0), token_live_server.TokenLiveHandler)
        thread = token_live_server.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            connection.request(
                "OPTIONS",
                token_live_server.MEMORY_FEEDBACK_PATH,
                headers={
                    "Origin": "https://example.com",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type,x-openrelix-token",
                },
            )
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(response.status, 403, body)
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))

    def test_window_search_endpoint_uses_readonly_index_and_requires_panel_token(self):
        with TemporaryDirectory() as tmpdir:
            paths = make_runtime_paths_for_test(tmpdir)
            status_payload = {
                "ok": True,
                "exists": True,
                "stale": True,
                "fts_enabled": True,
                "window_rows": 12,
                "rebuilt_at": "2026-05-20T10:00:00+08:00",
            }
            result_rows = [
                {
                    "window_id": "w-sqlite",
                    "date": "2026-05-20",
                    "cwd": "/tmp/openrelix",
                    "project_label": "openrelix",
                    "source": "history",
                    "originator": "codex_cli",
                    "started_at": "2026-05-20T09:50:00+08:00",
                    "latest_activity_at": "2026-05-20T10:00:00+08:00",
                    "session_file": "/tmp/private-session.jsonl",
                    "raw_path": "/tmp/private-window.json",
                    "prompt_count": 2,
                    "conclusion_count": 1,
                    "window_title": "SQLite window search",
                    "question_summary": "Should the panel search historical windows?",
                    "main_takeaway": "Use readonly SQLite queries from the local service.",
                    "summary_status": "summarized",
                    "summary_pairs": [
                        {
                            "question": "Should the panel search historical windows?",
                            "conclusion": "Use readonly SQLite queries from the local service.",
                        }
                    ],
                    "raw_summary_pairs": [
                        {
                            "question": "原始问题",
                            "conclusion": "原始结论",
                        }
                    ],
                    "matched_messages": [
                        {
                            "kind": "prompt",
                            "ordinal": 0,
                            "event_time": "2026-05-20T09:50:00+08:00",
                            "text": "原始问题 sqlite",
                        }
                    ],
                    "keywords": ["sqlite", "window"],
                }
            ]
            server = token_live_server.ThreadingHTTPServer(("127.0.0.1", 0), token_live_server.TokenLiveHandler)
            thread = token_live_server.threading.Thread(target=server.serve_forever, daemon=True)
            with mock.patch.object(token_live_server, "PATHS", paths), mock.patch.object(
                token_live_server,
                "get_update_token",
                return_value="secret",
            ), mock.patch.object(
                token_live_server.openrelix_index,
                "index_status",
                return_value=status_payload,
            ), mock.patch.object(
                token_live_server.openrelix_index,
                "search_windows",
                return_value=result_rows,
            ) as search:
                thread.start()
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                    connection.request(
                        "GET",
                        token_live_server.WINDOW_SEARCH_PATH
                        + "?q=sqlite&limit=5&scope=id&project=openrelix&date_from=2026-05-19&date_to=2026-05-20",
                        headers={"Origin": "null", "X-OpenRelix-Token": "secret"},
                    )
                    response = connection.getresponse()
                    body = response.read().decode("utf-8")
                    connection.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

            self.assertEqual(response.status, 200, body)
            self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "null")
            payload = json.loads(body)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["readonly"])
            self.assertTrue(payload["index"]["stale"])
            self.assertEqual(payload["scope"], "id")
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["results"][0]["window_id"], "w-sqlite")
            self.assertEqual(payload["results"][0]["raw_summary_pairs"][0]["question"], "原始问题")
            self.assertEqual(payload["results"][0]["matched_messages"][0]["text"], "原始问题 sqlite")
            self.assertNotIn("raw_path", payload["results"][0])
            self.assertNotIn("session_file", payload["results"][0])
            search.assert_called_once_with(
                "sqlite",
                project="openrelix",
                date_from="2026-05-19",
                date_to="2026-05-20",
                search_scope="id",
                include_review_related=False,
                limit=5,
                paths=paths,
                rebuild=False,
            )

    def test_window_search_limit_defaults_to_100_and_clamps(self):
        self.assertEqual(token_live_server.WINDOW_SEARCH_DEFAULT_LIMIT, 100)
        self.assertEqual(token_live_server.WINDOW_SEARCH_MAX_LIMIT, 100)
        self.assertEqual(token_live_server.normalize_window_search_limit(None), 100)
        self.assertEqual(token_live_server.normalize_window_search_limit(""), 100)
        self.assertEqual(token_live_server.normalize_window_search_limit("500"), 100)
        self.assertEqual(token_live_server.normalize_window_search_limit("0"), 1)
        self.assertEqual(token_live_server.normalize_window_search_limit("42"), 42)

    def test_window_search_endpoint_rejects_untrusted_origin_and_missing_token(self):
        with TemporaryDirectory() as tmpdir:
            paths = make_runtime_paths_for_test(tmpdir)
            server = token_live_server.ThreadingHTTPServer(("127.0.0.1", 0), token_live_server.TokenLiveHandler)
            thread = token_live_server.threading.Thread(target=server.serve_forever, daemon=True)
            with mock.patch.object(token_live_server, "PATHS", paths), mock.patch.object(
                token_live_server,
                "get_update_token",
                return_value="secret",
            ), mock.patch.object(token_live_server.openrelix_index, "search_windows") as search:
                thread.start()
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                    connection.request(
                        "OPTIONS",
                        token_live_server.WINDOW_SEARCH_PATH,
                        headers={
                            "Origin": "https://example.com",
                            "Access-Control-Request-Method": "GET",
                            "Access-Control-Request-Headers": "x-openrelix-token",
                        },
                    )
                    preflight = connection.getresponse()
                    preflight_body = preflight.read().decode("utf-8")
                    connection.close()

                    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                    connection.request(
                        "GET",
                        token_live_server.WINDOW_SEARCH_PATH + "?q=sqlite",
                        headers={"Origin": "null"},
                    )
                    response = connection.getresponse()
                    body = response.read().decode("utf-8")
                    connection.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

            self.assertEqual(preflight.status, 403, preflight_body)
            self.assertIsNone(preflight.getheader("Access-Control-Allow-Origin"))
            self.assertEqual(response.status, 403, body)
            self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
            search.assert_not_called()

    def test_panel_refresh_runs_refresh_overview_for_requested_date(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = replace(
                token_live_server.PATHS,
                repo_root=ROOT,
                state_root=root / "state",
                codex_home=root / "codex-home",
                reports_dir=root / "reports",
            )
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="refreshed\n",
                stderr="",
            )

            with mock.patch.object(token_live_server, "PATHS", paths), mock.patch.dict(
                os.environ,
                {
                    "AI_ASSET_STATE_DIR": "/tmp/wrong-state",
                    "CODEX_HOME": "/tmp/wrong-codex",
                    "OPENRELIX_ENABLE_NATIVE_DISPLAY_POLISH": "1",
                },
                clear=False,
            ), mock.patch.object(
                token_live_server.subprocess,
                "run",
                return_value=completed,
            ) as run:
                result = token_live_server.run_panel_refresh("2026-05-06")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["target_date"], "2026-05-06")
        self.assertEqual(result["asset_stats_path"], str(root / "reports" / "asset-stats-latest.json"))
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["/bin/zsh", str(ROOT / "scripts" / "refresh_overview.sh")])
        self.assertIn("--asset-layer-only", command)
        self.assertEqual(command[-2:], ["--date", "2026-05-06"])
        self.assertEqual(run.call_args.kwargs["cwd"], str(ROOT))
        self.assertEqual(run.call_args.kwargs["env"]["AI_ASSET_STATE_DIR"], str(root / "state"))
        self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], str(root / "codex-home"))
        self.assertEqual(run.call_args.kwargs["env"]["OPENRELIX_REFRESH_DATE"], "2026-05-06")
        self.assertEqual(run.call_args.kwargs["env"]["OPENRELIX_ENABLE_NATIVE_DISPLAY_POLISH"], "0")
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(run.call_args.kwargs["timeout"], token_live_server.PANEL_REFRESH_TIMEOUT_SECONDS)

    def test_memory_feedback_refresh_returns_before_background_rebuild(self):
        old_state = dict(token_live_server.MEMORY_FEEDBACK_REFRESH_STATE)

        class ImmediateThread:
            def __init__(self, target, name=None, daemon=None):
                self.target = target
                self.name = name
                self.daemon = daemon

            def start(self):
                self.target()

        try:
            token_live_server.MEMORY_FEEDBACK_REFRESH_STATE.clear()
            token_live_server.MEMORY_FEEDBACK_REFRESH_STATE.update(
                {
                    "status": "idle",
                    "started_at": 0,
                    "ended_at": 0,
                    "exit_code": None,
                    "error": "",
                }
            )
            with mock.patch.object(
                token_live_server.threading,
                "Thread",
                ImmediateThread,
            ), mock.patch.object(
                token_live_server,
                "run_memory_feedback_refresh",
                return_value={"ok": True, "status": "completed", "ended_at": 1, "exit_code": 0},
            ) as refresh:
                started, snapshot = token_live_server.start_memory_feedback_refresh_async()

            self.assertTrue(started)
            self.assertEqual(snapshot["status"], "running")
            refresh.assert_called_once()
            self.assertEqual(token_live_server.memory_feedback_refresh_snapshot()["status"], "completed")
        finally:
            token_live_server.MEMORY_FEEDBACK_REFRESH_STATE.clear()
            token_live_server.MEMORY_FEEDBACK_REFRESH_STATE.update(old_state)

    def test_memory_feedback_response_does_not_request_panel_reload(self):
        payload = token_live_server.memory_feedback_accepted_payload(
            {"memory_key": "demo", "feedback": "liked"},
            {"status": "running"},
            True,
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["feedback"]["feedback"], "liked")
        self.assertNotIn("reload_after_ms", payload)

    def test_panel_update_starts_detached_worker_and_persists_status(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = replace(
                token_live_server.PATHS,
                repo_root=ROOT,
                state_root=root,
                codex_home=root / "codex-home",
                runtime_dir=root / "runtime",
            )
            status_path = root / "runtime" / "update-status.json"
            worker_path = ROOT / "scripts" / "openrelix_update_worker.py"
            process = mock.Mock(pid=4242)

            with mock.patch.object(token_live_server, "PATHS", paths), mock.patch.object(
                token_live_server,
                "UPDATE_STATUS_PATH",
                status_path,
            ), mock.patch.object(
                token_live_server,
                "UPDATE_WORKER_SCRIPT",
                worker_path,
            ), mock.patch.object(
                token_live_server.subprocess,
                "Popen",
                return_value=process,
            ) as popen:
                started, snapshot = token_live_server.start_update_async()

            self.assertTrue(started)
            self.assertEqual(snapshot["status"], "running")
            self.assertEqual(snapshot["pid"], 4242)
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "running")
            self.assertEqual(payload["phase"], "installing")
            self.assertEqual(payload["pid"], 4242)

            command = popen.call_args.args[0]
            self.assertEqual(command[1], str(worker_path))
            self.assertIn("--status-file", command)
            self.assertIn(str(status_path), command)
            self.assertIn("--state-dir", command)
            self.assertIn(str(root), command)
            self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_update_worker_forces_reinstall_and_writes_completed_status(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            (repo_root / "scripts").mkdir(parents=True)
            status_path = root / "runtime" / "update-status.json"
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="installed\n",
                stderr="",
            )

            with mock.patch.object(
                openrelix_update_worker.subprocess,
                "run",
                return_value=completed,
            ) as run:
                exit_code = openrelix_update_worker.main(
                    [
                        "--repo-root",
                        str(repo_root),
                        "--status-file",
                        str(status_path),
                        "--state-dir",
                        str(root / "state"),
                        "--codex-home",
                        str(root / "codex-home"),
                        "--python-bin",
                        "/usr/bin/python3",
                    ]
                )

            self.assertEqual(exit_code, 0)
            command = run.call_args.args[0]
            self.assertEqual(
                command,
                [
                    "/usr/bin/python3",
                    str(repo_root.resolve() / "scripts" / "openrelix.py"),
                    "update",
                    "--yes",
                    "--force",
                ],
            )
            self.assertEqual(run.call_args.kwargs["env"]["AI_ASSET_STATE_DIR"], str(root / "state"))
            self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], str(root / "codex-home"))
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["exit_code"], 0)
            self.assertEqual(payload["reload_after_ms"], 1500)
            self.assertIn("installed", payload["log_tail"])

    def test_update_worker_downgrades_up_to_date_reinstall_failure(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            (repo_root / "scripts").mkdir(parents=True)
            status_path = root / "runtime" / "update-status.json"
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="OpenRelix 更新检查\n- 当前已是最新版本。\n",
                stderr="usage: openrelix ... invalid choice: 'install'\n",
            )

            with mock.patch.object(
                openrelix_update_worker.subprocess,
                "run",
                return_value=completed,
            ):
                exit_code = openrelix_update_worker.main(
                    [
                        "--repo-root",
                        str(repo_root),
                        "--status-file",
                        str(status_path),
                        "--state-dir",
                        str(root / "state"),
                        "--codex-home",
                        str(root / "codex-home"),
                        "--python-bin",
                        "/usr/bin/python3",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "reinstall_failed")
            self.assertEqual(payload["exit_code"], 2)
            self.assertEqual(payload["error"], "reinstall_failed_exit_code=2")
            self.assertEqual(payload["reload_after_ms"], 0)
            self.assertIn("当前已是最新版本", payload["log_tail"])

    def test_openrelix_update_finds_npx_from_common_tool_paths(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            npx_bin = bin_dir / "npx"
            npx_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            npx_bin.chmod(0o755)
            args = argparse.Namespace(
                check=False,
                print_command=False,
                recommended=False,
                yes=True,
                json=False,
                force=False,
            )

            with mock.patch.object(openrelix, "COMMON_CLI_TOOL_PATHS", (str(bin_dir),)), mock.patch.dict(
                os.environ,
                {"PATH": "/usr/bin"},
                clear=False,
            ), mock.patch.object(openrelix, "read_local_package_version", return_value="0.2.10"), mock.patch.object(
                openrelix,
                "fetch_latest_npm_version",
                return_value="0.3.0",
            ), mock.patch.object(
                openrelix,
                "check_npm_package_version_installable",
                return_value="",
            ), mock.patch.object(
                openrelix,
                "update_install_cwd",
                return_value=root / "npm-update",
            ), mock.patch.object(
                openrelix.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as run, mock.patch(
                "sys.stdout",
                new_callable=io.StringIO,
            ):
                openrelix.command_update(args)

            command = run.call_args.args[0]
            self.assertEqual(command[0], str(npx_bin))
            self.assertEqual(command[2], "openrelix@0.3.0")
            self.assertIn(str(bin_dir), run.call_args.kwargs["env"]["PATH"])
            self.assertEqual(run.call_args.kwargs["cwd"], str(root / "npm-update"))

    def test_openrelix_update_print_command_uses_explicit_latest_version(self):
        args = argparse.Namespace(
            check=False,
            print_command=True,
            recommended=False,
            yes=False,
            json=False,
            force=False,
        )

        with mock.patch.object(openrelix, "read_local_package_version", return_value="0.3.5"), mock.patch.object(
            openrelix,
            "fetch_latest_npm_version",
            return_value="0.3.7",
        ), mock.patch.object(
            openrelix,
            "resolve_cli_tool",
            return_value="/opt/homebrew/bin/npx",
        ), mock.patch(
            "sys.stdout",
            new_callable=io.StringIO,
        ) as stdout:
            openrelix.command_update(args)

        output = stdout.getvalue()
        self.assertIn("/opt/homebrew/bin/npx -y openrelix@0.3.7 install", output)
        self.assertNotIn("openrelix@latest install", output)

    def test_openrelix_update_reports_registry_lag_without_running_npx(self):
        args = argparse.Namespace(
            check=False,
            print_command=False,
            recommended=False,
            yes=True,
            json=False,
            force=True,
        )
        with mock.patch.object(openrelix, "read_local_package_version", return_value="0.2.10"), mock.patch.object(
            openrelix,
            "fetch_latest_npm_version",
            return_value="0.3.0",
        ), mock.patch.object(
            openrelix,
            "resolve_cli_tool",
            side_effect=lambda tool_name: "/tmp/{}".format(tool_name),
        ), mock.patch.object(
            openrelix,
            "check_npm_package_version_installable",
            return_value="npm ERR! code ETARGET\nnpm ERR! notarget No matching version found for openrelix@0.3.0.",
        ), mock.patch.object(
            openrelix.subprocess,
            "run",
        ) as run, mock.patch(
            "sys.stdout",
            new_callable=io.StringIO,
        ):
            with self.assertRaises(SystemExit) as raised:
                openrelix.command_update(args)

        self.assertIn("openrelix@0.3.0", str(raised.exception))
        run.assert_not_called()

    def test_openrelix_models_uses_codex_debug_models_and_sanitizes_catalog(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            paths = replace(openrelix.PATHS, state_root=root, runtime_dir=root / "runtime", codex_home=codex_home)
            stdout = json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-5.5",
                            "display_name": "GPT-5.5",
                            "description": "Frontier model.",
                            "default_reasoning_level": "medium",
                            "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}],
                            "supported_in_api": True,
                            "visibility": "list",
                            "priority": 0,
                            "base_instructions": "do not expose this prompt",
                        },
                        {
                            "slug": "codex-auto-review",
                            "display_name": "Codex Auto Review",
                            "visibility": "hide",
                            "priority": 100,
                            "base_instructions": "hidden prompt",
                        },
                    ]
                }
            )
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
            args = argparse.Namespace(all=False, bundled=False, json=True)

            with mock.patch.object(openrelix, "PATHS", paths), mock.patch.dict(
                os.environ,
                {"OPENRELIX_CODEX_MODEL": "", "AI_ASSET_CODEX_MODEL": ""},
                clear=False,
            ), mock.patch.object(openrelix.subprocess, "run", return_value=completed) as run, mock.patch(
                "sys.stdout",
                new_callable=io.StringIO,
            ) as stream:
                openrelix.command_models(args)

            command = run.call_args.args[0]
            self.assertEqual(command, [paths.codex_bin, "debug", "models"])
            self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], str(codex_home))
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["configured_model"], "gpt-5.4-mini")
            self.assertEqual(payload["recommended_default"], "gpt-5.4-mini")
            self.assertEqual([item["slug"] for item in payload["models"]], ["gpt-5.5"])
            self.assertEqual(payload["models"][0]["supported_reasoning_levels"], ["low", "medium"])
            self.assertNotIn("base_instructions", payload["models"][0])

    def test_openrelix_models_main_path_does_not_create_state_layout(self):
        old_paths = openrelix.PATHS
        try:
            with TemporaryDirectory() as tmpdir:
                paths = make_runtime_paths_for_test(Path(tmpdir) / "state")
                openrelix.PATHS = paths
                stdout = json.dumps({"models": [{"slug": "gpt-5.4-mini", "visibility": "list"}]})
                completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
                with mock.patch.object(sys, "argv", ["openrelix", "models", "--json"]), mock.patch.object(
                    openrelix.subprocess,
                    "run",
                    return_value=completed,
                ), mock.patch("sys.stdout", new_callable=io.StringIO) as stream:
                    openrelix.main()
                payload = json.loads(stream.getvalue())
                self.assertEqual(payload["models"][0]["slug"], "gpt-5.4-mini")
                self.assertFalse(paths.registry_dir.exists())
                self.assertFalse(paths.runtime_dir.exists())
        finally:
            openrelix.PATHS = old_paths

    def test_openrelix_config_rejects_out_of_range_memory_summary_max_tokens(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir(parents=True)
            paths = replace(openrelix.PATHS, state_root=root, runtime_dir=runtime_dir)
            args = argparse.Namespace(
                memory_summary_max_tokens=1000,
                activity_source=None,
                codex_model=None,
                read_codex_app=False,
                no_refresh=True,
                json=True,
            )

            with mock.patch.object(openrelix, "PATHS", paths):
                with self.assertRaises(SystemExit) as raised:
                    openrelix.command_config(args)

            self.assertIn("memory_summary_max_tokens must be between", str(raised.exception))
            self.assertFalse((runtime_dir / "config.json").exists())

    def test_openrelix_refresh_default_does_not_trigger_learning_pipeline(self):
        args = argparse.Namespace(
            learn_memory=False,
            date="2026-04-28",
            stage="manual",
            learn_window_days=7,
            json=True,
        )
        overview = {
            "generated_at": "2026-04-28T12:00:00+08:00",
            "summary": {"day_summary": "demo"},
            "metrics": {"today": 1},
            "token_usage": {},
            "nightly": {},
        }
        calls = []

        with mock.patch.object(openrelix, "run_checked", side_effect=lambda cmd: calls.append(cmd)), mock.patch.object(
            openrelix,
            "load_overview",
            return_value=overview,
        ), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            openrelix.command_refresh(args)

        self.assertEqual(calls, [["/bin/zsh", str(openrelix.REFRESH_SCRIPT)]])
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["learn_memory"])
        self.assertEqual(payload["summary"], overview["summary"])

    def test_openrelix_refresh_learn_memory_passes_explicit_learning_args(self):
        args = argparse.Namespace(
            learn_memory=True,
            date="2026-04-28",
            stage="manual",
            learn_window_days=7,
            json=True,
        )
        overview = {
            "generated_at": "2026-04-28T12:00:00+08:00",
            "summary": {},
            "metrics": {},
            "token_usage": {},
            "nightly": {},
        }
        calls = []

        with mock.patch.object(openrelix, "run_checked", side_effect=lambda cmd: calls.append(cmd)), mock.patch.object(
            openrelix,
            "load_overview",
            return_value=overview,
        ), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            openrelix.command_refresh(args)

        self.assertEqual(
            calls,
            [
                [
                    "/bin/zsh",
                    str(openrelix.REFRESH_SCRIPT),
                    "--learn-memory",
                    "--date",
                    "2026-04-28",
                    "--stage",
                    "manual",
                    "--learn-window-days",
                    "7",
                ]
            ],
        )
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["learn_memory"])

    def test_openrelix_refresh_learn_memory_omits_zero_window_arg(self):
        args = argparse.Namespace(
            learn_memory=True,
            date="2026-04-28",
            stage="manual",
            learn_window_days=0,
            json=True,
        )

        with mock.patch.object(openrelix, "run_checked") as run_checked, mock.patch.object(
            openrelix,
            "load_overview",
            return_value={},
        ), mock.patch("sys.stdout", new_callable=io.StringIO):
            openrelix.command_refresh(args)

        run_checked.assert_called_once_with(
            [
                "/bin/zsh",
                str(openrelix.REFRESH_SCRIPT),
                "--learn-memory",
                "--date",
                "2026-04-28",
                "--stage",
                "manual",
            ]
        )

    def test_openrelix_refresh_learn_memory_exits_nonzero_on_model_failure(self):
        args = argparse.Namespace(
            learn_memory=True,
            date="2026-04-28",
            stage="manual",
            learn_window_days=7,
            json=False,
        )
        overview = {
            "generated_at": "2026-04-28T12:00:00+08:00",
            "summary": {},
            "metrics": {},
            "token_usage": {},
            "nightly": {},
        }

        with TemporaryDirectory() as tmpdir:
            summary_json_path = Path(tmpdir) / "summary.json"
            summary_md_path = Path(tmpdir) / "summary.md"
            summary_json_path.write_text(
                json.dumps(
                    {
                        "date": "2026-04-28",
                        "last_run_model_status": "failed",
                        "last_run_model_error_hint": "请重新运行 `codex login`。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with mock.patch.object(openrelix, "run_checked"), mock.patch.object(
                openrelix,
                "load_overview",
                return_value=overview,
            ), mock.patch.object(
                openrelix,
                "review_summary_paths",
                return_value=(summary_json_path, summary_md_path),
            ), mock.patch("sys.stdout", new_callable=io.StringIO), mock.patch(
                "sys.stderr",
                new_callable=io.StringIO,
            ) as stderr:
                with self.assertRaises(SystemExit) as raised:
                    openrelix.command_refresh(args)

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("学习刷新未完整成功", stderr.getvalue())
        self.assertIn("codex login", stderr.getvalue())

    def test_openrelix_doctor_reports_latest_model_failure(self):
        old_paths = openrelix.PATHS
        old_consolidated_daily_dir = openrelix.CONSOLIDATED_DAILY_DIR
        try:
            with TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                codex_home = root / "codex-home"
                consolidated_daily_dir = root / "consolidated" / "daily"
                codex_home.mkdir(parents=True)
                summary_dir = consolidated_daily_dir / "2026-04-28"
                summary_dir.mkdir(parents=True)
                (summary_dir / "summary.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-28",
                            "last_run_model_status": "failed",
                            "last_run_model_error_hint": "请重新运行 `codex login`。",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                openrelix.PATHS = replace(
                    openrelix.PATHS,
                    state_root=root,
                    codex_home=codex_home,
                    codex_bin=sys.executable,
                    consolidated_daily_dir=consolidated_daily_dir,
                    nightly_runner_dir=root / "runtime" / "nightly-runner",
                    nightly_codex_home=root / "runtime" / "codex-nightly-home",
                )
                openrelix.CONSOLIDATED_DAILY_DIR = consolidated_daily_dir
                args = argparse.Namespace(model_check=False, json=False)
                with mock.patch.object(openrelix, "current_date_str", return_value="2026-04-28"), mock.patch(
                    "sys.stdout",
                    new_callable=io.StringIO,
                ) as stdout:
                    with self.assertRaises(SystemExit) as raised:
                        openrelix.command_doctor(args)

        finally:
            openrelix.PATHS = old_paths
            openrelix.CONSOLIDATED_DAILY_DIR = old_consolidated_daily_dir

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("latest_learning_run", stdout.getvalue())
        self.assertIn("codex login", stdout.getvalue())

    def test_doctor_model_check_detail_unwraps_claude_json(self):
        output = json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": "OPENRELIX_DOCTOR_OK",
                "terminal_reason": "completed",
            }
        )

        self.assertEqual(openrelix.doctor_model_check_detail("claude", output), "OPENRELIX_DOCTOR_OK")

    def test_openrelix_doctor_can_probe_codex_app_server(self):
        old_paths = openrelix.PATHS
        old_consolidated_daily_dir = openrelix.CONSOLIDATED_DAILY_DIR
        try:
            with TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                codex_home = root / "codex-home"
                consolidated_daily_dir = root / "consolidated" / "daily"
                codex_home.mkdir(parents=True)
                consolidated_daily_dir.mkdir(parents=True)
                openrelix.PATHS = replace(
                    openrelix.PATHS,
                    state_root=root,
                    codex_home=codex_home,
                    codex_bin=sys.executable,
                    consolidated_daily_dir=consolidated_daily_dir,
                    nightly_runner_dir=root / "runtime" / "nightly-runner",
                    nightly_codex_home=root / "runtime" / "codex-nightly-home",
                )
                openrelix.CONSOLIDATED_DAILY_DIR = consolidated_daily_dir
                args = argparse.Namespace(model_check=False, app_server_check=True, json=True)
                with mock.patch.object(
                    openrelix,
                    "run_codex_app_server_help_check",
                    return_value=subprocess.CompletedProcess(
                        ["codex", "app-server", "--help"],
                        0,
                        stdout="[experimental] Run the app server or related tooling\n",
                        stderr="",
                    ),
                ), mock.patch.object(
                    openrelix,
                    "run_doctor_app_server_check",
                    return_value=subprocess.CompletedProcess(
                        ["collect_codex_activity.py"],
                        0,
                        stdout="",
                        stderr="",
                    ),
                ), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    openrelix.command_doctor(args)

        finally:
            openrelix.PATHS = old_paths
            openrelix.CONSOLIDATED_DAILY_DIR = old_consolidated_daily_dir

        payload = json.loads(stdout.getvalue())
        checks = {check["name"]: check for check in payload["checks"]}
        self.assertTrue(payload["ok"])
        self.assertEqual(checks["codex_app_server_command"]["status"], "ok")
        self.assertEqual(checks["codex_app_server_probe"]["status"], "ok")

    def test_openrelix_doctor_reports_sqlite_index_status(self):
        checks = []
        with mock.patch.object(
            openrelix,
            "sqlite_index_status_payload",
            return_value={
                "db_path": "/tmp/openrelix-index.sqlite3",
                "exists": True,
                "schema_version": 1,
                "memory_rows": 3,
                "window_rows": 4,
                "stale": False,
                "ok": True,
            },
        ):
            openrelix.append_sqlite_index_doctor_check(checks)

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["name"], "sqlite_index")
        self.assertEqual(checks[0]["status"], "ok")
        self.assertIn("memories=3", checks[0]["detail"])
        self.assertIn("windows=4", checks[0]["detail"])

    def test_openrelix_index_command_rebuild_status_and_search_json(self):
        old_paths = openrelix.PATHS
        try:
            with TemporaryDirectory() as tmpdir:
                paths = make_runtime_paths_for_test(Path(tmpdir) / "state")
                openrelix.PATHS = paths
                asset_runtime.ensure_state_layout(paths)
                (paths.registry_dir / "memory_items.jsonl").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-28",
                            "source": "nightly_codex",
                            "bucket": "durable",
                            "title": "SQLite sidecar index",
                            "memory_type": "procedural",
                            "priority": "high",
                            "value_note": "Future search reads this rebuildable local database.",
                            "source_window_ids": ["w-cli"],
                            "keywords": ["sqlite", "search"],
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (paths.raw_daily_dir / "2026-04-28.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-28",
                            "stage": "manual",
                            "windows": [
                                {
                                    "date": "2026-04-28",
                                    "window_id": "w-cli",
                                    "cwd": "/tmp/openrelix",
                                    "source": "history",
                                    "prompts": [{"local_time": "2026-04-28T10:00:00+08:00", "text": "add sqlite cli"}],
                                    "conclusions": [{"completed_at": "2026-04-28T10:05:00+08:00", "text": "index command works"}],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    openrelix.command_index(
                        argparse.Namespace(
                            action="rebuild",
                            query="",
                            bucket=None,
                            priority=None,
                            project=None,
                            date_from=None,
                            date_to=None,
                            limit=20,
                            json=True,
                        )
                    )
                rebuild_payload = json.loads(stdout.getvalue())
                self.assertEqual(rebuild_payload["memory_rows"], 1)
                self.assertEqual(rebuild_payload["window_rows"], 1)

                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    openrelix.command_index(
                        argparse.Namespace(
                            action="status",
                            query="",
                            bucket=None,
                            priority=None,
                            project=None,
                            date_from=None,
                            date_to=None,
                            limit=20,
                            json=True,
                        )
                    )
                status_payload = json.loads(stdout.getvalue())
                self.assertTrue(status_payload["ok"])
                self.assertFalse(status_payload["stale"])

                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    openrelix.command_index(
                        argparse.Namespace(
                            action="search-memory",
                            query="sqlite",
                            bucket="durable",
                            priority=None,
                            project=None,
                            date_from=None,
                            date_to=None,
                            limit=20,
                            json=True,
                        )
                    )
                search_payload = json.loads(stdout.getvalue())
                self.assertEqual(search_payload["results"][0]["title"], "SQLite sidecar index")

                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    openrelix.command_index(
                        argparse.Namespace(
                            action="search-window",
                            query="index command",
                            bucket=None,
                            priority=None,
                            project="openrelix",
                            date_from=None,
                            date_to=None,
                            limit=20,
                            json=True,
                        )
                    )
                window_payload = json.loads(stdout.getvalue())
                self.assertEqual(window_payload["results"][0]["window_id"], "w-cli")
        finally:
            openrelix.PATHS = old_paths

    def test_openrelix_paths_prints_sqlite_index_path(self):
        old_paths = openrelix.PATHS
        try:
            with TemporaryDirectory() as tmpdir:
                paths = make_runtime_paths_for_test(Path(tmpdir) / "state")
                openrelix.PATHS = paths
                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    openrelix.command_paths()
        finally:
            openrelix.PATHS = old_paths

        self.assertIn("- index_db: {}".format(paths.runtime_dir / "openrelix-index.sqlite3"), stdout.getvalue())

    def test_openrelix_index_status_main_path_does_not_create_state_layout(self):
        old_paths = openrelix.PATHS
        try:
            with TemporaryDirectory() as tmpdir:
                paths = make_runtime_paths_for_test(Path(tmpdir) / "state")
                openrelix.PATHS = paths
                with mock.patch.object(sys, "argv", ["openrelix", "index", "status", "--json"]), mock.patch(
                    "sys.stdout",
                    new_callable=io.StringIO,
                ) as stdout:
                    openrelix.main()
                payload = json.loads(stdout.getvalue())
                self.assertFalse(payload["exists"])
                self.assertFalse(paths.registry_dir.exists())
                self.assertFalse(paths.runtime_dir.exists())
        finally:
            openrelix.PATHS = old_paths

    def test_refresh_overview_learn_memory_forwards_env_to_nightly_pipeline(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir(parents=True)
            refresh_script = scripts_dir / "refresh_overview.sh"
            refresh_script.write_text(
                (ROOT / "scripts" / "refresh_overview.sh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            record_path = root / "nightly-args.txt"
            (scripts_dir / "nightly_pipeline.sh").write_text(
                "#!/bin/zsh\nprintf '%s\\n' \"$@\" > \"$OPENRELIX_TEST_NIGHTLY_ARGS\"\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.update(
                {
                    "OPENRELIX_REFRESH_LEARN_MEMORY": "1",
                    "OPENRELIX_REFRESH_DATE": "2026-04-28",
                    "OPENRELIX_REFRESH_STAGE": "manual",
                    "OPENRELIX_REFRESH_LEARN_WINDOW_DAYS": "7",
                    "OPENRELIX_TEST_NIGHTLY_ARGS": str(record_path),
                }
            )

            result = subprocess.run(
                ["/bin/zsh", str(refresh_script)],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            recorded_args = record_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            recorded_args,
            ["2026-04-28", "manual", "--learn-window-days", "7"],
        )

    def test_refresh_overview_learn_memory_can_skip_unchanged_inputs(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir(parents=True)
            refresh_script = scripts_dir / "refresh_overview.sh"
            refresh_script.write_text(
                (ROOT / "scripts" / "refresh_overview.sh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            record_path = root / "nightly-args.txt"
            (scripts_dir / "nightly_pipeline.sh").write_text(
                "#!/bin/zsh\nprintf '%s\\n' \"$@\" > \"$OPENRELIX_TEST_NIGHTLY_ARGS\"\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.update(
                {
                    "OPENRELIX_REFRESH_LEARN_MEMORY": "1",
                    "OPENRELIX_REFRESH_DATE": "2026-04-28",
                    "OPENRELIX_REFRESH_STAGE": "preliminary",
                    "OPENRELIX_REFRESH_LEARN_WINDOW_DAYS": "7",
                    "OPENRELIX_REFRESH_SKIP_UNCHANGED": "1",
                    "OPENRELIX_TEST_NIGHTLY_ARGS": str(record_path),
                }
            )

            result = subprocess.run(
                ["/bin/zsh", str(refresh_script)],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            recorded_args = record_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            recorded_args,
            ["2026-04-28", "preliminary", "--learn-window-days", "7", "--skip-if-unchanged"],
        )

    def test_refresh_overview_native_display_polish_defaults_for_chinese_integrated_mode(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir(parents=True)
            refresh_script = scripts_dir / "refresh_overview.sh"
            marker_path = root / "native-display-polish-called"
            refresh_script.write_text(
                (ROOT / "scripts" / "refresh_overview.sh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (scripts_dir / "asset_runtime.py").write_text(
                "\n".join(
                    [
                        "import os",
                        "def get_memory_mode(*args, **kwargs):",
                        "    return os.environ.get('OPENRELIX_TEST_MEMORY_MODE', 'integrated')",
                        "def get_runtime_language(*args, **kwargs):",
                        "    return os.environ.get('OPENRELIX_TEST_LANGUAGE', 'zh')",
                    ]
                ),
                encoding="utf-8",
            )
            (scripts_dir / "collect_codex_activity.py").write_text("", encoding="utf-8")
            (scripts_dir / "build_overview.py").write_text("", encoding="utf-8")
            (scripts_dir / "build_codex_memory_summary.py").write_text("", encoding="utf-8")
            (scripts_dir / "sync_host_memory_summary.py").write_text("", encoding="utf-8")
            (scripts_dir / "build_codex_native_display_cache.py").write_text(
                "\n".join(
                    [
                        "import os",
                        "from pathlib import Path",
                        "Path(os.environ['OPENRELIX_TEST_NATIVE_DISPLAY_MARKER']).write_text('called', encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPENRELIX_TEST_NATIVE_DISPLAY_MARKER"] = str(marker_path)
            env["OPENRELIX_TEST_LANGUAGE"] = "zh"
            env["OPENRELIX_TEST_MEMORY_MODE"] = "integrated"
            env.pop("OPENRELIX_ENABLE_NATIVE_DISPLAY_POLISH", None)

            default_result = subprocess.run(
                ["/bin/zsh", str(refresh_script)],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(default_result.returncode, 0, default_result.stderr)
            self.assertEqual(marker_path.read_text(encoding="utf-8"), "called")

            marker_path.unlink()
            env["OPENRELIX_ENABLE_NATIVE_DISPLAY_POLISH"] = "0"
            disabled_result = subprocess.run(
                ["/bin/zsh", str(refresh_script)],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(disabled_result.returncode, 0, disabled_result.stderr)
            self.assertFalse(marker_path.exists())

    def test_refresh_overview_writes_asset_stats_before_panel_rebuild(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir(parents=True)
            refresh_script = scripts_dir / "refresh_overview.sh"
            order_log = root / "order.log"
            refresh_script.write_text(
                (ROOT / "scripts" / "refresh_overview.sh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (scripts_dir / "asset_runtime.py").write_text(
                "\n".join(
                    [
                        "def get_runtime_language(*args, **kwargs):",
                        "    return 'en'",
                        "def get_memory_mode(*args, **kwargs):",
                        "    return 'local-only'",
                    ]
                ),
                encoding="utf-8",
            )
            for script_name, marker in (
                ("collect_codex_activity.py", "collect"),
                ("sync_host_memory_summary.py", "sync"),
                ("build_overview.py", "build_overview"),
            ):
                (scripts_dir / script_name).write_text(
                    "\n".join(
                        [
                            "import os, sys",
                            "with open(os.environ['OPENRELIX_TEST_ORDER_LOG'], 'a', encoding='utf-8') as fh:",
                            "    fh.write('{} ' + ' '.join(sys.argv[1:]) + '\\n')".format(marker),
                        ]
                    ),
                    encoding="utf-8",
                )
            (scripts_dir / "openrelix.py").write_text(
                "\n".join(
                    [
                        "import os, sys",
                        "with open(os.environ['OPENRELIX_TEST_ORDER_LOG'], 'a', encoding='utf-8') as fh:",
                        "    fh.write('openrelix ' + ' '.join(sys.argv[1:]) + '\\n')",
                    ]
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPENRELIX_TEST_ORDER_LOG"] = str(order_log)
            env["OPENRELIX_REFRESH_DATE"] = "2026-05-06"

            result = subprocess.run(
                ["/bin/zsh", str(refresh_script)],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            lines = order_log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("openrelix memory-migration run --if-pending --quiet", lines)
        self.assertLess(
            lines.index("openrelix memory-migration run --if-pending --quiet"),
            lines.index("collect --date 2026-05-06 --stage manual"),
        )
        self.assertIn("openrelix asset-stats --date 2026-05-06 --no-refresh", lines)
        self.assertLess(
            lines.index("openrelix asset-stats --date 2026-05-06 --no-refresh"),
            lines.index("build_overview "),
        )

    def test_refresh_overview_asset_layer_only_skips_heavy_steps(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir(parents=True)
            refresh_script = scripts_dir / "refresh_overview.sh"
            order_log = root / "order.log"
            refresh_script.write_text(
                (ROOT / "scripts" / "refresh_overview.sh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            for script_name, marker in (
                ("collect_codex_activity.py", "collect"),
                ("sync_host_memory_summary.py", "sync"),
                ("openrelix_index.py", "index"),
                ("build_overview.py", "build_overview"),
            ):
                (scripts_dir / script_name).write_text(
                    "\n".join(
                        [
                            "import os, sys",
                            "with open(os.environ['OPENRELIX_TEST_ORDER_LOG'], 'a', encoding='utf-8') as fh:",
                            "    fh.write('{} ' + ' '.join(sys.argv[1:]) + '\\n')".format(marker),
                        ]
                    ),
                    encoding="utf-8",
                )
            (scripts_dir / "openrelix.py").write_text(
                "\n".join(
                    [
                        "import os, sys",
                        "with open(os.environ['OPENRELIX_TEST_ORDER_LOG'], 'a', encoding='utf-8') as fh:",
                        "    fh.write('openrelix ' + ' '.join(sys.argv[1:]) + '\\n')",
                    ]
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPENRELIX_TEST_ORDER_LOG"] = str(order_log)
            env["OPENRELIX_REFRESH_DATE"] = "2026-05-06"

            result = subprocess.run(
                ["/bin/zsh", str(refresh_script), "--asset-layer-only"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            lines = order_log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            lines,
            [
                "openrelix asset-stats --date 2026-05-06 --no-refresh",
                "build_overview ",
            ],
        )

    def test_nightly_pipeline_returns_nonzero_when_latest_model_run_failed(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir(parents=True)
            consolidated_daily_dir = root / "consolidated" / "daily"
            pipeline_script = scripts_dir / "nightly_pipeline.sh"
            pipeline_script.write_text(
                (ROOT / "scripts" / "nightly_pipeline.sh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (scripts_dir / "asset_runtime.py").write_text(
                "\n".join(
                    [
                        "import os",
                        "from pathlib import Path",
                        "class RuntimePaths:",
                        "    consolidated_daily_dir = Path(os.environ['OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR'])",
                        "def get_runtime_paths():",
                        "    return RuntimePaths()",
                        "def get_memory_mode(*args, **kwargs):",
                        "    return 'local-only'",
                    ]
                ),
                encoding="utf-8",
            )
            (scripts_dir / "collect_codex_activity.py").write_text("", encoding="utf-8")
            (scripts_dir / "build_overview.py").write_text("", encoding="utf-8")
            (scripts_dir / "build_codex_memory_summary.py").write_text("", encoding="utf-8")
            (scripts_dir / "sync_host_memory_summary.py").write_text("", encoding="utf-8")
            (scripts_dir / "nightly_consolidate.py").write_text(
                "\n".join(
                    [
                        "import argparse, json, os",
                        "from pathlib import Path",
                        "parser = argparse.ArgumentParser()",
                        "parser.add_argument('--date')",
                        "parser.add_argument('--stage')",
                        "parser.add_argument('--learn-window-days')",
                        "parser.add_argument('--skip-if-unchanged', action='store_true')",
                        "args = parser.parse_args()",
                        "summary_dir = Path(os.environ['OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR']) / args.date",
                        "summary_dir.mkdir(parents=True, exist_ok=True)",
                        "(summary_dir / 'summary.json').write_text(json.dumps({",
                        "    'date': args.date,",
                        "    'last_run_model_status': 'failed',",
                        "    'last_run_model_error_hint': 'login required',",
                        "}), encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            (scripts_dir / "sync_host_memory_summary.py").write_text("", encoding="utf-8")
            env = dict(os.environ)
            env["OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR"] = str(consolidated_daily_dir)

            result = subprocess.run(
                ["/bin/zsh", str(pipeline_script), "2026-04-28", "manual"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("model summarization failed", result.stderr)
        self.assertIn("login required", result.stderr)

    def test_nightly_pipeline_can_defer_global_refresh_and_skip_learning_collect(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir(parents=True)
            consolidated_daily_dir = root / "consolidated" / "daily"
            collect_log = root / "collect.log"
            nightly_args_path = root / "nightly-args.txt"
            overview_marker = root / "overview-called"
            pipeline_script = scripts_dir / "nightly_pipeline.sh"
            pipeline_script.write_text(
                (ROOT / "scripts" / "nightly_pipeline.sh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (scripts_dir / "asset_runtime.py").write_text(
                "\n".join(
                    [
                        "import os",
                        "from pathlib import Path",
                        "class RuntimePaths:",
                        "    consolidated_daily_dir = Path(os.environ['OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR'])",
                        "def get_runtime_paths():",
                        "    return RuntimePaths()",
                        "def get_memory_mode(*args, **kwargs):",
                        "    return 'local-only'",
                        "def get_runtime_language(*args, **kwargs):",
                        "    return 'en'",
                    ]
                ),
                encoding="utf-8",
            )
            (scripts_dir / "collect_codex_activity.py").write_text(
                "\n".join(
                    [
                        "import sys, os",
                        "with open(os.environ['OPENRELIX_TEST_COLLECT_LOG'], 'a', encoding='utf-8') as fh:",
                        "    fh.write(' '.join(sys.argv[1:]) + '\\n')",
                    ]
                ),
                encoding="utf-8",
            )
            (scripts_dir / "build_overview.py").write_text(
                "\n".join(
                    [
                        "import os",
                        "from pathlib import Path",
                        "Path(os.environ['OPENRELIX_TEST_OVERVIEW_MARKER']).write_text('called', encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            (scripts_dir / "build_codex_memory_summary.py").write_text("", encoding="utf-8")
            (scripts_dir / "nightly_consolidate.py").write_text(
                "\n".join(
                    [
                        "import argparse, json, os, sys",
                        "from pathlib import Path",
                        "Path(os.environ['OPENRELIX_TEST_NIGHTLY_ARGS']).write_text('\\n'.join(sys.argv[1:]), encoding='utf-8')",
                        "parser = argparse.ArgumentParser()",
                        "parser.add_argument('--date')",
                        "parser.add_argument('--stage')",
                        "parser.add_argument('--learn-window-days')",
                        "parser.add_argument('--skip-if-unchanged', action='store_true')",
                        "args = parser.parse_args()",
                        "summary_dir = Path(os.environ['OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR']) / args.date",
                        "summary_dir.mkdir(parents=True, exist_ok=True)",
                        "(summary_dir / 'summary.json').write_text(json.dumps({'date': args.date}), encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR"] = str(consolidated_daily_dir)
            env["OPENRELIX_TEST_COLLECT_LOG"] = str(collect_log)
            env["OPENRELIX_TEST_NIGHTLY_ARGS"] = str(nightly_args_path)
            env["OPENRELIX_TEST_OVERVIEW_MARKER"] = str(overview_marker)

            result = subprocess.run(
                [
                    "/bin/zsh",
                    str(pipeline_script),
                    "2026-04-28",
                    "final",
                    "--learn-window-days",
                    "2",
                    "--defer-global-refresh",
                    "--skip-learning-collect",
                ],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            collect_lines = collect_log.read_text(encoding="utf-8").splitlines()
            nightly_args = nightly_args_path.read_text(encoding="utf-8").splitlines()
            overview_exists = overview_marker.exists()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            collect_lines,
            ["--date 2026-04-28 --stage final"],
        )
        self.assertIn("--learn-window-days", nightly_args)
        self.assertNotIn("--defer-global-refresh", nightly_args)
        self.assertNotIn("--skip-learning-collect", nightly_args)
        self.assertFalse(overview_exists)

    def test_nightly_pipeline_writes_asset_stats_before_panel_rebuild(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir(parents=True)
            consolidated_daily_dir = root / "consolidated" / "daily"
            order_log = root / "order.log"
            pipeline_script = scripts_dir / "nightly_pipeline.sh"
            pipeline_script.write_text(
                (ROOT / "scripts" / "nightly_pipeline.sh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (scripts_dir / "asset_runtime.py").write_text(
                "\n".join(
                    [
                        "import os",
                        "from pathlib import Path",
                        "class RuntimePaths:",
                        "    consolidated_daily_dir = Path(os.environ['OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR'])",
                        "def get_runtime_paths():",
                        "    return RuntimePaths()",
                        "def get_memory_mode(*args, **kwargs):",
                        "    return 'local-only'",
                        "def get_runtime_language(*args, **kwargs):",
                        "    return 'en'",
                    ]
                ),
                encoding="utf-8",
            )
            for script_name, marker in (
                ("collect_codex_activity.py", "collect"),
                ("sync_host_memory_summary.py", "sync"),
                ("build_overview.py", "build_overview"),
            ):
                (scripts_dir / script_name).write_text(
                    "\n".join(
                        [
                            "import os, sys",
                            "with open(os.environ['OPENRELIX_TEST_ORDER_LOG'], 'a', encoding='utf-8') as fh:",
                            "    fh.write('{} ' + ' '.join(sys.argv[1:]) + '\\n')".format(marker),
                        ]
                    ),
                    encoding="utf-8",
                )
            (scripts_dir / "nightly_consolidate.py").write_text(
                "\n".join(
                    [
                        "import argparse, json, os, sys",
                        "from pathlib import Path",
                        "with open(os.environ['OPENRELIX_TEST_ORDER_LOG'], 'a', encoding='utf-8') as fh:",
                        "    fh.write('nightly ' + ' '.join(sys.argv[1:]) + '\\n')",
                        "parser = argparse.ArgumentParser()",
                        "parser.add_argument('--date')",
                        "parser.add_argument('--stage')",
                        "parser.add_argument('--skip-if-unchanged', action='store_true')",
                        "args = parser.parse_args()",
                        "summary_dir = Path(os.environ['OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR']) / args.date",
                        "summary_dir.mkdir(parents=True, exist_ok=True)",
                        "(summary_dir / 'summary.json').write_text(json.dumps({'date': args.date}), encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            (scripts_dir / "openrelix.py").write_text(
                "\n".join(
                    [
                        "import os, sys",
                        "with open(os.environ['OPENRELIX_TEST_ORDER_LOG'], 'a', encoding='utf-8') as fh:",
                        "    fh.write('openrelix ' + ' '.join(sys.argv[1:]) + '\\n')",
                    ]
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR"] = str(consolidated_daily_dir)
            env["OPENRELIX_TEST_ORDER_LOG"] = str(order_log)

            result = subprocess.run(
                ["/bin/zsh", str(pipeline_script), "2026-05-06", "preliminary"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            lines = order_log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("openrelix asset-stats --date 2026-05-06 --no-refresh", lines)
        self.assertLess(
            lines.index("openrelix asset-stats --date 2026-05-06 --no-refresh"),
            lines.index("build_overview "),
        )

    def test_nightly_pipeline_defaults_to_skip_and_consumes_no_skip_flag(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir(parents=True)
            consolidated_daily_dir = root / "consolidated" / "daily"
            record_path = root / "nightly-args.txt"
            pipeline_script = scripts_dir / "nightly_pipeline.sh"
            pipeline_script.write_text(
                (ROOT / "scripts" / "nightly_pipeline.sh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (scripts_dir / "asset_runtime.py").write_text(
                "\n".join(
                    [
                        "import os",
                        "from pathlib import Path",
                        "class RuntimePaths:",
                        "    consolidated_daily_dir = Path(os.environ['OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR'])",
                        "def get_runtime_paths():",
                        "    return RuntimePaths()",
                        "def get_memory_mode(*args, **kwargs):",
                        "    return 'local-only'",
                        "def get_runtime_language(*args, **kwargs):",
                        "    return 'en'",
                    ]
                ),
                encoding="utf-8",
            )
            (scripts_dir / "collect_codex_activity.py").write_text("", encoding="utf-8")
            (scripts_dir / "build_overview.py").write_text("", encoding="utf-8")
            (scripts_dir / "build_codex_memory_summary.py").write_text("", encoding="utf-8")
            (scripts_dir / "sync_host_memory_summary.py").write_text("", encoding="utf-8")
            (scripts_dir / "nightly_consolidate.py").write_text(
                "\n".join(
                    [
                        "import os, sys",
                        "from pathlib import Path",
                        "Path(os.environ['OPENRELIX_TEST_NIGHTLY_ARGS']).write_text(",
                        "    '\\n'.join(sys.argv[1:]),",
                        "    encoding='utf-8',",
                        ")",
                    ]
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR"] = str(consolidated_daily_dir)
            env["OPENRELIX_TEST_NIGHTLY_ARGS"] = str(record_path)

            default_result = subprocess.run(
                ["/bin/zsh", str(pipeline_script), "2026-04-28", "manual"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            default_args = record_path.read_text(encoding="utf-8").splitlines()

            no_skip_result = subprocess.run(
                ["/bin/zsh", str(pipeline_script), "2026-04-28", "manual", "--no-skip-if-unchanged"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            no_skip_args = record_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(default_result.returncode, 0, default_result.stderr)
        self.assertIn("--skip-if-unchanged", default_args)
        self.assertEqual(no_skip_result.returncode, 0, no_skip_result.stderr)
        self.assertNotIn("--skip-if-unchanged", no_skip_args)
        self.assertNotIn("--no-skip-if-unchanged", no_skip_args)

    def test_nightly_pipeline_native_display_polish_defaults_for_chinese_integrated_mode(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir(parents=True)
            consolidated_daily_dir = root / "consolidated" / "daily"
            marker_path = root / "native-display-polish-called"
            pipeline_script = scripts_dir / "nightly_pipeline.sh"
            pipeline_script.write_text(
                (ROOT / "scripts" / "nightly_pipeline.sh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (scripts_dir / "asset_runtime.py").write_text(
                "\n".join(
                    [
                        "import os",
                        "from pathlib import Path",
                        "class RuntimePaths:",
                        "    consolidated_daily_dir = Path(os.environ['OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR'])",
                        "def get_runtime_paths():",
                        "    return RuntimePaths()",
                        "def get_memory_mode(*args, **kwargs):",
                        "    return os.environ.get('OPENRELIX_TEST_MEMORY_MODE', 'integrated')",
                        "def get_runtime_language(*args, **kwargs):",
                        "    return os.environ.get('OPENRELIX_TEST_LANGUAGE', 'zh')",
                    ]
                ),
                encoding="utf-8",
            )
            (scripts_dir / "collect_codex_activity.py").write_text("", encoding="utf-8")
            (scripts_dir / "build_overview.py").write_text("", encoding="utf-8")
            (scripts_dir / "build_codex_memory_summary.py").write_text("", encoding="utf-8")
            (scripts_dir / "sync_host_memory_summary.py").write_text("", encoding="utf-8")
            (scripts_dir / "build_codex_native_display_cache.py").write_text(
                "\n".join(
                    [
                        "import os",
                        "from pathlib import Path",
                        "Path(os.environ['OPENRELIX_TEST_NATIVE_DISPLAY_MARKER']).write_text('called', encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            (scripts_dir / "nightly_consolidate.py").write_text(
                "\n".join(
                    [
                        "import argparse, json, os",
                        "from pathlib import Path",
                        "parser = argparse.ArgumentParser()",
                        "parser.add_argument('--date')",
                        "parser.add_argument('--stage')",
                        "parser.add_argument('--learn-window-days')",
                        "parser.add_argument('--skip-if-unchanged', action='store_true')",
                        "args = parser.parse_args()",
                        "summary_dir = Path(os.environ['OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR']) / args.date",
                        "summary_dir.mkdir(parents=True, exist_ok=True)",
                        "(summary_dir / 'summary.json').write_text(json.dumps({",
                        "    'date': args.date,",
                        "    'last_run_model_status': 'ok',",
                        "}), encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPENRELIX_TEST_CONSOLIDATED_DAILY_DIR"] = str(consolidated_daily_dir)
            env["OPENRELIX_TEST_NATIVE_DISPLAY_MARKER"] = str(marker_path)
            env.pop("OPENRELIX_ENABLE_NATIVE_DISPLAY_POLISH", None)
            env["OPENRELIX_TEST_LANGUAGE"] = "zh"
            env["OPENRELIX_TEST_MEMORY_MODE"] = "integrated"

            default_result = subprocess.run(
                ["/bin/zsh", str(pipeline_script), "2026-04-28", "manual"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(default_result.returncode, 0, default_result.stderr)
            self.assertEqual(marker_path.read_text(encoding="utf-8"), "called")

            marker_path.unlink()
            env["OPENRELIX_ENABLE_NATIVE_DISPLAY_POLISH"] = "0"
            disabled_result = subprocess.run(
                ["/bin/zsh", str(pipeline_script), "2026-04-28", "manual"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(disabled_result.returncode, 0, disabled_result.stderr)
            self.assertFalse(marker_path.exists())

            env["OPENRELIX_ENABLE_NATIVE_DISPLAY_POLISH"] = "1"
            env["OPENRELIX_TEST_MEMORY_MODE"] = "local-only"
            local_only_result = subprocess.run(
                ["/bin/zsh", str(pipeline_script), "2026-04-28", "manual"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(local_only_result.returncode, 0, local_only_result.stderr)
            self.assertFalse(marker_path.exists())

    def test_learning_refresh_install_guidance_and_launchd_env_are_present(self):
        showcase = (ROOT / "docs" / "product-showcase.html").read_text(encoding="utf-8")
        installer = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")
        launchd_template = (
            ROOT / "ops" / "launchd" / "io.github.openrelix.overview-refresh.plist.tmpl"
        ).read_text(encoding="utf-8")

        self.assertIn("npx openrelix@latest install --enable-learning-refresh", showcase)
        self.assertNotIn("npx openrelix install --profile integrated --enable-learning-refresh", showcase)
        self.assertNotIn("npx openrelix install --profile integrated --enable-learning-refresh --read-codex-app", showcase)
        self.assertIn("openrelix backfill --days 7 --stage final --learn-window-days 7 --force", showcase)
        self.assertNotIn(
            '<code class="command-code">openrelix refresh --learn-memory --learn-window-days 7</code>',
            showcase,
        )
        self.assertNotIn("<h3>开启 30 分钟自动学习（推荐）</h3>", showcase)
        self.assertIn("开启 1 小时自动学习", showcase)
        self.assertIn("--enable-learning-refresh", installer)
        self.assertIn("backfill --days %s --stage preliminary --learn-window-days 0 --jobs %s", installer)
        self.assertIn('INSTALL_DEEP_LEARN_JOBS=1', installer)
        self.assertIn('backfill --days "$LEARNING_REFRESH_WINDOW_DAYS" --stage final --learn-window-days "$LEARNING_REFRESH_WINDOW_DAYS" --jobs "$INSTALL_DEEP_LEARN_JOBS" --force', installer)
        self.assertIn("开始串行深度回溯最近 ${LEARNING_REFRESH_WINDOW_DAYS} 天，进度会继续显示在当前终端。", installer)
        self.assertIn("backfills the last ${LEARNING_REFRESH_WINDOW_DAYS} days deeply in this terminal", installer)
        self.assertIn("请手动刷新当前页面或 app", installer)
        self.assertIn("浅度回溯已完成，OpenRelix 现在可以先使用了", installer)
        self.assertIn("Lightweight backfill is complete. OpenRelix is ready to use now", installer)
        self.assertNotIn("深度回溯已在后台启动。日志:", installer)
        self.assertIn("写入可复用压缩层", installer)
        self.assertIn('if [[ -n "$DEEP_LEARN_MEMORY_COMMAND" ]]; then', installer)
        self.assertIn("--no-learn                    Skip the post-install prompt for two-step memory backfill.", installer)
        self.assertIn('INSTALL_PROFILE="integrated"', installer)
        self.assertIn("ENABLE_NIGHTLY=1", installer)
        self.assertIn("Default: integrated", installer)
        self.assertIn('ACTIVITY_SOURCE="${OPENRELIX_ACTIVITY_SOURCE:-${AI_ASSET_ACTIVITY_SOURCE:-auto}}"', installer)
        self.assertIn('ACTIVITY_HOST="${OPENRELIX_ACTIVITY_HOST:-${AI_ASSET_ACTIVITY_HOST:-all}}"', installer)
        self.assertIn('MODEL_CLI="${OPENRELIX_MODEL_CLI:-${AI_ASSET_MODEL_CLI:-}}"', installer)
        self.assertIn("Select model CLI for memory backfill", installer)
        self.assertIn("--model-cli CLI", installer)
        self.assertIn("--claude-home PATH", installer)
        self.assertIn("sync_host_memory_summary.py", installer)
        self.assertIn("Default: auto.", installer)
        self.assertIn("prompt_install_codex_cli_if_needed", installer)
        self.assertIn("Codex App 本身不提供 OpenRelix 记忆回溯需要的命令行能力。", installer)
        self.assertIn("npm install -g @openai/codex@latest", installer)
        self.assertIn("OPENRELIX_ACTIVITY_HOST", launchd_template)
        self.assertIn("OPENRELIX_MODEL_CLI", launchd_template)
        self.assertIn("CLAUDE_HOME", launchd_template)
        self.assertIn("OPENRELIX_REFRESH_LEARN_MEMORY", launchd_template)
        self.assertIn("OPENRELIX_REFRESH_LEARN_WINDOW_DAYS", launchd_template)
        self.assertIn("OPENRELIX_REFRESH_SKIP_UNCHANGED", launchd_template)
        self.assertIn("OPENRELIX_REFRESH_STAGE", launchd_template)
        self.assertIn("preliminary", launchd_template)

    def test_installer_configures_codex_memories_before_host_summary_sync(self):
        installer = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")

        self.assertLess(
            installer.index('localized_text "配置 Codex 用户设置..."'),
            installer.index('localized_text "同步受控的 host 记忆摘要..."'),
        )

    def test_learning_refresh_install_avoids_duplicate_immediate_model_runs(self):
        installer = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")
        launchd_template = (
            ROOT / "ops" / "launchd" / "io.github.openrelix.overview-refresh.plist.tmpl"
        ).read_text(encoding="utf-8")

        self.assertIn('OVERVIEW_RUN_AT_LOAD="<false/>"', installer)
        self.assertIn('"$(( ENABLE_LEARNING_REFRESH ? 0 : 1 ))"', installer)
        self.assertIn("首次自动学习会在下一个 1 小时周期运行", installer)
        self.assertIn("Automatic learning refresh is enabled", installer)
        self.assertIn("__OVERVIEW_RUN_AT_LOAD__", launchd_template)

    def test_installer_boots_out_current_launch_agent_label_before_bootstrap(self):
        installer = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")

        self.assertLess(
            installer.index('launchctl bootout "gui/$(id -u)/$label"'),
            installer.index('launchctl bootout "gui/$(id -u)" "$plist_path"'),
        )
        self.assertLess(
            installer.index('launchctl bootout "gui/$(id -u)" "$plist_path"'),
            installer.index('launchctl bootstrap "gui/$(id -u)" "$plist_path"'),
        )

    def test_integrated_install_defaults_include_nightly_launchagents(self):
        installer = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")
        npm_bin = (ROOT / "install" / "npm-bin.js").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        zh_readme = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

        self.assertIn("Integrated installs enable nightly by default.", installer)
        self.assertIn("ENABLE_BACKGROUND_SERVICES=1\n      ENABLE_NIGHTLY=1", installer)
        self.assertIn("nightly organization LaunchAgents", npm_bin)
        self.assertNotIn("install --enable-nightly --nightly-organize-time", npm_bin)
        self.assertIn("nightly organization LaunchAgents by default", readme)
        self.assertIn("夜间整理 LaunchAgents", zh_readme)
        self.assertIn(
            "./install/install.sh --enable-learning-refresh --keep-awake=during-job --enable-update-check",
            readme,
        )
        self.assertIn(
            "./install/install.sh --enable-learning-refresh --keep-awake=during-job --enable-update-check",
            zh_readme,
        )

    def test_install_entrypoints_forward_interrupts_to_backfill_children(self):
        installer = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")
        npm_bin = (ROOT / "install" / "npm-bin.js").read_text(encoding="utf-8")
        openrelix_cli = (ROOT / "scripts" / "openrelix.py").read_text(encoding="utf-8")

        self.assertIn('const { spawn, spawnSync } = require("node:child_process");', npm_bin)
        self.assertIn('process.on("SIGINT", forwardSignal);', npm_bin)
        self.assertIn('process.on("SIGTERM", forwardSignal);', npm_bin)
        self.assertIn('child.kill(signal);', npm_bin)
        self.assertIn("if (handleUpdate(args.slice(1)))", npm_bin)
        self.assertIn("return true;", npm_bin)

        self.assertIn("INSTALL_CHILD_PID=", installer)
        self.assertIn("trap 'handle_install_signal INT' INT", installer)
        self.assertIn("trap 'handle_install_signal TERM' TERM", installer)
        self.assertIn("trap '' HUP INT TERM", installer)
        self.assertIn("stop_install_child_process", installer)
        self.assertIn("run_interruptible_child \"$PYTHON_BIN\" \"$REPO_ROOT/scripts/openrelix.py\"", installer)
        self.assertIn("local child_status=0", installer)
        self.assertNotIn("local status=0", installer)

        self.assertIn("def install_termination_signal_handlers():", openrelix_cli)
        self.assertIn('for signal_name in ("SIGHUP", "SIGTERM"):', openrelix_cli)
        self.assertIn("process_descendant_pids(process.pid)", openrelix_cli)

    def test_pipeline_status_finish_avoids_zsh_readonly_status_variable(self):
        refresh_script = (ROOT / "scripts" / "refresh_overview.sh").read_text(encoding="utf-8")
        nightly_script = (ROOT / "scripts" / "nightly_pipeline.sh").read_text(encoding="utf-8")

        for script in (refresh_script, nightly_script):
            self.assertIn('local finish_status="completed"', script)
            self.assertIn('--status "$finish_status"', script)
            self.assertNotIn("local status=", script)
            self.assertNotIn('--status "$status"', script)

    def test_pipeline_recent_runs_show_target_and_actual_run_times(self):
        html = build_overview.make_pipeline_recent_runs(
            [
                {
                    "pipeline": "nightly_pipeline",
                    "title": "记忆整理流水线",
                    "title_en": "Memory Synthesis Pipeline",
                    "status": "failed",
                    "target_date": "2026-05-07",
                    "stage": "preliminary",
                    "started_at_iso": "2026-05-07T19:19:28+0800",
                    "ended_at_iso": "2026-05-07T19:20:53+0800",
                }
            ]
        )
        collector = TextCollector()
        collector.feed(html)

        self.assertIn("失败", collector.text)
        self.assertIn("日期 2026-05-07", collector.text)
        self.assertIn("快速回溯", collector.text)
        self.assertNotIn("preliminary", collector.text)
        self.assertIn("触发 05-07 19:19:28", collector.text)
        self.assertIn("结束 05-07 19:20:53", collector.text)
        self.assertIn("Date 2026-05-07", collector.text)
        self.assertIn("Quick backfill", collector.text)
        self.assertIn("Started 05-07 19:19:28", collector.text)
        self.assertIn("Ended 05-07 19:20:53", collector.text)

    def test_pipeline_recent_runs_offer_expand_to_latest_twenty_four(self):
        html = build_overview.make_pipeline_recent_runs(
            [
                {
                    "pipeline": "nightly_pipeline",
                    "title": "run-{}".format(index),
                    "title_en": "run-{}".format(index),
                    "status": "completed",
                    "stage": "final",
                }
                for index in range(35)
            ]
        )

        # Deep backfill uses the same 4-row default cap as before; the rest
        # sit inside a <details> so they only render on demand.
        self.assertIn("run-0", html)
        self.assertIn("run-3", html)
        self.assertIn("查看更早 20 次深度回溯", html)
        self.assertIn("深度回溯（消耗 token）", html)
        details_start = html.find('<details class="pipeline-history-deep-extras">')
        self.assertGreater(details_start, -1)
        run_0_index = html.find("run-0")
        run_3_index = html.find("run-3")
        self.assertLess(run_0_index, details_start)
        self.assertLess(run_3_index, details_start)
        run_4_index = html.find("run-4")
        self.assertGreater(run_4_index, details_start)

    def test_pipeline_status_panel_localizes_stage_labels(self):
        html = build_overview.make_pipeline_status_panel(
            {
                "pipeline": "nightly_pipeline",
                "title": "记忆整理流水线",
                "title_en": "Memory Synthesis Pipeline",
                "status": "running",
                "target_date": "2026-05-07",
                "stage": "preliminary",
                "current_step_index": 3,
                "step_count": 8,
                "next_run": {
                    "title": "前一日终版整理",
                    "title_en": "Previous-day Finalize",
                    "next_at_iso": "2026-05-08T00:10:00+08:00",
                    "stage": "final",
                },
                "steps": [],
                "recent_runs": [],
            }
        )
        collector = TextCollector()
        collector.feed(html)

        self.assertIn("2026-05-07 · 快速回溯", collector.text)
        self.assertIn("2026-05-07 · Quick backfill", collector.text)
        self.assertIn("2026-05-08T00:10:00+08:00 · 完整回溯", collector.text)
        self.assertIn("2026-05-08T00:10:00+08:00 · Full backfill", collector.text)
        self.assertNotIn("preliminary", collector.text)

    def test_installer_chinese_language_uses_chinese_guidance_for_install_steps(self):
        installer = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")
        openrelix_cli = (ROOT / "scripts" / "openrelix.py").read_text(encoding="utf-8")
        mac_client_builder = (ROOT / "scripts" / "build_macos_client.sh").read_text(encoding="utf-8")
        memory_summary_builder = (ROOT / "scripts" / "build_codex_memory_summary.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('localized_text "安装轻量 macOS 客户端..."', installer)
        self.assertIn('localized_text "完成" "done"', installer)
        self.assertIn("源码目录: $REPO_ROOT", installer)
        self.assertIn('INSTALLED_MAC_CLIENT_APP="$USER_APPLICATIONS_DIR/OpenRelix.app"', installer)
        self.assertIn('ditto "$STATE_DIR/runtime/mac-app/OpenRelix.app" "$INSTALLED_MAC_CLIENT_APP"', installer)
        self.assertNotIn('step "Installing the lightweight macOS client..."', installer)
        self.assertNotIn('ln -sfn "$STATE_DIR/runtime/mac-app/OpenRelix.app"', installer)

        self.assertIn('Path.home() / "Applications" / MACOS_CLIENT_APP_NAME', openrelix_cli)
        self.assertIn("def sync_macos_client_app(source, destination):", openrelix_cli)
        self.assertIn('"Output path for the .app bundle; default is ~/Applications/OpenRelix.app."', openrelix_cli)

        self.assertIn("normalize_language_code()", mac_client_builder)
        self.assertIn('localized_text "已构建" "Built"', mac_client_builder)
        self.assertIn('localized_text "状态目录" "State root"', mac_client_builder)
        self.assertNotIn('echo "Built $OUTPUT_PATH"', mac_client_builder)
        self.assertNotIn('echo "State root $STATE_ROOT"', mac_client_builder)
        self.assertIn("已跳过：未找到记忆索引、已有摘要或个人记忆登记册", memory_summary_builder)

    def test_macos_client_under_page_background_tracks_web_theme(self):
        mac_client = (ROOT / "macos" / "OpenRelixClient" / "main.swift").read_text(
            encoding="utf-8"
        )

        self.assertIn("panelThemeBridgeScript", mac_client)
        self.assertIn("WKScriptMessageHandler", mac_client)
        self.assertIn("openrelixTheme", mac_client)
        self.assertIn("openrelixOpenExternal", mac_client)
        self.assertIn("openrelixEnsureTokenLive", mac_client)
        self.assertIn("ensureTokenLiveLaunchAgent()", mac_client)
        self.assertIn('["kickstart", "-k", serviceTarget]', mac_client)
        self.assertIn("webView?.underPageBackgroundColor = background", mac_client)
        self.assertIn("window?.backgroundColor = background", mac_client)
        self.assertNotIn("private let defaultBackground", mac_client)

    def test_macos_client_opens_external_panel_links_outside_webview(self):
        mac_client = (ROOT / "macos" / "OpenRelixClient" / "main.swift").read_text(
            encoding="utf-8"
        )

        self.assertIn("WKNavigationDelegate", mac_client)
        self.assertIn("WKUIDelegate", mac_client)
        self.assertIn("webView.navigationDelegate = self", mac_client)
        self.assertIn("webView.uiDelegate = self", mac_client)
        self.assertIn("navigationAction.targetFrame == nil", mac_client)
        self.assertIn('message.name == "openrelixOpenExternal"', mac_client)
        self.assertIn("let url = URL(string: rawURL)", mac_client)
        self.assertIn("openOutsidePanel(_ url: URL)", mac_client)
        self.assertIn("NSWorkspace.shared.open(url)", mac_client)
        self.assertIn("url.isFileURL && isPanelURL(url)", mac_client)
        self.assertIn("decisionHandler(.cancel)", mac_client)

    def test_macos_client_has_privacy_bounded_panel_analytics(self):
        mac_client = (ROOT / "macos" / "OpenRelixClient" / "main.swift").read_text(
            encoding="utf-8"
        )
        panel_builder = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        zh_readme = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        mac_client_builder = (ROOT / "scripts" / "build_macos_client.sh").read_text(
            encoding="utf-8"
        )
        privacy_model = (ROOT / "docs" / "privacy-threat-model.md").read_text(encoding="utf-8")
        zh_privacy_model = (ROOT / "docs" / "privacy-threat-model.zh-CN.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("OPENRELIX_ANALYTICS_ENDPOINT", mac_client)
        self.assertIn("OPENRELIX_ANALYTICS_ENABLED", mac_client)
        self.assertIn("OPENRELIX_ANALYTICS_DISABLED", mac_client)
        self.assertIn("openrelixAnalytics", mac_client)
        self.assertIn("panelUsageAnalyticsScript", mac_client)
        self.assertIn('"app_launch"', mac_client)
        self.assertIn('"module_visible"', mac_client)
        self.assertIn('"module_hidden"', mac_client)
        self.assertIn('"control_click"', mac_client)
        self.assertIn('"skill_quarantine_action"', mac_client)
        self.assertIn('"nightly_summary"', mac_client)
        self.assertIn('"personal_asset_memory"', mac_client)
        self.assertIn('"skill_quarantine"', mac_client)
        self.assertIn('"skill_quarantine_delete_confirm"', mac_client)
        self.assertIn("allowedSkillQuarantineActions", mac_client)
        self.assertIn("skill_quarantine_warning_count", mac_client)
        self.assertIn('postPanelAnalyticsEvent("skill_quarantine_action"', panel_builder)
        self.assertIn("allowedModules", mac_client)
        self.assertIn("allowedControls", mac_client)
        self.assertIn("does not send window titles", mac_client)
        self.assertIn("Share Anonymous Usage Metrics", mac_client)
        self.assertIn('id="usage-events-section"', panel_builder)
        self.assertIn("--analytics-endpoint", mac_client_builder)
        self.assertIn("OpenRelixAnalyticsEndpoint.txt", mac_client_builder)
        self.assertIn("OPENRELIX_ANALYTICS_ENDPOINT", readme)
        self.assertIn("--analytics-endpoint <url>", readme)
        self.assertIn("Share Anonymous Usage", readme)
        self.assertIn("Metrics", readme)
        self.assertIn("不会上报 prompt", zh_readme)
        self.assertIn("Product Analytics Policy", privacy_model)
        self.assertIn("产品埋点外发", zh_privacy_model)

    def test_installer_openrelix_templates_exist_and_use_new_entrypoints(self):
        expected_templates = [
            ROOT / "install" / "templates" / "bin" / "openrelix.tmpl",
            ROOT / "ops" / "launchd" / "io.github.openrelix.overview-refresh.plist.tmpl",
            ROOT / "ops" / "launchd" / "io.github.openrelix.token-live.plist.tmpl",
            ROOT / "ops" / "launchd" / "io.github.openrelix.nightly-organize.plist.tmpl",
            ROOT / "ops" / "launchd" / "io.github.openrelix.nightly-finalize-previous-day.plist.tmpl",
        ]

        for template in expected_templates:
            self.assertTrue(template.exists(), str(template))

        command_template = expected_templates[0].read_text(encoding="utf-8")
        self.assertIn("scripts/openrelix.py", command_template)
        self.assertIn("OPENRELIX_ACTIVITY_SOURCE", command_template)

        token_live_template = expected_templates[2].read_text(encoding="utf-8")
        self.assertIn("<key>PATH</key>", token_live_template)
        self.assertIn("__SAFE_PATH__", token_live_template)

    def test_openrelix_uninstall_command_is_exposed_through_cli_and_npm(self):
        openrelix_cli = (ROOT / "scripts" / "openrelix.py").read_text(encoding="utf-8")
        npm_bin = (ROOT / "install" / "npm-bin.js").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        zh_readme = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

        self.assertIn('"uninstall"', openrelix_cli)
        self.assertIn("--delete-local-memory", openrelix_cli)
        self.assertIn("--keep-local-memory", openrelix_cli)
        self.assertIn("command_uninstall(args)", openrelix_cli)
        self.assertIn('command === "uninstall"', npm_bin)
        self.assertIn("npx openrelix uninstall --delete-local-memory", readme)
        self.assertIn("npx openrelix uninstall --delete-local-memory", zh_readme)

    def test_openrelix_uninstall_removes_only_codex_managed_memory_block(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = replace(
                openrelix.PATHS,
                codex_home=root / "codex-home",
            )
            summary_path = paths.codex_home / "memories" / "memory_summary.md"
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text(
                "## User Profile\n\n"
                "Native Codex memory.\n\n"
                + sync_host_memory_summary.managed_codex_block("## What's in Memory\n\n- Shared item\n"),
                encoding="utf-8",
            )
            actions = []

            with mock.patch.object(openrelix, "PATHS", paths):
                openrelix.remove_codex_memory_summary_for_uninstall(actions)

            self.assertEqual(actions[0]["status"], "removed")
            self.assertEqual(actions[0]["detail"], "managed block")
            self.assertTrue(summary_path.exists())
            updated = summary_path.read_text(encoding="utf-8")
            self.assertIn("Native Codex memory.", updated)
            self.assertNotIn(openrelix.CLAUDE_MANAGED_MEMORY_START, updated)
            self.assertNotIn("- Shared item", updated)

    def test_openrelix_uninstall_keeps_unmanaged_codex_native_memory(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = replace(
                openrelix.PATHS,
                codex_home=root / "codex-home",
            )
            summary_path = paths.codex_home / "memories" / "memory_summary.md"
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text("## User Profile\n\nNative Codex memory.\n", encoding="utf-8")
            actions = []

            with mock.patch.object(openrelix, "PATHS", paths):
                openrelix.remove_codex_memory_summary_for_uninstall(actions)

            self.assertEqual(actions[0]["status"], "kept")
            self.assertEqual(actions[0]["detail"], "no OpenRelix managed block")
            self.assertTrue(summary_path.exists())
            self.assertIn("Native Codex memory.", summary_path.read_text(encoding="utf-8"))

    def test_sqlite_index_is_exposed_in_cli_npm_and_package(self):
        openrelix_cli = (ROOT / "scripts" / "openrelix.py").read_text(encoding="utf-8")
        npm_bin = (ROOT / "install" / "npm-bin.js").read_text(encoding="utf-8")
        package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

        self.assertIn('"index"', openrelix_cli)
        self.assertIn('choices=["status", "rebuild", "search-memory", "search-window"]', openrelix_cli)
        self.assertIn("command_index(args)", openrelix_cli)
        self.assertIn('command === "index"', npm_bin)
        self.assertIn('runPythonCli(["index", ...args.slice(1)])', npm_bin)
        self.assertIn("npx openrelix index status", npm_bin)
        self.assertIn('"recall"', openrelix_cli)
        self.assertIn("command_recall(args)", openrelix_cli)
        self.assertIn('command === "recall"', npm_bin)
        self.assertIn('runPythonCli(["recall", ...args.slice(1)])', npm_bin)
        self.assertIn("npx openrelix recall", npm_bin)
        self.assertIn("scripts/openrelix_index.py", package_json["files"])
        self.assertIn("npx openrelix refresh", npm_bin)
        self.assertIn("npx openrelix tokens --provider all", npm_bin)
        self.assertIn("runPythonCli(args)", npm_bin)
        self.assertIn('command === "models"', npm_bin)
        self.assertIn('runPythonCli(["models", ...args.slice(1)])', npm_bin)
        self.assertIn("npx openrelix models", npm_bin)
        self.assertIn('command === "memory-migration"', npm_bin)
        self.assertIn('runPythonCli(["memory-migration", ...args.slice(1)])', npm_bin)
        self.assertIn("npx openrelix memory-migration status", npm_bin)
        self.assertIn('command === "task-summary-migration"', npm_bin)
        self.assertIn('runPythonCli(["task-summary-migration", ...args.slice(1)])', npm_bin)
        self.assertIn("npx openrelix task-summary-migration status", npm_bin)
        self.assertIn('"context"', openrelix_cli)
        self.assertIn("command_context(args)", openrelix_cli)
        self.assertIn('command === "context"', npm_bin)
        self.assertIn('runPythonCli(["context", ...args.slice(1)])', npm_bin)
        self.assertIn("npx openrelix context sync", npm_bin)
        self.assertIn("scripts/openrelix_memory_migration.py", package_json["files"])
        self.assertIn("scripts/openrelix_task_summary.py", package_json["files"])
        self.assertIn("templates/", package_json["files"])
        self.assertTrue((ROOT / "templates" / "window-task-summary-schema.json").exists())

    def test_window_task_summary_schema_is_strict_codex_response_schema(self):
        schema = json.loads((ROOT / "templates" / "window-task-summary-schema.json").read_text(encoding="utf-8"))

        self.assertEqual(sorted(schema["required"]), sorted(schema["properties"].keys()))
        cluster_schema = schema["properties"]["project_task_clusters"]["items"]
        self.assertEqual(sorted(cluster_schema["required"]), sorted(cluster_schema["properties"].keys()))
        self.assertNotIn("source_fingerprint", schema["properties"])
        self.assertNotIn("source_window_ids", schema["properties"])
        self.assertNotIn("model_status", schema["properties"])

    def test_sqlite_index_rebuild_is_warning_only_in_refresh_scripts(self):
        nightly = (ROOT / "scripts" / "nightly_pipeline.sh").read_text(encoding="utf-8")
        refresh = (ROOT / "scripts" / "refresh_overview.sh").read_text(encoding="utf-8")

        for script in (nightly, refresh):
            self.assertIn("rebuild_sqlite_index_if_available", script)
            self.assertIn("OPENRELIX_DISABLE_SQLITE_INDEX_REBUILD", script)
            self.assertIn("openrelix_index.py", script)
            self.assertIn("JSONL/raw outputs remain authoritative", script)
            self.assertIn("if ! \"$PYTHON_BIN\" \"$REPO_ROOT/scripts/openrelix_index.py\" rebuild >/dev/null; then", script)

    def test_task_summary_generation_honors_disable_flag_in_refresh_and_nightly(self):
        nightly = (ROOT / "scripts" / "nightly_pipeline.sh").read_text(encoding="utf-8")
        refresh = (ROOT / "scripts" / "refresh_overview.sh").read_text(encoding="utf-8")

        for script in (nightly, refresh):
            self.assertIn("OPENRELIX_DISABLE_TASK_SUMMARY_MIGRATION", script)

        self.assertIn("openrelix_task_summary.py", nightly)
        self.assertIn("task-summary-migration", refresh)
        self.assertIn("run_task_summary_if_enabled", nightly)
        self.assertIn("case \"${disabled:l}\" in", nightly)
        self.assertIn("1|true|yes|on)", nightly)
        self.assertIn("run_task_summary_if_enabled", nightly.split("if [[ \"$defer_global_refresh\" != \"1\" ]]; then", 1)[1])

    def test_uninstall_local_memory_delete_has_dry_run_and_repo_guard(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state_root = tmp / "state"
            codex_home = tmp / "codex"
            claude_home = tmp / "claude"
            state_root.mkdir()
            (state_root / "registry").mkdir()
            (state_root / "registry" / "memory_items.jsonl").write_text("{}", encoding="utf-8")
            (codex_home / "memories").mkdir(parents=True)
            (codex_home / "memories" / "memory_summary.md").write_text(
                sync_host_memory_summary.managed_codex_block("## What's in Memory\n\n- managed item\n"),
                encoding="utf-8",
            )
            claude_home.mkdir(parents=True)
            claude_file = claude_home / "CLAUDE.md"
            claude_file.write_text(
                "\n".join(
                    [
                        "# User Claude Notes",
                        "",
                        openrelix.CLAUDE_MANAGED_MEMORY_START,
                        "managed OpenRelix memory",
                        openrelix.CLAUDE_MANAGED_MEMORY_END,
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            paths = replace(
                openrelix.PATHS,
                state_root=state_root,
                codex_home=codex_home,
                claude_home=claude_home,
            )

            actions = []
            with mock.patch.object(openrelix, "PATHS", paths), mock.patch.object(
                openrelix,
                "local_memory_roots_for_uninstall",
                return_value=[state_root],
            ):
                openrelix.remove_local_memory_for_uninstall(actions, dry_run=True)

            self.assertTrue(state_root.exists())
            self.assertTrue((codex_home / "memories" / "memory_summary.md").exists())
            self.assertTrue(claude_file.exists())
            self.assertEqual([item["status"] for item in actions], ["would_remove", "would_remove", "would_remove"])

            actions = []
            with mock.patch.object(openrelix, "PATHS", paths), mock.patch.object(
                openrelix,
                "local_memory_roots_for_uninstall",
                return_value=[state_root],
            ):
                openrelix.remove_local_memory_for_uninstall(actions, dry_run=False)

            self.assertFalse(state_root.exists())
            self.assertFalse((codex_home / "memories" / "memory_summary.md").exists())
            self.assertTrue(claude_file.exists())
            self.assertIn("# User Claude Notes", claude_file.read_text(encoding="utf-8"))
            self.assertNotIn(openrelix.CLAUDE_MANAGED_MEMORY_START, claude_file.read_text(encoding="utf-8"))

        actions = []
        paths = replace(
            openrelix.PATHS,
            state_root=ROOT,
            codex_home=Path("/tmp/openrelix-codex-home"),
            claude_home=Path("/tmp/openrelix-claude-home"),
        )
        with mock.patch.object(openrelix, "PATHS", paths), mock.patch.object(
            openrelix,
            "local_memory_roots_for_uninstall",
            return_value=[ROOT],
        ):
            openrelix.remove_local_memory_for_uninstall(actions, dry_run=False)
        self.assertEqual(actions[0]["status"], "blocked")
        self.assertIn("protected root", actions[0]["detail"])

    def test_uninstall_removes_only_managed_shell_path_block(self):
        original = "\n".join(
            [
                "export KEEP=1",
                "# >>> openrelix >>>",
                'export PATH="/tmp/openrelix-bin:$PATH"',
                "# <<< openrelix <<<",
                "export AFTER=1",
            ]
        ) + "\n"

        updated, removed = openrelix.strip_managed_shell_path_block(original)

        self.assertTrue(removed)
        self.assertIn("export KEEP=1", updated)
        self.assertIn("export AFTER=1", updated)
        self.assertNotIn("openrelix-bin", updated)

    def test_learning_window_dates_are_chronological(self):
        self.assertEqual(
            openrelix.learning_window_dates("2026-04-27", 7),
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
                openrelix,
                "PATHS",
                replace(openrelix.PATHS, raw_daily_dir=raw_daily_dir, codex_home=codex_home),
            ), mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir):
                self.assertEqual(
                    openrelix.resolve_learning_backfill_dates("2026-04-27", 7),
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

            def fake_backfill_dates(
                dates,
                stage,
                learn_window_days=0,
                force=False,
                ensure_learning_final=True,
                defer_global_refresh=False,
                verbose=True,
                jobs=1,
            ):
                calls.append(
                    (
                        "backfill",
                        dates,
                        stage,
                        learn_window_days,
                        force,
                        ensure_learning_final,
                        defer_global_refresh,
                        verbose,
                        jobs,
                    )
                )
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
                jobs=2,
            )

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "resolve_learning_backfill_dates",
                return_value=["2026-04-20", "2026-04-21"],
            ), mock.patch.object(
                openrelix,
                "run_backfill_dates",
                side_effect=fake_backfill_dates,
            ), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch.object(
                openrelix,
                "sync_review_outputs",
            ), mock.patch("sys.stdout", new_callable=io.StringIO):
                openrelix.command_review(args)

            self.assertEqual(calls[0][0], "backfill")
            self.assertEqual(calls[0][1], ["2026-04-20", "2026-04-21"])
            self.assertEqual(calls[0][2], "final")
            self.assertEqual(calls[0][3], 0)
            self.assertIs(calls[0][4], False)
            self.assertIs(calls[0][5], False)
            self.assertIs(calls[0][6], True)
            self.assertEqual(calls[0][8], 2)
            self.assertEqual(calls[1][0], "review")

    def test_backfill_reruns_existing_lower_stage_summary(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            summary_dir = consolidated_daily_dir / "2026-04-23"
            summary_dir.mkdir(parents=True)
            (summary_dir / "summary.json").write_text(
                json.dumps({"date": "2026-04-23", "stage": "manual"}),
                encoding="utf-8",
            )
            calls = []

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                calls.append(cmd)

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch("sys.stdout", new_callable=io.StringIO):
                results = openrelix.run_backfill_dates(
                    ["2026-04-23"],
                    "final",
                    learn_window_days=0,
                    force=False,
                    verbose=True,
                )

            self.assertEqual(results[0]["status"], "completed")
            self.assertEqual(results[0]["reason"], "existing_stage_below_requested")
            self.assertEqual(calls[0][-3:], ["2026-04-23", "final", "--skip-if-unchanged"])

    def test_backfill_final_reuses_existing_lightweight_layer_when_available(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            paths = make_runtime_paths_for_test(tmp)
            consolidated_daily_dir = paths.consolidated_daily_dir
            summary_dir = consolidated_daily_dir / "2026-04-23"
            summary_dir.mkdir(parents=True)
            paths.raw_daily_dir.mkdir(parents=True)
            (paths.raw_daily_dir / "2026-04-23.json").write_text(
                json.dumps({"date": "2026-04-23", "window_count": 1}),
                encoding="utf-8",
            )
            (summary_dir / "compact_payload.json").write_text(
                json.dumps({"version": 1}),
                encoding="utf-8",
            )
            (summary_dir / "summary.json").write_text(
                json.dumps({"date": "2026-04-23", "stage": "preliminary"}),
                encoding="utf-8",
            )
            calls = []

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                calls.append(cmd)

            with mock.patch.object(openrelix, "PATHS", paths), mock.patch.object(
                openrelix,
                "CONSOLIDATED_DAILY_DIR",
                consolidated_daily_dir,
            ), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch("sys.stdout", new_callable=io.StringIO):
                results = openrelix.run_backfill_dates(
                    ["2026-04-23"],
                    "final",
                    learn_window_days=0,
                    force=False,
                    verbose=True,
                )

            self.assertEqual(results[0]["status"], "completed")
            self.assertIn("--reuse-lightweight", calls[0])

    def test_backfill_force_disables_unchanged_skip_for_pipeline(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            summary_dir = consolidated_daily_dir / "2026-04-23"
            summary_dir.mkdir(parents=True)
            (summary_dir / "summary.json").write_text(
                json.dumps({"date": "2026-04-23", "stage": "final"}),
                encoding="utf-8",
            )
            calls = []

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                calls.append(cmd)

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch("sys.stdout", new_callable=io.StringIO):
                results = openrelix.run_backfill_dates(
                    ["2026-04-23"],
                    "final",
                    learn_window_days=0,
                    force=True,
                    verbose=True,
                )

            self.assertEqual(results[0]["status"], "completed")
            self.assertEqual(results[0]["reason"], "force")
            self.assertEqual(calls[0][-3:], ["2026-04-23", "final", "--no-skip-if-unchanged"])

    def test_backfill_final_precollects_learning_once_and_defers_pipeline_refresh(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            quiet_calls = []
            pipeline_calls = []

            def fake_run_checked_quiet(cmd):
                quiet_calls.append(cmd)

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                pipeline_calls.append(cmd)

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "resolve_learning_backfill_dates_for_targets",
                return_value=[],
            ), mock.patch.object(
                openrelix,
                "run_checked_quiet",
                side_effect=fake_run_checked_quiet,
            ), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ):
                results = openrelix.run_backfill_dates(
                    ["2026-04-28", "2026-04-29"],
                    "final",
                    learn_window_days=2,
                    force=False,
                    ensure_learning_final=True,
                    defer_global_refresh=True,
                    verbose=False,
                )

            self.assertEqual([item["status"] for item in results], ["completed", "completed"])
            collected_dates = [
                call[call.index("--date") + 1]
                for call in quiet_calls
                if str(openrelix.COLLECT_CODEX_ACTIVITY_SCRIPT) in call
            ]
            self.assertEqual(collected_dates, ["2026-04-26", "2026-04-27"])
            self.assertEqual(len(pipeline_calls), 2)
            for command in pipeline_calls:
                self.assertIn("--defer-global-refresh", command)
                self.assertIn("--skip-learning-collect", command)

    def test_raw_history_hydration_collects_missing_or_nonfinal_daily_files_once(self):
        with TemporaryDirectory() as tmpdir:
            paths = make_runtime_paths_for_test(tmpdir)
            paths.raw_daily_dir.mkdir(parents=True)
            (paths.raw_daily_dir / "2026-05-20.json").write_text(
                json.dumps({"date": "2026-05-20", "stage": "final"}),
                encoding="utf-8",
            )
            (paths.raw_daily_dir / "2026-05-21.json").write_text(
                json.dumps({"date": "2026-05-21", "stage": "manual"}),
                encoding="utf-8",
            )
            calls = []

            with mock.patch.object(openrelix, "PATHS", paths), mock.patch.object(
                openrelix,
                "run_checked_quiet",
                side_effect=lambda cmd: calls.append(cmd),
            ):
                results = openrelix.hydrate_raw_history_windows(["2026-05-22"], days=3, verbose=False)

            self.assertEqual([item["date"] for item in results], ["2026-05-21", "2026-05-22"])
            self.assertEqual([item["status"] for item in results], ["completed", "completed"])
            self.assertEqual([call[call.index("--date") + 1] for call in calls], ["2026-05-21", "2026-05-22"])
            self.assertTrue(all(call[call.index("--stage") + 1] == "final" for call in calls))
            self.assertTrue(all("--learn-window-days" not in call for call in calls))

    def test_backfill_jobs_parallelize_deferred_independent_dates(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            pipeline_calls = []

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                pipeline_calls.append(cmd)

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch("sys.stdout", new_callable=io.StringIO):
                results = openrelix.run_backfill_dates(
                    ["2026-04-28", "2026-04-29"],
                    "preliminary",
                    learn_window_days=0,
                    force=False,
                    ensure_learning_final=True,
                    defer_global_refresh=True,
                    verbose=True,
                    jobs=9,
                )

            self.assertEqual([item["date"] for item in results], ["2026-04-28", "2026-04-29"])
            self.assertEqual(len(pipeline_calls), 2)
            self.assertEqual(openrelix.normalize_backfill_jobs(9), 2)
            self.assertEqual(openrelix.effective_backfill_jobs("preliminary", 9), 2)
            self.assertEqual(openrelix.effective_backfill_jobs("final", 9), 1)
            for command in pipeline_calls:
                self.assertIn("--defer-global-refresh", command)
                self.assertIn("--skip-if-unchanged", command)

    def test_backfill_final_with_learning_runs_serially_after_shared_precollection(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            pipeline_calls = []
            precollect_calls = []

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                pipeline_calls.append(cmd)

            def fake_precollect(date_strs, learn_window_days, verbose=True):
                precollect_calls.append((date_strs, learn_window_days, verbose))
                return []

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "resolve_learning_backfill_dates_for_targets",
                return_value=[],
            ), mock.patch.object(
                openrelix,
                "precollect_learning_window_sources",
                side_effect=fake_precollect,
            ), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                results = openrelix.run_backfill_dates(
                    ["2026-04-28", "2026-04-29"],
                    "final",
                    learn_window_days=7,
                    force=False,
                    ensure_learning_final=True,
                    defer_global_refresh=True,
                    verbose=True,
                    jobs=2,
                )

            self.assertEqual([item["date"] for item in results], ["2026-04-28", "2026-04-29"])
            self.assertEqual(precollect_calls, [(["2026-04-28", "2026-04-29"], 7, True)])
            self.assertNotIn("并发回溯: jobs=2", stdout.getvalue())
            self.assertEqual(len(pipeline_calls), 2)
            for command in pipeline_calls:
                self.assertIn("--defer-global-refresh", command)
                self.assertIn("--skip-learning-collect", command)
                self.assertIn("--learn-window-days", command)

    def test_progress_runner_stops_child_tree_on_keyboard_interrupt(self):
        class InterruptingProcess:
            pid = 12345
            returncode = None

            def communicate(self, timeout=None):
                raise KeyboardInterrupt

        process = InterruptingProcess()

        with mock.patch.object(openrelix.subprocess, "Popen", return_value=process), mock.patch.object(
            openrelix,
            "stop_child_process_tree",
        ) as stop_child:
            with self.assertRaises(KeyboardInterrupt):
                openrelix.run_checked_with_progress(["demo"], [], interval_seconds=1)

        stop_child.assert_called_once_with(process)
        self.assertNotIn(process, openrelix._ACTIVE_CHILD_PROCESSES)

    def test_quiet_runner_stops_child_tree_on_keyboard_interrupt(self):
        class InterruptingProcess:
            pid = 12345
            returncode = None

            def communicate(self):
                raise KeyboardInterrupt

        process = InterruptingProcess()

        with mock.patch.object(openrelix.subprocess, "Popen", return_value=process), mock.patch.object(
            openrelix,
            "stop_child_process_tree",
        ) as stop_child:
            with self.assertRaises(KeyboardInterrupt):
                openrelix.run_capture_interruptible(["demo"])

        stop_child.assert_called_once_with(process)
        self.assertNotIn(process, openrelix._ACTIVE_CHILD_PROCESSES)

    def test_send_signal_to_child_tree_signals_descendant_process_groups(self):
        class RunningProcess:
            pid = 100

            def poll(self):
                return None

            def kill(self):
                raise AssertionError("process.kill should not be needed")

        ps_output = "\n".join(
            [
                "  100     1",
                "  101   100",
                "  102   101",
                "  103   100",
            ]
        )
        pgids = {
            100: 100,
            101: 101,
            102: 101,
            103: 103,
        }

        with mock.patch.object(openrelix.subprocess, "check_output", return_value=ps_output), mock.patch.object(
            openrelix.os,
            "getpgrp",
            return_value=999,
        ), mock.patch.object(
            openrelix.os,
            "getpgid",
            side_effect=lambda pid: pgids[pid],
        ), mock.patch.object(
            openrelix.os,
            "killpg",
        ) as killpg, mock.patch.object(
            openrelix.os,
            "kill",
        ) as kill:
            openrelix.send_signal_to_child_tree(RunningProcess(), openrelix.signal.SIGTERM)

        self.assertEqual(
            {call.args for call in killpg.call_args_list},
            {
                (100, openrelix.signal.SIGTERM),
                (101, openrelix.signal.SIGTERM),
                (103, openrelix.signal.SIGTERM),
            },
        )
        kill.assert_not_called()

    def test_termination_signal_handler_stops_children_and_exits_with_signal_code(self):
        with mock.patch.object(openrelix, "stop_active_child_processes") as stop_children:
            with self.assertRaises(SystemExit) as raised:
                openrelix.stop_active_child_processes_for_signal(openrelix.signal.SIGTERM, None)

        stop_children.assert_called_once_with()
        self.assertEqual(raised.exception.code, 143)

    def test_stop_child_process_tree_escalates_when_descendant_group_remains_alive(self):
        class ExitingParentProcess:
            pid = 100

            def poll(self):
                return None

            def wait(self, timeout=None):
                return 0

            def kill(self):
                raise AssertionError("process.kill should not be needed")

        process_groups = {100, 101}
        individual_pids = set()

        with mock.patch.object(
            openrelix,
            "child_signal_targets",
            return_value=(process_groups, individual_pids),
        ), mock.patch.object(
            openrelix,
            "signal_child_targets",
        ) as signal_targets, mock.patch.object(
            openrelix,
            "child_targets_alive",
            side_effect=[True, False],
        ):
            openrelix.stop_child_process_tree(ExitingParentProcess())

        self.assertEqual(
            [call.args[2] for call in signal_targets.call_args_list],
            [openrelix.signal.SIGINT, openrelix.signal.SIGTERM],
        )

    def test_backfill_records_failed_pipeline_result(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            pipeline_calls = []

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                pipeline_calls.append(cmd)
                raise subprocess.CalledProcessError(2, cmd)

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch("sys.stdout", new_callable=io.StringIO):
                results = openrelix.run_backfill_dates(
                    ["2026-04-28"],
                    "final",
                    learn_window_days=0,
                    force=False,
                    ensure_learning_final=True,
                    defer_global_refresh=True,
                    verbose=True,
                    jobs=1,
                )

            self.assertEqual(len(pipeline_calls), 1)
            self.assertEqual(results[0]["status"], "failed")
            self.assertEqual(results[0]["returncode"], 2)
            self.assertEqual(results[0]["date"], "2026-04-28")

    def test_backfill_skips_existing_same_stage_summary(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            summary_dir = consolidated_daily_dir / "2026-04-23"
            summary_dir.mkdir(parents=True)
            (summary_dir / "summary.json").write_text(
                json.dumps({"date": "2026-04-23", "stage": "final"}),
                encoding="utf-8",
            )

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
            ) as run_pipeline, mock.patch("sys.stdout", new_callable=io.StringIO):
                results = openrelix.run_backfill_dates(
                    ["2026-04-23"],
                    "final",
                    learn_window_days=0,
                    force=False,
                    verbose=True,
                )

            self.assertEqual(results[0]["status"], "skipped_existing")
            self.assertEqual(results[0]["reason"], "existing_stage_satisfies_request")
            run_pipeline.assert_not_called()

    def test_command_backfill_defers_and_refreshes_global_outputs_once(self):
        calls = []

        def fake_run_backfill_dates(
            dates,
            stage,
            learn_window_days=0,
            force=False,
            ensure_learning_final=True,
            defer_global_refresh=False,
            verbose=True,
            jobs=1,
        ):
            calls.append(
                (
                    "backfill",
                    dates,
                    stage,
                    learn_window_days,
                    force,
                    ensure_learning_final,
                    defer_global_refresh,
                    verbose,
                    jobs,
                )
            )
            return [
                {
                    "date": "2026-04-28",
                    "status": "completed",
                    "summary_json": "",
                    "summary_md": "",
                },
                {
                    "date": "2026-04-29",
                    "status": "skipped_existing",
                    "summary_json": "",
                    "summary_md": "",
                },
            ]

        args = argparse.Namespace(
            dates="2026-04-28,2026-04-29",
            date_from=None,
            date_to="2026-04-29",
            days=0,
            stage="final",
            learn_window_days=7,
            force=False,
            json=False,
            jobs=2,
        )

        with mock.patch.object(
            openrelix,
            "run_backfill_dates",
            side_effect=fake_run_backfill_dates,
        ), mock.patch.object(
            openrelix,
            "hydrate_raw_history_windows",
            side_effect=lambda dates, verbose=True: calls.append(("hydrate", dates, verbose)) or [],
        ), mock.patch.object(
            openrelix,
            "sync_review_outputs",
            side_effect=lambda **kwargs: calls.append(("refresh", kwargs)),
        ), mock.patch("sys.stdout", new_callable=io.StringIO):
            openrelix.command_backfill(args)

        self.assertEqual(calls[0][0], "backfill")
        self.assertIs(calls[0][6], True)
        self.assertEqual(calls[0][8], 2)
        self.assertEqual(calls[1], ("hydrate", ["2026-04-28", "2026-04-29"], True))
        self.assertEqual(calls[2], ("refresh", {"include_index": True, "include_native_display": True, "verbose": True}))

    def test_backfill_final_refresh_progress_sets_user_expectations(self):
        source = (ROOT / "scripts" / "openrelix.py").read_text(encoding="utf-8")

        self.assertIn("这一步可能需要几分钟", source)
        self.assertIn("刷新提示: 最后同步会更新搜索索引、host context 摘要和面板", source)
        self.assertIn("仍在刷新: 已等待约 {} 分钟", source)
        self.assertIn("请手动刷新当前页面或 app", source)

    def test_command_backfill_syncs_outputs_when_only_raw_history_hydrates(self):
        calls = []

        args = argparse.Namespace(
            dates="2026-05-22",
            date_from=None,
            date_to="2026-05-22",
            days=0,
            stage="final",
            learn_window_days=0,
            force=False,
            json=False,
            jobs=1,
        )

        with mock.patch.object(
            openrelix,
            "run_backfill_dates",
            return_value=[
                {
                    "date": "2026-05-22",
                    "status": "skipped_existing",
                    "summary_json": "",
                    "summary_md": "",
                }
            ],
        ), mock.patch.object(
            openrelix,
            "hydrate_raw_history_windows",
            side_effect=lambda dates, verbose=True: calls.append(("hydrate", dates, verbose)) or [
                {"date": "2026-05-21", "status": "completed", "stage": "final"}
            ],
        ), mock.patch.object(
            openrelix,
            "sync_review_outputs",
            side_effect=lambda **kwargs: calls.append(("refresh", kwargs)),
        ), mock.patch("sys.stdout", new_callable=io.StringIO):
            openrelix.command_backfill(args)

        self.assertEqual(calls[0], ("hydrate", ["2026-05-22"], True))
        self.assertEqual(calls[1], ("refresh", {"include_index": True, "include_native_display": True, "verbose": True}))

    def test_preliminary_backfill_tells_user_it_is_ready_to_use(self):
        args = argparse.Namespace(
            dates="2026-04-28",
            date_from=None,
            date_to="2026-04-28",
            days=0,
            stage="preliminary",
            learn_window_days=0,
            force=False,
            json=False,
            jobs=1,
        )
        with mock.patch.object(
            openrelix,
            "run_backfill_dates",
            return_value=[
                {
                    "date": "2026-04-28",
                    "status": "completed",
                    "summary_json": "",
                    "summary_md": "",
                }
            ],
        ), mock.patch.object(openrelix, "sync_review_outputs"), mock.patch(
            "sys.stdout",
            new_callable=io.StringIO,
        ) as stdout:
            openrelix.command_backfill(args)

        self.assertIn("OpenRelix 现在可以先使用了", stdout.getvalue())
        self.assertIn("手动刷新即可看到快速总结", stdout.getvalue())

    def test_command_backfill_syncs_outputs_and_exits_when_deferred_pipeline_fails(self):
        calls = []

        def fake_run_backfill_dates(
            dates,
            stage,
            learn_window_days=0,
            force=False,
            ensure_learning_final=True,
            defer_global_refresh=False,
            verbose=True,
            jobs=1,
        ):
            calls.append(("backfill", defer_global_refresh, jobs))
            return [
                {
                    "date": "2026-04-28",
                    "status": "failed",
                    "summary_json": "",
                    "summary_md": "",
                    "returncode": 2,
                }
            ]

        args = argparse.Namespace(
            dates="2026-04-28",
            date_from=None,
            date_to="2026-04-28",
            days=0,
            stage="final",
            learn_window_days=0,
            force=False,
            json=False,
            jobs=2,
        )

        with mock.patch.object(
            openrelix,
            "run_backfill_dates",
            side_effect=fake_run_backfill_dates,
        ), mock.patch.object(
            openrelix,
            "sync_review_outputs",
            side_effect=lambda **kwargs: calls.append(("refresh", kwargs)),
        ), mock.patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as raised:
                openrelix.command_backfill(args)

        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(calls[0], ("backfill", True, 2))
        self.assertEqual(calls[1], ("refresh", {"include_index": True, "include_native_display": True, "verbose": True}))

    def test_command_backfill_final_ensures_preliminary_without_deepening_learning_dependencies(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            calls = []

            def fake_ensure_learning_windows_final(
                date_strs,
                learn_window_days,
                verbose=True,
                defer_global_refresh=False,
                jobs=1,
            ):
                calls.append(("ensure", date_strs, learn_window_days, defer_global_refresh, jobs))
                raise AssertionError("final backfill should not deepen learning-window dates")

            def fake_precollect_learning_window_sources(date_strs, learn_window_days, verbose=True):
                calls.append(("precollect", date_strs, learn_window_days, verbose))
                return ["2026-04-21"]

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                calls.append(("pipeline", cmd))

            args = argparse.Namespace(
                dates="2026-04-28",
                date_from=None,
                date_to="2026-04-28",
                days=0,
                stage="final",
                learn_window_days=7,
                force=False,
                json=False,
                jobs=2,
            )

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "resolve_learning_backfill_dates_for_targets",
                return_value=["2026-04-21"],
            ), mock.patch.object(
                openrelix,
                "ensure_learning_windows_final",
                side_effect=fake_ensure_learning_windows_final,
            ), mock.patch.object(
                openrelix,
                "precollect_learning_window_sources",
                side_effect=fake_precollect_learning_window_sources,
            ), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch.object(
                openrelix,
                "hydrate_raw_history_windows",
                return_value=[],
            ), mock.patch.object(
                openrelix,
                "sync_review_outputs",
                side_effect=lambda **kwargs: calls.append(("refresh", kwargs)),
            ), mock.patch("sys.stdout", new_callable=io.StringIO):
                openrelix.command_backfill(args)

            self.assertEqual(calls[0][0], "pipeline")
            self.assertEqual(calls[0][1][2:4], ["2026-04-21", "preliminary"])
            self.assertEqual(calls[1], ("precollect", ["2026-04-28"], 7, True))
            self.assertEqual(calls[2][0], "pipeline")
            self.assertEqual(calls[2][1][2:4], ["2026-04-28", "final"])
            self.assertEqual(calls[3], ("refresh", {"include_index": True, "include_native_display": True, "verbose": True}))

    def test_review_syncs_summary_and_panel_after_pipeline(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            summary_dir = consolidated_daily_dir / "2026-04-28"
            summary_json_path = summary_dir / "summary.json"
            summary_md_path = summary_dir / "summary.md"
            calls = []

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                calls.append(("pipeline", cmd))
                summary_dir.mkdir(parents=True)
                summary_json_path.write_text(
                    json.dumps(
                        {
                            "date": "2026-04-28",
                            "stage": "final",
                            "day_summary": "done",
                            "window_summaries": [],
                            "durable_memories": [],
                            "session_memories": [],
                            "low_priority_memories": [],
                        }
                    ),
                    encoding="utf-8",
                )
                summary_md_path.write_text("# done\n", encoding="utf-8")

            args = argparse.Namespace(
                date="2026-04-28",
                stage="final",
                open=False,
                json=False,
                learn_window_days=0,
                jobs=1,
            )

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch.object(
                openrelix,
                "sync_review_outputs",
                side_effect=lambda **kwargs: calls.append(("refresh", kwargs)),
            ), mock.patch("sys.stdout", new_callable=io.StringIO):
                openrelix.command_review(args)

            self.assertEqual([item[0] for item in calls], ["pipeline", "refresh"])
            self.assertEqual(calls[1][1], {"include_index": True, "include_native_display": True, "verbose": True})

    def test_review_syncs_outputs_when_deferred_pipeline_exits_after_fallback_summary(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            summary_dir = consolidated_daily_dir / "2026-04-28"
            summary_json_path = summary_dir / "summary.json"
            summary_md_path = summary_dir / "summary.md"
            calls = []

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                calls.append(("pipeline", cmd))
                summary_dir.mkdir(parents=True)
                summary_json_path.write_text(
                    json.dumps(
                        {
                            "date": "2026-04-28",
                            "stage": "final",
                            "day_summary": "fallback",
                            "window_summaries": [],
                            "durable_memories": [],
                            "session_memories": [],
                            "low_priority_memories": [],
                            "last_run_model_status": "failed",
                            "last_run_model_error_hint": "auth failed",
                        }
                    ),
                    encoding="utf-8",
                )
                summary_md_path.write_text("# fallback\n", encoding="utf-8")
                raise subprocess.CalledProcessError(1, cmd)

            args = argparse.Namespace(
                date="2026-04-28",
                stage="final",
                open=False,
                json=False,
                learn_window_days=0,
                jobs=1,
            )

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch.object(
                openrelix,
                "sync_review_outputs",
                side_effect=lambda **kwargs: calls.append(("refresh", kwargs)),
            ), mock.patch("sys.stdout", new_callable=io.StringIO), mock.patch(
                "sys.stderr",
                new_callable=io.StringIO,
            ):
                with self.assertRaises(SystemExit) as raised:
                    openrelix.command_review(args)

            self.assertEqual(raised.exception.code, 1)
            self.assertEqual([item[0] for item in calls], ["pipeline", "refresh"])
            self.assertEqual(calls[1][1], {"include_index": True, "include_native_display": True, "verbose": True})

    def test_review_final_ensures_preliminary_without_historical_final_sync(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            summary_dir = consolidated_daily_dir / "2026-04-28"
            summary_json_path = summary_dir / "summary.json"
            summary_md_path = summary_dir / "summary.md"
            calls = []

            def fake_backfill_dates(
                dates,
                stage,
                learn_window_days=0,
                force=False,
                ensure_learning_final=True,
                defer_global_refresh=False,
                verbose=True,
                jobs=1,
            ):
                calls.append(("backfill", dates, stage, learn_window_days, ensure_learning_final, defer_global_refresh, jobs))
                self.assertEqual(stage, "preliminary")
                self.assertEqual(learn_window_days, 0)
                self.assertIs(ensure_learning_final, False)
                return [
                    {
                        "date": date_str,
                        "status": "completed",
                        "summary_json": "",
                        "summary_md": "",
                    }
                    for date_str in dates
                ]

            def fake_precollect_learning_window_sources(date_strs, learn_window_days, verbose=True):
                calls.append(("precollect", date_strs, learn_window_days, verbose))
                return ["2026-04-21"]

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                calls.append(("pipeline", cmd))
                summary_dir.mkdir(parents=True)
                summary_json_path.write_text(
                    json.dumps(
                        {
                            "date": "2026-04-28",
                            "stage": "final",
                            "day_summary": "done with fallback history",
                            "window_summaries": [],
                            "durable_memories": [],
                            "session_memories": [],
                            "low_priority_memories": [],
                        }
                    ),
                    encoding="utf-8",
                )
                summary_md_path.write_text("# done\n", encoding="utf-8")

            args = argparse.Namespace(
                date="2026-04-28",
                stage="final",
                open=False,
                json=False,
                learn_window_days=7,
                jobs=2,
            )

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "resolve_learning_backfill_dates_for_targets",
                return_value=["2026-04-21"],
            ), mock.patch.object(
                openrelix,
                "precollect_learning_window_sources",
                side_effect=fake_precollect_learning_window_sources,
            ), mock.patch.object(
                openrelix,
                "run_backfill_dates",
                side_effect=fake_backfill_dates,
            ), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch.object(
                openrelix,
                "sync_review_outputs",
                side_effect=lambda **kwargs: calls.append(("refresh", kwargs)),
            ), mock.patch("sys.stdout", new_callable=io.StringIO):
                openrelix.command_review(args)

            self.assertEqual(calls[0], ("backfill", ["2026-04-21"], "preliminary", 0, False, True, 2))
            self.assertEqual(calls[1], ("precollect", ["2026-04-28"], 7, True))
            self.assertEqual(calls[2][0], "pipeline")
            self.assertEqual(calls[3], ("refresh", {"include_index": True, "include_native_display": True, "verbose": True}))

    def test_review_json_syncs_outputs_without_polluting_json(self):
        with TemporaryDirectory() as tmpdir:
            consolidated_daily_dir = Path(tmpdir) / "consolidated" / "daily"
            summary_dir = consolidated_daily_dir / "2026-04-28"
            summary_json_path = summary_dir / "summary.json"
            calls = []

            def fake_run_checked_with_progress(cmd, progress_messages, interval_seconds=20, reminder_seconds=60):
                calls.append(("pipeline", cmd, progress_messages))
                summary_dir.mkdir(parents=True)
                summary_json_path.write_text(
                    json.dumps(
                        {
                            "date": "2026-04-28",
                            "stage": "final",
                            "day_summary": "json ok",
                            "window_summaries": [],
                            "durable_memories": [],
                            "session_memories": [],
                            "low_priority_memories": [],
                        }
                    ),
                    encoding="utf-8",
                )

            args = argparse.Namespace(
                date="2026-04-28",
                stage="final",
                open=False,
                json=True,
                learn_window_days=0,
                jobs=1,
            )

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch.object(
                openrelix,
                "sync_review_outputs",
                side_effect=lambda **kwargs: calls.append(("refresh", kwargs)),
            ), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                openrelix.command_review(args)

            self.assertEqual([item[0] for item in calls], ["pipeline", "refresh"])
            self.assertEqual(calls[1][1], {"include_index": True, "include_native_display": True, "verbose": False})
            self.assertEqual(calls[0][2], [])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["day_summary"], "json ok")

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
                "range_command": "openrelix backfill --dates '2026-04-24' --stage final --learn-window-days 7",
                "commands_by_date": {
                    "2026-04-24": "openrelix backfill --from 2026-04-24 --to 2026-04-24 --stage final --learn-window-days 7",
                },
            },
        )

        self.assertIn("2026/04/24 · 未整理", html)
        self.assertIn('id="nightly-backfill-panel"', html)
        self.assertIn("缺少整理结果", html)
        self.assertIn("openrelix backfill --from 2026-04-24 --to 2026-04-24", html)
        self.assertIn("data-backfill-copy=\"single\"", html)

    def test_nightly_summary_panel_uses_preview_command_for_current_day_missing_date(self):
        with mock.patch.object(
            build_overview,
            "current_local_datetime",
            return_value=datetime.fromisoformat("2026-05-06T15:00:00+08:00"),
        ):
            html = build_overview.make_nightly_summary_panel(
                "每日整理结果",
                "暂无夜间整理结果",
                "",
                {},
                {"window_count": 0},
                [],
                summary_views=[],
                selected_date="2026-05-06",
                selectable_dates=["2026-05-06"],
                backfill={
                    "missing_dates": ["2026-05-06"],
                    "range_command": "openrelix backfill --dates '2026-05-06' --stage final --learn-window-days 7",
                    "commands_by_date": {
                        "2026-05-06": "openrelix backfill --from 2026-05-06 --to 2026-05-06 --stage final --learn-window-days 7",
                    },
                },
        )

        self.assertIn("今日仍在进行中", html)
        self.assertIn("快速回溯", html)
        self.assertIn("openrelix review --stage preliminary --learn-window-days 0", html)
        self.assertNotIn("openrelix backfill --from 2026-05-06 --to 2026-05-06 --stage final", html)

    def test_nightly_summary_panel_shows_final_backfill_command_for_preliminary_date(self):
        summary_view = build_overview.build_daily_summary_view(
            {
                "date": "2026-04-24",
                "stage": "preliminary",
                "day_summary": "轻量整理完成：读取 2 个窗口。",
                "raw_window_count": 2,
                "durable_memories": [],
                "session_memories": [1],
                "low_priority_memories": [],
            },
            {"window_count": 2},
            [],
        )
        html = build_overview.make_nightly_summary_panel(
            "每日整理结果",
            "2026-04-24 · 快速回溯",
            "",
            {},
            {"window_count": 2},
            [],
            summary_views=[summary_view],
            selected_date="2026-04-24",
            selectable_dates=["2026-04-24"],
            backfill={
                "missing_dates": [],
                "learn_window_days": 7,
                "range_command": "",
                "commands_by_date": {},
            },
        )

        self.assertIn("建议深度回溯", html)
        self.assertIn("当前是快速回溯，只生成窗口摘要和快速索引，不做记忆沉淀", html)
        self.assertIn("首次安装后，会自动触发完整回溯，请耐心等待。", html)
        self.assertIn("完整回溯", html)
        self.assertIn("openrelix backfill --from 2026-04-24 --to 2026-04-24 --stage final", html)
        self.assertIn('id="nightly-backfill-range" hidden', html)

    def test_nightly_summary_panel_does_not_recommend_final_for_current_day_preview(self):
        with mock.patch.object(
            build_overview,
            "current_local_datetime",
            return_value=datetime.fromisoformat("2026-05-06T15:00:00+08:00"),
        ):
            summary_view = build_overview.build_daily_summary_view(
                {
                    "date": "2026-05-06",
                    "stage": "preliminary",
                    "day_summary": "轻量整理完成：读取 1 个窗口。",
                    "raw_window_count": 1,
                    "durable_memories": [],
                    "session_memories": [1],
                    "low_priority_memories": [],
                },
                {"window_count": 1},
                [],
            )
            html = build_overview.make_nightly_summary_panel(
                "每日整理结果",
                "2026-05-06 · 快速回溯",
                "",
                {},
                {"window_count": 1},
                [],
                summary_views=[summary_view],
                selected_date="2026-05-06",
                selectable_dates=["2026-05-06"],
                backfill={
                    "missing_dates": [],
                    "learn_window_days": 7,
                    "range_command": "",
                    "commands_by_date": {},
                },
            )

        self.assertIn("今天仍在进行中", html)
        self.assertIn("次日完整回溯会再生成记忆", html)
        self.assertIn('id="nightly-backfill-panel" hidden', html)
        self.assertNotIn("建议深度回溯", html)
        self.assertNotIn("openrelix backfill --from 2026-05-06 --to 2026-05-06 --stage final", html)

    def test_build_html_wires_window_filter_views(self):
        window_item = {
            "date": "2026-04-26",
            "window_id": "w1",
            "cwd": "/tmp/openrelix",
            "cwd_display": "openrelix",
            "project_label": "OpenRelix",
            "activity_source": "history",
            "window_summary": "窗口筛选联动",
            "question_count": 1,
            "conclusion_count": 1,
            "question_summary": "如何联动窗口筛选？",
            "main_takeaway": "窗口总览和窗口明细共用同一筛选。",
            "keywords": ["窗口"],
            "latest_activity_at": "2026-04-26T10:30:00+08:00",
            "latest_activity_display": "04-26 10:30",
            "started_at": "2026-04-26T09:00:00+08:00",
            "started_at_display": "04-26 09:00",
            "recent_prompts": [],
            "recent_conclusions": [],
        }
        window_filter_overview = {
            "date": "2026-04-26",
            "window_count": 1,
            "source_kind": "daily_capture",
            "windows": [window_item],
        }
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
                "project_context_default_days": 3,
                "project_context_views": {
                    "3": {
                        "window_count": 1,
                        "source_dates": ["2026-04-26"],
                        "source_date_count": 1,
                        "scanned_date_count": 3,
                        "project_contexts": [
                            {
                                "label": "OpenRelix",
                                "cwd_preview": "openrelix",
                                "window_count": 1,
                                "topic_count": 1,
                                "question_count": 1,
                                "conclusion_count": 1,
                                "latest_activity_display": "04-26 10:30",
                                "topics": [
                                    {
                                        "label": "窗口筛选联动",
                                        "window_count": 1,
                                        "question_count": 1,
                                        "conclusion_count": 1,
                                        "source_windows": [
                                            {
                                                "window_id": "w1",
                                                "anchor_id": "window-w1",
                                                "display_label": "1",
                                                "latest_activity_display": "04-26 10:30",
                                                "title": "窗口筛选联动",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                },
                "window_overview": {
                    "date": "2026-04-26",
                    "window_count": 1,
                    "source_kind": "daily_capture",
                    "windows": [],
                },
                "window_filter_start_date": "2026-04-24",
                "window_filter_end_date": "2026-04-26",
                "window_filter_default_days": 3,
                "window_filter_max_days": 30,
                "window_detail_visible_count": 20,
                "window_filter_overview": window_filter_overview,
                "window_filter_default_overview": window_filter_overview,
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

        self.assertNotIn('id="window-overview-date-input"', html)
        self.assertIn('id="window-filter-panel"', html)
        self.assertIn('id="window-filter-sticky-placeholder" hidden', html)
        self.assertIn('id="window-start-date"', html)
        self.assertIn('data-window-date-display="start">2026/04/24</span>', html)
        self.assertIn('value="2026-04-24"', html)
        self.assertIn('id="window-end-date"', html)
        self.assertIn('data-window-date-display="end">2026/04/26</span>', html)
        self.assertIn('value="2026-04-26"', html)
        self.assertIn("function openWindowDatePicker(fieldKey)", html)
        self.assertIn("function renderWindowDatePicker()", html)
        self.assertIn('data-window-range-days="3" aria-pressed="true" data-active="true"', html)
        self.assertIn('id="window-search-query"', html)
        self.assertIn('id="window-search-submit"', html)
        self.assertIn('id="window-search-trigger"', html)
        self.assertIn('id="window-search-live" role="dialog" aria-modal="false"', html)
        self.assertIn('id="window-search-close"', html)
        self.assertIn('id="window-search-results"', html)
        self.assertIn('data-window-search-endpoint="http://127.0.0.1:8765/window-search"', html)
        self.assertIn('windowSearchEndpoint: "http://127.0.0.1:8765/window-search"', html)
        self.assertIn('query: "",', html)
        self.assertIn('searchScope: "all"', html)
        self.assertIn('id="window-overview-title"', html)
        self.assertIn('id="window-overview-note"', html)
        self.assertIn('id="window-overview-map"', html)
        self.assertIn('id="window-overview-context-list"', html)
        self.assertIn('id="window-backfill-hint" hidden', html)
        self.assertIn("轻度回溯（不使用大模型总结）", html)
        self.assertIn("深度回溯（使用大模型总结）", html)
        self.assertIn('data-window-backfill-copy="light"', html)
        self.assertIn('data-window-backfill-copy="deep"', html)
        self.assertIn("强提醒：当前搜索范围超出已回溯窗口，结果可能不完整", html)
        self.assertIn("搜索结果可能只覆盖已采集或已总结的窗口", html)
        self.assertIn('id="window-summary-list"', html)
        self.assertIn('id="window-detail-more-button"', html)
        self.assertIn('data-window-card="true"', html)
        self.assertIn('data-window-date="2026-04-26"', html)
        self.assertIn('data-window-started-at="2026-04-26T09:00:00+08:00"', html)
        self.assertIn("窗口创建 04-26 09:00", html)
        self.assertIn('"window_overview_default_date":"2026-04-26"', html)
        self.assertIn('"window_filter_start_date":"2026-04-24"', html)
        self.assertIn('"window_filter_end_date":"2026-04-26"', html)
        self.assertIn('"window_detail_visible_count":20', html)
        self.assertIn('"window_overview_project_visible_count":3', html)
        self.assertIn("function applyWindowFilters()", html)
        self.assertIn("function windowFilterNeedsBackfillHint(filters)", html)
        self.assertIn("function windowBackfillSearchWarning(filters)", html)
        self.assertIn("function windowSearchStatusText(baseText, filters)", html)
        self.assertIn("function renderWindowBackfillHint(filters, matchedCount)", html)
        self.assertIn("openrelix backfill --from \" + startDate + \" --to \" + endDate + \" --stage preliminary --learn-window-days 0", html)
        self.assertIn("openrelix backfill --from \" + startDate + \" --to \" + endDate + \" --stage final --learn-window-days \" + days", html)
        self.assertIn("wireWindowBackfillCopyButtons();", html)
        self.assertIn("const windowSearchResultLimit = 100;", html)
        self.assertIn("function wireWindowFilters()", html)
        self.assertIn("function normalizeWindowSearchValue(value)", html)
        self.assertIn("function windowCardMatchesSearch(card, filters)", html)
        self.assertIn("function setWindowSearchOpen(isOpen, shouldFocus)", html)
        self.assertIn("function syncWindowSearchDialogVisibility()", html)
        self.assertIn("windowSearchOpen: false", html)
        self.assertIn("windowSearchDraftQuery: \"\"", html)
        self.assertIn("function markWindowSearchDraftChanged()", html)
        self.assertIn("function submitWindowSearch()", html)
        self.assertIn('event.key === "Enter" && !event.isComposing', html)
        self.assertIn("按回车或点击搜索后检索", html)
        self.assertIn("当前输入尚未提交。", html)
        self.assertIn("展示前 \" + displayedResults.length + \" 个 · 可能还有更多", html)
        self.assertIn("Showing first \" + displayedResults.length + \" matches · more may exist", html)
        self.assertIn("markWindowSearchDraftChanged();", html)
        self.assertNotIn("query: elements.windowSearchInput.value", html)
        self.assertIn("const displayedResults = results;", html)
        self.assertNotIn("const filteredResults = allResults.filter(function (result)", html)
        self.assertNotIn("已按当前筛选过滤", html)
        self.assertIn("function runWindowSearchRequest(requestSeq, filters)", html)
        self.assertIn('requestUrl.searchParams.set("scope", filters.searchScope || "all");', html)
        self.assertIn('requestUrl.searchParams.set("limit", String(windowSearchResultLimit));', html)
        self.assertNotIn('requestUrl.searchParams.set("limit", "20");', html)
        self.assertIn('requestUrl.searchParams.set("date_from", filters.startDate);', html)
        self.assertIn('requestUrl.searchParams.set("date_to", filters.endDate);', html)
        self.assertIn("function scheduleWindowSearchRequest()", html)
        self.assertIn("data-window-search-target", html)
        self.assertIn("data-window-search-ai", html)
        self.assertIn(".window-filter-panel.is-search-open", html)
        self.assertIn(".window-filter-panel.is-sticky.is-search-open", html)
        self.assertIn('elements.windowFilterPanel.classList.toggle("is-search-open", isOpen);', html)
        self.assertIn("position: absolute;", html)
        self.assertIn("top: -1px;", html)
        self.assertIn("left: -1px;", html)
        self.assertIn("right: -1px;", html)
        self.assertIn("border-radius: inherit;", html)
        self.assertIn("max-height: min(720px, calc(100vh - 104px));", html)
        self.assertIn("align-content: start;", html)
        self.assertIn('class="window-search-sticky-stack"', html)
        self.assertIn(".window-search-sticky-stack", html)
        self.assertIn("position: sticky;", html)
        self.assertIn("top: -18px;", html)
        self.assertIn(".window-search-trigger[data-window-search-active=", html)
        self.assertIn("window-search-strip-scope-field", html)
        self.assertIn('updateDatePickerDisplays("window-search");', html)
        self.assertIn('data-date-picker-button="window-search:start"', html)
        self.assertIn(".window-search-close-label", html)
        self.assertIn('document.addEventListener("pointerdown"', html)
        self.assertIn("elements.windowSearchLive.contains(target)", html)
        self.assertIn("setWindowSearchOpen(false, false);", html)
        self.assertIn("overflow: auto;", html)
        self.assertIn("scrollbar-gutter: stable;", html)
        self.assertIn("max-height: none;", html)
        self.assertIn("overscroll-behavior: contain;", html)
        self.assertIn("windowSearchTriggerButton", html)
        self.assertIn("windowSearchInput", html)
        self.assertIn("windowSearchSubmitButton", html)
        self.assertIn("windowSearchScopeButtons", html)
        self.assertIn("windowSearchResults", html)
        self.assertIn("wireWindowFilters();", html)
        self.assertIn("windowOverviewProjectVisibleCount", html)
        self.assertIn("data-window-overview-project-more", html)
        self.assertIn("function wireWindowOverviewProjectMore()", html)
        self.assertIn("wireWindowOverviewProjectMore();", html)
        self.assertIn(".window-filter-panel.is-sticky", html)
        self.assertIn("function syncWindowFilterSticky()", html)
        self.assertIn("function wireWindowFilterSticky()", html)
        self.assertIn("wireWindowFilterSticky();", html)
        self.assertIn("scroll-margin-top: calc(var(--window-filter-sticky-height, 0px) + 28px);", html)
        self.assertIn("const currentFilters = normalizeWindowFilters(state.windowFilters);", html)
        self.assertIn("revealWindowCardById(anchor, true);", html)
        self.assertIn('window.history.replaceState(null, "", "#" + anchor);', html)
        self.assertIn("function syncDateControlValue(select)", html)
        self.assertNotIn("wireWindowOverviewDateInput();", html)
        self.assertIn("function wireExternalPanelLinks()", html)
        self.assertIn("openrelixOpenExternal", html)
        self.assertIn("wireExternalPanelLinks();", html)
        self.assertIn("wireWindowResumeActions();", html)
        self.assertIn("function reviewPromptTextFromNode(node)", html)
        self.assertIn("node.content.textContent", html)
        self.assertIn('const rawClusterKey = String(card.getAttribute("data-window-task-cluster-key") || "").trim();', html)
        self.assertIn("const taskKey = rawClusterKey && clusterKey", html)
        self.assertNotIn("cluster:untitled", html)

    def test_daily_summary_view_carries_bilingual_dynamic_fields(self):
        view = build_overview.build_daily_summary_view(
            {
                "date": "2026-04-27",
                "stage": "final",
                "day_summary": "今天沉淀了新的记忆。",
                "raw_window_count": 2,
                "durable_memories": [
                    {
                        "title": "编辑文件默认优先用 apply_patch",
                        "memory_type": "procedural",
                        "priority": "high",
                        "value_note": "文件修改的稳定偏好是优先用 apply_patch。",
                        "source_window_ids": ["w1"],
                        "keywords": ["apply_patch"],
                    }
                ],
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
            "These numbers combine the new memory policy with today's Token snapshot: general and project context both enter host context under separate budgets.",
        )

    def test_daily_summary_stats_use_context_policy_counts(self):
        view = build_overview.build_daily_summary_view(
            {
                "date": "2026-05-04",
                "stage": "final",
                "day_summary": "今天沉淀了新的记忆。",
                "raw_window_count": 3,
                "durable_memories": [
                    {
                        "title": "编辑文件默认优先用 apply_patch",
                        "memory_type": "procedural",
                        "priority": "high",
                        "value_note": "文件修改的稳定偏好是优先用 apply_patch。",
                        "source_window_ids": ["w1"],
                        "keywords": ["apply_patch"],
                    },
                    {
                        "title": "OpenRelix 的 worktree 与合并默认规则",
                        "memory_type": "procedural",
                        "priority": "high",
                        "value_note": "做 bugfix/feature 时先起独立 worktree，只推 origin/main。",
                        "source_window_ids": ["w1"],
                        "keywords": ["worktree"],
                    },
                ],
                "session_memories": [
                    {
                        "title": "当前任务已推进",
                        "memory_type": "procedural",
                        "priority": "high",
                        "value_note": "项目任务继续在当前 worktree 验证。",
                        "source_window_ids": ["w1"],
                    }
                ],
                "low_priority_memories": [
                    {
                        "title": "低优先证据",
                        "memory_type": "semantic",
                        "priority": "low",
                        "value_note": "仅作为本地证据保留。",
                        "source_window_ids": ["w1"],
                    }
                ],
            },
            {"window_count": 3},
            [],
            token_usage={
                "daily_rows": [
                    {
                        "date": "2026-05-04",
                        "token_display": "5.3亿",
                        "value": 533808705,
                    }
                ]
            },
        )

        self.assertEqual(
            view["stats"],
            [
                {"label": "工作窗口", "value": 3},
                {"label": "通用上下文", "value": 1},
                {"label": "项目上下文", "value": 2},
                {"label": "今日 Token", "value": "5.34亿"},
            ],
        )

    def test_daily_summary_token_metric_uses_short_three_digit_display(self):
        view = build_overview.build_daily_summary_view(
            {
                "date": "2026-05-05",
                "stage": "final",
                "day_summary": "当天完成 Token 面板修复。",
                "raw_window_count": 2,
                "durable_memories": [],
                "session_memories": [],
                "low_priority_memories": [],
            },
            {"window_count": 2},
            [],
            token_usage={
                "daily_rows": [
                    {
                        "date": "2026-05-05",
                        "token_display": "9789.6万",
                        "value": 97895744,
                    }
                ]
            },
        )

        self.assertEqual(view["stats"][3], {"label": "今日 Token", "value": "0.98亿"})

    def test_compact_token_zh_prefers_yi_and_wan_units(self):
        self.assertEqual(build_overview.compact_token(97895744, language="zh"), "0.98亿")
        self.assertEqual(build_overview.compact_token(99999999, language="zh"), "1亿")
        self.assertEqual(build_overview.compact_token(9789574, language="zh"), "979万")
        self.assertEqual(build_overview.compact_token(3600616, language="zh"), "360万")
        self.assertEqual(build_overview.compact_token(360000, language="zh"), "36万")

    def test_build_html_daily_summary_payload_supports_english_switch_for_generated_fields(self):
        summary_view = build_overview.build_daily_summary_view(
            {
                "date": "2026-04-27",
                "stage": "final",
                "day_summary": "今天沉淀了新的记忆。",
                "raw_window_count": 2,
                "durable_memories": [
                    {
                        "title": "编辑文件默认优先用 apply_patch",
                        "memory_type": "procedural",
                        "priority": "high",
                        "value_note": "文件修改的稳定偏好是优先用 apply_patch。",
                        "source_window_ids": ["w1"],
                        "keywords": ["apply_patch"],
                    }
                ],
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

        self.assertIn(
            json.dumps(
                {
                    "context_labels_en": [
                        "Personal assets system",
                        "Codex local environment",
                    ]
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )[1:-1],
            html,
        )
        self.assertIn(
            '"note_text_en":"These numbers combine the new memory policy with today\'s Token snapshot: general and project context both enter host context under separate budgets."',
            html,
        )
        self.assertIn('"lead_text_en":"2026-04-27 synthesis captured 2 work windows', html)
        self.assertIn('getLocalizedSummaryText(summary, "lead_text")', html)
        self.assertIn('getLocalizedSummaryList(summary, "detail_parts")', html)
        self.assertIn('getLocalizedSummaryList(summary, "context_labels")', html)
        self.assertIn('getLocalizedSummaryText(summary, "note_text")', html)

    def test_preliminary_daily_summary_view_prompts_final_backfill(self):
        view = build_overview.build_daily_summary_view(
            {
                "date": "2026-04-27",
                "stage": "preliminary",
                "day_summary": "轻量整理完成：读取 2 个窗口。",
                "raw_window_count": 2,
                "durable_memories": [],
                "session_memories": [1],
                "low_priority_memories": [],
            },
            {"window_count": 2},
            [],
        )

        self.assertIn("只保留窗口摘要和快速索引", view["note_text"])
        self.assertFalse(
            any(
                "openrelix backfill --from 2026-04-27 --to 2026-04-27 --stage final" in item
                for item in view["detail_parts"]
            )
        )
        self.assertFalse(any("may be inaccurate" in item for item in view["detail_parts_en"]))

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
        with mock.patch.object(
            build_overview,
            "current_local_datetime",
            return_value=datetime.fromisoformat("2026-04-27T12:00:00+08:00"),
        ):
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
                                "totalTokens": 1100,
                                "costUSD": 2.5,
                            },
                            {
                                "date": "Apr 27, 2026",
                                "inputTokens": 2000,
                                "cachedInputTokens": 1500,
                                "outputTokens": 300,
                                "reasoningOutputTokens": 100,
                                "totalTokens": 2300,
                                "costUSD": 4.5,
                            },
                        ]
                    },
                    "error": "",
                    "fetched_at": "2026-04-27T12:00:00+08:00",
                    "window_days": 14,
                },
                language="zh",
            )

        self.assertEqual(view["today_total_tokens"], 2300)
        self.assertEqual(view["today_cost_usd"], 4.5)
        self.assertEqual(view["today_token_cost_display"], "2300 · $5")
        self.assertIn("2026-04-26 至 2026-04-27", view["overview_note"])
        self.assertIn("2 个有数据日", view["overview_note"])
        self.assertIn("周期 Token", [card["label"] for card in view["summary_cards"]])
        self.assertIn("周期成本", [card["label"] for card in view["summary_cards"]])
        self.assertEqual(view["summary_cards"][0]["value"], "3400")
        self.assertEqual(view["summary_cards"][0]["caption"], "ccusage 估算")
        self.assertEqual(view["summary_cards"][1]["value"], "$7")
        self.assertEqual(view["summary_cards"][1]["caption"], "ccusage 估算")
        self.assertNotIn("缓存读取占总输入", [card["label"] for card in view["summary_cards"]])
        self.assertEqual(view["daily_rows"][-1]["display"], "2300 · $5")
        self.assertIn("details", view["daily_rows"][-1])
        self.assertEqual(view["today_breakdown"][0]["value"], 500)
        self.assertEqual(view["today_breakdown"][1]["label"], "缓存读取")
        self.assertIn("无缓存输入", view["today_breakdown"][0]["details"][0]["meta"])
        self.assertIn("占总输入", view["daily_rows"][-1]["details"][1]["meta"])
        self.assertIn("details", view["today_breakdown"][1])
        self.assertEqual(view["daily_rows"][0]["tone"], "token-daily-mid")
        self.assertEqual(view["daily_rows"][-1]["tone"], "token-daily-high")
        self.assertEqual(
            [row["tone"] for row in view["today_breakdown"]],
            ["token-input", "token-cache", "token-output", "token-reasoning"],
        )

    def test_token_usage_view_repairs_cached_total_to_match_ccusage_table(self):
        with mock.patch.object(
            build_overview,
            "current_local_datetime",
            return_value=datetime.fromisoformat("2026-05-18T12:00:00+08:00"),
        ):
            view = build_overview.build_token_usage_view(
                {
                    "available": True,
                    "payload": {
                        "daily": [
                            {
                                "date": "2026-05-18",
                                "inputTokens": 120,
                                "cachedInputTokens": 20,
                                "outputTokens": 30,
                                "reasoningOutputTokens": 5,
                                "totalTokens": 130,
                                "costUSD": 1.0,
                            }
                        ]
                    },
                    "error": "",
                    "fetched_at": "2026-05-18T12:00:00+08:00",
                    "window_days": 7,
                    "range_start": "2026-05-12",
                    "range_end": "2026-05-18",
                },
                language="zh",
            )

        self.assertEqual(view["today_total_tokens"], 150)
        self.assertEqual(view["period_total_tokens"], 150)
        self.assertEqual(view["daily_rows"][-1]["value"], 150)
        self.assertEqual(view["daily_rows"][-1]["display"], "150 · $1")
        self.assertEqual(view["today_breakdown"][0]["value"], 100)
        self.assertEqual(view["today_breakdown"][1]["value"], 20)
        self.assertEqual(view["today_breakdown"][2]["value"], 30)

    def test_token_usage_view_shows_zero_when_today_has_no_usage_row(self):
        with mock.patch.object(
            build_overview,
            "current_local_datetime",
            return_value=datetime.fromisoformat("2026-05-07T12:00:00+08:00"),
        ):
            view = build_overview.build_token_usage_view(
                {
                    "available": True,
                    "payload": {
                        "daily": [
                            {
                                "date": "May 06, 2026",
                                "inputTokens": 1000,
                                "cachedInputTokens": 250,
                                "outputTokens": 200,
                                "reasoningOutputTokens": 0,
                                "totalTokens": 1200,
                                "costUSD": 2.0,
                            },
                        ]
                    },
                    "error": "",
                    "fetched_at": "2026-05-07T12:00:00+08:00",
                    "window_days": 7,
                    "range_start": "2026-05-01",
                    "range_end": "2026-05-07",
                },
                language="zh",
            )

        self.assertEqual(view["today_total_tokens"], 0)
        self.assertEqual(view["today_total_tokens_display"], "0")
        self.assertEqual(view["today_token_cost_display"], "0")
        self.assertEqual(view["today_date_label"], "05-07")
        self.assertEqual(view["daily_rows"][-1]["date"], "2026-05-07")
        self.assertEqual(view["daily_rows"][-1]["value"], 0)
        self.assertEqual(view["daily_rows"][-1]["tone"], "token-daily-empty")
        self.assertEqual(view["daily_rows"][-2]["value"], 1200)
        self.assertEqual(view["period_total_tokens"], 1200)
        self.assertEqual(view["active_period_count"], 1)
        self.assertTrue(all(row["value"] == 0 for row in view["today_breakdown"]))

    def test_token_usage_view_filters_daily_rows_to_recent_calendar_week(self):
        daily = []
        for index, raw_date in enumerate(
            [
                "Apr 21, 2026",
                "Apr 24, 2026",
                "Apr 26, 2026",
                "Apr 27, 2026",
                "Apr 28, 2026",
                "Apr 30, 2026",
                "May 03, 2026",
            ],
            1,
        ):
            daily.append(
                {
                    "date": raw_date,
                    "inputTokens": index * 100,
                    "cachedInputTokens": 0,
                    "outputTokens": 10,
                    "reasoningOutputTokens": 5,
                    "totalTokens": index * 1000,
                    "costUSD": 1.0,
                }
            )

        with mock.patch.object(
            build_overview,
            "current_local_datetime",
            return_value=datetime.fromisoformat("2026-05-03T12:00:00+08:00"),
        ):
            view = build_overview.build_token_usage_view(
                {
                    "available": True,
                    "payload": {"daily": daily},
                    "error": "",
                    "fetched_at": "2026-05-03T12:00:00+08:00",
                    "window_days": 14,
                },
                language="zh",
            )

        self.assertEqual(
            [row["label"] for row in view["daily_rows"]],
            ["04-21", "04-24", "04-26", "04-27", "04-28", "04-30", "05-03"],
        )
        self.assertEqual(view["window_days"], 14)
        self.assertIn("2026-04-21 至 2026-05-03", view["overview_note"])
        self.assertIn("7 个有数据日", view["overview_note"])

    def test_token_usage_view_filters_range_and_groups_by_month(self):
        with mock.patch.object(
            build_overview,
            "current_local_datetime",
            return_value=datetime.fromisoformat("2026-05-31T12:00:00+08:00"),
        ):
            view = build_overview.build_token_usage_view(
                {
                    "available": True,
                    "payload": {
                        "daily": [
                            {
                                "date": "2026-03-31",
                                "inputTokens": 100,
                                "cachedInputTokens": 0,
                                "outputTokens": 10,
                                "reasoningOutputTokens": 0,
                                "totalTokens": 110,
                                "costUSD": 1.0,
                            },
                            {
                                "date": "2026-04-01",
                                "inputTokens": 200,
                                "cachedInputTokens": 50,
                                "outputTokens": 40,
                                "reasoningOutputTokens": 5,
                                "totalTokens": 240,
                                "costUSD": 2.0,
                            },
                            {
                                "date": "2026-04-20",
                                "inputTokens": 300,
                                "cachedInputTokens": 100,
                                "outputTokens": 60,
                                "reasoningOutputTokens": 10,
                                "totalTokens": 360,
                                "costUSD": 3.0,
                            },
                            {
                                "date": "2026-05-02",
                                "inputTokens": 500,
                                "cachedInputTokens": 125,
                                "cacheCreationTokens": 300,
                                "outputTokens": 100,
                                "reasoningOutputTokens": 20,
                                "totalTokens": 600,
                                "costUSD": 5.0,
                            },
                        ]
                    },
                    "error": "",
                    "fetched_at": "2026-05-31T12:00:00+08:00",
                    "window_days": 61,
                },
                language="zh",
                group_by="month",
                start_date="2026-04-01",
                end_date="2026-05-31",
            )

        self.assertEqual(view["group_by"], "month")
        self.assertEqual(view["range_start"], "2026-04-01")
        self.assertEqual(view["range_end"], "2026-05-31")
        self.assertEqual([row["label"] for row in view["daily_rows"]], ["2026-04", "2026-05"])
        self.assertEqual([row["value"] for row in view["daily_rows"]], [600, 600])
        self.assertEqual(view["period_total_tokens"], 1200)
        self.assertEqual(view["active_period_count"], 2)
        self.assertEqual(view["today_date_label"], "2026-05")
        self.assertEqual(view["daily_rows"][-1]["uncachedInputTokens"], 75)
        self.assertEqual(view["daily_rows"][-1]["cacheCreationTokens"], 300)
        self.assertEqual(view["today_breakdown"][0]["value"], 75)
        self.assertEqual(view["today_breakdown"][2]["label"], "缓存写入")
        self.assertEqual(view["today_breakdown"][2]["value"], 300)
        self.assertIn("峰值月", [card["label"] for card in view["summary_cards"]])

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
                            "meta": "自动化 / 仅个人使用 / OpenRelix",
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
                "display_type": "skills",
                "display_scope": "仅个人使用",
                "display_context": "OpenRelix",
            },
            {
                "id": "asset-b",
                "title": "B Skill",
                "type": "skill",
                "scope": "repo",
                "domain": "android",
                "display_type": "skills",
                "display_scope": "仓库场景复用",
                "display_context": "Android App",
            },
        ]

        rows = build_overview.build_asset_mix_rows(
            assets,
            lambda asset: asset.get("type", "unknown"),
            lambda value: build_overview.display_label("type", value),
        )

        self.assertEqual(rows[0]["label"], "skills")
        self.assertEqual(rows[0]["value"], 2)
        self.assertEqual([item["title"] for item in rows[0]["details"]], ["A Skill", "B Skill"])
        self.assertIn("OpenRelix", rows[0]["details"][0]["meta"])

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
            "note_zh": "已有 skills 提供了验证流程。",
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
        self.assertIn('<span data-lang-only="zh">飞书画板 CLI 方法</span>', asset_rows)
        self.assertIn('<span data-lang-only="en">Lark Whiteboard CLI Playbook</span>', asset_rows)
        self.assertIn('<span data-lang-only="zh">已验证本地渲染和 dry-run 上传路径。</span>', asset_rows)
        self.assertNotIn("English display title", asset_rows)
        self.assertIn('<span data-lang-only="zh">飞书画板 CLI 能力检查</span>', usage_rows)
        self.assertIn('<span data-lang-only="en">lark-cli whiteboard-cli capability check</span>', usage_rows)

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
        asset_template = (ROOT / "templates" / "asset-generation-template.md").read_text(encoding="utf-8")
        skill_template = (ROOT / "templates" / "skill-draft-template.md").read_text(encoding="utf-8")

        self.assertIn("Resolve runtime language", skill_text)
        self.assertIn("asset `title` / `source_task` / `value_note` / `notes`", skill_text)
        self.assertIn("Assetization gate", skill_text)
        self.assertIn("templates/asset-generation-template.md", skill_text)
        self.assertIn("registry/memory_entries.jsonl", skill_text)
        self.assertIn("project scope", skill_text)
        self.assertIn("Asset generation template", prompt_text)
        self.assertIn("Skill draft template", prompt_text)
        self.assertIn("classify the reusable value", prompt_text)
        self.assertIn("usage-event `task` / `note`", prompt_text)
        self.assertIn("Memory item row shape", asset_template)
        self.assertIn("source_review_path", asset_template)
        self.assertIn("Scope decision guide", skill_template)
        self.assertIn("CODEX_HOME/skills", skill_template)

    def test_codex_plugin_packaging_includes_memory_review_skill(self):
        canonical_skill = (ROOT / ".agents" / "skills" / "memory-review" / "SKILL.md").read_text(encoding="utf-8")
        plugin_skill_path = ROOT / "plugins" / "openrelix" / "skills" / "memory-review" / "SKILL.md"
        plugin_skill = plugin_skill_path.read_text(encoding="utf-8")
        marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

        self.assertFalse(plugin_skill_path.is_symlink())
        self.assertEqual(plugin_skill, canonical_skill)
        self.assertEqual(marketplace["plugins"][0]["policy"]["installation"], "AVAILABLE")
        self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugins/openrelix")
        self.assertIn("plugins/openrelix/", package_json["files"])
        self.assertIn(".agents/plugins/marketplace.json", package_json["files"])
        self.assertNotIn("install/", package_json["files"])
        self.assertIn("install/*.py", package_json["files"])
        self.assertIn("install/templates/", package_json["files"])
        self.assertIn("scripts/build_codex_native_display_cache.py", package_json["files"])
        self.assertIn("scripts/openrelix_index.py", package_json["files"])

    def test_project_version_helpers_use_package_json(self):
        package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(asset_runtime.get_project_version(ROOT), package_json["version"])
        self.assertEqual(openrelix.read_local_package_version(), package_json["version"])
        self.assertEqual(build_overview.read_panel_package_version(), package_json["version"])

    def test_static_showcase_version_meta_matches_package_json(self):
        package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        version = package_json["version"]

        for relative in ("docs/product-showcase.html", "docs/index.html"):
            html = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(
                '<meta name="openrelix:version" content="{}"'.format(version),
                html,
            )
            self.assertNotIn("v{} 预览版".format(version), html)
            self.assertNotIn("v{} preview".format(version), html)

    def test_asset_value_estimation_uses_events_and_recent_windows(self):
        asset = {
            "id": "ai_asset_overview_pipeline",
            "title": "AI 资产概览链路",
            "type": "automation",
            "scope": "personal",
            "domain": "general",
            "updated_at": "2026-04-27",
            "tags": ["overview", "panel"],
            "artifact_paths": ["/tmp/OpenRelix/scripts/build_overview.py"],
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
        self.assertIn(".token-overview-panel {{\n      display: grid;\n      gap: 18px;\n      overflow: visible;", source)
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
        self.assertIn("window_id", learning["window_samples"][0])
        self.assertTrue(learning["batch_summaries"][0]["sample_window_ids"])
        self.assertTrue(learning["context_patterns"][0]["sample_window_ids"])
        self.assertLessEqual(
            len(learning["batch_summaries"][0]["sample_window_ids"]),
            nightly_consolidate.LEARNING_WINDOW_BATCH_ID_LIMIT,
        )

        digest = nightly_consolidate.build_learning_context_digest(
            {"recent_window_learning": learning},
            1,
        )
        self.assertEqual(digest["recent_window_learning_scanned_days"], 1)
        self.assertEqual(digest["recent_window_learning_source_dates"], 1)
        self.assertEqual(digest["recent_window_learning_windows"], 25)
        self.assertEqual(digest["recent_window_learning_batches"], 2)

    def test_summary_skip_requires_requested_stage_and_successful_model(self):
        summary = {
            "learning_input_fingerprint": "abc123",
            "stage": "preliminary",
        }

        self.assertTrue(
            nightly_consolidate.summary_can_skip_for_learning_input(
                summary,
                "abc123",
                "manual",
            )
        )
        self.assertFalse(
            nightly_consolidate.summary_can_skip_for_learning_input(
                summary,
                "abc123",
                "final",
            )
        )

        summary["stage"] = "final"
        summary["last_run_model_status"] = "failed"
        self.assertFalse(
            nightly_consolidate.summary_can_skip_for_learning_input(
                summary,
                "abc123",
                "final",
            )
        )

    def test_compact_payload_cache_reuses_clustered_payload_and_invalidates_content(self):
        raw_payload = {
            "date": "2026-04-27",
            "window_count": 1,
            "prompt_count": 1,
            "conclusion_count": 1,
            "windows": [
                {
                    "window_id": "w1",
                    "cwd": "/tmp/demo",
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "prompts": [{"text": "cache this prompt"}],
                    "conclusions": [{"text": "cache this conclusion"}],
                }
            ],
        }

        nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear()
        self.addCleanup(nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear)
        with TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            with mock.patch.object(
                nightly_consolidate,
                "build_text_clusters",
                wraps=nightly_consolidate.build_text_clusters,
            ) as clustered:
                first = nightly_consolidate.build_compact_payload(
                    raw_payload,
                    language="zh",
                    cache_dir=cache_dir,
                )
                self.assertEqual(clustered.call_count, 2)

                nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear()
                second = nightly_consolidate.build_compact_payload(
                    raw_payload,
                    language="zh",
                    cache_dir=cache_dir,
                )
                self.assertEqual(first, second)
                self.assertEqual(clustered.call_count, 2)

                changed_payload = json.loads(json.dumps(raw_payload))
                changed_payload["windows"][0]["prompts"][0]["text"] = "changed prompt"
                changed = nightly_consolidate.build_compact_payload(
                    changed_payload,
                    language="zh",
                    cache_dir=cache_dir,
                )

        self.assertNotEqual(changed, first)
        self.assertEqual(clustered.call_count, 4)

    def test_preliminary_consolidate_writes_lightweight_summary_without_model(self):
        old_raw_dir = nightly_consolidate.RAW_DIR
        old_consolidated_dir = nightly_consolidate.CONSOLIDATED_DIR
        old_registry_dir = nightly_consolidate.REGISTRY_DIR
        old_runtime_dir = nightly_consolidate.RUNTIME_DIR
        old_language = nightly_consolidate.LANGUAGE
        old_memory_mode = nightly_consolidate.MEMORY_MODE
        old_personal_memory_enabled = nightly_consolidate.PERSONAL_MEMORY_ENABLED
        try:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                nightly_consolidate.RAW_DIR = tmp / "raw"
                nightly_consolidate.CONSOLIDATED_DIR = tmp / "consolidated" / "daily"
                nightly_consolidate.REGISTRY_DIR = tmp / "registry"
                nightly_consolidate.RUNTIME_DIR = tmp / "runtime"
                nightly_consolidate.LANGUAGE = "zh"
                nightly_consolidate.MEMORY_MODE = "integrated"
                nightly_consolidate.PERSONAL_MEMORY_ENABLED = True

                raw_daily_dir = nightly_consolidate.RAW_DIR / "daily"
                raw_daily_dir.mkdir(parents=True)
                raw_payload = {
                    "date": "2026-04-28",
                    "window_count": 1,
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "review_like_window_count": 0,
                    "windows": [
                        {
                            "window_id": "w1",
                            "cwd": "/tmp/openrelix",
                            "prompt_count": 1,
                            "conclusion_count": 1,
                            "prompts": [{"text": "先做轻量回溯"}],
                            "conclusions": [{"text": "轻量层要给 final 复用"}],
                        }
                    ],
                }
                (raw_daily_dir / "2026-04-28.json").write_text(
                    json.dumps(raw_payload),
                    encoding="utf-8",
                )

                with mock.patch.object(nightly_consolidate, "ensure_state_layout"), mock.patch.object(
                    nightly_consolidate,
                    "run_codex_consolidation",
                    side_effect=AssertionError("preliminary should not run the model"),
                ) as run_model, mock.patch.object(
                    sys,
                    "argv",
                    [
                        "nightly_consolidate.py",
                        "--date",
                        "2026-04-28",
                        "--stage",
                        "preliminary",
                        "--skip-if-unchanged",
                    ],
                ):
                    nightly_consolidate.main()

                summary_path = nightly_consolidate.CONSOLIDATED_DIR / "2026-04-28" / "summary.json"
                compact_path = nightly_consolidate.CONSOLIDATED_DIR / "2026-04-28" / "compact_payload.json"
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                registry_rows = [
                    json.loads(line)
                    for line in (nightly_consolidate.REGISTRY_DIR / "memory_entries.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ]

                run_model.assert_not_called()
                self.assertTrue(compact_path.exists())
                self.assertEqual(summary["stage"], "preliminary")
                self.assertEqual(summary["model_status"], "skipped_lightweight")
                self.assertEqual(summary["summary_generation"], "lightweight")
                self.assertEqual(summary["compact_payload_source"], "fresh")
                self.assertIn("轻量日报", summary["day_summary"])
                self.assertIn("记忆整理：浅度阶段跳过", summary["day_summary"])
                self.assertNotIn("可能不准确", summary["day_summary"])
                self.assertIn("openrelix backfill --from 2026-04-28 --to 2026-04-28", summary["next_actions"][0])
                self.assertEqual(summary["window_summaries"][0]["main_takeaway"], "轻量层要给 final 复用")
                self.assertGreater(len(summary["window_summaries"][0]["keywords"]), 1)
                self.assertEqual(summary["durable_memories"], [])
                self.assertEqual(summary["session_memories"], [])
                self.assertEqual(summary["low_priority_memories"], [])
                self.assertTrue(summary["lightweight_memory_deferred"])
                self.assertEqual(summary["lightweight_memory_limit"], 0)
                self.assertEqual(registry_rows, [])
        finally:
            nightly_consolidate.RAW_DIR = old_raw_dir
            nightly_consolidate.CONSOLIDATED_DIR = old_consolidated_dir
            nightly_consolidate.REGISTRY_DIR = old_registry_dir
            nightly_consolidate.RUNTIME_DIR = old_runtime_dir
            nightly_consolidate.LANGUAGE = old_language
            nightly_consolidate.MEMORY_MODE = old_memory_mode
            nightly_consolidate.PERSONAL_MEMORY_ENABLED = old_personal_memory_enabled

    def test_lightweight_summary_defers_memory_outputs(self):
        windows = []
        for index in range(30):
            windows.append(
                {
                    "window_id": "w{}".format(index),
                    "cwd": "/tmp/project-{}".format(index % 3),
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "prompt_cluster_count": 1,
                    "conclusion_cluster_count": 1,
                    "prompt_samples": ["问题 {}".format(index)],
                    "conclusion_samples": ["结论 {}".format(index)],
                }
            )
        raw_payload = {
            "date": "2026-04-28",
            "window_count": len(windows),
            "prompt_count": len(windows),
            "conclusion_count": len(windows),
        }
        compact_payload = {
            **raw_payload,
            "windows": windows,
        }

        summary = nightly_consolidate.build_lightweight_summary(
            raw_payload,
            compact_payload,
            language="zh",
        )

        self.assertEqual(summary["durable_memories"], [])
        self.assertEqual(summary["session_memories"], [])
        self.assertEqual(summary["low_priority_memories"], [])
        self.assertEqual(summary["lightweight_memory_candidate_count"], 30)
        self.assertEqual(summary["lightweight_memory_limit"], 0)
        self.assertEqual(summary["lightweight_durable_memory_limit"], 0)
        self.assertEqual(summary["lightweight_low_priority_memory_limit"], 0)
        self.assertTrue(summary["lightweight_memory_deferred"])
        self.assertEqual(summary["lightweight_memory_deferred_reason"], "preliminary_defers_memory_to_final")
        self.assertEqual(summary["lightweight_summary_version"], nightly_consolidate.LIGHTWEIGHT_SUMMARY_VERSION)

    def test_daily_memory_storage_rows_are_capped_by_bucket_quality(self):
        rows = []
        for index in range(nightly_consolidate.MAX_DAILY_DURABLE_MEMORY_ITEMS + 3):
            rows.append(
                {
                    "bucket": "durable",
                    "priority": "high",
                    "memory_type": "procedural",
                    "title": "durable-{}".format(index),
                    "source_window_ids": ["w{}".format(index)],
                    "storage_quality_score": index,
                }
            )
        for index in range(nightly_consolidate.MAX_DAILY_SESSION_MEMORY_ITEMS + 3):
            rows.append(
                {
                    "bucket": "session",
                    "priority": "medium",
                    "memory_type": "semantic",
                    "title": "session-{}".format(index),
                    "source_window_ids": ["s{}".format(index)],
                    "storage_quality_score": index,
                }
            )
        for index in range(nightly_consolidate.MAX_DAILY_LOW_PRIORITY_MEMORY_ITEMS + 3):
            rows.append(
                {
                    "bucket": "low_priority",
                    "priority": "low",
                    "memory_type": "task",
                    "title": "low-{}".format(index),
                    "source_window_ids": ["l{}".format(index)],
                    "storage_quality_score": index,
                }
            )

        selected = nightly_consolidate.select_daily_memory_rows_for_storage(rows)
        counts = {}
        for row in selected:
            counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1

        self.assertEqual(counts["durable"], nightly_consolidate.MAX_DAILY_DURABLE_MEMORY_ITEMS)
        self.assertEqual(counts["session"], nightly_consolidate.MAX_DAILY_SESSION_MEMORY_ITEMS)
        self.assertEqual(counts["low_priority"], nightly_consolidate.MAX_DAILY_LOW_PRIORITY_MEMORY_ITEMS)
        selected_titles = {row["title"] for row in selected}
        self.assertIn("durable-{}".format(nightly_consolidate.MAX_DAILY_DURABLE_MEMORY_ITEMS + 2), selected_titles)
        self.assertNotIn("durable-0", selected_titles)

    def test_lightweight_summary_uses_deep_style_pairs_and_daily_sections(self):
        raw_payload = {
            "date": "2026-04-28",
            "window_count": 1,
            "prompt_count": 2,
            "conclusion_count": 2,
        }
        compact_payload = {
            **raw_payload,
            "windows": [
                {
                    "window_id": "w-format",
                    "cwd": "/tmp/openrelix",
                    "prompt_count": 2,
                    "conclusion_count": 2,
                    "prompt_cluster_count": 2,
                    "conclusion_cluster_count": 2,
                    "prompt_samples": [
                        "轻度回溯后的每日总结要和深度回溯一样",
                        "窗口卡片也需要展示多条问题与结论",
                    ],
                    "conclusion_samples": [
                        "轻量路径应生成多条 summary_pairs",
                        "每日摘要要拆出范围、记忆和代表问答",
                    ],
                }
            ],
        }

        summary = nightly_consolidate.build_lightweight_summary(
            raw_payload,
            compact_payload,
            language="zh",
        )
        window = summary["window_summaries"][0]

        self.assertEqual(
            window["summary_pairs"],
            [
                {
                    "question": "轻度回溯后的每日总结要和深度回溯一样",
                    "conclusion": "轻量路径应生成多条 summary_pairs",
                },
                {
                    "question": "窗口卡片也需要展示多条问题与结论",
                    "conclusion": "每日摘要要拆出范围、记忆和代表问答",
                },
            ],
        )
        self.assertIn("问题1：轻度回溯后的每日总结要和深度回溯一样", window["question_summary"])
        self.assertIn("结论2：每日摘要要拆出范围、记忆和代表问答", window["main_takeaway"])
        self.assertIn("记忆整理：浅度阶段跳过", summary["day_summary"])
        self.assertIn("重点窗口", summary["day_summary"])
        self.assertIn("代表问答", summary["day_summary"])
        self.assertGreaterEqual(len(build_overview.split_nightly_summary(summary["day_summary"])), 4)

    def test_lightweight_summary_does_not_keep_tail_candidates_as_low_priority(self):
        windows = []
        for index in range(6):
            windows.append(
                {
                    "window_id": "w{}".format(index),
                    "cwd": "/tmp/project",
                    "prompt_count": 1,
                    "conclusion_count": 1 if index < 3 else 0,
                    "prompt_cluster_count": 1,
                    "conclusion_cluster_count": 1 if index < 3 else 0,
                    "prompt_samples": ["低优先候选 {}".format(index)],
                    "conclusion_samples": ["可复用结论 {}".format(index)] if index < 3 else [],
                }
            )
        raw_payload = {
            "date": "2026-04-28",
            "window_count": len(windows),
            "prompt_count": len(windows),
            "conclusion_count": 3,
        }
        compact_payload = {**raw_payload, "windows": windows}

        summary = nightly_consolidate.build_lightweight_summary(
            raw_payload,
            compact_payload,
            language="zh",
        )

        self.assertEqual(summary["durable_memories"], [])
        self.assertEqual(summary["session_memories"], [])
        self.assertEqual(summary["low_priority_memories"], [])
        self.assertEqual(summary["lightweight_memory_candidate_count"], 6)
        self.assertTrue(summary["lightweight_memory_deferred"])

    def test_lightweight_summary_keywords_keep_window_terms(self):
        raw_payload = {
            "date": "2026-04-30",
            "window_count": 1,
            "prompt_count": 3,
            "conclusion_count": 3,
        }
        compact_payload = {
            **raw_payload,
            "windows": [
                {
                    "window_id": "w-camera",
                    "cwd": "/tmp/Douyin",
                    "prompt_count": 3,
                    "conclusion_count": 3,
                    "prompt_cluster_count": 1,
                    "conclusion_cluster_count": 1,
                    "prompt_samples": [
                        "长按相机拍摄按钮后，我需要屏蔽除了按钮上方提示区外的全部UI，包括相机的实时tag、tab栏、工具区、关闭按钮等等元素，帮我设计技术方案，做独立审阅，然后实现，再独立审阅代码和走自测。"
                    ],
                    "conclusion_samples": ["相机页已经打开，可以用 wrangler 看看。"],
                }
            ],
        }

        summary = nightly_consolidate.build_lightweight_summary(
            raw_payload,
            compact_payload,
            language="zh",
        )
        keywords = summary["window_summaries"][0]["keywords"]

        self.assertEqual(
            keywords[:8],
            ["Douyin", "长按相机拍摄按钮", "按钮上方提示区", "全部UI", "实时tag", "tab栏", "工具区", "关闭按钮"],
        )
        self.assertIn("全部UI", summary["keywords"])
        self.assertEqual(summary["durable_memories"], [])
        self.assertEqual(summary["lightweight_memory_candidate_count"], 1)

    def test_lightweight_summary_filters_sentence_like_keywords(self):
        raw_payload = {
            "date": "2026-05-03",
            "window_count": 1,
            "prompt_count": 1,
            "conclusion_count": 1,
        }
        compact_payload = {
            **raw_payload,
            "windows": [
                {
                    "window_id": "w-token-cost",
                    "cwd": "/tmp/OpenRelix",
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "prompt_cluster_count": 1,
                    "conclusion_cluster_count": 1,
                    "prompt_samples": ["我们一般说token花销，最关注什么指标呢？ [Image]"],
                    "conclusion_samples": [
                        "一般说 **token 花销**，最该盯的是 Cost (USD)，其次才是 token 数量。"
                    ],
                }
            ],
        }

        summary = nightly_consolidate.build_lightweight_summary(
            raw_payload,
            compact_payload,
            language="zh",
        )
        keywords = summary["window_summaries"][0]["keywords"]

        self.assertEqual(keywords, ["OpenRelix", "token花销", "Cost (USD)", "token数量"])
        self.assertNotIn("Image", keywords)
        self.assertNotIn("是Cost", keywords)
        self.assertFalse(any("什么指标" in keyword for keyword in keywords))
        self.assertFalse(any("一般说" in keyword for keyword in keywords))
        self.assertFalse(any("最该盯" in keyword for keyword in keywords))
        self.assertFalse(any("**" in keyword for keyword in keywords))
        self.assertIn("token花销", summary["keywords"])


    def test_final_consolidate_reuses_lightweight_compact_artifact(self):
        old_raw_dir = nightly_consolidate.RAW_DIR
        old_consolidated_dir = nightly_consolidate.CONSOLIDATED_DIR
        old_registry_dir = nightly_consolidate.REGISTRY_DIR
        old_runtime_dir = nightly_consolidate.RUNTIME_DIR
        old_language = nightly_consolidate.LANGUAGE
        old_memory_mode = nightly_consolidate.MEMORY_MODE
        old_personal_memory_enabled = nightly_consolidate.PERSONAL_MEMORY_ENABLED
        old_model_cli = nightly_consolidate.MODEL_CLI
        try:
            nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear()
            self.addCleanup(nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear)
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                nightly_consolidate.RAW_DIR = tmp / "raw"
                nightly_consolidate.CONSOLIDATED_DIR = tmp / "consolidated" / "daily"
                nightly_consolidate.REGISTRY_DIR = tmp / "registry"
                nightly_consolidate.RUNTIME_DIR = tmp / "runtime"
                nightly_consolidate.LANGUAGE = "zh"
                nightly_consolidate.MEMORY_MODE = "integrated"
                nightly_consolidate.PERSONAL_MEMORY_ENABLED = True
                nightly_consolidate.MODEL_CLI = "codex"

                raw_daily_dir = nightly_consolidate.RAW_DIR / "daily"
                summary_dir = nightly_consolidate.CONSOLIDATED_DIR / "2026-04-28"
                raw_daily_dir.mkdir(parents=True)
                summary_dir.mkdir(parents=True)
                raw_payload = {
                    "date": "2026-04-28",
                    "window_count": 1,
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "review_like_window_count": 0,
                    "windows": [
                        {
                            "window_id": "w1",
                            "cwd": "/tmp/openrelix",
                            "prompt_count": 1,
                            "conclusion_count": 1,
                            "prompts": [{"text": "final 继续深度整理"}],
                            "conclusions": [{"text": "复用 preliminary 的压缩层"}],
                        }
                    ],
                }
                (raw_daily_dir / "2026-04-28.json").write_text(
                    json.dumps(raw_payload),
                    encoding="utf-8",
                )
                compact_payload = nightly_consolidate.build_compact_payload(
                    raw_payload,
                    language="zh",
                    cache_dir=tmp / "cache",
                )
                nightly_consolidate.write_daily_compact_payload(
                    summary_dir,
                    raw_payload,
                    compact_payload,
                    language="zh",
                )
                nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear()

                def fake_run_model(prompt, output_path, language=None, timeout_seconds=None):
                    self.assertIn("复用 preliminary 的压缩层", prompt)
                    output_path.write_text(
                        json.dumps(
                            {
                                "date": "2026-04-28",
                                "day_summary": "final done",
                                "window_summaries": [
                                    {
                                        "window_id": "w1",
                                        "window_title": "final",
                                        "question_summary": "final 继续深度整理",
                                        "main_takeaway": "复用 preliminary 的压缩层",
                                        "keywords": ["OpenRelix"],
                                        "summary_pairs": [
                                            {
                                                "question": "final 继续深度整理",
                                                "conclusion": "复用 preliminary 的压缩层",
                                            }
                                        ],
                                    }
                                ],
                                "durable_memories": [make_memory("final-memory")],
                                "session_memories": [],
                                "low_priority_memories": [],
                                "keywords": ["OpenRelix"],
                                "next_actions": [],
                            }
                        ),
                        encoding="utf-8",
                    )

                with mock.patch.object(nightly_consolidate, "ensure_state_layout"), mock.patch.object(
                    nightly_consolidate,
                    "build_text_clusters",
                    side_effect=AssertionError("final should reuse the daily compact artifact"),
                ), mock.patch.object(
                    nightly_consolidate,
                    "run_codex_consolidation",
                    side_effect=fake_run_model,
                ), mock.patch.object(
                    sys,
                    "argv",
                    [
                        "nightly_consolidate.py",
                        "--date",
                        "2026-04-28",
                        "--stage",
                        "final",
                    ],
                ):
                    nightly_consolidate.main()

                summary = json.loads((summary_dir / "summary.json").read_text(encoding="utf-8"))
                self.assertEqual(summary["stage"], "final")
                self.assertEqual(summary["model_status"], "completed")
                self.assertEqual(summary["compact_payload_source"], "daily_artifact")
                self.assertEqual(
                    summary["selection_decision"]["compact_payload_source"],
                    "daily_artifact",
                )
        finally:
            nightly_consolidate.RAW_DIR = old_raw_dir
            nightly_consolidate.CONSOLIDATED_DIR = old_consolidated_dir
            nightly_consolidate.REGISTRY_DIR = old_registry_dir
            nightly_consolidate.RUNTIME_DIR = old_runtime_dir
            nightly_consolidate.LANGUAGE = old_language
            nightly_consolidate.MEMORY_MODE = old_memory_mode
            nightly_consolidate.PERSONAL_MEMORY_ENABLED = old_personal_memory_enabled
            nightly_consolidate.MODEL_CLI = old_model_cli

    def test_final_consolidate_stores_historical_global_context_memories(self):
        old_raw_dir = nightly_consolidate.RAW_DIR
        old_consolidated_dir = nightly_consolidate.CONSOLIDATED_DIR
        old_registry_dir = nightly_consolidate.REGISTRY_DIR
        old_runtime_dir = nightly_consolidate.RUNTIME_DIR
        old_language = nightly_consolidate.LANGUAGE
        old_memory_mode = nightly_consolidate.MEMORY_MODE
        old_personal_memory_enabled = nightly_consolidate.PERSONAL_MEMORY_ENABLED
        old_model_cli = nightly_consolidate.MODEL_CLI
        try:
            nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear()
            self.addCleanup(nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear)
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                nightly_consolidate.RAW_DIR = tmp / "raw"
                nightly_consolidate.CONSOLIDATED_DIR = tmp / "consolidated" / "daily"
                nightly_consolidate.REGISTRY_DIR = tmp / "registry"
                nightly_consolidate.RUNTIME_DIR = tmp / "runtime"
                nightly_consolidate.LANGUAGE = "zh"
                nightly_consolidate.MEMORY_MODE = "integrated"
                nightly_consolidate.PERSONAL_MEMORY_ENABLED = True
                nightly_consolidate.MODEL_CLI = "codex"

                raw_daily_dir = nightly_consolidate.RAW_DIR / "daily"
                raw_daily_dir.mkdir(parents=True)
                (raw_daily_dir / "2026-04-27.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-27",
                            "window_count": 2,
                            "prompt_count": 2,
                            "conclusion_count": 2,
                            "windows": [
                                {
                                    "window_id": "hist-a",
                                    "cwd": "/tmp/openrelix",
                                    "prompt_count": 1,
                                    "conclusion_count": 1,
                                    "prompts": [{"text": "需求不清时先讨论契约"}],
                                    "conclusions": [{"text": "先确认契约和运行证据，再改代码"}],
                                },
                                {
                                    "window_id": "hist-b",
                                    "cwd": "/tmp/another-repo",
                                    "prompt_count": 1,
                                    "conclusion_count": 1,
                                    "prompts": [{"text": "改动前先说明判断依据"}],
                                    "conclusions": [{"text": "历史窗口显示用户偏好先给证据再执行"}],
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                (raw_daily_dir / "2026-04-28.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-28",
                            "window_count": 0,
                            "prompt_count": 0,
                            "conclusion_count": 0,
                            "windows": [],
                        }
                    ),
                    encoding="utf-8",
                )

                def fake_run_model(prompt, output_path, language=None, timeout_seconds=None):
                    self.assertIn("recent_window_learning", prompt)
                    self.assertIn("global_context_memories", prompt)
                    self.assertIn("hist-a", prompt)
                    output_path.write_text(
                        json.dumps(
                            {
                                "date": "2026-04-28",
                                "day_summary": "当日无新窗口，但已从历史窗口学习通用上下文。",
                                "window_summaries": [],
                                "durable_memories": [],
                                "session_memories": [],
                                "low_priority_memories": [],
                                "global_context_memories": [
                                    {
                                        "title": "用户习惯：先确认契约再改代码",
                                        "memory_type": "preference",
                                        "priority": "high",
                                        "value_note": "跨多个历史窗口反复出现：需求不清时先确认契约和运行证据，再做代码修改。",
                                        "source_window_ids": ["hist-a", "hist-b"],
                                        "keywords": ["用户习惯", "契约确认", "运行证据"],
                                        "occurrence_count": 2,
                                        "evidence_contexts": ["OpenRelix", "another repo"],
                                        "source_dates": ["2026-04-27"],
                                    }
                                ],
                                "keywords": ["通用上下文"],
                                "next_actions": [],
                            }
                        ),
                        encoding="utf-8",
                    )

                with mock.patch.object(nightly_consolidate, "ensure_state_layout"), mock.patch.object(
                    nightly_consolidate,
                    "run_codex_consolidation",
                    side_effect=fake_run_model,
                ), mock.patch.object(
                    sys,
                    "argv",
                    [
                        "nightly_consolidate.py",
                        "--date",
                        "2026-04-28",
                        "--stage",
                        "final",
                        "--learn-window-days",
                        "1",
                    ],
                ):
                    nightly_consolidate.main()

                summary = json.loads(
                    (nightly_consolidate.CONSOLIDATED_DIR / "2026-04-28" / "summary.json").read_text(
                        encoding="utf-8"
                    )
                )
                registry_rows = [
                    json.loads(line)
                    for line in (nightly_consolidate.REGISTRY_DIR / "memory_entries.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ]

                self.assertEqual(len(summary["global_context_memories"]), 1)
                self.assertEqual(summary["global_context_memories"][0]["source_window_ids"], ["hist-a", "hist-b"])
                self.assertEqual(len(registry_rows), 1)
                self.assertEqual(registry_rows[0]["scope"], "global")
                self.assertEqual(registry_rows[0]["injection_policy"], "global_context")
                self.assertEqual(registry_rows[0]["source_systems"], ["historical_window_learning"])
                self.assertEqual(registry_rows[0]["source_window_ids"], ["hist-a", "hist-b"])
                self.assertEqual(
                    overview_memory_context.host_context_injection_policy_from_record(registry_rows[0]),
                    "global_context",
                )
        finally:
            nightly_consolidate.RAW_DIR = old_raw_dir
            nightly_consolidate.CONSOLIDATED_DIR = old_consolidated_dir
            nightly_consolidate.REGISTRY_DIR = old_registry_dir
            nightly_consolidate.RUNTIME_DIR = old_runtime_dir
            nightly_consolidate.LANGUAGE = old_language
            nightly_consolidate.MEMORY_MODE = old_memory_mode
            nightly_consolidate.PERSONAL_MEMORY_ENABLED = old_personal_memory_enabled
            nightly_consolidate.MODEL_CLI = old_model_cli

    def test_compact_payload_cache_wrong_shape_is_ignored(self):
        raw_payload = {
            "date": "2026-04-27",
            "window_count": 1,
            "prompt_count": 1,
            "conclusion_count": 1,
            "windows": [
                {
                    "window_id": "w1",
                    "cwd": "/tmp/demo",
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "prompts": [{"text": "fresh prompt after bad cache"}],
                    "conclusions": [{"text": "fresh conclusion after bad cache"}],
                }
            ],
        }

        nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear()
        self.addCleanup(nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear)
        with TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            fingerprint = nightly_consolidate.compact_payload_fingerprint(raw_payload, language="zh")
            cache_path = cache_dir / "compact-payload" / "{}.json".format(fingerprint)
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text("[]", encoding="utf-8")

            with mock.patch.object(
                nightly_consolidate,
                "build_text_clusters",
                wraps=nightly_consolidate.build_text_clusters,
            ) as clustered:
                compact = nightly_consolidate.build_compact_payload(
                    raw_payload,
                    language="zh",
                    cache_dir=cache_dir,
                )
                self.assertEqual(clustered.call_count, 2)

                nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear()
                cache_path.write_text(
                    json.dumps(
                        {
                            "fingerprint": fingerprint,
                            "compact_payload": {
                                "date": "2026-04-27",
                                "window_count": 1,
                                "prompt_count": 1,
                                "conclusion_count": 1,
                                "windows": [],
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                compact_from_malformed_payload = nightly_consolidate.build_compact_payload(
                    raw_payload,
                    language="zh",
                    cache_dir=cache_dir,
                )
                self.assertEqual(clustered.call_count, 4)

                nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear()
                cache_path.write_text(
                    json.dumps(
                        {
                            "fingerprint": fingerprint,
                            "compact_payload": {
                                "date": "2026-04-27",
                                "window_count": 1,
                                "prompt_count": 1,
                                "conclusion_count": 1,
                                "windows": [
                                    {
                                        "window_id": "stale-window",
                                        "cwd": "/tmp/other",
                                        "prompt_count": 1,
                                        "conclusion_count": 1,
                                        "prompt_cluster_count": 1,
                                        "conclusion_cluster_count": 1,
                                        "prompt_samples": ["stale prompt"],
                                        "conclusion_samples": ["stale conclusion"],
                                    }
                                ],
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                compact_from_stale_window = nightly_consolidate.build_compact_payload(
                    raw_payload,
                    language="zh",
                    cache_dir=cache_dir,
                )
                self.assertEqual(clustered.call_count, 6)

                nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear()
                cache_path.write_text(
                    json.dumps(
                        {
                            "fingerprint": fingerprint,
                            "compact_payload": {
                                "date": "2026-04-27",
                                "window_count": 1,
                                "prompt_count": 1,
                                "conclusion_count": 1,
                                "windows": [
                                    {
                                        "window_id": "w1",
                                        "cwd": "/tmp/demo",
                                        "prompt_count": 1,
                                        "conclusion_count": 1,
                                        "prompt_cluster_count": 2,
                                        "conclusion_cluster_count": 1,
                                        "prompt_samples": [
                                            "fresh prompt after bad cache",
                                            "stale extra",
                                        ],
                                        "conclusion_samples": ["fresh conclusion after bad cache"],
                                    }
                                ],
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                compact_from_impossible_samples = nightly_consolidate.build_compact_payload(
                    raw_payload,
                    language="zh",
                    cache_dir=cache_dir,
                )

        self.assertEqual(compact["windows"][0]["prompt_samples"], ["fresh prompt after bad cache"])
        self.assertEqual(
            compact_from_malformed_payload["windows"][0]["prompt_samples"],
            ["fresh prompt after bad cache"],
        )
        self.assertEqual(
            compact_from_stale_window["windows"][0]["prompt_samples"],
            ["fresh prompt after bad cache"],
        )
        self.assertEqual(
            compact_from_impossible_samples["windows"][0]["prompt_samples"],
            ["fresh prompt after bad cache"],
        )
        self.assertEqual(clustered.call_count, 8)

    def test_nightly_cache_write_failures_do_not_break_fresh_results(self):
        raw_payload = {
            "date": "2026-04-27",
            "window_count": 1,
            "prompt_count": 1,
            "conclusion_count": 1,
            "windows": [
                {
                    "window_id": "w1",
                    "cwd": "/tmp/demo",
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "prompts": [{"text": "cache write should be optional"}],
                    "conclusions": [{"text": "fresh compact data still returns"}],
                }
            ],
        }

        old_raw_dir = nightly_consolidate.RAW_DIR
        old_consolidated_dir = nightly_consolidate.CONSOLIDATED_DIR
        try:
            nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear()
            nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear()
            self.addCleanup(nightly_consolidate._COMPACT_PAYLOAD_CACHE.clear)
            self.addCleanup(nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear)
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                cache_dir = tmp / "cache"
                cache_dir.mkdir()
                (cache_dir / "compact-payload").write_text("not a directory", encoding="utf-8")

                compact = nightly_consolidate.build_compact_payload(
                    raw_payload,
                    language="zh",
                    cache_dir=cache_dir,
                )
                self.assertEqual(compact["window_count"], 1)
                self.assertEqual(
                    compact["windows"][0]["prompt_samples"],
                    ["cache write should be optional"],
                )

                nightly_consolidate.RAW_DIR = tmp / "raw"
                nightly_consolidate.CONSOLIDATED_DIR = tmp / "consolidated" / "daily"
                raw_daily_dir = nightly_consolidate.RAW_DIR / "daily"
                raw_daily_dir.mkdir(parents=True)
                (raw_daily_dir / "2026-04-26.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-26",
                            "window_count": 1,
                            "windows": raw_payload["windows"],
                        }
                    ),
                    encoding="utf-8",
                )
                (cache_dir / "recent-window-learning").write_text(
                    "not a directory",
                    encoding="utf-8",
                )

                learning = nightly_consolidate.build_recent_window_learning(
                    "2026-04-27",
                    1,
                    cache_dir=cache_dir,
                )

        finally:
            nightly_consolidate.RAW_DIR = old_raw_dir
            nightly_consolidate.CONSOLIDATED_DIR = old_consolidated_dir

        self.assertEqual(learning["raw_window_count"], 1)
        self.assertEqual(learning["batch_count"], 1)

    def test_disable_nightly_cache_skips_cache_fingerprints(self):
        raw_payload = {
            "date": "2026-04-27",
            "window_count": 1,
            "prompt_count": 1,
            "conclusion_count": 1,
            "windows": [
                {
                    "window_id": "w1",
                    "cwd": "/tmp/demo",
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "prompts": [{"text": "cache disabled prompt"}],
                    "conclusions": [{"text": "cache disabled conclusion"}],
                }
            ],
        }

        old_raw_dir = nightly_consolidate.RAW_DIR
        old_consolidated_dir = nightly_consolidate.CONSOLIDATED_DIR
        try:
            with TemporaryDirectory() as tmpdir, mock.patch.dict(
                os.environ,
                {"OPENRELIX_DISABLE_NIGHTLY_CACHE": "1"},
                clear=False,
            ):
                tmp = Path(tmpdir)
                cache_dir = tmp / "cache"
                with mock.patch.object(
                    nightly_consolidate,
                    "compact_payload_fingerprint",
                    side_effect=AssertionError("disabled compact cache should not hash"),
                ):
                    compact = nightly_consolidate.build_compact_payload(
                        raw_payload,
                        language="zh",
                        cache_dir=cache_dir,
                    )
                self.assertEqual(compact["windows"][0]["prompt_samples"], ["cache disabled prompt"])
                self.assertFalse(cache_dir.exists())

                nightly_consolidate.RAW_DIR = tmp / "raw"
                nightly_consolidate.CONSOLIDATED_DIR = tmp / "consolidated" / "daily"
                raw_daily_dir = nightly_consolidate.RAW_DIR / "daily"
                raw_daily_dir.mkdir(parents=True)
                (raw_daily_dir / "2026-04-26.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-26",
                            "window_count": 1,
                            "windows": raw_payload["windows"],
                        }
                    ),
                    encoding="utf-8",
                )
                with mock.patch.object(
                    nightly_consolidate,
                    "recent_window_learning_fingerprint",
                    side_effect=AssertionError("disabled recent cache should not hash"),
                ):
                    learning = nightly_consolidate.build_recent_window_learning(
                        "2026-04-27",
                        1,
                        cache_dir=cache_dir,
                    )
                self.assertEqual(learning["raw_window_count"], 1)
                self.assertFalse(cache_dir.exists())
        finally:
            nightly_consolidate.RAW_DIR = old_raw_dir
            nightly_consolidate.CONSOLIDATED_DIR = old_consolidated_dir

    def test_recent_window_learning_cache_reuses_source_fingerprint_and_invalidates_summary(self):
        old_raw_dir = nightly_consolidate.RAW_DIR
        old_consolidated_dir = nightly_consolidate.CONSOLIDATED_DIR
        try:
            nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear()
            self.addCleanup(nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear)
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                nightly_consolidate.RAW_DIR = tmp / "raw"
                nightly_consolidate.CONSOLIDATED_DIR = tmp / "consolidated" / "daily"
                cache_dir = tmp / "cache"

                raw_daily_dir = nightly_consolidate.RAW_DIR / "daily"
                raw_daily_dir.mkdir(parents=True)
                summary_dir = nightly_consolidate.CONSOLIDATED_DIR / "2026-04-26"
                summary_dir.mkdir(parents=True)
                (raw_daily_dir / "2026-04-26.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-26",
                            "window_count": 1,
                            "windows": [
                                {
                                    "window_id": "w1",
                                    "cwd": "/tmp/demo",
                                    "prompt_count": 1,
                                    "conclusion_count": 1,
                                    "prompts": [{"text": "learning question"}],
                                    "conclusions": [{"text": "learning answer"}],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                (summary_dir / "summary.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-26",
                            "window_summaries": [
                                {
                                    "window_id": "w1",
                                    "question_summary": "learning question",
                                    "main_takeaway": "learning answer",
                                    "keywords": ["alpha"],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                fingerprint = nightly_consolidate.recent_window_learning_fingerprint(
                    "2026-04-27",
                    1,
                    language=nightly_consolidate.LANGUAGE,
                )
                malformed_cache_path = cache_dir / "recent-window-learning" / "{}.json".format(
                    fingerprint
                )
                malformed_cache_path.parent.mkdir(parents=True)
                malformed_cache_path.write_text(
                    json.dumps(
                        {
                            "fingerprint": fingerprint,
                            "recent_window_learning": {},
                        }
                    ),
                    encoding="utf-8",
                )

                first = nightly_consolidate.build_recent_window_learning(
                    "2026-04-27",
                    1,
                    cache_dir=cache_dir,
                )
                self.assertEqual(first["batch_summaries"][0]["top_keywords"], ["alpha"])

                nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear()
                malformed_learning = json.loads(json.dumps(first))
                malformed_learning["coverage"]["raw_window_count"] = 0
                malformed_learning["coverage"]["source_date_count"] = 0
                malformed_cache_path.write_text(
                    json.dumps(
                        {
                            "fingerprint": fingerprint,
                            "recent_window_learning": malformed_learning,
                        }
                    ),
                    encoding="utf-8",
                )
                repaired = nightly_consolidate.build_recent_window_learning(
                    "2026-04-27",
                    1,
                    cache_dir=cache_dir,
                )
                self.assertEqual(repaired["raw_window_count"], 1)
                self.assertEqual(repaired["coverage"]["raw_window_count"], 1)
                self.assertEqual(repaired["coverage"]["source_date_count"], 1)

                nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear()
                impossible_learning = json.loads(json.dumps(first))
                impossible_learning["source_dates"] = ["2026-04-26"]
                impossible_learning["coverage"]["source_dates"] = ["2026-04-25"]
                impossible_learning["window_samples"].append(
                    {
                        "date": "2026-04-25",
                        "context": "stale",
                        "cwd": "/tmp/stale",
                        "prompt_count": 1,
                        "conclusion_count": 1,
                        "question_summary": "stale",
                        "main_takeaway": "stale",
                        "keywords": [],
                    }
                )
                impossible_learning["coverage"]["injected_window_sample_count"] = len(
                    impossible_learning["window_samples"]
                )
                malformed_cache_path.write_text(
                    json.dumps(
                        {
                            "fingerprint": fingerprint,
                            "recent_window_learning": impossible_learning,
                        }
                    ),
                    encoding="utf-8",
                )
                repaired_impossible = nightly_consolidate.build_recent_window_learning(
                    "2026-04-27",
                    1,
                    cache_dir=cache_dir,
                )
                self.assertEqual(repaired_impossible["source_dates"], ["2026-04-26"])
                self.assertEqual(
                    repaired_impossible["coverage"]["source_dates"],
                    ["2026-04-26"],
                )
                self.assertEqual(len(repaired_impossible["window_samples"]), 1)

                nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear()
                with mock.patch.object(
                    nightly_consolidate,
                    "load_raw_daily_for_date",
                    side_effect=AssertionError("cache hit should not parse raw JSON"),
                ):
                    second = nightly_consolidate.build_recent_window_learning(
                        "2026-04-27",
                        1,
                        cache_dir=cache_dir,
                    )
                self.assertEqual(second, first)

                (summary_dir / "summary.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-26",
                            "window_summaries": [
                                {
                                    "window_id": "w1",
                                    "question_summary": "learning question",
                                    "main_takeaway": "learning answer",
                                    "keywords": ["beta"],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear()
                third = nightly_consolidate.build_recent_window_learning(
                    "2026-04-27",
                    1,
                    cache_dir=cache_dir,
                )

        finally:
            nightly_consolidate.RAW_DIR = old_raw_dir
            nightly_consolidate.CONSOLIDATED_DIR = old_consolidated_dir

        self.assertEqual(third["batch_summaries"][0]["top_keywords"], ["beta"])

    def test_recent_window_learning_does_not_cache_when_sources_change_during_build(self):
        old_raw_dir = nightly_consolidate.RAW_DIR
        old_consolidated_dir = nightly_consolidate.CONSOLIDATED_DIR
        try:
            nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear()
            self.addCleanup(nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear)
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                nightly_consolidate.RAW_DIR = tmp / "raw"
                nightly_consolidate.CONSOLIDATED_DIR = tmp / "consolidated" / "daily"
                cache_dir = tmp / "cache"
                raw_daily_dir = nightly_consolidate.RAW_DIR / "daily"
                raw_daily_dir.mkdir(parents=True)

                stale_fingerprint = nightly_consolidate.recent_window_learning_fingerprint(
                    "2026-04-27",
                    1,
                    language=nightly_consolidate.LANGUAGE,
                )
                original_loader = nightly_consolidate.load_raw_daily_for_date

                def create_raw_then_load(date_str):
                    (raw_daily_dir / "{}.json".format(date_str)).write_text(
                        json.dumps(
                            {
                                "date": date_str,
                                "window_count": 1,
                                "windows": [
                                    {
                                        "window_id": "w1",
                                        "cwd": "/tmp/demo",
                                        "prompt_count": 1,
                                        "conclusion_count": 1,
                                        "prompts": [{"text": "race question"}],
                                        "conclusions": [{"text": "race answer"}],
                                    }
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                    return original_loader(date_str)

                with mock.patch.object(
                    nightly_consolidate,
                    "load_raw_daily_for_date",
                    side_effect=create_raw_then_load,
                ):
                    learning = nightly_consolidate.build_recent_window_learning(
                        "2026-04-27",
                        1,
                        cache_dir=cache_dir,
                    )

                stale_cache_path = cache_dir / "recent-window-learning" / "{}.json".format(
                    stale_fingerprint
                )

        finally:
            nightly_consolidate.RAW_DIR = old_raw_dir
            nightly_consolidate.CONSOLIDATED_DIR = old_consolidated_dir

        self.assertEqual(learning["raw_window_count"], 1)
        self.assertFalse(stale_cache_path.exists())
        self.assertNotIn(stale_fingerprint, nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE)

    def test_recent_window_learning_cache_hit_rechecks_source_fingerprint(self):
        old_raw_dir = nightly_consolidate.RAW_DIR
        old_consolidated_dir = nightly_consolidate.CONSOLIDATED_DIR
        try:
            nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear()
            self.addCleanup(nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE.clear)
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                nightly_consolidate.RAW_DIR = tmp / "raw"
                nightly_consolidate.CONSOLIDATED_DIR = tmp / "consolidated" / "daily"
                cache_dir = tmp / "cache"
                raw_daily_dir = nightly_consolidate.RAW_DIR / "daily"
                raw_daily_dir.mkdir(parents=True)
                summary_dir = nightly_consolidate.CONSOLIDATED_DIR / "2026-04-26"
                summary_dir.mkdir(parents=True)
                (raw_daily_dir / "2026-04-26.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-26",
                            "window_count": 1,
                            "windows": [
                                {
                                    "window_id": "w1",
                                    "cwd": "/tmp/demo",
                                    "prompt_count": 1,
                                    "conclusion_count": 1,
                                    "prompts": [{"text": "live question"}],
                                    "conclusions": [{"text": "live answer"}],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                (summary_dir / "summary.json").write_text(
                    json.dumps(
                        {
                            "date": "2026-04-26",
                            "window_summaries": [
                                {
                                    "window_id": "w1",
                                    "question_summary": "live question",
                                    "main_takeaway": "live answer",
                                    "keywords": ["live"],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                stale_learning = {
                    "lookback_days": 1,
                    "scanned_date_count": 1,
                    "source_dates": ["2026-04-26"],
                    "raw_window_count": 1,
                    "batch_size": nightly_consolidate.LEARNING_WINDOW_BATCH_SIZE,
                    "batch_count": 1,
                    "coverage": {
                        "scanned_date_count": 1,
                        "raw_window_count": 1,
                        "source_date_count": 1,
                        "source_dates": ["2026-04-26"],
                        "context_count": 1,
                        "batch_size": nightly_consolidate.LEARNING_WINDOW_BATCH_SIZE,
                        "batch_count": 1,
                        "injected_window_sample_count": 1,
                        "injected_pattern_count": 1,
                    },
                    "batch_summaries": [
                        {
                            "batch_id": "2026-04-26#1",
                            "date": "2026-04-26",
                            "window_count": 1,
                            "prompt_count": 1,
                            "conclusion_count": 1,
                            "contexts": [{"context": "demo", "window_count": 1}],
                            "top_keywords": ["stale"],
                            "sample_takeaways": ["stale answer"],
                        }
                    ],
                    "window_samples": [
                        {
                            "date": "2026-04-26",
                            "context": "demo",
                            "cwd": "/tmp/demo",
                            "prompt_count": 1,
                            "conclusion_count": 1,
                            "question_summary": "stale question",
                            "main_takeaway": "stale answer",
                            "keywords": ["stale"],
                        }
                    ],
                    "context_patterns": [
                        {
                            "context": "demo",
                            "window_count": 1,
                            "prompt_count": 1,
                            "conclusion_count": 1,
                            "dates": ["2026-04-26"],
                            "top_keywords": ["stale"],
                            "sample_takeaways": ["stale answer"],
                        }
                    ],
                }
                cache_path = cache_dir / "recent-window-learning" / "stale-fingerprint.json"
                cache_path.parent.mkdir(parents=True)
                cache_path.write_text(
                    json.dumps(
                        {
                            "fingerprint": "stale-fingerprint",
                            "recent_window_learning": stale_learning,
                        }
                    ),
                    encoding="utf-8",
                )

                with mock.patch.object(
                    nightly_consolidate,
                    "recent_window_learning_fingerprint",
                    side_effect=["stale-fingerprint", "current-fingerprint", "current-fingerprint"],
                ):
                    disk_result = nightly_consolidate.build_recent_window_learning(
                        "2026-04-27",
                        1,
                        cache_dir=cache_dir,
                    )

                nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE["stale-fingerprint"] = stale_learning
                with mock.patch.object(
                    nightly_consolidate,
                    "recent_window_learning_fingerprint",
                    side_effect=["stale-fingerprint", "current-fingerprint", "current-fingerprint"],
                ):
                    memory_result = nightly_consolidate.build_recent_window_learning(
                        "2026-04-27",
                        1,
                        cache_dir=tmp / "missing-cache",
                    )

        finally:
            nightly_consolidate.RAW_DIR = old_raw_dir
            nightly_consolidate.CONSOLIDATED_DIR = old_consolidated_dir

        self.assertEqual(disk_result["batch_summaries"][0]["top_keywords"], ["live"])
        self.assertEqual(memory_result["batch_summaries"][0]["top_keywords"], ["live"])
        self.assertNotIn("stale-fingerprint", nightly_consolidate._RECENT_WINDOW_LEARNING_CACHE)

    def test_learning_input_fingerprint_includes_personal_memory_algorithm_version(self):
        raw_payload = {
            "date": "2026-05-06",
            "window_count": 0,
            "prompt_count": 0,
            "conclusion_count": 0,
            "windows": [],
        }
        compact_payload = {"window_count": 0, "windows": []}
        baseline = nightly_consolidate.build_learning_input_fingerprint(
            raw_payload,
            {},
            7,
            language="zh",
            compact_payload=compact_payload,
        )

        with mock.patch.object(
            nightly_consolidate,
            "PERSONAL_MEMORY_ALGORITHM_VERSION",
            nightly_consolidate.PERSONAL_MEMORY_ALGORITHM_VERSION + 1,
        ):
            changed = nightly_consolidate.build_learning_input_fingerprint(
                raw_payload,
                {},
                7,
                language="zh",
                compact_payload=compact_payload,
            )

        self.assertNotEqual(baseline, changed)

    def test_nightly_consolidate_skip_if_unchanged_avoids_model_call(self):
        old_raw_dir = nightly_consolidate.RAW_DIR
        old_consolidated_dir = nightly_consolidate.CONSOLIDATED_DIR
        old_registry_dir = nightly_consolidate.REGISTRY_DIR
        old_runtime_dir = nightly_consolidate.RUNTIME_DIR
        old_language = nightly_consolidate.LANGUAGE
        old_memory_mode = nightly_consolidate.MEMORY_MODE
        old_personal_memory_enabled = nightly_consolidate.PERSONAL_MEMORY_ENABLED
        try:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                nightly_consolidate.RAW_DIR = tmp / "raw"
                nightly_consolidate.CONSOLIDATED_DIR = tmp / "consolidated" / "daily"
                nightly_consolidate.REGISTRY_DIR = tmp / "registry"
                nightly_consolidate.RUNTIME_DIR = tmp / "runtime"
                nightly_consolidate.LANGUAGE = "zh"
                nightly_consolidate.MEMORY_MODE = "integrated"
                nightly_consolidate.PERSONAL_MEMORY_ENABLED = True

                raw_daily_dir = nightly_consolidate.RAW_DIR / "daily"
                raw_daily_dir.mkdir(parents=True)
                nightly_consolidate.REGISTRY_DIR.mkdir(parents=True)
                summary_dir = nightly_consolidate.CONSOLIDATED_DIR / "2026-04-28"
                summary_dir.mkdir(parents=True)

                raw_payload = {
                    "date": "2026-04-28",
                    "stage": "preliminary",
                    "generated_at": "2026-04-28T10:00:00+08:00",
                    "timezone": "CST",
                    "collection_source": "history",
                    "collection_errors": [],
                    "window_count": 1,
                    "excluded_window_count": 0,
                    "review_like_window_count": 0,
                    "prompt_count": 1,
                    "conclusion_count": 1,
                    "windows": [
                        {
                            "window_id": "w1",
                            "cwd": "/tmp/openrelix",
                            "prompt_count": 1,
                            "conclusion_count": 1,
                            "prompts": [{"text": "enable learning refresh"}],
                            "conclusions": [{"text": "skip unchanged scheduled runs"}],
                        }
                    ],
                    "excluded_windows": [],
                    "review_like_windows": [],
                }
                refreshed_raw_payload = dict(raw_payload)
                refreshed_raw_payload["generated_at"] = "2026-04-28T10:30:00+08:00"
                refreshed_raw_payload["timezone"] = "Asia/Shanghai"
                refreshed_raw_payload["collection_errors"] = ["transient app-server unavailable"]
                (raw_daily_dir / "2026-04-28.json").write_text(
                    json.dumps(refreshed_raw_payload),
                    encoding="utf-8",
                )

                existing_summary = {
                    "date": "2026-04-28",
                    "language": "zh",
                    "stage": "preliminary",
                    "day_summary": "已整理自动学习刷新。",
                    "window_summaries": [],
                    "durable_memories": [],
                    "session_memories": [],
                    "low_priority_memories": [],
                    "keywords": [],
                    "next_actions": [],
                }
                learning_context = nightly_consolidate.build_learning_context(
                    "2026-04-28",
                    None,
                    learn_window_days=7,
                )
                existing_summary["learning_input_fingerprint"] = (
                    nightly_consolidate.build_learning_input_fingerprint(
                        raw_payload,
                        learning_context,
                        7,
                        language="zh",
                    )
                )
                (summary_dir / "summary.json").write_text(
                    json.dumps(existing_summary),
                    encoding="utf-8",
                )

                with mock.patch.object(nightly_consolidate, "ensure_state_layout"), mock.patch.object(
                    nightly_consolidate,
                    "run_codex_consolidation",
                    side_effect=AssertionError("model should not run"),
                ) as run_model, mock.patch.object(
                    sys,
                    "argv",
                    [
                        "nightly_consolidate.py",
                        "--date",
                        "2026-04-28",
                        "--stage",
                        "preliminary",
                        "--learn-window-days",
                        "7",
                        "--skip-if-unchanged",
                    ],
                ):
                    nightly_consolidate.main()

                run_model.assert_not_called()
        finally:
            nightly_consolidate.RAW_DIR = old_raw_dir
            nightly_consolidate.CONSOLIDATED_DIR = old_consolidated_dir
            nightly_consolidate.REGISTRY_DIR = old_registry_dir
            nightly_consolidate.RUNTIME_DIR = old_runtime_dir
            nightly_consolidate.LANGUAGE = old_language
            nightly_consolidate.MEMORY_MODE = old_memory_mode
            nightly_consolidate.PERSONAL_MEMORY_ENABLED = old_personal_memory_enabled

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

        line = openrelix.format_learning_digest(summary)

        self.assertEqual(
            line,
            "窗口学习: 近 7 天 | 扫描: 7 天 | 有窗口日期: 4 天 | 全量历史窗口: 48 | 批次: 5 | 注入样本: 12 | 模式: 6",
        )
        self.assertNotIn("w1", line)


if __name__ == "__main__":
    unittest.main()
