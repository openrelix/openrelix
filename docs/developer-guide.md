# OpenRelix 开发者指南

> 语言版本：[English](developer-guide.en.md) | 简体中文


本文基于 `package.json` 中标记为 `0.3.3` 的代码结构整理，面向需要维护、扩展或发布 OpenRelix 的开发者。它不替代 [技术方案](technical-solution.md)、[系统概览](system-overview.md)、[贡献者快速上手](contributor-onboarding.md)、[数据契约](data-contracts.md)、[验证矩阵](validation-matrix.md) 和 [隐私威胁模型](privacy-threat-model.md)，而是把“改代码时该从哪里下手、如何验证、哪些边界不能破坏”收束成一份可执行指南。

## 先理解项目边界

OpenRelix 是一个本地优先的 AI coding agent 个人资产系统。开发时先把它拆成三层看：

```text
AI host home
  Codex / Claude Code 的 history、session、native memory、用户级 skill

OpenRelix repo
  可开源的 installer、skills、templates、scripts、docs、LaunchAgent 模板

External state root
  用户运行数据、raw capture、reviews、registry、reports、runtime cache、logs
```

这三层的边界是维护本项目最重要的设计约束：

- 仓库只保存可复用、可发布、已脱敏的逻辑和文档。
- 用户状态默认在仓库外的 state root，不能提交 raw history、生成报告、日志或个人配置。
- AI host 原生文件归 host 自己所有，OpenRelix 只在默认 `integrated` 模式下写一份 bounded summary 作为上下文注入。
- 强约束规则放 `AGENTS.md` 或项目文档；memory registry 是召回层，不是唯一事实来源。

## 本地开发前置条件

当前预览版按 macOS-only 理解。开发和验证通常需要：

| 依赖 | 用途 |
| --- | --- |
| macOS | installer 和后台自动化依赖用户级 `launchd` / LaunchAgent |
| Python 3.10+ | 所有主脚本、installer 辅助脚本和测试 |
| Node.js 18+ | `npx openrelix` bootstrapper 和 npm 打包 |
| zsh | `install/install.sh` 与 shell 脚本检查 |
| Codex CLI | 默认模型整理链路、Codex host adapter、模型 catalog |
| Claude Code CLI | 可选 host adapter 和可选 `model_cli=claude` 整理链路 |
| Xcode Command Line Tools | 仅构建轻量 macOS client 时需要 `swiftc` |

仓库没有运行时 npm 依赖，npm 包只是 bootstrapper。Python 主链路尽量使用标准库，新增第三方依赖前要先确认发布和安装边界。

## 仓库地图

| 路径 | 职责 | 什么时候改这里 |
| --- | --- | --- |
| `AGENTS.md` | 维护本仓库的稳定规则 | 需要固化贡献、隐私、worktree、验证规则 |
| `.agents/skills/memory-review/` | repo-local 的即时复盘 skill | 复盘流程、资产登记流程、skill 暴露方式变化 |
| `.agents/skills/openrelix-*-harness/` | 维护 OpenRelix 本仓库的开发 harness skills | 产品设计、技术方案、实现闭环、测试或合规检查流程变化；这些 skill 不进入 npm 包 |
| `.agents/plugins/` | Codex plugin marketplace metadata | plugin 发布信息、入口声明、展示信息变化 |
| `plugins/openrelix/` | 随包发布的 Codex plugin bundle | plugin 形态和 packaged skill route 变化 |
| `install/` | 安装器、模板渲染、Codex 配置、shell path 配置 | 安装参数、profile、host home、命令入口变化 |
| `install/templates/` | 安装后写入用户环境的模板 | 全局 `openrelix` shell 命令、custom prompt 变化 |
| `ops/launchd/` | macOS LaunchAgent 模板 | 后台刷新、token live、夜间整理、更新检查变化 |
| `scripts/asset_runtime.py` | runtime 路径和配置中心 | state root、host home、默认配置、atomic write 变化 |
| `scripts/openrelix.py` | 用户本地 CLI | 新增或调整 `openrelix` 子命令 |
| `scripts/collect_codex_activity.py` | AI host 活动采集 | Codex / Claude Code history、session、thread 读取变化 |
| `scripts/nightly_consolidate.py` | 模型整理、summary 选择、memory 写入 | memory 生成策略、模型调用、fallback、schema 变化 |
| `scripts/build_overview.py` | overview 兼容入口和面板生成 | overview 输出、面板 UI、legacy wrapper 变化 |
| `scripts/openrelix_overview/` | overview 内部模块化实现 | token、redaction、i18n、registry、contract 等 focused helper |
| `scripts/openrelix_index.py` | rebuildable SQLite sidecar index | memory/window 搜索索引 schema 或检索变化 |
| `scripts/token_live_server.py` | 本地 token live endpoint 和更新触发 | 面板 token 实时数据、本地服务控制变化 |
| `templates/` | 资产样例、复盘模板、nightly summary schema | registry 字段、模型输出 schema、复盘格式变化 |
| `tests/` | 单元测试和发布边界测试 | 行为变更、回归保护、隐私边界测试 |
| `docs/` | 公开文档和展示页 | 对外说明、架构文档、学习路径、隐私说明变化 |

