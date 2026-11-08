"""win_ops の spec 層テスト専用フィクスチャ.

仕様 (tools/win_ops/SPEC.md) と、テスト係へのモジュール契約補足
(transport の run_queue 返却形式・extras の対応表) だけから作る。
実装コードは存在しない前提で、実在の個人名・宛先・接続情報の実値は
含めない (アプリ名は ターミナル / メモ / 設定 等の一般名のみ)。

ScreenCheck list の行形式は Mac 側ツール (ScreenCheck list /
sck.swift) の実出力形式に合わせる:
  win id=<id> app=<app> title=<title> frame=<x>,<y> <w>x<h> onscreen=<bool>
cap の stdout は "wrote <W>x<H> -> <path>" を含む形式にする。
"""

import base64
import copy
import json

# --- 設定 -----------------------------------------------------------------

SESSION_NAME = "winops-test-session"
TERMINAL_APP = "ターミナル"
SHELL_COMMANDS = ["zsh", "bash", "login", "-zsh", "-bash", "sh", "-sh"]


def make_config(capture_dir):
    """capture_dir だけ差し替えたテスト用 config を返す.

    値の置き場所 (トップレベル / tunables) は仕様が細部まで固定して
    いないため、capture_dir とセッション名は両方に同じ値を置く。
    閉じるボタンのオフセット 14 / 許容差 12 は仕様の既定値を
    「設定値として」明示的に渡す。
    """
    capture = str(capture_dir)
    return {
        "capture_dir": capture,
        "session": SESSION_NAME,
        "session_name": SESSION_NAME,
        "screencheck_path": "/tmp/fake-tools/ScreenCheck",
        "agent_desktop_path": "/tmp/fake-tools/agent-desktop",
        "tunables": {
            "capture_dir": capture,
            "session": SESSION_NAME,
            "session_name": SESSION_NAME,
            "close_button_offset_px": 14,
            "close_button_tolerance_px": 12,
            "terminal_apps": [TERMINAL_APP, "Terminal"],
            "shell_commands": list(SHELL_COMMANDS),
        },
    }


# --- transport スタブ -------------------------------------------------------


class StubTransport:
    """契約補足どおりの run_queue を持つ transport スタブ.

    handler(script_body, name, extras) -> dict が返却 dict を決める。
    返却 dict は gui_flow と同形式 (結果キーがトップレベル、
    extras の中身は _files のサフィックス→文字列、_ssh_rc)。
    呼び出しは self.calls に全て記録する。
    """

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def run_queue(self, script_body, name, extras=()):
        self.calls.append(
            {"script": script_body, "name": name, "extras": tuple(extras)}
        )
        result = self._handler(script_body, name, tuple(extras))
        assert isinstance(result, dict), "handler は dict を返すこと"
        return result

    def scripts(self):
        return [c["script"] for c in self.calls]

    def scripts_containing(self, needle):
        return [s for s in self.scripts() if needle in s]


def queue_ok(files=None, **keys):
    """正常なキュー返却 dict を作る。"""
    result = {"status": "ok", "_files": dict(files or {}), "_ssh_rc": 0}
    result.update(keys)
    return result


def queue_timeout():
    """キュータイムアウトの返却 dict (契約補足: status で表す)。"""
    return {"status": "queue_timeout", "_files": {}, "_ssh_rc": 0}


# --- ScreenCheck list の生出力 ---------------------------------------------

# 解析テスト用: 日本語アプリ名・空白を含むタイトルとアプリ名・
# 負座標・onscreen=false・空タイトル・無関係行を混ぜたもの。
LIST_TEXT_BASIC = (
    "\n".join(
        [
            "win id=101 app=ターミナル title=作業 ログ — ローカル frame=-120,40 900x600 onscreen=true",
            "win id=102 app=メモ title=買い物 リスト frame=10,-20 640x480 onscreen=false",
            "win id=103 app=システム設定 title=Wi-Fi と ネットワーク frame=200,100 800x500 onscreen=true",
            "win id=104 app=テキストエディット title= frame=0,25 700x400 onscreen=true",
            "win id=105 app=ノート ブック title=会議 メモ frame=50,60 500x300 onscreen=true",
            "SCK_DONE",
            "",
            "windows id=999 app=ダミー title=紛らわしい行 frame=0,0 100x100 onscreen=true",
            "id=106 app=メモ title=win で始まらない行 frame=0,0 100x100 onscreen=true",
        ]
    )
    + "\n"
)

