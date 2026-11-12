import Foundation
import Observation
import os

public enum EngineState: Hashable, Sendable {
    case starting
    case ready
    /// Can't run: missing, incompatible, or won't start
    case failed(String)
}

/// app's state, shared by menu bar item, window, Settings
@MainActor
@Observable
public final class AppModel {
    public let inbox: InboxModel
    public let sources: SourcesModel
    public let feed: FeedModel
    public let add: AddModel
    public let icons: IconStore
    public let defaults: AppDefaults
    public let platform: AppPlatform
    public let appVersion: String

    public private(set) var engineState: EngineState = .starting
    public private(set) var hello: HelloReply?
    public private(set) var status: StatusReply?
    public private(set) var settings: Settings = .defaults
    public private(set) var privacy: Privacy?
    public internal(set) var unread = 0
    public private(set) var isChecking = false
    public private(set) var progress: ProgressEvent?
    public private(set) var lastCheck: Date?
    /// Source errors and failed webhooks or commands from the last check
    public private(set) var problems: [Problem] = []
    public private(set) var appProblems: [AppProblem] = []
    public private(set) var pause: Pause
    /// Settings → View, in UserDefaults
    public var viewOptions: ViewOptions {
        didSet { defaults.viewOptions = viewOptions }
    }
    public var hidesSelfWatchSuggestions: Bool {
        didSet { defaults.hidesSelfWatchSuggestions = hidesSelfWatchSuggestions }
    }

    /// The passphrase prompt is up
    public private(set) var needsPassphrase = false
    public private(set) var passphraseError: String?
    public var offersSelfWatch: Bool { offersSelfWatchNow && !hidesSelfWatchSuggestions }
    private var offersSelfWatchNow = false
    /// "Checks are also running from the command line..."
    public private(set) var showsCLINotice = false
    /// The config is empty -> show the 'add' box in inbox
    public private(set) var isFirstRun = false

    @ObservationIgnored let supervisor: EngineSupervisor
    @ObservationIgnored private let now: @Sendable () -> Date
    @ObservationIgnored public let calendar: Calendar
    @ObservationIgnored private var passphrase: String?
    @ObservationIgnored private var scheduler: Task<Void, Never>?
    @ObservationIgnored private var retryAt: Date?
    private static let log = Logger(subsystem: "Youpdated", category: "app")

    public init(
        launch: @escaping EngineSupervisor.Launch,
        platform: AppPlatform,
        defaults: AppDefaults = AppDefaults(),
        appVersion: String,
        now: @escaping @Sendable () -> Date = { Date() },
        calendar: Calendar = .current,
        sleep: @escaping EngineSupervisor.Sleep = { try await Task.sleep(for: $0) }
    ) {
        self.platform = platform
        self.defaults = defaults
        self.appVersion = appVersion
        self.now = now
        self.calendar = calendar
        pause = defaults.pause.settled(at: now())
        viewOptions = defaults.viewOptions
        hidesSelfWatchSuggestions = defaults.hidesSelfWatchSuggestions
        inbox = InboxModel()
        sources = SourcesModel()
        feed = FeedModel()
        add = AddModel()
        icons = IconStore()
        supervisor = EngineSupervisor(
            launch: launch, client: ClientInfo(version: appVersion), sleep: sleep, now: now)
        inbox.app = self
        sources.app = self
        feed.app = self
        add.app = self
        icons.app = self
        supervisor.onEvent = { [weak self] event in self?.received(event) }
        supervisor.onProblem = { [weak self] problem in
            AppLog.shared.write("\(problem.title) \(problem.detail)")
            self?.appProblems.append(problem)
        }
        supervisor.onReady = { [weak self] _, hello in
            self?.hello = hello
            await self?.engineReady()
        }
    }

    public var engine: Engine? { supervisor.engine }

    /// model's clock, so relative times agree with the schedule
    public var currentDate: Date { now() }

    /// What this session unlocked with, for "Remember passphrase"
    public var currentPassphrase: String? { passphrase }

    public var menuBarState: MenuBarState {
        if needsPassphrase || status?.locked == true { return .locked }
        if isChecking { return .checking }
        if pause.isActive(at: now()) { return .paused }
        if hasProblems { return .problems }
        return .idle
    }

    public var hasProblems: Bool {
        !problems.isEmpty || !appProblems.isEmpty || status?.configError != nil || engineState.failed
    }

    public var problemCount: Int {
        problems.count + appProblems.count + (status?.configError == nil ? 0 : 1) + (engineState.failed ? 1 : 0)
    }

