import AppKit
import SwiftUI
import YoupdatedKit

/// Updates: what changed, top to bottom
struct FeedView: View {
    @Environment(AppModel.self) private var model
    @Environment(WindowState.self) private var window
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var atTop = true
    @FocusState private var focused: Bool

    var body: some View {
        let feed = model.feed
        let layout = feed.layout
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    FeedHeadingView(heading: layout.heading)
                    if let target = feed.targetFilter {
                        FilterChip(title: target.display) {
                            withAnimation(Motion.cards(reduceMotion)) { feed.showOnly(nil) }
                        }
                    }
                    if model.offersSelfWatch {
                        SelfWatchOffer()
                    }
                    if let update = layout.update {
                        SelfUpdateCard(update: update)
                            .transition(Motion.arrival(reduceMotion))
                    }
                    ForEach(layout.sections) { section in
                        if !section.title.isEmpty {
                            SectionTitle(text: section.title)
                        }
                        ForEach(Array(section.cards.enumerated()), id: \.element.id) { index, card in
                            FeedCardView(card: card, cascade: index)
                                .id(card.id)
                                .transition(Motion.arrival(reduceMotion))
                        }
                    }
                    if layout.isEmpty && !feed.filter.isEmpty {
                        ContentUnavailableView.search(text: feed.filter)
                    } else if layout.isEmpty && !model.isFirstRun && !layout.heading.upToDate {
                        ContentUnavailableView(String(localized: "Nothing yet"), systemImage: "tray")
                            .padding(.top, 40)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 16)
                .frame(maxWidth: 640, alignment: .leading)
                .frame(maxWidth: .infinity)
                // new cards slide in once per check
                .animation(atTop ? Motion.cards(reduceMotion) : nil, value: layout.cards.map(\.id))
            }
            .onScrollGeometryChange(for: Bool.self) { $0.contentOffset.y < 60 } action: { _, top in atTop = top }
            .focusable()
            .focusEffectDisabled()
            .focused($focused)
            .onKeyPress(.upArrow) { select(-1, proxy) }
            .onKeyPress(.downArrow) { select(1, proxy) }
            .onKeyPress(.space) { toggleSelected() }
            .onKeyPress(.return) { toggleSelected() }
            .onKeyPress(.escape) {
                guard feed.expandedID != nil else { return .ignored }
                withAnimation(Motion.expand(reduceMotion)) { feed.collapse() }
                return .handled
            }
            .onPasteCommand(of: [.url, .plainText]) { _ in
                // cmd + V with a link on the clipboard opens Add, filled in
                if let link = ClipboardCheck.link(from: NSPasteboard.general.string(forType: .string)) {
                    window.add(link, model: model)
                }
            }
            .onAppear { focused = true }
        }
    }

    private func select(_ offset: Int, _ proxy: ScrollViewProxy) -> KeyPress.Result {
        model.feed.move(by: offset)
        if let id = model.feed.selectedID {
            withAnimation(.easeOut(duration: 0.12)) { proxy.scrollTo(id, anchor: .center) }
        }
        return .handled
    }

    private func toggleSelected() -> KeyPress.Result {
        guard let card = model.feed.selectedCard else { return .ignored }
        withAnimation(Motion.expand(reduceMotion)) { model.feed.toggle(card) }
        return .handled
    }
}

/// "3 new updates", "Since this morning · Checked 9:41"; "Paused until 15:00" resumes
struct FeedHeadingView: View {
    let heading: FeedHeading
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(heading.title)
                .font(.title2.weight(.semibold))
                .contentTransition(.numericText())
            if !heading.parts.isEmpty {
                HStack(spacing: 0) {
                    ForEach(Array(heading.parts.enumerated()), id: \.offset) { index, part in
                        if index > 0 { Text(" · ") }
                        switch part {
                        case .text(let text):
                            Text(text).contentTransition(.numericText())
                        case .paused(let text):
                            Button(text) { Task { await model.resume() } }
                                .buttonStyle(.link)
                                .help(String(localized: "Resume Checks"))
                        }
                    }
                }
                .font(.callout)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }
        }
        .padding(.horizontal, 4)
        .padding(.bottom, 4)
        .animation(Motion.fade, value: heading)
        .accessibilityElement(children: .combine)
    }
}

