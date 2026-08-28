import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import monitor_runner
import youtube_search
from runtime_utils import acquire_lock, run_locked


class SafetyTests(unittest.TestCase):
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

        with patch.object(youtube_search.gspread, "get_config_value", return_value="9999"), patch.object(
            youtube_search, "YoutubeDL", FakeYDL
        ):
            self.assertEqual(youtube_search.search_youtube("test"), [])
        self.assertEqual(FakeYDL.options["playlistend"], youtube_search.MAX_SEARCH_LIMIT)

    def test_direct_stage_skips_when_lock_is_held(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "monitor.lock"
            with acquire_lock(lock_path):
                called = []
                with patch.dict(os.environ, {}, clear=False):
                    self.assertEqual(run_locked(lambda: called.append(True), directory), 0)
                self.assertEqual(called, [])

    def test_runner_passes_lock_marker_to_children(self):
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
        self.assertEqual(calls[0][1]["env"]["PRICONNER_MONITOR_LOCK_HELD"], "1")


if __name__ == "__main__":
    unittest.main()
