# OpenRelix Detailed Developer Guide

> Languages: English | [简体中文](developer-guide.md)

This is the single developer entrypoint for OpenRelix. It is for anyone preparing to change OpenRelix: understand the boundaries first, run a local loop that does not touch private data, then choose files, tests, and privacy checks by change type.

For the product shape, start with [System Overview](system-overview.md). For deeper data flow and module design, read [Technical Solution](technical-solution.en.md). This page is the daily development entrypoint.

## One Model To Remember

The OpenRelix repository contains reusable logic. Real user runtime data lives outside the repository in the `state root`. Codex / Claude Code continue to own their own history and context.

```text
AI host home
  Codex / Claude Code history, sessions, native memory, user-level skills

OpenRelix source repository
  publishable installer, skills, templates, scripts, docs, LaunchAgent templates

state root, the OpenRelix application root
  user runtime data, window captures, reviews, registry, reports, runtime cache, logs
```

Keep those layers separate:

- The source repo stores reusable, publishable, sanitized logic and docs.
- The `state root` stores local user state. It should not enter git or the npm package by default.
- AI host-native files remain owned by the host. OpenRelix writes only a controlled summary when enabled.
- Stable rules belong in `AGENTS.md` or project docs. The memory registry is a recall layer, not the only source of truth.

## First 15 Minutes

### 1. Inspect the current checkout

```bash
git status --short --branch
```

For feature or docs work, prefer a dedicated worktree:

```bash
git worktree add -b codex/<task-name> ../openrelix-worktrees/<task-name> main
cd ../openrelix-worktrees/<task-name>
```

Run this once per clone so local development checks are active:

```bash
./scripts/setup-dev.sh
```

### 2. Run with a temporary state root

