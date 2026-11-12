import AppKit
import SwiftUI
import UniformTypeIdentifiers
import YoupdatedKit

/// The window feed of changes, followed items
public struct AppWindowView: View {
    @Environment(AppModel.self) private var model
    @Environment(WindowState.self) private var window
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var filterFocused: Bool
    @State private var confirmsUserAgent = false

    public init() {}

    public var body: some View {
        @Bindable var window = window
        @Bindable var feed = model.feed
        content
            .frame(minWidth: 420, minHeight: 480)
            .safeAreaInset(edge: .top, spacing: 0) { bars }
            .toolbar { toolbar }
            .toolbar(removing: .title)
            .searchable(text: $feed.filter, placement: .toolbar, prompt: String(localized: "Filter"))
            .searchFocused($filterFocused)
            .onChange(of: window.mode) { model.feed.filter = "" }
            .onChange(of: window.focusesFilter) { _, focuses in
                guard focuses else { return }
                filterFocused = true
                window.focusesFilter = false
            }
            .sheet(isPresented: importing) { ImportSheet() }
            .sheet(item: $window.removing) { RemoveSheet(target: $0) }
            .fileImporter(isPresented: $window.choosesImport, allowedContentTypes: [.json, .plainText, .data]) { result in
                if case .success(let url) = result { importFile(url) }
            }
            .background { TypeToFilter(enabled: !locked) }
            .dropDestination(for: URL.self) { urls, _ in dropped(urls) }
            .onAppear { model.feed.settle() }
            .animation(Motion.fade, value: model.isChecking)
    }

    @ViewBuilder private var content: some View {
        if model.needsPassphrase || model.status?.locked == true {
            LockedView()
        } else if case .failed(let message) = model.engineState {
            ContentUnavailableView {
                Label(String(localized: "Youpdated can't check"), systemImage: "xmark.octagon")
            } description: {
                Text(message)
            }
        } else if model.engineState == .starting || !model.inbox.loaded {
            ProgressView()
                .controlSize(.small)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            switch window.mode {
            case .updates:
                if model.isEmptySetup && model.inbox.items.allSatisfy(\.starred) {
                    FirstLaunchView()
                } else {
                    FeedView()
                }
            case .following:
                FollowingView()
            }
        }
    }

    @ViewBuilder private var bars: some View {
        VStack(spacing: 0) {
            if model.isChecking {
                ProgressLine(fraction: progress)
                    .transition(.opacity)
            }
            if model.needsUserAgentConfirmation {
                SlimBar(symbol: "hand.raised", text: String(localized: "Checks paused: confirm Youpdated may name itself in requests.")) {
                    Link(String(localized: "Learn More"), destination: URL(string: "https://github.com/Void1-1/youpdated/blob/main/docs/privacy.md#user-agent")!)
                    Button(String(localized: "Confirm…")) { confirmsUserAgent = true }
                }
                .userAgentConfirmation(isPresented: $confirmsUserAgent) {
                    Task { await model.confirmUserAgent() }
                }
            }
            if model.showsCLINotice {
                SlimBar(symbol: "terminal", text: String(localized: "Checks are also running from the command line. Updates they find won't appear here.")) {
                    Link(String(localized: "Learn More"), destination: URL(string: "https://github.com/Void1-1/youpdated/blob/main/docs/usage.md")!)
                    Button(String(localized: "OK")) { Task { await model.dismissCLINotice() } }
                }
            }
        }
    }

    private var locked: Bool { model.needsPassphrase || model.status?.locked == true }

    private var progress: Double {
        guard let progress = model.progress, progress.of > 0 else { return 0 }
        return Double(progress.done) / Double(progress.of)
    }

