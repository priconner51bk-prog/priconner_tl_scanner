# 運用手順

## 1. 3700xの定期実行

`register_monitor_tasks.ps1` は、現在のユーザーで毎月20〜30日に起動するブートストラップタスクを登録します。Windowsの仕様上、日付ごとに11個の起動タスクを作成します。ブートストラップが当日分のステージタスクを4個登録します。

| タスク | ステージ | 間隔 | 開始時刻 |
| --- | --- | ---: | ---: |
| `PriconnerTlScanner--Bootstrap-20`〜`30` | 当日タスク登録 | 月1回 | 12:00 |
| `PriconnerTlScanner-<Stage>-YYYYMMDD` | 各収集ステージ | 5〜30分 | 12:00〜12:03 |

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\register_monitor_tasks.ps1
```

タスクは `www51` の対話ログオンで実行する設定です。ログオフ中も実行したい場合は、パスワードをタスクへ保存する方式やサービスアカウント方式を別途設計してください。

登録確認:

```powershell
Get-ScheduledTask -TaskPath '\' |
  Where-Object TaskName -like 'PriconnerTlScanner-*' |
  Get-ScheduledTaskInfo
```

## 2. 監視期間

Windowsタスクスケジューラーは毎月20日〜30日に起動し、`scheduler_bootstrap.ps1` が当日分のステージタスクを登録します。22日〜30日は周期実行、20〜21日は1日1回です。登録されたタスクは`monitor_runner.py --stages ...`を直接起動します。

実在する日付だけが起動するため、2月は通常年が20〜28日、うるう年が20〜29日です。期間外のタスクは登録されません。

確認例:

```sh
python monitor_runner.py --stages youtube-search
```

## 3. Discord投稿

各ステージは、検出した投稿を `discord_queue.sqlite3` に登録します。`monitor_runner.py` はステージ成功後にキューを送信し、送信件数・再試行待ち・失敗件数を状態へ記録します。

そのため、Discord投稿専用の別タスクはありません。YouTube、WorryChefs、Discordチャンネルの各タスクが、それぞれの処理後に投稿キューを送信します。

投稿先は `discord_channels.json` の `scheduled_guild_keys` に従います。強制投稿、件数制限、削除、リセットなどの試験系フラグを使う場合は `test_guild_key` 側へ送信されます。

手動で保留キューを送信する場合:

```sh
python discord_queue.py
```

これは外部へ投稿する操作です。実行前にキュー内容と送信先を確認してください。

## 4. 状態・失敗確認

既定の状態保存先は次のとおりです。

```text
%USERPROFILE%\.local\state\priconner-tl-scanner\state.json
```

確認:

```powershell
Get-Content "$env:USERPROFILE\.local\state\priconner-tl-scanner\state.json"
```

主な状態値:

- `success`: 全ステージとDiscordキュー送信が成功
- `failed`: ステージまたはDiscordキュー送信に失敗
- `running`: 実行中。プロセス終了後も残る場合は、プロセスとロックを確認する

同時実行が疑われる場合:

```powershell
Get-Process python -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskPath '\' |
  Where-Object TaskName -like 'PriconnerTlScanner-*'
```

## 5. 手動実行と試験

全ステージを通常実行:

```sh
python monitor_runner.py
```

ステージを指定:

```sh
python monitor_runner.py --stages youtube-search
python monitor_runner.py --stages youtube-channel,worrychefs
```

`monitor_runner.py` はタスクスケジューラーからステージ単位で直接起動されます。

投稿せずに確認する場合は、`PRICONNER_NO_POST=1` などの試験用環境変数を確認してから実行します。試験フラグの組み合わせによってはSheetsへの書き込みや外部APIアクセスが発生するため、テスト用設定を分離してください。
