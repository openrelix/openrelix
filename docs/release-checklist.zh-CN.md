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

## GitHub Release

1. 确认本地分支已经合入目标 `main`。
2. 确认 validation 通过。
3. push branch 和 tag。
4. 使用配置好的 GitHub release workflow 或 release draft 流程。
5. 验证 npm publish 或 trusted-publishing 输出。
6. 在干净上下文中验证 `npx openrelix --version` 或 package metadata。

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