struct FilterChip: View {
    let title: String
    let clear: () -> Void
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 6) {
            Text(title)
            Button(action: clear) {
                Image(systemName: "xmark.circle.fill")
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .help(String(localized: "Show Everything"))
        }
        .font(.callout)
        .padding(.horizontal, 10)
        .padding(.vertical, 4)
        .background(Capsule().fill(Color.accentColor.opacity(hovering ? 0.2 : 0.12)))
        .onHover { inside in withAnimation(Motion.hover) { hovering = inside } }
    }
}

/// A target's updates: card, line for low priority and Compact density
struct FeedCardView: View {
    let card: FeedCard
    /// Placement in the section
    var cascade = 0

    @Environment(AppModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var hovering = false

    private var expanded: Bool { model.feed.expandedID == card.id }
    private var selected: Bool { model.feed.showsSelection && model.feed.selectedID == card.id }

    var body: some View {
        Group {
            if card.isCompact && !expanded {
                compact
            } else {
                full
            }
        }
        .contentShape(Rectangle())
        .onTapGesture {
            model.feed.showsSelection = false
            withAnimation(Motion.expand(reduceMotion)) { model.feed.toggle(card) }
        }
        .onHover { inside in withAnimation(Motion.hover) { hovering = inside } }
        .contextMenu { CardMenu(card: card) }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }

    private var full: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                IconView(source: card.target.source, key: card.target.key, name: card.target.display,
                            starred: card.starred)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(card.target.display)
                            .font(.headline)
                            .lineLimit(1)
                        Spacer(minLength: 8)
                        marks
                    }
                    Text(card.lead)
                        .font(card.leadIsVersion
                              ? .system(.title2, design: .rounded).weight(.semibold).monospacedDigit()
                              : .title3.weight(.medium))
                        .lineLimit(2)
                        .contentTransition(.numericText())
                        .animation(Motion.fade, value: card.lead)
                    if !expanded, let line = card.line {
                        Text(line)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                    Text(footer)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.top, 1)
                }
            }
            if expanded {
                ExpandedCard(card: card)
                    .transition(reduceMotion ? .opacity : .opacity.animation(.easeOut(duration: 0.15).delay(0.04)))
            }
        }
        .padding(14)
        .overlay(alignment: .leading) {
            if card.hasEdge {
                UnevenRoundedRectangle(topLeadingRadius: 12, bottomLeadingRadius: 12, style: .continuous)
                    .fill(Color.accentColor)
                    .frame(width: 3)
            }
        }
        .cardSurface(hovering: hovering, selected: selected)
        .opacity(card.isHistory && !expanded ? 0.6 : 1)
    }

    private var compact: some View {
        HStack(spacing: 10) {
            IconView(source: card.target.source, key: card.target.key, name: card.target.display,
                        size: 24, starred: card.starred)
            Text(card.target.display)
                .fontWeight(.medium)
                .lineLimit(1)
                .layoutPriority(1)
            Text(card.lead)
                .foregroundStyle(.secondary)
                .monospacedDigit()
                .lineLimit(1)
                .contentTransition(.numericText())
            Spacer(minLength: 8)
            Text("\(card.label) · \(time(card.date))")
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .fixedSize()
            marks
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background {
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(.primary.opacity(hovering || selected ? 0.06 : 0))
        }
        .overlay {
            if selected {
                RoundedRectangle(cornerRadius: 9, style: .continuous).strokeBorder(Color.accentColor, lineWidth: 2)
            }
        }
        .opacity(card.isHistory ? 0.6 : 1)
    }

    private var marks: some View {
        HStack(spacing: 6) {
            if card.priority == .high {
                Image(systemName: PriorityStyle.symbolName(.high))
                    .font(.caption)
                    .foregroundStyle(Color.accentColor)
                    .help(PriorityStyle.label(.high))
                    .accessibilityLabel(PriorityStyle.menuTitle(.high))
            }
            Circle()
                .fill(Color.accentColor)
                .frame(width: 8, height: 8)
                .opacity(card.isUnread ? 1 : 0)
                .animation(Motion.fade.delay(Double(cascade) * 0.03), value: card.isUnread)
                .accessibilityLabel(card.isUnread ? String(localized: "Unread") : "")
        }
    }

    private var footer: String {
        let when = expanded ? RelativeTime.full(card.date, calendar: model.calendar) : time(card.date)
        var parts = [card.label, when]
        if card.moreCount > 0 && !expanded { parts.append(String(localized: "+\(card.moreCount) more")) }
        return parts.joined(separator: " · ")
    }

    private func time(_ date: Date) -> String {
        Feed.time(date, times: model.viewOptions.times, now: model.currentDate, calendar: model.calendar)
    }
}

