# OpenRelix Data Contracts

> Languages: English | [简体中文](data-contracts.zh-CN.md)


This document captures the public, contributor-facing data contracts for OpenRelix. It is not a dump of one maintainer's local state. Examples must stay synthetic and sanitized.

The source of truth remains the code and tests. Treat this page as the shared map that lets contributors make compatible changes without inspecting private runtime files.

## Storage Boundary

OpenRelix has three storage zones:

| Zone | Examples | Public repo? | Notes |
| --- | --- | --- | --- |
| Repo source | `scripts/`, `install/`, `templates/`, `docs/`, tests | Yes | Reusable logic, schemas, sanitized examples |
| External state root | `raw/`, `registry/`, `consolidated/`, `reports/`, `runtime/`, `log/` | No | User runtime data, generated artifacts, caches, logs |
| Host home | `CODEX_HOME`, `CLAUDE_HOME`, host-native memory and sessions | No | Owned by the AI host; OpenRelix reads or updates only through explicit adapter rules |

All paths in reusable code should be resolved through `scripts/asset_runtime.py`. Public docs and fixtures may use generic placeholder paths such as `/tmp/openrelix-demo`, `~/Library/Application Support/openrelix`, or `CODEX_HOME`.

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
  registry/
    assets.jsonl
    usage_events.jsonl
    memory_entries.jsonl
    memory_items.jsonl
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

`reports/`, `runtime/`, and `log/` are generated or machine-local. Do not commit real files from those directories. Sanitized tests may include a small fixture under `tests/fixtures/sample-state/` only when every value is artificial.

## Raw Daily Contract

Path:

```text
raw/daily/<date>.json
```

Purpose: one day's collected host activity, grouped by window.

Minimum shape:

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

Common daily top-level fields:

| Field | Type | Notes |
| --- | --- | --- |
| `date` | string | ISO collection date |
| `stage` | string | Collection stage such as `manual` or `final`; tests may use synthetic values only when documented |
| `generated_at` | string | When the collector wrote the file |
| `timezone` | string | Local timezone used for daily grouping |
| `collection_source` | string | `history`, `app-server`, `auto`, `claude-history`, or compatible source label |
| `activity_host` | string | `codex`, `claude`, or `all` |
| `codex_profile_count` | integer | Number of Codex profiles inspected when available |
| `codex_profiles` | array | Sanitized profile metadata; do not publish real home paths |
| `host_counts` | object | Per-host window counts |
| `collection_errors` | array | Sanitized warnings or failures |
| `window_count` | integer | Included windows |
| `excluded_window_count` | integer | Windows excluded by filtering |
| `review_like_window_count` | integer | Review-like windows detected |
| `prompt_count` | integer | Total included prompts |
| `conclusion_count` | integer | Total included conclusions |
| `windows` | array | Included raw window objects |
| `excluded_windows` | array | Sanitized excluded-window metadata |
| `review_like_windows` | array | Sanitized review-like window metadata |

Common window fields:

| Field | Type | Required for | Notes |
| --- | --- | --- | --- |
| `date` | string | indexing, panel | ISO date for the collected day |
| `window_id` | string | all downstream joins | Stable per host window or session for that day |
| `ai_host` | string | host display and resume | `codex` or `claude`; absent older rows default to `codex` |
| `cwd` | string | project grouping | Use generic or redacted paths in fixtures and docs |
| `originator` | string | diagnostics | Example: `codex_cli`, `codex_app_server`, `claude_code` |
| `source` | string | diagnostics | Example: `history`, `app-server`, `claude-history` |
| `started_at` | string | sorting | ISO-like timestamp with timezone when available |
| `session_file` | string | local debugging | State-local or host-local path; never publish real paths |
| `thread_id` | string | app-server resume | Optional |
| `session_id` | string | host resume | Optional |
| `resume_id` | string | panel action | Optional, host-specific |
| `window_summary` | string | display/fallback | Optional host-provided short summary |
| `thread_title` | string | display/fallback | Optional app-server or host title |
| `prompt_count` | integer | metrics | Count after tool/system filtering |
| `conclusion_count` | integer | metrics | Count after review-like filtering |
| `raw_conclusion_count` | integer | diagnostics | Count before conclusion filtering |
| `review_like_window` | boolean | filtering | Whether the window is primarily a review workflow |
| `review_related_window` | boolean | filtering | Whether the window relates to review but still has usable prompts |
| `filtered_review_conclusion_count` | integer | diagnostics | Filtered conclusion count |
| `conclusion_policy` | string | diagnostics | Example: `included` |
| `prompts` | array | fallback summaries | User-facing questions or requests |
| `conclusions` | array | fallback summaries | Final assistant answers or useful conclusions |
| `app_server` | object | diagnostics | Optional, sanitized Codex app-server metadata |
| `claude_code` | object | diagnostics | Optional, sanitized Claude Code metadata |

