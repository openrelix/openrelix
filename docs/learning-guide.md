# OpenRelix 学习指南

## 适合谁读

这份指南面向三类读者：

- **使用者**：想把系统安装起来，理解它会读什么、写什么、不会碰什么。
- **贡献者**：想改 installer、脚本、skill、面板或文档。
- **维护者**：准备开源发布，需要快速判断边界、风险和验证方式。

建议先把项目理解成一句话：

> 仓库保存可复用能力，state root 保存个人运行数据，Codex 负责自己的原生上下文，本项目负责把可复用工作沉淀成结构化资产和本地面板。

GitHub 项目页：[openrelix/openrelix](https://github.com/openrelix/openrelix)。如果这套路径有帮助，欢迎点星支持。

## 30 分钟快速路径

### 1. 先读项目边界

按这个顺序读：

1. `README.md`
2. `docs/privacy-and-distribution.md`
3. `docs/technical-solution.md`

读完需要能回答：

- 为什么 repo 不保存真实 registry、reviews、raw history 和 logs？
- minimal install 和 integrated install 的区别是什么？
- 默认 memory mode 为什么是 `integrated`，以及什么时候要切到 `record-memory-only`？
- 为什么 Codex plugin 只承载 skill route，而完整本地集成仍由 installer 负责？

### 2. 跑一个最小安装

最快的 repo checkout 验证方式是直接跑临时面板烟测脚本：

```bash
scripts/smoke_temp_panel.sh
```

它会创建临时 state root 和临时 `CODEX_HOME`，执行 `--minimal --record-memory-only` 安装，打印 `doctor` / `core` 结果，最后打开生成的 `reports/panel.html`。如果只想在终端里拿到面板路径：

```bash
scripts/smoke_temp_panel.sh --no-open
```

验证后可以清理脚本创建的临时目录：

```bash
scripts/cleanup_smoke_temp.sh --dry-run
scripts/cleanup_smoke_temp.sh --yes
```

需要手动拆解安装过程时，也建议使用临时 state root，避免污染自己的正式数据：

```bash
export AI_ASSET_STATE_DIR="$(mktemp -d)"
./install/install.sh --minimal --language zh
```

最小安装会初始化 state root、生成第一份 overview，并按默认 `integrated` 开启 Codex memories/history、同步一份 bounded summary。它仍然不安装全局 skill，不改 shell rc，不注册 LaunchAgent。

检查输出：

```bash
find "$AI_ASSET_STATE_DIR" -maxdepth 2 -type f | sort
```

你应该看到类似：

```text
registry/assets.jsonl
registry/usage_events.jsonl
registry/memory_items.jsonl
reports/overview-data.json
reports/overview.md
reports/overview.csv
reports/panel.html
runtime/config.json
```

### 3. 看运行时路径

如果已经安装了 `openrelix`：

```bash
openrelix paths
```

如果没有全局命令，直接读核心路径模块：

```bash
python3 - <<'PY'
from scripts.asset_runtime import get_runtime_paths
paths = get_runtime_paths()
for name in ("repo_root", "state_root", "codex_home", "reports_dir", "registry_dir"):
    print(f"{name}: {getattr(paths, name)}")
PY
```

重点理解：所有 runtime 目录都从 `scripts/asset_runtime.py` 出来，不应该在新脚本里散落重复路径规则。

### 4. 打开面板

如果有 `openrelix`：

```bash
openrelix open panel
```

否则：

```bash
open "$AI_ASSET_STATE_DIR/reports/panel.html"
```

初始面板没有真实数据是正常的。先确认页面能打开，指标为空时展示合理。

## 2 小时源码阅读路径

### 第一段：运行时和安装器

阅读：

- `scripts/asset_runtime.py`
- `install/install.sh`
- `install/render_template.py`
- `install/configure_codex_user.py`
- `install/configure_shell_path.py`

理解目标：

- state root 如何选择。
- `CODEX_HOME` 如何覆盖。
- language 和 memory mode 如何写入 `runtime/config.json`。
- minimal profile 保持低侵入的原因。
- integrated profile 额外安装了哪些能力。

检查点：

- 新增安装能力时，是否有显式开关？
- 是否破坏非交互安装？
- 是否把 macOS-only 行为写成了跨平台承诺？

### 第二段：skill 和立即复盘

阅读：

- `.agents/skills/memory-review/SKILL.md`
- `install/templates/codex-prompts/memory-review.md.tmpl`
- `templates/task-review-template.md`
- `templates/asset-entry-example.json`

理解目标：

- `/memory-review` 的主入口是 skill。
- custom prompt 是兼容 fallback。
- 复盘写入 `reviews/YYYY/`。
- 稳定资产写入 `registry/assets.jsonl`。
- 真实复用证据写入 `registry/usage_events.jsonl`。

练习：

1. 写一份脱敏 review markdown 到临时 state root。
2. 手动追加一条 asset JSONL。
3. 运行 `python3 scripts/build_overview.py`。
4. 打开 panel，确认资产出现在概览里。

### 第三段：采集和夜间整理

阅读：

- `scripts/collect_codex_activity.py`
- `scripts/nightly_pipeline.sh`
- `scripts/nightly_organize_today.sh`
- `scripts/nightly_finalize_previous_day.sh`
- `scripts/nightly_consolidate.py`
- `templates/nightly-summary-schema.json`

理解目标：

- 采集阶段默认先把 Codex app-server threads 转成结构化 raw JSON，不可用时回退 Codex history/session。
- 整理阶段用 `codex exec --ephemeral` 生成 schema-constrained summary。
- 失败时会 fallback。
- 已有 summary 更好时会保留旧结果。
- `memory_mode=off` 时不写入个人 memory items。

关键问题：

- 为什么 prompt 里要明确禁止 shell、web、MCP、apply_patch 和额外文件读取？
- `learn-window-days` 学的是抽象粒度和分类方式，为什么不能把历史事实直接抄进当天结果？
- 为什么要保留 `runs/` 下的 candidate 记录？

### 第四段：overview 和 panel

阅读：

- `scripts/build_overview.py`
- `docs/metric-dictionary.md`
- `docs/product-showcase.html`

理解目标：

- overview 的输入不是单一来源，而是 registry、reviews、raw、consolidated 和 Codex native memory 的聚合。
- `reports/overview-data.json` 是面板和 markdown 的结构化事实源。
- `reports/panel.html` 是静态 HTML，token live server 只是增强实时 token 数据。
- value score 和 estimated saved time 是趋势指标，不是精确测速。

练习：

```bash
python3 scripts/build_overview.py
python3 -m json.tool "$AI_ASSET_STATE_DIR/reports/overview-data.json" >/dev/null
open "$AI_ASSET_STATE_DIR/reports/panel.html"
```

### 第五段：后台自动化

阅读：

- `ops/launchd/*.plist.tmpl`
- `scripts/run_with_optional_caffeinate.sh`
- `scripts/refresh_overview.sh`
- `scripts/token_live_server.py`

理解目标：

- `overview-refresh` 每 1800 秒刷新一次，并 `RunAtLoad`。
- `token-live` 作为本地服务运行，并 `KeepAlive`。
- `nightly-organize` 默认每天 23:00 运行，可通过 `--nightly-organize-time HH:MM` 调整。
- `nightly-finalize-previous-day` 默认每天 00:10 运行，可通过 `--nightly-finalize-time HH:MM` 调整。
- `--keep-awake=during-job` 只包住任务执行期。

运维判断：

- 锁屏可以继续跑。
- 退出登录不行，因为用户级 LaunchAgent 不再执行。
- 出问题先看 state root 下的 `log/`。

## 常见任务怎么改

### 新增一个稳定 skill

1. 放到 `.agents/skills/<name>/SKILL.md`。
2. 写清楚触发条件、运行时边界、输出位置和隐私要求。
3. 如果需要全局安装，更新 installer 的 skill symlink 逻辑。
4. 更新 README 或 docs。
5. 用临时 state root 跑一遍 install 验证。

### 新增一个自动化脚本

1. 从 `scripts/asset_runtime.py` 获取路径，不要硬编码个人路径。
2. 输出写到 state root。
3. 需要后台运行时，先做手动 CLI，再考虑 launchd 模板。
4. 新增 state 文件时，确认 `.gitignore`、`.npmignore` 和 package `files` 不会带上个人数据。
5. 补测试或至少补 shell / Python 语法检查。

### 修改安装器

优先保持这些不变：

- minimal 默认会写入必要的 Codex memory/history 配置和 bounded summary。
- minimal 不写 shell rc。
- minimal 不注册 LaunchAgent。
- `--record-memory-only` / `--no-memory-summary` 必须能显式阻止 Codex context 注入。
- macOS-only 能力必须在文档里明确标注。

验证：

```bash
zsh -n install/install.sh scripts/*.sh
python3 -m py_compile scripts/*.py install/*.py
```

### 修改 nightly 输出

需要同步检查：

- `templates/nightly-summary-schema.json`
- `scripts/nightly_consolidate.py`
- `scripts/build_overview.py`
- `tests/test_nightly_logic.py`
- `docs/metric-dictionary.md`

注意：schema 变更会影响模型输出、fallback、memory registry 和面板展示，最好配套测试。

### 修改 npm 发布内容

检查：

- `package.json` 的 `files`
- `.npmignore`
- `install/npm-bin.js`
- `README.md`
- `LICENSE`
- `SECURITY.md`

发布前：

```bash
npm pack --dry-run
```

确认包里有 installer、scripts、templates、docs、repo skill；没有 raw、registry、reports、runtime、log、真实 reviews。

## 推荐验证命令

完整本地检查：

```bash
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest discover -s tests
zsh -n install/install.sh scripts/*.sh
npm pack --dry-run
```

安装链路检查：

```bash
scripts/smoke_temp_panel.sh --no-open
scripts/cleanup_smoke_temp.sh --dry-run
```

nightly 手动检查：

```bash
./install/install.sh --profile integrated --enable-learning-refresh
openrelix refresh --learn-memory --learn-window-days 7
openrelix core
openrelix open panel
```

推荐安装时加 `--enable-learning-refresh`，让 30 分钟 overview-refresh 自动调用当前 Codex 适配器，用今日窗口和最近 7 天上下文生成本地记忆与 overview；默认后台 `overview-refresh` 不调模型。`openrelix refresh --learn-memory --learn-window-days 7` 仍可用于手动立即跑一次。如果需要完整补齐缺失或非 final 的日报，再使用 `openrelix review --date "$(date +%F)" --learn-window-days 7`。

多日回溯检查：

```bash
openrelix backfill --from 2026-04-24 --to 2026-04-27 --learn-window-days 7
```

不连续日期建议用 `--dates`，避免给中间没有活动的日期生成空 summary：

```bash
openrelix backfill --dates 2026-04-21,2026-04-23,2026-04-24 --learn-window-days 7
```

回溯时要区分两段：`collect_codex_activity.py` 是本地离线采集；`nightly_consolidate.py` 会通过 `codex exec --ephemeral` 调用 Codex 大模型生成结构化整理结果。

如果没有全局 `openrelix`：

```bash
python3 scripts/collect_codex_activity.py --date "$(date +%F)" --stage manual
python3 scripts/nightly_consolidate.py --date "$(date +%F)" --stage manual --learn-window-days 7
python3 scripts/build_overview.py
```

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

- 这是新 state root，还没有资产、复盘或 Codex history。
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

默认会接入 Codex native context，但只写压缩后的 bounded summary。完整 registry、reviews、raw windows 和 consolidated summaries 仍留在 state root；压缩策略会合并重复记忆，优先 durable / session，low-priority 默认只留本地，并把摘要控制在 6.7K target / 8K max token 左右。

如果要严格隔离，不往 Codex context 注入摘要，用：

```bash
./install/install.sh --profile integrated --record-memory-only
```

### 为什么要把 state root 放仓库外

因为开源仓库应该只包含可复用能力，不应该混入用户真实工作记录、内部路径、日志、cache 或复盘。这样 `git status`、npm 包内容和公开仓库边界都更清楚。

## 贡献前检查清单

- 没有新增硬编码个人绝对路径。
- 没有把真实 state root 内容加入仓库。
- 默认只写 bounded summary，不把原始窗口或完整 registry 注入 Codex native memory。
- 没有让当前预览版看起来承诺 Linux / Windows。
- 文档说明了隐私边界和安装边界。
- minimal profile 仍然低侵入。
- integrated profile 的额外动作都有明确开关。
- `npm pack --dry-run` 输出符合预期。

## 继续深入

按目标选择下一步：

- 想理解架构：继续读 `docs/technical-solution.md`。
- 想理解指标：继续读 `docs/metric-dictionary.md`。
- 想理解发布边界：继续读 `docs/privacy-and-distribution.md`。
- 想准备 README 对外介绍：继续读 `docs/open-source-install-and-project-overview.md`。
- 想做 UI 展示：打开 `docs/product-showcase.html` 和生成后的 `reports/panel.html` 对照看。
