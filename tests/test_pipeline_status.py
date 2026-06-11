#!/usr/bin/env python3

import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta
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

    def test_recent_runs_keep_latest_four_hundred_entries(self):
        rows = [
            {
                "run_id": "run-{}".format(index),
                "pipeline": "nightly_pipeline",
                "status": "completed",
            }
            for index in range(420)
        ]

        sanitized = pipeline_status._sanitize_recent_runs(rows)

        self.assertEqual(len(sanitized), 400)
        self.assertEqual(sanitized[0]["run_id"], "run-0")
        self.assertEqual(sanitized[-1]["run_id"], "run-399")

    def test_recent_runs_keep_two_week_history_by_timestamp(self):
        now = datetime.fromisoformat("2026-06-10T12:00:00+08:00")
        rows = [
            {
                "run_id": "fresh",
                "pipeline": "nightly_pipeline",
                "status": "completed",
                "ended_at": (now - timedelta(days=13, hours=23)).timestamp(),
            },
            {
                "run_id": "old",
                "pipeline": "nightly_pipeline",
                "status": "completed",
                "ended_at": (now - timedelta(days=15)).timestamp(),
            },
        ]

        sanitized = pipeline_status._sanitize_recent_runs(rows, now=now.timestamp())

        self.assertEqual([row["run_id"] for row in sanitized], ["fresh"])

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

    def test_normalize_token_usage_drops_zero_records(self):
        self.assertIsNone(pipeline_status.normalize_token_usage(None))
        self.assertIsNone(pipeline_status.normalize_token_usage({}))
        record = pipeline_status.normalize_token_usage({
            "input_tokens": 120,
            "output_tokens": 0,
            "cached_input_tokens": 0,
        })
        self.assertIsNotNone(record)
        self.assertEqual(record["input_tokens"], 120)
        self.assertEqual(record["output_tokens"], 0)
        self.assertEqual(record["total_tokens"], 120)
        self.assertEqual(record["source"], "estimate")

    def test_is_token_consuming_stage(self):
        self.assertFalse(pipeline_status.is_token_consuming_stage("preliminary"))
        self.assertTrue(pipeline_status.is_token_consuming_stage("final"))
        self.assertTrue(pipeline_status.is_token_consuming_stage("manual"))
        self.assertFalse(pipeline_status.is_token_consuming_stage(""))
        self.assertFalse(pipeline_status.is_token_consuming_stage(None))

    def test_summarize_token_usage_aggregates_records(self):
        rows = [
            {
                "run_id": "r1",
                "stage": "final",
                "status": "completed",
                "token_usage": {"input_tokens": 100, "output_tokens": 50, "source": "estimate"},
            },
            {
                "run_id": "r2",
                "stage": "manual",
                "status": "completed",
                "token_usage": {"input_tokens": 200, "output_tokens": 80, "source": "estimate"},
            },
            {
                "run_id": "r3",
                "stage": "preliminary",
                "status": "completed",
                "token_usage": {"input_tokens": 9999, "output_tokens": 9999, "source": "estimate"},
            },
            {
                "run_id": "r4",
                "stage": "final",
                "status": "completed",
            },
        ]

        totals = pipeline_status.summarize_token_usage(rows)

        self.assertEqual(totals["runs_with_tokens"], 3)
        self.assertEqual(totals["input_tokens"], 300 + 9999)
        self.assertEqual(totals["output_tokens"], 130 + 9999)
        self.assertEqual(totals["total_tokens"], 430 + 19998)

    def test_record_token_usage_rejects_preliminary_runs(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            payload = pipeline_status.start_run(
                "nightly_pipeline",
                target_date="2026-05-06",
                stage="preliminary",
                paths=paths,
            )

            updated = pipeline_status.record_token_usage(
                payload["run_id"],
                input_tokens=12345,
                output_tokens=6789,
                paths=paths,
            )

            self.assertNotIn("token_usage", updated)

    def test_record_token_usage_persists_for_final_runs(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(tmpdir)
            payload = pipeline_status.start_run(
                "nightly_pipeline",
                target_date="2026-05-06",
                stage="final",
                paths=paths,
            )

            updated = pipeline_status.record_token_usage(
                payload["run_id"],
                input_tokens=1024,
                output_tokens=512,
                cached_input_tokens=128,
                source="estimate",
                model="gpt-test",
                paths=paths,
            )

            self.assertIn("token_usage", updated)
            self.assertEqual(updated["token_usage"]["input_tokens"], 1024)
            self.assertEqual(updated["token_usage"]["output_tokens"], 512)
            self.assertEqual(updated["token_usage"]["cached_input_tokens"], 128)
            self.assertEqual(updated["token_usage"]["total_tokens"], 1536)
            self.assertEqual(updated["token_usage"]["model"], "gpt-test")

            payload = pipeline_status.finish_run(
                payload["run_id"],
                status="completed",
                exit_code=0,
                paths=paths,
            )
            self.assertEqual(payload["recent_runs"][0]["token_usage"]["input_tokens"], 1024)

            reloaded = pipeline_status.load_status(paths)
            self.assertIn("token_usage_totals", reloaded)
            self.assertEqual(reloaded["token_usage_totals"]["input_tokens"], 1024)


if __name__ == "__main__":
    unittest.main()
