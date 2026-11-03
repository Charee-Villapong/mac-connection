"""gui_flow の単体テスト (Mac 接続が要る部分はモックで切り分け)。

実行: python3 -m pytest tools/gui_flow/tests -q
"""

import base64
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gui_flow  # noqa: E402

DISCORD_TEXT = """```
[SYSTEM] AIエージェントからお知らせ
これはテストです。
送信はしません。
※注意
確認が終わったら消します。
返信は不要です。
```"""

LINE_TEXT = """【AIエージェントからのお知らせ】
これはテストです。
送信はしません。
確認が終わったら消します。"""

DISCORD_CFG = {
    "app_name": "Discord",
    "window_app": "Discord",
    "process": "Discord",
    "input_click": [700, 852],
    "nav_click": [174, 350],
    "recipient_aliases": ["example-dm"],
    "header_band": 110,
}
LINE_CFG = {
    "app_name": "LINE",
    "window_app": "LINE",
    "process": "LINE",
    "input_click": [800, 780],
    "nav_click": [210, 160],
    "recipient_aliases": ["example-talk"],
    "header_band": 110,
}
WIN = (767, 0, 30, 1440, 870)


def ocr_line(x, y, text, conf=1.00):
    return "TEXT [x=%d y=%d w=200 h=20 conf=%.2f] %s" % (x, y, conf, text)


def discord_ocr(body_lines=None, header="example-dm"):
    lines = [ocr_line(421, 50, header)]
    y = 460
    for ln in (
        body_lines
        if body_lines is not None
        else [
            "[SYSTEM] AIエージェントからお知らせ",
            "これはテストです。",
            "送信はしません。",
            "※注意",
            "確認が終わったら消します。",
            "返信は不要です。",
        ]
    ):
        lines.append(ocr_line(454, y, ln))
        y += 24
    return "\n".join(lines)


def checks_by_name(checks):
    return {c["name"]: c["ok"] for c in checks}


# --- 形式ルール -------------------------------------------------------------


def test_first_line_constants():
    assert gui_flow.FIRST_LINE["discord"] == "[SYSTEM] AIエージェントからお知らせ"
    assert gui_flow.FIRST_LINE["line"] == "【AIエージェントからのお知らせ】"
    # Discord の定型に 【】 の独立行は混ぜない
    assert "【" not in gui_flow.FIRST_LINE["discord"]


def test_content_lines_discord_strips_fences():
    lines = gui_flow.content_lines("discord", DISCORD_TEXT)
    assert lines[0] == "[SYSTEM] AIエージェントからお知らせ"
    assert "```" not in lines
    assert len(lines) == 6


def test_content_lines_line_keeps_notice_first():
    lines = gui_flow.content_lines("line", LINE_TEXT)
    assert lines[0] == "【AIエージェントからのお知らせ】"
    assert len(lines) == 4


# --- OCR 照合 (正常系) --------------------------------------------------------


def test_verify_discord_field_ok():
    checks = gui_flow.run_verify_checks(
        "discord", DISCORD_CFG, discord_ocr(), WIN, DISCORD_TEXT, "field"
    )
    got = checks_by_name(checks)
    assert got["recipient"] is True
    assert got["structure_codeblock"] is True
    assert got["first_line"] is True
    assert got["body_match"] is True
    assert got["field_state"] is True


def test_verify_line_field_ok():
    ocr = "\n".join(
        [
            ocr_line(144, 130, "example-talk"),
            ocr_line(454, 460, "【AIエージェントからのお知らせ】"),
            ocr_line(454, 484, "これはテストです。"),
            ocr_line(454, 508, "送信はしません。"),
            ocr_line(454, 532, "確認が終わったら消します。"),
        ]
    )
    checks = gui_flow.run_verify_checks("line", LINE_CFG, ocr, WIN, LINE_TEXT, "field")
    got = checks_by_name(checks)
    assert got["recipient"] is True
    assert got["first_line"] is True
    assert got["body_match"] is True


# --- OCR 照合 (異常系) --------------------------------------------------------


