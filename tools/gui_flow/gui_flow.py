#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui_flow — LINE / Discord の GUI 送信を一気通貫で実行する単一 CLI。

エージェントの仕事は「このプログラムを 1 回呼んで、結果 JSON を読む」だけ。
前面化 → 欄の空確認 → 入力 → (送信) → 最終検閲 (撮影 + OCR 照合) までの
ステップ判断と Mac との往復は、すべてこのプログラムの内部に閉じ込める。

使い方:
  gui_flow.py discord approve --text-file msg.txt [--expires <ISO8601>]
  gui_flow.py discord input  --text-file msg.txt
  gui_flow.py discord verify --mode field  --text-file msg.txt
  gui_flow.py discord send
  gui_flow.py discord clear
  gui_flow.py discord post   --text-file msg.txt   # 入力+送信+送信後検閲
  gui_flow.py discord test   --text-file msg.txt   # 入力+検閲+空に戻す (送信なし)
  gui_flow.py discord careful                      # 慎重モード (学習。送信なし)
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
  - 承認ゲート: post / send は、本人が文面を承認した記録 (承認記録) が
    無ければ送信しない。承認記録は approve サブコマンドで作成し、
    文面ハッシュ + アプリ + 宛先に紐づく。有効期限 (既定 24 時間) 内の
    未使用の記録だけが有効で、送信が成功したら使用済みにする
    (同じ承認で二重送信できない)。
  - post は送信前検閲 (入力 + 欄の OCR 照合) が全て合格したときだけ
    送信キーに進む。
  - 送信前検閲が「本文の不一致」だけで落ちた場合に限り、post は欄を
    全消去して再入力する高速リトライを最大 2 回まで自動で行う。
    宛先不一致・欄の残り・画面が読めない等の異常はリトライせず即停止する。
  - 送信後検閲 NG は自動で送り直さず、報告のみとする。
  - 送信成功時 (post / send) は、送信後検閲で撮影したキャプチャを VM 側の
    定位置 (~/workspace/mac-shots/) へ保存し、結果 JSON の screenshot に
    そのパスを返す (エージェントが探しに行く手順を定型化するため)。
    取得・保存に失敗しても送信結果は成功のままとし、screenshot は null +
    理由を checks に残す。
  - 慎重モード (careful) は例外処理ではなく「学習の経路」: 高速パスが
    対応できないケース (レイアウト変化で学習済み座標が合わない等) を
    段階的に解決し、学んだ座標・フレームを設定へ保存して、次回から
    そのケースを正常系として処理できるようにする。careful は送信キーを
    絶対に押さない (自己確認は入力 → 検閲 → 空に戻す、まで)。
  - 高速パスの停止理由が座標・形状の疑い (検閲の座標系チェックのみが
    落ちた / ウィンドウフレームが学習値から許容差超でずれた等) の場合は
    error を needs_calibration とし、careful での再学習を促す。宛先違い・
    欄の残り・構造エラーは学習対象にせず、従来通り人の慎重プロセス行き。
  - 調整用の数値はコードに直書きせず config の変数 (tunables / careful)
    で持つ。新しいケースへの対応は「コード改修」ではなく「変数の変更」
    で済む構造にする。config に値が無い場合は現行値と同じ既定値で動く。

