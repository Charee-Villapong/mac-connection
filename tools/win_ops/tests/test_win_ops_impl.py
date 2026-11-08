"""win_ops の impl 層回帰テスト (実装係が追加)。

spec 層 (test_win_ops_spec.py) が扱わない、実データ形式への適合を守る:
  - agent-desktop の実エンベロープ {"ok","data":{...}} と要素の ref_id キー
  - --include-bounds の float bounds と実測の閉じるボタン位置
  - キュー投入スクリプト同士の混線防止 (振り分け語句を含まないこと)
  - 実 transport (Mac.run_queue) の結果解析
"""

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spec_fixtures import (  # noqa: E402
    CLOSE_TREE_OK,
    LIST_TEXT_MEMO_ONLY,
    StubTransport,
    ax_json,
    click_json,
    make_config,
    queue_ok,
)

import win_ops  # noqa: E402


def _real_element(ref_id, role, name, bounds, children=()):
    x, y, w, h = bounds
    return {
        "ref_id": ref_id,
        "role": role,
        "name": name,
        "states": [],
        "available_actions": ["AXPress"],
        "native_id": "",
        "bounds": {"x": float(x), "y": float(y), "width": float(w), "height": float(h)},
        "children": list(children),
    }


def _real_envelope(tree):
    return {
        "version": "1.2.0",
        "ok": True,
        "command": "snapshot",
        "data": {
            "app": "メモ",
            "complete": True,
            "ref_count": 3,
            "snapshot_id": "snap-real-1",
            "tree": tree,
            "window": {"x": 100, "y": 50, "width": 800, "height": 600},
        },
    }


REAL_TREE = _real_element(
    "@snap-real-1:e1",
    "window",
    "メモ",
    (100, 50, 800, 600),
    children=[
        _real_element("@snap-real-1:e2", "button", "設定を開く", (120, 80, 120, 30)),
        # 実測の閉じるボタン: 窓左上から中心が約 (+16,+16) の 16x16
        _real_element("@snap-real-1:e3", "button", "Close", (108, 58, 16, 16)),
    ],
)


def test_iter_elements_normalizes_ref_id():
    refs = {e["ref"] for e in win_ops.iter_elements(REAL_TREE)}
    assert refs == {"@snap-real-1:e1", "@snap-real-1:e2", "@snap-real-1:e3"}


def test_find_by_name_on_real_tree():
    found = win_ops.find_elements_by_name(REAL_TREE, "設定を開く")
    assert len(found) == 1
    assert found[0]["ref"] == "@snap-real-1:e2"


def test_extract_tree_from_real_envelope():
    tree = win_ops.extract_tree(_real_envelope(REAL_TREE))
    assert tree is REAL_TREE


def test_close_button_real_measured_position():
    frame = {"x": 100, "y": 50, "width": 800, "height": 600}
    found = win_ops.find_close_button_candidates(
        REAL_TREE, frame, offset_px=14, tolerance_px=12
    )
    assert len(found) == 1
    assert found[0]["ref"] == "@snap-real-1:e3"


def test_ax_real_envelope_passthrough_and_press_by_name(tmp_path):
    envelope = _real_envelope(REAL_TREE)

    def handler(script, name, extras):
        if "click" in script:
            return queue_ok(
                {".click.json": json.dumps({"version": "1.2.0", "ok": True})}
            )
        return queue_ok({".ax.json": json.dumps(envelope, ensure_ascii=False)})

    transport = StubTransport(handler)
    ops = win_ops.WinOps(transport=transport, config=make_config(tmp_path))
    result = ops.snapshot(app="メモ")
    assert result["ok"] is True
    assert result["snapshot"] == envelope

    pressed = ops.press(app="メモ", name="設定を開く")
    assert pressed["ok"] is True
    clicks = transport.scripts_containing("click")
    assert len(clicks) == 1
    assert "@snap-real-1:e2" in clicks[0]


