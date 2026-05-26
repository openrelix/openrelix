#!/usr/bin/env python3

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT / "scripts")
import sys
sys.path.insert(0, sys_path)

import asset_runtime  # noqa: E402
import build_overview  # noqa: E402
from openrelix_overview import knowledge_drafts  # noqa: E402


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
        knowledge_dir=state_root / "knowledge",
        knowledge_drafts_dir=state_root / "knowledge" / "drafts",
        knowledge_published_dir=state_root / "knowledge" / "published",
    )


def sample_window_overview():
    return {
        "date": "2026-05-21",
        "window_count": 3,
        "source_kind": "daily_capture_range",
        "windows": [
            {
                "date": "2026-05-20",
                "window_id": "w-kb-a",
                "project_label": "OpenRelix",
                "cwd": "/tmp/openrelix-demo",
                "cwd_display": "OpenRelix / demo",
                "question_summary": "把多窗口对话整理成知识库文档，要求保留 source_window_ids 和 Feishu 发布状态。",
                "main_takeaway": "采用本地 raw + knowledge drafts + Feishu 确认三段式。",
                "keywords": ["knowledge", "Feishu", "window"],
                "recent_prompts": [{"time": "10:00", "text": "raw transcript should not appear"}],
                "recent_conclusions": [{"time": "10:05", "text": "raw transcript should not appear either"}],
            },
            {
                "date": "2026-05-21",
                "window_id": "w-kb-b",
                "project_label": "OpenRelix",
                "cwd": "/tmp/openrelix-demo",
                "cwd_display": "OpenRelix / demo",
                "question_summary": "知识草稿要沿用现有 panel，不新增聊天界面。",
                "main_takeaway": "panel 中增加知识队列、状态和来源回链。",
                "keywords": ["panel", "knowledge", "review"],
                "recent_prompts": [{"time": "11:00", "text": "raw transcript should not appear"}],
                "recent_conclusions": [{"time": "11:05", "text": "raw transcript should not appear either"}],
            },
            {
                "date": "2026-05-21",
                "window_id": "w-kb-c",
                "project_label": "OpenRelix",
                "cwd": "/tmp/openrelix-demo",
                "cwd_display": "OpenRelix / demo",
                "question_summary": "把 CSS overflow 问题修掉。",
                "main_takeaway": "采用 overflow-x: clip。",
                "keywords": ["ui"],
                "recent_prompts": [{"time": "12:00", "text": "raw transcript should not appear"}],
                "recent_conclusions": [{"time": "12:05", "text": "raw transcript should not appear either"}],
            },
        ],
    }


def sample_memory_rows():
    return [
        {
            "date": "2026-05-21",
            "source": "canonical",
            "scope": "global",
            "injection_policy": "global_context",
            "memory_type": "workflow",
            "priority": "high",
            "title": "知识库文档只收敛可复用内容",
            "value_note": "发布前要确认，不要把原始 transcript 写入知识正文。",
            "source_window_ids": ["w-kb-a", "w-kb-b"],
            "keywords": ["knowledge", "draft", "publish"],
        }
    ]


class KnowledgeDraftTests(unittest.TestCase):
    def test_build_pack_merges_related_windows_and_keeps_transcript_out(self):
        pack = knowledge_drafts.build_knowledge_draft_pack(
            sample_window_overview(),
            sample_memory_rows(),
            language="zh",
        )

        self.assertEqual(pack["schema_version"], 1)
        self.assertEqual(pack["draft_count"], 2)
        docs = {doc["doc_id"]: doc for doc in pack["documents"]}
        primary = next(doc for doc in docs.values() if "w-kb-a" in doc["source_window_ids"])

        self.assertEqual(primary["status"], "draft")
        self.assertEqual(primary["visibility"], "local")
        self.assertEqual(primary["feishu_doc_id"], "")
        self.assertEqual(primary["source_window_ids"], ["w-kb-a", "w-kb-b"])
        self.assertIn(primary["doc_type"], {"decision", "procedure", "pattern", "troubleshooting", "preference"})
        self.assertGreaterEqual(primary["confidence"], 0.5)

        markdown = knowledge_drafts.render_knowledge_draft_markdown(primary)
        self.assertIn("## 问题/场景", markdown)
        self.assertIn("## 结论", markdown)
        self.assertIn("## 可复用规则", markdown)
        self.assertIn("## 边界/反例", markdown)
        self.assertIn("## 证据窗口", markdown)
        self.assertIn("## 待确认项", markdown)
        self.assertIn("source_window_ids:", markdown)
        self.assertIn("feishu_doc_id: \"\"", markdown)
        self.assertNotIn("raw transcript should not appear", markdown)
        self.assertNotIn("/Users/", markdown)

    def test_write_pack_creates_knowledge_directories_and_index(self):
        with TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir) / "state"
            paths = runtime_paths_for_state(state_root)
            asset_runtime.ensure_state_layout(paths)
            pack = knowledge_drafts.build_knowledge_draft_pack(
                sample_window_overview(),
                sample_memory_rows(),
                language="zh",
            )

            knowledge_drafts.write_knowledge_draft_pack(paths, pack)

            drafts_dir = state_root / "knowledge" / "drafts"
            published_dir = state_root / "knowledge" / "published"
            index_path = drafts_dir / "index.json"
            self.assertTrue(drafts_dir.exists())
            self.assertTrue(published_dir.exists())
            self.assertTrue(index_path.exists())

            index = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(index["schema_version"], 1)
            self.assertEqual(index["draft_count"], 2)
            self.assertEqual(len(index["documents"]), 2)
            self.assertTrue(any(doc["markdown_path"].endswith(".md") for doc in index["documents"]))
            self.assertTrue(any((drafts_dir / doc["markdown_path"].split("/", 1)[-1]).exists() for doc in index["documents"]))

    def test_panel_body_renders_knowledge_queue(self):
        pack = knowledge_drafts.build_knowledge_draft_pack(
            sample_window_overview(),
            sample_memory_rows(),
            language="zh",
        )

        html = build_overview.make_knowledge_drafts_panel_body(pack)
        self.assertIn("知识草稿", html)
        self.assertIn("draft", html)
        self.assertIn("local", html)
        self.assertIn("w-kb-a", html)
        self.assertIn("w-kb-b", html)
        self.assertIn("feishu", html.lower())


if __name__ == "__main__":
    unittest.main()
