"""特性テスト (現状固定): gui_flow リファクタリング安全網の補充。

棚卸しハーネスのテスト係が、候補リスト (mac-connection 節) の
C-024 (Runner 肥大化) / C-025 (長い関数群・main) / C-026 (センチネル
回転の重複) / C-027 (撮影系の重複) / C-030 (生成スクリプトの sleep
直書き) / C-031 (SSH・キュー待ちの直書き) / C-032 (Mac 側パスの
直書き) / C-033 (定数・既定値の二重管理) を対象に、既存テストで
未検証だった現行の入出力だけを公開インターフェース経由で固定する。
既存テストで十分固定済みの領域 (検閲チェック群・ヘッダ解決・保存
3 経路の結果検証など) はここでは増やさない。

実データ保護: config / 承認 / 学習記録 / スクショの保存先はすべて
環境変数で tmp 側へ向け、Mac 接続はモックで切り分ける。実機には
アクセスしない。宛先名はすべて架空のテスト用名称である。

実行: python3 -m pytest tests/test_refactor_safety_net_spec.py -q
"""

import base64
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

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
FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-png-bytes"
FAKE_PNG_B64 = base64.b64encode(FAKE_PNG).decode("ascii")


def _runner():
    return gui_flow.Runner(FULL_CFG, "discord")


def _no_sleep(monkeypatch):
    monkeypatch.setattr(gui_flow.time, "sleep", lambda s: None)


def assert_order(text, *needles):
    """needles が text 内にこの順で現れることを固定する。"""
    pos = 0
    for needle in needles:
        idx = text.find(needle, pos)
        assert idx >= 0, "見つからない (または順序違い): %r" % needle
        pos = idx + len(needle)


# --- C-032: HDR (Mac 側パス類) の現行文面 -------------------------------------

EXPECTED_HDR = """export LC_ALL=en_US.UTF-8
GH=~/muse-tools/GuiHelper.app/Contents/MacOS/GuiHelper
KH=~/muse-tools/KeyHelper.app/Contents/MacOS/KeyHelper
SC=~/muse-tools/ScreenCheck.app/Contents/MacOS/ScreenCheck
mkdir -p ~/muse-tools/gf-out
R=~/muse-tools/gf-out/gf-x.sh.result
: > $R
say() { echo "$1" >> $R; }

"""


def _all_builder_scripts():
    ccfg = gui_flow.careful_cfg("discord", DISCORD_CFG)
    return {
        "input": gui_flow.build_input_script(
            "gf-x.sh", "discord", DISCORD_CFG, DISCORD_TEXT
        ),
        "send": gui_flow.build_send_script("gf-x.sh", "discord", DISCORD_CFG),
        "clear": gui_flow.build_clear_script("gf-x.sh", "discord", DISCORD_CFG),
        "readback": gui_flow.build_readback_script("gf-x.sh", "discord", DISCORD_CFG),
        "probe": gui_flow.build_probe_script(
            "gf-x.sh", "discord", DISCORD_CFG, [(700, 852)], ccfg["probe_text"]
        ),
        "dismiss": gui_flow.build_dismiss_script(
            "gf-x.sh", "discord", DISCORD_CFG, (0, 30, 1440, 870), ccfg
        ),
        "verify": gui_flow.build_verify_script("gf-x.sh", "discord", DISCORD_CFG),
        "capture": gui_flow.build_capture_script("gf-x.sh", "discord", DISCORD_CFG),
        "snapshot": gui_flow.build_snapshot_script(
            "gf-x.sh", "discord", DISCORD_CFG, 160
        ),
        "click": gui_flow.build_click_script(
            "gf-x.sh", "discord", DISCORD_CFG, (150, 240)
        ),
    }


def test_every_builder_script_starts_with_exact_hdr():
    # 全 builder の先頭は同一の HDR (ヘルパー 3 本のパス・gf-out・
    # 結果ファイルの置き場を含む)。パス集約 (C-032) で変わらないこと。
    for label, script in _all_builder_scripts().items():
        assert script.startswith(EXPECTED_HDR), label
        assert "__NAME__" not in script, label  # 名前は置換済み
        assert script.endswith("\n"), label


def test_hdr_helper_paths_appear_once_in_each_script():
    for label, script in _all_builder_scripts().items():
        assert script.count("GuiHelper.app/Contents/MacOS/GuiHelper") == 1, label
        assert script.count("KeyHelper.app/Contents/MacOS/KeyHelper") == 1, label
        assert script.count("ScreenCheck.app/Contents/MacOS/ScreenCheck") == 1, label


# --- C-026: センチネル回転 ------------------------------------------------------

EXPECTED_CLEAR_SCRIPT = EXPECTED_HDR + """PID=$(pgrep -x Discord | head -1)
say "pid=$PID"
if [ -z "$PID" ]; then say "status=app_not_running"; exit 0; fi
open -a "Discord"
sleep 0.6
$GH click 174 350
sleep 0.4
$GH click 700 852
sleep 0.4
$KH keyf 0 cmd pid $PID
sleep 0.25
$KH keyf 51 - pid $PID
sleep 0.3
SENT="GF_SENT_C_55e2"
printf %s "$SENT" | pbcopy
$KH keyf 0 cmd pid $PID
sleep 0.25
$KH keyf 8 cmd pid $PID
sleep 0.3
PB=$(pbpaste)
if [ "$PB" = "$SENT" ]; then say "field_empty=yes"; say "status=ok"; else say "field_empty=no"; say "status=fail_not_empty"; fi
"""


