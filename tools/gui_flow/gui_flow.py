#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui_flow — LINE / Discord の GUI 送信を一気通貫で実行する単一 CLI。

エージェントの仕事は「このプログラムを 1 回呼んで、結果 JSON を読む」だけ。
前面化 → 欄の空確認 → 入力 → (送信) → 最終検閲 (撮影 + OCR 照合) までの
ステップ判断と Mac との往復は、すべてこのプログラムの内部に閉じ込める。

使い方:
  gui_flow.py discord input  --text-file msg.txt
  gui_flow.py discord verify --mode field  --text-file msg.txt
  gui_flow.py discord send
  gui_flow.py discord clear
  gui_flow.py discord post   --text-file msg.txt   # 入力+送信+送信後検閲
  gui_flow.py discord test   --text-file msg.txt   # 入力+検閲+空に戻す (送信なし)
  gui_flow.py line    ... 同上

出力: 標準出力に JSON (合否・チェック内訳・各ステップ所要秒数)。
終了コード: 0 = 全チェック合格, 2 = 異常検知または失敗。

設計上の要点 (高速化):
  - Mac 側の手順はフェーズ単位で 1 本のキュー投入にまとめる (往復最小化)。
  - SSH は ControlMaster で多重化し、接続コストを初回だけにする。
  - 高速パスでは中間キャプチャをしない。撮影 + OCR は最終検閲の 1 回だけ。
  - 入力欄の内容確認はクリップボード読み戻し (データ確認) で行う。
  - キュー投入は「別名で書いてから mv」する (書き込み途中の実行を防ぐ)。

安全原則:
  - 異常 (宛先不一致 / 欄に残りあり / ウィンドウなし / 照合不一致など) を
    検知したら送信せずに停止し、結果 JSON で報告する。
  - post は送信前検閲 (入力 + 欄の OCR 照合) が全て合格したときだけ
    送信キーに進む。
  - 送信前検閲が「本文の不一致」だけで落ちた場合に限り、post は欄を
    全消去して再入力する高速リトライを最大 2 回まで自動で行う。
    宛先不一致・欄の残り・画面が読めない等の異常はリトライせず即停止する。
  - 送信後検閲 NG は自動で送り直さず、報告のみとする。

