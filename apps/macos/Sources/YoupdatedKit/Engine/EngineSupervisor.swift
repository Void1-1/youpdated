import Foundation

public enum EngineStartError: Error, LocalizedError, Equatable, Sendable {
    /// The engine uses another contract
    case incompatible(engine: String, contract: Int)

    public var errorDescription: String? {
        switch self {
        case .incompatible(let engine, let contract):
            String(localized: "This Youpdated engine (\(engine)) uses contract \(contract), and this app needs \(ContractCoding.major). Reinstall or update Youpdated.")
        }
    }
}

/// Owns the engine process: starts, runs handshake, and restarts with backoff
@MainActor
public final class EngineSupervisor {
    public typealias Launch = @Sendable () throws -> EngineTransport
    public typealias Sleep = @Sendable (Duration) async throws -> Void

    public private(set) var engine: Engine?
    public private(set) var hello: HelloReply?
    /// Restarts since the engine last stayed up for `healthyAfter`
    public private(set) var crashes = 0

    /// After a (re)start and handshake
    public var onReady: ((Engine, HelloReply) async -> Void)?
    public var onEvent: ((EngineEvent) -> Void)?
    /// A crash, or a restart that failed
    public var onProblem: ((AppProblem) -> Void)?

    private let launch: Launch
    private let client: ClientInfo
    private let sleep: Sleep
    private let now: @Sendable () -> Date
    private var stopping = false
    private var startedAt: Date?
    private var monitor: Task<Void, Never>?
    private var events: Task<Void, Never>?

    public static let healthyAfter: TimeInterval = 120

    public init(
        launch: @escaping Launch, client: ClientInfo,
        sleep: @escaping Sleep = { try await Task.sleep(for: $0) },
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.launch = launch
        self.client = client
        self.sleep = sleep
        self.now = now
    }

    /// 1s, 2s, 4s, up to 60
    public nonisolated static func backoff(after crashes: Int) -> Duration {
        .seconds(min(60, 1 << min(max(crashes - 1, 0), 6)))
    }

    /// Starts the engine and runs `hello` handshake
    @discardableResult
    public func start() async throws -> (Engine, HelloReply) {
        stopping = false
        let transport = try launch()
        let connection = EngineClient(transport: transport)
        await connection.start()
        let engine = Engine(client: connection)
        let hello: HelloReply
        do {
            hello = try await engine.hello(client)
        } catch {
            connection.terminate()
            throw error
        }
        guard hello.contract == ContractCoding.major else {
            try? await engine.shutdown()
            connection.terminate()
            throw EngineStartError.incompatible(engine: hello.engine, contract: hello.contract)
        }
        self.engine = engine
        self.hello = hello
        startedAt = now()
        watch(engine)
        return (engine, hello)
    }

    /// `shutdown`, then the process is ended if it hasn't gone within `grace`
    public func stop(grace: Duration = .seconds(2)) async {
        stopping = true
        monitor?.cancel()
        events?.cancel()
        guard let engine else { return }
        self.engine = nil
        let client = engine.client
        await withTaskGroup(of: Void.self) { group in
            group.addTask { try? await engine.shutdown() }
            group.addTask { try? await Task.sleep(for: grace) }
            await group.next()
            group.cancelAll()
        }
        client.terminate()
    }

    private func watch(_ engine: Engine) {
        events?.cancel()
        events = Task { [weak self] in
            for await event in engine.events {
                self?.onEvent?(event)
            }
        }
        monitor?.cancel()
        monitor = Task { [weak self] in
            let exit = await engine.client.waitForExit()
            await self?.exited(exit)
        }
    }

    private func exited(_ exit: EngineExit) async {
        guard !stopping else { return }
        engine = nil
        if let startedAt, now().timeIntervalSince(startedAt) > Self.healthyAfter {
            crashes = 0
        }
        crashes += 1
        let detail = exit.stderr.split(separator: "\n").suffix(8).joined(separator: "\n")
        onProblem?(AppProblem(
            title: EngineConnectionError.exited(exit).errorDescription ?? "",
            detail: detail.isEmpty ? String(localized: "It left no message.") : detail,
            date: now()))
        while !stopping {
            try? await sleep(Self.backoff(after: crashes))
            guard !stopping else { return }
            do {
                let (engine, hello) = try await start()
                await onReady?(engine, hello)
                return
            } catch {
                crashes += 1
                onProblem?(AppProblem(
                    title: String(localized: "The Youpdated engine couldn't restart."),
                    detail: error.localizedDescription, date: now()))
                if error is EngineStartError || error is EngineLaunchError { return }
            }
        }
    }
}
