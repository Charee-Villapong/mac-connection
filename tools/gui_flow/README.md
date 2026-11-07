# gui_flow（LINE / Discord の一気通貫 GUI フロー）

LINE と Discord へのメッセージ送信を、VM 側の Python プログラム 1 回の呼び出しで実行するための CLI です。

従来はエージェントが「キュー投入 → 撮影 → OCR → 判断」をステップごとに往復していて遅く、途中の判断ミスも起きていました。このプログラムはその往復を内部に閉じ込め、エージェントの仕事を「プログラムを 1 回呼んで、結果 JSON を読む」だけにします。

## 使い方

事前に設定ファイルを用意します（接続先・座標・宛先名などの環境依存値はコードに書かず、git 管理外の `config.local.json` から読みます）。

```bash
cp config.example.json config.local.json   # 実値に書き換える
```

```bash
# 本人の承認記録を作る (post / send の前に必須。Mac には接続しない)
python3 gui_flow.py discord approve --text-file msg.txt
python3 gui_flow.py line    approve --text-file msg.txt --expires 2026-11-05T13:00:00+09:00

# 入力だけ (送信しない)
python3 gui_flow.py discord input  --text-file msg.txt
python3 gui_flow.py line    input  --text-file msg.txt

# 最終検閲だけ (mode=field: 入力欄 / mode=bubble: 送信後の吹き出し)
python3 gui_flow.py discord verify --mode field --text-file msg.txt

# 入力 + 送信 + 送信後検閲まで通す (承認記録が無いと送信しない)
python3 gui_flow.py discord post   --text-file msg.txt
python3 gui_flow.py line    post   --text-file msg.txt

# 実働テスト用: 入力 → 検閲 → 欄を空に戻す (送信はしない)
python3 gui_flow.py discord test   --text-file msg.txt

# 送信キーだけ (欄の文面に承認記録があるときだけ送る) / 欄を空にするだけ
python3 gui_flow.py discord send
python3 gui_flow.py discord clear

# 慎重モード: 座標の再学習 (送信はしない)
python3 gui_flow.py discord careful
python3 gui_flow.py line    careful

# 宛先の指定 (どのアクションにも付けられる。未指定なら既定の宛先)
python3 gui_flow.py line    post    --to example-name --text-file msg.txt
python3 gui_flow.py line    approve --to example-name --text-file msg.txt

# 辞書に無い宛先は、careful の再学習フローで座標を再取得して登録する
python3 gui_flow.py line    careful --to 新しい宛先の名前
```

結果は標準出力に JSON で返ります（合否・チェック内訳・各ステップの所要秒数・送信成功時はスクショの保存先パス）。終了コードは 0 = 全チェック合格、2 = 異常検知または失敗です。結果 JSON には `recipient`（解決された宛先 ID）と `recipient_requested`（`--to` の指定そのまま）が入ります。

## 結果JSONの `error` 一覧

結果 JSON トップレベルの `error` に入りうる固定コードは 25 種です。成功時は `null` です。汎用例外時は、固定コードではなく例外型名とメッセージを組み合わせた文字列（例: `ExceptionType: message` 形式）が入る場合があります。また、`input` / `verify` / `clear` / `test` の単独失敗では `error` が `null` のままの場合があり、その場合は `checks` を見て失敗箇所を確認します。

