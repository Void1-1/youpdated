import SwiftUI
import YoupdatedKit

/// Watched targets, by source
struct FollowingView: View {
    @Environment(AppModel.self) private var model
    @Environment(WindowState.self) private var window
    @State private var selection: TargetKey?
    @FocusState private var listFocused: Bool

    var body: some View {
        let layout = Following.build(
            targets: model.sources.targets, items: model.inbox.items, filter: model.feed.filter,
            now: model.currentDate, calendar: model.calendar)
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    if let own = layout.own {
                        row(own, starred: true)
                    } else if model.feed.filter.isEmpty && model.sources.loaded && model.sources.selfTarget == nil {
                        WatchYoupdatedRow()
                    }
                    ForEach(layout.groups) { group in
                        SectionTitle(text: group.title)
                        ForEach(group.rows) { row($0) }
                    }
                    if layout.groups.isEmpty && layout.own == nil && !model.feed.filter.isEmpty {
                        ContentUnavailableView.search(text: model.feed.filter)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 16)
                .frame(maxWidth: 640)
                .frame(maxWidth: .infinity)
            }
            .focusable()
            .focusEffectDisabled()
            .focused($listFocused)
            .onAppear { listFocused = true }
            .onKeyPress(.upArrow) { move(-1, layout, proxy) }
            .onKeyPress(.downArrow) { move(1, layout, proxy) }
            .onKeyPress(.return) { showUpdates(selection) }
            .onDeleteCommand { remove(selection) }
            .onChange(of: window.highlighted, initial: true) { _, key in
                guard let key else { return }
                selection = key
                withAnimation(.easeOut(duration: 0.12)) { proxy.scrollTo(key, anchor: .center) }
                window.highlighted = nil
            }
        }
    }

    private func row(_ row: FollowingRow, starred: Bool = false) -> some View {
        FollowingRowView(row: row, selected: selection == row.id)
            .id(row.id)
            .contentShape(Rectangle())
            .onTapGesture {
                selection = row.id
                _ = showUpdates(row.id)
            }
            .contextMenu {
                Button(String(localized: "Show Updates")) { _ = showUpdates(row.id) }
                Menu(String(localized: "Priority")) {
                    PriorityChoices(selection: row.target.priority) { new in
                        Task { await model.inbox.setPriority(new, for: row.id) }
                    }
                }
                Divider()
                Button(String(localized: "Stop Following…"), role: .destructive) { window.removing = row.target }
            }
    }

    private func move(_ offset: Int, _ layout: FollowingLayout, _ proxy: ScrollViewProxy) -> KeyPress.Result {
        let keys = ([layout.own].compactMap { $0 } + layout.groups.flatMap(\.rows)).map(\.id)
        guard !keys.isEmpty else { return .ignored }
        let index = keys.firstIndex { $0 == selection }
        let next = index.map { min(max($0 + offset, 0), keys.count - 1) } ?? 0
        selection = keys[next]
        proxy.scrollTo(keys[next], anchor: .center)
        return .handled
    }

    /// Selecting a row filters Updates to that target
    private func showUpdates(_ key: TargetKey?) -> KeyPress.Result {
        guard let key, let target = model.sources.targets.first(where: { $0.id == key }) else { return .ignored }
        model.feed.showOnly(target.ref)
        window.mode = .updates
        return .handled
    }

    private func remove(_ key: TargetKey?) {
        window.removing = model.sources.targets.first { $0.id == key }
    }
}

struct FollowingRowView: View {
    let row: FollowingRow
    let selected: Bool
    @Environment(AppModel.self) private var model
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 10) {
            IconView(source: row.target.source, key: row.target.key, name: row.target.display,
                        size: 28, starred: row.target.starred)
            Text(row.target.starred ? String(localized: "Youpdated") : row.target.display)
                .lineLimit(1)
            Spacer(minLength: 8)
            // Youpdated's own row shows the version running
            Text(row.target.starred ? model.appVersion : row.latest)
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .frame(minWidth: 60, alignment: .trailing)
            PriorityMenu(priority: row.target.priority, target: row.id)
                .frame(width: 28)
            status
                .frame(width: 18)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(selected ? AnyShapeStyle(Color.accentColor.opacity(0.14))
                      : AnyShapeStyle(.primary.opacity(hovering ? 0.06 : 0)))
        }
        .onHover { inside in withAnimation(Motion.hover) { hovering = inside } }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var status: some View {
        switch row.status {
        case .ok:
            Circle().fill(.green.opacity(0.8)).frame(width: 7, height: 7)
                .help(row.target.lastChecked.map {
                    Feed.checked($0, now: model.currentDate, calendar: model.calendar)
                } ?? "")
                .accessibilityLabel(String(localized: "OK"))
        case .failed(let message):
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
                .help(message)
                .accessibilityLabel(String(localized: "Failed: \(message)"))
        case .notChecked:
            Image(systemName: "clock")
                .foregroundStyle(.tertiary)
                .help(String(localized: "Not checked yet"))
                .accessibilityLabel(String(localized: "Not checked yet"))
        }
    }
}

/// In place of Youpdated's entry
struct WatchYoupdatedRow: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(spacing: 10) {
            IconView(source: "git", key: "", name: "Youpdated", size: 28, starred: true)
            Text(String(localized: "Watch Youpdated for app updates"))
                .foregroundStyle(.secondary)
            Spacer()
            Button(String(localized: "Watch")) { Task { await model.watchSelf() } }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
    }
}

/// Stop following, optionally forgetting history (`rm --wipe`)
struct RemoveSheet: View {
    let target: Target
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var wipe = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 12) {
                IconView(source: target.source, key: target.key, name: target.display, starred: target.starred)
                Text(String(localized: "Stop following \(target.display)?")).font(.headline)
            }
            Toggle(String(localized: "Also forget its history"), isOn: $wipe)
                .help(String(localized: "Forgets what it has seen, its updates, and its priority"))
            HStack {
                Spacer()
                Button(String(localized: "Cancel"), role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(String(localized: "Stop Following"), role: .destructive) {
                    Task {
                        await model.sources.remove(target, wipe: wipe)
                        dismiss()
                    }
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 400)
    }
}
