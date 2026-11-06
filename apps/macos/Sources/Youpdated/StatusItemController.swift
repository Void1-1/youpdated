import AppKit
import SwiftUI
import YoupdatedKit
import YoupdatedUI

/// The menu bar item (AppKit `NSStatusItem` not `MenuBarExtra`)
@MainActor
final class StatusItemController: NSObject, NSMenuDelegate, NSWindowDelegate {
    private let model: AppModel
    private let router: Router
    private let downloader: Downloader
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let menu = NSMenu()

    static let shownRows = 5

    init(model: AppModel, router: Router, downloader: Downloader) {
        self.model = model
        self.router = router
        self.downloader = downloader
        super.init()
        item.behavior = .removalAllowed
        item.autosaveName = "Youpdated"
        menu.delegate = self
        menu.autoenablesItems = false
        item.menu = menu
        if let button = item.button {
            // SwiftUI's window actions, for Router; it lives as long as the menu bar item
            let bridge = NSHostingView(rootView: SceneBridge(router: router))
            bridge.frame = NSRect(x: 0, y: 0, width: 1, height: 1)
            button.addSubview(bridge)
            button.window?.registerForDraggedTypes([.URL, .string])
            button.window?.delegate = self
        }
        observe()
    }

    private func observe() {
        withObservationTracking {
            render()
        } onChange: { [weak self] in
            Task { @MainActor in self?.observe() }
        }
    }

    private func render() {
        guard let button = item.button else { return }
        let state = model.menuBarState
        let unread = model.unread
        button.image = NSImage(systemSymbolName: state.symbolName(unread: unread), accessibilityDescription: state.accessibilityLabel)
            .map(Self.centered)
        let count = unread > 0 && state != .locked ? "\(unread)" : ""
        button.imagePosition = count.isEmpty ? .imageOnly : .imageLeading
        button.attributedTitle = NSAttributedString(string: count, attributes: [
            .font: NSFont.monospacedDigitSystemFont(ofSize: NSFont.menuBarFont(ofSize: 0).pointSize, weight: .medium),
        ])
        button.toolTip = headline
        button.setAccessibilityLabel(unread > 0 ? "\(state.accessibilityLabel), \(Wording.newUpdates(unread))" : state.accessibilityLabel)
    }

