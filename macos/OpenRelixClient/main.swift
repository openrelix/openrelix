import AppKit
import Darwin
import Foundation
import WebKit

private let stateRootResourceName = "OpenRelixStateRoot"
private let tokenLiveLabel = "io.github.openrelix.token-live"
private let tokenLivePlistName = "\(tokenLiveLabel).plist"
private let analyticsDisabledDefaultsKey = "openrelix.analytics.disabled"
private let analyticsInstallIDDefaultsKey = "openrelix.analytics.install_id"

private func trimmed(_ value: String) -> String {
    value.trimmingCharacters(in: .whitespacesAndNewlines)
}

private func expandedPath(_ value: String) -> String {
    (value as NSString).expandingTildeInPath
}

private func panelURL(for stateRoot: URL) -> URL {
    stateRoot
        .appendingPathComponent("reports", isDirectory: true)
        .appendingPathComponent("panel.html", isDirectory: false)
}

private func bundledStateRootPath() -> String? {
    guard let value = bundledConfigValue(resourceName: stateRootResourceName) else {
        return nil
    }
    return expandedPath(value)
}

private func bundledConfigValue(resourceName: String) -> String? {
    guard
        let url = Bundle.main.url(forResource: resourceName, withExtension: "txt"),
        let text = try? String(contentsOf: url, encoding: .utf8)
    else {
        return nil
    }

    for line in text.components(separatedBy: .newlines) {
        let value = trimmed(line)
        if !value.isEmpty && !value.hasPrefix("#") {
            return value
        }
    }
    return nil
}

private func configuredValue(environmentKey: String, resourceName: String) -> String {
    let environmentValue = trimmed(ProcessInfo.processInfo.environment[environmentKey] ?? "")
    if !environmentValue.isEmpty {
        return environmentValue
    }
    return trimmed(bundledConfigValue(resourceName: resourceName) ?? "")
}

private func defaultApplicationSupportStateRoot() -> URL {
    let fallback = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
        .appendingPathComponent("Library", isDirectory: true)
        .appendingPathComponent("Application Support", isDirectory: true)
    let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first ?? fallback
    return appSupport.appendingPathComponent("openrelix", isDirectory: true)
}

private func candidateStateRoots() -> [URL] {
    var candidates: [URL] = []
    let environment = ProcessInfo.processInfo.environment

    if let explicit = environment["AI_ASSET_STATE_DIR"], !trimmed(explicit).isEmpty {
        candidates.append(URL(fileURLWithPath: expandedPath(explicit), isDirectory: true))
    }

    if let bundledPath = bundledStateRootPath() {
        candidates.append(URL(fileURLWithPath: bundledPath, isDirectory: true))
    }

    candidates.append(defaultApplicationSupportStateRoot())

    var seen = Set<String>()
    return candidates.filter { url in
        let key = url.standardizedFileURL.path
        if seen.contains(key) {
            return false
        }
        seen.insert(key)
        return true
    }
}

private func preferredStateRoot() -> URL {
    let candidates = candidateStateRoots()
    for candidate in candidates where FileManager.default.fileExists(atPath: panelURL(for: candidate).path) {
        return candidate
    }
    return candidates.first ?? defaultApplicationSupportStateRoot()
}

private func htmlEscaped(_ value: String) -> String {
    value
        .replacingOccurrences(of: "&", with: "&amp;")
        .replacingOccurrences(of: "<", with: "&lt;")
        .replacingOccurrences(of: ">", with: "&gt;")
        .replacingOccurrences(of: "\"", with: "&quot;")
}

private func isFalseyEnvironmentValue(_ value: String?) -> Bool {
    let normalized = trimmed(value ?? "").lowercased()
    return ["0", "false", "no", "off", "disabled"].contains(normalized)
}

private func isTruthyEnvironmentValue(_ value: String?) -> Bool {
    let normalized = trimmed(value ?? "").lowercased()
    return ["1", "true", "yes", "on", "enabled"].contains(normalized)
}

private func coarseOSVersion() -> String {
    let version = ProcessInfo.processInfo.operatingSystemVersion
    return "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)"
}

