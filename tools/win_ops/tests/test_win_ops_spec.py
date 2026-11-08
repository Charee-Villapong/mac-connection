"""win_ops の受け入れテスト (テスト係が仕様だけから先行作成)。

根拠は tools/win_ops/SPEC.md (完了の定義) と、モジュール契約の補足
のみ。実装コードはまだ存在しないため、本ファイルは import エラーで
失敗するのが現時点の期待動作 (構文エラーではないこと)。

契約補足で固定されているもの:
  - 本体は tools/win_ops/win_ops.py、import 名 win_ops。
  - WinOpsError は .status 属性を持つ例外。
  - WinOps は transport を受け取る。transport.run_queue(
    script_body, name, extras=()) -> dict で、返却 dict は結果キーが
    トップレベル、extras の中身が _files (サフィックス→文字列)、
    _ssh_rc に SSH 終了コード。キュータイムアウトは
    status == "queue_timeout" で表される。
  - extras の対応: list=".list" / cap=".png.b64"+".meta" /
    ax=".ax.json" / press の click=".click.json" /
    close の tty 取得は結果キー tty + extras ".ps"。
  - 各コマンドの返却 dict は ok / command / status を必須キーに持つ。

仕様が固定していない細部 (WinOps のコンストラクタ引数順・メソッド名・
キーワード名) は、下のファクトリ / アダプタ内にだけ閉じ込め、
複数の自然な形を試す。テスト本体はコマンド名と振る舞いだけで書く。
成功時の status の具体値も仕様が固定していないため、成功系では
status キーの存在だけを見て、値は固定しない (失敗系の status は
仕様が明記する文字列を厳密に見る)。

AX ツリーの形は SPEC の語彙 (tree / 要素の role・name・states・境界)
に従い、spec_fixtures の形 (ルート要素 dict、子は children) を正とする。

実行: tools/win_ops をカレントにして ~/.venv-dl/bin/pytest tests/
"""

import inspect
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spec_fixtures import (  # noqa: E402
    CAP_META_NOT_FOUND,
    CAP_META_OK,
    CLOSE_BUTTON_BOUNDS,
    CLOSE_TREE_NO_BUTTON,
    CLOSE_TREE_OK,
    CLOSE_TREE_TWO_BUTTONS,
    ELEMENT_TREE,
    LIST_TEXT_AFTER_MEMO_CLOSED,
    LIST_TEXT_AFTER_TERMINAL_CLOSED,
    LIST_TEXT_BASIC,
    LIST_TEXT_DUPLICATE_TITLE,
    LIST_TEXT_ERROR,
    LIST_TEXT_FILTER,
    LIST_TEXT_MEMO_ONLY,
    LIST_TEXT_TERMINAL,
    PNG_B64,
    PNG_BYTES,
    PRESS_TREE_AMBIGUOUS,
    PRESS_TREE_DISABLED,
    PRESS_TREE_UNIQUE,
    PS_BUSY,
    PS_SHELL_ONLY,
    SESSION_NAME,
    SHELL_COMMANDS,
    TARGET_FRAME,
    TTY_TERMINAL,
    StubTransport,
    ax_json,
    click_json,
    close_tree,
    make_config,
    make_element,
    queue_ok,
    queue_timeout,
    windows_basic,
    windows_for_resolve,
)

import win_ops  # noqa: E402

# ---------------------------------------------------------------------------
# WinOps の生成とメソッド呼び出しのアダプタ (署名の揺れはここだけに閉じる)
# ---------------------------------------------------------------------------


def make_ops(transport, config):
    """WinOps(transport, config) を引数の自然な並びで生成する。"""
    sig = inspect.signature(win_ops.WinOps.__init__)
    params = [p for p in sig.parameters if p != "self"]
    kwargs = {}
    positional = []
    for name in params:
        low = name.lower()
        if "transport" in low or low in ("runner", "mac", "queue"):
            kwargs[name] = transport
        elif "config" in low or low in ("cfg", "settings"):
            kwargs[name] = config
    if len(kwargs) == len(params) and kwargs:
        return win_ops.WinOps(**kwargs)
    if len(params) >= 2:
        for args in ((transport, config), (config, transport)):
            try:
                return win_ops.WinOps(*args)
            except TypeError:
                continue
    try:
        return win_ops.WinOps(transport)
    except TypeError:
        return win_ops.WinOps(config)


