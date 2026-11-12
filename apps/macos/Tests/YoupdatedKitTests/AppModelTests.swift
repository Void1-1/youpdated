import Foundation
import Testing
@testable import YoupdatedKit

@MainActor
@Suite struct AppModelTests {
    @Test func launchLoadsEverything() async throws {
        let harness = Harness()
        await harness.model.launch()
        let model = harness.model
        #expect(model.engineState == .ready)
        #expect(harness.engine.ops == ["hello", "status", "settings.get", "problems.list", "inbox.list", "targets.list"])
        #expect(model.engineVersion == "1.0.0")
        #expect(model.inbox.items.count == 6)
        #expect(model.unread == 4)
        #expect(model.sources.targets.first?.starred == true)
        #expect(model.problems.count == 2)
        #expect(model.menuBarState == .problems)
        #expect(model.appUpdate?.version == "1.1.0")
        #expect(model.recentUnread().map(\.target.display).first == "Void1-1/youpdated")
        // last run 08:00:06 by the app, every 6 hours: nothing due when launched
        #expect(model.nextCheck == ContractCoding.parseTimestamp("2026-10-29T14:00:06Z"))
    }

    @Test func refusesAnotherContract() async {
        let harness = Harness { $0.answer("hello", reply: [
            "ok": true, "engine": "2.0.0", "contract": 2, "ops": [], "commit": NSNull(), "encryption": true,
        ]) }
        await harness.model.launch()
        guard case .failed(let message) = harness.model.engineState else {
            Issue.record("expected the engine to be refused")
            return
        }
        #expect(message.contains("contract 2"))
        #expect(harness.engine.ops == ["hello", "shutdown"])
        #expect(harness.model.menuBarState == .problems)
    }

    @Test func firstLaunchCreatesTheConfigAndChecks() async {
        let harness = Harness {
            $0.script("status", "status/first_launch")
            $0.answer("status", reply: statusReply(["last_run": NSNull(), "last_run_by": NSNull(), "counts": NSNull()]))
        }
        await harness.model.launch()
        let ops = harness.engine.ops
        #expect(ops.prefix(3) == ["hello", "status", "config.init"])
        #expect(harness.model.isFirstRun)
        #expect(harness.engine.requests("config.init").first?["self_watch"] as? Bool == true)
        #expect(ops.contains("check"))
    }

    @Test func lockedAsksForThePassphrase() async {
        let harness = Harness { $0.script("status", "status/locked") }
        await harness.model.launch()
        #expect(harness.model.needsPassphrase)
        #expect(harness.model.menuBarState == .locked)
        #expect(harness.engine.ops == ["hello", "status"])
    }

    @Test func wrongPassphraseSaysSo() async {
        let harness = Harness {
            $0.script("status", "status/locked")
            $0.script("unlock", "unlock/wrong_passphrase")
        }
        await harness.model.launch()
        let unlocked = await harness.model.unlock("wrong", remember: true)
        #expect(!unlocked)
        #expect(harness.model.needsPassphrase)
        #expect(harness.model.passphraseError != nil)
        #expect(harness.platform.passphrase == nil)
    }

    @Test func rememberedPassphraseUnlocksAtLaunch() async {
        let harness = Harness { $0.script("status", "status/locked", "status/ok") }
        harness.platform.passphrase = "correct horse"
        await harness.model.launch()
        #expect(!harness.model.needsPassphrase)
        #expect(harness.engine.requests("unlock").first?["passphrase"] as? String == "correct horse")
        #expect(harness.engine.ops.contains("inbox.list"))
    }

    @Test func staleRememberedPassphraseIsForgotten() async {
        let harness = Harness {
            $0.script("status", "status/locked")
            $0.script("unlock", "unlock/wrong_passphrase")
        }
        harness.platform.passphrase = "old"
        await harness.model.launch()
        #expect(harness.model.needsPassphrase)
        #expect(harness.platform.passphrase == nil)
        #expect(harness.model.passphraseError == nil)
    }

    @Test func unlockRemembersOnlyWhenAsked() async {
        let harness = Harness { $0.script("status", "status/locked", "status/ok") }
        await harness.model.launch()
        #expect(await harness.model.unlock("correct horse", remember: false))
        #expect(harness.platform.passphrase == nil)
        #expect(!harness.model.needsPassphrase)
        #expect(harness.model.inbox.loaded)
    }