def test_verify_body_mismatch_fails():
    bad = discord_ocr(
        body_lines=[
            "[SYSTEM] AIエージェントからお知らせ",
            "これはテストです。",
            "送信はしません。",
            "※注意",
            "確認が終わったら消します。",
            "返信は必要です。",  # 期待文と違う
        ]
    )
    checks = gui_flow.run_verify_checks(
        "discord", DISCORD_CFG, bad, WIN, DISCORD_TEXT, "field"
    )
    got = checks_by_name(checks)
    assert got["body_match"] is False
    assert got["field_state"] is False


def test_verify_recipient_missing_fails():
    checks = gui_flow.run_verify_checks(
        "discord",
        DISCORD_CFG,
        discord_ocr(header="someone-else"),
        WIN,
        DISCORD_TEXT,
        "field",
    )
    assert checks_by_name(checks)["recipient"] is False


def test_verify_ocr_empty_fails():
    checks = gui_flow.run_verify_checks(
        "discord", DISCORD_CFG, "", WIN, DISCORD_TEXT, "field"
    )
    assert len(checks) == 1
    assert checks[0]["name"] == "ocr_readable"
    assert checks[0]["ok"] is False


def test_verify_line_rejects_system_first_line():
    # LINE の期待文が Discord 形式 ([SYSTEM] 始まり) だと 1 行目チェックで落ちる
    wrong_text = "[SYSTEM] AIエージェントからお知らせ\nこれはテストです。"
    ocr = "\n".join(
        [
            ocr_line(144, 130, "example-talk"),
            ocr_line(454, 460, "[SYSTEM] AIエージェントからお知らせ"),
            ocr_line(454, 484, "これはテストです。"),
        ]
    )
    checks = gui_flow.run_verify_checks("line", LINE_CFG, ocr, WIN, wrong_text, "field")
    assert checks_by_name(checks)["first_line"] is False


def test_verify_discord_structure_requires_fences():
    no_fence = DISCORD_TEXT.replace("```\n", "").rstrip("`").rstrip()
    checks = gui_flow.run_verify_checks(
        "discord", DISCORD_CFG, discord_ocr(), WIN, no_fence, "field"
    )
    assert checks_by_name(checks)["structure_codeblock"] is False


# --- OCR 解析・正規化 -----------------------------------------------------------


def test_parse_ocr_and_norm():
    items = gui_flow.parse_ocr(discord_ocr())
    assert len(items) == 7
    assert items[0]["text"] == "example-dm"
    # 全角ブラケットは NFKC で半角にそろう (OCR の ［SYSTEM］ 表記ゆれ対策)
    assert gui_flow.norm("［SYSTEM］ AIエージェント") == "[SYSTEM]AIエージェント"
    assert gui_flow.ratio("同じ文", "同じ文") == 1.0


# --- config 読み込み -------------------------------------------------------------


def test_load_config_from_env(tmp_path, monkeypatch):
    cfg = {
        "ssh": {
            "user": "test-user",
            "host": "192.0.2.1",
            "identity": "/tmp/id_test",
            "proxy_script": "/tmp/proxy.sh",
        },
        "apps": {"discord": DISCORD_CFG, "line": LINE_CFG},
    }
    p = tmp_path / "config.local.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("GUI_FLOW_CONFIG", str(p))
    loaded = gui_flow.load_config()
    assert loaded["ssh"]["user"] == "test-user"
    assert loaded["apps"]["discord"]["recipient_aliases"] == ["example-dm"]


def test_load_config_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("GUI_FLOW_CONFIG", str(tmp_path / "nope.json"))
    try:
        gui_flow.load_config()
    except SystemExit:
        return
    raise AssertionError("SystemExit expected")


# --- スクリプト生成 ---------------------------------------------------------------


def test_build_scripts_discord():
    s = gui_flow.build_input_script("gf-x.sh", "discord", DISCORD_CFG, DISCORD_TEXT)
    assert "keyf 36 shift" in s  # 改行は Shift+Enter
    assert "anomaly_leftover" in s  # 残りありは異常として停止
    assert "LC_ALL=en_US.UTF-8" in s  # pbpaste の文字コード対策
    send = gui_flow.build_send_script("gf-x.sh", "discord", DISCORD_CFG)
    assert "keyf 36 - pid" in send  # Discord の送信は素の Enter


