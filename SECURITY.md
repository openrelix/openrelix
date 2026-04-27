# Security Policy

## Supported Versions

The current public target is the latest `main` branch for macOS v0.

## Reporting a Vulnerability

Please open a private security advisory or contact the maintainers through the repository's security contact once one is configured.

Do not include secrets, tokens, cookies, account identifiers, raw Codex history, internal logs, or private user data in public issues.

## Data Boundary

OpenKeepsake is local-first. The repository should contain reusable logic only. User state belongs in the configured state root, not in source control.

When reporting security issues, prefer sanitized reproduction steps and reduced examples over raw local data.
