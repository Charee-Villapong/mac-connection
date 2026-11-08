# win_ops

Mac の窓・メニュー操作専用の単一 CLI（`win_ops.py`）。窓の一覧・撮影・AX
ツリー取得・要素のクリック・窓を閉じる操作だけを行い、文字入力や送信は
しません。LINE / Discord の送信と承認ゲートは `tools/gui_flow/` の担当で、
本ツールとは分離しています。仕様（完了の定義）は `SPEC.md`。

Mac への実行は gui_flow と同じ GUI エージェントキュー経由（SSH でキューへ
投入し、結果を回収）。結果は全コマンド共通で stdout に単一 JSON
（`ok` / `command` / `status`、失敗時は `error`、終了コード 2）を返します。

## 使い方

```sh
python3 win_ops.py list [--app NAME] [--onscreen-only]
python3 win_ops.py cap WINDOW_ID [--out PATH]
python3 win_ops.py ax --app NAME [--surface SURFACE] [--max-depth N]
python3 win_ops.py press --ref '@<snapshot_id>:e<n>'
python3 win_ops.py press --app NAME --name LABEL [--surface SURFACE]
python3 win_ops.py close (--id N | --title STR [--app NAME]) [--force]
```

- `list`: ScreenCheck の窓一覧を返す。
- `cap`: 窓を撮影し、`capture_dir`（または `--out`）に
  `win-ops-<id>-<日時>.png` で保存する。
- `ax`: agent-desktop の snapshot を境界情報つきで取得し、そのまま
  `snapshot` キーに入れて返す。実エンベロープ形（`data.tree`、要素は
  `ref_id`）と簡略形（`tree`、要素は `ref`）の両形式に対応する。
- `press`: ref 指定、または名前の完全一致で一意に定まる要素を click する。
  一意でない・無効化されている要素は押さない。
- `close`: 対象を一意に特定し、AX の閉じるボタン（窓フレーム左上 + 設定
  オフセット付近の `role=button`）を押して、再一覧で消えたことを確認する。

## 設定

`config.example.json` を `config.local.json`（git 管理外）にコピーして
使う。SSH の実値はそちらにだけ書く。座標オフセット・許容差・セッション名・
ターミナルアプリ名・シェル許可リストなどのケース依存値はすべて
`tunables` 側で変更する（コードの書き換えは不要）。

## close のプロセス確認の範囲

- プロセス確認は `tunables.terminal_apps` に含まれるアプリの窓だけが対象
  （それ以外は確認を適用せず進む）。
- 対象窓の tty は osascript で特定し、その tty 上のプロセスが
  `tunables.shell_commands` 以外を含めば busy として閉じない
  （`--force` で明示した場合のみ閉じる）。
- tty が特定できない場合は確認不能として扱い、`--force` なしでは閉じない。

## 注意

- 実地確認は読み取り系（list / cap / ax）まで。press / close の実地確認は
  行わない（受け入れテストは transport スタブで担保）。
- キューのスクリプト名は `.sh` 終端で一意、雛形の `__NAME__` は実名に
  置換し、アプリ名・タイトルは base64 経由で Mac 側へ渡す。
