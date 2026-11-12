import AppKit
import SwiftUI
import YoupdatedKit

public struct SettingsView: View {
    public enum Pane: Hashable, Sendable { case general, view, notifications, privacy, advanced, about }

    @State private var pane: Pane

    public init(pane: Pane = .general) {
        _pane = State(initialValue: pane)
    }

    public var body: some View {
        TabView(selection: $pane) {
            Tab(String(localized: "General"), systemImage: "gearshape", value: .general) { GeneralSettings() }
            Tab(String(localized: "View"), systemImage: "rectangle.grid.1x2", value: .view) { ViewSettings() }
            Tab(String(localized: "Notifications"), systemImage: "bell.badge", value: .notifications) { NotificationSettings() }
            Tab(String(localized: "Privacy"), systemImage: "hand.raised", value: .privacy) { PrivacySettings() }
            Tab(String(localized: "Advanced"), systemImage: "gearshape.2", value: .advanced) { AdvancedSettings() }
            Tab(String(localized: "About"), systemImage: "info.circle", value: .about) { AboutSettings() }
        }
        .frame(width: 480)
    }
}

struct GeneralSettings: View {
    @Environment(AppModel.self) private var model
    @Environment(LoginItem.self) private var loginItem
    @State private var remembered = Keychain.exists

    var body: some View {
        Form {
            Picker(String(localized: "Check for updates"), selection: Binding(
                get: { model.settings.checkIntervalHours },
                set: { hours in Task { await model.update(SettingsPatch(checkIntervalHours: hours)) } }
            )) {
                ForEach(Settings.intervals, id: \.self) { hours in
                    Text(hours == 1 ? String(localized: "Every hour") : String(localized: "Every \(hours) hours")).tag(hours)
                }
            }
            if let next = model.nextCheck {
                LabeledContent(String(localized: "Next check"),
                               value: next <= model.currentDate ? String(localized: "Now") : next.formatted(date: .omitted, time: .shortened))
            } else {
                LabeledContent(String(localized: "Next check"), value: String(localized: "Paused"))
            }

            Toggle(String(localized: "Start at login"), isOn: Binding(get: { loginItem.enabled }, set: { loginItem.set($0) }))
            if loginItem.needsApproval {
                Button(String(localized: "Allow in Login Items…")) { loginItem.openSystemSettings() }
            }
            if let error = loginItem.error {
                Text(error).font(.caption).foregroundStyle(.red)
            }

            if model.status?.encrypted.any == true {
                Toggle(String(localized: "Remember passphrase on this computer"), isOn: Binding(
                    get: { remembered },
                    set: { on in
                        if on, let known = model.currentPassphrase { Keychain.write(known) } else if !on { Keychain.delete() }
                        remembered = Keychain.exists
                    }
                ))
            }
        }
        .formStyle(.grouped)
        .onAppear { loginItem.refresh() }
    }
}

public struct SettingsPaneView: View {
    let pane: SettingsView.Pane

    public init(_ pane: SettingsView.Pane) {
        self.pane = pane
    }

    public var body: some View {
        switch pane {
        case .general: GeneralSettings()
        case .view: ViewSettings()
        case .notifications: NotificationSettings()
        case .privacy: PrivacySettings()
        case .advanced: AdvancedSettings()
        case .about: AboutSettings()
        }
    }
}

