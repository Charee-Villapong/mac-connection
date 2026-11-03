# mac-tools（画面操作ツールのソースコード）

Mac の画面をプログラムから操作するための、自作ツールのソースコード（Swift）です。

- gui-helper.swift … クリック・キー入力・文字入力
- screencheck-main.swift … 画面キャプチャ（ScreenCaptureKit でウィンドウ単位に撮影）
- key-helper.swift … 特定のアプリへのキー送信（ショートカット送信など）
- KeyHelper-Info.plist … KeyHelper アプリのバンドル情報（公開版は汎用のバンドルIDに置き換え）
- ocr.swift … キャプチャ画像の文字読み取り（OCR）
- winlist.swift … ウィンドウ一覧の取得
- scroll.swift … 指定座標でのスクロール
- sck.swift / wincap.swift … 画面キャプチャ方式の初期検証用
- axtrust.swift … アクセシビリティ権限（AXIsProcessTrusted）の確認
- diag-capture.sh / pass0.sh / pass1.sh / pass2.sh … キャプチャ基盤の診断・段階確認用

ビルド済みのアプリ本体は Mac 側（~/muse-tools）にあります。
