// Renders the window with contract fixtures, in light and dark, for review.
// swift run --package-path apps/macos YoupdatedScreens <output folder>

import AppKit
import SwiftUI
import YoupdatedKit
import YoupdatedUI

let output = URL(filePath: CommandLine.arguments.dropFirst().first ?? "screens", relativeTo: URL.currentDirectory())
let fixtures = URL(filePath: #filePath)
    .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()  // macos
    .deletingLastPathComponent()  // apps
    .appending(path: "contract/v1/fixtures")
// within six hours of the fixtures' last check, so nothing is due
let now = ContractCoding.parseTimestamp("2026-10-29T13:30:00Z")!

/// Draws into its own frame wherever it's put, so it can sit off every screen
final class OffscreenWindow: NSWindow {
    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect { frameRect }
}

@MainActor
final class QuietPlatform: AppPlatform {
    func post(_ notifications: [NotificationContent]) {}
    func schedule(_ notifications: [NotificationContent], at date: Date) {}
    func withdraw(itemIds: [String]) {}
    func storedPassphrase() -> String? { nil }
    func storePassphrase(_ passphrase: String?) {}
    func open(_ url: URL) {}
    var snapshotURL: URL? { nil }
    func snapshotWritten() {}
}

func fixtureReply(_ name: String) -> [String: Any] {
    let data = try! Data(contentsOf: fixtures.appending(path: "\(name).json"))
    let body = try! JSONSerialization.jsonObject(with: data) as! [String: Any]
    return body["reply"] as! [String: Any]
}

func inboxWithNotes() -> [String: Any] {
    var reply = fixtureReply("inbox.list/ok")
    var items = reply["items"] as! [[String: Any]]
    if let index = items.firstIndex(where: { $0["source"] as? String == "git" && $0["starred"] as? Bool == false }) {
        items[index]["body"] = """
        Security fixes for `ssl` and `email`, plus a regression fix in `asyncio`.

        - gh-12345: Fix a crash in `ssl.SSLContext.load_cert_chain` with an empty password.
        - gh-12346: `email.utils.parseaddr` no longer accepts malformed addresses.
        - gh-12347: Fix `asyncio.TaskGroup` canceling a finished task.

        See the [changelog](https://docs.python.org/3.14/whatsnew/changelog.html) for everything else.
        """
    }
    reply["items"] = items
    return reply
}

@MainActor
func makeModel(replies: [String: [String: Any]] = [:]) async -> (AppModel, ReplayTransport) {
    let transport = ReplayTransport(fixtures: fixtures, replies: replies)
    let defaults = AppDefaults(UserDefaults(suiteName: "YoupdatedScreens-\(UUID().uuidString)")!)
    let model = AppModel(launch: { transport }, platform: QuietPlatform(), defaults: defaults,
                         appVersion: "1.0.0", now: { now })
    await model.launch()
    return (model, transport)
}

@MainActor
func render(
    _ name: String, size: CGSize, toolbar: Bool = true, title: String = "Youpdated",
    after: (@MainActor () async -> Void)? = nil, @ViewBuilder _ content: () -> some View
) async throws {
    for dark in [false, true] {
        let controller = NSHostingController(rootView: content())
        if toolbar { controller.sceneBridgingOptions = [.toolbars, .title] }
        let style: NSWindow.StyleMask = toolbar ? [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView] : [.borderless]
        let window = OffscreenWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: style, backing: .buffered, defer: false)
        window.contentViewController = controller
        window.title = title
        window.toolbarStyle = .unified
        window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        window.backgroundColor = .windowBackgroundColor
        window.setFrame(NSRect(origin: CGPoint(x: -30000, y: -30000), size: size), display: false)
        window.alphaValue = 0
        window.orderFrontRegardless()
        try await Task.sleep(for: .milliseconds(600))
        if let after {
            await after()
            try await Task.sleep(for: .milliseconds(1500))
        }
        try await Task.sleep(for: .milliseconds(600))
        window.displayIfNeeded()
        let view = window.contentView!.superview ?? window.contentView!
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { continue }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        let file = output.appending(path: "\(name)-\(dark ? "dark" : "light").png")
        try bitmap.representation(using: .png, properties: [:])!.write(to: file)
        print(file.path)
        window.orderOut(nil)
    }
}