/// Settings → View (display preferences and Show icons)
struct ViewSettings: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Picker(String(localized: "Density"), selection: $model.viewOptions.density) {
                ForEach(ViewOptions.Density.allCases, id: \.self) { Text($0.title).tag($0) }
            }
            Picker(String(localized: "Group by"), selection: $model.viewOptions.grouping) {
                ForEach(ViewOptions.Grouping.allCases, id: \.self) { Text($0.title).tag($0) }
            }
            Toggle(String(localized: "Group read updates"), isOn: $model.viewOptions.groupsReadUpdates)
                .disabled(model.viewOptions.grouping == .day)
                .help(String(localized: "Read updates organized by group instead of Earlier"))
            Picker(String(localized: "Low priority"), selection: $model.viewOptions.showsLowPriority) {
                Text(String(localized: "Show in the Feed")).tag(true)
                Text(String(localized: "Hide from the Feed")).tag(false)
            }
            Picker(String(localized: "Read updates"), selection: $model.viewOptions.readUpdates) {
                ForEach(ViewOptions.ReadUpdates.allCases, id: \.self) { Text($0.title).tag($0) }
            }
            Toggle(String(localized: "Previews"), isOn: $model.viewOptions.previews)
            Picker(String(localized: "Times"), selection: $model.viewOptions.times) {
                ForEach(ViewOptions.Times.allCases, id: \.self) { Text($0.title).tag($0) }
            }
            Section {
                Toggle(String(localized: "Show icons"), isOn: Binding(
                    get: { model.settings.showIcons },
                    set: { on in Task { await model.update(SettingsPatch(showIcons: on)) } }
                ))
                .disabled(!model.icons.isAvailable)
            } footer: {
                Text(String(localized: "Fetched through your proxy settings"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }
}

struct NotificationSettings: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Form {
            Toggle(String(localized: "Show notifications"), isOn: Binding(
                get: { model.settings.osNotifications },
                set: { on in Task { await model.update(SettingsPatch(osNotifications: on)) } }
            ))
            Toggle(String(localized: "Send a daily digest"), isOn: Binding(
                get: { model.settings.digestTime != nil },
                set: { on in Task { await model.update(SettingsPatch(digestTime: .some(on ? DigestSchedule.suggested : nil))) } }
            ))
            if let time = model.settings.digestTime {
                DatePicker(String(localized: "Digest time"), selection: Binding(
                    get: { Self.date(for: time) },
                    set: { date in
                        let parts = Calendar.current.dateComponents([.hour, .minute], from: date)
                        let time = DigestSchedule.time(hour: parts.hour ?? 18, minute: parts.minute ?? 0)
                        Task { await model.update(SettingsPatch(digestTime: .some(time))) }
                    }
                ), displayedComponents: .hourAndMinute)
            }
            Section(String(localized: "Priorities")) {
                ForEach(Priority.allCases, id: \.self) { priority in
                    LabeledContent {
                        Text(PriorityStyle.label(priority)).foregroundStyle(.secondary)
                    } label: {
                        HStack(spacing: 6) {
                            // fixed col b/c bells vary in size
                            Image(systemName: PriorityStyle.symbolName(priority)).frame(width: 24)
                            Text(Wording.priority(priority))
                        }
                    }
                }
            }
            Button(String(localized: "Notification Settings…")) {
                let id = Bundle.main.bundleIdentifier ?? ""
                if let url = URL(string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension?id=\(id)") {
                    NSWorkspace.shared.open(url)
                }
            }
        }
        .formStyle(.grouped)
    }

    static func date(for time: String) -> Date {
        let parts = DigestSchedule.components(time) ?? DateComponents(hour: 18, minute: 0)
        return Calendar.current.date(bySettingHour: parts.hour ?? 18, minute: parts.minute ?? 0, second: 0, of: Date()) ?? Date()
    }
}

/// Settings → Privacy. Editable config settings: proxy and fast mode
struct PrivacySettings: View {
    @Environment(AppModel.self) private var model
    @State private var choice: ProxyChoice = .none
    @State private var address = ""
    @State private var saving = false
    @State private var error: String?
    @State private var confirmsFastMode = false

    enum ProxyChoice: Hashable { case none, tor, torBrowser, custom }

    /// The Tor service's and Tor Browser's SOCKS ports
    static let tor = "socks5://127.0.0.1:9050"
    static let torBrowser = "socks5://127.0.0.1:9150"