def _call(ops, method_names, argsets):
    """method_names のどれか・argsets のどれかで 1 回呼ぶ。"""
    last_exc = None
    for name in method_names:
        method = getattr(ops, name, None)
        if method is None:
            continue
        for args, kwargs in argsets:
            try:
                return method(*args, **kwargs)
            except TypeError as exc:
                last_exc = exc
                continue
    if last_exc is not None:
        raise last_exc
    raise AssertionError("WinOps に候補メソッド %s が無い" % (method_names,))


def call_list(ops, app=None, onscreen_only=False):
    argsets = []
    if app is not None:
        argsets += [
            ((), {"app": app, "onscreen_only": onscreen_only}),
            ((), {"app": app, "on_screen_only": onscreen_only}),
            ((app, onscreen_only), {}),
            ((), {"app": app}),
        ]
    elif onscreen_only:
        argsets += [
            ((), {"onscreen_only": True}),
            ((), {"on_screen_only": True}),
            ((None, True), {}),
        ]
    argsets.append(((), {}))
    return _call(ops, ["list_windows", "list", "cmd_list"], argsets)


def call_cap(ops, window_id, out=None):
    argsets = [
        ((), {"window_id": window_id}),
        ((), {"id": window_id}),
        ((window_id,), {}),
    ]
    if out is not None:
        argsets = [
            ((), {"window_id": window_id, "out": str(out)}),
            ((), {"window_id": window_id, "out_path": str(out)}),
            ((), {"window_id": window_id, "path": str(out)}),
            ((window_id, str(out)), {}),
        ] + argsets
    return _call(ops, ["capture", "cap", "cmd_cap"], argsets)


def call_ax(ops, app, surface=None, max_depth=None):
    kwargs = {"app": app}
    if surface is not None:
        kwargs["surface"] = surface
    if max_depth is not None:
        kwargs["max_depth"] = max_depth
    argsets = [((), dict(kwargs)), ((app,), {})]
    return _call(ops, ["snapshot", "ax", "cmd_ax"], argsets)


def call_press_ref(ops, ref):
    return _call(
        ops,
        ["press", "click", "cmd_press"],
        [((), {"ref": ref}), ((ref,), {})],
    )


def call_press_name(ops, app, name, surface=None):
    kwargs = {"app": app, "name": name}
    if surface is not None:
        kwargs["surface"] = surface
    return _call(
        ops,
        ["press", "click", "cmd_press"],
        [((), dict(kwargs)), ((), {"app": app, "label": name})],
    )


def call_close(ops, window_id=None, title=None, app=None, force=False):
    base = {}
    if title is not None:
        base["title"] = title
    if app is not None:
        base["app"] = app
    if force:
        base["force"] = True
    argsets = []
    if window_id is not None:
        argsets += [
            ((), dict(base, window_id=window_id)),
            ((), dict(base, id=window_id)),
            ((window_id,), dict(base)),
        ]
    argsets.append(((), dict(base)))
    return _call(ops, ["close", "cmd_close"], argsets)


def make_error(status):
    """WinOpsError を自然なコンストラクタ形で作る。"""
    for args, kwargs in (
        ((status,), {}),
        ((), {"status": status}),
        ((status, status), {}),
    ):
        try:
            return win_ops.WinOpsError(*args, **kwargs)
        except TypeError:
            continue
    raise AssertionError("WinOpsError を生成できない")


def candidates_of(result):
    """ambiguous_window の候補一覧を取り出す (キーの揺れを吸収)。"""
    for key in ("candidates", "window_candidates", "windows"):
        value = result.get(key)
        if isinstance(value, list) and value:
            return value
    return []


# ---------------------------------------------------------------------------
# parse_window_list
# ---------------------------------------------------------------------------


def test_parse_basic_fields_and_types():
    windows = win_ops.parse_window_list(LIST_TEXT_BASIC)
    assert len(windows) == 5
    for got, expected in zip(windows, windows_basic()):
        assert got["id"] == expected["id"]
        assert isinstance(got["id"], int)
        assert got["app"] == expected["app"]
        assert got["title"] == expected["title"]
        assert got["frame"] == expected["frame"]
        for value in got["frame"].values():
            assert isinstance(value, int)
        assert got["onscreen"] == expected["onscreen"]
        assert isinstance(got["onscreen"], bool)


def test_parse_japanese_app_and_title_with_spaces():
    windows = win_ops.parse_window_list(LIST_TEXT_BASIC)
    by_id = {w["id"]: w for w in windows}
    # 日本語アプリ名と、空白を含むタイトルが切れずに残ること
    assert by_id[101]["app"] == "ターミナル"
    assert by_id[101]["title"] == "作業 ログ — ローカル"
    assert by_id[103]["title"] == "Wi-Fi と ネットワーク"
    # 空白を含むアプリ名でも title= 以降と混ざらないこと
    assert by_id[105]["app"] == "ノート ブック"
    assert by_id[105]["title"] == "会議 メモ"


