"""Alternative YouTube keyword-search sources and comparison logging."""

import json
import os
import time
from pathlib import Path
from threading import Lock

import requests

import gspread_utils as gspread
import youtube_handoff

DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 50
SEARCH_BACKENDS = ("yt-dlp", "pytubefix", "direct")
SEARCH_LOG_LOCK = Lock()


def _search_limit():
    limit = gspread.get_int_config_value(
        "youtube", "search_limit", DEFAULT_SEARCH_LIMIT,
        minimum=1, maximum=MAX_SEARCH_LIMIT,
    )
    env_limit = os.environ.get("PRICONNER_YOUTUBE_SEARCH_LIMIT", "").strip()
    if env_limit.isdigit():
        limit = int(env_limit)
    return max(1, min(limit, MAX_SEARCH_LIMIT))


def search_youtube_pytubefix(query, video_factory):
    """Search with pytubefix and return normalized candidates."""
    from pytubefix.contrib.search import Filter, Search

    if (
        os.environ.get("PRICONNER_YOUTUBE_SEARCH_DATE_AFTER", "").strip()
        or os.environ.get("PRICONNER_YOUTUBE_SEARCH_DATE_BEFORE", "").strip()
    ):
        raise ValueError("pytubefix comparison does not support explicit date overrides")
    filters = (
        Filter.create()
        .upload_date(Filter.UploadDate.THIS_WEEK)
        .type(Filter.Type.VIDEO)
        .sort_by(Filter.SortBy.UPLOAD_DATE)
    )
    results = list(Search(query, filters=filters).videos[:_search_limit()])
    videos = []
    for result in results:
        info = {
            "id": getattr(result, "video_id", ""),
            "title": getattr(result, "title", ""),
            "webpage_url": getattr(result, "watch_url", ""),
            "channel": getattr(result, "author", ""),
            "channel_id": getattr(result, "channel_id", ""),
            "channel_url": getattr(result, "channel_url", ""),
        }
        try:
            videos.append(video_factory(info))
        except (KeyError, TypeError, ValueError):
            continue
    return videos


def _walk_video_renderers(value):
    if isinstance(value, dict):
        renderer = value.get("videoRenderer")
        if isinstance(renderer, dict):
            yield renderer
        for child in value.values():
            yield from _walk_video_renderers(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_video_renderers(child)


def _youtube_initial_data(page):
    marker = "ytInitialData"
    marker_index = page.find(marker)
    while marker_index >= 0:
        equals = page.find("=", marker_index + len(marker))
        if equals >= 0:
            start = page.find("{", equals + 1)
            if start >= 0:
                try:
                    data, _ = json.JSONDecoder().raw_decode(page[start:])
                    return data
                except json.JSONDecodeError:
                    pass
        marker_index = page.find(marker, marker_index + len(marker))
    raise ValueError("YouTube search page did not contain ytInitialData")


def search_youtube_direct(query, video_factory):
    """Read video cards from YouTube's public search results page directly."""
    response = requests.get(
        "https://www.youtube.com/results",
        params={"search_query": query},
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
            )
        },
        timeout=20,
    )
    response.raise_for_status()
    data = _youtube_initial_data(response.text)
    videos = []
    seen = set()
    search_limit = _search_limit()
    for renderer in _walk_video_renderers(data):
        video_id = renderer.get("videoId")
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        title = "".join(
            item.get("text", "") for item in renderer.get("title", {}).get("runs", [])
        )
        owner = renderer.get("ownerText", {}).get("runs", [])
        channel_name = "".join(item.get("text", "") for item in owner)
        browse = next((
            item.get("navigationEndpoint", {}).get("browseEndpoint", {})
            for item in owner
            if item.get("navigationEndpoint", {}).get("browseEndpoint", {}).get("browseId")
        ), {})
        channel_id = browse.get("browseId", "")
        info = {
            "id": video_id,
            "title": title,
            "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
            "channel": channel_name,
            "channel_id": channel_id,
            "channel_url": (
                f"https://www.youtube.com/channel/{channel_id}" if channel_id else ""
            ),
        }
        try:
            videos.append(video_factory(info))
        except (KeyError, TypeError, ValueError):
            continue
        if len(videos) >= search_limit:
            break
    return videos


def _video_log_record(video):
    publish_date = getattr(video, "publish_date", None)
    return {
        "id": youtube_handoff.video_id(getattr(video, "watch_url", "")),
        "url": getattr(video, "watch_url", ""),
        "title": getattr(video, "title", ""),
        "channel_id": getattr(video, "channel_id", ""),
        "channel_name": getattr(video, "channel_name", ""),
        "published_at": publish_date.isoformat() if publish_date else None,
    }


def write_search_comparison(query, backend_results, clock):
    """Append one JSONL record with source candidates and overlap details."""
    primary_ids = {
        _video_log_record(video)["id"]
        for video in backend_results.get("yt-dlp", {}).get("videos", [])
    }
    record = {
        "searched_at": clock.isoformat(),
        "query": query,
        "backends": {
            backend: {
                "elapsed_seconds": round(result["elapsed"], 3),
                "error": result["error"],
                "videos": [_video_log_record(video) for video in result["videos"]],
                "overlap_with_ytdlp": len({
                    _video_log_record(video)["id"] for video in result["videos"]
                } & primary_ids),
                "only_ids_vs_ytdlp": sorted({
                    _video_log_record(video)["id"] for video in result["videos"]
                } - primary_ids),
            }
            for backend, result in backend_results.items()
        },
    }
    path = _comparison_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with SEARCH_LOG_LOCK, path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as error:
        print(f"YouTube検索比較ログ保存失敗: {type(error).__name__}: {error}")


def _comparison_log_path():
    configured = os.environ.get("PRICONNER_YOUTUBE_SEARCH_LOG", "").strip()
    if configured:
        return Path(configured).expanduser()
    runtime = (
        os.environ.get("PRICONNER_MONITOR_SHARED_RUNTIME_DIR", "").strip()
        or os.environ.get("PRICONNER_MONITOR_RUNTIME_DIR", "").strip()
    )
    if runtime:
        return Path(runtime).expanduser() / "youtube_search_comparison.jsonl"
    return (
        Path.home() / ".local" / "state" / "priconner-tl-scanner"
        / "youtube_search_comparison.jsonl"
    )


def run_search_backend(backend, query, primary_search, video_factory, now_factory=None):
    """Run one source and capture its latency/error without failing the scan."""
    started = time.perf_counter()
    try:
        if backend == "yt-dlp":
            videos = primary_search(query, now_factory=now_factory)
        elif backend == "pytubefix":
            videos = search_youtube_pytubefix(query, video_factory)
        elif backend == "direct":
            videos = search_youtube_direct(query, video_factory)
        else:
            raise ValueError(f"Unknown YouTube search backend: {backend}")
        return {"videos": videos, "elapsed": time.perf_counter() - started, "error": ""}
    except Exception as error:  # noqa: BLE001 - each comparison source fails independently
        return {
            "videos": [],
            "elapsed": time.perf_counter() - started,
            "error": f"{type(error).__name__}: {error}",
        }
