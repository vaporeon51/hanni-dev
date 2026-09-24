import AppKit
import Foundation

let root = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = root.appendingPathComponent("static/og-image.png")
let photoPaths = [
  ("aespa", "0aa5560b78cc12cc.jpg"),
  ("IVE", "2bab0db9fb7f28dd.jpg"),
  ("BLACKPINK", "ade83f06edda49e5.jpg"),
  ("NewJeans", "6d72ef3811bf6d6b.jpg"),
]
let size = NSSize(width: 1200, height: 630)
guard let rep = NSBitmapImageRep(
  bitmapDataPlanes: nil, pixelsWide: 1200, pixelsHigh: 630,
  bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
  isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
), let graphics = NSGraphicsContext(bitmapImageRep: rep) else { fatalError("Could not create output canvas") }
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = graphics
graphics.imageInterpolation = .high
let paper = NSColor(calibratedRed: 1, green: 0.973, blue: 0.957, alpha: 1)
let blush = NSColor(calibratedRed: 0.98, green: 0.89, blue: 0.89, alpha: 1)
let berry = NSColor(calibratedRed: 0.55, green: 0.15, blue: 0.31, alpha: 1)
let pink = NSColor(calibratedRed: 0.78, green: 0.30, blue: 0.48, alpha: 1)
let muted = NSColor(calibratedRed: 0.55, green: 0.42, blue: 0.47, alpha: 1)
paper.setFill()
NSBezierPath(rect: NSRect(origin: .zero, size: size)).fill()

// Soft color blooms keep the layout warm while leaving breathing room.
for (x, y, diameter, color) in [
  (-150.0, 430.0, 310.0, blush), (1015.0, 405.0, 330.0, blush),
  (-120.0, -190.0, 380.0, blush), (980.0, -210.0, 400.0, blush)
] {
  color.setFill()
  NSBezierPath(ovalIn: NSRect(x: x, y: y, width: diameter, height: diameter)).fill()
}

func drawText(_ value: String, at point: NSPoint, font: NSFont, color: NSColor) {
  value.draw(at: point, withAttributes: [.font: font, .foregroundColor: color])
}
func centerText(_ value: String, y: CGFloat, font: NSFont, color: NSColor) {
  let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color]
  let width = (value as NSString).size(withAttributes: attributes).width
  value.draw(at: NSPoint(x: (1200 - width) / 2, y: y), withAttributes: attributes)
}
func rounded(_ rect: NSRect, radius: CGFloat) -> NSBezierPath {
  NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
}

// A simple brand and feature line framed by the original photo cards.
let titlePanel = NSRect(x: 247, y: 140, width: 706, height: 350)
paper.setFill(); rounded(titlePanel, radius: 32).fill()
NSColor(calibratedRed: 0.95, green: 0.84, blue: 0.87, alpha: 1).setStroke()
let panelOutline = rounded(titlePanel, radius: 32); panelOutline.lineWidth = 2; panelOutline.stroke()
centerText("YOUR FAVES, ALL IN ONE PLACE", y: 416, font: .monospacedSystemFont(ofSize: 15, weight: .medium), color: pink)
centerText("hanni♡", y: 295, font: NSFont(name: "Georgia", size: 108) ?? .systemFont(ofSize: 108), color: berry)
// Render the same SVG symbols used by the site, so the preview stays in sync.
let iconDocument = try XMLDocument(contentsOf: root.appendingPathComponent("static/icons.svg"))
let features = [("bias sorter", "i-heart"), ("leaderboard", "i-crown"), ("feed", "i-dice"), ("sets", "i-folder"), ("scroll", "i-swirl")]
for (index, feature) in features.enumerated() {
  let centerX = CGFloat(332 + index * 134)
  let badge = NSRect(x: centerX - 25, y: 218, width: 50, height: 50)
  NSColor(calibratedRed: 0.98, green: 0.89, blue: 0.93, alpha: 1).setFill()
  rounded(badge, radius: 17).fill()
  guard let symbol = try iconDocument.nodes(forXPath: "//*[@id='\(feature.1)']").first?.copy() as? XMLElement else { fatalError("Missing icon: \(feature.1)") }
  symbol.name = "svg"
  symbol.addNamespace(XMLNode.namespace(withName: "", stringValue: "http://www.w3.org/2000/svg") as! XMLNode)
  for (key, value) in [("width", "96"), ("height", "96"), ("color", "#bb638b")] {
    symbol.addAttribute(XMLNode.attribute(withName: key, stringValue: value) as! XMLNode)
  }
  guard let icon = NSImage(data: symbol.xmlString.data(using: .utf8)!) else { fatalError("Cannot render SVG: \(feature.1)") }
  icon.draw(in: NSRect(x: centerX - 17, y: 226, width: 34, height: 34))
  let font = NSFont(name: "Georgia", size: 20) ?? .systemFont(ofSize: 20)
  let width = (feature.0 as NSString).size(withAttributes: [.font: font]).width
  drawText(feature.0, at: NSPoint(x: centerX - width / 2, y: 184), font: font, color: muted)
}