def test_build_clear_script_exact_snapshot():
    # build_clear_script は既存テストから名前参照ゼロだったため、
    # 生成スクリプト全文を現行どおり固定する (C-026 の共通化前提)。
    s = gui_flow.build_clear_script("gf-x.sh", "discord", DISCORD_CFG)
    assert s == EXPECTED_CLEAR_SCRIPT


def test_build_clear_script_without_nav_click_skips_nav():
    cfg = {k: v for k, v in DISCORD_CFG.items() if k != "nav_click"}
    s = gui_flow.build_clear_script("gf-x.sh", "discord", cfg)
    assert "$GH click 174 350" not in s
    assert 'SENT="GF_SENT_C_55e2"' in s
    assert 'say "status=fail_not_empty"' in s


def test_input_script_sentinel_rotation_two_distinct_sentinels():
    s = gui_flow.build_input_script("gf-x.sh", "discord", DISCORD_CFG, DISCORD_TEXT)
    # 1 回目 (入力前の空確認): センチネル A を置いて全選択コピーし、
    # クリップボードがセンチネルのままなら空、違えば残りとして停止。
    assert_order(
        s,
        'SENT="GF_SENT_A_9d41"',
        'printf %s "$SENT" | pbcopy',
        "$KH keyf 0 cmd pid $PID",
        "$KH keyf 8 cmd pid $PID",
        "PB=$(pbpaste)",
        'if [ "$PB" != "$SENT" ]; then',
        "gf-out/gf-x.sh-leftover.txt",
        'say "status=anomaly_leftover"',
        'say "field_empty_before=yes"',
    )
    # 入力は空確認の後。2 回目 (読み戻し): 別センチネル B で回転し、
    # センチネルのまま = 読み戻し空として失敗にする。
    assert_order(
        s,
        'say "field_empty_before=yes"',
        "$GH type",
        'SENT2="GF_SENT_B_71c0"',
        'printf %s "$SENT2" | pbcopy',
        "$KH keyf 0 cmd pid $PID",
        "$KH keyf 8 cmd pid $PID",
        "GOT=$(pbpaste)",
        'if [ "$GOT" = "$SENT2" ]; then say "status=fail_readback_empty"; exit 0; fi',
        "gf-out/gf-x.sh-readback.txt",
        'say "status=ok"',
    )
    # センチネルは用途ごとに別値 (A / B が同一だと空確認と読み戻しが
    # 区別できない)。
    assert "GF_SENT_A_9d41" != "GF_SENT_B_71c0"
    assert s.count("GF_SENT_A_9d41") == 1
    assert s.count("GF_SENT_B_71c0") == 1


def test_readback_script_sentinel_rotation():
    s = gui_flow.build_readback_script("gf-x.sh", "line", LINE_CFG)
    assert_order(
        s,
        'SENT="GF_SENT_R_3b77"',
        'printf %s "$SENT" | pbcopy',
        "$KH keyf 0 cmd pid $PID",
        "$KH keyf 8 cmd pid $PID",
        "PB=$(pbpaste)",
        'if [ "$PB" = "$SENT" ]; then say "status=field_empty"; exit 0; fi',
        "gf-out/gf-x.sh-field.txt",
        'say "status=ok"',
    )


def test_probe_script_sentinel_is_single_and_reused():
    ccfg = gui_flow.careful_cfg("discord", DISCORD_CFG)
    s = gui_flow.build_probe_script(
        "gf-x.sh",
        "discord",
        DISCORD_CFG,
        [(700, 852), (720, 852)],
        ccfg["probe_text"],
    )
    # センチネル定義は 1 回だけ (P)。開始前確認・候補試行・終了確認の
    # 3 か所で同じセンチネルを pbcopy して回転させる。
    assert s.count('SENT="GF_SENT_P_20d9"') == 1
    assert s.count('printf %s "$SENT" | pbcopy') == 3
    # 候補試行の中では、コピーの後に必ず全選択 + 削除で欄を戻す。
    assert_order(
        s,
        "try_one() {",
        'printf %s "$SENT" | pbcopy',
        "$KH keyf 8 cmd pid $PID",
        "PB=$(pbpaste)",
        "$KH keyf 51 - pid $PID",
        'if [ "$PB" = "$PROBE" ]; then HIT=$IDX; HX=$CX; HY=$CY; fi',
    )
    # 終了確認の結果が field_empty_after として報告される。
    assert_order(
        s,
        'say "probe_hit=$HIT"',
        'printf %s "$SENT" | pbcopy',
        'say "field_empty_after=yes"',
        'say "status=ok"',
    )


def test_sentinel_values_are_unique_per_script():
    scripts = _all_builder_scripts()
    values = {}
    for label, script in scripts.items():
        for v in re.findall(r"GF_SENT_[A-Z]_[0-9a-f]{4}", script):
            assert v not in values, "%s と %s でセンチネルが重複" % (
                values.get(v),
                label,
            )
            values[v] = label
    assert values == {
        "GF_SENT_A_9d41": "input",
        "GF_SENT_B_71c0": "input",
        "GF_SENT_C_55e2": "clear",
        "GF_SENT_R_3b77": "readback",
        "GF_SENT_P_20d9": "probe",
    }


# --- C-027: 撮影系 builder ------------------------------------------------------

