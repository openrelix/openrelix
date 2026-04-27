# Metric Dictionary

## Core metrics

- `total_assets`: count of assets in the active state's `registry/assets.jsonl`
- `active_assets`: count of assets with `status = active`
- `task_reviews`: count of review markdown files under the active state's `reviews/`
- `tracked_usage_events`: count of rows in the active state's `registry/usage_events.jsonl`
- `tracked_minutes_saved`: estimated minutes saved, inferred from explicit usage events, missing-minute usage events, and recent work-window matches. Raw `minutes_saved` in usage events is treated as strong evidence, but it is no longer the only source.
- `durable_memories`: count of current durable memories after grouping repeated rows from `registry/memory_items.jsonl`
- `session_memories`: count of current session memories after grouping repeated rows from `registry/memory_items.jsonl`
- `low_priority_memories`: count of current low-priority memories after grouping repeated rows from `registry/memory_items.jsonl`

## Asset fields

- `id`: stable unique identifier
- `title`: readable asset name
- `type`: `playbook`, `skill`, `template`, `automation`, `knowledge_card`, `review`
- `domain`: `general`, `android`, `ios`, `web`, `backend`, `planning`, `collaboration`, or another durable area
- `scope`: `personal`, `repo`, `team`
- `status`: `active`, `draft`, `retired`
- `created_at`: `YYYY-MM-DD`
- `updated_at`: `YYYY-MM-DD`
- `source_task`: the task or thread that produced the asset
- `reuse_count`: manual running count when known
- `minutes_saved_total`: manual running total when known
- `estimated_value_score`: automatic 0-100 reuse value score used by the high-value asset panel
- `estimated_minutes_saved`: automatic minute estimate used for trend and ranking; it does not require manual user input
- `value_evidence_count`: explicit usage events plus recent work-window matches used as reuse evidence
- `value_note`: concise note explaining why the asset matters
- `artifact_paths`: local files that embody the asset

## Usage event fields

- `date`: `YYYY-MM-DD`
- `asset_id`: asset identifier
- `task`: task or issue label
- `minutes_saved`: optional recorded minutes saved on that reuse; if absent or zero, the dashboard estimates value from task text and asset metadata
- `note`: short evidence note

## Memory registry view

- The dashboard treats `registry/memory_items.jsonl` as a nightly log, then groups rows into a current memory view.
- Grouping key: `bucket + memory_type + normalized title` with `value_note` as fallback when title is empty.
- `created_at` in the dashboard memory view means the first date that grouped memory appeared in the log.
- `updated_at` in the dashboard memory view means the most recent date that grouped memory appeared in the log.

## Codex native memory view

- The dashboard also reads the configured Codex home, usually `~/.codex/memories/memory_summary.md`, and shows the `What's in Memory` topic items as a parallel native-memory view.
- This view is meant to represent what Codex itself has in its user-level memory layer, rather than what the nightly asset pipeline inferred afterward.
- In `codex-context` mode, routine refresh, review, and nightly jobs may regenerate the bounded `memory_summary.md`; they should not write raw windows or the full local registry into Codex native memory files.
- The native-memory section keeps user profile, preferences, and general tips in the source file; the panel focuses on the topic entries that are easiest to compare with nightly memory.
- In practice: native memory is closer to long-lived rules and rollout summaries, while the nightly registry is closer to recent task memory with source-window traceability.

## Reporting advice

- Prefer trend and impact metrics over activity metrics.
- Treat reuse value as evidence-weighted estimation: explicit reuse events are strongest, recent window matches are weaker, and type/recency affect the 0-100 value score as potential value rather than adding estimated saved minutes directly.
- A small number of high-reuse assets is more valuable than a large number of low-quality notes.
- Link summaries to specific assets and reviews whenever possible.
- Treat the repo as automation source and the state root as user data.
