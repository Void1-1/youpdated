import Foundation
import Testing
@testable import YoupdatedKit

@Suite struct EngineClientTests {
    func connect(_ fake: FakeEngine) async -> Engine {
        let client = EngineClient(transport: fake)
        await client.start()
        return Engine(client: client)
    }

    @Test func helloRoundTrip() async throws {
        let fake = FakeEngine()
        let engine = await connect(fake)
        let hello = try await engine.hello(ClientInfo(version: "1.0.0"))
        #expect(hello.contract == 1)
        #expect(hello.engine == "1.0.0")
        let sent = try #require(fake.requests.first)
        #expect(sent["op"] as? String == "hello")
        #expect(sent["id"] as? Int == 1)
        #expect((sent["client"] as? [String: String])?["platform"] == "macos")
    }

    @Test func repliesMatchedByIdOutOfOrder() async throws {
        let fake = FakeEngine()
        fake.hold("check")
        let engine = await connect(fake)
        async let check = engine.check()
        // the check is still running when other ops are answered
        while fake.requests("check").isEmpty { await Task.yield() }
        let page = try await engine.inboxList()
        #expect(page.total == 6)
        fake.release("check")
        let reply = try await check
        #expect(reply.new == 4)
        #expect(reply.alerts.count == 2)
    }

    @Test func eventsArriveBeforeTheReply() async throws {
        let fake = FakeEngine()
        let engine = await connect(fake)
        _ = try await engine.check()
        var received: [ProgressEvent] = []
        for await event in engine.events {
            if case .progress(let progress) = event { received.append(progress) }
            if received.count == 7 { break }
        }
        #expect(received.first == ProgressEvent(done: 0, of: 6, target: nil, failed: nil))
        #expect(received.last?.failed == true)
        #expect(received.last?.target?.display == "@SomeChannel")
    }

    @Test func errorRepliesThrow() async throws {
        let fake = FakeEngine()
        fake.answer("targets.preview", with: "targets.preview/ambiguous")
        let engine = await connect(fake)
        await #expect(throws: EngineError(code: .ambiguous, message: "`express` could be homebrew or npm or pypi", choices: ["homebrew", "npm", "pypi"])) {
            _ = try await engine.preview(value: "express")
        }
    }

    @Test func unknownOpsAreErrors() async throws {
        let fake = FakeEngine()
        let engine = await connect(fake)
        do {
            let _: EmptyReply = try await engine.client.call("inbox.purge")
            Issue.record("expected an error")
        } catch let error as EngineError {
            #expect(error.code == .unknownOp)
        }
    }

    @Test func garbageAndStrayRepliesAreSkipped() async throws {
        let fake = FakeEngine()
        let engine = await connect(fake)
        fake.emit("not json")
        fake.emit(#"{"id":null,"ok":false,"error":{"code":"bad_request","message":"unreadable line"}}"#)
        fake.emit(#"{"id":999,"ok":true}"#)
        fake.emit(#"{"event":"something_new","x":1}"#)
        let status = try await engine.status()
        #expect(status.lastRunBy == .app)
    }

    @Test func exitFailsPendingRequests() async throws {
        let fake = FakeEngine()
        fake.hold("check")
        let engine = await connect(fake)
        async let check = engine.check()
        while fake.requests("check").isEmpty { await Task.yield() }
        fake.crash(status: 1, stderr: "RuntimeError: boom")
        do {
            _ = try await check
            Issue.record("expected the check to fail")
        } catch let error as EngineConnectionError {
            guard case .exited(let exit) = error else { Issue.record("\(error)"); return }
            #expect(exit.status == 1)
            #expect(exit.stderr.contains("boom"))
        }
        // and later calls fail at once
        await #expect(throws: EngineConnectionError.self) { _ = try await engine.status() }
        let exit = await engine.client.waitForExit()
        #expect(!exit.clean)
    }
}

@Suite struct LineFramerTests {
    @Test func splitsOnNewlinesOnly() {
        var framer = LineFramer()
        // U+2028 is valid unescaped in JSON and must not end a line
        let text = "{\"a\":\"x\u{2028}y\"}\n{\"b\":1}\r\n{\"c\""
        let lines = framer.append(Data(text.utf8))
        #expect(lines.map { String(decoding: $0, as: UTF8.self) } == ["{\"a\":\"x\u{2028}y\"}", "{\"b\":1}"])
        #expect(framer.append(Data(":2}\n".utf8)).map { String(decoding: $0, as: UTF8.self) } == ["{\"c\":2}"])
        #expect(framer.finish() == nil)
    }

    @Test func keepsTheTail() {
        var framer = LineFramer()
        #expect(framer.append(Data("partial".utf8)).isEmpty)
        #expect(framer.finish().map { String(decoding: $0, as: UTF8.self) } == "partial")
    }

    @Test func tailBufferKeepsLastLines() {
        var tail = TailBuffer(limit: 2)
        tail.append(Data("one\ntwo\nthree\nfour".utf8))
        #expect(tail.text() == "three\nfour")
    }
}

@Suite struct EngineCommandTests {
    @Test func developmentOverride() {
        let command = EngineCommand.resolve(
            bundle: .main,
            environment: [
                "YOUPDATED_ENGINE": "/usr/bin/python3 -m youpdated",
                "YOUPDATED_ENGINE_CONFIG": "/tmp/y/config.yaml",
                "YOUPDATED_PASSPHRASE": "secret",
                "PATH": "/usr/bin",
            ])
        #expect(command.executable.path == "/usr/bin/python3")
        #expect(command.arguments == ["-m", "youpdated", "api", "serve", "--config", "/tmp/y/config.yaml"])
        #expect(command.environment["YOUPDATED_PASSPHRASE"] == nil)
        #expect(command.environment["PYTHONUNBUFFERED"] == "1")
    }

    @Test func bundledEngineByDefault() {
        let command = EngineCommand.resolve(bundle: .main, environment: [:])
        #expect(command.executable.path.hasSuffix("Contents/Resources/youpdated-engine/youpdated-engine"))
        #expect(command.arguments == ["api", "serve"])
    }

    @Test func splitsQuotedWords() {
        #expect(EngineCommand.split(#"  "/path with space/python" -m  youpdated "#) == ["/path with space/python", "-m", "youpdated"])
    }

    @Test func missingEngineIsAnError() {
        let command = EngineCommand(executable: URL(filePath: "/nonexistent/youpdated-engine"), arguments: [], environment: [:])
        #expect(throws: EngineLaunchError.self) { _ = try ProcessTransport(command: command) }
    }

    @Test func processTransportFramesLines() async throws {
        let transport = try ProcessTransport(command: EngineCommand(
            executable: URL(filePath: "/bin/cat"), arguments: [], environment: [:]))
        try transport.send(Data(#"{"id":1,"ok":true}"#.utf8))
        try transport.send(Data(#"{"id":2,"ok":true,"t":"xy"}"#.utf8))
        transport.closeInput()
        var lines: [String] = []
        var exit: EngineExit?
        for await message in transport.messages {
            switch message {
            case .line(let data): lines.append(String(decoding: data, as: UTF8.self))
            case .exited(let ended): exit = ended
            }
        }
        #expect(lines == [#"{"id":1,"ok":true}"#, #"{"id":2,"ok":true,"t":"xy"}"#])
        #expect(exit?.status == 0)
    }
}
