import AppKit
import UserNotifications
import YoupdatedKit

/// Posts the engine's alerts through Notification Center, with Open, Mark Read, and Snooze
@MainActor
final class Notifications: NSObject, UNUserNotificationCenterDelegate {
    static let category = "update"
    static let appUpdateCategory = "app-update"

    enum Action: String {
        case open = "open"
        case markRead = "mark-read"
        case snooze = "snooze"
        case download = "download"
    }

    private let center = UNUserNotificationCenter.current()
    /// The app answers buttons; `download` is nil for an ordinary item
    var respond: ((AppModel.NotificationAction, [String], URL?) async -> Date?)?
    var download: (() -> Void)?
    var showInbox: (() -> Void)?
    private var authorized: Bool?

    func start() {
        center.delegate = self
        let open = UNNotificationAction(identifier: Action.open.rawValue, title: String(localized: "Open"), options: [.foreground])
        let read = UNNotificationAction(identifier: Action.markRead.rawValue, title: String(localized: "Mark as Read"))
        let snooze = UNNotificationAction(identifier: Action.snooze.rawValue, title: String(localized: "Snooze 1 Hour"))
        let download = UNNotificationAction(identifier: Action.download.rawValue, title: String(localized: "Download"), options: [.foreground])
        center.setNotificationCategories([
            UNNotificationCategory(identifier: Self.category, actions: [open, read, snooze], intentIdentifiers: []),
            UNNotificationCategory(identifier: Self.appUpdateCategory, actions: [download, snooze], intentIdentifiers: []),
        ])
    }

    /// Asks once, on the first time
    private func authorize() async -> Bool {
        if let authorized { return authorized }
        let granted = (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        authorized = granted
        return granted
    }

    func post(_ contents: [NotificationContent], at date: Date? = nil) {
        guard !contents.isEmpty else { return }
        Task {
            guard await authorize() else { return }
            for content in contents {
                try? await center.add(request(for: content, at: date))
            }
        }
    }

    func withdraw(itemIds: [String]) {
        let ids = Set(itemIds)
        Task {
            let delivered = await center.deliveredNotifications()
            let matching = delivered.filter { notification in
                let shown = notification.request.content.userInfo["ids"] as? [String] ?? []
                return !ids.isDisjoint(with: shown)
            }
            center.removeDeliveredNotifications(withIdentifiers: matching.map(\.request.identifier))
        }
    }

    private func request(for content: NotificationContent, at date: Date?) -> UNNotificationRequest {
        let body = UNMutableNotificationContent()
        body.title = content.title
        if let subtitle = content.subtitle { body.subtitle = subtitle }
        body.body = content.body
        body.threadIdentifier = content.threadIdentifier
        body.categoryIdentifier = content.isAppUpdate ? Self.appUpdateCategory : Self.category
        body.sound = .default
        var info: [String: Any] = ["ids": content.itemIds, "appUpdate": content.isAppUpdate]
        if let url = content.url { info["url"] = url.absoluteString }
        body.userInfo = info
        let trigger = date.map {
            UNTimeIntervalNotificationTrigger(timeInterval: max(1, $0.timeIntervalSinceNow), repeats: false)
        }
        return UNNotificationRequest(identifier: content.identifier, content: body, trigger: trigger)
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .list, .sound]
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse
    ) async {
        let request = response.notification.request
        let info = request.content.userInfo
        let ids = info["ids"] as? [String] ?? []
        let url = (info["url"] as? String).flatMap(URL.init(string:))
        let isAppUpdate = info["appUpdate"] as? Bool ?? false
        let action = response.actionIdentifier
        let shown = request.content
        // the same notification again, for a snooze
        let again = NotificationContent(
            identifier: "snoozed-\(request.identifier)", title: shown.title,
            subtitle: shown.subtitle.isEmpty ? nil : shown.subtitle, body: shown.body,
            threadIdentifier: shown.threadIdentifier, itemIds: ids, url: url, isAppUpdate: isAppUpdate)
        await MainActor.run {
            self.handle(action: action, ids: ids, url: url, isAppUpdate: isAppUpdate, content: again)
        }
    }

    private func handle(action: String, ids: [String], url: URL?, isAppUpdate: Bool, content: NotificationContent) {
        Task {
            switch action {
            case Action.download.rawValue:
                download?()
            case Action.markRead.rawValue:
                _ = await respond?(.markRead, ids, url)
            case Action.snooze.rawValue:
                if let until = await respond?(.snooze, ids, url) {
                    post([content], at: until)
                }
            case UNNotificationDismissActionIdentifier:
                break
            default:
                // a click on the notification, or Open
                if isAppUpdate {
                    download?()
                } else if url == nil {
                    showInbox?()
                } else {
                    _ = await respond?(.open, ids, url)
                }
            }
        }
    }
}
