import Foundation

/// Settings → View: display preferences in UserDefaults
public struct ViewOptions: Codable, Hashable, Sendable {
    public enum Density: String, Codable, CaseIterable, Sendable {
        /// Cards
        case comfortable
        /// Every item a one-line row; cards only when expanded
        case compact
    }

    public enum Grouping: String, Codable, CaseIterable, Sendable {
        case day, priority, source, none
    }

    /// Which read updates the feed keeps, under Earlier
    public enum ReadUpdates: String, Codable, CaseIterable, Sendable {
        case week, month, all, none

        /// The oldest a read update can be found and still show; nil for no limit
        func cutoff(now: Date) -> Date? {
            switch self {
            case .week: now.addingTimeInterval(-7 * 86400)
            case .month: now.addingTimeInterval(-30 * 86400)
            case .all, .none: nil
            }
        }
    }

    public enum Times: String, Codable, CaseIterable, Sendable {
        case relative, absolute
    }

    public var density: Density = .comfortable
    public var grouping: Grouping = .day
    /// Read updates join their group, after unread; by day, under Earlier
    public var groupsReadUpdates = true
    public var showsLowPriority = true
    public var readUpdates: ReadUpdates = .month
    /// The first lines of the notes on cards
    public var previews = true
    public var times: Times = .relative

    public init() {}

    public static let standard = ViewOptions()

    // Options added later decode as their defaults
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        density = (try? container.decode(Density.self, forKey: .density)) ?? .comfortable
        grouping = (try? container.decode(Grouping.self, forKey: .grouping)) ?? .day
        groupsReadUpdates = (try? container.decode(Bool.self, forKey: .groupsReadUpdates)) ?? true
        showsLowPriority = (try? container.decode(Bool.self, forKey: .showsLowPriority)) ?? true
        readUpdates = (try? container.decode(ReadUpdates.self, forKey: .readUpdates)) ?? .month
        previews = (try? container.decode(Bool.self, forKey: .previews)) ?? true
        times = (try? container.decode(Times.self, forKey: .times)) ?? .relative
    }
}

extension ViewOptions.Density {
    public var title: String {
        switch self {
        case .comfortable: String(localized: "Comfortable")
        case .compact: String(localized: "Compact")
        }
    }
}

extension ViewOptions.Grouping {
    public var title: String {
        switch self {
        case .day: String(localized: "Day")
        case .priority: String(localized: "Priority")
        case .source: String(localized: "Source")
        case .none: String(localized: "None")
        }
    }
}

extension ViewOptions.ReadUpdates {
    public var title: String {
        switch self {
        case .week: String(localized: "Last 7 Days")
        case .month: String(localized: "Last 30 Days")
        case .all: String(localized: "All")
        case .none: String(localized: "None")
        }
    }
}

extension ViewOptions.Times {
    public var title: String {
        switch self {
        case .relative: String(localized: "Relative")
        case .absolute: String(localized: "Absolute")
        }
    }
}
