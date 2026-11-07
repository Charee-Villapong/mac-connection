"""gui_flow の宛先変数化に対する仕様テスト (テスト係が仕様だけから作成)。

実装コードは読まず、完了の定義 A〜D を公開レベルの API / CLI 結果 JSON /
既存テストのモック流儀 (Mac.run_queue 差し替え、Runner のフェーズ差し替え)
だけで担保する。

実行: python3 -m pytest tools/gui_flow/tests -q
"""

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gui_flow  # noqa: E402

GUI_FLOW_DIR = Path(__file__).resolve().parents[1]
FREEE_TEXT_DIR = Path.home() / "workspace" / "goals" / "freee" / "hidden_files"

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

SSH_CFG = {
    "user": "test-user",
    "host": "192.0.2.1",
    "identity": "/tmp/id_test",
    "proxy_script": "/tmp/proxy.sh",
    "control_path": "/tmp/gf-test-ctl",
}


def make_cfg():
    """宛先を 2 件ずつ持つ新形式 config (既定は 1 件目)。"""
    return {
        "ssh": dict(SSH_CFG),
        "apps": {
            "discord": {
                "app_name": "Discord",
                "window_app": "Discord",
                "process": "Discord",
                "input_click": [700, 852],
                "header_band": 110,
                "learned_frame": [0, 30, 1440, 870],
                "default_recipient": "alice",
                "recipients": {
                    "alice": {"aliases": ["Alice"], "nav_click": [10, 20]},
                    "bob": {"aliases": ["Bob", "bobby"], "nav_click": [30, 40]},
                },
            },
            "line": {
                "app_name": "LINE",
                "window_app": "LINE",
                "process": "LINE",
                "input_click": [800, 780],
                "header_band": 110,
                "learned_frame": [0, 30, 1440, 870],
                "default_recipient": "kei",
                "recipients": {
                    "kei": {"aliases": ["Kei", "ケイ"], "nav_click": [210, 160]},
                    "taro": {"aliases": ["Taro", "太郎"], "nav_click": [210, 300]},
                },
            },
        },
    }


def ocr_line(x, y, text):
    return "TEXT [x=%d y=%d w=200 h=20 conf=1.00] %s" % (x, y, text)


def discord_ocr(header):
    body = [
        "[SYSTEM] AIエージェントからお知らせ",
        "これはテストです。",
        "送信はしません。",
        "※注意",
        "確認が終わったら消します。",
        "返信は不要です。",
    ]
    lines = [ocr_line(421, 50, header)]
    y = 460
    for ln in body:
        lines.append(ocr_line(454, y, ln))
        y += 24
    return "\n".join(lines)


def line_ocr(header):
    lines = [ocr_line(454, 130, header)]
    y = 460
    for ln in LINE_TEXT.splitlines():
        lines.append(ocr_line(454, y, ln))
        y += 24
    return "\n".join(lines)


def run_main(monkeypatch, capsys, argv):
    """CLI を 1 回実行し、(終了コード, 結果 JSON) を返す。"""
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        gui_flow.main()
    out = capsys.readouterr().out
    return exc.value.code, json.loads(out)


def pre_send_with_mocked_mac(runner, text, ocr_text):
    """mac.run_queue を差し替え、入力→検閲 (pre_send_once) を 1 回回す。"""
    scripts = []

    def fake_run_queue(script, name, extras=(), timeout=None):
        scripts.append(script)
        return {
            "status": "ok",
            "pid": "1234",
            "window": "767 0 30 1440 870",
            "field_empty": "yes",
            "_files": {
                "-readback.txt": text,
                "-leftover.txt": "",
                "-ocr.txt": ocr_text,
                # フェーズ4 是正: 新しい撮影スクリプトは切り出し OCR
                # (-header-ocr.txt) も返す。ocr_text の先頭行は
                # ヘッダ文字列なので、それを切り出し結果として返す
                # (check_recipient_header の capture_header_runs 経由
                # でも同じヘッダが読めるようにする)。
                "-header-ocr.txt": ocr_text.splitlines()[0] if ocr_text else "",
            },
            "_ssh_rc": 0,
        }

    runner.mac.run_queue = fake_run_queue
    ok, retryable, record = runner.pre_send_once(text)
    return ok, retryable, record, scripts


