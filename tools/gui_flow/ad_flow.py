#!/usr/bin/env python3
"""ad_flow — agent-desktop 版 Discord 送信フロー.

画面写真 + OCR + 座標クリックで Discord を操作する gui_flow に対し、
ad_flow は agent-desktop のアクセシビリティツリー (名前・値を文字
データで取得) で Discord を操作する。宛先確認・入力・読み戻し・
送信後確認のすべてを文字列の完全一致で判定する。

設計 (~/workspace/ad-flow/DEFINITION.md が正本):
  - gui_flow 本体は変更しない。設定読み込み・Mac キュー・承認ゲートは
    gui_flow から import して再利用する (承認記録は共通)。
  - agent-desktop の呼び出しは必ず GUI キュー経由 (Mac.run_queue)。
    SSH 直実行は TCC の帰属で拒否されるため。
  - 送信手順と合否は DEFINITION.md の「post の手順と合否」に従う。
    欠けた場合は理由 (self.error) を残して停止し、成功とは言わない。

使い方:
  python3 ad_flow.py discord post --text-file FILE [--to RECIPIENT]
  python3 ad_flow.py discord approve --text-file FILE [--to RECIPIENT]
"""

import argparse
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gui_flow  # noqa: E402
from gui_flow import (  # noqa: E402
    check_approval,
    consume_approval,
    create_approval,
    effective_acfg,
    load_config,
)

APP = "discord"
AD_BIN = "~/muse-tools/agent-desktop/agent-desktop"


# ---------------------------------------------------------------------------
# ツリー探索 (純粋関数)
# ---------------------------------------------------------------------------


def iter_nodes(tree):
    """ツリーを文書順 (深さ優先) にたどる。"""
    if not isinstance(tree, dict):
        return
    yield tree
    for child in tree.get("children") or []:
        yield from iter_nodes(child)


def node_name(node):
    return node.get("name") or ""


def find_dm_rows(tree, aliases):
    """DM 一覧で宛先と完全一致する行 (link) の ref をすべて返す。

    行の name は「<表示名> (ダイレクトメッセージ), <状態>」形式。
    表示名の直後が " (ダイレクトメッセージ)" であることまで含めて
    一致条件にする (部分一致の別人行を拾わないため)。
    """
    refs = []
    for node in iter_nodes(tree):
        if node.get("role") != "link":
            continue
        name = node_name(node)
        for alias in aliases:
            if name.startswith(alias + " (ダイレクトメッセージ)"):
                ref = node.get("ref_id")
                if ref and ref not in refs:
                    refs.append(ref)
    return refs


def _descendant_labels(node):
    """配下の statictext の name を文書順に集める。"""
    return [
        node_name(n)
        for n in iter_nodes(node)
        if n.get("role") == "statictext" and node_name(n)
    ]


def find_home_ref(tree, names):
    """ホーム (DM へ戻る) 要素の ref を返す。無ければ None。

    Discord のレールボタンは name を持たない group で、中の
    statictext がラベルになっている (実測: ホームは
    「ダイレクトメッセージ」、各サーバーはサーバー名)。
    そのため (1) link/button の name 一致、(2) group の配下ラベルが
    すべて同じ対象名である場合、の 2 形式で探す。
    """
    if not names:
        return None
    for node in iter_nodes(tree):
        if node.get("role") in ("link", "button") and node_name(node) in names:
            ref = node.get("ref_id")
            if ref:
                return ref
    for node in iter_nodes(tree):
        if node.get("role") != "group":
            continue
        ref = node.get("ref_id")
        if not ref or "Click" not in (node.get("available_actions") or []):
            continue
        labels = _descendant_labels(node)
        if labels and len(set(labels)) == 1 and labels[0] in names:
            return ref
    return None


def header_alias(tree, aliases):
    """開いているトークの見出し (heading) が指す alias を返す。"""
    for node in iter_nodes(tree):
        if node.get("role") == "heading" and node_name(node) in aliases:
            return node_name(node)
    return None


def find_input(tree, alias):
    """入力欄 (textfield) を探し (ref, value) を返す。無ければ None。"""
    target = "@%s へメッセージを送信" % alias
    for node in iter_nodes(tree):
        if node.get("role") == "textfield" and node_name(node) == target:
            return node.get("ref_id"), node.get("value") or ""
    return None


