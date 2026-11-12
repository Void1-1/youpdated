import Foundation
import Testing
@testable import YoupdatedKit

/// Every fixture decodes into the app models
@Suite struct ContractTests {
    nonisolated(unsafe) static let replyTypes: [String: any Decodable.Type] = [
        "hello": HelloReply.self, "unlock": UnlockReply.self, "status": StatusReply.self,
        "check": CheckReply.self, "inbox.list": InboxPage.self, "inbox.mark": MarkReply.self,
        "inbox.snooze": SnoozeReply.self, "inbox.delete": DeleteReply.self, "targets.list": TargetsReply.self,
        "targets.preview": PreviewReply.self, "targets.add": AddReply.self,
        "targets.remove": RemoveReply.self, "import.preview": ImportPreviewReply.self,
        "import.apply": ImportApplyReply.self, "settings.get": SettingsReply.self,
        "settings.set": SettingsReply.self, "priority.set": PriorityReply.self,
        "problems.list": ProblemsReply.self, "digest.send": DigestReply.self,
        "snapshot.write": SnapshotReply.self, "config.init": ConfigInitReply.self,
        "shutdown": EmptyReply.self, "icon.get": IconReply.self,
        "privacy.get": PrivacyReply.self, "privacy.set": PrivacyReply.self,
    ]

    @Test func everyOpHasAReplyType() throws {
        let hello: HelloReply = try Fixtures.reply("hello/ok")
        #expect(Set(hello.ops) == Set(Self.replyTypes.keys))
        let fixtureOps = Set(Fixtures.all.values.map(\.op)).subtracting(["inbox.purge"])
        #expect(fixtureOps.isSubset(of: Set(Self.replyTypes.keys)))
    }

    @Test(arguments: Fixtures.all.keys.sorted())
    func replyDecodes(_ name: String) throws {
        let fixture = Fixtures.named(name)
        let decoder = ContractCoding.decoder()
        let header = try decoder.decode(ReplyHeader.self, from: fixture.replyData)
        #expect(header.id == fixture.request["id"] as? Int)
        if fixture.ok {
            let type = try #require(Self.replyTypes[fixture.op], "no reply type for \(fixture.op)")
            _ = try decoder.decode(type, from: fixture.replyData)
        } else {
            let error = try #require(header.error)
            if case .other(let code) = error.code {
                Issue.record("unknown error code \(code)")
            }
            #expect(!error.message.isEmpty)
        }
        for line in fixture.eventLines {
            let event = try decoder.decode(ReplyHeader.self, from: line)
            #expect(event.event == "progress")
            _ = try decoder.decode(ProgressEvent.self, from: line)
        }
        for line in fixture.afterLines {
            let event = try decoder.decode(ReplyHeader.self, from: line)
            #expect(event.event == "icon")
            #expect(try decoder.decode(IconEvent.self, from: line).keys.count == 1)
        }
    }

    @Test func timestampsAreUTCSeconds() throws {
        let page: InboxPage = try Fixtures.reply("inbox.list/ok")
        let item = try #require(page.items.first { $0.id == "i_8f3a1c2e9b7d4a10" })
        #expect(ContractCoding.formatTimestamp(item.foundAt) == "2026-10-29T08:00:04Z")
        #expect(item.published.map(ContractCoding.formatTimestamp) == "2026-10-28T14:02:11Z")
        #expect(item.priority == .high)
        #expect(item.snoozedUntil == nil)
    }

    @Test func ambiguousCarriesChoices() throws {
        let header = try ContractCoding.decoder().decode(
            ReplyHeader.self, from: Fixtures.named("targets.preview/ambiguous").replyData)
        #expect(header.error?.code == .ambiguous)
        #expect(header.error?.choices == ["homebrew", "npm", "pypi"])
    }

