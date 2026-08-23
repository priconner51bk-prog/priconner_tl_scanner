import os

import requests

import gspread_utils


def _webhook_url():
    url = gspread_utils.get_config_value(
        "discord", "webhook_url", os.environ.get("DISCORD_WEBHOOK_URL")
    )
    if not url:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not configured")
    return url


def _send(content, label):
    response = requests.post(_webhook_url(), json={"content": content})
    print(label if response.status_code == 204 else f"失敗: {response.status_code}")
    return response


def notify(text):
    return _send(f"@here\n{text}", "通知送信成功")


def post(text):
    return _send(text, "ポスト送信成功")
