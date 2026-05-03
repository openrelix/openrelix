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
import build_codex_native_display_cache  # noqa: E402
import check_personal_info  # noqa: E402
import openrelix  # noqa: E402
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


def make_runtime_paths_for_test(root):
    root = Path(root)
    return replace(
        openrelix.PATHS,
        state_root=root,
        codex_home=root / "codex-home",
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
                        "import sys; "
                        "sys.path.insert(0, 'scripts'); "
                        "import openrelix, build_codex_memory_summary, build_overview, "
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
                    "OPENRELIX_CODEX_MODEL": "",
                    "AI_ASSET_CODEX_MODEL": "",
                },
            ):
                paths = asset_runtime.get_runtime_paths()
                asset_runtime.ensure_state_layout(paths)
                config = asset_runtime.write_runtime_config(
                    language="en",
                    memory_mode="codex",
                    activity_source="auto",
                    codex_model="gpt5.4mini",
                    memory_summary_max_tokens=8000,
                    paths=paths,
                )

                self.assertEqual(config["language"], "en")
                self.assertEqual(config["memory_mode"], "integrated")
                self.assertEqual(config["activity_source"], "auto")
                self.assertEqual(config["codex_model"], "gpt-5.4-mini")
                self.assertEqual(config["memory_summary_max_tokens"], 8000)
                self.assertTrue(config["personal_memory_enabled"])
                self.assertTrue(config["codex_context_enabled"])
                self.assertEqual(asset_runtime.get_memory_summary_budget(paths)["max_tokens"], 8000)
                self.assertEqual(asset_runtime.get_memory_summary_budget(paths)["personal_memory_tokens"], 2400)
                self.assertEqual(asset_runtime.get_runtime_language(paths), "en")
                self.assertEqual(asset_runtime.get_memory_mode(paths), "integrated")
                self.assertEqual(asset_runtime.get_activity_source(paths), "auto")
                self.assertEqual(asset_runtime.get_codex_model(paths), "gpt-5.4-mini")
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

    def test_redaction_preserves_public_project_links_in_href(self):
        html = (
            '<a href="https://www.npmjs.com/~kk_kais" target="_blank">kk_kais</a> '
            '<a href="https://example.com/private">private</a>'
        )

        redacted = build_overview.normalize_brand_display_text(html)

        self.assertIn('href="https://www.npmjs.com/~kk_kais"', redacted)
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
                    'base_url = "https://proxy.example/api/modelhub/online/"\n',
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
                self.assertIn("DySearchTeam", nightly_config.read_text(encoding="utf-8"))
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

    def test_openrelix_help_uses_runtime_language(self):
        with mock.patch.object(openrelix, "LANGUAGE", "zh"):
            help_text = openrelix.build_parser().format_help()
        self.assertIn("OpenRelix 命令集", help_text)
        self.assertIn("运行指定日期的 review 流水线并打印摘要", help_text)
        self.assertIn("位置参数", help_text)
        self.assertIn("显示帮助并退出", help_text)
        self.assertNotIn("Run today's review pipeline", help_text)
        self.assertNotIn("optional arguments", help_text)

        with mock.patch.object(openrelix, "LANGUAGE", "en"):
            help_text = openrelix.build_parser().format_help()
        self.assertIn("OpenRelix command set", help_text)
        self.assertIn("Run the review pipeline for a target date", help_text)

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

