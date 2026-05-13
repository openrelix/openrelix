# 指标字典

> 语言版本：[English](metric-dictionary.md) | 简体中文

## 核心指标

- `total_assets`：当前 state 的 `registry/assets.jsonl` 中资产总数。
- `active_assets`：`status = active` 的资产数量。
- `task_reviews`：当前 state 的 `reviews/` 下 review markdown 文件数量。
- `tracked_usage_events`：当前 state 的 `registry/usage_events.jsonl` 行数。
- `tracked_minutes_saved`：估算节省分钟数，来自显式 usage events、缺少分钟数的 usage events，以及近期 work-window 匹配。usage event 中的原始 `minutes_saved` 是强证据，但不再是唯一来源。
- `durable_memories`：从 `registry/memory_entries.jsonl` 合并重复行后得到的当前长期记忆数量。
- `session_memories`：合并重复行后的当前工作记忆数量。
- `low_priority_memories`：合并重复行后的当前低优先级记忆数量。

## Asset 字段

- `id`：稳定唯一标识。
- `title`：可读资产名。
- `type`：`playbook`、`skill`、`template`、`automation`、`knowledge_card`、`review`。
- `domain`：`general`、`android`、`ios`、`web`、`backend`、`planning`、`collaboration` 或其他稳定领域。
- `scope`：`personal`、`repo`、`team`。
- `status`：`active`、`draft`、`retired`。
- `created_at`：`YYYY-MM-DD`。
- `updated_at`：`YYYY-MM-DD`。
- `source_task`：产出资产的任务或 thread。
- `reuse_count`：已知时的人工累计复用次数。
- `minutes_saved_total`：已知时的人工累计节省时间。
- `estimated_value_score`：高价值资产面板使用的自动 0 到 100 复用价值分。
- `estimated_minutes_saved`：趋势和排序使用的自动分钟估计，不要求用户手工录入。
- `value_evidence_count`：作为复用证据的显式 usage events 与近期 work-window 匹配数量。
- `value_note`：说明资产价值的简短文字。
- `artifact_paths`：体现该资产的本地文件路径。

## Usage Event 字段

- `date`：`YYYY-MM-DD`。
- `asset_id`：资产标识。
- `task`：任务或 issue 标签。
- `minutes_saved`：本次复用记录的可选节省分钟数；缺失或为 0 时，dashboard 会从任务文本和资产元数据估值。
- `note`：简短证据说明。

## Memory Registry View

- dashboard 把 `registry/memory_entries.jsonl` 当作 canonical memory log，再把行合并成当前 memory view。
- 分组键：`bucket + memory_type + normalized title`，当 title 为空时用 `value_note` fallback。
- dashboard memory view 中的 `created_at` 表示该组 memory 第一次出现在 log 中的日期。
- `updated_at` 表示该组 memory 最近一次出现在 log 中的日期。
- 长期和工作 memory cards 按 7 天 heat 排序，不只按更新时间排序。
- `usage_frequency` 仍作为兼容字段保留，但 UI 标注为 heat。它只统计 7 天窗口内直接 source-window references，加上同一 memory 的近期 synthesis dates；不再用 memory title、keywords 或 notes 去 fuzzy-match 近期窗口摘要。
- heat window 限制在最新 7 个 daily captures，以保证性能稳定；dashboard refresh 不调用 LLM。

## Host Native Memory Views

- dashboard 也读取已配置的 host-native context files：Codex 通常使用 `~/.codex/memories/memory_summary.md`，Claude Code 使用 `~/.claude/CLAUDE.md` 中的 OpenRelix managed block。
- 这些视图代表每个 host 能读到的 user-level context layer，而不是 nightly asset pipeline 事后推断出的全部内容。
- 在 `integrated` mode 下，routine refresh、review 和 nightly jobs 可以为启用的 host contexts 重新生成一份 shared bounded summary；它们不应把 raw windows 或完整 local registry 写入 host native memory files。
- native-memory section 保留 source file 中的 user profile、preferences 和 general tips；panel 聚焦最容易与 nightly memory 对照的话题条目。
- 实践上，native memory 更接近长期规则和 rollout summaries；nightly registry 更接近带 source-window traceability 的近期任务记忆。

## Reporting 建议

- 优先看 trend 和 impact metrics，而不是单纯 activity metrics。
- 把 reuse value 当作证据加权估计：显式 reuse events 最强，近期 window matches 较弱，type 和 recency 影响 0 到 100 value score，但不会直接叠加 estimated saved minutes。
- 少量高复用资产比大量低质量 notes 更有价值。
- summaries 尽量链接到具体 assets 和 reviews。
- repo 是 automation source，state root 是 user data。
