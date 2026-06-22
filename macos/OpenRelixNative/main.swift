import AppKit
import Foundation
import SwiftUI

// MARK: - Configuration / state-root resolution
//
// This is the native OpenRelix macOS client. Unlike the legacy
// OpenRelixClient shell (which renders reports/panel.html inside a
// WKWebView), this client decodes reports/overview-data.json and renders
// every section with native SwiftUI views. No web view, no HTML.

private let stateRootResourceName = "OpenRelixStateRoot"
private let tokenLiveLabel = "io.github.openrelix.token-live"
private let tokenLivePlistName = "\(tokenLiveLabel).plist"

private func trimmed(_ value: String) -> String {
    value.trimmingCharacters(in: .whitespacesAndNewlines)
}

private func expandedPath(_ value: String) -> String {
    (value as NSString).expandingTildeInPath
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

private func defaultApplicationSupportStateRoot() -> URL {
    let fallback = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
        .appendingPathComponent("Library", isDirectory: true)
        .appendingPathComponent("Application Support", isDirectory: true)
    let appSupport = FileManager.default
        .urls(for: .applicationSupportDirectory, in: .userDomainMask).first ?? fallback
    return appSupport.appendingPathComponent("openrelix", isDirectory: true)
}

private func overviewDataURL(for stateRoot: URL) -> URL {
    stateRoot
        .appendingPathComponent("reports", isDirectory: true)
        .appendingPathComponent("overview-data.json", isDirectory: false)
}

private func candidateStateRoots() -> [URL] {
    var candidates: [URL] = []
    let environment = ProcessInfo.processInfo.environment

    if let explicit = environment["AI_ASSET_STATE_DIR"], !trimmed(explicit).isEmpty {
        candidates.append(URL(fileURLWithPath: expandedPath(explicit), isDirectory: true))
    }
    if let bundled = bundledConfigValue(resourceName: stateRootResourceName) {
        candidates.append(URL(fileURLWithPath: expandedPath(bundled), isDirectory: true))
    }
    candidates.append(defaultApplicationSupportStateRoot())

    var seen = Set<String>()
    return candidates.filter { url in
        let key = url.standardizedFileURL.path
        if seen.contains(key) { return false }
        seen.insert(key)
        return true
    }
}

private func preferredStateRoot() -> URL {
    let candidates = candidateStateRoots()
    for candidate in candidates
    where FileManager.default.fileExists(atPath: overviewDataURL(for: candidate).path) {
        return candidate
    }
    return candidates.first ?? defaultApplicationSupportStateRoot()
}

// MARK: - Flexible decoding helpers

/// Decodes a JSON value that may be a string, number, or bool into a display string.
private struct DisplayValue: Decodable {
    let text: String

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(String.self) {
            text = value
        } else if let value = try? container.decode(Int.self) {
            text = value.formatted()
        } else if let value = try? container.decode(Double.self) {
            text = value == value.rounded() ? Int(value).formatted() : String(format: "%.2f", value)
        } else if let value = try? container.decode(Bool.self) {
            text = value ? "true" : "false"
        } else {
            text = "—"
        }
    }
}

// MARK: - Overview data model
//
// Decoded with `.convertFromSnakeCase`, so snake_case JSON keys map onto
// camelCase Swift properties. Everything is optional so the client tolerates
// schema drift instead of failing the whole decode.

private struct OverviewData: Decodable {
    var schemaVersion: Int?
    var language: String?
    var generatedAt: String?
    var summary: Summary?
    var metrics: [Metric]?
    var tokenUsage: TokenUsage?
    var pipelineStatus: PipelineStatus?
    var projectContexts: [ProjectContext]?
    var memoryRegistry: [MemoryEntry]?
    var codexNativeMemoryCounts: CodexCounts?
    var claudeNativeMemoryCounts: ClaudeCounts?
    var assetPanelRows: [AssetRow]?
    var summaryTerms: [Term]?
    var readingGuide: [String]?
    var nightlyTitle: String?
    var nightlyNote: String?

