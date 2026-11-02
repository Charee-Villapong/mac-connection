import CoreGraphics
import Foundation

let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
if let list = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] {
    for w in list {
        let owner = w[kCGWindowOwnerName as String] as? String ?? "?"
        let num = w[kCGWindowNumber as String] as? Int ?? 0
        let layer = w[kCGWindowLayer as String] as? Int ?? -1
        let name = w[kCGWindowName as String] as? String ?? ""
        var b = "?"
        if let bd = w[kCGWindowBounds as String] as? [String: Double] {
            b = "x=\(Int(bd["X"] ?? 0)) y=\(Int(bd["Y"] ?? 0)) w=\(Int(bd["Width"] ?? 0)) h=\(Int(bd["Height"] ?? 0))"
        }
        if layer == 0 {
            print("win num=\(num) owner=\(owner) \(b) name=\(name)")
        }
    }
}