EXPECTED_AWK_SELECT = """awk -v app="$APP" '
{
  id=""; a=""; x=0; y=0; w=0; h=0; on=0
  for (i=1; i<=NF; i++) {
    if ($i ~ /^id=/)  { split($i, t, "="); id=t[2] }
    if ($i ~ /^app=/) { split($i, t, "="); a=t[2] }
    if ($i ~ /^frame=/) { split($i, t, "="); split(t[2], c, ","); x=c[1]; y=c[2] }
    if ($i ~ /^[0-9]+x[0-9]+$/) { split($i, c, "x"); w=c[1]; h=c[2] }
    if ($i == "onscreen=true") on=1
  }
  if (a == app && on == 1 && w*h > best) { best = w*h; line = id" "x" "y" "w" "h }
}
END { print line }'"""


def test_awk_window_selection_text_is_fixed():
    # 窓選択 (対象アプリ最大の表示中ウィンドウ) の awk は撮影系
    # 3 builder で共有される現行文面そのもの。
    assert gui_flow.AWK_SELECT == EXPECTED_AWK_SELECT


EXPECTED_SNAPSHOT_SCRIPT = (
    EXPECTED_HDR
    + """APP="Discord"
WIN=$($SC list | """
    + EXPECTED_AWK_SELECT
    + """)
if [ -z "$WIN" ]; then say "status=window_not_found"; exit 0; fi
WID=$(printf %s "$WIN" | cut -d" " -f1)
$SC cap $WID ~/muse-tools/gf-out/gf-x.sh.png
if [ ! -s ~/muse-tools/gf-out/gf-x.sh.png ]; then say "status=capture_failed"; exit 0; fi
sips -Z 160 ~/muse-tools/gf-out/gf-x.sh.png --out ~/muse-tools/gf-out/gf-x.sh-thumb.png >/dev/null 2>&1
if [ ! -s ~/muse-tools/gf-out/gf-x.sh-thumb.png ]; then say "status=thumb_failed"; exit 0; fi
base64 -i ~/muse-tools/gf-out/gf-x.sh-thumb.png > ~/muse-tools/gf-out/gf-x.sh-thumb.b64
say "status=ok"
"""
)


def test_build_snapshot_script_exact_snapshot():
    # build_snapshot_script は既存テストから名前参照ゼロだった。
    # capture との差分 (window を say しない・サムネイル化する) ごと固定。
    s = gui_flow.build_snapshot_script("gf-x.sh", "discord", DISCORD_CFG, 160)
    assert s == EXPECTED_SNAPSHOT_SCRIPT
    assert 'say "window=$WIN"' not in s  # capture / verify との現行差分


def test_build_capture_script_window_line_and_branches():
    s = gui_flow.build_capture_script("gf-x.sh", "discord", DISCORD_CFG)
    assert_order(
        s,
        'APP="Discord"',
        "WIN=$($SC list | " + EXPECTED_AWK_SELECT + ")",
        'say "window=$WIN"',
        'WID=$(printf %s "$WIN" | cut -d" " -f1)',
        "$SC cap $WID ~/muse-tools/gf-out/gf-x.sh.png",
        'say "status=capture_failed"',
        "base64 -i ~/muse-tools/gf-out/gf-x.sh.png "
        "> ~/muse-tools/gf-out/gf-x.sh-png.b64",
        'say "status=ok"',
    )
    assert "sips" not in s  # サムネイル化は snapshot だけ


EXPECTED_CLICK_SCRIPT = EXPECTED_HDR + """open -a "Discord"
sleep 0.8
$GH click 150 240
sleep 0.6
say "clicked=150 240"
say "status=ok"
"""


def test_build_click_script_exact_snapshot():
    # build_click_script も既存テストから名前参照ゼロだった。
    s = gui_flow.build_click_script("gf-x.sh", "discord", DISCORD_CFG, (150, 240))
    assert s == EXPECTED_CLICK_SCRIPT
    # 座標は int 化されて埋まる (小数でも現行どおり切り捨て)。
    s2 = gui_flow.build_click_script("gf-x.sh", "discord", DISCORD_CFG, (150.9, 240.2))
    assert 'say "clicked=150 240"' in s2


# --- C-030: 生成スクリプト内の sleep 直書き値 -----------------------------------


def _sleeps(script):
    # try_one の中など字下げされた sleep も対象にする
    return re.findall(r"^\s*sleep ([\d.]+)$", script, flags=re.M)


def test_generated_script_sleep_values_are_fixed():
    ccfg = gui_flow.careful_cfg("discord", DISCORD_CFG)
    cases = {
        # 前面化 0.8 / nav 0.5 / 欄クリック 0.4 / 空確認 0.25+0.3 /
        # 改行 0.15 (2 行で 1 回) / 読み戻し 0.3+0.25+0.3
        "input_discord_2lines": (
            gui_flow.build_input_script("n", "discord", DISCORD_CFG, "1行目\n2行目"),
            ["0.8", "0.5", "0.4", "0.25", "0.3", "0.15", "0.3", "0.25", "0.3"],
        ),
        "input_line": (
            gui_flow.build_input_script("n", "line", LINE_CFG, LINE_TEXT),
            ["0.8", "0.5", "0.4", "0.25", "0.3", "0.3", "0.3", "0.25", "0.3"],
        ),
        "send": (gui_flow.build_send_script("n", "discord", DISCORD_CFG), []),
        "clear_nav": (
            gui_flow.build_clear_script("n", "discord", DISCORD_CFG),
            ["0.6", "0.4", "0.4", "0.25", "0.3", "0.25", "0.3"],
        ),
        "clear_no_nav": (
            gui_flow.build_clear_script(
                "n",
                "discord",
                {k: v for k, v in DISCORD_CFG.items() if k != "nav_click"},
            ),
            ["0.6", "0.4", "0.25", "0.3", "0.25", "0.3"],
        ),
        "readback": (
            gui_flow.build_readback_script("n", "line", LINE_CFG),
            ["0.8", "0.5", "0.4", "0.25", "0.3"],
        ),
        "probe": (
            gui_flow.build_probe_script(
                "n", "discord", DISCORD_CFG, [(700, 852)], ccfg["probe_text"]
            ),
            [
                "0.8",
                "0.5",
                "0.4",
                "0.25",
                "0.3",
                "0.35",
                "0.25",
                "0.2",
                "0.25",
                "0.15",
                "0.2",
                "0.25",
                "0.3",
            ],
        ),
        "dismiss": (
            gui_flow.build_dismiss_script(
                "n", "discord", DISCORD_CFG, (0, 30, 1440, 870), ccfg
            ),
            ["0.5", "0.4", "0.4", "0.3"],
        ),
        "verify": (gui_flow.build_verify_script("n", "discord", DISCORD_CFG), []),
        "capture": (
            gui_flow.build_capture_script("n", "discord", DISCORD_CFG),
            [],
        ),
        "snapshot": (
            gui_flow.build_snapshot_script("n", "discord", DISCORD_CFG, 160),
            [],
        ),
        "click": (
            gui_flow.build_click_script("n", "discord", DISCORD_CFG, (1, 2)),
            ["0.8", "0.6"],
        ),
    }
    for label, (script, expected) in cases.items():
        assert _sleeps(script) == expected, label