環境依存値 (接続先・座標・宛先名など) はコードに書かず、
同ディレクトリの config.local.json (git 管理外) から読む。
雛形は config.example.json を参照。
"""

import argparse
import base64
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone

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
# 承認記録の有効期限 (既定)。approve --expires で明示指定もできる。
APPROVAL_DEFAULT_TTL = timedelta(hours=24)
# --expires にオフセットが無い場合は JST とみなす。
JST = timezone(timedelta(hours=9))


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


def config_path():
    return os.environ.get("GUI_FLOW_CONFIG") or os.path.join(HERE, "config.local.json")


# --- 調整用変数 (tunables) -----------------------------------------------------
# 調整用の数値はコードに直書きせず、config の "tunables" で上書きできる
# ようにする (変数を変えるだけで対応できるコーディング)。ここにあるのは
# 既定値で、現行の挙動と一致する。config.example.json にも同じ値を示す。

DEFAULT_TUNABLES = {
    "ssh_timeout": 240,  # SSH / キュー 1 回のタイムアウト (秒)
    "connect_timeout": 60,  # 接続確認のタイムアウト (秒)
    "post_send_wait": 1.0,  # 送信キーを押してから送信後検閲までの待機 (秒)
    "max_pre_send_retries": MAX_PRE_SEND_RETRIES,
    "approval_ttl_hours": 24,  # 承認記録の有効期限 (時間)
    "ocr_threshold": 0.8,  # 1 行目・アンカーの一致しきい値
    "strong_threshold": 0.8,  # アンカー候補 (強一致) のしきい値
    "short_line_threshold": 0.9,  # 短い行の一致しきい値 (1 文字違いが重い)
    "long_line_threshold": 0.75,  # 通常行の一致しきい値
    "short_line_max_len": 8,  # 「短い行」とみなす最大文字数 (空白除去後)
    "leak_threshold": 0.5,  # 送信後検閲で「欄に本文が残っている」とみなす一致度
}


def tunables(cfg):
    """config の tunables を既定値へ重ねて返す (無いキーは既定値で動く)。"""
    t = dict(DEFAULT_TUNABLES)
    t.update((cfg or {}).get("tunables") or {})
    return t


# --- 慎重モード (careful) の設定 ------------------------------------------------
# ケース依存の値 (探索グリッド・許容差・自己確認文面) はアプリ別の
# "careful" 設定で持つ。新しいケースへの対応は変数の変更で済ませる。

CAREFUL_BASE_DEFAULTS = {
    "frame_tolerance": 40,  # フレーム比較の許容差 (px、各辺の差の最大値)
    "probe_text": "GF_PROBE_8f3a",  # フォーカス判定用のプローブ文字列 (改行なし)
    "probe_x_ratios": [0.5, 0.35, 0.65, 0.25, 0.75],  # 横位置 (フレーム幅比)
    "probe_max_candidates": 36,  # 候補点の上限 (既定グリッド総数 31 を覆う)
    "self_check_text": "",  # 空なら SELF_CHECK_TEXTS の既定値を使う
}

# アプリ別の既定値 (入力欄の縦位置はアプリで大きく異なるため分ける)
CAREFUL_APP_DEFAULTS = {
    "discord": {"probe_y_offsets": [48, 30, 70, 16, 95, 130]},
    "line": {"probe_y_offsets": [120, 95, 145, 70, 170, 200]},
}

SELF_CHECK_TEXTS = {
    "discord": (
        "```\n"
        "[SYSTEM] AIエージェントからお知らせ\n"
        "キャリブレーションの確認です。\n"
        "送信はしません。\n"
        "```"
    ),
    "line": (
        "【AIエージェントからのお知らせ】\n"
        "キャリブレーションの確認です。\n"
        "送信はしません。"
    ),
}


def careful_cfg(app, acfg):
    """アプリ別の careful 設定を既定値へ重ねて返す。"""
    c = dict(CAREFUL_BASE_DEFAULTS)
    c.update(CAREFUL_APP_DEFAULTS.get(app, {}))
    c.update((acfg or {}).get("careful") or {})
    if not c.get("self_check_text"):
        c["self_check_text"] = SELF_CHECK_TEXTS[app]
    return c


def probe_candidates(frame, acfg, ccfg):
    """入力欄の候補点を優先順に生成する (純関数)。

    先頭は学習済みの input_click (フレーム内にある場合)。続いてフレーム
    下部の帯を、設定した y オフセット (近い順) × x 比率のグリッドで走査する。
    frame は (x, y, w, h)。
    """
    fx, fy, fw, fh = (int(v) for v in frame)
    bottom = fy + fh
    pts = []
    cur = (acfg or {}).get("input_click")
    if cur and fx <= cur[0] <= fx + fw and fy <= cur[1] <= fy + fh:
        pts.append((int(cur[0]), int(cur[1])))
    for yo in ccfg["probe_y_offsets"]:
        for xr in ccfg["probe_x_ratios"]:
            p = (int(fx + fw * float(xr)), int(bottom - int(yo)))
            if p not in pts:
                pts.append(p)
    return pts[: int(ccfg["probe_max_candidates"])]


def frame_drift(frame_a, frame_b):
    """2 つのフレーム (x, y, w, h) の各辺の差の最大値。どちらか欠けたら None。"""
    if not frame_a or not frame_b:
        return None
    return max(abs(int(a) - int(b)) for a, b in zip(frame_a, frame_b))


# --- 学習の保存 (config 書き込み + 学習記録) --------------------------------------
# careful が学んだ値は config.local.json (git 管理外) へ保存する。
# 書き込み前に必ずバックアップを取り、学習記録を JSONL へ 1 行残す。


def save_config(cfg, path=None, now=None):
    """config を保存し、書き込み前に取ったバックアップのパスを返す。"""
    path = path or config_path()
    now = now or datetime.now(JST)
    backup = "%s.bak-%s" % (path, now.strftime("%Y%m%d-%H%M%S"))
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = f.read()
        with open(backup, "wb") as f:
            f.write(data)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return backup


def learned_cases_path():
    return os.environ.get("GUI_FLOW_LEARNED") or os.path.join(
        HERE, "learned_cases.jsonl"
    )


def append_learned_case(record, path=None):
    """学習記録を JSONL へ 1 行追記する。"""
    path = path or learned_cases_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


# --- 承認記録 (承認ゲート) ---------------------------------------------------
# 「送信前の本人確認」をエージェント側の注意力ではなくプログラムの構造で
# 担保する。approve で文面ごとの承認記録を作り、post / send は記録が
# 無ければ送信キーに進まない。記録はローカル状態 (git 管理外) に置く。


def approvals_dir():
    return os.environ.get("GUI_FLOW_APPROVALS") or os.path.join(HERE, "approvals")


def canonical_text(text):
    """承認ハッシュ用の正規化: 前後の空白・改行を除く。"""
    return (text or "").strip()


def text_sha256(text):
    return hashlib.sha256(canonical_text(text).encode("utf-8")).hexdigest()


def recipient_key(acfg):
    """宛先の識別子 (config の宛先エイリアスの先頭)。"""
    aliases = acfg.get("recipient_aliases", [])
    return aliases[0] if aliases else ""


def _parse_dt(s):
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt


def _approval_path(sha):
    return os.path.join(approvals_dir(), sha + ".json")


def _write_record(rec):
    path = _approval_path(rec["text_sha256"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def create_approval(app, acfg, text, expires=None, now=None, ttl=None):
    """承認記録を作成 (既存の同文面の記録があれば作り直す) し、記録を返す。"""
    now = now or datetime.now(timezone.utc)
    exp_dt = _parse_dt(expires) if expires else now + (ttl or APPROVAL_DEFAULT_TTL)
    rec = {
        "app": app,
        "recipient": recipient_key(acfg),
        "text_sha256": text_sha256(text),
        "created_at": now.isoformat(),
        "expires_at": exp_dt.isoformat(),
        "used": False,
        "used_at": None,
    }
    os.makedirs(approvals_dir(), exist_ok=True)
    _write_record(rec)
    return rec


def check_approval(app, acfg, text, now=None):
    """承認記録を照合する。

    戻り値: (ok, reason, record)
    reason は ok / missing (記録なし・文面不一致) / mismatch (アプリ・宛先が
    違う) / used (使用済み) / expired (期限切れ) のいずれか。
    """
    now = now or datetime.now(timezone.utc)
    path = _approval_path(text_sha256(text))
    try:
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return False, "missing", None
    if rec.get("app") != app or rec.get("recipient") != recipient_key(acfg):
        return False, "mismatch", rec
    if rec.get("used"):
        return False, "used", rec
    if now > _parse_dt(rec["expires_at"]):
        return False, "expired", rec
    return True, "ok", rec


def consume_approval(app, acfg, text, now=None):
    """承認記録を使用済みにする。照合に失敗したら何もせず False を返す。"""
    ok, _, rec = check_approval(app, acfg, text, now=now)
    if not ok:
        return False
    rec = dict(rec)
    rec["used"] = True
    rec["used_at"] = (now or datetime.now(timezone.utc)).isoformat()
    _write_record(rec)
    return True


APPROVAL_REASON_JA = {
    "missing": "承認記録がない (未承認の文面)",
    "mismatch": "承認記録のアプリ・宛先が一致しない",
    "used": "承認記録は使用済み",
    "expired": "承認記録の有効期限切れ",
}


# --- 送信後スクショの受け渡し -------------------------------------------------
# 本人の希望は「送信したら毎回、送信後のスクショをチャットで見る」こと。
# エージェントが Mac 側の作業フォルダから最新キャプチャを探す属人的な
# 手順をやめ、送信成功時はプログラムが定位置へ保存してパスを返す。


def screenshots_dir():
    return os.environ.get("GUI_FLOW_SCREENSHOTS") or os.path.expanduser(
        "~/workspace/mac-shots"
    )


def decode_b64_bytes(text):
    """base64 テキスト (改行・空白混じり可) を bytes に戻す。失敗時は None。"""
    if not text:
        return None
    try:
        return base64.b64decode(re.sub(r"\s+", "", text), validate=True)
    except ValueError:
        return None


def save_screenshot_file(app, png_bytes, now=None):
    """送信後キャプチャを VM 側の定位置へ保存し、保存先パスを返す。

    ファイル名は gui-flow-<app>-<YYYYMMDD-HHMMSS>.png (JST)。
    """
    now = now or datetime.now(JST)
    d = screenshots_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "gui-flow-%s-%s.png" % (app, now.strftime("%Y%m%d-%H%M%S")))
    with open(path, "wb") as f:
        f.write(png_bytes)
    return path


def save_careful_capture(app, step, png_bytes, now=None):
    """慎重モードのステップ証拠キャプチャを保存し、パスを返す。

    ファイル名は careful-<app>-<YYYYMMDD-HHMMSS>-<step>.png (JST)。
    """
    now = now or datetime.now(JST)
    d = screenshots_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(
        d, "careful-%s-%s-%s.png" % (app, now.strftime("%Y%m%d-%H%M%S"), step)
    )
    with open(path, "wb") as f:
        f.write(png_bytes)
    return path


# --- SSH (ControlMaster 多重化) ----------------------------------------------


class Mac:
    def __init__(self, cfg):
        s = cfg["ssh"]
        t = tunables(cfg)
        self.timeout = int(t["ssh_timeout"])
        self.connect_timeout = int(t["connect_timeout"])
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

    def run(self, remote_cmd, stdin_text=None, timeout=None):
        if timeout is None:
            timeout = self.timeout
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

    def run_queue(self, script_body, name, extras=(), timeout=None):
        """GUI キューへ 1 本投入し、完了を待ち、結果を dict で返す。

        投入はアトミックに行う (書き込み途中のスクリプトをキュー係が拾って
        空実行する競合を防ぐ)。完了待ちはリモート側ループで行い、
        SSH はこの 1 本だけ。結果ファイル (key=value) に加え、extras で
        指定した付随ファイル (読み戻し・OCR 等) の中身も同じ接続で回収する。
        """
        if timeout is None:
            timeout = self.timeout
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


def build_readback_script(name, app, acfg):
    """読み戻し専用フェーズ: 前面化 → 欄クリック → 全選択コピー → 回収。

    入力も消去もしない。send の承認ゲートで「今欄に入っている文面」を
    特定するために使う。
    """
    parts = [HDR]
    parts.append("rm -f ~/muse-tools/gf-out/__NAME__-field.txt")
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
    parts.append('SENT="GF_SENT_R_3b77"')
    parts.append('printf %s "$SENT" | pbcopy')
    parts.append("$KH keyf 0 cmd pid $PID")
    parts.append("sleep 0.25")
    parts.append("$KH keyf 8 cmd pid $PID")
    parts.append("sleep 0.3")
    parts.append("PB=$(pbpaste)")
    parts.append('if [ "$PB" = "$SENT" ]; then say "status=field_empty"; exit 0; fi')
    parts.append('printf %s "$PB" > ~/muse-tools/gf-out/__NAME__-field.txt')
    parts.append('say "status=ok"')
    return ("\n".join(parts) + "\n").replace("__NAME__", name)


def build_probe_script(name, app, acfg, candidates, probe_text):
    """プローブフェーズ (careful 専用): 候補点を順に試して入力欄を探す。

    候補をクリック → プローブ文字列を入力 → センチネル回転で読み戻し、
    読み戻しがプローブ文字列と一致したら「フォーカスが当たった」と判定
    する。各候補の試行後、入力した分は必ず全選択 + 削除で消し、最後に
    欄が空であることをセンチネルで確認する。送信キーは一切送らない
    (このスクリプトに送信キーの呼び出しは含まれない)。
    欄に既存の残りがある場合は anomaly_leftover として何も触らず停止する。
    """
    flat = " ".join("%d %d" % (int(x), int(y)) for x, y in candidates)
    parts = [HDR]
    parts.append(
        "rm -f ~/muse-tools/gf-out/__NAME__-leftover.txt "
        "~/muse-tools/gf-out/__NAME__-hit.txt"
    )
    parts.append('open -a "%s"' % acfg["app_name"])
    parts.append("sleep 0.8")
    if acfg.get("nav_click"):
        parts.append("$GH click %d %d" % tuple(acfg["nav_click"]))
        parts.append("sleep 0.5")
    parts.append("PID=$(pgrep -x %s | head -1)" % acfg["process"])
    parts.append('say "pid=$PID"')
    parts.append('if [ -z "$PID" ]; then say "status=app_not_running"; exit 0; fi')
    # 開始前に欄が空であることを確認 (空でなければ何も触らない)
    parts.append("$GH click %d %d" % tuple(acfg["input_click"]))
    parts.append("sleep 0.4")
    parts.append('SENT="GF_SENT_P_20d9"')
    parts.append('printf %s "$SENT" | pbcopy')
    parts.append("$KH keyf 0 cmd pid $PID")
    parts.append("sleep 0.25")
    parts.append("$KH keyf 8 cmd pid $PID")
    parts.append("sleep 0.3")
    parts.append("PB=$(pbpaste)")
    parts.append('if [ "$PB" != "$SENT" ]; then')
    parts.append('  printf %s "$PB" > ~/muse-tools/gf-out/__NAME__-leftover.txt')
    parts.append('  say "status=anomaly_leftover"')
    parts.append("  exit 0")
    parts.append("fi")
    parts.append('PROBE="%s"' % probe_text)
    parts.append('CANDS="%s"' % flat)
    parts.append("set -- $CANDS")
    parts.append("IDX=0; TRIED=0; HIT=-1; HX=; HY=")
    parts.append("while [ $# -ge 2 ]; do")
    parts.append("  CX=$1; CY=$2; shift 2")
    parts.append("  TRIED=$((TRIED+1))")
    parts.append("  $GH click $CX $CY")
    parts.append("  sleep 0.35")
    parts.append('  $GH type "$PROBE"')
    parts.append("  sleep 0.25")
    parts.append('  printf %s "$SENT" | pbcopy')
    parts.append("  $KH keyf 0 cmd pid $PID")
    parts.append("  sleep 0.2")
    parts.append("  $KH keyf 8 cmd pid $PID")
    parts.append("  sleep 0.25")
    parts.append("  PB=$(pbpaste)")
    parts.append("  $KH keyf 0 cmd pid $PID")
    parts.append("  sleep 0.15")
    parts.append("  $KH keyf 51 - pid $PID")
    parts.append("  sleep 0.2")
    parts.append('  if [ "$PB" = "$PROBE" ]; then HIT=$IDX; HX=$CX; HY=$CY; break; fi')
    parts.append("  IDX=$((IDX+1))")
    parts.append("done")
    parts.append('say "probe_tried=$TRIED"')
    parts.append('say "probe_hit=$HIT"')
    parts.append('if [ $HIT -ge 0 ]; then say "probe_point=$HX $HY"; fi')
    # 終了時にも欄が空であることをセンチネルで確認する
    parts.append('printf %s "$SENT" | pbcopy')
    parts.append("$KH keyf 0 cmd pid $PID")
    parts.append("sleep 0.25")
    parts.append("$KH keyf 8 cmd pid $PID")
    parts.append("sleep 0.3")
    parts.append("PB=$(pbpaste)")
    parts.append(
        'if [ "$PB" = "$SENT" ]; then say "field_empty_after=yes"; '
        'else say "field_empty_after=no"; fi'
    )
    parts.append(
        'if [ $HIT -ge 0 ]; then say "status=ok"; else say "status=probe_no_hit"; fi'
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
    """検閲フェーズ: ウィンドウ特定 → 撮影 → OCR。撮影はこの 1 回だけ。

    撮影した PNG は base64 化したコピーも作り、呼び出し側が求めた場合に
    テキスト経由で VM 側へ回収できるようにする (送信後スクショの受け渡し用)。
    """
    parts = [HDR]
    parts.append(
        "rm -f ~/muse-tools/gf-out/__NAME__-ocr.txt "
        "~/muse-tools/gf-out/__NAME__-png.b64"
    )
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
        "base64 -i ~/muse-tools/gf-out/__NAME__.png "
        "> ~/muse-tools/gf-out/__NAME__-png.b64"
    )
    parts.append(
        "swift ~/muse-tools/ocr.swift ~/muse-tools/gf-out/__NAME__.png "
        "> ~/muse-tools/gf-out/__NAME__-ocr.txt 2>&1"
    )
    parts.append('say "status=ok"')
    return ("\n".join(parts) + "\n").replace("__NAME__", name)


def build_capture_script(name, app, acfg):
    """撮影だけのフェーズ: ウィンドウ特定 → 撮影 → base64 化。

    単独 send の送信後スクショ用 (send には検閲フェーズが無いため)。
    OCR はしない。
    """
    parts = [HDR]
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
        "base64 -i ~/muse-tools/gf-out/__NAME__.png "
        "> ~/muse-tools/gf-out/__NAME__-png.b64"
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


def run_verify_checks(app, acfg, ocr_text, win, expected_text, mode, tun=None):
    """verify の必須チェック一式。戻り値は checks のリスト。

    本文の照合は「アンカー方式」: 期待文の先頭行に最もよく一致する OCR 行を
    アンカーとし、その周辺の帯を対象領域とする。入力欄は行数に応じて
    上方向に伸びるため、固定オフセットでは領域を決めない。
    一致しきい値は tunables (tun) から取る (変数化。既定値は現行値)。
    """
    tun = tun or DEFAULT_TUNABLES
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
    strong = [it for r, it in scored if r >= tun["strong_threshold"]]
    anchor = (
        max(strong, key=lambda it: it["y"])
        if strong
        else max(scored, key=lambda t: t[0])[1]
    )
    first_ok = best_r >= tun["ocr_threshold"] and norm(lines[0]) == norm(
        FIRST_LINE[app]
    )
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
        ok = r >= (
            tun["short_line_threshold"]
            if len(norm(ln)) <= tun["short_line_max_len"]
            else tun["long_line_threshold"]
        )
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
        leak = (
            any(best_match(ln, below)[1] >= tun["leak_threshold"] for ln in lines)
            if below
            else False
        )
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


# --- 異常分類: 座標・形状の疑いは needs_calibration --------------------------------
# 高速パスの停止理由のうち「座標・形状が疑わしいもの」は、人の慎重プロセス
# ではなく careful (慎重モード) での再学習に回す。学習対象にするのは
# 座標系のチェックだけで落ちた場合と、フレームのドリフトが大きい場合。
# 宛先違い・欄の残り・構造エラー・承認・接続は学習対象にしない。

CALIBRATION_ERRORS = {
    "pre_send_failed_stop",
    "pre_send_failed_retry_exhausted",
    "field_readback_failed",
    None,
}
GEOMETRY_CHECKS = {"field_state", "body_match", "readback_match"}
HARD_BLOCKER_CHECKS = {
    "connect",
    "approval",
    "recipient",
    "field_empty_before",
    "structure_codeblock",
    "ocr_readable",
    "verify_capture",
}


def classify_needs_calibration(error, checks, acfg, last_frame, tun=None):
    """停止理由を分類し、座標・形状の疑いなら calibration チェックを返す。

    戻り値: {"name": "calibration", "ok": False, "evidence": ...} または None。
    呼び出し側は、返り値があれば error を "needs_calibration" に差し替え、
    このチェックを checks へ追加する。
    """
    tun = tun or DEFAULT_TUNABLES
    if error not in CALIBRATION_ERRORS:
        return None
    failed = [c for c in checks if not c["ok"]]
    if any(c["name"] in HARD_BLOCKER_CHECKS for c in failed):
        return None
    reasons = []
    geo_names = []
    for c in failed:
        n = c["name"]
        if n in GEOMETRY_CHECKS:
            geo_names.append(n)
        elif n == "input_status" and "fail_readback_empty" in c["evidence"]:
            reasons.append("入力しても欄が空のまま (クリック位置が欄を外した疑い)")
        elif n == "field_readback" and "field_empty" in c["evidence"]:
            reasons.append("欄の読み戻しが空 (クリック位置が欄を外した疑い)")
        else:
            # 座標系以外の失敗 (1 行目・汎用の入力失敗・後片付け失敗等) が
            # 混ざる場合は学習対象にしない (「座標系チェックのみ」を厳密化)
            return None
    if geo_names:
        reasons.append(
            "座標系チェックのみ不合格 (%s)" % "/".join(sorted(set(geo_names)))
        )
    tol = int(
        ((acfg or {}).get("careful") or {}).get(
            "frame_tolerance", CAREFUL_BASE_DEFAULTS["frame_tolerance"]
        )
    )
    drift = frame_drift((acfg or {}).get("learned_frame"), last_frame)
    if drift is not None and drift > tol:
        reasons.append(
            "ウィンドウフレームが学習値から %dpx ずれた (許容差 %dpx)" % (drift, tol)
        )
    if not reasons:
        return None
    return {
        "name": "calibration",
        "ok": False,
        "evidence": "座標・形状の疑い: %s → careful モードでの再学習を推奨"
        % "; ".join(reasons),
    }


def run_post(r, text, max_retries=MAX_PRE_SEND_RETRIES, on_sent=None):
    """post フロー: 送信前 (入力 + 欄検閲) → 送信 → 送信後検閲。

    送信前が「本文の不一致」だけで落ちた場合に限り、欄を全消去して
    再入力する高速リトライを最大 max_retries 回まで行う。それ以外の
    異常はリトライせず即停止する。送信後検閲 NG は送り直さない。
    on_sent は送信キーが成功した直後に 1 回だけ呼ばれる (承認の消費用)。
    承認ゲート自体は呼び出し側 (main) が開始前に済ませる。

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
    if on_sent is not None:
        on_sent()
    time.sleep(getattr(r, "post_send_wait", DEFAULT_TUNABLES["post_send_wait"]))
    if not r.verify(text, "bubble", fetch_png=True):
        return False, "post_send_verify_failed", info
    return True, None, info


