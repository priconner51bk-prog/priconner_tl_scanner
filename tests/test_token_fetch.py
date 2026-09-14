"""Tests for the Discord token fetch helpers."""

from unittest.mock import patch

import requests

import tools.discord_token_fetch as token_fetch


def test_validate_returns_user_on_success():
    response = type("R", (), {"status_code": 200, "json": lambda self: {"id": "123"}})()
    with patch.object(token_fetch.requests, "get", return_value=response):
        user = token_fetch._validate("token")
    assert user == {"id": "123"}


def test_validate_returns_none_on_http_error():
    response = type("R", (), {"status_code": 401, "json": lambda self: {}})()
    with patch.object(token_fetch.requests, "get", return_value=response):
        assert token_fetch._validate("token") is None


def test_validate_returns_none_on_request_exception():
    with patch.object(
        token_fetch.requests,
        "get",
        side_effect=requests.RequestException("boom"),
    ):
        assert token_fetch._validate("token") is None


def test_test_channel_returns_false_when_unconfigured():
    with patch.object(token_fetch.dc, "_channel_ids", return_value=[]):
        assert token_fetch._test_channel("token") is False


def test_test_channel_returns_true_when_messages_readable():
    with patch.object(
        token_fetch.dc, "_channel_ids", return_value=["ch1"]
    ), patch.object(
        token_fetch.dc,
        "fetch_channel_messages",
        return_value=[{"content": "hello https://youtu.be/abc"}],
    ):
        assert token_fetch._test_channel("token") is True


def test_test_channel_returns_false_when_fetch_fails():
    with patch.object(
        token_fetch.dc, "_channel_ids", return_value=["ch1"]
    ), patch.object(
        token_fetch.dc, "fetch_channel_messages", return_value=None
    ):
        assert token_fetch._test_channel("token") is False