    struct Summary: Decodable {
        var totalAssets: Int?
        var discoveredAssets: Int?
        var activeAssets: Int?
        var taskReviews: Int?
        var trackedUsageEvents: Int?
        var trackedMinutesSaved: Int?
        var repoScopedAssets: Int?
        var dailyWindowCount: Int?
    }

    struct Metric: Decodable, Identifiable {
        var key: String?
        var label: String?
        var value: DisplayValue?
        var caption: String?
        var id: String { key ?? label ?? UUID().uuidString }
    }

    struct TokenUsage: Decodable {
        var available: Bool?
        var error: String?
        var todayTokenCostDisplay: String?
        var todayTotalTokensDisplay: String?
        var todayCostDisplay: String?
        var sevenDayTotalTokensDisplay: String?
        var sevenDayCostDisplay: String?
        var periodTotalTokensDisplay: String?
        var periodCostDisplay: String?
        var periodAverageTokensDisplay: String?
        var rangeLabel: String?
        var providerLabel: String?
        var overviewNote: String?
        var todayBreakdown: [Breakdown]?
        var dailyRows: [DailyRow]?

        struct Breakdown: Decodable, Identifiable {
            var label: String?
            var value: Int?
            var display: String?
            var tone: String?
            var id: String { label ?? UUID().uuidString }
        }

        struct DailyRow: Decodable, Identifiable {
            var label: String?
            var date: String?
            var value: Int?
            var id: String { date ?? label ?? UUID().uuidString }
        }
    }

    struct PipelineStatus: Decodable {
        var title: String?
        var titleEn: String?
        var status: String?
        var targetDate: String?
        var stage: String?
        var currentStep: String?
        var currentStepIndex: Int?
        var stepCount: Int?
        var message: String?
        var messageEn: String?
        var updatedAtIso: String?
        var startedAtIso: String?
    }

    struct ProjectContext: Decodable, Identifiable {
        var label: String?
        var windowCount: Int?
        var questionCount: Int?
        var conclusionCount: Int?
        var latestActivityDisplay: String?
        var cwdPreview: String?
        var summary: String?
        var id: String { label ?? UUID().uuidString }
    }

    struct MemoryEntry: Decodable, Identifiable {
        var memoryKey: String?
        var displayTitle: String?
        var displayBucket: String?
        var displayMemoryType: String?
        var displayPriority: String?
        var projectLabel: String?
        var valueNote: String?
        var id: String { memoryKey ?? displayTitle ?? UUID().uuidString }
    }

    struct CodexCounts: Decodable {
        var topicItems: Int?
        var userProfile: Int?
        var userPreferences: Int?
        var generalTips: Int?
    }

    struct ClaudeCounts: Decodable {
        var topicItems: Int?
        var userPreferences: Int?
        var generalTips: Int?
        var autoMemoryItems: Int?
        var autoMemoryFileCount: Int?
        var autoMemoryProjectCount: Int?
        var totalItems: Int?
    }

    struct AssetRow: Decodable, Identifiable {
        var assetKey: String?
        var name: String?
        var description: String?
        var type: String?
        var kind: String?
        var identifier: String?
        var windows7d: Int?
        var windows30d: Int?
        var readEvents7d: Int?
        var readEvents30d: Int?
        var id: String { assetKey ?? identifier ?? UUID().uuidString }
    }

    struct Term: Decodable, Identifiable {
        var label: String?
        var value: Int?
        var id: String { label ?? UUID().uuidString }
    }
}

private enum OverviewLoader {
    static func decode(from url: URL) throws -> OverviewData {
        let data = try Data(contentsOf: url)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(OverviewData.self, from: data)
    }
}

// MARK: - Store

private enum LoadState {
    case loading
    case loaded(OverviewData, URL)
    case failed(String, URL)
}

