import Foundation
import Testing
@testable import YoupdatedKit

@MainActor
@Suite struct FiltersTests {
    static let offered = ["v*", "app-macos-v*", "app-windows-v*"]

    // draft

    @Test func everyVersionSendsNoPatterns() {
        var draft = FilterDraft()
        draft.ticked = ["v*"]
        draft.custom = "*-lts"
        #expect(draft.match(offered: Self.offered) == [])
    }

    @Test func tickedPatternsComeInTheOrderOfferedThenTypedOnes() {
        var draft = FilterDraft()
        draft.limitsVersions = true
        draft.ticked = ["app-windows-v*", "v*"]
        draft.custom = " *-lts ,, v*, nightly "
        #expect(draft.match(offered: Self.offered) == ["v*", "app-windows-v*", "*-lts", "nightly"])
    }

    @Test func loadedPatternsBecomeTicksOnceTheyAreOffered() throws {
        let filters: Filters = try Fixtures.reply("filters.get/ok")
        var draft = FilterDraft(filters)
        #expect(draft.limitsVersions && draft.custom == "app-macos-v*" && draft.ticked.isEmpty)
        #expect(draft.skipPrereleases && draft.bump == .any && draft.minimum == nil)

        draft.custom += ", *-lts"
        draft.sort(offered: Self.offered)
        #expect(draft.ticked == ["app-macos-v*"] && draft.custom == "*-lts")
        #expect(draft.match(offered: Self.offered) == ["app-macos-v*", "*-lts"])
    }

    @Test func aBlankMinimumIsNone() {
        var draft = FilterDraft()
        draft.min = "  "
        #expect(draft.minimum == nil)
        draft.min = " 3.12 "
        #expect(draft.minimum == "3.12")
    }

    // sheet

    private func sheet(_ configure: (FakeEngine) -> Void = { _ in }) async throws -> (Harness, FiltersModel) {
        let harness = Harness(configure: configure)
        await harness.model.launch()
        let target = try #require(harness.model.sources.selfTarget)
        let filters = try #require(harness.model.sources.filters(for: target))
        filters.settle = .zero
        return (harness, filters)
    }

    @Test func theEngineSaysWhetherItFilters() async throws {
        let (harness, _) = try await sheet()
        #expect(harness.model.sources.canFilter)

        var hello = Fixtures.named("hello/ok").reply
        hello["ops"] = (hello["ops"] as? [String])?.filter { !$0.hasPrefix("filters.") }
        let older = Harness { $0.answer("hello", reply: hello) }
        await older.model.launch()
        #expect(!older.model.sources.canFilter)
    }

    @Test func loadingShowsTheRulesAndWhatTheyKeep() async throws {
        let (harness, filters) = try await sheet()
        await filters.load()

        #expect(filters.phase == .ready && filters.isEditable)
        #expect(filters.draft.ticked == ["app-macos-v*"] && filters.draft.custom.isEmpty)
        #expect(filters.match == ["app-macos-v*"])
        #expect(filters.preview?.kept == 2)
        #expect(!filters.hasChanges && !filters.canSave)
        let asked = try #require(harness.engine.requests("filters.preview").last)
        #expect(asked["match"] as? [String] == ["app-macos-v*"])
        #expect(asked["skip_prereleases"] as? Bool == true)
        #expect(asked["refresh"] == nil && asked["min"] == nil && asked["bump"] == nil)
    }

    @Test func aChangedDraftIsPreviewedAndCanBeSaved() async throws {
        let (harness, filters) = try await sheet()
        await filters.load()

        filters.draft.ticked.insert("v*")
        filters.draft.min = "1.0"
        filters.draft.bump = .minor
        filters.draftChanged()
        for _ in 0..<50 where harness.engine.requests("filters.preview").count < 2 { await Task.yield() }
        try await Task.sleep(for: .milliseconds(20))
        let asked = try #require(harness.engine.requests("filters.preview").last)
        #expect(asked["match"] as? [String] == ["v*", "app-macos-v*"])
        #expect(asked["min"] as? String == "1.0" && asked["bump"] as? String == "minor")
        #expect(filters.canSave)

        #expect(await filters.save())
        let sent = try #require(harness.engine.requests("filters.set").last)
        #expect(sent["source"] as? String == "git" && sent["key"] as? String == "github.com/Void1-1/youpdated")
        #expect(sent["match"] as? [String] == ["v*", "app-macos-v*"])
        #expect(sent["bump"] as? String == "minor" && sent["min"] as? String == "1.0")
    }

    @Test func patternsSavedInAnotherOrderAreNotAChange() async throws {
        var saved = Fixtures.named("filters.get/ok").reply
        saved["match"] = ["app-macos-v*", "v*"]
        let (harness, filters) = try await sheet { $0.answer("filters.get", reply: saved) }
        await filters.load()

        #expect(filters.match == ["v*", "app-macos-v*"])
        #expect(!filters.hasChanges && !filters.canSave)
        filters.draft.ticked.remove("v*")
        #expect(filters.hasChanges)
        _ = harness
    }

    @Test func aRefusedSaveKeepsTheSheetOpenAndSaysWhy() async throws {
        let (harness, filters) = try await sheet { $0.answer("filters.set", with: "filters.set/bad_min") }
        await filters.load()
        filters.draft.min = "latest"

        #expect(await filters.save() == false)
        #expect(filters.phase == .ready)
        #expect(filters.problem?.contains("must be a version number") == true)
        filters.draft.min = "2"
        #expect(filters.problem == nil)
        _ = harness
    }

    @Test func anItemListedTwiceIsReadOnly() async throws {
        let (harness, filters) = try await sheet { $0.answer("filters.get", with: "filters.get/listed_twice") }
        await filters.load()
        filters.draft.bump = .major
        #expect(!filters.isEditable && !filters.canSave)
        #expect(filters.saved?.reason?.contains("multiple times") == true)
        _ = harness
    }

    @Test func refreshAsksTheSourceAgain() async throws {
        let (harness, filters) = try await sheet()
        await filters.load()
        await filters.refresh()
        #expect(harness.engine.requests("filters.preview").last?["refresh"] as? Bool == true)
    }

    @Test func onlyVersionsLikeWithNothingPickedLimitsNothing() async throws {
        let (harness, filters) = try await sheet { $0.answer("filters.preview", with: "filters.preview/fetch_failed") }
        await filters.load()
        #expect(filters.offered.isEmpty && filters.draft.custom == "app-macos-v*")
        filters.draft.custom = ""
        #expect(filters.limitsNothing)
        filters.draft.limitsVersions = false
        #expect(!filters.limitsNothing && filters.hasChanges)
        _ = harness
    }

    // general wording

    @Test func thePreviewLineCountsWhatFits() throws {
        var preview: FiltersPreview = try Fixtures.reply("filters.preview/ok")
        #expect(FiltersText.fits(preview) == "2 of the last 6 versions fit: app-macos-v1.0.0, app-macos-v0.9.1")
        preview.kept = 0
        #expect(FiltersText.fits(preview) == "None of the last 6 versions fit.")
        preview.kept = 6
        #expect(FiltersText.fits(preview) == "All of the last 6 versions fit.")
        preview.versions = []
        #expect(FiltersText.fits(preview) == "It lists no versions to filter.")
    }
}
