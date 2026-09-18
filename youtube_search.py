import os
import time
from collections import Counter
from datetime import datetime as DateTime
from datetime import timezone

from yt_dlp import YoutubeDL

import datetime_utils as datetime
import discord_utils as discord
import gspread_utils as gspread
import post_change_tracker as post_tracker
from new_arrivals_markdown import write_arrival
from runtime_utils import run_locked
from tl_formatting import format_discord_tl
from video_relevance import is_relevant_video
from youtube_common import (
    as_utc,
    is_in_youtube_period,
    video_post_body,
    write_urls_with_retry,
)

URL_YOUTUBE_CHANNEL = "https://www.youtube.com/channel/"
WAIT_TIME = 2
DEFAULT_PERIOD_DAYS = 7
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 50


def selected_bosses(boss_names, selection=None):
    """Return the configured boss subset for an isolated worker."""
    if selection is None:
        selection = os.environ.get("PRICONNER_YOUTUBE_BOSS_INDEX", "").strip()
    if not selection:
        return list(enumerate(boss_names[:5], start=1))
    try:
        index = int(selection)
    except ValueError as error:
        raise ValueError("PRICONNER_YOUTUBE_BOSS_INDEX must be an integer from 1 to 5") from error
    if not 1 <= index <= min(5, len(boss_names)):
        raise ValueError("PRICONNER_YOUTUBE_BOSS_INDEX is outside the configured boss list")
    return [(index, boss_names[index - 1])]


def _post_to_channel(post, text, channel_key):
    if post is discord.post:
        return discord.post_to_configured_guilds(text, channel_key=channel_key)
    try:
        return post(text, channel_key=channel_key)
    except TypeError:
        # Keep compatibility with simple injected test callbacks.
        return post(text)


def _notify_to_channel(notify, text, channel_key):
    """Keep summary notifications in the same isolated boss channel."""
    if notify is discord.notify:
        return discord.notify_to_configured_guilds(text, channel_key=channel_key)
    try:
        return notify(text, channel_key=channel_key)
    except TypeError:
        # Keep compatibility with simple injected test callbacks.
        return notify(text)


def _format_youtube_tl(description):
    try:
        return format_discord_tl(description)
    except (RuntimeError, ValueError) as error:
        print(f"警告: YouTube概要欄のTL整形を利用できません: {error}")
        return ""


_as_utc = as_utc


class YTDLPVideo:
    def __init__(self, info):
        self._info = info
        video_id = info.get("id")
        if not video_id:
            raise ValueError("YouTube search result has no video id")
        self.watch_url = (
            info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
        )
        self.title = info.get("title", "")
        self.description = info.get("description", "")
        self.tags = info.get("tags", [])
        self.channel_id = info.get("channel_id", "")
        self.channel_url = info.get("channel_url") or (
            f"https://www.youtube.com/channel/{self.channel_id}"
            if self.channel_id
            else ""
        )
        timestamp = info.get("timestamp")
        upload_date = info.get("upload_date")
        if timestamp is not None:
            self.publish_date = DateTime.fromtimestamp(timestamp, tz=timezone.utc)
        elif upload_date:
            try:
                self.publish_date = DateTime.strptime(
                    upload_date, "%Y%m%d"
                ).replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                self.publish_date = None
        else:
            self.publish_date = None


class YTDLPChannel:
    def __init__(self, url):
        with YoutubeDL(
            {
                "quiet": True,
                "skip_download": True,
                "extract_flat": True,
                "remote_components": ["ejs:github"],
            }
        ) as ydl:
            info = ydl.extract_info(url, download=False)
        self.channel_name = info.get("channel") or info.get("uploader", "")
        self.channel_url = info.get("channel_url") or url


_write_urls_with_retry = write_urls_with_retry


def search_youtube(query, now_factory=None):
    period_days = gspread.get_int_config_value(
        "youtube", "period_days", DEFAULT_PERIOD_DAYS, minimum=1
    )
    period_mode = gspread.get_config_value("youtube", "period_mode", "days")
    period_month = gspread.get_config_value("youtube", "period_month", "")
    search_limit = gspread.get_int_config_value(
        "youtube", "search_limit", DEFAULT_SEARCH_LIMIT, minimum=1, maximum=MAX_SEARCH_LIMIT
    )
    search_limit = max(1, min(search_limit, MAX_SEARCH_LIMIT))
    options = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": False,
        "remote_components": ["ejs:github"],
        "playlistend": search_limit,
    }
    with YoutubeDL(options) as ydl:
        result = ydl.extract_info(f"ytsearch{search_limit}:{query}", download=False)
    now = _as_utc(now_factory() if now_factory else DateTime.now(timezone.utc))
    videos = []
    seen_urls = set()
    for entry in result.get("entries", []):
        if not entry:
            continue
        try:
            video = YTDLPVideo(entry)
        except (KeyError, TypeError, ValueError):
            continue
        if video.watch_url in seen_urls:
            continue
        seen_urls.add(video.watch_url)
        if is_in_youtube_period(
            video.publish_date, now, period_days, period_mode, period_month
        ):
            videos.append(video)
    return videos


