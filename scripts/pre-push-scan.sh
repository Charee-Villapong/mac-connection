#!/usr/bin/env bash
# pre-push-scan.sh — GitHub へ push する前の検閲をスクリプトで自動実行する。
#
# 使い方: scripts/pre-push-scan.sh [repo-root]
#   - 追跡対象ファイル (git ls-files) の全文を走査する。
#   - 汎用パターン (秘密鍵・認証情報らしき文字列・実 IP らしき文字列) は
#     このスクリプト本体に持つ。
#   - 個人特定につながるパターン (氏名・宛先名・実ホスト等) は、リポジトリ
#     直下の scan-patterns.local.txt (.gitignore 対象) に 1 行 1 パターンで
#     置く。本体には個人パターンを書かない。
#   - コミット作者情報も確認する。
# 終了コード: 0 = 問題なし / 1 = 検出あり (push を止めること)。
set -u
cd "${1:-$(git rev-parse --show-toplevel)}" || exit 1
FAIL=0

scan() {
  # $1 = ラベル, $2 = grep -E パターン
  # 追跡対象に加え、未追跡 (ただし .gitignore 対象外) のファイルも走査する
  hits=$(git ls-files -z --cached --others --exclude-standard | xargs -0 grep -nIE "$2" 2>/dev/null | grep -v '^scripts/pre-push-scan.sh' || true)
  if [ -n "$hits" ]; then
    echo "[検出] $1"
    echo "$hits" | head -20
    FAIL=1
  fi
}

# --- 汎用パターン (認証情報・秘密鍵・実 IP らしき値) ---
scan "秘密鍵" 'BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY'
scan "認証情報らしき代入" '(password|passwd|api[_-]?key|secret|access[_-]?token)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{8,}["'"'"']'
scan "Tailscale 帯の実 IP" '100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}'
scan "メールアドレス" '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(com|jp|net|org|io)'
scan "SSH 指紋" 'SHA256:[A-Za-z0-9+/=]{20,}'

# --- 個人パターン (ローカルファイルから。無ければ汎用のみで走査) ---
LOCAL_PATTERNS="scan-patterns.local.txt"
if [ -f "$LOCAL_PATTERNS" ]; then
  while IFS= read -r pat; do
    [ -z "$pat" ] && continue
    case "$pat" in '#'*) continue ;; esac
    scan "個人パターン" "$pat"
  done < "$LOCAL_PATTERNS"
else
  echo "[注意] $LOCAL_PATTERNS が無いため、個人パターンの走査を省略した"
fi

# --- バイナリ (画像・PDF 等は中身とメタデータを個別確認すること) ---
bins=$(git ls-files | grep -iE '\.(png|jpe?g|gif|pdf|zip|dmg)$' || true)
if [ -n "$bins" ]; then
  echo "[確認] 追跡対象のバイナリ (中身・メタデータを目視確認すること):"
  echo "$bins"
fi

# --- コミット作者情報 (noreply 以外の個人メールが混ざっていないか) ---
authors=$(git log --format='%ae' | sort -u | grep -v 'users.noreply.github.com' || true)
if [ -n "$authors" ]; then
  echo "[検出] noreply でないコミット作者メール:"
  echo "$authors"
  FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then
  echo "pre-push-scan: 問題なし"
else
  echo "pre-push-scan: 検出あり。push を止めて除去すること"
fi
exit "$FAIL"