# --- A. 後方互換 ---------------------------------------------------------------


def test_old_format_config_reads_as_single_default_recipient():
    old = {
        "app_name": "Discord",
        "window_app": "Discord",
        "process": "Discord",
        "input_click": [700, 852],
        "nav_click": [174, 350],
        "recipient_aliases": ["example-dm"],
        "header_band": 110,
    }
    recipients, default_id = gui_flow.recipients_of(old)
    assert default_id == "example-dm"
    assert recipients == {
        "example-dm": {"aliases": ["example-dm"], "nav_click": [174, 350]}
    }
    found_id, _ = gui_flow.find_recipient(old, None)
    assert found_id == "example-dm"

    cfg = {"ssh": dict(SSH_CFG), "apps": {"discord": old, "line": {}}}
    runner = gui_flow.Runner(cfg, "discord")
    assert runner.recipient_known is True
    assert runner.recipient_id == "example-dm"
    assert runner.acfg["recipient_aliases"] == ["example-dm"]
    assert runner.acfg["nav_click"] == [174, 350]


def test_real_config_defaults_resolve_without_to():
    cfg = json.loads((GUI_FLOW_DIR / "config.local.json").read_text(encoding="utf-8"))
    line_app = cfg["apps"]["line"]
    line_default = line_app["default_recipient"]
    line_entry = line_app["recipients"][line_default]
    line = gui_flow.Runner(cfg, "line")
    assert line.recipient_id == line_default
    assert line.recipient_known is True
    assert line.acfg["recipient_aliases"] == line_entry["aliases"]
    assert line.acfg["nav_click"] == line_entry["nav_click"]
    discord_app = cfg["apps"]["discord"]
    discord_default = discord_app["default_recipient"]
    discord = gui_flow.Runner(cfg, "discord")
    assert discord.recipient_id == discord_default
    assert discord.recipient_known is True


def _check_real_approval_record(tmp_path, monkeypatch, filename, text_filename):
    """実在の承認レコードを tmp へコピーし、新形式 config で有効なままか見る。

    実レコードそのものは消費も改変もしない。コピー側のバイト列が
    テスト前後で変わらないことも確認する。
    """
    src = GUI_FLOW_DIR / "approvals" / filename
    dest_dir = tmp_path / "approvals"
    dest_dir.mkdir()
    dest = dest_dir / filename
    shutil.copyfile(src, dest)
    before = dest.read_bytes()
    monkeypatch.setenv("GUI_FLOW_APPROVALS", str(dest_dir))

    text = (FREEE_TEXT_DIR / text_filename).read_text(encoding="utf-8")
    assert gui_flow.text_sha256(text) == filename[: -len(".json")]

    record = json.loads(dest.read_text(encoding="utf-8"))
    expected_recipient = record["recipient"]

    cfg = json.loads((GUI_FLOW_DIR / "config.local.json").read_text(encoding="utf-8"))
    runner = gui_flow.Runner(cfg, "line")
    assert runner.recipient_id == expected_recipient
    now = datetime(2026, 11, 4, 9, 0, tzinfo=gui_flow.JST)
    ok, reason, found = gui_flow.check_approval("line", runner.acfg, text, now=now)
    assert ok is True
    assert reason == "ok"
    assert found["app"] == "line"
    assert found["recipient"] == expected_recipient
    assert found["expires_at"] == "2026-11-05T13:00:00+09:00"
    assert found["used"] is False
    # ゲート判定は記録を消費しない
    assert dest.read_bytes() == before
    assert json.loads(dest.read_text(encoding="utf-8"))["used"] is False