def message_bodies(tree, alias):
    """メッセージ一覧 (list) 配下の本文 statictext を文書順に返す。"""
    list_name = "%sのメッセージ" % alias
    for node in iter_nodes(tree):
        if node.get("role") == "list" and node_name(node) == list_name:
            return [
                node_name(n) for n in iter_nodes(node) if n.get("role") == "statictext"
            ]
    return []


def strip_fences(text):
    """コードブロックの ``` 囲みを外した本文を返す。"""
    body = text
    if body.startswith("```\n"):
        body = body[4:]
    if body.endswith("\n```"):
        body = body[:-4]
    elif body.endswith("```"):
        body = body[:-3]
    return body


def value_matches(value, text):
    """入力欄の value と期待本文の比較 (末尾の改行 1 つまでの差は許容)。"""
    v = value or ""
    if v.endswith("\n"):
        v = v[:-1]
    t = text
    if t.endswith("\n"):
        t = t[:-1]
    return v == t


# ---------------------------------------------------------------------------
# 送信手順
# ---------------------------------------------------------------------------


class AdRunner:
    """agent-desktop 経由の Discord post 手順 (DEFINITION.md 準拠)。"""

    def __init__(self, cfg, client, to=None):
        self.cfg = cfg
        self.app_cfg = cfg["apps"][APP]
        self.recipient_id = to or self.app_cfg.get("default_recipient")
        recips = self.app_cfg.get("recipients") or {}
        entry = recips.get(self.recipient_id) or {}
        self.acfg = effective_acfg(self.app_cfg, entry)
        self.aliases = list(self.acfg.get("recipient_aliases", []))
        self.client = client
        self.checks = []
        self.error = None
        self.screenshot = None

    # -- 記録 ------------------------------------------------------------
    def _check(self, name, ok, evidence):
        self.checks.append({"name": name, "ok": bool(ok), "evidence": evidence})

    def _fail(self, error, name, evidence):
        self.error = error
        self._check(name, False, evidence)
        return False

    # -- 本体 ------------------------------------------------------------
    def post(self, text):
        ok, reason, _rec = check_approval(APP, self.acfg, text)
        if not ok:
            return self._fail(
                "approval_missing",
                "approval",
                "承認記録が無いか無効 (%s)。Mac には触れていない" % reason,
            )
        self._check("approval", True, "承認記録あり (gui_flow と共通の記録)")

        snap = self.client.snapshot()
        if snap is None:
            return self._fail("snapshot_failed", "snapshot", "ツリーを取得できなかった")
        rows = find_dm_rows(snap.get("tree"), self.aliases)
        if not rows:
            rows = self._home_retry(snap)
            if rows is None:
                return False  # _home_retry 内で記録済み
        if len(rows) > 1:
            return self._fail(
                "nav_recipient_ambiguous",
                "nav_scrutiny",
                "宛先 (%s) と完全一致する DM 行が %d 件あり特定できない"
                % ("/".join(self.aliases), len(rows)),
            )
        if not self.client.click(rows[0]):
            return self._fail(
                "nav_click_failed",
                "nav_scrutiny",
                "宛先の DM 行をクリックできなかった",
            )
        self._check("nav_scrutiny", True, "DM 一覧で宛先本人行を特定しクリックした")

        snap = self.client.snapshot()
        if snap is None:
            return self._fail(
                "snapshot_failed", "snapshot", "トークを開いた後の撮影に失敗"
            )
        tree = snap.get("tree")
        halias = header_alias(tree, self.aliases)
        if halias is None:
            return self._fail(
                "recipient_mismatch",
                "recipient",
                "開いたトークの見出しが宛先 (%s) と一致しない" % "/".join(self.aliases),
            )
        found = find_input(tree, halias)
        if found is None:
            return self._fail(
                "recipient_mismatch",
                "recipient",
                "宛先の入力欄 (@%s へメッセージを送信) が見つからない" % halias,
            )
        input_ref, value = found
        self._check("recipient", True, "見出しと入力欄で宛先 '%s' を確認" % halias)
        if (value or "").strip():
            return self._fail(
                "anomaly_leftover",
                "field_state",
                "入力欄に残りがある (消去も入力もしない): %r" % value[:40],
            )
        self._check("field_state", True, "入力前の欄は空")

        if not self.client.set_value(input_ref, text):
            return self._fail("input_failed", "input_status", "本文を入力できなかった")
        snap = self.client.snapshot()
        if snap is None:
            return self._fail(
                "snapshot_failed", "snapshot", "入力後の読み戻し撮影に失敗"
            )
        tree = snap.get("tree")
        found = find_input(tree, halias)
        readback = found[1] if found else None
        if readback is None or not value_matches(readback, text):
            self.client.clear(input_ref)
            return self._fail(
                "readback_mismatch",
                "readback_match",
                "読み戻しが期待本文と一致しないため欄を消して停止 "
                "(読み戻し: %r)" % (readback or "")[:60],
            )
        self._check("readback_match", True, "読み戻しが期待本文と完全一致")
        if header_alias(tree, self.aliases) != halias:
            self.client.clear(input_ref)
            return self._fail(
                "recipient_mismatch",
                "recipient",
                "送信直前の再確認で見出しが宛先から変わっていた",
            )
        self._check("recipient", True, "送信直前の見出しも宛先 '%s'" % halias)

        if not self.client.press_enter(input_ref):
            return self._fail("send_failed", "send_key", "送信キーを送れなかった")
        self._check("send_key", True, "送信キーを 1 回送信")

        snap = self.client.snapshot()
        tree = snap.get("tree") if snap else None
        problems = []
        if tree is None:
            problems.append("送信後のツリーを取得できない")
        else:
            if header_alias(tree, self.aliases) != halias:
                problems.append("見出しが宛先でない")
            found = find_input(tree, halias) if halias else None
            if found is None or (found[1] or "").strip():
                problems.append("入力欄が空になっていない")
            bodies = message_bodies(tree, halias) if halias else []
            if not bodies or bodies[-1] != strip_fences(text):
                problems.append("最後のメッセージ本文が期待と一致しない")
        if problems:
            return self._fail(
                "post_send_unverified",
                "post_send",
                "送信後確認が取れなかった: %s (成功とは扱わない・再送しない)"
                % " / ".join(problems),
            )
        self._check(
            "post_send",
            True,
            "送信後確認: 欄は空・最後の本文が完全一致・見出しは宛先",
        )
        consume_approval(APP, self.acfg, text)
        self._check("approval_consumed", True, "承認記録を使用済みにした")
        shot = self.client.screenshot()
        self.screenshot = shot
        self._check(
            "screenshot",
            bool(shot),
            "送信後スクショ: %s" % (shot or "保存できなかった (参考証跡)"),
        )
        return True

    def _home_retry(self, first_snap):
        """DM 行が無い場合のホーム遷移救済 (最大 1 回)。

        戻り値: 見つけた rows (list)。失敗を記録し終えたら None
        (呼び出し側はそのまま False を返す)。
        """
        names = self.acfg.get("ad_home_names") or []
        href = find_home_ref(first_snap.get("tree"), names) if first_snap else None
        if href is None:
            self._check(
                "nav_home",
                False,
                "ホーム遷移の手掛かり (ad_home_names) がツリーに無い",
            )
            return self._fail_rows_not_found()
        if not self.client.click(href):
            self._check("nav_home", False, "ホーム要素をクリックできなかった")
            return self._fail_rows_not_found()
        snap = self.client.snapshot()
        if snap is None:
            self._check("nav_home", False, "ホーム遷移後の撮影に失敗")
            return self._fail_rows_not_found()
        rows = find_dm_rows(snap.get("tree"), self.aliases)
        self._check(
            "nav_home",
            bool(rows),
            "ホームへ遷移して再探索した (完全一致候補 %d 件)" % len(rows),
        )
        if not rows:
            return self._fail_rows_not_found()
        return rows

    def _fail_rows_not_found(self):
        self.error = "nav_recipient_not_found"
        self._check(
            "nav_scrutiny",
            False,
            "トーク一覧に宛先 (%s) と完全一致する行が見つからない"
            % "/".join(self.aliases),
        )
        return None


