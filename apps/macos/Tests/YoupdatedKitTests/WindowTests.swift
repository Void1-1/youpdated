import Foundation
import Testing
@testable import YoupdatedKit

private func date(_ timestamp: String) -> Date { ContractCoding.parseTimestamp(timestamp)! }

/// Formatters put a narrow no-break space before AM and PM
private func plain(_ text: String?) -> String? { text?.replacingOccurrences(of: "\u{202F}", with: " ") }

private var utc: Calendar {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(identifier: "UTC")!
    calendar.locale = Locale(identifier: "en_US")
    return calendar
}

@Suite struct FeedTests {
    let items = (try! Fixtures.reply("inbox.list/ok", as: InboxPage.self)).items
    let now = date("2026-10-29T15:00:00Z")
    var update: AppUpdate { AppUpdate.available(in: items, current: "1.0.0")! }

    func build(_ options: ViewOptions = .standard, pinned: Set<String> = [], items: [InboxItem]? = nil,
               status: FeedStatus = FeedStatus(lastCheck: date("2026-10-29T08:00:06Z")), target: TargetKey? = nil) -> FeedLayout {
        Feed.build(items: items ?? self.items, options: options, target: target, pinned: pinned, update: update,
                   status: status, now: now, calendar: utc)
    }

    @Test func todayThenEarlierWithTheUpdatePinnedAbove() {
        let layout = build()
        #expect(layout.update?.version == "1.1.0")
        #expect(layout.sections.map(\.title) == ["Today", "Earlier"])
        // the app update has its own card, so it isn't in the sections too
        #expect(layout.sections[0].cards.map(\.target.display) == ["Team Fortress 2", "python/cpython", "react"])
        #expect(layout.sections[1].cards.map(\.target.display) == ["Bar", "Team Fortress 2"])
        #expect(layout.sections[1].cards.allSatisfy { $0.isHistory && !$0.isUnread })
    }

    @Test func theCountLeadsTheHeading() {
        let heading = build().heading
        #expect(heading.title == "4 new updates")
        #expect(plain(heading.line) == "Since this morning · Checked 8:00 AM")
        let read = items.map { item -> InboxItem in var copy = item; copy.read = true; return copy }
        let upToDate = build(items: read)
        #expect(upToDate.heading.upToDate)
        #expect(upToDate.heading.title == "You're up to date")
        #expect(plain(upToDate.heading.line) == "Checked 8:00 AM")
        #expect(upToDate.update == nil)
        #expect(upToDate.sections.map(\.title) == ["Earlier"])
    }

    @Test func checkingAndPausedJoinTheLine() {
        let checking = build(status: FeedStatus(checking: true, progress: ProgressEvent(done: 4, of: 15, target: nil, failed: nil)))
        #expect(checking.heading.line == "Since this morning · Checking… 4 of 15")
        let paused = build(status: FeedStatus(lastCheck: date("2026-10-29T08:00:06Z"), pause: .until(date("2026-10-29T16:00:00Z"))))
        #expect(plain(paused.heading.line) == "Since this morning · Checked 8:00 AM · Paused until 4:00 PM")
        #expect(paused.heading.parts.last.map { if case .paused = $0 { true } else { false } } == true)
        let resumed = build(status: FeedStatus(pause: .until(date("2026-10-29T14:00:00Z"))))
        #expect(!resumed.heading.parts.contains { if case .paused = $0 { true } else { false } })
        #expect(build(status: FeedStatus(pause: .indefinitely)).heading.parts.last == .paused("Paused"))
        let one = Feed.heading(unread: [items[1]], status: FeedStatus(), now: now, calendar: utc)
        #expect(one.title == "1 new update")
    }

    @Test func cardsLeadWithTheVersion() throws {
        let today = build().sections[0].cards
        let cpython = try #require(today.first { $0.target.source == "git" })
        #expect(cpython.lead == "3.14.7")
        #expect(cpython.leadIsVersion)
        #expect(cpython.label == "GitHub")
        // the title only repeats the version, and there are no notes
        #expect(cpython.line == nil)
        #expect(cpython.hasEdge)
        #expect(!cpython.isCompact)
        let tf2 = try #require(today.first { $0.target.source == "steam" })
        #expect(tf2.lead == "CLTF2 Halloween Cup")
        #expect(tf2.line == "Sign-ups for the Halloween cup are open until Friday.")
        let react = try #require(today.first { $0.target.source == "npm" })
        #expect(react.isCompact)
        #expect(react.isUnread)
    }

