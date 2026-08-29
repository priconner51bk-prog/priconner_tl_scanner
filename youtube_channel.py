import time
from datetime import datetime as DateTime
from datetime import timezone

from yt_dlp import YoutubeDL

import datetime_utils as datetime
import discord_utils as discord
import gspread_utils as gspread
from runtime_utils import run_locked

URL_YOUTUBE_CHANNEL = "https://www.youtube.com/channel/"
# Network calls are already rate limited by YouTube/Discord.  A four second
# delay per channel made a normal scan take several minutes.
WAIT_TIME = 0
DEFAULT_PERIOD_DAYS = 7
# A bounded page keeps scans predictable, while flat extraction avoids
# detailed video requests for every listed entry.
DEFAULT_CHANNEL_LIMIT = 20


class YTDLPVideo:
    def __init__(self, info):
        if not info or not info.get("id"):
            raise ValueError("YouTube channel entry has no video id")
        self.video_id = info["id"]
        self.watch_url = (
            info.get("webpage_url")
            or f"https://www.youtube.com/watch?v={self.video_id}"
        )
        self.title = info.get("title", "")
        self.description = info.get("description", "")
        self.tags = info.get("tags", [])
        timestamp = info.get("timestamp")
        upload_date = info.get("upload_date")
        if timestamp is not None:
            self.publish_date = DateTime.fromtimestamp(timestamp, tz=timezone.utc)
        elif upload_date:
            try:
                self.publish_date = DateTime.strptime(upload_date, "%Y%m%d").replace(
                    tzinfo=timezone.utc
                )
            except (TypeError, ValueError):
                self.publish_date = None
        else:
            self.publish_date = None
        self.channel_id = info.get("channel_id", "")
        self.channel_url = info.get("channel_url", "")


class YTDLPChannel:
    def __init__(self, url, playlist_start=1):
        # The channel landing page is not guaranteed to be the uploads feed.
        # Explicitly selecting /videos also avoids Shorts/live/playlists being
        # mixed into the first page on some channel layouts.
        videos_url = url.rstrip("/") + "/videos"
        playlist_end = playlist_start + DEFAULT_CHANNEL_LIMIT - 1
        options = {
            "quiet": True,
            "skip_download": True,
            # Listing entries is substantially faster and is enough here: a
            # managed channel is itself the user's inclusion decision.
            "extract_flat": True,
            "ignoreerrors": True,
            "remote_components": ["ejs:github"],
            # playlist_items is intentional in addition to start/end.  Some
            # YouTube channel extractors fetch a larger continuation page and
            # only apply playliststart/playlistend after extraction.  The
            # explicit selector keeps the work bounded to exactly one page.
            "playlist_items": f"{playlist_start}-{playlist_end}",
            "playlistend": playlist_end,
            "playliststart": playlist_start,
        }
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(videos_url, download=False) or {}
        self.channel_name = info.get("channel") or info.get("uploader", "")
        self.channel_url = info.get("channel_url") or url
        entries = info.get("entries", [])
        self.entry_count = len(entries)
        self.videos = []
        for entry in entries:
            try:
                self.videos.append(YTDLPVideo(entry))
            except (TypeError, ValueError):
                # A deleted/private video can appear as a null entry. It must
                # not prevent the rest of the channel from being scanned.
                continue


def updateYouTubeChannelIdList():
    print("YouTubeチャンネルID情報の更新")

    ss = gspread.getNewArrivalsSheet()
    sheetChannel = ss.worksheet("YouTubeチャンネル")
    rows = sheetChannel.get_all_values()
    updated = False
    for i, row in enumerate(rows, start=1):
        if i <= 1:
            continue

        channelName = row[2]
        if len(channelName) > 0:
            continue

        channelId = row[1]
        if len(channelId) == 0:
            videoUrl = row[0]
            if len(videoUrl) == 0:
                continue
            with YoutubeDL(
                {
                    "quiet": True,
                    "skip_download": True,
                    "extract_flat": True,
                    "remote_components": ["ejs:github"],
                }
            ) as ydl:
                video_info = ydl.extract_info(videoUrl, download=False)
            channelId = video_info.get("channel_id", "")
            if not channelId:
                continue

        url = f"{URL_YOUTUBE_CHANNEL}{channelId}"
        ch = YTDLPChannel(url)
        channelName = ch.channel_name
        channelUrl = ch.channel_url

        values = [[channelId, channelName, channelUrl]]
        sheetChannel.update(
            values,
            sheetChannel.cell(i, 2).address,
            value_input_option="USER_ENTERED",
        )
        updated = True

        time.sleep(WAIT_TIME)

    if updated:
        sheetChannel.sort((5, "des"), range="A2:Z10000")
        gspread.deleteEmptyRows(sheetChannel)


