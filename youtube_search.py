import time
from datetime import datetime as DateTime
from datetime import timedelta, timezone

from yt_dlp import YoutubeDL

import datetime_utils as datetime
import discord_utils as discord
import gspread_utils as gspread
from runtime_utils import run_locked
from video_relevance import is_relevant_video
from new_arrivals_markdown import write_arrival

URL_YOUTUBE_CHANNEL = "https://www.youtube.com/channel/"
WAIT_TIME = 2
DEFAULT_PERIOD_DAYS = 7
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 50


def _as_utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=datetime.JST).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


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
        if timestamp is not None:
            self.publish_date = DateTime.fromtimestamp(timestamp, tz=timezone.utc)
        else:
            upload_date = info.get("upload_date")
            self.publish_date = (
                DateTime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
                if upload_date
                else None
            )


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


def _write_urls_with_retry(write_urls, urls, sleep, retries=2):
    for attempt in range(retries + 1):
        try:
            return write_urls(urls)
        except Exception as error:
            if attempt >= retries:
                raise
            print(f"URL登録を再試行します ({attempt + 1}/{retries}): {error}")
            sleep(2**attempt)


def search_youtube(query, now_factory=None):
    period_days = gspread.get_int_config_value(
        "youtube", "period_days", DEFAULT_PERIOD_DAYS, minimum=1
    )
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
    cutoff = now - timedelta(days=period_days)
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
        if video.publish_date is not None and video.publish_date >= cutoff:
            videos.append(video)
    return videos


def is_recent_video(video, now=None, period_days=None):
    """Return whether a video falls within the configured period."""
    now = now or DateTime.now(timezone.utc)
    if period_days is None:
        period_days = gspread.get_int_config_value(
            "youtube", "period_days", DEFAULT_PERIOD_DAYS, minimum=1
        )
    publish_date = video.publish_date
    if publish_date is None:
        return False
    publish_date = _as_utc(publish_date)
    now = _as_utc(now)
    return publish_date >= now - timedelta(days=period_days)


def findYouTubeVideo(
    spreadsheet=None,
    search_factory=search_youtube,
    channel_factory=YTDLPChannel,
    post=discord.post,
    notify=discord.notify,
    write_urls=None,
    sleep=time.sleep,
    wait_time=WAIT_TIME,
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
    videoUrls = sheetVideo.col_values(5)
    known_video_urls = set(videoUrls)
    channelIds = sheetChannel.col_values(2)
    period_days = gspread.get_int_config_value(
        "youtube", "period_days", DEFAULT_PERIOD_DAYS, minimum=1
    )
    channel_name_cache = {}
    channel_values = []
    video_values = []

    for bossName in bossNames:
        print(f"ボス名：{bossName}")
        keywords = f"{bossName} プリコネ"

        nowScanTime = datetime.nowString()
        search = search_factory(keywords)

        videos = search.videos if hasattr(search, "videos") else search
        for video in videos:
            if not is_recent_video(video, period_days=period_days):
                continue
            videoUrl = video.watch_url
            if videoUrl in known_video_urls:
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
            pending_posts.append((videoUrl, videoTitle, f"対象ボス: {bossName}"))

        sleep(wait_time)

    if channel_values:
        sheetChannel.insert_rows(channel_values, row=2)
    if video_values:
        sheetVideo.insert_rows(video_values, row=2)

    try:
        _write_urls_with_retry(write_urls, damage_urls, sleep)
    except Exception as error:
        print(f"失敗: YouTube URL登録: {error}")

    for videoUrl, videoTitle, notes in pending_posts:
        try:
            post(f"動画タイトル: {videoTitle}\n備考: {notes}\n動画URL: {videoUrl}")
        except Exception as error:
            print(f"失敗: YouTube URL通知 {videoUrl}: {error}")
        sleep(wait_time)

    if count > 0:
        try:
            notify(f"Youtube新着{count}件")
        except Exception as error:
            print(f"失敗: YouTube集計通知: {error}")

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