    @Test func checkPostsAlertsAndRecordsProblems() async {
        let harness = Harness()
        await harness.model.launch()
        await harness.model.checkNow()
        #expect(harness.platform.posted.map(\.title) == ["python/cpython", "Youpdated 1.1.0 is available", "Team Fortress 2"])
        #expect(harness.model.problems.map(\.kind) == [.source, .notify])
        #expect(harness.model.lastCheck == ContractCoding.parseTimestamp("2026-10-29T08:00:06Z"))
        #expect(!harness.model.isChecking)
        #expect(Array(harness.engine.ops.suffix(4)) == ["status", "check", "inbox.list", "targets.list"])
    }

    @Test func historyHeldByTheCLIRetriesLater() async {
        let harness = Harness { $0.script("check", "check/history_busy") }
        await harness.model.launch()
        await harness.model.checkNow()
        #expect(harness.model.appProblems.isEmpty)
        #expect(harness.model.secondsUntilNextTick() <= 300)
        harness.clock.advance(301)
        await harness.model.tick()
        #expect(harness.engine.requests("check").count == 2)
    }

    @Test func busyIsNotAProblem() async {
        let harness = Harness { $0.script("check", "check/busy") }
        await harness.model.launch()
        await harness.model.checkNow()
        #expect(harness.model.appProblems.isEmpty)
    }

    @Test func cliRunsShowTheNoticeOnce() async {
        let harness = Harness { $0.answer("status", with: "status/cli_ran") }
        await harness.model.launch()
        #expect(harness.model.showsCLINotice)
        await harness.model.dismissCLINotice()
        #expect(!harness.model.showsCLINotice)
        #expect(harness.engine.requests("settings.set").last?["settings"] as? [String: Bool] == ["cli_notice_shown": true])
    }

    @Test func pauseAndResume() async {
        let harness = Harness()
        await harness.model.launch()
        harness.model.pause(.untilResumed)
        #expect(harness.model.menuBarState == .paused)
        #expect(harness.defaults.pause == .indefinitely)
        harness.clock.advance(86400)
        await harness.model.tick()
        #expect(harness.engine.requests("check").isEmpty)
        await harness.model.resume()
        #expect(harness.engine.requests("check").count == 1)
    }

    @Test func wakeChecksWhenDue() async {
        let harness = Harness()
        await harness.model.launch()
        await harness.model.didWake()
        #expect(harness.engine.requests("check").isEmpty)
        harness.clock.advance(6 * 3600)
        await harness.model.didWake()
        #expect(harness.engine.requests("check").count == 1)
    }

    @Test func digestGoesOutAtItsTimeAndAfterWake() async {
        let harness = Harness()
        await harness.model.launch()
        await harness.model.update(SettingsPatch(digestTime: .some("18:00")))
        #expect(harness.engine.requests("settings.set").last?["settings"] as? [String: String] == ["digest_time": "18:00"])
        // the fixture's reply turns it on
        #expect(harness.model.settings.digestTime == "18:00")
        await harness.model.tick()
        #expect(harness.engine.requests("digest.send").isEmpty)
        harness.clock.set("2026-10-30T07:00:00Z")
        await harness.model.didWake()
        #expect(harness.engine.requests("digest.send").count == 1)
        #expect(harness.platform.posted.last?.title == "Team Fortress 2")
    }

    @Test func selfWatchOffer() async {
        let harness = Harness()
        harness.engine.answer("status", reply: statusReply(["self_watch": "absent"]))
        await harness.model.launch()
        #expect(harness.model.offersSelfWatch)
        await harness.model.declineSelfWatch()
        #expect(!harness.model.offersSelfWatch)
        #expect(harness.engine.requests("settings.set").last?["settings"] as? [String: Bool] == ["self_watch_declined": true])
    }

    @Test func hidingSelfWatchSuggestionsHidesTheOfferAndIsRemembered() async {
        let harness = Harness()
        harness.engine.answer("status", reply: statusReply(["self_watch": "absent"]))
        await harness.model.launch()
        #expect(harness.model.offersSelfWatch)
        harness.model.hidesSelfWatchSuggestions = true
        #expect(!harness.model.offersSelfWatch)
        #expect(harness.model.defaults.hidesSelfWatchSuggestions)
        // nothing is declined for the CLI or the other app
        #expect(harness.engine.requests("settings.set").isEmpty)
        harness.model.hidesSelfWatchSuggestions = false
        #expect(harness.model.offersSelfWatch)
    }

