import SwiftUI
import WidgetKit
import YoupdatedKit

// The widget doesn't run the engine, reads a snapshot (redacted when encrypted)

struct SnapshotEntry: TimelineEntry {
    let date: Date
    let snapshot: WidgetSnapshot?
}

struct SnapshotProvider: TimelineProvider {
    func placeholder(in context: Context) -> SnapshotEntry {
        SnapshotEntry(date: .now, snapshot: .placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (SnapshotEntry) -> Void) {
        completion(SnapshotEntry(date: .now, snapshot: context.isPreview ? .placeholder : Self.read()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<SnapshotEntry>) -> Void) {
        // app reloads the timeline after snapshots
        let entry = SnapshotEntry(date: .now, snapshot: Self.read())
        completion(Timeline(entries: [entry], policy: .after(.now.addingTimeInterval(30 * 60))))
    }

    static func read() -> WidgetSnapshot? {
        guard let group = ShareQueue.appGroup(),
              let container = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group)
        else { return nil }
        return WidgetSnapshot.read(from: container.appending(path: WidgetSnapshot.fileName))
    }
}

struct UnreadWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: SnapshotEntry

    var body: some View {
        Group {
            if let snapshot = entry.snapshot {
                switch family {
                case .systemMedium: medium(snapshot)
                default: small(snapshot)
                }
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    Image(systemName: "bell").font(.title2).foregroundStyle(.tint)
                    Spacer()
                    Text(String(localized: "Open Youpdated to start checking."))
                        .font(.callout)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
        .containerBackground(.fill.tertiary, for: .widget)
        .widgetURL(URL(string: "youpdated://open"))
    }

    private func small(_ snapshot: WidgetSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Image(systemName: snapshot.unread > 0 ? "bell.badge.fill" : "bell")
                .font(.title3)
                .foregroundStyle(.tint)
            Spacer()
            Text(snapshot.unread, format: .number)
                .font(.system(size: 40, weight: .semibold, design: .rounded))
                .contentTransition(.numericText())
            Text(snapshot.unread == 1 ? String(localized: "new update") : String(localized: "new updates"))
                .font(.callout)
                .foregroundStyle(.secondary)
            checked(snapshot)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func medium(_ snapshot: WidgetSnapshot) -> some View {
        HStack(alignment: .top, spacing: 14) {
            small(snapshot).frame(width: 110)
            VStack(alignment: .leading, spacing: 6) {
                if snapshot.redacted {
                    Spacer()
                    Text(String(localized: "\(Wording.newUpdates(snapshot.unread)), open to view"))
                        .font(.callout)
                    Text(String(localized: "Titles stay in your encrypted history."))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                } else if snapshot.items.isEmpty {
                    Spacer()
                    Text(String(localized: "You're up to date."))
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    Spacer()
                } else {
                    ForEach(snapshot.items.prefix(3)) { item in
                        VStack(alignment: .leading, spacing: 0) {
                            Text(item.display).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            Text(item.title).font(.callout).lineLimit(1)
                        }
                    }
                    Spacer(minLength: 0)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder private func checked(_ snapshot: WidgetSnapshot) -> some View {
        if let last = snapshot.lastCheck {
            Text(String(localized: "Checked \(last.formatted(.relative(presentation: .named)))"))
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .lineLimit(1)
        }
    }
}

struct UnreadWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "Unread", provider: SnapshotProvider()) { entry in
            UnreadWidgetView(entry: entry)
        }
        .configurationDisplayName(String(localized: "Youpdated"))
        .description(String(localized: "What's new in the things you follow."))
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

@main
struct YoupdatedWidgets: WidgetBundle {
    var body: some Widget {
        UnreadWidget()
    }
}

#Preview(as: .systemMedium) {
    UnreadWidget()
} timeline: {
    SnapshotEntry(date: .now, snapshot: .placeholder)
    SnapshotEntry(date: .now, snapshot: WidgetSnapshot(
        version: 1, writtenAt: .now, lastCheck: .now, unread: 3, redacted: true, items: []))
}
