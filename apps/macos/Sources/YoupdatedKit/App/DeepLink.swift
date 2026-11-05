import Foundation

/// `youpdated://add?url=…`, `youpdated://open`, and `youpdated://check`
public enum DeepLink: Hashable, Sendable {
    case add(String)
    case open
    case check

    public static let scheme = "youpdated"

    public init?(url: URL) {
        guard url.scheme?.lowercased() == Self.scheme,
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        // youpdated://add?... and youpdated:add?...
        let action = (components.host ?? components.path).trimmingCharacters(in: CharacterSet(charactersIn: "/")).lowercased()
        switch action {
        case "add":
            let value = components.queryItems?.first { $0.name == "url" }?.value?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            guard let value, !value.isEmpty else { return nil }
            self = .add(value)
        case "open", "":
            self = .open
        case "check":
            self = .check
        default:
            return nil
        }
    }

    /// What dropped or shared text holds to watch: the first link, or the text when it's one line
    public static func watchValue(from text: String) -> String? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        if let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue),
           let match = detector.firstMatch(in: trimmed, range: NSRange(trimmed.startIndex..., in: trimmed)),
           let url = match.url, url.scheme?.hasPrefix("http") == true {
            return url.absoluteString
        }
        return trimmed.contains("\n") ? nil : trimmed
    }
}
