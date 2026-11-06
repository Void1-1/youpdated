import Foundation

public enum MenuBarState: Hashable, Sendable {
    case idle, checking, locked, paused, problems

    public func symbolName(unread: Int) -> String {
        switch self {
        case .idle: unread > 0 ? "bell.badge" : "bell"
        case .checking: "arrow.triangle.2.circlepath"
        case .locked: "lock"
        case .paused: "bell.slash"
        case .problems: "exclamationmark.triangle"
        }
    }

    public var accessibilityLabel: String {
        switch self {
        case .idle: String(localized: "Youpdated")
        case .checking: String(localized: "Youpdated, checking")
        case .locked: String(localized: "Youpdated, locked")
        case .paused: String(localized: "Youpdated, paused")
        case .problems: String(localized: "Youpdated, problems")
        }
    }
}

/// How each source is drawn
public enum SourceStyle {
    public static func symbolName(_ source: String) -> String {
        switch source {
        case "git": "chevron.left.forwardslash.chevron.right"
        case "steam", "itch": "gamecontroller"
        case "npm", "pypi": "shippingbox"
        case "homebrew": "mug"
        case "youtube": "play.rectangle"
        case "feed": "dot.radiowaves.up.forward"
        case "browser": "globe"
        default: "link"
        }
    }

    /// The source's label with no key
    public static func name(_ source: String) -> String {
        SourceLabel.label(source: source, key: nil)
    }
}

/// One name per source and forge
public enum SourceLabel {
    public static func label(source: String, key: String?) -> String {
        switch source {
        case "git":
            guard let host = key.flatMap(host(of:)) else { return "Git" }
            switch host {
            case "github.com": return "GitHub"
            case "gitlab.com": return "GitLab"
            case "codeberg.org": return "Codeberg"
            default: return host
            }
        case "steam": return "Steam"
        case "itch": return "itch.io"
        case "npm": return "npm"
        case "pypi": return "PyPI"
        case "homebrew": return "Homebrew"
        case "youtube": return "YouTube"
        case "feed": return key.flatMap(host(of:)) ?? String(localized: "Feed")
        case "browser": return String(localized: "Browsers")
        default: return source
        }
    }

    /// "github.com/python/cpython" or "https://blog.example.com/feed.xml" to the host
    static func host(of key: String) -> String? {
        if key.contains("://") {
            return URL(string: key)?.host()?.lowercased()
        }
        return key.split(separator: "/").first.map { $0.lowercased() }
    }
}

public enum PriorityStyle {
    public static func symbolName(_ priority: Priority) -> String {
        switch priority {
        case .high: "bell.and.waves.left.and.right"
        case .normal: "bell"
        case .low: "bell.slash"
        }
    }

    public static func label(_ priority: Priority) -> String {
        switch priority {
        case .high: String(localized: "Notify immediately")
        case .normal: String(localized: "Notify")
        case .low: String(localized: "Inbox only")
        }
    }

    /// "High: Notify immediately"
    public static func menuTitle(_ priority: Priority) -> String {
        "\(Wording.priority(priority)): \(label(priority))"
    }
}

public enum Wording {
    /// "1 new update", "3 new updates"
    public static func newUpdates(_ count: Int) -> String {
        let attributed = AttributedString(localized: "^[\(count) new update](inflect: true)")
        return String(attributed.characters)
    }

    public static func updates(_ count: Int) -> String {
        let attributed = AttributedString(localized: "^[\(count) update](inflect: true)")
        return String(attributed.characters)
    }

    public static func appUpdate(_ version: String) -> String {
        String(localized: "Youpdated \(version) is available")
    }

    public static func priority(_ priority: Priority) -> String {
        switch priority {
        case .high: String(localized: "High")
        case .normal: String(localized: "Normal")
        case .low: String(localized: "Low")
        }
    }
}

