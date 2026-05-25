#!/usr/bin/env python3

import json
from dataclasses import replace
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "knowledge"
sys.path.insert(0, str(ROOT / "scripts"))

import asset_runtime  # noqa: E402
import openrelix_model_runner  # noqa: E402
from openrelix_overview import knowledge_docs  # noqa: E402


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


class KnowledgeDocsContractTests(unittest.TestCase):
    def test_runtime_layout_creates_knowledge_dirs_and_registries(self):
        with TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            paths = runtime_paths_for_state(state_dir)

            asset_runtime.ensure_state_layout(paths)

            self.assertTrue((state_dir / "knowledge" / "docs").is_dir())
            self.assertTrue((state_dir / "knowledge" / "runs").is_dir())
            self.assertTrue((state_dir / "registry" / "knowledge_candidates.jsonl").is_file())
            self.assertTrue((state_dir / "registry" / "knowledge_docs.jsonl").is_file())

    def test_knowledge_doc_schema_is_strict_and_has_phase_one_fields(self):
        schema = json.loads((ROOT / "templates" / "knowledge-doc-schema.json").read_text(encoding="utf-8"))

        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(sorted(schema["required"]), sorted(schema["properties"].keys()))

        required = set(schema["required"])
        self.assertIn("schema_version", required)
        self.assertIn("algorithm_version", required)
        self.assertIn("canonical_key", required)
        self.assertIn("source_fingerprint", required)
        self.assertIn("source_refs", required)
        self.assertIn("redaction_status", required)
        self.assertIn("model_status", required)
        self.assertIn("generation_mode", required)
        self.assertIn("aggregation_key", required)
        self.assertIn("aggregation_scope", required)
        self.assertIn("evidence_window_days", required)
        self.assertIn("source_window_count", required)
        self.assertIn("reviewer_state", required)
        self.assertIn("visibility", required)
        self.assertIn("body_sections", required)

        self.assertEqual(
            schema["properties"]["status"]["enum"],
            ["draft", "reviewed", "published", "superseded", "rejected"],
        )
        self.assertEqual(
            schema["properties"]["knowledge_type"]["enum"],
            ["troubleshooting", "decision", "procedure", "project_context"],
        )
        self.assertEqual(
            schema["properties"]["model_status"]["enum"],
            ["success", "retryable", "poisoned", "not_run"],
        )
        self.assertEqual(
            schema["properties"]["generation_mode"]["enum"],
            ["llm_rewrite", "deterministic_fallback", "pending_llm", "failed"],
        )

    def test_canonical_key_is_deterministic_and_project_scoped(self):
        first = knowledge_docs.canonical_key(
            {
                "project_key": "OpenRelix",
                "knowledge_type": "Troubleshooting",
                "title": " Fix botmux route bug! ",
            }
        )
        second = knowledge_docs.canonical_key(
            {
                "project_key": "openrelix",
                "knowledge_type": "troubleshooting",
                "title": "fix BOTMUX route bug",
            }
        )
        other_project = knowledge_docs.canonical_key(
            {
                "project_key": "other-project",
                "knowledge_type": "troubleshooting",
                "title": "fix botmux route bug",
            }
        )

        self.assertEqual(first, "openrelix:troubleshooting:fix-botmux-route-bug")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_project)

    def test_state_machine_and_visibility_policy_are_explicit(self):
        self.assertTrue(knowledge_docs.can_transition_candidate("candidate", "draft"))
        self.assertTrue(knowledge_docs.can_transition_candidate("candidate", "deferred"))
        self.assertFalse(knowledge_docs.can_transition_candidate("candidate", "published"))

        self.assertTrue(knowledge_docs.can_transition_doc("draft", "reviewed"))
        self.assertTrue(knowledge_docs.can_transition_doc("reviewed", "published"))
        self.assertTrue(knowledge_docs.can_transition_doc("published", "superseded"))
        self.assertFalse(knowledge_docs.can_transition_doc("draft", "published"))
        self.assertFalse(knowledge_docs.can_transition_doc("published", "draft"))

        draft_visibility = knowledge_docs.visibility_policy("draft")
        self.assertTrue(draft_visibility["panel"])
        self.assertFalse(draft_visibility["default_search"])
        self.assertFalse(draft_visibility["host_context"])
        self.assertEqual(draft_visibility["trust_level"], "draft")

        published_visibility = knowledge_docs.visibility_policy("published")
        self.assertTrue(published_visibility["panel"])
        self.assertTrue(published_visibility["default_search"])
        self.assertFalse(published_visibility["host_context"])
        self.assertEqual(published_visibility["trust_level"], "reviewed")

    def test_model_runner_sanitizes_prompt_input_and_classifies_failures(self):
        private_path = "/" + "Users/alice/private"
        payload = {
            "summary": "Contact user@example.com",
            "details": [
                "token=super-secret-value",
                "run from {}".format(private_path),
                "Bearer abcdefghijklmnop",
            ],
        }

        sanitized = openrelix_model_runner.sanitize_model_input(payload)
        text = json.dumps(sanitized, ensure_ascii=False, sort_keys=True)

        self.assertNotIn("user@example.com", text)
        self.assertNotIn("super-secret-value", text)
        self.assertNotIn("abcdefghijklmnop", text)
        self.assertNotIn(private_path, text)
        self.assertIn("[redacted-email]", text)
        self.assertIn("token=[redacted]", text)
        self.assertIn("Bearer ***", text)

        self.assertEqual(
            openrelix_model_runner.classify_model_failure(returncode=124, stderr="command timed out"),
            "retryable",
        )
        self.assertEqual(
            openrelix_model_runner.classify_model_failure(returncode=1, stderr="invalid JSON response"),
            "poisoned",
        )
        self.assertEqual(
            openrelix_model_runner.classify_model_failure(returncode=0, stderr="schema validation failed"),
            "poisoned",
        )

    def test_synthetic_knowledge_fixture_is_parseable_and_contract_complete(self):
        with TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            shutil.copytree(FIXTURE_ROOT, state_dir)

            candidates = read_jsonl(state_dir / "registry" / "knowledge_candidates.jsonl")
            docs = read_jsonl(state_dir / "registry" / "knowledge_docs.jsonl")

            self.assertEqual(len(candidates), 2)
            self.assertEqual(len(docs), 1)
            self.assertEqual(candidates[0]["decision"], "draft")
            self.assertEqual(candidates[1]["decision"], "rejected")

            doc = docs[0]
            self.assertEqual(doc["status"], "draft")
            self.assertEqual(doc["generation_mode"], "llm_rewrite")
            self.assertEqual(doc["aggregation_scope"], "project")
            self.assertEqual(doc["visibility"]["host_context"], False)
            self.assertEqual(doc["redaction_status"], "publish_safe")
            self.assertEqual(
                doc["canonical_key"],
                knowledge_docs.canonical_key(doc),
            )
            self.assertTrue((state_dir / doc["body_path"]).is_file())

    def test_data_contract_docs_define_mvp_boundaries(self):
        english = (ROOT / "docs" / "data-contracts.md").read_text(encoding="utf-8")
        chinese = (ROOT / "docs" / "data-contracts.zh-CN.md").read_text(encoding="utf-8")

        self.assertIn("## Knowledge Candidate Contract", english)
        self.assertIn("## Knowledge Doc Registry Contract", english)
        self.assertIn("candidate -> draft", english)
        self.assertIn("MVP does not write `runtime/host-context/memory_summary.md`", english)

        self.assertIn("## 知识候选契约", chinese)
        self.assertIn("## 知识文档 Registry 契约", chinese)
        self.assertIn("candidate -> draft", chinese)
        self.assertIn("MVP 不写 `runtime/host-context/memory_summary.md`", chinese)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
