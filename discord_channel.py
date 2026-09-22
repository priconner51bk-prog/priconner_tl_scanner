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
import youtube_handoff
from new_arrivals_markdown import write_arrival
from runtime_utils import run_locked
from tl_formatting import extract_tl_text, format_discord_tl
from video_relevance import (
    clan_battle_filter_reason,
    load_ng_terms,
)
from youtube_common import effective_content_month

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


def _is_non_content_discord_message(content):
    """Ignore empty or month-boundary marker messages from source channels."""
    normalized = post_tracker.normalize_comparison_text(content)
    if not normalized:
        return True
    return bool(
        re.fullmatch(
            r"=+\s*ここから\s*\d{4}年\d{1,2}月\s*=+",
            normalized,
        )
    )


def _tracking_status(was_known, old, previous, comparison):
    """Classify material changes while migrating hashes from older formats."""
    if not old:
        return "same" if was_known else "new"
    return (
        "same"
        if post_tracker.comparison_text(previous) == comparison
        else "updated"
    )


def _notify_assigned_boss(notify, content):
    # The scheduled runner creates one summary from the complete Discord
    # send queue after every collector has finished. Avoid sending the
    # Discord-only summary here as well.
    if os.environ.get("PRICONNER_MONITOR_RUNTIME_DIR"):
        return None
    if notify is discord.notify:
        return discord.notify_summary_to_configured_guilds(content)
    return notify(content)


def _summary_channel_key(item):
    """Return the destination boss channel for a queued post item."""
    if item.get("channel_key"):
        return item["channel_key"]
    return _post_channel_key(item.get("text", ""), item.get("source_boss"))


def _summary_boss_label(channel_key):
    match = re.fullmatch(r"boss([0-5])_tl", str(channel_key or ""))
    if not match:
        return "ボス不明"
    number = int(match.group(1))
    return f"ボス{number}" if number else "ボス0（判定不能）"


def _summary_title(title):
    """Keep a title on one readable line in the Discord summary."""
    return " ".join(str(title or "").split())


def _summary_is_youtube(item):
    if "is_youtube" in item:
        return bool(item["is_youtube"])
    content = str(item.get("content") or item.get("text") or "")
    return bool(re.search(r"(?m)^\s*動画(?:タイトル|URL):", content))


def _summary_item_status(item):
    status = item.get("status")
    if status:
        return status
    content = str(item.get("content") or item.get("text") or "")
    return "updated" if "【差分】" in content or "更新" in content else "new"


def _summary_item_title(item):
    title = item.get("title")
    if title:
        return title
    content = str(item.get("content") or item.get("text") or "")
    match = re.search(r"(?m)^\s*(?:\+\s*)?動画タイトル:\s*(.+?)\s*$", content)
    return match.group(1) if match else ""


def _summary_item_author(item):
    author = item.get("summary_author")
    if author:
        return _summary_title(author)
    content = str(item.get("content") or item.get("text") or "")
    match = re.search(r"(?m)^\s*(?:\+\s*)?投稿者:\s*(.+?)\s*$", content)
    return _summary_title(match.group(1)) if match else "（不明）"


def _summary_body_excerpt(item, limit=30):
    content = str(item.get("content") or item.get("text") or "")
    match = re.search(r"(?m)^\s*Discord投稿本文:\s*(.*)$", content)
    if match:
        first_line = match.group(1).strip()
    else:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        first_line = next(
            (line for line in lines if not line.startswith("━")),
            "",
        )
    return _summary_title(first_line)[:limit]