## 运行时配置模型

开发新脚本时优先从 `scripts/asset_runtime.py` 读取路径和配置，不要在脚本里重新拼路径。核心入口是：

- `get_runtime_paths()`：返回 repo、state root、host home、reports、registry、runtime、LaunchAgent 等路径。
- `ensure_state_layout()`：创建 state root 标准目录和 JSONL 文件。
- `write_runtime_config()` / `load_runtime_config()`：读写 `runtime/config.json`。
- `atomic_write_text()` / `atomic_write_json()`：避免中途写坏 markdown、JSON 或面板产物。

常见环境变量：

| 变量 | 作用 |
| --- | --- |
| `AI_ASSET_STATE_DIR` | 覆盖 state root |
| `CODEX_HOME` / `CODEX_BIN` | 覆盖 Codex home 和 binary |
| `OPENRELIX_CODEX_HOMES` / `OPENRELIX_EXTRA_CODEX_HOMES` | 额外 Codex home 列表，逗号分隔；运行中的 Codex desktop profile 会在 macOS 上自动探测 |
| `CLAUDE_HOME` / `CLAUDE_BIN` | 覆盖 Claude Code data home 和 binary；Claude CLI auth/config env 通过 `--claude-env-file` 显式传入 |
| `AI_ASSET_LANGUAGE` | `zh` / `en`，控制本地输出和报告语言 |
| `AI_ASSET_MEMORY_MODE` | `integrated` / `local-only` / `off` |
| `OPENRELIX_ACTIVITY_SOURCE` | `history` / `app-server` / `auto` |
| `OPENRELIX_ACTIVITY_HOST` | `codex` / `claude` / `all` |
| `OPENRELIX_MODEL_CLI` | `codex` / `claude` |
| `OPENRELIX_CODEX_MODEL` | OpenRelix 内部 `codex exec` 模型 |
| `OPENRELIX_CLAUDE_MODEL` | OpenRelix 内部 `claude -p` 模型或别名 |
| `OPENRELIX_CLAUDE_SETTINGS` / `OPENRELIX_CLAUDE_ENV_FILE` | 仓库外 Claude provider / env 配置 |

开发时推荐把 state root 指到临时目录，避免污染真实用户数据：

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-dev.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
python3 -m json.tool "$STATE_DIR/reports/overview-data.json" >/dev/null
```

## 核心链路

### 安装链路

```text
npx openrelix install
  -> install/npm-bin.js
  -> install/install.sh
  -> scripts/asset_runtime.py
  -> scripts/build_overview.py
  -> optional host config / global skill / shell command / LaunchAgent
