"""Tests for the Discord webhook notification helpers."""

from unittest.mock import patch

import requests

import discord_utils


def _fake_response(status_code=200):
    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError("status")

    return FakeResponse(status_code)


def test_post_succeeds_on_first_try():
    with patch.object(discord_utils, "_bot_token", return_value="token"), patch.object(discord_utils, "channel_id", return_value="channel"), patch.object(
        discord_utils.requests, "post", return_value=_fake_response(200)
    ) as post:
        discord_utils.post("hello")
    assert post.call_count == 1


def test_post_retries_then_succeeds():
    calls = []

    def flaky_post(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise requests.RequestException("boom")
        return _fake_response(200)

    with patch.object(discord_utils, "_bot_token", return_value="token"), patch.object(discord_utils, "channel_id", return_value="channel"), patch.object(
        discord_utils.time, "sleep"
    ) as sleep, patch.object(discord_utils.requests, "post", side_effect=flaky_post):
        discord_utils.post("hello")
    assert len(calls) == 2
    assert sleep.call_count == 2


def test_post_raises_after_exhausting_retries():
    calls = []

    def failing_post(url, **kwargs):
        calls.append(url)
        raise requests.RequestException("boom")

    with patch.object(discord_utils, "_bot_token", return_value="token"), patch.object(discord_utils, "channel_id", return_value="channel"), patch.object(
        discord_utils.time, "sleep"
    ) as sleep, patch.object(discord_utils.requests, "post", side_effect=failing_post):
        try:
            discord_utils.post("hello")
        except requests.RequestException:
            pass
        else:
            raise AssertionError("expected RequestException")
    assert len(calls) == 3
    assert sleep.call_count == 2


def test_non_2xx_status_raises():
    with patch.object(discord_utils, "_bot_token", return_value="token"), patch.object(discord_utils, "channel_id", return_value="channel"), patch.object(
        discord_utils.time, "sleep"
    ), patch.object(discord_utils.requests, "post", return_value=_fake_response(500)):
        try:
            discord_utils.post("hello")
        except requests.HTTPError:
            pass
        else:
            raise AssertionError("expected HTTPError")


def test_bot_token_required():
    with patch.object(
        discord_utils.gspread_utils,
        "get_config_value",
        return_value="",
    ), patch.dict("os.environ", {"DISCORD_BOT_TOKEN": ""}, clear=False):
        try:
            discord_utils._bot_token()
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError")


def test_integer_config_clamps_minimum():
    original = discord_utils.gspread_utils.get_config_value
    try:
        discord_utils.gspread_utils.get_config_value = (
            lambda section, key, fallback=None: "0"
        )
        assert discord_utils._integer_config("s", "k", 5, minimum=1) == 1
        discord_utils.gspread_utils.get_config_value = (
            lambda section, key, fallback=None: "not-an-int"
        )
        assert discord_utils._integer_config("s", "k", 5) == 5
    finally:
        discord_utils.gspread_utils.get_config_value = original
