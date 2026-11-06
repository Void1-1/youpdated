import Foundation
@testable import YoupdatedKit

/// An engine that answers each request with a contract fixture: `<op>/ok` unless a test scripts another case
final class FakeEngine: EngineTransport, @unchecked Sendable {
    let messages: AsyncStream<TransportMessage>
    private let continuation: AsyncStream<TransportMessage>.Continuation
    private let lock = NSLock()
    private var scripted: [String: [String]] = [:]
    private var defaults: [String: String] = [:]
    private var custom: [String: [String: Any]] = [:]
    private var held: Set<String> = []
    private var waiting: [(op: String, lines: [Data])] = []
    private var finished = false
    private var _requests: [[String: Any]] = []

    init() {
        (messages, continuation) = AsyncStream.makeStream(of: TransportMessage.self)
    }

    /// The next replies to `op`, in order, before falling back to the default
    func script(_ op: String, _ fixtures: String...) {
        lock.withLock { scripted[op, default: []] += fixtures }
    }

    /// The fixture `op` answers with once nothing is scripted
    func answer(_ op: String, with fixture: String) {
        lock.withLock { defaults[op] = fixture }
    }

    /// A reply no fixture has
    func answer(_ op: String, reply: [String: Any]) {
        lock.withLock { custom[op] = reply }
    }

    /// Replies to `op` wait for `release`
    func hold(_ op: String) {
        lock.withLock { _ = held.insert(op) }
    }

    func release(_ op: String) {
        let lines: [Data] = lock.withLock {
            held.remove(op)
            let mine = waiting.filter { $0.op == op }.flatMap(\.lines)
            waiting.removeAll { $0.op == op }
            return mine
        }
        for line in lines { continuation.yield(.line(line)) }
    }

    /// Writes a raw line
    func emit(_ line: String) {
        continuation.yield(.line(Data(line.utf8)))
    }

    func crash(status: Int32 = 1, stderr: String = "Traceback (most recent call last):\nRuntimeError: explosion") {
        finish(EngineExit(status: status, stderr: stderr))
    }

    var requests: [[String: Any]] { lock.withLock { _requests } }
    var ops: [String] { requests.compactMap { $0["op"] as? String } }

    func requests(_ op: String) -> [[String: Any]] {
        requests.filter { $0["op"] as? String == op }
    }

    func send(_ line: Data) throws {
        guard let request = try JSONSerialization.jsonObject(with: line) as? [String: Any],
              let id = request["id"] as? Int, let op = request["op"] as? String else {
            throw CocoaError(.coderReadCorrupt)
        }
        let (lines, hold): ([Data], Bool) = lock.withLock {
            _requests.append(request)
            if finished { return ([], false) }
            var name = defaults[op] ?? Fixtures.standard(for: op)
            var reply = custom[op]
            if let queue = scripted[op], !queue.isEmpty {
                name = queue[0]
                reply = nil
                scripted[op] = Array(queue.dropFirst())
            }
            let lines: [Data]
            if var reply {
                reply["id"] = id
                lines = [try! JSONSerialization.data(withJSONObject: reply)]
            } else if let name, let fixture = Fixtures.all[name] {
                lines = fixture.eventLines + [fixture.replyLine(id: id)] + fixture.afterLines
            } else {
                let error = #"{"id":\#(id),"ok":false,"error":{"code":"unknown_op","message":"unknown op `\#(op)`"}}"#
                lines = [Data(error.utf8)]
            }
            if held.contains(op) {
                waiting.append((op, lines))
                return ([], true)
            }
            return (lines, false)
        }
        if !hold {
            for line in lines { continuation.yield(.line(line)) }
        }
    }

    func terminate() {
        finish(EngineExit(status: 0))
    }

    private func finish(_ exit: EngineExit) {
        let first: Bool = lock.withLock {
            defer { finished = true }
            return !finished
        }
        guard first else { return }
        continuation.yield(.exited(exit))
        continuation.finish()
    }
}

/// Collects what the model would ask from macos
@MainActor
final class FakePlatform: AppPlatform {
    var posted: [NotificationContent] = []
    var withdrawn: [String] = []
    var opened: [URL] = []
    var passphrase: String?
    var snapshotURL: URL?

    func post(_ notifications: [NotificationContent]) { posted += notifications }
    var scheduled: [(NotificationContent, Date)] = []
    func schedule(_ notifications: [NotificationContent], at date: Date) {
        scheduled += notifications.map { ($0, date) }
    }
    func withdraw(itemIds: [String]) { withdrawn += itemIds }
    func storedPassphrase() -> String? { passphrase }
    func storePassphrase(_ passphrase: String?) { self.passphrase = passphrase }
    func open(_ url: URL) { opened.append(url) }
    var snapshots = 0
    func snapshotWritten() { snapshots += 1 }
}

/// Manually editable clock for testing
final class TestClock: @unchecked Sendable {
    private let lock = NSLock()
    private var current: Date

    init(_ start: String = "2026-10-29T08:00:06Z") {
        current = ContractCoding.parseTimestamp(start)!
    }

    var now: Date { lock.withLock { current } }

    func advance(_ seconds: TimeInterval) {
        lock.withLock { current = current.addingTimeInterval(seconds) }
    }

    func set(_ timestamp: String) {
        lock.withLock { current = ContractCoding.parseTimestamp(timestamp)! }
    }
}

@MainActor
struct Harness {
    let engine = FakeEngine()
    let platform = FakePlatform()
    let clock = TestClock()
    let defaults: AppDefaults
    let model: AppModel

    init(version: String = "1.0.0", configure: (FakeEngine) -> Void = { _ in }) {
        let suite = UserDefaults(suiteName: "YoupdatedTests-\(UUID().uuidString)")!
        defaults = AppDefaults(suite)
        configure(engine)
        let engine = engine
        let clock = clock
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        model = AppModel(
            launch: { engine }, platform: platform, defaults: defaults, appVersion: version,
            now: { clock.now }, calendar: calendar, sleep: { _ in await Task.yield() })
    }
}
