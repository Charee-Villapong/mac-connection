import ScreenCaptureKit
import AppKit
import Foundation

// usage: swift sck.swift [windowID out.png]
// Lists all shareable windows (app, id, frame, title, onscreen),
// and if a windowID + out path are given, captures that window via SCK.
let sem = DispatchSemaphore(value: 0)
var targetID: CGWindowID = 0
var outPath = ""
if CommandLine.arguments.count >= 3, let v = UInt32(CommandLine.arguments[1]) {
    targetID = CGWindowID(v)
    outPath = CommandLine.arguments[2]
}

SCShareableContent.getWithCompletionHandler { content, error in
    if let error = error {
        print("ERROR shareable content: \(error)")
        sem.signal()
        return
    }
    guard let content = content else {
        print("ERROR no content")
        sem.signal()
        return
    }
    for w in content.windows {
        let app = w.owningApplication?.applicationName ?? "?"
        let f = w.frame
        print("win id=\(w.windowID) app=\(app) title=\(w.title ?? "") frame=\(Int(f.minX)),\(Int(f.minY)) \(Int(f.width))x\(Int(f.height)) onscreen=\(w.isOnScreen)")
    }
    if targetID == 0 {
        sem.signal()
        return
    }
    guard let win = content.windows.first(where: { $0.windowID == targetID }) else {
        print("ERROR target window \(targetID) not in shareable content")
        sem.signal()
        return
    }
    let filter = SCContentFilter(desktopIndependentWindow: win)
    let cfg = SCStreamConfiguration()
    cfg.width = Int(win.frame.width) * 2
    cfg.height = Int(win.frame.height) * 2
    cfg.showsCursor = false
    SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg) { image, err in
        if let err = err {
            print("ERROR capture: \(err)")
            sem.signal()
            return
        }
        guard let image = image else {
            print("ERROR nil image")
            sem.signal()
            return
        }
        let rep = NSBitmapImageRep(cgImage: image)
        if let data = rep.representation(using: .png, properties: [:]) {
            do {
                try data.write(to: URL(fileURLWithPath: outPath))
                print("wrote \(image.width)x\(image.height) -> \(outPath)")
            } catch {
                print("ERROR write: \(error)")
            }
        }
        sem.signal()
    }
}
_ = sem.wait(timeout: .now() + 60)
print("SCK_DONE")
