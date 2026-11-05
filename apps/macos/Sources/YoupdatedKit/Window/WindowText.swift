import Foundation

/// Release notes as blocks for the card to draw: md paragraphs, bullets, headings
public struct NoteBlock: Hashable, Sendable {
    public enum Kind: Hashable, Sendable { case paragraph, bullet, heading }

    public var kind: Kind
    public var text: String
}

public enum Notes {
    /// Blocks shown before "Show full notes"
    public static let clipLimit = 6

    public static func blocks(_ body: String?) -> [NoteBlock] {
        guard let body else { return [] }
        var blocks: [NoteBlock] = []
        var paragraph: [String] = []
        func flush() {
            if !paragraph.isEmpty {
                blocks.append(NoteBlock(kind: .paragraph, text: paragraph.joined(separator: " ")))
                paragraph.removeAll()
            }
        }
        for raw in body.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty {
                flush()
            } else if let rest = bulletText(line) {
                flush()
                blocks.append(NoteBlock(kind: .bullet, text: rest))
            } else if line.hasPrefix("#") {
                flush()
                let text = line.drop { $0 == "#" }.trimmingCharacters(in: .whitespaces)
                if !text.isEmpty { blocks.append(NoteBlock(kind: .heading, text: text)) }
            } else {
                paragraph.append(line)
            }
        }
        flush()
        return blocks
    }

    /// The first `limit` blocks, and whether any were left out
    public static func clipped(_ blocks: [NoteBlock], limit: Int = clipLimit) -> (blocks: [NoteBlock], clipped: Bool) {
        (Array(blocks.prefix(limit)), blocks.count > limit)
    }

    /// The first line of the notes as plain text, for a card's second line
    public static func firstLine(_ body: String?) -> String? {
        guard let first = blocks(body).first else { return nil }
        let plain = plainText(first.text)
        return plain.isEmpty ? nil : plain
    }

    /// Inline Markdown without its markup: `[text](url)` to text, and no `*`, `_`, or backticks
    public static func plainText(_ markdown: String) -> String {
        var text = markdown.replacingOccurrences(
            of: #"!?\[([^\]]*)\]\([^)]*\)"#, with: "$1", options: .regularExpression)
        for mark in ["**", "__", "`", "*"] {
            text = text.replacingOccurrences(of: mark, with: "")
        }
        return text.trimmingCharacters(in: .whitespaces)
    }

    private static func bulletText(_ line: String) -> String? {
        for marker in ["- ", "* ", "+ ", "• "] where line.hasPrefix(marker) {
            return String(line.dropFirst(marker.count)).trimmingCharacters(in: .whitespaces)
        }
        return nil
    }
}

public struct Monogram: Hashable, Sendable {
    public struct Color: Hashable, Sendable {
        public var name: String
        /// 0xRRGGBB
        public var rgb: UInt32
    }

    public static let palette: [Color] = [
        Color(name: "Red", rgb: 0xD9534F), Color(name: "Orange", rgb: 0xCC6B27),
        Color(name: "Amber", rgb: 0xB98318), Color(name: "Olive", rgb: 0x7F9A2E),
        Color(name: "Green", rgb: 0x3E9A5E), Color(name: "Teal", rgb: 0x2D9490),
        Color(name: "Cyan", rgb: 0x2B8FC4), Color(name: "Blue", rgb: 0x3A72D8),
        Color(name: "Indigo", rgb: 0x5A5BCF), Color(name: "Purple", rgb: 0x8655C8),
        Color(name: "Pink", rgb: 0xC94F86), Color(name: "Graphite", rgb: 0x66707A),
    ]

    public var initials: String
    /// An index into `palette`
    public var colorIndex: Int

    public var color: Color { Self.palette[colorIndex] }

    public init(source: String, key: String, display: String) {
        initials = Self.initials(display)
        colorIndex = Int(Self.hash("\(source):\(key)") % UInt32(Self.palette.count))
    }

    /// The last path segment without a leading "@"; the first letters of its first two
    /// words, or one letter: "Team Fortress 2" to "TF", "python/cpython" to "C"
    public static func initials(_ display: String) -> String {
        var last = display.split(separator: "/").last.map(String.init) ?? display
        if last.hasPrefix("@") { last.removeFirst() }
        let words = last.split(whereSeparator: \.isWhitespace)
        let letters = words.prefix(words.count >= 2 ? 2 : 1).compactMap(\.first).map { String($0).uppercased() }
        return letters.isEmpty ? "?" : letters.joined()
    }

