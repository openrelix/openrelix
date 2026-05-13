# OpenRelix Contributor Onboarding

> Languages: English | [简体中文](contributor-onboarding.zh-CN.md)


This guide is for contributors who already have a local checkout and need to make a small, reviewable OpenRelix change without touching private runtime state.

For architecture first, read [System Overview](system-overview.md), [Technical Solution](technical-solution.md), and then [Developer Guide](developer-guide.md). This page is the hands-on path after that.

## Goal

A new contributor should be able to:

1. Build a mental model of repo source versus runtime state.
2. Create a dedicated worktree and run a safe smoke loop.
3. Pick up a scoped task with clear files, tests, and privacy boundaries.
4. Hand off a change that another maintainer can review without guessing how it was verified.

This is intentionally not a release roadmap. Use it to prepare the project for more contributors before assigning larger product milestones.

## Ten-Minute Local Loop

Start from a clean checkout or a dedicated worktree:

```bash
git status --short --branch
git worktree add -b codex/<task-name> ../openrelix-worktrees/<task-name> main
cd ../openrelix-worktrees/<task-name>
```

Use temporary state for local runs:

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-dev.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
python3 -m json.tool "$STATE_DIR/reports/overview-data.json" >/dev/null
PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"
```

For a fuller installer-to-panel check that still avoids real user data:

```bash
scripts/smoke_temp_panel.sh --no-open
scripts/cleanup_smoke_temp.sh --dry-run
```

Only use `--seed-current-state` when you intentionally need to inspect the current machine's real OpenRelix state. Do not copy seeded output into docs, fixtures, tests, screenshots, or release artifacts.

## Repository Map

Use this map when choosing the owner for a task:

| Area | Main files | Common changes | Required checks |
| --- | --- | --- | --- |
| Installer | `install/`, `ops/launchd/`, `install/templates/` | install flags, profile behavior, LaunchAgents, shell command templates | `zsh -n install/install.sh scripts/*.sh`, focused installer tests |
| Runtime paths | `scripts/asset_runtime.py` | state root, host home, runtime config, atomic writes | focused unit tests, temp state smoke |
| Host collection | `scripts/collect_codex_activity.py` | Codex or Claude input mapping, raw windows, source metadata | `python3 -m unittest tests/test_collect_codex_activity.py` |
| Memory context | `scripts/build_codex_memory_summary.py`, `scripts/sync_host_memory_summary.py`, `scripts/openrelix_overview/memory_context.py` | scope, injection policy, summary budget, host block sync | memory summary and context tests |
| Curated memory | `scripts/build_curated_memory_pack.py`, `scripts/openrelix_overview/curated_memory.py` | pack grouping, diagnostics, redaction, sidecar output | `python3 -m unittest tests/test_curated_memory.py` |
| Overview and panel | `scripts/build_overview.py`, `scripts/openrelix_overview/`, `docs/*.html` | overview contract, report data, panel UI, public site | contract check, panel smoke, browser check for visible UI |
| Index | `scripts/openrelix_index.py` | SQLite sidecar schema, memory/window search | `python3 -m unittest tests/test_openrelix_index.py` |
| Public docs | `README*.md`, `docs/*.md`, `docs/*.html` | public explanations, contributor docs, privacy boundaries | `python3 scripts/check_personal_info.py`, link and version review |

Development-only harness skills under `.agents/skills/openrelix-*-harness/` are for maintaining this repository. Do not add them to `plugins/openrelix/` or the npm `files` allowlist unless there is an explicit package-surface decision.

## Task Card Template

Every task that another developer can pick up should fit this shape:

````markdown
## Title

### Scope
- Owner:
- Files or modules:
- Non-goals:

### User-visible outcome
- What changes:
- How to observe it:

### Data and privacy
- Reads from:
- Writes to:
- Must not include:

### Acceptance criteria
- [ ] Behavior:
- [ ] Docs:
- [ ] Tests:
- [ ] Privacy check:

### Verification
```bash
python3 scripts/check_personal_info.py
git diff --check
python3 -m unittest tests/<focused_test>.py
```
````

Prefer tasks with one clear owner and one main behavioral surface. If a task touches installer, overview, memory policy, and docs at once, split it before assignment.

## Done Criteria

A contributor change is ready for maintainer review when all of these are true:

- It runs in a dedicated branch or worktree.
- It keeps user state outside the repo.
- It uses `scripts/asset_runtime.py` for state root or host path resolution.
- It updates docs when behavior, data contracts, or contributor workflow changes.
- It adds focused tests for shared behavior, data contracts, or regressions.
- It runs:

```bash
python3 scripts/check_personal_info.py
git diff --check
```

For Python, installer, docs/site, release, or package-surface changes, also run the broader checks listed in [Developer Guide](developer-guide.md#验证清单).

## Privacy Rules For Contributors

Public repo changes must not include:

- Raw host transcripts, session files, logs, runtime reports, screenshots from a private panel, or real registry rows.
- Secrets, tokens, cookies, account identifiers, private organization names, internal URLs, or unreduced proprietary code snippets.
- Absolute user home paths such as `/Users/<name>/...`.
- Site-specific Codex or Claude memory mappings that belong in the external state root.

Use [Data Contracts](data-contracts.md) for schema examples and `tests/fixtures/sample-state/` for sanitized fixture shape.

## Common Pitfalls

| Symptom | Likely cause | First check |
| --- | --- | --- |
| A local test writes into the real state root | `AI_ASSET_STATE_DIR` was not set | Re-run with a temporary `STATE_DIR` |
| Overview import creates files unexpectedly | helper imports are not side-effect free | Add a focused import test or move write logic behind a command |
| Host context changed outside an OpenRelix block | sync code did not preserve host-owned content | Check `sync_host_memory_summary.py` block markers |
| Panel shows stale generated output | source changed but `reports/panel.html` was not rebuilt | Rebuild with temporary state or the intended state root |
| Package dry run includes private files | `package.json` `files` allowlist widened too far | Inspect `npm pack --dry-run --json` output |

## Review Checklist

Before asking for review, include:

- What changed and why.
- Files touched.
- Verification commands and results.
- Any checks skipped and why.
- Remaining risks, especially around privacy, package surface, host context, or runtime state.
