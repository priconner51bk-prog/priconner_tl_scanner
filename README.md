# priconner_tl_scanner

プリコネ関連の新着TLを各情報源から検出し、Google Sheetsへ記録してDiscordへ通知する監視ツールです。

## 運用上の安全策

- `monitor_runner.py` と各収集スクリプトは、それぞれ専用の実行中ロックを取得して同じ処理の重複起動をスキップします。
- ステージ名と動画URLは1回の走査内で重複排除します。YouTube検索件数には上限を設け、検索設定を大きくしても過剰な取得を抑えます。
- 登録済みチャンネルはチャンネルの `/videos` 一覧を新しい順に軽量取得します。既知の動画URLまたは期間境界に到達した時点でそのチャンネルの走査を終了するため、毎回の全件詳細取得やページカーソルに依存しません。
- チャンネル監視では、チャンネル登録自体を採用判断として扱います。動画タイトルに「プリコネ」やボス名が含まれない動画も検出対象にするため、概要欄だけに情報がある動画の取りこぼしを防ぎます。日付が一覧にない場合も新着候補として保持し、URLで次回以降の重複を防ぎます。
- 実行状態は `XDG_STATE_HOME` 配下（未設定時は `~/.local/state/priconner-tl-scanner`）の `state.json` に保存します。認証情報、`config.ini`、状態ファイルはGitへ登録しません。
- `config.ini.org` を `config.ini` にコピーして各種ID・認証情報・監視設定を記入します。監視設定はコマンドライン引数、環境変数、`config.ini`、既定値の順で優先されます。
- Discordの投稿・削除用Botトークンは `[discord] bot_token`、`DISCORD_BOT_TOKEN`、プロジェクト直下のGit管理外`.env`の順で設定できます（設定ファイルが優先）。`[discord_channel] token` は収集用で、投稿・削除には流用しません。トークン値はログへ出力しません。
- Discord通知は `timeout` 秒で打ち切り、`retries` 回まで待機時間を伸ばして再試行します。到着データをSheetsへ保存してからURL登録・通知を行います。
- 投稿の多重度はボス単位で分離できます。`boss1_tl`〜`boss5_tl` は別チャンネルのため、異なるボスの収集・投稿は多重化して構いません。同一ボスへの投稿は直列化し、API間隔と429の再試行を維持します。
- `discord_channels.json` は`production`と`experimental`の複数サーバーを登録できます。通常の定期実行は`scheduled_guild_keys`に指定した全サーバーへ投稿し、強制投稿・件数制限・リセットなどの試験系フラグがある実行は`test_guild_key`だけへ投稿します。
- 収集処理からのDiscord投稿は`discord_queue.py`のSQLiteキューへ登録され、`monitor_runner.py`の最後に単一ディスパッチャーが送信します。キューは送信先ごとの順序、429・通信失敗時の再試行、重複排除、添付画像を保持します。手動で送信する場合は`python3 discord_queue.py`を実行します。
- Discordチャンネル監視は`[discord_channel]`設定でサーバー・チャンネルを指定します。Discordユーザートークン（Bot登録不要）またはBotトークンでメッセージ本文・埋め込み・添付からYouTubeリンクを検出し、`limit` 件まで新しい順に取得します。既にSheetsへ記録済みのURLは重複排除し、429応答は`Retry-After`を待ってから再試行します。
- Discordメッセージ本文に時刻・矢印・UBなどのTL行が2行以上ある場合、`priconner_tl_formatter` の `format_text` で自動整形し、URLと整形済みTLをWebhookへ投稿します。TL行がない投稿は従来どおりURLだけを投稿します。

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
期間外の起動はラッパーが自動的にスキップします。

月末の最終日を除く直前8日間だけ定期実行する場合は、ラズパイのcrontabに
次の1行を登録します。`/path/to/priconner_tl_scanner` は実際の配置先に
置き換えてください。crontabは毎日呼び出しますが、`scheduled_monitor.py` が
対象期間以外の処理を自動的にスキップします。

```cron
0 3 * * * cd /path/to/priconner_tl_scanner && /usr/bin/python3 scheduled_monitor.py >> /path/to/priconner_tl_scanner/monitor.log 2>&1
```

