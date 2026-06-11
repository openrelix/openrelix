# Skill/MCP Quarantine PRD

> Languages: English | [简体中文](skill-quarantine-prd.md)

Version: 1.0
Date: 2026-06-05
Status: Implemented locally

## 1. Background

OpenRelix discovers local Skills, MCP servers, and reusable assets, then surfaces them in the workbench and in host-context candidates for Codex/Claude. As the local installation grows, unused Skills/MCP servers can still appear in default views or injection candidates.

The Quarantine feature adds a reversible quarantine layer. It does not delete assets. It makes inactive Skills/MCP servers opt-out by default, while keeping a clear path to restore them.

## 2. Goals

- Identify low-usage Skills/MCP servers from the last 30 days.
- Put newly added Skills/MCP servers into a 7-day optional isolation period.
- Support manual quarantine and restore.
- Support one-click quarantine for all suggested items.
- Hide quarantined items from default asset/MCP views and default injection candidates.
- Preserve source assets and local restore data.
- Move safe global Skills into a runtime quarantine directory; use state-only quarantine when mutation is unsafe.
- Isolate JSON MCP config entries with saved local restore payloads.
- Disable Codex TOML MCP servers with `enabled = false`, preserving the original TOML section for restore.
- Use state-only quarantine for unknown formats.

## 3. Non-Goals

- Do not delete Skill/MCP files.
- Do not upload quarantine state to third-party services.
- Do not move repository-owned Skills.
- Do not auto-edit unknown MCP config formats.

## 4. User Stories

### 4.1 Suggested Items

Users can open the OpenRelix workbench and see which Skills/MCP servers have not been used in the last 30 days.

Acceptance criteria:

- A new Skill/MCP Quarantine section appears in the workbench.
- The section shows suggested, quarantined, optional-isolation, and active counts.
- Each row shows type, name, 30-day usage, last-used time, added/configured time, and reason.
- Newly added unused items younger than 7 days are marked as optional isolation instead of suggested.

### 4.2 Manual Quarantine

Users can quarantine a single Skill/MCP server and hide it from default candidates.

Acceptance criteria:

- Every non-quarantined item has a quarantine action.
- Quarantining writes local runtime state.
- Movable global Skills are moved into the runtime quarantine directory.
- Repository Skills and unknown paths use state-only quarantine.
- Codex TOML MCP servers are disabled with `enabled = false` and keep their original section for restore.
- The workbench can refresh and show the updated state.
- Migration, config-write, and restore failures remain visible as migration notices.

### 4.3 One-Click Quarantine

Users can quarantine all suggested items in one action, and can explicitly quarantine all optional items after confirmation.

Acceptance criteria:

- The workbench exposes separate bulk actions for suggested items and optional items.
- The suggested bulk action only processes `suggested` items; the optional bulk action only processes `grace` items.
- The optional bulk action requires a separate confirmation because it overrides the 7-day protection period.
- The API returns quarantined count and item details.
- Partial failures do not undo successful quarantines.

### 4.4 Unblock

Users can restore a quarantined item.

Acceptance criteria:

- Quarantined rows expose an unblock action.
- Moved Skills are restored to their original path.
- Isolated JSON MCP config entries are restored with their saved payload.
- Disabled TOML MCP config sections are restored from their saved original text.
- Restore conflicts keep the entry quarantined and do not overwrite user files.

## 5. Product Surface

The workbench section includes:

- Top metrics: suggested, quarantined, optional, active.
- Bulk actions: quarantine all suggested items, or quarantine all optional items after confirmation.
- Item list grouped by status.
- Per-item actions: block and unblock.

CLI:

```bash
openrelix skill-quarantine list
openrelix skill-quarantine suggest
openrelix skill-quarantine blocked
openrelix skill-quarantine block skill:<id>
openrelix skill-quarantine unblock mcp:<id>
openrelix skill-quarantine block-all
openrelix skill-quarantine block-grace-all
```

All commands support `--json`.

## 6. Rules

- Lookback window: 30 days.
- Optional isolation period: 7 days.
- `usage_30d > 0`: active.
- `usage_30d == 0` and `age_days < 7`: grace.
- `usage_30d == 0` outside grace: suggested.
- Entries in runtime state: quarantined.

Isolation:

- Safe global Skills are moved to `{runtime_dir}/skill-mcp-quarantine/skills/`.
- Repository Skills and unknown paths use state-only quarantine.
- JSON MCP server entries are removed from config and saved locally for restore.
- Codex TOML MCP servers are disabled with `enabled = false` and saved locally for restore.
- Unknown MCP formats use state-only quarantine.

Hardening:

- Workbench and CLI mutations share a local runtime lock so concurrent clicks/commands do not move or write the same target at once.
- JSON/TOML MCP config files are backed up before mutation.
- Each isolation target records migration status; failures such as `move_failed`, `config_failed`, `toml_failed`, and `restore_conflict` are summarized as `migration_warnings`.
- The workbench shows persisted migration notices and immediate warning counts after an action.

Filtering:

- Quarantined Skills are filtered from default asset rows.
- Quarantined MCP servers are filtered from MCP usage/default injection candidates.
- The Quarantine section still lists quarantined rows for restore.

## 7. Data Model

State file:

```text
{runtime_dir}/skill-mcp-quarantine.json
```

The state contains `schema_version`, `updated_at`, and an `entries` map keyed by `skill:<id>` or `mcp:<id>`.
Entries also include `isolation_targets`, `migration_warnings`, and `migration_warning_count` so failed migrations can be shown without deleting or overwriting user files.

## 8. Local API

```http
POST /skill-quarantine
```

Request:

```json
{
  "action": "block|unblock|block-all|block-grace-all",
  "entity_key": "skill:unused-skill"
}
```

The endpoint is local-only and follows the existing OpenRelix workbench token boundary.
Responses include `migration_warning_count` and start a lightweight asset-layer refresh.

## 9. Privacy and Safety

- State stays local under runtime storage.
- The UI shows MCP server names, not command args or env.
- Saved JSON MCP payloads are local restore data only.
- JSON/TOML MCP config backups stay under the local runtime quarantine directory.
- Repository Skills are not moved.
- Restore never overwrites an existing path or MCP server entry.

## 10. Verification

- Unit test Skill move quarantine and restore.
- Unit test JSON MCP config isolation and restore.
- Unit test TOML MCP disable, backup, and restore.
- Unit test migration failure and restore conflict warnings.
- Unit test default view filtering for quarantined Skills/MCP servers.
- Unit test redaction placeholder restoration for local action attributes.
- Manual test `openrelix skill-quarantine list --json`.
- Manual test the workbench section and navigation.
- Performance test on a 100MB-class `overview-data.json`.
