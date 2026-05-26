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
                "status": "draft",
                "title": "Route Knowledge",
                "body_path": "knowledge/docs/2026/route.md",
                "feishu_export": {
                    "status": "not_configured",
                    "doc_url": "",
                    "doc_token": "",
                    "updated_at": "",
                    "error_hint": "",
                },
            }
            (paths.registry_dir / "knowledge_docs.jsonl").write_text(
                json.dumps(row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            captured = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                captured["cwd"] = kwargs.get("cwd")
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
                with mock.patch.object(token_live_server, "_resolve_lark_cli", return_value="feishu-cli"):
                    with mock.patch.object(token_live_server.subprocess, "run", side_effect=fake_run):
                        result = token_live_server.create_lark_doc_from_knowledge("kdoc-route")

            self.assertTrue(result["ok"])
            self.assertEqual(result["url"], "https://example.feishu.cn/docx/abc")
            self.assertIn("--markdown", captured["cmd"])
            self.assertIn("@{}".format(body_path.name), captured["cmd"])
            self.assertEqual(captured["cwd"], str(body_path.parent))
            updated = json.loads((paths.registry_dir / "knowledge_docs.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(updated["feishu_export"]["status"], "exported")
            self.assertEqual(updated["feishu_export"]["doc_url"], "https://example.feishu.cn/docx/abc")

    def test_create_lark_doc_treats_ok_false_json_as_failure(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            body_path = paths.state_root / "knowledge/docs/2026/route.md"
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text("# Route\n", encoding="utf-8")
            row = {
                "doc_id": "kdoc-route",
                "status": "draft",
                "title": "Route Knowledge",
                "body_path": "knowledge/docs/2026/route.md",
                "feishu_export": {
                    "status": "not_configured",
                    "doc_url": "",
                    "doc_token": "",
                    "updated_at": "",
                    "error_hint": "",
                },
            }
            (paths.registry_dir / "knowledge_docs.jsonl").write_text(
                json.dumps(row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            def fake_run(_cmd, **_kwargs):
                return type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps({"ok": False, "error": {"message": "keychain not initialized"}}),
                        "stderr": "",
                    },
                )()

            with mock.patch.object(token_live_server, "PATHS", paths):
                with mock.patch.object(token_live_server, "_resolve_lark_cli", return_value="lark-cli"):
                    with mock.patch.object(token_live_server.subprocess, "run", side_effect=fake_run):
                        result = token_live_server.create_lark_doc_from_knowledge("kdoc-route")

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "lark_cli_failed")
            updated = json.loads((paths.registry_dir / "knowledge_docs.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(updated["feishu_export"]["status"], "failed")
            self.assertIn("keychain not initialized", updated["feishu_export"]["error_hint"])

    def test_create_lark_doc_returns_existing_export_without_duplicate(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            body_path = paths.state_root / "knowledge/docs/2026/draft.md"
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text("# Draft\n", encoding="utf-8")
            row = {
                "doc_id": "kdoc-draft",
                "status": "draft",
                "title": "Draft Knowledge",
                "body_path": "knowledge/docs/2026/draft.md",
                "feishu_export": {
                    "status": "exported",
                    "doc_url": "https://example.feishu.cn/docx/existing",
                    "doc_token": "",
                    "updated_at": "2026-05-25T10:00:00Z",
                    "error_hint": "",
                },
            }
            (paths.registry_dir / "knowledge_docs.jsonl").write_text(
                json.dumps(row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(token_live_server, "PATHS", paths):
                with mock.patch.object(token_live_server.subprocess, "run") as run:
                    result = token_live_server.create_lark_doc_from_knowledge("kdoc-draft")

            self.assertTrue(result["ok"])
            self.assertTrue(result["already_exported"])
            self.assertEqual(result["url"], "https://example.feishu.cn/docx/existing")
            run.assert_not_called()

    def test_create_lark_doc_from_openviking_summary_uses_same_endpoint(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            body_path = paths.state_root / "openviking/docs/2026/summary.md"
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text("# OpenViking Summary\n", encoding="utf-8")
            row = {
                "doc_id": "ovdoc-summary",
                "status": "draft",
                "title": "OpenViking Summary",
                "body_path": "openviking/docs/2026/summary.md",
                "feishu_export": {
                    "status": "not_configured",
                    "doc_url": "",
                    "doc_token": "",
                    "updated_at": "",
                    "error_hint": "",
                },
            }
            (paths.registry_dir / "openviking_summary_docs.jsonl").write_text(
                json.dumps(row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            def fake_run(cmd, **_kwargs):
                return type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps({"data": {"url": "https://example.feishu.cn/docx/ov"}}),
                        "stderr": "",
                    },
                )()

            with mock.patch.object(token_live_server, "PATHS", paths):
                with mock.patch.object(token_live_server.shutil, "which", return_value="feishu-cli"):
                    with mock.patch.object(token_live_server.subprocess, "run", side_effect=fake_run):
                        result = token_live_server.create_lark_doc_from_knowledge("ovdoc-summary")

            self.assertTrue(result["ok"])
            self.assertEqual(result["url"], "https://example.feishu.cn/docx/ov")
            updated = json.loads(
                (paths.registry_dir / "openviking_summary_docs.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(updated["feishu_export"]["status"], "exported")
            self.assertEqual(updated["feishu_export"]["doc_url"], "https://example.feishu.cn/docx/ov")

    def test_create_lark_doc_from_codex_memory_doc_uses_same_endpoint(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            body_path = paths.state_root / "codex-memory/docs/2026-04-28.md"
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text("# Codex Memory\n", encoding="utf-8")
            row = {
                "doc_id": "codex-memory-2026-04-28",
                "status": "draft",
                "title": "Codex Memory",
                "body_path": "codex-memory/docs/2026-04-28.md",
                "feishu_export": {
                    "status": "not_configured",
                    "doc_url": "",
                    "doc_token": "",
                    "updated_at": "",
                    "error_hint": "",
                },
            }
            (paths.registry_dir / "codex_memory_docs.jsonl").write_text(
                json.dumps(row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            def fake_run(cmd, **_kwargs):
                return type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps({"data": {"url": "https://example.feishu.cn/docx/codex"}}),
                        "stderr": "",
                    },
                )()

            with mock.patch.object(token_live_server, "PATHS", paths):
                with mock.patch.object(token_live_server.shutil, "which", return_value="feishu-cli"):
                    with mock.patch.object(token_live_server.subprocess, "run", side_effect=fake_run):
                        result = token_live_server.create_lark_doc_from_knowledge("codex-memory-2026-04-28")

            self.assertTrue(result["ok"])
            self.assertEqual(result["url"], "https://example.feishu.cn/docx/codex")
            updated = json.loads(
                (paths.registry_dir / "codex_memory_docs.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(updated["feishu_export"]["status"], "exported")
            self.assertEqual(updated["feishu_export"]["doc_url"], "https://example.feishu.cn/docx/codex")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
