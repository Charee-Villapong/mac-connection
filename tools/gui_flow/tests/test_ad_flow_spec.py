"""仕様テスト: ad_flow (agent-desktop 版 Discord 送信フロー).

実装コードを読まず、DEFINITION.md (完了の定義) だけから書いた受け入れ
テスト。Mac / agent-desktop 層は AdClient 境界のフェイクで置き換え、
ツリー fixture は実測した Discord のツリー形状 (link の DM 行 /
heading の見出し / textfield の入力欄 / list 配下の statictext 本文)
を模す。人名はすべて架空 (テスト花子 / テスト太郎)。

検証する仕様:
  1. 承認が無ければ Mac に一切触れず approval_missing で停止する。
  2. DM 行は「<alias> (ダイレクトメッセージ)」で始まる link の
     完全一致で特定する。0 件なら (ホーム手掛かりがある場合のみ)
     ホームへ 1 回だけ遷移して再探索する。2 件以上は ambiguous。
  3. 行クリック後の見出し (heading) が alias と完全一致しなければ
     recipient_mismatch で、入力には絶対に進まない。
  4. 入力欄に残りがあれば anomaly_leftover で停止 (消去もしない)。
  5. 入力後の読み戻し (value) が期待本文と完全一致しなければ
     欄を消して readback_mismatch で停止し、送信しない。
  6. 送信は Enter 1 回だけ。送信後に (a) 欄が空 (b) メッセージ一覧の
     最後の本文が期待本文 (``` を除いた中身) と完全一致
     (c) 見出しが宛先のまま、の 3 点がそろって初めて成功とし、
     承認を消費する。欠ければ post_send_unverified で、承認は
     消費しない。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ad_flow  # noqa: E402

ALIAS = "テスト花子"
OTHER = "テスト太郎"
RECIPIENT_ID = "test-hanako"

TEXT = (
    "```\n"
    "[SYSTEM] AIエージェントからお知らせ\n"
    "大声を一定期間検知しませんでした。\n"
    "大声チェッカーの作動を停止します。\n"
    "```"
)
BODY = (
    "[SYSTEM] AIエージェントからお知らせ\n"
    "大声を一定期間検知しませんでした。\n"
    "大声チェッカーの作動を停止します。"
)
OLD_BODY = "[SYSTEM] AIエージェントからお知らせ\n前のメッセージです。"


# ---------------------------------------------------------------------------
# fixture: 実測した Discord ツリーの最小再現
# ---------------------------------------------------------------------------


def _node(role, name=None, ref=None, value=None, children=()):
    n = {"role": role, "children": list(children)}
    if name is not None:
        n["name"] = name
    if ref is not None:
        n["ref_id"] = ref
    if value is not None:
        n["value"] = value
    return n


def make_tree(
    dm_rows=(ALIAS,),
    header=None,
    input_value="",
    bodies=(OLD_BODY,),
    home=False,
    input_present=True,
):
    """snapshot envelope の data 部分を模す。

    dm_rows: DM 一覧に出す表示名 (空 = サーバー画面想定)。
    header: 開いているトークの見出し (None = 何も開いていない)。
    input_value: 入力欄の value。input_present=False で欄自体を消す。
    bodies: メッセージ一覧の本文 (statictext) の並び。
    home: ホーム (DM へ戻る) リンクを置く。
    """
    kids = []
    if home:
        kids.append(_node("link", "ホーム", ref="@snap:e1"))
    for i, name in enumerate(dm_rows):
        kids.append(
            _node(
                "link",
                "%s (ダイレクトメッセージ), オンライン" % name,
                ref="@snap:e%d" % (10 + i),
            )
        )
    if header is not None:
        kids.append(_node("heading", header))
        msgs = [_node("article", children=[_node("statictext", b)]) for b in bodies]
        kids.append(_node("list", "%sのメッセージ" % header, children=msgs))
        if input_present:
            kids.append(
                _node(
                    "textfield",
                    "@%s へメッセージを送信" % header,
                    ref="@snap:e233",
                    value=input_value,
                )
            )
    tree = _node("window", children=[_node("group", children=kids)])
    return {"snapshot_id": "snap", "complete": True, "tree": tree}


class FakeClient:
    """snapshot の列を順に返し、操作を記録するフェイク。"""

    def __init__(self, snapshots):
        self._queue = list(snapshots)
        self.clicks = []
        self.set_values = []
        self.clears = []
        self.presses = []
        self.snapshot_calls = 0

    def snapshot(self):
        self.snapshot_calls += 1
        if self._queue:
            return self._queue.pop(0)
        return None

    def click(self, ref):
        self.clicks.append(ref)
        return True

    def set_value(self, ref, text):
        self.set_values.append((ref, text))
        return True

    def clear(self, ref):
        self.clears.append(ref)
        return True

    def press_enter(self, ref):
        self.presses.append(ref)
        return True

    def screenshot(self):
        return None


def _make_cfg():
    app = {
        "app_name": "Discord",
        "window_app": "Discord",
        "process": "Discord",
        "default_recipient": RECIPIENT_ID,
        "recipients": {
            RECIPIENT_ID: {"aliases": [ALIAS], "nav_click": [170, 350]},
        },
        "recipient_aliases": [ALIAS],
        "talk_list_width_ratio": 0.27,
        "ad_home_names": ["ホーム"],
    }
    return {"apps": {"discord": app}}


class _Approval:
    """gui_flow の承認 API (check は (ok, reason, record) を返す) の模倣。"""

    def __init__(self, valid=True):
        self.valid = valid
        self.consumed = []

    def check(self, app, acfg, text, now=None):
        if self.valid:
            return True, "ok", {"id": "fake-approval"}
        return False, "missing", None

    def consume(self, app, acfg, text, now=None):
        self.consumed.append(text)
        return True


def _make_runner(monkeypatch, snapshots, approval_valid=True):
    client = FakeClient(snapshots)
    approval = _Approval(approval_valid)
    monkeypatch.setattr(ad_flow, "check_approval", approval.check)
    monkeypatch.setattr(ad_flow, "consume_approval", approval.consume)
    runner = ad_flow.AdRunner(_make_cfg(), client, to=RECIPIENT_ID)
    return runner, client, approval


def _success_snapshots(field_after_set=None, bodies_after=(OLD_BODY, BODY)):
    """正常系の snapshot 列: 一覧 → 開いた直後 → 入力後 → 送信後。"""
    return [
        make_tree(dm_rows=(ALIAS, OTHER)),
        make_tree(dm_rows=(ALIAS, OTHER), header=ALIAS, input_value=""),
        make_tree(
            dm_rows=(ALIAS, OTHER),
            header=ALIAS,
            input_value=TEXT if field_after_set is None else field_after_set,
        ),
        make_tree(
            dm_rows=(ALIAS, OTHER),
            header=ALIAS,
            input_value="",
            bodies=bodies_after,
        ),
    ]


# ---------------------------------------------------------------------------
# 1. 正常系
# ---------------------------------------------------------------------------


def test_post_success_end_to_end(monkeypatch):
    runner, client, approval = _make_runner(monkeypatch, _success_snapshots())

    assert runner.post(TEXT) is True
    assert runner.error is None
    # 行クリックは宛先行の 1 回だけ
    assert client.clicks == ["@snap:e10"]
    # 入力は期待本文そのもの
    assert client.set_values == [("@snap:e233", TEXT)]
    # 送信は Enter 1 回だけ
    assert client.presses == ["@snap:e233"]
    # 承認は消費される
    assert approval.consumed == [TEXT]


# ---------------------------------------------------------------------------
# 2. 承認なし -> Mac に触れない
# ---------------------------------------------------------------------------


def test_post_without_approval_never_touches_client(monkeypatch):
    runner, client, approval = _make_runner(
        monkeypatch, _success_snapshots(), approval_valid=False
    )

    assert runner.post(TEXT) is False
    assert runner.error == "approval_missing"
    assert client.snapshot_calls == 0
    assert client.clicks == [] and client.set_values == []
    assert client.presses == []
    assert approval.consumed == []


# ---------------------------------------------------------------------------
# 3. 見出し不一致 -> 入力前に停止
# ---------------------------------------------------------------------------


def test_post_header_mismatch_stops_before_input(monkeypatch):
    snaps = [
        make_tree(dm_rows=(ALIAS, OTHER)),
        # 行をクリックしたのに開いたのは別人のトーク
        make_tree(dm_rows=(ALIAS, OTHER), header=OTHER, input_value=""),
    ]
    runner, client, approval = _make_runner(monkeypatch, snaps)

    assert runner.post(TEXT) is False
    assert runner.error == "recipient_mismatch"
    assert client.set_values == []
    assert client.presses == []
    assert approval.consumed == []


# ---------------------------------------------------------------------------
# 4. 入力欄に残り -> 停止 (消去も入力もしない)
# ---------------------------------------------------------------------------


def test_post_leftover_stops_without_clear_or_input(monkeypatch):
    snaps = [
        make_tree(dm_rows=(ALIAS,)),
        make_tree(dm_rows=(ALIAS,), header=ALIAS, input_value="下書きの残り"),
    ]
    runner, client, approval = _make_runner(monkeypatch, snaps)

    assert runner.post(TEXT) is False
    assert runner.error == "anomaly_leftover"
    assert client.set_values == []
    assert client.clears == []
    assert client.presses == []


# ---------------------------------------------------------------------------
# 5. 読み戻し不一致 -> 欄を消して停止、送信しない
# ---------------------------------------------------------------------------


def test_post_readback_mismatch_clears_and_never_sends(monkeypatch):
    collapsed = TEXT.replace("\n", " ")  # 改行が潰れた読み戻し
    runner, client, approval = _make_runner(
        monkeypatch, _success_snapshots(field_after_set=collapsed)
    )

    assert runner.post(TEXT) is False
    assert runner.error == "readback_mismatch"
    assert client.clears == ["@snap:e233"]
    assert client.presses == []
    assert approval.consumed == []


# ---------------------------------------------------------------------------
# 6. 送信後確認が取れない -> 成功と言わない・承認を消費しない
# ---------------------------------------------------------------------------


def test_post_unverified_when_message_not_in_tree(monkeypatch):
    snaps = _success_snapshots(bodies_after=(OLD_BODY,))  # 新着が無い
    runner, client, approval = _make_runner(monkeypatch, snaps)

    assert runner.post(TEXT) is False
    assert runner.error == "post_send_unverified"
    assert client.presses == ["@snap:e233"]  # 送信キー自体は送った
    assert approval.consumed == []


# ---------------------------------------------------------------------------
# 7. サーバー画面 -> ホーム遷移 1 回で救済
# ---------------------------------------------------------------------------


def test_post_home_retry_recovers_from_server_view(monkeypatch):
    snaps = [make_tree(dm_rows=(), home=True)] + _success_snapshots()
    runner, client, approval = _make_runner(monkeypatch, snaps)

    assert runner.post(TEXT) is True
    # 最初のクリックはホーム、次が宛先行
    assert client.clicks[0] == "@snap:e1"
    assert client.clicks[1] == "@snap:e10"
    names = [c["name"] for c in runner.checks]
    assert "nav_home" in names


# ---------------------------------------------------------------------------
# 8. ホーム遷移後も宛先なし -> 停止 (ホームは 1 回だけ)
# ---------------------------------------------------------------------------


def test_post_home_retry_only_once_then_not_found(monkeypatch):
    snaps = [
        make_tree(dm_rows=(), home=True),
        make_tree(dm_rows=(OTHER,), home=True),
    ]
    runner, client, approval = _make_runner(monkeypatch, snaps)

    assert runner.post(TEXT) is False
    assert runner.error == "nav_recipient_not_found"
    assert client.clicks == ["@snap:e1"]
    assert client.set_values == []


# ---------------------------------------------------------------------------
# 9. ホーム要素が「名前なし group + 中のラベル」形式 (実測の Discord 形状)
# ---------------------------------------------------------------------------


def test_post_home_retry_with_rail_group_label(monkeypatch):
    server = make_tree(dm_rows=(), home=False)
    # レールのホームボタン: name なし group、配下 statictext がラベル
    rail = _node(
        "group",
        ref="@snap:e10",
        children=[_node("statictext", "ホーム")],
    )
    rail["available_actions"] = ["Click", "SetFocus"]
    server["tree"]["children"][0]["children"].insert(0, rail)
    snaps = [server] + _success_snapshots()
    runner, client, approval = _make_runner(monkeypatch, snaps)

    assert runner.post(TEXT) is True
    # クリックはホーム (レール group) → 宛先行の 2 回
    assert client.clicks == ["@snap:e10", "@snap:e10"]
    assert client.presses == ["@snap:e233"]


# ---------------------------------------------------------------------------
# 10. 宛先行が複数 -> ambiguous で停止 (クリックしない)
# ---------------------------------------------------------------------------


def test_post_ambiguous_rows_stop_without_click(monkeypatch):
    snaps = [make_tree(dm_rows=(ALIAS, ALIAS))]
    runner, client, approval = _make_runner(monkeypatch, snaps)

    assert runner.post(TEXT) is False
    assert runner.error == "nav_recipient_ambiguous"
    assert client.clicks == []
    assert client.set_values == []
