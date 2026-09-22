"""Extract and format TL text from Discord messages.

The formatter is an optional local dependency so the scanner can still run
when the formatter project is not installed.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import gspread_utils as config

WINDOWLESS_SUBPROCESS_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_TIME_LINE = re.compile(
    r"^\s*(?:[⭐️⭐︎⭐★☆🔺△#◆◇■□\\\-]|\[[54321-]+\])*"
    r"\d{1,2}:\d{1,2}(?:\s*[-~]\s*\d{1,2}(?::\d{1,2})?)?(?=\D|$)"
)
_FORMATION_SYMBOLS = frozenset("〇○◯●◌ー－-XxOo")
_URL_LINE = re.compile(r"(?:https?://|www\.)")
_STAR_LINE = re.compile(r"^\s*(?:⭐️|⭐︎|⭐|★|☆|🔺|△)")
_SUPPLEMENT_LINE = re.compile(
    r"^\s*(?:※|\*|//|''|(?:補足|注記|注意|備考|最速|目押し|手動|メモ)"
    r"|[（(](?:補足|注記|注意))"
)
_HEADING_LINE = re.compile(
    r"^\s*(?:[#＃]|(?:概要|説明|動画|参考|使用|編成|チャンネル|TL)"
    r"(?:\s*[:：]|\s+|URL|$))"
)
_SYNCED_FORMATTER_REPOSITORIES = set()


class FormatterSyncError(RuntimeError):
    """Raised when the configured GitHub formatter cannot be synchronized."""


def _is_formation_line(line):
    """Return whether a line is the second row of a two-line TL action.

    Source Discord posts commonly put the character/timestamp on one line
    and the five-position formation on the next line.  The old extractor
    dropped that second row because it only looked for timestamps or words.
    """
    stripped = (line or "").strip()
    if not stripped or ":" in stripped:
        return False
    symbol_count = sum(character in _FORMATION_SYMBOLS for character in stripped)
    return symbol_count >= 3


def _is_marker_line(line):
    """Return whether a line can continue an already-started TL block."""
    return bool(
        re.match(
            r"^\s*(?:→|⇒|->|UB|SET|"
            r"オート|連打|通常攻撃)",
            line or "",
        )
    )


def _is_supplement_line(line):
    """Return whether a non-timestamp line carries TL side information."""
    return bool(_STAR_LINE.match(line or "") or _SUPPLEMENT_LINE.match(line or ""))


def _is_block_boundary(line):
    """Return whether a line separates two independent content blocks."""
    stripped = (line or "").strip()
    if not stripped:
        return False
    return bool(
        _URL_LINE.search(stripped)
        or _HEADING_LINE.match(stripped)
        or len(stripped) > 120
    )


def _tl_blocks(lines):
    """Find contiguous TL blocks rather than collecting matching lines.

    A timestamp at the start of a line starts an item.  Adjacent timestamp
    items remain in the same block when the intervening lines are ordinary
    continuation text, formation rows, or a single blank separator.  URLs,
    headings, and long prose split blocks so unrelated description text is
    not merged into a TL.
    """
    anchors = [index for index, line in enumerate(lines) if _TIME_LINE.match(line)]
    if not anchors:
        return []

    blocks = []
    start = previous = anchors[0]
    for anchor in anchors[1:]:
        between = lines[previous + 1 : anchor]
        blank_count = sum(not line.strip() for line in between)
        separated = (
            blank_count > 1
            or any(_is_block_boundary(line) for line in between)
        )
        if separated:
            blocks.append((start, previous))
            start = anchor
        previous = anchor
    blocks.append((start, previous))

    expanded = []
    for start, end in blocks:
        # Keep formation and marker continuations that follow the last item.
        tail = end + 1
        while tail < len(lines):
            line = lines[tail]
            if (
                _is_formation_line(line)
                or _is_marker_line(line)
                or _is_supplement_line(line)
            ):
                tail += 1
                continue
            break
        expanded.append((start, tail - 1, sum(_TIME_LINE.match(lines[i]) is not None for i in range(start, end + 1))))
    return expanded


def _run_git(path, *arguments):
    """Run Git safely for a formatter checkout owned by another Windows user."""
    repository = str(Path(path).resolve())
    try:
        return subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repository}",
                "-C",
                repository,
                *arguments,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
            creationflags=WINDOWLESS_SUBPROCESS_FLAGS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise FormatterSyncError(
            f"Failed to update TL formatter repository {repository}: {detail.strip()[:500]}"
        ) from error


def sync_formatter_repository(path):
    """Fast-forward the configured formatter checkout from its GitHub origin."""
    repository = Path(path).resolve()
    key = str(repository).casefold()
    if key in _SYNCED_FORMATTER_REPOSITORIES:
        return
    if not (repository / ".git").exists():
        raise FormatterSyncError(f"TL formatter path is not a Git repository: {repository}")
    branch = _run_git(repository, "branch", "--show-current").stdout.strip()
    if not branch:
        raise FormatterSyncError(f"TL formatter checkout has no active branch: {repository}")
    _run_git(repository, "pull", "--ff-only", "origin", branch)
    _SYNCED_FORMATTER_REPOSITORIES.add(key)


def _load_formatter():
    path = config.get_config_value(
        "discord_channel", "tl_formatter_path", os.environ.get("TL_FORMATTER_PATH", "")
    )
    if path:
        sync_formatter_repository(path)
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
    """Return the strongest contiguous TL block from a Discord message."""
    lines = [line.rstrip() for line in (content or "").splitlines()]
    blocks = _tl_blocks(lines)
    if not blocks:
        return ""
    start, end, item_count = max(
        blocks,
        key=lambda block: (block[2], block[1] - block[0]),
    )
    if item_count < 2:
        return ""
    return "\n".join(lines[start : end + 1]).strip()


def format_discord_tl(content):
    """Extract and deterministically format a Discord TL message."""
    source = extract_tl_text(content)
    if not source:
        return ""
    try:
        return _load_formatter()(source).strip()
    except ValueError:
        # The formatter intentionally accepts the game's short in-battle
        # clock (0:00-1:59), while YouTube descriptions sometimes contain
        # longer elapsed timestamps such as 4:02.  Keep the extracted TL in
        # the post instead of dropping it when normalization rejects one of
        # those timestamps.
        return source
