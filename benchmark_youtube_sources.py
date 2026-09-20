"""Compare RSS discovery with yt-dlp for one or more public channel IDs."""
import argparse
import time

from yt_dlp import YoutubeDL

from youtube_rss import RSSChannel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("channel_ids", nargs="+", help="YouTube channel IDs")
    args = parser.parse_args()
    for channel_id in args.channel_ids:
        rss_start = time.perf_counter()
        rss_error = ""
        try:
            rss = RSSChannel(channel_id)
            rss_count = rss.entry_count
        except Exception as error:  # noqa: BLE001 - benchmark must report all source failures
            rss_count, rss_error = 0, f"{type(error).__name__}: {error}"
        rss_elapsed = time.perf_counter() - rss_start

        ytdlp_start = time.perf_counter()
        ytdlp_error = ""
        try:
            with YoutubeDL({"quiet": True, "skip_download": True,
                             "extract_flat": False, "playlist_items": "1-20",
                             "ignoreerrors": True, "socket_timeout": 15,
                             "retries": 1}) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/channel/{channel_id}/videos",
                    download=False,
                ) or {}
            ytdlp_count = len(info.get("entries") or [])
        except Exception as error:  # noqa: BLE001 - benchmark must report all source failures
            ytdlp_count, ytdlp_error = 0, f"{type(error).__name__}: {error}"
        ytdlp_elapsed = time.perf_counter() - ytdlp_start

        print(f"channel={channel_id}")
        print(f"  rss:   {rss_elapsed:.2f}s entries={rss_count} {rss_error}")
        print(f"  yt-dlp:{ytdlp_elapsed:.2f}s entries={ytdlp_count} {ytdlp_error}")
        if rss_count and ytdlp_count:
            print(f"  speedup: {ytdlp_elapsed / rss_elapsed:.1f}x")


if __name__ == "__main__":
    main()