private final class OverviewStore: ObservableObject {
    @Published var state: LoadState = .loading
    private(set) var stateRoot: URL = preferredStateRoot()

    var dataURL: URL { overviewDataURL(for: stateRoot) }

    func reload() {
        state = .loading
        stateRoot = preferredStateRoot()
        let url = overviewDataURL(for: stateRoot)
        guard FileManager.default.fileExists(atPath: url.path) else {
            state = .failed("没有找到 overview-data.json。请先运行一次安装或刷新流程。", url)
            return
        }
        do {
            let decoded = try OverviewLoader.decode(from: url)
            state = .loaded(decoded, url)
        } catch {
            state = .failed("解析 overview-data.json 失败：\(error.localizedDescription)", url)
        }
    }
}

// MARK: - Design helpers

private enum Palette {
    static func toneColor(_ tone: String?) -> Color {
        switch tone {
        case "token-input": return .blue
        case "token-output": return .green
        case "token-cache": return .orange
        case "token-reasoning": return .purple
        default: return .accentColor
        }
    }

    static func statusColor(_ status: String?) -> Color {
        switch status {
        case "running": return .blue
        case "succeeded", "success", "ok": return .green
        case "failed", "error": return .red
        case "idle", "scheduled": return .secondary
        default: return .accentColor
        }
    }
}

private struct Card<Content: View>: View {
    let title: String
    var subtitle: String? = nil
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.headline)
                if let subtitle, !subtitle.isEmpty {
                    Text(subtitle).font(.caption).foregroundStyle(.secondary)
                }
            }
            content()
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(.separator, lineWidth: 0.5)
        )
    }
}

// MARK: - Sections

private enum PanelSection: String, CaseIterable, Identifiable {
    case overview, tokens, pipeline, projects, memory, assets

    var id: String { rawValue }

    var title: String {
        switch self {
        case .overview: return "概览"
        case .tokens: return "Token 用量"
        case .pipeline: return "流水线状态"
        case .projects: return "工作窗口"
        case .memory: return "记忆"
        case .assets: return "资产"
        }
    }

    var systemImage: String {
        switch self {
        case .overview: return "square.grid.2x2"
        case .tokens: return "bolt.fill"
        case .pipeline: return "gearshape.2"
        case .projects: return "rectangle.3.group"
        case .memory: return "brain.head.profile"
        case .assets: return "shippingbox"
        }
    }
}

private struct OverviewSection: View {
    let data: OverviewData

    private let columns = [GridItem(.adaptive(minimum: 200), spacing: 14)]

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if let metrics = data.metrics, !metrics.isEmpty {
                LazyVGrid(columns: columns, spacing: 14) {
                    ForEach(metrics) { metric in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(metric.value?.text ?? "—")
                                .font(.system(size: 30, weight: .semibold, design: .rounded))
                                .monospacedDigit()
                            Text(metric.label ?? "")
                                .font(.subheadline.weight(.medium))
                            if let caption = metric.caption, !caption.isEmpty {
                                Text(caption)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(3)
                            }
                        }
                        .padding(16)
                        .frame(maxWidth: .infinity, minHeight: 120, alignment: .topLeading)
                        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 14, style: .continuous)
                                .strokeBorder(.separator, lineWidth: 0.5)
                        )
                    }
                }
            }

            if let terms = data.summaryTerms, !terms.isEmpty {
                Card(title: "高频关键词", subtitle: "最近窗口中出现最多的主题") {
                    FlowTags(terms: Array(terms.prefix(20)))
                }
            }

            if let guide = data.readingGuide, !guide.isEmpty {
                Card(title: "怎么看这块面板") {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(Array(guide.enumerated()), id: \.offset) { index, line in
                            HStack(alignment: .top, spacing: 8) {
                                Text("\(index + 1).")
                                    .font(.callout.weight(.semibold))
                                    .foregroundStyle(.secondary)
                                Text(line).font(.callout)
                            }
                        }
                    }
                }
            }
        }
    }
}

