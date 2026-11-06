import Foundation

public final class ReplayTransport: EngineTransport, @unchecked Sendable {
    public let messages: AsyncStream<TransportMessage>
    private let continuation: AsyncStream<TransportMessage>.Continuation
    private let fixtures: [String: [String: Any]]
    private let lock = NSLock()
    private var cases: [String: String]
    private var replies: [String: [String: Any]]

    /// `folder`: apps/contract/v1/fixtures
    public init(fixtures folder: URL, cases: [String: String] = [:], replies: [String: [String: Any]] = [:]) {
        (messages, continuation) = AsyncStream.makeStream(of: TransportMessage.self)
        var loaded: [String: [String: Any]] = [:]
        let files = FileManager.default.enumerator(at: folder, includingPropertiesForKeys: nil)
        while let url = files?.nextObject() as? URL {
            guard url.pathExtension == "json",
                  let data = try? Data(contentsOf: url),
                  let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
            loaded["\(url.deletingLastPathComponent().lastPathComponent)/\(url.deletingPathExtension().lastPathComponent)"] = body
        }
        fixtures = loaded
        self.cases = cases
        self.replies = replies
    }

    public func answer(_ op: String, with reply: [String: Any]) {
        lock.withLock { replies[op] = reply }
    }

    public func send(_ line: Data) throws {
        guard let request = try JSONSerialization.jsonObject(with: line) as? [String: Any],
              let id = request["id"] as? Int, let op = request["op"] as? String else { return }
        let (custom, name) = lock.withLock { (replies[op], cases[op]) }
        var lines: [[String: Any]] = []
        if var reply = custom {
            reply["id"] = id
            if reply["ok"] == nil { reply["ok"] = true }
            lines.append(reply)
        } else if let fixture = fixtures[name ?? standard(op)] {
            lines += fixture["events"] as? [[String: Any]] ?? []
            var reply = fixture["reply"] as? [String: Any] ?? [:]
            reply["id"] = id
            lines.append(reply)
            // events sent once the reply is out, like icons
            lines += fixture["after"] as? [[String: Any]] ?? []
        } else {
            lines.append(["id": id, "ok": false, "error": ["code": "unknown_op", "message": "no fixture for \(op)"]])
        }
        for object in lines {
            if let data = try? JSONSerialization.data(withJSONObject: object) {
                continuation.yield(.line(data))
            }
        }
        if op == "shutdown" { terminate() }
    }

    public func terminate() {
        continuation.yield(.exited(EngineExit(status: 0)))
        continuation.finish()
    }

    private func standard(_ op: String) -> String {
        if fixtures["\(op)/ok"] != nil { return "\(op)/ok" }
        return fixtures.keys.filter { $0.hasPrefix("\(op)/") && (fixtures[$0]?["reply"] as? [String: Any])?["ok"] as? Bool == true }
            .min() ?? "\(op)/ok"
    }
}
