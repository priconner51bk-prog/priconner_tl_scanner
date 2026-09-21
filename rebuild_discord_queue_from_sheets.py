"""Rebuild the production Discord queue from durable spreadsheet rows.

This is intentionally an enqueue-only operation.  It never calls YouTube or
Discord discovery APIs and never drains the queue.  Every selected row is
treated as a new post, which is useful after queue/history cleanup.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord_channel
import discord_queue
import gspread_utils as gspread
import post_change_tracker as post_tracker
from video_relevance import (
    clan_battle_filter_reason,
    load_content_period,
    load_ng_terms,
)
from worrychefs import build_worrychefs_content, build_worrychefs_split_messages
from youtube_channel import _boss_channel_key


JST = ZoneInfo("Asia/Tokyo")
DEFAULT_TARGET_MONTH = "2026-08"
WORRY_KEY_RE = re.compile(r"^[^:]+:(D[1-5](?:T\d{2}|\d{1,2}))$", re.IGNORECASE)


def _period_bounds(spreadsheet, target_month):
    fallback_start = gspread.get_config_value("youtube", "content_period_start", "")
    fallback_end = gspread.get_config_value("youtube", "content_period_end", "")
    start, end = load_content_period(
        spreadsheet,
        target_month,
        fallback_start,
        fallback_end,
    )
    return _parse_datetime(start), _parse_datetime(end)


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("/", "-")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    parsed = None
            if parsed is None:
                return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(timezone.utc)


def _in_period(value, start, end):
    parsed = _parse_datetime(value)
    return bool(parsed and start and end and start <= parsed < end)


def _snowflake_datetime(message_id):
    try:
        milliseconds = (int(str(message_id)) >> 22) + 1420070400000
        return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _source_boss(channel_id):
    return discord_channel._source_channel_boss(str(channel_id))


def _channel_key(boss):
    return f"boss{boss}_tl" if boss in range(0, 6) else "boss0_tl"


def _enqueue(content, channel_key, dedupe_key, path=None):
    return discord_queue.enqueue_for_guilds(
        content,
        channel_key=channel_key,
        guild_keys=("production",),
        dedupe_key=dedupe_key,
        path=path,
    )


def load_youtube_posts(spreadsheet, target_month, start, end, ng_terms):
    sheet = spreadsheet.worksheet("YouTube動画")
    boss_rows = spreadsheet.worksheet("ボス名").get_all_values()
    boss_names = [row[0] for row in boss_rows[1:6] if row and row[0]]
    posts = []
    rejected = Counter()
    seen = set()
    for row in sheet.get_all_values()[1:]:
        if len(row) < 9:
            rejected["動画備考・TL整形・投稿本文未保存"] += 1
            continue
        published = _parse_datetime(row[2])
        if not _in_period(published, start, end):
            rejected["期間外または日時不明"] += 1
            continue
        url = row[4].strip()
        if not url or url in seen:
            continue
        seen.add(url)
        video = {
            "title": row[3],
            "description": row[6],
            "tags": (),
            "publish_date": published,
        }
        reason = clan_battle_filter_reason(
            video,
            boss_names=boss_names,
            ng_terms=ng_terms,
            content_month=target_month,
            content_period_start=start.isoformat(),
            content_period_end=end.isoformat(),
        )
        if reason:
            rejected[reason] += 1
            continue
        if not row[5].strip() or not row[7].strip() or not row[8].strip():
            rejected["動画備考・TL整形・投稿本文未保存"] += 1
            continue
        channel_key = _boss_channel_key(row[3], boss_names)
        content = row[8].strip()
        posts.append(
            (channel_key, content, f"sheet-rebuild:youtube:{url}", url, row[3])
        )
    return posts, rejected


def load_discord_posts(spreadsheet, start, end, ng_terms):
    sheet = spreadsheet.worksheet("Discordスキャン")
    posts = []
    rejected = Counter()
    seen = set()
    for row in sheet.get_all_values()[1:]:
        if len(row) < 8 or not row[0] or not row[1] or not row[7]:
            continue
        source_time = _snowflake_datetime(row[7])
        if not _in_period(source_time, start, end):
            rejected["期間外または日時不明"] += 1
            continue
        if row[0] in seen:
            continue
        seen.add(row[0])
        body = row[1]
        hit = next(
            (term for term in ng_terms if str(term).casefold() in body.casefold()),
            "",
        )
        if hit:
            rejected[f"NGワード: {hit}"] += 1
            continue
        source_boss = _source_boss(row[6])
        if source_boss not in range(1, 6):
            rejected["送信元ボス不明"] += 1
            continue
        posts.append(
            (
                _channel_key(source_boss),
                body,
                f"sheet-rebuild:discord:{row[0]}",
                row[0],
                f"送信元boss{source_boss}",
            )
        )
    return posts, rejected


def load_worrychefs_posts(spreadsheet, detected_at="sheet-rebuild"):
    sheet = spreadsheet.worksheet("WorryChefs TL")
    posts = []
    rejected = Counter()
    for row in sheet.get_all_values()[1:]:
        if len(row) < 7 or not row[0] or not row[1]:
            continue
        match = WORRY_KEY_RE.fullmatch(row[0].strip())
        if not match:
            rejected["ボスコード不明"] += 1
            continue
        source, code = row[0].split(":", 1)
        record = {
            "key": row[0],
            "text": row[1],
            "hash": row[2] if len(row) > 2 else "",
            "first_seen": row[3] if len(row) > 3 else "",
            "updated_at": row[4] if len(row) > 4 else "",
            "source": source,
            "url": row[6],
            "status": "new",
            "force_full": True,
            "code": code.upper(),
            "damage": "",
            "author": "",
            "formation_md": "",
        }
        post_text = post_tracker.post_content(record)
        content = build_worrychefs_content(record, post_text, detected_at)
        messages = (
            [content]
            if content is not None
            else build_worrychefs_split_messages(record, post_text, detected_at)
        )
        boss = int(code[1])
        for index, message in enumerate(messages, 1):
            posts.append(
                (
                    _channel_key(boss),
                    message,
                    f"sheet-rebuild:worry:{row[0]}:{index}",
                    row[0],
                    f"{code} {index}/{len(messages)}",
                )
            )
    return posts, rejected


def prepare(target_month=DEFAULT_TARGET_MONTH, path=None):
    spreadsheet = gspread.getNewArrivalsSheet()
    start, end = _period_bounds(spreadsheet, target_month)
    if not start or not end or start >= end:
        raise RuntimeError("クラバト期間が解決できません")
    ng_terms = load_ng_terms(spreadsheet)
    youtube_posts, youtube_rejected = load_youtube_posts(
        spreadsheet, target_month, start, end, ng_terms
    )
    discord_posts, discord_rejected = load_discord_posts(spreadsheet, start, end, ng_terms)
    worry_posts, worry_rejected = load_worrychefs_posts(spreadsheet)
    all_posts = youtube_posts + discord_posts + worry_posts
    if not all(len(item[1]) <= 2000 for item in all_posts):
        oversized = [item[3] for item in all_posts if len(item[1]) > 2000]
        raise RuntimeError(f"Discord上限超過のため中止: {len(oversized)}件")
    inserted = Counter()
    inserted_by_source = Counter()
    for channel_key, content, dedupe_key, source_key, label in all_posts:
        ids = _enqueue(content, channel_key, dedupe_key, path=path)
        if ids:
            inserted[channel_key] += 1
            inserted_by_source[dedupe_key.split(":", 2)[1]] += 1
    print(f"対象期間: {start.astimezone(JST).isoformat()} ～ {end.astimezone(JST).isoformat()}")
    print(f"NGワード件数: {len(ng_terms)}")
    print(f"YouTube: 対象{len(youtube_posts)} / 追加{inserted_by_source['youtube']} / 除外{dict(youtube_rejected)}")
    print(f"Discordシート: 対象{len(discord_posts)} / 追加{inserted_by_source['discord']} / 除外{dict(discord_rejected)}")
    print(f"WorryChefs: 対象{len(worry_posts)} / 追加{inserted_by_source['worry']} / 除外{dict(worry_rejected)}")
    print(f"追加件数: {dict(inserted)}")
    print(f"キュー残件数: {discord_queue.pending_count(path=path)}")
    return len(all_posts), sum(inserted.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true", help="本番キューへ積む。送信はしない")
    parser.add_argument("--target-month", default=DEFAULT_TARGET_MONTH)
    parser.add_argument("--queue-db", default=None)
    args = parser.parse_args()
    if not args.prepare:
        parser.error("安全のため --prepare を明示してください")
    prepare(args.target_month, args.queue_db)


if __name__ == "__main__":
    main()
