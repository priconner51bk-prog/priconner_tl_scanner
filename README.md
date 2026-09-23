# priconner_tl_scanner

プリコネ関連の新着TLをYouTube・WorryChefs・Discordチャンネルから検出し、Google Sheetsへ記録してDiscordへ通知する監視ツールです。

## 実行構成

通常運用は、Windows PC `3700x` の中央スケジューラーで実行します。中央スケジューラーが日次コントローラーと、このプロジェクトの収集ジョブを登録・有効化します。Discord送信専用タスクは登録しません。

| 実行環境 | 役割 | 備考 |
| --- | --- | --- |
| 3700x / `www51` | 主系の定期実行 | `D:\git\priconner_clan_battle_task_scheduler\register_scheduler.ps1` で中央管理。ログオン中に実行 |
| 手動実行 | 調査・復旧・試験 | `monitor_runner.py` または個別ステージを直接実行 |

## 最短セットアップ

```powershell
python -m pip install -r requirements.txt
Copy-Item config.ini.org config.ini
# config.ini、.env、discord_channels.json を設定
Set-ExecutionPolicy -Scope Process Bypass
Set-Location D:\git\priconner_clan_battle_task_scheduler
.\register_scheduler.ps1
```

このリポジトリの `register_monitor_tasks.ps1` は、中央スケジューラーへ委譲する互換ラッパーです。既存の手順を使う場合は、リポジトリ直下で `.\register_monitor_tasks.ps1` を実行できます。

タスクの詳細、Discord投稿、障害時の確認方法は [OPERATIONS.md](OPERATIONS.md) を参照してください。

秘密情報の名前と保管場所は [SECRET_INVENTORY.local.md](SECRET_INVENTORY.local.md) にまとめています。このファイルと設定ファイルの値は、チャット・ログ・Gitへ出さないでください。

## 処理フロー

1. 各ステージがYouTube、WorryChefs、またはDiscordチャンネルを走査する。
2. 検出結果をGoogle Sheetsへ保存し、投稿対象をDiscordキューへ登録する。
3. 各収集ステージの終了後に `monitor_runner.py` がSQLiteキューを送信する。Discord送信専用の常駐・1分間隔タスクは登録しない。
4. Discordの送信先は `discord_channels.json` の通常運用設定に従う。

同一処理の重複起動はロックで抑止し、Discord投稿は送信先ごとの順序、重複排除、429・通信失敗時の再試行、添付画像を保持します。

## 直接実行

全ステージを実行する場合:

```sh
python monitor_runner.py
```

個別ステージを実行する場合:

```sh
python monitor_runner.py --stages youtube-search
python monitor_runner.py --stages discord-channel
python monitor_runner.py --stages worrychefs
python monitor_runner.py --stages youtube-channel
```

監視期間を無視して全ステージを試験する場合は、外部サービスへアクセスして投稿する可能性があるため、実行前に対象を確認してください。

固定月を使う試験例:

```sh
python monitor_runner.py --stages youtube-search --period-month 2026-08 --content-month 2026-08
```

## 主要な設定

- `config.ini.org` を `config.ini` にコピーして、Google Sheets・Discord・監視期間を設定する。
- `config.ini`、`.env`、認証JSON、`discord_channels.json`、実行状態はGit管理外に置く。
- YouTube URLの登録はスプレッドシートのApps Script APIを使う。共有トークンを `YOUTUBE_URL_API_TOKEN`（`.env`）またはローカルの `[youtube] url_api_token` に設定する。メインのダメージシートに未登録のURLを実験サーバーの「新着tl情報」にも送信する。
- YouTubeの公開対象月は、通常はJSTの当月を自動使用する。試験時だけ `--period-month YYYY-MM` と `--content-month YYYY-MM` で対象月を指定できる。
- YouTubeは当月の公開日時と内容月を基準にし、対象外月のクラバト表記、他ゲーム、SP・イベント動画は取得しない。追加の除外語は「プリコネTL新着」の `NGワード` シートで管理する。
- `PRICONNER_YOUTUBE_BOSS_INDEX=1`〜`5` で検索対象ボスを限定できる。チャンネル監視を併用する場合は `PRICONNER_ALLOW_SHARED_CHANNEL_SCAN=1` が必要。
- Discord投稿の送信にはBotトークン、Discordチャンネルの読み取りには `[discord_channel] token` または `DISCORD_TOKEN` を使用する。
- TLフォーマッタは任意依存です。利用できない場合も走査・URL投稿は継続しますが、TL本文の自動整形は省略されます。

## 安全策

- 収集タスクの全体ロックとDiscord送信キューのロックで同時実行を抑止する。
- 登録チャンネルは最新動画投稿日から `maintenance.inactive_days` 日を超えると通常走査を省略し、YouTube検索で見つかった動画から最新投稿日を更新する。
- Sheetsへの記録とURLの重複排除により、同じ検出結果の再投稿を抑止する。
- Discordの通常運用投稿と試験投稿では送信先を分離できる。
- 実行状態は `XDG_STATE_HOME` 配下、未設定時は `~/.local/state/priconner-tl-scanner` に保存する。

## 関連ファイル

- `monitor_runner.py`: ステージ実行とDiscordキュー送信
- `register_monitor_tasks.ps1`: 中央スケジューラーへの委譲用互換スクリプト
- `discord_queue.py`: Discord投稿キューの送信・再試行
- `OPERATIONS.md`: 運用手順とトラブルシュート
- `SECRET_INVENTORY.local.md`: 秘密情報の対応表