@MainActor
func run() async throws {
    try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
    let downloader = Downloader()

    // the feed, with cpython expanded
    let (feedModel, _) = await makeModel(replies: ["inbox.list": inboxWithNotes()])
    let feedWindow = WindowState()
    let expand: @MainActor () async -> Void = {
        // unread again, as if just found, so each pass starts from the same feed
        let cpython = { feedModel.feed.layout.cards.first { $0.target.source == "git" && !$0.starred } }
        feedModel.feed.collapse()
        if let card = cpython() { await feedModel.feed.setRead(false, card) }
        feedModel.feed.settle()
        if let card = cpython() { feedModel.feed.expand(card) }
    }
    try await render("feed-expanded", size: CGSize(width: 600, height: 860), after: expand) {
        AppWindowView().environment(feedModel).environment(feedWindow).environment(downloader)
    }

    // the feed in Compact density
    let (compactModel, _) = await makeModel(replies: ["inbox.list": inboxWithNotes()])
    compactModel.viewOptions.density = .compact
    try await render("feed-compact", size: CGSize(width: 600, height: 560)) {
        AppWindowView().environment(compactModel).environment(WindowState()).environment(downloader)
    }

    // Settings → View
    let (settingsModel, _) = await makeModel()
    try await render("settings-view", size: CGSize(width: 520, height: 470), title: "View") {
        SettingsPaneView(.view).environment(settingsModel).frame(width: 480).padding(20)
    }

    // Settings → Advanced
    let (advancedModel, _) = await makeModel()
    try await render("settings-advanced", size: CGSize(width: 520, height: 420), title: "Advanced") {
        SettingsPaneView(.advanced).environment(advancedModel).frame(width: 480).padding(20)
    }

    // a target with no updates, filtered in Following
    let (quietModel, _) = await makeModel()
    if let quiet = quietModel.sources.targets.first(where: { target in
        !target.starred && !quietModel.inbox.items.contains { $0.targetKey == target.id }
    }) {
        quietModel.feed.showOnly(quiet.ref)
    }
    try await render("feed-quiet-target", size: CGSize(width: 600, height: 360)) {
        AppWindowView().environment(quietModel).environment(WindowState()).environment(downloader)
    }

    // Following
    let followingWindow = WindowState()
    followingWindow.mode = .following
    let (followingModel, _) = await makeModel()
    try await render("following", size: CGSize(width: 600, height: 640)) {
        AppWindowView().environment(followingModel).environment(followingWindow).environment(downloader)
    }

    // the Filters sheet
    let (filtersModel, _) = await makeModel()
    if let own = filtersModel.sources.selfTarget {
        try await render("filters-sheet", size: CGSize(width: 440, height: 520), toolbar: false) {
            FiltersSheet(target: own).environment(filtersModel).environment(downloader)
                .background(Color(nsColor: .windowBackgroundColor))
        }
    }

    // the Add popover's contents, with a preview
    let (addModel, _) = await makeModel()
    addModel.add.fill("https://github.com/python/cpython")
    try await Task.sleep(for: .milliseconds(300))
    try await render("add-popover", size: CGSize(width: 360, height: 330), toolbar: false) {
        AddPanel().environment(addModel).environment(WindowState()).environment(downloader)
            .background(Color(nsColor: .windowBackgroundColor))
    }

    // the Add popover after an ambiguous name, with npm picked
    let ambiguous: [String: Any] = ["ok": false, "error": [
        "code": "ambiguous", "message": "`express` could be homebrew or npm or pypi", "choices": ["homebrew", "npm", "pypi"],
    ]]
    let (choiceModel, choiceEngine) = await makeModel(replies: ["targets.preview": ambiguous])
    choiceModel.add.fill("express")
    try await Task.sleep(for: .milliseconds(300))
    choiceEngine.answer("targets.preview", with: fixtureReply("targets.preview/picked"))
    await choiceModel.add.pick("npm")
    try await render("add-ambiguous", size: CGSize(width: 360, height: 360), toolbar: false) {
        AddPanel().environment(choiceModel).environment(WindowState()).environment(downloader)
            .background(Color(nsColor: .windowBackgroundColor))
    }

    // first launch: nothing followed but Youpdated itself
    var targets = fixtureReply("targets.list/ok")
    targets["targets"] = Array((targets["targets"] as! [[String: Any]]).prefix(1))
    let (firstModel, _) = await makeModel(replies: [
        "targets.list": targets,
        "inbox.list": ["ok": true, "items": [], "cursor": NSNull(), "total": 0, "unread": 0],
        "problems.list": ["ok": true, "problems": [], "checked_at": NSNull()],
    ])
    try await render("first-launch", size: CGSize(width: 600, height: 640)) {
        AppWindowView().environment(firstModel).environment(WindowState()).environment(downloader)
    }

    // locked
    let locked = fixtureReply("status/locked")
    let (lockedModel, _) = await makeModel(replies: ["status": locked])
    try await render("locked", size: CGSize(width: 600, height: 560)) {
        AppWindowView().environment(lockedModel).environment(WindowState()).environment(downloader)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
Task { @MainActor in
    do {
        try await run()
        exit(0)
    } catch {
        FileHandle.standardError.write(Data("\(error)\n".utf8))
        exit(1)
    }
}
app.run()
