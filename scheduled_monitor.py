"""Run the monitor only during the eight days before each month's last day."""

import argparse
from calendar import monthrange
from datetime import date, timedelta

import monitor_runner


STAGE_CHOICES = tuple(monitor_runner.STAGE_SCRIPTS)


def last_day_of_month(day):
    return date(day.year, day.month, monthrange(day.year, day.month)[1])


def is_monitor_day(day):
    """Return whether *day* is one of the eight days before month end.

    The final calendar day is deliberately excluded.  For example, this
    yields 20-27 in a common February and 21-28 in a leap-year February.
    """
    last_day = last_day_of_month(day)
    return last_day - timedelta(days=8) <= day < last_day


def main(argv=None, today=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date",
        help="Use YYYY-MM-DD instead of today's local date (mainly for checks).",
    )
    parser.add_argument(
        "--stage",
        choices=STAGE_CHOICES,
        help="Run only one monitor stage; useful for staggered local schedules.",
    )
    args = parser.parse_args(argv)
    if args.date:
        try:
            today = date.fromisoformat(args.date)
        except ValueError as error:
            parser.error(f"invalid date: {error}")
    today = today or date.today()

    if not is_monitor_day(today):
        print(f"Skipping monitor on {today.isoformat()} (outside scheduled period).")
        return 0
    # Do not let wrapper-only arguments leak into monitor_runner.
    runner_args = ["--stages", args.stage] if args.stage else []
    return monitor_runner.main(runner_args)


if __name__ == "__main__":
    raise SystemExit(main())
