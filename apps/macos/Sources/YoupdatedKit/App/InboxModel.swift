import Foundation
import Observation

/// Several updates from one target, as one row
public struct InboxRow: Identifiable, Hashable, Sendable {
    public var target: TargetRef
    /// Newest first
    public var items: [InboxItem]
    public var unread: Bool

    public var id: String { "\(unread ? "new" : "earlier")|\(target.source)|\(target.key)" }
    public var newest: InboxItem { items[0] }
    public var date: Date { newest.date }
    public var priority: Priority { newest.priority }
    public var starred: Bool { newest.starred }
}

public struct InboxSection: Identifiable, Hashable, Sendable {
    public enum Kind: Hashable, Sendable { case new, earlier }

    public var kind: Kind
    public var rows: [InboxRow]

    public var id: Kind { kind }
    public var title: String {
        kind == .new ? String(localized: "New") : String(localized: "Earlier")
    }
}

@MainActor
@Observable
public final class InboxModel {
    public private(set) var items: [InboxItem] = []
    public private(set) var loaded = false
    /// Only this source, or every source for nil
    public var sourceFilter: String?

    weak var app: AppModel?

    /// Pages are read until this many items
    static let cap = 2000
    static let pageSize = 500

    init() {}

    /// The source labels in the inbox, for Filter by Source
    public var sources: [String] {
        Array(Set(items.map(\.label))).sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }

    public var unreadCount: Int { items.lazy.filter { !$0.read }.count }

    /// New items first, then Earlier, each grouped by target and sorted
    public nonisolated static func sections(_ items: [InboxItem], sort: InboxSort, source: String?) -> [InboxSection] {
        let shown = source.map { name in items.filter { $0.source == name } } ?? items
        var result: [InboxSection] = []
        for (kind, unread) in [(InboxSection.Kind.new, true), (.earlier, false)] {
            let matching = shown.filter { $0.read != unread }
            let grouped = Dictionary(grouping: matching, by: \.targetKey)
            let rows = grouped.values.map { group in
                let sorted = group.sorted { $0.date > $1.date }
                return InboxRow(target: sorted[0].targetRef, items: sorted, unread: unread)
            }
            if !rows.isEmpty {
                result.append(InboxSection(kind: kind, rows: Self.sorted(rows, by: sort)))
            }
        }
        return result
    }

    /// Newest breaks ties
    public nonisolated static func sorted(_ rows: [InboxRow], by sort: InboxSort) -> [InboxRow] {
        rows.sorted { a, b in
            switch sort {
            case .newest:
                break
            case .priority:
                if a.priority != b.priority { return a.priority < b.priority }
            case .source:
                let order = a.target.source.localizedStandardCompare(b.target.source)
                if order != .orderedSame { return order == .orderedAscending }
            case .name:
                let order = a.target.display.localizedStandardCompare(b.target.display)
                if order != .orderedSame { return order == .orderedAscending }
            }
            if a.date != b.date { return a.date > b.date }
            return a.id < b.id
        }
    }

    public func reload() async {
        guard let engine = app?.engine else { return }
        var all: [InboxItem] = []
        var cursor: String?
        do {
            repeat {
                let page = try await engine.inboxList(sort: .newest, limit: Self.pageSize, cursor: cursor)
                all += page.items
                app?.unread = page.unread
                cursor = page.cursor
            } while cursor != nil && all.count < Self.cap
            items = all
            loaded = true
        } catch {
            app?.report(error, doing: String(localized: "Couldn't load the inbox."))
        }
    }

    /// Marks the row read
    @discardableResult
    public func open(_ row: InboxRow) async -> URL? {
        await setRead(true, row)
        return URL(string: row.newest.url)
    }

    public func setRead(_ read: Bool, _ row: InboxRow) async {
        let ids = row.items.filter { $0.read != read }.map(\.id)
        await mark(read, ids: ids)
    }

    public func mark(_ read: Bool, ids: [String]) async {
        guard !ids.isEmpty, let engine = app?.engine else { return }
        let wanted = Set(ids)
        let changed = items.lazy.filter { wanted.contains($0.id) && $0.read != read }.count
        app?.unread = max(0, (app?.unread ?? 0) + (read ? -changed : changed))
        apply { if wanted.contains($0.id) { $0.read = read } }
        do {
            let reply = try await engine.markRead(read, ids: ids)
            app?.unread = reply.unread
            await app?.writeSnapshot()
            if read { app?.platform.withdraw(itemIds: ids) }
        } catch {
            app?.report(error, doing: String(localized: "Couldn't change what's read."))
            await reload()
        }
    }

    public func markAllRead() async {
        guard let engine = app?.engine else { return }
        if let label = sourceFilter {
            // the engine marks by source, and a label can be part of one (GitHub within git)
            await mark(true, ids: items.filter { !$0.read && $0.label == label }.map(\.id))
            return
        }
        let ids = items.filter { !$0.read }.map(\.id)
        app?.unread = 0
        apply { $0.read = true }
        do {
            let reply = try await engine.markAll(read: true)
            app?.unread = reply.unread
            await app?.writeSnapshot()
            app?.platform.withdraw(itemIds: ids)
        } catch {
            app?.report(error, doing: String(localized: "Couldn't mark everything read."))
            await reload()
        }
    }

    public func setPriority(_ priority: Priority, for target: TargetKey) async {
        guard let engine = app?.engine else { return }
        let before = app?.sources.targets.first { $0.id == target }?.priority
            ?? items.first { $0.targetKey == target }?.priority
        changed(priority, for: target)
        do {
            let set = try await engine.setPriority(priority, source: target.source, key: target.key)
            if set != priority { changed(set, for: target) }
        } catch {
            if let before { changed(before, for: target) }
            app?.report(error, doing: String(localized: "Couldn't change the priority."))
        }
    }

    private func changed(_ priority: Priority, for target: TargetKey) {
        apply { if $0.targetKey == target { $0.priority = priority } }
        app?.sources.priorityChanged(priority, for: target)
    }

    /// Items a check just added, ahead of a reload
    func merge(_ new: [InboxItem]) {
        let known = Set(items.map(\.id))
        items = new.filter { !known.contains($0.id) } + items
    }

    private func apply(_ change: (inout InboxItem) -> Void) {
        var copy = items
        for index in copy.indices { change(&copy[index]) }
        items = copy
    }
}
