import Foundation

/// What the app keeps in ~/Library besides its settings, for Remove Youpdated from this Mac
public enum AppTraces {
    public static func paths(home: URL, bundleId: String) -> [URL] {
        let library = home.appending(path: "Library")
        return [
            library.appending(path: "Saved Application State/\(bundleId).savedState"),
            library.appending(path: "Caches/\(bundleId)"),
            library.appending(path: "HTTPStorages/\(bundleId)"),
            library.appending(path: "HTTPStorages/\(bundleId).binarycookies"),
            library.appending(path: "WebKit/\(bundleId)"),
        ]
    }

    /// Deletes them and the diagnostic log, then the log's folder once nothing else is in it
    public static func remove(home: URL, bundleId: String, log: AppLog = .shared) {
        let files = FileManager.default
        for url in paths(home: home, bundleId: bundleId) {
            try? files.removeItem(at: url)
        }
        log.delete()
        let folder = log.file.deletingLastPathComponent()
        if (try? files.contentsOfDirectory(atPath: folder.path))?.isEmpty == true {
            try? files.removeItem(at: folder)
        }
    }
}
