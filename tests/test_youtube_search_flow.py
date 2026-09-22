"""End-to-end test for the YouTube search orchestration flow."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import youtube_search


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

    def sort(self, *_args, **_kwargs):
        pass


def test_find_youtube_video_records_new_video_and_channel():
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    video = SimpleNamespace(
        watch_url="https://www.youtube.com/watch?v=new",
        title="プリコネ クラバト 4段階目 TL",
        description="Priconne TL video",
        tags=["tl"],
        publish_date=now - timedelta(hours=1),
        channel_id="ch1",
        channel_url="https://www.youtube.com/channel/ch1",
    )

    channel_sheet = FakeSheet([["h"] * 7, ["", "known", "name", "url", "", "", ""]])
    video_sheet = FakeSheet([["h"] * 5])
    boss_sheet = FakeSheet([["h"], ["BossA"]])
    spreadsheet = SimpleNamespace(
        worksheet=lambda name: {
            "YouTubeチャンネル": channel_sheet,
            "YouTube動画": video_sheet,
            "ボス名": boss_sheet,
        }[name]
    )

    with (
        patch.object(youtube_search.gspread, "get_int_config_value", return_value=7),
        patch.object(youtube_search.gspread, "get_config_value", return_value="days"),
    ):
        youtube_search.findYouTubeVideo(
            spreadsheet=spreadsheet,
            search_factory=lambda keywords: [video],
            channel_factory=lambda url: SimpleNamespace(channel_name="chan"),
            post=lambda *_args: None,
            notify=lambda *_args: None,
            write_urls=lambda *_args: None,
            sleep=lambda *_args: None,
            now_factory=lambda: now,
        )

    assert len(video_sheet.inserted) == 1
    assert video_sheet.inserted[0][4] == "https://www.youtube.com/watch?v=new"
    assert len(channel_sheet.inserted) == 1
    assert channel_sheet.inserted[0][1] == "ch1"


def test_find_youtube_video_processes_new_handoff_video_id():
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    video = SimpleNamespace(
        watch_url="https://www.youtube.com/watch?v=handoff",
        title="BossA Discord共有 TL",
        description="Priconne TL video",
        tags=["tl"],
        publish_date=now - timedelta(days=30),
        channel_name="chan",
        channel_id="ch-handoff",
        channel_url="https://www.youtube.com/channel/ch-handoff",
    )
    channel_sheet = FakeSheet([["h"] * 7])
    video_sheet = FakeSheet([["h"] * 5])
    boss_sheet = FakeSheet([["h"], ["BossA"]])
    spreadsheet = SimpleNamespace(
        worksheet=lambda name: {
            "YouTubeチャンネル": channel_sheet,
            "YouTube動画": video_sheet,
            "ボス名": boss_sheet,
        }[name]
    )
    acknowledged = []

    with (
        patch.object(youtube_search.youtube_handoff, "pending", return_value=[video.watch_url]),
        patch.object(youtube_search.youtube_handoff, "acknowledge", side_effect=lambda urls: acknowledged.extend(urls)),
        patch.object(youtube_search, "fetch_handoff_video", return_value=video),
        patch.object(youtube_search.gspread, "get_int_config_value", return_value=7),
        patch.object(youtube_search.gspread, "get_config_value", return_value="days"),
    ):
        youtube_search.findYouTubeVideo(
            spreadsheet=spreadsheet,
            search_factory=lambda _keywords: [],
            channel_factory=lambda _url: SimpleNamespace(channel_name="chan"),
            post=lambda *_args: None,
            notify=lambda *_args: None,
            write_urls=lambda *_args: None,
            sleep=lambda *_args: None,
            now_factory=lambda: now,
        )

    assert len(video_sheet.inserted) == 1
    assert video_sheet.inserted[0][4] == video.watch_url
    assert acknowledged == [video.watch_url]


def test_find_youtube_video_skips_irrelevant_and_known():
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    irrelevant = SimpleNamespace(
        watch_url="https://www.youtube.com/watch?v=other",
        title="Other game",
        description="Genshin content",
        tags=["genshin"],
        publish_date=now - timedelta(hours=1),
        channel_id="ch2",
        channel_url="https://www.youtube.com/channel/ch2",
    )
    known = SimpleNamespace(
        watch_url="https://www.youtube.com/watch?v=known",
        title="Priconne TL",
        description="Priconne TL video",
        tags=["tl"],
        publish_date=now - timedelta(hours=1),
        channel_id="ch3",
        channel_url="https://www.youtube.com/channel/ch3",
    )

    channel_sheet = FakeSheet([["h"] * 7, ["", "known", "name", "url", "", "", ""]])
    video_sheet = FakeSheet([["h"] * 5, ["c", "u", "d", "t", "https://www.youtube.com/watch?v=known"]])
    boss_sheet = FakeSheet([["h"], ["BossA"]])
    spreadsheet = SimpleNamespace(
        worksheet=lambda name: {
            "YouTubeチャンネル": channel_sheet,
            "YouTube動画": video_sheet,
            "ボス名": boss_sheet,
        }[name]
    )

    with patch.object(
        youtube_search.gspread, "get_int_config_value", return_value=7
    ):
        youtube_search.findYouTubeVideo(
            spreadsheet=spreadsheet,
            search_factory=lambda keywords: [irrelevant, known],
            channel_factory=lambda url: SimpleNamespace(channel_name="chan"),
            post=lambda *_args: None,
            notify=lambda *_args: None,
            write_urls=lambda *_args: None,
            sleep=lambda *_args: None,
        )

    assert video_sheet.inserted == []
    assert channel_sheet.inserted == []


def test_find_youtube_video_routes_summary_to_selected_boss():
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    video = SimpleNamespace(
        watch_url="https://www.youtube.com/watch?v=summary",
        title="BossA 4段階目 TL",
        description="BossA プリコネ TL",
        tags=["tl"],
        publish_date=now - timedelta(hours=1),
        channel_id="ch-summary",
        channel_url="https://www.youtube.com/channel/ch-summary",
    )
    channel_sheet = FakeSheet([["h"] * 7])
    video_sheet = FakeSheet([["h"] * 5])
    boss_sheet = FakeSheet([["h"], ["BossA"]])
    spreadsheet = SimpleNamespace(
        worksheet=lambda name: {
            "YouTubeチャンネル": channel_sheet,
            "YouTube動画": video_sheet,
            "ボス名": boss_sheet,
        }[name]
    )
    notified = []

    def notify(text, **kwargs):
        notified.append((text, kwargs))

    with (
        patch.dict("os.environ", {"PRICONNER_YOUTUBE_BOSS_INDEX": "1"}, clear=False),
        patch.object(youtube_search.gspread, "get_int_config_value", return_value=7),
        patch.object(youtube_search.gspread, "get_config_value", return_value="days"),
    ):
        youtube_search.findYouTubeVideo(
            spreadsheet=spreadsheet,
            search_factory=lambda keywords: [video],
            channel_factory=lambda url: SimpleNamespace(channel_name="chan"),
            post=lambda *_args, **_kwargs: None,
            notify=notify,
            write_urls=lambda *_args: None,
            sleep=lambda *_args: None,
            now_factory=lambda: now,
        )

    assert notified == []
