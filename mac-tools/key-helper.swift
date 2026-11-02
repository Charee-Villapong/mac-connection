import AppKit
import CoreGraphics

// KeyHelper — posts Return/Enter key events with several strategies so the
// receiving app (LINE Mac, Catalyst) can be tested without rebuilding.
// Modes differ by event tap (HID / session / pid-targeted) and by whether
// the character "\r" is attached to the events (real Return key events
// carry the character; bare synthetic keycode events may be dropped).

func makeKeyEvent(keyCode: CGKeyCode, keyDown: Bool, unicode: Bool) -> CGEvent? {
    let src = CGEventSource(stateID: .hidSystemState)
    let ev = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: keyDown)
    if unicode {
        let units = Array("\r".utf16)
        ev?.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
    }
    return ev
}

func postKey(_ keyCode: CGKeyCode, mode: String, pid: pid_t?) {
    let tapModes: [String: (CGEventTapLocation, Bool)] = [
        "hid": (.cghidEventTap, false),
        "hidu": (.cghidEventTap, true),
        "session": (.cgSessionEventTap, false),
        "sessionu": (.cgSessionEventTap, true),
    ]
    if let (tap, unicode) = tapModes[mode] {
        makeKeyEvent(keyCode: keyCode, keyDown: true, unicode: unicode)?.post(tap: tap)
        usleep(30000)
        makeKeyEvent(keyCode: keyCode, keyDown: false, unicode: unicode)?.post(tap: tap)
        return
    }
    if mode == "pid" || mode == "pidu" {
        guard let pid = pid else { print("pid required for mode \(mode)"); exit(1) }
        let unicode = (mode == "pidu")
        if let d = makeKeyEvent(keyCode: keyCode, keyDown: true, unicode: unicode) {
            d.postToPid(pid)
        }
        usleep(30000)
        if let u = makeKeyEvent(keyCode: keyCode, keyDown: false, unicode: unicode) {
            u.postToPid(pid)
        }
        return
    }
    print("unknown mode \(mode)")
    exit(1)
}

// keyf: same delivery as postKey, but with modifier flags set on both the
// keyDown and keyUp events. Flags spec: comma-separated subset of
// cmd,ctrl,shift,opt, or "-" for none.

func parseFlags(_ spec: String) -> CGEventFlags? {
    if spec == "-" { return CGEventFlags() }
    var flags = CGEventFlags()
    for part in spec.split(separator: ",") {
        switch part {
        case "cmd": flags.insert(.maskCommand)
        case "ctrl": flags.insert(.maskControl)
        case "shift": flags.insert(.maskShift)
        case "opt": flags.insert(.maskAlternate)
        default: return nil
        }
    }
    return flags
}

func makeKeyEventF(keyCode: CGKeyCode, keyDown: Bool, unicode: Bool, flags: CGEventFlags) -> CGEvent? {
    let src = CGEventSource(stateID: .hidSystemState)
    let ev = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: keyDown)
    ev?.flags = flags
    if unicode {
        let units = Array("\r".utf16)
        ev?.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
    }
    return ev
}

func postKeyF(_ keyCode: CGKeyCode, flags: CGEventFlags, mode: String, pid: pid_t?) {
    let tapModes: [String: (CGEventTapLocation, Bool)] = [
        "hid": (.cghidEventTap, false),
        "hidu": (.cghidEventTap, true),
        "session": (.cgSessionEventTap, false),
        "sessionu": (.cgSessionEventTap, true),
    ]
    if let (tap, unicode) = tapModes[mode] {
        makeKeyEventF(keyCode: keyCode, keyDown: true, unicode: unicode, flags: flags)?.post(tap: tap)
        usleep(30000)
        makeKeyEventF(keyCode: keyCode, keyDown: false, unicode: unicode, flags: flags)?.post(tap: tap)
        return
    }
    if mode == "pid" || mode == "pidu" {
        guard let pid = pid else { print("pid required for mode \(mode)"); exit(1) }
        let unicode = (mode == "pidu")
        if let d = makeKeyEventF(keyCode: keyCode, keyDown: true, unicode: unicode, flags: flags) {
            d.postToPid(pid)
        }
        usleep(30000)
        if let u = makeKeyEventF(keyCode: keyCode, keyDown: false, unicode: unicode, flags: flags) {
            u.postToPid(pid)
        }
        return
    }
    print("unknown mode \(mode)")
    exit(1)
}

let args = CommandLine.arguments
guard args.count >= 2 else {
    print("usage: KeyHelper access | enter <hid|hidu|session|sessionu|pid <pid>|pidu <pid>> | keycode <n> <mode> [pid]")
    exit(1)
}

switch args[1] {
case "access":
    print("post_access=\(CGPreflightPostEventAccess()) ax_trusted=\(AXIsProcessTrusted())")
case "enter":
    guard args.count >= 3 else { print("usage: KeyHelper enter <mode> [pid]"); exit(1) }
    let mode = args[2]
    let pid: pid_t? = args.count >= 4 ? pid_t(args[3]) : nil
    postKey(36, mode: mode, pid: pid)
    print("enter sent mode=\(mode)")
case "keycode":
    guard args.count >= 4, let code = Int(args[2]) else {
        print("usage: KeyHelper keycode <n> <mode> [pid]"); exit(1)
    }
    let mode = args[3]
    let pid: pid_t? = args.count >= 5 ? pid_t(args[4]) : nil
    postKey(CGKeyCode(code), mode: mode, pid: pid)
    print("keycode sent \(code) mode=\(mode)")
case "keyf":
    guard args.count >= 5, let code = Int(args[2]), let flags = parseFlags(args[3]) else {
        print("usage: KeyHelper keyf <n> <flags> <mode> [pid]  (flags: comma-separated cmd,ctrl,shift,opt or - for none)")
        exit(1)
    }
    let mode = args[4]
    let pid: pid_t? = args.count >= 6 ? pid_t(args[5]) : nil
    postKeyF(CGKeyCode(code), flags: flags, mode: mode, pid: pid)
    print("keyf sent \(code) flags=\(args[3]) mode=\(mode)")
default:
    print("unknown command \(args[1])")
    exit(1)
}
