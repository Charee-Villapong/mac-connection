# scripts（実行用スクリプト）

Mac への接続と運用のための、実行用スクリプト置き場です。

- mac-ssh / ts-proxy.sh … Tailscale 経由の SSH 接続ラッパー
- config.local.example.sh … mac-ssh 用ローカル設定のテンプレート（下記参照）
- gui-agent.sh … 画面操作の依頼を受け付ける常駐エージェント
- gui-agent.plist.example … gui-agent を常駐させる LaunchAgent のテンプレート
- show-activity.command … 作業ログを画面に流し続ける表示窓
- set-power-saving.command … 省電力設定（画面オフの時間）の適用
- set-display-never-ac.command … AC給電時の画面オフを無効に戻す
- run-macos27-update.command … macOS アップデート用

※ 接続先のアドレスやユーザー名などの実値は、コードに直書きしていません。
mac-ssh は同じフォルダの config.local.sh（.gitignore 対象・Git 管理外）から実値を読み込む構造で、
リポジトリには config.local.example.sh というテンプレートだけを置いています
（手元の実働版とは別物です）。
