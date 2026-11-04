"""ヘッダ候補拡張 (フェーズ5) の verify 経路の実装側回帰テスト。

仕様テスト (test_header_discord_spec.py) は check_recipient_header
経路の候補拡張を担保しているが、Runner.verify の集約 recipient
チェックでも同じ拡張が効くことは、そちらでは担保されていない。
本ファイルは verify 経路専用の恒常テストとして、拡張が有効な
Discord 設定で連結ヘッダが MATCH / OTHER に分類されることを
固定する。Mac 接続部分はモックで切り分ける。

config は config.example.json の apps.discord を正として複製し
(有効化キー header_candidate_expansion を含む)、宛先だけを
テスト用の架空名に差し替える方式にする (仕様テストと同じ作法)。

実行: python3 -m pytest tests/test_header_verify_impl.py -q
"""

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gui_flow  # noqa: E402

GUI_FLOW_DIR = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads(
    (GUI_FLOW_DIR / "config.example.json").read_text(encoding="utf-8")
)

TARGET = "てすとch"
ACTIVITY = "SampleGameおもろ"
OTHER = "テスト太郎"

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


def make_cfg():
    cfg = {
        "ssh": dict(DUMMY_SSH),
        "tunables": copy.deepcopy(EXAMPLE.get("tunables", {})),
        "apps": {
            "discord": copy.deepcopy(EXAMPLE["apps"]["discord"]),
            "line": copy.deepcopy(EXAMPLE["apps"]["line"]),
        },
    }
    cfg["apps"]["discord"]["recipients"] = {
        TARGET: {"aliases": [TARGET], "nav_click": [174, 350]},
        OTHER: {"aliases": [OTHER], "nav_click": [210, 232]},
    }
    cfg["apps"]["discord"]["default_recipient"] = TARGET
    return cfg


def discord_ocr(header):
    lines = [ocr_line(421, 50, 200, 20, header)]
    y = 460
    for ln in DISCORD_BODY_LINES:
        lines.append(ocr_line(454, y, 200, 20, ln))
        y += 24
    return "\n".join(lines)


def make_verify_runner(monkeypatch, header):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    runner = gui_flow.Runner(make_cfg(), "discord", to=TARGET)
    state = {"count": 0}

    def fake_run_queue(script, name, extras=(), timeout=None):
        state["count"] += 1
        return {
            "status": "ok",
            "pid": "1234",
            "window": "767 0 30 1440 870",
            "_files": {
                "-ocr.txt": discord_ocr(header),
                # 切り出し OCR は連結ヘッダを単一項目で返す。
                "-header-ocr.txt": header_ocr_line(header),
                "-png.b64": "",
            },
            "_ssh_rc": 0,
        }

    runner.mac.run_queue = fake_run_queue
    return runner, state


def recipient_check(runner):
    checks = [c for c in runner.checks if c["name"] == "recipient"]
    assert len(checks) == 1  # verify の recipient チェックは 1 件に集約
    return checks[0]


# --- verify 経路: 連結ヘッダの MATCH / OTHER ---------------------------------


def test_verify_header_expansion_single_item_activity_matches(monkeypatch):
    # 拡張が有効な Discord 設定で、切り出し OCR が単一項目の
    # 連結ヘッダ "てすとch SampleGameおもろ" を返す場合、候補拡張
    # (空白区切り第 1 トークン) により集約 recipient チェックが
    # MATCH になり、本文・構造チェックも含め verify 全体が通る。
    runner, state = make_verify_runner(
        monkeypatch, f"{TARGET} {ACTIVITY}"
    )
    assert runner.header_candidate_expansion_enabled() is True

    assert runner.verify(DISCORD_TEXT, "field") is True
    assert runner.recipient_header_error is None
    assert state["count"] == 3  # MATCH では早期終了しない

    check = recipient_check(runner)
    assert check["ok"] is True
    assert check["recipient_error"] is None
    assert [a["classification"] for a in check["attempts"]] == ["MATCH"] * 3
    # 証跡の header は拡張候補ではなく実際の OCR run のまま
    assert check["actual_header"] == [f"{TARGET} {ACTIVITY}"] * 3


def test_verify_header_expansion_other_recipient_is_other(monkeypatch):
    # 2 宛先の Discord 設定で、ヘッダが別宛先名 + 続き文字列
    # "テスト太郎 SampleGameおもろ" の場合、候補拡張により別宛先の
    # 第 1 トークンが OTHER 判定され、verify は即停止して False。
    runner, state = make_verify_runner(monkeypatch, f"{OTHER} {ACTIVITY}")

    assert runner.verify(DISCORD_TEXT, "field") is False
    assert runner.recipient_header_error == "wrong_recipient"
    assert state["count"] == 1  # OTHER で残りの撮影をせず打ち切る

    check = recipient_check(runner)
    assert check["ok"] is False
    assert check["recipient_error"] == "wrong_recipient"
    assert check["attempts"][0]["classification"] == "OTHER"
    assert check["attempts"][0]["matched"] == OTHER
