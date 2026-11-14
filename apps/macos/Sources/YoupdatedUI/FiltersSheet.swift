import SwiftUI
import YoupdatedKit

/// Which versions are reported for an item: `filters.get`, `.preview`, and `.set`
public struct FiltersSheet: View {
    let target: Target
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var filters: FiltersModel?

    public init(target: Target) {
        self.target = target
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 12) {
                IconView(source: target.source, key: target.key, name: target.display, starred: target.starred)
                Text(String(localized: "Filters for \(target.starred ? "Youpdated" : target.display)"))
                    .font(.headline)
                    .lineLimit(1)
            }
            if let filters {
                FiltersForm(filters: filters)
            }
            HStack {
                if let filters, filters.phase == .saving || filters.phase == .loading {
                    ProgressView().controlSize(.small)
                }
                Spacer()
                Button(String(localized: "Cancel"), role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(String(localized: "Save")) {
                    Task { if await filters?.save() == true { dismiss() } }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(filters?.canSave != true)
            }
        }
        .padding(20)
        .frame(width: 440)
        .task {
            let made = model.sources.filters(for: target)
            filters = made
            await made?.load()
        }
    }
}

private struct FiltersForm: View {
    @Bindable var filters: FiltersModel

    var body: some View {
        switch filters.phase {
        case .loading:
            EmptyView()
        case .failed(let message):
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.secondary)
                .symbolRenderingMode(.multicolor)
        case .ready, .saving:
            if let reason = filters.saved?.reason {
                Label(reason, systemImage: "lock.fill").foregroundStyle(.secondary)
            }
            Form {
                versions
                Divider().padding(.vertical, 4)
                Section {
                    Toggle(String(localized: "Skip prereleases"), isOn: $filters.draft.skipPrereleases)
                        .disabled(filters.saved?.skipPrereleasesInherited == true)
                        .help(filters.saved?.skipPrereleasesInherited == true
                              ? String(localized: "The config skips them for this source")
                              : String(localized: "Alphas, betas, release candidates, and nightlies"))
                    Picker(String(localized: "Report"), selection: $filters.draft.bump) {
                        ForEach(Bump.allCases, id: \.self) { Text(FiltersText.bump($0)).tag($0) }
                    }
                    TextField(String(localized: "Nothing before"), text: $filters.draft.min,
                              prompt: Text(String(localized: "Any version")))
                        .help(String(localized: "A standard version number"))
                }
            }
            .formStyle(.columns)
            .disabled(!filters.isEditable || filters.phase == .saving)
            .onChange(of: filters.draft) { filters.draftChanged() }
            FiltersPreviewLine(filters: filters)
            if let problem = filters.problem {
                Label(problem, systemImage: "exclamationmark.triangle.fill")
                    .symbolRenderingMode(.multicolor)
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder private var versions: some View {
        Section {
            Picker(String(localized: "Show"), selection: $filters.draft.limitsVersions) {
                Text(String(localized: "Every version")).tag(false)
                Text(String(localized: "Only versions like…")).tag(true)
            }
            .pickerStyle(.radioGroup)
            if filters.draft.limitsVersions {
                ForEach(filters.offered) { pattern in
                    Toggle(isOn: ticked(pattern.pattern)) {
                        Text(pattern.label).monospaced()
                            + Text(verbatim: "  ")
                            + Text(String(localized: "\(pattern.count) recent")).foregroundStyle(.secondary)
                    }
                    .toggleStyle(.checkbox)
                }
                LabeledContent(String(localized: "Custom pattern")) {
                    TextField(String(localized: "Custom pattern"), text: $filters.draft.custom,
                              prompt: Text(verbatim: "v3.12.*"))
                        .labelsHidden()
                        .monospaced()
                        .autocorrectionDisabled()
                        .help(String(localized: "* for any characters. Separate patterns with commas."))
                }
            }
        }
    }

    private func ticked(_ pattern: String) -> Binding<Bool> {
        Binding(
            get: { filters.draft.ticked.contains(pattern) },
            set: { on in
                if on { filters.draft.ticked.insert(pattern) } else { filters.draft.ticked.remove(pattern) }
            })
    }
}

/// "4 of the last 20 versions fit: ..."
private struct FiltersPreviewLine: View {
    let filters: FiltersModel

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            if let preview = filters.preview {
                if let error = preview.fetchError {
                    Text(String(localized: "Couldn't list versions: \(error)"))
                        .help(error)
                } else if filters.limitsNothing {
                    Text(String(localized: "Select or enter patterns."))
                } else {
                    Text(FiltersText.fits(preview))
                }
                Spacer(minLength: 4)
                Button {
                    Task { await filters.refresh() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .disabled(filters.isPreviewing)
                .help(String(localized: "List versions again"))
                .accessibilityLabel(String(localized: "List versions again"))
            } else if filters.isPreviewing {
                ProgressView().controlSize(.small)
                Text(String(localized: "Listing versions…"))
            }
        }
        .font(.callout)
        .foregroundStyle(.secondary)
        .lineLimit(2)
        .frame(minHeight: 18)
    }
}