    /// Newest unread rows
    public func recentUnread(limit: Int = 5) -> [InboxRow] {
        let rows = InboxModel.sections(inbox.items, sort: .newest, source: nil).first { $0.kind == .new }?.rows ?? []
        return Array(rows.prefix(limit))
    }

    public var appUpdate: AppUpdate? {
        AppUpdate.available(in: inbox.items, current: appVersion)
    }

    public var engineVersion: String? {
        guard let hello else { return nil }
        return hello.commit.map { "\(hello.engine) (\($0))" } ?? hello.engine
    }

    public var nextCheck: Date? {
        Schedule.nextCheck(lastRun: lastCheck, interval: settings.checkInterval, pause: pause, now: now())
    }

    /// Starts the engine and all setup; call once at launch
    public func launch() async {
        engineState = .starting
        do {
            let (_, hello) = try await supervisor.start()
            self.hello = hello
        } catch {
            engineState = .failed(SourcesModel.message(for: error))
            return
        }
        await engineReady()
    }

    /// Sends `shutdown` before the app quits
    public func quit() async {
        scheduler?.cancel()
        await supervisor.stop()
    }

    /// After (re)start: unlock if needed, then load and schedule
    func engineReady() async {
        engineState = .ready
        guard await refreshStatus() else { return }
        if status?.locked == true {
            if let known = passphrase ?? platform.storedPassphrase() {
                await unlock(known, remember: nil)
            } else {
                needsPassphrase = true
            }
            return
        }
        await unlocked()
    }

    /// Unlocks with a passphrase from the prompt (`remember` set) or a known one (nil)
    @discardableResult
    public func unlock(_ passphrase: String, remember: Bool?) async -> Bool {
        guard let engine else { return false }
        passphraseError = nil
        do {
            _ = try await engine.unlock(passphrase: passphrase)
        } catch let error as EngineError where error.code == .wrongPassphrase {
            if remember == nil {
                // a stored passphrase that stopped working is forgotten, and the prompt asks
                if platform.storedPassphrase() == passphrase { platform.storePassphrase(nil) }
                self.passphrase = nil
            } else {
                passphraseError = String(localized: "That passphrase didn't unlock Youpdated.")
            }
            needsPassphrase = true
            return false
        } catch {
            passphraseError = SourcesModel.message(for: error)
            needsPassphrase = true
            return false
        }
        self.passphrase = passphrase
        if let remember {
            platform.storePassphrase(remember ? passphrase : nil)
        }
        needsPassphrase = false
        await refreshStatus()
        await unlocked()
        return true
    }

    private func unlocked() async {
        guard let engine, let status else { return }
        if !status.configExists {
            do {
                _ = try await engine.initConfig(selfWatch: true)
                isFirstRun = true
                await refreshStatus()
            } catch {
                report(error, doing: String(localized: "Couldn't create a config."))
                return
            }
        }
        do {
            settings = try await engine.settings()
        } catch {
            report(error, doing: String(localized: "Couldn't read Settings."))
        }
        offersSelfWatchNow = self.status?.selfWatch == .absent && !settings.selfWatchDeclined
        await loadPrivacy()
        noteCLIRuns()
        if let reply = try? await engine.problems() {
            problems = reply.problems
        }
        await inbox.reload()
        await sources.reload()
        startScheduler()
        await tick()
    }

    @discardableResult
    func refreshStatus() async -> Bool {
        guard let engine else { return false }
        do {
            let status = try await engine.status()
            self.status = status
            lastCheck = status.lastRun
            if let counts = status.counts { unread = counts.unread }
            return true
        } catch {
            report(error, doing: String(localized: "Couldn't read Youpdated's status."))
            return false
        }
    }

    /// Whichever check runs first marks items seen, so a scheduled CLI check hides updates
    private func noteCLIRuns() {
        if status?.lastRunBy == .cli && !settings.cliNoticeShown {
            showsCLINotice = true
        }
    }

    public func checkNow(targets: [TargetKey]? = nil) async {
        guard let engine, !isChecking, !needsPassphrase else { return }
        isChecking = true
        progress = nil
        defer {
            isChecking = false
            progress = nil
        }
        if targets == nil {
            await refreshStatus()
            noteCLIRuns()
        }
        do {
            let reply = try await engine.check(targets: targets)
            retryAt = nil
            await handle(reply, partial: targets != nil)
        } catch let error as EngineError {
            switch error.code {
            case .busy:
                break
            case .historyBusy:
                // the CLI is checking; try again in a bit
                retryAt = now().addingTimeInterval(300)
                restartScheduler()
            case .locked:
                needsPassphrase = true
            default:
                report(error, doing: String(localized: "The check didn't finish."))
            }
        } catch {
            report(error, doing: String(localized: "The check didn't finish."))
        }
    }

