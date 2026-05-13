# OpenRelix 系统概览

> 语言版本：[English](system-overview.md) | 简体中文

面向 AI coding agents 的本地优先记忆和资产系统。

## 目标

构建一套本地优先系统，让 AI coding agents 随着使用逐渐变好，同时不把某个用户的机器布局或某个 AI host 变成产品本身。

这个仓库服务两个并行目标：

1. 保存稳定偏好、playbooks 和可复用 workflow assets。
2. 把这些资产变成可见输出，而不是困在聊天历史里。

GitHub 项目页：[openrelix/openrelix](https://github.com/openrelix/openrelix)。如果项目有帮助，欢迎点星。

## 分层

### 1. Host adapter layer

- 当前预览位置：用户的 `~/.codex/` 目录，以及可选的 `~/.claude/` 目录。
- 作用：host-specific config、可选 global instructions、本地 memories、用户级 installed skills。
- 当前预览示例：`~/.codex/config.toml`、`~/.codex/AGENTS.md`、`~/.codex/skills/`、`~/.claude/CLAUDE.md`。
- 所有权：AI host 拥有自己的 native memory/config files。默认 `integrated` mode 下，本 repo 只写一份 bounded shared summary 用于 context injection；完整 local registries、reviews、raw windows 和 reports 留在 state root。

### 2. Repo source layer

- 位置：本仓库。
- 作用：canonical skills、templates、installer logic 和 automation source code。
- 示例：`.agents/skills/`、`install/`、`ops/launchd/`、`scripts/`、`templates/`。

### 3. Runtime state layer

- 位置：installer 管理的外部 state root，默认在 repo 外。
- 作用：用户数据、采集历史、nightly output、reports、caches 和 logs。
- 示例：`registry/`、`reviews/`、`raw/`、`consolidated/`、`reports/`、`runtime/`、`log/`。

## Skill Loading Model

- 当 active AI host 支持 repo-local skills 时，repo-local skills 会自动可发现。当前预览 adapter 目标是 Codex discovery。
- 如果同一批 skills 需要在任意仓库可用，就把它们安装到 active host 的用户级 skill root。当前预览 installer 使用 Codex skill root。
- Hooks 不是主要 skill discovery path。它们只是可选 lifecycle automation。

## Memory Layering

- Host-native memory 由 host 拥有。Codex native memory 位于 `~/.codex/memories/`；Claude Code native context 可以包含 `~/.claude/CLAUDE.md`。
- 本 repo 把 host-native memory 当作可选 bounded context target 和 upstream signal。panel 可以把 Codex 和 Claude Code native memory 与 local nightly memories 并列展示，同时 OpenRelix 把一份 compressed personal-memory summary 同步到启用的 host contexts。
- repo 自己的 memory layer 位于 active state root。Memory System v2 优先读取 canonical `registry/memory_entries.jsonl`，并仍然 fallback 到 legacy `registry/memory_items.jsonl`；reviews、raw windows、consolidated summaries 和 generated reports 与它放在一起。
- 生成的 memories 进入可见 registry 前会经过 storage-quality gate：偏好可复用规则、稳定偏好、调试路径、workflow defaults 和 module mappings；问候、登录窗口、重复无结论窗口、一次性 artifact 和模糊 notes 会被丢弃或降级。
- Host context injection 按 scope 控制：`effective injection_policy` 为 `global_context` 或 `project_context` 的 memory entries 可以进入 bounded host summary。global context 最多占 summary budget 的 10%，project context 最多占 30%，两类内部按 heat 选择。legacy/nightly synthesis rows 没有 canonical、manual、approved 或 storage-quality-gated evidence 时，不会提升进 host context。domain、host-native、local-only、never-inject 和 low-priority memories 默认保留在 local registry/index。
- Personal-memory algorithm changes 有版本。增量安装会比较 `runtime/config.json` 的 stored version 与 code version；当检测到旧 local memory 时，标记 `runtime/memory-migration.json` pending，让常规 refresh 做 bounded recent-window migration，而不是静默复用旧 summary。
- 可重建 SQLite sidecar index 位于 `runtime/openrelix-index.sqlite3`，用于 memory 和 window lookup。它来自 `raw/`、`registry/` 和 `consolidated/`，删除它不能删除或损坏 source memory records。
- 默认 memory mode 是 `integrated`：local personal memory 记录一次，并把 bounded summary 注入启用的 host-native contexts。`--record-memory-only` 固定 strict local-only 行为，`--disable-personal-memory` 关闭 local memory-registry writes。
- Model-backed organization 使用 OpenRelix runtime config，而不是用户 global host default。默认 `model_cli` 是 Codex，配合 `codex exec --model` 使用 `gpt-5.4-mini`；`openrelix config --model-cli claude --claude-model sonnet` 会切到 Claude Code。用户可以用 `openrelix models` 检查本地 Codex catalog，用 `openrelix tokens --provider all` 查询合并 token usage。
- 强规则仍然属于 `AGENTS.md` 或项目文档。Local memory items 是 recall layer，不是必须一直生效规则的唯一来源。
- `scripts/build_codex_memory_summary.py` 会按 token budget 构建 compressed summary：合并重复 personal memories，忽略 legacy `MEMORY.md` task-group routes 的 injection，按 injection policy 选择 global/project context，按 priority、heat、freshness 排序，把 low-priority 和 on-demand/local entries 留在本地，并让 summary 适配 token budget。
- `scripts/sync_host_memory_summary.py` 只写 enabled host targets 中的 OpenRelix-managed blocks，保留 Codex / Claude Code native content。当前 Codex `[features].memories = false` 时，会报告 disabled，不更新 `memory_summary.md`。

## Daily Workflow

1. 先服从最近的 repo instructions。
2. 完成任务。
3. 如果结果可复用，在 active state root 写 sanitized task review。
4. 添加或更新对应 asset entry。
5. 如果已有 asset 对本次工作有实质帮助，追加 usage event。
6. 重建 overview 和 panel。

## 什么算资产

- Playbooks。
- Debugging recipes。
- Verification checklists。
- Reusable prompts。
- Templates。
- Scripts。
- Skills。
- Architecture 或 module maps。

## 不应该存什么

- Secrets 或 credentials。
- 用户数据或个人身份信息。
- 原始 proprietary task context。
- 大段内部 logs。
- 未脱敏 screenshots。
- 某台机器专属的绝对路径。
- 只有一次意义的聊天片段。
