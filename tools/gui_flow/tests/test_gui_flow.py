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
            ocr_line(454, 130, "example-talk"),
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
    # 宛先違いの報告用に、想定宛先と実際のヘッダ (OCR 証拠) を保持する
    rec = next(c for c in checks if c["name"] == "recipient")
    assert rec["expected"] == ["example-dm"]
    assert "someone-else" in rec["actual_header"]


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
        self.cleanup_calls = 0
        self.verify_fetch_png = None

    def check_recipient_header(self):
        return True

    def scrutinize_nav(self):
        return True

    def pre_send_once(self, text):
        self.pre_calls += 1
        return self.pre_results.pop(0)

    def clear(self):
        self.clear_calls += 1
        return self.clear_ok

    def cleanup_wrong_recipient(self):
        self.cleanup_calls += 1
        return self.clear()

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


def test_post_wrong_recipient_cleans_up_and_stops(monkeypatch):
    # 宛先違いの異常系: 送信されない / 自分が入れた文が消去され空になる /
    # 再実行されない、を担保する
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    fake = FakeRunner([PRE_STOP])
    ok, error, info = gui_flow.run_post(fake, "text")
    assert ok is False
    assert error == "wrong_recipient"
    assert info["count"] == 0  # リトライしていない
    assert fake.pre_calls == 1  # 再実行していない
    assert fake.send_calls == 0  # 送信キーを押していない
    assert fake.cleanup_calls == 1  # 後片付けが 1 回だけ走った
    assert fake.clear_calls == 1  # その中で欄が空に戻された


def test_cleanup_wrong_recipient_records_and_clears(monkeypatch):
    r = gui_flow.Runner(DUMMY_SSH_CFG, "discord")
    r.checks.append(
        {
            "name": "recipient",
            "ok": False,
            "evidence": "宛先が見当たらない",
            "expected": ["example-dm"],
            "actual_header": ["someone-else"],
        }
    )
    canned = {"status": "ok", "field_empty": "yes", "_files": {}, "_ssh_rc": 0}
    monkeypatch.setattr(r.mac, "run_queue", lambda *a, **k: canned)
    assert r.cleanup_wrong_recipient() is True
    assert r.wrong_recipient == {
        "expected": ["example-dm"],
        "actual_header": ["someone-else"],
        "cleaned": True,
    }
    assert checks_by_name(r.checks)["field_cleared"] is True
    assert checks_by_name(r.checks)["wrong_recipient_cleanup"] is True


def test_cleanup_wrong_recipient_clear_failure_is_reported(monkeypatch):
    r = gui_flow.Runner(DUMMY_SSH_CFG, "discord")
    r.checks.append(
        {
            "name": "recipient",
            "ok": False,
            "evidence": "宛先が見当たらない",
            "expected": ["example-dm"],
            "actual_header": ["someone-else"],
        }
    )
    canned = {"status": "ok", "field_empty": "no", "_files": {}, "_ssh_rc": 0}
    monkeypatch.setattr(r.mac, "run_queue", lambda *a, **k: canned)
    assert r.cleanup_wrong_recipient() is False
    assert r.wrong_recipient["cleaned"] is False
    assert checks_by_name(r.checks)["wrong_recipient_cleanup"] is False


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


# --- 調整用変数 (tunables) ---------------------------------------------------------


def test_tunables_defaults_match_current_behavior():
    t = gui_flow.tunables({})
    assert t["ssh_timeout"] == 240
    assert t["connect_timeout"] == 60
    assert t["post_send_wait"] == 1.0
    assert t["max_pre_send_retries"] == 2
    assert t["approval_ttl_hours"] == 24
    assert t["ocr_threshold"] == 0.8
    assert t["leak_threshold"] == 0.5
    assert t["short_line_threshold"] == 0.9
    assert t["long_line_threshold"] == 0.75


def test_tunables_config_override():
    t = gui_flow.tunables({"tunables": {"ocr_threshold": 0.95, "ssh_timeout": 99}})
    assert t["ocr_threshold"] == 0.95
    assert t["ssh_timeout"] == 99
    assert t["leak_threshold"] == 0.5  # 書いていないキーは既定値のまま


def test_mac_timeouts_come_from_tunables():
    assert gui_flow.Mac(DUMMY_SSH_CFG).timeout == 240
    cfg = dict(DUMMY_SSH_CFG, tunables={"ssh_timeout": 99, "connect_timeout": 7})
    mac = gui_flow.Mac(cfg)
    assert mac.timeout == 99
    assert mac.connect_timeout == 7


