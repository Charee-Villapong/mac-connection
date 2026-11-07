"""impl 層テスト専用の共通フィクスチャ (C-038).

実装側の *_impl.py テストと test_gui_flow.py が各々再定義していた
アプリ設定辞書のダミー一式を、ここに集約する。spec 層の
spec_fixtures.py とは相互に import せず、正準値は層の独立性の
ために同値を別々に定義する (意図的な重複)。

定数は直接配らず、呼ぶたびに新しい辞書 (deepcopy) を返すヘルパー
関数を正とする。モジュール内テンプレートは外から直接使わない。
"""

import copy
import json
from pathlib import Path

_GUI_FLOW_DIR = Path(__file__).resolve().parents[1]

# 正準 SSH (A)
_SSH = {
    "user": "test-user",
    "host": "192.0.2.1",
    "identity": "/tmp/id_test",
    "proxy_script": "/tmp/proxy.sh",
    "control_path": "/tmp/gf-test-ctl",
}

# 素の Discord acfg (旧形式)
_DISCORD_CFG = {
    "app_name": "Discord",
    "window_app": "Discord",
    "process": "Discord",
    "input_click": [700, 852],
    "nav_click": [174, 350],
    "recipient_aliases": ["example-dm"],
    "header_band": 110,
}

# 素の LINE acfg (旧形式)
_LINE_CFG = {
    "app_name": "LINE",
    "window_app": "LINE",
    "process": "LINE",
    "input_click": [800, 780],
    "nav_click": [210, 160],
    "recipient_aliases": ["example-talk"],
    "header_band": 110,
}

# header 系 make_cfg の 3 宛先 (テスト花子 / テスト太郎 / テスト次郎)
_HEADER_RECIPIENTS = {
    "テスト花子": {"aliases": ["テスト花子", "花子"], "nav_click": [210, 160]},
    "テスト太郎": {"aliases": ["テスト太郎", "太郎"], "nav_click": [210, 232]},
    "テスト次郎": {"aliases": ["テスト次郎", "次郎"], "nav_click": [210, 304]},
}

# recipients_impl の LINE_APP (新形式・単独)
_LINE_APP = {
    "app_name": "LINE",
    "window_app": "LINE",
    "process": "LINE",
    "input_click": [800, 780],
    "header_band": 110,
    "default_recipient": "example-default",
    "recipients": {
        "example-default": {
            "aliases": ["example-default", "example-alias"],
            "nav_click": [100, 120],
        }
    },
}


def make_ssh():
    """正準 SSH (A) の新しい辞書を返す。"""
    return copy.deepcopy(_SSH)


def make_discord_cfg():
    """素の Discord acfg (旧形式) の新しい辞書を返す。"""
    return copy.deepcopy(_DISCORD_CFG)


def make_line_cfg():
    """素の LINE acfg (旧形式) の新しい辞書を返す。"""
    return copy.deepcopy(_LINE_CFG)


def make_full_cfg():
    """{"ssh": A, "apps": {discord, line}} の新しい辞書を返す。"""
    return {
        "ssh": make_ssh(),
        "apps": {"discord": make_discord_cfg(), "line": make_line_cfg()},
    }


def load_example():
    """config.example.json を読んで返す (毎回新規読み込み)。

    example の値自体がテスト対象のため、リテラル化せず現行方式
    (ファイル読み) を維持する。
    """
    return json.loads(
        (_GUI_FLOW_DIR / "config.example.json").read_text(encoding="utf-8")
    )


def make_cfg_example_based_discord_only():
    """example を複製し、discord 側だけ宛先を差し替えた cfg。

    (header_verify_impl の make_cfg と同内容。line 側の
    recipients / default は example のまま差し替えない)
    """
    example = load_example()
    cfg = {
        "ssh": make_ssh(),
        "tunables": copy.deepcopy(example.get("tunables", {})),
        "apps": {
            "discord": copy.deepcopy(example["apps"]["discord"]),
            "line": copy.deepcopy(example["apps"]["line"]),
        },
    }
    cfg["apps"]["discord"]["recipients"] = {
        "てすとch": {"aliases": ["てすとch"], "nav_click": [174, 350]},
        "テスト太郎": {"aliases": ["テスト太郎"], "nav_click": [210, 232]},
    }
    cfg["apps"]["discord"]["default_recipient"] = "てすとch"
    return cfg


def make_cfg_header_multi(tunables=None):
    """3 宛先 (テスト花子/太郎/次郎) を持つ header 系 cfg。

    現行の make_cfg と同じく、recipients 辞書オブジェクトは
    line / discord の両アプリで共有する。
    """
    recipients = copy.deepcopy(_HEADER_RECIPIENTS)
    cfg = {
        "ssh": make_ssh(),
        "apps": {
            "line": {
                "app_name": "LINE",
                "window_app": "LINE",
                "process": "LINE",
                "input_click": [800, 780],
                "header_band": 110,
                "default_recipient": "テスト花子",
                "recipients": recipients,
            },
            "discord": {
                "app_name": "Discord",
                "window_app": "Discord",
                "process": "Discord",
                "input_click": [700, 852],
                "header_band": 110,
                "default_recipient": "テスト花子",
                "recipients": recipients,
            },
        },
    }
    if tunables:
        cfg["tunables"] = tunables
    return cfg


def make_cfg_nav_scrutiny(app, saved_nav, ratio):
    """nav_scrutiny_impl の cfg。

    現行のまま、app 引数の文字列 (小文字) をそのまま app_name 等に
    入れ、input_click は両アプリ共通の固有値 [700, 800]。
    """
    return {
        "ssh": make_ssh(),
        "apps": {
            app: {
                "app_name": app,
                "window_app": app,
                "process": app,
                "input_click": [700, 800],
                "talk_list_width_ratio": ratio,
                "default_recipient": "hanako",
                "recipients": {
                    "hanako": {
                        "aliases": ["テスト花子"],
                        "nav_click": list(saved_nav),
                    }
                },
            }
        },
    }


def make_line_app():
    """LINE_APP (新形式) の新しい辞書を返す。"""
    return copy.deepcopy(_LINE_APP)
