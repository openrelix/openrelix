# build_overview Isolation Refactor Plan

> Languages: English | [简体中文](build-overview-isolation-plan.md)

Status: incremental implementation. Phase 1 has already landed the smallest useful isolation around token/live-update behavior. Later phases follow this plan.

This plan addresses the oversized responsibility of `scripts/build_overview.py`. The goal is to split the overview builder into clear internal modules without breaking existing commands, tests, or generated output. The plan was previously reviewed by an independent Codex subreview and reached a 10/10 PASS after must-fix and should-fix items were cleared.

## Background

`scripts/build_overview.py` currently covers too many responsibilities:

- path and language initialization
- JSONL, reviews, raw capture, nightly summary, and Codex native memory reads
- redaction, brand normalization, local path linkifying
- i18n and display-copy conversion
- asset, reuse, value-score, and summary-term statistics
- window overview, project context, topic inference
- OpenRelix managed memory aggregation
- Codex native memory parsing, comparison, and highlighting
- token usage fetching and token card data
- Markdown, CSV, HTML, CSS, JS output
- final `reports/*` writes

Major hotspots include `build_html()`, `build_data()`, `build_markdown()`, Codex native memory parsing, memory registry building, and panel renderers. Another important issue was that `scripts/token_live_server.py` imported the full overview builder just to reuse token helpers, making the overview generator a hidden service dependency. Phase 1 removed that dependency by moving token helpers into focused `scripts/openrelix_overview/` modules.

## Goals

1. Keep `scripts/build_overview.py` as the compatibility entrypoint.
2. Move real implementation into `scripts/openrelix_overview/`.
3. Treat `overview-data.json` as the stable contract between data builder and renderers.
4. Keep OpenRelix managed memory and Codex native memory as distinct product boundaries.
5. Isolate side effects and core boundaries before migrating the large templates.
6. Keep runtime state outside the repo.

## Non-Goals

- Do not redesign the panel UI in the first phase.
- Do not change generated output paths: `overview-data.json`, `overview.md`, `overview.csv`, and `panel.html`.
- Do not write runtime state, raw history, generated reports, private paths, or user content into the repo.
- Do not delete existing `build_overview` function names until callers and tests have migrated.

## Target Module Shape

```text
scripts/openrelix_overview/
  __init__.py
  api.py
  entrypoint.py
  context.py
  io.py
  schema.py
  contract.py
  config.py
  redaction.py
  i18n.py
  assets.py
  windows.py
  memory_registry.py
  codex_native.py
  token_usage.py
  token_fetcher.py
  update_secret.py
  summary_terms.py
  builders.py
  renderers/
    markdown.py
    csv.py
    panel.py
    panel_templates.py
```

Already landed modules include `config.py`, `contract.py`, `common.py`, `i18n.py`, `labels.py`, `local_paths.py`, focused helpers in `memory_registry.py`, `token_usage.py`, `token_fetcher.py`, and `update_secret.py`.

## Phase Plan

### Phase 1: Isolate service-safe helpers

Move token and update-token helpers out of `build_overview.py` so live services can import focused modules without pulling the whole renderer graph.

Validation:

```bash
python3 -m unittest tests/test_token_usage.py tests/test_update_secret.py
python3 -m py_compile scripts/*.py install/*.py
```

### Phase 2: Define the data contract

Make `overview-data.json` the explicit renderer contract. Add normalized comparison and schema checks that can run against temporary state.

Validation:

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-overview.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"
```

### Phase 3: Split builders from renderers

Move data construction into builder modules and keep Markdown, CSV, and panel rendering behind renderer modules. Do not rewrite large HTML templates until the data boundary is stable.

### Phase 4: Move native-memory and registry helpers

Keep Codex native memory parsing separate from OpenRelix managed memory registry logic. They can share redaction and display helpers, but not ownership semantics.

### Phase 5: Reduce compatibility wrapper

After callers and tests use `scripts/openrelix_overview/api.py`, reduce `scripts/build_overview.py` to a thin wrapper.

## Invariants

- Generated reports remain rebuildable artifacts.
- Raw state and private reports never enter the repo.
- Importing helper modules should not write files.
- `overview-data.json` remains stable enough for tests and panel renderers.
- Panel output can change only when visible behavior or data shape intentionally changes.

## Verification Set

For overview refactors:

```bash
python3 scripts/check_personal_info.py
git diff --check
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest discover -s tests
STATE_DIR="$(mktemp -d /tmp/openrelix-overview.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"
```

If public docs or HTML pages are touched, also preview through a local HTTP server and verify the rendered page.
