# OpenRelix Learning Guide

> Languages: English | [简体中文](learning-guide.md)

## Who Should Read This

This guide is for three groups:

- Users who want to install OpenRelix and understand what it reads, writes, and avoids.
- Contributors who want to change installers, scripts, skills, panels, or docs.
- Maintainers preparing public releases and needing quick checks for boundaries, risks, and validation.

One-sentence model: OpenRelix turns completed AI-agent work into local reusable assets and visible dashboards, while keeping private runtime state outside the repository.

## 30-Minute Path For Users

1. Read [System Overview](system-overview.md) to understand the three layers: host home, repo source, and external state root.
2. Read the install section in `README.md`.
3. Install with the documented command for the current release.
4. Open the generated panel and start from runtime status, not memory cards.
5. Confirm the state root, last refresh time, and background status before interpreting summaries.

Useful commands:

```bash
openrelix doctor
openrelix status
openrelix panel
```

## 2-Hour Path For Contributors

1. Read [Contributor Onboarding](contributor-onboarding.md).
2. Create a dedicated worktree.
3. Run the temporary state loop.
4. Pick one bounded task with a clear owner and non-goals.
5. Add focused tests.
6. Run common privacy and diff checks.
7. Use [Validation Matrix](validation-matrix.md) for change-type specific checks.

Start every task by asking:

- Which layer owns this change?
- What source data does it read?
- What generated artifact does it write?
- What should remain outside the repo?
- How will another maintainer verify it?

## Maintainer Path

Maintainers should read:

1. [Technical Solution](technical-solution.en.md)
2. [Data Contracts](data-contracts.md)
3. [Privacy Threat Model](privacy-threat-model.md)
4. [Release Checklist](release-checklist.md)

Before accepting a change, check:

- Source/state separation is intact.
- Runtime paths remain configurable.
- Host-owned content is preserved.
- Tests cover shared contracts or regressions.
- Package dry run does not widen public surface unexpectedly.
- Public docs have both English and Chinese versions, or the HTML page has bilingual switching.

## Common Workflows

### Add Or Change A Script

- Read `scripts/asset_runtime.py`.
- Keep state root configurable.
- Avoid import-time writes.
- Add focused unit tests.
- Run py_compile and focused tests.

### Change Memory Policy

- Use `scope`, `injection_policy`, `priority`, evidence, feedback, and budget.
- Keep `registry/memory_entries.jsonl` as source truth.
- Treat host context as compiled bounded output.
- Validate memory context and summary builder tests.

### Change Overview Or Panel

- Treat `overview-data.json` as the stable contract.
- Rebuild panel output before visual verification.
- Use local HTTP preview for docs/site pages.
- Verify visible copy and language switching when touched.

### Add Public Documentation

- Add English and Chinese Markdown versions.
- Put language links near the title.
- Keep examples synthetic.
- Prefer Markdown for agent-readable contributor docs.
- Use HTML only for rich public pages with screenshots, theme, or language switching.

## Verification Habits

Common checks:

```bash
python3 scripts/check_personal_info.py
git diff --check
```

Temporary state loop:

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-learning.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
python3 -m json.tool "$STATE_DIR/reports/overview-data.json" >/dev/null
PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"
```

Full pre-release set:

```bash
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest discover -s tests
npm pack --dry-run --json
```

## What To Avoid

- Do not commit raw host history.
- Do not commit generated reports from real user data.
- Do not include real account, token, cookie, private URL, or internal log text.
- Do not hard-code user home paths in reusable scripts or docs.
- Do not make host-native memory the only source of durable policy.
- Do not widen npm package contents casually.

## Learning Outcome

After this path, a contributor should understand where OpenRelix stores public logic, where it stores private runtime state, how memory moves from registry to bounded host context, and how to verify a change without polluting real local data.
