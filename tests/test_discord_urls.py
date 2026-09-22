"""Tests for YouTube URL extraction and normalization in the Discord stage."""

import discord_channel


def test_source_channel_label_prefers_human_name():
    assert discord_channel._source_channel_label("888415796617965618") == "四段セミオ_②"


def test_normalize_youtube_url_short():
    assert (
        discord_channel._normalize_youtube_url("https://youtu.be/abc123")
        == "https://www.youtube.com/watch?v=abc123"
    )


def test_normalize_youtube_url_shorts_and_live():
    assert (
        discord_channel._normalize_youtube_url("https://www.youtube.com/shorts/xyz")
        == "https://www.youtube.com/watch?v=xyz"
    )
    assert (
        discord_channel._normalize_youtube_url("https://www.youtube.com/live/qwe")
        == "https://www.youtube.com/watch?v=qwe"
    )


def test_normalize_youtube_url_passthrough_non_youtube():
    assert (
        discord_channel._normalize_youtube_url("https://example.com/a")
        == "https://example.com/a"
    )


def test_extract_youtube_urls_from_content():
    message = {"content": "watch https://youtu.be/abc and https://www.youtube.com/watch?v=def"}
    assert discord_channel.extract_youtube_urls(message) == [
        "https://www.youtube.com/watch?v=abc",
        "https://www.youtube.com/watch?v=def",
    ]


def test_extract_youtube_urls_dedupes_identical_links():
    message = {"content": "https://youtu.be/abc https://www.youtube.com/watch?v=abc"}
    assert discord_channel.extract_youtube_urls(message) == [
        "https://www.youtube.com/watch?v=abc"
    ]


def test_extract_youtube_urls_from_embeds_and_attachments():
    message = {
        "content": "",
        "embeds": [
            {"url": "https://youtu.be/emb1"},
            {"video_url": "https://www.youtube.com/watch?v=emb2"},
        ],
        "attachments": [{"url": "https://www.youtube.com/shorts/att1"}],
    }
    assert discord_channel.extract_youtube_urls(message) == [
        "https://www.youtube.com/watch?v=att1",
        "https://www.youtube.com/watch?v=emb1",
        "https://www.youtube.com/watch?v=emb2",
    ]


def test_extract_youtube_urls_ignores_non_youtube_links():
    message = {"content": "https://example.com/x and http://youtu.be?nope"}
    assert discord_channel.extract_youtube_urls(message) == []


def test_fetch_channel_messages_returns_none_after_retries():
    import requests

    calls = []
    sleeps = []

    def failing_get(url, **kwargs):
        calls.append(url)
        raise requests.RequestException("boom")

    messages = discord_channel.fetch_channel_messages(
        "channel-1",
        "token",
        http_get=failing_get,
        retries=2,
        retry_sleep=sleeps.append,
    )
    assert messages is None
    assert len(calls) == 3
    assert sleeps == [1, 2]
