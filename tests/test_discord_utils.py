"""Tests for the Discord webhook notification helpers."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import requests

import discord_utils


def _fake_response(status_code=200):
    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code
            self.text = ""

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError("status")

    return FakeResponse(status_code)


def test_forced_posts_route_default_guild_to_configured_test_guild():
    config = {
        "default_guild_key": "production",
        "test_guild_key": "experimental",
        "guilds": {"production": {}, "experimental": {}},
    }
    with patch.dict("os.environ", {"PRICONNER_FORCE_POST": "1"}, clear=False):
        assert discord_utils._resolve_guild_key(config, "default") == "experimental"


def test_post_succeeds_on_first_try():
    with patch.object(discord_utils, "_bot_token", return_value="token"), patch.object(discord_utils, "channel_id", return_value="channel"), patch.object(
        discord_utils.requests, "post", return_value=_fake_response(200)
    ) as post:
        discord_utils.post("hello")
    assert post.call_count == 1


def test_summary_mentions_configured_user_instead_of_here():
    with patch.object(discord_utils, "_bot_token", return_value="token"), patch.object(
        discord_utils, "channel_id", return_value="channel"
    ), patch.object(
        discord_utils.requests, "post", return_value=_fake_response(200)
    ) as post:
        discord_utils.notify_summary("summary")

    payload = post.call_args.kwargs["json"]
    assert payload["content"].startswith("<@1276185515799871595>\n")
    assert payload["allowed_mentions"] == {
        "parse": [],
        "users": ["1276185515799871595"],
    }


def test_notify_does_not_add_here_mention():
    with patch.object(discord_utils, "_bot_token", return_value="token"), patch.object(
        discord_utils, "channel_id", return_value="channel"
    ), patch.object(
        discord_utils.requests, "post", return_value=_fake_response(200)
    ) as post:
        discord_utils.notify("通知本文")

    assert post.call_args.kwargs["json"]["content"] == "通知本文"


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


def test_bot_token_explicitly_loads_local_env_before_reading_process_env():
    with patch.object(
        discord_utils.gspread_utils,
        "get_config_value",
        side_effect=lambda section, key, fallback=None: fallback,
    ), patch.object(
        discord_utils.gspread_utils,
        "load_local_env",
    ) as load_env, patch.dict(
        "os.environ", {"DISCORD_BOT_TOKEN": "env-token"}, clear=False
    ):
        assert discord_utils._bot_token() == "env-token"
    load_env.assert_called_once_with()


def test_summary_is_not_sent_to_boss_zero_channel():
    config = {
        "summary_channel_keys": {
            "production": "boss0_tl",
            "experimental": "summary",
        }
    }
    queued = []
    with patch.object(discord_utils, "_channel_config", return_value=config), patch.object(
        discord_utils,
        "notify_to_configured_guilds",
        side_effect=lambda *args, **kwargs: queued.append((args, kwargs)) or ["queued"],
    ):
        result = discord_utils.notify_summary_to_configured_guilds(
            "summary", guild_keys=["production", "experimental"]
        )

    assert result == ["queued"]
    assert len(queued) == 1
    assert queued[0][1]["channel_key"] == "summary"
    assert queued[0][1]["guild_keys"] == ["experimental"]


def test_load_local_env_does_not_override_existing_environment():
    with tempfile.TemporaryDirectory() as directory:
        env_path = Path(directory) / ".env"
        env_path.write_text(
            "DISCORD_BOT_TOKEN=dotenv-token\nOTHER_VALUE=loaded\n",
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "process-token"}, clear=False):
            discord_utils.gspread_utils.load_local_env(env_path)
            assert discord_utils.os.environ["DISCORD_BOT_TOKEN"] == "process-token"
            assert discord_utils.os.environ["OTHER_VALUE"] == "loaded"


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
