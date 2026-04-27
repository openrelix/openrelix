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


if __name__ == "__main__":
    unittest.main()
