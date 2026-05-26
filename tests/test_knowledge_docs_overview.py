#!/usr/bin/env python3

import json
from dataclasses import replace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asset_runtime  # noqa: E402
import build_overview  # noqa: E402


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


def sample_doc():
    return {
        "schema_version": 1,
        "algorithm_version": 1,
        "doc_id": "kdoc-route-drift",
        "version": 1,
        "status": "draft",
        "knowledge_type": "troubleshooting",
        "title": "Route drift troubleshooting",
        "summary": "Keep route drift evidence project-scoped.",
        "body_path": "knowledge/docs/2026/route-drift.md",
        "body_sections": {
            "context": "Synthetic route drift",
            "decision": "Do not inject this draft into host context.",
            "procedure": ["Review source refs before publishing."],
            "evidence": ["window:w-route"],
            "limits": "Synthetic fixture only.",
            "next_actions": ["Review"],
        },
        "canonical_key": "openrelix:troubleshooting:route-drift-troubleshooting",
        "source_fingerprint": "sha256:route-drift",
        "source_refs": {
            "summary_dates": ["2026-04-28"],
            "window_ids": ["w-route"],
            "memory_ids": ["mem-route"],
            "review_paths": [],
            "project_keys": ["openrelix"],
        },
        "source_range": {"from": "2026-04-28", "to": "2026-04-28"},
        "source_contexts": [
            {
                "ai_host": "codex",
                "date": "2026-04-28",
                "window_id": "w-route",
                "title": "Route drift window",
                "project_label": "OpenRelix",
                "main_takeaway": "Keep route refs project-scoped.",
            }
        ],
        "project_key": "openrelix",
        "project_label": "OpenRelix",
        "generation_mode": "llm_rewrite",
        "aggregation_key": "openrelix:troubleshooting:route-drift-troubleshooting",
        "aggregation_scope": "project",
        "evidence_window_days": 1,
        "source_window_count": 1,
        "business_items": [
            {
                "key": "route-drift",
                "label": "Route drift",
                "summary": "Project-scoped route evidence.",
                "source_window_ids": ["w-route"],
                "source_dates": ["2026-04-28"],
            }
        ],
        "feishu_export": {
            "status": "not_configured",
            "doc_url": "",
            "doc_token": "",
            "updated_at": "",
            "error_hint": "",
        },
        "scope": "project",
        "sensitivity": "internal",
        "quality_score": 0.87,
        "reviewer_state": "needs_review",
        "redaction_status": "publish_safe",
        "model_status": "not_run",
        "visibility": {
            "panel": True,
            "default_search": False,
            "host_context": False,
            "trust_level": "draft",
        },
        "conflict_of_doc_ids": [],
        "created_at": "2026-04-28T10:20:00Z",
        "updated_at": "2026-04-28T10:20:00Z",
    }


