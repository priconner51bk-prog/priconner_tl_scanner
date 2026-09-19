import json
import os
import re
import time
from pathlib import Path

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
    # Keep token initialization explicit instead of relying only on the
    # import-time bootstrap in gspread_utils.  This also covers long-lived
    # workers where .env is created or mounted after module import.
    gspread_utils.load_local_env()
    token = gspread_utils.get_config_value("discord", "bot_token", os.environ.get("DISCORD_BOT_TOKEN"))
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not configured")
    return token


def _bot_user_id(token=None):
    """Return the authenticated bot ID for safe, owner-only cleanup."""
    token = token or _bot_token()
    response = requests.get(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {token}"},
        timeout=_integer_config("discord", "timeout", DEFAULT_TIMEOUT, minimum=1),
    )
    response.raise_for_status()
    user_id = str(response.json().get("id", "")).strip()
    if not user_id:
        raise RuntimeError("Discord bot user ID is unavailable")
    return user_id


def _channel_config():
    path = os.environ.get("DISCORD_CHANNELS_FILE", str(Path(__file__).with_name("discord_channels.json")))
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError as error:
        raise RuntimeError(f"Discord channel config is not configured: {path}") from error


def _resolve_guild_key(config, guild_key):
    if guild_key != "default":
        return guild_key
    guilds = config.get("guilds", {})
    if _is_test_post():
        test_guild = config.get("test_guild_key")
        if test_guild in guilds:
            return test_guild
    configured_default = config.get("default_guild_key")
    if configured_default in guilds:
        return configured_default
    if "production" in guilds:
        return "production"
    return "default"


def _is_test_post():
    def enabled(name):
        return os.environ.get(name, "").strip().lower() not in {"", "0", "false", "no"}

    return any(
        enabled(name)
        for name in (
            "PRICONNER_TEST_POST",
            "PRICONNER_FORCE_POST",
            "PRICONNER_FORCE_NEW_POST",
            "PRICONNER_FORCE_NEW_LIMIT_ONE",
            "PRICONNER_FORCE_POST_LIMIT",
            "PRICONNER_RESET_POSTS",
        )
    )


def configured_guild_keys(test_only=None):
    """Return guilds that may receive the current run's Discord posts."""
    config = _channel_config()
    if test_only is None:
        test_only = _is_test_post()
    if test_only:
        key = config.get("test_guild_key", "experimental")
        return [key] if key in config.get("guilds", {}) else []

    configured = config.get("scheduled_guild_keys") or []
    if configured:
        return [key for key in configured if key in config.get("guilds", {})]
    default_key = _resolve_guild_key(config, "default")
    return [default_key] if default_key in config.get("guilds", {}) else []


def channel_id(guild_key, channel_key):
    config = _channel_config()
    resolved_key = _resolve_guild_key(config, guild_key)
    guild = config.get("guilds", {}).get(resolved_key)
    if not guild or not guild.get("guild_id"):
        raise RuntimeError(f"Discord guild is not configured: {resolved_key}")
    channel = guild.get("channels", {}).get(channel_key)
    if not channel:
        raise RuntimeError(f"Discord channel is not configured: {resolved_key}/{channel_key}")
    return channel


