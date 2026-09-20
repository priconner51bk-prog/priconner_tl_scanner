"""Run the fast RSS phase before the slower keyword supplement phase."""

import os
import subprocess
import sys
import time


def run_step(name, script):
    started = time.perf_counter()
    print(f"[{name}] start", flush=True)
    result = subprocess.run([sys.executable, script], check=False)
    elapsed = time.perf_counter() - started
    print(f"[{name}] exit={result.returncode} elapsed={elapsed:.1f}s", flush=True)
    return result.returncode


def main():
    # Phase 1: youtube_channel.py is RSS-first and publishes its result.
    channel_code = run_step("rss", "youtube_channel.py")
    if channel_code:
        return channel_code

    # Make RSS output durable and visible before starting the slow supplement.
    queue_code = run_step("rss-discord-queue", "discord_queue.py")
    if queue_code:
        return queue_code

    # Phase 2: keyword search supplements RSS misses after RSS is finalized.
    keyword_code = run_step("keyword", "youtube_search.py")
    if keyword_code:
        return keyword_code
    return run_step("keyword-discord-queue", "discord_queue.py")


if __name__ == "__main__":
    raise SystemExit(main())
