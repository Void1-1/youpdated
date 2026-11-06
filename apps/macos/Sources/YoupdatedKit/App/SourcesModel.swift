import Foundation
import Observation

/// Paste a link or name -> open preview -> (pick a source) -> add -> check it
public enum AddFlow: Hashable, Sendable {
    case idle
    case previewing(String)
    /// More than one source could apply
    case choosing(String, choices: [String])
    case ready(String, PreviewReply)
    case adding(String, PreviewReply)
    case added([TargetRef])
    case failed(String, message: String)

    public var value: String? {
        switch self {
        case .idle, .added: nil
        case .previewing(let value), .choosing(let value, _), .ready(let value, _),
             .adding(let value, _), .failed(let value, _): value
        }
    }

    public var isBusy: Bool {
        switch self {
        case .previewing, .adding: true
        default: false
        }
    }
}

/// Import a package.json, requirements.txt, or Brewfile: preview, then confirm
public enum ImportFlow: Hashable, Sendable {
    case idle
    case previewing(ImportInput)
    case ready(ImportInput, ImportPreviewReply)
    case applying(ImportInput, ImportPreviewReply)
    case done(ImportApplyReply.Summary)
    case failed(String)
}

extension ImportApplyReply {
    public struct Summary: Hashable, Sendable {
        public var added: Int
        public var skipped: Int
    }

    public var summary: Summary {
        Summary(added: added.reduce(0) { $0 + $1.entries.count }, skipped: skipped.count)
    }
}

@MainActor
@Observable
public final class SourcesModel {
    /// Youpdated's own entry, then by name
    public private(set) var targets: [Target] = []
    public private(set) var loaded = false
    public private(set) var add: AddFlow = .idle
    public private(set) var importing: ImportFlow = .idle

    weak var app: AppModel?

    init() {}

    public var selfTarget: Target? { targets.first(where: \.starred) }

    public func reload() async {
        guard let engine = app?.engine else { return }
        do {
            targets = Self.ordered(try await engine.targets().targets)
            loaded = true
        } catch {
            app?.report(error, doing: String(localized: "Couldn't load what Youpdated watches."))
        }
    }

    public nonisolated static func ordered(_ targets: [Target]) -> [Target] {
        targets.sorted { a, b in
            if a.starred != b.starred { return a.starred }
            let order = a.display.localizedStandardCompare(b.display)
            if order != .orderedSame { return order == .orderedAscending }
            return (a.source, a.key) < (b.source, b.key)
        }
    }

    public func preview(_ value: String, source: String? = nil) async {
        let value = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, let engine = app?.engine else { return }
        add = .previewing(value)
        do {
            let reply = try await engine.preview(value: value, source: source)
            guard add == .previewing(value) else { return }
            add = .ready(value, reply)
        } catch let error as EngineError where error.code == .ambiguous {
            add = .choosing(value, choices: error.choices ?? [])
        } catch {
            add = .failed(value, message: Self.message(for: error))
        }
    }

    /// After `.choosing`: preview again as the source the user picked
    public func choose(source: String) async {
        guard case .choosing(let value, _) = add else { return }
        await preview(value, source: source)
    }

    public func confirmAdd(priority: Priority = .normal) async {
        guard case .ready(let value, let preview) = add, let engine = app?.engine else { return }
        add = .adding(value, preview)
        do {
            let reply = try await engine.add(source: preview.source, entries: [preview.entry])
            add = .added(reply.targets)
            if priority != .normal {
                for target in reply.targets {
                    _ = try? await engine.setPriority(priority, source: target.source, key: target.key)
                }
            }
            await reload()
            // a new target's first check records a baseline; its newest item shows as read
            await app?.checkNow(targets: reply.targets.map(\.targetKey))
        } catch {
            add = .failed(value, message: Self.message(for: error))
        }
    }

    public func resetAdd() {
        add = .idle
    }

    public func remove(_ target: Target, wipe: Bool) async {
        guard let engine = app?.engine else { return }
        do {
            _ = try await engine.remove(source: target.source, key: target.key, wipe: wipe)
            targets.removeAll { $0.id == target.id }
        } catch {
            app?.report(error, doing: String(localized: "Couldn't stop watching \(target.display)."))
        }
        await reload()
        if wipe { await app?.inbox.reload() }
    }

    public func previewImport(_ input: ImportInput) async {
        guard let engine = app?.engine else { return }
        importing = .previewing(input)
        do {
            importing = .ready(input, try await engine.importPreview(input))
        } catch {
            importing = .failed(Self.message(for: error))
        }
    }

    public func confirmImport() async {
        guard case .ready(let input, let preview) = importing, let engine = app?.engine else { return }
        importing = .applying(input, preview)
        do {
            let reply = try await engine.importApply(input)
            importing = .done(reply.summary)
            await reload()
            if !reply.targets.isEmpty {
                await app?.checkNow(targets: reply.targets.map(\.targetKey))
            }
        } catch {
            importing = .failed(Self.message(for: error))
        }
    }

    public func resetImport() {
        importing = .idle
    }

    func priorityChanged(_ priority: Priority, for key: TargetKey) {
        if let index = targets.firstIndex(where: { $0.id == key }) {
            targets[index].priority = priority
        }
    }

    static func message(for error: Error) -> String {
        (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    }
}
