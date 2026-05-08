#!/usr/bin/env python3

import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asset_runtime  # noqa: E402
from openrelix_overview import pipeline_status  # noqa: E402


def runtime_paths_for_state(state_root):
    base = asset_runtime.get_runtime_paths()
    state_root = Path(state_root)
    return replace(
        base,
        state_root=state_root,
        runtime_dir=state_root / "runtime",
    )


class PipelineStatusTests(unittest.TestCase):
    def test_start_step_finish_round_trip(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            payload = pipeline_status.start_run(
                "nightly_pipeline",
                target_date="2026-05-06",
                stage="manual",
                paths=paths,
            )

            self.assertEqual(payload["status"], "running")
            self.assertEqual(payload["pipeline"], "nightly_pipeline")
            self.assertEqual(payload["target_date"], "2026-05-06")
            self.assertEqual(payload["current_step"], "collect_activity")

            payload = pipeline_status.update_step(
                payload["run_id"],
                "synthesize",
                paths=paths,
            )
            self.assertEqual(payload["current_step"], "synthesize")
            synthesize = next(step for step in payload["steps"] if step["key"] == "synthesize")
            self.assertEqual(synthesize["status"], "running")

            payload = pipeline_status.finish_run(
                payload["run_id"],
                status="completed",
                exit_code=0,
                paths=paths,
            )
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["recent_runs"][0]["status"], "completed")

            saved = json.loads((paths.runtime_dir / "pipeline-status.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["run_id"], payload["run_id"])
            self.assertEqual(saved["recent_runs"][0]["target_date"], "2026-05-06")

    def test_recent_runs_keep_latest_twenty_four_entries(self):
        rows = [
            {
                "run_id": "run-{}".format(index),
                "pipeline": "nightly_pipeline",
                "status": "completed",
            }
            for index in range(35)
        ]

        sanitized = pipeline_status._sanitize_recent_runs(rows)

        self.assertEqual(len(sanitized), 24)
        self.assertEqual(sanitized[0]["run_id"], "run-0")
        self.assertEqual(sanitized[-1]["run_id"], "run-23")

    def test_failed_status_includes_safe_failure_hint(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            payload = pipeline_status.start_run(
                "nightly_pipeline",
                target_date="2026-05-06",
                stage="manual",
                paths=paths,
            )
            payload = pipeline_status.update_step(
                payload["run_id"],
                "build_panel",
                paths=paths,
            )
            payload = pipeline_status.finish_run(
                payload["run_id"],
                status="failed",
                exit_code=None,
                error="process_exited",
                paths=paths,
            )

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["failure_code"], "process_exited")
            self.assertIn("失败阶段：重建面板", payload["failure_hint"])
            self.assertIn("local OpenRelix logs", payload["failure_hint_en"])
            self.assertNotIn(str(paths.state_root), payload["failure_hint"])

    def test_load_status_includes_next_scheduled_launch_agent(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            launch_root = Path(tmpdir) / "LaunchAgents"
            launch_root.mkdir(parents=True)
            paths = replace(paths, launch_agents_dir=launch_root)
            (launch_root / "io.github.openrelix.nightly-organize.plist").write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>io.github.openrelix.nightly-organize</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>23</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OPENRELIX_REFRESH_LEARN_MEMORY</key>
    <string>1</string>
    <key>OPENRELIX_REFRESH_LEARN_WINDOW_DAYS</key>
    <string>7</string>
  </dict>
</dict>
</plist>
""",
                encoding="utf-8",
            )

            rows = pipeline_status.scheduled_runs(
                paths=paths,
                now=datetime(2026, 5, 6, 20, 0).astimezone(),
            )
            payload = pipeline_status.load_status(paths)

            self.assertEqual(rows[0]["label"], "io.github.openrelix.nightly-organize")
            self.assertEqual(rows[0]["title"], "夜间预览整理")
            self.assertTrue(rows[0]["learn_memory"])
            self.assertEqual(rows[0]["learn_window_days"], 7)
            self.assertEqual(payload["next_run"]["label"], "io.github.openrelix.nightly-organize")

    def test_interval_schedule_uses_latest_matching_run_as_anchor(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            launch_root = Path(tmpdir) / "LaunchAgents"
            launch_root.mkdir(parents=True)
            paths = replace(paths, launch_agents_dir=launch_root)
            (launch_root / "io.github.openrelix.overview-refresh.plist").write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>io.github.openrelix.overview-refresh</string>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OPENRELIX_REFRESH_LEARN_MEMORY</key>
    <string>1</string>
    <key>OPENRELIX_REFRESH_STAGE</key>
    <string>preliminary</string>
    <key>OPENRELIX_REFRESH_LEARN_WINDOW_DAYS</key>
    <string>7</string>
  </dict>
</dict>
</plist>
""",
                encoding="utf-8",
            )
            status_payload = {
                "pipeline": "nightly_pipeline",
                "stage": "preliminary",
                "ended_at": datetime.fromisoformat("2026-05-07T20:02:05+08:00").timestamp(),
                "recent_runs": [
                    {
                        "pipeline": "refresh_overview",
                        "stage": "manual",
                        "ended_at": datetime.fromisoformat("2026-05-07T20:25:00+08:00").timestamp(),
                    }
                ],
            }

            rows = pipeline_status.scheduled_runs(
                paths=paths,
                now=datetime.fromisoformat("2026-05-07T20:28:00+08:00"),
                status_payload=status_payload,
            )

            self.assertEqual(rows[0]["next_at_iso"], "2026-05-07T21:02:05+08:00")
            self.assertEqual(rows[0]["interval_anchor_at_iso"], "2026-05-07T20:02:05+08:00")

    def test_interval_schedule_falls_back_to_now_without_matching_run(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            launch_root = Path(tmpdir) / "LaunchAgents"
            launch_root.mkdir(parents=True)
            paths = replace(paths, launch_agents_dir=launch_root)
            (launch_root / "io.github.openrelix.overview-refresh.plist").write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>io.github.openrelix.overview-refresh</string>
  <key>StartInterval</key>
  <integer>3600</integer>
</dict>
</plist>
""",
                encoding="utf-8",
            )

            rows = pipeline_status.scheduled_runs(
                paths=paths,
                now=datetime.fromisoformat("2026-05-07T20:28:00+08:00"),
                status_payload={"recent_runs": []},
            )

            self.assertEqual(rows[0]["next_at_iso"], "2026-05-07T21:28:00+08:00")


if __name__ == "__main__":
    unittest.main()
