"""仕様テスト: nav 精査で宛先が見つからない場合のホーム遷移リトライ.

実装コードを読まず、与えられた仕様 (完了の定義) だけから書いた受け入れテスト。

対象仕様:
  Runner.scrutinize_nav() は、トーク一覧の OCR で宛先と完全一致する行が
  見つからない場合、実効設定に home_click ([x, y]) があれば次の救済を
  最大 1 回だけ行う (Discord がサーバー画面を開いていて DM 一覧が
  見えていないケースの救済。home_click 未設定なら従来どおり即停止):

  - home_click を 1 回クリックし、再撮影して宛先探索をやり直す。
    クリックは click_point 経由 (文字入力・送信キーは絶対に行わない)。
  - 再探索で候補が 1 件見つかれば、通常の精査ロジック (保存座標と
    同じ行なら通過 / 違う行なら候補行をクリックして nav_click を
    更新・保存) に合流する。
  - 再探索でも候補 0 件なら False / nav_scrutiny_error ==
    "nav_recipient_not_found" (ホームクリックは 2 回行わない)。
  - ホームクリック自体が失敗 (click_point が False) なら False /
    nav_scrutiny_error == "nav_home_click_failed" で、再撮影はしない。
  - 再撮影が読めない (frame None 等) なら False /
    nav_scrutiny_error == "nav_scrutiny_unreadable"。
  - 1 回目の探索で候補が見つかる場合は home_click を発動しない。
  - ホーム遷移の試行結果は checks に {"name": "nav_home", ...} として
    残る。

人名・宛先名はすべて架空のもの (テスト花子 / テスト太郎) を使う。
Mac 接続部分は monkeypatch / インスタンス属性の差し替えでモック化する。
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gui_flow  # noqa: E402

FRAME = (0, 30, 1440, 870)
TALK_LIST_WIDTH_RATIO = 0.27

ALIAS = "テスト花子"
OTHER_1 = "テスト太郎"

RECIPIENT_ID = "test-hanako"
HOME_CLICK = [36, 83]

ROW_CENTERS_ABS = [(160, 180), (160, 252), (160, 324)]


def _item(text, row, x=100, w=120):
    center_abs_y = ROW_CENTERS_ABS[row][1]
    center_rel_y = center_abs_y - FRAME[1]
    return {
        "x": x,
        "y": center_rel_y - 12,
        "w": w,
        "h": 24,
        "conf": 0.99,
        "text": text,
    }


def _make_cfg(saved_nav, home_click):
    app = {
        "app_name": "Discord",
        "window_app": "Discord",
        "process": "Discord",
        "input_click": [700, 780],
        "default_recipient": RECIPIENT_ID,
        "recipients": {
            RECIPIENT_ID: {
                "aliases": [ALIAS],
                "nav_click": list(saved_nav),
            }
        },
        "recipient_aliases": [ALIAS],
        "talk_list_width_ratio": TALK_LIST_WIDTH_RATIO,
    }
    if home_click is not None:
        app["home_click"] = list(home_click)
    return {
        "ssh": {
            "host": "127.0.0.1",
            "user": "test-user",
            "identity": "/tmp/id_test_dummy",
            "control_path": "/tmp/gf-test-ctl",
            "proxy_script": "/tmp/proxy_test_dummy.sh",
        },
        "apps": {"discord": app},
    }


class _Record:
    def __init__(self):
        self.clicks = []
        self.capture_calls = 0
        self.save_config_calls = []
        self.input_calls = []
        self.send_calls = []


def _make_runner(monkeypatch, saved_nav, home_click, captures, click_ok=True):
    """captures: capture_ocr が順に返す (frame, items) のリスト。"""
    cfg = _make_cfg(saved_nav, home_click)
    runner = gui_flow.Runner(cfg, "discord", to=RECIPIENT_ID)
    record = _Record()
    queue = list(captures)

    def fake_capture_ocr():
        record.capture_calls += 1
        if queue:
            return queue.pop(0)
        return (None, [])

    def fake_click_point(point, *a, **k):
        record.clicks.append(tuple(point))
        return click_ok

    monkeypatch.setattr(
        gui_flow,
        "save_config",
        lambda cfg_arg=None, *a, **k: record.save_config_calls.append(
            copy.deepcopy(cfg_arg)
        ),
    )
    monkeypatch.setattr(gui_flow, "append_learned_case", lambda *a, **k: None)
    runner.capture_ocr = fake_capture_ocr
    runner.click_point = fake_click_point
    runner.input = lambda *a, **k: record.input_calls.append((a, k))
    runner.send = lambda *a, **k: record.send_calls.append((a, k))
    return runner, cfg, record


def _nav_home_checks(runner):
    return [c for c in runner.checks if c.get("name") == "nav_home"]


# ---------------------------------------------------------------------------
# 1. ホーム遷移後に本人行 (保存座標と同じ行) が見つかる -> 通過
# ---------------------------------------------------------------------------


def test_home_retry_passes_when_recipient_appears_on_saved_row(monkeypatch):
    saved = ROW_CENTERS_ABS[0]
    first = (FRAME, [_item(OTHER_1, row=0)])  # サーバー画面想定: 宛先なし
    second = (FRAME, [_item(ALIAS, row=0), _item(OTHER_1, row=1)])
    runner, cfg, record = _make_runner(monkeypatch, saved, HOME_CLICK, [first, second])

    result = runner.scrutinize_nav()

    assert result is True
    # クリックはホームボタンの 1 回だけ (行クリックはしない)
    assert record.clicks == [tuple(HOME_CLICK)]
    assert record.capture_calls == 2
    assert record.save_config_calls == []
    # nav_home の記録が残る
    entries = _nav_home_checks(runner)
    assert entries and entries[-1]["ok"] is True
    # 文字入力・送信は一切行わない
    assert record.input_calls == [] and record.send_calls == []


# ---------------------------------------------------------------------------
# 2. ホーム遷移後に本人行 (違う行) -> 行クリック + nav_click 更新
# ---------------------------------------------------------------------------


def test_home_retry_corrects_nav_click_when_row_differs(monkeypatch):
    saved = ROW_CENTERS_ABS[0]
    expected = ROW_CENTERS_ABS[1]
    first = (FRAME, [_item(OTHER_1, row=0)])
    second = (FRAME, [_item(OTHER_1, row=0), _item(ALIAS, row=1)])
    runner, cfg, record = _make_runner(monkeypatch, saved, HOME_CLICK, [first, second])

    result = runner.scrutinize_nav()

    assert result is True
    assert len(record.clicks) == 2
    assert record.clicks[0] == tuple(HOME_CLICK)
    clicked_row = record.clicks[1]
    assert abs(clicked_row[0] - expected[0]) <= 5
    assert abs(clicked_row[1] - expected[1]) <= 5
    # nav_click が本人の行へ更新・保存される
    assert tuple(runner.acfg["nav_click"]) == clicked_row
    assert record.save_config_calls


# ---------------------------------------------------------------------------
# 3. ホーム遷移後も宛先なし -> nav_recipient_not_found (再試行は 1 回だけ)
# ---------------------------------------------------------------------------


def test_home_retry_still_not_found_stops_after_one_retry(monkeypatch):
    saved = ROW_CENTERS_ABS[0]
    first = (FRAME, [_item(OTHER_1, row=0)])
    second = (FRAME, [_item(OTHER_1, row=0)])
    runner, cfg, record = _make_runner(monkeypatch, saved, HOME_CLICK, [first, second])

    result = runner.scrutinize_nav()

    assert result is False
    assert runner.nav_scrutiny_error == "nav_recipient_not_found"
    # ホームクリックは 1 回だけ。再撮影も 1 回だけ。
    assert record.clicks == [tuple(HOME_CLICK)]
    assert record.capture_calls == 2


# ---------------------------------------------------------------------------
# 4. home_click 未設定 -> 従来どおり即停止 (クリックなし)
# ---------------------------------------------------------------------------


def test_no_home_click_keeps_legacy_behavior(monkeypatch):
    saved = ROW_CENTERS_ABS[0]
    first = (FRAME, [_item(OTHER_1, row=0)])
    runner, cfg, record = _make_runner(monkeypatch, saved, None, [first])

    result = runner.scrutinize_nav()

    assert result is False
    assert runner.nav_scrutiny_error == "nav_recipient_not_found"
    assert record.clicks == []
    assert record.capture_calls == 1
    assert _nav_home_checks(runner) == []


# ---------------------------------------------------------------------------
# 5. ホームクリック失敗 -> nav_home_click_failed (再撮影しない)
# ---------------------------------------------------------------------------


def test_home_click_failure_stops_without_recapture(monkeypatch):
    saved = ROW_CENTERS_ABS[0]
    first = (FRAME, [_item(OTHER_1, row=0)])
    second = (FRAME, [_item(ALIAS, row=0)])
    runner, cfg, record = _make_runner(
        monkeypatch, saved, HOME_CLICK, [first, second], click_ok=False
    )

    result = runner.scrutinize_nav()

    assert result is False
    assert runner.nav_scrutiny_error == "nav_home_click_failed"
    assert record.clicks == [tuple(HOME_CLICK)]
    assert record.capture_calls == 1


# ---------------------------------------------------------------------------
# 6. 1 回目で見つかる場合はホーム遷移しない
# ---------------------------------------------------------------------------


def test_home_not_used_when_recipient_found_first_time(monkeypatch):
    saved = ROW_CENTERS_ABS[0]
    first = (FRAME, [_item(ALIAS, row=0), _item(OTHER_1, row=1)])
    runner, cfg, record = _make_runner(monkeypatch, saved, HOME_CLICK, [first])

    result = runner.scrutinize_nav()

    assert result is True
    assert record.clicks == []
    assert record.capture_calls == 1
    assert _nav_home_checks(runner) == []


# ---------------------------------------------------------------------------
# 7. 再撮影が読めない -> nav_scrutiny_unreadable
# ---------------------------------------------------------------------------


def test_home_retry_unreadable_recapture(monkeypatch):
    saved = ROW_CENTERS_ABS[0]
    first = (FRAME, [_item(OTHER_1, row=0)])
    second = (None, [])
    runner, cfg, record = _make_runner(monkeypatch, saved, HOME_CLICK, [first, second])

    result = runner.scrutinize_nav()

    assert result is False
    assert runner.nav_scrutiny_error == "nav_scrutiny_unreadable"
    assert record.clicks == [tuple(HOME_CLICK)]
    assert record.capture_calls == 2
