"""Per-source YouTube search snapshots shared with the single writer stage."""

import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gspread_utils as gspread
import youtube_handoff
import youtube_search_backends
from runtime_utils import default_runtime_dir

BACKENDS = youtube_search_backends.SEARCH_BACKENDS
SNAPSHOT_TTL = timedelta(minutes=12)


def snapshot_directory():
    shared_runtime = os.environ.get("PRICONNER_MONITOR_SHARED_RUNTIME_DIR", "").strip()
    if shared_runtime:
        return Path(shared_runtime).expanduser() / "youtube-search-candidates"
    runtime = os.environ.get("PRICONNER_MONITOR_RUNTIME_DIR", "").strip()
    root = Path(runtime).expanduser() if runtime else default_runtime_dir()
    return root / "youtube-search-candidates"


def _snapshot_path(backend):
    if backend not in BACKENDS:
        raise ValueError(f"Unknown YouTube search backend: {backend}")
    return snapshot_directory() / f"{backend}.json"


def _read_snapshot(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"queries": {}}


def _write_snapshot(path, snapshot):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(snapshot, output, ensure_ascii=False, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _video_record(video):
    published_at = getattr(video, "publish_date", None)
    return {
        "id": youtube_handoff.video_id(getattr(video, "watch_url", "")),
        "url": getattr(video, "watch_url", ""),
        "title": getattr(video, "title", ""),
        "channel_name": getattr(video, "channel_name", ""),
        "channel_id": getattr(video, "channel_id", ""),
        "channel_url": getattr(video, "channel_url", ""),
        "description": getattr(video, "description", ""),
        "tags": getattr(video, "tags", []) or [],
        "published_at": published_at.isoformat() if published_at else None,
    }


def save_source_snapshot(backend, results, now=None):
    """Persist this source's latest per-query candidates and error status."""
    now = now or datetime.now(timezone.utc)
    path = _snapshot_path(backend)
    snapshot = _read_snapshot(path)
    queries = snapshot.setdefault("queries", {})
    for query, result in results.items():
        record = queries.setdefault(query, {"videos": [], "fetched_at": None})
        record["last_attempt_at"] = now.isoformat()
        record["elapsed_seconds"] = round(result["elapsed"], 3)
        record["error"] = result["error"]
        if not result["error"]:
            record["fetched_at"] = now.isoformat()
            record["videos"] = [_video_record(video) for video in result["videos"]]
    snapshot["backend"] = backend
    snapshot["updated_at"] = now.isoformat()
    _write_snapshot(path, snapshot)
    history_path = path.parent / "history" / f"{backend}-{now:%Y%m%d}.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as history:
        for query, result in results.items():
            history.write(json.dumps({
                "searched_at": now.isoformat(),
                "backend": backend,
                "query": query,
                "elapsed_seconds": round(result["elapsed"], 3),
                "error": result["error"],
                "videos": [_video_record(video) for video in result["videos"]],
            }, ensure_ascii=False) + "\n")
    return path


def _deserialize_video(record, video_factory):
    video = video_factory({
        "id": record.get("id"),
        "webpage_url": record.get("url"),
        "title": record.get("title", ""),
        "channel": record.get("channel_name", ""),
        "channel_id": record.get("channel_id", ""),
        "channel_url": record.get("channel_url", ""),
        "description": record.get("description", ""),
        "tags": record.get("tags", []),
    })
    published_at = record.get("published_at")
    if published_at:
        value = datetime.fromisoformat(published_at)
        video.publish_date = (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
    return video


def load_candidates(query, video_factory, now=None):
    """Merge fresh candidates from all source snapshots, deduping by video ID."""
    now = now or datetime.now(timezone.utc)
    merged = {}
    comparison = {}
    for backend in BACKENDS:
        snapshot = _read_snapshot(_snapshot_path(backend))
        result = snapshot.get("queries", {}).get(query, {})
        fetched_at = result.get("fetched_at")
        if not fetched_at:
            comparison[backend] = {
                "videos": [],
                "elapsed": float(result.get("elapsed_seconds", 0)),
                "error": result.get("error") or "no successful snapshot",
            }
            continue
        try:
            fetched_time = datetime.fromisoformat(fetched_at)
            if fetched_time.tzinfo is None:
                fetched_time = fetched_time.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            comparison[backend] = {
                "videos": [],
                "elapsed": float(result.get("elapsed_seconds", 0)),
                "error": "invalid snapshot timestamp",
            }
            continue
        age = now - fetched_time.astimezone(timezone.utc)
        if age > SNAPSHOT_TTL or age < timedelta(0):
            comparison[backend] = {
                "videos": [],
                "elapsed": float(result.get("elapsed_seconds", 0)),
                "error": f"stale snapshot ({int(age.total_seconds())} seconds old)",
            }
            continue
        source_videos = []
        print(
            f"検索候補キャッシュ: source={backend} query={query!r} "
            f"件数={len(result.get('videos', []))} age={int(age.total_seconds())}秒"
            + (f" error={result['error']}" if result.get("error") else "")
        )
        for record in result.get("videos", []):
            identifier = record.get("id") or record.get("url")
            if not identifier:
                continue
            try:
                video = _deserialize_video(record, video_factory)
            except (KeyError, TypeError, ValueError):
                continue
            source_videos.append(video)
            merged.setdefault(identifier, video)
        comparison[backend] = {
            "videos": source_videos,
            "elapsed": float(result.get("elapsed_seconds", 0)),
            "error": result.get("error", ""),
        }
    youtube_search_backends.write_search_comparison(query, comparison, now)
    if not merged:
        print(f"新しい検索候補スナップショットなし: query={query!r}")
    return list(merged.values())


def run_source_scan(backend, now=None):
    """Search every configured boss term once for a single backend."""
    import youtube_search

    if backend not in BACKENDS:
        raise ValueError(f"Unknown YouTube search backend: {backend}")
    spreadsheet = gspread.getNewArrivalsSheet()
    boss_rows = spreadsheet.worksheet("ボス名").get_all_values()
    boss_names = [
        row[0] for row in boss_rows[1:] if row and str(row[0]).strip()
    ]
    selected = youtube_search.selected_bosses(boss_names)
    jobs = [
        (boss_name, search_term)
        for _, boss_name in selected
        for search_term in youtube_search.TITLE_SEARCH_MARKERS.get(
            boss_name, (boss_name,)
        )
    ]
    print(f"YouTube検索ソース開始: {backend} / {len(jobs)}キーワード")
    results = {}
    if jobs:
        def primary_search(query, now_factory=None):
            return youtube_search.search_youtube_ytdlp(
                query, now_factory=now_factory, filter_period=False
            )

        with ThreadPoolExecutor(max_workers=min(5, len(jobs))) as executor:
            futures = {
                executor.submit(
                    youtube_search_backends.run_search_backend,
                    backend,
                    query,
                    primary_search,
                    youtube_search.YTDLPVideo,
                ): query
                for _, query in jobs
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result()
    path = save_source_snapshot(backend, results, now=now)
    for query, result in results.items():
        print(
            f"source={backend} query={query!r} candidates={len(result['videos'])} "
            f"elapsed={result['elapsed']:.2f}s"
            + (f" error={result['error']}" if result["error"] else "")
        )
    print(f"YouTube検索候補を保存: {path}")
    return 0 if all(not result["error"] for result in results.values()) else 1
