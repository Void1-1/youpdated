import Foundation

public struct EngineCommand: Sendable, Hashable {
    public var executable: URL
    public var arguments: [String]
    public var environment: [String: String]
    public var directory: URL?

    public init(executable: URL, arguments: [String], environment: [String: String], directory: URL? = nil) {
        self.executable = executable
        self.arguments = arguments
        self.environment = environment
        self.directory = directory
    }

    /// `YOUPDATED_ENGINE` (environment) or `YoupdatedEngine` (Info.plist) override the bundled engine for development
    public static let commandKey = "YOUPDATED_ENGINE"
    public static let configKey = "YOUPDATED_ENGINE_CONFIG"
    public static let stateKey = "YOUPDATED_ENGINE_STATE"
    public static let infoKey = "YoupdatedEngine"

    /// The engine this app runs
    public static func resolve(
        bundle: Bundle = .main,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> EngineCommand {
        var command = engine(bundle: bundle, environment: environment)
        command.arguments += ["api", "serve"]
        let overrides = overrides(bundle: bundle, environment: environment)
        if let config = overrides.config {
            command.arguments += ["--config", config]
        }
        if let state = overrides.state {
            command.arguments += ["--state", state]
        }
        return command
    }

    /// The engine's `uninstall` of the config and history, for Remove Youpdated from this Mac
    public static func uninstall(
        bundle: Bundle = .main,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> EngineCommand? {
        let overrides = overrides(bundle: bundle, environment: environment)
        guard overrides.config == nil, overrides.state == nil else { return nil }
        var command = engine(bundle: bundle, environment: environment)
        command.arguments += ["uninstall", "--yes", "--keep-package", "--keep-app"]
        return command
    }

    /// The engine's program and its leading arguments (`python3 -m youpdated`)
    private static func engine(bundle: Bundle, environment: [String: String]) -> EngineCommand {
        let info = bundle.object(forInfoDictionaryKey: infoKey) as? [String: Any] ?? [:]
        var env = environment
        // stdout carries protocol
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        // passphrase over the pipe, not through the environment
        env.removeValue(forKey: "YOUPDATED_PASSPHRASE")
        for key in [commandKey, configKey, stateKey] {
            env.removeValue(forKey: key)
        }

        var argv: [String]
        if let raw = environment[commandKey], !raw.isEmpty {
            argv = split(raw)
        } else if let list = info["command"] as? [String], !list.isEmpty {
            argv = list
        } else {
            let helper = bundle.bundleURL
                .appending(path: "Contents/Resources/youpdated-engine/youpdated-engine")
            argv = [helper.path]
        }
        if let pythonPath = info["pythonPath"] as? String, !pythonPath.isEmpty {
            env["PYTHONPATH"] = pythonPath
        }
        let executable = argv.removeFirst()
        return EngineCommand(
            executable: locate(executable, path: env["PATH"]),
            arguments: argv,
            environment: env
        )
    }

    private static func overrides(bundle: Bundle, environment: [String: String]) -> (config: String?, state: String?) {
        let info = bundle.object(forInfoDictionaryKey: infoKey) as? [String: Any] ?? [:]
        let config = environment[configKey] ?? info["config"] as? String
        let state = environment[stateKey] ?? info["state"] as? String
        return (config?.isEmpty == false ? config : nil, state?.isEmpty == false ? state : nil)
    }

    /// Runs it to the end with no input or output, as for `uninstall`
    public func run() async -> Bool {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        if let directory { process.currentDirectoryURL = directory }
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        return await withCheckedContinuation { continuation in
            process.terminationHandler = { finished in
                continuation.resume(returning: finished.terminationReason == .exit && finished.terminationStatus == 0)
            }
            do {
                try process.run()
            } catch {
                process.terminationHandler = nil
                continuation.resume(returning: false)
            }
        }
    }

    static func split(_ raw: String) -> [String] {
        var words: [String] = []
        var current = ""
        var quoted = false
        var started = false
        for character in raw {
            if character == "\"" {
                quoted.toggle()
                started = true
            } else if character.isWhitespace && !quoted {
                if started { words.append(current) }
                current = ""
                started = false
            } else {
                current.append(character)
                started = true
            }
        }
        if started { words.append(current) }
        return words
    }

    /// Bare names (`python3`) are looked up on PATH
    static func locate(_ name: String, path: String?) -> URL {
        if name.contains("/") {
            return URL(filePath: (name as NSString).expandingTildeInPath)
        }
        let folders: [String] = (path ?? "/usr/bin:/bin").split(separator: ":").map(String.init)
            + ["/opt/homebrew/bin", "/usr/local/bin"]
        for folder in folders {
            let candidate = URL(filePath: folder).appending(path: name)
            if FileManager.default.isExecutableFile(atPath: candidate.path) {
                return candidate
            }
        }
        return URL(filePath: "/usr/bin").appending(path: name)
    }
}
