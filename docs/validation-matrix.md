# OpenRelix Validation Matrix

> Languages: English | [简体中文](validation-matrix.zh-CN.md)


Use this matrix to choose the smallest useful verification set for a change. Always run the common checks first, then add the row that matches the files you touched.

## Common Checks

Run for every OpenRelix change:

```bash
python3 scripts/check_personal_info.py
git diff --check
```

If the checkout has unrelated local files, do not stage or modify them. Mention any relevant dirty state in the handoff.

## Change-Type Matrix

| Change type | Typical files | Extra checks |
| --- | --- | --- |
| Docs only | `README*.md`, `docs/*.md`, `.github/*` | Bilingual companion check, link review, version review against `package.json`, privacy check |
| Public site docs | `docs/*.html`, `docs/assets/*` | Local HTTP preview, browser screenshot/interaction check if layout changed, privacy check on screenshots/assets |
| Python scripts | `scripts/*.py`, `scripts/openrelix_overview/*.py` | `python3 -m py_compile scripts/*.py install/*.py`, focused unit tests |
| Installer | `install/`, `ops/launchd/`, `install/templates/` | `zsh -n install/install.sh scripts/*.sh`, focused installer tests, temporary state smoke |
| Runtime path/config | `scripts/asset_runtime.py`, installer config writes | Temporary `AI_ASSET_STATE_DIR` loop, config read/write focused tests |
| Host collection | `scripts/collect_codex_activity.py` | `python3 -m unittest tests/test_collect_codex_activity.py` |
| Memory policy/context | `scripts/build_codex_memory_summary.py`, `scripts/sync_host_memory_summary.py`, `scripts/openrelix_overview/memory_context.py` | `python3 -m unittest tests/test_memory_context.py tests/test_memory_summary_builder.py` |
| Curated memory pack | `scripts/build_curated_memory_pack.py`, `scripts/openrelix_overview/curated_memory.py` | `python3 -m unittest tests/test_curated_memory.py` |
| Index | `scripts/openrelix_index.py` | `python3 -m unittest tests/test_openrelix_index.py` |
| Overview/panel data | `scripts/build_overview.py`, `scripts/openrelix_overview/*`, report contract | temporary overview build plus `PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"` |
| Package surface | `package.json`, package allowlist, public plugin bundle | `npm pack --dry-run --json` and inspect file list |
| Release/publish | version, changelog, GitHub release, npm publish | Full test suite, package dry run, release checklist |

## Temporary State Loop

Use this when a change may touch state-root behavior:

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-validation.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
python3 -m json.tool "$STATE_DIR/reports/overview-data.json" >/dev/null
PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"
```

For installer-to-panel smoke:

```bash
scripts/smoke_temp_panel.sh --no-open
scripts/cleanup_smoke_temp.sh --dry-run
```

Only seed from current state when the task explicitly needs real local data. Never copy seeded output into repo files.

## Full Pre-Release Set

Run before release, publish, installer, docs/site, or package-surface work:

```bash
python3 scripts/check_personal_info.py
git diff --check
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest discover -s tests
npm pack --dry-run --json
```

Inspect the package list and confirm it does not include state roots, raw history, generated reports, logs, private screenshots, or development-only harness skills.

## Handoff Format

Include this in the final review note or pull request:

```text
Change:
Files:
Verification:
Skipped checks:
Privacy/package notes:
Residual risk:
```

Keep command output summaries short. Do not paste raw private logs.