### Local personal memory registry

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
        self.assertEqual(row["display_title"], "Codex 原生主题")
        self.assertIn("英文原文已折叠", row["display_value_note"])
        self.assertIn("Example release validation rule", row["display_title_en"])
        self.assertIn("Keep public release notes minimal", row["display_value_note_en"])

    def test_codex_native_structured_chinese_memory_keeps_meaningful_title_and_body(self):
        sample_summary = """## What's in Memory

### Local personal memory registry

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
        self.assertNotIn("历史任务组 1", html)
        self.assertIn("来自 MEMORY.md 的历史任务组索引", html)
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
        self.assertIn("主题项 1 条", comparison["note"])
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
        self.assertNotIn("历史任务组 1", row["display_title"])
        self.assertIn("来自 MEMORY.md 的历史任务组索引", row["display_body"])
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
                                "body_zh": "这个任务组沉淀发布清单、包配置和公开页面验证经验。",
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
        self.assertEqual(row["display_body"], "这个任务组沉淀发布清单、包配置和公开页面验证经验。")
        self.assertNotIn("历史任务组", row["display_title"])
        self.assertNotIn("来自 MEMORY.md 的历史任务组索引", row["display_body"])

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
        self.assertEqual(row["display_title"], "发布面 / 包检查任务组")
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
        self.assertIn("查看更多 5 条", cards_html)
        self.assertIn("Show 5 more items", cards_html)
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
        self.assertGreaterEqual(durable_rows[0]["usage_frequency_estimated_window_count"], 1)
        self.assertEqual(durable_rows[0]["usage_frequency_window_days"], 7)

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
                        "display_title": "Example task group",
                        "display_body": "Example dashboard and memory runtime.",
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
        self.assertIn("示例面板与 LaunchAgent 运行时", html)
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
        self.assertNotIn("历史任务组 1", html)
        self.assertIn("Example task group", html)
        self.assertIn("native-brief-card", html)
        self.assertIn("User Preference", html)
        self.assertIn("General Tip", html)
        self.assertIn("Codex 原生记忆-任务组", html)
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
        self.assertIn("Codex context 预算", widget)
        self.assertIn("≈ ", widget)
        self.assertIn("摘要目标 6.7K / 警戒 7.4K / 上限 8K", widget)
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
        self.assertEqual(actual_usage["estimated_context_item_count"], 2)
        self.assertIn("8 条留本地，实际 2 条进摘要", actual_usage["mode_note_zh"])

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
        self.assertIn("长期记忆 · 高优先 · 高频率", cards_html)
        self.assertIn("Long-term Memory · High Priority · High Frequency", cards_html)
        self.assertIn("短期记忆 · 中优先 · 中频率", cards_html)
        self.assertIn("低优先级记忆 · 中优先 · 中频率", cards_html)
        self.assertNotIn(" · 低优先 · ", cards_html)
        self.assertIn("查看来源与上下文", cards_html)
        self.assertIn("查看更多 1 条", cards_html)

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

        self.assertEqual([row["display_title"] for row in preview], ["长期高优记忆"])
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
        self.assertIn("事件记忆 · 中优先", cards_html)
        self.assertIn("Episodic · Medium Priority", cards_html)

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
        self.assertIn('"OpenRelix 工作台": "OpenRelix Workbench"', html)
        self.assertIn(
            '<span class="hero-brand-line"><span data-lang-only="zh">你的专属AI记忆珍藏</span><span data-lang-only="en">Your personal AI memory keepsake</span></span>',
            html,
        )
        package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertIn(
            '<span class="hero-version-line">v{}</span>'.format(package_json["version"]),
            html,
        )
        self.assertIn("applyLanguage(defaultLanguage);", html)
        self.assertIn("refreshStatusLanguage();", html)
        self.assertIn('setStatus("live", "", "live_refreshed");', html)
        self.assertIn("offline_service", html)
        self.assertIn("本地 Token 服务未启动。请运行 openrelix open panel 后再点实时刷新。", html)
        self.assertIn("The local Token service is not running. Run openrelix open panel", html)
        self.assertIn("window.localStorage", html)
        self.assertNotIn("side-nav-sublabel", html)
        self.assertIn("personal-memory-context-section", html)
        self.assertIn("进入 Codex context 的记忆", html)
        self.assertIn("personal-memory-durable-section", html)
        self.assertIn("codex-native-topic-section", html)

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

        self.assertIn('const todayTokenValue = tokenTotalDisplay(tokenUsage, "today_total_tokens", "today_total_tokens_display");', html)
        self.assertIn('const sevenDayTokenValue = tokenTotalDisplay(tokenUsage, "seven_day_total_tokens", "seven_day_total_tokens_display");', html)
        self.assertIn("function extractTokenRowCost(row)", html)
        self.assertIn("display: compactTokenWithCostValue(row.value, rowCost)", html)
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

    def test_memory_sections_stack_and_cards_use_two_column_brief_cards(self):
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
        self.assertIn('class="memory-group-list"', memory_stack)
        self.assertNotIn('class="review-grid memory-grid"', memory_stack)
        self.assertIn("{low_priority_memory_header}", main_template)
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
        self.assertIn("document.documentElement.setAttribute(\"data-theme-choice\", themeChoice);", source)
        self.assertIn("document.documentElement.setAttribute(\"data-theme-choice\", currentThemeChoice);", source)
        self.assertIn("html[data-theme=\"dark\"],", source)
        self.assertIn("background: #f5f5f7;", source)
        self.assertIn("font-family: -apple-system, BlinkMacSystemFont", source)
        self.assertNotIn("linear-gradient(135deg, #182225", source)
        self.assertNotIn("radial-gradient", source)
        self.assertNotIn("font-size: clamp", source)
        self.assertNotIn("letter-spacing: 0.08em", source)

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

    def test_window_overview_date_control_keeps_selected_empty_state_clickable(self):
        html = build_overview.make_window_overview_date_control([], "2026-04-30")

        self.assertIn('id="window-overview-date-input"', html)
        self.assertIn('value="2026-04-30" selected>2026/04/30</option>', html)
        self.assertNotIn(" disabled", html)

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

        self.assertIn("OpenRelix · 原始窗口 ID：{}".format(thread_id), html)
        self.assertLess(
            html.index("OpenRelix · 原始窗口 ID：{}".format(thread_id)),
            html.index("问题"),
        )
        self.assertNotIn('class="window-card-title-label"', html)
        self.assertNotIn("OpenRelix · 窗口 1", html)
        self.assertIn("采集：Codex app-server · 线程来源：cli", html)
        self.assertIn('class="window-card-cwd"', html)
        self.assertIn("当前目录 <a", html)
        self.assertIn("data-window-resume-copy", html)
        self.assertIn('data-resume-command="codex resume {}"'.format(thread_id), html)
        self.assertIn("data-window-resume-open", html)
        self.assertIn('href="codex://threads/{}"'.format(thread_id), html)
        self.assertIn('data-codex-url="codex://threads/{}"'.format(thread_id), html)
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
        self.assertIn('class="window-summary-pair-list"', html)
        self.assertIn('class="window-summary-pair-item"', html)
        self.assertNotIn("会话文件", html)
        self.assertNotIn("会话 JSONL", html)

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
        detail_html = html[html.index('class="window-card-detail"') :]
        summary_html = html[
            html.index('<summary class="window-card-trigger">') : html.index("</summary>")
        ]
        self.assertIn('class="window-card-pair-preview"', summary_html)
        self.assertIn("问题1", summary_html)
        self.assertIn("SQLite 是否值得切", summary_html)
        self.assertIn("结论1", summary_html)
        self.assertIn("先切底层索引", summary_html)
        self.assertNotIn("问题2：搜索 UI 是否先做", html)
        self.assertNotIn("结论2：UI 后置", html)
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

        self.assertIn("OpenRelix · Raw Window ID: w2", html)
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
            self.assertEqual(payload["personal_memory_budget_tokens"], 2400)
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
        self.assertIn("openrelix review --stage final --learn-window-days 7", showcase)
        self.assertNotIn(
            '<code class="command-code">openrelix refresh --learn-memory --learn-window-days 7</code>',
            showcase,
        )
        self.assertNotIn("<h3>开启 30 分钟自动学习（推荐）</h3>", showcase)
        self.assertIn("--enable-learning-refresh", installer)
        self.assertIn('INSTALL_PROFILE="integrated"', installer)
        self.assertIn("Default: integrated", installer)
        self.assertIn('ACTIVITY_SOURCE="${OPENRELIX_ACTIVITY_SOURCE:-${AI_ASSET_ACTIVITY_SOURCE:-auto}}"', installer)
        self.assertIn("Default: auto.", installer)
        self.assertIn("OPENRELIX_REFRESH_LEARN_MEMORY", launchd_template)
        self.assertIn("OPENRELIX_REFRESH_LEARN_WINDOW_DAYS", launchd_template)
        self.assertIn("OPENRELIX_REFRESH_SKIP_UNCHANGED", launchd_template)
        self.assertIn("OPENRELIX_REFRESH_STAGE", launchd_template)
        self.assertIn("preliminary", launchd_template)

    def test_learning_refresh_install_avoids_duplicate_immediate_model_runs(self):
        installer = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")
        launchd_template = (
            ROOT / "ops" / "launchd" / "io.github.openrelix.overview-refresh.plist.tmpl"
        ).read_text(encoding="utf-8")

        self.assertIn('OVERVIEW_RUN_AT_LOAD="<false/>"', installer)
        self.assertIn('"$(( ENABLE_LEARNING_REFRESH ? 0 : 1 ))"', installer)
        self.assertIn("首次自动学习会在下一个 30 分钟周期运行", installer)
        self.assertIn("Automatic learning refresh is enabled", installer)
        self.assertIn("__OVERVIEW_RUN_AT_LOAD__", launchd_template)

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
        self.assertIn("openOutsidePanel(_ url: URL)", mac_client)
        self.assertIn("NSWorkspace.shared.open(url)", mac_client)
        self.assertIn("url.isFileURL && isPanelURL(url)", mac_client)
        self.assertIn("decisionHandler(.cancel)", mac_client)

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
        self.assertIn("scripts/openrelix_index.py", package_json["files"])
        self.assertIn('command === "models"', npm_bin)
        self.assertIn('runPythonCli(["models", ...args.slice(1)])', npm_bin)
        self.assertIn("npx openrelix models", npm_bin)

    def test_sqlite_index_rebuild_is_warning_only_in_refresh_scripts(self):
        nightly = (ROOT / "scripts" / "nightly_pipeline.sh").read_text(encoding="utf-8")
        refresh = (ROOT / "scripts" / "refresh_overview.sh").read_text(encoding="utf-8")

        for script in (nightly, refresh):
            self.assertIn("rebuild_sqlite_index_if_available", script)
            self.assertIn("OPENRELIX_DISABLE_SQLITE_INDEX_REBUILD", script)
            self.assertIn("openrelix_index.py", script)
            self.assertIn("JSONL/raw outputs remain authoritative", script)
            self.assertIn("if ! \"$PYTHON_BIN\" \"$REPO_ROOT/scripts/openrelix_index.py\" rebuild >/dev/null; then", script)

    def test_uninstall_local_memory_delete_has_dry_run_and_repo_guard(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state_root = tmp / "state"
            codex_home = tmp / "codex"
            state_root.mkdir()
            (state_root / "registry").mkdir()
            (state_root / "registry" / "memory_items.jsonl").write_text("{}", encoding="utf-8")
            (codex_home / "memories").mkdir(parents=True)
            (codex_home / "memories" / "memory_summary.md").write_text("## What's in Memory\n", encoding="utf-8")
            paths = replace(
                openrelix.PATHS,
                state_root=state_root,
                codex_home=codex_home,
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
            self.assertEqual([item["status"] for item in actions], ["would_remove", "would_remove"])

        actions = []
        paths = replace(openrelix.PATHS, state_root=ROOT, codex_home=Path("/tmp/openrelix-codex-home"))
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
                verbose=True,
            ):
                calls.append(("backfill", dates, stage, learn_window_days, force, ensure_learning_final, verbose))
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
            )

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch.object(
                openrelix,
                "sync_review_outputs",
                side_effect=lambda: calls.append(("refresh", None)),
            ), mock.patch("sys.stdout", new_callable=io.StringIO):
                openrelix.command_review(args)

            self.assertEqual([item[0] for item in calls], ["pipeline", "refresh"])

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
            )

            with mock.patch.object(openrelix, "CONSOLIDATED_DAILY_DIR", consolidated_daily_dir), mock.patch.object(
                openrelix,
                "run_checked_with_progress",
                side_effect=fake_run_checked_with_progress,
            ), mock.patch.object(
                openrelix,
                "sync_review_outputs",
                side_effect=lambda: calls.append(("refresh", None, None)),
            ), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                openrelix.command_review(args)

            self.assertEqual([item[0] for item in calls], ["pipeline", "refresh"])
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
        self.assertIn("wireWindowResumeActions();", html)

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
        self.assertIn("近 7 天中 2 天有记录", view["overview_note"])
        self.assertIn("7 日账单", [card["label"] for card in view["summary_cards"]])
        self.assertEqual(view["summary_cards"][0]["value"], "$7")
        self.assertIn("3400 Token", view["summary_cards"][0]["caption"])
        self.assertIn("缓存读取占总输入", [card["label"] for card in view["summary_cards"]])
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
                "display_type": "技能",
                "display_scope": "仅个人使用",
                "display_context": "OpenRelix",
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

        self.assertIn("Resolve runtime language", skill_text)
        self.assertIn("asset `title` / `source_task` / `value_note` / `notes`", skill_text)
        self.assertIn("usage-event `task` / `note`", prompt_text)

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

    def test_nightly_consolidate_skip_if_unchanged_avoids_model_call(self):
        old_raw_dir = nightly_consolidate.RAW_DIR
        old_consolidated_dir = nightly_consolidate.CONSOLIDATED_DIR
        old_registry_dir = nightly_consolidate.REGISTRY_DIR
        old_language = nightly_consolidate.LANGUAGE
        old_memory_mode = nightly_consolidate.MEMORY_MODE
        old_personal_memory_enabled = nightly_consolidate.PERSONAL_MEMORY_ENABLED
        try:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                nightly_consolidate.RAW_DIR = tmp / "raw"
                nightly_consolidate.CONSOLIDATED_DIR = tmp / "consolidated" / "daily"
                nightly_consolidate.REGISTRY_DIR = tmp / "registry"
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
