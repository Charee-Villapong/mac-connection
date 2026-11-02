#!/bin/zsh
echo "=================================="
echo " macOS 27.2 アップデート（再起動つき）"
echo " Password: と出たら、Macのログインパスワードを"
echo " 入力してEnterを押してください（文字は表示されません）"
echo " このあと自動で再起動して、インストールが始まります"
echo "=================================="
sudo softwareupdate --install "macOS 27.2-26B123" --agree-to-license --restart
echo ""
echo "=== softwareupdate ga owari mashita ==="
read -k 1 "?Press any key..."
