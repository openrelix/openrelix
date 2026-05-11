#!/usr/bin/env python3

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_overview  # noqa: E402


def make_window(index, title, project_label="OpenRelix", extra_text="", conclusion_count=4):
    text = " ".join(
        [
            title,
            "完成 实现 验证通过 确认 落地 提交 收口",
            "资产 沉淀 复用 规则 以后 必须",
            extra_text,
        ]
    )
    return {
        "window_id": "window-{}".format(index),
        "window_id_short": "w{}".format(index),
        "display_label": "#{}".format(index),
        "window_title": title,
        "window_summary": text,
        "question_summary": "如何把这次成果沉淀为可复用资产",
        "main_takeaway": text,
        "keywords": ["资产", "沉淀", "复用"],
        "recent_prompts": [{"text": "后续记住这条规则"}],
        "recent_conclusions": [{"text": text}],
        "summary_pairs": [{"q": "q", "a": "a"}],
        "question_count": 3,
        "conclusion_count": conclusion_count,
        "cwd_display": "/workspace/openrelix",
        "project_label": project_label,
    }


class WorkAssetDeskTests(unittest.TestCase):
    def test_daily_outcomes_are_importance_filtered_capped_and_traceable(self):
        window_overview = {
            "date": "2026-05-09",
            "window_count": 6,
            "windows": [
                make_window(1, "记忆规则沉淀完成", extra_text="memory codex_home"),
                make_window(2, "排障流程收口完成", extra_text="playbook 检查清单"),
                make_window(3, "模板方案验证通过", extra_text="template 模板"),
                make_window(4, "后台自动化落地", extra_text="automation 自动化"),
                make_window(5, "Skill 资产发布", extra_text="skill skill.md"),
                {
                    "window_id": "",
                    "window_title": "普通闲聊",
                    "window_summary": "暂无结论",
                    "question_count": 0,
                    "conclusion_count": 0,
                    "summary_pairs": [],
                    "project_label": "OpenRelix",
                },
            ],
        }

        outcomes = build_overview.build_daily_outcomes_for_window_overview(window_overview, language="zh")

        self.assertEqual(len(outcomes), 4)
        self.assertNotIn("普通闲聊", {item["title"] for item in outcomes})
        self.assertEqual(
            [item["importance_score"] for item in outcomes],
            sorted([item["importance_score"] for item in outcomes], reverse=True),
        )
        for outcome in outcomes:
            self.assertEqual(outcome["project_key"], "openrelix")
            self.assertTrue(outcome["source_window_ids"])
            self.assertTrue(outcome["evidence_refs"])
            self.assertTrue(any(ref["kind"] == "window" for ref in outcome["evidence_refs"]))

    def test_work_asset_candidates_detect_kinds_and_stay_pending(self):
        windows = [
            make_window(1, "记忆规则沉淀", extra_text="memory 记忆 规则 codex_home"),
            make_window(2, "排障流程资产", extra_text="playbook 排障 检查清单 流程"),
            make_window(3, "复用模板资产", extra_text="template 模板"),
            make_window(4, "后台自动化资产", extra_text="automation 自动化 定时 后台任务"),
        ]
        window_overview = {"date": "2026-05-09", "windows": windows}
        outcomes = [
            {
                "id": "outcome-{}".format(index),
                "date": "2026-05-09",
                "title": window["window_title"],
                "summary": window["main_takeaway"],
                "reuse_score": 0.70,
                "projects": ["OpenRelix"],
                "project_key": "openrelix",
                "source_window_ids": [window["window_id"]],
            }
            for index, window in enumerate(windows, 1)
        ]

        candidates = build_overview.build_work_asset_candidates(outcomes, window_overview=window_overview, language="zh")

        self.assertEqual({item["kind"] for item in candidates}, {"key_memory", "playbook", "template", "automation"})
        self.assertEqual({item["action_state"] for item in candidates}, {"pending_review"})
        self.assertTrue(all(item["source_outcome_id"] for item in candidates))
        self.assertTrue(all(item["source_window_ids"] for item in candidates))

    def test_work_asset_candidates_extract_documents_and_score_reuse_horizon(self):
        window = make_window(
            1,
            "OpenRelix 技术方案文档已沉淀",
            extra_text=(
                "生成飞书文档 https://sample.feishu.cn/docx/example "
                "同步 Google 文档 https://docs.google.com/document/d/example/edit "
                "参考 API 文档 https://docs.example.com/reference/widgets?api_key=abc&tab=overview "
                "关联 PR https://github.com/example/openrelix/pull/7 "
                "并落地 docs/technical-solution.md 技术方案 长期复用"
            ),
        )
        window_overview = {"date": "2026-05-09", "windows": [window], "window_count": 1}

        candidates = build_overview.build_work_asset_candidates([], window_overview=window_overview, language="zh")
        by_kind = {item["kind"]: item for item in candidates}

        self.assertIn("feishu_doc", by_kind)
        self.assertIn("google_doc", by_kind)
        self.assertIn("markdown_file", by_kind)
        self.assertIn("api_doc", by_kind)
        self.assertIn("issue_link", by_kind)
        self.assertEqual(by_kind["markdown_file"]["reuse_horizon"], "long_term")
        self.assertTrue(by_kind["markdown_file"]["open_path"].endswith("docs/technical-solution.md"))
        self.assertEqual(by_kind["feishu_doc"]["open_url"], "https://sample.feishu.cn/docx/example")
        self.assertEqual(
            by_kind["api_doc"]["open_url"],
            "https://docs.example.com/reference/widgets?api_key=REDACTED&tab=overview",
        )

    def test_work_asset_candidates_preserve_raw_cloud_docs_after_summary_redaction(self):
        raw_window = {
            "window_id": "window-doc",
            "ai_host": "codex",
            "cwd": str(ROOT),
            "prompt_count": 1,
            "conclusion_count": 1,
            "started_at": "2026-05-09T09:00:00+08:00",
            "prompts": [
                {
                    "local_time": "2026-05-09T09:01:00+08:00",
                    "text": "帮我生成飞书云文档和 md 文件。",
                }
            ],
            "conclusions": [
                {
                    "completed_at": "2026-05-09T09:12:00+08:00",
                    "text": (
                        "已生成 [OpenRelix 技术方案](https://www.feishu.cn/docx/abc123) "
                        "和 [technical-solution.md](docs/technical-solution.md)。"
                    ),
                }
            ],
        }
        items = build_overview.build_window_items_from_daily_capture(
            {"date": "2026-05-09", "windows": [raw_window], "collection_source": "app-server"},
            latest_nightly=None,
            language="zh",
        )
        self.assertEqual(len(items), 1)
        self.assertIn("artifact_refs", items[0])
        self.assertTrue(items[0]["artifact_refs"])

        redacted_item = dict(items[0])
        redacted_item["window_summary"] = "<link> 已生成云文档"
        redacted_item["main_takeaway"] = "已生成 <link> 和本地文档"
        redacted_item["recent_conclusions"] = [{"text": "已生成 <link> 和本地文档"}]
        candidates = build_overview.build_work_asset_candidates(
            [],
            window_overview={"date": "2026-05-09", "windows": [redacted_item], "window_count": 1},
            language="zh",
        )
        by_kind = {item["kind"]: item for item in candidates}

        self.assertIn("feishu_doc", by_kind)
        self.assertIn("markdown_file", by_kind)
        self.assertEqual(by_kind["feishu_doc"]["title"], "OpenRelix 技术方案")
        self.assertEqual(by_kind["feishu_doc"]["open_url"], "https://www.feishu.cn/docx/abc123")
        self.assertTrue(by_kind["markdown_file"]["open_path"].endswith("docs/technical-solution.md"))

    def test_work_asset_candidates_extract_general_web_references(self):
        window = make_window(
            1,
            "外部资料对齐完成",
            project_label="个人工作区",
            extra_text="有用网站资料 https://example.com/research/resource；后续可参考",
        )
        window_overview = {"date": "2026-05-09", "windows": [window], "window_count": 1}

        candidates = build_overview.build_work_asset_candidates([], window_overview=window_overview, language="zh")
        by_kind = {item["kind"]: item for item in candidates}

        self.assertIn("web_reference", by_kind)
        self.assertEqual(by_kind["web_reference"]["reuse_horizon"], "long_term")
        self.assertEqual(by_kind["web_reference"]["open_url"], "https://example.com/research/resource")

    def test_project_links_aggregate_items_candidates_and_windows(self):
        project_contexts = [
            {
                "label": "OpenRelix",
                "window_count": 7,
                "latest_activity_display": "今天",
                "summary": "项目资产台",
            },
            {
                "label": "Douyin",
                "window_count": 2,
                "latest_activity_display": "今天",
                "summary": "搜索工作",
            },
        ]
        outcomes = [
            {"project_key": "openrelix", "projects": ["OpenRelix"], "source_window_ids": ["w1"]},
            {"project_key": "openrelix", "projects": ["OpenRelix"], "source_window_ids": ["w2"]},
            {"project_key": "douyin", "projects": ["Douyin"], "source_window_ids": ["w3"]},
        ]
        candidates = [
            {"project_key": "openrelix", "kind": "playbook"},
            {"project_key": "openrelix", "kind": "template"},
            {"project_key": "douyin", "kind": "automation"},
        ]

        links = build_overview.build_work_asset_project_links(project_contexts, outcomes, candidates, language="zh")
        links_by_key = {item["project_key"]: item for item in links}

        self.assertEqual(links_by_key["openrelix"]["item_count"], 2)
        self.assertEqual(links_by_key["openrelix"]["asset_count"], 2)
        self.assertEqual(links_by_key["openrelix"]["window_count"], 7)
        self.assertEqual(links_by_key["douyin"]["item_count"], 1)
        self.assertEqual(links_by_key["douyin"]["asset_count"], 1)
        self.assertEqual(links_by_key["douyin"]["window_count"], 2)

    def test_daily_outcomes_merge_related_windows_and_do_not_treat_checks_as_pending(self):
        first = make_window(
            1,
            "指标解释和记忆面板已完成",
            extra_text="metrics memory panel check_personal_info.py 已完成",
        )
        first["keywords"] = ["记忆面板", "指标解释", "检查"]
        second = make_window(
            2,
            "记忆面板指标说明完成",
            extra_text="metrics memory panel git diff --check 验证通过",
        )
        second["keywords"] = ["记忆面板", "指标说明", "检查"]
        window_overview = {
            "date": "2026-05-09",
            "window_count": 2,
            "windows": [first, second],
        }

        outcomes = build_overview.build_daily_outcomes_for_window_overview(window_overview, language="zh")

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["status"], "confirmed")
        self.assertEqual(len(outcomes[0]["source_window_ids"]), 2)
        self.assertGreaterEqual(outcomes[0]["source_window_count"], 2)

    def test_work_asset_desk_requires_final_model_summary_for_main_outcomes(self):
        window = make_window(1, "局部窗口看起来完成", extra_text="完成 修复")
        preliminary = {
            "date": "2026-05-09",
            "stage": "preliminary",
            "model_status": "skipped_lightweight",
            "day_summary": "浅度整理不能代表一天成果",
            "durable_memories": [],
            "session_memories": [],
            "next_actions": ["继续处理主线"],
        }

        view = build_overview.build_work_asset_desk_view(
            "2026-05-09",
            window_overview={"date": "2026-05-09", "windows": [window], "window_count": 1},
            nightly_summary=preliminary,
            language="zh",
        )

        self.assertFalse(view["has_deep_summary"])
        self.assertEqual(view["outcomes"], [])
        self.assertEqual(view["pending_items"], [])
        self.assertIn("final", view["lead_text"])

    def test_work_asset_desk_uses_final_summary_for_mainline_and_followups(self):
        window = make_window(1, "OpenRelix 工作资产台主线完成", extra_text="主线完成")
        final = {
            "date": "2026-05-09",
            "stage": "final",
            "model_status": "completed",
            "day_summary": "今天主线完成了工作资产台总结升级，并留下回溯验证待办。",
            "durable_memories": [
                {
                    "title": "工作资产台总结升级完成",
                    "memory_type": "workflow",
                    "priority": "high",
                    "value_note": "成果改为围绕一天主线总结，避免把局部窗口当成日成果。",
                    "source_window_ids": ["window-1"],
                    "keywords": ["工作资产台", "总结"],
                }
            ],
            "session_memories": [
                {
                    "title": "hi",
                    "memory_type": "task",
                    "priority": "high",
                    "value_note": "噪声，不应该成为主线成果。",
                    "source_window_ids": ["window-1"],
                    "keywords": [],
                }
            ],
            "next_actions": ["回溯验证：跑最近 14 天 final 并检查主线成果质量"],
        }

        view = build_overview.build_work_asset_desk_view(
            "2026-05-09",
            window_overview={"date": "2026-05-09", "windows": [window], "window_count": 1},
            nightly_summary=final,
            language="zh",
        )

        self.assertTrue(view["has_deep_summary"])
        self.assertEqual([item["title"] for item in view["outcomes"]], ["工作资产台总结升级完成"])
        self.assertEqual(len(view["pending_items"]), 1)
        self.assertIn("回溯验证", view["pending_items"][0]["title"])

    def test_side_nav_renders_group_icons_or_icon_data_uris(self):
        html = build_overview.make_side_nav()

        self.assertIn("side-nav-group-icon", html)
        self.assertIn("<svg viewBox=", html)
        self.assertNotIn("data:image/png;base64,", html)


if __name__ == "__main__":
    unittest.main()
