---
name: openrelix-verification-compliance-harness
description: "Use when testing OpenRelix changes, hardening harnesses, checking git upload safety, validating npm package contents, or reviewing privacy/open-source compliance. Builds deterministic verification loops and reports package, repo, docs, and generated-artifact risks."
---

# OpenRelix Verification And Compliance Harness

Use this skill when the user asks whether OpenRelix changes are safe to upload, package, release, publish, or rely on as a test harness.

## Workflow

1. Define the verification surface:
   - Repo source only.
   - Docs or GitHub Pages.
   - Installer or runtime config.
   - Host-context sync.
   - Overview/panel generated artifacts.
   - npm package contents.
2. Build a deterministic loop:
   - Use a temporary `AI_ASSET_STATE_DIR` for runtime tests when possible.
   - Prefer focused unit tests for changed modules.
   - For panel behavior, validate the generated data contract or rendered file, not just source diffs.
   - For package surface, inspect both the dry-run manifest and the actual tarball when release risk is high.
3. Run the baseline repository checks:
   - `python3 scripts/check_personal_info.py`
   - `git diff --check`
   - Focused tests for touched code.
4. Add package-surface checks when relevant:
   - `npm pack --dry-run --json`
   - For release-level confidence: `npm pack --json`, inspect with `tar -tzf`, and run a packaged CLI smoke check.
   - Confirm development-only skills are not listed in `package.json` `files`, plugin bundles, or tarball contents.
5. Review compliance risks:
   - No secrets, tokens, cookies, private account names, raw logs, proprietary snippets, or user data.
   - No machine-specific absolute paths in publishable files.
   - No generated reports, raw captures, local registry data, or runtime cache in git.
   - Third-party code has license and placement approval before entering the repo.
   - User-local state remains under the configured external state root.

## Output Shape

Report:

- Surface checked.
- Commands run.
- Pass/fail summary.
- Package contents findings, if package checks were run.
- Compliance findings with file paths.
- Remaining risks or skipped checks.

## Guardrails

- Do not expose matched secret-like text in the final answer. Report labels and file paths only.
- Do not rely only on `npm pack --dry-run --json` for a release claim if the task is publish-sensitive.
- Do not modify user state or host-native files while performing compliance review unless the user explicitly asks.
