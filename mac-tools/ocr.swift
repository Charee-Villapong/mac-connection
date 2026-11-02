import Foundation
import Vision
import AppKit

// usage: swift ocr.swift <image-path> [scale]
// Prints each recognized text with its bounding box in POINTS
// (Vision boxes are normalized, origin bottom-left; image px / scale = points).
let args = CommandLine.arguments
guard args.count >= 2 else { print("usage: ocr.swift <image> [scale]"); exit(1) }
let path = args[1]
let scale: Double = args.count >= 3 ? (Double(args[2]) ?? 2.0) : 2.0

guard let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    print("ERROR cannot load image")
    exit(2)
}
let W = Double(cg.width), H = Double(cg.height)
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = ["ja-JP", "en-US"]
req.usesLanguageCorrection = false
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do {
    try handler.perform([req])
} catch {
    print("ERROR vision: \(error)")
    exit(3)
}
let obs = req.results ?? []
print("image_px=\(Int(W))x\(Int(H)) scale=\(scale) texts=\(obs.count)")
for o in obs {
    guard let cand = o.topCandidates(1).first else { continue }
    let b = o.boundingBox
    let x = b.minX * W / scale
    let y = (1.0 - b.maxY) * H / scale
    let w = b.width * W / scale
    let h = b.height * H / scale
    print(String(format: "TEXT [x=%.0f y=%.0f w=%.0f h=%.0f conf=%.2f] %@", x, y, w, h, cand.confidence, cand.string))
}
