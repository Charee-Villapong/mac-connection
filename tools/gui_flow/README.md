# gui_flow（LINE / Discord の一気通貫 GUI フロー）

LINE と Discord へのメッセージ送信を、VM 側の Python プログラム 1 回の呼び出しで実行するための CLI です。

従来はエージェントが「キュー投入 → 撮影 → OCR → 判断」をステップごとに往復していて遅く、途中の判断ミスも起きていました。このプログラムはその往復を内部に閉じ込め、エージェントの仕事を「プログラムを 1 回呼んで、結果 JSON を読む」だけにします。

## 使い方

事前に設定ファイルを用意します（接続先・座標・宛先名などの環境依存値はコードに書かず、git 管理外の `config.local.json` から読みます）。

```bash
cp config.example.json config.local.json   # 実値に書き換える
```

```bash
# 入力だけ (送信しない)
python3 gui_flow.py discord input  --text-file msg.txt
python3 gui_flow.py line    input  --text-file msg.txt

# 最終検閲だけ (mode=field: 入力欄 / mode=bubble: 送信後の吹き出し)
python3 gui_flow.py discord verify --mode field --text-file msg.txt

# 入力 + 送信 + 送信後検閲まで通す
python3 gui_flow.py discord post   --text-file msg.txt
python3 gui_flow.py line    post   --text-file msg.txt

# 実働テスト用: 入力 → 検閲 → 欄を空に戻す (送信はしない)
python3 gui_flow.py discord test   --text-file msg.txt

# 送信キーだけ / 欄を空にするだけ
python3 gui_flow.py discord send
python3 gui_flow.py discord clear
```

結果は標準出力に JSON で返ります（合否・チェック内訳・各ステップの所要秒数）。終了コードは 0 = 全チェック合格、2 = 異常検知または失敗です。

## 形式ルール (アプリ別・混同厳禁)

- **Discord**: コードブロックのみで送る。ブロックの先頭行は「[SYSTEM] AIエージェントからお知らせ」。本文は簡単な言葉で、「。」ごとに改行する。【AIエージェントからのお知らせ】という独立行は付けない。
- **LINE**: 1 行目は必ず「【AIエージェントからのお知らせ】」。その後に本文を続ける。

verify の 1 行目チェックもこの区別で実装しています。

## 仕組み

- Mac 側の手順はフェーズ単位で 1 本のキュー投入にまとめます（GUI エージェントキュー経由で GuiHelper / KeyHelper / ScreenCheck / OCR を呼ぶ）。キュー投入は「別名で書いてから mv」するアトミック方式で、書き込み途中のスクリプトが実行される競合を防ぎます。
- SSH は ControlMaster で多重化し、接続コストは初回だけにします。
- 高速パスでは中間キャプチャをしません。撮影 + OCR は最終検閲の 1 回だけです。入力欄の内容確認はクリップボードの読み戻し（データ確認）で行います。
- 選択・コピーなどのショートカットは KeyHelper の pid 宛て送信を使います（HID 経由の仮想キーイベントはアプリ内で効かないため）。
- キュー側シェルのロケールは C のため、pbpaste が日本語を Shift_JIS で返す問題があります。スクリプト内で UTF-8 ロケールを明示して回避しています。

## 安全原則

- 異常（宛先不一致 / 入力欄に残りあり / ウィンドウが見つからない / 画面が読めないなど）を検知したら、**送信せずに停止**し、結果 JSON で報告します。
- `post` は送信前検閲（入力の読み戻し照合 + 欄の OCR 照合）の全チェックが合格したときだけ送信キーに進みます。
- **高速リトライ（`post` のみ）**: 送信前検閲が「本文の不一致」（読み戻し不一致・行単位照合 NG など）だけで落ちた場合は、慎重プロセスへ落とす前に、プログラム内で自動リトライします。手順は「入力欄を全消去（消すのはこの run で自分が入力した分だけ）→ 同じ文面を再入力 → 再検閲」で、最大 2 回までです。合格すればそのまま送信へ進み、2 回とも NG なら停止して報告します。リトライ 1 回の追加時間は実測で Discord 約 25 秒 / LINE 約 22 秒です。
- **リトライしない異常（即停止）**: 宛先不一致、入力開始時に他人の下書き等の残りがあった場合、ウィンドウが見つからない、画面が読めない（ロック等）。これらは従来通り即停止します。
- 送信後検閲 NG は自動で送り直さず、報告のみとします。
- 入力欄に残りがある場合は、消去も入力もせず異常として報告します（下書きを勝手に消さないため）。
- 結果 JSON の `retry` フィールドに、リトライ回数と各失敗回の差分要約（落ちたチェック名と根拠）が入ります。

## テスト

単体テスト (Mac 接続が要る部分はモックで切り分けています):

```bash
python3 -m pytest tools/gui_flow/tests -q
```

コードの整形は Black + isort（`--profile black`）を標準とします。

## 注意

- Mac 側の既存ツール（GuiHelper / KeyHelper / ScreenCheck など）には変更を加えていません。このプログラムは VM 側から既存の仕組みを呼ぶだけです。
- `config.local.json` は git 管理外です。リポジトリに含まれるのは雛形 `config.example.json` のみです。