    var body: some View {
        Form {
            if let privacy = model.privacy {
                proxySection(privacy)
                fastModeSection(privacy)
                configSection(privacy)
            } else if !model.canEditPrivacy {
                Text(LocalizedStringKey(String(localized: "This engine can't change privacy settings. Edit `settings.privacy` manually.")))
                    .foregroundStyle(.secondary)
            } else if let problem = model.status?.configError {
                Label {
                    Text(problem)
                } icon: {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.yellow)
                }
            } else {
                ProgressView().controlSize(.small).frame(maxWidth: .infinity)
            }
        }
        .formStyle(.grouped)
        .task {
            await model.loadPrivacy()
            sync()
        }
        .onChange(of: model.privacy) { sync() }
        .confirmationDialog(String(localized: "Turn on fast mode?"), isPresented: $confirmsFastMode) {
            Button(String(localized: "Turn On Fast Mode")) { save(fastMode: true) }
            Button(String(localized: "Cancel"), role: .cancel) {}
        } message: {
            Text(String(localized: "Requests may look automated. Sites are more likely to rate limit or block them, and they're easier to link to you, even through a proxy."))
        }
    }

    private func proxySection(_ privacy: Privacy) -> some View {
        Section {
            Picker(String(localized: "Proxy"), selection: Binding(get: { choice }, set: { choose($0) })) {
                Text(String(localized: "None")).tag(ProxyChoice.none)
                Text(String(localized: "Tor")).tag(ProxyChoice.tor)
                Text(String(localized: "Tor Browser")).tag(ProxyChoice.torBrowser)
                Text(String(localized: "Custom")).tag(ProxyChoice.custom)
            }
            .disabled(saving)
            if choice == .custom {
                LabeledContent(String(localized: "Address")) {
                    HStack {
                        TextField(String(localized: "Address"), text: $address, prompt: Text(verbatim: "socks5://127.0.0.1:1080"))
                            .labelsHidden()
                            .textFieldStyle(.roundedBorder)
                            .autocorrectionDisabled()
                            .onSubmit(apply)
                        Button(String(localized: "Use"), action: apply)
                            .disabled(saving || trimmed.isEmpty || trimmed == privacy.proxy)
                    }
                }
            }
            if saving {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text(String(localized: "Connecting to the proxy…")).foregroundStyle(.secondary)
                }
            }
            if let error {
                Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
            }
        } footer: {
            Text(String(localized: "Every request goes through this proxy. If it stops answering, checks stop."))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func fastModeSection(_ privacy: Privacy) -> some View {
        Section {
            Toggle(String(localized: "Fast mode"), isOn: Binding(
                get: { privacy.fastMode },
                set: { on in if on { confirmsFastMode = true } else { save(fastMode: false) } }
            ))
            .disabled(saving)
        } footer: {
            Text(String(localized: "Shorter gaps between requests. Faster checks, but it is easier to identify traffic."))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    /// `rotate` and `youpdated` are keywords
    static func userAgent(_ value: String) -> String {
        switch value {
        case "rotate": String(localized: "A new browser each check")
        case "youpdated": String(localized: "Youpdated identifies itself")
        default: value
        }
    }

    private func configSection(_ privacy: Privacy) -> some View {
        Section {
            LabeledContent(String(localized: "User agent"), value: Self.userAgent(privacy.userAgent))
            LabeledContent(String(localized: "Gap between requests to a site"), value: Self.seconds(privacy.jitter))
            LabeledContent(String(localized: "Requests at once"), value: privacy.concurrency.formatted())
            LabeledContent(String(localized: "Timeout"), value: Self.seconds([privacy.timeout]))
            if let path = model.status?.configPath {
                Button(String(localized: "Show Config in Finder")) {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(filePath: path)])
                }
            }
        } header: {
            Text(String(localized: "Other Config settings"))
        } footer: {
            // markdown, for the code spans
            Text(LocalizedStringKey(model.status?.encrypted.config == true
                 ? String(localized: "Change these manually with `youpdated edit`.")
                 : String(localized: "Change these manually under `settings: privacy:`.")))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var trimmed: String { address.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// "0.5-3 s", "20 s"
    static func seconds(_ values: [Double]) -> String {
        let style = FloatingPointFormatStyle<Double>.number.precision(.fractionLength(0...1))
        let parts = (values.first == values.last ? Array(values.prefix(1)) : values).map { $0.formatted(style) }
        return String(localized: "\(parts.joined(separator: "-")) s")
    }

    private func sync() {
        guard let privacy = model.privacy, !saving else { return }
        switch privacy.proxy {
        case nil: choice = .none
        case Self.tor: choice = .tor
        case Self.torBrowser: choice = .torBrowser
        case let proxy?:
            choice = .custom
            address = proxy
        }
    }

    private func choose(_ new: ProxyChoice) {
        error = nil
        choice = new
        switch new {
        case .none: save(proxy: .some(nil))
        case .tor: save(proxy: Self.tor)
        case .torBrowser: save(proxy: Self.torBrowser)
        case .custom:
            if address.isEmpty || address == Self.tor || address == Self.torBrowser { address = "" }
        }
    }

    private func apply() {
        guard !trimmed.isEmpty, trimmed != model.privacy?.proxy else { return }
        save(proxy: trimmed)
    }

    private func save(proxy: String?? = nil, fastMode: Bool? = nil) {
        error = nil
        saving = true
        Task {
            let problem = await model.setPrivacy(proxy: proxy, fastMode: fastMode)
            saving = false
            error = problem
            // a custom address that failed stays
            if problem == nil || choice != .custom { sync() }
        }
    }
}

/// Settings → Advanced
struct AdvancedSettings: View {
    @Environment(AppModel.self) private var model
    @State private var logging = AppLog.shared.enabled
    @State private var logExists = AppLog.shared.exists

    var body: some View {
        @Bindable var model = model
        Form {
            Section {
                Toggle(String(localized: "Hide suggestions to watch Youpdated"), isOn: $model.hidesSelfWatchSuggestions)
            } footer: {
                Text(String(localized: "No prompts to add Youpdated to your sources."))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section {
                Toggle(String(localized: "Save diagnostic logs"), isOn: Binding(
                    get: { logging },
                    set: { on in
                        AppLog.shared.enabled = on
                        logging = on
                        if on {
                            let os = ProcessInfo.processInfo.operatingSystemVersionString
                            AppLog.shared.write("Diagnostic logs enabled, Youpdated \(model.appVersion), engine \(model.engineVersion ?? "not running"), macOS \(os)")
                        }
                        logExists = AppLog.shared.exists
                    }
                ))
                Button(String(localized: "Show Log in Finder")) {
                    NSWorkspace.shared.activateFileViewerSelecting([AppLog.shared.file])
                }
                .disabled(!logExists)
            } footer: {
                Text(String(localized: "Saves errors to ~/Library/Logs/Youpdated/app.log for bug reports."))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .onAppear { logExists = AppLog.shared.exists }
    }
}

struct AboutSettings: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Form {
            HStack(spacing: 14) {
                Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 56, height: 56)
                VStack(alignment: .leading) {
                    Text(String(localized: "Youpdated")).font(.title2).bold()
                    Text(String(localized: "What changed in the things you follow")).foregroundStyle(.secondary)
                }
            }
            LabeledContent(String(localized: "App version"), value: model.appVersion)
            LabeledContent(String(localized: "Engine version"), value: model.engineVersion ?? String(localized: "Not running"))
            Section(String(localized: "Updates")) {
                if let update = model.appUpdate {
                    LabeledContent(Wording.appUpdate(update.version)) { DownloadButton(update: update) }
                } else if model.sources.selfTarget != nil {
                    Label(String(localized: "Youpdated is up to date"), systemImage: "checkmark.circle")
                } else if model.hidesSelfWatchSuggestions {
                    Text(String(localized: "Not watching for new versions"))
                } else {
                    LabeledContent(String(localized: "Not watching for new versions")) {
                        Button(String(localized: "Watch")) { Task { await model.watchSelf() } }
                    }
                }
            }
            Section {
                Link(String(localized: "Youpdated on GitHub"), destination: URL(string: "https://github.com/Void1-1/youpdated")!)
                if let path = model.status?.configPath {
                    LabeledContent(String(localized: "Config")) {
                        Button((path as NSString).abbreviatingWithTildeInPath) {
                            NSWorkspace.shared.activateFileViewerSelecting([URL(filePath: path)])
                        }
                        .buttonStyle(.link)
                    }
                }
            }
        }
        .formStyle(.grouped)
    }
}
