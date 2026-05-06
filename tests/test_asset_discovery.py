#!/usr/bin/env python3

import json
import os
import re
import sys
import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asset_runtime  # noqa: E402
import build_overview  # noqa: E402
from openrelix_overview import asset_discovery  # noqa: E402


def runtime_paths_for_fixture(root, home):
    base = asset_runtime.get_runtime_paths()
    state_root = root / "state"
    repo_root = root / "repo"
    return replace(
        base,
        repo_root=repo_root,
        state_root=state_root,
        codex_home=home / ".codex",
        claude_home=home / ".claude",
        repo_skill_root=repo_root / ".agents" / "skills",
        user_skill_root=home / ".codex" / "skills",
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
        launch_agents_dir=home / "Library" / "LaunchAgents",
        schema_path=repo_root / "templates" / "nightly-summary-schema.json",
    )


class AssetDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir(parents=True)
        self.paths = runtime_paths_for_fixture(self.root, self.home)
        self.home_patcher = mock.patch.object(asset_discovery.Path, "home", return_value=self.home)
        self.home_patcher.start()
        self.today = date(2026, 5, 5)

    def tearDown(self):
        self.home_patcher.stop()
        self.tmp.cleanup()

    def write_skill(self, root, identifier, name=None, description=None, frontmatter=True):
        skill_dir = root / identifier
        skill_dir.mkdir(parents=True, exist_ok=True)
        pieces = []
        if frontmatter:
            pieces.append("---")
            if name is not None:
                pieces.append("name: {}".format(name))
            if description is not None:
                pieces.append("description: {}".format(description))
            pieces.append("---")
        pieces.append("# {}".format(identifier))
        manifest = skill_dir / "SKILL.md"
        manifest.write_text("\n".join(pieces) + "\n", encoding="utf-8")
        return manifest

    def write_codex_rollout(self, day, session_id, commands, extra_lines=None):
        root = self.paths.codex_home / "sessions" / day.strftime("%Y") / day.strftime("%m") / day.strftime("%d")
        root.mkdir(parents=True, exist_ok=True)
        path = root / "rollout-{}.jsonl".format(session_id)
        rows = list(extra_lines or [])
        for command in commands:
            rows.append(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": command}),
                    },
                }
            )
        path.write_text("\n".join(json.dumps(row) if not isinstance(row, str) else row for row in rows) + "\n", encoding="utf-8")
        return path

    def write_claude_session(self, session_id, timestamp, file_paths, mtime_day=None):
        root = self.home / ".claude" / "projects" / "encoded-project"
        root.mkdir(parents=True, exist_ok=True)
        path = root / "{}.jsonl".format(session_id)
        content = [
            {
                "type": "tool_use",
                "name": "Read",
                "input": {"file_path": str(file_path)},
            }
            for file_path in file_paths
        ]
        rows = [
            {
                "type": "assistant",
                "timestamp": timestamp,
                "message": {"content": content},
            }
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        if mtime_day is not None:
            stamp = datetime.combine(mtime_day, time(12, 0)).timestamp()
            os.utime(path, (stamp, stamp))
        return path

    def assets_by_key(self, assets):
        return {asset["asset_key"]: asset for asset in assets}

    def compute(self, installed=None):
        return asset_discovery.compute_activations_and_extend(self.paths, installed or [], self.today)

    def test_high_level_type_mapping_covers_all_discovered_kinds(self):
        expected = {
            "codex_skill": "skill",
            "claude_skill": "skill",
            "repo_skill": "skill",
            "external_repo_skill": "skill",
            "project_skill": "skill",
            "codex_prompt": "prompt",
            "codex_rule": "rule",
            "claude_plugin": "plugin",
            "launch_agent": "automation",
        }

        for kind, high_level in expected.items():
            self.assertEqual(asset_discovery._high_level_type(kind), high_level)

    def test_discovers_codex_skill(self):
        self.write_skill(self.paths.codex_home / "skills", "alpha", name="Alpha", description="Codex helper")

        assets = self.assets_by_key(asset_discovery.discover_installed_assets(self.paths))

        self.assertIn("codex_skill:alpha", assets)
        self.assertEqual(assets["codex_skill:alpha"]["name"], "Alpha")
        self.assertEqual(assets["codex_skill:alpha"]["description"], "Codex helper")

    def test_discovers_claude_skill(self):
        self.write_skill(self.home / ".claude" / "skills", "bravo", description="Claude helper")

        assets = self.assets_by_key(asset_discovery.discover_installed_assets(self.paths))

        self.assertIn("claude_skill:bravo", assets)

    def test_discovers_repo_skill(self):
        self.write_skill(self.paths.repo_skill_root, "repo-helper", description="Repo helper")

        assets = self.assets_by_key(asset_discovery.discover_installed_assets(self.paths))

        self.assertIn("repo_skill:repo-helper", assets)
        self.assertEqual(assets["repo_skill:repo-helper"]["manifest_path"], ".agents/skills/repo-helper/SKILL.md")

    def test_discovers_codex_prompt(self):
        prompt_root = self.paths.codex_home / "prompts"
        prompt_root.mkdir(parents=True)
        (prompt_root / "daily.md").write_text("# Daily\n", encoding="utf-8")

        assets = self.assets_by_key(asset_discovery.discover_installed_assets(self.paths))

        self.assertIn("codex_prompt:daily", assets)

    def test_discovers_codex_rule(self):
        rule_root = self.paths.codex_home / "rules"
        rule_root.mkdir(parents=True)
        (rule_root / "repo.rules").write_text("rule\n", encoding="utf-8")

        assets = self.assets_by_key(asset_discovery.discover_installed_assets(self.paths))

        self.assertIn("codex_rule:repo", assets)

    def test_discovers_claude_plugin_from_manifest(self):
        plugin_root = self.home / ".claude" / "plugins"
        plugin_root.mkdir(parents=True)
        (plugin_root / "installed_plugins.json").write_text(
            json.dumps({"plugins": {"plug-a": {"name": "Plugin A", "description": "Plugin helper"}}}),
            encoding="utf-8",
        )

        assets = self.assets_by_key(asset_discovery.discover_installed_assets(self.paths))

        self.assertIn("claude_plugin:plug-a", assets)
        self.assertEqual(assets["claude_plugin:plug-a"]["name"], "Plugin A")

    def test_discovers_launch_agent_on_macos(self):
        launch_root = self.paths.launch_agents_dir
        launch_root.mkdir(parents=True)
        (launch_root / "com.openrelix.worker.plist").write_text("<plist />", encoding="utf-8")

        with mock.patch.object(asset_discovery.sys, "platform", "darwin"):
            assets = self.assets_by_key(asset_discovery.discover_installed_assets(self.paths))

        self.assertIn("launch_agent:com.openrelix.worker", assets)

    def test_launch_agent_discovery_is_platform_guarded(self):
        launch_root = self.paths.launch_agents_dir
        launch_root.mkdir(parents=True)
        (launch_root / "com.openrelix.worker.plist").write_text("<plist />", encoding="utf-8")

        with mock.patch.object(asset_discovery.sys, "platform", "linux"):
            assets = self.assets_by_key(asset_discovery.discover_installed_assets(self.paths))

        self.assertNotIn("launch_agent:com.openrelix.worker", assets)

    def test_missing_discovery_directories_are_tolerated(self):
        assets = asset_discovery.discover_installed_assets(self.paths)

        self.assertEqual(assets, [])

    def test_frontmatter_parser_well_formed(self):
        manifest = self.write_skill(self.paths.codex_home / "skills", "front", name="Front", description="Clean desc")

        parsed = asset_discovery.parse_skill_frontmatter(manifest)

        self.assertEqual(parsed, {"name": "Front", "description": "Clean desc"})

    def test_frontmatter_parser_missing_closing_fence(self):
        skill_dir = self.paths.codex_home / "skills" / "bad"
        skill_dir.mkdir(parents=True)
        manifest = skill_dir / "SKILL.md"
        manifest.write_text("---\nname: Bad\ndescription: missing close\n# Body\n", encoding="utf-8")

        parsed = asset_discovery.parse_skill_frontmatter(manifest)

        self.assertEqual(parsed, {})

    def test_frontmatter_parser_multiline_description(self):
        skill_dir = self.paths.codex_home / "skills" / "multi"
        skill_dir.mkdir(parents=True)
        manifest = skill_dir / "SKILL.md"
        manifest.write_text(
            "---\nname: Multi\ndescription: first line\n  second line\n  third line\n---\n",
            encoding="utf-8",
        )

        parsed = asset_discovery.parse_skill_frontmatter(manifest)

        self.assertEqual(parsed["description"], "first line second line third line")

    def test_missing_frontmatter_name_falls_back_to_identifier(self):
        self.write_skill(self.paths.codex_home / "skills", "fallback", description="No name")

        assets = self.assets_by_key(asset_discovery.discover_installed_assets(self.paths))

        self.assertEqual(assets["codex_skill:fallback"]["name"], "fallback")

    def test_description_redacts_user_paths(self):
        user_path = "/{}{}".format("Users", "/alice/private/project")
        self.write_skill(
            self.paths.codex_home / "skills",
            "paths",
            description="Use {} for fixtures".format(user_path),
        )

        assets = self.assets_by_key(asset_discovery.discover_installed_assets(self.paths))

        self.assertIn("~/private/project", assets["codex_skill:paths"]["description"])
        self.assertNotIn("/{}{}".format("Users", "/alice"), assets["codex_skill:paths"]["description"])

    def test_codex_rollout_counts_discovered_skill_only(self):
        manifest = self.write_skill(self.paths.codex_home / "skills", "foo")
        installed = asset_discovery.discover_installed_assets(self.paths)
        self.write_codex_rollout(
            self.today,
            "one",
            [
                "sed -n '1,20p' {}".format(manifest),
                "sed -n '1,20p' {}/skills/missing/SKILL.md".format(self.paths.codex_home),
            ],
        )

        assets, frequency = self.compute(installed)

        self.assertIn("codex_skill:foo", self.assets_by_key(assets))
        self.assertEqual(frequency["codex_skill:foo"]["windows_30d"], 1)
        self.assertNotIn("codex_skill:missing", frequency)

    def test_codex_rollout_dedupes_same_skill_within_session(self):
        manifest = self.write_skill(self.paths.codex_home / "skills", "foo")
        installed = asset_discovery.discover_installed_assets(self.paths)
        self.write_codex_rollout(self.today, "one", ["cat {}".format(manifest), "sed -n '1p' {}".format(manifest)])

        _assets, frequency = self.compute(installed)

        self.assertEqual(frequency["codex_skill:foo"]["windows_30d"], 1)
        self.assertEqual(frequency["codex_skill:foo"]["read_events_30d"], 2)

    def test_codex_parallel_tool_use_counts_nested_skill_reads(self):
        manifest = self.write_skill(self.paths.codex_home / "skills", "foo")
        installed = asset_discovery.discover_installed_assets(self.paths)
        self.write_codex_rollout(
            self.today,
            "parallel",
            [],
            extra_lines=[
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "multi_tool_use.parallel",
                        "arguments": json.dumps(
                            {
                                "tool_uses": [
                                    {
                                        "recipient_name": "functions.exec_command",
                                        "parameters": {"cmd": "cat {}".format(manifest)},
                                    },
                                    {
                                        "recipient_name": "functions.exec_command",
                                        "parameters": {"cmd": "sed -n '1p' {}".format(manifest)},
                                    },
                                ]
                            }
                        ),
                    },
                }
            ],
        )

        _assets, frequency = self.compute(installed)

        self.assertEqual(frequency["codex_skill:foo"]["windows_30d"], 1)
        self.assertEqual(frequency["codex_skill:foo"]["read_events_30d"], 2)

    def test_codex_rollout_counts_multiple_sessions_on_same_date(self):
        manifest = self.write_skill(self.paths.codex_home / "skills", "foo")
        installed = asset_discovery.discover_installed_assets(self.paths)
        self.write_codex_rollout(self.today, "one", ["cat {}".format(manifest)])
        self.write_codex_rollout(self.today, "two", ["cat {}".format(manifest)])

        _assets, frequency = self.compute(installed)

        self.assertEqual(frequency["codex_skill:foo"]["windows_30d"], 2)

    def test_codex_rollout_uses_path_date_for_bucket(self):
        manifest = self.write_skill(self.paths.codex_home / "skills", "foo")
        installed = asset_discovery.discover_installed_assets(self.paths)
        old_day = self.today - timedelta(days=8)
        self.write_codex_rollout(old_day, "old", ["cat {}".format(manifest)])

        _assets, frequency = self.compute(installed)

        self.assertEqual(frequency["codex_skill:foo"]["windows_7d"], 0)
        self.assertEqual(frequency["codex_skill:foo"]["windows_30d"], 1)
        self.assertEqual(frequency["codex_skill:foo"]["last_seen"], old_day.isoformat())

    def test_codex_rollout_malformed_json_is_ignored(self):
        manifest = self.write_skill(self.paths.codex_home / "skills", "foo")
        installed = asset_discovery.discover_installed_assets(self.paths)
        self.write_codex_rollout(self.today, "bad", ["cat {}".format(manifest)], extra_lines=["{bad json"])

        _assets, frequency = self.compute(installed)

        self.assertEqual(frequency["codex_skill:foo"]["windows_30d"], 1)

    def test_claude_tool_use_activation_counts_read_manifest(self):
        manifest = self.write_skill(self.home / ".claude" / "skills", "bar")
        installed = asset_discovery.discover_installed_assets(self.paths)
        self.write_claude_session("c1", "2026-05-05T12:00:00+00:00", [manifest, manifest], mtime_day=self.today)

        _assets, frequency = self.compute(installed)

        self.assertEqual(frequency["claude_skill:bar"]["windows_30d"], 1)
        self.assertEqual(frequency["claude_skill:bar"]["read_events_30d"], 2)

    def test_claude_session_mtime_pre_prunes_old_file(self):
        manifest = self.write_skill(self.home / ".claude" / "skills", "bar")
        installed = asset_discovery.discover_installed_assets(self.paths)
        self.write_claude_session(
            "old-mtime",
            "2026-05-05T12:00:00+00:00",
            [manifest],
            mtime_day=self.today - timedelta(days=60),
        )

        _assets, frequency = self.compute(installed)

        self.assertEqual(frequency["claude_skill:bar"]["windows_30d"], 0)

    def test_activation_31_days_ago_is_excluded(self):
        manifest = self.write_skill(self.paths.codex_home / "skills", "foo")
        installed = asset_discovery.discover_installed_assets(self.paths)
        self.write_codex_rollout(self.today - timedelta(days=31), "old", ["cat {}".format(manifest)])

        _assets, frequency = self.compute(installed)

        self.assertEqual(frequency["codex_skill:foo"]["windows_30d"], 0)

    def test_cross_cli_attribution_uses_path_not_session_source(self):
        codex_manifest = self.write_skill(self.paths.codex_home / "skills", "cross")
        self.write_skill(self.home / ".claude" / "skills", "cross")
        installed = asset_discovery.discover_installed_assets(self.paths)
        self.write_claude_session("c1", "2026-05-05T12:00:00+00:00", [codex_manifest], mtime_day=self.today)

        _assets, frequency = self.compute(installed)

        self.assertEqual(frequency["codex_skill:cross"]["windows_30d"], 1)
        self.assertEqual(frequency["claude_skill:cross"]["windows_30d"], 0)

    def test_external_repo_skill_singleton_is_discovered_but_gated_from_renderable(self):
        manifest = self.write_skill(self.root / "other-repo" / ".agents" / "skills", "alpha")
        self.write_codex_rollout(self.today, "one", ["cat {}".format(manifest)])

        assets, frequency = self.compute([])
        visible = asset_discovery.filter_renderable_assets(assets, frequency)

        self.assertIn("external_repo_skill:alpha", self.assets_by_key(assets))
        self.assertEqual(frequency["external_repo_skill:alpha"]["windows_30d"], 1)
        self.assertNotIn("external_repo_skill:alpha", self.assets_by_key(visible))

    def test_external_repo_skill_with_two_sessions_is_renderable(self):
        manifest = self.write_skill(self.root / "other-repo" / ".agents" / "skills", "alpha")
        self.write_codex_rollout(self.today, "one", ["cat {}".format(manifest)])
        self.write_codex_rollout(self.today, "two", ["cat {}".format(manifest)])

        assets, frequency = self.compute([])
        visible = self.assets_by_key(asset_discovery.filter_renderable_assets(assets, frequency))

        self.assertIn("external_repo_skill:alpha", visible)
        self.assertEqual(frequency["external_repo_skill:alpha"]["windows_30d"], 2)

    def test_project_skill_discovered_via_claude_sessions_with_description(self):
        manifest = self.write_skill(
            self.root / "project-a" / "skills",
            "beta",
            name="Beta",
            description="hello world",
        )
        self.write_claude_session("c1", "2026-05-05T12:00:00+00:00", [manifest], mtime_day=self.today)
        self.write_claude_session("c2", "2026-05-05T13:00:00+00:00", [manifest], mtime_day=self.today)

        assets, frequency = self.compute([])
        visible = self.assets_by_key(asset_discovery.filter_renderable_assets(assets, frequency))

        self.assertIn("project_skill:beta", visible)
        self.assertEqual(visible["project_skill:beta"]["description"], "hello world")
        self.assertEqual(frequency["project_skill:beta"]["windows_30d"], 2)

    def test_project_skill_same_name_aggregates_across_paths_and_dedupes_per_session(self):
        manifest_one = self.write_skill(self.root / "proj1" / "skills", "helper")
        manifest_two = self.write_skill(self.root / "proj2" / "skills", "helper")
        manifest_three = self.write_skill(self.root / "proj3" / "skills", "helper")
        self.write_codex_rollout(self.today, "one", ["cat {} && cat {}".format(manifest_one, manifest_two)])
        self.write_codex_rollout(self.today, "two", ["cat {}".format(manifest_three)])

        assets, frequency = self.compute([])
        visible = self.assets_by_key(asset_discovery.filter_renderable_assets(assets, frequency))

        self.assertEqual([key for key in self.assets_by_key(assets) if key == "project_skill:helper"], ["project_skill:helper"])
        self.assertIn("project_skill:helper", visible)
        self.assertEqual(frequency["project_skill:helper"]["windows_30d"], 2)

    def test_same_name_in_different_kinds_stays_separate(self):
        codex_manifest = self.write_skill(self.paths.codex_home / "skills", "foo")
        project_manifest = self.write_skill(self.root / "proj" / "skills", "foo")
        installed = asset_discovery.discover_installed_assets(self.paths)
        self.write_codex_rollout(self.today, "one", ["cat {} && cat {}".format(codex_manifest, project_manifest)])
        self.write_codex_rollout(self.today, "two", ["cat {}".format(project_manifest)])

        assets, frequency = self.compute(installed)
        visible = self.assets_by_key(asset_discovery.filter_renderable_assets(assets, frequency))

        self.assertIn("codex_skill:foo", visible)
        self.assertIn("project_skill:foo", visible)
        self.assertEqual(frequency["codex_skill:foo"]["windows_30d"], 1)
        self.assertEqual(frequency["project_skill:foo"]["windows_30d"], 2)

    def test_same_name_render_rows_aggregate_across_skill_sub_kinds(self):
        codex_manifest = self.write_skill(self.paths.codex_home / "skills", "foo", description="Codex source")
        repo_manifest = self.write_skill(self.paths.repo_skill_root, "foo", description="Repo source")
        installed = asset_discovery.discover_installed_assets(self.paths)
        self.write_codex_rollout(self.today, "one", ["cat {}".format(codex_manifest)])
        self.write_codex_rollout(self.today, "two", ["cat {}".format(codex_manifest)])
        self.write_codex_rollout(self.today, "three", ["cat {}".format(repo_manifest)])

        assets, frequency = self.compute(installed)
        visible = asset_discovery.filter_renderable_assets(assets, frequency)
        rows = asset_discovery.aggregate_renderable_assets(visible, frequency)
        skill_rows = [row for row in rows if row["type"] == "skill" and row["identifier"] == "foo"]

        self.assertEqual(len(skill_rows), 1)
        self.assertEqual(skill_rows[0]["windows_30d"], 3)
        self.assertEqual(skill_rows[0]["description"], "Codex source")
        self.assertEqual(
            {source["kind"] for source in skill_rows[0]["sources"]},
            {"codex_skill", "repo_skill"},
        )
        self.assertEqual(
            {source["label"] for source in skill_rows[0]["source_labels"]},
            {"~/.codex/skills", "<repo>/.agents/skills"},
        )

    def test_click_target_uses_highest_activation_source(self):
        paths = {
            "codex_skill": self.root / "codex" / "foo" / "SKILL.md",
            "repo_skill": self.root / "repo" / "foo" / "SKILL.md",
            "claude_skill": self.root / "claude" / "foo" / "SKILL.md",
        }
        assets = [
            {
                "asset_key": "{}:foo".format(kind),
                "kind": kind,
                "identifier": "foo",
                "name": "foo",
                "description": kind,
                "source_root": kind,
                "manifest_path": str(path),
                "manifest_abspath": str(path),
            }
            for kind, path in paths.items()
        ]
        frequency = {
            "codex_skill:foo": {"windows_7d": 5, "windows_30d": 5, "last_seen": "2026-05-04"},
            "repo_skill:foo": {"windows_7d": 2, "windows_30d": 2, "last_seen": "2026-05-05"},
            "claude_skill:foo": {"windows_7d": 0, "windows_30d": 0, "last_seen": None},
        }

        rows = asset_discovery.aggregate_renderable_assets(assets, frequency)
        foo = [row for row in rows if row["identifier"] == "foo"][0]

        self.assertEqual(foo["click_target"], str(paths["codex_skill"]))

    def test_click_target_falls_back_to_openable_source_when_active_source_has_no_path(self):
        codex_manifest = self.paths.codex_home / "skills" / "foo" / "SKILL.md"
        assets = [
            {
                "asset_key": "codex_skill:foo",
                "kind": "codex_skill",
                "identifier": "foo",
                "name": "foo",
                "description": "Installed source",
                "source_root": "~/.codex/skills",
                "manifest_path": str(codex_manifest),
                "manifest_abspath": str(codex_manifest),
            },
            {
                "asset_key": "project_skill:foo",
                "kind": "project_skill",
                "identifier": "foo",
                "name": "foo",
                "description": "Active source",
                "source_root": ".../skills",
                "manifest_path": ".../skills/foo/SKILL.md",
                "manifest_abspath": "",
            },
        ]
        frequency = {
            "codex_skill:foo": {"windows_7d": 0, "windows_30d": 0, "last_seen": None},
            "project_skill:foo": {"windows_7d": 4, "windows_30d": 9, "last_seen": "2026-05-05"},
        }

        rows = asset_discovery.aggregate_renderable_assets(assets, frequency)
        foo = [row for row in rows if row["identifier"] == "foo"][0]

        self.assertEqual(foo["windows_30d"], 9)
        self.assertEqual(foo["click_target"], str(codex_manifest))

    def test_discovered_skill_name_uses_finder_button_instead_of_file_href(self):
        html = build_overview.make_discovered_asset_name_html(
            {
                "type": "skill",
                "name": "foo",
                "identifier": "foo",
                "click_target": str(self.paths.codex_home / "skills" / "foo" / "SKILL.md"),
            }
        )

        self.assertIn("data-open-finder-path", html)
        self.assertIn("<button", html)
        self.assertNotIn("file://", html)
        self.assertNotIn("<a ", html)

    def test_noise_gate_preserves_installed_zero_activation_skill(self):
        self.write_skill(self.paths.codex_home / "skills", "quiet")
        project_manifest = self.write_skill(self.root / "proj" / "skills", "single")
        installed = asset_discovery.discover_installed_assets(self.paths)
        self.write_codex_rollout(self.today, "one", ["cat {}".format(project_manifest)])

        assets, frequency = self.compute(installed)
        visible = self.assets_by_key(asset_discovery.filter_renderable_assets(assets, frequency))
        html = "".join("<tr><td>{}</td></tr>".format(asset["identifier"]) for asset in visible.values())

        self.assertIn("codex_skill:quiet", visible)
        self.assertNotIn("project_skill:single", visible)
        self.assertIn("quiet", html)
        self.assertNotIn("single", html)

    def test_noise_gate_persists_for_organic_skill_kinds(self):
        external_manifest = self.write_skill(self.root / "other-repo" / ".agents" / "skills", "external")
        project_manifest = self.write_skill(self.root / "project" / "skills", "project")
        self.write_codex_rollout(self.today, "one", ["cat {} && cat {}".format(external_manifest, project_manifest)])

        assets, frequency = self.compute([])
        visible = self.assets_by_key(asset_discovery.filter_renderable_assets(assets, frequency))

        self.assertEqual(frequency["external_repo_skill:external"]["windows_30d"], 1)
        self.assertEqual(frequency["project_skill:project"]["windows_30d"], 1)
        self.assertNotIn("external_repo_skill:external", visible)
        self.assertNotIn("project_skill:project", visible)

    def test_old_asset_panels_are_absent_from_rendered_asset_html(self):
        rows = [
            {
                "type": "skill",
                "identifier": "alpha",
                "name": "alpha",
                "description": "Alpha skill",
                "windows_7d": 1,
                "windows_30d": 3,
                "last_seen": "2026-05-05",
                "click_target": str(self.root / "alpha" / "SKILL.md"),
                "source_labels": [{"label": "~/.codex/skills", "label_en": "~/.codex/skills"}],
            }
        ]
        html = "\n".join(
            [
                build_overview.make_bar_group(
                    "资产类型分布",
                    build_overview.build_discovered_type_mix_rows(rows),
                    "teal",
                ),
                build_overview.make_bar_group(
                    "月度活动",
                    [{"label": "2026-05", "label_en": "2026-05", "value": 1}],
                    "slate",
                ),
                build_overview.make_discovered_assets_section(rows),
                build_overview.make_top_skill_rows(asset_discovery.top_skill_rows(rows)),
            ]
        )

        for removed in ("适用层级", "项目 / 上下文分布", "最近更新的资产"):
            self.assertEqual(html.count(removed), 0)

    def test_top_skill_rows_sort_by_30d_reads_then_sessions_then_name(self):
        rows = [
            {"type": "skill", "identifier": "beta", "name": "beta", "description": "", "windows_30d": 4, "read_events_30d": 9},
            {"type": "skill", "identifier": "alpha", "name": "alpha", "description": "", "windows_30d": 8, "read_events_30d": 8},
            {"type": "skill", "identifier": "gamma", "name": "gamma", "description": "", "windows_30d": 4, "read_events_30d": 9},
        ]

        html = build_overview.make_top_skill_rows(asset_discovery.top_skill_rows(rows))
        first_identifier = re.search(r'<tr data-asset-identifier="([^"]+)"', html).group(1)

        self.assertEqual(first_identifier, "beta")
        self.assertIn("<td>9</td>", html)

    def test_single_asset_stats_snapshot_anchors_frequency_to_requested_date(self):
        manifest = self.write_skill(self.paths.codex_home / "skills", "foo", name="Foo")
        self.write_codex_rollout(self.today, "today", ["cat {}".format(manifest)])
        self.write_codex_rollout(self.today - timedelta(days=8), "old", ["cat {}".format(manifest)])

        snapshot = asset_discovery.build_asset_stats_snapshot(
            self.paths,
            self.today,
            generated_at="2026-05-05T12:00:00+08:00",
            monthly_months=2,
        )

        self.assertEqual(snapshot["date"], "2026-05-05")
        self.assertEqual(snapshot["lookback"]["windows_7d_start"], "2026-04-29")
        self.assertEqual(snapshot["lookback"]["windows_30d_start"], "2026-04-06")
        self.assertEqual(snapshot["summary"]["active_skills_7d"], 1)
        self.assertEqual(snapshot["summary"]["active_skills_30d"], 1)
        self.assertEqual(snapshot["summary"]["skill_sessions_7d"], 1)
        self.assertEqual(snapshot["summary"]["skill_sessions_30d"], 2)
        self.assertEqual(snapshot["summary"]["skill_reads_7d"], 1)
        self.assertEqual(snapshot["summary"]["skill_reads_30d"], 2)
        self.assertEqual(snapshot["top_skills"][0]["identifier"], "foo")
        self.assertEqual(snapshot["top_skills"][0]["windows_30d"], 2)
        self.assertEqual(snapshot["top_skills"][0]["read_events_30d"], 2)
        self.assertEqual(
            {row["label"]: row["value"] for row in snapshot["monthly_activity"]},
            {"2026-04": 1, "2026-05": 1},
        )

    def test_single_asset_stats_monthly_one_still_scans_full_30d_frequency(self):
        manifest = self.write_skill(self.paths.codex_home / "skills", "foo", name="Foo")
        self.write_codex_rollout(self.today, "today", ["cat {}".format(manifest)])
        self.write_codex_rollout(self.today - timedelta(days=8), "old", ["cat {}".format(manifest)])

        snapshot = asset_discovery.build_asset_stats_snapshot(
            self.paths,
            self.today,
            generated_at="2026-05-05T12:00:00+08:00",
            monthly_months=1,
        )

        self.assertEqual(snapshot["summary"]["skill_sessions_30d"], 2)
        self.assertEqual(snapshot["summary"]["skill_reads_30d"], 2)
        self.assertEqual(
            {row["label"]: row["value"] for row in snapshot["monthly_activity"]},
            {"2026-05": 1},
        )

    def test_asset_stats_snapshot_panel_renders_single_backfill_command(self):
        snapshot = {
            "date": "2026-05-05",
            "generated_at": "2026-05-05T12:00:00+08:00",
            "command": "openrelix asset-stats --date 2026-05-05",
            "summary": {
                "renderable_assets": 3,
                "active_skills_30d": 2,
                "skill_reads_30d": 11,
                "skill_sessions_30d": 7,
            },
            "top_skills": [
                {"identifier": "alpha", "name": "Alpha", "read_events_30d": 9, "windows_30d": 5},
            ],
        }

        html = build_overview.make_asset_stats_snapshot_panel(snapshot, "2026-05-05")

        self.assertIn('id="asset-stats-snapshot-section"', html)
        self.assertIn("openrelix asset-stats --date 2026-05-05", html)
        self.assertIn("Alpha", html)
        self.assertIn("11", html)
        self.assertIn("9", html)
        self.assertIn("Snapshot Top Skills", html)

    def test_path_classifier_follows_canonical_roots(self):
        codex_manifest = self.paths.codex_home / "skills" / "foo" / "SKILL.md"
        claude_manifest = self.home / ".claude" / "skills" / "bar" / "SKILL.md"
        repo_manifest = self.paths.repo_skill_root / "baz" / "SKILL.md"
        external_manifest = self.root / "other" / ".agents" / "skills" / "qux" / "SKILL.md"
        project_manifest = self.root / "project" / "skills" / "local" / "SKILL.md"

        self.assertEqual(asset_discovery.classify_skill_manifest_path(str(codex_manifest), self.paths), ("codex_skill", "foo"))
        self.assertEqual(asset_discovery.classify_skill_manifest_path(str(claude_manifest), self.paths), ("claude_skill", "bar"))
        self.assertEqual(asset_discovery.classify_skill_manifest_path(str(repo_manifest), self.paths), ("repo_skill", "baz"))
        self.assertEqual(asset_discovery.classify_skill_manifest_path(str(external_manifest), self.paths), ("external_repo_skill", "qux"))
        self.assertEqual(asset_discovery.classify_skill_manifest_path("skills/local/SKILL.md", self.paths), ("project_skill", "local"))
        self.assertEqual(asset_discovery.classify_skill_manifest_path(str(project_manifest), self.paths), ("project_skill", "local"))


if __name__ == "__main__":
    unittest.main()
