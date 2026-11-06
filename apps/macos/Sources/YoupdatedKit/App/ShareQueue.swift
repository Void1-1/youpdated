import Foundation

/// Links the Share extension passes through the app group: appends to a queue file and posts a Darwin notification
public struct ShareQueue: Sendable {
    public let file: URL
    /// Posted after each append; named after the app group
    public let notificationName: String

    public init(container: URL, appGroup: String) {
        file = container.appending(path: "share-queue.json")
        notificationName = "\(appGroup).share"
    }

    public static func appGroup(in bundle: Bundle = .main) -> String? {
        appGroup(bundle.object(forInfoDictionaryKey: "YoupdatedAppGroup") as? String)
    }

    static func appGroup(_ value: String?) -> String? {
        guard let value, !value.isEmpty, !value.hasPrefix("$("), !value.hasPrefix(".") else { return nil }
        return value
    }

    public static func shared(bundle: Bundle = .main) -> ShareQueue? {
        guard let group = appGroup(in: bundle),
              let container = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group)
        else { return nil }
        return ShareQueue(container: container, appGroup: group)
    }

    public func append(_ values: [String]) throws {
        try coordinate(writing: true) { url in
            let queued = Self.read(url) + values
            try JSONEncoder().encode(queued).write(to: url, options: .atomic)
        }
        CFNotificationCenterPostNotification(
            CFNotificationCenterGetDarwinNotifyCenter(), CFNotificationName(notificationName as CFString), nil, nil, true)
    }

    /// Everything queued, oldest first
    public func drain() -> [String] {
        var values: [String] = []
        try? coordinate(writing: true) { url in
            values = Self.read(url)
            if !values.isEmpty {
                try JSONEncoder().encode([String]()).write(to: url, options: .atomic)
            }
        }
        return values
    }

    private static func read(_ url: URL) -> [String] {
        (try? Data(contentsOf: url)).flatMap { try? JSONDecoder().decode([String].self, from: $0) } ?? []
    }

    private func coordinate(writing: Bool, _ body: (URL) throws -> Void) throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        var coordinationError: NSError?
        var bodyError: Error?
        NSFileCoordinator().coordinate(writingItemAt: file, options: .forMerging, error: &coordinationError) { url in
            do { try body(url) } catch { bodyError = error }
        }
        if let error = coordinationError ?? bodyError { throw error }
    }
}
