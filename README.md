# mac-connection

スマホの Muse から、自宅の MacBook Air を操作できるようにする仕組み一式です。2026年11月2日に構築しました。

接続は Tailscale + SSH が土台で、その上に画面操作ツール（クリック・文字入力・画面確認）を自作して載せています。LINE の送信や macOS のアップデートなども、この仕組みで画面を見ながら行います。

## 構成

- **muse-mac-setup-guide.pdf** — Mac と Muse をつなぐ設定ガイド（家族にも配布したもの）
- **mac-tools/** — 画面操作ツールのソースコード（Swift）
  - gui-helper … クリック・キー入力
  - screencheck-main … 画面キャプチャ（ScreenCaptureKit）
  - key-helper … キー送信（LINE の Cmd+Enter 送信など）
  - ocr … 画面の文字読み取り
  - scroll … 指定座標でのスクロール
- **tools/gui_flow/** — LINE / Discord の送信を 1 回の呼び出しで一気通貫実行する Python CLI（詳細は tools/gui_flow/README.md）
- **scripts/** — 接続用スクリプト、作業ログ窓、省電力設定、OSアップデート用など
  - 接続先などの実値はコードに直書きせず、.gitignore 対象の config.local.sh から読み込む構造（テンプレート config.local.example.sh のみ公開）
- **guide/** — 設定ガイドの原稿（HTML）とプレビュー画像
- **connectors/** — Mac のブラウザ操作（Playwright MCP）の接続メモ
- スクリーンショット類は公開対象外（手元でのみ保管）

## 同期の流れ

マスターは Claude の VM 側にあります。変更は VM からこのリポジトリへプッシュされ、Mac の `~/Desktop/muse/mac-connection` は pull で受け取ります。
