# OpenRelix System Overview

Local-first memory and asset system for AI coding agents

## Goal

Build a local-first system that helps AI coding agents improve over time without turning one user's machine layout or one AI host into the product.

This repository serves two parallel goals:

1. Preserve durable preferences, playbooks, and reusable workflow assets.
2. Turn those assets into visible output instead of leaving them trapped in chat history.

GitHub project page: [openrelix/openrelix](https://github.com/openrelix/openrelix). Stars are welcome if the project is useful to you.

## Layering

### 1. Host adapter layer

- Current preview locations: the user's `~/.codex/` directory and optional `~/.claude/` directory
- Purpose: host-specific config, optional global instructions, local memories, and user-level installed skills
- Current preview examples: `~/.codex/config.toml`, `~/.codex/AGENTS.md`, `~/.codex/skills/`, `~/.claude/CLAUDE.md`
- Ownership: the AI host owns its native memory/config files. In the default `integrated` mode this repo writes only a bounded shared summary for context injection; full local registries, reviews, raw windows, and reports stay in the state root.

### 2. Repo source layer

- Location: this repository
- Purpose: canonical skills, templates, installer logic, and automation source code
- Examples: `.agents/skills/`, `install/`, `ops/launchd/`, `scripts/`, `templates/`

### 3. Runtime state layer

- Location: installer-managed state root outside the repo by default
- Purpose: user data, collected history, nightly output, reports, caches, and logs
- Examples: `registry/`, `reviews/`, `raw/`, `consolidated/`, `reports/`, `runtime/`, `log/`

## Skill loading model

- Repo-local skills are automatically discoverable when the active AI host supports repo-local skills. The current preview adapter targets Codex discovery.
- If the same skills should be available from any repository, install them into the user-level skill root for the active host. The current preview installer uses the Codex skill root.
- Hooks are not the primary skill discovery path. They are optional lifecycle automation.

## Memory layering

- Host-native memory is owned by the host. Codex native memory lives under `~/.codex/memories/`; Claude Code native context can include `~/.claude/CLAUDE.md`.
- This repo treats host-native memory as an optional bounded context target and upstream signal. The panel can display Codex and Claude Code native memory alongside local nightly memories, while OpenRelix syncs one compressed personal-memory summary into enabled host contexts.
- This repo's own memory layer lives in the active state root. Memory System v2 can read the canonical `registry/memory_entries.jsonl` first and still falls back to the legacy `registry/memory_items.jsonl`; reviews, raw windows, consolidated summaries, and generated reports stay alongside it.
- Generated memories pass a storage-quality gate before entering the visible registry: reusable rules, stable preferences, debugging paths, workflow defaults, and module mappings are favored; greetings, login-only windows, repeated no-conclusion windows, one-off artifacts, and vague notes are dropped or demoted to low priority.
- Host context injection is scoped: memory entries whose effective `injection_policy` is `global_context` or `project_context` can enter the shared bounded summary. Project and repo memories keep their project label/key in the compact line so the model sees their applicability boundary. Legacy/nightly synthesis rows are not promoted into host context without canonical, manual, approved, or storage-quality-gated evidence. Domain, host-native, local-only, never-inject, and low-priority memories remain available to the local registry/index without entering host context by default.
- Personal-memory algorithm changes are versioned. Incremental installs compare the stored version in `runtime/config.json` with the code version, mark `runtime/memory-migration.json` pending when older local memory exists, and let normal refresh run a bounded recent-window migration instead of silently reusing stale summaries.
- A rebuildable SQLite sidecar index lives under `runtime/openrelix-index.sqlite3` for memory and window lookup. It is derived from `raw/`, `registry/`, and `consolidated/`; deleting it must not delete or corrupt the source memory records.
- The default memory mode is `integrated`: local personal memory is recorded once, and a bounded summary is injected into enabled host-native contexts. `--record-memory-only` pins strict local-only behavior, and `--disable-personal-memory` turns off local memory-registry writes.
- Model-backed organization uses OpenRelix runtime config rather than the user's global host default. The default `model_cli` is Codex with `gpt-5.4-mini` passed through `codex exec --model`; `openrelix config --model-cli claude --claude-model sonnet` switches consolidation to Claude Code through `claude -p`. Users can inspect the current local Codex catalog with `openrelix models` and query merged token usage with `openrelix tokens --provider all`.
- Required rules still belong in `AGENTS.md` or project docs. Local memory items are a recall layer, not the only source for behavior that must always apply.
- `scripts/build_codex_memory_summary.py` builds a compressed summary with a token budget: merge duplicate personal memories, de-dupe against already-present host summary bullets with host-native memory taking priority, prioritize eligible global/project durable / session items, keep low-priority and on-demand/local entries local, and fit the injected summary around a configurable token budget. `scripts/sync_host_memory_summary.py` writes that one shared summary to enabled host targets, but reports Codex as disabled instead of writing `memory_summary.md` when `[features].memories = false`. The default maximum is 8K tokens, target / warning budgets are derived automatically from that max, and there is no fixed item cap for personal-memory candidates. Users can change the max through `openrelix config --memory-summary-max-tokens <tokens>` within the supported 2000-20000 range. Strict local-only runs keep it out of host-native context.

## Daily workflow

1. Use the nearest repo instructions first.
2. Solve the task.
3. If the result is reusable, write a sanitized task review in the active state root.
4. Add or update the corresponding asset entry.
5. If an existing asset materially helped, append a usage event.
6. Rebuild the overview and panel.

## What qualifies as an asset

- Playbooks
- Debugging recipes
- Verification checklists
- Reusable prompts
- Templates
- Scripts
- Skills
- Architecture or module maps

## What should not be stored

- Secrets or credentials
- User data or personally identifiable data
- Raw internal logs
- Unredacted incident details
- Large proprietary code excerpts unless there is an explicit local-only reason
- One-off chat content with no reuse value

## Visualization

The overview should emphasize:

- Efficiency: reuse events, tracked minutes saved, and repeat-task speedups
- Knowledge capital: total reusable assets, growth by month, and type/domain mix
- Business impact: assets tied to delivery, debugging, review quality, or risk reduction
- Influence: assets promoted from personal use to repo or team use

## Optional future extension

If a third-party memory layer is needed later, prefer a local or self-hosted deployment first. Keep cloud memory optional and behind explicit privacy review.