func drawPhotoCard(name: String, file: String, x: CGFloat, y: CGFloat, angle: CGFloat) {
  let cardSize = NSSize(width: 146, height: 174)
  let cardRect = NSRect(origin: .zero, size: cardSize)
  guard let image = NSImage(contentsOf: root.appendingPathComponent("static/sorter/idols/" + file)) else { fatalError("Missing photo: \(file)") }
  graphics.saveGraphicsState()
  let transform = NSAffineTransform()
  transform.translateX(by: x + cardSize.width / 2, yBy: y + cardSize.height / 2)
  transform.rotate(byDegrees: angle)
  transform.translateX(by: -cardSize.width / 2, yBy: -cardSize.height / 2)
  transform.concat()
  NSColor(calibratedWhite: 0.4, alpha: 0.10).setFill()
  rounded(NSRect(x: 3, y: -4, width: cardSize.width, height: cardSize.height), radius: 15).fill()
  NSColor.white.setFill(); rounded(cardRect, radius: 14).fill()
  let photoRect = NSRect(x: 7, y: 38, width: 132, height: 129)
  graphics.saveGraphicsState()
  rounded(photoRect, radius: 9).addClip()
  let sourceSize = image.size
  let factor = max(photoRect.width / sourceSize.width, photoRect.height / sourceSize.height)
  let cropWidth = photoRect.width / factor, cropHeight = photoRect.height / factor
  let crop = NSRect(x: (sourceSize.width - cropWidth) / 2, y: (sourceSize.height - cropHeight) / 2, width: cropWidth, height: cropHeight)
  image.draw(in: photoRect, from: crop, operation: .copy, fraction: 1)
  graphics.restoreGraphicsState()
  let captionFont = NSFont.systemFont(ofSize: 11, weight: .bold)
  let captionWidth = (name as NSString).size(withAttributes: [.font: captionFont]).width
  drawText(name, at: NSPoint(x: (cardSize.width - captionWidth) / 2, y: 12), font: captionFont, color: berry)
  graphics.restoreGraphicsState()
}

drawPhotoCard(name: photoPaths[0].0, file: photoPaths[0].1, x: 62, y: 383, angle: -7)
drawPhotoCard(name: photoPaths[1].0, file: photoPaths[1].1, x: 992, y: 380, angle: 6)
drawPhotoCard(name: photoPaths[2].0, file: photoPaths[2].1, x: 75, y: 71, angle: 6)
drawPhotoCard(name: photoPaths[3].0, file: photoPaths[3].1, x: 980, y: 72, angle: -6)
NSGraphicsContext.restoreGraphicsState()
guard let png = rep.representation(using: .png, properties: [:]) else { fatalError("Could not encode PNG") }
try png.write(to: outputURL)
print("Wrote \(outputURL.path) · 1200 × 630")
