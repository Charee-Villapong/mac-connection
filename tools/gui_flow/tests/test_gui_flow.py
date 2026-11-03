"""gui_flow の単体テスト (Mac 接続が要る部分はモックで切り分け)。

実行: python3 -m pytest tools/gui_flow/tests -q
"""

import json
import os
import sys

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