| コード | 出る段階 | 条件 |
|---|---|---|
| `unknown_recipient` | `main`（`approve` と、`careful` 以外の全アクションの開始時） | `--to` で指定した宛先が `recipients` 辞書に無い（`approve` では承認記録を作らず、他のアクションでは Mac に触らず停止する） |
| `connect_failed` | `main`（接続確認） | Mac への接続確認に失敗した |
| `approval_missing` | `main`（`post` / `send` の承認ゲート） | 承認記録が無い／期限切れ／使用済み等で承認ゲートを通らなかった（`post` は入力前、`send` は欄の文面に対する承認が無い場合） |
| `field_readback_failed` | `main`（`send`） | 入力欄の文面を読み戻せなかった |
| `overlay_unresolved` | `main`（`careful`） | 覆いの汎用解消を試みても解消できずに停止した |
| `careful_failed` | `main`（`careful`） | 再学習・覆い解消以外の理由で `careful` が失敗した |
| `timeout` | `main`（全アクション共通） | SSH／キュー等の処理がタイムアウトした |
| `needs_calibration` | `main`（`post` / `input` / `send`） | 停止理由が座標・形状の疑い（座標系チェックのみ不合格／入力しても欄が空のまま／ウィンドウフレームが学習値から許容差超でずれた）の場合に、元の `error` から差し替わる |
| `nav_scrutiny_failed` | `post`（nav 精査） | 精査が理由コードなしで失敗した場合のフォールバック（現行Runnerでは通常発生しない） |
| `nav_scrutiny_unreadable` | `post`（nav 精査） | トーク一覧の撮影・OCRができなかった（ホーム遷移後の再撮影・再OCRができなかった場合を含む） |
| `nav_home_click_failed` | `post`（nav 精査のホーム救済） | 候補 0 件で `home_click` 設定がある場合に、ホームボタンのクリックに失敗した |
| `nav_recipient_not_found` | `post`（nav 精査） | トーク一覧に宛先と完全一致する行が 0 件だった（ホーム救済の再探索でも 0 件だった場合と、`home_click` 未設定で直ちに停止する場合を含む） |
| `nav_recipient_ambiguous` | `post`（nav 精査） | トーク一覧に宛先と完全一致する行が 2 件以上あり特定できなかった |
| `nav_scrutiny_click_failed` | `post`（nav 精査） | 候補が 1 件だが保存座標と違う行で、その行への修正クリックに失敗した |
| `wrong_recipient` | `post`（入力前のヘッダ確認／送信前検閲） | ヘッダの宛名が想定宛先と不一致だった（別人の宛名が読めた場合） |
| `recipient_header_unreadable` | `post`（入力前のヘッダ確認／送信前検閲） | ヘッダ確認の撮影・OCRが全回できず、宛名を確認できなかった（`nav_click` のクリック失敗を含む） |
| `pre_send_failed_stop` | `post`（送信前検閲） | 送信前検閲が、リトライ対象外の理由で失敗した（宛先系の失敗を除く） |
| `pre_send_failed_retry_exhausted` | `post`（送信前検閲の高速リトライ） | 本文不一致のリトライが上限（`max_pre_send_retries`）まで達しても合格しなかった |
| `retry_clear_failed` | `post`（高速リトライ） | リトライ時の入力欄の全消去（`clear`）に失敗した |
| `send_failed` | `post`（送信） | 送信キーの送信処理に失敗した |
| `post_send_verify_failed` | `post`（送信後検閲） | 送信後の吹き出し検閲が不合格だった |
| `recipient_search_failed` | `careful` の再学習フロー | 画面を撮影できずトーク一覧を探せなかった、または特定した行のクリックに失敗した |
| `recipient_not_found` | `careful` の再学習フロー | トーク一覧に指定名の行が見つからなかった |
| `recipient_ambiguous` | `careful` の再学習フロー | 同じ名前の行が複数ある、または完全一致する行が無く部分一致しかなく特定できなかった |
| `recipient_header_mismatch` | `careful` の再学習フロー | 開いた先のヘッダが完全一致も前方一致（余り上限以内）もしない |

## 宛先の変数化（recipients 辞書と `--to`）

宛先はコードではなく config の変数として持ちます。アプリごとに `recipients` 辞書（キー = 宛先 ID、値 = その宛先固有の値だけ）と `default_recipient` を持ちます。

```json
"apps": {
  "line": {
    "input_click": [800, 780],
    "learned_frame": [0, 30, 1420, 850],
    "default_recipient": "宛先ID",
    "recipients": {
      "宛先ID": {"aliases": ["照合用エイリアス", "別表記"], "nav_click": [210, 160]}
    }
  }
}
```

- 宛先固有の値は辞書側だけに置きます: `aliases`（検閲でヘッダの宛名と照合するエイリアス群）と `nav_click`（その宛先のトークを開くための座標）。ウィンドウ全体で共通の値（入力欄座標 `input_click`、学習済みフレーム `learned_frame` 等）はアプリ側に残します。
- `--to` は宛先 ID の完全一致 → ID・エイリアスの正規化一致、の順で解決します。未指定なら `default_recipient` です。
- 承認ゲートは宛先ごとに効きます（ある宛先の承認記録では、別の宛先へは送れません）。承認記録の `recipient` は、解決された宛先のエイリアス先頭です。
- 旧形式（アプリ直下の `recipient_aliases` / `nav_click`）の config も、既定宛先 1 件だけの辞書として読み替えられます（後方互換）。
- `default_recipient` が辞書に無い ID の場合は、辞書の先頭エントリにフォールバックします。
- **辞書に無い宛先**を `--to` で指定した場合、careful 以外のアクションは Mac に触らず `error=unknown_recipient` で停止します。まず `careful --to <宛先>` で座標を再取得して登録してから送る、が手順です（登録の流れは慎重モードの節を参照）。

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

