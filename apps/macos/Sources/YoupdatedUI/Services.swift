import AppKit
import Observation
import Security
import ServiceManagement
import UserNotifications
import YoupdatedKit

/// The passphrase, as a generic password, if "Remember on this computer" is on
public enum Keychain {
    public static let service = Bundle.main.object(forInfoDictionaryKey: "YoupdatedDevelopment") as? Bool ?? false
        ? "Youpdated Dev" : "Youpdated"
    public static let account = "passphrase"

    private static var query: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    public static func read() -> String? {
        var item: CFTypeRef?
        var search = query
        search[kSecReturnData as String] = true
        search[kSecMatchLimit as String] = kSecMatchLimitOne
        guard SecItemCopyMatching(search as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    public static func write(_ passphrase: String) {
        let data = Data(passphrase.utf8)
        let update = [kSecValueData as String: data]
        if SecItemUpdate(query as CFDictionary, update as CFDictionary) == errSecItemNotFound {
            var add = query
            add[kSecValueData as String] = data
            add[kSecAttrLabel as String] = String(localized: "Youpdated passphrase")
            add[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlocked
            SecItemAdd(add as CFDictionary, nil)
        }
    }

    public static func delete() {
        SecItemDelete(query as CFDictionary)
    }

    public static var exists: Bool {
        var search = query
        search[kSecMatchLimit as String] = kSecMatchLimitOne
        return SecItemCopyMatching(search as CFDictionary, nil) == errSecSuccess
    }
}

/// Start at login, through `SMAppService`
@MainActor
@Observable
public final class LoginItem {
    public private(set) var enabled = SMAppService.mainApp.status == .enabled
    public private(set) var needsApproval = SMAppService.mainApp.status == .requiresApproval
    public private(set) var error: String?

    public init() {}

    public func set(_ on: Bool) {
        error = nil
        do {
            if on {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            self.error = error.localizedDescription
        }
        refresh()
    }

    public func refresh() {
        enabled = SMAppService.mainApp.status == .enabled
        needsApproval = SMAppService.mainApp.status == .requiresApproval
    }

    public func openSystemSettings() {
        SMAppService.openSystemSettingsLoginItems()
    }
}

/// Fetches this platform's file from an app release and opens it
@MainActor
@Observable
public final class Downloader {
    public enum State: Equatable {
        case idle
        case downloading(String)
        case done(URL)
        case failed(String)
    }

    public private(set) var state: State = .idle

    public init() {}

    public func download(_ update: AppUpdate) {
        if case .downloading = state { return }
        state = .downloading(update.version)
        Task {
            do {
                let (temporary, response) = try await URLSession.shared.download(from: update.downloadURL)
                if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                    throw URLError(.badServerResponse)
                }
                let downloads = try FileManager.default.url(for: .downloadsDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
                let destination = Self.unique(downloads.appending(path: AppUpdate.assetName(version: update.version)))
                try FileManager.default.moveItem(at: temporary, to: destination)
                state = .done(destination)
                NSWorkspace.shared.open(destination)
            } catch {
                state = .failed(error.localizedDescription)
                // the release page always has the file
                if let page = update.releaseURL { NSWorkspace.shared.open(page) }
            }
        }
    }

    private static func unique(_ url: URL) -> URL {
        var candidate = url
        var index = 2
        let base = url.deletingPathExtension().lastPathComponent
        while FileManager.default.fileExists(atPath: candidate.path) {
            candidate = url.deletingLastPathComponent().appending(path: "\(base) \(index).\(url.pathExtension)")
            index += 1
        }
        return candidate
    }
}

/// Settings → Advanced → Remove Youpdated from this Mac
@MainActor
@Observable
public final class Removal {
    public enum State: Equatable {
        case idle
        case removing
        /// `dataRemoved` is nil when the config and history were kept
        case removed(dataRemoved: Bool?)
    }

    public private(set) var state: State = .idle
    public let canRemoveData = EngineCommand.uninstall() != nil
    public let canTrashApp = !(Bundle.main.object(forInfoDictionaryKey: "YoupdatedDevelopment") as? Bool ?? false)

    public init() {}

    /// Stops checks and the engine, then clears what the app set up in macOS, and the config and history if asked
    public func remove(model: AppModel, alsoData: Bool) async {
        guard state == .idle else { return }
        state = .removing
        await model.quit()
        if SMAppService.mainApp.status != .notRegistered {
            try? await SMAppService.mainApp.unregister()
        }
        let center = UNUserNotificationCenter.current()
        center.removeAllDeliveredNotifications()
        center.removeAllPendingNotificationRequests()
        Keychain.delete()
        var dataRemoved: Bool?
        if alsoData, let command = EngineCommand.uninstall() {
            dataRemoved = await command.run()
        }
        state = .removed(dataRemoved: dataRemoved)
    }

    /// Set once Quit is chosen, so the done alert doesn't come back while quitting
    public private(set) var quitting = false

    public func quit(trash: Bool) {
        guard !quitting else { return }
        quitting = true
        Task {
            if trash && canTrashApp {
                let app = Bundle.main.bundleURL
                do {
                    _ = try await NSWorkspace.shared.recycle([app])
                } catch {
                    let alert = NSAlert()
                    alert.alertStyle = .warning
                    alert.messageText = String(localized: "Youpdated couldn't be moved to the Trash")
                    alert.informativeText = String(localized: "It's still at \(app.path). Drag it to the Trash to finish removing it.")
                    alert.addButton(withTitle: String(localized: "Show in Finder and Quit"))
                    alert.addButton(withTitle: String(localized: "Quit"))
                    if alert.runModal() == .alertFirstButtonReturn {
                        NSWorkspace.shared.activateFileViewerSelecting([app])
                    }
                }
            }
            if let id = Bundle.main.bundleIdentifier {
                AppTraces.remove(home: FileManager.default.homeDirectoryForCurrentUser, bundleId: id)
                UserDefaults.standard.removePersistentDomain(forName: id)
            }
            exit(0)
        }
    }
}
