# OpenRelix PostHog Analytics Worker

This is a small Cloudflare Worker template for the OpenRelix macOS panel
analytics path:

```text
OpenRelix macOS client -> this Worker -> PostHog /batch
```

It is intentionally not in the npm package allowlist yet. Treat it as an
operator/deployment template for the product analytics endpoint.

## Cost Fit

For the current expected scale of roughly 800 DAU, use PostHog Product
Analytics only. With about 40 events per daily app session, that is roughly:

```text
800 DAU * 40 events * 30 days = 960,000 events/month
```

That should stay near PostHog's monthly free Product Analytics tier. Do not
enable Session Replay for this first pass; module dwell time and control clicks
are enough for product direction without sending screen recordings.

Before production rollout, re-check the current official pricing pages:

- PostHog: <https://posthog.com/pricing>
- Cloudflare Workers: <https://developers.cloudflare.com/workers/platform/pricing/>

## Privacy Contract

The worker accepts only the fixed OpenRelix macOS client schema and drops
unknown events/properties. It forwards aggregate product analytics fields only:

- anonymous install ID and per-launch session ID
- app version and coarse macOS version
- fixed event names
- whitelisted `module_id` / `control_id`
- Chinese display labels `module_label_zh` / `control_label_zh` derived from
  the fixed module/control allowlists
- `dwell_ms`, `session_duration_ms`, `reason`, and `panel_language`

It must not receive or forward prompts, model responses, memory or review text,
window titles, project names, file paths, usernames, hostnames, cookies, tokens,
local reports, raw OpenRelix state, or generated panel content.

## AI Development Guardrails

When an AI or human developer adds a macOS panel module, button, filter, or
other product surface, treat analytics as part of the feature contract:

- Start with the product question the metric should answer.
- Reuse the existing event schema when possible.
- Add any new `module_id` / `control_id` to the Worker allowlist and add the
  matching Chinese display label.
- Update the analytics table and dashboard maintenance notes in
  `analytics-governance.zh-CN.md`.
- Keep Chinese product dashboards broken down by `module_label_zh` or
  `control_label_zh`; use raw IDs only for debug dashboards.
- Add or update focused Worker tests before shipping.

The Worker fails at module load time if an allowlisted module or control is
missing its Chinese label. This keeps AI-assisted feature work from silently
breaking the Chinese dashboard.

## Deploy

1. Create a PostHog project and copy the project token.
2. Copy the example Wrangler config:

```bash
cd analytics/posthog-worker
cp wrangler.example.toml wrangler.toml
```

3. Pick the PostHog host:

```toml
[vars]
POSTHOG_HOST = "https://us.i.posthog.com"
```

Use `https://eu.i.posthog.com` for EU Cloud, or your self-hosted PostHog
origin.

4. Set secrets:

```bash
wrangler secret put POSTHOG_API_KEY
wrangler secret put OPENRELIX_ANALYTICS_INGEST_TOKEN
```

`POSTHOG_API_KEY` is the PostHog project token. `OPENRELIX_ANALYTICS_INGEST_TOKEN`
is the token the macOS client sends to this Worker. Use a random value:

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
```

5. Deploy:

```bash
wrangler deploy
```

6. Build the OpenRelix macOS app with the Worker endpoint:

```bash
scripts/build_macos_client.sh \
  --analytics-endpoint "https://openrelix-posthog-analytics.<account>.workers.dev/events" \
  --analytics-token "$OPENRELIX_ANALYTICS_INGEST_TOKEN"
```

## PostHog Dashboard Recipe

Create one PostHog dashboard named `OpenRelix macOS Panel`.

Recommended insights:

- `DAU`: unique users for `openrelix_panel_ready`, grouped daily.
- `Panel load success`: `openrelix_panel_loaded / openrelix_app_launch`.
- `Session duration`: average and p50 of `session_duration_ms` on
  `openrelix_app_quit`.
- `Module dwell heatmap`: `sum(dwell_ms)` and `p50(dwell_ms)` from
  `openrelix_module_hidden`, broken down by `module_label_zh` for Chinese
  dashboards or `module_id` for raw debugging.
- `Core action clicks`: count and unique users for `openrelix_control_click`,
  broken down by `control_label_zh` for Chinese dashboards or `control_id` for
  raw debugging.
- `Memory value funnel`: `openrelix_panel_ready -> openrelix_module_visible`
  filtered to `module_id=personal_asset_memory -> openrelix_control_click`
  filtered to `control_id=memory_feedback`.
- `Summary value funnel`: `openrelix_panel_ready -> openrelix_module_visible`
  filtered to `module_id=nightly_summary -> openrelix_module_visible`
  filtered to `module_id=window_details`.
- `Retention`: start and return event both `openrelix_panel_ready`, grouped
  weekly.

The main product questions this should answer are:

- How many people use the macOS panel daily?
- Which modules earn real dwell time?
- Which modules get ignored?
- Which controls are actually used?
- Whether memory, summary, asset, and window modules lead to repeat usage.
