import Foundation
import Observation

/// Feed state: expanded and selected cards, read-on-expand, filters
@MainActor
@Observable
public final class FeedModel {
    public private(set) var expandedID: FeedCard.ID?
    public var selectedID: FeedCard.ID?
    /// The selection ring shows only while moving with the keyboard
    public var showsSelection = false
    /// Following's selected row: Updates shows only that target
    public var targetFilter: TargetRef?
    /// The window's filter field, which Updates and Following both apply
    public var filter = ""
    /// Items read while the window was open: they keep their place until `settle()`
    public private(set) var pinned: Set<String> = []

    weak var app: AppModel?
    @ObservationIgnored var readDelay: Duration = .milliseconds(700)
    @ObservationIgnored var settleDelay: Duration = .milliseconds(450)
    @ObservationIgnored private var reading: Task<Void, Never>?

    init() {}

    public var layout: FeedLayout {
        guard let app else { return Feed.build(items: [], now: Date()) }
        return Feed.build(
            items: app.inbox.items, options: app.viewOptions, source: app.inbox.sourceFilter,
            target: targetFilter?.targetKey, text: filter, pinned: pinned, update: app.appUpdate,
            status: FeedStatus(lastCheck: app.lastCheck, checking: app.isChecking, progress: app.progress, pause: app.pause),
            now: app.currentDate, calendar: app.calendar)
    }

    public func toggle(_ card: FeedCard) {
        if expandedID == card.id { collapse() } else { expand(card) }
    }

    /// Grows the card in place and marks its updates read, after a moment so the dot doesn't
    /// flicker while you're still reading
    public func expand(_ card: FeedCard) {
        expandedID = card.id
        selectedID = card.id
        let ids = card.unreadIds
        guard !ids.isEmpty else { return }
        pinned.formUnion(card.items.map(\.id))
        reading?.cancel()
        reading = Task { [weak self, readDelay] in
            try? await Task.sleep(for: readDelay)
            await self?.app?.inbox.mark(true, ids: ids)
        }
    }

    public func collapse() {
        expandedID = nil
    }

    /// Opening a link marks the card read, like expanding it
    @discardableResult
    public func open(_ card: FeedCard, item: InboxItem? = nil) async -> URL? {
        let item = item ?? card.newest
        pinned.formUnion(card.items.map(\.id))
        await app?.inbox.mark(true, ids: item.id == card.newest.id ? card.unreadIds : [item.id])
        guard let url = URL(string: item.url) else { return nil }
        app?.platform.open(url)
        return url
    }

    public func setRead(_ read: Bool, _ card: FeedCard) async {
        pinned.formUnion(card.items.map(\.id))
        await app?.inbox.mark(read, ids: card.items.filter { $0.read != read }.map(\.id))
    }

    public func markAllRead() async {
        guard let app else { return }
        pinned.formUnion(app.inbox.items.filter { !$0.read }.map(\.id))
        await app.inbox.markAllRead()
        try? await Task.sleep(for: settleDelay)
        settle()
    }

    /// Read cards go to Earlier: after a check, when the window opens, and after Mark all read
    public func settle() {
        pinned.removeAll()
    }

    public func delete(_ card: FeedCard) async {
        if expandedID == card.id { expandedID = nil }
        await app?.inbox.delete(ids: card.items.map(\.id))
    }

    public func snooze(_ card: FeedCard) async {
        await app?.snooze(card.unreadIds.isEmpty ? [card.newest.id] : card.unreadIds)
    }

    public func move(by offset: Int) {
        let cards = layout.cards
        guard !cards.isEmpty else { return }
        let index = cards.firstIndex { $0.id == selectedID }
        let next = index.map { min(max($0 + offset, 0), cards.count - 1) } ?? (offset > 0 ? 0 : cards.count - 1)
        selectedID = cards[next].id
        showsSelection = true
    }

    public var selectedCard: FeedCard? {
        layout.cards.first { $0.id == selectedID }
    }

    public func showOnly(_ target: TargetRef?) {
        targetFilter = target
        expandedID = nil
    }
}

/// The + popover: paste or type, a preview as you go, then Follow
@MainActor
@Observable
public final class AddModel {
    public var text = "" {
        didSet { if text != oldValue { textChanged() } }
    }
    public var priority: Priority = .normal
    public private(set) var choices: [String] = []
    public private(set) var chosen: String?

    weak var app: AppModel?
    @ObservationIgnored var debounce: Duration = .milliseconds(450)
    @ObservationIgnored private var pending: Task<Void, Never>?

    init() {}

    public var flow: AddFlow { app?.sources.add ?? .idle }

    public var preview: PreviewReply? {
        switch flow {
        case .ready(_, let preview), .adding(_, let preview): preview
        default: nil
        }
    }

    public var canFollow: Bool {
        guard case .ready(_, let preview) = flow else { return false }
        return !preview.alreadyWatching
    }

    public var isLooking: Bool {
        if pending != nil { return true }
        if case .previewing = flow { return true }
        return false
    }

    /// Opened from cmd + V, a drop, or a link: preview immediately
    public func fill(_ value: String) {
        pending?.cancel()
        pending = nil
        choices = []
        chosen = nil
        withoutDebounce { text = value }
        Task { await lookUp(value) }
    }

    public func pick(_ source: String) async {
        chosen = source
        await app?.sources.preview(text, source: source)
    }

    /// Adds it at the chosen priority
    public func follow() async {
        guard canFollow else { return }
        await app?.sources.confirmAdd(priority: priority)
    }

    public func reset() {
        pending?.cancel()
        pending = nil
        choices = []
        chosen = nil
        priority = .normal
        withoutDebounce { text = "" }
        app?.sources.resetAdd()
    }

    @ObservationIgnored private var quiet = false

    private func withoutDebounce(_ change: () -> Void) {
        quiet = true
        change()
        quiet = false
    }

    private func textChanged() {
        guard !quiet else { return }
        pending?.cancel()
        choices = []
        chosen = nil
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            pending = nil
            app?.sources.resetAdd()
            return
        }
        pending = Task { [weak self, debounce] in
            try? await Task.sleep(for: debounce)
            guard !Task.isCancelled else { return }
            self?.pending = nil
            await self?.lookUp(value)
        }
    }

    private func lookUp(_ value: String) async {
        await app?.sources.preview(value)
        if case .choosing(_, let choices) = flow {
            self.choices = choices
        }
    }
}

/// Icons fetched by the engine when turned on and the engine has `icon.get`.
@MainActor
@Observable
public final class IconStore {
    public private(set) var images: [TargetKey: Data] = [:]
    @ObservationIgnored private var asked: Set<TargetKey> = []

    weak var app: AppModel?

    init() {}

    public var isAvailable: Bool { app?.hello?.ops.contains("icon.get") == true }
    public var isEnabled: Bool { isAvailable && app?.settings.showIcons == true }

    /// Called from a view's `.task(id:)`
    public func load(_ key: TargetKey) async {
        guard isEnabled, !asked.contains(key), let engine = app?.engine else { return }
        asked.insert(key)
        if let image = try? await engine.icon(source: key.source, key: key.key) {
            images[key] = image.data
        }
    }

    /// After a check the engine may have fetched icons it didn't have
    func forgetMisses() {
        asked = Set(images.keys)
    }

    /// The `icon` event: the engine just fetched these, after the check replied
    func arrived(_ keys: [TargetKey]) async {
        for key in keys {
            asked.remove(key)
            await load(key)
        }
    }

    /// Turning icons off
    func clear() {
        images.removeAll()
        asked.removeAll()
    }
}
