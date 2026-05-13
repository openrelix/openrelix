# Open Source Install And Project Overview

> Languages: English | [简体中文](open-source-install-and-project-overview.md)

This document explains the current OpenRelix macOS preview from a public, install-first perspective. It avoids private runtime state, internal logs, account information, and unredacted screenshots.

## What OpenRelix Is

OpenRelix is a local-first personal asset layer for AI coding agents. It turns useful patterns from finished work into reusable reviews, skills, templates, automations, bounded memory summaries, and a private dashboard.

It is designed for people who repeatedly use agents such as Codex or Claude Code and want the useful parts of past work to remain available without uploading raw work history to a hosted memory service.

## Current Preview Boundary

The current preview is macOS-first:

- Installer and background jobs rely on user-level `launchd`.
- The default host adapter targets Codex, with optional Claude Code support.
- Runtime state lives outside the repository.
- npm is a bootstrapper for the installer path.
- Linux and Windows are not public preview commitments yet.

## What Gets Installed

OpenRelix can install:

- the `openrelix` command wrapper
- repo-provided skills
- launchd jobs for overview refresh, nightly organization, token live service, and update checks
- runtime config under the external state root
- generated dashboard files under the external state root

The repo remains the source for reusable logic. User data remains in the state root.

## Quick Install Shape

Follow the current README for exact commands. The install flow is roughly:

```bash
npx openrelix install
openrelix doctor
openrelix panel
```

For development or validation, prefer a temporary state root:

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-demo.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
```

## Data Boundary

The repository may contain:

- installer scripts
- templates
- skills
- documentation
- tests
- synthetic fixtures

The repository must not contain:

- raw Codex or Claude sessions
- real registry rows
- generated reports from a real user state root
- logs
- tokens, cookies, credentials, account identifiers
- private screenshots
- proprietary work context

## Daily Use Model

1. Use AI agents normally.
2. OpenRelix collects local host activity according to configured adapters.
3. Nightly jobs summarize recent work and produce memory candidates.
4. The memory registry stores selected, quality-gated entries.
5. The overview builder creates `overview-data.json`, Markdown, CSV, and `panel.html`.
6. In integrated mode, a bounded summary can be synced into enabled host context blocks.

## Reading The Dashboard

Start with runtime status:

- last refresh time
- background job status
- token pressure
- collection and model status

Then read:

- memory layer for stable reusable context
- asset layer for reviews, skills, templates, and reuse value
- tool heat for what agents actually used
- window layer for traceable evidence

Do not treat generated cards as the only source of truth. Trace important claims back to the registry, review, or window source.

## Project Documents

Recommended local reading order:

1. [System Overview](system-overview.md)
2. [Technical Solution](technical-solution.en.md)
3. [Developer Guide](developer-guide.en.md)
4. [Contributor Onboarding](contributor-onboarding.md)
5. [Data Contracts](data-contracts.md)
6. [Privacy Threat Model](privacy-threat-model.md)
7. [Validation Matrix](validation-matrix.md)

Public product pages with richer layout and screenshots:

- [Product Showcase](product-showcase.html)
- [Getting Started](getting-started.html)
- [v0.x Changelog](changelog/v0.x.html)

## Privacy Promise

OpenRelix should keep raw work history local by default. It can produce summaries and public docs, but those outputs must be explicitly sanitized before entering the repository, Feishu docs, GitHub Pages, or npm packages.

## Troubleshooting

If the panel is empty, check collection and runtime status first:

```bash
openrelix doctor
openrelix status
```

If memory is stale, check model CLI credentials and the latest nightly status. If the package is current but local integrations look stale, use the documented repair or reinstall flow rather than editing generated state manually.
