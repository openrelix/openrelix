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
            self.assertIn("w-route", html)

    def test_overview_source_contains_knowledge_docs_section(self):
        source = (ROOT / "scripts" / "build_overview.py").read_text(encoding="utf-8")

        self.assertIn("{knowledge_docs_header}", source)
        self.assertIn("{knowledge_docs_body}", source)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
