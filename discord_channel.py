"""Collect configured Discord posts and publish new or changed items."""

import hashlib
import os
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
from tl_formatting import extract_tl_text, format_discord_tl

DISCORD_API = "https://discord.com/api/v10"
WAIT_TIME = 0
DEFAULT_LIMIT = 100
MAX_LIMIT = 100
DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 2

YOUTUBE_URL_PATTERN = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)[A-Za-z0-9_-]+"
)
GENERIC_URL_PATTERN = re.compile(r"https?://[^\s<>]+")
BOSS_CODE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])D([1-5])(?:\d{1,2}|T\d{2})?(?![A-Za-z0-9])",
    re.IGNORECASE,
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
    before=None,
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
                params={"limit": limit, **({"before": before} if before else {})},
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


def fetch_channel_messages_for_month(
    channel_id,
    token,
    target_month,
    limit=DEFAULT_LIMIT,
    timeout=DEFAULT_TIMEOUT,
    retries=DEFAULT_RETRIES,
    retry_sleep=time.sleep,
    http_get=requests.get,
):
    """Fetch every page that can contain messages in the target month."""
    collected = []
    before = None
    while True:
        page = fetch_channel_messages(
            channel_id,
            token,
            limit=limit,
            before=before,
            timeout=timeout,
            retries=retries,
            retry_sleep=retry_sleep,
            http_get=http_get,
        )
        if not page:
            break
        collected.extend(
            message for message in page if _is_target_month(message, target_month)
        )
        timestamps = [_message_timestamp(message) for message in page]
        older_than_target = any(
            timestamp is not None
            and (timestamp.year, timestamp.month) < target_month
            for timestamp in timestamps
        )
        if len(page) < limit or older_than_target:
            break
        next_before = str(page[-1].get("id") or "")
        if not next_before or next_before == before:
            break
        before = next_before
        retry_sleep(1)
    return collected


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


def extract_message_urls(message):
    """Collect ordinary URLs from text, embeds, and attachments."""
    urls = set()
    values = [(message.get("content") or "")]
    for embed in message.get("embeds") or []:
        values.extend(embed.get(key) or "" for key in ("url", "video_url", "description"))
    values.extend(
        attachment.get("url") or "" for attachment in message.get("attachments") or []
    )
    for value in values:
        urls.update(GENERIC_URL_PATTERN.findall(value))
    return sorted(url.rstrip(".,)>]}") for url in urls)


def message_bosses(message):
    values = [message.get("content") or ""]
    for embed in message.get("embeds") or []:
        values.extend(
            embed.get(key) or "" for key in ("title", "description", "url")
        )
    return {
        int(match.group(1))
        for value in values
        for match in BOSS_CODE_PATTERN.finditer(value)
    }


def _forced_boss_number():
    channel = os.environ.get("PRICONNER_POST_CHANNEL", "").strip().lower()
    match = re.fullmatch(r"boss([1-5])_tl", channel)
    return int(match.group(1)) if match else None


def _source_channel_boss(channel_id):
    """Map the four source-channel groups ending ①..⑤ to bosses 1..5."""
    channel_ids = _channel_ids()
    try:
        index = channel_ids.index(str(channel_id))
    except ValueError:
        return None
    return index % 5 + 1 if index < 20 else None


SOURCE_CHANNEL_NAMES = {
    # The source-channel list is intentionally kept in config order.  These
    # names make copied Discord posts useful to readers; an ID alone is not.
    "888411637185388585": "四段セミオ_①",
    "888415796617965618": "四段セミオ_②",
    "888415902394105906": "四段セミオ_③",
    "888416089074200597": "四段セミオ_④",
    "888417056351989790": "四段セミオ_⑤",
    "888411707918123018": "四段_①セミオ相談",
    "888415864355954708": "四段_②セミオ相談",
    "888416041351409704": "四段_③セミオ相談",
    "888417120428384297": "四段_④セミオ相談",
    "889752971594829844": "四段手動_②",
    "890430547833286676": "四段手動_②相談",
}