def _build_post_summary(items):
    """Build a compact boss/type/status summary for Discord notifications."""
    groups = {}
    queue_preview = any("post_result" not in item for item in items)
    result_counts = {"成功": 0, "失敗": 0, "未送信": 0, "送信予定": 0}
    for item in items:
        result = item.get("post_result", "送信予定")
        result_counts[result] = result_counts.get(result, 0) + 1
        channel_key = _summary_channel_key(item)
        group = groups.setdefault(
            channel_key,
            {"items": [], "statuses": {}, "kinds": {}, "titles": []},
        )
        group["items"].append(item)
        status = _summary_item_status(item)
        group["statuses"][status] = group["statuses"].get(status, 0) + 1
        kind = "YouTube" if _summary_is_youtube(item) else "Discord本文"
        group["kinds"][kind] = group["kinds"].get(kind, 0) + 1
        title = _summary_title(_summary_item_title(item))
        if title:
            group["titles"].append(title)

    status_labels = {"new": "新規", "updated": "更新", "same": "変更なし"}
    ordered_keys = sorted(
        groups,
        key=lambda key: int(re.fullmatch(r"boss([0-5])_tl", key).group(1))
        if re.fullmatch(r"boss([0-5])_tl", key)
        else 99,
    )
    if queue_preview:
        lines = [f"Discord投稿サマリー（対象{len(items)}件 / 送信予定{len(items)}件）"]
    else:
        lines = [
            (
                f"Discord投稿サマリー（対象{len(items)}件 / 成功{result_counts.get('成功', 0)}件 / "
                f"失敗{result_counts.get('失敗', 0)}件）"
            )
        ]
    if result_counts.get("未送信"):
        lines[0] += f" / 未送信{result_counts['未送信']}件"

    for channel_key in ordered_keys:
        group = groups[channel_key]
        status_text = " / ".join(
            f"{status_labels.get(status, status)}{count}件"
            for status, count in group["statuses"].items()
        )
        kind_text = " / ".join(
            f"{kind}{count}件" for kind, count in group["kinds"].items()
        )
        lines.append(f"\n{_summary_boss_label(channel_key)}: {len(group['items'])}件")
        lines.append(f"状態: {status_text}")
        lines.append(f"種別: {kind_text}")
        for item in group["items"]:
            title = _summary_title(_summary_item_title(item))
            author = _summary_item_author(item)
            if title:
                lines.append(f"- タイトル: {title} / 投稿者: {author}")
            else:
                excerpt = _summary_body_excerpt(item) or "（本文なし）"
                lines.append(f"- 本文: {excerpt} / 投稿者: {author}")

    summary = "\n".join(lines)
    # Discord rejects messages over 2000 characters. Keep the per-boss counts
    # intact and only trim the tail of an unusually large title list.
    if len(summary) > 1950:
        summary = summary[:1900].rstrip() + "\n（タイトル一覧の一部は省略）"
    return summary


DISCORD_BODY_LIMIT = 1950


def _clip_discord_text(value, limit):
    """Clip one post section without exceeding Discord's safe body limit."""
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    if limit <= 1:
        return "…"[:limit]
    return value[: limit - 1].rstrip() + "…"


