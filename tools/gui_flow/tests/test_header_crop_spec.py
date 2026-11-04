"""仕様テスト: ヘッダ判定の切り出し OCR 化 (フェーズ4).

実装コード (gui_flow.py) を読まず、与えられた仕様 (完了の定義) だけ
から書いた受け入れテスト。既存の spec テスト
(test_header_multi_spec.py / test_recipient_header_spec.py 等) の流儀
(フィクスチャ・フェイク・OCR 行フォーマット、WIN 定数、架空の人名、
Mac 接続の差し替え方法) に倣う。

背景: 従来は撮影 PNG 全体の OCR 結果からヘッダ領域の項目を座標で
拾って宛先を判定していたが、周囲の内容変化に引きずられてヘッダ名が
文字化けし、正しい宛先でも「確認できず」で止まる事故があった。
フェーズ4 では、撮影画像からヘッダ帯だけを切り出して OCR する方式
に変える。

担保する仕様:
  1. 宛先判定の 3 経路 (Runner.check_recipient_header、
     Runner.verify の recipient チェック = mode "field" の送信前
     検閲と mode "bubble" の送信後確認の両方) は、判定文字列を
     「全体 OCR からヘッダ領域を座標で拾う」方式では決めない。
     各回の撮影で得た「ヘッダ帯の切り出し画像だけを OCR した結果」
     の文字列だけで MATCH / OTHER / UNREADABLE を判定する。
  2. build_verify_script が作る Mac 側スクリプトは、従来の全体 OCR
     ファイル (-ocr.txt) に加えて、ヘッダ帯の切り出し OCR ファイル
     (-header-ocr.txt) を生成するステップを含む。切り出しは sips で
     行い、切り出し範囲のパラメータ (talk_list_width_ratio /
     relearn_header_max_y) はスクリプト本文に数値として現れ、
     生成時に渡される acfg / tunables の値から決まる。
  3. Runner.capture_header_runs() があり、(フレーム, runs) を返す
     (runs は (中心点, 文字列) のリストで text_runs と同じ形)。
     check_recipient_header の各回の撮影はこのメソッド経由で行う。
  4. Runner.verify の各回の判定は、同じ回の run_queue レスポンスに
     含まれる -header-ocr.txt の内容から作った run 群で行う。
  5. 全体 OCR (-ocr.txt) の内容は宛先判定に影響しない:
     - 全体 OCR のヘッダ相当部分が文字化けしていても、切り出し OCR
       が宛先名 (前方一致・余り 2 文字以内を含む) を含めば MATCH。
     - 全体 OCR 側に宛先名が含まれていても (左ペインの一覧等)、
       切り出し OCR が別人の正式名を含めば OTHER で wrong_recipient。
     - 切り出し OCR が全回文字化けなら recipient_header_unreadable。
     - -header-ocr.txt が取得できない (無い・空の) 回は UNREADABLE。
  6. フェーズ3 の複数回判定の規則は維持: 最大 header_check_attempts
     回 (既定 3) 撮り直し、OTHER が 1 回でも出たら残りを撮らず即
     wrong_recipient、MATCH が出ても早期終了しない、全回 UNREADABLE
     なら recipient_header_unreadable。間隔は
     header_check_interval_sec。
  7. 宛先以外のチェック (本文照合・読み戻し等) は従来通り全体 OCR
     で行われ、切り出し OCR の内容は本文照合に影響しない。
  8. 再学習フロー (relearn_recipient) は対象外 (既存テストが担保)。

人名・宛先名はすべて架空のもの (テスト花子 / テスト太郎 /
テスト次郎) を使う。切り出し OCR の行座標は、仕様 1・4 が
「文字列だけで判定する」と定めるため、座標判定の担保には使わず、
ヘッダ位置相当のダミー値を置く。

実行: python3 -m pytest tests/test_header_crop_spec.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gui_flow  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# Runner.verify に渡る window タプル相当。フレーム幅は 1440。
WIN = (767, 0, 30, 1440, 870)
# capture 系が返すフレーム (x, y, w, h)
FRAME = WIN[1:]

TARGET = "テスト花子"  # 宛先本人 (正式名 = recipients 辞書のキー)
TARGET_ALIAS_SHORT = "花子"  # 宛先本人の短いエイリアス
OTHER = "テスト太郎"  # 別人の正式名
OTHER_2 = "テスト次郎"  # もう一人の別人の正式名

# 文字化け (どのエイリアス / 正式名にも exact / prefix で一致しない)
GARBLED_1 = "テスト主小心"
GARBLED_2 = "読取不明ＡＢ"
GARBLED_3 = "ノイズＸＹＺ"

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

DISCORD_BODY_LINES = [
    "[SYSTEM] AIエージェントからお知らせ",
    "これはテストです。",
    "送信はしません。",
    "※注意",
    "確認が終わったら消します。",
    "返信は不要です。",
]

DUMMY_SSH = {
    "user": "test-user",
    "host": "192.0.2.1",
    "identity": "/tmp/id_test",
    "proxy_script": "/tmp/proxy.sh",
    "control_path": "/tmp/gf-test-ctl",
}

# build_verify_script 用の素の acfg (test_gui_flow.py の DISCORD_CFG と同形)
BARE_ACFG = {
    "app_name": "Discord",
    "window_app": "Discord",
    "process": "Discord",
    "input_click": [700, 852],
    "nav_click": [174, 350],
    "recipient_aliases": ["example-dm"],
    "header_band": 110,
}


def ocr_line(x, y, w, h, text):
    return f"TEXT [x={x} y={y} w={w} h={h} conf=1.00] {text}"


def header_ocr_text(text):
    """切り出し OCR ファイル (-header-ocr.txt) の中身 (1 行)。"""
    return ocr_line(500, 60, 200, 30, text)


def make_cfg(tunables=None):
    recipients = {
        TARGET: {"aliases": [TARGET, TARGET_ALIAS_SHORT], "nav_click": [210, 160]},
        OTHER: {"aliases": [OTHER, "太郎"], "nav_click": [210, 232]},
        OTHER_2: {"aliases": [OTHER_2, "次郎"], "nav_click": [210, 304]},
    }
    cfg = {
        "ssh": dict(DUMMY_SSH),
        "apps": {
            "line": {
                "app_name": "LINE",
                "window_app": "LINE",
                "process": "LINE",
                "input_click": [800, 780],
                "header_band": 110,
                "default_recipient": TARGET,
                "recipients": recipients,
            },
            "discord": {
                "app_name": "Discord",
                "window_app": "Discord",
                "process": "Discord",
                "input_click": [700, 852],
                "header_band": 110,
                "default_recipient": TARGET,
                "recipients": recipients,
            },
        },
    }
    if tunables:
        cfg["tunables"] = tunables
    return cfg


def _no_sleep(monkeypatch):
    # 撮影間隔 (tunables) でテストが実時間待たないようにする。
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)


def checks_blob(runner, name):
    entries = [c for c in runner.checks if c.get("name") == name]
    assert entries, f"checks に {name} の記録が無い: {runner.checks!r}"
    return "\n".join(str(c) for c in entries)


def last_check(runner, name):
    entries = [c for c in runner.checks if c.get("name") == name]
    assert entries, f"checks に {name} の記録が無い: {runner.checks!r}"
    return entries[-1]


def assert_unreadable_indicated(runner, name="recipient"):
    """「確認できず」が wrong_recipient と区別できる形で残ること。"""
    blob = checks_blob(runner, name)
    extra = "\n".join(
        str(getattr(runner, attr, ""))
        for attr in (
            "recipient_header_error",
            "recipient_error",
            "recipient_verify_error",
            "last_recipient_error",
        )
    )
    combined = blob + "\n" + extra
    assert any(
        token in combined
        for token in ("UNREADABLE", "recipient_header_unreadable", "確認できず")
    ), f"確認できずの区別が残っていない: {combined!r}"


# ---------------------------------------------------------------------------
# check_recipient_header 経路 (仕様 1・3・5・6)
#
# seam: 仕様 3 が定める Runner.capture_header_runs を撮影列で差し替える。
# 対照として capture_ocr (全体 OCR 経由の従来経路) には「全体 OCR 側の
# 読み」を返させ、判定がそちらに引きずられないことを見る。
# cropped が None の回は、切り出しが取得できなかった回として
# capture_header_runs が空の runs を返した状況を表す。
# ---------------------------------------------------------------------------


def make_check_runner(monkeypatch, pairs):
    """pairs = [(全体 OCR のヘッダ読み, 切り出し OCR の読み or None), ...]"""
    _no_sleep(monkeypatch)
    runner = gui_flow.Runner(make_cfg(), "line", to=TARGET)
    assert runner.recipient_id == TARGET
    seq = list(pairs)
    state = {"count": 0, "remaining": seq}

    def fake_capture_header_runs(*args, **kwargs):
        state["count"] += 1
        _overall, cropped = seq.pop(0) if seq else (GARBLED_1, GARBLED_1)
        if cropped is None:
            return (FRAME, [])
        return (FRAME, [((600, 75), cropped)])

    def fake_capture_ocr():
        # 従来経路が残っていた場合に使われる全体 OCR 側の読み。
        overall = seq[0][0] if seq else GARBLED_1
        items = gui_flow.parse_ocr(ocr_line(500, 60, 200, 30, overall))
        return (FRAME, items)

    runner.capture_header_runs = fake_capture_header_runs
    runner.capture_ocr = fake_capture_ocr
    runner.click_point = lambda point: True
    return runner, state


def test_check_crop_passes_when_overall_garbled_and_crop_has_target(monkeypatch):
    # 仕様 5 (1): 全体 OCR のヘッダは全回文字化けでも、切り出し OCR に
    # [文字化け, 宛先, 文字化け] があれば MATCH で合格する。
    # 仕様 3: 撮影は capture_header_runs 経由で 3 回 (MATCH でも早期
    # 終了しない = 仕様 6)。
    runner, state = make_check_runner(
        monkeypatch,
        [
            (GARBLED_2, GARBLED_1),
            (GARBLED_3, TARGET),
            (GARBLED_2, GARBLED_3),
        ],
    )

    assert runner.check_recipient_header() is True
    assert runner.recipient_header_error is None
    assert state["count"] == 3


def test_check_crop_prefix_two_extra_passes_when_overall_garbled(monkeypatch):
    # 仕様 5 (1): 切り出し OCR の前方一致 + 余り 2 文字以内も MATCH。
    runner, state = make_check_runner(
        monkeypatch,
        [
            (GARBLED_1, TARGET + "4呼"),  # 宛先名 + 余り 2 文字
            (GARBLED_2, GARBLED_2),
            (GARBLED_3, GARBLED_3),
        ],
    )

    assert runner.check_recipient_header() is True
    assert runner.recipient_header_error is None
    assert state["count"] == 3


def test_check_crop_other_aborts_at_second_despite_overall_target(monkeypatch):
    # 仕様 5 (2): 全体 OCR 側はヘッダ位置にも一覧にも宛先名があるが、
    # 切り出し OCR が [文字化け, 別人, ...] なら 2 回目で OTHER と
    # なり wrong_recipient で即停止する (仕様 6: 3 回目は撮らない)。
    runner, state = make_check_runner(
        monkeypatch,
        [
            (TARGET, GARBLED_1),
            (TARGET, OTHER),
            (TARGET, TARGET),
        ],
    )

    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "wrong_recipient"
    assert state["count"] == 2
    assert len(state["remaining"]) == 1


def test_check_crop_other_first_aborts_at_first_capture(monkeypatch):
    # 仕様 6: OTHER が初回に出たら撮影は 1 回で打ち切る。
    runner, state = make_check_runner(
        monkeypatch,
        [
            (TARGET, OTHER),
            (TARGET, TARGET),
            (TARGET, TARGET),
        ],
    )

    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "wrong_recipient"
    assert state["count"] == 1
    assert len(state["remaining"]) == 2


def test_check_crop_all_garbled_is_unreadable_despite_overall_target(monkeypatch):
    # 仕様 5 (3): 全体 OCR 側に宛先名があっても、切り出し OCR が
    # 全回文字化けなら recipient_header_unreadable で停止する。
    runner, state = make_check_runner(
        monkeypatch,
        [
            (TARGET, GARBLED_1),
            (TARGET, GARBLED_2),
            (TARGET, GARBLED_3),
        ],
    )

    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "recipient_header_unreadable"
    assert runner.recipient_header_error != "wrong_recipient"
    assert state["count"] == 3


def test_check_missing_crop_runs_are_unreadable(monkeypatch):
    # 仕様 5: 切り出しが取得できない回 (capture_header_runs が空の
    # runs を返す回) は UNREADABLE。全回取得できなければ
    # recipient_header_unreadable。全体 OCR 側の宛先名は影響しない。
    runner, state = make_check_runner(
        monkeypatch,
        [
            (TARGET, None),
            (TARGET, None),
            (TARGET, None),
        ],
    )

    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "recipient_header_unreadable"
    assert state["count"] == 3


# ---------------------------------------------------------------------------
# Runner.verify 経路 (仕様 1・4・5・6・7)
#
# seam: Mac 層 (mac.run_queue) を差し替える。各回のレスポンスには
# 全体 OCR (-ocr.txt: 本文は常に正しく、ヘッダ相当部分と一覧は
# シナリオで変える) と切り出し OCR (-header-ocr.txt: シナリオの
# 切り出し読み) を同梱し、同じ回のファイル同士だけで判定される
# ことを見る。cropped が None の回は -header-ocr.txt 自体を
# レスポンスに含めない (取得できない回)。
# ---------------------------------------------------------------------------


def overall_discord_ocr(header, body_lines=None, list_name=None):
    lines = []
    if list_name is not None:
        # 左ペインの一覧項目 (中心 x = 200 でヘッダ領域の外)
        lines.append(ocr_line(100, 60, 200, 30, list_name))
    lines.append(ocr_line(421, 50, 200, 20, header))
    y = 460
    for ln in DISCORD_BODY_LINES if body_lines is None else body_lines:
        lines.append(ocr_line(454, y, 200, 20, ln))
        y += 24
    return "\n".join(lines)


def make_verify_runner(monkeypatch, pairs, body_lines=None):
    """pairs = [(全体 OCR のヘッダ読み, 切り出し OCR の読み or None), ...]

    切り出し読みが "" の回は -header-ocr.txt が空ファイルで返る回。
    """
    _no_sleep(monkeypatch)
    runner = gui_flow.Runner(make_cfg(), "discord", to=TARGET)
    assert runner.recipient_id == TARGET
    seq = list(pairs)
    state = {"count": 0, "remaining": seq}

    def fake_run_queue(script, name, extras=(), timeout=None, **_kw):
        state["count"] += 1
        overall, cropped = seq.pop(0) if seq else (GARBLED_1, GARBLED_1)
        files = {
            "-ocr.txt": overall_discord_ocr(overall, body_lines=body_lines),
            "-png.b64": "",
        }
        if cropped is not None:
            files["-header-ocr.txt"] = (
                "" if cropped == "" else header_ocr_text(cropped)
            )
        return {
            "status": "ok",
            "pid": "1234",
            "window": "767 0 30 1440 870",
            "_files": files,
            "_ssh_rc": 0,
        }

    runner.mac.run_queue = fake_run_queue
    return runner, state


def test_verify_field_crop_passes_when_overall_garbled(monkeypatch):
    # 仕様 1・4・5 (1): mode "field" (送信前検閲) でも、全体 OCR の
    # ヘッダ文字化けに引きずられず、切り出し OCR の
    # [文字化け, 宛先, 文字化け] で合格する。撮影は 3 回。
    runner, state = make_verify_runner(
        monkeypatch,
        [
            (GARBLED_2, GARBLED_1),
            (GARBLED_3, TARGET),
            (GARBLED_2, GARBLED_3),
        ],
    )

    assert runner.verify(DISCORD_TEXT, "field") is True
    assert state["count"] == 3
    check = last_check(runner, "recipient")
    assert check["ok"] is True


def test_verify_bubble_crop_passes_when_overall_garbled(monkeypatch):
    # 仕様 1・4・5 (1): mode "bubble" (送信後確認) でも同じ。
    runner, state = make_verify_runner(
        monkeypatch,
        [
            (GARBLED_2, GARBLED_1),
            (GARBLED_3, TARGET),
            (GARBLED_2, GARBLED_3),
        ],
    )

    assert runner.verify(DISCORD_TEXT, "bubble", fetch_png=True) is True
    assert state["count"] == 3
    check = last_check(runner, "recipient")
    assert check["ok"] is True


def test_verify_field_crop_other_aborts_at_second_despite_overall_target(
    monkeypatch,
):
    # 仕様 5 (2): 全体 OCR 側はヘッダ位置にも左ペインの一覧にも
    # 宛先名があるが、切り出し OCR が [宛先, 別人, ...] なら
    # 2 回目で OTHER となり wrong_recipient で即停止する (仕様 6)。
    _no_sleep(monkeypatch)
    runner = gui_flow.Runner(make_cfg(), "discord", to=TARGET)
    seq = [(TARGET, TARGET), (TARGET, OTHER), (TARGET, TARGET)]
    state = {"count": 0}

    def fake_run_queue(script, name, extras=(), timeout=None, **_kw):
        state["count"] += 1
        overall, cropped = seq.pop(0)
        return {
            "status": "ok",
            "pid": "1234",
            "window": "767 0 30 1440 870",
            "_files": {
                "-ocr.txt": overall_discord_ocr(overall, list_name=TARGET),
                "-header-ocr.txt": header_ocr_text(cropped),
                "-png.b64": "",
            },
            "_ssh_rc": 0,
        }

    runner.mac.run_queue = fake_run_queue

    assert runner.verify(DISCORD_TEXT, "field") is False
    assert state["count"] == 2
    check = last_check(runner, "recipient")
    assert check["ok"] is False
    assert runner.recipient_header_error == "wrong_recipient"


def test_verify_bubble_crop_other_first_aborts_at_first_capture(monkeypatch):
    # 仕様 6: mode "bubble" でも OTHER 初回で撮影は 1 回で打ち切る。
    runner, state = make_verify_runner(
        monkeypatch,
        [
            (TARGET, OTHER),
            (TARGET, TARGET),
            (TARGET, TARGET),
        ],
    )

    assert runner.verify(DISCORD_TEXT, "bubble") is False
    assert state["count"] == 1
    check = last_check(runner, "recipient")
    assert check["ok"] is False
    assert runner.recipient_header_error == "wrong_recipient"


def test_verify_field_crop_all_garbled_is_unreadable_despite_overall_target(
    monkeypatch,
):
    # 仕様 5 (3): 全体 OCR 側に宛先名があっても、切り出し OCR が
    # 全回文字化けなら「確認できず」で失敗し、wrong_recipient と
    # 区別される。
    runner, state = make_verify_runner(
        monkeypatch,
        [
            (TARGET, GARBLED_1),
            (TARGET, GARBLED_2),
            (TARGET, GARBLED_3),
        ],
    )

    assert runner.verify(DISCORD_TEXT, "field") is False
    assert state["count"] == 3
    check = last_check(runner, "recipient")
    assert check["ok"] is False
    assert_unreadable_indicated(runner)


def test_verify_missing_header_file_attempt_is_unreadable_then_recovers(
    monkeypatch,
):
    # 仕様 5: -header-ocr.txt が無い回は UNREADABLE として数え、
    # その 1 回だけで全体が落ちることはない。全体 OCR のヘッダは
    # 全回文字化けなので、合格は切り出し OCR の宛先名による。
    runner, state = make_verify_runner(
        monkeypatch,
        [
            (GARBLED_1, None),  # 切り出しファイルが無い回
            (GARBLED_2, TARGET),
            (GARBLED_3, GARBLED_1),
        ],
    )

    assert runner.verify(DISCORD_TEXT, "field") is True
    assert state["count"] == 3
    check = last_check(runner, "recipient")
    assert check["ok"] is True


def test_verify_empty_header_file_attempt_is_unreadable_then_recovers(
    monkeypatch,
):
    # 仕様 5: -header-ocr.txt が空の回も UNREADABLE として扱う。
    runner, state = make_verify_runner(
        monkeypatch,
        [
            (GARBLED_1, ""),  # 空の切り出しファイル
            (GARBLED_2, TARGET),
            (GARBLED_3, GARBLED_2),
        ],
    )

    assert runner.verify(DISCORD_TEXT, "field") is True
    assert state["count"] == 3
    check = last_check(runner, "recipient")
    assert check["ok"] is True


def test_verify_all_missing_header_file_is_unreadable(monkeypatch):
    # 仕様 5: 全回 -header-ocr.txt が取得できなければ、全回
    # UNREADABLE として recipient_header_unreadable 相当で停止する。
    # 全体 OCR 側の宛先名は影響しない。
    runner, state = make_verify_runner(
        monkeypatch,
        [
            (TARGET, None),
            (TARGET, None),
            (TARGET, None),
        ],
    )

    assert runner.verify(DISCORD_TEXT, "field") is False
    assert state["count"] == 3
    check = last_check(runner, "recipient")
    assert check["ok"] is False
    assert_unreadable_indicated(runner)


def test_verify_body_judged_by_overall_not_crop_noise(monkeypatch):
    # 仕様 7: 切り出し OCR に本文と無関係な文字列 (本文照合に使えば
    # 落ちる文言) が混ざっていても、本文照合は全体 OCR で判定され、
    # 全体 OCR の本文が正しければ合格する。宛先は切り出し OCR の
    # 宛先名で合格する (全体 OCR のヘッダは文字化け)。
    _no_sleep(monkeypatch)
    runner = gui_flow.Runner(make_cfg(), "discord", to=TARGET)
    state = {"count": 0}
    crop_noise = "\n".join(
        [
            header_ocr_text(TARGET),
            ocr_line(500, 460, 200, 20, "全然違う本文です"),
            ocr_line(500, 484, 200, 20, "返信は必要です"),
        ]
    )

    def fake_run_queue(script, name, extras=(), timeout=None, **_kw):
        state["count"] += 1
        return {
            "status": "ok",
            "pid": "1234",
            "window": "767 0 30 1440 870",
            "_files": {
                "-ocr.txt": overall_discord_ocr(GARBLED_1),
                "-header-ocr.txt": crop_noise,
                "-png.b64": "",
            },
            "_ssh_rc": 0,
        }

    runner.mac.run_queue = fake_run_queue

    assert runner.verify(DISCORD_TEXT, "field") is True
    assert last_check(runner, "recipient")["ok"] is True
    assert last_check(runner, "body_match")["ok"] is True


def test_verify_body_mismatch_in_overall_not_rescued_by_crop_body(monkeypatch):
    # 仕様 7: 逆方向。全体 OCR の本文が期待文と違えば、切り出し OCR
    # 側に正しい本文が含まれていても body_match は救済されない。
    # 宛先判定だけは切り出し OCR で合格していること (失敗の原因が
    # 本文照合であること) もあわせて確認する。
    wrong_body = DISCORD_BODY_LINES[:-1] + ["返信は必要です。"]
    _no_sleep(monkeypatch)
    runner = gui_flow.Runner(make_cfg(), "discord", to=TARGET)
    crop_with_body = "\n".join(
        [header_ocr_text(TARGET)]
        + [
            ocr_line(500, 460 + 24 * i, 200, 20, ln)
            for i, ln in enumerate(DISCORD_BODY_LINES)
        ]
    )

    def fake_run_queue(script, name, extras=(), timeout=None, **_kw):
        return {
            "status": "ok",
            "pid": "1234",
            "window": "767 0 30 1440 870",
            "_files": {
                "-ocr.txt": overall_discord_ocr(
                    GARBLED_1, body_lines=wrong_body
                ),
                "-header-ocr.txt": crop_with_body,
                "-png.b64": "",
            },
            "_ssh_rc": 0,
        }

    runner.mac.run_queue = fake_run_queue

    assert runner.verify(DISCORD_TEXT, "field") is False
    assert last_check(runner, "recipient")["ok"] is True
    assert last_check(runner, "body_match")["ok"] is False


# ---------------------------------------------------------------------------
# Runner.capture_header_runs (仕様 3・5)
#
# seam: Mac 層 (mac.run_queue) を差し替え、レスポンスの
# -header-ocr.txt の内容だけから run 群が作られることを見る。
# 全体 OCR (-ocr.txt) には切り出し側と違う文字列を入れておく。
# ---------------------------------------------------------------------------


def make_capture_runner(monkeypatch, files):
    _no_sleep(monkeypatch)
    runner = gui_flow.Runner(make_cfg(), "discord", to=TARGET)
    state = {"count": 0, "calls": []}

    def fake_run_queue(script, name, extras=(), timeout=None, **_kw):
        state["count"] += 1
        state["calls"].append((script, name, extras))
        return {
            "status": "ok",
            "pid": "1234",
            "window": "767 0 30 1440 870",
            "_files": files,
            "_ssh_rc": 0,
        }

    runner.mac.run_queue = fake_run_queue
    return runner, state


def run_texts(runs):
    """runs が (中心点, 文字列) のリスト形であることを確認しつつ
    文字列だけを取り出す。"""
    texts = []
    for run in runs:
        assert isinstance(run, (tuple, list)) and len(run) == 2, run
        center, text = run
        assert isinstance(center, (tuple, list)) and len(center) == 2, run
        assert all(isinstance(v, (int, float)) for v in center), run
        assert isinstance(text, str), run
        texts.append(text)
    return texts


def test_capture_header_runs_method_exists():
    # 仕様 3: Runner に capture_header_runs() があること。
    method = getattr(gui_flow.Runner, "capture_header_runs", None)
    assert callable(method)


def test_capture_header_runs_returns_runs_from_header_file(monkeypatch):
    # 仕様 3: (フレーム, runs) を返し、runs は -header-ocr.txt の
    # 内容から作られる (全体 OCR 側の別人文字列は混ざらない)。
    second = "補助テキスト"
    runner, state = make_capture_runner(
        monkeypatch,
        {
            "-ocr.txt": overall_discord_ocr(OTHER),
            "-header-ocr.txt": "\n".join(
                [header_ocr_text(TARGET), ocr_line(500, 90, 200, 30, second)]
            ),
            "-png.b64": "",
        },
    )

    frame, runs = runner.capture_header_runs()

    assert frame == FRAME
    assert state["count"] == 1
    texts = run_texts(runs)
    assert texts == [TARGET, second]
    assert OTHER not in texts


def test_capture_header_runs_missing_file_returns_empty_runs(monkeypatch):
    # 仕様 3・5: -header-ocr.txt がレスポンスに無い場合は空の runs
    # (呼び出し側では UNREADABLE 相当になる形) を返す。
    runner, state = make_capture_runner(
        monkeypatch,
        {
            "-ocr.txt": overall_discord_ocr(TARGET),
            "-png.b64": "",
        },
    )

    frame, runs = runner.capture_header_runs()

    assert frame == FRAME
    assert runs == []


def test_capture_header_runs_empty_file_returns_empty_runs(monkeypatch):
    # 仕様 3・5: -header-ocr.txt が空の場合も空の runs を返す。
    runner, state = make_capture_runner(
        monkeypatch,
        {
            "-ocr.txt": overall_discord_ocr(TARGET),
            "-header-ocr.txt": "",
            "-png.b64": "",
        },
    )

    frame, runs = runner.capture_header_runs()

    assert frame == FRAME
    assert runs == []


# ---------------------------------------------------------------------------
# build_verify_script (仕様 2)
#
# 値の渡し方は acfg 直下 / tunables 経由の両形式を試し、どちらかの
# 正規の経路で「渡した値がスクリプト本文の数値になる」ことを見る
# (仕様: 生成時に渡される acfg / tunables の値から決まる)。
# ---------------------------------------------------------------------------


def _script_attempts(acfg_extra=None, tun_overrides=None):
    acfg = dict(BARE_ACFG)
    if acfg_extra:
        acfg.update(acfg_extra)
    scripts = []
    # 基本形式 (既存テストと同じ 3 引数) は失敗したらそのまま伝播させる
    scripts.append(gui_flow.build_verify_script("gf-x.sh", "discord", acfg))
    if tun_overrides is not None:
        tun_variants = []
        try:
            tun_variants.append(gui_flow.tunables({"tunables": tun_overrides}))
        except Exception:
            pass
        tun_variants.append(dict(tun_overrides))
        for tun in tun_variants:
            for call in (
                lambda tun=tun: gui_flow.build_verify_script(
                    "gf-x.sh", "discord", acfg, tun
                ),
                lambda tun=tun: gui_flow.build_verify_script(
                    "gf-x.sh", "discord", acfg, tun=tun
                ),
            ):
                try:
                    scripts.append(call())
                except Exception:
                    pass
    return scripts


def test_build_verify_script_generates_header_ocr_with_sips_in_addition():
    # 仕様 2: 全体 OCR (-ocr.txt) に加えて切り出し OCR
    # (-header-ocr.txt) を生成するステップを含み、切り出しは sips。
    script = gui_flow.build_verify_script("gf-x.sh", "discord", dict(BARE_ACFG))

    assert "-ocr.txt" in script  # 従来の全体 OCR は残る
    assert "-header-ocr.txt" in script
    assert "sips" in script


def test_build_verify_script_default_crop_params_appear_as_numbers():
    # 仕様 2: 既定値 (talk_list_width_ratio = 0.32、
    # relearn_header_max_y = 260) を渡すと、その数値がスクリプト
    # 本文に現れる。
    values = {"talk_list_width_ratio": 0.32, "relearn_header_max_y": 260}
    scripts = _script_attempts(acfg_extra=values, tun_overrides=values)

    assert any(
        "-header-ocr.txt" in s and "sips" in s and "0.32" in s and "260" in s
        for s in scripts
    ), f"既定の切り出しパラメータが現れるスクリプトが無い: {scripts!r}"


def test_build_verify_script_crop_params_follow_custom_values():
    # 仕様 2: tunables / acfg の値を変えるとスクリプト内の切り出し
    # パラメータもその値に変わる (特徴的な値で確認する)。
    values = {"talk_list_width_ratio": 0.47, "relearn_header_max_y": 231}
    scripts = _script_attempts(acfg_extra=values, tun_overrides=values)

    assert any(
        "-header-ocr.txt" in s and "sips" in s and "0.47" in s and "231" in s
        for s in scripts
    ), f"変更した切り出しパラメータが現れるスクリプトが無い: {scripts!r}"
