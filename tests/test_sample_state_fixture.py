#!/usr/bin/env python3

import json
from dataclasses import replace
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "sample-state"
sys.path.insert(0, str(ROOT / "scripts"))

import asset_runtime  # noqa: E402
import openrelix_index  # noqa: E402
from openrelix_overview import curated_memory  # noqa: E402


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


class SampleStateFixtureTests(unittest.TestCase):
    def test_fixture_json_and_jsonl_are_parseable(self):
        for path in FIXTURE_ROOT.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix == ".json":
                with path.open(encoding="utf-8") as handle:
                    payload = json.load(handle)
                self.assertIsInstance(payload, dict)
            elif path.suffix == ".jsonl":
                with path.open(encoding="utf-8") as handle:
                    rows = [json.loads(line) for line in handle if line.strip()]
                self.assertGreaterEqual(len(rows), 1, str(path))
                self.assertTrue(all(isinstance(row, dict) for row in rows))

    def test_fixture_rebuilds_index_and_curated_pack(self):
        with TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            shutil.copytree(FIXTURE_ROOT, state_dir)
            paths = runtime_paths_for_state(state_dir)
            asset_runtime.ensure_state_layout(paths)

            db_path = state_dir / "runtime" / "fixture-index.sqlite3"
            stats = openrelix_index.rebuild_index(paths, db_path)

            self.assertEqual(stats["memory_rows"], 1)
            self.assertEqual(stats["window_rows"], 1)
            self.assertEqual(stats["daily_summary_rows"], 1)
            status = openrelix_index.index_status(paths, db_path)
            self.assertTrue(status["ok"])
            self.assertFalse(status["stale"])

            memories = openrelix_index.search_memories("synthetic", paths=paths, db_path=db_path)
            self.assertEqual([row["title"] for row in memories], ["Sample fixtures must stay synthetic"])
            windows = openrelix_index.search_windows("fixture", paths=paths, db_path=db_path)
            self.assertEqual([row["window_id"] for row in windows], ["w-demo-codex"])

            registry_text = (state_dir / "registry" / "memory_entries.jsonl").read_text(encoding="utf-8")
            pack = curated_memory.build_curated_memory_pack_from_text(registry_text)
            self.assertEqual(pack["entry_count"], 1)
            rendered = curated_memory.render_markdown(pack)
            self.assertIn("Sample fixtures must stay synthetic", rendered)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