def test_scripts_do_not_cross_contain_routing_words():
    list_script = win_ops.build_list_script("winops-t-1.sh", "/tmp/ScreenCheck")
    ax_script = win_ops.build_ax_script(
        "winops-t-2.sh", app="メモ", agent_desktop_path="/tmp/agent-desktop"
    )
    click_script = win_ops.build_click_script(
        "winops-t-3.sh",
        ref="@snap-real-1:e2",
        agent_desktop_path="/tmp/agent-desktop",
    )
    # list は cap と誤振り分けされない、ax/click は ScreenCheck を含まない
    assert "cap" not in list_script
    assert "ScreenCheck" not in ax_script and "click" not in ax_script
    assert "ScreenCheck" not in click_script and "snapshot" not in click_script
    for script in (list_script, ax_script, click_script):
        assert "__NAME__" not in script


def test_ax_script_embeds_app_via_base64():
    script = win_ops.build_ax_script(
        "winops-t-4.sh", app="メモ", agent_desktop_path="/tmp/agent-desktop"
    )
    encoded = base64.b64encode("メモ".encode("utf-8")).decode("ascii")
    assert encoded in script
    assert "base64 -D" in script


def test_tty_script_uses_osascript_and_ps_without_routing_words():
    script = win_ops._build_tty_script(
        "winops-t-5.sh", app="ターミナル", title="作業 — zsh"
    )
    assert "osascript" in script
    assert "ps -t" in script
    assert "tty" in script
    for word in ("ScreenCheck", "click", "snapshot"):
        assert word not in script


def test_queue_names_end_with_sh(tmp_path):
    transport = StubTransport(lambda s, n, e: queue_ok({".list": ""}))
    ops = win_ops.WinOps(transport=transport, config=make_config(tmp_path))
    ops.list_windows()
    assert transport.calls
    assert all(c["name"].endswith(".sh") for c in transport.calls)


def test_mac_run_queue_parses_result_and_files(monkeypatch):
    cfg = {
        "ssh": {
            "host": "192.0.2.1",
            "user": "example-user",
            "identity": "/path/to/id_ed25519",
            "proxy_script": "/path/to/ts-proxy.sh",
        },
        "tunables": {"ssh_timeout": 10, "connect_timeout": 5},
    }
    mac = win_ops.Mac(cfg)
    out = (
        "status=ok\ntty=/dev/ttys003\n"
        "__GF_FILE__.ps\n  PID COMM\n  501 login\n__GF_EOF__.ps\n"
    )
    monkeypatch.setattr(
        mac, "run", lambda cmd, stdin_text=None, timeout=None: (0, out, "")
    )
    result = mac.run_queue("script-body", "winops-x-1.sh", extras=(".ps",))
    assert result["status"] == "ok"
    assert result["tty"] == "/dev/ttys003"
    assert "501 login" in result["_files"][".ps"]
    assert result["_ssh_rc"] == 0


def test_mac_run_queue_detects_timeout(monkeypatch):
    cfg = {
        "ssh": {
            "host": "192.0.2.1",
            "user": "example-user",
            "identity": "/path/to/id_ed25519",
            "proxy_script": "/path/to/ts-proxy.sh",
        }
    }
    mac = win_ops.Mac(cfg)
    monkeypatch.setattr(
        mac,
        "run",
        lambda cmd, stdin_text=None, timeout=None: (9, "GF_WAIT_TIMEOUT\n", ""),
    )
    result = mac.run_queue("script-body", "winops-x-2.sh")
    assert result["status"] == "queue_timeout"


def _mac_cfg(tunables=None):
    cfg = {
        "ssh": {
            "host": "192.0.2.1",
            "user": "example-user",
            "identity": "/path/to/id_ed25519",
            "proxy_script": "/path/to/ts-proxy.sh",
        }
    }
    if tunables is not None:
        cfg["tunables"] = tunables
    return cfg


def test_mac_ssh_options_use_connect_timeout_tunable():
    mac = win_ops.Mac(_mac_cfg({"ssh_timeout": 10, "connect_timeout": 42}))
    assert mac.connect_timeout == 42
    assert "ConnectTimeout=42" in mac.base
    assert "ConnectTimeout=25" not in mac.base


