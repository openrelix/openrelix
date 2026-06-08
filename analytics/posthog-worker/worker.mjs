const DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com";
const MAX_BODY_BYTES = 64 * 1024;
const MAX_BATCH_EVENTS = 50;
const DEFAULT_RATE_LIMIT_PER_MINUTE = 180;
const RATE_BUCKETS = new Map();

const ALLOWED_EVENTS = new Set([
  "app_launch",
  "app_quit",
  "panel_loaded",
  "panel_load_failed",
  "panel_ready",
  "module_visible",
  "module_hidden",
  "control_click",
]);

const ALLOWED_MODULES = new Set([
  "overview",
  "nightly_summary",
  "token_filters",
  "token_usage",
  "token_overview",
  "daily_token_usage",
  "today_token_usage",
  "pipeline_status",
  "personal_asset_memory",
  "memory_compiler",
  "curated_memory",
  "codex_native_memory",
  "claude_native_memory",
  "asset_ledger",
  "asset_filters",
  "asset_stats",
  "top_assets",
  "mcp_usage",
  "discovered_assets",
  "reviews",
  "usage_events",
  "window_context",
  "window_filters",
  "window_details",
]);

const ALLOWED_CONTROLS = new Set([
  "asset_refresh",
  "update_primary",
  "theme_switch",
  "language_switch",
  "token_provider_filter",
  "token_group_filter",
  "token_range_filter",
  "window_range_filter",
  "window_search_range_filter",
  "window_search_scope_filter",
  "window_search_open",
  "window_search_submit",
  "window_search_reset",
  "window_search_close",
  "window_detail_more",
  "window_backfill_copy",
  "memory_feedback",
  "context_days_filter",
  "expand_more",
  "collapse_more",
  "window_resume_codex",
  "window_resume_claude",
  "window_resume_copy",
  "window_review_copy",
]);

const ALLOWED_REASONS = new Set([
  "intersection",
  "page_hidden",
  "page_unload",
  "missing_panel",
  "navigation_error",
  "provisional_navigation_error",
]);

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function normalizeHost(value) {
  const host = String(value || DEFAULT_POSTHOG_HOST).trim().replace(/\/+$/, "");
  if (!/^https:\/\/[a-z0-9.-]+(?::\d+)?$/i.test(host)) {
    return DEFAULT_POSTHOG_HOST;
  }
  return host;
}

function bearerToken(request) {
  const raw = request.headers.get("authorization") || "";
  const match = raw.match(/^Bearer\s+(.+)$/i);
  return match ? match[1].trim() : "";
}

function timingSafeEqual(left, right) {
  const a = new TextEncoder().encode(String(left || ""));
  const b = new TextEncoder().encode(String(right || ""));
  if (a.length !== b.length) {
    return false;
  }
  let diff = 0;
  for (let index = 0; index < a.length; index += 1) {
    diff |= a[index] ^ b[index];
  }
  return diff === 0;
}

function contentLengthIsTooLarge(request) {
  const raw = request.headers.get("content-length");
  if (!raw) {
    return false;
  }
  const size = Number(raw);
  return Number.isFinite(size) && size > MAX_BODY_BYTES;
}

async function readJsonBody(request) {
  if (contentLengthIsTooLarge(request)) {
    throw new Error("body_too_large");
  }
  const text = await request.text();
  if (text.length > MAX_BODY_BYTES) {
    throw new Error("body_too_large");
  }
  return JSON.parse(text);
}

function eventList(payload) {
  if (Array.isArray(payload)) {
    return payload;
  }
  if (payload && Array.isArray(payload.events)) {
    return payload.events;
  }
  return null;
}

function cleanString(value, maxLength = 120) {
  if (typeof value !== "string") {
    return "";
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > maxLength) {
    return "";
  }
  return trimmed;
}

function cleanID(value) {
  const text = cleanString(value, 80).toLowerCase();
  return /^[a-z0-9-]{8,80}$/.test(text) ? text : "";
}

function cleanVersion(value) {
  const text = cleanString(value, 40);
  return /^[0-9a-zA-Z._+-]{1,40}$/.test(text) ? text : "";
}

function cleanOSVersion(value) {
  const text = cleanString(value, 40);
  return /^[0-9a-zA-Z._+-]{1,40}$/.test(text) ? text : "";
}

function cleanTimestamp(value) {
  const text = cleanString(value, 64);
  const parsed = Date.parse(text);
  if (!Number.isFinite(parsed)) {
    return new Date().toISOString();
  }
  return new Date(parsed).toISOString();
}

function boundedInteger(value, maxValue) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return null;
  }
  return Math.min(Math.max(Math.round(number), 0), maxValue);
}

function sanitizeProperties(properties) {
  const clean = {};
  const source = properties && typeof properties === "object" ? properties : {};

  const moduleID = cleanString(source.module_id, 80);
  if (ALLOWED_MODULES.has(moduleID)) {
    clean.module_id = moduleID;
  }

  const controlID = cleanString(source.control_id, 80);
  if (ALLOWED_CONTROLS.has(controlID)) {
    clean.control_id = controlID;
  }

  const reason = cleanString(source.reason, 80);
  if (ALLOWED_REASONS.has(reason)) {
    clean.reason = reason;
  }

  const panelLanguage = cleanString(source.panel_language, 8);
  if (panelLanguage === "zh" || panelLanguage === "en") {
    clean.panel_language = panelLanguage;
  }

  const dwellMs = boundedInteger(source.dwell_ms, 24 * 60 * 60 * 1000);
  if (dwellMs !== null) {
    clean.dwell_ms = dwellMs;
  }

  const sessionDurationMs = boundedInteger(source.session_duration_ms, 24 * 60 * 60 * 1000);
  if (sessionDurationMs !== null) {
    clean.session_duration_ms = sessionDurationMs;
  }

  return clean;
}