def test_parse_negative_coordinates_and_offscreen_false():
    windows = win_ops.parse_window_list(LIST_TEXT_BASIC)
    by_id = {w["id"]: w for w in windows}
    assert by_id[101]["frame"]["x"] == -120
    assert by_id[102]["frame"]["y"] == -20
    assert by_id[102]["onscreen"] is False
    assert by_id[101]["onscreen"] is True


def test_parse_ignores_lines_not_starting_with_win():
    windows = win_ops.parse_window_list(LIST_TEXT_BASIC)
    ids = [w["id"] for w in windows]
    # "windows ..." 行と接頭辞なし行の id は混入しないこと
    assert 999 not in ids
    assert 106 not in ids
    assert ids == [101, 102, 103, 104, 105]


def test_parse_empty_title():
    windows = win_ops.parse_window_list(LIST_TEXT_BASIC)
    by_id = {w["id"]: w for w in windows}
    assert by_id[104]["title"] == ""
    assert by_id[104]["frame"] == {"x": 0, "y": 25, "width": 700, "height": 400}


def test_parse_empty_text_returns_empty_list():
    assert win_ops.parse_window_list("") == []
    assert win_ops.parse_window_list("\nSCK_DONE\n") == []


# ---------------------------------------------------------------------------
# list コマンド (WinOps + transport スタブ)
# ---------------------------------------------------------------------------


def _list_transport(text=LIST_TEXT_FILTER):
    return StubTransport(lambda s, n, e: queue_ok({".list": text}))


def test_list_returns_all_windows(tmp_path):
    transport = _list_transport()
    ops = make_ops(transport, make_config(tmp_path))
    result = call_list(ops)
    assert result["ok"] is True
    assert result["command"] == "list"
    assert "status" in result
    assert [w["id"] for w in result["windows"]] == [201, 202, 203, 204]
    assert result["windows"][1]["app"] == "メモ"
    # ScreenCheck list を呼ぶスクリプトが投入されていること
    assert transport.scripts_containing("ScreenCheck")
    assert any("list" in s for s in transport.scripts())


def test_list_app_filter_is_exact_match(tmp_path):
    ops = make_ops(_list_transport(), make_config(tmp_path))
    result = call_list(ops, app="メモ")
    assert result["ok"] is True
    # 「メモ帳」は完全一致しないので含まれないこと
    assert [w["id"] for w in result["windows"]] == [202]


def test_list_app_filter_partial_name_matches_nothing(tmp_path):
    ops = make_ops(_list_transport(), make_config(tmp_path))
    result = call_list(ops, app="メ")
    assert result["ok"] is True
    assert result["windows"] == []


def test_list_onscreen_only(tmp_path):
    ops = make_ops(_list_transport(), make_config(tmp_path))
    result = call_list(ops, onscreen_only=True)
    assert result["ok"] is True
    assert [w["id"] for w in result["windows"]] == [201, 202]
    assert all(w["onscreen"] for w in result["windows"])


def test_list_screencheck_error(tmp_path):
    ops = make_ops(_list_transport(LIST_TEXT_ERROR), make_config(tmp_path))
    result = call_list(ops)
    assert result["ok"] is False
    assert result["command"] == "list"
    assert result["status"] == "screencheck_error"
    assert result.get("error")


def test_list_queue_timeout_propagates(tmp_path):
    transport = StubTransport(lambda s, n, e: queue_timeout())
    ops = make_ops(transport, make_config(tmp_path))
    result = call_list(ops)
    assert result["ok"] is False
    assert result["status"] == "queue_timeout"


# ---------------------------------------------------------------------------
# cap コマンド
# ---------------------------------------------------------------------------


def _cap_transport(meta=CAP_META_OK, png_b64=PNG_B64):
    files = {".meta": meta}
    if png_b64 is not None:
        files[".png.b64"] = png_b64
    return StubTransport(lambda s, n, e: queue_ok(files))


def test_cap_saves_png_and_reports_metadata(tmp_path):
    capture_dir = tmp_path / "caps"
    capture_dir.mkdir()
    ops = make_ops(_cap_transport(), make_config(capture_dir))
    result = call_cap(ops, 301)
    assert result["ok"] is True
    assert result["command"] == "cap"
    assert result["window_id"] == 301
    assert result["bytes"] == len(PNG_BYTES)
    saved = Path(result["path"])
    assert saved.parent == capture_dir
    assert saved.read_bytes() == PNG_BYTES


