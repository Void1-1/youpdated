import Foundation

/// How an engine process ended
public struct EngineExit: Sendable, Hashable {
    public var status: Int32
    /// Killed by a signal rather than exiting
    public var signaled: Bool
    /// last lines written to sterr
    public var stderr: String

    public init(status: Int32, signaled: Bool = false, stderr: String = "") {
        self.status = status
        self.signaled = signaled
        self.stderr = stderr
    }

    public var clean: Bool { status == 0 && !signaled }
}

public enum TransportMessage: Sendable {
    /// Protocol line, without newline
    case line(Data)
    case exited(EngineExit)
}

/// The pipe pair to an engine
public protocol EngineTransport: Sendable {
    var messages: AsyncStream<TransportMessage> { get }
    func send(_ line: Data) throws
    func terminate()
}

/// Splits a byte stream into lines on `\n` only
public struct LineFramer: Sendable {
    private var buffer = Data()

    public init() {}

    public mutating func append(_ data: Data) -> [Data] {
        buffer.append(data)
        var lines: [Data] = []
        while let newline = buffer.firstIndex(of: 0x0A) {
            var line = buffer[buffer.startIndex..<newline]
            if line.last == 0x0D {
                line = line.dropLast()
            }
            if !line.isEmpty {
                lines.append(Data(line))
            }
            buffer = Data(buffer[buffer.index(after: newline)...])
        }
        return lines
    }

    /// Whatever followed the last newline
    public mutating func finish() -> Data? {
        defer { buffer = Data() }
        return buffer.isEmpty ? nil : buffer
    }
}

/// Keeps the last few lines of a stream for diagnostics
struct TailBuffer: Sendable {
    let limit: Int
    private(set) var lines: [String] = []
    private var framer = LineFramer()

    init(limit: Int = 40) { self.limit = limit }

    mutating func append(_ data: Data) {
        for line in framer.append(data) {
            lines.append(String(decoding: line, as: UTF8.self))
        }
        if lines.count > limit {
            lines.removeFirst(lines.count - limit)
        }
    }

    mutating func text() -> String {
        if let rest = framer.finish() {
            lines.append(String(decoding: rest, as: UTF8.self))
        }
        return lines.suffix(limit).joined(separator: "\n")
    }
}

public enum EngineLaunchError: Error, LocalizedError, Sendable {
    case notFound(String)
    case failed(String)

    public var errorDescription: String? {
        switch self {
        case .notFound(let path):
            String(localized: "The Youpdated engine is missing from \(path).")
        case .failed(let reason):
            String(localized: "The Youpdated engine couldn't start: \(reason)")
        }
    }
}

/// An engine run as a child process, over stdin and stdout
public final class ProcessTransport: EngineTransport, @unchecked Sendable {
    public let messages: AsyncStream<TransportMessage>
    private let continuation: AsyncStream<TransportMessage>.Continuation
    private let process = Process()
    private let input = Pipe()
    private let lock = NSLock()
    private var framer = LineFramer()
    private var tail = TailBuffer()
    private var outputClosed = false
    private var exitStatus: (Int32, Bool)?

    public init(command: EngineCommand) throws {
        (messages, continuation) = AsyncStream.makeStream(of: TransportMessage.self)
        guard FileManager.default.isExecutableFile(atPath: command.executable.path) else {
            throw EngineLaunchError.notFound(command.executable.path)
        }
        process.executableURL = command.executable
        process.arguments = command.arguments
        process.environment = command.environment
        if let directory = command.directory {
            process.currentDirectoryURL = directory
        }
        let output = Pipe()
        let errors = Pipe()
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errors

        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            self?.received(stdout: data)
        }
        errors.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
            } else {
                self?.received(stderr: data)
            }
        }
        process.terminationHandler = { [weak self] process in
            self?.ended(status: process.terminationStatus, signaled: process.terminationReason == .uncaughtSignal)
        }
        do {
            try process.run()
        } catch {
            throw EngineLaunchError.failed(error.localizedDescription)
        }
    }

    public func send(_ line: Data) throws {
        var data = line
        data.append(0x0A)
        try input.fileHandleForWriting.write(contentsOf: data)
    }

    public func terminate() {
        closeInput()
        if process.isRunning {
            process.terminate()
        }
    }

    /// The engine considers the end of stdin `shutdown`
    public func closeInput() {
        try? input.fileHandleForWriting.close()
    }

    public var processIdentifier: Int32 { process.processIdentifier }

    private func received(stdout data: Data) {
        lock.lock()
        defer { lock.unlock() }
        guard !outputClosed else { return }
        if data.isEmpty {
            outputClosed = true
            if let rest = framer.finish() {
                continuation.yield(.line(rest))
            }
            finishIfDone()
            return
        }
        for line in framer.append(data) {
            continuation.yield(.line(line))
        }
    }

    private func received(stderr data: Data) {
        lock.lock()
        tail.append(data)
        lock.unlock()
        // logs go on to the app's own stderr, so Console and the terminal show
        FileHandle.standardError.write(data)
    }

    private func ended(status: Int32, signaled: Bool) {
        lock.lock()
        defer { lock.unlock() }
        exitStatus = (status, signaled)
        finishIfDone()
    }

    /// Caller holds lock. Every stdout line goes out before `.exited`.
    private func finishIfDone() {
        guard outputClosed, let (status, signaled) = exitStatus else { return }
        continuation.yield(.exited(EngineExit(status: status, signaled: signaled, stderr: tail.text())))
        continuation.finish()
    }
}