    @Test func unknownErrorCodesAndProblemKindsDecode() throws {
        let error = try ContractCoding.decoder().decode(
            EngineError.self, from: Data(#"{"code":"rate_limited","message":"slow down"}"#.utf8))
        #expect(error.code == .other("rate_limited"))
        let problem = try ContractCoding.decoder().decode(
            Problem.self, from: Data(#"{"kind":"plugin","source":"x","target":"-","message":"m"}"#.utf8))
        #expect(problem.kind == .other)
    }

    @Test func iconIsBase64() throws {
        let found: IconReply = try Fixtures.reply("icon.get/found")
        #expect(found.icon?.mime == "image/png")
        #expect(found.icon?.data.prefix(4) == Data([0x89, 0x50, 0x4E, 0x47]))
        let none: IconReply = try Fixtures.reply("icon.get/none")
        #expect(none.icon == nil)
    }

    @Test func snapshotsDecode() throws {
        let files = Fixtures.snapshots
        #expect(files.count == 2)
        for url in files {
            let snapshot = try #require(WidgetSnapshot.read(from: url), "\(url.lastPathComponent)")
            #expect(snapshot.version == 1)
            #expect(snapshot.redacted == snapshot.items.isEmpty)
        }
    }

    /// The params this app sends match requests from fixtures
    @Test(arguments: [
        ("hello/ok", Params.Hello(client: ClientInfo(version: "1.0.0")) as any Encodable & Sendable),
        ("unlock/ok", Params.Unlock(passphrase: "correct horse")),
        ("check/targets", Params.Check(sources: nil, targets: [TargetKey(source: "itch", key: "https://foo.itch.io/bar")], notify: nil)),
        ("inbox.list/ok", Params.InboxList(unread: nil, source: nil, target: nil, sort: .newest, limit: 100, cursor: nil)),
        ("inbox.list/page", Params.InboxList(unread: true, source: nil, target: nil, sort: nil, limit: 2, cursor: nil)),
        ("inbox.mark/ids", Params.Mark(read: true, ids: ["i_8f3a1c2e9b7d4a10"], all: nil, source: nil)),
        ("inbox.mark/all", Params.Mark(read: true, ids: nil, all: true, source: nil)),
        ("inbox.snooze/ok", Params.Snooze(ids: ["i_2b9e5d7f0c1a3e44"], minutes: 60)),
        ("inbox.delete/ok", Params.Delete(ids: ["i_8f3a1c2e9b7d4a10"])),
        ("targets.preview/picked", Params.Preview(value: "express", source: "npm")),
        ("targets.add/ok", Params.Add(source: "git", entries: ["python/cpython"])),
        ("targets.remove/wipe", Params.Remove(source: "steam", key: "440", wipe: true)),
        ("targets.remove/ok", Params.Remove(source: "npm", key: "react", wipe: nil)),
        ("import.preview/content", Params.Import(.content("httpx>=0.27\nrich\n", format: "requirements.txt"))),
        ("import.apply/ok", Params.Import(.path("/Users/me/project/package.json"))),
        ("settings.set/ok", Params.SetSettings(settings: SettingsPatch(digestTime: .some("18:00")))),
        ("privacy.set/tor", Params.SetPrivacy(proxy: "socks5://127.0.0.1:9050", fastMode: nil)),
        ("privacy.set/off", Params.SetPrivacy(proxy: .some(nil), fastMode: false)),
        ("priority.set/ok", Params.SetPriority(source: "steam", key: "440", priority: .high)),
        ("config.init/existing", Params.ConfigInit(selfWatch: true)),
        ("icon.get/found", Params.Icon(source: "steam", key: "440")),
    ])
    func requestsMatchFixtures(_ name: String, _ params: any Encodable & Sendable) throws {
        let fixture = Fixtures.named(name)
        var expected = fixture.request
        expected.removeValue(forKey: "id")
        expected.removeValue(forKey: "op")
        if name == "hello/ok" {
            expected["client"] = ["name": "Youpdated", "version": "1.0.0", "platform": "macos"]
        }
        let data = try ContractCoding.encoder().encode(params)
        let encoded = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        #expect(NSDictionary(dictionary: encoded).isEqual(to: expected), "\(encoded) != \(expected)")
    }

    @Test func turningTheDigestOffSendsNull() throws {
        let data = try ContractCoding.encoder().encode(SettingsPatch(digestTime: .some(nil)))
        #expect(String(decoding: data, as: UTF8.self) == #"{"digest_time":null}"#)
        let unchanged = try ContractCoding.encoder().encode(SettingsPatch(osNotifications: false))
        #expect(String(decoding: unchanged, as: UTF8.self) == #"{"os_notifications":false}"#)
    }

    @Test func envelopePutsParamsBesideIdAndOp() throws {
        let data = try ContractCoding.encoder().encode(
            RequestEnvelope(id: 7, op: "targets.preview", params: Params.Preview(value: "express", source: nil)))
        #expect(String(decoding: data, as: UTF8.self) == #"{"id":7,"op":"targets.preview","value":"express"}"#)
        let empty = try ContractCoding.encoder().encode(RequestEnvelope(id: 1, op: "status", params: NoParams()))
        #expect(String(decoding: empty, as: UTF8.self) == #"{"id":1,"op":"status"}"#)
    }
}
