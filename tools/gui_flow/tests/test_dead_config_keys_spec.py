"""特性テスト (現状固定): 死んでいる config キー header_band /
field_from_bottom の無影響性 (候補 C-035 / 候補リスト候補12)。

テスト係が仕様 (候補リスト mac-connection 候補12 と config.example.json)
だけから作成した。実装コード (gui_flow.py 本体) は読んでいない。
候補12 の実施内容は「config.example.json の各アプリ配下の2キー
(ヘッダ帯の高さ系 = header_band、入力欄の下端距離系 =
field_from_bottom) を config.example.json とテスト用ダミー設定から
削除する」こと。本テストは削除の前後で挙動が変わらないことを、
公開インターフェース経由で固定する:

  1. ダミー設定に2キーが (例示値 / 欠落 / 異常値 / 0) のいずれで
     あっても、run_verify_checks の結果 (全チェック dict) が
     完全に同一である (discord / line 両方)。
  2. 同じく生成スクリプト (build_verify_script /
     build_input_script) と careful_cfg の解決結果が同一である。
  3. config.example.json を実際に読み込む経路 (load_config) でも、
     2キーを除去したコピーと元とで tunables 解決・アプリ別
     careful_cfg・生成スクリプトが同一である。除去後も
     load_config が成功することが、削除実施後の固定になる。

宛先名はすべて架空のテスト用名称である。実機にはアクセスしない。

実行: python3 -m pytest tests/test_dead_config_keys_spec.py -q
"""

import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gui_flow  # noqa: E402

GUI_FLOW_DIR = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads(
    (GUI_FLOW_DIR / "config.example.json").read_text(encoding="utf-8")
)

DEAD_KEYS = ("header_band", "field_from_bottom")

WIN = (767, 0, 30, 1440, 870)

DISCORD_TEXT = (
    "```\n"
    "[SYSTEM] AIエージェントからお知らせ\n"
    "これはテストです。\n"
    "送信はしません。\n"
    "※注意\n"
    "確認が終わったら消します。\n"
    "返信は不要です。\n"
    "```"
)
LINE_TEXT = (
    "【AIエージェントからのお知らせ】\n"
    "これはテストです。\n"
    "送信はしません。\n"
    "確認が終わったら消します。"
)


def _ocr_line(x, y, text):
    return "TEXT [x=%d y=%d w=200 h=20 conf=1.00] %s" % (x, y, text)


DISCORD_OCR = "\n".join(
    [_ocr_line(421, 50, "example-dm")]
    + [
        _ocr_line(454, 460 + 24 * i, ln)
        for i, ln in enumerate(
            [
                "[SYSTEM] AIエージェントからお知らせ",
                "これはテストです。",
                "送信はしません。",
                "※注意",
                "確認が終わったら消します。",
                "返信は不要です。",
            ]
        )
    ]
)
LINE_OCR = "\n".join(
    [
        _ocr_line(454, 130, "example-talk"),
        _ocr_line(454, 460, "【AIエージェントからのお知らせ】"),
        _ocr_line(454, 484, "これはテストです。"),
        _ocr_line(454, 508, "送信はしません。"),
        _ocr_line(454, 532, "確認が終わったら消します。"),
    ]
)


def _discord_cfg(**extra):
    cfg = {
        "app_name": "Discord",
        "window_app": "Discord",
        "process": "Discord",
        "input_click": [700, 852],
        "nav_click": [174, 350],
        "recipient_aliases": ["example-dm"],
    }
    cfg.update(extra)
    return cfg


def _line_cfg(**extra):
    cfg = {
        "app_name": "LINE",
        "window_app": "LINE",
        "process": "LINE",
        "input_click": [800, 780],
        "nav_click": [210, 160],
        "recipient_aliases": ["example-talk"],
    }
    cfg.update(extra)
    return cfg


# config.example.json の現行例示値 (discord: 110/90、line: 110/160)
_EXAMPLE_VALUES = {
    "discord": {"header_band": 110, "field_from_bottom": 90},
    "line": {"header_band": 110, "field_from_bottom": 160},
}


