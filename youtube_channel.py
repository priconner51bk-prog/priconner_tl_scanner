import os
import time
from collections import Counter
from datetime import datetime as DateTime
from datetime import timezone
from itertools import islice

from yt_dlp import YoutubeDL

try:
    from pytubefix import Channel as PytubeFixChannel
except ImportError:
    PytubeFixChannel = None

import datetime_utils as datetime
import discord_utils as discord
import gspread_utils as gspread
import post_change_tracker as post_tracker
from new_arrivals_markdown import write_arrival
from runtime_utils import run_locked
from tl_formatting import format_discord_tl
from youtube_common import (
    as_utc,
    is_in_youtube_period,
    video_post_body,
    write_urls_with_retry,
    youtube_period_bounds,
)
from youtube_rss import RSSChannel

URL_YOUTUBE_CHANNEL = "https://www.youtube.com/channel/"
# Keep both channel requests and Discord posts serialized.  Discord also has
# its own API interval/429 retry guard, but this delay protects injected or
# alternate post implementations used by the scheduled runner.
WAIT_TIME = 2
DEFAULT_PERIOD_DAYS = 7
# A bounded page keeps scans predictable while retaining upload dates needed
# for the current-month safety boundary.
DEFAULT_CHANNEL_LIMIT = 20
YOUTUBE_SOCKET_TIMEOUT = 15
YOUTUBE_RETRIES = 1
USE_RSS_DISCOVERY = os.environ.get("PRICONNER_YOUTUBE_RSS", "1").strip().lower() not in {"0", "false", "no"}


_as_utc = as_utc


_write_urls_with_retry = write_urls_with_retry


def _format_youtube_tl(description):
    try:
        return format_discord_tl(description)
    except (RuntimeError, ValueError) as error:
        print(f"警告: YouTube概要欄のTL整形を利用できません: {error}")
        return ""


def _post_configured(post, text):
    if post is discord.post:
        return discord.post_to_configured_guilds(text)
    return post(text)


def _notify_configured(notify, text):
    if notify is discord.notify:
        return discord.notify_to_configured_guilds(text)
    return notify(text)


def _rss_covers_period(channel_id, videos, period_start):
    """RSS is complete only when its oldest dated item crosses the boundary."""
    dates = [_as_utc(getattr(video, "publish_date", None)) for video in videos]
    dates = [value for value in dates if value is not None]
    return bool(dates) and min(dates) < period_start