- **承認ゲート（`post` / `send`）**: 「送信前の本人確認」をエージェントの注意力ではなくプログラムの構造で担保します。`approve` で作成した承認記録（文面の SHA-256 + アプリ + 宛先に紐づき、有効期限・使用済みフラグを持つ）が無い文面は送信できません。
  - `post` は接続後・入力前に照合し、記録が無い／期限切れ／使用済みの場合は `approval_missing`（理由つき）で停止します。入力欄には一切触りません。
  - `send` は欄の文面を読み戻し、その文面の承認があるときだけ送信キーを押します。
  - 送信が成功したら承認記録を使用済みにします（同じ承認で二重送信できません）。送信前検閲で止まった場合は消費しません。
  - 有効期限の既定は作成から 24 時間です。`--expires` で ISO8601 指定もできます（オフセット無しは JST とみなします）。文面のハッシュは前後の空白・改行を除いた本文に対して計算します。
  - 承認記録はローカル状態として `tools/gui_flow/approvals/` に置き、git 管理外です。
- **送信後スクショの受け渡し（`post` / `send`）**: 送信が成功したら、送信後検閲で撮影したキャプチャが VM 側の `~/workspace/mac-shots/gui-flow-<app>-<YYYYMMDD-HHMMSS>.png`（JST）に保存され、結果 JSON の `screenshot` フィールドにその絶対パスが入ります。エージェントはこのパスを開いてチャットで提示するだけです（Mac 側の作業フォルダから最新キャプチャを探す手順は不要になりました）。送信しなかった場合（承認ゲート停止・検閲 NG 等）は `screenshot` は `null` です。取得・保存に失敗した場合も送信自体は成功として扱い、`screenshot` は `null` で、理由が `checks` の `screenshot` 項目に残ります。
- 異常（宛先不一致 / 入力欄に残りあり / ウィンドウが見つからない / 画面が読めないなど）を検知したら、**送信せずに停止**し、結果 JSON で報告します。
- **宛先確認はヘッダ帯の切り出し OCR 専用（LINE / Discord 共通）**: verify / post の宛先判定（`Runner.verify` の `recipient` チェックと `Runner.check_recipient_header()`）は、撮影 PNG 全体の OCR からヘッダ領域を座標で拾う方式ではなく、撮影 PNG からヘッダ帯だけを切り出して OCR した文字列だけで判定します。切り出しは Mac 側スクリプトが `sips` で行い（`-header.png` → `-header-ocr.txt`）、範囲は従来のヘッダ領域定義の読み替えで、左端が PNG 幅 × `talk_list_width_ratio`、高さが `relearn_header_max_y` を窓フレーム高で換算した帯です。判定は切り出し文字列を行単位の run にまとめ、完全一致（exact）、または余り `relearn_header_prefix_max_extra` 文字以内の前方一致（prefix）で行います。アプリ別キー `header_candidate_expansion` が真のアプリ（例: Discord）では、判定に渡す候補を「従来の run 群 + 各 OCR 項目そのもの + 各 run／項目の空白区切りの第 1 トークン」へ拡張します（ヘッダ名がアクティビティ表示と連結して読まれるケースへの対応。判定規則と余り上限は従来通りで、証跡に残るヘッダ文字列は実際の OCR run のままです）。このキーが無い／偽のアプリ（LINE を含む）では候補は従来の run 群のまま変わりません。全体 OCR 側（トーク一覧など）に宛先名が写っていても合格しません。切り出し OCR に文字列が無い・取得できない場合も不合格（確認できず）です。チェック結果には方式・余りと、切り出しで実際に読めた run 文字列（`actual_header`）が残ります。本文照合・読み戻し等の他のチェックは従来通り全体 OCR で行います。再学習フロー（`relearn_recipient`）のヘッダ確認は、この切り出しOCR方式の対象外です（従来通り全体OCRに対する座標スコープ（`header_region_runs`）で判定します）。ただし一致規則は対象外ではありません。再学習側も同じく、完全一致（exact）、または余り `relearn_header_prefix_max_extra` 文字以内の前方一致（prefix）で判定し、このキーを verify/post と共有します。verify/post との違いは、ヘッダ文字列の取得方式だけです。
- **ナビゲーション前の座標精査（`post`）**: `post` はヘッダ確認の前に `Runner.scrutinize_nav()` で、保存済み `nav_click` が宛先本人の行を指しているかをトーク一覧の OCR で精査します。一覧から宛先エイリアスと**完全一致**する行を探し（部分一致のみは候補にしません。再学習の一覧探索と同じ規則）、候補が 0 件なら、ホーム救済を行います: `home_click` 設定がある場合のみ、ホームボタンへ1回だけ遷移して再探索します（ホームクリックに失敗した場合は `error=nav_home_click_failed`、遷移後の撮影・OCRができなければ `error=nav_scrutiny_unreadable` で停止します）。再探索でも候補が無ければ `error=nav_recipient_not_found` で停止し、`home_click` 未設定なら直ちに `error=nav_recipient_not_found` で停止します。候補が 2 件以上なら `error=nav_recipient_ambiguous`、撮影・OCR ができなければ `error=nav_scrutiny_unreadable` で、設定変更はせず停止します。候補が 1 件で保存座標と同じ行（縦座標差が `nav_scrutiny_row_tolerance_px` 以内）なら通過します。候補が 1 件だが保存座標と違う行（＝並び替え等で保存座標が別人の行を指している）なら、その行をクリックして開き、`recipients` 辞書の `nav_click` を候補座標へ更新して保存します（書き込み前にバックアップ、学習記録へ追記。再学習の登録と同じ作法）。修正クリックに失敗した場合は `error=nav_scrutiny_click_failed` で停止します。`nav_click` 未設定で候補が 1 件なら、それを正として通過します。精査は文字入力・送信キーを絶対に行いません（行を開くクリックのみ）。この精査は Runner 共通経路で、一覧の幅比率はアプリ別に解決されるため、LINE / Discord の両方に効きます。
- **入力前のヘッダ確認（`post`）**: `post` は入力を始める前に `Runner.check_recipient_header()` でヘッダだけを確認します（`nav_click` があればその行を開いてから、各回の撮影ごとに `Runner.capture_header_runs()` でヘッダ帯の切り出し OCR を取得して判定）。ここで不一致なら `error=wrong_recipient`、撮影自体ができなければ `error=recipient_header_unreadable` で停止し、入力・送信・後片付けは一切しません（まだ何も入力していないため消すものはありません。結果 JSON の `wrong_recipient.cleaned` は `false`）。送信前検閲で宛先違いが分かった場合（入力済み）の後片付けは従来通りです。
- `post` は送信前検閲（入力の読み戻し照合 + 欄の OCR 照合）の全チェックが合格したときだけ送信キーに進みます。
- **高速リトライ（`post` のみ）**: 送信前検閲が「本文の不一致」（読み戻し不一致・行単位照合 NG など）だけで落ちた場合は、慎重プロセスへ落とす前に、プログラム内で自動リトライします。手順は「入力欄を全消去（消すのはこの run で自分が入力した分だけ）→ 同じ文面を再入力 → 再検閲」で、最大 2 回までです。合格すればそのまま送信へ進み、2 回とも NG なら停止して報告します。リトライ 1 回の追加時間は実測で Discord 約 25 秒 / LINE 約 22 秒です。
- **リトライしない異常（即停止）**: 宛先不一致、入力開始時に他人の下書き等の残りがあった場合、ウィンドウが見つからない、画面が読めない（ロック等）。これらは従来通り即停止します。
- **宛先違いの後片付け（`post`）**: 宛先不一致を検知した場合、送信キーは絶対に押さず、その会話の入力欄に入っている「自分が入れた文」だけを消去して空に戻し（読み戻しで空を確認）、停止して報告します。再実行はしません。結果 JSON の `wrong_recipient` に想定宛先と実際の宛先（ヘッダの OCR 証拠）が入ります。入力は欄が空でなければ進まないため、相手の既存の下書きに触れることはありません。エイリアスの学習は自動で行いません（表示名変更などの同一人物ケースは、本人の承認があった時だけ別途学びます）。
- 送信後検閲 NG は自動で送り直さず、報告のみとします。
- 入力欄に残りがある場合は、消去も入力もせず異常として報告します（下書きを勝手に消さないため）。
- 結果 JSON の `retry` フィールドに、リトライ回数と各失敗回の差分要約（落ちたチェック名と根拠）が入ります。

