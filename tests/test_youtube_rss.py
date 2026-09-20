from datetime import timezone

import pytest

from youtube_rss import RSSChannel


FEED = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
<title>Example Channel</title><entry><yt:videoId>abc123</yt:videoId><title>Hello</title><published>2026-09-20T00:00:00+00:00</published></entry></feed>'''


class Response:
    content = FEED

    def raise_for_status(self):
        pass


class Session:
    def get(self, url, timeout):
        assert "channel_id=UC123" in url
        assert timeout == 7
        return Response()


def test_rss_channel_maps_uploads_to_scanner_shape():
    channel = RSSChannel("UC123", timeout=7, session=Session())
    assert channel.channel_name == "Example Channel"
    assert channel.entry_count == 1
    assert channel.videos[0].watch_url.endswith("abc123")
    assert channel.videos[0].publish_date.tzinfo == timezone.utc


def test_rss_channel_skips_entries_without_video_id():
    class EmptySession(Session):
        def get(self, url, timeout):
            return type("R", (), {"content": b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>x</title></entry></feed>', "raise_for_status": lambda self: None})()
    assert RSSChannel("UC123", session=EmptySession()).videos == []


def test_rss_channel_retries_rate_limit_with_retry_after():
    class LimitedSession:
        def __init__(self):
            self.calls = 0

        def get(self, url, timeout):
            self.calls += 1
            if self.calls == 1:
                return type("R", (), {"status_code": 429, "headers": {"Retry-After": "0"}, "content": b"", "raise_for_status": lambda self: None})()
            return Response()

    session = LimitedSession()
    RSSChannel("UC123", session=session, sleep=lambda _: None)
    assert session.calls == 2
