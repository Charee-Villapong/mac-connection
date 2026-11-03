# mac-ssh 用ローカル設定のテンプレート。
# このファイルを config.local.sh という名前でコピーし、自分の環境の値を入れて使う。
# config.local.sh は .gitignore 対象なので、実値が Git に上がることはない。

# SSHユーザー名
MAC_SSH_USER="<SSHユーザー名>"
# 接続先（Tailscale の IP アドレス、または MagicDNS のホスト名）
MAC_HOST="<MacのTailscale IP>"
# （任意）ProxyCommand に使う ts-proxy.sh のパス。
# 未設定なら mac-ssh と同じフォルダの ts-proxy.sh を使う。
# MAC_TS_PROXY="/path/to/ts-proxy.sh"
