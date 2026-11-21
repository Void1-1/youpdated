import SwiftUI
import YoupdatedKit

/// config's `notify:` webhooks and command
struct NotifySections: View {
    @Environment(AppModel.self) private var model
    @State private var editingWebhook: WebhookDraft?
    @State private var editingCommand: CommandDraft?
    @State private var error: String?

    var body: some View {
        Group {
            if let notify = model.notify {
                webhooks(notify)
                command(notify)
            } else if !model.canEditNotify {
                Section {
                    Text(LocalizedStringKey(String(localized: "This engine can't change webhooks or commands. Edit `settings.notify` manually.")))
                        .foregroundStyle(.secondary)
                }
            } else {
                Section {
                    ProgressView().controlSize(.small).frame(maxWidth: .infinity)
                }
            }
        }
    }

    private func webhooks(_ notify: Notify) -> some View {
        Section {
            ForEach(Array(notify.webhooks.enumerated()), id: \.offset) { index, hook in
                LabeledContent {
                    HStack {
                        Button(String(localized: "Edit…")) { editingWebhook = WebhookDraft(index: index, webhook: hook) }
                        Button(role: .destructive) {
                            Task { await remove(at: index) }
                        } label: {
                            Image(systemName: "minus.circle")
                        }
                        .buttonStyle(.borderless)
                        .help(String(localized: "Remove Webhook"))
                    }
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(NotifyText.host(hook.url))
                        Text(NotifyText.summary(hook)).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            Button(String(localized: "Add Webhook…")) { editingWebhook = WebhookDraft(index: nil, webhook: Webhook(url: "")) }
            if let error {
                ErrorLine(message: error)
            }
        } header: {
            Text(String(localized: "Webhooks"))
        } footer: {
            Text(String(localized: "Each check posts new updates to these addresses."))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .sheet(item: $editingWebhook) { draft in
            WebhookEditor(draft: draft) { saved in
                var list = model.notify?.webhooks ?? []
                if let index = draft.index, list.indices.contains(index) {
                    list[index] = saved
                } else {
                    list.append(saved)
                }
                return await model.saveWebhooks(list)
            }
        }
    }

    private func command(_ notify: Notify) -> some View {
        Section {
            if let command = notify.command {
                LabeledContent {
                    HStack {
                        Button(String(localized: "Edit…")) { editingCommand = CommandDraft(command: command, isNew: false) }
                        Button(role: .destructive) {
                            Task { error = await model.saveCommand(nil) }
                        } label: {
                            Image(systemName: "minus.circle")
                        }
                        .buttonStyle(.borderless)
                        .help(String(localized: "Remove Command"))
                    }
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(command.run).font(.body.monospaced()).lineLimit(1).truncationMode(.middle)
                        Text(NotifyText.rules(priority: command.priority, digest: command.digest))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            } else {
                Button(String(localized: "Set Command…")) { editingCommand = CommandDraft(command: NotifyCommand(run: ""), isNew: true) }
            }
        } header: {
            Text(String(localized: "Command"))
        } footer: {
            Text(String(localized: "Runs once for each new update."))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .sheet(item: $editingCommand) { draft in
            CommandEditor(draft: draft) { await model.saveCommand($0) }
        }
    }

    private func remove(at index: Int) async {
        guard var list = model.notify?.webhooks, list.indices.contains(index) else { return }
        list.remove(at: index)
        error = await model.saveWebhooks(list)
    }
}

/// A webhook being added (`index` nil) or edited
struct WebhookDraft: Identifiable {
    let id = UUID()
    var index: Int?
    var webhook: Webhook
}

struct CommandDraft: Identifiable {
    let id = UUID()
    var command: NotifyCommand
    var isNew: Bool
}

/// Wording for webhooks and commands
enum NotifyText {
    static func format(_ format: String?) -> String {
        switch format.flatMap(WebhookFormat.init(rawValue:)) {
        case .json: String(localized: "JSON")
        case .discord: String(localized: "Discord")
        case .slack: String(localized: "Slack")
        case .ntfy: String(localized: "ntfy")
        case nil: format ?? String(localized: "Automatic")
        }
    }

    /// The lowest priority an endpoint takes
    static func priority(_ priority: Priority) -> String {
        switch priority {
        case .high: String(localized: "High priority only")
        case .normal: String(localized: "Normal and high priority")
        case .low: String(localized: "Every update")
        }
    }

    static func rules(priority: Priority, digest: Bool) -> String {
        let sends = Self.priority(priority)
        return digest ? sends : String(localized: "\(sends), ignores the digest")
    }

    /// A saved webhook's format, with what Automatic resolved to
    static func format(of hook: Webhook) -> String {
        guard hook.format == nil, let sendsAs = hook.sendsAs else { return format(hook.format) }
        return String(localized: "Automatic (\(format(sendsAs)))")
    }

    static func summary(_ hook: Webhook) -> String {
        "\(format(of: hook)) · \(rules(priority: hook.priority, digest: hook.digest))"
    }

    /// Just the host: the rest of a webhook's URL is often its secret
    static func host(_ url: String) -> String {
        URLComponents(string: url)?.host ?? url
    }
}

/// Endpoint priority and digest, shared by both editors
private struct EndpointRules: View {
    @Binding var priority: Priority
    @Binding var digest: Bool

    var body: some View {
        Picker(String(localized: "Send"), selection: $priority) {
            ForEach([Priority.high, .normal, .low], id: \.self) { Text(NotifyText.priority($0)).tag($0) }
        }
        Toggle(String(localized: "Wait for the daily digest"), isOn: $digest)
            .help(String(localized: "When a digest is set, normal priority updates wait. High priority updates are sent immediately."))
    }
}

/// Send Test's progress; a failure goes to the editor's one `ErrorLine`
private enum TestState: Equatable {
    case idle, sending, sent
}

private struct TestResult: View {
    let state: TestState

    var body: some View {
        switch state {
        case .idle:
            EmptyView()
        case .sending:
            ProgressView().controlSize(.small)
        case .sent:
            Label(String(localized: "Sent"), systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
        }
    }
}

/// A save or test failure
struct ErrorLine: View {
    let message: String

    var body: some View {
        Label {
            Text((try? AttributedString(
                markdown: message, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
                ?? AttributedString(message))
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        } icon: {
            Image(systemName: "exclamationmark.triangle.fill")
        }
        .font(.callout)
        .foregroundStyle(.red)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct WebhookEditor: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var hook: Webhook
    @State private var headers: [HeaderRow]
    @State private var saving = false
    @State private var error: String?
    @State private var test: TestState = .idle
    private let isNew: Bool
    private let save: (Webhook) async -> String?

    init(draft: WebhookDraft, save: @escaping (Webhook) async -> String?) {
        _hook = State(initialValue: draft.webhook)
        _headers = State(initialValue: draft.webhook.headers.map { HeaderRow(name: $0.name, value: $0.value) })
        isNew = draft.index == nil
        self.save = save
    }

    private var trimmed: Webhook {
        var hook = hook
        hook.url = hook.url.trimmingCharacters(in: .whitespacesAndNewlines)
        hook.headers = headers
            .map { Header(name: $0.name.trimmingCharacters(in: .whitespaces), value: $0.value) }
            .filter { !$0.name.isEmpty }
        return hook
    }

    private var urlIsValid: Bool { Webhook.isValidURL(trimmed.url) }

    /// Explains a bad URL
    private var urlHint: String? {
        let url = trimmed.url.lowercased()
        if url.isEmpty || urlIsValid || "https://".hasPrefix(url) || "http://".hasPrefix(url) { return nil }
        return String(localized: "Enter the full address, with https://")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(isNew ? String(localized: "Add Webhook") : String(localized: "Edit Webhook")).font(.headline)
            Form {
                TextField(String(localized: "URL"), text: $hook.url, prompt: Text(verbatim: "https://ntfy.sh/my-topic"))
                    .autocorrectionDisabled()
                Picker(String(localized: "Format"), selection: $hook.format) {
                    Text(String(localized: "Automatic")).tag(String?.none)
                    ForEach(WebhookFormat.allCases, id: \.self) { Text(NotifyText.format($0.rawValue)).tag(Optional($0.rawValue)) }
                }
                EndpointRules(priority: $hook.priority, digest: $hook.digest)
                Section(String(localized: "Headers")) {
                    ForEach($headers) { $row in
                        HStack {
                            TextField(String(localized: "Name"), text: $row.name, prompt: Text(verbatim: "Authorization"))
                                .labelsHidden()
                                .autocorrectionDisabled()
                            TextField(String(localized: "Value"), text: $row.value)
                                .labelsHidden()
                                .autocorrectionDisabled()
                            Button {
                                headers.removeAll { $0.id == row.id }
                            } label: {
                                Image(systemName: "minus.circle")
                            }
                            .buttonStyle(.borderless)
                            .help(String(localized: "Remove Header"))
                        }
                    }
                    Button(String(localized: "Add Header")) { headers.append(HeaderRow()) }
                }
            }
            .formStyle(.grouped)
            .disabled(saving)
            Text(String(localized: "A URL or a header token is saved in the config."))
                .font(.caption)
                .foregroundStyle(.secondary)
            if let message = urlHint ?? error {
                ErrorLine(message: message)
            }
            HStack {
                Button(String(localized: "Send Test")) {
                    test = .sending
                    error = nil
                    Task {
                        error = await model.testWebhook(trimmed)
                        test = error == nil ? .sent : .idle
                    }
                }
                .disabled(!urlIsValid || test == .sending)
                TestResult(state: test)
                Spacer()
                Button(String(localized: "Cancel"), role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(String(localized: "Save")) {
                    saving = true
                    Task {
                        error = await save(trimmed)
                        saving = false
                        if error == nil { dismiss() }
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(!urlIsValid || saving)
            }
        }
        .padding(20)
        .frame(width: 480)
        // an edit makes the last test or save outcome stale
        .onChange(of: hook) { test = .idle; error = nil }
        .onChange(of: headers) { test = .idle; error = nil }
    }
}

private struct HeaderRow: Identifiable, Equatable {
    let id = UUID()
    var name = ""
    var value = ""
}

struct CommandEditor: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var command: NotifyCommand
    @State private var saving = false
    @State private var error: String?
    @State private var test: TestState = .idle
    private let isNew: Bool
    private let save: (NotifyCommand) async -> String?

    init(draft: CommandDraft, save: @escaping (NotifyCommand) async -> String?) {
        _command = State(initialValue: draft.command)
        isNew = draft.isNew
        self.save = save
    }

    private var trimmed: NotifyCommand {
        var command = command
        command.run = command.run.trimmingCharacters(in: .whitespacesAndNewlines)
        return command
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(isNew ? String(localized: "Set Command") : String(localized: "Edit Command")).font(.headline)
            Form {
                TextField(String(localized: "Command"), text: $command.run,
                          prompt: Text(verbatim: "say \"{label}: {title}\""))
                    .font(.body.monospaced())
                    .autocorrectionDisabled()
                EndpointRules(priority: $command.priority, digest: $command.digest)
            }
            .formStyle(.grouped)
            .disabled(saving)
            // markdown, for the code spans
            Text(LocalizedStringKey(String(localized: "`{title}`, `{label}`, `{url}`, `{version}`, `{source}`, `{target}`, `{published}`, `{tags}`, and `{body}` are filled in.")))
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let error {
                ErrorLine(message: error)
            }
            HStack {
                Button(String(localized: "Send Test")) {
                    test = .sending
                    error = nil
                    Task {
                        error = await model.testCommand(trimmed)
                        test = error == nil ? .sent : .idle
                    }
                }
                .disabled(trimmed.run.isEmpty || test == .sending)
                TestResult(state: test)
                Spacer()
                Button(String(localized: "Cancel"), role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(String(localized: "Save")) {
                    saving = true
                    Task {
                        error = await save(trimmed)
                        saving = false
                        if error == nil { dismiss() }
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(trimmed.run.isEmpty || saving)
            }
        }
        .padding(20)
        .frame(width: 480)
        .onChange(of: command) { test = .idle; error = nil }
    }
}
