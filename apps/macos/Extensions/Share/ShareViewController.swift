import AppKit
import SwiftUI
import UniformTypeIdentifiers
import YoupdatedKit

// Share → Youpdated.
// Sandboxed extension and doesn't run the engine

final class ShareViewController: NSViewController {
    private let state = ShareState()

    override var nibName: NSNib.Name? { nil }

    override func loadView() {
        let host = NSHostingView(rootView: ShareView(state: state, cancel: { [weak self] in self?.cancel() },
                                                    watch: { [weak self] in self?.watch() }))
        host.frame = NSRect(x: 0, y: 0, width: 360, height: 150)
        view = host
        preferredContentSize = host.fittingSize
        Task { @MainActor in state.value = await self.sharedValue() }
    }

    private func sharedValue() async -> String? {
        let items = extensionContext?.inputItems as? [NSExtensionItem] ?? []
        for item in items {
            for provider in item.attachments ?? [] {
                if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier),
                   let url = try? await provider.loadItem(forTypeIdentifier: UTType.url.identifier) as? URL,
                   !url.isFileURL {
                    return url.absoluteString
                }
                if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier),
                   let text = try? await provider.loadItem(forTypeIdentifier: UTType.plainText.identifier) as? String,
                   let value = DeepLink.watchValue(from: text) {
                    return value
                }
            }
            if let text = item.attributedContentText?.string, let value = DeepLink.watchValue(from: text) {
                return value
            }
        }
        return nil
    }

    private func watch() {
        guard let value = state.value else { return cancel() }
        do {
            guard let queue = ShareQueue.shared() else { throw CocoaError(.featureUnsupported) }
            try queue.append([value])
        } catch {
            state.failed = true
            return
        }
        launchAppIfNeeded()
        extensionContext?.completeRequest(returningItems: nil)
    }

    private func cancel() {
        extensionContext?.cancelRequest(withError: CocoaError(.userCancelled))
    }

    private func launchAppIfNeeded() {
        let app = Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let running = NSWorkspace.shared.runningApplications.contains { $0.bundleURL?.standardizedFileURL == app.standardizedFileURL }
        guard !running, app.pathExtension == "app" else { return }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        NSWorkspace.shared.openApplication(at: app, configuration: configuration)
    }
}

@MainActor
@Observable
final class ShareState {
    var value: String?
    var failed = false
}

struct ShareView: View {
    let state: ShareState
    let cancel: () -> Void
    let watch: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(String(localized: "Watch with Youpdated"), systemImage: "bell.badge")
                .font(.headline)
            if let value = state.value {
                Text(value)
                    .lineLimit(2)
                    .truncationMode(.middle)
                    .foregroundStyle(.secondary)
                Text(String(localized: "Youpdated shows what it is before adding."))
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            } else {
                Text(String(localized: "There's no link to watch here."))
                    .foregroundStyle(.secondary)
            }
            if state.failed {
                Text(String(localized: "Youpdated couldn't accept this link. Open Youpdated and paste it."))
                    .font(.caption)
                    .foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button(String(localized: "Cancel"), role: .cancel, action: cancel)
                    .keyboardShortcut(.cancelAction)
                Button(String(localized: "Watch"), action: watch)
                    .keyboardShortcut(.defaultAction)
                    .disabled(state.value == nil)
            }
        }
        .padding(16)
        .frame(width: 360)
    }
}