# ---------------------------------------------------------------------------
# 実クライアント (GUI キュー経由で agent-desktop を呼ぶ)
# ---------------------------------------------------------------------------


class MacAdClient:
    """gui_flow.Mac.run_queue で agent-desktop を実行するクライアント。"""

    def __init__(self, cfg):
        self.mac = gui_flow.Mac(cfg)
        self._seq = 0
        self.snapshot_last = None

    def _name(self, tag):
        self._seq += 1
        return "ad-%s-%d-%d" % (tag, int(time.time()), self._seq)

    def _run_ad(self, tag, ad_args, stdin_file_content=None):
        """agent-desktop を 1 コマンド実行し、封筒 JSON を dict で返す。"""
        name = self._name(tag)
        parts = [gui_flow.HDR]
        if stdin_file_content is not None:
            parts.append(
                "printf %s '%s' | base64 -D > ~/muse-tools/gf-out/%s-text.txt"
                % (base64.b64encode(stdin_file_content.encode()).decode(), name)
            )
        cmd = "%s %s" % (AD_BIN, ad_args)
        if stdin_file_content is not None:
            cmd += " --stdin < ~/muse-tools/gf-out/%s-text.txt" % name
        parts.append("%s > ~/muse-tools/gf-out/%s-out.json 2>/dev/null" % (cmd, name))
        parts.append('say "ad_rc=$?"')
        parts.append('say "status=ok"')
        res = self.mac.run_queue("\n".join(parts) + "\n", name, extras=("-out.json",))
        raw = res.get("_files", {}).get("-out.json", "")
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def snapshot(self):
        env = self._run_ad("snap", "snapshot --app Discord")
        if not env or not env.get("ok"):
            self.snapshot_last = None
            return None
        self.snapshot_last = env.get("data")
        return self.snapshot_last

    def click(self, ref):
        env = self._run_ad("click", "click %s" % ref)
        return bool(env and env.get("ok"))

    def set_value(self, ref, text):
        env = self._run_ad("set", "set-value %s" % ref, stdin_file_content=text)
        return bool(env and env.get("ok"))

    def clear(self, ref):
        env = self._run_ad("clear", "clear %s" % ref)
        return bool(env and env.get("ok"))

    def press_enter(self, ref):
        env = self._run_ad("focus", "focus %s" % ref)
        if not (env and env.get("ok")):
            return False
        env = self._run_ad("press", "press return --app Discord")
        return bool(env and env.get("ok"))

    def screenshot(self):
        """送信後の証跡スクショ (ScreenCheck)。判定には使わない。"""
        name = self._name("shot")
        script = "\n".join(
            [
                gui_flow.HDR,
                "$SC list > ~/muse-tools/gf-out/%s-list.txt 2>/dev/null" % name,
                "WID=$(grep 'app=Discord' ~/muse-tools/gf-out/%s-list.txt "
                "| grep 'onscreen=true' | head -1 "
                "| sed -E 's/.*id=([0-9]+).*/\\1/')" % name,
                '$SC cap "$WID" ~/muse-tools/gf-out/%s.png 2>/dev/null' % name,
                'say "cap_rc=$?"',
                'say "status=ok"',
            ]
        )
        res = self.mac.run_queue(script + "\n", name)
        if res.get("cap_rc") != "0":
            return None
        # スクショは Mac 側に残る (報告時に scp で回収する想定の参考証跡)。
        return "~/muse-tools/gf-out/%s.png" % name


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ad_flow")
    parser.add_argument("app", choices=[APP])
    parser.add_argument("action", choices=["post", "approve"])
    parser.add_argument("--to", default=None)
    parser.add_argument("--text-file", required=True)
    args = parser.parse_args(argv)

    text = Path(args.text_file).read_text(encoding="utf-8")
    if text.endswith("\n"):
        text = text[:-1]
    cfg = load_config()
    if args.action == "approve":
        runner = AdRunner(cfg, MacAdClient(cfg), to=args.to)
        rec = create_approval(APP, runner.acfg, text)
        print(
            json.dumps(
                {
                    "ok": True,
                    "approval_id": rec["text_sha256"][:12],
                    "expires_at": rec["expires_at"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    runner = AdRunner(cfg, MacAdClient(cfg), to=args.to)
    ok = runner.post(text)
    print(
        json.dumps(
            {
                "ok": ok,
                "error": runner.error,
                "checks": runner.checks,
                "screenshot": runner.screenshot,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
