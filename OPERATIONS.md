# 運用手順

## 1. 3700xの定期実行

定期実行の登録は、中央リポジトリ `D:\git\priconner_clan_battle_task_scheduler` で一元管理します。`projects.json` にプロジェクトごとのジョブ、開始時刻、間隔、実行時間上限を定義し、`register_scheduler.ps1` が日次コントローラーと設定済みジョブを登録します。

| タスク | ステージ | 間隔 | 開始時刻 |
| --- | --- | ---: | ---: |
| `PriconnerClanBattle-Scheduler` | 個別タスクの無効化・期間判定・有効化 | 毎日 | 12:00 |
| `PriconnerClanBattle-priconner_tl_scanner-youtube-channel` | YouTubeチャンネル収集 | 15分 | 12:00〜 |
| `PriconnerClanBattle-priconner_tl_scanner-youtube-search` | YouTubeキーワード収集 | 5分 | 12:02〜 |
| `PriconnerClanBattle-priconner_tl_scanner-discord-channel` | Discordチャンネル収集 | 5分 | 12:01〜 |

```powershell
Set-ExecutionPolicy -Scope Process Bypass
Set-Location D:\git\priconner_clan_battle_task_scheduler
.\register_scheduler.ps1
```

このリポジトリの `register_monitor_tasks.ps1` は中央スクリプトへ委譲する互換ラッパーです。既存の呼び出しを維持する場合は、リポジトリ直下で `.\register_monitor_tasks.ps1` を実行できます。

既定では `www51` の対話ログオンで実行します。ログオフ中も実行したい場合は、必要な権限を確認したうえで `PRICONNER_SCHEDULER_LOGON_TYPE=S4U` を設定して中央スクリプトを実行します。

登録確認:

```powershell
Get-ScheduledTask -TaskPath '\' |
  Where-Object TaskName -like 'PriconnerClanBattle-*' |
  Get-ScheduledTaskInfo
```

## 2. 監視期間

`PriconnerClanBattle-Scheduler` は設定されたジョブのうち最も早い開始時刻に毎日起動します。起動時に全ジョブをいったん無効化し、日本時間の毎月22日00:00〜月末日（最大30日）23:59:59だけ個別ジョブを有効化します。トレーニング期間も同じ範囲に含まれます。

期間外は中央スケジューラーだけが起動し、個別ジョブは無効です。個別ジョブは `scheduler.py --run-job` 経由で起動し、実行時にも期間と開始時刻を再確認してから `monitor_runner.py --stages ...` を1回だけ実行します。

確認例:

```sh
python monitor_runner.py --stages youtube-search
```

## 3. Discord投稿

各ステージは、検出した投稿を `discord_queue.sqlite3` に登録します。`monitor_runner.py` はステージ成功後にキューを送信し、送信件数・再試行待ち・失敗件数を状態へ記録します。

Discord送信は各ステージ終了後にキューを送信します。専用の常駐・1分間隔タスクは登録しません。

投稿先は `discord_channels.json` の `scheduled_guild_keys` に従います。強制投稿、件数制限、削除、リセットなどの試験系フラグを使う場合は `test_guild_key` 側へ送信されます。

手動で保留キューを送信する場合:

```sh
python discord_queue.py
```

これは外部へ投稿する操作です。実行前にキュー内容と送信先を確認してください。送信処理は同時実行ロックを持つため、ステージ終了後の送信と手動送信が重なっても二重送信しません。

## 4. 状態・失敗確認

既定の状態保存先は次のとおりです。

```text
%USERPROFILE%\.local\state\priconner-tl-scanner\state.json
```

確認:

```powershell
Get-Content "$env:USERPROFILE\.local\state\priconner-tl-scanner\state.json"
```

中央スケジューラーのログと状態は次の場所です。

```text
D:\git\priconner_clan_battle_task_scheduler\logs\scheduler.log
D:\git\priconner_clan_battle_task_scheduler\logs\<project>\<job>.log
D:\git\priconner_clan_battle_task_scheduler\scheduler_state.json
```

主な状態値:

- `success`: 全ステージとDiscordキュー送信が成功
- `failed`: ステージまたはDiscordキュー送信に失敗
- `running`: 実行中。プロセス終了後も残る場合は、プロセスとロックを確認する

同時実行が疑われる場合:

```powershell
Get-Process python -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskPath '\' |
  Where-Object TaskName -like 'PriconnerClanBattle-*'
```

収集ステージは内部の処理単位です。中央スケジューラーにはYouTubeチャンネル収集とYouTubeキーワード収集の2ジョブを登録し、それぞれ異なる間隔で実行します。各ジョブは中央の `scheduler.py --run-job` を経由して `monitor_runner.py` を起動します。

YouTube登録チャンネルは、最新動画投稿日から `maintenance.inactive_days` 日を超えると通常走査を省略します。そのチャンネルの動画がYouTube検索で見つかった場合は、検索結果を保存し、最新投稿日を更新します。

YouTubeの公開対象月は通常、JSTの当月を自動使用します。試験時だけ `monitor_runner.py --period-month YYYY-MM --content-month YYYY-MM` で固定月を指定できます。対象外月のクラバト、`NGワード` シートに登録された他ゲーム・イベント動画は、スプレッドシート・Discordキューへ登録しません。

`YouTube動画` タブには、動画備考・概要欄・TL整形・投稿直前本文も保存します。RSSの簡易情報から概要欄を取得できない場合は、不完全な行やDiscord投稿を作成せず、その動画を保留してログに不足理由を出します。

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

`monitor_runner.py` はWindowsタスクから直接起動せず、中央スケジューラーの `scheduler.py --run-job` 経由で起動されます。

投稿せずに確認する場合は、`PRICONNER_NO_POST=1` などの試験用環境変数を確認してから実行します。試験フラグの組み合わせによってはSheetsへの書き込みや外部APIアクセスが発生するため、テスト用設定を分離してください。
