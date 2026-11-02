import ScreenCaptureKit
import AppKit
import Foundation

// ScreenCheck: dedicated capture tool for the verification pipeline.
// Modes:
//   ScreenCheck list                      -> list shareable windows
//   ScreenCheck cap <windowID> <out.png>  -> capture one window via SCK
// Requires a Screen Recording grant for this app (TCC). Never rebuild after grant.
//
// Fix (2026-11-02): `cap` used to abort with
//   Assertion failed: (did_initialize), function CGS_REQUIRE_INIT,
//   file CGInitialization.c, line 44   (exit 134, no PNG)
// because this process never initialized a connection to the WindowServer.
// SCShareableContent (`list`) is served over XPC and works without one, but
// SCScreenshotManager's image path touches CoreGraphics directly and asserts
// when CGS was never initialized. Initializing NSApplication below performs
// that initialization. The async work is also driven by dispatchMain()
// instead of blocking the main thread on a semaphore, so SCK callbacks are
// delivered on the main run loop as AppKit expects.
let nsApp = NSApplication.shared
nsApp.setActivationPolicy(.accessory)
nsApp.finishLaunching()

let args = CommandLine.arguments

func finish(_ code: Int32) -> Never {
    exit(code)
}

guard args.count >= 2 else {
    print("usage: ScreenCheck list | cap <windowID> <out.png>")
    exit(1)
}
let mode = args[1]

DispatchQueue.main.asyncAfter(deadline: .now() + 60) {
    print("ERROR timeout")
    exit(5)
}

SCShareableContent.getWithCompletionHandler { content, error in
    if let error = error {
        print("ERROR shareable content: \(error)")
        finish(2)
    }
    guard let content = content else {
        print("ERROR no content")
        finish(2)
    }
    if mode == "list" {
        for w in content.windows {
            let app = w.owningApplication?.applicationName ?? "?"
            let f = w.frame
            print("win id=\(w.windowID) app=\(app) title=\(w.title ?? "") frame=\(Int(f.minX)),\(Int(f.minY)) \(Int(f.width))x\(Int(f.height)) onscreen=\(w.isOnScreen)")
        }
        finish(0)
    }
    guard mode == "cap", args.count >= 4, let raw = UInt32(args[2]) else {
        print("usage: ScreenCheck cap <windowID> <out.png>")
        finish(1)
    }
    let outPath = args[3]
    guard let win = content.windows.first(where: { $0.windowID == CGWindowID(raw) }) else {
        print("ERROR target window \(raw) not found")
        finish(3)
    }
    let filter = SCContentFilter(desktopIndependentWindow: win)
    let cfg = SCStreamConfiguration()
    cfg.width = Int(win.frame.width) * 2
    cfg.height = Int(win.frame.height) * 2
    cfg.showsCursor = false
    SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg) { image, err in
        if let err = err {
            print("ERROR capture: \(err)")
            finish(2)
        }
        guard let image = image else {
            print("ERROR nil image")
            finish(2)
        }
        let rep = NSBitmapImageRep(cgImage: image)
        if let data = rep.representation(using: .png, properties: [:]) {
            do {
                try data.write(to: URL(fileURLWithPath: outPath))
                print("wrote \(image.width)x\(image.height) -> \(outPath)")
                finish(0)
            } catch {
                print("ERROR write: \(error)")
                finish(4)
            }
        }
        finish(4)
    }
}
dispatchMain()