Prompt item:

```json
{
  "turn_id": "turn-demo-1",
  "ts": "2026-04-28T10:00:00+08:00",
  "local_time": "2026-04-28T10:00:00+08:00",
  "text": "设计一个脱敏样例 state"
}
```

Conclusion item:

```json
{
  "turn_id": "turn-demo-1",
  "completed_at": "2026-04-28T10:05:00+08:00",
  "text": "样例 state 应覆盖 raw、consolidated、registry 和 index。"
}
```

Do not store raw tool outputs, raw logs, tokens, cookies, account data, or proprietary snippets in public examples.

## Raw Window Contract

Path:

```text
raw/windows/<date>/<window_id>.json
```

Purpose: one window record extracted from the daily file for direct lookup and panel source references.

It should contain the same window object used in `raw/daily/<date>.json`. Downstream code should tolerate older records that lack newer fields, but new collectors should include `ai_host`, `prompt_count`, `conclusion_count`, `prompts`, and `conclusions`.

## Consolidated Summary Contract

Path:

```text
consolidated/daily/<date>/summary.json
```

Purpose: model-backed or fallback daily summary.

The model output is constrained by `templates/nightly-summary-schema.json`. Contributor-facing fields are:

| Field | Type | Notes |
| --- | --- | --- |
| `date` | string | Same collection date |
| `day_summary` | string | Human-readable day overview |
| `window_summaries` | array | Per-window summary rows |
| `keywords` | array | Day-level keywords |
| `next_actions` | array | Non-binding follow-up suggestions |
| `durable_memories` | array | Legacy model output bucket; normalized before host injection |
| `session_memories` | array | Legacy model output bucket; normalized before host injection |
| `low_priority_memories` | array | Local or low-signal output |
| `global_context_memories` | array | Evidence-rich cross-window memory candidates |

Window summary fields:

| Field | Type | Notes |
| --- | --- | --- |
| `window_id` | string | Joins to raw window |
| `cwd` | string | Project grouping |
| `window_title` | string | Short title for panel and index |
| `question_summary` | string | Condensed user need |
| `question_count` | integer | Number of prompts represented |
| `conclusion_count` | integer | Number of conclusions represented |
| `keywords` | array | Search and grouping |
| `main_takeaway` | string | Highest-signal result |
| `summary_pairs` | array | Question/conclusion pairs for traceability |

The current implementation still accepts older `stage`, `summary_stage`, `model_status`, and `summary_generation` diagnostics. Use them for selection and fallback behavior, not as user-facing facts.

Common diagnostic fields:

| Field | Notes |
| --- | --- |
| `language` | Output language for generated text |
| `stage` / `summary_stage` | Manual, preliminary, final, or compatibility stage |
| `generated_at` | Summary generation time |
| `model_status` / `last_run_model_status` | Model run status |
| `summary_generation` | Lightweight, fallback, or model-backed generation label |
| `personal_memory_algorithm_version` | Memory algorithm version used for fingerprinting |
| `compact_payload_source` | Input compaction source |
| `learning_input_fingerprint` | Skip-if-unchanged fingerprint |
| `quality` / `selection_decision` | Summary quality and selection metadata |

