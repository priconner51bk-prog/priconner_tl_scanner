import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime as DateTime
from datetime import timezone

from yt_dlp import YoutubeDL

import datetime_utils as datetime
import discord_utils as discord
import gspread_utils as gspread
import post_change_tracker as post_tracker
from new_arrivals_markdown import write_arrival
from runtime_utils import run_locked
from video_relevance import (
    clan_battle_filter_reason,
    load_ng_terms,
)
from youtube_common import (
    as_utc,
    build_video_sheet_row,
    build_youtube_post,
    effective_content_month,
    is_in_youtube_period,
    log_youtube_registration_results,
    post_new_url_to_experimental,
    video_metadata_fields,
    video_post_body,
    write_urls_with_retry,
    youtube_period_bounds,
)
from youtube_rss import RSSChannel
from youtube_storage import ensure_video_headers, persist_rows
from youtube_url_api import register_urls

URL_YOUTUBE_CHANNEL = "https://www.youtube.com/channel/"
# Keep both channel requests and Discord posts serialized.  Discord also has
# its own API interval/429 retry guard, but this delay protects injected or
# alternate post implementations used by the scheduled runner.
# Discord posts are queued by the normal runner, so a long collector delay is
# unnecessary. Keep a small configurable pause for alternate/injected senders.
WAIT_TIME = float(os.environ.get("PRICONNER_YOUTUBE_WAIT", "0.25"))
DEFAULT_PERIOD_DAYS = 7
# A bounded page keeps scans predictable while retaining upload dates needed
# for the current-month safety boundary.
DEFAULT_CHANNEL_LIMIT = 20
YOUTUBE_SOCKET_TIMEOUT = 15
YOUTUBE_RETRIES = 1
USE_RSS_DISCOVERY = os.environ.get("PRICONNER_YOUTUBE_RSS", "1").strip().lower() not in {"0", "false", "no"}
RSS_WORKERS = 5


_as_utc = as_utc


_write_urls_with_retry = write_urls_with_retry


def _post_configured(
    post, text, channel_key="boss0_tl", dedupe_key=None, summary_author=None
):
    if post is discord.post:
        return discord.post_to_configured_guilds(
            text,
            channel_key=channel_key,
            dedupe_key=dedupe_key,
            summary_author=summary_author,
        )
    return post(text)


def _rss_covers_period(channel_id, videos, period_start):
    """RSS is complete only when its oldest dated item crosses the boundary."""
    dates = [_as_utc(getattr(video, "publish_date", None)) for video in videos]
    dates = [value for value in dates if value is not None]
    return bool(dates) and min(dates) < period_start


def _boss_channel_key(title, boss_names):
    """Route channel-discovered videos by the longest matching boss name."""
    title = str(title or "")
    matches = [(len(name), index) for index, name in enumerate(boss_names[:5], 1)
               if name and name in title]
    return f"boss{max(matches)[1]}_tl" if matches else "boss0_tl"


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


def enrich_rss_video(video):
    """Fetch full metadata for an RSS-discovered video before using it."""
    try:
        with YoutubeDL(
            {
                "quiet": True,
                "skip_download": True,
                "extract_flat": False,
                "ignoreerrors": True,
                "retries": YOUTUBE_RETRIES,
                "extractor_retries": YOUTUBE_RETRIES,
                "remote_components": ["ejs:github"],
            }
        ) as ydl:
            info = ydl.extract_info(video.watch_url, download=False) or {}
        if info and info.get("id"):
            return YTDLPVideo(info)
    except Exception as error:
        print(f"YouTube詳細情報取得失敗: {video.watch_url}: {error}")
    return video


