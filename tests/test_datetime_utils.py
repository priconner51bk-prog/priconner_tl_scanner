"""Tests for the shared datetime helpers used by the monitor."""

import unittest
from datetime import datetime, timezone

import datetime_utils


class DateTimeUtilsTests(unittest.TestCase):
    def test_dateTime2String_uses_jst_for_naive_input(self):
        # A naive value is interpreted as JST and rendered in JST.
        self.assertEqual(
            datetime_utils.dateTime2String(datetime(2026, 1, 1, 0, 0, 0)),
            "2026/01/01 00:00:00",
        )

    def test_dateTime2String_converts_aware_to_jst(self):
        # 2025-12-31T15:00:00Z is 2026-01-01 00:00:00 JST.
        utc = datetime(2025, 12, 31, 15, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(
            datetime_utils.dateTime2String(utc),
            "2026/01/01 00:00:00",
        )

    def test_string2DateTime_round_trip(self):
        parsed = datetime_utils.string2DateTime("2026/01/01 00:00:00")
        self.assertEqual(parsed.tzinfo is not None, True)
        self.assertEqual(parsed, datetime(2025, 12, 31, 15, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(
            datetime_utils.dateTime2String(parsed),
            "2026/01/01 00:00:00",
        )

    def test_isoString2DateTime_returns_aware_utc(self):
        parsed = datetime_utils.isoString2DateTime("2026-01-01T00:00:00.000Z")
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed, datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc))

    def test_calcDate_subtracts_days_and_returns_utc(self):
        start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(datetime_utils.calcDate(start, 7), datetime(2025, 12, 25, 0, 0, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