private func missingPanelHTML(panelPath: String, stateRootPath: String) -> String {
    let panel = htmlEscaped(panelPath)
    let stateRoot = htmlEscaped(stateRootPath)
    return """
    <!doctype html>
    <html lang="zh-CN">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>OpenRelix</title>
      <style>
        :root {
          color-scheme: light dark;
          --bg: #f5f5f7;
          --ink: #1d1d1f;
          --muted: rgba(29, 29, 31, .68);
          --card-bg: rgba(255, 255, 255, .82);
          --card-border: rgba(0, 0, 0, .1);
          --code-bg: rgba(0, 0, 0, .06);
          --code-ink: #174ea6;
          --shadow: 0 24px 80px rgba(0, 0, 0, .12);
        }
        @media (prefers-color-scheme: dark) {
          :root {
            color-scheme: dark;
            --bg: #171a21;
            --ink: #f4f5f7;
            --muted: rgba(244, 245, 247, .74);
            --card-bg: rgba(255, 255, 255, .06);
            --card-border: rgba(255, 255, 255, .13);
            --code-bg: rgba(0, 0, 0, .28);
            --code-ink: #d8e7ff;
            --shadow: 0 24px 80px rgba(0, 0, 0, .42);
          }
        }
        * { box-sizing: border-box; }
        html, body { margin: 0; min-height: 100%; }
        body {
          display: grid;
          min-height: 100vh;
          place-items: center;
          background: var(--bg);
          color: var(--ink);
          font: 15px/1.55 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif;
        }
        main {
          width: min(680px, calc(100vw - 48px));
          border: 1px solid var(--card-border);
          border-radius: 18px;
          background: var(--card-bg);
          padding: 32px;
          box-shadow: var(--shadow);
        }
        h1 { margin: 0 0 10px; font-size: 26px; letter-spacing: 0; }
        p { margin: 10px 0 0; color: var(--muted); }
        code {
          display: block;
          margin-top: 14px;
          padding: 12px 14px;
          overflow-wrap: anywhere;
          border-radius: 10px;
          background: var(--code-bg);
          color: var(--code-ink);
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: 13px;
        }
      </style>
    </head>
    <body>
      <main>
        <h1>OpenRelix</h1>
        <p>没有找到本地可视化面板。请先运行一次安装或刷新流程，然后重新加载客户端。</p>
        <code>\(panel)</code>
        <p>当前 state root: \(stateRoot)</p>
      </main>
    </body>
    </html>
    """
}

private let panelThemeBridgeScript = """
(function() {
  if (!window.webkit || !window.webkit.messageHandlers || !window.webkit.messageHandlers.openrelixTheme) {
    return;
  }

  var lastTheme = "";
  var themeStorageKey = "openrelix-panel-theme";
  var supportedThemes = ["system", "light", "dark"];
  var systemDarkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function resolveTheme() {
    var rootTheme = document.documentElement.getAttribute("data-theme");
    var bodyTheme = document.body ? document.body.getAttribute("data-theme") : "";
    var theme = rootTheme || bodyTheme;
    if (theme === "dark" || theme === "light") {
      return theme;
    }
    try {
      var storedTheme = window.localStorage ? window.localStorage.getItem(themeStorageKey) : "";
      if (supportedThemes.indexOf(storedTheme) !== -1 && storedTheme !== "system") {
        return storedTheme;
      }
    } catch (error) {
    }
    return systemDarkQuery && systemDarkQuery.matches ? "dark" : "light";
  }

  function postTheme() {
    var theme = resolveTheme();
    if (theme === lastTheme) {
      return;
    }
    lastTheme = theme;
    window.webkit.messageHandlers.openrelixTheme.postMessage(theme);
  }

  postTheme();

  var observer = new MutationObserver(postTheme);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  function observeBody() {
    if (document.body) {
      observer.observe(document.body, { attributes: true, attributeFilter: ["data-theme"] });
      postTheme();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", observeBody, { once: true });
  } else {
    observeBody();
  }

  if (systemDarkQuery) {
    if (typeof systemDarkQuery.addEventListener === "function") {
      systemDarkQuery.addEventListener("change", postTheme);
    } else if (typeof systemDarkQuery.addListener === "function") {
      systemDarkQuery.addListener(postTheme);
    }
  }
})();
"""