## 慎重モード（careful）＝ 学習の経路

高速パスが対応できないケース（レイアウト変化で学習済み座標が合わなくなった等）に当たったら、慎重モードに入って段階的に解決し、**学んだ内容を設定へ保存して、次回からそのケースを正常系として処理できるようにします**。慎重モードは例外処理ではなく、正常系を広げるための学習の経路です。

```bash
python3 gui_flow.py discord careful
```

処理の流れ:

1. **フレーム取得と比較**: 対象ウィンドウを撮影し（ステップごとに証拠キャプチャを `~/workspace/mac-shots/careful-<app>-<日時>-<step>.png` へ保存）、ウィンドウフレームを config の学習済みフレームと比較します。
2. **入力欄の探索（プローブ）**: フレーム相対の候補点（現行の学習済み座標 → 下部帯のグリッドの順）を 1 本のキューで順に試し、クリック → プローブ文字列の入力 → センチネル読み戻しで「フォーカスが当たったか」を検証します。当たった候補が正解です。試行のたびに入力した分は必ず消去し、終了時にも欄が空であることを確認します。**送信キーは絶対に押しません**（プローブ用スクリプトに送信キーの呼び出しは含まれず、テストでも担保しています）。欄に既存の残りがある場合は何も触らず停止します。
2.5. **覆いの汎用解消**: プローブが全候補で入力欄に到達できなかった場合、異常の種類を「覆いの疑い」と分類し、対処として汎用の解消（Esc キー＋ウィンドウ上部帯への外側クリック。個別のポップアップの文言・形・専用座標には依存しません）を行い、解消前後の画面サムネイル差分で「クリーンになったか」を再確認してから再プローブします。画面に変化が無ければ覆いが残っているとみなして再プローブせず停止します。試行回数には上限（`dismiss_retries`）があり、上限後は無理に進めず `error=overlay_unresolved` で停止して報告します（欄に既存の残りがある場合は従来通り何も触らず停止し、解消も試みません）。
3. **学習の保存**: 正解座標（`input_click`）とフレーム（`learned_frame`）を `config.local.json` へ書き込みます。書き込み前に `config.local.json.bak-<日時>` のバックアップを取り、学習記録を `tools/gui_flow/learned_cases.jsonl`（git 管理外）へ 1 行ずつ追記します（日時・アプリ・項目・旧値・新値・理由）。
4. **自己確認**: 固定文（設定で変更可能）で「入力 → 検閲 → 空に戻す」を実行し、正常系に戻ったことを証明します。結果 JSON には `learned`（旧値→新値）、`self_check`（合否）、`screenshot`（代表キャプチャのパス）が入ります。

