# OpenRelix™

[English](README.md) | [简体中文](https://github.com/openrelix/openrelix/blob/main/README.zh-CN.md)

Open-source personal memory relics for AI coding agents, currently published as a v0.3.10 preview.

OpenRelix™ is a local-first asset layer for AI coding agents. It turns finished agent work into reusable task reviews, skills, templates, bounded memory summaries, and a private dashboard instead of leaving useful patterns buried in old chats.

The name means open-source personal memory relics: reusable work stays organized locally, while only sanitized, bounded summaries are shared with the active AI host.

The project is intentionally not tied to one AI host. The current preview supports Codex CLI / Codex app-server and Claude Code CLI for the core local-memory workflow, while keeping the adapter boundary open for other AI CLI / agent hosts.

GitHub project page: [openrelix/openrelix](https://github.com/openrelix/openrelix). If this project helps your workflow, a star is welcome.

## What lives in the repo

- `AGENTS.md`: repo instructions for maintaining the system itself.
- `.agents/skills/`: canonical repo-local skills for reusable agent workflows.
- `.agents/plugins/`: Codex plugin marketplace metadata.
- `install/`: one-command installer and user config helpers.
- `ops/launchd/`: macOS LaunchAgent templates.
- `plugins/`: packaged Codex plugin bundles for shared skills.
- `scripts/`: collectors, nightly consolidation, overview generation, and token live server.
- `templates/`: review schema, asset entry templates, and asset / skill generation templates.
- `docs/`: operating model, technical design, developer guide, privacy boundary, and reporting notes.

## Documentation

- [Docs Index](docs/README.md): bilingual map for agent-readable Markdown docs and rich HTML pages.
- [Technical Solution](docs/technical-solution.md): architecture, data flow, module responsibilities, runtime state, and release boundaries.
- [Detailed Developer Guide](docs/developer-guide.md): 10-minute local loop, repository map, common task paths, task card template, validation workflow, and contributor handoff rules.
- [Validation Matrix](docs/validation-matrix.md): change-type specific checks for docs, scripts, installer, memory, overview, package, and release work.
- [Data Contracts](docs/data-contracts.md): state-root layout, raw windows, summaries, registries, curated pack, host context, overview data, and sample fixture contracts.
- [Privacy Threat Model](docs/privacy-threat-model.md): contributor and connector privacy gates for host data, runtime state, package surface, and external integrations.
- [Release Checklist](docs/release-checklist.md): version, changelog, package, GitHub release, npm, and public-doc release gates.
- [Open Source Install And Project Overview](docs/open-source-install-and-project-overview.md): Chinese install guide and project explanation for the current macOS preview release.
- [Product Showcase](docs/product-showcase.html): visual product introduction and sanitized panel previews.
- [Getting Started Guide](docs/getting-started.html): user-facing panel guide with real Chinese / English UI example screenshots and section-by-section usage notes for the core panel modules.
- [System Overview](docs/system-overview.md): layered operating model for AI hosts, repo source, runtime state, and local memory.
- [Privacy And Distribution Boundary](docs/privacy-and-distribution.md): what belongs in the public repo and what must stay local.
- [Metric Dictionary](docs/metric-dictionary.md): counting rules and interpretation notes for generated reports and the panel.

## Public showcase

The static showcase is ready for GitHub Pages. Configure Pages to deploy from
the `main` branch and `/docs` folder, then the public entry point is:

```text
https://openrelix.github.io/openrelix/
```

## License And Trademarks

The source code is released under the [MIT License](LICENSE). Project names,
logos, package names, and other source-identifying marks are governed separately
by the [Trademark Policy](TRADEMARKS.md).

OpenRelix™ and openrelix™ are trademarks of the project maintainer. The source code is licensed under MIT; trademark rights are not granted by the MIT License.

## Current Adapter Support

The current preview is macOS-only. The supported install path assumes:

- macOS with user-level `launchd` / `LaunchAgent`
- Node.js 18+ with `npm` / `npx`
- `zsh`
- Python 3.10+
- Codex CLI with a writable `CODEX_HOME`, defaulting to `~/.codex`
- Optional Claude Code CLI with a writable `CLAUDE_HOME`, defaulting to `~/.claude`, when you want Claude Code windows, Token usage, native context, or model-backed consolidation. If your Claude CLI auth depends on a custom `CLAUDE_CONFIG_DIR`, pass it through an external env file with `--claude-env-file`.
- For model-backed learning refresh, a working `model_cli`. The default is Codex through `codex exec --model gpt-5.4-mini`; `--model-cli claude` uses `claude -p --model <claude_model>` instead. OpenRelix passes the model explicitly for review, backfill, hourly learning refresh, and nightly summaries without changing either host's global default. If Codex reports `401`, `Unauthorized`, or `invalid_issuer`, first confirm `codex exec` works in a normal terminal. Shared/proxy Codex providers must keep `CODEX_HOME/auth.json` and `CODEX_HOME/config.toml` together because `model_provider/base_url` is not stored in `auth.json`; official OpenAI API key setups should also check or clear an invalid `OPENAI_API_KEY`.

Linux and Windows support are future work. Some lower-level Python scripts are written to keep paths configurable, but the public installer and background automation should be treated as macOS-only for this release.

The current public adapters cover Codex CLI / Codex app-server and Claude Code CLI. Codex support includes `CODEX_HOME`, app-server threads, history/session files, native memories, skills, and custom prompts. Claude Code support maps local transcripts, Token usage, `CLAUDE.md` native context, and model-backed consolidation into the same OpenRelix state root. Gemini CLI and other AI CLI / coding-agent hosts remain future adapter targets.

Codex app-server collection is part of the default Codex adapter path. By default, OpenRelix uses `--activity-host all` and `--activity-source auto`: it reads Claude Code transcripts when present, tries `codex app-server` for Codex threads, maps every host into the same raw window format with an `ai_host` field, and falls back to `CODEX_HOME/history.jsonl` plus `CODEX_HOME/sessions/**/*.jsonl` when app-server is unavailable.

On macOS, OpenRelix also detects running Codex desktop profiles by reading the process environment for `CODEX_HOME` and `CODEX_ELECTRON_USER_DATA_PATH`. This lets the panel collect windows from multiple active Codex homes and focus the matching desktop profile without falling back to the global `codex://` URL scheme for isolated homes. For extra homes that are not currently running, set `OPENRELIX_EXTRA_CODEX_HOMES` or `OPENRELIX_CODEX_HOMES` to a comma-separated list.

```bash
npx openrelix install --activity-source auto
npx openrelix install --activity-source history
npx openrelix install --activity-host all --model-cli claude
python3 scripts/collect_codex_activity.py --date "$(date +%F)" --activity-source app-server
python3 scripts/collect_codex_activity.py --date "$(date +%F)" --activity-host claude
openrelix doctor --app-server-check
OPENRELIX_ACTIVITY_SOURCE=app-server openrelix review --date "$(date +%F)"
```

Use `--activity-host codex`, `--activity-host claude`, or `--activity-host all` to choose which host windows are collected. Use `--activity-source history` only when you want to force Codex's stable CLI history/session collector. Use `--activity-source app-server` or `OPENRELIX_ACTIVITY_SOURCE=app-server` for a one-off strict Codex app-server run. `--read-codex-app` remains accepted as a compatibility alias for `--activity-source auto`.

## Dependency notes

The one-line npm install should not require a separate project setup step after the machine prerequisites above are present:

- No `pip install ...` step is required. The shipped Python scripts use the Python standard library.
- No `npm install` step is required. The npm package is a bootstrapper and does not declare runtime npm dependencies.
- No manual LaunchAgent setup is required. The installer renders and bootstraps LaunchAgents when background services are enabled.
- Token usage metrics are optional. The panel fetches Codex data with `npx -y ccusage@latest codex daily` and Claude Code data with `npx -y ccusage@latest claude daily` on demand. The default view merges both providers; if either command is unavailable or offline, the rest of the panel still works and Token cards show a fallback, partial, or cached state.

If Python 3.10+ is missing on macOS, install Python first, then rerun the installer:

```bash
brew install python
npx openrelix install
```

## What does not need to live in the repo

Fresh installs should keep user state outside the repository. The installer creates or reuses a state root that contains:

- `registry/`: asset registry, usage events, and nightly memory items.
- `reviews/`: sanitized task reviews.
- `raw/`: collected AI host activity grouped by day and window, with `ai_host` distinguishing Codex and Claude Code.
- `consolidated/`: nightly organization output.
- `reports/`: generated overview markdown, JSON, CSV, and HTML panel.
- `runtime/`: token cache and adapter runtime such as isolated nightly Codex / Claude homes.
- `log/`: background task logs.

By default the installer uses:

- `~/Library/Application Support/openrelix`

You can override this with `AI_ASSET_STATE_DIR` or `./install/install.sh --state-dir ...`.
For continuity after the package rename, legacy state roots may be reused only when the new `openrelix` root does not exist and no explicit state root is set.

## Quick start

These commands are for macOS current preview.

One-line `npx` install:

```bash
npx openrelix install
```

When run in an interactive terminal, the installer prompts you to choose `中文 (zh)` or `English (en)`. Non-interactive installs default to `zh`; pass `--language` to make automation explicit.

English `npx` install:

```bash
npx openrelix install --language en
```

Recommended full `npx` install:

```bash
npx openrelix install --enable-learning-refresh --keep-awake=during-job --enable-update-check
```

Minimal install:

```bash
./install/install.sh --minimal
```

The default install profile is `integrated`. It installs the local shell command, global skill symlink, lightweight macOS client, background refresh services, and nightly organization LaunchAgents by default. Minimal install initializes the state root, generates the first overview, enables bounded host context, and syncs one shared bounded memory summary into managed OpenRelix blocks inside configured host contexts: Codex `memory_summary.md` and Claude Code `CLAUDE.md`. It preserves host-owned content outside those blocks. It still does not install shell commands, change shell rc files, or bootstrap LaunchAgents. Use `--minimal --record-memory-only` when you want a minimal install that records only to this system's local state root without host-context injection.

For a repo-checkout smoke test that stops at the generated panel and does not touch your real state root or real `CODEX_HOME`, run:

```bash
scripts/smoke_temp_panel.sh
```

The script creates temporary state and Codex home directories, runs a `--minimal --record-memory-only` install, prints `doctor` / `core` output, and opens the generated `reports/panel.html`. Use `--no-open` in terminal-only or CI-style checks:

```bash
scripts/smoke_temp_panel.sh --no-open
```

By default this is an empty-state smoke test. To inspect the panel with recent data from your current OpenRelix state while still rendering into a temporary directory, seed the temporary state explicitly:

```bash
scripts/smoke_temp_panel.sh --seed-current-state
```

Clean up the temporary smoke directories when you are done:

```bash
scripts/cleanup_smoke_temp.sh --dry-run
scripts/cleanup_smoke_temp.sh --yes
```

Uninstall OpenRelix local integrations:

```bash
npx openrelix uninstall
```

The uninstall command removes the LaunchAgents, `~/Applications/OpenRelix.app`, the installer-managed `openrelix` shell entrypoint, the user-level `memory-review` skill symlink, the custom-prompt fallback, and the managed shell `PATH` block. In an interactive terminal it asks whether to delete the local memory state root as well. For unattended runs, choose explicitly:

```bash
npx openrelix uninstall --keep-local-memory
npx openrelix uninstall --delete-local-memory
```

`--delete-local-memory` deletes the active state root and removes the managed OpenRelix blocks inside `CODEX_HOME/memories/memory_summary.md` and `CLAUDE_HOME/CLAUDE.md`. It preserves host-owned content outside those blocks and does not delete your whole `CODEX_HOME`, your whole `CLAUDE_HOME`, host auth, or host history/session files.

The installer stores the selected runtime language, memory mode, activity source, activity host, model CLI, Codex model, Claude model, and token budget in the state root under `runtime/config.json`. Supported language values are `zh` and `en`; interactive installs prompt when no language is passed, and non-interactive installs default to `zh`. The language controls local terminal output, generated overview files, nightly summary prompts, fallback summaries, immediate task reviews, asset / usage-event human-facing fields, and the structured memory items written by the local consolidation pipeline. Stable enum keys stay canonical so automation can still classify records, while the visible fields follow the selected language.

```bash
./install/install.sh --language zh
./install/install.sh --language en
```

Memory is on by default. The default mode is `integrated`: the system records one reusable personal-memory registry into the active state root, then syncs a bounded summary into enabled host-native contexts. Codex and Claude Code read the same shared personal memory summary for context injection, but the panel keeps OpenRelix personal memory out of the host-native memory views. Use `--record-memory-only` when you want strict local recording without context injection, or `--disable-personal-memory` to disable this system's local memory writes.

The context sync is intentionally compressed: duplicate personal memories are merged by signature, injection policy decides whether an item can enter host context, low-priority items stay local-only, and the injected summary targets about 6.7K tokens with an 8K hard budget. By default, global memory can use up to 10% of the configured summary budget and project memory can use up to 30%; with the default 8K budget, that is about 800 tokens for global memory plus 2.4K tokens for project memory. Both global and project context participate in one unified host-context summary, selected by priority, heat, and freshness within those budgets.

```bash
./install/install.sh --record-memory-only
./install/install.sh --disable-personal-memory
```

`--record-memory-only` keeps the personal memory system on, enables enough host history for local collection, disables host-native memory context, and keeps bounded memory-summary sync off. `--disable-personal-memory` records the mode as `off` and skips local memory-registry writes. `--use-integrated` is the explicit alias for the default mode.

Recommended integrated install with global skill symlinks, bounded history config, the `openrelix` shell command, the lightweight macOS client, default nightly organization, hourly automatic learning refresh, a daily update check, and sleep protection while nightly jobs are running:

```bash
./install/install.sh --enable-learning-refresh --keep-awake=during-job --enable-update-check
```

The integrated profile does this:

1. Initializes the active state root and generates the first overview.
2. Enables bounded host history and host-native memory context by default.
3. Installs the repo-provided `memory-review` skill globally by symlinking it into `~/.codex/skills/`.
4. Installs the repo-provided custom prompt into `~/.codex/prompts/memory-review.md` as a compatibility fallback.
5. Installs the global `openrelix` shell command and ensures the chosen user bin directory is on `PATH`.
6. Builds the lightweight macOS client in the state root, then installs a real app bundle at `~/Applications/OpenRelix.app` when `swiftc` is available.
7. Renders and bootstraps macOS LaunchAgents for:
   - overview refresh every hour by default; with `--enable-learning-refresh`, this reads the configured activity host and runs model-backed consolidation through the configured `model_cli`. Use `--overview-refresh-interval-minutes` to change the interval
   - token live server
   - nightly preview at `23:00`
   - nightly finalize for the previous day at `00:10`
   - optional npm update check at `09:30` when `--enable-update-check` is passed

For Chinese runtime language, the manual refresh and nightly pipelines automatically maintain a local Codex-native display cache so memory cards get readable Chinese titles and summaries by default. Set `OPENRELIX_ENABLE_NATIVE_DISPLAY_POLISH=0` to keep those pipelines strictly source-text only. The generated display cache stays in the local state root.

When you need an immediate task review inside the active AI coding agent, type the plain-text skill trigger so Codex CLI does not reject it as an unsupported slash command:

```text
memory-review
```

The custom prompt compatibility route is:

```text
/prompts:memory-review
```

After the installer finishes, it prints recommended next steps. The first action is to open the local panel or the macOS client:

```bash
openrelix app
```

Recommended after install: the installer can enable automatic learning refresh every hour by default:

```bash
npx openrelix install --enable-learning-refresh
```

This option is intentionally explicit: the default background `overview-refresh` does not learn memory from recent windows, while `--enable-learning-refresh` makes that LaunchAgent read the configured activity host, learn from recent AI host windows, update this system's local memory and overview, and keep host-context injection bounded. It runs every 60 minutes by default; after installation, use `openrelix schedule --overview-refresh-interval-minutes 30` or another positive minute value to change the interval. Chinese runtime language may still maintain the small Codex-native display cache described above. If the global `openrelix` command was not installed, the installer prints a direct `python3 scripts/openrelix.py ...` fallback command with the selected state root and host homes.

The integrated installer also provides a shell entrypoint:

```bash
openrelix open panel
openrelix app
openrelix core
openrelix mode
openrelix review
openrelix update --check
openrelix update --yes
```

On macOS, `openrelix app` builds and opens a lightweight native client installed at
`~/Applications/OpenRelix.app`. It is an AppKit/WebKit shell over
the same local `reports/panel.html`, so it adds no Electron runtime or hosted
service. From a repo checkout, you can also run `./scripts/build_macos_client.sh
--open` to build a local `dist/OpenRelix.app`.

The macOS client includes privacy-bounded product analytics for the panel. When
`OPENRELIX_ANALYTICS_ENDPOINT` is configured, anonymous usage metrics are shared
by default so maintainers can understand daily active use, module usefulness,
and product friction. Events cover app launch, panel load state, fixed panel
module visibility and dwell time, core control clicks, and app quit. Payloads
use a random install ID and per-launch session ID plus app/coarse macOS version;
they do not include prompts, memory or review text, window titles, file paths,
usernames, hostnames, tokens, cookies, local reports, or raw OpenRelix state.
Users can turn this off from the OpenRelix app menu with `Share Anonymous Usage
Metrics`, or by launching with `OPENRELIX_ANALYTICS_ENABLED=0` /
`OPENRELIX_ANALYTICS_DISABLED=1`. The endpoint can be embedded at build time
with `scripts/build_macos_client.sh --analytics-endpoint <url>` or supplied at
launch time with `OPENRELIX_ANALYTICS_ENDPOINT`. `OPENRELIX_ANALYTICS_TOKEN` or
`--analytics-token` is optional and, when present, is sent as a bearer token to
the configured endpoint. Treat embedded tokens as client-side ingestion tokens,
not service secrets; keep privileged analytics keys server-side.

For an 800 DAU first pass, the repo includes a PostHog + Cloudflare Worker
collector template and dashboard recipe at
`analytics/posthog-worker/README.md`. It forwards only the whitelisted schema to
PostHog Product Analytics and is intentionally kept outside the npm package
allowlist until there is an explicit release decision. AI-assisted panel feature
work should also follow the analytics governance checklist at
`analytics/posthog-worker/analytics-governance.zh-CN.md` so event tables,
Chinese labels, focused tests, and PostHog dashboard cards stay in sync.

For release updates, use `openrelix update --check` in automation and `openrelix update --yes` when you actually want to reinstall the latest npm package. If the package is already current but the local app, LaunchAgents, or generated panel need to be resynced, use `openrelix update --yes --force`. The in-panel update button uses that repair path automatically, then reloads the regenerated panel. The daily check is intentionally no-mutation; `09:30` is the default because it avoids the `23:00` nightly preview and `00:10` previous-day finalize windows.

If the chosen bin directory is not already on `PATH`, the installer appends a managed `PATH` block to your active shell rc file and prints the one-line `export PATH=...` command for the current shell.

By default, the installer and routine `review` / `backfill` / `refresh` commands maintain the same bounded global-plus-project summary as managed OpenRelix blocks in enabled host contexts so Codex and Claude Code can read compressed personal memory. The full local asset memory still lives in the active state root, while each host remains the owner of its native files and OpenRelix only updates its own block. Use `--record-memory-only` or `--no-memory-summary` when you want to keep this system's memory out of host-native context.

You can also build a custom profile by starting from the minimal default and adding explicit flags such as `--install-global-skills`, `--install-global-command`, `--enable-background-services`, `--record-memory-only`, `--disable-personal-memory`, `--enable-memories`, `--enable-history`, or `--sync-memory-summary`.

## npm Distribution

The npm package is only a bootstrapper. It ships this repository's installer, skills, templates, scripts, and docs, then runs `install/install.sh` from the npm package cache. The installer remains the single source of truth.

Before publishing, validate the package contents:

```bash
npm pack --dry-run
```

Publish the public preview to the currently configured registry after logging in:

```bash
npm login
npm publish --access public
```

## Public Launch Checklist

Before opening the repository and package broadly, keep the public evidence path consistent:

- Use `OpenRelix™` on the first visible brand mention in the README, showcase, release notes, and npm page.
- Keep `openrelix™` as the CLI mark and `openrelix` as the npm package name.
- Publish a GitHub release and tag matching the `package.json` version, using `v<version>`.
- Enable GitHub Pages from the `main` branch and `/docs` folder.
- Save screenshots of the GitHub README, npm package page, release page, and GitHub Pages showcase after publication.
- Do not use `OpenRelix®` or `openrelix®` unless registration has issued for the relevant mark and jurisdiction.

## License

This project is released under the MIT License.

Copyright (c) 2026 [kk_kais](https://www.npmjs.com/~kk_kais).

The license allows free personal use, copying, modification, distribution, and sublicensing, as long as the copyright notice and license text are included in copies or substantial portions of the software. See `LICENSE` for the full terms.

## Project context detection

- The overview groups work by the `cwd` captured from each AI host window.
- It prefers the detected project root, using Git roots first and then common project markers such as `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, Gradle files, or Xcode workspaces.
- If no project root can be inferred, it falls back to broader context labels such as `Codex 本地环境`, `OpenRelix`, or `个人工作区`.
- There is no hard-coded repository name in the context detection path.

## How skills load

- When the active AI host supports repo-local skills, skills under `.agents/skills/` are discoverable automatically. The current preview adapter targets Codex discovery.
- If you want the same skill to be available from any repo, install it into the user-level skill root for the active host. The default `integrated` profile does this automatically; use `--install-global-skills` when building a custom profile.
- This repository does not rely on hooks to make skills discoverable globally. Hooks are optional lifecycle automation; skill availability comes from repo-local discovery or user-level installation.
- Development harness skills under `.agents/skills/openrelix-*-harness/` are repo-maintenance helpers for designing, implementing, testing, and compliance-checking OpenRelix itself. They are intentionally excluded from the npm package; the public package skill surface remains `.agents/skills/memory-review/`.

## Plugin status

The Codex plugin directory is the packaged Codex route for shared OpenRelix skills. It includes the `memory-review` skill and repo marketplace metadata, while the installer remains the full user-local integration path for config, LaunchAgents, shell commands, and custom-prompt fallback.

## Runtime commands

These commands require the `openrelix` shell entrypoint from the default `integrated` profile or `--install-global-command`.

Refresh the overview snapshot:

```bash
openrelix refresh
```

Refresh and immediately synthesize memory from today's windows with the last 7 days as context:

```bash
openrelix refresh --learn-memory --learn-window-days 7
```

Show or adjust the schedule for already-installed background jobs:

```bash
openrelix schedule
openrelix schedule --overview-refresh-interval-minutes 30
openrelix schedule --nightly-organize-time 22:30 --nightly-finalize-time 01:00
```

Open the generated panel:

```bash
openrelix open panel
```

Open the same panel in the lightweight macOS client:

```bash
openrelix app
```

Print the current core metrics in the terminal:

```bash
openrelix core
```

Check the local runtime and model authentication path:

```bash
openrelix doctor
openrelix doctor --model-check
```

View or switch the memory mode without reinstalling:

```bash
openrelix mode
openrelix mode integrated
openrelix mode local-only
openrelix mode off
```

Run today's review pipeline only when you want an immediate local consolidation:

```bash
openrelix review
```

Run a one-off manual review that first backfills missing or non-final daily reports in the previous 7 days, then learns from that 7-day window before generating today's memories and report:

```bash
openrelix review --date "$(date +%F)" --learn-window-days 7
```

Backfill several past days in one command:

```bash
openrelix backfill --from 2026-04-24 --to 2026-04-27 --learn-window-days 7
```

Backfill specific non-contiguous dates:

```bash
openrelix backfill --dates 2026-04-21,2026-04-23,2026-04-24 --learn-window-days 7
```

Backfill collection is local, but synthesis is not purely offline: raw AI host activity collection is handled by local scripts, while each target date's structured summary is generated through the configured `model_cli` (`codex exec --ephemeral` by default, or `claude -p` when selected).

In the default `integrated` mode, review, backfill, and refresh also regenerate the shared bounded summary for enabled host contexts. They still keep full local registry data under the state root, do not compile `MEMORY.md` task-group routes into the injected summary, and do not write raw windows into host-native memory. Personal-memory candidates do not have a fixed item cap; the generated summary is bounded by a configurable token budget instead.

Show or update runtime config:

```bash
openrelix config
openrelix models
openrelix tokens --provider all
openrelix tokens --provider codex
openrelix tokens --provider cc
openrelix config --codex-model gpt-5.4-mini
openrelix config --model-cli claude --claude-model sonnet
openrelix config --activity-host all
openrelix config --memory-summary-max-tokens 8000
```

`openrelix models` reads the current local Codex CLI model catalog through `codex debug models` and prints a sanitized list of selectable model IDs. `openrelix tokens` defaults to `--provider all`, merging Codex `ccusage codex daily` and Claude Code `ccusage claude daily`; pass `--provider codex` or `--provider cc` for a single host. `codex_model` defaults to `gpt-5.4-mini`, `claude_model` defaults to `sonnet`, and `model_cli` selects which CLI OpenRelix uses for internal memory consolidation. `memory_summary_max_tokens` defaults to 8000 and accepts values from 2000 to 20000. Target and warning budgets are derived automatically from that max. Updating config refreshes the summary, overview, and panel by default; add `--no-refresh` when you only want to persist the config.

Host context can be resynced at any time. It compiles one unified summary from eligible global and project personal memories, then writes that same bounded summary into OpenRelix-managed blocks in the enabled Codex / Claude Code host targets. Codex and Claude Code use the same selection policy: global context is capped at 10% of the configured summary budget, and project context is capped at 30%. The compiled summary stays in the OpenRelix state root under `runtime/host-context/memory_summary.md`; OpenRelix does not write personal memory into the project repository by default and does not replace host-owned native memory outside its managed blocks.

```bash
openrelix context sync
```

OpenRelix also maintains a local SQLite sidecar index for memory and window lookup. The source of truth stays in the state root's `raw/`, `registry/`, and `consolidated/` files; the database under `runtime/openrelix-index.sqlite3` is rebuildable and can be deleted. Routine `refresh` and nightly runs rebuild it on a warning-only path so search freshness does not block raw capture or JSONL memory writes.

```bash
openrelix index status
openrelix index rebuild
openrelix index search-memory sqlite --bucket durable
openrelix index search-window "review loop" --project openrelix
openrelix paths
```

Advanced fallback:

```bash
python3 scripts/build_overview.py
```

Migrate older repo-local runtime data into the external state root:

```bash
python3 scripts/migrate_legacy_state.py
```

Manual open fallback:

```bash
open "${AI_ASSET_STATE_DIR:-$(python3 - <<'PY'
import sys
sys.path.insert(0, 'scripts')
from asset_runtime import default_state_root
print(default_state_root())
PY
)}/reports/panel.html"
```

## Privacy boundary

- Store only sanitized and durable knowledge.
- Do not commit raw Codex history, reports, logs, or runtime caches.
- Do not store secrets, tokens, credentials, raw internal logs, or user data.
- Treat third-party memory providers as optional integrations rather than the default storage layer.

## Notes for maintainers

- Keep canonical reusable logic in the repo.
- Keep generated state outside the repo for new installs.
- Do not reintroduce hard-coded absolute user paths into scripts or templates.
