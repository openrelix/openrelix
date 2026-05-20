#!/usr/bin/env python3
"""Send sanitized OpenRelix workflow notifications to Feishu custom bots."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_CHANGELOG = Path("docs/changelog/v0.x.html")
MAX_CHANGELOG_CHARS = 1400


def select_env(primary: str, fallback: str = "") -> str:
    return (os.environ.get(primary) or os.environ.get(fallback) or "").strip()


def feishu_sign(secret: str, timestamp: str) -> str:
    string_to_sign = "{}\n{}".format(timestamp, secret)
    digest = hmac.new(
        string_to_sign.encode("utf-8"),
        b"",
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_text_payload(message: str, secret: str = "", now: int | None = None) -> dict:
    payload = {
        "msg_type": "text",
        "content": {"text": message},
    }
    if secret:
        timestamp = str(int(now if now is not None else time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = feishu_sign(secret, timestamp)
    return payload


def post_payload(webhook_url: str, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError("Feishu webhook returned HTTP {}: {}".format(error.code, body)) from error
    if response.status >= 300:
        raise RuntimeError("Feishu webhook returned HTTP {}: {}".format(response.status, body))


def article_id_for_version(version: str) -> str:
    return "v" + str(version or "").strip().lstrip("v").replace(".", "-")


def strip_html_to_lines(fragment: str) -> list[str]:
    text = fragment
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"(?i)<span[^>]*class=\"group-title\"[^>]*>", "\n", text)
    text = re.sub(r"(?i)</span\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return lines


def truncate_text(value: str, max_chars: int = MAX_CHANGELOG_CHARS) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def extract_changelog(version: str, changelog_path: Path = DEFAULT_CHANGELOG) -> str:
    fallback = "未找到对应更新日志，请查看 docs/changelog/v0.x.html。"
    if not changelog_path.exists():
        return fallback
    text = changelog_path.read_text(encoding="utf-8")
    article_id = article_id_for_version(version)
    article_match = re.search(
        r'<article\b[^>]*id="' + re.escape(article_id) + r'"[^>]*>(.*?)</article>',
        text,
        re.S,
    )
    if not article_match:
        return fallback
    body_match = re.search(
        r'<div\b[^>]*class="changelog-body"[^>]*>(.*?)</div>',
        article_match.group(1),
        re.S,
    )
    body = body_match.group(1) if body_match else article_match.group(1)
    lines = strip_html_to_lines(body)
    return truncate_text("\n".join(lines) if lines else fallback)


def build_backmerge_failure_message() -> str:
    branch = os.environ.get("GITHUB_REF_NAME", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("GITHUB_SHA", "")
    run_url = os.environ.get("RUN_URL", "")
    commit_url = os.environ.get("COMMIT_URL", "")
    return "\n".join(
        [
            "OpenRelix bugfix 回合 main 失败",
            "仓库：{}".format(repo),
            "分支：{}".format(branch),
            "提交：{}".format(sha[:7] or "unknown"),
            "结果：未推送 main",
            "处理：把最新 origin/main 合入 bugfix 分支，解决冲突或检查失败后重新 push。",
            "Actions：{}".format(run_url),
            "Commit：{}".format(commit_url),
        ]
    )


def build_npm_published_message(version: str, changelog_path: Path = DEFAULT_CHANGELOG) -> str:
    normalized = str(version or "").strip().lstrip("v")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_url = os.environ.get("RUN_URL", "")
    npm_url = "https://www.npmjs.com/package/openrelix/v/{}".format(normalized)
    release_url = "https://github.com/{}/releases/tag/v{}".format(repo, normalized) if repo else ""
    return "\n".join(
        [
            "OpenRelix npm 新版本发布成功",
            "版本：v{}".format(normalized),
            "npm：{}".format(npm_url),
            "GitHub Release：{}".format(release_url),
            "Actions：{}".format(run_url),
            "",
            "更新日志：",
            extract_changelog(normalized, changelog_path),
        ]
    )


def send_message(message: str, *, dry_run: bool = False) -> int:
    webhook_url = select_env("FEISHU_WEBHOOK_URL", "FEISHU_FALLBACK_WEBHOOK_URL")
    if not webhook_url and not dry_run:
        print("Feishu webhook is not configured; skip notification.")
        return 0
    secret = select_env("FEISHU_WEBHOOK_SECRET", "FEISHU_FALLBACK_WEBHOOK_SECRET")
    payload = build_text_payload(message, secret=secret)
    if dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    post_payload(webhook_url, payload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send OpenRelix Feishu workflow notifications.")
    parser.add_argument("kind", choices=("backmerge-failure", "npm-published"))
    parser.add_argument("--version", default="")
    parser.add_argument("--changelog", default=str(DEFAULT_CHANGELOG))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.kind == "backmerge-failure":
        message = build_backmerge_failure_message()
    else:
        if not args.version:
            raise SystemExit("--version is required for npm-published")
        message = build_npm_published_message(args.version, Path(args.changelog))
    return send_message(message, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
