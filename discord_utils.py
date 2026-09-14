import json
import os
import re
from pathlib import Path
import time

import requests

import gspread_utils

DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 2
DEFAULT_API_INTERVAL = 1.0


def _integer_config(section, key, fallback, minimum=0):
    try:
        return max(minimum, int(gspread_utils.get_config_value(section, key, fallback)))
    except (TypeError, ValueError):
        return fallback


def _api_interval():
    try:
        return max(0.0, float(gspread_utils.get_config_value(
            "discord", "api_interval", DEFAULT_API_INTERVAL
        )))
    except (TypeError, ValueError):
        return DEFAULT_API_INTERVAL


def _bot_token():
    token = gspread_utils.get_config_value("discord", "bot_token", os.environ.get("DISCORD_BOT_TOKEN"))
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not configured")
    return token


def _channel_config():
    path = os.environ.get("DISCORD_CHANNELS_FILE", str(Path(__file__).with_name("discord_channels.json")))
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError as error:
        raise RuntimeError(f"Discord channel config is not configured: {path}") from error


def channel_id(guild_key, channel_key):
    config = _channel_config()
    guild = config.get("guilds", {}).get(guild_key)
    if not guild or not guild.get("guild_id"):
        raise RuntimeError(f"Discord guild is not configured: {guild_key}")
    channel = guild.get("channels", {}).get(channel_key)
    if not channel:
        raise RuntimeError(f"Discord channel is not configured: {guild_key}/{channel_key}")
    return channel


def _send(content, label, guild_key="default", channel_key="boss0_tl", files=None):
    timeout = _integer_config("discord", "timeout", DEFAULT_TIMEOUT, minimum=1)
    retries = _integer_config("discord", "retries", DEFAULT_RETRIES)
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                f"https://discord.com/api/v10/channels/{channel_id(guild_key, channel_key)}/messages",
                headers={"Authorization": f"Bot {_bot_token()}"},
                data={"content": content}, files=files, timeout=timeout
            )
            raise_for_status = getattr(response, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
            if not 200 <= response.status_code < 300:
                raise requests.HTTPError(
                    f"Discord webhook returned HTTP {response.status_code}"
                )
            print(label)
            time.sleep(_api_interval())
            return response
        except requests.RequestException as error:
            if attempt >= retries:
                print(f"失敗: Discord通知: {error}")
                raise
            time.sleep(2**attempt)


def notify(text, guild_key="default", channel_key="boss0_tl"):
    return _send(f"@here\n{text}", "通知送信成功", guild_key, channel_key)


def post(text, guild_key="default", channel_key="boss0_tl", files=None):
    return _send(text, "ポスト送信成功", guild_key, channel_key, files)


def boss_channel_key(code):
    """Map D10/D101/D530-style codes to boss0_tl..boss5_tl."""
    match = re.fullmatch(r"D([1-5])(?:\d{1,2}|T\d{2})?", str(code or "").strip().upper())
    if not match:
        return "boss0_tl"
    number = int(match.group(1))
    boss = int(match.group(1)[0]) if number else 0
    return f"boss{boss}_tl" if 1 <= boss <= 5 else "boss0_tl"


def post_for_boss(code, text, guild_key="default", files=None):
    """Post a TL to its configured boss channel; unknown codes go to boss0."""
    return post(text, guild_key=guild_key, channel_key=boss_channel_key(code), files=files)


def delete_worrychefs_posts(guild_key="default", channel_keys=None):
    """Delete only bot messages created by the WorryChefs notifier."""
    token = _bot_token()
    headers = {"Authorization": f"Bot {token}"}
    channel_keys = channel_keys or [f"boss{i}_tl" for i in range(1, 6)]
    deleted = 0
    for channel_key in channel_keys:
        channel = channel_id(guild_key, channel_key)
        before = None
        while True:
            params = {"limit": 100}
            if before:
                params["before"] = before
            response = requests.get(
                f"https://discord.com/api/v10/channels/{channel}/messages",
                headers=headers, params=params, timeout=_integer_config("discord", "timeout", DEFAULT_TIMEOUT, minimum=1),
            )
            response.raise_for_status()
            messages = response.json()
            if not messages:
                break
            for message in messages:
                if not message.get("author", {}).get("bot"):
                    continue
                if not str(message.get("content", "")).lstrip().startswith("[WorryChefs更新]"):
                    continue
                delete_response = requests.delete(
                    f"https://discord.com/api/v10/channels/{channel}/messages/{message['id']}",
                    headers=headers, timeout=DEFAULT_TIMEOUT,
                )
                if delete_response.status_code == 429:
                    retry_after = float(delete_response.headers.get("Retry-After", "1"))
                    time.sleep(retry_after)
                    delete_response = requests.delete(
                        f"https://discord.com/api/v10/channels/{channel}/messages/{message['id']}",
                        headers=headers, timeout=DEFAULT_TIMEOUT,
                    )
                delete_response.raise_for_status()
                deleted += 1
                time.sleep(_api_interval())
            if len(messages) < 100:
                break
            before = messages[-1]["id"]
    return deleted