This validates the main path without touching real OpenRelix data:

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-dev.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
python3 -m json.tool "$STATE_DIR/reports/overview-data.json" >/dev/null
PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"
```

For a fuller installer-to-panel loop:

```bash
scripts/smoke_temp_panel.sh --no-open
scripts/cleanup_smoke_temp.sh --dry-run
```

Use `--seed-current-state` only when the task explicitly needs the current machine's real OpenRelix state. Do not copy seeded output into docs, fixtures, tests, screenshots, or release artifacts.

### 3. Inspect the output

```bash
open "$STATE_DIR/reports/panel.html"
```

An empty first panel is fine. Confirm that the page opens, the empty state is reasonable, and `overview-data.json` is valid JSON.

## Local Prerequisites

| Dependency | Purpose |
| --- | --- |
| macOS | installer and background automation use user-level `launchd` / LaunchAgent |
| Python 3.10+ | main scripts, installer helpers, tests |
| Node.js 18+ | `npx openrelix` bootstrapper and npm package checks |
| zsh | `install/install.sh` and shell validation |
| Codex CLI | default model-backed organization path and Codex host adapter |
| Claude Code CLI | optional host adapter and optional Claude-backed organization path |
| Xcode Command Line Tools | only needed for the lightweight macOS client |

The repo has no runtime npm dependency. Python should mostly use the standard library. Confirm release/install boundaries before adding third-party dependencies.

## Repository Map

| Path | Responsibility | Change when |
| --- | --- | --- |
| `AGENTS.md` | Stable repo rules | contribution, privacy, worktree, validation rules change |
| `.agents/skills/memory-review/` | repo-local immediate review skill | review or asset registration behavior changes |
| `.agents/skills/openrelix-*-harness/` | development harness skills | internal product, technical, implementation, or verification workflows change; these skills do not ship in npm |
| `.agents/plugins/` | Codex plugin marketplace metadata | plugin display or entry declarations change |
| `plugins/openrelix/` | packaged Codex plugin bundle | public plugin surface changes |
| `install/` | installer and config rendering | install flags, profiles, host home, command entrypoints change |
| `install/templates/` | templates written into the user environment | global `openrelix` shell command or custom prompt changes |
| `ops/launchd/` | macOS LaunchAgent templates | background refresh, nightly, token live, update checks change |
| `scripts/asset_runtime.py` | runtime path and config hub | state root, host home, defaults, atomic writes change |
| `scripts/openrelix.py` | user-facing local CLI | subcommands change |
| `scripts/collect_codex_activity.py` | AI host activity collection | history/session/thread parsing changes |
| `scripts/nightly_consolidate.py` | model organization and memory writes | memory generation, fallback, schema changes |
| `scripts/build_overview.py` | overview compatibility entrypoint | overview output or panel UI changes |
| `scripts/openrelix_overview/` | focused overview modules | token, redaction, i18n, registry, contract helpers change |
| `scripts/openrelix_index.py` | rebuildable SQLite sidecar index | memory/window search schema changes |
| `scripts/token_live_server.py` | local token live endpoint and update trigger | panel token live data or local service behavior changes |
| `templates/` | examples and schemas | registry, model output, review format changes |
| `tests/` | unit and package-boundary tests | behavior or privacy contracts change |
| `docs/` | public docs and HTML page sources | public explanations, architecture, privacy, validation, release docs change |
| `local-docs/` | local human-readable HTML docs | generated from `docs/*.md`; not shipped in npm |

## Runtime Configuration

New scripts should read paths and config from `scripts/asset_runtime.py` rather than rebuilding path logic.

Core helpers:

- `get_runtime_paths()`
- `ensure_state_layout()`
- `write_runtime_config()` / `load_runtime_config()`
- `atomic_write_text()` / `atomic_write_json()`

Important environment variables:

| Variable | Purpose |
| --- | --- |
| `AI_ASSET_STATE_DIR` | override state root |
| `CODEX_HOME` / `CODEX_BIN` | override Codex home and binary |
| `OPENRELIX_CODEX_HOMES` / `OPENRELIX_EXTRA_CODEX_HOMES` | additional Codex homes |
| `CLAUDE_HOME` / `CLAUDE_BIN` | override Claude Code data home and binary |
| `AI_ASSET_LANGUAGE` | `zh` / `en`, local output language |
| `AI_ASSET_MEMORY_MODE` | `integrated` / `local-only` / `off` |
| `OPENRELIX_ACTIVITY_SOURCE` | `history` / `app-server` / `auto` |
| `OPENRELIX_ACTIVITY_HOST` | `codex` / `claude` / `all` |
| `OPENRELIX_MODEL_CLI` | `codex` / `claude` |
| `OPENRELIX_CODEX_MODEL` | internal `codex exec` model |
| `OPENRELIX_CLAUDE_MODEL` | internal `claude -p` model or alias |

## Core Flows

### Install flow

```text
npx openrelix install
  -> install/npm-bin.js
  -> install/install.sh
  -> scripts/asset_runtime.py
  -> scripts/build_overview.py
  -> optional host config / global skill / shell command / LaunchAgent
```

When maintaining the installer, confirm:

- `minimal` stays low-touch: initialize state root, write runtime config, generate the first overview.
- `integrated` is the profile that installs global skills, custom prompts, shell commands, the macOS client, and background services.
- User paths are configurable through flags or environment variables.
- LaunchAgent templates write user-level paths only.
- Uninstall keeps local memory by default; deleting the state root must be explicit.

### Activity collection flow

```text
scripts/collect_codex_activity.py
  -> Codex app-server or Codex history/session
  -> Claude Code projects/history
  -> raw/daily/<date>.json
  -> raw/windows/<date>/*.json
```

Keep the boundary simple: read host history, write state root. Raw windows should keep source, cwd, session identifiers, prompts, and metadata so later organization and debugging can trace them.

### Nightly organization flow

```text
openrelix review / backfill
  -> collect_codex_activity.py
  -> nightly_consolidate.py
  -> sync_host_memory_summary.py
  -> openrelix_index.py rebuild
  -> build_overview.py
```

`scripts/nightly_consolidate.py` handles model organization, fallback summaries, summary selection, memory writes, and learning logs. Model failures must still produce fallback summaries and should not block overview generation.

### Overview and panel flow

```text
scripts/build_overview.py
  -> reports/overview-data.json
  -> reports/overview.md
  -> reports/overview.csv
  -> reports/panel.html
```

`overview-data.json` is the stable input for the panel and Markdown report. Renderers should not directly read raw captures, JSONL registries, or host memory files. Verify the real HTML when the visible page changes.

### Local index flow

`scripts/openrelix_index.py` builds `runtime/openrelix-index.sqlite3`. It is a rebuildable local index, not the authoritative data source. Deleting it must not affect `raw/`, `registry/`, or `consolidated/`.

## Common Development Tasks

### Add an `openrelix` subcommand

1. Add parser, flags, and help text in `scripts/openrelix.py` `build_parser()`.
2. Add `command_<name>()` or reuse an existing command function.
3. Dispatch it in `main()`.
4. If the npm wrapper exposes it, update `install/npm-bin.js` help and command switching.
5. If the installed shell entry should expose it, check `install/templates/bin/openrelix.tmpl`.
6. Add tests for parser exposure, npm help, key behavior, or dry-run behavior.

### Add an installer flag

1. Update `install/install.sh` usage, defaults, argument parsing, exported environment, and behavior.
2. If runtime config persists it, update `scripts/asset_runtime.py` normalization, runtime config read/write, and defaults.
3. If it affects `npx openrelix install`, update `install/npm-bin.js` help.
4. Update README / docs when user behavior changes.
5. Run `zsh -n install/install.sh scripts/*.sh` and focused installer tests.

### Add a stable skill

1. Put it under `.agents/skills/<name>/SKILL.md`.
2. Document trigger conditions, runtime boundary, output location, and privacy requirements.
3. If it needs global installation, update installer skill symlink logic.
4. Update README or docs.
5. Validate with a temporary state root.

### Add an automation script

1. Use `scripts/asset_runtime.py` for paths; do not hard-code personal paths.
2. Write output into the state root.
3. Build a manual CLI first; add launchd templates only when the manual path is stable.
4. For new state files, confirm `.gitignore`, `.npmignore`, and package `files` cannot include private data.
5. Add tests, or at least shell / Python syntax checks.

### Change a host adapter

1. Confirm whether the change is for Codex, Claude Code, or a future host.
2. Resolve native host directories only through `CODEX_HOME`, `CLAUDE_HOME`, or a new configurable variable.
3. Preserve minimal trace fields such as `ai_host`, cwd, thread id, or session id.
4. Do not write raw transcripts into repo, docs, fixtures, or release artifacts.
5. Public docs should use generic path shapes and configurable variables, not personal machine paths.

### Change overview or panel UI

1. Adjust the `overview-data.json` section contract first, then let the renderer consume it.
2. Do not make the renderer read runtime state directly.
3. Static showcase pages and real panels must avoid private screenshots, paths, and data.
4. Preview HTML through a local HTTP server and verify navigation, layout, links, and both languages.

### Change Markdown and local HTML docs

`docs/*.md` is the source. `local-docs/reference/*.html` is generated for human local reading. After editing Markdown, run:

```bash
python3 scripts/build_local_docs.py
python3 scripts/build_local_docs.py --check
```

For automatic sync while writing:

```bash
python3 scripts/build_local_docs.py --watch
```

## Task Card Template

A small task that another developer can pick up should fit this shape:

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

Prefer tasks with one clear owner and one main behavioral surface. If a task touches installer, overview, memory policy, and docs at once, split it first.

## Validation Checklist

For everyday small changes:

```bash
python3 scripts/check_personal_info.py
git diff --check
```

For Python or installer changes:

```bash
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest discover -s tests
zsh -n install/install.sh scripts/*.sh
```

For overview or state-root changes, add a temporary-state loop:

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-overview.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
python3 -m json.tool "$STATE_DIR/reports/overview-data.json" >/dev/null
PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"
```

For installer-to-panel smoke:

```bash
scripts/smoke_temp_panel.sh --no-open
scripts/cleanup_smoke_temp.sh --dry-run
```

For release, installer, docs/site, or package-surface changes:

```bash
npm pack --dry-run --json
```

Fix real failures. Do not widen ignore rules to bypass privacy, path, or package allowlist checks.

## Privacy And Security Boundary

Public repo changes must not include:

- Raw host transcripts, session files, logs, runtime reports, screenshots from a private panel, or real registry rows.
- Secrets, tokens, cookies, account identifiers, private organization names, internal URLs, or unreduced proprietary snippets.
- Absolute user home paths.
- Site-specific Codex or Claude memory mappings that belong in the external state root.
- One-off local machine experience written as a global project rule.

Use [Data Contracts](data-contracts.md) for schema examples and `tests/fixtures/sample-state/` for sanitized fixture shape.

## Common Questions

### I cannot find where data was written

Check:

```bash
echo "$AI_ASSET_STATE_DIR"
python3 - <<'PY'
from scripts.asset_runtime import get_runtime_paths
print(get_runtime_paths().state_root)
PY
```

If `openrelix` is installed:

```bash
openrelix paths
```

### The panel has no content

Common causes:

- This is a new state root with no assets, reviews, or AI host history.
- Codex history is not enabled, so there are no windows to collect.
- `openrelix review` or `python3 scripts/build_overview.py` has not run yet.

Try:

```bash
openrelix refresh
```

or:

```bash
python3 scripts/build_overview.py
```

### Nightly jobs did not run

Check:

- Whether `--enable-nightly` was used.
- Whether the user is still logged in.
- Whether state root `log/` contains errors.
- Whether the LaunchAgent plist was bootstrapped.

Screen lock is fine. Logging out is the boundary.

### Why only write a bounded summary by default

OpenRelix can integrate with host-native context, but it writes a compressed bounded summary by default. Full registries, reviews, raw windows, and consolidated summaries stay in the state root. The compression path merges duplicate memories, includes only effective `injection_policy=global_context/project_context` entries, and keeps low-priority entries local by default.

For strict isolation, disable host-context injection:

```bash
./install/install.sh --profile integrated --record-memory-only
```

## Review Checklist

Before asking for review, include:

- What changed and why.
- Files touched.
- Verification commands and results.
- Any checks skipped and why.
- Remaining risks, especially around privacy, package surface, host context, or runtime state.

## Continue Deeper

Choose the next document by goal:

- For the full architecture: read [System Overview](system-overview.md) and [Technical Solution](technical-solution.en.md).
- For raw, registry, summary, overview, or host context fields: read [Data Contracts](data-contracts.md).
- For choosing commands by change type: read [Validation Matrix](validation-matrix.md).
- For public boundary checks: read [Privacy Boundary](privacy-and-distribution.md) and [Privacy Threat Model](privacy-threat-model.md).
- For release work: read [Release Checklist](release-checklist.md).
