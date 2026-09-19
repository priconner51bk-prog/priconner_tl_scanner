# priconner_tl_scanner

プリコネ関連の新着TLをYouTube・WorryChefs・Discordチャンネルから検出し、Google Sheetsへ記録してDiscordへ通知する監視ツールです。

## 実行構成

通常運用は、Windows PC `3700x` のタスクスケジューラで実行します。現在の登録スクリプトは、監視ステージを分けて起動するため、処理の重複と不要なAPIアクセスを抑えます。

| 実行環境 | 役割 | 備考 |
| --- | --- | --- |
| 3700x / `www51` | 主系の定期実行 | `register_monitor_tasks.ps1` で登録。ログオン中に実行 |
| 手動実行 | 調査・復旧・試験 | `monitor_runner.py` または個別ステージを直接実行 |

## 最短セットアップ

```powershell
python -m pip install -r requirements.txt
Copy-Item config.ini.org config.ini
# config.ini、.env、discord_channels.json を設定
Set-ExecutionPolicy -Scope Process Bypass
.\register_monitor_tasks.ps1
```

タスクの詳細、Discord投稿、障害時の確認方法は [OPERATIONS.md](OPERATIONS.md) を参照してください。

秘密情報の名前と保管場所は [SECRET_INVENTORY.local.md](SECRET_INVENTORY.local.md) にまとめています。このファイルと設定ファイルの値は、チャット・ログ・Gitへ出さないでください。

## 処理フロー

1. 各ステージがYouTube、WorryChefs、またはDiscordチャンネルを走査する。
2. 検出結果をGoogle Sheetsへ保存し、投稿対象をDiscordキューへ登録する。
3. `monitor_runner.py` がステージ終了後にSQLiteキューを送信する。
4. Discordの送信先は `discord_channels.json` の通常運用設定に従う。

同一処理の重複起動はロックで抑止し、Discord投稿は送信先ごとの順序、重複排除、429・通信失敗時の再試行、添付画像を保持します。

## 直接実行

全ステージを実行する場合:

```sh
python monitor_runner.py
```

個別ステージを、月末監視期間の判定付きで実行する場合:

```sh
python scheduled_monitor.py --stage youtube-search
python scheduled_monitor.py --stage discord-channel
python scheduled_monitor.py --stage worrychefs
python scheduled_monitor.py --stage youtube-channel
```

監視期間を無視して全ステージを試験する場合は、外部サービスへアクセスして投稿する可能性があるため、実行前に対象を確認してください。

## 主要な設定

- `config.ini.org` を `config.ini` にコピーして、Google Sheets・Discord・監視期間を設定する。
- `config.ini`、`.env`、認証JSON、`discord_channels.json`、実行状態はGit管理外に置く。
- YouTubeの期間は `[youtube] period_mode=current_month` を基本とする。固定月試験は `target_month`、日数指定は `days` を使用する。
- `PRICONNER_YOUTUBE_BOSS_INDEX=1`〜`5` で検索対象ボスを限定できる。チャンネル監視を併用する場合は `PRICONNER_ALLOW_SHARED_CHANNEL_SCAN=1` が必要。
- Discord投稿の送信にはBotトークン、Discordチャンネルの読み取りには `[discord_channel] token` または `DISCORD_TOKEN` を使用する。
- TLフォーマッタは任意依存です。利用できない場合も走査・URL投稿は継続しますが、TL本文の自動整形は省略されます。

## 安全策

- ステージごとのロックと全体ロックで同時実行を抑止する。
- Sheetsへの記録とURLの重複排除により、同じ検出結果の再投稿を抑止する。
- Discordの通常運用投稿と試験投稿では送信先を分離できる。
- 実行状態は `XDG_STATE_HOME` 配下、未設定時は `~/.local/state/priconner-tl-scanner` に保存する。

## 関連ファイル

- `monitor_runner.py`: ステージ実行とDiscordキュー送信
- `scheduled_monitor.py`: 月末監視期間の判定と個別ステージ起動
- `register_monitor_tasks.ps1`: 3700xのタスクスケジューラ登録
- `discord_queue.py`: Discord投稿キューの送信・再試行
- `OPERATIONS.md`: 運用手順とトラブルシュート
- `SECRET_INVENTORY.local.md`: 秘密情報の対応表
