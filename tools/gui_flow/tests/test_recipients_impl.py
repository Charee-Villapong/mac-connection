"""宛先の再学習フロー (登録の正面ケース) のテスト。

これは実装係が書いた「実装側のテスト」。仕様だけから書かれた独立
テストは tests/test_recipients_spec.py 側が担い、こちらは再学習の
登録パス (座標の再取得 → 辞書への変数登録) をメソッド単位のモックで
担保する補完分。どちらが仕様由来かは検閲係が判定する。

実行: python3 -m pytest tools/gui_flow/tests -q
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gui_flow  # noqa: E402

from impl_fixtures import make_line_app  # noqa: E402
from impl_fixtures import make_ssh  # noqa: E402

DUMMY_SSH = make_ssh()

LINE_APP = make_line_app()

FRAME = (0, 30, 1420, 850)


def _item(x, y, text, w=120, h=20):
    return {"x": x, "y": y, "w": w, "h": h, "conf": 1.0, "text": text}


def _runner(tmp_path, monkeypatch):
    cfg = {"ssh": DUMMY_SSH, "apps": {"line": dict(LINE_APP), "discord": {}}}
    cfg_file = tmp_path / "config.local.json"
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GUI_FLOW_CONFIG", str(cfg_file))
    monkeypatch.setenv("GUI_FLOW_LEARNED", str(tmp_path / "learned_cases.jsonl"))
    r = gui_flow.Runner(cfg, "line", to="新しい人")
    assert r.recipient_known is False
    return r, cfg_file, tmp_path / "learned_cases.jsonl"


def test_relearn_registers_coordinates_and_aliases(tmp_path, monkeypatch):
    r, cfg_file, learned_file = _runner(tmp_path, monkeypatch)
    shots = [
        (FRAME, [_item(100, 200, "新しい人"), _item(100, 260, "別の人")]),
        (FRAME, [_item(500, 60, "新しい人")]),
    ]
    r.capture_ocr = lambda: shots.pop(0)
    clicked = []
    r.click_point = lambda point: clicked.append(point) or True

    ok, learned = r.relearn_recipient("新しい人")

    assert ok is True
    assert clicked == [(160, 240)]  # 一覧の行の中心 (フレーム原点 + 行内中心)
    assert r.recipient_known is True
    assert r.recipient_id == "新しい人"
    assert r.acfg["recipient_aliases"] == ["新しい人"]
    # learned に登録内容 (座標とエイリアス) が現れる
    assert learned[0]["item"] == "recipient"
    assert learned[0]["new"] == {
        "id": "新しい人",
        "aliases": ["新しい人"],
        "nav_click": [160, 240],
    }
    # config の辞書へ登録され、バックアップも取られている
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["apps"]["line"]["recipients"]["新しい人"] == {
        "aliases": ["新しい人"],
        "nav_click": [160, 240],
    }
    assert saved["apps"]["line"]["default_recipient"] == "example-default"
    assert list(tmp_path.glob("config.local.json.bak-*"))
    recs = [
        json.loads(ln)
        for ln in learned_file.read_text(encoding="utf-8").strip().split("\n")
    ]
    assert recs[0]["item"] == "recipient:新しい人"
    assert recs[0]["new"]["nav_click"] == [160, 240]


def test_relearn_not_found_registers_nothing(tmp_path, monkeypatch):
    r, cfg_file, learned_file = _runner(tmp_path, monkeypatch)
    before = cfg_file.read_bytes()
    r.capture_ocr = lambda: (FRAME, [_item(100, 200, "別の人")])
    r.careful_capture = lambda step: (str(tmp_path / "s.png"), FRAME)

    ok, learned = r.relearn_recipient("新しい人")

    assert ok is False
    assert learned == []
    assert r.relearn_error == "recipient_not_found"
    assert cfg_file.read_bytes() == before  # config は書き換わらない
    assert not learned_file.exists()
    assert r.recipient_known is False


def test_careful_unknown_to_learns_then_self_checks(tmp_path, monkeypatch):
    r, cfg_file, learned_file = _runner(tmp_path, monkeypatch)
    monkeypatch.setenv("GUI_FLOW_SCREENSHOTS", str(tmp_path / "shots"))
    shots = [
        (FRAME, [_item(100, 200, "新しい人")]),
        (FRAME, [_item(500, 60, "新しい人")]),
    ]
    r.capture_ocr = lambda: shots.pop(0)
    r.click_point = lambda point: True
    r.careful_capture = lambda step: (str(tmp_path / "s.png"), FRAME)
    r.snapshot_thumb = lambda ccfg: None
    r.probe = lambda candidates, ccfg: {
        "status": "ok",
        "hit_index": 0,
        "point": (800, 780),
        "tried": 1,
        "field_empty_after": True,
    }
    r.input = lambda text: True
    r.verify = lambda text, mode, fetch_png=False: True
    r.clear = lambda: True

    def _no_send():
        raise AssertionError("careful は送信キーを押してはいけない")

    r.send = _no_send
    ok, learned, self_check_ok = r.careful(to_name="新しい人")

    assert ok is True
    assert self_check_ok is True
    items = [e["item"] for e in learned]
    assert items[0] == "recipient"  # 登録 → 本体 (座標・フレーム) の順
    assert "learned_frame" in items  # 本体側の学習も続けて行われる
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "新しい人" in saved["apps"]["line"]["recipients"]


def test_relearn_partial_match_only_is_ambiguous(tmp_path, monkeypatch):
    # 一覧に「名前を含むだけ」の行 (グループ名等) しか無い場合は、
    # 特定できないため曖昧として登録せず停止する
    r, cfg_file, learned_file = _runner(tmp_path, monkeypatch)
    before = cfg_file.read_bytes()
    r.capture_ocr = lambda: (
        FRAME,
        [_item(100, 200, "新しい人と他2名のグループ", w=220)],
    )
    r.careful_capture = lambda step: (str(tmp_path / "s.png"), FRAME)
    clicked = []
    r.click_point = lambda point: clicked.append(point) or True

    ok, learned = r.relearn_recipient("新しい人")

    assert ok is False
    assert learned == []
    assert r.relearn_error == "recipient_ambiguous"
    assert clicked == []  # 行を開いてすらいない
    assert cfg_file.read_bytes() == before
    assert not learned_file.exists()


def test_relearn_header_partial_match_registers_nothing(tmp_path, monkeypatch):
    # 開いた先のヘッダが「名前を含むだけ」では不十分 (完全一致が必須)
    r, cfg_file, learned_file = _runner(tmp_path, monkeypatch)
    before = cfg_file.read_bytes()
    shots = [
        (FRAME, [_item(100, 200, "新しい人")]),
        (FRAME, [_item(500, 60, "新しい人（3）", w=140)]),
    ]
    r.capture_ocr = lambda: shots.pop(0)
    r.click_point = lambda point: True
    r.careful_capture = lambda step: (str(tmp_path / "s.png"), FRAME)

    ok, learned = r.relearn_recipient("新しい人")

    assert ok is False
    assert learned == []
    assert r.relearn_error == "recipient_header_mismatch"
    assert cfg_file.read_bytes() == before
    assert not learned_file.exists()


def test_save_config_same_second_backups_do_not_overwrite(tmp_path):
    # 同じ秒に 2 回保存しても、先行バックアップ (古い状態) が残る
    p = tmp_path / "config.local.json"
    p.write_text('{"v": 1}\n', encoding="utf-8")
    now = gui_flow.datetime(2026, 11, 4, 9, 0, 0, tzinfo=gui_flow.JST)
    b1 = gui_flow.save_config({"v": 2}, path=str(p), now=now)
    b2 = gui_flow.save_config({"v": 3}, path=str(p), now=now)
    assert b1 != b2
    with open(b1, encoding="utf-8") as f:
        assert json.load(f) == {"v": 1}  # 登録前の状態が失われていない
    with open(b2, encoding="utf-8") as f:
        assert json.load(f) == {"v": 2}


def test_relearn_split_group_suffix_row_is_ambiguous(tmp_path, monkeypatch):
    # 名前と人数表記が別 OCR 項目に割れて返っても、行全体としては
    # 部分一致でしかないため、曖昧として登録せず停止する
    r, cfg_file, learned_file = _runner(tmp_path, monkeypatch)
    before = cfg_file.read_bytes()
    r.capture_ocr = lambda: (
        FRAME,
        [
            _item(100, 200, "新しい人", w=80),
            _item(184, 200, "（3）", w=30),
        ],
    )
    r.careful_capture = lambda step: (str(tmp_path / "s.png"), FRAME)
    clicked = []
    r.click_point = lambda point: clicked.append(point) or True

    ok, learned = r.relearn_recipient("新しい人")

    assert ok is False
    assert learned == []
    assert r.relearn_error == "recipient_ambiguous"
    assert clicked == []
    assert cfg_file.read_bytes() == before
    assert not learned_file.exists()


def test_relearn_split_name_fragments_register(tmp_path, monkeypatch):
    # 名前自体が別項目に割れて返っても、連結すれば完全一致する行は
    # 正規の候補として登録できる (座標は先頭項目の中心)
    r, cfg_file, learned_file = _runner(tmp_path, monkeypatch)
    shots = [
        (FRAME, [_item(100, 200, "新しい", w=60), _item(164, 200, "人", w=20)]),
        (FRAME, [_item(500, 60, "新しい人")]),
    ]
    r.capture_ocr = lambda: shots.pop(0)
    clicked = []
    r.click_point = lambda point: clicked.append(point) or True
    r.careful_capture = lambda step: (str(tmp_path / "s.png"), FRAME)

    ok, learned = r.relearn_recipient("新しい人")

    assert ok is True
    assert clicked == [(130, 240)]
    assert learned[0]["new"]["nav_click"] == [130, 240]
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "新しい人" in saved["apps"]["line"]["recipients"]


def test_relearn_header_hit_in_list_area_does_not_count(tmp_path, monkeypatch):
    # 開いた後も左に残るトーク一覧の名前では、ヘッダ確認は合格しない
    # (ヘッダ確認は一覧より右の領域に限定する)
    r, cfg_file, learned_file = _runner(tmp_path, monkeypatch)
    before = cfg_file.read_bytes()
    shots = [
        (FRAME, [_item(100, 200, "新しい人")]),
        (
            FRAME,
            [
                _item(500, 60, "別人", w=80),
                _item(100, 60, "新しい人", w=80),
            ],
        ),
    ]
    r.capture_ocr = lambda: shots.pop(0)
    r.click_point = lambda point: True
    r.careful_capture = lambda step: (str(tmp_path / "s.png"), FRAME)

    ok, learned = r.relearn_recipient("新しい人")

    assert ok is False
    assert learned == []
    assert r.relearn_error == "recipient_header_mismatch"
    assert cfg_file.read_bytes() == before
    assert not learned_file.exists()


def test_relearn_header_split_fragments_register(tmp_path, monkeypatch):
    # ヘッダ側でも、名前が別項目に割れていれば連結して完全一致を見る
    r, cfg_file, learned_file = _runner(tmp_path, monkeypatch)
    shots = [
        (FRAME, [_item(100, 200, "新しい人")]),
        (FRAME, [_item(500, 60, "新しい", w=60), _item(564, 60, "人", w=20)]),
    ]
    r.capture_ocr = lambda: shots.pop(0)
    r.click_point = lambda point: True
    r.careful_capture = lambda step: (str(tmp_path / "s.png"), FRAME)

    ok, learned = r.relearn_recipient("新しい人")

    assert ok is True
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "新しい人" in saved["apps"]["line"]["recipients"]


def test_approve_to_second_recipient_and_unknown_via_cli(tmp_path):
    # approve 経路: 第2宛先の承認はその宛先キーで作られ、未知宛先は
    # 記録を作らず unknown_recipient で止まる (Mac には接続しない)
    import subprocess

    cfg = {
        "ssh": DUMMY_SSH,
        "apps": {
            "discord": {
                "app_name": "Discord",
                "window_app": "Discord",
                "process": "Discord",
                "default_recipient": "alice",
                "recipients": {
                    "alice": {"aliases": ["Alice"], "nav_click": [10, 20]},
                    "bob": {"aliases": ["Bob", "bobby"], "nav_click": [30, 40]},
                },
            },
            "line": {},
        },
    }
    cfg_file = tmp_path / "config.local.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
    env = dict(
        os.environ,
        GUI_FLOW_CONFIG=str(cfg_file),
        GUI_FLOW_APPROVALS=str(tmp_path / "approvals"),
    )
    script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "gui_flow.py"
    )

    def run_cli(*cli_args):
        proc = subprocess.run(
            [sys.executable, script, *cli_args],
            capture_output=True,
            text=True,
            env=env,
        )
        return proc.returncode, json.loads(proc.stdout)

    rc, out = run_cli("discord", "approve", "--to", "bob", "--text", "本文テスト")
    assert rc == 0
    assert out["recipient"] == "bob"
    assert out["approval"]["recipient"] == "Bob"  # bob のエイリアス先頭
    files = list((tmp_path / "approvals").glob("*.json"))
    assert len(files) == 1

    rc, out = run_cli("discord", "approve", "--to", "carol", "--text", "本文テスト")
    assert rc == 2
    assert out["ok"] is False
    assert out["error"] == "unknown_recipient"
    assert out["recipient"] is None
    assert len(list((tmp_path / "approvals").glob("*.json"))) == 1  # 増えていない