private let panelUsageAnalyticsScript = """
(function() {
  var handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.openrelixAnalytics;
  if (!handler) return;

  var sectionToModule = {
    "overview-top": "overview",
    "nightly-summary": "nightly_summary",
    "token-filter-panel": "token_filters",
    "token-section": "token_usage",
    "token-overview-panel": "token_overview",
    "daily-token-panel": "daily_token_usage",
    "today-token-panel": "today_token_usage",
    "pipeline-section": "pipeline_status",
    "memory-section": "personal_asset_memory",
    "personal-memory-compiler-section": "memory_compiler",
    "personal-memory-curated-section": "curated_memory",
    "codex-native-section": "codex_native_memory",
    "claude-native-section": "claude_native_memory",
    "asset-overview-section": "asset_ledger",
    "asset-filter-panel": "asset_filters",
    "asset-stats-snapshot-section": "asset_stats",
    "top-assets-section": "top_assets",
    "mcp-usage-section": "mcp_usage",
    "skill-quarantine-section": "skill_quarantine",
    "discovered-assets-section": "discovered_assets",
    "reviews-section": "reviews",
    "usage-events-section": "usage_events",
    "project-context-section": "window_context",
    "window-filter-panel": "window_filters",
    "window-overview-section": "window_details"
  };

  var controlSelectors = [
    ["#asset-layer-refresh-button", "asset_refresh"],
    ["[data-update-primary]", "update_primary"],
    ["[data-theme-option]", "theme_switch"],
    ["[data-language-option]", "language_switch"],
    ["[data-token-provider]", "token_provider_filter"],
    ["[data-token-group]", "token_group_filter"],
    ["[data-token-range-days]", "token_range_filter"],
    ["[data-window-range-days]", "window_range_filter"],
    ["[data-window-search-range-days]", "window_search_range_filter"],
    ["[data-window-search-scope]", "window_search_scope_filter"],
    ["#window-search-trigger", "window_search_open"],
    ["#window-search-submit", "window_search_submit"],
    ["#window-search-reset", "window_search_reset"],
    ["#window-search-close", "window_search_close"],
    ["#window-detail-more-button", "window_detail_more"],
    ["[data-window-backfill-copy]", "window_backfill_copy"],
    ["[data-memory-feedback]", "memory_feedback"],
    ["[data-context-days]", "context_days_filter"],
    ["[data-expand-group]", "expand_more"],
    ["[data-collapse-details]", "collapse_more"],
    ["[data-window-resume-open]", "window_resume_codex"],
    ["[data-window-resume-claude-desktop]", "window_resume_claude"],
    ["[data-window-resume-copy]", "window_resume_copy"],
    ["[data-window-review-copy]", "window_review_copy"],
    [".skill-quarantine-open-folder", "skill_quarantine_open_folder"],
    ['[data-skill-quarantine-confirm-submit="true"]', "skill_quarantine_delete_confirm"],
    ['[data-skill-quarantine-action="cancel-delete"]', "skill_quarantine_delete_cancel"],
    ['[data-skill-quarantine-action="block-all"]', "skill_quarantine_block_all_suggested"],
    ['[data-skill-quarantine-action="block-grace-all"]', "skill_quarantine_block_all_optional"],
    ['[data-skill-quarantine-action="block"]', "skill_quarantine_block_item"],
    ['[data-skill-quarantine-action="unblock"]', "skill_quarantine_unblock_item"],
    ['[data-skill-quarantine-action="delete"]', "skill_quarantine_delete_open"],
    ['[data-skill-quarantine-project-choose]', "skill_quarantine_project_root_choose"],
    ['[data-skill-quarantine-action="add-project-skill-root"]', "skill_quarantine_project_root_add"],
    ['[data-skill-quarantine-action="remove-project-skill-root"]', "skill_quarantine_project_root_remove"]
  ];

  function currentPanelLanguage() {
    var lang = (document.body && document.body.getAttribute("data-language")) ||
      document.documentElement.getAttribute("lang") || "";
    lang = String(lang || "").toLowerCase();
    return lang.indexOf("zh") === 0 ? "zh" : "en";
  }

  function elementFromTarget(target) {
    if (!target) return null;
    if (target.nodeType === 1) return target;
    return target.parentElement || null;
  }

  function moduleForElement(element) {
    var node = elementFromTarget(element);
    while (node && node !== document.documentElement) {
      if (node.id && sectionToModule[node.id]) {
        return sectionToModule[node.id];
      }
      node = node.parentElement;
    }
    return "";
  }

  function post(eventName, properties) {
    try {
      handler.postMessage({
        event: eventName,
        properties: Object.assign({ panel_language: currentPanelLanguage() }, properties || {})
      });
    } catch (error) {
    }
  }

  function controlForClick(target) {
    target = elementFromTarget(target);
    if (!target || !target.closest) return "";
    for (var i = 0; i < controlSelectors.length; i += 1) {
      if (target.closest(controlSelectors[i][0])) {
        return controlSelectors[i][1];
      }
    }
    return "";
  }

  document.addEventListener("click", function(event) {
    var controlId = controlForClick(event.target);
    if (!controlId) return;
    post("control_click", {
      control_id: controlId,
      module_id: moduleForElement(event.target)
    });
  }, true);

  var visibilityState = {};
  var visibleThreshold = 0.55;
  var hiddenThreshold = 0.15;

  function markVisible(moduleId) {
    if (!moduleId) return;
    var state = visibilityState[moduleId] || {};
    if (state.visible) return;
    visibilityState[moduleId] = { visible: true, startedAt: Date.now() };
    post("module_visible", { module_id: moduleId });
  }

  function markHidden(moduleId, reason) {
    if (!moduleId) return;
    var state = visibilityState[moduleId];
    if (!state || !state.visible) return;
    var dwellMs = Math.max(0, Date.now() - state.startedAt);
    visibilityState[moduleId] = { visible: false, startedAt: 0 };
    if (dwellMs >= 250) {
      post("module_hidden", {
        module_id: moduleId,
        dwell_ms: dwellMs,
        reason: reason || "intersection"
      });
    }
  }

  function markAllHidden(reason) {
    Object.keys(visibilityState).forEach(function(moduleId) {
      markHidden(moduleId, reason);
    });
  }

  function setupVisibilityObserver() {
    var targets = Object.keys(sectionToModule).map(function(id) {
      return document.getElementById(id);
    }).filter(Boolean);
    if (!targets.length) return;
    if (!window.IntersectionObserver) {
      targets.forEach(function(target) { markVisible(sectionToModule[target.id]); });
      return;
    }
    var observer = new IntersectionObserver(function(entries) {
      entries.forEach(function(entry) {
        var moduleId = sectionToModule[entry.target.id] || "";
        if (!moduleId) return;
        if (entry.isIntersecting && entry.intersectionRatio >= visibleThreshold) {
          markVisible(moduleId);
        } else if (!entry.isIntersecting || entry.intersectionRatio <= hiddenThreshold) {
          markHidden(moduleId, "intersection");
        }
      });
    }, { threshold: [0, hiddenThreshold, visibleThreshold, 1] });
    targets.forEach(function(target) { observer.observe(target); });
  }

  document.addEventListener("visibilitychange", function() {
    if (document.visibilityState === "hidden") {
      markAllHidden("page_hidden");
    }
  });
  window.addEventListener("pagehide", function() { markAllHidden("page_unload"); });
  window.addEventListener("beforeunload", function() { markAllHidden("page_unload"); });

  function start() {
    setupVisibilityObserver();
    post("panel_ready", {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
"""

