import os
from configparser import ConfigParser
from pathlib import Path

import gspread
from oauth2client.service_account import ServiceAccountCredentials

WAIT_TIME = 2

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.ini"


def load_local_env(env_path=None):
    """Load simple KEY=VALUE entries from a local, ignored .env file.

    Existing process environment variables always win.  The optional path is
    useful for callers that initialize a worker after module import and for
    tests without exposing secret values.
    """
    env_path = Path(env_path) if env_path else BASE_DIR / ".env"
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip("\"'")
        if name and name not in os.environ:
            os.environ[name] = value


_load_local_env = load_local_env
load_local_env()

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
_client = None
_spreadsheets = {}
_config_parser = None
_config_mtime = None


def _config_value(name):
    section, key = {
        "GOOGLE_SERVICE_ACCOUNT_JSON": ("google", "service_account_json"),
        "GOOGLE_NEW_ARRIVALS_SPREADSHEET_ID": ("google", "new_arrivals_spreadsheet_id"),
        "GOOGLE_DAMAGES_SPREADSHEET_ID": ("google", "damages_spreadsheet_id"),
        "DISCORD_WEBHOOK_URL": ("discord", "webhook_url"),
    }.get(name, (None, None))
    if section and key:
        value = get_config_value(section, key)
        if value:
            return value
    return os.environ.get(name)


def get_config_value(section, key, fallback=None):
    global _config_parser, _config_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime_ns
    except OSError:
        mtime = None
    if _config_parser is None or _config_mtime != mtime:
        _config_parser = ConfigParser()
        if mtime is not None:
            _config_parser.read(CONFIG_PATH, encoding="utf-8")
        _config_mtime = mtime
    config = _config_parser
    if config.has_option(section, key):
        value = config.get(section, key).strip()
        if value:
            return value
    return fallback


def get_int_config_value(section, key, fallback, minimum=None, maximum=None):
    """Read an integer setting without allowing malformed config to stop a scan."""
    value = get_config_value(section, key, fallback)
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = fallback
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _get_client():
    global _client
    if _client is None:
        credential_path = Path(_required_env("GOOGLE_SERVICE_ACCOUNT_JSON"))
        if not credential_path.is_absolute():
            credential_path = BASE_DIR / credential_path
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            str(credential_path), scope
        )
        _client = gspread.authorize(creds)
    return _client


def _get_spreadsheet(spreadsheet_id):
    if spreadsheet_id not in _spreadsheets:
        _spreadsheets[spreadsheet_id] = _get_client().open_by_key(spreadsheet_id)
    return _spreadsheets[spreadsheet_id]


def getNewArrivalsSheet():
    return _get_spreadsheet(_required_env("GOOGLE_NEW_ARRIVALS_SPREADSHEET_ID"))


def getDamagesSheet():
    return _get_spreadsheet(_required_env("GOOGLE_DAMAGES_SPREADSHEET_ID"))


def _required_env(name):
    value = _config_value(name)
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def deleteEmptyRows(worksheet):
    rows = worksheet.get_all_values()
    emptyIdx = [i + 1 for i, row in enumerate(rows) if all(cell == "" for cell in row)]

    ranges = splitRanges(emptyIdx)
    if not ranges:
        return

    spreadsheet = getattr(worksheet, "spreadsheet", None)
    batch_update = getattr(spreadsheet, "batch_update", None)
    sheet_id = getattr(worksheet, "id", None)
    if batch_update and sheet_id is not None:
        requests = [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": start - 1,
                        "endIndex": end,
                    }
                }
            }
            for start, end in reversed(ranges)
        ]
        batch_update({"requests": requests})
        return

    for start, end in reversed(ranges):
        worksheet.delete_rows(start, end)


def existing_column_values(worksheet, column=1):
    """Return non-empty values from one worksheet column as a set."""
    return {value for value in worksheet.col_values(column) if value}


def splitRanges(idxList):
    ranges, start = [], None
    for i, v in enumerate(idxList):
        if start is None:
            start = v
        if i + 1 == len(idxList) or idxList[i + 1] != v + 1:
            ranges.append((start, v))
            start = None
    return ranges


def writeToFirstEmptyCell(worksheet, value, column=1, wait_time=0):
    """Write a value to the first empty data row in a worksheet column."""
    rows = writeToFirstEmptyCells(worksheet, [value], column, wait_time)
    return rows[0] if rows else None


def writeToFirstEmptyCells(worksheet, values, column=1, wait_time=0):
    """Write multiple values while reading the target column only once."""
    values = [value for value in values if value]
    if not values:
        return []

    existing_values = worksheet.col_values(column)
    empty_rows = []
    for index, cell in enumerate(existing_values[1:], start=2):
        if not cell:
            empty_rows.append(index)
            if len(empty_rows) == len(values):
                break

    next_row = len(existing_values) + 1
    row_indices = empty_rows + list(
        range(next_row, next_row + len(values) - len(empty_rows))
    )

    column_letter = _column_letter(column)
    start = 0
    for index in range(1, len(row_indices) + 1):
        is_end = index == len(row_indices)
        is_gap = not is_end and row_indices[index] != row_indices[index - 1] + 1
        if not is_end and not is_gap:
            continue

        end = index
        target_rows = row_indices[start:end]
        target_values = [[value] for value in values[start:end]]
        if len(target_rows) == 1:
            worksheet.update_cell(target_rows[0], column, target_values[0][0])
        else:
            range_name = (
                f"{column_letter}{target_rows[0]}:{column_letter}{target_rows[-1]}"
            )
            worksheet.update(
                target_values,
                range_name,
                value_input_option="USER_ENTERED",
            )
        if wait_time:
            import time

            time.sleep(wait_time)
        start = end
    return row_indices


def _column_letter(column):
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def main():
    pass


if __name__ == "__main__":
    main()