    @Test func readWhileOpenKeepsItsPlace() {
        var read = items
        let index = read.firstIndex { $0.id == "i_2b9e5d7f0c1a3e44" }!
        read[index].read = true
        let pinned = build(pinned: ["i_2b9e5d7f0c1a3e44"], items: read)
        let tf2 = pinned.sections[0].cards.first { $0.target.source == "steam" }
        #expect(tf2 != nil)
        #expect(tf2?.isUnread == false)
        #expect(tf2?.isCompact == false)
        let settled = build(items: read)
        #expect(!settled.sections[0].cards.contains { $0.target.source == "steam" })
        #expect(settled.sections.last?.cards.first { $0.target.source == "steam" }?.items.count == 2)
    }

    @Test func groupBy() {
        var options = ViewOptions()
        options.grouping = .priority
        let priority = build(options)
        #expect(!priority.sections.contains { $0.title == "Earlier" })
        // a group's read cards come after its unread ones
        for section in priority.sections {
            let read = section.cards.map(\.isHistory)
            #expect(read == read.sorted { !$0 && $1 })
        }
        options.grouping = .source
        #expect(!build(options).sections.contains { $0.title == "Earlier" })
        options.grouping = .none
        let none = build(options)
        #expect(none.sections.map(\.title) == [""])
        #expect(none.sections[0].cards.map(\.target.display) == ["Team Fortress 2", "python/cpython", "react", "Bar"])
    }

    @Test func readUpdatesCanStayUnderEarlier() {
        var options = ViewOptions()
        options.groupsReadUpdates = false
        options.grouping = .priority
        #expect(build(options).sections.map(\.title) == ["High", "Normal", "Low", "Earlier"])
        options.grouping = .none
        let none = build(options)
        #expect(none.sections.map(\.title) == ["", "Earlier"])
        #expect(none.sections[0].cards.map(\.target.display) == ["Team Fortress 2", "python/cpython", "react"])
    }

    @Test func filterMatchesNamesKeysAndLabels() {
        let byName = Feed.build(items: items, text: "fortress", now: now, calendar: utc)
        #expect(byName.cards.allSatisfy { $0.target.source == "steam" } && !byName.cards.isEmpty)
        #expect(Feed.build(items: items, text: " NPM ", now: now, calendar: utc).cards.map(\.target.display) == ["react"])
        let none = Feed.build(items: items, text: "nothing like this", now: now, calendar: utc)
        #expect(none.isEmpty)
        // the heading still counts every unread update
        #expect(none.heading.title == Feed.build(items: items, now: now, calendar: utc).heading.title)
    }

    @Test func viewOptions() {
        var options = ViewOptions()
        #expect(build(options).cards.contains { $0.isHistory && !$0.isCompact })
        options.density = .compact
        #expect(build(options).cards.allSatisfy { $0.isCompact })

        options = ViewOptions()
        options.showsLowPriority = false
        let hidden = build(options)
        #expect(!hidden.cards.contains { $0.priority == .low })
        #expect(hidden.heading.title == "3 new updates")
        // still there when filtering to that target
        #expect(build(options, target: TargetKey(source: "npm", key: "react")).cards.count == 1)

        options = ViewOptions()
        options.readUpdates = .none
        #expect(build(options).sections.map(\.title) == ["Today"])
        options.readUpdates = .week
        // Bar was found today; TF2's older post eight days ago
        #expect(build(options).sections.last?.cards.map(\.target.display) == ["Bar"])

        options = ViewOptions()
        options.previews = false
        let tf2 = build(options).cards.first { $0.target.source == "steam" }
        #expect(tf2?.line == nil)
    }