def test_build_scripts_line():
    s = gui_flow.build_input_script("gf-x.sh", "line", LINE_CFG, LINE_TEXT)
    assert "keyf 36 shift" not in s  # LINE は type の LF で改行
    send = gui_flow.build_send_script("gf-x.sh", "line", LINE_CFG)
    assert "keyf 36 cmd pid" in send  # LINE の送信は Cmd+Enter


# --- Mac 通信 (モック) --------------------------------------------------------------

DUMMY_SSH_CFG = {
    "ssh": {
        "user": "test-user",
        "host": "192.0.2.1",
        "identity": "/tmp/id_test",
        "proxy_script": "/tmp/proxy.sh",
        "control_path": "/tmp/gf-test-ctl",
    },
    "apps": {"discord": DISCORD_CFG, "line": LINE_CFG},
}


def test_run_queue_parses_result_and_files(monkeypatch):
    mac = gui_flow.Mac(DUMMY_SSH_CFG)
    canned = (
        "pid=1234\nstatus=ok\nreadback_present=yes\n"
        "__GF_FILE__-readback.txt\n"
        "line1\nline2\n"
        "__GF_EOF__-readback.txt\n"
    )
    monkeypatch.setattr(
        mac, "run", lambda cmd, stdin_text=None, timeout=240: (0, canned, "")
    )
    res = mac.run_queue("body", "gf-x.sh", extras=("-readback.txt",))
    assert res["status"] == "ok"
    assert res["pid"] == "1234"
    assert res["_files"]["-readback.txt"].strip() == "line1\nline2"


# --- 送信前リトライの分類 ---------------------------------------------------------


def test_classify_pre_send_body_only_is_retryable():
    checks = [
        {"name": "recipient", "ok": True, "evidence": "宛先を確認"},
        {"name": "body_match", "ok": False, "evidence": "行単位照合: x (0.40)"},
        {"name": "field_state", "ok": False, "evidence": "期待文と一致しない"},
    ]
    failed, retryable, names, summary = gui_flow.classify_pre_send(checks)
    assert failed is True
    assert retryable is True
    assert names == ["body_match", "field_state"]
    assert "body_match" in summary


def test_classify_pre_send_recipient_is_not_retryable():
    checks = [
        {"name": "recipient", "ok": False, "evidence": "宛先が見当たらない"},
        {"name": "body_match", "ok": False, "evidence": "行単位照合: x (0.40)"},
    ]
    failed, retryable, names, _ = gui_flow.classify_pre_send(checks)
    assert failed is True
    assert retryable is False
    assert names == ["recipient", "body_match"]


def test_classify_pre_send_all_ok():
    checks = [{"name": "body_match", "ok": True, "evidence": "一致"}]
    failed, retryable, names, summary = gui_flow.classify_pre_send(checks)
    assert failed is False
    assert retryable is False
    assert names == []
    assert summary == ""


def test_pre_send_once_readback_mismatch_is_retryable(monkeypatch):
    r = gui_flow.Runner(DUMMY_SSH_CFG, "discord")
    canned = {
        "status": "ok",
        "pid": "1234",
        "_files": {"-readback.txt": "違う文面です", "-leftover.txt": ""},
        "_ssh_rc": 0,
    }
    monkeypatch.setattr(r.mac, "run_queue", lambda *a, **k: canned)
    ok, retryable, record = r.pre_send_once(DISCORD_TEXT)
    assert ok is False
    assert retryable is True
    assert record["failed_checks"] == ["readback_match"]


def test_pre_send_once_leftover_is_not_retryable(monkeypatch):
    r = gui_flow.Runner(DUMMY_SSH_CFG, "discord")
    canned = {
        "status": "anomaly_leftover",
        "pid": "1234",
        "_files": {"-leftover.txt": "他人の下書き", "-readback.txt": ""},
        "_ssh_rc": 0,
    }
    monkeypatch.setattr(r.mac, "run_queue", lambda *a, **k: canned)
    ok, retryable, record = r.pre_send_once(DISCORD_TEXT)
    assert ok is False
    assert retryable is False
    assert "field_empty_before" in record["failed_checks"]


