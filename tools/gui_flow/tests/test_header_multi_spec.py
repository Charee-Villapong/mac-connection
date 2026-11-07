"""仕様テスト: ヘッダ判定の複数回撮影化 (フェーズ3).

実装コードを読まず、与えられた仕様 (完了の定義) だけから書いた
受け入れテスト。既存の spec テスト
(test_recipient_header_spec.py / test_nav_scrutiny_spec.py) の流儀
(フィクスチャ・フェイク・OCR 行フォーマット、WIN 定数、架空の人名のみ)
に倣う。Mac 接続部分 (capture_ocr / click_point / Mac.run_queue) は
monkeypatch / インスタンス属性の差し替えでモック化する。

担保する仕様:
  1. ヘッダで宛先を判定する全箇所
     (Runner.check_recipient_header、Runner.verify 経由の送信前検閲
     recipient チェック = mode "field"、送信後確認 recipient チェック
     = mode "bubble") は、最大 N 回 (N は tunables 変数、既定 3) の
     撮影で判定する。各回は必ず撮り直す (画像の使い回し禁止)。
     撮影間には tunables の間隔 (秒) が空く。
  2. 各回の分類は 3 値:
     - MATCH: ヘッダ領域の run が宛先のエイリアス
       (recipient_aliases) のいずれかに header_name_match
       (exact / 前方一致 + 余り既定 2 文字まで) で一致
     - OTHER: 宛先以外の登録済み宛先の「正式名」
       (recipients 辞書の宛先名そのもの。短いエイリアスでは
       判定しない) に同じ規則で一致
     - UNREADABLE: どちらにも一致しない
  3. 総合判定: OTHER が 1 回でも出たら、その時点で残りの撮影をせず
     wrong_recipient として失敗。N 回撮り終えて OTHER が無く MATCH が
     1 回でもあれば合格。全部 UNREADABLE なら「確認できず」で失敗し、
     エラー名は wrong_recipient と区別される
     (check_recipient_header では
     self.recipient_header_error == "recipient_header_unreadable")。
  4. 一致 (MATCH) が出ても早期終了しない。OTHER のときだけ打ち切る。
  5. 失敗時の振る舞い: 入力前ゲート (check_recipient_header が
     False) では run_post は従来通り何も入力せず停止し、エラーは
     recipient_header_error の値
     (wrong_recipient / recipient_header_unreadable) を返す。
     送信前検閲の段階で recipient が「確認できず」で落ちた場合も、
     wrong_recipient のときと同様に cleanup_wrong_recipient() で
     自分の入力文を消去してから停止し、絶対に送信しない。ただし
     run_post の返すエラー名は recipient_header_unreadable で
     区別される。
  6. evidence には各回のヘッダ文字列と分類が残る
     (recipient チェック / recipient_header チェックの evidence
     または付随フィールドから、各回の読みと
     MATCH / OTHER / UNREADABLE の別が追えること)。

人名・宛先名はすべて架空のもの
(テスト花子 / テスト太郎 / テスト次郎) を使う。

実行: python3 -m pytest tests/test_header_multi_spec.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gui_flow  # noqa: E402

from spec_fixtures import make_cfg_header_multi as make_cfg  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# run_verify_checks / Runner.verify に渡る window タプル
# (window_id, x, y, width, height)。フレーム幅は 1440 なので
# 既定 0.32 の一覧境界は centre-x = 460.8。
WIN = (767, 0, 30, 1440, 870)
# capture_ocr が返すフレーム (x, y, w, h)
FRAME = WIN[1:]

TARGET = "テスト花子"  # 宛先本人 (正式名 = recipients 辞書のキー)
TARGET_ALIAS_SHORT = "花子"  # 宛先本人の短いエイリアス
OTHER = "テスト太郎"  # 別人の正式名
OTHER_ALIAS_SHORT = "太郎"  # 別人の短いエイリアス (OTHER 判定に使わない)
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

LINE_TEXT = (
    "【AIエージェントからのお知らせ】\n"
    "これはテストです。\n"
    "送信はしません。\n"
    "確認が終わったら消します。"
)



def ocr_line(x, y, w, h, text):
    return f"TEXT [x={x} y={y} w={w} h={h} conf=1.00] {text}"


# ヘッダ項目: centre-x = 500 + 200/2 = 600 (境界 460.8 より右)、
# y = 60 (上限 260 未満) なのでヘッダ領域に入る。
def header_ocr_line(text, y=60, x=500):
    return ocr_line(x, y, 200, 30, text)




def _no_sleep(monkeypatch):
    # 撮影間隔 (tunables) でテストが実時間待たないようにする。
    # 既存テストと同じく gui_flow.time.sleep を無害化する。
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)


def make_header_runner(monkeypatch, header_sequence):
    """capture_ocr を撮影列で差し替えた Runner (line / 宛先=TARGET).

    戻り値: (runner, state)。state["count"] が撮影回数、
    state["remaining"] が未消費の撮影列。
    """
    _no_sleep(monkeypatch)
    runner = gui_flow.Runner(make_cfg(), "line", to=TARGET)
    assert runner.recipient_id == TARGET
    seq = list(header_sequence)
    state = {"count": 0, "remaining": seq, "clicks": []}

    def fake_capture_ocr():
        state["count"] += 1
        header = seq.pop(0) if seq else GARBLED_1
        items = gui_flow.parse_ocr(header_ocr_line(header))
        return (FRAME, items)

    def fake_capture_header_runs():
        # フェーズ4 是正: check_recipient_header の撮影は
        # capture_header_runs (切り出し OCR) 経由になったため、同じ
        # 撮影列を切り出し側の run 群として返すフェイクも付ける。
        state["count"] += 1
        header = seq.pop(0) if seq else GARBLED_1
        items = gui_flow.parse_ocr(header_ocr_line(header))
        runs = gui_flow.text_runs(items, float(runner.tunables["talk_row_merge_px"]))
        return (FRAME, runs)

    runner.capture_ocr = fake_capture_ocr
    runner.capture_header_runs = fake_capture_header_runs
    runner.click_point = lambda point: state["clicks"].append(point) or True
    return runner, state


def checks_blob(runner, name):
    """name の checks をすべて文字列化したもの (evidence + 付随
    フィールド)。分類や各回の読みがどこに残っていても追える。"""
    entries = [c for c in runner.checks if c.get("name") == name]
    assert entries, f"checks に {name} の記録が無い: {runner.checks!r}"
    return "\n".join(str(c) for c in entries)


def last_check(runner, name):
    entries = [c for c in runner.checks if c.get("name") == name]
    assert entries, f"checks に {name} の記録が無い: {runner.checks!r}"
    return entries[-1]


# ---------------------------------------------------------------------------
# check_recipient_header: 複数回撮影の総合判定
# ---------------------------------------------------------------------------


def test_header_multi_garbled_match_garbled_passes(monkeypatch):
    # [文字化け, 一致, 文字化け] -> True (撮影列を差し替え、回数も検証)
    runner, state = make_header_runner(
        monkeypatch, [GARBLED_1, TARGET, GARBLED_2]
    )

    assert runner.check_recipient_header() is True
    assert runner.recipient_header_error is None
    assert state["count"] == 3


def test_header_multi_match_then_other_aborts_at_second_capture(monkeypatch):
    # [一致, 別人] -> False / wrong_recipient、撮影は 2 回で打ち切り
    runner, state = make_header_runner(monkeypatch, [TARGET, OTHER, TARGET])

    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "wrong_recipient"
    assert state["count"] == 2
    assert len(state["remaining"]) == 1  # 3 回目は撮っていない


def test_header_multi_other_first_aborts_at_first_capture(monkeypatch):
    # [別人, ...] -> 初回で即停止 (撮影回数 == 1)
    runner, state = make_header_runner(monkeypatch, [OTHER, TARGET, TARGET])

    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "wrong_recipient"
    assert state["count"] == 1
    assert len(state["remaining"]) == 2


def test_header_multi_all_garbled_is_unreadable_after_three(monkeypatch):
    # [文字化け x3] -> False / recipient_header_unreadable、撮影回数 == 3
    runner, state = make_header_runner(
        monkeypatch, [GARBLED_1, GARBLED_2, GARBLED_3]
    )

    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "recipient_header_unreadable"
    assert runner.recipient_header_error != "wrong_recipient"
    assert state["count"] == 3


def test_header_multi_match_first_does_not_early_exit(monkeypatch):
    # [一致, 文字化け, 文字化け] -> True かつ撮影回数 == 3
    # (一致でも早期終了しない)
    runner, state = make_header_runner(
        monkeypatch, [TARGET, GARBLED_1, GARBLED_2]
    )

    assert runner.check_recipient_header() is True
    assert runner.recipient_header_error is None
    assert state["count"] == 3


def test_header_multi_all_match_passes_with_three_captures(monkeypatch):
    # [一致 x3] -> True (1 回目で止めず 3 回撮る)
    runner, state = make_header_runner(monkeypatch, [TARGET, TARGET, TARGET])

    assert runner.check_recipient_header() is True
    assert runner.recipient_header_error is None
    assert state["count"] == 3


def test_header_multi_other_short_alias_is_unreadable_not_other(monkeypatch):
    # 別人の「短いエイリアス」だけがヘッダに出た場合は OTHER に
    # ならない (UNREADABLE 扱いで撮り続ける)。続く一致で合格する。
    runner, state = make_header_runner(
        monkeypatch, [OTHER_ALIAS_SHORT, TARGET, GARBLED_1]
    )

    assert runner.check_recipient_header() is True
    assert runner.recipient_header_error is None
    assert state["count"] == 3


def test_header_multi_other_short_alias_then_garbleds_is_unreadable_not_wrong(
    monkeypatch,
):
    # 短いエイリアスのみ + 文字化け x2 は全部 UNREADABLE 相当なので
    # wrong_recipient ではなく recipient_header_unreadable。
    runner, state = make_header_runner(
        monkeypatch, [OTHER_ALIAS_SHORT, GARBLED_1, GARBLED_2]
    )

    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "recipient_header_unreadable"
    assert state["count"] == 3


def test_header_multi_other_formal_prefix_is_other_and_aborts(monkeypatch):
    # 別人の正式名への前方一致 (余り 2 文字) も OTHER として打ち切る。
    other_prefix = OTHER + "4呼"  # 正式名 + 余り 2 文字
    runner, state = make_header_runner(
        monkeypatch, [TARGET, other_prefix, TARGET]
    )

    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "wrong_recipient"
    assert state["count"] == 2


def test_header_multi_evidence_records_each_attempt_classification(monkeypatch):
    # evidence (または付随フィールド) に各回のヘッダ文字列と
    # MATCH / UNREADABLE の別が残ること。
    runner, state = make_header_runner(
        monkeypatch, [GARBLED_1, TARGET, GARBLED_2]
    )

    assert runner.check_recipient_header() is True
    assert state["count"] == 3
    blob = checks_blob(runner, "recipient_header")
    assert GARBLED_1 in blob
    assert TARGET in blob
    assert GARBLED_2 in blob
    assert "MATCH" in blob
    assert "UNREADABLE" in blob


# ---------------------------------------------------------------------------
# run_post: フェイク Runner での失敗時の振る舞い
# ---------------------------------------------------------------------------


class FakeRunner:
    """run_post 用フェイク。呼び出しを順序つきで記録する.

    pre_send_once の戻りは (ok, retryable, record)。
    送信前検閲の recipient が「確認できず」で落ちる状況は、
    record 側 (failed_checks / diff_summary / recipient_error 系
    フィールド) と Runner 属性の両方に
    recipient_header_unreadable の手がかりを置いて再現する
    (既存の wrong_recipient 経路テストと対比できる形)。
    """

    def __init__(
        self,
        header_ok=True,
        header_error=None,
        pre_results=None,
    ):
        self.header_ok = header_ok
        self.recipient_header_error = header_error
        # 実装が別名で見る場合にも備え、同じ値を別属性にも置く
        self.recipient_error = header_error
        self.pre_results = list(pre_results) if pre_results else []
        self.calls = []

    def _record(self, name, args=()):
        self.calls.append((name, args))

    def called(self, name):
        return [c for c in self.calls if c[0] == name]

    def call_names(self):
        return [c[0] for c in self.calls]

    def scrutinize_nav(self):
        self._record("scrutinize_nav")
        return True

    def check_recipient_header(self):
        self._record("check_recipient_header")
        return self.header_ok

    def pre_send_once(self, text):
        self._record("pre_send_once", (text,))
        return self.pre_results.pop(0)

    def send(self):
        self._record("send")
        return True

    def verify(self, text, mode, fetch_png=False):
        self._record("verify", (text, mode))
        return True

    def cleanup_wrong_recipient(self):
        self._record("cleanup_wrong_recipient")
        return True

    def clear(self):
        self._record("clear")
        return True


PRE_PASS = (True, False, {"failed_checks": [], "diff_summary": ""})


def _pre_recipient_failure(error_name, summary):
    record = {
        "failed_checks": ["recipient"],
        "diff_summary": summary,
        "recipient_error": error_name,
        "error": error_name,
    }
    return (False, False, record)


def test_run_post_header_gate_unreadable_returns_distinct_error(monkeypatch):
    # 入力前ゲートが「確認できず」で落ちたら、何も入力せず、
    # エラー名 recipient_header_unreadable で停止する。
    _no_sleep(monkeypatch)
    runner = FakeRunner(
        header_ok=False, header_error="recipient_header_unreadable"
    )

    ok, error, info = gui_flow.run_post(runner, LINE_TEXT)

    assert ok is False
    assert error == "recipient_header_unreadable"
    assert error != "wrong_recipient"
    assert info == {"count": 0, "attempts": []}
    assert len(runner.called("check_recipient_header")) == 1
    assert runner.called("pre_send_once") == []
    assert runner.called("send") == []
    assert runner.called("cleanup_wrong_recipient") == []


def test_run_post_pre_send_recipient_unreadable_cleans_up_and_never_sends(
    monkeypatch,
):
    # 送信前検閲の recipient が「確認できず」で落ちた場合も、
    # wrong_recipient と同様に cleanup で自分の入力文を消去してから
    # 停止し、絶対に送信しない。ただし返るエラー名は
    # recipient_header_unreadable で区別される。
    _no_sleep(monkeypatch)
    pre = _pre_recipient_failure(
        "recipient_header_unreadable",
        "recipient: ヘッダを確認できず (UNREADABLE / "
        "recipient_header_unreadable)",
    )
    runner = FakeRunner(header_ok=True, pre_results=[pre])
    # 送信前段階の「確認できず」を Runner 属性側にも置く
    runner.recipient_header_error = "recipient_header_unreadable"
    runner.recipient_error = "recipient_header_unreadable"
    runner.recipient_verify_error = "recipient_header_unreadable"
    runner.last_recipient_error = "recipient_header_unreadable"

    ok, error, info = gui_flow.run_post(runner, LINE_TEXT)

    assert ok is False
    assert error == "recipient_header_unreadable"
    assert error != "wrong_recipient"
    assert len(runner.called("pre_send_once")) == 1  # 再実行しない
    assert len(runner.called("cleanup_wrong_recipient")) == 1
    assert runner.called("send") == []
    assert runner.called("verify") == []


def test_run_post_pre_send_wrong_recipient_still_cleans_up_with_wrong_name(
    monkeypatch,
):
    # 対比: 送信前検閲の recipient が別人 (wrong) で落ちた場合は
    # 従来通り cleanup して error == "wrong_recipient"。
    _no_sleep(monkeypatch)
    pre = _pre_recipient_failure(
        "wrong_recipient", "recipient: 宛先がヘッダに見当たらない (OTHER)"
    )
    runner = FakeRunner(header_ok=True, pre_results=[pre])
    runner.recipient_header_error = "wrong_recipient"
    runner.recipient_error = "wrong_recipient"

    ok, error, info = gui_flow.run_post(runner, LINE_TEXT)

    assert ok is False
    assert error == "wrong_recipient"
    assert len(runner.called("cleanup_wrong_recipient")) == 1
    assert runner.called("send") == []


# ---------------------------------------------------------------------------
# Runner.verify: recipient チェック (field / bubble) の複数回判定
#
# seam の判断: Runner.verify は Mac 層 (mac.run_queue) を差し替えれば
# 撮影列を差し替えられる (既存 test_gui_flow.py の
# test_verify_fetch_png_sets_last_png_bytes と同じ作法)。各回の OCR には
# 本文を常に正しく入れておき、ヘッダだけを変えることで、合否が
# recipient 判定だけに依存するようにする。
# ---------------------------------------------------------------------------


def discord_ocr(header):
    lines = [ocr_line(421, 50, 200, 20, header)]
    y = 460
    for ln in DISCORD_BODY_LINES:
        lines.append(ocr_line(454, y, 200, 20, ln))
        y += 24
    return "\n".join(lines)


def make_verify_runner(monkeypatch, header_sequence):
    _no_sleep(monkeypatch)
    runner = gui_flow.Runner(make_cfg(), "discord", to=TARGET)
    assert runner.recipient_id == TARGET
    seq = list(header_sequence)
    state = {"count": 0, "remaining": seq}

    def fake_run_queue(script, name, extras=(), timeout=None):
        state["count"] += 1
        header = seq.pop(0) if seq else GARBLED_1
        return {
            "status": "ok",
            "pid": "1234",
            "window": "767 0 30 1440 870",
            "_files": {
                "-ocr.txt": discord_ocr(header),
                # フェーズ4 是正: 新しい撮影スクリプトは切り出し OCR
                # (-header-ocr.txt) も返すため、フェイクにも同じ回の
                # ヘッダ文字列で同梱する。
                "-header-ocr.txt": header_ocr_line(header),
                "-png.b64": "",
            },
            "_ssh_rc": 0,
        }

    runner.mac.run_queue = fake_run_queue
    return runner, state


def test_verify_field_garbled_match_garbled_passes_with_three_captures(
    monkeypatch,
):
    runner, state = make_verify_runner(
        monkeypatch, [GARBLED_1, TARGET, GARBLED_2]
    )

    assert runner.verify(DISCORD_TEXT, "field") is True
    assert state["count"] == 3
    check = last_check(runner, "recipient")
    assert check["ok"] is True


def test_verify_bubble_garbled_match_garbled_passes_with_three_captures(
    monkeypatch,
):
    runner, state = make_verify_runner(
        monkeypatch, [GARBLED_1, TARGET, GARBLED_2]
    )

    assert runner.verify(DISCORD_TEXT, "bubble", fetch_png=True) is True
    assert state["count"] == 3
    check = last_check(runner, "recipient")
    assert check["ok"] is True


def test_verify_field_match_then_other_aborts_at_second_capture(monkeypatch):
    runner, state = make_verify_runner(monkeypatch, [TARGET, OTHER, TARGET])

    assert runner.verify(DISCORD_TEXT, "field") is False
    assert state["count"] == 2
    check = last_check(runner, "recipient")
    assert check["ok"] is False


def test_verify_bubble_all_garbled_fails_with_unreadable_indication(monkeypatch):
    runner, state = make_verify_runner(
        monkeypatch, [GARBLED_1, GARBLED_2, GARBLED_3]
    )

    assert runner.verify(DISCORD_TEXT, "bubble") is False
    assert state["count"] == 3
    check = last_check(runner, "recipient")
    assert check["ok"] is False
    # 「確認できず」が wrong_recipient と区別できる形で残ること
    blob = checks_blob(runner, "recipient")
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


def test_verify_field_evidence_records_each_attempt(monkeypatch):
    runner, state = make_verify_runner(
        monkeypatch, [GARBLED_1, TARGET, GARBLED_2]
    )

    assert runner.verify(DISCORD_TEXT, "field") is True
    assert state["count"] == 3
    blob = checks_blob(runner, "recipient")
    assert GARBLED_1 in blob
    assert TARGET in blob
    assert GARBLED_2 in blob
    assert "MATCH" in blob
    assert "UNREADABLE" in blob
