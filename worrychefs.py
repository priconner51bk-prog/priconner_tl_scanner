import re
import time
from decimal import Decimal, InvalidOperation

import requests
from bs4 import BeautifulSoup

import datetime_utils as datetime
import discord_utils as discord
import gspread_utils as gspread
from runtime_utils import run_locked


def normalize_damage(value):
    """Convert a damage value to the sheet's integer representation."""
    value = value.rsplit("-", 1)[-1]
    cleaned = value.replace("m", "").replace("+", "")
    try:
        return int(Decimal(cleaned) * 100)
    except (InvalidOperation, TypeError, ValueError):
        return cleaned


def translate_style(style):
    return {
        "Semi-Auto": "セミオート",
        "Manual": "手動",
        "Simple Manual": "簡易手動",
        "Simple": "簡易",
        "Auto": "オート",
    }.get(style, style)


def extract_damage_entries(rows):
    """Extract consecutive code, damage, and style triples from table rows."""
    extracted = []
    for row in rows:
        for index in range(len(row) - 2):
            code, damage, style = row[index : index + 3]
            if re.match(r"^D([1-5][0-9]{1,2}|10)$", code) and all(
                value != "" for value in (code, damage, style)
            ):
                extracted.append(
                    [code, normalize_damage(damage), translate_style(style)]
                )
    return extracted


def nonempty_rows(rows):
    return [row for row in rows if any(cell != "" for cell in row)]


def fetch_html(
    url,
    http_get=requests.get,
    timeout=10,
    retries=2,
    retry_sleep=time.sleep,
):
    for attempt in range(retries + 1):
        try:
            response = http_get(url, timeout=timeout)
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except requests.RequestException:
            if attempt == retries:
                return None
            retry_sleep(2**attempt)


def parse_table_rows(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return None
    return nonempty_rows(
        [
            [col.get_text(strip=True) for col in row.find_all(["td", "th"])]
            for row in table.find_all("tr")
            if row.find_all(["td", "th"])
        ]
    )


def build_tl_values(rows, existing_tls, scan_time):
    existing_tls = set(existing_tls)
    values = []
    for code, damage, style in extract_damage_entries(rows):
        tl = f"{code},{damage},{style}"
        if tl in existing_tls:
            continue
        values.append([tl, scan_time])
        existing_tls.add(tl)
    return values


def save_tl_values(sheet, values):
    sheet.insert_rows(values, row=2)
    sheet.sort((2, "des"), range="A2:Z10000")
    gspread.deleteEmptyRows(sheet)


def notify_tl_values(notify, link_url, values):
    if not values:
        return
    text = "\n".join(row[0] for row in values)
    notify(f"[WorryChefs]({link_url})\n```cs\n{text}```")


def checkNewArrivalsForWorryChefs(
    spreadsheet=None,
    http_get=requests.get,
    notify=discord.notify,
    now_factory=datetime.now,
    format_time=datetime.dateTime2String,
    http_timeout=10,
    http_retries=2,
    retry_sleep=time.sleep,
):
    print("新着チェック対象:WorryChefs")

    ss = spreadsheet or gspread.getNewArrivalsSheet()
    sheet_tl = ss.worksheet("WorryChefs TL")
    existing_tls = sheet_tl.col_values(1)
    sheet_channel = ss.worksheet("WorryChefs")
    source_rows = sheet_channel.get_all_values()
    tl_values = []
    last_link_url = ""
    valid_source_seen = False

    for row in source_rows[1:]:
        gsheet_id, gid = row[:2]
        if not gsheet_id or not gid:
            continue

        url = f"https://docs.google.com/spreadsheets/d/e/{gsheet_id}/pubhtml/sheet?headers=false&gid={gid}"
        last_link_url = f"https://docs.google.com/spreadsheets/d/e/{gsheet_id}/pubhtml"
        print(url)
        html = fetch_html(
            url,
            http_get,
            timeout=http_timeout,
            retries=http_retries,
            retry_sleep=retry_sleep,
        )
        if html is None:
            continue
        rows = parse_table_rows(html)
        if rows is None:
            continue
        valid_source_seen = True
        scan_time = format_time(now_factory())
        new_values = build_tl_values(rows, existing_tls, scan_time)
        existing_tls.extend(row[0] for row in new_values)
        tl_values.extend(new_values)

    if not valid_source_seen:
        return

    if tl_values:
        save_tl_values(sheet_tl, tl_values)
        notify_tl_values(notify, last_link_url, tl_values)


def main():
    def run():
        print("-----------------------------------------------")
        print(f"開始{datetime.nowString()}")
        print("-----------------------------------------------")

        checkNewArrivalsForWorryChefs()

        print("-----------------------------------------------")
        print(f"終了{datetime.nowString()}")
        print("-----------------------------------------------")

    return run_locked(run)


if __name__ == "__main__":
    main()