def _rss_channel_for_period(channel_id, period_start):
    """Return an RSS channel only when its retained window is sufficient."""
    try:
        channel = RSSChannel(channel_id)
    except Exception as error:
        print(f"RSS取得失敗、yt-dlpへフォールバック: {type(error).__name__}: {error}")
        return None
    if _rss_covers_period(channel_id, channel.videos, period_start):
        print(f"RSS採用: {channel.channel_name} ({len(channel.videos)}件)")
        return channel
    print("RSS保持件数では期間境界に届かないため、yt-dlpへフォールバック")
    return None


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
            # Detailed entries are required for upload_date.  Flat entries
            # frequently omit it, which makes a current-month boundary
            # impossible to enforce safely.
            "extract_flat": False,
            "ignoreerrors": True,
            "socket_timeout": YOUTUBE_SOCKET_TIMEOUT,
            "retries": YOUTUBE_RETRIES,
            "fragment_retries": YOUTUBE_RETRIES,
            "extractor_retries": YOUTUBE_RETRIES,
            "remote_components": ["ejs:github"],
            # playlist_items is intentional in addition to start/end.  Some
            # YouTube channel extractors fetch a larger continuation page and
            # only apply playliststart/playlistend after extraction.  The
            # explicit selector keeps the work bounded to exactly one page.
            "playlist_items": f"{playlist_start}-{playlist_end}",
            "playlistend": playlist_end,
            "playliststart": playlist_start,
        }
        info = None
        if PytubeFixChannel is not None:
            try:
                channel = PytubeFixChannel(url, client="WEB")
                entries = []
                video_iter = iter(channel.videos or [])
                for video in islice(video_iter, playlist_start - 1, playlist_end):
                    entries.append({
                        "id": video.video_id,
                        "webpage_url": video.watch_url,
                        "title": video.title,
                        "description": video.description,
                        "channel": channel.channel_name,
                        "uploader": channel.channel_name,
                        "channel_id": channel.channel_id,
                        "channel_url": url,
                        "timestamp": (
                            video.publish_date.timestamp()
                            if video.publish_date else None
                        ),
                    })
                info = {
                    "channel": channel.channel_name,
                    "channel_url": url,
                    "entries": entries,
                }
            except Exception as error:
                print(
                    f"pytubefixチャンネル取得失敗、yt-dlpへフォールバック: "
                    f"{type(error).__name__}: {error}"
                )
        if info is None:
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
    if (os.environ.get("PRICONNER_YOUTUBE_BOSS_INDEX", "").strip()
            and not os.environ.get("PRICONNER_ALLOW_SHARED_CHANNEL_SCAN")):
        print(
            "YouTubeチャンネル監視をスキップ: "
            "ボス限定試験中は共通投稿先を混在させません"
        )
        return
    print("新着チェック対象:YouTube")

    if write_urls is None:
        write_urls = write_urls_to_youtube_sheet
    ss = spreadsheet or gspread.getNewArrivalsSheet()
    sheetVideo = ss.worksheet("YouTube動画")
    count = 0
    damage_urls = []
    pending_posts = []
    post_failures = Counter()
    posted_count = 0

    sheetChannel = ss.worksheet("YouTubeチャンネル")
    video_sheet_rows = sheetVideo.get_all_values()
    videoUrls = [row[4] for row in video_sheet_rows if len(row) > 4]
    known_video_urls = set(videoUrls)
    video_rows = {row[4]: (index, row) for index, row in enumerate(video_sheet_rows[1:], start=2) if len(row) > 4 and row[4]}
    rows = sheetChannel.get_all_values()
    period_days = gspread.get_int_config_value(
        "youtube", "period_days", DEFAULT_PERIOD_DAYS, minimum=1
    )
    period_mode = gspread.get_config_value("youtube", "period_mode", "days")
    period_month = gspread.get_config_value("youtube", "period_month", "")
    inactive_days = gspread.get_int_config_value(
        "maintenance", "inactive_days", 60, minimum=1
    )
    now = _as_utc(now_factory())
    period_start, period_end = youtube_period_bounds(
        now, period_days, period_mode, period_month
    )
    channel_updates = []
    for i, row in enumerate(rows, start=1):
        if i <= 1:
            continue

        channelId = row[1]
        if len(channelId) == 0:
            continue
        ignore = row[6] if len(row) > 6 else ""
        if len(ignore) > 0:
            continue

        latest_video = row[4] if len(row) > 4 else ""
        if latest_video:
            try:
                latest_video_at = _as_utc(datetime.string2DateTime(latest_video))
            except (TypeError, ValueError):
                latest_video_at = None
            if latest_video_at and (now - latest_video_at).total_seconds() > inactive_days * 86400:
                print(f"skip: inactive registered channel ({inactive_days} days since latest video): {row[2]}")
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
            if (page_start == 1 and USE_RSS_DISCOVERY
                    and channel_factory is YTDLPChannel):
                rss_channel = _rss_channel_for_period(channelId, period_start)
                ch = rss_channel or channel_factory(channelUrl, playlist_start=page_start)
            else:
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
            page_unknown_date = False
            for yt in page_videos:
                try:
                    videoUrl = yt.watch_url
                except Exception as e:
                    print(f"skip: invalid video object error={e}")
                    continue

                print(f"videoUrl:{videoUrl}")
                description = getattr(yt, "description", "")
                formatted_tl = _format_youtube_tl(description)

                publishDate = yt.publish_date
                if (publishDate is None
                        and str(period_mode).strip().lower()
                        in {"month", "current_month", "target_month"}):
                    print("upload date unavailable; cannot verify current month; skipping")
                    page_unknown_date = True
                    continue
                if publishDate is not None:
                    publishDate = _as_utc(publishDate)
                    if publishDate < period_start:
                        stop_channel = True
                        break
                    if period_end is not None and publishDate >= period_end:
                        # The feed is newest-first.  A future/out-of-target
                        # entry is skipped, but older entries may still be in
                        # the requested target month.
                        continue

                if videoUrl in known_video_urls:
                    old = video_rows.get(videoUrl)
                    if (os.environ.get("PRICONNER_FORCE_POST") and old
                            and is_in_youtube_period(
                                yt.publish_date,
                                now,
                                period_days,
                                period_mode,
                                period_month,
                            )):
                        pending_posts.append({"url": videoUrl, "title": yt.title, "description": description, "formatted_tl": formatted_tl, "notes": "登録チャンネルの確認用（更新）", "status": "updated", "force_full": True})
                    if (os.environ.get("PRICONNER_FORCE_NEW_POST") and old
                            and is_in_youtube_period(
                                yt.publish_date,
                                now,
                                period_days,
                                period_mode,
                                period_month,
                            )):
                        pending_posts.append({"url": videoUrl, "title": yt.title, "description": description, "formatted_tl": formatted_tl, "notes": "登録チャンネルの確認用（新規）", "status": "new"})
                    if old and len(old[1]) > 3 and post_tracker.normalize_comparison_text(old[1][3]) != post_tracker.normalize_comparison_text(yt.title):
                        notes = "登録チャンネルの動画更新"
                        previous = video_post_body(old[1][3], notes, videoUrl)
                        row = list(old[1]) + [""] * max(0, 6 - len(old[1]))
                        row[3], row[5] = yt.title, previous
                        if hasattr(sheetVideo, "update"):
                            sheetVideo.update(f"A{old[0]}:F{old[0]}", [row[:6]], value_input_option="USER_ENTERED")
                        pending_posts.append({"url": videoUrl, "title": yt.title, "description": description, "formatted_tl": formatted_tl, "notes": notes, "status": "updated", "previous_text": previous, "force_full": True})
                    # Keep scanning known entries inside the configured period
                    # so title updates on older videos are not missed.
                    continue

                if publishDate is None:
                    # Flat playlist entries occasionally omit upload_date. Since
                    # this is a managed channel and the entry is before the first
                    # known video, keeping it is safer than silently losing a new
                    # upload. The next run deduplicates it by URL.
                    print("upload date unavailable; keeping entry")
                    publishDate = now
                else:
                    publishDate = _as_utc(publishDate)

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
                pending_posts.append({"url": videoUrl, "title": yt.title, "description": description, "formatted_tl": formatted_tl, "notes": "登録チャンネルの新着動画", "status": "new"})

            if (stop_channel or entry_count < DEFAULT_CHANNEL_LIMIT
                    or (page_unknown_date and str(period_mode).strip().lower()
                        in {"month", "current_month", "target_month"})):
                break
            page_start += DEFAULT_CHANNEL_LIMIT

        if len(videoValues) > 0:
            sheetVideo.insert_rows(videoValues, row=2)

            # Defer notifications until both the arrival rows and damage URLs
            # have had a chance to become durable.

        channelValues = [[publishDateString, nowScanTime]]
        try:
            channel_updates.append({
                "range": f"E{i}:F{i}",
                "values": channelValues,
            })
        except Exception as error:
            # The arrival rows are already durable; a metadata update should
            # not suppress the pending URL registration and notifications.
            print(f"失敗: YouTubeチャンネル状態更新: {error}")

        sleep(wait_time)

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
            _post_configured(post, post_tracker.post_content({**item, "text": body}))
            posted_count += 1
        except Exception as error:
            print(f"失敗: YouTube URL通知 {item['url']}: {error}")
            post_failures[f"{type(error).__name__}: {error}"] += 1
        sleep(wait_time)

    if channel_updates:
        if hasattr(sheetChannel, "batch_update"):
            sheetChannel.batch_update(
                channel_updates, raw=False, value_input_option="USER_ENTERED"
            )
        else:
            for update in channel_updates:
                sheetChannel.update(
                    update["range"], update["values"],
                    value_input_option="USER_ENTERED",
                )

    print(
        f"YouTubeチャンネル投稿結果: 候補{len(post_items)}件 / 成功{posted_count}件 / "
        f"失敗{sum(post_failures.values())}件"
    )
    for reason, count in post_failures.items():
        print(f"失敗理由 ({count}件): {reason}")

    if count > 0:
        try:
            _notify_configured(notify, f"Youtube新着{count}件")
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
    existing_urls = gspread.existing_column_values(sheet, 1)
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
