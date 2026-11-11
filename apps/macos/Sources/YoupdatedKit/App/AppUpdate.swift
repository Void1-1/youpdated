import Foundation

/// A newer Youpdated app version
public struct AppUpdate: Hashable, Sendable {
    public var version: String
    public var tag: String
    public var itemId: String
    public var releaseURL: URL?
    public var downloadURL: URL

    /// This app's release tags. Windows releases are `app-windows-v*` and the CLI's `v*`, on the same repo
    public static let tagPrefixes = ["app-macos-v", "app-v"]

    /// `Youpdated-<version>-macOS.dmg`, attached to every `app-macos-v*` release
    public static func assetName(version: String) -> String {
        "Youpdated-\(version)-macOS.dmg"
    }

    /// The newest starred item that names a version above `current`
    public static func available(in items: [InboxItem], current: String) -> AppUpdate? {
        items
            .filter(\.starred)
            .compactMap(update(from:))
            .filter { compare($0.version, current) == .orderedDescending }
            .max { compare($0.version, $1.version) == .orderedAscending }
    }

    public static func update(from item: InboxItem) -> AppUpdate? {
        let tag = item.version ?? item.title
        guard let prefix = tagPrefixes.first(where: { tag.hasPrefix($0) }) else { return nil }
        let version = String(tag.dropFirst(prefix.count))
        guard !version.isEmpty, version.first?.isNumber == true else { return nil }
        // the target key is host/owner/repo
        let parts = item.target.split(separator: "/")
        guard parts.count >= 3,
              let download = URL(string: "https://\(parts[0])/\(parts[1])/\(parts[2])/releases/download/\(tag)/\(assetName(version: version))")
        else { return nil }
        return AppUpdate(
            version: version, tag: tag, itemId: item.id, releaseURL: URL(string: item.url), downloadURL: download)
    }

    /// Dotted versions compared number by number; a pre-release suffix sorts below its release
    public static func compare(_ a: String, _ b: String) -> ComparisonResult {
        func split(_ version: String) -> ([Int], String) {
            let core = version.prefix { $0.isNumber || $0 == "." }
            let numbers = core.split(separator: ".").map { Int($0) ?? 0 }
            return (numbers, String(version.dropFirst(core.count)))
        }
        let (left, leftRest) = split(a)
        let (right, rightRest) = split(b)
        for index in 0..<max(left.count, right.count) {
            let l = index < left.count ? left[index] : 0
            let r = index < right.count ? right[index] : 0
            if l != r { return l < r ? .orderedAscending : .orderedDescending }
        }
        switch (leftRest.isEmpty, rightRest.isEmpty) {
        case (true, false): return .orderedDescending
        case (false, true): return .orderedAscending
        default: return leftRest == rightRest ? .orderedSame : (leftRest < rightRest ? .orderedAscending : .orderedDescending)
        }
    }
}
