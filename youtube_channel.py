import time
import json
import os
import tempfile
from datetime import datetime as DateTime
from datetime import timezone
from pathlib import Path

from yt_dlp import YoutubeDL

import datetime_utils as datetime
import discord_utils as discord
import gspread_utils as gspread
from runtime_utils import default_runtime_dir, run_locked
from video_relevance import is_relevant_video

URL_YOUTUBE_CHANNEL = "https://www.youtube.com/channel/"
WAIT_TIME = 4
DEFAULT_PERIOD_DAYS = 7
DEFAULT_CHANNEL_LIMIT = 20


class YTDLPVideo:
    def __init__(self, info):
        self.watch_url = (
            info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}"
        )
        self.title = info.get("title", "")
        self.description = info.get("description", "")
        self.tags = info.get("tags", [])
        timestamp = info.get("timestamp")
        upload_date = info.get("upload_date")
        if timestamp is not None:
            self.publish_date = DateTime.fromtimestamp(timestamp, tz=timezone.utc)
        else:
            self.publish_date = (
                DateTime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
                if upload_date
                else None
            )
        self.channel_id = info.get("channel_id", "")


class YTDLPChannel:
    def __init__(self, url, playlist_start=1):
        playlist_end = playlist_start + DEFAULT_CHANNEL_LIMIT - 1
        options = {
            "quiet": True,
            "skip_download": True,
            # A channel URL is a playlist.  Resolving every video in the
            # playlist here makes yt-dlp perform a full video extraction for
            # each item before the caller can apply its date/duplicate
            # checks.  The channel scan only needs the playlist entries; the
            # title and date are already present in flat entries on YouTube.
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
            info = ydl.extract_info(url, download=False)
        self.channel_name = info.get("channel") or info.get("uploader", "")
        self.channel_url = info.get("channel_url") or url
        entries = info.get("entries", [])
        self.entry_count = len(entries)
        self.videos = [
            YTDLPVideo(entry) for entry in entries if entry and entry.get("id")
        ]


def _cursor_path():
    runtime_dir = os.environ.get("PRICONNER_MONITOR_RUNTIME_DIR")
    return (
        Path(runtime_dir) / "youtube-channel-cursors.json"
        if runtime_dir
        else default_runtime_dir() / "youtube-channel-cursors.json"
    )


def _load_cursors():
    path = _cursor_path()
    try:
        with path.open(encoding="utf-8") as cursor_file:
            cursors = json.load(cursor_file)
        return cursors if isinstance(cursors, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_cursors(cursors):
    path = _cursor_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as cursor_file:
            json.dump(cursors, cursor_file, ensure_ascii=False, sort_keys=True)
            cursor_file.write("\n")
            cursor_file.flush()
            os.fsync(cursor_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


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
    boss_names = [
        row[0] for row in ss.worksheet("ボス名").get_all_values()[1:] if row and row[0]
    ]

    count = 0
    damage_urls = []

    sheetChannel = ss.worksheet("YouTubeチャンネル")
    videoUrls = sheetVideo.col_values(5)
    known_video_urls = set(videoUrls)
    cursors = _load_cursors()
    rows = sheetChannel.get_all_values()
    for i, row in enumerate(rows, start=1):
        if i <= 1:
            continue

        channelId = row[1]
        if len(channelId) == 0:
            continue
        ignore = row[6]
        if len(ignore) > 0:
            continue

        channelName = row[2]
        print(f"YouTubeチャンネル名「{channelName}」")
        channelUrl = row[3]
        publishDateString = row[4]
        dateTimeStr = row[5]
        now = now_factory()
        nowScanTime = datetime.dateTime2String(now)
        period_days = int(
            gspread.get_config_value("youtube", "period_days", DEFAULT_PERIOD_DAYS)
        )
        lastedScanTime = datetime.calcDate(now, period_days)
        if len(dateTimeStr) > 0:
            lastedScanTime = datetime.string2DateTime(dateTimeStr)

        videoValues = []
        channelUrl = f"{URL_YOUTUBE_CHANNEL}{channelId}"
        print(f"channelUrl:{channelUrl}")
        playlist_start = max(1, int(cursors.get(channelId, 1)))
        if playlist_start > 1:
            # Continuation pages may contain videos published before the last
            # scan started; the configured lookback is the relevant boundary.
            lastedScanTime = datetime.calcDate(now, period_days)
        ch = channel_factory(channelUrl, playlist_start=playlist_start)
        reached_scan_boundary = False

        for yt in ch.videos:
            try:
                videoUrl = yt.watch_url
            except Exception as e:
                print(f"skip: invalid video object error={e}")
                continue

            print(f"videoUrl:{videoUrl}")

            if not is_relevant_video(yt, boss_names):
                print("skip: not a likely Princess Connect video")
                continue

            if videoUrl in known_video_urls:
                continue

            publishDate = yt.publish_date
            if publishDate is None:
                print("skip: upload date is unavailable")
                continue

            if publishDate <= lastedScanTime:
                reached_scan_boundary = True
                break

            if len(publishDateString) == 0 or publishDate > datetime.string2DateTime(
                publishDateString
            ):
                publishDateString = datetime.dateTime2String(publishDate)

            values = [
                channelName,
                channelUrl,
                publishDateString,
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

        if reached_scan_boundary or ch.entry_count < DEFAULT_CHANNEL_LIMIT:
            cursors.pop(channelId, None)
        else:
            cursors[channelId] = playlist_start + DEFAULT_CHANNEL_LIMIT

        if len(videoValues) > 0:
            sheetVideo.insert_rows(videoValues, row=2)

        channelValues = [[publishDateString, nowScanTime]]
        sheetChannel.update(
            channelValues,
            sheetChannel.cell(i, 5).address,
            value_input_option="USER_ENTERED",
        )

        sleep(wait_time)

    _save_cursors(cursors)

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
