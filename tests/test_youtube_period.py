from datetime import datetime, timezone

from youtube_common import (
    effective_content_month,
    is_in_youtube_period,
    youtube_cutoff,
    youtube_period_bounds,
)


def test_current_month_cutoff_uses_japan_calendar_month():
    now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    assert youtube_cutoff(now, 7, "current_month").isoformat() == "2026-08-31T15:00:00+00:00"


def test_current_month_excludes_previous_month_and_includes_first_day():
    now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    assert is_in_youtube_period(datetime(2026, 8, 31, 14, 59, tzinfo=timezone.utc), now, 7, "current_month") is False
    assert is_in_youtube_period(datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc), now, 7, "current_month") is True


def test_period_defaults_to_current_month_when_mode_and_month_are_omitted():
    now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    start, end = youtube_period_bounds(now, 7)
    assert start.isoformat() == "2026-08-31T15:00:00+00:00"
    assert end == now


def test_content_month_defaults_to_jst_current_month_and_keeps_explicit_override():
    now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    assert effective_content_month("", "", now) == "2026-09"
    assert effective_content_month("", "2026-08", now) == "2026-08"
    assert effective_content_month("2026-07", "2026-08", now) == "2026-07"


def test_target_month_is_bounded_at_both_ends():
    now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    assert is_in_youtube_period(
        datetime(2026, 7, 31, 14, 59, tzinfo=timezone.utc),
        now,
        7,
        "target_month",
        "2026-08",
    ) is False
    assert is_in_youtube_period(
        datetime(2026, 8, 31, 14, 59, tzinfo=timezone.utc),
        now,
        7,
        "target_month",
        "2026-08",
    ) is True
    assert is_in_youtube_period(
        datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc),
        now,
        7,
        "target_month",
        "2026-08",
    ) is False
