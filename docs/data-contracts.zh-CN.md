# OpenRelix 数据契约

> 语言版本：[English](data-contracts.md) | 简体中文

这份文档记录 OpenRelix 面向贡献者的公开数据契约。它不是某个维护者本机 state 的 dump。所有示例都必须是合成、脱敏数据。

代码和测试仍然是 source of truth。本文是共享地图，帮助贡献者在不查看私有 runtime 文件的情况下做兼容改动。

## 存储边界

OpenRelix 有三类存储区：

| 区域 | 示例 | 是否进公开 repo | 说明 |
| --- | --- | --- | --- |
| Repo source | `scripts/`, `install/`, `templates/`, `docs/`, tests | 是 | 可复用逻辑、schema、脱敏示例 |
| External state root | `raw/`, `registry/`, `consolidated/`, `reports/`, `runtime/`, `log/` | 否 | 用户运行数据、生成产物、缓存、日志 |
| Host home | `CODEX_HOME`, `CLAUDE_HOME`, host-native memory 和 sessions | 否 | AI host 拥有；OpenRelix 只按显式 adapter 规则读取或更新 |

可复用代码中的路径解析应走 `scripts/asset_runtime.py`。公开 docs 和 fixtures 可以使用 `/tmp/openrelix-demo`、`~/Library/Application Support/openrelix` 或 `CODEX_HOME` 这类通用占位路径。

## State Root Layout

```text
<state-root>/
  raw/
    daily/<date>.json
    windows/<date>/<window_id>.json
  consolidated/
    daily/<date>/summary.json
    daily/<date>/summary.md
    daily/<date>/runs/
  knowledge/
    docs/<year>/<slug>.md
    runs/<run_id>/
  registry/
    assets.jsonl
    usage_events.jsonl
    memory_entries.jsonl
    memory_items.jsonl
    knowledge_candidates.jsonl
    knowledge_docs.jsonl
    curated_memory_pack.json
  reports/
    overview-data.json
    overview.md
    overview.csv
    panel.html
  runtime/
    config.json
    openrelix-index.sqlite3
    host-context/
      memory_summary.md
      curated-personal-memory-summary.md
  log/
```

`reports/`、`runtime/` 和 `log/` 是生成态或机器本地目录。不要提交真实文件。脱敏测试可以在 `tests/fixtures/sample-state/` 下提供一个小 fixture，但每个值都必须是人工合成。

## Raw Daily Contract

路径：

```text
raw/daily/<date>.json
```

用途：某一天采集到的 host activity，按 window 分组。

最小形态：

```json
{
  "date": "2026-04-28",
  "stage": "manual",
  "generated_at": "2026-04-28T10:10:00+08:00",
  "timezone": "Asia/Shanghai",
  "collection_source": "history",
  "activity_host": "codex",
  "window_count": 1,
  "windows": []
}
```

常见顶层字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `date` | string | capture date，格式 `YYYY-MM-DD` |
| `stage` | string | `manual`、`preview` 或 `final` |
| `generated_at` | string | ISO timestamp |
| `timezone` | string | 本地时区 |
| `collection_source` | string | `app_server`、`history`、`session` 等 |
| `activity_host` | string | `codex`、`claude` 或合并 host |
| `codex_profile_count` | number | 参与采集的 Codex profile 数 |
| `codex_profiles` | array | 脱敏 profile metadata |
| `host_counts` | object | 每个 host 的窗口/消息统计 |
| `collection_errors` | array | 脱敏采集错误 |
| `window_count` | number | `windows` 数量 |
| `excluded_window_count` | number | 被过滤窗口数量 |
| `review_like_window_count` | number | review-like 窗口数量 |
| `prompt_count` | number | prompt 总数 |
| `conclusion_count` | number | conclusion 总数 |
| `windows` | array | 已纳入整理的窗口 |
| `excluded_windows` | array | 被排除窗口的脱敏摘要 |
| `review_like_windows` | array | review-like 窗口的脱敏摘要 |

## Raw Window Contract

