import Foundation
import os

public enum EngineConnectionError: Error, Sendable, LocalizedError, Equatable {
    /// The engine exited with requests unanswered
    case exited(EngineExit)
    /// A reply that doesn't decode as the op's reply
    case malformedReply(op: String, detail: String)

    public var errorDescription: String? {
        switch self {
        case .exited(let exit):
            exit.signaled
                ? String(localized: "The Youpdated engine stopped unexpectedly (signal \(exit.status)).")
                : String(localized: "The Youpdated engine stopped unexpectedly (exit \(exit.status)).")
        case .malformedReply(let op, _):
            String(localized: "The Youpdated engine sent a reply this app can't read (\(op)).")
        }
    }
}

/// Requests and replies over one engine connection: ids, out-of-order replies, and events
public actor EngineClient {
    private let transport: EngineTransport
    private let decoder = ContractCoding.decoder()
    private let encoder = ContractCoding.encoder()
    private var nextId = 1
    private var pending: [Int: CheckedContinuation<Data, Error>] = [:]
    private var exit: EngineExit?
    private var reading: Task<Void, Never>?
    private let exitWaiters = ExitWaiters()
    private static let log = Logger(subsystem: "Youpdated", category: "engine")

    public nonisolated let events: AsyncStream<EngineEvent>
    private let eventContinuation: AsyncStream<EngineEvent>.Continuation

    public init(transport: EngineTransport) {
        self.transport = transport
        (events, eventContinuation) = AsyncStream.makeStream(of: EngineEvent.self, bufferingPolicy: .bufferingNewest(256))
    }

    /// Starts reading; call once
    public func start() {
        guard reading == nil else { return }
        let messages = transport.messages
        reading = Task { [weak self] in
            for await message in messages {
                guard let self else { return }
                await self.handle(message)
            }
        }
    }

    public func call<Reply: Decodable & Sendable>(_ op: String, as type: Reply.Type = Reply.self) async throws -> Reply {
        try await call(op, NoParams(), as: type)
    }

    public func call<Reply: Decodable & Sendable, Params: Encodable & Sendable>(
        _ op: String, _ params: Params, as type: Reply.Type = Reply.self
    ) async throws -> Reply {
        if let exit {
            throw EngineConnectionError.exited(exit)
        }
        let id = nextId
        nextId += 1
        let line = try encoder.encode(RequestEnvelope(id: id, op: op, params: params))
        let data: Data = try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            do {
                try transport.send(line)
            } catch {
                pending.removeValue(forKey: id)
                continuation.resume(throwing: error)
            }
        }
        let header = try decoder.decode(ReplyHeader.self, from: data)
        if header.ok == false {
            throw header.error ?? EngineError(code: .internal, message: "error reply without an error")
        }
        do {
            return try decoder.decode(Reply.self, from: data)
        } catch {
            throw EngineConnectionError.malformedReply(op: op, detail: String(describing: error))
        }
    }

    /// Waits for the engine to exit
    public func waitForExit() async -> EngineExit {
        if let exit { return exit }
        return await withCheckedContinuation { continuation in
            exitWaiters.add(continuation)
        }
    }

    public nonisolated func terminate() {
        transport.terminate()
    }

    private func handle(_ message: TransportMessage) {
        switch message {
        case .line(let data):
            receive(data)
        case .exited(let exit):
            self.exit = exit
            let waiting = pending
            pending.removeAll()
            for continuation in waiting.values {
                continuation.resume(throwing: EngineConnectionError.exited(exit))
            }
            eventContinuation.finish()
            exitWaiters.resume(exit)
        }
    }

    private func receive(_ data: Data) {
        guard let header = try? decoder.decode(ReplyHeader.self, from: data) else {
            Self.log.error("unreadable line from engine: \(String(decoding: data.prefix(200), as: UTF8.self), privacy: .public)")
            AppLog.shared.write("engine wrote a line that isn't JSON: \(String(decoding: data.prefix(200), as: UTF8.self))")
            return
        }
        if let event = header.event {
            eventContinuation.yield(decodeEvent(event, data))
            return
        }
        guard let id = header.id, let continuation = pending.removeValue(forKey: id) else {
            // bad_request for an unreadable line has a null id
            Self.log.error("reply for no pending request: \(String(decoding: data.prefix(200), as: UTF8.self), privacy: .public)")
            AppLog.shared.write("engine reply for no pending request: \(String(decoding: data.prefix(200), as: UTF8.self))")
            return
        }
        continuation.resume(returning: data)
    }

    private func decodeEvent(_ name: String, _ data: Data) -> EngineEvent {
        switch name {
        case "progress":
            if let progress = try? decoder.decode(ProgressEvent.self, from: data) {
                return .progress(progress)
            }
        case "icon":
            if let icon = try? decoder.decode(IconEvent.self, from: data) {
                return .icon(icon.keys)
            }
        default:
            break
        }
        return .unknown(name)
    }
}

public struct NoParams: Encodable, Sendable {
    public init() {}
}

struct RequestEnvelope<Params: Encodable>: Encodable {
    let id: Int
    let op: String
    let params: Params

    enum CodingKeys: String, CodingKey { case id, op }

    func encode(to encoder: Encoder) throws {
        try params.encode(to: encoder)
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(op, forKey: .op)
    }
}

struct ReplyHeader: Decodable {
    let id: Int?
    let ok: Bool?
    let event: String?
    let error: EngineError?
}

/// Continuations waiting on exit, outside the actor's state: `handle` stays synchronous
private final class ExitWaiters: @unchecked Sendable {
    private let lock = NSLock()
    private var waiting: [CheckedContinuation<EngineExit, Never>] = []
    private var result: EngineExit?

    func add(_ continuation: CheckedContinuation<EngineExit, Never>) {
        lock.lock()
        if let result {
            lock.unlock()
            continuation.resume(returning: result)
            return
        }
        waiting.append(continuation)
        lock.unlock()
    }

    func resume(_ exit: EngineExit) {
        lock.lock()
        result = exit
        let all = waiting
        waiting.removeAll()
        lock.unlock()
        for continuation in all {
            continuation.resume(returning: exit)
        }
    }
}
