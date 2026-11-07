"""特性テスト (現状固定): 検閲幾何の数値 (候補 C-034 / 候補リスト候補11)。

テスト係が仕様 (候補リスト mac-connection 候補11) と公開インターフェース
経由の実測だけから作成した。実装コード (gui_flow.py 本体) は読んで
いない。候補11 の実施内容は「run_verify_checks のアンカー帯オフセット類、
parse_ocr の行ソート粒度、png_diff_ratio の画素差しきい値既定を
tunables へ移す」こと。候補リストの記載どおり run_verify_checks 本体は
既存テストが厚く特性テストを兼ねる (所在は本ファイル末尾のコメント
参照)。本ファイルは、その中でも数値そのものが固定されていなかった
箇所を実測で固定する:

  1. parse_ocr の並び順: y を 8px 粒度で丸めた行単位で並び、同じ行
     では x の昇順 (実測: y=104 と 106 は同じ行として x 順、
     y=99 と 100 も同じ行、y=100 と 104 は別行として y 順、
     y=84 と 85 も別行)。行の丸め境界そのものを代表点で固定する。
  2. png_diff_ratio の画素差しきい値: 既定引数値は 24 で、画素差は
     輝度加重 (R:G:B の効き方が異なる) で測られ、加重値が既定を
     超えた画素だけが差分として数えられる (実測の境界: グレー差
     24 は数えず 25 は数える、R のみ 83/84、G のみ 42/43、
     B のみ 227/228)。明示引数でも境界は threshold+1 で動く。
  3. run_verify_checks (bubble) の field_state 帯: 送信後に本文と
     一致する行が欄付近の帯 (実測で窓相対 y >= 720) にあると
     「欄に残っている」と判定され、帯の外 (y=710) では判定
     されない。アンカー帯オフセットの代表境界として固定する。

宛先名はすべて架空のテスト用名称である。実機にはアクセスしない。

実行: python3 -m pytest tests/test_verify_geometry_spec.py -q

既存テストが特性テストを兼ねる所在 (候補11 の本体分):
  - run_verify_checks の正常系・異常系・しきい値変数化:
    tests/test_gui_flow.py の test_verify_* 群と
    test_verify_thresholds_are_variables、test_tunables_* 群。
  - run_verify_checks の宛先 (ヘッダ領域) 判定:
    tests/test_recipient_header_spec.py。
  - png_diff_ratio の基本 (同一 0.0 / 半分 0.5 / 不正入力 None):
    tests/test_gui_flow.py の test_png_diff_ratio。
  - parse_ocr の基本と負座標: tests/test_gui_flow.py の
    test_parse_ocr_and_norm、tests/test_ocr_negative_coords_spec.py。
"""

import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gui_flow  # noqa: E402

WIN = (767, 0, 30, 1440, 870)

DISCORD_CFG = {
    "app_name": "Discord",
    "window_app": "Discord",
    "process": "Discord",
    "input_click": [700, 852],
    "nav_click": [174, 350],
    "recipient_aliases": ["example-dm"],
    "header_band": 110,
}
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
_DISCORD_BODY = [
    "[SYSTEM] AIエージェントからお知らせ",
    "これはテストです。",
    "送信はしません。",
    "※注意",
    "確認が終わったら消します。",
    "返信は不要です。",
]


def _ocr_line(x, y, text):
    return "TEXT [x=%d y=%d w=200 h=20 conf=1.00] %s" % (x, y, text)


def _discord_ocr(extra_line=None):
    lines = [_ocr_line(421, 50, "example-dm")]
    y = 460
    for ln in _DISCORD_BODY:
        lines.append(_ocr_line(454, y, ln))
        y += 24
    if extra_line is not None:
        lines.append(extra_line)
    return "\n".join(lines)


def _checks_by_name(checks):
    return {c["name"]: c["ok"] for c in checks}


# --- 1. parse_ocr の行ソート粒度 (8px) ------------------------------------------


def _order(y_a, y_b):
    """A (x=300, y=y_a) を先に入力し、B (x=100, y=y_b) との並び順を返す。"""
    text = "\n".join(
        [
            "TEXT [x=300 y=%d w=100 h=20 conf=1.00] A" % y_a,
            "TEXT [x=100 y=%d w=100 h=20 conf=1.00] B" % y_b,
        ]
    )
    return [item["text"] for item in gui_flow.parse_ocr(text)]