## Window Task Summary Contract

Path:

```text
consolidated/task_summaries/<from>_<to>.json
```

Purpose: model-backed project task clusters built from existing `window_summaries`. This layer does not read raw transcripts; it lets already-organized windows gain a business-level "parallel tasks" summary after install or upgrade.

The model output is constrained by `templates/window-task-summary-schema.json`. Contributor-facing fields are:

| Field | Type | Notes |
| --- | --- | --- |
| `schema_version` | integer | Contract version for the task summary artifact |
| `task_cluster_algorithm_version` | integer | Algorithm version used for migration and cache invalidation |
| `date_range` | object | Inclusive source summary date range |
| `source_summary_dates` | array | Daily summaries that contributed windows |
| `source_window_ids` | array | All compact input window ids; newer artifacts tombstone older overlapping clusters for these windows |
| `source_fingerprint` | string | Fingerprint of the compact window-summary input |
| `project_task_clusters` | array | Model task clusters |
| `model_status` | string | `completed` or `failed`; failed artifacts can tombstone stale clusters |
| `error` | string | Sanitized failure category when `model_status=failed` |

Task cluster fields:

| Field | Type | Notes |
| --- | --- | --- |
| `cluster_id` | string | Stable local identifier for grouping windows |
| `project_label` | string | Project display label inferred from source windows |
| `cwd` | string | Source working directory when stable |
| `task_title` | string | User-visible business/feature task name |
| `task_summary` | string | Short grouping rationale |
| `source_window_ids` | array | Window ids from `window_summaries`; invalid ids are dropped |
| `status_tags` | array | Process state such as compile, verification, or commit |
| `confidence` | string | `high`, `medium`, or `low`; panel grouping uses high/medium and falls back for low |

## Memory Registry Contract

Canonical path:

```text
registry/memory_entries.jsonl
```

Compatibility path:

```text
registry/memory_items.jsonl
```

`memory_entries.jsonl` is the canonical personal-memory registry. `memory_items.jsonl` is a legacy fallback for migration and old state roots.

Recommended row fields:

| Field | Type | Notes |
| --- | --- | --- |
| `memory_id` or `id` | string | Stable id when known; otherwise code can derive a fingerprint |
| `date` | string | First or latest source date |
| `updated_at` | string | Optional ISO timestamp |
| `language` | string | `zh` or `en` when display language matters |
| `source` | string | Example: `canonical`, `nightly_codex`, `manual`, `memory_review` |
| `scope` | string | `global`, `project`, `repo`, `local`, or compatible legacy values |
| `injection_policy` | string | `global_context`, `project_context`, `on_demand`, `local_only`, `never` |
| `project_key` | string | Stable project key for project-scoped rows |
| `project_label` | string | Display label |
| `bucket` | string | Compatibility grouping such as `durable`, `session`, `low_priority` |
| `memory_type` | string | `preference`, `procedural`, `semantic`, `episodic`, `task`, `rule`, `workflow` |
| `priority` | string | `high`, `medium`, `low` |
| `title` | string | Short display title |
| `value_note` | string | The reusable rule or fact |
| `source_window_ids` | array | Evidence window ids |
| `source_dates` | array | Evidence dates |
| `occurrence_count` | integer | Repeated evidence count |
| `evidence_contexts` | array | Short sanitized reasons; not raw transcript |
| `keywords` | array | Search terms |

Injection policy is the important boundary:

| Policy | Default behavior |
| --- | --- |
| `global_context` | Eligible for bounded host context within global budget |
| `project_context` | Eligible for bounded host context within project budget |
| `on_demand` | Searchable in registry/index, not injected by default |
| `local_only` | Local display/search only |
| `never` | Do not inject and avoid surfacing unless explicitly inspecting local data |

Do not use legacy `durable/session` labels as the product model for new docs. They exist for compatibility; current behavior is governed by `scope`, `injection_policy`, and `priority`.

