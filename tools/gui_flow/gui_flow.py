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

宛先は --to <宛先IDまたは名前> で指定できる (未指定なら config の
default_recipient)。宛先はアプリごとに config の recipients 辞書で持ち、
宛先固有の値 (照合用エイリアス・トークを開く座標) だけを辞書側に置く。
--to で辞書に無い宛先を指定して careful を呼ぶと、再学習フローに入る:
画面を撮影してトーク一覧からその宛先の行を探し、座標を再取得して
辞書へ新しい変数として登録する (登録できなければ何も登録せず停止)。

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
import struct
import subprocess
import sys
import time
import unicodedata
import zlib
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
    "talk_list_width_ratio": 0.32,  # トーク一覧とみなす横範囲 (窓幅比)。再学習と verify/post のヘッダ確認で共有。apps.<app> の同名キーでアプリ別に上書き可
    "relearn_header_max_y": 260,  # 「ヘッダ」とみなす縦範囲 (窓相対 px)。
    # 再学習と verify/post のヘッダ確認で共有するキー (新規キーは作らない)
    "relearn_header_prefix_max_extra": 2,  # ヘッダ確認で前方一致を
    # 許す余りの最大文字数。再学習と verify/post のヘッダ確認で共有する
    # キー (新規キーは作らない)。ヘッダ名の右のアイコンが OCR で名前の
    # 後ろに連結される誤読 (例「<名前>4呼」) を吸収するための緩和で、
    # 完全一致しない場合に限り「指定名で始まり余りがこの文字数以内」を
    # 一致とする
    "talk_row_merge_px": 12,  # 行判定: 同じ行とみなす縦間隔と、横に連続
    # した文字列とみなす横間隔 (px)。再学習と verify/post のヘッダ確認
    # の run 化で共有する
    "header_check_attempts": 3,  # ヘッダ宛先判定 (複数回判定) の撮影
    # 回数上限。check_recipient_header と verify の recipient チェック
    # で共有する
    "header_check_interval_sec": 0.5,  # ヘッダ複数回判定で、撮影と
    # 撮影の間に空ける間隔 (秒)。同上共有
    "nav_scrutiny_row_tolerance_px": 36,  # nav 精査: 保存済み nav_click と
    # 宛先本人の行を「同じ行」とみなす縦座標差の許容差 (px)。行間隔
    # (約 72px) の半分で、隣の行は別人の行として検出できる値
    # --- 検閲幾何 (C-034 でコード内の直書きから移した値。既定値と
    # 計算方式は移行前と同一) ---
    "verify_anchor_top_offset": 45,  # run_verify_checks: アンカー行の
    # 上端オフセット (アンカー y から上に取る px)
    "verify_anchor_bottom_offset": 60,  # 同: アンカー帯の下端の基本
    # オフセット (アンカー y から下に取る px)
    "verify_anchor_line_step": 30,  # 同: 期待文 1 行ごとに下端へ加える px
    "verify_leak_band_offset": 10,  # 同: 送信後検閲で「欄付近の帯」と
    # みなす下端からの余白 (px)。この帯より下の行を残留判定の対象にする
    "verify_field_bottom_margin": 110,  # 同: 窓下端からこの px 以内を
    # 欄付近とみなし、残留判定の対象に含める
    "ocr_row_granularity_px": 8,  # parse_ocr: 行ソートで y を丸める粒度 (px)
    "png_diff_pixel_threshold": 24,  # png_diff_ratio: 輝度加重した画素差
    # がこの値を超えた画素を「大きく変わった」と数える (既定引数の値)
    "png_luminance_weights": [30, 59, 11],  # _png_gray_rows: RGB → 輝度
    # の加重 (百分率、合計 100 で割る)。R < G < B の順に効きにくい
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
    # 想定外の覆い (ポップアップ等) への汎用解消。個別の文言・形には
    # 依存せず、Esc + 窓の上部帯への外側クリックだけで解消を試みる。
    "dismiss_retries": 1,  # 解消 → 再プローブの試行回数
    "dismiss_click_x_ratio": 0.5,  # 外側クリックの横位置 (フレーム幅比)
    "dismiss_click_y_offset": 16,  # 外側クリックの縦位置 (フレーム上端から px)
    "dismiss_thumb_width": 160,  # 再確認用サムネイルの幅 (px)
    "dismiss_change_threshold": 0.005,  # 「画面が変わった」とみなす差分比率
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
    stamp = now.strftime("%Y%m%d-%H%M%S")
    backup = "%s.bak-%s" % (path, stamp)
    if os.path.exists(backup):
        # バックアップ名は秒単位のため、同秒に複数回保存すると先行する
        # バックアップ (登録前の状態等) を上書きしてしまう。ミリ秒と
        # 連番を足して一意にする。
        backup = "%s.bak-%s-%03d" % (path, stamp, now.microsecond // 1000)
        n = 1
        while os.path.exists(backup):
            n += 1
            backup = "%s.bak-%s-%03d-%d" % (
                path,
                stamp,
                now.microsecond // 1000,
                n,
            )
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


# --- 宛先の変数化 (recipients 辞書) ------------------------------------------
# 宛先はアプリごとに config の "recipients" 辞書で持つ。キー = 宛先 ID、
# 値 = その宛先固有の値だけ (aliases = 照合用エイリアス、nav_click =
# その宛先のトークを開くための座標)。ウィンドウ全体で共通の値
# (フレーム・入力欄座標など) はアプリ側に残す。"default_recipient" が
# 未指定 (--to なし) のときの宛先 ID。
# 旧形式 (アプリ直下の recipient_aliases / nav_click) の config は、
# 既定宛先 1 件だけの辞書として読み替える (後方互換)。


def recipients_of(acfg):
    """アプリ設定から (宛先辞書, 既定宛先 ID) を返す。"""
    recips = (acfg or {}).get("recipients")
    if recips:
        default = (acfg or {}).get("default_recipient")
        if default not in recips:
            default = next(iter(recips))
        return recips, default
    aliases = list((acfg or {}).get("recipient_aliases") or [])
    if not aliases:
        return {}, None
    entry = {"aliases": aliases}
    if (acfg or {}).get("nav_click"):
        entry["nav_click"] = list(acfg["nav_click"])
    return {aliases[0]: entry}, aliases[0]


def find_recipient(acfg, to):
    """--to の指定を宛先辞書から解決する。

    戻り値: (宛先 ID, 宛先エントリ)。to が None / 空なら既定宛先。
    辞書に無い宛先なら (None, None)。ID の完全一致 → ID・エイリアスの
    正規化一致、の順で探す。
    """
    recips, default = recipients_of(acfg)
    if to is None or not str(to).strip():
        if default is None:
            return None, None
        return default, recips[default]
    t = str(to).strip()
    if t in recips:
        return t, recips[t]
    nt = norm(t)
    for rid, entry in recips.items():
        if norm(rid) == nt:
            return rid, entry
        if any(norm(a) == nt for a in entry.get("aliases", [])):
            return rid, entry
    return None, None


def effective_acfg(acfg, entry):
    """宛先エントリを重ねた「実効アプリ設定」を返す。

    アプリ側の共通値 (フレーム・入力欄座標・careful 等) を土台に、
    宛先固有の値だけを上書きする。aliases は従来の recipient_aliases
    として渡す (検閲・承認ゲートはこの形で宛先を見る)。
    """
    eff = {
        k: v
        for k, v in (acfg or {}).items()
        if k not in ("recipients", "default_recipient")
    }
    for k, v in (entry or {}).items():
        if k == "aliases":
            eff["recipient_aliases"] = list(v)
        else:
            eff[k] = v
    return eff


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


def _png_gray_rows(data):
    """PNG (8bit・RGB/RGBA・非インターレース) をグレースケール行列にする。

    対応外の形式・破損データでは None。覆い解消前後の画面差分を取る
    ための最小実装 (サムネイル前提)。
    """
    if not data or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    try:
        pos = 8
        idat = b""
        w = h = None
        bpp = 0
        while pos + 8 <= len(data):
            size = struct.unpack(">I", data[pos : pos + 4])[0]
            ctype = data[pos + 4 : pos + 8]
            chunk = data[pos + 8 : pos + 8 + size]
            if ctype == b"IHDR":
                w, h, depth, color, _comp, _filt, inter = struct.unpack(
                    ">IIBBBBB", chunk
                )
                if depth != 8 or inter != 0 or color not in (2, 6):
                    return None
                bpp = 3 if color == 2 else 4
            elif ctype == b"IDAT":
                idat += chunk
            pos += 12 + size
        if w is None or not idat:
            return None
        raw = zlib.decompress(idat)
        stride = w * bpp
        _wr, _wg, _wb = DEFAULT_TUNABLES["png_luminance_weights"]
        rows = []
        prev = bytearray(stride)
        i = 0
        for _y in range(h):
            filt = raw[i]
            i += 1
            line = bytearray(raw[i : i + stride])
            i += stride
            if filt == 1:
                for x in range(bpp, stride):
                    line[x] = (line[x] + line[x - bpp]) & 0xFF
            elif filt == 2:
                for x in range(stride):
                    line[x] = (line[x] + prev[x]) & 0xFF
            elif filt == 3:
                for x in range(stride):
                    left = line[x - bpp] if x >= bpp else 0
                    line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
            elif filt == 4:
                for x in range(stride):
                    a = line[x - bpp] if x >= bpp else 0
                    b = prev[x]
                    c = prev[x - bpp] if x >= bpp else 0
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    line[x] = (line[x] + pr) & 0xFF
            elif filt != 0:
                return None
            rows.append(
                [
                    (line[x] * _wr + line[x + 1] * _wg + line[x + 2] * _wb) // 100
                    for x in range(0, stride, bpp)
                ]
            )
            prev = line
        return w, h, rows
    except Exception:  # noqa: BLE001
        return None


def png_diff_ratio(
    data_a, data_b, pixel_threshold=DEFAULT_TUNABLES["png_diff_pixel_threshold"]
):
    """2 枚の PNG で大きく変わったピクセルの比率 (0.0–1.0)。

    サイズが違えば 1.0、判定不能なら None。覆いの解消前後で「画面が
    クリーンになったか」を確認するために使う。
    """
    a = _png_gray_rows(data_a)
    b = _png_gray_rows(data_b)
    if a is None or b is None:
        return None
    wa, ha, ra = a
    wb, hb, rb = b
    if (wa, ha) != (wb, hb):
        return 1.0
    total = wa * ha
    if total == 0:
        return None
    diff = 0
    for y in range(ha):
        row_a, row_b = ra[y], rb[y]
        for x in range(wa):
            if abs(row_a[x] - row_b[x]) > pixel_threshold:
                diff += 1
    return diff / total


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
    parts.append("IDX=0; TRIED=0; HIT=-1; HX=; HY=")
    # 候補は関数 try_one へのリテラル引数呼び出しで 1 点ずつ試す。
    # 変数の単語分割 (set -- $CANDS) はキュー実行シェル (zsh) では効かず
    # (分割されないため $# が 1 になりループが空転する)、使わない。
    parts.append("try_one() {")
    parts.append("  if [ $HIT -ge 0 ]; then return 0; fi")
    parts.append("  CX=$1; CY=$2")
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
    parts.append('  if [ "$PB" = "$PROBE" ]; then HIT=$IDX; HX=$CX; HY=$CY; fi')
    parts.append("  IDX=$((IDX+1))")
    parts.append("}")
    for x, y in candidates:
        parts.append("try_one %d %d" % (int(x), int(y)))
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


def build_dismiss_script(name, app, acfg, frame, ccfg):
    """覆い解消フェーズ (careful 専用): 想定外の何かが画面を覆っている
    疑いがあるとき、汎用の方法だけで解消を試みる。

    個別のポップアップの文言・形・専用座標には依存しない: Esc キーと、
    ウィンドウ自身の上部帯 (フレーム相対のニュートラルな位置) への
    クリック (外側クリック) だけを使う。テキスト入力も送信キーも
    一切送らない。解消できたかの判定は呼び出し側の再プローブで行う。
    """
    fx, fy, fw, _ = (int(v) for v in frame)
    nx = int(fx + fw * float(ccfg["dismiss_click_x_ratio"]))
    ny = int(fy + int(ccfg["dismiss_click_y_offset"]))
    parts = [HDR]
    parts.append('open -a "%s"' % acfg["app_name"])
    parts.append("sleep 0.5")
    parts.append("PID=$(pgrep -x %s | head -1)" % acfg["process"])
    parts.append('say "pid=$PID"')
    parts.append('if [ -z "$PID" ]; then say "status=app_not_running"; exit 0; fi')
    parts.append("$KH keyf 53 - pid $PID")  # Esc: メニュー/ポップオーバーを閉じる
    parts.append("sleep 0.4")
    parts.append("$GH click %d %d" % (nx, ny))  # 外側クリック (窓の上部帯)
    parts.append("sleep 0.4")
    parts.append("$KH keyf 53 - pid $PID")
    parts.append("sleep 0.3")
    parts.append('say "status=ok"')
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


def _script_number(value):
    """スクリプト本文へ埋める数値リテラルの整形 (260.0 -> "260")。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    return ("%f" % f).rstrip("0").rstrip(".")


def build_verify_script(name, app, acfg, tun=None):
    """検閲フェーズ: ウィンドウ特定 → 撮影 → OCR。撮影はこの 1 回だけ。

    撮影した PNG は base64 化したコピーも作り、呼び出し側が求めた場合に
    テキスト経由で VM 側へ回収できるようにする (送信後スクショの受け渡し用)。
    全体 OCR (-ocr.txt) に加えて、ヘッダ帯の切り出し OCR
    (-header-ocr.txt) も作る: 撮影 PNG からヘッダ帯 (-header.png) を
    sips で切り出し、その切り出し画像だけを ocr.swift で読む。切り出し
    範囲は現行のヘッダ領域定義の読み替えで、x は PNG 幅 ×
    talk_list_width_ratio から右、y は窓高さに対する
    header_verify_crop_max_y (無ければ relearn_header_max_y) 分
    (窓相対 pt) の帯。ratio は acfg の talk_list_width_ratio 優先
    (無ければ tun)、max_y の解決順は acfg の
    header_verify_crop_max_y → tun の同キー → acfg の
    relearn_header_max_y → tun の同キー → 既定値の順。
    数値はスクリプト本文にリテラルで現れる。
    算出入力が不正なら切り出しをスキップし、-header-ocr.txt は
    空のままにする (fail-safe: 呼び出し側は UNREADABLE として扱う)。
    """
    if tun is None:
        tun = DEFAULT_TUNABLES
    ratio_v = (acfg or {}).get("talk_list_width_ratio")
    if ratio_v is None:
        ratio_v = tun.get(
            "talk_list_width_ratio", DEFAULT_TUNABLES["talk_list_width_ratio"]
        )
    max_y_v = (acfg or {}).get("header_verify_crop_max_y")
    if max_y_v is None:
        max_y_v = tun.get("header_verify_crop_max_y")
    if max_y_v is None:
        max_y_v = (acfg or {}).get("relearn_header_max_y")
    if max_y_v is None:
        max_y_v = tun.get(
            "relearn_header_max_y", DEFAULT_TUNABLES["relearn_header_max_y"]
        )
    ratio_lit = _script_number(ratio_v)
    max_y_lit = _script_number(max_y_v)
    parts = [HDR]
    parts.append(
        "rm -f ~/muse-tools/gf-out/__NAME__-ocr.txt "
        "~/muse-tools/gf-out/__NAME__-png.b64 "
        "~/muse-tools/gf-out/__NAME__-header-ocr.txt "
        "~/muse-tools/gf-out/__NAME__-header.png"
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
    # ヘッダ帯の切り出し OCR (全体 OCR の後)。フレーム幅・高さは
    # $WIN の第 4 / 5 フィールド (pt)、PNG 実寸は sips で取る (px)。
    # 切り出し矩形 (x0 / 幅 / 高さ) は awk で算出し、PNG 高でクランプ
    # する。入力が不正 (数値でない・0 以下等) なら CROP は空になり、
    # 切り出しをスキップして -header-ocr.txt は空のままになる。
    parts.append(": > ~/muse-tools/gf-out/__NAME__-header-ocr.txt")
    parts.append('FW=$(printf %s "$WIN" | cut -d" " -f4)')
    parts.append('FH=$(printf %s "$WIN" | cut -d" " -f5)')
    parts.append(
        "PW=$(sips -g pixelWidth ~/muse-tools/gf-out/__NAME__.png "
        "2>/dev/null | awk '/pixelWidth/ {print $2}')"
    )
    parts.append(
        "PH=$(sips -g pixelHeight ~/muse-tools/gf-out/__NAME__.png "
        "2>/dev/null | awk '/pixelHeight/ {print $2}')"
    )
    parts.append(
        'CROP=$(printf "%s %s %s %s" "$PW" "$PH" "$FW" "$FH" | '
        "awk -v ratio=" + ratio_lit + " -v max_y=" + max_y_lit + " '"
        "{ pw=$1; ph=$2; fw=$3; fh=$4; "
        "if (pw<=0 || ph<=0 || fw<=0 || fh<=0) exit; "
        "x0=int(pw*ratio+0.5); w=pw-x0; h=int(ph*max_y/fh+0.5); "
        "if (h>ph) h=ph; "
        "if (w<=0 || h<=0 || x0<0) exit; "
        'print x0" "w" "h }\')'
    )
    parts.append('if [ -n "$CROP" ]; then')
    parts.append('  X0=$(printf %s "$CROP" | cut -d" " -f1)')
    parts.append('  CW=$(printf %s "$CROP" | cut -d" " -f2)')
    parts.append('  CH=$(printf %s "$CROP" | cut -d" " -f3)')
    # sips の cropOffset は (y, x) の順・左上原点 (Mac で実測済み)。
    # ヘッダ帯は上端からなので y_px = 0。
    parts.append(
        "  sips -c $CH $CW --cropOffset 0 $X0 "
        "~/muse-tools/gf-out/__NAME__.png "
        "--out ~/muse-tools/gf-out/__NAME__-header.png >/dev/null 2>&1"
    )
    parts.append("  if [ -s ~/muse-tools/gf-out/__NAME__-header.png ]; then")
    parts.append(
        "    swift ~/muse-tools/ocr.swift "
        "~/muse-tools/gf-out/__NAME__-header.png "
        "> ~/muse-tools/gf-out/__NAME__-header-ocr.txt 2>&1"
    )
    parts.append("  fi")
    parts.append("fi")
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


def build_snapshot_script(name, app, acfg, width):
    """画面確認用の軽量撮影: ウィンドウ撮影 → サムネイル化 → base64。

    覆いの解消前後で画面が変わったか (クリーンになったか) を比べる
    ためのもの。OCR はしない。
    """
    parts = [HDR]
    parts.append('APP="%s"' % acfg["window_app"])
    parts.append("WIN=$($SC list | %s)" % AWK_SELECT)
    parts.append('if [ -z "$WIN" ]; then say "status=window_not_found"; exit 0; fi')
    parts.append('WID=$(printf %s "$WIN" | cut -d" " -f1)')
    parts.append("$SC cap $WID ~/muse-tools/gf-out/__NAME__.png")
    parts.append(
        "if [ ! -s ~/muse-tools/gf-out/__NAME__.png ]; then "
        'say "status=capture_failed"; exit 0; fi'
    )
    parts.append(
        "sips -Z %d ~/muse-tools/gf-out/__NAME__.png "
        "--out ~/muse-tools/gf-out/__NAME__-thumb.png >/dev/null 2>&1" % int(width)
    )
    parts.append(
        "if [ ! -s ~/muse-tools/gf-out/__NAME__-thumb.png ]; then "
        'say "status=thumb_failed"; exit 0; fi'
    )
    parts.append(
        "base64 -i ~/muse-tools/gf-out/__NAME__-thumb.png "
        "> ~/muse-tools/gf-out/__NAME__-thumb.b64"
    )
    parts.append('say "status=ok"')
    return ("\n".join(parts) + "\n").replace("__NAME__", name)


def build_click_script(name, app, acfg, point):
    """クリックだけのフェーズ: 前面化 → 指定点を 1 回クリック。

    再学習フローでトーク一覧の行を開くために使う。テキスト入力も
    送信キーも一切送らない。
    """
    parts = [HDR]
    parts.append('open -a "%s"' % acfg["app_name"])
    parts.append("sleep 0.8")
    parts.append("$GH click %d %d" % (int(point[0]), int(point[1])))
    parts.append("sleep 0.6")
    parts.append('say "clicked=%d %d"' % (int(point[0]), int(point[1])))
    parts.append('say "status=ok"')
    return ("\n".join(parts) + "\n").replace("__NAME__", name)


# --- OCR 解析と照合 ------------------------------------------------------------

OCR_RE = re.compile(
    r"^TEXT \[x=(-?[\d.]+) y=(-?[\d.]+) w=([\d.]+) h=([\d.]+) conf=([\d.]+)\] (.*)$"
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
    items.sort(
        key=lambda d: (
            round(d["y"] / DEFAULT_TUNABLES["ocr_row_granularity_px"]),
            d["x"],
        )
    )
    return items


def norm(s):
    """照合用の正規化: NFKC (全角→半角) + 空白除去。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def ratio(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def header_name_match(text, target, prefix_max_extra=0):
    """宛先チェック: ヘッダ等で読めた文字列と宛先名の照合 (アプリ非依存)。

    戻り値: (一致方式, 余り文字数)。一致すれば ("exact", 0) または
    ("prefix", n)、一致しなければ (None, 0)。
    - exact: 正規化後に完全一致。
    - prefix: 正規化後に「宛先名で始まり、余りが prefix_max_extra 文字
      以内」。ヘッダ名の右にあるアイコンが OCR で名前の後ろに連結される
      誤読 (例「<名前>4呼」) を吸収するための方式で、呼び出し側が
      prefix_max_extra=0 (既定) にすれば従来の完全一致のみになる。
    再学習と verify / post のヘッダ確認が共有する。この関数の戻り値
    (方式と余り) をそのまま証跡に使う。
    """
    t = norm(text)
    g = norm(target)
    if not t or not g:
        return None, 0
    if t == g:
        return "exact", 0
    extra = len(t) - len(g)
    if prefix_max_extra > 0 and t.startswith(g) and 0 < extra <= prefix_max_extra:
        return "prefix", extra
    return None, 0


def text_runs(items, merge_px):
    """OCR 項目を「行の中で横に連続した文字列」にまとめる (共有ヘルパー)。

    縦位置が近い (merge_px 以内) 項目を同じ行とし、行の中では横の
    間隔が近い項目同士を連結する。名前と人数表記が別項目に割れて
    返ってきた場合は 1 つの文字列になり、行の右端にある時刻のような
    離れた項目は別の文字列になる。
    戻り値: [(中心点 (窓相対), 連結した文字列), ...]。中心点は文字列の
    先頭項目の中心。並びは読み順 (行は上から、行内は左から)。
    再学習・verify / post のヘッダ確認で共有する。
    """
    merge_px = float(merge_px)
    rows = []
    for it in sorted(items, key=lambda i: (i["y"], i["x"])):
        if rows and abs(it["y"] - rows[-1]["y"]) <= merge_px:
            rows[-1]["items"].append(it)
        else:
            rows.append({"y": it["y"], "items": [it]})
    runs = []
    for row in rows:
        cur = None
        for it in sorted(row["items"], key=lambda i: i["x"]):
            if cur is not None and it["x"] - cur["right"] <= merge_px:
                cur["text"] += it["text"]
                cur["right"] = max(cur["right"], it["x"] + it["w"])
                continue
            if cur is not None:
                runs.append(cur)
            cur = {
                "text": it["text"],
                "right": it["x"] + it["w"],
                "point": (
                    int(it["x"] + it["w"] / 2),
                    int(it["y"] + it["h"] / 2),
                ),
            }
        if cur is not None:
            runs.append(cur)
    return [(r["point"], r["text"]) for r in runs]


def header_region_runs(items, frame_width, list_ratio, max_y, merge_px):
    """ヘッダ領域の項目だけを行単位の run にまとめて返す (共有ヘルパー)。

    ヘッダ領域 = 項目の中心 x (x + w/2) がフレーム幅 × 一覧幅比を
    超え、かつ y が上限未満の領域。トーク一覧 (左ペイン) に写った
    宛先名を拾わないための領域スコープで、再学習と verify / post の
    ヘッダ確認で共有する。
    戻り値: [(中心点, run 文字列), ...] (読み順)。
    """
    limit_cx = float(frame_width) * float(list_ratio)
    header_items = [
        it
        for it in items
        if it["y"] < float(max_y)
        and it["x"] + it["w"] / 2 > limit_cx
        and norm(it["text"])
    ]
    return text_runs(header_items, merge_px)


def header_alias_match(runs, aliases, prefix_max_extra=0):
    """ヘッダ run 群と宛先エイリアス群の照合 (共有ヘルパー)。

    各 run (読み順) × 各エイリアスに header_name_match を適用する。
    exact があれば即座にそれを返し、無ければ最初に見つかった prefix
    一致を返す。どちらも無ければ (None, 0, None, None)。
    戻り値: (一致方式, 余り文字数, run 文字列, 一致したエイリアス)。
    """
    prefix_hit = None
    for _point, text in runs:
        for alias in aliases:
            kind, extra = header_name_match(
                text, alias, prefix_max_extra=prefix_max_extra
            )
            if kind == "exact":
                return "exact", 0, text, alias
            if kind == "prefix" and prefix_hit is None:
                prefix_hit = ("prefix", extra, text, alias)
    if prefix_hit is not None:
        return prefix_hit
    return None, 0, None, None


# ヘッダ候補拡張の有効化キー (apps.<app> 配下の真偽値)。アプリ名や
# 文字列内容ではなく、この設定だけで拡張の有無を切り替える。
HEADER_CANDIDATE_EXPANSION_KEY = "header_candidate_expansion"


def _ocr_item_point(item):
    """OCR 項目 (dict) の中心点。座標が取れなければ (0, 0)。"""
    if isinstance(item, dict):
        try:
            return (
                int(item["x"] + item["w"] / 2),
                int(item["y"] + item["h"] / 2),
            )
        except (KeyError, TypeError, ValueError):
            return (0, 0)
    if isinstance(item, (tuple, list)) and len(item) == 2:
        return item[0]
    return (0, 0)


def _ocr_item_text(item):
    """OCR 項目 (dict / (中心点, 文字列)) から文字列を取り出す。"""
    if isinstance(item, dict):
        return item.get("text") or ""
    if isinstance(item, (tuple, list)) and len(item) == 2:
        return item[1] or ""
    return ""


def expand_header_candidates(runs, items=None):
    """ヘッダ判定の候補群を拡張する (候補拡張が有効なアプリ専用)。

    候補 = 従来の run 群 (先頭・そのままの順序) + 各 OCR 項目そのもの
    + 各 run / 項目の空白区切りの第 1 トークン。同じ文字列の候補は
    最初の 1 件だけを残すため、従来 run 由来の候補が常に優先される
    (証跡の run_text も従来 run 由来が優先される)。判定規則自体は
    変えず、返した候補群に既存の header_name_match (exact / prefix
    余り上限) をそのまま適用する想定。区切りも項目分割も無い文字列は
    第 1 トークンが文字列全体と同一になるため、新たな一致は生まれない。
    戻り値: [(中心点, 文字列), ...] (text_runs と同じ形)。
    """
    expanded = [(point, text) for point, text in (runs or [])]
    seen = {text for _point, text in expanded}
    item_candidates = []
    for item in items or []:
        text = _ocr_item_text(item)
        if not text:
            continue
        item_candidates.append((_ocr_item_point(item), text))
    for point, text in item_candidates:
        if text not in seen:
            expanded.append((point, text))
            seen.add(text)
    sources = [(point, text) for point, text in (runs or [])]
    sources.extend(item_candidates)
    for point, text in sources:
        parts = str(text).split()
        if len(parts) < 2:
            continue
        token = parts[0]
        if token and token not in seen:
            expanded.append((point, token))
            seen.add(token)
    return expanded


def formal_names_of_others(recips, recipient_id):
    """宛先以外の登録済み宛先の「正式名」リスト (OTHER 判定用・共有)。

    正式名 = recipients 辞書のキー (宛先名そのもの) と、各エントリの
    エイリアス先頭 (辞書キーと表記が異なる場合に画面に出る正式表記)。
    短いエイリアス (2 件目以降) は含めない: 短い名前だけが読めた場合に
    別人と断定しないため (その場合は UNREADABLE として扱う)。
    """
    names = []
    for rid, entry in (recips or {}).items():
        if rid == recipient_id:
            continue
        cands = [rid]
        aliases = list((entry or {}).get("aliases") or [])
        if aliases:
            cands.append(aliases[0])
        for cand in cands:
            if cand and cand not in names:
                names.append(cand)
    return names


def classify_header_attempt(runs, aliases, other_names, prefix_max_extra=0):
    """1 回分のヘッダ run 群を MATCH / OTHER / UNREADABLE に分類 (共有)。

    - MATCH: 宛先エイリアス (recipient_aliases) のいずれかに
      header_name_match (exact / 前方一致 + 余り上限) で一致
    - OTHER: 宛先以外の登録済み宛先の正式名に同じ規則で一致
    - UNREADABLE: どちらにも一致しない
    MATCH を先に判定する (宛先自身の名前に含まれる文字列で別宛先へ
    誤分類しないため)。戻り値: dict (classification / kind / extra /
    run_text / matched)。
    """
    kind, extra, run_text, matched = header_alias_match(
        runs, aliases, prefix_max_extra=prefix_max_extra
    )
    if kind is not None:
        return {
            "classification": "MATCH",
            "kind": kind,
            "extra": extra,
            "run_text": run_text,
            "matched": matched,
        }
    for name in other_names or []:
        k2, e2, rt2, _m = header_alias_match(
            runs, [name], prefix_max_extra=prefix_max_extra
        )
        if k2 is not None:
            return {
                "classification": "OTHER",
                "kind": k2,
                "extra": e2,
                "run_text": rt2,
                "matched": name,
            }
    return {
        "classification": "UNREADABLE",
        "kind": None,
        "extra": 0,
        "run_text": None,
        "matched": None,
    }


def header_attempts_evidence(attempts):
    """複数回判定の各回 (ヘッダ文字列と分類) を証跡文字列にする (共有)。"""
    parts = []
    for a in attempts:
        header = " / ".join(a.get("header") or []) or "(ヘッダ領域に文字列なし)"
        desc = a["classification"]
        if a.get("kind") == "exact":
            desc += " (exact)"
        elif a.get("kind") == "prefix":
            desc += " (prefix、余り%d文字)" % a.get("extra", 0)
        if a["classification"] == "OTHER" and a.get("matched"):
            desc += " 別宛先 '%s'" % a["matched"]
        parts.append("%d回目 [%s] -> %s" % (a["attempt"], header, desc))
    return " / ".join(parts)


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

    # 1) 宛先 (ヘッダ領域専用。画面全体では合格にしない)
    # ヘッダ領域 = 中心 x がフレーム幅 × 一覧幅比を超え、y が上限未満。
    # 一覧幅比は Runner.talk_list_width_ratio と同じ規則 (acfg の
    # talk_list_width_ratio 優先、無ければ tunables の既定 0.32)。
    # y 上限と前方一致の余り上限は再学習と共有のキー
    # (relearn_header_max_y / relearn_header_prefix_max_extra) を使う。
    # トーク一覧 (左ペイン) に写った宛先名では合格しない。
    aliases = list(acfg.get("recipient_aliases", []))
    ratio_v = (acfg or {}).get("talk_list_width_ratio")
    if ratio_v is None:
        ratio_v = tun.get(
            "talk_list_width_ratio", DEFAULT_TUNABLES["talk_list_width_ratio"]
        )
    header_max_y = float(
        tun.get("relearn_header_max_y", DEFAULT_TUNABLES["relearn_header_max_y"])
    )
    max_extra = int(
        tun.get(
            "relearn_header_prefix_max_extra",
            DEFAULT_TUNABLES["relearn_header_prefix_max_extra"],
        )
    )
    merge_px = float(
        tun.get("talk_row_merge_px", DEFAULT_TUNABLES["talk_row_merge_px"])
    )
    runs = header_region_runs(items, win[3], float(ratio_v), header_max_y, merge_px)
    actual_header = [text for _point, text in runs]
    kind, extra, run_text, matched_alias = header_alias_match(
        runs, aliases, prefix_max_extra=max_extra
    )
    if kind == "exact":
        evidence = "宛先 '%s' をヘッダで確認 (一致方式: exact / ヘッダ文字列: %s)" % (
            run_text,
            " / ".join(actual_header),
        )
    elif kind == "prefix":
        evidence = (
            "宛先 '%s' をヘッダで確認 (一致方式: prefix、余り%d文字 / "
            "ヘッダ文字列: %s)" % (matched_alias, extra, " / ".join(actual_header))
        )
    elif actual_header:
        evidence = "宛先 (%s) がヘッダに見当たらない (ヘッダ文字列: %s)" % (
            "/".join(aliases),
            " / ".join(actual_header),
        )
    else:
        evidence = "宛先 (%s) がヘッダに見当たらない (ヘッダ領域に文字列なし)" % (
            "/".join(aliases)
        )
    checks.append(
        {
            "name": "recipient",
            "ok": kind is not None,
            "evidence": evidence,
            # 宛先違いの報告用: 想定宛先と、ヘッダ領域で実際に読めた
            # run 文字列の全て
            "expected": list(aliases),
            "actual_header": actual_header,
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
    top = anchor["y"] - tun.get(
        "verify_anchor_top_offset",
        DEFAULT_TUNABLES["verify_anchor_top_offset"],
    )
    bottom = (
        anchor["y"]
        + tun.get(
            "verify_anchor_bottom_offset",
            DEFAULT_TUNABLES["verify_anchor_bottom_offset"],
        )
        + tun.get(
            "verify_anchor_line_step",
            DEFAULT_TUNABLES["verify_anchor_line_step"],
        )
        * len(lines)
    )
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
        leak_band_offset = tun.get(
            "verify_leak_band_offset",
            DEFAULT_TUNABLES["verify_leak_band_offset"],
        )
        field_bottom_margin = tun.get(
            "verify_field_bottom_margin",
            DEFAULT_TUNABLES["verify_field_bottom_margin"],
        )
        below = [
            it
            for it in items
            if it["y"] > bottom + leak_band_offset
            or it["y"] >= height - field_bottom_margin
        ]
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


def pre_send_recipient_error(r, record):
    """送信前検閲の宛先系失敗のエラー名を決める。

    record 側の recipient_error / error フィールド、無ければ Runner
    属性 (recipient_header_error 等) の順に
    "recipient_header_unreadable" / "wrong_recipient" の手がかりを
    探し、どちらも無ければ従来通り "wrong_recipient" を返す。
    「確認できず」を wrong_recipient と区別するための伝搬ヘルパー。
    """
    candidates = [
        (record or {}).get("recipient_error"),
        (record or {}).get("error"),
        getattr(r, "recipient_header_error", None),
        getattr(r, "recipient_error", None),
        getattr(r, "recipient_verify_error", None),
        getattr(r, "last_recipient_error", None),
    ]
    for cand in candidates:
        if cand in ("wrong_recipient", "recipient_header_unreadable"):
            return cand
    return "wrong_recipient"


def run_post(r, text, max_retries=MAX_PRE_SEND_RETRIES, on_sent=None):
    """post フロー: nav 精査 → 入力前ヘッダ確認 → 送信前 (入力 + 欄検閲)
    → 送信 → 送信後検閲。

    まず保存済み nav_click が宛先本人の行を指しているかを精査する
    (scrutinize_nav)。ここで落ちたらヘッダ確認以降は一切呼ばずに
    停止する。互換性のため getattr で取得し、メソッドを持たない
    軽量フェイクでは精査をスキップする (実 Runner には常時存在する)。
    次に入力前に、開いているトークのヘッダだけで宛先を確認する
    (check_recipient_header)。ここで落ちたら何も入力していないため、
    後片付けもせず即停止する。送信前が「本文の不一致」だけで落ちた
    場合に限り、欄を全消去して再入力する高速リトライを最大
    max_retries 回まで行う。それ以外の異常はリトライせず即停止する。
    送信前検閲で宛先違いが分かった場合 (入力済み) は、自分が入れた文
    を消して空に戻してから error=wrong_recipient で停止する (送信・
    再実行はしない)。送信前検閲の宛先が「確認できず」
    (recipient_header_unreadable) で落ちた場合も後片付けは同じで、
    返すエラー名だけを区別する (pre_send_recipient_error)。
    送信後検閲 NG は送り直さない。
    on_sent は送信キーが成功した直後に 1 回だけ呼ばれる (承認の消費用)。
    承認ゲート自体は呼び出し側 (main) が開始前に済ませる。

    戻り値: (ok, error, retry_info)
    retry_info = {"count": リトライ実施回数,
                  "attempts": [各失敗回の failed_checks と差分要約]}
    """
    scrutinize = getattr(r, "scrutinize_nav", None)
    if scrutinize is not None and not scrutinize():
        return (
            False,
            getattr(r, "nav_scrutiny_error", None) or "nav_scrutiny_failed",
            {
                "count": 0,
                "attempts": [],
            },
        )
    if not r.check_recipient_header():
        return (
            False,
            getattr(r, "recipient_header_error", None) or "wrong_recipient",
            {
                "count": 0,
                "attempts": [],
            },
        )
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
            if "recipient" in record["failed_checks"]:
                # 宛先違い / 確認できず: 送信キーは絶対に押さず、自分が
                # 入れた文を消して空に戻してから停止する (再実行は
                # しない)。後片付けは共通で、エラー名だけを
                # recipient_header_unreadable / wrong_recipient で区別する
                r.cleanup_wrong_recipient()
                return False, pre_send_recipient_error(r, record), info
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
    def __init__(self, cfg, app, to=None):
        self.cfg = cfg
        self.mac = Mac(cfg)
        self.app = app
        self.app_cfg = cfg["apps"][app]
        # 宛先の解決: --to 未指定なら既定宛先。辞書に無い宛先は
        # recipient_known=False となり、careful の再学習フロー以外は
        # 呼び出し側 (main) が unknown_recipient で停止する。
        self.to_name = str(to).strip() if to and str(to).strip() else None
        self.recipient_id, entry = find_recipient(self.app_cfg, self.to_name)
        self.recipient_known = entry is not None
        self.acfg = effective_acfg(self.app_cfg, entry)
        if self.recipient_id is not None:
            self.acfg["recipient_id"] = self.recipient_id
        self.relearn_error = None
        self.tunables = tunables(cfg)
        self.post_send_wait = float(self.tunables["post_send_wait"])
        self.steps = []
        self.checks = []
        self.counter = 0
        self.last_png_bytes = None
        self.last_frame = None
        self.careful_screenshot = None
        self.overlay_blocked = False
        self.wrong_recipient = None
        self.recipient_header_error = None
        self.nav_scrutiny_error = None
        # 直近の capture_header_runs が parse した切り出し OCR 項目。
        # 候補拡張 (有効なアプリのみ) が項目候補を作るために保持する。
        self._last_header_items = []

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

    def cleanup_wrong_recipient(self):
        """宛先違いの後片付け: 自分が入れた文だけを消して空に戻す。

        検閲で宛先不一致が分かった時点で、その会話の入力欄に入って
        いるのはこの run が入れた文だけ (input は欄が空でなければ
        進まないため)。それを消去し、センチネル読み戻しで空を確認
        する。相手の既存の下書き・履歴には触らない。エイリアスの
        学習は自動では行わない (本人の承認がある時だけ別途行う)。
        戻り値: 空に戻せたら True。結果は self.wrong_recipient と
        checks の wrong_recipient_cleanup に残す。
        """
        rec = next(
            (c for c in reversed(self.checks) if c.get("name") == "recipient"),
            {},
        )
        expected = rec.get("expected") or list(self.acfg.get("recipient_aliases", []))
        actual = rec.get("actual_header") or []
        cleaned = self.clear()
        self.wrong_recipient = {
            "expected": list(expected),
            "actual_header": list(actual),
            "cleaned": cleaned,
        }
        self.checks.append(
            {
                "name": "wrong_recipient_cleanup",
                "ok": cleaned,
                "evidence": "宛先違い (想定: %s / 実際のヘッダ: %s)。"
                "自分が入れた文を消去し%s"
                % (
                    "/".join(expected),
                    " / ".join(actual) if actual else "不明",
                    "、空を確認した" if cleaned else "たが空に戻せなかった",
                ),
            }
        )
        return cleaned

    def verify(self, text, mode, fetch_png=False):
        """検閲: 撮影 + OCR 照合。recipient チェックは複数回判定。

        宛先 (recipient) チェックは check_recipient_header と同じ
        共有ドライバ (_judge_header_multi) で判定する: 最大
        header_check_attempts 回まで撮り直し、各回を MATCH / OTHER /
        UNREADABLE に分類する (分類は、同じ回のレスポンスに含まれる
        ヘッダ帯切り出し OCR (-header-ocr.txt) から作った run 群に
        対して行う。全体 OCR は宛先判定に使わない)。OTHER が出たら
        残りの撮影をせず打ち切る。recipient 以外のチェック
        (本文・構造等) は、最後に撮影できた回の全体 OCR に対する
        run_verify_checks 結果を使う (run_verify_checks 自体は
        変更せず、その recipient チェックは集約チェックで置き換える)。
        最終的な
        recipient チェックは 1 件に集約して checks へ積み、各回の
        ヘッダ文字列と分類を evidence と attempts に残す。
        判定結果は self.recipient_header_error にも残す
        (None / "wrong_recipient" / "recipient_header_unreadable")。
        撮影自体が全回できなかった場合は従来通り verify_capture の
        失敗として False を返す。
        """
        other_names = self.other_recipient_formal_names()
        aliases = list(self.acfg.get("recipient_aliases", []))
        max_extra = int(
            self.tunables.get(
                "relearn_header_prefix_max_extra",
                DEFAULT_TUNABLES["relearn_header_prefix_max_extra"],
            )
        )
        state = {"last_status": "no_result", "last_vchecks": None}

        def attempt_fn():
            holder = {}

            def f():
                name = self._name("verify")
                extras = ["-ocr.txt", "-header-ocr.txt"]
                if fetch_png:
                    extras.append("-png.b64")
                holder["res"] = self.mac.run_queue(
                    build_verify_script(name, self.app, self.acfg, tun=self.tunables),
                    name,
                    extras=tuple(extras),
                )
                return holder["res"]

            res = self._timed("verify", f)
            st = res.get("status", "no_result")
            state["last_status"] = st
            if st != "ok":
                return {
                    "classification": "UNREADABLE",
                    "kind": None,
                    "extra": 0,
                    "run_text": None,
                    "matched": None,
                    "header": [],
                }
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
            state["last_vchecks"] = vchecks
            header_items = parse_ocr(res.get("_files", {}).get("-header-ocr.txt", ""))
            header_runs = self._text_runs(header_items)
            actual_header = [run_text for _point, run_text in header_runs]
            candidates = self._header_candidates(header_runs, header_items)
            att = classify_header_attempt(
                candidates,
                aliases,
                other_names,
                prefix_max_extra=max_extra,
            )
            # 証跡の header は実際の OCR run のまま (拡張候補は混ぜない)
            att["header"] = actual_header
            return att

        ok_multi, error, attempts = self._judge_header_multi(attempt_fn)
        self.recipient_header_error = error
        vchecks = state["last_vchecks"]
        if vchecks is None:
            self.checks.append(
                {
                    "name": "verify_capture",
                    "ok": False,
                    "evidence": "検閲できず: status=%s" % state["last_status"],
                }
            )
            return False
        attempts_ev = header_attempts_evidence(attempts)
        if ok_multi:
            rec_evidence = "宛先をヘッダで確認 (複数回判定 %d 回: %s)" % (
                len(attempts),
                attempts_ev,
            )
        elif error == "wrong_recipient":
            rec_evidence = "宛先違いをヘッダで検出 (複数回判定 %d 回: %s)" % (
                len(attempts),
                attempts_ev,
            )
        else:
            rec_evidence = (
                "ヘッダを確認できず (複数回判定 %d 回すべて UNREADABLE: %s)"
                % (len(attempts), attempts_ev)
            )
        aggregated = {
            "name": "recipient",
            "ok": ok_multi,
            "evidence": rec_evidence,
            "expected": list(aliases),
            "actual_header": [t for a in attempts for t in (a.get("header") or [])],
            "attempts": attempts,
            "recipient_error": error,
        }
        final_checks = []
        replaced = False
        for c in vchecks:
            if c.get("name") == "recipient" and not replaced:
                final_checks.append(aggregated)
                replaced = True
            else:
                final_checks.append(c)
        if not replaced:
            final_checks.insert(0, aggregated)
        self.checks.extend(final_checks)
        return all(c["ok"] for c in final_checks)

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
        record = {"failed_checks": names, "diff_summary": summary}
        if "recipient" in names:
            # 宛先系の失敗は種別 (別人 / 確認できず) を record にも残し、
            # run_post がエラー名を区別できるようにする
            record["recipient_error"] = self.recipient_header_error or "wrong_recipient"
        return False, retryable, record

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

    def dismiss_overlay(self, frame, ccfg):
        """想定外の覆いの汎用解消 (Esc + 外側クリック) を試みる。

        スクリプトが完走したら True。解消できたか自体の判定は、この後の
        再プローブに委ねる。結果は checks の overlay_dismiss に残す。
        """
        name = self._name("dismiss")
        res = self._timed(
            "dismiss",
            lambda: self.mac.run_queue(
                build_dismiss_script(name, self.app, self.acfg, frame, ccfg),
                name,
            ),
        )
        st = res.get("status", "no_result")
        self.checks.append(
            {
                "name": "overlay_dismiss",
                "ok": st == "ok",
                "evidence": "汎用解消 (Esc + 外側クリック) を実行: status=%s" % st,
            }
        )
        return st == "ok"

    def snapshot_thumb(self, ccfg):
        """画面確認用のサムネイル (PNG bytes)。失敗時は None。"""
        name = self._name("snapshot")
        res = self._timed(
            "snapshot",
            lambda: self.mac.run_queue(
                build_snapshot_script(
                    name, self.app, self.acfg, ccfg["dismiss_thumb_width"]
                ),
                name,
                extras=("-thumb.b64",),
            ),
        )
        if res.get("status") != "ok":
            return None
        return decode_b64_bytes(res.get("_files", {}).get("-thumb.b64", ""))

    def _probe_after_dismiss(self, candidates, frame, ccfg, res):
        """分類→対処: 覆いの疑い (probe_no_hit) への対処。

        対処は「汎用解消 → 画面がクリーンになったかの再確認 (解消前後の
        サムネイル差分) → 再プローブ」。再確認で変化が無ければ覆いが
        残っているとみなし、再プローブせず停止側に落とす。試行回数は
        dismiss_retries で上限を設ける (無限ループ防止)。解消できれば
        解消後のプローブ結果を返し、できなければ overlay_blocked を
        立てて最後の結果を返す (無理に進めない)。
        """
        self.checks.append(
            {
                "name": "anomaly_class",
                "ok": True,
                "evidence": "異常の種類: 覆いの疑い (入力欄に到達できず) "
                "→ 対処: 汎用解消 + 再確認 + 再プローブ",
            }
        )
        for _ in range(max(0, int(ccfg["dismiss_retries"]))):
            before = self.snapshot_thumb(ccfg)
            if not self.dismiss_overlay(frame, ccfg):
                break
            self.careful_capture("overlay-dismissed")
            after = self.snapshot_thumb(ccfg)
            ratio = (
                png_diff_ratio(before, after)
                if before is not None and after is not None
                else None
            )
            if ratio is not None:
                clean = ratio > float(ccfg["dismiss_change_threshold"])
                self.checks.append(
                    {
                        "name": "overlay_clean",
                        "ok": clean,
                        "evidence": "解消前後の画面差分 %.4f (しきい値 %.4f): %s"
                        % (
                            ratio,
                            float(ccfg["dismiss_change_threshold"]),
                            (
                                "変化あり (クリーンと確認)"
                                if clean
                                else "変化なし (覆いが残っている疑い)"
                            ),
                        ),
                    }
                )
                if not clean:
                    break
            res = self.probe(candidates, ccfg)
            if res["status"] != "probe_no_hit":
                break
        if res["status"] == "ok" and res["point"] is not None:
            self.checks.append(
                {
                    "name": "overlay",
                    "ok": True,
                    "evidence": "覆いを解消し、再プローブで入力欄に到達できた",
                }
            )
        elif res["status"] == "probe_no_hit":
            self.overlay_blocked = True
            self.checks.append(
                {
                    "name": "overlay",
                    "ok": False,
                    "evidence": "汎用解消を試みても入力欄に到達できず停止 "
                    "(想定外の覆いが残っている疑い。無理に進めない)",
                }
            )
        return res

    def capture_ocr(self):
        """対象ウィンドウを撮影し、(フレーム, OCR items) を返す。

        撮影・OCR ができなければ (None, [])。checks には積まない
        (呼び出し側が文脈に合ったチェック名で記録する)。
        """
        name = self._name("verify")
        res = self._timed(
            "capture",
            lambda: self.mac.run_queue(
                build_verify_script(name, self.app, self.acfg, tun=self.tunables),
                name,
                extras=("-ocr.txt",),
            ),
        )
        if res.get("status") != "ok":
            return None, []
        win = tuple(int(float(v)) for v in res.get("window", "0 0 0 0 0").split())
        frame = win[1:5] if len(win) == 5 else None
        if frame:
            self.last_frame = frame
        return frame, parse_ocr(res.get("_files", {}).get("-ocr.txt", ""))

    def capture_header_runs(self):
        """対象ウィンドウを撮影し、(フレーム, ヘッダ切り出し run 群) を返す。

        build_verify_script が作るヘッダ帯切り出し OCR
        (-header-ocr.txt) だけを取得し、その項目を text_runs
        (merge 間隔は tunables の talk_row_merge_px) で run 化する。
        全体 OCR (-ocr.txt) は取得も判定もしない。撮影ができなければ
        (None, [])、切り出しファイルが無い・空なら (フレーム, [])。
        checks には積まない (capture_ocr と同じ作法)。last_frame の
        更新も capture_ocr と同様に行う。戻り値 (フレーム, runs) は
        従来通りで、parse した項目は self._last_header_items にも
        保持する (候補拡張が有効なアプリで項目候補を作るため)。
        """
        self._last_header_items = []
        name = self._name("verify")
        res = self._timed(
            "capture",
            lambda: self.mac.run_queue(
                build_verify_script(name, self.app, self.acfg, tun=self.tunables),
                name,
                extras=("-header-ocr.txt",),
            ),
        )
        if res.get("status") != "ok":
            return None, []
        win = tuple(int(float(v)) for v in res.get("window", "0 0 0 0 0").split())
        frame = win[1:5] if len(win) == 5 else None
        if frame:
            self.last_frame = frame
        items = parse_ocr(res.get("_files", {}).get("-header-ocr.txt", ""))
        self._last_header_items = items
        return frame, text_runs(items, float(self.tunables["talk_row_merge_px"]))

    def click_point(self, point):
        """指定点を 1 回クリックする (前面化つき)。送信キーは送らない。"""
        name = self._name("click")
        res = self._timed(
            "click",
            lambda: self.mac.run_queue(
                build_click_script(name, self.app, self.acfg, point), name
            ),
        )
        return res.get("status") == "ok"

    def _exact_nav_candidates(self, frame, items, aliases):
        """トーク一覧の OCR から、宛先エイリアスのいずれかと完全一致する
        行の候補を (point, text) のリストで返す (座標の重複は除く)。

        scrutinize_nav の探索規則そのもの。ホーム遷移リトライ後の
        再探索でも同じ規則を使うため、切り出して共有する。
        """
        exact = []
        seen_points = set()
        for alias in aliases:
            for x, y, text in self.find_talk_candidates(alias, frame, items):
                if not any(norm(text) == norm(a) for a in aliases):
                    continue
                point = (int(x), int(y))
                if point in seen_points:
                    continue
                seen_points.add(point)
                exact.append((point, text))
        return exact

    def scrutinize_nav(self):
        """ナビゲーション前の座標精査: 保存済み nav_click が宛先本人の
        行を指しているかを、座標を使う前にトーク一覧の OCR で確認する。

        一覧の並び替えで保存座標が別人の行を指すようになっている場合に、
        その座標で開いてしまう事故を防ぐ前段ゲート。宛先エイリアス
        ごとに find_talk_candidates で一覧探索し (再学習と同じ規則)、
        返ってきた候補のうちエイリアスのいずれかと完全一致するもの
        だけを「完全一致候補」とする (部分一致のみは候補に数えない)。
        行の同一性は縦座標で判定する (横は一覧領域内であることが
        候補の条件で担保される)。許容差は tunables の
        nav_scrutiny_row_tolerance_px。

        - 候補 1 件かつ保存座標と同じ行: 通過 (クリック・保存なし)
        - 候補 1 件だが保存座標と違う行: その行をクリックして開き、
          recipients 辞書の nav_click を候補座標へ更新して保存する
          (書き込み前にバックアップ + 学習記録へ追記。再学習の登録と
          同じ作法)。self.acfg も新しい値に更新し、後段のヘッダ確認・
          入力が新しい座標を使うようにする
        - 候補 0 件で実効設定に home_click がある場合のみ、救済として
          ホームボタンへ 1 回だけ遷移し、再撮影して探索をやり直す
          (アプリが別画面を開いていて一覧が見えていないケース)。
          再探索で候補が 1 件見つかれば通常の判定に合流する。
          クリック失敗時は nav_home_click_failed、再撮影不可は
          nav_scrutiny_unreadable で停止する。試行は checks の
          nav_home に残す
        - 候補 0 件 / 2 件以上 / 撮影・OCR 不可 / 修正クリック失敗:
          False を返し、self.nav_scrutiny_error に理由を残す
          (クリックも設定変更もしない。修正クリック失敗時と
          上記のホーム救済時を除く)
        - nav_click 未設定で候補 1 件: それを正として通過扱い
        精査は文字入力・送信キーを絶対に行わない (行を開くクリックのみ)。
        この経路は Runner 共通 (アプリ非依存) で、一覧探索の幅比率は
        talk_list_width_ratio() がアプリ別に解決するため、LINE /
        Discord の両方で同じ精査が効く。
        結果は checks の nav_scrutiny に残す。戻り値: 通過なら True。
        """
        self.nav_scrutiny_error = None
        aliases = list(self.acfg.get("recipient_aliases", []))
        frame, items = self.capture_ocr()
        if frame is None or not items:
            self.nav_scrutiny_error = "nav_scrutiny_unreadable"
            self.checks.append(
                {
                    "name": "nav_scrutiny",
                    "ok": False,
                    "evidence": "トーク一覧を読み取れなかった "
                    "(撮影・OCR ができず nav_click を精査できない / "
                    "想定宛先: %s)" % "/".join(aliases),
                }
            )
            return False
        exact = self._exact_nav_candidates(frame, items, aliases)
        if not exact:
            home = self.acfg.get("home_click")
            if home:
                # 救済: アプリが別画面 (Discord のサーバー画面など) を開いて
                # いて DM 一覧が見えていないケース。ホームボタンへ 1 回だけ
                # 遷移して再探索する。クリックは最大 1 回で、文字入力・
                # 送信キーは行わない。home_click 未設定なら従来どおり
                # この救済は発動せず、そのまま未検出で停止する。
                home_point = tuple(int(v) for v in home)
                if self.click_point(home_point) is False:
                    self.checks.append(
                        {
                            "name": "nav_home",
                            "ok": False,
                            "evidence": "ホームボタン (%d, %d) をクリック"
                            "できなかったため、再探索せず停止" % home_point,
                        }
                    )
                    self.nav_scrutiny_error = "nav_home_click_failed"
                    self.checks.append(
                        {
                            "name": "nav_scrutiny",
                            "ok": False,
                            "evidence": "ホーム遷移のクリックに失敗したため"
                            " nav_click を精査できない (想定宛先: %s)"
                            % "/".join(aliases),
                        }
                    )
                    return False
                frame, items = self.capture_ocr()
                if frame is None or not items:
                    self.checks.append(
                        {
                            "name": "nav_home",
                            "ok": False,
                            "evidence": "ホーム遷移後のトーク一覧を"
                            "読み取れなかった (撮影・OCR 不可)",
                        }
                    )
                    self.nav_scrutiny_error = "nav_scrutiny_unreadable"
                    self.checks.append(
                        {
                            "name": "nav_scrutiny",
                            "ok": False,
                            "evidence": "ホーム遷移後にトーク一覧を"
                            "読み取れなかった (撮影・OCR ができず "
                            "nav_click を精査できない / 想定宛先: %s)"
                            % "/".join(aliases),
                        }
                    )
                    return False
                exact = self._exact_nav_candidates(frame, items, aliases)
                self.checks.append(
                    {
                        "name": "nav_home",
                        "ok": bool(exact),
                        "evidence": "ホームボタン (%d, %d) へ遷移して"
                        "再探索した (完全一致候補 %d 件)"
                        % (home_point + (len(exact),)),
                    }
                )
        if not exact:
            self.nav_scrutiny_error = "nav_recipient_not_found"
            self.checks.append(
                {
                    "name": "nav_scrutiny",
                    "ok": False,
                    "evidence": "トーク一覧に宛先 (%s) と完全一致する行が"
                    "見つからない (部分一致のみは候補にしない)" % "/".join(aliases),
                }
            )
            return False
        if len(exact) > 1:
            self.nav_scrutiny_error = "nav_recipient_ambiguous"
            self.checks.append(
                {
                    "name": "nav_scrutiny",
                    "ok": False,
                    "evidence": "トーク一覧に宛先 (%s) と完全一致する行が "
                    "%d 件あり特定できない (何も変更せず停止)"
                    % ("/".join(aliases), len(exact)),
                }
            )
            return False
        (cx, cy), row_text = exact[0]
        nav = self.acfg.get("nav_click")
        if not nav:
            self.checks.append(
                {
                    "name": "nav_scrutiny",
                    "ok": True,
                    "evidence": "nav_click 未設定のため、トーク一覧で"
                    "見つけた宛先 '%s' の行 (%d, %d) を正として通過"
                    % (row_text, cx, cy),
                }
            )
            return True
        tolerance = float(
            self.tunables.get(
                "nav_scrutiny_row_tolerance_px",
                DEFAULT_TUNABLES["nav_scrutiny_row_tolerance_px"],
            )
        )
        if abs(cy - int(nav[1])) <= tolerance:
            self.checks.append(
                {
                    "name": "nav_scrutiny",
                    "ok": True,
                    "evidence": "保存済み nav_click (%d, %d) の行が宛先 "
                    "'%s' 本人であることを確認 (候補行 (%d, %d) との"
                    "縦座標差が許容差 %dpx 以内)"
                    % (int(nav[0]), int(nav[1]), row_text, cx, cy, int(tolerance)),
                }
            )
            return True
        # 不一致: 保存座標が別人の行を指している。探索で見つけた
        # 本人の行を開き、nav_click をその座標へ更新して保存する。
        old_nav = [int(nav[0]), int(nav[1])]
        new_nav = [cx, cy]
        if self.click_point((cx, cy)) is False:
            self.nav_scrutiny_error = "nav_scrutiny_click_failed"
            self.checks.append(
                {
                    "name": "nav_scrutiny",
                    "ok": False,
                    "evidence": "宛先 '%s' の行 (%d, %d) をクリック"
                    "できなかったため、nav_click を更新せず停止" % (row_text, cx, cy),
                }
            )
            return False
        recips = self.app_cfg.get("recipients") or {}
        if self.recipient_id is not None and self.recipient_id in recips:
            recips[self.recipient_id]["nav_click"] = list(new_nav)
        else:
            # 旧形式 (アプリ直下の nav_click) の config への後方互換
            self.app_cfg["nav_click"] = list(new_nav)
        self.acfg["nav_click"] = list(new_nav)
        backup = save_config(self.cfg)
        append_learned_case(
            {
                "at": datetime.now(JST).isoformat(),
                "app": self.app,
                "item": "recipient_nav_click:" + str(self.recipient_id),
                "old": old_nav,
                "new": list(new_nav),
                "reason": "nav 精査で保存座標が別人の行を指していたため、"
                "一覧探索で見つけた宛先本人の行へ更新",
            }
        )
        backup_note = (
            "バックアップ: %s" % os.path.basename(backup) if backup else "保存済み"
        )
        self.checks.append(
            {
                "name": "nav_scrutiny",
                "ok": True,
                "evidence": "保存座標 (%d, %d) が別人の行を指していたため、"
                "探索で見つけた宛先 '%s' の行 (%d, %d) に nav_click を"
                "更新した (旧 %s → 新 %s / %s)"
                % (
                    old_nav[0],
                    old_nav[1],
                    row_text,
                    cx,
                    cy,
                    old_nav,
                    new_nav,
                    backup_note,
                ),
            }
        )
        return True

    def other_recipient_formal_names(self):
        """宛先以外の登録済み宛先の正式名リスト (OTHER 判定用)。

        アプリ設定の recipients 辞書から取る (formal_names_of_others)。
        旧形式 (宛先 1 件だけ) の config では空になる。
        """
        recips, _default = recipients_of(self.app_cfg)
        return formal_names_of_others(recips, self.recipient_id)

    def header_candidate_expansion_enabled(self):
        """ヘッダ候補拡張がこのアプリで有効か (config 駆動)。

        apps.<app> 配下の header_candidate_expansion が真の場合のみ
        有効。アプリ名のハードコードや文字列内容による分岐はしない。
        実効設定 (self.acfg) 側にキーがあればそちらも見る。
        """
        for cfg in (self.app_cfg, self.acfg):
            if cfg and cfg.get(HEADER_CANDIDATE_EXPANSION_KEY):
                return True
        return False

    def _header_candidates(self, runs, items=None):
        """宛先判定に渡す候補群を返す。

        候補拡張が有効なアプリでのみ expand_header_candidates で
        拡張する。無効なアプリ (LINE を含む) では runs をそのまま
        (同一のオブジェクト・順序・内容で) 返すため、classify に
        渡る run 群は現行と完全に同一になる。
        """
        if not self.header_candidate_expansion_enabled():
            return runs
        return expand_header_candidates(runs, items)

    def _judge_header_multi(self, attempt_fn):
        """ヘッダ複数回判定の共有ドライバ。

        attempt_fn() は 1 回撮影し直して、classify_header_attempt の
        結果に "header" (その回のヘッダ run 文字列リスト) を加えた
        dict を返す。各回は必ず撮り直す (前回の画像・OCR の使い回し
        禁止)。最大で tunables の header_check_attempts 回まで撮影し、
        撮影と撮影の間には header_check_interval_sec 秒を
        time.sleep で空ける。総合判定:
        - OTHER が 1 回でも出たら残りの撮影をせず即失敗
          (error="wrong_recipient")
        - N 回撮り終えて OTHER 無し・MATCH 1 回以上で合格
        - 全部 UNREADABLE なら「確認できず」で失敗
          (error="recipient_header_unreadable"、wrong_recipient と区別)
        MATCH が出ても早期終了はしない (打ち切るのは OTHER だけ)。
        戻り値: (ok, error, attempts)。
        """
        n = int(
            self.tunables.get(
                "header_check_attempts", DEFAULT_TUNABLES["header_check_attempts"]
            )
        )
        interval = float(
            self.tunables.get(
                "header_check_interval_sec",
                DEFAULT_TUNABLES["header_check_interval_sec"],
            )
        )
        attempts = []
        for i in range(max(1, n)):
            if i > 0:
                time.sleep(interval)
            att = dict(attempt_fn())
            att["attempt"] = i + 1
            attempts.append(att)
            if att["classification"] == "OTHER":
                break
        if any(a["classification"] == "OTHER" for a in attempts):
            return False, "wrong_recipient", attempts
        if any(a["classification"] == "MATCH" for a in attempts):
            return True, None, attempts
        return False, "recipient_header_unreadable", attempts

    def check_recipient_header(self):
        """入力前に、開いたトークのヘッダだけで宛先を確認する。

        nav_click があればその行をクリックして開いてから、複数回
        判定 (共有ドライバ _judge_header_multi) で、各回の撮影ごとに
        capture_header_runs() が返すヘッダ帯切り出し OCR の run 群
        だけを宛先エイリアスと照合する (全体 OCR からヘッダ領域を
        座標で拾う方式は使わない)。トーク一覧に写った名前では
        合格しない。各回の分類は MATCH / OTHER / UNREADABLE の 3 値で、
        evidence と構造化フィールド (attempts) に各回のヘッダ文字列と
        分類を残す。
        戻り値: 合格したら True。結果は checks の recipient_header
        に残す。OTHER (別人) の場合は self.wrong_recipient
        (cleaned=False: まだ何も入力していないため消すものは無い) と
        self.recipient_header_error="wrong_recipient" を残す。
        全部 UNREADABLE (撮影・OCR ができない場合を含む) は
        recipient_header_error="recipient_header_unreadable" として
        False を返す (wrong_recipient とは区別する)。
        """
        self.recipient_header_error = None
        aliases = list(self.acfg.get("recipient_aliases", []))
        nav = self.acfg.get("nav_click")
        if nav:
            if not self.click_point(nav):
                self.recipient_header_error = "recipient_header_unreadable"
                self.checks.append(
                    {
                        "name": "recipient_header",
                        "ok": False,
                        "evidence": "宛先のトークを開けなかった "
                        "(nav_click=%s のクリック失敗 / 想定宛先: %s)"
                        % (list(nav), "/".join(aliases)),
                    }
                )
                return False
        max_extra = int(
            self.tunables.get(
                "relearn_header_prefix_max_extra",
                DEFAULT_TUNABLES["relearn_header_prefix_max_extra"],
            )
        )
        other_names = self.other_recipient_formal_names()

        def attempt_fn():
            frame, runs = self.capture_header_runs()
            if frame is None or not runs:
                return {
                    "classification": "UNREADABLE",
                    "kind": None,
                    "extra": 0,
                    "run_text": None,
                    "matched": None,
                    "header": [],
                }
            candidates = self._header_candidates(
                runs, getattr(self, "_last_header_items", None)
            )
            att = classify_header_attempt(
                candidates, aliases, other_names, prefix_max_extra=max_extra
            )
            # 証跡の header は実際の OCR run のまま (拡張候補は混ぜない)
            att["header"] = [text for _point, text in runs]
            return att

        ok, error, attempts = self._judge_header_multi(attempt_fn)
        attempts_ev = header_attempts_evidence(attempts)
        if ok:
            evidence = "宛先をヘッダで確認 (複数回判定 %d 回: %s / 想定宛先: %s)" % (
                len(attempts),
                attempts_ev,
                "/".join(aliases),
            )
        elif error == "wrong_recipient":
            evidence = (
                "宛先違いをヘッダで検出 (複数回判定 %d 回: %s / 想定宛先: %s)"
                % (len(attempts), attempts_ev, "/".join(aliases))
            )
        else:
            evidence = (
                "ヘッダを確認できず (複数回判定 %d 回すべて UNREADABLE: %s / "
                "想定宛先: %s)" % (len(attempts), attempts_ev, "/".join(aliases))
            )
        self.checks.append(
            {
                "name": "recipient_header",
                "ok": ok,
                "evidence": evidence,
                "expected": list(aliases),
                "actual_header": [t for a in attempts for t in (a.get("header") or [])],
                "attempts": attempts,
                "recipient_error": error,
            }
        )
        if not ok:
            self.recipient_header_error = error
            if error == "wrong_recipient":
                other_att = next(a for a in attempts if a["classification"] == "OTHER")
                self.wrong_recipient = {
                    "expected": list(aliases),
                    "actual_header": list(other_att.get("header") or []),
                    "cleaned": False,
                }
            return False
        return True

    def _text_runs(self, items):
        """OCR 項目を行単位の run にまとめる (共有ヘルパーへの委譲)。

        規則はモジュールレベルの text_runs と同じで、merge 間隔は
        tunables の talk_row_merge_px を使う。
        """
        return text_runs(items, float(self.tunables["talk_row_merge_px"]))

    def talk_list_width_ratio(self):
        """トーク一覧とみなす横範囲 (窓幅比) をアプリ別に解決する。

        apps.<app> の talk_list_width_ratio があればそれを優先し、
        無ければ共通 tunables の値 (既定 0.32) を使う。一覧の幅は
        アプリのレイアウト依存 (例: LINE の一覧は窓幅の約26%で、共通値
        0.32 では開いた先のヘッダ名まで一覧側に含めてしまい、ヘッダ
        確認が常時失敗する) のため、アプリ別の上書きを可能にする。
        """
        v = self.app_cfg.get("talk_list_width_ratio")
        if v is None:
            v = self.tunables["talk_list_width_ratio"]
        return float(v)

    def find_talk_candidates(self, name, frame, items):
        """トーク一覧の領域から、名前の行の候補をクリック点つきで返す。

        戻り値: [(click_x, click_y, ocr_text), ...]。
        判定は行の中の連続した文字列単位で行い (名前と人数表記が別
        項目に割れていれば連結した文字列で見る)、完全一致する文字列
        があればそれだけを、無ければ名前を含む文字列を候補にする。
        クリック点は画面絶対座標。
        """
        target = norm(name)
        if not target or not frame:
            return []
        fx, fy, fw, _fh = (int(v) for v in frame)
        limit_x = fw * self.talk_list_width_ratio()
        in_list = [it for it in items if it["x"] <= limit_x and norm(it["text"])]
        exact, partial = [], []
        for (px, py), text in self._text_runs(in_list):
            text_n = norm(text)
            entry = (int(fx + px), int(fy + py), text)
            if text_n == target:
                exact.append(entry)
            elif target in text_n:
                partial.append(entry)
        return exact if exact else partial

    def relearn_recipient(self, name):
        """未知宛先の再学習: 座標の再取得 → 辞書への変数登録。

        流れ: 画面を撮影し、トーク一覧から指定名の行を探す → その行の
        座標をクリックして開く → 開いた先のヘッダの宛名が指定名と
        一致するか OCR で確認する → 一致したら、その座標 (nav_click) と
        照合用エイリアスを config の recipients 辞書へ新しい変数として
        登録する (書き込み前にバックアップ + 学習記録へ追記)。
        見つからない / 同じ名前の行が複数ある・名前と完全一致する行が
        無く部分一致のみ (グループ名等の可能性) で特定できない /
        ヘッダが完全一致しない場合は、何も登録せず停止する。戻り値:
        (ok, learned_entries)。失敗理由は self.relearn_error に残す。
        """
        name = str(name).strip()
        target = norm(name)
        frame, items = self.capture_ocr()
        if frame is None:
            self.checks.append(
                {
                    "name": "recipient_search",
                    "ok": False,
                    "evidence": "画面を撮影できず、トーク一覧を探せなかった",
                }
            )
            self.relearn_error = "recipient_search_failed"
            return False, []
        cands = self.find_talk_candidates(name, frame, items)
        if not cands:
            self.checks.append(
                {
                    "name": "recipient_search",
                    "ok": False,
                    "evidence": "トーク一覧に '%s' が見つからない" % name,
                }
            )
            self.careful_capture("recipient-not-found")
            self.relearn_error = "recipient_not_found"
            return False, []
        if len(cands) > 1:
            self.checks.append(
                {
                    "name": "recipient_search",
                    "ok": False,
                    "evidence": "トーク一覧に '%s' と読める行が %d 件あり特定できない"
                    " (同名・グループ等の可能性。何も登録せず停止)"
                    % (name, len(cands)),
                }
            )
            self.careful_capture("recipient-ambiguous")
            self.relearn_error = "recipient_ambiguous"
            return False, []
        if norm(cands[0][2]) != target:
            # 完全一致の行が無く、名前を含むだけの行が 1 件だけある。
            # より長い名前の一部か、グループ名 (「○○と他2名」等) の
            # 可能性があり特定できないため、曖昧として登録せず停止する。
            self.checks.append(
                {
                    "name": "recipient_search",
                    "ok": False,
                    "evidence": "トーク一覧に '%s' と完全一致する行がなく、"
                    "部分一致のみ (%s)。グループ等の可能性があるため"
                    "特定できず、何も登録せず停止" % (name, cands[0][2]),
                }
            )
            self.careful_capture("recipient-ambiguous")
            self.relearn_error = "recipient_ambiguous"
            return False, []
        x, y, _row_text = cands[0]
        self.checks.append(
            {
                "name": "recipient_search",
                "ok": True,
                "evidence": "トーク一覧で '%s' の行の座標を再取得 (%d, %d)"
                % (name, x, y),
            }
        )
        if not self.click_point((x, y)):
            self.checks.append(
                {
                    "name": "recipient_search",
                    "ok": False,
                    "evidence": "トーク行 (%d, %d) をクリックできなかった" % (x, y),
                }
            )
            self.relearn_error = "recipient_search_failed"
            return False, []
        _frame2, items2 = self.capture_ocr()
        header_limit = float(self.tunables["relearn_header_max_y"])
        # ヘッダ確認は、開いたトーク側の領域 (トーク一覧より右) に
        # 限定する。一覧は開いた後も左に残るため、帯の縦範囲だけで
        # 見ると一覧側の名前で合格してしまう。判定は行の中の連続した
        # 文字列単位で行う。一致条件は完全一致、または「指定名で始まり
        # 余りが relearn_header_prefix_max_extra 文字以内」の前方一致。
        # ヘッダ名の右にあるアイコンが OCR で名前の後ろに連結される
        # 誤読 (例「<名前>4呼」) を吸収するための緩和で、領域スコープと
        # 一覧側の完全一致 (クリック前に確定) が安全側の担保になる。
        # この判定はアプリ共通経路であり、LINE / Discord の両方に効く。
        fw2 = (_frame2 or frame)[2]
        max_extra = int(self.tunables["relearn_header_prefix_max_extra"])
        header_runs = header_region_runs(
            items2,
            fw2,
            self.talk_list_width_ratio(),
            header_limit,
            float(self.tunables["talk_row_merge_px"]),
        )
        kind, extra, run_text, _alias = header_alias_match(
            header_runs, [target], prefix_max_extra=max_extra
        )
        header_hit = norm(run_text) if kind else None
        header_match = kind  # "exact" | "prefix" | None
        header_extra = extra if kind else 0
        if header_hit is None:
            self.checks.append(
                {
                    "name": "recipient_header",
                    "ok": False,
                    "evidence": "開いた先のヘッダに '%s' が見当たらない"
                    " (完全一致・前方一致 (余り%d文字以内) のいずれでも"
                    "一致せず、何も登録せず停止)" % (name, max_extra),
                }
            )
            self.careful_capture("recipient-header-mismatch")
            self.relearn_error = "recipient_header_mismatch"
            return False, []
        if header_match == "exact":
            header_evidence = (
                "開いた先のヘッダで宛名 '%s' を確認 (一致方式: exact)" % header_hit
            )
        else:
            header_evidence = (
                "開いた先のヘッダで宛名 '%s' を確認"
                " (一致方式: prefix、ヘッダ文字列 '%s'、余り%d文字)"
                % (name, header_hit, header_extra)
            )
        self.checks.append(
            {
                "name": "recipient_header",
                "ok": True,
                "evidence": header_evidence,
            }
        )
        # 登録する変数: トークを開く座標 (nav_click) と照合用エイリアス。
        # 前方一致の場合はヘッダ文字列に誤読分が混ざっているため、
        # エイリアスには指定名だけを使う (完全一致時のみ従来通り
        # ヘッダで読めた文字列も候補にする)。
        aliases = [name]
        header_name = header_hit.strip() if header_match == "exact" else ""
        if header_name and header_name not in aliases:
            aliases.append(header_name)
        entry = {"aliases": aliases, "nav_click": [x, y]}
        old_recips, old_default = recipients_of(self.app_cfg)
        recips = dict(old_recips)
        recips[name] = entry
        self.app_cfg["recipients"] = recips
        if not self.app_cfg.get("default_recipient") and old_default:
            self.app_cfg["default_recipient"] = old_default
        backup = save_config(self.cfg)
        append_learned_case(
            {
                "at": datetime.now(JST).isoformat(),
                "app": self.app,
                "item": "recipient:" + name,
                "old": None,
                "new": entry,
                "reason": "再学習フローで座標を再取得して登録",
            }
        )
        self.recipient_id = name
        self.recipient_known = True
        self.acfg = effective_acfg(self.app_cfg, entry)
        self.acfg["recipient_id"] = name
        self.checks.append(
            {
                "name": "config_saved",
                "ok": True,
                "evidence": "宛先 '%s' を辞書へ登録 (nav_click=%s / aliases=%s。"
                "バックアップ: %s)" % (name, [x, y], aliases, os.path.basename(backup)),
            }
        )
        learned = [
            {
                "item": "recipient",
                "old": None,
                "new": {"id": name, "aliases": aliases, "nav_click": [x, y]},
                "reason": "再学習フローで座標を再取得して登録",
            }
        ]
        return True, learned

    def careful(self, to_name=None):
        """慎重モード本体。戻り値: (ok, learned, self_check_ok)。

        to_name に辞書に無い宛先が指定されていたら、先に再学習フロー
        (座標の再取得 → 辞書への変数登録) を行い、登録できたら通常の
        慎重モード本体 (座標の探索・保存・自己確認) へ進む。登録
        できなければ何も登録せず停止する。
        """
        learned = []
        if to_name and not self.recipient_known:
            ok_rel, entries = self.relearn_recipient(to_name)
            learned.extend(entries)
            if not ok_rel:
                return False, learned, False
        body_ok, body_learned, self_check_ok = self._careful_body()
        learned.extend(body_learned)
        return body_ok, learned, self_check_ok

    def _careful_body(self):
        """慎重モード本体 (座標の探索と学習)。戻り値は careful と同じ。

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
        if res["status"] == "probe_no_hit":
            # 入力欄に到達できない = 想定外の覆いの疑い。汎用解消を試す。
            # (欄の残り anomaly_leftover は従来通り何も触らず停止する)
            res = self._probe_after_dismiss(candidates, frame, ccfg, res)
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
                # 実効設定 (self.acfg) はコピーなので、保存対象の実体
                # (アプリ設定) にも同じ値を書く
                self.app_cfg[entry["item"]] = entry["new"]
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
        "--to",
        help="宛先 (config の recipients 辞書の ID または名前)。"
        "未指定なら default_recipient",
    )
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
        rid, entry = find_recipient(cfg["apps"][args.app], args.to)
        if entry is None:
            # 辞書に無い宛先には承認を作らない (まず careful --to で
            # 座標を再取得して登録してから承認する)
            out = {
                "ok": False,
                "app": args.app,
                "action": "approve",
                "recipient": None,
                "total_sec": round(time.time() - t0, 2),
                "recipient_requested": args.to,
                "error": "unknown_recipient",
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))
            sys.exit(2)
        ttl = timedelta(hours=float(tunables(cfg)["approval_ttl_hours"]))
        rec = create_approval(
            args.app,
            effective_acfg(cfg["apps"][args.app], entry),
            text,
            expires=args.expires,
            ttl=ttl,
        )
        out = {
            "ok": True,
            "app": args.app,
            "action": "approve",
            "recipient": rid,
            "recipient_requested": args.to,
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

    r = Runner(cfg, args.app, to=args.to)
    ok = True
    error = None
    retry_info = None
    learned = None
    self_check = None
    try:
        if r.to_name and not r.recipient_known and args.action != "careful":
            # 辞書に無い宛先は正常系に乗らない。座標の再取得と登録は
            # careful の再学習フローで行うため、ここでは Mac に触らず
            # 停止する (careful --to <宛先> を先に呼ぶのが手順)。
            ok = False
            error = "unknown_recipient"
            r.checks.append(
                {
                    "name": "recipient_resolution",
                    "ok": False,
                    "evidence": "宛先 '%s' は辞書に無い "
                    "(careful --to '%s' で座標を再取得して登録してから送る)"
                    % (r.to_name, r.to_name),
                }
            )
        elif not r.connect():
            ok = False
            error = "connect_failed"
        elif args.action == "input":
            ok = r.input(read_text(args))
        elif args.action == "verify":
            ok = r.verify(read_text(args), args.mode)
        elif args.action == "careful":
            # 慎重モード: 学習の経路。送信キーは絶対に押さない。
            # 辞書に無い宛先が --to で指定されていたら、先に再学習
            # フロー (座標の再取得 → 辞書への登録) に入る。
            ok, learned, self_check_ok = r.careful(to_name=r.to_name)
            self_check = {"ok": self_check_ok}
            if not ok and error is None:
                if r.relearn_error:
                    # 再学習で登録できなかった理由をそのまま報告する
                    error = r.relearn_error
                elif r.overlay_blocked:
                    # 覆いを解消できずに停止した場合は理由を分けて報告する
                    error = "overlay_unresolved"
                else:
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
        "recipient": r.recipient_id,
        "recipient_requested": r.to_name,
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
    if getattr(r, "wrong_recipient", None):
        # 宛先違い: 想定宛先と実際の宛先 (OCR 証拠) を結果に含める
        out["wrong_recipient"] = r.wrong_recipient
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