# --- C-031: Mac の SSH オプションとキュー待ち ------------------------------------


def test_mac_ssh_base_options_are_fixed():
    mac = gui_flow.Mac(FULL_CFG)
    assert mac.base == [
        "ssh",
        "-i",
        "/tmp/id_test",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ProxyCommand=/tmp/proxy.sh %h %p",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=25",
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPath=/tmp/gf-test-ctl",
        "-o",
        "ControlPersist=180",
        "test-user@192.0.2.1",
    ]


def test_mac_ssh_default_control_path():
    cfg = {"ssh": {k: v for k, v in DUMMY_SSH.items() if k != "control_path"}}
    mac = gui_flow.Mac(cfg)
    assert "ControlPath=/tmp/gf-ssh-ctl" in mac.base


def _capture_run(mac, canned):
    seen = {}

    def fake_run(remote_cmd, stdin_text=None, timeout=None):
        seen["cmd"] = remote_cmd
        seen["stdin"] = stdin_text
        seen["timeout"] = timeout
        return canned

    mac.run = fake_run
    return seen


def test_run_queue_remote_command_structure():
    mac = gui_flow.Mac(FULL_CFG)
    seen = _capture_run(mac, (0, "status=ok\n", ""))
    mac.run_queue("SCRIPT_BODY", "gf-x.sh", extras=("-ocr.txt", "-png.b64"))
    assert seen["stdin"] == "SCRIPT_BODY"
    assert seen["timeout"] == 240  # tunables ssh_timeout の既定
    assert seen["cmd"] == (
        "mkdir -p ~/muse-tools/gf-out; "
        "cat > ~/muse-tools/gf-out/.in-gf-x.sh; "
        "mv ~/muse-tools/gf-out/.in-gf-x.sh ~/muse-tools/gui-queue/gf-x.sh; "
        "n=0; while [ ! -f ~/muse-tools/gui-done/gf-x.sh ]; do "
        "n=$((n+1)); if [ $n -gt 400 ]; then echo GF_WAIT_TIMEOUT; exit 9; fi; "
        "sleep 0.3; done; "
        "cat ~/muse-tools/gf-out/gf-x.sh.result; "
        "echo __GF_FILE__-ocr.txt; "
        "cat ~/muse-tools/gf-out/gf-x.sh-ocr.txt 2>/dev/null; "
        "echo __GF_EOF__-ocr.txt; "
        "echo __GF_FILE__-png.b64; "
        "cat ~/muse-tools/gf-out/gf-x.sh-png.b64 2>/dev/null; "
        "echo __GF_EOF__-png.b64"
    )


def test_run_queue_timeout_and_value_with_equals():
    mac = gui_flow.Mac(FULL_CFG)
    _capture_run(mac, (9, "pid=12=34\nGF_WAIT_TIMEOUT\n", ""))
    res = mac.run_queue("body", "gf-x.sh")
    assert res["status"] == "queue_timeout"  # 完了待ち上限 (400 回) の現行表現
    assert res["pid"] == "12=34"  # key=value は最初の = でだけ割る
    assert res["_ssh_rc"] == 9


# --- C-027: Runner 撮影系の窓パース (4 か所の逐語重複の共有挙動) -----------------


def _canned_runner(monkeypatch, window_marker, status="ok"):
    r = _runner()
    _no_sleep(monkeypatch)

    def fake_run_queue(script, name, extras=()):
        res = {
            "status": status,
            "_files": {
                "-ocr.txt": "",
                "-header-ocr.txt": "",
                "-png.b64": FAKE_PNG_B64,
            },
            "_ssh_rc": 0,
        }
        if window_marker is not None:
            res["window"] = window_marker
        return res

    monkeypatch.setattr(r.mac, "run_queue", fake_run_queue)
    return r


