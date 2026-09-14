"""Tests for YTDLPVideo publish-date parsing in the search stage."""

from datetime import datetime, timezone

import youtube_search


def test_ytdlp_video_prefers_timestamp_over_upload_date():
    video = youtube_search.YTDLPVideo(
        {
            "id": "abc",
            "timestamp": 1700000000,
            "upload_date": "20230101",
        }
    )
    assert video.publish_date == datetime.fromtimestamp(1700000000, tz=timezone.utc)


def test_ytdlp_video_parses_upload_date():
    video = youtube_search.YTDLPVideo({"id": "abc", "upload_date": "20260101"})
    assert video.publish_date == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_ytdlp_video_none_when_dates_missing():
    video = youtube_search.YTDLPVideo({"id": "abc"})
    assert video.publish_date is None


def test_ytdlp_video_none_for_malformed_upload_date():
    video = youtube_search.YTDLPVideo({"id": "abc", "upload_date": "not-a-date"})
    assert video.publish_date is None


def test_ytdlp_video_requires_id():
    try:
        youtube_search.YTDLPVideo({"title": "no id"})
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing id")