    @Test func dayBuckets() {
        let base = items[1]
        func found(_ timestamp: String, id: String) -> InboxItem {
            var item = base
            item.id = id
            item.target = id
            item.foundAt = date(timestamp)
            item.published = nil
            return item
        }
        let layout = Feed.build(items: [
            found("2026-10-29T01:00:00Z", id: "a"), found("2026-10-28T12:00:00Z", id: "b"),
            found("2026-10-24T12:00:00Z", id: "c"), found("2026-10-01T12:00:00Z", id: "d"),
        ], now: now, calendar: utc)
        #expect(layout.sections.map(\.title) == ["Today", "Yesterday", "This week", "Earlier"])
        #expect(layout.heading.line == "Since Oct 1")
    }

    @Test func times() {
        #expect(RelativeTime.short(now.addingTimeInterval(-30), now: now, calendar: utc) == "just now")
        #expect(RelativeTime.short(now.addingTimeInterval(-7200), now: now, calendar: utc) == "2h ago")
        #expect(RelativeTime.short(date("2026-10-28T23:00:00Z"), now: now, calendar: utc) == "Yesterday")
        #expect(RelativeTime.short(now.addingTimeInterval(-86400 * 3), now: now, calendar: utc) == "3d ago")
        // 40 hours, but two calendar days
        #expect(RelativeTime.short(date("2026-10-27T23:00:00Z"), now: now, calendar: utc) == "2d ago")
        #expect(RelativeTime.short(date("2026-10-12T09:00:00Z"), now: now, calendar: utc) == "Oct 12")
        #expect(plain(Feed.time(date("2026-10-29T14:02:00Z"), times: .absolute, now: now, calendar: utc)) == "2:02 PM")
        #expect(plain(Feed.time(date("2026-10-28T14:02:00Z"), times: .absolute, now: now, calendar: utc)) == "Oct 28 at 2:02 PM")
    }
}

@Suite struct WindowTextTests {
    @Test func notesBecomeBlocks() {
        let blocks = Notes.blocks("""
        ## What's new
        Security fixes for **ssl**
        and email.

        - gh-12345: fix [a crash](https://example.com)
        * gh-12346: faster `re`
        """)
        #expect(blocks.map(\.kind) == [.heading, .paragraph, .bullet, .bullet])
        #expect(blocks[1].text == "Security fixes for **ssl** and email.")
        #expect(Notes.firstLine("- gh-12345: fix [a crash](https://example.com)") == "gh-12345: fix a crash")
        #expect(Notes.firstLine(nil) == nil)
        let long = Notes.blocks((1...9).map { "- item \($0)" }.joined(separator: "\n"))
        let (shown, clipped) = Notes.clipped(long)
        #expect(shown.count == Notes.clipLimit)
        #expect(clipped)
    }

    @Test func prioritiesAreBells() {
        #expect(PriorityStyle.symbolName(.high) == "bell.and.waves.left.and.right")
        #expect(PriorityStyle.symbolName(.normal) == "bell")
        #expect(PriorityStyle.symbolName(.low) == "bell.slash")
        #expect(PriorityStyle.menuTitle(.high) == "High: Notify immediately")
        #expect(PriorityStyle.label(.low) == "Inbox only")
    }

    @Test func clipboardHoldsALink() {
        #expect(ClipboardCheck.link(from: " https://github.com/python/cpython\n") == "https://github.com/python/cpython")
        #expect(ClipboardCheck.link(from: "express") == nil)
        #expect(ClipboardCheck.link(from: "see https://example.com") == nil)
        #expect(ClipboardCheck.link(from: "file:///etc/passwd") == nil)
        #expect(ClipboardCheck.link(from: nil) == nil)
    }