@pytest.mark.parametrize(
    "window,expected_frame",
    [
        ("767 0 30 1440 870", (0, 30, 1440, 870)),
        # 小数表記は int(float()) で切り捨てられる (30.5 -> 30)
        ("767.0 0.0 30.5 1440.0 870.0", (0, 30, 1440, 870)),
        # window キー欠落時は "0 0 0 0 0" 扱いでフレームは (0,0,0,0)
        (None, (0, 0, 0, 0)),
        # 要素数が 5 でない場合はフレームなし
        ("1 2 3 4", None),
        ("1 2 3 4 5 6", None),
    ],
)
def test_window_parse_is_consistent_across_capture_paths(
    tmp_path, monkeypatch, window, expected_frame
):
    monkeypatch.setenv("GUI_FLOW_SCREENSHOTS", str(tmp_path / "shots"))

    r = _canned_runner(monkeypatch, window)
    frame, _items = r.capture_ocr()
    assert frame == expected_frame

    r = _canned_runner(monkeypatch, window)
    frame, _runs = r.capture_header_runs()
    assert frame == expected_frame

    r = _canned_runner(monkeypatch, window)
    _path, frame = r.careful_capture("window")
    assert frame == expected_frame

    r = _canned_runner(monkeypatch, window)
    r.verify(DISCORD_TEXT, "field")
    assert r.last_frame == expected_frame


def test_capture_paths_on_failure_status(tmp_path, monkeypatch):
    monkeypatch.setenv("GUI_FLOW_SCREENSHOTS", str(tmp_path / "shots"))
    r = _canned_runner(monkeypatch, "767 0 30 1440 870", status="window_not_found")
    assert r.capture_ocr() == (None, [])
    r = _canned_runner(monkeypatch, "767 0 30 1440 870", status="window_not_found")
    assert r.capture_header_runs() == (None, [])
    r = _canned_runner(monkeypatch, "767 0 30 1440 870", status="capture_failed")
    assert r.careful_capture("window") == (None, None)
    shot = [c for c in r.checks if c["name"] == "careful_capture"]
    assert shot and shot[-1]["ok"] is False


def test_careful_capture_saves_evidence_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GUI_FLOW_SCREENSHOTS", str(tmp_path / "shots"))
    r = _canned_runner(monkeypatch, "767 0 30 1440 870")
    path, frame = r.careful_capture("window")
    assert frame == (0, 30, 1440, 870)
    assert re.fullmatch(r".*/careful-discord-\d{8}-\d{6}-window\.png", path)
    with open(path, "rb") as f:
        assert f.read() == FAKE_PNG
    assert r.careful_screenshot == path


# --- C-024: Runner の公開挙動 (checks / steps の形) -------------------------------


def test_runner_queue_name_format_and_counter(monkeypatch):
    r = _runner()
    monkeypatch.setattr(gui_flow.time, "time", lambda: 1762000000.0)
    n1 = r._name("input")
    n2 = r._name("verify")
    assert n1 == "gf-discord-input-1762000000000-1.sh"
    assert n2 == "gf-discord-verify-1762000000000-2.sh"


def _queue_runner(monkeypatch, responses):
    """run_queue を名前 (action 部分) で振り分ける Runner を作る。"""
    r = _runner()
    _no_sleep(monkeypatch)
    calls = []

    def fake_run_queue(script, name, extras=()):
        calls.append(name)
        for key, res in responses.items():
            if "-%s-" % key in name:
                return res
        raise AssertionError("想定外のキュー投入: %s" % name)

    monkeypatch.setattr(r.mac, "run_queue", fake_run_queue)
    return r, calls


def test_input_success_checks_and_steps_shape(monkeypatch):
    r, _calls = _queue_runner(
        monkeypatch,
        {
            "input": {
                "status": "ok",
                "pid": "1234",
                "_files": {"-readback.txt": DISCORD_TEXT, "-leftover.txt": ""},
                "_ssh_rc": 0,
            }
        },
    )
    assert r.input(DISCORD_TEXT) is True
    assert [c["name"] for c in r.checks] == [
        "input_status",
        "field_empty_before",
        "readback_match",
    ]
    assert all(c["ok"] for c in r.checks)
    assert r.checks[0]["evidence"] == "status=ok pid=1234"
    assert [s["name"] for s in r.steps] == ["input"]
    assert isinstance(r.steps[0]["sec"], float)


def test_input_leftover_checks_shape(monkeypatch):
    r, _calls = _queue_runner(
        monkeypatch,
        {
            "input": {
                "status": "anomaly_leftover",
                "pid": "1234",
                "_files": {"-leftover.txt": "下書き残り"},
                "_ssh_rc": 0,
            }
        },
    )
    assert r.input(DISCORD_TEXT) is False
    assert [(c["name"], c["ok"]) for c in r.checks] == [
        ("input_status", False),
        ("field_empty_before", False),
    ]
    assert "下書き残り" in r.checks[1]["evidence"]


def test_clear_result_requires_sentinel_field_empty(monkeypatch):
    ok_res = {"status": "ok", "field_empty": "yes", "_files": {}, "_ssh_rc": 0}
    r, _ = _queue_runner(monkeypatch, {"clear": ok_res})
    assert r.clear() is True
    assert [(c["name"], c["ok"]) for c in r.checks] == [("field_cleared", True)]

    no_res = {"status": "ok", "field_empty": "no", "_files": {}, "_ssh_rc": 0}
    r, _ = _queue_runner(monkeypatch, {"clear": no_res})
    assert r.clear() is False
    assert [(c["name"], c["ok"]) for c in r.checks] == [("field_cleared", False)]


def test_readback_field_returns_raw_text_and_none_on_empty(monkeypatch):
    ok_res = {
        "status": "ok",
        "_files": {"-field.txt": DISCORD_TEXT + "\n"},
        "_ssh_rc": 0,
    }
    r, _ = _queue_runner(monkeypatch, {"readback": ok_res})
    # 戻り値はファイル内容そのまま (strip しない)
    assert r.readback_field() == DISCORD_TEXT + "\n"
    assert r.checks[-1]["name"] == "field_readback"
    assert r.checks[-1]["ok"] is True

    empty_res = {"status": "field_empty", "_files": {}, "_ssh_rc": 0}
    r, _ = _queue_runner(monkeypatch, {"readback": empty_res})
    assert r.readback_field() is None
    assert r.checks[-1]["ok"] is False
    assert "status=field_empty" in r.checks[-1]["evidence"]


