"""Durable handoff queue for YouTube URLs discovered by other collectors."""

import json
import os
import re
import tempfile
from pathlib import Path

from runtime_utils import LockBusy, acquire_lock, default_runtime_dir


QUEUE_FILENAME = "youtube_url_handoff.json"
HANDOFF_PATH_ENV = "PRICONNER_YOUTUBE_HANDOFF_PATH"
YOUTUBE_ID_PATTERN = re.compile(
    r"(?:watch\?v=|shorts/|live/|youtu\.be/)([A-Za-z0-9_-]+)"
)


def video_id(url):
    """Return the YouTube video ID from any supported URL form."""
    match = YOUTUBE_ID_PATTERN.search(str(url or ""))
    return match.group(1) if match else ""


def canonical_url(url):
    """Normalize a YouTube URL while preserving non-YouTube test values."""
    value = str(url or "").strip()
    identifier = video_id(value)
    return f"https://www.youtube.com/watch?v={identifier}" if identifier else value


def queue_path(path=None):
    """Return the shared handoff queue path."""
    if path is not None:
        return Path(path)
    configured_path = os.environ.get(HANDOFF_PATH_ENV, "").strip()
    if configured_path:
        return Path(configured_path).expanduser()
    return default_runtime_dir() / QUEUE_FILENAME


def _lock_path(path):
    return Path(f"{path}.lock")


def _read(path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            values = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        return []
    return (
        list(dict.fromkeys(canonical_url(value) for value in values if value))
        if isinstance(values, list)
        else []
    )


def _write(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(list(dict.fromkeys(values)), handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def enqueue(urls, path=None):
    """Append URLs to the queue and return the URLs newly added."""
    target = queue_path(path)
    incoming = list(dict.fromkeys(canonical_url(url) for url in urls if url))
    if not incoming:
        return []
    with acquire_lock(_lock_path(target)):
        current = _read(target)
        current_set = set(current)
        added = [url for url in incoming if url not in current_set]
        if added:
            _write(target, current + added)
        return added


def pending(path=None):
    """Return a snapshot of queued URLs without removing them."""
    target = queue_path(path)
    try:
        with acquire_lock(_lock_path(target)):
            return _read(target)
    except LockBusy:
        return []


def acknowledge(urls, path=None):
    """Remove successfully handled URLs from the queue."""
    target = queue_path(path)
    completed = set(urls)
    if not completed:
        return []
    with acquire_lock(_lock_path(target)):
        current = _read(target)
        remaining = [url for url in current if url not in completed]
        if remaining != current:
            _write(target, remaining)
        return remaining