def test_real_approval_record_freee_still_valid_and_untouched(tmp_path, monkeypatch):
    _check_real_approval_record(
        tmp_path,
        monkeypatch,
        "51fe2cb0c5fefc9a95551fe8fe34c62b82d2533bb815fdb302d0840426beaffb.json",
        "wife-remind-freee.txt",
    )


def test_real_approval_record_duolingo_still_valid_and_untouched(tmp_path, monkeypatch):
    _check_real_approval_record(
        tmp_path,
        monkeypatch,
        "82706b04b0e3ecb9184b5290c2bcbda60489512ce60ab04ae6a37eea8d9e7cf6.json",
        "wife-remind-duolingo.txt",
    )


# --- 仕様 3 (careful 以外): 未知宛先は Mac に触らず停止 -------------------------


def test_unknown_recipient_non_careful_actions_stop_without_touching_mac(
    tmp_path, monkeypatch, capsys
):
    cfg_file = tmp_path / "config.local.json"
    cfg_file.write_text(json.dumps(make_cfg()), encoding="utf-8")
    monkeypatch.setenv("GUI_FLOW_CONFIG", str(cfg_file))
    monkeypatch.setenv("GUI_FLOW_APPROVALS", str(tmp_path / "approvals"))
    msg = tmp_path / "msg.txt"
    msg.write_text(DISCORD_TEXT, encoding="utf-8")

    def boom(self, *args, **kwargs):
        raise AssertionError("未知宛先では Mac に触ってはいけない")

    monkeypatch.setattr(gui_flow.Mac, "run", boom)
    monkeypatch.setattr(gui_flow.Mac, "run_queue", boom)

    # approve は結果 JSON の形が他アクションと別経路のため、仕様 5 が
    # approve にも recipient を求めるかは仕様確認事項とし、ここでは対象外。
    cases = [
        ("input", ["--text-file", str(msg)]),
        ("verify", ["--text-file", str(msg), "--mode", "field"]),
        ("send", []),
        ("clear", []),
        ("post", ["--text-file", str(msg)]),
        ("test", ["--text-file", str(msg)]),
    ]
    for action, extra in cases:
        code, out = run_main(
            monkeypatch,
            capsys,
            ["gui_flow.py", "--to", "unknown-x", "discord", action] + extra,
        )
        assert code == 2, action
        assert out["ok"] is False, action
        assert out["error"] == "unknown_recipient", action
        assert out["recipient"] is None, action
        assert out["recipient_requested"] == "unknown-x", action


# --- B. --to で既知の第 2 宛先を指定する正常系 ----------------------------------


def test_to_second_recipient_pre_send_passes_and_uses_its_nav_click():
    runner = gui_flow.Runner(make_cfg(), "discord", to="bob")
    assert runner.recipient_id == "bob"
    ok, retryable, record, scripts = pre_send_with_mocked_mac(
        runner, DISCORD_TEXT, discord_ocr("Bob")
    )
    assert ok is True
    assert retryable is False
    assert record["failed_checks"] == []
    joined = "\n".join(scripts)
    assert "click 30 40" in joined  # bob の nav_click でトークを開く
    assert "click 10 20" not in joined  # 既定宛先 alice の座標は使わない