def _source_channel_label(channel_id):
    """Return a human-readable source channel label with a safe fallback."""
    return SOURCE_CHANNEL_NAMES.get(str(channel_id), f"チャンネル {channel_id}")


def _non_tl_context(content):
    raw_tl = extract_tl_text(content or "")
    if not raw_tl:
        return (content or "").strip()
    tl_lines = set(raw_tl.splitlines())
    return "\n".join(
        line for line in (content or "").splitlines() if line.rstrip() not in tl_lines
    ).strip()


def _message_timestamp(message):
    value = message.get("timestamp") or ""
    try:
        return DateTime.fromisoformat(value.replace("Z", "+00:00")).astimezone(datetime.JST)
    except (TypeError, ValueError):
        return None


def _target_month(now_factory):
    value = os.environ.get("PRICONNER_DISCORD_TARGET_MONTH", "").strip()
    if value:
        try:
            year, month = (int(part) for part in value.split("-", 1))
            if 1 <= month <= 12:
                return year, month
        except (TypeError, ValueError):
            print(f"警告: 対象月を解釈できません: {value}")
    current = now_factory()
    return current.year, current.month


def _is_target_month(message, target_month):
    timestamp = _message_timestamp(message)
    # Real Discord payloads always include a timestamp; keep hand-built/test
    # payloads processable when that optional field is absent.
    return timestamp is None or (timestamp.year, timestamp.month) == target_month


def _post_channel_key(content, source_boss=None):
    forced = os.environ.get("PRICONNER_POST_CHANNEL", "").strip()
    if forced:
        return forced
    if source_boss in range(1, 6):
        return f"boss{source_boss}_tl"
    bosses = {
        int(match.group(1))
        for match in BOSS_CODE_PATTERN.finditer(content or "")
    }
    return f"boss{next(iter(bosses))}_tl" if len(bosses) == 1 else "boss0_tl"


def _post_to_assigned_boss(post, content, source_boss=None):
    if post is discord.post:
        return discord.post_to_configured_guilds(
            content, channel_key=_post_channel_key(content, source_boss)
        )
    return post(content)


def _notify_assigned_boss(notify, content):
    if notify is discord.notify:
        return discord.notify_to_configured_guilds(
            content,
            channel_key=os.environ.get("PRICONNER_POST_CHANNEL", "boss0_tl"),
        )
    return notify(content)


