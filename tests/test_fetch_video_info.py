"""Tests for discord_channel.fetch_video_info and sheets_maintenance apply path."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import discord_channel
import sheets_maintenance


class FakeYDL:
    def __init__(self, options):
        self.info = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, **kwargs):
        return self.info


def _fetch_with(info):
    ydl = FakeYDL({})
    ydl.info = info
    with patch.object(discord_channel, "YoutubeDL", lambda options: ydl):
        return discord_channel.fetch_video_info("https://www.youtube.com/watch?v=abc")


def test_fetch_video_info_prefers_timestamp():
    result = _fetch_with({"timestamp": 1700000000, "upload_date": "20230101", "title": "t", "channel": "c"})
    assert result["publish_date"] == datetime.fromtimestamp(1700000000, tz=timezone.utc)
    assert result["title"] == "t"
    assert result["channel_name"] == "c"


def test_fetch_video_info_parses_upload_date():
    result = _fetch_with({"upload_date": "20260101", "title": "t"})
    assert result["publish_date"] == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_fetch_video_info_none_when_no_dates():
    result = _fetch_with({"title": "t"})
    assert result["publish_date"] is None


def test_fetch_video_info_none_for_malformed_upload_date():
    result = _fetch_with({"upload_date": "bad", "title": "t"})
    assert result["publish_date"] is None


class FakeSheet:
    def __init__(self, rows):
        self.rows = rows
        self.updated = []

    def get_all_values(self):
        return self.rows

    def update(self, values, range_name, value_input_option="USER_ENTERED"):
        self.updated.append((values, range_name))


def test_maintain_flags_writes_changed_rows():
    channel_sheet = FakeSheet(
        [
            ["h"] * 7,
            ["", "c1", "name1", "url1", "", "", ""],
            ["", "c2", "name2", "url2", "", "", ""],
        ]
    )
    video_sheet = FakeSheet(
        [
            ["h"] * 5,
            ["name1", "url1", "2020/01/01 00:00:00", "old", "u1"],
            ["name2", "url2", "2026/09/01 00:00:00", "new", "u2"],
        ]
    )
    spreadsheet = SimpleNamespace(
        worksheet=lambda name: {
            "YouTubeチャンネル": channel_sheet,
            "YouTube動画": video_sheet,
        }[name]
    )

    changed = sheets_maintenance.maintain_youtube_channel_flags(
        spreadsheet=spreadsheet,
        now=datetime(2026, 9, 10, tzinfo=timezone.utc),
        inactive_days=90,
    )

    # c1 is inactive (last video 2020), c2 is recent (2026).
    assert len(changed) == 1
    assert channel_sheet.updated, "expected the sheet to be updated"
    values, range_name = channel_sheet.updated[0]
    assert range_name == "G2:G3"
    assert values[0][0] == sheets_maintenance.INACTIVE_MARKER


def test_maintain_flags_no_write_when_no_changes():
    channel_sheet = FakeSheet([["h"] * 7, ["", "c1", "name1", "url1", "", "", ""]])
    video_sheet = FakeSheet(
        [["h"] * 5, ["name1", "url1", "2026/09/01 00:00:00", "new", "u1"]]
    )
    spreadsheet = SimpleNamespace(
        worksheet=lambda name: {
            "YouTubeチャンネル": channel_sheet,
            "YouTube動画": video_sheet,
        }[name]
    )

    changed = sheets_maintenance.maintain_youtube_channel_flags(
        spreadsheet=spreadsheet,
        now=datetime(2026, 9, 10, tzinfo=timezone.utc),
        inactive_days=90,
    )

    assert changed == []
    assert channel_sheet.updated == []
