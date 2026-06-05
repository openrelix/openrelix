#!/usr/bin/env python3

import json
import os
import sys
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asset_runtime  # noqa: E402
from openrelix_overview import skill_quarantine  # noqa: E402


def runtime_paths_for_fixture(root):
    base = asset_runtime.get_runtime_paths()
    state_root = root / "state"
    repo_root = root / "repo"
    return replace(
        base,
        repo_root=repo_root,
        state_root=state_root,
        codex_home=root / "home" / ".codex",
        claude_home=root / "home" / ".claude",
        repo_skill_root=repo_root / ".agents" / "skills",
        user_skill_root=root / "home" / ".codex" / "skills",
        templates_dir=repo_root / "templates",
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
        launch_agents_dir=root / "LaunchAgents",
        schema_path=repo_root / "templates" / "nightly-summary-schema.json",
    )


class SkillQuarantineTests(unittest.TestCase):
    def test_view_cache_round_trips_panel_snapshot(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))
            view = {
                "schema_version": skill_quarantine.SCHEMA_VERSION,
                "items": [{"entity_key": "skill:demo", "entity_type": "skill"}],
                "suggested": [],
                "grace": [],
                "quarantined": [],
            }

            cached = skill_quarantine.write_view_cache(paths, view)
            restored = skill_quarantine.read_view_cache(paths)

            self.assertTrue(skill_quarantine.quarantine_view_cache_path(paths).is_file())
            self.assertEqual(cached["items"][0]["entity_key"], "skill:demo")
            self.assertEqual(restored["items"][0]["entity_key"], "skill:demo")
            self.assertIn("cached_at", restored)

    def test_action_lock_uses_runtime_lock_file(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))

            with skill_quarantine.quarantine_action_lock(paths):
                self.assertTrue(skill_quarantine.quarantine_action_lock_path(paths).is_file())

    def test_block_and_unblock_skill_moves_directory_without_deleting(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))
            skill_dir = paths.codex_home / "skills" / "unused-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Unused Skill\n", encoding="utf-8")
            view = {
                "items": [
                    {
                        "entity_key": "skill:unused-skill",
                        "entity_type": "skill",
                        "identifier": "unused-skill",
                        "display_name": "unused-skill",
                        "usage_30d": 0,
                        "sources": [
                            {
                                "kind": "codex",
                                "manifest_abspath": str(skill_dir / "SKILL.md"),
                            }
                        ],
                    }
                ]
            }

            entry = skill_quarantine.block_entity(paths, "skill:unused-skill", view=view)

            self.assertEqual(entry["isolation_status"], "moved")
            quarantine_path = Path(entry["isolation_targets"][0]["quarantine_path"])
            self.assertFalse(skill_dir.exists())
            self.assertTrue((quarantine_path / "SKILL.md").is_file())
            self.assertIn("skill:unused-skill", skill_quarantine.read_state(paths)["entries"])

            result = skill_quarantine.unblock_entity(paths, "skill:unused-skill")

            self.assertTrue(result["ok"])
            self.assertTrue((skill_dir / "SKILL.md").is_file())
            self.assertFalse(skill_quarantine.read_state(paths)["entries"])

    def test_block_entity_records_move_failure_warning(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))
            skill_dir = paths.codex_home / "skills" / "flaky-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Flaky Skill\n", encoding="utf-8")
            view = {
                "items": [
                    {
                        "entity_key": "skill:flaky-skill",
                        "entity_type": "skill",
                        "identifier": "flaky-skill",
                        "display_name": "flaky-skill",
                        "usage_30d": 0,
                        "sources": [{"kind": "codex", "manifest_abspath": str(skill_dir / "SKILL.md")}],
                    }
                ]
            }

            with mock.patch.object(skill_quarantine.shutil, "move", side_effect=OSError("denied")):
                entry = skill_quarantine.block_entity(paths, "skill:flaky-skill", view=view)

            state_entry = skill_quarantine.read_state(paths)["entries"]["skill:flaky-skill"]
            self.assertEqual(entry["isolation_status"], "move_failed")
            self.assertEqual(entry["migration_warning_count"], 1)
            self.assertEqual(entry["migration_warnings"][0]["status"], "move_failed")
            self.assertIn("denied", entry["migration_warnings"][0]["error"])
            self.assertEqual(state_entry["migration_warning_count"], 1)
            self.assertTrue((skill_dir / "SKILL.md").is_file())

    def test_unblock_records_restore_conflict_warning(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))
            skill_dir = paths.codex_home / "skills" / "conflict-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Conflict Skill\n", encoding="utf-8")
            view = {
                "items": [
                    {
                        "entity_key": "skill:conflict-skill",
                        "entity_type": "skill",
                        "identifier": "conflict-skill",
                        "display_name": "conflict-skill",
                        "usage_30d": 0,
                        "sources": [{"kind": "codex", "manifest_abspath": str(skill_dir / "SKILL.md")}],
                    }
                ]
            }
            skill_quarantine.block_entity(paths, "skill:conflict-skill", view=view)
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# New Copy\n", encoding="utf-8")

            result = skill_quarantine.unblock_entity(paths, "skill:conflict-skill", view=view)
            state_entry = skill_quarantine.read_state(paths)["entries"]["skill:conflict-skill"]

            self.assertFalse(result["ok"])
            self.assertEqual(result["migration_warnings"][0]["status"], "restore_conflict")
            self.assertEqual(state_entry["isolation_status"], "restore_failed")
            self.assertEqual(state_entry["migration_warning_count"], 1)

    def test_block_and_unblock_json_mcp_isolates_config_without_losing_payload(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))
            config_path = paths.claude_home / "settings.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "unused-mcp": {
                                "command": "node",
                                "args": ["server.js"],
                            },
                            "active-mcp": {"command": "node"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            view = {
                "items": [
                    {
                        "entity_key": "mcp:unused-mcp",
                        "entity_type": "mcp",
                        "identifier": "unused-mcp",
                        "display_name": "unused-mcp",
                        "usage_30d": 0,
                        "config_sources": [
                            {
                                "server": "unused-mcp",
                                "host": "claude",
                                "format": "json",
                                "path": str(config_path),
                                "section": "mcpServers",
                            }
                        ],
                    }
                ]
            }

            entry = skill_quarantine.block_entity(paths, "mcp:unused-mcp", view=view)
            blocked_payload = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertEqual(entry["isolation_status"], "config_isolated")
            self.assertNotIn("unused-mcp", blocked_payload["mcpServers"])
            self.assertIn("active-mcp", blocked_payload["mcpServers"])
            self.assertEqual(entry["isolation_targets"][0]["saved_config"]["command"], "node")
            backup_path = Path(entry["isolation_targets"][0]["backup_path"])
            self.assertTrue(backup_path.is_file())
            self.assertIn("unused-mcp", json.loads(backup_path.read_text(encoding="utf-8"))["mcpServers"])

            result = skill_quarantine.unblock_entity(paths, "mcp:unused-mcp")
            restored_payload = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertTrue(result["ok"])
            self.assertIn("unused-mcp", restored_payload["mcpServers"])

    def test_block_and_unblock_toml_mcp_disables_and_restores_section(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))
            config_path = paths.codex_home / "config.toml"
            config_path.parent.mkdir(parents=True)
            original_text = "\n".join(
                [
                    "[mcp_servers.unused-mcp]",
                    'command = "node"',
                    "",
                    "[mcp_servers.active-mcp]",
                    'command = "node"',
                ]
            )
            config_path.write_text(original_text, encoding="utf-8")
            view = {
                "items": [
                    {
                        "entity_key": "mcp:unused-mcp",
                        "entity_type": "mcp",
                        "identifier": "unused-mcp",
                        "display_name": "unused-mcp",
                        "usage_30d": 0,
                        "config_sources": [
                            {
                                "server": "unused-mcp",
                                "host": "codex",
                                "format": "toml",
                                "path": str(config_path),
                                "section": "mcp_servers",
                            }
                        ],
                    }
                ]
            }

            entry = skill_quarantine.block_entity(paths, "mcp:unused-mcp", view=view)
            disabled_text = config_path.read_text(encoding="utf-8")

            self.assertEqual(entry["isolation_status"], "toml_disabled")
            self.assertIn("enabled = false", disabled_text)
            self.assertIn("[mcp_servers.active-mcp]", disabled_text)
            self.assertTrue(Path(entry["isolation_targets"][0]["backup_path"]).is_file())
            self.assertIn('command = "node"', entry["isolation_targets"][0]["saved_config_text"])

            result = skill_quarantine.unblock_entity(paths, "mcp:unused-mcp")

            self.assertTrue(result["ok"])
            self.assertEqual(config_path.read_text(encoding="utf-8"), original_text)

    def test_filters_hide_quarantined_skills_and_mcp_usage(self):
        state = {
            "entries": {
                "skill:unused-skill": {"entity_key": "skill:unused-skill"},
                "mcp:unused-mcp": {"entity_key": "mcp:unused-mcp"},
            }
        }

        asset_rows = skill_quarantine.filter_asset_rows(
            [
                {"type": "skill", "identifier": "unused-skill"},
                {"type": "skill", "identifier": "active-skill"},
            ],
            state,
        )
        mcp_view = skill_quarantine.filter_mcp_usage_view(
            {
                "tools": [
                    {"server": "unused-mcp", "calls": 10},
                    {"server": "active-mcp", "calls": 3},
                ],
                "servers": [
                    {"server": "unused-mcp", "calls": 10},
                    {"server": "active-mcp", "calls": 3},
                ],
            },
            state,
        )

        self.assertEqual([row["identifier"] for row in asset_rows], ["active-skill"])
        self.assertEqual([row["server"] for row in mcp_view["tools"]], ["active-mcp"])
        self.assertEqual(mcp_view["total_calls"], 3)

    def test_disabled_toml_mcp_is_not_a_quarantine_candidate(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))
            paths.codex_home.mkdir(parents=True)
            (paths.codex_home / "config.toml").write_text(
                "\n".join(
                    [
                        "[mcp_servers.disabled-mcp]",
                        'url = "https://example.invalid/mcp"',
                        "enabled = false",
                        "",
                        "[mcp_servers.enabled-mcp]",
                        'url = "https://example.invalid/enabled"',
                    ]
                ),
                encoding="utf-8",
            )

            view = skill_quarantine.build_quarantine_view(
                paths,
                today="2026-06-05",
                activation_snapshot={"assets": [], "frequency_by_key": {}},
                mcp_usage_view={"servers": []},
            )

            keys = {row["entity_key"] for row in view["items"]}
            self.assertNotIn("mcp:disabled-mcp", keys)
            self.assertIn("mcp:enabled-mcp", keys)

    def test_mcp_first_seen_age_persists_across_config_rewrites(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))
            paths.codex_home.mkdir(parents=True)
            (paths.codex_home / "config.toml").write_text(
                "\n".join(
                    [
                        "[mcp_servers.enabled-mcp]",
                        'url = "https://example.invalid/enabled"',
                    ]
                ),
                encoding="utf-8",
            )

            skill_quarantine.build_quarantine_view(
                paths,
                today="2026-06-01",
                activation_snapshot={"assets": [], "frequency_by_key": {}},
                mcp_usage_view={"servers": []},
            )
            (paths.codex_home / "config.toml").write_text(
                "\n".join(
                    [
                        "[mcp_servers.enabled-mcp]",
                        'url = "https://example.invalid/enabled"',
                        "",
                        "[features]",
                        "hooks = true",
                    ]
                ),
                encoding="utf-8",
            )

            view = skill_quarantine.build_quarantine_view(
                paths,
                today="2026-06-05",
                activation_snapshot={"assets": [], "frequency_by_key": {}},
                mcp_usage_view={"servers": []},
            )

            item = next(row for row in view["items"] if row["entity_key"] == "mcp:enabled-mcp")
            state = skill_quarantine.read_state(paths)
            self.assertEqual(item["added_at"], "2026-06-01")
            self.assertEqual(item["age_days"], 4)
            self.assertEqual(state["observed"]["mcp:enabled-mcp"]["first_seen_at"], "2026-06-01")

    def test_skill_first_seen_age_persists_across_manifest_rewrites(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))
            skill_dir = paths.codex_home / "skills" / "lark-apps"
            skill_dir.mkdir(parents=True)
            manifest = skill_dir / "SKILL.md"
            manifest.write_text("---\nname: lark-apps\n---\n", encoding="utf-8")
            first_seen_ts = datetime(2026, 6, 1, 12, 0, 0).timestamp()
            os.utime(manifest, (first_seen_ts, first_seen_ts))
            os.utime(skill_dir, (first_seen_ts, first_seen_ts))
            activation_snapshot = {
                "assets": [
                    {
                        "kind": "codex_skill",
                        "identifier": "lark-apps",
                        "name": "lark-apps",
                        "description": "",
                        "source_root": "$CODEX_HOME/skills",
                        "manifest_path": "$CODEX_HOME/skills/lark-apps/SKILL.md",
                        "manifest_abspath": str(manifest),
                    }
                ],
                "frequency_by_key": {},
            }

            skill_quarantine.build_quarantine_view(
                paths,
                today="2026-06-02",
                activation_snapshot=activation_snapshot,
                mcp_usage_view={"servers": []},
            )
            rewrite_ts = datetime(2026, 6, 5, 12, 0, 0).timestamp()
            manifest.write_text("---\nname: lark-apps\nupdated: true\n---\n", encoding="utf-8")
            os.utime(manifest, (rewrite_ts, rewrite_ts))
            os.utime(skill_dir, (rewrite_ts, rewrite_ts))

            view = skill_quarantine.build_quarantine_view(
                paths,
                today="2026-06-08",
                activation_snapshot=activation_snapshot,
                mcp_usage_view={"servers": []},
            )

            item = next(row for row in view["items"] if row["entity_key"] == "skill:lark-apps")
            state = skill_quarantine.read_state(paths)
            self.assertEqual(item["added_at"], "2026-06-01")
            self.assertEqual(item["age_days"], 7)
            self.assertEqual(item["status"], "suggested")
            self.assertEqual(state["observed"]["skill:lark-apps"]["first_seen_at"], "2026-06-01")

    def test_block_all_grace_quarantines_buffered_items(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))
            grace_item = {
                "entity_key": "skill:lark-mail",
                "entity_type": "skill",
                "identifier": "lark-mail",
                "display_name": "lark-mail",
                "usage_30d": 0,
                "reason": skill_quarantine.NEW_GRACE_REASON,
                "sources": [],
            }
            view = {"items": [grace_item], "grace": [grace_item], "suggested": []}

            with mock.patch.object(skill_quarantine, "build_quarantine_view", return_value=view):
                preview = skill_quarantine.block_all_grace(
                    paths,
                    today="2026-06-05",
                    dry_run=True,
                )
                result = skill_quarantine.block_all_grace(
                    paths,
                    today="2026-06-05",
                    apply=False,
                )
            state = skill_quarantine.read_state(paths)

            self.assertEqual([row["entity_key"] for row in preview["grace"]], ["skill:lark-mail"])
            self.assertEqual([entry["entity_key"] for entry in result["blocked"]], ["skill:lark-mail"])
            self.assertIn("skill:lark-mail", state["entries"])
            self.assertEqual(state["entries"]["skill:lark-mail"]["reason"], skill_quarantine.MANUAL_REASON)

    def test_block_all_grace_uses_supplied_view_without_rebuilding(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))
            grace_item = {
                "entity_key": "skill:lark-mail",
                "entity_type": "skill",
                "identifier": "lark-mail",
                "display_name": "lark-mail",
                "usage_30d": 0,
                "reason": skill_quarantine.NEW_GRACE_REASON,
                "sources": [],
            }
            view = {"items": [grace_item], "grace": [grace_item], "suggested": []}

            with mock.patch.object(skill_quarantine, "build_quarantine_view") as build_view:
                result = skill_quarantine.block_all_grace(paths, apply=False, view=view)

            build_view.assert_not_called()
            self.assertEqual([entry["entity_key"] for entry in result["blocked"]], ["skill:lark-mail"])

    def test_quarantined_skill_is_reapplied_if_installer_recreates_source(self):
        with TemporaryDirectory() as tmp:
            paths = runtime_paths_for_fixture(Path(tmp))
            skill_dir = paths.codex_home / "skills" / "lark-event"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("---\nname: lark-event\n---\n", encoding="utf-8")

            entry = skill_quarantine.block_entity(
                paths,
                "skill:lark-event",
                view={
                    "items": [
                        {
                            "entity_key": "skill:lark-event",
                            "entity_type": "skill",
                            "identifier": "lark-event",
                            "display_name": "lark-event",
                            "usage_30d": 0,
                            "sources": [{"kind": "codex", "manifest_abspath": str(skill_dir / "SKILL.md")}],
                        }
                    ]
                },
            )
            quarantine_path = Path(entry["isolation_targets"][0]["quarantine_path"])
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("---\nname: lark-event\nupdated: true\n---\n", encoding="utf-8")

            view = skill_quarantine.build_quarantine_view(
                paths,
                today="2026-06-05",
                mcp_usage_view={"servers": []},
                codex_homes=[paths.codex_home],
            )
            state = skill_quarantine.read_state(paths)

            self.assertFalse(skill_dir.exists())
            self.assertTrue((quarantine_path / "SKILL.md").is_file())
            self.assertEqual(state["entries"]["skill:lark-event"]["isolation_status"], "moved")
            self.assertEqual(next(row for row in view["items"] if row["entity_key"] == "skill:lark-event")["status"], "quarantined")


if __name__ == "__main__":
    unittest.main()
