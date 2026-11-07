"""仕様テスト: Discord 宛先確認の幾何と連結文字列 (フェーズ5).

テスト係が完了の定義 (DEFINITION.md) だけから、実装コードを読まずに
書いた受け入れテスト。ハーネスの作法 (フェイク転送、OCR 行フォーマット、
Runner の差し替え) は既存テスト一式
(test_header_crop_spec.py / test_header_multi_spec.py 等) に倣う。

担保する条項とテストの対応:
  A1/A2 幾何: Discord のヘッダ crop 左端 (窓幅 1440pt 前提) が
         330pt 超・423pt 未満。値は apps.discord 配下のアプリ別変数で
         持ち、LINE の値 (0.27) とキー無し acfg の既定 (0.32) は不変。
  B1     単一項目 "てすとch SampleGameおもろ" (空白入り) が MATCH。
  B2     名前と続きが別項目 (同一行・項目間隔が run 連結の範囲内)
         で返っても MATCH。
  B4     続きの内容が別の文字列でも MATCH (内容・長さに依存しない)。
  B3     区切りも項目分割も無い大きな余りだけでは MATCH にしない
         (前方一致の余り上限 2 を緩める方式の否定)。
  C3     名前を含まない無関係な文字列 (日付・本文) では MATCH しない。
  C2     断片 ("すとch" / "ESTCHANNEL") では MATCH しない。
  C1     別宛先の名前 + 続き文字列が読めた場合は OTHER で即停止。
  C4     LINE では長い続きを持つ文字列は MATCH にならず (Discord の
         仕組みが漏れていない)、従来の短い誤読は従来通り MATCH。

有効化の設定 (B5) について: 定義は「config の apps.discord 配下の
設定で駆動する」とだけ定め、キーの名前は定義に書かれていない。
そのため本テストはキー名を決め打ちせず、Discord / LINE の Runner 用
config を config.example.json の apps.<app> を正として複製し、
宛先 (recipients / default_recipient) だけをテスト用に差し替える。
定義 A3 が example を正と定めるため、実装が example に追加する
有効化設定はそのままテスト側にも伝わる形にしてある。

実行: python3 -m pytest tests/test_header_discord_spec.py -q
"""

import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gui_flow  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

GUI_FLOW_DIR = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads(
    (GUI_FLOW_DIR / "config.example.json").read_text(encoding="utf-8")
)

# A1 の前提となる窓幅 (フレーム幅 1440pt)。
WIN_WIDTH = 1440

# 定義が実測例として挙げる宛先名 (Discord) と、その続き文字列。
DISCORD_TARGET = "てすとch"
DISCORD_ACTIVITY = "SampleGameおもろ"
# 架空の別宛先 (既存テストで使う架空名)。
OTHER = "テスト太郎"
# 定義・課題が C4 の例として挙げる LINE 宛先名。
LINE_TARGET = "山田花"

DUMMY_SSH = {
    "user": "test-user",
    "host": "192.0.2.1",
    "identity": "/tmp/id_test",
    "proxy_script": "/tmp/proxy.sh",
    "control_path": "/tmp/gf-test-ctl",
}

# build_verify_script 用の素の acfg (test_header_crop_spec.py の
# BARE_ACFG と同形)。talk_list_width_ratio キーを意図的に持たない。
BARE_ACFG = {
    "app_name": "Discord",
    "window_app": "Discord",
    "process": "Discord",
    "input_click": [700, 852],
    "nav_click": [174, 350],
    "recipient_aliases": ["example-dm"],
}


def ocr_line(x, y, w, h, text):
    return f"TEXT [x={x} y={y} w={w} h={h} conf=1.00] {text}"


def header_ocr(text):
    """切り出し OCR ファイル (-header-ocr.txt) の中身 (1 項目)。"""
    return ocr_line(10, 10, 300, 20, text)


def _no_sleep(monkeypatch):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)


def make_cfg():
    """example の apps / tunables を正として複製した合成 config。

    apps.discord / apps.line のキー (幾何の比率や、定義 B5 の有効化
    設定を含む) は example の値をそのまま保持し、宛先だけを
    テスト用に差し替える。
    """
    cfg = {
        "ssh": dict(DUMMY_SSH),
        "tunables": copy.deepcopy(EXAMPLE.get("tunables", {})),
        "apps": {
            "discord": copy.deepcopy(EXAMPLE["apps"]["discord"]),
            "line": copy.deepcopy(EXAMPLE["apps"]["line"]),
        },
    }
    cfg["apps"]["discord"]["recipients"] = {
        DISCORD_TARGET: {
            "aliases": [DISCORD_TARGET],
            "nav_click": [174, 350],
        },
        OTHER: {"aliases": [OTHER], "nav_click": [210, 232]},
    }
    cfg["apps"]["discord"]["default_recipient"] = DISCORD_TARGET
    cfg["apps"]["line"]["recipients"] = {
        LINE_TARGET: {"aliases": [LINE_TARGET], "nav_click": [210, 160]},
    }
    cfg["apps"]["line"]["default_recipient"] = LINE_TARGET
    return cfg


