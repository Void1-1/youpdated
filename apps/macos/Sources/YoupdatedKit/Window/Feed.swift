import Foundation

public enum FeedSectionKind: Hashable, Sendable {
    case today, yesterday, thisWeek, earlier
    case priority(Priority)
    /// A source's label
    case source(String)
    /// Group by None: one untitled section
    case all

    public var title: String {
        switch self {
        case .today: String(localized: "Today")
        case .yesterday: String(localized: "Yesterday")
        case .thisWeek: String(localized: "This week")
        case .earlier: String(localized: "Earlier")
        case .priority(let priority): Wording.priority(priority)
        case .source(let label): label
        case .all: ""
        }
    }

    var id: String {
        switch self {
        case .today: "today"
        case .yesterday: "yesterday"
        case .thisWeek: "week"
        case .earlier: "earlier"
        case .priority(let priority): "priority-\(priority.rawValue)"
        case .source(let label): "source-\(label)"
        case .all: "all"
        }
    }
}

/// A target's updates in a section; newest leading
public struct FeedCard: Identifiable, Hashable, Sendable {
    public var section: FeedSectionKind
    public var target: TargetRef
    /// Newest first
    public var items: [InboxItem]
    /// Read and settled into Earlier: dimmed
    public var isHistory: Bool
    /// One line: low priority, or every card under Compact density
    public var isCompact: Bool
    public var showsPreview: Bool = true

    public var id: String { "\(section.id)|\(target.source)|\(target.key)" }
    public var newest: InboxItem { items[0] }
    public var priority: Priority { newest.priority }
    public var starred: Bool { newest.starred }
    public var label: String { SourceLabel.label(source: target.source, key: target.key) }
    public var unreadIds: [String] { items.filter { !$0.read }.map(\.id) }
    public var isUnread: Bool { items.contains { !$0.read } }
    /// High priority and unread
    public var hasEdge: Bool { priority == .high && isUnread }
    public var moreCount: Int { items.count - 1 }
    public var date: Date { newest.date }
    public var lead: String { Feed.lead(newest).text }
    public var leadIsVersion: Bool { Feed.lead(newest).isVersion }
    /// Under the lead: the title when the version led, else notes' first line
    public var line: String? {
        if leadIsVersion, Feed.plainVersion(newest.title) != lead { return newest.title }
        return showsPreview ? Notes.firstLine(newest.body) : nil
    }
}

public struct FeedSection: Identifiable, Hashable, Sendable {
    public var kind: FeedSectionKind
    public var cards: [FeedCard]

    public var id: String { kind.id }
    public var title: String { kind.title }
}

/// "3 new updates", and a quieter line: "Since this morning · Checked 9:41"
public struct FeedHeading: Hashable, Sendable {
    public enum Part: Hashable, Sendable {
        case text(String)
        /// "Paused until 15:00"
        case paused(String)
    }

    public var title: String
    public var parts: [Part]
    public var upToDate: Bool

    /// The line as text, for accessibility and testing
    public var line: String {
        parts.map { part in
            switch part {
            case .text(let text), .paused(let text): text
            }
        }.joined(separator: " · ")
    }
}

public struct FeedLayout: Hashable, Sendable {
    public var heading: FeedHeading
    /// Pinned above the sections when an app update is unread
    public var update: AppUpdate?
    public var updateItem: InboxItem?
    public var sections: [FeedSection]

    /// Every card in reading order
    public var cards: [FeedCard] { sections.flatMap(\.cards) }
    public var isEmpty: Bool { sections.isEmpty && update == nil }
}

public struct FeedStatus: Hashable, Sendable {
    public var lastCheck: Date?
    public var checking = false
    public var progress: ProgressEvent?
    public var pause: Pause = .none

    public init(lastCheck: Date? = nil, checking: Bool = false, progress: ProgressEvent? = nil, pause: Pause = .none) {
        self.lastCheck = lastCheck
        self.checking = checking
        self.progress = progress
        self.pause = pause
    }
}

