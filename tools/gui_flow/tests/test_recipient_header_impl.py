"""check_recipient_header の実装側回帰テスト (Mac 接続はモックで切り分け)。

仕様テスト (test_recipient_header_spec.py) が担保する公開仕様に加え、
Runner.check_recipient_header の分岐 (クリック失敗 / 撮影不可 /
不一致時の状態) を実装側で固定する。

実行: python3 -m pytest tools/gui_flow/tests -q
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gui_flow  # noqa: E402

FRAME = (0, 30, 1440, 870)

CFG = {
    "ssh": {
        "user": "test-user",
        "host": "192.0.2.1",
        "identity": "/tmp/id_test",
        "proxy_script": "/tmp/proxy.sh",
        "control_path": "/tmp/gf-test-ctl",
    },
    "apps": {
        "discord": {
            "app_name": "Discord",
            "window_app": "Discord",
            "process": "Discord",
            "input_click": [700, 852],
            "nav_click": [174, 350],
            "recipient_aliases": ["example-dm"],
            "header_band": 110,
        },
        "line": {
            "app_name": "LINE",
            "window_app": "LINE",
            "process": "LINE",
            "input_click": [800, 780],
            "nav_click": [210, 160],
            "recipient_aliases": ["example-talk"],
            "header_band": 110,
        },
    },
}


def item(x, y, text, w=200, h=20):
    return {"x": x, "y": y, "w": w, "h": h, "conf": 1.0, "text": text}


def header_item(text):
    # 中心 x = 600 (一覧境界 460.8 より右)、y = 60 (上限 260 未満)
    return item(500, 60, text)


def list_item(text):
    # 中心 x = 200 (トーク一覧側)
    return item(100, 60, text)


def make_runner(items, click_ok=True, nav_click=True):
    r = gui_flow.Runner(CFG, "discord")
    if not nav_click:
        r.acfg = dict(r.acfg)
        r.acfg.pop("nav_click", None)
    clicks = []
    r.click_point = lambda point: clicks.append(point) or click_ok
    r.capture_ocr = lambda: (FRAME, list(items))

    def fake_capture_header_runs():
        # フェーズ4 是正: check_recipient_header は capture_header_runs
        # (切り出し OCR) 経由になった。切り出し画像にはヘッダ帯だけが
        # 写るため、従来フィクスチャの項目からヘッダ領域の run 群
        # (従来と同じ読み) を作って返す。
        runs = gui_flow.header_region_runs(
            list(items),
            FRAME[2],
            r.talk_list_width_ratio(),
            float(r.tunables["relearn_header_max_y"]),
            float(r.tunables["talk_row_merge_px"]),
        )
        return (FRAME, runs)

    r.capture_header_runs = fake_capture_header_runs
    r._test_clicks = clicks
    return r


def header_check(r):
    found = [c for c in r.checks if c["name"] == "recipient_header"]
    assert found, "checks に recipient_header の記録が無い"
    return found[-1]


def test_check_header_exact_passes_and_clicks_nav():
    r = make_runner([header_item("example-dm")])
    assert r.check_recipient_header() is True
    assert r._test_clicks == [[174, 350]]
    assert header_check(r)["ok"] is True
    assert "exact" in header_check(r)["evidence"]
    assert r.recipient_header_error is None
    assert r.wrong_recipient is None


def test_check_header_prefix_passes():
    r = make_runner([header_item("example-dm4呼")])  # 余り 2 文字
    assert r.check_recipient_header() is True
    assert "prefix" in header_check(r)["evidence"]


def test_check_header_list_name_only_fails_as_unreadable():
    # フェーズ3 (複数回判定) で仕様変更: ヘッダの "someone-else" は
    # 登録済み宛先の正式名ではないため OTHER ではなく UNREADABLE に
    # 分類され、全回 UNREADABLE は recipient_header_unreadable で
    # 失敗する (wrong_recipient になるのは登録済み別宛先の正式名が
    # 読めた場合だけ。test_header_multi_impl.py で担保)。
    r = make_runner([list_item("example-dm"), header_item("someone-else")])
    assert r.check_recipient_header() is False
    assert header_check(r)["ok"] is False
    assert r.recipient_header_error == "recipient_header_unreadable"
    assert r.wrong_recipient is None
    assert "someone-else" in header_check(r)["evidence"]


def test_check_header_unreadable_when_capture_fails():
    r = make_runner([])
    r.capture_ocr = lambda: (None, [])
    # フェーズ4 是正: 撮影経路は capture_header_runs に移った
    r.capture_header_runs = lambda: (None, [])
    assert r.check_recipient_header() is False
    assert header_check(r)["ok"] is False
    assert r.recipient_header_error == "recipient_header_unreadable"
    assert r.wrong_recipient is None


def test_check_header_click_failure_skips_capture():
    r = make_runner([header_item("example-dm")], click_ok=False)

    def boom():
        raise AssertionError("クリック失敗時は撮影してはいけない")

    r.capture_ocr = boom
    # フェーズ4 是正: 撮影経路は capture_header_runs に移った
    r.capture_header_runs = boom
    assert r.check_recipient_header() is False
    assert header_check(r)["ok"] is False
    assert r.recipient_header_error == "recipient_header_unreadable"


def test_check_header_without_nav_click_still_checks():
    r = make_runner([header_item("example-dm")], nav_click=False)
    assert r.check_recipient_header() is True
    assert r._test_clicks == []