def fetch_video_info(url):
    """Fetch lightweight metadata for one YouTube URL."""
    if os.environ.get("PRICONNER_SKIP_VIDEO_INFO"):
        return {"title": url, "publish_date": None, "channel_name": ""}
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
    target_month = _target_month(now_factory)
    print(f"Discord対象月: {target_month[0]:04d}-{target_month[1]:02d}")

    if os.environ.get("PRICONNER_RESET_POSTS"):
        post_channel = os.environ.get("PRICONNER_POST_CHANNEL", "").strip().lower()
        if not re.fullmatch(r"boss[1-5]_tl", post_channel):
            raise ValueError(
                "PRICONNER_RESET_POSTS requires PRICONNER_POST_CHANNEL=boss1_tl..boss5_tl"
            )
        deleted = discord.delete_bot_messages(channel_keys=[post_channel])
        print(f"Discord収集投稿を削除: チャンネル={post_channel} 件数={deleted}")

    ss = spreadsheet or gspread.getNewArrivalsSheet()
    known_urls = set(gspread.getDamagesSheet().worksheet("Youtube").col_values(1))
    initial_known_urls = set(known_urls)
    tracking = ss.worksheet("Discordスキャン")
    tracking_rows = tracking.get_all_values() if hasattr(tracking, "get_all_values") else []
    tracking_by_url = {row[0]: (i, row) for i, row in enumerate(tracking_rows[1:], start=2) if row and row[0]}
    processed_urls = set()
    new_urls = []
    pending_tracking_updates = []
    pending_tracking_inserts = []
    post_success = 0
    post_failures = []
    forced_boss = _forced_boss_number()

    for channel_id in channel_ids:
        source_boss = _source_channel_boss(channel_id)
        if forced_boss is not None and source_boss != forced_boss:
            continue
        print(f"DiscordチャンネルID「{channel_id}」")
        messages = fetch_channel_messages_for_month(
            channel_id,
            token,
            target_month,
            limit=limit,
            timeout=timeout,
            retries=retries,
            retry_sleep=retry_sleep,
            http_get=http_get,
        )
        if not messages:
            continue
        for message in messages:
            if not _is_target_month(message, target_month):
                continue
            if forced_boss is not None:
                codes = message_bosses(message)
                if source_boss is not None:
                    if source_boss != forced_boss or (codes and codes != {forced_boss}):
                        continue
                elif codes != {forced_boss}:
                    continue
            message_content = (message.get("content") or "")[:500]
            youtube_urls = extract_youtube_urls(message)
            message_id = str(message.get("id") or "")
            discord_post_url = (
                f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
                if message_id
                else ""
            )
            records = [(url, url, True) for url in youtube_urls]
            if not youtube_urls:
                message_key = message_id or hashlib.sha256(
                    (channel_id + "\n" + message_content).encode("utf-8")
                ).hexdigest()
                tracking_key = f"discord://{channel_id}/{message_key}"
                discord_url = (
                    discord_post_url
                    if message_id
                    else tracking_key
                )
                records = [(tracking_key, discord_url, False)]
            for tracking_key, source_url, is_youtube in records:
                if tracking_key in processed_urls:
                    continue
                processed_urls.add(tracking_key)
                was_known = tracking_key in known_urls
                if is_youtube:
                    known_urls.add(tracking_key)
                    info = video_info_factory(source_url)
                    title = info.get("title") or source_url
                else:
                    info = {"publish_date": None, "channel_name": "Discord"}
                    title = f"Discord投稿 {message.get('id') or channel_id}"
                publish_date = info.get("publish_date")
                write_arrival(
                    "discord-channel",
                    title,
                    source_url,
                    publish_date,
                    channel_name=info.get("channel_name", ""),
                    notes=(
                        "Discordメッセージから検出"
                        f"（サーバーID {guild_id}、チャンネルID {channel_id}）"
                    ),
                    details={
                        "Discordメッセージ": message_content,
                        "検出時刻": scan_time,
                        "投稿URL": source_url,
                    },
                )
                formatted_tl = ""
                try:
                    formatted_tl = format_discord_tl(message.get("content") or "")
                except RuntimeError as error:
                    print(f"警告: TLフォーマッタを利用できません: {error}")
                author_data = message.get("author") or {}
                author_name = (
                    author_data.get("global_name")
                    or author_data.get("display_name")
                    or author_data.get("username")
                    or author_data.get("id")
                    or "（不明）"
                )
                source_timestamp = _message_timestamp(message)
                source_time = (
                    source_timestamp.strftime("%Y-%m-%d %H:%M:%S %Z")
                    if source_timestamp else "（不明）"
                )
                source_channel_name = _source_channel_label(channel_id)
                source_channel_link = (
                    f"https://discord.com/channels/{guild_id}/{channel_id}"
                    if guild_id and channel_id
                    else ""
                )
                source_channel_text = (
                    f"[{source_channel_name}]({source_channel_link})"
                    if source_channel_link
                    else source_channel_name
                )
                metadata = (
                    f"投稿者: {author_name}\n"
                    f"元投稿日時: {source_time}\n"
                    f"チャンネル: {source_channel_text}\n"
                    f"検出日時: {scan_time}"
                )
                if is_youtube:
                    source_link = (
                        f"\n投稿元リンク: {discord_post_url}"
                        if discord_post_url else ""
                    )
                    body = (
                        f"{metadata}\n"
                        f"動画タイトル: {title}\n"
                        f"動画URL: {source_url}\n"
                        f"{post_tracker.markdown_note_line('Discordメッセージから検出')}\n"
                        f"{source_link}"
                    )
                    if formatted_tl:
                        formatted_body = post_tracker.add_post_separator(
                            f"{body}\n\nTL（整形済み）:\n```scm\n"
                            f"{formatted_tl}\n```"
                        )
                        if len(formatted_body) <= 1950:
                            body = formatted_body
                else:
                    raw_message = (message.get("content") or "").strip()
                    source_link = (
                        f"\n投稿元リンク: {discord_post_url}"
                        if discord_post_url else ""
                    )
                    body = (
                        f"{metadata}\n"
                        f"Discord投稿本文: {raw_message or '（本文なし）'}"
                        f"{source_link}"
                    )
                    if formatted_tl:
                        formatted_body = post_tracker.add_post_separator(
                            f"{body}\n\nTL（整形済み）:\n```scm\n"
                            f"{formatted_tl}\n```"
                        )
                        if len(formatted_body) <= 1950:
                            body = formatted_body
                body = post_tracker.add_post_separator(body)
                comparison = post_tracker.comparison_text(body)
                digest = hashlib.sha256(comparison.encode("utf-8")).hexdigest()
                old = tracking_by_url.get(tracking_key)
                previous = old[1][1] if old and len(old[1]) > 1 else ""
                row = [tracking_key, body, digest, scan_time, scan_time if old else "", previous, channel_id, str(message.get("id") or "")]
                if was_known and not old:
                    status = "same"
                else:
                    status = "new" if not old else ("updated" if len(old[1]) < 3 or old[1][2] != digest else "same")
                if os.environ.get("PRICONNER_FORCE_POST") and old:
                    status = "updated"
                if os.environ.get("PRICONNER_FORCE_NEW_POST") and old:
                    status = "new"
                if status == "same":
                    if not old:
                        pending_tracking_inserts.append(row)
                    continue
                if old and not os.environ.get("PRICONNER_FORCE_NEW_POST"):
                    pending_tracking_updates.append((old[0], row))
                elif not old:
                    pending_tracking_inserts.append(row)
                new_urls.append({"url": source_url, "tracking_key": tracking_key, "is_youtube": is_youtube, "source_boss": source_boss, "text": body, "status": status, "previous_text": previous})
                print(f"new: {source_url}")
        retry_sleep(wait_time)

    if pending_tracking_updates:
        if hasattr(tracking, "batch_update"):
            tracking.batch_update(
                [
                    {"range": f"A{row_number}:H{row_number}", "values": [row]}
                    for row_number, row in pending_tracking_updates
                ],
                raw=False,
                value_input_option="USER_ENTERED",
            )
        elif hasattr(tracking, "update"):
            for row_number, row in pending_tracking_updates:
                tracking.update(
                    f"A{row_number}:H{row_number}",
                    [row],
                    value_input_option="USER_ENTERED",
                )
    if pending_tracking_inserts and hasattr(tracking, "insert_rows"):
        tracking.insert_rows(
            pending_tracking_inserts,
            row=2,
            value_input_option="USER_ENTERED",
        )

    if not new_urls:
        print("Discord投稿結果: 成功0件 失敗0件 対象0件")
        return

    sheet = gspread.getDamagesSheet().worksheet("Youtube")
    gspread.writeToFirstEmptyCells(
        sheet,
        [item["url"] for item in new_urls
         if item.get("is_youtube") and item.get("status") == "new"
         and item["url"] not in initial_known_urls],
        wait_time=WAIT_TIME,
    )

    limit = 1 if os.environ.get("PRICONNER_FORCE_NEW_LIMIT_ONE") else int(os.environ.get("PRICONNER_FORCE_POST_LIMIT", "0") or 0)
    post_items = new_urls[:limit] if limit else new_urls
    for item in post_items:
        if os.environ.get("PRICONNER_NO_POST"):
            continue
        try:
            _post_to_assigned_boss(
                post,
                post_tracker.post_content(item),
                item.get("source_boss"),
            )
            post_success += 1
        except Exception as error:
            post_failures.append((item["url"], str(error)))
            print(f"失敗: Discord URL通知 {item['url']}: {error}")
        retry_sleep(wait_time)

    status_counts = {}
    for item in post_items:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
    print(
        "Discord投稿結果: "
        f"成功{post_success}件 失敗{len(post_failures)}件 "
        f"状態={status_counts}"
    )

    try:
        _notify_assigned_boss(notify, f"Discord新着{len(new_urls)}件")
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