    @Test func deletingACardsUpdatesRemovesThemForGood() async {
        let harness = Harness()
        await harness.model.launch()
        #expect(harness.model.inbox.canDelete)
        let before = harness.model.inbox.items.count
        await harness.model.inbox.delete(ids: ["i_8f3a1c2e9b7d4a10"])
        #expect(harness.model.inbox.items.count == before - 1)
        #expect(!harness.model.inbox.items.contains { $0.id == "i_8f3a1c2e9b7d4a10" })
        #expect(harness.engine.requests("inbox.delete").last?["ids"] as? [String] == ["i_8f3a1c2e9b7d4a10"])
        #expect(harness.model.unread == 3)
        #expect(harness.platform.withdrawn == ["i_8f3a1c2e9b7d4a10"])
    }

    @Test func notificationButtons() async throws {
        let harness = Harness()
        await harness.model.launch()
        let url = URL(string: "https://example.com/x")!
        await harness.model.respond(.open, itemIds: ["i_8f3a1c2e9b7d4a10"], url: url)
        #expect(harness.platform.opened == [url])
        #expect(harness.engine.requests("inbox.mark").last?["ids"] as? [String] == ["i_8f3a1c2e9b7d4a10"])
        #expect(harness.model.inbox.items.first { $0.id == "i_8f3a1c2e9b7d4a10" }?.read == true)
        #expect(harness.platform.withdrawn == ["i_8f3a1c2e9b7d4a10"])
        let until = await harness.model.respond(.snooze, itemIds: ["i_2b9e5d7f0c1a3e44"], url: nil)
        #expect(until == ContractCoding.parseTimestamp("2026-10-29T09:05:00Z"))
        #expect(harness.engine.requests("inbox.snooze").last?["minutes"] as? Int == 60)
    }

    @Test func crashIsAProblemAndTheEngineRestarts() async throws {
        let first = FakeEngine()
        let second = FakeEngine()
        let engines = EngineQueue([first, second])
        let platform = FakePlatform()
        let model = AppModel(
            launch: { try engines.next() }, platform: platform,
            defaults: AppDefaults(UserDefaults(suiteName: "YoupdatedTests-\(UUID().uuidString)")!),
            appVersion: "1.0.0", now: { ContractCoding.parseTimestamp("2026-10-29T08:00:06Z")! },
            sleep: { _ in await Task.yield() })
        await model.launch()
        first.crash(stderr: "Traceback\nRuntimeError: boom")
        for _ in 0..<200 where second.ops.count < 6 {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(model.appProblems.count == 1)
        #expect(model.appProblems.first?.detail.contains("boom") == true)
        #expect(second.ops.prefix(2) == ["hello", "status"])
        #expect(model.engineState == .ready)
    }

    @Test func quitSendsShutdown() async {
        let harness = Harness()
        await harness.model.launch()
        await harness.model.quit()
        #expect(harness.engine.ops.last == "shutdown")
    }
}

@MainActor
@Suite struct SourcesFlowTests {
    @Test func pasteThenPreviewThenAddThenCheck() async throws {
        let harness = Harness()
        await harness.model.launch()
        let sources = harness.model.sources
        await sources.preview("  https://github.com/python/cpython ")
        guard case .ready(let value, let preview) = sources.add else {
            Issue.record("expected a preview, got \(sources.add)")
            return
        }
        #expect(value == "https://github.com/python/cpython")
        #expect(preview.newest?.title == "v3.14.7")
        await sources.confirmAdd()
        #expect(sources.add == .added([TargetRef(source: "git", key: "github.com/python/cpython", display: "python/cpython")]))
        let add = try #require(harness.engine.requests("targets.add").first)
        #expect(add["source"] as? String == "git")
        #expect(add["entries"] as? [String] == ["python/cpython"])
        // the new target is checked at once, so its newest item shows
        let check = try #require(harness.engine.requests("check").last)
        #expect(check["targets"] as? [[String: String]] == [["source": "git", "key": "github.com/python/cpython"]])
    }

