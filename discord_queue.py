"""Persistent single-writer queue for Discord posts and notifications."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from runtime_utils import LockBusy, acquire_lock, default_runtime_dir

QUEUE_DB_ENV = "PRICONNER_DISCORD_QUEUE_DB"
QUEUE_LOCK_NAME = "discord_post_queue.lock"
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BATCH_SIZE = 100
RETRY_BASE_SECONDS = 30


def queue_path(path=None):
    if path:
        return Path(path).expanduser()
    configured = os.environ.get(QUEUE_DB_ENV)
    if configured:
        return Path(configured).expanduser()
    return default_runtime_dir() / "discord_queue.sqlite3"


def _connect(path):
    path = queue_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS discord_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_key TEXT NOT NULL UNIQUE,
            guild_key TEXT NOT NULL,
            channel_key TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'post',
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at REAL NOT NULL,
            created_at REAL NOT NULL,
            sent_at REAL,
            last_error TEXT,
            attachment_name TEXT,
            attachment_mime TEXT,
            attachment_blob BLOB
        );
        CREATE INDEX IF NOT EXISTS idx_discord_queue_ready
            ON discord_queue(status, available_at, created_at, id);
        CREATE INDEX IF NOT EXISTS idx_discord_queue_destination
            ON discord_queue(guild_key, channel_key, status, created_at, id);
        """
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(discord_queue)")
    }
    for name, declaration in (
        ("attachment_name", "TEXT"),
        ("attachment_mime", "TEXT"),
        ("attachment_blob", "BLOB"),
    ):
        if name not in columns:
            connection.execute(
                f"ALTER TABLE discord_queue ADD COLUMN {name} {declaration}"
            )
    connection.commit()
    return connection


def _stable_key(kind, guild_key, channel_key, content):
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"{kind}:{guild_key}:{channel_key}:{digest}"


def enqueue_for_guilds(
    content,
    channel_key="boss0_tl",
    guild_keys=(),
    kind="post",
    dedupe_key=None,
    files=None,
    path=None,
):
    """Append one logical message for each fixed destination guild."""
    content = str(content or "")
    if not content or kind not in {"post", "notify"}:
        return []

    attachment_name = attachment_mime = None
    attachment_blob = None
    if files:
        value = files.get("file") if isinstance(files, dict) else None
        if not isinstance(value, tuple) or len(value) < 2:
            raise ValueError("Discord添付形式を解釈できません")
        attachment_name = str(value[0] or "attachment.bin")
        attachment_blob = value[1]
        if hasattr(attachment_blob, "read"):
            attachment_blob = attachment_blob.read()
        if not isinstance(attachment_blob, (bytes, bytearray)):
            raise ValueError("Discord添付データはbytesである必要があります")
        attachment_blob = bytes(attachment_blob)
        attachment_mime = str(value[2]) if len(value) > 2 and value[2] else None

    now = time.time()
    inserted = []
    with closing(_connect(path)) as connection:
        for guild_key in dict.fromkeys(guild_keys):
            key = dedupe_key or _stable_key(kind, guild_key, channel_key, content)
            if dedupe_key:
                key = f"{dedupe_key}:{guild_key}:{channel_key}"
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO discord_queue
                    (dedupe_key, guild_key, channel_key, kind, content,
                     available_at, created_at, attachment_name, attachment_mime,
                     attachment_blob)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    guild_key,
                    channel_key,
                    kind,
                    content,
                    now,
                    now,
                    attachment_name,
                    attachment_mime,
                    attachment_blob,
                ),
            )
            if cursor.rowcount:
                inserted.append(cursor.lastrowid)
        connection.commit()
    return inserted


def pending_count(path=None):
    with closing(_connect(path)) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM discord_queue WHERE status = 'queued'"
        ).fetchone()
    return int(row["count"])


