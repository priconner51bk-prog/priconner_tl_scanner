"""Shared persistence helpers for RSS and keyword YouTube collectors."""

from youtube_common import VIDEO_HEADERS


def ensure_video_headers(sheet_video):
    """Upgrade the arrivals tab to the replay-safe video schema."""
    rows = sheet_video.get_all_values()
    if not rows or rows[0] == VIDEO_HEADERS or not hasattr(sheet_video, "update"):
        return
    end_column = chr(ord("A") + len(VIDEO_HEADERS) - 1)
    sheet_video.update(
        f"A1:{end_column}1",
        [VIDEO_HEADERS],
        value_input_option="USER_ENTERED",
    )


def persist_rows(sheet_channel, sheet_video, channel_values, video_values):
    """Insert only new channel IDs and video URLs in one place."""
    ensure_video_headers(sheet_video)
    if channel_values:
        existing_channels = {
            row[1]
            for row in sheet_channel.get_all_values()[1:]
            if len(row) > 1 and row[1]
        }
        unique_channels = []
        seen_channels = set(existing_channels)
        for row in channel_values:
            if len(row) > 1 and row[1] and row[1] not in seen_channels:
                unique_channels.append(row)
                seen_channels.add(row[1])
        if unique_channels:
            sheet_channel.insert_rows(unique_channels, row=2)

    if video_values:
        existing_videos = {
            row[4]
            for row in sheet_video.get_all_values()[1:]
            if len(row) > 4 and row[4]
        }
        unique_videos = []
        seen_videos = set(existing_videos)
        for row in video_values:
            if len(row) > 4 and row[4] not in seen_videos:
                unique_videos.append(row)
                seen_videos.add(row[4])
        if unique_videos:
            sheet_video.insert_rows(unique_videos, row=2)