    @Test func ambiguousAsksWhichSource() async throws {
        let harness = Harness { $0.script("targets.preview", "targets.preview/ambiguous", "targets.preview/picked") }
        await harness.model.launch()
        let sources = harness.model.sources
        await sources.preview("express")
        #expect(sources.add == .choosing("express", choices: ["homebrew", "npm", "pypi"]))
        await sources.choose(source: "npm")
        guard case .ready(_, let preview) = sources.add else {
            Issue.record("expected a preview")
            return
        }
        #expect(preview.alreadyWatching)
        #expect(harness.engine.requests("targets.preview").last?["source"] as? String == "npm")
    }

    @Test func unrecognizedFails() async {
        let harness = Harness { $0.script("targets.preview", "targets.preview/unrecognized") }
        await harness.model.launch()
        await harness.model.sources.preview("hello there")
        guard case .failed(_, let message) = harness.model.sources.add else {
            Issue.record("expected a failure")
            return
        }
        #expect(message.contains("could not be determined"))
    }

    @Test func removeCanForgetHistory() async throws {
        let harness = Harness()
        await harness.model.launch()
        let target = try #require(harness.model.sources.targets.first { $0.source == "steam" })
        await harness.model.sources.remove(target, wipe: true)
        let remove = try #require(harness.engine.requests("targets.remove").first)
        #expect(remove["wipe"] as? Bool == true)
        #expect(remove["key"] as? String == "440")
        await harness.model.sources.remove(target, wipe: false)
        #expect(harness.engine.requests("targets.remove").last?["wipe"] == nil)
    }

    @Test func importPreviewThenApply() async throws {
        let harness = Harness { $0.answer("import.preview", with: "import.preview/path") }
        await harness.model.launch()
        let sources = harness.model.sources
        await sources.previewImport(.path("/Users/me/project/package.json"))
        guard case .ready(_, let preview) = sources.importing else {
            Issue.record("expected an import preview")
            return
        }
        #expect(preview.count == 2)
        #expect(preview.skipped.first?.what == "my-lib")
        await sources.confirmImport()
        #expect(sources.importing == .done(ImportApplyReply.Summary(added: 2, skipped: 1)))
        let check = try #require(harness.engine.requests("check").last)
        #expect((check["targets"] as? [[String: String]])?.count == 2)
    }

    @Test func importFormatsFromFileNames() {
        #expect(ImportInput.format(forFileName: "package.json") == "package.json")
        #expect(ImportInput.format(forFileName: "requirements-dev.txt") == "requirements.txt")
        #expect(ImportInput.format(forFileName: "Brewfile") == "Brewfile")
        #expect(ImportInput.format(forFileName: "notes.txt") == nil)
    }

    @Test func inboxMarksAndPriorities() async throws {
        let harness = Harness()
        await harness.model.launch()
        let inbox = harness.model.inbox
        let row = try #require(InboxModel.sections(inbox.items, sort: .newest, source: nil).first?.rows.first { $0.target.source == "steam" })
        let url = await inbox.open(row)
        #expect(url?.absoluteString == "https://store.steampowered.com/news/app/440/view/5001")
        #expect(InboxModel.sections(inbox.items, sort: .newest, source: nil).first?.rows.contains { $0.target.source == "steam" } == false)
        await inbox.setPriority(.high, for: row.target.targetKey)
        #expect(harness.engine.requests("priority.set").last?["priority"] as? String == "high")
        #expect(inbox.items.filter { $0.source == "steam" }.allSatisfy { $0.priority == .high })
        await inbox.markAllRead()
        #expect(harness.engine.requests("inbox.mark").last?["all"] as? Bool == true)
        #expect(inbox.unreadCount == 0)
    }
}

final class EngineQueue: @unchecked Sendable {
    private let lock = NSLock()
    private var engines: [FakeEngine]

    init(_ engines: [FakeEngine]) { self.engines = engines }

    func next() throws -> FakeEngine {
        try lock.withLock {
            guard !engines.isEmpty else { throw EngineLaunchError.failed("no more engines") }
            return engines.removeFirst()
        }
    }
}

/// `status/ok` with some fields changed
func statusReply(_ changes: [String: Any]) -> [String: Any] {
    Fixtures.named("status/ok").reply.merging(changes) { _, new in new }
}
