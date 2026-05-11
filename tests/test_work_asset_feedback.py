#!/usr/bin/env python3

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from openrelix_overview import work_asset_feedback  # noqa: E402


class WorkAssetFeedbackTests(unittest.TestCase):
    def test_append_and_load_latest_candidate_action(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = SimpleNamespace(registry_dir=Path(tmpdir))

            first = work_asset_feedback.append_work_asset_feedback(
                paths,
                "candidate-1",
                "capture",
                title="沉淀一条资产",
                source_window_ids=["window-1"],
            )
            second = work_asset_feedback.append_work_asset_feedback(
                paths,
                "candidate-1",
                "ignore",
                title="沉淀一条资产",
                source_window_ids="window-1,window-2",
            )

            self.assertEqual(first["state"], work_asset_feedback.STATE_RESOLVED)
            self.assertEqual(second["state"], work_asset_feedback.STATE_IGNORED)
            self.assertEqual(second["source_window_ids"], ["window-1", "window-2"])

            rows = work_asset_feedback.load_work_asset_feedback_rows(paths)
            by_id = work_asset_feedback.load_work_asset_feedback_map(paths)

            self.assertEqual(len(rows), 2)
            self.assertEqual(by_id["candidate-1"]["state"], work_asset_feedback.STATE_IGNORED)

    def test_invalid_action_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = SimpleNamespace(registry_dir=Path(tmpdir))

            with self.assertRaises(ValueError):
                work_asset_feedback.append_work_asset_feedback(paths, "candidate-1", "unknown")

    def test_followup_actions_are_supported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = SimpleNamespace(registry_dir=Path(tmpdir))

            done = work_asset_feedback.append_work_asset_feedback(paths, "task-1", "done")
            snoozed = work_asset_feedback.append_work_asset_feedback(paths, "task-2", "snooze")

            self.assertEqual(done["state"], work_asset_feedback.STATE_DONE)
            self.assertEqual(snoozed["state"], work_asset_feedback.STATE_SNOOZED)


if __name__ == "__main__":
    unittest.main()