    static func centered(_ symbol: NSImage) -> NSImage {
        let size = symbol.size
        let scale: CGFloat = 2
        guard let bitmap = NSBitmapImageRep(
            bitmapDataPlanes: nil, pixelsWide: Int(size.width * scale), pixelsHigh: Int(size.height * scale),
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { return symbol }
        bitmap.size = size
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        symbol.draw(in: NSRect(origin: .zero, size: size))
        NSGraphicsContext.restoreGraphicsState()
        // rows count down from the top
        let inked = (0..<bitmap.pixelsHigh).filter { y in
            (0..<bitmap.pixelsWide).contains { x in (bitmap.colorAt(x: x, y: y)?.alphaComponent ?? 0) > 0.1 }
        }
        guard let top = inked.first, let bottom = inked.last else { return symbol }
        let lower = size.height / 2 - CGFloat(top + bottom + 1) / 2 / scale
        let image = NSImage(size: size, flipped: false) { rect in
            symbol.draw(in: rect.offsetBy(dx: 0, dy: -lower))
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = symbol.accessibilityDescription
        return image
    }

    private var headline: String {
        if model.needsPassphrase || model.status?.locked == true {
            return String(localized: "Locked: enter the passphrase to check")
        }
        if model.isChecking, let progress = model.progress, progress.of > 0 {
            return String(localized: "Checking… \(progress.done) of \(progress.of)")
        }
        if model.isChecking {
            return String(localized: "Checking…")
        }
        if case .failed(let message) = model.engineState {
            return message
        }
        switch model.pause {
        case .indefinitely:
            return String(localized: "Paused")
        case .until(let end) where end > Date():
            return String(localized: "Paused until \(end.formatted(date: .omitted, time: .shortened))")
        default:
            break
        }
        return model.unread == 0 ? String(localized: "No new updates") : Wording.newUpdates(model.unread)
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let locked = model.needsPassphrase || model.status?.locked == true

        let header = NSMenuItem(title: headline, action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)

        if !locked {
            let rows = model.recentUnread(limit: Self.shownRows)
            for row in rows {
                menu.addItem(rowItem(row))
            }
            let more = model.unread - rows.reduce(0) { $0 + $1.items.count }
            if more > 0 {
                menu.addItem(action(String(localized: "and \(more) more…"), #selector(openInbox)))
            }
        }

        menu.addItem(.separator())
        if locked {
            menu.addItem(action(String(localized: "Unlock Youpdated…"), #selector(unlock)))
        }
        menu.addItem(action(String(localized: "Open Youpdated…"), #selector(openInbox)))
        if model.hasProblems && !locked {
            menu.addItem(action(String(localized: "Show Problems…"), #selector(openProblems)))
        }
        let check = action(String(localized: "Check Now"), #selector(checkNow))
        check.isEnabled = !model.isChecking && !locked && model.engine != nil
        menu.addItem(check)

        if model.pause.isActive(at: Date()) {
            menu.addItem(action(String(localized: "Resume Checks"), #selector(resume)))
        } else {
            let pause = NSMenuItem(title: String(localized: "Pause"), action: nil, keyEquivalent: "")
            let options = NSMenu()
            for (option, title) in [
                (PauseOption.oneHour, String(localized: "1 Hour")),
                (.untilTomorrow, String(localized: "Until Tomorrow")),
                (.untilResumed, String(localized: "Until Resumed")),
            ] {
                let entry = action(title, #selector(pause(_:)))
                entry.representedObject = option.rawValue
                options.addItem(entry)
            }
            pause.submenu = options
            menu.addItem(pause)
        }

        menu.addItem(.separator())
        if let update = model.appUpdate {
            let title: String
            if case .downloading = downloader.state {
                title = String(localized: "Downloading Youpdated \(update.version)…")
            } else {
                title = Wording.appUpdate(update.version) + "…"
            }
            let entry = action(title, #selector(downloadUpdate))
            entry.image = NSImage(systemSymbolName: "arrow.down.circle", accessibilityDescription: nil)
            menu.addItem(entry)
        }
        let settings = action(String(localized: "Settings…"), #selector(openSettings))
        settings.keyEquivalent = ","
        menu.addItem(settings)
        let quit = action(String(localized: "Quit Youpdated"), #selector(quit))
        quit.keyEquivalent = "q"
        quit.toolTip = String(localized: "Checks stop while Youpdated is closed.")
        menu.addItem(quit)
    }

    private func rowItem(_ row: InboxRow) -> NSMenuItem {
        let entry = action(row.target.display, #selector(openRow(_:)))
        let title = NSMutableAttributedString(string: row.target.display + "   ")
        let detail = row.items.count > 1 ? "\(row.newest.title)  +\(row.items.count - 1)" : row.newest.title
        title.append(NSAttributedString(string: detail, attributes: [.foregroundColor: NSColor.secondaryLabelColor]))
        entry.attributedTitle = title
        entry.image = NSImage(
            systemSymbolName: row.starred ? "star.fill" : SourceStyle.symbolName(row.target.source),
            accessibilityDescription: SourceLabel.label(source: row.target.source, key: row.target.key))
        entry.representedObject = row
        return entry
    }

    private func action(_ title: String, _ selector: Selector) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        entry.target = self
        return entry
    }

    @objc private func openRow(_ sender: NSMenuItem) {
        guard let row = sender.representedObject as? InboxRow else { return }
        if row.starred, model.appUpdate?.itemId == row.newest.id {
            downloadUpdate()
            return
        }
        Task {
            if let url = await model.inbox.open(row) {
                NSWorkspace.shared.open(url)
            }
        }
    }

    @objc private func openInbox() { router.show(.updates) }
    @objc private func openProblems() { router.showProblems() }
    @objc private func unlock() { router.show(.updates) }
    @objc private func openSettings() { router.showSettings() }
    @objc private func checkNow() { Task { await model.checkNow() } }
    @objc private func resume() { Task { await model.resume() } }
    @objc private func quit() { NSApp.terminate(nil) }

    @objc private func pause(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String, let option = PauseOption(rawValue: raw) else { return }
        model.pause(option)
    }

    @objc private func downloadUpdate() {
        (NSApp.delegate as? AppDelegate)?.downloadUpdate()
    }

    func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        value(from: sender) == nil ? [] : .link
    }

    func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        value(from: sender) == nil ? [] : .link
    }

    func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        guard let value = value(from: sender) else { return false }
        (NSApp.delegate as? AppDelegate)?.add(value)
        return true
    }

    private func value(from info: NSDraggingInfo) -> String? {
        let pasteboard = info.draggingPasteboard
        if let url = (pasteboard.readObjects(forClasses: [NSURL.self]) as? [URL])?.first, !url.isFileURL {
            return url.absoluteString
        }
        return pasteboard.string(forType: .string).flatMap(DeepLink.watchValue(from:))
    }
}
