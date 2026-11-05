import AppKit
import Observation
import Security
import ServiceManagement
import WidgetKit
import YoupdatedKit
import YoupdatedUI

/// `AppPlatform` on macOS
@MainActor
final class MacPlatform: AppPlatform {
    let notifications = Notifications()

    func post(_ notifications: [NotificationContent]) {
        self.notifications.post(notifications)
    }

    func schedule(_ notifications: [NotificationContent], at date: Date) {
        self.notifications.post(notifications, at: date)
    }

    func withdraw(itemIds: [String]) {
        notifications.withdraw(itemIds: itemIds)
    }

    func storedPassphrase() -> String? {
        Keychain.read()
    }

    func storePassphrase(_ passphrase: String?) {
        if let passphrase {
            Keychain.write(passphrase)
        } else {
            Keychain.delete()
        }
    }

    func open(_ url: URL) {
        NSWorkspace.shared.open(url)
    }

    func snapshotWritten() {
        WidgetCenter.shared.reloadAllTimelines()
    }

    /// In the app group, for the widget
    var snapshotURL: URL? {
        guard let group = ShareQueue.appGroup(),
              let container = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group)
        else { return nil }
        return container.appending(path: WidgetSnapshot.fileName)
    }
}

/// Tells the model when the config changes
@MainActor
final class ConfigWatcher {
    private var source: DispatchSourceFileSystemObject?
    private var path: String?
    private var modified: Date?
    var changed: (() -> Void)?

    func watch(_ path: String?) {
        guard path != self.path else { return }
        source?.cancel()
        source = nil
        self.path = path
        guard let path else { return }
        modified = Self.modification(path)
        let folder = (path as NSString).deletingLastPathComponent
        let descriptor = open(folder, O_EVTONLY)
        guard descriptor >= 0 else { return }
        let source = DispatchSource.makeFileSystemObjectSource(fileDescriptor: descriptor, eventMask: [.write, .rename, .delete], queue: .main)
        source.setEventHandler { [weak self] in
            MainActor.assumeIsolated { self?.check() }
        }
        source.setCancelHandler { close(descriptor) }
        source.resume()
        self.source = source
    }

    private func check() {
        guard let path else { return }
        let now = Self.modification(path)
        if now != modified {
            modified = now
            changed?()
        }
    }

    private static func modification(_ path: String) -> Date? {
        (try? FileManager.default.attributesOfItem(atPath: path))?[.modificationDate] as? Date
    }
}
