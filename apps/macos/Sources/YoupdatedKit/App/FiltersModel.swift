import Foundation
import Observation

public struct FilterDraft: Hashable, Sendable {
    /// "Only versions like", not every version
    public var limitsVersions = false
    public var ticked: Set<String> = []
    public var custom = ""
    public var skipPrereleases = false
    public var bump: Bump = .any
    public var min = ""

    public init() {}

    /// Loaded rules
    public init(_ filters: Filters) {
        limitsVersions = !filters.match.isEmpty
        custom = filters.match.joined(separator: ", ")
        skipPrereleases = filters.skipPrereleases
        bump = filters.bump
        min = filters.min ?? ""
    }

    public var typed: [String] {
        custom.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    public func match(offered: [String]) -> [String] {
        guard limitsVersions else { return [] }
        var seen = Set<String>()
        return (offered.filter(ticked.contains) + typed).filter { seen.insert($0).inserted }
    }

    public var minimum: String? {
        let trimmed = min.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// Typed patterns in `offered` become ticks
    public mutating func sort(offered: [String]) {
        let known = Set(offered)
        let typed = typed
        ticked.formUnion(typed.filter(known.contains))
        custom = typed.filter { !known.contains($0) }.joined(separator: ", ")
    }
}

/// An item's Filters sheet: load its rules, preview a draft, save
@MainActor
@Observable
public final class FiltersModel {
    public enum Phase: Hashable, Sendable {
        case loading, ready, saving
        /// The rules couldn't be loaded
        case failed(String)
    }

    public let target: Target
    public private(set) var phase: Phase = .loading
    /// The rules as last loaded or saved
    public private(set) var saved: Filters?
    public private(set) var preview: FiltersPreview?
    public private(set) var isPreviewing = false
    /// Why a save was refused
    public private(set) var problem: String?
    public var draft = FilterDraft() {
        didSet { if draft != oldValue { problem = nil } }
    }

    private weak var app: AppModel?
    private var previewTask: Task<Void, Never>?
    var settle: Duration = .milliseconds(250)

    init(target: Target, app: AppModel) {
        self.target = target
        self.app = app
    }

    public var offered: [FiltersPreview.Pattern] { preview?.patterns ?? [] }
    public var match: [String] { draft.match(offered: offered.map(\.pattern)) }
    public var isEditable: Bool { saved?.editable ?? false }

    public var hasChanges: Bool {
        guard let saved else { return false }
        // a version fits any pattern, so their order isn't a change
        return Set(match) != Set(saved.match) || draft.skipPrereleases != saved.skipPrereleases
            || draft.bump != saved.bump || draft.minimum != saved.min
    }

    public var canSave: Bool { phase == .ready && isEditable && hasChanges }

    /// "Only versions like" with nothing selected would keep every version
    public var limitsNothing: Bool { draft.limitsVersions && match.isEmpty }

    public func load() async {
        guard let engine = app?.engine else { return }
        phase = .loading
        do {
            let filters = try await engine.filters(source: target.source, key: target.key)
            saved = filters
            draft = FilterDraft(filters)
            draft.sort(offered: offered.map(\.pattern))
            phase = .ready
        } catch {
            phase = .failed(SourcesModel.message(for: error))
            return
        }
        await runPreview(refresh: false)
    }

    /// Previews the draft after it rested
    public func draftChanged() {
        previewTask?.cancel()
        previewTask = Task { [settle] in
            try? await Task.sleep(for: settle)
            guard !Task.isCancelled else { return }
            await runPreview(refresh: false)
        }
    }

    public func refresh() async {
        previewTask?.cancel()
        await runPreview(refresh: true)
    }

    private func runPreview(refresh: Bool) async {
        guard let engine = app?.engine else { return }
        isPreviewing = true
        defer { isPreviewing = false }
        let asked = draft
        do {
            let reply = try await engine.previewFilters(
                source: target.source, key: target.key, match: match, skipPrereleases: draft.skipPrereleases,
                min: draft.minimum, refresh: refresh ? true : nil)
            let first = preview == nil
            preview = reply
            if first, draft == asked { draft.sort(offered: reply.patterns.map(\.pattern)) }
        } catch {
            // a version it can't read yet
        }
    }

    /// True when saved, and the sheet can close
    public func save() async -> Bool {
        guard canSave, let app, let engine = app.engine else { return false }
        phase = .saving
        problem = nil
        do {
            saved = try await engine.setFilters(
                source: target.source, key: target.key, match: match,
                skipPrereleases: draft.skipPrereleases, bump: draft.bump, min: draft.minimum)
            phase = .ready
        } catch {
            problem = SourcesModel.message(for: error)
            phase = .ready
            return false
        }
        // changed rules read the source in full, and may turn up versions the old ones dropped
        Task { await app.checkNow(targets: [target.id]) }
        return true
    }
}

extension SourcesModel {
    /// The engine has `filters.*`
    public var canFilter: Bool { app?.hello?.ops.contains("filters.get") == true }

    public func filters(for target: Target) -> FiltersModel? {
        app.map { FiltersModel(target: target, app: $0) }
    }
}

/// The Filters sheet's wording
public enum FiltersText {
    public static func bump(_ bump: Bump) -> String {
        switch bump {
        case .any: String(localized: "Every version")
        case .patch: String(localized: "New patch versions")
        case .minor: String(localized: "New minor versions")
        case .major: String(localized: "New major versions")
        }
    }

    /// "4 of the last 20 versions fit: 1.2.0, 1.1.0, 1.0.0"
    public static func fits(_ preview: FiltersPreview, naming: Int = 3) -> String {
        let total = preview.versions.count
        guard total > 0 else { return String(localized: "It lists no versions to filter.") }
        guard preview.kept > 0 else { return String(localized: "None of the last \(total) versions fit.") }
        if preview.kept == total { return String(localized: "All of the last \(total) versions fit.") }
        let names = preview.versions.lazy.filter(\.kept).prefix(naming).map(\.version).joined(separator: ", ")
        return String(localized: "\(preview.kept) of the last \(total) versions fit: \(names)")
    }
}
