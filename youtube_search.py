import time
from datetime import datetime as DateTime, timedelta, timezone

from yt_dlp import YoutubeDL

import datetime_utils as datetime
import discord_utils as discord
import gspread_utils as gspread

URL_YOUTUBE_CHANNEL = "https://www.youtube.com/channel/"
WAIT_TIME = 2
DEFAULT_PERIOD_DAYS = 7
DEFAULT_SEARCH_LIMIT = 100


class YTDLPVideo:
    def __init__(self, info):
        self._info = info
        self.watch_url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}"
        self.channel_url = info.get("channel_url") or f"https://www.youtube.com/channel/{info['channel_id']}"
        self.title = info.get("title", "")
        self.channel_id = info.get("channel_id", "")
        upload_date = info.get("upload_date")
        self.publish_date = (
            DateTime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
            if upload_date
            else None
        )


class YTDLPChannel:
    def __init__(self, url):
        with YoutubeDL({"quiet": True, "skip_download": True, "extract_flat": True, "remote_components": ["ejs:github"]}) as ydl:
            info = ydl.extract_info(url, download=False)
        self.channel_name = info.get("channel") or info.get("uploader", "")
        self.channel_url = info.get("channel_url") or url


def search_youtube(query):
    period_days = int(
        gspread.get_config_value("youtube", "period_days", DEFAULT_PERIOD_DAYS)
    )
    search_limit = int(
        gspread.get_config_value("youtube", "search_limit", DEFAULT_SEARCH_LIMIT)
    )
    options = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": False,
        "remote_components": ["ejs:github"],
        "playlistend": search_limit,
    }
    with YoutubeDL(options) as ydl:
        result = ydl.extract_info(f"ytsearch{search_limit}:{query}", download=False)
    cutoff = DateTime.now(timezone.utc) - timedelta(days=period_days)
    return [
        video
        for video in (YTDLPVideo(entry) for entry in result.get("entries", []) if entry)
        if video.publish_date is not None and video.publish_date >= cutoff
    ]


def is_recent_video(video, now=None, period_days=None):
    """Return whether a video falls within the configured period."""
    now = now or DateTime.now(timezone.utc)
    if period_days is None:
        period_days = int(
            gspread.get_config_value("youtube", "period_days", DEFAULT_PERIOD_DAYS)
        )
    publish_date = video.publish_date
    if publish_date is None:
        return False
    if publish_date.tzinfo is None:
        publish_date = publish_date.replace(tzinfo=timezone.utc)
    return publish_date >= now - timedelta(days=7)


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
    sheetChannelIgnores = list(
        map(
            lambda x: x[3],
            filter(lambda x: len(x[6]) > 0, sheetChannel.get_all_values()),
        )
    )

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
    videoUrls = sheetVideo.col_values(5)
    channelIds = sheetChannel.col_values(2)
    channel_name_cache = {}

    for bossName in bossNames:
        print(f"ボス名：{bossName}")
        keywords = f"{bossName} プリコネ"

        nowScanTime = datetime.nowString()
        search = search_factory(keywords)

        videos = search.videos if hasattr(search, "videos") else search
        for video in videos:
            if not is_recent_video(video, period_days=int(
                gspread.get_config_value("youtube", "period_days", DEFAULT_PERIOD_DAYS)
            )):
                continue
            videoUrl = video.watch_url
            if videoUrl in videoUrls:
                continue

            channelUrl = video.channel_url
            if channelUrl in sheetChannelIgnores:
                continue

            videoTitle = video.title
            publishDate = datetime.dateTime2String(video.publish_date)
            channelId = video.channel_id

            if channelUrl not in channel_name_cache:
                channel_name_cache[channelUrl] = channel_factory(channelUrl).channel_name
            channelName = channel_name_cache[channelUrl]

            if channelId not in channelIds:
                channelValues = [
                    [
                        "",
                        channelId,
                        channelName,
                        channelUrl,
                        publishDate,
                        nowScanTime,
                    ]
                ]
                print(channelValues)
                sheetChannel.insert_rows(channelValues, row=2)
                channelIds.append(channelId)
                sleep(wait_time)

            videoValues = [[channelName, channelUrl, publishDate, videoTitle, videoUrl]]
            print(videoValues)
            sheetVideo.insert_rows(videoValues, row=2)
            videoUrls.append(videoUrl)

            count += 1
            damage_urls.append(videoUrl)
            post(videoUrl)
            sleep(wait_time)

        sleep(wait_time)

    write_urls(damage_urls)

    if count > 0:
        notify(f"Youtube新着{count}件")

    sheetVideo.sort((3, "des"), range="A2:Z10000")
    gspread.deleteEmptyRows(sheetVideo)
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
    if not urls:
        return

    # スプレッドシート取得
    ss = gspread.getDamagesSheet()
    sheet = ss.worksheet("Youtube")

    gspread.writeToFirstEmptyCells(sheet, urls, wait_time=WAIT_TIME)


def main():
    print("-----------------------------------------------")
    print(f"開始{datetime.nowString()}")
    print("-----------------------------------------------")
    findYouTubeVideo()
    print("-----------------------------------------------")
    print(f"終了{datetime.nowString()}")
    print("-----------------------------------------------")


if __name__ == "__main__":
    main()
