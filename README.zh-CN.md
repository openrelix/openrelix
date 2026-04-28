# OpenRelix™

[English](https://github.com/openrelix/openrelix/blob/main/README.md) | 简体中文

面向 AI coding agents 的开源个人记忆珍藏系统，当前发布为 v0.1 预览版。

OpenRelix™ 是一套本地优先的 AI 个人资产层。它把已经完成的 agent 工作沉淀成可复用的任务复盘、技能、模板、自动化、受限记忆摘要和私有面板，而不是让有价值的经验散落在历史聊天里。

OpenRelix 的名字含义是开源的个人记忆珍藏：可复用工作在本地有序保存，只把脱敏、压缩、受限的摘要分享给当前 AI host。

这个项目不绑定单一 AI host。当前 v0.1.0 预览版先提供 Codex CLI 适配器，因为 Codex 已经暴露了 history、session、skill 和 memory 等足够落地的本地表面。后续 Claude Code、Gemini CLI 或其他 AI CLI / agent host 可以通过 adapter 层接入同一套本地资产模型。

GitHub 项目页：[openrelix/openrelix](https://github.com/openrelix/openrelix)。如果这个项目对你的工作流有帮助，欢迎点星支持。

## 仓库里有什么

- `AGENTS.md`：维护本系统本身的仓库级说明。
- `.agents/skills/`：可复用 agent 工作流的 canonical repo-local skills。
- `.agents/plugins/`：草案插件市场元数据，不是 v0.1.0 预览版的安装主链路。
- `install/`：一键安装器和用户配置辅助脚本。
- `ops/launchd/`：macOS LaunchAgent 模板。
- `plugins/`：为后续插件打包保留的草案 bundle。
- `scripts/`：采集、夜间整理、overview 生成、token live server 和 `openrelix` CLI。
- `templates/`：任务复盘 schema 和资产条目模板。
- `docs/`：运行模型、技术方案、学习指南、隐私边界和指标说明。

## 文档

- [技术方案](docs/technical-solution.md)：架构、数据流、模块职责、运行时状态和发布边界。
- [学习指南](docs/learning-guide.md)：给使用者、贡献者和维护者的阅读与验证路径。
- [开源安装说明与项目说明](docs/open-source-install-and-project-overview.md)：当前 macOS v0.1.0 预览版的中文安装指南和项目解释。
- [产品展示页](docs/product-showcase.html)：可部署到 GitHub Pages 的中英双语展示页和脱敏面板预览。
- [系统概览](docs/system-overview.md)：AI host、repo source、runtime state 和本地 memory 的分层模型。
- [隐私和分发边界](docs/privacy-and-distribution.md)：哪些内容属于公开仓库，哪些必须留在本地。
- [商标申请包](docs/trademark-filing-kit.md)：开源品牌边界和商标申请检查清单。
- [中美商标同日申请行动表](docs/trademark-dual-filing-action-sheet.md)：`OPENRELIX` 文字商标的 U.S. / China filing packet。
- [中国商标申请包](docs/china-chinese-trademark-filing-kit.md)：`OPENRELIX` 文字商标的中国申请资料。
- [指标字典](docs/metric-dictionary.md)：报告和面板里的统计口径。

## 公开展示页

静态展示页已经准备好用于 GitHub Pages。把 Pages source 设置为 `main` branch 和 `/docs` folder 后，公开入口是：

```text
https://openrelix.github.io/openrelix/
```

## 许可证和商标

源码使用 [MIT License](LICENSE) 开源。项目名称、Logo、包名和其他来源识别标识由 [Trademark Policy](TRADEMARKS.md) 单独约束。

OpenRelix™ 和 openrelix™ 是项目维护者的商标。MIT License 授权的是源码版权使用权，不授予商标权。

## 当前适配器支持

v0.1.0 预览版仅支持 macOS。公开安装路径假设：

- macOS，支持用户级 `launchd` / `LaunchAgent`
- Node.js 18+，带 `npm` / `npx`
- `zsh`
- Python 3.10+
- Codex CLI，并且 `CODEX_HOME` 可写，默认是 `~/.codex`

Linux 和 Windows 是后续工作。部分底层 Python 脚本已经把路径做成可配置，但当前公开 installer 和后台自动化应按 macOS-only 理解。

首个公开 adapter 面向 Codex CLI。v0.1.0 预览版的一些能力依赖 Codex 特定表面，包括 `CODEX_HOME`、Codex history/session 文件、Codex native memories、Codex skills 和 Codex custom prompts。产品方向仍然是 AI-agent-first：未来 host 只需要把自己的 history、skill、memory 和 command 表面映射到同一本地资产模型里。

实验性的 Codex app-server 活动源可用于本地测试。它通过 `codex app-server` 读取 Codex threads，并映射回现有 history/session collector 使用的 raw window 格式。它默认关闭，稳定默认路径仍然读取 `CODEX_HOME/history.jsonl` 和 `CODEX_HOME/sessions/**/*.jsonl`。

```bash
npx openrelix install --profile integrated --enable-learning-refresh --read-codex-app
npx openrelix install --profile integrated --activity-source auto
python3 scripts/collect_codex_activity.py --date "$(date +%F)" --activity-source app-server
OPENRELIX_ACTIVITY_SOURCE=app-server openrelix review --date "$(date +%F)"
```

## 依赖说明

只要机器满足上面的前置条件，一行 npm 安装不需要额外项目初始化步骤：

- 不需要 `pip install ...`。随包发布的 Python 脚本只使用标准库。
- 不需要 `npm install`。npm 包只是 bootstrapper，不声明运行时 npm 依赖。
- 不需要手动配置 LaunchAgent。启用后台服务时，installer 会渲染并 bootstrap LaunchAgents。
- Token 用量指标是可选增强。面板按需调用 `npx -y @ccusage/codex@latest` 获取；如果该命令不可用或离线，面板其他部分仍可使用，Token 卡片显示 fallback 或缓存状态。

如果 macOS 上缺少 Python 3.10+，先安装 Python，再重新运行 installer：

```bash
brew install python
npx openrelix install
```

## 什么不需要放进仓库

新安装应把用户状态保存在仓库外。installer 会创建或复用一个 state root，里面包含：

- `registry/`：资产注册表、复用事件和夜间 memory items。
- `reviews/`：脱敏任务复盘。
- `raw/`：按天和窗口分组的 AI host activity；v0.1.0 预览版来源是 Codex。
- `consolidated/`：夜间整理输出。
- `reports/`：生成的 overview markdown、JSON、CSV 和 HTML panel。
- `runtime/`：token cache 和 adapter runtime，例如隔离的 nightly Codex home。
- `log/`：后台任务日志。

默认 state root 是：

```text
~/Library/Application Support/openrelix
```

你可以通过 `AI_ASSET_STATE_DIR` 或 `./install/install.sh --state-dir ...` 覆盖。为兼容改名后的历史安装，如果没有显式设置 state root，且新的 `openrelix` state root 不存在，运行时可复用旧 state root。

## 快速开始

以下命令面向 macOS v0.1.0 预览版。

一行 `npx` 安装：

```bash
npx openrelix install
```

交互式终端会提示选择 `中文 (zh)` 或 `English (en)`。非交互安装默认 `zh`；自动化场景建议显式传 `--language`。

英文运行语言安装：

```bash
npx openrelix install --language en
```

推荐完整安装：

```bash
npx openrelix install --profile integrated --enable-learning-refresh --enable-nightly --keep-awake=during-job
```

最小安装：

```bash
./install/install.sh
```

最小安装会初始化 state root，生成第一份 overview，开启当前 Codex adapter 的 memories/history，并同步一份 bounded memory summary 到 `CODEX_HOME`。它不会安装 shell 命令，不改 shell rc，也不 bootstrap LaunchAgents。需要只在本系统本地记录、不注入 host context 时，使用 `--record-memory-only`。

installer 会把运行语言和 memory mode 写入 state root 下的 `runtime/config.json`。支持的语言是 `zh` 和 `en`；语言会影响终端输出、overview 文件、夜间 summary prompt、fallback summary、即时 task review、asset / usage event 的展示字段，以及本地 consolidation pipeline 写出的结构化 memory items。稳定 enum keys 保持 canonical，展示层再按语言格式化。

```bash
./install/install.sh --language zh
./install/install.sh --language en
```

Memory 默认开启。当前 Codex adapter 的默认模式是 `integrated`：系统把可复用记忆记录到 active state root，开启 Codex memories/history，并把 bounded summary 同步进 host-native context。需要严格本地记录时用 `--record-memory-only`，需要关闭本系统本地 memory 写入时用 `--disable-personal-memory`。

```bash
./install/install.sh --profile integrated --record-memory-only
./install/install.sh --profile integrated --disable-personal-memory
```

推荐的 integrated profile 会安装全局 skill symlink、bounded history config、`openrelix` shell command、30 分钟自动学习刷新、夜间整理和任务执行期间防睡眠：

```bash
./install/install.sh --profile integrated --enable-learning-refresh --enable-nightly --keep-awake=during-job
```

这个 profile 会：

1. 初始化 active state root 并生成第一份 overview。
2. 默认开启 bounded history 和 Codex native memory context。
3. 把 repo 提供的 `memory-review` skill symlink 到 `~/.codex/skills/`。
4. 把 repo 提供的 custom prompt 安装到 `~/.codex/prompts/memory-review.md` 作为兼容 fallback。
5. 安装全局 `openrelix` shell command，并确保用户选择的 bin 目录在 `PATH` 中。
6. 渲染并 bootstrap macOS LaunchAgents：
   - 每 30 分钟刷新 overview；如果开启 `--enable-learning-refresh`，会调用当前 Codex adapter 并从 7 天窗口学习
   - token live server
   - 每天 `23:00` 生成当日预览
   - 每天 `00:10` 生成前一日终版

在 active AI coding agent 里需要立即做任务复盘时，当前 Codex adapter 暴露的 skill 入口是：

```text
/memory-review
```

custom prompt 兼容入口是：

```text
/prompts:memory-review
```

安装完成后，首个推荐动作是打开本地面板：

```bash
openrelix open panel
```

常用命令：

```bash
openrelix open panel
openrelix core
openrelix mode
openrelix review
```

如果所选 bin 目录还不在 `PATH` 中，installer 会向当前 shell rc 文件追加一个受管理的 `PATH` block，并打印当前 shell 可直接执行的一行 `export PATH=...`。

## npm 分发

npm 包只是 bootstrapper。它随包带上 installer、skills、templates、scripts 和 docs，然后从 npm package cache 运行 `install/install.sh`。安装器仍然是唯一行为真源。

发布前检查包内容：

```bash
npm pack --dry-run
```

登录后发布公开预览版：

```bash
npm login
npm publish --access public
```

## 公开发布检查清单

公开仓库和包之前，保持证据链一致：

- README、showcase、release notes 和 npm page 的首个可见品牌使用 `OpenRelix™`。
- CLI mark 使用 `openrelix™`，npm package name 使用 `openrelix`。
- 创建 GitHub release 和 tag：`v0.1.0`。
- GitHub Pages 从 `main` branch 和 `/docs` folder 部署。
- 发布后保存 GitHub README、npm package page、release page 和 GitHub Pages showcase 截图。
- 除非相关司法辖区已经核准注册，不要使用 `OpenRelix®` 或 `openrelix®`。

## 运行时命令

以下命令需要通过 `--profile integrated` 或 `--install-global-command` 安装 `openrelix` shell entrypoint。

刷新 overview snapshot：

```bash
openrelix refresh
```

刷新并立刻从今天窗口中提炼 memory，同时参考最近 7 天上下文：

```bash
openrelix refresh --learn-memory --learn-window-days 7
```

打开生成的面板：

```bash
openrelix open panel
```

在终端打印核心指标：

```bash
openrelix core
```

查看或切换 memory mode：

```bash
openrelix mode
openrelix mode integrated
openrelix mode local-only
openrelix mode off
```

立即运行今天的 review pipeline：

```bash
openrelix review
```

手动 review 并先回补最近 7 天缺失或非 final 的日报：

```bash
openrelix review --date "$(date +%F)" --learn-window-days 7
```

回填连续多日：

```bash
openrelix backfill --from 2026-04-24 --to 2026-04-27 --learn-window-days 7
```

回填非连续日期：

```bash
openrelix backfill --dates 2026-04-21,2026-04-23,2026-04-24 --learn-window-days 7
```

查看或更新上下文摘要 token budget：

```bash
openrelix config
openrelix config --memory-summary-max-tokens 8000
```

`memory_summary_max_tokens` 默认 5000，支持 2000 到 20000。target 和 warning budgets 会自动从 max 派生。更新后默认刷新 summary、overview 和 panel；只想持久化配置时加 `--no-refresh`。

底层 fallback：

```bash
python3 scripts/build_overview.py
python3 scripts/migrate_legacy_state.py
```

## 技能如何加载

- 当 active AI host 支持 repo-local skill discovery 时，`.agents/skills/` 下的 skills 会自动被发现。v0.1.0 预览版 adapter 面向 Codex discovery。
- 如果希望同一个 skill 在任意仓库都可用，需要安装到 active host 的用户级 skill root。v0.1.0 预览版通过 `--profile integrated` 或 `--install-global-skills` 把 Codex symlink 安装到用户级 skill root。
- 本仓库不依赖 hooks 实现全局 skill discovery。Hooks 只是可选生命周期自动化；skill 可用性来自 repo-local discovery 或 user-level installation。

## Plugin 状态

`plugins/` 目录是当前 Codex plugin route 的草案包装层。它保留在仓库里，是为了后续 host-specific packages 可以复用同一套 canonical skills。v0.1.0 预览版应按 installer-first 发布和说明。repo marketplace entry 会保持 not available，直到 public plugin metadata、截图和 policy URLs 准备好。

## 隐私边界

- 只沉淀脱敏且长期有价值的知识。
- 不把原始 Codex history、runtime cache、logs、真实 registry、真实 reviews、token、账号、Cookie 或内部任务上下文提交到公开仓库。
- 默认只同步 bounded summary 到当前 host context；完整 registry、reviews、raw windows 和 consolidated summaries 留在 state root。
- 公开 issue 中请使用脱敏复现步骤和最小示例，不要贴原始本地数据。

## 许可证

本项目使用 MIT License。

Copyright (c) 2026 [kk_kais](https://www.npmjs.com/~kk_kais)。

MIT License 允许个人免费使用、复制、修改、合并、发布、分发和再授权，只要在副本或软件主要部分中保留版权声明和许可文本。完整条款见 [LICENSE](LICENSE)。
