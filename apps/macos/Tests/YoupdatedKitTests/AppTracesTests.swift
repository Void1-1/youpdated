import Foundation
import Testing
@testable import YoupdatedKit

@Suite struct AppTracesTests {
    private let id = "io.github.void1-1.Youpdated"

    @Test func removesTheAppsOwnFilesAndNothingElse() throws {
        let home = FileManager.default.temporaryDirectory.appending(path: "youpdated-home-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: home) }
        let files = FileManager.default
        for url in AppTraces.paths(home: home, bundleId: id) {
            try files.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            if url.pathExtension == "binarycookies" {
                try Data("x".utf8).write(to: url)
            } else {
                try files.createDirectory(at: url, withIntermediateDirectories: true)
                try Data("x".utf8).write(to: url.appending(path: "inside"))
            }
        }
        let others = home.appending(path: "Library/Caches/com.example.Other")
        try files.createDirectory(at: others, withIntermediateDirectories: true)
        let logs = home.appending(path: "Library/Logs/Youpdated")
        let defaults = UserDefaults(suiteName: home.appending(path: "defaults").path)!
        let log = AppLog(folder: logs, defaults: defaults)
        log.enabled = true
        log.write("error")
        #expect(log.exists)

        AppTraces.remove(home: home, bundleId: id, log: log)

        for url in AppTraces.paths(home: home, bundleId: id) {
            #expect(!files.fileExists(atPath: url.path), "\(url.path)")
        }
        #expect(!files.fileExists(atPath: logs.path))
        #expect(files.fileExists(atPath: others.path))
        #expect(files.fileExists(atPath: home.appending(path: "Library/Caches").path))
    }

    @Test func keepsALogFolderWithOtherFiles() throws {
        let folder = FileManager.default.temporaryDirectory.appending(path: "youpdated-logs-\(UUID().uuidString)")
        defer {
            try? FileManager.default.removeItem(at: folder)
            try? FileManager.default.removeItem(atPath: folder.path + "-defaults.plist")
        }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try Data("mine".utf8).write(to: folder.appending(path: "notes.txt"))
        let log = AppLog(folder: folder, defaults: UserDefaults(suiteName: folder.path + "-defaults")!)
        log.enabled = true
        log.write("error")

        AppTraces.remove(home: folder.appending(path: "home"), bundleId: id, log: log)

        #expect(!log.exists)
        #expect(FileManager.default.fileExists(atPath: folder.appending(path: "notes.txt").path))
    }
}
