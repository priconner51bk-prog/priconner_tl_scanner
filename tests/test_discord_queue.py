from contextlib import closing
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
        "cleaned_sent": 0,
        "cleaned_failed": 0,
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


def test_drain_cleans_old_history_but_keeps_active_items(tmp_path, monkeypatch):
    monkeypatch.setattr(discord_utils, "post", lambda **_kwargs: object())
    db_path = Path(tmp_path) / "queue.sqlite3"
    now = 2_000_000_000.0
    with closing(discord_queue._connect(db_path)) as connection:
        connection.executemany(
            """
            INSERT INTO discord_queue
                (dedupe_key, guild_key, channel_key, kind, content, status,
                 available_at, created_at, sent_at)
            VALUES (?, ?, ?, 'post', ?, ?, ?, ?, ?)
            """,
            [
                (
                    "old-sent",
                    "production",
                    "boss0_tl",
                    "old sent",
                    "sent",
                    now - 181 * 86400,
                    now - 181 * 86400,
                    now - 181 * 86400,
                ),
                (
                    "old-failed",
                    "production",
                    "boss0_tl",
                    "old failed",
                    "failed",
                    now - 366 * 86400,
                    now - 366 * 86400,
                    None,
                ),
                (
                    "active-sent",
                    "production",
                    "boss0_tl",
                    "recent sent",
                    "sent",
                    now - 10 * 86400,
                    now - 10 * 86400,
                    now - 10 * 86400,
                ),
            ],
        )
        connection.commit()

    monkeypatch.setattr(discord_queue.time, "time", lambda: now)
    result = discord_queue.drain(path=db_path, max_items=0)

    assert result["cleaned_sent"] == 1
    assert result["cleaned_failed"] == 1
    with closing(discord_queue._connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT dedupe_key FROM discord_queue ORDER BY dedupe_key"
        ).fetchall()
    assert [row["dedupe_key"] for row in rows] == ["active-sent"]
