import AppKit
import CoreGraphics

func postKey(_ keyCode: CGKeyCode, flags: CGEventFlags = []) {
    let src = CGEventSource(stateID: .hidSystemState)
    let down = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: true)
    let up = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: false)
    down?.flags = flags; up?.flags = flags
    down?.post(tap: .cghidEventTap)
    up?.post(tap: .cghidEventTap)
}

func clickAt(_ x: Double, _ y: Double) {
    let src = CGEventSource(stateID: .hidSystemState)
    let p = CGPoint(x: x, y: y)
    CGEvent(mouseEventSource: src, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap)
    usleep(60000)
    CGEvent(mouseEventSource: src, mouseType: .leftMouseDown, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap)
    usleep(60000)
    CGEvent(mouseEventSource: src, mouseType: .leftMouseUp, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap)
}

func typeUnicode(_ text: String) {
    let src = CGEventSource(stateID: .hidSystemState)
    for ch in text {
        let units = Array(String(ch).utf16)
        let down = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: true)
        down?.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
        down?.post(tap: .cghidEventTap)
        let up = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: false)
        up?.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
        up?.post(tap: .cghidEventTap)
        usleep(30000)
    }
}

// ---- Accessibility helpers ----
func axStr(_ el: AXUIElement, _ attr: String) -> String {
    var v: CFTypeRef?
    if AXUIElementCopyAttributeValue(el, attr as CFString, &v) != .success { return "" }
    if let str = v as? String { return str }
    if let num = v as? NSNumber { return num.stringValue }
    return ""
}
func axPoint(_ el: AXUIElement, _ attr: String) -> CGPoint {
    var v: CFTypeRef?
    if AXUIElementCopyAttributeValue(el, attr as CFString, &v) != .success { return .zero }
    var pt = CGPoint.zero
    if let val = v, CFGetTypeID(val) == AXValueGetTypeID() {
        AXValueGetValue(val as! AXValue, .cgPoint, &pt)
    }
    return pt
}
func axSize(_ el: AXUIElement, _ attr: String) -> CGSize {
    var v: CFTypeRef?
    if AXUIElementCopyAttributeValue(el, attr as CFString, &v) != .success { return .zero }
    var sz = CGSize.zero
    if let val = v, CFGetTypeID(val) == AXValueGetTypeID() {
        AXValueGetValue(val as! AXValue, .cgSize, &sz)
    }
    return sz
}
func axChildren(_ el: AXUIElement) -> [AXUIElement] {
    var v: CFTypeRef?
    if AXUIElementCopyAttributeValue(el, kAXChildrenAttribute as CFString, &v) != .success { return [] }
    return v as? [AXUIElement] ?? []
}
var axCount = 0
func axWalk(_ el: AXUIElement, _ depth: Int) {
    if axCount > 500 || depth > 9 { return }
    axCount += 1
    let role = axStr(el, kAXRoleAttribute)
    let title = axStr(el, kAXTitleAttribute)
    let val = axStr(el, kAXValueAttribute)
    let desc = axStr(el, kAXDescriptionAttribute)
    let pos = axPoint(el, kAXPositionAttribute)
    let size = axSize(el, kAXSizeAttribute)
    let indent = String(repeating: "  ", count: depth)
    var line = "\(indent)\(role) pos=(\(Int(pos.x)),\(Int(pos.y))) size=(\(Int(size.width))x\(Int(size.height)))"
    if !title.isEmpty { line += " title=\(title.prefix(40))" }
    if !val.isEmpty { line += " value=\(val.prefix(80))" }
    if !desc.isEmpty { line += " desc=\(desc.prefix(40))" }
    print(line)
    for c in axChildren(el) { axWalk(c, depth + 1) }
}

let args = CommandLine.arguments
guard args.count >= 2 else { print("usage"); exit(1) }
switch args[1] {
case "access":
    print("post_access=\(CGPreflightPostEventAccess()) listen=\(CGPreflightListenEventAccess()) ax_trusted=\(AXIsProcessTrusted())")
case "axline":
    if let app = NSWorkspace.shared.runningApplications.first(where: { $0.localizedName == "LINE" }) {
        let appEl = AXUIElementCreateApplication(app.processIdentifier)
        axWalk(appEl, 0)
    } else { print("LINE not running") }
case "front":
    if let app = NSWorkspace.shared.frontmostApplication {
        print("front=\(app.localizedName ?? "?") pid=\(app.processIdentifier)")
    }
case "windows":
    let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    if let list = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] {
        for w in list {
            let owner = w[kCGWindowOwnerName as String] as? String ?? "?"
            let pid = w[kCGWindowOwnerPID as String] as? Int ?? 0
            let layer = w[kCGWindowLayer as String] as? Int ?? -1
            var b = "?"
            if let bd = w[kCGWindowBounds as String] as? [String: Double] {
                b = "x=\(Int(bd["X"] ?? 0)) y=\(Int(bd["Y"] ?? 0)) w=\(Int(bd["Width"] ?? 0)) h=\(Int(bd["Height"] ?? 0))"
            }
            if layer == 0 { print("win owner=\(owner) pid=\(pid) \(b)") }
        }
    }
case "click":
    guard args.count >= 4, let x = Double(args[2]), let y = Double(args[3]) else { exit(1) }
    clickAt(x, y); print("clicked \(x) \(y)")
case "key":
    guard args.count >= 3 else { exit(1) }
    switch args[2] {
    case "enter": postKey(36)
    case "tab": postKey(48)
    case "esc": postKey(53)
    case "delete": postKey(51)
    case "cmd+v": postKey(9, flags: .maskCommand)
    case "cmd+a": postKey(0, flags: .maskCommand)
    case "cmd+c": postKey(8, flags: .maskCommand)
    default: print("unknown key"); exit(1)
    }
    print("key sent \(args[2])")
case "paste":
    guard args.count >= 3 else { exit(1) }
    let pb = NSPasteboard.general
    pb.clearContents()
    pb.setString(args[2], forType: .string)
    usleep(100000)
    postKey(9, flags: .maskCommand)
    print("pasted \(args[2].count) chars")
case "type":
    guard args.count >= 3 else { exit(1) }
    typeUnicode(args[2]); print("typed \(args[2].count) chars")
default:
    print("unknown command"); exit(1)
}
