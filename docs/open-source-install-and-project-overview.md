# OpenRelix™ 开源安装说明与项目说明

## 一句话说明

`OpenRelix™` 意为开源的个人记忆珍藏，是一套面向 AI coding agents / AI CLI 的本地优先资产层，当前公开版本以 `package.json` 为准。它不绑定某一个 AI host；当前首个公开适配器是 Codex CLI / Codex app-server，因为 Codex 已经提供可落地的 thread、history、session、skill 和 memory 表面。

GitHub 项目页：[openrelix/openrelix](https://github.com/openrelix/openrelix)。如果这个项目对你有帮助，欢迎点星支持。

它把可复用的方法、技能、模板和自动化留在仓库里，把用户自己的运行数据留在仓库外，从而同时满足下面两件事：

- 仓库本身适合开源、审阅和复用
- 用户本地又可以长期沉淀自己的资产、复盘、记忆整理和可视化面板

---

## 当前支持范围

当前预览版按 **macOS-only** 发布。

当前公开安装路径依赖：

- macOS 用户级 `launchd` / `LaunchAgent`
- Node.js 18+，并带有 `npm` / `npx`
- `zsh`
- Python 3.10+
- 当前预览版适配器所需的 Codex CLI，以及可写的 `CODEX_HOME`，默认是 `~/.codex`

Linux / Windows 暂时作为后续目标，不在当前预览版对外承诺里。底层 Python 脚本仍尽量保留可配置路径，但一键安装、后台刷新、夜间整理和防睡眠策略都先按 macOS 收口。

当前预览版的公开安装器先支持 Codex CLI 适配器。很多已落地能力依赖 Codex 专属表面，包括 `CODEX_HOME`、Codex history / session 文件、Codex native memory、Codex skills 和 Codex custom prompts。产品定位仍是 AI-agent-first；其他 CLI host 后续可以通过 adapter 层接入同一套本地资产模型，但不属于当前公开安装承诺。

---

## v0.1.0 预览版发布说明

`v0.1.0 预览版` 是 OpenRelix 的首个公开预览版本，目标是先把“本地个人 AI 资产系统”的核心闭环跑通：安装、采集、整理、复盘、记忆登记、面板可视化和本地自动化。

当前版本已经对外承诺的范围：

- **平台**：macOS。
- **安装方式**：`npx openrelix install` 或源码目录下 `./install/install.sh`。
- **当前适配项目**：Codex CLI / Codex app-server。默认 `auto` 会先尝试 `codex app-server` 读取 Codex threads，失败时回退到 Codex CLI 的 `history.jsonl` 和 `sessions/**/*.jsonl`，并把 repo 内维护的 skill / prompt / shell 入口安装到 Codex 用户环境。
- **诊断能力**：`openrelix doctor --app-server-check` 可以实际启动一次 app-server 协议探测，用来确认 Codex 客户端采集链路。

预期后续支持的项目 / host：

| 项目 / host | 预期状态 | 说明 |
| --- | --- | --- |
| Codex CLI | 当前支持 | 当前预览版的稳定回退采集面。 |
| Codex app-server / Codex 应用线程 | 当前支持 | 默认 `auto` 优先使用；失败时回退到 Codex CLI history/session。 |
| Claude Code | 计划适配 | 目标是映射其本地历史、命令和 skill / memory 表面到同一套资产模型。 |
| Gemini CLI | 计划适配 | 目标是通过 adapter 层接入历史、命令和项目上下文。 |
| 其他 AI coding agent / AI CLI | 设计预留 | 只要能提供本地活动记录、项目上下文和可复用命令入口，就可以按 adapter 方式接入。 |

这些后续项目是路线图，不是当前版本的安装承诺；当前公开支持仍以 macOS + Codex CLI 为准。

### 上线证据清单

公开上线后建议立刻保存这些证据，方便后续证明品牌和软件来源：

- GitHub README 首屏截图，包含 `OpenRelix™`、安装命令和仓库 URL。
- npm package 页面截图，包含 `openrelix` 包名、版本号和 `npx openrelix install`。
- GitHub Release `v0.1.0` 页面截图。
- GitHub Pages 展示页截图。
- 对应的 git tag、npm publish 时间和 release 发布时间。

商标标识使用 `OpenRelix™` / `openrelix™`；不要使用 `®`，除非相关商标已经在对应司法辖区完成注册。

### 依赖说明

用户机器满足上面的前置条件后，一键安装不应该再要求额外做项目级依赖安装：

- 不需要 `pip install ...`
  当前随包发布的 Python 脚本只使用 Python 标准库。
- 不需要 `npm install`
  npm 包只是 bootstrapper，没有声明运行时 npm dependencies。
- 不需要手动配置 `LaunchAgent`
  启用后台服务时，installer 会自动渲染 plist 并通过 `launchctl` 注册。
- Token 统计是可选增强
  面板会按需通过 `npx -y @ccusage/codex@latest` 获取数据。该命令不可用、离线或首次拉包失败时，面板其他部分仍可运行，Token 卡片会显示不可用状态或最近缓存。

如果 macOS 上缺少 Python 3.10+，先安装 Python，再重新执行 installer：

```bash
brew install python
npx openrelix install
```

---

## 适合什么场景

- 希望给 AI coding agent / AI CLI 增加一套可复用 skill、安装脚本和本地自动化能力
- 希望把一次次任务沉淀成可复用资产，而不是只留在聊天记录里
- 希望把“仓库源码”和“个人运行数据”分开管理
- 希望在 macOS 下支持一键安装、后台刷新、夜间整理，以及可选的夜间执行防睡眠策略

---

## 项目结构

仓库内主要保存这些内容：

- `AGENTS.md`
  维护这个项目本身时遵循的仓库级规则
- `.agents/skills/`
  仓库内的 canonical skills
- `install/`
  一键安装脚本和用户配置辅助脚本
- `ops/launchd/`
  macOS 下的 `LaunchAgent` 模板
- `scripts/`
  采集、整理、概览生成、实时 token 服务等自动化脚本
- `templates/`
  review 模板和夜间整理 schema
- `docs/`
  项目说明、安装说明和设计文档

默认情况下，用户运行后的状态数据不会写回仓库，而是放在独立 state root。

---

## 运行时数据放在哪里

默认 state root：

- `~/Library/Application Support/openrelix`

兼容旧安装：如果未显式设置 state root，且 legacy state root 存在而新的 `openrelix` state root 不存在，运行时会先复用旧目录，避免改名后看不到历史数据。

也可以在安装时显式指定：

```bash
./install/install.sh --state-dir "/your/state/root"
```

state root 中通常会包含：

- `registry/`
  资产注册表、复用记录、夜间整理后的 memory items
- `reviews/`
  脱敏任务复盘
- `raw/`
  原始采集结果，按日期和窗口整理
- `consolidated/`
  夜间整理输出
- `reports/`
  生成后的 `overview.md`、`overview-data.json`、`panel.html`
- `runtime/`
  token cache、夜间任务运行时目录、隔离 `CODEX_HOME`
- `log/`
  后台任务日志

这个分层的目的很直接：

- 仓库负责存“通用能力”
- state root 负责存“个人数据”

---

## 一键安装

以下命令面向 macOS 当前预览版。

`npx` 一行安装：

```bash
npx openrelix install
```

英文输出安装：

```bash
npx openrelix install --language en
```

`npx` 完整集成安装：

```bash
npx openrelix install --profile integrated --enable-learning-refresh --enable-nightly --keep-awake=during-job
```

默认安装会使用 `auto` 活动源：先尝试 `codex app-server` 读取 Codex threads，不可用时回退到稳定的 Codex CLI `history.jsonl` 和 `sessions/**/*.jsonl`。如果你想强制只读 CLI 文件，可以显式指定：

```bash
npx openrelix install --profile integrated --enable-learning-refresh --activity-source history
```

最小安装：

```bash
./install/install.sh --minimal
```

最小安装做这些事：

1. 初始化 state root
2. 生成第一份 overview 和 panel
3. 在当前 Codex 适配器下按默认 `integrated` 开启 memories/history，并同步一份 bounded summary

默认最小安装会初始化 state root、生成第一份 overview，并在当前 Codex 适配器下开启 memories/history，把 bounded summary 同步进 `CODEX_HOME` 以便注入 host 上下文。它仍然不会安装全局命令，不会写 shell rc，也不会注册 LaunchAgent。如果只想本地记录、不注入 host context，请显式传 `--record-memory-only`。

从 repo checkout 本地验证“安装后能看到面板”的最小闭环时，建议使用临时烟测脚本：

```bash
scripts/smoke_temp_panel.sh
```

它会创建临时 state root 和临时 `CODEX_HOME`，执行 `--minimal --record-memory-only`，打印 `doctor` / `core` 检查结果，并打开生成的 `reports/panel.html`。不想自动打开浏览器时使用：

```bash
scripts/smoke_temp_panel.sh --no-open
```

验证结束后可以清理临时 state root 和临时 `CODEX_HOME`：

```bash
scripts/cleanup_smoke_temp.sh --dry-run
scripts/cleanup_smoke_temp.sh --yes
```

安装时可以选择本地运行语言：

```bash
./install/install.sh --language zh
./install/install.sh --language en
```

当前支持 `zh` / `en`。交互式安装如果没有传 `--language`，会提示选择中文或英文；非交互安装默认 `zh`，也可以显式传 `--language` 固定自动化行为。选择结果会写入 state root 的 `runtime/config.json`，并影响终端输出、overview 本地转化、夜间整理 prompt、fallback summary、即时任务复盘、资产 / 复用记录的人读字段，以及写入本地 `memory_items.jsonl` 的结构化记忆语言。`type`、`scope`、`status`、`memory_type` 这类枚举键保持稳定，展示层再按语言转成中文或英文。

个人记忆系统默认开启，并且默认模式是 `integrated`：一份完整的本地资产记忆记录到本项目的 state root，同时把压缩后的 bounded summary 同步进当前 host native context。用户可以按需要显式选择只本地记录，或关闭本系统本地记忆写入。

bounded summary 的压缩策略保持轻量：同签名记忆跨天归并，durable / session 优先进入上下文，low-priority 默认只留本地；默认 token budget 是 target 6.7K、warn 7.4K、max 8K，避免把原始窗口或完整 registry 塞进 host context。

```bash
./install/install.sh --profile integrated
./install/install.sh --profile integrated --record-memory-only
./install/install.sh --profile integrated --disable-personal-memory
```

- `--use-integrated`
  使用当前 Codex 适配器的 host native memory context：开启 memories/history，并把 bounded summary 同步进 `CODEX_HOME`。这是当前默认模式。
- `--record-memory-only`
  记录个人记忆，但禁用 host native memory context，并保持 bounded summary 不同步到 `CODEX_HOME`
- `--disable-personal-memory`
  关闭本系统的个人记忆写入；仍可保留安装器、命令和面板能力

完整集成安装：

```bash
./install/install.sh --profile integrated --enable-learning-refresh
```

完整集成会额外做这些事：

1. 默认补齐用于本地采集的 bounded `history` 配置，并开启当前 Codex 适配器的 native `memories` 与 bounded summary 同步
2. 把 repo 内的 skills 软链接到用户级 `~/.codex/skills/`，包括 `memory-review`
3. 把 repo 提供的 custom prompt 安装到用户级 `~/.codex/prompts/`，作为兼容 fallback
4. 安装全局 `openrelix` 命令，并在需要时把对应用户 bin 目录写入 shell `PATH`
5. 安装 macOS 后台 refresh 服务；加 `--enable-learning-refresh` 时，每 30 分钟自动调用当前 Codex 适配器学习最近 7 天窗口

完整集成默认会把 bounded summary 写入 `CODEX_HOME`，让当前 host 能读取压缩后的上下文。完整的结构化资产记忆仍写入 state root；面板继续把 host native memory 和本项目本地 memory registry 分层展示。需要严格隔离时，使用 `--record-memory-only` 或 `--no-memory-summary`。

如果你还想启用夜间整理和夜间任务执行时的防睡眠策略：

```bash
./install/install.sh --profile integrated --enable-learning-refresh --enable-nightly --keep-awake=during-job
```

如需调整 nightly 时间，可以使用 24 小时制 `HH:MM`：

```bash
./install/install.sh --profile integrated \
  --enable-nightly \
  --nightly-organize-time 22:30 \
  --nightly-finalize-time 01:00
```

常用参数：

- `--profile minimal|integrated`
  选择最小安装或完整集成安装
- `--state-dir PATH`
  指定自定义 state root
- `--codex-home PATH`
  指定自定义 `CODEX_HOME`
- `--language zh|en`
  指定本地运行语言，默认 `zh`
- `--memory-mode integrated|local-only|off`
  指定记忆模式，默认 `integrated`
- `--record-memory-only`
  记录个人记忆，但不往 host native context 注入记忆摘要；等价于 `--memory-mode local-only`
- `--use-integrated`
  使用当前 host native memory context；等价于 `--memory-mode integrated`
- `--disable-personal-memory`
  关闭本系统本地记忆写入；等价于 `--memory-mode off`
- `--python PATH`
  指定脚本使用的 Python
- `--sync-memory-summary`
  显式同步 bounded summary 到 `CODEX_HOME`；主要用于覆盖组合参数时强制打开
- `--no-memory-summary`
  跳过 bounded host memory summary 同步；用于严格控制本系统不注入 host context 的安装
- `--no-global-skills`
  不把 repo skill 安装到用户级全局 skill 目录
- `--install-global-skills`
  在最小安装基础上显式安装全局 skill
- `--no-custom-prompts`
  不安装 repo 提供的 Codex custom prompt
- `--install-custom-prompts`
  在最小安装基础上显式安装 custom prompt
- `--no-global-command`
  不安装全局 `openrelix` 命令
- `--install-global-command`
  在最小安装基础上显式安装 `openrelix` 命令
- `--bin-dir PATH`
  指定 `openrelix` 命令的安装目录
- `--enable-background-services`
  安装 overview refresh 和 token-live 后台服务
- `--disable-background-services`
  不安装 overview refresh 和 token-live 后台服务
- `--enable-nightly`
  安装夜间整理服务
- `--nightly-organize-time HH:MM`
  调整当天预览整理时间，默认 `23:00`
- `--nightly-finalize-time HH:MM`
  调整前一天终版整理时间，默认 `00:10`
- `--keep-awake=during-job`
  夜间任务运行时用 `caffeinate` 保持唤醒
- `--disable-memories`
  不修改 `Codex memories` 相关配置
- `--enable-memories`
  在最小安装基础上显式开启 `Codex memories` 配置
- `--disable-history`
  不修改 `history` 相关配置
- `--enable-history`
  在最小安装基础上显式开启 bounded `history` 配置

---

## Skills 是怎么自动加载的

这里有两层：

### 1. 仓库内启动

如果你使用的 AI host 支持仓库内 skill 发现，仓库下 `.agents/skills/` 里的 skills 会被自动发现。当前预览版适配器使用 Codex 的 repo-local skill 发现机制。

这适合“只在当前仓里用”的能力。

### 2. 任意仓库下启动

如果你希望这些 skills 在任何仓库里启动 AI host 都能用，就需要把它们安装到该 host 的用户级 skill 目录。当前预览版会安装到 Codex 用户级 skill 目录。

完整集成安装用 symlink 方式把 canonical repo skill 挂到：

```bash
~/.codex/skills/
```

这一步来自 `--profile integrated`，或者在最小安装基础上显式加 `--install-global-skills`。

这样可以同时满足两件事：

- 真源仍然维护在当前仓里
- 用户在任意仓库打开当前 AI host 时，都能自动加载这套全局 skill

### 不是靠 hook 实现的

这个项目不把 hook 当作“全局 skill 挂载机制”。

hook 在这里是可选的生命周期自动化能力，主要用于后台任务或事件扩展；skill 的可发现性主要依赖：

- repo-local skill 发现
- user-level skill 安装

---

## `memory-review` 入口是怎么装进去的

`--profile integrated` 会同时安装两层入口：

- 主入口：用户级 `memory-review` skill
- 兼容层：用户级 custom prompt

custom prompt 文件路径是：

```bash
~/.codex/prompts/memory-review.md
```

其中 custom prompt 不是 repo 直接共享的“真源”，而是 integrated installer 在本地生成的兼容入口层，用来把下面这条共享 workflow 挂到用户本地：

- 执行 repo 内维护的 `memory-review` workflow
- 立即生成或更新任务复盘
- 按需更新资产注册表与复用记录
- 重建 overview / panel

也就是说：

- repo 负责维护可审阅、可开源的 skill、模板和脚本
- installer 负责把它挂成用户本地可直接调用的 skill / prompt / shell 入口

安装完成后，主推荐入口是在新的 AI agent 线程里直接输入；当前预览版适配器对应 Codex 线程：

```text
/memory-review
```

兼容 prompt 入口仍然是：

```text
/prompts:memory-review
```

之所以保留两层，是因为在部分 AI host / Codex CLI 版本里，custom prompt 不一定会稳定出现在顶层 slash 候选里；而 skill 路线更接近 repo 可共享能力的本意。

同时，shell 侧会有一个全局入口：

```bash
openrelix open panel
openrelix core
openrelix mode
openrelix review
```

安装完成后，installer 会集中打印推荐下一步。首个动作是打开本地面板：

```bash
openrelix open panel
```

推荐安装时打开 30 分钟自动学习刷新，让 OpenRelix 持续用今日窗口和最近 7 天上下文提炼本地记忆：

```bash
npx openrelix install --profile integrated --enable-learning-refresh
```

这个选项是显式指令：默认后台 `overview-refresh` 仍不调模型；加 `--enable-learning-refresh` 后，30 分钟 LaunchAgent 会调用当前 Codex 适配器，读取最近 Codex 窗口，更新本系统本地 memory 和 overview。默认 `integrated` 会同步 bounded summary 到 Codex native context，但不会写入原始窗口或完整 registry；如果使用 `--record-memory-only`，则只更新本地 state root。如果没有安装全局 `openrelix` 命令，installer 会打印一条带 `AI_ASSET_STATE_DIR` / `CODEX_HOME` 的 `python3 scripts/openrelix.py ...` fallback 命令。

其中：

- `openrelix open panel`
  直接打开可视化面板
- `openrelix core`
  在终端里打印当前 overview 的核心指标和今日 review 摘要
- `openrelix mode`
  查看当前记忆模式；安装后要切换模式时使用 `openrelix mode integrated`、`openrelix mode local-only` 或 `openrelix mode off`，不需要重新安装
- `openrelix review`
  按需跑一遍“今天”的 review / consolidate 流水线，并输出 `summary.md`

如果需要卸载本机集成，使用：

```bash
npx openrelix uninstall
```

它会清理 OpenRelix 的 LaunchAgents、macOS 客户端、全局 shell 入口、用户级 `memory-review` skill、custom prompt fallback 和 installer 管理的 shell `PATH` block。交互式终端会询问是否同时删除本地记忆；无人值守时请显式选择：

```bash
npx openrelix uninstall --keep-local-memory
npx openrelix uninstall --delete-local-memory
```

`--delete-local-memory` 会删除 active state root 和 OpenRelix 写入的 `CODEX_HOME/memories/memory_summary.md`，但不会删除整个 `CODEX_HOME`、Codex 登录凭据或 Codex history/session 文件。

---

## 后台自动化能力

在 macOS 下，安装脚本会按需渲染并注册 `LaunchAgent`。

### 默认后台能力

- `overview refresh`
  默认每 30 分钟刷新一次 overview / panel 快照；安装时加 `--enable-learning-refresh` 后，每 30 分钟会调用当前 Codex 适配器并学习最近 7 天窗口
- `token live server`
  提供本地 token 实时接口，给 panel 页面做即时刷新

### 可选夜间能力

- `23:00` 生成当天整理预览
- `00:10` 回补前一天终版整理，避免遗漏 23:00 之后的内容

可以通过安装参数调整这两个时间：

```bash
--nightly-organize-time 22:30 \
--nightly-finalize-time 01:00
```

### 可选防睡眠策略

如果安装时指定：

```bash
--keep-awake=during-job
```

夜间任务会通过 `caffeinate` 在执行期间临时保持唤醒。

这不是永久防睡眠，也不会默认长期改变机器行为。

---

## Plugin 路线状态

当前 Codex plugin 是已打包的 Codex route，会携带 `memory-review` skill 和 marketplace metadata；完整本地集成仍以 installer-first 为主。

首发推荐只讲 installer-first：

- 通过 `./install/install.sh` 初始化 state root
- 通过 symlink 把 repo skill 挂到用户级 `~/.codex/skills/`
- 通过 `openrelix` 和 macOS LaunchAgent 接上本地自动化

插件目录保留在仓里，是为了让同一套 canonical skills 可以直接作为 Codex plugin 发布；npm 包会携带插件目录和 marketplace metadata。

---

## 日常怎么使用

### 生成概览 / 刷新快照

```bash
openrelix refresh
```

### 立即学习并刷新本地记忆

```bash
openrelix refresh --learn-memory --learn-window-days 7
```

### 查看核心数据

```bash
openrelix core
```

### 立即发起今日 review

```bash
openrelix review
```

### 打开面板

```bash
openrelix open panel
```

### 底层脚本

```bash
python3 scripts/build_overview.py
```

如果没有设置 `AI_ASSET_STATE_DIR`，则使用脚本里的默认 state root 规则。

---

## npm 分发方式

npm 包只做 bootstrapper，不重新实现安装逻辑。

它会随包带上仓内的 installer、skills、templates、scripts 和 docs，然后从 npm package cache 里调用同一份：

```bash
install/install.sh
```

也就是说：

- npm 负责一键下载和入口命令
- `install/install.sh` 仍然是安装行为的唯一真源
- npm 包通过 `files` 白名单排除 `raw/`、`registry/`、`reports/`、`runtime/`、`log/` 等个人运行数据

发布前建议先检查包内容：

```bash
npm pack --dry-run
```

---

## 授权许可

本项目使用标准 `MIT License` 开源授权。

- 版权持有人：`kk_kais`
- npm 主页：[kk_kais](https://www.npmjs.com/~kk_kais)
- 授权年份：`2026`
- 授权范围：允许个人免费使用、复制、修改、合并、发布、分发和再授权
- 保留要求：分发本项目或本项目的主要部分时，需要保留版权声明和完整授权文本
- 免责声明：软件按现状提供，不提供任何明示或暗示担保

完整授权文本见仓库根目录的 `LICENSE`。

---

## 隐私和开源边界

这个项目默认遵循本地优先。

建议明确区分两类内容：

### 适合开源共享的

- installer
- skills
- templates
- docs
- launchd 模板
- 自动化源码
- draft plugin 包装层

### 不应进入公开仓库的

- 原始 Codex history
- runtime cache
- 日志
- 真实 review
- 真实 registry
- 内部任务上下文
- token、账号、Cookie、用户数据

也就是说，repo 是“能力层”，state root 是“个人数据层”。

---

## 当前设计原则

- 仓库内只维护可审阅、可迁移、可开源的源码与模板
- 用户状态尽量不写回仓库
- 不把某一台机器的绝对路径硬编码成项目能力
- 不把 hook 当成 skill 的主加载链路
- `v0.1.0 预览版` 先通过 macOS installer 提供低摩擦的一键安装体验
- 通过夜间整理和 overview/panel，提供可持续沉淀和可视化查看能力

---

## 推荐安装命令

如果你希望装出一套完整可用版本，推荐：

```bash
./install/install.sh --profile integrated --enable-learning-refresh --enable-nightly --keep-awake=during-job
```

默认会自动尝试读取 Codex 应用线程；如果只想强制读取 CLI history/session 文件，可以显式加：

```bash
./install/install.sh --profile integrated --enable-learning-refresh --activity-source history
```

如果你只想先体验最小能力，推荐：

```bash
./install/install.sh
```

这两个模式的差别是：

- 最小安装：初始化状态目录和 overview；在当前 Codex 适配器下开启 Codex memories/history，并同步 bounded summary；不改 shell / launchd 配置
- 完整安装：再把 skill、memory、`openrelix` 命令、后台刷新、夜间整理和任务期间防睡眠一起接上
- memory 边界：当前 Codex 适配器只向 `CODEX_HOME` 同步 bounded summary 作为轻量上下文；本项目自己的完整长期资产、复盘和夜间整理结果归 state root 管