private final class PanelAnalytics {
    private let endpoint: URL?
    private let token: String
    private let environmentDisabled: Bool
    private let sessionID = UUID().uuidString.lowercased()
    private let sessionStartedAt = Date()
    private let defaults = UserDefaults.standard
    private let appVersion: String
    private let queue = DispatchQueue(label: "openrelix.analytics.queue")
    private var pendingEvents: [[String: Any]] = []
    private var flushWorkItem: DispatchWorkItem?

    private let allowedEvents: Set<String> = [
        "app_launch",
        "app_quit",
        "panel_loaded",
        "panel_load_failed",
        "panel_ready",
        "module_visible",
        "module_hidden",
        "control_click",
        "skill_quarantine_action",
    ]
    private let allowedModules: Set<String> = [
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
    ]
    private let allowedControls: Set<String> = [
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
        "skill_quarantine_project_root_choose",
        "skill_quarantine_project_root_add",
        "skill_quarantine_project_root_remove",
    ]
    private let allowedSkillQuarantineActions: Set<String> = [
        "block",
        "unblock",
        "delete",
        "block_all",
        "block_grace_all",
        "add_project_skill_root",
        "remove_project_skill_root",
    ]
    private let allowedSkillQuarantineResults: Set<String> = [
        "accepted",
        "warning",
        "partial",
        "stale",
        "failed",
    ]
    private let allowedSkillQuarantineBuckets: Set<String> = [
        "suggested",
        "optional",
        "item",
        "quarantined",
        "project_roots",
    ]
    private let allowedReasons: Set<String> = [
        "intersection",
        "page_hidden",
        "page_unload",
        "missing_panel",
        "navigation_error",
        "provisional_navigation_error",
    ]

    init() {
        let environment = ProcessInfo.processInfo.environment
        let endpointValue = configuredValue(
            environmentKey: "OPENRELIX_ANALYTICS_ENDPOINT",
            resourceName: "OpenRelixAnalyticsEndpoint"
        )
        if let url = URL(string: endpointValue),
           let scheme = url.scheme?.lowercased(),
           scheme == "https" || scheme == "http",
           scheme == "https" || url.host == "127.0.0.1" || url.host == "localhost" {
            endpoint = url
        } else {
            endpoint = nil
        }
        token = configuredValue(
            environmentKey: "OPENRELIX_ANALYTICS_TOKEN",
            resourceName: "OpenRelixAnalyticsToken"
        )
        environmentDisabled = isFalseyEnvironmentValue(environment["OPENRELIX_ANALYTICS_ENABLED"])
            || isTruthyEnvironmentValue(environment["OPENRELIX_ANALYTICS_DISABLED"])
        appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0"
    }

    var isEndpointConfigured: Bool {
        endpoint != nil && !environmentDisabled
    }

    var isUserDisabled: Bool {
        defaults.bool(forKey: analyticsDisabledDefaultsKey)
    }

    var isEnabled: Bool {
        isEndpointConfigured && !isUserDisabled
    }

    func setUserDisabled(_ disabled: Bool) {
        defaults.set(disabled, forKey: analyticsDisabledDefaultsKey)
        if disabled {
            queue.async {
                self.flushWorkItem?.cancel()
                self.flushWorkItem = nil
                self.pendingEvents.removeAll()
            }
        }
    }

    func track(_ eventName: String, properties: [String: Any] = [:]) {
        guard isEnabled, allowedEvents.contains(eventName) else {
            return
        }
        let payload: [String: Any] = [
            "schema_version": 1,
            "app": "openrelix_macos",
            "event": eventName,
            "ts": ISO8601DateFormatter().string(from: Date()),
            "app_version": appVersion,
            "session_id": sessionID,
            "install_id": anonymousInstallID(),
            "os": "macOS",
            "os_version": coarseOSVersion(),
            "properties": sanitizedProperties(properties),
        ]
        queue.async {
            self.pendingEvents.append(payload)
            if self.pendingEvents.count >= 8 {
                self.flushOnQueue()
            } else {
                self.scheduleFlushOnQueue()
            }
        }
    }