    private func handle(_ reply: CheckReply, partial: Bool) async {
        lastCheck = reply.checkedAt
        unread = reply.unread
        let found = reply.errors + reply.notifyFailures
        if partial {
            problems = (problems + found).uniqued()
        } else {
            problems = found
            appProblems.removeAll { $0.date < reply.checkedAt.addingTimeInterval(-86400) }
        }
        platform.post(AlertComposer.compose(reply.alerts, items: reply.items, currentVersion: appVersion))
        inbox.merge(reply.items)
        feed.settle()
        icons.forgetMisses()
        await inbox.reload()
        await sources.reload()
        await writeSnapshot()
        restartScheduler()
    }

    private func received(_ event: EngineEvent) {
        switch event {
        case .progress(let progress) where isChecking:
            self.progress = progress
        case .icon(let keys):
            Task { await icons.arrived(keys) }
        default:
            break
        }
    }

    public func pause(_ option: PauseOption) {
        pause = option.pause(from: now(), calendar: calendar)
        defaults.pause = pause
        restartScheduler()
    }

    public func resume() async {
        pause = .none
        defaults.pause = .none
        restartScheduler()
        await tick()
    }

    /// After wake: check if one is due, and send any digests missed
    public func didWake() async {
        await tick()
    }

    /// Runs a due check and a due digest
    public func tick() async {
        guard engine != nil, !needsPassphrase, status?.locked != true else { return }
        pause = pause.settled(at: now())
        let due = Schedule.isDue(lastRun: lastCheck, interval: settings.checkInterval, pause: pause, now: now())
        let retry = retryAt.map { $0 <= now() && !pause.isActive(at: now()) } ?? false
        if due || retry {
            await checkNow()
        }
        await sendDigestIfDue()
    }

    private func sendDigestIfDue() async {
        guard let time = settings.digestTime, let engine else { return }
        guard defaults.lastDigest != nil else {
            defaults.lastDigest = now()
            return
        }
        guard DigestSchedule.isMissed(lastSent: defaults.lastDigest, now: now(), time: time, calendar: calendar) else {
            return
        }
        do {
            let reply = try await engine.sendDigest()
            defaults.lastDigest = now()
            problems = (problems + reply.notifyFailures).uniqued()
            platform.post(AlertComposer.compose(reply.alerts, items: reply.items, currentVersion: appVersion))
        } catch {
            report(error, doing: String(localized: "Couldn't send the digest."))
        }
    }

    private func startScheduler() {
        restartScheduler()
    }

    private func restartScheduler() {
        scheduler?.cancel()
        guard engine != nil else { return }
        scheduler = Task { [weak self] in
            while !Task.isCancelled {
                guard let wait = self?.secondsUntilNextTick() else { return }
                try? await Task.sleep(for: .seconds(wait))
                if Task.isCancelled { return }
                await self?.tick()
            }
        }
    }

    /// Seconds until the next check, digest, pause end, retry (between 1 and 3600)
    func secondsUntilNextTick() -> Double {
        let current = now()
        var moments: [Date] = []
        if let next = nextCheck { moments.append(next) }
        if case .until(let end) = pause { moments.append(end) }
        if let retryAt { moments.append(retryAt) }
        if let time = settings.digestTime, let next = DigestSchedule.next(after: current, time: time, calendar: calendar) {
            moments.append(next)
        }
        let soonest = moments.min().map { $0.timeIntervalSince(current) } ?? 3600
        return min(max(soonest, 1), 3600)
    }

    public func update(_ patch: SettingsPatch) async {
        guard let engine else { return }
        if case .some(.some) = patch.digestTime, settings.digestTime == nil {
            // turning the digest on doesn't send one for an earlier time
            defaults.lastDigest = now()
        }
        do {
            settings = try await engine.setSettings(patch)
            if settings.selfWatchDeclined { offersSelfWatchNow = false }
            if !settings.showIcons { icons.clear() }
        } catch {
            report(error, doing: String(localized: "Couldn't save Settings."))
        }
        restartScheduler()
    }

    public func watchSelf() async {
        guard let engine else { return }
        do {
            _ = try await engine.initConfig(selfWatch: true)
            offersSelfWatchNow = false
            await refreshStatus()
            await sources.reload()
            if let target = sources.selfTarget {
                await checkNow(targets: [target.id])
            }
        } catch {
            report(error, doing: String(localized: "Couldn't add Youpdated's entry."))
        }
    }

