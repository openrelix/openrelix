## Project Intent

- This repo packages reusable Codex skills, installer scripts, templates, and automation for a local-first personal asset system.
- Keep canonical reusable logic in the repo.
- Keep user state outside the repo whenever possible.

## Source Of Truth

- Reusable skills live under `.agents/skills/`.
- Installer logic lives under `install/`.
- macOS launchd templates live under `ops/launchd/`.
- Runtime path resolution lives in `scripts/asset_runtime.py`.

## Editing Rules

- Do not reintroduce hard-coded user paths like `/Users/<name>/...` into reusable scripts or docs.
- Do not commit user data, raw Codex history, generated reports, or launchd output logs.
- Never put personal information, internal-only project details, user-specific memory content, private paths, account names, tokens, logs, or proprietary snippets into files that may be published as open source. This includes source code, tests, docs, website assets, npm package contents, GitHub Pages content, release artifacts, screenshots, fixtures, generated examples, and changelogs.
- Keep personal or site-specific Codex native memory mappings outside the repo, for example in the external state root extension file. Open-source code may only contain generic parsers, generic fallbacks, and sanitized public examples.
- Prefer installer or template changes over one-off local setup instructions.
- When adding automation, make state roots and Codex home paths configurable through environment variables.

## Commit Checks

- Before committing OpenRelix changes, run `python3 scripts/check_personal_info.py`, `git diff --check`, and focused tests for the touched code.
- Before release, publish, installer, docs/site, or package-surface changes, also run `python3 -m py_compile scripts/*.py install/*.py`, `python3 -m unittest discover -s tests`, and `npm pack --dry-run --json`.
- Treat these checks as the project rule even when local git hooks are not installed or are bypassed.
