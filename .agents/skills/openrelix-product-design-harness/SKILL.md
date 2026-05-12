---
name: openrelix-product-design-harness
description: "Use when shaping OpenRelix product direction, roadmap, UX, PRD, feature scope, or iteration priority. Runs an evidence-first product grilling loop grounded in current docs, panel contracts, user feedback, and repo behavior before proposing a narrow next slice."
---

# OpenRelix Product Design Harness

Use this skill when the user wants to decide what OpenRelix should build next, refine product scope, turn vague product ideas into a PRD, or evaluate competing iteration directions.

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

## Output Shape

Return a concise product decision with:

- Evidence inspected.
- Product decision.
- Next shippable slice.
- Non-goals.
- Acceptance criteria.
- Validation plan.
- Open questions, only if they block execution.

## Guardrails

- Do not invent user research or metrics. Mark assumptions clearly.
- Do not treat stale screenshots, stale docs, or memory notes as current truth without verification when the repo can be checked.
- Do not add private user examples, raw transcripts, or internal-only details to public docs or repo files.
