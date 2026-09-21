# 運用手順

## 1. 3700xの定期実行

`register_monitor_tasks.ps1` は、現在のユーザーで毎月20〜30日に起動するブートストラップタスクを登録します。Windowsの仕様上、日付ごとに11個の起動タスクを作成します。ブートストラップが当日分のステージタスクを4個登録します。

| タスク | ステージ | 間隔 | 開始時刻 |
| --- | --- | ---: | ---: |
| `PriconnerTlScanner--Bootstrap-20`〜`30` | 当日タスク登録 | 月1回 | 12:00 |
| `PriconnerTlScanner--Collector-YYYYMMDD` | 全収集ステージ | 30分 | 12:00〜 |
| `PriconnerTlScanner--DiscordQueue-YYYYMMDD` | Discord送信キュー | 1分 | 当日12:01〜 |

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

Discord送信は収集ステージから独立した専用タスクで、1分ごとにキューを送信します。収集が長時間かかっても、完了済みの投稿は先に送信されます。各ステージ終了後の送信は補助的に残ります。

投稿先は `discord_channels.json` の `scheduled_guild_keys` に従います。強制投稿、件数制限、削除、リセットなどの試験系フラグを使う場合は `test_guild_key` 側へ送信されます。

手動で保留キューを送信する場合:

```sh
python discord_queue.py
```

これは外部へ投稿する操作です。実行前にキュー内容と送信先を確認してください。キュー送信タスクは同時実行ロックを持つため、前回の送信中に次回が起動しても二重送信しません。

スプレッドシートに保存済みの8月分を、本番のboss0〜5へ新規扱いでキューへ再構築する場合:

```sh
python rebuild_discord_queue_from_sheets.py --prepare --target-month 2026-08
```

この操作はYouTube・Discordの再検索を行わず、`YouTube動画`、`Discordスキャン`、`WorryChefs TL` の保存行だけを読みます。WorryChefsは保存済み行を全件対象にします。`--prepare` はキュー投入までで、送信は `python discord_queue.py` または有効な送信タスクが行います。

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

収集ステージは内部の処理単位です。スケジューラーには収集タスクを1つだけ登録し、内部で順番に実行します。

YouTube登録チャンネルは、最新動画投稿日から `maintenance.inactive_days` 日を超えると通常走査を省略します。そのチャンネルの動画がYouTube検索で見つかった場合は、検索結果を保存し、最新投稿日を更新します。

YouTubeは公開日だけでなく、`[youtube] content_month` と `[youtube] content_period_start/end`（トレーニング期間を含む）、動画内容も確認します。対象外期間・対象外月のクラバト、`NGワード` シートに登録された他ゲーム・イベント動画は、スプレッドシート・Discordキューへ登録しません。

`YouTube動画` タブには、動画備考・概要欄・TL整形・投稿直前本文も保存します。RSSの簡易情報から概要欄を取得できない場合は、不完全な行やDiscord投稿を作成せず、その動画を保留してログに不足理由を出します。保存済み本文がない行からのキュー再構築も停止します。

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