def _variants(app):
    """2キーの置き方 4 通り (例示値 / 欠落 / 異常値 / 0)。"""
    builders = {"discord": _discord_cfg, "line": _line_cfg}
    build = builders[app]
    return {
        "example_values": build(**_EXAMPLE_VALUES[app]),
        "absent": build(),
        "absurd_values": build(header_band=9999, field_from_bottom=1),
        "zero_values": build(header_band=0, field_from_bottom=0),
    }


# --- 1. run_verify_checks の結果が2キーに影響されない -------------------------


def test_dead_keys_do_not_change_verify_checks_discord():
    results = {
        label: gui_flow.run_verify_checks(
            "discord", cfg, DISCORD_OCR, WIN, DISCORD_TEXT, "field"
        )
        for label, cfg in _variants("discord").items()
    }
    reference = results["example_values"]
    # 前提: 例示値のケース自体が正常系 (全チェック合格) であること
    assert all(c["ok"] for c in reference)
    for label, checks in results.items():
        assert checks == reference, f"{label} で検閲結果が変わった"


def test_dead_keys_do_not_change_verify_checks_line():
    results = {
        label: gui_flow.run_verify_checks(
            "line", cfg, LINE_OCR, WIN, LINE_TEXT, "field"
        )
        for label, cfg in _variants("line").items()
    }
    reference = results["example_values"]
    assert all(c["ok"] for c in reference)
    for label, checks in results.items():
        assert checks == reference, f"{label} で検閲結果が変わった"


# --- 2. 生成スクリプトと careful_cfg が2キーに影響されない ---------------------


def test_dead_keys_do_not_change_generated_scripts_or_careful_cfg():
    variants = _variants("discord")
    reference = variants["example_values"]
    ref_verify = gui_flow.build_verify_script("gf-x.sh", "discord", reference)
    ref_input = gui_flow.build_input_script(
        "gf-x.sh", "discord", reference, DISCORD_TEXT
    )
    ref_careful = gui_flow.careful_cfg("discord", reference)
    for label, cfg in variants.items():
        assert (
            gui_flow.build_verify_script("gf-x.sh", "discord", cfg) == ref_verify
        ), f"{label} で verify スクリプトが変わった"
        assert (
            gui_flow.build_input_script("gf-x.sh", "discord", cfg, DISCORD_TEXT)
            == ref_input
        ), f"{label} で input スクリプトが変わった"
        assert (
            gui_flow.careful_cfg("discord", cfg) == ref_careful
        ), f"{label} で careful_cfg が変わった"


# --- 3. config.example.json の実読込でも除去前後が同一 -------------------------


def _load_from_dict(tmp_path, monkeypatch, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GUI_FLOW_CONFIG", str(path))
    return gui_flow.load_config()


def test_example_config_without_dead_keys_resolves_the_same(
    tmp_path, monkeypatch
):
    original = _load_from_dict(
        tmp_path, monkeypatch, "cfg-original.json", copy.deepcopy(EXAMPLE)
    )
    stripped_data = copy.deepcopy(EXAMPLE)
    for app_cfg in stripped_data["apps"].values():
        for key in DEAD_KEYS:
            app_cfg.pop(key, None)
    stripped = _load_from_dict(
        tmp_path, monkeypatch, "cfg-stripped.json", stripped_data
    )

    # 除去したコピーでも読込が成功し、全体の調整値解決が同一
    assert gui_flow.tunables(stripped) == gui_flow.tunables(original)
    for app in ("discord", "line"):
        # アプリ別 careful 解決と生成スクリプトが除去前後で同一
        assert gui_flow.careful_cfg(
            app, stripped["apps"][app]
        ) == gui_flow.careful_cfg(app, original["apps"][app])
        assert gui_flow.build_verify_script(
            "gf-x.sh", app, stripped["apps"][app]
        ) == gui_flow.build_verify_script(
            "gf-x.sh", app, original["apps"][app]
        )
