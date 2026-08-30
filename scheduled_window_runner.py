"""Run a scanner only during the current month's configured monitoring window."""

import argparse
import calendar
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
ALLOWED_SCRIPTS = {
    "worrychefs.py",
    "youtube_channel.py",
    "youtube_search.py",
    "boss_names_sync.py",
}


def window(now):
    last_day = calendar.monthrange(now.year, now.month)[1]
    end = now.replace(day=last_day, hour=12, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=8)
    return start, end


def should_run(now, script):
    start, end = window(now)
    if script == "boss_names_sync.py":
        return now.date() == start.date() and now.time() >= time(12, 15)
    return start <= now <= end


def main(argv=None, now=None, command_runner=subprocess.run):
    parser = argparse.ArgumentParser()
    parser.add_argument("script", choices=sorted(ALLOWED_SCRIPTS))
    args = parser.parse_args(argv)
    now = now or datetime.now().astimezone()
    if not should_run(now, args.script):
        print(f"Skipping {args.script}: outside the configured month-end window.")
        return 0
    result = command_runner(
        [sys.executable, str(ROOT_DIR / args.script)],
        cwd=ROOT_DIR,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
