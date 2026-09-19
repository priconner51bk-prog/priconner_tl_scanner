from pathlib import Path

import discord_queue
import discord_utils


def test_queue_deduplicates_and_sends_each_destination_in_order(tmp_path, monkeypatch):
    calls = []

    def fake_post(content, *, guild_key, channel_key, files=None):
        calls.append((guild_key, channel_key, content, files))
        return object()

    monkeypatch.setattr(discord_utils, "post", fake_post)
    db_path = Path(tmp_path) / "queue.sqlite3"

    first = discord_queue.enqueue_for_guilds(
        "first", channel_key="boss1_tl", guild_keys=["production", "experimental"], path=db_path
    )
    duplicate = discord_queue.enqueue_for_guilds(
        "first", channel_key="boss1_tl", guild_keys=["production", "experimental"], path=db_path
    )
    discord_queue.enqueue_for_guilds(
        "second", channel_key="boss1_tl", guild_keys=["production", "experimental"], path=db_path
    )

    assert len(first) == 2
    assert duplicate == []
    assert discord_queue.drain(path=db_path, max_items=10) == {
        "sent": 4,
        "retried": 0,
        "failed": 0,
        "remaining": 0,
        "locked": False,
    }
    assert [call[:3] for call in calls] == [
        ("production", "boss1_tl", "first"),
        ("experimental", "boss1_tl", "first"),
        ("production", "boss1_tl", "second"),
        ("experimental", "boss1_tl", "second"),
    ]


def test_queue_persists_attachment_bytes(tmp_path, monkeypatch):
    received = []

    def fake_post(content, *, guild_key, channel_key, files=None):
        received.append((content, guild_key, channel_key, files))
        return object()

    monkeypatch.setattr(discord_utils, "post", fake_post)
    db_path = Path(tmp_path) / "queue.sqlite3"
    discord_queue.enqueue_for_guilds(
        "with image",
        channel_key="boss2_tl",
        guild_keys=["experimental"],
        files={"file": ("formation.png", b"png-bytes", "image/png")},
        path=db_path,
    )

    result = discord_queue.drain(path=db_path)

    assert result["sent"] == 1
    assert received == [
        (
            "with image",
            "experimental",
            "boss2_tl",
            {"file": ("formation.png", b"png-bytes", "image/png")},
        )
    ]
