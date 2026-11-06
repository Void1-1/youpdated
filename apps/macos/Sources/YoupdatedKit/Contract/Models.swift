import Foundation

// Contract v1 types

public enum Priority: String, Codable, Sendable, CaseIterable, Comparable {
    case high, normal, low

    /// high sorts first
    var rank: Int {
        switch self {
        case .high: 0
        case .normal: 1
        case .low: 2
        }
    }

    public static func < (lhs: Priority, rhs: Priority) -> Bool { lhs.rank < rhs.rank }
}

public struct TargetKey: Codable, Sendable, Hashable {
    public var source: String
    public var key: String

    public init(source: String, key: String) {
        self.source = source
        self.key = key
    }
}

public struct TargetRef: Codable, Sendable, Hashable {
    public var source: String
    public var key: String
    public var display: String

    public init(source: String, key: String, display: String) {
        self.source = source
        self.key = key
        self.display = display
    }

    public var targetKey: TargetKey { TargetKey(source: source, key: key) }
}

public struct Target: Codable, Sendable, Hashable, Identifiable {
    public var source: String
    public var key: String
    public var display: String
    public var priority: Priority
    public var starred: Bool
    public var lastChecked: Date?
    public var lastError: String?
    public var unread: Int
    /// The newest item the engine has seen, for Following
    public var latest: Latest?

    public struct Latest: Codable, Sendable, Hashable {
        public var version: String?
        public var title: String
        public var published: Date?
    }

    public var id: TargetKey { TargetKey(source: source, key: key) }
    public var ref: TargetRef { TargetRef(source: source, key: key, display: display) }
}

public struct InboxItem: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var source: String
    /// The target's key
    public var target: String
    public var display: String
    public var title: String
    public var url: String
    public var version: String?
    public var published: Date?
    public var tags: [String]
    public var body: String?
    public var foundAt: Date
    public var read: Bool
    public var baseline: Bool
    public var priority: Priority
    public var starred: Bool
    public var snoozedUntil: Date?

    public var targetKey: TargetKey { TargetKey(source: source, key: target) }
    public var targetRef: TargetRef { TargetRef(source: source, key: target, display: display) }
    /// The source's label
    public var label: String { SourceLabel.label(source: source, key: target) }
    public var date: Date { published ?? foundAt }
}

public struct PreviewItem: Codable, Sendable, Hashable {
    public var title: String
    public var url: String
    public var version: String?
    public var published: Date?
}

public struct Problem: Codable, Sendable, Hashable {
    public enum Kind: String, Codable, Sendable {
        case source, notify, config
        /// a kind this app doesn't know
        case other

        public init(from decoder: Decoder) throws {
            let raw = try decoder.singleValueContainer().decode(String.self)
            self = Kind(rawValue: raw) ?? .other
        }
    }

    public var kind: Kind
    public var source: String
    public var target: String
    public var message: String
    /// The failing target's key, for Retry
    public var key: String?

    public init(kind: Kind, source: String, target: String, message: String, key: String? = nil) {
        self.kind = kind
        self.source = source
        self.target = target
        self.message = message
        self.key = key
    }
}

public struct Alert: Codable, Sendable, Hashable {
    public enum Level: String, Codable, Sendable { case high, normal }

    public var priority: Level
    /// The target of a high priority alert; nil for combined
    public var target: TargetRef?
    public var itemIds: [String]
}

public struct Settings: Codable, Sendable, Hashable {
    public static let intervals = [1, 3, 6, 12, 24]

    public var checkIntervalHours: Int
    /// Local "HH:MM", or nil for no digest
    public var digestTime: String?
    public var osNotifications: Bool
    public var selfWatchDeclined: Bool
    public var cliNoticeShown: Bool
    /// Real icons instead of monograms
    public var showIcons: Bool

    public static let defaults = Settings(
        checkIntervalHours: 6, digestTime: nil, osNotifications: true,
        selfWatchDeclined: false, cliNoticeShown: false, showIcons: false
    )

    public init(
        checkIntervalHours: Int, digestTime: String?, osNotifications: Bool,
        selfWatchDeclined: Bool, cliNoticeShown: Bool, showIcons: Bool = false
    ) {
        self.checkIntervalHours = checkIntervalHours
        self.digestTime = digestTime
        self.osNotifications = osNotifications
        self.selfWatchDeclined = selfWatchDeclined
        self.cliNoticeShown = cliNoticeShown
        self.showIcons = showIcons
    }

