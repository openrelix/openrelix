# OpenRelix Developer Guide

> Languages: English | [简体中文](developer-guide.md)

This guide targets developers who maintain, extend, or release OpenRelix at the current `0.3.4` code shape. It complements [Technical Solution](technical-solution.en.md), [System Overview](system-overview.md), [Contributor Onboarding](contributor-onboarding.md), [Data Contracts](data-contracts.md), [Validation Matrix](validation-matrix.md), and [Privacy Threat Model](privacy-threat-model.md).

## First Boundary To Understand

OpenRelix is a local-first personal asset system for AI coding agents. Keep three layers separate:

```text
AI host home
  Codex / Claude Code history, sessions, native memory, user-level skills

OpenRelix repo
  publishable installer, skills, templates, scripts, docs, LaunchAgent templates

External state root
  user runtime data, raw captures, reviews, registry, reports, runtime cache, logs
```

The repository stores reusable, publishable, sanitized logic and docs. User state stays outside the repo. AI host-native files remain owned by the host; OpenRelix writes only a bounded managed summary in integrated mode.

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
| `.agents/skills/openrelix-*-harness/` | development harness skills | internal product/technical/implementation/verification workflows change |
| `plugins/openrelix/` | packaged Codex plugin bundle | public plugin surface changes |
| `install/` | installer and config rendering | install flags, profiles, host home, command entrypoints change |
| `ops/launchd/` | macOS LaunchAgent templates | background refresh, nightly, token live, update checks change |
| `scripts/asset_runtime.py` | runtime path and config hub | state root, host home, defaults, atomic writes change |
| `scripts/openrelix.py` | user-facing local CLI | subcommands change |
| `scripts/collect_codex_activity.py` | AI host activity collection | history/session/thread parsing changes |
| `scripts/nightly_consolidate.py` | model organization and memory writes | memory generation, fallback, schema changes |
| `scripts/build_overview.py` | overview compatibility entrypoint | overview output or panel UI changes |
| `scripts/openrelix_overview/` | focused overview modules | token, redaction, i18n, registry, contract helpers change |
| `scripts/openrelix_index.py` | rebuildable SQLite sidecar index | memory/window search schema changes |
| `templates/` | examples and schemas | registry, model output, review format changes |
| `tests/` | unit and package-boundary tests | behavior or privacy contracts change |
| `docs/` | public docs and pages | public explanations, architecture, onboarding, privacy change |

## Runtime Configuration

New scripts should read paths and config from `scripts/asset_runtime.py` rather than rebuilding path logic.

Core helpers:

- `get_runtime_paths()`
- `ensure_state_layout()`
- `write_runtime_config()` / `load_runtime_config()`
- `atomic_write_text()` / `atomic_write_json()`

Important environment variables:

- `AI_ASSET_STATE_DIR`
- `CODEX_HOME`
- `CLAUDE_HOME`
- `OPENRELIX_CONFIG`

## Common Development Tasks

For installer changes, keep state roots and host homes configurable. Validate shell syntax, focused installer tests, and temporary-state smoke.

For collection changes, use synthetic fixtures and protect against real path leakage. Validate with focused collection tests.

For memory-policy changes, reason in terms of `scope`, `injection_policy`, `priority`, evidence, and token budget. Do not reintroduce legacy durable/session injection as the public model.

For overview/panel changes, treat `overview-data.json` as the data contract and `panel.html` as generated output. Verify rendered output when the visible page changes.

For docs/site changes, keep Markdown bilingual and use HTML only when the page needs screenshots, layout, or interactive language/theme switching.

## Privacy Rules

Never commit:

- raw host history or sessions
- generated reports tied to real work
- runtime caches or logs
- real registry rows
- private screenshots
- secrets, tokens, cookies, account identifiers
- private organization names, internal URLs, unredacted proprietary snippets
- real user home paths

Use synthetic data in `tests/fixtures/sample-state/`.

## Validation Checklist

Always run:

```bash
python3 scripts/check_personal_info.py
git diff --check
```

For Python/script changes:

```bash
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest tests/<focused_test>.py
```

For package, release, installer, docs/site, or package-surface changes:

```bash
python3 -m unittest discover -s tests
npm pack --dry-run --json
```

Inspect package contents and confirm that state roots, raw history, generated reports, logs, private screenshots, and development-only harness skills are not included.

## Recommended Reading Order

1. [System Overview](system-overview.md)
2. [Technical Solution](technical-solution.en.md)
3. [Contributor Onboarding](contributor-onboarding.md)
4. [Data Contracts](data-contracts.md)
5. This guide
6. [Learning Guide](learning-guide.en.md)
7. [Validation Matrix](validation-matrix.md)
8. [Privacy And Distribution Boundary](privacy-and-distribution.md) and [Privacy Threat Model](privacy-threat-model.md)
9. [Release Checklist](release-checklist.md)
10. [build_overview Isolation Plan](build-overview-isolation-plan.en.md)
