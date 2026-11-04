"""scrutinize_nav の実装側回帰テスト (Mac 接続はモックで切り分け)。

仕様テスト (test_nav_scrutiny_spec.py) が担保する公開仕様に加え、
Discord でも同じ共有経路で精査が効くこと、修正クリック失敗時の停止、
nav_click 未設定時の通過、許容差の境界を実装側で固定する。

実行: python3 -m pytest tools/gui_flow/tests -q
"""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gui_flow  # noqa: E402

FRAME = (0, 30, 1440, 850)

SSH_CFG = {
    "user": "test-user",
    "host": "192.0.2.1",
    "identity": "/tmp/id_test",
    "proxy_script": "/tmp/proxy.sh",
    "control_path": "/tmp/gf-test-ctl",
}


def make_cfg(app, saved_nav, ratio):
    return {
        "ssh": dict(SSH_CFG),
        "apps": {
            app: {
                "app_name": app,
                "window_app": app,
                "process": app,
                "input_click": [700, 800],
                "talk_list_width_ratio": ratio,
                "default_recipient": "hanako",
                "recipients": {
                    "hanako": {
                        "aliases": ["テスト花子"],
                        "nav_click": list(saved_nav),
                    }
                },
            }
        },
    }


def item(text, center_y, x=100, w=120):
    # 中心が (x + w/2, center_y) (画面絶対座標) になる OCR 項目 (窓相対)
    return {
        "x": x,
        "y": center_y - FRAME[1] - 12,
        "w": w,
        "h": 24,
        "conf": 0.99,
        "text": text,
    }


def make_runner(monkeypatch, app, saved_nav, ratio, capture_result, click_ok=True):
    cfg = make_cfg(app, saved_nav, ratio)
    runner = gui_flow.Runner(cfg, app, to="hanako")
    record = {"clicks": [], "saved": [], "learned": []}
    monkeypatch.setattr(
        gui_flow,
        "save_config",
        lambda cfg_arg=None, *a, **k: record["saved"].append(copy.deepcopy(cfg_arg)),
    )
    monkeypatch.setattr(
        gui_flow,
        "append_learned_case",
        lambda rec=None, *a, **k: record["learned"].append(copy.deepcopy(rec)),
    )
    runner.capture_ocr = lambda: capture_result

    def fake_click(point, *a, **k):
        record["clicks"].append(tuple(point))
        return click_ok

    runner.click_point = fake_click
    return runner, record


def nav_check(runner):
    found = [c for c in runner.checks if c["name"] == "nav_scrutiny"]
    assert found, "checks に nav_scrutiny の記録が無い"
    return found[-1]


def test_discord_pass_uses_app_specific_list_ratio(monkeypatch):
    # Discord の一覧幅比 0.32 でも、一覧内の完全一致行で通過する
    items = [item("テスト太郎", 180), item("テスト花子", 252)]
    runner, record = make_runner(
        monkeypatch, "discord", (160, 252), 0.32, (FRAME, items)
    )
    assert runner.scrutinize_nav() is True
    assert nav_check(runner)["ok"] is True
    assert record["clicks"] == []
    assert record["saved"] == []


def test_discord_mismatch_corrects_nav_click(monkeypatch):
    # Discord でも保存座標が別人の行なら、本人の行へ更新される
    items = [item("テスト太郎", 180), item("テスト花子", 252)]
    runner, record = make_runner(
        monkeypatch, "discord", (160, 180), 0.32, (FRAME, items)
    )
    assert runner.scrutinize_nav() is True
    assert record["clicks"] == [(160, 252)]
    assert tuple(runner.acfg["nav_click"]) == (160, 252)
    assert record["saved"], "save_config が呼ばれていない"
    assert record["learned"], "append_learned_case が呼ばれていない"


def test_click_failure_stops_without_saving(monkeypatch):
    items = [item("テスト太郎", 180), item("テスト花子", 252)]
    runner, record = make_runner(
        monkeypatch, "line", (160, 180), 0.27, (FRAME, items), click_ok=False
    )
    assert runner.scrutinize_nav() is False
    assert runner.nav_scrutiny_error == "nav_scrutiny_click_failed"
    assert nav_check(runner)["ok"] is False
    assert record["saved"] == []
    assert record["learned"] == []
    assert tuple(runner.acfg["nav_click"]) == (160, 180)


def test_unset_nav_click_passes_with_single_candidate(monkeypatch):
    items = [item("テスト花子", 252)]
    runner, record = make_runner(monkeypatch, "line", (160, 180), 0.27, (FRAME, items))
    runner.acfg = dict(runner.acfg)
    runner.acfg.pop("nav_click", None)
    assert runner.scrutinize_nav() is True
    assert nav_check(runner)["ok"] is True
    assert record["clicks"] == []
    assert record["saved"] == []


def test_empty_items_is_unreadable(monkeypatch):
    runner, record = make_runner(monkeypatch, "line", (160, 180), 0.27, (FRAME, []))
    assert runner.scrutinize_nav() is False
    assert runner.nav_scrutiny_error == "nav_scrutiny_unreadable"
    assert nav_check(runner)["ok"] is False
    assert record["clicks"] == []


def test_tolerance_boundary_is_inclusive(monkeypatch):
    # 縦座標差が許容差 (既定 36px) ちょうどなら同じ行として通過する
    items = [item("テスト花子", 216)]
    runner, record = make_runner(monkeypatch, "line", (160, 180), 0.27, (FRAME, items))
    assert runner.scrutinize_nav() is True
    assert record["clicks"] == []
    assert record["saved"] == []