環境依存値 (接続先・座標・宛先名など) はコードに書かず、
同ディレクトリの config.local.json (git 管理外) から読む。
雛形は config.example.json を参照。
"""

import argparse
import base64
import difflib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))

# --- 形式ルール (アプリ別の定型。本人の決定で固定) -------------------------
# Discord: コードブロックのみ。ブロック先頭行が [SYSTEM] 行。
#          【AIエージェントからのお知らせ】の独立行は付けない。
# LINE   : 1 行目は必ず 【AIエージェントからのお知らせ】。
FIRST_LINE = {
    "discord": "[SYSTEM] AIエージェントからお知らせ",
    "line": "【AIエージェントからのお知らせ】",
}
FENCE = "```"
# 送信キー (KeyHelper keyf の引数: キーコード 36 = Return)
SEND_KEY = {
    "discord": "36 -",  # 素の Enter
    "line": "36 cmd",  # Cmd+Enter
}
# Discord の改行キー (Shift+Enter)。LINE は貼り付けで改行ごと入れる。
NEWLINE_KEY = "36 shift"
# post の送信前検閲リトライ上限 (本人の承認仕様: 最大 2 回)
MAX_PRE_SEND_RETRIES = 2
# 送信前検閲で「本文の不一致」とみなし、高速リトライを許可するチェック名。
# これ以外のチェック (宛先・構造・撮影可否など) が 1 つでも落ちたら即停止する。
RETRYABLE_CHECKS = {"readback_match", "body_match", "field_state", "first_line"}


# --- 設定 -------------------------------------------------------------------


def load_config():
    path = os.environ.get("GUI_FLOW_CONFIG") or os.path.join(HERE, "config.local.json")
    if not os.path.exists(path):
        raise SystemExit(
            "config not found: %s (copy config.example.json to config.local.json)"
            % path
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --- SSH (ControlMaster 多重化) ----------------------------------------------


class Mac:
    def __init__(self, cfg):
        s = cfg["ssh"]
        self.base = [
            "ssh",
            "-i",
            s["identity"],
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ProxyCommand=%s %%h %%p" % s["proxy_script"],
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=25",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPath=%s" % s.get("control_path", "/tmp/gf-ssh-ctl"),
            "-o",
            "ControlPersist=180",
            "%s@%s" % (s["user"], s["host"]),
        ]

    def run(self, remote_cmd, stdin_text=None, timeout=240):
        data = stdin_text.encode("utf-8") if stdin_text is not None else None
        p = subprocess.run(
            self.base + [remote_cmd],
            input=data,
            capture_output=True,
            timeout=timeout,
        )
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        return p.returncode, out, err

    def run_queue(self, script_body, name, extras=(), timeout=240):
        """GUI キューへ 1 本投入し、完了を待ち、結果を dict で返す。

        投入はアトミックに行う (書き込み途中のスクリプトをキュー係が拾って
        空実行する競合を防ぐ)。完了待ちはリモート側ループで行い、
        SSH はこの 1 本だけ。結果ファイル (key=value) に加え、extras で
        指定した付随ファイル (読み戻し・OCR 等) の中身も同じ接続で回収する。
        """
        remote_parts = [
            "mkdir -p ~/muse-tools/gf-out",
            "cat > ~/muse-tools/gf-out/.in-%s" % name,
            "mv ~/muse-tools/gf-out/.in-%s ~/muse-tools/gui-queue/%s" % (name, name),
            "n=0; while [ ! -f ~/muse-tools/gui-done/%s ]; do "
            "n=$((n+1)); if [ $n -gt 400 ]; then echo GF_WAIT_TIMEOUT; exit 9; fi; "
            "sleep 0.3; done" % name,
            "cat ~/muse-tools/gf-out/%s.result" % name,
        ]
        for suf in extras:
            remote_parts.append("echo __GF_FILE__%s" % suf)
            remote_parts.append(
                "cat ~/muse-tools/gf-out/%s%s 2>/dev/null" % (name, suf)
            )
            remote_parts.append("echo __GF_EOF__%s" % suf)
        remote = "; ".join(remote_parts)
        rc, out, err = self.run(remote, stdin_text=script_body, timeout=timeout)
        result = {"_files": {}}
        chunks = out.split("__GF_FILE__")
        head = chunks[0]
        for line in head.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
        for chunk in chunks[1:]:
            suf, _, rest = chunk.partition("\n")
            suf = suf.strip()
            body, _, _ = rest.partition("__GF_EOF__" + suf)
            result["_files"][suf] = body
        result["_ssh_rc"] = rc
        if "GF_WAIT_TIMEOUT" in out:
            result["status"] = "queue_timeout"
        return result


# --- キュースクリプト生成 ------------------------------------------------------

HDR = """export LC_ALL=en_US.UTF-8
GH=~/muse-tools/GuiHelper.app/Contents/MacOS/GuiHelper
KH=~/muse-tools/KeyHelper.app/Contents/MacOS/KeyHelper
SC=~/muse-tools/ScreenCheck.app/Contents/MacOS/ScreenCheck
mkdir -p ~/muse-tools/gf-out
R=~/muse-tools/gf-out/__NAME__.result
: > $R
say() { echo "$1" >> $R; }
"""


def b64(s):
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def build_input_script(name, app, acfg, text):
    """入力フェーズ: 前面化 → 移動 → 空確認 → 入力 → 読み戻し。

    欄に残りがある場合は異常として、その場で停止する (消去も入力もしない)。
    """
    lines = text.split("\n")
    parts = [HDR]
    parts.append(
        "rm -f ~/muse-tools/gf-out/__NAME__-readback.txt "
        "~/muse-tools/gf-out/__NAME__-leftover.txt"
    )
    parts.append('open -a "%s"' % acfg["app_name"])
    parts.append("sleep 0.8")
    if acfg.get("nav_click"):
        parts.append("$GH click %d %d" % tuple(acfg["nav_click"]))
        parts.append("sleep 0.5")
    parts.append("PID=$(pgrep -x %s | head -1)" % acfg["process"])
    parts.append('say "pid=$PID"')
    parts.append('if [ -z "$PID" ]; then say "status=app_not_running"; exit 0; fi')
    parts.append("$GH click %d %d" % tuple(acfg["input_click"]))
    parts.append("sleep 0.4")
    # 空確認 (センチネル回転: 欄が空ならクリップボードはセンチネルのまま)
    parts.append('SENT="GF_SENT_A_9d41"')
    parts.append('printf %s "$SENT" | pbcopy')
    parts.append("$KH keyf 0 cmd pid $PID")
    parts.append("sleep 0.25")
    parts.append("$KH keyf 8 cmd pid $PID")
    parts.append("sleep 0.3")
    parts.append("PB=$(pbpaste)")
    parts.append('if [ "$PB" != "$SENT" ]; then')
    parts.append('  printf %s "$PB" > ~/muse-tools/gf-out/__NAME__-leftover.txt')
    parts.append('  say "leftover_present=yes"')
    parts.append('  say "status=anomaly_leftover"')
    parts.append("  exit 0")
    parts.append("fi")
    parts.append('say "field_empty_before=yes"')
    # 入力
    if app == "discord":
        for i, ln in enumerate(lines):
            if i > 0:
                parts.append("$KH keyf %s pid $PID" % NEWLINE_KEY)
                parts.append("sleep 0.15")
            parts.append("L=$(echo '%s' | base64 -D)" % b64(ln))
            parts.append('$GH type "$L"')
    else:  # line: type で改行 (LF) ごと一括入力する。
        # (貼り付けは Cmd+V キーイベントを使うが、それは LINE 内では無効。
        #  type の LF は LINE では改行として入ることが実証済み)
        parts.append("TXT=$(echo '%s' | base64 -D)" % b64(text))
        parts.append('$GH type "$TXT"')
        parts.append("sleep 0.3")
    # 読み戻し (データ確認)
    parts.append('SENT2="GF_SENT_B_71c0"')
    parts.append('printf %s "$SENT2" | pbcopy')
    parts.append("$GH click %d %d" % tuple(acfg["input_click"]))
    parts.append("sleep 0.3")
    parts.append("$KH keyf 0 cmd pid $PID")
    parts.append("sleep 0.25")
    parts.append("$KH keyf 8 cmd pid $PID")
    parts.append("sleep 0.3")
    parts.append("GOT=$(pbpaste)")
    parts.append(
        'if [ "$GOT" = "$SENT2" ]; then say "status=fail_readback_empty"; exit 0; fi'
    )
    parts.append('printf %s "$GOT" > ~/muse-tools/gf-out/__NAME__-readback.txt')
    parts.append('say "readback_present=yes"')
    parts.append('say "status=ok"')
    return ("\n".join(parts) + "\n").replace("__NAME__", name)


def build_send_script(name, app, acfg):
    """送信フェーズ: pid を解決し、送信キーを 1 回だけ送る。"""
    parts = [HDR]
    parts.append("PID=$(pgrep -x %s | head -1)" % acfg["process"])
    parts.append('say "pid=$PID"')
    parts.append('if [ -z "$PID" ]; then say "status=app_not_running"; exit 0; fi')
    parts.append("$KH keyf %s pid $PID" % SEND_KEY[app])
    parts.append('say "send_key_sent=1"')
    parts.append('say "status=ok"')
    return ("\n".join(parts) + "\n").replace("__NAME__", name)


def build_clear_script(name, app, acfg):
    """消去フェーズ: 入力欄を空にし、センチネルで空をデータ確認する。"""
    parts = [HDR]
    parts.append("PID=$(pgrep -x %s | head -1)" % acfg["process"])
    parts.append('say "pid=$PID"')
    parts.append('if [ -z "$PID" ]; then say "status=app_not_running"; exit 0; fi')
    parts.append('open -a "%s"' % acfg["app_name"])
    parts.append("sleep 0.6")
    if acfg.get("nav_click"):
        parts.append("$GH click %d %d" % tuple(acfg["nav_click"]))
        parts.append("sleep 0.4")
    parts.append("$GH click %d %d" % tuple(acfg["input_click"]))
    parts.append("sleep 0.4")
    parts.append("$KH keyf 0 cmd pid $PID")
    parts.append("sleep 0.25")
    parts.append("$KH keyf 51 - pid $PID")
    parts.append("sleep 0.3")
    parts.append('SENT="GF_SENT_C_55e2"')
    parts.append('printf %s "$SENT" | pbcopy')
    parts.append("$KH keyf 0 cmd pid $PID")
    parts.append("sleep 0.25")
    parts.append("$KH keyf 8 cmd pid $PID")
    parts.append("sleep 0.3")
    parts.append("PB=$(pbpaste)")
    parts.append(
        'if [ "$PB" = "$SENT" ]; then say "field_empty=yes"; say "status=ok"; '
        'else say "field_empty=no"; say "status=fail_not_empty"; fi'
    )
    return ("\n".join(parts) + "\n").replace("__NAME__", name)


# ウィンドウ選択 (ScreenCheck list から対象アプリ最大の表示中ウィンドウを 1 つ選ぶ)
AWK_SELECT = r"""awk -v app="$APP" '
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