def test_verify_thresholds_are_variables():
    # ocr_threshold を不可能な値にすると、正常な画面でも 1 行目で落ちる
    tun = dict(gui_flow.DEFAULT_TUNABLES, ocr_threshold=1.1)
    checks = gui_flow.run_verify_checks(
        "discord", DISCORD_CFG, discord_ocr(), WIN, DISCORD_TEXT, "field", tun=tun
    )
    assert checks_by_name(checks)["first_line"] is False
    # leak_threshold も変数: 送信後に欄付近へ本文が残っているケース
    ocr = discord_ocr() + "\n" + ocr_line(454, 800, "これはテストです。")
    checks = gui_flow.run_verify_checks(
        "discord", DISCORD_CFG, ocr, WIN, DISCORD_TEXT, "bubble"
    )
    assert checks_by_name(checks)["field_state"] is False  # 既定 0.5 では漏れ検出
    tun2 = dict(gui_flow.DEFAULT_TUNABLES, leak_threshold=1.1)
    checks = gui_flow.run_verify_checks(
        "discord", DISCORD_CFG, ocr, WIN, DISCORD_TEXT, "bubble", tun=tun2
    )
    assert checks_by_name(checks)["field_state"] is True


def test_approval_ttl_override(tmp_path, monkeypatch):
    _use_tmp_approvals(tmp_path, monkeypatch)
    from datetime import timedelta

    rec = gui_flow.create_approval(
        "discord", DISCORD_CFG, DISCORD_TEXT, ttl=timedelta(hours=1)
    )
    created = gui_flow._parse_dt(rec["created_at"])
    expires = gui_flow._parse_dt(rec["expires_at"])
    assert expires - created == timedelta(hours=1)


# --- 慎重モード (careful): 候補生成・フレーム比較 ---------------------------------


def test_probe_candidates_learned_first_then_grid():
    ccfg = gui_flow.careful_cfg("discord", DISCORD_CFG)
    pts = gui_flow.probe_candidates(WIN[1:], DISCORD_CFG, ccfg)
    assert pts[0] == (700, 852)  # 学習済み座標が先頭
    assert pts[1] == (720, 852)  # 次はグリッド (x 比 0.5, 下端 -48)
    assert len(pts) <= ccfg["probe_max_candidates"]
    assert len(set(pts)) == len(pts)  # 重複なし


def test_probe_candidates_cap_and_outside():
    ccfg = dict(gui_flow.careful_cfg("discord", DISCORD_CFG), probe_max_candidates=3)
    pts = gui_flow.probe_candidates(WIN[1:], DISCORD_CFG, ccfg)
    assert len(pts) == 3
    # 学習済み座標がフレーム外ならグリッドから始まる
    moved = dict(DISCORD_CFG, input_click=[5000, 5000])
    pts = gui_flow.probe_candidates(WIN[1:], moved, ccfg)
    assert pts[0] == (720, 852)


def test_frame_drift():
    assert gui_flow.frame_drift([0, 30, 1440, 870], [0, 30, 1440, 870]) == 0
    assert gui_flow.frame_drift([0, 30, 1440, 870], [200, 30, 1440, 870]) == 200
    assert gui_flow.frame_drift(None, [0, 30, 1440, 870]) is None
    assert gui_flow.frame_drift([0, 30, 1440, 870], None) is None


def test_careful_cfg_defaults_and_override():
    c = gui_flow.careful_cfg("line", LINE_CFG)
    assert c["frame_tolerance"] == 40
    assert c["self_check_text"].startswith("【AIエージェントからのお知らせ】")
    # 既定グリッドはアプリ別 (LINE の入力欄は Discord より下端から遠い)
    assert gui_flow.careful_cfg("discord", DISCORD_CFG)["probe_y_offsets"] == [
        48,
        30,
        70,
        16,
        95,
        130,
    ]
    assert c["probe_y_offsets"] == [120, 95, 145, 70, 170, 200]
    # 既定の候補上限は既定グリッド総数 (31) を切り捨てない
    for app, acfg in (("discord", DISCORD_CFG), ("line", LINE_CFG)):
        cc = gui_flow.careful_cfg(app, acfg)
        grid = 1 + len(cc["probe_y_offsets"]) * len(cc["probe_x_ratios"])
        assert cc["probe_max_candidates"] >= grid
    custom = dict(
        LINE_CFG, careful={"probe_y_offsets": [120, 95], "frame_tolerance": 5}
    )
    c2 = gui_flow.careful_cfg("line", custom)
    assert c2["probe_y_offsets"] == [120, 95]
    assert c2["frame_tolerance"] == 5


