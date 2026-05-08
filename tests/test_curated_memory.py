#!/usr/bin/env python3

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from openrelix_overview import curated_memory  # noqa: E402


def jsonl(rows):
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"


class CuratedMemoryTests(unittest.TestCase):
    def test_duplicate_global_rules_collapse(self):
        pack = curated_memory.build_curated_memory_pack_from_text(
            jsonl(
                [
                    {
                        "source": "canonical",
                        "scope": "global",
                        "injection_policy": "global_context",
                        "memory_type": "workflow",
                        "priority": "medium",
                        "title": "长任务先轻量后深度",
                        "value_note": "遇到安装或回溯这类长任务时，先给轻量结果，再补深度整理。",
                        "source_window_ids": ["w1"],
                    },
                    {
                        "source": "canonical",
                        "scope": "global",
                        "injection_policy": "global_context",
                        "memory_type": "workflow",
                        "priority": "high",
                        "title": "长任务更适合先给快速可用层，再补深度整理",
                        "value_note": "面对回溯或首次落库，先产出可用轻量层，再补完整深度整理。",
                        "source_window_ids": ["w2", "w3"],
                    },
                ]
            )
        )

        rules = pack["sections"][curated_memory.SECTION_OPERATING_RULES]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["canonical_key"], "topic:long_task_light_then_deep")
        self.assertEqual(rules[0]["evidence_count"], 3)
        self.assertEqual(len(pack["diagnostics"]["duplicate_clusters"]), 1)

    def test_timeline_entries_are_demoted_to_local_volatile(self):
        pack = curated_memory.build_curated_memory_pack_from_text(
            jsonl(
                [
                    {
                        "source": "canonical",
                        "scope": "project",
                        "injection_policy": "project_context",
                        "project_label": "OpenRelix",
                        "memory_type": "task",
                        "priority": "high",
                        "title": "0.2.9 已发布并同步 npm",
                        "value_note": "v0.2.9 已发布、打 tag、同步 npm。",
                    }
                ]
            )
        )

        self.assertEqual(pack["sections"][curated_memory.SECTION_PROJECT_PLAYBOOKS], [])
        local_titles = [item["title"] for item in pack["sections"][curated_memory.SECTION_LOCAL_VOLATILE]]
        self.assertEqual(local_titles, ["0.2.9 已发布并同步 npm"])
        self.assertEqual(len(pack["diagnostics"]["timeline_like_entries"]), 1)

    def test_render_markdown_redacts_private_and_truncated_text(self):
        private_path = "/" + "Users/alice/private"
        pack = curated_memory.build_curated_memory_pack_from_text(
            jsonl(
                [
                    {
                        "source": "canonical",
                        "scope": "local",
                        "injection_policy": "local_only",
                        "memory_type": "semantic",
                        "priority": "low",
                        "title": "本机可确认的账号…",
                        "value_note": (
                            "联系 user@example.com，token=super-secret，路径 {}，"
                            "扫描 app key 是 `xoNw1gl1oLYx3ZeR`。"
                        ).format(private_path),
                    }
                ]
            )
        )
        markdown = curated_memory.render_markdown(pack)

        self.assertNotIn("…", markdown)
        self.assertNotIn("super-secret", markdown)
        self.assertNotIn("xoNw1gl1oLYx3ZeR", markdown)
        self.assertNotIn("user@example.com", markdown)
        self.assertNotIn(private_path, markdown)
        self.assertIn("[redacted-email]", markdown)
        self.assertIn("token=[redacted]", markdown)

    def test_project_playbooks_are_grouped_by_project(self):
        pack = curated_memory.build_curated_memory_pack_from_text(
            jsonl(
                [
                    {
                        "source": "canonical",
                        "scope": "project",
                        "injection_policy": "project_context",
                        "project_key": "openrelix",
                        "project_label": "OpenRelix",
                        "memory_type": "workflow",
                        "priority": "high",
                        "title": "OpenRelix 默认独立 worktree",
                        "value_note": "feature 或 bugfix 默认先开独立 worktree。",
                    },
                    {
                        "source": "canonical",
                        "scope": "project",
                        "injection_policy": "project_context",
                        "project_key": "douyin",
                        "project_label": "Douyin",
                        "memory_type": "workflow",
                        "priority": "high",
                        "title": "Douyin ASR 先查 PCM",
                        "value_note": "长按录制识别失败时先查 PCM 是否进入 ASR。",
                    },
                ]
            )
        )

        labels = {
            item["project_label"]
            for item in pack["sections"][curated_memory.SECTION_PROJECT_PLAYBOOKS]
        }
        self.assertEqual(labels, {"Douyin", "OpenRelix"})
        markdown = curated_memory.render_markdown(pack)
        self.assertIn("### Douyin", markdown)
        self.assertIn("### OpenRelix", markdown)

    def test_pack_is_deterministic_when_registry_order_changes(self):
        rows = [
            {
                "source": "canonical",
                "scope": "global",
                "injection_policy": "global_context",
                "memory_type": "workflow",
                "priority": "medium",
                "title": "大改动先独立 worktree",
                "value_note": "结构性改动先在独立 worktree 收口。",
                "source_window_ids": ["b"],
            },
            {
                "source": "canonical",
                "scope": "global",
                "injection_policy": "global_context",
                "memory_type": "workflow",
                "priority": "high",
                "title": "大改动优先独立 worktree",
                "value_note": "重构和发布流先在独立 worktree 完成并验证。",
                "source_window_ids": ["a"],
            },
        ]

        left = curated_memory.build_curated_memory_pack_from_text(jsonl(rows))
        right = curated_memory.build_curated_memory_pack_from_text(jsonl(list(reversed(rows))))

        self.assertEqual(left, right)

    def test_cli_writes_sidecar_without_touching_host_summary_and_prefers_canonical(self):
        with TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            registry_dir = state_dir / "registry"
            host_dir = state_dir / "runtime" / "host-context"
            registry_dir.mkdir(parents=True)
            host_dir.mkdir(parents=True)
            (host_dir / "memory_summary.md").write_text("existing host summary\n", encoding="utf-8")
            (registry_dir / "memory_entries.jsonl").write_text(
                jsonl(
                    [
                        {
                            "source": "canonical",
                            "scope": "global",
                            "injection_policy": "global_context",
                            "memory_type": "preference",
                            "priority": "high",
                            "title": "文件修改默认优先 apply_patch",
                            "value_note": "用户偏好用 apply_patch 做文件修改。",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (registry_dir / "memory_items.jsonl").write_text(
                jsonl(
                    [
                        {
                            "title": "legacy should not win",
                            "value_note": "legacy",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/build_curated_memory_pack.py",
                    "--state-dir",
                    str(state_dir),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (host_dir / "memory_summary.md").read_text(encoding="utf-8"),
                "existing host summary\n",
            )
            payload = json.loads((registry_dir / "curated_memory_pack.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["entry_count"], 1)
            rendered = (host_dir / "curated-personal-memory-summary.md").read_text(encoding="utf-8")
            self.assertIn("文件修改默认优先 apply_patch", rendered)
            self.assertNotIn("legacy should not win", rendered)

    def test_cli_output_is_deterministic_and_redacts_external_registry_path(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_dir = root / "state"
            external_registry = root / "outside-memory.jsonl"
            external_registry.write_text(
                jsonl(
                    [
                        {
                            "source": "canonical",
                            "scope": "global",
                            "injection_policy": "global_context",
                            "memory_type": "workflow",
                            "priority": "high",
                            "title": "多 profile 恢复命令必须显式带 CODEX_HOME",
                            "value_note": "恢复隔离 profile 时显式带 CODEX_HOME。",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            command = [
                sys.executable,
                "scripts/build_curated_memory_pack.py",
                "--state-dir",
                str(state_dir),
                "--registry",
                str(external_registry),
            ]
            first = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            first_json = (state_dir / "registry" / "curated_memory_pack.json").read_bytes()
            first_markdown = (
                state_dir / "runtime" / "host-context" / "curated-personal-memory-summary.md"
            ).read_bytes()

            second = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first_json, (state_dir / "registry" / "curated_memory_pack.json").read_bytes())
            self.assertEqual(
                first_markdown,
                (state_dir / "runtime" / "host-context" / "curated-personal-memory-summary.md").read_bytes(),
            )

            rendered = first_markdown.decode("utf-8")
            payload = json.loads(first_json.decode("utf-8"))
            self.assertEqual(payload["source"], "external-registry/outside-memory.jsonl")
            self.assertNotIn(str(root), rendered)
            self.assertNotIn(str(root), first_json.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
