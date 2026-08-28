"""Maintain YouTube channel inclusion flags in the arrivals spreadsheet."""

from datetime import datetime, timedelta, timezone

import datetime_utils as datetime_utils
import gspread_utils as gspread
from runtime_utils import run_locked

DEFAULT_INACTIVE_DAYS = 90
INACTIVE_MARKER = "90日以上未投稿"


def calculate_channel_flags(
    channel_rows, video_rows, now=None, inactive_days=DEFAULT_INACTIVE_DAYS
):
    """Return G-column values, changing only automatically managed markers.

    Channels without a recorded video are left unchanged because the spreadsheet
    cannot prove that they have never posted a relevant video.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=inactive_days)
    latest_by_channel = {}
    for row in video_rows[1:]:
        if len(row) < 3 or not row[1] or not row[2]:
            continue
        try:
            published = datetime_utils.string2DateTime(row[2])
        except (TypeError, ValueError):
            continue
        if published > latest_by_channel.get(
            row[1], datetime.min.replace(tzinfo=timezone.utc)
        ):
            latest_by_channel[row[1]] = published

    flags = []
    changed_rows = []
    for number, row in enumerate(channel_rows[1:], start=2):
        current = row[6] if len(row) > 6 else ""
        updated = current
        channel_url = row[3] if len(row) > 3 else ""
        latest = latest_by_channel.get(channel_url)
        if latest is not None:
            if latest < cutoff and not current:
                updated = INACTIVE_MARKER
            elif current == INACTIVE_MARKER:
                updated = ""
        flags.append([updated])
        if updated != current:
            changed_rows.append((number, current, updated))
    return flags, changed_rows


def maintain_youtube_channel_flags(
    spreadsheet=None,
    now=None,
    inactive_days=DEFAULT_INACTIVE_DAYS,
    apply=True,
):
    """Mark inactive channels and reactivate channels with recent records."""
    ss = spreadsheet or gspread.getNewArrivalsSheet()
    channel_sheet = ss.worksheet("YouTubeチャンネル")
    video_sheet = ss.worksheet("YouTube動画")
    channel_rows = channel_sheet.get_all_values()
    video_rows = video_sheet.get_all_values()
    flags, changed_rows = calculate_channel_flags(
        channel_rows, video_rows, now=now, inactive_days=inactive_days
    )
    if apply and changed_rows:
        channel_sheet.update(
            flags,
            f"G2:G{len(channel_rows)}",
            value_input_option="USER_ENTERED",
        )
    return changed_rows


def main():
    def run():
        inactive_days = int(
            gspread.get_config_value(
                "maintenance", "inactive_days", DEFAULT_INACTIVE_DAYS
            )
        )
        changed_rows = maintain_youtube_channel_flags(inactive_days=inactive_days)
        print(f"更新行数: {len(changed_rows)}")

    return run_locked(run, lock_name="sheets_maintenance.lock")


if __name__ == "__main__":
    main()
