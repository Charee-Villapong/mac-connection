# win_ops 仕様（完了の定義）

Mac の窓・メニュー操作専用の単一 CLI。gui_flow（LINE/Discord 送信・承認ゲート付き）
とは分離し、送信系・文字入力系の機能は持たない。Mac へは gui_flow と同じ
GUI エージェントキュー経由で実行し、結果は stdout に単一 JSON で返す。

## 共通

- 配置: `tools/win_ops/`（win_ops.py 本体、tests/、config.example.json、README.md）
- 全コマンドは stdout に単一 JSON オブジェクトを出す。必須キー: `ok`（bool）、
  `command`、`status`。失敗時は `ok=false` かつ `error` に理由、終了コード 2。成功時 0。
- キュー投入はアトミックに行い、スクリプト名は `.sh` で終わること、雛形の
  `__NAME__` は実名に置換すること（gui_flow と同じキュー手順）。
- キュー完了待ちが上限を超えたら `status="queue_timeout"`、`ok=false`。
- 設定は `config.local.json`（gitignore 対象、同梱は config.example.json のみ）。
  SSH の実値・接続先はコードに書かない。座標オフセット・許容差・待機秒数・
  セッション名・ターミナルアプリ名・シェル許可リスト等のケース依存値は
  すべて設定（tunables）側に持つ。
- Mac 上の実行ファイルは ScreenCheck.app と agent-desktop（いずれも
  ~/muse-tools 配下、パスは設定側）。

## コマンド

### list [--app NAME] [--onscreen-only]

- ScreenCheck `list` の出力を解析して `windows` 配列を返す。
  要素: `id`（int）、`app`、`title`、`frame{x,y,width,height}`、`onscreen`（bool）。
- `--app` はアプリ名の完全一致フィルタ。`--onscreen-only` は onscreen=true のみ。
- `win ` で始まらない行は無視する。出力が `ERROR` で始まる場合は
  `ok=false, status="screencheck_error"`。

### cap WINDOW_ID [--out PATH]

- ScreenCheck `cap` で窓を撮影し、base64 で VM 側へ回収、設定の capture_dir
  （または --out）に保存する。返却: `window_id`、`path`、`bytes`。
- 保存名は `win-ops-<id>-<YYYYMMDD-HHMMSS>.png`（JST）。
- 窓が無い（ScreenCheck の not found）場合は `ok=false, status="window_not_found"`。

### ax --app NAME [--surface SURFACE] [--max-depth N]

- agent-desktop `snapshot` を実行する。surface 既定は `window`
  （`menu-bar-extras` 等 agent-desktop の許容値すべて可）。境界情報を含める。
- agent-desktop が返した JSON を `snapshot` キーにそのまま入れて返す。
  向こうの `ok=false` はこちらの `ok=false, status="ax_failed"` に対応させる。

### press (--ref REF | --app NAME [--surface S] --name LABEL)

- `--ref` は agent-desktop の合格 ref（`@<snapshot_id>:e<n>` 形式）を click する。
- `--name` 形式は snapshot を取り、`name` が完全一致する要素を一意に特定して
  click する。一致 0 件は `status="element_not_found"`、2 件以上は
  `status="ambiguous_element"` で押さない。`states` に `disabled` を含む要素は
  押さない（`status="element_disabled"`）。
- snapshot と click は設定の同一セッション名を使う（ref の名前空間を揃える）。

### close (--id N | --title STR [--app NAME]) [--force]

- 対象は list の結果から一意に特定する。0 件は `status="window_not_found"`、
  2 件以上は `status="ambiguous_window"`（候補一覧を返す）。いずれも閉じない。
- プロセス確認: 対象アプリが tunables.terminal_apps に含まれる場合、その窓の
  tty 上のプロセスを調べる。tunables.shell_commands 以外のプロセスがあれば
  busy とし、`--force` なしでは閉じない（`status="busy"`、`processes` を返す）。
  ターミナルでないアプリは `process_check.applicable=false` として進む。
  tty が特定できない場合は確認不能として busy と同じ扱いにし、`--force`
  なしでは閉じない（`status="process_check_failed"`）。
- 閉じる実行は AX で行う: 対象窓の snapshot（境界つき）から閉じるボタンを
  特定する。規則: `role="button"` で、その中心が窓フレーム左上 +
  tunables.close_button_offset_px（既定 14）から許容差
  tunables.close_button_tolerance_px（既定 12）以内にあるもの。
  候補 0 件は `status="close_button_not_found"`、複数は
  `status="ambiguous_close_button"`。いずれも押さない。
- 実行後は list で再確認する。対象 id が消えていれば `verified=true`。
  残っていれば `ok=false, status="close_unverified"`。

## モジュール構成（テスト対象の公開関数）

- `parse_window_list(text) -> list[dict]`
- `resolve_window(windows, window_id=None, title=None, app=None) -> dict`
  （特定できない場合は `.status` を持つ `WinOpsError` を送る）
- `iter_elements(tree) -> Iterator[dict]` / `find_elements_by_name(tree, name)`
- `find_close_button_candidates(tree, frame, offset_px, tolerance_px) -> list[dict]`
- `busy_processes(ps_text, shell_commands) -> list[str]`
- `build_*_script(name, ...) -> str`（キュー投入スクリプト生成）
- `class WinOps`（transport を差し替え可能にし、各コマンドの結果 dict を返す）
- `main(argv) -> int`

## 範囲外（v1 では作らない）

- 文字入力・値設定・ドラッグ等の入力系、アプリの起動・終了、窓の移動・リサイズ。
- 実地確認は読み取り系（list / cap / ax）まで。press / close の実地確認は行わない。
