import Observation
import SwiftUI
import YoupdatedKit

@MainActor
@Observable
public final class WindowState {
    public enum Mode: Hashable, Sendable { case updates, following }

    public var mode: Mode = .updates
    public var showsAdd = false
    public var showsProblems = false
    public var choosesImport = false
    /// A file to preview for import
    public var importFile: URL?
    /// Set by cmd+F; the filter field takes focus and clears it
    public var focusesFilter = false
    public var removing: Target?
    /// Following scrolls to and highlights this row
    public var highlighted: TargetKey?

    public init() {}

    /// The + popover
    public func add(_ value: String? = nil, model: AppModel) {
        mode = .updates
        if let value, !value.isEmpty { model.add.fill(value) }
        showsAdd = true
    }

    public func showInFollowing(_ key: TargetKey, model: AppModel) {
        model.feed.filter = ""
        highlighted = key
        mode = .following
    }

    public func focusFilter() {
        focusesFilter = true
    }
}

enum Motion {
    static func expand(_ reduce: Bool) -> Animation {
        reduce ? .easeInOut(duration: 0.15) : .snappy(duration: 0.22)
    }

    static func cards(_ reduce: Bool) -> Animation {
        reduce ? .easeInOut(duration: 0.15) : .snappy(duration: 0.25)
    }

    static let fade = Animation.easeOut(duration: 0.18)
    static let hover = Animation.easeOut(duration: 0.12)

    static func arrival(_ reduce: Bool) -> AnyTransition {
        reduce ? .opacity : .move(edge: .top).combined(with: .opacity)
    }
}

/// section header style
struct SectionTitle: View {
    let text: String

    var body: some View {
        Text(text.uppercased())
            .font(.caption.weight(.semibold))
            .foregroundStyle(.secondary)
            .tracking(0.6)
            .padding(.top, 10)
            .padding(.leading, 4)
            .accessibilityAddTraits(.isHeader)
    }
}

struct CardSurface: ViewModifier {
    var hovering = false
    var selected = false
    var tint: Color? = nil

    func body(content: Content) -> some View {
        content
            .background {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Color(nsColor: .controlBackgroundColor))
                    .overlay {
                        if let tint {
                            RoundedRectangle(cornerRadius: 12, style: .continuous).fill(tint.opacity(0.08))
                        }
                    }
                    .brightness(hovering ? 0.02 : 0)
                    .shadow(color: .black.opacity(hovering ? 0.10 : 0.04), radius: hovering ? 8 : 2, y: hovering ? 3 : 1)
            }
            .overlay {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(selected ? Color.accentColor : Color(nsColor: .separatorColor).opacity(0.6),
                                  lineWidth: selected ? 2 : 0.5)
            }
    }
}

extension View {
    func cardSurface(hovering: Bool = false, selected: Bool = false, tint: Color? = nil) -> some View {
        modifier(CardSurface(hovering: hovering, selected: selected, tint: tint))
    }
}
