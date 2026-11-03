#!/bin/bash
# AC給電時だけ、ディスプレイを自動でオフにしない設定に戻す（本体スリープなしは現行のまま）
sudo pmset -c displaysleep 0
echo "--- 現在のAC電源設定 ---"
pmset -g custom | sed -n '/AC Power:/,$p' | grep -E "displaysleep|^ sleep"
echo "完了しました。この窓は閉じて大丈夫です。"
