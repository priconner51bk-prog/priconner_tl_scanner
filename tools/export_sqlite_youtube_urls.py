"""Export YouTube URLs from sent Discord posts stored in the local SQLite queue.

The SQL query applies the requested period to ``sent_at`` before message
contents are read. By default, only URLs not already present in the dedicated
``Youtube`` worksheet are written to a plain text file. Pass ``--apply`` to register
those URLs with the spreadsheet API and post unregistered items to the
experimental server's summary channel.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import discord_channel
import discord_utils
import gspread_utils
import youtube_handoff
import youtube_url_api
from discord_queue import queue_path


JST = ZoneInfo("Asia/Tokyo")
DEFAULT_START = "2026-09-22T12:00:00+09:00"
DEFAULT_END = "2026-09-30T00:00:00+09:00"
DEFAULT_OUTPUT = ROOT / "youtube_urls_in_period.txt"


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("日時にはタイムゾーンを付けてください")
    return parsed


def _read_sent_posts(db_path: Path, start: datetime, end: datetime):
    uri = f"file:{quote(db_path.resolve().as_posix(), safe='/:')}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT guild_key, channel_key, content, sent_at
            FROM discord_queue
            WHERE status = 'sent'
              AND kind = 'post'
              AND sent_at >= ?
              AND sent_at < ?
              AND guild_key IN ('production', 'experimental')
            ORDER BY sent_at, id
            """,
            (start.timestamp(), end.timestamp()),
        ).fetchall()


def _registered_video_ids():
    sheet = gspread_utils.getDamagesSheet()
    # The Apps Script API registers into the dedicated Youtube worksheet.
    # URLs in the damage table are source records, not registration state.
    urls = sheet.worksheet("Youtube").col_values(1)[1:]
    return {
        youtube_handoff.video_id(url)
        for url in urls
        if youtube_handoff.video_id(url)
    }


def collect_candidates(db_path: Path, start: datetime, end: datetime):
    if start >= end:
        raise ValueError("開始日時は終了日時より前にしてください")

    posts = _read_sent_posts(db_path, start, end)
    known_ids = _registered_video_ids()
    already_in_new_arrivals = set()
    candidates = {}

    for row in posts:
        urls = discord_channel.extract_youtube_urls({"content": row["content"]})
        for url in urls:
            video_id = youtube_handoff.video_id(url)
            if not video_id:
                continue
            item = {
                "url": url,
                "video_id": video_id,
                "content": row["content"],
                "guild_key": row["guild_key"],
                "channel_key": row["channel_key"],
                "sent_at": datetime.fromtimestamp(row["sent_at"], JST),
            }
            if item["guild_key"] == "experimental" and item["channel_key"] == "summary":
                already_in_new_arrivals.add(video_id)
            candidates.setdefault(video_id, item)

    return [
        item for video_id, item in candidates.items()
        if video_id not in known_ids
    ], already_in_new_arrivals, len(posts)


def write_urls(output: Path, candidates, start: datetime, end: datetime):
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# SQLite sent_at period: {start.isoformat()} <= sent_at < {end.isoformat()}",
        "# URLs absent from the visible Youtube tab; the Apps Script API is authoritative.",
        "# Lines beginning with # are comments.",
        *(item["url"] for item in candidates),
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _selected_video_ids(path: Path):
    urls = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return {
        video_id
        for url in urls
        if (video_id := youtube_handoff.video_id(url))
    }


def apply_candidates(candidates, already_in_new_arrivals):
    counts = {"api_checked": 0, "registered": 0, "posted": 0, "already_posted": 0, "errors": 0}
    for item in candidates:
        result = youtube_url_api.register_urls([item["url"]])[0]
        counts["api_checked"] += 1
        status = result.get("status")
        if status == "error":
            counts["errors"] += 1
            print(f"API error: {item['video_id']}", flush=True)
            continue
        if result.get("registered"):
            counts["registered"] += 1
            print(f"Already registered: {item['video_id']} row={result.get('row')}", flush=True)
            continue
        if item["video_id"] in already_in_new_arrivals:
            counts["already_posted"] += 1
            print(f"Already in 新着tl情報: {item['video_id']}", flush=True)
            continue
        try:
            response = discord_utils.post(
                item["content"], guild_key="experimental", channel_key="summary"
            )
            if response is False:
                raise RuntimeError("Discord returned False")
            counts["posted"] += 1
            print(f"Posted: {item['video_id']}", flush=True)
        except Exception as error:  # noqa: BLE001 - report each failed external operation
            counts["errors"] += 1
            print(f"Discord post error: {item['video_id']} ({type(error).__name__})", flush=True)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=queue_path(), help="SQLite queue database path")
    parser.add_argument("--start", type=_parse_datetime, default=_parse_datetime(DEFAULT_START))
    parser.add_argument("--end", type=_parse_datetime, default=_parse_datetime(DEFAULT_END))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--selected-file",
        type=Path,
        help="Optional URL list to limit export and --apply to selected video IDs",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Register exported candidates through the sheet API and post unregistered URLs",
    )
    args = parser.parse_args(argv)

    candidates, already_posted, scanned_posts = collect_candidates(
        args.db, args.start, args.end
    )
    if args.selected_file:
        selected_ids = _selected_video_ids(args.selected_file)
        candidates = [item for item in candidates if item["video_id"] in selected_ids]
        found_ids = {item["video_id"] for item in candidates}
        print(
            f"選択ファイルURL数={len(selected_ids)}, "
            f"期間内・未登録の一致={len(found_ids)}, "
            f"対象外または未登録でないURL={len(selected_ids - found_ids)}"
        )
    write_urls(args.output, candidates, args.start, args.end)
    print(
        f"SQLite期間内の送信済み投稿={scanned_posts}, "
        f"シート未登録候補={len(candidates)}, "
        f"新着tl情報に既投稿={len(already_posted)}"
    )
    print(f"出力: {args.output.resolve()}")

    if args.apply:
        counts = apply_candidates(candidates, already_posted)
        print("処理結果: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    else:
        print("API登録・Discord投稿は行っていません。実行する場合は --apply を指定してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
