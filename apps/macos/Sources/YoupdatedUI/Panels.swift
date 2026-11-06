import AppKit
import SwiftUI
import YoupdatedKit

/// source errors, failed webhooks/commands, engine crashes
struct ProblemsPopover: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(String(localized: "Problems"))
                .font(.headline)
                .padding([.horizontal, .top], 16)
                .padding(.bottom, 8)
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if case .failed(let message) = model.engineState {
                        ProblemRow(symbol: "xmark.octagon.fill", tint: .red, title: String(localized: "Youpdated can't check"), message: message)
                    }
                    if let error = model.status?.configError {
                        ProblemRow(symbol: "doc.badge.gearshape", tint: .red, title: String(localized: "The config can't be read"), message: error)
                    }
                    ForEach(model.problems, id: \.self) { problem in
                        ProblemRow(
                            symbol: problem.kind == .notify ? "paperplane" : SourceStyle.symbolName(problem.source),
                            tint: .orange,
                            title: problem.target == "-"
                                ? SourceLabel.label(source: problem.source, key: problem.key) : problem.target,
                            message: problem.message,
                            retry: model.target(for: problem) == nil ? nil : { Task { await model.retry(problem) } })
                    }
                    ForEach(model.appProblems) { problem in
                        ProblemRow(symbol: "macbook", tint: .secondary, title: problem.title, message: problem.detail)
                    }
                }
                .padding(.horizontal, 16)
            }
            .frame(maxHeight: 360)
            Divider().padding(.top, 8)
            HStack {
                if !model.appProblems.isEmpty {
                    Button(String(localized: "Clear")) { model.clearAppProblems() }
                }
                Spacer()
                Button(String(localized: "Copy Details")) {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(model.problemReport, forType: .string)
                }
            }
            .padding(12)
        }
        .frame(width: 380)
    }
}

struct ProblemRow: View {
    let symbol: String
    let tint: Color
    let title: String
    let message: String
    var retry: (() -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol).foregroundStyle(tint).frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(message)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            if let retry {
                Button(String(localized: "Retry"), action: retry)
                    .controlSize(.small)
            }
        }
    }
}

/// What a dropped or chosen file adds and skips, then Import
struct ImportSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(WindowState.self) private var window
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let flow = model.sources.importing
        VStack(alignment: .leading, spacing: 14) {
            Text(String(localized: "Import \(window.importFile?.lastPathComponent ?? "")"))
                .font(.headline)
            switch flow {
            case .idle, .previewing:
                HStack { ProgressView().controlSize(.small); Text(String(localized: "Reading the file…")) }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .ready(_, let preview), .applying(_, let preview):
                ImportList(preview: preview)
            case .done(let summary):
                Label(String(localized: "Following \(summary.added) more"), systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let message):
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            HStack {
                Spacer()
                if case .done = flow {
                    Button(String(localized: "Done")) { close() }.keyboardShortcut(.defaultAction)
                } else {
                    Button(String(localized: "Cancel"), role: .cancel) { close() }.keyboardShortcut(.cancelAction)
                    if case .ready(_, let preview) = flow {
                        Button(String(localized: "Import \(preview.count)")) {
                            Task { await model.sources.confirmImport() }
                        }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut(.defaultAction)
                        .disabled(preview.count == 0)
                    }
                }
            }
        }
        .padding(20)
        .frame(width: 460, height: 420)
    }

    private func close() {
        window.importFile = nil
        model.sources.resetImport()
        dismiss()
    }
}

struct ImportList: View {
    let preview: ImportPreviewReply

    var body: some View {
        List {
            if !preview.add.isEmpty {
                Section(String(localized: "Will Follow")) {
                    ForEach(preview.add, id: \.source) { group in
                        ForEach(group.entries, id: \.self) { entry in
                            Label(entry, systemImage: SourceStyle.symbolName(group.source))
                        }
                    }
                }
            }
            if !preview.already.isEmpty {
                Section(String(localized: "Already Following")) {
                    ForEach(preview.already, id: \.source) { group in
                        ForEach(group.entries, id: \.self) { Text($0).foregroundStyle(.secondary) }
                    }
                }
            }
            if !preview.skipped.isEmpty {
                Section(String(localized: "Skipped")) {
                    ForEach(preview.skipped, id: \.self) { skipped in
                        VStack(alignment: .leading) {
                            Text(skipped.what)
                            Text(skipped.why).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            ForEach(preview.warnings, id: \.self) { warning in
                Label(warning, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
            }
        }
        .listStyle(.inset)
    }
}

/// Locked: show the passphrase entry in the window
struct LockedView: View {
    @Environment(AppModel.self) private var model
    @State private var passphrase = ""
    @State private var remember = false
    @State private var working = false
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "lock.fill")
                .font(.system(size: 38))
                .foregroundStyle(Color.accentColor)
            Text(String(localized: "Youpdated is locked")).font(.title2.weight(.semibold))
            SecureField(String(localized: "Passphrase"), text: $passphrase)
                .textFieldStyle(.roundedBorder)
                .controlSize(.large)
                .focused($focused)
                .onSubmit(unlock)
            Toggle(String(localized: "Remember on this computer"), isOn: $remember)
                .frame(maxWidth: .infinity, alignment: .leading)
                .help(String(localized: "Keeps the passphrase in your keychain"))
            if let error = model.passphraseError {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Spacer()
                if working { ProgressView().controlSize(.small) }
                Button(String(localized: "Unlock"), action: unlock)
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    .disabled(passphrase.isEmpty || working)
            }
        }
        .padding(28)
        .frame(width: 380)
        .cardSurface()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear { focused = true }
    }

    private func unlock() {
        guard !passphrase.isEmpty, !working else { return }
        working = true
        Task {
            if await model.unlock(passphrase, remember: remember) { passphrase = "" }
            working = false
        }
    }
}

/// slim bar under toolbar: paused, or CLI notice
struct SlimBar<Actions: View>: View {
    let symbol: String
    let text: String
    @ViewBuilder var actions: Actions

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: symbol).foregroundStyle(.secondary)
            Text(text).lineLimit(2)
            Spacer(minLength: 8)
            actions
        }
        .font(.callout)
        .controlSize(.small)
        .padding(.horizontal, 16)
        .padding(.vertical, 7)
        .background(.bar)
        .overlay(alignment: .bottom) { Divider() }
    }
}

struct ProgressLine: View {
    let fraction: Double

    var body: some View {
        GeometryReader { geometry in
            Capsule()
                .fill(Color.accentColor)
                .frame(width: max(geometry.size.width * fraction, 6))
                .animation(.easeOut(duration: 0.3), value: fraction)
        }
        .frame(height: 2)
        .accessibilityElement()
        .accessibilityLabel(String(localized: "Checking"))
        .accessibilityValue(Text(fraction, format: .percent.precision(.fractionLength(0))))
    }
}