## Asset Registry Contract

Path:

```text
registry/assets.jsonl
```

Purpose: stable reusable assets that a human or agent can intentionally reuse.

Recommended row fields:

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Stable asset id |
| `title` | string | Display title |
| `type` | string | `playbook`, `skill`, `template`, `automation`, or related durable asset type |
| `domain` | string | Area such as `openrelix`, `general`, `frontend` |
| `scope` | string | `personal`, `repo`, `project`, or `global` |
| `status` | string | `active`, `draft`, `archived` |
| `created_at` | string | ISO date |
| `updated_at` | string | ISO date |
| `source_task` | string | Sanitized task id or summary |
| `source_review_path` | string | Review path when available |
| `reuse_count` | integer | Count of recorded reuse events |
| `minutes_saved_total` | integer | Estimated time saved |
| `value_note` | string | Why this asset matters |
| `artifact_paths` | array | Local or repo-relative artifact paths |
| `tags` | array | Search/grouping tags |

Only long-term reusable assets belong here. One-off task status belongs in a review or local note, not in `assets.jsonl`.

## Asset Candidate Contract

There is currently no stable canonical `registry/asset_candidates.jsonl` writer. Treat this as a candidate/review-stage contract, not as current persisted repo truth.

Suggested candidate fields when this becomes a durable workflow:

| Field | Type | Notes |
| --- | --- | --- |
| `date` | string | Candidate creation date |
| `task` | string | Sanitized task summary |
| `domain` | string | Domain or project area |
| `repo` | string | Optional repo label, not a private path |
| `source_review_path` | string | Review artifact when available |
| `source_window_ids` | array | Evidence window ids |
| `decision` | string | `accept`, `defer`, `reject`, or `needs_user_confirmation` |
| `candidate_types` | array | `memory`, `playbook`, `template`, `automation`, `skill` |
| `confidence` | string | Human-readable confidence or score |
| `reuse_trigger` | string | When this asset should be reused |
| `evidence` | array | Sanitized reasons, not raw transcript |
| `privacy_risk` | string | Privacy classification |
| `scope_decision` | string | Proposed asset scope |
| `user_confirmation` | string | Whether the user approved it |
| `no_asset_reason` | string | Why rejected or deferred |
| `proposed_artifact_paths` | array | Repo-relative or generic local paths |
| `generated_asset_ids` | array | Created asset ids |
| `generated_memory_ids` | array | Created memory ids |

Do not let an asset candidate enter `assets.jsonl` or host context just because it was detected. Human confirmation or an explicit policy gate should decide.

## Usage Event Contract

Path:

```text
registry/usage_events.jsonl
```

Purpose: evidence that an asset helped in a later task.

Recommended row fields:

| Field | Type | Notes |
| --- | --- | --- |
| `event_id` | string | Stable event id |
| `asset_id` | string | Joins to `assets.jsonl` |
| `date` | string | ISO date |
| `task` | string | Sanitized task summary |
| `minutes_saved` | integer | Estimated impact |
| `outcome` | string | Short result |
| `source_window_id` | string | Optional evidence reference |

Usage events should not include raw user data or private logs. They prove reuse, not transcript content.

## Curated Pack Contract

Paths:

```text
registry/curated_memory_pack.json
runtime/host-context/curated-personal-memory-summary.md
```

Purpose: deterministic review artifact generated from `registry/memory_entries.jsonl`.

The curated pack is non-invasive: it does not replace the active host summary and does not change injection behavior by itself.

Main fields:

| Field | Type | Notes |
| --- | --- | --- |
| `schema_version` | integer | Current curated pack schema |
| `source` | string | Input label, normally `registry/memory_entries.jsonl` |
| `model_calls` | integer | Should be `0` for deterministic builder |
| `entry_count` | integer | Parsed memory rows |
| `sections` | object | Grouped profile, preferences, rules, playbooks, task groups, local volatile notes |
| `diagnostics` | object | Duplicate clusters, redaction, timeline-like entries, malformed lines |
| `artifact` | object | Output metadata |

