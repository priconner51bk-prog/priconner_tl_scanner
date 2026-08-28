# priconner_tl_movie_scanner

プリコネ関連の新着TLを各情報源から検出し、Google Sheetsへ記録してDiscordへ通知する監視ツールです。

## 運用上の安全策

- `monitor_runner.py` は実行中ロックを取得し、同時起動された監視をスキップします。各収集スクリプトを直接起動した場合も同じロックを使用します。
- ステージ名と動画URLは1回の走査内で重複排除します。YouTube検索件数には上限を設け、検索設定を大きくしても過剰な取得を抑えます。
- 実行状態は `XDG_STATE_HOME` 配下（未設定時は `~/.local/state/priconner-tl-movie-scanner`）の `state.json` に保存します。認証情報、`config.ini`、状態ファイルはGitへ登録しません。

通常の実行:

```sh
python3 monitor_runner.py
```
