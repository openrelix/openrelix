# OpenRelix Plugin Draft

This repo-local plugin is a draft packaging surface for the reusable OpenRelix workflow. OpenRelix is not meant to be tied to one AI host; this directory is the current Codex plugin adapter draft.

Project page: [openrelix/openrelix](https://github.com/openrelix/openrelix). Stars are welcome if this workflow is useful.

The v0.1.0 preview public route is installer-first. Use:

```bash
./install/install.sh --profile integrated
```

The marketplace entry intentionally marks this plugin as `NOT_AVAILABLE` until public plugin metadata, screenshots, policy URLs, and release flow are ready.

What it includes:

- the canonical `memory-review` skill for immediate task review requests
- repo-local marketplace metadata under `.agents/plugins/marketplace.json`

What it does not try to do:

- it does not directly replace the installer
- it does not hard-code user-local data into the repo
- it does not make `/memory-review` a repo-global fact by itself
- it does not define the whole product around one host

The exact custom prompt compatibility entrypoint remains a user-local prompt installed by:

```bash
./install/install.sh --profile integrated
```

After integrated installer setup, the primary in-Codex entrypoint is:

```text
/memory-review
```

And the user-level custom prompt compatibility route is:

```text
/prompts:memory-review
```

That split is intentional:

- repo plugin = draft Codex packaging layer for shared capabilities
- installer = user-local adapter integration layer and custom-prompt fallback
