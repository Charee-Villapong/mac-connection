"""spec 層テスト専用の共通フィクスチャ (C-038).

仕様だけから書いた *_spec.py テストが各々再定義していたアプリ設定
辞書のダミー一式を、ここに集約する。impl 層の impl_fixtures.py とは
相互に import せず、正準値は層の独立性のために同値を別々に定義する
(意図的な重複)。

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

# SSH バリアント (B): nav 系 spec の固有値。正準 (A) に寄せない。
_SSH_LOCALHOST = {
    "host": "127.0.0.1",
    "user": "test-user",
    "identity": "/tmp/id_test_dummy",
    "control_path": "/tmp/gf-test-ctl",
    "proxy_script": "/tmp/proxy_test_dummy.sh",
}

# 素の Discord acfg (旧形式)
_BARE_DISCORD_ACFG = {
    "app_name": "Discord",
    "window_app": "Discord",
    "process": "Discord",
    "input_click": [700, 852],
    "nav_click": [174, 350],
    "recipient_aliases": ["example-dm"],
    "header_band": 110,
}

# 素の LINE acfg (旧形式)
_BARE_LINE_ACFG = {
    "app_name": "LINE",
    "window_app": "LINE",
    "process": "LINE",
    "input_click": [800, 780],
    "nav_click": [210, 160],
    "recipient_aliases": ["example-talk"],
    "header_band": 110,
}

# header_verify_crop_spec 専用: recipient_aliases が ["example-dm"] の
# LINE 版 (正準 LINE と 1 値だけ違う現行値をそのまま保持する)。
_BARE_LINE_ACFG_VERIFY_CROP = {
    "app_name": "LINE",
    "window_app": "LINE",
    "process": "LINE",
    "input_click": [800, 780],
    "nav_click": [210, 160],
    "recipient_aliases": ["example-dm"],
    "header_band": 110,
}

# header 系 make_cfg の 3 宛先 (テスト花子 / テスト太郎 / テスト次郎)
_HEADER_RECIPIENTS = {
    "テスト花子": {"aliases": ["テスト花子", "花子"], "nav_click": [210, 160]},
    "テスト太郎": {"aliases": ["テスト太郎", "太郎"], "nav_click": [210, 232]},
    "テスト次郎": {"aliases": ["テスト次郎", "次郎"], "nav_click": [210, 304]},
}


def make_ssh():
    """正準 SSH (A) の新しい辞書を返す。"""
    return copy.deepcopy(_SSH)


def make_ssh_localhost():
    """SSH バリアント (B) の新しい辞書を返す。"""
    return copy.deepcopy(_SSH_LOCALHOST)


def make_bare_discord_acfg():
    """素の Discord acfg (旧形式) の新しい辞書を返す。"""
    return copy.deepcopy(_BARE_DISCORD_ACFG)


def make_bare_line_acfg():
    """素の LINE acfg (旧形式) の新しい辞書を返す。"""
    return copy.deepcopy(_BARE_LINE_ACFG)


def make_bare_line_acfg_verify_crop():
    """BARE_LINE_ACFG (example-dm 版) の新しい辞書を返す。"""
    return copy.deepcopy(_BARE_LINE_ACFG_VERIFY_CROP)


def load_example():
    """config.example.json を読んで返す (毎回新規読み込み)。

    example の値自体がテスト対象のため、リテラル化せず現行方式
    (ファイル読み) を維持する。
    """
    return json.loads(
        (_GUI_FLOW_DIR / "config.example.json").read_text(encoding="utf-8")
    )


def make_cfg_header_multi(tunables=None):
    """3 宛先 (テスト花子/太郎/次郎) を持つ header 系 cfg。

    現行の各 make_cfg と同じく、recipients 辞書オブジェクトは
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