    enum CodingKeys: String, CodingKey {
        case checkIntervalHours, digestTime, osNotifications, selfWatchDeclined, cliNoticeShown, showIcons
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        checkIntervalHours = try container.decode(Int.self, forKey: .checkIntervalHours)
        digestTime = try container.decodeIfPresent(String.self, forKey: .digestTime)
        osNotifications = try container.decode(Bool.self, forKey: .osNotifications)
        selfWatchDeclined = try container.decode(Bool.self, forKey: .selfWatchDeclined)
        cliNoticeShown = try container.decode(Bool.self, forKey: .cliNoticeShown)
        showIcons = try container.decodeIfPresent(Bool.self, forKey: .showIcons) ?? false
    }

    public var checkInterval: TimeInterval { TimeInterval(checkIntervalHours) * 3600 }
}

public struct SettingsPatch: Encodable, Sendable, Equatable {
    public var checkIntervalHours: Int?
    public var digestTime: String??
    public var osNotifications: Bool?
    public var selfWatchDeclined: Bool?
    public var cliNoticeShown: Bool?
    public var showIcons: Bool?

    public init(
        checkIntervalHours: Int? = nil, digestTime: String?? = nil, osNotifications: Bool? = nil,
        selfWatchDeclined: Bool? = nil, cliNoticeShown: Bool? = nil, showIcons: Bool? = nil
    ) {
        self.checkIntervalHours = checkIntervalHours
        self.digestTime = digestTime
        self.osNotifications = osNotifications
        self.selfWatchDeclined = selfWatchDeclined
        self.cliNoticeShown = cliNoticeShown
        self.showIcons = showIcons
    }

    enum CodingKeys: String, CodingKey {
        case checkIntervalHours, digestTime, osNotifications, selfWatchDeclined, cliNoticeShown, showIcons
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(checkIntervalHours, forKey: .checkIntervalHours)
        if let digestTime {
            try container.encode(digestTime, forKey: .digestTime)
        }
        try container.encodeIfPresent(osNotifications, forKey: .osNotifications)
        try container.encodeIfPresent(selfWatchDeclined, forKey: .selfWatchDeclined)
        try container.encodeIfPresent(cliNoticeShown, forKey: .cliNoticeShown)
        try container.encodeIfPresent(showIcons, forKey: .showIcons)
    }
}

public struct SourceEntries: Codable, Sendable, Hashable {
    public var source: String
    public var entries: [String]
}

public struct Skipped: Codable, Sendable, Hashable {
    public var what: String
    public var why: String
}

public struct Encrypted: Codable, Sendable, Hashable {
    public var config: Bool
    public var history: Bool

    public var any: Bool { config || history }
}

public struct HelloReply: Codable, Sendable {
    public var engine: String
    public var contract: Int
    public var ops: [String]
    public var commit: String?
    public var encryption: Bool
}

public struct UnlockReply: Codable, Sendable {
    public var encrypted: Encrypted
}

public struct StatusReply: Codable, Sendable {
    public enum RunBy: String, Codable, Sendable { case app, cli }
    public enum SelfWatch: String, Codable, Sendable { case watching, declined, absent }

    public struct Counts: Codable, Sendable, Hashable {
        public var targets: Int
        public var unread: Int
        public var problems: Int
    }

    public var locked: Bool
    public var encrypted: Encrypted
    public var configPath: String?
    public var configExists: Bool
    public var configError: String?
    public var historyPath: String
    public var lastRun: Date?
    public var lastRunBy: RunBy?
    public var selfWatch: SelfWatch?
    public var counts: Counts?
}

public struct CheckReply: Codable, Sendable {
    public var checkedAt: Date
    public var new: Int
    public var items: [InboxItem]
    public var baselined: [TargetRef]
    public var errors: [Problem]
    public var warnings: [String]
    public var notifyFailures: [Problem]
    public var alerts: [Alert]
    public var held: Int
    public var unread: Int
}

public struct InboxPage: Codable, Sendable {
    public var items: [InboxItem]
    public var cursor: String?
    public var total: Int
    public var unread: Int
}

public struct MarkReply: Codable, Sendable {
    public var changed: Int
    public var unread: Int
}

public struct SnoozeReply: Codable, Sendable {
    public var until: Date
}

public struct TargetsReply: Codable, Sendable {
    public var targets: [Target]
}

