"""Synchronize the fixed monthly Clan Battle boss-name list to Google Sheets."""

import argparse
import time
from datetime import datetime

import requests

import gspread_utils as gspread

REPOSITORY = "esterTion/redive_master_db_diff"
SOURCE_PATH = "v1_006d327e17d5a53bf407981a40deb883041a2fe55e8519f568f8f9e41e1d3fbb.sql"
RAW_SOURCE_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/{{commit}}/{SOURCE_PATH}"
WORKSHEET_NAME = "ボス名"
REQUEST_TIMEOUT = 20
RETRY_INTERVAL = 5 * 60
MONTHLY_BOSS_NAMES = (
    "アクアリオス", "トルペドン", "メサルティム", "ミノタウロス",
    "ツインピッグス", "カルキノス", "オルレオン", "メデューサ",
    "グラットン", "レサトパルト", "サジタリウス", "アルゲティ",
)


def boss_name_for_month(month):
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    return MONTHLY_BOSS_NAMES[month - 1]


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


def source_contains_boss(source_text, boss_name):
    return boss_name in source_text


def sync_boss_names(
    spreadsheet=None,
    now=None,
    fetch=fetch_source,
):
    now = now or datetime.now()
    current_name = boss_name_for_month(now.month)
    if not source_contains_boss(fetch(), current_name):
        print(f"Current boss is not available in master data: {current_name}")
        return False
    sheet = (spreadsheet or gspread.getNewArrivalsSheet()).worksheet(WORKSHEET_NAME)
    sheet.update(
        [[name] for name in MONTHLY_BOSS_NAMES],
        "A2:A13",
        value_input_option="USER_ENTERED",
    )
    print(f"Synchronized monthly boss names; current boss: {current_name}")
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