# --- フェーズ実行 ---------------------------------------------------------------


class Runner:
    def __init__(self, cfg, app):
        self.cfg = cfg
        self.mac = Mac(cfg)
        self.app = app
        self.acfg = cfg["apps"][app]
        self.tunables = tunables(cfg)
        self.post_send_wait = float(self.tunables["post_send_wait"])
        self.steps = []
        self.checks = []
        self.counter = 0
        self.last_png_bytes = None
        self.last_frame = None
        self.careful_screenshot = None

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
            rc, out, _ = self.mac.run("echo GF_OK", timeout=self.mac.connect_timeout)
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

    def readback_field(self):
        """入力欄の現在の文面を読み戻す (入力・消去はしない)。

        成功したら文面、読めなければ None を返し、結果を checks に積む。
        """
        name = self._name("readback")
        res = self._timed(
            "readback",
            lambda: self.mac.run_queue(
                build_readback_script(name, self.app, self.acfg),
                name,
                extras=("-field.txt",),
            ),
        )
        st = res.get("status", "no_result")
        if st != "ok":
            self.checks.append(
                {
                    "name": "field_readback",
                    "ok": False,
                    "evidence": "欄の読み戻し不可: status=%s" % st,
                }
            )
            return None
        txt = res.get("_files", {}).get("-field.txt", "")
        self.checks.append(
            {
                "name": "field_readback",
                "ok": True,
                "evidence": "欄の読み戻し取得 (%d 字)" % len(txt.strip()),
            }
        )
        return txt

    def check_approval(self, text):
        """承認ゲートの照合。結果を checks に積み、(ok, reason) を返す。"""
        ok, reason, rec = check_approval(self.app, self.acfg, text)
        if ok:
            ev = "承認記録あり (id=%s, 期限 %s)" % (
                rec["text_sha256"][:12],
                rec["expires_at"],
            )
        else:
            ev = "承認ゲート停止: %s" % APPROVAL_REASON_JA.get(reason, reason)
        self.checks.append({"name": "approval", "ok": ok, "evidence": ev})
        return ok, reason

    def consume_approval(self, text):
        """送信成功後に承認記録を使用済みにし、二重送信を防ぐ。"""
        done = consume_approval(self.app, self.acfg, text)
        self.checks.append(
            {
                "name": "approval_consumed",
                "ok": done,
                "evidence": (
                    "承認記録を使用済みにした" if done else "承認記録の消費に失敗"
                ),
            }
        )
        return done

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

    def verify(self, text, mode, fetch_png=False):
        holder = {}

        def f():
            name = self._name("verify")
            extras = ["-ocr.txt"]
            if fetch_png:
                extras.append("-png.b64")
            holder["res"] = self.mac.run_queue(
                build_verify_script(name, self.app, self.acfg),
                name,
                extras=tuple(extras),
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
        if fetch_png:
            self.last_png_bytes = decode_b64_bytes(
                res.get("_files", {}).get("-png.b64", "")
            )
        win = tuple(int(float(v)) for v in res.get("window", "0 0 0 0 0").split())
        if len(win) == 5:
            self.last_frame = win[1:5]
        ocr_text = res.get("_files", {}).get("-ocr.txt", "")
        vchecks = run_verify_checks(
            self.app, self.acfg, ocr_text, win, text, mode, tun=self.tunables
        )
        self.checks.extend(vchecks)
        return all(c["ok"] for c in vchecks)

    def capture_png(self):
        """対象ウィンドウを撮影だけし、PNG を last_png_bytes に取り込む。

        単独 send の送信後スクショ用。成功したら True。
        """
        name = self._name("capture")
        res = self._timed(
            "capture",
            lambda: self.mac.run_queue(
                build_capture_script(name, self.app, self.acfg),
                name,
                extras=("-png.b64",),
            ),
        )
        if res.get("status") != "ok":
            return False
        self.last_png_bytes = decode_b64_bytes(
            res.get("_files", {}).get("-png.b64", "")
        )
        return self.last_png_bytes is not None

    def store_post_send_screenshot(self):
        """送信成功後のキャプチャを VM 側の定位置へ保存する。

        last_png_bytes (post の送信後検閲で撮影したもの) があればそれを、
        無ければ capture_png() で撮り直して (単独 send 用) 保存する。
        戻り値: 保存先パス or None。結果は checks の 'screenshot' に残す。
        取得・保存に失敗しても例外は投げない (送信結果は成功のままにする)。
        """
        try:
            if not self.last_png_bytes:
                self.capture_png()
            if not self.last_png_bytes:
                self.checks.append(
                    {
                        "name": "screenshot",
                        "ok": False,
                        "evidence": "送信後キャプチャを取得できなかった",
                    }
                )
                return None
            path = save_screenshot_file(self.app, self.last_png_bytes)
            self.checks.append(
                {
                    "name": "screenshot",
                    "ok": True,
                    "evidence": "送信後キャプチャを保存: %s" % path,
                }
            )
            return path
        except Exception as e:  # noqa: BLE001
            self.checks.append(
                {
                    "name": "screenshot",
                    "ok": False,
                    "evidence": "スクショ保存に失敗: %s: %s" % (type(e).__name__, e),
                }
            )
            return None

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

    # --- 慎重モード (careful): 学習の経路 -------------------------------------
    # 高速パスが対応できないケースを段階的に解決し、学んだ座標・フレームを
    # config.local.json へ保存して、次回から正常系として処理できるように
    # する。送信キーは絶対に押さない (自己確認は test と同じ範囲まで)。

    def careful_capture(self, step):
        """ステップ証拠の撮影。成功したら (保存先パス, フレーム) を返す。

        失敗したら (None, None)。結果は checks の careful_capture に残す。
        """
        name = self._name("capture")
        res = self._timed(
            "capture",
            lambda: self.mac.run_queue(
                build_capture_script(name, self.app, self.acfg),
                name,
                extras=("-png.b64",),
            ),
        )
        if res.get("status") != "ok":
            self.checks.append(
                {
                    "name": "careful_capture",
                    "ok": False,
                    "evidence": "ステップ '%s' の撮影不可: status=%s"
                    % (step, res.get("status", "no_result")),
                }
            )
            return None, None
        win = tuple(int(float(v)) for v in res.get("window", "0 0 0 0 0").split())
        frame = win[1:5] if len(win) == 5 else None
        if frame:
            self.last_frame = frame
        png = decode_b64_bytes(res.get("_files", {}).get("-png.b64", ""))
        if png is None:
            self.checks.append(
                {
                    "name": "careful_capture",
                    "ok": False,
                    "evidence": "ステップ '%s' のキャプチャを取得できなかった" % step,
                }
            )
            return None, frame
        path = save_careful_capture(self.app, step, png)
        self.careful_screenshot = path
        self.checks.append(
            {
                "name": "careful_capture",
                "ok": True,
                "evidence": "ステップ '%s' の証拠を保存: %s (frame=%s)"
                % (step, path, frame),
            }
        )
        return path, frame

    def probe(self, candidates, ccfg):
        """候補点を一括プローブし、フォーカスが当たった点を返す。

        戻り値: dict (status / hit_index / point / tried / field_empty_after)。
        送信キーは送らない。結果は checks の probe に残す。
        """
        name = self._name("probe")
        res = self._timed(
            "probe",
            lambda: self.mac.run_queue(
                build_probe_script(
                    name, self.app, self.acfg, candidates, ccfg["probe_text"]
                ),
                name,
                extras=("-leftover.txt",),
            ),
        )
        st = res.get("status", "no_result")
        out = {
            "status": st,
            "hit_index": int(res.get("probe_hit", "-1")),
            "point": None,
            "tried": int(res.get("probe_tried", "0")),
            "field_empty_after": res.get("field_empty_after") == "yes",
        }
        if res.get("probe_point"):
            xy = res["probe_point"].split()
            if len(xy) == 2:
                out["point"] = (int(float(xy[0])), int(float(xy[1])))
        if st == "anomaly_leftover":
            left = res.get("_files", {}).get("-leftover.txt", "")
            ev = "欄に残りあり (何も触らず停止): %s" % left.strip()[:80]
        elif st == "ok" and out["point"] is not None:
            ev = "候補 %d 点中 %d 点目 %s でフォーカス確認" % (
                out["tried"],
                out["hit_index"] + 1,
                out["point"],
            )
        else:
            ev = "候補 %d 点すべてフォーカスせず (status=%s)" % (out["tried"], st)
        self.checks.append(
            {
                "name": "probe",
                "ok": st == "ok" and out["point"] is not None,
                "evidence": ev,
            }
        )
        self.checks.append(
            {
                "name": "probe_field_empty",
                "ok": out["field_empty_after"],
                "evidence": (
                    "プローブ後、入力欄は空 (センチネル確認)"
                    if out["field_empty_after"]
                    else "プローブ後、入力欄が空に戻っていない"
                ),
            }
        )
        return out

    def careful(self):
        """慎重モード本体。戻り値: (ok, learned, self_check_ok)。

        learned は {"item", "old", "new", "reason"} のリスト。学習した値は
        config.local.json へ保存 (バックアップ + 学習記録の追記) し、
        仕上げに固定文での自己確認 (入力 → 検閲 → 空に戻す) で正常系へ
        戻ったことを証明する。
        """
        learned = []
        ccfg = careful_cfg(self.app, self.acfg)
        tol = int(ccfg["frame_tolerance"])
        evidence_ok = True  # ステップ証拠が全て保存できたか (合否に連動させる)

        # 1) ウィンドウのフレーム取得 + 学習済みフレームとの比較
        win_path, frame = self.careful_capture("window")
        if frame is None:
            return False, learned, False
        evidence_ok = evidence_ok and win_path is not None
        old_frame = self.acfg.get("learned_frame")
        drift = frame_drift(old_frame, frame)
        self.checks.append(
            {
                "name": "frame_compare",
                "ok": True,
                "evidence": (
                    "学習済みフレームなし (今回を基準として記録)"
                    if old_frame is None
                    else "フレーム差 %s (許容差 %dpx): 学習 %s → 現在 %s"
                    % (drift, tol, old_frame, list(frame))
                ),
            }
        )

        # 2) 入力欄の探索 (現行座標 → グリッド候補の順)
        candidates = probe_candidates(frame, self.acfg, ccfg)
        self.checks.append(
            {
                "name": "probe_candidates",
                "ok": True,
                "evidence": "候補 %d 点を生成 (先頭は学習済み座標)" % len(candidates),
            }
        )
        res = self.probe(candidates, ccfg)
        if res["status"] != "ok" or res["point"] is None:
            self.careful_capture("probe-failed")
            return False, learned, False
        if not res["field_empty_after"]:
            return False, learned, False
        probe_path, _ = self.careful_capture("probe")
        evidence_ok = evidence_ok and probe_path is not None

        # 3) 学習の保存 (必要な場合のみ)
        hit = [int(res["point"][0]), int(res["point"][1])]
        old_click = [int(v) for v in self.acfg.get("input_click", [])]
        if hit != old_click:
            learned.append(
                {
                    "item": "input_click",
                    "old": old_click,
                    "new": hit,
                    "reason": "プローブでフォーカスが当たった候補 (候補 %d 点目)"
                    % (res["hit_index"] + 1),
                }
            )
        if old_frame is None or (drift is not None and drift > tol):
            learned.append(
                {
                    "item": "learned_frame",
                    "old": old_frame,
                    "new": [int(v) for v in frame],
                    "reason": (
                        "基準フレームの新規記録"
                        if old_frame is None
                        else "フレーム差 %dpx が許容差 %dpx を超過" % (drift, tol)
                    ),
                }
            )
        if learned:
            for entry in learned:
                self.acfg[entry["item"]] = entry["new"]
            backup = save_config(self.cfg)
            now_iso = datetime.now(JST).isoformat()
            for entry in learned:
                append_learned_case(
                    {
                        "at": now_iso,
                        "app": self.app,
                        "item": entry["item"],
                        "old": entry["old"],
                        "new": entry["new"],
                        "reason": entry["reason"],
                    }
                )
            self.checks.append(
                {
                    "name": "config_saved",
                    "ok": True,
                    "evidence": "学習を config.local.json へ保存 "
                    "(バックアップ: %s / 学習記録へ追記)" % os.path.basename(backup),
                }
            )
        else:
            self.checks.append(
                {
                    "name": "config_saved",
                    "ok": True,
                    "evidence": "現行の座標・フレームで問題なし (学習不要)",
                }
            )

        # 4) 自己確認 (送信なし): 固定文で 入力 → 検閲 → 空に戻す
        text = ccfg["self_check_text"]
        in_ok = self.input(text)
        v_ok = self.verify(text, "field") if in_ok else False
        c_ok = self.clear() if in_ok else False
        self_check_ok = bool(in_ok and v_ok and c_ok)
        self.checks.append(
            {
                "name": "self_check",
                "ok": self_check_ok,
                "evidence": (
                    "自己確認合格 (入力 → 検閲 → 空に戻す)"
                    if self_check_ok
                    else "自己確認 NG (入力 %s / 検閲 %s / 後片付け %s)"
                    % (in_ok, v_ok, c_ok)
                ),
            }
        )
        shot_path, _ = self.careful_capture("selfcheck")
        evidence_ok = evidence_ok and shot_path is not None
        return self_check_ok and evidence_ok, learned, self_check_ok


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
        "action",
        choices=[
            "approve",
            "input",
            "verify",
            "send",
            "clear",
            "post",
            "test",
            "careful",
        ],
    )
    ap.add_argument("--text")
    ap.add_argument("--text-file")
    ap.add_argument("--mode", choices=["field", "bubble"], default="field")
    ap.add_argument(
        "--expires",
        help="approve の有効期限 (ISO8601。オフセット無しは JST とみなす)",
    )
    args = ap.parse_args()

    cfg = load_config()
    t0 = time.time()

    # approve は Mac に接続しない (承認記録を作るだけ)。
    if args.action == "approve":
        text = read_text(args)
        ttl = timedelta(hours=float(tunables(cfg)["approval_ttl_hours"]))
        rec = create_approval(
            args.app, cfg["apps"][args.app], text, expires=args.expires, ttl=ttl
        )
        out = {
            "ok": True,
            "app": args.app,
            "action": "approve",
            "total_sec": round(time.time() - t0, 2),
            "approval": {
                "id": rec["text_sha256"][:12],
                "text_sha256": rec["text_sha256"],
                "app": rec["app"],
                "recipient": rec["recipient"],
                "created_at": rec["created_at"],
                "expires_at": rec["expires_at"],
                "used": rec["used"],
            },
            "error": None,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    r = Runner(cfg, args.app)
    ok = True
    error = None
    retry_info = None
    learned = None
    self_check = None
    try:
        if not r.connect():
            ok = False
            error = "connect_failed"
        elif args.action == "input":
            ok = r.input(read_text(args))
        elif args.action == "verify":
            ok = r.verify(read_text(args), args.mode)
        elif args.action == "careful":
            # 慎重モード: 学習の経路。送信キーは絶対に押さない。
            ok, learned, self_check_ok = r.careful()
            self_check = {"ok": self_check_ok}
            if not ok and error is None:
                error = "careful_failed"
        elif args.action == "send":
            # 承認ゲート: 欄の文面を読み戻し、その文面の承認があるときだけ送る。
            field_text = r.readback_field()
            if field_text is None:
                ok = False
                error = "field_readback_failed"
            else:
                gate_ok, _ = r.check_approval(field_text)
                if not gate_ok:
                    ok = False
                    error = "approval_missing"
                else:
                    ok = r.send()
                    if ok:
                        r.consume_approval(field_text)
        elif args.action == "clear":
            ok = r.clear()
        elif args.action == "post":
            text = read_text(args)
            # 承認ゲート: 接続後・入力前に照合する。落ちたら欄に一切触らない。
            gate_ok, _ = r.check_approval(text)
            if not gate_ok:
                ok = False
                error = "approval_missing"
            else:
                ok, error, retry_info = run_post(
                    r,
                    text,
                    max_retries=int(r.tunables["max_pre_send_retries"]),
                    on_sent=lambda: r.consume_approval(text),
                )
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

    # 異常分類: 座標・形状の疑いが強い停止は needs_calibration に差し替える
    # (careful での再学習に回す。宛先違い・残り・構造エラーは従来通り)。
    if not ok and args.action in ("post", "input", "send"):
        cal = classify_needs_calibration(
            error, r.checks, r.acfg, r.last_frame, tun=r.tunables
        )
        if cal is not None:
            error = "needs_calibration"
            r.checks.append(cal)

    # 送信成功時だけ、送信後キャプチャを定位置へ保存してパスを返す。
    # 失敗しても送信結果 (ok) は変えない (screenshot は null + checks に理由)。
    screenshot = None
    if ok and args.action in ("post", "send"):
        screenshot = r.store_post_send_screenshot()
    elif args.action == "careful":
        # 慎重モードでは代表キャプチャ (最後のステップ証拠) を返す。
        screenshot = r.careful_screenshot

    out = {
        "ok": ok,
        "app": args.app,
        "action": args.action,
        "total_sec": round(time.time() - t0, 2),
        "steps": r.steps,
        "checks": r.checks,
        "retry": retry_info,
        "screenshot": screenshot,
        "error": error,
    }
    if args.action == "careful":
        out["learned"] = learned if learned is not None else []
        out["self_check"] = self_check if self_check is not None else {"ok": False}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
