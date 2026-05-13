---
name: openrelix-product-design-harness
description: "Use when shaping OpenRelix product direction, project planning, roadmap, UX, PRD, feature scope, or iteration priority. Runs an evidence-first product grilling loop grounded in current docs, panel contracts, user feedback, and repo behavior before proposing a narrow next slice or producing a durable planning document."
---

# OpenRelix Product Design Harness

Use this skill when the user wants to decide what OpenRelix should build next, refine product scope, write a project plan, turn vague product ideas into a PRD, or evaluate competing iteration directions.

## Principle

Ground product direction in current OpenRelix evidence before proposing a story. If the answer can be found in repo docs, rendered artifacts, runtime output, or git history, inspect that evidence instead of asking the user to restate it.

## Workflow

1. Gather current evidence:
   - Read the relevant repo docs, usually `README.md`, `README.zh-CN.md`, `docs/technical-solution.md`, and `docs/developer-guide.md`.
   - Check the current implementation or rendered artifact when the product surface already exists.
   - Use git state or recent diffs when the user is asking about what has actually landed.
2. Frame the product question:
   - Target user or operator.
   - Pain or repeated failure mode.
   - Desired behavior after the change.
   - Privacy, local-first, host-boundary, and context-budget constraints.
3. Grill only unresolved decisions:
   - Ask one blocking question at a time.
   - Include a recommended answer for each question.
   - Prefer evidence lookup over user questions when the repo can answer.
4. Compare options using OpenRelix criteria:
   - Does it strengthen local-first asset capture?
   - Does it preserve host-native memory and user data boundaries?
   - Does it reduce context waste or repeated agent work?
   - Can it be validated with a small, observable slice?
   - Is it reversible if the product bet is wrong?
5. Choose the smallest useful iteration:
   - Define the next slice, non-goals, user-facing acceptance criteria, and validation path.
   - Note what should become docs, memory, playbook, template, or skill only if the value is durable.
6. If the user asks for a written project plan, PRD, roadmap, or full product proposal, switch to Document Mode:
   - Confirm the destination from the request: answer-only, repo markdown, an existing cloud-doc URL, or both repo markdown and a cloud doc when the task clearly asks for publishable delivery.
   - Prefer updating an existing relevant document over creating a near-duplicate. If no destination is specified, recommend a repo path under `docs/` only when the decision is durable enough for future contributors.
   - Keep shipped behavior, committed work, and future plans explicitly separated.
   - Write acceptance criteria as observable user or operator outcomes, not implementation wishes.
   - Add a short review block before delivery that records evidence checked, assumptions, unresolved risks, and validation steps.

## Document Templates

Use these structures when the user asks for a durable product artifact. Keep sections compact; omit sections that are truly irrelevant, but do not omit privacy, local-first, and validation considerations for OpenRelix-facing work.

### Project Plan

- Context and evidence: current repo/docs/runtime facts that justify the plan.
- Planning horizon: the concrete time window or release window, with dates only when evidence supports them.
- Goals: what changes for users, operators, or contributors.
- Non-goals: what is intentionally out of scope.
- Milestones: 2-5 slices, each with outcome, owner role, dependency, and validation.
- Risks and reversibility: product, privacy, package-surface, adoption, or maintenance risks.
- Acceptance criteria: how to know the plan succeeded.
- Validation plan: commands, rendered artifacts, docs, or user checks needed.

### PRD

- Problem statement: pain, repeated failure mode, or opportunity grounded in evidence.
- Target users/operators: who benefits and when.
- User scenarios: concrete workflows, including edge cases and recovery paths.
- Goals and non-goals.
- Requirements: functional behavior, UX expectations, data/privacy boundaries, and compatibility constraints.
- User stories or jobs-to-be-done, each mapped to acceptance criteria.
- Metrics or signals: only real or proposed signals; mark assumptions clearly.
- Rollout and fallback: staged delivery, migration, rollback, or documentation plan.
- Validation and open questions.

### Product Decision Memo

- Decision.
- Evidence.
- Options considered.
- Why this option now.
- What changes in the next slice.
- What must not change.
- Verification and review path.

## Self-Review Gate

Before presenting or saving a product planning document, check it against this list:

- Repo truth checked: current docs, implementation, rendered artifact, git history, or runtime output were inspected when available.
- No invented research: user quotes, usage metrics, market claims, or dates are either sourced, marked as assumptions, or removed.
- Shipped versus planned is clear: future work is not described as current product behavior.
- Acceptance criteria are testable by a human, command, rendered page, or generated artifact.
- Privacy and package-surface impact is explicit when the plan touches state roots, host memory, docs, plugin, installer, npm, or screenshots.
- The document does not include private paths, tokens, raw logs, proprietary snippets, or user-specific memory content.
- The scope is small enough to produce an observable next slice.

## Output Shape

Return a concise product decision with:

- Evidence inspected.
- Product decision.
- Next shippable slice.
- Non-goals.
- Acceptance criteria.
- Validation plan.
- Open questions, only if they block execution.

When Document Mode is used, return or write the requested artifact using the relevant template above, then include the self-review result and destination path or URL.

## Guardrails

- Do not invent user research or metrics. Mark assumptions clearly.
- Do not treat stale screenshots, stale docs, or memory notes as current truth without verification when the repo can be checked.
- Do not add private user examples, raw transcripts, or internal-only details to public docs or repo files.
- Do not create `docs/superpowers/` or adopt external workflow directory conventions. Use OpenRelix-owned docs and skill boundaries.
- Do not ask for broad discovery questions when repo evidence can answer them. Ask only blocking questions, one at a time, with a recommended answer.
