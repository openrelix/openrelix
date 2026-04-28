# OpenRelix™

Open-source personal memory keepsake for AI coding agents, currently published as a v0.1.0 preview.

OpenRelix™ is a local-first asset layer for AI coding agents. It turns finished agent work into reusable task reviews, skills, templates, bounded memory summaries, and a private dashboard instead of leaving useful patterns buried in old chats.

The name means an open-source personal memory keepsake: reusable work stays organized locally, while only sanitized, bounded summaries are shared with the active AI host.

The project is intentionally not tied to one AI host. The current v0.1.0 preview ships a Codex CLI adapter first because Codex exposes the history, session, skill, and memory surfaces needed for a working local installer. Other AI CLI / agent hosts can be added through adapter layers without changing the product goal.

GitHub project page: [openrelix/openrelix](https://github.com/openrelix/openrelix). If this project helps your workflow, a star is welcome.

## What lives in the repo

- `AGENTS.md`: repo instructions for maintaining the system itself.
- `.agents/skills/`: canonical repo-local skills for reusable agent workflows.
- `.agents/plugins/`: draft plugin marketplace metadata, not part of the v0.1.0 preview install path.
- `install/`: one-command installer and user config helpers.
- `ops/launchd/`: macOS LaunchAgent templates.
- `plugins/`: draft plugin bundles for future packaging work.
- `scripts/`: collectors, nightly consolidation, overview generation, and token live server.
- `templates/`: review schema and asset entry templates.
- `docs/`: operating model, technical design, learning guide, privacy boundary, and reporting notes.

## Documentation

- [Technical Solution](docs/technical-solution.md): architecture, data flow, module responsibilities, runtime state, and release boundaries.
- [Learning Guide](docs/learning-guide.md): a practical path for users, contributors, and maintainers to understand and validate the project.
- [Open Source Install And Project Overview](docs/open-source-install-and-project-overview.md): Chinese install guide and project explanation for the current macOS v0.1.0 preview release.
- [Product Showcase](docs/product-showcase.html): visual product introduction and sanitized panel previews.
- [System Overview](docs/system-overview.md): layered operating model for AI hosts, repo source, runtime state, and local memory.
- [Privacy And Distribution Boundary](docs/privacy-and-distribution.md): what belongs in the public repo and what must stay local.
- [Trademark Filing Kit](docs/trademark-filing-kit.md): filing checklist and open source brand boundary notes.
- [Dual Trademark Filing Action Sheet](docs/trademark-dual-filing-action-sheet.md): U.S. and China same-day filing packet for the `OPENRELIX` word mark.
- [China Trademark Filing Kit](docs/china-chinese-trademark-filing-kit.md): China filing packet for the `OPENRELIX` word mark.
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

v0.1.0 preview is macOS-only. The supported install path assumes:

- macOS with user-level `launchd` / `LaunchAgent`
- Node.js 18+ with `npm` / `npx`
- `zsh`
- Python 3.10+
- Codex CLI with a writable `CODEX_HOME`, defaulting to `~/.codex`

Linux and Windows support are future work. Some lower-level Python scripts are written to keep paths configurable, but the public installer and background automation should be treated as macOS-only for this release.

The first public adapter targets Codex CLI. Several v0.1.0 preview capabilities depend on Codex-specific surfaces, including `CODEX_HOME`, Codex history/session files, Codex native memories, Codex skills, and Codex custom prompts. The product direction remains AI-agent-first: future hosts should plug in by mapping their own history, skill, memory, and command surfaces into the same local asset model.

Expected adapter work after v0.1.0 preview includes Claude Code, Gemini CLI, and other AI CLI / coding-agent hosts that expose local history, command, skill, or memory surfaces. These are roadmap targets, not current support guarantees.

An experimental Codex app-server activity source is available for local testing. It reads Codex threads through `codex app-server` and maps them back into the same raw window format used by the existing history/session collector. It is opt-in only; the default collector still reads `CODEX_HOME/history.jsonl` and `CODEX_HOME/sessions/**/*.jsonl`.

```bash
npx openrelix install --profile integrated --enable-learning-refresh --read-codex-app
npx openrelix install --profile integrated --activity-source auto
python3 scripts/collect_codex_activity.py --date "$(date +%F)" --activity-source app-server
OPENRELIX_ACTIVITY_SOURCE=app-server openrelix review --date "$(date +%F)"
```

Use `--read-codex-app` during install when you want installed `openrelix` commands and LaunchAgents to try app-server first and fall back to the stable history/session collector. Use `OPENRELIX_ACTIVITY_SOURCE=app-server` for a one-off strict app-server run, or `OPENRELIX_ACTIVITY_SOURCE=auto` for one-off fallback behavior.

## Dependency notes

The one-line npm install should not require a separate project setup step after the machine prerequisites above are present:

- No `pip install ...` step is required. The shipped Python scripts use the Python standard library.
- No `npm install` step is required. The npm package is a bootstrapper and does not declare runtime npm dependencies.
- No manual LaunchAgent setup is required. The installer renders and bootstraps LaunchAgents when background services are enabled.
- Token usage metrics are optional. The panel fetches them with `npx -y @ccusage/codex@latest` on demand; if that command is unavailable or offline, the rest of the panel still works and Token cards show a fallback state or cached data.

If Python 3.10+ is missing on macOS, install Python first, then rerun the installer:

```bash
brew install python
npx openrelix install
```

## What does not need to live in the repo

Fresh installs should keep user state outside the repository. The installer creates or reuses a state root that contains:

- `registry/`: asset registry, usage events, and nightly memory items.
- `reviews/`: sanitized task reviews.
- `raw/`: collected AI host activity grouped by day and window; Codex is the v0.1.0 preview source.
- `consolidated/`: nightly organization output.
- `reports/`: generated overview markdown, JSON, CSV, and HTML panel.
- `runtime/`: token cache and adapter runtime such as an isolated nightly Codex home.
- `log/`: background task logs.

By default the installer uses:

- `~/Library/Application Support/openrelix`

You can override this with `AI_ASSET_STATE_DIR` or `./install/install.sh --state-dir ...`.
For continuity after the package rename, legacy state roots may be reused only when the new `openrelix` root does not exist and no explicit state root is set.

## Quick start

These commands are for macOS v0.1.0 preview.

One-line `npx` install:

```bash
npx openrelix install
```

When run in an interactive terminal, the installer prompts you to choose `中文 (zh)` or `English (en)`. Non-interactive installs default to `zh`; pass `--language` to make automation explicit.

English `npx` install:

```bash
npx openrelix install --language en
```

Integrated `npx` install:

```bash
npx openrelix install --profile integrated --enable-learning-refresh --enable-nightly --keep-awake=during-job
```

Minimal install:

```bash
./install/install.sh
```

Minimal install initializes the state root, generates the first overview, enables the current Codex adapter's memories/history, and syncs a bounded memory summary into `CODEX_HOME`. It still does not install shell commands, change shell rc files, or bootstrap LaunchAgents. Use `--record-memory-only` when you want a minimal install that records only to this system's local state root without host-context injection.

The installer stores the selected runtime language and memory mode in the state root under `runtime/config.json`. Supported language values are `zh` and `en`; interactive installs prompt when no language is passed, and non-interactive installs default to `zh`. The language controls local terminal output, generated overview files, nightly summary prompts, fallback summaries, immediate task reviews, asset / usage-event human-facing fields, and the structured memory items written by the local consolidation pipeline. Stable enum keys stay canonical so automation can still classify records, while the visible fields follow the selected language.

```bash
./install/install.sh --language zh
./install/install.sh --language en
```

Memory is on by default. In the current Codex adapter, the default mode is `integrated`: the system records reusable memory into the active state root, enables Codex memories/history, and syncs a bounded summary into host-native context. Use `--record-memory-only` when you want strict local recording without context injection, or `--disable-personal-memory` to disable this system's local memory writes.

The context sync is intentionally compressed: duplicate personal memories are merged by signature, durable / session items are prioritized, low-priority items stay local-only, and the injected summary targets about 4.2K tokens with a 5K hard budget.

```bash
./install/install.sh --profile integrated --record-memory-only
./install/install.sh --profile integrated --disable-personal-memory
```

`--record-memory-only` keeps the personal memory system on, enables enough Codex history for local collection, disables Codex native memory context, and keeps bounded memory-summary sync off. `--disable-personal-memory` records the mode as `off` and skips local memory-registry writes. `--use-integrated` is the explicit alias for the default mode.

Recommended integrated install with global skill symlinks, bounded history config, the `openrelix` shell command, 30-minute automatic learning refresh, nightly organization, and sleep protection while nightly jobs are running:

```bash
./install/install.sh --profile integrated --enable-learning-refresh --enable-nightly --keep-awake=during-job
```

The integrated profile does this:

1. Initializes the active state root and generates the first overview.
2. Enables bounded history and Codex native memory context by default.
3. Installs the repo-provided `memory-review` skill globally by symlinking it into `~/.codex/skills/`.
4. Installs the repo-provided custom prompt into `~/.codex/prompts/memory-review.md` as a compatibility fallback.
5. Installs the global `openrelix` shell command and ensures the chosen user bin directory is on `PATH`.
6. Renders and bootstraps macOS LaunchAgents for:
   - overview refresh every 30 minutes; with `--enable-learning-refresh`, this calls the current Codex adapter and learns from a 7-day window
   - token live server
   - nightly preview at `23:00`
   - nightly finalize for the previous day at `00:10`

When you need an immediate task review inside the active AI coding agent, the current Codex adapter exposes this skill entrypoint:

```text
/memory-review
```

The custom prompt compatibility route is:

```text
/prompts:memory-review
```

After the installer finishes, it prints recommended next steps. The first action is to open the local panel:

```bash
openrelix open panel
```

Recommended after install: the installer can enable automatic learning refresh every 30 minutes:

```bash
npx openrelix install --profile integrated --enable-learning-refresh
```

This option is intentionally explicit: the default background `overview-refresh` stays no-model, while `--enable-learning-refresh` makes that 30-minute LaunchAgent call the current Codex adapter, learn from recent AI host windows, update this system's local memory and overview, and keep host-context injection bounded. If the global `openrelix` command was not installed, the installer prints a direct `python3 scripts/openrelix.py ...` fallback command with the selected state root and host home.

The integrated installer also provides a shell entrypoint:

```bash
openrelix open panel
openrelix core
openrelix mode
openrelix review
```

If the chosen bin directory is not already on `PATH`, the installer appends a managed `PATH` block to your active shell rc file and prints the one-line `export PATH=...` command for the current shell.

By default, the installer and routine `review` / `backfill` / `refresh` commands write a bounded summary into `CODEX_HOME` so Codex can read the compressed context. The full local asset memory still lives in the active state root, while Codex remains the owner of `~/.codex/memories/`. Use `--record-memory-only` or `--no-memory-summary` when you want to keep this system's memory out of Codex native context.

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
- Publish a GitHub release and tag named `v0.1.0`.
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

- When the active AI host supports repo-local skills, skills under `.agents/skills/` are discoverable automatically. The v0.1.0 preview adapter targets Codex discovery.
- If you want the same skill to be available from any repo, install it into the user-level skill root for the active host. Use `--profile integrated` or `--install-global-skills` to do this with Codex symlinks in v0.1.0 preview.
- This repository does not rely on hooks to make skills discoverable globally. Hooks are optional lifecycle automation; skill availability comes from repo-local discovery or user-level installation.

## Plugin status

The plugin draft directory is a packaging surface for the current Codex plugin route. It is kept in the repo so future host-specific packages can reuse the same canonical skills, but v0.1.0 preview should be published and documented as installer-first. The repo marketplace entry marks the plugin as not available until the public plugin metadata, screenshots, and policy URLs are ready.

## Runtime commands

These commands require the `openrelix` shell entrypoint from `--profile integrated` or `--install-global-command`.

Refresh the overview snapshot:

```bash
openrelix refresh
```

Refresh and immediately synthesize memory from today's windows with the last 7 days as context:

```bash
openrelix refresh --learn-memory --learn-window-days 7
```

Open the generated panel:

```bash
openrelix open panel
```

Print the current core metrics in the terminal:

```bash
openrelix core
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

Backfill collection is local, but synthesis is not purely offline: the raw Codex activity collection is handled by local scripts, while each target date's structured summary is generated through `codex exec --ephemeral`.

In the default `integrated` mode, review, backfill, and refresh also regenerate the bounded `memory_summary.md` under `CODEX_HOME` so Codex can read the compressed context. They still keep full local registry data under the state root and do not write raw windows into Codex native memory. Personal-memory candidates do not have a fixed item cap; the generated summary is bounded by a configurable token budget instead.

Show or update the context summary budget:

```bash
openrelix config
openrelix config --memory-summary-max-tokens 8000
```

`memory_summary_max_tokens` defaults to 5000 and accepts values from 2000 to 20000. Target and warning budgets are derived automatically from that max. Updating it refreshes the summary, overview, and panel by default; add `--no-refresh` when you only want to persist the config.

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
