---
name: openrelix-implementation-harness
description: "Use when implementing OpenRelix features, bug fixes, refactors, docs-aligned code changes, installer updates, or panel behavior. Enforces a clean implementation boundary, vertical slices, scoped patches, focused tests, privacy checks, and package-surface awareness."
---

# OpenRelix Implementation Harness

Use this skill when the user wants code or documentation changes made in the OpenRelix repo.

## Workflow

1. Start from a clean implementation boundary:
   - Inspect `git status --short` and avoid touching unrelated user changes.
   - Use the current checkout or a normal branch by default. Use a separate worktree only when the task explicitly needs release isolation, dirty-state isolation, or parallel checkout isolation.
2. Gather the minimum current context:
   - Read the nearby source, tests, docs, and templates before editing.
   - Prefer repo patterns over new abstractions.
   - For visible surfaces, identify the real rendered artifact or generated data contract.
3. Implement one vertical slice:
   - Make the smallest change that creates observable value.
   - Update tests with behavior-level assertions through public interfaces where possible.
   - Update docs only when user-facing or maintainer-facing behavior changed.
4. Preserve OpenRelix boundaries:
   - Keep runtime state outside the repo.
   - Do not hard-code user paths.
   - Do not commit generated reports, raw host history, launchd logs, secrets, or private examples.
   - Do not widen npm package contents unless the task is explicitly package-surface work.
5. Verify before handoff:
   - Run `python3 scripts/check_personal_info.py`.
   - Run `git diff --check`.
   - Run focused tests for touched modules.
   - If release, installer, docs/site, or package surface changed, also run the broader package checks required by `AGENTS.md`.

## Editing Rules

- Prefer `apply_patch` for manual edits.
- Keep changes scoped to the requested surface.
- Work with existing user changes; never revert unrelated work.
- Use structured parsers or existing helper APIs for structured data instead of ad hoc string rewrites when practical.

## Output Shape

When done, report:

- What changed.
- Files touched.
- Verification commands and results.
- Any checks that could not be run.
- Any residual risk or follow-up that materially affects the user.

## Guardrails

- Do not clone or vendor third-party source into the repo unless the user explicitly requests it and license/package/privacy checks pass.
- Do not add OpenRelix development-only skills to `plugins/openrelix/` or the npm `files` allowlist.
- If a change might affect `main` versus another checkout, verify with git before claiming it has landed.
