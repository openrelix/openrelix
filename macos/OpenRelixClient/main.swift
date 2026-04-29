import AppKit
import WebKit

private let stateRootResourceName = "OpenRelixStateRoot"

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
    guard
        let url = Bundle.main.url(forResource: stateRootResourceName, withExtension: "txt"),
        let text = try? String(contentsOf: url, encoding: .utf8)
    else {
        return nil
    }

    for line in text.components(separatedBy: .newlines) {
        let value = trimmed(line)
        if !value.isEmpty && !value.hasPrefix("#") {
            return expandedPath(value)
        }
    }
    return nil
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
        :root { color-scheme: dark; }
        * { box-sizing: border-box; }
        html, body { margin: 0; min-height: 100%; }
        body {
          display: grid;
          min-height: 100vh;
          place-items: center;
          background: #111318;
          color: #f4f5f7;
          font: 15px/1.55 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif;
        }
        main {
          width: min(680px, calc(100vw - 48px));
          border: 1px solid rgba(255,255,255,.13);
          border-radius: 18px;
          background: rgba(255,255,255,.06);
          padding: 32px;
          box-shadow: 0 24px 80px rgba(0,0,0,.42);
        }
        h1 { margin: 0 0 10px; font-size: 26px; letter-spacing: 0; }
        p { margin: 10px 0 0; color: rgba(244,245,247,.74); }
        code {
          display: block;
          margin-top: 14px;
          padding: 12px 14px;
          overflow-wrap: anywhere;
          border-radius: 10px;
          background: rgba(0,0,0,.28);
          color: #d8e7ff;
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

private final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow?
    private var webView: WKWebView?
    private var stateRoot = preferredStateRoot()
    private let defaultBackground = NSColor(
        calibratedRed: 0.09,
        green: 0.10,
        blue: 0.13,
        alpha: 1.0
    )

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        buildWindow()
        loadPanel()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    private func buildWindow() {
        let configuration = WKWebViewConfiguration()
        let pagePreferences = WKWebpagePreferences()
        pagePreferences.allowsContentJavaScript = true
        configuration.defaultWebpagePreferences = pagePreferences

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.allowsBackForwardNavigationGestures = true
        webView.wantsLayer = true
        webView.layer?.backgroundColor = defaultBackground.cgColor
        if webView.responds(to: Selector(("setDrawsBackground:"))) {
            webView.setValue(false, forKey: "drawsBackground")
        }
        if #available(macOS 12.0, *) {
            webView.underPageBackgroundColor = defaultBackground
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
        window.backgroundColor = defaultBackground
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        if #available(macOS 11.0, *) {
            window.toolbarStyle = .unified
        }
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        self.window = window
    }

    private func buildMenu() {
        let mainMenu = NSMenu()

        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu(title: "OpenRelix")

        let aboutItem = NSMenuItem(title: "About OpenRelix", action: #selector(showAbout(_:)), keyEquivalent: "")
        aboutItem.target = self
        appMenu.addItem(aboutItem)
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
    }

    private func loadPanel() {
        stateRoot = preferredStateRoot()
        let panel = panelURL(for: stateRoot)
        if FileManager.default.fileExists(atPath: panel.path) {
            webView?.loadFileURL(panel, allowingReadAccessTo: stateRoot)
        } else {
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
}

private let application = NSApplication.shared
private let delegate = AppDelegate()
application.delegate = delegate
application.run()
