# OpenRelix Release Checklist

> Languages: English | [简体中文](release-checklist.zh-CN.md)


This checklist is for release and publish work. It is not a product roadmap. It keeps version, changelog, GitHub release, npm package, and public docs in sync without leaking local state.

## Release Inputs

Before starting:

- Confirm the target version and whether it is a patch, minor, or preview release.
- Confirm the release branch is based on current `main`.
- Check `package.json` and changelog state.
- Check whether release notes should mention docs/site, installer, memory behavior, host adapters, package surface, or privacy changes.

Prepare releases from a clean checkout based on current `main`. If the current checkout has unrelated dirty state, clean it up or choose an isolated checkout before changing version and release files.

## Version And Docs

Update only current-release truth:

- `package.json` version.
- `docs/changelog/v0.x.html` or release notes source.
- README version mentions when they describe the current preview.
- Public site version metadata if the site is changed.
- Developer docs if package surface, installer behavior, or validation rules changed.

Do not mix a future roadmap into the release checklist. Planned work should stay separate and should not be represented as shipped behavior.

## Local Verification

Run the full pre-release set:

```bash
python3 scripts/check_personal_info.py
git diff --check
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest discover -s tests
npm pack --dry-run --json
```

If installer or panel behavior changed, also run a temporary smoke:

```bash
scripts/smoke_temp_panel.sh --no-open
scripts/cleanup_smoke_temp.sh --dry-run
```

For static site changes, preview through a local HTTP server rather than `file://`:

```bash
python3 -m http.server 8766 --directory docs
```

Then open the relevant page at `http://127.0.0.1:8766/`.

## Package Surface Review

Inspect `npm pack --dry-run --json` output and confirm:

- Only intended source, docs, installer, public plugin, templates, ops, and allowed scripts are included.
- No state root, `raw/`, `reports/`, `runtime/`, `log/`, real reviews, local registry rows, or generated panel files from a private machine are included.
- Development-only OpenRelix harness skills remain outside `plugins/openrelix/` and the npm allowlist unless there is an explicit release decision.
- No secrets, tokens, cookies, account identifiers, private paths, internal URLs, or proprietary snippets appear in package files.

## Publish Flow

Use the repository's configured release workflows where possible:

1. Commit the release preparation.
2. Tag with `v<package.json version>` only after validation passes.
3. Push the branch and tag.
4. Use the GitHub release workflow or release draft process configured in `.github/workflows/`.
5. Verify npm publish or trusted-publishing output.
6. Verify a fresh `npx openrelix --version` or package metadata check in a clean context.

If a version has already been published, do not overwrite it. Prepare the next patch version instead.

## Release Notes Checklist

Release notes should be clear to users and contributors:

- What changed.
- Who should upgrade.
- Any migration behavior.
- Any installer or host-adapter behavior changes.
- Any privacy or package-surface changes.
- Validation commands run.
- Known limitations or follow-up work.

Avoid internal task names, private documents, private logs, or unreduced incident details.

## Rollback And Recovery

If release validation fails:

- Stop publishing.
- Fix the issue on the release branch or prepare a new patch.
- Do not delete user state as a workaround.
- If a bad package was published, document the mitigation and publish a forward fix.
- If host-context sync is involved, verify managed block preservation before asking users to rerun sync.

## Final Handoff

Record:

```text
Version:
Commit/tag:
Validation:
Package dry-run result:
Published artifacts:
Residual risk:
```

Keep private credentials and raw logs out of the release record.
