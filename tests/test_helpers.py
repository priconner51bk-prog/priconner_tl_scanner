"""Tests for the shared helper utilities used across the monitor stages."""

import gspread_utils
import new_arrivals_markdown


def test_split_ranges_groups_consecutive_indices():
    assert gspread_utils.splitRanges([2, 3, 4, 7, 8]) == [(2, 4), (7, 8)]


def test_split_ranges_single_and_empty():
    assert gspread_utils.splitRanges([5]) == [(5, 5)]
    assert gspread_utils.splitRanges([]) == []


def test_delete_empty_rows_deletes_contiguous_blank_rows():
    class FakeSheet:
        def __init__(self, rows):
            self.rows = rows
            self.deleted = []

        def get_all_values(self):
            return list(self.rows)

        def delete_rows(self, start, end):
            self.deleted.append((start, end))

    sheet = FakeSheet(
        [
            ["h", "h"],
            ["a", "b"],
            ["", ""],
            ["", ""],
            ["c", "d"],
        ]
    )
    gspread_utils.deleteEmptyRows(sheet)
    assert sheet.deleted == [(3, 4)]


def test_get_int_config_value_clamps_and_falls_back():
    original = gspread_utils.get_config_value
    try:
        gspread_utils.get_config_value = lambda section, key, fallback=None: fallback
        assert gspread_utils.get_int_config_value("s", "k", 5, minimum=1, maximum=10) == 5
        gspread_utils.get_config_value = lambda section, key, fallback=None: "not-an-int"
        assert gspread_utils.get_int_config_value("s", "k", 3, minimum=1, maximum=10) == 3
        gspread_utils.get_config_value = lambda section, key, fallback=None: "99"
        assert gspread_utils.get_int_config_value("s", "k", 5, minimum=1, maximum=10) == 10
    finally:
        gspread_utils.get_config_value = original


def test_safe_filename_sanitizes_and_truncates():
    assert new_arrivals_markdown._safe("a/b c") == "a_b_c"
    assert new_arrivals_markdown._safe("") == "item"
    assert new_arrivals_markdown._safe("x" * 100) == "x" * 80


def test_published_at_text_handles_datetime_and_string():
    from datetime import datetime, timezone

    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert (
        new_arrivals_markdown._published_at_text(dt)
        == "2026-01-01T00:00:00+00:00"
    )
    assert new_arrivals_markdown._published_at_text("2026-01-01") == "2026-01-01"
    assert new_arrivals_markdown._published_at_text(None) == ""
