"""Republish the stored August Discord scan for one boss channel.

This is deliberately explicit about scope: it reads the existing scan sheet,
filters Discord snowflake timestamps to 2026-08, and only touches the selected
experimental bot channel.  Use --dry-run before --execute.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import time
from datetime import datetime, timezone

import gspread_utils as gspread
import discord_utils as discord
from tl_formatting import format_discord_tl

SOURCE_GUILD_ID = "883641935175233646"
TARGET_GUILD_ID = "1360448049272328333"
TARGET_CHANNEL = "boss2_tl"
SOURCE_CHANNELS = {
    "888415796617965618": "四段セミオ_②",
    "888415864355954708": "四段_②セミオ相談",
    "889752971594829844": "四段手動_②",
    "890430547833286676": "四段手動_②相談",
}
TARGET_YEAR = 2026
TARGET_MONTH = 8
URL_RE = re.compile(r"https?://[^\s<>]+")
YOUTUBE_RE = re.compile(r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[^\s<>]+")
SECTION_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"


def snowflake_datetime(message_id: str) -> datetime | None:
    try:
        milliseconds = (int(message_id) >> 22) + 1420070400000
        return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _extract_source_text(body: str) -> str:
    marker = "Discord投稿本文:"
    if marker not in body:
        return body.strip()
    value = body.split(marker, 1)[1]
    for end_marker in ("\nDiscord投稿URL:", "\n投稿内URL:"):
        if end_marker in value:
            value = value.split(end_marker, 1)[0]
    # Older scan rows may contain the original fenced TL followed by an
    # already-formatted TL.  Keep only the first fenced block so a republish
    # never formats the previous output a second time.
    if "```" in value:
        prefix, fenced = value.split("```", 1)
        if "```" in fenced:
            fenced, _ = fenced.split("```", 1)
            if fenced.startswith("cs\n"):
                fenced = fenced[3:]
            value = prefix.rstrip() + "\n" + fenced.lstrip("\r\n")
    value = value.strip()
    if value.startswith("```cs"):
        value = value[5:]
    if value.startswith("```"):
        value = value[3:]
    if value.endswith("```"):
        value = value[:-3]
    return value.strip()


def _urls(body: str) -> list[str]:
    return sorted({url.rstrip(".,)>]}") for url in URL_RE.findall(body)})


def _youtube_url(body: str) -> str:
    matches = YOUTUBE_RE.findall(body)
    return matches[0].rstrip(".,)>]}") if matches else ""


def _clip(value: str, limit: int) -> str:
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    if limit <= 1:
        return "…"[:limit]
    return value[: limit - 1].rstrip() + "…"


def _md_block(label: str, value: str) -> tuple[str, str]:
    """Return Markdown-fence prefix/suffix that cannot be closed by value."""
    longest_run = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * max(3, longest_run + 1)
    return f"\n\n{label}:\n{fence}md\n", f"\n{fence}"


def load_records() -> list[dict]:
    sheet = gspread.getNewArrivalsSheet().worksheet("Discordスキャン")
    records = []
    seen = set()
    for row in sheet.get_all_values()[1:]:
        if len(row) < 8 or row[6] not in SOURCE_CHANNELS:
            continue
        message_id = str(row[7]).strip()
        source_time = snowflake_datetime(message_id)
        if not source_time or (source_time.year, source_time.month) != (
            TARGET_YEAR,
            TARGET_MONTH,
        ):
            continue
        key = f"discord://{row[6]}/{message_id}"
        if key in seen:
            continue
        seen.add(key)
        source_channel_id = row[6]
        source_channel_name = SOURCE_CHANNELS[source_channel_id]
        source_url = f"https://discord.com/channels/{SOURCE_GUILD_ID}/{source_channel_id}/{message_id}"
        records.append(
            {
                "key": key,
                "message_id": message_id,
                "source_channel_id": source_channel_id,
                "source_channel_name": source_channel_name,
                "source_time": source_time,
                "source_url": source_url,
                "body": row[1],
            }
        )
    return sorted(records, key=lambda item: item["source_time"])


def build_post(record: dict) -> dict:
    raw = _extract_source_text(record["body"])
    formatted = ""
    try:
        formatted = format_discord_tl(raw)
    except RuntimeError:
        formatted = ""
    youtube_url = _youtube_url(record["body"])
    # Use one link only: the human-readable channel name opens the original
    # message, so a separate 元投稿 line is unnecessary.
    source_link = f"[{record['source_channel_name']}]({record['source_url']})"
    header = (
        "🧪 **ボス2 / 2026年8月 再収集**\n"
        f"チャンネル: {source_link}\n"
        f"元投稿日時: {record['source_time'].astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
    )
    footer = ""
    # Some scan rows already contain the generated `動画URL:` metadata in
    # their original body.  Do not append the same URL a second time.
    if youtube_url and youtube_url not in raw:
        footer += f"\n動画URL: {youtube_url}"
    elif "投稿内URL:" in record["body"]:
        embedded = [url for url in _urls(record["body"]) if url != record["source_url"]]
        if embedded:
            footer += f"\n投稿内URL: {embedded[0]}"
    if formatted:
        original_prefix, original_suffix = _md_block("TL（原文）", raw or "（原文なし）")
        formatted_prefix, formatted_suffix = _md_block("TL（整形済み）", formatted)
        available = 2000 - len(header) - len(footer)
        available -= len(original_prefix) + len(original_suffix)
        available -= len(formatted_prefix) + len(formatted_suffix)
        original_limit = min(len(raw), max(0, available))
        original = _clip(raw or "（原文なし）", original_limit)
        formatted_limit = max(0, available - len(original))
        tl = _clip(formatted, formatted_limit)
        content = (
            header
            + original_prefix
            + original
            + original_suffix
            + formatted_prefix
            + tl
            + formatted_suffix
            + footer
        )
        clipped = original != (raw or "（原文なし）") or tl != formatted
    else:
        prefix, suffix = _md_block("本文（原文）", raw or "（本文なし）")
        available = 2000 - len(header) - len(footer) - len(prefix) - len(suffix)
        text = _clip(raw or "（本文なし）", max(80, available))
        content = header + prefix + text + suffix + footer
        clipped = text != (raw or "（本文なし）")
    return {
        **record,
        "content": content,
        "digest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "youtube_url": youtube_url,
        "formatted": bool(formatted),
        "clipped": clipped,
    }


def prepare() -> list[dict]:
    return [build_post(record) for record in load_records()]


def print_dry_run(posts: list[dict]) -> None:
    duplicate_keys = len(posts) - len({post["key"] for post in posts})
    youtube_posts = [post for post in posts if post["youtube_url"]]
    duplicate_urls = len(youtube_posts) - len(
        {post["youtube_url"] for post in youtube_posts}
    )
    print(
        f"対象={len(posts)} 重複キー={duplicate_keys} "
        f"重複動画URL={duplicate_urls} 整形TL={sum(p['formatted'] for p in posts)} "
        f"本文切詰め={sum(p['clipped'] for p in posts)}"
    )
    for index, post in enumerate(posts, 1):
        print(
            f"{index:02d} {post['source_channel_name']} {post['message_id']} "
            f"len={len(post['content'])} {'TL' if post['formatted'] else '本文'} "
            f"{post['source_url']}"
        )


def execute(posts: list[dict]) -> None:
    deleted = discord.delete_bot_messages(
        guild_key="experimental", channel_keys=[TARGET_CHANNEL]
    )
    print(f"削除成功: {deleted}件")
    posted = []
    failures = []
    separator_success = 0
    separator_failures = []
    for index, post in enumerate(posts, 1):
        try:
            response = discord.post(
                post["content"], guild_key="experimental", channel_key=TARGET_CHANNEL
            )
            message_id = ""
            try:
                message_id = str(response.json().get("id", ""))
            except (AttributeError, ValueError):
                pass
            posted.append({**post, "posted_message_id": message_id})
            print(f"投稿成功 {index}/{len(posts)}: {post['message_id']} -> {message_id}")
        except Exception as error:  # keep the batch moving and report each reason
            failures.append((post["key"], str(error)))
            print(f"投稿失敗 {index}/{len(posts)}: {post['key']}: {error}")
        if index < len(posts):
            try:
                discord.post(
                    SECTION_SEPARATOR,
                    guild_key="experimental",
                    channel_key=TARGET_CHANNEL,
                )
                separator_success += 1
            except Exception as error:  # keep the batch moving and report each reason
                separator_failures.append((index, str(error)))
                print(f"区切り投稿失敗 {index}件目の後: {error}")
    print(
        f"投稿結果: 本文成功{len(posted)}件 失敗{len(failures)}件 "
        f"区切り成功{separator_success}件 失敗{len(separator_failures)}件"
    )
    for key, reason in failures:
        print(f"失敗理由: {key}: {reason}")
    for index, reason in separator_failures:
        print(f"区切り失敗理由: {index}件目の後: {reason}")
    verify(posted)


def verify(posted: list[dict]) -> None:
    import requests

    token = discord._bot_token()
    channel_id = discord.channel_id("experimental", TARGET_CHANNEL)
    headers = {"Authorization": f"Bot {token}"}
    ok = 0
    failed = []
    for post in posted:
        message_id = post["posted_message_id"]
        if not message_id:
            failed.append((post["key"], "送信レスポンスに投稿IDがありません"))
            continue
        for attempt in range(4):
            response = requests.get(
                f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
                headers=headers,
                timeout=10,
            )
            if response.status_code != 429:
                break
            wait = max(1.0, float(response.headers.get("Retry-After", "1")))
            print(f"検証429待機: {wait:.1f}秒")
            time.sleep(wait)
        if response.status_code != 200:
            failed.append((post["key"], f"検証HTTP {response.status_code}"))
            continue
        actual = response.json().get("content", "")
        if actual != post["content"]:
            failed.append((post["key"], "本文不一致"))
            continue
        ok += 1
    print(f"検証結果: 本文一致{ok}件 失敗{len(failed)}件")
    for key, reason in failed:
        print(f"検証失敗理由: {key}: {reason}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.dry_run == args.execute:
        parser.error("--dry-run または --execute のどちらか一つを指定してください")
    posts = prepare()
    if args.dry_run:
        print_dry_run(posts)
    else:
        execute(posts)


if __name__ == "__main__":
    main()