def test_mac_ssh_options_default_connect_timeout():
    mac = win_ops.Mac(_mac_cfg())
    assert "ConnectTimeout=60" in mac.base


def test_main_without_config_returns_2(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("WIN_OPS_CONFIG", str(tmp_path / "missing.json"))
    rc = win_ops.main(["list"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "list"


@pytest.mark.parametrize("status", ["window_not_found", "busy"])
def test_winopserror_status_attribute(status):
    err = win_ops.WinOpsError(status)
    assert err.status == status


# --- close のセレクタ無指定の拒否 (必須1) ------------------------------------


def _single_window_close_transport():
    """窓が 1 件だけある状態で、無指定 close が進めば click まで届くスタブ."""

    def handler(script, name, extras):
        if "click" in script:
            return queue_ok({".click.json": click_json(True)})
        if "snapshot" in script:
            return queue_ok({".ax.json": ax_json(CLOSE_TREE_OK)})
        return queue_ok({".list": LIST_TEXT_MEMO_ONLY})

    return StubTransport(handler)


def test_close_without_selector_rejected_and_no_click(tmp_path):
    transport = _single_window_close_transport()
    ops = win_ops.WinOps(transport=transport, config=make_config(tmp_path))
    result = ops.close()
    assert result["ok"] is False
    assert result["command"] == "close"
    assert result["status"] == "invalid_argument"
    assert transport.scripts_containing("click") == []
    assert transport.calls == []


def test_close_with_only_app_rejected_and_no_click(tmp_path):
    transport = _single_window_close_transport()
    ops = win_ops.WinOps(transport=transport, config=make_config(tmp_path))
    result = ops.close(app="メモ")
    assert result["ok"] is False
    assert result["status"] == "invalid_argument"
    assert transport.scripts_containing("click") == []
    assert transport.calls == []


# --- ax の surface 検証 (推奨3) ----------------------------------------------


@pytest.mark.parametrize(
    "bad_surface",
    [
        "not-a-surface",
        "WINDOW",
        "window; rm -rf /",
        "window$(id)",
        "../window",
        "window --help",
    ],
)
def test_build_ax_script_rejects_invalid_surface(bad_surface):
    with pytest.raises(win_ops.WinOpsError) as excinfo:
        win_ops.build_ax_script("winops-t-bad.sh", app="メモ", surface=bad_surface)
    assert excinfo.value.status == "invalid_argument"


@pytest.mark.parametrize(
    "surface", ["window", "focused", "menu-bar-extras", "notification-center"]
)
def test_build_ax_script_accepts_allowed_surfaces(surface):
    script = win_ops.build_ax_script("winops-t-ok.sh", app="メモ", surface=surface)
    assert "--surface %s" % surface in script


def test_ax_invalid_surface_no_script_queued(tmp_path):
    transport = StubTransport(lambda s, n, e: queue_ok({".ax.json": "{}"}))
    ops = win_ops.WinOps(transport=transport, config=make_config(tmp_path))
    result = ops.snapshot(app="メモ", surface="window; rm -rf /")
    assert result["ok"] is False
    assert result["command"] == "ax"
    assert result["status"] == "invalid_argument"
    assert transport.calls == []


def test_press_invalid_surface_no_script_queued(tmp_path):
    transport = StubTransport(lambda s, n, e: queue_ok({".ax.json": "{}"}))
    ops = win_ops.WinOps(transport=transport, config=make_config(tmp_path))
    result = ops.press(app="メモ", name="設定を開く", surface="bad surface")
    assert result["ok"] is False
    assert result["status"] == "invalid_argument"
    assert transport.scripts_containing("click") == []
    assert transport.calls == []


@pytest.mark.parametrize("argv", [["close"], ["close", "--app", "メモ"]])
def test_main_close_without_selector_returns_json_and_2(capsys, argv):
    rc = win_ops.main(argv)
    assert rc == 2
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["command"] == "close"
    assert payload["status"] == "invalid_argument"
    assert payload.get("error")
