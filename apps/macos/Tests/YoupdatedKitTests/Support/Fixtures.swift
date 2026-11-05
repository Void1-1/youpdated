import Foundation
@testable import YoupdatedKit

/// An exchange from apps/contract/v1/fixtures
struct Fixture: @unchecked Sendable {
    let name: String
    let op: String
    let request: [String: Any]
    let events: [[String: Any]]
    let reply: [String: Any]
    /// Events sent after the reply
    let after: [[String: Any]]

    var ok: Bool { reply["ok"] as? Bool ?? false }

    /// The reply as a line with `id` replaced
    func replyLine(id: Int) -> Data {
        var reply = reply
        reply["id"] = id
        return try! JSONSerialization.data(withJSONObject: reply, options: [.sortedKeys])
    }

    var replyData: Data {
        try! JSONSerialization.data(withJSONObject: reply, options: [.sortedKeys])
    }

    var eventLines: [Data] {
        events.map { try! JSONSerialization.data(withJSONObject: $0, options: [.sortedKeys]) }
    }

    var afterLines: [Data] {
        after.map { try! JSONSerialization.data(withJSONObject: $0, options: [.sortedKeys]) }
    }
}

enum Fixtures {
    static let root = URL(filePath: #filePath)
        .deletingLastPathComponent()  // Support
        .deletingLastPathComponent()  // YoupdatedKitTests
        .deletingLastPathComponent()  // Tests
        .deletingLastPathComponent()  // macos
        .deletingLastPathComponent()  // apps
        .appending(path: "contract/v1/fixtures")

    /// Exchanges, by "<op folder>/<case>"
    static let all: [String: Fixture] = {
        var fixtures: [String: Fixture] = [:]
        let files = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)!
        for case let url as URL in files where url.pathExtension == "json" {
            let folder = url.deletingLastPathComponent().lastPathComponent
            guard folder != "_snapshot" else { continue }
            let name = "\(folder)/\(url.deletingPathExtension().lastPathComponent)"
            let body = try! JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
            let request = body["request"] as! [String: Any]
            fixtures[name] = Fixture(
                name: name, op: request["op"] as! String, request: request,
                events: body["events"] as! [[String: Any]], reply: body["reply"] as! [String: Any],
                after: body["after"] as? [[String: Any]] ?? [])
        }
        precondition(!fixtures.isEmpty, "no fixtures under \(root.path)")
        return fixtures
    }()

    static func named(_ name: String) -> Fixture {
        guard let fixture = all[name] else { fatalError("no fixture \(name)") }
        return fixture
    }

    /// `<op>/ok`, else the op's first ok case by name
    static func standard(for op: String) -> String? {
        if all["\(op)/ok"] != nil { return "\(op)/ok" }
        return all.values.filter { $0.op == op && $0.ok && !$0.name.hasPrefix("_") }.map(\.name).min()
    }

    static var snapshots: [URL] {
        (try? FileManager.default.contentsOfDirectory(at: root.appending(path: "_snapshot"), includingPropertiesForKeys: nil)) ?? []
    }

    static func reply<T: Decodable>(_ name: String, as type: T.Type = T.self) throws -> T {
        try ContractCoding.decoder().decode(T.self, from: named(name).replyData)
    }
}