# --- 慎重モード: プローブスクリプトは送信キーを含まない -------------------------------


def test_probe_script_never_sends():
    ccfg = gui_flow.careful_cfg("discord", DISCORD_CFG)
    pts = gui_flow.probe_candidates(WIN[1:], DISCORD_CFG, ccfg)
    for app, acfg in (("discord", DISCORD_CFG), ("line", LINE_CFG)):
        s = gui_flow.build_probe_script("gf-x.sh", app, acfg, pts, ccfg["probe_text"])
        assert "keyf 36" not in s  # 送信キー (Return) は一切送らない
        assert "anomaly_leftover" in s  # 残りありは触らず停止
        assert "probe_no_hit" in s
        assert "field_empty_after" in s  # 終了時に空を確認する
        assert ccfg["probe_text"] in s


def test_probe_script_candidates_are_literal_calls():
    # キュー実行シェル (zsh) では変数の単語分割が効かないため、候補は
    # set -- $CANDS のような分割依存で渡さず、リテラル引数で 1 点ずつ呼ぶ。
    ccfg = gui_flow.careful_cfg("discord", DISCORD_CFG)
    pts = gui_flow.probe_candidates(WIN[1:], DISCORD_CFG, ccfg)
    s = gui_flow.build_probe_script("gf-x.sh", "discord", DISCORD_CFG, pts, "PROBE")
    assert "set --" not in s
    assert "CANDS" not in s
    assert "try_one() {" in s
    calls = [ln for ln in s.splitlines() if ln.startswith("try_one ")]
    assert calls == ["try_one %d %d" % p for p in pts]


# --- 慎重モード: 覆いの汎用解消 -------------------------------------------------------


def test_dismiss_script_is_generic():
    # 解消は Esc + フレーム相対の外側クリックのみ。個別ポップアップの
    # 文言・専用座標に依存せず、入力も送信キーも送らない。
    ccfg = gui_flow.careful_cfg("discord", DISCORD_CFG)
    s = gui_flow.build_dismiss_script(
        "gf-x.sh", "discord", DISCORD_CFG, (0, 30, 1440, 870), ccfg
    )
    assert "keyf 53" in s  # Esc
    assert "click 720 46" in s  # 0 + 1440*0.5 / 30 + 16 (フレーム相対)
    assert s.count(" click ") == 1  # クリックはこの 1 点だけ
    assert "$GH type" not in s  # テキスト入力なし
    assert "keyf 36" not in s  # 送信キーなし


def _no_hit():
    return {
        "status": "probe_no_hit",
        "hit_index": -1,
        "point": None,
        "tried": 31,
        "field_empty_after": True,
    }


def test_careful_overlay_dismiss_then_retry_succeeds(tmp_path, monkeypatch):
    # 覆いがある → 解消 → 再試行できる、という仕組み側の異常系
    seq = [
        _no_hit(),
        {
            "status": "ok",
            "hit_index": 0,
            "point": (700, 852),
            "tried": 1,
            "field_empty_after": True,
        },
    ]
    r, cfg_file, learned_file = _careful_runner(tmp_path, monkeypatch, seq[0])
    calls = {"probe": 0, "dismiss": 0}

    def probe(candidates, ccfg):
        calls["probe"] += 1
        return seq.pop(0)

    def dismiss(frame, ccfg):
        calls["dismiss"] += 1
        return True

    r.probe = probe
    r.dismiss_overlay = dismiss
    ok, learned, self_check_ok = r.careful()
    assert ok is True
    assert calls == {"probe": 2, "dismiss": 1}
    assert checks_by_name(r.checks)["overlay"] is True
    assert r.overlay_blocked is False


