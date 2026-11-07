"""gui_flow の再学習フロー (line careful --to 未知宛先) のヘッダ確認に
対する仕様テスト: 一致ルール「完全一致 OR 前方一致 + 余り N 文字以内」。

テスト係が仕様 (完了の定義) だけから作成した独立テスト。実装コードは
読まず、公開フロー (Runner.relearn_recipient) と既存スペックテストの
モック作法 (capture_ocr の差し替え、click_point のモック、config 保存先
の tmp 化) だけで担保する。実機 (Mac) には一切アクセスしない。
このルールは LINE 限定ではなく再学習のアプリ共通経路に適用されるため、
Discord 形状のケースも 1 件含む。

担保する仕様:
  1. ヘッダの OCR 文字列が「<名前>4呼」(前方一致 + 余り 2 文字、末尾は
     アイコン誤読を模したもの) の場合、ヘッダ確認が通過し宛先が登録
     される。checks の recipient_header の evidence に prefix 方式で
     あることが記録される。
  2. 前方一致でも余りが 3 文字以上 (例「<名前>あいう」) の場合は
     ヘッダ確認で停止し、何も登録されない。
  3. 前方一致しない名前 (全く別の文字列) がヘッダにある場合は停止し、
     登録されない。
  4. 完全一致の場合は従来通り通過し、evidence に exact 方式である
     ことが記録される。
  5. 一覧側は不変: 名前を部分一致で含むだけの行しか無い場合は、
     従来通り曖昧 (recipient_ambiguous) として停止し登録されない。
  6. N は tunables のキー relearn_header_prefix_max_extra (既定値 2)
     で上書きできる: N=0 では「<名前>4呼」が停止し、N=3 では余り
     3 文字が通過する。
  7. Discord でも同じ: ヘッダが「<名前> + アイコン誤読の連結
     (余り 2 文字以内)」の場合、app=discord でも prefix 方式で通過し
     登録される。

宛先名はすべて架空のテスト用名称であり、実在の宛先は使わない。

実行: python3 -m pytest tests/test_header_prefix_spec.py -q
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

# 窓幅 1420pt のフレーム (x, y, w, h)。既存スペックテストと同じ前提。
FRAME = (0, 30, 1420, 850)

# 架空の宛先名 (実在の宛先名は書かない)
LINE_NAME = "テスト花子"
DISCORD_NAME = "ミラ・テスト"

# 一覧の行 (左端 x=100 は境界 0.32 x 1420 = 454.4pt の内側) と、その中心
ROW_ITEM = (100, 200)
ROW_CLICK = (160, 240)  # フレーム原点 + 行内中心 (100+120/2, 30+200+20/2)

# ヘッダ項目の位置: x=500 は境界 (454.4pt) より右、y=60 は
# relearn_header_max_y (既定 260) 未満のヘッダ領域内。
HEADER_XY = (500, 60)


def _item(x, y, text, w=120, h=20):
    return {"x": x, "y": y, "w": w, "h": h, "conf": 1.0, "text": text}


def _app_cfg(label, default_id):
    return {
        "app_name": label,
        "window_app": label,
        "process": label,
        "input_click": [800, 780] if label == "LINE" else [700, 852],
        "default_recipient": default_id,
        "recipients": {
            default_id: {"aliases": [default_id], "nav_click": [100, 120]},
        },
    }


def _runner(tmp_path, monkeypatch, app_key, name, extra_tunables=None):
    """未知宛先宛ての Runner と、その config ファイルを用意する。"""
    tunables = {"talk_list_width_ratio": 0.32}
    if extra_tunables:
        tunables.update(extra_tunables)
    cfg = {
        "ssh": dict(DUMMY_SSH),
        "tunables": tunables,
        "apps": {
            "line": _app_cfg("LINE", "test-default-line"),
            "discord": _app_cfg("Discord", "test-default-discord"),
        },
    }
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


def _header_check(runner):
    """checks から recipient_header の記録を取り出す (無ければ失敗)。"""
    found = [c for c in runner.checks if c["name"] == "recipient_header"]
    assert found, "checks に recipient_header の記録が無い"
    return found[-1]


def _assert_registered(runner, cfg_file, app_key, name, learned):
    assert runner.recipient_known is True
    assert learned[0]["item"] == "recipient"
    assert learned[0]["new"]["nav_click"] == list(ROW_CLICK)
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert name in saved["apps"][app_key]["recipients"]
    assert saved["apps"][app_key]["recipients"][name]["nav_click"] == list(ROW_CLICK)


def _assert_nothing_registered(runner, cfg_file, learned_file, before):
    assert runner.recipient_known is False
    assert cfg_file.read_bytes() == before
    assert not learned_file.exists()


# --- ケース 1: 前方一致 + 余り 2 文字 (アイコン誤読) は prefix で通過 ---------


def test_header_prefix_within_two_extra_chars_registers_as_prefix(
    tmp_path, monkeypatch
):
    runner, cfg_file, _learned_file = _runner(tmp_path, monkeypatch, "line", LINE_NAME)
    # ヘッダ文字列は指定名で始まり、余りは「4呼」の 2 文字 (アイコン誤読想定)
    header_text = LINE_NAME + "4呼"
    assert header_text.startswith(LINE_NAME)
    assert len(header_text) - len(LINE_NAME) == 2
    clicked = _shots(
        runner,
        [
            (FRAME, [_item(*ROW_ITEM, LINE_NAME)]),
            (FRAME, [_item(*HEADER_XY, header_text, w=160)]),
        ],
    )

    ok, learned = runner.relearn_recipient(LINE_NAME)

    assert ok is True
    assert clicked == [ROW_CLICK]
    _assert_registered(runner, cfg_file, "line", LINE_NAME, learned)
    check = _header_check(runner)
    assert check["ok"] is True
    assert "prefix" in check["evidence"]


# --- ケース 2: 前方一致でも余り 3 文字以上は停止 -------------------------------


def test_header_prefix_with_three_extra_chars_stops_and_registers_nothing(
    tmp_path, monkeypatch
):
    runner, cfg_file, learned_file = _runner(tmp_path, monkeypatch, "line", LINE_NAME)
    before = cfg_file.read_bytes()
    header_text = LINE_NAME + "あいう"  # 前方一致だが余り 3 文字
    assert len(header_text) - len(LINE_NAME) == 3
    clicked = _shots(
        runner,
        [
            (FRAME, [_item(*ROW_ITEM, LINE_NAME)]),
            (FRAME, [_item(*HEADER_XY, header_text, w=180)]),
        ],
    )

    ok, learned = runner.relearn_recipient(LINE_NAME)

    assert ok is False
    assert learned == []
    assert runner.relearn_error == "recipient_header_mismatch"
    assert clicked == [ROW_CLICK]  # 行は開いたが、ヘッダ確認で止まる
    assert _header_check(runner)["ok"] is False
    _assert_nothing_registered(runner, cfg_file, learned_file, before)


# --- ケース 3: 前方一致しない名前は停止 -----------------------------------------


def test_header_unrelated_name_stops_and_registers_nothing(tmp_path, monkeypatch):
    runner, cfg_file, learned_file = _runner(tmp_path, monkeypatch, "line", LINE_NAME)
    before = cfg_file.read_bytes()
    other = "全く別人"  # 指定名では始まらない、全く別の文字列
    assert not other.startswith(LINE_NAME)
    clicked = _shots(
        runner,
        [
            (FRAME, [_item(*ROW_ITEM, LINE_NAME)]),
            (FRAME, [_item(*HEADER_XY, other)]),
        ],
    )

    ok, learned = runner.relearn_recipient(LINE_NAME)

    assert ok is False
    assert learned == []
    assert runner.relearn_error == "recipient_header_mismatch"
    assert clicked == [ROW_CLICK]
    assert _header_check(runner)["ok"] is False
    _assert_nothing_registered(runner, cfg_file, learned_file, before)


# --- ケース 4: 完全一致は従来通り exact で通過 -----------------------------------


def test_header_exact_match_registers_as_exact(tmp_path, monkeypatch):
    runner, cfg_file, _learned_file = _runner(tmp_path, monkeypatch, "line", LINE_NAME)
    clicked = _shots(
        runner,
        [
            (FRAME, [_item(*ROW_ITEM, LINE_NAME)]),
            (FRAME, [_item(*HEADER_XY, LINE_NAME)]),
        ],
    )

    ok, learned = runner.relearn_recipient(LINE_NAME)

    assert ok is True
    assert clicked == [ROW_CLICK]
    _assert_registered(runner, cfg_file, "line", LINE_NAME, learned)
    check = _header_check(runner)
    assert check["ok"] is True
    assert "exact" in check["evidence"]


# --- ケース 5: 一覧側は不変 (部分一致のみの行は曖昧として停止) -------------------


def test_talk_list_partial_match_only_is_ambiguous_and_registers_nothing(
    tmp_path, monkeypatch
):
    runner, cfg_file, learned_file = _runner(tmp_path, monkeypatch, "line", LINE_NAME)
    before = cfg_file.read_bytes()
    # 名前を部分一致で含むだけの行 (グループ名等) しか無い
    row = _item(*ROW_ITEM, LINE_NAME + "と他2名のグループ", w=220)
    runner.capture_ocr = lambda: (FRAME, [row])
    clicked = []
    runner.click_point = lambda point: clicked.append(point) or True

    ok, learned = runner.relearn_recipient(LINE_NAME)

    assert ok is False
    assert learned == []
    assert runner.relearn_error == "recipient_ambiguous"
    assert clicked == []  # 行を開いてすらいない
    _assert_nothing_registered(runner, cfg_file, learned_file, before)


# --- ケース 6: N の tunables 上書き ----------------------------------------------


def test_tunables_default_prefix_max_extra_is_two():
    # N の既定値は 2 で、config の tunables で上書きできる (公開関数で担保)
    assert gui_flow.tunables({})["relearn_header_prefix_max_extra"] == 2
    overridden = gui_flow.tunables(
        {"tunables": {"relearn_header_prefix_max_extra": 5}}
    )
    assert overridden["relearn_header_prefix_max_extra"] == 5


def test_tunable_zero_blocks_prefix_with_two_extra_chars(tmp_path, monkeypatch):
    # N=0 に上書きすると、前方一致 + 余り 2 文字は通過しなくなる
    runner, cfg_file, learned_file = _runner(
        tmp_path,
        monkeypatch,
        "line",
        LINE_NAME,
        extra_tunables={"relearn_header_prefix_max_extra": 0},
    )
    before = cfg_file.read_bytes()
    _shots(
        runner,
        [
            (FRAME, [_item(*ROW_ITEM, LINE_NAME)]),
            (FRAME, [_item(*HEADER_XY, LINE_NAME + "4呼", w=160)]),
        ],
    )

    ok, learned = runner.relearn_recipient(LINE_NAME)

    assert ok is False
    assert learned == []
    assert runner.relearn_error == "recipient_header_mismatch"
    _assert_nothing_registered(runner, cfg_file, learned_file, before)


def test_tunable_three_allows_prefix_with_three_extra_chars(tmp_path, monkeypatch):
    # N=3 に上書きすると、前方一致 + 余り 3 文字が prefix 方式で通過する
    runner, cfg_file, _learned_file = _runner(
        tmp_path,
        monkeypatch,
        "line",
        LINE_NAME,
        extra_tunables={"relearn_header_prefix_max_extra": 3},
    )
    header_text = LINE_NAME + "あいう"
    assert len(header_text) - len(LINE_NAME) == 3
    _shots(
        runner,
        [
            (FRAME, [_item(*ROW_ITEM, LINE_NAME)]),
            (FRAME, [_item(*HEADER_XY, header_text, w=180)]),
        ],
    )

    ok, learned = runner.relearn_recipient(LINE_NAME)

    assert ok is True
    _assert_registered(runner, cfg_file, "line", LINE_NAME, learned)
    check = _header_check(runner)
    assert check["ok"] is True
    assert "prefix" in check["evidence"]


# --- ケース 7: Discord でもアプリ共通経路として prefix が効く ---------------------


def test_discord_header_prefix_within_two_extra_chars_registers_as_prefix(
    tmp_path, monkeypatch
):
    # Discord の DM 画面を模したモック: 一覧で名前の行を完全一致で開き、
    # ヘッダは「名前 + アイコン誤読の連結 (余り 2 文字)」になっている。
    runner, cfg_file, _learned_file = _runner(
        tmp_path, monkeypatch, "discord", DISCORD_NAME
    )
    header_text = DISCORD_NAME + "4呼"
    assert header_text.startswith(DISCORD_NAME)
    assert len(header_text) - len(DISCORD_NAME) == 2
    clicked = _shots(
        runner,
        [
            (FRAME, [_item(*ROW_ITEM, DISCORD_NAME, w=140)]),
            (FRAME, [_item(*HEADER_XY, header_text, w=180)]),
        ],
    )

    ok, learned = runner.relearn_recipient(DISCORD_NAME)

    assert ok is True
    # 行の中心: Discord 行は w=140 のため (100+140/2, 30+200+20/2) = (170, 240)
    assert clicked == [(170, 240)]
    assert runner.recipient_known is True
    assert learned[0]["item"] == "recipient"
    assert learned[0]["new"]["nav_click"] == [170, 240]
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert DISCORD_NAME in saved["apps"]["discord"]["recipients"]
    assert saved["apps"]["discord"]["recipients"][DISCORD_NAME]["nav_click"] == [
        170,
        240,
    ]
    check = _header_check(runner)
    assert check["ok"] is True
    assert "prefix" in check["evidence"]
