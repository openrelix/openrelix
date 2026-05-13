---
name: openrelix-technical-design-harness
description: "Use when designing OpenRelix architecture, memory policy, host adapters, runtime state, installer behavior, package surface, migration plans, or durable technical proposals. Produces an evidence-backed technical plan or document with invariants, seams, data contracts, failure modes, implementation slices, and verification steps before code changes."
---

# OpenRelix Technical Design Harness

Use this skill when the user asks for a technical design, architecture plan, migration strategy, adapter design, refactor plan, or publishable technical proposal for OpenRelix.

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
6. If the user asks for a written technical proposal, architecture proposal, migration proposal, RFC, or full technical document, switch to Document Mode:
   - Confirm the destination from the request: answer-only, repo markdown, an existing cloud-doc URL, or both repo markdown and a cloud doc when the task clearly asks for publishable delivery.
   - Prefer updating the existing canonical document when the proposal changes current architecture. Use a new `docs/*.md` document only for a focused design, migration, or implementation plan that should remain independently reviewable.
   - Keep current implementation, proposed changes, and speculative future work in separate sections.
   - Include implementation slices only after the design is stress-tested against privacy, state-root, package-surface, and migration constraints.
   - Add a short review block before delivery that records evidence checked, assumptions, unresolved risks, and verification steps.

## Document Templates

Use these structures when the user asks for a durable technical artifact. Keep them concise enough for a contributor to execute, but complete enough that a later agent can verify the same boundaries without asking the user again.

### Technical Proposal

- Context and evidence: repo files, docs, tests, runtime output, package contents, or rendered artifacts inspected.
- Problem and target behavior.
- Invariants and constraints: local-first state, host-native memory, privacy, package surface, compatibility, installer behavior.
- Proposed design: module boundaries, data contracts, adapters, configuration, and ownership.
- Alternatives considered and why rejected.
- Migration and fallback: old state roots, missing fields, unavailable hosts, failed writes, or partial installs.
- Failure modes and recovery behavior.
- Implementation slices: small ordered changes, each with files/modules, tests, docs, and verification command.
- Compliance and release impact.
- Open questions that genuinely block execution.

### Architecture Decision Memo

- Decision.
- Status: proposed, accepted, superseded, or rejected.
- Current evidence.
- Consequences: benefits, trade-offs, new obligations.
- Compatibility and migration.
- Validation plan.
- Review date or trigger for revisiting, if the decision is reversible or uncertain.

### Migration Plan

- Source state and target state.
- Data contract changes.
- Backward compatibility and fallback reads.
- Write path and idempotency.
- Dry-run or synthetic-state validation.
- Rollout, rollback, and cleanup.
- Package, installer, and docs impact.

## Implementation Plan Requirements

When a technical design needs implementation steps, make each slice independently reviewable:

- Name the files or modules likely touched.
- State the behavior that changes.
- State the failing or focused test to add when practical.
- State the verification command.
- State the privacy/package-surface risk.
- Keep unrelated refactors out unless they remove a real blocker.

## Self-Review Gate

Before presenting or saving a technical document, check it against this list:

- Repo truth checked: `AGENTS.md`, current docs, touched modules, tests, generated artifacts, or package rules were inspected when relevant.
- Invariants preserved or explicitly called out for user decision.
- Current behavior, proposed behavior, and future work are separated.
- Data contracts and state ownership are clear enough to test.
- Migration and fallback behavior covers missing, old, partial, or malformed local state when applicable.
- Verification commands are concrete and scoped to the touched surface.
- Package-surface impact is explicit; development-only skills or harnesses are not added to `plugins/openrelix/` or `package.json` `files` without an explicit release decision.
- No private paths, tokens, raw logs, proprietary snippets, account names, or user-specific memory content are included.

## Output Shape

Return:

- Current evidence.
- Invariants affected.
- Proposed design.
- Alternatives rejected.
- Migration and fallback notes.
- Test and compliance plan.
- Open questions, only if execution would be risky without the answer.

When Document Mode is used, return or write the requested artifact using the relevant template above, then include the self-review result and destination path or URL.

## Guardrails

- Do not write an ADR or docs update just because a plan exists. Only recommend durable docs when the decision is hard to reverse, surprising without context, or likely to recur.
- Do not add new third-party runtime dependencies without checking installer, release, and package boundaries.
- Do not put personal or site-specific mappings into source. Keep them in external state root extensions.
- Do not create `docs/superpowers/` or adopt external workflow directory conventions. Use OpenRelix-owned docs and skill boundaries.
- Do not require subagents, parallel agents, or TDD-only workflows unless the user explicitly requests them or the active harness already allows them.
