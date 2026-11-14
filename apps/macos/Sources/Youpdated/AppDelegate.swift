import AppKit
import SwiftUI
import YoupdatedKit
import YoupdatedUI

/// Wires the model to macOS: the menu bar item, notifications, the URL scheme, Services,
/// wake, and quitting
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    /// For App Intents
    private(set) static var shared: AppDelegate?

    let platform = MacPlatform()
    let router = Router()
    let downloader = Downloader()
    let loginItem = LoginItem()
    let model: AppModel
    private let configWatcher = ConfigWatcher()
    private var statusItem: StatusItemController?
    private var shareInbox: ShareInbox?

    /// dev build from scripts/make-dev-app.sh
    static var isDevelopment: Bool {
        Bundle.main.object(forInfoDictionaryKey: "YoupdatedDevelopment") as? Bool ?? false
    }

    static var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0"
    }

    override init() {
        let command = EngineCommand.resolve()
        model = AppModel(launch: { try ProcessTransport(command: command) }, platform: platform, appVersion: Self.appVersion)
        super.init()
        Self.shared = self
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = StatusItemController(model: model, router: router, downloader: downloader)
        platform.notifications.start()
        platform.notifications.respond = { [weak self] action, ids, url in
            await self?.model.respond(action, itemIds: ids, url: url)
        }
        platform.notifications.download = { [weak self] in self?.downloadUpdate() }
        platform.notifications.showInbox = { [weak self] in self?.router.show(.updates) }
        NSApp.servicesProvider = ServicesProvider { [weak self] value in self?.add(value) }
        NSUpdateDynamicServices()
        configWatcher.changed = { [weak self] in Task { await self?.model.configChanged() } }
        shareInbox = ShareInbox { [weak self] value in self?.add(value) }

        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                Task { await self.model.didWake() }
            }
        }

        firstLaunch()
        Task {
            await model.launch()
            configWatcher.watch(model.status?.configPath)
            follow()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        Task {
            await model.quit()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    /// agent app must keep running with all windows closed
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    /// Opening the app again from Finder or Spotlight shows the window
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { router.open() }
        return true
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls {
            switch DeepLink(url: url) {
            case .add(let value): add(value)
            case .open: router.show(.updates)
            case .check: Task { await model.checkNow() }
            case nil: break
            }
        }
    }

    /// A link from: menu bar, Services, Share extension, youpdated:// link
    func add(_ value: String) {
        router.add(value, model: model)
    }

    func downloadUpdate() {
        if let update = model.appUpdate {
            downloader.download(update)
            Task { await model.inbox.mark(true, ids: [update.itemId]) }
        }
    }

    /// Start at login is on by default
    private func firstLaunch() {
        guard !model.defaults.launchedBefore else { return }
        model.defaults.launchedBefore = true
        if !Self.isDevelopment && !loginItem.enabled {
            loginItem.set(true)
        }
    }

    /// Prompts with the model's state
    private func follow() {
        withObservationTracking {
            _ = model.needsPassphrase
            _ = model.isFirstRun
            _ = model.needsUserAgentConfirmation
            _ = model.status?.configPath
        } onChange: { [weak self] in
            Task { @MainActor in
                self?.react()
                self?.follow()
            }
        }
        react()
    }

    private func react() {
        // locked: the window asks for the passphrase. Unconfirmed user agent: it asks about that
        if model.needsPassphrase || model.isFirstRun || model.needsUserAgentConfirmation {
            router.show(.updates)
        }
        configWatcher.watch(model.status?.configPath)
    }
}

/// "Watch with Youpdated" in the Services menu, for selected links and text
@MainActor
final class ServicesProvider: NSObject {
    private let handle: (String) -> Void

    init(handle: @escaping (String) -> Void) {
        self.handle = handle
    }

    @objc func watchWithYoupdated(_ pasteboard: NSPasteboard, userData: String?, error: AutoreleasingUnsafeMutablePointer<NSString?>) {
        let text = pasteboard.string(forType: .URL) ?? pasteboard.string(forType: .string) ?? ""
        guard let value = DeepLink.watchValue(from: text) else {
            error.pointee = String(localized: "There's no link or name to watch in the selection.") as NSString
            return
        }
        handle(value)
    }
}