```

`install/install.sh` 是公开安装主入口。`install/npm-bin.js` 只做 thin wrapper，把 npm 命令转给 installer 或 `scripts/openrelix.py`。维护安装器时重点确认：

- `minimal` profile 仍低侵入：初始化 state root、写 runtime config、生成第一份 overview。
- `integrated` profile 才安装全局 skill、custom prompt、shell 命令、macOS client、后台服务。
- 所有用户路径都必须可通过参数或环境变量覆盖。
- LaunchAgent 模板只写用户级路径，不假设系统级权限。
- 卸载命令默认保留本地记忆，删除 state root 必须来自显式选择。

### 活动采集链路

```text
scripts/collect_codex_activity.py
  -> Codex app-server or Codex history/session
  -> Claude Code projects/history
  -> raw/daily/<date>.json
  -> raw/windows/<date>/*.json
```

脚本名保留 `codex` 是兼容历史入口；0.3.0 代码已经用 `--activity-host codex|claude|all` 支持多 host。采集逻辑要保持“只读 host history、写 state root”的边界。

### 夜间整理链路

```text
openrelix review / backfill
  -> collect_codex_activity.py
  -> nightly_consolidate.py
  -> sync_host_memory_summary.py
  -> openrelix_index.py rebuild
  -> build_overview.py
```

`scripts/nightly_consolidate.py` 负责模型整理、fallback summary、summary 选择、memory 写入和学习日志。重要维护点：

- 默认 `model_cli=codex` 使用 `codex exec --ephemeral`：轻量/手动整理显式传 `--model <codex_model>`，深度 `final` 回溯默认 `final_codex_model=user-default`，省略 `--model` 并尊重用户 Codex 默认模型。
- `model_cli=claude` 使用 `claude -p`，仓库外 provider 设置通过 runtime config 或环境变量传入。
- 模型输出受 `templates/nightly-summary-schema.json` 约束。
- 个人记忆生成策略有不兼容变化时，需要提升 `PERSONAL_MEMORY_ALGORITHM_VERSION`，让迁移和 fingerprint 生效。
- 模型失败时必须能生成 fallback summary，不应阻断后续 overview 生成。

### Overview 和面板链路

```text
scripts/build_overview.py
  -> reports/overview-data.json
  -> reports/overview.md
  -> reports/overview.csv
  -> reports/panel.html
```

`scripts/build_overview.py` 仍是兼容入口，内部能力正在逐步拆到 `scripts/openrelix_overview/`。做 overview 重构时遵守 [build_overview 隔离重构方案](build-overview-isolation-plan.md)：

- 不一次性删除 facade 函数，现有测试和调用方可能 monkeypatch `build_overview` 的模块级变量。
- 纯 helper 可以迁到 `openrelix_overview/`，依赖 runtime globals 的函数先保留 wrapper。
- `overview-data.json` 是 renderer 的稳定输入，renderer 不应再直接读 JSONL、raw capture 或 host memory 文件。
- 普通 import 不应创建 runtime 文件、写 repo 或触发 subprocess。

### 本地索引链路

`scripts/openrelix_index.py` 构建 `runtime/openrelix-index.sqlite3`。它是 rebuildable sidecar，不是权威数据源。删除索引不能影响 `raw/`、`registry/` 或 `consolidated/` 源文件。

## 常见开发任务

### 新增 `openrelix` 子命令

1. 在 `scripts/openrelix.py` 的 `build_parser()` 里添加 parser、参数和帮助文案。
2. 添加对应 `command_<name>()` 或复用已有命令函数。
3. 在 `main()` 里分发新命令。
4. 如果 npm wrapper 也要暴露，更新 `install/npm-bin.js` 的 help 和 command switch。
5. 如果命令应进入安装后的 shell 入口，检查 `install/templates/bin/openrelix.tmpl`。
6. 补测试，至少覆盖 parser 暴露、npm help、关键行为或 dry-run。

### 新增 installer 参数

1. 更新 `install/install.sh` 的 usage、变量默认值、参数解析、导出环境和实际使用点。
2. 如果运行时需要持久化，更新 `scripts/asset_runtime.py` 的 normalization、runtime config 读写和默认值。
3. 如影响 `npx openrelix install`，同步 `install/npm-bin.js` help。
4. 如影响文档或用户行为，更新 README / docs。
5. 跑 `zsh -n install/install.sh scripts/*.sh` 和 installer 相关单测。

### 新增 registry 或 summary 字段

1. 优先更新 `templates/` 中的 schema 或示例。
2. 明确字段是否进入 state root、host summary、overview-data 或面板。
3. 如果字段会被注入 host context，确认 `injection_policy`、scope 和去重逻辑。
4. 更新 `scripts/build_codex_memory_summary.py`、`scripts/sync_host_memory_summary.py` 或 overview builder 的读取逻辑；项目记忆要验证统一 summary 中 global / project 两类预算是否分别生效。
5. 增加兼容旧 JSONL 的 fallback，不要让旧 state root 直接崩溃。

### 修改 host adapter

1. 先确认是 Codex、Claude Code，还是未来 host 的新 adapter。
2. host 原生目录只能通过 `CODEX_HOME`、`CLAUDE_HOME` 或新增可配置变量解析；不要把 Claude CLI auth/config 目录和 Claude data home 混用。
3. 采集 raw window 时保留 `ai_host`、cwd、thread/session id 等最小定位字段。
4. 不把未脱敏原始 transcript 写入 repo、docs、fixtures 或 release artifact。
5. 对外文档只写通用路径形态和可配置方式，不写个人机器路径。

### 修改面板 UI

1. 优先调整 `overview-data.json` 的 section contract，再让 renderer 消费。
2. 不让 renderer 重新读 runtime state。
3. 静态展示页和真实 panel 都要避免放入私人截图、路径或数据。
4. 修改 `docs/index.html` 或 `docs/product-showcase.html` 时，确认版本 meta 与 `package.json` 保持一致。

## 验证清单

日常小改至少运行：

```bash
python3 scripts/check_personal_info.py
git diff --check
```

Python 或 installer 改动追加：

```bash
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest discover -s tests
zsh -n install/install.sh scripts/*.sh
```

overview 或 state root 相关改动建议追加临时闭环：

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-overview.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
python3 -m json.tool "$STATE_DIR/reports/overview-data.json" >/dev/null
PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"
```

安装到面板的 smoke 测试：

```bash
scripts/smoke_temp_panel.sh --no-open
scripts/cleanup_smoke_temp.sh --dry-run
```

发布、installer、docs/site 或 package surface 改动还要运行：

```bash
npm pack --dry-run --json
```

检查失败时先修复真实问题。不要通过扩大忽略范围绕过隐私、路径或 package 白名单检查。

## 发布和 package surface

0.3.0 的 npm 包通过 `package.json` 的 `files` 白名单发布必要源码：

- README、许可证、贡献和安全文档。
- `.agents/skills/memory-review/` 和 plugin metadata。
- `docs/*.md`、静态展示页和图标。
- `install/`、`ops/`、`plugins/openrelix/`。
- 指定的 `scripts/*.py`、shell 脚本和 `scripts/openrelix_overview/*.py`。
- `templates/`。

改 package surface 时必须确认：

- 不把 state root、raw history、reports、runtime、logs 或 personal denylist 打进包。
- 不把个人路径、内部项目名、token、账号、私有日志、专有代码片段放进 README、docs、fixtures、截图或 changelog。
- `npx openrelix --version` 来源仍是 `package.json`。
- GitHub release tag 应与 `package.json` 版本一致，使用 `v<version>`。

## 隐私和安全边界

提交前用这个列表快速过一遍：

- 是否新增了形如用户 home 的绝对路径。
- 是否把 raw host history、session、transcript、日志或生成报告写入 repo。
- 是否把 token、Cookie、API key、账号、内部项目名、未脱敏报错全文写入公开文件。
- 是否让 installer 在默认路径外写入不可配置状态。
- 是否让 `integrated` 模式之外的路径意外写 host native memory。
- 是否把 one-off 本机经验写成了项目全局规则。

本项目的长期价值来自“可复用资产”和“隐私边界”同时成立。任何让用户状态混入开源源码的改动，都应优先回退设计，而不是继续堆保护逻辑。

## 推荐阅读顺序

新贡献者建议按这个顺序读：

1. [系统概览](system-overview.md)：先建立三层模型。
2. [技术方案](technical-solution.md)：理解完整数据流和模块职责。
3. [贡献者快速上手](contributor-onboarding.md)：按 10 分钟闭环和任务卡模板跑第一单。
4. [数据契约](data-contracts.md)：改 raw、registry、summary、overview 或 host context 前先看字段边界。
5. 本文：把架构映射到日常开发动作。
6. [学习指南](learning-guide.md)：按角色走一遍使用和验证路径。
7. [验证矩阵](validation-matrix.md)：按改动类型选择最小但足够的检查命令。
8. [隐私和分发边界](privacy-and-distribution.md) 与 [隐私威胁模型](privacy-threat-model.md)：提交前确认公开边界和 connector / host-context 风险。
9. [发布检查清单](release-checklist.md)：发布、npm、GitHub Release 或 package surface 变更时使用。
10. [build_overview 隔离重构方案](build-overview-isolation-plan.md)：处理 overview builder 时再深入。
