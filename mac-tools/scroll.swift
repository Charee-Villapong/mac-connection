import Cocoa
let a = CommandLine.arguments
guard a.count >= 4, let x = Double(a[1]), let y = Double(a[2]), let d = Int32(a[3]) else { exit(1) }
let pt = CGPoint(x: x, y: y)
CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: pt, mouseButton: .left)?.post(tap: .cghidEventTap)
usleep(200000)
for _ in 0..<4 {
    guard let e = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1, wheel1: d/4, wheel2: 0, wheel3: 0) else { exit(1) }
    e.location = pt
    e.post(tap: .cghidEventTap)
    usleep(60000)
}
print("scrolled \(d)")