private struct FlowTags: View {
    let terms: [OverviewData.Term]

    private let columns = [GridItem(.adaptive(minimum: 120), spacing: 8)]

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: 8) {
            ForEach(terms) { term in
                HStack(spacing: 6) {
                    Text(term.label ?? "—").font(.callout)
                    Spacer(minLength: 4)
                    Text((term.value ?? 0).formatted())
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(.background.secondary, in: Capsule())
            }
        }
    }
}

private struct TokenUsageView: View {
    let usage: OverviewData.TokenUsage

    private let headlineColumns = [GridItem(.adaptive(minimum: 170), spacing: 14)]

    private var maxDaily: Int {
        usage.dailyRows?.compactMap { $0.value }.max() ?? 0
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if usage.available == false {
                Card(title: "Token 用量不可用") {
                    Text(usage.error?.isEmpty == false ? usage.error! : "暂无 token 用量数据。")
                        .foregroundStyle(.secondary)
                }
            } else {
                LazyVGrid(columns: headlineColumns, spacing: 14) {
                    headline("今日", usage.todayTotalTokensDisplay, usage.todayCostDisplay)
                    headline("近 7 天", usage.sevenDayTotalTokensDisplay, usage.sevenDayCostDisplay)
                    headline("区间合计", usage.periodTotalTokensDisplay, usage.periodCostDisplay)
                    headline("日均", usage.periodAverageTokensDisplay, nil)
                }

                if let rows = usage.dailyRows, !rows.isEmpty {
                    Card(title: "每日 Token", subtitle: usage.rangeLabel) {
                        VStack(spacing: 8) {
                            ForEach(rows) { row in
                                HStack(spacing: 10) {
                                    Text(row.label ?? "")
                                        .font(.caption.monospacedDigit())
                                        .frame(width: 52, alignment: .leading)
                                        .foregroundStyle(.secondary)
                                    GeometryReader { geo in
                                        let fraction = maxDaily > 0 ? Double(row.value ?? 0) / Double(maxDaily) : 0
                                        ZStack(alignment: .leading) {
                                            Capsule().fill(.quaternary)
                                            Capsule()
                                                .fill(Color.accentColor.gradient)
                                                .frame(width: max(4, geo.size.width * fraction))
                                        }
                                    }
                                    .frame(height: 14)
                                    Text((row.value ?? 0).formatted())
                                        .font(.caption.monospacedDigit())
                                        .frame(width: 110, alignment: .trailing)
                                }
                            }
                        }
                    }
                }

                if let breakdown = usage.todayBreakdown, !breakdown.isEmpty {
                    Card(title: "今日构成") {
                        VStack(spacing: 10) {
                            ForEach(breakdown) { item in
                                HStack(spacing: 10) {
                                    Circle()
                                        .fill(Palette.toneColor(item.tone))
                                        .frame(width: 9, height: 9)
                                    Text(item.label ?? "")
                                    Spacer()
                                    Text(item.display ?? (item.value ?? 0).formatted())
                                        .font(.callout.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }

                if let note = usage.overviewNote, !note.isEmpty {
                    Text(note).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func headline(_ title: String, _ tokens: String?, _ cost: String?) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(tokens ?? "—")
                .font(.system(size: 26, weight: .semibold, design: .rounded))
                .monospacedDigit()
            if let cost, !cost.isEmpty {
                Text(cost).font(.callout.weight(.medium)).foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 110, alignment: .topLeading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(.separator, lineWidth: 0.5)
        )
    }
}

private struct PipelineSection: View {
    let status: OverviewData.PipelineStatus

    private var progress: Double {
        guard let count = status.stepCount, count > 0,
              let index = status.currentStepIndex else { return 0 }
        return min(1, max(0, Double(index) / Double(count)))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Card(title: status.title ?? "流水线", subtitle: status.targetDate) {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(spacing: 8) {
                        Circle().fill(Palette.statusColor(status.status)).frame(width: 10, height: 10)
                        Text(status.status?.capitalized ?? "—").font(.headline)
                        if let stage = status.stage, !stage.isEmpty {
                            Text(stage)
                                .font(.caption)
                                .padding(.horizontal, 8).padding(.vertical, 3)
                                .background(.quaternary, in: Capsule())
                        }
                    }
                    if let count = status.stepCount, count > 0 {
                        VStack(alignment: .leading, spacing: 6) {
                            ProgressView(value: progress)
                            Text("步骤 \(min((status.currentStepIndex ?? 0) + 1, count)) / \(count) · \(status.currentStep ?? "")")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if let message = status.message, !message.isEmpty {
                        Text(message).font(.callout)
                    }
                    if let updated = status.updatedAtIso {
                        Text("更新于 \(updated)").font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }
}

private struct ProjectsSection: View {
    let contexts: [OverviewData.ProjectContext]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(contexts) { context in
                Card(title: context.label ?? "未命名", subtitle: context.cwdPreview) {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(spacing: 18) {
                            stat("窗口", context.windowCount)
                            stat("提问", context.questionCount)
                            stat("结论", context.conclusionCount)
                            Spacer()
                            if let activity = context.latestActivityDisplay {
                                Text(activity).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        if let summary = context.summary, !summary.isEmpty {
                            Text(summary)
                                .font(.callout)
                                .foregroundStyle(.secondary)
                                .lineLimit(3)
                        }
                    }
                }
            }
        }
    }

    private func stat(_ label: String, _ value: Int?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text((value ?? 0).formatted())
                .font(.title3.weight(.semibold).monospacedDigit())
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }
    }
}

private struct MemorySection: View {
    let data: OverviewData

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 14) {
                if let codex = data.codexNativeMemoryCounts {
                    Card(title: "Codex 原生记忆") {
                        VStack(alignment: .leading, spacing: 8) {
                            countRow("主题条目", codex.topicItems)
                            countRow("用户画像", codex.userProfile)
                            countRow("偏好", codex.userPreferences)
                            countRow("通用提示", codex.generalTips)
                        }
                    }
                }
                if let claude = data.claudeNativeMemoryCounts {
                    Card(title: "Claude 原生记忆") {
                        VStack(alignment: .leading, spacing: 8) {
                            countRow("主题条目", claude.topicItems)
                            countRow("偏好", claude.userPreferences)
                            countRow("通用提示", claude.generalTips)
                            countRow("自动记忆", claude.autoMemoryItems)
                        }
                    }
                }
            }

            if let registry = data.memoryRegistry, !registry.isEmpty {
                Card(title: "个人资产记忆", subtitle: "\(registry.count) 条") {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(registry.prefix(30))) { entry in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack(spacing: 8) {
                                    if let priority = entry.displayPriority {
                                        Text(priority)
                                            .font(.caption2.weight(.semibold))
                                            .padding(.horizontal, 6).padding(.vertical, 2)
                                            .background(.tint.opacity(0.15), in: Capsule())
                                    }
                                    Text(entry.displayTitle ?? "—").font(.callout.weight(.medium))
                                    Spacer()
                                    if let label = entry.projectLabel {
                                        Text(label).font(.caption2).foregroundStyle(.secondary)
                                    }
                                }
                                if let note = entry.valueNote, !note.isEmpty {
                                    Text(note)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(2)
                                }
                            }
                            .padding(.vertical, 8)
                            Divider()
                        }
                    }
                }
            }
        }
    }

    private func countRow(_ label: String, _ value: Int?) -> some View {
        HStack {
            Text(label).font(.callout)
            Spacer()
            Text((value ?? 0).formatted())
                .font(.title3.weight(.semibold).monospacedDigit())
        }
    }
}

private struct AssetsSection: View {
    let rows: [OverviewData.AssetRow]

    var body: some View {
        Card(title: "高频 Skills / 资产", subtitle: "\(rows.count) 项 · 7 日 / 30 日读取") {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(rows.prefix(40)) { row in
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.name ?? row.identifier ?? "—").font(.callout.weight(.medium))
                            if let description = row.description, !description.isEmpty {
                                Text(description)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                            }
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text("读取 \(row.readEvents7d ?? 0) / \(row.readEvents30d ?? 0)")
                                .font(.caption.monospacedDigit())
                            Text("窗口 \(row.windows7d ?? 0) / \(row.windows30d ?? 0)")
                                .font(.caption2.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 8)
                    Divider()
                }
            }
        }
    }
}

// MARK: - Root view

private struct ContentView: View {
    @EnvironmentObject var store: OverviewStore
    @State private var selection: PanelSection = .overview

