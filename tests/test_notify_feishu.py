#!/usr/bin/env python3

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import notify_feishu  # noqa: E402


class NotifyFeishuTest(unittest.TestCase):
    def test_sign_matches_feishu_custom_bot_algorithm_shape(self):
        self.assertEqual(
            notify_feishu.feishu_sign("secret", "1700000000"),
            "fiWS2+gh28DOydAv7hzONH/mDn9+b1Y4Y5ivXWXy8vA=",
        )

    def test_extract_changelog_reads_article_body_and_bullets(self):
        with TemporaryDirectory() as tmpdir:
            changelog = Path(tmpdir) / "v0.x.html"
            changelog.write_text(
                """
                <article class="changelog-entry" id="v1-2-0">
                  <div class="changelog-body">
                    <p>首段摘要 <code>openrelix</code></p>
                    <span class="group-title">修复</span>
                    <ul>
                      <li>修复回合失败通知。</li>
                      <li>补充发布提醒。</li>
                    </ul>
                  </div>
                </article>
                """,
                encoding="utf-8",
            )

            text = notify_feishu.extract_changelog("1.2.0", changelog)

        self.assertIn("首段摘要 openrelix", text)
        self.assertIn("修复", text)
        self.assertIn("- 修复回合失败通知。", text)
        self.assertIn("- 补充发布提醒。", text)

    def test_npm_published_message_includes_changelog_links_and_version(self):
        with TemporaryDirectory() as tmpdir:
            changelog = Path(tmpdir) / "v0.x.html"
            changelog.write_text(
                """
                <article id="v0-4-0">
                  <div class="changelog-body"><p>发布 0.4.0。</p></div>
                </article>
                """,
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "GITHUB_REPOSITORY": "openrelix/openrelix",
                    "RUN_URL": "https://github.com/openrelix/openrelix/actions/runs/1",
                },
                clear=False,
            ):
                message = notify_feishu.build_npm_published_message("0.4.0", changelog)

        self.assertIn("OpenRelix npm 新版本发布成功", message)
        self.assertIn("版本：v0.4.0", message)
        self.assertIn("https://www.npmjs.com/package/openrelix/v/0.4.0", message)
        self.assertIn("https://github.com/openrelix/openrelix/releases/tag/v0.4.0", message)
        self.assertIn("发布 0.4.0。", message)

    def test_mention_for_github_actor_uses_open_id_mapping(self):
        mention = notify_feishu.mention_for_github_actor(
            "octocat",
            json.dumps({"octocat": {"open_id": "ou_abc123", "name": "章三"}}),
        )

        self.assertEqual(mention, '<at user_id="ou_abc123">章三</at> (@octocat)')

    def test_mention_for_github_actor_falls_back_for_name_only_mapping(self):
        mention = notify_feishu.mention_for_github_actor(
            "octocat",
            json.dumps({"octocat": {"name": "章三"}}),
        )

        self.assertEqual(mention, "章三 (@octocat，未配置可 @ 的飞书 open_id)")

    def test_backmerge_failure_message_includes_submitter_mapping(self):
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_REF_NAME": "bugfix/version_0_3",
                "GITHUB_REPOSITORY": "openrelix/openrelix",
                "GITHUB_SHA": "abcdef123456",
                "GITHUB_ACTOR": "octocat",
                "FEISHU_USER_MAP_JSON": json.dumps(
                    {"octocat": {"open_id": "ou_abc123", "name": "章三"}}
                ),
            },
            clear=True,
        ):
            message = notify_feishu.build_backmerge_failure_message()

        self.assertIn("提交者：<at user_id=\"ou_abc123\">章三</at> (@octocat)", message)

    def test_send_message_skips_when_webhook_is_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(notify_feishu.send_message("hello"), 0)

    def test_dry_run_uses_fallback_webhook_and_secret(self):
        with mock.patch.dict(
            os.environ,
            {
                "FEISHU_FALLBACK_WEBHOOK_URL": "https://example.test/hook",
                "FEISHU_FALLBACK_WEBHOOK_SECRET": "secret",
            },
            clear=True,
        ):
            with mock.patch("builtins.print") as mock_print:
                self.assertEqual(notify_feishu.send_message("hello", dry_run=True), 0)

        payload = json.loads(mock_print.call_args.args[0])
        self.assertEqual(payload["msg_type"], "text")
        self.assertEqual(payload["content"]["text"], "hello")
        self.assertIn("timestamp", payload)
        self.assertIn("sign", payload)


if __name__ == "__main__":
    unittest.main()
