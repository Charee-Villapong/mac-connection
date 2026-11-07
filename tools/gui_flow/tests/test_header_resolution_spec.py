"""特性テスト (現状固定): ヘッダ関連の設定解決の優先順位・既定値
(候補 C-028 / 候補リスト候補5)。

テスト係が仕様 (候補リスト mac-connection 候補5、README「調整用変数」
の節) だけから作成した。実装コード (gui_flow.py 本体) は読んでいない。
候補5 の実施内容は「talk_list_width_ratio の解決 (3実装) と
relearn_header_prefix_max_extra の解決 (3か所 + 直接参照1か所) を
共通ヘルパーへ寄せる」こと。共通化で解決順や解決元が静かに変わら
ないよう、現行の解決行列を公開インターフェース経由で固定する。

既存テストで既に厚い部分 (本ファイルでは重ねない):
  - talk_list_width_ratio: 再学習経路のアプリ別解決は
    test_talk_list_ratio_spec.py、crop スクリプトの比率は
    test_header_crop_spec.py / test_header_discord_spec.py、
    nav 精査のアプリ別比率は test_nav_scrutiny_* が固定済み。
  - relearn_header_prefix_max_extra: 再学習経路の既定 2 と
    tunables 上書き (0 / 3 / 5) は test_header_prefix_spec.py が
    固定済み。

本ファイルが埋める薄い箇所:
  1. Runner.talk_list_width_ratio() そのものの解決優先順位
     (アプリ別 > 共通 tunables > 既定 0.32) の直接固定。
  2. run_verify_checks の比率解決: アプリ別が無い場合に tun 引数の
     共通値へフォールバックすること、アプリ別が tun 引数に勝つこと
     (既存 spec はアプリ別上書きと既定しか固定していない)。
  3. run_verify_checks の prefix 余り上限: tun 引数で解決される
     (0 で余り2も不可、3 で余り3も可) こと。既存 spec は既定 2 の
     挙動のみ固定。
  4. 現行の解決元不整合の固定: run_verify_checks ではアプリ別
     acfg 直下の relearn_header_prefix_max_extra は参照されない
     (置いても上限は既定 2 のまま)。共通化の際にこの経路だけ
     解決元が変わると挙動が変わるため、現状として明記する。
  5. Runner.check_recipient_header (verify/post 経路) の prefix
     上限が config の tunables で解決されること (再学習経路の
     固定は既存 spec にあるが、こちらは既定値ケースのみだった)。

宛先名はすべて架空のテスト用名称である。実機にはアクセスしない。

実行: python3 -m pytest tests/test_header_resolution_spec.py -q
"""

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

WIN = (767, 0, 30, 1440, 870)
FRAME = (0, 30, 1440, 870)

LINE_TEXT = (
    "【AIエージェントからのお知らせ】\n"
    "これはテストです。\n"
    "送信はしません。\n"
    "確認が終わったら消します。"
)
ALIAS = "テスト花子"


def _ocr_line(x, y, w, h, text):
    return f"TEXT [x={x} y={y} w={w} h={h} conf=1.00] {text}"


def _line_ocr(header_text):
    """ヘッダ項目 (中心 x=600) + 本文 (低位置) の全体 OCR。"""
    items = [_ocr_line(500, 60, 200, 30, header_text)]
    items += [
        _ocr_line(500, 700 + 30 * i, 300, 30, ln)
        for i, ln in enumerate(LINE_TEXT.split("\n"))
    ]
    return "\n".join(items)


def _line_acfg(**extra):
    acfg = {
        "app_name": "LINE",
        "window_app": "LINE",
        "process": "LINE",
        "input_click": [800, 780],
        "nav_click": [210, 160],
        "recipient_aliases": [ALIAS],
    }
    acfg.update(extra)
    return acfg


def _recipient_check(acfg, header_text, tun=None):
    if tun is None:
        result = gui_flow.run_verify_checks(
            "line", acfg, _line_ocr(header_text), WIN, LINE_TEXT, "field"
        )
    else:
        result = gui_flow.run_verify_checks(
            "line", acfg, _line_ocr(header_text), WIN, LINE_TEXT, "field", tun=tun
        )
    return next(c for c in result if c["name"] == "recipient")


def _tun(**overrides):
    tun = dict(gui_flow.DEFAULT_TUNABLES)
    tun.update(overrides)
    return tun


# --- 1. Runner.talk_list_width_ratio() の解決優先順位 ---------------------------


def _runner_ratio(app_ratio=None, tun_ratio=None):
    app = {
        "app_name": "LINE",
        "window_app": "LINE",
        "process": "LINE",
        "input_click": [800, 780],
        "nav_click": [210, 160],
        "recipient_aliases": ["example-talk"],
    }
    if app_ratio is not None:
        app["talk_list_width_ratio"] = app_ratio
    cfg = {"ssh": dict(DUMMY_SSH), "apps": {"line": app}}
    if tun_ratio is not None:
        cfg["tunables"] = {"talk_list_width_ratio": tun_ratio}
    return gui_flow.Runner(cfg, "line").talk_list_width_ratio()


