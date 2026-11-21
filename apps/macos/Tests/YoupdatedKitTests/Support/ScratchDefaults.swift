import Foundation

/// A defaults suite of its own, in a temporary folder that's removed with this
final class ScratchDefaults: @unchecked Sendable {
    let folder = FileManager.default.temporaryDirectory.appending(path: "youpdated-defaults-\(UUID().uuidString)")
    let defaults: UserDefaults

    init() {
        // a suite named by an absolute path is kept at that path
        defaults = UserDefaults(suiteName: folder.appending(path: "defaults").path)!
    }

    deinit {
        try? FileManager.default.removeItem(at: folder)
    }
}