    public func declineSelfWatch() async {
        offersSelfWatchNow = false
        await update(SettingsPatch(selfWatchDeclined: true))
    }

    public func dismissCLINotice() async {
        showsCLINotice = false
        await update(SettingsPatch(cliNoticeShown: true))
    }

    public func dismissFirstRun() {
        isFirstRun = false
    }

    /// The config changed on disk (`youpdated add` from terminal)
    public func configChanged() async {
        guard engine != nil, status?.locked != true else { return }
        await refreshStatus()
        await sources.reload()
        await loadPrivacy()
    }

    /// The engine reads and writes the config's privacy options
    public var canEditPrivacy: Bool { hello?.ops.contains("privacy.set") == true }

    public func loadPrivacy() async {
        guard let engine, canEditPrivacy, !needsPassphrase, status?.locked != true else { return }
        do {
            privacy = try await engine.privacy()
        } catch {
            privacy = nil
        }
    }

    /// Changes the config
    public func setPrivacy(proxy: String?? = nil, fastMode: Bool? = nil) async -> String? {
        guard let engine else { return String(localized: "Youpdated's engine isn't running.") }
        do {
            privacy = try await engine.setPrivacy(proxy: proxy, fastMode: fastMode)
            return nil
        } catch {
            return SourcesModel.message(for: error)
        }
    }

    public enum NotificationAction: String, Sendable {
        case open, markRead, snooze
    }

    /// A notification's button, or a (`open`) click
    @discardableResult
    public func respond(_ action: NotificationAction, itemIds: [String], url: URL?) async -> Date? {
        switch action {
        case .open:
            if let url { platform.open(url) }
            await inbox.mark(true, ids: itemIds)
        case .markRead:
            await inbox.mark(true, ids: itemIds)
        case .snooze:
            guard let engine else { return nil }
            do {
                return try await engine.snooze(ids: itemIds, minutes: 60).until
            } catch {
                report(error, doing: String(localized: "Couldn't snooze."))
            }
        }
        return nil
    }

    public func snooze(_ ids: [String], minutes: Int = 60) async {
        guard let engine, !ids.isEmpty else { return }
        do {
            let until = try await engine.snooze(ids: ids, minutes: minutes).until
            let items = inbox.items.filter { ids.contains($0.id) }
            guard let first = items.first else { return }
            let alert = Alert(priority: .high, target: first.targetRef, itemIds: items.map(\.id))
            platform.schedule(AlertComposer.compose([alert], items: items, currentVersion: appVersion), at: until)
        } catch {
            report(error, doing: String(localized: "Couldn't snooze."))
        }
    }

    /// The target of a source problem, for Retry
    public func target(for problem: Problem) -> TargetKey? {
        guard problem.kind == .source else { return nil }
        if let key = problem.key { return TargetKey(source: problem.source, key: key) }
        // an engine from before `Problem.key`
        return sources.targets.first { $0.source == problem.source && $0.display == problem.target }?.id
    }

    /// Retry in the Problems popover: checks just that target
    public func retry(_ problem: Problem) async {
        guard let key = target(for: problem) else { return }
        problems.removeAll { $0 == problem }
        await checkNow(targets: [key])
    }

    public var problemReport: String {
        var failure: String?
        if case .failed(let message) = engineState { failure = message }
        return ProblemReport.text(
            problems: problems, appProblems: appProblems, configError: status?.configError, engineFailure: failure)
    }

    /// Nothing followed (not including Youpdated): the window shows the big Add field
    public var isEmptySetup: Bool {
        sources.loaded && sources.targets.allSatisfy(\.starred)
    }

    func report(_ error: Error, doing: String) {
        if let error = error as? EngineError {
            switch error.code {
            case .locked:
                needsPassphrase = true
                return
            case .historyBusy:
                return
            default:
                break
            }
        }
        if error is CancellationError { return }
        Self.log.error("\(doing, privacy: .public) \(String(describing: error), privacy: .public)")
        AppLog.shared.write("\(doing) \(String(describing: error))")
        appProblems.append(AppProblem(title: doing, detail: SourcesModel.message(for: error), date: now()))
    }

    public func clearAppProblems() {
        appProblems.removeAll()
    }

    /// For the widget, after a check and whenever what's unread changes
    func writeSnapshot() async {
        guard let url = platform.snapshotURL, let engine else { return }
        if (try? await engine.writeSnapshot(to: url.path)) != nil {
            platform.snapshotWritten()
        }
    }
}

extension EngineState {
    var failed: Bool {
        if case .failed = self { return true }
        return false
    }
}

extension Array where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