def test_careful_overlay_unresolved_stops(tmp_path, monkeypatch):
    # 解消しても到達できなければ、学習せず停止して報告する
    r, cfg_file, learned_file = _careful_runner(tmp_path, monkeypatch, _no_hit())
    calls = {"probe": 0, "dismiss": 0}

    def probe(candidates, ccfg):
        calls["probe"] += 1
        return _no_hit()

    def dismiss(frame, ccfg):
        calls["dismiss"] += 1
        return True

    r.probe = probe
    r.dismiss_overlay = dismiss
    ok, learned, self_check_ok = r.careful()
    assert ok is False
    assert self_check_ok is False
    assert learned == []
    assert calls == {"probe": 2, "dismiss": 1}  # 初回 + 解消後の 1 回だけ
    assert checks_by_name(r.checks)["overlay"] is False
    assert r.overlay_blocked is True
    assert not learned_file.exists()


def _tiny_png(rows):
    """テスト用の最小 PNG (RGB・フィルタなし)。rows は (r, g, b) の行列。"""
    import struct as _struct
    import zlib as _zlib

    h = len(rows)
    w = len(rows[0])
    raw = b""
    for row in rows:
        raw += b"\x00" + b"".join(bytes(px) for px in row)

    def chunk(typ, data):
        c = _struct.pack(">I", len(data)) + typ + data
        return c + _struct.pack(">I", _zlib.crc32(typ + data) & 0xFFFFFFFF)

    ihdr = _struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", _zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_png_diff_ratio():
    black = [[(0, 0, 0)] * 4 for _ in range(4)]
    half_white = [[(255, 255, 255)] * 2 + [(0, 0, 0)] * 2 for _ in range(4)]
    a = _tiny_png(black)
    assert gui_flow.png_diff_ratio(a, a) == 0.0
    assert gui_flow.png_diff_ratio(a, _tiny_png(half_white)) == 0.5
    assert gui_flow.png_diff_ratio(a, _tiny_png([[(0, 0, 0)] * 2])) == 1.0
    assert gui_flow.png_diff_ratio(b"not a png", a) is None
    assert gui_flow.png_diff_ratio(None, a) is None


def test_careful_overlay_screen_unchanged_stops(tmp_path, monkeypatch):
    # 解消しても画面が変わらなければ「覆いが残っている」とみなし、
    # 再プローブせず停止する (再確認ステップの異常系)
    r, cfg_file, learned_file = _careful_runner(tmp_path, monkeypatch, _no_hit())
    thumb = _tiny_png([[(10, 10, 10)] * 4 for _ in range(4)])
    r.snapshot_thumb = lambda ccfg: thumb
    calls = {"probe": 0, "dismiss": 0}

    def probe(candidates, ccfg):
        calls["probe"] += 1
        return _no_hit()

    def dismiss(frame, ccfg):
        calls["dismiss"] += 1
        return True

    r.probe = probe
    r.dismiss_overlay = dismiss
    ok, learned, self_check_ok = r.careful()
    assert ok is False
    assert calls == {"probe": 1, "dismiss": 1}  # 再プローブしていない
    assert checks_by_name(r.checks)["overlay_clean"] is False
    assert checks_by_name(r.checks)["overlay"] is False
    assert r.overlay_blocked is True
    assert not learned_file.exists()


def test_careful_leftover_never_dismisses(tmp_path, monkeypatch):
    # 欄の残りは「何も触らない」が優先。解消 (クリック等) も試みない
    probe_result = {
        "status": "anomaly_leftover",
        "hit_index": -1,
        "point": None,
        "tried": 0,
        "field_empty_after": False,
    }
    r, _, _ = _careful_runner(tmp_path, monkeypatch, probe_result)

    def dismiss(frame, ccfg):
        raise AssertionError("残りありでは解消を試みてはいけない")

    r.dismiss_overlay = dismiss
    ok, learned, self_check_ok = r.careful()
    assert ok is False


# --- 学習の保存 -----------------------------------------------------------------


def test_save_config_backup_and_content(tmp_path):
    p = tmp_path / "config.local.json"
    p.write_text('{"old": true}\n', encoding="utf-8")
    now = datetime(2026, 11, 4, 8, 0, 0, tzinfo=gui_flow.JST)
    backup = gui_flow.save_config({"new": 1}, path=str(p), now=now)
    assert backup.endswith("config.local.json.bak-20261104-080000")
    with open(backup, encoding="utf-8") as f:
        assert json.load(f) == {"old": True}
    with open(p, encoding="utf-8") as f:
        assert json.load(f) == {"new": 1}