# --- post の高速リトライ ------------------------------------------------------------


class FakeRunner:
    """run_post 用の疑似 Runner。pre_send_once の結果列をスクリプトする。"""

    def __init__(self, pre_results, send_ok=True, bubble_ok=True, clear_ok=True):
        self.pre_results = list(pre_results)
        self.send_ok = send_ok
        self.bubble_ok = bubble_ok
        self.clear_ok = clear_ok
        self.pre_calls = 0
        self.clear_calls = 0
        self.send_calls = 0
        self.verify_fetch_png = None

    def pre_send_once(self, text):
        self.pre_calls += 1
        return self.pre_results.pop(0)

    def clear(self):
        self.clear_calls += 1
        return self.clear_ok

    def send(self):
        self.send_calls += 1
        return self.send_ok

    def verify(self, text, mode, fetch_png=False):
        assert mode == "bubble"
        self.verify_fetch_png = fetch_png
        return self.bubble_ok


PRE_PASS = (True, False, {"failed_checks": [], "diff_summary": ""})
PRE_MISMATCH = (
    False,
    True,
    {"failed_checks": ["body_match"], "diff_summary": "body_match: 行単位照合 NG"},
)
PRE_STOP = (
    False,
    False,
    {"failed_checks": ["recipient"], "diff_summary": "recipient: 宛先が見当たらない"},
)


def test_post_retries_on_body_mismatch_then_sends(monkeypatch):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    fake = FakeRunner([PRE_MISMATCH, PRE_PASS])
    ok, error, info = gui_flow.run_post(fake, "text")
    assert ok is True
    assert error is None
    assert info["count"] == 1
    assert info["attempts"][0]["failed_checks"] == ["body_match"]
    assert fake.clear_calls == 1
    assert fake.send_calls == 1


def test_post_retry_exhaustion_stops_without_send(monkeypatch):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    fake = FakeRunner([PRE_MISMATCH, PRE_MISMATCH, PRE_MISMATCH])
    ok, error, info = gui_flow.run_post(fake, "text")
    assert ok is False
    assert error == "pre_send_failed_retry_exhausted"
    assert info["count"] == 2
    assert len(info["attempts"]) == 3
    assert fake.clear_calls == 2
    assert fake.send_calls == 0


def test_post_recipient_mismatch_never_retries(monkeypatch):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    fake = FakeRunner([PRE_STOP])
    ok, error, info = gui_flow.run_post(fake, "text")
    assert ok is False
    assert error == "pre_send_failed_stop"
    assert info["count"] == 0
    assert fake.pre_calls == 1
    assert fake.clear_calls == 0
    assert fake.send_calls == 0


def test_post_calls_on_sent_once_after_send(monkeypatch):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    calls = []
    fake = FakeRunner([PRE_PASS])
    ok, error, _ = gui_flow.run_post(fake, "text", on_sent=lambda: calls.append(1))
    assert ok is True
    assert calls == [1]
    # 送信に進まなかった場合は呼ばれない (承認を消費しない)
    fake2 = FakeRunner([PRE_STOP])
    ok, error, _ = gui_flow.run_post(fake2, "text", on_sent=lambda: calls.append(1))
    assert ok is False
    assert calls == [1]


# --- 承認ゲート -----------------------------------------------------------------


def _use_tmp_approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("GUI_FLOW_APPROVALS", str(tmp_path / "approvals"))


def test_approval_create_and_check_ok(tmp_path, monkeypatch):
    _use_tmp_approvals(tmp_path, monkeypatch)
    rec = gui_flow.create_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    assert rec["used"] is False
    assert rec["recipient"] == "example-dm"
    stored = tmp_path / "approvals" / (rec["text_sha256"] + ".json")
    assert stored.exists()
    ok, reason, found = gui_flow.check_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    assert ok is True
    assert reason == "ok"
    assert found["text_sha256"] == rec["text_sha256"]


