# OpenRelix Technical Solution

> Languages: English | [简体中文](technical-solution.md)

## Positioning

`OpenRelix` is an open-source personal memory and asset system for AI coding agents and AI CLI workflows. It is not another chat-history backup. It turns repeated working methods, checklists, skills, templates, and automations into reusable local assets that can be reviewed, searched, and surfaced in a private dashboard.

GitHub project page: [openrelix/openrelix](https://github.com/openrelix/openrelix). Stars help more people discover the local-first approach.

The current preview is macOS installer-first:

- The repository stores publishable capability code: skills, installer, templates, scripts, docs, and launchd templates.
- User runtime data stays outside the repository by default.
- The preview supports Codex CLI / Codex app-server and Claude Code CLI. Host-native memory remains owned by each host; OpenRelix writes only a shared bounded summary by default, while full personal memory remains in the state root.
- Linux and Windows are not public preview commitments yet.

## Design Principles

1. Local first: raw AI host history, reviews, registries, nightly output, logs, and dashboard output stay on the local machine by default.
2. Source and state are separate: the repo is source of truth for reusable logic; the state root is runtime data.
3. Installer-first delivery: npm is a bootstrapper, while `install/install.sh` remains the real install path.
4. Skills are not hook-mounted: repo-local skills are discovered by the host, and the installer can expose canonical skills to the user-level host skill root.
5. Layered memory: mandatory rules belong in `AGENTS.md` or project docs; generated memory is a recall layer, not the only policy source.
6. Clear privacy boundary: no secrets, tokens, cookies, raw internal logs, user data, or unredacted proprietary context in publishable files.

## Architecture

```text
AI coding agent / CLI host
  |
  | adapter reads/writes
  v
Host home
  - history / session records
  - optional native memories
  - optional user skills

Current preview adapters: Codex CLI / CODEX_HOME and Claude Code CLI / CLAUDE_HOME

OpenRelix repo
  - .agents/skills/
  - install/
  - scripts/
  - templates/
  - ops/launchd/
  - docs/
  |
  | installer / openrelix / LaunchAgent
  v
External state root
  - raw/
  - registry/
  - reviews/
  - consolidated/
  - reports/
  - runtime/
  - log/
```

Core flow:

```text
collect_codex_activity.py
  -> raw/daily/<date>.json
  -> raw/windows/*.json

nightly_consolidate.py
  -> consolidated/daily/<date>/summary.json
  -> consolidated/daily/<date>/summary.md
  -> registry/memory_entries.jsonl

openrelix_index.py
  -> runtime/openrelix-index.sqlite3

memory-review skill
  -> reviews/YYYY/*.md
  -> registry/assets.jsonl
  -> registry/usage_events.jsonl

build_overview.py
  -> reports/overview-data.json
  -> reports/overview.md
  -> reports/overview.csv
  -> reports/panel.html

sync_host_memory_summary.py
  -> runtime/host-context/memory_summary.md
  -> managed block in enabled host context files
```

## Runtime Configuration

Path resolution should go through `scripts/asset_runtime.py`.

Important environment variables:

| Variable | Purpose |
| --- | --- |
| `AI_ASSET_STATE_DIR` | Override the external state root |
| `CODEX_HOME` | Override Codex host home |
| `CLAUDE_HOME` | Override Claude Code host home |
| `OPENRELIX_CONFIG` | Override runtime config path when needed |

Runtime config should describe language, memory mode, personal memory enablement, host context targets, activity source, model CLI, selected models, and token budget. Do not hard-code user paths into scripts or docs.

## Memory Model

OpenRelix uses the policy-based memory model:

- `registry/memory_entries.jsonl` is the canonical memory registry.
- `memory_summary.md` is compiled bounded output for host context injection.
- `scope` describes where a memory applies: global, domain, project, repo, host, or local.
- `injection_policy` controls host-context eligibility: `global_context`, `project_context`, `on_demand`, `local_only`, or `never`.
- `priority`, heat, evidence, feedback, and freshness affect selection inside budget.

Host context injection is intentionally narrow. The sync process updates only OpenRelix-managed blocks in enabled host targets and preserves host-owned content outside those blocks.

## Overview And Panel

`scripts/build_overview.py` remains the compatibility entrypoint. Focused implementation lives under `scripts/openrelix_overview/`.

The stable renderer contract is `reports/overview-data.json`. Markdown, CSV, and HTML outputs should be rebuildable from state-root source records. A stale `panel.html` should be treated as generated output, not source truth.

## Package Surface

The npm package should ship the public reusable skill surface and installer path. Development-only harness skills under `.agents/skills/openrelix-*-harness/` are for maintaining this repo and should not be added to `plugins/openrelix/` or the `package.json` `files` allowlist without an explicit release decision.

## Validation

Common checks:

```bash
python3 scripts/check_personal_info.py
git diff --check
```

Before release, installer, docs/site, or package-surface work:

```bash
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest discover -s tests
npm pack --dry-run --json
```

Use a local HTTP preview for static site checks because `file://` preview is blocked in this environment:

```bash
python3 -m http.server 4173 -d docs
```

## Non-Goals

- OpenRelix is not a cloud memory service.
- It does not make host-native memory the only source of truth.
- It does not commit user runtime state into the repository.
- It does not publish private screenshots, raw logs, or private connector content.
- It does not promise Linux or Windows behavior in the current macOS preview.