def build_verify_script(name, app, acfg):
    """検閲フェーズ: ウィンドウ特定 → 撮影 → OCR。撮影はこの 1 回だけ。"""
    parts = [HDR]
    parts.append("rm -f ~/muse-tools/gf-out/__NAME__-ocr.txt")
    parts.append('APP="%s"' % acfg["window_app"])
    parts.append("WIN=$($SC list | %s)" % AWK_SELECT)
    parts.append('if [ -z "$WIN" ]; then say "status=window_not_found"; exit 0; fi')
    parts.append('say "window=$WIN"')
    parts.append('WID=$(printf %s "$WIN" | cut -d" " -f1)')
    parts.append("$SC cap $WID ~/muse-tools/gf-out/__NAME__.png")
    parts.append(
        "if [ ! -s ~/muse-tools/gf-out/__NAME__.png ]; then "
        'say "status=capture_failed"; exit 0; fi'
    )
    parts.append(
        "swift ~/muse-tools/ocr.swift ~/muse-tools/gf-out/__NAME__.png "
        "> ~/muse-tools/gf-out/__NAME__-ocr.txt 2>&1"
    )
    parts.append('say "status=ok"')
    return ("\n".join(parts) + "\n").replace("__NAME__", name)


# --- OCR 解析と照合 ------------------------------------------------------------