class KnowledgeDocsOverviewTests(unittest.TestCase):
    def test_loads_and_renders_knowledge_docs_panel(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            doc = sample_doc()
            (paths.registry_dir / "knowledge_docs.jsonl").write_text(
                json.dumps(doc, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            body_path = paths.state_root / doc["body_path"]
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text("# Route drift\n", encoding="utf-8")

            with mock.patch.object(build_overview, "PATHS", paths), mock.patch.object(
                build_overview,
                "REGISTRY_DIR",
                paths.registry_dir,
            ):
                rows = build_overview.load_knowledge_docs()
                html = build_overview.make_knowledge_docs_panel_body(rows)

            self.assertEqual([row["doc_id"] for row in rows], ["kdoc-route-drift"])
            self.assertEqual(rows[0]["body_path_label"], "knowledge/docs/2026/route-drift.md")
            self.assertTrue(rows[0]["body_path_uri"].startswith("file://"))
            self.assertIn("Route drift troubleshooting", html)
            self.assertIn("needs_review", html)
            self.assertIn("查看来源与上下文", html)
            self.assertIn("Route drift window", html)
            self.assertIn("Keep route refs project-scoped.", html)
            self.assertIn("data-knowledge-doc-card-href", html)
            self.assertIn("data-knowledge-lark-doc", html)
            self.assertIn("导出飞书文档", html)
            self.assertNotIn("审核后可导出", html)
            self.assertIn("w-route", html)

    def test_exported_knowledge_doc_renders_status_tag_and_open_button(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            doc = sample_doc()
            doc["feishu_export"] = {
                "status": "exported",
                "doc_url": "https://www.feishu.cn/docx/abc",
                "doc_token": "abc",
                "updated_at": "2026-05-25T10:00:00Z",
                "error_hint": "",
            }
            (paths.registry_dir / "knowledge_docs.jsonl").write_text(
                json.dumps(doc, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            body_path = paths.state_root / doc["body_path"]
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text("# Route drift\n", encoding="utf-8")

            with mock.patch.object(build_overview, "PATHS", paths), mock.patch.object(
                build_overview,
                "REGISTRY_DIR",
                paths.registry_dir,
            ):
                rows = build_overview.load_knowledge_docs()
                html = build_overview.make_knowledge_docs_panel_body(rows)

            self.assertIn('data-knowledge-lark-exported="true"', html)
            self.assertIn("已导出飞书文档", html)
            self.assertIn("打开飞书文档", html)
            self.assertIn('href="https://www.feishu.cn/docx/abc"', html)
            self.assertIn('knowledge-doc-action-tag" data-knowledge-lark-exported="true" href="https://www.feishu.cn/docx/abc"', html)

    def test_exported_knowledge_doc_url_survives_panel_redaction_as_open_button(self):
        doc = sample_doc()
        doc["feishu_export"] = {
            "status": "exported",
            "doc_url": "https://www.feishu.cn/docx/abc",
            "doc_token": "abc",
            "updated_at": "2026-05-25T10:00:00Z",
            "error_hint": "",
        }
        html = build_overview.make_knowledge_docs_panel_body([doc])
        redacted = html.replace("https://www.feishu.cn/docx/abc", "<link>")

        restored = build_overview.restore_knowledge_doc_export_urls(redacted, [doc])

        self.assertIn(
            '<a class="knowledge-doc-action" href="https://www.feishu.cn/docx/abc" target="_blank" rel="noopener noreferrer">打开飞书文档</a>',
            restored,
        )

    def test_loads_and_renders_openviking_summary_panel(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            doc = {
                "schema_version": 1,
                "algorithm_version": 1,
                "doc_id": "ovdoc-route",
                "version": 1,
                "status": "draft",
                "summary_type": "openviking_summary",
                "title": "OpenViking route summary",
                "summary": "OpenViking produced a parallel summary.",
                "body_path": "openviking/docs/2026/route.md",
                "source_refs": {
                    "openviking_uris": ["viking://agent/memories/route"],
                    "session_ids": ["orx-openrelix-2026-05-26"],
                },
                "source_contexts": [
                    {
                        "source": "openviking",
                        "overview": "Parallel summary evidence.",
                    }
                ],
                "project_key": "openrelix",
                "project_label": "OpenRelix",
                "reviewer_state": "needs_review",
                "visibility": {
                    "panel": True,
                    "default_search": False,
                    "host_context": False,
                    "trust_level": "draft",
                },
                "feishu_export": {
                    "status": "not_configured",
                    "doc_url": "",
                    "doc_token": "",
                    "updated_at": "",
                    "error_hint": "",
                },
                "created_at": "2026-05-26T10:20:00Z",
                "updated_at": "2026-05-26T10:20:00Z",
            }
            (paths.registry_dir / "openviking_summary_docs.jsonl").write_text(
                json.dumps(doc, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            body_path = paths.state_root / doc["body_path"]
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text("# Route\n", encoding="utf-8")

            with mock.patch.object(build_overview, "PATHS", paths), mock.patch.object(
                build_overview,
                "REGISTRY_DIR",
                paths.registry_dir,
            ):
                rows = build_overview.load_openviking_summary_docs()
                html = build_overview.make_openviking_summaries_panel_body(rows)

            self.assertEqual([row["doc_id"] for row in rows], ["ovdoc-route"])
            self.assertEqual(rows[0]["body_path_label"], "openviking/docs/2026/route.md")
            self.assertTrue(rows[0]["body_path_uri"].startswith("file://"))
            self.assertIn("OpenViking route summary", html)
            self.assertIn("openviking_summary", html)
            self.assertIn("viking://agent/memories/route", html)
            self.assertIn("orx-openrelix-2026-05-26", html)
            self.assertIn("data-knowledge-doc-card-href", html)
            self.assertIn("data-knowledge-lark-doc", html)

    def test_exported_openviking_summary_url_survives_panel_redaction_as_open_button(self):
        doc = {
            "doc_id": "ovdoc-route",
            "status": "draft",
            "summary_type": "openviking_summary",
            "title": "OpenViking route summary",
            "summary": "OpenViking produced a parallel summary.",
            "body_path": "openviking/docs/2026/route.md",
            "feishu_export": {
                "status": "exported",
                "doc_url": "https://www.feishu.cn/docx/ovabc",
                "doc_token": "ovabc",
                "updated_at": "2026-05-26T10:00:00Z",
                "error_hint": "",
            },
        }
        html = build_overview.make_openviking_summaries_panel_body([doc])
        redacted = html.replace("https://www.feishu.cn/docx/ovabc", "<link>")

        restored = build_overview.restore_knowledge_doc_export_urls(redacted, [doc])

        self.assertIn(
            '<a class="knowledge-doc-action" href="https://www.feishu.cn/docx/ovabc" target="_blank" rel="noopener noreferrer">打开飞书文档</a>',
            restored,
        )

    def test_loads_and_renders_codex_memory_docs_panel(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            doc = {
                "schema_version": 1,
                "algorithm_version": 1,
                "doc_id": "codex-memory-2026-04-28",
                "version": 1,
                "status": "draft",
                "summary_type": "codex_memory_archive",
                "title": "Codex window memory 2026-04-28",
                "summary": "1 Codex window archived from 1 profile.",
                "body_path": "codex-memory/docs/2026-04-28.md",
                "source_refs": {
                    "summary_dates": ["2026-04-28"],
                    "window_ids": ["w-codex"],
                    "archive_ids": ["archive-codex"],
                },
                "source_contexts": [
                    {
                        "source": "codex_memory_archive",
                        "date": "2026-04-28",
                        "window_id": "w-codex",
                        "title": "Codex archive window",
                        "main_takeaway": "Fixed archive is visible.",
                    }
                ],
                "project_key": "codex",
                "project_label": "Codex",
                "reviewer_state": "needs_review",
                "visibility": {"panel": True, "trust_level": "draft"},
                "updated_at": "2026-04-28T10:20:00Z",
            }
            (paths.registry_dir / "codex_memory_docs.jsonl").write_text(
                json.dumps(doc, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            body_path = paths.state_root / doc["body_path"]
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text("# Codex Memory\n", encoding="utf-8")

            with mock.patch.object(build_overview, "PATHS", paths), mock.patch.object(
                build_overview,
                "REGISTRY_DIR",
                paths.registry_dir,
            ):
                rows = build_overview.load_codex_memory_docs()
                html = build_overview.make_codex_memory_docs_panel_body(rows)

            self.assertEqual([row["doc_id"] for row in rows], ["codex-memory-2026-04-28"])
            self.assertEqual(rows[0]["body_path_label"], "codex-memory/docs/2026-04-28.md")
            self.assertTrue(rows[0]["body_path_uri"].startswith("file://"))
            self.assertIn("Codex window memory 2026-04-28", html)
            self.assertIn("codex_memory_archive", html)
            self.assertIn("w-codex", html)
            self.assertIn("data-knowledge-doc-card-href", html)
            self.assertIn("data-knowledge-lark-doc", html)

    def test_overview_source_contains_knowledge_docs_section(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")

        self.assertIn("{knowledge_docs_header}", source)
        self.assertIn("{knowledge_docs_body}", source)
        self.assertIn("{codex_memory_docs_header}", source)
        self.assertIn("{codex_memory_docs_body}", source)
        self.assertIn("{openviking_summaries_header}", source)
        self.assertIn("{openviking_summaries_body}", source)
        self.assertIn('window.open(href, "_blank"', source)
        self.assertNotIn("window.location.href = href", source)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
