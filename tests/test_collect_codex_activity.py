#!/usr/bin/env python3

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_codex_activity  # noqa: E402


class CollectCodexActivityTests(unittest.TestCase):
    def test_app_server_thread_maps_to_existing_raw_window_shape(self):
        thread = {
            "id": "thread-1",
            "createdAt": 1777305600,
            "updatedAt": 1777305900,
            "cwd": "/tmp/project",
            "source": "appServer",
            "path": "/tmp/thread.jsonl",
            "modelProvider": "openai",
            "cliVersion": "0.125.0",
            "preview": "请帮我复盘",
            "turns": [
                {
                    "id": "turn-1",
                    "startedAt": 1777305600,
                    "completedAt": 1777305900,
                    "status": "completed",
                    "items": [
                        {
                            "type": "userMessage",
                            "id": "item-user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "请帮我复盘这个任务",
                                }
                            ],
                        },
                        {
                            "type": "agentMessage",
                            "id": "item-agent",
                            "text": "已完成复盘并更新面板。",
                            "phase": "final",
                        },
                    ],
                }
            ],
        }

        window = collect_codex_activity.app_server_thread_to_window(thread, "2026-04-28", "manual")

        self.assertIsNotNone(window)
        self.assertEqual(window["window_id"], "thread-1")
        self.assertEqual(window["source"], "codex_app_server:appServer")
        self.assertEqual(window["prompt_count"], 1)
        self.assertEqual(window["conclusion_count"], 1)
        self.assertEqual(window["prompts"][0]["text"], "请帮我复盘这个任务")
        self.assertEqual(window["conclusions"][0]["text"], "已完成复盘并更新面板。")
        self.assertEqual(window["app_server"]["thread_id"], "thread-1")
        self.assertEqual(window["window_summary"], "请帮我复盘")
        self.assertEqual(window["thread_title"], "请帮我复盘")
        self.assertEqual(window["resume_id"], "thread-1")
        self.assertEqual(window["ai_host"], "codex")

    def test_app_server_unavailable_message_points_to_doctor_and_history_override(self):
        message = collect_codex_activity.app_server_unavailable_message(
            RuntimeError("connection closed"),
            8,
        )

        self.assertIn("openrelix doctor --app-server-check", message)
        self.assertIn("OPENRELIX_ACTIVITY_SOURCE=history", message)
        self.assertIn("connection closed", message)

    def test_claude_session_file_maps_to_raw_window_with_ai_host(self):
        with self.subTest("claude jsonl"):
            from tempfile import TemporaryDirectory
            import json

            with TemporaryDirectory() as tmpdir:
                session_path = Path(tmpdir) / "session.jsonl"
                rows = [
                    {"type": "summary", "summary": "Claude memory work"},
                    {
                        "type": "user",
                        "sessionId": "claude-session-1",
                        "cwd": "/tmp/project",
                        "version": "2.1.126",
                        "timestamp": "2026-05-04T09:00:00Z",
                        "uuid": "u1",
                        "message": {"content": [{"type": "text", "text": "整理 Claude Code 记忆"}]},
                    },
                    {
                        "type": "assistant",
                        "sessionId": "claude-session-1",
                        "cwd": "/tmp/project",
                        "timestamp": "2026-05-04T09:01:00Z",
                        "uuid": "a1",
                        "message": {"content": [{"type": "text", "text": "已整理完成。"}]},
                    },
                ]
                session_path.write_text(
                    "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                    encoding="utf-8",
                )

                window = collect_codex_activity.claude_session_file_to_window(
                    session_path,
                    "2026-05-04",
                    "manual",
                )

        self.assertIsNotNone(window)
        self.assertEqual(window["window_id"], "claude-claude-session-1")
        self.assertEqual(window["ai_host"], "claude")
        self.assertEqual(window["source"], "claude_code:jsonl")
        self.assertEqual(window["resume_id"], "claude-session-1")
        self.assertEqual(window["prompt_count"], 1)
        self.assertEqual(window["conclusion_count"], 1)
        self.assertEqual(window["claude_code"]["summary"], "Claude memory work")


if __name__ == "__main__":
    unittest.main()