def _send(content, label, guild_key="default", channel_key="boss0_tl", files=None):
    timeout = _integer_config("discord", "timeout", DEFAULT_TIMEOUT, minimum=1)
    retries = _integer_config("discord", "retries", DEFAULT_RETRIES)
    for attempt in range(retries + 1):
        try:
            payload = {
                "content": content,
                "allowed_mentions": {"parse": []},
            }
            request_kwargs = {"timeout": timeout}
            if files:
                request_kwargs.update(
                    data={"payload_json": json.dumps(payload)}, files=files
                )
            else:
                request_kwargs["json"] = payload
            response = requests.post(
                f"https://discord.com/api/v10/channels/{channel_id(guild_key, channel_key)}/messages",
                headers={"Authorization": f"Bot {_bot_token()}"},
                **request_kwargs,
            )
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", "1"))
                if attempt >= retries:
                    response.raise_for_status()
                time.sleep(max(1.0, retry_after))
                continue
            if not 200 <= response.status_code < 300:
                detail = str(getattr(response, "text", "") or "").strip()
                if len(detail) > 500:
                    detail = detail[:500]
                raise requests.HTTPError(
                    f"Discord API returned HTTP {response.status_code}"
                    + (f": {detail}" if detail else "")
                )
            raise_for_status = getattr(response, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
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


def post_to_configured_guilds(
    text, channel_key="boss0_tl", files=None, guild_keys=None
):
    from discord_queue import enqueue_for_guilds

    return enqueue_for_guilds(
        text,
        channel_key=channel_key,
        guild_keys=guild_keys if guild_keys is not None else configured_guild_keys(),
        kind="post",
        files=files,
    )


def notify_to_configured_guilds(text, channel_key="boss0_tl", guild_keys=None):
    from discord_queue import enqueue_for_guilds

    return enqueue_for_guilds(
        text,
        channel_key=channel_key,
        guild_keys=guild_keys if guild_keys is not None else configured_guild_keys(),
        kind="notify",
        dedupe_key=None,
    )


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


def post_for_boss_to_configured_guilds(code, text, files=None, guild_keys=None):
    return post_to_configured_guilds(
        text,
        channel_key=boss_channel_key(code),
        files=files,
        guild_keys=guild_keys,
    )


def delete_worrychefs_posts(guild_key="default", channel_keys=None):
    """Delete only WorryChefs messages created by this authenticated bot."""
    token = _bot_token()
    headers = {"Authorization": f"Bot {token}"}
    own_bot_id = _bot_user_id(token)
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
                if str(message.get("author", {}).get("id", "")) != own_bot_id:
                    continue
                message_content = str(message.get("content", "")).lstrip()
                is_worrychefs = message_content.startswith(
                    (
                        "[WorryChefs更新]",
                        "[WorryChefs更新 続き]",
                        "キャラ名       ⚔️     ⭐     UE",
                    )
                )
                if not is_worrychefs:
                    continue
                delete_url = f"https://discord.com/api/v10/channels/{channel}/messages/{message['id']}"
                delete_response = None
                for attempt in range(DEFAULT_RETRIES + 1):
                    try:
                        delete_response = requests.delete(
                            delete_url, headers=headers, timeout=DEFAULT_TIMEOUT,
                        )
                        if delete_response.status_code == 404:
                            # Another cleanup pass may have removed it already.
                            break
                        if delete_response.status_code == 429:
                            retry_after = float(delete_response.headers.get("Retry-After", "1"))
                            time.sleep(max(1.0, retry_after))
                            continue
                        delete_response.raise_for_status()
                        break
                    except requests.RequestException:
                        if attempt >= DEFAULT_RETRIES:
                            raise
                        time.sleep(2 ** attempt)
                deleted += 1
                time.sleep(_api_interval())
            if len(messages) < 100:
                break
            before = messages[-1]["id"]
    return deleted


def delete_bot_messages(guild_key="default", channel_keys=None):
    """Delete every message authored by this bot in the selected channels.

    This is intentionally owner-only: messages from human users or other bots
    are never touched. It is used for resetting isolated experimental channels
    before a full Discord-collection republish.
    """
    token = _bot_token()
    headers = {"Authorization": f"Bot {token}"}
    own_bot_id = _bot_user_id(token)
    channel_keys = channel_keys or []
    deleted = 0
    timeout = _integer_config("discord", "timeout", DEFAULT_TIMEOUT, minimum=1)
    for channel_key in channel_keys:
        channel = channel_id(guild_key, channel_key)
        before = None
        while True:
            params = {"limit": 100}
            if before:
                params["before"] = before
            for attempt in range(DEFAULT_RETRIES + 1):
                response = requests.get(
                    f"https://discord.com/api/v10/channels/{channel}/messages",
                    headers=headers, params=params, timeout=timeout,
                )
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    if attempt >= DEFAULT_RETRIES:
                        response.raise_for_status()
                    time.sleep(max(1.0, retry_after))
                    continue
                response.raise_for_status()
                break
            messages = response.json()
            if not messages:
                break
            for message in messages:
                if str(message.get("author", {}).get("id", "")) != own_bot_id:
                    continue
                delete_url = (
                    f"https://discord.com/api/v10/channels/{channel}/messages/"
                    f"{message['id']}"
                )
                for attempt in range(DEFAULT_RETRIES + 1):
                    try:
                        delete_response = requests.delete(
                            delete_url, headers=headers, timeout=timeout,
                        )
                        if delete_response.status_code == 404:
                            break
                        if delete_response.status_code == 429:
                            retry_after = float(
                                delete_response.headers.get("Retry-After", "1")
                            )
                            if attempt >= DEFAULT_RETRIES:
                                delete_response.raise_for_status()
                            time.sleep(max(1.0, retry_after))
                            continue
                        delete_response.raise_for_status()
                        break
                    except requests.RequestException:
                        if attempt >= DEFAULT_RETRIES:
                            raise
                        time.sleep(2**attempt)
                deleted += 1
                time.sleep(_api_interval())
            if len(messages) < 100:
                break
            before = messages[-1]["id"]
    return deleted


def delete_youtube_posts(guild_key="default", channel_keys=None):
    """Delete only this bot's structured YouTube notification messages."""
    token = _bot_token()
    headers = {"Authorization": f"Bot {token}"}
    own_bot_id = _bot_user_id(token)
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
                headers=headers,
                params=params,
                timeout=_integer_config("discord", "timeout", DEFAULT_TIMEOUT, minimum=1),
            )
            response.raise_for_status()
            messages = response.json()
            if not messages:
                break
            for message in messages:
                if str(message.get("author", {}).get("id", "")) != own_bot_id:
                    continue
                content = str(message.get("content", ""))
                is_full_youtube = (
                    "動画タイトル:" in content
                    and re.search(
                        r"動画URL:\s*<?https://www\.youtube\.com/watch",
                        content,
                    )
                )
                is_legacy_title_diff = (
                    "削除: 動画タイトル:" in content
                    and "追加: 動画タイトル:" in content
                )
                if not (is_full_youtube or is_legacy_title_diff):
                    continue
                delete_url = f"https://discord.com/api/v10/channels/{channel}/messages/{message['id']}"
                for attempt in range(DEFAULT_RETRIES + 1):
                    try:
                        delete_response = requests.delete(
                            delete_url, headers=headers, timeout=DEFAULT_TIMEOUT
                        )
                        if delete_response.status_code == 404:
                            break
                        if delete_response.status_code == 429:
                            retry_after = float(delete_response.headers.get("Retry-After", "1"))
                            time.sleep(max(1.0, retry_after))
                            continue
                        delete_response.raise_for_status()
                        break
                    except requests.RequestException:
                        if attempt >= DEFAULT_RETRIES:
                            raise
                        time.sleep(2 ** attempt)
                deleted += 1
                time.sleep(_api_interval())
            if len(messages) < 100:
                break
            before = messages[-1]["id"]
    return deleted