public struct PreviewReply: Codable, Sendable, Hashable {
    public var source: String
    public var entry: String
    public var key: String
    public var display: String
    public var alreadyWatching: Bool
    public var newest: PreviewItem?
    public var fetchError: String?
}

public struct AddReply: Codable, Sendable {
    public var section: String
    public var added: [String]
    public var skipped: [String]
    public var targets: [TargetRef]
}

public struct RemoveReply: Codable, Sendable {
    public struct Wiped: Codable, Sendable, Hashable {
        public var seen: Int
        public var validators: Int
        public var inbox: Int
    }

    public var removed: [String]
    public var wiped: Wiped?
}

public struct ImportPreviewReply: Codable, Sendable, Hashable {
    public var add: [SourceEntries]
    public var already: [SourceEntries]
    public var skipped: [Skipped]
    public var warnings: [String]

    public var count: Int { add.reduce(0) { $0 + $1.entries.count } }
}

public struct ImportApplyReply: Codable, Sendable {
    public var added: [SourceEntries]
    public var skipped: [Skipped]
    public var warnings: [String]
    public var targets: [TargetRef]
}

public struct SettingsReply: Codable, Sendable {
    public var settings: Settings
}

public struct PriorityReply: Codable, Sendable {
    public var priority: Priority
}

public struct ProblemsReply: Codable, Sendable {
    public var problems: [Problem]
    public var checkedAt: Date?
}

public struct DigestReply: Codable, Sendable {
    public var items: [InboxItem]
    public var alerts: [Alert]
    public var notifyFailures: [Problem]
}

public struct SnapshotReply: Codable, Sendable {
    public var path: String
    public var redacted: Bool
    public var items: Int
}

public struct ConfigInitReply: Codable, Sendable {
    public var configPath: String
    public var created: Bool
    public var selfWatchAdded: Bool
}

public struct EmptyReply: Codable, Sendable {}

public struct IconReply: Codable, Sendable {
    public struct Image: Codable, Sendable, Hashable {
        public var mime: String
        /// base64
        public var data: Data
    }

    public var icon: Image?
}

public struct ProgressEvent: Codable, Sendable, Hashable {
    public var done: Int
    public var of: Int
    public var target: TargetRef?
    public var failed: Bool?
}

public struct IconEvent: Codable, Sendable, Hashable {
    public var target: TargetKey?

    public var keys: [TargetKey] { target.map { [$0] } ?? [] }
}

public enum EngineEvent: Sendable, Hashable {
    case progress(ProgressEvent)
    case icon([TargetKey])
    /// an event kind this app doesn't know; 1.x may add them
    case unknown(String)
}

public struct EngineError: Error, Sendable, Hashable, Codable, LocalizedError {
    public enum Code: Sendable, Hashable {
        case badRequest, unknownOp, locked, wrongPassphrase, encryptionUnavailable, noConfig
        case configError, historyBusy, busy, ambiguous, unrecognized, badEntry, notFound
        case ioError, `internal`
        case other(String)

        static let wire: [String: Code] = [
            "bad_request": .badRequest, "unknown_op": .unknownOp, "locked": .locked,
            "wrong_passphrase": .wrongPassphrase, "encryption_unavailable": .encryptionUnavailable,
            "no_config": .noConfig, "config_error": .configError, "history_busy": .historyBusy,
            "busy": .busy, "ambiguous": .ambiguous, "unrecognized": .unrecognized,
            "bad_entry": .badEntry, "not_found": .notFound, "io_error": .ioError,
            "internal": .internal,
        ]

        public init(wire: String) {
            self = Code.wire[wire] ?? .other(wire)
        }

        public var wire: String {
            if case .other(let raw) = self { return raw }
            return Code.wire.first { $0.value == self }!.key
        }
    }

    public var code: Code
    public var message: String
    public var choices: [String]?

    public init(code: Code, message: String, choices: [String]? = nil) {
        self.code = code
        self.message = message
        self.choices = choices
    }

    enum CodingKeys: String, CodingKey { case code, message, choices }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        code = Code(wire: try container.decode(String.self, forKey: .code))
        message = try container.decode(String.self, forKey: .message)
        choices = try container.decodeIfPresent([String].self, forKey: .choices)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(code.wire, forKey: .code)
        try container.encode(message, forKey: .message)
        try container.encodeIfPresent(choices, forKey: .choices)
    }

    public var errorDescription: String? { message }
}
