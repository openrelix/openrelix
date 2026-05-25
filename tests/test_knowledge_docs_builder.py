#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asset_runtime  # noqa: E402
import openrelix_model_runner  # noqa: E402


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


def write_sample_inputs(paths, target_date="2026-04-28"):
    asset_runtime.ensure_state_layout(paths)
    private_path = "/" + "Users/alice/private-openrelix"
    private_email = "user" + "@example.com"
    private_secret = "super-" + "secret-value"
    summary_dir = paths.consolidated_daily_dir / target_date
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / "summary.json").write_text(
        json.dumps(
            {
                "date": target_date,
                "day_summary": "Synthetic bot route troubleshooting work.",
                "window_summaries": [
                    {
                        "window_id": "w-synthetic-route",
                        "cwd": private_path,
                        "window_title": "Fix botmux route bug",
                        "question_summary": "Diagnose synthetic route drift for {}.".format(private_email),
                        "question_count": 1,
                        "conclusion_count": 1,
                        "keywords": ["botmux", "routing"],
                        "main_takeaway": "Use project-scoped source refs and avoid host context.",
                        "summary_pairs": [
                            {
                                "question": "Why did route drift?",
                                "conclusion": "Stale recipient context was isolated.",
                            }
                        ],
                    }
                ],
                "keywords": ["botmux"],
                "next_actions": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    memory_row = {
        "memory_id": "mem-synthetic-route",
        "project_key": "openrelix",
        "project_label": "OpenRelix",
        "title": "Route drift troubleshooting",
        "value_note": "Keep bot bridge route fixes project-scoped. token={}".format(private_secret),
        "source_window_ids": ["w-synthetic-route"],
        "memory_type": "workflow",
        "scope": "project",
        "injection_policy": "on_demand",
        "priority": "medium",
    }
    memory_path = paths.registry_dir / "memory_entries.jsonl"
    memory_path.write_text(json.dumps(memory_row, ensure_ascii=False) + "\n", encoding="utf-8")
    host_context = paths.runtime_dir / "host-context" / "memory_summary.md"
    host_context.parent.mkdir(parents=True, exist_ok=True)
    host_context.write_text("do not change host context\n", encoding="utf-8")
    return memory_path, host_context


class KnowledgeDocsBuilderTests(unittest.TestCase):
    def test_build_creates_draft_doc_without_touching_memory_or_host_context(self):
        import build_knowledge_docs

        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            memory_path, host_context = write_sample_inputs(paths)
            original_memory = memory_path.read_text(encoding="utf-8")
            original_host_context = host_context.read_text(encoding="utf-8")

            result = build_knowledge_docs.build_knowledge_docs(paths=paths, date="2026-04-28")

            self.assertEqual(result["created_candidates"], 1)
            self.assertEqual(result["created_docs"], 1)
            self.assertEqual(result["failed_runs"], 0)
            self.assertEqual(memory_path.read_text(encoding="utf-8"), original_memory)
            self.assertEqual(host_context.read_text(encoding="utf-8"), original_host_context)

            candidates = read_jsonl(paths.registry_dir / "knowledge_candidates.jsonl")
            docs = read_jsonl(paths.registry_dir / "knowledge_docs.jsonl")
            self.assertEqual(len(candidates), 1)
            self.assertEqual(len(docs), 1)

            candidate = candidates[0]
            self.assertEqual(candidate["decision"], "draft")
            self.assertEqual(candidate["redaction_status"], "source_safe")
            self.assertEqual(candidate["model_status"], "not_run")
            self.assertEqual(candidate["source_refs"]["window_ids"], ["w-synthetic-route"])
            self.assertEqual(candidate["source_refs"]["memory_ids"], ["mem-synthetic-route"])

            doc = docs[0]
            self.assertEqual(doc["status"], "draft")
            self.assertEqual(doc["reviewer_state"], "needs_review")
            self.assertEqual(doc["model_status"], "not_run")
            self.assertEqual(doc["redaction_status"], "publish_safe")
            self.assertFalse(doc["visibility"]["host_context"])
            self.assertEqual(doc["body_path"], "knowledge/docs/2026/fix-botmux-route-bug.md")
            body_text = (paths.state_root / doc["body_path"]).read_text(encoding="utf-8")
            serialized = json.dumps({"candidate": candidate, "doc": doc, "body": body_text}, ensure_ascii=False)
            self.assertNotIn("user" + "@example.com", serialized)
            self.assertNotIn("super-" + "secret-value", serialized)
            self.assertNotIn("/" + "Users/alice/private-openrelix", serialized)
            self.assertIn("w-synthetic-route", body_text)

            run_artifact = Path(result["run_artifact"])
            self.assertTrue(run_artifact.is_file())
            run_payload = json.loads(run_artifact.read_text(encoding="utf-8"))
            self.assertEqual(run_payload["status"], "success")
            self.assertEqual(run_payload["model_status"], "not_run")

    def test_model_retryable_failure_writes_failed_run_artifact_only(self):
        import build_knowledge_docs

        def failing_runner(_request):
            return openrelix_model_runner.ModelRunResult(
                status=openrelix_model_runner.MODEL_STATUS_RETRYABLE,
                error_hint="model timeout in {}".format("/" + "Users/alice/private-openrelix"),
            )

        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            memory_path, _host_context = write_sample_inputs(paths)
            original_memory = memory_path.read_text(encoding="utf-8")

            result = build_knowledge_docs.build_knowledge_docs(
                paths=paths,
                date="2026-04-28",
                model_runner=failing_runner,
            )

            self.assertEqual(result["created_candidates"], 0)
            self.assertEqual(result["created_docs"], 0)
            self.assertEqual(result["failed_runs"], 1)
            self.assertEqual(read_jsonl(paths.registry_dir / "knowledge_candidates.jsonl"), [])
            self.assertEqual(read_jsonl(paths.registry_dir / "knowledge_docs.jsonl"), [])
            self.assertEqual(memory_path.read_text(encoding="utf-8"), original_memory)

            run_payload = json.loads(Path(result["run_artifact"]).read_text(encoding="utf-8"))
            self.assertEqual(run_payload["status"], "failed")
            self.assertEqual(run_payload["model_status"], "retryable")
            self.assertNotIn("/" + "Users/alice/private-openrelix", json.dumps(run_payload, ensure_ascii=False))

    def test_invalid_model_output_is_poisoned_and_does_not_update_registry(self):
        import build_knowledge_docs

        def invalid_runner(_request):
            return openrelix_model_runner.ModelRunResult(
                status=openrelix_model_runner.MODEL_STATUS_SUCCESS,
                payload={"doc_id": "missing-required-fields"},
            )

        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            write_sample_inputs(paths)

            result = build_knowledge_docs.build_knowledge_docs(
                paths=paths,
                date="2026-04-28",
                model_runner=invalid_runner,
            )

            self.assertEqual(result["created_candidates"], 0)
            self.assertEqual(result["created_docs"], 0)
            self.assertEqual(result["failed_runs"], 1)
            self.assertEqual(read_jsonl(paths.registry_dir / "knowledge_docs.jsonl"), [])
            run_payload = json.loads(Path(result["run_artifact"]).read_text(encoding="utf-8"))
            self.assertEqual(run_payload["status"], "failed")
            self.assertEqual(run_payload["model_status"], "poisoned")
            self.assertIn("missing required", run_payload["error_hint"])

    def test_cli_exposes_knowledge_build_list_and_status(self):
        with TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            paths = runtime_paths_for_state(state_dir)
            write_sample_inputs(paths)
            env = os.environ.copy()
            env["AI_ASSET_STATE_DIR"] = str(state_dir)

            build = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "openrelix.py"),
                    "knowledge",
                    "build",
                    "--date",
                    "2026-04-28",
                    "--json",
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            self.assertEqual(json.loads(build.stdout)["created_docs"], 1)

            listing = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "openrelix.py"),
                    "knowledge",
                    "list",
                    "--json",
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(listing.returncode, 0, listing.stderr)
            self.assertEqual(json.loads(listing.stdout)["docs"][0]["status"], "draft")

            status = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "openrelix.py"),
                    "knowledge",
                    "status",
                    "--json",
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)["doc_rows"], 1)

    def test_cli_reviews_publishes_and_rejects_knowledge_docs(self):
        with TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            paths = runtime_paths_for_state(state_dir)
            write_sample_inputs(paths)
            env = os.environ.copy()
            env["AI_ASSET_STATE_DIR"] = str(state_dir)
            build = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "openrelix.py"),
                    "knowledge",
                    "build",
                    "--date",
                    "2026-04-28",
                    "--json",
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            doc_id = json.loads(build.stdout)["doc_ids"][0]

            reviewed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "openrelix.py"),
                    "knowledge",
                    "review",
                    "--doc-id",
                    doc_id,
                    "--json",
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            self.assertEqual(json.loads(reviewed.stdout)["doc"]["status"], "reviewed")

            published = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "openrelix.py"),
                    "knowledge",
                    "publish",
                    "--doc-id",
                    doc_id,
                    "--json",
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(published.returncode, 0, published.stderr)
            published_doc = json.loads(published.stdout)["doc"]
            self.assertEqual(published_doc["status"], "published")
            self.assertTrue(published_doc["visibility"]["default_search"])
            self.assertFalse(published_doc["visibility"]["host_context"])
            self.assertIn(
                "Status: published",
                (state_dir / published_doc["body_path"]).read_text(encoding="utf-8"),
            )

            reject_published = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "openrelix.py"),
                    "knowledge",
                    "reject",
                    "--doc-id",
                    doc_id,
                    "--json",
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(reject_published.returncode, 0)
            self.assertIn("cannot transition", reject_published.stderr)

    def test_package_includes_knowledge_builder_for_npm_cli(self):
        package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

        self.assertIn("scripts/build_knowledge_docs.py", package_json["files"])


if __name__ == "__main__":
    raise SystemExit(unittest.main())
