import discord_channel


def test_post_summary_groups_by_boss_status_kind_and_title():
    summary = discord_channel._build_post_summary(
        [
            {
                "source_boss": 1,
                "text": "post 1",
                "status": "new",
                "is_youtube": True,
                "title": "ボス1 新規TL",
                "summary_author": "YouTubeチャンネルA",
                "post_result": "成功",
            },
            {
                "source_boss": 1,
                "text": "post 2",
                "status": "updated",
                "is_youtube": True,
                "title": "ボス1 更新TL",
                "summary_author": "YouTubeチャンネルB",
                "post_result": "失敗",
            },
            {
                "source_boss": 2,
                "text": "post 3",
                "status": "new",
                "is_youtube": False,
                "title": "",
                "post_result": "成功",
            },
        ]
    )

    assert "Discord投稿サマリー（対象3件 / 成功2件 / 失敗1件）" in summary
    assert "ボス1: 2件" in summary
    assert "状態: 新規1件 / 更新1件" in summary
    assert "種別: YouTube2件" in summary
    assert "- タイトル: ボス1 新規TL / 投稿者: YouTubeチャンネルA" in summary
    assert "- タイトル: ボス1 更新TL / 投稿者: YouTubeチャンネルB" in summary
    assert "ボス2: 1件" in summary
    assert "種別: Discord本文1件" in summary


def test_post_summary_labels_unassigned_posts_as_boss_zero():
    summary = discord_channel._build_post_summary(
        [
            {
                "source_boss": None,
                "text": "post",
                "status": "new",
                "is_youtube": False,
            }
        ]
    )

    assert "ボス0（判定不能）: 1件" in summary


def test_post_summary_can_read_the_exact_queued_message_body():
    summary = discord_channel._build_post_summary(
        [
            {
                "channel_key": "boss3_tl",
                "content": (
                    "━━━━━━━━━━━━\n"
                    "動画タイトル: キューから確認するTL\n"
                    "動画URL: https://www.youtube.com/watch?v=queued"
                ),
            },
            {
                "channel_key": "boss3_tl",
                "content": "━━━━━━━━━━━━\n【差分】\n- 旧\n+ 新",
            },
        ]
    )

    assert "ボス3: 2件" in summary
    assert "状態: 新規1件 / 更新1件" in summary
    assert "種別: YouTube1件 / Discord本文1件" in summary
    assert "- タイトル: キューから確認するTL / 投稿者: （不明）" in summary


def test_post_summary_includes_worrychefs_source_sheet_link():
    url = (
        "https://docs.google.com/spreadsheets/d/e/current-month/"
        "pubhtml/sheet?headers=false&gid=1391911131"
    )
    summary = discord_channel._build_post_summary(
        [
            {
                "channel_key": "boss1_tl",
                "content": (
                    "[WorryChefs更新] D102 (simple)\n"
                    "状態: 更新\n"
                    f"参照スプシ: [シートを開く]({url})\n"
                    "制作者: g8 / ダメージ: 378.5m"
                ),
            }
        ]
    )

    assert "- タイトル: D102 (simple) / 制作者: g8 / ダメージ: 378.5m" in summary
    assert f"参照スプシ: [シートを開く]({url})" in summary


def test_post_summary_includes_short_worrychefs_source_sheet_link():
    url = "https://docs.google.com/spreadsheets/d/e/current-month/pubhtml/sheet?gid=123"
    summary = discord_channel._build_post_summary(
        [{"channel_key": "boss1_tl", "content": f"[WorryChefs更新] D102\n参照: {url}"}]
    )

    assert f"参照スプシ: [シートを開く]({url})" in summary


def test_post_summary_shows_each_worrychefs_sheet_link_once():
    shared_url = "https://docs.google.com/spreadsheets/d/e/month/pubhtml/sheet?gid=1"
    other_url = "https://docs.google.com/spreadsheets/d/e/month/pubhtml/sheet?gid=2"
    summary = discord_channel._build_post_summary(
        [
            {
                "channel_key": "boss1_tl",
                "content": (
                    "[WorryChefs更新] D101 (simple)\n"
                    f"参照スプシ: [シートを開く]({shared_url})"
                ),
            },
            {
                "channel_key": "boss2_tl",
                "content": f"[WorryChefs更新] D201 (simple)\n参照: {shared_url}",
            },
            {
                "channel_key": "boss2_tl",
                "content": f"[WorryChefs更新] D202 (manual-d2)\n参照: {other_url}",
            },
        ]
    )

    assert summary.count(f"参照スプシ: [シートを開く]({shared_url})") == 1
    assert summary.count(f"参照スプシ: [シートを開く]({other_url})") == 1