def test_to_alias_and_default_recipient_in_result_json(tmp_path, monkeypatch, capsys):
    cfg_file = tmp_path / "config.local.json"
    cfg_file.write_text(json.dumps(make_cfg()), encoding="utf-8")
    monkeypatch.setenv("GUI_FLOW_CONFIG", str(cfg_file))
    monkeypatch.setenv("GUI_FLOW_SCREENSHOTS", str(tmp_path / "shots"))
    msg = tmp_path / "msg.txt"
    msg.write_text(DISCORD_TEXT, encoding="utf-8")
    monkeypatch.setattr(
        gui_flow.Mac,
        "run",
        lambda self, cmd, stdin_text=None, timeout=None: (0, "GF_OK\n", ""),
    )
    monkeypatch.setattr(gui_flow.Runner, "input", lambda self, text: True)

    code, out = run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "--to", "bobby", "discord", "input", "--text-file", str(msg)],
    )
    assert code == 0
    assert out["ok"] is True
    assert out["recipient"] == "bob"  # エイリアス bobby は ID bob に解決される
    assert out["recipient_requested"] == "bobby"

    code, out = run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "discord", "input", "--text-file", str(msg)],
    )
    assert code == 0
    assert out["recipient"] == "alice"  # 未指定なら default_recipient
    assert out["recipient_requested"] is None


def test_line_to_second_recipient_pre_send_passes():
    runner = gui_flow.Runner(make_cfg(), "line", to="taro")
    assert runner.recipient_id == "taro"
    ok, retryable, record, scripts = pre_send_with_mocked_mac(
        runner, LINE_TEXT, line_ocr("Taro")
    )
    assert ok is True
    assert record["failed_checks"] == []
    assert "click 210 300" in "\n".join(scripts)  # taro の nav_click


# --- C. careful の再学習フロー (結果 JSON で担保) -------------------------------


def _run_careful_main(tmp_path, monkeypatch, capsys, name, ocr_texts):
    """Runner のフェーズを差し替えて CLI の careful を実行する。"""
    cfg = make_cfg()
    cfg_file = tmp_path / "config.local.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("GUI_FLOW_CONFIG", str(cfg_file))
    monkeypatch.setenv("GUI_FLOW_LEARNED", str(tmp_path / "learned_cases.jsonl"))
    monkeypatch.setenv("GUI_FLOW_SCREENSHOTS", str(tmp_path / "shots"))
    monkeypatch.setenv("GUI_FLOW_APPROVALS", str(tmp_path / "approvals"))

    seq = [gui_flow.parse_ocr(t) for t in ocr_texts]
    clicks = []
    calls = {"input": [], "verify": [], "clear": 0}
    monkeypatch.setattr(
        gui_flow.Runner,
        "capture_ocr",
        lambda self: ((0, 30, 1440, 870), seq.pop(0) if seq else []),
    )
    monkeypatch.setattr(
        gui_flow.Runner, "click_point", lambda self, point: clicks.append(point) or True
    )
    monkeypatch.setattr(
        gui_flow.Runner,
        "careful_capture",
        lambda self, step: (str(tmp_path / "shot.png"), (0, 30, 1440, 870)),
    )
    monkeypatch.setattr(gui_flow.Runner, "snapshot_thumb", lambda self, ccfg: None)
    monkeypatch.setattr(
        gui_flow.Runner,
        "probe",
        lambda self, candidates, ccfg: {
            "status": "ok",
            "hit_index": 0,
            "point": (700, 852),
            "tried": 1,
            "field_empty_after": True,
        },
    )

    def fake_input(self, text):
        calls["input"].append(text)
        return True

    def fake_verify(self, text, mode, fetch_png=False):
        calls["verify"].append(mode)
        return True

    def fake_clear(self):
        calls["clear"] += 1
        return True

    def fake_send(self):
        raise AssertionError("careful は送信キーを押してはいけない")

    monkeypatch.setattr(gui_flow.Runner, "input", fake_input)
    monkeypatch.setattr(gui_flow.Runner, "verify", fake_verify)
    monkeypatch.setattr(gui_flow.Runner, "clear", fake_clear)
    monkeypatch.setattr(gui_flow.Runner, "send", fake_send)
    monkeypatch.setattr(
        gui_flow.Mac,
        "run",
        lambda self, cmd, stdin_text=None, timeout=None: (0, "GF_OK\n", ""),
    )
    code, out = run_main(
        monkeypatch, capsys, ["gui_flow.py", "--to", name, "discord", "careful"]
    )
    return code, out, clicks, calls, cfg, cfg_file