この判定では、31日ある月は23〜30日、30日ある月は22〜29日、通常年の2月は
20〜27日、うるう年の2月は21〜28日が対象で、月の最終日は対象外です。

スプレッドシートのチャンネル除外状態を更新する場合:

```sh
python3 sheets_maintenance.py
```

コミット日時による待機判定は行いません。

`[maintenance] inactive_days` 未満の新しい動画記録があるチャンネルは、自動除外印が解除されます。動画記録がないチャンネルや手動設定の除外印は変更しません。

動画の対象判定には、タイトルだけでなく概要欄・タグ・ボス名・TL用語も使用します。他ゲーム名を含む動画は減点して対象外にします。
収集期間は`[youtube] period_mode=current_month`で当月1日以降に制限します。試験月を固定する場合は`period_mode=target_month`と`period_month=YYYY-MM`を指定します。強制投稿モードでも期間外の既存動画は投稿しません。日数指定に戻す場合は`period_mode=days`と`period_days`を設定します。
WorryChefsの公開タブには安定した公開日列がないため、`worrychefs.py --month YYYY/MM` の月は試験スナップショットのラベルとして扱います。保存先シートの初回検出日時が別月でも、指定スナップショットの全レコードを対象にできます。
新規投稿と更新差分を交互に試験する場合は`--alternate-new-update`を併用します。奇数番目は全文の新規投稿、偶数番目は保存済み本文との差分更新になります。
ボス別に分離して実行する場合は`PRICONNER_YOUTUBE_BOSS_INDEX=3`のように指定できます。値は1〜5で、検索投稿を指定ボスのチャンネルだけに限定します。チャンネル監視は共通投稿先のため、ボス限定環境では自動停止します（通常運転で明示的に併用する場合だけ`PRICONNER_ALLOW_SHARED_CHANNEL_SCAN=1`を指定）。

DiscordチャンネルのメッセージからYouTubeリンクを検出する場合:

```sh
python3 discord_channel.py
```

`[discord_channel]` セクションに `token`（DiscordユーザートークンまたはBotトークン）、`guild_id`（サーバーID）、`channel_ids`（カンマ区切りのチャンネルID）、`limit`（取得メッセージ上限）を記入します。TL整形を使う場合は `tl_formatter_path` に `priconner_tl_formatter` のパスを指定してください（例: `D:/git/priconner_tl_formatter`）。このパスがGitリポジトリなら、各プロセスの初回ロード時にGitHubの`origin`から`pull --ff-only`して最新化します。未コミット変更がある場合は上書きせず、同期失敗として停止します。`pip install -r requirements.txt` でもローカル依存として導入できます。Bot登録が不要な場合、Discordユーザートークン（Discord設定 > OAuth2 > トークン）を使用できます。対象チャンネルにアクセス権があるユーザーであればそのまま読み取れます。

#### トークンの取得

Discord ユーザートークンは定期的に失効するため、失効した場合は再取得が必要です。
以下の 2 つのツールで取得できます。

**1. 手動取得（対話式）**: `tools/discord_token_fetch.py`

```sh
python3 tools/discord_token_fetch.py
```

Discord のブラウザで `F12 → Console` に `mbi.user.token` を実行して得たトークンを貼り付けると、
検証（`GET /users/@me`）とサンプルチャンネルの読み取りテストを経て、
`config.ini` の `[discord_channel] token=` に保存されます。

**2. 自動取得（CDP）**: `tools/discord_token_autofetch.py`

```sh
python3 tools/discord_token_autofetch.py
```

新規 Chrome プロファイルでリモートデバッグポートを起動し、Discord のログインページを開きます。
開いたウィンドウでログインすると、DevTools Protocol を経由して `localStorage.token` を自動で評価し、
検証して `config.ini` に保存します。デフォルトプロファイルがロックダウンされている環境でも動作します。

> 注意: ユーザートークンはアカウント全体へのフルアクセスを付与します（2FA を迂回）。
> 他人に共有しないでください。失効させたい場合はパスワードを変更するとトークンが自動再生成されます。
