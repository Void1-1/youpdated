import Foundation

@MainActor
public protocol AppPlatform: AnyObject {
    func post(_ notifications: [NotificationContent])
    func schedule(_ notifications: [NotificationContent], at date: Date)
    /// Withdraws delivered notifications for these items
    func withdraw(itemIds: [String])
    /// The passphrase stored by "Remember on this computer"
    func storedPassphrase() -> String?
    /// Stores it, or deletes it for nil
    func storePassphrase(_ passphrase: String?)
    func open(_ url: URL)
    /// Where `snapshot.write` should put the widget's snapshot; nil without an app group
    var snapshotURL: URL? { get }
    /// After a new snapshot, so the widget reloads
    func snapshotWritten()
}

/// State that lives with the app rather than the engine
@MainActor
public final class AppDefaults {
    private let defaults: UserDefaults

    public init(_ defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public var pause: Pause {
        get {
            defaults.data(forKey: "pause").flatMap { try? JSONDecoder().decode(Pause.self, from: $0) } ?? .none
        }
        set {
            defaults.set(try? JSONEncoder().encode(newValue), forKey: "pause")
        }
    }

    public var lastDigest: Date? {
        get { defaults.object(forKey: "lastDigest") as? Date }
        set { defaults.set(newValue, forKey: "lastDigest") }
    }

    /// If the first launch's choices (Start at login) have been made
    public var launchedBefore: Bool {
        get { defaults.bool(forKey: "launchedBefore") }
        set { defaults.set(newValue, forKey: "launchedBefore") }
    }

    /// Settings → Advanced
    public var hidesSelfWatchSuggestions: Bool {
        get { defaults.bool(forKey: "hidesSelfWatchSuggestions") }
        set { defaults.set(newValue, forKey: "hidesSelfWatchSuggestions") }
    }

    /// Settings → View
    public var viewOptions: ViewOptions {
        get {
            defaults.data(forKey: "viewOptions").flatMap { try? JSONDecoder().decode(ViewOptions.self, from: $0) } ?? .standard
        }
        set {
            defaults.set(try? JSONEncoder().encode(newValue), forKey: "viewOptions")
        }
    }
}
