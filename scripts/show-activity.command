#!/bin/zsh
clear
echo "🤖 Muse の作業ログ - この窓でコマンド実行の様子を確認できます"
echo ""
tail -n 100 -f "$HOME/muse-tools/activity.log"
