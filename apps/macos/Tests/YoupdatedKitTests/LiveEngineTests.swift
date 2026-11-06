import Foundation
import Testing
@testable import YoupdatedKit

/// Uses a temporary config and history, and sends no real requests
@Suite(.enabled(if: ProcessInfo.processInfo.environment["YOUPDATED_LIVE_ENGINE"] != nil))
struct LiveEngineTests {
    @Test func firstLaunchAgainstTheRealEngine() async throws {
        let folder = FileManager.default.temporaryDirectory.appending(path: "youpdated-live-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let config = folder.appending(path: "config.yaml").path
        let environment = ProcessInfo.processInfo.environment.merging([
            "YOUPDATED_ENGINE": ProcessInfo.processInfo.environment["YOUPDATED_LIVE_ENGINE"]!,
            "YOUPDATED_ENGINE_CONFIG": config,
            "YOUPDATED_ENGINE_STATE": folder.appending(path: "state.sqlite3").path,
            // the engine in this checkout: Fixtures.root is <repo>/apps/contract/v1/fixtures
            "PYTHONPATH": Fixtures.root.deletingLastPathComponent().deletingLastPathComponent()
                .deletingLastPathComponent().deletingLastPathComponent().path,
        ]) { _, new in new }
        let command = EngineCommand.resolve(bundle: .main, environment: environment)
        let client = EngineClient(transport: try ProcessTransport(command: command))
        await client.start()
        let engine = Engine(client: client)

        let hello = try await engine.hello(ClientInfo(version: "1.0.0"))
        #expect(hello.contract == ContractCoding.major)
        #expect(Set(hello.ops).isSuperset(of: ContractTests.replyTypes.keys))

        let first = try await engine.status()
        #expect(!first.configExists)
        #expect(!first.locked)

        let created = try await engine.initConfig(selfWatch: true)
        #expect(created.created && created.selfWatchAdded)
        #expect(created.configPath == config)

        let status = try await engine.status()
        #expect(status.selfWatch == .watching)
        let settings = try await engine.settings()
        #expect(settings == Settings.defaults)
        let changed = try await engine.setSettings(SettingsPatch(digestTime: .some("18:00")))
        #expect(changed.digestTime == "18:00")
        let off = try await engine.setSettings(SettingsPatch(digestTime: .some(nil)))
        #expect(off.digestTime == nil)

        let targets = try await engine.targets().targets
        #expect(targets.count == 1)
        #expect(targets.first?.starred == true)
        let set = try await engine.setPriority(.high, source: "git", key: try #require(targets.first?.key))
        #expect(set == .high)

        let page = try await engine.inboxList()
        #expect(page.items.isEmpty)
        #expect(page.cursor == nil)
        _ = try await engine.problems()

        let preview = try await engine.importPreview(.content("httpx>=0.27\nrich\n", format: "requirements.txt"))
        #expect(preview.add.first?.source == "pypi")

        do {
            _ = try await engine.preview(value: "hello there")
            Issue.record("expected unrecognized")
        } catch let error as EngineError {
            #expect(error.code == .unrecognized)
        }

        try await engine.shutdown()
        let exit = await client.waitForExit()
        #expect(exit.clean, "\(exit.stderr)")
    }
}
