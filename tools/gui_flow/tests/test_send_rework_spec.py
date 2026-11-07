"""仕様テスト: Discord 送信改修 (ad_flow 読み取り専用化 + gui_flow 分類).

実装コード (gui_flow.py / ad_flow.py 本体) を読まず、担当者間で確定した
契約 (完了の定義) だけから書いた受け入れテスト。

検証する契約:
  1. ad_flow の post 封鎖 (CLI 層): ad_flow.main(["discord", "post",
     "--text-file", <ファイル>]) は戻り値 2 で、標準出力に
     "ok": false / "error": "ad_flow_read_only" の JSON を 1 つ出し、
     理由文には「読み取り専用」と「gui_flow」の両方が含まれる。
     その際 MacAdClient は生成すらされない (Mac に一切触らない)。
  2. gui_flow の新分類関数 classify_app_unresponsive(error, checks,
     acfg, last_frame, tun=None): 全条件を満たすときだけ
     {"name": "app_unresponsive", "ok": False, "evidence": <再起動を含む>}
     を返し、それ以外は None を返す純関数。
  3. main 統合 (post のみ): discord post で Runner.input が
     input_status / fail_readback_empty で失敗し、実フレームが学習値と
     一致する場合、最終 JSON の error は "app_unresponsive_suspect" に
     なり、checks 末尾に name="app_unresponsive" が付く。input
     アクションでは従来通り "needs_calibration" のまま。

Mac 実機には一切アクセスしない (接続部はモックで切り分ける)。
宛先名・接続情報はすべて架空のテスト用ダミーである。

実行: python3 -m pytest tests/test_send_rework_spec.py -q
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ad_flow  # noqa: E402
import gui_flow  # noqa: E402

DISCORD_TEXT = """```
[SYSTEM] AIエージェントからお知らせ
これはテストです。
送信はしません。
※注意
確認が終わったら消します。
返信は不要です。
```"""

LEARNED_FRAME = [0, 30, 1440, 870]

DISCORD_CFG = {
    "app_name": "Discord",
    "window_app": "Discord",
    "process": "Discord",
    "input_click": [700, 852],
    "nav_click": [174, 350],
    "recipient_aliases": ["example-dm"],
    "learned_frame": list(LEARNED_FRAME),
}
LINE_CFG = {
    "app_name": "LINE",
    "window_app": "LINE",
    "process": "LINE",
    "input_click": [800, 780],
    "nav_click": [210, 160],
    "recipient_aliases": ["example-talk"],
}
DUMMY_SSH = {
    "user": "test-user",
    "host": "192.0.2.1",
    "identity": "/tmp/id_test",
    "proxy_script": "/tmp/proxy.sh",
    "control_path": "/tmp/gf-test-ctl",
}
FULL_CFG = {
    "ssh": DUMMY_SSH,
    "apps": {"discord": DISCORD_CFG, "line": LINE_CFG},
}


def _no_sleep(monkeypatch):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)


# ===========================================================================
# 契約 1: ad_flow の post 封鎖 (CLI 層)
# ===========================================================================


def _ad_text_file(tmp_path):
    msg = tmp_path / "ad-msg.txt"
    msg.write_text("テスト用ダミー本文です。\n", encoding="utf-8")
    return msg


class _ExplodingMacAdClient:
    """生成されたら即例外。生成回数も数える (Mac に触らないことの検証)。"""

    instances = 0

    def __init__(self, *args, **kwargs):
        type(self).instances += 1
        raise AssertionError("読み取り専用封鎖より前に MacAdClient が生成された")


def _run_ad_post(monkeypatch, capsys, tmp_path):
    _ExplodingMacAdClient.instances = 0
    monkeypatch.setattr(ad_flow, "MacAdClient", _ExplodingMacAdClient)
    msg = _ad_text_file(tmp_path)
    ret = ad_flow.main(["discord", "post", "--text-file", str(msg)])
    out = capsys.readouterr().out
    return ret, out


def _all_strings(obj):
    """JSON 内の全文字列値 (入れ子含む) を平らに集める。"""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        found = []
        for v in obj.values():
            found.extend(_all_strings(v))
        return found
    if isinstance(obj, (list, tuple)):
        found = []
        for v in obj:
            found.extend(_all_strings(v))
        return found
    return []


def test_ad_flow_post_returns_read_only_error_json(tmp_path, monkeypatch, capsys):
    ret, out = _run_ad_post(monkeypatch, capsys, tmp_path)
    assert ret == 2
    # 標準出力は JSON が 1 つだけ (全体がそのままパースできること)
    data = json.loads(out)
    assert data["ok"] is False
    assert data["error"] == "ad_flow_read_only"
    # 理由文 (evidence 等の文字列フィールド) に両方の語が入っていること
    assert any(
        "読み取り専用" in s and "gui_flow" in s for s in _all_strings(data)
    ), data


def test_ad_flow_post_never_instantiates_mac_client(tmp_path, monkeypatch, capsys):
    ret, _out = _run_ad_post(monkeypatch, capsys, tmp_path)
    assert ret == 2
    # MacAdClient は生成すらされない (生成されれば例外でここへ来ない)
    assert _ExplodingMacAdClient.instances == 0


# ===========================================================================
# 契約 2: gui_flow.classify_app_unresponsive (純関数)
# ===========================================================================


def _acfg():
    return dict(DISCORD_CFG, learned_frame=list(LEARNED_FRAME))


def _input_fail_check():
    return {
        "name": "input_status",
        "ok": False,
        "evidence": "status=fail_readback_empty pid=1234",
    }


def _classify(error, checks, acfg=None, frame=None):
    return gui_flow.classify_app_unresponsive(
        error, checks, acfg if acfg is not None else _acfg(), frame
    )


def test_classify_app_unresponsive_matches_all_conditions():
    result = _classify(
        "pre_send_failed_stop",
        [_input_fail_check()],
        frame=tuple(LEARNED_FRAME),
    )
    assert result is not None
    assert result["name"] == "app_unresponsive"
    assert result["ok"] is False
    assert isinstance(result["evidence"], str)
    assert "再起動" in result["evidence"]


def test_classify_app_unresponsive_ignores_passing_checks():
    # 失敗チェックが input_status だけなら、成功チェックが混ざっても分類する
    # (main の checks には connect / approval 等の成功チェックが並ぶため)。
    checks = [
        {"name": "connect", "ok": True, "evidence": "status=ok"},
        _input_fail_check(),
        {"name": "approval", "ok": True, "evidence": "承認済み"},
    ]
    result = _classify("pre_send_failed_stop", checks, frame=tuple(LEARNED_FRAME))
    assert result is not None
    assert result["name"] == "app_unresponsive"
    assert result["ok"] is False


def test_classify_app_unresponsive_frame_at_tolerance_boundary():
    # 各辺差の最大値が許容差 (既定 40px) ちょうどなら分類対象。
    # tun はキーワードでも渡せること (仕様のシグネチャどおり)。
    frame = (
        LEARNED_FRAME[0] + 40,
        LEARNED_FRAME[1] + 40,
        LEARNED_FRAME[2] - 40,
        LEARNED_FRAME[3] - 40,
    )
    result = gui_flow.classify_app_unresponsive(
        "pre_send_failed_stop",
        [_input_fail_check()],
        _acfg(),
        frame,
        tun=None,
    )
    assert result is not None
    assert result["name"] == "app_unresponsive"


@pytest.mark.parametrize(
    "frame",
    [
        (200, 30, 1440, 870),  # x が大きくずれている
        (41, 30, 1440, 870),  # x が許容差を 1px 超えている
        (0, 30, 1440, 911),  # 高さが許容差を 1px 超えている
    ],
)
def test_classify_app_unresponsive_frame_beyond_tolerance_returns_none(frame):
    result = _classify("pre_send_failed_stop", [_input_fail_check()], frame=frame)
    assert result is None


def test_classify_app_unresponsive_none_frame_returns_none():
    result = _classify("pre_send_failed_stop", [_input_fail_check()], frame=None)
    assert result is None


def test_classify_app_unresponsive_mixed_failures_returns_none():
    checks = [
        _input_fail_check(),
        {"name": "body_match", "ok": False, "evidence": "行単位照合 NG"},
    ]
    result = _classify("pre_send_failed_stop", checks, frame=tuple(LEARNED_FRAME))
    assert result is None


def test_classify_app_unresponsive_approval_missing_returns_none():
    result = _classify(
        "approval_missing", [_input_fail_check()], frame=tuple(LEARNED_FRAME)
    )
    assert result is None


def test_classify_app_unresponsive_field_readback_pattern_returns_none():
    # send 経路の field_readback / field_empty は似ていても対象外。
    checks = [
        {
            "name": "field_readback",
            "ok": False,
            "evidence": "欄の読み戻し不可: status=field_empty",
        }
    ]
    assert _classify("pre_send_failed_stop", checks, frame=tuple(LEARNED_FRAME)) is None
    assert (
        _classify("field_readback_failed", checks, frame=tuple(LEARNED_FRAME)) is None
    )


def test_classify_app_unresponsive_other_input_status_returns_none():
    # input_status でも fail_readback_empty 以外の失敗は対象外。
    checks = [
        {
            "name": "input_status",
            "ok": False,
            "evidence": "status=app_not_running pid=?",
        }
    ]
    result = _classify("pre_send_failed_stop", checks, frame=tuple(LEARNED_FRAME))
    assert result is None


# ===========================================================================
# 契約 3: main 統合 (post のみ)
# ===========================================================================


def _write_main_cfg(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.local.json"
    cfg_file.write_text(json.dumps(FULL_CFG), encoding="utf-8")
    monkeypatch.setenv("GUI_FLOW_CONFIG", str(cfg_file))
    monkeypatch.setenv("GUI_FLOW_APPROVALS", str(tmp_path / "approvals"))
    monkeypatch.setenv("GUI_FLOW_SCREENSHOTS", str(tmp_path / "shots"))
    monkeypatch.setenv("GUI_FLOW_LEARNED", str(tmp_path / "learned.jsonl"))
    msg = tmp_path / "msg.txt"
    msg.write_text(DISCORD_TEXT, encoding="utf-8")
    return msg


def _run_main(monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        gui_flow.main()
    out = capsys.readouterr().out
    return exc.value.code, json.loads(out)


def _mac_ok(monkeypatch):
    monkeypatch.setattr(
        gui_flow.Mac,
        "run",
        lambda self, cmd, stdin_text=None, timeout=None: (0, "GF_OK\n", ""),
    )


def _fake_input_readback_empty(self, text):
    """Runner.input の差し替え: 実フレームは学習値と一致、読み戻し空で失敗。"""
    self.last_frame = tuple(LEARNED_FRAME)
    self.checks.append(
        {
            "name": "input_status",
            "ok": False,
            "evidence": "status=fail_readback_empty pid=1234",
        }
    )
    return False


def _stub_post_surroundings(monkeypatch):
    """post 前段 (遷移確認・ヘッダ確認) と後始末を Mac なしで素通しさせる。

    検証対象は Runner.input の失敗に対する main の分類だけなので、
    それ以外の Runner 手順は成功扱いで固定する。送信キーには進ませない。
    """
    monkeypatch.setattr(gui_flow.Runner, "input", _fake_input_readback_empty)
    monkeypatch.setattr(gui_flow.Runner, "scrutinize_nav", lambda self: True)
    monkeypatch.setattr(gui_flow.Runner, "check_recipient_header", lambda self: True)
    monkeypatch.setattr(
        gui_flow.Runner,
        "verify",
        lambda self, text, mode, fetch_png=False: True,
    )
    monkeypatch.setattr(gui_flow.Runner, "clear", lambda self: True)
    monkeypatch.setattr(gui_flow.Runner, "cleanup_wrong_recipient", lambda self: True)

    def _no_send(self):
        raise AssertionError("送信前失敗なのに送信キーへ進んではいけない")

    monkeypatch.setattr(gui_flow.Runner, "send", _no_send)
    monkeypatch.setattr(
        gui_flow.Runner, "store_post_send_screenshot", lambda self: None
    )


def test_main_post_input_readback_empty_with_matching_frame_is_app_unresponsive_suspect(
    tmp_path, monkeypatch, capsys
):
    msg = _write_main_cfg(tmp_path, monkeypatch)
    _mac_ok(monkeypatch)
    _no_sleep(monkeypatch)
    gui_flow.create_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    _stub_post_surroundings(monkeypatch)

    code, out = _run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "discord", "post", "--text-file", str(msg)],
    )
    assert code == 2
    assert out["ok"] is False
    assert out["error"] == "app_unresponsive_suspect"
    assert out["checks"][-1]["name"] == "app_unresponsive"
    assert out["checks"][-1]["ok"] is False
    assert "再起動" in out["checks"][-1]["evidence"]


def test_main_input_same_failure_stays_needs_calibration(tmp_path, monkeypatch, capsys):
    # 対照: 同じ失敗でも action が input なら従来通り needs_calibration
    # (app_unresponsive への差し替えは post のみ)。
    msg = _write_main_cfg(tmp_path, monkeypatch)
    _mac_ok(monkeypatch)
    _no_sleep(monkeypatch)
    monkeypatch.setattr(gui_flow.Runner, "input", _fake_input_readback_empty)

    code, out = _run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "discord", "input", "--text-file", str(msg)],
    )
    assert code == 2
    assert out["ok"] is False
    assert out["error"] == "needs_calibration"
    assert out["checks"][-1]["name"] == "calibration"
    assert all(c["name"] != "app_unresponsive" for c in out["checks"])


def test_main_line_post_input_readback_empty_with_matching_frame_stays_needs_calibration(
    tmp_path, monkeypatch, capsys
):
    # 対照 (LINE 回帰ガード): discord post と全く同じ失敗
    # (input_status / fail_readback_empty + 実フレームが学習値と一致)
    # でも、LINE post では app_unresponsive への差し替えは起きず、
    # 従来通り needs_calibration のままであること
    # (仕様: LINE 側の挙動に一切変更を入れない)。
    line_text = (
        "【AIエージェントからのお知らせ】\n" "これはテストです。\n" "送信はしません。"
    )
    line_frame = [10, 40, 1200, 800]
    line_cfg = dict(LINE_CFG, learned_frame=list(line_frame))
    full_cfg = {
        "ssh": DUMMY_SSH,
        "apps": {"discord": DISCORD_CFG, "line": line_cfg},
    }
    cfg_file = tmp_path / "config.local.json"
    cfg_file.write_text(json.dumps(full_cfg), encoding="utf-8")
    monkeypatch.setenv("GUI_FLOW_CONFIG", str(cfg_file))
    monkeypatch.setenv("GUI_FLOW_APPROVALS", str(tmp_path / "approvals"))
    monkeypatch.setenv("GUI_FLOW_SCREENSHOTS", str(tmp_path / "shots"))
    monkeypatch.setenv("GUI_FLOW_LEARNED", str(tmp_path / "learned.jsonl"))
    msg = tmp_path / "line-msg.txt"
    msg.write_text(line_text, encoding="utf-8")

    _mac_ok(monkeypatch)
    _no_sleep(monkeypatch)
    gui_flow.create_approval("line", line_cfg, line_text)

    def _fake_line_input(self, text):
        self.last_frame = tuple(line_frame)
        self.checks.append(
            {
                "name": "input_status",
                "ok": False,
                "evidence": "status=fail_readback_empty pid=1234",
            }
        )
        return False

    monkeypatch.setattr(gui_flow.Runner, "input", _fake_line_input)
    monkeypatch.setattr(gui_flow.Runner, "scrutinize_nav", lambda self: True)
    monkeypatch.setattr(gui_flow.Runner, "check_recipient_header", lambda self: True)
    monkeypatch.setattr(
        gui_flow.Runner,
        "verify",
        lambda self, text, mode, fetch_png=False: True,
    )
    monkeypatch.setattr(gui_flow.Runner, "clear", lambda self: True)
    monkeypatch.setattr(gui_flow.Runner, "cleanup_wrong_recipient", lambda self: True)

    def _no_send(self):
        raise AssertionError("送信前失敗なのに送信キーへ進んではいけない")

    monkeypatch.setattr(gui_flow.Runner, "send", _no_send)
    monkeypatch.setattr(
        gui_flow.Runner, "store_post_send_screenshot", lambda self: None
    )

    code, out = _run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "line", "post", "--text-file", str(msg)],
    )
    assert code == 2
    assert out["ok"] is False
    assert out["error"] == "needs_calibration"
    assert out["checks"][-1]["name"] == "calibration"
    assert all(c["name"] != "app_unresponsive" for c in out["checks"])
