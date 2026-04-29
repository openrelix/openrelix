# OpenRelix 技术方案

## 定位

`OpenRelix` 意为开源的个人记忆珍藏，是一套面向 AI coding agents / AI CLI 的本地优先个人资产系统。它解决的问题不是“再做一个聊天记录备份”，而是把重复出现的工作方法、检查清单、技能、模板和自动化沉淀成可复用资产，并在本地持续整理、可视化和回看。

GitHub 项目页：[openrelix/openrelix](https://github.com/openrelix/openrelix)。欢迎点星支持，便于更多人发现这个本地优先方案。

当前 `v0.1.0 预览版` 的公开交付方式是 macOS installer-first：

- 仓库保存可开源的能力层：skills、installer、templates、scripts、docs、launchd 模板。
- 用户运行数据默认保存在仓库外的 state root。
- 当前 v0.1.0 预览版首个适配器是 Codex CLI / Codex app-server；Codex 原生 memory 归 Codex 管，本项目默认只写一份 bounded summary 供上下文读取，完整个人记忆仍留在 state root。
- Linux / Windows 不作为 `v0.1.0 预览版` 对外承诺。

## 设计原则

1. **本地优先**：原始 AI host history、复盘、registry、夜间整理产物、日志和面板默认都留在本机 state root；当前 v0.1.0 预览版默认通过 Codex app-server 采集 threads，并在不可用时回退到 Codex history / sessions。
2. **源码和状态分离**：repo 是 source of truth，state root 是 runtime data，不把个人运行数据提交进仓库。
3. **安装器是主链路**：npm 只做 bootstrapper，真正安装逻辑仍在 `install/install.sh`。
4. **skill 不靠 hook 挂载**：repo 内启动时发现 `.agents/skills/`，全局可用时由 adapter / installer 把 canonical skill 暴露到对应 host 的用户级 skill root；当前 v0.1.0 预览版使用 Codex skill root。
5. **记忆分层**：稳定强规则放 `AGENTS.md` 或项目文档；本项目生成的 memory 是可检索 recall 层，不是唯一事实来源。
6. **隐私边界清晰**：不沉淀 secrets、token、Cookie、原始内部日志、用户数据或未脱敏的专有上下文。

## 总体架构

```text
AI coding agent / CLI host
  |
  | adapter reads/writes
  v
Host home
  - history / session records
  - optional native memories
  - optional user skills

Current v0.1.0 preview adapter: Codex CLI + CODEX_HOME

OpenRelix repo
  - .agents/skills/
  - install/
  - scripts/
  - templates/
  - ops/launchd/
  - docs/
  |
  | installer / openrelix / LaunchAgent
  v
External state root
  - raw/
  - registry/
  - reviews/
  - consolidated/
  - reports/
  - runtime/
  - log/
```

核心链路：

```text
collect_codex_activity.py
  -> raw/daily/<date>.json
  -> raw/windows/*.json

nightly_consolidate.py
  -> consolidated/daily/<date>/summary.json
  -> consolidated/daily/<date>/summary.md
  -> registry/memory_items.jsonl

memory-review skill
  -> reviews/YYYY/*.md
  -> registry/assets.jsonl
  -> registry/usage_events.jsonl

build_overview.py
  -> reports/overview-data.json
  -> reports/overview.md
  -> reports/overview.csv
  -> reports/panel.html
```

## 分层说明

### 1. Host adapter 层

AI host 自己的用户级目录、history、session 和 native memory 由各 host adapter 负责映射。当前 v0.1.0 预览版 Codex 适配器由 `CODEX_HOME` 决定用户级目录，默认是 `~/.codex`；默认 `auto` 先通过 `codex app-server` 读取 threads，失败时再读取其中的 `history.jsonl` 和 `sessions/**/*.jsonl` 来识别当天窗口、问题和最终结论。

默认安装开启本地个人记忆，并在当前 Codex 适配器下把压缩后的 bounded summary 写入 `CODEX_HOME`，让 host native context 能读取轻量摘要。完整结构化记录仍保留在 state root；需要严格隔离时使用 `--record-memory-only` 或 `--no-memory-summary`。

压缩策略保持轻量：同签名记忆跨天归并，durable / session 优先进上下文，low-priority 默认只留本地；默认 token budget 是 target 6.7K、warn 7.4K、max 8K，不把原始窗口明细塞进 host context。

### 2. Repo 源码层

仓库保存可复用能力：

- `.agents/skills/memory-review/`：立即复盘入口。
- `install/`：安装器、模板渲染、host adapter 用户配置和 shell path 配置；当前 v0.1.0 预览版实现 Codex 配置。
- `scripts/`：运行时路径、采集、整理、概览生成、token live server 和 `openrelix` CLI。
- `templates/`：资产样例、任务复盘模板、夜间整理 JSON schema。
- `ops/launchd/`：macOS LaunchAgent 模板。
- `docs/`：设计、安装、隐私、指标和学习材料。

### 3. Runtime state 层

state root 默认在：

```text
~/Library/Application Support/openrelix
```

可通过 `AI_ASSET_STATE_DIR` 或 `./install/install.sh --state-dir ...` 覆盖。

state root 的主要目录：

- `raw/`：从 AI host history 和 session 采集出的日维度、窗口维度原始结构化数据；当前 v0.1.0 预览版来源是 Codex。
- `registry/`：资产注册表、复用事件、nightly memory items 和整理质量日志。
- `reviews/`：脱敏任务复盘。
- `consolidated/`：夜间或手动整理后的 summary。
- `reports/`：overview、CSV 和 HTML panel。
- `runtime/`：运行时缓存、host adapter 运行目录、nightly isolated Codex home、token cache。
- `log/`：LaunchAgent 和后台任务日志。

## 核心模块

### `scripts/asset_runtime.py`

运行时路径和配置的中心模块。

它负责：

- 解析 state root、host home、host binary、user skill root 和 LaunchAgent 目录；当前 v0.1.0 预览版对应 `CODEX_HOME` 和 Codex binary。
- 管理 `language` 和 `memory_mode`。
- 创建 state root 下的标准目录和 JSONL 文件。
- 提供 atomic write，避免中途写坏 JSON / markdown / panel。
- 兼容旧 slug 的 state root，并支持 legacy repo-local state 迁移。

新增脚本时应优先复用这里的 `get_runtime_paths()` 和 `ensure_state_layout()`，不要在脚本里重新散落路径规则。

### `install/install.sh`

公开安装主入口。

两种 profile：

- `minimal`：初始化 state root，生成第一份 overview / panel，并按默认 `integrated` 同步 bounded summary。不会安装全局命令、shell rc 或 LaunchAgent。
- `integrated`：在 minimal 基础上安装全局 skill、custom prompt、`openrelix` 命令、bounded history 配置和后台服务。

重要开关：

- `--language zh|en`
- `--memory-mode integrated|local-only|off`
- `--record-memory-only`
- `--use-integrated`
- `--disable-personal-memory`
- `--enable-background-services`
- `--enable-nightly`
- `--nightly-organize-time HH:MM`
- `--nightly-finalize-time HH:MM`
- `--keep-awake=during-job`

安装器只应做可重复执行的配置动作，避免把一次性的本机状态写死进仓库。

### `scripts/collect_codex_activity.py`

采集 Codex 当天活动。

输入：

- `CODEX_HOME/history.jsonl`
- `CODEX_HOME/sessions/**/*.jsonl`

输出：

- `raw/daily/<date>.json`
- `raw/windows/*.json`

它会把同一天的用户 prompt、窗口 metadata、最终 conclusion 汇总起来，并过滤明显的 review-like conclusion，避免把 reviewer 输出本身当作普通业务结论反复沉淀。

### `scripts/nightly_consolidate.py`

夜间整理核心。

输入：

- `raw/daily/<date>.json`
- 近期 `registry/memory_items.jsonl`
- 近期 `consolidated/daily/*/summary.json`
- 可选的 `--learn-window-days`

模型调用方式：

- 使用 `codex exec --ephemeral`
- 使用隔离的 nightly `CODEX_HOME`
- 通过 `templates/nightly-summary-schema.json` 约束输出 JSON
- 在 prompt 前加安全前缀，声明这是纯整理任务，不允许读额外文件、调用 shell、web、MCP 或 patch

输出：

- `consolidated/daily/<date>/summary.json`
- `consolidated/daily/<date>/summary.md`
- `consolidated/daily/<date>/runs/*`
- `registry/memory_items.jsonl`
- `registry/nightly_learning_journal.jsonl`

当模型整理失败时，会生成 fallback summary；当新结果质量不如已有结果时，会保留已有 summary，并记录 selection decision。

### `scripts/build_overview.py`

本地可视化生成器。

输入：

- `registry/assets.jsonl`
- `registry/usage_events.jsonl`
- `registry/memory_items.jsonl`
- `reviews/`
- `raw/daily/`
- `consolidated/daily/`
- 可读的 Codex native memory summary 和 `MEMORY.md`

输出：

- `reports/overview-data.json`
- `reports/overview.md`
- `reports/overview.csv`
- `reports/panel.html`

它会聚合资产数量、复用事件、估算节省、项目上下文、nightly memory、Codex native memory 对照和 token 使用趋势。

### `scripts/openrelix.py`

面向用户的本地 CLI。

常用命令：

```bash
openrelix review
openrelix review --date "$(date +%F)" --learn-window-days 7
openrelix backfill --from 2026-04-24 --to 2026-04-27 --learn-window-days 7
openrelix backfill --dates 2026-04-21,2026-04-23,2026-04-24 --learn-window-days 7
openrelix core
openrelix refresh
openrelix refresh --learn-memory --learn-window-days 7
./install/install.sh --profile integrated --enable-learning-refresh
openrelix mode
openrelix mode local-only
openrelix open panel
openrelix paths
```

`openrelix` 是对底层脚本的稳定入口，适合文档、LaunchAgent、手动调试和日常使用。安装后的记忆模式切换走 `openrelix mode`，不需要重复执行安装器。

### `.agents/skills/memory-review/`

立即复盘 skill。

触发后做四件事：

1. 在 state root 的 `reviews/YYYY/` 下写入脱敏任务复盘。
2. 如有长期复用价值，更新 `registry/assets.jsonl`。
3. 如有资产实际帮上忙，追加 `registry/usage_events.jsonl`。
4. 运行 `scripts/build_overview.py` 刷新 overview 和 panel。

custom prompt 是兼容层，canonical workflow 仍以 skill 为主。

### `ops/launchd/`

macOS 后台自动化模板。

当前包含：

- `overview-refresh`：`RunAtLoad`，并每 1800 秒刷新一次 overview。
- `token-live`：`RunAtLoad` + `KeepAlive`，提供本地 token live endpoint。
- `nightly-organize`：默认每天 23:00 生成当日整理预览，可通过 `--nightly-organize-time HH:MM` 调整。
- `nightly-finalize-previous-day`：默认每天 00:10 回补前一天终版整理，可通过 `--nightly-finalize-time HH:MM` 调整。

`--keep-awake=during-job` 只在夜间任务运行期间使用 `caffeinate`，不是永久改变系统睡眠策略。

## 数据模型

### Asset

资产是长期可复用的条目，落在 `registry/assets.jsonl`。

典型字段：

- `id`
- `title`
- `type`
- `domain`
- `scope`
- `status`
- `created_at`
- `updated_at`
- `source_task`
- `reuse_count`
- `minutes_saved_total`
- `value_note`
- `artifact_paths`
- `tags`
- `notes`

示例见 `templates/asset-entry-example.json`。

### Usage Event

复用事件落在 `registry/usage_events.jsonl`，用于证明某个资产在真实任务里发挥过作用。

常见字段：

- `date`
- `asset_id`
- `task`
- `minutes_saved`
- `note`

面板会把 usage event 作为强证据，同时结合资产内容自动估算 value score 和 saved time。估算值用于排序和趋势观察，不是精确测速。

### Task Review

任务复盘落在 `reviews/YYYY/*.md`。

它记录脱敏后的：

- 任务背景
- 最终结果
- 可复用价值
- 验证路径
- 风险和后续
- 资产动作

模板见 `templates/task-review-template.md`。

### Nightly Memory Item

夜间整理记忆落在 `registry/memory_items.jsonl`。

核心字段：

- `date`
- `language`
- `source`
- `bucket`
- `title`
- `memory_type`
- `priority`
- `value_note`
- `source_window_ids`
- `keywords`

`bucket` 分为：

- `durable`：长期可复用记忆
- `session`：短期工作记忆
- `low_priority`：保留但低优先级记忆

## 运行链路

### 最小安装

```text
install/install.sh
  -> resolve runtime paths
  -> ensure state layout
  -> write runtime config
  -> build first overview
```

适合先体验，不写 shell rc，不注册 LaunchAgent，不安装全局 skill。

### 完整安装

```text
install/install.sh --profile integrated
  -> minimal setup
  -> configure bounded host history for the current adapter
  -> optionally configure host-native memory context
  -> symlink repo skill to the current adapter's user skill root
  -> install custom prompt fallback
  -> install global openrelix command
  -> optionally render/bootstrap LaunchAgents
  -> with --enable-learning-refresh, make overview-refresh call the current Codex adapter every 30 minutes
```

### 手动整理

```text
openrelix review --date <date> --learn-window-days N
  -> auto-backfill missing or non-final daily reports in the learning window
     without recursively expanding each prerequisite day into another learning window
  -> collect_codex_activity.py
  -> nightly_consolidate.py
  -> build_overview.py
  -> print summary
```

### 多日回溯

```text
openrelix backfill --from <start-date> --to <end-date> --learn-window-days N
  -> for each date:
     -> collect_codex_activity.py
     -> nightly_consolidate.py
     -> build_overview.py
```

也可以使用 `--dates` 回溯不连续日期，避免把中间没有活动的日期也生成空 summary：

```bash
openrelix backfill --dates 2026-04-21,2026-04-23,2026-04-24 --learn-window-days 7
```

回溯不是完全离线：采集阶段是本地脚本读取当前 host 的 history 和 session JSONL；当前 v0.1.0 预览版 Codex 适配器会通过 `codex exec --ephemeral` 调用模型生成结构化 summary；最后再由本地脚本重建 overview / panel。

### 后台整理

```text
LaunchAgent
  -> nightly_organize_today.sh or nightly_finalize_previous_day.sh
  -> nightly_pipeline.sh
  -> collect
  -> consolidate
  -> refresh overview
```

锁屏不影响用户级 LaunchAgent 的正常运行；退出登录后用户级 LaunchAgent 不再执行。

## 发布方案

当前开源发布建议按 installer-first 讲清楚：

- GitHub 仓库提供源码、模板、skills、docs 和 launchd 模板。
- npm 包提供 `npx openrelix install` 入口。
- 项目定位是 AI-agent-first；当前 v0.1.0 预览版安装器先交付 Codex CLI / Codex app-server 适配器。
- npm 通过 `files` 白名单携带必要源码，不携带个人运行数据。
- 发布前用 `npm pack --dry-run` 检查包内容。
- Codex plugin 作为已打包的 skill route 随仓库/包发布；完整本地集成仍由 installer 负责。

## 开发与验证

常用本地检查：

```bash
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest discover -s tests
zsh -n install/install.sh scripts/*.sh
npm pack --dry-run
```

验证安装到面板的临时闭环：

```bash
scripts/smoke_temp_panel.sh --no-open
scripts/cleanup_smoke_temp.sh --dry-run
```

文档或安装器改动应额外检查：

- 是否引入了硬编码个人绝对路径。
- 是否把 state root 内容加入 npm 包。
- 是否会默认改写 host-native memory；当前 v0.1.0 预览版重点检查 Codex native memory。
- 是否破坏 minimal profile 的低侵入边界。
- 是否让 macOS-only 能力被误读成跨平台承诺。

## 当前边界和后续方向

当前不承诺：

- Linux / Windows 一键安装。
- Codex plugin 单独替代 installer 成为完整本地集成入口。
- 除 Codex CLI 外的 host adapter。
- 云端 memory 同步。
- 对 token 使用量的强依赖。`ccusage` 不可用时，面板应降级展示已有快照。

后续可以演进：

- 将 launchd 层抽象为跨平台 scheduler。
- 补充 English-first docs。
- 收口 plugin metadata、截图和 policy URL。
- 引入更严格的 registry schema 校验。
- 给 sample state root 提供脱敏 demo 数据，方便开源读者不用接入真实 Codex history 也能看 panel。