def test_append_learned_case_jsonl(tmp_path):
    p = tmp_path / "learned_cases.jsonl"
    rec = {
        "at": "2026-11-04T08:00:00+09:00",
        "app": "discord",
        "item": "input_click",
        "old": [700, 852],
        "new": [720, 852],
        "reason": "プローブで一致",
    }
    gui_flow.append_learned_case(rec, path=str(p))
    gui_flow.append_learned_case(rec, path=str(p))
    lines = p.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["item"] == "input_click"


def test_save_careful_capture_name(tmp_path, monkeypatch):
    _use_tmp_screenshots(tmp_path, monkeypatch)
    now = datetime(2026, 11, 4, 8, 15, 0, tzinfo=gui_flow.JST)
    path = gui_flow.save_careful_capture("discord", "window", FAKE_PNG, now=now)
    assert re.fullmatch(r".*/careful-discord-20261104-081500-window\.png", path)
    with open(path, "rb") as f:
        assert f.read() == FAKE_PNG


# --- 異常分類 (needs_calibration) ---------------------------------------------------


def _cal(error, checks, acfg=None, frame=None):
    return gui_flow.classify_needs_calibration(
        error, checks, acfg if acfg is not None else DISCORD_CFG, frame
    )


def test_classify_retry_exhausted_geometry_is_calibration():
    checks = [
        {"name": "field_state", "ok": False, "evidence": "入力欄の内容が一致しない"},
        {"name": "body_match", "ok": False, "evidence": "行単位照合 NG"},
    ]
    cal = _cal("pre_send_failed_retry_exhausted", checks)
    assert cal is not None
    assert cal["name"] == "calibration"
    assert cal["ok"] is False


def test_classify_hard_blockers_are_not_calibration():
    base = [{"name": "body_match", "ok": False, "evidence": "NG"}]
    for blocker in ("recipient", "field_empty_before", "structure_codeblock"):
        checks = base + [{"name": blocker, "ok": False, "evidence": "NG"}]
        assert _cal("pre_send_failed_stop", checks) is None, blocker
    # 承認ゲート停止は分類対象外
    assert _cal("approval_missing", base) is None
    # 送信後検閲 NG も対象外
    assert _cal("post_send_verify_failed", base) is None


def test_classify_frame_drift_is_calibration():
    acfg = dict(DISCORD_CFG, learned_frame=[0, 30, 1440, 870])
    checks = [{"name": "body_match", "ok": False, "evidence": "NG"}]
    cal = _cal("pre_send_failed_stop", checks, acfg=acfg, frame=(200, 30, 1440, 870))
    assert cal is not None
    assert "フレーム" in cal["evidence"]
    # 許容差内なら座標系チェックの理由が無い限り分類しない
    checks_ok = [
        {"name": "input_status", "ok": False, "evidence": "status=queue_timeout"}
    ]
    assert _cal(None, checks_ok, acfg=acfg, frame=(10, 30, 1440, 870)) is None


def test_classify_readback_empty_is_calibration():
    checks = [
        {
            "name": "input_status",
            "ok": False,
            "evidence": "status=fail_readback_empty pid=1234",
        }
    ]
    assert _cal(None, checks) is not None
    checks2 = [
        {
            "name": "field_readback",
            "ok": False,
            "evidence": "欄の読み戻し不可: status=field_empty",
        }
    ]
    assert _cal("field_readback_failed", checks2) is not None


# --- 慎重モード本体 (モック) ---------------------------------------------------------


def _careful_runner(tmp_path, monkeypatch, probe_result, acfg_extra=None):
    """config を tmp に置き、Mac 接続部を差し替えた Runner を作る。"""
    acfg = dict(DISCORD_CFG)
    if acfg_extra:
        acfg.update(acfg_extra)
    cfg = {
        "ssh": DUMMY_SSH_CFG["ssh"],
        "apps": {"discord": acfg, "line": LINE_CFG},
    }
    cfg_file = tmp_path / "config.local.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("GUI_FLOW_CONFIG", str(cfg_file))
    monkeypatch.setenv("GUI_FLOW_LEARNED", str(tmp_path / "learned_cases.jsonl"))
    _use_tmp_screenshots(tmp_path, monkeypatch)
    r = gui_flow.Runner(cfg, "discord")
    r.careful_capture = lambda step: (str(tmp_path / "shot.png"), (0, 30, 1440, 870))
    r.snapshot_thumb = lambda ccfg: None  # 差分判定不能 → 再プローブへ進む
    r.probe = lambda candidates, ccfg: probe_result

    def _no_send():
        raise AssertionError("careful は送信キーを押してはいけない")

    r.send = _no_send
    r.input = lambda text: True
    r.verify = lambda text, mode, fetch_png=False: True
    r.clear = lambda: True
    return r, cfg_file, tmp_path / "learned_cases.jsonl"