def make_cfg_example_based():
    """example の apps / tunables を正として複製し、宛先だけ差し替えた cfg。

    (header_discord_spec の make_cfg と同内容)
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
        "てすとch": {
            "aliases": ["てすとch"],
            "nav_click": [174, 350],
        },
        "テスト太郎": {"aliases": ["テスト太郎"], "nav_click": [210, 232]},
    }
    cfg["apps"]["discord"]["default_recipient"] = "てすとch"
    cfg["apps"]["line"]["recipients"] = {
        "山田花": {"aliases": ["山田花"], "nav_click": [210, 160]},
    }
    cfg["apps"]["line"]["default_recipient"] = "山田花"
    return cfg


def app_cfg(label, default_id, ratio=None):
    """再学習系の単一宛先 app cfg。

    ratio が None でないときだけ talk_list_width_ratio を付ける
    (header_prefix_spec の _app_cfg は ratio 無し、talk_list_ratio_spec
    の _app_cfg は ratio ありで、現行の戻り値と一致する)。
    """
    cfg = {
        "app_name": label,
        "window_app": label,
        "process": label,
        "input_click": [800, 780] if label == "LINE" else [700, 852],
        "header_band": 110,
        "default_recipient": default_id,
        "recipients": {
            default_id: {"aliases": [default_id], "nav_click": [100, 120]},
        },
    }
    if ratio is not None:
        cfg["talk_list_width_ratio"] = ratio
    return cfg


def make_cfg_nav_home(saved_nav, home_click):
    """nav_home_spec の cfg (Discord 単独・input_click [700, 780]・SSH は B)。"""
    app = {
        "app_name": "Discord",
        "window_app": "Discord",
        "process": "Discord",
        "input_click": [700, 780],
        "default_recipient": "test-hanako",
        "recipients": {
            "test-hanako": {
                "aliases": ["テスト花子"],
                "nav_click": list(saved_nav),
            }
        },
        "recipient_aliases": ["テスト花子"],
        "talk_list_width_ratio": 0.27,
    }
    if home_click is not None:
        app["home_click"] = list(home_click)
    return {
        "ssh": make_ssh_localhost(),
        "apps": {"discord": app},
    }


def make_cfg_nav_scrutiny(saved_nav):
    """nav_scrutiny_spec の cfg (LINE 単独・SSH は B)。"""
    saved = list(saved_nav)
    return {
        "ssh": make_ssh_localhost(),
        "apps": {
            "line": {
                "app_name": "LINE",
                "window_app": "LINE",
                "process": "LINE",
                "input_click": [800, 780],
                "nav_click": list(saved),
                "default_recipient": "test-hanako",
                "recipients": {
                    "test-hanako": {
                        "aliases": ["テスト花子"],
                        "nav_click": list(saved),
                    }
                },
                "recipient_aliases": ["テスト花子"],
                "talk_list_width_ratio": 0.27,
            }
        },
    }


def line_acfg(aliases=None, **overrides):
    """recipient_header_spec の LINE acfg (素の LINE acfg + overrides)。"""
    acfg = make_bare_line_acfg()
    acfg["recipient_aliases"] = list(aliases) if aliases else ["テスト花子"]
    acfg.update(overrides)
    return acfg


def discord_acfg(aliases=None, **overrides):
    """recipient_header_spec の Discord acfg。

    現行値のまま座標は LINE の値 (input_click [800, 780] /
    nav_click [210, 160])。正準 Discord 値には寄せない。
    """
    acfg = {
        "app_name": "Discord",
        "window_app": "Discord",
        "process": "Discord",
        "input_click": [800, 780],
        "nav_click": [210, 160],
        "recipient_aliases": list(aliases) if aliases else ["テスト花子"],
        "header_band": 110,
    }
    acfg.update(overrides)
    return acfg


def make_cfg_recipients():
    """宛先を 2 件ずつ持つ新形式 config (recipients_spec の make_cfg)。"""
    return {
        "ssh": make_ssh(),
        "apps": {
            "discord": {
                "app_name": "Discord",
                "window_app": "Discord",
                "process": "Discord",
                "input_click": [700, 852],
                "header_band": 110,
                "learned_frame": [0, 30, 1440, 870],
                "default_recipient": "alice",
                "recipients": {
                    "alice": {"aliases": ["Alice"], "nav_click": [10, 20]},
                    "bob": {"aliases": ["Bob", "bobby"], "nav_click": [30, 40]},
                },
            },
            "line": {
                "app_name": "LINE",
                "window_app": "LINE",
                "process": "LINE",
                "input_click": [800, 780],
                "header_band": 110,
                "learned_frame": [0, 30, 1440, 870],
                "default_recipient": "kei",
                "recipients": {
                    "kei": {"aliases": ["Kei", "ケイ"], "nav_click": [210, 160]},
                    "taro": {"aliases": ["Taro", "太郎"], "nav_click": [210, 300]},
                },
            },
        },
    }
