import discord_channel


def test_tracking_status_migrates_legacy_hash_without_reposting():
    previous = (
        "元投稿日時: 2026-09-22 12:00:00 JST\n"
        "検出日時: 2026/09/22 13:00:00\n"
        "Discord投稿本文: 本文"
    )
    current = (
        "元投稿日時: 2026-09-22 12:00:00 JST\n"
        "検出日時: 2026/09/22 13:05:00\n"
        "Discord投稿本文: 本文"
    )
    old = ["key", previous, "legacy-hash"]

    assert discord_channel._tracking_status(
        False,
        old,
        previous,
        discord_channel.post_tracker.comparison_text(current),
    ) == "same"


def test_tracking_status_still_detects_material_change():
    previous = "検出日時: 2026/09/22 13:00:00\nDiscord投稿本文: 旧本文"
    current = "検出日時: 2026/09/22 13:05:00\nDiscord投稿本文: 新本文"
    old = ["key", previous, "legacy-hash"]

    assert discord_channel._tracking_status(
        False,
        old,
        previous,
        discord_channel.post_tracker.comparison_text(current),
    ) == "updated"


def test_non_content_discord_messages_include_empty_and_month_markers():
    assert discord_channel._is_non_content_discord_message("")
    assert discord_channel._is_non_content_discord_message(
        "＝＝＝＝ ここから 2026年9月 ＝＝＝＝"
    )
    assert not discord_channel._is_non_content_discord_message("全SET")