Section keys are stable:

```text
user_profile
stable_preferences
operating_rules
project_playbooks
task_groups
local_volatile_notes
```

Curated item fields commonly include `section`, `canonical_key`, `title`, `value_note`, `scope`, `injection_policy`, `project_label`, `memory_type`, `priority`, `memory_key`, `user_feedback`, `evidence_count`, `source_entry_ids`, `source_memory_keys`, `source_window_ids`, `source_dates`, and `diagnostics`.

Diagnostics commonly include `duplicate_clusters`, `timeline_like_entries`, `local_privacy_like_entries`, `possible_cross_project_leakage`, `truncation_markers`, and `malformed_lines`.

## Active Host Context Contract

Path inside state root:

```text
runtime/host-context/memory_summary.md
```

Host targets:

```text
CODEX_HOME/memories/memory_summary.md
CLAUDE_HOME/CLAUDE.md
```

Purpose: bounded summary compiled from eligible memory entries and written into OpenRelix-managed blocks in each enabled host target.

This is a compiled artifact, not source data. The source of truth is `registry/memory_entries.jsonl`; the active host context should be reproducible from registry rows, config, and compiler policy.

Rules:

- Preserve host-owned content outside OpenRelix markers.
- Respect `memory_mode` and host feature flags.
- Include only eligible `global_context` and `project_context` entries within token budgets.
- Keep `on_demand`, `local_only`, `never`, and low-priority rows out of default host context.
- Never write a project repo file just to inject personal memory.

Useful budget/config fields to surface in generated diagnostics include `target_tokens`, `warn_tokens`, `max_tokens`, `global_memory_tokens`, `project_memory_tokens`, and `personal_memory_tokens`.

## Runtime Config Contract

Path:

```text
runtime/config.json
```

Minimum contributor-facing fields:

| Field | Type | Notes |
| --- | --- | --- |
| `schema_version` | integer | Runtime config schema |
| `language` | string | `zh` or `en` |
| `memory_mode` | string | `integrated`, `local-only`, or `off` |
| `personal_memory_enabled` | boolean | Whether local personal memory writes are enabled |
| `codex_context_enabled` | boolean | Whether Codex host context sync is enabled |
| `activity_source` | string | `history`, `app-server`, or `auto` |
| `activity_host` | string | `codex`, `claude`, or `all` |
| `model_cli` | string | `codex` or `claude` |
| `codex_model` | string | Internal Codex model for OpenRelix jobs |
| `claude_model` | string | Internal Claude model for OpenRelix jobs |
| `memory_summary_max_tokens` | integer | Supported host-context budget max |
| `host_context_targets` | array | Enabled host context targets |

## Overview Data Contract

Path:

```text
reports/overview-data.json
```

Purpose: stable renderer input for `reports/panel.html` and report files.

Current contract validation lives in `scripts/openrelix_overview/contract.py`. Required top-level keys include:

```text
schema_version, language, generated_at, summary, metrics, mix,
assets, reviews, usage_events, summary_terms, summary_term_views,
pipeline_status, token_usage, window_overview, window_overview_views,
memory_registry, memory_policy_views, nightly_memory_views,
codex_native_memory, codex_native_memory_counts,
claude_native_memory, claude_native_memory_counts
```

Renderer code should consume `overview-data.json` rather than rereading raw JSONL, host memory files, or raw windows.

## Fixture Contract

Sanitized sample state lives under:

```text
tests/fixtures/sample-state/
```

Rules for fixtures:

- Use artificial dates, ids, project names, and paths.
- Keep paths generic, for example `/tmp/openrelix-demo`.
- Include enough data for raw window lookup, daily summary, registry search, and curated pack validation.
- Do not include real local state, real host transcripts, account names, internal URLs, tokens, screenshots, or generated panel files.

After changing fixture shape, run:

```bash
python3 -m unittest tests/test_sample_state_fixture.py
```
