# OpenRelix 开发者详细指南

> 语言版本：[English](developer-guide.en.md) | 简体中文

这份文档是 OpenRelix 的唯一开发者入口。它面向准备改 OpenRelix 的人：先理解边界，再跑一个不污染真实数据的本地闭环，然后按改动类型找到文件、测试和隐私红线。

如果只想理解产品形态，先看 [系统概览](system-overview.zh-CN.md)。如果要深入数据流和模块设计，再看 [技术方案](technical-solution.md)。本文是日常开发的主入口。

## 先记住一句话

OpenRelix 的仓库只放可复用逻辑；用户真实运行数据放在仓库外的 `state root`；Codex / Claude Code 这类 AI 工具继续管理自己的历史和上下文。

```text
AI 工具目录
  Codex / Claude Code 的 history、session、native memory、用户级 skill

OpenRelix 源码仓库
  可发布的 installer、skills、templates、scripts、docs、LaunchAgent 模板

state root，也就是 OpenRelix 应用根目录
  用户运行数据、窗口采集、复盘、registry、reports、runtime cache、日志
```

这三层不能混：

- 源码仓库只保存可开源、可发布、已脱敏的逻辑和文档。
- `state root` 保存用户本地运行状态，默认不进 git，也不进 npm 包。
- AI 工具目录归 host 自己所有，OpenRelix 只在需要时写一份受控摘要。
- 强规则放 `AGENTS.md` 或项目文档；memory registry 是召回层，不是唯一事实来源。

## 前 15 分钟怎么跑

### 1. 先看当前工作区

```bash
git status --short --branch
```

如果要做功能或文档改动，优先在独立 worktree 里做：

```bash
git worktree add -b codex/<task-name> ../openrelix-worktrees/<task-name> main
cd ../openrelix-worktrees/<task-name>
```

每个 clone 建议先启用本地开发检查：

```bash
./scripts/setup-dev.sh
```

### 2. 用临时 state root 跑一遍

