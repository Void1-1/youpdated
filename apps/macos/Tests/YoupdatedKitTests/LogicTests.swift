import Foundation
import Testing
@testable import YoupdatedKit

private func date(_ timestamp: String) -> Date { ContractCoding.parseTimestamp(timestamp)! }

private var utc: Calendar {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(identifier: "UTC")!
    return calendar
}

@Suite struct ScheduleTests {
    let now = date("2026-10-29T12:00:00Z")
    let sixHours: TimeInterval = 6 * 3600

    @Test func dueAtLaunchWithoutAPreviousRun() {
        #expect(Schedule.isDue(lastRun: nil, interval: sixHours, pause: .none, now: now))
    }

    @Test func dueOnceTheIntervalHasPassed() {
        #expect(!Schedule.isDue(lastRun: date("2026-10-29T07:00:01Z"), interval: sixHours, pause: .none, now: now))
        #expect(Schedule.isDue(lastRun: date("2026-10-29T06:00:00Z"), interval: sixHours, pause: .none, now: now))
        #expect(Schedule.nextCheck(lastRun: date("2026-10-29T09:00:00Z"), interval: sixHours, pause: .none, now: now)
            == date("2026-10-29T15:00:00Z"))
    }

    @Test func pausesHoldChecks() {
        let lastRun = date("2026-10-29T00:00:00Z")
        let hour = PauseOption.oneHour.pause(from: now, calendar: utc)
        #expect(hour == .until(date("2026-10-29T13:00:00Z")))
        #expect(!Schedule.isDue(lastRun: lastRun, interval: sixHours, pause: hour, now: now))
        #expect(Schedule.nextCheck(lastRun: lastRun, interval: sixHours, pause: hour, now: now) == date("2026-10-29T13:00:00Z"))
        // an ended pause doesn't hold updates
        #expect(Schedule.isDue(lastRun: lastRun, interval: sixHours, pause: hour, now: date("2026-10-29T13:00:01Z")))

        let tomorrow = PauseOption.untilTomorrow.pause(from: now, calendar: utc)
        #expect(tomorrow == .until(date("2026-10-30T00:00:00Z")))

        let resumed = PauseOption.untilResumed.pause(from: now, calendar: utc)
        #expect(Schedule.nextCheck(lastRun: lastRun, interval: sixHours, pause: resumed, now: now) == nil)
        #expect(resumed.isActive(at: now.addingTimeInterval(86400 * 365)))
    }
}

@Suite struct DigestScheduleTests {
    @Test func parsesTimes() {
        #expect(DigestSchedule.components("18:00") == DateComponents(hour: 18, minute: 0))
        #expect(DigestSchedule.components("24:00") == nil)
        #expect(DigestSchedule.time(hour: 7, minute: 5) == "07:05")
    }

    @Test func nextAndLatest() {
        let now = date("2026-10-29T12:00:00Z")
        #expect(DigestSchedule.next(after: now, time: "18:00", calendar: utc) == date("2026-10-29T18:00:00Z"))
        #expect(DigestSchedule.latest(atOrBefore: now, time: "18:00", calendar: utc) == date("2026-10-28T18:00:00Z"))
        #expect(DigestSchedule.latest(atOrBefore: date("2026-10-29T18:00:00Z"), time: "18:00", calendar: utc)
            == date("2026-10-29T18:00:00Z"))
    }

    @Test func missedWhileAsleep() {
        let sent = date("2026-10-28T18:00:05Z")
        #expect(!DigestSchedule.isMissed(lastSent: sent, now: date("2026-10-29T17:59:00Z"), time: "18:00", calendar: utc))
        #expect(DigestSchedule.isMissed(lastSent: sent, now: date("2026-10-29T18:00:00Z"), time: "18:00", calendar: utc))
        // opened next morning: yesterday's digest still delivers
        #expect(DigestSchedule.isMissed(lastSent: sent, now: date("2026-10-30T08:00:00Z"), time: "18:00", calendar: utc))
    }
}

@Suite struct DeepLinkTests {
    @Test func parsesTheScheme() {
        #expect(DeepLink(url: URL(string: "youpdated://add?url=https%3A%2F%2Fgithub.com%2Fpython%2Fcpython")!)
            == .add("https://github.com/python/cpython"))
        #expect(DeepLink(url: URL(string: "youpdated://open")!) == .open)
        #expect(DeepLink(url: URL(string: "YOUPDATED://check")!) == .check)
        #expect(DeepLink(url: URL(string: "youpdated://add")!) == nil)
        #expect(DeepLink(url: URL(string: "youpdated://delete-everything")!) == nil)
        #expect(DeepLink(url: URL(string: "https://example.com")!) == nil)
    }

    @Test func findsWhatToWatchInText() {
        #expect(DeepLink.watchValue(from: "look at https://github.com/python/cpython please") == "https://github.com/python/cpython")
        #expect(DeepLink.watchValue(from: "  express \n") == "express")
        #expect(DeepLink.watchValue(from: "two\nlines") == nil)
        #expect(DeepLink.watchValue(from: "   ") == nil)
    }
}