/// Notes, target's other updates, actions, all inside the card
struct ExpandedCard: View {
    let card: FeedCard
    @Environment(AppModel.self) private var model
    @State private var showsAll = false

    var body: some View {
        let blocks = Notes.blocks(card.newest.body)
        let (shown, clipped) = Notes.clipped(blocks)
        VStack(alignment: .leading, spacing: 10) {
            Divider().padding(.top, 12)
            if blocks.isEmpty {
                Text(card.newest.title)
                if let url = URL(string: card.newest.url) {
                    Link(card.newest.url, destination: url)
                        .font(.callout)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array((showsAll ? blocks : shown).enumerated()), id: \.offset) { _, block in
                        NoteBlockView(block: block)
                    }
                }
                .textSelection(.enabled)
                if clipped && !showsAll {
                    HStack {
                        Spacer()
                        Button(String(localized: "Show Full Notes")) { showsAll = true }
                            .buttonStyle(.link)
                    }
                }
            }
            if card.items.count > 1 {
                Divider()
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(String(localized: "Also new")).foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(card.items.dropFirst()) { item in
                            Button {
                                Task { await model.feed.open(card, item: item) }
                            } label: {
                                Text("\(Feed.lead(item).text) · \(Feed.time(item.date, times: model.viewOptions.times, now: model.currentDate, calendar: model.calendar))")
                            }
                            .buttonStyle(.link)
                        }
                    }
                }
                .font(.callout)
            }
            Divider()
            HStack(spacing: 12) {
                Button {
                    Task { await model.feed.open(card) }
                } label: {
                    Label(String(localized: "Open"), systemImage: "arrow.up.right")
                }
                .buttonStyle(.borderedProminent)
                Button(card.isUnread ? String(localized: "Mark Read") : String(localized: "Mark Unread")) {
                    Task { await model.feed.setRead(!card.isUnread, card) }
                }
                Spacer()
                PriorityMenu(priority: card.priority, target: card.target.targetKey, showsName: true)
            }
        }
    }
}

struct NoteBlockView: View {
    let block: NoteBlock

    var body: some View {
        switch block.kind {
        case .heading:
            Text(markdown).font(.subheadline.weight(.semibold))
        case .bullet:
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text("•").foregroundStyle(.secondary)
                Text(markdown)
            }
        case .paragraph:
            Text(markdown)
        }
    }

    private var markdown: AttributedString {
        (try? AttributedString(markdown: block.text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(block.text)
    }
}

/// Priority control
struct PriorityMenu: View {
    let priority: Priority
    var target: TargetKey?
    var showsName = false
    /// Instead of saving to the engine
    var choose: ((Priority) -> Void)?

    @Environment(AppModel.self) private var model
    @State private var hovering = false

    var body: some View {
        Menu {
            PriorityChoices(selection: priority) { set($0) }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: PriorityStyle.symbolName(priority))
                    .foregroundStyle(priority == .high ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(.secondary))
                if showsName {
                    Text(Wording.priority(priority))
                    Image(systemName: "chevron.down")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 5)
            .padding(.vertical, 3)
            .background {
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(.primary.opacity(hovering ? 0.08 : 0))
            }
            .contentShape(Rectangle())
        }
        .onHover { inside in withAnimation(Motion.hover) { hovering = inside } }
        .menuStyle(.button)
        .buttonStyle(.plain)
        .menuIndicator(.hidden)
        .fixedSize()
        .help(PriorityStyle.label(priority))
        .accessibilityLabel(String(localized: "Priority: \(PriorityStyle.menuTitle(priority))"))
    }

    private func set(_ new: Priority) {
        if let choose {
            choose(new)
        } else if let target {
            Task { await model.inbox.setPriority(new, for: target) }
        }
    }
}

/// "priority: explanation", with its bell
struct PriorityChoices: View {
    let selection: Priority
    let choose: (Priority) -> Void

    var body: some View {
        ForEach(Priority.allCases, id: \.self) { priority in
            Toggle(isOn: Binding(get: { selection == priority }, set: { _ in choose(priority) })) {
                Label(PriorityStyle.menuTitle(priority), systemImage: PriorityStyle.symbolName(priority))
            }
        }
    }
}

/// Open, Mark read/unread, Priority, Filters, Snooze, Copy link, Show in Following, Stop following
struct CardMenu: View {
    let card: FeedCard
    @Environment(AppModel.self) private var model
    @Environment(WindowState.self) private var window