    func trackPanelMessage(_ body: Any) {
        guard let message = body as? [String: Any],
              let eventName = message["event"] as? String
        else {
            return
        }
        let properties = message["properties"] as? [String: Any] ?? [:]
        track(eventName, properties: properties)
    }

    func trackAppQuit() {
        let durationMs = max(0, Int(Date().timeIntervalSince(sessionStartedAt) * 1000))
        track("app_quit", properties: ["session_duration_ms": durationMs])
        flush(waitForCompletion: true)
    }

    func flush(waitForCompletion: Bool = false) {
        let semaphore = waitForCompletion ? DispatchSemaphore(value: 0) : nil
        queue.async {
            self.flushOnQueue(completion: {
                semaphore?.signal()
            })
        }
        if waitForCompletion {
            _ = semaphore?.wait(timeout: .now() + 2.0)
        }
    }

    private func anonymousInstallID() -> String {
        if let existing = defaults.string(forKey: analyticsInstallIDDefaultsKey),
           !trimmed(existing).isEmpty {
            return existing
        }
        let value = UUID().uuidString.lowercased()
        defaults.set(value, forKey: analyticsInstallIDDefaultsKey)
        return value
    }

    private func sanitizedProperties(_ properties: [String: Any]) -> [String: Any] {
        var clean: [String: Any] = [:]
        if let moduleID = properties["module_id"] as? String, allowedModules.contains(moduleID) {
            clean["module_id"] = moduleID
        }
        if let controlID = properties["control_id"] as? String, allowedControls.contains(controlID) {
            clean["control_id"] = controlID
        }
        if let language = properties["panel_language"] as? String, ["zh", "en"].contains(language) {
            clean["panel_language"] = language
        }
        if let reason = properties["reason"] as? String, allowedReasons.contains(reason) {
            clean["reason"] = reason
        }
        if let dwellMs = integerProperty(properties["dwell_ms"]) {
            clean["dwell_ms"] = min(max(dwellMs, 0), 24 * 60 * 60 * 1000)
        }
        if let sessionDurationMs = integerProperty(properties["session_duration_ms"]) {
            clean["session_duration_ms"] = min(max(sessionDurationMs, 0), 24 * 60 * 60 * 1000)
        }
        if let action = properties["skill_quarantine_action"] as? String,
           allowedSkillQuarantineActions.contains(action) {
            clean["skill_quarantine_action"] = action
        }
        if let result = properties["skill_quarantine_result"] as? String,
           allowedSkillQuarantineResults.contains(result) {
            clean["skill_quarantine_result"] = result
        }
        if let bucket = properties["skill_quarantine_bucket"] as? String,
           allowedSkillQuarantineBuckets.contains(bucket) {
            clean["skill_quarantine_bucket"] = bucket
        }
        if let count = integerProperty(properties["skill_quarantine_count"]) {
            clean["skill_quarantine_count"] = min(max(count, 0), 500)
        }
        if let failedCount = integerProperty(properties["skill_quarantine_failed_count"]) {
            clean["skill_quarantine_failed_count"] = min(max(failedCount, 0), 500)
        }
        if let warningCount = integerProperty(properties["skill_quarantine_warning_count"]) {
            clean["skill_quarantine_warning_count"] = min(max(warningCount, 0), 500)
        }
        return clean
    }

    private func integerProperty(_ value: Any?) -> Int? {
        if let intValue = value as? Int {
            return intValue
        }
        if let doubleValue = value as? Double {
            return Int(doubleValue)
        }
        if let number = value as? NSNumber {
            return number.intValue
        }
        return nil
    }

    private func scheduleFlushOnQueue() {
        flushWorkItem?.cancel()
        let item = DispatchWorkItem { [weak self] in
            self?.flush()
        }
        flushWorkItem = item
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 5.0, execute: item)
    }

    private func flushOnQueue(completion: (() -> Void)? = nil) {
        flushWorkItem?.cancel()
        flushWorkItem = nil
        guard isEnabled, let endpoint = endpoint, !pendingEvents.isEmpty else {
            if !isEnabled {
                pendingEvents.removeAll()
            }
            completion?()
            return
        }
        let batch = pendingEvents
        pendingEvents.removeAll()
        send(batch, to: endpoint, completion: completion)
    }

    private func send(_ events: [[String: Any]], to endpoint: URL, completion: (() -> Void)? = nil) {
        let body: [String: Any] = ["events": events]
        guard JSONSerialization.isValidJSONObject(body),
              let data = try? JSONSerialization.data(withJSONObject: body, options: [])
        else {
            completion?()
            return
        }
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.httpBody = data
        request.timeoutInterval = 3.0
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("OpenRelix-macOS", forHTTPHeaderField: "User-Agent")
        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        URLSession.shared.dataTask(with: request) { _, _, _ in
            completion?()
        }.resume()
    }
}