def test_runner_ratio_resolution_priority_and_default():
    # アプリ別値が共通 tunables に勝つ
    assert _runner_ratio(app_ratio=0.27, tun_ratio=0.32) == 0.27
    # アプリ別が無ければ共通 tunables の値
    assert _runner_ratio(app_ratio=None, tun_ratio=0.47) == 0.47
    # どちらも無ければ既定 0.32
    assert _runner_ratio(app_ratio=None, tun_ratio=None) == 0.32
    # アプリ別だけでも効く
    assert _runner_ratio(app_ratio=0.27, tun_ratio=None) == 0.27


# --- 2. run_verify_checks の比率解決 (tun へフォールバック / アプリ別優先) ------


def test_verify_ratio_falls_back_to_tun_and_app_beats_tun():
    # ヘッダ項目の中心 x=600: 比率 0.32 の境界 (460.8) より右 = ヘッダ側、
    # 比率 0.5 の境界 (720) より左 = 一覧側 (recipient 不合格)。
    # アプリ別が無い場合は tun 引数の共通値で解決される (現行)
    check = _recipient_check(_line_acfg(), ALIAS, tun=_tun(talk_list_width_ratio=0.5))
    assert check["ok"] is False
    check = _recipient_check(_line_acfg(), ALIAS, tun=_tun(talk_list_width_ratio=0.32))
    assert check["ok"] is True
    # アプリ別がある場合は tun 引数よりアプリ別が優先される (現行)
    check = _recipient_check(
        _line_acfg(talk_list_width_ratio=0.32),
        ALIAS,
        tun=_tun(talk_list_width_ratio=0.5),
    )
    assert check["ok"] is True
    check = _recipient_check(
        _line_acfg(talk_list_width_ratio=0.5),
        ALIAS,
        tun=_tun(talk_list_width_ratio=0.32),
    )
    assert check["ok"] is False


# --- 3. run_verify_checks の prefix 上限は tun 引数で解決される ------------------


def test_verify_prefix_max_extra_resolved_from_tun():
    # 既定 (tun 無し): 余り 2 は可、余り 3 は不可 (既存 spec と同じ前提)
    assert _recipient_check(_line_acfg(), ALIAS + "4呼")["ok"] is True
    assert _recipient_check(_line_acfg(), ALIAS + "さんへ")["ok"] is False
    # tun で 0 にすると余り 2 でも不可になる (現行)
    check = _recipient_check(
        _line_acfg(), ALIAS + "4呼", tun=_tun(relearn_header_prefix_max_extra=0)
    )
    assert check["ok"] is False
    # tun で 3 にすると余り 3 でも prefix で可になる (現行)
    check = _recipient_check(
        _line_acfg(), ALIAS + "さんへ", tun=_tun(relearn_header_prefix_max_extra=3)
    )
    assert check["ok"] is True
    assert "prefix" in check["evidence"]


# --- 4. 現行の解決元不整合: verify では acfg 直下の同名キーは参照されない --------


def test_verify_prefix_max_extra_acfg_key_is_not_consulted():
    # acfg 直下に 0 を置いても、verify の上限は既定 2 のまま (現行)
    check = _recipient_check(
        _line_acfg(relearn_header_prefix_max_extra=0), ALIAS + "4呼"
    )
    assert check["ok"] is True
    # acfg 直下に 3 を置いても、余り 3 は可にならない (現行)
    check = _recipient_check(
        _line_acfg(relearn_header_prefix_max_extra=3), ALIAS + "さんへ"
    )
    assert check["ok"] is False


# --- 5. check_recipient_header の prefix 上限は config の tunables で解決 --------


def _header_runner(monkeypatch, extra_tunables=None):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    cfg = {
        "ssh": dict(DUMMY_SSH),
        "apps": {
            "discord": {
                "app_name": "Discord",
                "window_app": "Discord",
                "process": "Discord",
                "input_click": [700, 852],
                "nav_click": [174, 350],
                "recipient_aliases": ["example-dm"],
            }
        },
    }
    if extra_tunables:
        cfg["tunables"] = dict(extra_tunables)
    runner = gui_flow.Runner(cfg, "discord")
    items = [
        {"x": 500, "y": 60, "w": 200, "h": 20, "conf": 1.0, "text": "example-dm4呼"}
    ]

    def fake_capture_header_runs():
        runs = gui_flow.header_region_runs(
            list(items),
            FRAME[2],
            runner.talk_list_width_ratio(),
            float(runner.tunables["relearn_header_max_y"]),
            float(runner.tunables["talk_row_merge_px"]),
        )
        return (FRAME, runs)

    runner.capture_header_runs = fake_capture_header_runs
    runner.click_point = lambda point: True
    return runner


def test_check_recipient_header_prefix_limit_uses_cfg_tunables(monkeypatch):
    # 既定: 余り 2 文字は prefix で通過 (既存 impl テストと同じ前提)
    runner = _header_runner(monkeypatch)
    assert runner.check_recipient_header() is True
    # config の tunables で 0 にすると同じヘッダでも通過しない (現行)
    runner = _header_runner(
        monkeypatch, extra_tunables={"relearn_header_prefix_max_extra": 0}
    )
    assert runner.check_recipient_header() is False