def test_send_requires_send_key_sent_flag(monkeypatch):
    base = {"status": "ok", "pid": "1234", "_files": {}, "_ssh_rc": 0}
    r, _ = _queue_runner(monkeypatch, {"send": dict(base, send_key_sent="1")})
    assert r.send() is True
    # status=ok でも送信キー送信の記録が無ければ成功にしない
    r, _ = _queue_runner(monkeypatch, {"send": dict(base)})
    assert r.send() is False
    assert r.checks[-1]["name"] == "send_key"
    assert r.checks[-1]["ok"] is False


def test_capture_png_outcomes(monkeypatch):
    ok_res = {
        "status": "ok",
        "_files": {"-png.b64": FAKE_PNG_B64},
        "_ssh_rc": 0,
    }
    r, _ = _queue_runner(monkeypatch, {"capture": ok_res})
    assert r.capture_png() is True
    assert r.last_png_bytes == FAKE_PNG

    r, _ = _queue_runner(
        monkeypatch, {"capture": dict(ok_res, status="window_not_found")}
    )
    assert r.capture_png() is False
    assert r.last_png_bytes is None

    bad = {"status": "ok", "_files": {"-png.b64": "!!!not-base64!!!"}, "_ssh_rc": 0}
    r, _ = _queue_runner(monkeypatch, {"capture": bad})
    assert r.capture_png() is False


def test_snapshot_thumb_uses_configured_width_and_decodes(monkeypatch):
    r = _runner()
    _no_sleep(monkeypatch)
    seen = {}

    def fake_run_queue(script, name, extras=()):
        seen["script"] = script
        seen["extras"] = extras
        return {
            "status": "ok",
            "_files": {"-thumb.b64": FAKE_PNG_B64},
            "_ssh_rc": 0,
        }

    monkeypatch.setattr(r.mac, "run_queue", fake_run_queue)
    ccfg = gui_flow.careful_cfg("discord", r.acfg)
    assert r.snapshot_thumb(ccfg) == FAKE_PNG
    assert "sips -Z 160 " in seen["script"]  # dismiss_thumb_width の既定
    assert seen["extras"] == ("-thumb.b64",)

    monkeypatch.setattr(
        r.mac,
        "run_queue",
        lambda script, name, extras=(): {
            "status": "capture_failed",
            "_files": {},
            "_ssh_rc": 0,
        },
    )
    assert r.snapshot_thumb(ccfg) is None


def test_click_point_result_and_script(monkeypatch):
    r = _runner()
    _no_sleep(monkeypatch)
    seen = {}

    def fake_run_queue(script, name, extras=()):
        seen["script"] = script
        return {"status": "ok", "_files": {}, "_ssh_rc": 0}

    monkeypatch.setattr(r.mac, "run_queue", fake_run_queue)
    assert r.click_point((150, 240)) is True
    assert 'say "clicked=150 240"' in seen["script"]

    monkeypatch.setattr(
        r.mac,
        "run_queue",
        lambda script, name, extras=(): {
            "status": "no_result",
            "_files": {},
            "_ssh_rc": 0,
        },
    )
    assert r.click_point((150, 240)) is False


# --- C-025: main の CLI 分岐 -------------------------------------------------------


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


def test_main_connect_failed(tmp_path, monkeypatch, capsys):
    msg = _write_main_cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        gui_flow.Mac,
        "run",
        lambda self, cmd, stdin_text=None, timeout=None: (1, "", "refused"),
    )
    code, out = _run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "discord", "input", "--text-file", str(msg)],
    )
    assert code == 2
    assert out["ok"] is False
    assert out["error"] == "connect_failed"
    assert out["screenshot"] is None
    assert [c["name"] for c in out["checks"]] == ["connect"]
    assert out["checks"][0]["ok"] is False


def test_main_timeout_is_reported_as_timeout(tmp_path, monkeypatch, capsys):
    msg = _write_main_cfg(tmp_path, monkeypatch)

    def boom(self, cmd, stdin_text=None, timeout=None):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=1)

    monkeypatch.setattr(gui_flow.Mac, "run", boom)
    code, out = _run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "discord", "input", "--text-file", str(msg)],
    )
    assert code == 2
    assert out["ok"] is False
    assert out["error"] == "timeout"


def test_main_post_without_approval_never_touches_field(tmp_path, monkeypatch, capsys):
    msg = _write_main_cfg(tmp_path, monkeypatch)
    _mac_ok(monkeypatch)

    def boom_input(self, text):
        raise AssertionError("承認が無いのに欄へ触ってはいけない")

    monkeypatch.setattr(gui_flow.Runner, "input", boom_input)
    code, out = _run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "discord", "post", "--text-file", str(msg)],
    )
    assert code == 2
    assert out["ok"] is False
    assert out["error"] == "approval_missing"
    gate = [c for c in out["checks"] if c["name"] == "approval"]
    assert gate and gate[-1]["ok"] is False


