# mac-tools（画面操作ツールのソースコード）

Mac の画面をプログラムから操作するための、自作ツールのソースコード（Swift）です。

- gui-helper.swift … クリック・キー入力・文字入力
- screencheck-main.swift … 画面キャプチャ（ScreenCaptureKit でウィンドウ単位に撮影）
- key-helper.swift … 特定のアプリへのキー送信（ショートカット送信など）
- ocr.swift … キャプチャ画像の文字読み取り（OCR）
- winlist.swift … ウィンドウ一覧の取得

ビルド済みのアプリ本体は Mac 側（~/muse-tools）にあります。
