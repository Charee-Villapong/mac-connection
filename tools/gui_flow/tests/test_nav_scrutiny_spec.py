"""仕様テスト: 送信前の nav_click 精査 (scrutinize_nav) と run_post の前段ゲート.

実装コードを読まず、与えられた仕様 (完了の定義) だけから書いた受け入れテスト。

対象仕様:
  1. Runner.scrutinize_nav() が bool を返し、トーク一覧の OCR から
     宛先名と完全一致する行候補を (一覧領域内でのみ) 探す。
     - 候補 1 件かつ保存済み nav_click と同じ行: 通過 (クリックも保存もなし)
     - 候補 1 件だが保存座標と違う行: 候補行をクリックして開き、
       nav_click を候補座標に更新して保存し、学習記録にも残す。戻り値は True。
     - 候補 0 件: False / nav_scrutiny_error == "nav_recipient_not_found"
     - 候補 2 件以上: False / nav_scrutiny_error == "nav_recipient_ambiguous"
     - 撮影・OCR 不可: False / nav_scrutiny_error == "nav_scrutiny_unreadable"
     - いずれの場合も結果が self.checks に
       {"name": "nav_scrutiny", "ok": ..., "evidence": ...} として残る。
  2. gui_flow.run_post(r, text, ...) は check_recipient_header() の前に
     scrutinize_nav() を 1 回呼ぶ。False の場合は後段を一切呼ばず
     (False, r.nav_scrutiny_error, {"count": 0, "attempts": []}) を返す。
  3. 精査は文字入力・送信キーを絶対に行わない (行を開くクリックのみ)。

人名・宛先名はすべて架空のもの (テスト花子 / テスト太郎 / テスト次郎) を使う。
Mac 接続部分 (capture_ocr / click_point / save_config / append_learned_case 等) は
monkeypatch / インスタンス属性の差し替えでモック化し、公開メソッド経由で検証する。
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gui_flow  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

FRAME = (0, 30, 1440, 850)  # (x, y, w, h)
# フレーム幅 1440 * 一覧幅比 0.27 = 388.8 が一覧領域の境界 x
TALK_LIST_WIDTH_RATIO = 0.27

ALIAS = "テスト花子"
OTHER_1 = "テスト太郎"
OTHER_2 = "テスト次郎"

RECIPIENT_ID = "test-hanako"

# 行の縦間隔は約 72px。各行の名前項目は一覧側 (x=100, w=120) に置く。
# 項目の中心 (画面絶対座標) がその行の候補座標になる:
#   行0: (160, 180) / 行1: (160, 252) / 行2: (160, 324)
ROW_CENTERS_ABS = [(160, 180), (160, 252), (160, 324)]


def _item(text, row, x=100, w=120):
    """行 `row` の名前項目 (窓相対座標)。y は中心が ROW_CENTERS_ABS に合う位置。"""
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


def _make_cfg(saved_nav):
    saved = list(saved_nav)
    return {
        # Runner 生成時に Mac オブジェクトが参照するだけのダミー (接続はしない)
        "ssh": {
            "host": "127.0.0.1",
            "user": "test-user",
            "identity": "/tmp/id_test_dummy",
            "control_path": "/tmp/gf-test-ctl",
            "proxy_script": "/tmp/proxy_test_dummy.sh",
        },
        "apps": {
            "line": {
                "app_name": "LINE",
                "window_app": "LINE",
                "process": "LINE",
                "input_click": [800, 780],
                "nav_click": list(saved),
                "default_recipient": RECIPIENT_ID,
                "recipients": {
                    RECIPIENT_ID: {
                        "aliases": [ALIAS],
                        "nav_click": list(saved),
                    }
                },
                "recipient_aliases": [ALIAS],
                "talk_list_width_ratio": TALK_LIST_WIDTH_RATIO,
            }
        },
    }


class _Record:
    def __init__(self):
        self.clicks = []  # click_point に渡された座標 (tuple 化)
        self.save_config_calls = []  # save_config に渡された cfg の deep copy
        self.learned_calls = []  # append_learned_case に渡された record の deep copy
        self.input_calls = []  # 文字入力系が呼ばれたら記録
        self.send_calls = []  # 送信系が呼ばれたら記録


def _make_runner(monkeypatch, saved_nav, capture_result):
    cfg = _make_cfg(saved_nav)
    runner = gui_flow.Runner(cfg, "line", to=RECIPIENT_ID)
    record = _Record()

    def fake_save_config(cfg_arg=None, *args, **kwargs):
        # 呼び出し時点の状態を snapshot しておく (後からの変更と切り分ける)
        record.save_config_calls.append(copy.deepcopy(cfg_arg))

    def fake_append_learned_case(record_arg=None, *args, **kwargs):
        record.learned_calls.append(copy.deepcopy(record_arg))

    # モジュールレベルの保存関数をモック化する (実ファイルへは書かない)
    monkeypatch.setattr(gui_flow, "save_config", fake_save_config)
    monkeypatch.setattr(gui_flow, "append_learned_case", fake_append_learned_case)
    # Runner メソッドとして呼ばれる実装にも備え、インスタンス側にも同じ fake を置く
    runner.save_config = fake_save_config
    runner.append_learned_case = fake_append_learned_case

    runner.capture_ocr = lambda: capture_result
    runner.click_point = lambda point, *a, **k: record.clicks.append(tuple(point))
    # 精査が文字入力・送信を行わないことの監視用
    runner.input = lambda *a, **k: record.input_calls.append((a, k))
    runner.send = lambda *a, **k: record.send_calls.append((a, k))

    return runner, cfg, record


def _nav_scrutiny_check(runner):
    entries = [c for c in runner.checks if c.get("name") == "nav_scrutiny"]
    assert entries, "checks に nav_scrutiny の結果が残っていない"
    return entries[-1]


def _collect_nav_clicks(obj, found=None):
    """ネストした dict/list の中にある nav_click の値をすべて集める。"""
    if found is None:
        found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "nav_click" and isinstance(value, (list, tuple)):
                found.append(tuple(value))
            else:
                _collect_nav_clicks(value, found)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _collect_nav_clicks(value, found)
    return found


# ---------------------------------------------------------------------------
# a. 本人通過
# ---------------------------------------------------------------------------


def test_a_pass_when_saved_row_is_recipient(monkeypatch):
    saved = ROW_CENTERS_ABS[0]  # テスト花子は行0、保存座標も行0
    items = [
        _item(ALIAS, row=0),
        _item(OTHER_1, row=1),
        _item(OTHER_2, row=2),
    ]
    runner, cfg, record = _make_runner(monkeypatch, saved, (FRAME, items))

    result = runner.scrutinize_nav()

    assert result is True
    # 行クリックは行われない
    assert record.clicks == []
    # 設定保存 (nav_click 更新) は行われない
    assert record.save_config_calls == []
    # 保存座標は変更されていない
    assert tuple(runner.acfg["nav_click"]) == tuple(saved)
    assert tuple(saved) in _collect_nav_clicks(cfg)
    # checks に通過として残る
    check = _nav_scrutiny_check(runner)
    assert check["ok"] is True
    assert "evidence" in check


# ---------------------------------------------------------------------------
# b. 不一致 → 探索で修正 (事故の形)
# ---------------------------------------------------------------------------


def test_b_mismatch_corrects_nav_click_to_recipient_row(monkeypatch):
    saved = ROW_CENTERS_ABS[0]  # 保存座標は行0 (テスト太郎) を指している = 別人
    expected = ROW_CENTERS_ABS[1]  # テスト花子は行1
    items = [
        _item(OTHER_1, row=0),
        _item(ALIAS, row=1),
        _item(OTHER_2, row=2),
    ]
    runner, cfg, record = _make_runner(monkeypatch, saved, (FRAME, items))

    result = runner.scrutinize_nav()

    assert result is True
    # 本人の行がクリックされて開かれる (ちょうど 1 回、本人の行座標で)
    assert len(record.clicks) == 1
    clicked = record.clicks[0]
    assert abs(clicked[0] - expected[0]) <= 5
    assert abs(clicked[1] - expected[1]) <= 5
    # nav_click が候補座標に更新される
    assert tuple(runner.acfg["nav_click"]) == clicked
    assert clicked in _collect_nav_clicks(cfg)
    # 設定ファイルへ保存される (保存時点の cfg に新しい座標が入っている)
    assert record.save_config_calls, "save_config が呼ばれていない"
    saved_cfgs_nav = []
    for saved_cfg in record.save_config_calls:
        saved_cfgs_nav.extend(_collect_nav_clicks(saved_cfg))
    assert clicked in saved_cfgs_nav
    # 学習記録にも残る
    assert record.learned_calls, "append_learned_case が呼ばれていない"
    # checks は ok=True で、座標更新に触れた evidence を含む
    check = _nav_scrutiny_check(runner)
    assert check["ok"] is True
    evidence_text = str(check.get("evidence", "")).lower()
    assert any(
        token in evidence_text
        for token in (
            "更新",
            "修正",
            "候補",
            "不一致",
            "nav_click",
            "nav",
            "updated",
            "corrected",
            str(expected[1]),
        )
    ), f"evidence が座標更新に触れていない: {check.get('evidence')!r}"
    # 精査中に文字入力・送信キーは絶対に行わない
    assert record.input_calls == []
    assert record.send_calls == []


# ---------------------------------------------------------------------------
# c. 一覧に宛先が無い (部分一致のみ含む)
# ---------------------------------------------------------------------------


def test_c_not_found_when_only_partial_match_in_list(monkeypatch):
    saved = ROW_CENTERS_ABS[0]
    items = [
        _item(OTHER_1, row=0),
        # 名前を含むだけの部分一致は候補にしない
        _item("テスト花子さん", row=1, w=180),
        _item(OTHER_2, row=2),
        # 一覧領域の外 (チャット側ヘッダ想定, x=800) の完全一致も候補にしない
        _item(ALIAS, row=0, x=800),
    ]
    runner, cfg, record = _make_runner(monkeypatch, saved, (FRAME, items))

    result = runner.scrutinize_nav()

    assert result is False
    assert runner.nav_scrutiny_error == "nav_recipient_not_found"
    assert record.clicks == []
    assert record.save_config_calls == []
    assert tuple(runner.acfg["nav_click"]) == tuple(saved)
    check = _nav_scrutiny_check(runner)
    assert check["ok"] is False


# ---------------------------------------------------------------------------
# d. 同名 2 件 (完全一致の行が 2 つ)
# ---------------------------------------------------------------------------


def test_d_ambiguous_when_two_exact_matches(monkeypatch):
    saved = ROW_CENTERS_ABS[0]
    items = [
        _item(ALIAS, row=0),
        _item(OTHER_1, row=1),
        _item(ALIAS, row=2),
    ]
    runner, cfg, record = _make_runner(monkeypatch, saved, (FRAME, items))

    result = runner.scrutinize_nav()

    assert result is False
    assert runner.nav_scrutiny_error == "nav_recipient_ambiguous"
    assert record.clicks == []
    assert record.save_config_calls == []
    assert tuple(runner.acfg["nav_click"]) == tuple(saved)
    check = _nav_scrutiny_check(runner)
    assert check["ok"] is False


# ---------------------------------------------------------------------------
# e. 撮影不可
# ---------------------------------------------------------------------------


def test_e_unreadable_when_capture_fails(monkeypatch):
    saved = ROW_CENTERS_ABS[0]
    runner, cfg, record = _make_runner(monkeypatch, saved, (None, []))

    result = runner.scrutinize_nav()

    assert result is False
    assert runner.nav_scrutiny_error == "nav_scrutiny_unreadable"
    assert record.clicks == []
    assert record.save_config_calls == []
    check = _nav_scrutiny_check(runner)
    assert check["ok"] is False


# ---------------------------------------------------------------------------
# f. run_post: 精査 False では後段を一切呼ばずに専用の戻り値を返す
# ---------------------------------------------------------------------------


def _make_runner_for_run_post(monkeypatch):
    cfg = _make_cfg(ROW_CENTERS_ABS[0])
    runner = gui_flow.Runner(cfg, "line", to=RECIPIENT_ID)
    return runner


def test_f_run_post_stops_before_header_when_scrutiny_fails(monkeypatch):
    runner = _make_runner_for_run_post(monkeypatch)
    calls = []

    def fake_scrutinize():
        calls.append("scrutinize_nav")
        runner.nav_scrutiny_error = "nav_recipient_not_found"
        return False

    runner.nav_scrutiny_error = "nav_recipient_not_found"
    runner.scrutinize_nav = fake_scrutinize
    runner.check_recipient_header = lambda *a, **k: calls.append(
        "check_recipient_header"
    )
    runner.pre_send_once = lambda *a, **k: calls.append("pre_send_once")
    runner.send = lambda *a, **k: calls.append("send")
    runner.cleanup_wrong_recipient = lambda *a, **k: calls.append(
        "cleanup_wrong_recipient"
    )

    result = gui_flow.run_post(runner, "テスト送信文")

    assert result == (False, "nav_recipient_not_found", {"count": 0, "attempts": []})
    # scrutinize_nav は 1 回だけ呼ばれ、後段は一切呼ばれない
    assert calls == ["scrutinize_nav"]


# ---------------------------------------------------------------------------
# g. run_post: 精査 True では check_recipient_header へ進む (順序の担保)
# ---------------------------------------------------------------------------


def test_g_run_post_proceeds_to_header_when_scrutiny_passes(monkeypatch):
    runner = _make_runner_for_run_post(monkeypatch)
    calls = []

    def fake_scrutinize():
        calls.append("scrutinize_nav")
        return True

    def fake_header(*a, **k):
        calls.append("check_recipient_header")
        return True

    runner.scrutinize_nav = fake_scrutinize
    runner.check_recipient_header = fake_header
    runner.pre_send_once = lambda *a, **k: (
        calls.append("pre_send_once"),
        True,
    )[1]
    runner.send = lambda *a, **k: (calls.append("send"), True)[1]
    runner.cleanup_wrong_recipient = lambda *a, **k: calls.append(
        "cleanup_wrong_recipient"
    )
    # 送信後処理などがあれば Mac に触らないよう無害化する (存在する場合のみ)
    for name in ("store_post_send_screenshot", "dismiss_overlay"):
        if hasattr(runner, name):
            setattr(runner, name, lambda *a, **k: None)

    # このケースの焦点は「精査 → ヘッダ確認」の順序と前進の担保。
    # ヘッダ以降の戻り値の解釈は既存スイート側の担当なので、
    # 後段 fake との解釈差で例外になっても順序の検証自体は行う。
    try:
        gui_flow.run_post(runner, "テスト送信文")
    except Exception:
        pass

    assert "scrutinize_nav" in calls
    assert "check_recipient_header" in calls
    assert calls.index("scrutinize_nav") < calls.index("check_recipient_header")
    # 少なくとも最初の 2 ステップがこの順序であること
    assert calls[:2] == ["scrutinize_nav", "check_recipient_header"]