private final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow?
    private var webView: WKWebView?
    private var stateRoot = preferredStateRoot()
    private var loadedPanelPath: String?
    private var loadedPanelModificationDate: Date?
    private var panelDirectoryMonitor: DispatchSourceFileSystemObject?
    private var panelDirectoryFileDescriptor: CInt = -1
    private var monitoredPanelDirectoryPath: String?
    private var pendingPanelReloadCheck: DispatchWorkItem?
    private let analytics = PanelAnalytics()
    private var analyticsMenuItem: NSMenuItem?
    private let lightPanelBackground = NSColor(
        calibratedRed: 245.0 / 255.0,
        green: 245.0 / 255.0,
        blue: 247.0 / 255.0,
        alpha: 1.0
    )
    private let darkPanelBackground = NSColor(
        calibratedRed: 0.09,
        green: 0.10,
        blue: 0.13,
        alpha: 1.0
    )

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        buildWindow()
        ensureTokenLiveLaunchAgent()
        analytics.track("app_launch")
        loadPanel()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationWillTerminate(_ notification: Notification) {
        stopPanelDirectoryMonitor()
        pendingPanelReloadCheck?.cancel()
        analytics.trackAppQuit()
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        reloadPanelIfChanged()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            window?.makeKeyAndOrderFront(nil)
        }
        reloadPanelIfChanged()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func windowDidBecomeKey(_ notification: Notification) {
        reloadPanelIfChanged()
    }

    private func buildWindow() {
        let configuration = WKWebViewConfiguration()
        let pagePreferences = WKWebpagePreferences()
        pagePreferences.allowsContentJavaScript = true
        configuration.defaultWebpagePreferences = pagePreferences
        configuration.userContentController.addUserScript(
            WKUserScript(
                source: panelThemeBridgeScript,
                injectionTime: .atDocumentStart,
                forMainFrameOnly: true
            )
        )
        configuration.userContentController.addUserScript(
            WKUserScript(
                source: panelUsageAnalyticsScript,
                injectionTime: .atDocumentEnd,
                forMainFrameOnly: true
            )
        )
        configuration.userContentController.add(self, name: "openrelixTheme")
        configuration.userContentController.add(self, name: "openrelixAnalytics")
        configuration.userContentController.add(self, name: "openrelixOpenExternal")
        configuration.userContentController.add(self, name: "openrelixEnsureTokenLive")
        configuration.userContentController.add(self, name: "openrelixChooseProjectFolder")

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        webView.wantsLayer = true
        if webView.responds(to: Selector(("setDrawsBackground:"))) {
            webView.setValue(false, forKey: "drawsBackground")
        }
        self.webView = webView

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.center()
        window.minSize = NSSize(width: 920, height: 620)
        window.title = "OpenRelix"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        if #available(macOS 11.0, *) {
            window.toolbarStyle = .unified
        }
        window.contentView = webView
        window.delegate = self
        self.window = window
        applyPanelBackground(isDark: nil)
        window.makeKeyAndOrderFront(nil)
    }

    private func systemPrefersDark() -> Bool {
        NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
    }

    private func panelBackground(isDark: Bool?) -> NSColor {
        (isDark ?? systemPrefersDark()) ? darkPanelBackground : lightPanelBackground
    }

    private func applyPanelBackground(isDark: Bool?) {
        let background = panelBackground(isDark: isDark)
        webView?.layer?.backgroundColor = background.cgColor
        if #available(macOS 12.0, *) {
            webView?.underPageBackgroundColor = background
        }
        window?.backgroundColor = background
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "openrelixTheme", let theme = message.body as? String {
            if theme == "dark" {
                applyPanelBackground(isDark: true)
            } else if theme == "light" {
                applyPanelBackground(isDark: false)
            }
            return
        }
        if message.name == "openrelixEnsureTokenLive" {
            ensureTokenLiveLaunchAgent()
            return
        }
        if message.name == "openrelixChooseProjectFolder" {
            chooseProjectFolderForPanel()
            return
        }
        if message.name == "openrelixAnalytics" {
            analytics.trackPanelMessage(message.body)
            return
        }
        if message.name == "openrelixOpenExternal",
           let rawURL = message.body as? String,
           let url = URL(string: rawURL) {
            _ = openOutsidePanel(url)
        }
    }

    private func javascriptStringLiteral(_ value: String) -> String {
        guard
            JSONSerialization.isValidJSONObject([value]),
            let data = try? JSONSerialization.data(withJSONObject: [value], options: []),
            let text = String(data: data, encoding: .utf8),
            text.count >= 2
        else {
            return "\"\""
        }
        return String(text.dropFirst().dropLast())
    }

    private func sendProjectFolderSelectionToPanel(_ path: String?) {
        let argument = path.map { javascriptStringLiteral($0) } ?? "null"
        webView?.evaluateJavaScript(
            "window.openrelixProjectFolderSelected && window.openrelixProjectFolderSelected(\(argument));",
            completionHandler: nil
        )
    }

    private func chooseProjectFolderForPanel() {
        DispatchQueue.main.async {
            let panel = NSOpenPanel()
            panel.canChooseFiles = false
            panel.canChooseDirectories = true
            panel.allowsMultipleSelection = false
            panel.canCreateDirectories = false
            panel.prompt = "添加项目文件夹"
            panel.message = "选择要纳入 OpenRelix skill 扫描的项目根目录。"
            if let window = self.window {
                panel.beginSheetModal(for: window) { response in
                    guard response == .OK, let url = panel.url else {
                        self.sendProjectFolderSelectionToPanel(nil)
                        return
                    }
                    self.sendProjectFolderSelectionToPanel(url.standardizedFileURL.path)
                }
            } else {
                let response = panel.runModal()
                guard response == .OK, let url = panel.url else {
                    self.sendProjectFolderSelectionToPanel(nil)
                    return
                }
                self.sendProjectFolderSelectionToPanel(url.standardizedFileURL.path)
            }
        }
    }

    private func runLaunchctl(_ arguments: [String]) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        do {
            try process.run()
            process.waitUntilExit()
        } catch {
        }
    }

    private func ensureTokenLiveLaunchAgent() {
        let plistURL = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("LaunchAgents", isDirectory: true)
            .appendingPathComponent(tokenLivePlistName, isDirectory: false)
        let plistPath = plistURL.path
        guard FileManager.default.fileExists(atPath: plistPath) else {
            return
        }
        let domain = "gui/\(getuid())"
        let serviceTarget = "\(domain)/\(tokenLiveLabel)"
        DispatchQueue.global(qos: .utility).async {
            self.runLaunchctl(["bootstrap", domain, plistPath])
            self.runLaunchctl(["kickstart", "-k", serviceTarget])
        }
    }

    private func isPanelURL(_ url: URL) -> Bool {
        guard url.isFileURL else {
            return false
        }
        return url.standardizedFileURL.path == panelURL(for: stateRoot).standardizedFileURL.path
    }

    private func openOutsidePanel(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else {
            return false
        }
        if scheme == "about" || scheme == "data" || scheme == "javascript" {
            return false
        }
        if url.isFileURL && isPanelURL(url) {
            return false
        }
        NSWorkspace.shared.open(url)
        return true
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.allow)
            return
        }
        if openOutsidePanel(url) {
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        analytics.track("panel_loaded")
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        analytics.track("panel_load_failed", properties: ["reason": "navigation_error"])
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        analytics.track("panel_load_failed", properties: ["reason": "provisional_navigation_error"])
    }

    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        guard navigationAction.targetFrame == nil, let url = navigationAction.request.url else {
            return nil
        }
        if openOutsidePanel(url) {
            return nil
        }
        webView.load(navigationAction.request)
        return nil
    }

    private func buildMenu() {
        let mainMenu = NSMenu()

        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu(title: "OpenRelix")

        let aboutItem = NSMenuItem(title: "About OpenRelix", action: #selector(showAbout(_:)), keyEquivalent: "")
        aboutItem.target = self
        appMenu.addItem(aboutItem)

        let analyticsItem = NSMenuItem(title: "Share Anonymous Usage Metrics", action: #selector(toggleAnalytics(_:)), keyEquivalent: "")
        analyticsItem.target = self
        appMenu.addItem(analyticsItem)
        analyticsMenuItem = analyticsItem

        let privacyItem = NSMenuItem(title: "Analytics Privacy", action: #selector(showAnalyticsPrivacy(_:)), keyEquivalent: "")
        privacyItem.target = self
        appMenu.addItem(privacyItem)
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(NSMenuItem(title: "Quit OpenRelix", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu

        let fileMenuItem = NSMenuItem()
        mainMenu.addItem(fileMenuItem)
        let fileMenu = NSMenu(title: "File")

        let reloadItem = NSMenuItem(title: "Reload", action: #selector(reloadPanel(_:)), keyEquivalent: "r")
        reloadItem.target = self
        fileMenu.addItem(reloadItem)

        let browserItem = NSMenuItem(title: "Open Panel in Browser", action: #selector(openPanelInBrowser(_:)), keyEquivalent: "b")
        browserItem.target = self
        fileMenu.addItem(browserItem)

        let revealItem = NSMenuItem(title: "Reveal State Folder", action: #selector(revealStateFolder(_:)), keyEquivalent: "")
        revealItem.target = self
        fileMenu.addItem(revealItem)

        fileMenuItem.submenu = fileMenu

        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "Edit")

        editMenu.addItem(NSMenuItem(title: "Undo", action: Selector(("undo:")), keyEquivalent: "z"))
        let redoItem = NSMenuItem(title: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redoItem.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(redoItem)
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        editMenuItem.submenu = editMenu

        NSApp.mainMenu = mainMenu
        updateAnalyticsMenuItem()
    }

    private func updateAnalyticsMenuItem() {
        analyticsMenuItem?.state = analytics.isUserDisabled ? .off : .on
    }

    private func panelModificationDate(_ panel: URL) -> Date? {
        let attributes = try? FileManager.default.attributesOfItem(atPath: panel.path)
        return attributes?[.modificationDate] as? Date
    }

    private func stopPanelDirectoryMonitor() {
        panelDirectoryMonitor?.cancel()
        panelDirectoryMonitor = nil
        panelDirectoryFileDescriptor = -1
        monitoredPanelDirectoryPath = nil
    }

    private func refreshPanelDirectoryMonitor(for panel: URL) {
        let directory = panel.deletingLastPathComponent()
        let directoryPath = directory.standardizedFileURL.path
        guard monitoredPanelDirectoryPath != directoryPath || panelDirectoryMonitor == nil else {
            return
        }

        stopPanelDirectoryMonitor()
        guard FileManager.default.fileExists(atPath: directoryPath) else {
            return
        }

        let fileDescriptor = open(directoryPath, O_EVTONLY)
        guard fileDescriptor >= 0 else {
            return
        }

        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fileDescriptor,
            eventMask: [.write, .rename, .delete, .attrib],
            queue: DispatchQueue.main
        )
        source.setEventHandler { [weak self] in
            self?.schedulePanelReloadCheck()
        }
        source.setCancelHandler {
            close(fileDescriptor)
        }
        panelDirectoryMonitor = source
        panelDirectoryFileDescriptor = fileDescriptor
        monitoredPanelDirectoryPath = directoryPath
        source.resume()
    }

    private func schedulePanelReloadCheck() {
        pendingPanelReloadCheck?.cancel()
        let item = DispatchWorkItem { [weak self] in
            self?.reloadPanelIfChanged()
        }
        pendingPanelReloadCheck = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.75, execute: item)
    }

    private func rememberLoadedPanel(_ panel: URL) {
        loadedPanelPath = panel.standardizedFileURL.path
        loadedPanelModificationDate = panelModificationDate(panel)
    }

    private func reloadPanelIfChanged() {
        let nextStateRoot = preferredStateRoot()
        let panel = panelURL(for: nextStateRoot)
        refreshPanelDirectoryMonitor(for: panel)
        guard FileManager.default.fileExists(atPath: panel.path) else {
            return
        }

        let nextPath = panel.standardizedFileURL.path
        let nextModificationDate = panelModificationDate(panel)
        let loadedDate = loadedPanelModificationDate
        let panelPathChanged = loadedPanelPath != nextPath
        let panelContentChanged: Bool
        if let nextModificationDate = nextModificationDate, let loadedDate = loadedDate {
            panelContentChanged = abs(nextModificationDate.timeIntervalSince(loadedDate)) > 0.001
        } else {
            panelContentChanged = nextModificationDate != nil && loadedDate == nil
        }

        if panelPathChanged || panelContentChanged {
            loadPanel()
        }
    }

    private func loadPanel() {
        stateRoot = preferredStateRoot()
        let panel = panelURL(for: stateRoot)
        refreshPanelDirectoryMonitor(for: panel)
        if FileManager.default.fileExists(atPath: panel.path) {
            rememberLoadedPanel(panel)
            webView?.loadFileURL(panel, allowingReadAccessTo: stateRoot)
        } else {
            loadedPanelPath = nil
            loadedPanelModificationDate = nil
            analytics.track("panel_load_failed", properties: ["reason": "missing_panel"])
            webView?.loadHTMLString(
                missingPanelHTML(panelPath: panel.path, stateRootPath: stateRoot.path),
                baseURL: nil
            )
        }
    }

    @objc private func reloadPanel(_ sender: Any?) {
        loadPanel()
    }

    @objc private func openPanelInBrowser(_ sender: Any?) {
        let panel = panelURL(for: stateRoot)
        if FileManager.default.fileExists(atPath: panel.path) {
            NSWorkspace.shared.open(panel)
        } else {
            NSWorkspace.shared.open(stateRoot)
        }
    }

    @objc private func revealStateFolder(_ sender: Any?) {
        NSWorkspace.shared.activateFileViewerSelecting([stateRoot])
    }

    @objc private func showAbout(_ sender: Any?) {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0"
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "OpenRelix",
            .applicationVersion: version,
            .version: version,
        ])
    }

    @objc private func toggleAnalytics(_ sender: Any?) {
        analytics.setUserDisabled(!analytics.isUserDisabled)
        updateAnalyticsMenuItem()
    }

    @objc private func showAnalyticsPrivacy(_ sender: Any?) {
        let alert = NSAlert()
        alert.messageText = "Anonymous Usage Metrics"
        alert.informativeText = """
        When OPENRELIX_ANALYTICS_ENDPOINT is configured, OpenRelix sends anonymous macOS client events by default: app launch, panel load state, fixed panel module visibility, dwell time, core control clicks, and app quit.

        The payload includes a random install ID, per-launch session ID, app version, coarse macOS version, fixed module/control IDs, and durations. It does not send window titles, prompts, memory text, review text, file paths, usernames, hostnames, tokens, cookies, local reports, or raw OpenRelix state.

        Turn this off from the OpenRelix menu with "Share Anonymous Usage Metrics", or launch with OPENRELIX_ANALYTICS_ENABLED=0 / OPENRELIX_ANALYTICS_DISABLED=1.
        """
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}

private let application = NSApplication.shared
private let delegate = AppDelegate()
application.delegate = delegate
application.run()
