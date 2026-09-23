"""Standalone five-minute candidate collector for direct YouTube search."""

from runtime_utils import run_locked
from youtube_search_candidates import run_source_scan


def main():
    return run_locked(
        lambda: run_source_scan("direct"),
        lock_name="youtube_search_source_direct.lock",
    )


if __name__ == "__main__":
    raise SystemExit(main())
