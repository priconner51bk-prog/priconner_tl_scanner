"""Record a GitHub Actions heartbeat in the monitoring spreadsheet."""

import os
from datetime import datetime, timezone

import gspread
from oauth2client.service_account import ServiceAccountCredentials


SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


def main():
    credentials = ServiceAccountCredentials.from_json_keyfile_name(
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"], SCOPES
    )
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(os.environ["GOOGLE_NEW_ARRIVALS_SPREADSHEET_ID"])
    worksheet = spreadsheet.worksheet("Actions稼働状況")
    worksheet.append_row(
        [
            datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            "GitHub Actions",
            "started",
            os.environ.get("GITHUB_SHA", ""),
        ],
        value_input_option="USER_ENTERED",
    )


if __name__ == "__main__":
    main()