    @ToolbarContentBuilder private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .navigation) {
            @Bindable var window = window
            Picker(String(localized: "View"), selection: $window.mode) {
                Text(String(localized: "Updates")).tag(WindowState.Mode.updates)
                Text(String(localized: "Following")).tag(WindowState.Mode.following)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .disabled(locked)
        }
        ToolbarItemGroup(placement: .primaryAction) {
            if model.hasProblems {
                ProblemsButton()
            }
            Button {
                Task { await model.checkNow() }
            } label: {
                Label(String(localized: "Check Now"), systemImage: "arrow.clockwise")
                    .symbolEffect(.rotate, options: .repeat(.continuous), isActive: model.isChecking && !reduceMotion)
            }
            .help(String(localized: "Check Now"))
            .disabled(model.engine == nil || model.needsPassphrase || model.isChecking)
            AddButton().disabled(locked)
            MoreMenu().disabled(locked)
        }
    }

    private var importing: Binding<Bool> {
        Binding {
            window.importFile != nil && model.sources.importing != .idle
        } set: { shown in
            if !shown {
                window.importFile = nil
                model.sources.resetImport()
            }
        }
    }

    /// A link is added; a package.json, requirements.txt, or Brewfile is imported
    // todo: pyproject.toml
    private func dropped(_ urls: [URL]) -> Bool {
        guard let url = urls.first else { return false }
        if url.isFileURL {
            importFile(url)
        } else {
            window.add(url.absoluteString, model: model)
        }
        return true
    }

    private func importFile(_ url: URL) {
        window.importFile = url
        Task { await model.sources.previewImport(ImportInputs.input(for: url)) }
    }
}

/// Typing in the window, outside a text field, goes to the filter
private struct TypeToFilter: NSViewRepresentable {
    let enabled: Bool

    func makeNSView(context: Context) -> Monitor { Monitor() }

    func updateNSView(_ view: Monitor, context: Context) {
        view.enabled = enabled
        view.fitSearch()
    }

    final class Monitor: NSView {
        var enabled = true
        private var monitor: Any?

        private var search: NSSearchToolbarItem? {
            window?.toolbar?.items.lazy.compactMap { $0 as? NSSearchToolbarItem }.first
        }

        func fitSearch() {
            guard let search else { return }
            if search.preferredWidthForSearchField != 170 { search.preferredWidthForSearchField = 170 }
        }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let monitor { NSEvent.removeMonitor(monitor) }
            monitor = nil
            guard window != nil else { return }
            // build the toolbar after the view joins the window
            DispatchQueue.main.async { [weak self] in self?.fitSearch() }
            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                MainActor.assumeIsolated { self?.redirect(event) }
                return event
            }
        }

        private func redirect(_ event: NSEvent) {
            guard enabled, let window, event.window === window,
                  event.modifierFlags.isDisjoint(with: [.command, .control, .option, .function]),
                  !(window.firstResponder is NSText),
                  let typed = event.characters, typed.count == 1, let scalar = typed.unicodeScalars.first,
                  !CharacterSet.whitespacesAndNewlines.union(.controlCharacters).contains(scalar),
                  let search
            else { return }
            fitSearch()
            search.beginSearchInteraction()
        }
    }
}

struct ProblemsButton: View {
    @Environment(AppModel.self) private var model
    @Environment(WindowState.self) private var window

    var body: some View {
        @Bindable var window = window
        Button {
            window.showsProblems.toggle()
        } label: {
            Label {
                Text(model.problemCount, format: .number)
            } icon: {
                Image(systemName: "exclamationmark.triangle.fill")
            }
            .labelStyle(.titleAndIcon)
            .foregroundStyle(.orange)
            .accessibilityLabel(String(localized: "\(model.problemCount) problems"))
        }
        .help(String(localized: "Problems"))
        .popover(isPresented: $window.showsProblems, arrowEdge: .bottom) { ProblemsPopover() }
    }
}

struct AddButton: View {
    @Environment(AppModel.self) private var model
    @Environment(WindowState.self) private var window

