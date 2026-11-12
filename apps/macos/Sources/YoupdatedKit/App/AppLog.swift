import Foundation
import os

/// Settings → Advanced → Save diagnostic logs: errors in `~/Library/Logs/Youpdated/app.log` for attaching to a bug report
public final class AppLog: @unchecked Sendable {
    public static let shared = AppLog()

    /// Started over past this
    static let limit = 256 * 1024
    static let enabledKey = "diagnosticLog"

    public let file: URL
    private let defaults: UserDefaults
    private let lock = NSLock()
    private static let fallback = Logger(subsystem: "Youpdated", category: "log")

    public init(
        folder: URL = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Logs/Youpdated"),
        defaults: UserDefaults = .standard
    ) {
        file = folder.appending(path: "app.log")
        self.defaults = defaults
    }

    public var enabled: Bool {
        get { defaults.bool(forKey: Self.enabledKey) }
        set { defaults.set(newValue, forKey: Self.enabledKey) }
    }

    public var exists: Bool { FileManager.default.fileExists(atPath: file.path) }

    public func write(_ message: String) {
        guard enabled else { return }
        let stamp = Date.now.formatted(.iso8601.year().month().day().time(includingFractionalSeconds: false).timeZone(separator: .colon))
        let entry = Data("\(stamp) \(message)\n".utf8)
        lock.lock()
        defer { lock.unlock() }
        do {
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            let size = (try? FileManager.default.attributesOfItem(atPath: file.path)[.size] as? Int) ?? 0
            if size > Self.limit {
                try FileManager.default.removeItem(at: file)
            }
            if !FileManager.default.fileExists(atPath: file.path) {
                FileManager.default.createFile(atPath: file.path, contents: nil, attributes: [.posixPermissions: 0o600])
            }
            let handle = try FileHandle(forWritingTo: file)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: entry)
        } catch {
            Self.fallback.error("no diagnostic log: \(error.localizedDescription, privacy: .public)")
        }
    }

    public func delete() {
        lock.lock()
        defer { lock.unlock() }
        try? FileManager.default.removeItem(at: file)
    }
}
