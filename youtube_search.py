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
import youtube_handoff
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
    video_metadata_fields,
    video_post_body,
    write_urls_with_retry,
)
from youtube_storage import ensure_video_headers, persist_rows

URL_YOUTUBE_CHANNEL = "https://www.youtube.com/channel/"
# The collector normally enqueues Discord work; rate limiting belongs to the
# single-writer queue drain rather than delaying discovery.
WAIT_TIME = float(os.environ.get("PRICONNER_YOUTUBE_WAIT", "0.25"))
DEFAULT_PERIOD_DAYS = 1
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 50
SEARCH_WORKERS = 5
SEARCH_RETRIES = 2
TITLE_SEARCH_MARKERS = {
    "メデューサ": ("メデューサ", "メドューサ"),
}


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


def _post_to_channel(post, text, channel_key, dedupe_key=None, summary_author=None):
    if post is discord.post:
        return discord.post_to_configured_guilds(
            text,
            channel_key=channel_key,
            dedupe_key=dedupe_key,
            summary_author=summary_author,
        )
    try:
        return post(text, channel_key=channel_key)
    except TypeError:
        # Keep compatibility with simple injected test callbacks.
        return post(text)


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
        self.channel_name = info.get("channel") or info.get("uploader", "")
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
    period_mode = gspread.get_config_value("youtube", "period_mode", "current_month")
    period_month = (
        os.environ.get("PRICONNER_YOUTUBE_PERIOD_MONTH", "").strip()
        or gspread.get_config_value("youtube", "period_month", "")
    )
    search_limit = gspread.get_int_config_value(
        "youtube", "search_limit", DEFAULT_SEARCH_LIMIT, minimum=1, maximum=MAX_SEARCH_LIMIT
    )
    search_limit = max(1, min(search_limit, MAX_SEARCH_LIMIT))
    env_limit = os.environ.get("PRICONNER_YOUTUBE_SEARCH_LIMIT", "").strip()
    if env_limit.isdigit():
        search_limit = max(1, min(int(env_limit), MAX_SEARCH_LIMIT))
    options = {
        "quiet": True,
        "skip_download": True,
        # Discovery only: fetch IDs/titles/dates first.  Descriptions and tags
        # are fetched later only for candidates that survive the cheap filters.
        "extract_flat": True,
        "remote_components": ["ejs:github"],
        "retries": 1,
        "extractor_retries": 1,
        "sleep_interval_requests": 0.5,
        "playlistend": search_limit,
    }
    date_after = os.environ.get("PRICONNER_YOUTUBE_SEARCH_DATE_AFTER", "").strip()
    date_before = os.environ.get("PRICONNER_YOUTUBE_SEARCH_DATE_BEFORE", "").strip()
    search_start = os.environ.get("PRICONNER_YOUTUBE_SEARCH_START", "").strip()
    if date_after:
        options["dateafter"] = date_after
    if date_before:
        options["datebefore"] = date_before
    if search_start.isdigit() and int(search_start) > 1:
        options["playliststart"] = int(search_start)
        options["playlistend"] = int(search_start) + search_limit - 1
    result = None
    for attempt in range(SEARCH_RETRIES + 1):
        try:
            with YoutubeDL(options) as ydl:
                result = ydl.extract_info(
                    f"ytsearch{search_limit}:{query}", download=False
                )
            break
        except Exception as error:
            if attempt >= SEARCH_RETRIES:
                print(f"YouTube検索失敗: query={query!r}: {error}")
                result = {"entries": []}
            else:
                time.sleep(2 ** attempt)
    entries = list((result or {}).get("entries") or [])

    result = {**(result or {}), "entries": entries}
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


def enrich_video(video):
    """Fetch full metadata for one search candidate when it is actually used."""
    try:
        with YoutubeDL(
            {
                "quiet": True,
                "skip_download": True,
                "extract_flat": False,
                "ignoreerrors": True,
                "retries": 1,
                "extractor_retries": 1,
                "remote_components": ["ejs:github"],
            }
        ) as ydl:
            info = ydl.extract_info(video.watch_url, download=False)
        if info:
            return YTDLPVideo(info)
    except Exception as error:
        print(f"YouTube詳細情報取得失敗、候補情報を使用: {video.watch_url}: {error}")
    return video