    var body: some View {
        @Bindable var window = window
        Button {
            window.showsAdd.toggle()
        } label: {
            Label(String(localized: "Add"), systemImage: "plus")
        }
        .help(String(localized: "Follow something new"))
        .popover(isPresented: $window.showsAdd, arrowEdge: .bottom) {
            AddPanel()
                .onDisappear { model.add.reset() }
        }
    }
}

/// Sort, Filter by source, Mark all read, Import a file
struct MoreMenu: View {
    @Environment(AppModel.self) private var model
    @Environment(WindowState.self) private var window

    var body: some View {
        @Bindable var inbox = model.inbox
        @Bindable var model = model
        Menu {
            Picker(String(localized: "Group By"), selection: $model.viewOptions.grouping) {
                ForEach(ViewOptions.Grouping.allCases, id: \.self) { Text($0.title).tag($0) }
            }
            Toggle(String(localized: "Group Read Updates"), isOn: $model.viewOptions.groupsReadUpdates)
                .disabled(model.viewOptions.grouping == .day)
            Picker(String(localized: "Density"), selection: $model.viewOptions.density) {
                ForEach(ViewOptions.Density.allCases, id: \.self) { Text($0.title).tag($0) }
            }
            Picker(String(localized: "Filter by Source"), selection: $inbox.sourceFilter) {
                Text(String(localized: "All Sources")).tag(String?.none)
                Divider()
                ForEach(inbox.sources, id: \.self) { label in
                    Text(label).tag(String?.some(label))
                }
            }
            Divider()
            Button(String(localized: "Mark All as Read")) { Task { await model.feed.markAllRead() } }
                .disabled(model.unread == 0)
            Button(String(localized: "Import a File…")) { window.choosesImport = true }
            Divider()
            SettingsLink { Text(String(localized: "Settings…")) }
        } label: {
            Label(String(localized: "More"), systemImage: "ellipsis")
        }
        .menuIndicator(.hidden)
        .help(String(localized: "More"))
    }
}

public enum ImportInputs {
    public static func input(for url: URL) -> ImportInput {
        if ImportInput.format(forFileName: url.lastPathComponent) != nil {
            return .path(url.path)
        }
        let text = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
        return .content(text, format: url.pathExtension.lowercased() == "json" ? "package.json" : "requirements.txt")
    }
}

public struct WindowCommands: Commands {
    let model: AppModel
    let window: WindowState
    let show: () -> Void

    public init(model: AppModel, window: WindowState, show: @escaping () -> Void) {
        self.model = model
        self.window = window
        self.show = show
    }

    public var body: some Commands {
        CommandGroup(after: .newItem) {
            Button(String(localized: "Follow something new…")) { show(); window.add(model: model) }
                .keyboardShortcut("n")
            Button(String(localized: "Import a file…")) { show(); window.choosesImport = true }
        }
        CommandGroup(after: .toolbar) {
            Button(String(localized: "Updates")) { show(); window.mode = .updates }
                .keyboardShortcut("1")
            Button(String(localized: "Following")) { show(); window.mode = .following }
                .keyboardShortcut("2")
            Button(String(localized: "Filter")) { show(); window.focusFilter() }
                .keyboardShortcut("f")
            Divider()
        }
        CommandMenu(String(localized: "Update")) {
            Button(String(localized: "Open link")) {
                if let card = model.feed.selectedCard { Task { await model.feed.open(card) } }
            }
            .keyboardShortcut(.return, modifiers: .command)
            Button(String(localized: "Mark as Read or Unread")) {
                if let card = model.feed.selectedCard { Task { await model.feed.setRead(!card.isUnread, card) } }
            }
            .keyboardShortcut("u")
            Button(String(localized: "Mark all as Read")) { Task { await model.feed.markAllRead() } }
                .keyboardShortcut("u", modifiers: [.command, .option])
            Divider()
            Button(String(localized: "Check now")) { Task { await model.checkNow() } }
                .keyboardShortcut("r")
        }
    }
}