def is_recent_video(
    video, now=None, period_days=None, period_mode=None, period_month=None
):
    """Return whether a video falls within the configured period."""
    now = now or DateTime.now(timezone.utc)
    explicit_period_days = period_days is not None
    if period_days is None:
        period_days = gspread.get_int_config_value(
            "youtube", "period_days", DEFAULT_PERIOD_DAYS, minimum=1
        )
    if period_mode is None:
        period_mode = (
            "days" if explicit_period_days
            else gspread.get_config_value("youtube", "period_mode", "days")
        )
    if period_month is None:
        period_month = gspread.get_config_value("youtube", "period_month", "")
    return is_in_youtube_period(
        video.publish_date, now, period_days, period_mode, period_month
    )


def findYouTubeVideo(
    spreadsheet=None,
    search_factory=search_youtube,
    channel_factory=YTDLPChannel,
    post=discord.post,
    notify=discord.notify,
    write_urls=None,
    sleep=time.sleep,
    wait_time=WAIT_TIME,
    now_factory=None,
):
    print("YouTube検索")

    if write_urls is None:
        write_urls = write_urls_to_youtube_sheet
    ss = spreadsheet or gspread.getNewArrivalsSheet()
    sheetChannel = ss.worksheet("YouTubeチャンネル")
    sheetChannelIgnores = [
        row[3] for row in sheetChannel.get_all_values() if len(row) > 6 and row[6]
    ]

    sheetVideo = ss.worksheet("YouTube動画")

    sheetBossNames = ss.worksheet("ボス名")
    rows = sheetBossNames.get_all_values()
    bossNames = []
    for i, row in enumerate(rows, start=1):
        if i <= 1:
            continue

        boss = row[0]
        if len(boss) == 0:
            continue
        bossNames.append(boss)

    count = 0
    damage_urls = []
    pending_posts = []
    post_failures = Counter()
    posted_count = 0
    new_count_by_channel = Counter()
    videoUrls = sheetVideo.col_values(5)
    known_video_urls = set(videoUrls)
    video_rows = {row[4]: (index, row) for index, row in enumerate(sheetVideo.get_all_values()[1:], start=2) if len(row) > 4 and row[4]}
    channelIds = sheetChannel.col_values(2)
    period_days = gspread.get_int_config_value(
        "youtube", "period_days", DEFAULT_PERIOD_DAYS, minimum=1
    )
    period_mode = gspread.get_config_value("youtube", "period_mode", "days")
    period_month = gspread.get_config_value("youtube", "period_month", "")
    now = _as_utc(
        now_factory() if now_factory else DateTime.now(timezone.utc)
    )
    channel_name_cache = {}
    channel_values = []
    video_values = []

    for boss_index, bossName in selected_bosses(bossNames):
        print(f"ボス名：{bossName}")
        keywords = f"{bossName} プリコネ"

        nowScanTime = datetime.dateTime2String(now)
        search = search_factory(keywords)

        videos = search.videos if hasattr(search, "videos") else search
        for video in videos:
            if not is_recent_video(
                video,
                now=now,
                period_days=period_days,
                period_mode=period_mode,
                period_month=period_month,
            ):
                continue
            videoUrl = video.watch_url
            description = getattr(video, "description", "")
            formatted_tl = _format_youtube_tl(description)
            if videoUrl in known_video_urls:
                old = video_rows.get(videoUrl)
                if os.environ.get("PRICONNER_FORCE_POST") and old:
                    pending_posts.append({"url": videoUrl, "title": video.title, "description": description, "formatted_tl": formatted_tl, "notes": f"確認用（更新・対象ボス: {bossName}）", "channel_key": f"boss{boss_index}_tl", "status": "updated", "force_full": True})
                if os.environ.get("PRICONNER_FORCE_NEW_POST") and old:
                    pending_posts.append({"url": videoUrl, "title": video.title, "description": description, "formatted_tl": formatted_tl, "notes": f"確認用（新規・対象ボス: {bossName}）", "channel_key": f"boss{boss_index}_tl", "status": "new"})
                if old and len(old[1]) > 3 and post_tracker.normalize_comparison_text(old[1][3]) != post_tracker.normalize_comparison_text(video.title):
                    notes = f"対象ボス: {bossName}（更新）"
                    previous = video_post_body(old[1][3], notes, videoUrl)
                    row = list(old[1]) + [""] * max(0, 6 - len(old[1]))
                    row[3], row[5] = video.title, previous
                    if hasattr(sheetVideo, "update"):
                        sheetVideo.update(f"A{old[0]}:F{old[0]}", [row[:6]], value_input_option="USER_ENTERED")
                    pending_posts.append({"url": videoUrl, "title": video.title, "description": description, "formatted_tl": formatted_tl, "notes": notes, "channel_key": f"boss{boss_index}_tl", "status": "updated", "previous_text": previous, "force_full": True})
                continue

            if not is_relevant_video(video, (bossName,)):
                print("skip: not a likely Princess Connect video")
                continue

            channelUrl = video.channel_url
            if not channelUrl or not video.channel_id:
                print("skip: channel information is unavailable")
                continue
            if channelUrl in sheetChannelIgnores:
                continue

            videoTitle = video.title
            publishDate = datetime.dateTime2String(video.publish_date)
            channelId = video.channel_id

            if channelUrl not in channel_name_cache:
                channel_name_cache[channelUrl] = channel_factory(
                    channelUrl
                ).channel_name
            channelName = channel_name_cache[channelUrl]

            if channelId not in channelIds:
                channel_values.append(
                    [
                        "",
                        channelId,
                        channelName,
                        channelUrl,
                        publishDate,
                        nowScanTime,
                    ]
                )
                print(channel_values[-1:])
                channelIds.append(channelId)

            video_values.append(
                [channelName, channelUrl, publishDate, videoTitle, videoUrl]
            )
            write_arrival("youtube-search", videoTitle, videoUrl, video.publish_date,
                          channel_name=channelName,
                          notes=f"対象ボス: {bossName}",
                          details={"channel_url": channelUrl})
            print(video_values[-1:])
            videoUrls.append(videoUrl)
            known_video_urls.add(videoUrl)

            count += 1
            damage_urls.append(videoUrl)
            pending_posts.append({"url": videoUrl, "title": videoTitle, "description": description, "formatted_tl": formatted_tl, "notes": f"対象ボス: {bossName}", "channel_key": f"boss{boss_index}_tl", "status": "new"})
            new_count_by_channel[f"boss{boss_index}_tl"] += 1

        sleep(wait_time)

    if channel_values:
        sheetChannel.insert_rows(channel_values, row=2)
    if video_values:
        sheetVideo.insert_rows(video_values, row=2)

    try:
        _write_urls_with_retry(write_urls, damage_urls, sleep)
    except Exception as error:
        print(f"失敗: YouTube URL登録: {error}")

    limit = 1 if os.environ.get("PRICONNER_FORCE_NEW_LIMIT_ONE") else int(os.environ.get("PRICONNER_FORCE_POST_LIMIT", "0") or 0)
    post_items = pending_posts[:limit] if limit else pending_posts
    for item in post_items:
        if os.environ.get("PRICONNER_NO_POST"):
            continue
        try:
            body = video_post_body(
                item['title'], item['notes'], item['url'],
                item.get('description', ''), item.get('formatted_tl', ''),
            )
            _post_to_channel(post, post_tracker.post_content({**item, "text": body}), item["channel_key"])
            posted_count += 1
        except Exception as error:
            print(f"失敗: YouTube URL通知 {item['url']}: {error}")
            post_failures[f"{type(error).__name__}: {error}"] += 1
        sleep(wait_time)

    print(
        f"YouTube検索投稿結果: 候補{len(post_items)}件 / 成功{posted_count}件 / "
        f"失敗{sum(post_failures.values())}件"
    )
    for reason, count in post_failures.items():
        print(f"失敗理由 ({count}件): {reason}")

    if count > 0:
        for channel_key, channel_count in new_count_by_channel.items():
            try:
                _notify_to_channel(notify, f"Youtube新着{channel_count}件", channel_key)
            except Exception as error:
                print(f"失敗: YouTube集計通知 ({channel_key}): {error}")

    if video_values:
        sheetVideo.sort((3, "des"), range="A2:Z10000")
        gspread.deleteEmptyRows(sheetVideo)
    if channel_values:
        sheetChannel.sort((5, "des"), range="A2:Z10000")
        gspread.deleteEmptyRows(sheetChannel)


def write_urls_to_youtube_sheet(urls):
    """
    url_list: ["https://youtu.be/...", ...] を想定
    A列のうち「空セル」の行に順番に書き込む。
    1行目はヘッダ想定なので 2 行目以降を対象。
    """
    if isinstance(urls, str):
        urls = [urls]
    urls = list(dict.fromkeys(url for url in urls if url))
    if not urls:
        return

    # スプレッドシート取得
    ss = gspread.getDamagesSheet()
    sheet = ss.worksheet("Youtube")

    existing_urls = set(sheet.col_values(1))
    urls = [url for url in urls if url not in existing_urls]
    if not urls:
        return

    gspread.writeToFirstEmptyCells(sheet, urls, wait_time=WAIT_TIME)


def main():
    def run():
        print("-----------------------------------------------")
        print(f"開始{datetime.nowString()}")
        print("-----------------------------------------------")
        findYouTubeVideo()
        print("-----------------------------------------------")
        print(f"終了{datetime.nowString()}")
        print("-----------------------------------------------")

    return run_locked(run, lock_name="youtube_search.lock")


if __name__ == "__main__":
    main()
