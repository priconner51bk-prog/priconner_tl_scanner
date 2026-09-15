"""Scrape YouTube links from configured Discord server channels."""

import os
import hashlib
import re
import time
from datetime import datetime as DateTime
from datetime import timezone

import requests
from yt_dlp import YoutubeDL

import datetime_utils as datetime
import discord_utils as discord
import gspread_utils as gspread
import post_change_tracker as post_tracker
from new_arrivals_markdown import write_arrival
from runtime_utils import run_locked
from tl_formatting import format_discord_tl

DISCORD_API = "https://discord.com/api/v10"
WAIT_TIME = 0
DEFAULT_LIMIT = 100
MAX_LIMIT = 100
DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 2

YOUTUBE_URL_PATTERN = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)[A-Za-z0-9_-]+"
)


def _token():
    return (
        gspread.get_config_value(
            "discord_channel", "token", os.environ.get("DISCORD_TOKEN")
        )
        or ""
    )


def _guild_id():
    return (
        gspread.get_config_value(
            "discord_channel", "guild_id", os.environ.get("DISCORD_GUILD_ID")
        )
        or ""
    )


def _channel_ids():
    raw = gspread.get_config_value(
        "discord_channel", "channel_ids", os.environ.get("DISCORD_CHANNEL_IDS", "")
    ) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def fetch_channel_messages(
    channel_id,
    token,
    limit=DEFAULT_LIMIT,
    timeout=DEFAULT_TIMEOUT,
    retries=DEFAULT_RETRIES,
    retry_sleep=time.sleep,
    http_get=requests.get,
):
    """Fetch the newest messages of one channel, honoring Discord rate limits."""
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    # User tokens are sent as-is. (A bot token would need the "Bot " prefix, but
    # this stage targets accounts that cannot register a bot.)
    headers = {"Authorization": token}
    for attempt in range(retries + 1):
        try:
            response = http_get(
                url,
                params={"limit": limit},
                headers=headers,
                timeout=timeout,
            )
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", "1"))
                retry_sleep(max(1, int(retry_after)))
                continue
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            if attempt >= retries:
                return None
            retry_sleep(2**attempt)
    return None


def _normalize_youtube_url(url):
    """Normalize any YouTube URL form to the canonical watch?v=ID form."""
    match = re.search(
        r"(?:watch\?v=|shorts/|live/|youtu\.be/)([A-Za-z0-9_-]+)", url
    )
    if match:
        return f"https://www.youtube.com/watch?v={match.group(1)}"
    return url


def extract_youtube_urls(message):
    """Collect YouTube links from the message text, embeds, and attachments."""
    urls = set()
    for value in [message.get("content") or ""]:
        urls.update(YOUTUBE_URL_PATTERN.findall(value))
    for embed in message.get("embeds") or []:
        for key in ("url", "video_url"):
            value = embed.get(key)
            if value:
                urls.update(YOUTUBE_URL_PATTERN.findall(value))
    for attachment in message.get("attachments") or []:
        value = attachment.get("url")
        if value:
            urls.update(YOUTUBE_URL_PATTERN.findall(value))
    return sorted({_normalize_youtube_url(url) for url in urls})


def fetch_video_info(url):
    """Fetch lightweight metadata for one YouTube URL."""
    options = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "ignoreerrors": True,
        "remote_components": ["ejs:github"],
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    timestamp = info.get("timestamp")
    publish_date = None
    if timestamp is not None:
        publish_date = DateTime.fromtimestamp(timestamp, tz=timezone.utc)
    elif info.get("upload_date"):
        try:
            publish_date = DateTime.strptime(
                info["upload_date"], "%Y%m%d"
            ).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            publish_date = None
    return {
        "title": info.get("title", ""),
        "publish_date": publish_date,
        "channel_name": info.get("channel") or info.get("uploader", ""),
    }


