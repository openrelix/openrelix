#!/usr/bin/env python3

import json
from dataclasses import replace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asset_runtime  # noqa: E402


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


def read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class OpenVikingSummariesBuilderTests(unittest.TestCase):
    def test_build_creates_openviking_docs_without_touching_knowledge_docs(self):
        import build_openviking_summaries

        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            (paths.registry_dir / "knowledge_docs.jsonl").write_text("", encoding="utf-8")
            source_path = paths.registry_dir / "openviking_memory_exports.jsonl"
            source_path.write_text(
                json.dumps(
                    {
                        "memories": [
                            {
                                "uri": "viking://agent/memories/openrelix-route",
                                "context_type": "agent.patterns",
                                "level": "L2",
                                "abstract": "Keep OpenRelix route fixes project scoped.",
                                "overview": "OpenViking summarized the route drift fix as reusable evidence.",
                                "session_id": "orx-openrelix-2026-05-26",
                                "archive_id": "archive-1",
                                "score": 0.91,
                                "metadata": {
                                    "project_key": "openrelix",
                                    "project_label": "OpenRelix",
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            result = build_openviking_summaries.build_openviking_summaries(
                paths=paths,
                date="2026-05-26",
            )

            self.assertEqual(result["created_docs"], 1)
            docs = read_jsonl(paths.registry_dir / "openviking_summary_docs.jsonl")
            self.assertEqual(len(docs), 1)
            doc = docs[0]
            self.assertTrue(doc["doc_id"].startswith("ovdoc-"))
            self.assertEqual(doc["summary_type"], "openviking_summary")
            self.assertEqual(doc["project_key"], "openrelix")
            self.assertEqual(doc["source_refs"]["session_ids"], ["orx-openrelix-2026-05-26"])
            self.assertEqual(doc["feishu_export"]["status"], "not_configured")
            self.assertEqual(read_jsonl(paths.registry_dir / "knowledge_docs.jsonl"), [])

            body_path = paths.state_root / doc["body_path"]
            self.assertTrue(body_path.is_file())
            body_text = body_path.read_text(encoding="utf-8")
            self.assertIn("Source: OpenViking", body_text)
            self.assertIn("viking://agent/memories/openrelix-route", body_text)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
