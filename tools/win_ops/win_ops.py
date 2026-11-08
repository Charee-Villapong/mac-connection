"""win_ops: Mac の窓・メニュー操作専用 CLI。

gui_flow (LINE/Discord 送信・承認ゲート付き) とは分離した単一ファイル。
送信系・文字入力系の機能は持たない。Mac へは gui_flow と同じ GUI
エージェントキュー経由で実行し、結果は stdout に単一 JSON で返す。

仕様は同ディレクトリの SPEC.md (完了の定義) を参照。
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))

DEFAULT_SCREENCHECK_PATH = "~/muse-tools/ScreenCheck.app/Contents/MacOS/ScreenCheck"
DEFAULT_AGENT_DESKTOP_PATH = "~/muse-tools/agent-desktop/agent-desktop"

DEFAULT_TUNABLES = {
    "screencheck_path": DEFAULT_SCREENCHECK_PATH,
    "agent_desktop_path": DEFAULT_AGENT_DESKTOP_PATH,
    "capture_dir": "~/muse-tools/win-ops-captures",
    "session": "win-ops",
    "ssh_timeout": 240,
    "connect_timeout": 60,
    "queue_wait_loops": 400,
    "close_button_offset_px": 14,
    "close_button_tolerance_px": 12,
    "terminal_apps": ["Terminal", "ターミナル", "iTerm2"],
    "shell_commands": ["zsh", "bash", "login", "-zsh", "-bash", "sh", "-sh"],
}

# AppleScript の `tell application` で使う名前への寄せ (表示名 -> スクリプト名)。
_APPLESCRIPT_APP_NAMES = {"ターミナル": "Terminal"}

_REF_RE = re.compile(r"^@[^:\s]+:e\d+$")
_LIST_RE = re.compile(
    r"^win id=(\d+) app=(.*?) title=(.*?) "
    r"frame=(-?\d+),(-?\d+) (\d+)x(\d+) onscreen=(true|false)$"
)


class WinOpsError(Exception):
    """status を持つ win_ops の例外。"""

    def __init__(self, status, message=None, candidates=None):
        super().__init__(message or status)
        self.status = status
        self.candidates = list(candidates) if candidates else []


# --- 設定 -------------------------------------------------------------------


def load_config():
    path = os.environ.get("WIN_OPS_CONFIG") or os.path.join(HERE, "config.local.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            "config not found: %s (copy config.example.json to config.local.json)"
            % path
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _cfg(config, key, default=None):
    """config のトップレベル → tunables の順にキーを探す。"""
    if isinstance(config, dict):
        if config.get(key) is not None:
            return config[key]
        tun = config.get("tunables")
        if isinstance(tun, dict) and tun.get(key) is not None:
            return tun[key]
    return default


def _tunables(config):
    t = dict(DEFAULT_TUNABLES)
    if isinstance(config, dict):
        for key in list(t):
            value = _cfg(config, key)
            if value is not None:
                t[key] = value
    return t


def b64(s):
    return base64.b64encode(str(s).encode("utf-8")).decode("ascii")


# --- 純関数: ScreenCheck list の解析 -----------------------------------------


def parse_window_list(text):
    """ScreenCheck `list` の出力を windows 配列に解析する。"""
    windows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line.startswith("win "):
            continue
        m = _LIST_RE.match(line)
        if not m:
            continue
        windows.append(
            {
                "id": int(m.group(1)),
                "app": m.group(2),
                "title": m.group(3),
                "frame": {
                    "x": int(m.group(4)),
                    "y": int(m.group(5)),
                    "width": int(m.group(6)),
                    "height": int(m.group(7)),
                },
                "onscreen": m.group(8) == "true",
            }
        )
    return windows


def resolve_window(windows, window_id=None, title=None, app=None):
    """条件に一致する窓を一意に特定する。できなければ WinOpsError。"""
    found = list(windows or [])
    if window_id is not None:
        found = [w for w in found if w.get("id") == window_id]
    if title is not None:
        found = [w for w in found if w.get("title") == title]
    if app is not None:
        found = [w for w in found if w.get("app") == app]
    if not found:
        raise WinOpsError("window_not_found")
    if len(found) > 1:
        raise WinOpsError("ambiguous_window", candidates=found)
    return found[0]


# --- 純関数: AX ツリー --------------------------------------------------------
# wire アダプタ: agent-desktop の実エンベロープ形 (要素は ref_id キー) と
# fixtures の簡略形 (要素は ref キー) の両方を受け付け、内部形
# {ref, role, name, states, bounds, children} に寄せて扱う。


def _coerce_bounds(raw):
    if isinstance(raw, dict):
        try:
            return {
                "x": raw["x"],
                "y": raw["y"],
                "width": raw["width"],
                "height": raw["height"],
            }
        except (KeyError, TypeError):
            return None
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        return {"x": raw[0], "y": raw[1], "width": raw[2], "height": raw[3]}
    return None


def _normalize_node(elem):
    node = dict(elem)
    node["ref"] = elem.get("ref") or elem.get("ref_id") or ""
    node["role"] = elem.get("role") or ""
    node["name"] = elem.get("name") or ""
    states = elem.get("states")
    node["states"] = list(states) if isinstance(states, (list, tuple)) else []
    bounds = _coerce_bounds(elem.get("bounds") or elem.get("frame"))
    if bounds is not None:
        node["bounds"] = bounds
        node.setdefault("frame", bounds)
    return node


def iter_elements(tree):
    """ツリーの全ノードを深さ優先で yield する (正規化済みの dict)。"""
    if isinstance(tree, (list, tuple)):
        for item in tree:
            yield from iter_elements(item)
        return
    if not isinstance(tree, dict):
        return
    yield _normalize_node(tree)
    for child in tree.get("children") or []:
        yield from iter_elements(child)


def find_elements_by_name(tree, name):
    """name が完全一致する要素をすべて返す。"""
    return [e for e in iter_elements(tree) if e.get("name") == name]


def extract_tree(payload):
    """snapshot ペイロード (実エンベロープ形 / 簡略形) から tree を取り出す。"""
    if not isinstance(payload, dict):
        return None
    if payload.get("tree") is not None:
        return payload["tree"]
    data = payload.get("data")
    if isinstance(data, dict) and data.get("tree") is not None:
        return data["tree"]
    return None


def extract_window_frame(payload):
    """実エンベロープ形の data.window からフレームを取り出す (無ければ None)。"""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        window = data.get("window")
        if isinstance(window, dict):
            return _coerce_bounds(window.get("bounds") or window.get("frame") or window)
    return None


def find_close_button_candidates(tree, frame, offset_px=14, tolerance_px=12):
    """窓フレーム左上 + offset が中心になる role=button 要素を返す。

    許容差は中心のずれに対する各軸の絶対差で判定する。
    """
    if not isinstance(frame, dict):
        return []
    target_x = frame["x"] + offset_px
    target_y = frame["y"] + offset_px
    found = []
    for elem in iter_elements(tree):
        if elem.get("role") != "button":
            continue
        bounds = _coerce_bounds(elem.get("bounds"))
        if bounds is None:
            continue
        center_x = bounds["x"] + bounds["width"] / 2
        center_y = bounds["y"] + bounds["height"] / 2
        if (
            abs(center_x - target_x) <= tolerance_px
            and abs(center_y - target_y) <= tolerance_px
        ):
            found.append(elem)
    return found


# --- 純関数: ps 出力 ----------------------------------------------------------


def busy_processes(ps_text, shell_commands):
    """ps 出力から、シェル許可リスト以外のプロセス名を順序どおり返す。"""
    shells = set(shell_commands or [])
    busy = []
    for raw in (ps_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] == "PID" or len(parts) < 2:
            continue
        if not parts[0].lstrip("-").isdigit():
            continue
        comm = parts[1].rsplit("/", 1)[-1]
        if comm not in shells:
            busy.append(comm)
    return busy


# --- SSH + GUI キュー (gui_flow の Mac クラスの手順を小さく再実装) -------------


class Mac:
    """実 transport: SSH で GUI エージェントキューへスクリプトを投入する。"""

    def __init__(self, cfg):
        s = (cfg or {})["ssh"]
        t = _tunables(cfg)
        self.timeout = int(t["ssh_timeout"])
        self.connect_timeout = int(t["connect_timeout"])
        self.wait_loops = int(t["queue_wait_loops"])
        self.base = [
            "ssh",
            "-i",
            s["identity"],
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ProxyCommand=%s %%h %%p" % s["proxy_script"],
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=25",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPath=%s" % s.get("control_path", "/tmp/winops-ssh-ctl"),
            "-o",
            "ControlPersist=180",
            "%s@%s" % (s["user"], s["host"]),
        ]

    def run(self, remote_cmd, stdin_text=None, timeout=None):
        if timeout is None:
            timeout = self.timeout
        data = stdin_text.encode("utf-8") if stdin_text is not None else None
        p = subprocess.run(
            self.base + [remote_cmd],
            input=data,
            capture_output=True,
            timeout=timeout,
        )
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        return p.returncode, out, err

    def run_queue(self, script_body, name, extras=(), timeout=None):
        """キューへ 1 本投入 (アトミック) し、完了を待ち、結果 dict を返す。"""
        if timeout is None:
            timeout = self.timeout
        remote_parts = [
            "mkdir -p ~/muse-tools/gf-out",
            "cat > ~/muse-tools/gf-out/.in-%s" % name,
            "mv ~/muse-tools/gf-out/.in-%s ~/muse-tools/gui-queue/%s" % (name, name),
            "n=0; while [ ! -f ~/muse-tools/gui-done/%s ]; do "
            "n=$((n+1)); if [ $n -gt %d ]; then echo GF_WAIT_TIMEOUT; exit 9; fi; "
            "sleep 0.3; done" % (name, self.wait_loops),
            "cat ~/muse-tools/gf-out/%s.result" % name,
        ]
        for suf in extras:
            remote_parts.append("echo __GF_FILE__%s" % suf)
            remote_parts.append(
                "cat ~/muse-tools/gf-out/%s%s 2>/dev/null" % (name, suf)
            )
            remote_parts.append("echo __GF_EOF__%s" % suf)
        remote = "; ".join(remote_parts)
        rc, out, err = self.run(remote, stdin_text=script_body, timeout=timeout)
        result = {"_files": {}}
        chunks = out.split("__GF_FILE__")
        head = chunks[0]
        for line in head.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
        for chunk in chunks[1:]:
            suf, _, rest = chunk.partition("\n")
            suf = suf.strip()
            body, _, _ = rest.partition("__GF_EOF__" + suf)
            result["_files"][suf] = body
        result["_ssh_rc"] = rc
        if "GF_WAIT_TIMEOUT" in out:
            result["status"] = "queue_timeout"
        return result


# --- キュースクリプト生成 ------------------------------------------------------
# スクリプトごとに使うツールの変数だけを置く (list 系は SC のみ、
# ax/click 系は AD のみ)。HDR の __NAME__ は実名に置換して返す。


def _script_head(name, tool_line):
    lines = [
        "export LC_ALL=en_US.UTF-8",
        tool_line,
        "mkdir -p ~/muse-tools/gf-out",
        "R=~/muse-tools/gf-out/__NAME__.result",
        ": > $R",
        'say() { echo "$1" >> $R; }',
    ]
    return lines


def _finish(lines, name):
    return ("\n".join(lines) + "\n").replace("__NAME__", name)


def build_list_script(name, screencheck_path=None):
    """ScreenCheck で窓一覧を取得し .list に残すスクリプト。"""
    sc = screencheck_path or DEFAULT_SCREENCHECK_PATH
    lines = _script_head(name, "SC=%s" % sc)
    lines.append("rm -f ~/muse-tools/gf-out/__NAME__.list")
    lines.append("$SC list > ~/muse-tools/gf-out/__NAME__.list 2>&1")
    lines.append('say "status=ok"')
    return _finish(lines, name)


def build_cap_script(name, window_id=None, screencheck_path=None):
    """ScreenCheck で 1 窓を撮影し、PNG の base64 とメタを残すスクリプト。"""
    sc = screencheck_path or DEFAULT_SCREENCHECK_PATH
    wid = int(window_id) if window_id is not None else 0
    lines = _script_head(name, "SC=%s" % sc)
    lines.append(
        "rm -f ~/muse-tools/gf-out/__NAME__.png "
        "~/muse-tools/gf-out/__NAME__.png.b64 "
        "~/muse-tools/gf-out/__NAME__.meta"
    )
    lines.append(
        "$SC cap %d ~/muse-tools/gf-out/__NAME__.png "
        "> ~/muse-tools/gf-out/__NAME__.meta 2>&1" % wid
    )
    lines.append("if [ -s ~/muse-tools/gf-out/__NAME__.png ]; then")
    lines.append(
        "  base64 -i ~/muse-tools/gf-out/__NAME__.png "
        "> ~/muse-tools/gf-out/__NAME__.png.b64"
    )
    lines.append('  say "status=ok"')
    lines.append("else")
    lines.append('  say "status=window_not_found"')
    lines.append("fi")
    return _finish(lines, name)


def build_ax_script(
    name,
    app=None,
    surface="window",
    max_depth=None,
    agent_desktop_path=None,
    session=None,
):
    """agent-desktop snapshot を実行し .ax.json に残すスクリプト。"""
    ad = agent_desktop_path or DEFAULT_AGENT_DESKTOP_PATH
    lines = _script_head(name, "AD=%s" % ad)
    lines.append("APP=$(echo '%s' | base64 -D)" % b64(app or ""))
    cmd = '$AD snapshot --app "$APP" --surface %s --session "%s" --include-bounds' % (
        surface or "window",
        session or "win-ops",
    )
    if max_depth is not None:
        cmd += " --max-depth %d" % int(max_depth)
    cmd += (
        " > ~/muse-tools/gf-out/__NAME__.ax.json "
        "2> ~/muse-tools/gf-out/__NAME__.ax.err"
    )
    lines.append("rm -f ~/muse-tools/gf-out/__NAME__.ax.json")
    lines.append(cmd)
    lines.append('say "status=ok"')
    return _finish(lines, name)


def build_snapshot_script(
    name,
    app=None,
    surface="window",
    max_depth=None,
    agent_desktop_path=None,
    session=None,
):
    """build_ax_script と同じ (snapshot の別名)。"""
    return build_ax_script(
        name,
        app=app,
        surface=surface,
        max_depth=max_depth,
        agent_desktop_path=agent_desktop_path,
        session=session,
    )


def build_click_script(name, ref=None, agent_desktop_path=None, session=None):
    """agent-desktop click を実行し .click.json に残すスクリプト。"""
    ad = agent_desktop_path or DEFAULT_AGENT_DESKTOP_PATH
    lines = _script_head(name, "AD=%s" % ad)
    lines.append("rm -f ~/muse-tools/gf-out/__NAME__.click.json")
    lines.append(
        '$AD click --session "%s" "%s" '
        "> ~/muse-tools/gf-out/__NAME__.click.json "
        "2> ~/muse-tools/gf-out/__NAME__.click.err" % (session or "win-ops", ref or "")
    )
    lines.append('say "status=ok"')
    return _finish(lines, name)


def _build_tty_script(name, app=None, title=None):
    """ターミナル窓の tty 特定 + その tty の ps を .ps に残すスクリプト。

    窓の特定は osascript (対象アプリの窓をタイトルで探し、選択中タブの
    tty を取得) で行う。アプリ名・タイトルは base64 経由で埋め込む。
    このスクリプトはビルド対象の公開ビルダーにはしない (内部用)。
    """
    script_app = _APPLESCRIPT_APP_NAMES.get(app or "", app or "")
    lines = [
        "export LC_ALL=en_US.UTF-8",
        "mkdir -p ~/muse-tools/gf-out",
        "R=~/muse-tools/gf-out/__NAME__.result",
        ": > $R",
        'say() { echo "$1" >> $R; }',
        "WAPP=$(echo '%s' | base64 -D)" % b64(script_app),
        "WTITLE=$(echo '%s' | base64 -D)" % b64(title or ""),
        "export WAPP WTITLE",
        ": > ~/muse-tools/gf-out/__NAME__.ps",
        "TTY=$(osascript <<'APPLESCRIPT'",
        'set targetTitle to system attribute "WTITLE"',
        'set targetApp to system attribute "WAPP"',
        "try",
        "  tell application targetApp",
        "    repeat with w in windows",
        "      if (name of w) contains targetTitle then",
        "        return tty of selected tab of w",
        "      end if",
        "    end repeat",
        "  end tell",
        "end try",
        'return ""',
        "APPLESCRIPT",
        ")",
        'say "tty=$TTY"',
        'if [ -n "$TTY" ]; then',
        '  ps -t "${TTY#/dev/}" -o pid,comm '
        "> ~/muse-tools/gf-out/__NAME__.ps 2>/dev/null",
        "fi",
        'say "status=ok"',
    ]
    return _finish(lines, name)


# --- WinOps 本体 ---------------------------------------------------------------


def _ok(command, **extra):
    result = {"ok": True, "command": command, "status": "ok"}
    result.update(extra)
    return result


def _fail(command, status, error=None, **extra):
    result = {
        "ok": False,
        "command": command,
        "status": status,
        "error": error or status,
    }
    result.update(extra)
    return result


class WinOps:
    """各コマンドの結果 dict を返す。transport は差し替え可能。"""

    def __init__(self, transport=None, config=None):
        self.transport = transport
        self.config = config or {}
        self.tunables = _tunables(self.config)
        self._counter = 0
        self._real_transport = None

    # -- 基盤 --

    def _get_transport(self):
        if self.transport is not None:
            return self.transport
        if self._real_transport is None:
            self._real_transport = Mac(self.config)
        return self._real_transport

    def _queue_name(self, action):
        self._counter += 1
        return "winops-%s-%d-%d-%d.sh" % (
            action,
            int(time.time() * 1000),
            os.getpid(),
            self._counter,
        )

    def _run_queue(self, script, name, extras=()):
        return self._get_transport().run_queue(script, name, extras=extras) or {}

    @staticmethod
    def _is_timeout(res):
        return (res or {}).get("status") == "queue_timeout"

    def _session(self):
        return (
            _cfg(self.config, "session")
            or _cfg(self.config, "session_name")
            or self.tunables["session"]
        )

    # -- list --

    def list_windows(self, app=None, onscreen_only=False):
        name = self._queue_name("list")
        script = build_list_script(
            name, screencheck_path=self.tunables["screencheck_path"]
        )
        res = self._run_queue(script, name, extras=(".list",))
        if self._is_timeout(res):
            return _fail("list", "queue_timeout")
        text = (res.get("_files") or {}).get(".list", "")
        if text.strip().startswith("ERROR"):
            return _fail(
                "list", "screencheck_error", error=text.strip().splitlines()[0]
            )
        windows = parse_window_list(text)
        if app is not None:
            windows = [w for w in windows if w["app"] == app]
        if onscreen_only:
            windows = [w for w in windows if w["onscreen"]]
        return _ok("list", windows=windows)

    # -- cap --

    def capture(self, window_id=None, out=None):
        name = self._queue_name("cap")
        script = build_cap_script(
            name,
            window_id=window_id,
            screencheck_path=self.tunables["screencheck_path"],
        )
        res = self._run_queue(script, name, extras=(".png.b64", ".meta"))
        if self._is_timeout(res):
            return _fail("cap", "queue_timeout")
        files = res.get("_files") or {}
        meta = files.get(".meta", "") or ""
        if "not found" in meta or res.get("status") == "window_not_found":
            return _fail("cap", "window_not_found", window_id=window_id)
        if meta.strip().startswith("ERROR"):
            return _fail("cap", "window_not_found", window_id=window_id)
        raw_b64 = re.sub(r"\s+", "", files.get(".png.b64", "") or "")
        if not raw_b64:
            return _fail("cap", "capture_failed", window_id=window_id)
        try:
            data = base64.b64decode(raw_b64, validate=True)
        except (ValueError, TypeError):
            return _fail("cap", "capture_failed", window_id=window_id)
        if out is not None:
            path = Path(out).expanduser()
        else:
            stamp = datetime.now(JST).strftime("%Y%m%d-%H%M%S")
            path = Path(self.tunables["capture_dir"]).expanduser() / (
                "win-ops-%s-%s.png" % (window_id, stamp)
            )
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return _ok("cap", window_id=window_id, path=str(path), bytes=len(data))

    # -- ax --

    def snapshot(self, app=None, surface=None, max_depth=None):
        name = self._queue_name("ax")
        script = build_ax_script(
            name,
            app=app,
            surface=surface or "window",
            max_depth=max_depth,
            agent_desktop_path=self.tunables["agent_desktop_path"],
            session=self._session(),
        )
        res = self._run_queue(script, name, extras=(".ax.json",))
        if self._is_timeout(res):
            return _fail("ax", "queue_timeout")
        raw = (res.get("_files") or {}).get(".ax.json", "") or ""
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return _fail("ax", "ax_failed", error="snapshot JSON を解析できない")
        if not isinstance(payload, dict) or payload.get("ok") is False:
            error = None
            if isinstance(payload, dict):
                error = payload.get("error") or "ax_failed"
            return _fail("ax", "ax_failed", error=str(error), snapshot=payload)
        return _ok("ax", snapshot=payload)

    # -- press --

    def _click_ref(self, ref):
        """ref を 1 回 click する。(ok, status) を返す。"""
        name = self._queue_name("click")
        script = build_click_script(
            name,
            ref=ref,
            agent_desktop_path=self.tunables["agent_desktop_path"],
            session=self._session(),
        )
        res = self._run_queue(script, name, extras=(".click.json",))
        if self._is_timeout(res):
            return False, "queue_timeout"
        raw = (res.get("_files") or {}).get(".click.json", "") or ""
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return False, "click_failed"
        if isinstance(payload, dict) and payload.get("ok") is True:
            return True, "ok"
        return False, "click_failed"

    def press(self, ref=None, app=None, name=None, surface=None):
        if ref is not None:
            if not _REF_RE.match(ref or ""):
                return _fail("press", "invalid_ref", ref=ref)
            ok, status = self._click_ref(ref)
            if not ok:
                return _fail("press", status, ref=ref)
            return _ok("press", ref=ref)
        if name is None:
            return _fail("press", "invalid_argument")
        snap = self.snapshot(app=app, surface=surface)
        if not snap.get("ok"):
            return _fail("press", snap.get("status", "ax_failed"))
        tree = extract_tree(snap.get("snapshot"))
        found = find_elements_by_name(tree, name)
        if not found:
            return _fail("press", "element_not_found")
        if len(found) > 1:
            return _fail("press", "ambiguous_element")
        elem = found[0]
        if "disabled" in (elem.get("states") or []):
            return _fail("press", "element_disabled")
        target_ref = elem.get("ref") or ""
        if not _REF_RE.match(target_ref):
            return _fail("press", "element_not_found")
        ok, status = self._click_ref(target_ref)
        if not ok:
            return _fail("press", status, ref=target_ref)
        return _ok("press", ref=target_ref)

    # -- close --

    def close(self, window_id=None, title=None, app=None, force=False):
        if window_id is None and title is None:
            return _fail(
                "close",
                "invalid_argument",
                error="close には --id か --title のいずれかが必要",
            )
        listed = self.list_windows()
        if not listed.get("ok"):
            return _fail("close", listed.get("status", "list_failed"))
        try:
            target = resolve_window(
                listed["windows"], window_id=window_id, title=title, app=app
            )
        except WinOpsError as exc:
            extra = {}
            if exc.candidates:
                extra["candidates"] = exc.candidates
            return _fail("close", exc.status, **extra)

        terminal_apps = self.tunables.get("terminal_apps") or []
        process_check = {"applicable": target["app"] in terminal_apps}
        if process_check["applicable"]:
            qname = self._queue_name("tty")
            script = _build_tty_script(qname, app=target["app"], title=target["title"])
            res = self._run_queue(script, qname, extras=(".ps",))
            if self._is_timeout(res):
                return _fail("close", "queue_timeout")
            tty = (res.get("tty") or "").strip()
            ps_text = (res.get("_files") or {}).get(".ps", "") or ""
            process_check["tty"] = tty or None
            if not tty:
                if not force:
                    return _fail(
                        "close",
                        "process_check_failed",
                        process_check=process_check,
                        window=target,
                    )
            else:
                busy = busy_processes(
                    ps_text, self.tunables.get("shell_commands") or []
                )
                process_check["processes"] = busy
                if busy and not force:
                    return _fail(
                        "close",
                        "busy",
                        processes=busy,
                        process_check=process_check,
                        window=target,
                    )

        snap = self.snapshot(app=target["app"])
        if not snap.get("ok"):
            return _fail(
                "close",
                snap.get("status", "ax_failed"),
                process_check=process_check,
                window=target,
            )
        payload = snap.get("snapshot")
        tree = extract_tree(payload)
        # フレームは snapshot の窓 (ツリーのルート) を正とする。
        # ボタンの座標は snapshot と同一座標系のため。
        frame = None
        if isinstance(tree, dict):
            root = _normalize_node(tree)
            frame = _coerce_bounds(root.get("bounds"))
        if frame is None:
            frame = extract_window_frame(payload) or target["frame"]
        candidates = find_close_button_candidates(
            tree,
            frame,
            offset_px=self.tunables["close_button_offset_px"],
            tolerance_px=self.tunables["close_button_tolerance_px"],
        )
        if not candidates:
            return _fail(
                "close",
                "close_button_not_found",
                process_check=process_check,
                window=target,
            )
        if len(candidates) > 1:
            return _fail(
                "close",
                "ambiguous_close_button",
                candidates=candidates,
                process_check=process_check,
                window=target,
            )
        ok, status = self._click_ref(candidates[0].get("ref") or "")
        if not ok:
            return _fail("close", status, process_check=process_check, window=target)

        after = self.list_windows()
        if not after.get("ok"):
            return _fail(
                "close",
                after.get("status", "list_failed"),
                process_check=process_check,
                window=target,
            )
        remaining = [w for w in after["windows"] if w["id"] == target["id"]]
        if remaining:
            return _fail(
                "close",
                "close_unverified",
                verified=False,
                process_check=process_check,
                window=target,
            )
        return _ok(
            "close",
            verified=True,
            process_check=process_check,
            window=target,
        )

    # 別名 (テスト・呼び出し側の表記ゆれ吸収)
    list = list_windows
    cap = capture
    ax = snapshot
    click = press
    cmd_list = list_windows
    cmd_cap = capture
    cmd_ax = snapshot
    cmd_press = press
    cmd_close = close


# --- CLI -----------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(prog="win_ops", description="Mac 窓・メニュー操作")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list")
    p_list.add_argument("--app")
    p_list.add_argument("--onscreen-only", action="store_true")

    p_cap = sub.add_parser("cap")
    p_cap.add_argument("window_id", type=int)
    p_cap.add_argument("--out")

    p_ax = sub.add_parser("ax")
    p_ax.add_argument("--app", required=True)
    p_ax.add_argument("--surface")
    p_ax.add_argument("--max-depth", type=int)

    p_press = sub.add_parser("press")
    p_press.add_argument("--ref")
    p_press.add_argument("--app")
    p_press.add_argument("--name")
    p_press.add_argument("--surface")

    p_close = sub.add_parser("close")
    p_close.add_argument("--id", type=int)
    p_close.add_argument("--title")
    p_close.add_argument("--app")
    p_close.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    command = args.command

    if command == "close" and args.id is None and args.title is None:
        result = _fail(
            "close",
            "invalid_argument",
            error="close には --id か --title のいずれかが必要",
        )
        print(json.dumps(result, ensure_ascii=False))
        return 2

    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                _fail(command, "config_not_found", error=str(exc)),
                ensure_ascii=False,
            )
        )
        return 2

    ops = WinOps(config=config)
    try:
        if command == "list":
            result = ops.list_windows(app=args.app, onscreen_only=args.onscreen_only)
        elif command == "cap":
            result = ops.capture(window_id=args.window_id, out=args.out)
        elif command == "ax":
            result = ops.snapshot(
                app=args.app, surface=args.surface, max_depth=args.max_depth
            )
        elif command == "press":
            result = ops.press(
                ref=args.ref, app=args.app, name=args.name, surface=args.surface
            )
        else:  # close
            result = ops.close(
                window_id=args.id,
                title=args.title,
                app=args.app,
                force=args.force,
            )
    except WinOpsError as exc:
        result = _fail(command, exc.status)
    except Exception as exc:  # noqa: BLE001
        result = _fail(command, "error", error="%s: %s" % (type(exc).__name__, exc))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
