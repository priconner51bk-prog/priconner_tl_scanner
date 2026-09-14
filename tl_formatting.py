"""Extract and format TL text from Discord messages.

The formatter is an optional local dependency so the scanner can still run
when the formatter project is not installed.
"""

import os
import re
import sys

import gspread_utils as config

_TIME_LINE = re.compile(r"(?:^|\s)\d{1,2}:\d{2}(?:[-~]\d{1,2})?")
_TL_MARKERS = ("→", "->", "UB", "SET", "オート", "連打", "通常攻撃")


def _load_formatter():
    path = config.get_config_value(
        "discord_channel", "tl_formatter_path", os.environ.get("TL_FORMATTER_PATH", "")
    )
    if path and path not in sys.path:
        sys.path.insert(0, path)
    try:
        from priconner_tl import format_text
    except ImportError as error:
        raise RuntimeError(
            "TL formatter is not available; set [discord_channel] tl_formatter_path"
        ) from error
    return format_text


def extract_tl_text(content):
    """Return the TL portion of a Discord message, or an empty string."""
    lines = [line.rstrip() for line in (content or "").splitlines()]
    candidates = [
        line for line in lines
        if _TIME_LINE.search(line) or any(marker in line for marker in _TL_MARKERS)
    ]
    if len(candidates) < 2:
        return ""
    return "\n".join(candidates).strip()


def format_discord_tl(content):
    """Extract and deterministically format a Discord TL message."""
    source = extract_tl_text(content)
    if not source:
        return ""
    return _load_formatter()(source).strip()