def test_main_post_success_passes_retries_and_screenshot(tmp_path, monkeypatch, capsys):
    msg = _write_main_cfg(tmp_path, monkeypatch)
    _mac_ok(monkeypatch)
    _no_sleep(monkeypatch)
    gui_flow.create_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    seen = {}

    def fake_run_post(r, text, max_retries=None, on_sent=None):
        seen["max_retries"] = max_retries
        if on_sent is not None:
            on_sent()
        return True, None, {"failed_checks": [], "diff_summary": ""}

    monkeypatch.setattr(gui_flow, "run_post", fake_run_post)
    monkeypatch.setattr(
        gui_flow.Runner,
        "store_post_send_screenshot",
        lambda self: "/tmp/shots/gui-flow-discord-20261107-120000.png",
    )
    code, out = _run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "discord", "post", "--text-file", str(msg)],
    )
    assert code == 0
    assert out["ok"] is True
    assert out["error"] is None
    assert seen["max_retries"] == 2  # tunables max_pre_send_retries の既定
    assert out["retry"] == {"failed_checks": [], "diff_summary": ""}
    assert out["screenshot"] == "/tmp/shots/gui-flow-discord-20261107-120000.png"
    # on_sent 経由で承認が消費されている
    ok, reason, _rec = gui_flow.check_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    assert ok is False
    assert reason == "used"


def _send_flow_queue(monkeypatch, field):
    """send アクション用のキュー応答 (readback / send / capture)。"""
    queued = []

    def fake_run_queue(self, script, name, extras=()):
        queued.append(name)
        if "-readback-" in name:
            if field is None:
                return {"status": "field_empty", "_files": {}, "_ssh_rc": 0}
            return {
                "status": "ok",
                "_files": {"-field.txt": field},
                "_ssh_rc": 0,
            }
        if "-send-" in name:
            return {
                "status": "ok",
                "pid": "1234",
                "send_key_sent": "1",
                "_files": {},
                "_ssh_rc": 0,
            }
        if "-capture-" in name:
            return {
                "status": "ok",
                "_files": {"-png.b64": FAKE_PNG_B64},
                "_ssh_rc": 0,
            }
        raise AssertionError("想定外のキュー投入: %s" % name)

    monkeypatch.setattr(gui_flow.Mac, "run_queue", fake_run_queue)
    return queued


def test_main_send_success_consumes_approval_and_saves_screenshot(
    tmp_path, monkeypatch, capsys
):
    _write_main_cfg(tmp_path, monkeypatch)
    _mac_ok(monkeypatch)
    _no_sleep(monkeypatch)
    gui_flow.create_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    queued = _send_flow_queue(monkeypatch, DISCORD_TEXT)
    code, out = _run_main(monkeypatch, capsys, ["gui_flow.py", "discord", "send"])
    assert code == 0
    assert out["ok"] is True
    assert out["error"] is None
    names = [c["name"] for c in out["checks"]]
    assert names == [
        "connect",
        "field_readback",
        "approval",
        "send_key",
        "approval_consumed",
        "screenshot",
    ]
    assert all(c["ok"] for c in out["checks"])
    assert re.fullmatch(r".*/gui-flow-discord-\d{8}-\d{6}\.png", out["screenshot"])
    assert any("-readback-" in n for n in queued)
    assert any("-send-" in n for n in queued)
    ok, reason, _rec = gui_flow.check_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    assert (ok, reason) == (False, "used")


def test_main_send_field_empty_stops_before_gate(tmp_path, monkeypatch, capsys):
    _write_main_cfg(tmp_path, monkeypatch)
    _mac_ok(monkeypatch)
    _no_sleep(monkeypatch)
    gui_flow.create_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    queued = _send_flow_queue(monkeypatch, None)
    code, out = _run_main(monkeypatch, capsys, ["gui_flow.py", "discord", "send"])
    assert code == 2
    assert out["ok"] is False
    # 現行挙動: 欄が空の読み戻し失敗は、main の異常分類で
    # field_readback_failed から needs_calibration へ差し替わる
    # (send は差し替え対象アクション。根拠は checks 側に残る)。
    assert out["error"] == "needs_calibration"
    assert out["checks"][-1]["name"] == "calibration"
    readback = [c for c in out["checks"] if c["name"] == "field_readback"]
    assert readback and readback[-1]["ok"] is False
    assert "status=field_empty" in readback[-1]["evidence"]
    assert not any("-send-" in n for n in queued)  # 送信キーは送らない
    ok, _reason, _rec = gui_flow.check_approval("discord", DISCORD_CFG, DISCORD_TEXT)
    assert ok is True  # 承認は消費されない


def test_main_send_without_approval_stops_before_send_key(
    tmp_path, monkeypatch, capsys
):
    _write_main_cfg(tmp_path, monkeypatch)
    _mac_ok(monkeypatch)
    _no_sleep(monkeypatch)
    queued = _send_flow_queue(monkeypatch, DISCORD_TEXT)
    code, out = _run_main(monkeypatch, capsys, ["gui_flow.py", "discord", "send"])
    assert code == 2
    assert out["ok"] is False
    assert out["error"] == "approval_missing"
    assert not any("-send-" in n for n in queued)


def test_main_input_failure_can_be_reclassified_needs_calibration(
    tmp_path, monkeypatch, capsys
):
    msg = _write_main_cfg(tmp_path, monkeypatch)
    _mac_ok(monkeypatch)

    def fake_input(self, text):
        self.checks.append(
            {
                "name": "input_status",
                "ok": False,
                "evidence": "status=fail_readback_empty pid=1234",
            }
        )
        return False

    monkeypatch.setattr(gui_flow.Runner, "input", fake_input)
    code, out = _run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "discord", "input", "--text-file", str(msg)],
    )
    assert code == 2
    assert out["error"] == "needs_calibration"
    assert out["checks"][-1]["name"] == "calibration"
    assert out["checks"][-1]["ok"] is False


