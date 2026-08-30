import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import monitor_runner
import scheduled_monitor
import sheets_maintenance
import video_relevance
import youtube_channel
import youtube_search
import boss_names_sync
from runtime_utils import acquire_lock, run_locked


class SafetyTests(unittest.TestCase):
    def test_monthly_boss_name_rotation(self):
        self.assertEqual(boss_names_sync.battle_code_for_month(8), "4019")

    def test_boss_sync_does_not_write_before_source_is_ready(self):
        sheet = SimpleNamespace(update=unittest.mock.Mock())
        spreadsheet = SimpleNamespace(worksheet=lambda _name: sheet)
        self.assertFalse(
            boss_names_sync.sync_boss_names(
                spreadsheet=spreadsheet,
                now=datetime(2026, 8, 23),
                fetch=lambda: '..., "ゴブリングレート", 0, 401901101,',
            )
        )
        sheet.update.assert_not_called()

    def test_boss_sync_writes_fixed_list_when_source_is_ready(self):
        sheet = SimpleNamespace(update=unittest.mock.Mock())
        spreadsheet = SimpleNamespace(worksheet=lambda _name: sheet)
        self.assertTrue(
            boss_names_sync.sync_boss_names(
                spreadsheet=spreadsheet,
                now=datetime(2026, 8, 23),
                fetch=lambda: (
                    '..., "ゴブリングレート", 0, 401901101,\n'
                    '..., "ライライ", 0, 401901102,\n'
                    '..., "ムーバ", 0, 401901103,\n'
                    '..., "ネプテリオン", 0, 401901104,\n'
                    '..., "ネプテリオンA", 0, 401901105,\n'
                    '..., "ネプテリオンB", 0, 401901106,\n'
                    '..., "ネプテリオンC", 0, 401901107,\n'
                    '..., "アクアリオス", 0, 401901108,\n'
                ),
            )
        )
        sheet.update.assert_called_once()
        values, range_name = sheet.update.call_args.args[:2]
        self.assertEqual(range_name, "A2:A6")
        self.assertEqual(values, [["ゴブリングレート"], ["ライライ"], ["ムーバ"], ["ネプテリオン"], ["アクアリオス"]])

    def test_scheduled_monitor_excludes_month_end_and_handles_february(self):
        self.assertTrue(scheduled_monitor.is_monitor_day(datetime(2025, 2, 20).date()))
        self.assertTrue(scheduled_monitor.is_monitor_day(datetime(2025, 2, 27).date()))
        self.assertFalse(scheduled_monitor.is_monitor_day(datetime(2025, 2, 28).date()))
        self.assertTrue(scheduled_monitor.is_monitor_day(datetime(2024, 2, 21).date()))
        self.assertTrue(scheduled_monitor.is_monitor_day(datetime(2024, 2, 28).date()))
        self.assertFalse(scheduled_monitor.is_monitor_day(datetime(2024, 2, 29).date()))

    def test_scheduled_monitor_uses_previous_eight_days_for_30_and_31_day_months(self):
        self.assertTrue(scheduled_monitor.is_monitor_day(datetime(2026, 10, 23).date()))
        self.assertTrue(scheduled_monitor.is_monitor_day(datetime(2026, 10, 30).date()))
        self.assertFalse(scheduled_monitor.is_monitor_day(datetime(2026, 10, 31).date()))
        self.assertTrue(scheduled_monitor.is_monitor_day(datetime(2026, 4, 22).date()))
        self.assertTrue(scheduled_monitor.is_monitor_day(datetime(2026, 4, 29).date()))
        self.assertFalse(scheduled_monitor.is_monitor_day(datetime(2026, 4, 30).date()))

    def test_video_relevance_uses_description_and_rejects_other_games(self):
        relevant = SimpleNamespace(
            title="4段階目 TL",
            description="プリコネ クランバトルの編成と持越し手順",
            tags=[],
        )
        other_game = SimpleNamespace(
            title="新イベント攻略",
            description="ブルーアーカイブの攻略動画です",
            tags=[],
        )
        self.assertGreaterEqual(video_relevance.relevance_score(relevant), 4)
        self.assertTrue(video_relevance.is_relevant_video(relevant))
        self.assertFalse(video_relevance.is_relevant_video(other_game))

    def test_maintenance_marks_old_channels_and_reactivates_recent_ones(self):
        channel_rows = [
            ["header"] * 7,
            ["", "old", "old", "old-url", "", "", ""],
            [
                "",
                "recent",
                "recent",
                "recent-url",
                "",
                "",
                sheets_maintenance.INACTIVE_MARKER,
            ],
            ["", "manual", "manual", "manual-url", "", "", "手動除外"],
            ["", "unknown", "unknown", "unknown-url", "", "", ""],
        ]
        video_rows = [
            ["header"] * 5,
            ["old", "old-url", "2026/04/01 00:00:00", "", ""],
            ["recent", "recent-url", "2026/08/01 00:00:00", "", ""],
            ["manual", "manual-url", "2026/04/01 00:00:00", "", ""],
        ]
        flags, changed = sheets_maintenance.calculate_channel_flags(
            channel_rows,
            video_rows,
            now=datetime(2026, 8, 29, tzinfo=timezone.utc),
        )
        self.assertEqual(
            flags,
            [[sheets_maintenance.INACTIVE_MARKER], [""], ["手動除外"], [""]],
        )
        self.assertEqual(changed[0][0], 2)
        self.assertEqual(changed[0][2], sheets_maintenance.INACTIVE_MARKER)
        self.assertEqual(changed[1][0], 3)

    def test_channel_extraction_ignores_unavailable_videos(self):
        captured = {}

        class FakeYDL:
            def __init__(self, options):
                captured.update(options)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, *_args, **_kwargs):
                return {"channel": "test", "entries": [None] * 20}

        with patch.object(youtube_channel, "YoutubeDL", FakeYDL):
            channel = youtube_channel.YTDLPChannel("https://example.test/channel")

        self.assertTrue(captured["ignoreerrors"])
        self.assertEqual(channel.videos, [])
        self.assertEqual(captured["playliststart"], 1)
        self.assertEqual(captured["playlistend"], youtube_channel.DEFAULT_CHANNEL_LIMIT)
        self.assertEqual(captured["playlist_items"], "1-20")

    def test_channel_extraction_requests_each_page_as_a_flat_20_item_slice(self):
        captured = {}

        class FakeYDL:
            def __init__(self, options):
                captured.update(options)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, *_args, **_kwargs):
                return {"channel": "test", "entries": []}

        with patch.object(youtube_channel, "YoutubeDL", FakeYDL):
            youtube_channel.YTDLPChannel("https://example.test/channel", playlist_start=21)

        self.assertTrue(captured["extract_flat"])
        self.assertEqual(captured["playlist_items"], "21-40")
        self.assertEqual(captured["playliststart"], 21)
        self.assertEqual(captured["playlistend"], 40)

    def test_channel_video_uses_flat_entry_timestamp(self):
        video = youtube_channel.YTDLPVideo(
            {"id": "abc", "timestamp": 1787961600, "title": "test"}
        )
        self.assertEqual(video.publish_date.tzinfo, timezone.utc)

    def test_channel_listing_uses_fast_uploads_feed(self):
        captured = {}

        class FakeYDL:
            def __init__(self, options):
                captured.update(options)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, **_kwargs):
                captured["url"] = url
                return {"channel": "test", "entries": []}

        with patch.object(youtube_channel, "YoutubeDL", FakeYDL):
            youtube_channel.YTDLPChannel("https://example.test/channel/")

        self.assertEqual(captured["url"], "https://example.test/channel/videos")
        self.assertTrue(captured["extract_flat"])

    def test_registered_channel_accepts_video_without_relevance_terms(self):
        class FakeSheet:
            def __init__(self, rows):
                self.rows = rows
                self.updated = []
                self.inserted = []

            def get_all_values(self):
                return self.rows

            def col_values(self, column):
                return [row[column - 1] for row in self.rows]

            def update(self, values, *_args, **_kwargs):
                self.updated.append(values)

            def insert_rows(self, values, **_kwargs):
                self.inserted.extend(values)

            def cell(self, row, column):
                return SimpleNamespace(address=f"{chr(64 + column)}{row}")

            def sort(self, *_args, **_kwargs):
                pass

        now = datetime(2026, 8, 29, tzinfo=timezone.utc)
        video = SimpleNamespace(
            watch_url="https://www.youtube.com/watch?v=new",
            title="雑談だけのタイトル",
            publish_date=now - timedelta(hours=1),
        )

        class FakeChannel:
            videos = [video]
            entry_count = 1

        channel_sheet = FakeSheet(
            [["h"] * 7, ["", "channel", "name", "url", "", "", ""]]
        )
        video_sheet = FakeSheet([["h"] * 5])
        boss_sheet = FakeSheet([["h"]])
        spreadsheet = SimpleNamespace(
            worksheet=lambda name: {
                "YouTubeチャンネル": channel_sheet,
                "YouTube動画": video_sheet,
                "ボス名": boss_sheet,
            }[name]
        )

        with patch.object(
            youtube_channel.gspread, "get_config_value", return_value="7"
        ):
            youtube_channel.checkNewArrivalsForYouTube(
                spreadsheet=spreadsheet,
                channel_factory=lambda *_args, **_kwargs: FakeChannel(),
                post=lambda *_args: None,
                notify=lambda *_args: None,
                write_urls=lambda *_args: None,
                sleep=lambda *_args: None,
                now_factory=lambda: now,
            )

        self.assertEqual(len(video_sheet.inserted), 1)
        self.assertEqual(video_sheet.inserted[0][4], video.watch_url)

    def test_registered_channel_normalizes_naive_publish_datetime(self):
        class FakeSheet:
            def __init__(self, rows):
                self.rows = rows
                self.inserted = []

            def get_all_values(self):
                return self.rows

            def col_values(self, column):
                return [row[column - 1] for row in self.rows]

            def insert_rows(self, values, **_kwargs):
                self.inserted.extend(values)

            def update(self, *_args, **_kwargs):
                pass

            def cell(self, row, column):
                return SimpleNamespace(address=f"{chr(64 + column)}{row}")

            def sort(self, *_args, **_kwargs):
                pass

        video = SimpleNamespace(
            watch_url="https://www.youtube.com/watch?v=naive",
            title="naive date",
            publish_date=datetime(2026, 8, 29, 11, 0),
        )
        channel_sheet = FakeSheet([["h"] * 7, ["", "channel", "name", "url", "", "", ""]])
        video_sheet = FakeSheet([["h"] * 5])
        spreadsheet = SimpleNamespace(
            worksheet=lambda name: {
                "YouTubeチャンネル": channel_sheet,
                "YouTube動画": video_sheet,
            }[name]
        )

        with patch.object(youtube_channel.gspread, "get_config_value", return_value="7"):
            youtube_channel.checkNewArrivalsForYouTube(
                spreadsheet=spreadsheet,
                channel_factory=lambda *_args, **_kwargs: SimpleNamespace(
                    videos=[video], entry_count=1
                ),
                post=lambda *_args: None,
                notify=lambda *_args: None,
                write_urls=lambda *_args: None,
                sleep=lambda *_args: None,
                now_factory=lambda: datetime(2026, 8, 29, 12, 0),
            )

        self.assertEqual(len(video_sheet.inserted), 1)
        self.assertEqual(video_sheet.inserted[0][4], video.watch_url)

    def test_parse_stages_removes_duplicate_stage_names(self):
        self.assertEqual(
            monitor_runner.parse_stages("youtube-search,youtube-search,worrychefs"),
            ("youtube-search", "worrychefs"),
        )

    def test_period_days_argument_is_respected(self):
        now = datetime(2026, 8, 29, tzinfo=timezone.utc)
        video = SimpleNamespace(publish_date=now - timedelta(days=10))
        self.assertTrue(youtube_search.is_recent_video(video, now, period_days=14))
        self.assertFalse(youtube_search.is_recent_video(video, now, period_days=7))

    def test_search_limit_is_capped_before_ytdlp_is_called(self):
        class FakeYDL:
            options = None

            def __init__(self, options):
                self.options = options
                FakeYDL.options = options

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, *_args, **_kwargs):
                return {"entries": []}

        with (
            patch.object(
                youtube_search.gspread, "get_config_value", return_value="9999"
            ),
            patch.object(youtube_search, "YoutubeDL", FakeYDL),
        ):
            self.assertEqual(youtube_search.search_youtube("test"), [])
        self.assertEqual(
            FakeYDL.options["playlistend"], youtube_search.MAX_SEARCH_LIMIT
        )

    def test_search_normalizes_timestamp_and_deduplicates_results(self):
        class FakeYDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, *_args, **_kwargs):
                return {
                    "entries": [
                        {
                            "id": "abc",
                            "webpage_url": "https://www.youtube.com/watch?v=abc",
                            "channel_id": "channel",
                            "timestamp": 1787961600,
                            "title": "first",
                        },
                        {
                            "id": "abc",
                            "webpage_url": "https://www.youtube.com/watch?v=abc",
                            "channel_id": "channel",
                            "timestamp": 1787961600,
                            "title": "duplicate",
                        },
                        {"title": "malformed"},
                    ]
                }

        now = datetime(2026, 8, 29, tzinfo=timezone.utc)
        with (
            patch.object(youtube_search.gspread, "get_config_value", return_value="20"),
            patch.object(youtube_search, "YoutubeDL", FakeYDL),
        ):
            videos = youtube_search.search_youtube("test", now_factory=lambda: now)

        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0].publish_date.tzinfo, timezone.utc)

    def test_stage_lock_name_can_be_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "youtube_search.lock"
            with acquire_lock(lock_path):
                called = []
                with patch.dict(os.environ, {}, clear=False):
                    self.assertEqual(
                        run_locked(
                            lambda: called.append(True),
                            directory,
                            "youtube_search.lock",
                        ),
                        0,
                    )
                self.assertEqual(called, [])

    def test_runner_passes_runtime_directory_to_children(self):
        calls = []

        def fake_runner(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as directory:
            result = monitor_runner.run_stages(
                ("youtube-search",),
                directory,
                command_runner=fake_runner,
                root_dir=Path.cwd(),
            )
        self.assertEqual(result, 0)
        self.assertEqual(calls[0][1]["env"]["PRICONNER_MONITOR_RUNTIME_DIR"], directory)


if __name__ == "__main__":
    unittest.main()
