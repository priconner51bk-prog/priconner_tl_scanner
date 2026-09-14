"""Shared helpers for YouTube search and channel scanners."""

import time
from datetime import timezone

import datetime_utils as datetime


def as_utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=datetime.JST).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def write_urls_with_retry(write_urls, urls, sleep=time.sleep, retries=2):
    for attempt in range(retries + 1):
        try:
            return write_urls(urls)
        except Exception as error:
            if attempt >= retries:
                raise
            print(f"URL登録を再試行します ({attempt + 1}/{retries}): {error}")
            sleep(2**attempt)


def unique_urls(urls):
    return list(dict.fromkeys(url for url in urls if url))
