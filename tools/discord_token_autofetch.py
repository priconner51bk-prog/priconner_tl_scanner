"""Recover a valid Discord user token by driving a fresh Chrome profile over CDP.

A fresh (non-default) profile can be launched with a remote debugging port
even on machines where the default profile is locked down. The script opens
the Discord login page in that fresh window; after you log in, it reads
``localStorage.token`` through the DevTools Protocol, validates the token
against GET /users/@me, and writes it into [discord_channel] token= in
config.ini.

Usage:
    python tools/discord_token_autofetch.py            # open window, wait for login, save
    python tools/discord_token_autofetch.py --port 9555
    python tools/discord_token_autofetch.py --keep-open  # leave the window open
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

import requests

import discord_channel as dc

DISCORD_API = "https://discord.com/api/v10"
TIMEOUT = 15
DISCORD_URL = "https://discord.com/app"

EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# Read the token from the page's localStorage. Discord stores it as a JSON
# string under the key "token"; JSON.parse yields the raw token.
JS_GET_TOKEN = (
    "(function(){"
    "try{var v=localStorage.getItem('token');"
    "if(v){try{return JSON.parse(v);}catch(e){return v;}}"
    "return null;}catch(e){return null;}"
    "})()"
)


def _cdp_get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
        return json.loads(resp.read().decode())


def _wait_cdp(port, deadline=30):
    end = time.time() + deadline
    while time.time() < end:
        try:
            _cdp_get(port, "/json/version")
            return True
        except Exception:
            time.sleep(1)
    return False


def _find_discord_tab(port, deadline=120):
    end = time.time() + deadline
    while time.time() < end:
        try:
            tabs = _cdp_get(port, "/json")
        except Exception:
            time.sleep(1)
            continue
        for tab in tabs:
            if "discord.com" in (tab.get("url") or ""):
                return tab
        time.sleep(1)
    return None


def _evaluate(tab, expression):
    import websocket

    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }))
        end = time.time() + 30
        while time.time() < end:
            message = json.loads(ws.recv())
            if message.get("id") == 1:
                return message.get("result", {}).get("result", {}).get("value")
    finally:
        ws.close()
    return None


def _validate(token):
    try:
        resp = requests.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": token},
            timeout=TIMEOUT,
        )
    except requests.RequestException as error:
        print(f"  validation error: {error}")
        return None
    if resp.status_code != 200:
        return None
    return resp.json()


def _save_token(token):
    import tools.discord_token_fetch as token_fetch
    from pathlib import Path
    config_path = Path(dc.__file__).resolve().parent / "config.ini"
    return token_fetch._save_token(token, config_path)


def fetch_token(port=9555, profile_dir=None, keep_open=False):
    profile_dir = profile_dir or os.path.join(
        os.environ.get("TEMP", r"C:\Windows\Temp"), "discord-cdp-profile"
    )
    args = [
        EXE,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
        DISCORD_URL,
    ]
    subprocess.Popen(args, creationflags=0x00000008)
    print(f"Opened a fresh Chrome window (profile: {profile_dir}).")
    print("Please log in to Discord in that window. Waiting for the token...")
    if not _wait_cdp(port):
        print("CDP endpoint did not come up.")
        return None
    tab = _find_discord_tab(port)
    if tab is None:
        print("No discord.com tab found.")
        return None
    token = None
    end = time.time() + 300
    last_prompt = 0
    while time.time() < end:
        token = _evaluate(tab, JS_GET_TOKEN)
        if token:
            break
        if time.time() - last_prompt > 15:
            print("Still waiting for login... (log in in the opened Chrome window)")
            last_prompt = time.time()
        time.sleep(2)
    if not token:
        print("Timed out waiting for the token.")
        return None
    user = _validate(token)
    if user is None:
        print("Token rejected by Discord (HTTP 401).")
        return None
    print(f"Valid token for user id {user.get('id')}")
    if _save_token(token):
        print("Saved to config.ini")
        return token
    print("Failed to save token.")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9555)
    parser.add_argument("--profile-dir", default=None)
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args(argv)
    token = fetch_token(port=args.port, profile_dir=args.profile_dir, keep_open=args.keep_open)
    if token is None:
        print("No valid Discord token could be recovered.")
        return 1
    print("Token recovered and saved. Run: python discord_channel.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
