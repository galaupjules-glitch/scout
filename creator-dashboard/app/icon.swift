// Dessine l'icône de l'app (carré sombre arrondi + rond bleu) en PNG 1024×1024
import Cocoa

let size = 1024.0
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size), bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                           bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let inset = size * 0.1
NSColor(calibratedWhite: 0.11, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: inset, y: inset, width: size - 2 * inset, height: size - 2 * inset),
             xRadius: size * 0.18, yRadius: size * 0.18).fill()
NSColor(calibratedRed: 0.23, green: 0.51, blue: 0.96, alpha: 1).setFill()
let r = size * 0.24
NSBezierPath(ovalIn: NSRect(x: size / 2 - r, y: size / 2 - r, width: 2 * r, height: 2 * r)).fill()
NSGraphicsContext.current = nil
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