**高速パスからの接続**: `post` / `input` / `send` の停止理由が座標・形状の疑い（検閲の座標系チェック＝欄の状態・本文照合・読み戻しだけが落ちた／入力しても欄が空のまま／ウィンドウフレームが学習値から許容差超でずれた）の場合、結果 JSON の `error` は `needs_calibration` になり、`checks` の `calibration` 項目に疑いの内容が残ります。これが careful を呼ぶ合図です。宛先違い・欄の残り・構造エラーは学習対象にせず、従来通り人の慎重プロセスへ回します。

### 未知の宛先の再学習フロー（`careful --to <宛先>`）

`--to` で辞書に無い宛先が指定された場合、careful は通常の座標探索の前に「再学習フロー」に入ります。これは抽象的な学習ではなく、**画面を撮影して座標を再取得し、それを新しい変数として config に登録する作業**です。登録する変数は主に、その宛先のトークを開くための座標（トーク一覧での位置 = `nav_click`）と、照合用エイリアス（`aliases`）です。

1. **撮影と行の特定**: 対象ウィンドウを撮影して OCR し、トーク一覧の領域（窓幅 × `talk_list_width_ratio` の範囲）から、指定名と**完全一致**する行を探します。判定は「行の中で横に連続した文字列」単位で行います（名前と人数表記が別々の OCR 項目に割れて返ってきた場合は、連結した文字列で判定します）。名前を含むだけの文字列（部分一致）しか無い場合は、より長い名前の一部かグループ名（「○○と他2名」等）の可能性があり特定できないため、曖昧として停止します。
2. **座標の再取得とオープン確認**: 特定した行の中心座標をクリックしてトークを開き、開いた先のヘッダの宛名が、指定名と完全一致（exact）するか、または指定名で始まり余りが `relearn_header_prefix_max_extra` 文字以内（既定2）の前方一致（prefix）をするか、OCRで確認します。ヘッダ確認は、トーク一覧より右側（開いたトーク側）の領域に限定します（一覧は開いた後も左に残るため、一覧側の名前では合格しません）。名前を含むだけの部分一致や、余り上限を超える前方一致では、グループトーク等を取り違えるため不十分とします。これは一覧探索側の完全一致必須（手順1）とは別の段階の規則です。
3. **変数の登録**: 一致したら、辞書キー = 指定名で `recipients` へ新規登録します（`nav_click` = 再取得した座標、`aliases` = 指定名＋ヘッダで読めた名前。ただしヘッダで読めた名前を加えるのは完全一致（exact）の場合のみで、前方一致（prefix）の場合は指定名だけを使います）。書き込み前にバックアップを取り、学習記録にも追記します。その後、通常の慎重モード本体（入力欄の探索 → 自己確認）へ進みます。なお登録後に自己確認が失敗しても登録自体は config に残ります（次回からは既知宛先として扱われます）。
4. **登録せず停止するケース**: 一覧に見つからない場合は `error=recipient_not_found`、同じ名前の行が複数ある／部分一致しかなく特定できない（グループ等で曖昧）場合は `error=recipient_ambiguous`、開いた先のヘッダが完全一致も前方一致（余り上限以内）もしない場合は `error=recipient_header_mismatch`、撮影やクリック自体が失敗した場合は `error=recipient_search_failed` で、いずれも何も登録せず停止して報告します。