def test_approval_missing_without_record(tmp_path, monkeypatch):
    _use_tmp_approvals(tmp_path, monkeypatch)
    ok, reason, found = gui_flow.check_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    assert ok is False
    assert reason == "missing"
    assert found is None


def test_approval_hash_mismatch_stops(tmp_path, monkeypatch):
    _use_tmp_approvals(tmp_path, monkeypatch)
    gui_flow.create_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    ok, reason, _ = gui_flow.check_approval("discord", DISCORD_CFG, LINE_TEXT)
    assert ok is False
    assert reason == "missing"


def test_approval_expired_stops(tmp_path, monkeypatch):
    _use_tmp_approvals(tmp_path, monkeypatch)
    gui_flow.create_approval(
        "discord", DISCORD_CFG, DISCORD_TEXT, expires="2020-01-01T00:00:00+09:00"
    )
    ok, reason, _ = gui_flow.check_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    assert ok is False
    assert reason == "expired"


def test_approval_used_cannot_be_reused(tmp_path, monkeypatch):
    _use_tmp_approvals(tmp_path, monkeypatch)
    gui_flow.create_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    assert gui_flow.consume_approval("discord", DISCORD_CFG, DISCORD_TEXT) is True
    ok, reason, _ = gui_flow.check_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    assert ok is False
    assert reason == "used"
    # 使用済みの記録は二度と消費できない
    assert gui_flow.consume_approval("discord", DISCORD_CFG, DISCORD_TEXT) is False


def test_approval_recipient_mismatch_stops(tmp_path, monkeypatch):
    _use_tmp_approvals(tmp_path, monkeypatch)
    gui_flow.create_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    other = dict(DISCORD_CFG, recipient_aliases=["another-dm"])
    ok, reason, _ = gui_flow.check_approval("discord", other, DISCORD_TEXT)
    assert ok is False
    assert reason == "mismatch"


def test_runner_gate_blocks_then_passes(tmp_path, monkeypatch):
    _use_tmp_approvals(tmp_path, monkeypatch)
    r = gui_flow.Runner(DUMMY_SSH_CFG, "discord")
    ok, reason = r.check_approval(DISCORD_TEXT)
    assert ok is False
    assert reason == "missing"
    assert r.checks[-1]["name"] == "approval"
    assert r.checks[-1]["ok"] is False
    gui_flow.create_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    ok, reason = r.check_approval(DISCORD_TEXT)
    assert ok is True
    assert reason == "ok"
    assert r.checks[-1]["ok"] is True


def test_build_readback_script_is_read_only():
    s = gui_flow.build_readback_script("gf-x.sh", "line", LINE_CFG)
    assert "status=field_empty" in s
    assert "keyf 0 cmd pid" in s  # 全選択
    assert "keyf 8 cmd pid" in s  # コピー
    assert "$GH type" not in s  # 入力しない
    assert "keyf 51" not in s  # 消去しない


# --- 送信後スクショの受け渡し -----------------------------------------------------

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-png-bytes"


def _use_tmp_screenshots(tmp_path, monkeypatch):
    monkeypatch.setenv("GUI_FLOW_SCREENSHOTS", str(tmp_path / "shots"))


def test_decode_b64_bytes_roundtrip_and_failures():
    enc = base64.b64encode(FAKE_PNG).decode("ascii")
    wrapped = "\n".join(enc[i : i + 8] for i in range(0, len(enc), 8))
    assert gui_flow.decode_b64_bytes(wrapped) == FAKE_PNG
    assert gui_flow.decode_b64_bytes("") is None
    assert gui_flow.decode_b64_bytes("!!!not-base64!!!") is None


def test_save_screenshot_file_name_and_content(tmp_path, monkeypatch):
    _use_tmp_screenshots(tmp_path, monkeypatch)
    now = datetime(2026, 11, 3, 16, 7, 9, tzinfo=gui_flow.JST)
    path = gui_flow.save_screenshot_file("discord", FAKE_PNG, now=now)
    assert re.fullmatch(r".*/gui-flow-discord-20261103-160709\.png", path)
    with open(path, "rb") as f:
        assert f.read() == FAKE_PNG


