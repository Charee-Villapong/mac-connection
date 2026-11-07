"""gui_flow の talk_list_width_ratio アプリ別解決に対する仕様テスト。

テスト係が仕様 (完了の定義) だけから作成した独立テスト。実装コードは
読まず、既存テストのモック作法 (Runner の生成、capture_ocr の差し替え、
click_point のモック、config 保存先の tmp 化) だけで担保する。
実機 (Mac) には一切アクセスしない。

担保する仕様:
  1. talk_list_width_ratio はアプリ別に解決できる。config の
     apps.<app> 直下にキーがあればそれを優先し、無ければ共通 tunables
     の値 (既定 0.32) にフォールバックする (後方互換)。
  2. この解決は 2 箇所の両方に適用される: (1) トーク一覧の行探索の
     境界、(2) 開いた先のヘッダ確認で「一覧より右」を判定する境界。
  3. LINE 実測ケース: 窓幅 1420pt でヘッダ名の中心が約 29% (411pt)
     付近にあるとき、アプリ別値 0.27 ではヘッダ確認を通過し、
     フォールバック 0.32 では recipient_header_mismatch で失敗する。
  4. Discord の既定挙動は不変: アプリ別値が無い場合の境界は 0.32 で、
     中心が窓幅の 40% にあるヘッダ名はヘッダ側と判定される。
  5. 行探索側: アプリ別値 0.27 の場合、左端 x が窓幅の 30% にある項目は
     トーク一覧の行として扱われない。フォールバック 0.32 では扱われる
     (境界 = 比率 x 窓幅、項目の x が境界以下なら一覧側、という現行の
     判定方式を前提とする)。

宛先名はすべて架空のテスト用名称であり、実在の宛先は使わない。

実行: python3 -m pytest tests/test_talk_list_ratio_spec.py -q
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gui_flow  # noqa: E402

DUMMY_SSH = {
    "user": "test-user",
    "host": "192.0.2.1",
    "identity": "/tmp/id_test",
    "proxy_script": "/tmp/proxy.sh",
    "control_path": "/tmp/gf-test-ctl",
}

# 仕様 3・5 の前提となる窓幅 1420pt のフレーム (x, y, w, h)
FRAME = (0, 30, 1420, 850)
WINDOW_WIDTH = FRAME[2]

# 架空の宛先名 (実在の宛先名は書かない)
LINE_NAME = "test-mira"
DISCORD_NAME = "test-sora"

_OMIT = object()


def _item(x, y, text, w=120, h=20):
    return {"x": x, "y": y, "w": w, "h": h, "conf": 1.0, "text": text}


def _app_cfg(label, default_id, ratio):
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


def _runner(
    tmp_path,
    monkeypatch,
    app_key,
    name,
    line_ratio=None,
    discord_ratio=None,
    tunables_ratio=0.32,
):
    """未知宛先宛ての Runner と、その config ファイルを用意する。"""
    cfg = {
        "ssh": dict(DUMMY_SSH),
        "apps": {
            "line": _app_cfg("LINE", "test-default-line", line_ratio),
            "discord": _app_cfg("Discord", "test-default-discord", discord_ratio),
        },
    }
    if tunables_ratio is not _OMIT:
        cfg["tunables"] = {"talk_list_width_ratio": tunables_ratio}
    cfg_file = tmp_path / "config.local.json"
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GUI_FLOW_CONFIG", str(cfg_file))
    monkeypatch.setenv("GUI_FLOW_LEARNED", str(tmp_path / "learned_cases.jsonl"))
    runner = gui_flow.Runner(cfg, app_key, to=name)
    assert runner.recipient_known is False
    runner.careful_capture = lambda step: (str(tmp_path / "s.png"), FRAME)
    return runner, cfg_file, tmp_path / "learned_cases.jsonl"


def _shots(runner, shots):
    """capture_ocr を差し替え、クリック座標を記録するリストを返す。"""
    seq = list(shots)
    runner.capture_ocr = lambda: seq.pop(0)
    clicked = []
    runner.click_point = lambda point: clicked.append(point) or True
    return clicked


# 一覧の行 (左端 x=100 は 0.27 でも 0.32 でも境界の内側) の中心クリック座標
ROW_ITEM = (100, 200)
ROW_CLICK = (160, 240)  # フレーム原点 + 行内中心 (100+120/2, 30+200+20/2)


# --- 仕様 3 (ヘッダ側・LINE アプリ別値が優先されるケース) ---------------------


def test_line_header_at_29pct_passes_with_app_ratio_027(tmp_path, monkeypatch):
    # 仕様 1・2(2)・3: LINE にアプリ別値 0.27 があれば tunables の 0.32
    # より優先され、中心が窓幅の約 29% (411pt) のヘッダ名はヘッダ側と
    # 判定されて再学習がヘッダ確認を通過する。
    runner, cfg_file, _learned = _runner(
        tmp_path,
        monkeypatch,
        "line",
        LINE_NAME,
        line_ratio=0.27,
        tunables_ratio=0.32,
    )
    # ヘッダ名: x=351, w=120 → 中心 411pt = 窓幅 1420 の約 29%
    header = _item(351, 60, LINE_NAME)
    assert header["x"] + header["w"] / 2 == 411
    clicked = _shots(
        runner,
        [
            (FRAME, [_item(*ROW_ITEM, LINE_NAME)]),
            (FRAME, [header]),
        ],
    )

    ok, learned = runner.relearn_recipient(LINE_NAME)

    assert ok is True
    assert clicked == [ROW_CLICK]
    assert runner.recipient_known is True
    assert learned[0]["item"] == "recipient"
    assert learned[0]["new"]["nav_click"] == list(ROW_CLICK)
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert LINE_NAME in saved["apps"]["line"]["recipients"]


def test_line_header_at_29pct_fails_with_fallback_032(tmp_path, monkeypatch):
    # 仕様 1・2(2)・3: アプリ別値が無い (フォールバック 0.32) 場合は、
    # 同じ位置 (中心約 29%) のヘッダ名は一覧側と判定され、ヘッダ確認は
    # recipient_header_mismatch で失敗し、何も登録されない。
    runner, cfg_file, learned_file = _runner(
        tmp_path, monkeypatch, "line", LINE_NAME, tunables_ratio=0.32
    )
    before = cfg_file.read_bytes()
    clicked = _shots(
        runner,
        [
            (FRAME, [_item(*ROW_ITEM, LINE_NAME)]),
            (FRAME, [_item(351, 60, LINE_NAME)]),  # 中心 411pt = 約 29%
        ],
    )

    ok, learned = runner.relearn_recipient(LINE_NAME)

    assert ok is False
    assert learned == []
    assert runner.relearn_error == "recipient_header_mismatch"
    assert clicked == [ROW_CLICK]  # 行は開いたが、ヘッダ確認で止まる
    assert runner.recipient_known is False
    assert cfg_file.read_bytes() == before
    assert not learned_file.exists()


# --- 仕様 4 (Discord の既定挙動は変わらない) -----------------------------------


def test_discord_header_at_40pct_passes_with_default_ratio(tmp_path, monkeypatch):
    # 仕様 1・4: Discord にアプリ別値が無く tunables 自体も無い場合、
    # 境界は既定の 0.32 のままで、中心が窓幅の 40% にあるヘッダ名は
    # ヘッダ側と判定されて再学習が通過する。
    runner, cfg_file, _learned = _runner(
        tmp_path,
        monkeypatch,
        "discord",
        DISCORD_NAME,
        tunables_ratio=_OMIT,
    )
    # ヘッダ名: x=508, w=120 → 中心 568pt = 窓幅 1420 の 40%
    header = _item(508, 60, DISCORD_NAME)
    assert (header["x"] + header["w"] / 2) / WINDOW_WIDTH == 0.4
    clicked = _shots(
        runner,
        [
            (FRAME, [_item(*ROW_ITEM, DISCORD_NAME)]),
            (FRAME, [header]),
        ],
    )

    ok, learned = runner.relearn_recipient(DISCORD_NAME)

    assert ok is True
    assert clicked == [ROW_CLICK]
    assert runner.recipient_known is True
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert DISCORD_NAME in saved["apps"]["discord"]["recipients"]


def test_line_app_ratio_does_not_leak_into_discord(tmp_path, monkeypatch):
    # 仕様 1 (アプリ別スコープ): LINE 側にだけアプリ別値 0.27 があっても、
    # Discord の解決には影響しない。Discord はフォールバック 0.32 の
    # ままなので、中心約 29% のヘッダ名は一覧側として失敗する。
    runner, cfg_file, learned_file = _runner(
        tmp_path,
        monkeypatch,
        "discord",
        DISCORD_NAME,
        line_ratio=0.27,
        discord_ratio=None,
        tunables_ratio=0.32,
    )
    before = cfg_file.read_bytes()
    _shots(
        runner,
        [
            (FRAME, [_item(*ROW_ITEM, DISCORD_NAME)]),
            (FRAME, [_item(351, 60, DISCORD_NAME)]),  # 中心 411pt = 約 29%
        ],
    )

    ok, learned = runner.relearn_recipient(DISCORD_NAME)

    assert ok is False
    assert learned == []
    assert runner.relearn_error == "recipient_header_mismatch"
    assert cfg_file.read_bytes() == before
    assert not learned_file.exists()


# --- 仕様 5 (行探索側の境界にもアプリ別値が適用される) -------------------------


def test_row_at_30pct_not_a_talk_row_with_app_ratio_027(tmp_path, monkeypatch):
    # 仕様 1・2(1)・5: アプリ別値 0.27 の場合、左端 x が窓幅の 30%
    # (426pt) にある項目は境界 (0.27 x 1420 = 383.4pt) より右なので
    # トーク一覧の行として扱われず、見つからないまま停止する。
    runner, cfg_file, learned_file = _runner(
        tmp_path, monkeypatch, "line", LINE_NAME, line_ratio=0.27
    )
    before = cfg_file.read_bytes()
    row = _item(426, 200, LINE_NAME)
    assert row["x"] / WINDOW_WIDTH == 0.3
    runner.capture_ocr = lambda: (FRAME, [row])
    clicked = []
    runner.click_point = lambda point: clicked.append(point) or True

    ok, learned = runner.relearn_recipient(LINE_NAME)

    assert ok is False
    assert learned == []
    assert runner.relearn_error == "recipient_not_found"
    assert clicked == []  # 行として扱われないので開いてすらいない
    assert cfg_file.read_bytes() == before
    assert not learned_file.exists()


def test_row_at_30pct_is_a_talk_row_with_fallback_032(tmp_path, monkeypatch):
    # 仕様 1・2(1)・5: フォールバック 0.32 の場合は境界
    # (0.32 x 1420 = 454.4pt) 以下なので、左端 x が窓幅の 30% にある
    # 項目はトーク一覧の行として扱われ、開いて登録まで進む。
    runner, cfg_file, _learned = _runner(
        tmp_path, monkeypatch, "line", LINE_NAME, tunables_ratio=0.32
    )
    row = _item(426, 200, LINE_NAME)
    assert row["x"] / WINDOW_WIDTH == 0.3
    clicked = _shots(
        runner,
        [
            (FRAME, [row]),
            # 開いた先のヘッダは境界より十分右に置く
            (FRAME, [_item(700, 60, LINE_NAME)]),
        ],
    )

    ok, learned = runner.relearn_recipient(LINE_NAME)

    assert ok is True
    # 行の中心 (426+120/2, 30+200+20/2) がクリックされ、登録座標になる
    assert clicked == [(486, 240)]
    assert learned[0]["new"]["nav_click"] == [486, 240]
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["apps"]["line"]["recipients"][LINE_NAME]["nav_click"] == [486, 240]
