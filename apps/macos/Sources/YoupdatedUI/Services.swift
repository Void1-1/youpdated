import AppKit
import Observation
import Security
import ServiceManagement
import YoupdatedKit

/// The passphrase, as a generic password, if "Remember on this computer" is on
public enum Keychain {
    public static let service = "Youpdated"
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
