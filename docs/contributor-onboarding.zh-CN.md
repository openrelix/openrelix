# OpenRelix 贡献者快速上手

> 语言版本：[English](contributor-onboarding.md) | 简体中文

这份指南面向已经有本地 checkout、准备做一个小而可审查的 OpenRelix 改动的贡献者。目标是帮你完成一次安全的本地闭环，不碰真实运行时状态。

如果需要先理解架构，请按顺序阅读 [系统概览](system-overview.zh-CN.md)、[技术方案](technical-solution.md)，再看 [开发者指南](developer-guide.md)。本文是读完架构后的动手路径。

## 目标

新贡献者应该能做到：

1. 理清 repo source 和 runtime state 的边界。
2. 创建专用 worktree，并跑一个不会污染本机真实数据的 smoke loop。
3. 接一个边界清晰的任务，知道该改哪些文件、跑哪些测试、避开什么隐私风险。
4. 交付一组别人可以审查的改动，不需要靠猜测还原验证方式。

这不是 release roadmap。它只用于在更多贡献者加入前，把协作入口、任务形态和验证习惯先固定下来。

## 10 分钟本地闭环

每个 clone 运行一次，让 pre-commit hook 自动生效：

```bash
./scripts/setup-dev.sh
```

之后从干净 checkout 或专用 worktree 开始。Codex 会话用 `codex/<task>`、Claude Code 会话用 `claude/<task>`，让 log 里能直接看出 PR 是哪类 agent 产出的：

```bash
git status --short --branch
git worktree add -b codex/<task-name> ../openrelix-worktrees/<task-name> main
cd ../openrelix-worktrees/<task-name>
```

用临时 state root 跑本地生成：

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-dev.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
python3 -m json.tool "$STATE_DIR/reports/overview-data.json" >/dev/null
PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"
```

如果要做更完整的 install-to-panel smoke，并且仍然避免真实用户数据：

```bash
scripts/smoke_temp_panel.sh --no-open
scripts/cleanup_smoke_temp.sh --dry-run
```

只有在任务明确需要检查当前机器的真实 OpenRelix state 时，才使用 `--seed-current-state`。不要把 seed 产生的内容复制进 docs、fixtures、tests、screenshots 或 release artifacts。

## 仓库地图

选择任务 owner 时，先看这张表：

| 区域 | 主要文件 | 常见改动 | 必要检查 |
| --- | --- | --- | --- |
| Installer | `install/`, `ops/launchd/`, `install/templates/` | 安装参数、profile、LaunchAgents、shell 模板 | `zsh -n install/install.sh scripts/*.sh`，focused installer tests |
| Runtime paths | `scripts/asset_runtime.py` | state root、host home、runtime config、atomic writes | focused unit tests，临时 state smoke |
| Host collection | `scripts/collect_codex_activity.py` | Codex 或 Claude 输入映射、raw windows、source metadata | `python3 -m unittest tests/test_collect_codex_activity.py` |
| Memory context | `scripts/build_codex_memory_summary.py`, `scripts/sync_host_memory_summary.py`, `scripts/openrelix_overview/memory_context.py` | scope、injection policy、summary budget、host block sync | memory summary 和 context tests |
| Curated memory | `scripts/build_curated_memory_pack.py`, `scripts/openrelix_overview/curated_memory.py` | pack grouping、diagnostics、redaction、sidecar output | `python3 -m unittest tests/test_curated_memory.py` |
| Overview and panel | `scripts/build_overview.py`, `scripts/openrelix_overview/`, `docs/*.html` | overview contract、report data、panel UI、public site | contract check、panel smoke、必要时做 browser check |
| Index | `scripts/openrelix_index.py` | SQLite sidecar schema、memory/window search | `python3 -m unittest tests/test_openrelix_index.py` |
| Public docs | `README*.md`, `docs/*.md`, `docs/*.html` | 公开说明、贡献者文档、隐私边界 | `python3 scripts/check_personal_info.py`，link/version review |

`.agents/skills/openrelix-*-harness/` 是维护本仓库用的开发 harness skill。除非明确做 package-surface 决策，否则不要把它们加入 `plugins/openrelix/` 或 npm `files` allowlist。

## 任务卡模板

一个可交接的小任务应该能写成这样：

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

## 完成标准

贡献者改动可以进入 maintainer review，需要同时满足：

- 在专用 branch 或 worktree 中完成。
- 用户状态仍然留在 repo 外。
- state root 或 host path 解析走 `scripts/asset_runtime.py`。
- 行为、数据契约或贡献流程变化时同步更新 docs。
- 共享行为、数据契约或回归风险有 focused tests。
- 至少跑过：

```bash
python3 scripts/check_personal_info.py
git diff --check
```

如果改了 Python、installer、docs/site、release 或 package surface，还要按 [验证矩阵](validation-matrix.zh-CN.md) 增加检查。

## 贡献者隐私规则

公开 repo 改动不能包含：

- 原始 host transcripts、session files、logs、runtime reports、私有面板截图或真实 registry rows。
- secrets、tokens、cookies、账号标识、私有组织名、内部 URL 或未脱敏的专有代码片段。
- 真实用户 home 绝对路径。
- 应留在外部 state root 的 Codex 或 Claude 站点化 memory mappings。

schema 示例见 [数据契约](data-contracts.zh-CN.md)，脱敏 fixture 形态见 `tests/fixtures/sample-state/`。

## 常见坑

| 现象 | 可能原因 | 先查什么 |
| --- | --- | --- |
| 本地测试写进真实 state root | 没有设置 `AI_ASSET_STATE_DIR` | 用临时 `STATE_DIR` 重跑 |
| import overview helper 时创建了文件 | helper import 带副作用 | 加 focused import test，或把写逻辑移到命令入口后 |
| host context 在 OpenRelix block 外被改了 | sync code 没保留 host-owned content | 检查 `sync_host_memory_summary.py` 的 block markers |
| panel 显示旧内容 | source 改了但 `reports/panel.html` 没重建 | 用临时 state 或目标 state root 重建 |
| package dry run 带进私有文件 | `package.json` `files` allowlist 放宽过头 | 检查 `npm pack --dry-run --json` 输出 |

## Review 清单

请求 review 前写清：

- 改了什么、为什么改。
- 涉及文件。
- 验证命令和结果。
- 跳过了哪些检查，以及原因。
- 剩余风险，尤其是隐私、package surface、host context 或 runtime state。
