"""Client for the spreadsheet's YouTube URL registration web API."""

import os
import time

import requests

import gspread_utils as gspread
from youtube_common import unique_urls


DEFAULT_API_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbzwK2LiHtGCorTAmaJ3S4rMMTGnxSLEuxvNQgupVzI83Hj8KW44MfX0T0siZMBl1K9_2Q/exec"
)
DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 2
RECOGNIZED_STATUSES = {"accepted", "already_registered", "already_queued"}


def _api_url():
    return gspread.get_config_value("youtube", "url_api_url", DEFAULT_API_URL)


def _api_token():
    return gspread.get_config_value(
        "youtube", "url_api_token", os.environ.get("YOUTUBE_URL_API_TOKEN")
    )


def register_url(url, post=requests.post, sleep=time.sleep, retries=DEFAULT_RETRIES):
    """Register one URL and return the sheet API result, including its row."""
    token = _api_token()
    if not token:
        raise RuntimeError("YOUTUBE_URL_API_TOKEN is not configured")

    result = None
    had_uncertain_failure = False
    timeout = gspread.get_int_config_value(
        "youtube", "url_api_timeout", DEFAULT_TIMEOUT, minimum=1
    )
    for attempt in range(retries + 1):
        try:
            response = post(
                _api_url(),
                json={"token": token, "url": url},
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()
        except requests.RequestException:
            had_uncertain_failure = True
            if attempt >= retries:
                raise
            sleep(2**attempt)
            continue

        if not isinstance(result, dict) or not result.get("ok"):
            error = (
                result.get("error", "invalid API response")
                if isinstance(result, dict)
                else "invalid API response"
            )
            raise RuntimeError(f"YouTube URL API rejected {url}: {error}")

        status = result.get("status")
        if status not in RECOGNIZED_STATUSES:
            raise RuntimeError(f"YouTube URL API returned an unknown status for {url}")
        try:
            row = int(result["row"])
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"YouTube URL API omitted the sheet row for {url}") from error
        if row < 2:
            raise RuntimeError(f"YouTube URL API returned an invalid sheet row for {url}")

        # If an earlier request timed out after the sheet write, the retry
        # reports already_queued. Treat it as new for this invocation; the
        # Discord queue's dedupe key keeps the follow-up idempotent.
        if had_uncertain_failure and status == "already_queued":
            status = "accepted"
        return {
            "url": url,
            "status": status,
            "row": row,
            "registered": bool(result.get("registered", status == "already_registered")),
        }

    raise RuntimeError(f"YouTube URL API failed for {url}")


def register_urls(urls, post=requests.post, sleep=time.sleep, retries=DEFAULT_RETRIES):
    """Register distinct URLs and return one status/row record per URL."""
    results = []
    for url in unique_urls(urls):
        try:
            results.append(register_url(url, post=post, sleep=sleep, retries=retries))
        except (requests.RequestException, RuntimeError, ValueError, TypeError) as error:
            results.append({"url": url, "status": "error", "error": str(error)})
    return results