## 調整用変数（コードではなく設定で変える）

調整用の数値はコードに直書きせず、すべて `config.local.json` の変数として持ちます。**新しいケースへの対応は「コード改修」ではなく「変数の変更」で済む構造**にしています。config に値が無いキーは、下記の既定値（＝現行の挙動）で動きます。型と既定値は `config.example.json` に示します。

### `tunables`（全体の調整値）

| キー | 既定値 | 意味 |
|---|---|---|
| `ssh_timeout` | 240 | SSH / キュー 1 回のタイムアウト（秒） |
| `connect_timeout` | 60 | 接続確認のタイムアウト（秒） |
| `post_send_wait` | 1.0 | 送信キーを押してから送信後検閲までの待機（秒） |
| `max_pre_send_retries` | 2 | 送信前検閲の高速リトライ上限 |
| `approval_ttl_hours` | 24 | 承認記録の有効期限（時間） |
| `ocr_threshold` | 0.8 | 検閲の 1 行目・アンカーの一致しきい値 |
| `strong_threshold` | 0.8 | アンカー候補（強一致）のしきい値 |
| `short_line_threshold` | 0.9 | 短い行の一致しきい値 |
| `long_line_threshold` | 0.75 | 通常行の一致しきい値 |
| `short_line_max_len` | 8 | 「短い行」とみなす最大文字数（空白除去後） |
| `leak_threshold` | 0.5 | 送信後検閲で「欄に本文が残っている」とみなす一致度 |
| `talk_list_width_ratio` | 0.32 | トーク一覧とみなす横範囲（窓幅比）。再学習では一覧／ヘッダ領域の境界、verify/post ではヘッダ帯切り出しの左端として共有。`apps.<app>` の同名キーでアプリ別に上書き可（一覧の幅はアプリのレイアウト依存。例: LINE は 0.27、Discord も 0.27＝1440pt 窓で切り出し左端が約 389pt となり、ヘッダ名の開始位置より左に収まる） |
| `header_candidate_expansion`（`apps.<app>` 直下） | 無効（キー無し） | 真のアプリでのみ、ヘッダ宛先判定の候補を run 群に加えて OCR 項目と空白区切り第 1 トークンへ拡張する。Discord のみ `true`（`config.example.json` 参照） |
| `relearn_header_max_y` | 260 | 「ヘッダ」とみなす縦範囲（窓相対 px）。再学習ではヘッダ領域の上限、verify/post では切り出し帯の高さ（窓フレーム高で換算）のフォールバックとして使うキー（verify/post の切り出し高さは `header_verify_crop_max_y` で個別に上書き可） |
| `header_verify_crop_max_y`（`apps.<app>` 直下 / `tunables` どちらでも可） | 無し（未設定なら従来通り `relearn_header_max_y` の解決結果） | verify / post のヘッダ帯切り出しの高さ（窓相対 pt、窓フレーム高で換算）だけを上書きするキー。解決順は `apps.<app>` の同キー → `tunables` の同キー → `apps.<app>` の `relearn_header_max_y` → `tunables` の同キー → 既定 260 の順で最優先。再学習のヘッダ領域には影響しない。LINE では宛先名が窓上端から約 50–75pt にあり、100pt の帯ならメッセージ欄を含まないため、複数行の入力でメッセージ欄がずれても名前の読みがぶれない（`config.example.json` の line 節で 100 を例示） |
| `relearn_header_prefix_max_extra` | 2 | ヘッダ確認で前方一致を許す余りの最大文字数。**再学習と verify/post のヘッダ確認で共有**するキー（verify/post 用の新規キーは作らない） |
| `talk_row_merge_px` | 12 | 行判定（同じ行とみなす縦間隔と、横に連続した文字列とみなす横間隔、px）。再学習と verify/post のヘッダ確認の run 化で共有 |
| `nav_scrutiny_row_tolerance_px` | 36 | nav 精査で、保存済み `nav_click` と宛先本人の行を「同じ行」とみなす縦座標差の許容差（px）。行間隔（約 72px）の半分で、隣の行は別人の行として検出できる値 |

