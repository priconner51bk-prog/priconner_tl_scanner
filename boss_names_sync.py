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
    # Master data uses 4019 for August, 4018 for July, and so on.
    return f"40{month + 11}"


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


def _is_boss_part(name):
    return (
        name == "バーストエネミー"
        or any(name.endswith(suffix) for suffix in ("A", "B", "C", "D", "本体", "水瓶"))
        or "部位" in name
        or "子分" in name
    )


def extract_boss_names(source_text, battle_code):
    """Extract the first five main bosses in the first current battle group.

    A fifth boss is not necessarily the fifth SQL row because some bosses have
    parts.  The group is ordered by the source rows, so collect main-boss rows
    until the fifth one and ignore those part rows.
    """
    row_pattern = re.compile(
        rf'\b({re.escape(battle_code)}01\d{{3}})\b'
    )
    names = []
    group_started = False
    for line in source_text.splitlines():
        match = row_pattern.search(line)
        if not match:
            continue
        battle_id = match.group(1)
        position = battle_id[-3:]
        name_match = re.search(r',\s*"([^"]+)"', line)
        if not name_match:
            continue
        if position == "101":
            if group_started:
                break
            group_started = True
        if not group_started:
            continue
        name = name_match.group(1)
        if not _is_boss_part(name):
            names.append(name)
            if len(names) == BOSS_COUNT:
                return names
    return []


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