    var body: some View {
        NavigationSplitView {
            List(PanelSection.allCases, selection: $selection) { section in
                Label(section.title, systemImage: section.systemImage).tag(section)
            }
            .navigationSplitViewColumnWidth(min: 180, ideal: 200)
        } detail: {
            detail
        }
    }

    @ViewBuilder private var detail: some View {
        switch store.state {
        case .loading:
            ProgressView("正在加载本地数据…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case let .failed(message, url):
            VStack(spacing: 12) {
                Image(systemName: "exclamationmark.triangle").font(.largeTitle).foregroundStyle(.secondary)
                Text(message).multilineTextAlignment(.center)
                Text(url.path).font(.caption.monospaced()).foregroundStyle(.secondary)
                Button("重新加载") { store.reload() }
            }
            .padding(40)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        case let .loaded(data, _):
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    header(data)
                    sectionView(for: data)
                }
                .padding(24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func header(_ data: OverviewData) -> some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(selection.title).font(.largeTitle.weight(.bold))
                if let generated = data.generatedAt {
                    Text("数据生成于 \(generated)").font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button {
                store.reload()
            } label: {
                Label("刷新", systemImage: "arrow.clockwise")
            }
        }
    }

    @ViewBuilder private func sectionView(for data: OverviewData) -> some View {
        switch selection {
        case .overview:
            OverviewSection(data: data)
        case .tokens:
            if let usage = data.tokenUsage {
                TokenUsageView(usage: usage)
            } else {
                empty("暂无 token 数据")
            }
        case .pipeline:
            if let status = data.pipelineStatus {
                PipelineSection(status: status)
            } else {
                empty("暂无流水线状态")
            }
        case .projects:
            if let contexts = data.projectContexts, !contexts.isEmpty {
                ProjectsSection(contexts: contexts)
            } else {
                empty("暂无工作窗口")
            }
        case .memory:
            MemorySection(data: data)
        case .assets:
            if let rows = data.assetPanelRows, !rows.isEmpty {
                AssetsSection(rows: rows)
            } else {
                empty("暂无资产数据")
            }
        }
    }

    private func empty(_ text: String) -> some View {
        Text(text).foregroundStyle(.secondary).padding(.top, 40)
    }
}

// MARK: - App delegate / AppKit shell

private final class AppDelegate: NSObject, NSApplicationDelegate {
    private let store = OverviewStore()
    private var window: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        ensureTokenLiveLaunchAgent()
        store.reload()

        let root = ContentView().environmentObject(store)
        let hosting = NSHostingView(rootView: root)

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1180, height: 820),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.center()
        window.minSize = NSSize(width: 900, height: 600)
        window.title = "OpenRelix"
        window.titlebarAppearsTransparent = true
        window.contentView = hosting
        self.window = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    private func buildMenu() {
        let mainMenu = NSMenu()

        let appItem = NSMenuItem()
        mainMenu.addItem(appItem)
        let appMenu = NSMenu(title: "OpenRelix")
        let about = NSMenuItem(title: "About OpenRelix", action: #selector(showAbout(_:)), keyEquivalent: "")
        about.target = self
        appMenu.addItem(about)
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "Quit OpenRelix", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appItem.submenu = appMenu

        let fileItem = NSMenuItem()
        mainMenu.addItem(fileItem)
        let fileMenu = NSMenu(title: "File")
        let reload = NSMenuItem(title: "Reload", action: #selector(reload(_:)), keyEquivalent: "r")
        reload.target = self
        fileMenu.addItem(reload)
        let reveal = NSMenuItem(title: "Reveal State Folder", action: #selector(revealStateFolder(_:)), keyEquivalent: "")
        reveal.target = self
        fileMenu.addItem(reveal)
        fileItem.submenu = fileMenu

        NSApp.mainMenu = mainMenu
    }

    @objc private func reload(_ sender: Any?) { store.reload() }

    @objc private func revealStateFolder(_ sender: Any?) {
        NSWorkspace.shared.activateFileViewerSelecting([store.stateRoot])
    }

    @objc private func showAbout(_ sender: Any?) {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0"
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "OpenRelix",
            .applicationVersion: version,
            .version: version,
        ])
    }

    private func runLaunchctl(_ arguments: [String]) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        do {
            try process.run()
            process.waitUntilExit()
        } catch {}
    }

    private func ensureTokenLiveLaunchAgent() {
        let plistURL = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("LaunchAgents", isDirectory: true)
            .appendingPathComponent(tokenLivePlistName, isDirectory: false)
        guard FileManager.default.fileExists(atPath: plistURL.path) else { return }
        let domain = "gui/\(getuid())"
        let target = "\(domain)/\(tokenLiveLabel)"
        DispatchQueue.global(qos: .utility).async {
            self.runLaunchctl(["bootstrap", domain, plistURL.path])
            self.runLaunchctl(["kickstart", "-k", target])
        }
    }
}

// MARK: - Headless self-test
//
// `OpenRelix --selftest` resolves the state root, decodes overview-data.json,
// prints a section summary, and exits — without starting the GUI. Used to
// verify the native decoder against real data in CI / headless environments.

private func runSelfTest() -> Int32 {
    let root = preferredStateRoot()
    let url = overviewDataURL(for: root)
    guard FileManager.default.fileExists(atPath: url.path) else {
        FileHandle.standardError.write(Data("selftest: missing \(url.path)\n".utf8))
        return 1
    }
    do {
        let data = try OverviewLoader.decode(from: url)
        var lines: [String] = []
        lines.append("selftest OK: \(url.path)")
        lines.append("  schema_version: \(data.schemaVersion.map(String.init) ?? "nil")")
        lines.append("  language: \(data.language ?? "nil")")
        lines.append("  generated_at: \(data.generatedAt ?? "nil")")
        lines.append("  metrics: \(data.metrics?.count ?? 0)")
        lines.append("  token_usage.available: \(data.tokenUsage?.available.map(String.init) ?? "nil")")
        lines.append("  token_usage.daily_rows: \(data.tokenUsage?.dailyRows?.count ?? 0)")
        lines.append("  pipeline_status.status: \(data.pipelineStatus?.status ?? "nil")")
        lines.append("  project_contexts: \(data.projectContexts?.count ?? 0)")
        lines.append("  memory_registry: \(data.memoryRegistry?.count ?? 0)")
        lines.append("  asset_panel_rows: \(data.assetPanelRows?.count ?? 0)")
        lines.append("  codex topic items: \(data.codexNativeMemoryCounts?.topicItems ?? 0)")
        lines.append("  claude auto memory: \(data.claudeNativeMemoryCounts?.autoMemoryItems ?? 0)")
        print(lines.joined(separator: "\n"))
        return 0
    } catch {
        FileHandle.standardError.write(Data("selftest: decode failed: \(error)\n".utf8))
        return 1
    }
}

// MARK: - Entry point

if CommandLine.arguments.contains("--selftest") {
    exit(runSelfTest())
}

private let application = NSApplication.shared
private let delegate = AppDelegate()
application.delegate = delegate
application.run()