OCR_RE = re.compile(
    r"^TEXT \[x=([\d.]+) y=([\d.]+) w=([\d.]+) h=([\d.]+) conf=([\d.]+)\] (.*)$"
)


def parse_ocr(text):
    items = []
    for line in (text or "").splitlines():
        m = OCR_RE.match(line.strip())
        if m:
            x, y, w, h, conf = (float(m.group(i)) for i in range(1, 6))
            items.append(
                {"x": x, "y": y, "w": w, "h": h, "conf": conf, "text": m.group(6)}
            )
    items.sort(key=lambda d: (round(d["y"] / 8), d["x"]))
    return items


def norm(s):
    """照合用の正規化: NFKC (全角→半角) + 空白除去。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def ratio(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def content_lines(app, text):
    """期待文のうち本文として照合する行 (Discord のフェンス行は除く)。"""
    lines = [ln for ln in text.split("\n")]
    if app == "discord":
        lines = [ln for ln in lines if ln.strip() != FENCE]
    return [ln for ln in lines if ln.strip() != ""]


def best_match(expected, candidates):
    """candidates (OCR items) の中から expected に最も近いものを返す。"""
    best, best_r = None, 0.0
    for c in candidates:
        r = ratio(expected, c["text"])
        if r > best_r:
            best, best_r = c, r
    return best, best_r


def run_verify_checks(app, acfg, ocr_text, win, expected_text, mode):
    """verify の必須チェック一式。戻り値は checks のリスト。

    本文の照合は「アンカー方式」: 期待文の先頭行に最もよく一致する OCR 行を
    アンカーとし、その周辺の帯を対象領域とする。入力欄は行数に応じて
    上方向に伸びるため、固定オフセットでは領域を決めない。
    """
    checks = []
    items = parse_ocr(ocr_text)
    if not items:
        return [
            {
                "name": "ocr_readable",
                "ok": False,
                "evidence": "OCR で文字が読めなかった (画面が見えていない可能性)",
            }
        ]
    height = win[4]
    header = [it for it in items if it["y"] < float(acfg.get("header_band", 110))]

    # 1) 宛先
    aliases = acfg.get("recipient_aliases", [])
    hit = None
    for scope_name, scope in (("header", header), ("all", items)):
        for it in scope:
            if any(norm(a) and norm(a) in norm(it["text"]) for a in aliases):
                hit = (scope_name, it["text"])
                break
        if hit:
            break
    checks.append(
        {
            "name": "recipient",
            "ok": bool(hit),
            "evidence": (
                ("宛先 '%s' を %s で確認" % (hit[1], hit[0]))
                if hit
                else "宛先 (%s) が画面に見当たらない" % "/".join(aliases)
            ),
        }
    )

    # 2) 構造 (Discord はコードブロック形式か) — 期待文そのもので判定
    if app == "discord":
        ls = expected_text.strip().split("\n")
        struct_ok = ls[0].strip() == FENCE and ls[-1].strip() == FENCE
        checks.append(
            {
                "name": "structure_codeblock",
                "ok": struct_ok,
                "evidence": (
                    "コードブロック (``` で囲み) 形式"
                    if struct_ok
                    else "フェンス行が無い"
                ),
            }
        )

    # 3) 1 行目 + 本文 (アンカー方式・行単位・順序つき)
    lines = content_lines(app, expected_text)
    if not lines:
        checks.append({"name": "body_match", "ok": False, "evidence": "期待文が空"})
        return checks
    scored = [(ratio(lines[0], it["text"]), it) for it in items]
    best_r = max(r for r, _ in scored)
    strong = [it for r, it in scored if r >= 0.8]
    anchor = (
        max(strong, key=lambda it: it["y"])
        if strong
        else max(scored, key=lambda t: t[0])[1]
    )
    first_ok = best_r >= 0.8 and norm(lines[0]) == norm(FIRST_LINE[app])
    checks.append(
        {
            "name": "first_line",
            "ok": first_ok,
            "evidence": "1 行目 = '%s' (最良一致 %.2f)" % (lines[0], best_r),
        }
    )
    top = anchor["y"] - 45
    bottom = anchor["y"] + 60 + 30 * len(lines)
    region = [it for it in items if top <= it["y"] <= bottom]
    per_line = []
    all_ok = True
    order_ok = True
    last_idx = -1
    for ln in lines:
        c, r = best_match(ln, region)
        # 短い行は 1 文字違いの意味が大きい (例: 不要↔必要) ので厳しめに見る
        ok = r >= (0.9 if len(norm(ln)) <= 8 else 0.75)
        all_ok = all_ok and ok
        if c is not None:
            idx = region.index(c)
            if idx < last_idx:
                order_ok = False
            last_idx = max(last_idx, idx)
        per_line.append("%s (%.2f)" % (ln, r))
    checks.append(
        {
            "name": "body_match",
            "ok": all_ok and order_ok,
            "evidence": "行単位照合: %s / 順序 %s"
            % (" | ".join(per_line), "OK" if order_ok else "NG"),
        }
    )

    # 4) 入力欄の状態
    if mode == "field":
        checks.append(
            {
                "name": "field_state",
                "ok": all_ok,
                "evidence": (
                    "入力欄に期待文が入っている"
                    if all_ok
                    else "入力欄の内容が期待文と一致しない"
                ),
            }
        )
    else:
        below = [it for it in items if it["y"] > bottom + 10 or it["y"] >= height - 110]
        leak = any(best_match(ln, below)[1] >= 0.5 for ln in lines) if below else False
        checks.append(
            {
                "name": "field_state",
                "ok": not leak,
                "evidence": (
                    "送信後、入力欄は空 (期待文は残っていない)"
                    if not leak
                    else "入力欄に期待文が残っている"
                ),
            }
        )
    return checks


def classify_pre_send(checks):
    """送信前チェック群を分類する。

    戻り値: (failed, retryable, failed_names, diff_summary)
    retryable は「落ちたチェックが全て本文の不一致系 (RETRYABLE_CHECKS)
    のとき」だけ True。宛先不一致・残りあり・画面が読めない等が混ざれば
    False (即停止)。
    """
    failed = [c for c in checks if not c["ok"]]
    if not failed:
        return False, False, [], ""
    names = [c["name"] for c in failed]
    retryable = all(n in RETRYABLE_CHECKS for n in names)
    summary = " / ".join("%s: %s" % (c["name"], c["evidence"]) for c in failed)
    return True, retryable, names, summary[:300]


def run_post(r, text, max_retries=MAX_PRE_SEND_RETRIES):
    """post フロー: 送信前 (入力 + 欄検閲) → 送信 → 送信後検閲。

    送信前が「本文の不一致」だけで落ちた場合に限り、欄を全消去して
    再入力する高速リトライを最大 max_retries 回まで行う。それ以外の
    異常はリトライせず即停止する。送信後検閲 NG は送り直さない。

    戻り値: (ok, error, retry_info)
    retry_info = {"count": リトライ実施回数,
                  "attempts": [各失敗回の failed_checks と差分要約]}
    """
    attempts = []
    retries_done = 0
    while True:
        ok_pre, retryable, record = r.pre_send_once(text)
        if ok_pre:
            break
        record = dict(record)
        record["attempt"] = len(attempts) + 1
        attempts.append(record)
        info = {"count": retries_done, "attempts": attempts}
        if not retryable:
            return False, "pre_send_failed_stop", info
        if retries_done >= max_retries:
            return False, "pre_send_failed_retry_exhausted", info
        # 消すのはこの run で自分が入力した分だけ (pre_send_once が通した範囲)
        if not r.clear():
            return False, "retry_clear_failed", info
        retries_done += 1
    info = {"count": retries_done, "attempts": attempts}
    if not r.send():
        return False, "send_failed", info
    time.sleep(1.0)
    if not r.verify(text, "bubble"):
        return False, "post_send_verify_failed", info
    return True, None, info


# --- フェーズ実行 ---------------------------------------------------------------


class Runner:
    def __init__(self, cfg, app):
        self.mac = Mac(cfg)
        self.app = app
        self.acfg = cfg["apps"][app]
        self.steps = []
        self.checks = []
        self.counter = 0

    def _name(self, action):
        self.counter += 1
        return "gf-%s-%s-%d-%d.sh" % (
            self.app,
            action,
            int(time.time() * 1000),
            self.counter,
        )

    def _timed(self, label, fn):
        t0 = time.time()
        out = fn()
        self.steps.append({"name": label, "sec": round(time.time() - t0, 2)})
        return out

    def connect(self):
        def f():
            rc, out, _ = self.mac.run("echo GF_OK", timeout=60)
            return rc == 0 and "GF_OK" in out

        ok = self._timed("connect", f)
        self.checks.append(
            {
                "name": "connect",
                "ok": ok,
                "evidence": "SSH 接続 (ControlMaster 初回確立)",
            }
        )
        return ok

    def input(self, text):
        name = self._name("input")
        res = self._timed(
            "input",
            lambda: self.mac.run_queue(
                build_input_script(name, self.app, self.acfg, text),
                name,
                extras=("-readback.txt", "-leftover.txt"),
            ),
        )
        return self._eval_input(res, text)

    def _eval_input(self, res, text):
        st = res.get("status", "no_result")
        ok = st == "ok"
        self.checks.append(
            {
                "name": "input_status",
                "ok": ok,
                "evidence": "status=%s pid=%s" % (st, res.get("pid", "?")),
            }
        )
        if st == "anomaly_leftover":
            left = res.get("_files", {}).get("-leftover.txt", "")
            self.checks.append(
                {
                    "name": "field_empty_before",
                    "ok": False,
                    "evidence": "欄に残りあり (消去せず停止): %s" % left.strip()[:80],
                }
            )
            return False
        if not ok:
            return False
        self.checks.append(
            {
                "name": "field_empty_before",
                "ok": True,
                "evidence": "入力前の欄は空 (センチネル確認)",
            }
        )
        readback = res.get("_files", {}).get("-readback.txt", "")
        match = readback.strip() == text.strip()
        self.checks.append(
            {
                "name": "readback_match",
                "ok": match,
                "evidence": (
                    "読み戻し一致 (改行位置を含む)"
                    if match
                    else "読み戻し不一致: 期待 %r / 実際 %r"
                    % (text.strip()[:60], readback.strip()[:60])
                ),
            }
        )
        return match

    def send(self):
        name = self._name("send")
        res = self._timed(
            "send",
            lambda: self.mac.run_queue(
                build_send_script(name, self.app, self.acfg), name
            ),
        )
        ok = res.get("status") == "ok" and res.get("send_key_sent") == "1"
        self.checks.append(
            {
                "name": "send_key",
                "ok": ok,
                "evidence": "送信キーを 1 回送信 (pid=%s)" % res.get("pid", "?"),
            }
        )
        return ok

    def clear(self):
        name = self._name("clear")
        res = self._timed(
            "clear",
            lambda: self.mac.run_queue(
                build_clear_script(name, self.app, self.acfg), name
            ),
        )
        ok = res.get("status") == "ok" and res.get("field_empty") == "yes"
        self.checks.append(
            {
                "name": "field_cleared",
                "ok": ok,
                "evidence": (
                    "入力欄を空に戻した (センチネル確認)" if ok else "空にできなかった"
                ),
            }
        )
        return ok

    def verify(self, text, mode):
        holder = {}

        def f():
            name = self._name("verify")
            holder["res"] = self.mac.run_queue(
                build_verify_script(name, self.app, self.acfg),
                name,
                extras=("-ocr.txt",),
            )
            return holder["res"]

        res = self._timed("verify", f)
        st = res.get("status", "no_result")
        if st != "ok":
            self.checks.append(
                {
                    "name": "verify_capture",
                    "ok": False,
                    "evidence": "検閲できず: status=%s" % st,
                }
            )
            return False
        win = tuple(int(float(v)) for v in res.get("window", "0 0 0 0 0").split())
        ocr_text = res.get("_files", {}).get("-ocr.txt", "")
        vchecks = run_verify_checks(self.app, self.acfg, ocr_text, win, text, mode)
        self.checks.extend(vchecks)
        return all(c["ok"] for c in vchecks)

    def pre_send_once(self, text):
        """送信前の一巡: 入力 → 欄モード検閲。

        戻り値: (ok, retryable, record)
        record は {"failed_checks": [...], "diff_summary": "..."} で、
        失敗時にどのチェックがどう違ったかを保持する (成功時は空)。
        """
        start = len(self.checks)
        passed = self.input(text) and self.verify(text, "field")
        _, retryable, names, summary = classify_pre_send(self.checks[start:])
        if passed:
            return True, False, {"failed_checks": [], "diff_summary": ""}
        return False, retryable, {"failed_checks": names, "diff_summary": summary}


# --- メイン ---------------------------------------------------------------------


def read_text(args):
    if args.text_file:
        with open(args.text_file, encoding="utf-8") as f:
            return f.read().rstrip("\n")
    if args.text is not None:
        return args.text
    raise SystemExit("--text or --text-file is required for this action")


def main():
    ap = argparse.ArgumentParser(description="LINE/Discord GUI flow (one-shot)")
    ap.add_argument("app", choices=["discord", "line"])
    ap.add_argument(
        "action", choices=["input", "verify", "send", "clear", "post", "test"]
    )
    ap.add_argument("--text")
    ap.add_argument("--text-file")
    ap.add_argument("--mode", choices=["field", "bubble"], default="field")
    args = ap.parse_args()

    cfg = load_config()
    r = Runner(cfg, args.app)
    t0 = time.time()
    ok = True
    error = None
    retry_info = None
    try:
        if not r.connect():
            ok = False
            error = "connect_failed"
        elif args.action == "input":
            ok = r.input(read_text(args))
        elif args.action == "verify":
            ok = r.verify(read_text(args), args.mode)
        elif args.action == "send":
            ok = r.send()
        elif args.action == "clear":
            ok = r.clear()
        elif args.action == "post":
            text = read_text(args)
            ok, error, retry_info = run_post(r, text)
        elif args.action == "test":
            text = read_text(args)
            in_ok = r.input(text)
            v_ok = r.verify(text, "field") if in_ok else False
            c_ok = r.clear() if in_ok else False
            ok = in_ok and v_ok and c_ok
    except subprocess.TimeoutExpired:
        ok = False
        error = "timeout"
    except Exception as e:  # noqa: BLE001
        ok = False
        error = "%s: %s" % (type(e).__name__, e)

    out = {
        "ok": ok,
        "app": args.app,
        "action": args.action,
        "total_sec": round(time.time() - t0, 2),
        "steps": r.steps,
        "checks": r.checks,
        "retry": retry_info,
        "error": error,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
