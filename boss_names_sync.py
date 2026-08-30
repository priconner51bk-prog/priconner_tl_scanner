"""Synchronize the fixed monthly Clan Battle boss-name list to Google Sheets."""

import argparse
import re
import time
from datetime import datetime

import requests

import gspread_utils as gspread

REPOSITORY = "esterTion/redive_master_db_diff"
SOURCE_PATH = "v1_7ce15cd873f0e35053e2a1c15111fa91cec7710d5d3ab887d94179e883f46cea.sql"
RAW_SOURCE_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/{{commit}}/{SOURCE_PATH}"
WORKSHEET_NAME = "ボス名"
REQUEST_TIMEOUT = 20
RETRY_INTERVAL = 5 * 60
BOSS_COUNT = 5


def battle_code_for_month(month):
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    return f"102{month + 11:02d}"


def fetch_latest_commit_sha(
    timeout=REQUEST_TIMEOUT,
    get=requests.get,
):
    response = get(
        f"https://api.github.com/repos/{REPOSITORY}/commits?per_page=1",
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()[0]["sha"]


def fetch_source(
    commit=None,
    timeout=REQUEST_TIMEOUT,
    get=requests.get,
):
    commit = commit or fetch_latest_commit_sha(timeout=timeout, get=get)
    response = get(RAW_SOURCE_URL.format(commit=commit), timeout=timeout)
    response.raise_for_status()
    return response.text


def extract_boss_names(source_text, battle_code):
    pattern = re.compile(
        rf',\s*"([^"]+)",\s*30,\s*1,\s*0,\s*\d+,\s*'
        rf'({re.escape(battle_code)}\d{{4}}),\s*1,'
    )
    names_by_number = {}
    for name, battle_id in pattern.findall(source_text):
        number = int(battle_id[-2:])
        if 1 <= number <= BOSS_COUNT:
            names_by_number.setdefault(number, name)
    names = [names_by_number.get(number) for number in range(1, BOSS_COUNT + 1)]
    if any(name is None for name in names):
        return []
    return names


def sync_boss_names(
    spreadsheet=None,
    now=None,
    fetch=fetch_source,
):
    now = now or datetime.now()
    boss_names = extract_boss_names(fetch(), battle_code_for_month(now.month))
    if len(boss_names) != BOSS_COUNT:
        print(f"Current month's {BOSS_COUNT} bosses are not available in master data")
        return False
    sheet = (spreadsheet or gspread.getNewArrivalsSheet()).worksheet(WORKSHEET_NAME)
    sheet.update(
        [[name] for name in boss_names],
        "A2:A6",
        value_input_option="USER_ENTERED",
    )
    print(f"Synchronized current month's bosses: {', '.join(boss_names)}")
    return True


def retry_until_success(
    sync=sync_boss_names,
    sleep=time.sleep,
    retry_interval=RETRY_INTERVAL,
):
    """Retry synchronization forever, sleeping between unsuccessful attempts."""
    while True:
        try:
            if sync():
                return 0
        except (OSError, requests.RequestException, RuntimeError, ValueError) as error:
            print(f"Boss-name synchronization failed: {error}")
        print(f"Retrying boss-name synchronization in {retry_interval} seconds.")
        sleep(retry_interval)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", type=int)
    args = parser.parse_args(argv)
    now = datetime.now().replace(month=args.month) if args.month else None
    if now is not None:
        return retry_until_success(
            sync=lambda: sync_boss_names(now=now),
        )
    return retry_until_success()


if __name__ == "__main__":
    raise SystemExit(main())
