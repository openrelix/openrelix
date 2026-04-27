---
name: memory-review
description: Use when the user types `/memory-review`, `memory-review`, `task review`, `任务复盘`, or asks to immediately capture the current AI agent work into a sanitized review, update reusable assets, and rebuild the overview.
---

# Memory Review

Use this skill as the stable immediate-entry workflow for task review inside an AI coding agent. The current v0.1.0 preview installer exposes it through Codex.

## Why this exists

- The reusable asset workflow for immediate task review lives in this repository.
- Some AI host / Codex CLI versions do not surface repo-installed custom prompts as top-level slash commands.
- This skill gives the model a direct, shared route for `/memory-review` style requests without depending on custom prompt UI behavior.

## Canonical source

- Repo source of truth: `.agents/skills/memory-review/`

## Runtime model

- Resolve the active state root through `AI_ASSET_STATE_DIR`.
- If it is unset, follow the fallback rules in `scripts/asset_runtime.py`.
- Keep reusable code and templates in the repo.
- Keep user state under the active state root.

## Workflow

1. Treat `/memory-review` as an explicit request to do an immediate task review for the current thread.
2. Resolve runtime language from `scripts/asset_runtime.py` / `runtime/config.json` before writing files.
3. Infer the task name from the recent conversation unless the user already provided one.
4. Write or update a sanitized task review under `reviews/YYYY/` in the active state root.
5. If the work produced durable reusable value, add or update the matching asset row in `registry/assets.jsonl`.
6. If an existing asset materially helped, append a row to `registry/usage_events.jsonl`.
7. Rebuild the overview with `python3 scripts/build_overview.py` from the repo root.
8. Summarize the review file path, asset changes, usage-event changes, and overview rebuild status.

## Language rule

- If runtime language is `zh`, write human-facing stored fields in Chinese by default: review `Task` / `Domain` / prose sections, asset `title` / `source_task` / `value_note` / `notes`, and usage-event `task` / `note`.
- If runtime language is `en`, write those human-facing fields in English.
- Keep stable enum keys canonical (`type`, `domain`, `scope`, `status`, `memory_type`, `priority`) so scripts can still classify them; the overview layer translates their display labels.
- Preserve file paths, commands, code symbols, IDs, package names, and user-provided proper nouns exactly instead of translating them.

## Quality bar

- Do not store secrets, tokens, cookies, raw internal logs, or large proprietary code dumps.
- Prefer concrete outcome, reusable value, evidence, and follow-up risk over transcript-style notes.
- Keep the result specific enough to help a later AI agent session.

## Notes

- Prefer this skill-trigger route as the primary `/memory-review` entrypoint.
- If a user-level custom prompt like `/prompts:memory-review` also exists, treat it as a compatibility layer, not the main contract.