### アプリ別 `careful`（慎重モードの調整値）

| キー | 既定値 | 意味 |
|---|---|---|
| `frame_tolerance` | 40 | フレーム比較の許容差（px） |
| `probe_text` | `GF_PROBE_8f3a` | フォーカス判定用のプローブ文字列（改行なし） |
| `probe_x_ratios` | `[0.5, 0.35, 0.65, 0.25, 0.75]` | 探索グリッドの横位置（フレーム幅比・試行順） |
| `probe_y_offsets` | アプリ別（Discord `[48, 30, 70, 16, 95, 130]` / LINE `[120, 95, 145, 70, 170, 200]`） | 探索グリッドの縦位置（フレーム下端からの距離 px・試行順） |
| `probe_max_candidates` | 36 | 候補点の上限（既定グリッド総数 31 を覆う値にすること） |
| `self_check_text` | アプリ別の固定文 | 自己確認で使う文面（形式ルールに沿ったもの） |
| `dismiss_retries` | 1 | 覆いの汎用解消 → 再プローブの試行回数 |
| `dismiss_click_x_ratio` | 0.5 | 外側クリックの横位置（フレーム幅比） |
| `dismiss_click_y_offset` | 16 | 外側クリックの縦位置（フレーム上端からの px） |
| `dismiss_thumb_width` | 160 | 再確認用サムネイルの幅（px） |
| `dismiss_change_threshold` | 0.005 | 「画面が変わった（クリーン）」とみなす差分比率 |

座標そのものも変数です: `apps.<app>.input_click`（入力欄のクリック点）と `apps.<app>.learned_frame`（学習済みウィンドウフレーム `[x, y, w, h]`）は careful が自動で更新します。宛先ごとの `apps.<app>.recipients.<宛先ID>.nav_click`（トークを開く座標）は、未知宛先の再学習フローが自動で登録します。

## テスト

単体テスト (Mac 接続が要る部分はモックで切り分けています):

```bash
python3 -m pytest tools/gui_flow/tests -q
```

コードの整形は Black + isort（`--profile black`）を標準とします。

## 注意

- Mac 側の既存ツール（GuiHelper / KeyHelper / ScreenCheck など）には変更を加えていません。このプログラムは VM 側から既存の仕組みを呼ぶだけです。
- `config.local.json` は git 管理外です。リポジトリに含まれるのは雛形 `config.example.json` のみです。
