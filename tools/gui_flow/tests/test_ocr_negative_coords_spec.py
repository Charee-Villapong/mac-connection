"""仕様テスト (再現テスト): 負の座標を持つ OCR 行が parse_ocr で
捨てられる欠陥 (フェーズ4 追補 / 第2の欠陥).

実装コード (gui_flow.py) を読まず、仕様書
(~/workspace/gui-flow-header-crop-fix/DEFINITION.md の末尾「追補
(2026-11-06 実働テストで発見した第2の欠陥)」) だけから書いた
再現テスト。既存の spec テスト (test_header_crop_spec.py 等) の
流儀 (OCR 行フォーマット、公開関数の呼び方) に倣う。

背景 (仕様書の追補より):
  実働テストで、新クロップ (100pt) のヘッダ切り出し OCR は名前
  「田中葵小心」を正しく読めていたのに、送信前のヘッダ判定が
  「ヘッダ領域に文字列なし」で 3 回とも UNREADABLE となり停止した。
  原因は、OCR 項目の座標が x=-0 (切り出し左端に接する文字に Vision
  が付ける負の原点) で、parse_ocr が座標に負号を許さないため、
  該当行が丸ごと捨てられていたこと。

担保する仕様 (追補の「追加完了条件 1」):
  負の座標 (x=-0、y の負値を含む) を持つ OCR 行が parse_ocr で
  捨てられず、項目として返ること。その項目からヘッダ判定が
  MATCH になること。

宛先名について: 既存 spec テストは架空の人名を使うが、本追補は
実働の証跡そのものを対象とするため、証跡ファイル
(gf-line-verify-1793931309204-5.sh-header-ocr.txt) の実文字列
「田中葵小心」と宛先「田中葵」を使う。ただしテスト自体はファイルに
依存せず、文字列リテラルで完結させる。

対照ケースとして、正の座標だけの従来形式も従来通り parse できる
ことを併せて担保する。

実行: python3 -m pytest tests/test_ocr_negative_coords_spec.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gui_flow  # noqa: E402

# 証跡ファイルの中身をそのまま文字列リテラルにしたもの。
# 1 行目は OCR ファイルのヘッダ行 (TEXT 行ではない)。
EVIDENCE_OCR = (
    "image_px=2073x200 scale=2.0 texts=2\n"
    "TEXT [x=-0 y=74 w=95 h=22 conf=0.30] 田中葵小心\n"
    "TEXT [x=-0 y=21 w=9 h=13 conf=0.30] d1"
)

TARGET = "田中葵"  # 証跡の宛先本人
TARGET_ALIASES = ["田中葵", "葵"]


def texts_of(items):
    return sorted(item["text"] for item in items)


# ---------------------------------------------------------------------------
# parse_ocr: 負の座標の行が捨てられないこと (追加完了条件 1・前半)
# ---------------------------------------------------------------------------


def test_parse_ocr_keeps_line_with_negative_zero_x():
    """仕様 (追補・追加完了条件 1): 負の座標 (x=-0) を持つ OCR 行が
    parse_ocr で捨てられず、項目として返ること。

    証跡の実文字列 (田中葵小心、x=-0) をそのまま使う。
    """
    items = gui_flow.parse_ocr(
        "TEXT [x=-0 y=74 w=95 h=22 conf=0.30] 田中葵小心"
    )

    assert len(items) == 1
    item = items[0]
    assert item["text"] == "田中葵小心"
    # 座標は数値として保持される (-0 は 0 と等しい)
    assert item["x"] == 0
    assert item["y"] == 74
    assert item["w"] == 95
    assert item["h"] == 22
    assert item["conf"] == 0.30


def test_parse_ocr_keeps_line_with_negative_x_value():
    """仕様 (追補・追加完了条件 1): 負の座標 (x=-0、y の負値を含む)
    を持つ OCR 行が parse_ocr で捨てられず、項目として返ること。

    x が 0 でない負値 (-12) の場合も同様に返り、値が保持される。
    """
    items = gui_flow.parse_ocr(
        "TEXT [x=-12 y=74 w=95 h=22 conf=0.30] 田中葵小心"
    )

    assert len(items) == 1
    assert items[0]["text"] == "田中葵小心"
    assert items[0]["x"] == -12
    assert items[0]["y"] == 74


def test_parse_ocr_keeps_line_with_negative_y():
    """仕様 (追補・追加完了条件 1): 負の座標 (x=-0、y の負値を含む)
    を持つ OCR 行が parse_ocr で捨てられず、項目として返ること。

    y が負値 (-5) の行も捨てられず、値が保持される。
    """
    items = gui_flow.parse_ocr(
        "TEXT [x=10 y=-5 w=95 h=22 conf=0.30] 田中葵小心"
    )

    assert len(items) == 1
    assert items[0]["text"] == "田中葵小心"
    assert items[0]["x"] == 10
    assert items[0]["y"] == -5


def test_parse_ocr_evidence_shaped_text_returns_all_items():
    """仕様 (追補・追加完了条件 1): 負の座標 (x=-0、y の負値を含む)
    を持つ OCR 行が parse_ocr で捨てられず、項目として返ること。

    証跡ファイル全体の形 (ヘッダ行 + TEXT 行 2 件、どちらも x=-0)
    を渡しても、TEXT 行が 2 件とも項目として返る (ヘッダ行は
    従来通り対象外)。
    """
    items = gui_flow.parse_ocr(EVIDENCE_OCR)

    assert len(items) == 2
    assert texts_of(items) == ["d1", "田中葵小心"]


# ---------------------------------------------------------------------------
# ヘッダ判定: 負座標の項目から MATCH になること (追加完了条件 1・後半)
# ---------------------------------------------------------------------------


def test_header_attempt_matches_from_negative_coordinate_items():
    """仕様 (追補・追加完了条件 1): 負の座標を持つ OCR 行が
    parse_ocr で捨てられず項目として返り、その項目からヘッダ判定が
    MATCH になること。

    証跡の項目群 (田中葵小心 + d1) から run 群を作り、宛先「田中葵」
    で判定すると、「田中葵小心」が前方一致 (余り 2 文字) で MATCH
    になる (判定ルール自体は従来通り)。
    """
    items = gui_flow.parse_ocr(EVIDENCE_OCR)
    runs = gui_flow.text_runs(items, 10)

    run_texts = [text for _center, text in runs]
    assert "田中葵小心" in run_texts

    att = gui_flow.classify_header_attempt(
        runs, TARGET_ALIASES, [], prefix_max_extra=2
    )
    assert att["classification"] == "MATCH"
    assert att["matched"] == TARGET


# ---------------------------------------------------------------------------
# 対照ケース: 正の座標だけの従来形式は従来通り
# ---------------------------------------------------------------------------


def test_parse_ocr_positive_coordinates_unchanged():
    """対照ケース: 正の座標だけの従来形式の OCR テキストは、従来通り
    parse_ocr で全件 parse でき、座標・文字列も従来通り返る。

    証跡と同じ文字列・寸法で x だけを正の値にしたものを使う。
    """
    items = gui_flow.parse_ocr(
        "TEXT [x=0 y=74 w=95 h=22 conf=0.30] 田中葵小心\n"
        "TEXT [x=0 y=21 w=9 h=13 conf=0.30] d1"
    )

    assert len(items) == 2
    assert texts_of(items) == ["d1", "田中葵小心"]
    by_text = {item["text"]: item for item in items}
    assert by_text["田中葵小心"]["x"] == 0
    assert by_text["田中葵小心"]["y"] == 74
    assert by_text["d1"]["x"] == 0
    assert by_text["d1"]["y"] == 21


def test_header_attempt_matches_from_positive_coordinate_items():
    """対照ケース: 正の座標だけの従来形式でも、ヘッダ判定は従来通り
    宛先「田中葵」への前方一致で MATCH になる。
    """
    items = gui_flow.parse_ocr(
        "TEXT [x=0 y=74 w=95 h=22 conf=0.30] 田中葵小心\n"
        "TEXT [x=0 y=21 w=9 h=13 conf=0.30] d1"
    )
    runs = gui_flow.text_runs(items, 10)

    att = gui_flow.classify_header_attempt(
        runs, TARGET_ALIASES, [], prefix_max_extra=2
    )
    assert att["classification"] == "MATCH"
    assert att["matched"] == TARGET