public enum Feed {
    /// Where cards go, and the headings for them.
    /// `pinned`: items read while the window was open keep their place until `settle`
    public static func build(
        items: [InboxItem], options: ViewOptions = .standard, source: String? = nil, target: TargetKey? = nil,
        text: String = "", pinned: Set<String> = [], update: AppUpdate? = nil, status: FeedStatus = FeedStatus(),
        now: Date, calendar: Calendar = .current
    ) -> FeedLayout {
        var shown = items
        // `source` is a label, as Filter by Source lists them
        if let source { shown = shown.filter { $0.label == source } }
        if let target {
            shown = shown.filter { $0.targetKey == target }
        } else if !options.showsLowPriority {
            // hidden from the feed, but not from Following or a filter to their target
            shown = shown.filter { $0.priority != .low }
        }
        // the heading counts what the filter field hides
        let unfiltered = shown
        let needle = text.trimmingCharacters(in: .whitespaces).lowercased()
        if !needle.isEmpty {
            shown = shown.filter { item in [item.display, item.target, item.label].contains { $0.lowercased().contains(needle) } }
        }

        var updateItem: InboxItem?
        var pinnedUpdate: AppUpdate?
        if let update, let item = shown.first(where: { $0.id == update.itemId }), !item.read || pinned.contains(item.id) {
            updateItem = item
            pinnedUpdate = update
            shown.removeAll { $0.id == item.id }
        }

        // placed with the unread: unread, or read while the window was open
        let placed = shown.filter { !$0.read || pinned.contains($0.id) }
        var history = shown.filter { $0.read && !pinned.contains($0.id) }
        switch options.readUpdates {
            case .none: history = []
            case .all: break
            case .week, .month:
                let cutoff = options.readUpdates.cutoff(now: now) ?? .distantPast
                history = history.filter { $0.foundAt >= cutoff }
        }

        var buckets: [FeedSectionKind: [InboxItem]] = [:]
        for item in placed {
            buckets[section(for: item, grouping: options.grouping, now: now, calendar: calendar), default: []].append(item)
        }
        var sections: [FeedSection] = order(Array(buckets.keys)).map { kind in
            FeedSection(kind: kind, cards: cards(buckets[kind] ?? [], in: kind, history: [], options: options))
        }
        if !history.isEmpty && options.grouping != .day && options.groupsReadUpdates {
            var read: [FeedSectionKind: [InboxItem]] = [:]
            for item in history {
                read[section(for: item, grouping: options.grouping, now: now, calendar: calendar), default: []].append(item)
            }
            sections = order(Array(Set(buckets.keys).union(read.keys))).map { kind in
                FeedSection(kind: kind, cards: cards(buckets[kind] ?? [], in: kind, history: read[kind] ?? [], options: options))
            }
        } else if !history.isEmpty {
            let earlier = sections.firstIndex { $0.kind == .earlier }
            let unreadEarlier = earlier.map { sections[$0].cards.flatMap(\.items) } ?? []
            let merged = cards(unreadEarlier, in: .earlier, history: history, options: options)
            if let earlier {
                sections[earlier].cards = merged
            } else {
                sections.append(FeedSection(kind: .earlier, cards: merged))
            }
        }

        let unread = unfiltered.filter { !$0.read }
        return FeedLayout(
            heading: heading(unread: unread, status: status, now: now, calendar: calendar),
            update: pinnedUpdate, updateItem: updateItem, sections: sections)
    }

    public static func heading(unread: [InboxItem], status: FeedStatus, now: Date, calendar: Calendar = .current) -> FeedHeading {
        var parts: [FeedHeading.Part] = []
        if let oldest = unread.map(\.foundAt).min() {
            parts.append(.text(since(oldest, now: now, calendar: calendar)))
        }
        if status.checking {
            if let progress = status.progress, progress.of > 0 {
                parts.append(.text(String(localized: "Checking… \(progress.done) of \(progress.of)")))
            } else {
                parts.append(.text(String(localized: "Checking…")))
            }
        } else if let last = status.lastCheck {
            parts.append(.text(checked(last, now: now, calendar: calendar)))
        }
        switch status.pause.settled(at: now) {
            case .until(let end):
                parts.append(.paused(String(localized: "Paused until \(clock(end, calendar: calendar))")))
            case .indefinitely:
                parts.append(.paused(String(localized: "Paused")))
            case .none:
                break
        }
        let title = unread.isEmpty ? String(localized: "You're up to date") : Wording.newUpdates(unread.count)
        return FeedHeading(title: title, parts: parts, upToDate: unread.isEmpty)
    }

    static func section(for item: InboxItem, grouping: ViewOptions.Grouping, now: Date, calendar: Calendar) -> FeedSectionKind {
        switch grouping {
        case .priority: return .priority(item.priority)
        case .source: return .source(SourceLabel.label(source: item.source, key: item.target))
        case .none: return .all
        case .day: break
        }
        let found = item.foundAt
        if calendar.isDate(found, inSameDayAs: now) { return .today }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
           calendar.isDate(found, inSameDayAs: yesterday) { return .yesterday }
        if let weekStart = calendar.date(byAdding: .day, value: -6, to: calendar.startOfDay(for: now)), found >= weekStart {
            return .thisWeek
        }
        return .earlier
    }

    static func order(_ kinds: [FeedSectionKind]) -> [FeedSectionKind] {
        func rank(_ kind: FeedSectionKind) -> (Int, String) {
            switch kind {
            case .today: (0, "")
            case .yesterday: (1, "")
            case .thisWeek: (2, "")
            case .priority(let priority): (priority.rank, "")
            case .source(let label): (0, label.lowercased())
            case .all: (0, "")
            case .earlier: (9, "")
            }
        }
        return kinds.sorted { rank($0) < rank($1) }
    }

