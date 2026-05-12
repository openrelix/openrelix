---
name: openrelix-technical-design-harness
description: "Use when designing OpenRelix architecture, memory policy, host adapters, runtime state, installer behavior, package surface, or migration plans. Produces an evidence-backed technical plan with invariants, seams, data contracts, failure modes, and verification steps before code changes."
---

# OpenRelix Technical Design Harness

Use this skill when the user asks for a technical design, architecture plan, migration strategy, adapter design, or refactor plan for OpenRelix.

## Core Invariants

Preserve these unless the user explicitly asks to revisit them:

- OpenRelix is local-first: raw host history, reviews, registries, reports, runtime cache, and logs stay in the external state root.
- The repo contains reusable, sanitized source logic, templates, docs, and skills.
- Host-native memory is owned by the host. OpenRelix managed context supplements it and must not overwrite host-owned content.
- User paths, state roots, Codex homes, Claude homes, and automation paths must be configurable.
- Public package and docs surfaces must not include user data, private paths, tokens, raw logs, or proprietary snippets.
- The npm package surface is controlled by `package.json` `files` plus ignore rules; do not widen it casually.

## Workflow

1. Read current truth:
   - `AGENTS.md` for repository rules.
   - `docs/technical-solution.md` and `docs/developer-guide.md` for architecture and maintenance boundaries.
   - The specific scripts, tests, templates, and docs touched by the proposed change.
2. Identify the technical surface:
   - State root, registry, host context, overview data, panel renderer, installer, package, plugin, docs, or tests.
   - Existing contracts and backward-compatibility expectations.
3. Define the design:
   - Data contract or API shape.
   - Module seams and adapters.
   - Migration or fallback behavior for older state roots.
   - Failure modes and recovery behavior.
   - Privacy and package-surface impact.
4. Stress-test the design:
   - What breaks if a host is unavailable?
   - What happens with missing or old JSONL fields?
   - What is written to repo versus state root?
   - What can be validated without using real user data?
5. Produce an implementation-ready plan:
   - Files or modules likely involved.
   - Tests to add or update.
   - Docs that must change.
   - Commands to verify.

## Output Shape

Return:

- Current evidence.
- Invariants affected.
- Proposed design.
- Alternatives rejected.
- Migration and fallback notes.
- Test and compliance plan.
- Open questions, only if execution would be risky without the answer.

## Guardrails

- Do not write an ADR or docs update just because a plan exists. Only recommend durable docs when the decision is hard to reverse, surprising without context, or likely to recur.
- Do not add new third-party runtime dependencies without checking installer, release, and package boundaries.
- Do not put personal or site-specific mappings into source. Keep them in external state root extensions.
