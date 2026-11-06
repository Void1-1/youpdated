import Foundation

// file `snapshot.write` uses to sync with the app
public struct WidgetSnapshot: Codable, Sendable, Hashable {
    public struct Item: Codable, Sendable, Hashable, Identifiable {
        public var id: String
        public var source: String
        public var display: String
        public var title: String
        public var url: String
        public var foundAt: Date

        public init(id: String, source: String, display: String, title: String, url: String, foundAt: Date) {
            self.id = id
            self.source = source
            self.display = display
            self.title = title
            self.url = url
            self.foundAt = foundAt
        }
    }

    public init(version: Int, writtenAt: Date, lastCheck: Date?, unread: Int, redacted: Bool, items: [Item]) {
        self.version = version
        self.writtenAt = writtenAt
        self.lastCheck = lastCheck
        self.unread = unread
        self.redacted = redacted
        self.items = items
    }

    public var version: Int
    public var writtenAt: Date
    public var lastCheck: Date?
    public var unread: Int
    public var redacted: Bool
    public var items: [Item]

    public static let fileName = "snapshot.json"

    public static func read(from url: URL) -> WidgetSnapshot? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? ContractCoding.decoder().decode(WidgetSnapshot.self, from: data)
    }

    public static let placeholder = WidgetSnapshot(
        version: 1, writtenAt: Date(), lastCheck: Date().addingTimeInterval(-1800), unread: 3, redacted: false,
        items: [
            Item(id: "1", source: "git", display: "python/cpython", title: "v3.14.7", url: "", foundAt: Date()),
            Item(id: "2", source: "steam", display: "Team Fortress 2", title: "Halloween Update", url: "", foundAt: Date()),
            Item(id: "3", source: "npm", display: "react", title: "19.3.0", url: "", foundAt: Date()),
        ])
}