def _reset_stale_sending(connection, now):
    connection.execute(
        """
        UPDATE discord_queue
        SET status = 'queued', available_at = ?, last_error = ?
        WHERE status = 'sending' AND available_at < ?
        """,
        (now, "送信プロセスが中断されたため再試行", now - 600),
    )


def _next_ready(connection, now):
    return connection.execute(
        """
        SELECT item.*
        FROM discord_queue AS item
        WHERE item.status = 'queued'
          AND item.available_at <= ?
          AND NOT EXISTS (
              SELECT 1
              FROM discord_queue AS earlier
              WHERE earlier.guild_key = item.guild_key
                AND earlier.channel_key = item.channel_key
                AND earlier.status IN ('queued', 'sending')
                AND (
                    earlier.created_at < item.created_at
                    OR (earlier.created_at = item.created_at AND earlier.id < item.id)
                )
          )
        ORDER BY item.created_at, item.id
        LIMIT 1
        """,
        (now,),
    ).fetchone()


def _send(item):
    # Import lazily to keep enqueue-only collectors free of API initialization.
    import discord_utils

    sender = discord_utils.notify if item["kind"] == "notify" else discord_utils.post
    files = None
    if item["attachment_blob"] is not None:
        files = {
            "file": (
                item["attachment_name"] or "attachment.bin",
                item["attachment_blob"],
                item["attachment_mime"] or "application/octet-stream",
            )
        }
    kwargs = {
        "guild_key": item["guild_key"],
        "channel_key": item["channel_key"],
    }
    if item["kind"] == "post":
        kwargs["files"] = files
    return sender(
        item["content"],
        **kwargs,
    )


def drain(path=None, max_items=DEFAULT_BATCH_SIZE, max_attempts=DEFAULT_MAX_ATTEMPTS):
    """Send queued items in destination order under one process lock."""
    path = queue_path(path)
    result = {"sent": 0, "retried": 0, "failed": 0, "remaining": 0, "locked": False}
    try:
        with acquire_lock(path.with_name(QUEUE_LOCK_NAME)), closing(
            _connect(path)
        ) as connection:
                now = time.time()
                _reset_stale_sending(connection, now)
                connection.commit()

                for _ in range(max(0, int(max_items))):
                    item = _next_ready(connection, time.time())
                    if item is None:
                        break
                    connection.execute(
                        "UPDATE discord_queue SET status = 'sending', available_at = ? WHERE id = ?",
                        (time.time(), item["id"]),
                    )
                    connection.commit()
                    try:
                        _send(item)
                    except Exception as error:  # noqa: BLE001 - queue all send failures
                        attempts = int(item["attempts"]) + 1
                        terminal = attempts >= max_attempts
                        status = "failed" if terminal else "queued"
                        delay = RETRY_BASE_SECONDS * (2 ** min(attempts - 1, 5))
                        connection.execute(
                            """
                            UPDATE discord_queue
                            SET status = ?, attempts = ?, available_at = ?, last_error = ?
                            WHERE id = ?
                            """,
                            (
                                status,
                                attempts,
                                time.time() + (0 if terminal else delay),
                                f"{type(error).__name__}: {error}"[:1000],
                                item["id"],
                            ),
                        )
                        connection.commit()
                        if terminal:
                            result["failed"] += 1
                        else:
                            result["retried"] += 1
                        continue

                    connection.execute(
                        "UPDATE discord_queue SET status = 'sent', sent_at = ?, last_error = NULL WHERE id = ?",
                        (time.time(), item["id"]),
                    )
                    connection.commit()
                    result["sent"] += 1

                result["remaining"] = int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM discord_queue WHERE status = 'queued'"
                    ).fetchone()["count"]
                )
    except LockBusy:
        result["locked"] = True
    return result


if __name__ == "__main__":
    summary = drain()
    print(
        "Discordキュー: "
        f"送信{summary['sent']}件 再試行待ち{summary['retried']}件 "
        f"失敗{summary['failed']}件 残り{summary['remaining']}件"
    )
