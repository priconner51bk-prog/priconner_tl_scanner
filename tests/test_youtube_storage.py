from youtube_storage import ensure_video_headers
from youtube_common import VIDEO_HEADERS


class FakeSheet:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    def get_all_values(self):
        return self.rows

    def update(self, range_name, values, **kwargs):
        self.updates.append((range_name, values, kwargs))


def test_ensure_video_headers_upgrades_legacy_video_tab():
    sheet = FakeSheet([["チャンネル名", "チャンネルurl", "投稿日", "動画タイトル", "動画url"]])

    ensure_video_headers(sheet)

    assert sheet.updates == [("A1:I1", [VIDEO_HEADERS], {"value_input_option": "USER_ENTERED"})]


def test_ensure_video_headers_does_not_rewrite_current_schema():
    sheet = FakeSheet([VIDEO_HEADERS])

    ensure_video_headers(sheet)

    assert sheet.updates == []
