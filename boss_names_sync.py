"""Synchronize the fixed monthly Clan Battle boss-name list to Google Sheets."""

import argparse
from datetime import datetime

import requests

import gspread_utils as gspread

SOURCE_URL = (
    "https://raw.githubusercontent.com/esterTion/redive_master_db_diff/"
    "master/v1_006d327e17d5a53bf407981a40deb883041a2fe55e8519f568f8f9e41e1d3fbb.sql"
)
WORKSHEET_NAME = "ボス名"
REQUEST_TIMEOUT = 20
MONTHLY_BOSS_NAMES = (
    "アクアリオス", "トルペドン", "メサルティム", "ミノタウロス",
    "ツインピッグス", "カルキノス", "オルレオン", "メデューサ",
    "グラットン", "レサトパルト", "サジタリウス", "アルゲティ",
)


def boss_name_for_month(month):
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    return MONTHLY_BOSS_NAMES[month - 1]


def fetch_source(url=SOURCE_URL, timeout=REQUEST_TIMEOUT, get=requests.get):
    response = get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def source_contains_boss(source_text, boss_name):
    return boss_name in source_text


def sync_boss_names(spreadsheet=None, now=None, fetch=fetch_source):
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


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", type=int)
    args = parser.parse_args(argv)
    now = datetime.now().replace(month=args.month) if args.month else None
    try:
        return 0 if sync_boss_names(now=now) else 1
    except (OSError, requests.RequestException, RuntimeError, ValueError) as error:
        print(f"Boss-name synchronization failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
