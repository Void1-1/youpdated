// .dmg window's background
import AppKit

let width = 640.0, height = 400.0
let iconY = 165.0, leftX = 170.0, rightX = 470.0

let background = NSColor(srgbRed: 0.96, green: 0.96, blue: 0.97, alpha: 1)
let ink = NSColor(srgbRed: 0.11, green: 0.11, blue: 0.12, alpha: 1)
let muted = NSColor(srgbRed: 0.40, green: 0.40, blue: 0.43, alpha: 1)
let arrow = NSColor(srgbRed: 0.68, green: 0.68, blue: 0.71, alpha: 1)

func draw() {
  background.setFill()
  NSRect(x: 0, y: 0, width: width, height: height).fill()

  func text(_ string: String, y: Double, font: NSFont, color: NSColor) {
    let style = NSMutableParagraphStyle()
    style.alignment = .center
    let attributed = NSAttributedString(string: string, attributes: [.font: font, .foregroundColor: color, .paragraphStyle: style])
    let box = attributed.boundingRect(with: NSSize(width: width - 80, height: 200), options: [.usesLineFragmentOrigin])
    attributed.draw(with: NSRect(x: 40, y: height - y - box.height, width: width - 80, height: box.height), options: [.usesLineFragmentOrigin])
  }

  text("Drag Youpdated to Applications", y: 36, font: .systemFont(ofSize: 20, weight: .semibold), color: ink)

  let y = height - iconY
  let start = leftX + 92, end = rightX - 92
  let shaft = NSBezierPath()
  shaft.lineWidth = 5
  shaft.lineCapStyle = .round
  shaft.move(to: NSPoint(x: start, y: y))
  shaft.line(to: NSPoint(x: end - 8, y: y))
  arrow.setStroke()
  shaft.stroke()
  let head = NSBezierPath()
  head.lineWidth = 5
  head.lineCapStyle = .round
  head.lineJoinStyle = .round
  head.move(to: NSPoint(x: end - 18, y: y + 16))
  head.line(to: NSPoint(x: end, y: y))
  head.line(to: NSPoint(x: end - 18, y: y - 16))
  head.stroke()

  // The app isn't notarized (docs/macos-app.md), so first launch needs the following
  text("The first time you open Youpdated, macOS can’t verify it.\nClick Done, then Open Anyway in System Settings → Privacy & Security.",
       y: 296, font: .systemFont(ofSize: 12), color: muted)
}

func render(scale: Int, to url: URL) throws {
  guard let image = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: Int(width) * scale, pixelsHigh: Int(height) * scale,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)
  else { throw CocoaError(.fileWriteUnknown) }
  image.size = NSSize(width: width, height: height)
  NSGraphicsContext.saveGraphicsState()
  NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: image)
  draw()
  NSGraphicsContext.restoreGraphicsState()
  guard let png = image.representation(using: .png, properties: [:]) else { throw CocoaError(.fileWriteUnknown) }
  try png.write(to: url)
}

guard CommandLine.arguments.count == 2 else {
  FileHandle.standardError.write("usage: swift dmg-background.swift <folder>\n".data(using: .utf8)!)
  exit(2)
}
let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try render(scale: 1, to: folder.appendingPathComponent("background.png"))
try render(scale: 2, to: folder.appendingPathComponent("background@2x.png"))
