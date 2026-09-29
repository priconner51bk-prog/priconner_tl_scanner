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


def test_tracking_ignores_author_display_name_changes_in_existing_rows():
    previous = (
        "投稿者: クローン🐎\n"
        "元投稿日時: 2026-09-22 12:50:34 JST\n"
        "Discord投稿本文: 分からないけど一応全SET"
    )
    current = previous.replace("クローン🐎", "クローン")
    previous_comparison = discord_channel.post_tracker.comparison_text(previous)
    current_comparison = discord_channel._discord_comparison_text(current)
    old = ["key", previous, "old-hash", "", "", previous, "channel", "message", previous_comparison]

    assert discord_channel._tracking_status(
        False, old, previous, current_comparison
    ) == "same"
    assert discord_channel._discord_comparison_text(previous) == current_comparison


def test_author_display_name_is_omitted_from_material_change_diff():
    previous = "投稿者: クローン🐎\nDiscord投稿本文: 旧本文"
    current = "投稿者: クローン\nDiscord投稿本文: 新本文"
    post = discord_channel.post_tracker.post_content({
        "status": "updated",
        "previous_text": previous,
        "text": current,
        "diff_previous_text": discord_channel._discord_comparison_text(previous),
        "diff_current_text": discord_channel._discord_comparison_text(current),
    })

    diff = post.split("【差分】", 1)[1].split("【現行本文】", 1)[0]
    assert "投稿者:" not in diff
    assert "- Discord投稿本文: 旧本文" in diff
    assert "+ Discord投稿本文: 新本文" in diff
    assert discord_channel._discord_comparison_text(
        "投稿者: A\nDiscord投稿本文: 投稿者: Alice"
    ) != discord_channel._discord_comparison_text(
        "投稿者: B\nDiscord投稿本文: 投稿者: Bob"
    )


def test_tracking_ignores_formatter_only_changes_but_keeps_current_tl():
    prefix = "投稿者: test\nDiscord投稿本文: 0:05 チエル UB"
    previous = prefix + "\n\nTL（整形済み）:\n```scm\n0:05 チエル→UB\n```"
    current = prefix + "\n\nTL（整形済み）:\n```scm\n0:05 チエル [54321] UB\n```"
    comparison = discord_channel.post_tracker.comparison_text(
        discord_channel._without_formatted_tl(current)
    )

    assert discord_channel._tracking_status(False, ["key", previous, "old-hash"], previous, comparison) == "same"
    assert discord_channel.post_tracker.post_content({
        "status": "updated",
        "previous_text": previous,
        "text": current,
        "diff_previous_text": discord_channel._without_formatted_tl(previous),
        "diff_current_text": discord_channel._without_formatted_tl(current),
    }) == f"【差分なし】\n【現行本文】\n{current}"


def test_tracking_detects_raw_change_without_listing_formatter_changes():
    previous = "Discord投稿本文: 0:05 チエル UB\n\nTL（整形済み）:\n```scm\n旧書式\n```"
    current = "Discord投稿本文: 0:05 チエル SET\n\nTL（整形済み）:\n```scm\n新書式\n```"
    comparison = discord_channel.post_tracker.comparison_text(
        discord_channel._without_formatted_tl(current)
    )

    assert discord_channel._tracking_status(False, ["key", previous, "old-hash"], previous, comparison) == "updated"
    post = discord_channel.post_tracker.post_content({
        "status": "updated",
        "previous_text": previous,
        "text": current,
        "diff_previous_text": discord_channel._without_formatted_tl(previous),
        "diff_current_text": discord_channel._without_formatted_tl(current),
    })
    assert "- Discord投稿本文: 0:05 チエル UB" in post
    assert "+ Discord投稿本文: 0:05 チエル SET" in post
    assert "- 旧書式" not in post
    assert "+ 新書式" not in post


def test_tracking_uses_full_source_snapshot_for_clipped_messages():
    raw = "本文" * 800
    previous = discord_channel.post_tracker.add_post_separator(
        discord_channel._build_discord_post_body(
            "投稿者: test", raw, "\n投稿元リンク: https://discord.test/source", "短いTL"
        )
    )
    current = discord_channel.post_tracker.add_post_separator(
        discord_channel._build_discord_post_body(
            "投稿者: test", raw, "\n投稿元リンク: https://discord.test/source", "長いTL" * 100
        )
    )
    source = discord_channel.post_tracker.comparison_text(
        discord_channel.post_tracker.add_post_separator(
            f"投稿者: test\nDiscord投稿本文: {raw}\n投稿元リンク: https://discord.test/source"
        )
    )

    assert discord_channel._without_formatted_tl(previous) != discord_channel._without_formatted_tl(current)
    assert discord_channel._tracking_status(
        False, ["key", previous, "old", "", "", "", "", "", source],
        previous, source, current, raw,
    ) == "same"
    assert discord_channel._tracking_status(
        False, ["key", previous, "old"], previous, source, current, raw,
    ) == "same"
    assert discord_channel._tracking_status(
        False, ["key", previous, "old", "", "", "", "", "", source],
        previous, source + "変更", current, raw + "変更",
    ) == "updated"


def test_non_content_discord_messages_include_empty_and_month_markers():
    assert discord_channel._is_non_content_discord_message("")
    assert discord_channel._is_non_content_discord_message(
        "＝＝＝＝ ここから 2026年9月 ＝＝＝＝"
    )
    assert not discord_channel._is_non_content_discord_message("全SET")
