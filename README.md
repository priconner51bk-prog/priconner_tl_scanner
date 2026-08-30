# priconner_tl_movie_scanner

プリコネ関連の新着TLを各情報源から検出し、Google Sheetsへ記録してDiscordへ通知する監視ツールです。

## 運用上の安全策

- `monitor_runner.py` と各収集スクリプトは、それぞれ専用の実行中ロックを取得して同じ処理の重複起動をスキップします。
- ステージ名と動画URLは1回の走査内で重複排除します。YouTube検索件数には上限を設け、検索設定を大きくしても過剰な取得を抑えます。
- 登録済みチャンネルはチャンネルの `/videos` 一覧を新しい順に軽量取得します。既知の動画URLまたは期間境界に到達した時点でそのチャンネルの走査を終了するため、毎回の全件詳細取得やページカーソルに依存しません。
- チャンネル監視では、チャンネル登録自体を採用判断として扱います。動画タイトルに「プリコネ」やボス名が含まれない動画も検出対象にするため、概要欄だけに情報がある動画の取りこぼしを防ぎます。日付が一覧にない場合も新着候補として保持し、URLで次回以降の重複を防ぎます。
- 実行状態は `XDG_STATE_HOME` 配下（未設定時は `~/.local/state/priconner-tl-movie-scanner`）の `state.json` に保存します。認証情報、`config.ini`、状態ファイルはGitへ登録しません。

通常の実行:

```sh
python3 monitor_runner.py
```

月末期間の監視タスクを登録する場合:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\register_monitor_tasks.ps1
```

月末8日前の12:00から月末12:00まで、`worrychefs.py` は10分、
`youtube_channel.py` は30分、`youtube_search.py` は5分ごとに実行します。
`boss_names_sync.py` は毎日12:15に実行し、当月のボス名がマスターデータに
確認できた時点でスプレッドシートを更新します。
期間外の起動はラッパーが自動的にスキップします。

月末の最終日を除く直前8日間だけ定期実行する場合は、ラズパイのcrontabに
次の1行を登録します。`/path/to/priconner_tl_movie_scanner` は実際の配置先に
置き換えてください。crontabは毎日呼び出しますが、`scheduled_monitor.py` が
対象期間以外の処理を自動的にスキップします。

```cron
0 3 * * * cd /path/to/priconner_tl_movie_scanner && /usr/bin/python3 scheduled_monitor.py >> /path/to/priconner_tl_movie_scanner/monitor.log 2>&1
```

この判定では、31日ある月は23〜30日、30日ある月は22〜29日、通常年の2月は
20〜27日、うるう年の2月は21〜28日が対象で、月の最終日は対象外です。

スプレッドシートのチャンネル除外状態を更新する場合:

```sh
python3 sheets_maintenance.py
```

ボス名をゲーム内の更新後に同期する場合:

```sh
python3 boss_names_sync.py
```

毎回 `redive_master_db_diff` の最新コミットSHAを取得し、そのコミットのマスターデータ
から当月のボス名を確認します。確認できない場合はシートを変更せず、5分スリープして
最新コミットから再取得します。期日・日付・コミット日時には依存しません。

`[maintenance] inactive_days` 未満の新しい動画記録があるチャンネルは、自動除外印が解除されます。動画記録がないチャンネルや手動設定の除外印は変更しません。

動画の対象判定には、タイトルだけでなく概要欄・タグ・ボス名・TL用語も使用します。他ゲーム名を含む動画は減点して対象外にします。
