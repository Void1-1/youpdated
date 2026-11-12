import Foundation
import Testing
@testable import YoupdatedKit

@Suite struct AppLogTests {
    private func makeLog() -> (AppLog, URL) {
        let folder = FileManager.default.temporaryDirectory.appending(path: "youpdated-log-\(UUID().uuidString)")
        let defaults = UserDefaults(suiteName: "YoupdatedTests-\(UUID().uuidString)")!
        return (AppLog(folder: folder, defaults: defaults), folder)
    }

    @Test func writesNothingUntilTurnedOn() throws {
        let (log, folder) = makeLog()
        defer { try? FileManager.default.removeItem(at: folder) }
        log.write("not saved")
        #expect(!log.exists)
        log.enabled = true
        log.write("saved")
        log.write("engine: Traceback\n  boom")
        let text = try String(contentsOf: log.file, encoding: .utf8)
        #expect(!text.contains("not saved"))
        #expect(text.contains(" saved\n"))
        #expect(text.contains("engine: Traceback\n  boom\n"))
        let mode = try FileManager.default.attributesOfItem(atPath: log.file.path)[.posixPermissions] as? Int
        #expect(mode == 0o600)
    }

    @Test func startsOverPastTheLimit() throws {
        let (log, folder) = makeLog()
        defer { try? FileManager.default.removeItem(at: folder) }
        log.enabled = true
        log.write(String(repeating: "x", count: AppLog.limit + 1))
        log.write("after")
        let text = try String(contentsOf: log.file, encoding: .utf8)
        #expect(text.hasSuffix(" after\n"))
        #expect(text.count < 100)
        log.delete()
        #expect(!log.exists)
    }
}
