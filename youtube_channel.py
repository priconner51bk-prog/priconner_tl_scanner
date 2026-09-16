import time
import os
from datetime import datetime as DateTime
from datetime import timezone

from yt_dlp import YoutubeDL

import datetime_utils as datetime
import discord_utils as discord
import post_change_tracker as post_tracker
import gspread_utils as gspread
from youtube_common import as_utc, write_urls_with_retry, video_post_body, VIDEO_HEADERS
from new_arrivals_markdown import write_arrival
from runtime_utils import run_locked

URL_YOUTUBE_CHANNEL = "https://www.youtube.com/channel/"
# Network calls are already rate limited by YouTube/Discord.  A four second
# delay per channel made a normal scan take several minutes.
WAIT_TIME = 0
DEFAULT_PERIOD_DAYS = 7
# A bounded page keeps scans predictable, while flat extraction avoids
# detailed video requests for every listed entry.
DEFAULT_CHANNEL_LIMIT = 20


_as_utc = as_utc


_write_urls_with_retry = write_urls_with_retry


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
        entries = list(info.get("entries") or [])
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
    pending_posts = []

    sheetChannel = ss.worksheet("YouTubeチャンネル")
    videoUrls = sheetVideo.col_values(5)
    known_video_urls = set(videoUrls)
    video_rows = {row[4]: (index, row) for index, row in enumerate(sheetVideo.get_all_values()[1:], start=2) if len(row) > 4 and row[4]}
    rows = sheetChannel.get_all_values()
    period_days = gspread.get_int_config_value(
        "youtube", "period_days", DEFAULT_PERIOD_DAYS, minimum=1
    )
    now = _as_utc(now_factory())
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
        page_seen = set()
        page_start = 1
        while True:
            ch = channel_factory(channelUrl, playlist_start=page_start)
            page_videos = list(getattr(ch, "videos", []) or [])
            entry_count = getattr(ch, "entry_count", len(page_videos))
            try:
                entry_count = int(entry_count)
            except (TypeError, ValueError):
                entry_count = len(page_videos)
            page_urls = tuple(
                getattr(video, "watch_url", None) for video in page_videos
            )
            if page_urls and page_urls in page_seen:
                print("skip: repeated YouTube channel page")
                break
            if page_urls:
                page_seen.add(page_urls)

            if not page_videos:
                break

            stop_channel = False
            for yt in page_videos:
                try:
                    videoUrl = yt.watch_url
                except Exception as e:
                    print(f"skip: invalid video object error={e}")
                    continue

                print(f"videoUrl:{videoUrl}")

                if videoUrl in known_video_urls:
                    old = video_rows.get(videoUrl)
                    if os.environ.get("PRICONNER_FORCE_POST") and old:
                        pending_posts.append({"url": videoUrl, "title": yt.title, "notes": "登録チャンネルの確認用（更新）", "status": "updated", "previous_text": video_post_body(old[1][3] if len(old[1]) > 3 else "", "登録チャンネルの新着動画", videoUrl)})
                    if os.environ.get("PRICONNER_FORCE_NEW_POST") and old:
                        pending_posts.append({"url": videoUrl, "title": yt.title, "notes": "登録チャンネルの確認用（新規）", "status": "new"})
                    if old and len(old[1]) > 3 and old[1][3] != yt.title:
                        previous = video_post_body(old[1][3], "登録チャンネルの新着動画", videoUrl)
                        row = list(old[1]) + [""] * max(0, 6 - len(old[1]))
                        row[3], row[5] = yt.title, previous
                        if hasattr(sheetVideo, "update"):
                            sheetVideo.update(f"A{old[0]}:F{old[0]}", [row[:6]], value_input_option="USER_ENTERED")
                        pending_posts.append({"url": videoUrl, "title": yt.title, "notes": "登録チャンネルの動画更新", "status": "updated", "previous_text": previous})
                    # Entries are newest first. Once a known entry is reached,
                    # older entries cannot produce a new result.
                    stop_channel = True
                    break

                publishDate = yt.publish_date
                if publishDate is None:
                    # Flat playlist entries occasionally omit upload_date. Since
                    # this is a managed channel and the entry is before the first
                    # known video, keeping it is safer than silently losing a new
                    # upload. The next run deduplicates it by URL.
                    print("upload date unavailable; keeping entry")
                    publishDate = now
                else:
                    publishDate = _as_utc(publishDate)

                if publishDate <= lasted_scan_time:
                    stop_channel = True
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
                write_arrival("youtube-channel", yt.title, videoUrl, yt.publish_date,
                              channel_name=channelName,
                              details={"channel_url": channelUrl})
                videoUrls.append(videoUrl)
                known_video_urls.add(videoUrl)

                count += 1
                damage_urls.append(videoUrl)
                pending_posts.append({"url": videoUrl, "title": yt.title, "notes": "登録チャンネルの新着動画", "status": "new"})

            if stop_channel or entry_count < DEFAULT_CHANNEL_LIMIT:
                break
            page_start += DEFAULT_CHANNEL_LIMIT

        if len(videoValues) > 0:
            sheetVideo.insert_rows(videoValues, row=2)

            # Defer notifications until both the arrival rows and damage URLs
            # have had a chance to become durable.

        channelValues = [[publishDateString, nowScanTime]]
        try:
            sheetChannel.update(
                channelValues,
                sheetChannel.cell(i, 5).address,
                value_input_option="USER_ENTERED",
            )
        except Exception as error:
            # The arrival rows are already durable; a metadata update should
            # not suppress the pending URL registration and notifications.
            print(f"失敗: YouTubeチャンネル状態更新: {error}")

        sleep(wait_time)

    try:
        _write_urls_with_retry(write_urls, damage_urls, sleep)
    except Exception as error:
        print(f"失敗: YouTube URL登録: {error}")

    post_items = pending_posts[:1] if os.environ.get("PRICONNER_FORCE_NEW_LIMIT_ONE") else pending_posts
    for item in post_items:
        if os.environ.get("PRICONNER_NO_POST"):
            continue
        try:
            body = video_post_body(item['title'], item['notes'], item['url'])
            post(post_tracker.post_content({**item, "text": body}))
        except Exception as error:
            print(f"失敗: YouTube URL通知 {item['url']}: {error}")
        sleep(wait_time)

    if count > 0:
        try:
            notify(f"Youtube新着{count}件")
        except Exception as error:
            print(f"失敗: YouTube集計通知: {error}")

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

    # A partially completed prior write must not create a duplicate URL when
    # this operation is retried.
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
        updateYouTubeChannelIdList()
        checkNewArrivalsForYouTube()
        print("-----------------------------------------------")
        print(f"終了{datetime.nowString()}")
        print("-----------------------------------------------")

    return run_locked(run, lock_name="youtube_channel.lock")


if __name__ == "__main__":
    main()
