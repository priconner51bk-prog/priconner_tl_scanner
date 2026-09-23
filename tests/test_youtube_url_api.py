from unittest.mock import patch

import youtube_url_api
from youtube_common import (
    log_youtube_registration_results,
    post_new_url_to_experimental,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_register_url_returns_duplicate_status_and_sheet_row(monkeypatch):
    monkeypatch.setattr(youtube_url_api, "_api_token", lambda: "test-token")
    monkeypatch.setattr(youtube_url_api, "_api_url", lambda: "https://example.test/api")
    requests = []

    def post(url, **kwargs):
        requests.append((url, kwargs))
        return FakeResponse({
            "ok": True,
            "status": "already_registered",
            "registered": True,
            "row": 3,
        })

    result = youtube_url_api.register_url(
        "https://www.youtube.com/watch?v=abcdefghijk", post=post
    )

    assert result == {
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "status": "already_registered",
        "row": 3,
        "registered": True,
    }
    assert requests[0][0] == "https://example.test/api"
    assert requests[0][1]["json"] == {
        "token": "test-token",
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
    }


def test_only_main_sheet_unregistered_urls_are_selected_for_experimental_post():
    urls = log_youtube_registration_results([
        {"url": "registered", "status": "already_registered", "row": 3, "registered": True},
        {"url": "new", "status": "accepted", "row": 11, "registered": False},
        {"url": "queued", "status": "already_queued", "row": 12, "registered": False},
    ])

    assert urls == {"new", "queued"}


def test_experimental_post_targets_summary_channel_and_deduplicates_by_url():
    item = {"url": "https://www.youtube.com/watch?v=abcdefghijk", "summary_author": "配信者"}
    with patch("discord_utils.post_to_configured_guilds", return_value=["queued"]) as post:
        result = post_new_url_to_experimental(item)

    assert result == ["queued"]
    post.assert_called_once_with(
        "https://www.youtube.com/watch?v=abcdefghijk",
        channel_key="summary",
        guild_keys=["experimental"],
        dedupe_key="youtube-new-experimental:https://www.youtube.com/watch?v=abcdefghijk",
        summary_author="配信者",
    )