路径：

```text
raw/windows/<date>/<window_id>.json
```

window 对象常见字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `date` | string | window 所属日期 |
| `window_id` | string | 稳定窗口标识 |
| `cwd` | string | 工作目录，应脱敏或使用合成路径 |
| `ai_host` | string | `codex`、`claude` 等 |
| `originator` | string | host 来源 |
| `source` | string | 采集来源 |
| `started_at` | string | window 开始时间 |
| `session_file` | string | 脱敏 session 文件标识 |
| `thread_id` | string | host thread id 或合成值 |
| `resume_id` | string | resume id 或空 |
| `window_summary` | string | 窗口摘要 |
| `thread_title` | string | thread 标题 |
| `prompt_count` | number | prompts 数量 |
| `conclusion_count` | number | conclusions 数量 |
| `raw_conclusion_count` | number | 过滤前 conclusion 数量 |
| `review_like_window` | boolean | 是否像 review 窗口 |
| `review_related_window` | boolean | 是否与 review 相关 |
| `filtered_review_conclusion_count` | number | review 过滤后的 conclusion 数 |
| `conclusion_policy` | string | conclusion 选择策略说明 |
| `prompts` | array | prompt entries |
| `conclusions` | array | conclusion entries |

运行时可能出现的字段，如 `codex_home`、`codex_electron_user_data_path`、`codex_profile_source`、`app_server`、`claude_code`，公开文档只描述其语义，不放真实值。