def checkNewArrivalsForYouTube(
    spreadsheet=None,
    channel_factory=YTDLPChannel,
    post=discord.post,
    notify=discord.notify,
    write_urls=None,
    sleep=time.sleep,
    wait_time=WAIT_TIME,
    now_factory=datetime.now,
):
    print("新着チェック対象:YouTube")

    if write_urls is None:
        write_urls = write_urls_to_youtube_sheet
    ss = spreadsheet or gspread.getNewArrivalsSheet()
    sheetVideo = ss.worksheet("YouTube動画")
    count = 0
    damage_urls = []

    sheetChannel = ss.worksheet("YouTubeチャンネル")
    videoUrls = sheetVideo.col_values(5)
    known_video_urls = set(videoUrls)
    rows = sheetChannel.get_all_values()
    period_days = int(
        gspread.get_config_value("youtube", "period_days", DEFAULT_PERIOD_DAYS)
    )
    now = now_factory()
    lasted_scan_time = datetime.calcDate(now, period_days)
    for i, row in enumerate(rows, start=1):
        if i <= 1:
            continue

        channelId = row[1]
        if len(channelId) == 0:
            continue
        ignore = row[6] if len(row) > 6 else ""
        if len(ignore) > 0:
            continue

        channelName = row[2]
        print(f"YouTubeチャンネル名「{channelName}」")
        channelUrl = row[3]
        publishDateString = row[4] if len(row) > 4 else ""
        nowScanTime = datetime.dateTime2String(now)

        videoValues = []
        channelUrl = f"{URL_YOUTUBE_CHANNEL}{channelId}"
        print(f"channelUrl:{channelUrl}")
        ch = channel_factory(channelUrl, playlist_start=1)

        for yt in ch.videos:
            try:
                videoUrl = yt.watch_url
            except Exception as e:
                print(f"skip: invalid video object error={e}")
                continue

            print(f"videoUrl:{videoUrl}")

            if videoUrl in known_video_urls:
                # Entries are newest first. Once a known entry is reached,
                # older entries cannot produce a new result.
                break

            publishDate = yt.publish_date
            if publishDate is None:
                # Flat playlist entries occasionally omit upload_date. Since
                # this is a managed channel and the entry is before the first
                # known video, keeping it is safer than silently losing a new
                # upload. The next run deduplicates it by URL.
                print("upload date unavailable; keeping entry")
                publishDate = now

            if publishDate <= lasted_scan_time:
                break

            if len(publishDateString) == 0 or publishDate > datetime.string2DateTime(
                publishDateString
            ):
                publishDateString = datetime.dateTime2String(publishDate)

            values = [
                channelName,
                channelUrl,
                datetime.dateTime2String(publishDate),
                yt.title,
                videoUrl,
            ]
            print(f"YouTube動画タイトル「{yt.title}」")
            videoValues.append(values)
            videoUrls.append(videoUrl)
            known_video_urls.add(videoUrl)

            count += 1
            damage_urls.append(videoUrl)
            post(videoUrl)
            sleep(wait_time)

        if len(videoValues) > 0:
            sheetVideo.insert_rows(videoValues, row=2)

        channelValues = [[publishDateString, nowScanTime]]
        sheetChannel.update(
            channelValues,
            sheetChannel.cell(i, 5).address,
            value_input_option="USER_ENTERED",
        )

        sleep(wait_time)

    write_urls(damage_urls)

    if count > 0:
        notify(f"Youtube新着{count}件")

    if count > 0:
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
    urls = list(dict.fromkeys(url for url in urls if url))
    if not urls:
        return

    # スプレッドシート取得
    ss = gspread.getDamagesSheet()
    sheet = ss.worksheet("Youtube")

    gspread.writeToFirstEmptyCells(sheet, urls, wait_time=WAIT_TIME)


def main():
    def run():
        print("-----------------------------------------------")
        print(f"開始{datetime.nowString()}")
        print("-----------------------------------------------")
        updateYouTubeChannelIdList()
        checkNewArrivalsForYouTube()
        print("-----------------------------------------------")
        print(f"終了{datetime.nowString()}")
        print("-----------------------------------------------")

    return run_locked(run, lock_name="youtube_channel.lock")


if __name__ == "__main__":
    main()