def _complete_video_row(old_row, channel_name, channel_url, published_at,
                        title, url, notes, description, formatted_tl, body):
    """Merge fetched metadata into old rows without losing legacy columns."""
    values = list(old_row or [])
    while len(values) < 9:
        values.append("")
    values[:9] = build_video_sheet_row(
        channel_name, channel_url, published_at, title, url, notes,
        description, formatted_tl, body,
    )
    return values[:9]


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
    ensure_video_headers(sheetVideo)
    count = 0
    damage_urls = []
    pending_posts = []
    post_failures = Counter()
    posted_count = 0

    sheetChannel = ss.worksheet("YouTubeチャンネル")
    try:
        boss_rows = ss.worksheet("ボス名").get_all_values()
        boss_names = [row[0] for row in boss_rows[1:] if row and row[0]]
    except Exception:
        boss_names = []
    video_sheet_rows = sheetVideo.get_all_values()
    videoUrls = [row[4] for row in video_sheet_rows if len(row) > 4]
    known_video_urls = set(videoUrls)
    video_rows = {row[4]: (index, row) for index, row in enumerate(video_sheet_rows[1:], start=2) if len(row) > 4 and row[4]}
    rows = sheetChannel.get_all_values()
    now = _as_utc(now_factory())
    period_days = gspread.get_int_config_value(
        "youtube", "period_days", DEFAULT_PERIOD_DAYS, minimum=1
    )
    period_mode = gspread.get_config_value("youtube", "period_mode", "current_month")
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
    ng_terms = load_ng_terms(ss)
    inactive_days = gspread.get_int_config_value(
        "maintenance", "inactive_days", 60, minimum=1
    )
    period_start, period_end = youtube_period_bounds(
        now, period_days, period_mode, period_month
    )
    channel_updates = []
    rss_channels = {}
    if USE_RSS_DISCOVERY and channel_factory is YTDLPChannel:
        rss_targets = []
        for row in rows[1:]:
            if len(row) <= 1 or not row[1] or (len(row) > 6 and row[6]):
                continue
            latest_video = row[4] if len(row) > 4 else ""
            if latest_video:
                try:
                    latest_video_at = _as_utc(datetime.string2DateTime(latest_video))
                except (TypeError, ValueError):
                    latest_video_at = None
                if latest_video_at and (now - latest_video_at).total_seconds() > inactive_days * 86400:
                    continue
            rss_targets.append(row[1])
        with ThreadPoolExecutor(max_workers=RSS_WORKERS) as executor:
            futures = {
                executor.submit(_rss_channel_for_period, channel_id, period_start): channel_id
                for channel_id in rss_targets
            }
            for future in as_completed(futures):
                channel_id = futures[future]
                try:
                    rss_channels[channel_id] = future.result()
                except Exception as error:
                    print(f"RSS並列取得失敗 ({channel_id}): {type(error).__name__}: {error}")
        print(f"RSS並列取得完了: {len(rss_targets)}チャンネル / 採用{sum(value is not None for value in rss_channels.values())}件")

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
                rss_channel = rss_channels.get(channelId)
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

            rss_page = isinstance(ch, RSSChannel)

            stop_channel = False
            page_unknown_date = False
            for yt in page_videos:
                try:
                    videoUrl = yt.watch_url
                except Exception as e:
                    print(f"skip: invalid video object error={e}")
                    continue

                print(f"videoUrl:{videoUrl}")
                if rss_page:
                    yt = enrich_rss_video(yt)
                description = getattr(yt, "description", "")

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

                rejection = clan_battle_filter_reason(
                    yt,
                    boss_names=boss_names[:5],
                    ng_terms=ng_terms,
                    content_month=content_month,
                )
                if rejection:
                    print(f"skip: YouTube内容フィルタ ({rejection}): {yt.title}")
                    continue

                notes = "登録チャンネルの新着動画"
                description, formatted_tl, current_body = video_metadata_fields(
                    yt.title, notes, videoUrl, description
                )
                if not description:
                    print(
                        f"skip: YouTube詳細情報不足（概要欄なし）: {videoUrl}"
                    )
                    continue

                if videoUrl in known_video_urls:
                    old = video_rows.get(videoUrl)
                    if old and hasattr(sheetVideo, "update"):
                        merged_row = _complete_video_row(
                            old[1], channelName, channelUrl,
                            datetime.dateTime2String(yt.publish_date),
                            yt.title, videoUrl, notes, description,
                            formatted_tl, current_body,
                        )
                        if list(old[1][:9]) != merged_row:
                            sheetVideo.update(
                                f"A{old[0]}:I{old[0]}",
                                [merged_row],
                                value_input_option="USER_ENTERED",
                            )
                    if (os.environ.get("PRICONNER_FORCE_POST") and old
                            and is_in_youtube_period(
                                yt.publish_date,
                                now,
                                period_days,
                                period_mode,
                                period_month,
                            )):
                        pending_posts.append({"url": videoUrl, "title": yt.title, "description": description, "formatted_tl": formatted_tl, "notes": "登録チャンネルの確認用（更新）", "channel_key": _boss_channel_key(yt.title, boss_names), "status": "updated", "force_full": True, "summary_author": channelName})
                    if ((os.environ.get("PRICONNER_FORCE_NEW_POST")
                         or os.environ.get("PRICONNER_REPOST_NEW")) and old
                            and is_in_youtube_period(
                                yt.publish_date,
                                now,
                                period_days,
                                period_mode,
                                period_month,
                            )):
                        pending_posts.append({"url": videoUrl, "title": yt.title, "description": description, "formatted_tl": formatted_tl, "notes": "登録チャンネルの確認用（新規）", "channel_key": _boss_channel_key(yt.title, boss_names), "status": "new", "summary_author": channelName})
                    if old and len(old[1]) > 3 and post_tracker.normalize_comparison_text(old[1][3]) != post_tracker.normalize_comparison_text(yt.title):
                        notes = "登録チャンネルの動画更新"
                        previous = old[1][8] if len(old[1]) > 8 and old[1][8] else (old[1][5] if len(old[1]) > 5 else "")
                        current_body = video_post_body(
                            yt.title, notes, videoUrl, description, formatted_tl
                        )
                        row = _complete_video_row(
                            old[1], channelName, channelUrl,
                            datetime.dateTime2String(yt.publish_date),
                            yt.title, videoUrl, notes, description,
                            formatted_tl, current_body,
                        )
                        if hasattr(sheetVideo, "update"):
                            sheetVideo.update(f"A{old[0]}:I{old[0]}", [row], value_input_option="USER_ENTERED")
                        pending_posts.append({"url": videoUrl, "title": yt.title, "description": description, "formatted_tl": formatted_tl, "notes": notes, "channel_key": _boss_channel_key(yt.title, boss_names), "status": "updated", "previous_text": previous, "summary_author": channelName})
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

                notes = "登録チャンネルの新着動画"
                description, formatted_tl, post_body = video_metadata_fields(
                    yt.title, notes, videoUrl, description
                )
                if not description:
                    print(
                        f"skip: YouTube詳細情報不足（概要欄なし）: {videoUrl}"
                    )
                    continue
                values = build_video_sheet_row(
                    channelName,
                    channelUrl,
                    datetime.dateTime2String(publishDate),
                    yt.title,
                    videoUrl,
                    notes,
                    description,
                    formatted_tl,
                    post_body,
                )
                print(f"YouTube動画タイトル「{yt.title}」")
                videoValues.append(values)
                write_arrival(
                    "youtube-channel", yt.title, videoUrl, yt.publish_date,
                    channel_name=channelName,
                    notes=notes,
                    details={
                        "channel_url": channelUrl,
                        "動画概要欄": description,
                        "TL整形": formatted_tl,
                        "投稿直前本文": post_body,
                    },
                )
                videoUrls.append(videoUrl)
                known_video_urls.add(videoUrl)

                count += 1
                damage_urls.append(videoUrl)
                pending_posts.append(build_youtube_post(
                    videoUrl, yt.title, description, formatted_tl, notes,
                    _boss_channel_key(yt.title, boss_names),
                    summary_author=channelName,
                ))

            if (stop_channel or entry_count < DEFAULT_CHANNEL_LIMIT
                    or (page_unknown_date and str(period_mode).strip().lower()
                        in {"month", "current_month", "target_month"})):
                break
            page_start += DEFAULT_CHANNEL_LIMIT

        if len(videoValues) > 0:
            persist_rows(sheetChannel, sheetVideo, [], videoValues)

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

    registration_results = []
    try:
        registration_results = _write_urls_with_retry(write_urls, damage_urls, sleep)
    except Exception as error:
        print(f"失敗: YouTube URL登録: {error}")
    newly_registered = log_youtube_registration_results(registration_results)

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
            item_content = post_tracker.post_content({**item, "text": body})
            dedupe_key = f"youtube-new:{item['url']}" if item.get("status") == "new" else None
            if item["url"] in newly_registered:
                try:
                    post_new_url_to_experimental(item, item_content)
                except Exception as error:
                    print(f"失敗: 実験サーバー新着TL通知 {item['url']}: {error}")
            _post_configured(
                post,
                item_content,
                item.get("channel_key", "boss0_tl"),
                dedupe_key,
                item.get("summary_author", ""),
            )
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
        sheetVideo.sort((3, "des"), range="A2:Z10000")
        gspread.deleteEmptyRows(sheetVideo)
        sheetChannel.sort((5, "des"), range="A2:Z10000")
        gspread.deleteEmptyRows(sheetChannel)


def write_urls_to_youtube_sheet(urls):
    """Register discovered videos through the spreadsheet's idempotent API."""
    return register_urls(urls)


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