def _build_discord_post_body(prefix, raw_message, source_link, formatted_tl):
    """Build a Discord post while preserving the formatted TL when possible."""
    raw_value = raw_message or "（本文なし）"
    raw_prefix = "Discord投稿本文: "
    tl_prefix = "\n\nTL（整形済み）:\n```scm\n"
    tl_suffix = "\n```"
    separator_length = len(post_tracker.POST_SEPARATOR) + 1
    body_limit = max(0, DISCORD_BODY_LIMIT - separator_length)
    formatted_tl = post_tracker.suppress_discord_embeds(formatted_tl)

    # Reserve space for the complete formatted TL before shortening the raw
    # Discord content. This avoids silently dropping the TL just because the
    # source message itself is long.
    tl_length = (
        len(tl_prefix) + len(formatted_tl) + len(tl_suffix)
        if formatted_tl
        else 0
    )
    raw_limit = max(
        0,
        body_limit
        - len(prefix)
        - len(raw_prefix)
        - len(source_link)
        - tl_length,
    )
    body = (
        f"{prefix}\n"
        f"{raw_prefix}{_clip_discord_text(raw_value, raw_limit)}"
        f"{source_link}"
    )

    if not formatted_tl:
        return body

    tl_limit = max(
        0,
        body_limit
        - len(body)
        - len(tl_prefix)
        - len(tl_suffix),
    )
    clipped_tl = _clip_discord_text(formatted_tl, tl_limit)
    if not clipped_tl:
        return body
    return f"{body}{tl_prefix}{clipped_tl}{tl_suffix}"


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
    known_urls = gspread.existing_column_values(
        gspread.getDamagesSheet().worksheet("Youtube"), 1
    )
    initial_known_urls = set(known_urls)
    tracking = ss.worksheet("Discordスキャン")
    try:
        boss_rows = ss.worksheet("ボス名").get_all_values()
        boss_names = [row[0] for row in boss_rows[1:] if row and row[0]]
    except Exception:
        boss_names = []
    ng_terms = load_ng_terms(ss)
    now = now_factory()
    period_month = (
        os.environ.get("PRICONNER_YOUTUBE_PERIOD_MONTH", "").strip()
        or gspread.get_config_value("youtube", "period_month", "")
    )
    content_month = effective_content_month(
        os.environ.get("PRICONNER_YOUTUBE_CONTENT_MONTH", "").strip()
        or gspread.get_config_value("youtube", "content_month", ""),
        period_month,
        now,
    )
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
            if not youtube_urls and _is_non_content_discord_message(
                message.get("content")
            ):
                print(f"skip: Discord収集の空本文・月境界マーカー {message.get('id') or channel_id}")
                continue
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
                    video_title = info.get("title") or ""
                    title = video_title or source_url
                    rejection = clan_battle_filter_reason(
                        info,
                        boss_names=boss_names[:5],
                        ng_terms=ng_terms,
                        content_month=content_month,
                    )
                    if rejection:
                        print(f"skip: Discord経由YouTube内容フィルタ ({rejection}): {title}")
                        continue
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
                raw_message = post_tracker.remove_discord_detection_note(
                    message.get("content") or ""
                )
                if is_youtube:
                    display_source_url = post_tracker.suppress_discord_embeds(source_url)
                    source_link = (
                        f"\n投稿元リンク: {post_tracker.suppress_discord_embeds(discord_post_url)}"
                        if discord_post_url else ""
                    )
                    fixed_body = (
                        f"{metadata}\n"
                        f"動画タイトル: {title}\n"
                        f"動画URL: {display_source_url}\n"
                        f"{post_tracker.markdown_note_line('Discordメッセージから検出')}"
                    )
                    body = _build_discord_post_body(
                        fixed_body, raw_message, source_link, formatted_tl
                    )
                else:
                    source_link = (
                        f"\n投稿元リンク: {discord_post_url}"
                        if discord_post_url else ""
                    )
                    body = _build_discord_post_body(
                        metadata, raw_message, source_link, formatted_tl
                    )
                body = post_tracker.add_post_separator(body)
                comparison = post_tracker.comparison_text(body)
                digest = hashlib.sha256(comparison.encode("utf-8")).hexdigest()
                old = tracking_by_url.get(tracking_key)
                previous = old[1][1] if old and len(old[1]) > 1 else ""
                row = [tracking_key, body, digest, scan_time, scan_time if old else "", previous, channel_id, str(message.get("id") or "")]
                status = _tracking_status(was_known, old, previous, comparison)
                if os.environ.get("PRICONNER_FORCE_POST") and old:
                    status = "updated"
                if (os.environ.get("PRICONNER_FORCE_NEW_POST")
                        or os.environ.get("PRICONNER_REPOST_NEW")) and old:
                    status = "new"
                if status == "same":
                    if not old:
                        pending_tracking_inserts.append(row)
                    elif len(old[1]) < 3 or old[1][2] != digest:
                        pending_tracking_updates.append((old[0], row))
                    continue
                if old and not os.environ.get("PRICONNER_FORCE_NEW_POST"):
                    pending_tracking_updates.append((old[0], row))
                elif not old:
                    pending_tracking_inserts.append(row)
                new_urls.append({
                    "url": source_url,
                    "tracking_key": tracking_key,
                    "is_youtube": is_youtube,
                    "source_boss": source_boss,
                    "text": body,
                    "title": video_title if is_youtube else "",
                    "status": status,
                    "previous_text": previous,
                })
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

    try:
        handed_off = youtube_handoff.enqueue(
            item["url"]
            for item in new_urls
            if item.get("is_youtube") and item.get("status") == "new"
        )
        if handed_off:
            print(f"YouTube URL引き渡し: {len(handed_off)}件")
    except Exception as error:
        # Handoff failure must not stop Discord collection or posting.
        print(f"警告: YouTube URL引き渡しに失敗: {error}")

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
            item["post_result"] = "未送信"
            continue
        try:
            _post_to_assigned_boss(
                post,
                post_tracker.post_content(item),
                item.get("source_boss"),
            )
            post_success += 1
            item["post_result"] = "成功"
        except Exception as error:
            item["post_result"] = "失敗"
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
        _notify_assigned_boss(notify, _build_post_summary(post_items))
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