def test_verify_fetch_png_sets_last_png_bytes(monkeypatch):
    r = gui_flow.Runner(DUMMY_SSH_CFG, "discord")
    canned = {
        "status": "ok",
        "pid": "1234",
        "window": "767 0 30 1440 870",
        "_files": {
            "-ocr.txt": discord_ocr(),
            "-png.b64": base64.b64encode(FAKE_PNG).decode("ascii"),
        },
        "_ssh_rc": 0,
    }
    seen = {}

    def fake_run_queue(script, name, extras=()):
        seen["extras"] = extras
        return canned

    monkeypatch.setattr(r.mac, "run_queue", fake_run_queue)
    assert r.verify(DISCORD_TEXT, "bubble", fetch_png=True) is True
    assert "-png.b64" in seen["extras"]
    assert r.last_png_bytes == FAKE_PNG
    # fetch_png=False のときは PNG を取りに行かない
    r2 = gui_flow.Runner(DUMMY_SSH_CFG, "discord")
    monkeypatch.setattr(r2.mac, "run_queue", fake_run_queue)
    assert r2.verify(DISCORD_TEXT, "bubble") is True
    assert "-png.b64" not in seen["extras"]
    assert r2.last_png_bytes is None


def test_post_requests_png_on_bubble_verify(monkeypatch):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    fake = FakeRunner([PRE_PASS])
    ok, error, _ = gui_flow.run_post(fake, "text")
    assert ok is True
    assert fake.verify_fetch_png is True


def test_store_screenshot_success_with_preset_bytes(tmp_path, monkeypatch):
    _use_tmp_screenshots(tmp_path, monkeypatch)
    r = gui_flow.Runner(DUMMY_SSH_CFG, "line")
    r.last_png_bytes = FAKE_PNG
    path = r.store_post_send_screenshot()
    assert path is not None
    assert re.search(r"gui-flow-line-\d{8}-\d{6}\.png$", path)
    with open(path, "rb") as f:
        assert f.read() == FAKE_PNG
    shot = [c for c in r.checks if c["name"] == "screenshot"]
    assert shot and shot[-1]["ok"] is True


def test_store_screenshot_capture_failure_returns_none(monkeypatch, tmp_path):
    _use_tmp_screenshots(tmp_path, monkeypatch)
    r = gui_flow.Runner(DUMMY_SSH_CFG, "discord")
    canned = {"status": "window_not_found", "_files": {}, "_ssh_rc": 0}
    monkeypatch.setattr(r.mac, "run_queue", lambda *a, **k: canned)
    # 取得に失敗しても例外にならず None を返し、理由が checks に残る
    assert r.store_post_send_screenshot() is None
    shot = [c for c in r.checks if c["name"] == "screenshot"]
    assert shot and shot[-1]["ok"] is False


def test_store_screenshot_save_error_returns_none(monkeypatch, tmp_path):
    _use_tmp_screenshots(tmp_path, monkeypatch)
    r = gui_flow.Runner(DUMMY_SSH_CFG, "discord")
    r.last_png_bytes = FAKE_PNG

    def boom(app, data, now=None):
        raise OSError("disk full")

    monkeypatch.setattr(gui_flow, "save_screenshot_file", boom)
    # 保存に失敗しても例外は投げない (送信結果は成功のままにするため)
    assert r.store_post_send_screenshot() is None
    shot = [c for c in r.checks if c["name"] == "screenshot"]
    assert shot and shot[-1]["ok"] is False
    assert "disk full" in shot[-1]["evidence"]


def test_build_verify_and_capture_scripts_png():
    v = gui_flow.build_verify_script("gf-x.sh", "discord", DISCORD_CFG)
    assert "-png.b64" in v  # 撮影した PNG の base64 コピーを作る
    assert "base64 -i" in v  # macOS の base64 は -i 形式でないとファイルを読めない
    c = gui_flow.build_capture_script("gf-x.sh", "discord", DISCORD_CFG)
    assert "-png.b64" in c
    assert "base64 -i" in c
    assert "ocr.swift" not in c  # 撮影だけ (OCR はしない)
    assert "window_not_found" in c