    var body: some View {
        Button(String(localized: "Open")) { Task { await model.feed.open(card) } }
        Button(card.isUnread ? String(localized: "Mark as Read") : String(localized: "Mark as Unread")) {
            Task { await model.feed.setRead(!card.isUnread, card) }
        }
        Menu(String(localized: "Priority")) {
            PriorityChoices(selection: card.priority) { new in
                Task { await model.inbox.setPriority(new, for: card.target.targetKey) }
            }
        }
        if model.sources.canFilter,
           let target = model.sources.targets.first(where: { $0.id == card.target.targetKey }) {
            Button(String(localized: "Filters…")) { window.filtering = target }
        }
        Button(String(localized: "Snooze 1 Hour")) { Task { await model.feed.snooze(card) } }
        Divider()
        Button(String(localized: "Copy Link")) {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(card.newest.url, forType: .string)
        }
        Button(String(localized: "Show in Following")) { window.showInFollowing(card.target.targetKey, model: model) }
        Divider()
        if model.inbox.canDelete {
            Button(card.items.count == 1 ? String(localized: "Delete Update") : String(localized: "Delete \(card.items.count) Updates"),
                   role: .destructive) {
                Task { await model.feed.delete(card) }
            }
        }
        if let target = model.sources.targets.first(where: { $0.id == card.target.targetKey }) {
            Button(String(localized: "Stop Following…"), role: .destructive) { window.removing = target }
        }
    }
}

/// Pinned above sections if an app update is unread
struct SelfUpdateCard: View {
    let update: AppUpdate

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "arrow.down.circle.fill")
                .font(.system(size: 24))
                .foregroundStyle(Color.accentColor)
            Text(Wording.appUpdate(update.version)).font(.headline)
            Spacer(minLength: 8)
            DownloadButton(update: update)
        }
        .padding(14)
        .cardSurface(tint: .accentColor)
    }
}

struct DownloadButton: View {
    let update: AppUpdate
    @Environment(AppModel.self) private var model
    @Environment(Downloader.self) private var downloader

    var body: some View {
        switch downloader.state {
        case .downloading:
            ProgressView().controlSize(.small)
        case .failed(let message):
            Button(String(localized: "Try Again")) { download() }
                .help(message)
        default:
            Button(String(localized: "Download")) { download() }
                .buttonStyle(.borderedProminent)
        }
    }

    private func download() {
        downloader.download(update)
        Task { await model.inbox.mark(true, ids: [update.itemId]) }
    }
}

struct SelfWatchOffer: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "star.circle.fill")
                .font(.system(size: 24))
                .foregroundStyle(Color.accentColor)
            Text(String(localized: "Watch Youpdated for app updates?")).font(.headline)
            Spacer(minLength: 8)
            Button(String(localized: "Not Now")) { Task { await model.declineSelfWatch() } }
            Button(String(localized: "Watch")) { Task { await model.watchSelf() } }
                .buttonStyle(.borderedProminent)
        }
        .padding(14)
        .cardSurface()
    }
}

/// Nothing followed yet
struct FirstLaunchView: View {
    @Environment(AppModel.self) private var model
    @Environment(WindowState.self) private var window

    static let examples: [(String, String, String)] = [
        (String(localized: "GitHub repo"), "chevron.left.forwardslash.chevron.right", "https://github.com/godotengine/godot"),
        (String(localized: "Steam game"), "gamecontroller", "https://store.steampowered.com/app/440/"),
        (String(localized: "npm package"), "shippingbox", "react"),
        (String(localized: "YouTube channel"), "play.rectangle", "https://www.youtube.com/@NASA"),
    ]

    var body: some View {
        ScrollView {
            VStack(spacing: 18) {
                Spacer(minLength: 24)
                Image(systemName: "bell.badge")
                    .font(.system(size: 40, weight: .regular))
                    .foregroundStyle(Color.accentColor)
                    .symbolRenderingMode(.hierarchical)
                AddPanel(large: true)
                    .cardSurface()
                HStack(spacing: 8) {
                    ForEach(Self.examples, id: \.2) { title, symbol, value in
                        Button {
                            model.add.fill(value)
                        } label: {
                            Label(title, systemImage: symbol)
                                .font(.callout)
                                .fixedSize()
                        }
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.capsule)
                    }
                }
                Button(String(localized: "Import a File…")) { window.choosesImport = true }
                    .buttonStyle(.link)
                Spacer(minLength: 24)
            }
            .padding(24)
            .frame(maxWidth: 560)
            .frame(maxWidth: .infinity)
        }
    }
}
