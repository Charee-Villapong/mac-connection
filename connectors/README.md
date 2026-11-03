# connectors（ブラウザ操作の接続メモ）

Mac 上の Chrome を Playwright MCP 経由で操作するための、接続メモとクライアントです。

専用プロファイルの Chrome を使い、画面に表示した状態（headed）で操作します。接続先は Tailscale の内側だけに限定されています。

※ 公開版では、接続先のアドレスなどの実値はコードに直書きせず、環境変数（MAC_PLAYWRIGHT_HOST）やローカル設定に分離してあります。