def test_parse_ocr_sorts_by_row_granularity_then_x():
    # 同じ行 (8px 粒度で同じ丸め) なら x の昇順になる (実測で固定)
    assert _order(104, 106) == ["B", "A"]
    assert _order(99, 100) == ["B", "A"]
    assert _order(108, 109) == ["B", "A"]
    # 別行なら y の昇順 (x が大きくても上行が先。実測で固定)
    assert _order(100, 104) == ["A", "B"]
    assert _order(84, 85) == ["A", "B"]
    assert _order(107, 108) == ["A", "B"]
    # 大きく離れれば当然 y の昇順、同 y なら x の昇順
    assert _order(100, 200) == ["A", "B"]
    text = "\n".join(
        [
            "TEXT [x=300 y=100 w=100 h=20 conf=1.00] R",
            "TEXT [x=100 y=100 w=100 h=20 conf=1.00] L",
        ]
    )
    assert [i["text"] for i in gui_flow.parse_ocr(text)] == ["L", "R"]


# --- 2. png_diff_ratio の画素差しきい値 (既定 24・輝度加重) ----------------------


def _tiny_png(rows):
    """テスト用の最小 PNG (RGB・フィルタなし)。test_gui_flow.py と同作法。"""
    h = len(rows)
    w = len(rows[0])
    raw = b""
    for row in rows:
        raw += b"\x00" + b"".join(bytes(px) for px in row)

    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


_BLACK = _tiny_png([[(0, 0, 0)]])


def _ratio_1px(px, **kwargs):
    return gui_flow.png_diff_ratio(_BLACK, _tiny_png([[px]]), **kwargs)


def test_png_diff_ratio_default_threshold_boundaries():
    # 既定のしきい値引数は 24 (公開シグネチャで固定)
    import inspect

    sig = inspect.signature(gui_flow.png_diff_ratio)
    assert sig.parameters["pixel_threshold"].default == 24
    # グレー (全チャンネル同値): 24 は差分に数えず、25 は数える (実測)
    assert _ratio_1px((24, 24, 24)) == 0.0
    assert _ratio_1px((25, 25, 25)) == 1.0
    # 輝度加重のため単チャンネルでは効き方が異なる (実測の境界)
    assert _ratio_1px((83, 0, 0)) == 0.0
    assert _ratio_1px((84, 0, 0)) == 1.0
    assert _ratio_1px((0, 42, 0)) == 0.0
    assert _ratio_1px((0, 43, 0)) == 1.0
    assert _ratio_1px((0, 0, 227)) == 0.0
    assert _ratio_1px((0, 0, 228)) == 1.0


def test_png_diff_ratio_explicit_threshold_moves_boundary():
    # 明示引数では境界が threshold + 1 のグレー差へ動く (実測)
    assert _ratio_1px((10, 10, 10), pixel_threshold=10) == 0.0
    assert _ratio_1px((11, 11, 11), pixel_threshold=10) == 1.0
    assert _ratio_1px((50, 50, 50), pixel_threshold=50) == 0.0
    assert _ratio_1px((51, 51, 51), pixel_threshold=50) == 1.0


# --- 3. bubble の field_state 帯の境界 ------------------------------------------


def test_bubble_field_state_band_boundary():
    # 帯の外 (y=710) に本文行が残っていても field_state は合格 (現行)
    ocr = _discord_ocr(extra_line=_ocr_line(454, 710, "これはテストです。"))
    checks = gui_flow.run_verify_checks(
        "discord", DISCORD_CFG, ocr, WIN, DISCORD_TEXT, "bubble"
    )
    assert _checks_by_name(checks)["field_state"] is True
    # 帯の中 (y=720) に残っていると「欄に本文が残っている」で不合格 (現行)
    ocr = _discord_ocr(extra_line=_ocr_line(454, 720, "これはテストです。"))
    checks = gui_flow.run_verify_checks(
        "discord", DISCORD_CFG, ocr, WIN, DISCORD_TEXT, "bubble"
    )
    assert _checks_by_name(checks)["field_state"] is False
