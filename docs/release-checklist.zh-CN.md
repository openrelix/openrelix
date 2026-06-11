# OpenRelix 发布检查清单

> 语言版本：[English](release-checklist.md) | 简体中文

这份清单用于 OpenRelix release、npm publish、GitHub release、package surface 或公开文档发布。普通功能改动请先看 [验证矩阵](validation-matrix.zh-CN.md)。

## 发布前边界

发布前确认：

- 版本号来自 `package.json`，并且和 changelog、站点文案一致。
- release notes 面向用户，避免内部工作细节。
- npm package 只包含公开可复用 surface。
- `.agents/skills/openrelix-*-harness/` 仍然是开发用 harness，不进入 public plugin bundle，除非有明确 release 决策。
- 公开 docs、fixtures、screenshots、examples 均已脱敏。

## 版本与 Changelog

1. 更新 `package.json` 版本。
2. 更新相关 changelog 或 release notes。
3. 对站点 roadmap、showcase、getting-started、README 做版本口径检查。
4. 如果版本已经发布，不覆盖它，准备下一个 patch 版本。

## 本地验证

发布前运行：

```bash
python3 scripts/check_personal_info.py
git diff --check
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest discover -s tests
npm pack --dry-run --json
```

如果 installer 或 LaunchAgent 变更，再补：

```bash
zsh -n install/install.sh scripts/*.sh
scripts/smoke_temp_panel.sh --no-open
scripts/cleanup_smoke_temp.sh --dry-run
```

如果 public site 变更，用本地 HTTP preview 验证，不用 `file://`：

```bash
python3 -m http.server 4173 -d docs
```

然后在浏览器中检查目标页面、语言切换、移动端宽度和截图/链接是否正常。

## Package Surface 检查

运行：

```bash
npm pack --dry-run --json
```

检查输出：

- 包含 `package.json`、public installer、public plugin bundle、public docs。
- 不包含 `.github/`、`tests/fixtures/`、raw state、logs、runtime cache、generated reports、private screenshots。
- 不包含 development-only harness skills，除非 release 决策明确改变 package surface。
- 不包含 secret、Cookie、账号标识、私有路径、内部 URL 或专有片段。唯一允许进入正式 npm 包的 token-like 文件是 `install/analytics/OpenRelixAnalyticsToken.txt`，由官方 publish workflow 从 `OPENRELIX_ANALYTICS_CLIENT_TOKEN` 注入，作为客户端 ingest token；高权限 analytics key 仍必须保留在包外。

## GitHub Release

1. 确认本地分支已经合入目标 `main`。
2. 确认 validation 通过。
3. 如果发布 `x.y.0` 大版本，确认上一个版本线分支，例如 `bugfix/version_0_3`，已经合回 `main`。
4. push branch 和 tag。
5. 使用配置好的 GitHub release workflow 或 release draft 流程。
6. 确认发布仓库已经配置 GitHub Actions secret：`OPENRELIX_ANALYTICS_CLIENT_TOKEN`，这样正式 npm 包会内置 macOS 埋点客户端 ingest token。
7. 验证 npm publish 或 trusted-publishing 输出。
8. 在干净上下文中验证 `npx openrelix --version` 或 package metadata。

`create-release.yml` 可以从 `main` 或指定维护分支（例如 `bugfix/version_0_3`）运行。它会读取所选 ref 上的 `package.json` 并在该 ref 上创建 release tag，然后用同一个 ref 触发 `publish.yml`，因此 patch 版本可以从维护分支发布。它也会自动执行版本线规则：发布 `x.y.0` 时，如果上一个 `bugfix/version_<x>_<y>` 分支还有未合入 `main` 的提交，会阻断 release；release 创建后，会在当前 release commit 上创建或复用新的远端维护分支 `bugfix/version_<x>_<y>`。

提交进入远端 `bugfix/version_*` 分支后，`bugfix-backmerge.yml` 会先基于最新 `origin/main` 准备一次合并，在合并结果上跑提交检查，全部通过后才 push 回 `origin/main`。如果检查失败或 merge 冲突，workflow 保持失败，需要人工修复后再继续发布。

如果要把回合失败消息发到飞书群，在目标群添加「自定义机器人」，把 webhook 保存到 GitHub Actions secret：`OPENRELIX_FEISHU_BACKMERGE_WEBHOOK_URL`。如果机器人开启了签名校验，再把签名密钥保存到 `OPENRELIX_FEISHU_BACKMERGE_SECRET`。这两个值都不要写进仓库；workflow 只会发送仓库、分支、commit、Actions 链接和通用处理提示。

如果要在回合失败时 @ 到提交者，把 GitHub 账号到飞书成员的 JSON 映射保存到 `OPENRELIX_FEISHU_USER_MAP_JSON`。飞书自定义机器人要用成员 `open_id` 才能真正触发 @；只有显示名只能展示文本。示例：`{"github-login":{"open_id":"ou_xxx","name":"Name"}}`。映射必须保存为 GitHub Actions secret，不要提交到仓库。

npm 发布通知由 `publish.yml` 发送：只有新版本真的发布成功后，才把版本号、npm 链接、GitHub Release 链接、Actions 链接和对应 changelog 条目发到飞书。默认复用 `OPENRELIX_FEISHU_BACKMERGE_WEBHOOK_URL`；只有发布通知需要发到另一个机器人或使用另一套签名密钥时，才额外配置 `OPENRELIX_FEISHU_RELEASE_WEBHOOK_URL` 和可选的 `OPENRELIX_FEISHU_RELEASE_SECRET`。

## Release Notes 清单

Release notes 应该对用户和贡献者清楚：

- 说明用户可感知变化。
- 说明升级或重新安装动作。
- 标出已知限制。
- 避免暴露内部任务、账号、路径、token、未脱敏日志。

## 发布后检查

发布后做：

- `npm view openrelix version` 或等价 metadata 检查。
- 新 `npx openrelix --version` 检查。
- GitHub release 页面可访问性检查。
- docs/site 链接和版本口径检查。

如果发现 published package 有隐私或 package-surface 问题，立即停止继续传播，准备修复版本并记录原因。