/// a notification, ready to post
public struct NotificationContent: Hashable, Sendable {
    public var identifier: String
    public var title: String
    public var subtitle: String?
    public var body: String
    public var threadIdentifier: String
    public var itemIds: [String]
    public var url: URL?
    public var isAppUpdate: Bool

    public init(
        identifier: String, title: String, subtitle: String?, body: String, threadIdentifier: String,
        itemIds: [String], url: URL?, isAppUpdate: Bool
    ) {
        self.identifier = identifier
        self.title = title
        self.subtitle = subtitle
        self.body = body
        self.threadIdentifier = threadIdentifier
        self.itemIds = itemIds
        self.url = url
        self.isAppUpdate = isAppUpdate
    }
}

/// Turns the engine's alerts into notification text
public enum AlertComposer {
    public static let shownLines = 3

    public static func compose(_ alerts: [Alert], items: [InboxItem], currentVersion: String) -> [NotificationContent] {
        let byId = Dictionary(items.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        var contents: [NotificationContent] = []
        for alert in alerts {
            let found = alert.itemIds.compactMap { byId[$0] }
            let (own, rest) = (found.filter(\.starred), found.filter { !$0.starred })
            for item in own {
                if let update = AppUpdate.update(from: item),
                   AppUpdate.compare(update.version, currentVersion) == .orderedDescending {
                    contents.append(NotificationContent(
                        identifier: "update-\(item.id)", title: Wording.appUpdate(update.version), subtitle: nil,
                        body: String(localized: "Download it from the Youpdated menu."),
                        threadIdentifier: "youpdated", itemIds: [item.id], url: URL(string: item.url),
                        isAppUpdate: true))
                } else {
                    contents.append(single(item, thread: item.source))
                }
            }
            guard !rest.isEmpty else { continue }
            if alert.priority == .high || rest.count == 1 || Set(rest.map(\.targetKey)).count == 1 {
                contents.append(rest.count == 1 ? single(rest[0], thread: rest[0].source) : sameTarget(rest))
            } else {
                contents.append(combined(rest))
            }
        }
        return contents
    }

    static func single(_ item: InboxItem, thread: String) -> NotificationContent {
        NotificationContent(
            identifier: "item-\(item.id)", title: item.display, subtitle: nil, body: item.title,
            threadIdentifier: thread, itemIds: [item.id], url: URL(string: item.url), isAppUpdate: false)
    }

    static func sameTarget(_ items: [InboxItem]) -> NotificationContent {
        let first = items[0]
        let titles = items.prefix(shownLines).map(\.title).joined(separator: ", ")
        let more = items.count > shownLines ? String(localized: ", and \(items.count - shownLines) more") : ""
        return NotificationContent(
            identifier: "target-\(first.source)-\(first.target)-\(first.id)", title: first.display,
            subtitle: Wording.newUpdates(items.count), body: titles + more,
            threadIdentifier: first.source, itemIds: items.map(\.id), url: URL(string: first.url),
            isAppUpdate: false)
    }

    static func combined(_ items: [InboxItem]) -> NotificationContent {
        var lines = items.prefix(shownLines).map { "\($0.display): \($0.title)" }
        if items.count > shownLines {
            lines.append(String(localized: "and \(items.count - shownLines) more"))
        }
        let sources = Set(items.map(\.source))
        return NotificationContent(
            identifier: "check-\(items[0].id)", title: Wording.newUpdates(items.count), subtitle: nil,
            body: lines.joined(separator: "\n"),
            threadIdentifier: sources.count == 1 ? items[0].source : "updates",
            itemIds: items.map(\.id), url: nil, isAppUpdate: false)
    }
}

/// A problem the app ran into, not an engine one
public struct AppProblem: Identifiable, Hashable, Sendable {
    public var id = UUID()
    public var title: String
    public var detail: String
    public var date: Date

    public init(title: String, detail: String, date: Date = Date()) {
        self.title = title
        self.detail = detail
        self.date = date
    }
}
