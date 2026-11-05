import AppIntents
import Foundation
import YoupdatedKit

// Shortcuts actions. They run in the app's process, which macOS launches in the background
// when it isn't running; Xcode's build extracts their metadata, so they only show up in
// Shortcuts from the Xcode build.

enum IntentError: Error, CustomLocalizedStringResourceConvertible {
    case notReady
    case locked
    case message(String)

    var localizedStringResource: LocalizedStringResource {
        switch self {
        case .notReady: "Youpdated isn't running its checks yet. Open and try again."
        case .locked: "Youpdated is locked. Open and enter the passphrase."
        case .message(let text): "\(text)"
        }
    }
}

@MainActor
enum IntentSupport {
    /// The model once the engine is up and unlocked
    static func model() async throws -> AppModel {
        for _ in 0..<200 {
            if let model = AppDelegate.shared?.model {
                if model.needsPassphrase { throw IntentError.locked }
                if case .failed(let message) = model.engineState { throw IntentError.message(message) }
                if model.engineState == .ready, model.inbox.loaded { return model }
            }
            try await Task.sleep(for: .milliseconds(100))
        }
        throw IntentError.notReady
    }
}

struct CheckForUpdatesIntent: AppIntent {
    static let title: LocalizedStringResource = "Check for Updates"
    static let description = IntentDescription("Checks everything Youpdated watches.")

    @MainActor
    func perform() async throws -> some IntentResult & ReturnsValue<Int> & ProvidesDialog {
        let model = try await IntentSupport.model()
        let before = model.unread
        await model.checkNow()
        let found = max(0, model.unread - before)
        return .result(value: found, dialog: "\(Wording.newUpdates(found))")
    }
}

struct AddSourceIntent: AppIntent {
    static let title: LocalizedStringResource = "Add Source"
    static let description = IntentDescription("Starts watching a link or name.")

    @Parameter(title: "Link or Name")
    var value: String

    @Parameter(title: "Source", description: "Needed when the name could be more than one source.")
    var source: String?

    static var parameterSummary: some ParameterSummary {
        Summary("Watch \(\.$value)") { \.$source }
    }

    @MainActor
    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let model = try await IntentSupport.model()
        guard let engine = model.engine else { throw IntentError.notReady }
        let preview: PreviewReply
        do {
            preview = try await engine.preview(value: value, source: source)
        } catch let error as EngineError where error.code == .ambiguous {
            let choices = (error.choices ?? []).formatted(.list(type: .or))
            throw IntentError.message(String(localized: "“\(value)” could be \(choices). Set Source to one of them."))
        } catch {
            throw IntentError.message(error.localizedDescription)
        }
        if preview.alreadyWatching {
            return .result(value: preview.display, dialog: "Already watching \(preview.display).")
        }
        do {
            let added = try await engine.add(source: preview.source, entries: [preview.entry])
            await model.sources.reload()
            await model.checkNow(targets: added.targets.map(\.targetKey))
        } catch {
            throw IntentError.message(error.localizedDescription)
        }
        return .result(value: preview.display, dialog: "Watching \(preview.display).")
    }
}

struct UpdateEntity: AppEntity {
    static let typeDisplayRepresentation: TypeDisplayRepresentation = "Update"
    static let defaultQuery = UpdateQuery()

    let id: String
    @Property(title: "Name") var name: String
    @Property(title: "Title") var title: String
    @Property(title: "Source") var source: String
    @Property(title: "Link") var url: URL?

    init(_ item: InboxItem) {
        id = item.id
        name = item.display
        title = item.title
        source = item.label
        url = URL(string: item.url)
    }

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "\(name)", subtitle: "\(title)")
    }
}

struct UpdateQuery: EntityQuery {
    @MainActor
    func entities(for identifiers: [String]) async throws -> [UpdateEntity] {
        let model = try await IntentSupport.model()
        let wanted = Set(identifiers)
        return model.inbox.items.filter { wanted.contains($0.id) }.map(UpdateEntity.init)
    }

    @MainActor
    func suggestedEntities() async throws -> [UpdateEntity] {
        let model = try await IntentSupport.model()
        return model.inbox.items.filter { !$0.read }.prefix(20).map(UpdateEntity.init)
    }
}

struct GetUnreadUpdatesIntent: AppIntent {
    static let title: LocalizedStringResource = "Get Unread Updates"
    static let description = IntentDescription("Unopened updates, newest first.")

    @MainActor
    func perform() async throws -> some IntentResult & ReturnsValue<[UpdateEntity]> {
        let model = try await IntentSupport.model()
        let unread = model.inbox.items.filter { !$0.read }.sorted { $0.date > $1.date }
        return .result(value: unread.map(UpdateEntity.init))
    }
}

struct MarkAllReadIntent: AppIntent {
    static let title: LocalizedStringResource = "Mark All Updates as Read"
    static let description = IntentDescription("Mark everything in the inbox as read.")

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let model = try await IntentSupport.model()
        model.inbox.sourceFilter = nil
        await model.inbox.markAllRead()
        return .result(dialog: "Marked everything as read.")
    }
}

struct YoupdatedShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: CheckForUpdatesIntent(),
            phrases: ["Check for updates with \(.applicationName)", "\(.applicationName) check now"],
            shortTitle: "Check for Updates", systemImageName: "arrow.clockwise")
        AppShortcut(
            intent: GetUnreadUpdatesIntent(),
            phrases: ["What's new in \(.applicationName)", "Get unread updates from \(.applicationName)"],
            shortTitle: "Unread Updates", systemImageName: "tray")
        AppShortcut(
            intent: MarkAllReadIntent(),
            phrases: ["Mark all \(.applicationName) updates as read"],
            shortTitle: "Mark All as Read", systemImageName: "envelope.open")
    }
}
