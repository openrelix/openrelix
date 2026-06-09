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
  "skill_quarantine_action",
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
  "skill_quarantine",
  "discovered_assets",
  "reviews",
  "usage_events",
  "window_context",
  "window_filters",
  "window_details",
]);

const MODULE_LABELS_ZH = new Map([
  ["overview", "总览"],
  ["nightly_summary", "整理摘要"],
  ["token_filters", "Token 筛选"],
  ["token_usage", "Token 使用明细"],
  ["token_overview", "Token 总览"],
  ["daily_token_usage", "每日 Token 使用"],
  ["today_token_usage", "今日 Token 使用"],
  ["pipeline_status", "处理状态"],
  ["personal_asset_memory", "个人资产记忆"],
  ["memory_compiler", "记忆编译"],
  ["curated_memory", "精选记忆"],
  ["codex_native_memory", "Codex 原生记忆"],
  ["claude_native_memory", "Claude 原生记忆"],
  ["asset_ledger", "资产台账"],
  ["asset_filters", "资产筛选"],
  ["asset_stats", "资产统计"],
  ["top_assets", "高频资产"],
  ["mcp_usage", "MCP 使用"],
  ["skill_quarantine", "Skill/MCP 小黑屋"],
  ["discovered_assets", "已发现资产"],
  ["reviews", "复盘"],
  ["usage_events", "使用事件"],
  ["window_context", "窗口上下文"],
  ["window_filters", "窗口筛选"],
  ["window_details", "窗口详情"],
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
  "skill_quarantine_open_folder",
  "skill_quarantine_block_all_suggested",
  "skill_quarantine_block_all_optional",
  "skill_quarantine_block_item",
  "skill_quarantine_unblock_item",
  "skill_quarantine_delete_open",
  "skill_quarantine_delete_confirm",
  "skill_quarantine_delete_cancel",
  "skill_quarantine_project_root_add",
  "skill_quarantine_project_root_remove",
]);

const CONTROL_LABELS_ZH = new Map([
  ["asset_refresh", "刷新资产"],
  ["update_primary", "主更新"],
  ["theme_switch", "切换主题"],
  ["language_switch", "切换语言"],
  ["token_provider_filter", "Token 提供方筛选"],
  ["token_group_filter", "Token 分组筛选"],
  ["token_range_filter", "Token 时间范围筛选"],
  ["window_range_filter", "窗口时间范围筛选"],
  ["window_search_range_filter", "窗口搜索时间范围筛选"],
  ["window_search_scope_filter", "窗口搜索范围筛选"],
  ["window_search_open", "打开窗口搜索"],
  ["window_search_submit", "提交窗口搜索"],
  ["window_search_reset", "重置窗口搜索"],
  ["window_search_close", "关闭窗口搜索"],
  ["window_detail_more", "展开窗口详情"],
  ["window_backfill_copy", "复制窗口回填"],
  ["memory_feedback", "记忆反馈"],
  ["context_days_filter", "上下文天数筛选"],
  ["expand_more", "展开更多"],
  ["collapse_more", "收起更多"],
  ["window_resume_codex", "用 Codex 续聊"],
  ["window_resume_claude", "用 Claude 续聊"],
  ["window_resume_copy", "复制续聊内容"],
  ["window_review_copy", "复制复盘内容"],
  ["skill_quarantine_open_folder", "打开小黑屋文件夹"],
  ["skill_quarantine_block_all_suggested", "一键隔离建议项"],
  ["skill_quarantine_block_all_optional", "一键隔离可选项"],
  ["skill_quarantine_block_item", "隔离单项"],
  ["skill_quarantine_unblock_item", "放行单项"],
  ["skill_quarantine_delete_open", "打开删除确认"],
  ["skill_quarantine_delete_confirm", "确认删除"],
  ["skill_quarantine_delete_cancel", "取消删除"],
  ["skill_quarantine_project_root_add", "添加项目 skill 路径"],
  ["skill_quarantine_project_root_remove", "移除项目 skill 路径"],
]);

const ALLOWED_SKILL_QUARANTINE_ACTIONS = new Set([
  "block",
  "unblock",
  "delete",
  "block_all",
  "block_grace_all",
  "add_project_skill_root",
  "remove_project_skill_root",
]);

