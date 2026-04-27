# Privacy And Distribution Boundary

This repository is designed to be shareable without bundling one person's local machine state.

## What should be shared

- The repo source itself
- Installer scripts
- Canonical skills under `.agents/skills/`
- Templates and schemas
- Sanitized screenshots or example output created for documentation

## What should stay local

- Raw Codex history
- Runtime caches and logs
- Generated reports tied to one user's work
- Real registry entries and task reviews
- Anything containing secrets, credentials, internal paths, or proprietary task context

## Suggested shareable story

1. The repo provides the reusable automation and installer.
2. User data lives in an external state root managed by the installer.
3. Repo-local skills work automatically inside the repo.
4. Optional global install makes those skills available from any repo.
5. Background services and nightly consolidation are opt-in installer features.
