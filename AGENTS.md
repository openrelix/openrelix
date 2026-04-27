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
- Prefer installer or template changes over one-off local setup instructions.
- When adding automation, make state roots and Codex home paths configurable through environment variables.
