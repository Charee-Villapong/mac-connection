"""ヘッダ複数回判定 (フェーズ3) の実装側回帰テスト。

仕様テスト (test_header_multi_spec.py) が担保する受け入れ条件に加え、
実装の分岐 (tunables の既定値・上書き、OTHER 正式名の取り方、
撮影間隔の sleep、pre_send_once の record への伝搬、撮影全滅時の
verify_capture) を固定する。Mac 接続部分はモックで切り分ける。

実行: python3 -m pytest tests/test_header_multi_impl.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gui_flow  # noqa: E402

WIN = (767, 0, 30, 1440, 870)
FRAME = WIN[1:]

TARGET = "テスト花子"
OTHER = "テスト太郎"
OTHER_2 = "テスト次郎"
GARBLED = "テスト主小心"

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


def ocr_line(x, y, w, h, text):
    return f"TEXT [x={x} y={y} w={w} h={h} conf=1.00] {text}"


def header_ocr_line(text, y=60, x=500):
    return ocr_line(x, y, 200, 30, text)


def make_cfg(tunables=None):
    recipients = {
        TARGET: {"aliases": [TARGET, "花子"], "nav_click": [210, 160]},
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


def make_header_runner(monkeypatch, header_sequence, tunables=None, sleeps=None):
    if sleeps is not None:
        monkeypatch.setattr(gui_flow.time, "sleep", lambda s: sleeps.append(s))
    else:
        monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    runner = gui_flow.Runner(make_cfg(tunables), "line", to=TARGET)
    seq = list(header_sequence)
    state = {"count": 0}

    def fake_capture_ocr():
        state["count"] += 1
        header = seq.pop(0) if seq else GARBLED
        items = gui_flow.parse_ocr(header_ocr_line(header))
        return (FRAME, items)

    def fake_capture_header_runs():
        # フェーズ4 是正: check_recipient_header は capture_header_runs
        # (切り出し OCR) 経由になったため、同じ撮影列の run 群を返す。
        state["count"] += 1
        header = seq.pop(0) if seq else GARBLED
        items = gui_flow.parse_ocr(header_ocr_line(header))
        runs = gui_flow.text_runs(items, float(runner.tunables["talk_row_merge_px"]))
        return (FRAME, runs)

    runner.capture_ocr = fake_capture_ocr
    runner.capture_header_runs = fake_capture_header_runs
    runner.click_point = lambda point: True
    return runner, state


# --- tunables -----------------------------------------------------------------


def test_header_multi_tunables_defaults_and_override():
    t = gui_flow.tunables({})
    assert t["header_check_attempts"] == 3
    assert t["header_check_interval_sec"] == 0.5
    t2 = gui_flow.tunables(
        {"tunables": {"header_check_attempts": 5, "header_check_interval_sec": 0.1}}
    )
    assert t2["header_check_attempts"] == 5
    assert t2["header_check_interval_sec"] == 0.1


def test_header_multi_attempts_tunable_limits_captures(monkeypatch):
    runner, state = make_header_runner(
        monkeypatch,
        [GARBLED, GARBLED, GARBLED],
        tunables={"header_check_attempts": 2},
    )
    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "recipient_header_unreadable"
    assert state["count"] == 2


def test_header_multi_sleep_interval_between_captures(monkeypatch):
    sleeps = []
    runner, state = make_header_runner(
        monkeypatch, [TARGET, GARBLED, GARBLED], sleeps=sleeps
    )
    assert runner.check_recipient_header() is True
    assert state["count"] == 3
    # 撮影は 3 回、sleep は間の 2 回だけ (既定間隔 0.5 秒)
    assert sleeps == [0.5, 0.5]


def test_header_multi_sleep_stops_when_other_aborts(monkeypatch):
    sleeps = []
    runner, state = make_header_runner(
        monkeypatch, [TARGET, OTHER, TARGET], sleeps=sleeps
    )
    assert runner.check_recipient_header() is False
    assert state["count"] == 2
    assert sleeps == [0.5]  # 2 回目の撮影前の 1 回だけ


# --- OTHER 正式名の取り方 --------------------------------------------------------


def test_formal_names_of_others_excludes_target_and_short_aliases():
    recips = {
        TARGET: {"aliases": [TARGET, "花子"]},
        OTHER: {"aliases": [OTHER, "太郎"]},
        "id-x": {"aliases": ["表示名", "短い別名"]},
    }
    names = gui_flow.formal_names_of_others(recips, TARGET)
    assert OTHER in names  # 辞書キー = 正式名
    assert "id-x" in names  # キー表記
    assert "表示名" in names  # エイリアス先頭 (正式表記)
    assert "太郎" not in names  # 短いエイリアスでは判定しない
    assert "短い別名" not in names
    assert TARGET not in names
    assert "花子" not in names


def test_classify_match_takes_priority_over_other():
    # run が宛先エイリアスへの前方一致でもあり、別宛先の正式名への
    # 完全一致でもある場合、MATCH を先に判定して誤分類しない
    runs = [((600, 75), "テスト花子")]
    att = gui_flow.classify_header_attempt(
        runs, ["テスト花"], ["テスト花子"], prefix_max_extra=2
    )
    assert att["classification"] == "MATCH"


def test_check_header_other_formal_name_sets_wrong_recipient(monkeypatch):
    runner, state = make_header_runner(monkeypatch, [OTHER, TARGET, TARGET])
    assert runner.check_recipient_header() is False
    assert runner.recipient_header_error == "wrong_recipient"
    assert state["count"] == 1  # OTHER で即打ち切り
    assert runner.wrong_recipient == {
        "expected": [TARGET, "花子"],
        "actual_header": [OTHER],
        "cleaned": False,
    }
    check = [c for c in runner.checks if c["name"] == "recipient_header"][-1]
    assert check["recipient_error"] == "wrong_recipient"
    assert check["attempts"][0]["classification"] == "OTHER"


# --- verify: 撮影全滅と OTHER の属性伝搬 -----------------------------------------


def discord_ocr(header):
    lines = [ocr_line(421, 50, 200, 20, header)]
    y = 460
    for ln in DISCORD_BODY_LINES:
        lines.append(ocr_line(454, y, 200, 20, ln))
        y += 24
    return "\n".join(lines)


def make_verify_runner(monkeypatch, header_sequence):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    runner = gui_flow.Runner(make_cfg(), "discord", to=TARGET)
    seq = list(header_sequence)
    state = {"count": 0}

    def fake_run_queue(script, name, extras=(), timeout=None):
        state["count"] += 1
        header = seq.pop(0) if seq else GARBLED
        return {
            "status": "ok",
            "pid": "1234",
            "window": "767 0 30 1440 870",
            "_files": {
                "-ocr.txt": discord_ocr(header),
                # フェーズ4 是正: 切り出し OCR (-header-ocr.txt) を
                # 同じ回のヘッダ文字列で同梱する。
                "-header-ocr.txt": header_ocr_line(header),
                "-png.b64": "",
            },
            "_ssh_rc": 0,
        }

    runner.mac.run_queue = fake_run_queue
    return runner, state


def test_verify_other_aborts_and_sets_wrong_recipient_attr(monkeypatch):
    runner, state = make_verify_runner(monkeypatch, [OTHER, TARGET, TARGET])
    assert runner.verify(DISCORD_TEXT, "field") is False
    assert state["count"] == 1
    assert runner.recipient_header_error == "wrong_recipient"
    check = [c for c in runner.checks if c["name"] == "recipient"][-1]
    assert check["ok"] is False
    assert check["recipient_error"] == "wrong_recipient"
    assert len([c for c in runner.checks if c["name"] == "recipient"]) == 1


def test_verify_all_captures_failed_is_verify_capture(monkeypatch):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    runner = gui_flow.Runner(make_cfg(), "discord", to=TARGET)
    calls = {"count": 0}

    def fake_run_queue(script, name, extras=(), timeout=None):
        calls["count"] += 1
        return {"status": "window_not_found", "_files": {}, "_ssh_rc": 0}

    runner.mac.run_queue = fake_run_queue
    assert runner.verify(DISCORD_TEXT, "field") is False
    assert calls["count"] == 3  # 全回撮り直してから諦める
    assert runner.recipient_header_error == "recipient_header_unreadable"
    check = [c for c in runner.checks if c["name"] == "verify_capture"][-1]
    assert check["ok"] is False


# --- pre_send_once: record への recipient_error 伝搬 ------------------------------


def _pre_send_runner(monkeypatch, header):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    runner = gui_flow.Runner(make_cfg(), "discord", to=TARGET)

    def fake_run_queue(script, name, extras=(), timeout=None):
        files = {"-readback.txt": DISCORD_TEXT, "-leftover.txt": ""}
        if "ocr.swift" in script:
            files["-ocr.txt"] = discord_ocr(header)
            # フェーズ4 是正: 切り出し OCR も同じヘッダ文字列で返す。
            files["-header-ocr.txt"] = header_ocr_line(header)
        return {
            "status": "ok",
            "pid": "1234",
            "window": "767 0 30 1440 870",
            "field_empty": "yes",
            "_files": files,
            "_ssh_rc": 0,
        }

    runner.mac.run_queue = fake_run_queue
    return runner


def test_pre_send_once_record_carries_unreadable_recipient_error(monkeypatch):
    runner = _pre_send_runner(monkeypatch, GARBLED)
    ok, retryable, record = runner.pre_send_once(DISCORD_TEXT)
    assert ok is False
    assert retryable is False
    assert "recipient" in record["failed_checks"]
    assert record["recipient_error"] == "recipient_header_unreadable"


def test_pre_send_once_record_carries_wrong_recipient_error(monkeypatch):
    runner = _pre_send_runner(monkeypatch, OTHER)
    ok, retryable, record = runner.pre_send_once(DISCORD_TEXT)
    assert ok is False
    assert retryable is False
    assert "recipient" in record["failed_checks"]
    assert record["recipient_error"] == "wrong_recipient"
