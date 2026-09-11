import os
import time

import requests

import gspread_utils


DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 2


def _integer_config(section, key, fallback, minimum=0):
    try:
        return max(minimum, int(gspread_utils.get_config_value(section, key, fallback)))
    except (TypeError, ValueError):
        return fallback


def _webhook_url():
    url = gspread_utils.get_config_value(
        "discord", "webhook_url", os.environ.get("DISCORD_WEBHOOK_URL")
    )
    if not url:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not configured")
    return url


def _send(content, label):
    timeout = _integer_config("discord", "timeout", DEFAULT_TIMEOUT, minimum=1)
    retries = _integer_config("discord", "retries", DEFAULT_RETRIES)
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                _webhook_url(), json={"content": content}, timeout=timeout
            )
            raise_for_status = getattr(response, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
            if not 200 <= response.status_code < 300:
                raise requests.HTTPError(
                    f"Discord webhook returned HTTP {response.status_code}"
                )
            print(label)
            return response
        except requests.RequestException as error:
            if attempt >= retries:
                print(f"失敗: Discord通知: {error}")
                raise
            time.sleep(2**attempt)


def notify(text):
    return _send(f"@here\n{text}", "通知送信成功")


def post(text):
    return _send(text, "ポスト送信成功")
