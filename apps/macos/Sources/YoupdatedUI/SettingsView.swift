import AppKit
import SwiftUI
import YoupdatedKit

public struct SettingsView: View {
    public enum Pane: Hashable, Sendable { case general, view, notifications, advanced, about }

    @State private var pane: Pane

    public init(pane: Pane = .general) {
        _pane = State(initialValue: pane)
    }

    public var body: some View {
        TabView(selection: $pane) {
            Tab(String(localized: "General"), systemImage: "gearshape", value: .general) { GeneralSettings() }
            Tab(String(localized: "View"), systemImage: "rectangle.grid.1x2", value: .view) { ViewSettings() }
            Tab(String(localized: "Notifications"), systemImage: "bell.badge", value: .notifications) { NotificationSettings() }
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
