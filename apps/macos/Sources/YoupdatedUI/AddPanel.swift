import SwiftUI
import YoupdatedKit

/// +: paste or type, see the card as it will look in the feed, then Follow
public struct AddPanel: View {
    var large = false

    public init(large: Bool = false) {
        self.large = large
    }

    @Environment(AppModel.self) private var model
    @Environment(WindowState.self) private var window
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var focused: Bool

    public var body: some View {
        @Bindable var add = model.add
        VStack(alignment: .leading, spacing: 12) {
            Text(large ? String(localized: "Paste a link to anything you want to follow") : String(localized: "Paste a link or name"))
                .font(large ? .title3.weight(.semibold) : .headline)
            TextField(String(localized: "Link or name"), text: $add.text)
                .textFieldStyle(.roundedBorder)
                .controlSize(large ? .large : .regular)
                .focused($focused)
                .onSubmit { follow() }

            if !add.choices.isEmpty {
                Picker(String(localized: "Source"), selection: Binding(
                    get: { add.chosen },
                    set: { source in if let source { Task { await add.pick(source) } } }
                )) {
                    ForEach(add.choices, id: \.self) { source in
                        Text(SourceStyle.name(source)).tag(String?.some(source))
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .transition(.opacity)
            }

            preview(add)
                .animation(Motion.fade, value: add.flow)

            HStack(spacing: 12) {
                if add.preview.map({ !$0.alreadyWatching }) == true {
                    PriorityMenu(priority: add.priority, showsName: true) { add.priority = $0 }
                        .transition(.opacity)
                }
                Spacer()
                if let preview = add.preview, preview.alreadyWatching {
                    Button(String(localized: "Show in Following")) {
                        window.showsAdd = false
                        window.showInFollowing(TargetKey(source: preview.source, key: preview.key), model: model)
                    }
                    .keyboardShortcut(.defaultAction)
                } else {
                    Button(String(localized: "Follow")) { follow() }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut(.defaultAction)
                        .disabled(!add.canFollow)
                }
            }

            if !large {
                Button(String(localized: "Import a File…")) {
                    window.showsAdd = false
                    window.choosesImport = true
                }
                .buttonStyle(.link)
            }
        }
        .padding(large ? 20 : 16)
        .frame(width: large ? 460 : 340)
        .animation(Motion.expand(reduceMotion), value: add.choices)
        .onAppear { focused = true }
        .onChange(of: add.flow) { _, flow in
            if case .added = flow {
                window.showsAdd = false
                add.reset()
            }
        }
    }

    private func follow() {
        guard model.add.canFollow else { return }
        Task { await model.add.follow() }
    }

    @ViewBuilder private func preview(_ add: AddModel) -> some View {
        switch add.flow {
        case .ready(_, let preview), .adding(_, let preview):
            VStack(alignment: .leading, spacing: 6) {
                PreviewCard(preview: preview)
                if let error = preview.fetchError {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .lineLimit(2)
                }
            }
        case .failed(_, let message):
            Label(message, systemImage: "questionmark.circle")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        default:
            if add.isLooking {
                HStack {
                    Spacer()
                    ProgressView().controlSize(.small)
                    Spacer()
                }
                .frame(height: 64)
            }
        }
    }
}

/// The card as it would appear
struct PreviewCard: View {
    let preview: PreviewReply
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            IconView(source: preview.source, key: preview.key, name: preview.display)
            VStack(alignment: .leading, spacing: 3) {
                Text(preview.display).font(.headline).lineLimit(1)
                if let newest = preview.newest {
                    HStack(spacing: 4) {
                        Text(newest.version.map(Feed.versionText) ?? newest.title)
                            .lineLimit(1)
                        if let published = newest.published {
                            Text("· \(Feed.time(published, times: model.viewOptions.times, now: model.currentDate, calendar: model.calendar))")
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                Text(SourceLabel.label(source: preview.source, key: preview.key))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .cardSurface()
    }
}
