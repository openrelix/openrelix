# Skill Draft Template

Use this only after the assetization gate decides that a workflow should become a skill and the user confirms generation. Replace every placeholder before saving as `SKILL.md`.

```markdown
---
name: <stable-skill-name>
description: <Trigger this skill when... Include concrete user phrases, file types, domains, or workflow signals.>
---

# <Skill Title>

Use this skill when <clear trigger conditions>.

## Scope

- Scope: project | global
- Owner path:
- Why this scope:
- Privacy boundary:

## Workflow

1. <First stable step.>
2. <Second stable step.>
3. <Verification or output step.>

## Inputs

- <Input or context the agent should gather.>

## Outputs

- <Files, registry rows, reports, or summaries the agent should produce.>

## Quality bar

- <What makes the result acceptable.>
- <What must not be stored or exposed.>

## Fallbacks

- <What to do when a required tool, path, permission, or source is unavailable.>
```

## Scope decision guide

- Choose `project` when the workflow depends on one repo, repo-local commands, project-specific terms, internal systems, or privacy-sensitive context. Save to the target repo's `.agents/skills/<stable-skill-name>/SKILL.md` when that repo owns the behavior.
- Choose `global` only when the workflow is generic across repositories and can be written without private paths, internal-only project details, tokens, customer data, raw logs, or proprietary snippets. Save to the active host's user-level skill root, such as the resolved Codex `CODEX_HOME/skills/<stable-skill-name>/SKILL.md`.
- If the workflow is useful but not stable enough for a skill, downgrade it to `playbook` and register the playbook artifact instead.