function toPostHogEvent(input) {
  if (!input || typeof input !== "object") {
    return null;
  }
  const app = cleanString(input.app, 40);
  const event = cleanString(input.event, 80);
  const installID = cleanID(input.install_id);
  const sessionID = cleanID(input.session_id);
  if (app !== "openrelix_macos" || !ALLOWED_EVENTS.has(event) || !installID || !sessionID) {
    return null;
  }

  const properties = sanitizeProperties(input.properties);
  properties.distinct_id = installID;
  properties.$process_person_profile = false;
  properties.$lib = "openrelix-posthog-worker";
  properties.openrelix_schema_version = Number(input.schema_version) === 1 ? 1 : 0;
  properties.openrelix_app = app;
  properties.openrelix_session_id = sessionID;
  properties.openrelix_app_version = cleanVersion(input.app_version);
  properties.openrelix_os = cleanString(input.os, 20) === "macOS" ? "macOS" : "";
  properties.openrelix_os_version = cleanOSVersion(input.os_version);

  return {
    event: `openrelix_${event}`,
    properties,
    timestamp: cleanTimestamp(input.ts),
  };
}

function rateLimitKey(request, events) {
  const firstInstallID = events.find((event) => event && event.install_id)?.install_id;
  const cleanInstallID = cleanID(firstInstallID);
  if (cleanInstallID) {
    return `install:${cleanInstallID}`;
  }
  return `ip:${request.headers.get("cf-connecting-ip") || "unknown"}`;
}

function isRateLimited(key, limit) {
  const now = Date.now();
  const bucket = Math.floor(now / 60000);
  const state = RATE_BUCKETS.get(key);
  if (!state || state.bucket !== bucket) {
    RATE_BUCKETS.set(key, { bucket, count: 1 });
    return false;
  }
  state.count += 1;
  return state.count > limit;
}

async function handleEvents(request, env) {
  const ingestToken = String(env.OPENRELIX_ANALYTICS_INGEST_TOKEN || "").trim();
  const posthogAPIKey = String(env.POSTHOG_API_KEY || "").trim();
  if (!ingestToken || !posthogAPIKey) {
    return jsonResponse({ ok: false, error: "collector_not_configured" }, 503);
  }
  if (!timingSafeEqual(bearerToken(request), ingestToken)) {
    return jsonResponse({ ok: false, error: "unauthorized" }, 401);
  }

  let payload;
  try {
    payload = await readJsonBody(request);
  } catch (error) {
    const message = error && error.message === "body_too_large" ? "body_too_large" : "invalid_json";
    return jsonResponse({ ok: false, error: message }, message === "body_too_large" ? 413 : 400);
  }

  const events = eventList(payload);
  if (!events) {
    return jsonResponse({ ok: false, error: "events_array_required" }, 400);
  }
  if (events.length > MAX_BATCH_EVENTS) {
    return jsonResponse({ ok: false, error: "too_many_events" }, 413);
  }

  const rateLimit = Number(env.OPENRELIX_ANALYTICS_RATE_LIMIT_PER_MINUTE || DEFAULT_RATE_LIMIT_PER_MINUTE);
  if (isRateLimited(rateLimitKey(request, events), Number.isFinite(rateLimit) ? rateLimit : DEFAULT_RATE_LIMIT_PER_MINUTE)) {
    return jsonResponse({ ok: false, error: "rate_limited" }, 429);
  }

  const batch = [];
  let rejected = 0;
  for (const event of events) {
    const posthogEvent = toPostHogEvent(event);
    if (posthogEvent) {
      batch.push(posthogEvent);
    } else {
      rejected += 1;
    }
  }
  if (!batch.length) {
    return jsonResponse({ ok: false, accepted: 0, rejected, error: "no_valid_events" }, 422);
  }

  const posthogHost = normalizeHost(env.POSTHOG_HOST);
  const response = await fetch(`${posthogHost}/batch/`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      api_key: posthogAPIKey,
      historical_migration: false,
      batch,
    }),
  });

  if (!response.ok) {
    return jsonResponse({
      ok: false,
      accepted: 0,
      rejected: events.length,
      error: "posthog_forward_failed",
      status: response.status,
    }, 502);
  }

  return jsonResponse({
    ok: true,
    accepted: batch.length,
    rejected,
  });
}

export default {
  async fetch(request, env = {}) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/healthz") {
      return jsonResponse({
        ok: Boolean(env.OPENRELIX_ANALYTICS_INGEST_TOKEN && env.POSTHOG_API_KEY),
        service: "openrelix-posthog-worker",
      });
    }
    if (request.method === "POST" && url.pathname === "/events") {
      return handleEvents(request, env);
    }
    return jsonResponse({ ok: false, error: "not_found" }, 404);
  },
};
