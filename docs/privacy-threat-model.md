# OpenRelix Privacy Threat Model

> Languages: English | [简体中文](privacy-threat-model.zh-CN.md)


OpenRelix is useful only if contributors can improve it without moving private work history into the public repo or into unintended host context. This document turns that boundary into a review checklist.

For the shorter publishing boundary, see [Privacy And Distribution Boundary](privacy-and-distribution.md). This page is the contributor and integration threat model.

## Assets To Protect

| Asset | Why it matters | Default location |
| --- | --- | --- |
| Raw AI host transcripts | May contain proprietary work, credentials, or personal data | Host home and state root `raw/` |
| Native host memory | Owned by Codex or Claude, may mix user-managed context and OpenRelix blocks | `CODEX_HOME`, `CLAUDE_HOME` |
| Registry rows and reviews | Sanitized but still personal work memory | State root `registry/`, `reviews/` |
| Generated reports and panel | Summarize real work and local paths | State root `reports/` |
| Runtime config and logs | May reveal local setup, commands, or failures | State root `runtime/`, `log/` |
| Feishu or other connector content | Can contain organization data and document permissions | External service plus local state |
| Release/package artifacts | Publicly shared and hard to retract | npm package, GitHub release, GitHub Pages |

## Trust Boundaries

```text
AI host home
  -> adapter reads local records
  -> OpenRelix writes only explicit managed host-context blocks

OpenRelix repo
  -> reusable source, docs, templates, sanitized fixtures
  -> must not contain private runtime data

External state root
  -> real user data and generated reports
  -> local-first, configurable, removable

External connectors
  -> Feishu, meeting notes, docs, chat, or future tools
  -> opt-in, least privilege, source-labeled, deletable
```

Every new feature should name which boundary it crosses before implementation.

## Threats And Required Mitigations

| Threat | Example | Required mitigation |
| --- | --- | --- |
| Private data committed to repo | Real `raw/windows` file copied into tests | Use synthetic fixtures only; run `python3 scripts/check_personal_info.py` |
| Hard-coded user path | a personal home directory copied into docs or code | Use env/config paths, `~`, `CODEX_HOME`, `AI_ASSET_STATE_DIR`, or `/tmp/openrelix-demo` |
| Host-owned memory overwritten | Sync replaces a full `memory_summary.md` | Update only OpenRelix-managed blocks and preserve surrounding content |
| Excessive host-context injection | Low-signal local notes enter Codex or Claude context | Enforce `scope`, `injection_policy`, priority, dedupe, and token budgets |
| Generated panel published | Real `reports/panel.html` copied to `docs/` | Use sanitized screenshots and public examples only |
| Connector over-collection | Feishu chat or meeting import without source policy | Require opt-in source, scope list, retention policy, and delete path |
| Secrets in logs | CLI error includes token or cookie | Redact before persistence; keep raw logs local; never paste raw error dumps into docs |
| Package surface widens silently | npm tarball includes state root or fixtures with private data | Inspect `npm pack --dry-run --json` before release |
| Model prompt leaks private context | Nightly consolidation sends raw proprietary snippets to an unintended model | Keep model CLI explicit, document provider boundary, and avoid third-party SaaS paths without approval |
| Stale public docs mislead contributors | README claims an old version or missing adapter support | Align docs with `package.json`, changelog, and current code before onboarding contributors |

## Connector Policy

Before adding or expanding a connector such as Feishu docs, chat, calendar, meeting notes, or cloud drive:

1. State the user-visible value and the minimum source needed.
2. List the exact scopes or permissions.
3. Mark the identity used: user or bot.
4. Record where imported content is stored.
5. Define the retention and deletion behavior.
6. Add source labels so users can trace every card or memory back to the connector.
7. Default raw content to local-only storage.
8. Keep host-context injection opt-in through `injection_policy`, not connector presence.
9. Document how failures behave when auth expires or a scope is missing.

Connector imports should produce sanitized summaries, evidence references, or asset candidates. They should not turn OpenRelix into a raw cloud mirror.

