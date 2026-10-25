from contextlib import nullcontext
from unittest.mock import patch

import youtube_storage
from youtube_common import VIDEO_HEADERS
from youtube_storage import ensure_video_headers, persist_rows


class FakeSheet:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    def get_all_values(self):
        return self.rows

    def update(self, range_name, values, **kwargs):
        self.updates.append((range_name, values, kwargs))

    def insert_rows(self, values, row=1, **_kwargs):
        self.rows[row - 1:row - 1] = values


def test_ensure_video_headers_upgrades_legacy_video_tab():
    sheet = FakeSheet([["チャンネル名", "チャンネルurl", "投稿日", "動画タイトル", "動画url"]])

    ensure_video_headers(sheet)

    assert sheet.updates == [("A1:I1", [VIDEO_HEADERS], {"value_input_option": "USER_ENTERED"})]


def test_ensure_video_headers_does_not_rewrite_current_schema():
    sheet = FakeSheet([VIDEO_HEADERS])

    ensure_video_headers(sheet)

    assert sheet.updates == []


def test_persist_rows_skips_duplicate_channel_id_and_url_in_existing_sheet():
    channel_sheet = FakeSheet([
        ["動画url", "チャンネルID", "チャンネル名", "チャンネルurl"],
        ["", "UCexisting", "existing", "https://www.youtube.com/channel/UCexisting"],
        ["", "legacy-id", "legacy", "https://www.youtube.com/@samehandle/"],
    ])
    video_sheet = FakeSheet([VIDEO_HEADERS])
    candidates = [
        ["", "UCexisting", "same id", "https://youtube.com/channel/UCexisting/"],
        ["", "different-id", "same url", "http://youtube.com/@SameHandle?feature=shared"],
        ["", "UCnew", "new", "https://www.youtube.com/channel/UCnew"],
    ]

    with patch.object(youtube_storage, "_locked_channel_registry", nullcontext):
        persist_rows(channel_sheet, video_sheet, candidates, [])

    assert [row[1] for row in channel_sheet.rows[1:]] == ["UCnew", "UCexisting", "legacy-id"]


def test_persist_rows_dedupes_channels_inside_candidate_batch_by_url():
    channel_sheet = FakeSheet([["動画url", "チャンネルID", "チャンネル名", "チャンネルurl"]])
    video_sheet = FakeSheet([VIDEO_HEADERS])
    candidates = [
        ["", "UCfirst", "first", "https://www.youtube.com/@SharedHandle"],
        ["", "UCsecond", "second", "http://youtube.com/@sharedhandle/"],
    ]

    with patch.object(youtube_storage, "_locked_channel_registry", nullcontext):
        persist_rows(channel_sheet, video_sheet, candidates, [])

    assert [row[1] for row in channel_sheet.rows[1:]] == ["UCfirst"]
