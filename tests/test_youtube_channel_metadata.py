from types import SimpleNamespace
from unittest.mock import patch

import youtube_channel


def test_enrich_rss_video_fetches_description_and_tags():
    rss_video = SimpleNamespace(
        watch_url="https://www.youtube.com/watch?v=abc",
        title="RSS title",
        publish_date=None,
    )

    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download=False):
            assert url == rss_video.watch_url
            assert download is False
            return {
                "id": "abc",
                "webpage_url": url,
                "title": "Full title",
                "description": "1:00 キャラ→UB",
                "tags": ["プリコネ"],
                "timestamp": 1780000000,
            }

    with patch.object(youtube_channel, "YoutubeDL", FakeYDL):
        enriched = youtube_channel.enrich_rss_video(rss_video)

    assert enriched.title == "Full title"
    assert enriched.description == "1:00 キャラ→UB"
    assert enriched.tags == ["プリコネ"]
