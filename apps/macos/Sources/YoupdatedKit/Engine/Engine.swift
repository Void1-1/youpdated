import Foundation

public enum InboxSort: String, Codable, Sendable, CaseIterable {
    case newest, priority, source, name
}

public struct ClientInfo: Codable, Sendable {
    public var name: String
    public var version: String
    public var platform: String

    public init(name: String = "Youpdated", version: String, platform: String = "macos") {
        self.name = name
        self.version = version
        self.platform = platform
    }
}

/// Where `import.preview` and `import.apply` read
public enum ImportInput: Sendable, Hashable {
    case path(String)
    case content(String, format: String)

    /// The format `content` names, from a file name; nil when it isn't one the engine reads
    public static func format(forFileName name: String) -> String? {
        let lower = name.lowercased()
        if lower == "package.json" { return "package.json" }
        if lower == "brewfile" || lower.hasSuffix(".brewfile") { return "Brewfile" }
        if lower.hasSuffix(".txt") || lower.hasSuffix(".in"), lower.contains("requirements") {
            return "requirements.txt"
        }
        return nil
    }
}

/// The typed ops of contract v1
public struct Engine: Sendable {
    public let client: EngineClient

    public init(client: EngineClient) {
        self.client = client
    }

    public var events: AsyncStream<EngineEvent> { client.events }

    public func hello(_ info: ClientInfo) async throws -> HelloReply {
        try await client.call("hello", Params.Hello(client: info))
    }

    public func unlock(passphrase: String) async throws -> UnlockReply {
        try await client.call("unlock", Params.Unlock(passphrase: passphrase))
    }

    public func status() async throws -> StatusReply {
        try await client.call("status")
    }

    public func check(sources: [String]? = nil, targets: [TargetKey]? = nil, notify: Bool? = nil) async throws -> CheckReply {
        try await client.call("check", Params.Check(sources: sources, targets: targets, notify: notify))
    }

    public func inboxList(
        unread: Bool? = nil, source: String? = nil, target: TargetKey? = nil,
        sort: InboxSort? = nil, limit: Int? = nil, cursor: String? = nil
    ) async throws -> InboxPage {
        try await client.call(
            "inbox.list",
            Params.InboxList(unread: unread, source: source, target: target, sort: sort, limit: limit, cursor: cursor)
        )
    }

    public func markRead(_ read: Bool, ids: [String]) async throws -> MarkReply {
        try await client.call("inbox.mark", Params.Mark(read: read, ids: ids, all: nil, source: nil))
    }

    public func markAll(read: Bool, source: String? = nil) async throws -> MarkReply {
        try await client.call("inbox.mark", Params.Mark(read: read, ids: nil, all: true, source: source))
    }

    /// Only when `hello.ops` has `inbox.delete`
    public func deleteItems(ids: [String]) async throws -> DeleteReply {
        try await client.call("inbox.delete", Params.Delete(ids: ids))
    }

    public func snooze(ids: [String], minutes: Int = 60) async throws -> SnoozeReply {
        try await client.call("inbox.snooze", Params.Snooze(ids: ids, minutes: minutes))
    }

    public func targets() async throws -> TargetsReply {
        try await client.call("targets.list")
    }

    public func preview(value: String, source: String? = nil) async throws -> PreviewReply {
        try await client.call("targets.preview", Params.Preview(value: value, source: source))
    }

    public func add(source: String, entries: [String]) async throws -> AddReply {
        try await client.call("targets.add", Params.Add(source: source, entries: entries))
    }

    public func remove(source: String, key: String, wipe: Bool) async throws -> RemoveReply {
        try await client.call("targets.remove", Params.Remove(source: source, key: key, wipe: wipe ? true : nil))
    }

    public func importPreview(_ input: ImportInput) async throws -> ImportPreviewReply {
        try await client.call("import.preview", Params.Import(input))
    }

    public func importApply(_ input: ImportInput) async throws -> ImportApplyReply {
        try await client.call("import.apply", Params.Import(input))
    }

    public func settings() async throws -> Settings {
        let reply: SettingsReply = try await client.call("settings.get")
        return reply.settings
    }

    public func setSettings(_ patch: SettingsPatch) async throws -> Settings {
        let reply: SettingsReply = try await client.call("settings.set", Params.SetSettings(settings: patch))
        return reply.settings
    }