def fetch_handoff_video(url):
    """Fetch one full video object from a URL handed off by Discord."""
    try:
        with YoutubeDL(
            {
                "quiet": True,
                "skip_download": True,
                "extract_flat": False,
                "ignoreerrors": True,
                "retries": 1,
                "extractor_retries": 1,
                "remote_components": ["ejs:github"],
            }
        ) as ydl:
            info = ydl.extract_info(url, download=False)
        return YTDLPVideo(info) if info else None
    except Exception as error:
        print(f"YouTube引き渡しURL取得失敗: {url}: {error}")
        return None


def _handoff_boss_index(title, boss_names):
    """Route a handed-off video using the longest matching configured boss name."""
    matches = [
        (len(name), index)
        for index, name in enumerate(boss_names[:5], start=1)
        if name and name in str(title or "")
    ]
    return max(matches)[1] if matches else 0


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
            else gspread.get_config_value("youtube", "period_mode", "current_month")
        )
    if period_month is None:
        period_month = (
            os.environ.get("PRICONNER_YOUTUBE_PERIOD_MONTH", "").strip()
            or gspread.get_config_value("youtube", "period_month", "")
        )
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
    channel_sheet_rows = sheetChannel.get_all_values()
    sheetChannelIgnores = [
        row[3] for row in channel_sheet_rows if len(row) > 6 and row[6]
    ]

    sheetVideo = ss.worksheet("YouTube動画")
    ensure_video_headers(sheetVideo)

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
    video_sheet_rows = sheetVideo.get_all_values()
    videoUrls = [row[4] for row in video_sheet_rows if len(row) > 4]
    known_video_urls = set(videoUrls)
    known_video_ids = {
        youtube_handoff.video_id(url) or url for url in videoUrls
    }
    video_rows = {row[4]: (index, row) for index, row in enumerate(video_sheet_rows[1:], start=2) if len(row) > 4 and row[4]}
    channelIds = [row[1] for row in channel_sheet_rows if len(row) > 1]
    channel_rows = {
        row[1]: (index, row)
        for index, row in enumerate(channel_sheet_rows[1:], start=2)
        if len(row) > 1 and row[1]
    }
    channel_refreshes = {}
    now = _as_utc(
        now_factory() if now_factory else DateTime.now(timezone.utc)
    )
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
    channel_name_cache = {}
    channel_values = []
    video_values = []

    selected = selected_bosses(bossNames)
    handoff_urls = youtube_handoff.pending()
    handoff_retry_urls = set()
    handoff_videos = []
    for handoff_url in handoff_urls:
        handoff_id = youtube_handoff.video_id(handoff_url) or handoff_url
        if handoff_id in known_video_ids:
            continue
        video = fetch_handoff_video(handoff_url)
        if video is None:
            handoff_retry_urls.add(handoff_url)
            continue
        handoff_videos.append(video)

    search_jobs = [
        (boss_index, bossName, search_term)
        for boss_index, bossName in selected
        for search_term in TITLE_SEARCH_MARKERS.get(bossName, (bossName,))
    ]
    search_results = {}
    if search_jobs:
        with ThreadPoolExecutor(max_workers=min(SEARCH_WORKERS, len(search_jobs))) as executor:
            futures = {
                executor.submit(search_factory, search_term): job
                for job in search_jobs
                for search_term in [job[2]]
            }
            for future in as_completed(futures):
                search_results[futures[future]] = future.result()

    handoff_groups = {}
    for video in handoff_videos:
        boss_index = _handoff_boss_index(video.title, bossNames)
        handoff_groups.setdefault(boss_index, []).append(video)
    scan_groups = list(selected)
    for boss_index, videos in handoff_groups.items():
        handoff_name = f"__handoff_boss_{boss_index}__"
        scan_groups.append((boss_index, handoff_name))
        search_results[(boss_index, handoff_name, handoff_name)] = videos

    for boss_index, bossName in scan_groups:
        print(f"ボス名：{bossName}")
        nowScanTime = datetime.dateTime2String(now)
        videos = []
        seen_search_urls = set()
        # The reference sheet shows many valid titles containing only the
        # boss name and damage number, without クラバト/4段階/EX markers.
        is_handoff_group = bossName.startswith("__handoff_boss_")
        search_terms = (
            (bossName,)
            if is_handoff_group
            else TITLE_SEARCH_MARKERS.get(bossName, (bossName,))
        )
        for search_term in search_terms:
            search = search_results[(boss_index, bossName, search_term)]
            candidates = search.videos if hasattr(search, "videos") else search
            for candidate in candidates:
                if candidate.watch_url not in seen_search_urls:
                    seen_search_urls.add(candidate.watch_url)
                    videos.append(candidate)
        sleep(wait_time)

        for video in videos:
            if (not is_handoff_group and not is_recent_video(
                video,
                now=now,
                period_days=period_days,
                period_mode=period_mode,
                period_month=period_month,
            )):
                continue
            # Search results are intentionally flat.  Only period-matching
            # candidates reach this more expensive metadata request.
            if not is_handoff_group:
                video = enrich_video(video)
            videoUrl = youtube_handoff.canonical_url(video.watch_url)
            if (
                is_handoff_group
                and (youtube_handoff.video_id(videoUrl) or videoUrl) in known_video_ids
            ):
                continue
            description = getattr(video, "description", "")
            rejection = clan_battle_filter_reason(
                video,
                boss_names=bossNames[:5],
                ng_terms=ng_terms,
                content_month=content_month,
            )
            if rejection:
                print(f"skip: YouTube検索内容フィルタ ({rejection}): {video.title}")
                continue
            notes = "キーワード検索の新着動画"
            description, formatted_tl, current_body = video_metadata_fields(
                video.title, notes, videoUrl, description
            )
            if not description:
                print(
                    f"skip: YouTube詳細情報不足（概要欄なし）: {videoUrl}"
                )
                continue
            if videoUrl in known_video_urls:
                old = video_rows.get(videoUrl)
                if old and hasattr(sheetVideo, "update"):
                    old_row = list(old[1]) + [""] * max(0, 9 - len(old[1]))
                    merged_row = build_video_sheet_row(
                        old_row[0], old_row[1], old_row[2], video.title,
                        videoUrl, notes, description, formatted_tl,
                        current_body,
                    )
                    if old_row[:9] != merged_row:
                        sheetVideo.update(
                            f"A{old[0]}:I{old[0]}",
                            [merged_row],
                            value_input_option="USER_ENTERED",
                        )
                if os.environ.get("PRICONNER_FORCE_POST") and old:
                    pending_posts.append({"url": videoUrl, "title": video.title, "description": description, "formatted_tl": formatted_tl, "notes": "確認用（更新）", "channel_key": f"boss{boss_index}_tl", "status": "updated", "force_full": True, "summary_author": getattr(video, "channel_name", "")})
                if (os.environ.get("PRICONNER_FORCE_NEW_POST")
                        or os.environ.get("PRICONNER_REPOST_NEW")) and old:
                    pending_posts.append({"url": videoUrl, "title": video.title, "description": description, "formatted_tl": formatted_tl, "notes": "確認用（新規）", "channel_key": f"boss{boss_index}_tl", "status": "new", "summary_author": getattr(video, "channel_name", "")})
                if old and len(old[1]) > 3 and post_tracker.normalize_comparison_text(old[1][3]) != post_tracker.normalize_comparison_text(video.title):
                    notes = "更新"
                    previous = old[1][8] if len(old[1]) > 8 and old[1][8] else (old[1][5] if len(old[1]) > 5 else "")
                    current_body = video_post_body(
                        video.title, notes, videoUrl, description, formatted_tl
                    )
                    row = list(old[1]) + [""] * max(0, 9 - len(old[1]))
                    row = build_video_sheet_row(
                        row[0], row[1], row[2], video.title, videoUrl,
                        notes, description, formatted_tl, current_body,
                    )
                    if hasattr(sheetVideo, "update"):
                        sheetVideo.update(f"A{old[0]}:I{old[0]}", [row], value_input_option="USER_ENTERED")
                    pending_posts.append({"url": videoUrl, "title": video.title, "description": description, "formatted_tl": formatted_tl, "notes": notes, "channel_key": f"boss{boss_index}_tl", "status": "updated", "previous_text": previous, "summary_author": getattr(video, "channel_name", "")})
                continue

            # A boss-name search is authoritative here; the reference sheet
            # contains valid titles that omit explicit clan-battle markers.

            channelUrl = video.channel_url
            if not channelUrl or not video.channel_id:
                print("skip: channel information is unavailable")
                continue
            if channelUrl in sheetChannelIgnores:
                continue

            videoTitle = video.title
            publishDate = datetime.dateTime2String(video.publish_date)
            channelId = video.channel_id

            if channelId in channel_rows:
                channel_refreshes[channelId] = (
                    channel_rows[channelId][0],
                    video.publish_date,
                    nowScanTime,
                )

            if channelUrl not in channel_name_cache:
                channel_name_cache[channelUrl] = getattr(video, "channel_name", "")
            if not channel_name_cache[channelUrl]:
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
                build_video_sheet_row(
                    channelName, channelUrl, publishDate, videoTitle,
                    videoUrl, notes, description, formatted_tl, current_body,
                )
            )
            write_arrival(
                "youtube-search", videoTitle, videoUrl, video.publish_date,
                channel_name=channelName,
                notes=notes,
                details={
                    "channel_url": channelUrl,
                    "動画概要欄": description,
                    "TL整形": formatted_tl,
                    "投稿直前本文": current_body,
                },
            )
            print(video_values[-1:])
            videoUrls.append(videoUrl)
            known_video_urls.add(videoUrl)
            known_video_ids.add(youtube_handoff.video_id(videoUrl) or videoUrl)

            count += 1
            damage_urls.append(videoUrl)
            pending_posts.append(build_youtube_post(
                videoUrl, videoTitle, description, formatted_tl, notes,
                f"boss{boss_index}_tl",
                summary_author=channelName,
            ))

            if is_handoff_group:
                handoff_urls_for_video = {
                    url for url in handoff_urls if url == videoUrl
                }
                handoff_retry_urls.difference_update(handoff_urls_for_video)

    persist_rows(sheetChannel, sheetVideo, channel_values, video_values)
    channel_updates = []
    for row_number, publish_date, scan_time in channel_refreshes.values():
        existing = list(channel_sheet_rows[row_number - 1])
        while len(existing) < 6:
            existing.append("")
        existing[4] = datetime.dateTime2String(publish_date)
        existing[5] = scan_time
        channel_updates.append({
            "range": f"A{row_number}:F{row_number}",
            "values": [existing[:6]],
        })
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
    try:
        _write_urls_with_retry(write_urls, damage_urls, sleep)
    except Exception as error:
        print(f"失敗: YouTube URL登録: {error}")

    try:
        completed_handoff_urls = set(handoff_urls) - handoff_retry_urls
        youtube_handoff.acknowledge(completed_handoff_urls)
    except Exception as error:
        print(f"警告: YouTube URL引き渡しキュー更新に失敗: {error}")

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
            _post_to_channel(
                post,
                item_content,
                item["channel_key"],
                dedupe_key,
                item.get("summary_author", ""),
            )
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
        findYouTubeVideo()
        print("-----------------------------------------------")
        print(f"終了{datetime.nowString()}")
        print("-----------------------------------------------")

    return run_locked(run, lock_name="youtube_search.lock")


if __name__ == "__main__":
    main()
