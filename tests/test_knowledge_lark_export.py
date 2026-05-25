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
import token_live_server  # noqa: E402


def runtime_paths_for_state(state_root):
    base = asset_runtime.get_runtime_paths()
    state_root = Path(state_root)
    return replace(
        base,
        state_root=state_root,
        registry_dir=state_root / "registry",
        reports_dir=state_root / "reports",
        runtime_dir=state_root / "runtime",
        log_dir=state_root / "log",
    )


class KnowledgeLarkExportTests(unittest.TestCase):
    def test_create_lark_doc_from_knowledge_uses_markdown_body(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            body_path = paths.state_root / "knowledge/docs/2026/route.md"
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text("# Route\n", encoding="utf-8")
            row = {
                "doc_id": "kdoc-route",
                "status": "published",
                "title": "Route Knowledge",
                "body_path": "knowledge/docs/2026/route.md",
            }
            (paths.registry_dir / "knowledge_docs.jsonl").write_text(
                json.dumps(row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            captured = {}

            def fake_run(cmd, **_kwargs):
                captured["cmd"] = cmd
                return type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps({"data": {"url": "https://example.feishu.cn/docx/abc"}}),
                        "stderr": "",
                    },
                )()

            with mock.patch.object(token_live_server, "PATHS", paths):
                with mock.patch.object(token_live_server.shutil, "which", return_value="lark-cli"):
                    with mock.patch.object(token_live_server.subprocess, "run", side_effect=fake_run):
                        result = token_live_server.create_lark_doc_from_knowledge("kdoc-route")

            self.assertTrue(result["ok"])
            self.assertEqual(result["url"], "https://example.feishu.cn/docx/abc")
            self.assertIn("--doc-format", captured["cmd"])
            self.assertIn("markdown", captured["cmd"])
            self.assertIn("@{}".format(body_path), captured["cmd"])

    def test_create_lark_doc_rejects_unreviewed_draft(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            row = {
                "doc_id": "kdoc-draft",
                "status": "draft",
                "title": "Draft Knowledge",
                "body_path": "knowledge/docs/2026/draft.md",
            }
            (paths.registry_dir / "knowledge_docs.jsonl").write_text(
                json.dumps(row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(token_live_server, "PATHS", paths):
                result = token_live_server.create_lark_doc_from_knowledge("kdoc-draft")

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "knowledge_doc_not_reviewed")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