def make_header_runner(monkeypatch, app, target, header_ocr_text):
    """フェイク転送 (mac.run_queue) が毎回同じ -header-ocr.txt を
    返す Runner。戻り値は (runner, state) で、state["count"] が
    転送回数 (= 撮影回数)。
    """
    _no_sleep(monkeypatch)
    runner = gui_flow.Runner(make_cfg(), app, to=target)
    assert runner.recipient_id == target
    state = {"count": 0}

    def fake_run_queue(script, name, extras=(), timeout=None, **_kw):
        state["count"] += 1
        return {
            "status": "ok",
            "pid": "1234",
            "window": "767 0 30 1440 870",
            "_files": {
                "-ocr.txt": "",
                "-header-ocr.txt": header_ocr_text,
                "-png.b64": "",
            },
            "_ssh_rc": 0,
        }

    runner.mac.run_queue = fake_run_queue
    runner.click_point = lambda point: True
    return runner, state


def assert_match(runner, state):
    assert runner.check_recipient_header() is True
    assert runner.recipient_header_error is None
    # MATCH が出ても早期終了しない (複数回判定の従来規則)。
    assert state["count"] == 3


def assert_unreadable(runner, state):
    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "recipient_header_unreadable"
    assert runner.recipient_header_error != "wrong_recipient"
    assert state["count"] == 3


# ---------------------------------------------------------------------------
# A. 幾何 (A1 / A2)
#
# 公開経路は既存テストと同じ build_verify_script。生成スクリプトの
# crop 計算には ratio=<数値> が現れる (既存の
# test_build_verify_script_* が担保する形) ので、その数値から
# 窓幅 1440pt 前提の crop 左端 x0 = int(1440 * ratio + 0.5) を求める。
# ---------------------------------------------------------------------------


def script_ratio(script):
    match = re.search(r"ratio=([0-9]+(?:\.[0-9]+)?)", script)
    assert match, f"スクリプトに crop の ratio が現れない: {script!r}"
    return float(match.group(1))


def crop_left_pt(ratio):
    # 生成スクリプト内の切り出し計算と同じ丸め方。
    return int(WIN_WIDTH * ratio + 0.5)


def test_a1_discord_example_crop_left_between_330_and_423():
    # A1/A2/A3: example の apps.discord から生成した crop スクリプト
    # の左端が、DM 一覧の実コンテンツ右端 (330pt) より右で、
    # ヘッダ名の開始位置 (423pt) より左に来ること。
    acfg = copy.deepcopy(EXAMPLE["apps"]["discord"])
    script = gui_flow.build_verify_script("gf-x.sh", "discord", acfg)

    assert "-header-ocr.txt" in script
    assert "sips" in script
    left = crop_left_pt(script_ratio(script))
    assert 330 < left < 423, f"Discord crop 左端が範囲外: {left}pt"


def test_a2_line_example_ratio_stays_027():
    # A2: LINE の example 値 (0.27) は変えない。
    assert EXAMPLE["apps"]["line"]["talk_list_width_ratio"] == 0.27
    acfg = copy.deepcopy(EXAMPLE["apps"]["line"])
    script = gui_flow.build_verify_script("gf-x.sh", "line", acfg)

    assert script_ratio(script) == 0.27


def test_a2_bare_acfg_default_ratio_stays_032():
    # A2: talk_list_width_ratio キーを持たない acfg の既定は 0.32
    # のまま (共通既定値を変えない)。
    assert "talk_list_width_ratio" not in BARE_ACFG
    script = gui_flow.build_verify_script(
        "gf-x.sh", "discord", dict(BARE_ACFG)
    )

    assert script_ratio(script) == 0.32
    assert crop_left_pt(0.32) == 461


# ---------------------------------------------------------------------------
# B. 連結文字列の分類 (B1 / B2 / B4) — Discord の Runner で判定する
# ---------------------------------------------------------------------------


def test_b1_single_item_with_space_activity_matches(monkeypatch):
    # B1: 単一項目 "てすとch SampleGameおもろ" (空白入り)。
    runner, state = make_header_runner(
        monkeypatch,
        "discord",
        DISCORD_TARGET,
        header_ocr(f"{DISCORD_TARGET} {DISCORD_ACTIVITY}"),
    )

    assert_match(runner, state)