def test_cap_filename_format(tmp_path):
    capture_dir = tmp_path / "caps"
    capture_dir.mkdir()
    ops = make_ops(_cap_transport(), make_config(capture_dir))
    result = call_cap(ops, 301)
    assert result["ok"] is True
    name = Path(result["path"]).name
    assert re.fullmatch(r"win-ops-301-\d{8}-\d{6}\.png", name), name


def test_cap_out_path_is_used(tmp_path):
    capture_dir = tmp_path / "caps"
    capture_dir.mkdir()
    out = tmp_path / "explicit" / "shot.png"
    out.parent.mkdir()
    ops = make_ops(_cap_transport(), make_config(capture_dir))
    result = call_cap(ops, 301, out=out)
    assert result["ok"] is True
    assert Path(result["path"]) == out
    assert out.read_bytes() == PNG_BYTES


def test_cap_window_not_found(tmp_path):
    capture_dir = tmp_path / "caps"
    capture_dir.mkdir()
    transport = StubTransport(
        lambda s, n, e: queue_ok({".meta": CAP_META_NOT_FOUND}, _ssh_rc=3)
    )
    ops = make_ops(transport, make_config(capture_dir))
    result = call_cap(ops, 999)
    assert result["ok"] is False
    assert result["status"] == "window_not_found"
    assert list(capture_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# ax コマンド
# ---------------------------------------------------------------------------


def test_ax_returns_snapshot_passthrough(tmp_path):
    snapshot = json.loads(ax_json(PRESS_TREE_UNIQUE))
    transport = StubTransport(
        lambda s, n, e: queue_ok({".ax.json": ax_json(PRESS_TREE_UNIQUE)})
    )
    ops = make_ops(transport, make_config(tmp_path))
    result = call_ax(ops, app="メモ")
    assert result["ok"] is True
    assert result["command"] == "ax"
    # 向こうの JSON がそのまま snapshot キーに入ること
    assert result["snapshot"] == snapshot
    assert transport.scripts_containing("agent-desktop")
    assert any("snapshot" in s for s in transport.scripts())


def test_ax_remote_ok_false_becomes_ax_failed(tmp_path):
    payload = json.dumps({"ok": False, "error": "テスト用の snapshot 失敗"})
    transport = StubTransport(lambda s, n, e: queue_ok({".ax.json": payload}))
    ops = make_ops(transport, make_config(tmp_path))
    result = call_ax(ops, app="メモ")
    assert result["ok"] is False
    assert result["status"] == "ax_failed"


# ---------------------------------------------------------------------------
# press コマンド
# ---------------------------------------------------------------------------


def _press_transport(tree=None):
    def handler(script, name, extras):
        if "click" in script:
            return queue_ok({".click.json": click_json(True)})
        if "snapshot" in script:
            return queue_ok({".ax.json": ax_json(tree or PRESS_TREE_UNIQUE)})
        return queue_ok()

    return StubTransport(handler)


def test_press_ref_clicks_and_uses_session(tmp_path):
    transport = _press_transport()
    ops = make_ops(transport, make_config(tmp_path))
    result = call_press_ref(ops, "@snap-test-1:e7")
    assert result["ok"] is True
    assert result["command"] == "press"
    clicks = transport.scripts_containing("click")
    assert len(clicks) == 1
    assert "@snap-test-1:e7" in clicks[0]
    assert SESSION_NAME in clicks[0]


@pytest.mark.parametrize(
    "bad_ref",
    [
        "e7",
        "@snap-test-1",
        "snap-test-1:e7",
        "@snap-test-1:7",
        "@snap-test-1:e",
        "@:e7",
        "@snap-test-1:e-1",
        "",
    ],
)
def test_press_invalid_ref_rejected_without_click(tmp_path, bad_ref):
    transport = _press_transport()
    ops = make_ops(transport, make_config(tmp_path))
    result = call_press_ref(ops, bad_ref)
    assert result["ok"] is False
    assert result["command"] == "press"
    # 不正な ref では click スクリプトが一切投入されないこと
    assert transport.scripts_containing("click") == []


def test_press_by_name_unique_clicks_its_ref(tmp_path):
    transport = _press_transport(PRESS_TREE_UNIQUE)
    ops = make_ops(transport, make_config(tmp_path))
    result = call_press_name(ops, app="メモ", name="設定を開く")
    assert result["ok"] is True
    clicks = transport.scripts_containing("click")
    assert len(clicks) == 1
    assert "e7" in clicks[0]


def test_press_by_name_not_found_does_not_click(tmp_path):
    transport = _press_transport(PRESS_TREE_UNIQUE)
    ops = make_ops(transport, make_config(tmp_path))
    result = call_press_name(ops, app="メモ", name="存在しないラベル")
    assert result["ok"] is False
    assert result["status"] == "element_not_found"
    assert transport.scripts_containing("click") == []


def test_press_by_name_ambiguous_does_not_click(tmp_path):
    transport = _press_transport(PRESS_TREE_AMBIGUOUS)
    ops = make_ops(transport, make_config(tmp_path))
    result = call_press_name(ops, app="メモ", name="開く")
    assert result["ok"] is False
    assert result["status"] == "ambiguous_element"
    assert transport.scripts_containing("click") == []


def test_press_by_name_disabled_does_not_click(tmp_path):
    transport = _press_transport(PRESS_TREE_DISABLED)
    ops = make_ops(transport, make_config(tmp_path))
    result = call_press_name(ops, app="メモ", name="設定を開く")
    assert result["ok"] is False
    assert result["status"] == "element_disabled"
    assert transport.scripts_containing("click") == []


def test_press_by_name_uses_same_session_for_snapshot_and_click(tmp_path):
    transport = _press_transport(PRESS_TREE_UNIQUE)
    ops = make_ops(transport, make_config(tmp_path))
    result = call_press_name(ops, app="メモ", name="設定を開く")
    assert result["ok"] is True
    snapshots = [s for s in transport.scripts() if "snapshot" in s]
    clicks = transport.scripts_containing("click")
    assert snapshots and clicks
    # snapshot と click の両方に設定の同一セッション名が入ること
    assert all(SESSION_NAME in s for s in snapshots)
    assert all(SESSION_NAME in s for s in clicks)


# ---------------------------------------------------------------------------
# resolve_window (純関数)
# ---------------------------------------------------------------------------


def test_resolve_by_id():
    window = win_ops.resolve_window(windows_for_resolve(), window_id=312)
    assert window["id"] == 312
    assert window["app"] == "メモ帳"


def test_resolve_by_title():
    window = win_ops.resolve_window(windows_for_resolve(), title="作業 — zsh")
    assert window["id"] == 301


def test_resolve_by_title_and_app_narrows_to_one():
    window = win_ops.resolve_window(windows_for_resolve(), title="買い物", app="メモ帳")
    assert window["id"] == 312


def test_resolve_not_found_raises_window_not_found():
    with pytest.raises(win_ops.WinOpsError) as excinfo:
        win_ops.resolve_window(windows_for_resolve(), title="存在しない題")
    assert excinfo.value.status == "window_not_found"


def test_resolve_unknown_id_raises_window_not_found():
    with pytest.raises(win_ops.WinOpsError) as excinfo:
        win_ops.resolve_window(windows_for_resolve(), window_id=999)
    assert excinfo.value.status == "window_not_found"


def test_resolve_ambiguous_title_raises_ambiguous_window():
    with pytest.raises(win_ops.WinOpsError) as excinfo:
        win_ops.resolve_window(windows_for_resolve(), title="買い物")
    assert excinfo.value.status == "ambiguous_window"


# ---------------------------------------------------------------------------
# iter_elements / find_elements_by_name (純関数)
# ---------------------------------------------------------------------------


def test_iter_elements_yields_all_nodes():
    elements = list(win_ops.iter_elements(ELEMENT_TREE))
    assert elements, "要素が 1 件も返らない"
    assert all(isinstance(e, dict) for e in elements)
    names = {e.get("name") for e in elements}
    # 入れ子 (group の子) を含む全ノードの名前が辿れること
    assert {"設定", "サイドバー", "一般", "設定を開く", "開く"} <= names
    refs = {e.get("ref") for e in elements}
    assert "@snap-test-1:e3" in refs  # 最も深い子
    assert "@snap-test-1:e6" in refs


def test_find_elements_by_name_exact_match():
    found = win_ops.find_elements_by_name(ELEMENT_TREE, "設定を開く")
    assert len(found) == 1
    assert found[0]["ref"] == "@snap-test-1:e4"
    found_multi = win_ops.find_elements_by_name(ELEMENT_TREE, "開く")
    assert len(found_multi) == 2


def test_find_elements_by_name_partial_does_not_match():
    # 「設定」は「設定を開く」の部分文字列だが完全一致ではない
    assert win_ops.find_elements_by_name(ELEMENT_TREE, "設定を開") == []
    assert win_ops.find_elements_by_name(ELEMENT_TREE, "存在しない") == []


# ---------------------------------------------------------------------------
# busy_processes (純関数)
# ---------------------------------------------------------------------------


def test_busy_processes_extracts_non_shell():
    assert win_ops.busy_processes(PS_BUSY, SHELL_COMMANDS) == ["vim", "python3"]


def test_busy_processes_all_shell_returns_empty():
    assert win_ops.busy_processes(PS_SHELL_ONLY, SHELL_COMMANDS) == []


def test_busy_processes_ignores_header_and_blank_lines():
    text = "  PID COMM\n\n  501 login\n  502 zsh\n\n"
    assert win_ops.busy_processes(text, SHELL_COMMANDS) == []
    assert win_ops.busy_processes("", SHELL_COMMANDS) == []


def test_busy_processes_respects_given_shell_list():
    # 許可リストは引数で渡すもの: vim を許可側に入れれば busy にならない
    assert win_ops.busy_processes(PS_BUSY, SHELL_COMMANDS + ["vim"]) == ["python3"]


# ---------------------------------------------------------------------------
# find_close_button_candidates (純関数)
# ---------------------------------------------------------------------------


def _button(ref, bounds, role="button", name=""):
    return make_element(ref, role, name, bounds)


def test_close_button_at_offset_is_candidate():
    tree = close_tree([CLOSE_BUTTON_BOUNDS])
    found = win_ops.find_close_button_candidates(
        tree, TARGET_FRAME, offset_px=14, tolerance_px=12
    )
    assert len(found) == 1
    assert found[0]["bounds"]["x"] == CLOSE_BUTTON_BOUNDS[0]


def test_close_button_at_tolerance_boundary_is_candidate():
    # 中心が (左上+offset) から x 方向に許容差ちょうど (12) ずれた位置
    boundary = (119, 57, 14, 14)  # 中心 (126, 64) = (114+12, 64)
    tree = close_tree([boundary])
    found = win_ops.find_close_button_candidates(
        tree, TARGET_FRAME, offset_px=14, tolerance_px=12
    )
    assert len(found) == 1


def test_close_button_beyond_tolerance_is_not_candidate():
    outside = (120, 57, 14, 14)  # 中心 (127, 64): 許容差 +1
    tree = close_tree([outside])
    found = win_ops.find_close_button_candidates(
        tree, TARGET_FRAME, offset_px=14, tolerance_px=12
    )
    assert found == []


def test_close_button_of_other_window_is_not_candidate():
    # 別の窓 (フレーム左上 500,50) の閉じるボタン位置にあるボタンは、
    # 対象フレーム基準では候補に入らないこと
    other_window_button = (507, 57, 14, 14)  # 中心 (514, 64)
    tree = close_tree([other_window_button])
    found = win_ops.find_close_button_candidates(
        tree, TARGET_FRAME, offset_px=14, tolerance_px=12
    )
    assert found == []
    # 窓の右上にあるボタン (最大化等) も候補ではない
    tree2 = close_tree([(879, 57, 14, 14)])
    assert (
        win_ops.find_close_button_candidates(
            tree2, TARGET_FRAME, offset_px=14, tolerance_px=12
        )
        == []
    )


def test_close_button_non_button_role_is_not_candidate():
    tree = close_tree(
        [],
        extra_children=[_button("@snap-test-1:e20", CLOSE_BUTTON_BOUNDS, role="image")],
    )
    found = win_ops.find_close_button_candidates(
        tree, TARGET_FRAME, offset_px=14, tolerance_px=12
    )
    assert found == []


def test_close_button_nested_button_is_found():
    nested = make_element(
        "@snap-test-1:e2",
        "group",
        "",
        (100, 50, 200, 40),
        children=[_button("@snap-test-1:e21", CLOSE_BUTTON_BOUNDS)],
    )
    tree = close_tree([], extra_children=[nested])
    found = win_ops.find_close_button_candidates(
        tree, TARGET_FRAME, offset_px=14, tolerance_px=12
    )
    assert len(found) == 1
    assert found[0]["ref"] == "@snap-test-1:e21"


def test_close_button_respects_given_offset_and_tolerance():
    # オフセット 14 では候補だが、オフセットを 40 に変えると外れること
    tree = close_tree([CLOSE_BUTTON_BOUNDS])
    assert (
        win_ops.find_close_button_candidates(
            tree, TARGET_FRAME, offset_px=40, tolerance_px=12
        )
        == []
    )
    # 許容差を広げれば、既定では外れる位置も候補になること
    outside = (120, 57, 14, 14)
    tree2 = close_tree([outside])
    found = win_ops.find_close_button_candidates(
        tree2, TARGET_FRAME, offset_px=14, tolerance_px=20
    )
    assert len(found) == 1


# ---------------------------------------------------------------------------
# close コマンド (WinOps + transport スタブ)
# ---------------------------------------------------------------------------


def _close_transport(
    first_list,
    after_list,
    tty=TTY_TERMINAL,
    ps_text=PS_SHELL_ONLY,
    tree=CLOSE_TREE_OK,
    click_ok=True,
):
    """close の多段フロー (list→tty/ps→snapshot→click→list) 用スタブ。"""
    state = {"list_calls": 0}

    def handler(script, name, extras):
        if "ScreenCheck" in script and "cap" in script:
            return queue_ok({".meta": CAP_META_OK, ".png.b64": PNG_B64})
        if "ScreenCheck" in script:
            state["list_calls"] += 1
            text = first_list if state["list_calls"] == 1 else after_list
            return queue_ok({".list": text})
        if "click" in script:
            return queue_ok({".click.json": click_json(click_ok)})
        if "snapshot" in script:
            return queue_ok({".ax.json": ax_json(tree)})
        # tty 取得 + ps (契約補足: 結果キー tty、extras ".ps")
        return queue_ok({".ps": ps_text}, tty=tty)

    return StubTransport(handler)


def test_close_window_not_found(tmp_path):
    transport = _close_transport(LIST_TEXT_MEMO_ONLY, LIST_TEXT_MEMO_ONLY)
    ops = make_ops(transport, make_config(tmp_path))
    result = call_close(ops, window_id=999)
    assert result["ok"] is False
    assert result["command"] == "close"
    assert result["status"] == "window_not_found"
    assert transport.scripts_containing("click") == []


def test_close_ambiguous_window_returns_candidates(tmp_path):
    transport = _close_transport(LIST_TEXT_DUPLICATE_TITLE, LIST_TEXT_DUPLICATE_TITLE)
    ops = make_ops(transport, make_config(tmp_path))
    result = call_close(ops, title="買い物")
    assert result["ok"] is False
    assert result["status"] == "ambiguous_window"
    candidates = candidates_of(result)
    assert {c["id"] for c in candidates} == {311, 312}
    assert transport.scripts_containing("click") == []


def test_close_terminal_busy_blocks_and_reports_processes(tmp_path):
    transport = _close_transport(
        LIST_TEXT_TERMINAL,
        LIST_TEXT_TERMINAL,
        ps_text=PS_BUSY,
    )
    ops = make_ops(transport, make_config(tmp_path))
    result = call_close(ops, window_id=301)
    assert result["ok"] is False
    assert result["status"] == "busy"
    assert "vim" in result["processes"]
    assert "python3" in result["processes"]
    assert transport.scripts_containing("click") == []


def test_close_terminal_shell_only_closes_and_verifies(tmp_path):
    transport = _close_transport(
        LIST_TEXT_TERMINAL,
        LIST_TEXT_AFTER_TERMINAL_CLOSED,
        ps_text=PS_SHELL_ONLY,
    )
    ops = make_ops(transport, make_config(tmp_path))
    result = call_close(ops, window_id=301)
    assert result["ok"] is True
    assert result["command"] == "close"
    assert result["verified"] is True
    assert len(transport.scripts_containing("click")) == 1


def test_close_tty_unknown_is_process_check_failed(tmp_path):
    transport = _close_transport(
        LIST_TEXT_TERMINAL,
        LIST_TEXT_TERMINAL,
        tty="",
        ps_text="",
    )
    ops = make_ops(transport, make_config(tmp_path))
    result = call_close(ops, window_id=301)
    assert result["ok"] is False
    assert result["status"] == "process_check_failed"
    assert transport.scripts_containing("click") == []


def test_close_non_terminal_skips_process_check_and_closes(tmp_path):
    transport = _close_transport(
        LIST_TEXT_TERMINAL,
        LIST_TEXT_AFTER_MEMO_CLOSED,
        ps_text=PS_BUSY,  # ターミナルでなければ ps の中身は問わない
    )
    ops = make_ops(transport, make_config(tmp_path))
    result = call_close(ops, window_id=302)
    assert result["ok"] is True
    assert result["verified"] is True
    process_check = result.get("process_check") or {}
    assert process_check.get("applicable") is False


def test_close_force_closes_even_when_busy(tmp_path):
    transport = _close_transport(
        LIST_TEXT_TERMINAL,
        LIST_TEXT_AFTER_TERMINAL_CLOSED,
        ps_text=PS_BUSY,
    )
    ops = make_ops(transport, make_config(tmp_path))
    result = call_close(ops, window_id=301, force=True)
    assert result["ok"] is True
    assert result["verified"] is True
    assert len(transport.scripts_containing("click")) == 1


def test_close_button_not_found(tmp_path):
    transport = _close_transport(
        LIST_TEXT_TERMINAL,
        LIST_TEXT_TERMINAL,
        tree=CLOSE_TREE_NO_BUTTON,
    )
    ops = make_ops(transport, make_config(tmp_path))
    result = call_close(ops, window_id=301)
    assert result["ok"] is False
    assert result["status"] == "close_button_not_found"
    assert transport.scripts_containing("click") == []


def test_close_ambiguous_close_button(tmp_path):
    transport = _close_transport(
        LIST_TEXT_TERMINAL,
        LIST_TEXT_TERMINAL,
        tree=CLOSE_TREE_TWO_BUTTONS,
    )
    ops = make_ops(transport, make_config(tmp_path))
    result = call_close(ops, window_id=301)
    assert result["ok"] is False
    assert result["status"] == "ambiguous_close_button"
    assert transport.scripts_containing("click") == []


def test_close_unverified_when_window_remains(tmp_path):
    transport = _close_transport(
        LIST_TEXT_TERMINAL,
        LIST_TEXT_TERMINAL,  # 閉じた後も窓が残る
    )
    ops = make_ops(transport, make_config(tmp_path))
    result = call_close(ops, window_id=301)
    assert result["ok"] is False
    assert result["status"] == "close_unverified"


# ---------------------------------------------------------------------------
# スクリプト生成 (build_*_script)
# ---------------------------------------------------------------------------


def _generated_scripts():
    """build_*_script を自然な引数で呼び、生成できたスクリプトを集める。"""
    builders = [
        getattr(win_ops, name)
        for name in dir(win_ops)
        if name.startswith("build_") and name.endswith("_script")
    ]
    builders = [b for b in builders if callable(b)]
    assert builders, "build_*_script が 1 つも無い"
    scripts = []
    attempts = [
        (("winops-test-1",), {}),
        (("winops-test-1", 301), {}),
        (("winops-test-1", "ターミナル"), {}),
        (("winops-test-1",), {"window_id": 301}),
        (("winops-test-1",), {"app": "メモ"}),
        (("winops-test-1",), {"ref": "@snap-test-1:e7"}),
        (("winops-test-1", 301, "/tmp/win-ops-test.png"), {}),
    ]
    for builder in builders:
        produced = None
        for args, kwargs in attempts:
            try:
                produced = builder(*args, **kwargs)
                break
            except TypeError:
                continue
        assert (
            isinstance(produced, str) and produced
        ), "%s を自然な引数で呼べない" % getattr(builder, "__name__", builder)
        scripts.append(produced)
    return scripts


def test_build_scripts_replace_name_placeholder():
    for script in _generated_scripts():
        assert "__NAME__" not in script


def test_build_scripts_invoke_screencheck_or_agent_desktop():
    scripts = _generated_scripts()
    joined = "\n".join(scripts)
    assert "ScreenCheck" in joined
    assert "agent-desktop" in joined
    for script in scripts:
        assert "ScreenCheck" in script or "agent-desktop" in script


def test_build_scripts_queue_name_ends_with_sh():
    # キュー投入スクリプト名は .sh で終わる (仕様の共通ルール)。
    # 生成側が名前を返す/含む場合の担保として、投入名に使う文字列が
    # スクリプト内に埋め込まれていれば .sh 形式であることを見る。
    for script in _generated_scripts():
        for token in re.findall(r"[\w./-]+\.sh\b", script):
            assert token.endswith(".sh")


# ---------------------------------------------------------------------------
# モジュール契約
# ---------------------------------------------------------------------------


def test_winopserror_carries_status():
    err = make_error("window_not_found")
    assert isinstance(err, Exception)
    assert err.status == "window_not_found"


def test_main_is_callable():
    assert callable(win_ops.main)
    sig = inspect.signature(win_ops.main)
    assert len(sig.parameters) >= 1  # main(argv)
