# priconner_tl_movie_scanner

プリコネ関連の新着TLを各情報源から検出し、Google Sheetsへ記録してDiscordへ通知する監視ツールです。

## 運用上の安全策

- `monitor_runner.py` と各収集スクリプトは、それぞれ専用の実行中ロックを取得して同じ処理の重複起動をスキップします。
- ステージ名と動画URLは1回の走査内で重複排除します。YouTube検索件数には上限を設け、検索設定を大きくしても過剰な取得を抑えます。
- 実行状態は `XDG_STATE_HOME` 配下（未設定時は `~/.local/state/priconner-tl-movie-scanner`）の `state.json` に保存します。認証情報、`config.ini`、状態ファイルはGitへ登録しません。

通常の実行:

```sh
python3 monitor_runner.py
```

スプレッドシートのチャンネル除外状態を更新する場合:

```sh
python3 sheets_maintenance.py
```

`[maintenance] inactive_days` 未満の新しい動画記録があるチャンネルは、自動除外印が解除されます。動画記録がないチャンネルや手動設定の除外印は変更しません。