def test_b2_separate_items_same_row_within_merge_gap_matches(monkeypatch):
    # B2: 名前項目と続き項目が別項目で返るケース。同一行 (y 同じ)、
    # 項目間隔は 5pt (1 項目目の右端 100 に対し 2 項目目の左端 105)
    # で、run 連結が起きる範囲 (talk_row_merge_px 既定 12 以内)。
    # run 化では空白なく連結されるが、項目レベルでは名前が単独で
    # 読めているので MATCH にならなければならない。
    text = "\n".join(
        [
            ocr_line(10, 10, 90, 20, DISCORD_TARGET),
            ocr_line(105, 10, 150, 20, DISCORD_ACTIVITY),
        ]
    )
    runner, state = make_header_runner(
        monkeypatch, "discord", DISCORD_TARGET, text
    )

    assert_match(runner, state)


def test_b4_different_activity_content_matches(monkeypatch):
    # B4: 続きの内容が別のアクティビティ文字列でも MATCH
    # (続きの内容・長さに依存しない)。
    runner, state = make_header_runner(
        monkeypatch,
        "discord",
        DISCORD_TARGET,
        header_ocr(f"{DISCORD_TARGET} XYZプレイ中"),
    )

    assert_match(runner, state)


# ---------------------------------------------------------------------------
# B3 / C3 / C2. 安全側: 大きな余りだけ・無関係・断片は MATCH にしない
# ---------------------------------------------------------------------------


def test_b3_no_separator_large_extra_alone_does_not_match(monkeypatch):
    # B3: 単一項目で空白の区切りも項目分割も無く、名前の後に大きな
    # 余りがそのまま続くだけの文字列。候補のどれも既存の照合規則
    # (exact / prefix 余り 2 以内) を満たさないので MATCH にしない。
    runner, state = make_header_runner(
        monkeypatch,
        "discord",
        DISCORD_TARGET,
        header_ocr(f"{DISCORD_TARGET}{DISCORD_ACTIVITY}"),
    )

    assert_unreadable(runner, state)


def test_c3_unrelated_date_does_not_match(monkeypatch):
    # C3: 名前を含まない日付文字列では MATCH しない。
    runner, state = make_header_runner(
        monkeypatch, "discord", DISCORD_TARGET, header_ocr("2026年11月4日")
    )

    assert_unreadable(runner, state)


def test_c3_message_body_text_does_not_match(monkeypatch):
    # C3: 名前を含まないメッセージ本文では MATCH しない。
    runner, state = make_header_runner(
        monkeypatch,
        "discord",
        DISCORD_TARGET,
        header_ocr("これはテストです。"),
    )

    assert_unreadable(runner, state)


def test_c2_fragment_yoku_ch_does_not_match(monkeypatch):
    # C2: ヘッダ名の頭が切れた断片 "すとch" は MATCH にしない。
    runner, state = make_header_runner(
        monkeypatch, "discord", DISCORD_TARGET, header_ocr("すとch")
    )

    assert_unreadable(runner, state)


def test_c2_fragment_ouhakchannel_does_not_match(monkeypatch):
    # C2: 断片化・文字化けした "ESTCHANNEL" は MATCH にしない。
    runner, state = make_header_runner(
        monkeypatch, "discord", DISCORD_TARGET, header_ocr("ESTCHANNEL")
    )

    assert_unreadable(runner, state)


# ---------------------------------------------------------------------------
# C1. 別宛先の名前 + 続き文字列は OTHER
# ---------------------------------------------------------------------------


def test_c1_other_recipient_with_activity_is_other(monkeypatch):
    # C1: Discord 設定ベースの 2 宛先 config で、別宛先の正式名 +
    # 続き文字列がヘッダに読めた場合は OTHER 分類となり、
    # wrong_recipient で即停止する (残りの撮影をしない)。
    runner, state = make_header_runner(
        monkeypatch,
        "discord",
        DISCORD_TARGET,
        header_ocr(f"{OTHER} {DISCORD_ACTIVITY}"),
    )

    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "wrong_recipient"
    assert state["count"] == 1
    blob = "\n".join(
        str(c) for c in runner.checks if c.get("name") == "recipient_header"
    )
    assert "OTHER" in blob


# ---------------------------------------------------------------------------
# C4. LINE には Discord の仕組みが漏れていない
# ---------------------------------------------------------------------------


def test_c4_line_long_continuation_does_not_match(monkeypatch):
    # C4: LINE の設定では、名前 + 空白 + 長い続きの文字列は
    # MATCH にならない (連結対応は Discord の設定でのみ有効)。
    runner, state = make_header_runner(
        monkeypatch,
        "line",
        LINE_TARGET,
        header_ocr(f"{LINE_TARGET} これは長い続きの文字列"),
    )

    assert_unreadable(runner, state)


def test_c4_line_short_misread_still_matches_as_before(monkeypatch):
    # C4 (従来通り): LINE の短い誤読 (宛先名 + 余り 2 文字) は
    # 従来通り prefix で MATCH する。
    runner, state = make_header_runner(
        monkeypatch, "line", LINE_TARGET, header_ocr(f"{LINE_TARGET}小心")
    )

    assert_match(runner, state)
