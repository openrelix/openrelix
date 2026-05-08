---
name: memory-review
description: Use when the user types `/memory-review`, `memory-review`, `task review`, `任务复盘`, or asks to immediately capture the current AI agent work into a sanitized review, update reusable assets, and rebuild the overview.
---

# Memory Review

Use this skill as the stable immediate-entry workflow for task review inside an AI coding agent. The current v0.1.0 preview installer exposes it through Codex.

## Why this exists

- The reusable asset workflow for immediate task review lives in this repository.
- Some AI host / Codex CLI versions do not surface repo-installed custom prompts as top-level slash commands, and may reject `/memory-review` before the model sees it.
- This skill gives the model a direct, shared route for `memory-review` style requests without depending on custom prompt UI behavior.

## Canonical source

- Repo source of truth: `.agents/skills/memory-review/`

## Runtime model

- Resolve the active state root through `AI_ASSET_STATE_DIR`.
- If it is unset, follow the fallback rules in `scripts/asset_runtime.py`.
- Keep reusable code and templates in the repo.
- Keep user state under the active state root.

## Workflow

1. Treat `memory-review` or `/memory-review` as an explicit request to do an immediate task review for the current thread.
2. Resolve runtime language from `scripts/asset_runtime.py` / `runtime/config.json` before writing files.
3. Infer the task name from the recent conversation unless the user already provided one.
4. Write or update a sanitized task review under `reviews/YYYY/` in the active state root.
5. Run the assetization gate below. Do this after the review markdown exists so the judgment has a durable source path.
6. If the work reused an existing asset, append a row to `registry/usage_events.jsonl`.
7. Rebuild the overview with `python3 scripts/build_overview.py` from the repo root.
8. Summarize the review file path, assetization decision, generated artifact paths, registry changes, usage-event changes, and overview rebuild status.

## Assetization gate

Use the active model's judgment to decide whether the completed work contains reusable value. Do not treat every review as an asset.

1. Classify the reusable value:
   - `none`: one-off task, weak signal, stale context, too project-private, or not reusable enough.
   - `memory`: durable preference, stable decision, project rule, troubleshooting conclusion, or bounded context that should be remembered.
   - `playbook`: repeatable multi-step workflow or checklist.
   - `template`: reusable output shape, prompt, report, or schema.
   - `automation`: repeatable command or scheduled/background workflow.
   - `skill`: stable agent workflow with clear trigger conditions and enough steps to be useful later.
2. For every non-`none` candidate, fill the decision shape from `templates/asset-generation-template.md`.
3. Ask the user to confirm before creating new reusable memory rows, skills, templates, playbooks, or automation artifacts, unless the user already explicitly asked for automatic generation.
4. If the user declines, keep the review and record `Asset actions: not generated` with the reason.
5. If the user confirms, create or update the artifact, then add or update the matching row in `registry/assets.jsonl`.

## Skill generation

Generate a skill only when the workflow has stable triggers, repeatable steps, clear privacy boundaries, and enough value that a future agent should call it directly.

- Use `templates/skill-draft-template.md` as the starting shape for new `SKILL.md` files.
- Choose `project` scope when the workflow depends on a specific repository, project layout, internal command surface, domain vocabulary, or repo-local privacy boundary. Write it under the target repo's `.agents/skills/<skill-name>/SKILL.md` when that repo is the correct owner.
- Choose `global` scope only when the workflow is generic, sanitized, and useful across repositories. Write it under the active host's user-level skill root, for example the resolved Codex `CODEX_HOME/skills/<skill-name>/SKILL.md`; do not hard-code user paths.
- If a proposed global skill contains private paths, internal project details, customer data, tokens, raw logs, or proprietary snippets, downgrade it to project scope, sanitize it, or do not generate it.
- After generating a skill, register it in `registry/assets.jsonl` with `type: "skill"`, `scope: "repo"` for project skills or `scope: "personal"` for global user skills, and `artifact_paths` pointing at the created `SKILL.md`.

## Memory generation

Generate reusable memory only for concise, durable facts that should influence future agent behavior.

- Prefer `durable` bucket for long-lived preferences, rules, stable project decisions, and reusable troubleshooting conclusions.
- Prefer `session` bucket for near-term follow-ups that should not become permanent guidance.
- Prefer `low_priority` for weak or emerging signals that should stay local until repeated.
- When confirmed, append or update a sanitized row in `registry/memory_entries.jsonl` with `source: "memory_review"`, the review path in `source_review_path`, and any available source window IDs. Keep raw conversation text out of the memory row.
- Rebuild the overview after writing memory rows so the memory appears in the panel.

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

- Prefer the plain-text `memory-review` skill-trigger route as the primary Codex CLI entrypoint.
- If a user-level custom prompt like `/prompts:memory-review` also exists, treat it as a compatibility layer, not the main contract.
