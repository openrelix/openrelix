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
import openrelix_model_runner  # noqa: E402


def runtime_paths_for_state(state_root):
    base = asset_runtime.get_runtime_paths()
    state_root = Path(state_root)
    return replace(
        base,
        state_root=state_root,
        registry_dir=state_root / "registry",
        reports_dir=state_root / "reports",
        consolidated_dir=state_root / "consolidated",
        consolidated_daily_dir=state_root / "consolidated" / "daily",
        runtime_dir=state_root / "runtime",
        nightly_runner_dir=state_root / "runtime" / "nightly-runner",
        nightly_codex_home=state_root / "runtime" / "codex-nightly-home",
        nightly_claude_home=state_root / "runtime" / "claude-nightly-home",
        log_dir=state_root / "log",
        codex_bin="codex",
    )


class OpenRelixModelRunnerTests(unittest.TestCase):
    def test_codex_runner_uses_schema_and_parses_output_last_message(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            schema = Path(tmpdir) / "schema.json"
            schema.write_text('{"type":"object"}', encoding="utf-8")
            request = openrelix_model_runner.ModelRunRequest(
                task_name="knowledge-doc-rewrite",
                schema_path=schema,
                payload={"hello": "world"},
                timeout_seconds=12,
            )
            payload = {"docs": [{"title": "LLM doc"}]}
            captured = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                captured["input"] = kwargs.get("input")
                output_path = Path(cmd[cmd.index("--output-last-message") + 1])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json.dumps(payload), encoding="utf-8")
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with mock.patch.object(openrelix_model_runner.subprocess, "run", side_effect=fake_run):
                with mock.patch.object(openrelix_model_runner, "sync_codex_exec_home"):
                    result = openrelix_model_runner.run_model_request(request, paths=paths, model_cli="codex")

            self.assertEqual(result.status, "success")
            self.assertEqual(result.payload, payload)
            self.assertIn("--output-schema", captured["cmd"])
            self.assertIn(str(schema), captured["cmd"])
            self.assertIn("knowledge-doc-rewrite", captured["input"])


if __name__ == "__main__":
    raise SystemExit(unittest.main())