# list コマンドのフィルタ検証用 (メモ と メモ帳 は別アプリ)。
LIST_TEXT_FILTER = (
    "\n".join(
        [
            "win id=201 app=ターミナル title=作業 frame=0,25 900x600 onscreen=true",
            "win id=202 app=メモ title=買い物 frame=10,30 640x480 onscreen=true",
            "win id=203 app=メモ帳 title=下書き frame=20,40 640x480 onscreen=false",
            "win id=204 app=システム設定 title=一般 frame=30,50 800x500 onscreen=false",
        ]
    )
    + "\n"
)

LIST_TEXT_ERROR = "ERROR shareable content: テスト用の取得失敗\n"

# close 検証用。
LIST_TEXT_TERMINAL = (
    "win id=301 app=ターミナル title=作業 — zsh frame=100,50 800x600 onscreen=true\n"
    "win id=302 app=メモ title=買い物 frame=10,30 640x480 onscreen=true\n"
)
LIST_TEXT_AFTER_TERMINAL_CLOSED = (
    "win id=302 app=メモ title=買い物 frame=10,30 640x480 onscreen=true\n"
)
LIST_TEXT_MEMO_ONLY = LIST_TEXT_AFTER_TERMINAL_CLOSED
LIST_TEXT_AFTER_MEMO_CLOSED = (
    "win id=301 app=ターミナル title=作業 — zsh frame=100,50 800x600 onscreen=true\n"
)
LIST_TEXT_DUPLICATE_TITLE = (
    "win id=311 app=メモ title=買い物 frame=10,30 640x480 onscreen=true\n"
    "win id=312 app=メモ title=買い物 frame=400,30 640x480 onscreen=true\n"
    "win id=313 app=ターミナル title=作業 frame=0,25 900x600 onscreen=true\n"
)


def windows_basic():
    """LIST_TEXT_BASIC の win 行 5 件に対応する解析済み windows。"""
    return copy.deepcopy(
        [
            {
                "id": 101,
                "app": "ターミナル",
                "title": "作業 ログ — ローカル",
                "frame": {"x": -120, "y": 40, "width": 900, "height": 600},
                "onscreen": True,
            },
            {
                "id": 102,
                "app": "メモ",
                "title": "買い物 リスト",
                "frame": {"x": 10, "y": -20, "width": 640, "height": 480},
                "onscreen": False,
            },
            {
                "id": 103,
                "app": "システム設定",
                "title": "Wi-Fi と ネットワーク",
                "frame": {"x": 200, "y": 100, "width": 800, "height": 500},
                "onscreen": True,
            },
            {
                "id": 104,
                "app": "テキストエディット",
                "title": "",
                "frame": {"x": 0, "y": 25, "width": 700, "height": 400},
                "onscreen": True,
            },
            {
                "id": 105,
                "app": "ノート ブック",
                "title": "会議 メモ",
                "frame": {"x": 50, "y": 60, "width": 500, "height": 300},
                "onscreen": True,
            },
        ]
    )


def windows_for_resolve():
    """resolve_window 検証用 (同名タイトル 2 件を含む)。"""
    return copy.deepcopy(
        [
            {
                "id": 301,
                "app": "ターミナル",
                "title": "作業 — zsh",
                "frame": {"x": 100, "y": 50, "width": 800, "height": 600},
                "onscreen": True,
            },
            {
                "id": 311,
                "app": "メモ",
                "title": "買い物",
                "frame": {"x": 10, "y": 30, "width": 640, "height": 480},
                "onscreen": True,
            },
            {
                "id": 312,
                "app": "メモ帳",
                "title": "買い物",
                "frame": {"x": 400, "y": 30, "width": 640, "height": 480},
                "onscreen": True,
            },
        ]
    )


# --- cap 用 -----------------------------------------------------------------

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"win-ops-spec-test-payload"
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")
CAP_META_OK = "wrote 1600x1200 -> /tmp/win-ops-cap-301.png"
CAP_META_NOT_FOUND = "ERROR target window 999 not found"


# --- AX (agent-desktop snapshot) 用 -----------------------------------------


def make_element(ref, role, name, bounds, states=(), children=()):
    """AX ツリーの要素 1 件。bounds と frame には同じ境界を持たせる。"""
    x, y, w, h = bounds
    box = {"x": x, "y": y, "width": w, "height": h}
    return {
        "ref": ref,
        "role": role,
        "name": name,
        "states": list(states),
        "bounds": dict(box),
        "frame": dict(box),
        "children": list(children),
    }