`prompts[]`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ts` | string | timestamp |
| `local_time` | string | local timestamp |
| `text` | string | 脱敏 prompt 文本 |
| `turn_id` | string | Claude 等 host 可选字段 |

`conclusions[]`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `turn_id` | string | turn 标识 |
| `completed_at` | string | 完成时间 |
| `text` | string | 脱敏 conclusion |

## Consolidated Summary Contract

路径：

```text
consolidated/daily/<date>/summary.json
```

模型输出稳定字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `date` | string | summary 日期 |
| `day_summary` | string | 当日摘要 |
| `window_summaries` | array | 窗口级摘要 |
| `durable_memories` | array | 长期记忆候选 |
| `session_memories` | array | 工作记忆候选 |
| `low_priority_memories` | array | 低优先级记忆 |
| `global_context_memories` | array | 可进入 global context 的记忆 |
| `keywords` | array | 关键词 |
| `next_actions` | array | 下一步 |

`window_summaries[]` 建议公开字段：

- `window_id`
- `cwd`
- `window_title`
- `question_summary`
- `question_count`
- `conclusion_count`
- `keywords`
- `main_takeaway`
- `summary_pairs`

`summary_pairs[]` 使用：

```json
{
  "question": "What changed?",
  "conclusion": "The fixture now covers memory and asset registry rows."
}
```

运行诊断字段可以分组描述：`language`、`stage`、`generated_at`、`model_status`、`summary_generation`、`personal_memory_algorithm_version`、`compact_payload_source`、`learning_input_fingerprint`、`quality`、`selection_decision`、`last_run_model_status`。

## 知识候选契约

Canonical 路径：

```text
registry/knowledge_candidates.jsonl
```

用途：保存从 consolidated summary 或 curated memory 中发现的可复用知识候选，作为本地 review queue。它不读取、不保存 raw chat transcript。

候选状态机：

```text
candidate -> draft
candidate -> deferred
candidate -> rejected
deferred -> candidate
deferred -> draft
deferred -> rejected
```

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | integer | 契约版本，当前为 `1` |
| `algorithm_version` | integer | 候选抽取算法版本 |
| `candidate_id` | string | 稳定本地候选 id |
| `date` | string | 候选创建日期 |
| `decision` | string | `candidate`、`draft`、`deferred` 或 `rejected` |
| `knowledge_type` | string | `troubleshooting`、`decision`、`procedure`、`project_context` |
| `title` | string | 候选标题 |
| `summary` | string | source-safe 摘要，不是 raw transcript |
| `canonical_key` | string | `slug(project_key or scope):knowledge_type:slug(title)` |
| `source_fingerprint` | string | compact source input 的幂等 hash |
| `source_refs` | object | `summary_dates`、`window_ids`、`memory_ids`、`review_paths` |
| `project_key` / `project_label` | string | 默认项目级隔离；global 必须显式且少用 |
| `scope` | string | `project`、`global` 或 `user_private` |
| `sensitivity` | string | `public`、`internal`、`private` 或 `restricted` |
| `quality_score` | number | `0` 到 `1` 的质量门禁分 |
| `redaction_status` | string | 候选阶段通常是 `source_safe` |
| `model_status` | string | `not_run`、`success`、`retryable` 或 `poisoned` |
| `reason` | string | 晋升、延后或拒绝原因 |

候选不应自动进入 host context、`memory_entries.jsonl` 或 `assets.jsonl`。MVP 遇到证据弱、跨项目、隐私敏感、或只是临时任务状态时，应优先 defer 或 reject。

候选写入应遵循和知识文档 registry 一样的纪律：先加锁，写临时文件，校验 JSONL，再原子 rename。坏行应隔离到 run artifact，而不是破坏现有 registry。

## 知识文档 Registry 契约

Canonical registry 路径：

```text
registry/knowledge_docs.jsonl
```

文档正文路径：

```text
knowledge/docs/<year>/<slug>.md
```

运行产物路径：

```text
knowledge/runs/<run_id>/
```

用途：保存从 compact OpenRelix state 派生出来的本地知识文档草稿或已审核文档。每条 registry row 的 schema 在 `templates/knowledge-doc-schema.json`；LLM 改写 prompt 在 `templates/knowledge-doc-rewrite-prompt.md`；调用方只能把脱敏后的 compact JSON 交给 model runner。

文档状态机：

```text
draft -> reviewed
draft -> rejected
reviewed -> published
reviewed -> draft
reviewed -> rejected
published -> superseded
```

人工审核具有更高优先级。后续模型运行不能静默覆盖人工 rejected/reviewed 结果；如果新证据冲突，应创建新的 draft 或 conflict draft，并写明 `conflict_of_doc_ids`。

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | integer | 知识文档 schema 版本 |
| `algorithm_version` | integer | 改写/去重算法版本 |
| `doc_id` | string | 稳定本地 id |
| `version` | integer | 同一 `canonical_key` 下单调递增 |
| `status` | string | `draft`、`reviewed`、`published`、`superseded` 或 `rejected` |
| `knowledge_type` | string | MVP 类型：`troubleshooting`、`decision`、`procedure`、`project_context` |
| `title` / `summary` | string | 展示字段 |
| `body_path` | string | state-root 相对 Markdown 正文路径 |
| `body_sections` | object | Markdown 渲染的结构化来源 |
| `canonical_key` | string | 稳定去重 key；MVP 默认项目级 |
| `source_fingerprint` | string | compact input、schema、algorithm、prompt version 的 hash |
| `source_refs` | object | 只引用 summary/window/memory/review |
| `project_key` / `project_label` | string | 项目隔离键 |
| `scope` | string | `project`、`global` 或 `user_private` |
| `sensitivity` | string | 隐私分级 |
| `quality_score` | number | 本地质量门禁结果 |
| `reviewer_state` | string | `needs_review`、`reviewed` 或 `rejected` |
| `redaction_status` | string | `source_safe`、`prompt_safe`、`publish_safe` 或 `failed` |
| `model_status` | string | `success`、`retryable`、`poisoned` 或 `not_run` |
| `visibility` | object | `panel`、`default_search`、`host_context`、`trust_level` |
| `conflict_of_doc_ids` | array | 当前 draft 冲突的文档 |
| `created_at` / `updated_at` | string | ISO timestamp |

可见性策略：

| 状态 | Panel | 默认检索 | Host context |
| --- | --- | --- | --- |
| `draft` | 是 | 否 | 否 |
| `reviewed` | 是 | 否 | 否 |
| `published` | 是 | 是 | MVP 中仍为否 |
| `superseded` | 否 | 否 | 否 |
| `rejected` | 否 | 否 | 否 |

知识生成有三层脱敏门禁：candidate 创建前做 source-safe 裁剪，model runner 内做 prompt-safe sanitization，Markdown 或 index 暴露前做 publish-safe redaction。Registry writer 必须加锁并原子更新；模型超时、schema 校验失败或输出不可用时，只在 `knowledge/runs/<run_id>/` 记录失败产物，不更新 active `knowledge_docs.jsonl`。

MVP 不写 `runtime/host-context/memory_summary.md`、`CLAUDE.md`、`registry/memory_entries.jsonl` 或 raw collection 文件。它不承诺知识真值，只产出可追溯的本地草稿和证据引用。它也不承诺历史回填、跨项目合并、向量检索或自动发布。

当前 MVP CLI 入口：

```bash
openrelix knowledge build --date YYYY-MM-DD
openrelix knowledge list
openrelix knowledge status
openrelix knowledge review --doc-id DOC_ID
openrelix knowledge publish --doc-id DOC_ID
openrelix knowledge reject --doc-id DOC_ID
openrelix index search-knowledge "query" --status draft
```

`build` 读取 `consolidated/daily/<date>/summary.json` 和当前 memory registry，只写入 `registry/knowledge_candidates.jsonl`、`registry/knowledge_docs.jsonl`、`knowledge/docs/**` 和 `knowledge/runs/**`。
`review`、`publish`、`reject` 只更新 `knowledge_docs.jsonl` 与对应 Markdown 正文里的状态行；`published` 仍是本地可见，不进入 host context。

## Memory Registry Contract

路径：

```text
registry/memory_entries.jsonl
```

这是 OpenRelix memory 的 canonical registry。`memory_summary.md` 是编译产物，不是源头。

核心字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `date` | string | 记录日期 |
| `language` | string | `zh` 或 `en` |
| `source` | string | 来源，如 `nightly`、`review`、`manual` |
| `bucket` | string | 兼容 bucket |
| `scope` | string | `global`、`domain`、`project`、`repo`、`host`、`local` |
| `injection_policy` | string | `global_context`、`project_context`、`on_demand`、`local_only`、`never` |
| `project_key` | string | 项目标识 |
| `project_label` | string | 可读项目名 |
| `title` | string | memory title |
| `memory_type` | string | memory 分类 |
| `priority` | string | 优先级 |
| `value_note` | string | 价值说明 |
| `source_window_ids` | array | 来源 window ids |
| `keywords` | array | 关键词 |

建议补充字段：

- `memory_key`
- `source_systems`
- `source_dates`
- `evidence_contexts`
- `occurrence_count`
- `global_context_confidence`
- `storage_quality_score`
- `storage_quality_reason`
- `memory_algorithm_version`
- `source_review_path`
- `title_zh`
- `title_en`
- `value_note_zh`
- `value_note_en`
- `user_feedback`
- `user_pinned`

## Asset Candidate Contract

当前代码没有稳定的 `registry/asset_candidates.jsonl` canonical 写入点。它更像 review/assetization 阶段的候选决策，不应描述成当前持久化事实。

如果后续需要公开候选契约，建议字段：

- `date`
- `task`
- `domain`
- `repo`
- `source_review_path`
- `source_window_ids`
- `decision`
- `candidate_types`
- `confidence`
- `reuse_trigger`
- `evidence`
- `privacy_risk`
- `scope_decision`
- `user_confirmation`
- `no_asset_reason`
- `proposed_artifact_paths`
- `generated_asset_ids`
- `generated_memory_ids`

`candidate_types` 可使用 `memory`、`playbook`、`template`、`automation`、`skill`。

## Asset Registry Contract

路径：

```text
registry/assets.jsonl
```

常见字段：

- `id`
- `title`
- `type`
- `domain`
- `scope`
- `status`
- `created_at`
- `updated_at`
- `source_task`
- `source_review_path`
- `reuse_count`
- `minutes_saved_total`
- `value_note`
- `artifact_paths`
- `tags`
- `notes`

## Usage Event Contract

路径：

```text
registry/usage_events.jsonl
```

字段：

- `date`
- `asset_id`
- `task`
- `minutes_saved`
- `outcome`
- `note`

`note` 应继续兼容，因为模板和展示层都可能用 note 型描述。

## Curated Memory Pack Contract

路径：

```text
registry/curated_memory_pack.json
```

顶层字段：

- `schema_version`
- `source`
- `model_calls`
- `entry_count`
- `sections`
- `diagnostics`

推荐固定 sections：

- `user_profile`
- `stable_preferences`
- `operating_rules`
- `project_playbooks`
- `task_groups`
- `local_volatile_notes`

curated item 字段：

- `section`
- `canonical_key`
- `title`
- `value_note`
- `scope`
- `injection_policy`
- `project_label`
- `memory_type`
- `priority`
- `memory_key`
- `user_feedback`
- `evidence_count`
- `source_entry_ids`
- `source_memory_keys`
- `source_window_ids`
- `source_dates`
- `diagnostics`

diagnostics 可包括：

- `duplicate_clusters`
- `timeline_like_entries`
- `local_privacy_like_entries`
- `possible_cross_project_leakage`
- `truncation_markers`
- `malformed_lines`

## Active Context Contract

不要把 active context 写成独立源数据。公开契约应表达为：

- 源头是 `registry/memory_entries.jsonl`。
- active context 是编译后的 Markdown 产物。
- 可记录产物路径是 `runtime/host-context/memory_summary.md`，以及同步目标中的 managed block。
- overview 展示字段包括 `memory_policy_views`、`personal_memory_token_usage`、`context_memory_preview`。

预算字段建议公开：

- `target_tokens`
- `warn_tokens`
- `max_tokens`
- `global_memory_tokens`
- `project_memory_tokens`
- `personal_memory_tokens`

## Runtime Config Contract

最小运行时配置字段：

- `schema_version`
- `language`
- `memory_mode`
- `personal_memory_enabled`
- `codex_context_enabled`
- `activity_source`
- `activity_host`
- `model_cli`
- `codex_model`
- `claude_model`
- `memory_summary_max_tokens`
- `host_context_targets`

状态目录只写相对结构，不写真实用户路径：`raw/`、`raw/daily/`、`raw/windows/`、`registry/`、`consolidated/daily/`、`runtime/`、`reports/`、`reviews/`、`logs/`。

## Sample-State Fixture

最小结构：

```text
tests/fixtures/sample-state/
  README.md
  raw/
    daily/
      2026-04-28.json
    windows/
      2026-04-28/
        w-demo-codex.json
  consolidated/
    daily/
      2026-04-28/
        summary.json
  registry/
    memory_entries.jsonl
    assets.jsonl
    usage_events.jsonl
```

不要提交这些生成态目录：

- `reports/`
- `runtime/host-context/`
- `runtime/*.sqlite3`
- `logs/`
- `registry/curated_memory_pack.json`

测试应复制 fixture 到临时目录后再生成这些产物。

fixture 内容建议：

- `raw/daily` 使用 `stage: "manual"` 或 `stage: "final"`，不要为了测试新增公共运行时枚举。
- `raw/window` 保留 1 个 prompt、1 个 conclusion、1 个 synthetic cwd，例如 `/workspace/sample-project`，并包含 `raw_conclusion_count` 与 `filtered_review_conclusion_count`。
- `summary.json` 至少覆盖 1 个 `window_summaries[]`、1 个 `durable_memories[]`、关键词和 next action。
- `memory_entries.jsonl` 放 1 条 synthetic project memory，覆盖 `scope`、`injection_policy`、`project_key`、`source_window_ids`。
- `assets.jsonl` 和 `usage_events.jsonl` 各 1 条，用于验证 registry JSONL 与 usage 关联。