def test_main_verify_failure_is_not_reclassified(tmp_path, monkeypatch, capsys):
    msg = _write_main_cfg(tmp_path, monkeypatch)
    _mac_ok(monkeypatch)

    def fake_verify(self, text, mode, fetch_png=False):
        self.checks.append(
            {
                "name": "input_status",
                "ok": False,
                "evidence": "status=fail_readback_empty pid=1234",
            }
        )
        return False

    monkeypatch.setattr(gui_flow.Runner, "verify", fake_verify)
    code, out = _run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "discord", "verify", "--text-file", str(msg)],
    )
    assert code == 2
    assert out["ok"] is False
    # 差し替えは post / input / send のみ。verify は error=None のまま。
    assert out["error"] is None
    assert all(c["name"] != "calibration" for c in out["checks"])


@pytest.mark.parametrize(
    "relearn_error,overlay_blocked,expected",
    [
        ("recipient_not_found", False, "recipient_not_found"),
        (None, True, "overlay_unresolved"),
        (None, False, "careful_failed"),
    ],
)
def test_main_careful_error_mapping(
    tmp_path, monkeypatch, capsys, relearn_error, overlay_blocked, expected
):
    _write_main_cfg(tmp_path, monkeypatch)
    _mac_ok(monkeypatch)

    def fake_careful(self, to_name=None):
        self.relearn_error = relearn_error
        self.overlay_blocked = overlay_blocked
        return False, [], False

    monkeypatch.setattr(gui_flow.Runner, "careful", fake_careful)
    code, out = _run_main(monkeypatch, capsys, ["gui_flow.py", "discord", "careful"])
    assert code == 2
    assert out["ok"] is False
    assert out["error"] == expected
    assert out["learned"] == []
    assert out["self_check"] == {"ok": False}
    assert out["screenshot"] is None


def test_main_test_action_runs_input_verify_clear(tmp_path, monkeypatch, capsys):
    msg = _write_main_cfg(tmp_path, monkeypatch)
    _mac_ok(monkeypatch)
    calls = []
    monkeypatch.setattr(
        gui_flow.Runner, "input", lambda self, text: calls.append("input") or True
    )
    monkeypatch.setattr(
        gui_flow.Runner,
        "verify",
        lambda self, text, mode, fetch_png=False: calls.append("verify") or True,
    )
    monkeypatch.setattr(
        gui_flow.Runner, "clear", lambda self: calls.append("clear") or True
    )
    code, out = _run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "discord", "test", "--text-file", str(msg)],
    )
    assert code == 0
    assert out["ok"] is True
    assert calls == ["input", "verify", "clear"]
    assert out["screenshot"] is None  # test では送信後スクショを保存しない


def test_main_test_action_short_circuits_on_input_failure(
    tmp_path, monkeypatch, capsys
):
    msg = _write_main_cfg(tmp_path, monkeypatch)
    _mac_ok(monkeypatch)
    calls = []
    monkeypatch.setattr(
        gui_flow.Runner, "input", lambda self, text: calls.append("input") or False
    )
    monkeypatch.setattr(
        gui_flow.Runner,
        "verify",
        lambda self, text, mode, fetch_png=False: calls.append("verify") or True,
    )
    monkeypatch.setattr(
        gui_flow.Runner, "clear", lambda self: calls.append("clear") or True
    )
    code, out = _run_main(
        monkeypatch,
        capsys,
        ["gui_flow.py", "discord", "test", "--text-file", str(msg)],
    )
    assert code == 2
    assert out["ok"] is False
    assert calls == ["input"]  # 入力が落ちたら検閲・消去へ進まない


# --- C-033: 定数・既定値の二重管理 -----------------------------------------------


def test_example_config_tunables_match_default_tunables():
    example_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "config.example.json",
    )
    with open(example_path, encoding="utf-8") as f:
        example = json.load(f)
    # 二重管理の片方だけが変わると即検知できるよう、全文一致で固定。
    assert example["tunables"] == gui_flow.DEFAULT_TUNABLES
    assert gui_flow.tunables(example) == gui_flow.DEFAULT_TUNABLES


def test_self_check_texts_start_with_first_line():
    for app in ("discord", "line"):
        lines = gui_flow.content_lines(app, gui_flow.SELF_CHECK_TEXTS[app])
        assert lines[0] == gui_flow.FIRST_LINE[app]
        # careful の既定の自己確認文面は SELF_CHECK_TEXTS そのもの
        ccfg = gui_flow.careful_cfg(app, {})
        assert ccfg["self_check_text"] == gui_flow.SELF_CHECK_TEXTS[app]


def test_approval_ttl_two_definitions_agree(tmp_path, monkeypatch):
    # モジュール定数 (APPROVAL_DEFAULT_TTL) と tunables
    # (approval_ttl_hours) は現行どちらも 24 時間で一致している。
    assert gui_flow.APPROVAL_DEFAULT_TTL == timedelta(hours=24)
    assert gui_flow.DEFAULT_TUNABLES["approval_ttl_hours"] == 24
    monkeypatch.setenv("GUI_FLOW_APPROVALS", str(tmp_path / "approvals"))
    now = datetime(2026, 11, 7, 12, 0, tzinfo=gui_flow.JST)
    rec = gui_flow.create_approval("discord", DISCORD_CFG, DISCORD_TEXT, now=now)
    created = gui_flow._parse_dt(rec["created_at"])
    expires = gui_flow._parse_dt(rec["expires_at"])
    assert expires - created == timedelta(hours=24)