def checkNewArrivalsForDiscordChannel(
    spreadsheet=None,
    http_get=requests.get,
    post=discord.post,
    notify=discord.notify,
    video_info_factory=fetch_video_info,
    now_factory=datetime.now,
    timeout=DEFAULT_TIMEOUT,
    retries=DEFAULT_RETRIES,
    retry_sleep=time.sleep,
    wait_time=WAIT_TIME,
):
    print("新着チェック対象:Discordチャンネル")

    token = _token()
    channel_ids = _channel_ids()
    if not token or not channel_ids:
        print("skip: Discordチャンネル監視が設定されていません")
        return

    guild_id = _guild_id()
    limit = gspread.get_int_config_value(
        "discord_channel", "limit", DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT
    )
    scan_time = datetime.dateTime2String(now_factory())

    known_urls = set(gspread.getDamagesSheet().worksheet("Youtube").col_values(1))
    tracking = gspread.getNewArrivalsSheet().worksheet("Discordスキャン")
    tracking_rows = tracking.get_all_values()
    tracking_by_url = {row[0]: (i, row) for i, row in enumerate(tracking_rows[1:], start=2) if row and row[0]}
    new_urls = []

    for channel_id in channel_ids:
        print(f"DiscordチャンネルID「{channel_id}」")
        messages = fetch_channel_messages(
            channel_id,
            token,
            limit=limit,
            timeout=timeout,
            retries=retries,
            retry_sleep=retry_sleep,
            http_get=http_get,
        )
        if not messages:
            continue
        for message in messages:
            message_content = (message.get("content") or "")[:500]
            for url in extract_youtube_urls(message):
                if url in known_urls:
                    continue
                known_urls.add(url)
                info = video_info_factory(url)
                title = info.get("title") or url
                publish_date = info.get("publish_date")
                write_arrival(
                    "discord-channel",
                    title,
                    url,
                    publish_date,
                    channel_name=info.get("channel_name", ""),
                    notes=(
                        "Discordメッセージから検出"
                        f"（サーバーID {guild_id}、チャンネルID {channel_id}）"
                    ),
                    details={
                        "Discordメッセージ": message_content,
                        "検出時刻": scan_time,
                    },
                )
                formatted_tl = ""
                try:
                    formatted_tl = format_discord_tl(message.get("content") or "")
                except RuntimeError as error:
                    print(f"警告: TLフォーマッタを利用できません: {error}")
                comparison = formatted_tl or title
                digest = hashlib.sha256(comparison.encode("utf-8")).hexdigest()
                old = tracking_by_url.get(url)
                status = "new" if not old else ("updated" if len(old[1]) < 3 or old[1][2] != digest else "same")
                if status == "same":
                    continue
                previous = old[1][1] if old and len(old[1]) > 1 else ""
                row = [url, comparison, digest, scan_time, scan_time if old else "", previous, channel_id, str(message.get("id") or "")]
                if old:
                    tracking.update(f"A{old[0]}:H{old[0]}", [row], value_input_option="USER_ENTERED")
                else:
                    tracking.insert_rows([row], row=2, value_input_option="USER_ENTERED")
                body = f"{url}\n\n{formatted_tl}" if formatted_tl else url
                new_urls.append({"url": url, "text": body, "status": status, "previous_text": previous})
                print(f"new: {url}")
        retry_sleep(wait_time)

    if not new_urls:
        return

    sheet = gspread.getDamagesSheet().worksheet("Youtube")
    gspread.writeToFirstEmptyCells(sheet, [item["url"] for item in new_urls], wait_time=WAIT_TIME)

    for item in new_urls:
        if os.environ.get("PRICONNER_NO_POST"):
            continue
        try:
            post(post_tracker.post_content(item))
        except Exception as error:
            print(f"失敗: Discord URL通知 {item['url']}: {error}")
        retry_sleep(wait_time)

    try:
        notify(f"Discord新着{len(new_urls)}件")
    except Exception as error:
        print(f"失敗: Discord集計通知: {error}")


def main():
    def run():
        print("-----------------------------------------------")
        print(f"開始{datetime.nowString()}")
        print("-----------------------------------------------")
        checkNewArrivalsForDiscordChannel()
        print("-----------------------------------------------")
        print(f"終了{datetime.nowString()}")
        print("-----------------------------------------------")

    return run_locked(run, lock_name="discord_channel.lock")


if __name__ == "__main__":
    main()