    @Test func followingGroupsByLabel() throws {
        let targets = (try Fixtures.reply("targets.list/ok", as: TargetsReply.self)).targets
        let items = (try Fixtures.reply("inbox.list/ok", as: InboxPage.self)).items
        let layout = Following.build(targets: targets, items: items, now: date("2026-10-29T15:00:00Z"), calendar: utc)
        #expect(layout.own?.target.starred == true)
        #expect(layout.groups.map(\.title) == ["GitHub", "itch.io", "npm", "Steam", "YouTube"])
        let rows = Dictionary(uniqueKeysWithValues: layout.groups.flatMap(\.rows).map { ($0.target.display, $0) })
        #expect(rows["python/cpython"]?.latest == "3.14.7")
        // no version: the date of the latest post
        #expect(rows["Team Fortress 2"]?.latest == "Oct 28")
        #expect(rows["@SomeChannel"]?.latest == "")
        #expect(rows["@SomeChannel"]?.status == .failed(targets.last!.lastError!))
        #expect(rows["react"]?.status == .ok)
        let filtered = Following.build(targets: targets, items: items, filter: "steam")
        #expect(filtered.groups.map(\.title) == ["Steam"])
        #expect(filtered.own == nil)
    }

    @Test func followingPrefersTheEnginesLatest() throws {
        var targets = (try Fixtures.reply("targets.list/ok", as: TargetsReply.self)).targets
        let index = try #require(targets.firstIndex { $0.source == "npm" })
        targets[index].latest = Target.Latest(version: "v19.4.0", title: "19.4.0", published: nil)
        let rows = Following.build(targets: targets, items: []).groups.flatMap(\.rows)
        #expect(rows.first { $0.target.source == "npm" }?.latest == "19.4.0")
        // no version: the date of the latest post
        #expect(rows.first { $0.target.source == "steam" }?.latest == "Oct 28")
        #expect(rows.first { $0.target.source == "youtube" }?.latest == "")
    }