## Product Analytics Policy

Official macOS panel client builds include the OpenRelix analytics endpoint and
client ingest token, so first-time installs and updates may send privacy-bounded
product analytics by default without extra user setup. Custom builds can
override the endpoint or disable the endpoint fallback with
`OPENRELIX_ANALYTICS_DEFAULT_ENABLED=0`. Runtime users retain an in-app menu
toggle and environment kill-switches (`OPENRELIX_ANALYTICS_ENABLED=0` or
`OPENRELIX_ANALYTICS_DISABLED=1`).

Allowed analytics fields:

- Random install ID and per-launch session ID.
- App version and coarse macOS version.
- Fixed event names: app launch, app quit, panel load state, panel ready,
  module visible/hidden, core control click, and Skill/MCP quarantine action
  outcomes.
- Fixed module/control IDs from a whitelist.
- Dwell duration in milliseconds and panel language (`zh` or `en`).
- Skill/MCP quarantine action/result/bucket enum values plus bounded aggregate
  counts for affected items, failures, and migration notices.

Analytics must not include prompts, model responses, memory/review text, window
titles, project names, skill/MCP names, entity keys, file paths, usernames,
hostnames, tokens, cookies, connector content, local reports, raw state files,
raw error messages, or generated panel content.
The client should drop unrecognized event/property keys before sending and
should not persist failed analytics payloads to disk. Any token embedded in the
macOS app must be treated as a client-side ingestion token, not a service secret;
privileged analytics credentials belong behind the collection endpoint.

## Host Context Policy

OpenRelix may write bounded summaries into enabled host-native contexts, but only through managed blocks.

Allowed:

- Add or update OpenRelix-managed blocks.
- Preserve host-owned lines before and after the managed block.
- Report disabled or missing host context without forcing writes.
- Keep a compiled copy in `runtime/host-context/`.

Not allowed:

- Replace a full host memory file.
- Write personal memory into a project repo file by default.
- Inject `on_demand`, `local_only`, `never`, low-priority, or unapproved legacy rows into host context.
- Treat old durable/session labels as enough to inject.

## Public Repo And Package Policy

Public repo and package artifacts may include:

- Source code.
- Installer and LaunchAgent templates.
- Public docs.
- Sanitized fixtures.
- Sanitized screenshots or examples.
- Repo-local skills intended for release.

They must not include:

- Real state root content.
- Raw host histories, sessions, transcripts, or generated reports.
- Internal URLs, account names, tokens, cookies, secrets, raw logs, or proprietary snippets.
- Development-only harness skills in the public plugin/package surface unless an explicit release decision says so.

## Review Gates

Small changes:

```bash
python3 scripts/check_personal_info.py
git diff --check
python3 -m unittest tests/<focused_test>.py
```

Python, installer, docs/site, release, or package-surface changes:

```bash
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest discover -s tests
npm pack --dry-run --json
```

When checking package output, inspect the file list. Do not rely only on a successful exit code.

## Deletion And Recovery Expectations

Users should be able to:

- Delete the local state root through the uninstall path when they choose `--delete-local-memory`.
- Remove OpenRelix-managed host-context blocks without deleting host-owned memory.
- Rebuild the SQLite sidecar from source state files.
- Regenerate reports from registry, raw, and consolidated sources.
- Disable host-context sync with local-only memory modes.

Contributors should design new outputs so they are either source-of-truth records with clear ownership or rebuildable artifacts. Ambiguous caches are where privacy bugs tend to hide.

## Contributor Checklist

Before merging a feature that reads, writes, syncs, imports, or publishes data:

- [ ] The source and destination are named.
- [ ] Runtime paths are configurable.
- [ ] No real user data is in repo files, docs, fixtures, screenshots, or tests.
- [ ] Host-owned files are preserved outside managed blocks.
- [ ] Connector permissions are least-privilege and documented.
- [ ] Sensitive values are redacted before persistence or display.
- [ ] Package and docs surfaces were checked if changed.
- [ ] Verification commands are recorded in the handoff.
