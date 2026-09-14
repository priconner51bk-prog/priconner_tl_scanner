"""Fetch and validate a Discord user token, then save it to config.ini.

Usage:
    python tools/discord_token_fetch.py

Interactively prompts for a Discord user token, verifies it against
GET /users/@me, tests a sample channel fetch, and writes the token
into [discord_channel] token= in config.ini when valid.
"""

from pathlib import Path

import requests

import discord_channel as dc

DISCORD_API = "https://discord.com/api/v10"
TIMEOUT = 15


def _prompt_token():
    print("Discord ユーザートークンを貼り付けてください。")
    print("取得方法: ブラウザDiscord -> F12 -> Console -> mbi.user.token")
    token = input("token> ").strip()
    return token


def _validate(token):
    """Return the user object if the token is valid, else None."""
    try:
        r = requests.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": token},
            timeout=TIMEOUT,
        )
    except requests.RequestException as error:
        print(f"接続失敗: {error}")
        return None
    if r.status_code != 200:
        print(f"トークンが無効です (HTTP {r.status_code})")
        return None
    return r.json()


def _test_channel(token):
    """Fetch a configured channel and report how many messages are readable."""
    channel_ids = dc._channel_ids()
    if not channel_ids:
        print("channel_ids が未設定です。config.ini の [discord_channel] を確認してください。")
        return False
    for channel_id in channel_ids:
        messages = dc.fetch_channel_messages(channel_id, token, limit=5)
        if messages is None:
            print(f"チャンネル {channel_id} の取得に失敗しました。")
            return False
        print(f"チャンネル {channel_id}: {len(messages)} 件のメッセージを取得しました。")
        for message in messages[:3]:
            content = (message.get("content") or "")[:60]
            urls = dc.extract_youtube_urls(message)
            print(f"  内容: {content!r}  YouTube: {urls}")
    return True


def _save_token(token, config_path=None):
    """Write the token into config.ini under [discord_channel]."""
    config_path = Path(config_path or Path(dc.__file__).resolve().parent / "config.ini")
    if not config_path.exists():
        print("config.ini がありません。config.ini.org をコピーしてください。")
        return False
    text = config_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_section = False
    replaced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[discord_channel]":
            in_section = True
            continue
        if in_section and stripped.startswith("["):
            in_section = False
            continue
        if in_section and stripped.startswith("token="):
            lines[index] = f"token={token}"
            replaced = True
            break
    if not replaced:
        # Append the section if it is missing.
        lines.append("")
        lines.append("[discord_channel]")
        lines.append(f"token={token}")
    new_text = "\n".join(lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    config_path.write_text(new_text, encoding="utf-8")
    print(f"config.ini に保存しました: {config_path}")
    return True


def main():
    token = _prompt_token()
    if not token:
        print("トークンが空です。中断します。")
        return 1
    user = _validate(token)
    if user is None:
        print("検証に失敗しました。別のトークンで再試行してください。")
        return 1
    print(f"検証成功: ユーザーID {user.get('id')}")
    if not _test_channel(token):
        print("チャンネルの読み取りテストに失敗しました。トークンの保存は行いません。")
        return 1
    if not _save_token(token):
        return 1
    print("完了: トークンを保存しました。python discord_channel.py で監視を実行できます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())