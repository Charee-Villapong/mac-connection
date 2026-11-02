import CoreGraphics
import AppKit
import Foundation

// usage: swift wincap.swift <windowID> <out.png>
// Captures one window's backing store via CGWindowListCreateImage,
// bypassing display compositing / Spaces.
let args = CommandLine.arguments
guard args.count >= 3, let raw = UInt32(args[1]) else {
    print("usage: wincap.swift <windowID> <out.png>")
    exit(1)
}
let wid = CGWindowID(raw)
guard let img = CGWindowListCreateImage(.null, .optionIncludingWindow, wid, [.bestResolution]) else {
    print("ERROR nil image (create failed)")
    exit(2)
}
let rep = NSBitmapImageRep(cgImage: img)
guard let data = rep.representation(using: .png, properties: [:]) else {
    print("ERROR png encode failed")
    exit(3)
}
do {
    try data.write(to: URL(fileURLWithPath: args[2]))
    print("wrote \(img.width)x\(img.height) -> \(args[2])")
} catch {
    print("ERROR write: \(error)")
    exit(4)
}