这样可以验证主链路，又不会碰自己的真实 OpenRelix 数据：

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-dev.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
python3 -m json.tool "$STATE_DIR/reports/overview-data.json" >/dev/null
PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"
```

如果要验证从安装到面板的完整闭环：

```bash
scripts/smoke_temp_panel.sh --no-open
scripts/cleanup_smoke_temp.sh --dry-run
```

只有在任务明确需要检查当前机器真实状态时，才使用 `--seed-current-state`。不要把 seed 出来的内容复制进 docs、fixtures、tests、截图或 release artifact。

### 3. 看生成结果

```bash
open "$STATE_DIR/reports/panel.html"
```

初始面板没有真实数据是正常的。先确认页面能打开、空状态合理、`overview-data.json` 能通过 JSON 校验。

## 本地开发前置条件

| 依赖 | 用途 |
| --- | --- |
| macOS | installer 和后台自动化依赖用户级 `launchd` / LaunchAgent |
| Python 3.10+ | 主脚本、installer 辅助脚本和测试 |
| Node.js 18+ | `npx openrelix` bootstrapper 和 npm 打包检查 |
| zsh | `install/install.sh` 与 shell 脚本检查 |
| Codex CLI | 默认模型整理链路、Codex host adapter、模型 catalog |
| Claude Code CLI | 可选 host adapter 和可选 Claude 整理链路 |
| Xcode Command Line Tools | 仅构建轻量 macOS client 时需要 `swiftc` |

仓库没有运行时 npm 依赖，npm 包主要是 bootstrapper。Python 主链路尽量使用标准库；新增第三方依赖前先确认发布和安装边界。

## 仓库地图

| 路径 | 职责 | 什么时候改这里 |
| --- | --- | --- |
| `AGENTS.md` | 本仓库稳定规则 | 固化贡献、隐私、worktree、验证规则 |
| `.agents/skills/memory-review/` | repo-local 即时复盘 skill | 复盘流程、资产登记流程、skill 暴露方式变化 |
| `.agents/skills/openrelix-*-harness/` | 维护本仓库的开发 harness skills | 产品、技术、实现、验证工作流变化；这些 skill 不进入 npm 包 |
| `.agents/plugins/` | Codex plugin marketplace metadata | plugin 展示和入口声明变化 |
| `plugins/openrelix/` | 随包发布的 Codex plugin bundle | 公开 plugin surface 变化 |
| `install/` | 安装器、模板渲染、Codex 配置、shell path 配置 | 安装参数、profile、host home、命令入口变化 |
| `install/templates/` | 安装后写入用户环境的模板 | 全局 `openrelix` shell 命令、custom prompt 变化 |
| `ops/launchd/` | macOS LaunchAgent 模板 | 后台刷新、token live、夜间整理、更新检查变化 |
| `scripts/asset_runtime.py` | runtime 路径和配置中心 | state root、host home、默认配置、atomic write 变化 |
| `scripts/openrelix.py` | 用户本地 CLI | 新增或调整 `openrelix` 子命令 |
| `scripts/collect_codex_activity.py` | AI 工具活动采集 | Codex / Claude Code history、session、thread 读取变化 |
| `scripts/nightly_consolidate.py` | 模型整理、summary 选择、memory 写入 | memory 生成策略、模型调用、fallback、schema 变化 |
| `scripts/build_overview.py` | overview 兼容入口和面板生成 | overview 输出、面板 UI、legacy wrapper 变化 |
| `scripts/openrelix_overview/` | overview 内部模块化实现 | token、redaction、i18n、registry、contract 等 helper |
| `scripts/openrelix_index.py` | 可重建的 SQLite 本地索引 | memory/window 搜索索引 schema 或检索变化 |
| `scripts/token_live_server.py` | 本地 token live endpoint 和更新触发 | 面板 token 实时数据、本地服务控制变化 |
| `templates/` | 资产样例、复盘模板、nightly summary schema | registry 字段、模型输出 schema、复盘格式变化 |
| `tests/` | 单元测试和发布边界测试 | 行为变更、回归保护、隐私边界测试 |
| `docs/` | 公开文档和 HTML 页面源 | 对外说明、架构、隐私、验证、发布文档变化 |
| `local-docs/` | 本地给人看的 HTML 文档 | 由 `docs/*.md` 生成，不进 npm 包 |

## 运行时配置模型

开发新脚本时优先从 `scripts/asset_runtime.py` 读取路径和配置，不要在脚本里重新拼路径。核心入口是：

- `get_runtime_paths()`：返回 repo、state root、host home、reports、registry、runtime、LaunchAgent 等路径。
- `ensure_state_layout()`：创建 state root 标准目录和 JSONL 文件。
- `write_runtime_config()` / `load_runtime_config()`：读写 `runtime/config.json`。
- `atomic_write_text()` / `atomic_write_json()`：避免中途写坏 Markdown、JSON 或面板产物。

常见环境变量：

| 变量 | 作用 |
| --- | --- |
| `AI_ASSET_STATE_DIR` | 覆盖 state root |
| `CODEX_HOME` / `CODEX_BIN` | 覆盖 Codex home 和 binary |
| `OPENRELIX_CODEX_HOMES` / `OPENRELIX_EXTRA_CODEX_HOMES` | 额外 Codex home 列表 |
| `CLAUDE_HOME` / `CLAUDE_BIN` | 覆盖 Claude Code data home 和 binary |
| `AI_ASSET_LANGUAGE` | `zh` / `en`，控制本地输出和报告语言 |
| `AI_ASSET_MEMORY_MODE` | `integrated` / `local-only` / `off` |
| `OPENRELIX_ACTIVITY_SOURCE` | `history` / `app-server` / `auto` |
| `OPENRELIX_ACTIVITY_HOST` | `codex` / `claude` / `all` |
| `OPENRELIX_MODEL_CLI` | `codex` / `claude` |
| `OPENRELIX_CODEX_MODEL` | OpenRelix 内部 `codex exec` 模型 |
| `OPENRELIX_CLAUDE_MODEL` | OpenRelix 内部 `claude -p` 模型或别名 |

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

维护安装器时重点确认：

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

采集逻辑要保持“只读 host history、写 state root”的边界。raw windows 里保留来源、cwd、会话标识、输入和元信息，方便后续整理和排障。

### 夜间整理链路

```text
openrelix review / backfill
  -> collect_codex_activity.py
  -> nightly_consolidate.py
  -> sync_host_memory_summary.py
  -> openrelix_index.py rebuild
  -> build_overview.py
```

`scripts/nightly_consolidate.py` 负责模型整理、fallback summary、summary 选择、memory 写入和学习日志。模型失败时必须能生成 fallback summary，不应阻断后续 overview 生成。

### Overview 和面板链路

```text
scripts/build_overview.py
  -> reports/overview-data.json
  -> reports/overview.md
  -> reports/overview.csv
  -> reports/panel.html
```

`overview-data.json` 是面板和 Markdown 报告的稳定输入。renderer 不应再直接读 raw capture、JSONL 或 host memory 文件。修改可视化页面时，要同时验证真实 HTML。

### 本地索引链路

`scripts/openrelix_index.py` 构建 `runtime/openrelix-index.sqlite3`。它是可重建的本地索引，不是权威数据源。删除索引不能影响 `raw/`、`registry/` 或 `consolidated/` 源文件。

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

### 新增稳定 skill

1. 放到 `.agents/skills/<name>/SKILL.md`。
2. 写清楚触发条件、运行时边界、输出位置和隐私要求。
3. 如果需要全局安装，更新 installer 的 skill symlink 逻辑。
4. 更新 README 或 docs。
5. 用临时 state root 跑一遍 install 验证。

### 新增自动化脚本

1. 从 `scripts/asset_runtime.py` 获取路径，不要硬编码个人路径。
2. 输出写到 state root。
3. 需要后台运行时，先做手动 CLI，再考虑 launchd 模板。
4. 新增 state 文件时，确认 `.gitignore`、`.npmignore` 和 package `files` 不会带上个人数据。
5. 补测试或至少补 shell / Python 语法检查。

### 修改 host adapter

1. 先确认是 Codex、Claude Code，还是未来 host 的新 adapter。
2. host 原生目录只能通过 `CODEX_HOME`、`CLAUDE_HOME` 或新增可配置变量解析。
3. 采集 raw window 时保留 `ai_host`、cwd、thread/session id 等最小定位字段。
4. 不把未脱敏原始 transcript 写入 repo、docs、fixtures 或 release artifact。
5. 对外文档只写通用路径形态和可配置方式，不写个人机器路径。

### 修改 overview 或面板

1. 优先调整 `overview-data.json` 的 section contract，再让 renderer 消费。
2. 不让 renderer 重新读 runtime state。
3. 静态展示页和真实 panel 都要避免放入私人截图、路径或数据。
4. 修改 HTML 后用本地 HTTP 预览，确认导航、布局、链接和中英文都可用。

### 修改 Markdown 和本地 HTML 文档

`docs/*.md` 是源文件，`local-docs/reference/*.html` 是给人看的本地生成页面。每次改 Markdown 后运行：

```bash
python3 scripts/build_local_docs.py
python3 scripts/build_local_docs.py --check
```

边写边自动同步：

```bash
python3 scripts/build_local_docs.py --watch
```

## 任务卡模板

一个适合交给新人或协作者的小任务，应该能写成这样：

````markdown
## Title

### Scope
- Owner:
- Files or modules:
- Non-goals:

### User-visible outcome
- What changes:
- How to observe it:

### Data and privacy
- Reads from:
- Writes to:
- Must not include:

### Acceptance criteria
- [ ] Behavior:
- [ ] Docs:
- [ ] Tests:
- [ ] Privacy check:

### Verification
```bash
python3 scripts/check_personal_info.py
git diff --check
python3 -m unittest tests/<focused_test>.py
```
````

优先选择一个 owner、一个主要行为面的任务。如果一个任务同时碰 installer、overview、memory policy 和 docs，先拆分再分配。

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

## 隐私和安全边界

公开 repo 改动不能包含：

- 原始 host transcripts、session files、logs、runtime reports、私有面板截图或真实 registry rows。
- secrets、tokens、cookies、账号标识、私有组织名、内部 URL 或未脱敏的专有代码片段。
- 真实用户 home 绝对路径。
- 应留在外部 state root 的 Codex 或 Claude 站点化 memory mappings。
- 一次性本机经验被写成项目全局规则。

schema 示例见 [数据契约](data-contracts.zh-CN.md)，脱敏 fixture 形态见 `tests/fixtures/sample-state/`。

## 常见问题

### 我找不到数据写到哪里了

优先检查：

```bash
echo "$AI_ASSET_STATE_DIR"
python3 - <<'PY'
from scripts.asset_runtime import get_runtime_paths
print(get_runtime_paths().state_root)
PY
```

如果装了 `openrelix`，用：

```bash
openrelix paths
```

### panel 没有内容

常见原因：

- 这是新 state root，还没有资产、复盘或 AI 工具历史。
- Codex history 没有开启，采集不到窗口。
- 还没跑 `openrelix review` 或 `python3 scripts/build_overview.py`。

先运行：

```bash
openrelix refresh
```

或：

```bash
python3 scripts/build_overview.py
```

### 夜间任务没跑

检查方向：

- 是否使用了 `--enable-nightly`。
- 是否仍处于用户登录会话。
- 是否有 state root `log/` 下的错误日志。
- LaunchAgent plist 是否已经 bootstrap。

锁屏不是问题；退出登录是边界。

### 为什么默认只写 bounded summary

默认会接入 host native context，但只写压缩后的 bounded summary。完整 registry、reviews、raw windows 和 consolidated summaries 仍留在 state root；压缩策略会合并重复记忆，只让有效 `injection_policy=global_context/project_context` 的条目参与注入，low-priority 默认只留本地。

如果要严格隔离，不往 host context 注入摘要，用：

```bash
./install/install.sh --profile integrated --record-memory-only
```

## Review 清单

请求 review 前写清：

- 改了什么、为什么改。
- 涉及文件。
- 验证命令和结果。
- 跳过了哪些检查，以及原因。
- 剩余风险，尤其是隐私、package surface、host context 或 runtime state。

## 继续深入

按目标选择下一步：

- 想理解完整架构：读 [系统概览](system-overview.zh-CN.md) 和 [技术方案](technical-solution.md)。
- 想改 raw、registry、summary、overview 或 host context：读 [数据契约](data-contracts.zh-CN.md)。
- 想按改动类型选择命令：读 [验证矩阵](validation-matrix.zh-CN.md)。
- 想确认公开边界：读 [隐私与分发边界](privacy-and-distribution.zh-CN.md) 和 [隐私威胁模型](privacy-threat-model.zh-CN.md)。
- 想发布：读 [发布检查清单](release-checklist.zh-CN.md)。
