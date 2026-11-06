import AppKit
import SwiftUI
import YoupdatedKit

/// A target's icon if ready and Show icons is on; otherwise monogram
struct IconView: View {
    let source: String
    let key: String
    let name: String
    var size: CGFloat = 40
    var starred = false

    @Environment(AppModel.self) private var model

    var body: some View {
        let target = TargetKey(source: source, key: key)
        ZStack(alignment: .bottomTrailing) {
            Group {
                if let data = model.icons.images[target], let image = NSImage(data: data) {
                    Image(nsImage: image)
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                } else if starred {
                    tile(.accentColor) {
                        Image(systemName: "star.fill").font(.system(size: size * 0.42, weight: .semibold))
                    }
                } else {
                    let monogram = Monogram(source: source, key: key, display: name)
                    tile(Color(rgb: monogram.color.rgb)) {
                        Text(monogram.initials)
                            .font(.system(size: size * (monogram.initials.count > 1 ? 0.36 : 0.44),
                                          weight: .semibold, design: .rounded))
                    }
                }
            }
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: size * 0.24, style: .continuous))

            if !starred && size >= 28 {
                Image(systemName: SourceStyle.symbolName(source))
                    .font(.system(size: size * 0.2, weight: .bold))
                    .foregroundStyle(.secondary)
                    .frame(width: size * 0.42, height: size * 0.42)
                    .background(Circle().fill(Color(nsColor: .windowBackgroundColor)))
                    .overlay(Circle().strokeBorder(Color(nsColor: .separatorColor).opacity(0.5), lineWidth: 0.5))
                    .offset(x: size * 0.12, y: size * 0.12)
            }
        }
        .accessibilityHidden(true)
        .task(id: target) { await model.icons.load(target) }
    }

    private func tile(_ color: Color, @ViewBuilder _ content: () -> some View) -> some View {
        ZStack {
            LinearGradient(colors: [color.mix(with: .white, by: 0.08), color], startPoint: .top, endPoint: .bottom)
            content().foregroundStyle(.white)
        }
    }
}

extension Color {
    /// From 0xRRGGBB
    init(rgb: UInt32) {
        self.init(
            .sRGB, red: Double((rgb >> 16) & 0xFF) / 255, green: Double((rgb >> 8) & 0xFF) / 255,
            blue: Double(rgb & 0xFF) / 255)
    }
}
