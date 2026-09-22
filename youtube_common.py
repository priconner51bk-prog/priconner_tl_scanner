"""Shared helpers for YouTube search and channel scanners."""

import time
import os
from datetime import datetime as DateTime
from datetime import timedelta, timezone

import datetime_utils as datetime
from tl_formatting import format_discord_tl
from post_change_tracker import (
    POST_SEPARATOR,
    add_post_separator,
    markdown_note_line,
    suppress_discord_embeds,
)


def as_utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=datetime.JST).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def build_youtube_post(url, title, description, formatted_tl, notes,
                       channel_key, status="new", **extra):
    """Create the shared posting form used by channel and keyword scans."""
    item = {
        "url": url,
        "title": title,
        "description": description,
        "formatted_tl": formatted_tl,
        "notes": notes,
        "channel_key": channel_key,
        "status": status,
    }
    item.update(extra)
    return item


def format_youtube_tl(description):
    """Format TL text extracted from a YouTube description when available."""
    try:
        return format_discord_tl(description)
    except (RuntimeError, ValueError) as error:
        print(f"警告: YouTube概要欄のTL整形を利用できません: {error}")
        return ""


def write_urls_with_retry(write_urls, urls, sleep=time.sleep, retries=2):
    for attempt in range(retries + 1):
        try:
            return write_urls(urls)
        except Exception as error:
            if attempt >= retries:
                raise
            print(f"URL登録を再試行します ({attempt + 1}/{retries}): {error}")
            sleep(2**attempt)


def unique_urls(urls):
    return list(dict.fromkeys(url for url in urls if url))


VIDEO_HEADERS = [
    "チャンネル名", "チャンネルURL", "投稿日", "動画タイトル", "動画URL",
    "動画備考", "概要欄", "TL整形", "投稿直前本文",
]
VIDEO_NOTES_INDEX = 5
VIDEO_DESCRIPTION_INDEX = 6
VIDEO_FORMATTED_TL_INDEX = 7
VIDEO_POST_BODY_INDEX = 8


def _month_start(year, month):
    return DateTime(year, month, 1, tzinfo=datetime.JST).astimezone(timezone.utc)


def _next_month(year, month):
    if month == 12:
        return year + 1, 1
    return year, month + 1


def current_month_key(now):
    """Return the JST calendar month for a timestamp as YYYY-MM."""
    local_now = as_utc(now).astimezone(datetime.JST)
    return f"{local_now.year:04d}-{local_now.month:02d}"


def effective_content_month(content_month, period_month, now):
    """Resolve an optional content month, defaulting to the JST current month."""
    return (
        str(content_month or "").strip()
        or str(period_month or "").strip()
        or current_month_key(now)
    )


def youtube_period_bounds(now, period_days, period_mode="current_month", period_month=""):
    """Return the UTC half-open bounds for the configured collection period."""
    now = as_utc(now)
    override_start = os.environ.get("PRICONNER_YOUTUBE_PERIOD_START", "").strip()
    override_end = os.environ.get("PRICONNER_YOUTUBE_PERIOD_END", "").strip()
    if override_start and override_end:
        try:
            start = as_utc(DateTime.fromisoformat(override_start))
            end = as_utc(DateTime.fromisoformat(override_end))
        except ValueError as error:
            raise ValueError("YouTube period overrides must be ISO datetimes") from error
        if start >= end:
            raise ValueError("YouTube period start must precede end")
        return start, end
    mode = str(period_mode or "days").strip().lower()
    configured_month = str(period_month or "").strip()
    if mode in {"month", "current_month", "target_month"} and configured_month:
        try:
            year, month = (int(value) for value in configured_month.split("-", 1))
            if not 1 <= month <= 12:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("period_month must be YYYY-MM")
        start = _month_start(year, month)
        next_year, next_month = _next_month(year, month)
        return start, _month_start(next_year, next_month)
    if mode in {"month", "current_month", "target_month"}:
        local_now = now.astimezone(datetime.JST)
        start = _month_start(local_now.year, local_now.month)
        return start, now
    # Preserve the historical trailing-days behavior: it has a lower bound
    # but does not reject a future-dated result returned by the provider.
    return now - timedelta(days=period_days), None


def youtube_cutoff(now, period_days, period_mode="current_month", period_month=""):
    """Return the UTC lower bound for the configured YouTube collection period."""
    return youtube_period_bounds(now, period_days, period_mode, period_month)[0]


def is_in_youtube_period(
    publish_date, now, period_days, period_mode="current_month", period_month=""
):
    """Return whether a known YouTube publication date is in scope."""
    if publish_date is None:
        return False
    start, end = youtube_period_bounds(
        now, period_days, period_mode, period_month
    )
    publish_date = as_utc(publish_date)
    return start <= publish_date and (end is None or publish_date < end)


DISCORD_MESSAGE_LIMIT = 2000


def _clip_middle(value, limit):
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    if limit <= 1:
        return "…"[:limit]
    return value[: limit - 1].rstrip() + "…"


def video_post_body(title, notes, url, description="", formatted_tl=""):
    """Build a YouTube post with title, URL, then notes in that order."""
    header = f"動画タイトル: {title}\n動画URL: {url}\n"
    if str(notes or "").strip():
        header += markdown_note_line(notes) + "\n"
    blocks = []
    raw_description = str(description or "").strip()
    description = suppress_discord_embeds(raw_description)
    formatted_tl = suppress_discord_embeds(str(formatted_tl or "").strip())
    if description and not formatted_tl:
        formatted_tl = suppress_discord_embeds(format_youtube_tl(raw_description))
    if description:
        blocks.append(("概要欄:\n", description))
    if formatted_tl:
        blocks.append(("TL（整形済み）:\n```scm\n", formatted_tl + "\n```"))
    if not blocks:
        return add_post_separator(header)

    separator_prefix = f"{POST_SEPARATOR}\n"
    available = max(
        0, DISCORD_MESSAGE_LIMIT - len(separator_prefix) - len(header) - 6
    )
    rendered = []
    for index, (prefix, content) in enumerate(blocks):
        remaining_blocks = len(blocks) - index - 1
        reserve = sum(len(p) + len(c) + 2 for p, c in blocks[index + 1:])
        budget = max(0, available - reserve) if remaining_blocks else available
        clipped = _clip_middle(content, max(0, budget - len(prefix)))
        rendered.append(prefix + clipped)
        available -= len(prefix) + len(clipped) + 2
    return add_post_separator("\n\n".join([header, *rendered]))


def build_video_sheet_row(
    channel_name,
    channel_url,
    published_at,
    title,
    url,
    notes,
    description,
    formatted_tl,
    post_body=None,
):
    """Return one durable YouTube row with all fields needed for replay."""
    if post_body is None:
        post_body = video_post_body(
            title, notes, url, description, formatted_tl
        )
    return [
        channel_name,
        channel_url,
        published_at,
        title,
        url,
        notes,
        description,
        formatted_tl,
        post_body,
    ]


def video_metadata_fields(title, notes, url, description):
    """Normalize fetched metadata and build the exact post body once."""
    description = str(description or "").strip()
    formatted_tl = format_youtube_tl(description) if description else ""
    post_body = video_post_body(
        title, notes, url, description, formatted_tl
    )
    return description, formatted_tl, post_body