    /// One card per target; unread first, read history, newest first
    static func cards(_ placed: [InboxItem], in kind: FeedSectionKind, history: [InboxItem], options: ViewOptions) -> [FeedCard] {
        let grouped = Dictionary(grouping: placed + history, by: \.targetKey)
        let historyIds = Set(history.map(\.id))
        let cards = grouped.values.map { group -> FeedCard in
            let items = group.sorted { $0.date > $1.date }
            let isHistory = items.allSatisfy { historyIds.contains($0.id) }
            return FeedCard(
                section: kind, target: items[0].targetRef, items: items, isHistory: isHistory,
                isCompact: options.density == .compact || items[0].priority == .low,
                showsPreview: options.previews)
        }
        return cards.sorted { a, b in
            if a.isHistory != b.isHistory { return !a.isHistory }
            if a.date != b.date { return a.date > b.date }
            return a.id < b.id
        }
    }

    /// "Since this morning", "Since yesterday", "Since Tuesday", "Since Oct 12"
    public static func since(_ date: Date, now: Date, calendar: Calendar = .current) -> String {
        if calendar.isDate(date, inSameDayAs: now) {
            switch calendar.component(.hour, from: date) {
            case ..<12: return String(localized: "Since this morning")
            case 12..<18: return String(localized: "Since this afternoon")
            default: return String(localized: "Since this evening")
            }
        }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now), calendar.isDate(date, inSameDayAs: yesterday) {
            return String(localized: "Since yesterday")
        }
        if let weekStart = calendar.date(byAdding: .day, value: -6, to: calendar.startOfDay(for: now)), date >= weekStart {
            return String(localized: "Since \(format(date, calendar: calendar, style: Date.FormatStyle(date: .omitted, time: .omitted).weekday(.wide)))")
        }
        return String(localized: "Since \(day(date, now: now, calendar: calendar))")
    }

    /// "Checked 9:41", or "Checked Oct 28" on another day
    public static func checked(_ date: Date, now: Date, calendar: Calendar = .current) -> String {
        calendar.isDate(date, inSameDayAs: now)
            ? String(localized: "Checked \(clock(date, calendar: calendar))")
            : String(localized: "Checked \(day(date, now: now, calendar: calendar))")
    }

    public static func time(_ date: Date, times: ViewOptions.Times, now: Date, calendar: Calendar = .current) -> String {
        switch times {
        case .relative:
            return RelativeTime.short(date, now: now, calendar: calendar)
        case .absolute:
            return calendar.isDate(date, inSameDayAs: now)
                ? clock(date, calendar: calendar)
                : RelativeTime.full(date, calendar: calendar)
        }
    }

    /// The version, without leading "v"; else the title
    public static func lead(_ item: InboxItem) -> (text: String, isVersion: Bool) {
        if let version = item.version, !version.isEmpty {
            return (plainVersion(version), true)
        }
        return (item.title, false)
    }

    /// "v3.14.7" to "3.14.7"
    public static func versionText(_ version: String) -> String { plainVersion(version) }

    static func plainVersion(_ version: String) -> String {
        let trimmed = version.trimmingCharacters(in: .whitespaces)
        if let first = trimmed.first, first == "v" || first == "V",
           trimmed.dropFirst().first?.isNumber == true {
            return String(trimmed.dropFirst())
        }
        return trimmed
    }

    /// "9:41 AM"
    static func clock(_ date: Date, calendar: Calendar) -> String {
        format(date, calendar: calendar, style: Date.FormatStyle(date: .omitted, time: .shortened))
    }

    /// "Oct 12", with the year when it isn't this one
    static func day(_ date: Date, now: Date, calendar: Calendar) -> String {
        var style = Date.FormatStyle(date: .omitted, time: .omitted).month(.abbreviated).day()
        if !calendar.isDate(date, equalTo: now, toGranularity: .year) { style = style.year() }
        return format(date, calendar: calendar, style: style)
    }

    static func format(_ date: Date, calendar: Calendar, style: Date.FormatStyle) -> String {
        var style = style
        style.calendar = calendar
        style.timeZone = calendar.timeZone
        if let locale = calendar.locale { style.locale = locale }
        return date.formatted(style)
    }
}

/// "just now", "5m ago", "2h ago", "Yesterday", "3d ago", "Oct 12"
public enum RelativeTime {
    public static func short(_ date: Date, now: Date, calendar: Calendar = .current) -> String {
        let seconds = now.timeIntervalSince(date)
        if seconds < 60 { return String(localized: "just now") }
        if seconds < 3600 { return String(localized: "\(Int(seconds / 60))m ago") }
        if calendar.isDate(date, inSameDayAs: now) { return String(localized: "\(Int(seconds / 3600))h ago") }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now), calendar.isDate(date, inSameDayAs: yesterday) {
            return String(localized: "Yesterday")
        }
        // whole calendar days
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: date), to: calendar.startOfDay(for: now)).day ?? 0
        if days < 7 { return String(localized: "\(days)d ago") }
        return Feed.day(date, now: now, calendar: calendar)
    }

    /// "Oct 28, 2:02 PM", for an expanded card and absolute times
    public static func full(_ date: Date, calendar: Calendar = .current) -> String {
        Feed.format(date, calendar: calendar,
                    style: Date.FormatStyle(date: .omitted, time: .shortened).month(.abbreviated).day())
    }
}