@Suite struct AppUpdateTests {
    let page: InboxPage = try! Fixtures.reply("inbox.list/ok")

    @Test func newerStarredReleaseIsAnUpdate() throws {
        let update = try #require(AppUpdate.available(in: page.items, current: "1.0.0"))
        #expect(update.version == "1.1.0")
        #expect(update.downloadURL.absoluteString
            == "https://github.com/Void1-1/youpdated/releases/download/app-v1.1.0/Youpdated-1.1.0-macOS.dmg")
        #expect(update.releaseURL?.absoluteString == "https://github.com/Void1-1/youpdated/releases/tag/app-v1.1.0")
    }

    @Test func runningThatVersionOrNewerIsNot() {
        #expect(AppUpdate.available(in: page.items, current: "1.1.0") == nil)
        #expect(AppUpdate.available(in: page.items, current: "1.2") == nil)
    }

    @Test func comparesVersions() {
        #expect(AppUpdate.compare("1.10.0", "1.9.9") == .orderedDescending)
        #expect(AppUpdate.compare("1.1", "1.1.0") == .orderedSame)
        #expect(AppUpdate.compare("1.1.0rc1", "1.1.0") == .orderedAscending)
    }
}

@Suite struct AlertComposerTests {
    let check: CheckReply = try! Fixtures.reply("check/ok")

    @Test func composesTheCheckAlerts() {
        let contents = AlertComposer.compose(check.alerts, items: check.items, currentVersion: "1.0.0")
        #expect(contents.map(\.title) == ["python/cpython", "Youpdated 1.1.0 is available", "Team Fortress 2"])
        #expect(contents[0].body == "v3.14.7")
        #expect(contents[0].threadIdentifier == "git")
        #expect(contents[0].url?.absoluteString == "https://github.com/python/cpython/releases/tag/v3.14.7")
        #expect(contents[1].isAppUpdate)
        #expect(contents[2].threadIdentifier == "steam")
        #expect(Set(contents.flatMap(\.itemIds)) == Set(check.alerts.flatMap(\.itemIds)))
    }

    @Test func combinesAcrossTargets() {
        let items = check.items.filter { !$0.starred }
        let alert = Alert(priority: .normal, target: nil, itemIds: items.map(\.id))
        let contents = AlertComposer.compose([alert], items: items, currentVersion: "1.0.0")
        #expect(contents.count == 1)
        #expect(contents[0].title == "4 new updates")
        #expect(contents[0].threadIdentifier == "updates")
        #expect(contents[0].body.split(separator: "\n").count == AlertComposer.shownLines + 1)
    }

    @Test func pluralizes() {
        #expect(Wording.newUpdates(1) == "1 new update")
        #expect(Wording.newUpdates(3) == "3 new updates")
    }
}

@Suite struct InboxSectionTests {
    let items: [InboxItem] = (try! Fixtures.reply("inbox.list/ok", as: InboxPage.self)).items

    @Test func newThenEarlierGroupedByTarget() {
        let sections = InboxModel.sections(items, sort: .newest, source: nil)
        #expect(sections.map(\.kind) == [.new, .earlier])
        #expect(sections[0].rows.map(\.target.display) == ["Void1-1/youpdated", "Team Fortress 2", "python/cpython", "react"])
        // read items from the same target share a row in Earlier
        #expect(sections[1].rows.map(\.target.display) == ["Bar", "Team Fortress 2"])
        #expect(sections[1].rows.allSatisfy { !$0.unread })
    }

    @Test func sortsWithNewestBreakingTies() {
        let new = { (sort: InboxSort) in InboxModel.sections(items, sort: sort, source: nil)[0].rows.map(\.target.display) }
        #expect(new(.priority) == ["python/cpython", "Void1-1/youpdated", "Team Fortress 2", "react"])
        #expect(new(.source) == ["Void1-1/youpdated", "python/cpython", "react", "Team Fortress 2"])
        #expect(new(.name) == ["python/cpython", "react", "Team Fortress 2", "Void1-1/youpdated"])
    }

    @Test func filtersBySource() {
        let sections = InboxModel.sections(items, sort: .newest, source: "steam")
        #expect(sections.flatMap(\.rows).allSatisfy { $0.target.source == "steam" })
        #expect(sections.map(\.kind) == [.new, .earlier])
    }

    @Test func iconsFollowState() {
        #expect(MenuBarState.idle.symbolName(unread: 0) == "bell")
        #expect(MenuBarState.idle.symbolName(unread: 2) == "bell.badge")
        #expect(MenuBarState.locked.symbolName(unread: 2) == "lock")
    }
}
