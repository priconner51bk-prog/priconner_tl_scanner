"""Shared runtime helpers for safe monitor execution."""

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class LockBusy(Exception):
    """Raised when another local monitor process owns the lock."""


def default_runtime_dir():
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "priconner-tl-movie-scanner"
    return Path.home() / ".local" / "state" / "priconner-tl-movie-scanner"


@contextmanager
def acquire_lock(lock_path):
    """Acquire a non-blocking process lock and release it on every exit path."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LockBusy from error
        yield


def run_locked(callback, runtime_dir=None):
    """Run a standalone stage under the monitor lock unless the runner owns it."""
    if os.environ.get("PRICONNER_MONITOR_LOCK_HELD") == "1":
        return callback()
    try:
        with acquire_lock(Path(runtime_dir or default_runtime_dir()) / "monitor.lock"):
            return callback()
    except LockBusy:
        print("Another monitor run is already in progress; skipping.")
        return 0