    public func setPriority(_ priority: Priority, source: String, key: String) async throws -> Priority {
        let reply: PriorityReply = try await client.call(
            "priority.set", Params.SetPriority(source: source, key: key, priority: priority))
        return reply.priority
    }

    /// Only when `hello.ops` has `filters.get`
    public func filters(source: String, key: String) async throws -> Filters {
        try await client.call("filters.get", Params.Icon(source: source, key: key))
    }

    public func setFilters(
        source: String, key: String, match: [String], skipPrereleases: Bool, bump: Bump, min: String?
    ) async throws -> Filters {
        try await client.call(
            "filters.set",
            Params.SetFilters(
                source: source, key: key, match: match, skipPrereleases: skipPrereleases, bump: bump, min: min))
    }

    public func previewFilters(
        source: String, key: String, match: [String]? = nil, skipPrereleases: Bool? = nil,
        min: String? = nil, refresh: Bool? = nil
    ) async throws -> FiltersPreview {
        try await client.call(
            "filters.preview",
            Params.PreviewFilters(
                source: source, key: key, match: match, skipPrereleases: skipPrereleases, min: min, refresh: refresh))
    }

    public func problems() async throws -> ProblemsReply {
        try await client.call("problems.list")
    }

    public func sendDigest() async throws -> DigestReply {
        try await client.call("digest.send")
    }

    public func writeSnapshot(to path: String) async throws -> SnapshotReply {
        try await client.call("snapshot.write", Params.Snapshot(path: path))
    }

    public func initConfig(selfWatch: Bool? = nil) async throws -> ConfigInitReply {
        try await client.call("config.init", Params.ConfigInit(selfWatch: selfWatch))
    }

    /// Only when `hello.ops` has `icon.get`
    public func icon(source: String, key: String) async throws -> IconReply.Image? {
        let reply: IconReply = try await client.call("icon.get", Params.Icon(source: source, key: key))
        return reply.icon
    }

    public func shutdown() async throws {
        let _: EmptyReply = try await client.call("shutdown")
    }
}

/// Request params. nil fields excluded
enum Params {
    struct Hello: Encodable, Sendable { let client: ClientInfo }
    struct Unlock: Encodable, Sendable { let passphrase: String }
    struct Check: Encodable, Sendable {
        let sources: [String]?
        let targets: [TargetKey]?
        let notify: Bool?
    }
    struct InboxList: Encodable, Sendable {
        let unread: Bool?
        let source: String?
        let target: TargetKey?
        let sort: InboxSort?
        let limit: Int?
        let cursor: String?
    }
    struct Mark: Encodable, Sendable {
        let read: Bool
        let ids: [String]?
        let all: Bool?
        let source: String?
    }
    struct Delete: Encodable, Sendable { let ids: [String] }
    struct Snooze: Encodable, Sendable {
        let ids: [String]
        let minutes: Int
    }
    struct Preview: Encodable, Sendable {
        let value: String
        let source: String?
    }
    struct Add: Encodable, Sendable {
        let source: String
        let entries: [String]
    }
    struct Remove: Encodable, Sendable {
        let source: String
        let key: String
        let wipe: Bool?
    }
    struct Import: Encodable, Sendable {
        let path: String?
        let content: String?
        let format: String?

        init(_ input: ImportInput) {
            switch input {
            case .path(let path):
                (self.path, content, format) = (path, nil, nil)
            case .content(let content, let format):
                (path, self.content, self.format) = (nil, content, format)
            }
        }
    }
    struct SetSettings: Encodable, Sendable { let settings: SettingsPatch }
    struct SetPriority: Encodable, Sendable {
        let source: String
        let key: String
        let priority: Priority
    }
    struct SetFilters: Encodable, Sendable {
        let source: String
        let key: String
        let match: [String]
        let skipPrereleases: Bool
        let bump: Bump
        let min: String?
    }
    struct PreviewFilters: Encodable, Sendable {
        let source: String
        let key: String
        let match: [String]?
        let skipPrereleases: Bool?
        let min: String?
        let refresh: Bool?
    }
    struct Snapshot: Encodable, Sendable { let path: String }
    struct Icon: Encodable, Sendable {
        let source: String
        let key: String
    }
    struct ConfigInit: Encodable, Sendable { let selfWatch: Bool? }
}