def test_careful_unknown_recipient_success_registers_recipient(
    tmp_path, monkeypatch, capsys
):
    code, out, clicks, calls, cfg, cfg_file = _run_careful_main(
        tmp_path,
        monkeypatch,
        capsys,
        "new-person",
        [
            "\n".join([ocr_line(50, 100, "Alice"), ocr_line(50, 200, "new-person")]),
            ocr_line(421, 50, "new-person"),
        ],
    )
    assert code == 0
    assert out["ok"] is True
    assert out["error"] is None
    assert out["recipient"] == "new-person"
    assert out["recipient_requested"] == "new-person"
    assert clicks == [(150, 240)]  # 一覧で見つけた行の座標をクリックして開いた
    learned = [e for e in out["learned"] if e["item"] == "recipient"]
    assert len(learned) == 1
    assert learned[0]["new"] == {
        "id": "new-person",
        "aliases": ["new-person"],
        "nav_click": [150, 240],
    }
    # 辞書へ登録され、既存宛先と既定宛先はそのまま残る
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    recipients = saved["apps"]["discord"]["recipients"]
    assert recipients["new-person"] == {
        "aliases": ["new-person"],
        "nav_click": [150, 240],
    }
    assert recipients["alice"] == cfg["apps"]["discord"]["recipients"]["alice"]
    assert recipients["bob"] == cfg["apps"]["discord"]["recipients"]["bob"]
    assert saved["apps"]["discord"]["default_recipient"] == "alice"
    # 書き換え前にバックアップを取り、学習記録にも残る
    backups = list(tmp_path.glob("config.local.json.bak-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == cfg
    records = [
        json.loads(ln)
        for ln in (tmp_path / "learned_cases.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    ]
    assert any(
        "recipient" in rec["item"]
        and rec["new"] == {"aliases": ["new-person"], "nav_click": [150, 240]}
        for rec in records
    )
    # 送信なしの自己確認まで行う
    assert out["self_check"]["ok"] is True
    assert calls["input"] and calls["verify"] and calls["clear"] == 1


def _assert_careful_failure_registers_nothing(
    tmp_path, monkeypatch, capsys, name, ocr_texts, error, expected_clicks
):
    code, out, clicks, calls, cfg, cfg_file = _run_careful_main(
        tmp_path, monkeypatch, capsys, name, ocr_texts
    )
    assert code == 2
    assert out["ok"] is False
    assert out["error"] == error
    assert out["recipient"] is None
    assert out["recipient_requested"] == name
    assert out["learned"] == []
    assert out["self_check"]["ok"] is False
    assert clicks == expected_clicks
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert (
        saved["apps"]["discord"]["recipients"] == cfg["apps"]["discord"]["recipients"]
    )
    assert not list(tmp_path.glob("config.local.json.bak-*"))
    assert not (tmp_path / "learned_cases.jsonl").exists()


def test_careful_unknown_recipient_not_found_registers_nothing(
    tmp_path, monkeypatch, capsys
):
    _assert_careful_failure_registers_nothing(
        tmp_path,
        monkeypatch,
        capsys,
        "ghost",
        ["\n".join([ocr_line(50, 100, "Alice"), ocr_line(50, 200, "Bob")])],
        "recipient_not_found",
        [],
    )


def test_careful_unknown_recipient_ambiguous_registers_nothing(
    tmp_path, monkeypatch, capsys
):
    _assert_careful_failure_registers_nothing(
        tmp_path,
        monkeypatch,
        capsys,
        "new-person",
        ["\n".join([ocr_line(50, 100, "new-person"), ocr_line(50, 300, "new-person")])],
        "recipient_ambiguous",
        [],
    )


def test_careful_unknown_recipient_header_mismatch_registers_nothing(
    tmp_path, monkeypatch, capsys
):
    _assert_careful_failure_registers_nothing(
        tmp_path,
        monkeypatch,
        capsys,
        "new-person",
        [
            ocr_line(50, 200, "new-person"),
            ocr_line(421, 50, "someone-else"),
        ],
        "recipient_header_mismatch",
        [(150, 240)],
    )


# --- D. 既存の異常系 (宛先分離・期限切れ・使用済み・宛先違いの後片付け) ----------


def test_approval_separation_expired_and_used_for_second_recipient(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GUI_FLOW_APPROVALS", str(tmp_path / "approvals"))
    cfg = make_cfg()
    alice = gui_flow.Runner(cfg, "discord", to="alice")
    bob = gui_flow.Runner(cfg, "discord", to="bob")

    # 宛先 A の承認では宛先 B へ送れない (ゲートで mismatch)
    gui_flow.create_approval("discord", alice.acfg, DISCORD_TEXT)
    ok, reason, _ = gui_flow.check_approval("discord", bob.acfg, DISCORD_TEXT)
    assert (ok, reason) == (False, "mismatch")
    ok, reason = bob.check_approval(DISCORD_TEXT)
    assert (ok, reason) == (False, "mismatch")
    ok, reason, _ = gui_flow.check_approval("discord", alice.acfg, DISCORD_TEXT)
    assert (ok, reason) == (True, "ok")

    # 第 2 宛先でも期限切れは停止する
    expired_text = DISCORD_TEXT.replace("テストです", "期限切れです")
    gui_flow.create_approval(
        "discord", bob.acfg, expired_text, expires="2020-01-01T00:00:00+09:00"
    )
    ok, reason, _ = gui_flow.check_approval("discord", bob.acfg, expired_text)
    assert (ok, reason) == (False, "expired")

    # 第 2 宛先でも使用済みは再利用できない
    used_text = DISCORD_TEXT.replace("テストです", "使用済みです")
    gui_flow.create_approval("discord", bob.acfg, used_text)
    assert gui_flow.consume_approval("discord", bob.acfg, used_text) is True
    ok, reason, _ = gui_flow.check_approval("discord", bob.acfg, used_text)
    assert (ok, reason) == (False, "used")
    assert gui_flow.consume_approval("discord", bob.acfg, used_text) is False


def test_wrong_recipient_for_second_recipient_cleans_up_and_never_sends(monkeypatch):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)
    runner = gui_flow.Runner(make_cfg(), "discord", to="bob")
    # このテストの焦点はヘッダ確認ゲートであり、nav 精査は通過スタブにする
    # (精査自体は test_nav_scrutiny_spec / impl が担保する)
    runner.scrutinize_nav = lambda: True
    # ヘッダが既定宛先 Alice の画面を返すモックを付ける (宛先違いの再現)
    pre_send_with_mocked_mac(runner, DISCORD_TEXT, discord_ocr("Alice"))
    sent = []
    ok, error, info = gui_flow.run_post(
        runner, DISCORD_TEXT, on_sent=lambda: sent.append(1)
    )
    assert ok is False
    assert error == "wrong_recipient"
    assert info["count"] == 0
    # ヘッダ確認ゲート (入力前) で止まるため、run_post 内ではまだ
    # 何も入力しておらず後片付けは不要 (cleaned=False)。入力済みの
    # 宛先違いで後片付けする経路は test_gui_flow 側の
    # test_post_wrong_recipient_cleans_up_and_stops が担保する。
    assert runner.wrong_recipient == {
        "expected": ["Bob", "bobby"],
        "actual_header": ["Alice"],
        "cleaned": False,
    }
    assert sent == []  # 送信成功扱いにならない (承認を消費しない)
    check_names = {c["name"]: c["ok"] for c in runner.checks}
    assert check_names["recipient_header"] is False
    assert "send_key" not in check_names  # 送信キーを押していない
