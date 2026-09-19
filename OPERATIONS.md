# 運用手順

## 1. 3700xの定期実行

`register_monitor_tasks.ps1` は、現在のユーザーで次の4タスクを登録します。

| タスク | ステージ | 間隔 | 開始時刻 |
| --- | --- | ---: | ---: |
| `PriconnerTlScanner-YouTubeSearch` | `youtube-search` | 5分 | 12:00 |
| `PriconnerTlScanner-DiscordChannel` | `discord-channel` | 5分 | 12:01 |
| `PriconnerTlScanner-WorryChefs` | `worrychefs` | 10分 | 12:02 |
| `PriconnerTlScanner-YouTubeChannel` | `youtube-channel` | 30分 | 12:03 |

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

`scheduled_monitor.py` は、各月の最終日を除く直前8日間だけステージを実行します。

| 月の日数 | 実行対象日 |
| ---: | --- |
| 31日 | 23〜30日 |
| 30日 | 22〜29日 |
| 通常年の2月 | 20〜27日 |
| うるう年の2月 | 21〜28日 |

正確な判定は `last_day - 8 <= today < last_day` です。期間外のタスク起動は終了コード0でスキップし、外部サービスへ接続しません。

確認例:

```sh
python scheduled_monitor.py --date 2026-09-19 --stage youtube-search
python scheduled_monitor.py --date 2026-09-23 --stage youtube-search
```

2つ目は対象期間内のため、実際に外部サービスへ接続します。確認目的では日付だけのスキップ例を使用してください。

## 3. Discord投稿

各ステージは、検出した投稿を `discord_queue.sqlite3` に登録します。`monitor_runner.py` はステージ成功後にキューを送信し、送信件数・再試行待ち・失敗件数を状態へ記録します。

そのため、Discord投稿専用の別タスクはありません。YouTube、WorryChefs、Discordチャンネルの各タスクが、それぞれの処理後に投稿キューを送信します。

投稿先は `discord_channels.json` の `scheduled_guild_keys` に従います。強制投稿、件数制限、削除、リセットなどの試験系フラグを使う場合は `test_guild_key` 側へ送信されます。

手動で保留キューを送信する場合:

```sh
python discord_queue.py
```

これは外部へ投稿する操作です。実行前にキュー内容と送信先を確認してください。

## 4. GitHub Actions

`.github/workflows/monitor.yml` は次の2通りで動きます。

- `workflow_dispatch`: 手動実行。通常投稿、新規投稿、更新投稿、1件制限を選択できる。
- `schedule`: `17 3 * * *`（UTC、JSTでは12:17）に全ステージを1回実行する。

GitHub Actionsのスケジュールは遅延・スキップすることがあるため、3700xのタスクスケジューラを主系にしています。両方を同時に有効にすると、同じ検出結果を別環境で同時処理する可能性があるため、主系を3700xにする場合はActionsの定期実行を予備扱いにしてください。

Actionsには、[SECRET_INVENTORY.local.md](SECRET_INVENTORY.local.md) に記載したActions用Secretsが必要です。Actionsではローカルの `config.ini` や `.env` は存在しないため、Secretから一時ファイルと環境変数を構成します。

## 5. 状態・失敗確認

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

## 6. 手動実行と試験

全ステージを通常実行:

```sh
python monitor_runner.py
```

ステージを指定:

```sh
python monitor_runner.py --stages youtube-search
python monitor_runner.py --stages youtube-channel,worrychefs
```

`monitor_runner.py` の直接実行は月末期間の判定を行いません。通常の定期実行では、タスクが呼び出す `scheduled_monitor.py` を使用してください。

投稿せずに確認する場合は、`PRICONNER_NO_POST=1` などの試験用環境変数を確認してから実行します。試験フラグの組み合わせによってはSheetsへの書き込みや外部APIアクセスが発生するため、テスト用設定を分離してください。
