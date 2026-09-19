"""Run the monitor only from the 22nd through the 30th of each month."""

import argparse
from datetime import date

import monitor_runner


STAGE_CHOICES = tuple(monitor_runner.STAGE_SCRIPTS)


def is_monitor_day(day):
    """Return whether *day* is in the fixed 22nd-30th monitoring window."""
    return 22 <= day.day <= 30


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