    /// 32-bit FNV-1a over the UTF-8 bytes
    public static func hash(_ text: String) -> UInt32 {
        var hash: UInt32 = 2_166_136_261
        for byte in text.utf8 {
            hash ^= UInt32(byte)
            hash = hash &* 16_777_619
        }
        return hash
    }
}

/// Following: every watched target, grouped by source, Youpdated's own entry above the groups
public struct FollowingRow: Identifiable, Hashable, Sendable {
    public enum Status: Hashable, Sendable {
        case ok
        case failed(String)
        case notChecked
    }

    public var target: Target
    /// The latest version, the date of the latest post for sources without versions, or empty
    public var latest: String

    public var id: TargetKey { target.id }
    public var label: String { SourceLabel.label(source: target.source, key: target.key) }
    public var status: Status {
        if let error = target.lastError { return .failed(error) }
        return target.lastChecked == nil ? .notChecked : .ok
    }
}

public struct FollowingGroup: Identifiable, Hashable, Sendable {
    public var title: String
    public var rows: [FollowingRow]

    public var id: String { title }
}

public struct FollowingLayout: Hashable, Sendable {
    public var own: FollowingRow?
    public var groups: [FollowingGroup]
}

public enum Following {
    /// `items` fill in the latest version for an engine that doesn't send `Target.latest`
    public static func build(
        targets: [Target], items: [InboxItem], filter: String = "", now: Date = Date(), calendar: Calendar = .current
    ) -> FollowingLayout {
        let newest = Dictionary(grouping: items, by: \.targetKey).mapValues { $0.max { $0.date < $1.date } }
        let needle = filter.trimmingCharacters(in: .whitespaces).lowercased()
        func row(_ target: Target) -> FollowingRow {
            let latest: Target.Latest? = target.latest ?? (newest[target.id] ?? nil).map {
                Target.Latest(version: $0.version, title: $0.title, published: $0.published ?? $0.foundAt)
            }
            var text = ""
            if let latest {
                if let version = latest.version, !version.isEmpty {
                    text = Feed.versionText(version)
                } else if let published = latest.published {
                    text = Feed.day(published, now: now, calendar: calendar)
                }
            }
            return FollowingRow(target: target, latest: text)
        }
        func matches(_ target: Target) -> Bool {
            needle.isEmpty || target.display.lowercased().contains(needle) || target.key.lowercased().contains(needle)
                || SourceLabel.label(source: target.source, key: target.key).lowercased().contains(needle)
        }
        let own = targets.first(where: \.starred).flatMap { matches($0) ? row($0) : nil }
        let grouped = Dictionary(grouping: targets.filter { !$0.starred && matches($0) }) {
            SourceLabel.label(source: $0.source, key: $0.key)
        }
        let groups = grouped.keys.sorted { $0.localizedStandardCompare($1) == .orderedAscending }.map { title in
            FollowingGroup(title: title, rows: grouped[title]!
                .sorted { $0.display.localizedStandardCompare($1.display) == .orderedAscending }
                .map(row))
        }
        return FollowingLayout(own: own, groups: groups)
    }
}

/// cmd + V in the window opens Add filled in, but only when the clipboard holds a link
public enum ClipboardCheck {
    public static func link(from text: String?) -> String? {
        guard let text = text?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty,
              !text.contains(where: \.isWhitespace),
              let url = URL(string: text), let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https", url.host() != nil else { return nil }
        return text
    }
}

/// Copy Details in the Problems popover
public enum ProblemReport {
    public static func text(
        problems: [Problem], appProblems: [AppProblem], configError: String?, engineFailure: String?
    ) -> String {
        var lines: [String] = []
        if let engineFailure { lines.append("engine: \(engineFailure)") }
        if let configError { lines.append("config: \(configError)") }
        for problem in problems {
            let label = problem.kind == .notify ? "notify" : SourceLabel.label(source: problem.source, key: problem.key)
            lines.append("\(label) · \(problem.target): \(problem.message)")
        }
        for problem in appProblems {
            lines.append("app: \(problem.title) \(problem.detail)".trimmingCharacters(in: .whitespaces))
        }
        return lines.joined(separator: "\n")
    }
}