const SKILL_QUARANTINE_ACTION_LABELS_ZH = new Map([
  ["block", "隔离单项"],
  ["unblock", "放行单项"],
  ["delete", "删除小黑屋记录"],
  ["block_all", "一键隔离建议项"],
  ["block_grace_all", "一键隔离可选项"],
  ["add_project_skill_root", "添加项目 skill 路径"],
  ["remove_project_skill_root", "移除项目 skill 路径"],
]);

const ALLOWED_SKILL_QUARANTINE_RESULTS = new Set([
  "accepted",
  "warning",
  "partial",
  "stale",
  "failed",
]);

const SKILL_QUARANTINE_RESULT_LABELS_ZH = new Map([
  ["accepted", "已受理"],
  ["warning", "已受理但有提示"],
  ["partial", "部分成功"],
  ["stale", "面板待刷新"],
  ["failed", "失败"],
]);

const ALLOWED_SKILL_QUARANTINE_BUCKETS = new Set([
  "suggested",
  "optional",
  "item",
  "quarantined",
  "project_roots",
]);

const ALLOWED_REASONS = new Set([
  "intersection",
  "page_hidden",
  "page_unload",
  "missing_panel",
  "navigation_error",
  "provisional_navigation_error",
]);

function assertLabelCoverage(ids, labels, labelName) {
  for (const id of ids) {
    if (!labels.has(id)) {
      throw new Error(`${labelName}_missing_${id}`);
    }
  }
}

assertLabelCoverage(ALLOWED_MODULES, MODULE_LABELS_ZH, "module_label_zh");
assertLabelCoverage(ALLOWED_CONTROLS, CONTROL_LABELS_ZH, "control_label_zh");
assertLabelCoverage(
  ALLOWED_SKILL_QUARANTINE_ACTIONS,
  SKILL_QUARANTINE_ACTION_LABELS_ZH,
  "skill_quarantine_action_label_zh",
);
assertLabelCoverage(
  ALLOWED_SKILL_QUARANTINE_RESULTS,
  SKILL_QUARANTINE_RESULT_LABELS_ZH,
  "skill_quarantine_result_label_zh",
);

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
    clean.module_label_zh = MODULE_LABELS_ZH.get(moduleID) || moduleID;
  }

  const controlID = cleanString(source.control_id, 80);
  if (ALLOWED_CONTROLS.has(controlID)) {
    clean.control_id = controlID;
    clean.control_label_zh = CONTROL_LABELS_ZH.get(controlID) || controlID;
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

  const skillQuarantineAction = cleanString(source.skill_quarantine_action, 80);
  if (ALLOWED_SKILL_QUARANTINE_ACTIONS.has(skillQuarantineAction)) {
    clean.skill_quarantine_action = skillQuarantineAction;
    clean.skill_quarantine_action_label_zh = SKILL_QUARANTINE_ACTION_LABELS_ZH.get(skillQuarantineAction) || skillQuarantineAction;
  }

  const skillQuarantineResult = cleanString(source.skill_quarantine_result, 80);
  if (ALLOWED_SKILL_QUARANTINE_RESULTS.has(skillQuarantineResult)) {
    clean.skill_quarantine_result = skillQuarantineResult;
    clean.skill_quarantine_result_label_zh = SKILL_QUARANTINE_RESULT_LABELS_ZH.get(skillQuarantineResult) || skillQuarantineResult;
  }

  const skillQuarantineBucket = cleanString(source.skill_quarantine_bucket, 80);
  if (ALLOWED_SKILL_QUARANTINE_BUCKETS.has(skillQuarantineBucket)) {
    clean.skill_quarantine_bucket = skillQuarantineBucket;
  }

  const skillQuarantineCount = boundedInteger(source.skill_quarantine_count, 500);
  if (skillQuarantineCount !== null) {
    clean.skill_quarantine_count = skillQuarantineCount;
  }

  const skillQuarantineFailedCount = boundedInteger(source.skill_quarantine_failed_count, 500);
  if (skillQuarantineFailedCount !== null) {
    clean.skill_quarantine_failed_count = skillQuarantineFailedCount;
  }

  const skillQuarantineWarningCount = boundedInteger(source.skill_quarantine_warning_count, 500);
  if (skillQuarantineWarningCount !== null) {
    clean.skill_quarantine_warning_count = skillQuarantineWarningCount;
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
