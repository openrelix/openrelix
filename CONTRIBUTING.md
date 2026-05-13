# Contributing

Thanks for helping improve OpenRelix.

Start with [Contributor Onboarding](docs/contributor-onboarding.md) for the 10-minute local loop, task card template, and review checklist. Use [Validation Matrix](docs/validation-matrix.md) to pick checks by change type, [Data Contracts](docs/data-contracts.md) for state shapes, and [Privacy Threat Model](docs/privacy-threat-model.md) for host, connector, package, and release boundaries.

Docs under `docs/` are bilingual by default. Use [Docs Index](docs/README.md) for the Markdown companion naming rule and the boundary between agent-readable Markdown and rich bilingual HTML pages.

## Scope

This project keeps reusable capabilities in the repository and user-specific state outside the repository.

Good contribution targets:

- installer and setup scripts
- reusable skills
- templates and documentation
- state-root path handling
- macOS LaunchAgent templates
- tests for consolidation, overview, and installer helper behavior

Avoid contributing:

- raw Codex history
- local logs or generated reports
- personal registry entries or reviews
- secrets, tokens, credentials, cookies, or account data
- private internal code or unsanitized proprietary logs

## One-Time Dev Setup

Run this once after cloning the repo (safe to re-run):

```bash
./scripts/setup-dev.sh
```

It points `git config core.hooksPath` at `scripts/git-hooks/`, so the pre-commit personal-info check (and any future hooks) run automatically. Pull requests are also gated by `.github/workflows/ci.yml`, which runs the same checks on every PR — running them locally just shortens feedback.

## Development

Run common validation before sending a change:

```bash
python3 scripts/check_personal_info.py
git diff --check
```

Then add focused tests based on the files you touched. See [Validation Matrix](docs/validation-matrix.md).

When you need to verify the install-to-panel path without writing to your real
state root or real `CODEX_HOME`, run:

```bash
scripts/smoke_temp_panel.sh --no-open
```

Preview and clean temporary smoke directories with:

```bash
scripts/cleanup_smoke_temp.sh --dry-run
scripts/cleanup_smoke_temp.sh --yes
```

If your change touches JSON metadata, validate it with:

```bash
python3 -m json.tool .agents/plugins/marketplace.json >/dev/null
for f in plugins/*/.codex-plugin/plugin.json; do python3 -m json.tool "$f" >/dev/null; done
```

## Pull Requests

Keep pull requests small and explain:

- what changed
- how it was validated
- whether it touches repo capabilities, user state, or both
- any migration needed for existing local installs

Use sanitized examples in issues and pull requests.

Use the pull request template when available. If a change touches release or package surface, also follow [Release Checklist](docs/release-checklist.md).

## AI Agent Collaboration

This repo is co-maintained by humans and AI coding agents (Codex CLI, Claude Code). To keep multi-window work converging instead of fighting:

- **Branch prefix**: use `codex/<task>` from Codex sessions and `claude/<task>` from Claude Code sessions, so `git log` and worktree listings make the producing agent obvious.
- **Hot files**: `scripts/build_overview.py` and `scripts/openrelix.py` are large single-file modules with high conflict risk under parallel work. Before editing either, claim the file by opening a draft PR titled `[wip:hot-file] <path>`; other agents should rebase or wait until the draft closes.
- **Design harnesses**: non-trivial product or architecture changes should be grounded by the relevant harness skill under `.agents/skills/openrelix-*-harness/` (read the SKILL.md directly from Claude Code — those skills are Codex-discovered only).
- **CI is the gate**: never `--no-verify` or skip the personal-info check to push faster. CI will fail the PR.
- **Independent review**: for non-trivial PRs, request an independent review from the *other* agent family (Codex PRs reviewed by a Claude session and vice versa) before merging.
