#!/usr/bin/env python3

from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_codex_activity  # noqa: E402
from openrelix_overview import codex_profiles  # noqa: E402


class CollectCodexActivityTests(unittest.TestCase):
    def test_running_codex_process_text_extracts_home_and_electron_profile(self):
        ps_text = (
            "12345 /Applications/Codex.app/Contents/MacOS/Codex /repo "
            "CODEX_HOME=/tmp/.codex-openrelix-pro "
            "CODEX_ELECTRON_USER_DATA_PATH=/tmp/Application Support/Codex-OpenRelix-Pro "
            "XPC_FLAGS=1\n"
        )

        profiles = codex_profiles.parse_codex_profiles_from_process_text(ps_text)

        self.assertEqual(len(profiles), 1)
        self.assertEqual(str(profiles[0].codex_home), "/tmp/.codex-openrelix-pro")
        self.assertEqual(profiles[0].electron_user_data_path, "/tmp/Application Support/Codex-OpenRelix-Pro")
        self.assertEqual(profiles[0].source, "running")
        self.assertEqual(profiles[0].process_id, 12345)

    def test_running_codex_process_text_ignores_helper_processes(self):
        ps_text = (
            "12345 /Applications/Codex.app/Contents/Frameworks/Codex Helper (Renderer).app/Contents/MacOS/Codex Helper "
            "--type=renderer CODEX_HOME=/tmp/.codex-openrelix-pro "
            "CODEX_ELECTRON_USER_DATA_PATH=/tmp/Application Support/Codex-OpenRelix-Pro\n"
            "12346 /Applications/Codex.app/Contents/MacOS/Codex /repo "
            "CODEX_HOME=/tmp/.codex-openrelix-pro "
            "CODEX_ELECTRON_USER_DATA_PATH=/tmp/Application Support/Codex-OpenRelix-Pro\n"
        )

        profiles = codex_profiles.parse_codex_profiles_from_process_text(ps_text)

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].process_id, 12346)

    def test_running_chatgpt_app_server_exposes_bundled_codex_binary(self):
        ps_text = (
            "90625 /Applications/ChatGPT.app/Contents/Resources/codex "
            "-c features.code_mode_host=true app-server --analytics-default-enabled\n"
        )

        binaries = codex_profiles.parse_codex_app_server_binaries_from_process_text(ps_text)

        self.assertEqual(binaries, ["/Applications/ChatGPT.app/Contents/Resources/codex"])

    def test_explicit_app_server_binary_overrides_discovery(self):
        binary = codex_profiles.resolve_codex_app_server_binary(
            "/usr/local/bin/codex",
            env={codex_profiles.CODEX_APP_SERVER_BIN_ENV: "~/bin/codex-app"},
            include_running=False,
        )

        self.assertEqual(binary, str(Path.home() / "bin" / "codex-app"))

    def test_history_collection_reads_requested_codex_home(self):
        from tempfile import TemporaryDirectory
        import json

        with TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / "codex-home"
            session_id = "019dcefe-37f1-7a83-a8a6-720bd6b79d7f"
            history_path = codex_home / "history.jsonl"
            session_path = codex_home / "sessions" / "2026" / "04" / "28" / "rollout-{}.jsonl".format(session_id)
            history_path.parent.mkdir(parents=True)
            session_path.parent.mkdir(parents=True)
            history_path.write_text(
                json.dumps(
                    {
                        "session_id": session_id,
                        "ts": 1777305600,
                        "text": "读取第二个 Codex home",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            session_rows = [
                {
                    "type": "session_meta",
                    "payload": {
                        "cwd": "/tmp/project",
                        "originator": "codex",
                        "source": "cli",
                        "timestamp": "2026-04-28T00:00:00Z",
                    },
                },
                {"type": "turn_context", "payload": {"turn_id": "turn-1"}},
                {
                    "type": "event_msg",
                    "timestamp": "2026-04-28T00:00:00Z",
                    "payload": {"type": "user_message", "message": "读取第二个 Codex home"},
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": "turn-1",
                        "completed_at": 1777305900,
                        "last_agent_message": "已读取第二个 home。",
                    },
                },
            ]
            session_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in session_rows) + "\n",
                encoding="utf-8",
            )
            profile = codex_profiles.CodexProfile(
                codex_home=codex_home,
                electron_user_data_path="/tmp/Codex Profile",
                source="test",
            )

            windows = collect_codex_activity.load_history_windows_for_date(
                "2026-04-28",
                "manual",
                profile=profile,
            )

        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["window_id"], session_id)
        self.assertEqual(windows[0]["codex_home"], str(codex_home))
        self.assertEqual(windows[0]["codex_electron_user_data_path"], "/tmp/Codex Profile")
        self.assertEqual(windows[0]["prompts"][0]["text"], "读取第二个 Codex home")

    def test_session_file_collection_reads_codex_jsonl_without_history_prompt(self):
        from tempfile import TemporaryDirectory
        import json

        with TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / "codex-home"
            session_id = "019dcefe-37f1-7a83-a8a6-720bd6b79d7f"
            session_path = (
                codex_home
                / "sessions"
                / "2026"
                / "04"
                / "28"
                / "rollout-2026-04-28T09-00-00-{}.jsonl".format(session_id)
            )
            session_path.parent.mkdir(parents=True)
            rows = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-04-28T01:00:00Z",
                    "payload": {
                        "session_id": session_id,
                        "id": session_id,
                        "cwd": "/tmp/project",
                        "originator": "codex",
                        "source": "codex_cli_rs",
                        "timestamp": "2026-04-28T01:00:00Z",
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-04-28T01:00:00Z",
                    "payload": {"type": "task_started", "turn_id": "turn-1"},
                },
                {"type": "turn_context", "payload": {"turn_id": "turn-1"}},
                {
                    "type": "event_msg",
                    "timestamp": "2026-04-28T01:00:05Z",
                    "payload": {"type": "user_message", "message": "采集最近 Codex 窗口"},
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-04-28T01:01:00Z",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": "turn-1",
                        "completed_at": collect_codex_activity.epoch_from_iso("2026-04-28T01:01:00Z"),
                        "last_agent_message": "已从 session JSONL 采到窗口。",
                    },
                },
            ]
            session_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            profile = codex_profiles.CodexProfile(codex_home=codex_home, source="test")

            windows = collect_codex_activity.load_session_file_windows_for_date(
                "2026-04-28",
                "manual",
                profile=profile,
            )
            local_windows = collect_codex_activity.load_local_codex_windows_for_date(
                "2026-04-28",
                "manual",
                profile=profile,
            )

        self.assertEqual(len(windows), 1)
        window = windows[0]
        self.assertEqual(window["window_id"], session_id)
        self.assertEqual(window["source"], "codex_session_jsonl:codex_cli_rs")
        self.assertEqual(window["prompt_count"], 1)
        self.assertEqual(window["conclusion_count"], 1)
        self.assertEqual(window["prompts"][0]["text"], "采集最近 Codex 窗口")
        self.assertEqual(window["conclusions"][0]["text"], "已从 session JSONL 采到窗口。")
        self.assertEqual(window["codex_session_jsonl"]["session_id"], session_id)
        self.assertEqual([item["window_id"] for item in local_windows], [session_id])

    def test_session_index_collection_uses_local_date_for_updated_threads(self):
        from tempfile import TemporaryDirectory
        import json

        with TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / "codex-home"
            session_id = "019dcefe-37f1-7a83-a8a6-720bd6b79d7f"
            session_path = (
                codex_home
                / "sessions"
                / "2026"
                / "04"
                / "27"
                / "rollout-2026-04-27T23-55-00-{}.jsonl".format(session_id)
            )
            session_path.parent.mkdir(parents=True)
            (codex_home / "session_index.jsonl").parent.mkdir(parents=True, exist_ok=True)
            (codex_home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": session_id,
                        "thread_name": "本地日期采集",
                        "updated_at": "2026-04-27T16:30:00Z",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            rows = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-04-27T15:55:00Z",
                    "payload": {
                        "session_id": session_id,
                        "cwd": "/tmp/project",
                        "originator": "codex",
                        "source": "codex_cli_rs",
                        "timestamp": "2026-04-27T15:55:00Z",
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-04-27T16:30:00Z",
                    "payload": {"type": "task_started", "turn_id": "turn-local"},
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-04-27T16:30:03Z",
                    "payload": {"type": "user_message", "message": "按本地日期收进 4 月 28 日"},
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-04-27T16:31:00Z",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": "turn-local",
                        "completed_at": collect_codex_activity.epoch_from_iso("2026-04-27T16:31:00Z"),
                        "last_agent_message": "本地日期归属正确。",
                    },
                },
            ]
            session_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            profile = codex_profiles.CodexProfile(codex_home=codex_home, source="test")

            windows = collect_codex_activity.load_session_file_windows_for_date(
                "2026-04-28",
                "manual",
                profile=profile,
            )

        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["window_id"], session_id)
        self.assertEqual(windows[0]["prompts"][0]["text"], "按本地日期收进 4 月 28 日")
        self.assertEqual(windows[0]["conclusions"][0]["text"], "本地日期归属正确。")

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

    def test_session_jsonl_wins_duplicate_merge_and_keeps_app_server_metadata(self):
        app_server_window = {
            "ai_host": "codex",
            "window_id": "thread-1",
            "source": "codex_app_server:appServer",
            "window_summary": "App title",
            "thread_title": "App title",
            "prompt_count": 1,
            "prompts": [{"text": "app prompt"}],
            "app_server": {"thread_id": "thread-1"},
        }
        session_window = {
            "ai_host": "codex",
            "window_id": "thread-1",
            "source": "codex_session_jsonl:vscode",
            "window_summary": "",
            "thread_title": "",
            "prompt_count": 2,
            "prompts": [{"text": "session prompt"}],
            "codex_session_jsonl": {"session_id": "thread-1"},
        }

        windows = collect_codex_activity.dedupe_windows([app_server_window, session_window])

        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["source"], "codex_session_jsonl:vscode")
        self.assertEqual(windows[0]["prompt_count"], 2)
        self.assertEqual(windows[0]["thread_title"], "App title")
        self.assertEqual(windows[0]["app_server"]["thread_id"], "thread-1")

    def test_primary_codex_home_profile_metadata_preserves_configured_symlink_entry(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            real_home = root / "codex-real"
            link_home = root / ".codex"
            real_home.mkdir()
            link_home.symlink_to(real_home, target_is_directory=True)
            profile = codex_profiles.CodexProfile(
                codex_home=real_home,
                electron_user_data_path="/tmp/isolated-codex-profile",
                source="running",
                process_id=123,
            )

            with mock.patch.object(collect_codex_activity, "CODEX_HOME", link_home):
                metadata = collect_codex_activity.profile_metadata(profile)
                electron_user_data_path = collect_codex_activity.profile_electron_user_data_path(profile)

        self.assertEqual(metadata["codex_home"], str(link_home))
        self.assertEqual(metadata["codex_electron_user_data_path"], "")
        self.assertEqual(metadata["codex_profile_source"], "running")
        self.assertEqual(electron_user_data_path, "")

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

    def test_claude_subagent_transcripts_are_not_top_level_windows(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir) / "projects"
            session_file = projects_dir / "project" / "session.jsonl"
            subagent_file = projects_dir / "project" / "session" / "subagents" / "agent.jsonl"
            session_file.parent.mkdir(parents=True)
            subagent_file.parent.mkdir(parents=True)
            session_file.write_text("{}\n", encoding="utf-8")
            subagent_file.write_text("{}\n", encoding="utf-8")

            with mock.patch.object(collect_codex_activity, "CLAUDE_PROJECTS_DIR", projects_dir), mock.patch.object(
                collect_codex_activity,
                "CLAUDE_HISTORY_PATH",
                Path(tmpdir) / "missing-history.jsonl",
            ):
                files = list(collect_codex_activity.iter_claude_session_files())

        self.assertEqual(files, [session_file])

    def test_claude_session_ignores_tool_events_and_counts_turn_level_conclusions(self):
        from tempfile import TemporaryDirectory
        import json

        with TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / "session.jsonl"
            rows = [
                {
                    "type": "user",
                    "sessionId": "claude-session-tools",
                    "cwd": "/tmp/project",
                    "timestamp": "2026-05-04T09:00:00Z",
                    "uuid": "u1",
                    "message": {"content": [{"type": "text", "text": "查看 OpenRelix 项目用途"}]},
                },
                {
                    "type": "assistant",
                    "sessionId": "claude-session-tools",
                    "cwd": "/tmp/project",
                    "timestamp": "2026-05-04T09:00:05Z",
                    "uuid": "a1",
                    "message": {"content": [{"type": "text", "text": "我先查看项目入口。"}]},
                },
                {
                    "type": "assistant",
                    "sessionId": "claude-session-tools",
                    "cwd": "/tmp/project",
                    "timestamp": "2026-05-04T09:00:06Z",
                    "uuid": "a-tool",
                    "message": {"content": [{"type": "tool_use", "name": "Bash"}]},
                },
                {
                    "type": "user",
                    "sessionId": "claude-session-tools",
                    "cwd": "/tmp/project",
                    "timestamp": "2026-05-04T09:00:07Z",
                    "uuid": "tool-result",
                    "message": {"content": [{"type": "tool_result", "content": "README output"}]},
                },
                {
                    "type": "assistant",
                    "sessionId": "claude-session-tools",
                    "cwd": "/tmp/project",
                    "timestamp": "2026-05-04T09:00:08Z",
                    "uuid": "a2",
                    "message": {"content": [{"type": "text", "text": "OpenRelix 是本地优先的记忆与资产系统。"}]},
                },
                {
                    "type": "user",
                    "sessionId": "claude-session-tools",
                    "cwd": "/tmp/project",
                    "timestamp": "2026-05-04T09:02:00Z",
                    "uuid": "u2",
                    "message": {"content": [{"type": "text", "text": "再看安装入口"}]},
                },
                {
                    "type": "assistant",
                    "sessionId": "claude-session-tools",
                    "cwd": "/tmp/project",
                    "timestamp": "2026-05-04T09:02:10Z",
                    "uuid": "a3",
                    "message": {"content": [{"type": "text", "text": "安装入口在 install/install.sh。"}]},
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
        self.assertEqual(window["prompt_count"], 2)
        self.assertEqual(window["conclusion_count"], 2)
        self.assertEqual(window["raw_conclusion_count"], 2)
        self.assertEqual([item["text"] for item in window["prompts"]], ["查看 OpenRelix 项目用途", "再看安装入口"])
        self.assertEqual(
            [item["text"] for item in window["conclusions"]],
            ["OpenRelix 是本地优先的记忆与资产系统。", "安装入口在 install/install.sh。"],
        )
        self.assertNotIn("Tool", json.dumps(window, ensure_ascii=False))

    def test_claude_mem_observer_session_is_excluded_from_work_windows(self):
        from tempfile import TemporaryDirectory
        import json

        with TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / "claude-mem-observer-sessions" / "session.jsonl"
            session_path.parent.mkdir(parents=True)
            rows = [
                {
                    "type": "user",
                    "sessionId": "observer-session",
                    "cwd": "/tmp/.claude-mem/observer-sessions",
                    "timestamp": "2026-05-21T13:00:00Z",
                    "uuid": "u1",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "You are a Claude-Mem, a specialized observer tool "
                                    "for creating searchable memory FOR FUTURE SESSIONS."
                                ),
                            }
                        ]
                    },
                },
                {
                    "type": "assistant",
                    "sessionId": "observer-session",
                    "cwd": "/tmp/.claude-mem/observer-sessions",
                    "timestamp": "2026-05-21T13:01:00Z",
                    "uuid": "a1",
                    "message": {"content": [{"type": "text", "text": "Observation stored."}]},
                },
            ]
            session_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            window = collect_codex_activity.claude_session_file_to_window(
                session_path,
                "2026-05-21",
                "manual",
            )
            included, excluded = collect_codex_activity.split_excluded_windows([window])

        self.assertEqual(included, [])
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["window_id"], "claude-observer-session")
        self.assertEqual(excluded[0]["reason"], "claude_mem_observer_session")
        self.assertEqual(excluded[0]["ai_host"], "claude")

    def test_codex_window_mentioning_claude_mem_is_not_excluded(self):
        window = collect_codex_activity.build_window_payload(
            "2026-05-22",
            {
                "window_id": "codex-mentions-claude-mem",
                "cwd": "/tmp/openrelix",
                "ai_host": "codex",
                "originator": "codex_app_server",
                "source": "codex_app_server:vscode",
                "started_at": "2026-05-22T10:00:00+08:00",
                "session_file": "/tmp/codex-session.jsonl",
            },
            [
                {
                    "local_time": "2026-05-22T10:00:00+08:00",
                    "text": "哥，咋回事啊，claude-mem 一直在调用 Claude 么？",
                }
            ],
            [{"completed_at": "2026-05-22T10:01:00+08:00", "text": "这是一次诊断讨论。"}],
            1,
        )

        included, excluded = collect_codex_activity.split_excluded_windows([window])

        self.assertEqual([item["window_id"] for item in included], ["codex-mentions-claude-mem"])
        self.assertEqual(excluded, [])

    def test_codex_automation_window_is_excluded_from_work_windows(self):
        window = collect_codex_activity.build_window_payload(
            "2026-05-22",
            {
                "window_id": "codex-automation-refresh",
                "cwd": "/tmp/search-kb",
                "ai_host": "codex",
                "originator": "codex_app_server",
                "source": "codex_app_server:vscode",
                "started_at": "2026-05-22T10:00:00+08:00",
                "session_file": "/tmp/codex-session.jsonl",
            },
            [
                {
                    "local_time": "2026-05-22T10:00:00+08:00",
                    "text": "Automation: Refresh Search Android KB\nAutomation ID: refresh",
                }
            ],
            [{"completed_at": "2026-05-22T10:01:00+08:00", "text": "Refreshed."}],
            1,
        )

        included, excluded = collect_codex_activity.split_excluded_windows([window])

        self.assertEqual(included, [])
        self.assertEqual(excluded[0]["window_id"], "codex-automation-refresh")
        self.assertEqual(excluded[0]["reason"], "knowledge_automation_session")
        self.assertEqual(excluded[0]["ai_host"], "codex")

    def test_non_knowledge_codex_automation_window_is_not_excluded(self):
        window = collect_codex_activity.build_window_payload(
            "2026-05-22",
            {
                "window_id": "codex-automation-standup",
                "cwd": "/tmp/project",
                "ai_host": "codex",
                "originator": "codex_app_server",
                "source": "codex_app_server:vscode",
                "started_at": "2026-05-22T10:00:00+08:00",
                "session_file": "/tmp/codex-session.jsonl",
            },
            [
                {
                    "local_time": "2026-05-22T10:00:00+08:00",
                    "text": "Automation: Draft daily standup\nAutomation ID: standup",
                }
            ],
            [{"completed_at": "2026-05-22T10:01:00+08:00", "text": "Drafted."}],
            1,
        )

        included, excluded = collect_codex_activity.split_excluded_windows([window])

        self.assertEqual([item["window_id"] for item in included], ["codex-automation-standup"])
        self.assertEqual(excluded, [])


if __name__ == "__main__":
    unittest.main()