def test_careful_learns_and_saves(tmp_path, monkeypatch):
    probe_result = {
        "status": "ok",
        "hit_index": 2,
        "point": (720, 852),
        "tried": 3,
        "field_empty_after": True,
    }
    r, cfg_file, learned_file = _careful_runner(tmp_path, monkeypatch, probe_result)
    ok, learned, self_check_ok = r.careful()
    assert ok is True
    assert self_check_ok is True
    items = {e["item"]: e for e in learned}
    assert items["input_click"]["old"] == [700, 852]
    assert items["input_click"]["new"] == [720, 852]
    assert items["learned_frame"]["new"] == [0, 30, 1440, 870]
    # config ファイルへ保存され、バックアップも取られている
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["apps"]["discord"]["input_click"] == [720, 852]
    assert saved["apps"]["discord"]["learned_frame"] == [0, 30, 1440, 870]
    assert list(tmp_path.glob("config.local.json.bak-*"))
    # 学習記録が項目ごとに追記されている
    recs = [
        json.loads(ln)
        for ln in learned_file.read_text(encoding="utf-8").strip().split("\n")
    ]
    assert {rec["item"] for rec in recs} == {"input_click", "learned_frame"}
    assert checks_by_name(r.checks)["self_check"] is True


def test_careful_no_learning_needed(tmp_path, monkeypatch):
    probe_result = {
        "status": "ok",
        "hit_index": 0,
        "point": (700, 852),
        "tried": 1,
        "field_empty_after": True,
    }
    r, cfg_file, learned_file = _careful_runner(
        tmp_path,
        monkeypatch,
        probe_result,
        acfg_extra={"learned_frame": [0, 30, 1440, 870]},
    )
    ok, learned, self_check_ok = r.careful()
    assert ok is True
    assert learned == []
    assert not list(tmp_path.glob("config.local.json.bak-*"))  # 保存していない
    assert not learned_file.exists()


def test_classify_mixed_failures_are_not_calibration():
    # 座標系チェック以外の失敗が混ざると学習対象にしない (厳密化)
    body = {"name": "body_match", "ok": False, "evidence": "NG"}
    cases = [
        [{"name": "first_line", "ok": False, "evidence": "1 行目 NG"}, body],
        [
            {
                "name": "input_status",
                "ok": False,
                "evidence": "status=app_not_running pid=?",
            },
            body,
        ],
        [
            {"name": "field_cleared", "ok": False, "evidence": "空にできなかった"},
            {"name": "field_state", "ok": False, "evidence": "一致しない"},
        ],
    ]
    for checks in cases:
        assert _cal("pre_send_failed_retry_exhausted", checks) is None, checks


def test_careful_capture_failure_fails_run(tmp_path, monkeypatch):
    probe_result = {
        "status": "ok",
        "hit_index": 0,
        "point": (700, 852),
        "tried": 1,
        "field_empty_after": True,
    }
    r, _, _ = _careful_runner(
        tmp_path,
        monkeypatch,
        probe_result,
        acfg_extra={"learned_frame": [0, 30, 1440, 870]},
    )

    def cap(step):
        if step == "window":
            return (str(tmp_path / "shot.png"), (0, 30, 1440, 870))
        return (None, (0, 30, 1440, 870))  # 証拠が保存できない

    r.careful_capture = cap
    ok, learned, self_check_ok = r.careful()
    assert self_check_ok is True  # 自己確認自体は合格
    assert ok is False  # しかし証拠が欠けるので全体は不合格


def test_careful_leftover_stops_without_saving(tmp_path, monkeypatch):
    probe_result = {
        "status": "anomaly_leftover",
        "hit_index": -1,
        "point": None,
        "tried": 0,
        "field_empty_after": False,
    }
    r, cfg_file, learned_file = _careful_runner(tmp_path, monkeypatch, probe_result)
    ok, learned, self_check_ok = r.careful()
    assert ok is False
    assert learned == []
    assert self_check_ok is False
    assert not learned_file.exists()
