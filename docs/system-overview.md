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

- Current preview location: the user's `~/.codex/` directory
- Purpose: host-specific config, optional global instructions, local memories, and user-level installed skills
- Current preview examples: `~/.codex/config.toml`, `~/.codex/AGENTS.md`, `~/.codex/skills/`
- Ownership: the AI host owns its native memory/config files. In the current Codex adapter's default `integrated` mode this repo writes only a bounded `memory_summary.md` for context injection; full local registries, reviews, raw windows, and reports stay in the state root.

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

- Host-native memory is owned by the host. In the current Codex adapter, native memory lives under `~/.codex/memories/`.
- This repo treats host-native memory as an optional bounded context target and upstream signal. The panel can display native memory alongside local nightly memories, while the Codex adapter can sync a compressed summary into Codex home for context injection.
- This repo's own memory layer lives in the active state root, mainly `registry/memory_items.jsonl`, `reviews/`, `raw/`, `consolidated/`, and generated `reports/`.
- A rebuildable SQLite sidecar index lives under `runtime/openrelix-index.sqlite3` for memory and window lookup. It is derived from `raw/`, `registry/`, and `consolidated/`; deleting it must not delete or corrupt the source memory records.
- The current adapter's default memory mode is `integrated`: local personal memory is recorded, and a bounded summary is injected into Codex native context. `--record-memory-only` pins strict local-only behavior, and `--disable-personal-memory` turns off local memory-registry writes.
- Model-backed organization uses OpenRelix runtime config rather than the user's global Codex default. The default internal Codex model is `gpt-5.4-mini`, passed through `codex exec --model` for review, backfill, learning refresh, and nightly summaries; users can inspect the current local Codex catalog with `openrelix models` and switch with `openrelix config --codex-model <model>`.
- Required rules still belong in `AGENTS.md` or project docs. Local memory items are a recall layer, not the only source for behavior that must always apply.
- `scripts/build_codex_memory_summary.py` builds a compressed summary with a token budget: merge duplicate personal memories, prioritize durable / session items, keep low-priority items local-only, and fit the injected summary around a configurable token budget. The default maximum is 8K tokens, target / warning budgets are derived automatically from that max, and there is no fixed item cap for personal-memory candidates. Users can change the max through `openrelix config --memory-summary-max-tokens <tokens>` within the supported 2000-20000 range. The default installer, review, backfill, and refresh paths write it to `~/.codex/memories/memory_summary.md`; strict local-only runs keep it out of Codex context.

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