    @Test func targetLatestAndProblemKeyDecode() throws {
        let target = try ContractCoding.decoder().decode(Target.self, from: Data(#"""
        {"source":"npm","key":"react","display":"react","priority":"low","starred":false,"last_checked":null,
         "last_error":null,"unread":0,"latest":{"version":"19.3.0","title":"19.3.0","published":"2026-10-27T21:15:40Z"}}
        """#.utf8))
        #expect(target.latest?.version == "19.3.0")
        let problem = try ContractCoding.decoder().decode(Problem.self, from: Data(
            #"{"kind":"source","source":"youtube","target":"@SomeChannel","message":"HTTP 404","key":"UC123"}"#.utf8))
        #expect(problem.key == "UC123")
    }

    @Test func problemReport() throws {
        let problems = (try Fixtures.reply("problems.list/ok", as: ProblemsReply.self)).problems
        let text = ProblemReport.text(problems: problems, appProblems: [], configError: nil, engineFailure: nil)
        #expect(text.split(separator: "\n").count == 2)
        #expect(text.hasPrefix("YouTube · @SomeChannel: FetchError"))
    }

    @Test func settingsWithoutIconsDecode() throws {
        let settings: SettingsReply = try Fixtures.reply("settings.get/ok")
        #expect(settings.settings.showIcons == false)
        // an engine from before `show_icons`
        let older = try ContractCoding.decoder().decode(Settings.self, from: Data(
            #"{"check_interval_hours":6,"digest_time":null,"os_notifications":true,"self_watch_declined":false,"cli_notice_shown":false}"#.utf8))
        #expect(older.showIcons == false)
        let patch = try ContractCoding.encoder().encode(SettingsPatch(showIcons: true))
        #expect(String(decoding: patch, as: UTF8.self) == #"{"show_icons":true}"#)
    }

    @Test func viewOptionsAddedLaterDecodeAsDefaults() throws {
        let options = try JSONDecoder().decode(ViewOptions.self, from: Data(#"{"density":"compact"}"#.utf8))
        #expect(options.density == .compact)
        #expect(options.grouping == .day)
        #expect(options.readUpdates == .month)
    }
}

@MainActor
@Suite struct WindowModelTests {
    func launched(_ configure: (FakeEngine) -> Void = { _ in }) async -> Harness {
        let harness = Harness(configure: configure)
        harness.model.feed.readDelay = .zero
        harness.model.feed.settleDelay = .zero
        harness.model.add.debounce = .milliseconds(40)
        await harness.model.launch()
        return harness
    }

    @Test func expandingMarksTheCardRead() async throws {
        let harness = await launched()
        let feed = harness.model.feed
        let card = try #require(feed.layout.sections.first?.cards.first { $0.target.source == "steam" })
        #expect(card.isUnread)
        feed.expand(card)
        #expect(feed.expandedID == card.id)
        for _ in 0..<100 where harness.engine.requests("inbox.mark").isEmpty {
            try await Task.sleep(for: .milliseconds(5))
        }
        #expect(harness.engine.requests("inbox.mark").first?["ids"] as? [String] == ["i_2b9e5d7f0c1a3e44"])
        let after = try #require(feed.layout.sections.first?.cards.first { $0.target.source == "steam" })
        #expect(!after.isUnread)
        feed.toggle(after)
        #expect(feed.expandedID == nil)
        feed.settle()
        #expect(feed.layout.sections.first?.cards.contains { $0.target.source == "steam" } == false)
    }

    @Test func openingMarksReadAndOpens() async throws {
        let harness = await launched()
        let card = try #require(harness.model.feed.layout.sections.first?.cards.first { $0.target.source == "git" })
        let url = await harness.model.feed.open(card)
        #expect(url?.absoluteString == "https://github.com/python/cpython/releases/tag/v3.14.7")
        #expect(harness.platform.opened == [url!])
        #expect(harness.engine.requests("inbox.mark").last?["ids"] as? [String] == ["i_8f3a1c2e9b7d4a10"])
    }

    @Test func markAllReadSettles() async {
        let harness = await launched()
        await harness.model.feed.markAllRead()
        #expect(harness.model.feed.pinned.isEmpty)
        #expect(harness.model.feed.layout.heading.upToDate)
        #expect(harness.model.feed.layout.sections.map(\.title) == ["Earlier"])
    }

    @Test func arrowKeysMoveBetweenCards() async {
        let harness = await launched()
        let feed = harness.model.feed
        let cards = feed.layout.cards
        #expect(!feed.showsSelection)
        feed.move(by: 1)
        #expect(feed.selectedID == cards[0].id)
        #expect(feed.showsSelection)
        feed.move(by: 1)
        feed.move(by: 1)
        #expect(feed.selectedID == cards[2].id)
        feed.move(by: -5)
        #expect(feed.selectedID == cards[0].id)
        feed.move(by: 99)
        #expect(feed.selectedCard?.id == cards.last?.id)
    }

    @Test func followingFiltersUpdates() async {
        let harness = await launched()
        let feed = harness.model.feed
        feed.showOnly(TargetRef(source: "steam", key: "440", display: "Team Fortress 2"))
        #expect(feed.layout.cards.allSatisfy { $0.target.source == "steam" })
        #expect(feed.layout.update == nil)
        feed.showOnly(nil)
        #expect(feed.layout.update != nil)
    }

    @Test func viewOptionsPersist() async {
        let harness = await launched()
        harness.model.viewOptions.density = .compact
        #expect(harness.defaults.viewOptions.density == .compact)
        #expect(harness.model.feed.layout.cards.allSatisfy { $0.isCompact })
    }

    @Test func typingPreviewsOnceItSettles() async throws {
        let harness = await launched()
        let add = harness.model.add
        add.text = "h"
        add.text = "ht"
        add.text = "https://github.com/python/cpython"
        #expect(add.isLooking)
        for _ in 0..<100 where add.preview == nil {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(harness.engine.requests("targets.preview").count == 1)
        #expect(harness.engine.requests("targets.preview").first?["value"] as? String == "https://github.com/python/cpython")
        #expect(add.canFollow)
        add.text = ""
        #expect(add.flow == .idle)
    }

    @Test func ambiguousThenPickThenFollowAtAPriority() async throws {
        let harness = await launched {
            $0.script("targets.preview", "targets.preview/ambiguous", "targets.preview/ok")
        }
        let add = harness.model.add
        add.fill("express")
        for _ in 0..<100 where add.choices.isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(add.choices == ["homebrew", "npm", "pypi"])
        await add.pick("npm")
        #expect(add.chosen == "npm")
        #expect(harness.engine.requests("targets.preview").last?["source"] as? String == "npm")
        add.priority = .high
        await add.follow()
        #expect(harness.engine.ops.contains("targets.add"))
        let priority = try #require(harness.engine.requests("priority.set").last)
        #expect(priority["priority"] as? String == "high")
        #expect(harness.engine.ops.contains("check"))
        guard case .added = add.flow else {
            Issue.record("expected added, got \(add.flow)")
            return
        }
        add.reset()
        #expect(add.flow == .idle)
        #expect(add.text.isEmpty)
    }

    @Test func snoozeSchedulesTheNotification() async throws {
        let harness = await launched()
        await harness.model.snooze(["i_2b9e5d7f0c1a3e44"])
        let (content, at) = try #require(harness.platform.scheduled.first)
        #expect(content.title == "Team Fortress 2")
        #expect(at == ContractCoding.parseTimestamp("2026-10-29T09:05:00Z"))
    }

    @Test func retryChecksJustThatTarget() async throws {
        let harness = await launched()
        let problem = try #require(harness.model.problems.first { $0.kind == .source })
        #expect(harness.model.target(for: problem) == TargetKey(source: "youtube", key: "UC123"))
        await harness.model.retry(problem)
        let check = try #require(harness.engine.requests("check").last)
        #expect(check["targets"] as? [[String: String]] == [["source": "youtube", "key": "UC123"]])
        #expect(harness.model.problemReport.contains("@SomeChannel"))
        let keyed = Problem(kind: .source, source: "steam", target: "Renamed Game", message: "x", key: "440")
        #expect(harness.model.target(for: keyed) == TargetKey(source: "steam", key: "440"))
    }

    func iconHarness(png: Data) async -> Harness {
        await launched { engine in
            var settings = Fixtures.named("settings.get/ok").reply
            var values = settings["settings"] as! [String: Any]
            values["show_icons"] = true
            settings["settings"] = values
            engine.answer("settings.get", reply: settings)
            engine.answer("icon.get", reply: ["ok": true, "icon": ["mime": "image/png", "data": png.base64EncodedString()]])
        }
    }

    @Test func iconsOnlyWhenTheEngineHasThemAndTheyreOn() async throws {
        // an engine from before `icon.get`
        let without = await launched { engine in
            var hello = Fixtures.named("hello/ok").reply
            hello["ops"] = (hello["ops"] as! [String]).filter { $0 != "icon.get" }
            engine.answer("hello", reply: hello)
        }
        await without.model.icons.load(TargetKey(source: "steam", key: "440"))
        #expect(without.engine.requests("icon.get").isEmpty)
        #expect(!without.model.icons.isAvailable)

        let png = Data([0x89, 0x50, 0x4E, 0x47])
        let with = await iconHarness(png: png)
        #expect(with.model.icons.isEnabled)
        let key = TargetKey(source: "steam", key: "440")
        await with.model.icons.load(key)
        await with.model.icons.load(key)
        #expect(with.model.icons.images[key] == png)
        #expect(with.engine.requests("icon.get").count == 1)
        #expect(with.engine.requests("icon.get").first?["key"] as? String == "440")
    }

    @Test func theIconEventFetchesAgain() async throws {
        let harness = await iconHarness(png: Data([1, 2, 3]))
        let key = TargetKey(source: "npm", key: "react")
        await harness.model.icons.load(key)
        #expect(harness.engine.requests("icon.get").count == 1)
        harness.engine.emit(#"{"event":"icon","target":{"source":"npm","key":"react"}}"#)
        for _ in 0..<100 where harness.engine.requests("icon.get").count < 2 {
            try await Task.sleep(for: .milliseconds(5))
        }
        #expect(harness.engine.requests("icon.get").count == 2)
    }

    @Test func emptySetupShowsTheBigAddField() async {
        let harness = await launched { engine in
            var targets = Fixtures.named("targets.list/ok").reply
            targets["targets"] = Array((targets["targets"] as! [[String: Any]]).prefix(1))
            engine.answer("targets.list", reply: targets)
        }
        #expect(harness.model.isEmptySetup)
    }
}