def make_snapshot(tree, snapshot_id="snap-test-1", ok=True):
    """agent-desktop snapshot の JSON オブジェクト (フィクスチャ形)。"""
    return {"ok": ok, "snapshot_id": snapshot_id, "tree": tree}


def ax_json(tree, **kwargs):
    """extras ".ax.json" に入れる snapshot stdout (JSON 文字列)。"""
    return json.dumps(make_snapshot(tree, **kwargs), ensure_ascii=False)


def click_json(ok=True):
    """extras ".click.json" に入れる click stdout (JSON 文字列)。"""
    payload = {"ok": ok}
    if not ok:
        payload["error"] = "テスト用の click 失敗"
    return json.dumps(payload)


# press --name 検証用の木: 「設定を開く」は 1 件だけ現れる。
PRESS_TREE_UNIQUE = make_element(
    "@snap-test-1:e1",
    "window",
    "メモ",
    (10, 30, 640, 480),
    children=[
        make_element(
            "@snap-test-1:e2",
            "group",
            "",
            (10, 30, 640, 480),
            children=[
                make_element(
                    "@snap-test-1:e7", "button", "設定を開く", (20, 40, 120, 30)
                ),
                make_element(
                    "@snap-test-1:e8", "button", "キャンセル", (160, 40, 120, 30)
                ),
            ],
        ),
    ],
)

# 同名 2 件。
PRESS_TREE_AMBIGUOUS = make_element(
    "@snap-test-1:e1",
    "window",
    "メモ",
    (10, 30, 640, 480),
    children=[
        make_element("@snap-test-1:e3", "button", "開く", (20, 40, 100, 30)),
        make_element("@snap-test-1:e4", "button", "開く", (140, 40, 100, 30)),
    ],
)

# 一意だが disabled。
PRESS_TREE_DISABLED = make_element(
    "@snap-test-1:e1",
    "window",
    "メモ",
    (10, 30, 640, 480),
    children=[
        make_element(
            "@snap-test-1:e5",
            "button",
            "設定を開く",
            (20, 40, 120, 30),
            states=["disabled"],
        ),
    ],
)

# iter / find 検証用の木 (合計 6 ノード)。
ELEMENT_TREE = make_element(
    "@snap-test-1:e1",
    "window",
    "設定",
    (0, 0, 800, 600),
    children=[
        make_element(
            "@snap-test-1:e2",
            "group",
            "サイドバー",
            (0, 0, 200, 600),
            children=[
                make_element("@snap-test-1:e3", "button", "一般", (10, 10, 100, 30)),
                make_element(
                    "@snap-test-1:e4", "button", "設定を開く", (10, 50, 100, 30)
                ),
            ],
        ),
        make_element("@snap-test-1:e5", "button", "開く", (300, 10, 100, 30)),
        make_element("@snap-test-1:e6", "button", "開く", (300, 50, 100, 30)),
    ],
)

# close 検証用: 窓フレーム (100,50,800,600) の左上 + (14,14) が
# 閉じるボタンの中心 (114,64) になる配置 (ボタンは 14x14)。
TARGET_FRAME = {"x": 100, "y": 50, "width": 800, "height": 600}
CLOSE_BUTTON_BOUNDS = (107, 57, 14, 14)


def close_tree(button_bounds_list, extra_children=()):
    """閉じるボタン候補を button_bounds_list の数だけ持つ窓の木。"""
    children = [
        make_element(
            "@snap-test-1:e%d" % (10 + i),
            "button",
            "",
            bounds,
        )
        for i, bounds in enumerate(button_bounds_list)
    ]
    children.extend(extra_children)
    return make_element(
        "@snap-test-1:e1",
        "window",
        "対象の窓",
        (
            TARGET_FRAME["x"],
            TARGET_FRAME["y"],
            TARGET_FRAME["width"],
            TARGET_FRAME["height"],
        ),
        children=children,
    )


CLOSE_TREE_OK = close_tree([CLOSE_BUTTON_BOUNDS])
CLOSE_TREE_NO_BUTTON = close_tree(
    [(700, 57, 14, 14)]  # 右上寄り: 閉じるボタンの位置ではない
)
CLOSE_TREE_TWO_BUTTONS = close_tree([CLOSE_BUTTON_BOUNDS, (113, 57, 14, 14)])


# --- ps 出力 (close のプロセス確認用) ----------------------------------------

PS_BUSY = "  PID COMM\n  501 login\n  502 -zsh\n  601 vim\n  602 python3\n"
PS_SHELL_ONLY = "  PID COMM\n  501 login\n  502 -zsh\n  503 zsh\n"
TTY_TERMINAL = "/dev/ttys003"
